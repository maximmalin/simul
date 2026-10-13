# tests/requirements/thermal/

Тепловой режим Krystal Radio (SDR). Два уровня расчёта.

## fast — узловая модель
`T_j = T_a + P · θ_JA`, где `P` — рассеиваемая мощность, `θ_JA` — тепловое
сопротивление «кристалл–среда» из даташита. Проверяет, что `T_j` каждого
прибора не превышает `T_j,max` с запасом ≥ 10 °C.

## fem — 2D FEM теплопроводности по плате
* Домен и посадочные места — из **PCB CAD**
  (`hardware/tscircuit/out/circuit.json`): контур, толщина 1.6 мм, 4 слоя FR4.
* Источники тепла — из **ngspice** (`sim/netlists/thermal_bias.cir`, `.op`):
  получасовой DC-режим даёт мощности LDO, MCU, RF-тракта, смесителя, ОУ.
* Уравнение (усреднение по толщине, heat-spreading):
  `−∇·(k·t·∇T) + h·(T − T_a) = q(x,y)`
  — P1-треугольники, решатель `scipy.sparse.linalg.spsolve`.
* Контроль: энергобаланс `|Q − h·∫(T−T_a)|` < 0.5 мВт.

3D FEM (Elmer/CalculiX) — следующий уровень; для платы 1.6 мм 2D достаточно.

## Запуск
```
pytest tests/requirements/thermal/ -v
make thermal        # -> out/thermal_fast.json, out/thermal_fem.json
python3 sim/scripts/thermal_pcb.py
```

## Источники
* `hardware/tscircuit/out/circuit.json` (PCB CAD)
* `sim/scripts/thermal_pcb.py` (модель)
* `sim/netlists/thermal_bias.cir` (генерируется автоматически)
