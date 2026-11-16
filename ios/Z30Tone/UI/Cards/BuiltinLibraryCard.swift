import SwiftUI

/// 内置预设库入口卡。显示总套数、已隐藏条数，以及当前选中的那一条。
struct BuiltinLibraryCard: View {
    @EnvironmentObject private var model: AppModel
    let onOpen: () -> Void

    var body: some View {
        CardSection(
            title: "内置预设库",
            subtitle: "\(model.presets.count) 套 · 已隐藏 \(model.hiddenPresetIds.count) 条",
            trailing: {
                Button("挑选", action: onOpen)
                    .font(.subheadline)
                    .disabled(model.presets.isEmpty)
            }
        ) {
            if let recipe = model.selectedRecipe {
                HStack(alignment: .top, spacing: 10) {
                    Image(systemName: "checkmark.seal.fill")
                        .foregroundStyle(.tint)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(recipe.name)
                            .font(.subheadline)
                        Text(detail(for: recipe))
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                    }
                    Spacer(minLength: 0)
                }
            } else {
                Text("还没有选中配方。点右上角「挑选」，或从照片生成一条。")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }

            Text("预设只描述影调，不含色彩与颗粒。曲线对三通道同映射，色偏/饱和度/分色相不在能力范围内。")
                .font(.caption2)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private func detail(for recipe: RecipeItem) -> String {
        var parts: [String] = []
        if let cam = recipe.parsed?.name, !cam.isEmpty, cam != recipe.name {
            parts.append("机身名 \(cam)")
        }
        if let source = recipe.source, !source.isEmpty {
            parts.append(source)
        }
        parts.append("\(recipe.payload.count) 字节")
        return parts.joined(separator: " · ")
    }
}

/// 预设库弹窗：搜索、分类分组、隐藏/恢复、逐条改名。
struct PresetLibrarySheet: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss
    @State private var query = ""
    @State private var renaming: BuiltinPreset?
    @State private var newName = ""
    @State private var showHidden = false

    private var filtered: [BuiltinPreset] {
        let source = showHidden ? model.hiddenPresets : model.visiblePresets
        let q = query.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        guard !q.isEmpty else { return source }
        return source.filter {
            $0.id.lowercased().contains(q)
                || $0.name.lowercased().contains(q)
                || $0.cam.lowercased().contains(q)
                || $0.cat.lowercased().contains(q)
                || $0.desc.lowercased().contains(q)
        }
    }

    var body: some View {
        NavigationStack {
            List {
                if filtered.isEmpty {
                    Text(showHidden ? "没有隐藏的预设" : "没有匹配的预设")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
                ForEach(PresetLibrary.grouped(filtered), id: \.category) { group in
                    Section(group.category) {
                        ForEach(group.items) { preset in
                            row(preset)
                        }
                    }
                }
            }
            .listStyle(.insetGrouped)
            .searchable(text: $query, prompt: "名字 / 分类 / 机身名")
            .navigationTitle(showHidden ? "已隐藏" : "内置预设")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button(showHidden ? "看全部" : "看隐藏") {
                        showHidden.toggle()
                    }
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button("关闭") { dismiss() }
                }
                ToolbarItem(placement: .bottomBar) {
                    Button("全部恢复") {
                        model.restoreAllPresets()
                    }
                    .disabled(model.hiddenPresetIds.isEmpty)
                }
            }
            .alert("改名", isPresented: Binding(
                get: { renaming != nil },
                set: { if !$0 { renaming = nil } }
            )) {
                TextField("新名称（最多 40 字）", text: $newName)
                Button("取消", role: .cancel) { renaming = nil }
                Button("保存") {
                    if let preset = renaming {
                        model.renamePreset(preset, to: newName)
                    }
                    renaming = nil
                }
            } message: {
                Text("只改显示名，预设 id 不变。")
            }
        }
    }

    @ViewBuilder
    private func row(_ preset: BuiltinPreset) -> some View {
        Button {
            if showHidden {
                model.restorePreset(preset.id)
            } else {
                model.installPreset(preset)
                dismiss()
            }
        } label: {
            HStack(alignment: .top, spacing: 10) {
                VStack(alignment: .leading, spacing: 2) {
                    Text(model.displayName(for: preset))
                        .font(.subheadline)
                        .foregroundStyle(.primary)
                    Text("\(preset.cam) · \(preset.desc)")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                        .lineLimit(2)
                }
                Spacer(minLength: 0)
                if !preset.cameraParams.isEmpty {
                    Text("S\(Int(preset.cameraParams[.sharp] ?? 0)) C\(Int(preset.cameraParams[.clarity] ?? 0)) V\(Int(preset.cameraParams[.saturation] ?? 0))")
                        .font(.caption2.monospacedDigit())
                        .foregroundStyle(.tertiary)
                }
            }
        }
        .swipeActions(edge: .trailing) {
            if showHidden {
                Button("恢复") { model.restorePreset(preset.id) }
                    .tint(.green)
            } else {
                Button("隐藏", role: .destructive) {
                    model.hidePresets([preset.id])
                }
                Button("改名") {
                    newName = model.displayName(for: preset)
                    renaming = preset
                }
                .tint(.orange)
            }
        }
    }
}

/// 运行日志。安卓端叫「运行日志」，iOS 没有协议交互，改成操作日志。
struct LogCard: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        CardSection(title: "操作日志", subtitle: "最近 \(model.log.count) 条") {
            if model.log.isEmpty {
                Text("还没有操作记录")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            } else {
                VStack(alignment: .leading, spacing: 4) {
                    ForEach(Array(model.log.suffix(12).enumerated()), id: \.offset) { entry in
                        Text(entry.element)
                            .font(.caption2.monospaced())
                            .foregroundStyle(.secondary)
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }
                }
            }
        }
    }
}
