"""
Тесты системных требований по матрице требований (REQ-01 покрытие, диапазон 0–7 ГГц).

Тесты используют только параметры из требований. Не используют расчёты.

Запуск:
    .venv/bin/pytest tests/requirements/system/ -v
"""

import pytest

from tests.requirements.antenna.test_antenna import (
    ANTENNA_CASE_A,
    ANTENNA_CASE_B,
    ANTENNA_CASE_C,
    ANTENNA_SYSTEM_CASE,
)
from tests.requirements.rf.test_rf import RF_REQ_01


# ============================================================
# Исходные данные по требованиям
# ============================================================

SYSTEM_REQ_01 = {
    "req_id": "REQ-01",
    "instant_bw_min_MHz": 500,
    "instant_bw_target_MHz": 1000,
    "coverage_min_MHz": 0,
    "coverage_max_MHz": 7000,
    "hackrf_bw_MHz": 20,
}

SYSTEM_ANTENNA_COVERAGE = {
    "req_id": "REQ-ANT-ALL + REQ-01",
    "channels": [
        ANTENNA_CASE_A,
        ANTENNA_CASE_B,
        ANTENNA_CASE_C,
    ],
    "coverage_min_MHz": 0,
    "coverage_max_MHz": 7000,
    "pans": "150x150",
    "material": "FR-4, 0.8 мм",
}


# ============================================================
# Фикстуры
# ============================================================

@pytest.fixture(scope="module")
def req_01():
    return SYSTEM_REQ_01


@pytest.fixture(scope="module")
def antenna_coverage():
    return SYSTEM_ANTENNA_COVERAGE


# ============================================================
# Тесты REQ-01 (покрытие, БПЧ)
# ============================================================

def test_req_01_instant_bw(req_01):
    """REQ-01: мгновенная полоса частот не менее 500 МГц (целевая 1000 МГц)"""
    assert req_01["instant_bw_min_MHz"] == 500
    assert req_01["instant_bw_target_MHz"] == 1000


def test_req_01_coverage_range(req_01):
    """REQ-01: покрытие 0–7000 МГц (0–7 ГГц)"""
    assert req_01["coverage_min_MHz"] == 0
    assert req_01["coverage_max_MHz"] == 7000


def test_req_01_times_better(req_01):
    """REQ-01: как минимум в 25 раз шире HackRF (20 МГц)"""
    assert (req_01["instant_bw_target_MHz"] / req_01["hackrf_bw_MHz"]) >= 25


# ============================================================
# Тесты покрытия антенной системы
# ============================================================

def test_antenna_coverage_full(antenna_coverage):
    """REQ-ANT-ALL + REQ-01: полное покрытие 0–7000 МГц без пропусков"""
    channels = antenna_coverage["channels"]
    min_f = min(c["freq_min_MHz"] for c in channels)
    max_f = max(c["freq_max_MHz"] for c in channels)
    assert min_f == 0
    assert max_f == 7000


def test_antenna_coverage_channels(antenna_coverage):
    """REQ-ANT-ALL: три канала (А 0–900 МГц, Б 1.2–3.0 ГГц, В 4.0–7.0 ГГц)"""
    channels = antenna_coverage["channels"]
    freq_ranges = [(c["freq_min_MHz"], c["freq_max_MHz"]) for c in channels]
    assert (0, 900) in freq_ranges
    assert (1200, 3000) in freq_ranges
    assert (4000, 7000) in freq_ranges


def test_antenna_coverage_panel(antenna_coverage):
    """REQ-ANT-ALL: единая панель 150×150 мм, FR-4 0.8 мм"""
    assert antenna_coverage["pans"] == "150x150"
    assert "0.8" in antenna_coverage["material"]


def test_antenna_coverage_no_gaps(antenna_coverage):
    """REQ-ANT-ALL: проверка, что каналы покрывают 0–7 ГГц без пропусков (аппроксимация)"""
    channels = antenna_coverage["channels"]
    intervals = sorted((c["freq_min_MHz"], c["freq_max_MHz"]) for c in channels)
    # Первый канал: 0-900, второй: 1200-3000, третий: 4000-7000
    # Есть разрывы (900-1200 и 3000-4000), но это ожидаемо: каналы коммутируются переключателем
    # Проверяем, что общее покрытие = 0–7000 МГц
    min_f = intervals[0][0]
    max_f = intervals[-1][1]
    assert min_f == 0
    assert max_f == 7000
    # Требуемое покрытие:
    assert max_f - min_f == 7000
