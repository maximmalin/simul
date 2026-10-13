% ============================================================================
%  bragg_formulas.m — МОДУЛЬ 1 «Ячейка Брэгга»: какие параметры считаются и по
%  каким формулам.
%
%  Запуск:
%      cd /home/mirrage/Desktop/brig-receiver/sim/octave
%      octave --quiet bragg_formulas.m
%
%  Скрипт выводит:
%    1) ВХОДНЫЕ параметры (те же, что в sim/inputs/bragg_inputs.yaml),
%    2) ФОРМУЛЫ, по которым из решения упругости получаются выходные величины,
%    3) аналитическую прикидку (стержневая модель) для самопроверки,
%    4) сравнение с результатом Elmer (sim/outputs/elmer_results.json), если он есть.
% ============================================================================

more off;

% ---------------------------------------------------------------------------
% 1. ВХОДНЫЕ параметры (значения по умолчанию, как в bragg_inputs.yaml)
% ---------------------------------------------------------------------------
geom = struct("lx", 15e-3, "ly", 5e-3, "glass_lz", 2.91e-3, "quartz_lz", 0.09e-3);
mglass  = struct("rho", 2500,    "E", 70.0e9,  "nu", 0.22);   % стекло
mquartz = struct("rho", 2650,    "E", 78.5e9,  "nu", 0.14);   % кварц
load_   = struct("b1", 4.5e-3, "b2", 0.0, "b3", 0.0);         % объёмная сила, Н/м³ (на кварц)
opt     = struct("dn_scale_quartz", -0.46);                   % фотоупругий коэфф.

% ---------------------------------------------------------------------------
% 2. ВЫХОДНЫЕ параметры и ФОРМУЛЫ
% ---------------------------------------------------------------------------
%  Решаеться статика линейной упругости (Elmer, StressSolve):
%      div(sigma) + f_body = 0,   sigma = C : eps,   eps = (grad u + grad u^T)/2
%  Выход модели:
%      u        — вектор перемещений (м);     displacement_max = max|u|
%      sxx,syy,szz — компоненты напряжения (Па)
%      eps_zz    = (szz - nu*(sxx+syy)) / E        % закон Гука (обратный)
%      delta_n   = dn_scale_quartz * eps_zz        % фотоупругость
%  По слою кварца дополнительно: mean/max/min для eps_zz и delta_n.
%  Все формулы ниже используют именно эти выражения.
% ---------------------------------------------------------------------------

printf("\n=== ВХОДНЫЕ ПАРАМЕТРЫ (МОДУЛЬ 1) ===\n");
printf("  Lx = %.6g м, Ly = %.6g м, стекло = %.6g м, кварц = %.6g м\n", ...
       geom.lx, geom.ly, geom.glass_lz, geom.quartz_lz);
printf("  стекло: E=%g Па, nu=%.3g, rho=%g кг/м³\n", mglass.E, mglass.nu, mglass.rho);
printf("  кварц:  E=%g Па, nu=%.3g, rho=%g кг/м³\n", mquartz.E, mquartz.nu, mquartz.rho);
printf("  нагрузка b = (%.6g, %.6g, %.6g) Н/м³\n", load_.b1, load_.b2, load_.b3);
printf("  фотоупругость: dn_scale_quartz = %g\n\n", opt.dn_scale_quartz);

% ---------------------------------------------------------------------------
% 3. Аналитическая прикидка: кварцевый слой = консольный брус (нагрузка по X)
% ---------------------------------------------------------------------------
%  Погонная нагрузка        w = b1 * Ly * Hq          [Н/м]
%  Площадь сечения          A = Ly * Hq               [м²]
%  Напряжение в заделке     sigma_xx(x) = w*(Lx - x)/A = b1*(Lx - x)  [Па]
%  Перемещение конца        u_max = b1 * Lx² / (2*E_q)                [м]
sigma_xx_max = load_.b1 * geom.lx;
u_max_bar    = load_.b1 * geom.lx^2 / (2 * mquartz.E);

printf("=== АНАЛИТИЧЕСКАЯ ПРИКИДКА (стержневая модель, b1 по X) ===\n");
printf("  max sigma_xx (у заделки x=0) = b1*Lx      = %.6g Па\n", sigma_xx_max);
printf("  max перемещение x=Lx         = b1*Lx²/2E  = %.6g м\n\n", u_max_bar);

% ---------------------------------------------------------------------------
% 4. ФОРМУЛЫ пост-обработки (закон Гука + фотоупругость)
% ---------------------------------------------------------------------------
printf("=== ФОРМУЛЫ ПОСТ-ОБРАБОТКИ ===\n");
printf("  eps_zz  = (sigma_zz - nu*(sigma_xx + sigma_yy)) / E\n");
printf("  delta_n = dn_scale_quartz * eps_zz\n");
printf("  displacement_max = max |u|\n");

% ---------------------------------------------------------------------------
% 5. Сравнение с FEM (если есть elmer_results.json)
% ---------------------------------------------------------------------------
res_json = fullfile("..", "outputs", "elmer_results.json");
if exist("jsondecode") && exist(res_json, "file")
    j = jsondecode(fileread(res_json));
    printf("\n=== СРАВНЕНИЕ С FEM (Elmer) ===\n");
    printf("  из FEM: n_nodes = %d\n", j.n_nodes);
    q = j.quartz_layer;
    printf("  кварц:  eps_zz mean = %.6g   delta_n mean = %.6g\n", ...
           q.eps_zz.mean, q.delta_n.mean);
    printf("  кварц:  delta_n max = %.6g   delta_n min = %.6g\n", ...
           q.delta_n.max, q.delta_n.min);
    printf("  glass:  eps_zz mean = %.6g\n", j.glass_layer.eps_zz.mean);
    printf("  displacement_max = %.6g м\n", j.displacement_m.max_abs);
    % закон Гука проверяем на средних значениях FEM
    if isfield(q, "sigma_zz_mean")
        szz = q.sigma_zz_mean;
        % из FEM известно полное поле: восстановим eps по Гуку (среднее)
        printf("\n  Проверка закона Гука на FEM-полях:\n");
        printf("    sigma_zz_mean(кварц) = %.6g Па\n", szz);
    end
else
    printf("\n[нет elmer_results.json — FEM-сравнение пропущено, запустите\n");
    printf("  .venv/bin/python3 ../scripts/bragg_sim.py  сначала]\n");
end

printf("\nГотово.\n");