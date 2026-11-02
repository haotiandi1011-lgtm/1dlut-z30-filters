package com.z30tone.writer

import com.z30tone.writer.core.fit.Colorimetry
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 色度学层的基准值全部取自电脑端 `z30fit/color.py` 的实际输出（同一套公式），
 * 数字改动就是两边跑偏了。
 */
class ColorimetryTest {

    private fun de(a: DoubleArray, b: DoubleArray) = Colorimetry.deltaE2000(a, b)

    private fun lab(l: Double, a: Double, b: Double) = doubleArrayOf(l, a, b)

    @Test
    fun deltaE2000MatchesReferencePairs() {
        // Sharma 等人 CIEDE2000 测试集里的前几组，电脑端跑出来是这些值
        assertEquals(2.042460, de(lab(50.0, 2.6772, -79.7751), lab(50.0, 0.0, -82.7485)), 1e-5)
        assertEquals(2.861510, de(lab(50.0, 3.1571, -77.2803), lab(50.0, 0.0, -82.7485)), 1e-5)
        assertEquals(0.999999, de(lab(50.0, -1.3802, -84.2814), lab(50.0, 0.0, -82.7485)), 1e-5)
        assertEquals(1.000005, de(lab(50.0, -1.1848, -84.8006), lab(50.0, 0.0, -82.7485)), 1e-5)
    }

    @Test
    fun deltaE2000OnGrayAxis() {
        // 灰阶上 a = b = 0，公式退化成 |ΔL| / S_L，但走的还是同一条实现
        assertEquals(9.470579, de(lab(50.0, 0.0, 0.0), lab(60.0, 0.0, 0.0)), 1e-5)
        assertEquals(60.0, de(lab(20.0, 0.0, 0.0), lab(80.0, 0.0, 0.0)), 1e-5)
        assertEquals(0.0, de(lab(50.0, 0.0, 0.0), lab(50.0, 0.0, 0.0)), 1e-9)
    }

    @Test
    fun deltaE2000IsSymmetric() {
        val a = lab(50.0, 2.6772, -79.7751)
        val b = lab(50.0, 0.0, -82.7485)
        assertEquals(de(a, b), de(b, a), 1e-9)
    }

    @Test
    fun labMatchesKnownValues() {
        // 白点 / 中灰 / 纯红 的 Lab，电脑端同一套矩阵
        val white = Colorimetry.rgbToLab(1.0, 1.0, 1.0)
        assertEquals(100.0, white[0], 1e-4)
        assertEquals(0.0, white[1], 1e-3)
        assertEquals(0.0, white[2], 1e-3)

        val mid = Colorimetry.rgbToLab(0.5, 0.5, 0.5)
        assertEquals(53.3889671, mid[0], 1e-4)

        val red = Colorimetry.rgbToLab(1.0, 0.0, 0.0)
        assertEquals(53.2407941, red[0], 1e-4)
        assertEquals(80.0924596, red[1], 1e-4)
        assertEquals(67.2031965, red[2], 1e-4)

        val black = Colorimetry.rgbToLab(0.0, 0.0, 0.0)
        assertEquals(0.0, black[0], 1e-9)
    }

    @Test
    fun lumaUsesRec709() {
        assertEquals(0.2126, Colorimetry.luma(1.0, 0.0, 0.0), 1e-12)
        assertEquals(0.7152, Colorimetry.luma(0.0, 1.0, 0.0), 1e-12)
        assertEquals(0.0722, Colorimetry.luma(0.0, 0.0, 1.0), 1e-12)
        assertTrue(Colorimetry.luma(0.4, 0.4, 0.4) < 0.41)
    }

    @Test
    fun quantileMatchesNumpyLinear() {
        val sorted = doubleArrayOf(0.0, 0.25, 0.5, 0.75, 1.0)
        assertEquals(0.0, Colorimetry.quantileSorted(sorted, 0.0), 1e-12)
        assertEquals(1.0, Colorimetry.quantileSorted(sorted, 1.0), 1e-12)
        assertEquals(0.5, Colorimetry.quantileSorted(sorted, 0.5), 1e-12)
        // p = 0.125 → pos = 0.5 → 0 与 0.25 的中点
        assertEquals(0.125, Colorimetry.quantileSorted(sorted, 0.125), 1e-12)
        // 单个值 / 越界概率都要安全
        assertEquals(0.7, Colorimetry.quantileSorted(doubleArrayOf(0.7), 0.3), 1e-12)
        assertEquals(1.0, Colorimetry.quantileSorted(sorted, 2.0), 1e-12)
    }
}
