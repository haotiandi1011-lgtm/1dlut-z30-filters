"""偏差报告（markdown）与视觉对比图生成。"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

import numpy as np

from . import LUT_MAX
from . import images as img_mod
from .cube import CubeLUT
from .fit1d import FitResult
from .metrics import verdict
from .np3 import PAYLOAD_LUT_OFFSET, PAYLOAD_LUT_BYTES


def _fmt(x: float, nd: int = 2) -> str:
    return f"{x:.{nd}f}"


def _hexlines(data: bytes, per_line: int = 16, limit: int = 4, base: int = 0) -> list[str]:
    lines = []
    for i in range(0, min(len(data), per_line * limit), per_line):
        chunk = data[i : i + per_line]
        lines.append(f"`0x{base + i:03X}  {chunk.hex(' ')}`")
    return lines


# --------------------------------------------------------------------------
# 视觉对比
# --------------------------------------------------------------------------
def render_previews(result: FitResult, lut: CubeLUT | None, outdir: str | Path) -> dict[str, Path]:
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}

    samples = {
        "chart": img_mod.synth_chart(),
        "scene": img_mod.synth_scene(),
        "gradient": img_mod.synth_gradient(),
    }
    titles = {
        "chart": img_mod.text("色卡 · 灰阶 · 肤色", "Chart / ramp / skin"),
        "scene": img_mod.text("合成风景（天空 / 植被 / 高光 / 阴影）", "Synthetic scene"),
        "gradient": img_mod.text("连续渐变（断层体检）", "Smooth gradient (banding test)"),
    }

    fitted = img_mod.apply_curve

    for key, base in samples.items():
        panels: list[tuple[str, np.ndarray]] = [(img_mod.text("原图", "Original"), base)]
        after = fitted(base, result.curve)
        if lut is not None:
            ref = lut.apply(base)
            got = after
            panels.append((img_mod.text("参考 LUT", "Reference LUT"), ref))
            panels.append((img_mod.text("拟合 1D", "Fitted 1D"), got))
            panels.append((img_mod.text("差异 ×8（已提亮）", "Diff x8 (gamma-lifted)"), img_mod.diff_panel(ref, got, 8.0)))
        else:
            panels.append((img_mod.text("拟合 1D", "Fitted 1D"), after))
        paths[key] = img_mod.strip(outdir / f"preview_{key}.png", panels, title=titles[key])

        # 前端预览台要的是两张尺寸完全一致、不带标签的图，才能做拖动对比
        paths[f"cmp_{key}_a"] = img_mod.save_png(outdir / f"cmp_{key}_a.png", base)
        paths[f"cmp_{key}_b"] = img_mod.save_png(outdir / f"cmp_{key}_b.png", after)

    # 曲线图
    xs = np.linspace(0.0, 1.0, 256)
    series = [
        (img_mod.text("恒等", "identity"), xs, xs, (200, 200, 196)),
    ]
    if lut is not None:
        _, ref_out = lut.neutral_ramp(256)
        series.append((img_mod.text("参考（中性轴）", "reference neutral"), xs, ref_out.mean(axis=1), (90, 120, 220)))
    series.append((img_mod.text("拟合曲线", "fitted"), xs, result.table_0_1, (200, 60, 50)))
    paths["curve"] = img_mod.plot_curves(
        outdir / "curve.png", series, title=img_mod.text("影调曲线", "tone curve")
    )
    return paths


# --------------------------------------------------------------------------
# 报告
# --------------------------------------------------------------------------
def build_markdown(result: FitResult, lut: CubeLUT | None, previews: dict[str, Path], outdir: Path) -> str:
    m = result.metrics
    b = result.banding
    t = result.tone
    now = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")

    def rel(p: Path) -> str:
        return p.relative_to(outdir).as_posix()

    lines: list[str] = []
    lines.append(f"# 拟合报告 · {Path(result.source).name or result.source}")
    lines.append("")
    lines.append(f"> 生成时间 {now} · z30fit Phase 1（离线拟合，未接触相机）")
    lines.append("")

    lines.append("## 结论")
    lines.append("")
    lines.append(f"**{verdict(m)}**")
    lines.append("")
    lines.append(
        f"ΔE2000 平均 **{_fmt(m['mean'])}**、中位 {_fmt(m['median'])}、P95 {_fmt(m['p95'])}、最大 {_fmt(m['max'])}；"
        f"{m['within_1'] * 100:.0f}% 的采样点 ≤ 1.0，{m['within_2'] * 100:.0f}% ≤ 2.0，{m['within_3'] * 100:.0f}% ≤ 3.0。"
    )
    lines.append("")
    lines.append(f"逐通道 RMSE：R {_fmt(m['rmse_r'])} / G {_fmt(m['rmse_g'])} / B {_fmt(m['rmse_b'])}（0–255 刻度）。")
    lines.append("")

    lines.append("## 输入")
    lines.append("")
    lines.append(f"- 文件：`{result.source}`")
    lines.append(f"- 类型：{result.lut_kind}")
    lines.append(f"- 采用策略：**{result.label}**")
    extra = result.extra or {}
    for k, v in extra.items():
        lines.append(f"- {k}：{v}")
    lines.append("")

    lines.append("## 策略对比")
    lines.append("")
    lines.append("| 策略 | 采样点数 | 灰阶偏离 | ΔE 平均 | ΔE P95 | 综合分 |")
    lines.append("|---|---|---|---|---|---|")
    for c in result.candidates:
        mark = " ✅" if c.mode == result.mode else ""
        lines.append(
            f"| {c.label}{mark} | {c.sample_count} | {c.metrics['gray_dev'] * 100:.1f}% | "
            f"{_fmt(c.metrics['mean'])} | {_fmt(c.metrics['p95'])} | {_fmt(c.score)} |"
        )
    lines.append("")
    lines.append(
        "> 择优规则：灰阶偏离 ≤ 2% 才有资格参选，再按综合分取最小。"
        "灰阶是硬指标——黑位一抬，整张片子的暗部就废了，这不是「平均误差略低」能补偿的。"
    )
    lines.append("")
    if result.mode == "neutral" and len(result.candidates) > 1:
        others = [c for c in result.candidates if c.mode != "neutral"]
        better = [c for c in others if c.metrics["mean"] < result.metrics["mean"] - 0.05]
        if better:
            detail = "、".join(f"{c.label} {c.metrics['gray_dev'] * 100:.1f}%" for c in better)
            lines.append(f"> 注：{detail} 的整体色差略低，但以牺牲灰阶为代价，未采用。")
        else:
            lines.append("> 注：另外两种策略既没改善整体色差又破坏灰阶，说明这个参考的本质就是一条影调曲线。")
        lines.append("")

    lines.append("## 视觉对比")
    lines.append("")
    for key, title in (("chart", "色卡"), ("scene", "风景"), ("gradient", "渐变")):
        if key in previews:
            lines.append(f"**{title}**")
            lines.append("")
            lines.append(f"![{key}]({rel(previews[key])})")
            lines.append("")
    if "curve" in previews:
        lines.append("**曲线**")
        lines.append("")
        lines.append(f"![curve]({rel(previews['curve'])})")
        lines.append("")

    lines.append("## 曲线特征")
    lines.append("")
    lines.append("| 指标 | 值 | 说明 |")
    lines.append("|---|---|---|")
    lines.append(f"| 黑位 | {t['black'] * 100:.1f}% | 0 输入映射到的输出，0 表示纯黑 |")
    lines.append(f"| 白位 | {t['white'] * 100:.1f}% | 1.0 输入映射到的输出 |")
    lines.append(f"| 18% 灰 | {t['mid_18'] * 100:.1f}% | 中间调亮度 |")
    lines.append(f"| 25–75% 对比 | {t['contrast_25_75']:.2f}× | 1.0 = 与线性相同 |")
    lines.append(f"| 趾部斜率 | {t['slope_toe']:.2f} | 暗部压缩/抬升强度 |")
    lines.append(f"| 中间调斜率 | {t['slope_mid']:.2f} | 主体反差 |")
    lines.append(f"| 肩部斜率 | {t['slope_shoulder']:.2f} | 高光滚降强度 |")
    lines.append("")

    lines.append("## 色阶断层检测")
    lines.append("")
    ok = b["monotone"] and not b["artifact_flats"] and b["max_8bit_dupes"] <= 2
    lines.append(f"- 单调性：{'✅ 严格单调' if b['monotone'] else '❌ 存在反转'}")
    lines.append(f"- 最长塌陷段：{b['max_flat_run']} 步（1 = 每个点都不同，属正常）")
    if b["artifact_flats"]:
        detail = "、".join(f"第 {s} 点起 {ln} 步" for s, ln in b["artifact_flats"])
        lines.append(f"- ❌ 其中无法用参考解释的塌陷：{detail}")
    elif b["flat_runs"]:
        detail = "、".join(f"第 {s} 点起 {ln} 步" for s, ln in b["flat_runs"])
        lines.append(f"- 塌陷位置：{detail} —— 与参考自身的黑位截断一致，属于还原")
    else:
        lines.append("- 塌陷段：无")
    lines.append(f"- 单段 8 位码值复用：最多 {b['max_8bit_dupes']} 个点（>2 表示该段被压死）")
    lines.append(f"- 8 位可用码值：{b['distinct_codes']}/256")
    lines.append(f"- 归一化斜率范围：{b['min_slope']:.2f} ~ {b['max_slope']:.2f}")
    if b["compressed_segments"]:
        seg = "，".join(f"第 {s} 点起 {n} 步（斜率 {sl:.2f}）" for s, n, sl in b["compressed_segments"])
        lines.append(f"- 强压缩段：{seg}")
    lines.append("")
    lines.append("总体：**" + ("没有断层风险，可以直接进 Phase 2 打包" if ok else "存在塌陷风险，建议加大 knots 或放松端点约束后重拟合") + "**")
    lines.append("")

    lines.append("## 输出物")
    lines.append("")
    lines.append("| 文件 | 内容 |")
    lines.append("|---|---|")
    lines.append("| `lut_segment.bin` | 512 字节 = 256 × u16 大端，即 614 字节载荷的 `+0x066..+0x265` 段 |")
    lines.append("| `curve.csv` | 256 行，input/output（0–1）与 u16 原始值 |")
    lines.append("| `fitted_1d.cube` | 同一条曲线的 1D .cube，方便在第三方软件里交叉验证 |")
    lines.append("| `preview_*.png` | 视觉对比图 |")
    lines.append("")
    lines.append(f"LUT 段首尾（偏移从 `+0x{PAYLOAD_LUT_OFFSET:03X}` 起，共 {PAYLOAD_LUT_BYTES} 字节）：")
    lines.append("")
    lines.extend(_hexlines(result.segment, 16, 2))
    lines.append("`...`")
    lines.extend(_hexlines(result.segment[-32:], 16, 2, base=PAYLOAD_LUT_BYTES - 32))
    lines.append("")

    lines.append("## 已知局限")
    lines.append("")
    lines.append("- 机身只有**一条**作用到 R/G/B 的曲线（512 字节 = 256×单通道），做不了分色相调整、分离色调、HSL。")
    lines.append("- ΔE 是对「参考 LUT 输出」算的，不是对真实机身成像算的——机身自身的色彩处理会再叠加一层差异。")
    lines.append("- 评测图是合成的，用来查灰阶/断层/整体观感，不能替代实拍样张。")
    lines.append("- 载荷偏移、控制点语义尚未在真机验证（Phase 2/3）。")
    lines.append("")
    return "\n".join(lines)


def write_sample_outputs(result: FitResult, lut: CubeLUT | None, outdir: str | Path, previews: bool = True) -> Path:
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    (outdir / "lut_segment.bin").write_bytes(result.segment)

    table = result.table
    rows = ["input,output,u16"]
    for i in range(table.size):
        rows.append(f"{i / (table.size - 1):.6f},{table[i] / LUT_MAX:.6f},{int(table[i])}")
    (outdir / "curve.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")

    from .cube import write_cube_1d

    curves = np.stack([result.table_0_1] * 3, axis=-1)
    write_cube_1d(outdir / "fitted_1d.cube", curves, title=f"z30fit {Path(result.source).stem}")

    paths = render_previews(result, lut, outdir) if previews else {}
    md = build_markdown(result, lut, paths, outdir)
    report_path = outdir / "report.md"
    report_path.write_text(md, encoding="utf-8")
    return report_path


def write_index(rows: list[dict], outdir: str | Path) -> Path:
    """多份样本的总览报告。"""
    outdir = Path(outdir)
    now = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        "# z30fit 拟合总览",
        "",
        f"> {now} · Phase 1 离线拟合，未接触相机",
        "",
        "| 样本 | 类型 | 策略 | ΔE 平均 | ΔE P95 | 最长塌陷 | 判定 |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        flat = str(r["flat"]) + (" ⚠" if r.get("artifact") else "")
        lines.append(
            f"| [{r['name']}]({r['dir']}/report.md) | {r['kind']} | {r['mode']} | "
            f"{r['mean']:.2f} | {r['p95']:.2f} | {flat} | {r['verdict']} |"
        )
    lines.append("")
    lines.append("## 怎么看")
    lines.append("")
    lines.append("1. 先看每个样本 `report.md` 里「色卡」和「风景」两张对比图，肉眼判断像不像。")
    lines.append("2. ΔE 平均 < 2 且 P95 < 6 基本可用；平均 > 4 说明这个风格主要靠 3D 特性，1D 表达不了。")
    lines.append("3. 「最长塌陷」= 1 属正常；>1 表示有连续相同的输出值，若带 ⚠ 则是拟合把中间调压平了，")
    lines.append("   需要调 knots 重拟合；不带 ⚠ 说明参考本身在该段就是纯黑/纯白，属忠实还原。")
    lines.append("")
    path = outdir / "总览.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
