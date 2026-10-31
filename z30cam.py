"""相机侧操作：列槽位、备份、写入、校验、回滚。

所有写入都走同一套保险：
  读备份 → 落盘 → （非空槽需显式允许）→ 写入 → 读回逐字节比对
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import z30ptp
from z30fit import payload as pl
from z30fit.paths import data_root

BACKUP_DIR = data_root() / "backups"
INDEX = BACKUP_DIR / "index.json"

SLOTS = {f"C{i}": 0x00C8 + i for i in range(1, 10)}

# 相机是独占设备：网页一打开会并发发好几个请求，两个线程同时开同一个 USB 句柄
# 会直接拿到 Access denied。所有相机访问必须串行。
_CAM_LOCK = threading.RLock()


class CameraUnavailable(RuntimeError):
    pass


def slot_id(name: str) -> int:
    key = str(name).strip().upper()
    if key in SLOTS:
        return SLOTS[key]
    raise ValueError(f"未知槽位 {name}，可用：{', '.join(SLOTS)}")


def _open():
    try:
        return z30ptp.PtpCamera.open(retries=2, verbose=False)
    except Exception as exc:  # noqa: BLE001
        raise CameraUnavailable(str(exc)) from exc


def describe_slot(data: bytes) -> dict:
    """把一段槽位数据翻译成可读状态。"""
    if not any(data):
        return {"state": "empty", "label": "空槽", "name": "", "size": len(data)}
    if len(data) == pl.PAYLOAD_SIZE:
        info = pl.parse_payload(data)
        return {
            "state": "full",
            "label": "完整配方（含曲线）",
            "name": info["name"],
            "size": len(data),
            "lut_monotone": info["lut_monotone"],
            "black": round(info["lut_first"] / 32767, 4),
            "white": round(info["lut_last"] / 32767, 4),
        }
    if len(data) == 36:
        return {
            "state": "params",
            "label": "仅参数（机身菜单建的，无曲线）",
            "name": data[2:0x16].rstrip(b"\x00").decode("ascii", "replace"),
            "size": len(data),
        }
    return {"state": "unknown", "label": f"未知形态（{len(data)}B）", "name": "", "size": len(data)}


def list_slots(probe_camera: bool = True) -> list[dict]:
    """列 9 个自定义槽位。没插相机时返回状态为 offline 的占位表。"""
    out = []
    if not probe_camera:
        for name in SLOTS:
            out.append({"slot": name, **describe_slot(b"\x00" * 36)})
        return out

    try:
        with _CAM_LOCK, _open() as cam:
            for name, sid in SLOTS.items():
                try:
                    data = cam.get_pict_ctrl(sid)
                    out.append({"slot": name, **describe_slot(data)})
                except z30ptp.PtpError as exc:
                    out.append({"slot": name, "state": "error", "label": str(exc), "name": "", "size": 0})
    except CameraUnavailable as exc:
        return [{"slot": n, "state": "offline", "label": "相机未连接", "name": "", "size": 0, "detail": str(exc)} for n in SLOTS]
    return out


def save_backup(slot: str, data: bytes) -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    path = BACKUP_DIR / f"{stamp}_{slot}_{len(data)}B.bin"
    path.write_bytes(data)

    idx: list[dict] = []
    if INDEX.exists():
        try:
            idx = json.loads(INDEX.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            idx = []
    idx.append({"file": path.name, "slot": slot, "size": len(data), "time": stamp,
                "empty": not any(data)})
    INDEX.write_text(json.dumps(idx[-200:], ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def latest_backup(slot: str) -> Path | None:
    if not INDEX.exists():
        return None
    try:
        idx = json.loads(INDEX.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    for item in reversed(idx):
        if item["slot"] == slot:
            p = BACKUP_DIR / item["file"]
            if p.exists():
                return p
    return None


def write_slot(
    payload: bytes,
    slot: str = "C9",
    allow_overwrite: bool = False,
    name_hint: str = "",
) -> dict:
    """写入并校验。返回结构化结果，不抛异常（除相机不可用）。"""
    if len(payload) != pl.PAYLOAD_SIZE:
        return {"ok": False, "stage": "payload", "message": f"载荷必须 614 字节，当前 {len(payload)}"}

    sid = slot_id(slot)
    with _CAM_LOCK, _open() as cam:
        try:
            original = cam.get_pict_ctrl(sid)
        except z30ptp.PtpError as exc:
            return {"ok": False, "stage": "backup", "message": f"读取原槽位失败：{exc}"}

        backup = save_backup(slot, original)
        was_empty = not any(original)
        if not was_empty and not allow_overwrite:
            return {
                "ok": False,
                "stage": "guard",
                "message": f"{slot} 里已有内容，需要确认覆盖",
                "occupied": True,
                "backup": backup.name,
                "current": describe_slot(original),
            }

        try:
            cam.set_pict_ctrl(sid, payload)
        except z30ptp.PtpError as exc:
            return {"ok": False, "stage": "write", "message": f"写入被拒：{exc}", "backup": backup.name}
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": False,
                "stage": "write",
                "message": f"写入异常：{type(exc).__name__}：{exc}",
                "backup": backup.name,
                "hint": "若相机无响应，关机拔电池 10 秒后开机，再从备份回滚",
            }

        time.sleep(0.3)
        try:
            back = cam.get_pict_ctrl(sid)
        except z30ptp.PtpError as exc:
            return {"ok": False, "stage": "verify", "message": f"读回失败：{exc}", "backup": backup.name}

    if back == payload:
        return {
            "ok": True,
            "stage": "done",
            "slot": slot,
            "message": f"已写入 {slot} 并逐字节校验通过",
            "backup": backup.name,
            "was_empty": was_empty,
        }

    diffs = pl.diff_payloads(payload, back)
    return {
        "ok": False,
        "stage": "verify",
        "message": f"读回不一致（{len(diffs)} 处差异，读回 {len(back)} 字节）",
        "backup": backup.name,
        "diffs": [{"offset": f"0x{o:03X}", "wrote": a, "read": b} for o, a, b in diffs[:10]],
    }


def rollback(slot: str = "C9", backup: str | None = None) -> dict:
    """把槽位恢复到某次写入前的状态。"""
    sid = slot_id(slot)
    path = (BACKUP_DIR / backup) if backup else latest_backup(slot)
    if path is None or not path.exists():
        return {"ok": False, "message": f"找不到 {slot} 的备份"}

    data = path.read_bytes()
    with _CAM_LOCK, _open() as cam:
        try:
            if len(data) == pl.PAYLOAD_SIZE:
                cam.set_pict_ctrl(sid, data)
                time.sleep(0.3)
                back = cam.get_pict_ctrl(sid)
                ok = back == data
            else:
                # 备份是 36 字节的「仅参数」形态或全零空槽：无法用 0x90CD 写回
                cam.delete_pict_ctrl(sid)
                back = cam.get_pict_ctrl(sid)
                ok = not any(back)
        except z30ptp.PtpError as exc:
            return {"ok": False, "message": f"回滚失败：{exc}"}

    return {
        "ok": ok,
        "message": f"{slot} 已从 {path.name} 恢复" if ok else f"回滚后读回不一致（{len(back)} 字节）",
        "file": path.name,
    }


def delete_slot(slot: str = "C9") -> dict:
    """删除自定义槽位（破坏性，仅在明确清理时调用）。"""
    sid = slot_id(slot)
    with _CAM_LOCK, _open() as cam:
        try:
            cam.delete_pict_ctrl(sid)
        except z30ptp.PtpError as exc:
            return {"ok": False, "message": f"删除失败：{exc}"}
        try:
            back = cam.get_pict_ctrl(sid)
        except z30ptp.PtpError:
            back = b""
    return {"ok": not any(back), "message": f"{slot} 已删除" if not any(back) else f"{slot} 仍有 {len(back)} 字节"}
