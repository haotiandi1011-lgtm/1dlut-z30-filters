package com.z30tone.writer.data

import android.content.Context
import android.util.Base64
import com.z30tone.writer.core.Payload
import org.json.JSONObject

/**
 * 随 APK 打包的内置预设。
 *
 * 数据来自 `assets/builtin_presets.json`，由桌面端的 `tools/gen_android_presets.py` 生成：
 * 每条预设都已经封装成 614 字节载荷（曲线 + 控制点 + 名称 + 参数），
 * **手机端不做拟合、也不重新拼载荷**，和「导入 .z30」走的是同一条写入路径。
 *
 * 生成脚本保证：载荷里的 LUT 与桌面端曲线逐点一致、名称是 ≤19 字符 ASCII、曲线单调。
 */
class BuiltinPreset(
    val id: String,
    val name: String,
    val cat: String,
    val desc: String,
    val cam: String,
    val payload: ByteArray,
) {
    val parsed: Payload.Parsed by lazy { Payload.parse(payload) }
    val lut: IntArray get() = parsed.lut
    val params: Map<String, Float> get() = parsed.params

    /** 列表里显示的第二行：分类 + 机身名（写进机身的就是这个）。 */
    val subtitle: String get() = "$cat · $cam"

    /**
     * 落成 `.z30` 时带的元数据。载荷才是唯一真值，这里只是让人在配方列表里认得出它。
     */
    fun metaJson(): String = JSONObject().apply {
        put("name", name)
        put("source", "内置预设")
        put("note", desc)
        put("tool", "Z30 影调工具")
        put("created", java.text.SimpleDateFormat("yyyy-MM-dd HH:mm", java.util.Locale.US)
            .format(java.util.Date()))
    }.toString()
}

class BuiltinPresetStore(private val context: Context) {

    private val prefs by lazy {
        context.getSharedPreferences("z30_presets", Context.MODE_PRIVATE)
    }

    /** 被用户隐藏掉的内置预设。内置数据删不掉「真身」，所以对它们只做隐藏。 */
    fun hiddenIds(): Set<String> =
        prefs.getStringSet(KEY_HIDDEN, emptySet())?.toSet() ?: emptySet()

    fun hide(ids: Collection<String>): Set<String> {
        val next = hiddenIds() + ids
        prefs.edit().putStringSet(KEY_HIDDEN, next).apply()
        return next
    }

    fun unhide(ids: Collection<String>): Set<String> {
        val next = hiddenIds() - ids.toSet()
        prefs.edit().putStringSet(KEY_HIDDEN, next).apply()
        return next
    }

    fun unhideAll(): Set<String> {
        prefs.edit().putStringSet(KEY_HIDDEN, emptySet()).apply()
        return emptySet()
    }

    /**
     * 读一次资产文件。失败时返回空列表——没有内置预设也不该让应用起不来，
     * 用户仍然可以导入自己的 .z30。
     */
    fun load(): List<BuiltinPreset> {
        val text = try {
            context.assets.open(ASSET).bufferedReader(Charsets.UTF_8).use { it.readText() }
        } catch (e: Exception) {
            return emptyList()
        }

        return try {
            val array = JSONObject(text).optJSONArray("presets") ?: return emptyList()
            (0 until array.length()).mapNotNull { index ->
                val obj = array.optJSONObject(index) ?: return@mapNotNull null
                val payload = runCatching {
                    Base64.decode(obj.optString("payload"), Base64.DEFAULT)
                }.getOrNull() ?: return@mapNotNull null
                if (payload.size != Payload.SIZE) return@mapNotNull null

                val cam = obj.optString("cam")
                BuiltinPreset(
                    id = obj.optString("id"),
                    name = obj.optString("name").ifBlank { cam },
                    cat = obj.optString("cat").ifBlank { "内置预设" },
                    desc = obj.optString("desc"),
                    cam = cam,
                    payload = payload,
                )
            }
        } catch (e: Exception) {
            emptyList()
        }
    }

    companion object {
        const val ASSET = "builtin_presets.json"
        private const val KEY_HIDDEN = "hidden_ids"
    }
}
