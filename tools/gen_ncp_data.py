"""把 z-cloud-ai 的 BuiltInPresets.kt 里「真实配方（ncp）」转成 z30fit/ncp_data.py。

⚠️ 生成源 `thirdparty/` **不随仓库发布**（含大量品牌/型号字符串，运行时不参与）。
   要重新生成，请自行从 laoziocean-code/z-cloud-ai（MIT）取回 BuiltInPresets.kt，
   解析成 `thirdparty/parsed_presets.json` 后放进 thirdparty/ 再跑本脚本。
   `z30fit/ncp_data.py` 已经生成好并入库，日常开发不需要重跑。

来源与许可：
  - 数据来自 laoziocean-code/z-cloud-ai（MIT）的 BuiltInPresets.kt，
    其数据又来自 nikonpc.com 社区公开 NCP 库。
  - 我们只取「彩色、无色相偏移、有实质曲线」的配方，
    影调曲线 1:1 无损；饱和度/锐化等参数按真机结论如实标注。

筛选规则（见真机结论）：
  - pcd == 0（彩色族，Z30 可写）
  - hue == 0（无色相偏移，Z30 无「色相」参数）
  - len(pts) >= 3（有实质曲线，排除纯直线参数型）
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# ---- 命名：一律中性化，只描述影调，不带任何厂商/型号/产品名 ----
# 原因见 开源计划.md：预设名里出现品牌商标会给仓库带来下架风险。
# 中文名 = 「分类 序号 · 影调特征」，机身 ASCII 名 = 「分类码+序号-特征码」。
CAT_CODE = {
    "胶片": "FL", "黑白": "BW", "电影": "CN", "街拍": "ST",
    "风光": "LS", "暗调": "MD", "日系": "JP", "人像": "PT",
}

TAG_EN = {
    "灰雾": "HAZE", "提亮": "LIFT", "沉暗": "DARK", "压光": "ROLL",
    "淡彩": "MUTE", "浓彩": "VIVID", "锐利": "CRISP",
    "强反差": "HARD", "平直": "FLAT", "柔和": "SOFT",
}


def tone_tags(note: str) -> list[str]:
    """从曲线描述里提炼 1–2 个影调特征词。顺序即重要性。"""
    tags: list[str] = []
    if "灰雾" in note:
        tags.append("灰雾")
    elif "偏亮" in note:
        tags.append("提亮")
    elif "偏沉" in note:
        tags.append("沉暗")
    if "压住不飞" in note:
        tags.append("压光")
    if "饱和 -" in note:
        tags.append("淡彩")
    elif "饱和 +" in note:
        tags.append("浓彩")
    if "锐化" in note:
        tags.append("锐利")
    if not tags:
        if "接近直线" in note:
            tags = ["平直"]
        elif "暗部压" in note:
            tags = ["强反差"]
        else:
            tags = ["柔和"]
    return tags


def _is_monotone(pts: list[tuple[int, int]]) -> bool:
    """锚点插值后必须单调（机身 LUT 硬要求）。用 PCHIP 试插，检查是否单调。"""
    import sys

    sys.path.insert(0, str(ROOT))
    from z30fit.curve import MonotoneSpline  # noqa: E402

    import numpy as np

    xs = np.array([p[0] for p in pts], dtype=np.float64) / 255.0
    ys = np.array([p[1] for p in pts], dtype=np.float64) / 255.0
    _, first = np.unique(xs, return_index=True)
    xs, ys = xs[np.sort(first)], ys[np.sort(first)]
    if xs.size < 2:
        return True
    if xs[0] != 0.0:
        xs = np.concatenate([[0.0], xs])
        ys = np.concatenate([[0.0], ys])
    if xs[-1] < 1.0:
        xs = np.concatenate([xs, [1.0]])
        ys = np.concatenate([ys, [1.0]])
    try:
        yq = MonotoneSpline(xs, ys)(np.linspace(0.0, 1.0, 256))
    except Exception:
        return False
    return bool(np.all(np.diff(yq) >= 0))


def main() -> None:
    src = ROOT / "thirdparty" / "parsed_presets.json"
    if not src.exists():
        raise SystemExit(
            "缺少 thirdparty/parsed_presets.json。\n"
            "该目录不随仓库发布（含品牌字符串）。\n"
            "如需重新生成，请先按模块顶部说明取回数据；已入库的 z30fit/ncp_data.py 可直接使用。"
        )
    items = json.loads(src.read_text(encoding="utf-8"))
    keep = [x for x in items
            if x["source"] == "ncp" and x["pcd"] == 0 and x["hue"] == 0 and len(x["pts"]) >= 3]
    keep.sort(key=lambda x: x["rank"])

    dropped = [x for x in keep if not _is_monotone(x["pts"])]
    keep = [x for x in keep if _is_monotone(x["pts"])]
    if dropped:
        print("排除非单调曲线（机身 LUT 要求单调）:")
        for x in dropped:
            print(f"  {x['id']} {x['disp']}")

    counters: dict[str, int] = {}
    lines: list[str] = []
    lines.append('"""自动生成，勿手改。来源见 tools/gen_ncp_data.py。"""')
    lines.append("# 名称已做中性化处理：只描述影调，不含任何厂商/型号/产品名。")
    lines.append("")
    lines.append("NCP_PRESETS = [")
    for x in keep:
        cat = x["cat"]
        idx = counters.get(cat, 0) + 1
        counters[cat] = idx
        tags = tone_tags(x["note"])
        name = f"{cat} {idx:02d} · {'·'.join(tags[:2])}"
        # 机身 ASCII 名：分类码 + 序号 + 特征码，≤19 字符
        cam = f"{CAT_CODE.get(cat, 'PC')}{idx:02d}-{TAG_EN[tags[0]]}"
        # 描述：曲线形状说明（面向用户，不提第三方来源与技术细节）
        desc = f"{x['note']}｜真实配方，写入时曲线与参数一并进机身"
        pts = ", ".join(f"({a},{b})" for a, b in x["pts"])
        # 保留原配方参数（当前写不进机身，但信息不丢，未来破解参数位后可写）
        params = ", ".join(
            f'"{k}": {x[k]}' for k in ("sat", "sharp", "contrast", "bright", "clarity")
        )
        lines.append("    {")
        lines.append(f'        "id": "ncp-{x["id"]}",')
        lines.append(f'        "name": {name!r},')
        lines.append(f'        "cam": {cam!r},')
        lines.append(f'        "cat": {x["cat"]!r},')
        lines.append(f'        "base": {x["base"]!r},')
        lines.append(f'        "params": {{{params}}},')
        lines.append(f'        "desc": {desc!r},')
        lines.append(f'        "pts": [{pts}],')
        lines.append("    },")
    lines.append("]")
    lines.append("")

    out = ROOT / "z30fit" / "ncp_data.py"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"已生成 {out}：{len(keep)} 套")


if __name__ == "__main__":
    main()
