import XCTest
@testable import Z30ToneCore

/// `.z30` 配方包的格式守卫。
final class RecipeTests: XCTestCase {

    private func makePayload(name: String = "RECIPE") throws -> Data {
        let lut = (0..<256).map { Int(Double($0) / 255.0 * 32767) }
        return try Payload.buildFromLut(lut, name: name)
    }

    func testBuildWritesMagicAndLittleEndianLength() throws {
        let payload = try makePayload()
        let json = "{\"name\":\"测试\"}"
        let packed = try Recipe.build(payload: payload, metaJSON: json)
        let bytes = [UInt8](packed)

        XCTAssertEqual(Array(bytes[0..<8]), Recipe.magic)
        XCTAssertEqual(packed.count, Recipe.header + json.utf8.count + Payload.size)

        let metaLength = Recipe.readIntLE(bytes, 8)
        XCTAssertEqual(metaLength, json.utf8.count)
        // 小端：长度 20 字节时首字节是 0x14
        if metaLength < 256 {
            XCTAssertEqual(bytes[8], UInt8(metaLength))
            XCTAssertEqual(bytes[9], 0)
        }
    }

    func testRoundTrip() throws {
        let payload = try makePayload(name: "ROUND")
        let json = """
        {"name":"胶片 01 · 提亮","source":"内置","metrics":{"tone_dev_mean":0.4321},"created":"2026-11-16 20:00:00","tool":"Z30 影调 iOS 端"}
        """
        let packed = try Recipe.build(payload: payload, metaJSON: json)
        XCTAssertTrue(Recipe.matches(packed))

        let pkg = try Recipe.parse(packed)
        XCTAssertEqual(pkg.payload, payload)
        XCTAssertEqual(pkg.meta.name, "胶片 01 · 提亮")
        XCTAssertEqual(pkg.meta.source, "内置")
        XCTAssertEqual(pkg.meta.metrics["tone_dev_mean"] ?? 0, 0.4321, accuracy: 1e-9)
        XCTAssertEqual(pkg.displayName, "胶片 01 · 提亮")
        XCTAssertEqual(pkg.parsed?.name, "ROUND")
    }

    func testMatchesRejectsGarbage() {
        XCTAssertFalse(Recipe.matches(Data()))
        XCTAssertFalse(Recipe.matches(Data(repeating: 0, count: 1000)))
        XCTAssertFalse(Recipe.matches(Data(repeating: 0x5A, count: 700)))
    }

    func testParseRejectsBadMagic() {
        let junk = Data(repeating: 0x41, count: Recipe.header + Payload.size)
        XCTAssertThrowsError(try Recipe.parse(junk))
    }

    func testBadMetaLengthIsRejected() throws {
        let payload = try makePayload()
        var bytes = [UInt8](try Recipe.build(payload: payload, metaJSON: "{}"))
        // 把元数据长度改成一个越界值
        Recipe.writeIntLE(&bytes, 8, 10_000)
        XCTAssertThrowsError(try Recipe.parse(Data(bytes)))
    }

    /// 元数据只是备注：JSON 坏了也要能读载荷，**绝不能**因此拒绝一份配方。
    func testBrokenJSONStillYieldsPayload() throws {
        let payload = try makePayload()
        let packed = try Recipe.build(payload: payload, metaJSON: "{not json at all")
        let pkg = try Recipe.parse(packed)

        XCTAssertEqual(pkg.payload, payload)
        XCTAssertNil(pkg.meta.name)
        XCTAssertTrue(pkg.meta.metrics.isEmpty)
        // 回退到载荷里的机身名
        XCTAssertEqual(pkg.displayName, "RECIPE")
    }

    /// 单图拟合的元数据不许带会被读成 ΔE 的键。
    func testImageMetaHasNoDeltaE() throws {
        let result = PhotoFit.fromLuma((0..<1000).map { Double($0) / 1000.0 })
        let json = PhotoFit.metaJSON(result, name: "PH-test")
        let payload = try PhotoFit.buildPayload(result, name: "PH-test")
        let meta = try Recipe.parse(try Recipe.build(payload: payload, metaJSON: json)).meta

        XCTAssertNil(meta.deltaEAvg)
        XCTAssertNil(meta.deltaEP95)
        XCTAssertNotNil(meta.toneDevMean)
        XCTAssertNotNil(meta.toneDevMax)
    }
}
