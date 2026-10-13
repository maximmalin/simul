% reliability_model.m — надёжность Krystal Radio (SDR, direct conversion).
%
% Модель parts-count с температурным ускорением по Аррениусу:
%
%   pi_T    = exp( Ea/kB * (1/T_ref - 1/T_use) )        температурный фактор
%   lambda  = base_FIT * pi_T * pi_Q * 1e-9             отказов/ч элемента
%   Lambda  = SUM_i N_i * lambda_i                      система (последоват.)
%   MTBF    = 1 / Lambda                                ч
%
% base_FIT, Ea, T_ref, T_use, pi_Q берутся из reliability_parts.tsv
% (справочники Siemens SN 29500 / MIL-HDBK-217F и даташиты).
%
% Запуск:
%   octave --quiet --eval "addpath('sim/octave'); reliability_model('sim/octave/reliability_parts.tsv','out.json')"

function out = reliability_model(parts_path, out_path)

  KB = 8.617333262e-5;          % эВ/К (постоянная Больцмана)
  MTBF_TARGET_H = 10000.0;      % REQ-26: MTBF >= 10 000 ч

  if nargin < 1 || isempty(parts_path)
    this = fileparts(mfilename('fullpath'));
    parts_path = fullfile(this, 'reliability_parts.tsv');
  end
  if nargin < 2 || isempty(out_path)
    out_path = fullfile(tempdir(), 'reliability_result.json');
  end

  [cat, qty, base_fit, ea, t_ref, t_use, quality] = read_parts(parts_path);

  Tref = t_ref + 273.15;
  Tuse = t_use + 273.15;
  pi_T = exp(ea ./ KB .* (1 ./ Tref - 1 ./ Tuse));
  lam_fit = base_fit .* pi_T .* quality;          % FIT на элемент (штуку)
  lam_tot_fit = qty .* lam_fit;                   % FIT на группу

  total_fit = sum(lam_tot_fit);
  lambda_per_hour = total_fit * 1e-9;
  mtbf_h = 1.0 / lambda_per_hour;

  % «горячий» сценарий +20 °C к T_use — проверка чувствительности к перегреву
  Tuse_hot = t_use + 20.0 + 273.15;
  pi_T_hot = exp(ea ./ KB .* (1 ./ Tref - 1 ./ Tuse_hot));
  total_fit_hot = sum(qty .* base_fit .* pi_T_hot .* quality);
  mtbf_hot_h = 1.0 / (total_fit_hot * 1e-9);

  % ---- отчёт в stdout ----------------------------------------------------
  printf('\n=== Надёжность Krystal Radio (parts-count, Arrhenius) ===\n');
  printf('%-16s %4s %8s %6s %8s %9s\n', ...
         'category', 'N', 'FIT/elem', 'Ea,eV', 'Tuse,C', 'FIT grp');
  for i = 1:numel(cat)
    printf('%-16s %4d %8.2f %6.2f %8.1f %9.2f\n', ...
           cat{i}, qty(i), lam_fit(i), ea(i), t_use(i), lam_tot_fit(i));
  end
  printf('%-16s %4d %8s %6s %8s %9.2f\n', ...
         'TOTAL', sum(qty), '', '', '', total_fit);
  printf('\nMTBF = %.0f ч  (lambda = %.3e 1/ч = %.1f FIT)\n', ...
         mtbf_h, lambda_per_hour, total_fit);
  printf('MTBF при +20 C перегрева = %.0f ч\n', mtbf_hot_h);
  printf('REQ-26 (MTBF >= %.0f ч): %s\n\n', MTBF_TARGET_H, ...
         tern(mtbf_h >= MTBF_TARGET_H, 'PASS', 'FAIL'));

  % ---- JSON --------------------------------------------------------------
  out = struct();
  out.generated_by = 'sim/octave/reliability_model.m';
  out.model = 'parts-count + Arrhenius';
  out.categories = struct('category', cat, 'qty', num2cell(qty), ...
      'base_fit', num2cell(base_fit), 'ea_ev', num2cell(ea), ...
      't_use_c', num2cell(t_use), 'pi_t', num2cell(pi_T), ...
      'lambda_fit', num2cell(lam_fit), 'fit_group', num2cell(lam_tot_fit));
  out.total_qty = sum(qty);
  out.total_fit = total_fit;
  out.lambda_per_hour = lambda_per_hour;
  out.mtbf_h = mtbf_h;
  out.mtbf_hot_h = mtbf_hot_h;
  out.mtbf_target_h = MTBF_TARGET_H;
  out.pass = (mtbf_h >= MTBF_TARGET_H);

  fid = fopen(out_path, 'w');
  if fid < 0, error('reliability: cannot write %s', out_path); end
  fputs(fid, jsonencode(out));
  fclose(fid);
  printf('JSON: %s\n', out_path);
end

% ---------------------------------------------------------------------------
function [cat, qty, base_fit, ea, t_ref, t_use, quality] = read_parts(path)
  fid = fopen(path, 'r');
  if fid < 0, error('reliability: cannot open %s', path); end
  C = textscan(fid, '%s %f %f %f %f %f %f', ...
               'Delimiter', ' ', 'CommentStyle', '#', ...
               'MultipleDelimsAsOne', true);
  fclose(fid);
  cat = C{1}; qty = C{2}; base_fit = C{3}; ea = C{4};
  t_ref = C{5}; t_use = C{6}; quality = C{7};
  if isempty(cat), error('reliability: no data rows in %s', path); end
end

function s = tern(cond, a, b)
  if cond, s = a; else s = b; end
end
