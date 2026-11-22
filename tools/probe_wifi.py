"""用 WTU 的握手身份走 Wi-Fi，实测能否读写机身 C 槽位。

背景
----
Z30 的 Wi-Fi 通道是标准 PTP/IP（TCP 15740）。相机只接受「已配对主机」，
判断依据是 Init Command Request 里那 12 字节主机 ID。
我们从 WTU 的抓包里取到了这个 ID，所以这里直接复刻它。

抓包得到的事实（2026-11-22，Z30 固件 V1.20）
--------------------------------------------
包结构：4 字节小端「总长度（含这 4 字节）」+ 包体

Init 请求包体 56 字节：
    类型 4 (=1) + 连接号 4 + 主机ID 12 + UTF-16 名 + NUL + 版本 4 (=1.0)

Init 应答包体 54 字节：
    类型 4 (=2) + 连接号 4 + 相机ID 16 + UTF-16 名 + NUL + 版本 4

操作请求体：类型 4 (=6) + DataPhaseInfo 4 + 操作码 2 + 事务号 4 + 参数 4*n
操作应答体：类型 4 (=7) + 操作码 2 + 事务号 4 + 参数 4*n
数据阶段  ：StartData 9 / Data 10 / EndData 12，均带 4 字节事务号

DeviceInfo 里明确列出支持 0x90CC（读）、0x90CD（写）、0x90CE（删槽）。

用法
----
    python tools/probe_wifi.py --read              # 只读 C9
    python tools/probe_wifi.py --roundtrip         # 读出原样写回，验证写通道
    python tools/probe_wifi.py --slot 0xC9 --read

安全约定：默认只读；--roundtrip 写回的是刚读到的原始字节，画面不会变化。
"""

from __future__ import annotations

import argparse
import socket
import struct
import sys
import time

HOST = "192.168.1.1"
PORT = 15740

# 抓包中 WTU 发出的 Init Command Request 包体（56 字节），逐字节复刻。
INIT_BODY_HEX = (
    "01000000"                          # 类型 1
    "95670f1d"                          # 连接号（WTU 随机取的值）
    "9d45d44a9db6a2c45fcde713"          # 主机 ID（12 字节，相机记住的就是它）
    "4c004100500054004f0050002d0032005100480052004a004f0052003100"  # "LAPTOP-2QHRJOR1"
    "0000"                              # 名字结束符
    "00000100"                          # 协议版本 1.0
)

PKT_INIT_REQ = 1
PKT_INIT_ACK = 2
PKT_INIT_EVENT_REQ = 3
PKT_INIT_EVENT_ACK = 4
PKT_INIT_FAIL = 5
PKT_OP_REQ = 6
PKT_OP_RESP = 7
PKT_EVENT = 8
PKT_START_DATA = 9
PKT_DATA = 10
PKT_END_DATA = 12

OP_GET_DEVICE_INFO = 0x1001
OP_OPEN_SESSION = 0x1002
OP_CLOSE_SESSION = 0x1003
OP_9010 = 0x9010
OP_GET_PICT_CTRL = 0x90CC
OP_SET_PICT_CTRL = 0x90CD

RC_OK = 0x2001
RC_NAMES = {
    0x2001: "OK",
    0x2002: "GeneralError",
    0x2003: "SessionNotOpen",
    0x2004: "InvalidTransactionID",
    0x2005: "OperationNotSupported",
    0x2006: "ParameterNotSupported",
    0x2007: "IncompleteTransfer",
    0x2008: "InvalidStorageID",
    0x2009: "InvalidObjectHandle",
    0x200A: "DevicePropNotSupported",
    0x200B: "InvalidObjectFormatCode",
    0x200C: "StoreFull",
    0x200D: "ObjectWriteProtected",
    0x200E: "StoreReadOnly",
    0x200F: "AccessDenied",
    0x2012: "PartialDeletion",
    0x2013: "StoreNotAvailable",
    0x2015: "NoValidObjectInfo",
    0x2016: "InvalidCodeFormat",
    0x2017: "UnknownVendorCode",
    0x2018: "CaptureAlreadyTerminated",
    0x2019: "DeviceBusy",
    0x201A: "InvalidParentObject",
    0x201B: "InvalidDevicePropFormat",
    0x201C: "InvalidDevicePropValue",
    0x201D: "InvalidParameter",
    0x201E: "SessionAlreadyOpen",
    0x201F: "TransactionCancelled",
}


def rc_text(code: int) -> str:
    return RC_NAMES.get(code, f"0x{code:04X}")


class PtpIp:
    def __init__(self, host: str = HOST, port: int = PORT, timeout: float = 8.0, verbose: bool = True):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.verbose = verbose
        self.sock: socket.socket | None = None
        self.event_sock: socket.socket | None = None
        self.conn = 1
        self.txn = 1

    def log(self, msg: str) -> None:
        if self.verbose:
            print(msg)

    # -- 传输层 ----------------------------------------------------------

    def _recv_exact(self, sock: socket.socket, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("对端关闭了连接")
            buf += chunk
        return buf

    def _send_packet(self, body: bytes) -> None:
        assert self.sock is not None
        total = len(body) + 4
        self.sock.sendall(struct.pack("<I", total) + body)

    def _recv_packet(self) -> tuple[int, bytes]:
        assert self.sock is not None
        total = struct.unpack("<I", self._recv_exact(self.sock, 4))[0]
        if total < 4:
            raise RuntimeError(f"长度字段异常：{total}")
        body = self._recv_exact(self.sock, total - 4)
        ptype = struct.unpack_from("<I", body, 0)[0] if len(body) >= 4 else -1
        return ptype, body

    # -- 握手 ------------------------------------------------------------

    def connect(self) -> None:
        self.sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        self.sock.settimeout(self.timeout)
        self.log(f"TCP 已连接 {self.host}:{self.port}")

    def init(self) -> dict:
        body = bytes.fromhex(INIT_BODY_HEX)
        if len(body) != 56:
            raise AssertionError(f"内置 Init 包应为 56 字节，实际 {len(body)}")
        self._send_packet(body)
        self.log("  → Init Command Request（复刻 WTU 的主机 ID）")

        ptype, ack = self._recv_packet()
        if ptype == PKT_INIT_FAIL:
            reason = struct.unpack_from("<I", ack, 4)[0] if len(ack) >= 8 else -1
            raise RuntimeError(f"相机拒绝握手（Init_Fail，原因 {reason}）")
        if ptype != PKT_INIT_ACK:
            raise RuntimeError(f"期望 Init_Ack(2)，收到类型 {ptype}")

        conn = struct.unpack_from("<I", ack, 4)[0]
        cam_id = ack[8:24].hex()
        # 名字从 24 开始
        chars = []
        i = 24
        while i + 1 < len(ack):
            code = struct.unpack_from("<H", ack, i)[0]
            if code == 0:
                break
            chars.append(chr(code))
            i += 2
        name = "".join(chars)
        self.conn = conn
        self.log(f"  ← Init_Ack：连接号 {conn}，相机「{name}」，相机 ID {cam_id}")
        return {"conn": conn, "name": name, "cam_id": cam_id}

    def open_event_channel(self) -> None:
        """事件通道：另开一条 TCP，发 Init_Event_Request。失败不影响主通道。"""
        try:
            s = socket.create_connection((self.host, self.port), timeout=self.timeout)
            s.settimeout(self.timeout)
            body = struct.pack("<II", PKT_INIT_EVENT_REQ, self.conn)
            s.sendall(struct.pack("<I", len(body) + 4) + body)
            total = struct.unpack("<I", self._recv_exact(s, 4))[0]
            resp = self._recv_exact(s, total - 4)
            ptype = struct.unpack_from("<I", resp, 0)[0]
            if ptype == PKT_INIT_EVENT_ACK:
                self.event_sock = s
                self.log("  ← 事件通道已建立")
            else:
                s.close()
                self.log(f"  ← 事件通道被拒（类型 {ptype}），继续用主通道")
        except Exception as exc:  # noqa: BLE001
            self.log(f"  ← 事件通道失败（{type(exc).__name__}: {exc}），继续用主通道")

    # -- 事务 ------------------------------------------------------------

    def transact(self, code: int, params=(), data_out: bytes | None = None, expect_data: bool = False):
        txn = self.txn
        self.txn += 1
        phase = 2 if data_out is not None else (3 if expect_data else 1)

        body = struct.pack("<IIHI", PKT_OP_REQ, phase, code, txn)
        for p in params:
            body += struct.pack("<I", p)
        self._send_packet(body)

        if data_out is not None:
            self._send_packet(struct.pack("<IIQ", PKT_START_DATA, txn, len(data_out)))
            self._send_packet(struct.pack("<II", PKT_DATA, txn) + data_out)
            self._send_packet(struct.pack("<II", PKT_END_DATA, txn))

        chunks: list[bytes] = []
        while True:
            ptype, pkt = self._recv_packet()
            if ptype == PKT_OP_RESP:
                rcode = struct.unpack_from("<H", pkt, 4)[0]
                rtxn = struct.unpack_from("<I", pkt, 6)[0]
                if rtxn != txn:
                    continue
                return rcode, b"".join(chunks)
            if ptype in (PKT_START_DATA, PKT_DATA, PKT_END_DATA):
                rtxn = struct.unpack_from("<I", pkt, 4)[0]
                if rtxn != txn:
                    continue
                if ptype == PKT_START_DATA:
                    total = struct.unpack_from("<Q", pkt, 8)[0]
                    self.log(f"    ← StartData：共 {total} 字节")
                elif ptype == PKT_DATA:
                    chunks.append(pkt[8:])
                else:
                    chunks.append(pkt[8:])
            elif ptype == PKT_EVENT:
                continue
            else:
                self.log(f"    ← 忽略包类型 {ptype}")

    # -- 业务 ------------------------------------------------------------

    def get_device_info(self) -> dict:
        rcode, data = self.transact(OP_GET_DEVICE_INFO, expect_data=True)
        if rcode != RC_OK:
            raise RuntimeError(f"GetDeviceInfo 失败：{rc_text(rcode)}")
        return parse_device_info(data)

    def open_session(self) -> None:
        rcode, _ = self.transact(OP_OPEN_SESSION, (1,))
        if rcode == 0x201E:  # SessionAlreadyOpen
            self.log("  会话已开，先关再开")
            self.transact(OP_CLOSE_SESSION)
            rcode, _ = self.transact(OP_OPEN_SESSION, (1,))
        if rcode != RC_OK:
            raise RuntimeError(f"OpenSession 失败：{rc_text(rcode)}")

    def get_pict_ctrl(self, slot: int) -> bytes:
        rcode, data = self.transact(OP_GET_PICT_CTRL, (slot, 0), expect_data=True)
        if rcode != RC_OK:
            raise RuntimeError(f"读槽位 0x{slot:02X} 失败：{rc_text(rcode)}")
        return data

    def send_9010_no_wait(self) -> None:
        """发 0x9010(0x2001) 但不等待回应。

        抓包里 WTU 发出这个包之后也从未收到过回应，说明它本来就不保证有应答
        （或者是异步的）。所以这里只发不等，避免把整个流程卡在 socket 超时上。
        """
        txn = self.txn
        self.txn += 1
        body = struct.pack("<IIHI", PKT_OP_REQ, 1, OP_9010, txn) + struct.pack("<I", 0x2001)
        self._send_packet(body)
        self.log(f"  → 0x9010(0x2001) 已发送，不等回应（txn={txn}）")

    def set_pict_ctrl(self, slot: int, payload: bytes, framing: str = "end") -> None:
        """写槽位。framing="end" 把数据放进 EndData（相机自己就是这么发的）；
        framing="data" 用 Data 包携带数据、EndData 空收尾（ISO 15740 的常见写法）。
        """
        txn = self.txn
        self.txn += 1
        body = struct.pack("<IIHI", PKT_OP_REQ, 2, OP_SET_PICT_CTRL, txn)
        body += struct.pack("<I", slot) + struct.pack("<I", 0)
        self._send_packet(body)

        self._send_packet(struct.pack("<IIQ", PKT_START_DATA, txn, len(payload)))
        if framing == "end":
            self._send_packet(struct.pack("<II", PKT_END_DATA, txn) + payload)
        else:
            self._send_packet(struct.pack("<II", PKT_DATA, txn) + payload)
            self._send_packet(struct.pack("<II", PKT_END_DATA, txn))

        while True:
            ptype, pkt = self._recv_packet()
            if ptype == PKT_OP_RESP:
                rcode = struct.unpack_from("<H", pkt, 4)[0]
                rtxn = struct.unpack_from("<I", pkt, 6)[0]
                if rtxn != txn:
                    continue
                if rcode != RC_OK:
                    raise RuntimeError(f"写槽位 0x{slot:02X} 失败：{rc_text(rcode)}（分帧 {framing}）")
                return
            if ptype in (PKT_START_DATA, PKT_DATA, PKT_END_DATA, PKT_EVENT):
                continue

    def close(self) -> None:
        try:
            if self.sock:
                try:
                    self.transact(OP_CLOSE_SESSION)
                except Exception:
                    pass
                self.sock.close()
        except Exception:
            pass
        try:
            if self.event_sock:
                self.event_sock.close()
        except Exception:
            pass


def ptp_string(data: bytes, pos: int) -> tuple[str, int]:
    n = data[pos]
    if n == 0:
        return "", pos + 1
    raw = data[pos + 1: pos + 1 + n * 2]
    return raw.decode("utf-16-le", "replace").rstrip("\x00"), pos + 1 + n * 2


def parse_device_info(data: bytes) -> dict:
    pos = 0
    std_ver = struct.unpack_from("<H", data, pos)[0]; pos += 2
    vendor_id = struct.unpack_from("<I", data, pos)[0]; pos += 4
    vendor_ver = struct.unpack_from("<H", data, pos)[0]; pos += 2
    vendor_desc, pos = ptp_string(data, pos)
    pos += 2  # FunctionalMode

    def code_array(p: int) -> tuple[list[int], int]:
        n = struct.unpack_from("<I", data, p)[0]
        p += 4
        vals = list(struct.unpack_from(f"<{n}H", data, p))
        return vals, p + n * 2

    ops, pos = code_array(pos)
    _evts, pos = code_array(pos)
    _props, pos = code_array(pos)
    _cfmts, pos = code_array(pos)
    _ifmts, pos = code_array(pos)
    manufacturer, pos = ptp_string(data, pos)
    model, pos = ptp_string(data, pos)
    firmware, pos = ptp_string(data, pos)
    serial, pos = ptp_string(data, pos)
    return {
        "std_ver": std_ver,
        "vendor_id": vendor_id,
        "vendor_ver": vendor_ver,
        "vendor_desc": vendor_desc,
        "ops": ops,
        "manufacturer": manufacturer,
        "model": model,
        "firmware": firmware,
        "serial": serial,
    }


def describe_payload(payload: bytes) -> None:
    print(f"  载荷长度：{len(payload)} 字节")
    if len(payload) != 614:
        print("  ⚠ 不是 614 字节，可能不是 Picture Control 载荷")
        return
    name_bytes = payload[2:22]
    name = "".join(chr(b) if 0x20 <= b <= 0x7E else "." for b in name_bytes).strip(". ")
    print(f"  名称字段：{name!r}")
    print(f"  参数区 0x18：{payload[0x18:0x1F].hex(' ')}")
    print(f"  族标记 0x2C：0x{payload[0x2C]:02X}")
    lut = [struct.unpack_from(">H", payload, 0x66 + i * 2)[0] for i in range(256)]
    print(f"  LUT：{lut[0]} → {lut[128]} → {lut[255]}（0..32767）")
    rising = all(lut[i] <= lut[i + 1] for i in range(255))
    print(f"  单调不降：{'是' if rising else '否'}")


def hexdump(data: bytes, limit: int = 96) -> str:
    data = data[:limit]
    lines = []
    for i in range(0, len(data), 16):
        row = data[i:i + 16]
        hexpart = " ".join(f"{b:02x}" for b in row)
        text = "".join(chr(b) if 0x20 <= b < 0x7F else "." for b in row)
        lines.append(f"    {i:04x}  {hexpart:<47}  {text}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=HOST)
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--slot", default="0xC9")
    ap.add_argument("--read", action="store_true", help="只读（默认）")
    ap.add_argument("--roundtrip", action="store_true", help="读出后原样写回，验证写通道")
    ap.add_argument("--scan", action="store_true", help="把 0..8 与 0xC9..0xD1 全扫一遍")
    ap.add_argument("--list", action="store_true", help="扫 0xC0..0xE0，打印每个槽位的长度与名称，用于确定 C1..C9 编号")
    ap.add_argument("--start", default="0xC0", help="--list 的起始槽位")
    ap.add_argument("--end", default="0xE0", help="--list 的结束槽位")
    ap.add_argument("--try-9010", action="store_true", help="额外测试 0x9010（会污染连接，默认不做）")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    slot = int(args.slot, 16) if str(args.slot).lower().startswith("0x") else int(args.slot)
    cam = PtpIp(args.host, args.port, verbose=not args.quiet)

    print("=" * 64)
    print(f"Z30 Wi-Fi 探针 · 槽位 0x{slot:02X} · 目标 {args.host}:{args.port}")
    print("=" * 64)

    try:
        cam.connect()
        cam.init()
    except Exception as exc:  # noqa: BLE001
        print(f"\n[失败] 握手未通过：{type(exc).__name__}: {exc}")
        print("\n排查：")
        print("  1. 电脑是否连着相机 Wi-Fi（相机屏上应显示已连接）")
        print("  2. WTU 是否还开着并占着会话 —— 先把它关掉")
        print("  3. 相机是否熄屏退出了「连接到计算机」")
        return 2

    try:
        cam.open_event_channel()
        cam.open_session()
        info = cam.get_device_info()
        print(f"\n相机：{info['manufacturer']} {info['model']}  固件 {info['firmware']}")
        print(f"序列号：{info['serial']}")
        print(f"支持操作码 {len(info['ops'])} 项")
        for code, label in ((0x90CC, "读 Picture Control"), (0x90CD, "写 Picture Control")):
            mark = "✔" if code in info["ops"] else "✘"
            print(f"  0x{code:04X} {label}：{mark}")

        # 先不带 0x9010 读一次：这一版一定能拿到回应（36 字节），把它打出来
        # 才能判断这 36 字节到底是什么东西。
        if args.list:
            lo = int(args.start, 16) if str(args.start).lower().startswith("0x") else int(args.start)
            hi = int(args.end, 16) if str(args.end).lower().startswith("0x") else int(args.end)
            print(f"\n-- 扫描 0x{lo:02X}..0x{hi:02X}（只读）--")
            found = []
            for s in range(lo, hi + 1):
                try:
                    d = cam.get_pict_ctrl(s)
                    if len(d) == 614:
                        nm = "".join(chr(b) if 32 <= b < 127 else "." for b in d[2:22]).strip(". ")
                        lut0 = struct.unpack_from(">H", d, 0x66)[0]
                        lut128 = struct.unpack_from(">H", d, 0x66 + 128 * 2)[0]
                        lut255 = struct.unpack_from(">H", d, 0x66 + 255 * 2)[0]
                        found.append(s)
                        print(f"  0x{s:02X}: 614  名称 {nm!r:<24} LUT {lut0}/{lut128}/{lut255}")
                    else:
                        asc = "".join(chr(b) if 32 <= b < 127 else "." for b in d)
                        print(f"  0x{s:02X}: {len(d):>3}  {asc[:44]}")
                except Exception as exc:  # noqa: BLE001
                    print(f"  0x{s:02X}: 失败 {type(exc).__name__}: {exc}")
            print(f"\n返回 614 字节的槽位：{[hex(x) for x in found]}")
            print("这些就是可写的自定义槽位；36 字节的是内置影调描述符，不能写。")
            return 0

        print(f"\n-- 读取槽位 0x{slot:02X}（未发 0x9010）--")
        before = cam.get_pict_ctrl(slot)
        describe_payload(before)
        if len(before) != 614:
            print("  原始字节：")
            print(hexdump(before))

        best_slot, best_data = (slot, before) if len(before) == 614 else (None, None)

        if args.scan:
            print("\n-- 扫描槽位（未发 0x9010）--")
            for s in list(range(0, 9)) + list(range(0xC9, 0xD2)):
                if s == slot:
                    continue
                try:
                    d = cam.get_pict_ctrl(s)
                    flag = ""
                    if len(d) == 614:
                        flag = " ← 614 字节"
                        if best_slot is None:
                            best_slot, best_data = s, d
                    print(f"  0x{s:02X}: {len(d):>3} 字节{flag}")
                except Exception as exc:  # noqa: BLE001
                    print(f"  0x{s:02X}: 失败 {type(exc).__name__}: {exc}")

        if args.try_9010:
            print("\n-- 测试 0x9010(0x2001)（已知会污染连接，仅用于排查）--")
            cam.send_9010_no_wait()
            time.sleep(1.5)
            try:
                after = cam.get_pict_ctrl(slot)
                print(f"  重新读取：{len(after)} 字节")
            except Exception as exc:  # noqa: BLE001
                print(f"  重新读取失败：{type(exc).__name__}: {exc}")
        else:
            print("\n-- 已跳过 0x9010（实测不需要它，发了反而会让下一次读取超时）--")

        if args.roundtrip:
            if best_slot is None:
                print("\n[跳过写测试] 没有任何槽位返回 614 字节，不确定载荷格式，不写")
            else:
                print(f"\n-- 原样写回槽位 0x{best_slot:02X}（内容不变，只为验证写通道）--")
                wrote = False
                for framing in ("end", "data"):
                    try:
                        cam.set_pict_ctrl(best_slot, best_data, framing)
                        print(f"  写入成功（分帧方式：{framing}）")
                        wrote = True
                        break
                    except Exception as exc:  # noqa: BLE001
                        print(f"  分帧 {framing} 失败：{exc}")
                if wrote:
                    again = cam.get_pict_ctrl(best_slot)
                    same = again == best_data
                    print(f"  回读比对：{'逐字节一致 ✔' if same else '不一致 ✘'}")
                    if not same:
                        print(f"    原 {len(best_data)} 字节，回读 {len(again)} 字节")

        print("\n结论：Wi-Fi 通道可用。")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"\n[失败] {type(exc).__name__}: {exc}")
        return 3
    finally:
        cam.close()


if __name__ == "__main__":
    raise SystemExit(main())
