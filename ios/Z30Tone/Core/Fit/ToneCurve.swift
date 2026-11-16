import Foundation

/// 单调三次样条（Fritsch–Carlson / PCHIP）、保序回归、量化与断层检测。
///
/// 逐行对标 `android/.../core/fit/ToneCurve.kt` 与电脑端 `z30fit/curve.py`：
/// 同样的节点数、同样的端点钉法、同样的平滑先验（二阶差分 λ = 0.02）、同样的
/// 高斯-牛顿步长序列。机身 LUT 必须单调，一旦非单调，对应亮度段就会出现反转或
/// 塌陷，所以每一步都做保序投影，而不是拟合完再修。
///
/// 改这里之前先看 Kotlin 那边的注释——两端必须逐点对得上。
enum ToneCurve {

    /// 相机 LUT 是 15 位：0..32767。
    static let lutMax = Payload.lutMax

    // MARK: - PCHIP

    /// PCHIP 在各节点处的导数。要求 x 递增（出现重复点时按 0 斜率处理）。
    static func pchipSlopes(_ x: [Double], _ y: [Double]) -> [Double] {
        let n = x.count
        if n < 2 { return [Double](repeating: 0, count: n) }
        if n == 2 {
            let h = x[1] - x[0]
            let d = h != 0 ? (y[1] - y[0]) / h : 0
            return [d, d]
        }

        var h = [Double](repeating: 0, count: n - 1)
        var delta = [Double](repeating: 0, count: n - 1)
        for i in 0..<(n - 1) {
            h[i] = x[i + 1] - x[i]
            delta[i] = h[i] != 0 ? (y[i + 1] - y[i]) / h[i] : 0
        }

        var d = [Double](repeating: 0, count: n)
        if n > 2 {
            for i in 1..<(n - 1) {
                let d0 = delta[i - 1]
                let d1 = delta[i]
                if d0 * d1 <= 0 { continue }
                let w1 = 2.0 * h[i] + h[i - 1]
                let w2 = h[i] + 2.0 * h[i - 1]
                let den = w1 / d0 + w2 / d1
                d[i] = den != 0 ? (w1 + w2) / den : 0
            }
        }

        // 端点：三点插值公式 + Fritsch–Carlson 限幅
        d[0] = ((2.0 * h[0] + h[1]) * delta[0] - h[0] * delta[1]) / (h[0] + h[1])
        if sgn(d[0]) != sgn(delta[0]) {
            d[0] = 0
        } else if sgn(delta[0]) != sgn(delta[1]) && abs(d[0]) > 3.0 * abs(delta[0]) {
            d[0] = 3.0 * delta[0]
        }

        let last = n - 1
        d[last] = ((2.0 * h[last - 1] + h[last - 2]) * delta[last - 1]
                   - h[last - 1] * delta[last - 2]) / (h[last - 1] + h[last - 2])
        if sgn(d[last]) != sgn(delta[last - 1]) {
            d[last] = 0
        } else if sgn(delta[last - 1]) != sgn(delta[last - 2])
                    && abs(d[last]) > 3.0 * abs(delta[last - 1]) {
            d[last] = 3.0 * delta[last - 1]
        }

        return d
    }

    /// 在 xq 处求 PCHIP 值；xq 超出 [x0, xn] 时按端点值截断。
    static func pchipEval(_ x: [Double], _ y: [Double], _ xq: [Double]) -> [Double] {
        let n = x.count
        var out = [Double](repeating: 0, count: xq.count)
        if n == 0 { return out }
        if n == 1 { return out.map { _ in y[0] } }

        let d = pchipSlopes(x, y)
        let lo = x[0]
        let hi = x[n - 1]

        for k in xq.indices {
            let q = min(max(xq[k], lo), hi)
            var idx = searchRight(x, q) - 1
            idx = min(max(idx, 0), n - 2)

            let h = x[idx + 1] - x[idx]
            let t = h != 0 ? (q - x[idx]) / h : 0
            let t2 = t * t
            let t3 = t2 * t
            let h00 = 2.0 * t3 - 3.0 * t2 + 1.0
            let h10 = t3 - 2.0 * t2 + t
            let h01 = -2.0 * t3 + 3.0 * t2
            let h11 = t3 - t2
            out[k] = h00 * y[idx] + h10 * h * d[idx] + h01 * y[idx + 1] + h11 * h * d[idx + 1]
        }
        return out
    }

    /// `np.searchsorted(x, q, side: "right")`。
    static func searchRight(_ x: [Double], _ q: Double) -> Int {
        var low = 0
        var high = x.count
        while low < high {
            let mid = (low + high) >> 1
            if q < x[mid] { high = mid } else { low = mid + 1 }
        }
        return low
    }

    private static func sgn(_ v: Double) -> Double {
        v > 0 ? 1 : (v < 0 ? -1 : 0)
    }

    /// 以节点值参数化的单调曲线。
    struct MonotoneSpline {
        let knots: [Double]
        let values: [Double]

        func callAsFunction(_ xq: [Double]) -> [Double] {
            pchipEval(knots, values, xq)
        }

        func sample(_ n: Int = Payload.lutPoints) -> [Double] {
            let grid = (0..<n).map { n == 1 ? 0 : Double($0) / Double(n - 1) }
            return callAsFunction(grid)
        }

        var monotone: Bool {
            guard values.count > 1 else { return true }
            for i in 1..<values.count where values[i] < values[i - 1] - 1e-12 { return false }
            return true
        }
    }

    // MARK: - 保序回归

    /// 加权保序回归（PAVA）：把 y 投影成非递减序列，L2 最优。
    static func pava(_ y: [Double], _ w: [Double]? = nil) -> [Double] {
        let n = y.count
        if n == 0 { return [] }
        let weight = w ?? [Double](repeating: 1, count: n)

        var vals = [Double](repeating: 0, count: n)
        var weights = [Double](repeating: 0, count: n)
        var counts = [Int](repeating: 0, count: n)
        var size = 0

        for i in 0..<n {
            size += 1
            vals[size - 1] = y[i]
            weights[size - 1] = weight[i]
            counts[size - 1] = 1
            while size > 1 && vals[size - 2] > vals[size - 1] {
                let v2 = vals[size - 1]
                let w2 = weights[size - 1]
                let c2 = counts[size - 1]
                size -= 1
                let v1 = vals[size - 1]
                let w1 = weights[size - 1]
                let c1 = counts[size - 1]
                let tw = w1 + w2
                vals[size - 1] = tw > 0 ? (v1 * w1 + v2 * w2) / tw : 0
                weights[size - 1] = tw
                counts[size - 1] = c1 + c2
            }
        }

        var out = [Double](repeating: 0, count: n)
        var pos = 0
        for b in 0..<size {
            for k in 0..<counts[b] { out[pos + k] = vals[b] }
            pos += counts[b]
        }
        return out
    }

    // MARK: - 单调最小二乘

    /// 用单调三次样条拟合 (x, y)，权重 w。
    ///
    /// 参数化：节点值 v（K 个），前向模型 = PCHIP(knots, v)(x)。
    /// 每步高斯-牛顿后做 PAVA 保序投影，保证输出曲线单调 —— 这是「不出现断层」的
    /// 结构保证，而不是事后修补。
    static func fitMonotoneCurve(
        xIn: [Double],
        yIn: [Double],
        wIn: [Double]? = nil,
        nKnots: Int = 33,
        pinEndpoints: Bool = true,
        endpointWeight: Double = 40.0,
        smoothness: Double = 0.02,
        maxIter: Int = 60,
        tol: Double = 1e-9
    ) -> MonotoneSpline {
        precondition(xIn.count == yIn.count, "x 与 y 长度必须一致")

        var keep: [Int] = []
        for i in xIn.indices where xIn[i].isFinite && yIn[i].isFinite {
            keep.append(i)
        }
        guard !keep.isEmpty else {
            return MonotoneSpline(knots: [0, 1], values: [0, 1])
        }
        // 稳定排序：值相同按原索引，和 Kotlin 的 sortedBy 一致。
        keep.sort { a, b in xIn[a] == xIn[b] ? a < b : xIn[a] < xIn[b] }

        var x = keep.map { xIn[$0] }
        var y = keep.map { yIn[$0] }
        var w = keep.map { wIn?[$0] ?? 1.0 }

        // 权重归一化，让不同量级的采样方式可比较
        let wAvg = w.reduce(0.0, +) / Double(w.count)
        let wScale = max(wAvg, 1e-12)
        for i in w.indices { w[i] /= wScale }

        // 同值聚合（加权均值）——同一个输入值出现几百次时不去重，雅可比会病态。
        do {
            var ux: [Double] = []
            var uy: [Double] = []
            var uw: [Double] = []
            ux.reserveCapacity(x.count)
            uy.reserveCapacity(x.count)
            uw.reserveCapacity(x.count)
            var i = 0
            while i < x.count {
                var j = i
                var sw = 0.0
                var sy = 0.0
                while j < x.count && x[j] == x[i] {
                    sw += w[j]
                    sy += w[j] * y[j]
                    j += 1
                }
                ux.append(x[i])
                uw.append(max(sw, 1e-12))
                uy.append(sy / max(sw, 1e-12))
                i = j
            }
            if ux.count < x.count {
                x = ux
                y = uy
                w = uw
            }
        }

        // 端点钉住（曲线两头必须落在真实端点上，黑位/白位不许自己漂）
        if pinEndpoints {
            let y0 = x[0] <= 1e-9 ? y[0] : interp(0, x, y)
            let y1 = x[x.count - 1] >= 1.0 - 1e-9 ? y[y.count - 1] : interp(1, x, y)
            let wPin = (w.reduce(0.0, +) / Double(w.count)) * endpointWeight

            var nx = x
            var ny = y
            var nw = w
            nx.append(0)
            ny.append(y0)
            nw.append(wPin)
            nx.append(1)
            ny.append(y1)
            nw.append(wPin)

            let order = nx.indices.sorted { a, b in nx[a] == nx[b] ? a < b : nx[a] < nx[b] }
            x = order.map { nx[$0] }
            y = order.map { ny[$0] }
            w = order.map { nw[$0] }
        }

        // 节点数不超过有效支撑点（另留余量给样条自由度）
        let nUnique = Set(x).count
        let k = min(nKnots, max(2, nUnique))
        let knots = (0..<k).map { Double($0) / Double(k - 1) }

        var v = pava(knots.map { interp($0, x, y) })
        for i in v.indices { v[i] = min(max(v[i], 0), 1) }

        // 二阶差分罚矩阵 P = D2ᵀ·D2（平滑先验）
        var p = [[Double]](repeating: [Double](repeating: 0, count: k), count: k)
        if k >= 3 {
            var d2 = [[Double]](repeating: [Double](repeating: 0, count: k), count: k - 2)
            for r in 0..<(k - 2) {
                d2[r][r] = 1
                d2[r][r + 1] = -2
                d2[r][r + 2] = 1
            }
            for i in 0..<k {
                for j in 0..<k {
                    var s = 0.0
                    for r in 0..<(k - 2) { s += d2[r][i] * d2[r][j] }
                    p[i][j] = s
                }
            }
        }

        let n = x.count

        func model(_ vv: [Double]) -> [Double] {
            pchipEval(knots, vv, x)
        }

        func cost(_ vv: [Double]) -> Double {
            let pred = model(vv)
            var s = 0.0
            for i in 0..<n {
                let r = w[i] * (pred[i] - y[i])
                s += r * r
            }
            var quad = 0.0
            for i in 0..<k {
                var row = 0.0
                for j in 0..<k { row += p[i][j] * vv[j] }
                quad += vv[i] * row
            }
            return 0.5 * s + 0.5 * smoothness * quad
        }

        var lam = 1e-6
        var c0 = cost(v)
        let eps = 1e-6

        for _ in 0..<maxIter {
            let pred = model(v)
            var r = [Double](repeating: 0, count: n)
            for i in 0..<n { r[i] = w[i] * (pred[i] - y[i]) }

            // 数值雅可比（与电脑端一致：前向差分，步长 1e-6）
            var jac = [[Double]](repeating: [Double](repeating: 0, count: k), count: n)
            var vp = v
            for col in 0..<k {
                vp[col] = v[col] + eps
                let shifted = model(vp)
                vp[col] = v[col]
                for row in 0..<n {
                    jac[row][col] = w[row] * (shifted[row] - pred[row]) / eps
                }
            }

            var jtj = [[Double]](repeating: [Double](repeating: 0, count: k), count: k)
            for i in 0..<k {
                for j in i..<k {
                    var s = 0.0
                    for row in 0..<n { s += jac[row][i] * jac[row][j] }
                    let value = s + smoothness * p[i][j]
                    jtj[i][j] = value
                    jtj[j][i] = value
                }
            }

            var grad = [Double](repeating: 0, count: k)
            for i in 0..<k {
                var s = 0.0
                for row in 0..<n { s += jac[row][i] * r[row] }
                var pv = 0.0
                for j in 0..<k { pv += p[i][j] * v[j] }
                grad[i] = s + smoothness * pv
            }

            var a = [[Double]](repeating: [Double](repeating: 0, count: k), count: k)
            for i in 0..<k {
                for j in 0..<k {
                    let damping = i == j ? jtj[i][i] + 1e-9 : 0
                    a[i][j] = jtj[i][j] + lam * damping
                }
            }
            guard let dv = luSolve(a, grad.map { -$0 }) else { break }

            var accepted = false
            var alpha = 1.0
            for _ in 0..<8 {
                var cand = [Double](repeating: 0, count: k)
                for i in 0..<k { cand[i] = min(max(v[i] + alpha * dv[i], 0), 1) }
                let projected = pava(cand)
                let c1 = cost(projected)
                if c1 < c0 - 1e-14 {
                    v = projected
                    c0 = c1
                    lam = max(lam * 0.5, 1e-9)
                    accepted = true
                    break
                }
                alpha *= 0.5
            }

            if !accepted {
                lam *= 8
                if lam > 1e6 { break }
                continue
            }
            if (dv.map { abs(alpha * $0) }.max() ?? 0) < tol { break }
        }

        var projected = pava(v)
        for i in projected.indices { projected[i] = min(max(projected[i], 0), 1) }
        return MonotoneSpline(knots: knots, values: projected)
    }

    /// `np.interp(xq, x, y)`：区间外按端点常数延长。
    static func interp(_ xq: Double, _ x: [Double], _ y: [Double]) -> Double {
        if x.isEmpty { return 0 }
        if x.count == 1 { return y[0] }
        if xq <= x[0] { return y[0] }
        if xq >= x[x.count - 1] { return y[y.count - 1] }
        var idx = searchRight(x, xq) - 1
        idx = min(max(idx, 0), x.count - 2)
        let h = x[idx + 1] - x[idx]
        if h == 0 { return y[idx] }
        let t = (xq - x[idx]) / h
        return y[idx] + (y[idx + 1] - y[idx]) * t
    }

    /// 高斯消元 + 部分主元（对应 `np.linalg.solve`）。奇异时返回 nil。
    static func luSolve(_ matrix: [[Double]], _ rhs: [Double]) -> [Double]? {
        let n = rhs.count
        guard n > 0 else { return [] }
        var a = matrix
        var b = rhs

        for col in 0..<n {
            var pivot = col
            var row = col + 1
            while row < n {
                if abs(a[row][col]) > abs(a[pivot][col]) { pivot = row }
                row += 1
            }
            if abs(a[pivot][col]) < 1e-300 { return nil }
            if pivot != col {
                a.swapAt(pivot, col)
                b.swapAt(pivot, col)
            }
            let diag = a[col][col]
            row = col + 1
            while row < n {
                let factor = a[row][col] / diag
                if factor != 0 {
                    var c = col
                    while c < n {
                        a[row][c] -= factor * a[col][c]
                        c += 1
                    }
                    b[row] -= factor * b[col]
                }
                row += 1
            }
        }

        var out = [Double](repeating: 0, count: n)
        var row = n - 1
        while row >= 0 {
            var s = b[row]
            var c = row + 1
            while c < n {
                s -= a[row][c] * out[c]
                c += 1
            }
            out[row] = s / a[row][row]
            row -= 1
        }
        return out
    }

    // MARK: - 量化与断层

    /// 0..1 的曲线量化成 15 位码值。Swift 默认 `.rounded()` 是「四舍六入五成双」，
    /// 与 Kotlin 的 `Math.rint` / numpy 的 `np.rint` 一致。
    static func quantize(_ values: [Double], _ vmax: Int = lutMax) -> [Int] {
        values.map { Int((min(max($0, 0), 1) * Double(vmax)).rounded()) }
    }

    struct Banding {
        let monotone: Bool
        let maxFlatRun: Int
        let flatRuns: [[Int]]
        let artifactFlats: [[Int]]
        let max8bitDupes: Int
        let blackPoint: Int
        let whitePoint: Int
        let minSlope: Double
        let maxSlope: Double
        let distinctCodes: Int

        /// 最长的「参考本来有变化、却被压平」的段。1 表示没有塌陷。
        var worstArtifactRun: Int { artifactFlats.map { $0[1] }.max() ?? 1 }
        var hasArtifact: Bool { !artifactFlats.isEmpty }
    }

    /// 量化后曲线的塌陷 / 断层检测。
    ///
    /// - flatRun：连续相同输出值的最长长度（1 = 每个点都不同，> 1 就是塌陷）
    /// - 8bitDupes：落到同一个 8 位输出的点数（> 1 表示该段被压死）
    ///
    /// 传 reference 时会额外判断每段塌陷「是不是参考本身就这么黑」。胶片曲线常在趾部
    /// 把 0–5% 直接压到纯黑，那是还原，不是拟合缺陷。
    ///
    /// `minArtifactLevels` 是材料性阈值：一段塌陷只有在参考曲线本应上升超过这么多
    /// 个 8 位码值时才算缺陷。低于阈值就当没问题，否则每张照片都会亮黄灯。
    static func bandingAnalysis(
        _ table: [Int],
        reference: [Double]? = nil,
        minArtifactLevels: Double = 4.0
    ) -> Banding {
        let n = table.count
        var diffs = [Int](repeating: 0, count: n - 1)
        for i in 0..<(n - 1) { diffs[i] = table[i + 1] - table[i] }

        var flatRuns: [[Int]] = []
        var runStart = 0
        for i in 1..<n {
            if diffs[i - 1] != 0 {
                if i - runStart > 1 { flatRuns.append([runStart, i - runStart]) }
                runStart = i
            }
        }
        if n - runStart > 1 { flatRuns.append([runStart, n - runStart]) }

        var q8 = [Int](repeating: 0, count: n)
        for i in 0..<n {
            q8[i] = Int((Double(table[i]) / Double(lutMax) * 255).rounded())
        }

        var dupRuns: [[Int]] = []
        var rs = 0
        for i in 1..<n {
            if q8[i] != q8[i - 1] {
                if i - rs > 1 { dupRuns.append([rs, i - rs]) }
                rs = i
            }
        }
        if n - rs > 1 { dupRuns.append([rs, n - rs]) }

        var slope = [Double](repeating: 0, count: n - 1)
        for i in 0..<(n - 1) {
            slope[i] = Double(diffs[i]) / Double(lutMax) * Double(n - 1)
        }

        var artifactFlats: [[Int]] = []
        if let reference, reference.count == n {
            let minLoss = minArtifactLevels / 255.0
            for run in flatRuns {
                let start = run[0]
                let length = run[1]
                let end = min(start + length, n - 1)
                if end <= start { continue }
                if reference[end] - reference[start] >= minLoss {
                    artifactFlats.append(run)
                }
            }
        }

        return Banding(
            monotone: diffs.allSatisfy { $0 >= 0 },
            maxFlatRun: max(1, flatRuns.map { $0[1] }.max() ?? 1),
            flatRuns: Array(flatRuns.prefix(8)),
            artifactFlats: Array(artifactFlats.prefix(8)),
            max8bitDupes: max(1, dupRuns.map { $0[1] }.max() ?? 1),
            blackPoint: table[0],
            whitePoint: table[n - 1],
            minSlope: slope.min() ?? 0,
            maxSlope: slope.max() ?? 0,
            distinctCodes: Set(q8).count
        )
    }

    /// 曲线特征（黑位/白位/18% 灰/三段斜率），与电脑端 tone_stats 同一套口径。
    struct Tone {
        let black: Double
        let white: Double
        let mid18: Double
        let contrast2575: Double
        let slopeToe: Double
        let slopeMid: Double
        let slopeShoulder: Double

        static func of(_ values01: [Double]) -> Tone {
            let n = values01.count
            let grid = (0..<n).map { n == 1 ? 0 : Double($0) / Double(n - 1) }
            return Tone(
                black: values01[0],
                white: values01[n - 1],
                mid18: interp(0.18, grid, values01),
                contrast2575: (interp(0.75, grid, values01) - interp(0.25, grid, values01)) / 0.5,
                slopeToe: (values01[min(8, n - 1)] - values01[0]) * 12.5,
                slopeMid: (values01[min(136, n - 1)] - values01[min(120, n - 1)]) * 16.0,
                slopeShoulder: (values01[n - 1] - values01[max(0, n - 9)]) * 12.5
            )
        }
    }
}
