#!/usr/bin/env python
"""打包成 Windows 可执行文件（PyInstaller onedir）。

    python build.py

产物：dist/Z30影调工具/Z30影调工具.exe
整个文件夹一起发，不要只发 exe（依赖和资源都在旁边）。
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
NAME = "Z30影调工具"


def main() -> int:
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
        "--noconsole",                     # 不弹黑窗口；异常写进 data/app.log
        "--add-data", f"{ROOT / 'webui' / 'index.html'};webui",
        "--add-data", f"{ROOT / 'samples'};samples",
        "--hidden-import", "webui.server",
        "--hidden-import", "usb",
        "--hidden-import", "usb.backend.libusb1",
        "--hidden-import", "usb.backend.libusb0",
        "--collect-binaries", "libusb_package",
        "--collect-submodules", "usb",
        "--distpath", str(ROOT / "dist"),
        "--workpath", str(ROOT / "build"),
        "--specpath", str(ROOT / "build"),
    ]

    print("打包中，第一次会比较慢（1–3 分钟）…")
    pyi.run(args)

    out = ROOT / "dist" / NAME
    exe = out / f"{NAME}.exe"
    if not exe.exists():
        print("× 打包失败，没找到 exe")
        return 2

    # 给用户一个空的 data 目录，避免首次运行权限问题
    (out / "data").mkdir(exist_ok=True)
    (out / "使用说明.txt").write_text(
        "Z30 影调工具\n\n"
        "1. 双击 Z30影调工具.exe\n"
        "2. 会自动弹出窗口（用的是系统自带的 Edge）\n"
        "3. 相机要先用 Zadig 把驱动换成 WinUSB，否则读写不了机身\n\n"
        "数据都在同目录的 data 文件夹里：拟合产物、备份、日志。\n"
        "删掉 data 不影响程序运行。\n",
        encoding="utf-8",
    )

    size = sum(f.stat().st_size for f in out.rglob("*") if f.is_file()) / 1e6
    print(f"\n✓ 完成：{exe}")
    print(f"  整个文件夹约 {size:.0f} MB，要一起发出去")
    return 0


if __name__ == "__main__":
    sys.exit(main())
