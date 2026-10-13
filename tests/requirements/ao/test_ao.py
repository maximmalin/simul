"""
Тесты акустооптического процессора по матрице требований (REQ-07/08/09/11/13/14/15).

Тесты используют только параметры из требований. Не используют расчёты.

Запуск:
    .venv/bin/pytest tests/requirements/ao/ -v
"""

import pytest

# ============================================================
# Исходные данные по требованиям
# ============================================================

AO_REQ_07 = {
    "req_id": "REQ-07",
    "wavelength_nm_options": [650, 532],
    "wavelength_primary_nm": 650,
    "standard": "ГОСТ IEC 60825-1-2013 / IEC 60825-1 (лазер 3B)",
}

AO_REQ_08 = {
    "req_id": "REQ-08",
    "power_stability_max_percent": 0.5,
    "standard": "ГОСТ IEC 60825-1-2013",
}

AO_REQ_09 = {
    "req_id": "REQ-09",
    "aprc_present": True,
    "arm_standard": "ГОСТ IEC 60825-1-2013 / IEC 60825-1",
    "laser_safety_class": "3B",
}

AO_REQ_11 = {
    "req_id": "REQ-11",
    "crystal": "TeO₂ (диоксид теллура)",
    "wave_type": "медленная сдвиговая волна",
    "delta_n_target": 1.51e-5,
    "standard": "ГОСТ ISO 9211-1-2016 / ISO 9211-1 (просветление R<0.2%)",
}

AO_REQ_13 = {
    "req_id": "REQ-13",
    "pixels_min": 2048,
    "pixel_dynamic_range_min_dB": 72,
    "line_readout_min_Hz": 50e3,
    "standard": "EMVA 1288 Standard",
}

AO_REQ_14 = {
    "req_id": "REQ-14",
    "pixel_dynamic_range_min_dB": 72,
    "standard": "EMVA 1288 Standard",
}

AO_REQ_15 = {
    "req_id": "REQ-15",
    "line_readout_min_Hz": 50e3,
    "standard": "EMVA 1288 Standard",
}

# ============================================================
# Фикстуры
# ============================================================

@pytest.fixture(scope="module")
def req_07():
    return AO_REQ_07


@pytest.fixture(scope="module")
def req_08():
    return AO_REQ_08


@pytest.fixture(scope="module")
def req_09():
    return AO_REQ_09


@pytest.fixture(scope="module")
def req_11():
    return AO_REQ_11


@pytest.fixture(scope="module")
def req_13():
    return AO_REQ_13


@pytest.fixture(scope="module")
def req_14():
    return AO_REQ_14


@pytest.fixture(scope="module")
def req_15():
    return AO_REQ_15


# ============================================================
# Тесты REQ-07 (лазерная длина волны)
# ============================================================

def test_req_07_wave_length(req_07):
    """REQ-07: полупроводниковый лазерный диод с длиной волны λ = 650 нм (или 532 нм)"""
    assert req_07["wavelength_primary_nm"] in req_07["wavelength_nm_options"]
    assert 650 in req_07["wavelength_nm_options"]
    assert 532 in req_07["wavelength_nm_options"]


# ============================================================
# Тесты REQ-08 (стабильность оптической мощности)
# ============================================================

def test_req_08_stability(req_08):
    """REQ-08: стабильность оптической мощности не хуже 0.5%"""
    assert req_08["power_stability_max_percent"] == 0.5


def test_req_08_standard(req_08):
    """REQ-08: стандарт ГОСТ IEC 60825-1-2013"""
    assert "ГОСТ IEC 60825-1-2013" in req_08["standard"]


# ============================================================
# Тесты REQ-09 (АРМ)
# ============================================================

def test_req_09_aprc(req_09):
    """REQ-09: наличие цепи автоматического регулирования мощности (АРМ)"""
    assert req_09["aprc_present"] is True


def test_req_09_laser_safety(req_09):
    """REQ-09: класс безопасности лазера 3B (ГОСТ IEC 60825-1-2013)"""
    assert req_09["laser_safety_class"] == "3B"
    assert "ГОСТ IEC 60825-1-2013" in req_09["arm_standard"]


# ============================================================
# Тесты REQ-11 (кристалл TeO₂)
# ============================================================

def test_req_11_crystal(req_11):
    """REQ-11: кристалл диоксида теллура TeO₂"""
    assert req_11["crystal"] == "TeO₂ (диоксид теллура)"
    assert req_11["wave_type"] == "медленная сдвиговая волна"


def test_req_11_delta_n(req_11):
    """REQ-11: глубина изменения индекса преломления Δn ≈ 1.51×10⁻⁵"""
    expected = 1.51e-5
    assert req_11["delta_n_target"] == pytest.approx(expected, rel=1e-2)


def test_req_11_standard(req_11):
    """REQ-11: стандарт просветления R<0.2% (ГОСТ ISO 9211-1-2016)"""
    assert "R<0.2%" in req_11["standard"]
    assert "ГОСТ ISO 9211-1-2016" in req_11["standard"]


# ============================================================
# Тесты REQ-13 (КМОП-матрица)
# ============================================================

def test_req_13_pixels(req_13):
    """REQ-13: КМОП-матрица не менее 2048 пикселей"""
    assert req_13["pixels_min"] == 2048


def test_req_13_dynamic_range(req_13):
    """REQ-13: динамический диапазон пикселя не менее 72 дБ"""
    assert req_13["pixel_dynamic_range_min_dB"] == 72


def test_req_13_line_readout(req_13):
    """REQ-13: частота считывания строк не менее 50 кГц"""
    assert req_13["line_readout_min_Hz"] == 50e3


def test_req_13_standard(req_13):
    """REQ-13: стандарт EMVA 1288"""
    assert req_13["standard"] == "EMVA 1288 Standard"


# ============================================================
# Тесты REQ-14 (динамический диапазон пикселя)
# ============================================================

def test_req_14_dynamic_range(req_14):
    """REQ-14: динамический диапазон пикселя не менее 72 дБ"""
    assert req_14["pixel_dynamic_range_min_dB"] == 72


def test_req_14_standard(req_14):
    """REQ-14: стандарт EMVA 1288"""
    assert req_14["standard"] == "EMVA 1288 Standard"


# ============================================================
# Тесты REQ-15 (частота считывания строк)
# ============================================================

def test_req_15_line_readout(req_15):
    """REQ-15: частота считывания строк не менее 50 кГц"""
    assert req_15["line_readout_min_Hz"] == 50e3


def test_req_15_standard(req_15):
    """REQ-15: стандарт EMVA 1288"""
    assert req_15["standard"] == "EMVA 1288 Standard"
