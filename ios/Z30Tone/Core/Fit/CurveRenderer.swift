import CoreGraphics
import Foundation

/// 把 256 点 LUT 应用到图片上，用于分割线对比。
///
/// 逐像素查表，不做插值以外的处理：机身就是「同一张表作用到三个通道」，
/// 预览必须复现这一点，否则对比就没有意义。
enum CurveRenderer {

    /// 预计算 8 位输入 → 8 位输出的查找表。
    static func lookup(_ lut: [Int]) -> [UInt8] {
        guard lut.count == 256 else { return (0...255).map { UInt8($0) } }
        return (0..<256).map { i in
            let v = min(max(lut[i], 0), 32767)
            return UInt8(min(255, max(0, Int((Double(v) / 32767.0 * 255.0).rounded()))))
        }
    }

    static func apply(_ image: CGImage, lut: [Int]) -> CGImage? {
        let table = lookup(lut)
        let width = image.width
        let height = image.height
        guard width > 0, height > 0 else { return nil }

        let bytesPerRow = width * 4
        var buffer = [UInt8](repeating: 0, count: bytesPerRow * height)

        let ok: Bool = buffer.withUnsafeMutableBytes { raw -> Bool in
            guard let base = raw.baseAddress,
                  let ctx = CGContext(
                    data: base,
                    width: width,
                    height: height,
                    bitsPerComponent: 8,
                    bytesPerRow: bytesPerRow,
                    space: CGColorSpaceCreateDeviceRGB(),
                    bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue
                  )
            else { return false }
            ctx.draw(image, in: CGRect(x: 0, y: 0, width: width, height: height))
            return true
        }
        guard ok else { return nil }

        for y in 0..<height {
            let row = y * bytesPerRow
            for x in 0..<width {
                let o = row + x * 4
                buffer[o] = table[Int(buffer[o])]
                buffer[o + 1] = table[Int(buffer[o + 1])]
                buffer[o + 2] = table[Int(buffer[o + 2])]
            }
        }

        return buffer.withUnsafeMutableBytes { raw -> CGImage? in
            guard let base = raw.baseAddress,
                  let ctx = CGContext(
                    data: base,
                    width: width,
                    height: height,
                    bitsPerComponent: 8,
                    bytesPerRow: bytesPerRow,
                    space: CGColorSpaceCreateDeviceRGB(),
                    bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue
                  )
            else { return nil }
            return ctx.makeImage()
        }
    }

    /// 缩放到长边不超过 maxEdge，避免预览时处理整张 6000px 的图。
    static func downscale(_ image: CGImage, maxEdge: Int = 1600) -> CGImage {
        let w = image.width
        let h = image.height
        let longest = max(w, h)
        guard longest > maxEdge else { return image }

        let scale = Double(maxEdge) / Double(longest)
        let nw = max(1, Int(Double(w) * scale))
        let nh = max(1, Int(Double(h) * scale))
        guard let ctx = CGContext(
            data: nil,
            width: nw,
            height: nh,
            bitsPerComponent: 8,
            bytesPerRow: nw * 4,
            space: CGColorSpaceCreateDeviceRGB(),
            bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue
        ) else { return image }
        ctx.interpolationQuality = .high
        ctx.draw(image, in: CGRect(x: 0, y: 0, width: nw, height: nh))
        return ctx.makeImage() ?? image
    }
}
