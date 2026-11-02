package com.z30tone.writer.core

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Matrix
import android.media.ExifInterface
import android.net.Uri
import java.io.OutputStream

/**
 * 预览用的照片处理：解码、缩到预览尺寸、套 256 点曲线。
 *
 * 套曲线就是一次 8 位查表——机身里干的也是同一件事（LUT 逐像素查表），
 * 所以这里看到的效果和写进机身后的成像是一个映射，不是「近似模拟」。
 */
object Preview {

    const val MAX_SIDE = 1600

    /** 导出上限：像素总量。常见照片（12–25MP）原样导出，更大的按 2 的幂缩进来。 */
    const val EXPORT_MAX_PIXELS = 24_000_000L

    class Loaded(val bitmap: Bitmap, val note: String)

    /** 读一张照片并缩到预览尺寸。RAW 由系统解不了，会抛 IllegalArgumentException。 */
    fun load(context: Context, uri: Uri, maxSide: Int = MAX_SIDE): Loaded {
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        context.contentResolver.openInputStream(uri)?.use {
            BitmapFactory.decodeStream(it, null, bounds)
        }
        if (bounds.outWidth <= 0 || bounds.outHeight <= 0) {
            throw IllegalArgumentException("这张图系统解不了（RAW，或 Android 9 以下的 HEIC），换张 JPG 试试")
        }

        var sample = 1
        while (maxOf(bounds.outWidth, bounds.outHeight) / sample > maxSide * 2) sample *= 2

        val opts = BitmapFactory.Options().apply {
            inSampleSize = sample
            inPreferredConfig = Bitmap.Config.ARGB_8888
        }
        val raw = context.contentResolver.openInputStream(uri)?.use {
            BitmapFactory.decodeStream(it, null, opts)
        } ?: throw IllegalArgumentException("读不到这张照片")

        val scaled = if (maxOf(raw.width, raw.height) > maxSide) {
            val ratio = maxSide.toFloat() / maxOf(raw.width, raw.height)
            Bitmap.createScaledBitmap(
                raw,
                (raw.width * ratio).toInt().coerceAtLeast(1),
                (raw.height * ratio).toInt().coerceAtLeast(1),
                true,
            )
        } else {
            raw
        }
        return Loaded(scaled, "${scaled.width}×${scaled.height}")
    }

    /** 256 点表（0..32767）→ 8 位查表。 */
    fun buildLut(table: IntArray): IntArray = IntArray(256) { i ->
        (table[i].coerceIn(0, Payload.LUT_MAX) * 255 / Payload.LUT_MAX)
    }

    /**
     * 逐像素套曲线。曲线对 R/G/B 是同一个映射——这正是机身的限制，
     * 所以这里也不做任何「分通道」的加工，看到什么就是写进去什么。
     */
    fun applyLut(src: Bitmap, lut: IntArray): Bitmap {
        val w = src.width
        val h = src.height
        val px = IntArray(w * h)
        src.getPixels(px, 0, w, 0, 0, w, h)
        for (i in px.indices) {
            val c = px[i]
            px[i] = (0xFF shl 24) or
                (lut[(c shr 16) and 0xFF] shl 16) or
                (lut[(c shr 8) and 0xFF] shl 8) or
                (lut[c and 0xFF])
        }
        return Bitmap.createBitmap(px, w, h, Bitmap.Config.ARGB_8888)
    }

    // -- 导出成品 -------------------------------------------------------------

    class Exported(
        val width: Int,
        val height: Int,
        /** 原图超过像素上限，导出时按 2 的幂缩小了。 */
        val shrunk: Boolean,
        /** 原图带 EXIF 旋转标记，导出时转正了。 */
        val rotated: Boolean,
    )

    /**
     * 导出：按**原始分辨率**重解码、按 EXIF 转正、套曲线，写进 [out]。
     *
     * 格式两种：PNG 无损（曲线作用在原始像素上，文件大）、JPEG 高质量 95（体积小，
     * 适合分享，有轻微有损压缩）——由调用方选择。
     *
     * 为什么不复用预览那张图：预览缩到 1600px 是为了刷新快，用户要存的是能拿去用的照片。
     * 像素总量超过 [maxPixels] 时按 2 的幂缩（48MP 拍 24MP 的情形），避免 96MB 级的
     * Bitmap 把应用内存压爆；正常 20MP 机身直出全程原尺寸。
     *
     * 逐行套 LUT、直接改在可变 Bitmap 上——不去再开一张同尺寸副本，
     * 峰值内存从三张图降到一张多一点。
     */
    fun exportImage(
        context: Context,
        uri: Uri,
        lut: IntArray,
        out: OutputStream,
        format: Bitmap.CompressFormat = Bitmap.CompressFormat.PNG,
        quality: Int = 100,
        maxPixels: Long = EXPORT_MAX_PIXELS,
    ): Exported {
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        context.contentResolver.openInputStream(uri)?.use {
            BitmapFactory.decodeStream(it, null, bounds)
        }
        if (bounds.outWidth <= 0 || bounds.outHeight <= 0) {
            throw IllegalArgumentException("这张图系统解不了（RAW，或 Android 9 以下的 HEIC）")
        }

        var sample = 1
        while ((bounds.outWidth / sample).toLong() * (bounds.outHeight / sample) > maxPixels) sample *= 2

        val opts = BitmapFactory.Options().apply {
            inSampleSize = sample
            inPreferredConfig = Bitmap.Config.ARGB_8888
            inMutable = true
        }
        val bmp = context.contentResolver.openInputStream(uri)?.use {
            BitmapFactory.decodeStream(it, null, opts)
        } ?: throw IllegalArgumentException("读不到这张照片")

        val rotated = applyExifRotation(context, uri, bmp)?.also {
            if (it !== bmp) bmp.recycle()
        } ?: bmp

        applyLutInPlace(rotated, lut)
        rotated.compress(format, quality, out)
        out.flush()
        val result = Exported(rotated.width, rotated.height, sample > 1, rotated !== bmp)
        rotated.recycle() // 20MP 级的位图近 100MB，尽早还内存
        return result
    }

    /** 按 EXIF 标记转正。不需要转时返回 null（调用方沿用原图）。 */
    private fun applyExifRotation(context: Context, uri: Uri, bmp: Bitmap): Bitmap? {
        val orientation = try {
            context.contentResolver.openInputStream(uri)?.use { stream ->
                ExifInterface(stream).getAttributeInt(
                    ExifInterface.TAG_ORIENTATION,
                    ExifInterface.ORIENTATION_NORMAL,
                )
            } ?: ExifInterface.ORIENTATION_NORMAL
        } catch (e: Exception) {
            ExifInterface.ORIENTATION_NORMAL
        }
        val degrees = when (orientation) {
            ExifInterface.ORIENTATION_ROTATE_90 -> 90f
            ExifInterface.ORIENTATION_ROTATE_180 -> 180f
            ExifInterface.ORIENTATION_ROTATE_270 -> 270f
            else -> 0f
        }
        if (degrees == 0f) return null
        val m = Matrix().apply { postRotate(degrees) }
        return Bitmap.createBitmap(bmp, 0, 0, bmp.width, bmp.height, m, true)
    }

    /** 逐行套 LUT，直接改在可变 Bitmap 上。要求 [bmp] mutable。 */
    private fun applyLutInPlace(bmp: Bitmap, lut: IntArray) {
        if (!bmp.isMutable) {
            throw IllegalStateException("导出用的 Bitmap 必须可变")
        }
        val w = bmp.width
        val row = IntArray(w)
        for (y in 0 until bmp.height) {
            bmp.getPixels(row, 0, w, 0, y, w, 1)
            for (x in 0 until w) {
                val c = row[x]
                row[x] = (0xFF shl 24) or
                    (lut[(c shr 16) and 0xFF] shl 16) or
                    (lut[(c shr 8) and 0xFF] shl 8) or
                    (lut[c and 0xFF])
            }
            bmp.setPixels(row, 0, w, 0, y, w, 1)
        }
    }
}
