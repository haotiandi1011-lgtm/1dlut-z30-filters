package com.z30tone.writer.core

import kotlin.math.roundToInt

/**
 * 614 字节 PTP 载荷的解析、构建与对比。
 *
 * 结构（真机回读验证，详见仓库根目录 docs/真机发现-载荷结构.md）：
 *
 *     +0x000   2   版本 02 00
 *     +0x002  20   ASCII 名称（NUL 补齐，实际可用 19 字符）
 *     +0x018   7   参数区（快速锐化 / 锐化 / 中等锐化 / 清晰度 / 对比度 / 亮度 / 饱和度）
 *     +0x02C   1   族标记（实测 0x05 可用）
 *     +0x02F   8   4 组 (x, y) 控制点，只用于机身 LCD 画曲线
 *     +0x066 512   256 × u16 大端 LUT，值域 0..32767  ← 成像真值
 *
 * 手机端只负责「原样搬运 + 改名称/参数」，不重新拟合曲线。
 */
object Payload {

    const val SIZE = 614
    const val LUT_OFFSET = 0x066
    const val LUT_POINTS = 256
    const val LUT_BYTES = 512
    const val LUT_MAX = 32767

    const val NAME_OFFSET = 0x002
    const val NAME_LEN = 20
    const val NAME_MAX = 19

    const val FAMILY_OFFSET = 0x02C
    const val CONTROL_POINTS_OFFSET = 0x02F
    const val CONTROL_POINTS_LEN = 8

    /** 参数区偏移，顺序与机身菜单一致。 */
    val PARAM_OFFSETS: Map<String, Int> = linkedMapOf(
        "quick_sharp" to 0x018,
        "sharp" to 0x019,
        "mid_sharp" to 0x01A,
        "clarity" to 0x01B,
        "contrast" to 0x01C,
        "brightness" to 0x01D,
        "saturation" to 0x01E,
    )

    /** 每个参数一档的步长。真机回读：只有快速锐化是 1/档，其余 0.25/档。 */
    val PARAM_STEPS: Map<String, Float> = mapOf(
        "quick_sharp" to 1.0f,
        "sharp" to 0.25f,
        "mid_sharp" to 0.25f,
        "clarity" to 0.25f,
        "contrast" to 0.25f,
        "brightness" to 0.25f,
        "saturation" to 0.25f,
    )

    val PARAM_LABELS: Map<String, String> = mapOf(
        "quick_sharp" to "快速锐化",
        "sharp" to "锐化",
        "mid_sharp" to "中等锐化",
        "clarity" to "清晰度",
        "contrast" to "对比度",
        "brightness" to "亮度",
        "saturation" to "饱和度",
    )

    /** 有自定义曲线时，机身会禁用对比度与亮度，写了也不生效。 */
    val DISABLED_WITH_CURVE = setOf("contrast", "brightness")

    class Parsed(
        val raw: ByteArray,
        val name: String,
        val family: Int,
        val controlPoints: List<Pair<Int, Int>>,
        val lut: IntArray,
        val params: Map<String, Float>,
    ) {
        val size: Int get() = raw.size
        val first: Int get() = lut.first()
        val last: Int get() = lut.last()
        val peak: Int get() = lut.max()

        val monotonic: Boolean
            get() = (1 until lut.size).all { lut[it] >= lut[it - 1] }

        fun param(key: String): Float = params[key] ?: 0f

        /** 归一化到 0..1，画图用。 */
        fun curve01(): FloatArray = FloatArray(lut.size) { lut[it].toFloat() / LUT_MAX }
    }

    /** 逐字节比对结果。 */
    class Diff(val offsets: List<Int>, val sizeA: Int, val sizeB: Int) {
        val lengthMismatch: Boolean get() = sizeA != sizeB
        val identical: Boolean get() = offsets.isEmpty() && !lengthMismatch

        val summary: String
            get() = when {
                identical -> "读回一致：$sizeA 字节逐字节相同"
                lengthMismatch -> "长度不一致：写入 $sizeA 字节，读回 $sizeB 字节"
                else -> "有 ${offsets.size} 字节不一致：" +
                    offsets.take(8).joinToString(" ") { "0x%02X".format(it) }
            }
    }

    fun parse(raw: ByteArray): Parsed {
        require(raw.size >= SIZE) { "载荷至少 $SIZE 字节，实际 ${raw.size}" }

        val name = StringBuilder()
        for (i in 0 until NAME_LEN) {
            val code = raw[NAME_OFFSET + i].toInt() and 0xFF
            if (code == 0) break
            name.append(if (code in 0x20..0x7E) code.toChar() else '?')
        }

        val lut = IntArray(LUT_POINTS) { i ->
            val offset = LUT_OFFSET + i * 2
            ((raw[offset].toInt() and 0xFF) shl 8) or (raw[offset + 1].toInt() and 0xFF)
        }

        val controlPoints = (0 until CONTROL_POINTS_LEN / 2).map { i ->
            val x = raw[CONTROL_POINTS_OFFSET + i * 2].toInt() and 0xFF
            val y = raw[CONTROL_POINTS_OFFSET + i * 2 + 1].toInt() and 0xFF
            x to y
        }

        val params = PARAM_OFFSETS.mapValues { (key, offset) ->
            decodeParam(raw[offset].toInt() and 0xFF, key)
        }

        return Parsed(
            raw = raw.copyOf(SIZE),
            name = name.toString(),
            family = raw[FAMILY_OFFSET].toInt() and 0xFF,
            controlPoints = controlPoints,
            lut = lut,
            params = params,
        )
    }

    fun encodeParam(value: Float, key: String): Int {
        val step = PARAM_STEPS[key] ?: 0.25f
        return Math.round(value / step) and 0xFF
    }

    fun decodeParam(raw: Int, key: String): Float {
        val signed = if (raw < 128) raw else raw - 256
        val step = PARAM_STEPS[key] ?: 0.25f
        return Math.round(signed * step * 100f) / 100f
    }

    /** 只改参数区，其余字节原样保留。 */
    fun setParams(payload: ByteArray, params: Map<String, Float>): ByteArray {
        val out = payload.copyOf()
        for ((key, offset) in PARAM_OFFSETS) {
            val value = params[key] ?: continue
            out[offset] = encodeParam(value, key).toByte()
        }
        return out
    }

    /** 只改名称，其余字节原样保留。 */
    fun withName(payload: ByteArray, name: String): ByteArray {
        val out = payload.copyOf()
        val clean = sanitizeName(name).toByteArray(Charsets.US_ASCII)
        java.util.Arrays.fill(out, NAME_OFFSET, NAME_OFFSET + NAME_LEN, 0)
        System.arraycopy(clean, 0, out, NAME_OFFSET, minOf(clean.size, NAME_LEN))
        return out
    }

    /**
     * 机身名称只能是 ≤19 字符的 ASCII。中文会被替换成 `?`——这是机身限制，不是本工具的取舍。
     */
    fun sanitizeName(raw: String): String {
        val sb = StringBuilder()
        for (ch in raw.trim()) {
            val code = ch.code
            sb.append(if (code in 0x20..0x7E) ch else '?')
            if (sb.length >= NAME_MAX) break
        }
        return sb.toString().trim().ifEmpty { "Z30FIT" }
    }

    fun diff(a: ByteArray, b: ByteArray): Diff {
        val common = minOf(a.size, b.size)
        val offsets = ArrayList<Int>()
        for (i in 0 until common) {
            if (a[i] != b[i]) offsets.add(i)
        }
        return Diff(offsets, a.size, b.size)
    }

    /**
     * 结构合法的 614 字节骨架（来自真机读回的两条真实配方逐字节比对）。
     * 手机端正常流程用不到——写入的载荷直接来自 `.z30`；这里保留给单测与将来的手调功能。
     */
    fun fallbackTemplate(family: Int = 0x05): ByteArray {
        val p = ByteArray(SIZE)
        p[0x000] = 0x02
        p[0x001] = 0x00
        p[0x019] = 0x08
        p[0x01A] = 0x04
        p[0x023] = 0x01
        p[0x024] = 0x49 // 'I'
        p[0x025] = 0x30 // '0'
        p[0x027] = 0xFF.toByte()
        p[0x029] = 0xFF.toByte()
        p[0x02A] = 0x01
        p[0x02B] = 0x0F
        p[0x02C] = (family and 0xFF).toByte()
        return p
    }

    /** 用 256 点 LUT 组装载荷。模板给定时逐字节复制，只覆盖名称、控制点、LUT 段。 */
    fun buildFromLut(
        lut: IntArray,
        name: String,
        family: Int = 0x05,
        template: ByteArray? = null,
        params: Map<String, Float>? = null,
    ): ByteArray {
        require(lut.size == LUT_POINTS) { "LUT 必须是 $LUT_POINTS 点，实际 ${lut.size}" }
        val base = (template ?: fallbackTemplate(family)).copyOf()
        require(base.size == SIZE) { "模板必须是 $SIZE 字节，实际 ${base.size}" }

        val out = withName(base, name)
        controlPoints(lut).forEachIndexed { i, point ->
            out[CONTROL_POINTS_OFFSET + i * 2] = point.first.toByte()
            out[CONTROL_POINTS_OFFSET + i * 2 + 1] = point.second.toByte()
        }
        for (i in 0 until LUT_POINTS) {
            val v = lut[i].coerceIn(0, LUT_MAX)
            out[LUT_OFFSET + i * 2] = ((v shr 8) and 0xFF).toByte()
            out[LUT_OFFSET + i * 2 + 1] = (v and 0xFF).toByte()
        }
        return if (params.isNullOrEmpty()) out else setParams(out, params)
    }

    /** 给机身 LCD 画图用的 4 组控制点，8 位量化。不影响成像。 */
    fun controlPoints(lut: IntArray, count: Int = 4): List<Pair<Int, Int>> {
        val points = ArrayList<Pair<Int, Int>>(count)
        for (i in 1..count) {
            val x = i.toFloat() / (count + 1)
            val pos = x * (lut.size - 1)
            val i0 = pos.toInt().coerceIn(0, lut.size - 1)
            val i1 = (i0 + 1).coerceAtMost(lut.size - 1)
            val t = pos - i0
            val value = (lut[i0] * (1 - t) + lut[i1] * t) / LUT_MAX
            points.add((x * 255f).roundToInt() to (value * 255f).roundToInt().coerceIn(0, 255))
        }
        return points
    }
}
