#!/usr/bin/env python
"""z30fit —— 把任意风格参考拟合成尼康 Z30 的 1D LUT（Phase 1，离线，不碰相机）。

用法示例：
    python fit.py samples/warm_soft_3d.cube
    python fit.py samples/teal_orange_3d.cube --mode neutral
    python fit.py "output/backup/example.np3"
    python fit.py --demo
    python fit.py --inspect "output/backup/PICCON01_相机原厂导出_248B.np3"

每个输入会产出 out/<name>/：
    lut_segment.bin   512 字节（256 × u16 大端）
    curve.csv         256 行曲线数据
    fitted_1d.cube    同曲线 1D LUT，供第三方软件交叉验证
    report.md         偏差报告
    preview_*.png     视觉对比图
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

from z30fit import cube as cube_mod
from z30fit import np3 as np3_mod
from z30fit.fit1d import fit_curve_direct, fit_lut
from z30fit.metrics import verdict
from z30fit.report import write_index, write_sample_outputs

ROOT = Path(__file__).parent
DEFAULT_OUT = ROOT / "out"  # 源码运行时的默认目录；打包后由 webui 走 data_root()


def slugify(name: str) -> str:
    keep = []
    for ch in name:
        if ch.isalnum() or ch in "._-":
            keep.append(ch)
        elif ch in " （）()":
            keep.append("_")
    out = "".join(keep).strip("._")
    while "__" in out:
        out = out.replace("__", "_")
    return out[:60] or "sample"


def inspect_np3(path: Path) -> None:
    f = np3_mod.parse(path)
    info = f.describe()
    print(f"[inspect] {path}")
    for k, v in info.items():
        print(f"  {k:14s}: {v}")
    got = np3_mod.extract_curve(f)
    if got is None:
        print("  curve         : 未找到曲线段（这类文件只有参数记录）")
    else:
        curve, meta = got
        print(f"  curve         : {meta['points']} 点，偏移 0x{meta['offset']:03X}，"
              f"首值 {meta['first']}，末值 {meta['last']}，零点前缀 {meta['zero_prefix']}")


def process(path: Path, outroot: Path, args) -> dict | None:
    suffix = path.suffix.lower()
    extra: dict = {}

    if suffix == ".cube":
        lut = cube_mod.load_cube(path)
        extra["TITLE"] = lut.title or "（无）"
        result = fit_lut(lut, source=str(path), mode=args.mode, knots=args.knots, extra=extra)
        ref_lut = lut
    elif suffix == ".np3":
        f = np3_mod.parse(path)
        extra["版本"] = f.version
        extra["内部名"] = f.name or "（空）"
        extra["基础校准 ID"] = f"0x{(f.base_id or 0):04X}"
        extra["记录数"] = len(f.records)
        got = np3_mod.extract_curve(f)
        if got is None:
            print(f"  ! {path.name} 没有内嵌曲线段（只有参数记录），跳过拟合")
            return None
        curve, meta = got
        extra["曲线来源"] = f"文件偏移 0x{meta['offset']:03X}，{meta['points']} 点 u16BE"
        extra["说明"] = meta["note"]
        curve256 = np3_mod.resample_curve(curve, 256)
        result = fit_curve_direct(curve256, source=str(path), knots=args.knots, extra=extra)
        ref_lut = None
    else:
        print(f"  ! 不支持的格式：{path.suffix}（只认 .cube / .np3）")
        return None

    outdir = outroot / slugify(path.stem)
    write_sample_outputs(result, ref_lut, outdir, previews=not args.no_preview)

    print(
        f"  → {outdir.name}/ : 策略={result.label} "
        f"ΔE mean={result.metrics['mean']:.2f} P95={result.metrics['p95']:.2f} "
        f"黑位={result.tone['black'] * 100:.1f}% 白位={result.tone['white'] * 100:.1f}%"
    )
    print(f"    {verdict(result.metrics)}")

    return {
        "name": path.stem,
        "dir": outdir.name,
        "kind": result.lut_kind,
        "mode": result.label,
        "mean": result.metrics["mean"],
        "p95": result.metrics["p95"],
        "flat": result.banding["max_flat_run"],
        "artifact": bool(result.banding["artifact_flats"]),
        "verdict": verdict(result.metrics),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="把风格参考拟合成 Z30 1D LUT")
    ap.add_argument("inputs", nargs="*", help=".cube 或 .np3 文件")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="输出根目录（默认 out/）")
    ap.add_argument("--mode", default="auto", choices=["auto", "neutral", "channels", "hybrid"])
    ap.add_argument("--knots", type=int, default=33, help="样条节点数（越大越贴合，越容易出断层）")
    ap.add_argument("--no-preview", action="store_true", help="跳过对比图，只出数据和报告")
    ap.add_argument("--demo", action="store_true", help="跑内置全部样本")
    ap.add_argument("--inspect", action="store_true", help="只打印 NP3 结构，不拟合")
    args = ap.parse_args(argv)

    outroot = Path(args.out)
    targets: list[Path] = [Path(p) for p in args.inputs]

    if args.demo:
        targets = sorted((ROOT / "samples").glob("*.cube"))
        backup = ROOT / "output" / "backup"
        if backup.exists():
            seen: set[str] = set()
            for p in sorted(backup.iterdir()):
                if p.suffix.lower() == ".np3" and p.name.lower() not in seen:
                    seen.add(p.name.lower())
                    targets.append(p)
    if not targets:
        ap.print_help()
        return 1

    if args.inspect:
        for p in targets:
            if p.suffix.lower() == ".np3":
                inspect_np3(p)
            else:
                print(f"[inspect] {p}：不是 NP3，跳过")
        return 0

    outroot.mkdir(parents=True, exist_ok=True)
    rows = []
    for p in targets:
        if not p.exists():
            print(f"  ! 找不到文件：{p}")
            continue
        print(f"[fit] {p.name}")
        row = process(p, outroot, args)
        if row:
            rows.append(row)

    if rows:
        index = write_index(rows, outroot)
        print(f"\n总览：{index}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
