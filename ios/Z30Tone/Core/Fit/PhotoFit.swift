import Foundation

/// 单张照片 → 256 点影调曲线，对标电脑端 `z30fit/refimage.py`。
///
/// **这里没有真值。** 一张成品图里混着原场景、光照、相机原有调色和后期，反推不出唯一的
/// 「输入→输出」映射。所以这条路做的是**影调分布迁移**：把「典型场景」的亮度分布映射到
/// 参考图的亮度分布上，得到一条单调曲线。
///
/// 含义：如果你的场景和典型场景差不多，套上这条曲线，影调分布会接近参考图。
/// 它不等于「把那张照片还原出来」，所以这里**只报「影调贴合误差（灰阶）」**，
/// 单位是 8 位码值（曲线与目标的逐点差），口径与电脑端 `refimage.py` 的
/// `tone_match_mean / tone_match_max` 一致。
///
/// 不在这里算 ΔE，也不给任何「还原度百分比」：单张图没有真值，那个数字是编的。
enum PhotoFit {

    /// 与电脑端 `np.linspace(0, 1, 513)` 一致：分位数采 513 个。
    static let quantiles = 513

    /// 曲线节点数，与电脑端默认值一致。
    static let knots = 33

    /// 端点权重：电脑端 refimage 用的是 10（不是 fit1d 的默认 40）。
    private static let endpointWeight = 10.0

    /// 黑位守卫：量化后黑位比目标抬高超过这么多（8 位码值）就压回去。
    private static let blackLiftTolerance8Bit = 2.0

    /// 参考图贴黑/贴白像素占比超过这个值，就提示「这张图不宜当参考」。
    private static let clipWarn = 0.06

    /// 贴合偏差超过这么多（8 位码值）就提示「这条曲线不够贴身眼」。
    private static let toneDevWarn = 2.0

    /// 参考图平均彩度超过这个值就提示「色彩个性会丢」——不是误差，是能力边界。
    private static let chromaWarn = 20.0

    // MARK: - 结果模型

    struct Metrics {
        /// **影调贴合误差（灰阶）**，单位是 8 位码值：量化后的曲线与分位数匹配目标在
        /// 256 个灰阶点上的平均 / 最大绝对差。电脑端同名口径 `tone_match_mean / _max`。
        ///
        /// 它不是 ΔE，也不代表「还原度」——单张图没有真值。
        let toneDevMean: Double
        let toneDevMax: Double
    }

    struct Stats {
        let clipLow: Double
        let clipHigh: Double
        /// 参考图平均彩度（CIELAB √(a²+b²)）；NaN 表示没算（纯亮度输入）。
        let meanChroma: Double
        let anchors: [String: Double]
        let sourceP: [String: Double]

        var maxClip: Double { max(clipLow, clipHigh) }
    }

    struct Advice {
        /// "ok" / "caution" / "no"
        let level: String
        let text: String
        var ok: Bool { level != "no" }
    }

    struct Result {
        /// 256 点 15 位码值，直接进 614 字节载荷的 LUT 段。
        let table: [Int]
        /// 量化后的曲线（0..1），画图用。
        let values: [Double]
        /// 分位数匹配得到的目标曲线（0..1），画图对照用。
        let target: [Double]
        let metrics: Metrics
        let tone: ToneCurve.Tone
        let banding: ToneCurve.Banding
        let stats: Stats
        let advice: Advice
        /// 黑位守卫是否压过黑位（正常应为 false）。
        let blackGuarded: Bool

        var writable: Bool { banding.monotone }

        /// 像素占比口径的截断比例，写进报告用。
        var clipText: String {
            String(format: "贴黑 %.1f%% · 贴白 %.1f%%", stats.clipLow * 100, stats.clipHigh * 100)
        }
    }

    // MARK: - 主流程

    /// 从**已排序**的亮度数组（0..1）拟合曲线。纯 Foundation 实现，单测直接调它。
    ///
    /// - Parameter meanChroma: 参考图平均彩度，只用于风险提示。不参与曲线计算。
    static func fromLuma(_ refSorted: [Double], meanChroma: Double = .nan) -> Result {
        precondition(!refSorted.isEmpty, "参考图没有有效像素")

        let sorted: [Double]
        if refSorted.count > 1 && refSorted[0] > refSorted[refSorted.count - 1] {
            sorted = refSorted.sorted()
        } else {
            sorted = refSorted
        }

        let canon = CanonicalPool.values
        precondition(canon.count == quantiles, "典型场景分位数应为 \(quantiles) 个，实际 \(canon.count)")

        // 1) 分位数匹配：x = 典型场景的分位数，y = 参考图同分位的亮度
        var xs: [Double] = []
        var ys: [Double] = []
        xs.reserveCapacity(quantiles)
        ys.reserveCapacity(quantiles)
        for i in 0..<quantiles {
            let u = Double(i) / Double(quantiles - 1)
            // 典型场景在 0 附近会堆叠出重复值，重复的 x 会让样条病态，先剔掉
            if i > 0 && canon[i] - canon[i - 1] <= 1e-6 { continue }
            xs.append(canon[i])
            ys.append(Colorimetry.quantileSorted(sorted, u))
        }
        let count = xs.count

        // 2) 单调样条（端点钉住 + 每步 PAVA 投影）
        let curve = ToneCurve.fitMonotoneCurve(
            xIn: xs,
            yIn: ys,
            wIn: [Double](repeating: 1, count: count),
            nKnots: knots,
            pinEndpoints: true,
            endpointWeight: endpointWeight
        )

        let grid = (0..<Payload.lutPoints).map { Double($0) / Double(Payload.lutPoints - 1) }
        let target = grid.map { ToneCurve.interp($0, xs, ys) }
        var fitted = curve(grid)
        for i in fitted.indices { fitted[i] = min(max(fitted[i], 0), 1) }

        // 3) 量化 + 黑位守卫 + 单调兜底
        var table = ToneCurve.quantize(fitted)
        let targetBlack = Payload.javaRound(min(max(target[0], 0), 1) * Double(ToneCurve.lutMax))
        let tolerance = Payload.javaRound(blackLiftTolerance8Bit / 255.0 * Double(ToneCurve.lutMax))

        var guarded = false
        if table[0] > targetBlack + tolerance {
            // 只往下压：table[0] 变小不会破坏单调（它本来就不大于 table[1]）
            table[0] = min(max(targetBlack, 0), table[1])
            guarded = true
        }
        table = enforceMonotone(table)

        let values = table.map { Double($0) / Double(ToneCurve.lutMax) }

        // 4) 误差：只报「影调贴合误差（灰阶）」，单位 8 位码值。
        //    量的是**真正写进机身的那条曲线**（量化 + 黑位守卫之后）与目标的逐点差。
        let dev = (0..<Payload.lutPoints).map { abs(values[$0] - target[$0]) * 255.0 }
        let metrics = Metrics(
            toneDevMean: dev.reduce(0.0, +) / Double(dev.count),
            toneDevMax: dev.max() ?? 0
        )

        let banding = ToneCurve.bandingAnalysis(table, reference: target)

        let clipLowCount = sorted.filter { $0 <= 0.003 }.count
        let clipHighCount = sorted.filter { $0 >= 0.997 }.count
        let stats = Stats(
            clipLow: Double(clipLowCount) / Double(sorted.count),
            clipHigh: Double(clipHighCount) / Double(sorted.count),
            meanChroma: meanChroma,
            anchors: [
                "p1": Colorimetry.quantileSorted(sorted, 0.01),
                "p5": Colorimetry.quantileSorted(sorted, 0.05),
                "p25": Colorimetry.quantileSorted(sorted, 0.25),
                "p50": Colorimetry.quantileSorted(sorted, 0.50),
                "p75": Colorimetry.quantileSorted(sorted, 0.75),
                "p95": Colorimetry.quantileSorted(sorted, 0.95),
                "p99": Colorimetry.quantileSorted(sorted, 0.99),
            ],
            sourceP: [
                "p5": Colorimetry.quantileSorted(canon, 0.05),
                "p25": Colorimetry.quantileSorted(canon, 0.25),
                "p50": Colorimetry.quantileSorted(canon, 0.50),
                "p75": Colorimetry.quantileSorted(canon, 0.75),
                "p95": Colorimetry.quantileSorted(canon, 0.95),
            ]
        )

        return Result(
            table: table,
            values: values,
            target: target,
            metrics: metrics,
            tone: ToneCurve.Tone.of(values),
            banding: banding,
            stats: stats,
            advice: advise(metrics, banding, stats),
            blackGuarded: guarded
        )
    }

    /// 单调兜底：任何下降都压成「不高于前一个点」，首尾一并夹进 0..32767。
    ///
    /// 这一步是「机身绝不会收到非单调曲线」的最后一道保证 —— 前面 PAVA 投影已经
    /// 从结构上保证单调，这里只兜量化/守卫之后的意外。
    private static func enforceMonotone(_ table: [Int]) -> [Int] {
        var out = table
        let maxValue = ToneCurve.lutMax
        out[0] = min(max(out[0], 0), maxValue)
        for i in 1..<out.count {
            if out[i] < out[i - 1] { out[i] = out[i - 1] }
            if out[i] > maxValue { out[i] = maxValue }
            if out[i] < 0 { out[i] = 0 }
        }
        return out
    }

    /// 写前判据，口径同电脑端：先看能不能写（单调），再看该不该写。
    private static func advise(
        _ m: Metrics,
        _ band: ToneCurve.Banding,
        _ stats: Stats
    ) -> Advice {
        if !band.monotone { return Advice(level: "no", text: "曲线非单调，不能写入") }
        if band.hasArtifact {
            let run = band.worstArtifactRun
            return Advice(
                level: "caution",
                text: "参考图里有 \(run) 个连续灰阶被压成同一个输出码值，写进机身这一段可能出现色阶台阶。"
                    + "建议换一张暗部层次更完整的参考图。"
            )
        }
        if stats.maxClip > clipWarn {
            return Advice(
                level: "caution",
                text: String(format: "参考图有 %.0f%% 的像素已经压到纯黑或纯白，曲线会把这一段压平；", stats.maxClip * 100)
                    + "建议换一张层次更完整的参考图。"
            )
        }
        if m.toneDevMean > toneDevWarn {
            return Advice(
                level: "caution",
                text: String(format: "曲线对参考影调的贴合偏差偏大（平均 %.1f 个码值），写进去可能不够准；", m.toneDevMean)
                    + "建议换一张影调更连贯的参考图。"
            )
        }
        if stats.meanChroma > chromaWarn {
            return Advice(
                level: "caution",
                text: String(format: "参考图色彩很浓（平均彩度 %.0f）。机身只有一条对三通道同映射的曲线，", stats.meanChroma)
                    + "影调能贴近，色彩个性（色偏、饱和度、分色相）不会跟着进机身。"
            )
        }
        return Advice(
            level: "ok",
            text: "影调贴合良好。单图模式只迁移影调，参考图的色彩个性不会跟着进机身"
        )
    }

    // MARK: - 与机身载荷对接

    /// 机身名称：ASCII、≤19 字符，按时间生成（`PH1102-134012`）。
    static func suggestName(_ now: Date = Date()) -> String {
        "PH" + formatted(now, "MMdd-HHmmss")
    }

    /// 直接组 614 字节载荷，走原有 Payload（模板 + 名称 + 控制点 + LUT）。
    static func buildPayload(_ result: Result, name: String) throws -> Data {
        try Payload.buildFromLut(result.table, name: name)
    }

    /// 写配方包时用的元数据 JSON（电脑端读得懂同一套键）。
    ///
    /// 注意用的是 `tone_dev_*`，**故意不写 `mean` / `p95`**：那两个键在配方卡上会被当成
    /// ΔE 显示，而单图模式没有真值。`mode` 标明这是影调迁移，不是色彩还原。
    static func metaJSON(_ result: Result, name: String, now: Date = Date()) -> String {
        let m = result.metrics
        let t = result.tone
        func d(_ v: Double) -> String { String(format: "%.4f", v) }

        var out = "{"
        out += "\"name\":\"" + name.replacingOccurrences(of: "\"", with: "'") + "\","
        out += "\"source\":\"照片拟合（影调分布迁移）\","
        out += "\"mode\":\"image\","
        out += "\"note\":\"单图模式：只迁移影调分布，不含参考图的色彩个性\","
        out += "\"metrics\":{"
        out += "\"tone_dev_mean\":" + d(m.toneDevMean) + ","
        out += "\"tone_dev_max\":" + d(m.toneDevMax) + ","
        out += "\"clip_low\":" + d(result.stats.clipLow) + ","
        out += "\"clip_high\":" + d(result.stats.clipHigh)
        out += "},"
        out += "\"tone\":{"
        out += "\"black\":" + d(t.black) + ","
        out += "\"white\":" + d(t.white) + ","
        out += "\"mid_18\":" + d(t.mid18)
        out += "},"
        out += "\"created\":\"" + formatted(now, "yyyy-MM-dd HH:mm:ss") + "\","
        out += "\"tool\":\"Z30 影调 iOS 端\""
        out += "}"
        return out
    }

    /// 量化后与目标曲线逐点之差（0..1），给界面画偏差条。
    static func deviationCurve(_ result: Result) -> [Double] {
        (0..<result.values.count).map { result.values[$0] - result.target[$0] }
    }

    private static func formatted(_ date: Date, _ format: String) -> String {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.dateFormat = format
        return f.string(from: date)
    }
}
