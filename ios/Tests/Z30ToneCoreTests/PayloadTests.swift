import XCTest
@testable import Z30ToneCore

/// 614 字节载荷的结构守卫。任何一条挂了，写进机身的都是废数据。
final class PayloadTests: XCTestCase {

    func testFallbackTemplateLayout() {
        let t = [UInt8](Payload.fallbackTemplate())
        XCTAssertEqual(t.count, 614)
        XCTAssertEqual(t[0x000], 0x02)
        XCTAssertEqual(t[0x001], 0x00)
        XCTAssertEqual(t[0x023], 0x01)
        XCTAssertEqual(t[0x024], 0x49)  // 'I'
        XCTAssertEqual(t[0x025], 0x30)  // '0'
        XCTAssertEqual(t[0x027], 0xFF)
        XCTAssertEqual(t[0x029], 0xFF)
        XCTAssertEqual(t[0x02A], 0x01)
        XCTAssertEqual(t[0x02B], 0x0F)
        XCTAssertEqual(t[0x02C], 0x05)  // family
    }

    /// LUT 是 **大端**。这条挂了说明有人照着旧文档改成了小端。
    func testLutIsBigEndian() throws {
        let lut = (0..<256).map { $0 * 100 }
        let payload = try Payload.buildFromLut(lut, name: "ENDIAN")
        let b = [UInt8](payload)

        XCTAssertEqual(b[0x066], 0x00)   // 0
        XCTAssertEqual(b[0x067], 0x00)
        XCTAssertEqual(b[0x068], 0x00)   // 100 = 0x0064
        XCTAssertEqual(b[0x069], 0x64)
        XCTAssertEqual(b[0x06A], 0x00)   // 200 = 0x00C8
        XCTAssertEqual(b[0x06B], 0xC8)
    }

    func testRoundTripKeepsLutAndName() throws {
        let lut = (0..<256).map { Int(Double($0) / 255.0 * 32767) }
        let payload = try Payload.buildFromLut(lut, name: "ROUND-TRIP")
        let parsed = try Payload.parse(payload)

        XCTAssertEqual(parsed.name, "ROUND-TRIP")
        XCTAssertEqual(parsed.lut.count, 256)
        XCTAssertEqual(parsed.lut, lut)
        XCTAssertTrue(parsed.monotonic)
    }

    func testNameIsAsciiAndCapped() throws {
        XCTAssertEqual(Payload.sanitizeName("中文名字"), "????")
        XCTAssertEqual(Payload.sanitizeName(""), "Z30FIT")
        XCTAssertEqual(Payload.sanitizeName("   "), "Z30FIT")

        let long = String(repeating: "A", count: 30)
        XCTAssertEqual(Payload.sanitizeName(long).count, 19)

        let payload = try Payload.buildFromLut([Int](repeating: 0, count: 256), name: "中文AB")
        let parsed = try Payload.parse(payload)
        XCTAssertEqual(parsed.name, "??AB")
    }

    /// 参数编码：快速锐化 1/档、其余 0.25/档，负数补码。
    func testParamEncoding() {
        XCTAssertEqual(Payload.encodeParam(-2, .quickSharp), 0xFE)
        XCTAssertEqual(Payload.encodeParam(9, .sharp), 0x24)      // 9 / 0.25 = 36
        XCTAssertEqual(Payload.encodeParam(-5, .clarity), 0xEC)   // -20 → 236
        XCTAssertEqual(Payload.encodeParam(3, .saturation), 0x0C)

        XCTAssertEqual(Payload.decodeParam(0xFE, .quickSharp), -2, accuracy: 1e-9)
        XCTAssertEqual(Payload.decodeParam(0x24, .sharp), 9, accuracy: 1e-9)
        XCTAssertEqual(Payload.decodeParam(0xEC, .clarity), -5, accuracy: 1e-9)
    }

    func testSetParamsOnlyTouchesParamArea() throws {
        let lut = (0..<256).map { $0 * 128 }
        let base = try Payload.buildFromLut(lut, name: "PARAM")
        let changed = Payload.setParams(base, [.sharp: 3, .saturation: -1])

        let diff = Payload.diff(base, changed)
        XCTAssertEqual(diff.offsets.sorted(), [CameraParam.sharp.offset, CameraParam.saturation.offset].sorted())

        let parsed = try Payload.parse(changed)
        XCTAssertEqual(parsed.param(.sharp), 3, accuracy: 1e-9)
        XCTAssertEqual(parsed.param(.saturation), -1, accuracy: 1e-9)
        XCTAssertEqual(parsed.lut, lut)
    }

    func testControlPointsDoNotAffectImage() throws {
        let lut = (0..<256).map { Int(Double($0) / 255.0 * 32767) }
        let payload = try Payload.buildFromLut(lut, name: "CP")
        let parsed = try Payload.parse(payload)

        XCTAssertEqual(parsed.controlPoints.count, 4)
        for (i, point) in parsed.controlPoints.enumerated() {
            let expectedX = Payload.javaRound(Double(i + 1) / 5.0 * 255)
            XCTAssertEqual(point.x, expectedX)
            XCTAssertTrue((0...255).contains(point.y))
        }
    }

    func testDiffReportsOffsets() {
        let a = Data([1, 2, 3, 4])
        let b = Data([1, 9, 3, 4])
        let diff = Payload.diff(a, b)
        XCTAssertEqual(diff.offsets, [1])
        XCTAssertFalse(diff.identical)
        XCTAssertTrue(Payload.diff(a, a).identical)
    }

    func testParseRejectsShortPayload() {
        XCTAssertThrowsError(try Payload.parse(Data(repeating: 0, count: 100)))
    }
}
