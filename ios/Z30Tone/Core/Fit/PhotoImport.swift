import CoreGraphics
import Foundation
import ImageIO

/// 照片 → 排序后的亮度数组。
///
/// 与安卓端 `PhotoLoader.kt`、电脑端 `z30fit/images.py` 同一套口径：
/// - 亮度用 Rec.709 权重（Colorimetry.luma）；
/// - **不按 EXIF 旋转**：亮度分布与像素排列无关，电脑端 Pillow 也不转，转了两边就对不上；
/// - 超过 1200 万像素先降采样，最多取 30 万个点参与分位数统计；
/// - 顺手算平均彩度（CIELAB √(a²+b²)），只用于风险提示，不参与曲线计算。
///
/// RAW（NEF/CR2）解不了——系统解码器不认相机原始格式，给一句能听懂的话。
enum PhotoImport {

    /// 与电脑端同一个上限：超过 12MP 先降采样。
    static let maxPixels = 12_000_000

    /// 与电脑端 `max_pts` 一致：最多取 30 万个像素点。
    static let maxLumaPoints = 300_000

    struct Loaded {
        let sortedLuma: [Double]
        let width: Int
        let height: Int
        let sampled: Int
        let meanChroma: Double

        var pixels: Int { width * height }
        var sampleRatio: Double { pixels > 0 ? Double(sampled) / Double(pixels) : 0 }
    }

    static func load(data: Data) throws -> Loaded {
        guard let source = CGImageSourceCreateWithData(data as CFData, nil) else {
            throw ImportError.unsupported
        }
        guard CGImageSourceGetCount(source) > 0 else { throw ImportError.unsupported }

        let props = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString: Any]
        let width = props?[kCGImagePropertyPixelWidth] as? Int ?? 0
        let height = props?[kCGImagePropertyPixelHeight] as? Int ?? 0
        guard width > 0, height > 0 else { throw ImportError.unsupported }

        let total = Double(width) * Double(height)
        var maxEdge = max(width, height)
        if total > Double(maxPixels) {
            maxEdge = Int(Double(maxEdge) * sqrt(Double(maxPixels) / total))
        }

        let options: [CFString: Any] = [
            kCGImageSourceCreateThumbnailFromImageAlways: true,
            // 明确不跟随 EXIF 方向，和安卓/电脑端保持一致
            kCGImageSourceCreateThumbnailWithTransform: false,
            kCGImageSourceThumbnailMaxPixelSize: maxEdge,
            kCGImageSourceShouldCacheImmediately: true,
        ]
        guard let image = CGImageSourceCreateThumbnailAtIndex(source, 0, options as CFDictionary) else {
            throw ImportError.unsupported
        }
        return try extract(image)
    }

    static func extract(_ image: CGImage) throws -> Loaded {
        let width = image.width
        let height = image.height
        guard width > 0, height > 0 else { throw ImportError.unsupported }

        let bytesPerRow = width * 4
        var buffer = [UInt8](repeating: 0, count: bytesPerRow * height)
        let bitmapInfo = CGImageAlphaInfo.noneSkipLast.rawValue

        let drawn: Bool = buffer.withUnsafeMutableBytes { raw -> Bool in
            guard let base = raw.baseAddress,
                  let ctx = CGContext(
                    data: base,
                    width: width,
                    height: height,
                    bitsPerComponent: 8,
                    bytesPerRow: bytesPerRow,
                    space: CGColorSpaceCreateDeviceRGB(),
                    bitmapInfo: bitmapInfo
                  )
            else {
                return false
            }
            ctx.draw(image, in: CGRect(x: 0, y: 0, width: width, height: height))
            return true
        }
        guard drawn else { throw ImportError.unsupported }

        let total = width * height
        let step = max(1, total / maxLumaPoints)
        var luma: [Double] = []
        luma.reserveCapacity(total / step + 1)
        var chromaSum = 0.0
        var k = 0
        var index = 0

        for y in 0..<height {
            let rowStart = y * bytesPerRow
            for x in 0..<width {
                if index % step == 0 {
                    let o = rowStart + x * 4
                    let r = Double(buffer[o]) / 255.0
                    let g = Double(buffer[o + 1]) / 255.0
                    let b = Double(buffer[o + 2]) / 255.0
                    luma.append(Colorimetry.luma(r, g, b))
                    let lab = Colorimetry.rgbToLab(r, g, b)
                    chromaSum += hypot(lab.a, lab.b)
                    k += 1
                }
                index += 1
            }
        }

        luma.sort()
        return Loaded(
            sortedLuma: luma,
            width: width,
            height: height,
            sampled: step,
            meanChroma: k > 0 ? chromaSum / Double(k) : 0
        )
    }
}

enum ImportError: LocalizedError {
    case unsupported
    case tooLarge

    var errorDescription: String? {
        switch self {
        case .unsupported:
            return "这张图解不开（RAW 或已损坏）。支持 JPG / PNG / HEIC，换一张再试。"
        case .tooLarge:
            return "这张图太大了，换一张再试。"
        }
    }
}
