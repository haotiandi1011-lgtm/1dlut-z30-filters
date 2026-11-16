import SwiftUI
import UniformTypeIdentifiers
import Z30ToneCore

/// 配方卡：导入、选中、改名、删除（进回收站）。
struct RecipeCard: View {
    @EnvironmentObject private var model: AppModel

    @State private var showImporter = false
    @State private var showTrash = false
    @State private var renaming: RecipeItem?
    @State private var newName = ""

    var body: some View {
        CardSection(
            title: "配方",
            subtitle: "\(model.recipes.count) 条 · 回收站 \(model.trashRecipes.count) 条",
            trailing: {
                HStack(spacing: 12) {
                    Button("回收站") { showTrash = true }
                        .disabled(model.trashRecipes.isEmpty)
                    Button("导入") { showImporter = true }
                }
                .font(.subheadline)
            }
        ) {
            if model.recipes.isEmpty {
                Text("还没有配方。从预设库挑一条，或用照片生成一条。")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            } else {
                ForEach(Array(model.recipes.enumerated()), id: \.element.id) { entry in
                    row(entry.element)
                    if entry.offset < model.recipes.count - 1 {
                        Divider().opacity(0.5)
                    }
                }
            }

            Text("删除会先移入回收站，回收站里才有「彻底删除」。配方文件存在本机「文件 → Z30 影调」里，可以直接拷走。")
                .font(.caption2)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .fileImporter(
            isPresented: $showImporter,
            allowedContentTypes: [.data],
            allowsMultipleSelection: false
        ) { result in
            switch result {
            case .success(let urls):
                guard let url = urls.first else { return }
                let scoped = url.startAccessingSecurityScopedResource()
                defer { if scoped { url.stopAccessingSecurityScopedResource() } }
                do {
                    model.importRecipe(data: try Data(contentsOf: url))
                } catch {
                    model.message = "读不到这个文件：\(error.localizedDescription)"
                }
            case .failure(let error):
                model.message = error.localizedDescription
            }
        }
        .sheet(isPresented: $showTrash) {
            TrashSheet()
                .environmentObject(model)
        }
        .alert("改名", isPresented: Binding(
            get: { renaming != nil },
            set: { if !$0 { renaming = nil } }
        )) {
            TextField("新名称（最多 40 字）", text: $newName)
            Button("取消", role: .cancel) { renaming = nil }
            Button("保存") {
                if let item = renaming {
                    model.renameRecipe(item, to: newName)
                }
                renaming = nil
            }
        } message: {
            Text("只改配方名，曲线与载荷不动。")
        }
    }

    @ViewBuilder
    private func row(_ item: RecipeItem) -> some View {
        HStack(alignment: .top, spacing: 10) {
            Button {
                model.selectRecipe(item)
            } label: {
                HStack(alignment: .top, spacing: 10) {
                    Image(systemName: model.selectedRecipeID == item.id ? "largecircle.fill.circle" : "circle")
                        .foregroundStyle(model.selectedRecipeID == item.id ? Color.accentColor : Color.secondary)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(item.name)
                            .font(.subheadline)
                            .foregroundStyle(.primary)
                            .multilineTextAlignment(.leading)
                        Text(detail(item))
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                            .multilineTextAlignment(.leading)
                    }
                    Spacer(minLength: 0)
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)

            Menu {
                Button("改名") {
                    newName = item.name
                    renaming = item
                }
                Button("移入回收站", role: .destructive) {
                    model.deleteRecipes([item])
                }
            } label: {
                Image(systemName: "ellipsis")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                    .padding(.vertical, 6)
                    .padding(.horizontal, 4)
            }
        }
    }

    private func detail(_ item: RecipeItem) -> String {
        var parts: [String] = []
        if let source = item.source, !source.isEmpty {
            parts.append(source)
        } else {
            parts.append("本机配方")
        }
        if let de = item.deltaEMean {
            parts.append(String(format: "ΔE %.2f", de))
        } else if let parsed = item.parsed {
            parts.append("机身名 \(parsed.name)")
        }
        parts.append("\(item.payload.count) B")
        return parts.joined(separator: " · ")
    }
}

/// 回收站：恢复 / 彻底删除 / 清空。
struct TrashSheet: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss
    @State private var confirmPurgeAll = false

    var body: some View {
        NavigationStack {
            List {
                if model.trashRecipes.isEmpty {
                    Text("回收站是空的")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
                ForEach(model.trashRecipes) { item in
                    HStack {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(item.name).font(.subheadline)
                            Text(item.createdAt ?? item.payload.count.description + " 字节")
                                .font(.caption2)
                                .foregroundStyle(.secondary)
                        }
                        Spacer()
                        Button("恢复") { model.restoreRecipe(item) }
                            .font(.footnote)
                        Button("彻底删除", role: .destructive) { model.purgeRecipe(item) }
                            .font(.footnote)
                    }
                }
            }
            .navigationTitle("回收站 · \(model.trashRecipes.count) 条")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button("清空", role: .destructive) { confirmPurgeAll = true }
                        .disabled(model.trashRecipes.isEmpty)
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button("关闭") { dismiss() }
                }
            }
            .confirmationDialog(
                "彻底删除回收站里的 \(model.trashRecipes.count) 条？",
                isPresented: $confirmPurgeAll,
                titleVisibility: .visible
            ) {
                Button("彻底删除", role: .destructive) { model.purgeTrash() }
                Button("取消", role: .cancel) {}
            } message: {
                Text("彻底删除后无法恢复。")
            }
        }
    }
}
