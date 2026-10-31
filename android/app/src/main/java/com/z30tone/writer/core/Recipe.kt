package com.z30tone.writer.core

import org.json.JSONObject

/**
 * 配方包 `.z30` 的读取与写出。
 *
 * 格式（小端）：
 *
 *     +0x00   8   魔数 b"Z30PC1\0\0"
 *     +0x08   4   元数据长度 u32
 *     +0x0C   n   元数据 JSON（UTF-8）
 *     ...   614  载荷本体
 *
 * 设计原则：**载荷是唯一真值**，元数据只用于展示。JSON 坏掉也不影响写入。
 */
object Recipe {

    private val MAGIC = byteArrayOf(0x5A, 0x33, 0x30, 0x50, 0x43, 0x31, 0x00, 0x00) // "Z30PC1\0\0"
    const val HEADER = 12
    const val SUFFIX = ".z30"

    class Meta(
        val name: String?,
        val source: String?,
        val note: String?,
        val created: String?,
        val tool: String?,
        val metrics: Map<String, Double>,
        val tone: Map<String, Double>,
    ) {
        /** 电脑端写入的键是 mean / p95 / max。 */
        val deltaEAvg: Double? get() = metrics["mean"] ?: metrics["delta_e_avg"]
        val deltaEP95: Double? get() = metrics["p95"]

        companion object {
            val EMPTY = Meta(null, null, null, null, null, emptyMap(), emptyMap())
        }
    }

    class Package(val payload: ByteArray, val meta: Meta)

    /** 文件是否像一份配方包（长度与魔数都对得上）。 */
    fun matches(raw: ByteArray): Boolean =
        raw.size >= HEADER + Payload.SIZE && MAGIC.indices.all { raw[it] == MAGIC[it] }

    fun parse(raw: ByteArray): Package {
        if (raw.size < HEADER + Payload.SIZE) {
            throw IllegalArgumentException("文件太小（${raw.size} 字节），不是有效的配方包")
        }
        if (!MAGIC.indices.all { raw[it] == MAGIC[it] }) {
            throw IllegalArgumentException("文件头不匹配，不是 .z30 配方包")
        }
        val metaLength = readIntLe(raw, 8)
        // 用「剩余可用字节」做上界，避免 HEADER + metaLength + SIZE 整数溢出后绕过校验。
        val maxMetaLength = raw.size - HEADER - Payload.SIZE
        if (metaLength < 0 || metaLength > maxMetaLength) {
            throw IllegalArgumentException("配方包的元数据长度异常（$metaLength）")
        }
        val payload = raw.copyOfRange(HEADER + metaLength, HEADER + metaLength + Payload.SIZE)
        val metaText = String(raw, HEADER, metaLength, Charsets.UTF_8)
        return Package(payload, parseMeta(metaText))
    }

    fun build(payload: ByteArray, metaJson: String): ByteArray {
        require(payload.size == Payload.SIZE) { "载荷必须是 ${Payload.SIZE} 字节" }
        val meta = metaJson.toByteArray(Charsets.UTF_8)
        val out = ByteArray(HEADER + meta.size + payload.size)
        System.arraycopy(MAGIC, 0, out, 0, MAGIC.size)
        writeIntLe(out, 8, meta.size)
        System.arraycopy(meta, 0, out, HEADER, meta.size)
        System.arraycopy(payload, 0, out, HEADER + meta.size, payload.size)
        return out
    }

    private fun parseMeta(text: String): Meta {
        if (text.isBlank()) return Meta.EMPTY
        return try {
            val json = JSONObject(text)
            Meta(
                name = json.optString("name").ifBlank { null },
                source = json.optString("source").ifBlank { null },
                note = json.optString("note").ifBlank { null },
                created = json.optString("created").ifBlank { null },
                tool = json.optString("tool").ifBlank { null },
                metrics = numbers(json.optJSONObject("metrics")),
                tone = numbers(json.optJSONObject("tone")),
            )
        } catch (e: Exception) {
            // 元数据只是备注，坏了不影响写入。
            Meta.EMPTY
        }
    }

    private fun numbers(json: JSONObject?): Map<String, Double> {
        if (json == null) return emptyMap()
        val out = LinkedHashMap<String, Double>()
        val keys = json.keys()
        while (keys.hasNext()) {
            val key = keys.next()
            val value = json.optDouble(key, Double.NaN)
            if (!value.isNaN()) out[key] = value
        }
        return out
    }

    private fun readIntLe(src: ByteArray, offset: Int): Int =
        (src[offset].toInt() and 0xFF) or
            ((src[offset + 1].toInt() and 0xFF) shl 8) or
            ((src[offset + 2].toInt() and 0xFF) shl 16) or
            ((src[offset + 3].toInt() and 0xFF) shl 24)

    private fun writeIntLe(dst: ByteArray, offset: Int, value: Int) {
        dst[offset] = (value and 0xFF).toByte()
        dst[offset + 1] = ((value shr 8) and 0xFF).toByte()
        dst[offset + 2] = ((value shr 16) and 0xFF).toByte()
        dst[offset + 3] = ((value shr 24) and 0xFF).toByte()
    }
}
