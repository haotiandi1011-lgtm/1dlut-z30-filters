import Foundation

/// 色彩空间与色差，对标 `android/.../core/fit/Colorimetry.kt` 与电脑端 `z30fit/color.py`。
///
/// 约定：所有 RGB 都是**编码后的 sRGB**（0..1，不做线性化）。相机的 1D LUT 作用在
/// 显示参考信号上，不是线性光；只有转 Lab 时才线性化。
enum Colorimetry {

    /// Rec.709 亮度权重。
    static let lumaR = 0.2126
    static let lumaG = 0.7152
    static let lumaB = 0.0722

    private static let epsCbrt = 216.0 / 24389.0   // (6/29)^3
    private static let kappa = 24389.0 / 27.0      // (29/3)^3
    private static let whiteX = 0.95047
    private static let whiteY = 1.0
    private static let whiteZ = 1.08883

    static func luma(_ r: Double, _ g: Double, _ b: Double) -> Double {
        r * lumaR + g * lumaG + b * lumaB
    }

    static func srgbToLinear(_ c: Double) -> Double {
        c <= 0.04045 ? c / 12.92 : pow((c + 0.055) / 1.055, 2.4)
    }

    /// 编码 sRGB(0..1) → CIELAB(D65)。
    static func rgbToLab(_ r: Double, _ g: Double, _ b: Double) -> (l: Double, a: Double, b: Double) {
        let lr = srgbToLinear(min(max(r, 0), 1))
        let lg = srgbToLinear(min(max(g, 0), 1))
        let lb = srgbToLinear(min(max(b, 0), 1))

        let x = (0.4124564 * lr + 0.3575761 * lg + 0.1804375 * lb) / whiteX
        let y = (0.2126729 * lr + 0.7151522 * lg + 0.0721750 * lb) / whiteY
        let z = (0.0193339 * lr + 0.1191920 * lg + 0.9503041 * lb) / whiteZ

        let fx = x > epsCbrt ? cbrt(max(x, 0)) : (kappa * x + 16.0) / 116.0
        let fy = y > epsCbrt ? cbrt(max(y, 0)) : (kappa * y + 16.0) / 116.0
        let fz = z > epsCbrt ? cbrt(max(z, 0)) : (kappa * z + 16.0) / 116.0

        return (
            l: 116.0 * fy - 16.0,
            a: 500.0 * (fx - fy),
            b: 200.0 * (fy - fz)
        )
    }

    /// 灰阶 Lab：a = b = 0，只剩 L*。曲线对三通道同映射，中性轴上没有色相。
    static func grayLab(_ l: Double) -> (l: Double, a: Double, b: Double) {
        (l: l, a: 0, b: 0)
    }

    /// CIEDE2000 色差（kL = kC = kH = 1），与电脑端逐项对齐（同一个 Sharma 实现）。
    static func deltaE2000(
        _ lab1: (l: Double, a: Double, b: Double),
        _ lab2: (l: Double, a: Double, b: Double)
    ) -> Double {
        let l1 = lab1.l, a1 = lab1.a, b1 = lab1.b
        let l2 = lab2.l, a2 = lab2.a, b2 = lab2.b

        let c1 = hypot(a1, b1)
        let c2 = hypot(a2, b2)
        let cBar = 0.5 * (c1 + c2)
        let cBar7 = pow(cBar, 7)
        let g = 0.5 * (1.0 - sqrt(cBar7 / (cBar7 + pow(25.0, 7))))

        let a1p = (1.0 + g) * a1
        let a2p = (1.0 + g) * a2
        let c1p = hypot(a1p, b1)
        let c2p = hypot(a2p, b2)

        var h1p = normalizeHue(atan2(b1, a1p) * 180.0 / Double.pi)
        var h2p = normalizeHue(atan2(b2, a2p) * 180.0 / Double.pi)
        if c1p == 0 { h1p = 0 }
        if c2p == 0 { h2p = 0 }

        let dLp = l2 - l1
        let dCp = c2p - c1p

        var dhp = h2p - h1p
        if dhp > 180 { dhp -= 360 }
        if dhp < -180 { dhp += 360 }
        if c1p * c2p == 0 { dhp = 0 }
        let dHp = 2.0 * sqrt(c1p * c2p) * sin(dhp * Double.pi / 360.0)

        let lBar = 0.5 * (l1 + l2)
        let cBarP = 0.5 * (c1p + c2p)

        let hSum = h1p + h2p
        let hDiff = abs(h1p - h2p)
        let hBarP: Double
        if c1p * c2p == 0 {
            hBarP = hSum
        } else if hDiff <= 180 {
            hBarP = hSum / 2
        } else if hSum < 360 {
            hBarP = (hSum + 360) / 2
        } else {
            hBarP = (hSum - 360) / 2
        }

        let deg = { (v: Double) -> Double in v * Double.pi / 180.0 }
        let t = 1.0
            - 0.17 * cos(deg(hBarP - 30))
            + 0.24 * cos(deg(2 * hBarP))
            + 0.32 * cos(deg(3 * hBarP + 6))
            - 0.20 * cos(deg(4 * hBarP - 63))

        let dTheta = 30.0 * exp(-pow((hBarP - 275.0) / 25.0, 2))
        let cBarP7 = pow(cBarP, 7)
        let rC = 2.0 * sqrt(cBarP7 / (cBarP7 + pow(25.0, 7)))
        let sL = 1.0 + (0.015 * (lBar - 50) * (lBar - 50))
            / sqrt(20.0 + (lBar - 50) * (lBar - 50))
        let sC = 1.0 + 0.045 * cBarP
        let sH = 1.0 + 0.015 * cBarP * t
        let rT = -sin(deg(2 * dTheta)) * rC

        let tL = dLp / sL
        let tC = dCp / sC
        let tH = dHp / sH

        return sqrt(tL * tL + tC * tC + tH * tH + rT * tC * tH)
    }

    private static func normalizeHue(_ deg: Double) -> Double {
        let m = deg.truncatingRemainder(dividingBy: 360.0)
        return m < 0 ? m + 360 : m
    }

    /// 排序数组上的分位数，等价于 `np.quantile(a, p)` 的默认线性插值。
    /// 参考图侧和典型场景侧必须用同一套插值，否则两条分位曲线会错位。
    static func quantileSorted(_ sorted: [Double], _ p: Double) -> Double {
        if sorted.isEmpty { return 0 }
        if sorted.count == 1 { return sorted[0] }
        let pos = min(max(p, 0), 1) * Double(sorted.count - 1)
        let lo = min(max(Int(pos.rounded(.down)), 0), sorted.count - 1)
        let hi = min(max(Int(pos.rounded(.up)), 0), sorted.count - 1)
        let frac = pos - Double(lo)
        return sorted[lo] + (sorted[hi] - sorted[lo]) * frac
    }
}
