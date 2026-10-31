"""偏差指标：ΔE2000、逐通道 RMSE、中性轴偏差。"""

from __future__ import annotations

import numpy as np

from . import color
from .cube import CubeLUT
from .curve import MonotoneSpline


_PHOTO_CACHE: np.ndarray | None = None


def photo_color_cloud(max_pts: int = 4000, seed: int = 0) -> np.ndarray:
    """从合成测试图上抽一批像素，作为「接近真实照片」的颜色分布。

    为什么不用均匀 RGB 立方：均匀网格里高饱和角点占比过大，会奖励那些平均误差
    好看但把灰阶搞歪的拟合（实测会把纯黑抬到 6% 以上）。照片的颜色分布天然偏向
    低饱和和中间调，用它当评测集更接近肉眼判断。
    """
    global _PHOTO_CACHE
    if _PHOTO_CACHE is None:
        from .images import synth_chart, synth_gradient, synth_scene

        pixels = np.concatenate(
            [im.reshape(-1, 3) for im in (synth_chart(), synth_scene(), synth_gradient())], axis=0
        )
        rng = np.random.default_rng(seed)
        idx = rng.choice(pixels.shape[0], size=min(max_pts, pixels.shape[0]), replace=False)
        _PHOTO_CACHE = pixels[np.sort(idx)]
    return _PHOTO_CACHE


def evaluation_points(lut: CubeLUT, max_nodes: int = 1728) -> np.ndarray:
    """构造评测点集。

    组成（约 5900 点）：
      · 4000 点 合成照片像素 —— 反映真实观感，占主导
      · 12³ 均匀网格       —— 保证高饱和区域也被覆盖
      · 128 点中性灰轴     —— 灰阶是硬指标
    """
    photo = photo_color_cloud(4000)

    g = np.linspace(0.0, 1.0, 12)
    b, gg, r = np.meshgrid(g, g, g, indexing="ij")
    grid = np.stack([r.ravel(), gg.ravel(), b.ravel()], axis=-1)[:max_nodes]

    ramp = np.linspace(0.0, 1.0, 128)
    neutral = np.stack([ramp, ramp, ramp], axis=-1)

    return np.concatenate([photo, grid, neutral], axis=0), np.concatenate(
        [np.zeros(photo.shape[0], bool), np.zeros(grid.shape[0], bool), np.ones(neutral.shape[0], bool)]
    )


def evaluate(lut: CubeLUT, curve: MonotoneSpline, points: tuple | None = None) -> dict:
    """把「参考 LUT」和「拟合出的 1D 曲线」在评测点上对比。

    预测方式就是机身真实行为：同一条曲线分别作用到 R/G/B 三个通道。
    """
    if points is None:
        points = evaluation_points(lut)
    pts, is_neutral = points
    pts = np.asarray(pts, dtype=np.float64)

    ref = lut.apply(pts)
    pred = np.clip(curve(pts), 0.0, 1.0)

    # 灰阶是硬指标：中性点的色差按 4 倍计入，否则「黑位抬到 12%」这种
    # 明显问题会因为灰点在照片里占比小而躲过评分。
    weights = np.where(is_neutral, 4.0, 1.0)

    lab_ref = color.rgb_to_lab(ref)
    lab_pred = color.rgb_to_lab(pred)
    de = color.delta_e_2000(lab_ref, lab_pred)
    stats = color.delta_e_stats(lab_ref, lab_pred, weights)

    rmse_c = np.sqrt(np.mean((pred - ref) ** 2, axis=0)) * 255.0
    rmse = float(np.sqrt(np.mean((pred - ref) ** 2)) * 255.0)
    de_neutral = de[is_neutral] if np.any(is_neutral) else np.array([0.0])

    # 中性轴偏离量（输出单位）：曲线在灰轴上的值与参考灰轴之差的平均绝对值。
    # 这是相机直出的硬指标——灰阶歪了，整张片子就废了。
    ramp = np.linspace(0.0, 1.0, 256)
    neutral_in = np.stack([ramp, ramp, ramp], axis=-1)
    ref_neutral = color.luma(lut.apply(neutral_in))
    gray_dev = float(np.mean(np.abs(curve(ramp) - ref_neutral)))

    out = dict(stats)
    out.update(
        {
            "rmse": rmse,
            "rmse_r": float(rmse_c[0]),
            "rmse_g": float(rmse_c[1]),
            "rmse_b": float(rmse_c[2]),
            "de_neutral_mean": float(np.mean(de_neutral)),
            "de_neutral_max": float(np.max(de_neutral)),
            "gray_dev": gray_dev,
            "points": int(pts.shape[0]),
        }
    )
    return out


def tone_stats(table_0_1: np.ndarray) -> dict:
    """曲线的可读统计：黑位、白位、中间调对比、端点斜率。"""
    c = np.asarray(table_0_1, dtype=np.float64)
    x = np.linspace(0.0, 1.0, c.size)

    black = float(c[0])
    white = float(c[-1])
    mid = float(np.interp(0.18, x, c))
    p25 = float(np.interp(0.25, x, c))
    p75 = float(np.interp(0.75, x, c))
    contrast = (p75 - p25) / 0.5  # 1.0 = 恒等

    # 端点斜率（用首尾 5% 段的最小二乘斜率）
    def seg_slope(lo: float, hi: float) -> float:
        m = (x >= lo) & (x <= hi)
        if np.count_nonzero(m) < 2:
            return float("nan")
        return float(np.polyfit(x[m], c[m], 1)[0])

    return {
        "black": black,
        "white": white,
        "mid_18": mid,
        "contrast_25_75": contrast,
        "slope_toe": seg_slope(0.0, 0.05),
        "slope_shoulder": seg_slope(0.95, 1.0),
        "slope_mid": seg_slope(0.45, 0.55),
    }


def verdict(metrics: dict) -> str:
    """把指标翻译成人话（对外口径：近似 + 偏差报告，不承诺 1:1）。"""
    mean = metrics["mean"]
    p95 = metrics["p95"]
    if mean < 1.0 and p95 < 3.0:
        return "高度一致：肉眼几乎无法区分，可视为等效还原"
    if mean < 2.0 and p95 < 6.0:
        return "良好近似：整体观感一致，个别高饱和色会有可见偏差"
    if mean < 4.0:
        return "可接受：影调走向正确，颜色倾向只对了一部分"
    return "偏差较大：这个风格依赖分色相调整，1D LUT 表达不了主要特征"
