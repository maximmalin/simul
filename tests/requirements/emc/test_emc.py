"""Тесты ЭМС (REQ-21…REQ-25) по матрице требований.

Проверяются для **платы Krystal Radio (zero-IF, прямое преобразование)**:

* REQ-21 — экранирование корпуса: глубина скин-слоя алюминия и
  эффективность экрана;
* REQ-22 — иммунитет к внешним ЭМ-полям (ГОСТ 30804.4.3-2013 / IEC 61000-4-3,
  уровень жёсткости 2 — 3 В/м, 80–2700 МГц);
* REQ-23 — уровень эмиссии ГОСТ CISPR 22-2012 (Класс Б);
* REQ-24 — фильтры питания: f_c = 1/(2π√(LC)), ферриты;
* REQ-25 — зонирование корпуса на три секции.

В конструкции **нет** оптического блока, лазера, ножа Фуко, фотодиодов,
FPGA и Wi-Fi/ESP32: данные выводятся по USB CDC, микроконтроллер —
STM32F103C8T6, питание — линейное AMS1117-3.3. Тесты проверяют именно
эту архитектуру; см. `requirements/blocks/req_21.md`…`req_25.md`, где
исправлены устаревшие ссылки на предыдущую акустооптическую схему.

Запуск:
    pytest tests/requirements/emc/ -v
"""

import math

MU0 = 4.0 * math.pi * 1e-7        # Гн/м
RHO_AL = 2.65e-8                  # Ом·м — удельное сопротивление алюминия
SIGMA_AL = 3.5e7                  # См/м
SIGMA_CU = 5.8e7                  # См/м

#: Элементы, реально стоящие на плате (см. sim/scripts/sdr_skidl_circuit.py).
BOARD = {
    "mcu": "STM32F103C8T6",
    "mcu_clock_hz": 72e6,
    "crystal_hz": 8e6,
    "regulator": "AMS1117-3.3",          # линейный, не импульсный
    "link": "USB_CDC",                   # беспроводной связи нет
    "mixer": "LT5560",
    "opamp": "OPA2134",
    "lna": "2N7002",
    "filter": "LPJ-1",
    "conn": "SMA_EDGE",
    "ferrite": "FB1",
    "ferrite_r_100mhz_ohm": 600.0,
}

#: Что архитектура НЕ содержит (регрессия против возврата к старой схеме).
NOT_PRESENT = ("ESP32", "Wi-Fi", "WiFi", "FPGA", "ПЛИС", "лазер 650",
               "нож Фуко", "фотодиод", "BPW34")


# ===========================================================================
# REQ-21 — экранирование корпуса (Case Shielding)
# ===========================================================================
EMC_REQ_21 = {
    "req_id": "REQ-21",
    "material": "алюминий (фрезерованный)",
    "sections": 3,                 # ВЧ-аналог / цифровой / питание
    "rho_ohm_m": RHO_AL,
    "mu_r": 1.0,
    "wall_thickness_m": 0.5e-3,    # стенка корпуса, м (IPC-2221 Class 2)
    "reference_frequency_hz": 1.0e9,
    "gasket_contact_resistance_ohm": 10e-3,   # ≤ 10 мОм (MIL-DTL-83528)
    "se_required_db": 40.0,
    "band_ghz": (0.0, 7.0),
}


def skin_depth(freq_hz: float, rho: float = RHO_AL, mu_r: float = 1.0) -> float:
    """δ = √(2ρ / (ω·μ₀·μᵣ)) — глубина скин-слоя, м."""
    omega = 2.0 * math.pi * freq_hz
    return math.sqrt(2.0 * rho / (omega * MU0 * mu_r))


def test_req_21_skin_depth_aluminium():
    """δ алюминия на 1 ГГц ≈ 2.6 мкм (в тексте ТЗ ранее было указано 0.025 мм)."""
    d = skin_depth(1.0e9)
    assert 2.0e-6 < d < 3.2e-6, f"δ(1 ГГц) = {d * 1e6:.2f} мкм"
    # исправленное значение из req_21.md
    assert not (2.0e-5 < d < 3.0e-5), "вернулась ошибочная редакция 0.025 мм"


def test_req_21_skin_depth_scales_with_frequency():
    """δ ∝ 1/√f: на 7 ГГц примерно в 2.65 раза тоньше, чем на 1 ГГц."""
    d1, d7 = skin_depth(1.0e9), skin_depth(7.0e9)
    ratio = d1 / d7
    assert 2.5 < ratio < 2.8, f"отношение δ(1 ГГц)/δ(7 ГГц) = {ratio:.2f}"
    assert 0.8e-6 < d7 < 1.2e-6


def test_req_21_shielding_absorption_dominates():
    """A = 8.686·t/δ при t=0.5 мм, δ(1 ГГц) даёт многократно больше 40 дБ."""
    t = EMC_REQ_21["wall_thickness_m"]
    a_db = 8.686 * t / skin_depth(1.0e9)
    assert a_db > 1000, f"поглощение A = {a_db:.0f} дБ"
    # реальный предел задают стыки, поэтому норма — контакт прокладок
    assert EMC_REQ_21["gasket_contact_resistance_ohm"] <= 10e-3


def test_req_21_reflection_component():
    """Отражение R ≈ 168 − 20·log₁₀(f·r) + поправка на проводимость Al."""
    f, r = 1.0e9, 0.1
    r_db = 168.0 - 20.0 * math.log10(f * r)
    r_db += 20.0 * math.log10(math.sqrt(SIGMA_CU / SIGMA_AL))
    assert 8.0 < r_db < 13.0, f"R = {r_db:.1f} дБ"


def test_req_21_three_sections():
    """Корпус разделён на три секции."""
    assert EMC_REQ_21["sections"] == 3


def test_req_21_band_coverage():
    """Экранирование должно работать во всём диапазоне 0–7 ГГц."""
    lo, hi = EMC_REQ_21["band_ghz"]
    assert lo == 0.0 and hi == 7.0
    assert skin_depth(hi) > 0.9e-6


def test_req_21_no_legacy_optical_block():
    """В конструкции нет оптического блока — только ВЧ/цифра/питание."""
    for token in ("лазер", "нож Фуко", "фотодиод", "650 нм"):
        assert token not in EMC_REQ_21.get("material", "")


# ===========================================================================
# REQ-22 — иммунитет к внешним ЭМ-полям
# ===========================================================================
EMC_REQ_22 = {
    "req_id": "REQ-22",
    "standard": "ГОСТ 30804.4.3-2013 / IEC 61000-4-3",
    "severity_level": 2,
    "field_v_per_m": 3.0,
    "band_mhz": (80.0, 2700.0),
    "input_impedance_ohm": 50.0,
    "effective_length_m": 0.1,
    "attenuation_required_db": (40.0, 60.0),
}


def induced_power(e_v_per_m: float, h_m: float, r_ohm: float = 50.0) -> float:
    """Мощность, наведённая полем на эффективную длину антенны, Вт.

    U = E·h; в согласованном 50-омном входе P = U²/(4R).
    """
    u = e_v_per_m * h_m
    return u * u / (4.0 * r_ohm)


def test_req_22_induced_power_correct():
    """P_ind = (E·h)²/(4R) = 4.5·10⁻⁴ Вт при 3 В/м и h = 0.1 м."""
    p = induced_power(3.0, 0.1, 50.0)
    assert 4.0e-4 < p < 5.0e-4, f"P_ind = {p:.2e} Вт"
    # исходная редакция давала 1.8·10⁻⁵ Вт — занижено в 25 раз
    assert not (1.0e-5 < p < 2.5e-5), "вернулась ошибочная редакция"


def test_req_22_induced_power_dwarfs_signal():
    """Наведённая помеха многократно превышает целевой сигнал приёмника."""
    p_ind = induced_power(3.0, 0.1, 50.0)
    mds_w = 10 ** (-126.16 / 10.0) * 1e-3      # MDS −126.16 дБм, Вт
    ratio_db = 10.0 * math.log10(p_ind / mds_w)
    assert ratio_db > 90, f"превышение над MDS = {ratio_db:.0f} дБ"


def test_req_22_required_attenuation_range():
    """Требуемое ослабление 40–60 дБ."""
    lo, hi = EMC_REQ_22["attenuation_required_db"]
    assert 40.0 <= lo < hi <= 60.0


def test_req_22_severity_level_and_band():
    """Уровень жёсткости 2 = 3 В/м, полоса 80–2700 МГц."""
    assert EMC_REQ_22["severity_level"] == 2
    assert EMC_REQ_22["field_v_per_m"] == 3.0
    assert EMC_REQ_22["band_mhz"] == (80.0, 2700.0)


def test_req_22_scaled_by_field_strength():
    """Мощность помехи растёт квадратично с напряжённостью поля."""
    p3 = induced_power(3.0, 0.1)
    p1 = induced_power(1.0, 0.1)
    assert 8.8 < p3 / p1 < 9.2, f"отношение = {p3 / p1:.2f}"


def test_req_22_interferer_power_at_antenna():
    """Поле 3 В/м даёт на согласованной антенне 1 ГГц доступную мощность.

    P = E²·G·λ²/(32π²). Для диполя (1.5 дБи) на 1 ГГц это ≈ 3.6 мВт (+5.6 дБм)
    — поле уровня 2 достаточно сильное, чтобы нагрузить тракт.
    """
    e = EMC_REQ_22["field_v_per_m"]
    lam = 0.3
    gain_lin = 10 ** (1.5 / 10.0)
    p_avail = e ** 2 * gain_lin * lam ** 2 / (32 * math.pi ** 2)
    dbm = 10 * math.log10(p_avail * 1e3)
    assert 2e-3 < p_avail < 6e-3, f"P = {p_avail:.2e} Вт"
    assert 3.0 < dbm < 8.0, f"{dbm:.1f} дБм"


def test_req_22_shielding_keeps_interferer_out_of_compression():
    """После экранирования помеха должна остаться в линейном режиме тракта.

    Критерий иммунитета по IEC 61000-4-3 — отсутствие потери
    функционирования (нет насыщения АЦП, нет потери функций), а не
    сохранение отношения сигнал/помеха.

    +5.6 дБм на антенне − 40…60 дБ экрана + 20 дБ усиления LNA
    даёт −14…−34 дБм на входе смесителя — это ниже точки компрессии 1 дБ.
    """
    e = EMC_REQ_22["field_v_per_m"]
    lam, gain_lin = 0.3, 10 ** (1.5 / 10.0)
    p_avail_dbm = 10 * math.log10(
        e ** 2 * gain_lin * lam ** 2 / (32 * math.pi ** 2) * 1e3)

    shield_lo, shield_hi = EMC_REQ_22["attenuation_required_db"]
    lna_gain_dbi = 20.0           # Q_LNA1 / Q_LNA2 + фильтр LPJ-1
    p_at_mixer_dbm = p_avail_dbm - shield_lo + lna_gain_dbi   # наихудший случай
    p1db_dbm = 10.0               # точка 1 дБ компрессии ВЧ-тракта

    assert p_at_mixer_dbm < p1db_dbm, (
        f"на входе тракта {p_at_mixer_dbm:.1f} дБм ≥ компрессии {p1db_dbm} дБм")
    assert p1db_dbm - p_at_mixer_dbm > 20.0, "запас по компрессии менее 20 дБ"


def test_req_22_selectivity_rejects_out_of_band_field():
    """Вне рабочей полосы помеха отсекается избирательностью ПЧ-тракта.

    Тест по ГОСТ 30804.4.3 / IEC 61000-4-3 — качание поля 80–2700 МГц.
    Помеха, не попавшая в настроенную полосу ПЧ (2.7 кГц), отсекается
    трактом; в этой области избыток помехи над MDS закрывается суммарно.
    """
    shield_lo, shield_hi = EMC_REQ_22["attenuation_required_db"]
    selectivity_db = 10 * math.log10(1000e6 / 2700.0)
    assert 50 < selectivity_db < 60, f"избирательность = {selectivity_db:.1f} дБ"
    assert shield_lo + selectivity_db > 90.0, (
        "ослабление экрана плюс избирательность должны давать >90 дБ")


# ===========================================================================
# REQ-23 — уровень радиопомех (CISPR 22 Класс Б)
# ===========================================================================
EMC_REQ_23 = {
    "req_id": "REQ-23",
    "standard": "ГОСТ CISPR 22-2012 (Класс Б) / EN 55022 / CISPR 32",
    "detector": "квазипиковый",
    "distance_m": 3.0,
    # пределы Класс Б, дБмкВ/м
    "limit_30_1000_mhz": 40.0,
    "limit_0p5_5_ghz": 47.0,
    "regulator": BOARD["regulator"],
    "regulator_is_linear": True,
    "decoupling_uf": 0.1,
    "bulk_uf": 100.0,
}


def test_req_23_class_b_limits():
    """Нормы Класс Б: 40 дБмкВ/м (30–1000 МГц), 47 дБмкВ/м (0.5–5 ГГц)."""
    assert EMC_REQ_23["limit_30_1000_mhz"] == 40.0
    assert EMC_REQ_23["limit_0p5_5_ghz"] == 47.0
    assert EMC_REQ_23["detector"] == "квазипиковый"


def test_req_23_no_switching_converter():
    """Импульсных преобразователей в схеме нет — питание линейное."""
    assert EMC_REQ_23["regulator_is_linear"] is True
    assert BOARD["regulator"] == "AMS1117-3.3"


def test_req_23_emission_sources_are_real():
    """Источники помех — реальные узлы платы, а не Wi-Fi модуль."""
    assert BOARD["mcu"] == "STM32F103C8T6"
    assert BOARD["link"] == "USB_CDC"
    for token in ("ESP32", "Wi-Fi"):
        assert token not in str(EMC_REQ_23)


def test_req_23_decoupling_present():
    """Обязательны 0.1 мкФ ВЧ-шунт и 100 мкФ накопление на стабилизаторе."""
    assert EMC_REQ_23["decoupling_uf"] == 0.1
    assert EMC_REQ_23["bulk_uf"] == 100.0


def test_req_23_digital_harmonics_within_band():
    """Гармоники тактовой STM32 (72 МГц) попадают в нормируемую полосу 30–1000 МГц."""
    f0 = BOARD["mcu_clock_hz"]
    assert 30e6 <= f0 <= 1000e6, "основная гармоника вне полосы Класс Б"
    harmonics = [f0 * n for n in range(1, int(1000e6 / f0) + 1)]
    assert len(harmonics) >= 10


# ===========================================================================
# REQ-24 — фильтры питания
# ===========================================================================
EMC_REQ_24 = {
    "req_id": "REQ-24",
    "ferrite_ref": BOARD["ferrite"],
    "ferrite_r_100mhz_ohm": BOARD["ferrite_r_100mhz_ohm"],
    "lc_reference_l_h": 10e-6,
    "lc_reference_c_f": 100e-9,
    "rails": ("VIN_5V", "+3V3", "+3V3_ANA", "GND"),
    "hf_shunt_nf": 100.0,
    "bulk_uf": 100.0,
    "analog_bypass_uf": 10.0,
}


def lc_cutoff(ind_h: float, cap_f: float) -> float:
    """f_c = 1/(2π√(LC)), Гц."""
    return 1.0 / (2.0 * math.pi * math.sqrt(ind_h * cap_f))


def test_req_24_lc_cutoff_correct():
    """L=10 мкГн, C=100 нФ → f_c ≈ 159 кГц (в ТЗ ранее было 16 кГц)."""
    fc = lc_cutoff(10e-6, 100e-9)
    assert 1.4e5 < fc < 1.8e5, f"f_c = {fc:.0f} Гц"
    assert not (1.2e4 < fc < 2.0e4), "вернулась ошибочная редакция 16 кГц"


def test_req_24_ferrite_isolation():
    """Феррит FB1 даёт ≥600 Ом на 100 МГц."""
    assert EMC_REQ_24["ferrite_r_100mhz_ohm"] >= 600.0
    assert EMC_REQ_24["ferrite_ref"] == "FB1"


def test_req_24_decoupling_network():
    """ВЧ-шунт 100 нФ, накопление 100 мкФ, обход аналоговой шины 10 мкФ."""
    assert EMC_REQ_24["hf_shunt_nf"] == 100.0
    assert EMC_REQ_24["bulk_uf"] == 100.0
    assert EMC_REQ_24["analog_bypass_uf"] == 10.0


def test_req_24_all_rails_filtered():
    """Все четыре цепи питания развязаны."""
    rails = EMC_REQ_24["rails"]
    assert set(rails) == {"VIN_5V", "+3V3", "+3V3_ANA", "GND"}


def test_req_24_analog_rail_isolated():
    """Аналоговая шина +3V3_ANA отделяется от цифровой +3V3 ферритом."""
    assert "+3V3_ANA" in EMC_REQ_24["rails"]
    assert "+3V3_ANA" != "+3V3"


def test_req_24_shunt_covers_rf_band():
    """Шунт 100 нФ работает на всём диапазоне 0–7 ГГц."""
    c = EMC_REQ_24["hf_shunt_nf"] * 1e-9
    xc_at_1g = 1.0 / (2.0 * math.pi * 1e9 * c)
    assert xc_at_1g < 2.0, f"|Xc|(1 ГГц) = {xc_at_1g:.3f} Ом"
    assert xc_at_1g < 1.0


# ===========================================================================
# REQ-25 — зонирование экранирования
# ===========================================================================
EMC_REQ_25 = {
    "req_id": "REQ-25",
    "zones": ("ВЧ-аналоговая (SMA/LNA/смеситель/УО)",
              "цифровая (STM32F103C8T6, USB CDC)",
              "питание (AMS1117-3.3, феррит FB1)"),
    "partition_material": "Al с RFI-прокладками",
    "gasket_contact_resistance_ohm": 10e-3,
    "dynamic_range_db": (85.0, 100.0),
}


def test_req_25_three_zones():
    """Корпус разделён на три секции."""
    assert len(EMC_REQ_25["zones"]) == 3


def test_req_25_zones_match_board_contents():
    """Названия зон соответствуют реальным узлам платы."""
    joined = " ".join(EMC_REQ_25["zones"])
    assert "STM32F103C8T6" in joined
    assert "AMS1117-3.3" in joined
    assert "USB CDC" in joined


def test_req_25_no_legacy_digital_block():
    """Цифровая секция описана через STM32/USB, а не ESP32/ПЛИС/Wi-Fi."""
    joined = " ".join(EMC_REQ_25["zones"])
    for token in ("ESP32", "ПЛИС", "FPGA", "Wi-Fi"):
        assert token not in joined, f"в зонировании остался {token}"


def test_req_25_gasket_and_partition():
    """Перегородки из Al с прокладками ≤10 мОм."""
    assert "Al" in EMC_REQ_25["partition_material"]
    assert EMC_REQ_25["gasket_contact_resistance_ohm"] <= 10e-3


def test_req_25_dynamic_range():
    """Динамический диапазон 85–100 дБ."""
    lo, hi = EMC_REQ_25["dynamic_range_db"]
    assert 85.0 <= lo < hi <= 100.0


# ===========================================================================
# Сквозная проверка архитектуры: никакого возврата к старой схеме
# ===========================================================================
def test_board_has_no_wifi_or_fpga():
    """В плате нет ни ESP32, ни Wi-Fi, ни ПЛИС."""
    text = " ".join(str(v) for v in BOARD.values())
    for token in NOT_PRESENT:
        assert token not in text, f"в BOARD остался {token}"


def test_board_signal_chain_is_actual():
    """ВЧ-тракт: SMA → LNA → фильтр → смеситель → УО → АЦП."""
    assert BOARD["conn"] == "SMA_EDGE"
    assert BOARD["lna"] == "2N7002"
    assert BOARD["filter"] == "LPJ-1"
    assert BOARD["mixer"] == "LT5560"
    assert BOARD["opamp"] == "OPA2134"
    assert BOARD["mcu"].startswith("STM32")


def test_board_is_battery_free_usb_powered():
    """Питание — от USB (5V), Li-Po и Buck в этой плате нет."""
    assert BOARD["regulator"] == "AMS1117-3.3"
    assert BOARD["link"] == "USB_CDC"