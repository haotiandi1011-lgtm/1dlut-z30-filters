package com.z30tone.writer.core.fit

import com.z30tone.writer.core.Payload
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import kotlin.math.abs
import kotlin.math.roundToInt

/**
 * 单张照片 → 256 点影调曲线，对标电脑端 `z30fit/refimage.py`。
 *
 * **这里没有真值。** 一张成品图里混着原场景、光照、相机原有调色和后期，反推不出唯一的
 * 「输入→输出」映射。所以这条路做的是**影调分布迁移**：把「典型场景」的亮度分布映射到
 * 参考图的亮度分布上，得到一条单调曲线。
 *
 * 含义：如果你的场景和典型场景差不多，套上这条曲线，影调分布会接近参考图。
 * 它不等于「把那张照片还原出来」，所以这里**只报「影调贴合误差（灰阶）」**，
 * 单位是 8 位码值（曲线与目标的逐点差），口径与电脑端 `refimage.py` 的
 * `tone_match_mean / tone_match_max` 一致。
 *
 * 不在这里算 ΔE，也不给任何「还原度百分比」：单张图没有真值，那个数字是编的。
 * 参考图的色彩个性（色偏、饱和度、分色相）1D 曲线本来就表达不了。
 * 想量化真正的色彩误差，只能用成对图（同一场景的原图 + 目标图），那是电脑端的功能。
 */
object PhotoFit {

    /** 与电脑端 `np.linspace(0, 1, 513)` 一致：分位数采 513 个。 */
    private const val QUANTILES = 513

    /** 曲线节点数，与电脑端默认值一致。 */
    const val KNOTS = 33

    /** 端点权重：电脑端 refimage 用的是 10（不是 fit1d 的默认 40）。 */
    private const val ENDPOINT_WEIGHT = 10.0

    /** 黑位守卫：量化后黑位比目标抬高超过这么多（8 位码值）就压回去。 */
    private const val BLACK_LIFT_TOLERANCE_8BIT = 2.0

    /** 参考图贴黑/贴白像素占比超过这个值，就提示「这张图不宜当参考」。 */
    private const val CLIP_WARN = 0.06

    /** 贴合偏差超过这么多（8 位码值）就提示「这条曲线不够贴身眼」——与电脑端同一个阈值。 */
    private const val TONE_DEV_WARN = 2.0

    /** 参考图平均彩度超过这个值就提示「色彩个性会丢」——不是误差，是能力边界。 */
    private const val CHROMA_WARN = 20.0

    // -- 结果模型 ------------------------------------------------------------

    class Metrics(
        /**
         * **影调贴合误差（灰阶）**，单位是 8 位码值：量化后的曲线与分位数匹配目标在
         * 256 个灰阶点上的平均 / 最大绝对差。电脑端同名口径 `tone_match_mean / _max`。
         *
         * 它不是 ΔE，也不代表「还原度」——单张图没有真值。
         */
        val toneDevMean: Double,
        val toneDevMax: Double,
    )

    class Stats(
        val clipLow: Double,
        val clipHigh: Double,
        /** 参考图平均彩度（CIELAB √(a²+b²)）；NaN 表示没算（纯亮度输入）。 */
        val meanChroma: Double,
        val anchors: Map<String, Double>,
        val sourceP: Map<String, Double>,
    ) {
        val maxClip: Double get() = maxOf(clipLow, clipHigh)
    }

    class Advice(val level: String, val text: String) {
        val ok: Boolean get() = level != "no"
    }

    class Result(
        /** 256 点 15 位码值，直接进 614 字节载荷的 LUT 段。 */
        val table: IntArray,
        /** 量化后的曲线（0..1），画图用。 */
        val values: DoubleArray,
        /** 分位数匹配得到的目标曲线（0..1），画图对照用。 */
        val target: DoubleArray,
        val metrics: Metrics,
        val tone: ToneCurve.Tone,
        val banding: ToneCurve.Banding,
        val stats: Stats,
        val advice: Advice,
        /** 黑位守卫是否压过黑位（正常应为 false）。 */
        val blackGuarded: Boolean,
    ) {
        val writable: Boolean get() = banding.monotone

        val curve01: FloatArray get() = FloatArray(values.size) { values[it].toFloat() }

        val target01: FloatArray get() = FloatArray(target.size) { target[it].toFloat() }

        /** 像素占比口径的截断比例，写进报告用。 */
        val clipText: String
            get() = "贴黑 %.1f%% · 贴白 %.1f%%".format(Locale.US, stats.clipLow * 100, stats.clipHigh * 100)
    }

    // -- 主流程 --------------------------------------------------------------

    /**
     * 从**已排序**的亮度数组（0..1）拟合曲线。纯 JVM 实现，单测直接调它。
     *
     * @param meanChroma 参考图平均彩度，只用于风险提示（机身只有一条曲线，彩度越高，
     *   能还原的影调与还原不了的色彩之间落差越大）。不参与曲线计算。
     */
    fun fromLuma(refSorted: DoubleArray, meanChroma: Double = Double.NaN): Result {
        require(refSorted.isNotEmpty()) { "参考图没有有效像素" }
        val sorted = if (refSorted.size > 1 && refSorted[0] > refSorted[refSorted.size - 1]) {
            refSorted.sortedArray()
        } else {
            refSorted
        }

        val canon = CanonicalPool.values
        require(canon.size == QUANTILES) { "典型场景分位数应为 $QUANTILES 个，实际 ${canon.size}" }

        // 1) 分位数匹配：x = 典型场景的分位数，y = 参考图同分位的亮度
        val x = DoubleArray(QUANTILES)
        val y = DoubleArray(QUANTILES)
        var count = 0
        for (i in 0 until QUANTILES) {
            val u = i.toDouble() / (QUANTILES - 1)
            // 典型场景在 0 附近会堆叠出重复值，重复的 x 会让样条病态，先剔掉
            if (i > 0 && canon[i] - canon[i - 1] <= 1e-6) continue
            x[count] = canon[i]
            y[count] = Colorimetry.quantileSorted(sorted, u)
            count++
        }
        val xs = x.copyOf(count)
        val ys = y.copyOf(count)

        // 2) 单调样条（端点钉住 + 每步 PAVA 投影）
        val curve = ToneCurve.fitMonotoneCurve(
            xIn = xs,
            yIn = ys,
            wIn = DoubleArray(count) { 1.0 },
            nKnots = KNOTS,
            pinEndpoints = true,
            endpointWeight = ENDPOINT_WEIGHT,
        )

        val grid = DoubleArray(Payload.LUT_POINTS) { it.toDouble() / (Payload.LUT_POINTS - 1) }
        val target = DoubleArray(Payload.LUT_POINTS) { ToneCurve.interp(grid[it], xs, ys) }
        val fitted = curve.invoke(grid)
        for (i in fitted.indices) fitted[i] = fitted[i].coerceIn(0.0, 1.0)

        // 3) 量化 + 黑位守卫 + 单调兜底
        var table = ToneCurve.quantize(fitted)
        val targetBlack = (target[0].coerceIn(0.0, 1.0) * ToneCurve.LUT_MAX).roundToInt()
        val tolerance = (BLACK_LIFT_TOLERANCE_8BIT / 255.0 * ToneCurve.LUT_MAX).roundToInt()

        var guarded = false
        if (table[0] > targetBlack + tolerance) {
            // 只往下压：table[0] 变小不会破坏单调（它本来就不大于 table[1]）
            table[0] = targetBlack.coerceIn(0, table[1])
            guarded = true
        }
        table = enforceMonotone(table)

        val values = DoubleArray(Payload.LUT_POINTS) { table[it].toDouble() / ToneCurve.LUT_MAX }

        // 4) 误差：只报「影调贴合误差（灰阶）」，单位 8 位码值。
        //    量的是**真正写进机身的那条曲线**（量化 + 黑位守卫之后）与目标的逐点差，
        //    所以这里用 values 而不是 fitted —— 差的那一步量化也该算进去。
        val dev = DoubleArray(Payload.LUT_POINTS) { abs(values[it] - target[it]) * 255.0 }

        val metrics = Metrics(
            toneDevMean = dev.average(),
            toneDevMax = dev.max(),
        )

        val banding = ToneCurve.bandingAnalysis(table, target)

        val stats = Stats(
            clipLow = sorted.count { it <= 0.003 }.toDouble() / sorted.size,
            clipHigh = sorted.count { it >= 0.997 }.toDouble() / sorted.size,
            meanChroma = meanChroma,
            anchors = mapOf(
                "p1" to Colorimetry.quantileSorted(sorted, 0.01),
                "p5" to Colorimetry.quantileSorted(sorted, 0.05),
                "p25" to Colorimetry.quantileSorted(sorted, 0.25),
                "p50" to Colorimetry.quantileSorted(sorted, 0.50),
                "p75" to Colorimetry.quantileSorted(sorted, 0.75),
                "p95" to Colorimetry.quantileSorted(sorted, 0.95),
                "p99" to Colorimetry.quantileSorted(sorted, 0.99),
            ),
            sourceP = mapOf(
                "p5" to Colorimetry.quantileSorted(canon, 0.05),
                "p25" to Colorimetry.quantileSorted(canon, 0.25),
                "p50" to Colorimetry.quantileSorted(canon, 0.50),
                "p75" to Colorimetry.quantileSorted(canon, 0.75),
                "p95" to Colorimetry.quantileSorted(canon, 0.95),
            ),
        )

        return Result(
            table = table,
            values = values,
            target = target,
            metrics = metrics,
            tone = ToneCurve.Tone.of(values),
            banding = banding,
            stats = stats,
            advice = advise(metrics, banding, stats),
            blackGuarded = guarded,
        )
    }

    /**
     * 单调兜底：任何下降都压成「不高于前一个点」，首尾一并夹进 0..32767。
     *
     * 这一步是「机身绝不会收到非单调曲线」的最后一道保证 —— 前面 PAVA 投影已经
     * 从结构上保证单调，这里只兜量化/守卫之后的意外。
     */
    private fun enforceMonotone(table: IntArray): IntArray {
        val out = table.copyOf()
        out[0] = out[0].coerceIn(0, ToneCurve.LUT_MAX)
        for (i in 1 until out.size) {
            if (out[i] < out[i - 1]) out[i] = out[i - 1]
            if (out[i] > ToneCurve.LUT_MAX) out[i] = ToneCurve.LUT_MAX
            if (out[i] < 0) out[i] = 0
        }
        return out
    }

    /**
     * 写前判据，口径同电脑端：先看能不能写（单调），再看该不该写（塌陷 / 截断 / 贴合偏差 / 色彩落差）。
     */
    private fun advise(m: Metrics, band: ToneCurve.Banding, stats: Stats): Advice {
        if (!band.monotone) return Advice("no", "曲线非单调，不能写入")
        if (band.hasArtifact) {
            val run = band.worstArtifactRun
            return Advice(
                "caution",
                "参考图里有 $run 个连续灰阶被压成同一个输出码值，写进机身这一段可能出现色阶台阶。" +
                    "建议换一张暗部层次更完整的参考图。",
            )
        }
        if (stats.maxClip > CLIP_WARN) {
            return Advice(
                "caution",
                "参考图有 %.0f%% 的像素已经压到纯黑或纯白，曲线会把这一段压平；".format(stats.maxClip * 100) +
                    "建议换一张层次更完整的参考图。",
            )
        }
        if (m.toneDevMean > TONE_DEV_WARN) {
            return Advice(
                "caution",
                "曲线对参考影调的贴合偏差偏大（平均 %.1f 个码值），写进去可能不够准；".format(m.toneDevMean) +
                    "建议换一张影调更连贯的参考图。",
            )
        }
        if (stats.meanChroma > CHROMA_WARN) {
            return Advice(
                "caution",
                "参考图色彩很浓（平均彩度 %.0f）。机身只有一条对三通道同映射的曲线，".format(stats.meanChroma) +
                    "影调能贴近，色彩个性（色偏、饱和度、分色相）不会跟着进机身。",
            )
        }
        return Advice("ok", "影调贴合良好。单图模式只迁移影调，参考图的色彩个性不会跟着进机身")
    }

    // -- 与机身载荷对接 -------------------------------------------------------

    /**
     * 机身名称：ASCII、≤19 字符，按时间生成。
     *
     * 精确到秒（`PH1102-134012`，13 字符）：配方文件按载荷哈希区分，不靠名字，
     * 但列表里两条同名会让人分不清是哪张照片。秒级足够避免这种混淆。
     */
    fun suggestName(now: Date = Date()): String =
        "PH" + SimpleDateFormat("MMdd-HHmmss", Locale.US).format(now)

    /** 直接组 614 字节载荷，走原有 Payload（模板 + 名称 + 控制点 + LUT）。 */
    fun buildPayload(result: Result, name: String): ByteArray =
        Payload.buildFromLut(result.table, name)

    /**
     * 写配方包时用的元数据 JSON（电脑端读得懂同一套键）。
     *
     * 注意用的是 `tone_dev_*`，**故意不写 `mean` / `p95`**：那两个键在配方卡上会被当成
     * ΔE 显示，而单图模式没有真值。`mode` 标明这是影调迁移，不是色彩还原。
     */
    fun metaJson(result: Result, name: String, now: Date = Date()): String {
        val m = result.metrics
        val t = result.tone
        fun d(v: Double) = "%.4f".format(Locale.US, v)
        return buildString {
            append("{")
            append("\"name\":\"").append(name.replace("\"", "'")).append("\",")
            append("\"source\":\"照片拟合（影调分布迁移）\",")
            append("\"mode\":\"image\",")
            append("\"note\":\"单图模式：只迁移影调分布，不含参考图的色彩个性\",")
            append("\"metrics\":{")
            append("\"tone_dev_mean\":").append(d(m.toneDevMean)).append(",")
            append("\"tone_dev_max\":").append(d(m.toneDevMax)).append(",")
            append("\"clip_low\":").append(d(result.stats.clipLow)).append(",")
            append("\"clip_high\":").append(d(result.stats.clipHigh))
            append("},")
            append("\"tone\":{")
            append("\"black\":").append(d(t.black)).append(",")
            append("\"white\":").append(d(t.white)).append(",")
            append("\"mid_18\":").append(d(t.mid18))
            append("},")
            append("\"created\":\"").append(SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.US).format(now)).append("\",")
            append("\"tool\":\"Z30 影调 手机端\"")
            append("}")
        }
    }

    /** 量化后与目标曲线逐点之差（0..1），给界面画偏差条。 */
    fun deviationCurve(result: Result): DoubleArray =
        DoubleArray(result.values.size) { result.values[it] - result.target[it] }
}
