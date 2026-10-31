"""把参考风格（3D/1D LUT）拟合成 Z30 的 256 点 1D 曲线。

三种策略，最后按 ΔE2000 自动择优：

    neutral  只拟合中性灰轴 —— 灰阶绝对正确，彩色靠曲线顺带带过
    channels 全通道最小二乘 —— 把每个节点的 R/G/B 输出都当约束，整体误差最小
    hybrid   两者加权合并 —— 中性轴加权 3 倍，兼顾灰阶与整体

机身只有一条作用到三个通道的曲线，所以任何 3D 效果（分离色调、HSL）都会
不可避免地丢失，这正是报告要量化出来的东西。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from . import LUT_MAX, LUT_POINTS, color
from .cube import CubeLUT
from .curve import MonotoneSpline, banding_analysis, fit_monotone_curve, quantize
from .metrics import evaluate, evaluation_points, tone_stats, verdict
from .np3 import pack_lut_segment

MODE_LABELS = {
    "neutral": "中性轴拟合",
    "channels": "全通道最小二乘",
    "hybrid": "中性加权混合",
}


@dataclass
class Candidate:
    mode: str
    curve: MonotoneSpline
    metrics: dict
    sample_count: int

    @property
    def label(self) -> str:
        return MODE_LABELS.get(self.mode, self.mode)

    @property
    def score(self) -> float:
        # 主看平均色差，P95 只作次要惩罚，避免单个离群色主导选择
        return float(self.metrics["mean"] + 0.35 * self.metrics["p95"])


@dataclass
class FitResult:
    source: str
    lut_kind: str
    mode: str
    curve: MonotoneSpline
    table: np.ndarray           # uint16 (256,)
    segment: bytes              # 512 字节 LUT 段
    metrics: dict
    banding: dict
    tone: dict
    candidates: list[Candidate]
    extra: dict

    @property
    def label(self) -> str:
        return MODE_LABELS.get(self.mode, self.mode)

    @property
    def table_0_1(self) -> np.ndarray:
        return self.table.astype(np.float64) / LUT_MAX


# --------------------------------------------------------------------------
# 采样
# --------------------------------------------------------------------------
def collect_neutral(lut: CubeLUT, n: int = LUT_POINTS, weight: float = 1.0):
    """沿中性轴采样：目标值 = 输出的 Rec.709 亮度（曲线作用在三通道后与之等价）。"""
    x = np.linspace(0.0, 1.0, n)
    pts = np.stack([x, x, x], axis=-1)
    out = lut.apply(pts)
    y = color.luma(out)
    w = np.full(n, weight, dtype=np.float64)
    return x, y, w


def collect_channels(lut: CubeLUT, max_nodes: int = 3000):
    """全通道采样：每个节点的每个通道都作为一组 (输入通道值, 输出通道值)。

    这等价于在 RGB 立方体上用等权 L2 逼近，对「曲线 + 轻色彩偏移」类风格最有效。
    """
    if lut.kind == 3:
        n = lut.size**3
        step = max(1, int(np.ceil(n / max_nodes)))
        g = np.linspace(0.0, 1.0, lut.size)
        b, gg, r = np.meshgrid(g, g, g, indexing="ij")
        nodes = np.stack([r.ravel(), gg.ravel(), b.ravel()], axis=-1)[::step]
    else:
        g = np.linspace(0.0, 1.0, lut.size)
        b, gg, r = np.meshgrid(g, g, g, indexing="ij")
        nodes = np.stack([r.ravel(), gg.ravel(), b.ravel()], axis=-1)

    out = lut.apply(nodes)
    xs = nodes.reshape(-1)
    ys = out.reshape(-1)
    node_luma = np.repeat(color.luma(nodes), 3)
    ws = 0.5 + 0.5 * node_luma
    return xs, ys, ws


# --------------------------------------------------------------------------
# 拟合
# --------------------------------------------------------------------------
def _candidates(lut: CubeLUT, knots: int, neutral_weight: float) -> list[Candidate]:
    pts_eval = evaluation_points(lut)  # 三种策略共用同一批评测点，比较才公平
    cands: list[Candidate] = []

    xn, yn, wn = collect_neutral(lut)
    curve_n = fit_monotone_curve(xn, yn, wn, n_knots=knots)
    cands.append(Candidate("neutral", curve_n, evaluate(lut, curve_n, pts_eval), xn.size))

    xc, yc, wc = collect_channels(lut)
    curve_c = fit_monotone_curve(xc, yc, wc, n_knots=knots)
    cands.append(Candidate("channels", curve_c, evaluate(lut, curve_c, pts_eval), xc.size))

    # 混合：先把两组权重各自归一，再按 neutral_weight 配比总量，
    # 否则「256 个中性点 vs 数千个通道点」会让中性轴几乎不起作用。
    xh = np.concatenate([xn, xc])
    yh = np.concatenate([yn, yc])
    wh = np.concatenate([wn / np.sum(wn) * neutral_weight, wc / np.sum(wc)])
    curve_h = fit_monotone_curve(xh, yh, wh, n_knots=knots)
    cands.append(Candidate("hybrid", curve_h, evaluate(lut, curve_h, pts_eval), xh.size))

    return cands


def fit_lut(
    lut: CubeLUT,
    source: str = "",
    mode: str = "auto",
    knots: int = 33,
    neutral_weight: float = 3.0,
    extra: dict | None = None,
) -> FitResult:
    """拟合入口。mode = auto / neutral / channels / hybrid。"""
    cands = _candidates(lut, knots, neutral_weight)

    if mode == "auto":
        # 字典序择优：灰阶先过关，再比整体色差。
        # 灰阶偏差超过 2% 的方案直接不具备资格——机身直出时黑位一抬，
        # 整张片子的暗部就废了，这不是「平均误差略低」能补偿的。
        tol = 0.02
        qualified = [c for c in cands if c.metrics["gray_dev"] <= tol]
        if qualified:
            best = min(qualified, key=lambda c: c.score)
        else:
            best = min(cands, key=lambda c: c.metrics["gray_dev"])
    else:
        matches = [c for c in cands if c.mode == mode]
        if not matches:
            raise ValueError(f"未知策略 {mode}，可选 auto/{'/'.join(MODE_LABELS)}")
        best = matches[0]

    values = best.curve.sample(LUT_POINTS)
    table = quantize(values, LUT_MAX)
    values_q = table.astype(np.float64) / LUT_MAX

    # 参考曲线的中性轴取值，用来判断塌陷段是「还原黑位」还是「拟合压平」
    ramp = np.linspace(0.0, 1.0, LUT_POINTS)
    ref_neutral = color.luma(lut.apply(np.stack([ramp, ramp, ramp], axis=-1)))

    return FitResult(
        source=source,
        lut_kind=f"{lut.kind}D/{lut.size}",
        mode=best.mode,
        curve=best.curve,
        table=table,
        segment=pack_lut_segment(table),
        metrics=best.metrics,
        banding=banding_analysis(table, ref_neutral),
        tone=tone_stats(values_q),
        candidates=cands,
        extra=extra or {},
    )


def fit_curve_direct(curve_0_1: np.ndarray, source: str = "", knots: int = 33, extra: dict | None = None) -> FitResult:
    """从一条已经是 1D 的曲线（比如 NP3 内嵌曲线）直接拟合，不需要 .cube。

    这种做法只做「重采样 + 单调化 + 量化」，因为参考本身就是单通道映射。
    """
    curve_0_1 = np.asarray(curve_0_1, dtype=np.float64)
    x = np.linspace(0.0, 1.0, curve_0_1.size)
    y = np.clip(curve_0_1, 0.0, 1.0)
    fitted = fit_monotone_curve(x, y, np.ones_like(x), n_knots=knots)

    values = fitted.sample(LUT_POINTS)
    table = quantize(values, LUT_MAX)
    values_q = table.astype(np.float64) / LUT_MAX

    # 以曲线自身为参考算偏差（重采样误差 + 量化误差）
    pred = values_q
    de = np.abs(pred - np.interp(np.linspace(0, 1, LUT_POINTS), x, y)) * 255.0
    metrics = {
        "mean": float(np.mean(de)) / 2.5,  # 粗略折算成 ΔE 量级，仅作自检参考
        "median": float(np.median(de)) / 2.5,
        "p95": float(np.percentile(de, 95)) / 2.5,
        "max": float(np.max(de)) / 2.5,
        "within_1": float(np.mean(de <= 2.5)),
        "within_2": float(np.mean(de <= 5.0)),
        "within_3": float(np.mean(de <= 7.5)),
        "rmse": float(np.sqrt(np.mean(de**2))),
        "rmse_r": float(np.sqrt(np.mean(de**2))),
        "rmse_g": float(np.sqrt(np.mean(de**2))),
        "rmse_b": float(np.sqrt(np.mean(de**2))),
        "de_neutral_mean": float(np.mean(de)) / 2.5,
        "de_neutral_max": float(np.max(de)) / 2.5,
        "gray_dev": 0.0,
        "points": LUT_POINTS,
        "reference": "自身曲线（重采样保真度）",
    }

    return FitResult(
        source=source,
        lut_kind="1D curve",
        mode="direct",
        curve=fitted,
        table=table,
        segment=pack_lut_segment(table),
        metrics=metrics,
        banding=banding_analysis(table, np.interp(np.linspace(0, 1, LUT_POINTS), x, y)),
        tone=tone_stats(values_q),
        candidates=[Candidate("direct", fitted, metrics, curve_0_1.size)],
        extra=extra or {},
    )


def summarize(result: FitResult) -> str:
    m = result.metrics
    return (
        f"{result.source}: 策略={result.label} ΔE2000 mean={m['mean']:.2f} "
        f"P95={m['p95']:.2f} max={m['max']:.2f} → {verdict(m)}"
    )
