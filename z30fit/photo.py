"""用户照片：解码（含相机 RAW 的内嵌预览）、缩放到预览尺寸、套曲线。

为什么单独一个模块：预览台要的是「拿用户自己的照片看效果」，
这和拟合用的合成测试图完全是两件事——尺寸大、可能是 RAW、要管方向（EXIF 旋转）。

RAW 策略：
  1. 装了 `rawpy` 就走正经解码（相机白平衡、sRGB 输出）；
  2. 没装或解码失败，就退到**文件里内嵌的 JPEG 预览**——NEF/CR2/ARW 里都有一张，
     扫容器里的 JPEG 段取最大那张即可，不依赖任何第三方库。
  读到的都不是 RAW 原始数据而是它的渲染结果，界面上会说清楚。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

# 普通位图
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
# 相机 RAW：靠 rawpy 或内嵌预览
RAW_EXT = {".nef", ".nrw", ".arw", ".cr2", ".cr3", ".dng", ".raf", ".orf",
           ".rw2", ".pef", ".srw", ".3fr", ".x3f", ".raw"}
ALL_EXT = IMAGE_EXT | RAW_EXT

DEFAULT_MAX_SIDE = 1600


class PhotoError(ValueError):
    """读不了这张照片。消息直接给用户看，要具体。"""


def is_raw(path: str | Path) -> bool:
    return Path(path).suffix.lower() in RAW_EXT


def is_photo(path: str | Path) -> bool:
    return Path(path).suffix.lower() in ALL_EXT


# --------------------------------------------------------------------------
# 解码
# --------------------------------------------------------------------------
def _embedded_jpegs(path: Path):
    """把文件里所有像 JPEG 的段吐出来（大 → 小）。NEF/CR2/ARW 都能用。"""
    data = Path(path).read_bytes()
    segments: list[bytes] = []
    pos = 0
    while True:
        start = data.find(b"\xff\xd8\xff", pos)
        if start < 0:
            break
        end = data.find(b"\xff\xd9", start + 3)
        if end < 0:
            break
        segments.append(data[start:end + 2])
        pos = end + 2
    segments.sort(key=len, reverse=True)
    return segments[:6]


def _decode_with_rawpy(path: Path) -> Image.Image:
    import rawpy  # noqa: PLC0415 - 可选依赖，失败就退回内嵌预览

    with rawpy.imread(str(path)) as raw:
        rgb = raw.postprocess(use_camera_wb=True, half_size=True, no_auto_bright=True,
                              output_bps=8, gamma=(2.222, 4.5))
    return Image.fromarray(rgb, mode="RGB")


def _decode_embedded(path: Path) -> Image.Image:
    import io

    for seg in _embedded_jpegs(path):
        try:
            im = Image.open(io.BytesIO(seg))
            im.load()
            if im.width >= 320 and im.height >= 240:  # 缩略图太小，没参考价值
                return im.convert("RGB")
        except Exception:  # noqa: BLE001
            continue
    raise PhotoError("这个 RAW 里没有可用的内嵌预览，换一张，或在相机里存一份 JPEG")


def load_image(path: str | Path, max_side: int = DEFAULT_MAX_SIDE) -> tuple[Image.Image, str]:
    """读成 PIL 图像（已转正、已缩小）。返回 (图像, 来源说明)。"""
    p = Path(path)
    if not p.is_file():
        raise PhotoError(f"找不到文件：{p.name}")

    note = ""
    im: Image.Image | None = None

    if is_raw(p):
        try:
            im = _decode_with_rawpy(p)
            note = "RAW 解码"
        except Exception:  # noqa: BLE001 - rawpy 没装/不支持，退回内嵌预览
            im = _decode_embedded(p)
            note = "RAW 内嵌预览"
    else:
        try:
            im = Image.open(p)
            im.load()
            im = im.convert("RGB")
            note = "JPEG" if p.suffix.lower() in {".jpg", ".jpeg"} else p.suffix.lstrip(".").upper()
        except Exception as exc:  # noqa: BLE001
            raise PhotoError(f"读不了这张图（{type(exc).__name__}）：{p.name}") from exc

    im = ImageOps.exif_transpose(im).convert("RGB")
    if max(im.size) > max_side:
        im.thumbnail((max_side, max_side), Image.LANCZOS)
    return im, note


def load_rgb(path: str | Path, max_side: int = DEFAULT_MAX_SIDE) -> np.ndarray:
    im, _ = load_image(path, max_side)
    return np.asarray(im, dtype=np.float32) / 255.0


# --------------------------------------------------------------------------
# 套曲线 / 存预览
# --------------------------------------------------------------------------
def build_lut(table: np.ndarray) -> np.ndarray:
    """256 点表（0..32767）→ 8 位查表。单调保证它是个合法的映射。"""
    t = np.clip(np.asarray(table, dtype=np.float64) / 32767.0, 0.0, 1.0)
    return np.rint(t * 255.0).astype(np.uint8)


def apply_lut(im: Image.Image, lut: np.ndarray) -> Image.Image:
    arr = np.asarray(im.convert("RGB"), dtype=np.uint8)
    return Image.fromarray(lut[arr], mode="RGB")


def _token_for(path: Path, table: np.ndarray) -> str:
    h = hashlib.sha1()
    h.update(str(path.resolve()).encode("utf-8", "ignore"))
    h.update(np.asarray(table, dtype=np.uint16).tobytes())
    return h.hexdigest()[:10]


def write_pair(
    src: str | Path,
    table: np.ndarray,
    outdir: str | Path,
    max_side: int = DEFAULT_MAX_SIDE,
    quality: int = 92,
) -> dict:
    """生成「原图 / 套用后」两张同尺寸 JPEG，返回路径与说明。

    两张图尺寸、压缩方式完全一致，前端叠在一起做分割对比时不会错位。
    """
    src = Path(src)
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    im, note = load_image(src, max_side)
    lut = build_lut(table)
    after = apply_lut(im, lut)

    tag = _token_for(src, table)
    a = outdir / f"photo_{tag}_a.jpg"
    b = outdir / f"photo_{tag}_b.jpg"
    if not a.exists():
        im.save(a, quality=quality, optimize=True)
    if not b.exists():
        after.save(b, quality=quality, optimize=True)

    return {
        "a": a, "b": b, "width": im.width, "height": im.height,
        "note": note, "source": src.name,
    }


# --------------------------------------------------------------------------
# 导出成品（PNG / JPG / TIFF）
# --------------------------------------------------------------------------
# 像素总量上限：常见相机/手机照片（12–25MP）不受影响；极端大图等比缩进来，
# 不然 40MP 的 RGB 数组 + PNG 编码能吃掉几百 MB 内存。
EXPORT_MAX_PIXELS = 40_000_000

# fmt → (扩展名, 媒体类型, PIL 参数)
EXPORT_FORMATS = {
    "png": ("png", "image/png", {"format": "PNG"}),
    "jpg": ("jpg", "image/jpeg", {"format": "JPEG", "quality": 95, "optimize": True}),
    "tif": ("tif", "image/tiff", {"format": "TIFF", "compression": "tiff_lzw"}),
}


def export_photo(
    src: str | Path,
    table: np.ndarray,
    fmt: str = "png",
    max_pixels: int = EXPORT_MAX_PIXELS,
) -> tuple[bytes, str, str]:
    """按**原始分辨率**套上曲线，导出成品图字节。返回 (字节, 建议文件名, 说明)。

    三种格式：
      - png：无损（留档首选）
      - jpg：高质量 95（体积小，适合分享，有轻微有损压缩）
      - tif：无损 + LZW 压缩（专业后期工作流常用，比 PNG 更被打印/修图软件友好）

    「无损」指 PNG/TIFF 不做有损压缩，曲线作用在原始像素上（预览图是缩过的）；
    已按 EXIF 转正（`load_image` 统一处理）；超过 `max_pixels` 的图等比缩到上限内，
    说明里会写明。RAW 导不出来：套完曲线的图已经是渲染后的像素，
    而 RAW 必须是去马赛克之前的传感器数据——物理上不是一种东西。
    """
    key = fmt.lower()
    if key not in EXPORT_FORMATS:
        raise PhotoError(f"不支持的导出格式：{fmt}（可选 png / jpg / tif）")

    im, note = load_image(src, max_side=10 ** 7)  # 边长上限给到实际到不了的量级，即「不缩」
    if im.width * im.height > max_pixels:
        scale = (max_pixels / (im.width * im.height)) ** 0.5
        im = im.resize(
            (max(1, round(im.width * scale)), max(1, round(im.height * scale))),
            Image.LANCZOS,
        )
        note += "，图太大已按像素上限缩小"
    after = apply_lut(im, build_lut(table))

    import io

    buf = io.BytesIO()
    ext, _, save_kwargs = EXPORT_FORMATS[key]
    after.save(buf, **save_kwargs)

    # 下载名保持 ASCII（与机身名同一套顾虑），中文原图名给个兜底
    ext, _, _ = EXPORT_FORMATS[key]
    stem = Path(src).stem.encode("ascii", "ignore").decode().strip("-. ") or "photo"
    return buf.getvalue(), f"{stem}-z30tone.{ext}", note
