#!/usr/bin/env python
"""打包入口：启动本地服务，开一个独立窗口（Edge 的 --app 模式）。

为什么不用 pywebview：它依赖 .NET / WebView2 SDK，打包进去容易在别人机器上炸。
Windows 10/11 一定有 Edge，`--app=URL` 给的就是无地址栏的独立窗口，
零依赖、零额外体积。

双击 exe 后如果窗口没起来，日志在 data/app.log。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import traceback
import webbrowser
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def log(msg: str) -> None:
    try:
        from z30fit.paths import data_root

        p = data_root() / "app.log"
        with open(p, "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except Exception:  # noqa: BLE001
        pass


def find_edge() -> str | None:
    cands = [
        os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)") + r"\Microsoft\Edge\Application\msedge.exe",
        os.environ.get("PROGRAMFILES", r"C:\Program Files") + r"\Microsoft\Edge\Application\msedge.exe",
        os.environ.get("LOCALAPPDATA", "") + r"\Microsoft\Edge\Application\msedge.exe",
    ]
    for c in cands:
        if c and Path(c).exists():
            return c
    return shutil.which("msedge")


def main() -> int:
    try:
        import webui.server as srv

        httpd, port = srv.make_server(8765)
        url = f"http://127.0.0.1:{port}/"

        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        log(f"[start] {url}")

        if os.environ.get("Z30_NO_WINDOW") == "1":
            log("[test] Z30_NO_WINDOW=1，只起服务不弹窗")
            threading.Event().wait()
            return 0

        if os.environ.get("Z30_NO_WINDOW") == "1":
            log("[test] Z30_NO_WINDOW=1，只起服务不弹窗")
            threading.Event().wait()
            return 0

        edge = find_edge()
        if edge:
            # --app：无地址栏的独立窗口；--window-size 给个舒服的默认尺寸
            subprocess.Popen([edge, f"--app={url}", "--window-size=1380,920"])
        else:
            webbrowser.open(url)

        threading.Event().wait()  # 主线程挂着，等用户关窗口
    except Exception:  # noqa: BLE001
        log(traceback.format_exc())
        raise
    return 0


if __name__ == "__main__":
    sys.exit(main())
