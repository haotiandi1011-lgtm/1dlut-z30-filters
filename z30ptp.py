"""极简 PTP over USB 传输层（Windows / libusb）。

只做离线工具需要的事：枚举相机、握手、发一个命令、收数据。
**默认只读。** `set_pict_ctrl` 存在但是 Phase 3 的事，probe.py 不会调用它。

握手要点（交接文档 §3.2，来自 z-cloud-ai 在 Z30 真机验证）：
    PTP Class Reset → 清两个 Bulk 端点的 HALT → OpenSession（事务号必须为 0）
    → 失败重试 3 轮重新枚举

Windows 前置条件：相机在 MTP 模式下挂的是 WPD 驱动，pyusb 拿不到句柄。
需要先用 Zadig 把 `Nikon Z30` 的驱动换成 WinUSB（详见 Phase1 文档的「接相机」一节）。
"""

from __future__ import annotations

import struct
import time

try:
    import usb.core
    import usb.util
except ImportError as exc:  # pragma: no cover - 环境缺依赖时给出可执行提示
    raise ImportError("缺少 pyusb，先执行： python -m pip install pyusb") from exc

VID = 0x04B0  # Nikon
PID = 0x0452  # Z30（MTP/PTP 模式）

# 容器类型
CT_COMMAND = 1
CT_DATA = 2
CT_RESPONSE = 3
CT_EVENT = 4

RC_OK = 0x2001
RC_OK_WITH_DATA = 0x2002
RC_ERRORS = {
    0x2003: "GeneralError（相机拒绝了这条指令）",
    0x2005: "OperationNotSupported（这条指令在本机上不实现）",
    0x2006: "ParameterNotSupported（参数不对）",
    0x2009: "DevicePropNotSupported",
    0x2019: "DeviceBusy（相机忙，可能在传图或睡眠）",
    0x201D: "InvalidParameter",
    0xA001: "AccessDenied",
    0xA002: "SessionAlreadyOpen",
}

# 操作码
OP_OPEN_SESSION = 0x1002
OP_CLOSE_SESSION = 0x1003
OP_GET_PICT_CTRL = 0x90CC   # GetPictCtrlData(slot, 0)
OP_SET_PICT_CTRL = 0x90CD   # SetPictCtrlData(slot, 0)  ← 写入
OP_DEL_PICT_CTRL = 0x90CE   # DelCstPicCtrl(slot)       ← 删除，破坏性

SLOT_FIRST = 0x00C9
SLOT_COUNT = 9


def _backend():
    """优先用 libusb-1.0（对应 Zadig 装的 WinUSB 驱动）。

    这台机器上还残留着老的 libusb-win32（libusb0）驱动，pyusb 会退到它上面去，
    结果就是「设备明明在、却一个都找不到」。所以显式指定后端，别让它自己猜。
    """
    try:
        import libusb_package
        import usb.backend.libusb1 as libusb1

        be = libusb1.get_backend(find_library=libusb_package.find_library)
        if be is not None:
            return be
    except Exception:  # noqa: BLE001
        pass
    try:
        import usb.backend.libusb1 as libusb1

        return libusb1.get_backend()
    except Exception:  # noqa: BLE001
        return None


BACKEND = _backend()


class PtpError(RuntimeError):
    """相机返回了错误响应码。"""

    def __init__(self, code: int, context: str = ""):
        self.code = code
        msg = RC_ERRORS.get(code, "未知错误码")
        super().__init__(f"相机返回 0x{code:04X}：{msg}" + (f"（{context}）" if context else ""))


class CameraNotFound(RuntimeError):
    pass


class PtpCamera:
    """一台 Z30。用 `with PtpCamera.open() as cam:` 打开。"""

    def __init__(self, dev, ep_in: int, ep_out: int, iface: int):
        self.dev = dev
        self.ep_in = ep_in
        self.ep_out = ep_out
        self.iface = iface
        self.txn = 0
        self.timeout = 5000
        self.verbose = True

    # -- 打开与握手 -------------------------------------------------------
    @classmethod
    def open(cls, retries: int = 3, verbose: bool = True) -> "PtpCamera":
        last: Exception | None = None
        for attempt in range(1, retries + 1):
            try:
                cam = cls._open_once(verbose)
                return cam
            except Exception as exc:  # noqa: BLE001 - 重新枚举后重试
                last = exc
                if verbose:
                    print(f"  第 {attempt}/{retries} 轮握手失败：{type(exc).__name__}: {exc}")
                time.sleep(0.8)
        raise RuntimeError(f"连接相机失败（已重试 {retries} 轮）：{last}")

    @classmethod
    def _open_once(cls, verbose: bool) -> "PtpCamera":
        dev = usb.core.find(idVendor=VID, idProduct=PID, backend=BACKEND)
        if dev is None:
            raise CameraNotFound(
                "没找到 Nikon Z30（VID 04B0 / PID 0452）。检查：\n"
                "  · 相机是否开机、USB 模式是否设为 MTP/PTP\n"
                "  · 线是否插好（换一根数据线试试）\n"
                "  · Windows 下是否已用 Zadig 换成 WinUSB 驱动\n"
                f"  · libusb 后端：{'已就绪' if BACKEND else '不可用（装 libusb-package）'}"
            )
        if verbose:
            print(f"  找到设备 bus {dev.bus} addr {dev.address}")

        try:
            dev.set_configuration()
        except usb.core.USBError:
            pass  # 已经配置过

        cfg = dev.get_active_configuration()
        iface = None
        for intf in cfg:
            if intf.bInterfaceClass == 6 and intf.bInterfaceSubClass == 1:
                iface = intf
                break
        if iface is None:
            raise RuntimeError("设备没有 PTP 接口（class 6 / subclass 1），确认相机在 MTP 模式")

        try:
            usb.util.claim_interface(dev, iface.bInterfaceNumber)
        except usb.core.USBError:
            pass

        ep_in = ep_out = None
        for ep in iface:
            is_in = ep.bEndpointAddress & 0x80
            if ep.bmAttributes & 0x03 != 2:  # 只看 bulk
                continue
            if is_in and ep_in is None:
                ep_in = ep.bEndpointAddress
            elif not is_in and ep_out is None:
                ep_out = ep.bEndpointAddress
        if ep_in is None or ep_out is None:
            raise RuntimeError("找不到 Bulk 端点")

        cam = cls(dev, ep_in, ep_out, iface.bInterfaceNumber)

        # 1) PTP Class Reset
        try:
            dev.ctrl_transfer(0x21, 0x66, 0, cam.iface, b"", 2000)
        except usb.core.USBError:
            pass

        # 2) 清 HALT
        for ep in (cam.ep_in, cam.ep_out):
            try:
                dev.clear_halt(ep)
            except usb.core.USBError:
                pass

        # 3) 清掉可能的残留数据
        cam._drain()

        # 4) OpenSession，事务号必须是 0
        cam._send(CT_COMMAND, OP_OPEN_SESSION, 0, (1,))
        ctype, rcode, _, _ = cam._read_container()
        if rcode == 0xA002:  # 已经有会话，先关掉重开
            cam._send(CT_COMMAND, OP_CLOSE_SESSION, 0)
            cam._read_container()
            cam._send(CT_COMMAND, OP_OPEN_SESSION, 0, (1,))
            ctype, rcode, _, _ = cam._read_container()
        if rcode != RC_OK:
            raise PtpError(rcode, "OpenSession")

        cam.txn = 0
        if verbose:
            print("  会话已建立")
        return cam

    # -- 收发 -------------------------------------------------------------
    @staticmethod
    def _container(ctype: int, code: int, txn: int, params=(), data: bytes = b"") -> bytes:
        body = b"".join(struct.pack("<I", int(p)) for p in params) + data
        return struct.pack("<IHHI", 12 + len(body), ctype, code, txn) + body

    def _send(self, ctype: int, code: int, txn: int, params=(), data: bytes = b"") -> None:
        self.dev.write(self.ep_out, self._container(ctype, code, txn, params, data), self.timeout)

    def _read_container(self) -> tuple[int, int, int, bytes]:
        try:
            buf = bytes(self.dev.read(self.ep_in, 65536, self.timeout))
        except usb.core.USBTimeoutError as exc:
            raise RuntimeError("读超时：相机没回话（可能在睡眠或不在 MTP 模式）") from exc
        if len(buf) < 12:
            raise RuntimeError(f"收到的数据太短：{buf.hex()}")
        length, ctype, code, txn = struct.unpack_from("<IHHI", buf, 0)
        while len(buf) < length:
            more = bytes(self.dev.read(self.ep_in, min(65536, length - len(buf)), self.timeout))
            if not more:
                break
            buf += more
        return ctype, code, txn, buf[12:length]

    def _drain(self, rounds: int = 4) -> None:
        for _ in range(rounds):
            try:
                self.dev.read(self.ep_in, 65536, 200)
            except usb.core.USBError:
                return

    def _txn(self, code: int, params=(), data_out: bytes = b"") -> bytes:
        self.txn += 1
        self._send(CT_COMMAND, code, self.txn, params)
        if data_out:
            self._send(CT_DATA, code, self.txn, (), data_out)

        chunks: list[bytes] = []
        while True:
            ctype, rcode, _, body = self._read_container()
            if ctype == CT_DATA:
                chunks.append(body)
            elif ctype == CT_EVENT:
                continue
            elif ctype == CT_RESPONSE:
                if rcode not in (RC_OK, RC_OK_WITH_DATA):
                    raise PtpError(rcode, f"opcode 0x{code:04X}")
                return b"".join(chunks)
            else:
                raise RuntimeError(f"意外的容器类型 {ctype}")

    # -- 相机操作 ---------------------------------------------------------
    def get_pict_ctrl(self, slot: int) -> bytes:
        """0x90CC：读回某个槽位的完整载荷（只读，安全）。"""
        return self._txn(OP_GET_PICT_CTRL, (slot, 0))

    # -- 文件下载（只读） ---------------------------------------------------
    def storage_ids(self) -> list[int]:
        d = self._txn(0x1004)  # GetStorageIDs
        n = struct.unpack_from("<I", d, 0)[0]
        return list(struct.unpack_from(f"<{n}I", d, 4))

    def object_handles(self, storage_id: int) -> list[int]:
        d = self._txn(0x1007, (storage_id, 0xFFFFFFFF, 0))  # GetObjectHandles
        n = struct.unpack_from("<I", d, 0)[0]
        return list(struct.unpack_from(f"<{n}I", d, 4))

    def object_info(self, handle: int) -> dict:
        """0x1008 GetObjectInfo。PTP 字符串是 [1 字节长度][UTF-16LE]。"""
        d = self._txn(0x1008, (handle,))
        fmt = struct.unpack_from("<H", d, 4)[0]
        size = struct.unpack_from("<I", d, 8)[0]
        width, height = struct.unpack_from("<II", d, 26)

        def _str(off: int) -> tuple[str, int]:
            n = d[off]
            if n == 0:
                return "", off + 1
            raw = d[off + 1 : off + 1 + (n - 1) * 2]
            return raw.decode("utf-16-le", "replace"), off + 1 + n * 2

        name, p = _str(52)
        date, p = _str(p)
        return {
            "handle": handle,
            "format": fmt,
            "size": size,
            "width": width,
            "height": height,
            "filename": name,
            "date": date,
        }

    def get_object(self, handle: int, timeout: int = 60000) -> bytes:
        """0x1009 GetObject：把文件内容读出来（只读）。"""
        old = self.timeout
        self.timeout = timeout
        try:
            return self._txn(0x1009, (handle,))
        finally:
            self.timeout = old

    def set_pict_ctrl(self, slot: int, payload: bytes, second_param: int = 0) -> int:
        """0x90CD：把 614 字节载荷写进槽位。

        ⚠️ 这是**会改变相机内容**的操作。调用方必须自己先备份、自己确认目标槽位。
        """
        self._txn(OP_SET_PICT_CTRL, (slot, second_param), data_out=payload)
        return RC_OK

    def delete_pict_ctrl(self, slot: int) -> None:
        """0x90CE：删除自定义槽位。

        ⚠️ 破坏性。z-cloud-ai 的教训：把它当"无害前置动作"混进自动流程，
        真机上把用户的一个预设删没了。只在明确需要清理时调用。
        """
        self._txn(OP_DEL_PICT_CTRL, (slot,))

    def close(self) -> None:
        try:
            self._send(CT_COMMAND, OP_CLOSE_SESSION, self.txn + 1)
            self._read_container()
        except Exception:  # noqa: BLE001
            pass
        try:
            usb.util.release_interface(self.dev, self.iface)
        except Exception:  # noqa: BLE001
            pass
        try:
            usb.util.dispose_resources(self.dev)
        except Exception:  # noqa: BLE001
            pass

    def __enter__(self) -> "PtpCamera":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def slot_name(slot: int) -> str:
    """0x00C9 → C1 … 0x00D1 → C9"""
    idx = slot - SLOT_FIRST + 1
    return f"C{idx}" if 1 <= idx <= SLOT_COUNT else f"0x{slot:04X}"


def slot_from_name(name: str) -> int:
    s = name.strip().upper()
    if s.startswith("C") and s[1:].isdigit():
        idx = int(s[1:])
        if 1 <= idx <= SLOT_COUNT:
            return SLOT_FIRST + idx - 1
    if s.startswith("0X"):
        return int(s, 16)
    raise ValueError(f"看不懂的槽位名：{name}（用 C1..C9 或 0x00C9）")


def list_devices() -> list[str]:
    """枚举所有 Nikon 设备，不握手、不发指令。"""
    found = []
    for dev in usb.core.find(find_all=True, idVendor=VID, backend=BACKEND):
        try:
            product = usb.util.get_string(dev, dev.iProduct) or ""
        except Exception:  # noqa: BLE001
            product = ""
        found.append(f"bus {dev.bus} addr {dev.address}  PID 0x{dev.idProduct:04X}  {product}")
    return found
