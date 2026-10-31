"""单调三次样条（Fritsch–Carlson / PCHIP）、保序回归、量化与断层检测。

相机曲线必须**严格单调**：一旦非单调，机身在对应亮度段就会出现反转或塌陷。
所以拟合的每一步都做保序投影，而不是拟合完再修。
"""

from __future__ import annotations

import numpy as np

from . import LUT_MAX, LUT_POINTS


# --------------------------------------------------------------------------
# PCHIP：分段三次 Hermite，斜率用加权调和平均（Fritsch–Carlson）
# --------------------------------------------------------------------------
def pchip_slopes(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """计算 PCHIP 在各节点处的导数。x 必须严格递增。"""
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    n = x.size
    if n == 2:
        d = np.full(2, (y[1] - y[0]) / (x[1] - x[0]))
        return d

    h = np.diff(x)
    delta = np.diff(y) / h
    d = np.zeros(n)

    # 内部节点：两个相邻差商的加权调和平均（同号时）
    w1 = 2.0 * h[1:] + h[:-1]
    w2 = h[1:] + 2.0 * h[:-1]
    same_sign = delta[:-1] * delta[1:] > 0.0
    num = w1 + w2
    den = np.divide(w1, delta[:-1], out=np.zeros_like(w1), where=same_sign)
    den += np.divide(w2, delta[1:], out=np.zeros_like(w2), where=same_sign)
    d[1:-1] = np.divide(num, den, out=np.zeros_like(num), where=same_sign & (den != 0.0))

    # 端点：三点插值公式 + Fritsch–Carlson 限幅
    d[0] = ((2.0 * h[0] + h[1]) * delta[0] - h[0] * delta[1]) / (h[0] + h[1])
    if np.sign(d[0]) != np.sign(delta[0]):
        d[0] = 0.0
    elif np.sign(delta[0]) != np.sign(delta[1]) and abs(d[0]) > 3.0 * abs(delta[0]):
        d[0] = 3.0 * delta[0]

    d[-1] = ((2.0 * h[-1] + h[-2]) * delta[-1] - h[-1] * delta[-2]) / (h[-1] + h[-2])
    if np.sign(d[-1]) != np.sign(delta[-1]):
        d[-1] = 0.0
    elif np.sign(delta[-1]) != np.sign(delta[-2]) and abs(d[-1]) > 3.0 * abs(delta[-1]):
        d[-1] = 3.0 * delta[-1]

    return d


def pchip_eval(x: np.ndarray, y: np.ndarray, xq: np.ndarray) -> np.ndarray:
    """在 xq 处求 PCHIP 值；xq 超出 [x0, xn] 时按端点值截断。"""
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    xq = np.asarray(xq, dtype=np.float64)
    d = pchip_slopes(x, y)

    xq_c = np.clip(xq, x[0], x[-1])
    idx = np.searchsorted(x, xq_c, side="right") - 1
    idx = np.clip(idx, 0, x.size - 2)

    h = x[idx + 1] - x[idx]
    t = (xq_c - x[idx]) / h

    t2 = t * t
    t3 = t2 * t
    h00 = 2.0 * t3 - 3.0 * t2 + 1.0
    h10 = t3 - 2.0 * t2 + t
    h01 = -2.0 * t3 + 3.0 * t2
    h11 = t3 - t2

    return h00 * y[idx] + h10 * h * d[idx] + h01 * y[idx + 1] + h11 * h * d[idx + 1]


class MonotoneSpline:
    """以节点值参数化的单调曲线。节点上的 Hermite 基线性可导，便于最小二乘。"""

    def __init__(self, knots: np.ndarray, values: np.ndarray):
        knots = np.asarray(knots, dtype=np.float64)
        values = np.asarray(values, dtype=np.float64)
        order = np.argsort(knots)
        self.knots = knots[order]
        self.values = values[order]

    def __call__(self, xq) -> np.ndarray:
        return pchip_eval(self.knots, self.values, np.asarray(xq, dtype=np.float64))

    def sample(self, n: int = LUT_POINTS) -> np.ndarray:
        """在 [0,1] 上等距采样 n 点。"""
        return self(np.linspace(0.0, 1.0, n))

    def slope_report(self) -> dict:
        return {
            "min_slope": float(np.min(np.diff(self.values) / np.diff(self.knots))),
            "max_slope": float(np.max(np.diff(self.values) / np.diff(self.knots))),
            "monotone": bool(np.all(np.diff(self.values) >= -1e-12)),
        }


# --------------------------------------------------------------------------
# 保序回归（PAVA）
# --------------------------------------------------------------------------
def pava(y: np.ndarray, w: np.ndarray | None = None) -> np.ndarray:
    """加权保序回归：把 y 投影成非递减序列（L2 最优）。"""
    y = np.asarray(y, dtype=np.float64)
    if w is None:
        w = np.ones_like(y)
    w = np.asarray(w, dtype=np.float64)

    vals: list[float] = []
    weights: list[float] = []
    counts: list[int] = []

    for yi, wi in zip(y, w):
        vals.append(float(yi))
        weights.append(float(wi))
        counts.append(1)
        while len(vals) > 1 and vals[-2] > vals[-1]:
            v2, w2, c2 = vals.pop(), weights.pop(), counts.pop()
            v1, w1, c1 = vals.pop(), weights.pop(), counts.pop()
            tw = w1 + w2
            vals.append((v1 * w1 + v2 * w2) / tw if tw > 0 else 0.0)
            weights.append(tw)
            counts.append(c1 + c2)

    out = np.empty_like(y)
    pos = 0
    for v, c in zip(vals, counts):
        out[pos : pos + c] = v
        pos += c
    return out


# --------------------------------------------------------------------------
# 单调最小二乘拟合：高斯-牛顿 + 保序投影
# --------------------------------------------------------------------------
def fit_monotone_curve(
    x: np.ndarray,
    y: np.ndarray,
    w: np.ndarray | None = None,
    n_knots: int = 33,
    pin_endpoints: bool = True,
    endpoint_weight: float = 40.0,
    smoothness: float = 0.02,
    max_iter: int = 60,
    tol: float = 1e-9,
) -> MonotoneSpline:
    """用单调三次样条最小二乘拟合 (x, y)，权重 w。

    参数化：节点值 v（K 个），前向模型 = PCHIP(knots, v)(x)。
    每步高斯-牛顿后做 PAVA 保序投影，保证输出曲线严格单调。

    两个必要的工程处理：
    1. **同值聚合**：全通道采样时同一个输入通道值会出现几百次，先去重取加权均值，
       否则雅可比严重病态（实测会拟合出阶梯状曲线）。
    2. **节点数封顶 + 二阶差分平滑**：节点多于有效支撑点时，落在数据点之间的节点
       雅可比列恒为 0，属于不可辨识参数，必须用平滑先验约束住。
    """
    x = np.asarray(x, dtype=np.float64).ravel()
    y = np.asarray(y, dtype=np.float64).ravel()
    if w is None:
        w = np.ones_like(x)
    w = np.asarray(w, dtype=np.float64).ravel()

    keep = np.isfinite(x) & np.isfinite(y)
    x, y, w = x[keep], y[keep], w[keep]
    if x.size == 0:
        raise ValueError("没有有效的拟合数据点")

    order = np.argsort(x)
    x, y, w = x[order], y[order], w[order]

    # 权重归一化，让不同量级的采样方式可比较
    w = w / max(float(np.mean(w)), 1e-12)

    # 1) 同值聚合（加权均值）
    ux, inv = np.unique(x, return_inverse=True)
    if ux.size < x.size:
        sw = np.bincount(inv, weights=w)
        sy = np.bincount(inv, weights=w * y)
        x, y, w = ux, sy / np.maximum(sw, 1e-12), np.maximum(sw, 1e-12)

    # 2) 端点钉住（曲线两头必须落在真实端点上）
    if pin_endpoints:
        y0 = float(y[0]) if x[0] <= 1e-9 else float(np.interp(0.0, x, y))
        y1 = float(y[-1]) if x[-1] >= 1 - 1e-9 else float(np.interp(1.0, x, y))
        w_pin = float(np.mean(w)) * endpoint_weight
        x = np.concatenate([x, [0.0, 1.0]])
        y = np.concatenate([y, [y0, y1]])
        w = np.concatenate([w, [w_pin, w_pin]])

    # 3) 节点数不超过有效支撑点（另留 3 个余量给样条自由度）
    n_unique = int(np.unique(x).size)
    k_eff = int(min(n_knots, max(2, n_unique)))
    knots = np.linspace(0.0, 1.0, k_eff)

    v = pava(np.interp(knots, x, y))
    v = np.clip(v, 0.0, 1.0)
    K = k_eff

    # 二阶差分矩阵（平滑先验）
    if K >= 3:
        D2 = np.zeros((K - 2, K))
        for i in range(K - 2):
            D2[i, i : i + 3] = (1.0, -2.0, 1.0)
        P = D2.T @ D2
    else:
        P = np.zeros((K, K))
    lam_s = float(smoothness)

    def model(vv: np.ndarray) -> np.ndarray:
        return pchip_eval(knots, vv, x)

    def cost(vv: np.ndarray) -> float:
        r = w * (model(vv) - y)
        return 0.5 * float(np.dot(r, r)) + 0.5 * lam_s * float(vv @ P @ vv)

    lam = 1e-6
    c0 = cost(v)
    eps = 1e-6

    for _ in range(max_iter):
        pred = model(v)
        r = w * (pred - y)
        J = np.empty((x.size, K), dtype=np.float64)
        for j in range(K):
            vp = v.copy()
            vp[j] += eps
            J[:, j] = w * (model(vp) - pred) / eps

        JtJ = J.T @ J + lam_s * P
        grad = J.T @ r + lam_s * (P @ v)
        try:
            dv = np.linalg.solve(JtJ + lam * (np.diag(np.diag(JtJ)) + 1e-9 * np.eye(K)), -grad)
        except np.linalg.LinAlgError:
            break

        accepted = False
        alpha = 1.0
        for _ls in range(8):
            cand = np.clip(v + alpha * dv, 0.0, 1.0)
            cand = pava(cand)
            c1 = cost(cand)
            if c1 < c0 - 1e-14:
                v, c0 = cand, c1
                lam = max(lam * 0.5, 1e-9)
                accepted = True
                break
            alpha *= 0.5
        if not accepted:
            lam *= 8.0
            if lam > 1e6:
                break
            continue
        if np.max(np.abs(alpha * dv)) < tol:
            break

    return MonotoneSpline(knots, np.clip(pava(v), 0.0, 1.0))


# --------------------------------------------------------------------------
# 量化与断层检测
# --------------------------------------------------------------------------
def quantize(values: np.ndarray, vmax: int = LUT_MAX) -> np.ndarray:
    """把 0..1 的曲线量化成 u16 表（机身是 15 位，0..32767）。"""
    scaled = np.clip(np.asarray(values, dtype=np.float64), 0.0, 1.0) * vmax
    return np.rint(scaled).astype(np.uint16)


def banding_analysis(table: np.ndarray, reference: np.ndarray | None = None) -> dict:
    """检测量化后的曲线有没有塌陷段 / 断层（会导致色阶断裂）。

    - flat_run：连续相同输出值的最长长度（1 = 每个点都不同，>1 就是塌陷）
    - 8bit_dupes：落到同一个 8 位输出的点数（>1 表示该段被压死）
    - 压缩段：局部斜率 < 0.15 且持续 ≥ 8 步

    传入 reference（参考曲线在同样 256 点上的取值）时，会额外判断每段塌陷
    「是不是参考本身就这么黑」。胶片曲线常在趾部把 0–5% 直接压到纯黑，
    那是还原，不是拟合缺陷——不区分开就没法判断到底该不该改参数。
    """
    t = np.asarray(table, dtype=np.int64)
    n = t.size
    diffs = np.diff(t)

    flat_runs = []
    run_start = 0
    for i in range(1, n):
        if diffs[i - 1] != 0:
            if i - run_start > 1:
                flat_runs.append((int(run_start), int(i - run_start)))
            run_start = i
    if n - run_start > 1:
        flat_runs.append((int(run_start), int(n - run_start)))

    q8 = np.rint(t.astype(np.float64) / LUT_MAX * 255.0).astype(np.int64)
    dup_runs = []
    rs = 0
    for i in range(1, n):
        if q8[i] != q8[i - 1]:
            if i - rs > 1:
                dup_runs.append((int(rs), int(i - rs)))
            rs = i
    if n - rs > 1:
        dup_runs.append((int(rs), int(n - rs)))

    slope = diffs.astype(np.float64) / LUT_MAX * (n - 1)  # 归一化斜率 ≈ 1 表示恒等
    compressed = []
    rs = 0
    for i in range(1, n):
        low = slope[i - 1] < 0.15
        prev_low = slope[rs] < 0.15 if rs < slope.size else False
        if low != prev_low:
            if prev_low and i - rs >= 8:
                compressed.append((int(rs), int(i - rs), float(slope[rs])))
            rs = i
    if slope.size and slope[rs] < 0.15 and n - rs >= 8:
        compressed.append((int(rs), int(n - rs), float(slope[rs])))

    # 参考感知：区分「还原黑位截断」和「拟合把中间调压平了」
    artifact_flats: list[tuple[int, int]] = []
    if reference is not None and flat_runs:
        ref = np.asarray(reference, dtype=np.float64).ravel()
        if ref.size == n:
            for start, length in flat_runs:
                span = ref[start : start + length + 1]
                if span.size < 2:
                    continue
                # 判定标准：参考曲线在这一段里每步涨得比 2 个 15 位码值还快，
                # 才说明是我们把它压平了。压暗部时本来就会出现几个相同值，
                # 那是正常压缩，不是缺陷（阈值太严会天天误报）。
                if float(span[-1] - span[0]) > length * 2.0 / LUT_MAX:
                    artifact_flats.append((start, length))

    return {
        "monotone": bool(np.all(diffs >= 0)),
        "max_flat_run": int(max(1, max((ln for _, ln in flat_runs), default=1))),
        "flat_runs": flat_runs[:8],
        "artifact_flats": artifact_flats[:8],
        "max_8bit_dupes": int(max(1, max((ln for _, ln in dup_runs), default=1))),
        "compressed_segments": compressed[:8],
        "black_point": int(t[0]),
        "white_point": int(t[-1]),
        "min_slope": float(np.min(slope)),
        "max_slope": float(np.max(slope)),
        "distinct_codes": int(np.unique(q8).size),
    }
