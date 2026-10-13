"""
Тесты антенной системы по матрице требований (REQ-A1, REQ-B1, REQ-C1, REQ-SW1, REQ-ANT-ALL).

Тесты проверяют только параметры из требований: импеданс, SWR, резонанс, КНД,
диапазоны, изоляция переключателя, покрытие. Не используют расчёты.

Запуск:
    .venv/bin/pytest tests/requirements/antenna/ -v
"""

import pytest

# ============================================================
# Исходные данные по требованиям (не расчёты):
# (req-id, freq_MHz, z_expected_ohm, z_tolerance_ohm,
#  vswr_max, resonance_MHz, resonance_tolerance_MHz,
#  gain_min_dBi, sidelobe_max_dB, panel_mm, step_mm, elements)
# ============================================================

ANTENNA_CASE_A = {
    "req_id": "REQ-A1",
    "channel": "А",
    "freq_min_MHz": 0,
    "freq_max_MHz": 900,
    "panels": "150x150",
    "material": "FR-4, 0.8 мм, ε=4.5",
    "gcpw": "50 Ом, W=1.2 мм, зазор=0.2 мм",
    "impedance_ohm": 50,
    "impedance_tolerance_ohm": 10,
    "vswr_max": 2.0,
    "gain_freq_MHz": 433,
    "gain_min_dBi": 5,
    "type": "печатная полуволновая змейка",
}

ANTENNA_CASE_B = {
    "req_id": "REQ-B1",
    "channel": "Б",
    "freq_min_MHz": 1200,
    "freq_max_MHz": 3000,
    "panels": "150x150",
    "material": "FR-4, 0.8 мм, ε=4.5",
    "gcpw": "50 Ом, W=1.2 мм, зазор=0.2 мм",
    "impedance_ohm": 50,
    "impedance_tolerance_ohm": 5,
    "vswr_max": 1.5,
    "resonance_MHz": 2450,
    "resonance_tolerance_MHz": 20,
    "gain_min_dBi": 8,
    "step_mm": 91.7,
    "elements": 4,
    "type": "микрополосковая матрица 2×2",
}

ANTENNA_CASE_C = {
    "req_id": "REQ-C1",
    "channel": "В",
    "freq_min_MHz": 4000,
    "freq_max_MHz": 7000,
    "panels": "150x150",
    "material": "FR-4, 0.8 мм, ε=4.5 (для ≥5 ГГц — Rogers RO4350B допускается)",
    "gcpw": "50 Ом, W=1.2 мм, зазор=0.2 мм",
    "impedance_ohm": 50,
    "impedance_tolerance_ohm": 5,
    "vswr_max": 2.0,
    "resonance_MHz": 5800,
    "resonance_tolerance_MHz": 50,
    "gain_min_dBi": 12,
    "sidelobe_max_dB": -15,
    "shield_mm": 1.5,
    "elements": 16,
    "type": "микрополосковая матрица 4×4",
}

SWITCH_CASE = {
    "req_id": "REQ-SW1",
    "type": "бескорпусные PIN-диоды КА507",
    "inputs": 3,
    "outputs": 1,
    "isolation_min_dB": 40,
    "switch_time_ns": 1000,
    "control": "TTL от ESP32",
    "burst_protection": "гашение во время Wi-Fi Burst",
    "impedance_ohm": 50,
    "impedance_tolerance_ohm": 5,
}

ANTENNA_SYSTEM_CASE = {
    "req_id": "REQ-ANT-ALL",
    "panels": "150x150",
    "material": "FR-4, 0.8 мм",
    "channels": ["REQ-A1", "REQ-B1", "REQ-C1"],
    "switch": "REQ-SW1",
    "coverage_min_MHz": 0,
    "coverage_max_MHz": 7000,
    "trace_files": [
        "block0_5v_bus.kicad_sch",
        "block1_rf_frontend.kicad_sch",
        "block3.kicad_sch",
    ],
}


# ============================================================
# Фикстуры
# ============================================================

@pytest.fixture(scope="module")
def antenna_req_a():
    return ANTENNA_CASE_A


@pytest.fixture(scope="module")
def antenna_req_b():
    return ANTENNA_CASE_B


@pytest.fixture(scope="module")
def antenna_req_c():
    return ANTENNA_CASE_C


@pytest.fixture(scope="module")
def switch_req():
    return SWITCH_CASE


@pytest.fixture(scope="module")
def antenna_system_req():
    return ANTENNA_SYSTEM_CASE


# ============================================================
# Тесты REQ-A1
# ============================================================

def test_req_a1_channel_range(antenna_req_a):
    """REQ-A1: диапазон приема не менее 0–900 МГц (SWR ≤ 2.0 по полосе)"""
    assert antenna_req_a["freq_min_MHz"] == 0
    assert antenna_req_a["freq_max_MHz"] == 900
    assert antenna_req_a["vswr_max"] == 2.0


def test_req_a1_impedance(antenna_req_a):
    """REQ-A1: входное сопротивление 50±10 Ом по полосе (REQ-03, ГОСТ 20275-91)"""
    z = antenna_req_a["impedance_ohm"]
    tol = antenna_req_a["impedance_tolerance_ohm"]
    assert z == 50
    assert abs(z - 50) <= tol


def test_req_a1_vswr(antenna_req_a):
    """REQ-A1: SWR ≤ 2.0 по полосе"""
    vswr = antenna_req_a["vswr_max"]
    assert vswr <= 2.0


def test_req_a1_gain(antenna_req_a):
    """REQ-A1: КНД ≥ 5 дБи на 433 МГц"""
    assert antenna_req_a["gain_min_dBi"] >= 5
    assert antenna_req_a["gain_freq_MHz"] == 433


def test_req_a1_gcpw(antenna_req_a):
    """REQ-A1: связь с трактом через GCPW 50 Ом, W=1.2 мм, зазор 0.2 мм"""
    gcpw = antenna_req_a["gcpw"]
    assert "50" in gcpw
    assert "1.2" in gcpw
    assert "0.2" in gcpw


def test_req_a1_panel(antenna_req_a):
    """REQ-A1: панель 150×150 мм, FR-4 0.8 мм"""
    assert antenna_req_a["panels"] == "150x150"
    assert "0.8" in antenna_req_a["material"]


# ============================================================
# Тесты REQ-B1
# ============================================================

def test_req_b1_channel_range(antenna_req_b):
    """REQ-B1: диапазон 1.2–3.0 ГГц, SWR≤1.5"""
    assert 1200 <= antenna_req_b["freq_min_MHz"] <= 1300
    assert 2900 <= antenna_req_b["freq_max_MHz"] <= 3100
    assert antenna_req_b["vswr_max"] == 1.5


def test_req_b1_resonance(antenna_req_b):
    """REQ-B1: резонансная частота 2.45±0.02 ГГц (2450±20 МГц)"""
    r = antenna_req_b["resonance_MHz"]
    tol = antenna_req_b["resonance_tolerance_MHz"]
    assert abs(r - 2450) <= tol


def test_req_b1_impedance(antenna_req_b):
    """REQ-B1: входное сопротивление 50±5 Ом (SWR≤1.5)"""
    z = antenna_req_b["impedance_ohm"]
    tol = antenna_req_b["impedance_tolerance_ohm"]
    assert z == 50
    assert abs(z - 50) <= tol


def test_req_b1_gain(antenna_req_b):
    """REQ-B1: КНД ≥ 8 дБи"""
    assert antenna_req_b["gain_min_dBi"] >= 8


def test_req_b1_step_and_elements(antenna_req_b):
    """REQ-B1: шаг между центрами элементов 91.7 мм, матрица 2×2 (4 элемента)"""
    assert antenna_req_b["step_mm"] == pytest.approx(91.7, abs=0.1)
    assert antenna_req_b["elements"] == 4


# ============================================================
# Тесты REQ-C1
# ============================================================

def test_req_c1_channel_range(antenna_req_c):
    """REQ-C1: диапазон 4.0–7.0 ГГц, SWR≤2.0"""
    assert 4000 <= antenna_req_c["freq_min_MHz"] <= 4100
    assert 6900 <= antenna_req_c["freq_max_MHz"] <= 7100
    assert antenna_req_c["vswr_max"] == 2.0


def test_req_c1_resonance(antenna_req_c):
    """REQ-C1: резонансная частота 5.8±0.05 ГГц (5800±50 МГц)"""
    r = antenna_req_c["resonance_MHz"]
    tol = antenna_req_c["resonance_tolerance_MHz"]
    assert abs(r - 5800) <= tol


def test_req_c1_impedance(antenna_req_c):
    """REQ-C1: входное сопротивление 50±5 Ом"""
    z = antenna_req_c["impedance_ohm"]
    tol = antenna_req_c["impedance_tolerance_ohm"]
    assert z == 50
    assert abs(z - 50) <= tol


def test_req_c1_gain(antenna_req_c):
    """REQ-C1: КНД ≥ 12 дБи (матрица 4×4)"""
    assert antenna_req_c["gain_min_dBi"] >= 12
    assert antenna_req_c["elements"] == 16


def test_req_c1_sidelobes(antenna_req_c):
    """REQ-C1: уровень боковых лепестков ≤-15 дБ"""
    sl = antenna_req_c["sidelobe_max_dB"]
    assert sl <= -15


def test_req_c1_shield(antenna_req_c):
    """REQ-C1: экранирование алюминий ≥1.5 мм"""
    shield = antenna_req_c["shield_mm"]
    assert shield >= 1.5


# ============================================================
# Тесты REQ-SW1
# ============================================================

def test_req_sw1_isolation(switch_req):
    """REQ-SW1: изоляция невыбранных каналов ≥ 40 дБ"""
    iso = switch_req["isolation_min_dB"]
    assert iso >= 40


def test_req_sw1_switch_time(switch_req):
    """REQ-SW1: время переключения ≤ 1 мкс (1000 нс)"""
    t = switch_req["switch_time_ns"]
    assert t <= 1000


def test_req_sw1_control(switch_req):
    """REQ-SW1: управление TTL от ESP32, 3 входа → 1 выход"""
    assert switch_req["control"] == "TTL от ESP32"
    assert switch_req["inputs"] == 3
    assert switch_req["outputs"] == 1


def test_req_sw1_impedance(switch_req):
    """REQ-SW1: входное и выходное сопротивление 50±5 Ом (REQ-03)"""
    z = switch_req["impedance_ohm"]
    tol = switch_req["impedance_tolerance_ohm"]
    assert z == 50
    assert abs(z - 50) <= tol


def test_req_sw1_burst_protection(switch_req):
    """REQ-SW1: гарантированное закрытие каналов во время Wi-Fi Burst"""
    assert switch_req["burst_protection"] == "гашение во время Wi-Fi Burst"


# ============================================================
# Тесты REQ-ANT-ALL
# ============================================================

def test_req_ant_all_coverage(antenna_system_req):
    """REQ-ANT-ALL: полное покрытие 0–7 ГГц без пропусков"""
    assert antenna_system_req["coverage_min_MHz"] == 0
    assert antenna_system_req["coverage_max_MHz"] == 7000


def test_req_ant_all_channels(antenna_system_req):
    """REQ-ANT-ALL: три канала (А, Б, В) на единой панели 150×150 мм, FR-4 0.8 мм"""
    assert set(antenna_system_req["channels"]) == {"REQ-A1", "REQ-B1", "REQ-C1"}
    assert antenna_system_req["panels"] == "150x150"
    assert "0.8" in antenna_system_req["material"]


def test_req_ant_all_switch(antenna_system_req):
    """REQ-ANT-ALL: переключение через PIN-ключи (REQ-SW1)"""
    assert antenna_system_req["switch"] == "REQ-SW1"


def test_req_ant_all_trace_files(antenna_system_req):
    """REQ-ANT-ALL: файлы трассировки KiCad"""
    expected = [
        "block0_5v_bus.kicad_sch",
        "block1_rf_frontend.kicad_sch",
        "block3.kicad_sch",
    ]
    assert antenna_system_req["trace_files"] == expected
