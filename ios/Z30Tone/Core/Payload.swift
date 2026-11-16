import Foundation

/// 614 字节 PTP 载荷的解析、构建与对比。
///
/// 结构（真机回读验证，与 `android/.../core/Payload.kt`、`z30fit/payload.py` 完全一致）：
///
///     +0x000   2   版本 02 00
///     +0x002  20   ASCII 名称（NUL 补齐，实际可用 19 字符）
///     +0x018   7   参数区（快速锐化 / 锐化 / 中等锐化 / 清晰度 / 对比度 / 亮度 / 饱和度）
///     +0x02C   1   族标记（实测 0x05 可用）
///     +0x02F   8   4 组 (x, y) 控制点，只用于机身 LCD 画曲线
///     +0x066 512   256 × u16 **大端** LUT，值域 0..32767  ← 成像真值
///
/// 注意 LUT 是大端，别照某些旧文档写成小端。
enum Payload {

    static let size = 614
    static let lutOffset = 0x066
    static let lutPoints = 256
    static let lutBytes = 512
    static let lutMax = 32767

    static let nameOffset = 0x002
    static let nameLength = 20
    static let nameMax = 19

    static let familyOffset = 0x02C
    static let controlPointsOffset = 0x02F
    static let controlPointsLength = 8
}

enum PayloadError: LocalizedError {
    case tooShort(Int)
    case badTemplateSize(Int)
    case badLutSize(Int)

    var errorDescription: String? {
        switch self {
        case .tooShort(let n):
            return "载荷至少 \(Payload.size) 字节，实际 \(n)"
        case .badTemplateSize(let n):
            return "模板必须是 \(Payload.size) 字节，实际 \(n)"
        case .badLutSize(let n):
            return "LUT 必须是 \(Payload.lutPoints) 点，实际 \(n)"
        }
    }
}

/// 参数区里的 7 个参数，顺序与机身菜单完全一致。
enum CameraParam: String, CaseIterable, Identifiable {
    case quickSharp
    case sharp
    case midSharp
    case clarity
    case contrast
    case brightness
    case saturation

    var id: String { rawValue }

    var offset: Int {
        switch self {
        case .quickSharp: return 0x018
        case .sharp: return 0x019
        case .midSharp: return 0x01A
        case .clarity: return 0x01B
        case .contrast: return 0x01C
        case .brightness: return 0x01D
        case .saturation: return 0x01E
        }
    }

    /// 每档的步长。真机回读：只有快速锐化是 1/档，其余 0.25/档。
    var step: Double {
        self == .quickSharp ? 1.0 : 0.25
    }

    var label: String {
        switch self {
        case .quickSharp: return "快速锐化"
        case .sharp: return "锐化"
        case .midSharp: return "中等锐化"
        case .clarity: return "清晰度"
        case .contrast: return "对比度"
        case .brightness: return "亮度"
        case .saturation: return "饱和度"
        }
    }

    /// 有自定义曲线时，机身会禁用对比度与亮度，写了也不生效。
    var disabledWithCurve: Bool {
        self == .contrast || self == .brightness
    }

    /// 界面滑块允许的范围（与桌面端一致）。
    var range: ClosedRange<Double> {
        switch self {
        case .quickSharp: return -3...9
        case .sharp: return -3...9
        case .midSharp: return -5...5
        case .clarity: return -5...5
        case .contrast: return -3...3
        case .brightness: return -3...3
        case .saturation: return -3...3
        }
    }
}

/// 解析后的载荷。
struct ParsedPayload {
    let raw: Data
    let name: String
    let family: Int
    let controlPoints: [(x: Int, y: Int)]
    let lut: [Int]
    let params: [CameraParam: Double]

    var size: Int { raw.count }
    var first: Int { lut.first ?? 0 }
    var last: Int { lut.last ?? 0 }
    var peak: Int { lut.max() ?? 0 }

    var monotonic: Bool {
        guard lut.count > 1 else { return true }
        for i in 1..<lut.count where lut[i] < lut[i - 1] { return false }
        return true
    }

    func param(_ key: CameraParam) -> Double { params[key] ?? 0 }

    /// 归一化到 0..1，画图用。
    var curve01: [Double] { lut.map { Double($0) / Double(Payload.lutMax) } }
}

/// 逐字节比对结果。
struct PayloadDiff {
    let offsets: [Int]
    let sizeA: Int
    let sizeB: Int

    var lengthMismatch: Bool { sizeA != sizeB }
    var identical: Bool { offsets.isEmpty && !lengthMismatch }

    var summary: String {
        if identical { return "读回一致：\(sizeA) 字节逐字节相同" }
        if lengthMismatch { return "长度不一致：写入 \(sizeA) 字节，读回 \(sizeB) 字节" }
        let head = offsets.prefix(8)
            .map { String(format: "0x%02X", $0) }
            .joined(separator: " ")
        return "有 \(offsets.count) 字节不一致：\(head)"
    }
}

extension Payload {

    /// 复刻 Java 的 `Math.round`：`floor(x + 0.5)`，负数同规则。
    /// Swift 的 `rounded()` 是银行家舍入，直接用会和安卓端差一个码值。
    static func javaRound(_ x: Double) -> Int {
        Int((x + 0.5).rounded(.down))
    }

    static func parse(_ raw: Data) throws -> ParsedPayload {
        guard raw.count >= size else { throw PayloadError.tooShort(raw.count) }
        let b = [UInt8](raw.prefix(size))

        var nameBytes: [UInt8] = []
        for i in 0..<nameLength {
            let code = b[nameOffset + i]
            if code == 0 { break }
            nameBytes.append(code)
        }
        let name = String(nameBytes.map { byte -> Character in
            (0x20...0x7E).contains(Int(byte)) ? Character(UnicodeScalar(byte)) : Character("?")
        })

        var lut = [Int](repeating: 0, count: lutPoints)
        for i in 0..<lutPoints {
            let o = lutOffset + i * 2
            lut[i] = (Int(b[o]) << 8) | Int(b[o + 1])   // 大端
        }

        var points: [(x: Int, y: Int)] = []
        for i in 0..<(controlPointsLength / 2) {
            points.append((x: Int(b[controlPointsOffset + i * 2]),
                           y: Int(b[controlPointsOffset + i * 2 + 1])))
        }

        var params: [CameraParam: Double] = [:]
        for key in CameraParam.allCases {
            params[key] = decodeParam(b[key.offset], key)
        }

        return ParsedPayload(
            raw: Data(b),
            name: name,
            family: Int(b[familyOffset]),
            controlPoints: points,
            lut: lut,
            params: params
        )
    }

    static func encodeParam(_ value: Double, _ key: CameraParam) -> UInt8 {
        UInt8(javaRound(value / key.step) & 0xFF)
    }

    static func decodeParam(_ raw: UInt8, _ key: CameraParam) -> Double {
        let signed = raw < 128 ? Double(raw) : Double(Int(raw) - 256)
        return (signed * key.step * 100).rounded() / 100
    }

    /// 只改参数区，其余字节原样保留。
    static func setParams(_ payload: Data, _ params: [CameraParam: Double]) -> Data {
        var b = [UInt8](payload)
        for (key, value) in params where b.count > key.offset {
            b[key.offset] = encodeParam(value, key)
        }
        return Data(b)
    }

    /// 只改名称，其余字节原样保留。
    static func withName(_ payload: Data, _ name: String) -> Data {
        var b = [UInt8](payload)
        let clean = Array(sanitizeName(name).utf8)
        for i in 0..<nameLength { b[nameOffset + i] = 0 }
        for (i, byte) in clean.enumerated() where i < nameLength {
            b[nameOffset + i] = byte
        }
        return Data(b)
    }

    /// 机身名称只能是 ≤19 字符的 ASCII。中文会被替换成 `?`——这是机身限制。
    static func sanitizeName(_ raw: String) -> String {
        var out = ""
        for scalar in raw.trimmingCharacters(in: .whitespacesAndNewlines).unicodeScalars {
            out.append((0x20...0x7E).contains(Int(scalar.value)) ? Character(scalar) : Character("?"))
            if out.count >= nameMax { break }
        }
        let trimmed = out.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? "Z30FIT" : trimmed
    }

    static func diff(_ a: Data, _ b: Data) -> PayloadDiff {
        let x = [UInt8](a)
        let y = [UInt8](b)
        let common = min(x.count, y.count)
        var offsets: [Int] = []
        for i in 0..<common where x[i] != y[i] { offsets.append(i) }
        return PayloadDiff(offsets: offsets, sizeA: x.count, sizeB: y.count)
    }

    /// 结构合法的 614 字节骨架（来自真机读回的两条真实配方逐字节比对）。
    static func fallbackTemplate(family: UInt8 = 0x05) -> Data {
        var p = [UInt8](repeating: 0, count: size)
        p[0x000] = 0x02
        p[0x001] = 0x00
        p[0x019] = 0x08
        p[0x01A] = 0x04
        p[0x023] = 0x01
        p[0x024] = 0x49   // 'I'
        p[0x025] = 0x30   // '0'
        p[0x027] = 0xFF
        p[0x029] = 0xFF
        p[0x02A] = 0x01
        p[0x02B] = 0x0F
        p[0x02C] = family
        return Data(p)
    }

    /// 用 256 点 LUT 组装载荷。模板给定时逐字节复制，只覆盖名称、控制点、LUT 段。
    static func buildFromLut(
        _ lut: [Int],
        name: String,
        family: UInt8 = 0x05,
        template: Data? = nil,
        params: [CameraParam: Double]? = nil
    ) throws -> Data {
        guard lut.count == lutPoints else { throw PayloadError.badLutSize(lut.count) }
        let base = template ?? fallbackTemplate(family: family)
        guard base.count == size else { throw PayloadError.badTemplateSize(base.count) }

        var out = [UInt8](withName(base, name))
        for (i, point) in controlPoints(lut).enumerated() {
            out[controlPointsOffset + i * 2] = UInt8(point.x & 0xFF)
            out[controlPointsOffset + i * 2 + 1] = UInt8(point.y & 0xFF)
        }
        for i in 0..<lutPoints {
            let v = min(max(lut[i], 0), lutMax)
            out[lutOffset + i * 2] = UInt8((v >> 8) & 0xFF)
            out[lutOffset + i * 2 + 1] = UInt8(v & 0xFF)
        }
        var data = Data(out)
        if let params, !params.isEmpty {
            data = setParams(data, params)
        }
        return data
    }

    /// 给机身 LCD 画图用的 4 组控制点，8 位量化。不影响成像。
    static func controlPoints(_ lut: [Int], count: Int = 4) -> [(x: Int, y: Int)] {
        var points: [(x: Int, y: Int)] = []
        for i in 1...count {
            let x = Double(i) / Double(count + 1)
            let pos = x * Double(lut.count - 1)
            let i0 = min(max(Int(pos), 0), lut.count - 1)
            let i1 = min(i0 + 1, lut.count - 1)
            let t = pos - Double(i0)
            let value = (Double(lut[i0]) * (1 - t) + Double(lut[i1]) * t) / Double(lutMax)
            points.append((
                x: javaRound(x * 255),
                y: min(max(javaRound(value * 255), 0), 255)
            ))
        }
        return points
    }
}
