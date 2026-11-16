import Foundation

/// 配方包 `.z30` 的读取与写出。
///
/// 格式（小端）：
///
///     +0x00   8   魔数 b"Z30PC1\0\0"
///     +0x08   4   元数据长度 u32
///     +0x0C   n   元数据 JSON（UTF-8）
///     ...   614  载荷本体
///
/// 设计原则：**载荷是唯一真值**，元数据只用于展示。JSON 坏掉也不影响写入。
enum Recipe {

    static let magic: [UInt8] = [0x5A, 0x33, 0x30, 0x50, 0x43, 0x31, 0x00, 0x00]  // "Z30PC1\0\0"
    static let header = 12
    static let suffix = ".z30"

    static func matches(_ raw: Data) -> Bool {
        guard raw.count >= header + Payload.size else { return false }
        let b = [UInt8](raw.prefix(magic.count))
        return b == magic
    }

    static func readIntLE(_ raw: [UInt8], _ offset: Int) -> Int {
        Int(raw[offset])
            | (Int(raw[offset + 1]) << 8)
            | (Int(raw[offset + 2]) << 16)
            | (Int(raw[offset + 3]) << 24)
    }

    static func writeIntLE(_ raw: inout [UInt8], _ offset: Int, _ value: Int) {
        raw[offset] = UInt8(value & 0xFF)
        raw[offset + 1] = UInt8((value >> 8) & 0xFF)
        raw[offset + 2] = UInt8((value >> 16) & 0xFF)
        raw[offset + 3] = UInt8((value >> 24) & 0xFF)
    }

    /// 取出元数据原文（改名时只在原文上改 name，其余字段原样保留）。
    static func metaJSON(_ raw: Data) throws -> String {
        guard matches(raw) else { throw RecipeError.notAPackage }
        let b = [UInt8](raw)
        let metaLength = readIntLE(b, 8)
        let maxMetaLength = b.count - header - Payload.size
        guard metaLength >= 0, metaLength <= maxMetaLength else {
            throw RecipeError.badMetaLength(metaLength)
        }
        return String(decoding: b[header..<(header + metaLength)], as: UTF8.self)
    }

    static func parse(_ raw: Data) throws -> RecipePackage {
        let text = try metaJSON(raw)
        let b = [UInt8](raw)
        let metaLength = readIntLE(b, 8)
        let start = header + metaLength
        let payload = Data(b[start..<(start + Payload.size)])
        return RecipePackage(payload: payload, meta: parseMeta(text), metaJSON: text)
    }

    static func build(payload: Data, metaJSON: String) throws -> Data {
        guard payload.count == Payload.size else { throw PayloadError.badTemplateSize(payload.count) }
        let meta = Array(metaJSON.data(using: .utf8) ?? Data())
        var out = [UInt8](repeating: 0, count: header + meta.count + payload.count)
        for (i, byte) in magic.enumerated() { out[i] = byte }
        writeIntLE(&out, 8, meta.count)
        for (i, byte) in meta.enumerated() { out[header + i] = byte }
        let payloadStart = header + meta.count
        for (i, byte) in payload.enumerated() { out[payloadStart + i] = byte }
        return Data(out)
    }

    static func parseMeta(_ text: String) -> RecipeMeta {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty,
              let data = trimmed.data(using: .utf8),
              let obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
        else {
            // 元数据只是备注，坏了不影响写入。
            return .empty
        }

        func str(_ key: String) -> String? {
            guard let value = obj[key] as? String else { return nil }
            return value.isEmpty ? nil : value
        }

        func numbers(_ key: String) -> [String: Double] {
            guard let dict = obj[key] as? [String: Any] else { return [:] }
            var out: [String: Double] = [:]
            for (k, v) in dict {
                if let n = v as? NSNumber {
                    out[k] = n.doubleValue
                } else if let s = v as? String, let d = Double(s) {
                    out[k] = d
                }
            }
            return out
        }

        return RecipeMeta(
            name: str("name"),
            source: str("source"),
            note: str("note"),
            created: str("created"),
            tool: str("tool"),
            metrics: numbers("metrics"),
            tone: numbers("tone")
        )
    }
}

enum RecipeError: LocalizedError {
    case notAPackage
    case badMetaLength(Int)
    case tooSmall(Int)

    var errorDescription: String? {
        switch self {
        case .notAPackage:
            return "文件头不匹配，不是 .z30 配方包"
        case .badMetaLength(let n):
            return "配方包的元数据长度异常（\(n)）"
        case .tooSmall(let n):
            return "文件太小（\(n) 字节），不是有效的配方包"
        }
    }
}

struct RecipeMeta {
    var name: String?
    var source: String?
    var note: String?
    var created: String?
    var tool: String?
    var metrics: [String: Double]
    var tone: [String: Double]

    static let empty = RecipeMeta(
        name: nil, source: nil, note: nil, created: nil, tool: nil,
        metrics: [:], tone: [:]
    )

    /// 电脑端写入的键是 mean / p95。
    var deltaEAvg: Double? { metrics["mean"] ?? metrics["delta_e_avg"] }
    var deltaEP95: Double? { metrics["p95"] }

    /// 单图拟合的贴合误差（8 位码值）。口径与桌面端 `tone_match_*` 一致。
    var toneDevMean: Double? { metrics["tone_dev_mean"] }
    var toneDevMax: Double? { metrics["tone_dev_max"] }
}

struct RecipePackage {
    let payload: Data
    let meta: RecipeMeta
    let metaJSON: String

    var parsed: ParsedPayload? { try? Payload.parse(payload) }

    /// 列表里显示的名字：优先元数据里的 name，其次载荷里的机身名。
    var displayName: String {
        if let name = meta.name, !name.isEmpty { return name }
        if let p = parsed, !p.name.isEmpty { return p.name }
        return "未命名"
    }
}
