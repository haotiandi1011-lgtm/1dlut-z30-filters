"""为安卓端生成两份「对齐数据」，保证手机与电脑拟合出同一条曲线。

一、CanonicalPool.kt
    `z30fit/refimage.py::canonical_pool()` 造的是「典型场景」的亮度分布：
    两张合成图（synth_scene + synth_gradient）的全部像素亮度，排序后缓存。
    但 build_curve 只用它在 513 个等分概率上的分位数（`np.quantile(canon, u)`）。
    所以手机端不需要那两张图、也不需要复刻 numpy 的随机数发生器，只需要这 513 个数。

    这 513 个数值由本脚本从**桌面端同一份代码**里直接取出来固化，精度 10 位小数，
    量化到 15 位码值时误差 < 1e-6 码值，等于没有。

二、fit-golden.txt（单测用）
    用一批可被 Kotlin 逐位复现的合成亮度，跑完整的 分位数匹配 → 单调样条 → 量化，
    把 256 点表与拟合曲线存下来，供安卓端单测逐点比对（不含亮度数组本身，
    Kotlin 侧用同一个公式现算，避免几百 KB 的测试数据）。

用法：
    python tools/gen_android_fit_data.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from z30fit import refimage  # noqa: E402
from z30fit.curve import fit_monotone_curve, quantize  # noqa: E402

OUT_KT = ROOT / "android/app/src/main/java/com/z30tone/writer/core/fit/CanonicalPool.kt"
OUT_GOLDEN = ROOT / "android/app/src/test/resources/fit-golden.txt"

MAX_PTS = 300_000


def canonical_quantiles() -> np.ndarray:
    canon = refimage.canonical_pool()
    u = np.linspace(0.0, 1.0, 513)
    return np.quantile(canon, u)


def write_canonical(x: np.ndarray) -> None:
    # 每行 6 个，避免超长单行；Kotlin 侧按逗号切开解析。
    chunks = []
    for i in range(0, x.size, 6):
        row = ", ".join(f"{v:.10f}" for v in x[i : i + 6])
        chunks.append("            " + row + ",")
    body = "\n".join(chunks)

    text = f'''package com.z30tone.writer.core.fit

/**
 * 「典型场景」亮度分布在 513 个等分概率上的分位数（0..1，升序）。
 *
 * 由 `tools/gen_android_fit_data.py` 从电脑端 `z30fit/refimage.py::canonical_pool()`
 * 直接导出，**不要手改**。手机端拟合只依赖这 513 个数，因此不需要复刻电脑端的
 * 合成测试图，也不需要复刻 numpy 的随机数发生器——两边算的是同一批分位点，
 * 曲线才逐点对得上（实测 256 点最大差 1 个 15 位码值）。
 *
 * 含义：典型场景最暗的那一点点像素亮度定在 values[0]，最亮的定在 values[512]。
 * 拟合时拿它当 x 轴、参考图同分位的亮度当 y 轴，就是分位数匹配。
 */
object CanonicalPool {{

    /** 采样点数，与电脑端 `np.linspace(0, 1, 513)` 一致。 */
    const val SIZE = {x.size}

    private val RAW = """
{body}
""".trimIndent()

    /** 升序分位数，长度 [SIZE]。首次访问时解析，之后复用。 */
    val values: DoubleArray by lazy(LazyThreadSafetyMode.SYNCHRONIZED) {{
        RAW.split(",")
            .map {{ it.trim() }}
            .filter {{ it.isNotEmpty() }}
            .map {{ it.toDouble() }}
            .toDoubleArray()
            .also {{ require(it.size == SIZE) {{ "典型场景分位数应为 $SIZE 个，实际 ${{it.size}}" }} }}
    }}
}}
'''
    OUT_KT.parent.mkdir(parents=True, exist_ok=True)
    OUT_KT.write_text(text, encoding="utf-8")
    print(f"写出 {OUT_KT.relative_to(ROOT)}  ({x.size} 个分位数)")


# --------------------------------------------------------------------------
# 单测金标准
# --------------------------------------------------------------------------
def synth_luma(w: int, h: int) -> np.ndarray:
    """可被 Kotlin 逐位复现的合成亮度（纯整数运算，无随机数）。

    与 Kotlin 侧 `GoldenImages.luma()` 必须完全一致，改动要两边一起改。
    """
    ys, xs = np.mgrid[0:h, 0:w]
    v = (xs * 37 + ys * 91 + (xs * ys) % 53) % 256
    r = v
    g = (v * 3) % 256
    b = (v * 7 + 11) % 256
    return ((r * 0.2126 + g * 0.7152 + b * 0.0722) / 255.0).ravel()


def subsample_desc(lum: np.ndarray) -> np.ndarray:
    """与电脑端 load_luma 相同：先等步长抽样，再排序。"""
    step = max(1, lum.size // MAX_PTS)
    return np.sort(lum[::step])


def golden_case(name: str, ref_sorted: np.ndarray, knots: int = 33) -> str:
    canon = canonical_quantiles()
    u = np.linspace(0.0, 1.0, canon.size)
    x, y = canon.copy(), np.quantile(ref_sorted, u)

    keep = np.concatenate([[True], np.diff(x) > 1e-6])
    x, y = x[keep], y[keep]

    curve = fit_monotone_curve(
        x, y, np.ones_like(x), n_knots=knots, pin_endpoints=True, endpoint_weight=10.0
    )
    grid = np.linspace(0.0, 1.0, 256)
    values = np.clip(curve(grid), 0.0, 1.0)
    table = quantize(values)

    return "\n".join(
        [
            f"case {name}",
            "table " + " ".join(str(int(v)) for v in table),
            "fit01 " + " ".join(f"{v:.9f}" for v in values),
        ]
    )


def write_golden() -> None:
    lum = synth_luma(97, 61)
    cases = [golden_case("normal", subsample_desc(lum))]

    # 暗部被压死：一半像素贴黑，用来验证黑位守卫不过度抬黑
    crushed = np.clip((lum - 0.35) * 2.2, 0.0, 1.0) ** 0.85
    crushed[: int(crushed.size * 0.5)] = 0.0
    cases.append(golden_case("crushed", subsample_desc(crushed)))

    # 低反差褪色片：整段挤在中间
    cases.append(golden_case("faded", subsample_desc(0.30 + 0.45 * lum)))

    text = "\n\n".join(cases) + "\n"
    OUT_GOLDEN.parent.mkdir(parents=True, exist_ok=True)
    # 强制 LF：Windows 下 write_text 默认会把 \n 翻成 \r\n，安卓端单测按空行切块会切错
    with OUT_GOLDEN.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    print(f"写出 {OUT_GOLDEN.relative_to(ROOT)}（{len(cases)} 组金标准）")


if __name__ == "__main__":
    write_canonical(canonical_quantiles())
    write_golden()
