#!/usr/bin/env python
"""向 C9 写入 614 字节优化校准载荷（最小变量试验）。

默认只生成载荷、不写入。加 --commit 才真的发指令。

    python write_test.py --dry-run
    python write_test.py --commit
    python write_test.py --commit --curve out/punch_s_1d/lut_segment.bin --name TEST-S

安全设计：
  · 只写 C9（0x00D1），不遍历、不自动重试
  · 写前必须确认 C9 是全零空槽（--force 可跳过）
  · 写前先把原内容存到 out/write/backup_C9_*.bin
  · 写完立刻读回逐字节比对，不一致就报错不做任何补救动作
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np

from z30fit import payload as pl

ROOT = Path(__file__).parent
OUT = ROOT / "out" / "write"
TARGET_SLOT = 0x00D1  # C9
TARGET_NAME = "C9"


def load_curve(path: str | None) -> tuple[np.ndarray, str]:
    """返回 (256 点曲线 0..1, 说明)。没给路径就用恒等曲线。"""
    if not path:
        return np.linspace(0.0, 1.0, 256), "恒等曲线（不改变画面）"

    p = Path(path)
    if p.suffix.lower() == ".bin":
        raw = p.read_bytes()
        if len(raw) != pl.LUT_BYTES:
            raise ValueError(f"{p.name} 不是 512 字节 LUT 段")
        table = np.frombuffer(raw, dtype=">u2")
        return table.astype(np.float64) / 32767.0, f"512 字节 LUT 段：{p}"

    if p.suffix.lower() == ".cube":
        from z30fit import cube as cube_mod

        lut = cube_mod.load_cube(p)
        _, out = lut.neutral_ramp(256)
        from z30fit import color

        return color.luma(out), f".cube 中性轴：{p}"

    raise ValueError(f"不认识的格式：{p.suffix}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="向 C9 写入 614 字节优化校准载荷")
    ap.add_argument("--dry-run", action="store_true", help="只生成载荷，不连相机")
    ap.add_argument("--commit", action="store_true", help="真的写入（危险）")
    ap.add_argument("--curve", default=None, help="512 字节 LUT 段或 .cube；默认恒等曲线")
    ap.add_argument("--name", default="TEST-ID", help="写进机身的名称（≤19 字符 ASCII）")
    ap.add_argument("--family", type=lambda s: int(s, 0), default=0x05, help="族标记，默认 0x05")
    ap.add_argument("--force", action="store_true", help="即使 C9 不是空槽也写")
    args = ap.parse_args(argv)

    if not args.dry_run and not args.commit:
        print("二选一：--dry-run（只生成）或 --commit（真写）")
        return 2

    curve, curve_desc = load_curve(args.curve)
    data = pl.build_payload_614(curve, name=args.name, family=args.family)

    OUT.mkdir(parents=True, exist_ok=True)
    path = pl.save(OUT / f"payload_{args.name}.bin", data)

    print("载荷")
    print(f"  曲线：{curve_desc}")
    print(f"  名称：{args.name}   族：0x{args.family:02X}   长度：{len(data)}")
    print(f"  已存：{path}")
    print()
    print(pl.describe_payload(data))
    print()

    if args.dry_run:
        print("（dry-run，未连接相机）")
        return 0

    import z30ptp

    print(f"连接相机，准备写 {TARGET_NAME}…")
    with z30ptp.PtpCamera.open() as cam:
        # 1) 备份
        try:
            original = cam.get_pict_ctrl(TARGET_SLOT)
        except z30ptp.PtpError as exc:
            print(f"× 读备份失败：{exc}")
            return 3
        stamp = time.strftime("%H%M%S")
        bak = pl.save(OUT / f"backup_{TARGET_NAME}_{stamp}.bin", original)
        empty = all(b == 0 for b in original)
        print(f"  备份 {len(original)} 字节 → {bak.name}（{'全零，空槽' if empty else '非空'}）")

        if not empty and not args.force:
            print("× 目标槽位不是空的，非 --force 不写")
            return 4

        # 2) 写入
        print(f"  写入 {len(data)} 字节…")
        t0 = time.time()
        try:
            cam.set_pict_ctrl(TARGET_SLOT, data)
        except z30ptp.PtpError as exc:
            print(f"× 写入被拒：{exc}")
            print("  相机状态未改变。")
            return 5
        except Exception as exc:  # noqa: BLE001
            print(f"× 写入异常：{type(exc).__name__}: {exc}")
            print("  相机可能进入了异常状态，若屏幕无响应请关机拔电池 10 秒。")
            return 6
        print(f"  写入返回 OK（耗时 {time.time() - t0:.2f}s）")

        # 3) 读回
        time.sleep(0.4)
        try:
            back = cam.get_pict_ctrl(TARGET_SLOT)
        except z30ptp.PtpError as exc:
            print(f"× 读回失败：{exc}")
            return 7
        pl.save(OUT / f"readback_{TARGET_NAME}_{stamp}.bin", back)

    print(f"  读回 {len(back)} 字节")
    if len(back) == pl.PAYLOAD_SIZE and back == data:
        print("\n✅ 逐字节完全一致：614 字节写入通道是通的")
        return 0
    if len(back) == pl.PAYLOAD_SIZE:
        diffs = pl.diff_payloads(data, back)
        print(f"\n⚠️ 长度对，但有 {len(diffs)} 处字节不同：")
        for off, a, b in diffs[:12]:
            print(f"    0x{off:03X}: 写入 {a:02x} → 读回 {b:02x}")
        return 8
    if len(back) == 36:
        print("\n⚠️ 读回 36 字节：机身把 614 字节降级成了「仅参数」记录")
        print(f"   {back.hex(' ')}")
        return 9
    print(f"\n⚠️ 读回长度异常（{len(back)} 字节），前 32 字节：")
    print(f"   {back[:32].hex(' ')}")
    return 10


if __name__ == "__main__":
    sys.exit(main())
