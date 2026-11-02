#!/usr/bin/env python
"""打包成 Windows 可执行文件（PyInstaller onedir）。

    python build.py              # 产物：dist/Z30影调工具/Z30影调工具.exe
    python build.py --out dist2  # 工具正开着时用它：先打到旁边，关掉再换

整个文件夹一起发，不要只发 exe（依赖和资源都在旁边）。
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
NAME = "Z30影调工具"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="dist", help="输出目录（默认 dist）")
    args_cli = ap.parse_args(argv)
    distpath = ROOT / args_cli.out

    try:
        import PyInstaller.__main__ as pyi
    except ImportError:
        print("× 缺少 PyInstaller：python -m pip install pyinstaller")
        return 1

    if not (ROOT / "webui" / "index.html").exists():
        print("× 找不到 webui/index.html")
        return 1

    args = [
        str(ROOT / "app.py"),
        "--name", NAME,
        "--noconfirm", "--clean",
        "--noconsole",                     # 不弹黑窗口；异常写进数据目录的 app.log
        "--add-data", f"{ROOT / 'webui' / 'index.html'};webui",
        "--add-data", f"{ROOT / 'samples'};samples",
        "--hidden-import", "webui.server",
        "--hidden-import", "usb",
        "--hidden-import", "usb.backend.libusb1",
        "--hidden-import", "usb.backend.libusb0",
        "--collect-binaries", "libusb_package",
        "--collect-submodules", "usb",
        "--distpath", str(distpath),
        "--workpath", str(ROOT / "build"),
        "--specpath", str(ROOT / "build"),
    ]

    print("打包中，第一次会比较慢（1–3 分钟）…")
    pyi.run(args)

    out = distpath / NAME
    exe = out / f"{NAME}.exe"
    if not exe.exists():
        print("× 打包失败，没找到 exe")
        return 2

    # 不再在程序目录里建 data/：默认模式下数据在系统用户目录，建了反而误导
    (out / "使用说明.txt").write_text(
        "Z30 影调工具\n"
        "============================================================\n\n"
        "【怎么启动】\n"
        "  双击 Z30影调工具.exe，会自动弹出窗口（用系统自带的 Edge 内核）。\n"
        "  相机要先用 Zadig 把驱动换成 WinUSB，否则读写不了机身。\n\n"
        "【你的数据放在哪】\n"
        "  默认：C:\\Users\\<你的用户名>\\AppData\\Local\\Z30影调工具\\\n"
        "        （预设、隐藏列表、回收站、备份、拟合产物、日志都在这里）\n"
        "  这样升级时把新版本解压到任何目录、甚至删掉旧文件夹，你的东西都还在。\n"
        "  程序里「滤镜库 → 管理」底部会显示当前的数据目录，点一下能复制路径。\n\n"
        "【想带着预设去别的电脑？两种办法，任选一种】\n"
        "  办法一 · 便携模式（推荐，连设置一起带走）\n"
        "    1. 在 Z30影调工具.exe 旁边新建一个空文件，文件名就叫  portable.txt\n"
        "       （注意是 .txt，不是 portable.txt.txt；内容是空的就行）\n"
        "    2. 重新启动程序。数据会落在同目录的 data\\ 里。\n"
        "    3. 以后把整个「Z30影调工具」文件夹拷到 U 盘 / 另一台电脑，配置跟着走。\n"
        "    想切回默认模式：删掉 portable.txt 即可（原来的默认数据还在，不受影响）。\n\n"
        "  办法二 · 只搬预设（轻量）\n"
        "    把这几个文件拷到目标电脑的同一路径下即可：\n"
        "      %LOCALAPPDATA%\\Z30影调工具\\presets.json          你的自存预设\n"
        "      %LOCALAPPDATA%\\Z30影调工具\\presets_hidden.json   哪些预设被你隐藏了\n"
        "      %LOCALAPPDATA%\\Z30影调工具\\presets_trash.json    回收站（可选）\n"
        "    目标电脑如果开了便携模式，路径改成  <程序目录>\\data\\ 下的同名文件。\n\n"
        "【升级时注意】\n"
        "  默认模式下随便替换程序文件，数据不会丢；\n"
        "  便携模式下，请把旧文件夹里的 data\\ 一起拷到新文件夹，否则配置不会跟过来\n"
        "  （首次以默认模式启动时，程序也会尝试从旧 data\\ 自动搬一次）。\n\n"
        "【出问题了看哪】\n"
        "  数据目录里的 app.log。删掉它不影响使用。\n",
        encoding="utf-8",
    )

    size = sum(f.stat().st_size for f in out.rglob("*") if f.is_file()) / 1e6
    print(f"\n✓ 完成：{exe}")
    print(f"  整个文件夹约 {size:.0f} MB，要一起发出去")
    return 0


if __name__ == "__main__":
    sys.exit(main())
