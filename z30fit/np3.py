"""NP3 解析、内嵌曲线提取、512 字节 LUT 段打包。

结构（Z30 实测，见交接文档 §4.1，本模块按字节复核后做了修正）：

    0x00  "NCP\\0"
    0x04  00 00 01 00
    0x08  00 00 00 04
    0x0C  版本 ASCII：0300 = Z30 / 0310 = 带灵活颜色
    0x10  00 00 02 00
    0x14  u32 名称长度（20）
    0x18  名称 20 字节
    0x2C  00 00
    0x2E  参数记录 × N，每条 10 字节：u16 id(LE) | 0000 | u16 len(BE) | data | 0000
    末尾  00 00 终止符

关键实测：**248 字节的原厂导出件只有 20 条参数记录（id 0x03–0x16），没有 LUT 段**；
1012 字节的云创件在尾部多出 512 字节曲线表（256 × u16BE）+ 4 字节终止符。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import LUT_MAX, LUT_POINTS

MAGIC = b"NCP\x00"
RECORD_START = 0x2E

# 614 字节载荷里的段偏移（Phase 2 用，本阶段只产出 LUT 段）
PAYLOAD_SIZE = 614
PAYLOAD_LUT_OFFSET = 0x066
PAYLOAD_LUT_BYTES = 512
PAYLOAD_CONTROL_POINTS = 0x02F  # 4 组 (x, y)，仅给机身 LCD 画曲线


@dataclass
class Record:
    id: int
    data: bytes
    offset: int

    def as_be_u16(self) -> int | None:
        return int.from_bytes(self.data, "big") if len(self.data) == 2 else None


@dataclass
class Np3:
    raw: bytes
    version: str
    name: str
    records: list[Record] = field(default_factory=list)
    terminator_ok: bool = True
    path: str = ""

    @property
    def size(self) -> int:
        return len(self.raw)

    @property
    def base_id(self) -> int | None:
        """基础优化校准 ID：0x0001 = 标准，0x0020 = 灵活颜色（Z30 不认）。"""
        for r in self.records:
            if r.id == 0x03:
                return r.as_be_u16()
        return None

    @property
    def param_ids(self) -> list[int]:
        return [r.id for r in self.records]

    def describe(self) -> dict:
        return {
            "path": self.path,
            "size": self.size,
            "version": self.version,
            "name": self.name,
            "base_id": self.base_id,
            "records": len(self.records),
            "record_ids": [f"0x{i:02X}" for i in self.param_ids],
            "terminator_ok": self.terminator_ok,
        }


def parse(path_or_bytes: str | Path | bytes) -> Np3:
    if isinstance(path_or_bytes, (str, Path)):
        p = Path(path_or_bytes)
        raw = p.read_bytes()
        path = str(p)
    else:
        raw = bytes(path_or_bytes)
        path = ""

    if len(raw) < RECORD_START:
        raise ValueError("文件太短，不是 NP3")
    if raw[:4] != MAGIC:
        raise ValueError(f"魔数不符：{raw[:4]!r}（应为 NCP\\0）")

    version = raw[0x0C:0x10].decode("ascii", "replace")
    name_len = int.from_bytes(raw[0x14:0x18], "big")
    name = raw[0x18 : 0x18 + min(name_len, 0x14)].rstrip(b"\x00").decode("ascii", "replace")

    records: list[Record] = []
    off = RECORD_START
    term_ok = False
    while off + 6 <= len(raw):
        rid = int.from_bytes(raw[off : off + 2], "little")
        ln = int.from_bytes(raw[off + 4 : off + 6], "big")
        # 记录 id 只到 0x20（0x03–0x16 是标准参数，0x17–0x20 是灵活颜色附加项，
        # 0x01 是作者/注释串）。超出范围说明已经走进尾部曲线块，停止解析。
        if rid > 0x40 or ln > len(raw) - off - 6:
            break
        data = raw[off + 6 : off + 6 + ln]
        records.append(Record(id=rid, data=data, offset=off))
        off += 6 + ln + 2
        if off + 2 <= len(raw) and raw[off : off + 2] == b"\x00\x00" and rid == 0x16:
            # 记录区之后紧跟终止符
            term_ok = True
    term_ok = term_ok or raw[-2:] == b"\x00\x00"

    return Np3(raw=raw, version=version, name=name, records=records, terminator_ok=term_ok, path=path)


def extract_curve(np3: Np3 | str | Path) -> tuple[np.ndarray, dict] | None:
    """提取尾部内嵌曲线，返回 (0..1 的曲线, 元信息)；没有曲线段则返回 None。

    规则：取文件末尾 4 字节终止符之前的一段 u16BE 表，要求非递减且最大値 ≤ 32767。
    优先尝试 256 点（与机身 LUT 点数一致），失败则退 252 点。
    """
    if not isinstance(np3, Np3):
        np3 = parse(np3)
    raw = np3.raw

    for n in (LUT_POINTS, 252, 128):
        nbytes = n * 2
        end = len(raw) - 4
        start = end - nbytes
        if start < 0:
            continue
        vals = np.frombuffer(raw[start:end], dtype=">u2").astype(np.int64)
        if vals.max() > LUT_MAX:
            continue
        if not np.all(np.diff(vals) >= 0):
            continue
        curve = vals.astype(np.float64) / LUT_MAX
        info = {
            "offset": start,
            "points": n,
            "terminator": raw[end:].hex(),
            "first": int(vals[0]),
            "last": int(vals[-1]),
            "zero_prefix": int(np.sum(vals == 0)),
            "note": "从文件尾部按 256 点规则反推；若后续真机读回不符，可用 --curve-points 覆盖",
        }
        return curve, info
    return None


def resample_curve(curve: np.ndarray, n: int = LUT_POINTS) -> np.ndarray:
    """把任意点数的曲线重采样到 n 点（线性插值，端点对齐）。"""
    curve = np.asarray(curve, dtype=np.float64)
    src = np.linspace(0.0, 1.0, curve.size)
    dst = np.linspace(0.0, 1.0, n)
    return np.interp(dst, src, curve)


def pack_lut_segment(table: np.ndarray) -> bytes:
    """256 × u16 大端 → 512 字节 LUT 段。"""
    t = np.asarray(table)
    if t.size != LUT_POINTS:
        raise ValueError(f"LUT 段必须是 {LUT_POINTS} 点，收到 {t.size}")
    t = np.clip(t.astype(np.int64), 0, LUT_MAX).astype(">u2")
    return t.tobytes()


def unpack_lut_segment(seg: bytes) -> np.ndarray:
    if len(seg) != PAYLOAD_LUT_BYTES:
        raise ValueError("LUT 段长度必须是 512 字节")
    return np.frombuffer(seg, dtype=">u2").astype(np.int64)


def curve_to_control_points(curve_0_1: np.ndarray, count: int = 4) -> list[tuple[int, int]]:
    """给机身 LCD 画曲线用的 4 组 (x, y)，8 位量化。仅用于显示近似。"""
    xs = np.linspace(0.0, 1.0, count + 2)[1:-1]
    ys = np.interp(xs, np.linspace(0.0, 1.0, len(curve_0_1)), curve_0_1)
    return [(int(round(x * 255)), int(round(y * 255))) for x, y in zip(xs, ys)]
