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
    {"id": "neutral", "name": "中性直出", "cam": "NEUTRAL", "desc": "不做任何改动，用来对照",
     "spec": {}},
    {"id": "soft-portrait", "name": "柔和人像", "cam": "SOFT-PORTRAIT", "desc": "抬暗部、缓高光，皮肤不容易死白",
     "spec": {"black": 0.045, "white": 0.965, "gamma": 1.02, "contrast": -0.18, "shoulder": 0.35, "toe": 0.4}},
    {"id": "bright-clear", "name": "明亮通透", "cam": "BRIGHT-CLEAR", "desc": "中间调整体提亮，适合室内和阴天",
     "spec": {"black": 0.02, "white": 1.0, "gamma": 0.86, "contrast": 0.05, "shoulder": 0.15}},
    {"id": "hard-street", "name": "硬朗街头", "cam": "HARD-STREET", "desc": "强 S 曲线，暗部压深、高光提亮，适合光影强烈",
     "spec": {"black": 0.0, "white": 0.995, "gamma": 1.0, "contrast": 0.42, "shoulder": 0.25}},
    {"id": "faded-soft", "name": "柔和褪色", "cam": "FADED-SOFT", "desc": "抬黑压白，反差低，老照片那种蒙一层灰",
     "spec": {"black": 0.075, "white": 0.93, "gamma": 1.04, "contrast": -0.12, "shoulder": 0.3}},
    {"id": "cinema-dark", "name": "电影暗调", "cam": "CINEMA-DARK", "desc": "压暗整体、高光缓慢滚降，夜景和室内氛围",
     "spec": {"black": 0.0, "white": 0.97, "gamma": 1.18, "contrast": 0.12, "shoulder": 0.55}},
    {"id": "retro-grey", "name": "复古灰调", "cam": "RETRO-GREY", "desc": "大幅抬黑，几乎没有纯黑，适合纪实",
     "spec": {"black": 0.11, "white": 0.95, "gamma": 1.0, "contrast": -0.25, "shoulder": 0.2}},
    {"id": "backlight", "name": "逆光柔化", "cam": "BACKLIGHT", "desc": "只压高光不动暗部，逆光和大光比场景",
     "spec": {"black": 0.0, "white": 0.985, "gamma": 0.98, "contrast": 0.0, "shoulder": 0.7}},
    {"id": "crisp-doc", "name": "硬朗纪实", "cam": "CRISP-DOC", "desc": "趾部陡、暗部扎实，适合街拍和人文",
     "spec": {"black": 0.0, "white": 0.99, "gamma": 0.95, "contrast": 0.3, "shoulder": 0.2, "toe": 0.5}},
    {"id": "light-film", "name": "轻胶片", "cam": "LIGHT-FILM", "desc": "轻 S 加轻微抬黑，最接近常见胶片曲线的形态",
     "spec": {"black": 0.032, "white": 0.975, "gamma": 1.0, "contrast": 0.22, "shoulder": 0.28, "toe": 0.2}},
    {"id": "highlight-rolloff", "name": "高光滚降", "cam": "HIGHLIGHT-ROLLOFF", "desc": "只处理高光，暗部原样，适合白色主体",
     "spec": {"black": 0.0, "white": 1.0, "gamma": 1.0, "contrast": -0.05, "shoulder": 0.85}},
    {"id": "deep-low", "name": "深沉低调", "cam": "DEEP-LOW", "desc": "中间调压暗，画面沉，适合夜景和静物",
     "spec": {"black": 0.01, "white": 0.98, "gamma": 1.22, "contrast": 0.18, "shoulder": 0.35}},

    # ---- 影调族：只近似影调，绝不含色彩/颗粒，说明里必须写明 ----
    {"id": "classic-neg", "name": "经典负片 · 抬黑中反差", "cam": "CLASSIC-NEG", "desc": "影调近似：抬黑、中高反差。不含色彩与颗粒",
     "spec": {"black": 0.055, "white": 0.97, "gamma": 1.05, "contrast": 0.28, "shoulder": 0.3, "toe": 0.25}},
    {"id": "soft-shoulder", "name": "柔肩人像 · 低反差", "cam": "SOFT-SHOULDER", "desc": "影调近似：低反差、柔肩，皮肤不易死白。不含色彩",
     "spec": {"black": 0.035, "white": 0.975, "gamma": 1.0, "contrast": -0.14, "shoulder": 0.4, "toe": 0.35}},
    {"id": "deep-landscape", "name": "深影风光 · 强 S", "cam": "DEEP-LANDSCAPE", "desc": "影调近似：强 S、深影。注意：只有影调，没有高饱和",
     "spec": {"black": 0.0, "white": 0.99, "gamma": 1.04, "contrast": 0.5, "shoulder": 0.22, "toe": 0.3}},
    {"id": "mid-contrast", "name": "中强反差 · 轻抬黑", "cam": "MID-CONTRAST", "desc": "影调近似：中强 S、轻抬黑。不含暖色",
     "spec": {"black": 0.03, "white": 0.98, "gamma": 1.02, "contrast": 0.34, "shoulder": 0.26, "toe": 0.2}},
    {"id": "faded-portrait", "name": "褪色人像 · 柔肩", "cam": "FADED-PORTRAIT", "desc": "影调近似：抬黑、低反差、柔肩。不含肤色处理",
     "spec": {"black": 0.06, "white": 0.96, "gamma": 1.0, "contrast": -0.2, "shoulder": 0.32, "toe": 0.4}},
    {"id": "mono-hard", "name": "黑白 · 强对比", "cam": "MONO-HARD", "desc": "影调近似：强对比。配合机身黑白模式用",
     "spec": {"black": 0.0, "white": 0.995, "gamma": 1.1, "contrast": 0.55, "shoulder": 0.18, "toe": 0.4}},
    {"id": "modern-color", "name": "现代彩色 · S 形轻抬黑", "cam": "MODERN-COLOR", "desc": "影调近似：S 形 + 轻抬黑。不含色彩",
     "spec": {"black": 0.028, "white": 0.975, "gamma": 1.0, "contrast": 0.24, "shoulder": 0.3, "toe": 0.22}},
    {"id": "deep-fade", "name": "大幅褪色 · 压白", "cam": "DEEP-FADE", "desc": "影调近似：大幅抬黑压白。不含去饱和",
     "spec": {"black": 0.09, "white": 0.93, "gamma": 1.02, "contrast": -0.16, "shoulder": 0.28}},

    # ---- 补充族（20 套）：低饱和 / 冷调 / 硬光 ----
    # 这一族是手调的参数化曲线，不是社区配方库里的条目，所以编号接着胶片系列往下排。
    # 三条硬事实必须写进 desc，别让名字把人骗了：
    #   1. 1D 曲线只改明暗，改不了色相；「冷调」靠的是机身白平衡往低色温走，
    #      曲线负责的是「平、软、压高光」这种冷调常见的明暗骨架。
    #   2. 「低饱和」能真做——饱和度在参数区 +0x01E，跟着曲线一起进机身。
    #   3. 「硬光」= 强 S + 陡趾部 + 收肩，再配清晰度；不含颗粒。
    {"id": "fl25-cool-mute", "name": "胶片 25 · 低饱和冷调", "cam": "FL25-COOL",
     "desc": "影调近似：低反差 + 抬黑 + 压高光，观感偏冷偏淡。饱和度 -2 随曲线写入；"
             "真正的冷色要把机身白平衡往低色温调",
     "spec": {"black": 0.045, "white": 0.96, "gamma": 1.06, "contrast": -0.1, "shoulder": 0.35, "toe": 0.3},
     "params": {"sat": -2, "sharp": 1, "clarity": 0}},
    {"id": "fl26-warm-mute", "name": "胶片 26 · 低饱和暖调", "cam": "FL26-WARM",
     "desc": "影调近似：轻抬黑、中间调略提亮，饱和度压低但暗部不发灰。不含暖色偏移",
     "spec": {"black": 0.05, "white": 0.97, "gamma": 0.97, "contrast": -0.05, "shoulder": 0.3, "toe": 0.28},
     "params": {"sat": -1.5, "sharp": 1.5, "clarity": 0}},
    {"id": "fl27-flat-mute", "name": "胶片 27 · 极低饱和", "cam": "FL27-FLAT",
     "desc": "影调近似：几乎全平的曲线 + 压白，饱和度 -3 到底。想要灰片质感用它",
     "spec": {"black": 0.03, "white": 0.94, "gamma": 1.0, "contrast": -0.22, "shoulder": 0.25, "toe": 0.2},
     "params": {"sat": -3, "sharp": 0.5, "clarity": -0.5}},
    {"id": "fl28-pale-mute", "name": "胶片 28 · 淡颜低饱和", "cam": "FL28-PALE",
     "desc": "影调近似：中间调提亮 + 低反差，皮肤不容易死白。肤色本身不处理",
     "spec": {"black": 0.06, "white": 0.975, "gamma": 0.93, "contrast": -0.12, "shoulder": 0.3, "toe": 0.35},
     "params": {"sat": -1.75, "sharp": 1, "clarity": -0.25}},
    {"id": "fl29-grey-mute", "name": "胶片 29 · 灰调低饱和", "cam": "FL29-GREY",
     "desc": "影调近似：大幅抬黑压白，整幅片子蒙一层灰。饱和度 -2.25",
     "spec": {"black": 0.085, "white": 0.92, "gamma": 1.03, "contrast": -0.18, "shoulder": 0.26, "toe": 0.24},
     "params": {"sat": -2.25, "sharp": 0.75, "clarity": -0.75}},

    {"id": "fl30-cool-flat", "name": "胶片 30 · 冷调平光", "cam": "FL30-COOL",
     "desc": "影调近似：平光低反差、高光缓收，阴天和棚拍最稳。冷色靠白平衡，曲线只管明暗",
     "spec": {"black": 0.02, "white": 0.955, "gamma": 1.02, "contrast": -0.08, "shoulder": 0.45, "toe": 0.2},
     "params": {"sat": -1, "sharp": 1.5, "clarity": 0}},
    {"id": "fl31-cool-high", "name": "胶片 31 · 冷调高光", "cam": "FL31-HIGH",
     "desc": "影调近似：中间调压一点、高光滚降拉长，逆光下白衣服不会一刀切。不含冷色偏移",
     "spec": {"black": 0.0, "white": 0.97, "gamma": 1.08, "contrast": 0.1, "shoulder": 0.6, "toe": 0.25},
     "params": {"sat": -0.75, "sharp": 1.5, "clarity": 0.25}},
    {"id": "fl32-cool-night", "name": "胶片 32 · 冷调夜景", "cam": "FL32-NIGHT",
     "desc": "影调近似：中间调压暗、高光慢慢滚，夜景灯光不炸。暗部保留一点层次",
     "spec": {"black": 0.01, "white": 0.95, "gamma": 1.24, "contrast": 0.14, "shoulder": 0.5, "toe": 0.3},
     "params": {"sat": -1.25, "sharp": 1, "clarity": 0.5}},
    {"id": "fl33-cool-blue", "name": "胶片 33 · 冷调灰蓝", "cam": "FL33-BLUE",
     "desc": "影调近似：抬黑 + 低反差 + 缓肩，配低色温白平衡就是灰蓝色调。曲线不含蓝",
     "spec": {"black": 0.065, "white": 0.94, "gamma": 1.05, "contrast": -0.14, "shoulder": 0.32, "toe": 0.26},
     "params": {"sat": -1.75, "sharp": 0.75, "clarity": -0.25}},
    {"id": "fl34-cool-press", "name": "胶片 34 · 冷调压光", "cam": "FL34-PRESS",
     "desc": "影调近似：白位压到 93% + 强肩部，整幅偏沉。适合阴雨、雾天这类平淡光线",
     "spec": {"black": 0.0, "white": 0.93, "gamma": 1.12, "contrast": 0.2, "shoulder": 0.7, "toe": 0.3},
     "params": {"sat": -1, "sharp": 1.25, "clarity": 0.5}},

    {"id": "fl35-hard-contrast", "name": "胶片 35 · 硬光高反差", "cam": "FL35-HARD",
     "desc": "影调近似：强 S 曲线 + 陡趾部，正午硬光下暗部扎实。清晰度 +1 一起写入",
     "spec": {"black": 0.0, "white": 0.995, "gamma": 1.0, "contrast": 0.55, "shoulder": 0.18, "toe": 0.35},
     "params": {"sat": 0, "sharp": 2, "clarity": 1}},
    {"id": "fl36-hard-sun", "name": "胶片 36 · 硬光直射", "cam": "FL36-SUN",
     "desc": "影调近似：反差拉到最满、肩部收紧，直射光下的质感。亮部容易丢细节，注意测光",
     "spec": {"black": 0.0, "white": 1.0, "gamma": 1.04, "contrast": 0.62, "shoulder": 0.12, "toe": 0.45},
     "params": {"sat": 0.25, "sharp": 2.5, "clarity": 1.5}},
    {"id": "fl37-hard-portrait", "name": "胶片 37 · 硬光人像", "cam": "FL37-FACE",
     "desc": "影调近似：中反差 + 柔肩，硬光人像里保住高光。肤色不单独处理",
     "spec": {"black": 0.005, "white": 0.985, "gamma": 0.97, "contrast": 0.4, "shoulder": 0.4, "toe": 0.25},
     "params": {"sat": -0.5, "sharp": 1, "clarity": 0.75}},
    {"id": "fl38-hard-arch", "name": "胶片 38 · 硬朗建筑", "cam": "FL38-ARCH",
     "desc": "影调近似：暗部压深、白位保住，线条和材质更清楚。清晰度 +2 随曲线写入",
     "spec": {"black": 0.0, "white": 0.99, "gamma": 1.03, "contrast": 0.48, "shoulder": 0.22, "toe": 0.4},
     "params": {"sat": -0.25, "sharp": 2, "clarity": 2}},
    {"id": "fl39-hard-rolloff", "name": "胶片 39 · 硬光压制", "cam": "FL39-ROLL",
     "desc": "影调近似：反差够硬但肩部拉得很长，大光比场景把高光压回来",
     "spec": {"black": 0.0, "white": 0.965, "gamma": 1.06, "contrast": 0.38, "shoulder": 0.72, "toe": 0.3},
     "params": {"sat": -0.5, "sharp": 1.75, "clarity": 1.25}},

    {"id": "fl40-bright-mute", "name": "胶片 40 · 明亮低饱和", "cam": "FL40-BRIGHT",
     "desc": "影调近似：整体提亮 + 轻压饱和，室内和阴天不容易闷。不含暖色",
     "spec": {"black": 0.025, "white": 0.99, "gamma": 0.88, "contrast": -0.06, "shoulder": 0.25, "toe": 0.18},
     "params": {"sat": -1.25, "sharp": 1, "clarity": 0}},
    {"id": "fl41-warm-grey", "name": "胶片 41 · 低饱和暖灰", "cam": "FL41-WGREY",
     "desc": "影调近似：抬黑压白 + 低反差，暖灰底子，纪实和街头耐看",
     "spec": {"black": 0.075, "white": 0.945, "gamma": 1.02, "contrast": -0.2, "shoulder": 0.28, "toe": 0.22},
     "params": {"sat": -2, "sharp": 1.25, "clarity": 0.25}},
    {"id": "fl42-cool-hard", "name": "胶片 42 · 冷调硬光", "cam": "FL42-CHARD",
     "desc": "影调近似：硬反差的骨架 + 压饱和，冷硬的工业感。冷色仍需白平衡配合",
     "spec": {"black": 0.0, "white": 0.98, "gamma": 1.02, "contrast": 0.5, "shoulder": 0.3, "toe": 0.35},
     "params": {"sat": -1.5, "sharp": 2, "clarity": 1}},
    {"id": "fl43-mute-portrait", "name": "胶片 43 · 低饱和人像", "cam": "FL43-MPORT",
     "desc": "影调近似：低反差 + 长趾部，暗部有细节、肤色不发红。不含肤色调整",
     "spec": {"black": 0.04, "white": 0.965, "gamma": 0.99, "contrast": -0.15, "shoulder": 0.42, "toe": 0.38},
     "params": {"sat": -1, "sharp": 0.75, "clarity": -0.25}},
    {"id": "fl44-min-grey", "name": "胶片 44 · 极简灰调", "cam": "FL44-MINGR",
     "desc": "影调近似：黑白位各收 10%、反差拉平，接近灰卡的极简底子。饱和度 -2.5",
     "spec": {"black": 0.1, "white": 0.9, "gamma": 1.0, "contrast": -0.3, "shoulder": 0.2, "toe": 0.18},
     "params": {"sat": -2.5, "sharp": 0.5, "clarity": -0.75}},
]


# --------------------------------------------------------------------------
# 用户自存配方
# --------------------------------------------------------------------------
def _user_path() -> Path:
    return data_root() / "presets.json"


def _hidden_path() -> Path:
    return data_root() / "presets_hidden.json"


def _trash_path() -> Path:
    return data_root() / "presets_trash.json"


def load_user() -> list[dict]:
    p = _user_path()
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []


def _write_user(items: list[dict]) -> None:
    global _LISTING_CACHE
    _LISTING_CACHE = None
    _user_path().write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")


# ---- 隐藏（内置 / 配方库）与回收站（自存预设）----
# 内置和配方库是随包发出去的数据，删不掉「真身」，所以对它们只做隐藏：
# 列表里不再出现，随时能恢复。用户自己存的预设才是真删——删到回收站，也能恢复。

def load_hidden() -> list[str]:
    p = _hidden_path()
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return [str(x) for x in data] if isinstance(data, list) else []
    except Exception:  # noqa: BLE001
        return []


def hide(ids: list[str]) -> int:
    global _LISTING_CACHE
    ids = [str(i) for i in ids if i]
    if not ids:
        return 0
    hidden = sorted(set(load_hidden()) | set(ids))
    _hidden_path().write_text(json.dumps(hidden, ensure_ascii=False), encoding="utf-8")
    _LISTING_CACHE = None
    return len(hidden)


def unhide(ids: list[str]) -> int:
    global _LISTING_CACHE
    drop = {str(i) for i in ids if i}
    hidden = load_hidden()
    left = [i for i in hidden if i not in drop]
    _hidden_path().write_text(json.dumps(left, ensure_ascii=False), encoding="utf-8")
    _LISTING_CACHE = None
    return len(hidden) - len(left)


def unhide_all() -> int:
    global _LISTING_CACHE
    n = len(load_hidden())
    _hidden_path().write_text("[]", encoding="utf-8")
    _LISTING_CACHE = None
    return n


def load_trash() -> list[dict]:
    p = _trash_path()
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:  # noqa: BLE001
        return []


def _write_trash(items: list[dict]) -> None:
    _trash_path().write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")


def move_to_trash(ids: list[str], limit: int = 200) -> int:
    """自存预设 → 回收站。返回真的删掉了几条。"""
    drop = {str(i) for i in ids if i}
    items = load_user()
    removed = [i for i in items if i["id"] in drop]
    if not removed:
        return 0
    _write_user([i for i in items if i["id"] not in drop])

    stamp = time.strftime("%Y-%m-%d %H:%M")
    trash = load_trash() + [{**r, "deleted": stamp} for r in removed]
    _write_trash(trash[-limit:])
    return len(removed)


def restore_from_trash(ids: list[str]) -> int:
    """回收站 → 自存预设。同名冲突的跳过（不覆盖现有配方），且留在回收站里。"""
    pick = {str(i) for i in ids if i}
    trash = load_trash()
    items = load_user()
    names = {i.get("name") for i in items}

    restored: list[dict] = []
    kept: list[dict] = []
    for t in trash:
        if t["id"] not in pick:
            kept.append(t)
            continue
        entry = {k: v for k, v in t.items()
                 if k in ("id", "name", "desc", "table", "source", "created", "builtin")}
        if entry.get("name") in names:
            kept.append(t)          # 同名的不动它，留在回收站
            continue
        restored.append(entry)
        names.add(entry.get("name"))

    if restored:
        _write_user(items + restored)
    if len(kept) != len(trash):
        _write_trash(kept)
    return len(restored)


def restore_all_trash() -> int:
    return restore_from_trash([t["id"] for t in load_trash()])


def purge_trash(ids: list[str]) -> int:
    """彻底删除：从回收站里抹掉，不可恢复。"""
    drop = {str(i) for i in ids if i}
    trash = load_trash()
    kept = [t for t in trash if t["id"] not in drop]
    if len(kept) == len(trash):
        return 0
    _write_trash(kept)
    return len(trash) - len(kept)


def purge_all_trash() -> int:
    n = len(load_trash())
    _write_trash([])
    return n


def save_user(name: str, desc: str, table: np.ndarray, source: str = "") -> dict:
    global _LISTING_CACHE
    _LISTING_CACHE = None
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
    _write_user(items[-60:])
    return entry


def delete_user(pid: str) -> bool:
    """删到回收站（可恢复），不是真删。"""
    return move_to_trash([pid]) > 0


def get(pid: str) -> dict | None:
    for p in BUILTIN:
        if p["id"] == pid:
            y = curve_from_spec(p["spec"])
            return {**p, "table": quantize_curve(y), "builtin": True}
    for p in load_user():
        if p["id"] == pid:
            return {**p, "table": np.asarray(p["table"], dtype=np.uint16), "builtin": False}
    return None


def sparkline(table: np.ndarray, n: int = 17) -> list[int]:
    """给界面上那 26×22 的小缩略曲线用：把 256 点压成 n 个 0..1000 的整数。"""
    t = np.asarray(table, dtype=np.float64) / LUT_MAX
    xs = np.linspace(0.0, 1.0, t.size)
    ys = np.interp(np.linspace(0.0, 1.0, n), xs, t)
    return [int(round(float(v) * 1000)) for v in ys]


_LISTING_CACHE: list[dict] | None = None


def listing() -> list[dict]:
    """预设列表（带小缩略曲线）。算一次缓存住，界面每次刷页不必重算 121 条曲线。"""
    global _LISTING_CACHE
    if _LISTING_CACHE is None:
        out = []
        for p in BUILTIN:
            table = quantize_curve(curve_from_spec(p["spec"]))
            out.append({
                "id": p["id"], "name": p["name"], "desc": p["desc"], "builtin": True,
                "cam": p.get("cam", ""), "params": p.get("params", {}),
                "group": "builtin", "cat": p.get("cat", "内置影调"),
                "spark": sparkline(table),
            })
        for p in load_user():
            out.append({"id": p["id"], "name": p["name"], "desc": p["desc"], "builtin": False,
                        "group": "user", "cat": "我的预设",
                        "spark": sparkline(np.asarray(p["table"], dtype=np.float64))})
        _LISTING_CACHE = out
    return _LISTING_CACHE
