package com.z30tone.writer.core.fit

import kotlin.math.abs
import kotlin.math.atan2
import kotlin.math.ceil
import kotlin.math.cos
import kotlin.math.cbrt
import kotlin.math.exp
import kotlin.math.floor
import kotlin.math.hypot
import kotlin.math.pow
import kotlin.math.sin
import kotlin.math.sqrt

/**
 * 色彩空间与色差，对标电脑端 `z30fit/color.py`。
 *
 * 约定：所有 RGB 都是**编码后的 sRGB**（0..1，不做线性化）。相机的 1D LUT 作用在
 * 显示参考信号上，不是线性光；只有转 Lab 时才线性化。
 */
object Colorimetry {

    /** Rec.709 亮度权重。 */
    const val LUMA_R = 0.2126
    const val LUMA_G = 0.7152
    const val LUMA_B = 0.0722

    private const val EPS_CBRT = 216.0 / 24389.0   // (6/29)^3
    private const val KAPPA = 24389.0 / 27.0       // (29/3)^3
    private const val WHITE_X = 0.95047
    private const val WHITE_Y = 1.0
    private const val WHITE_Z = 1.08883

    fun luma(r: Double, g: Double, b: Double): Double = r * LUMA_R + g * LUMA_G + b * LUMA_B

    fun srgbToLinear(c: Double): Double =
        if (c <= 0.04045) c / 12.92 else ((c + 0.055) / 1.055).pow(2.4)

    /** 编码 sRGB(0..1) → CIELAB(D65)。 */
    fun rgbToLab(r: Double, g: Double, b: Double): DoubleArray {
        val lr = srgbToLinear(r.coerceIn(0.0, 1.0))
        val lg = srgbToLinear(g.coerceIn(0.0, 1.0))
        val lb = srgbToLinear(b.coerceIn(0.0, 1.0))

        val x = (0.4124564 * lr + 0.3575761 * lg + 0.1804375 * lb) / WHITE_X
        val y = (0.2126729 * lr + 0.7151522 * lg + 0.0721750 * lb) / WHITE_Y
        val z = (0.0193339 * lr + 0.1191920 * lg + 0.9503041 * lb) / WHITE_Z

        val fx = if (x > EPS_CBRT) cbrt(x.coerceAtLeast(0.0)) else (KAPPA * x + 16.0) / 116.0
        val fy = if (y > EPS_CBRT) cbrt(y.coerceAtLeast(0.0)) else (KAPPA * y + 16.0) / 116.0
        val fz = if (z > EPS_CBRT) cbrt(z.coerceAtLeast(0.0)) else (KAPPA * z + 16.0) / 116.0

        return doubleArrayOf(
            116.0 * fy - 16.0,
            500.0 * (fx - fy),
            200.0 * (fy - fz),
        )
    }

    /** 灰阶 Lab：a = b = 0，只剩 L\*。曲线对三通道同映射，中性轴上没有色相。 */
    fun grayLab(l: Double): DoubleArray = doubleArrayOf(l, 0.0, 0.0)

    /**
     * CIEDE2000 色差（kL = kC = kH = 1）。
     *
     * 与电脑端 `color.delta_e_2000` 逐项对齐（同一个 Sharma 实现）。
     * 灰阶对灰阶时 a = b = 0，结果退化成 |ΔL\*| / S_L，仍走同一条公式。
     */
    fun deltaE2000(lab1: DoubleArray, lab2: DoubleArray): Double {
        val l1 = lab1[0]
        val a1 = lab1[1]
        val b1 = lab1[2]
        val l2 = lab2[0]
        val a2 = lab2[1]
        val b2 = lab2[2]

        val c1 = hypot(a1, b1)
        val c2 = hypot(a2, b2)
        val cBar = 0.5 * (c1 + c2)
        val cBar7 = cBar.pow(7.0)
        val g = 0.5 * (1.0 - sqrt(cBar7 / (cBar7 + 25.0.pow(7.0))))

        val a1p = (1.0 + g) * a1
        val a2p = (1.0 + g) * a2
        val c1p = hypot(a1p, b1)
        val c2p = hypot(a2p, b2)

        var h1p = normalizeHue(Math.toDegrees(atan2(b1, a1p)))
        var h2p = normalizeHue(Math.toDegrees(atan2(b2, a2p)))
        if (c1p == 0.0) h1p = 0.0
        if (c2p == 0.0) h2p = 0.0

        val dLp = l2 - l1
        val dCp = c2p - c1p

        var dhp = h2p - h1p
        if (dhp > 180.0) dhp -= 360.0
        if (dhp < -180.0) dhp += 360.0
        if (c1p * c2p == 0.0) dhp = 0.0
        val dHp = 2.0 * sqrt(c1p * c2p) * sin(Math.toRadians(dhp) / 2.0)

        val lBar = 0.5 * (l1 + l2)
        val cBarP = 0.5 * (c1p + c2p)

        val hSum = h1p + h2p
        val hDiff = abs(h1p - h2p)
        val hBarP = when {
            c1p * c2p == 0.0 -> hSum
            hDiff <= 180.0 -> hSum / 2.0
            hSum < 360.0 -> (hSum + 360.0) / 2.0
            else -> (hSum - 360.0) / 2.0
        }

        val t = 1.0 -
            0.17 * cos(Math.toRadians(hBarP - 30.0)) +
            0.24 * cos(Math.toRadians(2.0 * hBarP)) +
            0.32 * cos(Math.toRadians(3.0 * hBarP + 6.0)) -
            0.20 * cos(Math.toRadians(4.0 * hBarP - 63.0))

        val dTheta = 30.0 * exp(-(((hBarP - 275.0) / 25.0) * ((hBarP - 275.0) / 25.0)))
        val cBarP7 = cBarP.pow(7.0)
        val rC = 2.0 * sqrt(cBarP7 / (cBarP7 + 25.0.pow(7.0)))
        val sL = 1.0 + (0.015 * (lBar - 50.0) * (lBar - 50.0)) /
            sqrt(20.0 + (lBar - 50.0) * (lBar - 50.0))
        val sC = 1.0 + 0.045 * cBarP
        val sH = 1.0 + 0.015 * cBarP * t
        val rT = -sin(Math.toRadians(2.0 * dTheta)) * rC

        val tL = dLp / sL
        val tC = dCp / sC
        val tH = dHp / sH

        return sqrt(tL * tL + tC * tC + tH * tH + rT * tC * tH)
    }

    private fun normalizeHue(deg: Double): Double {
        val m = deg % 360.0
        return if (m < 0.0) m + 360.0 else m
    }

    /**
     * 排序数组上的分位数，等价于 `np.quantile(a, p)` 的默认线性插值。
     * 参考图侧和典型场景侧必须用同一套插值，否则两条分位曲线会错位。
     */
    fun quantileSorted(sorted: DoubleArray, p: Double): Double {
        if (sorted.isEmpty()) return 0.0
        if (sorted.size == 1) return sorted[0]
        val pos = p.coerceIn(0.0, 1.0) * (sorted.size - 1)
        val lo = floor(pos).toInt().coerceIn(0, sorted.size - 1)
        val hi = ceil(pos).toInt().coerceIn(0, sorted.size - 1)
        val frac = pos - lo
        return sorted[lo] + (sorted[hi] - sorted[lo]) * frac
    }
}
