"""Дальность приёма (link budget) и пеленгация — тесты по требованиям.

Проверяется:
    * энергетика тракта приёма: шумовая дорожка, MDS (REQ-36/REQ-37),
      максимальная дальность радиовидимости по каналам A/B/C;
    * «дальность приёма» по разделам требований (requirements/reception_range.md);
    * фазовый интерферометр: Δφ = 2π·d·sinθ/λ, θ = asin(λΔφ/2πd),
      зона однозначности, СКО пеленга, усиление решётки;
    * обмен с AntennaSim/NEC-2: генерация .nec и wires-JSON.

Запуск:
    .venv/bin/pytest tests/requirements/reception/ -v
"""

import json
import math
from pathlib import Path

import pytest

import direction_finding as DF
import link_budget as LB

ROOT = Path(__file__).resolve().parents[3]


# ===========================================================================
# Дальность приёма — энергетика тракта
# ===========================================================================

def test_noise_floor_matches_req37():
    """Шумовая дорожка 2.7 кГц, NF 3.5 дБ ≈ −136 дБм (≤ −120 дБм по REQ-37)"""
    n = LB.noise_floor_dbm(LB.BW_SSB_HZ, LB.NF_DB)
    assert math.isclose(n, -136.16, abs_tol=0.2), f"N = {n:.2f} дБм"
    assert n <= LB.NOISE_FLOOR_MAX_DBM


def test_sensitivity_mds_req37():
    """MDS = N + SNR (10 дБ) ≤ −110 дБм (REQ-37)"""
    s = LB.sensitivity_dbm(LB.BW_SSB_HZ, LB.NF_DB, LB.SNR_DETECT_DB)
    assert math.isclose(s, -126.16, abs_tol=0.2)
    assert s <= LB.MDS_DBM


def test_fspl_sanity():
    """FSPL(1 км, 200 МГц) ≈ 78.5 дБ; растёт с частотой и дальностью"""
    a = LB.fspl_db(1000.0, 200e6)
    assert math.isclose(a, 78.47, abs_tol=0.1), f"FSPL = {a:.2f} дБ"
    assert LB.fspl_db(1000.0, 2.45e9) > a
    assert LB.fspl_db(2000.0, 200e6) > a


def test_range_scales_with_power_and_gain():
    """Дальность ∝ √(EIRP·G_rx): +10 дБ мощности → ×10^(1/2)... (в дБ → 20·log)"""
    r0 = LB.max_range_m(30.0, 6.0, 2.45e9)
    r1 = LB.max_range_m(40.0, 6.0, 2.45e9)   # +10 дБ EIRP
    # FSPL растёт на 20·log10(R): +10 дБ бюджета → R умножается на 10^(10/20)
    assert math.isclose(r1 / r0, 10 ** 0.5, rel_tol=1e-9)


def test_channel_order_and_positive():
    """Каналы A/B/C дают положительную дальность; A ≥ B ≥ C (по λ и усилению)"""
    rep = LB.build_report(eirp_dbm=30.0, margin_db=20.0)
    chans = {c["key"]: c for c in rep["channels"]}
    assert len(rep["channels"]) == 3
    for c in rep["channels"]:
        assert c["range_free_space_m"] > 0
        assert c["range_realistic_m"] < c["range_free_space_m"]
    assert chans["channel_a"]["range_free_space_m"] > chans["channel_b"]["range_free_space_m"]
    assert chans["channel_b"]["range_free_space_m"] > chans["channel_c"]["range_free_space_m"]


def test_report_noise_pass():
    """Сводный отчёт фиксирует выполнение REQ-36/REQ-37 по шуму"""
    rep = LB.build_report()
    assert rep["noise"]["noise_floor_pass"] is True
    assert rep["noise"]["mds_pass"] is True
    assert rep["reference"]["bw_hz"] == LB.BW_SSB_HZ


def test_render_svg_is_valid_xml(tmp_path):
    """Диаграмма дальности — корректный SVG"""
    import xml.etree.ElementTree as ET

    rep = LB.build_report()
    out = LB.render_svg(rep, tmp_path / "lb.svg")
    assert out.exists()
    ET.parse(out)  # бросит при некорректном XML


# ===========================================================================
# «Дальность приёма» по разделам требований
# ===========================================================================

def test_reception_range_doc_exists():
    """requirements/reception_range.md описывает дальность по разделам"""
    doc = ROOT / "requirements" / "reception_range.md"
    assert doc.exists(), "нет requirements/reception_range.md"
    text = doc.read_text(encoding="utf-8")
    for n in range(1, 9):
        assert f"Раздел {n}" in text, f"нет раздела {n}"


def test_requirements_matrix_has_reception_column():
    """В матрице требований есть столбец дальности приёма"""
    csv = ROOT / "requirements" / "requirements_matrix.csv"
    header = csv.read_text(encoding="utf-8").splitlines()[0]
    assert "Reception_Distance_m" in header


# ===========================================================================
# Пеленгация — фазовый интерферометр
# ===========================================================================

def test_phase_difference_formula():
    """Δφ = 2π·d·sinθ/λ"""
    f = 2.45e9
    d = LB.C0 / f / 2.0
    lam = LB.C0 / f
    for deg in (-60.0, -10.0, 0.0, 25.0, 45.0):
        dphi = DF.phase_difference(math.radians(deg), f, d)
        assert math.isclose(dphi, 2 * math.pi * d * math.sin(math.radians(deg)) / lam,
                            rel_tol=1e-12, abs_tol=1e-12)


def test_doa_round_trip_half_wave():
    """d = λ/2: θ → Δφ → θ̂ восстанавливается точно и однозначно"""
    f = 2.45e9
    d = LB.C0 / f / 2.0
    for deg in (-80.0, -30.0, 0.0, 35.0, 70.0):
        dphi = DF.phase_difference(math.radians(deg), f, d)
        est = DF.doa_from_phase(dphi, f, d)
        assert est is not None
        assert math.isclose(math.degrees(est), deg, abs_tol=1e-9)
    assert DF.unambiguous_fov_deg(f, d) == 90.0


def test_doa_out_of_ambiguity_returns_none():
    """При неразрешимой разности фаз функция возвращает None"""
    f = 2.45e9
    d = LB.C0 / f / 2.0
    assert DF.doa_from_phase(math.pi * 2.0, f, d) is None


def test_array_baseline_ambiguity():
    """Шаг 91.7 мм при 2.45 ГГц (d>λ/2) сужает зону однозначности"""
    f = 2.45e9
    fov = DF.unambiguous_fov_deg(f, 0.0917)
    lam = LB.C0 / f
    assert math.isclose(fov, math.degrees(math.asin(lam / (2 * 0.0917))), rel_tol=1e-9)
    assert fov < 90.0


def test_doa_sigma_scaling():
    """σ_θ ∝ σ_φ и минимально при θ=0 (множитель 1/cosθ)"""
    f = 2.45e9
    d = LB.C0 / f / 2.0
    s0 = DF.doa_sigma_rad(math.radians(5.0), 0.0, f, d)
    s1 = DF.doa_sigma_rad(math.radians(5.0), math.radians(45.0), f, d)
    assert s1 > s0
    assert math.isclose(DF.doa_sigma_rad(math.radians(10.0), 0.0, f, d),
                        2 * s0, rel_tol=1e-9)


def test_array_gain():
    """Усиление решётки из 4 диполей в широкой стороне ≈ G_эл + 10log10(N)"""
    f = 2.45e9
    d = LB.C0 / f / 2.0
    g = DF.array_gain_dbi(f, d, n_elem=4)
    assert math.isclose(g, 2.15 + 10 * math.log10(4), rel_tol=1e-9)


def test_build_scenario_recovers_bearing():
    """Сценарий: пеленг восстанавливает заданный азимут"""
    sc = DF.build_scenario("channel_b", theta_true_deg=35.0, sigma_phi_deg=10.0)
    assert math.isclose(sc["theta_est_deg"], 35.0, abs_tol=1e-6)
    assert sc["sigma_theta_deg"] > 0
    assert sc["fov_deg"] == 90.0
    assert sc["channel"]["key"] == "channel_b"


def test_write_nec_array(tmp_path):
    """NEC-дек содержит по одной карте GW на элемент (совместим с AntennaSim)"""
    f = 2.45e9
    d = LB.C0 / f / 2.0
    p = DF.write_nec_array(tmp_path / "df.nec", f, d, n_elem=4)
    text = p.read_text(encoding="utf-8")
    assert text.count("\nGW ") + text.startswith("GW ") >= 4
    assert "GW" in text and "EN" in text
    wires = DF.wires_json(f, d, n_elem=4)
    assert len(wires) == 4
    assert all("tag" in w and "radius" in w for w in wires)


def test_direction_render_svg_is_valid_xml(tmp_path):
    """Диаграмма «направление + дальность» — корректный SVG"""
    import xml.etree.ElementTree as ET

    sc = DF.build_scenario("channel_b", theta_true_deg=35.0)
    out = DF.render_svg(sc, tmp_path / "df.svg")
    assert out.exists()
    ET.parse(out)


def test_cli_artifacts(tmp_path):
    """CLI: link_budget.py и direction_finding.py пишут json+svg в --out"""
    import subprocess
    import sys

    for script in ("link_budget.py", "direction_finding.py"):
        r = subprocess.run(
            [sys.executable, str(ROOT / "sim" / "scripts" / script), "--out", str(tmp_path)],
            capture_output=True, text=True, timeout=180)
        assert r.returncode == 0, f"{script}: {r.stderr[-500:]}"
    assert (tmp_path / "link_budget.json").exists()
    assert (tmp_path / "link_budget.svg").exists()
    assert (tmp_path / "direction_finding.json").exists()
    assert (tmp_path / "direction_finding.svg").exists()
    json.loads((tmp_path / "link_budget.json").read_text(encoding="utf-8"))
