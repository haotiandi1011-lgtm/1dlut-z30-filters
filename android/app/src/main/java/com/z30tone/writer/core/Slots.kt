package com.z30tone.writer.core

/** 槽位常量：0x00C9 = C1 … 0x00D1 = C9。 */
object Slots {
    const val FIRST = 0x00C9
    const val LAST = 0x00D1
    const val RECOMMENDED = LAST // 试写只在 C9

    val ALL: List<Int> = (FIRST..LAST).toList()

    fun label(slot: Int): String {
        val index = slot - FIRST + 1
        return if (index in 1..(LAST - FIRST + 1)) "C$index" else "0x%04X".format(slot)
    }

    fun isValid(slot: Int): Boolean = slot in FIRST..LAST
}
