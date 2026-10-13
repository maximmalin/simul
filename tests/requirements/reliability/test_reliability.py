"""
Тесты надёжности Krystal Radio (SDR) по матрице требований.

REQ-26: MTBF >= 10 000 ч.

Расчёт ведёт Octave-модель `sim/octave/reliability_model.m` (parts-count +
температурное ускорение по Аррениусу). Исходные данные — `reliability_parts.tsv`
по даташитам и справочникам (Siemens SN 29500 / MIL-HDBK-217F).

Запуск:
    pytest tests/requirements/reliability/ -v
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
OCTAVE = shutil.which("octave-cli") or shutil.which("octave")
MODEL = ROOT / "sim" / "octave" / "reliability_model.m"
PARTS = ROOT / "sim" / "octave" / "reliability_parts.tsv"

# REQ-26
MTBF_TARGET_H = 10000.0


@pytest.fixture(scope="module")
def result(tmp_path_factory):
    if OCTAVE is None:
        pytest.skip("octave not installed")
    out = tmp_path_factory.mktemp("reliability") / "result.json"
    script = (f"addpath('{ROOT / 'sim' / 'octave'}'); "
              f"reliability_model('{PARTS}','{out}')")
    r = subprocess.run([OCTAVE, "--quiet", "--eval", script],
                       capture_output=True, text=True)
    assert r.returncode == 0, f"octave rc={r.returncode}\n{r.stdout}\n{r.stderr}"
    assert out.exists(), "reliability_model produced no JSON"
    return json.loads(out.read_text())


# ---------------------------------------------------------------------------
# REQ-26: система в целом
# ---------------------------------------------------------------------------

def test_req_26_mtbf_target(result):
    """REQ-26: MTBF не меньше 10 000 ч"""
    assert result["mtbf_target_h"] == MTBF_TARGET_H
    assert result["mtbf_h"] >= MTBF_TARGET_H, (
        f"MTBF {result['mtbf_h']:.0f} ч < {MTBF_TARGET_H:.0f} ч")


def test_req_26_passes_flag(result):
    """REQ-26: модель подтверждает соответствие"""
    assert result["pass"] is True


def test_mtbf_equals_inverse_lambda(result):
    """MTBF = 1/Λ — внутренняя согласованность модели"""
    assert result["mtbf_h"] == pytest.approx(1.0 / result["lambda_per_hour"],
                                              rel=1e-9)


def test_lambda_matches_fit_sum(result):
    """Λ[1/ч] = (Σ FIT)·1e-9"""
    assert result["lambda_per_hour"] == pytest.approx(result["total_fit"] * 1e-9,
                                                      rel=1e-9)
    grp = sum(c["fit_group"] for c in result["categories"])
    assert result["total_fit"] == pytest.approx(grp, rel=1e-9)


# ---------------------------------------------------------------------------
# структура и параметры даташитов
# ---------------------------------------------------------------------------

def test_parts_cover_all_categories(result):
    """Параметры есть для всех категорий BOM"""
    cats = {c["category"] for c in result["categories"]}
    assert len(cats) >= 8
    assert "IC_STM32" in cats and "IC_Regulator" in cats


def test_total_qty_matches_bom(result):
    """Суммарное число элементов соответствует BOM SDR (60 шт.)"""
    assert result["total_qty"] == 60


def test_datasheet_activation_energies(result):
    """Ea положительны и лежат в физичном диапазоне 0.05–0.5 эВ"""
    for c in result["categories"]:
        assert 0.05 <= c["ea_ev"] <= 0.5, c


def test_all_pi_t_at_least_one(result):
    """Рабочая температура не ниже опорной → π_T ≥ 1"""
    for c in result["categories"]:
        assert c["pi_t"] >= 1.0 - 1e-12, c


# ---------------------------------------------------------------------------
# температурная чувствительность (Аррениус)
# ---------------------------------------------------------------------------

def test_hot_is_worse(result):
    """При перегреве MTBF падает (деградация по Аррениусу)"""
    assert result["mtbf_hot_h"] < result["mtbf_h"]


def test_hot_still_meets_target(result):
    """Даже при +20 °C к рабочей температуре REQ-26 выполняется"""
    assert result["mtbf_hot_h"] >= MTBF_TARGET_H, (
        f"горячий MTBF {result['mtbf_hot_h']:.0f} ч < {MTBF_TARGET_H:.0f} ч")


def test_arrhenius_matches_formula(result):
    """π_T = exp(Ea/kB·(1/T_ref − 1/T_use)) — проверка формулы"""
    import math
    kB = 8.617333262e-5
    for c in result["categories"]:
        # T_ref неизвестна из JSON — берём фиксированную 40 °C из TSV
        t_ref = 40.0 + 273.15
        t_use = c["t_use_c"] + 273.15
        pi = math.exp(c["ea_ev"] / kB * (1.0 / t_ref - 1.0 / t_use))
        assert c["pi_t"] == pytest.approx(pi, rel=1e-6)
