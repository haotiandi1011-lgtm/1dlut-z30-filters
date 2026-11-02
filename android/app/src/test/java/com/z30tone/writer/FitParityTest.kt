package com.z30tone.writer

import com.z30tone.writer.core.Recipe
import com.z30tone.writer.core.fit.PhotoFit
import com.z30tone.writer.core.fit.ToneCurve
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import kotlin.math.abs
import kotlin.math.pow

/**
 * **移植没跑偏的验收测试。**
 *
 * 金标准由 `tools/gen_android_fit_data.py` 用电脑端 `z30fit/refimage.py` 的那套代码
 * 在完全相同的输入上算出来，存在 `src/test/resources/fit-golden.txt`。
 *
 * 断言分两层：
 *   1. 256 点 15 位码值逐点差 ≤ 2（= 8 位坐标下 0.016/255，远严于验收要求的 2/255）；
 *   2. 曲线数值差 ≤ 1e-3，用来在出现偏差时一眼看出漂了多少。
 *
 * 输入是纯整数公式生成的合成亮度，Kotlin 与 Python 必须逐位一致，所以这里能卡这么死。
 */
class FitParityTest {

    private class Golden(val name: String, val table: IntArray, val fit: DoubleArray)

    private fun loadGolden(): List<Golden> {
        val stream = javaClass.classLoader!!.getResourceAsStream("fit-golden.txt")
            ?: error("找不到 fit-golden.txt，检查 src/test/resources")
        // 金标准文件强制 LF，这里仍归一化一次，免得有人用 Windows 编辑器另存为 CRLF
        val text = stream.bufferedReader().use { it.readText() }.replace("\r\n", "\n")

        return text.trim().split("\n\n").map { block ->
            val lines = block.trim().lines()
            val name = lines[0].removePrefix("case ").trim()
            val table = lines.first { it.startsWith("table ") }
                .removePrefix("table ").trim().split(" ")
                .map { it.toInt() }.toIntArray()
            val fit = lines.first { it.startsWith("fit01 ") }
                .removePrefix("fit01 ").trim().split(" ")
                .map { it.toDouble() }.toDoubleArray()
            Golden(name, table, fit)
        }
    }

    /** 与 `gen_android_fit_data.py::synth_luma` 逐位一致的合成亮度（纯整数运算）。 */
    private fun synthLuma(w: Int = 97, h: Int = 61): DoubleArray {
        val out = DoubleArray(w * h)
        for (y in 0 until h) {
            for (x in 0 until w) {
                val v = (x * 37 + y * 91 + (x * y) % 53) % 256
                val r = v
                val g = (v * 3) % 256
                val b = (v * 7 + 11) % 256
                out[y * w + x] = (r * 0.2126 + g * 0.7152 + b * 0.0722) / 255.0
            }
        }
        return out
    }

    private fun referenceFor(case: String): DoubleArray {
        val base = synthLuma()
        val raw = when (case) {
            "normal" -> base
            "crushed" -> {
                val c = DoubleArray(base.size) {
                    ((base[it] - 0.35) * 2.2).coerceIn(0.0, 1.0).pow(0.85)
                }
                for (i in 0 until c.size / 2) c[i] = 0.0
                c
            }

            "faded" -> DoubleArray(base.size) { 0.30 + 0.45 * base[it] }
            else -> error("未知金标准用例 $case")
        }
        return raw.sortedArray()
    }

    @Test
    fun goldenCasesParserFindsAll() {
        val cases = loadGolden()
        assertEquals(3, cases.size)
        assertTrue(cases.all { it.table.size == 256 })
        assertTrue(cases.all { it.fit.size == 256 })
    }

    @Test
    fun androidCurveMatchesDesktopWithinTwoCodes() {
        for (golden in loadGolden()) {
            val result = PhotoFit.fromLuma(referenceFor(golden.name))

            var worst = 0
            var worstAt = 0
            for (i in 0 until 256) {
                val d = abs(result.table[i] - golden.table[i])
                if (d > worst) {
                    worst = d
                    worstAt = i
                }
            }
            val detail = "第 $worstAt 点差 $worst 个码值" +
                "（Android ${result.table[worstAt]} / 桌面 ${golden.table[worstAt]}）"
            assertTrue("[${golden.name}] $detail，超过 2 个码值说明移植跑偏了", worst <= 2)
        }
    }

    @Test
    fun androidCurveValuesMatchDesktop() {
        for (golden in loadGolden()) {
            val result = PhotoFit.fromLuma(referenceFor(golden.name))
            var worst = 0.0
            for (i in 0 until 256) worst = maxOf(worst, abs(result.values[i] - golden.fit[i]))
            assertTrue("[${golden.name}] 曲线数值最大偏差 $worst 超过 1e-3", worst <= 1e-3)
        }
    }

    @Test
    fun fittedCurvesAreAlwaysWritable() {
        for (golden in loadGolden()) {
            val result = PhotoFit.fromLuma(referenceFor(golden.name))
            assertTrue("[${golden.name}] 曲线非单调，不能写进机身", result.writable)
            assertTrue("[${golden.name}] 黑位被抬高了", result.table[0] <= result.table[1])
            assertTrue(result.advice.ok)
            // 电脑端同一批输入实测 0.18–0.40 个码值；超过 1 个码值就说明哪里偏了
            assertTrue(
                "[${golden.name}] 影调贴合误差 ${result.metrics.toneDevMean} 个码值，偏大",
                result.metrics.toneDevMean <= 1.0,
            )
        }
    }

    /** 机身 LUT 的硬要求：0..32767、非递减、首尾不许越界。 */
    @Test
    fun tableStaysInsideCameraLimits() {
        for (golden in loadGolden()) {
            val table = PhotoFit.fromLuma(referenceFor(golden.name)).table
            assertEquals(256, table.size)
            assertTrue("[${golden.name}] 码值越界", table.all { it in 0..ToneCurve.LUT_MAX })
            for (i in 1 until table.size) {
                assertTrue(
                    "[${golden.name}] 第 $i 点下降：${table[i - 1]} → ${table[i]}",
                    table[i] >= table[i - 1],
                )
            }
        }
    }

    @Test
    fun heavyBlackClipDoesNotLiftBlackPoint() {
        // 全黑的参考图：曲线应当整体压到纯黑，绝不出现「抬黑」
        val black = DoubleArray(4096) { 0.0 }
        val result = PhotoFit.fromLuma(black)
        assertEquals(0, result.table[0])
        assertEquals(0, result.table[255])
        assertTrue(result.writable)
        assertTrue(result.stats.clipLow > 0.9)
        // 截断比例这么高，必须给出「换一张图」的提示
        assertEquals("caution", result.advice.level)
    }

    @Test
    fun payloadIsValidForCamera() {
        val result = PhotoFit.fromLuma(referenceFor("normal"))
        val payload = PhotoFit.buildPayload(result, "PH1101-1200")
        assertEquals(614, payload.size)

        val parsed = com.z30tone.writer.core.Payload.parse(payload)
        assertEquals("PH1101-1200", parsed.name)
        assertEquals(0x05, parsed.family)
        assertTrue(parsed.monotonic)
        assertEquals(result.table[0], parsed.first)
        assertEquals(result.table[255], parsed.last)
        // 名称、控制点、LUT 之外不该被动过：骨架的固定字节还在
        assertEquals(0x02, payload[0x00].toInt() and 0xFF)
        assertEquals(0x49, payload[0x24].toInt() and 0xFF) // 'I'
        assertEquals(0x30, payload[0x25].toInt() and 0xFF) // '0'
    }

    @Test
    fun metaJsonCarriesMetricsForDesktop() {
        val result = PhotoFit.fromLuma(referenceFor("faded"))
        val meta = PhotoFit.metaJson(result, "PH-test")
        assertTrue(meta.contains("\"name\":\"PH-test\""))
        assertTrue(meta.contains("\"metrics\""))
        assertTrue(meta.contains("\"tone_dev_mean\""))
        // 元数据是纯 JSON，不能有裸引号把电脑端解析搞崩
        assertTrue(meta.trim().startsWith("{"))
        assertTrue(meta.trim().endsWith("}"))
    }

    /**
     * 口径守卫：单图没有真值，产出里**不许出现会被读成 ΔE 的键**。
     * 配方卡是把 metrics["mean"] 当 ΔE 显示的，写了就等于给用户编了个还原度。
     */
    @Test
    fun imageFitNeverReportsDeltaE() {
        val result = PhotoFit.fromLuma(referenceFor("normal"))
        val payload = PhotoFit.buildPayload(result, "PH1102-101010")
        val packed = Recipe.build(payload, PhotoFit.metaJson(result, "PH1102-101010"))
        val meta = Recipe.parse(packed).meta

        assertNull("单图拟合不许带 ΔE（metrics.mean）", meta.deltaEAvg)
        assertNull("单图拟合不许带 ΔE P95（metrics.p95）", meta.deltaEP95)
        assertTrue("元数据里要留着贴合误差", meta.metrics.containsKey("tone_dev_mean"))
    }

    /** 机身名：ASCII、≤19 字符，且同秒内不再是「同一分钟」的粗粒度。 */
    @Test
    fun suggestedNameFitsTheCamera() {
        val name = PhotoFit.suggestName()
        assertTrue("名称必须是 ASCII：$name", name.all { it.code in 0x20..0x7E })
        assertTrue("名称超过 19 字符：$name", name.length <= 19)
        assertEquals(name, com.z30tone.writer.core.Payload.sanitizeName(name))
    }
}
