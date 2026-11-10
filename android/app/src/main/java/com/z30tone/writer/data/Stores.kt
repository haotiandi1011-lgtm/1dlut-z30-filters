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

    /** 导入上限：.z30 配方包只有 1 KB 左右，超这个数的文件就是选错了。 */
    private val MAX_IMPORT_BYTES = 1_000_000

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

    /** 回收站。删掉的配方先放这儿，随时能拿回来，也可以在里面彻底删掉。 */
    private val trashDir: File
        get() = File(context.filesDir, "recipes_trash").apply { if (!exists()) mkdirs() }

    fun list(): List<Item> = listIn(dir)

    fun trash(): List<Item> = listIn(trashDir)

    private fun listIn(folder: File): List<Item> {
        val files = folder.listFiles { file -> file.isFile && file.name.endsWith(Recipe.SUFFIX) }
            ?: return emptyList()
        return files
            .sortedByDescending { it.lastModified() }
            .mapNotNull { runCatching { read(it) }.getOrNull() }
    }

    fun import(uri: Uri): Item {
        // openInputStream 的三种失败要分开说话：拒绝授权、文件没了、拿不到流。
        // 另外配方包只有 ~1KB，超大文件（误选了视频/相册原图）在读之前就拦掉，别把内存吃爆。
        val bytes = try {
            context.contentResolver.openInputStream(uri)?.use { stream ->
                readCapped(stream, MAX_IMPORT_BYTES)
            }
        } catch (e: SecurityException) {
            throw IllegalArgumentException("没有权限读这个文件，重新在系统选择器里选一次")
        } catch (e: java.io.FileNotFoundException) {
            throw IllegalArgumentException("文件不存在或已被移动，重新选一次")
        } ?: throw IllegalArgumentException("读不到这个文件，换一个位置再试。")
        return importBytes(bytes)
    }

    private fun readCapped(stream: java.io.InputStream, cap: Int): ByteArray {
        val out = java.io.ByteArrayOutputStream()
        val buf = ByteArray(64 * 1024)
        while (true) {
            val n = stream.read(buf)
            if (n < 0) break
            out.write(buf, 0, n)
            if (out.size() > cap) {
                throw IllegalArgumentException(
                    "这个文件超过 ${cap / 1024} KB，不是配方包（.z30 只有 1 KB 左右）"
                )
            }
        }
        return out.toByteArray()
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

    /**
     * 改名：只改元数据里的 name，载荷与文件名（按载荷哈希）都不动，
     * 「同一条配方反复导入去重」的逻辑不受影响。
     */
    fun rename(item: Item, newName: String): Item {
        val clean = newName.trim().take(40)
        require(clean.isNotEmpty()) { "名字不能为空" }
        val raw = item.file.readBytes()
        val pkg = Recipe.parse(raw)
        val meta = org.json.JSONObject(Recipe.metaJson(raw)).put("name", clean)
        val out = Recipe.build(pkg.payload, meta.toString())
        val tmp = File(item.file.parentFile, item.file.name + ".tmp")
        tmp.writeBytes(out)
        if (!tmp.renameTo(item.file)) {
            item.file.delete()
            if (!tmp.renameTo(item.file)) throw IllegalStateException("改名失败：写不进文件")
        }
        return read(item.file)
    }

    /** 移入回收站（不是真删）。同名文件先清掉，避免恢复时串包。 */
    fun delete(item: Item): Boolean {
        val target = File(trashDir, item.file.name)
        if (target.exists()) target.delete()
        val ok = item.file.renameTo(target)
        if (!ok) return item.file.delete()
        return true
    }

    fun restore(item: Item): Boolean {
        val target = File(dir, item.file.name)
        if (target.exists()) target.delete()
        return item.file.renameTo(target)
    }

    /** 彻底删除：回收站里的条目直接抹掉，不可恢复。 */
    fun purge(item: Item): Boolean = item.file.delete()

    fun purgeAll(): Int = trash().count { it.file.delete() }

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

    /**
     * 删除一份备份文件。备份不进回收站（约定如此），删了就没法再从这份恢复，
     * 所以界面要求二次确认。
     */
    fun delete(item: Item): Boolean = item.file.delete()
}
