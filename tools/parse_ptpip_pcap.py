"""从抓包文件里提取 PTP/IP / MTP/IP 的握手信息（主要是主机 GUID）。

用途：尼康 Z 相机只接受「已配对」的主机，而配对动作只能由官方
Wireless Transmitter Utility 完成。配对之后，WTU 用的 GUID 被相机记住；
把这个 GUID 取出来，自己的客户端就能以「已配对主机」的身份连进去。

抓包用 Windows 自带的 pktmon，不需要装 Wireshark：

    pktmon filter remove
    pktmon filter add -p 15740 -t TCP
    pktmon start --capture --pkt-size 0 --file out/ptpip-probe/cap.etl
    （此时用 WTU 配对）
    pktmon stop
    pktmon etl2pcap out/ptpip-probe/cap.etl -o out/ptpip-probe/cap.pcap

然后：
    python tools/parse_ptpip_pcap.py out/ptpip-probe/cap.pcap

脚本同时认两种握手格式：
    MTP/IP：type(4) + GUID(16) + 主机名(ASCII, NUL 结尾)
    PTP/IP：type(4) + 连接号(4) + GUID(16) + 主机名(UTF-16, NUL 结尾) + 版本(4)
"""

from __future__ import annotations

import struct
import sys


def read_pcap(path: str) -> list[bytes]:
    """返回每个 TCP 连接的 payload 列表（按出现顺序）。"""
    with open(path, "rb") as f:
        blob = f.read()

    if len(blob) < 24:
        return []

    magic = blob[:4]
    if magic in (b"\xd4\xc3\xb2\xa1", b"\x4d\x3c\xb2\xa1"):
        endian = "<"
    elif magic in (b"\xa1\xb2\xc3\xd4", b"\xa1\xb2\x3c\x4d"):
        endian = ">"
    else:
        return []

    # pcapng 是另一种格式（Block），pktmon 默认出 pcap，这里遇到就退回暴力扫描
    if blob[:4] == b"\x0a\x0d\x0d\x0a":
        return []

    linktype = struct.unpack_from(endian + "I", blob, 20)[0]
    payloads: list[bytes] = []
    off = 24

    while off + 16 <= len(blob):
        _, _, incl_len, _ = struct.unpack_from(endian + "IIII", blob, off)
        off += 16
        if incl_len <= 0 or off + incl_len > len(blob):
            break
        frame = blob[off : off + incl_len]
        off += incl_len

        payload = _tcp_payload(frame, linktype)
        if payload:
            payloads.append(payload)

    return payloads


def _tcp_payload(frame: bytes, linktype: int) -> bytes:
    """从一帧里取出 TCP payload；不是 TCP 就返回空。"""
    if linktype == 1:  # Ethernet
        if len(frame) < 14:
            return b""
        eth_type = struct.unpack_from(">H", frame, 12)[0]
        pos = 14
        while eth_type in (0x8100, 0x88A8):  # VLAN
            if len(frame) < pos + 4:
                return b""
            eth_type = struct.unpack_from(">H", frame, pos + 2)[0]
            pos += 4
        if eth_type != 0x0800:
            return b""
    elif linktype == 113:  # Linux cooked
        if len(frame) < 16:
            return b""
        if struct.unpack_from(">H", frame, 14)[0] != 0x0800:
            return b""
        pos = 16
    elif linktype == 101:  # raw IP
        pos = 0
    else:
        return b""

    if len(frame) < pos + 20:
        return b""
    ver_ihl = frame[pos]
    if ver_ihl >> 4 != 4:
        return b""
    ihl = (ver_ihl & 0x0F) * 4
    if len(frame) < pos + ihl + 20:
        return b""
    if frame[pos + 9] != 6:  # TCP
        return b""

    tcp = pos + ihl
    data_off = (frame[tcp + 12] >> 4) * 4
    start = tcp + data_off
    if start >= len(frame):
        return b""
    return frame[start:]


def looks_like_init(payload: bytes) -> bool:
    """握手包第一个字段是包类型 1（小端）。"""
    return len(payload) >= 20 and payload[:4] == b"\x01\x00\x00\x00"


def describe(payload: bytes) -> dict:
    """判断是 MTP/IP 还是 PTP/IP 的握手包，并取出 GUID / 主机名 / 版本。"""
    body = payload[4:]

    # 先按 PTP/IP 试：4 字节连接号 + 16 字节 GUID + UTF-16 名 + 4 字节版本
    if len(body) >= 20:
        conn = struct.unpack_from("<I", body, 0)[0]
        guid = body[4:20]
        rest = body[20:]
        # UTF-16 名的特征是每个 ASCII 字符后面跟一个 0x00
        if len(rest) >= 4 and rest[1:2] == b"\x00" and rest[3:4] == b"\x00":
            name = ""
            i = 0
            while i + 1 < len(rest):
                code = struct.unpack_from("<H", rest, i)[0]
                if code == 0:
                    i += 2
                    break
                name += chr(code)
                i += 2
            version = struct.unpack_from("<I", rest, i)[0] if i + 4 <= len(rest) else 0
            return {
                "proto": "PTP/IP",
                "conn": conn,
                "guid": guid.hex(),
                "name": name,
                "version": f"{version >> 16}.{version & 0xFFFF}",
            }

    # 再按 MTP/IP 试：16 字节 GUID + ASCII 名
    guid = body[:16]
    rest = body[16:]
    name = rest.split(b"\x00")[0].decode("ascii", "replace")
    if all(0x20 <= b < 0x7F for b in rest.split(b"\x00")[0]) and rest.endswith(b"\x00"):
        return {"proto": "MTP/IP", "conn": None, "guid": guid.hex(), "name": name, "version": "n/a"}

    return {"proto": "未知", "conn": None, "guid": guid.hex(), "name": name, "version": "?"}


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1

    path = sys.argv[1]
    payloads = read_pcap(path)
    hits = [p for p in payloads if looks_like_init(p)]

    if not hits:
        # 退回暴力扫描：pcap 解析失败或链路层不认识时，直接在原始字节里找
        with open(path, "rb") as f:
            blob = f.read()
        idx = blob.find(b"\x01\x00\x00\x00")
        if idx < 0:
            print(f"没找到握手包（解析到 {len(payloads)} 个 TCP payload）。")
            print("确认抓包时确实有一次新的连接（WTU 启动/配对会新建连接）。")
            return 2
        print(f"pcap 结构没认出来，改用原始字节扫描（偏移 0x{idx:X}）")
        hits = [blob[idx : idx + 200]]

    print(f"找到 {len(hits)} 个握手包\n")
    seen = set()
    for i, payload in enumerate(hits, 1):
        info = describe(payload)
        key = (info["proto"], info["guid"])
        if key in seen:
            continue
        seen.add(key)
        print(f"[{i}] 协议 {info['proto']}")
        print(f"    GUID     {info['guid']}")
        print(f"    主机名   {info['name']!r}")
        print(f"    版本     {info['version']}")
        if info["conn"] is not None:
            print(f"    连接号   {info['conn']}")
        print()

    if len(seen) == 1:
        guid = next(iter(seen))[1]
        print("只有一种身份，直接用它跑探针：")
        print(f"    python tools/probe_ptpip.py --guid {guid} --read --proto auto")
    else:
        print("有多个身份，取 WTU 自己那次连接的（通常是最后一个）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
