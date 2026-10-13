#!/usr/bin/env python3
"""
Блок 0 — расчет 5V шины / Buck-преобразователя / LC-фильтров
Bragg cell: 7.4V (2S Li-Ion) -> 5.0V Bus для ESP32 SDR
"""
import json
import math
import os

# =============================================================================
# ВХОДНЫЕ ПАРАМЕТРЫ
# =============================================================================

# Батарея
BATTERY_NS = 2           # количество элементов
BATTERY_V_NOMINAL = 7.4  # номинальное напряжение (V)
BATTERY_V_MIN = 6.0      # минимальное (разряд)
BATTERY_V_MAX = 8.4      # максимальное (полный заряд)
BATTERY_CAPACITY_MAH = 2500  # емкость элемента (mAh)

# Нагрузка
LOAD_VOUT = 5.0          # целевое напряжение (V)
LOAD_ICONTINUOUS = 0.5   # непрерывный ток (A)
LOAD_IPEAK = 1.5         # пиковый ток (A)
LOAD_MARGIN = 1.2        # запас по току

# Buck-преобразователь
BUCK_F_SW = 300e3        # частота переключения (Гц)
BUCK_L_MIN_RATIO = 0.3   # минимальный коэффициент L (от Iout_max)

# LC-фильтр (для чувствительных узлов)
LC_FILTER_TARGET_ATTEN = 40.0  # целевое затухение (дБ) на частоте пульсаций

# Параметры компонентов
R_ESR_CAP_REALISTIC = 0.050    # ESR конденсатора (Ом) — типичное значение
R_ESR_IN_REALISTIC = 0.020     # ESR индуктивности (Ом)

# =============================================================================
# 1. РАСЧЕТ БАТАРЕИ
# =============================================================================

total_capacity_mAh = BATTERY_NS * BATTERY_CAPACITY_MAH
total_energy_Wh = BATTERY_V_NOMINAL * total_capacity_mAh / 1000.0
total_capacity_Ah = total_capacity_mAh / 1000.0

# Оценка времени работы при Typ모적인 нагрузке 500mA
typical_power_consumption_W = LOAD_VOUT * LOAD_ICONTINUOUS
estimated_runtime_hours = (BATTERY_V_NOMINAL * total_capacity_mAh / 1000.0) / typical_power_consumption_W
estimated_runtime_hours *= 0.85  # учет КПД Buck

print("=== 1. BATTERY ===")
print(f"  Battery: 2S 18650 Li-Ion, {BATTERY_CAPACITY_MAH} mAh each")
print(f"  Total capacity: {total_capacity_mAh} mAh ({total_capacity_Ah:.3f} Ah)")
print(f"  Total energy: {total_energy_Wh:.2f} Wh")
print(f"  Nominal voltage: {BATTERY_V_NOMINAL} V")
print(f"  Voltage range: {BATTERY_V_MIN} - {BATTERY_V_MAX} V")
print(f"  Typical load: {LOAD_VOUT} V @ {LOAD_ICONTINUOUS} A = {typical_power_consumption_W} W")
print(f"  Estimated runtime: {estimated_runtime_hours:.2f} h")
print()

# =============================================================================
# 2. DВОИЧНЫЙ РАСЧЕТ BUCK-КОНВЕРТА
# =============================================================================

# Коэффициент преобразования
D_NOM = LOAD_VOUT / BATTERY_V_NOMINAL
print(f"=== 2. BUCK CONVERTER ===")
print(f"  Target Vout: {LOAD_VOUT} V")
print(f"  Input range: {BATTERY_V_MIN} - {BATTERY_V_MAX} V")
print(f"  Nominal duty cycle (D): {D_NOM:.4f} ({D_NOM*100:.1f} %)")
print(f"  Dmax (at Vin_min=6.0V): {LOAD_VOUT/BATTERY_V_MIN:.4f} ({LOAD_VOUT/BATTERY_V_MIN*100:.1f} %)")
print(f"  Dmin (at Vin_max=8.4V): {LOAD_VOUT/BATTERY_V_MAX:.4f} ({LOAD_VOUT/BATTERY_V_MAX*100:.1f} %)")
print()

# Проверка: D должно быть < 1
if LOAD_VOUT / BATTERY_V_MIN >= 1.0:
    print("  ERROR: Vout >= Vin_min! Buck не работает в этом режиме.")
    exit(1)

# =============================================================================
# 3. РАСЧЕТ ИНДУКТИВНОСТИ L
# =============================================================================

# Пиковый ток индуктивности
I_peak = LOAD_IPEAK * LOAD_MARGIN
I_ripple_target = 0.2 * I_peak  # желаемый пульсации тока 20% от пикового

# ΔI_L = (Vin - Vout) * D / (L * f_sw)
# L = (Vin - Vout) * D / (ΔI_L * f_sw)

L_nom = (BATTERY_V_NOMINAL - LOAD_VOUT) * D_NOM / (I_ripple_target * BUCK_F_SW)
print(f"  Load: {LOAD_ICONTINUOUS} A continuous, {LOAD_IPEAK} A peak (margin x{LOAD_MARGIN})")
print(f"  I_ripple target: {I_ripple_target*1000:.1f} mA (20% of I_peak)")
print(f"  Switching frequency: {BUCK_F_SW/1e3:.0f} kHz")
print(f"  Nominal L: {L_nom*1e6:.2f} µH")
print()

# Проверка по минимальному входному напряжению (наихудший случай)
D_min_input = LOAD_VOUT / BATTERY_V_MIN
L_for_min_input = (BATTERY_V_MIN - LOAD_VOUT) * D_min_input / (I_ripple_target * BUCK_F_SW)
print(f"  L at Vin_min ({BATTERY_V_MIN} V): {L_for_min_input*1e6:.2f} µH")
print(f"  Recommended L: min({L_nom*1e6:.2f}, {L_for_min_input*1e6:.2f}) = {min(L_nom*1e6, L_for_min_input*1e6):.2f} µH")
L_selected = min(L_nom, L_for_min_input)
print(f"  Selected L: {L_selected*1e6:.2f} µH")

# =============================================================================
# 4. РАСЧЕТ КОНДЕНСАТОРА Cout
# =============================================================================

# Пульсации напряжения конденсатора (основной вклад от ESR + ripple current)
# ΔVc = ΔI_L / (8 * Cout * f_sw)  (для шагового режима, основная часть)
Cout_ideal = I_ripple_target / (8.0 * LOAD_VOUT * BUCK_F_SW * 0.05)  # 50mV p-p target
print(f"\n=== 3. OUTPUT CAPACITOR ===")
print(f"  Target V ripple: < 50 mV p-p")
print(f"  Cout_ideal (50mV p-p from ΔVc): {Cout_ideal*1e6:.1f} µF")
print()

# Учет ESR
delta_v_esr = I_ripple_target * R_ESR_CAP_REALISTIC
print(f"  ESR contribution to V ripple: {delta_v_esr*1000:.1f} mV (ESR={R_ESR_CAP_REALISTIC*1000:.0f} mOhm)")
Cout_with_esr = I_ripple_target / (8.0 * LOAD_VOUT * BUCK_F_SW * (0.05 - delta_v_esr))
print(f"  Cout considering ESR: {Cout_with_esr*1e6:.1f} µF")
Cout_selected = max(Cout_ideal, Cout_with_esr)
print(f"  Selected Cout: {Cout_selected*1e6:.1f} µF")
print()

# =============================================================================
# 5. ПРОВЕРКА КПД И ПОТЕРЬ
# =============================================================================

# Оценка КПД (типичный для Buck 85-95%)
efficiency_estimate = 0.90
I_in_nominal = (LOAD_VOUT * LOAD_ICONTINUOUS) / (BATTERY_V_NOMINAL * efficiency_estimate)
P_input = BATTERY_V_NOMINAL * I_in_nominal
P_output = LOAD_VOUT * LOAD_ICONTINUOUS
P_loss = P_input - P_output

print(f"=== 4. EFFICIENCY ===")
print(f"  Efficiency estimate: {efficiency_estimate*100:.0f}%")
print(f"  Output power: {P_output:.2f} W")
print(f"  Input power: {P_input:.2f} W")
print(f"  Losses: {P_loss:.2f} W")
print(f"  Input current (nominal): {I_in_nominal:.2f} A")
print(f"  Input current (at Vin_min): {P_input/BATTERY_V_MIN:.2f} A")
print(f"  Input current (at Vin_max): {P_input/BATTERY_V_MAX:.2f} A")
print()

# =============================================================================
# 6. LC-ФИЛЬТР ДЛЯ ЧУВСТИТЕЛЬНЫХ УЗЛОВ
# =============================================================================

# Настройка LC-фильтра для дополнительного гасяния пульсаций
f_ripple = BUCK_F_SW  # основная частота пульсаций
L_filter = 10e-6      # 10 µH (小型 inductor)
C_filter = 100e-6     # 100 µF

f_cutoff = 1.0 / (2.0 * math.pi * math.sqrt(L_filter * C_filter))
attenuation_at_f_ripple = 20.0 * math.log10(1.0 / math.sqrt(1.0 + (f_ripple/f_cutoff)**4))

print(f"=== 5. LC FILTER (SENSITIVE NODES) ===")
print(f"  L_filter: {L_filter*1e6:.0f} µH")
print(f"  C_filter: {C_filter*1e6:.0f} µF")
print(f"  Corner frequency: {f_cutoff/1e3:.1f} kHz")
print(f"  Attenuation at f_sw ({BUCK_F_SW/1e3:.0f} kHz): {attenuation_at_f_ripple:.1f} dB")
print()

# =============================================================================
# 7. СОХРАНЕНИЕ РЕЗУЛЬТАТОВ
# =============================================================================

results = {
    "block": "block0_bus_power",
    "battery": {
        "type": "2S 18650 Li-Ion",
        "voltage_nominal": BATTERY_V_NOMINAL,
        "voltage_min": BATTERY_V_MIN,
        "voltage_max": BATTERY_V_MAX,
        "capacity_mAh_per_cell": BATTERY_CAPACITY_MAH,
        "total_capacity_mAh": total_capacity_mAh,
        "total_energy_Wh": total_energy_Wh,
        "estimated_runtime_hours": estimated_runtime_hours
    },
    "buck": {
        "vout_target": LOAD_VOUT,
        "f_sw_hz": BUCK_F_SW,
        "duty_cycle_nominal": D_NOM,
        "duty_cycle_min": LOAD_VOUT/BATTERY_V_MAX,
        "duty_cycle_max": LOAD_VOUT/BATTERY_V_MIN,
        "inductance_um": L_selected * 1e6,
        "output_capacitance_uF": Cout_selected * 1e6,
        "ripple_current_mA": I_ripple_target * 1000,
        "efficiency_estimate": efficiency_estimate,
        "output_power_W": P_output,
        "input_power_W": P_input,
        "losses_W": P_loss
    },
    "lc_filter": {
        "L_uH": L_filter * 1e6,
        "C_uF": C_filter * 1e6,
        "f_cutoff_kHz": f_cutoff / 1e3,
        "attenuation_at_fsw_dB": attenuation_at_f_ripple
    },
    "input_current_range": {
        "min": P_input / BATTERY_V_MAX,
        "nominal": I_in_nominal,
        "max": P_input / BATTERY_V_MIN
    }
}

output_dir = "/home/mirrage/brig-receiver/results"
os.makedirs(output_dir, exist_ok=True)
output_path = os.path.join(output_dir, "block0_bus_power_results.json")
with open(output_path, 'w') as f:
    json.dump(results, f, indent=2, ensure_ascii=False)

print(f"=== 6. RESULTS SAVED ===")
print(f"  File: {output_path}")
print()

print("========================================================================")
print("SUMMARY")
print("========================================================================")
print(f"  Battery:   {BATTERY_V_NOMINAL}V {total_capacity_mAh}mAh (2S 18650)")
print(f"  Buck:      {LOAD_VOUT}V @ {LOAD_ICONTINUOUS}A, f_sw={BUCK_F_SW/1e3:.0f}kHz")
print(f"  L:         {L_selected*1e6:.1f} µH")
print(f"  Cout:      {Cout_selected*1e6:.1f} µF")
print(f"  Eff:       {efficiency_estimate*100:.0f}%")
print(f"  Runtime:   {estimated_runtime_hours:.1f} h")
print(f"  LC filter: {L_filter*1e6:.0f} µH + {C_filter*1e6:.0f} µF, {attenuation_at_f_ripple:.1f} dB at f_sw")
print("========================================================================")