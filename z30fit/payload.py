"""614 字节 PTP 载荷的解析、构建与对比。

布局（z-cloud-ai 来源 + 本机真机回读验证 2026-10-31）：

    +0x000   2   版本 02 00
    +0x002  20   ASCII 名称（NUL 补齐）
    +0x016   2   保留
    +0x018   7   参数区（真机回读验证，见下）
    +0x01F   4   保留 0
    +0x023   1   0x01 标志位
    +0x024   2   "I0" 机型/系列标记
    +0x026   1   0
    +0x027   1   0xFF
    +0x028   1   0
    +0x029   1   0xFF
    +0x02A   1   0x01
    +0x02B   1   0x0F
    +0x02C   1   族 / 序号标记（实测 0x05 可用）
    +0x02D   2   保留
    +0x02F   8   4 组 (x, y) 控制点，仅用于机身 LCD 画曲线
    +0x037  47   保留 0（真机探测：不影响影调/色彩）
    +0x066 512   256 × u16 大端 LUT，值域 0..32767  ← 成像真值
    ----------  0x66 + 512 = 0x266 = 614

参数区（2026-10-31 真机发现：对带曲线的 614 配方按「调整」改参数后，
逐字节 diff 读回定位）——顺序与机身菜单一致：

    +0x018  快速锐化    （1/级，-2 → 0xFE）
    +0x019  锐化        （0.25/级，+9 → 0x24）
    +0x01A  中等锐化    （0.25/级，+5 → 0x14）
    +0x01B  清晰度      （0.25/级，-5 → 0xEC）
    +0x01C  对比度      （有自定义曲线时被机身禁用，恒 0）
    +0x01D  亮度        （有自定义曲线时被机身禁用，恒 0）
    +0x01E  饱和度      （0.25/级，+3 → 0x0C）

注意：z-cloud-ai 把这 7 字节当成「保留/固定值」（+0x019=0x08、+0x01A=0x04
实为锐化+2、中等锐化+1 的默认值），因为它从没改过参数。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from . import LUT_MAX, LUT_POINTS

PAYLOAD_SIZE = 614
LUT_OFFSET = 0x066
LUT_BYTES = 512
NAME_OFFSET = 0x002
NAME_LEN = 20
POINT_COUNT_OFFSET = 0x019  # 旧称「点位数据字节数」，实为参数区锐化位（见上）
FAMILY_OFFSET = 0x02C
CONTROL_POINTS_OFFSET = 0x02F
CONTROL_POINTS_LEN = 8

# 参数区偏移（真机回读验证）
PARAM_OFFSET = {
    "quick_sharp": 0x18,   # 快速锐化
    "sharp": 0x19,         # 锐化
    "mid_sharp": 0x1A,     # 中等锐化
    "clarity": 0x1B,       # 清晰度
    "contrast": 0x1C,      # 对比度（有曲线时禁用）
    "brightness": 0x1D,    # 亮度（有曲线时禁用）
    "saturation": 0x1E,    # 饱和度
}
# 每个参数的步长（编码 = round(value/step) 补码）。真机回读验证：全部 0.25/级（×4），
# 唯快速锐化是 1/级（×1）。
PARAM_STEP = {
    "quick_sharp": 1.0,
    "sharp": 0.25,
    "mid_sharp": 0.25,
    "clarity": 0.25,
    "contrast": 0.25,
    "brightness": 0.25,
    "saturation": 0.25,
}


def encode_param(value: float, key: str) -> int:
    """参数值 → 字节（有符号补码）。"""
    step = PARAM_STEP[key]
    return round(value / step) & 0xFF


def decode_param(raw: int, key: str) -> float:
    v = raw if raw < 128 else raw - 256
    return round(v * PARAM_STEP[key], 2)


def set_params(payload: bytes, params: dict) -> bytes:
    """把参数写进载荷（只改参数区，其余字节不动）。

    params 键：quick_sharp / sharp / mid_sharp / clarity / contrast /
    brightness / saturation。值为浮点（如 sharp=9.0 表示锐化 +9）。
    对比度/亮度在有自定义曲线时会被机身禁用，写了也不生效。
    """
    out = bytearray(payload)
    if len(out) < PAYLOAD_SIZE:
        raise ValueError(f"载荷至少 {PAYLOAD_SIZE} 字节")
    for key, off in PARAM_OFFSET.items():
        if key in params and params[key] is not None:
            out[off] = encode_param(params[key], key)
    return bytes(out)


def parse_payload(data: bytes) -> dict:
    """把 614 字节载荷拆成字段。返回的每一项都带原始 hex，方便人工核对。"""
    b = bytes(data)
    if len(b) < PAYLOAD_SIZE:
        raise ValueError(f"载荷至少 {PAYLOAD_SIZE} 字节，收到 {len(b)}")

    lut = np.frombuffer(b[LUT_OFFSET : LUT_OFFSET + LUT_BYTES], dtype=">u2").astype(np.int64)
    cp_raw = b[CONTROL_POINTS_OFFSET : CONTROL_POINTS_OFFSET + CONTROL_POINTS_LEN]
    control_points = [(cp_raw[i], cp_raw[i + 1]) for i in range(0, CONTROL_POINTS_LEN, 2)]

    return {
        "size": len(b),
        "version": b[0:2].hex(" "),
        "name": b[NAME_OFFSET : NAME_OFFSET + NAME_LEN].rstrip(b"\x00").decode("ascii", "replace"),
        "name_raw": b[NAME_OFFSET : NAME_OFFSET + NAME_LEN].hex(" "),
        "point_bytes": b[POINT_COUNT_OFFSET],
        "family": b[FAMILY_OFFSET],
        "control_points": control_points,
        "control_points_raw": cp_raw.hex(" "),
        "lut": lut,
        "lut_first": int(lut[0]),
        "lut_last": int(lut[-1]),
        "lut_monotone": bool(np.all(np.diff(lut) >= 0)),
        "lut_max": int(lut.max()),
        "trailer": b[LUT_OFFSET + LUT_BYTES :].hex(" "),
        # 头部前 0x66 字节里的非零字节位置，用来和真机对照哪些是标志位
        "nonzero_before_lut": [f"0x{i:02X}" for i in range(LUT_OFFSET) if b[i] != 0],
    }


def describe_payload(data: bytes) -> str:
    p = parse_payload(data)
    lines = [
        f"载荷长度      {p['size']} 字节",
        f"版本          {p['version']}",
        f"名称          {p['name']!r}",
        f"点位字节数    {p['point_bytes']}",
        f"族/序号       {p['family']}",
        f"控制点        {p['control_points']}  (raw {p['control_points_raw']})",
        f"LUT           首值 {p['lut_first']}，末值 {p['lut_last']}，最大 {p['lut_max']}，"
        f"{'单调' if p['lut_monotone'] else '⚠ 非单调'}",
        f"尾部          {p['trailer'] or '（无）'}",
        f"LUT 段前非零  {' '.join(p['nonzero_before_lut']) or '（全零）'}",
    ]
    return "\n".join(lines)


def build_payload(
    curve: np.ndarray,
    name: str = "Z30FIT",
    template: bytes | None = None,
    control_points: bool = True,
) -> bytes:
    """把 256 点曲线（0..1 或 0..32767 均可）打成 614 字节载荷。

    template 给定时：逐字节复制模板，只覆盖名称、控制点、LUT 段。
    这是首次真机写入的推荐做法——参数区、标志位原样保留，变量只剩 LUT。
    """
    c = np.asarray(curve, dtype=np.float64).ravel()
    if c.size != LUT_POINTS:
        raise ValueError(f"曲线必须是 {LUT_POINTS} 点，收到 {c.size}")
    if c.max() <= 1.0 + 1e-9:
        table = np.rint(np.clip(c, 0, 1) * LUT_MAX).astype(np.int64)
    else:
        table = np.rint(np.clip(c, 0, LUT_MAX)).astype(np.int64)

    out = bytearray(template if template is not None else bytes(PAYLOAD_SIZE))
    if len(out) < PAYLOAD_SIZE:
        raise ValueError(f"模板长度不足 {PAYLOAD_SIZE}")

    if template is None:
        out[0:2] = b"\x02\x00"
        out[POINT_COUNT_OFFSET] = 0x08

    out[NAME_OFFSET : NAME_OFFSET + NAME_LEN] = name.encode("ascii", "replace")[:NAME_LEN].ljust(NAME_LEN, b"\x00")
    if control_points:
        cp = curve_to_control_points(table)
        for i, (x, y) in enumerate(cp):
            out[CONTROL_POINTS_OFFSET + i * 2] = x
            out[CONTROL_POINTS_OFFSET + i * 2 + 1] = y
    out[LUT_OFFSET : LUT_OFFSET + LUT_BYTES] = table.astype(">u2").tobytes()
    return bytes(out)


def curve_to_control_points(table: np.ndarray, count: int = 4) -> list[tuple[int, int]]:
    """给 LCD 画图用的 4 组 (x, y)，8 位量化。只影响屏幕显示，不影响成像。"""
    t = np.asarray(table, dtype=np.float64) / LUT_MAX
    xs = np.linspace(0.0, 1.0, count + 2)[1:-1]
    ys = np.interp(xs, np.linspace(0.0, 1.0, t.size), t)
    return [(int(round(x * 255)), int(round(y * 255))) for x, y in zip(xs, ys)]


def fallback_template(family: int = 0x05) -> bytes:
    """一个结构合法的 614 字节骨架。

    字节布局照抄 z-cloud-ai 的 `PcPayload614.fallbackTemplate()`，那一份是他们
    从真机 0x90CC 读回两条真实配方（Fuji Provia / Fuji Astia）逐字节比对出来的。
    我们自己的槽位全是 36 字节的「仅参数」形态，拿不到 614 模板，只能用这个。

        +0x000  02 00        版本
        +0x019  08           点位数据字节数
        +0x01A  04
        +0x023  01
        +0x024  "I0"         机型/系列标记
        +0x027  FF
        +0x029  FF
        +0x02A  01
        +0x02B  0F
        +0x02C  family       族/序号标记（Provia=0x05, Astia=0x06）
        +0x02F  4 组控制点
        +0x066  512 字节 LUT
    """
    p = bytearray(PAYLOAD_SIZE)
    p[0x000] = 0x02
    p[0x001] = 0x00
    p[0x019] = 0x08
    p[0x01A] = 0x04
    p[0x023] = 0x01
    p[0x024] = 0x49  # 'I'
    p[0x025] = 0x30  # '0'
    p[0x027] = 0xFF
    p[0x029] = 0xFF
    p[0x02A] = 0x01
    p[0x02B] = 0x0F
    p[0x02C] = family & 0xFF
    return bytes(p)


def build_payload_614(
    curve: np.ndarray,
    name: str = "Z30FIT",
    family: int = 0x05,
    template: bytes | None = None,
    params: dict | None = None,
) -> bytes:
    """曲线 → 614 字节载荷。template 为空时用 fallback_template()。

    params 可选：dict，键见 PARAM_OFFSET（sharp/mid_sharp/clarity/saturation
    等），值为浮点。用于把真实配方的参数一并写进机身（「提拟合度」）。
    """
    base = fallback_template(family) if template is None else bytes(template)
    if len(base) != PAYLOAD_SIZE:
        raise ValueError(f"模板必须是 {PAYLOAD_SIZE} 字节，收到 {len(base)}")
    payload = build_payload(curve, name=name, template=base, control_points=True)
    if params:
        payload = set_params(payload, params)
    return payload


def diff_payloads(a: bytes, b: bytes) -> list[tuple[int, int, int]]:
    """逐字节对比两份载荷，返回 [(偏移, a 的字节, b 的字节)]。"""
    n = min(len(a), len(b))
    return [(i, a[i], b[i]) for i in range(n) if a[i] != b[i]]


def save(path: str | Path, data: bytes) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


def load(path: str | Path) -> bytes:
    return Path(path).read_bytes()
