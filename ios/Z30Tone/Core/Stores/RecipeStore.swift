import CryptoKit
import Foundation

/// 已导入的配方。文件落在 App 的「文稿」目录，配合 `UIFileSharingEnabled`
/// 用户能在「文件」App 里直接看到、拷进拷出。
///
/// 文件名用**载荷哈希**，同一份配方反复导入不会堆一串副本。
struct RecipeItem: Identifiable {
    let id: String          // 文件名
    let url: URL
    let name: String
    let source: String?
    let note: String?
    let createdAt: String?
    let deltaEMean: Double?
    let deltaEP95: Double?
    let payload: Data

    var parsed: ParsedPayload? { try? Payload.parse(payload) }
    var slotName: String { parsed?.name ?? "" }
}

/// 配方库 + 回收站。
///
/// 删除语义按 `产品约定.md`：删除 = 移入回收站；回收站里才有「彻底删除」。
final class RecipeStore {

    /// 导入上限：`.z30` 只有 1 KB 左右，超过这个数就是选错了文件。
    static let maxImportBytes = 1_000_000

    private let fileManager = FileManager.default
    private let root: URL

    init(root: URL? = nil) {
        if let root {
            self.root = root
        } else {
            let base = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask).first
                ?? FileManager.default.temporaryDirectory
            self.root = base.appendingPathComponent("recipes", isDirectory: true)
        }
        try? fileManager.createDirectory(at: self.recipesDir, withIntermediateDirectories: true)
        try? fileManager.createDirectory(at: self.trashDir, withIntermediateDirectories: true)
    }

    var recipesDir: URL { root }
    var trashDir: URL { root.deletingLastPathComponent().appendingPathComponent("recipes_trash", isDirectory: true) }

    // MARK: - 读

    func list() -> [RecipeItem] { listIn(recipesDir) }
    func trash() -> [RecipeItem] { listIn(trashDir) }

    private func listIn(_ folder: URL) -> [RecipeItem] {
        guard let files = try? fileManager.contentsOfDirectory(
            at: folder,
            includingPropertiesForKeys: [.contentModificationDateKey],
            options: [.skipsHiddenFiles]
        ) else {
            return []
        }
        return files
            .filter { $0.pathExtension.lowercased() == "z30" }
            .compactMap { try? read($0) }
            .sorted { lhs, rhs in
                let l = (try? lhs.url.resourceValues(forKeys: [.contentModificationDateKey]).contentModificationDate) ?? .distantPast
                let r = (try? rhs.url.resourceValues(forKeys: [.contentModificationDateKey]).contentModificationDate) ?? .distantPast
                return l > r
            }
    }

    private func read(_ url: URL) throws -> RecipeItem {
        let raw = try Data(contentsOf: url)
        let pkg = try Recipe.parse(raw)
        return RecipeItem(
            id: url.lastPathComponent,
            url: url,
            name: pkg.displayName,
            source: pkg.meta.source,
            note: pkg.meta.note,
            createdAt: pkg.meta.created,
            deltaEMean: pkg.meta.deltaEAvg,
            deltaEP95: pkg.meta.deltaEP95,
            payload: pkg.payload
        )
    }

    // MARK: - 导入

    /// 接受两种输入：完整 `.z30` 包，或裸的 614 字节载荷。
    @discardableResult
    func importData(_ raw: Data) throws -> RecipeItem {
        guard raw.count <= Self.maxImportBytes else {
            throw RecipeError.tooSmall(raw.count)
        }

        let packed: Data
        if Recipe.matches(raw) {
            packed = raw
        } else if raw.count == Payload.size {
            packed = try Recipe.build(
                payload: raw,
                metaJSON: "{\"name\":\"\",\"source\":\"614 字节载荷\",\"tool\":\"Z30 影调 iOS 端\"}"
            )
        } else {
            throw RecipeError.tooSmall(raw.count)
        }

        let payload = try Recipe.parse(packed).payload
        let fileName = "z30-\(Self.sha256Hex(payload).prefix(16)).z30"
        let target = recipesDir.appendingPathComponent(fileName)
        if !fileManager.fileExists(atPath: target.path) {
            try packed.write(to: target, options: .atomic)
        }
        return try read(target)
    }

    // MARK: - 改名（只改元数据，载荷与文件名都不动）

    @discardableResult
    func rename(_ item: RecipeItem, to newName: String) throws -> RecipeItem {
        let clean = String(newName.trimmingCharacters(in: .whitespacesAndNewlines).prefix(40))
        guard !clean.isEmpty else { throw StoreError.emptyName }

        let raw = try Data(contentsOf: item.url)
        let pkg = try Recipe.parse(raw)
        var object = (try? JSONSerialization.jsonObject(with: Data(pkg.metaJSON.utf8))) as? [String: Any] ?? [:]
        object["name"] = clean
        let meta = (try? JSONSerialization.data(withJSONObject: object, options: [.sortedKeys]))
            .flatMap { String(data: $0, encoding: .utf8) } ?? pkg.metaJSON

        let out = try Recipe.build(payload: pkg.payload, metaJSON: meta)
        try out.write(to: item.url, options: .atomic)
        return try read(item.url)
    }

    // MARK: - 删除 / 回收站

    @discardableResult
    func moveToTrash(_ item: RecipeItem) throws -> Bool {
        let target = trashDir.appendingPathComponent(item.url.lastPathComponent)
        if fileManager.fileExists(atPath: target.path) {
            try fileManager.removeItem(at: target)
        }
        try fileManager.moveItem(at: item.url, to: target)
        return true
    }

    @discardableResult
    func restore(_ item: RecipeItem) throws -> Bool {
        let target = recipesDir.appendingPathComponent(item.url.lastPathComponent)
        if fileManager.fileExists(atPath: target.path) {
            try fileManager.removeItem(at: target)
        }
        try fileManager.moveItem(at: item.url, to: target)
        return true
    }

    /// 彻底删除：不可恢复。
    @discardableResult
    func purge(_ item: RecipeItem) -> Bool {
        (try? fileManager.removeItem(at: item.url)) != nil
    }

    @discardableResult
    func purgeAll() -> Int {
        trash().reduce(0) { count, item in count + (purge(item) ? 1 : 0) }
    }

    // MARK: - 工具

    static func sha256Hex(_ data: Data) -> String {
        SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
    }
}

enum StoreError: LocalizedError {
    case emptyName

    var errorDescription: String? {
        switch self {
        case .emptyName: return "名字不能为空"
        }
    }
}
