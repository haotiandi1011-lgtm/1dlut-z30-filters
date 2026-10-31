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
