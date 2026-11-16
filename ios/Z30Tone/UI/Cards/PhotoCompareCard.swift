import ImageIO
import PhotosUI
import SwiftUI
import UIKit
import Z30ToneCore

/// 照片对比卡：左半边原图、右半边套上当前曲线，拖动分割线看差异。
struct PhotoCompareCard: View {
    @EnvironmentObject private var model: AppModel

    @State private var picked: PhotosPickerItem?
    @State private var original: CGImage?
    @State private var graded: CGImage?
    @State private var split: CGFloat = 0.5
    @State private var askFormat = false
    @State private var exported: ExportResult?

    private var currentLUT: [Int]? {
        model.selectedRecipe?.parsed?.lut
    }

    private var presetName: String {
        model.selectedRecipe?.name ?? "未选预设"
    }

    var body: some View {
        CardSection(
            title: "照片对比",
            subtitle: "拖动分割线：左边原图，右边套上「\(presetName)」"
        ) {
            PhotosPicker(selection: $picked, matching: .images, photoLibrary: .shared()) {
                Label(original == nil ? "选一张照片来看效果" : "换一张照片", systemImage: "rectangle.split.2x1")
                    .font(.subheadline)
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(.bordered)
            .onChange(of: picked) { item in
                guard let item else { return }
                Task {
                    let data = try? await item.loadTransferable(type: Data.self)
                    model.setComparePhoto(data: data)
                }
            }

            if let original, let graded {
                GeometryReader { geo in
                    let width = geo.size.width
                    ZStack(alignment: .leading) {
                        Image(decorative: original, scale: 1)
                            .resizable()
                            .scaledToFit()
                        Image(decorative: graded, scale: 1)
                            .resizable()
                            .scaledToFit()
                            .mask(alignment: .leading) {
                                Rectangle().frame(width: max(0, width * split))
                            }
                        Rectangle()
                            .fill(.white)
                            .frame(width: 1.5)
                            .offset(x: width * split)
                            .shadow(color: .black.opacity(0.35), radius: 2)
                        Circle()
                            .fill(.white)
                            .frame(width: 30, height: 30)
                            .overlay(
                                Image(systemName: "arrow.left.and.right")
                                    .font(.caption2)
                                    .foregroundStyle(.black)
                            )
                            .shadow(color: .black.opacity(0.3), radius: 3)
                            .position(x: width * split, y: geo.size.height / 2)
                    }
                    .contentShape(Rectangle())
                    .gesture(
                        DragGesture(minimumDistance: 0)
                            .onChanged { value in
                                split = min(max(value.location.x / max(width, 1), 0), 1)
                            }
                    )
                }
                .aspectRatio(CGFloat(original.width) / CGFloat(max(original.height, 1)), contentMode: .fit)
                .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))

                HStack {
                    Text("原图")
                    Spacer()
                    Text("‹›")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                    Spacer()
                    Text(presetName)
                        .lineLimit(1)
                }
                .font(.caption2)
                .foregroundStyle(.secondary)

                Button {
                    askFormat = true
                } label: {
                    Label("导出预览图", systemImage: "square.and.arrow.up")
                        .font(.subheadline)
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.bordered)
            } else {
                Text("选一张照片后，这里会出现可拖动的对比。曲线只改明暗，不改色相。")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .onChange(of: model.compareImageData) { data in
            reload(data)
        }
        .onChange(of: model.selectedRecipeID) { _ in
            regrade()
        }
        .confirmationDialog("保存为什么格式？", isPresented: $askFormat, titleVisibility: .visible) {
            Button("PNG · 无损，所见即所得，文件大") { save(asJPEG: false) }
            Button("JPG · 高质量 95，体积小，适合分享") { save(asJPEG: true) }
            Button("取消", role: .cancel) {}
        }
        .sheet(item: $exported) { result in
            ExportSheet(result: result) { exported = nil }
        }
    }

    private func reload(_ data: Data?) {
        graded = nil
        original = nil
        guard let data,
              let source = CGImageSourceCreateWithData(data as CFData, nil),
              let image = CGImageSourceCreateThumbnailAtIndex(source, 0, [
                kCGImageSourceCreateThumbnailFromImageAlways: true,
                kCGImageSourceThumbnailMaxPixelSize: 1600,
              ] as CFDictionary)
        else { return }
        original = image
        regrade()
    }

    private func regrade() {
        guard let original, let lut = currentLUT else {
            graded = nil
            return
        }
        graded = CurveRenderer.apply(original, lut: lut)
        if graded == nil {
            model.message = "这条配方的曲线数据有问题，换一条再试"
        }
    }

    private func save(asJPEG: Bool) {
        guard let graded else { return }
        let ui = UIImage(cgImage: graded)
        let data = asJPEG ? ui.jpegData(compressionQuality: 0.95) : ui.pngData()
        guard let data else {
            model.message = "导出失败：图片编码不出来"
            return
        }
        let name = "Z30-\(presetName.replacingOccurrences(of: " ", with: ""))"
        let file = FileManager.default.temporaryDirectory
            .appendingPathComponent("\(name)-\(Int(Date().timeIntervalSince1970)).\(asJPEG ? "jpg" : "png")")
        do {
            try data.write(to: file, options: .atomic)
            exported = ExportResult(url: file, name: file.lastPathComponent)
        } catch {
            model.message = error.localizedDescription
        }
    }
}
