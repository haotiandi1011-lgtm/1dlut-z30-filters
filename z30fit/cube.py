"""`.cube` LUT 读写（1D / 3D）与插值应用。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np


@dataclass
class CubeLUT:
    """一个 .cube LUT。

    kind     : 1 或 3
    size     : 1D 为点数，3D 为每边点数
    3D 时 data 形状为 (size, size, size, 3)，索引顺序 [b, g, r]（.cube 里红轴变化最快）
    1D 时 data 形状为 (size, 3)
    """

    kind: int
    size: int
    data: np.ndarray
    domain_min: np.ndarray
    domain_max: np.ndarray
    title: str = ""
    path: str = ""

    @property
    def is_3d(self) -> bool:
        return self.kind == 3

    # -- 输入归一化 -------------------------------------------------------
    def _to_unit(self, rgb: np.ndarray) -> np.ndarray:
        span = self.domain_max - self.domain_min
        span = np.where(np.abs(span) < 1e-12, 1.0, span)
        return np.clip((rgb - self.domain_min) / span, 0.0, 1.0)

    # -- 应用 -------------------------------------------------------------
    def apply(self, rgb: np.ndarray) -> np.ndarray:
        rgb = np.asarray(rgb, dtype=np.float64)
        unit = self._to_unit(rgb)

        if self.kind == 1:
            return self._apply_1d(unit)
        return self._apply_3d(unit)

    def _apply_1d(self, unit: np.ndarray) -> np.ndarray:
        grid = np.linspace(0.0, 1.0, self.size)
        out = np.empty_like(unit)
        for c in range(3):
            out[..., c] = np.interp(unit[..., c], grid, self.data[:, c])
        return np.clip(out, 0.0, 1.0)

    def _apply_3d(self, unit: np.ndarray) -> np.ndarray:
        n = self.size
        coord = unit * (n - 1)
        i0 = np.floor(coord).astype(np.int64)
        i0 = np.clip(i0, 0, n - 2)
        f = coord - i0

        r0, g0, b0 = i0[..., 0], i0[..., 1], i0[..., 2]
        r1, g1, b1 = r0 + 1, g0 + 1, b0 + 1
        fr, fg, fb = f[..., 0:1], f[..., 1:2], f[..., 2:3]

        d = self.data
        c000 = d[b0, g0, r0]
        c100 = d[b0, g0, r1]
        c010 = d[b0, g1, r0]
        c110 = d[b0, g1, r1]
        c001 = d[b1, g0, r0]
        c101 = d[b1, g0, r1]
        c011 = d[b1, g1, r0]
        c111 = d[b1, g1, r1]

        c00 = c000 * (1 - fr) + c100 * fr
        c01 = c010 * (1 - fr) + c110 * fr
        c10 = c001 * (1 - fr) + c101 * fr
        c11 = c011 * (1 - fr) + c111 * fr

        c0 = c00 * (1 - fg) + c01 * fg
        c1 = c10 * (1 - fg) + c11 * fg

        return np.clip(c0 * (1 - fb) + c1 * fb, 0.0, 1.0)

    # -- 1D 曲线视图 --------------------------------------------------------
    def as_1d_channels(self) -> np.ndarray:
        """返回 (N, 3) 的逐通道曲线；3D LUT 取中性轴采样。"""
        if self.kind == 1:
            return self.data
        ramp = np.linspace(0.0, 1.0, self.size)
        neutral = np.stack([ramp, ramp, ramp], axis=-1)
        return self.apply(neutral)

    def neutral_ramp(self, n: int = 256) -> tuple[np.ndarray, np.ndarray]:
        """沿中性轴采样，返回 (输入灰度, 输出 RGB)。"""
        x = np.linspace(0.0, 1.0, n)
        pts = np.stack([x, x, x], axis=-1)
        return x, self.apply(pts)

    def describe(self) -> str:
        return f"{self.kind}D size={self.size} data={self.data.shape}"


def load_cube(path: str | Path) -> CubeLUT:
    """读取 .cube。容忍注释、空行、TITLE、DOMAIN_MIN/MAX。"""
    path = Path(path)
    kind = 0
    size = 0
    domain_min = np.zeros(3)
    domain_max = np.ones(3)
    title = ""
    values: list[tuple[float, float, float]] = []

    text = path.read_text(encoding="utf-8", errors="replace")
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        upper = line.upper()
        if upper.startswith("TITLE"):
            title = line.split(None, 1)[1].strip().strip('"') if len(line.split(None, 1)) > 1 else ""
            continue
        if upper.startswith("LUT_1D_SIZE"):
            kind, size = 1, int(line.split()[1])
            continue
        if upper.startswith("LUT_3D_SIZE"):
            kind, size = 3, int(line.split()[1])
            continue
        if upper.startswith("DOMAIN_MIN"):
            domain_min = np.array([float(v) for v in line.split()[1:4]])
            continue
        if upper.startswith("DOMAIN_MAX"):
            domain_max = np.array([float(v) for v in line.split()[1:4]])
            continue
        if upper.startswith(("LUT_1D_INPUT_RANGE", "LUT_3D_INPUT_RANGE")):
            parts = [float(v) for v in line.split()[1:3]]
            domain_min = np.full(3, parts[0])
            domain_max = np.full(3, parts[1])
            continue

        parts = line.split()
        if len(parts) == 3:
            try:
                values.append((float(parts[0]), float(parts[1]), float(parts[2])))
            except ValueError:
                continue

    if kind == 0 or size == 0:
        raise ValueError(f"{path.name}: 缺少 LUT_1D_SIZE / LUT_3D_SIZE")
    if len(values) != size**kind:
        raise ValueError(f"{path.name}: 数据行数 {len(values)} 与声明尺寸 {size}^{kind} 不符")

    arr = np.array(values, dtype=np.float64)
    data = arr.reshape((size, size, size, 3)) if kind == 3 else arr.reshape((size, 3))

    return CubeLUT(
        kind=kind,
        size=size,
        data=data,
        domain_min=domain_min,
        domain_max=domain_max,
        title=title,
        path=str(path),
    )


def write_cube_1d(path: str | Path, curves: np.ndarray, title: str = "") -> None:
    """把 (N, 3) 的 1D 曲线写成 .cube（便于在第三方软件里交叉验证）。"""
    curves = np.asarray(curves, dtype=np.float64)
    n = curves.shape[0]
    lines = [f'TITLE "{title}"' if title else 'TITLE "z30fit"', "", f"LUT_1D_SIZE {n}", "DOMAIN_MIN 0.0 0.0 0.0", "DOMAIN_MAX 1.0 1.0 1.0", ""]
    for i in range(n):
        r, g, b = curves[i]
        lines.append(f"{r:.6f} {g:.6f} {b:.6f}")
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def identity_1d(n: int = 33) -> CubeLUT:
    g = np.linspace(0.0, 1.0, n)
    data = np.stack([g, g, g], axis=-1)
    return CubeLUT(kind=1, size=n, data=data, domain_min=np.zeros(3), domain_max=np.ones(3), title="identity")


def identity_3d(n: int = 17) -> CubeLUT:
    g = np.linspace(0.0, 1.0, n)
    b, gg, r = np.meshgrid(g, g, g, indexing="ij")
    data = np.stack([r, gg, b], axis=-1)
    return CubeLUT(kind=3, size=n, data=data, domain_min=np.zeros(3), domain_max=np.ones(3), title="identity")
