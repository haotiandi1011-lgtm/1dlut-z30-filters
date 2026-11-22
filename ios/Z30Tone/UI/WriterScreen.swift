import SwiftUI

/// 主界面。卡片顺序对齐安卓端 `WriterScreen.kt`：
/// 设备 → 槽位 → 照片拟合 → 预设库 → 照片对比 → 配方 → 日志。
///
/// 差异只在连接方式：安卓走 USB，iOS 走相机 Wi-Fi。写入能力两端一致。
struct WriterScreen: View {
    @EnvironmentObject private var model: AppModel
    @State private var showLibrary = false

    var body: some View {
        ScrollView {
            LazyVStack(spacing: 14) {
                DeviceCard()
                SlotCard()
                PhotoFitCard()
                BuiltinLibraryCard { showLibrary = true }
                PhotoCompareCard()
                RecipeCard()
                LogCard()
            }
            .padding(.horizontal, 16)
            .padding(.top, 8)
            .padding(.bottom, 24)
        }
        .background(Color(uiColor: .systemGroupedBackground))
        .navigationTitle("Z30 影调")
        .scrollDismissesKeyboard(.interactively)
        .safeAreaInset(edge: .bottom) { ExportBar() }
        .sheet(isPresented: $showLibrary) {
            PresetLibrarySheet()
                .environmentObject(model)
        }
        .sheet(item: $model.exportResult) { result in
            ExportSheet(result: result) { model.dismissExport() }
        }
        .alert(
            "出错了",
            isPresented: Binding(
                get: { model.message != nil },
                set: { if !$0 { model.consumeMessage() } }
            )
        ) {
            Button("好") { model.consumeMessage() }
        } message: {
            Text(model.message ?? "")
        }
        .overlay(alignment: .top) {
            if model.busy {
                HStack(spacing: 8) {
                    ProgressView()
                    Text(model.busyText).font(.footnote)
                }
                .padding(.horizontal, 14)
                .padding(.vertical, 8)
                .background(.regularMaterial, in: Capsule())
                .padding(.top, 6)
            }
        }
    }
}

/// 底部固定栏，对应安卓的 `WriteBar`。
struct ExportBar: View {
    @EnvironmentObject private var model: AppModel

    private var barSubtitle: String {
        if model.connected {
            if let slot = model.selectedSlot, !model.selectedSlotLabel.isEmpty {
                return "已连接相机 · 可在槽位卡写入 \(model.selectedSlotLabel)"
            }
            return "已连接相机 · 在槽位卡里选一个槽位"
        }
        if model.selectedRecipe == nil {
            return "先在预设库或配方里选一条"
        }
        return "连上相机 Wi-Fi 可直接写入，或导出 .z30"
    }

    var body: some View {
        HStack(spacing: 12) {
            VStack(alignment: .leading, spacing: 1) {
                Text(model.selectedRecipe?.name ?? "未选配方")
                    .font(.subheadline)
                    .lineLimit(1)
                Text(barSubtitle)
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }
            Spacer(minLength: 0)
            Button {
                model.exportSelected()
            } label: {
                Label("导出 .z30", systemImage: "square.and.arrow.up")
                    .font(.subheadline)
            }
            .buttonStyle(.borderedProminent)
            .disabled(model.selectedRecipe == nil)
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
        .background(.bar)
        .overlay(alignment: .top) {
            Divider()
        }
    }
}

/// 导出完成后的分享面板。
struct ExportSheet: View {
    let result: ExportResult
    let onClose: () -> Void

    var body: some View {
        NavigationStack {
            VStack(spacing: 16) {
                Image(systemName: "doc.badge.arrow.up")
                    .font(.system(size: 44))
                    .foregroundStyle(.tint)
                Text(result.name)
                    .font(.headline)
                Text("也可以在设备卡连上相机 Wi-Fi，直接写入 C1–C9；这个文件适合备份或传给别的设备。")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
                ShareLink(item: result.url) {
                    Label("保存 / 分享", systemImage: "square.and.arrow.up")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent)
                Button("完成", action: onClose)
                    .buttonStyle(.bordered)
                    .frame(maxWidth: .infinity)
            }
            .padding(24)
            .navigationTitle("已导出")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("关闭", action: onClose)
                }
            }
        }
        .presentationDetents([.medium])
    }
}
