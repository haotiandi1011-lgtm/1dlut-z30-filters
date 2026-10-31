"""单张参考图 → 影调曲线。

**这里没有真值。** 一张成品图里混着原场景、光照、相机原有调色和后期，反推不出
唯一的「输入→输出」映射。所以这条路做的是**影调分布迁移**：

    把参考图的亮度分布，映射到「典型场景」的亮度分布上，得到一条单调曲线。

含义是：如果你的场景和典型场景差不多，套上这条曲线，影调分布会接近参考图。
它不等于「把参考图还原出来」，所以界面里给的数字叫「影调贴合误差（灰阶）」，不叫 ΔE。

要真正的还原度，必须用**成对图**（同一场景的原始版 + 目标版），那才有真值。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from . import color
from .curve import MonotoneSpline, banding_analysis, fit_monotone_curve, quantize
from .metrics import verdict

_CANON: np.ndarray | None = None


def canonical_pool(max_pts: int = 120_000) -> np.ndarray:
    """「典型场景」的亮度分布，用合成测试图当代理（缓存一次）。"""
    global _CANON
    if _CANON is None:
        from .images import synth_gradient, synth_scene

        # 只用连续影调的两张。色卡是大片同值色块，混进来会在分布上砸出一堆尖峰，
        # 拟合出来的曲线会跟着抖。
        px = np.concatenate([im.reshape(-1, 3) for im in (synth_scene(), synth_gradient())])
        lum = px @ color.LUMA_REC709
        step = max(1, lum.size // max_pts)
        _CANON = np.sort(lum[::step])
    return _CANON


def load_luma(path: str | Path, max_pts: int = 300_000) -> tuple[np.ndarray, tuple[int, int]]:
    """读图 → 排序后的亮度数组（0..1）+ 原始尺寸。"""
    from PIL import Image

    im = Image.open(path).convert("RGB")
    size = im.size
    if size[0] * size[1] > 12_000_000:
        im.thumbnail((3000, 3000))
    a = np.asarray(im).astype(np.float64) / 255.0
    lum = (a @ color.LUMA_REC709).ravel()
    step = max(1, lum.size // max_pts)
    return np.sort(lum[::step]), size


def build_curve(ref_sorted: np.ndarray, knots: int = 33) -> tuple[MonotoneSpline, dict]:
    """分位数匹配：x = 典型场景的分位数，y = 参考图同分位的亮度。"""
    canon = canonical_pool()
    u = np.linspace(0.0, 1.0, 513)
    x = np.quantile(canon, u)
    y = np.quantile(ref_sorted, u)

    # 去掉重复的 x（典型场景在 0 和 1 附近会堆叠）
    keep = np.concatenate([[True], np.diff(x) > 1e-6])
    x, y = x[keep], y[keep]

    curve = fit_monotone_curve(
        x, y, np.ones_like(x), n_knots=knots, pin_endpoints=True, endpoint_weight=10.0
    )

    grid = np.linspace(0.0, 1.0, 256)
    target = np.interp(grid, x, y)
    fitted = np.clip(curve(grid), 0.0, 1.0)
    dev = np.abs(fitted - target) * 255.0

    stats = {
        "tone_match_mean": float(dev.mean()),
        "tone_match_max": float(dev.max()),
        "anchors": {f"p{p}": float(np.quantile(ref_sorted, p / 100)) for p in (1, 5, 25, 50, 75, 95, 99)},
        "clip_low": float(np.mean(ref_sorted <= 0.003)),
        "clip_high": float(np.mean(ref_sorted >= 0.997)),
        "source_p": {f"p{p}": float(np.quantile(canon, p / 100)) for p in (5, 25, 50, 75, 95)},
    }
    return curve, stats


def image_fit(path: str | Path, knots: int = 33) -> dict:
    """完整流程，返回给前端用的字典（同时含 256 点表与统计）。"""
    ref, size = load_luma(path)
    curve, stats = build_curve(ref, knots=knots)

    grid = np.linspace(0.0, 1.0, 256)
    values = np.clip(curve(grid), 0.0, 1.0)
    table = quantize(values)
    values_q = table.astype(np.float64) / 32767.0

    band = banding_analysis(table, np.interp(grid, grid, values))

    # 界面统一用 metrics 结构，但 image 模式下这些数字不代表 ΔE
    m = {
        "mean": stats["tone_match_mean"],
        "median": stats["tone_match_mean"],
        "p95": stats["tone_match_max"],
        "max": stats["tone_match_max"],
        "within_1": 0.0,
        "within_2": 0.0,
        "within_3": 0.0,
        "rmse": stats["tone_match_mean"],
        "rmse_r": stats["tone_match_mean"],
        "rmse_g": stats["tone_match_mean"],
        "rmse_b": stats["tone_match_mean"],
        "gray_dev": 0.0,
        "de_neutral_max": stats["tone_match_max"],
        "points": int(ref.size),
    }

    tone = {
        "black": float(values_q[0]),
        "white": float(values_q[-1]),
        "mid_18": float(np.interp(0.18, grid, values_q)),
        "contrast_25_75": float((np.interp(0.75, grid, values_q) - np.interp(0.25, grid, values_q)) / 0.5),
        "slope_toe": float(values_q[8] - values_q[0]) * 12.5,
        "slope_mid": float(values_q[136] - values_q[120]) * 16.0,
        "slope_shoulder": float(values_q[-1] - values_q[-9]) * 12.5,
    }

    return {
        "curve": curve,
        "table": table,
        "values": values_q,
        "metrics": m,
        "tone": tone,
        "banding": band,
        "stats": stats,
        "size": size,
        "advice": _advice(stats, band),
    }


def _advice(stats: dict, band: dict) -> dict:
    clip = max(stats["clip_low"], stats["clip_high"])
    if not band["monotone"]:
        return {"level": "no", "text": "曲线非单调，不能写入"}
    if clip > 0.06:
        return {
            "level": "caution",
            "text": f"参考图有 {clip * 100:.0f}% 的像素已经压到纯黑或纯白，曲线会把这一段压平；"
                    "建议换一张层次更完整的参考图",
        }
    if stats["tone_match_mean"] > 2.0:
        return {"level": "caution", "text": "曲线对参考影调的贴合偏差偏大，写进去可能不够准"}
    return {"level": "ok", "text": "影调贴合良好。注意：单图模式只能迁移影调，色彩个性会丢失"}


def render_compare(ref_path: str | Path, values: np.ndarray, outdir: str | Path) -> dict:
    """参考图 + 曲线作用在标准场景 + 两条亮度直方图。"""
    from PIL import Image, ImageDraw

    from .images import _font, synth_scene, to_u8

    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    ref_img = Image.open(ref_path).convert("RGB")
    ref_img.thumbnail((620, 620))

    scene = synth_scene()
    styled = np.clip(np.interp(scene, np.linspace(0, 1, 256), values), 0, 1)
    preview = to_u8(styled)
    preview.thumbnail((620, 620))

    # 直方图按实际显示宽度渲染（网页里这一栏约 1000px），画小了会被放大糊掉
    hw, hh = 1020, 300
    canvas = Image.new("RGB", (hw, hh), (252, 252, 250))
    d = ImageDraw.Draw(canvas)
    font, _ = _font(19)

    def hist(lum: np.ndarray, color_rgb, scale_max=None):
        h, _ = np.histogram(lum, bins=128, range=(0, 1))
        h = h / max(h.sum(), 1)
        return h

    a = np.asarray(ref_img).astype(np.float64) / 255.0
    ref_lum = (a @ color.LUMA_REC709).ravel()

    # 直方图必须是「同一批数据」才有可比性：曲线是在三张合成图的合成分布上拟合的，
    # 这里也用同一批数据渲染后的分布，否则两张直方图会因为场景不同而天然错开。
    grid = np.linspace(0, 1, 256)
    styled_lum = np.interp(canonical_pool(), grid, values)

    h_ref = hist(ref_lum, None)
    h_sty = hist(styled_lum, None)
    top = max(h_ref.max(), h_sty.max()) * 1.15 or 1

    def plot(h, rgb, width=2):
        pts = []
        for i, v in enumerate(h):
            x = 40 + i * (hw - 80) / 127
            y = hh - 38 - (v / top) * (hh - 82)
            pts.append((x, y))
        d.line(pts, fill=rgb, width=width)

    plot(h_ref, (37, 99, 235), 4)
    plot(h_sty, (219, 39, 119), 3)
    d.rectangle([40, 40, hw - 40, hh - 38], outline=(225, 227, 231))
    d.text((44, hh - 30), "灰阶 0 → 255", fill=(120, 124, 130), font=font)
    d.text((44, 14), "蓝 = 参考图　　粉 = 曲线作用后的典型场景", fill=(80, 84, 90), font=font)

    hp = outdir / "hist_compare.png"
    canvas.save(hp)

    panel = outdir / "ref_compare.png"
    width = ref_img.width + preview.width + 12
    height = max(ref_img.height, preview.height) + 34
    cv = Image.new("RGB", (width, height), (248, 248, 246))
    cv.paste(ref_img, (0, 26))
    cv.paste(preview, (ref_img.width + 12, 26))
    dd = ImageDraw.Draw(cv)
    dd.text((2, 6), "参考图", fill=(40, 40, 40), font=font)
    dd.text((ref_img.width + 14, 6), "这条曲线作用在标准场景上", fill=(40, 40, 40), font=font)
    cv.save(panel)

    return {"ref_compare": panel.name, "hist": hp.name}
