"""PTP/IP 探针 —— 验证 Z30 的无线通道是否放行 0x90CC / 0x90CD。

为什么要有这个脚本
------------------
iOS 第三方 App 没有 USB Host / 原始 PTP 权限（Apple DTS 原话：There are no low-level
USB APIs on iOS），所以安卓端那套「USB 直连写 C1–C9」在 iOS 上无法复现。

唯一候选是 Wi-Fi：尼康官方《Wireless Transmitter Utility》的支持列表里有 Z30，
菜单路径「设定菜单 → 连接到 PC → 网络设定」，访问点模式下相机 IP 固定 192.168.1.1。
这就是标准 PTP/IP（ISO 15740 over TCP，端口 15740），而不是 SnapBridge 的私有 HTTP。

PTP/IP 在 iOS 上只是 TCP socket —— 公开 API 完全允许。所以 iOS 能不能写机身，
只取决于一件事：**这条通道是否放行尼康私有的 0x90CC / 0x90CD**。

这个脚本就是来回答那件事的，而且是**在 Windows 上、不需要 iPhone** 就能回答。

判读方式（三层证据，越往后越硬）
--------------------------------
1. GetDeviceInfo 返回的 OperationsSupported 表里有没有 0x90CC / 0x90CD；
2. 实际发 0x90CC 读 C9，看响应码是不是 0x2001（OK）；
3. `--write-test` 写 C9 后读回逐字节比对。

第 1 层只说明「相机自称支持」，第 2、3 层才是硬证据 —— 有的固件会在表里列出，
但按传输层拒绝执行。

安全约定
--------
**默认只读。** `--write-test` 必须显式指定，且：
  - 先读 C9 原内容备份到 out/ptpip-probe/；
  - 原内容是 614 字节就**原样写回**（内容不变，纯通道验证）；
  - 原内容不是 614 字节（36 字节「仅参数」或空槽）才写恒等曲线，并在输出里明确警告。
全程不碰 0x90CE（删槽，破坏性）。

用法
----
    python tools/probe_ptpip.py                      # 只读诊断：连上、看操作码表
    python tools/probe_ptpip.py --read               # 再读 C9，打印名称与曲线
    python tools/probe_ptpip.py --write-test         # 写 C9 并读回校验（先备份）
    python tools/probe_ptpip.py --host 192.168.1.2   # 基础结构模式下路由器分配的 IP
"""

from __future__ import annotations

import argparse
import os
import socket
import struct
import sys
import time

# 从 tools/ 里跑时，把仓库根目录加进来（z30fit 在根目录）。
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# -- PTP/IP 包类型（ISO 15740 / PIMA 15740 第 5 章） -------------------------
PKT_INIT_CMD_REQ = 1
PKT_INIT_CMD_ACK = 2
PKT_INIT_EVENT_REQ = 3
PKT_INIT_EVENT_ACK = 4
PKT_INIT_FAIL = 5
PKT_OP_REQ = 6
PKT_OP_RESP = 7
PKT_EVENT = 8
PKT_START_DATA = 9
PKT_DATA = 10
PKT_END_DATA = 12

PKT_NAMES = {
    PKT_INIT_CMD_REQ: "InitCommandRequest",
    PKT_INIT_CMD_ACK: "InitCommandAck",
    PKT_INIT_EVENT_REQ: "InitEventRequest",
    PKT_INIT_EVENT_ACK: "InitEventAck",
    PKT_INIT_FAIL: "InitFail",
    PKT_OP_REQ: "OperationRequest",
    PKT_OP_RESP: "OperationResponse",
    PKT_EVENT: "Event",
    PKT_START_DATA: "StartData",
    PKT_DATA: "Data",
    PKT_END_DATA: "EndData",
}

# -- PTP 操作码 -------------------------------------------------------------
OP_GET_DEVICE_INFO = 0x1001
OP_OPEN_SESSION = 0x1002
OP_CLOSE_SESSION = 0x1003
OP_GET_PICT_CTRL = 0x90CC
OP_SET_PICT_CTRL = 0x90CD

# 尼康 Z 系列 Wi-Fi 的 PIN 配对（libgphoto2 issue #1314 逆向并验证）
OP_GET_PAIRING_CODE = 0x952B
OP_CONFIRM_PAIRING = 0x935A

RC_OK = 0x2001
RC_OK_WITH_DATA = 0x2002
RC_NAMES = {
    0x2001: "OK",
    0x2002: "OK（带数据）",
    0x2003: "GeneralError —— 相机拒绝了这条指令",
    0x2004: "SessionNotOpen",
    0x2005: "OperationNotSupported —— 这条指令在本通道上不实现",
    0x2006: "ParameterNotSupported —— 参数个数/取值不对",
    0x2009: "DevicePropNotSupported",
    0x2019: "DeviceBusy —— 相机忙（可能在传图 / 睡眠 / 屏幕未唤醒）",
    0x201D: "InvalidParameter",
    0xA001: "AccessDenied",
    0xA002: "SessionAlreadyOpen",
}

SLOT_C9 = 0x00D1
SLOT_NAMES = {0x00C9 + i: f"C{i + 1}" for i in range(9)}

# 固定的 GUID：让相机记住这台主机，重连时不用重新配对。
DEFAULT_GUID = bytes.fromhex("5A333054 4F4E452D 494F532D 50524F42".replace(" ", ""))


def rc_text(code: int) -> str:
    return RC_NAMES.get(code, f"未知响应码 0x{code:04X}")


class InitFail(RuntimeError):
    """相机用 Init_Fail 拒绝握手。

    reason 0x00000001 是尼康的「当前网络配置文件已配对到别的主机」——
    不是包格式错误，而是相机认得 GUID、且这个 GUID 不在白名单里。
    解决办法在相机上：换配置文件，或新建一个未配对的配置文件。
    """

    def __init__(self, reason: int):
        self.reason = reason
        if reason == 1:
            text = (
                "reason 0x00000001：当前网络配置文件已经配对给别的主机。\n"
                "  这不是包格式问题，是相机按 GUID 白名单拒绝了。\n"
                "  解决：在相机上「网络设定 → 新建配置文件 → 直接连接到计算机」，\n"
                "        用这个全新的（未配对）配置文件重试 —— 未配对时相机接受任意 GUID。"
            )
        else:
            text = f"reason 0x{reason:08X}：相机拒绝了握手（原因未知）"
        super().__init__(text)


class PtpIp:
    """一条 PTP/IP 命令连接。

    刻意不建事件连接（不发 InitEventRequest）：规范里事件连接是可选的，
    不建的话相机不会往命令连接推 Event 包，读包逻辑就不用处理变长的事件参数。
    命令与响应本身不依赖事件。
    """

    def __init__(
        self,
        host: str,
        port: int = 15740,
        timeout: float = 8.0,
        verbose: bool = True,
        proto: str = "mtp",
    ):
        self.host = host
        self.port = port
        self.verbose = verbose
        # "mtp" = 微软 MTP over TCP/IP（尼康 SnapBridge 时代常见）
        # "ptp" = 标准 PTP/IP（ISO 15740，ZR 那一代 / WTU 用）
        self.proto = proto
        self.sock = None
        self.event_sock = None
        self.conn = 1
        self._buf = b""
        self.txn = 1

    # -- 底层读写 ---------------------------------------------------------
    def _log(self, text: str) -> None:
        if self.verbose:
            print(text)

    def _fill(self, n: int) -> bytes:
        """精确读 n 字节（先吃缓冲区）。"""
        while len(self._buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError(
                    "相机关闭了 TCP 连接。常见原因：不接受这个 GUID（需要先配对）、"
                    "相机屏幕已熄 / 已退出连接模式、或另一台设备正连着相机。"
                )
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def connect(self, timeout: float = 8.0) -> None:
        self.sock = socket.create_connection((self.host, self.port), timeout=timeout)
        self.sock.settimeout(timeout)
        self._buf = b""
        self._log(f"  TCP 已连上 {self.host}:{self.port}")

    # -- 握手 -------------------------------------------------------------
    def init(self, guid: bytes = DEFAULT_GUID, name: str = "Z30Tone-Probe", conn: int = 1) -> dict:
        """Init Command Request → Ack。

        两种协议的区别（这正是之前被相机断开的根因）：
          PTP/IP：type + 连接号 + GUID + UTF-16 名 + 协议版本
          MTP/IP：type + GUID + ASCII 名（没有连接号，也没有协议版本）
        """
        if self.proto == "ptp":
            pkt = struct.pack("<II", PKT_INIT_CMD_REQ, conn) + guid
            pkt += (name + "\x00").encode("utf-16-le")
            pkt += struct.pack("<I", 0x00010000)
        else:
            pkt = struct.pack("<I", PKT_INIT_CMD_REQ) + guid
            pkt += (name + "\x00").encode("ascii", "replace")

        self.sock.sendall(pkt)
        self._log(f"  → InitCommandRequest [{self.proto}] (GUID {guid.hex()[:16]}…)")

        ptype = struct.unpack("<I", self._fill(4))[0]
        if ptype == PKT_INIT_FAIL:
            reason = struct.unpack("<I", self._fill(4))[0]
            raise InitFail(reason)
        if ptype != PKT_INIT_CMD_ACK:
            raise RuntimeError(
                f"期望 InitCommandAck(2)，收到 {ptype}（{PKT_NAMES.get(ptype, '未知')}）"
            )

        conn_id = struct.unpack("<I", self._fill(4))[0]
        cam_guid = self._fill(16)

        if self.proto == "ptp":
            chars: list[str] = []
            while True:
                ch = self._fill(2)
                if ch == b"\x00\x00":
                    break
                chars.append(ch.decode("utf-16-le", "replace"))
                if len(chars) > 256:
                    raise RuntimeError("friendlyName 没读到结束符，协议解析可能错位")
            version = struct.unpack("<I", self._fill(4))[0]
            cam_name = "".join(chars)
            ver_text = f"{(version >> 16) & 0xFFFF}.{version & 0xFFFF}"
        else:
            # MTP/IP 的 hostname 没有长度前缀。握手阶段相机不会并发发别的包，
            # 所以把当前可读到的字节整段当 hostname。
            self.sock.settimeout(0.3)
            try:
                raw = self.sock.recv(4096)
            except socket.timeout:
                raw = b""
            finally:
                self.sock.settimeout(8.0)
            cam_name = raw.split(b"\x00")[0].decode("ascii", "replace")
            ver_text = "n/a"

        self.conn = conn_id
        info = {
            "connection": conn_id,
            "guid": cam_guid.hex(),
            "name": cam_name,
            "version": ver_text,
        }
        self._log(
            f"  ← InitCommandAck: 会话号/连接号 {conn_id}, 相机自称「{cam_name}」, 协议 {ver_text}"
        )
        return info

    # -- 事件通道 ---------------------------------------------------------
    def open_event_channel(self) -> bool:
        """建立第二条 TCP 并做 Init_Event_Request。

        规范里事件通道可选，但尼康的配对流程靠它收 0x4008（DeviceInfoChanged，
        即「用户在相机上确认了配对」）。建不上不影响命令通道。
        """
        try:
            ev = socket.create_connection((self.host, self.port), timeout=5.0)
            ev.settimeout(5.0)
            ev.sendall(struct.pack("<II", PKT_INIT_EVENT_REQ, self.conn))
            head = b""
            while len(head) < 4:
                chunk = ev.recv(4 - len(head))
                if not chunk:
                    raise ConnectionError("事件通道被相机关掉")
                head += chunk
            ptype = struct.unpack("<I", head)[0]
            if ptype != PKT_INIT_EVENT_ACK:
                raise RuntimeError(f"期望 InitEventAck(4)，收到 {ptype}")
            ev.recv(4)  # connection number
            self.event_sock = ev
            self._log("  事件通道已建立")
            return True
        except Exception as exc:  # noqa: BLE001
            self._log(f"  ! 事件通道没建立（不影响命令通道）：{type(exc).__name__}: {exc}")
            self.event_sock = None
            return False

    def wait_event(self, want: int, timeout: float = 90.0) -> bool:
        """在事件通道上等某个事件码。相机确认配对后会发 0x4008。"""
        if not self.event_sock:
            return False
        deadline = time.time() + timeout
        self.event_sock.settimeout(2.0)
        while time.time() < deadline:
            try:
                head = self.event_sock.recv(4)
            except socket.timeout:
                continue
            except OSError:
                return False
            if len(head) < 4:
                return False
            if struct.unpack("<I", head)[0] != PKT_EVENT:
                continue
            body = b""
            while len(body) < 6:
                chunk = self.event_sock.recv(6 - len(body))
                if not chunk:
                    return False
                body += chunk
            if struct.unpack("<H", body[:2])[0] == want:
                return True
        return False

    # -- 配对（尼康 Z 系列 Wi-Fi，见 libgphoto2 issue #1314） --------------
    def get_pairing_code(self) -> str:
        """0x952B：取配对码。相机同时把同一串码显示在屏幕上，由人核对。"""
        rcode, data = self.op(OP_GET_PAIRING_CODE, (), data_in=True)
        if rcode not in (RC_OK, RC_OK_WITH_DATA):
            raise RuntimeError(f"取配对码失败 0x{rcode:04X}：{rc_text(rcode)}")
        if len(data) < 4:
            raise RuntimeError(f"配对码数据太短：{data.hex(' ')}")
        n = struct.unpack_from("<I", data, 0)[0]
        return "".join(str(b) for b in data[4 : 4 + n])

    def confirm_pairing(self) -> int:
        """0x935A：告诉相机配对码已核对。param1 固定 0x00002001。"""
        rcode, _ = self.op(OP_CONFIRM_PAIRING, (0x00002001,))
        return rcode

    # -- 事务 -------------------------------------------------------------
    def op(
        self,
        code: int,
        params: tuple = (),
        data_out: bytes | None = None,
        data_in: bool = False,
    ) -> tuple[int, bytes]:
        """发一条 PTP 操作。返回 (响应码, 数据)。

        DataPhaseInfo：1 = 无数据，2 = 主机→相机，3 = 相机→主机。
        """
        txn = self.txn
        self.txn += 1
        if self.proto == "ptp":
            # PTP/IP：1 = 无数据，2 = 主机→相机，3 = 相机→主机
            phase = 2 if data_out is not None else (3 if data_in else 1)
        else:
            # MTP/IP：0 = 无数据，1 = 相机→主机，2 = 主机→相机
            phase = 2 if data_out is not None else (1 if data_in else 0)

        pkt = struct.pack("<IIHI", PKT_OP_REQ, phase, code, txn)
        for p in params:
            pkt += struct.pack("<I", p & 0xFFFFFFFF)
        self.sock.sendall(pkt)
        self._log(
            f"  → 0x{code:04X} 事务 {txn} 参数 {[hex(p) for p in params]}"
            + (f"，数据 {len(data_out)} 字节" if data_out is not None else "")
        )

        if data_out is not None:
            if self.proto == "ptp":
                head = struct.pack("<IIQ", PKT_START_DATA, txn, len(data_out))
            else:
                head = struct.pack("<III", PKT_START_DATA, txn, len(data_out))
            self.sock.sendall(head)
            self.sock.sendall(struct.pack("<II", PKT_DATA, txn) + data_out)
            self.sock.sendall(struct.pack("<II", PKT_END_DATA, txn))

        return self._wait_response(txn)

    def _wait_response(self, txn: int) -> tuple[int, bytes]:
        total = 0
        data = bytearray()
        started = False

        while True:
            ptype = struct.unpack("<I", self._fill(4))[0]

            if ptype == PKT_START_DATA:
                if self.proto == "ptp":
                    _, total = struct.unpack("<IQ", self._fill(12))
                else:
                    _, total = struct.unpack("<II", self._fill(8))
                started = True
                self._log(f"    ← StartData: 共 {total} 字节")
            elif ptype == PKT_DATA:
                self._fill(4)  # transaction id
                remain = max(0, total - len(data))
                if remain:
                    data += self._fill(remain)
            elif ptype == PKT_END_DATA:
                self._fill(4)
                remain = max(0, total - len(data))
                if remain:
                    data += self._fill(remain)
            elif ptype == PKT_OP_RESP:
                rcode, rtxn = struct.unpack("<HI", self._fill(6))
                if rtxn != txn:
                    self._log(f"    ! 响应事务号 {rtxn} 与请求 {txn} 不一致（继续）")
                self._log(
                    f"    ← 响应 0x{rcode:04X}（{rc_text(rcode)}）"
                    + (f", 数据 {len(data)} 字节" if data else "")
                )
                return rcode, bytes(data)
            elif ptype == PKT_EVENT:
                ecode, _ = struct.unpack("<HI", self._fill(6))
                self._log(f"    ← Event 0x{ecode:04X}（忽略）")
            else:
                # Operation Response 若带参数，会被误当成下一个包的开头。
                # 尼康实测不带参数；真出现就在这里停住并报出来，别硬猜。
                raise RuntimeError(
                    f"收到无法识别的包类型 {ptype}（0x{ptype:08X}）。"
                    + ("上一包的响应可能带了参数，需要按响应码调整解析。" if started else "")
                )

    def close(self) -> None:
        try:
            self.op(OP_CLOSE_SESSION)
        except Exception:
            pass
        for s in (self.event_sock, self.sock):
            try:
                if s:
                    s.close()
            except Exception:
                pass
        self.event_sock = None

    def __enter__(self) -> "PtpIp":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


# -- DeviceInfo 解析 --------------------------------------------------------

def parse_device_info(d: bytes) -> dict:
    """PTP DeviceInfo 数据块。字符串是 [1 字节字符数][UTF-16LE，含结束符]。"""
    off = 0

    def u16() -> int:
        nonlocal off
        v = struct.unpack_from("<H", d, off)[0]
        off += 2
        return v

    def u32() -> int:
        nonlocal off
        v = struct.unpack_from("<I", d, off)[0]
        off += 4
        return v

    def text() -> str:
        nonlocal off
        n = d[off]
        off += 1
        if n == 0:
            return ""
        raw = d[off : off + (n - 1) * 2]
        off += n * 2
        return raw.decode("utf-16-le", "replace")

    def u16array() -> list[int]:
        nonlocal off
        n = u32()
        vals = list(struct.unpack_from(f"<{n}H", d, off))
        off += n * 2
        return vals

    info = {
        "standard_version": u16(),
        "vendor_ext_id": u32(),
        "vendor_ext_version": u16(),
        "vendor_ext_desc": text(),
        "functional_mode": u16(),
        "operations": u16array(),
        "events": u16array(),
        "properties": u16array(),
        "capture_formats": u16array(),
        "image_formats": u16array(),
        "manufacturer": text(),
        "model": text(),
        "device_version": text(),
        "serial": text(),
    }
    return info


def parse_payload_header(raw: bytes) -> dict:
    """按真机验证过的 614 字节结构取名称与 LUT 首尾（见 真机发现-载荷结构.md）。"""
    out: dict = {"size": len(raw)}
    if len(raw) >= 0x066 + 512:
        name = raw[0x002:0x016].split(b"\x00")[0].decode("ascii", "replace")
        lut = struct.unpack_from(">256H", raw, 0x066)
        out.update(
            {
                "version": raw[0:2].hex(" "),
                "name": name,
                "family": f"0x{raw[0x02C]:02X}",
                "lut_head": list(lut[:8]),
                "lut_tail": list(lut[-4:]),
                "lut_min": min(lut),
                "lut_max": max(lut),
                "monotonic": all(lut[i] >= lut[i - 1] for i in range(1, 256)),
            }
        )
    return out


def indent(text: str, prefix: str = "    ") -> str:
    return "\n".join(prefix + line for line in text.splitlines())


def resolve_guid(spec: str) -> bytes:
    s = (spec or "default").strip().lower()
    if s == "default":
        return DEFAULT_GUID
    if s == "zero":
        return b"\x00" * 16
    if s == "ff":
        return b"\xff" * 16
    if s == "random":
        return os.urandom(16)
    raw = bytes.fromhex(s.replace("-", "").replace(" ", ""))
    if len(raw) != 16:
        raise SystemExit(f"--guid 需要 16 字节（32 个十六进制字符），收到 {len(raw)} 字节")
    return raw


def sweep(host: str, port: int, timeout: float = 3.0) -> list:
    """快速试多种 GUID / 连接号组合，找相机接受哪一种。

    相机对不认识的 GUID 会直接 RST（WinError 10054），所以「能收到 InitCommandAck」
    就是唯一判据。全程只握手，不发任何 PTP 操作，不碰相机内容。
    """
    variants = [
        ("固定 GUID / 连接号 1", DEFAULT_GUID, 1),
        ("全零 GUID / 连接号 1", b"\x00" * 16, 1),
        ("全 FF GUID / 连接号 1", b"\xff" * 16, 1),
        ("随机 GUID / 连接号 1", os.urandom(16), 1),
        ("固定 GUID / 连接号 0", DEFAULT_GUID, 0),
        ("全零 GUID / 连接号 0", b"\x00" * 16, 0),
        ("全 FF GUID / 连接号 0", b"\xff" * 16, 0),
    ]

    print("\n-- 握手变体探测（两种协议 × 多种 GUID/连接号，只握手） --")
    accepted = []
    for proto in ("mtp", "ptp"):
        for label, guid, conn in variants:
            cam = PtpIp(host, port, timeout=timeout, verbose=False, proto=proto)
            try:
                cam.connect(timeout=timeout)
                info = cam.init(guid=guid, conn=conn)
                print(f"  ✔ [{proto}] {label}：通过，相机自称「{info['name']}」")
                accepted.append((proto, label, guid, conn))
            except Exception as exc:  # noqa: BLE001
                print(f"  ✘ [{proto}] {label}：{type(exc).__name__}: {exc}")
            finally:
                try:
                    if cam.sock:
                        cam.sock.close()
                except Exception:
                    pass
            time.sleep(0.6)

    if accepted:
        proto, label, guid, conn = accepted[0]
        print(f"\n  共 {len(accepted)} 种组合通过，推荐继续用：")
        print(f"    --proto {proto} --guid {guid.hex()} --conn {conn}")
    else:
        print("\n  全部被拒 —— 相机要求「已配对的 GUID」或拒绝外部主机。下一步：")
        print("  1. 相机切到「连接到 PC」模式（网络设定 → 直接连接到计算机），再跑一次 --sweep；")
        print("  2. 仍全部被拒，就用 Wireless Transmitter Utility 配对一次，再抓出它用的 GUID。")
    return accepted


# -- 主流程 ----------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="Z30 Wi-Fi PTP/IP 探针（默认只读）")
    ap.add_argument("--host", default="192.168.1.1", help="相机 IP，访问点模式默认 192.168.1.1")
    ap.add_argument("--port", type=int, default=15740)
    ap.add_argument("--slot", default="0x00D1", help="槽位，默认 C9 = 0x00D1")
    ap.add_argument("--read", action="store_true", help="读一次槽位（只读）")
    ap.add_argument("--write-test", action="store_true", help="写槽位并读回校验（会先备份）")
    ap.add_argument("--params", type=int, default=2, help="0x90CC 的参数个数，默认 2")
    ap.add_argument("--guid", default="default", help="default / zero / ff / random / 32 位十六进制")
    ap.add_argument(
        "--proto",
        default="auto",
        choices=["auto", "mtp", "ptp"],
        help="握手协议：mtp（尼康移动端风格）/ ptp（标准 ISO 15740）/ auto（两种都试）",
    )
    ap.add_argument("--conn", type=int, default=1, help="InitCommandRequest 的连接号，默认 1")
    ap.add_argument("--sweep", action="store_true", help="遍历多种 GUID/连接号组合，只做握手")
    ap.add_argument("--pair", action="store_true", help="跑尼康 Z 系列 Wi-Fi 的 PIN 配对流程")
    ap.add_argument("--wait", type=int, default=0, help="最多等 N 秒让相机上线（每 2 秒探一次端口）")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    slot = int(args.slot, 16) if args.slot.lower().startswith("0x") else int(args.slot)
    verbose = not args.quiet

    print("=" * 66)
    print("Z30 Wi-Fi PTP/IP 探针")
    print(f"目标 {args.host}:{args.port} · 槽位 {SLOT_NAMES.get(slot, hex(slot))}")
    print("=" * 66)

    if args.wait:
        deadline = time.time() + args.wait
        print(f"等待相机上线（最多 {args.wait} 秒）：电脑请连相机的 Wi-Fi…")
        while True:
            try:
                socket.create_connection((args.host, args.port), timeout=1.0).close()
                print(f"  端口已通，开始握手（等了 {int(args.wait - (deadline - time.time()))} 秒）")
                break
            except OSError:
                if time.time() >= deadline:
                    print("\n[超时] 一直没连上。确认电脑已连到相机 Wi-Fi，且相机没熄屏。")
                    return 2
                time.sleep(2)

    if args.sweep:
        accepted = sweep(args.host, args.port)
        return 0 if accepted else 5

    # -- 握手：两种协议都试，谁通算谁 --
    protos = [args.proto] if args.proto != "auto" else ["mtp", "ptp"]
    guid = resolve_guid(args.guid)
    cam = None
    identity = None
    failures: list[str] = []

    print("\n-- 握手 --")
    for proto in protos:
        cand = PtpIp(args.host, args.port, verbose=verbose, proto=proto)
        try:
            cand.connect()
            identity = cand.init(guid=guid, conn=args.conn)
            cam = cand
            print(f"  ✔ 握手通过：协议 = {proto.upper()}，可继续后续操作")
            break
        except InitFail as exc:
            failures.append(f"[{proto}] {exc}")
            print(f"  ✘ [{proto}] 被 Init_Fail 拒绝：{exc}")
        except Exception as exc:  # noqa: BLE001
            failures.append(f"[{proto}] {type(exc).__name__}: {exc}")
            print(f"  ✘ [{proto}] {type(exc).__name__}: {exc}")
        finally:
            if cam is None:
                try:
                    if cand.sock:
                        cand.sock.close()
                except Exception:
                    pass
        time.sleep(0.5)

    if cam is None:
        print("\n[失败] 两种协议都没握手成功。")
        print(
            "\n如果两种都是「相机关闭了 TCP 连接」，说明相机不接受这个 GUID ——"
            "\n未配对配置文件在 ZR 上放行任意 GUID，但 Z30 这一代未必。此时唯一的路是"
            "\n用尼康 Wireless Transmitter Utility 配对一次，再把它的 GUID 抓出来。\n"
            "\n其它可能：相机熄屏退出连接模式 / 另一台设备正连着相机。"
        )
        for line in failures:
            print(f"  · {line.splitlines()[0]}")
        return 2

    try:
        cam.open_event_channel()
        rcode, _ = cam.op(OP_OPEN_SESSION, (1,))
        if rcode == 0xA002:  # SessionAlreadyOpen
            print("  相机会话已开，先关再开")
            cam.op(OP_CLOSE_SESSION)
            rcode, _ = cam.op(OP_OPEN_SESSION, (1,))
        if rcode != RC_OK:
            print(f"\n[失败] OpenSession 返回 0x{rcode:04X}：{rc_text(rcode)}")
            return 3

        if args.pair:
            print("\n-- PIN 配对 --")
            try:
                code = cam.get_pairing_code()
                print(f"  相机给出的配对码：{code}")
                print("  ↑ 请核对相机屏幕上显示的是不是同一串数字")
                rcode = cam.confirm_pairing()
                print(f"  0x935A 确认配对 → 0x{rcode:04X}（{rc_text(rcode)}）")
                print("  现在请在相机屏幕上确认这台主机（按 OK / 确定）…")
                if cam.wait_event(0x4008, timeout=120):
                    print("  ✔ 相机已确认（收到事件 0x4008）")
                else:
                    print("  ! 没等到 0x4008（可能还没按，或事件丢了）")
                print("  相机会重启 Wi-Fi：等它恢复后，用同一 GUID 再跑一次探针。")
            except Exception as exc:  # noqa: BLE001
                print(f"  ✘ 配对失败：{type(exc).__name__}: {exc}")
            finally:
                # 配对流程要求「关两个 socket 但不要发 CloseSession」——相机会重置连接。
                for s in (cam.event_sock, cam.sock):
                    try:
                        if s:
                            s.close()
                    except Exception:
                        pass
                cam.sock = None
                cam.event_sock = None
            return 0

        rcode, info_raw = cam.op(OP_GET_DEVICE_INFO, data_in=True)
        if rcode not in (RC_OK, RC_OK_WITH_DATA) or not info_raw:
            print(f"\n[失败] GetDeviceInfo 返回 0x{rcode:04X}：{rc_text(rcode)}")
            return 4

        info = parse_device_info(info_raw)
        print("\n-- 设备 --")
        print(f"  厂商      {info['manufacturer']}")
        print(f"  型号      {info['model']}")
        print(f"  固件      {info['device_version']}")
        print(f"  序列号    {info['serial']}")
        print(f"  PTP 标准  {info['standard_version']} · 厂商扩展 0x{info['vendor_ext_id']:08X}")
        print(f"  操作码数  {len(info['operations'])}")

        ops = set(info["operations"])
        has_read = OP_GET_PICT_CTRL in ops
        has_write = OP_SET_PICT_CTRL in ops

        print("\n-- 私有操作码（DeviceInfo 自报） --")
        print(f"  0x90CC 读槽位   {'✔ 在表里' if has_read else '✘ 不在表里'}")
        print(f"  0x90CD 写槽位   {'✔ 在表里' if has_write else '✘ 不在表里'}")

        if verbose:
            sample = " ".join(f"{c:04X}" for c in sorted(ops) if c >= 0x9000)
            print(f"  所有 0x9xxx 段：{sample or '（无）'}")

        # -- 实测读（比自报表更硬） --------------------------------------
        read_ok = False
        payload = b""
        if args.read or args.write_test:
            print(f"\n-- 实测读 {SLOT_NAMES.get(slot, hex(slot))} --")
            params = tuple([slot, 0][: args.params])
            rcode, payload = cam.op(OP_GET_PICT_CTRL, params, data_in=True)
            if rcode in (RC_OK, RC_OK_WITH_DATA):
                read_ok = True
                meta = parse_payload_header(payload)
                print(f"  ✔ 读到 {meta['size']} 字节")
                if meta["size"] > 36:
                    print(f"    版本 {meta['version']} · 名称「{meta['name']}」· 族 {meta['family']}")
                    print(f"    LUT 首 {meta['lut_head'][:5]} … 尾 {meta['lut_tail']} · 范围 {meta['lut_min']}..{meta['lut_max']}")
                    print(f"    单调 {'是' if meta['monotonic'] else '否'}")
                else:
                    print("    这是 36 字节「仅参数」形态，没有曲线")
            else:
                print(f"  ✘ 0x{rcode:04X}：{rc_text(rcode)}")

        # -- 写测试 -------------------------------------------------------
        write_ok = None
        if args.write_test:
            print(f"\n-- 写测试 {SLOT_NAMES.get(slot, hex(slot))} --")
            out_dir = os.path.join("out", "ptpip-probe")
            os.makedirs(out_dir, exist_ok=True)
            stamp = time.strftime("%Y%m%d-%H%M%S")

            if read_ok and len(payload) == 614:
                backup_path = os.path.join(out_dir, f"C9-backup-{stamp}.bin")
                with open(backup_path, "wb") as f:
                    f.write(payload)
                print(f"  已备份原内容 → {backup_path}")
                target = payload
                print("  原内容是 614 字节，原样写回（内容不变，只验证通道）")
            else:
                from z30fit.payload import build_payload_614

                # 恒等曲线：overall 画面应无变化，只验证通道。
                target = build_payload_614([i / 255.0 for i in range(256)], "PROBE-ID")
                print("  ⚠ 原内容不是 614 字节，将写入恒等曲线「PROBE-ID」（占用该槽位，画面应无变化）")

            rcode, _ = cam.op(OP_SET_PICT_CTRL, (slot, 0), data_out=target)
            if rcode not in (RC_OK, RC_OK_WITH_DATA):
                print(f"  ✘ 写入返回 0x{rcode:04X}：{rc_text(rcode)}")
                write_ok = False
            else:
                rcode, back = cam.op(OP_GET_PICT_CTRL, (slot, 0), data_in=True)
                if rcode not in (RC_OK, RC_OK_WITH_DATA):
                    print(f"  ✘ 读回失败 0x{rcode:04X}：{rc_text(rcode)}")
                    write_ok = False
                elif back == target:
                    print(f"  ✔ 读回一致：{len(back)} 字节逐字节相同")
                    write_ok = True
                else:
                    diff = [i for i in range(min(len(back), len(target))) if back[i] != target[i]]
                    print(f"  ✘ 读回不一致：{len(back)} 字节，{len(diff)} 处不同，前几个 {[hex(i) for i in diff[:8]]}")
                    write_ok = False

        # -- 结论 ---------------------------------------------------------
        print("\n" + "=" * 66)
        print("结论")
        print("=" * 66)

        if write_ok is True:
            print("  写通道可用：Wi-Fi 能读写 C 槽，iOS 端可以做完整功能（含写机身）。")
            print("  下一步：iOS 端实现同一套 PTP/IP 传输层，命令层照搬 UsbPtp.kt。")
        elif write_ok is False:
            print("  写通道被拒：相机在该通道不接受 0x90CD。")
            print("  下一步：iOS 端走「曲线工厂 + 局域网写入代理」。")
            print("  （读通道若可用，槽位列表仍能显示，只是不能写。）")
        elif read_ok:
            print(f"  读通道可用（0x90CC 返回 {len(payload)} 字节），写通道未测。")
            print("  下一步：加 --write-test 验证写入。")
        elif has_read or has_write:
            print("  操作码表里有私有码，但实测读没通过 —— 以实测为准。")
            print("  下一步：检查相机是否处于允许控制的模式，或换模式重试。")
        else:
            print("  该通道不放行私有操作码：iOS 端只能做「曲线工厂 + .z30 导出」。")
            print("  可再试另一个 Wi-Fi 模式（连接至智能设备 / 连接到 PC 互换）确认一次。")

        return 0
    finally:
        cam.close()


if __name__ == "__main__":
    raise SystemExit(main())
