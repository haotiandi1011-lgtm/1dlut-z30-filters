"""成对图 → 影调曲线。

和单图模式的区别：这里的「输入」是用户自己的原图，不是假设的典型场景。
所以输入输出都是已知的，能算出**真正的 ΔE2000**。

做法：
  1. 两张图缩到同一尺寸，用 FFT 互相关估计平移量并对齐（手持拍摄几乎不可能完全对齐）
  2. 分位数匹配得到单调曲线（x = 原图第 u 分位，y = 目标图第 u 分位）
  3. 把曲线作用到原图上，和目标图逐像素比 ΔE —— 这个数字是真的

注意：曲线只能搬走影调。目标图里的偏色、去饱和、分离色调搬不走，
这部分会体现在 ΔE 里，报告里要如实说。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from . import color
from .curve import banding_analysis, fit_monotone_curve, quantize
from .metrics import verdict

MAX_SIDE = 720


def _load(path: str | Path, size: tuple[int, int] | None = None) -> np.ndarray:
    from PIL import Image

    im = Image.open(path).convert("RGB")
    if size:
        im = im.resize(size, Image.LANCZOS)
    elif max(im.size) > MAX_SIDE:
        s = MAX_SIDE / max(im.size)
        im = im.resize((max(1, int(im.width * s)), max(1, int(im.height * s))), Image.LANCZOS)
    return np.asarray(im).astype(np.float64) / 255.0


def estimate_shift(a: np.ndarray, b: np.ndarray) -> tuple[int, int]:
    """估计 b 相对 a 的平移量，返回需要 roll b 的 (dy, dx)。"""
    la = a @ color.LUMA_REC709
    lb = b @ color.LUMA_REC709
    la = la - la.mean()
    lb = lb - lb.mean()

    cc = np.fft.irfft2(np.fft.rfft2(la) * np.conj(np.fft.rfft2(lb)), s=la.shape)
    idx = np.unravel_index(int(np.argmax(cc)), cc.shape)
    dy, dx = int(idx[0]), int(idx[1])
    if dy > la.shape[0] // 2:
        dy -= la.shape[0]
    if dx > la.shape[1] // 2:
        dx -= la.shape[1]

    # 两个方向都试一下，取残差小的那个（互相关的符号约定容易搞反）
    best = (dy, dx)
    best_err = np.inf
    for cand in ((dy, dx), (-dy, -dx)):
        rolled = np.roll(np.roll(lb, cand[0], axis=0), cand[1], axis=1)
        err = float(np.mean(np.abs(rolled - la)))
        if err < best_err:
            best, best_err = cand, err
    return best


def _quantile_curve(src_luma: np.ndarray, dst_luma: np.ndarray, knots: int = 33):
    u = np.linspace(0.0, 1.0, 513)
    x = np.quantile(src_luma, u)
    y = np.quantile(dst_luma, u)
    keep = np.concatenate([[True], np.diff(x) > 1e-6])
    x, y = x[keep], y[keep]
    return fit_monotone_curve(x, y, np.ones_like(x), n_knots=knots,
                              pin_endpoints=True, endpoint_weight=10.0)


def pair_fit(src_path: str | Path, dst_path: str | Path, knots: int = 33) -> dict:
    src = _load(src_path)
    dst = _load(dst_path, size=(src.shape[1], src.shape[0]))

    dy, dx = estimate_shift(src, dst)
    dst_aligned = np.roll(np.roll(dst, dy, axis=0), dx, axis=1)

    # 裁掉卷绕带
    h, w = src.shape[:2]
    y0, y1 = max(0, dy), h - max(0, -dy)
    x0, x1 = max(0, dx), w - max(0, -dx)
    m = 4
    s_crop = src[y0 + m : y1 - m, x0 + m : x1 - m]
    d_crop = dst_aligned[y0 + m : y1 - m, x0 + m : x1 - m]
    if s_crop.size < 1000:  # 位移太大，退化成不裁剪
        s_crop, d_crop = src, dst_aligned

    curve = _quantile_curve((s_crop @ color.LUMA_REC709).ravel(),
                            (d_crop @ color.LUMA_REC709).ravel(), knots=knots)

    grid = np.linspace(0.0, 1.0, 256)
    values = np.clip(curve(grid), 0.0, 1.0)
    table = quantize(values)
    values_q = table.astype(np.float64) / 32767.0

    pred = np.clip(np.interp(s_crop, grid, values), 0, 1)
    de = color.delta_e_2000(color.rgb_to_lab(pred), color.rgb_to_lab(d_crop))
    stats = color.delta_e_stats(color.rgb_to_lab(pred), color.rgb_to_lab(d_crop))

    rmse_c = np.sqrt(np.mean((pred - d_crop) ** 2, axis=(0, 1))) * 255.0

    # 只保留影调能解释的部分：如果目标图有偏色，ΔE 会高，那是先天限制
    before = color.delta_e_stats(color.rgb_to_lab(s_crop), color.rgb_to_lab(d_crop))

    m = dict(stats)
    m.update({
        "rmse": float(np.sqrt(np.mean((pred - d_crop) ** 2)) * 255.0),
        "rmse_r": float(rmse_c[0]), "rmse_g": float(rmse_c[1]), "rmse_b": float(rmse_c[2]),
        "gray_dev": 0.0,
        "de_neutral_max": float(np.percentile(de, 99)),
        "points": int(s_crop.shape[0] * s_crop.shape[1]),
        "before_mean": before["mean"],
        "shift": [int(dy), int(dx)],
    })

    band = banding_analysis(table, np.interp(grid, grid, values))
    tone = {
        "black": float(values_q[0]), "white": float(values_q[-1]),
        "mid_18": float(np.interp(0.18, grid, values_q)),
        "contrast_25_75": float((np.interp(0.75, grid, values_q) - np.interp(0.25, grid, values_q)) / 0.5),
        "slope_toe": float(values_q[8] - values_q[0]) * 12.5,
        "slope_mid": float(values_q[136] - values_q[120]) * 16.0,
        "slope_shoulder": float(values_q[-1] - values_q[-9]) * 12.5,
    }

    return {
        "curve": curve, "table": table, "values": values_q, "metrics": m,
        "tone": tone, "banding": band, "shift": (dy, dx),
        "preview": {"src": s_crop, "dst": d_crop, "pred": pred},
        "advice": _advice(m),
    }


def _advice(m: dict) -> dict:
    mean, p95 = m["mean"], m["p95"]
    if mean < 1.5 and p95 < 4.0:
        return {"level": "ok", "text": "高度一致：目标图的调子基本能搬进机身"}
    if mean < 3.0:
        return {"level": "caution",
                "text": f"整体一致，但有 {100 - m['within_2'] * 100:.0f}% 的像素偏差超过 2。"
                        "多半是目标图里的偏色/饱和度，1D 曲线搬不走"}
    return {"level": "no",
            "text": "偏差较大：目标图的风格主要靠色彩层（偏色、去饱和、分离色调），"
                    "单曲线只能搬走影调"}


def render_pair_preview(res: dict, outdir: str | Path) -> str:
    from PIL import Image

    from .images import _font, diff_panel, strip

    outdir = Path(outdir)
    p = res["preview"]
    panels = [
        ("原图", p["src"]),
        ("目标图", p["dst"]),
        ("曲线作用在原图上", p["pred"]),
        ("差异 ×8", diff_panel(p["pred"], p["dst"], 8.0)),
    ]
    name = strip(outdir / "pair_compare.png", panels,
                 title="成对图：原图 → 目标图 的影调迁移")
    return name.name
