package com.z30tone.writer

import com.z30tone.writer.core.fit.ToneCurve
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import kotlin.math.abs

/**
 * 曲线层的行为约束：单调、端点不漂、量化不塌陷。
 * 这几个性质是「写进机身不出色阶断层」的唯一保证，比数值精度更重要。
 */
class ToneCurveTest {

    private fun grid(n: Int = 256): DoubleArray = DoubleArray(n) { it.toDouble() / (n - 1) }

    @Test
    fun pchipOfIdentityIsIdentity() {
        val x = DoubleArray(5) { it / 4.0 }
        val y = x.copyOf()
        val out = ToneCurve.pchipEval(x, y, grid(17))
        for (i in 0 until 17) assertEquals(i / 16.0, out[i], 1e-12)
    }

    @Test
    fun pchipKeepsMonotoneInputMonotone() {
        val x = doubleArrayOf(0.0, 0.1, 0.35, 0.6, 0.9, 1.0)
        val y = doubleArrayOf(0.0, 0.02, 0.30, 0.62, 0.94, 1.0)
        val out = ToneCurve.pchipEval(x, y, grid(256))
        for (i in 1 until out.size) assertTrue("第 $i 点下降", out[i] >= out[i - 1] - 1e-12)
        // 端点必须精确落在数据端点上
        assertEquals(0.0, out[0], 1e-12)
        assertEquals(1.0, out[out.size - 1], 1e-12)
    }

    @Test
    fun pchipClampsOutsideRange() {
        val x = doubleArrayOf(0.2, 0.8)
        val y = doubleArrayOf(0.1, 0.9)
        val out = ToneCurve.pchipEval(x, y, doubleArrayOf(0.0, 0.5, 1.0))
        assertEquals(0.1, out[0], 1e-12)
        assertEquals(0.9, out[2], 1e-12)
        assertEquals(0.5, out[1], 1e-12)
    }

    @Test
    fun pavaProjectsToNonDecreasing() {
        val y = doubleArrayOf(0.5, 0.2, 0.9, 0.1, 0.4)
        val out = ToneCurve.pava(y)
        for (i in 1 until out.size) assertTrue(out[i] >= out[i - 1] - 1e-12)
        // L2 最优：整体均值不变
        assertEquals(y.average(), out.average(), 1e-12)
    }

    @Test
    fun pavaLeavesSortedDataAlone() {
        val y = doubleArrayOf(0.1, 0.2, 0.3)
        val out = ToneCurve.pava(y)
        for (i in y.indices) assertEquals(y[i], out[i], 1e-15)
    }

    @Test
    fun fitPinsEndpointsAndStaysMonotone() {
        val x = grid(64)
        // 目标带噪、且两端有抬黑抬白
        val y = DoubleArray(64) {
            val v = 0.06 + 0.88 * x[it] + 0.02 * Math.sin(x[it] * 12.0)
            v.coerceIn(0.0, 1.0)
        }
        val curve = ToneCurve.fitMonotoneCurve(x, y, null, nKnots = 17, pinEndpoints = true)
        assertTrue(curve.monotone)

        val s = curve.sample(256)
        for (i in 1 until s.size) assertTrue("第 $i 点下降", s[i] >= s[i - 1] - 1e-12)
        // 端点钉住：曲线起点应该落在目标起点附近（不会自己抬黑）
        assertEquals(y[0], s[0], 0.01)
        assertEquals(y[y.size - 1], s[s.size - 1], 0.01)
    }

    @Test
    fun fitHandlesDegenerateInput() {
        // 常量目标：不能塌成 NaN，也不能非单调
        val x = grid(32)
        val y = DoubleArray(32) { 0.25 }
        val s = ToneCurve.fitMonotoneCurve(x, y, null, nKnots = 9).sample(64)
        for (v in s) assertTrue(v.isFinite())
        for (i in 1 until s.size) assertTrue(s[i] >= s[i - 1] - 1e-12)
    }

    @Test
    fun quantizeUsesFifteenBit() {
        val table = ToneCurve.quantize(doubleArrayOf(0.0, 0.5, 1.0))
        assertEquals(0, table[0])
        assertEquals(16384, table[1]) // 0.5 * 32767 = 16383.5，np.rint 银行家舍入给 16384
        assertEquals(32767, table[2])
    }

    @Test
    fun quantizeClampsOutOfRange() {
        val table = ToneCurve.quantize(doubleArrayOf(-0.2, 1.7))
        assertEquals(0, table[0])
        assertEquals(ToneCurve.LUT_MAX, table[1])
    }

    @Test
    fun bandingDetectsFlatRun() {
        // 中间压出 6 个相同的码值
        val table = IntArray(256) { it * 128 }
        for (i in 100..105) table[i] = table[100]

        val band = ToneCurve.bandingAnalysis(table)
        assertTrue(band.monotone)
        assertTrue(band.maxFlatRun >= 6)
    }

    @Test
    fun bandingTreatsReferenceBlackClipAsIntentional() {
        // 参考本身在趾部就是纯黑（连续 22 个点没变化）：这段塌陷是还原，不算缺陷
        val table = IntArray(256) { if (it <= 20) 0 else (it - 20) * 140 }
        val reference = DoubleArray(256) { if (it <= 21) 0.0 else (it - 21) / 234.0 * 0.99 }

        val band = ToneCurve.bandingAnalysis(table, reference)
        assertFalse("参考在这段本来就没有变化，不该报缺陷", band.hasArtifact)
    }

    @Test
    fun bandingFlagsFlatteningThatReferenceDoesNotHave() {
        // 参考是直线，我们却把中段压平了 → 必须报出来
        val table = IntArray(256) { it * 128 }
        for (i in 120..140) table[i] = table[120]

        val reference = DoubleArray(256) { it / 255.0 }
        val band = ToneCurve.bandingAnalysis(table, reference)
        assertTrue(band.hasArtifact)
        assertTrue(band.worstArtifactRun >= 20)
    }

    @Test
    fun bandingReportsNonMonotone() {
        val table = IntArray(256) { it * 128 }
        table[200] = 0
        val band = ToneCurve.bandingAnalysis(table)
        assertFalse(band.monotone)
    }

    /**
     * 材料性阈值：肩部 8 点平段，但参考曲线在这一段只涨了约 1 个 8 位码值。
     * 真机 JPEG 几乎张张都有这种平段（实测 8 张里 6 张），报出来就是狼来了。
     */
    @Test
    fun bandingIgnoresShoulderFlatteningNobodyCanSee() {
        val table = IntArray(256) { it * 128 }
        for (i in 216..223) table[i] = table[216]

        // 肩部斜率只有 0.133：8 个点合计涨 0.004 ≈ 1 个 8 位码值
        val reference = DoubleArray(256) { i ->
            val t = i / 255.0
            if (t < 0.85) 0.9 * t else 0.9 * 0.85 + 0.02 * (t - 0.85) / 0.15
        }
        val band = ToneCurve.bandingAnalysis(table, reference)
        assertFalse("这段塌陷肉眼看不出来，不该报缺陷", band.hasArtifact)
        // 平段本身还是要如实统计出来（界面上的「平段 N」用的是这个）
        assertTrue(band.maxFlatRun >= 8)
    }

    @Test
    fun bandingFlagsFlatteningThatCostsAPerceptibleStep() {
        // 中段 12 点平段，参考却是 45° 直线：丢了约 12 个 8 位码值，必须报
        val table = IntArray(256) { it * 128 }
        for (i in 120..131) table[i] = table[120]

        val reference = DoubleArray(256) { it / 255.0 }
        val band = ToneCurve.bandingAnalysis(table, reference)
        assertTrue(band.hasArtifact)
        assertTrue(band.worstArtifactRun >= 12)
    }

    @Test
    fun interpClampsAtBothEnds() {
        val x = doubleArrayOf(0.2, 0.6)
        val y = doubleArrayOf(0.3, 0.7)
        assertEquals(0.3, ToneCurve.interp(0.0, x, y), 1e-12)
        assertEquals(0.7, ToneCurve.interp(1.0, x, y), 1e-12)
        assertEquals(0.5, ToneCurve.interp(0.4, x, y), 1e-12)
        for (v in listOf(0.0, 0.5, 1.0)) assertTrue(abs(ToneCurve.interp(v, x, y)) < 2.0)
    }
}
