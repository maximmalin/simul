% antenna_system_calc.m - extracted from calculations/antenna_system_calc.ipynb
% End-to-end Brig Receiver calculation (antenna + RF path + acousto-optics).
% Run with:  octave sim/octave/antenna_system_calc.m

# =============================================================================
# СКВЗНОЙ РАСЧЁТ Brig Receiver — чистый Octave (без numpy)
# =============================================================================

# ---- Физические константы ----
# `global` so the helper functions below (defined in this script) can see them;
# a plain script variable is not visible inside a function's own scope.
global C PI;
C = 299792458;                # скорость света м/с
PI = pi;                      # число пи

# ---- Параметры материалов ----
epsilon_frd4 = 4.5;           # диэлектрическая проницаемость FR-4
epsilon_ro4350b = 3.48;       # Rogers RO4350B (для >=5 ГГц)

# ---- Параметры антенной системы (REQ-A1, REQ-B1, REQ-C1) ----
panel_size = 150;             # мм
substrate_h = 0.8;            # мм (толщина FR-4)
gcpw_width = 1.2;             # мм
gcpw_gap = 0.2;               # мм
gcpw_impedance = 50;          # Ом

# ---- Параметры акустооптики (PMMA / TOСП) ----
n_pmma = 1.49;                # показатель преломления
v_sound_pmma = 2700.0;        # м/с
rho_pmma = 1190.0;            # кг/м³
M2_pmma = 1.05e-15;           # с³/кг
L_interact = 0.020;           # м (длина взаимодействия)
H_block = 0.005;              # м (высота бруска)
L_arm = 0.100;                # м (плечо скамьи)
f_IF = 38.0e6;                # Гц (промежуточная частота)

# ---- Параметры лазера ----
lambda_laser = 650e-9;        # м (650 нм)
P_laser = 5.0e-3;             # Вт
R_lambda = 0.4;               # А/В (спектральная чувствительность)

# ---- Пределы безопасности ----
P_kvarz_max = 1.0e-3;          # Вт (максимальная мощность на кварце)

fprintf('Константы загружены.\n');
fprintf('  Скорость света: %d м/с\n', C);
fprintf('  Частота ПЧ: %.0f МГц\n\n', f_IF/1e6);

# =============================================================================
# ФУНКЦИИ
# =============================================================================

## Эффективная диэлектрическая проницаемость микрополосковой линии
## ε_eff = (ε_r+1)/2 + (ε_r-1)/2 * (1 + 12*h/w)^(-1/2)
function eps_eff = epsilon_eff_microstrip(epsilon_r, h_mm, w_mm)
    ratio = 12.0 * h_mm / w_mm;
    eps_eff = (epsilon_r + 1) / 2 + (epsilon_r - 1) / 2 * (1 + ratio) ^ (-0.5);
endfunction

## Длина змейки для заданной частоты
function [length_mm, lambda0_mm] = calc_meander_length(freq_mhz, epsilon_eff, num_meanders)
    global C PI;
    f_hz = freq_mhz * 1e6;
    lambda_0 = C / f_hz;
    lambda_eff = lambda_0 / sqrt(epsilon_eff);
    half_wave = lambda_eff / 2;
    length_mm = half_wave * 1000 * num_meanders;
    lambda0_mm = lambda_0 * 1000;
endfunction

## Размеры микрополоскового патча
function [W_mm, L_mm, eps_eff, delta_L_mm] = calc_patch_dimensions(f0_hz, epsilon_r, h_mm)
    global C PI;
    W_m = C / (2 * f0_hz) * sqrt(2 / (epsilon_r + 1));
    W_mm = W_m * 1000;
    eps_eff = epsilon_eff_microstrip(epsilon_r, h_mm, W_mm);
    delta_L_mm = 0.412 * h_mm * (eps_eff + 0.3) * (W_mm/h_mm + 0.264) / ...
                 ((eps_eff - 0.258) * (W_mm/h_mm + 0.8));
    L_m = C / (2 * f0_hz * sqrt(eps_eff)) - 2 * delta_L_mm / 1000;
    L_mm = L_m * 1000;
endfunction

## Формула Фрииса
function [P_rec, lambda_rf, path_loss] = friis_received_power(P_tx_watts, G_tx_linear, G_rec_linear, f_hz, R_m)
    global C PI;
    lambda_rf = C / f_hz;
    path_loss = (lambda_rf / (4 * PI * R_m)) ^ 2;
    P_rec = P_tx_watts * G_tx_linear * G_rec_linear * path_loss;
endfunction

## Акустооптическая интенсивность
function I_ac = acoustic_intensity(P_rf_watts, eta_coupling, L_interact, H_block)
    P_acoustic = P_rf_watts * eta_coupling;
    S_area = L_interact * H_block;
    I_ac = P_acoustic / S_area;
endfunction

## Δn (изменение индекса преломления)
function dn = delta_n_acoustooptic(M2, rho, v_sound, I_ac)
    dn = sqrt(0.5 * M2 * rho * (v_sound ^ 3) * I_ac);
endfunction

## Эффективность Брэгговской дифракции
function eta_opt = bragg_efficiency(dn, L, lambda_laser)
    global C PI;
    eta_opt = (sin(PI * dn * L / lambda_laser)) ^ 2;
endfunction

## Угол дифракции Брэгга
function [theta_medium, theta_air] = bragg_angle(lambda_laser, n_medium, Lambda_ac)
    theta_medium = lambda_laser / (n_medium * Lambda_ac);
    theta_air = asin(n_medium * sin(theta_medium));
endfunction

## Параметр Клейна-Кука Q (критерий Брэгга)
function Q = bragg_Q_criterion(lambda_laser, L, n_medium, Lambda_ac)
    global C PI;
    Q = (2 * PI * lambda_laser * L) / (n_medium * Lambda_ac ^ 2);
endfunction

## Импеданс GCPW
function [Z_gcpw, Z_ms] = calc_gcpw_impedance(epsilon_r, h_mm, w_mm, g_mm)
    Z_ms = (60 / sqrt(epsilon_r)) * log(8*h_mm/w_mm + w_mm/(4*h_mm));
    g_ratio = g_mm / w_mm;
    correction = 1.0 + 0.5 * log(1 + 1/g_ratio);
    Z_gcpw = Z_ms * correction / sqrt(epsilon_r);
endfunction

# =============================================================================
# 1. АНТЕННА: КАНАЛ А (ЗМЕЙКА 0–900 МГЦ)
# =============================================================================

fprintf('============================================================\n');
fprintf('КАНАЛ А: Печатная змейка 0–900 МГц\n');
fprintf('============================================================\n');

epsilon_eff_a = (epsilon_frd4 + 1) / 2;

for freq = [433, 900]
    [length_mm, lambda_mm] = calc_meander_length(freq, epsilon_eff_a, 4);
    fprintf('  f = %d МГц: lambda0 = %.1f мм, длина змейки (4 звена) ≈ %.1f мм\n', freq, lambda_mm, length_mm);
end

fprintf('  ε_eff для змейки на FR-4 (0.8 мм): ≈ %.2f\n', epsilon_eff_a);
fprintf('  Панель: %dx%d мм, FR-4 %.1f мм\n', panel_size, panel_size, substrate_h);
fprintf('  GCPW: Z₀ = %d Ом, W = %.1f мм, зазор = %.1f мм\n\n', gcpw_impedance, gcpw_width, gcpw_gap);

# =============================================================================
# 2. АНТЕННА: КАНАЛ Б (МАТРИЦА 2×2, 2.45 ГГЦ)
# =============================================================================

fprintf('============================================================\n');
fprintf('КАНАЛ Б: Микрополосковая матрица 2×2, f₀ = 2.45 ГГц\n');
fprintf('============================================================\n');

f0_b = 2.45e9;
[W_b, L_b, eps_eff_b, dl_b] = calc_patch_dimensions(f0_b, epsilon_frd4, substrate_h);

fprintf('  Частота: %.2f ГГц\n', f0_b/1e9);
fprintf('  Материал: FR-4, ε_r = %.1f, h = %.1f мм\n', epsilon_frd4, substrate_h);
fprintf('  Ширина патча W = %.2f мм\n', W_b);
fprintf('  Длина патча L = %.2f мм\n', L_b);
fprintf('  ε_eff = %.3f\n', eps_eff_b);
fprintf('  Удлинение ΔL = %.3f мм\n', dl_b);

lambda_0_b = C / f0_b;
step_min = 0.7 * lambda_0_b * 1000;
step_max = 0.8 * lambda_0_b * 1000;
step_req = 91.7;

fprintf('  Шаг элементов: %.1f..%.1f мм (0.7..0.8 λ₀)\n', step_min, step_max);
fprintf('  Требуемый шаг: %.1f мм\n', step_req);
if (step_min <= step_req) && (step_req <= step_max)
    fprintf('  Шаг %.1f мм в пределах: YES\n', step_req);
else
    fprintf('  Шаг %.1f мм в пределах: NO\n', step_req);
endif
fprintf('  Матрица: 2×2 (4 элемента), панель %dx%d мм\n\n', panel_size, panel_size);

# =============================================================================
# 3. АНТЕННА: КАНАЛ В (МАТРИЦА 4×4, 5.8 ГГЦ)
# =============================================================================

fprintf('============================================================\n');
fprintf('КАНАЛ В: Микрополосковая матрица 4×4, f₀ = 5.8 ГГц\n');
fprintf('============================================================\n');

epsilon_c = epsilon_ro4350b;
f0_c = 5.8e9;

[W_c_ro4350, L_c_ro4350, eps_eff_c, dl_c] = calc_patch_dimensions(f0_c, epsilon_c, substrate_h);

fprintf('  Частота: %.2f ГГц\n', f0_c/1e9);
fprintf('  Материал: Rogers RO4350B, ε_r = %.2f, h = %.1f мм\n', epsilon_c, substrate_h);
fprintf('  Ширина патча W = %.2f мм\n', W_c_ro4350);
fprintf('  Длина патча L = %.2f мм\n', L_c_ro4350);
fprintf('  ε_eff = %.3f\n', eps_eff_c);

[W_c_frd4, L_c_frd4, eps_eff_frd4, dl_frd4] = calc_patch_dimensions(f0_c, epsilon_frd4, substrate_h);
fprintf('  (Для сравнения FR-4: W = %.2f мм, L = %.2f мм, ε_eff = %.3f)\n', W_c_frd4, L_c_frd4, eps_eff_frd4);

lambda_0_c = C / f0_c;
step_c = 0.75 * lambda_0_c * 1000;
fprintf('  Шаг элементов (0.75 λ₀): %.1f мм\n', step_c);
fprintf('  Матрица: 4×4 (16 элементов), панель %dx%d мм\n', panel_size, panel_size);
fprintf('  Экранирование: алюминий ≥1.5 мм\n');
fprintf('  Боковые лепестки: ≤-15 дБ\n\n');

# =============================================================================
# 4. GCPW: Копланарный волновод с заземлением
# =============================================================================

fprintf('============================================================\n');
fprintf('GCPW: Копланарный волновод с заземлением\n');
fprintf('============================================================\n');

[Z_gcpw, Z_ms] = calc_gcpw_impedance(epsilon_frd4, substrate_h, gcpw_width, gcpw_gap);

fprintf('  Параметры: ε_r = %.1f, h = %.1f мм\n', epsilon_frd4, substrate_h);
fprintf('  GCPW: W = %.1f мм, зазор g = %.1f мм\n', gcpw_width, gcpw_gap);
fprintf('  Расчётное Z₀ GCPW ≈ %.1f Ом\n', Z_gcpw);
fprintf('  (Микрополосковый Z₀ ≈ %.1f Ом, коррекция для coplanar)\n', Z_ms);
fprintf('  Требуемый импеданс: %d ± 10 Ом (REQ-A1) / ± 5 Ом (REQ-B1, REQ-C1)\n', gcpw_impedance);
fprintf('  Требуемый VSWR: ≤ 2.0 (REQ-A1, REQ-C1) / ≤ 1.5 (REQ-B1)\n\n');

# =============================================================================
# 5. RF-ТРАКТ: Фриисово передача
# =============================================================================

fprintf('============================================================\n');
fprintf('RF-тракт: Фриисово передача (REQ-01 сквозной расчёт)\n');
fprintf('============================================================\n');

P_tx = 1.0;
G_tx = 1.58;
G_rec = 10.0;
R = 100;

for f_mhz = [433, 900, 2450, 5800]
    f_hz = f_mhz * 1e6;
    [P_rec, lambda_rf, path_loss] = friis_received_power(P_tx, G_tx, G_rec, f_hz, R);
    if P_rec > 0
        P_rec_dbm = 10 * log10(P_rec * 1000);
    else
        P_rec_dbm = -Inf;
    endif
    fprintf('  f = %4d МГц: λ = %.2f мм, P_rec = %.2f мкВт = %.1f дБм\n', f_mhz, lambda_rf*1000, P_rec*1e6, P_rec_dbm);
end

fprintf('  P_tx = %.1f Вт (%.0f дБм)\n', P_tx, 10*log10(P_tx*1000));
fprintf('  G_tx = %.2f (%.1f дБи)\n', G_tx, 10*log10(G_tx));
fprintf('  G_rec = %.1f (%.0f дБи) — Cantenna 90 мм\n', G_rec, 10*log10(G_rec));
fprintf('  R = %d м\n\n', R);

# =============================================================================
# 6. КАСКАДНОЕ УСИЛЕНИЕ И ЗАЩИТА КВАРЦА
# =============================================================================

G_tuner_db = 25.0;
G_kt368_db = 18.0;
G_loss_db = 6.0;
G_total_db = G_tuner_db + G_kt368_db - G_loss_db;

G_tuner_lin = 10 ^ (G_tuner_db / 10);
G_kt368_lin = 10 ^ (G_kt368_db / 10);
G_total_lin = 10 ^ (G_total_db / 10);

fprintf('============================================================\n');
fprintf('Каскадное усиление и защита кварца\n');
fprintf('============================================================\n');

fprintf('  ТВ-селектор: %.1f дБ (G = %.1f)\n', G_tuner_db, G_tuner_lin);
fprintf('  УПЧ КТ368А: %.1f дБ (G = %.1f)\n', G_kt368_db, G_kt368_lin);
fprintf('  Потери диодов: %.1f дБ\n', G_loss_db);
fprintf('  Итого: %.1f дБ (G = %.1f)\n', G_total_db, G_total_lin);

P_rec_2g45 = 1.56e-7;
P_out = P_rec_2g45 * G_total_lin;

fprintf('\n  Входная мощность (2.45 ГГц, 100 м): %.2e Вт = %.1f дБм\n', P_rec_2g45, 10*log10(P_rec_2g45*1000));
fprintf('  Выходная мощность: %.2e Вт = %.1f дБм\n', P_out, 10*log10(P_out*1000));
fprintf('  Требуемая мощность на кварце: ≤ %.1f мВт\n', P_kvarz_max*1000);
if P_out > P_kvarz_max
    fprintf('  Предел безопасности: ПРЕВЫШЕНИЕ\n');
else
    fprintf('  Предел безопасности: В ПОРЯДКЕ\n');
endif

fprintf('\n  Защита: ограничительная вилка КД522 срезает P > %.1f мВт\n', P_kvarz_max*1000);
fprintf('  U_пп ≤ 0.63 В (два диода встречно-параллельно)\n\n');

# =============================================================================
# 7. АКУСТООПТИКА: Δn И ИНТЕНСИВНОСТЬ УЛЬТРАЗВУКА
# =============================================================================

fprintf('============================================================\n');
fprintf('Акустооптика: Δn и интенсивность ультразвука\n');
fprintf('============================================================\n');

eta_coupling = 0.3;
I_ac = acoustic_intensity(P_out, eta_coupling, L_interact, H_block);
dn = delta_n_acoustooptic(M2_pmma, rho_pmma, v_sound_pmma, I_ac);

fprintf('  P_rf_out = %.2e Вт\n', P_out);
fprintf('  η_coupling = %.1f\n', eta_coupling);
fprintf('  P_acoustic = %.2e Вт\n', P_out * eta_coupling);
fprintf('  S_area = %.4f м² (%.0f×%.0f мм)\n', L_interact * H_block, L_interact*1000, H_block*1000);
fprintf('  I_acoustic = %.1f Вт/м²\n', I_ac);
fprintf('  Δn = %.4e\n', dn);

dn_target = 1.51e-5;
fprintf('  Требуемый Δn ≈ %.4e (REQ-11)\n', dn_target);
fprintf('  Отклонение: %.1f%%\n\n', abs(dn - dn_target)/dn_target*100);

# =============================================================================
# 8. ДИФРАКЦИЯ БРЭГГА: η_opt, УГОЛ, ШАГ ФОТОДИОДОВ
# =============================================================================

fprintf('============================================================\n');
fprintf('Дифракция Брэгга: η_opt, угол, шаг фотодиодов\n');
fprintf('============================================================\n');

Lambda_ac = v_sound_pmma / f_IF;
eta_opt = bragg_efficiency(dn, L_interact, lambda_laser);
P_light_out = P_laser * eta_opt;

[theta_medium, theta_air] = bragg_angle(lambda_laser, n_pmma, Lambda_ac);
delta_x = L_arm * tan(theta_air);
delta_x_mm = delta_x * 1000;

fprintf('  Λ_ac = v / f_IF = %.2f мкм\n', Lambda_ac*1e6);
fprintf('  η_opt = sin²(π·%.4e·%.3f/%.1f нм) = %.4f\n', dn, L_interact, lambda_laser*1e9, eta_opt);
fprintf('  P_light_out = %.1f мВт × %.4f = %.2f мВт\n', P_laser*1000, eta_opt, P_light_out*1000);
fprintf('  θ_inside = %.4f°\n', theta_medium*180/PI);
fprintf('  θ_air (после Снеллиуса) = %.4f°\n', theta_air*180/PI);
fprintf('  Сдвиг на фотодиодах: Δx = %.0f мм × tan(%.4f°) = %.3f мм\n', L_arm*1000, theta_air*180/PI, delta_x_mm);

pitch_req = 0.915;
fprintf('  Требуемый шаг фотодиодов: %.3f мм\n', pitch_req);
fprintf('  Расчётный шаг: %.3f мм\n', delta_x_mm);
if abs(delta_x_mm - pitch_req)/pitch_req < 0.05
    fprintf('  Совпадение: YES\n');
else
    fprintf('  Совпадение: NO\n');
endif

# =============================================================================
# 9. КРИТЕРИЙ БРЭГГА Q (ПАРАМЕТР КЛЕЙНА-КУКА)
# =============================================================================

fprintf('\n============================================================\n');
fprintf('Критерий Брэгга (параметр Q)\n');
fprintf('============================================================\n');

Q = bragg_Q_criterion(lambda_laser, L_interact, n_pmma, Lambda_ac);

fprintf('  Q = 2π·%.1f нм·%.0f мм / (%.1f·(%.2f мкм)²)\n', lambda_laser*1e9, L_interact*1000, n_pmma, Lambda_ac*1e6);
fprintf('  Q = %.2f\n', Q);
if Q > 10
    fprintf('  Режим: БРЭГГ (Q > 10)\n');
else
    fprintf('  Режим: РЕЖИМ РЕНТГЕНА\n');
endif
if Q >= 10
    fprintf('  Требуемый Q ≥ 10 (REQ-11): ДОСТИГНУТО\n');
else
    fprintf('  Требуемый Q ≥ 10 (REQ-11): НЕ ДОСТИГНУТО\n');
endif

for lambda_nm = [532, 650]
    Q_test = bragg_Q_criterion(lambda_nm*1e-9, L_interact, n_pmma, Lambda_ac);
    fprintf('  При λ = %d нм: Q = %.2f\n', lambda_nm, Q_test);
end

# =============================================================================
# 10. СВОДКА
# =============================================================================

fprintf('\n======================================================================\n');
fprintf('СВОДКА: Результаты сквозного расчёта Brig Receiver\n');
fprintf('======================================================================\n');

fprintf('\n--- АНТЕННА ---\n');
fprintf('  Канал А (0–900 МГц):            змейка, FR-4, ε_eff ≈ %.2f\n', epsilon_eff_a);
fprintf('  Канал Б (2.45 ГГц):             2×2, W=%.1f мм, L=%.1f мм, шаг=%.1f мм\n', W_b, L_b, step_req);
fprintf('  Канал В (5.8 ГГц):              4×4, W=%.1f мм, L=%.1f мм (RO4350B)\n', W_c_ro4350, L_c_ro4350);
fprintf('  GCPW:                           Z₀ ≈ %.1f Ом (цель 50 Ом)\n', Z_gcpw);

fprintf('\n--- RF-ТРАКТ ---\n');
fprintf('  P_rec (2.45 ГГц, 100 м):       %e Вт = %.1f дБм\n', P_rec_2g45, 10*log10(P_rec_2g45*1000));
fprintf('  G_total:                        %.0f дБ (%.0f)\n', G_total_db, G_total_lin);
fprintf('  P_out (на кварц):               %e Вт = %.1f дБм\n', P_out, 10*log10(P_out*1000));
if P_out > P_kvarz_max
    fprintf('  Предел кварца:                  ≤ %.1f мВт — ПРЕВЫШЕНИЕ\n', P_kvarz_max*1000);
else
    fprintf('  Предел кварца:                  ≤ %.1f мВт — НОРМАЛЬНО\n', P_kvarz_max*1000);
endif

fprintf('\n--- АКУСТООПТИКА ---\n');
fprintf('  I_acoustic:                     %.1f Вт/м²\n', I_ac);
fprintf('  Δn:                             %.4e (цель %.4e)\n', dn, dn_target);
fprintf('  η_opt:                          %.4f (%.1f%%)\n', eta_opt, eta_opt*100);
fprintf('  θ_air:                          %.4f°\n', theta_air*180/PI);
fprintf('  Δx (шаг фотодиодов):            %.3f мм (цель %.3f мм)\n', delta_x_mm, pitch_req);
fprintf('  Q (Брэгг):                      %.2f (цель Q > 10)\n', Q);

fprintf('\n--- ФОТОДИОДЫ ---\n');
fprintf('  P_light_out:                    %.2f мВт\n', P_light_out*1000);
I_out_raw = P_light_out * R_lambda;
fprintf('  I_out:                          %.2f мА\n', I_out_raw*1000);
if I_out_raw > 0
    fprintf('  SNR (шум 100 нА):               %.1f дБ\n', 20*log10(I_out_raw/1e-7));
else
    fprintf('  SNR (шум 100 нА):               -100.0 дБ\n');
endif

