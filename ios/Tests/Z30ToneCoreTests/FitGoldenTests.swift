import XCTest
@testable import Z30ToneCore

/// **移植有没有跑偏的验收测试。**
///
/// 金标准由电脑端 `z30fit/refimage.py` 在完全相同的输入上算出来，与安卓端用的是同一份
/// `fit-golden.txt`。断言分两层：
///   1. 256 点 15 位码值逐点差 ≤ 2；
///   2. 曲线数值差 ≤ 1e-3，用来在出现偏差时一眼看出漂了多少。
///
/// 输入是纯整数公式生成的合成亮度，Kotlin / Python / Swift 必须逐位一致，所以能卡这么死。
final class FitGoldenTests: XCTestCase {

    /// 与 `gen_android_fit_data.py::synth_luma` 逐位一致的合成亮度（纯整数运算）。
    private func synthLuma(w: Int = 97, h: Int = 61) -> [Double] {
        var out = [Double](repeating: 0, count: w * h)
        for y in 0..<h {
            for x in 0..<w {
                let v = (x * 37 + y * 91 + (x * y) % 53) % 256
                let r = v
                let g = (v * 3) % 256
                let b = (v * 7 + 11) % 256
                out[y * w + x] = (Double(r) * 0.2126 + Double(g) * 0.7152 + Double(b) * 0.0722) / 255.0
            }
        }
        return out
    }

    private func reference(for name: String) -> [Double] {
        let base = synthLuma()
        let raw: [Double]
        switch name {
        case "normal":
            raw = base
        case "crushed":
            var c = base.map { pow(min(max(($0 - 0.35) * 2.2, 0), 1), 0.85) }
            for i in 0..<(c.count / 2) { c[i] = 0 }
            raw = c
        case "faded":
            raw = base.map { 0.30 + 0.45 * $0 }
        default:
            XCTFail("未知金标准用例 \(name)")
            return []
        }
        return raw.sorted()
    }

    func testGoldenCasesAreLoaded() {
        XCTAssertEqual(FitGoldenData.cases.count, 3)
        for golden in FitGoldenData.cases {
            XCTAssertEqual(golden.table.count, 256, "[\(golden.name)] table 不是 256 点")
            XCTAssertEqual(golden.fit.count, 256, "[\(golden.name)] fit 不是 256 点")
        }
    }

    func testCanonicalPoolShape() {
        let values = CanonicalPool.values
        XCTAssertEqual(values.count, 513)
        XCTAssertEqual(values.first ?? -1, 0.0, accuracy: 1e-12)
        XCTAssertEqual(values.last ?? -1, 1.0, accuracy: 1e-12)
        for i in 1..<values.count {
            XCTAssertGreaterThanOrEqual(values[i], values[i - 1], "第 \(i) 个分位点不是升序")
        }
    }

    func testCurveMatchesDesktopWithinTwoCodes() {
        for golden in FitGoldenData.cases {
            let result = PhotoFit.fromLuma(reference(for: golden.name))
            var worst = 0
            var worstAt = 0
            for i in 0..<256 {
                let d = abs(result.table[i] - golden.table[i])
                if d > worst {
                    worst = d
                    worstAt = i
                }
            }
            XCTAssertLessThanOrEqual(
                worst, 2,
                "[\(golden.name)] 第 \(worstAt) 点差 \(worst) 个码值"
                    + "（iOS \(result.table[worstAt]) / 桌面 \(golden.table[worstAt])），超过 2 说明移植跑偏了"
            )
        }
    }

    func testCurveValuesMatchDesktop() {
        for golden in FitGoldenData.cases {
            let result = PhotoFit.fromLuma(reference(for: golden.name))
            var worst = 0.0
            for i in 0..<256 {
                worst = max(worst, abs(result.values[i] - golden.fit[i]))
            }
            XCTAssertLessThanOrEqual(worst, 1e-3, "[\(golden.name)] 曲线数值最大偏差 \(worst) 超过 1e-3")
        }
    }

    func testFittedCurvesAreAlwaysWritable() {
        for golden in FitGoldenData.cases {
            let result = PhotoFit.fromLuma(reference(for: golden.name))
            XCTAssertTrue(result.writable, "[\(golden.name)] 曲线非单调，不能写进机身")
            XCTAssertLessThanOrEqual(result.table[0], result.table[1], "[\(golden.name)] 黑位被抬高了")
            XCTAssertTrue(result.advice.ok, "[\(golden.name)] 写前判据给了否定：\(result.advice.text)")
            XCTAssertLessThanOrEqual(
                result.metrics.toneDevMean, 1.0,
                "[\(golden.name)] 影调贴合误差 \(result.metrics.toneDevMean) 个码值，偏大"
            )
        }
    }

    /// 机身 LUT 的硬要求：0..32767、非递减、首尾不许越界。
    func testTableStaysInsideCameraLimits() {
        for golden in FitGoldenData.cases {
            let table = PhotoFit.fromLuma(reference(for: golden.name)).table
            XCTAssertEqual(table.count, 256)
            for (i, value) in table.enumerated() {
                XCTAssertTrue((0...32767).contains(value), "[\(golden.name)] 第 \(i) 点码值越界：\(value)")
                if i > 0 {
                    XCTAssertGreaterThanOrEqual(value, table[i - 1], "[\(golden.name)] 第 \(i) 点下降")
                }
            }
        }
    }

    func testHeavyBlackClipDoesNotLiftBlackPoint() {
        // 全黑的参考图：曲线应当整体压到纯黑，绝不出现「抬黑」
        let black = [Double](repeating: 0, count: 4096)
        let result = PhotoFit.fromLuma(black)
        XCTAssertEqual(result.table[0], 0)
        XCTAssertEqual(result.table[255], 0)
        XCTAssertTrue(result.writable)
        XCTAssertGreaterThan(result.stats.clipLow, 0.9)
        XCTAssertEqual(result.advice.level, "caution", "截断比例这么高，必须给出「换一张图」的提示")
    }

    func testPayloadIsValidForCamera() throws {
        let result = PhotoFit.fromLuma(reference(for: "normal"))
        let payload = try PhotoFit.buildPayload(result, name: "PH1101-1200")
        XCTAssertEqual(payload.count, 614)

        let parsed = try Payload.parse(payload)
        XCTAssertEqual(parsed.name, "PH1101-1200")
        XCTAssertEqual(parsed.family, 0x05)
        XCTAssertTrue(parsed.monotonic)
        XCTAssertEqual(parsed.first, result.table[0])
        XCTAssertEqual(parsed.last, result.table[255])

        let bytes = [UInt8](payload)
        XCTAssertEqual(bytes[0x00], 0x02)
        XCTAssertEqual(bytes[0x24], 0x49)  // 'I'
        XCTAssertEqual(bytes[0x25], 0x30)  // '0'
    }

    func testImageFitNeverReportsDeltaE() throws {
        let result = PhotoFit.fromLuma(reference(for: "normal"))
        let payload = try PhotoFit.buildPayload(result, name: "PH1102-101010")
        let packed = try Recipe.build(payload: payload, metaJSON: PhotoFit.metaJSON(result, name: "PH1102-101010"))
        let meta = try Recipe.parse(packed).meta

        XCTAssertNil(meta.deltaEAvg, "单图拟合不许带 ΔE（metrics.mean）")
        XCTAssertNil(meta.deltaEP95, "单图拟合不许带 ΔE P95（metrics.p95）")
        XCTAssertNotNil(meta.metrics["tone_dev_mean"], "元数据里要留着贴合误差")
    }

    func testSuggestedNameFitsTheCamera() {
        let name = PhotoFit.suggestName()
        XCTAssertTrue(name.unicodeScalars.allSatisfy { (0x20...0x7E).contains(Int($0.value)) }, "名称必须是 ASCII：\(name)")
        XCTAssertLessThanOrEqual(name.count, 19, "名称超过 19 字符：\(name)")
        XCTAssertEqual(name, Payload.sanitizeName(name))
    }
}
