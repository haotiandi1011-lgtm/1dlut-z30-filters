"""色彩空间转换与色差（仅依赖 numpy）。

约定：所有 RGB 都是 **编码后的 sRGB**（0..1，不做线性化），因为相机的
1D LUT 作用在显示参考信号上，不是线性光。只有在转 Lab 时才做线性化。
"""

from __future__ import annotations

import numpy as np

# Rec.709 亮度权重（用于把彩色目标压成单通道标量）
LUMA_REC709 = np.array([0.2126, 0.7152, 0.0722], dtype=np.float64)

# sRGB(线性) -> XYZ(D65)
_M_RGB2XYZ = np.array(
    [
        [0.4124564, 0.3575761, 0.1804375],
        [0.2126729, 0.7151522, 0.0721750],
        [0.0193339, 0.1191920, 0.9503041],
    ],
    dtype=np.float64,
)

# D65 参考白
_WHITE_D65 = np.array([0.95047, 1.00000, 1.08883], dtype=np.float64)

_EPS = 216.0 / 24389.0   # (6/29)^3
_KAPPA = 24389.0 / 27.0  # (29/3)^3


def srgb_to_linear(c: np.ndarray) -> np.ndarray:
    """sRGB 编码值 -> 线性光。"""
    c = np.asarray(c, dtype=np.float64)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def linear_to_srgb(c: np.ndarray) -> np.ndarray:
    """线性光 -> sRGB 编码值。"""
    c = np.asarray(c, dtype=np.float64)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * np.power(np.clip(c, 0.0, None), 1 / 2.4) - 0.055)


def rgb_to_xyz(rgb: np.ndarray) -> np.ndarray:
    """编码 sRGB -> XYZ(D65)，输入/输出形状 (..., 3)。"""
    lin = srgb_to_linear(np.clip(rgb, 0.0, 1.0))
    return lin @ _M_RGB2XYZ.T


def xyz_to_lab(xyz: np.ndarray) -> np.ndarray:
    t = np.asarray(xyz, dtype=np.float64) / _WHITE_D65
    f = np.where(t > _EPS, np.cbrt(np.clip(t, 0.0, None)), (_KAPPA * t + 16.0) / 116.0)
    L = 116.0 * f[..., 1] - 16.0
    a = 500.0 * (f[..., 0] - f[..., 1])
    b = 200.0 * (f[..., 1] - f[..., 2])
    return np.stack([L, a, b], axis=-1)


def rgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    """编码 sRGB(0..1) -> CIELAB(D65)。"""
    return xyz_to_lab(rgb_to_xyz(rgb))


def luma(rgb: np.ndarray) -> np.ndarray:
    """编码域亮度（0..1），作为把彩色目标压成标量时的权重。"""
    return np.asarray(rgb, dtype=np.float64) @ LUMA_REC709


def delta_e_2000(lab1: np.ndarray, lab2: np.ndarray) -> np.ndarray:
    """CIEDE2000 色差（kL=kC=kH=1），输入形状 (..., 3)，返回 (...,)。"""
    lab1 = np.asarray(lab1, dtype=np.float64)
    lab2 = np.asarray(lab2, dtype=np.float64)

    L1, a1, b1 = lab1[..., 0], lab1[..., 1], lab1[..., 2]
    L2, a2, b2 = lab2[..., 0], lab2[..., 1], lab2[..., 2]

    C1 = np.hypot(a1, b1)
    C2 = np.hypot(a2, b2)
    C_bar = 0.5 * (C1 + C2)

    C_bar7 = C_bar**7
    G = 0.5 * (1.0 - np.sqrt(C_bar7 / (C_bar7 + 25.0**7)))

    a1p = (1.0 + G) * a1
    a2p = (1.0 + G) * a2
    C1p = np.hypot(a1p, b1)
    C2p = np.hypot(a2p, b2)

    h1p = np.degrees(np.arctan2(b1, a1p)) % 360.0
    h2p = np.degrees(np.arctan2(b2, a2p)) % 360.0
    # 消色差像素的色相无定义，统一置 0
    h1p = np.where(C1p == 0.0, 0.0, h1p)
    h2p = np.where(C2p == 0.0, 0.0, h2p)

    dLp = L2 - L1
    dCp = C2p - C1p

    dhp = h2p - h1p
    dhp = np.where(dhp > 180.0, dhp - 360.0, dhp)
    dhp = np.where(dhp < -180.0, dhp + 360.0, dhp)
    dhp = np.where(C1p * C2p == 0.0, 0.0, dhp)

    dHp = 2.0 * np.sqrt(C1p * C2p) * np.sin(np.radians(dhp) / 2.0)

    L_bar = 0.5 * (L1 + L2)
    C_barp = 0.5 * (C1p + C2p)

    h_sum = h1p + h2p
    h_diff = np.abs(h1p - h2p)
    h_barp = np.where(
        C1p * C2p == 0.0,
        h_sum,
        np.where(
            h_diff <= 180.0,
            h_sum / 2.0,
            np.where(h_sum < 360.0, (h_sum + 360.0) / 2.0, (h_sum - 360.0) / 2.0),
        ),
    )

    T = (
        1.0
        - 0.17 * np.cos(np.radians(h_barp - 30.0))
        + 0.24 * np.cos(np.radians(2.0 * h_barp))
        + 0.32 * np.cos(np.radians(3.0 * h_barp + 6.0))
        - 0.20 * np.cos(np.radians(4.0 * h_barp - 63.0))
    )

    d_theta = 30.0 * np.exp(-(((h_barp - 275.0) / 25.0) ** 2))
    C_barp7 = C_barp**7
    R_C = 2.0 * np.sqrt(C_barp7 / (C_barp7 + 25.0**7))
    S_L = 1.0 + (0.015 * (L_bar - 50.0) ** 2) / np.sqrt(20.0 + (L_bar - 50.0) ** 2)
    S_C = 1.0 + 0.045 * C_barp
    S_H = 1.0 + 0.015 * C_barp * T
    R_T = -np.sin(np.radians(2.0 * d_theta)) * R_C

    tL = dLp / S_L
    tC = dCp / S_C
    tH = dHp / S_H

    return np.sqrt(tL**2 + tC**2 + tH**2 + R_T * tC * tH)


def delta_e_stats(lab1: np.ndarray, lab2: np.ndarray, weights: np.ndarray | None = None) -> dict:
    """把色差压成一组统计量（ΔE2000）。weighted 时用加权均值与加权分位数。"""
    de = np.asarray(delta_e_2000(lab1, lab2), dtype=np.float64)
    de = de.ravel()

    if weights is None:
        w = np.ones_like(de)
    else:
        w = np.asarray(weights, dtype=np.float64).ravel()
    w = w / max(float(np.sum(w)), 1e-12)

    order = np.argsort(de)
    de_s = de[order]
    cw = np.cumsum(w[order])

    def pct(q: float) -> float:
        return float(de_s[min(np.searchsorted(cw, q), de_s.size - 1)])

    return {
        "mean": float(np.sum(de * w)),
        "median": pct(0.50),
        "p95": pct(0.95),
        "max": float(np.max(de)),
        "within_1": float(np.sum(w[de <= 1.0])),
        "within_2": float(np.sum(w[de <= 2.0])),
        "within_3": float(np.sum(w[de <= 3.0])),
    }
