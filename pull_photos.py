#!/usr/bin/env python
"""从相机里把照片拷到本地（只读，走 PTP 0x1009）。

    python pull_photos.py                # 下载最新的 JPEG
    python pull_photos.py --n 5          # 最新 5 张
    python pull_photos.py --all          # 连 NEF 一起下

为什么需要它：相机换成 WinUSB 驱动后，Windows 资源管理器看不到相机，
没法直接拷文件。这个脚本用 PTP 自己取。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).parent
OUT = ROOT / "out" / "photos"

FMT_LABELS = {0x3801: "JPEG", 0x3000: "NEF", 0x3001: "TIFF"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="从相机拷贝照片（只读）")
    ap.add_argument("--n", type=int, default=3, help="下载最新几张 JPEG（默认 3）")
    ap.add_argument("--all", action="store_true", help="连 RAW 一起下")
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args(argv)

    import z30ptp

    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)

    with z30ptp.PtpCamera.open() as cam:
        items: list[dict] = []
        for sid in cam.storage_ids():
            for h in cam.object_handles(sid):
                try:
                    items.append(cam.object_info(h))
                except Exception:  # noqa: BLE001
                    continue

        files = [i for i in items if i["format"] in FMT_LABELS]
        if not args.all:
            files = [i for i in files if i["format"] == 0x3801]
        files.sort(key=lambda i: i["filename"])
        files = files[-args.n :]

        if not files:
            print("相机里没有找到可下载的文件")
            return 1

        print(f"准备下载 {len(files)} 个文件：")
        for f in files:
            print(f"  {f['filename']:16s} {FMT_LABELS[f['format']]:4s} {f['size'] / 1e6:6.1f} MB  {f['date']}")

        for f in files:
            target = outdir / f["filename"]
            if target.exists() and target.stat().st_size == f["size"]:
                print(f"  跳过（已存在）{target.name}")
                continue
            print(f"  下载 {f['filename']} …", end="", flush=True)
            data = cam.get_object(f["handle"])
            target.write_bytes(data)
            ok = "✓" if len(data) == f["size"] else f"⚠ 期望 {f['size']} 实收 {len(data)}"
            print(f" {len(data) / 1e6:.1f} MB {ok}")

    print(f"\n目录：{outdir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
