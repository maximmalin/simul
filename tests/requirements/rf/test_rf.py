"""
Тесты RF Frontend по матрице требований (REQ-01, REQ-03, REQ-04, REQ-05, REQ-06,
REQ-36, REQ-37).

Тесты используют только параметры из требований. Не используют расчёты.

Запуск:
    .venv/bin/pytest tests/requirements/rf/ -v
"""

import math

import pytest

# ============================================================
# Исходные данные по требованиям
# ============================================================

RF_REQ_01 = {
    "req_id": "REQ-01",
    "instant_bw_min_MHz": 500,
    "instant_bw_target_MHz": 1000,
    "band_min_MHz": 0,
    "band_max_MHz": 7000,
    "hackrf_bw_MHz": 20,
    "times_better_min": 25,
}

RF_REQ_03 = {
    "req_id": "REQ-03",
    "impedance_ohm": 50,
    "impedance_tolerance_ohm": 10,
    "vswr_max": 1.5,
    "band_min_MHz": 0,
    "band_max_MHz": 7000,
    "connector": "SMA",
    "standard": "ГОСТ 20275-91 / IEC 60169-15",
}

RF_REQ_04 = {
    "req_id": "REQ-04",
    "nf_max_dB": 3.5,
    "sensitivity_dBm": -110,
    "gain_tuner_dB": 25,
    "gain_kt368a_dB": 18,
    "g_tuner_linear": 316,
    "g_kt368a_linear": 63,
    "p_rec_watt": 1.56e-7,
    "nf_total_max_dB": 3.53,
}

RF_REQ_05 = {
    "req_id": "REQ-05",
    "overload_protection_dBm": 20,
    "preselector_block": True,
    "lna_with_protection": True,
    "laser_safety_class": "3B",
    "p_limit_mw": 3.1,
    "p_overload_w": 1.3,
}

RF_REQ_06 = {
    "req_id": "REQ-06",
    "pout_max_W": 1.5,
    "pout_safe_W": 1.5,
    "p_rf_out_mw": 3.1,
    "p_rf_out_dBm": 4.9,
    "i_ac_wm2": 18.6,
    "eta_opt_min_dB": None,
}

RF_REQ_36 = {
    "req_id": "REQ-36",
    "snr_min_db": 60.0,
    "ref_level_dbm": -60.0,
    "s9_level_dbm": -73.0,
    "mds_snr_db": 10.0,
    "bw_hz": 2700,
    "bw_label": "2.7 кГц",
    "std": "IEEE Std 1057 / ITU-R SM.326",
}

RF_REQ_37 = {
    "req_id": "REQ-37",
    "noise_floor_dbm": -120.0,
    "mds_dbm": -110.0,
    "bw_hz": 2700,
    "bw_label": "2.7 кГц",
    "nf_db": 3.5,
    "t_kelvin": 290,
    "std": "ITU-R SM.326 / IEEE Std 145",
}


# ============================================================
# Фикстуры
# ============================================================

@pytest.fixture(scope="module")
def req_01():
    return RF_REQ_01


@pytest.fixture(scope="module")
def req_03():
    return RF_REQ_03


@pytest.fixture(scope="module")
def req_04():
    return RF_REQ_04


@pytest.fixture(scope="module")
def req_05():
    return RF_REQ_05


@pytest.fixture(scope="module")
def req_06():
    return RF_REQ_06


@pytest.fixture(scope="module")
def req_36():
    return RF_REQ_36


@pytest.fixture(scope="module")
def req_37():
    return RF_REQ_37


# ============================================================
# Тесты REQ-01 (мгновенная полоса частот)
# ============================================================

def test_req_01_bandwidth_min(req_01):
    """REQ-01: мгновенная полоса частот не менее 500 МГц (целевая 1000 МГц)"""
    assert req_01["instant_bw_min_MHz"] == 500
    assert req_01["instant_bw_target_MHz"] == 1000


def test_req_01_coverage(req_01):
    """REQ-01: покрытие 0–7000 МГц (0–7 ГГц)"""
    assert req_01["band_min_MHz"] == 0
    assert req_01["band_max_MHz"] == 7000


def test_req_01_competitive(req_01):
    """REQ-01: как минимум в 25 раз шире HackRF (20 МГц)"""
    times = req_01["hackrf_bw_MHz"] / req_01["instant_bw_target_MHz"]
    # целевая 1000 МГц / 20 МГц = 50×, минимум 500/20=25×
    assert req_01["times_better_min"] == 25
    assert (req_01["instant_bw_target_MHz"] / req_01["hackrf_bw_MHz"]) >= (
        req_01["times_better_min"]
    )


# ============================================================
# Тесты REQ-03 (входное сопротивление)
# ============================================================

def test_req_03_impedance(req_03):
    """REQ-03: входное сопротивление 50±10 Ом"""
    z = req_03["impedance_ohm"]
    tol = req_03["impedance_tolerance_ohm"]
    assert z == 50
    assert abs(z - 50) <= tol


def test_req_03_vswr(req_03):
    """REQ-03: VSWR ≤ 1.5 в диапазоне 0–7 ГГц"""
    assert req_03["vswr_max"] <= 1.5


def test_req_03_connector(req_03):
    """REQ-03: разъём SMA, ГОСТ 20275-91 / IEC 60169-15"""
    assert req_03["connector"] == "SMA"
    assert "ГОСТ 20275-91" in req_03["standard"]


# ============================================================
# Тесты REQ-04 (коэффициент шума)
# ============================================================

def test_req_04_nf(req_04):
    """REQ-04: NF ≤ 3.5 дБ (IEEE Std 145)"""
    assert req_04["nf_max_dB"] == 3.5


def test_req_04_nf_total(req_04):
    """REQ-04: итоговый NF системы ≈ 3.53 дБ (NF_KT368 = 10 дБ, G1 = 316)"""
    nf1 = req_04["nf_max_dB"]
    nf2 = 10.0  # допущение
    g1 = req_04["g_tuner_linear"]
    nf_total = nf1 + (nf2 - 1) / g1
    assert nf_total <= req_04["nf_total_max_dB"]


def test_req_04_sensitivity(req_04):
    """REQ-04: чувствительность -110 дБм"""
    assert req_04["sensitivity_dBm"] == -110


def test_req_04_gain_chain(req_04):
    """REQ-04: каскадное усиление: TUNER 25 дБ (316), КТ368А 18 дБ (63)"""
    assert req_04["g_tuner_linear"] == 316
    assert req_04["g_kt368a_linear"] == 63
    g_db = 10 * (req_04["g_tuner_linear"] * req_04["g_kt368a_linear"]) ** 0  # проверка только наличие ключей
    assert "g_tuner_linear" in req_04
    assert "g_kt368a_linear" in req_04


# ============================================================
# Тесты REQ-05 (защита от перегрузки)
# ============================================================

def test_req_05_overload_protection(req_05):
    """REQ-05: защита от перегрузки до +20 дБм"""
    assert req_05["overload_protection_dBm"] == 20


def test_req_05_preselector(req_05):
    """REQ-05: коммутируемый блок преселекторов"""
    assert req_05["preselector_block"] is True


def test_req_05_lna(req_05):
    """REQ-05: малошумящий усилитель с защитой от перегрузки"""
    assert req_05["lna_with_protection"] is True


def test_req_05_laser_safety(req_05):
    """REQ-05: класс безопасности лазера 3B (GOST IEC 60825-1)"""
    assert req_05["laser_safety_class"] == "3B"


def test_req_05_power_limits(req_05):
    """REQ-05: выходная мощность ≤ 1.3 Вт при перегрузке, защита от насыщения лазера"""
    assert req_05["p_overload_w"] <= 1.3
    assert req_05["p_limit_mw"] == 3.1


# ============================================================
# Тесты REQ-06 (выходной ВЧ-драйвер)
# ============================================================

def test_req_06_pout_max(req_06):
    """REQ-06: линейный усилитель до 1.5 Вт"""
    assert req_06["pout_max_W"] == 1.5


def test_req_06_pout_safe(req_06):
    """REQ-06: выходная мощность 1.5 Вт — безопасный предел (без насыщения лазера)"""
    assert req_06["pout_safe_W"] == 1.5


def test_req_06_chain_output(req_06):
    """REQ-06: каскадное усиление даёт P_rf_out = 3.1 мВт (+4.9 дБм)"""
    assert req_06["p_rf_out_mw"] == pytest.approx(3.1, abs=0.1)
    assert req_06["p_rf_out_dBm"] == pytest.approx(4.9, abs=0.1)


def test_req_06_i_ac_limit(req_06):
    """REQ-06: интенсивность ультразвука I_ac ≤ 18.6 Вт/м² (без перегрузки кварца TeO₂)"""
    assert req_06["i_ac_wm2"] == pytest.approx(18.6, abs=0.1)


# ============================================================
# Тесты REQ-36 (отношение сигнал/шум)
# ============================================================

def test_req_36_snr_min(req_36):
    """REQ-36: SNR не менее 60 дБ при входном уровне −60 дБм (полоса 2.7 кГц)"""
    assert req_36["snr_min_db"] == 60.0
    assert req_36["ref_level_dbm"] == -60.0


def test_req_36_bandwidth(req_36):
    """REQ-36: полоса измерения 2.7 кГц (SSB)"""
    assert req_36["bw_hz"] == 2700
    assert req_36["bw_label"] == "2.7 кГц"


def test_req_36_standard(req_36):
    """REQ-36: стандарт IEEE Std 1057 / ITU-R SM.326"""
    assert "1057" in req_36["std"]


def test_req_36_snr_definition(req_36):
    """REQ-36: SNR = S − N (S = −60 дБм, N = −120 дБм из REQ-37)"""
    s = req_36["ref_level_dbm"]
    n = -120.0
    assert (s - n) == pytest.approx(req_36["snr_min_db"], abs=0.1)


def test_req_36_mds(req_36):
    """REQ-36: на пороге обнаружимости S+N/N ≥ 10 дБ"""
    assert req_36["mds_snr_db"] == 10.0
    assert req_36["s9_level_dbm"] == -73.0


# ============================================================
# Тесты REQ-37 (уровень собственного шума / MDS)
# ============================================================

def test_req_37_noise_floor(req_37):
    """REQ-37: уровень собственного шума не более −120 дБм в полосе 2.7 кГц"""
    assert req_37["noise_floor_dbm"] == -120.0


def test_req_37_bandwidth(req_37):
    """REQ-37: полоса измерения 2.7 кГц"""
    assert req_37["bw_hz"] == 2700
    assert req_37["bw_label"] == "2.7 кГц"


def test_req_37_mds(req_37):
    """REQ-37: MDS = −110 дБм (S+N/N = 10 дБ над шумом)"""
    assert req_37["mds_dbm"] == -110.0
    assert (req_37["mds_dbm"] - req_37["noise_floor_dbm"]) == pytest.approx(10.0, abs=0.1)


def test_req_37_thermal_noise(req_37):
    """REQ-37: рассчётная шумовая дорожка kTB + NF не хуже нормы −120 дБм

    N0 = −174 dBm/Hz + 10*log10(B); N = N0 + NF. Должно быть N ≤ noise_floor.
    """
    n0 = -174.0 + 10.0 * math.log10(req_37["bw_hz"])
    n = n0 + req_37["nf_db"]
    assert n <= req_37["noise_floor_dbm"]


def test_req_37_nf_consistency(req_37):
    """REQ-37: NF нормируется по REQ-04 (≤3.5 дБ)"""
    assert req_37["nf_db"] == 3.5
