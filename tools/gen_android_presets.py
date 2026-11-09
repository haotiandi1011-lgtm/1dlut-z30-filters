"""把桌面端的全部预设打包成安卓端内置资源。

    python tools/gen_android_presets.py

产物：`android/app/src/main/assets/builtin_presets.json`

为什么在桌面端生成载荷而不是让手机自己拼：
  手机端的职责是「原样搬运 614 字节」。载荷在这里用 `z30fit.payload` 造好，
  和桌面端写机身走的是同一条代码路径，手机端只是把字节换个名字发出去——
  少一个实现分支，就少一处「手机写出来和电脑不一样」的可能。

载荷里已经带好：
  · 名称（ASCII，机身字段只认这个）
  · 4 组控制点（机身 LCD 画曲线用）
  · 512 字节 LUT（成像真值）
  · 参数区（锐化 / 清晰度 / 饱和度）

命名红线见 `产品规划.md` §9：只描述影调，不含任何厂商商标与胶片型号名。
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from z30fit import LUT_MAX  # noqa: E402
from z30fit import payload as pl  # noqa: E402
from z30fit import presets as builtin  # noqa: E402
from z30fit import ncp_presets  # noqa: E402

OUT = ROOT / "android" / "app" / "src" / "main" / "assets" / "builtin_presets.json"
VERSION = 1

# 预设里的参数名 → 载荷参数名。对比度/亮度有自定义曲线时被机身禁用，不写。
PARAM_MAP = {"sat": "saturation", "saturation": "saturation", "sharp": "sharp", "clarity": "clarity"}


def payload_params(raw: dict | None) -> dict:
    out: dict[str, float] = {}
    for key, value in (raw or {}).items():
        target = PARAM_MAP.get(key)
        if target and value is not None:
            out[target] = float(value)
    return out


def make_entry(pid: str, name: str, cat: str, desc: str, cam: str, table, raw_params: dict | None) -> dict:
    params = payload_params(raw_params)
    payload = pl.build_payload_614(
        table.astype("float64") / LUT_MAX,
        name=cam,
        family=0x05,
        params=params or None,
    )
    if len(payload) != pl.PAYLOAD_SIZE:
        raise SystemExit(f"× {pid} 载荷长度异常：{len(payload)}")
    info = pl.parse_payload(payload)
    if not info["lut_monotone"]:
        raise SystemExit(f"× {pid} 曲线非单调，机身会画错")
    if info["name"] != cam:
        raise SystemExit(f"× {pid} 名称写进载荷后对不上：{info['name']!r} != {cam!r}")
    return {
        "id": pid,
        "name": name,
        "cat": cat,
        "desc": desc,
        "cam": cam,
        "params": params,
        "payload": base64.b64encode(payload).decode("ascii"),
    }


def main() -> int:
    items: list[dict] = []

    # 1) 内置影调（参数化生成 + 手调那一族）
    for p in builtin.BUILTIN:
        items.append(make_entry(
            p["id"], p["name"], p.get("cat", "内置影调"), p["desc"],
            p["cam"], builtin.get(p["id"])["table"], p.get("params"),
        ))

    # 2) 配方库（社区沉淀的经典配方，曲线 1:1）
    for p in ncp_presets.listing():
        full = ncp_presets.get(p["id"])
        items.append(make_entry(
            p["id"], p["name"], p.get("cat", "配方库"), p["desc"],
            p["cam"], full["table"], p.get("params"),
        ))

    # 机身名不能撞（同一个槽位两个同名预设，用户分不清）
    cams = [i["cam"] for i in items]
    dup = {c for c in cams if cams.count(c) > 1}
    if dup:
        raise SystemExit(f"× 机身名重复：{sorted(dup)}")

    doc = {
        "version": VERSION,
        "note": "内置预设：曲线与参数已封装成 614 字节载荷，手机端只做搬运，不做拟合。",
        "count": len(items),
        "presets": items,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    size = OUT.stat().st_size
    shown = OUT.relative_to(ROOT) if OUT.is_relative_to(ROOT) else OUT
    print(f"✓ {shown}")
    print(f"  {len(items)} 套 · {size / 1024:.0f} KB")
    cats: dict[str, int] = {}
    for i in items:
        cats[i["cat"]] = cats.get(i["cat"], 0) + 1
    print("  " + " / ".join(f"{k} {v}" for k, v in cats.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
