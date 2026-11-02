package com.z30tone.writer.core.fit

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.net.Uri
import kotlin.math.max

/**
 * 照片 → 排序后的亮度数组。
 *
 * 解 JPEG / PNG / HEIC。RAW（NEF / CR2 等）解不了：系统的图片解码器不认相机原始格式，
 * 这是安卓的已知限制，别在这里硬扛——提示用户换张 JPG。
 *
 * 不做 EXIF 旋转：亮度分布是像素的排列，转不转都一样，电脑端 Pillow 也不转，两边才对得上。
 */
object PhotoLoader {

    /** 与电脑端同一个上限：超过 12MP 先降采样，否则内存和耗时都不划算。 */
    const val MAX_PIXELS = 12_000_000

    /** 与电脑端 `max_pts` 一致：最多取 30 万个像素点参与分位数统计。 */
    const val MAX_LUMA_POINTS = 300_000

    /**
     * 系统图片解码器吃得下的格式。
     *
     * HEIC / HEIF 从 Android 9（API 28）起才由系统解码，本项目 minSdk 26，
     * 所以解不开时要给一句能听懂的话，而不是「解码失败」。
     */
    private val SUPPORTED = setOf("image/jpeg", "image/png", "image/heic", "image/heif")

    private const val FORMAT_HINT = "支持 JPG / PNG / HEIC"

    class Loaded(
        val sortedLuma: DoubleArray,
        val width: Int,
        val height: Int,
        val mime: String?,
        val sampled: Int,
        /**
         * 平均彩度（CIELAB 的 √(a²+b²)）。机身只有一条曲线，参考图的彩度越高，
         * 「能还原的影调」和「还原不了的色彩」之间落差越大 —— 这是给人看的风险提示，
         * 不是误差指标。
         */
        val meanChroma: Double,
    ) {
        val pixels: Int get() = width * height
        val sampleRatio: Double get() = sampled.toDouble() / pixels
    }

    fun load(context: Context, uri: Uri): Loaded {
        val resolver = context.contentResolver

        // 第一步：只量尺寸。
        // 注意：inJustDecodeBounds=true 时 decodeStream **返回 null**（尺寸填在 options 里），
        // 所以返回值不能当成功依据——那是「正常」。真正的失败信号是拿不到流、或 bounds 没填上。
        val boundsStream = try {
            resolver.openInputStream(uri)
        } catch (e: Exception) {
            null
        } ?: throw IllegalArgumentException(
            "拿不到这个文件的内容（系统拒绝读取）。请用相册/文件管理里能直接打开的图，重新选一次"
        )
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        boundsStream.use { BitmapFactory.decodeStream(it, null, bounds) }

        val width = bounds.outWidth
        val height = bounds.outHeight
        if (width <= 0 || height <= 0) {
            // 尺寸都没解出来：多半是系统解码器不认这个格式（RAW，或 Android 9 以下的 HEIC）
            throw IllegalArgumentException("这张图系统解不了（RAW 或旧系统的 HEIC），换张 JPG 再试。$FORMAT_HINT。")
        }
        val mime = bounds.outMimeType
        if (mime != null && mime !in SUPPORTED) {
            throw IllegalArgumentException("只支持 JPG / PNG / HEIC，这张是 $mime")
        }

        // 高像素图降采样：2 的幂，内存直接少 4 倍
        var sample = 1
        while ((width / sample).toLong() * (height / sample) > MAX_PIXELS) sample *= 2

        val options = BitmapFactory.Options().apply {
            inSampleSize = sample
            inPreferredConfig = Bitmap.Config.ARGB_8888
        }
        val dataStream = try {
            resolver.openInputStream(uri)
        } catch (e: Exception) {
            null
        } ?: throw IllegalArgumentException("第二次读取拿不到内容，重新选一次这张图")
        val bitmap = dataStream.use { BitmapFactory.decodeStream(it, null, options) }
            ?: throw IllegalArgumentException(
                if (mime != null && (mime == "image/heic" || mime == "image/heif")) {
                    "这张 HEIC 解不开：系统要 Android 9 以上才支持，换张 JPG 再试"
                } else {
                    "解码失败：这张图可能已损坏，换一张再试"
                },
            )

        return try {
            extract(bitmap, mime, sample)
        } finally {
            bitmap.recycle()
        }
    }

    /** 逐行取像素算亮度。整张图不落地成 DoubleArray，只留抽样后的 30 万个点。 */
    fun extract(bitmap: Bitmap, mime: String? = null, sampled: Int = 1): Loaded {
        val width = bitmap.width
        val height = bitmap.height
        require(width > 0 && height > 0) { "图片尺寸异常" }

        val total = width.toLong() * height
        val step = max(1, (total / MAX_LUMA_POINTS).toInt())
        val out = DoubleArray(((total + step - 1) / step).toInt())

        val row = IntArray(width)
        var index = 0L
        var k = 0
        var chromaSum = 0.0
        for (y in 0 until height) {
            bitmap.getPixels(row, 0, width, 0, y, width, 1)
            for (x in 0 until width) {
                if (index % step == 0L) {
                    val c = row[x]
                    val r = ((c shr 16) and 0xFF) / 255.0
                    val g = ((c shr 8) and 0xFF) / 255.0
                    val b = (c and 0xFF) / 255.0
                    out[k++] = Colorimetry.luma(r, g, b)

                    val lab = Colorimetry.rgbToLab(r, g, b)
                    chromaSum += kotlin.math.hypot(lab[1], lab[2])
                }
                index++
            }
        }

        val luma = out.copyOf(k)
        luma.sort()
        val chroma = if (k > 0) chromaSum / k else 0.0
        return Loaded(luma, width, height, mime, sampled, chroma)
    }
}
