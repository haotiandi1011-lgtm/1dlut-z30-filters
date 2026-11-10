#!/usr/bin/env python
"""本地网页服务：拟合 + 写入 Z30。

只监听 127.0.0.1，不对外网开放。启动方式见根目录的 `启动.cmd`。

    python webui/server.py [--port 8765] [--no-browser]
"""

from __future__ import annotations

import argparse
import hashlib
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
from urllib.parse import parse_qs, quote, unquote, urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
HERE = Path(__file__).resolve().parent

from z30fit import payload as pl  # noqa: E402
from z30fit.paths import app_root, data_root, resource  # noqa: E402
from z30fit.fit1d import MODE_LABELS  # noqa: E402
from z30fit.metrics import verdict  # noqa: E402
from z30fit.curve import banding_analysis  # noqa: E402
from z30fit.pipeline import fit_from_path  # noqa: E402
from z30fit.photo import RAW_EXT  # noqa: E402
from z30fit.report import render_previews  # noqa: E402
from z30fit.reporthtml import write_html_report  # noqa: E402

WORK = data_root() / "webui"
UPLOADS = WORK / "uploads"
STATIC = resource("webui")
SAMPLES = resource("samples")

SESSIONS: dict[str, dict] = {}
# 预设 → 已渲染的 token。同一条曲线反复点不该反复渲染（点一次几百毫秒，切一圈会卡）
PREVIEW_CACHE: dict[str, str] = {}
LOCK = threading.Lock()
MAX_UPLOAD = 90 * 1024 * 1024   # NEF 单张能到 30–50 MB，给够
SERVER_PORT = 8765


def _resolve_src(src: str) -> Path | None:
    """`sample:xxx.cube` / `upload:xxx.NEF` → 真实路径。"""
    if src.startswith("sample:"):
        path = SAMPLES / src.split(":", 1)[1]
    elif src.startswith("upload:"):
        path = UPLOADS / src.split(":", 1)[1]
    else:
        return None
    return path if path.is_file() else None


def _report_url(token: str) -> str:
    return f"/files/{token}/report.html"


def _open_report(token: str) -> None:
    """在系统浏览器里打开这份报告（独立于工具窗口，可以留着对照看）。"""
    url = f"http://127.0.0.1:{SERVER_PORT}{_report_url(token)}"

    def run() -> None:
        time.sleep(0.35)  # 先让拟合的响应回到页面，别跟浏览器抢线程
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001
            pass

    threading.Thread(target=run, daemon=True).start()


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


def _preset_bulk(action: str, q: dict) -> dict:
    """预设施批量操作：隐藏 / 取消隐藏 / 删除到回收站 / 从回收站恢复。

    内置与配方库是随包发出去的数据，删不掉真身 → 只能「隐藏」；
    用户自己存的预设才是真删，但删进回收站，随时能恢复。
    """
    from z30fit import ncp_presets, presets

    ids = [x for x in (q.get("ids", [""])[0] or "").split(",") if x]
    everything = q.get("all", ["0"])[0] == "1"

    user_ids = {p["id"] for p in presets.load_user()}
    builtin_ids = ({p["id"] for p in presets.listing()} | {p["id"] for p in ncp_presets.listing()}) - user_ids

    if action == "hide":
        target = [i for i in ids if i in builtin_ids] if not everything else sorted(builtin_ids)
        n = presets.hide(target)
        return {"ok": True, "count": len(target), "message": f"已隐藏 {len(target)} 套"}
    if action == "unhide":
        n = presets.unhide_all() if everything else presets.unhide([i for i in ids or [""]])
        return {"ok": True, "count": n, "message": f"已恢复 {n} 套"}
    if action == "remove":
        target = [i for i in ids if i in user_ids] if not everything else sorted(user_ids)
        n = presets.move_to_trash(target)
        return {"ok": True, "count": n, "message": f"已移入回收站 {n} 套"}
    if action == "restore":
        n = presets.restore_all_trash() if everything else presets.restore_from_trash(ids)
        return {"ok": True, "count": n, "message": f"已恢复 {n} 套"}
    if action == "purge":
        n = presets.purge_all_trash() if everything else presets.purge_trash(ids)
        return {"ok": True, "count": n, "message": f"已彻底删除 {n} 套（不可恢复）"}
    return {"ok": False, "message": f"不认识的操作：{action}"}


def _save_preset(q: dict) -> dict:
    """把当前结果存成「我的预设」。GET/POST 都能调（前端历史上用 POST）。"""
    from z30fit import presets

    token = q.get("token", [""])[0]
    name = safe_name(q.get("name", [""])[0] or "自定义")
    desc = q.get("desc", [""])[0][:60]
    with LOCK:
        sess = SESSIONS.get(token)
    if not sess:
        return {"ok": False, "message": "结果已过期，请重新选一次"}
    entry = presets.save_user(name, desc, sess["table"], sess["record"].get("source", ""))
    return {"ok": True, "preset": entry, "message": f"已保存「{entry['name']}」"}


def _attach_report(rec: dict, fit, lut, previews, outdir, **kwargs) -> None:
    """把这条曲线的 HTML 报告落盘，并把地址挂到记录上。

    报告是自包含单文件（图内嵌、曲线是 SVG），所以走 /files/ 直接下也行、
    离线拷走也行。生成失败不该影响拟合结果本身，这里吞掉异常。
    """
    try:
        write_html_report(fit, lut, previews, outdir, **kwargs)
        rec["report_url"] = _report_url(rec["token"])
    except Exception:  # noqa: BLE001
        traceback.print_exc()


IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"} | RAW_EXT


def _decodable(path: Path) -> Path:
    """RAW 先转成 JPEG 再进现有的单图 / 成对图管线，避免每个算法都去处理 RAW。"""
    if path.suffix.lower() not in RAW_EXT:
        return path
    from z30fit import photo as photo_mod

    im, _ = photo_mod.load_image(path, max_side=2600)
    tag = hashlib.sha1(path.name.encode("utf-8", "ignore")).hexdigest()[:8]
    out = UPLOADS / f"_raw_{tag}.jpg"
    if not out.exists():
        im.save(out, quality=95, optimize=True)
    return out


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
    _attach_report(
        rec, fit, None, previews, outdir,
        metric_mode="none",
        strategy="配方包直读（不重新拟合）",
        headline=f"配方包「{rec['name']}」已就绪",
        facts={"元数据": meta.get("note") or "（无）", "生成工具": meta.get("tool") or "—"},
        notes=["配方包里存的是 614 字节载荷，曲线是唯一真值；元数据只是备注，丢了也不影响写入。"],
    )
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


def _fit_image(path: Path, knots: int, label: str = "") -> dict:
    """单张参考图 → 影调曲线。这里给不出 ΔE，只有「影调贴合误差（灰阶）」。

    label 是给界面看的原始文件名（RAW 会先转成临时 JPEG 再算，别把临时名露出去）。
    """
    from z30fit import refimage
    from z30fit.fit1d import Candidate, FitResult

    display = Path(label).name if label else path.name
    res = refimage.image_fit(path, knots=knots)
    token = f"i{int(time.time() * 1000) % 10_000_000}"
    outdir = WORK / token
    outdir.mkdir(parents=True, exist_ok=True)

    fit = FitResult(
        source=display,
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
        "name": Path(display).stem,
        "source": display,
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
    _attach_report(
        rec, fit, None, previews, outdir,
        metric_mode="none",
        strategy="分位数匹配（单图，无真值）",
        headline="影调已贴合参考图",
        facts={
            "影调贴合误差": f"{st['tone_match_mean']:.2f} 灰阶（最大 {st['tone_match_max']:.2f}）",
            "截断比例": f"纯黑 {st['clip_low'] * 100:.1f}% · 纯白 {st['clip_high'] * 100:.1f}%",
        },
        extra_images=[
            ("ref_compare", "参考图 vs 这条曲线的效果"),
            ("hist", "亮度分布：参考图 vs 曲线作用后的典型场景"),
        ],
        notes=["单图模式没有真值，所以这里不给 ΔE。数值衡量的是「曲线的分位锚点有没有复现参考图」，"
               "不是逐像素还原度；色彩个性（偏色、去饱和、分离色调）无法从一张图反推。"],
    )

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
    _attach_report(
        rec, fit, None, previews, outdir,
        metric_mode="none",
        strategy="预设曲线（不拟合）",
        headline=f"「{name}」",
        facts={"说明": desc or "—"},
    )
    with LOCK:
        # 存的是 rec 本身：调用方还会往里补 cam / params，缓存命中时要能读到
        SESSIONS[token] = {"record": rec, "table": table, "meta": meta}
    return rec


def _fit_pair(src_path: Path, dst_path: Path, knots: int,
              label_src: str = "", label_dst: str = "") -> dict:
    """成对图 → 影调曲线。有真值，所以给的是真正的 ΔE2000。"""
    from z30fit import pairfit
    from z30fit.fit1d import Candidate, FitResult

    name_src = Path(label_src).name if label_src else src_path.name
    name_dst = Path(label_dst).name if label_dst else dst_path.name

    res = pairfit.pair_fit(src_path, dst_path, knots=knots)
    token = f"p{int(time.time() * 1000) % 10_000_000}"
    outdir = WORK / token
    outdir.mkdir(parents=True, exist_ok=True)

    fit = FitResult(
        source=f"{name_src} → {name_dst}",
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
        "name": Path(name_src).stem,
        "source": f"{name_src} → {name_dst}",
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
    _attach_report(
        rec, fit, None, previews, outdir,
        metric_mode="deltae",
        strategy="成对图拟合（三种输入里唯一有真值）",
        facts={
            "套曲线前 ΔE": f"{m['before_mean']:.2f} → 之后 {m['mean']:.2f}",
            "对准位移": f"{m['shift'][0]}, {m['shift'][1]} px",
        },
        extra_images=[("pair", "原图 → 目标图 → 这条曲线的效果")],
        notes=["ΔE 是拿原图套上曲线后和目标图逐像素比出来的。曲线只能搬影调——目标图里的偏色、去饱和、"
               "分离色调搬不走，这部分会留在 ΔE 里，所以「降了多少」比「降到多少」更值得看。"],
    )

    with LOCK:
        SESSIONS[token] = {"record": rec, "table": res["table"]}
    return {"ok": True, **rec}


def do_fit(src: str, mode: str, knots: int, target: str = "", open_report: bool = False) -> dict:
    path = _resolve_src(src)
    if path is None:
        raise ValueError(f"找不到文件：{src.split(':')[-1]}")

    if path.suffix.lower() in RECIPE_EXT:
        return _fit_recipe(path)

    if path.suffix.lower() in IMAGE_EXT:
        if target.startswith("upload:"):
            tpath = UPLOADS / target.split(":", 1)[1]
            if tpath.exists() and tpath.suffix.lower() in IMAGE_EXT:
                return _fit_pair(_decodable(path), _decodable(tpath), knots,
                                 label_src=path.name, label_dst=tpath.name)
            raise ValueError("目标图不存在或不是图片")
        return _fit_image(_decodable(path), knots, label=path.name)

    result, ref_lut = fit_from_path(path, mode=mode, knots=knots)

    token = f"t{int(time.time() * 1000) % 10_000_000}"
    outdir = WORK / token
    outdir.mkdir(parents=True, exist_ok=True)

    previews = render_previews(result, ref_lut, outdir)
    payload = pl.build_payload_614(result.table_0_1, name="TEMP", family=0x05,
                                   template=None)

    rec = {
        "token": token,
        "kind": "fit",
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

    # 报告两版都写：HTML 给人看（自包含、可转发），markdown 给存档/检索
    _attach_report(
        rec, result, ref_lut, previews, outdir,
        metric_mode="deltae",
        strategy=result.label,
        facts={k: v for k, v in (result.extra or {}).items()},
    )
    try:
        from z30fit.report import build_markdown

        md = build_markdown(result, ref_lut, previews, outdir)
        (outdir / "report.md").write_text(md, encoding="utf-8")
        rec["report_md"] = f"/files/{token}/report.md"
    except Exception:  # noqa: BLE001
        pass

    if open_report and rec.get("report_url"):
        _open_report(token)

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
            if path == "/api/photo_preview":
                # 拿用户自己的照片生成「原图 / 套用后」两张同尺寸图，供拖动对比
                from z30fit import photo as photo_mod

                token = q.get("token", [""])[0]
                src = q.get("src", [""])[0]
                with LOCK:
                    sess = SESSIONS.get(token)
                if not sess:
                    return _json(self, {"ok": False, "message": "结果已过期，请重新选一次"})

                target = _resolve_src(src)
                if target is None:
                    return _json(self, {"ok": False, "message": "找不到这张照片，重新拖一次"})

                try:
                    res = photo_mod.write_pair(
                        target, sess["table"], WORK / token,
                        max_side=int(q.get("w", ["1600"])[0]),
                    )
                except photo_mod.PhotoError as exc:
                    return _json(self, {"ok": False, "message": str(exc)})

                return _json(self, {
                    "ok": True,
                    "a": f"/files/{token}/{res['a'].name}",
                    "b": f"/files/{token}/{res['b'].name}",
                    "width": res["width"], "height": res["height"],
                    "note": res["note"], "source": res["source"],
                })
            if path == "/api/photo_export":
                # 全分辨率套曲线 → 成品图下发（png / jpg / tif），浏览器「另存为」选路径
                from z30fit import photo as photo_mod

                token = q.get("token", [""])[0]
                src = q.get("src", [""])[0]
                fmt = q.get("fmt", ["png"])[0].lower()
                with LOCK:
                    sess = SESSIONS.get(token)
                if not sess:
                    return _json(self, {"ok": False, "message": "结果已过期，请重新拟合一次"})

                target = _resolve_src(src)
                if target is None:
                    return _json(self, {"ok": False, "message": "找不到这张照片，重新拖一次"})

                try:
                    data, fname, note = photo_mod.export_photo(target, sess["table"], fmt=fmt)
                except photo_mod.PhotoError as exc:
                    return _json(self, {"ok": False, "message": str(exc)})

                mime = photo_mod.EXPORT_FORMATS[fmt.lower()][1]
                fname_ascii = fname.encode("ascii", "ignore").decode() or "photo"
                fname_u8 = quote(fname)
                self.send_response(200)
                self.send_header("Content-Type", mime)
                self.send_header(
                    "Content-Disposition",
                    f'attachment; filename="{fname_ascii}"; filename*=UTF-8\'\'{fname_u8}',
                )
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
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
                # 注意：单图（照片拟合）的记录没有 metrics（口径：没有真值不给 ΔE），
                # 其他模式才有。这里必须用 .get 兜底，不能 rec["metrics"]。
                m = rec.get("metrics") or {}
                meta = rc.make_meta(
                    name=pname,
                    source=rec.get("source", ""),
                    metrics={k: m.get(k) for k in ("mean", "p95", "max") if m.get(k) is not None},
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

                hidden_ids = set(presets.load_hidden())
                every = presets.listing() + ncp_presets.listing()
                visible = [p for p in every if p["id"] not in hidden_ids]
                hidden = [p for p in every if p["id"] in hidden_ids]
                trash = [
                    {k: t.get(k) for k in ("id", "name", "desc", "created", "deleted")}
                    for t in presets.load_trash()
                ]
                return _json(self, {
                    "ok": True, "presets": visible, "hidden": hidden, "trash": trash,
                    "note": ncp_presets.NOTE,
                })
            if path == "/api/save_preset":
                return _json(self, _save_preset(q))
            if path == "/api/delete_preset":
                from z30fit import presets

                pid = q.get("id", [""])[0]
                return _json(self, {"ok": presets.delete_user(pid)})


            if path == "/api/env":
                from z30fit import paths

                return _json(self, {
                    "ok": True,
                    "data_dir": str(paths.data_root()),
                    "portable": paths.is_portable(),
                    "frozen": paths.FROZEN,
                    "app_dir": str(paths.exe_dir()),
                })
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
                from z30fit.photo import ALL_EXT

                name = Path(q.get("name", ["upload.cube"])[0]).name
                ok_ext = {".cube", ".np3", ".z30"} | ALL_EXT
                if Path(name).suffix.lower() not in ok_ext:
                    return _json(self, {
                        "ok": False,
                        "message": "不支持这个格式（认 .cube / .np3 / .z30 / 常见图片 / 相机 RAW）",
                    })
                UPLOADS.mkdir(parents=True, exist_ok=True)
                target = UPLOADS / name
                target.write_bytes(body)
                return _json(self, {"ok": True, "name": name, "size": len(body)})

            if path == "/api/preset":
                from z30fit import presets, ncp_presets

                pid = q.get("id", [""])[0]
                # 同一条曲线只渲染一次：来回点预设时不该每次都等几百毫秒
                with LOCK:
                    hit = SESSIONS.get(PREVIEW_CACHE.get(pid, ""))
                if hit and hit.get("record"):
                    return _json(self, {"ok": True, **hit["record"]})

                p = presets.get(pid) or ncp_presets.get(pid)
                if not p:
                    return _json(self, {"ok": False, "message": "找不到这个预设"})

                rec = _record_from_table("k", "preset", p["name"], p["table"], p.get("desc", ""))
                # 机身名（ASCII）、原配方参数与基础 profile：写入前弹窗里会用到
                rec["cam"] = p.get("cam", "")
                rec["base"] = p.get("base", "")
                rec["params"] = p.get("params", {}) or {}
                with LOCK:
                    PREVIEW_CACHE[pid] = rec["token"]
                return _json(self, {"ok": True, **rec})
            if path in ("/api/presets/hide", "/api/presets/unhide",
                        "/api/presets/remove", "/api/presets/restore",
                        "/api/presets/purge"):
                return _json(self, _preset_bulk(path.rsplit("/", 1)[-1], q))
            if path == "/api/save_preset":
                return _json(self, _save_preset(q))
            if path == "/api/delete_preset":
                from z30fit import presets

                return _json(self, {"ok": presets.delete_user(q.get("id", [""])[0])})
            if path == "/api/preset_rename":
                # 逐条改名：内置/配方库存别名（不动内置数据），我的预设直接改 name。
                # id 永远不变——老用户的隐藏/回收站记录都挂在 id 上。
                from z30fit import ncp_presets, presets

                pid = q.get("id", [""])[0]
                name = (q.get("name", [""])[0] or "").strip()
                if not pid:
                    return _json(self, {"ok": False, "message": "缺少 id"})
                if len(name) > 40:
                    return _json(self, {"ok": False, "message": "名字最多 40 个字"})
                is_builtin = any(p["id"] == pid for p in presets.BUILTIN) or pid.startswith("ncp-")
                if is_builtin:
                    applied = presets.set_alias(pid, name)
                    return _json(self, {"ok": True, "name": applied, "reset": not applied})
                applied = presets.rename_user(pid, name)
                return _json(self, {"ok": bool(applied), "name": applied,
                                    "message": "" if applied else "没找到这条预设，或名字为空"})

            if path == "/api/fit":
                src = q.get("src", [""])[0]
                mode = q.get("mode", ["auto"])[0]
                knots = int(q.get("knots", ["33"])[0])
                target = q.get("target", [""])[0]
                want_open = q.get("open", ["1"])[0] != "0"
                t0 = time.time()
                res = do_fit(src, mode, knots, target, open_report=want_open)
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
    global SERVER_PORT
    WORK.mkdir(parents=True, exist_ok=True)
    port = start_port
    for _ in range(tries):
        try:
            httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
            httpd.daemon_threads = True
            SERVER_PORT = port  # 报告要用绝对地址打开，端口可能已经顺延过
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
