"""预设库：一批评测过的影调配方 + 用户自存配方。

**不需要拟合**——预设本身就是一条曲线，选中即用。
因此这里没有 ΔE（没有参考真值），只有影调画像。

命名规则（见 产品规划.md 第 9 节）：一律中文描述性命名，不写任何厂商商标。
名字描述的是**影调形态**，不是"某厂风格"。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np

from . import LUT_MAX
from .paths import data_root

GRID = np.linspace(0.0, 1.0, 256)


def _smoothstep(t: np.ndarray) -> np.ndarray:
    t = np.clip(t, 0, 1)
    return t * t * (3 - 2 * t)


def curve_from_spec(spec: dict) -> np.ndarray:
    """按参数生成 256 点单调曲线。

    black/white  黑位白位
    gamma        中间调整体位置（>1 压暗，<1 提亮）
    contrast     >0 加对比（S 形），<0 减对比（拉平），范围 -1..1
    shoulder     高光滚降强度 0..1
    toe          暗部压缩强度 0..1
    """
    b = float(spec.get("black", 0.0))
    w = float(spec.get("white", 1.0))
    g = float(spec.get("gamma", 1.0))
    c = float(spec.get("contrast", 0.0))
    sh = float(spec.get("shoulder", 0.0))
    toe = float(spec.get("toe", 0.0))

    x = GRID.copy()
    y = b + (w - b) * np.power(np.clip(x, 0, 1), g)

    x_ = np.clip(y, 0, 1)
    if c > 0:
        y = (1 - c) * x_ + c * _smoothstep(x_)
    elif c < 0:
        flat = 0.25 + 0.5 * x_          # 拉平：把范围压到中间
        y = (1 + c) * x_ + (-c) * flat
    else:
        y = x_

    # 高光滚降：只压低高光段的斜率，端点（白位）保持不变
    if sh > 0:
        hi = np.clip((y - 0.55) / 0.45, 0, 1)
        y = y - sh * 0.45 * (hi - hi ** 2) * 0.5
    # 趾部：抬暗部上方、但保持黑位不动，形成更陡的趾部
    if toe > 0:
        lo = np.clip((0.2 - y) / 0.2, 0, 1)
        y = y + toe * 0.2 * (lo - lo ** 2) * 0.5

    y = np.clip(y, 0, 1)
    # 数值上保个底：任何情况下都不许回折
    y = np.maximum.accumulate(y)
    return y


def quantize_curve(y: np.ndarray) -> np.ndarray:
    return np.rint(np.clip(y, 0, 1) * LUT_MAX).astype(np.uint16)


# --------------------------------------------------------------------------
# 内置配方：名字描述影调形态，desc 写清适合什么
# --------------------------------------------------------------------------
BUILTIN: list[dict] = [
    {"id": "neutral", "name": "中性直出", "desc": "不做任何改动，用来对照",
     "spec": {}},
    {"id": "soft-portrait", "name": "柔和人像", "desc": "抬暗部、缓高光，皮肤不容易死白",
     "spec": {"black": 0.045, "white": 0.965, "gamma": 1.02, "contrast": -0.18, "shoulder": 0.35, "toe": 0.4}},
    {"id": "bright-clear", "name": "明亮通透", "desc": "中间调整体提亮，适合室内和阴天",
     "spec": {"black": 0.02, "white": 1.0, "gamma": 0.86, "contrast": 0.05, "shoulder": 0.15}},
    {"id": "hard-street", "name": "硬朗街头", "desc": "强 S 曲线，暗部压深、高光提亮，适合光影强烈",
     "spec": {"black": 0.0, "white": 0.995, "gamma": 1.0, "contrast": 0.42, "shoulder": 0.25}},
    {"id": "faded-soft", "name": "柔和褪色", "desc": "抬黑压白，反差低，老照片那种蒙一层灰",
     "spec": {"black": 0.075, "white": 0.93, "gamma": 1.04, "contrast": -0.12, "shoulder": 0.3}},
    {"id": "cinema-dark", "name": "电影暗调", "desc": "压暗整体、高光缓慢滚降，夜景和室内氛围",
     "spec": {"black": 0.0, "white": 0.97, "gamma": 1.18, "contrast": 0.12, "shoulder": 0.55}},
    {"id": "retro-grey", "name": "复古灰调", "desc": "大幅抬黑，几乎没有纯黑，适合纪实",
     "spec": {"black": 0.11, "white": 0.95, "gamma": 1.0, "contrast": -0.25, "shoulder": 0.2}},
    {"id": "backlight", "name": "逆光柔化", "desc": "只压高光不动暗部，逆光和大光比场景",
     "spec": {"black": 0.0, "white": 0.985, "gamma": 0.98, "contrast": 0.0, "shoulder": 0.7}},
    {"id": "crisp-doc", "name": "硬朗纪实", "desc": "趾部陡、暗部扎实，适合街拍和人文",
     "spec": {"black": 0.0, "white": 0.99, "gamma": 0.95, "contrast": 0.3, "shoulder": 0.2, "toe": 0.5}},
    {"id": "light-film", "name": "轻胶片", "desc": "轻 S 加轻微抬黑，最接近常见胶片曲线的形态",
     "spec": {"black": 0.032, "white": 0.975, "gamma": 1.0, "contrast": 0.22, "shoulder": 0.28, "toe": 0.2}},
    {"id": "highlight-rolloff", "name": "高光滚降", "desc": "只处理高光，暗部原样，适合白色主体",
     "spec": {"black": 0.0, "white": 1.0, "gamma": 1.0, "contrast": -0.05, "shoulder": 0.85}},
    {"id": "deep-low", "name": "深沉低调", "desc": "中间调压暗，画面沉，适合夜景和静物",
     "spec": {"black": 0.01, "white": 0.98, "gamma": 1.22, "contrast": 0.18, "shoulder": 0.35}},

    # ---- 影调族：只近似影调，绝不含色彩/颗粒，说明里必须写明 ----
    {"id": "classic-neg", "name": "经典负片 · 抬黑中反差", "desc": "影调近似：抬黑、中高反差。不含色彩与颗粒",
     "spec": {"black": 0.055, "white": 0.97, "gamma": 1.05, "contrast": 0.28, "shoulder": 0.3, "toe": 0.25}},
    {"id": "soft-shoulder", "name": "柔肩人像 · 低反差", "desc": "影调近似：低反差、柔肩，皮肤不易死白。不含色彩",
     "spec": {"black": 0.035, "white": 0.975, "gamma": 1.0, "contrast": -0.14, "shoulder": 0.4, "toe": 0.35}},
    {"id": "deep-landscape", "name": "深影风光 · 强 S", "desc": "影调近似：强 S、深影。注意：只有影调，没有高饱和",
     "spec": {"black": 0.0, "white": 0.99, "gamma": 1.04, "contrast": 0.5, "shoulder": 0.22, "toe": 0.3}},
    {"id": "mid-contrast", "name": "中强反差 · 轻抬黑", "desc": "影调近似：中强 S、轻抬黑。不含暖色",
     "spec": {"black": 0.03, "white": 0.98, "gamma": 1.02, "contrast": 0.34, "shoulder": 0.26, "toe": 0.2}},
    {"id": "faded-portrait", "name": "褪色人像 · 柔肩", "desc": "影调近似：抬黑、低反差、柔肩。不含肤色处理",
     "spec": {"black": 0.06, "white": 0.96, "gamma": 1.0, "contrast": -0.2, "shoulder": 0.32, "toe": 0.4}},
    {"id": "mono-hard", "name": "黑白 · 强对比", "desc": "影调近似：强对比。配合机身黑白模式用",
     "spec": {"black": 0.0, "white": 0.995, "gamma": 1.1, "contrast": 0.55, "shoulder": 0.18, "toe": 0.4}},
    {"id": "modern-color", "name": "现代彩色 · S 形轻抬黑", "desc": "影调近似：S 形 + 轻抬黑。不含色彩",
     "spec": {"black": 0.028, "white": 0.975, "gamma": 1.0, "contrast": 0.24, "shoulder": 0.3, "toe": 0.22}},
    {"id": "deep-fade", "name": "大幅褪色 · 压白", "desc": "影调近似：大幅抬黑压白。不含去饱和",
     "spec": {"black": 0.09, "white": 0.93, "gamma": 1.02, "contrast": -0.16, "shoulder": 0.28}},
]


# --------------------------------------------------------------------------
# 用户自存配方
# --------------------------------------------------------------------------
def _user_path() -> Path:
    return data_root() / "presets.json"


def load_user() -> list[dict]:
    p = _user_path()
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []


def save_user(name: str, desc: str, table: np.ndarray, source: str = "") -> dict:
    items = load_user()
    entry = {
        "id": f"user-{int(time.time() * 1000) % 10_000_000}",
        "name": name[:19] or "自定义",
        "desc": desc or "自己保存的配方",
        "table": [int(v) for v in table],
        "source": source,
        "created": time.strftime("%Y-%m-%d %H:%M"),
        "builtin": False,
    }
    # 同名覆盖
    items = [i for i in items if i.get("name") != entry["name"]]
    items.append(entry)
    _user_path().write_text(json.dumps(items[-60:], ensure_ascii=False, indent=1), encoding="utf-8")
    return entry


def delete_user(pid: str) -> bool:
    items = load_user()
    left = [i for i in items if i["id"] != pid]
    _user_path().write_text(json.dumps(left, ensure_ascii=False, indent=1), encoding="utf-8")
    return len(left) != len(items)


def get(pid: str) -> dict | None:
    for p in BUILTIN:
        if p["id"] == pid:
            y = curve_from_spec(p["spec"])
            return {**p, "table": quantize_curve(y), "builtin": True}
    for p in load_user():
        if p["id"] == pid:
            return {**p, "table": np.asarray(p["table"], dtype=np.uint16), "builtin": False}
    return None


def listing() -> list[dict]:
    out = [{"id": p["id"], "name": p["name"], "desc": p["desc"], "builtin": True} for p in BUILTIN]
    out += [{"id": p["id"], "name": p["name"], "desc": p["desc"], "builtin": False} for p in load_user()]
    return out
