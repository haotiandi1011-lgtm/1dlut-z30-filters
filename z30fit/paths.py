"""路径解析：同时支持「源码运行」和「PyInstaller 打包后运行」。

打包后：
  · 只读资源（index.html / samples）在 sys._MEIPASS 里
  · 可写数据（拟合产物、备份、上传）放在 exe 同级的 data/ 目录，
    不能写进 _MEIPASS（那是临时解压目录，退出就没了）
"""

from __future__ import annotations

import sys
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))


def app_root() -> Path:
    """只读资源的根目录。"""
    if FROZEN:
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


def data_root() -> Path:
    """可写数据的根目录。"""
    if FROZEN:
        base = Path(sys.executable).parent / "data"
    else:
        base = app_root() / "out"
    base.mkdir(parents=True, exist_ok=True)
    return base


def resource(*parts: str) -> Path:
    return app_root().joinpath(*parts)


def writable(*parts: str) -> Path:
    p = data_root().joinpath(*parts)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p
