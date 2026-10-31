#!/usr/bin/env python
"""本地网页服务：拟合 + 写入 Z30。

只监听 127.0.0.1，不对外网开放。启动方式见根目录的 `启动.cmd`。

    python webui/server.py [--port 8765] [--no-browser]
"""

from __future__ import annotations

import argparse
import json
import re
import numpy as np
import sys
import threading
import time
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
HERE = Path(__file__).resolve().parent

from z30fit import payload as pl  # noqa: E402
from z30fit.paths import app_root, data_root, resource  # noqa: E402
from z30fit.fit1d import MODE_LABELS  # noqa: E402
from z30fit.metrics import verdict  # noqa: E402
from z30fit.curve import banding_analysis  # noqa: E402
from z30fit.pipeline import fit_from_path  # noqa: E402
from z30fit.report import render_previews  # noqa: E402

WORK = data_root() / "webui"
UPLOADS = WORK / "uploads"
STATIC = resource("webui")
SAMPLES = resource("samples")

SESSIONS: dict[str, dict] = {}
LOCK = threading.Lock()
MAX_UPLOAD = 40 * 1024 * 1024


def _json(handler: BaseHTTPRequestHandler, obj: dict, code: int = 200) -> None:
    body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


def _file(handler: BaseHTTPRequestHandler, path: Path) -> None:
    if not path.is_file():
        handler.send_error(404, "not found")
        return
    ctype = {
        ".html": "text/html; charset=utf-8",
        ".png": "image/png",
        ".md": "text/markdown; charset=utf-8",
        ".css": "text/css; charset=utf-8",
        ".js": "application/javascript; charset=utf-8",
        ".cube": "text/plain; charset=utf-8",
    }.get(path.suffix.lower(), "application/octet-stream")
    data = path.read_bytes()
    handler.send_response(200)
    handler.send_header("Content-Type", ctype)
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def _advice(metrics: dict) -> dict:
    mean, p95 = metrics["mean"], metrics["p95"]
    if mean < 2.0 and p95 < 6.0:
        return {"level": "ok", "text": "建议写入：肉眼基本分辨不出差异"}
    if mean < 4.0:
        return {"level": "caution", "text": "谨慎：影调对了，但色彩个性会丢一部分，看对比图再定"}
    return {"level": "no", "text": "不建议写入：这个风格主要靠分色相调整，1D 曲线表达不了"}


IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}


def safe_name(name: str, fallback: str = "Z30FIT") -> str:
    """机身名称只支持 ASCII。中文/符号会被固件显示成乱码或问号，这里直接剔掉。"""
    cleaned = re.sub(r"[^A-Za-z0-9 _\-]", "", name or "").strip()[:19]
    return cleaned or fallback
RECIPE_EXT = {".z30"}


def _fit_recipe(path: Path) -> dict:
    """读配方包：载荷是唯一真值，元数据只用来展示。"""
    from z30fit import recipe as rc
    from z30fit.curve import MonotoneSpline
    from z30fit.fit1d import Candidate, FitResult

    payload, meta = rc.load(path)
    table = rc.table_from_payload(payload)
    values = table.astype(np.float64) / 32767.0
    info = pl.parse_payload(payload)

    token = f"r{int(time.time() * 1000) % 10_000_000}"
    outdir = WORK / token
    outdir.mkdir(parents=True, exist_ok=True)

    curve = MonotoneSpline(np.linspace(0, 1, 256), values)
    m = {"mean": 0.0, "median": 0.0, "p95": 0.0, "max": 0.0,
         "within_1": 0.0, "within_2": 0.0, "within_3": 0.0,
         "rmse": 0.0, "rmse_r": 0.0, "rmse_g": 0.0, "rmse_b": 0.0,
         "gray_dev": 0.0, "de_neutral_max": 0.0, "points": 256}
    if meta.get("metrics"):
        for k in ("mean", "p95", "max"):
            if k in meta["metrics"]:
                m[k] = float(meta["metrics"][k])

    fit = FitResult(source=path.name, lut_kind="配方包", mode="recipe", curve=curve,
                    table=table, segment=b"", metrics=m, banding=banding_analysis(table, values),
                    tone=rc.tone_from_table(table), candidates=[], extra={})
    previews = render_previews(fit, None, outdir)

    rec = {
        "token": token, "kind": "recipe",
        "name": meta.get("name") or info["name"] or path.stem,
        "source": path.name,
        "lut_kind": "配方包",
        "mode": "recipe", "mode_label": "配方包",
        "recipe_meta": meta,
        "metrics": m,
        "tone": rc.tone_from_table(table),
        "banding": {k: fit.banding[k] for k in
                    ("monotone", "max_flat_run", "max_8bit_dupes", "distinct_codes", "artifact_flats")},
        "candidates": [],
        "verdict": meta.get("note") or f"导入自 {path.name}",
        "advice": {"level": "ok", "text": "配方包校验通过，可以直接写入机身"},
        "curve": [round(float(v), 5) for v in values[::4]],
        "extra": {},
    }
    rec["previews"] = {k: f"/files/{token}/{v.name}" for k, v in previews.items()}
    with LOCK:
        SESSIONS[token] = {"record": rec, "table": table, "meta": meta}
    return {"ok": True, **rec}

# 内置示例只是公式合成的测试样本，不是真实胶片。对外一律用中文名，
# 不许出现厂商商标（Portra / Kodachrome / Provia 这类）。
SAMPLE_LABELS = [
    ("fade_1d.cube", "合成 01 · 褪色低反差"),
    ("punch_s_1d.cube", "合成 02 · 强对比 S 曲线"),
    ("warm_soft_3d.cube", "合成 03 · 低反差暖调"),
    ("vivid_red_3d.cube", "合成 04 · 高饱和红黄"),
    ("fade_matte_3d.cube", "合成 05 · 哑光去饱和"),
    ("teal_orange_3d.cube", "合成 06 · 分离色调（1D 做不出）"),
]


def _fit_image(path: Path, knots: int) -> dict:
    """单张参考图 → 影调曲线。这里给不出 ΔE，只有「影调贴合误差（灰阶）」。"""
    from z30fit import refimage
    from z30fit.fit1d import Candidate, FitResult

    res = refimage.image_fit(path, knots=knots)
    token = f"i{int(time.time() * 1000) % 10_000_000}"
    outdir = WORK / token
    outdir.mkdir(parents=True, exist_ok=True)

    fit = FitResult(
        source=str(path),
        lut_kind="参考图",
        mode="reference",
        curve=res["curve"],
        table=res["table"],
        segment=b"",
        metrics=res["metrics"],
        banding=res["banding"],
        tone=res["tone"],
        candidates=[Candidate("reference", res["curve"], res["metrics"], int(res["metrics"]["points"]))],
        extra={},
    )

    previews = render_previews(fit, None, outdir)
    extra = refimage.render_compare(path, res["values"], outdir)

    st = res["stats"]
    rec = {
        "token": token,
        "kind": "image",
        "name": path.stem,
        "source": path.name,
        "lut_kind": "参考图",
        "mode": "reference",
        "mode_label": "参考图影调迁移",
        "image_metrics": {
            "tone_match_mean": round(st["tone_match_mean"], 2),
            "tone_match_max": round(st["tone_match_max"], 2),
            "clip_low": round(st["clip_low"] * 100, 1),
            "clip_high": round(st["clip_high"] * 100, 1),
            "anchors": {k: round(v * 255, 0) for k, v in st["anchors"].items()},
            "source_p": {k: round(v * 255, 0) for k, v in st["source_p"].items()},
        },
        "tone": res["tone"],
        "banding": {k: res["banding"][k] for k in
                    ("monotone", "max_flat_run", "max_8bit_dupes", "distinct_codes", "artifact_flats")},
        "candidates": [],
        "verdict": "单图模式：只能迁移影调，色彩个性无法还原（没有真值可比）",
        "advice": res["advice"],
        "curve": [round(float(v), 5) for v in res["values"][::4]],
        "extra": {},
    }
    rec["previews"] = {
        **{k: f"/files/{token}/{v.name}" for k, v in previews.items()},
        "ref_compare": f"/files/{token}/{extra['ref_compare']}",
        "hist": f"/files/{token}/{extra['hist']}",
    }

    with LOCK:
        SESSIONS[token] = {"record": rec, "table": res["table"]}
    return {"ok": True, **rec}


def _record_from_table(token_prefix: str, kind: str, name: str, table, desc: str = "",
                        extra_meta: dict | None = None) -> dict:
    """把一张 256 点表做成前端记录（预设/配方通用）。"""
    from z30fit.curve import MonotoneSpline
    from z30fit.fit1d import FitResult
    from z30fit import recipe as rc

    table = np.asarray(table, dtype=np.uint16)
    values = table.astype(np.float64) / 32767.0
    token = f"{token_prefix}{int(time.time() * 1000) % 10_000_000}"
    outdir = WORK / token
    outdir.mkdir(parents=True, exist_ok=True)
    curve = MonotoneSpline(np.linspace(0, 1, 256), values)

    m = {"mean": 0.0, "median": 0.0, "p95": 0.0, "max": 0.0, "within_1": 0.0,
         "within_2": 0.0, "within_3": 0.0, "rmse": 0.0, "rmse_r": 0.0, "rmse_g": 0.0,
         "rmse_b": 0.0, "gray_dev": 0.0, "de_neutral_max": 0.0, "points": 256}
    meta = extra_meta or {}
    if meta.get("metrics"):
        for k in ("mean", "p95", "max"):
            if k in meta["metrics"]:
                m[k] = float(meta["metrics"][k])

    fit = FitResult(source=name, lut_kind=kind, mode=kind, curve=curve, table=table,
                    segment=b"", metrics=m, banding=banding_analysis(table, values),
                    tone=rc.tone_from_table(table), candidates=[], extra={})
    previews = render_previews(fit, None, outdir)

    rec = {
        "token": token, "kind": kind,
        "name": name, "source": desc or name, "lut_kind": kind,
        "mode": kind, "mode_label": "预设",
        "recipe_meta": meta,
        "metrics": m,
        "tone": rc.tone_from_table(table),
        "banding": {k: fit.banding[k] for k in
                    ("monotone", "max_flat_run", "max_8bit_dupes", "distinct_codes", "artifact_flats")},
        "candidates": [],
        "verdict": desc,
        "advice": {"level": "ok", "text": desc or "预设已载入，可以直接写入"},
        "curve": [round(float(v), 5) for v in values[::4]],
        "extra": {},
    }
    rec["previews"] = {k: f"/files/{token}/{v.name}" for k, v in previews.items()}
    with LOCK:
        SESSIONS[token] = {"record": rec, "table": table, "meta": meta}
    return {"ok": True, **rec}


def _fit_pair(src_path: Path, dst_path: Path, knots: int) -> dict:
    """成对图 → 影调曲线。有真值，所以给的是真正的 ΔE2000。"""
    from z30fit import pairfit
    from z30fit.fit1d import Candidate, FitResult

    res = pairfit.pair_fit(src_path, dst_path, knots=knots)
    token = f"p{int(time.time() * 1000) % 10_000_000}"
    outdir = WORK / token
    outdir.mkdir(parents=True, exist_ok=True)

    fit = FitResult(
        source=f"{src_path.name} → {dst_path.name}",
        lut_kind="成对图",
        mode="pair",
        curve=res["curve"],
        table=res["table"],
        segment=b"",
        metrics=res["metrics"],
        banding=res["banding"],
        tone=res["tone"],
        candidates=[Candidate("pair", res["curve"], res["metrics"], int(res["metrics"]["points"]))],
        extra={},
    )
    previews = render_previews(fit, None, outdir)
    pair_png = pairfit.render_pair_preview(res, outdir)

    m = res["metrics"]
    rec = {
        "token": token,
        "kind": "pair",
        "name": src_path.stem,
        "source": f"{src_path.name} → {dst_path.name}",
        "lut_kind": "成对图",
        "mode": "pair",
        "mode_label": "成对图拟合",
        "metrics": {k: m[k] for k in
                    ("mean", "median", "p95", "max", "within_1", "within_2", "within_3",
                     "rmse", "rmse_r", "rmse_g", "rmse_b", "before_mean", "shift")},
        "tone": res["tone"],
        "banding": {k: res["banding"][k] for k in
                    ("monotone", "max_flat_run", "max_8bit_dupes", "distinct_codes", "artifact_flats")},
        "candidates": [],
        "verdict": verdict(m),
        "advice": res["advice"],
        "curve": [round(float(v), 5) for v in res["values"][::4]],
        "extra": {},
    }
    rec["previews"] = {**{k: f"/files/{token}/{v.name}" for k, v in previews.items()},
                       "pair": f"/files/{token}/{pair_png}"}

    with LOCK:
        SESSIONS[token] = {"record": rec, "table": res["table"]}
    return {"ok": True, **rec}


def do_fit(src: str, mode: str, knots: int, target: str = "") -> dict:
    if src.startswith("sample:"):
        path = SAMPLES / src.split(":", 1)[1]
    elif src.startswith("upload:"):
        name = src.split(":", 1)[1]
        path = UPLOADS / name
    else:
        raise ValueError("无效的来源")

    if not path.exists():
        raise ValueError(f"找不到文件：{path.name}")

    if path.suffix.lower() in RECIPE_EXT:
        return _fit_recipe(path)

    if path.suffix.lower() in IMAGE_EXT:
        if target.startswith("upload:"):
            tpath = UPLOADS / target.split(":", 1)[1]
            if tpath.exists() and tpath.suffix.lower() in IMAGE_EXT:
                return _fit_pair(path, tpath, knots)
            raise ValueError("目标图不存在或不是图片")
        return _fit_image(path, knots)

    result, ref_lut = fit_from_path(path, mode=mode, knots=knots)

    token = f"t{int(time.time() * 1000) % 10_000_000}"
    outdir = WORK / token
    outdir.mkdir(parents=True, exist_ok=True)

    previews = render_previews(result, ref_lut, outdir)
    payload = pl.build_payload_614(result.table_0_1, name="TEMP", family=0x05,
                                   template=None)

    rec = {
        "token": token,
        "name": path.stem,
        "source": path.name,
        "lut_kind": result.lut_kind,
        "mode": result.mode,
        "mode_label": result.label,
        "metrics": {k: result.metrics[k] for k in
                    ("mean", "median", "p95", "max", "within_1", "within_2", "within_3",
                     "rmse", "rmse_r", "rmse_g", "rmse_b", "gray_dev", "de_neutral_max")},
        "tone": result.tone,
        "banding": {k: result.banding[k] for k in
                    ("monotone", "max_flat_run", "max_8bit_dupes", "distinct_codes", "artifact_flats")},
        "candidates": [
            {"mode": c.mode, "label": c.label, "mean": c.metrics["mean"],
             "p95": c.metrics["p95"], "gray_dev": c.metrics["gray_dev"], "samples": c.sample_count}
            for c in result.candidates
        ],
        "verdict": verdict(result.metrics),
        "advice": _advice(result.metrics),
        "extra": result.extra,
        "curve": [round(float(v), 5) for v in result.table_0_1[::4]],  # 64 点，够画图
        "payload_hex_head": payload[:16].hex(" "),
    }
    rec["previews"] = {k: f"/files/{token}/{v.name}" for k, v in previews.items()}

    with LOCK:
        SESSIONS[token] = {"record": rec, "table": result.table}

    # 把报告也写一份，方便存档
    try:
        from z30fit.report import build_markdown

        md = build_markdown(result, ref_lut, previews, outdir)
        (outdir / "report.md").write_text(md, encoding="utf-8")
        rec["report_url"] = f"/files/{token}/report.md"
    except Exception:  # noqa: BLE001
        pass

    return {"ok": True, **rec}


class Handler(BaseHTTPRequestHandler):
    server_version = "Z30Lab/0.1"

    def log_message(self, fmt: str, *args) -> None:  # 安静点，只记错误
        if args and str(args[0]).startswith(("4", "5")):
            sys.stderr.write("  %s\n" % (fmt % args))

    def handle_one_request(self) -> None:
        # 浏览器提前断开是常态（页面刷新/无头截图），不该刷一屏堆栈
        try:
            super().handle_one_request()
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            self.close_connection = True

    # -- GET --------------------------------------------------------------
    def do_GET(self) -> None:
        u = urlparse(self.path)
        path = unquote(u.path)
        q = parse_qs(u.query)

        try:
            if path in ("/", "/index.html"):
                return _file(self, STATIC / "index.html")
            if path == "/favicon.ico":
                self.send_response(204)
                self.end_headers()
                return
            if path == "/api/samples":
                items = []
                for fname, label in SAMPLE_LABELS:
                    p = SAMPLES / fname
                    if p.exists():
                        items.append({"name": fname, "label": label, "size": p.stat().st_size})
                return _json(self, {
                    "ok": True,
                    "samples": items,
                    "note": "内置示例是公式合成的测试样本，只用来熟悉功能，不是真实胶片。",
                    "modes": [{"key": k, "label": v} for k, v in MODE_LABELS.items()],
                })
            if path == "/api/slots":
                try:
                    import z30cam

                    return _json(self, {"ok": True, "slots": z30cam.list_slots()})
                except ImportError as exc:
                    return _json(self, {
                        "ok": False, "reason": "missing_deps", "slots": [],
                        "message": f"缺少依赖：{exc}",
                        "hint": "命令行执行：python -m pip install pyusb libusb-package",
                    })
            if path == "/api/backups":
                import z30cam

                slot = q.get("slot", ["C9"])[0]
                p = z30cam.latest_backup(slot)
                return _json(self, {"ok": True, "slot": slot,
                                    "latest": p.name if p else None,
                                    "size": p.stat().st_size if p else 0})
            if path.startswith("/files/"):
                parts = path[len("/files/"):].split("/", 1)
                if len(parts) != 2:
                    self.send_error(404)
                    return
                token, name = parts
                return _file(self, (WORK / token / name).resolve())
            if path == "/api/recipe":
                import z30fit.recipe as rc

                token = q.get("token", [""])[0]
                name = (q.get("name", [""])[0] or "").strip()
                with LOCK:
                    sess = SESSIONS.get(token)
                if not sess:
                    return _json(self, {"ok": False, "message": "结果已过期，请重新拟合"})
                rec = sess["record"]
                pname = safe_name(name or rec.get("name") or "Z30FIT")
                payload = pl.build_payload_614(sess["table"].astype(np.float64) / 32767.0,
                                               name=pname, family=0x05)
                meta = rc.make_meta(
                    name=pname,
                    source=rec.get("source", ""),
                    metrics={k: rec["metrics"].get(k) for k in ("mean", "p95", "max")},
                    tone=rec.get("tone", {}),
                    note=rec.get("verdict", ""),
                )
                data = rc.build(payload, meta)
                fname = f"{pname}.z30"
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Disposition", f'attachment; filename="{fname}"')
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            if path == "/api/presets":
                from z30fit import presets, ncp_presets

                listing = presets.listing() + ncp_presets.listing()
                return _json(self, {"ok": True, "presets": listing,
                                    "note": ncp_presets.NOTE})
            if path == "/api/save_preset":
                from z30fit import presets

                token = q.get("token", [""])[0]
                name = safe_name(q.get("name", [""])[0] or "自定义")
                desc = q.get("desc", [""])[0][:60]
                with LOCK:
                    sess = SESSIONS.get(token)
                if not sess:
                    return _json(self, {"ok": False, "message": "结果已过期"})
                entry = presets.save_user(name, desc, sess["table"], sess["record"].get("source", ""))
                return _json(self, {"ok": True, "preset": entry, "message": f"已保存「{entry['name']}」"})
            if path == "/api/delete_preset":
                from z30fit import presets

                pid = q.get("id", [""])[0]
                return _json(self, {"ok": presets.delete_user(pid)})
            if path == "/api/session":
                token = q.get("token", [""])[0]
                with LOCK:
                    rec = SESSIONS.get(token, {}).get("record")
                return _json(self, {"ok": bool(rec), "record": rec})
            self.send_error(404)
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            _json(self, {"ok": False, "message": f"{type(exc).__name__}: {exc}"}, 500)

    # -- POST -------------------------------------------------------------
    def do_POST(self) -> None:
        u = urlparse(self.path)
        path = unquote(u.path)
        q = parse_qs(u.query)

        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_UPLOAD:
                return _json(self, {"ok": False, "message": "文件太大"}, 413)
            body = self.rfile.read(length) if length else b""

            if path == "/api/upload":
                name = Path(q.get("name", ["upload.cube"])[0]).name
                if not name.lower().endswith((".cube", ".np3", ".z30", ".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff")):
                    return _json(self, {"ok": False, "message": "只支持 .cube / .np3 / 图片 / .z30 配方包"})
                UPLOADS.mkdir(parents=True, exist_ok=True)
                target = UPLOADS / name
                target.write_bytes(body)
                return _json(self, {"ok": True, "name": name, "size": len(body)})

            if path == "/api/preset":
                from z30fit import presets, ncp_presets

                pid = q.get("id", [""])[0]
                p = presets.get(pid)
                cam = None
                if not p:
                    p = ncp_presets.get(pid)
                    if p:
                        cam = p.get("cam")
                if not p:
                    return _json(self, {"ok": False, "message": "找不到这个预设"})
                rec = _record_from_table("k", "preset", p["name"], p["table"], p.get("desc", ""))
                if cam:
                    rec["cam"] = cam
                # 保留原配方参数与基础 profile（当前写不进机身，界面如实展示）
                rec["base"] = p.get("base", "")
                rec["params"] = p.get("params", {})
                return _json(self, rec)
            if path == "/api/fit":
                src = q.get("src", [""])[0]
                mode = q.get("mode", ["auto"])[0]
                knots = int(q.get("knots", ["33"])[0])
                target = q.get("target", [""])[0]
                t0 = time.time()
                res = do_fit(src, mode, knots, target)
                res["elapsed"] = round(time.time() - t0, 2)
                return _json(self, res)

            if path == "/api/write":
                import z30cam

                token = q.get("token", [""])[0]
                slot = q.get("slot", ["C9"])[0]
                name = safe_name(q.get("name", [""])[0])
                overwrite = q.get("overwrite", ["0"])[0] == "1"

                with LOCK:
                    sess = SESSIONS.get(token)
                if not sess:
                    return _json(self, {"ok": False, "message": "拟合结果已过期，请重新拟合"})

                # 参数来源：前端显式传的优先（用户在弹窗里调过），否则用配方自带参数。
                # 对比度/亮度有曲线时被机身禁用，故不接受。
                PARAM_RANGE = {
                    "quick_sharp": (-2.0, 2.0),
                    "sharp": (-3.0, 9.0),
                    "mid_sharp": (-5.0, 5.0),
                    "clarity": (-5.0, 5.0),
                    "saturation": (-3.0, 3.0),
                }
                params_json = q.get("params", [""])[0]
                params = {}
                if params_json:
                    try:
                        raw = json.loads(params_json)
                    except Exception:
                        raw = {}
                    for key, (lo, hi) in PARAM_RANGE.items():
                        if key in raw and raw[key] is not None:
                            try:
                                v = float(raw[key])
                            except (TypeError, ValueError):
                                continue
                            params[key] = max(lo, min(hi, v))
                else:
                    raw = (sess.get("record") or {}).get("params") or {}
                    if raw:
                        _map = {"sat": "saturation", "sharp": "sharp", "clarity": "clarity"}
                        for src, dst in _map.items():
                            if src in raw and raw[src] is not None:
                                params[dst] = float(raw[src])
                        # 真实配方没有「中等锐化」「快速锐化」概念，写中性 0
                        params.setdefault("mid_sharp", 0.0)
                        params.setdefault("quick_sharp", 0.0)

                payload = pl.build_payload_614(
                    sess["table"].astype("float64") / 32767.0,
                    name=name[:19],
                    family=0x05,
                    params=params or None,
                )
                res = z30cam.write_slot(payload, slot=slot, allow_overwrite=overwrite)
                res["slot"] = slot
                return _json(self, res)

            if path == "/api/rollback":
                import z30cam

                slot = q.get("slot", ["C9"])[0]
                return _json(self, z30cam.rollback(slot=slot))

            if path == "/api/delete":
                import z30cam

                slot = q.get("slot", ["C9"])[0]
                return _json(self, z30cam.delete_slot(slot=slot))

            self.send_error(404)
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            _json(self, {"ok": False, "message": f"{type(exc).__name__}: {exc}", "stage": "server"}, 500)


def make_server(start_port: int = 8765, tries: int = 12):
    """返回 (httpd, port)。端口被占就往后顺延。"""
    WORK.mkdir(parents=True, exist_ok=True)
    port = start_port
    for _ in range(tries):
        try:
            httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
            httpd.daemon_threads = True
            return httpd, port
        except OSError:
            port += 1
    raise RuntimeError("找不到可用端口")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args(argv)

    try:
        httpd, port = make_server(args.port)
    except RuntimeError as exc:
        print(f"× {exc}")
        return 1
    url = f"http://127.0.0.1:{port}/"
    print("Z30 影调工具")
    print(f"  地址：{url}")
    print(f"  Python：{sys.executable}")
    try:
        import usb  # noqa: F401
        import libusb_package  # noqa: F401

        print("  相机支持：已就绪（pyusb + libusb）")
    except ImportError:
        print("  ⚠ 相机支持：缺少 pyusb / libusb-package，只能在离线模式拟合，无法读写机身")
        print("     修复：python -m pip install pyusb libusb-package")
    print("  关闭这个窗口即停止服务")
    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    return 0


if __name__ == "__main__":
    sys.exit(main())
