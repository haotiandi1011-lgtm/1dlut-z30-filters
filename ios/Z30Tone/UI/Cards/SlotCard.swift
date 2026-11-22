import SwiftUI

/// 槽位卡：对标安卓端的槽位九宫格。
/// 编号由客户端连接时扫描得出（Wi-Fi 下 614 字节的才是自定义槽位），
/// 界面上按顺序叫 C1–C9。
struct SlotCard: View {
    @EnvironmentObject private var model: AppModel
    @State private var confirmWrite = false

    private let columns = [
        GridItem(.flexible(), spacing: 8),
        GridItem(.flexible(), spacing: 8),
        GridItem(.flexible(), spacing: 8)
    ]

    var body: some View {
        CardSection(
            title: "槽位",
            subtitle: "写入前自动备份原内容，写完立刻读回比对"
        ) {
            if model.slots.isEmpty {
                Text(model.connected ? "没有发现可写槽位。确认相机里已存有自定义 Picture Control。" : "连上相机后，这里会列出 C1–C9。")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            } else {
                LazyVGrid(columns: columns, spacing: 8) {
                    ForEach(model.slots) { slot in
                        slotButton(slot)
                    }
                }
            }

            if let preview = model.slotPreview {
                Divider().opacity(0.5)
                VStack(alignment: .leading, spacing: 3) {
                    Text("\(model.selectedSlotLabel) · \(preview.name)")
                        .font(.subheadline)
                    Text("首 \(preview.first) / 末 \(preview.last) / 峰值 \(preview.peak) · \(preview.monotonic ? "单调不降" : "有回折")")
                        .font(.caption2.monospacedDigit())
                        .foregroundStyle(.secondary)
                }
            }

            if let result = model.lastWriteResult {
                Text(result)
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }

            HStack(spacing: 10) {
                Button {
                    confirmWrite = true
                } label: {
                    Label(
                        "写入 \(model.selectedSlotLabel.isEmpty ? "槽位" : model.selectedSlotLabel)",
                        systemImage: "arrow.down.to.line"
                    )
                    .frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent)
                .disabled(!model.connected || model.selectedSlot == nil || model.selectedRecipe == nil || model.busy)

                if let slot = model.selectedSlot, model.backups[slot] != nil {
                    Button("还原") { model.restoreSlot(slot) }
                        .buttonStyle(.bordered)
                        .disabled(model.busy)
                }
            }

            if model.selectedRecipe == nil {
                Text("先在预设库或配方里选一条，才能写入。")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
        }
        .confirmationDialog(
            "写入会改变相机里这个槽位的内容",
            isPresented: $confirmWrite,
            titleVisibility: .visible
        ) {
            Button("写入 \(model.selectedSlotLabel)", role: .destructive) {
                model.writeToSelectedSlot()
            }
            Button("取消", role: .cancel) {}
        } message: {
            Text("原内容会备份在 App 里，写入后可以点「还原」写回去。")
        }
    }

    private func slotButton(_ slot: CameraSlot) -> some View {
        let selected = model.selectedSlot == slot.id
        return Button {
            model.selectSlot(slot.id)
        } label: {
            VStack(spacing: 2) {
                Text(slot.label)
                    .font(.footnote.bold())
                Text(slot.name.isEmpty ? "空" : slot.name)
                    .font(.caption2)
                    .lineLimit(1)
                    .minimumScaleFactor(0.7)
            }
            .frame(maxWidth: .infinity)
            .padding(.vertical, 8)
            .background(
                RoundedRectangle(cornerRadius: 10, style: .continuous)
                    .fill(selected ? Color.accentColor.opacity(0.16) : Color(uiColor: .tertiarySystemGroupedBackground))
            )
            .overlay(
                RoundedRectangle(cornerRadius: 10, style: .continuous)
                    .strokeBorder(selected ? Color.accentColor.opacity(0.65) : Color.clear, lineWidth: 1)
            )
        }
        .buttonStyle(.plain)
    }
}
