import PhotosUI
import SwiftUI
import Z30ToneCore

/// 照片拟合卡：选一张参考图 → 256 点曲线 → 指标与建议。
struct PhotoFitCard: View {
    @EnvironmentObject private var model: AppModel
    @State private var picked: PhotosPickerItem?

    var body: some View {
        CardSection(
            title: "从照片生成曲线",
            subtitle: "单图模式：只迁移影调分布，不含参考图的色彩个性"
        ) {
            PhotosPicker(selection: $picked, matching: .images, photoLibrary: .shared()) {
                Label(model.fit == nil ? "选一张参考照片" : "换一张照片", systemImage: "photo.on.rectangle")
                    .font(.subheadline)
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(.bordered)
            .onChange(of: picked) { item in
                guard let item else { return }
                Task {
                    if let data = try? await item.loadTransferable(type: Data.self) {
                        model.fitPhoto(data: data)
                    } else {
                        model.message = "读不到这张照片，换一张再试"
                    }
                }
            }

            if model.busy {
                HStack(spacing: 8) {
                    ProgressView()
                    Text(model.busyText)
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
            }

            if let fit = model.fit {
                Divider()

                HStack(spacing: 0) {
                    MetricPill(label: "尺寸", value: "\(fit.width)×\(fit.height)")
                    MetricPill(label: "耗时", value: "\(fit.millis) ms")
                    MetricPill(
                        label: "贴合误差",
                        value: String(format: "%.2f / %.2f", fit.toneDevMean, fit.toneDevMax)
                    )
                }

                HStack(spacing: 0) {
                    MetricPill(
                        label: "贴黑",
                        value: String(format: "%.1f%%", fit.blackPercent * 100)
                    )
                    MetricPill(
                        label: "贴白",
                        value: String(format: "%.1f%%", fit.whitePercent * 100)
                    )
                    MetricPill(label: "平均彩度", value: String(format: "%.0f", fit.meanChroma))
                }

                CurveChart(curve: fit.curve, target: fit.target)

                HStack(spacing: 12) {
                    Text("橙线是目标，实线是写进机身的那条")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                    Spacer()
                    Text("最长平段 \(fit.maxFlatRun)")
                        .font(.caption2.monospacedDigit())
                        .foregroundStyle(.secondary)
                }

                AdviceBox(level: fit.adviceLevel, text: fit.adviceText)

                if fit.blackGuarded {
                    Label("黑位比目标高，已压回目标值", systemImage: "arrow.down.to.line")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }

                Button {
                    model.saveFitAsRecipe()
                } label: {
                    Label("存为配方", systemImage: "square.and.arrow.down")
                        .font(.subheadline)
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent)
            }

            Text("「影调贴合误差」单位是 8 位码值，衡量曲线与参考影调的逐点差。单张图没有真值，所以这里不给 ΔE，也不给还原度百分比。")
                .font(.caption2)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}
