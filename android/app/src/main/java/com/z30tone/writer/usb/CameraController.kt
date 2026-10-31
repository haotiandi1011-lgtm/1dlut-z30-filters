package com.z30tone.writer.usb

import android.content.Context
import android.hardware.usb.UsbDevice
import android.hardware.usb.UsbManager
import com.z30tone.writer.core.Payload
import com.z30tone.writer.data.BackupStore
import java.io.File

/**
 * 把「一次会话」封装成几个动作：连、读槽位、写槽位（含备份与读回校验）。
 * 所有方法都是阻塞的，调用方负责放到 IO 线程。
 */
class CameraController(private val context: Context) {

    private var ptp: UsbPtp? = null
    private val backups = BackupStore(context)

    val connected: Boolean get() = ptp != null

    fun connect(manager: UsbManager, device: UsbDevice, log: (String) -> Unit = {}) {
        disconnect()
        ptp = try {
            UsbPtp.open(manager, device, log, resetFirst = true)
        } catch (first: PtpException) {
            log("握手失败：${first.message}")
            log("歇 0.8 秒后换一种方式重试（跳过 Class Reset）")
            // 桌面端的记录里这个握手本来就要重试几轮才成，节奏照抄。
            Thread.sleep(800)
            disconnect()
            try {
                UsbPtp.open(manager, device, log, resetFirst = false)
            } catch (second: PtpException) {
                log("第二种方式也失败：${second.message}")
                throw second
            }
        }
    }

    fun disconnect() {
        runCatching { ptp?.close() }
        ptp = null
    }

    private fun camera(): UsbPtp = ptp ?: throw PtpException("还没连上相机")

    /** 读一个槽位。空槽位可能返回 0 字节或报错，两种都按「空」处理。 */
    fun readSlot(slot: Int): ByteArray? {
        val raw = camera().readSlot(slot)
        return raw.takeIf { it.isNotEmpty() }
    }

    class WriteOutcome(
        val slot: Int,
        val written: Int,
        val readback: ByteArray,
        val diff: Payload.Diff,
        val backup: File?,
        val backupFailed: Boolean,
    ) {
        val verified: Boolean get() = diff.identical
    }

    /**
     * 写槽位。顺序固定：先备份 → 再写 → 立刻读回逐字节比对。
     * 任何一步失败都向上抛，由界面显示原因；不做「自动重试」这种看不见的动作。
     */
    fun write(slot: Int, payload: ByteArray, hadContent: Boolean): WriteOutcome {
        val camera = camera()

        var backup: File? = null
        var backupFailed = false
        try {
            val existing = camera.readSlot(slot)
            if (existing.isNotEmpty()) {
                backup = backups.save(slot, existing)
            }
        } catch (e: Exception) {
            // 原槽位读不出来：如果界面认为它有内容，就得如实告诉用户「没能备份」。
            backupFailed = hadContent
        }

        camera.writeSlot(slot, payload)

        val readback = camera.readSlot(slot)
        return WriteOutcome(
            slot = slot,
            written = payload.size,
            readback = readback,
            diff = Payload.diff(payload, readback),
            backup = backup,
            backupFailed = backupFailed,
        )
    }
}
