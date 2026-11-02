"""出包后的验收检查：确认 APK 里**有**手机端拟合、内置预设仍然是全量 121 套。

历史：0.2.0 及之前，拟合是半成品，出包要走 `make_android_clean_copy.py`
（复制 android/ → out/android-clean/，把拟合代码摘掉再编译）。0.2.0 起拟合正式进包，
那条路已退役，改为在打出的 APK 上做正向检查：

    1. dex 里能搜到 PhotoFit / FitUi —— 拟合代码在包里；
    2. assets/builtin_presets.json 的 count == presets 数 == 121 —— 内置预设没缺；
    3. 报告 APK 大小与版本。

用法：
    python tools/check_android_apk.py                 # 默认检查 android 里的 debug 包
    python tools/check_android_apk.py path/to/apk     # 指定 APK
"""

from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_APK = ROOT / "android" / "app" / "build" / "outputs" / "apk" / "debug" / "app-debug.apk"

FIT_MARKERS = [b"core/fit/PhotoFit", b"FitUi", b"photoPicker"]
PRESET_COUNT = 121


def check(apk: Path) -> int:
    if not apk.exists():
        print(f"× 找不到 APK：{apk}")
        return 1

    problems: list[str] = []
    size_mb = apk.stat().st_size / 1024 / 1024
    print(f"APK  {apk.name}  ({size_mb:.1f} MB)")

    with zipfile.ZipFile(apk) as zf:
        names = zf.namelist()

        dexes = [n for n in names if n.startswith("classes") and n.endswith(".dex")]
        if not dexes:
            problems.append("包里没有 classes*.dex")
        blob = b"".join(zf.read(n) for n in dexes)
        for marker in FIT_MARKERS:
            ok = marker in blob
            print(f"{'✓' if ok else '×'} dex 含 {marker.decode()}")
            if not ok:
                problems.append(f"dex 里搜不到 {marker.decode()}（拟合代码没进包？）")

        asset = "assets/builtin_presets.json"
        if asset in names:
            data = json.loads(zf.read(asset).decode("utf-8"))
            count, presets = data.get("count"), len(data.get("presets", []))
            ok = count == presets == PRESET_COUNT
            print(f"{'✓' if ok else '×'} 内置预设 count={count} presets={presets}（应为 {PRESET_COUNT}）")
            if not ok:
                problems.append("内置预设数量不对：改了预设要跑 python tools/gen_android_presets.py")
        else:
            problems.append(f"包里没有 {asset}")

    if problems:
        print("\n× 验收未通过：")
        for p in problems:
            print(f"  - {p}")
        return 1

    print("\n✓ 验收通过：拟合在包里，内置预设齐全")
    return 0


def main() -> int:
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    return check(Path(arg) if arg else DEFAULT_APK)


if __name__ == "__main__":
    sys.exit(main())
