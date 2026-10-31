"""生成测试用 .cube 样本。

工作区里没有真实可用的 .cube，先用公式造几组：
    1D 样本：验证「管线本身不引入误差」
    3D 样本：验证「3D 风格被压成 1D 后还剩多少」
    其中一个故意做成强分离色调，用来确认工具会诚实地报出高偏差。

用法：python make_samples.py
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from z30fit.cube import write_cube_1d
from z30fit.color import LUMA_REC709

OUT = Path(__file__).parent / "samples"


def _luma(rgb: np.ndarray) -> np.ndarray:
    return rgb @ LUMA_REC709


def _grid(n: int) -> np.ndarray:
    g = np.linspace(0.0, 1.0, n)
    b, gg, r = np.meshgrid(g, g, g, indexing="ij")
    return np.stack([r, gg, b], axis=-1)


def _write_cube_3d(path: Path, data: np.ndarray, title: str, size: int) -> None:
    lines = [f'TITLE "{title}"', "", f"LUT_3D_SIZE {size}", "DOMAIN_MIN 0.0 0.0 0.0", "DOMAIN_MAX 1.0 1.0 1.0", ""]
    flat = data.reshape(-1, 3)
    for r, g, b in flat:
        lines.append(f"{min(max(r, 0.0), 1.0):.6f} {min(max(g, 0.0), 1.0):.6f} {min(max(b, 0.0), 1.0):.6f}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------
# 1D 样本：曲线本身
# --------------------------------------------------------------------------
def s_curve(x: np.ndarray, strength: float = 6.0, lift: float = 0.0, roll: float = 0.0) -> np.ndarray:
    s = 1.0 / (1.0 + np.exp(-strength * (x - 0.5)))
    s = (s - s[0]) / (s[-1] - s[0])
    return np.clip(s * (1.0 - roll) + lift * (1.0 - s), 0.0, 1.0)


def make_punch_s() -> Path:
    x = np.linspace(0, 1, 65)
    y = s_curve(x, strength=5.2)
    y = 0.006 + 0.986 * y  # 极轻抬黑 + 压白
    data = np.stack([y, y, y], axis=-1)
    p = OUT / "punch_s_1d.cube"
    write_cube_1d(p, data, title="Punch S (1D 合成样本)")
    return p


def make_fade_1d() -> Path:
    x = np.linspace(0, 1, 65)
    y = 0.055 + 0.90 * x**1.05  # 抬黑压白，低反差褪色
    data = np.stack([y, y, y], axis=-1)
    p = OUT / "fade_1d.cube"
    write_cube_1d(p, data, title="Faded (1D 合成样本)")
    return p


# --------------------------------------------------------------------------
# 3D 样本
# --------------------------------------------------------------------------
def make_warm_soft() -> Path:
    """柔和暖调：低反差 + 轻微去饱和 + 高光偏暖。曲线主导，1D 应该还原得不错。"""
    n = 17
    rgb = _grid(n)
    lum = _luma(rgb)[..., None]
    out = rgb * 0.88 + lum * 0.12
    out = 0.012 + 0.975 * out**0.94
    out = out * np.array([1.025, 1.000, 0.962])
    out = 1.0 - np.clip(1.0 - np.clip(out, 0, 1), 0.0, None) ** 1.06
    p = OUT / "warm_soft_3d.cube"
    _write_cube_3d(p, np.clip(out, 0, 1), "Warm Soft (3D 合成样本)", n)
    return p


def make_fade_matte() -> Path:
    """哑光褪色：抬黑 + 压白 + 降饱和 + 阴影偏青。"""
    n = 17
    rgb = _grid(n)
    lum = _luma(rgb)[..., None]
    out = rgb * 0.55 + lum * 0.45
    out = 0.048 + 0.90 * out
    out = 0.30 * out + 0.70 * (out**1.02)
    out = out + (1.0 - out) * np.array([-0.012, 0.004, 0.022])
    p = OUT / "fade_matte_3d.cube"
    _write_cube_3d(p, np.clip(out, 0, 1), "Fade Matte (3D 合成样本)", n)
    return p


def make_teal_orange() -> Path:
    """强分离色调：阴影压青、高光推橙，并且 R/G/B 曲线斜率不同。

    这是 1D LUT 的死角，工具必须报出高偏差，否则就是指标算错了。
    """
    n = 17
    rgb = _grid(n)
    lum = _luma(rgb)[..., None]
    out = rgb.copy()
    out[..., 0] = np.clip(rgb[..., 0] ** 0.86 * 1.05, 0, 1)   # 红通道更陡
    out[..., 1] = np.clip(rgb[..., 1] ** 0.98, 0, 1)
    out[..., 2] = np.clip(rgb[..., 2] ** 1.12 * 0.92, 0, 1)   # 蓝通道更缓
    shadows = (1.0 - lum) ** 1.6
    highs = lum**1.4
    out = out + shadows * np.array([-0.035, 0.020, 0.070]) + highs * np.array([0.075, 0.010, -0.055])
    p = OUT / "teal_orange_3d.cube"
    _write_cube_3d(p, np.clip(out, 0, 1), "Teal & Orange (3D 合成样本)", n)
    return p


def make_vivid_red() -> Path:
    """中等强度：反差 + 红黄偏暖 + 高饱和。介于前两者之间。"""
    n = 17
    rgb = _grid(n)
    lum = _luma(rgb)[..., None]
    out = rgb * 0.80 + lum * 0.20
    out = np.clip((out - 0.5) * 1.12 + 0.48, 0, 1)
    out = out**0.96
    out = out * np.array([1.04, 1.005, 0.945])
    out = 1.0 - np.clip(1.0 - np.clip(out, 0, 1), 0.0, None) ** 1.04
    p = OUT / "vivid_red_3d.cube"
    _write_cube_3d(p, np.clip(out, 0, 1), "Vivid Red-Yellow (3D 合成样本)", n)
    return p


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    made = [
        make_punch_s(),
        make_fade_1d(),
        make_warm_soft(),
        make_fade_matte(),
        make_vivid_red(),
        make_teal_orange(),
    ]
    for p in made:
        print(f"  {p.name:24s} {p.stat().st_size / 1024:6.1f} KB")


if __name__ == "__main__":
    main()
