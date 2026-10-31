"""把「读输入 → 拟合」这一段封成单一入口，CLI 和网页端共用。"""

from __future__ import annotations

from pathlib import Path

from .cube import CubeLUT, load_cube
from .fit1d import FitResult, fit_curve_direct, fit_lut
from . import np3 as np3_mod


def fit_from_path(path: str | Path, mode: str = "auto", knots: int = 33) -> tuple[FitResult, CubeLUT | None]:
    """支持 .cube / .np3。返回 (拟合结果, 参考 LUT)；NP3 没有 LUT 对象时为 None。"""
    p = Path(path)
    suffix = p.suffix.lower()
    extra: dict = {}

    if suffix == ".cube":
        lut = load_cube(p)
        extra["TITLE"] = lut.title or "（无）"
        return fit_lut(lut, source=str(p), mode=mode, knots=knots, extra=extra), lut

    if suffix == ".np3":
        f = np3_mod.parse(p)
        extra["版本"] = f.version
        extra["内部名"] = f.name or "（空）"
        extra["基础校准 ID"] = f"0x{(f.base_id or 0):04X}"
        got = np3_mod.extract_curve(f)
        if got is None:
            raise ValueError(f"{p.name} 里没有内嵌曲线（这类文件只有参数记录，36 字节形态）")
        curve, meta = got
        extra["曲线来源"] = f"偏移 0x{meta['offset']:03X}，{meta['points']} 点 u16BE"
        curve256 = np3_mod.resample_curve(curve, 256)
        return fit_curve_direct(curve256, source=str(p), knots=knots, extra=extra), None

    raise ValueError(f"不支持的格式：{p.suffix}（只认 .cube / .np3）")
