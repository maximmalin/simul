"""Constructor drawing (engineering drawing) smoke tests."""
import sys, os, json, shutil
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "backend"))

import pytest

from drawing_sheet import (hole_callout_text, _tol, _fmt, _positive, _offset,
                           SHEET_SIZES, PROJECTIONS, Sheet, View, load_geometry,
                           render, make_sheet_from_step)

ROOT = Path(__file__).resolve().parents[3]
FREECAD = shutil.which("freecadcmd")

# ---------------------------------------------------------------------------
# чистые юнит-тесты (без FreeCAD)
# ---------------------------------------------------------------------------


def test_hole_callout_text_thru():
    spec = {"count": 4, "diameter": 6.6, "depth": None, "thru": True,
            "cbore": None, "csk": None, "thread": None, "label": None}
    assert hole_callout_text(spec) == "4× ⌀6.6 THRU"


def test_hole_callout_text_depth():
    spec = {"count": None, "diameter": 6.6, "depth": 12, "thru": False,
            "cbore": None, "csk": None, "thread": None, "label": None}
    assert hole_callout_text(spec) == "⌀6.6 ↧12"


def test_hole_callout_text_cbore_csk_thread():
    spec = {"count": 2, "diameter": 5.0, "depth": None, "thru": True,
            "cbore": (9.0, 3.0), "csk": (10.0, 90.0), "thread": "M6x1 - 6H",
            "label": "WHEEL"}
    txt = hole_callout_text(spec, tol=0.1, fit="H7")
    assert "2×" in txt
    assert "M6x1 - 6H" in txt
    assert "THRU" in txt
    assert "CB ⌀9 ↧3" in txt
    assert "CS ⌀10 × 90°" in txt
    assert "WHEEL" in txt
    assert "±0.1" in txt
    assert "H7" in txt


def test_tol_normalisation():
    assert _tol(0.1) == (0.1, 0.1)
    assert _tol((0.05, 0.02)) == (0.05, 0.02)
    assert _tol(None) is None
    with pytest.raises(TypeError):
        _tol(True)
    with pytest.raises(TypeError):
        _tol("x")


def test_fmt():
    assert _fmt(150.0) == "150"
    assert _fmt(6.6) == "6.6"
    assert _fmt(0.1) == "0.1"


def test_positive_validation():
    assert _positive("d", 5) == 5.0
    with pytest.raises(ValueError):
        _positive("d", 0)
    with pytest.raises(ValueError):
        _positive("d", -1)
    with pytest.raises(ValueError):
        _positive("d", float("inf"))


def test_offset_validation():
    assert _offset(None) is None
    assert _offset(5) == 5.0
    with pytest.raises(ValueError):
        _offset(float("nan"))


def test_sheet_validation_projection():
    with pytest.raises(ValueError):
        Sheet(projection="SIDE ANGLE")


def test_sheet_refuses_bare_string_notes():
    with pytest.raises(TypeError):
        Sheet(notes="BREAK EDGES")


def test_sheet_refuses_bare_string_revisions():
    with pytest.raises(TypeError):
        Sheet(revisions="A")


def test_sheet_refuses_bad_revision_row():
    with pytest.raises(ValueError):
        Sheet(revisions=[("A", "2026-01-01")])  # not triple


def test_view_name_validation():
    s = Sheet()
    with pytest.raises(ValueError):
        s.view({}, "diagonal", at=(10, 10))


def test_view_at_finite():
    s = Sheet()
    with pytest.raises(ValueError):
        s.view({}, "top", at=(float("nan"), 5))


def test_dim_orientation_validation():
    s = Sheet()
    v = View(s, {}, "top", (10, 10))
    with pytest.raises(ValueError):
        v.dim((0, 0, 0), (1, 1, 0), orientation="x")


def test_hole_thru_and_depth_exclusive():
    s = Sheet()
    v = View(s, {}, "top", (10, 10))
    with pytest.raises(ValueError):
        v.hole((0, 0, 0), 5, depth=3, thru=True)


def test_hole_callout_count_positive():
    s = Sheet()
    v = View(s, {}, "top", (10, 10))
    with pytest.raises(ValueError):
        v.hole((0, 0, 0), 5, count=0)


def test_angle_rejects_collinear():
    s = Sheet()
    v = View(s, {}, "top", (10, 10))
    with pytest.raises(ValueError):
        v.angle((0, 0, 0), (1, 0, 0), (2, 0, 0))


def test_note_offset_finite():
    s = Sheet()
    v = View(s, {}, "top", (10, 10))
    with pytest.raises(ValueError):
        v.note("hi", (0, 0, 0), offset=(float("inf"), 1))


def test_standard_scales_present():
    assert "A4" in SHEET_SIZES and "A3" in SHEET_SIZES
    assert SHEET_SIZES["A4"] == (297.0, 210.0)


# ---------------------------------------------------------------------------
# интеграционные (требуют freecadcmd)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(FREECAD is None, reason="freecadcmd not found")
def test_load_geometry_band_a():
    g = load_geometry(ROOT / "hardware/antenna/geometry/band_a.step")
    assert g["bbox"][0] == 150.0
    assert g["bbox"][1] == 150.0
    assert "top" in g["views"]
    assert "front" in g["views"]
    assert "iso" in g["views"]
    # скрытые линии присутствуют
    assert isinstance(g["views"]["top"]["hidden"], list)
    # видимые линии присутствуют
    assert len(g["views"]["top"]["visible"]) > 0


@pytest.mark.skipif(FREECAD is None, reason="freecadcmd not found")
def test_render_produces_pdf_and_svg(tmp_path):
    out_pdf = tmp_path / "band_a_GOST.pdf"
    out_svg = tmp_path / "band_a_GOST.svg"
    s = make_sheet_from_step(ROOT / "hardware/antenna/geometry/band_a.step",
                             title="Антенна A", part_number="ANT-A", material="FR4",
                             general_tolerance="ISO 2768-m", iso=True, size="A2")
    ws = render(s, out_pdf, out_svg)
    assert out_pdf.exists() and out_pdf.stat().st_size > 1000
    assert out_svg.exists() and out_svg.stat().st_size > 1000
    svg = out_svg.read_text(encoding="utf-8")
    assert "<svg" in svg
    # измеренные габариты присутствуют (150)
    assert "150" in svg
    # основная надпись
    assert "Антенна A" in svg
    assert "Масштаб" in svg


@pytest.mark.skipif(FREECAD is None, reason="freecadcmd not found")
def test_render_deterministic(tmp_path):
    out1 = tmp_path / "a.pdf"
    out2 = tmp_path / "b.pdf"
    s1 = make_sheet_from_step(ROOT / "hardware/antenna/geometry/band_a.step",
                              title="Антенна A", part_number="ANT-A", material="FR4",
                              general_tolerance="ISO 2768-m", iso=True, size="A2")
    s2 = make_sheet_from_step(ROOT / "hardware/antenna/geometry/band_a.step",
                              title="Антенна A", part_number="ANT-A", material="FR4",
                              general_tolerance="ISO 2768-m", iso=True, size="A2")
    render(s1, out1)
    render(s2, out2)
    assert out1.read_bytes() == out2.read_bytes()


@pytest.mark.skipif(FREECAD is None, reason="freecadcmd not found")
def test_blank_paper_check_fires():
    s = make_sheet_from_step(ROOT / "hardware/antenna/geometry/band_a.step",
                             title="T", part_number="T", material="FR4", iso=True, size="A2")
    v = s.views[0]
    # размер между точками вне геометрии
    v.dim((1000, 1000, 0), (2000, 2000, 0))
    ws = render(s, Path("/tmp/_bp.pdf"), None)
    assert any("measures blank paper" in w or "lands outside" in w for w in ws)


@pytest.mark.skipif(FREECAD is None, reason="freecadcmd not found")
def test_off_frame_guard_warns_on_huge_part():
    # A0 sheet would fit; tiny A4 with 150mm part should warn
    s = make_sheet_from_step(ROOT / "hardware/antenna/geometry/band_a.step",
                             title="T", part_number="T", material="FR4", iso=True, size="A4")
    ws = render(s, Path("/tmp/_of.pdf"), None)
    assert any("runs off the frame" in w for w in ws)
