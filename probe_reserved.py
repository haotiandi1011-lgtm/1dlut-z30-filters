#!/usr/bin/env python
"""探测 614 字节载荷里 +0x037..+0x065 那 47 字节保留区的用途。

方法：LUT 固定用四级台阶曲线（0/85/170/255）当"刻度尺"，只改保留区的字节，
拍出来的照片里台阶位置一动就说明那些字节参与了成像。

    python probe_reserved.py --pattern ff --dry-run    # 只看载荷
    python probe_reserved.py --pattern ff --commit     # 写入 C9
    python probe_reserved.py --range 0x037-0x045 --pattern 0x40 --commit
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

from z30fit import payload as pl

ROOT = Path(__file__).parent
OUT = ROOT / "out" / "reserved"
STEP_LUT = ROOT / "out" / "write" / "step4.bin"
TARGET_SLOT = 0x00D1  # C9

RESERVED_START = 0x037
RESERVED_END = 0x066  # 不含


def parse_range(text: str) -> tuple[int, int]:
    if "-" in text:
        a, b = text.split("-", 1)
        return int(a, 0), int(b, 0) + 1
    v = int(text, 0)
    return v, v + 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="探测载荷保留区用途")
    ap.add_argument("--pattern", default="ff", help="填入的字节值（十六进制，如 ff / 40）")
    ap.add_argument("--range", default=f"0x{RESERVED_START:03X}-0x{RESERVED_END - 1:03X}",
                    help="要改的区间，默认整块")
    ap.add_argument("--commit", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--name", default="TEST-RSV")
    args = ap.parse_args(argv)

    if not (args.commit or args.dry_run):
        print("二选一：--dry-run 或 --commit")
        return 2

    lo, hi = parse_range(args.range)
    if not (RESERVED_START <= lo < hi <= RESERVED_END):
        print(f"× 区间 {lo:#x}-{hi - 1:#x} 超出保留区 {RESERVED_START:#x}-{RESERVED_END - 1:#x}")
        return 2

    val = int(args.pattern, 16) & 0xFF
    curve = np.frombuffer(STEP_LUT.read_bytes(), dtype=">u2").astype(np.float64) / 32767.0

    data = bytearray(pl.build_payload_614(curve, name=args.name, family=0x05))
    for i in range(lo, hi):
        data[i] = val

    OUT.mkdir(parents=True, exist_ok=True)
    tag = f"{lo:03x}-{hi - 1:03x}_{val:02x}"
    path = pl.save(OUT / f"payload_{tag}.bin", bytes(data))

    print(f"保留区 {lo:#05x}..{hi - 1:#05x} 共 {hi - lo} 字节 → 0x{val:02X}")
    print(f"载荷 → {path}")
    print(f"名称 {pl.parse_payload(bytes(data))['name']}   LUT 首尾 "
          f"{pl.parse_payload(bytes(data))['lut_first']}/{pl.parse_payload(bytes(data))['lut_last']}")

    if args.dry_run:
        return 0

    import time

    import z30ptp

    with z30ptp.PtpCamera.open() as cam:
        original = cam.get_pict_ctrl(TARGET_SLOT)
        stamp = time.strftime("%H%M%S")
        pl.save(OUT / f"backup_{stamp}.bin", original)
        try:
            cam.set_pict_ctrl(TARGET_SLOT, bytes(data))
        except z30ptp.PtpError as exc:
            print(f"× 写入被拒：{exc}")
            return 5
        time.sleep(0.3)
        back = cam.get_pict_ctrl(TARGET_SLOT)
        pl.save(OUT / f"readback_{tag}.bin", back)

    if back == bytes(data):
        print("✅ 读回一致，可以拍了")
        return 0
    print(f"⚠️ 读回 {len(back)} 字节，不一致")
    return 8


if __name__ == "__main__":
    sys.exit(main())
