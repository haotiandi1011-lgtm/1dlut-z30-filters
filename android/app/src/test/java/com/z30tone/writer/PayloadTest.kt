package com.z30tone.writer

import com.z30tone.writer.core.Payload
import com.z30tone.writer.core.Slots
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import kotlin.math.roundToInt

/**
 * 载荷层是唯一会「写进相机」的代码，所以这里的断言都是逐字节的。
 * 编码规则见 真机发现-载荷结构.md §3.1。
 */
class PayloadTest {

    private fun linearLut(): IntArray = IntArray(Payload.LUT_POINTS) {
        (it * Payload.LUT_MAX.toDouble() / (Payload.LUT_POINTS - 1)).roundToInt()
    }

    @Test
    fun buildAndParseRoundTrip() {
        val payload = Payload.buildFromLut(linearLut(), "TEST-CURVE")
        assertEquals(Payload.SIZE, payload.size)

        val parsed = Payload.parse(payload)
        assertEquals("TEST-CURVE", parsed.name)
        assertEquals(0, parsed.first)
        assertEquals(Payload.LUT_MAX, parsed.last)
        assertTrue(parsed.monotonic)
        assertEquals(0x05, parsed.family)
        assertEquals(4, parsed.controlPoints.size)
    }

    @Test
    fun paramsUseNikonEncoding() {
        val payload = Payload.setParams(
            Payload.buildFromLut(linearLut(), "PARAM"),
            mapOf(
                "quick_sharp" to -2f,
                "sharp" to 9f,
                "mid_sharp" to 5f,
                "clarity" to -5f,
                "saturation" to 3f,
            ),
        )

        // 真机回读验证过的四组样本值
        assertEquals(0xFE, payload[0x018].toInt() and 0xFF)
        assertEquals(0x24, payload[0x019].toInt() and 0xFF)
        assertEquals(0x14, payload[0x01A].toInt() and 0xFF)
        assertEquals(0xEC, payload[0x01B].toInt() and 0xFF)
        assertEquals(0x0C, payload[0x01E].toInt() and 0xFF)

        val parsed = Payload.parse(payload)
        assertEquals(9.0, parsed.param("sharp").toDouble(), 0.001)
        assertEquals(-5.0, parsed.param("clarity").toDouble(), 0.001)
        assertEquals(3.0, parsed.param("saturation").toDouble(), 0.001)
        assertEquals(-2.0, parsed.param("quick_sharp").toDouble(), 0.001)
    }

    @Test
    fun paramsDoNotTouchCurveBytes() {
        val base = Payload.buildFromLut(linearLut(), "KEEP")
        val changed = Payload.setParams(base, mapOf("sharp" to 4f))
        val diff = Payload.diff(base, changed)
        assertEquals(listOf(0x019), diff.offsets)
    }

    @Test
    fun nameIsAsciiOnlyAndTruncated() {
        assertEquals("Z30FIT", Payload.sanitizeName(""))
        assertEquals("??", Payload.sanitizeName("中文"))
        assertEquals("ABC", Payload.sanitizeName("  ABC  "))
        assertEquals(19, Payload.sanitizeName("ABCDEFGHIJKLMNOPQRSTUVWXYZ").length)
        // 右上角的引号之类非 ASCII 字符同样会被替换
        assertEquals("A?B", Payload.sanitizeName("A\u2018B"))
    }

    @Test
    fun withNameZeroPadsTheField() {
        val payload = Payload.withName(Payload.buildFromLut(linearLut(), "AAA"), "NEW-NAME")
        assertEquals("NEW-NAME", Payload.parse(payload).name)
        // 名称字段 20 字节，8 个字符之后必须补 0，否则机身会读到脏字符
        assertEquals(0, payload[Payload.NAME_OFFSET + 8].toInt())
        assertEquals(0, payload[Payload.NAME_OFFSET + Payload.NAME_LEN - 1].toInt())
    }

    @Test
    fun lutIsBigEndian() {
        val lut = IntArray(Payload.LUT_POINTS)
        lut[0] = 0x1234
        lut[1] = 0x00FF
        val payload = Payload.buildFromLut(lut, "BE")

        assertEquals(0x12, payload[Payload.LUT_OFFSET].toInt() and 0xFF)
        assertEquals(0x34, payload[Payload.LUT_OFFSET + 1].toInt() and 0xFF)
        assertEquals(0x00, payload[Payload.LUT_OFFSET + 2].toInt() and 0xFF)
        assertEquals(0xFF, payload[Payload.LUT_OFFSET + 3].toInt() and 0xFF)
    }

    @Test
    fun diffReportsOffsets() {
        val a = ByteArray(Payload.SIZE)
        val b = a.copyOf()
        b[10] = 1
        b[200] = 2

        val diff = Payload.diff(a, b)
        assertFalse(diff.identical)
        assertEquals(listOf(10, 200), diff.offsets)
        assertTrue(diff.summary.contains("2 字节"))

        assertTrue(Payload.diff(a, a.copyOf()).identical)
    }

    @Test
    fun diffDetectsLengthMismatch() {
        val long = ByteArray(Payload.SIZE)
        val short = ByteArray(36)
        val diff = Payload.diff(long, short)
        assertTrue(diff.lengthMismatch)
        assertFalse(diff.identical)
    }

    @Test
    fun controlPointsStayInByteRange() {
        val points = Payload.controlPoints(linearLut())
        assertEquals(4, points.size)
        // 线性曲线：x 依次为 0.2 / 0.4 / 0.6 / 0.8，y 应等于 x
        assertEquals(listOf(51, 102, 153, 204), points.map { it.first })
        assertTrue(points.all { it.second in 0..255 })
    }

    @Test
    fun slotLabelsMatchFirmwareMenu() {
        assertEquals("C1", Slots.label(0x00C9))
        assertEquals("C9", Slots.label(0x00D1))
        assertEquals(Slots.LAST, Slots.RECOMMENDED)
    }

    @Test
    fun parseAcceptsLongerBuffers() {
        val payload = Payload.buildFromLut(linearLut(), "LONG")
        val padded = payload + ByteArray(16) { 0x7F }
        val parsed = Payload.parse(padded)
        assertEquals(Payload.SIZE, parsed.size)
        assertEquals("LONG", parsed.name)
    }
}
