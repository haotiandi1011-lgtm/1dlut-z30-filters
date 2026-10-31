"""合成测试图、曲线绘制、对比图排版。

工作区里没有实拍样张，所以这里造三张「体检图」：
    chart     色卡 + 灰阶 —— 看颜色和灰阶还原
    scene     合成风景 —— 看整体观感（天空/植被/高光/阴影）
    gradient  连续渐变 —— 看色阶断层（1D 量化最容易翻车的地方）
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .curve import MonotoneSpline

_FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\msyhl.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\Deng.ttf",
]


def _font(size: int) -> tuple[ImageFont.ImageFont, bool]:
    for p in _FONT_CANDIDATES:
        if Path(p).exists():
            try:
                return ImageFont.truetype(p, size), True
            except OSError:
                continue
    return ImageFont.load_default(size=size), False


def text(zh: str, en: str) -> str:
    """有中文字体就用中文标签，否则退回英文，避免豆腐块。"""
    return zh if _font(16)[1] else en


# --------------------------------------------------------------------------
# 合成测试图
# --------------------------------------------------------------------------
CHECKER_24 = np.array(
    [
        [115, 82, 68], [194, 150, 130], [98, 122, 157], [87, 108, 67], [133, 128, 177], [103, 189, 170],
        [214, 126, 44], [80, 91, 166], [193, 90, 99], [94, 60, 108], [157, 188, 64], [224, 163, 46],
        [56, 61, 150], [70, 148, 73], [175, 54, 60], [231, 199, 31], [187, 86, 149], [8, 133, 161],
        [243, 243, 242], [200, 200, 200], [160, 160, 160], [122, 122, 121], [85, 85, 85], [52, 52, 52],
    ],
    dtype=np.float64,
) / 255.0


def synth_chart(h: int = 470, w: int = 768) -> np.ndarray:
    img = np.full((h, w, 3), 0.14, dtype=np.float64)

    # 11 级灰阶
    for i in range(11):
        xa = int(i * w / 11) + 3
        xb = int((i + 1) * w / 11) - 3
        img[18:96, xa:xb] = i / 10.0

    # 24 色卡
    cw = w / 6.0
    for idx, rgb in enumerate(CHECKER_24):
        row, col = divmod(idx, 6)
        xa = int(col * cw) + 4
        xb = int((col + 1) * cw) - 4
        ya = 104 + row * 66
        img[ya : ya + 62, xa:xb] = rgb

    # 肤色渐变（看肤色是否被压死或偏色）
    ya, yb = 104 + 4 * 66 + 6, h - 6
    if yb > ya:
        ramp = np.linspace(0, 1, w)
        band_h = (yb - ya) // 3
        for i, base in enumerate([(0.72, 0.52, 0.42), (0.45, 0.30, 0.24), (0.90, 0.74, 0.64)]):
            y_s = ya + i * band_h
            y_e = y_s + band_h - 3
            img[y_s:y_e, :, :] = np.clip(np.array(base)[None, None, :] * (0.55 + 0.75 * ramp[None, :, None]), 0, 1)

    return np.clip(img, 0, 1)


def synth_scene(h: int = 420, w: int = 768, seed: int = 7) -> np.ndarray:
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w]
    u = xx / (w - 1)
    v = yy / (h - 1)

    top = np.array([0.09, 0.27, 0.58])
    hor = np.array([0.99, 0.87, 0.68])
    k = np.clip(v / 0.66, 0, 1) ** 0.8
    img = top[None, None, :] * (1 - k[..., None]) + hor[None, None, :] * k[..., None]

    # 太阳 + 光晕（考高光滚降）
    d = np.hypot((u - 0.27) * 1.85, (v - 0.19))
    disc = np.clip(1.0 - d / 0.045, 0, 1) ** 0.6
    img = img + disc[..., None] * np.array([1.0, 0.96, 0.86]) * 1.4
    img = img + (np.exp(-((d / 0.11) ** 2)) * 0.55)[..., None] * np.array([1.0, 0.94, 0.78])
    img = img + (np.exp(-((d / 0.40) ** 2)) * 0.16)[..., None] * np.array([1.0, 0.92, 0.80])

    # 远山：带空气透视（偏蓝、低对比）
    far_ridge = 0.585 + 0.045 * np.sin(u * 5.1 + 0.9) + 0.018 * np.sin(u * 13.0)
    far_m = np.clip((v - far_ridge) * 30.0, 0, 1) * np.clip(1.0 - (v - 0.66) * 6.0, 0, 1)
    far_col = np.array([0.42, 0.50, 0.52])[None, None, :]
    img = img * (1 - far_m[..., None]) + far_col * far_m[..., None]

    # 近山 / 植被
    near_ridge = 0.655 + 0.035 * np.sin(u * 3.4 + 2.2) + 0.012 * np.sin(u * 11.0)
    near_m = np.clip((v - near_ridge) * 26.0, 0, 1)
    tex = rng.normal(0, 0.035, size=(h, w, 1))
    veg = np.array([0.24, 0.34, 0.15])[None, None, :] * (1.05 - 0.35 * v[..., None]) + tex
    img = img * (1 - near_m[..., None]) + veg * near_m[..., None]

    # 前景阴影：从右下角向外衰减的柔和暗部（考黑位压缩）
    sd = np.hypot((u - 0.92) * 1.3, (v - 1.05))
    shadow = np.clip(1.0 - sd / 0.62, 0, 1) ** 1.4 * 0.80
    img = img * (1 - shadow[..., None])

    # 肤色椭圆（考肤色）
    su = (u - 0.46) / 0.075
    sv = (v - 0.76) / 0.095
    skin = np.clip(1.0 - (su**2 + sv**2), 0, 1) ** 0.45
    img = img * (1 - skin[..., None]) + np.array([0.86, 0.66, 0.55])[None, None, :] * skin[..., None]

    return np.clip(img, 0, 1)


def synth_gradient(h: int = 220, w: int = 768) -> np.ndarray:
    img = np.zeros((h, w, 3), dtype=np.float64)
    ramp = np.linspace(0, 1, w)[None, :, None]

    # 上：纯灰阶
    img[: h // 2] = ramp
    # 下：蓝 → 中性 → 暖，考色相过渡
    blue = np.array([0.06, 0.16, 0.62])
    mid = np.array([0.72, 0.72, 0.70])
    warm = np.array([0.86, 0.52, 0.24])
    t = np.linspace(0, 1, w)[:, None]
    colr = np.where(t < 0.5, blue * (1 - 2 * t) + mid * (2 * t), mid * (2 - 2 * t) + warm * (2 * t - 1))
    img[h // 2 :] = colr[None, :, :]
    return np.clip(img, 0, 1)


# --------------------------------------------------------------------------
# 应用与渲染
# --------------------------------------------------------------------------
def apply_curve(img: np.ndarray, curve: MonotoneSpline) -> np.ndarray:
    return np.clip(curve(np.asarray(img, dtype=np.float64)), 0, 1)


def to_u8(img: np.ndarray) -> Image.Image:
    arr = np.rint(np.clip(img, 0, 1) * 255.0).astype(np.uint8)
    return Image.fromarray(arr, mode="RGB")


def diff_panel(a: np.ndarray, b: np.ndarray, gain: float = 8.0) -> np.ndarray:
    """放大的绝对差。开平方根是为了把 1–3 个码值的小偏差也显形，否则整块偏黑看不出东西。"""
    d = np.abs(np.asarray(a) - np.asarray(b)) * gain
    return np.clip(d, 0, 1) ** 0.55


def strip(
    path: str | Path,
    panels: list[tuple[str, np.ndarray]],
    title: str = "",
    gap: int = 10,
    pad: int = 14,
    panel_w: int = 440,
) -> Path:
    """把若干面板拼成一条对比图（每块缩放到 panel_w，保证标签看得清）。"""
    font, _ = _font(17)
    title_font, _ = _font(21)

    imgs = []
    for _, p in panels:
        im = to_u8(p)
        if panel_w and im.width > panel_w:
            new_h = int(round(im.height * panel_w / im.width))
            im = im.resize((panel_w, new_h), Image.LANCZOS)
        imgs.append(im)

    ph = max(im.height for im in imgs)
    total_w = pad * 2 + sum(im.width for im in imgs) + gap * (len(imgs) - 1)
    head = 52 if title else 30
    canvas = Image.new("RGB", (total_w, ph + head + 30), (248, 248, 246))
    draw = ImageDraw.Draw(canvas)

    if title:
        draw.text((pad, 12), title, fill=(20, 20, 20), font=title_font)

    x = pad
    for (label, _), im in zip(panels, imgs):
        canvas.paste(im, (x, head))
        draw.text((x + 2, head + ph + 7), label, fill=(50, 50, 50), font=font)
        x += im.width + gap

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(path)
    return path


def plot_curves(
    path: str | Path,
    series: list[tuple[str, np.ndarray, np.ndarray, tuple[int, int, int]]],
    title: str = "tone curve",
    size: int = 460,
) -> Path:
    """自己画曲线图（不依赖 matplotlib）。"""
    font, cjk = _font(15)
    m = 52
    w = h = size
    canvas = Image.new("RGB", (w, h + 56), (252, 252, 250))
    d = ImageDraw.Draw(canvas)

    def px(x: float, y: float) -> tuple[float, float]:
        return (m + x * (w - m - 22), h - 16 - y * (h - 16 - m))

    for i in range(0, 11):
        gx, gy = px(i / 10, 0)[0], px(0, i / 10)[1]
        d.line([(gx, m - 10), (gx, h - 16)], fill=(226, 226, 222))
        d.line([(m, gy), (w - 22, gy)], fill=(226, 226, 222))
    d.rectangle([m, m - 10, w - 22, h - 16], outline=(120, 120, 118))

    # 恒等参考线
    x_id = np.linspace(0, 1, 64)
    d.line([px(float(x), float(y)) for x, y in zip(x_id, x_id)], fill=(190, 190, 186))

    for label, xs, ys, rgb in series:
        pts = [px(float(x), float(np.clip(y, 0, 1))) for x, y in zip(xs, ys)]
        if len(pts) > 1:
            d.line(pts, fill=rgb, width=2, joint="curve")

    ty = h + 14
    tx = m
    for label, _, _, rgb in series:
        d.rectangle([tx, ty + 4, tx + 12, ty + 14], fill=rgb)
        d.text((tx + 18, ty), label, fill=(50, 50, 50), font=font)
        tx += 24 + int(d.textlength(label, font=font))
    d.text((m, 16), title, fill=(20, 20, 20), font=_font(18)[0])

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(path)
    return path
