package com.z30tone.writer

import com.z30tone.writer.core.Payload
import com.z30tone.writer.core.Recipe
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class RecipeTest {

    private fun payload(): ByteArray =
        Payload.buildFromLut(IntArray(Payload.LUT_POINTS) { it * 128 }, "RECIPE")

    private val metaJson = """
        {
          "name": "测试配方",
          "source": "sample.cube",
          "note": "合成样本",
          "metrics": {"mean": 1.23, "p95": 3.4, "max": 6.1},
          "tone": {"black": 0.01, "white": 0.98},
          "created": "2026-10-31 17:00:00",
          "tool": "Z30影调工具 0.1"
        }
    """.trimIndent()

    @Test
    fun buildAndParseRoundTrip() {
        val bytes = Recipe.build(payload(), metaJson)
        assertEquals(Recipe.HEADER + metaJson.toByteArray(Charsets.UTF_8).size + Payload.SIZE, bytes.size)

        val packageData = Recipe.parse(bytes)
        assertEquals(Payload.SIZE, packageData.payload.size)
        assertEquals("测试配方", packageData.meta.name)
        assertEquals("sample.cube", packageData.meta.source)
        assertEquals(1.23, packageData.meta.deltaEAvg!!, 0.0001)
        assertEquals(3.4, packageData.meta.deltaEP95!!, 0.0001)
        assertEquals(0.98, packageData.meta.tone["white"]!!, 0.0001)
    }

    @Test
    fun payloadSurvivesByteForByte() {
        val original = payload()
        val parsed = Recipe.parse(Recipe.build(original, metaJson))
        assertTrue(Payload.diff(original, parsed.payload).identical)
    }

    @Test
    fun brokenMetaDoesNotBreakPayload() {
        val bytes = Recipe.build(payload(), "{ 这不是 JSON ")
        val packageData = Recipe.parse(bytes)
        assertEquals(Payload.SIZE, packageData.payload.size)
        assertNull(packageData.meta.name)
    }

    @Test
    fun emptyMetaIsAllowed() {
        val packageData = Recipe.parse(Recipe.build(payload(), ""))
        assertEquals(Payload.SIZE, packageData.payload.size)
        assertNull(packageData.meta.name)
    }

    @Test
    fun matchesDetectsMagic() {
        assertTrue(Recipe.matches(Recipe.build(payload(), "{}")))
        assertFalse(Recipe.matches(ByteArray(700)))
        assertFalse(Recipe.matches(ByteArray(Payload.SIZE)))
    }

    @Test(expected = IllegalArgumentException::class)
    fun rejectsForeignFile() {
        Recipe.parse(ByteArray(700))
    }

    @Test(expected = IllegalArgumentException::class)
    fun rejectsTruncatedFile() {
        Recipe.parse(ByteArray(100))
    }

    @Test(expected = IllegalArgumentException::class)
    fun rejectsAbsurdMetaLength() {
        val bytes = Recipe.build(payload(), "{}")
        // 把元数据长度改成 0x7FFFFFFF，解析必须拒绝而不是越界
        bytes[8] = 0xFF.toByte()
        bytes[9] = 0xFF.toByte()
        bytes[10] = 0xFF.toByte()
        bytes[11] = 0x7F
        Recipe.parse(bytes)
    }
}
