"""配方包 `.z30`：把 614 字节载荷 + 元数据打成一个文件，方便跨机器/跨平台传递。

格式（小端）：

    +0x00   8   魔数 b"Z30PC1\\0\\0"
    +0x08   4   元数据长度 u32
    +0x0C   n   元数据 JSON（UTF-8）
    ...   614  载荷本体

设计原则：载荷是唯一真值，元数据只是说明。所以即使 JSON 丢了，也能从载荷里
把曲线完整恢复出来；反过来不行。
"""

from __future__ import annotations

import json
import struct
import time
from pathlib import Path

import numpy as np

from z30fit import __version__

from . import payload as pl

MAGIC = b"Z30PC1\x00\x00"
HEADER = 12
SUFFIX = ".z30"


def build(payload: bytes, meta: dict) -> bytes:
    if len(payload) != pl.PAYLOAD_SIZE:
        raise ValueError(f"载荷必须是 {pl.PAYLOAD_SIZE} 字节")
    body = json.dumps(meta, ensure_ascii=False).encode("utf-8")
    return MAGIC + struct.pack("<I", len(body)) + body + bytes(payload)


def parse(raw: bytes) -> tuple[bytes, dict]:
    if not raw.startswith(MAGIC) or len(raw) < HEADER + pl.PAYLOAD_SIZE:
        raise ValueError("不是有效的配方包")
    (n,) = struct.unpack_from("<I", raw, 8)
    meta_raw = raw[HEADER : HEADER + n]
    payload = raw[HEADER + n : HEADER + n + pl.PAYLOAD_SIZE]
    if len(payload) != pl.PAYLOAD_SIZE:
        raise ValueError("配方包里的载荷长度不对")
    try:
        meta = json.loads(meta_raw.decode("utf-8"))
    except Exception:  # noqa: BLE001
        meta = {}
    return payload, meta


def save(path: str | Path, payload: bytes, meta: dict) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(build(payload, meta))
    return p


def load(path: str | Path) -> tuple[bytes, dict]:
    return parse(Path(path).read_bytes())


def make_meta(name: str, source: str, metrics: dict | None = None,
              tone: dict | None = None, note: str = "") -> dict:
    return {
        "name": name,
        "source": source,
        "note": note,
        "metrics": metrics or {},
        "tone": tone or {},
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "tool": f"Z30影调工具 {__version__}",
    }


def table_from_payload(payload: bytes) -> np.ndarray:
    info = pl.parse_payload(payload)
    return np.asarray(info["lut"], dtype=np.uint16)


def tone_from_table(table: np.ndarray) -> dict:
    v = table.astype(np.float64) / 32767.0
    grid = np.linspace(0, 1, 256)
    return {
        "black": float(v[0]),
        "white": float(v[-1]),
        "mid_18": float(np.interp(0.18, grid, v)),
        "contrast_25_75": float((np.interp(0.75, grid, v) - np.interp(0.25, grid, v)) / 0.5),
        "slope_toe": float(v[8] - v[0]) * 12.5,
        "slope_mid": float(v[136] - v[120]) * 16.0,
        "slope_shoulder": float(v[-1] - v[-9]) * 12.5,
    }
