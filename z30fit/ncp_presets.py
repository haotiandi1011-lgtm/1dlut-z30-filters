"""真实配方库（来自 nikonpc.com 社区，经 z-cloud-ai 整理）。

数据在 `ncp_data.py`（自动生成，勿手改）。这里的职责：
  1. 把稀疏锚点用单调样条插值成 256 点曲线；
  2. 提供与 `presets.py` 相同形状的 listing()/get()，供 webui 合并展示。

曲线是「单曲线（1D）」：对 RGB 三通道是同一个映射，影调 1:1 无损。
配方里的饱和度/锐化/色相等参数不在这里写入（色相 Z30 干脆没有）。
"""

from __future__ import annotations

import numpy as np

from . import LUT_MAX, LUT_POINTS
from .curve import MonotoneSpline
from .ncp_data import NCP_PRESETS

NOTE = (
    "真实配方库：曲线取自社区沉淀的经典优化校准配方，影调 1:1 还原。"
    "写入时会连同饱和度、锐化、清晰度一起进机身，可在写入前调整。"
    "「仿」系列只仿影调，不含原厂色彩与颗粒。"
)


def curve_from_pts(pts: list[tuple[int, int]]) -> np.ndarray:
    """稀疏锚点 (0..255) → 256 点单调曲线 (0..1)。"""
    xs = np.array([p[0] for p in pts], dtype=np.float64) / 255.0
    ys = np.array([p[1] for p in pts], dtype=np.float64) / 255.0
    # 去重 x（数据里不应有，但保险）：保留首次出现的 x
    _, first = np.unique(xs, return_index=True)
    xs, ys = xs[np.sort(first)], ys[np.sort(first)]
    if xs.size < 2:
        return np.linspace(0.0, 1.0, LUT_POINTS)
    if xs[0] != 0.0 or xs[-1] != 1.0:
        # 端点没对齐时补两端，保证曲线覆盖全影调
        xs = np.concatenate([[0.0], xs])
        ys = np.concatenate([[0.0], ys])
        if xs[-1] < 1.0:
            xs = np.concatenate([xs, [1.0]])
            ys = np.concatenate([ys, [1.0]])
    spline = MonotoneSpline(xs, ys)
    xq = np.linspace(0.0, 1.0, LUT_POINTS)
    return np.clip(spline(xq), 0.0, 1.0)


def table_from_pts(pts: list[tuple[int, int]]) -> np.ndarray:
    """锚点 → u16 表（0..32767），供载荷打包。"""
    y = curve_from_pts(pts)
    return np.rint(np.clip(y, 0.0, 1.0) * LUT_MAX).astype(np.int64)


_LISTING: list[dict] | None = None


def _personal_overrides() -> dict:
    """个人使用版的命名覆盖层（可选）。

    存在 `z30fit/personal_names.py` 时，用它替换中性显示名——
    只改「显示名 / 建议机身名 / 说明里的旧风格名」，曲线与参数一个字节都不动。
    删掉该文件即回到中性命名（开源版行为）。
    """
    try:
        from z30fit import personal_names as pn

        return getattr(pn, "PERSONAL_NAMES", {}) or {}
    except Exception:
        return {}


def _apply_personal(item: dict, pid: str, neutral_name: str, neutral_cam: str, desc: str) -> None:
    ov = _personal_overrides().get(pid)
    if not ov:
        return
    item["name"] = ov.get("name") or neutral_name
    item["cam"] = ov.get("cam") or neutral_cam
    # 中性风格名单独给一个字段：桌面端拼进说明、安卓端放在副标题里
    item["style"] = ov.get("style") or ""
    item["desc"] = f"「{ov.get('style') or neutral_name}」· {desc}"


def listing() -> list[dict]:
    global _LISTING
    if _LISTING is None:
        out = []
        for p in NCP_PRESETS:
            curve = curve_from_pts(p["pts"])
            # 界面那个小缩略曲线：17 个采样点，0..1000
            spark = np.interp(np.linspace(0.0, 1.0, 17), np.linspace(0.0, 1.0, curve.size), curve)
            item = {
                "id": p["id"], "name": p["name"], "cat": p["cat"], "desc": p["desc"],
                "base": p.get("base", ""), "params": p.get("params", {}),
                "cam": p.get("cam", ""), "builtin": True, "group": "ncp",
                "spark": [int(round(float(v) * 1000)) for v in spark],
            }
            _apply_personal(item, p["id"], p["name"], p.get("cam", ""), p["desc"])
            out.append(item)
        _LISTING = out
    return _LISTING


def get(pid: str) -> dict | None:
    for p in NCP_PRESETS:
        if p["id"] == pid:
            item = {**p, "table": table_from_pts(p["pts"]), "builtin": True, "group": "ncp"}
            _apply_personal(item, p["id"], p["name"], p.get("cam", ""), p["desc"])
            return item
    return None
