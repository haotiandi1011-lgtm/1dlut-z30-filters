import Foundation

/// 内置预设库（121 套，随包只读）。
///
/// 资源就是安卓端那一份 `builtin_presets.json`——曲线已经封装成 614 字节载荷（base64），
/// 两端搬的是同一批字节，天然一致，不需要在 iOS 侧重算。
struct BuiltinPreset: Codable, Identifiable, Equatable {
    let id: String
    let name: String
    let cat: String
    let desc: String
    let cam: String
    let params: [String: Double]
    let payload: String

    var data: Data? { Data(base64Encoded: payload) }
    var parsed: ParsedPayload? { data.flatMap { try? Payload.parse($0) } }

    /// JSON 里的键名与 `CameraParam.rawValue` 同名（sharp / clarity / saturation）。
    var cameraParams: [CameraParam: Double] {
        var out: [CameraParam: Double] = [:]
        for (key, value) in params {
            if let param = CameraParam(rawValue: key) { out[param] = value }
        }
        return out
    }
}

private struct PresetFile: Codable {
    let version: Int
    let note: String
    let count: Int
    let presets: [BuiltinPreset]
}

enum PresetLibrary {

    /// 读随包的 `builtin_presets.json`。读不到就返回空——界面上会显示「预设库为空」，
    /// 不静默崩掉。
    static func load(from bundle: Bundle = .main) -> [BuiltinPreset] {
        guard let url = bundle.url(forResource: "builtin_presets", withExtension: "json"),
              let raw = try? Data(contentsOf: url),
              let file = try? JSONDecoder().decode(PresetFile.self, from: raw)
        else {
            return []
        }
        return file.presets
    }

    /// 按分类分组，保持 JSON 里的原始顺序（安卓端也是按这个顺序展示）。
    static func grouped(_ presets: [BuiltinPreset]) -> [(category: String, items: [BuiltinPreset])] {
        var order: [String] = []
        var buckets: [String: [BuiltinPreset]] = [:]
        for preset in presets {
            if buckets[preset.cat] == nil {
                order.append(preset.cat)
                buckets[preset.cat] = []
            }
            buckets[preset.cat]?.append(preset)
        }
        return order.map { ($0, buckets[$0] ?? []) }
    }
}

/// 隐藏列表与逐条改名。
///
/// **改名只改显示名，id 永远不动**——隐藏/恢复/回收站都挂在 id 上，
/// 这是 `产品约定.md` 里的硬契约。
final class PresetOverrides {

    private let defaults: UserDefaults
    private let hiddenKey = "preset.hiddenIds"
    private let aliasKey = "preset.aliases"

    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
    }

    var hiddenIds: Set<String> {
        get { Set(defaults.stringArray(forKey: hiddenKey) ?? []) }
        set { defaults.set(Array(newValue).sorted(), forKey: hiddenKey) }
    }

    var aliases: [String: String] {
        get { defaults.dictionary(forKey: aliasKey) as? [String: String] ?? [:] }
        set { defaults.set(newValue, forKey: aliasKey) }
    }

    func isHidden(_ id: String) -> Bool { hiddenIds.contains(id) }

    func displayName(for preset: BuiltinPreset) -> String {
        aliases[preset.id] ?? preset.name
    }

    func hide(_ ids: [String]) {
        var current = hiddenIds
        current.formUnion(ids)
        hiddenIds = current
    }

    func restore(_ id: String) {
        var current = hiddenIds
        current.remove(id)
        hiddenIds = current
    }

    func restoreAll() {
        hiddenIds = []
    }

    func rename(_ id: String, to name: String) {
        let clean = name.trimmingCharacters(in: .whitespacesAndNewlines)
        var current = aliases
        if clean.isEmpty {
            current.removeValue(forKey: id)
        } else {
            current[id] = String(clean.prefix(40))
        }
        aliases = current
    }
}
