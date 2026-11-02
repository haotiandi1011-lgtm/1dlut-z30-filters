"""自包含 HTML 报告：`build_markdown()` 的增强出口。

区别只在「怎么交付」：

* markdown 报告里图片是相对路径（`preview_scene.png`），离开产物目录就散架；
* 这里把预览图 **base64 内联**、曲线画成 **内联 SVG**、样式 **内联在 `<style>`**，
  出去的就只有一个 `.html` 文件——双击能开、能发微信、能丢进网盘归档，
  不依赖本地服务和任何外部资源。

口径与界面一致（见 `产品规划.md` §16）：只说这条曲线做了什么、偏差多大、
哪里做不到；不提数据来源与逆向过程。
"""

from __future__ import annotations

import base64
import datetime as _dt
import html
import io
from pathlib import Path

import numpy as np

from .cube import CubeLUT
from .fit1d import FitResult

# 报告里的深色文字/浅色底，打印也不费墨
_CSS = """
:root{
  --bg:#f6f7f9; --card:#fff; --line:#e4e7ec; --text:#171a1f; --muted:#667085;
  --accent:#2563eb; --ok:#15803d; --warn:#b45309; --bad:#b91c1c;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);
  font:15px/1.65 -apple-system,"Segoe UI","Microsoft YaHei",system-ui,sans-serif;
  -webkit-font-smoothing:antialiased}
.wrap{max-width:960px;margin:0 auto;padding:32px 20px 56px}
header{margin-bottom:18px}
h1{margin:0 0 6px;font-size:23px;letter-spacing:-.2px}
.sub{color:var(--muted);font-size:13px}
.badge{display:inline-block;font-size:12px;padding:2px 9px;border-radius:999px;
  background:#eef2ff;color:#3730a3;margin-right:6px}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;
  padding:20px 22px;margin:16px 0;box-shadow:0 1px 2px rgba(16,24,40,.04)}
h2{margin:0 0 14px;font-size:15px;letter-spacing:.3px;color:#344054;
  text-transform:none;border-bottom:1px solid var(--line);padding-bottom:10px}
.verdict{display:flex;gap:14px;align-items:flex-start;border-radius:12px;padding:16px 18px;
  border:1px solid;margin-bottom:18px}
.verdict.ok{background:#f0fdf4;border-color:#bbf7d0}
.verdict.caution{background:#fffbeb;border-color:#fde68a}
.verdict.no{background:#fef2f2;border-color:#fecaca}
.verdict .lbl{font-weight:700;font-size:16px}
.verdict.ok .lbl{color:var(--ok)} .verdict.caution .lbl{color:var(--warn)}
.verdict.no .lbl{color:var(--bad)}
.verdict .txt{font-size:13.5px;color:#475467;margin-top:3px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px}
.stat{background:#fafbfc;border:1px solid var(--line);border-radius:10px;padding:11px 13px}
.stat .k{font-size:11.5px;color:var(--muted)}
.stat .v{font-size:19px;font-weight:700;font-variant-numeric:tabular-nums;margin-top:2px}
.stat .v small{font-size:11px;font-weight:500;color:var(--muted)}
table{width:100%;border-collapse:collapse;font-size:13.5px}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line)}
th{color:var(--muted);font-weight:500}
td.n{font-variant-numeric:tabular-nums}
tr:last-child td{border-bottom:none}
.best{font-size:11px;padding:1px 7px;border-radius:999px;background:#dcfce7;color:#15803d}
figure{margin:0}
figure img{width:100%;height:auto;display:block;border:1px solid var(--line);border-radius:10px}
figcaption{font-size:12.5px;color:var(--muted);margin:7px 0 18px}
.banner{background:#f8fafc;border:1px solid var(--line);border-radius:10px;
  padding:12px 15px;font-size:13px;color:#475467;margin:0 0 16px}
.banner b{color:var(--text)}
ul{margin:6px 0 0;padding-left:22px}
li{margin:3px 0}
.ok-line{color:var(--ok);font-weight:600}
.bad-line{color:var(--bad);font-weight:600}
.muted{color:var(--muted)}
.foot{margin-top:26px;font-size:12.5px;color:var(--muted);line-height:1.8}
code{background:#f2f4f7;border-radius:5px;padding:1px 5px;font-size:12.5px}
@media print{
  body{background:#fff} .card{box-shadow:none;break-inside:avoid}
  .wrap{max-width:none;padding:0}
}
"""


def _esc(x) -> str:
    return html.escape(str(x), quote=True)


def _fmt(x: float, nd: int = 2) -> str:
    try:
        return f"{float(x):.{nd}f}"
    except (TypeError, ValueError):
        return "—"


def _data_uri(path: Path, max_w: int = 1100) -> str:
    """PNG → data URI。宽图先缩到 max_w，报告不至于几百 KB。"""
    from PIL import Image

    try:
        with Image.open(path) as im:
            im = im.convert("RGB")
            if im.width > max_w:
                h = int(round(im.height * max_w / im.width))
                im = im.resize((max_w, h), Image.LANCZOS)
            buf = io.BytesIO()
            im.save(buf, format="PNG", optimize=True)
        data = buf.getvalue()
    except Exception:  # noqa: BLE001 - 图有问题不该拖垮整份报告
        data = Path(path).read_bytes()
    return "data:image/png;base64," + base64.b64encode(data).decode("ascii")


def _curve_svg(series: list[tuple[str, np.ndarray, np.ndarray, str]], w: int = 760, h: int = 420) -> str:
    """影调曲线 → 内联 SVG。series: [(标签, xs, ys, 颜色)]。"""
    pad_l, pad_r, pad_t, pad_b = 52, 18, 46, 40
    x0, x1 = pad_l, w - pad_r
    y0, y1 = pad_t, h - pad_b

    def px(x: float) -> float:
        return x0 + float(np.clip(x, 0, 1)) * (x1 - x0)

    def py(y: float) -> float:
        return y1 - float(np.clip(y, 0, 1)) * (y1 - y0)

    out = [
        f'<svg viewBox="0 0 {w} {h}" width="100%" style="max-width:{w}px" '
        f'role="img" aria-label="影调曲线">',
        '<rect x="0" y="0" width="100%" height="100%" fill="#fcfcfb"/>',
    ]

    # 图例放顶部，单独占一行，不压曲线
    lx = x0
    for label, _, _, color in series:
        out.append(f'<rect x="{lx:.1f}" y="12" width="12" height="12" rx="2" fill="{color}"/>')
        out.append(f'<text x="{lx + 18:.1f}" y="22" font-size="12.5" fill="#475467">{_esc(label)}</text>')
        lx += 30 + 12.2 * len(label)

    for i in range(11):
        t = i / 10
        out.append(f'<line x1="{px(t):.1f}" y1="{y0}" x2="{px(t):.1f}" y2="{y1}" stroke="#eceef1"/>')
        out.append(f'<line x1="{x0}" y1="{py(t):.1f}" x2="{x1}" y2="{py(t):.1f}" stroke="#eceef1"/>')
    out.append(f'<rect x="{x0}" y="{y0}" width="{x1 - x0}" height="{y1 - y0}" fill="none" stroke="#c8cdd4"/>')
    out.append(f'<line x1="{px(0):.1f}" y1="{py(0):.1f}" x2="{px(1):.1f}" y2="{py(1):.1f}" '
               f'stroke="#d4d8dd" stroke-dasharray="4 4"/>')

    for label, xs, ys, color in series:
        pts = " ".join(f"{px(float(x)):.1f},{py(float(y)):.1f}" for x, y in zip(xs, ys))
        dash = ' stroke-dasharray="5 4"' if label.startswith("参考") else ""
        out.append(f'<polyline points="{pts}" fill="none" stroke="{color}" '
                   f'stroke-width="2.4" stroke-linejoin="round"{dash}/>')

    for i in (0, 64, 128, 192, 255):
        t = i / 255
        out.append(f'<text x="{px(t):.1f}" y="{y1 + 18}" font-size="11" fill="#667085" '
                   f'text-anchor="middle">{i}</text>')
    for v in (0.0, 0.5, 1.0):
        out.append(f'<text x="{x0 - 8}" y="{py(v) + 4:.1f}" font-size="11" fill="#667085" '
                   f'text-anchor="end">{int(round(v * 255))}</text>')
    out.append(f'<text x="{(x0 + x1) / 2:.1f}" y="{h - 6}" font-size="11.5" fill="#98a2b3" '
               f'text-anchor="middle">输入亮度</text>')
    out.append("</svg>")
    return "\n".join(out)


def _series(result: FitResult, lut: CubeLUT | None) -> list[tuple[str, np.ndarray, np.ndarray, str]]:
    xs = np.linspace(0.0, 1.0, 256)
    series: list[tuple[str, np.ndarray, np.ndarray, str]] = [
        ("恒等", xs, xs, "#c8cdd4"),
    ]
    if lut is not None:
        try:
            _, ref_out = lut.neutral_ramp(256)
            series.append(("参考（中性轴）", xs, ref_out.mean(axis=1), "#6b7fd7"))
        except Exception:  # noqa: BLE001
            pass
    series.append(("这条曲线", xs, np.asarray(result.table_0_1, dtype=float), "#2563eb"))
    return series


def _preview_block(previews: dict[str, Path], key: str, caption: str) -> str:
    path = previews.get(key)
    if not path or not Path(path).is_file():
        return ""
    uri = _data_uri(Path(path))
    return (
        "<figure>"
        f'<img src="{uri}" alt="{_esc(caption)}">'
        f"<figcaption>{_esc(caption)}</figcaption>"
        "</figure>"
    )


def build_html(
    result: FitResult,
    lut: CubeLUT | None,
    previews: dict[str, Path],
    outdir: str | Path,
    *,
    facts: dict | None = None,
    notes: list[str] | None = None,
    metric_mode: str = "deltae",
    headline: str | None = None,
    subtitle: str = "离线拟合，未接触相机",
    strategy: str | None = None,
    extra_images: list[tuple[str, str]] | None = None,
) -> str:
    """生成整份报告 HTML（单文件、无外部依赖）。

    metric_mode:
        "deltae" 有真值（.cube / .np3 / 成对图）→ 给 ΔE 与策略对比
        "none"   没有真值（预设 / 配方包 / 单图）→ 只给影调画像，不给任何"还原度"数字
    extra_images: 额外插进「视觉对比」的图，[(previews 键, 说明)]
    """
    m = result.metrics or {}
    b = result.banding or {}
    t = result.tone or {}
    now = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    src_name = Path(result.source).name or str(result.source) or "未命名"
    deltae = metric_mode == "deltae" and "mean" in m

    parts: list[str] = [
        "<!DOCTYPE html>",
        '<html lang="zh-CN"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        f"<title>影调报告 · {_esc(src_name)}</title>",
        f"<style>{_CSS}</style></head><body><div class=\"wrap\">",
        "<header>",
        f"<h1>影调报告 · {_esc(src_name)}</h1>",
        f'<div class="sub"><span class="badge">{_esc(result.lut_kind)}</span>'
        f"生成于 {now} · {_esc(subtitle)}</div>",
        "</header>",
    ]

    if notes:
        body = "".join(f"<p style=\"margin:{0 if i == 0 else 8}px 0 0\">{n}</p>" for i, n in enumerate(notes))
        parts.append(f'<div class="banner">{body}</div>')

    # -- 结论 ------------------------------------------------------------
    if deltae:
        level, text = _verdict_ui(m)
        parts.append(
            f'<div class="verdict {level}"><div>'
            f'<div class="lbl">{_esc(headline or text[0])}</div>'
            f'<div class="txt">{_esc(text[1])}</div></div></div>'
        )
        parts.append('<div class="card"><h2>偏差</h2><div class="grid">')
        parts.append(f'<div class="stat"><div class="k">ΔE2000 平均</div><div class="v">{_fmt(m.get("mean"))}</div></div>')
        parts.append(f'<div class="stat"><div class="k">中位 / P95</div><div class="v">{_fmt(m.get("median"))}'
                     f'<small> / {_fmt(m.get("p95"))}</small></div></div>')
        parts.append(f'<div class="stat"><div class="k">≤2.0 的点</div><div class="v">'
                     f'{_fmt(float(m.get("within_2", 0)) * 100, 0)}<small>%</small></div></div>')
        parts.append(f'<div class="stat"><div class="k">逐通道 RMSE</div><div class="v" style="font-size:15px">'
                     f'{_fmt(m.get("rmse_r"), 1)}/{_fmt(m.get("rmse_g"), 1)}/{_fmt(m.get("rmse_b"), 1)}</div></div>')
        parts.append("</div>")
        parts.append(
            f'<p class="muted" style="font-size:13px;margin:12px 0 0">'
            f'ΔE 平均 &lt; 2 肉眼基本无差；2–4 谨慎，通常丢的是色彩个性；&gt; 4 说明这个风格靠 1D 曲线表达不了。'
            f'本份最大偏差 {_fmt(m.get("max"))}。</p>'
        )
        parts.append("</div>")
    else:
        parts.append(
            '<div class="verdict ok"><div>'
            f'<div class="lbl">{_esc(headline or "这条曲线已就绪")}</div>'
            '<div class="txt">预设本身就是一条曲线，没有参考真值，所以这里不给 ΔE——'
            '只看它把明暗改成了什么样。</div></div></div>'
        )

    # -- 输入 ------------------------------------------------------------
    rows = [
        ("来源", f"<code>{_esc(result.source)}</code>"),
        ("类型", _esc(result.lut_kind)),
        ("处理策略", _esc(strategy or (result.label if hasattr(result, "label") else result.mode))),
    ]
    for k, v in (facts or {}).items():
        rows.append((_esc(k), _esc(v)))
    parts.append('<div class="card"><h2>输入</h2><table><tbody>')
    for k, v in rows:
        parts.append(f'<tr><th style="width:110px">{k}</th><td>{v}</td></tr>')
    parts.append("</tbody></table></div>")

    # -- 曲线 ------------------------------------------------------------
    parts.append('<div class="card"><h2>影调曲线</h2>')
    parts.append(_curve_svg(_series(result, lut)))
    parts.append(
        '<p class="muted" style="font-size:12.5px;margin:10px 0 0">'
        "横轴是输入亮度，纵轴是输出亮度（都是 0–255）。曲线整体抬高＝提亮，变陡＝加对比，"
        "左端不落在 0＝抬黑（暗部发灰），右端不落在 255＝压白。</p>"
    )
    parts.append("</div>")

    # -- 视觉对比 ---------------------------------------------------------
    blocks = [
        _preview_block(previews, "chart", "色卡 / 灰阶 / 肤色"),
        _preview_block(previews, "scene", "合成风景：天空、植被、高光、阴影"),
        _preview_block(previews, "gradient", "连续渐变：看色阶有没有断层"),
    ]
    for key, caption in extra_images or []:
        blocks.append(_preview_block(previews, key, caption))
    if any(blocks):
        parts.append('<div class="card"><h2>视觉对比</h2>')
        parts.extend(blocks)
        parts.append("</div>")

    # -- 影调画像 ---------------------------------------------------------
    parts.append('<div class="card"><h2>影调画像</h2><table><thead><tr>'
                 "<th>指标</th><th>值</th><th>怎么读</th></tr></thead><tbody>")
    tone_rows = [
        ("黑位", f"{float(t.get('black', 0)) * 100:.1f}%", "0 输入映射到的输出，0 才是纯黑"),
        ("白位", f"{float(t.get('white', 1)) * 100:.1f}%", "1.0 输入映射到的输出"),
        ("18% 灰", f"{float(t.get('mid_18', 0)) * 100:.1f}%", "中间调亮度，决定整体观感明暗"),
        ("25–75% 对比", f"{float(t.get('contrast_25_75', 1)):.2f}×", "1.0 = 与线性相同"),
        ("趾部斜率", f"{float(t.get('slope_toe', 0)):.2f}", "暗部压缩 / 抬升强度"),
        ("中间调斜率", f"{float(t.get('slope_mid', 0)):.2f}", "主体反差"),
        ("肩部斜率", f"{float(t.get('slope_shoulder', 0)):.2f}", "高光滚降强度"),
    ]
    for k, v, note in tone_rows:
        parts.append(f"<tr><td>{k}</td><td class=\"n\">{v}</td><td class=\"muted\">{note}</td></tr>")
    parts.append("</tbody></table></div>")

    # -- 断层 ------------------------------------------------------------
    mono = bool(b.get("monotone"))
    flats = b.get("artifact_flats") or []
    ok = mono and not flats and int(b.get("max_8bit_dupes", 0)) <= 2
    parts.append('<div class="card"><h2>色阶断层检测</h2><ul>')
    parts.append(f'<li>单调性：{"严格单调 ✅" if mono else "存在反转 ❌"}</li>')
    parts.append(f'<li>最长塌陷段：{b.get("max_flat_run", 1)} 步（1 = 每个点都不同，正常）</li>')
    if flats:
        detail = "、".join(f"第 {s} 点起 {ln} 步" for s, ln in flats)
        parts.append(f'<li class="bad-line">无法用参考解释的塌陷：{_esc(detail)}</li>')
    else:
        parts.append("<li>没有无法解释的塌陷</li>")
    parts.append(f'<li>单段 8 位码值复用：最多 {b.get("max_8bit_dupes", 0)} 个点（&gt;2 表示该段被压死）</li>')
    parts.append(f'<li>8 位可用码值：{b.get("distinct_codes", 256)}/256</li>')
    parts.append("</ul>")
    parts.append(
        f'<p class="{"ok-line" if ok else "bad-line"}" style="margin-top:12px">'
        + ("没有断层风险，可以写进机身" if ok else "存在塌陷风险，建议调大样条节点数后重拟合")
        + "</p></div>"
    )

    # -- 策略对比 ---------------------------------------------------------
    cands = getattr(result, "candidates", None) or []
    if deltae and len(cands) > 1:
        parts.append('<div class="card"><h2>策略对比</h2><table><thead><tr>'
                     "<th>策略</th><th>采样点</th><th>灰阶偏离</th><th>ΔE 平均</th><th>ΔE P95</th>"
                     "</tr></thead><tbody>")
        for c in cands:
            mark = ' <span class="best">采用</span>' if c.mode == result.mode else ""
            parts.append(
                f"<tr><td>{_esc(c.label)}{mark}</td><td class=\"n\">{c.sample_count}</td>"
                f"<td class=\"n\">{float(c.metrics.get('gray_dev', 0)) * 100:.1f}%</td>"
                f"<td class=\"n\">{_fmt(c.metrics.get('mean'))}</td>"
                f"<td class=\"n\">{_fmt(c.metrics.get('p95'))}</td></tr>"
            )
        parts.append("</tbody></table>")
        parts.append(
            '<p class="muted" style="font-size:12.5px;margin:10px 0 0">'
            "择优规则：灰阶偏离 ≤ 2% 才有资格参选，再按综合分取最小。"
            "灰阶是硬指标——黑位一抬，整张片子的暗部就废了，不是「平均误差略低」能补偿的。</p>"
        )
        parts.append("</div>")

    # -- 边界 ------------------------------------------------------------
    parts.append('<div class="card"><h2>这个工具做不到的</h2><ul>')
    parts.append("<li>机身只有<b>一条</b>作用于 R/G/B 的曲线，做不了分色相调整、分离色调、HSL。</li>")
    parts.append("<li>整体色偏只能靠机身白平衡，曲线改不了色相——所谓「冷调/暖调」由白平衡决定。</li>")
    parts.append("<li>曲线在机身里排在白平衡前面（乘法关系），换白平衡效果会跟着变。</li>")
    if deltae:
        parts.append("<li>ΔE 是对「参考输出」算的，不是对机身实拍算的；机身自己的色彩处理会再叠一层差异。</li>")
    parts.append("<li>评测图是程序合成的，用来查灰阶 / 断层 / 整体观感，不能替代实拍样张。</li>")
    parts.append("</ul></div>")

    parts.append(
        '<div class="foot">'
        "报告由本地工具生成，全部计算都在本机完成，没有联网、没有上传。<br>"
        "曲线是<b>近似</b>：同一份曲线在不同白平衡、不同机内设置下观感会变，"
        "写进机身前建议先看视觉对比。报告可以整份保存或转发——图片已内嵌在文件里。"
        "</div>"
    )
    parts.append("</div></body></html>")
    return "\n".join(parts)


def _verdict_ui(m: dict) -> tuple[str, tuple[str, str]]:
    """把 verdict() 的结论翻成 (css 类, (标题, 说明))。"""
    mean = float(m.get("mean", 0))
    p95 = float(m.get("p95", 0))
    if mean < 2.0 and p95 < 6.0:
        return "ok", ("建议写入", "肉眼基本分辨不出差异。")
    if mean < 4.0:
        return "caution", ("谨慎写入", "影调对上了，但色彩个性会丢一部分——先看下面的视觉对比再决定。")
    return "no", ("不建议写入", "这个风格主要靠分色相调整，1D 曲线表达不了。")


def write_html_report(
    result: FitResult,
    lut: CubeLUT | None,
    previews: dict[str, Path],
    outdir: str | Path,
    **kwargs,
) -> Path:
    """生成 `<outdir>/report.html` 并返回路径。"""
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    path = outdir / "report.html"
    path.write_text(build_html(result, lut, previews, outdir, **kwargs), encoding="utf-8")
    return path
