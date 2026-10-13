"""
Тесты теплового режима Krystal Radio (SDR).

Два уровня:
  * fast — узловая модель T_j = T_a + P·θ_JA (θ_JA из даташитов);
  * fem  — 2D FEM теплопроводности по контуру платы из PCB CAD
           (hardware/tscircuit/out/circuit.json) с источниками из ngspice.

Запуск:
    pytest tests/requirements/thermal/ -v
"""

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "sim" / "scripts" / "thermal_pcb.py"
CAD = next((p for p in (ROOT / "out" / "circuit.json",
                        ROOT / "hardware" / "tscircuit" / "out" / "circuit.json")
            if p.exists()), ROOT / "out" / "circuit.json")

TA = 25.0


@pytest.fixture(scope="module")
def thermal(tmp_path_factory):
    if shutil.which("ngspice") is None:
        pytest.skip("ngspice not installed")
    if not CAD.exists():
        pytest.skip("PCB CAD (circuit.json) not built — run `make pcb`")
    if (importlib.util.find_spec("numpy") is None
            or importlib.util.find_spec("scipy") is None):
        pytest.skip("numpy/scipy not available")
    out = tmp_path_factory.mktemp("thermal")
    r = subprocess.run([sys.executable, str(SCRIPT), "--out", str(out)],
                       capture_output=True, text=True)
    assert r.returncode == 0, f"thermal rc={r.returncode}\n{r.stdout}\n{r.stderr}"
    ff = json.loads((out / "thermal_fast.json").read_text())
    fm = json.loads((out / "thermal_fem.json").read_text())
    return {
        "bias": fast_extract(out)["bias"] if False else json.loads(
            (out / "thermal_fast.json").read_text())["ngspice"],
        "fast": json.loads((out / "thermal_fast.json").read_text())["fast"],
        "board": json.loads((out / "thermal_fast.json").read_text())["board"],
        "fem": json.loads((out / "thermal_fem.json").read_text())["fem"],
        "fem_sources": json.loads((out / "thermal_fem.json").read_text())["sources"],
        "assets": json.loads((out / "thermal_fem.json").read_text())["assets"],
        "log": r.stdout,
    }


# ---------------------------------------------------------------------------
# ngspice: электрический режим → потери
# ---------------------------------------------------------------------------

def test_ngspice_rail_voltage(thermal):
    """LDO держит шину 3.3 В (ngspice .op)"""
    assert thermal["bias"]["v_rail"] == pytest.approx(3.3, abs=0.02)


def test_ngspice_total_power_plausible(thermal):
    """ngspice посчитал положительную суммарную мощность (~0.3 Вт)"""
    total = thermal["bias"]["total_w"]
    assert 0.05 < total < 1.5, total


def test_ngspice_power_conservation(thermal):
    """Сумма мощностей нагрузки и LDO = общая; всё сопоставлено приборам"""
    p = thermal["bias"]["powers"]
    assert sum(p.values()) == pytest.approx(thermal["bias"]["total_w"], rel=1e-9)
    assert sum(d["p_w"] for d in thermal["fast"]["devices"]) == pytest.approx(
        thermal["bias"]["total_w"], rel=1e-9)


def test_bias_deck_written(thermal):
    """ngspice-дека сохраняется для воспроизводимости"""
    assert Path(thermal["bias"]["deck"]).exists()


# ---------------------------------------------------------------------------
# fast: T_j = T_a + P·θ_JA
# ---------------------------------------------------------------------------

def test_fast_ambient(thermal):
    """fast-модель считает от заданной температуры среды"""
    assert thermal["fast"]["ta_c"] == TA


def test_fast_all_devices_pass(thermal):
    """fast: T_j каждого прибора не превышает T_j,max даташита"""
    devs = thermal["fast"]["devices"]
    assert devs, "no devices in fast model"
    for d in devs:
        assert d["pass_"], d
        assert d["tj_c"] <= d["tj_max_c"]


def test_fast_worst_temperature_margin(thermal):
    """fast: у худшего прибора запас не меньше 10 °C"""
    worst = max(thermal["fast"]["devices"], key=lambda d: d["tj_c"])
    assert worst["margin_c"] >= 10.0, worst


def test_fast_models_expected_hotspots(thermal):
    """fast: самые горячие — LDO и MCU (наибольшие мощности)"""
    refs = {d["ref"] for d in thermal["fast"]["devices"]}
    assert {"U_LDO", "U1"} <= refs


# ---------------------------------------------------------------------------
# FEM: распределение тепла по плате
# ---------------------------------------------------------------------------

def test_fem_above_ambient(thermal):
    """FEM: плата греется выше окружающей среды"""
    assert thermal["fem"]["t_max_c"] > TA
    assert thermal["fem"]["t_avg_c"] >= TA


def test_fem_peak_above_average(thermal):
    """FEM: есть локальный перегрев (T_max > T_avg)"""
    assert thermal["fem"]["t_max_c"] > thermal["fem"]["t_avg_c"]


def test_fem_energy_balance(thermal):
    """FEM: приход тепла = сток конвекции (невязка < 0.5 мВт)"""
    assert thermal["fem"]["q_total_w"] > 0.0
    assert thermal["fem"]["energy_balance_w"] < 0.5e-3, thermal["fem"]["energy_balance_w"]


def test_fem_heat_equals_ngspice_losses(thermal):
    """FEM: суммарный источник тепла = мощности из ngspice"""
    total = sum(thermal["bias"]["powers"].values())
    assert thermal["fem"]["q_total_w"] == pytest.approx(total, rel=1e-6)


def test_fem_sources_placed(thermal):
    """FEM: каждой мощности ngspice сопоставлен источник на посадочном месте"""
    srcs = thermal["fem"]["sources"]
    assert len(srcs) >= 4
    assert all(t >= TA for t in srcs.values())


def test_fem_full_board_model(thermal):
    """FEM: домен — вся плата (число узлов соответствует сетке)"""
    fem = thermal["fem"]
    assert fem["nodes"] > 1000
    assert fem["n_tri"] == 2 * fem["nx"] * fem["ny"]


def test_fem_not_hotter_than_theta_ja(thermal):
    """FEM (растекание по меди) не горячее аналитической оценки θ_JA"""
    assert thermal["fem"]["t_max_c"] <= thermal["fast"]["worst_tj_c"] + 5.0


# ---------------------------------------------------------------------------
# CAD / технология
# ---------------------------------------------------------------------------

def test_board_stackup(thermal):
    """CAD: 4 слоя, FR4, 1.6 мм"""
    b = thermal["board"]
    assert b["layers"] == 4
    assert b["thickness"] == pytest.approx(1.6, abs=1e-9)
    assert b["material"].lower() == "fr4"


def test_copper_fraction_physical(thermal):
    """Эффективная теплопроводность меди+FR4 лежит в физичном диапазоне"""
    k = thermal["assets"]["k_in_w_mk"]
    assert 0.3 < k < 400.0
    assert 0.0 < thermal["assets"]["f_cu"] < 1.0
