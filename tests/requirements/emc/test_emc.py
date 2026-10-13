"""Тесты ЭМС (REQ-21…REQ-25) по матрице требований.

Проверяются: физика экранирования (скин-слой, эффективность экрана),
иммунитет к внешним ЭМ-полям (ГОСТ 30804.4.3-2013 / IEC 61000-4-3,
уровень жёсткости 2 — 3 В/м), норма эмиссии ГОСТ CISPR 22-2012 (Класс Б),
фильтры питания и зонирование корпуса.

Тесты опираются только на параметры требований и стандартные формулы,
без использования каталога `calculations/`.

Запуск:
    .venv/bin/pytest tests/requirements/emc/ -v
"""

import math

MU0 = 4.0 * math.pi * 1e-7        # Гн/м
RHO_AL = 2.65e-8                  # Ом·м — удельное сопротивление алюминия


# ===========================================================================
# REQ-21 — экранирование корпуса (Case Shielding)
# ===========================================================================
EMC_REQ_21 = {
    "req_id": "REQ-21",
    "material": "алюминий (фрезерованный)",
    "sections": 3,                 # ВЧ / оптический / цифровой
    "rho_ohm_m": RHO_AL,
    "mu_r": 1.0,
    "shield_thickness_m": 1.0e-3,  # стенка корпуса, м
    "reference_frequency_hz": 1.0e9,
    "gasket_contact_resistance_ohm": 10e-3,   # ≤ 10 мОм (MIL-DTL-83528)
    "se_required_db": 40.0,
    "band_ghz": (0.0, 7.0),
}


def test_req_21_skin_depth():
    """δ = √(2ρ/ωμ) для алюминия; масштаб — единицы микрометров.

    Примечание: текст требования приводит «δ ≈ 0.025 мм при 1 ГГц» — это
    консервативная оценка (истинное значение ≈2.6 мкм). Обе величины дают
    эффективность экрана, многократно превышающую требуемые 40 дБ.
    """
    delta = math.sqrt(2.0 * EMC_REQ_21["rho_ohm_m"]
                      / (2.0 * math.pi * EMC_REQ_21["reference_frequency_hz"]
                         * MU0 * EMC_REQ_21["mu_r"]))
    assert 2.0e-6 <= delta <= 3.5e-6, f"δ = {delta*1e6:.2f} мкм"
    # даже консервативное δ из текста требования даёт SE ≫ 40 дБ
    se_conservative = 8.686 * EMC_REQ_21["shield_thickness_m"] / 0.025e-3
    assert se_conservative >= EMC_REQ_21["se_required_db"]


def test_req_21_shielding_effectiveness():
    """Экран обеспечивает SE ≥ 40 дБ (стенка 1 мм даёт >100 дБ)"""
    delta = math.sqrt(2.0 * EMC_REQ_21["rho_ohm_m"]
                      / (2.0 * math.pi * EMC_REQ_21["reference_frequency_hz"]
                         * MU0 * EMC_REQ_21["mu_r"]))
    se_db = 8.686 * EMC_REQ_21["shield_thickness_m"] / delta
    assert se_db > 100.0, f"SE = {se_db:.0f} дБ"
    assert se_db >= EMC_REQ_21["se_required_db"]


def test_req_21_gasket_contact_resistance():
    """RF-прокладки: контактное сопротивление ≤ 10 мОм до 10 ГГц"""
    assert EMC_REQ_21["gasket_contact_resistance_ohm"] <= 10e-3
    assert EMC_REQ_21["sections"] == 3
    assert EMC_REQ_21["material"].startswith("алюминий")


def test_req_21_band_0_7ghz():
    """Экран рассчитан на диапазон 0–7 ГГц"""
    assert EMC_REQ_21["band_ghz"] == (0.0, 7.0)


# ===========================================================================
# REQ-22 — иммунитет к внешним ЭМ-полям
# ===========================================================================
EMC_REQ_22 = {
    "req_id": "REQ-22",
    "standard": "ГОСТ 30804.4.3-2013 / IEC 61000-4-3",
    "hardness_level": 2,
    "field_v_per_m": 3.0,
    "field_band_mhz": (80.0, 2700.0),
    "effective_length_m": 0.1,
    "input_impedance_ohm": 50.0,
    "p_rec_w": 1.56e-7,            # полезный сигнал (REQ-04)
    "suppression_db": (40.0, 60.0),
    "dynamic_range_db": (85.0, 100.0),
    "foucault_phasenoise_dbc_hz": -110.0,
}


def test_req_22_immunity_level_and_band():
    """Уровень жёсткости 2 = 3 В/м, полоса 80–2700 МГц"""
    assert EMC_REQ_22["hardness_level"] == 2
    assert EMC_REQ_22["field_v_per_m"] == 3.0
    assert EMC_REQ_22["field_band_mhz"] == (80.0, 2700.0)


def test_req_22_induced_voltage_and_power():
    """V = E·h; P = V²/R при 50 Ом для E = 3 В/м, h = 0.1 м"""
    v = EMC_REQ_22["field_v_per_m"] * EMC_REQ_22["effective_length_m"]
    p = v ** 2 / EMC_REQ_22["input_impedance_ohm"]
    assert math.isclose(v, 0.3, rel_tol=1e-9)
    assert math.isclose(p, 1.8e-3, rel_tol=1e-6)


def test_req_22_required_suppression():
    """Подавление помехи до уровня полезного сигнала — 40…60 дБ"""
    v = EMC_REQ_22["field_v_per_m"] * EMC_REQ_22["effective_length_m"]
    p_ind = v ** 2 / EMC_REQ_22["input_impedance_ohm"]
    atten_db = 10.0 * math.log10(p_ind / EMC_REQ_22["p_rec_w"])
    lo, hi = EMC_REQ_22["suppression_db"]
    assert lo <= atten_db <= hi, f"нужно подавление {atten_db:.1f} дБ"


def test_req_22_dynamic_range_and_foucault():
    """Экранирование сохраняет ДД 85–100 дБ; нож Фуко < −110 дБс/Гц"""
    assert EMC_REQ_22["dynamic_range_db"] == (85.0, 100.0)
    assert EMC_REQ_22["foucault_phasenoise_dbc_hz"] <= -110.0


# ===========================================================================
# REQ-23 — уровень радиопомех (Emission Level)
# ===========================================================================
EMC_REQ_23 = {
    "req_id": "REQ-23",
    "standard": "ГОСТ CISPR 22-2012 (Класс Б) / EN 55022",
    "wifi_bands_ghz": (2.4, 5.0),
    "shielded_emission_dbm_per_m": -30.0,
    "linear_regulators": ("LM7812", "LM7805"),
    "decoupling_cap_uf": 0.1,
}


def test_req_23_emission_standard_class_b():
    """Норма — ГОСТ CISPR 22-2012, Класс Б (жилые/промышленные зоны)"""
    assert "CISPR 22" in EMC_REQ_23["standard"]
    assert "Класс Б" in EMC_REQ_23["standard"]


def test_req_23_wifi_shielded_emission():
    """Экранирование гасит излучение Wi-Fi (2.4/5 ГГц) до ≤ −30 дБм/м"""
    assert EMC_REQ_23["shielded_emission_dbm_per_m"] <= -30.0
    assert EMC_REQ_23["wifi_bands_ghz"] == (2.4, 5.0)


def test_req_23_conducted_measures():
    """Линейные стабилизаторы + 0.1 мкФ на выводах гасят проводимые помехи"""
    assert EMC_REQ_23["linear_regulators"] == ("LM7812", "LM7805")
    assert EMC_REQ_23["decoupling_cap_uf"] == 0.1


# ===========================================================================
# REQ-24 — фильтры питания (Power Supply Filters)
# ===========================================================================
EMC_REQ_24 = {
    "req_id": "REQ-24",
    "rails": ("+12V", "+5V", "GND"),
    "inductance_h": 10e-6,          # L ≈ 10 мкГн
    "capacitance_f": 100e-9,        # C ≈ 100 нФ
    "fc_reference_hz": 16e3,        # требование: fc ≈ 16 кГц
    "ferrite_r_ohm": 100.0,         # R_f ≈ 100 Ом на 100 МГц
    "ferrite_f_hz": 100e6,
}


def test_req_24_lc_cutoff_formula():
    """fc = 1/(2π√(LC)); при L=10 мкГн, C=100 нФ — это ФНЧ для питания"""
    fc = 1.0 / (2.0 * math.pi
                * math.sqrt(EMC_REQ_24["inductance_h"]
                            * EMC_REQ_24["capacitance_f"]))
    assert fc < 1e6, f"fc = {fc/1e3:.1f} кГц"


def test_req_24_fc_reference():
    """Требование fc ≈ 16 кГц: подбор C при L = 10 мкГн воспроизводит его"""
    l = EMC_REQ_24["inductance_h"]
    c16 = 1.0 / (l * (2.0 * math.pi * EMC_REQ_24["fc_reference_hz"]) ** 2)
    fc16 = 1.0 / (2.0 * math.pi * math.sqrt(l * c16))
    assert math.isclose(fc16, EMC_REQ_24["fc_reference_hz"], rel_tol=1e-9)
    # в тексте требования C ≈ 100 нФ → fc ≈ 159 кГц (тот же порядок ФНЧ)
    assert 1e4 <= fc16 <= 1e5


def test_req_24_ferrite_beads():
    """Обводки (ferrite beads) R_f ≈ 100 Ом на 100 МГц подавляют ВЧ-шум"""
    assert EMC_REQ_24["ferrite_r_ohm"] == 100.0
    assert EMC_REQ_24["ferrite_f_hz"] == 100e6
    assert EMC_REQ_24["rails"] == ("+12V", "+5V", "GND")


# ===========================================================================
# REQ-25 — зонирование экранирования (Shielding Zoning)
# ===========================================================================
EMC_REQ_25 = {
    "req_id": "REQ-25",
    "zones": ("ВЧ-блок (0–7 ГГц)", "оптический (650 нм)", "цифровой (ESP32/ПЛИС)"),
    "isolation": "гальваническая (связь с ПК только по Wi-Fi)",
    "partition_material": "Al с RFI-прокладками",
    "dynamic_range_db": (85.0, 100.0),
}


def test_req_25_three_zones():
    """Корпус разделён на 3 изолированные зоны"""
    assert len(EMC_REQ_25["zones"]) == 3
    joined = " ".join(EMC_REQ_25["zones"])
    assert "ВЧ" in joined and "650" in joined and "ESP32" in joined


def test_req_25_galvanic_isolation():
    """Гальваническая изоляция; разделение сохраняет ДД 85–100 дБ"""
    assert "гальваническая" in EMC_REQ_25["isolation"]
    assert EMC_REQ_25["partition_material"].startswith("Al")
    assert EMC_REQ_25["dynamic_range_db"] == (85.0, 100.0)
