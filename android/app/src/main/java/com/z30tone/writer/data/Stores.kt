package com.z30tone.writer.data

import android.content.Context
import android.net.Uri
import com.z30tone.writer.core.Payload
import com.z30tone.writer.core.Recipe
import com.z30tone.writer.core.Slots
import java.io.File
import java.security.MessageDigest
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * 已导入的配方。文件落在应用私有目录，不占相册、不需要存储权限。
 * 用「载荷哈希」做文件名，同一份配方反复导入不会堆一堆副本。
 */
class RecipeStore(private val context: Context) {

    class Item(
        val file: File,
        val name: String,
        val source: String?,
        val note: String?,
        val createdAt: String?,
        val deltaEMean: Double?,
        val deltaEP95: Double?,
        val payload: ByteArray,
    ) {
        val parsed: Payload.Parsed by lazy { Payload.parse(payload) }
    }

    private val dir: File
        get() = File(context.filesDir, "recipes").apply { if (!exists()) mkdirs() }

    fun list(): List<Item> {
        val files = dir.listFiles { file -> file.isFile && file.name.endsWith(Recipe.SUFFIX) }
            ?: return emptyList()
        return files
            .sortedByDescending { it.lastModified() }
            .mapNotNull { runCatching { read(it) }.getOrNull() }
    }

    fun import(uri: Uri): Item {
        val bytes = context.contentResolver.openInputStream(uri)?.use { it.readBytes() }
            ?: throw IllegalArgumentException("读不到这个文件，换一个位置再试。")
        return importBytes(bytes)
    }

    fun importBytes(bytes: ByteArray): Item {
        val raw = when {
            Recipe.matches(bytes) -> bytes
            bytes.size == Payload.SIZE -> Recipe.build(
                bytes,
                """{"name":"","source":"614 字节载荷","tool":"Z30 写入端"}""",
            )

            else -> throw IllegalArgumentException(
                "这不是 .z30 配方包（${bytes.size} 字节）。请在电脑端拟合、导出 .z30 后再传过来。"
            )
        }

        val payload = Recipe.parse(raw).payload
        val target = File(dir, "z30-${sha256(payload).take(16)}${Recipe.SUFFIX}")
        if (!target.exists()) target.writeBytes(raw)
        return read(target)
    }

    fun delete(item: Item): Boolean = item.file.delete()

    private fun read(file: File): Item {
        val packageData = Recipe.parse(file.readBytes())
        val parsed = Payload.parse(packageData.payload)
        return Item(
            file = file,
            name = packageData.meta.name ?: parsed.name.ifEmpty { file.nameWithoutExtension },
            source = packageData.meta.source,
            note = packageData.meta.note,
            createdAt = packageData.meta.created,
            deltaEMean = packageData.meta.deltaEAvg,
            deltaEP95 = packageData.meta.deltaEP95,
            payload = packageData.payload,
        )
    }

    private fun sha256(bytes: ByteArray): String {
        val digest = MessageDigest.getInstance("SHA-256").digest(bytes)
        val sb = StringBuilder(digest.size * 2)
        for (byte in digest) sb.append("%02x".format(byte))
        return sb.toString()
    }
}

/**
 * 写入前的原槽位备份。写在手机本地，格式就是相机吐出来的原始字节，不做任何加工。
 * 只有 614 字节的备份能原样回写；36 字节的「仅参数」形态没有曲线，回写没有意义。
 */
class BackupStore(private val context: Context) {

    class Item(
        val file: File,
        val slot: Int,
        val size: Int,
        val takenAt: Long,
        val name: String?,
    ) {
        val slotLabel: String get() = Slots.label(slot)
        val restorable: Boolean get() = size == Payload.SIZE
    }

    private val dir: File
        get() = File(context.filesDir, "backups").apply { if (!exists()) mkdirs() }

    fun save(slot: Int, raw: ByteArray): File {
        val stamp = SimpleDateFormat("yyyyMMdd-HHmmss-SSS", Locale.US).format(Date())
        val file = File(dir, "C${slot - Slots.FIRST + 1}_${stamp}_${raw.size}.bin")
        file.writeBytes(raw)
        return file
    }

    fun list(): List<Item> {
        val files = dir.listFiles { file -> file.isFile && file.name.endsWith(".bin") }
            ?: return emptyList()
        return files
            .sortedByDescending { it.lastModified() }
            .mapNotNull { file ->
                val parts = file.nameWithoutExtension.split("_")
                if (parts.size != 3) return@mapNotNull null
                val index = parts[0].removePrefix("C").toIntOrNull() ?: return@mapNotNull null
                val size = parts[2].toIntOrNull() ?: return@mapNotNull null
                val name = if (size == Payload.SIZE) {
                    runCatching { Payload.parse(file.readBytes()).name }.getOrNull()
                } else {
                    null
                }
                Item(file, Slots.FIRST + index - 1, size, file.lastModified(), name)
            }
    }

    fun read(file: File): ByteArray = file.readBytes()
}
