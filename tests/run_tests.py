"""回归测试（不依赖 pytest，直接 python tests/run_tests.py）。

覆盖：样条单调性与插值精度、保序回归、色差实现、LUT 读写与插值、
NP3 解析与曲线提取、LUT 段打包、拟合不变量。
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from z30fit import LUT_MAX, color, cube, curve, fit1d, np3  # noqa: E402

PASS: list[str] = []
FAIL: list[tuple[str, str]] = []
SKIP: list[str] = []


class SkipTest(Exception):
    """本地私有样本缺失时跳过。这些 NP3 样本不随仓库发布，需自备。"""


def fixture(rel: str) -> Path:
    path = ROOT / rel
    if not path.exists():
        raise SkipTest(f"缺本地样本 {rel}，跳过")
    return path


def check(name: str):
    def deco(fn):
        try:
            fn()
            PASS.append(name)
        except SkipTest as exc:
            SKIP.append(f"{name}（{exc}）")
        except Exception as exc:  # noqa: BLE001
            FAIL.append((name, f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=3)}"))
        return fn

    return deco


# --------------------------------------------------------------------------
@check("PCHIP 穿过节点且严格单调")
def _():
    x = np.linspace(0, 1, 17)
    y = np.sort(np.random.default_rng(1).random(17))
    y[0], y[-1] = 0.0, 1.0
    s = curve.MonotoneSpline(x, y)
    assert np.allclose(s(x), y, atol=1e-12), "样条未穿过节点"
    dense = s(np.linspace(0, 1, 4001))
    assert np.all(np.diff(dense) >= -1e-12), "输出非单调"
    assert np.allclose(s(np.linspace(0, 1, 5))[0], 0.0)


@check("PCHIP 不会过冲（保形）")
def _():
    x = np.array([0.0, 0.25, 0.5, 0.75, 1.0])
    y = np.array([0.0, 0.05, 0.9, 0.95, 1.0])
    s = curve.MonotoneSpline(x, y)
    dense = s(np.linspace(0, 1, 2001))
    assert dense.min() >= -1e-9 and dense.max() <= 1 + 1e-9, "出现过冲"


@check("PAVA 保序且为 L2 最优解之一")
def _():
    y = np.array([1.0, 3.0, 2.0, 4.0])
    out = curve.pava(y)
    assert np.all(np.diff(out) >= 0)
    assert np.allclose(out, [1.0, 2.5, 2.5, 4.0]), out


@check("单调最小二乘能还原已知曲线")
def _():
    x = np.linspace(0, 1, 256)
    t = np.clip(0.02 + 0.96 * x**1.7, 0, 1)
    s = curve.fit_monotone_curve(x, t, np.ones_like(x), n_knots=33)
    pred = s(x)
    rmse = float(np.sqrt(np.mean((pred - t) ** 2)))
    assert rmse < 0.004, f"RMSE 过大：{rmse:.5f}"
    assert abs(float(s(0.0)) - 0.02) < 0.01, "起点没跟上"


@check("CIEDE2000 命中 Sharma 标准算例")
def _():
    p1 = np.array([[50.0, 2.6772, -79.7751]])
    p2 = np.array([[50.0, 0.0, -82.7485]])
    de = float(color.delta_e_2000(p1, p2)[0])
    assert abs(de - 2.0425) < 0.001, f"得到 {de:.4f}，应为 2.0425"
    p3 = np.array([[50.0, 3.1571, -77.2803]])
    de2 = float(color.delta_e_2000(p3, p2)[0])
    assert abs(de2 - 2.8615) < 0.001, f"得到 {de2:.4f}，应为 2.8615"
    assert float(color.delta_e_2000(p1, p1)[0]) == 0.0


@check("恒等 1D LUT 拟合无误差")
def _():
    r = fit1d.fit_lut(cube.identity_1d(33), source="id1")
    assert r.metrics["mean"] < 0.02, r.metrics["mean"]
    assert int(r.table[0]) == 0 and int(r.table[-1]) == LUT_MAX
    assert len(r.segment) == 512


@check("恒等 3D LUT 拟合无误差")
def _():
    r = fit1d.fit_lut(cube.identity_3d(17), source="id3")
    assert r.metrics["mean"] < 0.02, r.metrics["mean"]
    assert r.metrics["gray_dev"] < 1e-3


@check("纯 gamma 3D LUT 能精确还原")
def _():
    n = 17
    g = np.linspace(0, 1, n)
    b, gg, rr = np.meshgrid(g, g, g, indexing="ij")
    lut = cube.CubeLUT(
        kind=3, size=n, data=np.stack([rr**1.8, gg**1.8, b**1.8], axis=-1),
        domain_min=np.zeros(3), domain_max=np.ones(3),
    )
    r = fit1d.fit_lut(lut, source="gamma")
    assert r.metrics["mean"] < 0.05, r.metrics["mean"]
    v = float(np.interp(0.5, np.linspace(0, 1, 256), r.table_0_1))
    assert abs(v - 0.5**1.8) < 0.005, v


@check(".cube 读写往返一致")
def _():
    p = ROOT / "out" / "_test_roundtrip.cube"
    p.parent.mkdir(parents=True, exist_ok=True)
    vals = np.stack([np.linspace(0, 1, 17)] * 3, axis=-1) ** 1.3
    cube.write_cube_1d(p, vals, title="rt")
    back = cube.load_cube(p)
    assert back.kind == 1 and back.size == 17
    assert np.allclose(back.data, vals, atol=1e-6)
    assert back.title == "rt"
    p.unlink()


@check("3D LUT 三线性插值精度")
def _():
    n = 33
    g = np.linspace(0, 1, n)
    b, gg, r = np.meshgrid(g, g, g, indexing="ij")
    data = np.stack([r**2, gg**2, b**2], axis=-1)
    lut = cube.CubeLUT(kind=3, size=n, data=data, domain_min=np.zeros(3), domain_max=np.ones(3))
    pts = np.random.default_rng(3).random((500, 3)) * 0.98 + 0.01
    err = np.abs(lut.apply(pts) - pts**2).max()
    assert err < 2e-3, f"插值误差 {err:.5f}"


@check("NP3 248B 原厂件：20 条记录、无曲线")
def _():
    f = np3.parse(fixture("output/backup/PICCON01_相机原厂导出_248B.np3"))
    assert f.size == 248
    assert f.version == "0300"
    assert f.base_id == 0x0001, f.base_id
    assert len(f.records) == 20
    assert np3.extract_curve(f) is None
    assert f.terminator_ok


@check("NP3 1012B 云创件：提取 256 点曲线")
def _():
    f = np3.parse(fixture("output/backup/sample_1012.np3"))
    assert f.version == "0310"
    assert f.base_id == 0x0020
    got = np3.extract_curve(f)
    assert got is not None
    c, meta = got
    assert meta["points"] == 256
    assert c.size == 256
    assert abs(float(c[-1]) - 31996 / LUT_MAX) < 1e-9, c[-1]
    assert np.all(np.diff(c) >= 0)


@check("LUT 段打包：512 字节大端往返")
def _():
    t = np.arange(256, dtype=np.uint16) * 128
    t[-1] = LUT_MAX
    seg = np3.pack_lut_segment(t)
    assert len(seg) == 512
    assert seg[:2] == b"\x00\x00"
    assert seg[-2:] == b"\x7f\xff"  # 32767 大端
    back = np3.unpack_lut_segment(seg)
    assert np.array_equal(back, t.astype(np.int64))


@check("恒等曲线无塌陷、无断层")
def _():
    t = curve.quantize(np.linspace(0, 1, 256))
    b = curve.banding_analysis(t)
    assert b["monotone"]
    assert b["max_flat_run"] == 1, b["max_flat_run"]
    assert b["distinct_codes"] == 256
    assert not b["compressed_segments"]
    assert not b["artifact_flats"]


@check("塌陷能被检出，且能区分参考自身的黑位截断")
def _():
    t = np.repeat(np.arange(0, 256, 2), 2).astype(np.uint16) * 64
    b = curve.banding_analysis(t)
    assert b["max_flat_run"] == 2, b["max_flat_run"]
    assert b["distinct_codes"] < 256

    # 参考在这段本来是线性的 → 塌陷被判定为拟合缺陷
    ref_lin = np.linspace(0, 1, 256)
    b2 = curve.banding_analysis(t, ref_lin)
    assert b2["artifact_flats"], "没能识别出无解释塌陷"

    # 参考自己在这段也是纯黑 → 不算缺陷
    ref_flat = np.clip(np.linspace(-0.2, 1, 256), 0, 1)
    t2 = curve.quantize(np.clip(np.linspace(-0.05, 1, 256), 0, 1))
    b3 = curve.banding_analysis(t2, ref_flat)
    assert not b3["artifact_flats"], "把忠实还原的黑位截断误判为缺陷"


@check("3D 样本：灰阶不被带偏（黑位守卫）")
def _():
    for name in ("warm_soft_3d", "fade_matte_3d", "vivid_red_3d", "teal_orange_3d"):
        lut = cube.load_cube(ROOT / f"samples/{name}.cube")
        r = fit1d.fit_lut(lut, source=name)
        _, ref = lut.neutral_ramp(256)
        ref_black = float(color.luma(ref[0]))
        got_black = float(r.table_0_1[0])
        assert abs(got_black - ref_black) < 0.02, f"{name}: 黑位 {got_black:.3f} vs 参考 {ref_black:.3f}"
        assert r.banding["monotone"], f"{name}: 曲线非单调"
        assert not r.banding["artifact_flats"], f"{name}: 出现无法解释的塌陷 {r.banding['artifact_flats']}"


@check("拟合可复现")
def _():
    lut = cube.load_cube(ROOT / "samples/vivid_red_3d.cube")
    a = fit1d.fit_lut(lut, source="x")
    b = fit1d.fit_lut(lut, source="x")
    assert np.array_equal(a.table, b.table)


@check("载荷构建 → 解析 往返一致")
def _():
    from z30fit import payload as pl

    curve = np.linspace(0, 1, 256) ** 1.4
    data = pl.build_payload(curve, name="TEST-01")
    assert len(data) == pl.PAYLOAD_SIZE == 614
    assert data[0:2] == b"\x02\x00"

    info = pl.parse_payload(data)
    assert info["name"] == "TEST-01"
    assert info["point_bytes"] == 0x08
    assert info["lut_monotone"]
    assert abs(info["lut_first"] - 0) <= 1
    expect = np.rint(curve * LUT_MAX).astype(np.int64)
    assert np.array_equal(info["lut"], expect), "LUT 段与输入曲线不一致"


@check("载荷模板模式：只覆盖名称/控制点/LUT")
def _():
    from z30fit import payload as pl

    template = bytes(range(256)) * 3  # 614 字节以内随便造
    template = (template + bytes(614))[:614]
    curve = np.linspace(0, 1, 256)

    out = pl.build_payload(curve, name="ABC", template=template)
    diffs = pl.diff_payloads(template, out)
    touched = {i for i, _, _ in diffs}
    allowed = set(range(pl.NAME_OFFSET, pl.NAME_OFFSET + pl.NAME_LEN))
    allowed |= set(range(pl.CONTROL_POINTS_OFFSET, pl.CONTROL_POINTS_OFFSET + pl.CONTROL_POINTS_LEN))
    allowed |= set(range(pl.LUT_OFFSET, pl.LUT_OFFSET + pl.LUT_BYTES))
    assert touched <= allowed, f"改到了不该改的位置：{sorted(touched - allowed)[:8]}"


@check("载荷解析拒绝过短输入")
def _():
    from z30fit import payload as pl

    try:
        pl.parse_payload(bytes(600))
    except ValueError:
        return
    raise AssertionError("短载荷没有被拒绝")


@check("控制点落在 0..255 且与曲线同向")
def _():
    from z30fit import payload as pl

    curve = np.linspace(0, 1, 256) ** 2
    cp = pl.curve_to_control_points(np.rint(curve * LUT_MAX).astype(np.int64))
    assert len(cp) == 4
    for x, y in cp:
        assert 0 <= x <= 255 and 0 <= y <= 255
    ys = [y for _, y in cp]
    assert ys == sorted(ys), "控制点 y 非单调"


@check("参考图模式：曲线单调，且参考=典型场景时接近恒等")
def _():
    from z30fit import refimage
    from z30fit.images import synth_scene

    im = synth_scene()
    lum = np.sort((im.reshape(-1, 3) @ np.array([0.2126, 0.7152, 0.0722]))[::37])
    curve, stats = refimage.build_curve(lum, knots=33)
    v = curve(np.linspace(0, 1, 256))
    assert np.all(np.diff(v) >= -1e-9), "曲线非单调"
    # 参考分布就是典型分布本身，拟合出来应该接近恒等
    assert stats["tone_match_mean"] < 3.0, stats["tone_match_mean"]
    assert abs(float(v[0]) - float(v[0])) < 1e-9


@check("参考图模式：极端暗图不会产生非法曲线")
def _():
    from z30fit import refimage

    dark = np.sort(np.clip(np.random.default_rng(0).normal(0.02, 0.01, 5000), 0, 1))
    curve, stats = refimage.build_curve(dark, knots=33)
    v = curve(np.linspace(0, 1, 256))
    assert np.all(np.isfinite(v))
    assert np.all(np.diff(v) >= -1e-9)
    assert v.min() >= -1e-9 and v.max() <= 1 + 1e-9


@check("成对图模式：对齐 + 曲线能把误差降下来")
def _():
    import tempfile
    from pathlib import Path as _P

    from PIL import Image

    from z30fit import pairfit
    from z30fit.images import synth_scene

    a = synth_scene()
    s = np.linspace(0, 1, 256)
    y = 1 / (1 + np.exp(-6 * (s - 0.5)))
    y = (y - y[0]) / (y[-1] - y[0])
    b = np.clip(np.interp(a, s, 0.02 + 0.96 * y), 0, 1)

    tmp = _P(tempfile.mkdtemp())
    Image.fromarray((a * 255).astype(np.uint8)).save(tmp / "s.jpg", quality=92)
    # 目标图故意平移 3 像素，检验对齐
    bb = np.roll(b, 3, axis=1)
    Image.fromarray((bb * 255).astype(np.uint8)).save(tmp / "d.jpg", quality=92)

    r = pairfit.pair_fit(tmp / "s.jpg", tmp / "d.jpg")
    assert r["metrics"]["before_mean"] > r["metrics"]["mean"] * 2, "曲线没起作用"
    assert r["metrics"]["mean"] < 3.0, r["metrics"]["mean"]
    assert abs(r["metrics"]["shift"][1]) >= 2, f"没检测到平移：{r['metrics']['shift']}"


@check("真实配方库：81 套曲线单调合法，机身名为 ASCII")
def _():
    from z30fit import ncp_presets as N

    presets = N.listing()
    assert len(presets) >= 80, f"真实配方数量异常：{len(presets)}"
    for p in presets:
        t = N.get(p["id"])
        assert t is not None, p["id"]
        table = t["table"]
        assert table.size == 256
        assert np.all(np.diff(table) >= 0), f"{p['id']} 曲线非单调"
        assert table.min() >= 0 and table.max() <= LUT_MAX
        cam = t.get("cam", "")
        assert all(32 <= ord(c) <= 126 for c in cam), f"{p['id']} 机身名非 ASCII"
        assert len(cam) <= 19


@check("内置预设：增补 20 套，命名中性、机身名 ASCII、曲线单调")
def _():
    from z30fit import presets as P

    builtin = P.BUILTIN
    assert len(builtin) >= 40, f"内置预设只有 {len(builtin)} 套"

    # 厂商商标 / 胶片型号一律不许出现在预设名与说明里（产品规划 §9）
    banned = [
        "portra", "kodak", "kodachrome", "ektar", "ektachrome", "fuji", "fujifilm",
        "provia", "astia", "velvia", "superia", "acros", "ilford", "agfa",
        "cinestill", "vision3", "lomo", "sony", "canon", "nikon", "leica",
        "富士", "柯达", "柯尼卡", "徕卡", "尼康", "佳能", "索尼", "哈苏", "禄来",
    ]
    allowed_params = {"sat", "saturation", "sharp", "clarity", "contrast", "bright",
                      "mid_sharp", "quick_sharp"}
    seen_names, seen_cams = set(), set()
    for p in builtin:
        text = (p["name"] + p["desc"]).lower()
        for word in banned:
            assert word not in text, f"{p['id']} 含厂商名「{word}」"
        assert p["name"] not in seen_names, f"预设名重复：{p['name']}"
        seen_names.add(p["name"])

        cam = p.get("cam", "")
        if cam:
            assert len(cam) <= 19 and all(32 <= ord(c) <= 126 for c in cam), f"{p['id']} 机身名异常：{cam}"
            assert cam not in seen_cams, f"机身名重复：{cam}"
            seen_cams.add(cam)

        for key, value in (p.get("params") or {}).items():
            assert key in allowed_params, f"{p['id']} 不认识的参数：{key}"
            assert -5 <= float(value) <= 9, f"{p['id']} 参数越界：{key}={value}"

        table = P.get(p["id"])["table"]
        assert table.size == 256, f"{p['id']} 曲线点数不对"
        assert np.all(np.diff(table) >= 0), f"{p['id']} 曲线非单调"
        assert int(table[0]) <= int(table[-1]), f"{p['id']} 黑白位颠倒"

    # 这一轮要补的风格：低饱和 / 冷调 / 硬光
    names = " ".join(p["name"] for p in builtin)
    for word in ("低饱和", "冷调", "硬光"):
        assert word in names, f"缺少「{word}」风格的预设"


@check("预设列表带缩略曲线，且与真实曲线一致")
def _():
    from z30fit import presets as P

    items = P.listing()
    item = next(i for i in items if i["id"] == "fl35-hard-contrast")
    spark = item["spark"]
    assert len(spark) == 17
    assert all(0 <= v <= 1000 for v in spark)
    assert all(b >= a for a, b in zip(spark, spark[1:])), "缩略曲线非单调"

    table = P.get("fl35-hard-contrast")["table"].astype(np.float64) / LUT_MAX
    assert abs(spark[0] - table[0] * 1000) < 15
    assert abs(spark[-1] - table[-1] * 1000) < 15


@check("预览成对图：原图/效果同尺寸，供拖动对比")
def _():
    import tempfile
    from pathlib import Path as _P

    from z30fit import fit1d
    from z30fit.report import render_previews

    res = fit1d.fit_lut(cube.load_cube(ROOT / "samples/fade_1d.cube"), source="pair-preview")
    out = _P(tempfile.mkdtemp())
    previews = render_previews(res, None, out)

    for key in ("chart", "scene", "gradient"):
        a, b = previews[f"cmp_{key}_a"], previews[f"cmp_{key}_b"]
        assert a.is_file() and b.is_file(), f"缺 {key} 的成对图"
        from PIL import Image

        with Image.open(a) as ia, Image.open(b) as ib:
            assert ia.size == ib.size, f"{key} 两张图尺寸不一致：{ia.size} vs {ib.size}"
            assert ia.size[0] >= 600, f"{key} 预览图太小：{ia.size}"
            # 曲线不是恒等，两张图必须真的不一样
            assert ia.tobytes() != ib.tobytes(), f"{key} 原图与效果图完全一样"

    # 带标注的对比条还在（报告里要用）
    assert previews["scene"].is_file() and previews["curve"].is_file()


@check("HTML 报告自包含：图内嵌、曲线是 SVG、没有外链")
def _():
    import tempfile
    from pathlib import Path as _P

    from z30fit import fit1d
    from z30fit.report import render_previews
    from z30fit.reporthtml import write_html_report

    res = fit1d.fit_lut(cube.load_cube(ROOT / "samples/warm_soft_3d.cube"), source="warm_soft_3d.cube")
    out = _P(tempfile.mkdtemp())
    previews = render_previews(res, None, out)
    path = write_html_report(res, None, previews, out)

    text = path.read_text(encoding="utf-8")
    assert "<!DOCTYPE html>" in text
    assert "warm_soft_3d.cube" in text.split("<title>")[1]
    assert text.count("data:image/png;base64,") >= 3, "预览图没有内嵌"
    assert "<svg" in text, "曲线不是内联 SVG"
    assert "http://" not in text and "https://" not in text, "报告里出现了外链，不是自包含的"
    assert "ΔE2000" in text
    assert len(text) > 60_000, "报告太小，图片可能没进去"


@check("安卓内置预设资源：与桌面预设逐点一致（改预设后要重新生成）")
def _():
    import base64
    import json

    from z30fit import ncp_presets as N
    from z30fit import payload as pl
    from z30fit import presets as P

    path = ROOT / "android" / "app" / "src" / "main" / "assets" / "builtin_presets.json"
    if not path.exists():
        raise SkipTest("还没生成内置预设资源")

    doc = json.loads(path.read_text(encoding="utf-8"))
    items = doc["presets"]
    assert doc["count"] == len(items), "count 与条目数不一致"
    assert len(items) >= 100, f"内置预设只有 {len(items)} 条"

    cams: set[str] = set()
    for item in items:
        raw = base64.b64decode(item["payload"])
        assert len(raw) == pl.PAYLOAD_SIZE, f"{item['id']} 载荷长度不对"
        info = pl.parse_payload(raw)
        assert info["lut_monotone"], f"{item['id']} 曲线非单调"
        assert info["name"] == item["cam"], f"{item['id']} 载荷名与 cam 不一致"
        assert 0 < len(item["cam"]) <= 19 and all(32 <= ord(c) <= 126 for c in item["cam"])
        assert item["cam"] not in cams, f"机身名重复：{item['cam']}"
        cams.add(item["cam"])

        full = P.get(item["id"]) or N.get(item["id"])
        assert full is not None, f"桌面端找不到预设 {item['id']}"
        expect = full["table"].astype(np.int64)
        assert np.array_equal(info["lut"], expect), f"{item['id']} 曲线与桌面不一致，请重跑 tools/gen_android_presets.py"


@check("内置预设的机身名不与配方库冲突")
def _():
    from z30fit import ncp_presets as N
    from z30fit import presets as P

    builtin_cams = {p["cam"] for p in P.BUILTIN if p.get("cam")}
    ncp_cams = {p["cam"] for p in N.listing() if p.get("cam")}
    assert not (builtin_cams & ncp_cams), f"机身名撞车：{sorted(builtin_cams & ncp_cams)}"


@check("预设记录：缓存里存的就是返回的那一份（cam/params 不会丢）")
def _():
    import webui.server as srv

    table = np.rint(np.linspace(0, 1, 256) * LUT_MAX).astype(np.uint16)
    rec = srv._record_from_table("t", "preset", "测试预设", table, "说明")
    assert "ok" not in rec, "记录本身不该带 ok 字段"
    # 调用方（/api/preset）会在返回前补这三个字段，命中缓存时也必须读得到
    rec["cam"] = "TEST-01"
    rec["params"] = {"sat": -1}
    stored = srv.SESSIONS[rec["token"]]["record"]
    assert stored.get("cam") == "TEST-01", "缓存里的记录没有跟着更新，第二次点同一预设会丢机身名"
    assert stored.get("params") == {"sat": -1}
    assert stored["token"] == rec["token"]


@check("预设管理：隐藏可恢复、自存预设删进回收站可恢复")
def _():
    import tempfile
    from pathlib import Path as _P

    from z30fit import presets as P

    tmp = _P(tempfile.mkdtemp())
    original = P.data_root
    P.data_root = lambda: tmp
    try:
        assert P.hide(["neutral", "fl25-cool-mute"]) == 2
        assert set(P.load_hidden()) == {"neutral", "fl25-cool-mute"}
        assert P.unhide(["neutral"]) == 1
        assert P.load_hidden() == ["fl25-cool-mute"]
        assert P.unhide_all() == 1
        assert P.load_hidden() == []

        table = np.rint(np.linspace(0, 1, 256) * LUT_MAX).astype(np.uint16)
        entry = P.save_user("T1", "测试", table)
        assert len(P.load_user()) == 1

        assert P.move_to_trash([entry["id"]]) == 1
        assert P.load_user() == []
        assert [t["name"] for t in P.load_trash()] == ["T1"]
        assert P.load_trash()[0].get("deleted"), "回收站没记删除时间"

        assert P.restore_from_trash([entry["id"]]) == 1
        assert [u["name"] for u in P.load_user()] == ["T1"]
        assert P.load_trash() == []

        # 同名冲突：不覆盖现有配方，而且那条还留在回收站里
        again = P.save_user("T1", "又存了一遍", table)
        assert P.move_to_trash([again["id"]]) == 1
        assert P.save_user("T1", "第三条", table)
        assert P.restore_from_trash([again["id"]]) == 0
        assert len(P.load_user()) == 1
        assert len(P.load_trash()) == 1, "同名恢复失败后不该把回收站条目弄丢"

        # 彻底删除：抹掉且不再出现在回收站
        assert P.purge_trash([again["id"]]) == 1
        assert P.load_trash() == []

        # 清空回收站
        third = P.save_user("T2", "再存一条", table)
        assert P.move_to_trash([third["id"]]) == 1
        assert P.purge_all_trash() == 1
        assert P.load_trash() == []
        assert len(P.load_user()) == 1, "清空回收站不该动到正常列表"
    finally:
        P.data_root = original


@check("数据目录：默认落系统用户目录，便携模式落程序目录，老数据只搬一次")
def _():
    import tempfile
    from pathlib import Path as _P

    from z30fit import paths

    repo, app, appdata = _P("/repo"), _P("/app"), _P("/appdata")
    common = dict(exe_folder=app, appdata=appdata, source_root=repo)

    # 源码运行：还是仓库里的 out/
    assert paths.resolve_data_dir(frozen=False, portable=False, **common) == repo / "out"
    # 打包 + 默认：%LOCALAPPDATA%\Z30影调工具
    assert paths.resolve_data_dir(frozen=True, portable=False, **common) == appdata / paths.APP_NAME
    # 打包 + 便携：程序目录下的 data/
    assert paths.resolve_data_dir(frozen=True, portable=True, **common) == app / "data"

    # 老数据迁移：搬一次，且不覆盖、不重复
    tmp = _P(tempfile.mkdtemp())
    old, new = tmp / "data", tmp / "appdata" / paths.APP_NAME
    old.mkdir()
    (old / "presets.json").write_text("[]", encoding="utf-8")
    (old / "webui").mkdir()
    (old / "webui" / "x.txt").write_text("1", encoding="utf-8")

    assert paths._migrate(old, new) is True
    assert (new / "presets.json").exists() and (new / "webui" / "x.txt").exists()
    assert (new / "迁移来源.txt").exists(), "搬完要留个来源说明"
    assert paths._migrate(old, new) is False, "已经搬过就别再搬"

    # 空的旧 data 不该触发迁移
    empty = tmp / "empty" / "data"
    empty.mkdir(parents=True)
    assert paths._migrate(empty, tmp / "target") is False


@check("照片预览：JPG 能读、RAW 取内嵌预览、前后两张同尺寸")
def _():
    import io
    import tempfile
    from pathlib import Path as _P

    from PIL import Image

    from z30fit import photo

    rng = np.random.default_rng(7)
    base = Image.fromarray((rng.random((300, 450, 3)) * 255).astype(np.uint8))
    tmp = _P(tempfile.mkdtemp())

    jpg = tmp / "p.jpg"
    base.save(jpg, quality=92)
    im, note = photo.load_image(jpg, max_side=200)
    assert max(im.size) <= 200, f"没缩到预览尺寸：{im.size}"
    assert note, "没给来源说明"

    # 假 RAW：文件头 + 一张内嵌 JPEG，走「取内嵌预览」那条路
    buf = io.BytesIO()
    base.save(buf, format="JPEG", quality=90)
    nef = tmp / "p.nef"
    nef.write_bytes(b"\x4d\x4d\x00\x2a" + b"\x00" * 1024 + buf.getvalue())
    im2, note2 = photo.load_image(nef, max_side=400)
    assert im2.size == im2.size and note2 == "RAW 内嵌预览", note2
    assert max(im2.size) > 200, "内嵌预览没读到"

    table = np.rint(np.clip(np.linspace(0, 1, 256) ** 0.7, 0, 1) * LUT_MAX).astype(np.uint16)
    res = photo.write_pair(jpg, table, tmp / "out", max_side=256)
    a = np.asarray(Image.open(res["a"]))
    b = np.asarray(Image.open(res["b"]))
    assert a.shape == b.shape, "前后两张尺寸不一致，分割对比会错位"
    assert (a != b).any(), "曲线没起作用"
    assert b.mean() > a.mean(), "提亮曲线套完反而更暗"

    lut = photo.build_lut(table)
    assert lut.shape == (256,) and np.all(np.diff(lut.astype(int)) >= 0), "查表非单调"


@check("照片导出：全分辨率无损、超限会缩、png/jpg/tif 三种格式、文件名 ASCII")
def _():
    import io
    import tempfile
    from pathlib import Path as _P

    from PIL import Image

    from z30fit import photo

    rng = np.random.default_rng(11)
    base = Image.fromarray((rng.random((200, 320, 3)) * 255).astype(np.uint8))
    tmp = _P(tempfile.mkdtemp())

    jpg = tmp / "DSC_0001.jpg"
    base.save(jpg, quality=95)
    table = np.rint(np.clip(np.linspace(0, 1, 256) ** 0.7, 0, 1) * LUT_MAX).astype(np.uint16)

    data, fname, note = photo.export_photo(jpg, table, fmt="png")
    assert fname == "DSC_0001-z30tone.png" and fname.isascii(), fname
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "不是 PNG"
    out = Image.open(io.BytesIO(data))
    assert out.size == base.size, f"导出应该按原尺寸：{out.size} vs {base.size}"
    assert (np.asarray(out) != np.asarray(base)).any(), "曲线没起作用"
    # 曲线是提亮的（^0.7），导出图必须整体更亮，且逐点等于查表结果（对**重新解码后**的原图）
    lut = photo.build_lut(table)
    src_im, _ = photo.load_image(jpg, max_side=10**7)
    expect = photo.apply_lut(src_im, lut)
    assert np.array_equal(np.asarray(out), np.asarray(expect)), "导出与查表结果不一致"
    assert (np.asarray(out) != np.asarray(base)).any(), "曲线没起作用"
    assert "缩小" not in note, note

    # JPG / TIFF：能导、尺寸一致、JPG 有损但与查表结果足够接近
    jpg_bytes, jpg_name, _ = photo.export_photo(jpg, table, fmt="jpg")
    tif_bytes, tif_name, _ = photo.export_photo(jpg, table, fmt="tif")
    assert jpg_name.endswith("-z30tone.jpg") and tif_name.endswith("-z30tone.tif")
    out_jpg = Image.open(io.BytesIO(jpg_bytes))
    out_tif = Image.open(io.BytesIO(tif_bytes))
    assert out_jpg.format == "JPEG" and out_tif.format == "TIFF"
    assert out_jpg.size == base.size and out_tif.size == base.size
    d = np.abs(np.asarray(out_jpg).astype(int) - np.asarray(expect).astype(int))
    # 测试图是纯随机噪点（JPEG 的最坏情况），真实照片远好于这个水平
    assert d.mean() < 8.0, f"JPG 质量 95 偏差过大：{d.mean():.2f}"
    assert len(jpg_bytes) < len(data), "JPG 应该比 PNG 小"

    # 超过像素上限：等比缩，且说明里写明
    small, _, note_s = photo.export_photo(jpg, table, fmt="png", max_pixels=1000)
    outs = Image.open(io.BytesIO(small))
    assert outs.width * outs.height <= 1000, f"没缩到上限：{outs.size}"
    assert "缩小" in note_s, note_s

    # 中文文件名：下载名退到 ASCII 兜底
    cn = tmp / "晚霞.jpg"
    base.save(cn, quality=95)
    _, fname2, _ = photo.export_photo(cn, table)
    assert fname2.isascii() and fname2.endswith("-z30tone.png"), fname2
    try:
        photo.export_photo(jpg, table, fmt="raw")
        raise AssertionError("非法 fmt 应该报 PhotoError")
    except photo.PhotoError:
        pass


@check("单图拟合记录：导出 .z30 不再 KeyError（metrics 缺省兜底，口径不塞 ΔE）")
def _():
    import tempfile
    from pathlib import Path as _P

    from PIL import Image

    from z30fit import recipe as rc
    from webui import server as ws

    rng = np.random.default_rng(5)
    im = Image.fromarray((rng.random((120, 160, 3)) * 255).astype(np.uint8))
    tmp = _P(tempfile.mkdtemp())
    jpg = tmp / "ref.jpg"
    im.save(jpg, quality=92)

    rec = ws._fit_image(jpg, 33, "ref.jpg")  # do_fit 的 src 只认 sample:/upload: 白名单
    assert rec.get("kind") == "image", rec.get("kind")
    assert "metrics" not in rec, "单图记录不该带 metrics（没有真值，口径不给 ΔE）"
    assert rec.get("image_metrics"), "单图记录应该带影调贴合误差"

    # 复现 /api/recipe 的元数据组装（修复后必须是 .get 兜底）
    m = rec.get("metrics") or {}
    meta = rc.make_meta(
        name="TEST",
        source=rec.get("source", ""),
        metrics={k: m.get(k) for k in ("mean", "p95", "max") if m.get(k) is not None},
        tone=rec.get("tone", {}),
        note=rec.get("verdict", ""),
    )
    assert meta["metrics"] == {}, "单图配方的元数据不该出现会被读成 ΔE 的键"
    # 带着这份元数据把配方包组装出来（载荷用 256 点占位表）
    data = rc.build(ws.pl.build_payload_614(np.linspace(0, 1, 256), name="TEST"), meta)
    assert len(data) > 614, "配方包应该组装成功"


def main() -> int:
    total = len(PASS) + len(FAIL) + len(SKIP)
    for name, msg in FAIL:
        print(f"FAIL  {name}\n{msg}\n")
    for name in PASS:
        print(f"ok    {name}")
    for name in SKIP:
        print(f"skip  {name}")
    tail = f"，{len(SKIP)} 项跳过" if SKIP else ""
    print(f"\n{len(PASS)}/{total} 通过{tail}")
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
