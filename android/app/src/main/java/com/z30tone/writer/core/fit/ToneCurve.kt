package com.z30tone.writer.core.fit

import com.z30tone.writer.core.Payload
import kotlin.math.abs
import kotlin.math.max
import kotlin.math.min
import kotlin.math.sign

/**
 * 单调三次样条（Fritsch–Carlson / PCHIP）、保序回归、量化与断层检测。
 *
 * 逐行对标电脑端 `z30fit/curve.py`：同样的节点数、同样的端点钉法、同样的平滑先验
 * （二阶差分 λ = 0.02）、同样的高斯-牛顿步长序列。改这里之前先看那边的注释——
 * 机身 LUT 必须单调，一旦非单调，对应亮度段就会出现反转或塌陷，所以每一步都做
 * 保序投影，而不是拟合完再修。
 */
object ToneCurve {

    /** 相机 LUT 是 15 位：0..32767。 */
    const val LUT_MAX = Payload.LUT_MAX

    // -- PCHIP --------------------------------------------------------------

    /** PCHIP 在各节点处的导数。要求 x 递增（出现重复点时按 0 斜率处理）。 */
    fun pchipSlopes(x: DoubleArray, y: DoubleArray): DoubleArray {
        val n = x.size
        if (n < 2) return DoubleArray(n)
        if (n == 2) {
            val h = x[1] - x[0]
            val d = if (h != 0.0) (y[1] - y[0]) / h else 0.0
            return doubleArrayOf(d, d)
        }

        val h = DoubleArray(n - 1) { x[it + 1] - x[it] }
        val delta = DoubleArray(n - 1) { if (h[it] != 0.0) (y[it + 1] - y[it]) / h[it] else 0.0 }
        val d = DoubleArray(n)

        // 内部节点：相邻两个差商同号时取加权调和平均，异号时导数为 0（保住极值点）
        for (i in 1 until n - 1) {
            val d0 = delta[i - 1]
            val d1 = delta[i]
            if (d0 * d1 <= 0.0) {
                d[i] = 0.0
                continue
            }
            val w1 = 2.0 * h[i] + h[i - 1]
            val w2 = h[i] + 2.0 * h[i - 1]
            val den = w1 / d0 + w2 / d1
            d[i] = if (den != 0.0) (w1 + w2) / den else 0.0
        }

        // 端点：三点插值公式 + Fritsch–Carlson 限幅
        d[0] = ((2.0 * h[0] + h[1]) * delta[0] - h[0] * delta[1]) / (h[0] + h[1])
        if (sign(d[0]) != sign(delta[0])) {
            d[0] = 0.0
        } else if (sign(delta[0]) != sign(delta[1]) && abs(d[0]) > 3.0 * abs(delta[0])) {
            d[0] = 3.0 * delta[0]
        }

        val last = n - 1
        d[last] = ((2.0 * h[last - 1] + h[last - 2]) * delta[last - 1] -
            h[last - 1] * delta[last - 2]) / (h[last - 1] + h[last - 2])
        if (sign(d[last]) != sign(delta[last - 1])) {
            d[last] = 0.0
        } else if (sign(delta[last - 1]) != sign(delta[last - 2]) &&
            abs(d[last]) > 3.0 * abs(delta[last - 1])
        ) {
            d[last] = 3.0 * delta[last - 1]
        }

        return d
    }

    /** 在 xq 处求 PCHIP 值；xq 超出 [x0, xn] 时按端点值截断。 */
    fun pchipEval(x: DoubleArray, y: DoubleArray, xq: DoubleArray): DoubleArray {
        val n = x.size
        val out = DoubleArray(xq.size)
        if (n == 0) return out
        if (n == 1) {
            java.util.Arrays.fill(out, y[0])
            return out
        }

        val d = pchipSlopes(x, y)
        val lo = x[0]
        val hi = x[n - 1]

        for (k in xq.indices) {
            val q = xq[k].coerceIn(lo, hi)
            var idx = searchRight(x, q) - 1
            idx = idx.coerceIn(0, n - 2)

            val h = x[idx + 1] - x[idx]
            val t = if (h != 0.0) (q - x[idx]) / h else 0.0
            val t2 = t * t
            val t3 = t2 * t
            val h00 = 2.0 * t3 - 3.0 * t2 + 1.0
            val h10 = t3 - 2.0 * t2 + t
            val h01 = -2.0 * t3 + 3.0 * t2
            val h11 = t3 - t2
            out[k] = h00 * y[idx] + h10 * h * d[idx] + h01 * y[idx + 1] + h11 * h * d[idx + 1]
        }
        return out
    }

    /** `np.searchsorted(x, q, side="right")`。 */
    private fun searchRight(x: DoubleArray, q: Double): Int {
        var low = 0
        var high = x.size
        while (low < high) {
            val mid = (low + high) ushr 1
            if (q < x[mid]) high = mid else low = mid + 1
        }
        return low
    }

    /** 以节点值参数化的单调曲线。 */
    class MonotoneSpline(val knots: DoubleArray, values: DoubleArray) {

        val values: DoubleArray = values.copyOf()

        operator fun invoke(xq: DoubleArray): DoubleArray = pchipEval(knots, values, xq)

        fun sample(n: Int = Payload.LUT_POINTS): DoubleArray {
            val grid = DoubleArray(n) { if (n == 1) 0.0 else it.toDouble() / (n - 1) }
            return invoke(grid)
        }

        val monotone: Boolean
            get() = (1 until values.size).all { values[it] >= values[it - 1] - 1e-12 }
    }

    // -- 保序回归 ------------------------------------------------------------

    /** 加权保序回归（PAVA）：把 y 投影成非递减序列，L2 最优。 */
    fun pava(y: DoubleArray, w: DoubleArray? = null): DoubleArray {
        val n = y.size
        if (n == 0) return DoubleArray(0)
        val weight = w ?: DoubleArray(n) { 1.0 }

        val vals = DoubleArray(n)
        val weights = DoubleArray(n)
        val counts = IntArray(n)
        var size = 0

        for (i in 0 until n) {
            size++
            vals[size - 1] = y[i]
            weights[size - 1] = weight[i]
            counts[size - 1] = 1
            while (size > 1 && vals[size - 2] > vals[size - 1]) {
                val v2 = vals[size - 1]
                val w2 = weights[size - 1]
                val c2 = counts[size - 1]
                size--
                val v1 = vals[size - 1]
                val w1 = weights[size - 1]
                val c1 = counts[size - 1]
                val tw = w1 + w2
                vals[size - 1] = if (tw > 0.0) (v1 * w1 + v2 * w2) / tw else 0.0
                weights[size - 1] = tw
                counts[size - 1] = c1 + c2
            }
        }

        val out = DoubleArray(n)
        var pos = 0
        for (b in 0 until size) {
            val v = vals[b]
            for (k in 0 until counts[b]) out[pos + k] = v
            pos += counts[b]
        }
        return out
    }

    // -- 单调最小二乘 --------------------------------------------------------

    /**
     * 用单调三次样条拟合 (x, y)，权重 w。
     *
     * 参数化：节点值 v（K 个），前向模型 = PCHIP(knots, v)(x)。
     * 每步高斯-牛顿后做 PAVA 保序投影，保证输出曲线单调 —— 这是「不出现断层」的
     * 结构保证，而不是事后修补。
     */
    fun fitMonotoneCurve(
        xIn: DoubleArray,
        yIn: DoubleArray,
        wIn: DoubleArray? = null,
        nKnots: Int = 33,
        pinEndpoints: Boolean = true,
        endpointWeight: Double = 40.0,
        smoothness: Double = 0.02,
        maxIter: Int = 60,
        tol: Double = 1e-9,
    ): MonotoneSpline {
        require(xIn.size == yIn.size) { "x 与 y 长度必须一致" }

        // 1) 过滤非有限值，按 x 升序
        val keep = ArrayList<Int>(xIn.size)
        for (i in xIn.indices) {
            if (xIn[i].isFinite() && yIn[i].isFinite()) keep.add(i)
        }
        require(keep.isNotEmpty()) { "没有有效的拟合数据点" }
        keep.sortBy { xIn[it] }

        var x = DoubleArray(keep.size) { xIn[keep[it]] }
        var y = DoubleArray(keep.size) { yIn[keep[it]] }
        var w = DoubleArray(keep.size) { wIn?.get(keep[it]) ?: 1.0 }

        // 权重归一化，让不同量级的采样方式可比较
        val wScale = max(w.average(), 1e-12)
        for (i in w.indices) w[i] /= wScale

        // 2) 同值聚合（加权均值）——同一个输入值出现几百次时不去重，雅可比会病态。
        run {
            val ux = ArrayList<Double>()
            val uy = ArrayList<Double>()
            val uw = ArrayList<Double>()
            var i = 0
            while (i < x.size) {
                var j = i
                var sw = 0.0
                var sy = 0.0
                while (j < x.size && x[j] == x[i]) {
                    sw += w[j]
                    sy += w[j] * y[j]
                    j++
                }
                ux.add(x[i])
                uw.add(max(sw, 1e-12))
                uy.add(sy / max(sw, 1e-12))
                i = j
            }
            if (ux.size < x.size) {
                x = ux.toDoubleArray()
                y = uy.toDoubleArray()
                w = uw.toDoubleArray()
            }
        }

        // 3) 端点钉住（曲线两头必须落在真实端点上，黑位/白位不许自己漂）
        if (pinEndpoints) {
            val y0 = if (x[0] <= 1e-9) y[0] else interp(0.0, x, y)
            val y1 = if (x[x.size - 1] >= 1.0 - 1e-9) y[y.size - 1] else interp(1.0, x, y)
            val wPin = w.average() * endpointWeight

            val nx = DoubleArray(x.size + 2)
            val ny = DoubleArray(y.size + 2)
            val nw = DoubleArray(w.size + 2)
            System.arraycopy(x, 0, nx, 0, x.size)
            System.arraycopy(y, 0, ny, 0, y.size)
            System.arraycopy(w, 0, nw, 0, w.size)
            nx[x.size] = 0.0
            ny[y.size] = y0
            nw[w.size] = wPin
            nx[x.size + 1] = 1.0
            ny[y.size + 1] = y1
            nw[w.size + 1] = wPin

            // 追加的 0/1 可能和原数据点重复或破坏升序，重排一次（原序列已升序，等价稳定排序）
            val order = nx.indices.sortedBy { nx[it] }
            x = DoubleArray(order.size) { nx[order[it]] }
            y = DoubleArray(order.size) { ny[order[it]] }
            w = DoubleArray(order.size) { nw[order[it]] }
        }

        // 4) 节点数不超过有效支撑点（另留余量给样条自由度）
        val nUnique = x.distinct().size
        val k = min(nKnots, max(2, nUnique))
        val knots = DoubleArray(k) { it.toDouble() / (k - 1) }

        var v = pava(DoubleArray(k) { interp(knots[it], x, y) })
        for (i in v.indices) v[i] = v[i].coerceIn(0.0, 1.0)

        // 5) 二阶差分罚矩阵 P = D2ᵀ·D2（平滑先验）
        val p = Array(k) { DoubleArray(k) }
        if (k >= 3) {
            val d2 = Array(k - 2) { DoubleArray(k) }
            for (r in 0 until k - 2) {
                d2[r][r] = 1.0
                d2[r][r + 1] = -2.0
                d2[r][r + 2] = 1.0
            }
            for (i in 0 until k) {
                for (j in 0 until k) {
                    var s = 0.0
                    for (r in 0 until k - 2) s += d2[r][i] * d2[r][j]
                    p[i][j] = s
                }
            }
        }

        val n = x.size
        val model = { vv: DoubleArray -> pchipEval(knots, vv, x) }

        fun cost(vv: DoubleArray): Double {
            val pred = model(vv)
            var s = 0.0
            for (i in 0 until n) {
                val r = w[i] * (pred[i] - y[i])
                s += r * r
            }
            var quad = 0.0
            for (i in 0 until k) {
                var row = 0.0
                for (j in 0 until k) row += p[i][j] * vv[j]
                quad += vv[i] * row
            }
            return 0.5 * s + 0.5 * smoothness * quad
        }

        var lam = 1e-6
        var c0 = cost(v)
        val eps = 1e-6

        for (iter in 0 until maxIter) {
            val pred = model(v)
            val r = DoubleArray(n) { w[it] * (pred[it] - y[it]) }

            // 数值雅可比（与电脑端一致：前向差分，步长 1e-6）
            val jac = Array(n) { DoubleArray(k) }
            val vp = v.copyOf()
            for (col in 0 until k) {
                vp[col] = v[col] + eps
                val shifted = model(vp)
                vp[col] = v[col]
                for (row in 0 until n) {
                    jac[row][col] = w[row] * (shifted[row] - pred[row]) / eps
                }
            }

            val jtj = Array(k) { DoubleArray(k) }
            for (i in 0 until k) {
                for (j in i until k) {
                    var s = 0.0
                    for (row in 0 until n) s += jac[row][i] * jac[row][j]
                    val value = s + smoothness * p[i][j]
                    jtj[i][j] = value
                    jtj[j][i] = value
                }
            }
            val grad = DoubleArray(k)
            for (i in 0 until k) {
                var s = 0.0
                for (row in 0 until n) s += jac[row][i] * r[row]
                var pv = 0.0
                for (j in 0 until k) pv += p[i][j] * v[j]
                grad[i] = s + smoothness * pv
            }

            val a = Array(k) { i ->
                DoubleArray(k) { j ->
                    val damping = if (i == j) jtj[i][i] + 1e-9 else 0.0
                    jtj[i][j] + lam * damping
                }
            }
            val dv = luSolve(a, DoubleArray(k) { -grad[it] })
            if (dv == null) break

            var accepted = false
            var alpha = 1.0
            for (attempt in 0 until 8) {
                val cand = DoubleArray(k) { (v[it] + alpha * dv[it]).coerceIn(0.0, 1.0) }
                val projected = pava(cand)
                val c1 = cost(projected)
                if (c1 < c0 - 1e-14) {
                    v = projected
                    c0 = c1
                    lam = max(lam * 0.5, 1e-9)
                    accepted = true
                    break
                }
                alpha *= 0.5
            }

            if (!accepted) {
                lam *= 8.0
                if (lam > 1e6) break
                continue
            }
            if (dv.maxOf { abs(alpha * it) } < tol) break
        }

        val projected = pava(v)
        for (i in projected.indices) projected[i] = projected[i].coerceIn(0.0, 1.0)
        return MonotoneSpline(knots, projected)
    }

    /** `np.interp(xq, x, y)`：区间外按端点常数延长。 */
    fun interp(xq: Double, x: DoubleArray, y: DoubleArray): Double {
        if (x.isEmpty()) return 0.0
        if (x.size == 1) return y[0]
        if (xq <= x[0]) return y[0]
        if (xq >= x[x.size - 1]) return y[y.size - 1]
        var idx = searchRight(x, xq) - 1
        idx = idx.coerceIn(0, x.size - 2)
        val h = x[idx + 1] - x[idx]
        if (h == 0.0) return y[idx]
        val t = (xq - x[idx]) / h
        return y[idx] + (y[idx + 1] - y[idx]) * t
    }

    /** 高斯消元 + 部分主元（对应 `np.linalg.solve`）。奇异时返回 null。 */
    private fun luSolve(matrix: Array<DoubleArray>, rhs: DoubleArray): DoubleArray? {
        val n = rhs.size
        val a = Array(n) { matrix[it].copyOf() }
        val b = rhs.copyOf()

        for (col in 0 until n) {
            var pivot = col
            for (row in col + 1 until n) {
                if (abs(a[row][col]) > abs(a[pivot][col])) pivot = row
            }
            if (abs(a[pivot][col]) < 1e-300) return null
            if (pivot != col) {
                val tmpRow = a[pivot]
                a[pivot] = a[col]
                a[col] = tmpRow
                val tmpB = b[pivot]
                b[pivot] = b[col]
                b[col] = tmpB
            }
            val diag = a[col][col]
            for (row in col + 1 until n) {
                val factor = a[row][col] / diag
                if (factor == 0.0) continue
                for (c in col until n) a[row][c] -= factor * a[col][c]
                b[row] -= factor * b[col]
            }
        }

        val out = DoubleArray(n)
        for (row in n - 1 downTo 0) {
            var s = b[row]
            for (c in row + 1 until n) s -= a[row][c] * out[c]
            out[row] = s / a[row][row]
        }
        return out
    }

    // -- 量化与断层 ----------------------------------------------------------

    /** 0..1 的曲线量化成 15 位码值。`Math.rint` 是四舍六入五成双，与 `np.rint` 一致。 */
    fun quantize(values: DoubleArray, vmax: Int = LUT_MAX): IntArray =
        IntArray(values.size) { Math.rint(values[it].coerceIn(0.0, 1.0) * vmax).toInt() }

    class Banding(
        val monotone: Boolean,
        val maxFlatRun: Int,
        val flatRuns: List<IntArray>,
        val artifactFlats: List<IntArray>,
        val max8bitDupes: Int,
        val blackPoint: Int,
        val whitePoint: Int,
        val minSlope: Double,
        val maxSlope: Double,
        val distinctCodes: Int,
    ) {
        /** 最长的「参考本来有变化、却被压平」的段。1 表示没有塌陷。 */
        val worstArtifactRun: Int get() = artifactFlats.maxOfOrNull { it[1] } ?: 1

        val hasArtifact: Boolean get() = artifactFlats.isNotEmpty()
    }

    /**
     * 量化后曲线的塌陷 / 断层检测。
     *
     * - flatRun：连续相同输出值的最长长度（1 = 每个点都不同，> 1 就是塌陷）
     * - 8bitDupes：落到同一个 8 位输出的点数（> 1 表示该段被压死）
     *
     * 传 reference（同一批 256 点上的参考曲线，0..1）时会额外判断每段塌陷
     * 「是不是参考本身就这么黑」。胶片曲线常在趾部把 0–5% 直接压到纯黑，那是还原，
     * 不是拟合缺陷。
     *
     * `minArtifactLevels` 是**材料性阈值**：一段塌陷只有在参考曲线本应上升超过这么多
     * 个 8 位码值时才算缺陷。实测（八张真机 JPEG）曲线肩部常出现 8–33 点的平段，
     * 但目标本身只涨 0.7–2.9 个码值——那是平滑先验在压肩，肉眼看不出来。
     * 低于阈值就当「没问题」，否则每张照片都会亮黄灯，提示就没人看了。
     */
    fun bandingAnalysis(
        table: IntArray,
        reference: DoubleArray? = null,
        minArtifactLevels: Double = 4.0,
    ): Banding {
        val n = table.size
        val diffs = IntArray(n - 1) { table[it + 1] - table[it] }

        val flatRuns = ArrayList<IntArray>()
        var runStart = 0
        for (i in 1 until n) {
            if (diffs[i - 1] != 0) {
                if (i - runStart > 1) flatRuns.add(intArrayOf(runStart, i - runStart))
                runStart = i
            }
        }
        if (n - runStart > 1) flatRuns.add(intArrayOf(runStart, n - runStart))

        val q8 = IntArray(n) { Math.rint(table[it].toDouble() / LUT_MAX * 255.0).toInt() }
        val dupRuns = ArrayList<IntArray>()
        var rs = 0
        for (i in 1 until n) {
            if (q8[i] != q8[i - 1]) {
                if (i - rs > 1) dupRuns.add(intArrayOf(rs, i - rs))
                rs = i
            }
        }
        if (n - rs > 1) dupRuns.add(intArrayOf(rs, n - rs))

        val slope = DoubleArray(n - 1) { diffs[it].toDouble() / LUT_MAX * (n - 1) }

        val artifactFlats = ArrayList<IntArray>()
        if (reference != null && reference.size == n) {
            val minLoss = minArtifactLevels / 255.0
            for (run in flatRuns) {
                val start = run[0]
                val length = run[1]
                val end = min(start + length, n - 1)
                if (end <= start) continue
                // 只有参考曲线在这一段里涨够了「看得见的量」，才说明是我们把它压平了。
                if (reference[end] - reference[start] >= minLoss) {
                    artifactFlats.add(run)
                }
            }
        }

        return Banding(
            monotone = diffs.all { it >= 0 },
            maxFlatRun = max(1, flatRuns.maxOfOrNull { it[1] } ?: 1),
            flatRuns = flatRuns.take(8),
            artifactFlats = artifactFlats.take(8),
            max8bitDupes = max(1, dupRuns.maxOfOrNull { it[1] } ?: 1),
            blackPoint = table[0],
            whitePoint = table[n - 1],
            minSlope = slope.minOrNull() ?: 0.0,
            maxSlope = slope.maxOrNull() ?: 0.0,
            distinctCodes = q8.distinct().size,
        )
    }

    /** 曲线特征（黑位/白位/18% 灰/三段斜率），与电脑端 tone_stats 同一套口径。 */
    class Tone(
        val black: Double,
        val white: Double,
        val mid18: Double,
        val contrast2575: Double,
        val slopeToe: Double,
        val slopeMid: Double,
        val slopeShoulder: Double,
    ) {
        companion object {
            fun of(values01: DoubleArray): Tone {
                val n = values01.size
                val grid = DoubleArray(n) { if (n == 1) 0.0 else it.toDouble() / (n - 1) }
                return Tone(
                    black = values01[0],
                    white = values01[n - 1],
                    mid18 = interp(0.18, grid, values01),
                    contrast2575 = (interp(0.75, grid, values01) - interp(0.25, grid, values01)) / 0.5,
                    slopeToe = (values01[min(8, n - 1)] - values01[0]) * 12.5,
                    slopeMid = (values01[min(136, n - 1)] - values01[min(120, n - 1)]) * 16.0,
                    slopeShoulder = (values01[n - 1] - values01[max(0, n - 9)]) * 12.5,
                )
            }
        }
    }
}
