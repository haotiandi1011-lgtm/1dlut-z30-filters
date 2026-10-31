#!/usr/bin/env python
"""只读探针：把 Z30 机身里 C1–C9 的载荷原样读出来并拆开看。

**这个脚本只发读指令（0x90CC），绝不写入。** 相机上不会发生任何改变。

用法：
    python probe.py --list                 # 只看有没有连上，不握手
    python probe.py                        # 读全部 9 个槽位，存到 out/probe/
    python probe.py --slots C9             # 只读 C9
    python probe.py --compare a.bin b.bin  # 对比两份 dump 的差异

Windows 前置：先装 pyusb；相机需用 Zadig 换成 WinUSB 驱动。详见 Phase1 文档。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from z30fit import payload as pl

ROOT = Path(__file__).parent
OUT = ROOT / "out" / "probe"

REGIONS = [
    (0x000, 0x002, "版本"),
    (0x002, 0x016, "名称"),
    (0x016, 0x02F, "标志/参数"),
    (0x02F, 0x037, "控制点"),
    (0x037, 0x066, "参数区"),
    (0x066, 0x266, "LUT 段"),
    (0x266, 0x266, "尾部"),
]


def region_of(off: int) -> str:
    for lo, hi, name in REGIONS:
        if lo <= off < hi:
            return name
    return "越界"


def do_compare(a_path: Path, b_path: Path) -> int:
    a, b = pl.load(a_path), pl.load(b_path)
    print(f"A {a_path}  {len(a)} 字节")
    print(f"B {b_path}  {len(b)} 字节")

    # 512 字节的是裸 LUT 段（不是完整载荷），按曲线比更实在
    if len(a) == pl.LUT_BYTES and len(b) == pl.LUT_BYTES:
        import numpy as np

        ta = np.frombuffer(a, dtype=">u2").astype(np.int64)
        tb = np.frombuffer(b, dtype=">u2").astype(np.int64)
        d = np.abs(ta - tb)
        worst = int(np.argmax(d))
        print("按裸 LUT 段对比：")
        print(f"  最大差 {int(d.max())} / 32767（出现在第 {worst} 点）")
        print(f"  平均差 {float(d.mean()):.1f}")
        print(f"  首值 {int(ta[0])} vs {int(tb[0])}；末值 {int(ta[-1])} vs {int(tb[-1])}")
        return 0

    diffs = pl.diff_payloads(a, b)
    if not diffs:
        print("两份载荷完全一致。")
        return 0

    by_region: dict[str, list[tuple[int, int, int]]] = {}
    for off, x, y in diffs:
        by_region.setdefault(region_of(off), []).append((off, x, y))

    print(f"差异字节：{len(diffs)} 个")
    for region, items in by_region.items():
        offs = ", ".join(f"0x{o:03X}" for o, _, _ in items[:12])
        more = "" if len(items) <= 12 else f" …共 {len(items)} 处"
        print(f"  {region:8s} {len(items):3d} 处：{offs}{more}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="只读探针：dump Z30 的 C1–C9 载荷")
    ap.add_argument("--list", action="store_true", help="只枚举设备，不握手")
    ap.add_argument("--slots", nargs="*", default=None, help="要读的槽位，如 C1 C9（默认全部）")
    ap.add_argument("--compare", nargs=2, metavar=("A", "B"), help="对比两份 dump")
    ap.add_argument("--out", default=str(OUT), help="保存目录（默认 out/probe/）")
    args = ap.parse_args(argv)

    if args.compare:
        return do_compare(Path(args.compare[0]), Path(args.compare[1]))

    try:
        import z30ptp
    except ImportError as exc:
        print(f"× {exc}")
        print("\n先装依赖： python -m pip install pyusb")
        return 2

    if args.list:
        devs = z30ptp.list_devices()
        if not devs:
            print("没找到 Nikon 相机（VID 0x04B0）。")
            print("检查：相机是否开机 / USB 模式是否 MTP / 线是否插好。")
            return 1
        print("找到：")
        for d in devs:
            print(f"  {d}")
        print("\n能看到设备不等于能发指令——若下面读槽位报「Access denied」，")
        print("说明驱动还是 WPD，需要用 Zadig 换成 WinUSB。")
        return 0

    slots = [z30ptp.slot_from_name(s) for s in args.slots] if args.slots else list(
        range(z30ptp.SLOT_FIRST, z30ptp.SLOT_FIRST + z30ptp.SLOT_COUNT)
    )

    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)

    print("连接相机…")
    with z30ptp.PtpCamera.open() as cam:
        print()
        report: list[str] = ["# 相机槽位 dump", ""]
        for slot in slots:
            name = z30ptp.slot_name(slot)
            try:
                data = cam.get_pict_ctrl(slot)
            except z30ptp.PtpError as exc:
                print(f"[{name}] 读取失败：{exc}")
                report.append(f"## {name}（0x{slot:04X}）\n\n读取失败：{exc}\n")
                continue
            except Exception as exc:  # noqa: BLE001
                print(f"[{name}] 读取异常：{type(exc).__name__}: {exc}")
                continue

            path = pl.save(outdir / f"slot_{name}.bin", data)
            print(f"[{name}] 0x{slot:04X}  {len(data)} 字节  → {path.name}")
            if len(data) < pl.PAYLOAD_SIZE:
                print(f"       ⚠ 只有 {len(data)} 字节，不满 614，可能这个槽是空的")
                report.append(f"## {name}（0x{slot:04X}）\n\n长度 {len(data)} 字节（不满 614，槽位可能是空的）\n")
                continue

            info = pl.describe_payload(data)
            for line in info.splitlines():
                print(f"       {line}")
            report.append(f"## {name}（0x{slot:04X}）\n\n```\n{info}\n```\n")
            print()

        (outdir / "probe_report.md").write_text("\n".join(report), encoding="utf-8")
        print(f"报告：{outdir / 'probe_report.md'}")

    print("\n下一步：把 out/probe/ 里的 bin 发我（或者直接说结果），我按真机字节改 Phase 2。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
