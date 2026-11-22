"""从抓包文件里提取 PTP/IP / MTP/IP 的握手信息（主要是主机 GUID）。

用途：尼康 Z 相机只接受「已配对」的主机，而配对动作只能由官方
Wireless Transmitter Utility 完成。配对之后，WTU 用的 GUID 被相机记住；
把这个 GUID 取出来，自己的客户端就能以「已配对主机」的身份连进去。

抓包用 Windows 自带的 pktmon，不需要装 Wireshark：

    pktmon filter remove
    pktmon filter add PTPIP -p 15740 -t TCP
    pktmon start --capture --pkt-size 0 --file-name out/ptpip-probe/cap.etl
    （此时用 WTU 配对）
    pktmon stop
    pktmon etl2pcap out/ptpip-probe/cap.etl -o out/ptpip-probe/cap.pcap

然后：
    python tools/parse_ptpip_pcap.py out/ptpip-probe/cap.pcap

脚本同时认两种握手格式：
    MTP/IP：type(4) + GUID(16) + 主机名(ASCII, NUL 结尾)
    PTP/IP：type(4) + 连接号(4) + GUID(16) + 主机名(UTF-16, NUL 结尾) + 版本(4)

实现要点：按 TCP 五元组重组字节流再找包首，避免握手包被分片/合并时漏掉。
"""

from __future__ import annotations

import struct
import sys

PKT_INIT_CMD_REQ = 1
PKT_TYPES = {
    1: "InitCommandRequest",
    2: "InitCommandAck",
    3: "InitEventRequest",
    4: "InitEventAck",
    5: "InitFail",
    6: "OperationRequest",
    7: "OperationResponse",
    8: "Event",
    9: "StartData",
    10: "Data",
    12: "EndData",
    13: "Cancel",
}


# -- 抓包文件解析 ---------------------------------------------------------


def read_pcapng(blob: bytes) -> list[tuple[int, bytes]]:
    """解析 pcapng：Section Header / Interface Description / Enhanced / Simple Packet。

    返回 [(linktype, frame_bytes), ...]。
    """
    out: list[tuple[int, bytes]] = []
    pos = 0
    n = len(blob)
    endian = "<"
    linktype = 1

    while pos + 12 <= n:
        btype = struct.unpack_from(endian + "I", blob, pos)[0]

        if btype == 0x0A0D0D0A:  # Section Header Block：由 byte-order magic 决定字节序
            magic = struct.unpack_from("<I", blob, pos + 8)[0]
            endian = "<" if magic == 0x1A2B3C4D else ">"

        blen = struct.unpack_from(endian + "I", blob, pos + 4)[0]
        if blen < 12 or pos + blen > n:
            break

        if btype == 0x00000001:  # Interface Description Block
            linktype = struct.unpack_from(endian + "H", blob, pos + 8)[0]
        elif btype == 0x00000006:  # Enhanced Packet Block
            cap_len = struct.unpack_from(endian + "I", blob, pos + 20)[0]
            start = pos + 28
            if 0 < cap_len <= n - start:
                out.append((linktype, blob[start : start + cap_len]))
        elif btype == 0x00000003:  # Simple Packet Block
            orig_len = struct.unpack_from(endian + "I", blob, pos + 8)[0]
            start = pos + 12
            if 0 < orig_len <= n - start:
                out.append((linktype, blob[start : start + orig_len]))

        pos += blen

    return out


def read_frames(path: str) -> list[tuple[int, bytes]]:
    """把 pcap 或 pcapng 统一读成 [(linktype, frame), ...]。"""
    with open(path, "rb") as f:
        blob = f.read()

    if len(blob) >= 4 and blob[:4] == b"\x0a\x0d\x0d\x0a":
        return read_pcapng(blob)

    if len(blob) < 24:
        return []

    magic = blob[:4]
    if magic in (b"\xd4\xc3\xb2\xa1", b"\x4d\x3c\xb2\xa1"):
        endian = "<"
    elif magic in (b"\xa1\xb2\xc3\xd4", b"\xa1\xb2\x3c\x4d"):
        endian = ">"
    else:
        return []

    linktype = struct.unpack_from(endian + "I", blob, 20)[0]
    frames: list[tuple[int, bytes]] = []
    off = 24
    while off + 16 <= len(blob):
        _, _, incl_len, _ = struct.unpack_from(endian + "IIII", blob, off)
        off += 16
        if incl_len <= 0 or off + incl_len > len(blob):
            break
        frames.append((linktype, blob[off : off + incl_len]))
        off += incl_len
    return frames


# -- 以太网 / IP / TCP 拆包 ------------------------------------------------


def _l3_offset(frame: bytes, linktype: int) -> int:
    """返回 IP 头在帧内的偏移；不是 IPv4 返回 -1。"""
    if linktype == 1:  # Ethernet
        if len(frame) < 14:
            return -1
        eth_type = struct.unpack_from(">H", frame, 12)[0]
        pos = 14
        while eth_type in (0x8100, 0x88A8):  # VLAN / QinQ
            if len(frame) < pos + 4:
                return -1
            eth_type = struct.unpack_from(">H", frame, pos + 2)[0]
            pos += 4
        return pos if eth_type == 0x0800 else -1
    if linktype == 113:  # Linux cooked
        if len(frame) < 16:
            return -1
        return 16 if struct.unpack_from(">H", frame, 14)[0] == 0x0800 else -1
    if linktype == 101:  # raw IP
        return 0
    return -1


def tcp_segment(frame: bytes, linktype: int):
    """拆出 (src, sport, dst, dport, seq, flags, payload)；不是 TCP 返回 None。"""
    pos = _l3_offset(frame, linktype)
    if pos < 0 or len(frame) < pos + 20:
        return None
    ver_ihl = frame[pos]
    if ver_ihl >> 4 != 4:
        return None
    ihl = (ver_ihl & 0x0F) * 4
    if len(frame) < pos + ihl + 20 or frame[pos + 9] != 6:
        return None

    total_len = struct.unpack_from(">H", frame, pos + 2)[0]
    src = ".".join(str(b) for b in frame[pos + 12 : pos + 16])
    dst = ".".join(str(b) for b in frame[pos + 16 : pos + 20])

    tcp = pos + ihl
    sport, dport, seq = struct.unpack_from(">HHI", frame, tcp)
    flags = frame[tcp + 13]
    data_off = (frame[tcp + 12] >> 4) * 4
    start = tcp + data_off

    # 用 IP 总长裁剪，避免把以太网填充算成 payload
    end = pos + total_len if total_len >= ihl + data_off else len(frame)
    end = min(end, len(frame))
    if start >= end:
        payload = b""
    else:
        payload = frame[start:end]
    return src, sport, dst, dport, seq, flags, payload


def build_streams(frames: list[tuple[int, bytes]]) -> dict:
    """按五元组重组字节流。返回 {(src,sport,dst,dport): bytes}。"""
    chunks: dict[tuple, list[tuple[int, bytes]]] = {}
    for linktype, frame in frames:
        seg = tcp_segment(frame, linktype)
        if not seg:
            continue
        src, sport, dst, dport, seq, _flags, payload = seg
        if not payload:
            continue
        chunks.setdefault((src, sport, dst, dport), []).append((seq, payload))

    streams: dict[tuple, bytes] = {}
    for key, parts in chunks.items():
        parts.sort(key=lambda x: x[0])  # 按序列号排序，处理乱序
        streams[key] = b"".join(p for _, p in parts)
    return streams


# -- 握手包识别 -----------------------------------------------------------


def hexdump(data: bytes, width: int = 16, limit: int = 128) -> str:
    data = data[:limit]
    lines = []
    for i in range(0, len(data), width):
        row = data[i : i + width]
        hexpart = " ".join(f"{b:02x}" for b in row)
        text = "".join(chr(b) if 0x20 <= b < 0x7F else "." for b in row)
        lines.append(f"  {i:04x}  {hexpart:<{width * 3}}  {text}")
    return "\n".join(lines)


def describe(payload: bytes) -> dict:
    """判断是 MTP/IP 还是 PTP/IP 的握手包，并取出 GUID / 主机名 / 版本。"""
    body = payload[4:]
    candidates = []

    # PTP/IP：4 字节连接号 + 16 字节 GUID + UTF-16LE 名 + 4 字节版本
    if len(body) >= 24:
        conn = struct.unpack_from("<I", body, 0)[0]
        guid = body[4:20]
        rest = body[20:]
        name_chars = []
        i = 0
        ok = True
        while i + 1 < len(rest):
            code = struct.unpack_from("<H", rest, i)[0]
            if code == 0:
                i += 2
                break
            if code < 0x20 or code > 0x7E:
                ok = False
                break
            name_chars.append(chr(code))
            i += 2
        if ok and name_chars and i + 4 <= len(rest):
            version = struct.unpack_from("<I", rest, i)[0]
            candidates.append(
                {
                    "proto": "PTP/IP",
                    "conn": conn,
                    "guid": guid.hex(),
                    "name": "".join(name_chars),
                    "version": f"{version >> 16}.{version & 0xFFFF}",
                    "score": 2,
                }
            )

    # MTP/IP：16 字节 GUID + ASCII 名（单 NUL 结尾，后面没有版本字段）
    if len(body) >= 18:
        guid = body[:16]
        rest = body[16:]
        nul = rest.find(b"\x00")
        if nul > 0:
            name = rest[:nul]
            if all(0x20 <= b < 0x7F for b in name):
                candidates.append(
                    {
                        "proto": "MTP/IP",
                        "conn": None,
                        "guid": guid.hex(),
                        "name": name.decode("ascii"),
                        "version": "n/a",
                        # MTP/IP 的名称后面紧跟的就是结尾，长度吻合才给高分
                        "score": 3 if len(rest) - nul <= 4 else 2,
                    }
                )

    if not candidates:
        return {"proto": "未知", "conn": None, "guid": payload[4:20].hex(), "name": "", "version": "?"}
    candidates.sort(key=lambda c: c["score"], reverse=True)
    return candidates[0]


def find_handshakes(streams: dict) -> list[tuple[tuple, int, bytes]]:
    """在重组后的流里找 InitCommandRequest。返回 [(key, offset, slice), ...]。"""
    hits: list[tuple[tuple, int, bytes]] = []
    for key, data in streams.items():
        src, sport, dst, dport = key
        if dport != 15740:  # 只看客户端 → 相机方向
            continue
        # 优先：流首就是握手（新建连接一定会先发 Init）
        if data[:4] == b"\x01\x00\x00\x00":
            hits.append((key, 0, data[:160]))
            continue
        # 兜底：流中任意位置出现
        idx = data.find(b"\x01\x00\x00\x00")
        if idx >= 0:
            hits.append((key, idx, data[idx : idx + 160]))
    return hits


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    path = sys.argv[1]

    frames = read_frames(path)
    streams = build_streams(frames)
    hits = find_handshakes(streams)

    print(f"抓包文件：{path}")
    print(f"帧数 {len(frames)}，TCP 流 {len(streams)}，其中到 15740 的流 "
          f"{sum(1 for k in streams if k[3] == 15740)} 条")

    if not hits:
        print("\n没找到握手包。可能原因：")
        print("  1. 抓包开始时 WTU 已经连上了（要先把 WTU 关掉再抓）；")
        print("  2. WTU 里硬件类型选错了（必须是「照相机内置 Wi-Fi」）；")
        print("  3. 相机不在等待配对状态。")
        # 把通往 15740 的流前 32 字节打出来，便于判断到底收到了什么
        for key, data in streams.items():
            if key[3] == 15740:
                print(f"\n流 {key[0]}:{key[1]} → {key[2]}:{key[3]}  共 {len(data)} 字节，开头：")
                print(hexdump(data, limit=48))
        return 2

    print(f"\n找到 {len(hits)} 个握手包\n")
    seen = set()
    for i, (key, offset, payload) in enumerate(hits, 1):
        info = describe(payload)
        ptype = struct.unpack_from("<I", payload, 0)[0]
        sig = (info["proto"], info["guid"])
        print(f"[{i}] {key[0]}:{key[1]} → {key[2]}:{key[3]}  流内偏移 {offset}")
        print(f"    包类型   {ptype} ({PKT_TYPES.get(ptype, '?')})")
        print(f"    协议     {info['proto']}")
        print(f"    GUID     {info['guid']}")
        print(f"    主机名   {info['name']!r}")
        print(f"    版本     {info['version']}")
        if info["conn"] is not None:
            print(f"    连接号   {info['conn']}")
        print("    原始字节：")
        print(hexdump(payload, limit=80))
        print()
        if sig in seen:
            continue
        seen.add(sig)

    guids = {g for _, g in seen}
    protos = {p for p, _ in seen}
    if len(guids) == 1 and "未知" not in protos:
        guid = guids.pop()
        proto = protos.pop()
        print("=" * 60)
        print(f"结论：WTU 用的是 {proto}，GUID = {guid}")
        print("接着跑探针（只读，不改相机）：")
        print(f"    python tools/probe_ptpip.py --guid {guid} --read --proto auto")
    else:
        print("有多个身份或协议没认出来，请看上面的原始字节，取 WTU 那次连接的。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
