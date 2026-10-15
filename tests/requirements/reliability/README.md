# tests/requirements/reliability/

Надёжность Krystal Radio (SDR, direct conversion) по **REQ-26** (MTBF ≥ 10 000 ч).

## Что проверяется
* соответствие MTBF требованию REQ-26 и внутренняя согласованность модели;
* охват всех категорий BOM (60 элементов) и физичность параметров даташитов;
* температурная чувствительность по Аррениусу (при +20 °C MTBF падает, но
  требование всё ещё выполняется).

## Как считается
* **Движок:** `sim/octave/reliability_model.m` (GNU Octave).
* **Данные:** `sim/octave/reliability_parts.tsv`:

  | столбец | смысл |
  |---|---|
  | `category` | категория элемента |
  | `qty` | количество |
  | `base_fit` | базовый поток отказов, FIT (1e-9/ч) при `t_ref` |
  * `ea_ev` — энергия активации, эВ;
  * `t_ref_c` / `t_use_c` — опорная и рабочая температура;
  * `quality` — π_Q (промышленный класс = 1.0).

  Модель: `π_T = exp(Ea/kB·(1/T_ref − 1/T_use))`,
  `Λ = Σ N·base_FIT·π_T·π_Q·1e-9`, `MTBF = 1/Λ`.

## Запуск
```bash
pytest tests/requirements/reliability/ -v
make reliability          # → out/reliability.json
```

## Источник
* `requirements/reliability/REQ-REL-01_mtbf.md`
* `sim/octave/reliability_model.m`, `sim/octave/reliability_parts.tsv`
