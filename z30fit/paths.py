"""路径解析：同时支持「源码运行」和「PyInstaller 打包后运行」。

可写数据（预设、隐藏列表、备份、拟合产物、上传）放哪儿，打包后分两种：

  · **默认**：`%LOCALAPPDATA%\\Z30影调工具\\`
    升级换目录、删旧文件夹都不影响用户的东西。
  · **便携模式**：exe 旁边放一个 `portable.txt`（空文件就行），数据落在同级 `data\\`，
    整个文件夹拷到别的机器，配置一起走。

源码运行时仍是仓库里的 `out\\`，不干扰开发。

首次以默认模式启动时，如果 exe 同级还留着老版本的 `data\\`（旧版就是这么存的），
会自动把里面的东西搬到新位置，并留一张 `迁移来源.txt` 说明来源。
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))

APP_NAME = "Z30影调工具"
PORTABLE_FLAG = "portable.txt"

# 老 data/ 里出现这些，才认为「有真东西要搬」
_MEANINGFUL = ("presets.json", "presets_hidden.json", "presets_trash.json", "webui", "write", "backups")


def app_root() -> Path:
    """只读资源的根目录。"""
    if FROZEN:
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


def exe_dir() -> Path:
    return Path(sys.executable).parent if FROZEN else app_root()


def is_portable() -> bool:
    """exe 旁边有 portable.txt 就是便携模式（大小写不敏感，内容随便）。"""
    try:
        base = exe_dir()
        if not base.is_dir():
            return False
        return any(p.name.lower() == PORTABLE_FLAG for p in base.iterdir())
    except OSError:
        return False


def local_app_data() -> Path:
    raw = os.environ.get("LOCALAPPDATA")
    if raw:
        return Path(raw)
    return Path.home() / "AppData" / "Local"


def resolve_data_dir(*, frozen: bool, portable: bool, exe_folder: Path,
                     appdata: Path, source_root: Path) -> Path:
    """纯函数版的数据目录决策，方便测试。"""
    if not frozen:
        return source_root / "out"
    if portable:
        return exe_folder / "data"
    return appdata / APP_NAME


def _has_content(folder: Path) -> bool:
    if not folder.is_dir():
        return False
    return any((folder / name).exists() for name in _MEANINGFUL)


def _migrate(old: Path, new: Path) -> bool:
    """把老 data/ 里还缺的东西搬到新位置（新位置已有的文件不覆盖）。"""
    if not _has_content(old) or _has_content(new):
        return False
    new.mkdir(parents=True, exist_ok=True)
    moved = 0
    for item in old.iterdir():
        target = new / item.name
        if target.exists():
            continue
        try:
            if item.is_dir():
                shutil.copytree(item, target)
            else:
                shutil.copy2(item, target)
            moved += 1
        except OSError:
            continue
    if moved:
        (new / "迁移来源.txt").write_text(
            f"这些数据是从旧版本的 data 目录搬过来的：\n{old}\n\n"
            "旧目录没有删，确认新版本一切正常后可以自行清理。\n",
            encoding="utf-8",
        )
    return moved > 0


_CACHE: Path | None = None


def data_root() -> Path:
    """可写数据的根目录（首次调用时可能触发一次老数据迁移）。"""
    global _CACHE
    if _CACHE is not None:
        return _CACHE

    target = resolve_data_dir(
        frozen=FROZEN,
        portable=is_portable(),
        exe_folder=exe_dir(),
        appdata=local_app_data(),
        source_root=app_root(),
    )
    target.mkdir(parents=True, exist_ok=True)

    if FROZEN:
        old = exe_dir() / "data"
        if old.resolve() != target.resolve():
            _migrate(old, target)

    _CACHE = target
    return target


def resource(*parts: str) -> Path:
    return app_root().joinpath(*parts)


def writable(*parts: str) -> Path:
    p = data_root().joinpath(*parts)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p
