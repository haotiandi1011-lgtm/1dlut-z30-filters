package com.z30tone.writer.usb

import android.hardware.usb.UsbConstants
import android.hardware.usb.UsbDevice
import android.hardware.usb.UsbDeviceConnection
import android.hardware.usb.UsbEndpoint
import android.hardware.usb.UsbInterface
import android.hardware.usb.UsbManager
import java.io.ByteArrayOutputStream
import java.io.IOException

class PtpException(message: String, val code: Int? = null) : IOException(message)

/**
 * 极简 PTP over USB 传输层（Android USB Host）。
 *
 * 握手顺序与桌面端（Windows + libusb）已验证的流程一致：
 *
 *     PTP Class Reset → 清两个 Bulk 端点的 HALT → 丢掉残留数据
 *     → OpenSession（事务号必须为 0）→ 之后事务号从 1 开始
 *
 * 每一步都通过 [log] 记录结果，出问题时能看出卡在哪一环。
 * 只做三件事：读槽位（0x90CC）、写槽位（0x90CD）、关闭会话。
 * **不实现 0x90CE 删槽**——它是破坏性的，不该出现在写入端。
 */
class UsbPtp private constructor(
    private val connection: UsbDeviceConnection,
    private val usbInterface: UsbInterface,
    private val endpointIn: UsbEndpoint,
    private val endpointOut: UsbEndpoint,
    private val log: (String) -> Unit,
) {

    private var transactionId = 0
    private val timeoutMs = 5000
    private val firstResponseTimeoutMs = 8000

    companion object {
        const val VENDOR_ID = 0x04B0
        const val PRODUCT_ID = 0x0452

        const val OP_GET_PICT_CTRL = 0x90CC
        const val OP_SET_PICT_CTRL = 0x90CD

        private const val OP_OPEN_SESSION = 0x1002
        private const val OP_CLOSE_SESSION = 0x1003

        private const val TYPE_COMMAND = 1
        private const val TYPE_DATA = 2
        private const val TYPE_RESPONSE = 3
        private const val TYPE_EVENT = 4

        private const val RC_OK = 0x2001
        private const val RC_OK_WITH_DATA = 0x2002
        private const val RC_SESSION_ALREADY_OPEN = 0xA002

        private const val MAX_CONTAINER = 65536

        private val EMPTY = ByteArray(0)

        private val ERROR_TEXT = mapOf(
            0x2003 to "相机拒绝了这条指令",
            0x2005 to "本机不支持这条指令",
            0x2006 to "参数不被支持",
            0x2009 to "设备属性不被支持",
            0x2019 to "相机忙（可能在回放、传图或休眠）",
            0x201D to "参数无效",
            0xA001 to "访问被拒绝",
            0xA002 to "会话已打开",
        )

        fun isCamera(device: UsbDevice): Boolean =
            device.vendorId == VENDOR_ID && device.productId == PRODUCT_ID

        /**
         * 打开相机并建立 PTP 会话。
         *
         * @param resetFirst true：先做 PTP Class Reset（桌面端验证过的顺序）
         *                   false：跳过 Reset 直接 OpenSession（个别机型对 Reset 敏感）
         */
        fun open(
            manager: UsbManager,
            device: UsbDevice,
            log: (String) -> Unit = {},
            resetFirst: Boolean = true,
        ): UsbPtp {
            if (!isCamera(device)) {
                throw PtpException(
                    "这不是 Z30（VID 0x%04X / PID 0x%04X）".format(device.vendorId, device.productId)
                )
            }

            log("设备 VID=0x%04X PID=0x%04X，接口数 %d".format(
                device.vendorId, device.productId, device.interfaceCount
            ))
            for (i in 0 until device.interfaceCount) {
                val itf = device.getInterface(i)
                log("  接口%d：class=%d subclass=%d protocol=%d 端点=%d".format(
                    i, itf.interfaceClass, itf.interfaceSubclass,
                    itf.interfaceProtocol, itf.endpointCount
                ))
            }

            val connection = try {
                manager.openDevice(device)
            } catch (e: SecurityException) {
                null
            } ?: throw PtpException("打不开 USB 设备。请拔插相机，并在系统弹窗里允许访问。")

            val ptpInterface = (0 until device.interfaceCount)
                .map { device.getInterface(it) }
                .firstOrNull { it.interfaceClass == 6 && it.interfaceSubclass == 1 }
                ?: run {
                    connection.close()
                    throw PtpException("相机没有 PTP 接口。请把相机的 USB 模式设为 MTP/PTP。")
                }
            log("选用接口 ${ptpInterface.id}（protocol ${ptpInterface.interfaceProtocol}）")

            // force = true：必要时把内核的 MTP 驱动踢开，否则拿不到接口。
            val claimed = connection.claimInterface(ptpInterface, true)
            log("claim 接口：$claimed")
            if (!claimed) {
                connection.close()
                throw PtpException("USB 接口被系统占用。请拔插相机后重试。")
            }

            var endpointIn: UsbEndpoint? = null
            var endpointOut: UsbEndpoint? = null
            for (i in 0 until ptpInterface.endpointCount) {
                val endpoint = ptpInterface.getEndpoint(i)
                if (endpoint.type != UsbConstants.USB_ENDPOINT_XFER_BULK) continue
                if (endpoint.direction == UsbConstants.USB_DIR_IN) {
                    if (endpointIn == null) endpointIn = endpoint
                } else {
                    if (endpointOut == null) endpointOut = endpoint
                }
            }

            val inEp = endpointIn
            val outEp = endpointOut
            if (inEp == null || outEp == null) {
                connection.releaseInterface(ptpInterface)
                connection.close()
                throw PtpException("相机没有可用的 Bulk 端点。")
            }
            log("端点 IN=0x%02X（包长 %d） / OUT=0x%02X（包长 %d）".format(
                inEp.address, inEp.maxPacketSize, outEp.address, outEp.maxPacketSize
            ))

            val camera = UsbPtp(connection, ptpInterface, inEp, outEp, log)
            try {
                camera.handshake(resetFirst)
            } catch (t: Throwable) {
                camera.close()
                throw t
            }
            return camera
        }
    }

    // -- 握手 ---------------------------------------------------------------

    private fun handshake(resetFirst: Boolean) {
        // 刚 claim 完就发命令，个别机型会丢第一包，给它一点时间。
        Thread.sleep(200)

        if (resetFirst) {
            val result = runCatching {
                connection.controlTransfer(0x21, 0x66, 0, usbInterface.id, null, 0, 1000)
            }.getOrNull()
            log("PTP Class Reset → $result")
        } else {
            log("跳过 PTP Class Reset")
        }

        val haltIn = runCatching {
            connection.controlTransfer(0x02, 0x01, 0x00, endpointIn.address, null, 0, 1000)
        }.getOrNull()
        val haltOut = runCatching {
            connection.controlTransfer(0x02, 0x01, 0x00, endpointOut.address, null, 0, 1000)
        }.getOrNull()
        log("清 HALT → IN:$haltIn OUT:$haltOut")

        drain()

        transactionId = 0
        val start = System.currentTimeMillis()
        send(TYPE_COMMAND, OP_OPEN_SESSION, 0, intArrayOf(1), EMPTY)
        log("OpenSession 已发送，等相机回话…")

        var response = readContainer(firstResponseTimeoutMs)
        if (response.code == RC_SESSION_ALREADY_OPEN) {
            log("相机说会话已打开，先关再开")
            send(TYPE_COMMAND, OP_CLOSE_SESSION, 0, intArrayOf(), EMPTY)
            readContainer()
            send(TYPE_COMMAND, OP_OPEN_SESSION, 0, intArrayOf(1), EMPTY)
            response = readContainer(firstResponseTimeoutMs)
        }
        if (response.code != RC_OK) {
            throw PtpException(describe(response.code), response.code)
        }
        log("会话已建立（%d ms）".format(System.currentTimeMillis() - start))

        transactionId = 0
    }

    private fun drain() {
        val buffer = ByteArray(4096)
        var rounds = 0
        repeat(4) {
            val read = runCatching {
                connection.bulkTransfer(endpointIn, buffer, buffer.size, 200)
            }.getOrDefault(-1)
            if (read < 0) return
            rounds++
            log("丢弃残留数据 $read 字节")
        }
        if (rounds == 0) log("无残留数据")
    }

    // -- 收发 ---------------------------------------------------------------

    private class Container(val type: Int, val code: Int, val transaction: Int, val data: ByteArray)

    private fun send(type: Int, code: Int, transaction: Int, params: IntArray, data: ByteArray) {
        val body = ByteArray(params.size * 4 + data.size)
        params.forEachIndexed { index, value -> writeIntLe(body, index * 4, value) }
        if (data.isNotEmpty()) {
            System.arraycopy(data, 0, body, params.size * 4, data.size)
        }

        val header = ByteArray(12)
        writeIntLe(header, 0, 12 + body.size)
        writeShortLe(header, 4, type)
        writeShortLe(header, 6, code)
        writeIntLe(header, 8, transaction)

        bulkWrite(header + body)
    }

    private fun readContainer(timeout: Int = timeoutMs): Container {
        val buffer = ByteArray(MAX_CONTAINER)
        var total = 0
        val start = System.currentTimeMillis()

        while (total < 12) {
            val read = bulkRead(buffer, total, buffer.size - total, timeout)
            if (read <= 0) {
                throw PtpException(
                    "读相机超时（${System.currentTimeMillis() - start} ms 无响应）。" +
                        "相机可能已休眠、不在 MTP 模式，或 USB 供电不足。"
                )
            }
            total += read
        }

        val length = readIntLe(buffer, 0)
        val type = readShortLe(buffer, 4)
        val code = readShortLe(buffer, 6)
        val transaction = readIntLe(buffer, 8)

        if (length < 12 || length > MAX_CONTAINER) {
            throw PtpException("收到的数据长度异常：$length")
        }

        while (total < length) {
            val read = bulkRead(buffer, total, length - total, timeout)
            if (read <= 0) break
            total += read
        }

        val end = minOf(total, length)
        val data = if (end > 12) buffer.copyOfRange(12, end) else ByteArray(0)
        return Container(type, code, transaction, data)
    }

    private fun bulkRead(buffer: ByteArray, offset: Int, length: Int, timeout: Int): Int =
        connection.bulkTransfer(endpointIn, buffer, offset, length, timeout)

    private fun bulkWrite(data: ByteArray) {
        var offset = 0
        var guard = 0
        while (offset < data.size) {
            val written = connection.bulkTransfer(
                endpointOut, data, offset, data.size - offset, timeoutMs
            )
            if (written <= 0) {
                throw PtpException("向相机写入失败（错误码 $written）。请拔插相机后重试。")
            }
            offset += written
            if (++guard > 32) {
                throw PtpException("向相机写入失败：多次重试仍未写完。")
            }
        }
    }

    private fun transaction(code: Int, params: IntArray, dataOut: ByteArray?): ByteArray {
        transactionId += 1
        send(TYPE_COMMAND, code, transactionId, params, EMPTY)
        if (dataOut != null && dataOut.isNotEmpty()) {
            send(TYPE_DATA, code, transactionId, intArrayOf(), dataOut)
        }

        val output = ByteArrayOutputStream()
        while (true) {
            val container = readContainer()
            when (container.type) {
                TYPE_DATA -> output.write(container.data)
                TYPE_EVENT -> Unit // 事件插在中间，忽略
                TYPE_RESPONSE -> {
                    if (container.code != RC_OK && container.code != RC_OK_WITH_DATA) {
                        throw PtpException(describe(container.code), container.code)
                    }
                    return output.toByteArray()
                }

                else -> throw PtpException("意外的容器类型 ${container.type}")
            }
        }
    }

    // -- 对外操作 -----------------------------------------------------------

    /** 0x90CC：读回某个槽位的原始载荷（只读，安全）。空槽位可能返回 0 字节或错误。 */
    fun readSlot(slot: Int): ByteArray =
        transaction(OP_GET_PICT_CTRL, intArrayOf(slot, 0), null)

    /** 0x90CD：把 614 字节载荷写进槽位。调用方必须先备份、先确认目标槽位。 */
    fun writeSlot(slot: Int, payload: ByteArray) {
        require(payload.size == 614) { "载荷必须是 614 字节，实际 ${payload.size}" }
        transaction(OP_SET_PICT_CTRL, intArrayOf(slot, 0), payload)
    }

    fun close() {
        runCatching {
            send(TYPE_COMMAND, OP_CLOSE_SESSION, transactionId + 1, intArrayOf(), EMPTY)
            // 收尾用短超时：相机不应答时别把界面卡住
            readContainer(300)
        }
        runCatching { connection.releaseInterface(usbInterface) }
        runCatching { connection.close() }
    }

    // -- 小工具 -------------------------------------------------------------

    private fun describe(code: Int): String {
        val text = ERROR_TEXT[code] ?: "未知错误"
        return "相机返回 0x%04X：%s".format(code, text)
    }

    private fun readIntLe(src: ByteArray, offset: Int): Int =
        (src[offset].toInt() and 0xFF) or
            ((src[offset + 1].toInt() and 0xFF) shl 8) or
            ((src[offset + 2].toInt() and 0xFF) shl 16) or
            ((src[offset + 3].toInt() and 0xFF) shl 24)

    private fun readShortLe(src: ByteArray, offset: Int): Int =
        (src[offset].toInt() and 0xFF) or ((src[offset + 1].toInt() and 0xFF) shl 8)

    private fun writeIntLe(dst: ByteArray, offset: Int, value: Int) {
        dst[offset] = (value and 0xFF).toByte()
        dst[offset + 1] = ((value shr 8) and 0xFF).toByte()
        dst[offset + 2] = ((value shr 16) and 0xFF).toByte()
        dst[offset + 3] = ((value shr 24) and 0xFF).toByte()
    }

    private fun writeShortLe(dst: ByteArray, offset: Int, value: Int) {
        dst[offset] = (value and 0xFF).toByte()
        dst[offset + 1] = ((value shr 8) and 0xFF).toByte()
    }
}
