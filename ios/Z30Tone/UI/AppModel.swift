import Foundation
import SwiftUI

/// 与安卓端 `FitUi` 同字段，界面上要显示的数字一一对应。
/// 放在顶层（不嵌在 @MainActor 类里）是为了能安全地跨线程返回。
struct FitUI: Sendable {
    let name: String
    let table: [Int]         // 256 点 15 位码值，导出载荷用这个
    let curve: [Double]      // 0..1，画图用
    let target: [Double]     // 0..1，分位数匹配的目标
    let toneDevMean: Double
    let toneDevMax: Double
    let blackPercent: Double
    let whitePercent: Double
    let maxFlatRun: Int
    let blackGuarded: Bool
    let adviceLevel: String
    let adviceText: String
    let clipText: String
    let meanChroma: Double
    let width: Int
    let height: Int
    let millis: Int

    var ok: Bool { adviceLevel != "no" }
}

/// 导出结果，界面弹系统分享面板。
struct ExportResult: Identifiable {
    let id = UUID()
    let url: URL
    let name: String
}

/// 设备信息跨线程带回主线程用。
struct CameraInfoBox: Sendable {
    let model: String
    let firmware: String
    let serial: String
    let supportsRead: Bool
    let supportsWrite: Bool
}

/// 一次连接的结果。
private struct ConnectResult: Sendable {
    let cameraName: String
    let info: CameraInfoBox
    let slots: [CameraSlot]
}

/// iOS 端的单一状态源。对标安卓 `AppViewModel`，但去掉 USB 相关（设备/槽位/写入/备份），
/// 把出口换成「导出 .z30」。
@MainActor
final class AppModel: ObservableObject {

    // MARK: - 状态

    @Published private(set) var presets: [BuiltinPreset] = []
    @Published private(set) var hiddenPresetIds: Set<String> = []
    @Published private(set) var aliases: [String: String] = [:]

    @Published private(set) var recipes: [RecipeItem] = []
    @Published private(set) var trashRecipes: [RecipeItem] = []
    @Published private(set) var selectedRecipeID: String?

    @Published var draftName = ""
    @Published var paramsEnabled = true
    @Published var sharp: Double = 0
    @Published var clarity: Double = 0
    @Published var saturation: Double = 0

    @Published private(set) var fit: FitUI?
    @Published private(set) var log: [String] = []
    @Published var message: String?
    @Published private(set) var busy = false
    @Published private(set) var busyText = ""

    /// 「照片对比」选中的照片。放在这里而不是卡片里：列表滚动会重建卡片，
    /// 卡片内部状态会丢，用户表现为「换个预设回来就要重新选照片」。
    @Published private(set) var compareImageData: Data?

    @Published var exportResult: ExportResult?

    // MARK: - 相机连接状态

    @Published var host = PtpIpClient.defaultHost
    @Published private(set) var connected = false
    @Published private(set) var cameraName = ""
    @Published private(set) var cameraModel = ""
    @Published private(set) var cameraFirmware = ""
    @Published private(set) var supportsWrite = false
    @Published private(set) var slots: [CameraSlot] = []
    @Published private(set) var selectedSlot: Int?
    @Published private(set) var slotPreview: ParsedPayload?
    /// 每个槽位写入前的原始字节，供「还原」用。
    @Published private(set) var backups: [Int: Data] = [:]
    /// 最近一次写入的逐字节比对结论。
    @Published private(set) var lastWriteResult: String?

    private let store: RecipeStore
    private let overrides: PresetOverrides
    private var client: PtpIpClient?

    var selectedRecipe: RecipeItem? {
        guard let id = selectedRecipeID else { return nil }
        return recipes.first { $0.id == id }
    }

    var visiblePresets: [BuiltinPreset] {
        presets.filter { !hiddenPresetIds.contains($0.id) }
    }

    var hiddenPresets: [BuiltinPreset] {
        presets.filter { hiddenPresetIds.contains($0.id) }
    }

    func displayName(for preset: BuiltinPreset) -> String {
        aliases[preset.id] ?? preset.name
    }

    init(store: RecipeStore = RecipeStore(), overrides: PresetOverrides = PresetOverrides()) {
        self.store = store
        self.overrides = overrides
        load()
    }

    // MARK: - 加载

    func load() {
        presets = PresetLibrary.load()
        hiddenPresetIds = overrides.hiddenIds
        aliases = overrides.aliases
        refreshRecipes()
        log("预设库 \(presets.count) 套 · 配方 \(recipes.count) 条")
    }

    private func refreshRecipes(prefer id: String? = nil) {
        recipes = store.list()
        trashRecipes = store.trash()
        if let id, recipes.contains(where: { $0.id == id }) {
            selectedRecipeID = id
        } else if let current = selectedRecipeID, recipes.contains(where: { $0.id == current }) {
            return
        } else {
            selectedRecipeID = recipes.first?.id
        }
    }

    private func applyRecipeToDraft(_ item: RecipeItem) {
        draftName = item.name
        let params = item.parsed?.params ?? [:]
        sharp = params[.sharp] ?? 0
        clarity = params[.clarity] ?? 0
        saturation = params[.saturation] ?? 0
    }

    var selectedSlotLabel: String {
        guard let id = selectedSlot, let slot = slots.first(where: { $0.id == id }) else { return "" }
        return slot.label
    }

    /// 当前草稿 + 选中配方拼出来的 614 字节载荷。导出与写入共用。
    private func currentPayload() -> Data? {
        guard let item = selectedRecipe else {
            message = "先在配方库里选一条"
            return nil
        }
        var payload = Payload.withName(item.payload, draftName.isEmpty ? item.name : draftName)
        if paramsEnabled {
            payload = Payload.setParams(payload, [
                .sharp: sharp,
                .clarity: clarity,
                .saturation: saturation,
            ])
        }
        return payload
    }

    // MARK: - 相机 Wi-Fi

    func connectCamera() {
        guard !busy else { return }
        busy = true
        busyText = "正在连接相机…"
        lastWriteResult = nil
        let target = host.trimmingCharacters(in: .whitespacesAndNewlines)
        let cam = PtpIpClient()
        client?.close()
        client = cam

        Task {
            do {
                let result = try await Task.detached(priority: .userInitiated) { () -> ConnectResult in
                    try cam.connect(host: target)
                    let name = try cam.handshake()
                    try cam.openSession()
                    let info = try cam.deviceInfo()
                    let discovered = try cam.discoverSlots()
                    return ConnectResult(
                        cameraName: name,
                        info: CameraInfoBox(
                            model: info.model,
                            firmware: info.firmware,
                            serial: info.serial,
                            supportsRead: info.supportsRead,
                            supportsWrite: info.supportsWrite
                        ),
                        slots: discovered
                    )
                }.value

                self.connected = true
                self.cameraName = result.cameraName
                self.cameraModel = result.info.model
                self.cameraFirmware = result.info.firmware
                self.supportsWrite = result.info.supportsWrite
                self.slots = result.slots
                self.busy = false
                self.busyText = ""
                self.log(
                    "已连接「\(result.cameraName)」\(result.info.model) 固件 \(result.info.firmware)"
                        + " · 可写槽位 \(result.slots.count) 个"
                )
                if result.slots.isEmpty {
                    self.message = "相机连上了，但没有发现可写槽位。确认相机里有自定义 Picture Control"
                } else {
                    self.selectSlot(result.slots[0].id)
                }
            } catch {
                self.busy = false
                self.busyText = ""
                self.connected = false
                self.slots = []
                self.message = error.localizedDescription
                self.log("连接失败：\(error.localizedDescription)")
                cam.close()
            }
        }
    }

    func disconnectCamera() {
        let cam = client
        client = nil
        connected = false
        slots = []
        selectedSlot = nil
        slotPreview = nil
        backups = [:]
        cameraName = ""
        cameraModel = ""
        cameraFirmware = ""
        supportsWrite = false
        Task.detached { cam?.close() }
        log("已断开相机")
    }

    func selectSlot(_ slot: Int) {
        selectedSlot = slot
        loadSlotPreview(slot)
    }

    private func loadSlotPreview(_ slot: Int) {
        guard let cam = client, connected else { return }
        slotPreview = nil
        Task {
            do {
                let data = try await Task.detached(priority: .userInitiated) {
                    try cam.getPictCtrl(slot: slot)
                }.value
                if let parsed = try? Payload.parse(data) {
                    self.slotPreview = parsed
                }
            } catch {
                self.log("读槽位失败：\(error.localizedDescription)")
            }
        }
    }

    /// 把当前配方写进选中槽位：写前备份原字节，写完立刻读回逐字节比对。
    func writeToSelectedSlot() {
        guard let cam = client, connected, let slot = selectedSlot else {
            message = "先连接相机并选一个槽位"
            return
        }
        guard let payload = currentPayload(), !busy else { return }
        let label = selectedSlotLabel
        busy = true
        busyText = "正在写入 \(label)…"

        Task {
            do {
                let result = try await Task.detached(priority: .userInitiated) { () -> (summary: String, original: Data) in
                    let original = try cam.getPictCtrl(slot: slot)
                    try cam.setPictCtrl(slot: slot, payload: payload)
                    let readBack = try cam.getPictCtrl(slot: slot)
                    return (Payload.diff(payload, readBack).summary, original)
                }.value

                if result.original.count == 614 {
                    self.backups[slot] = result.original
                }
                self.lastWriteResult = result.summary
                self.busy = false
                self.busyText = ""
                self.log("写入 \(label)：\(result.summary)")
                if let parsed = try? Payload.parse(payload) {
                    let updated = CameraSlot(
                        id: slot,
                        label: label,
                        name: parsed.name,
                        lutFirst: parsed.first,
                        lutLast: parsed.last
                    )
                    self.slots = self.slots.map { $0.id == slot ? updated : $0 }
                    self.slotPreview = parsed
                }
            } catch {
                self.busy = false
                self.busyText = ""
                self.message = error.localizedDescription
                self.log("写入失败：\(error.localizedDescription)")
            }
        }
    }

    func restoreSlot(_ slot: Int) {
        guard let cam = client, connected, let backup = backups[slot], !busy else { return }
        let label = selectedSlotLabel
        busy = true
        busyText = "正在还原 \(label)…"

        Task {
            do {
                let summary = try await Task.detached(priority: .userInitiated) { () -> String in
                    try cam.setPictCtrl(slot: slot, payload: backup)
                    let readBack = try cam.getPictCtrl(slot: slot)
                    return Payload.diff(backup, readBack).summary
                }.value
                self.lastWriteResult = summary
                self.busy = false
                self.busyText = ""
                self.log("还原 \(label)：\(summary)")
                self.loadSlotPreview(slot)
            } catch {
                self.busy = false
                self.busyText = ""
                self.message = error.localizedDescription
                self.log("还原失败：\(error.localizedDescription)")
            }
        }
    }

    // MARK: - 照片拟合

    func fitPhoto(data: Data) {
        guard !busy else { return }
        busy = true
        busyText = "正在分析照片…"
        let started = Date()

        // 纯计算放后台：30 万点的分位数 + 60 轮高斯-牛顿，主线程扛不住
        let work = Task.detached(priority: .userInitiated) { () -> FitUI in
            let loaded = try PhotoImport.load(data: data)
            let result = PhotoFit.fromLuma(loaded.sortedLuma, meanChroma: loaded.meanChroma)
            let millis = Int(Date().timeIntervalSince(started) * 1000)
            return FitUI(
                name: PhotoFit.suggestName(),
                table: result.table,
                curve: result.values,
                target: result.target,
                toneDevMean: result.metrics.toneDevMean,
                toneDevMax: result.metrics.toneDevMax,
                blackPercent: result.tone.black,
                whitePercent: result.tone.white,
                maxFlatRun: result.banding.maxFlatRun,
                blackGuarded: result.blackGuarded,
                adviceLevel: result.advice.level,
                adviceText: result.advice.text,
                clipText: result.clipText,
                meanChroma: loaded.meanChroma,
                width: loaded.width,
                height: loaded.height,
                millis: millis
            )
        }

        Task {
            do {
                let ui = try await work.value
                self.fit = ui
                self.draftName = ui.name
                self.busy = false
                self.busyText = ""
                self.log(
                    "照片拟合完成 \(ui.width)×\(ui.height) · 贴合误差 "
                        + String(format: "%.2f", ui.toneDevMean) + " 码值 · \(ui.millis) ms"
                )
            } catch {
                self.busy = false
                self.busyText = ""
                self.message = error.localizedDescription
                self.log("拟合失败：\(error.localizedDescription)")
            }
        }
    }

    /// 把拟合结果落成一条配方（进配方库，之后可导出）。
    @discardableResult
    func saveFitAsRecipe() -> RecipeItem? {
        guard let fit else { return nil }
        do {
            let name = draftName.isEmpty ? fit.name : draftName
            let payload = try Payload.buildFromLut(fit.table, name: name)
            let json = """
            {"name":"\(name.replacingOccurrences(of: "\"", with: "'"))",\
            "source":"照片拟合（影调分布迁移）",\
            "mode":"image",\
            "note":"单图模式：只迁移影调分布，不含参考图的色彩个性",\
            "metrics":{"tone_dev_mean":\(String(format: "%.4f", fit.toneDevMean)),\
            "tone_dev_max":\(String(format: "%.4f", fit.toneDevMax))},\
            "tool":"Z30 影调 iOS 端"}
            """
            let raw = try Recipe.build(payload: payload, metaJSON: json)
            let item = try store.importData(raw)
            refreshRecipes(prefer: item.id)
            applyRecipeToDraft(item)
            log("已存为配方：\(item.name)")
            return item
        } catch {
            message = error.localizedDescription
            return nil
        }
    }

    func dismissFit() {
        fit = nil
    }

    // MARK: - 预设库

    func installPreset(_ preset: BuiltinPreset) {
        guard let data = preset.data else {
            message = "这条预设的载荷坏了"
            return
        }
        do {
            let item = try store.importData(data)
            refreshRecipes(prefer: item.id)
            applyRecipeToDraft(item)
            let params = preset.cameraParams
            if !params.isEmpty {
                paramsEnabled = true
                sharp = params[.sharp] ?? sharp
                clarity = params[.clarity] ?? clarity
                saturation = params[.saturation] ?? saturation
            }
            log("已载入预设：\(displayName(for: preset))")
        } catch {
            message = error.localizedDescription
        }
    }

    func hidePresets(_ ids: [String]) {
        overrides.hide(ids)
        hiddenPresetIds = overrides.hiddenIds
        log("已隐藏 \(ids.count) 条预设")
    }

    func restorePreset(_ id: String) {
        overrides.restore(id)
        hiddenPresetIds = overrides.hiddenIds
    }

    func restoreAllPresets() {
        overrides.restoreAll()
        hiddenPresetIds = []
        log("已恢复全部隐藏的预设")
    }

    func renamePreset(_ preset: BuiltinPreset, to name: String) {
        overrides.rename(preset.id, to: name)
        aliases = overrides.aliases
    }

    // MARK: - 配方库

    func selectRecipe(_ item: RecipeItem) {
        selectedRecipeID = item.id
        applyRecipeToDraft(item)
    }

    func importRecipe(data: Data) {
        do {
            let item = try store.importData(data)
            refreshRecipes(prefer: item.id)
            applyRecipeToDraft(item)
            log("导入配方：\(item.name)")
        } catch {
            message = error.localizedDescription
        }
    }

    func renameRecipe(_ item: RecipeItem, to name: String) {
        do {
            let updated = try store.rename(item, to: name)
            refreshRecipes(prefer: updated.id)
            if selectedRecipeID == updated.id { applyRecipeToDraft(updated) }
        } catch {
            message = error.localizedDescription
        }
    }

    func deleteRecipes(_ items: [RecipeItem]) {
        for item in items {
            _ = try? store.moveToTrash(item)
        }
        refreshRecipes()
        log("移入回收站 \(items.count) 条")
    }

    func restoreRecipe(_ item: RecipeItem) {
        _ = try? store.restore(item)
        refreshRecipes(prefer: item.id)
    }

    func purgeRecipe(_ item: RecipeItem) {
        _ = store.purge(item)
        refreshRecipes()
    }

    func purgeTrash() {
        let n = store.purgeAll()
        refreshRecipes()
        log("彻底删除 \(n) 条")
    }

    // MARK: - 对比照片

    func setComparePhoto(data: Data?) {
        compareImageData = data
        if data != nil { log("已选择对比照片") }
    }

    // MARK: - 导出

    /// 把当前选中的配方（套上草稿名与参数）导出成 `.z30`。
    func exportSelected() {
        guard let item = selectedRecipe else {
            message = "先在配方库里选一条"
            return
        }
        do {
            guard let payload = currentPayload() else { return }

            let raw = try Data(contentsOf: item.url)
            let originalMeta = try Recipe.metaJSON(raw)
            var object = (try? JSONSerialization.jsonObject(with: Data(originalMeta.utf8))) as? [String: Any] ?? [:]
            object["name"] = draftName.isEmpty ? item.name : draftName
            object["tool"] = "Z30 影调 iOS 端"
            let meta = (try? JSONSerialization.data(withJSONObject: object, options: [.sortedKeys]))
                .flatMap { String(data: $0, encoding: .utf8) } ?? originalMeta

            let packed = try Recipe.build(payload: payload, metaJSON: meta)
            let safeName = Payload.sanitizeName(draftName.isEmpty ? item.name : draftName)
            let url = FileManager.default.temporaryDirectory
                .appendingPathComponent("\(safeName)-\(Int(Date().timeIntervalSince1970)).z30")
            try packed.write(to: url, options: .atomic)

            exportResult = ExportResult(url: url, name: safeName)
            log("已导出 \(url.lastPathComponent)（\(packed.count) 字节）")
        } catch {
            message = error.localizedDescription
        }
    }

    // MARK: - 杂项

    func consumeMessage() {
        message = nil
    }

    func dismissExport() {
        exportResult = nil
    }

    private func log(_ text: String) {
        let stamp = DateFormatter()
        stamp.locale = Locale(identifier: "en_US_POSIX")
        stamp.dateFormat = "HH:mm:ss"
        log.append("[\(stamp.string(from: Date()))] \(text)")
        if log.count > 200 { log.removeFirst(log.count - 200) }
    }
}
