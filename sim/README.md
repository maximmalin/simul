# Ячейка Брэгга — Модуль 1 (механика + фотоупругость)

**Единственная точка входа для расчёта ячейки:** Gmsh → ElmerSolver → результат.
Результат — показатель преломления кварца: `Δn = dn_scale_quartz · ε_zz`.

---

## 1. Структура папки sim/

```
sim/
├── README.md                    ← этот файл (читайте первым)
│
├── inputs/                      ← ВХОДНЫЕ ПАРАМЕТРЫ (правите ЭТОТ файл)
│   └── bragg_inputs.yaml        ← геометрия, материалы, нагрузка, оптика, пути
│
├── outputs/                     ← РЕЗУЛЬТАТЫ (читаете после прогона)
│   └── elmer_results.json       ← единственный выходной файл (JSON)
│
├── fmu/                         ← FMI-блок для ко-симуляции
│   └── BraggBlock.fmu           ← FMI 2.0 Co-Simulation (готовый бинарник)
│
├── scripts/                     ← ВСЕ скрипты
│   ├── bragg_sim.py             ← конвейер целиком (точка входа, см. §2)
│   ├── bragg_fmu.py             ← исходник FMU-блока BraggBlock
│   ├── test_fmu.py              ← проверка FMU через FMPy (см. §3)
│   ├── realtime_loop_fmu.py     ← пример RT-цикла на FMU (см. §4)
│   └── msh_to_elmer.py          ← конвертер .msh (Gmsh) → mesh.* (Elmer)
│
├── octave/                      ← ФОРМУЛЫ (см. §5)
│   ├── bragg_formulas.m         ← какие параметры и по каким формулам
│   └── README_formulas.md       ← формулы в текстовом виде + самопроверка
│
├── cad/                         ← CAD для FreeCAD (см. §6)
│   ├── bragg_cell.FCStd         ← параметрическая 3D-модель (мм)
│   ├── bragg_cell.step          ← STEP-копия
│   ├── refresh_cad.py           ← перегенерация из bragg_inputs.yaml
│   └── make_bragg_cell_fc.py    ← макрос FreeCAD
│
├── work/                        ← промежуточные файлы (создаются сами,
│   │                                НЕ редактировать, можно удалить)
│   ├── bragg.geo                ← геометрия Gmsh из параметров
│   ├── case.sif                 ← собранный SIF для Elmer
│   ├── elmersolver.log          ← лог решателя
│   └── bragg_cell/              ← сетка Elmer + результат .vtu
│       ├── mesh.*               ← сетка (узлы, элементы, границы)
│       └── bragg_results_t0001.vtu ← поля решения для визуализации (ParaView)
│
└── archive_old/                 ← старые версии/черновики (не используются,
                                     можно удалить целиком)
```

## 2. Где ввести входные параметры и как получить выходные

**Место ввода** — один файл:

```
sim/inputs/bragg_inputs.yaml
```

| Раздел            | Что задаёт                                    |
|-------------------|-----------------------------------------------|
| `geometry`        | размеры блока: длина, ширина, стекло, кварц   |
| `mesh_size`       | характерный размер сетки, м                   |
| `material`        | плотность, E, ν стекла и кварца               |
| `bodyforce`       | объёмная сила на кварце b1/b2/b3, Н/м³        |
| `optics`          | `dn_scale_quartz` (Δn = scale · ε_zz)         |
| `bc`              | коды граничных условий из mesh.boundary       |
| `gmsh_bin`/`elmer_solver` | пути к исполняемым                 |

**Запуск** (одна команда):

```bash
cd /home/mirrage/Desktop/brig-receiver
.venv/bin/python3 sim/scripts/bragg_sim.py
```

**Выход** — один файл:

```
sim/outputs/elmer_results.json
```

Содержимое: `n_nodes`, `displacement_m.max_abs`, и по слоям кварца/стекла —
`sigma_zz_mean`, `eps_zz` (mean/max/min), `delta_n` (mean/max/min), плюс
`vtu`, `solver`, `total_elapsed_s`, `params_used`.

Файл `bragg_inputs.yaml` создаётся автоматически при первом запуске, если его
нет. Сетка кешируется по геометрии: если меняются только материалы/нагрузка,
пересчёт занимает ~0.6–0.7 с (полный прогон ~1 с).

## 3. Использование FMU (`sim/fmu/BraggBlock.fmu`)

Блок — **FMI 2.0 Co-Simulation** (реализация pythonfmu).
Два режима (`engine_mode`):

| engine_mode | Режим                                              | Скорость шага  |
|-------------|----------------------------------------------------|----------------|
| 0           | **linearized** — 3 опорных решения при инициализации, дальше линейная суперпозиция по b1/b2/b3 | мгновенно (мкс) |
| 1           | **full** — полный расчёт Elmer на каждый шаг      | ~0.6–0.7 с/шаг |

### Переменные (интерфейс FMI 2.0)

| Переменная          | Тип       | Направление | Описание                              |
|---------------------|-----------|-------------|---------------------------------------|
| `glass_E, glass_nu` | Real      | parameter   | материал стекла (до init)             |
| `quartz_E, quartz_nu` | Real    | parameter   | материал кварца (до init)             |
| `lx, ly, glass_lz, quartz_lz` | Real | parameter | геометрия, м (до init)       |
| `mesh_size`         | Real      | parameter   | сетка, м (до init)                    |
| `dn_scale_quartz`   | Real      | parameter   | фотоупругий коэффициент               |
| `engine_mode`       | Integer   | parameter   | 0 = linearized, 1 = full (до init)    |
| `b1, b2, b3`        | Real      | input       | объёмная сила на кварце, Н/м³ (время) |
| `delta_n_mean`      | Real      | output      | Δn кварца (среднее)                   |
| `eps_zz_mean`       | Real      | output      | ε_zz кварца (среднее)                 |
| `sigma_zz_mean`     | Real      | output      | σ_zz кварца (среднее), Па             |
| `displacement_max`  | Real      | output      | макс. перемещение, м                  |
| `model_status`      | Integer   | output      | 0 = ok (discrete)                     |
| `accuracy_metadata` | String    | output      | JSON-описание режима/точности         |

Семантика шага: вход удерживается постоянным в течение шага, выход отражает его
в конце шага.

### Пример подключения (Python + fmpy)

```python
import fmpy, numpy as np

fmu = "/home/mirrage/Desktop/brig-receiver/sim/fmu/BraggBlock.fmu"

res = fmpy.simulate_fmu(
    fmu,
    start_values={"engine_mode": 1},      # 0 = быстрый, 1 = точный
    input=np.array([(0.0, 4.5e-3, 0.0, 0.0),
                    (1.0, 4.5e-2, 0.0, 0.0)],
                   dtype=[("time", "f8"), ("b1", "f8"), ("b2", "f8"), ("b3", "f8")]),
    stop_time=2.0,
)
print(res[-1]["delta_n_mean"])
```

### Проверка блока

```bash
cd /home/mirrage/Desktop/brig-receiver
.venv/bin/python3 sim/scripts/test_fmu.py     # → "ALL FMU TESTS PASSED"
```

### Пересборка FMU (после правки `bragg_fmu.py`)

```bash
cd /home/mirrage/Desktop/brig-receiver
.venv/bin/python -m pythonfmu build -f sim/scripts/bragg_fmu.py -d sim/fmu
```

## 4. Петля реального времени через FMU

**Да, можно.** В режиме `engine_mode = 0` шаг занимает микросекунды — FMU тянет
RT-циклы с частотами до сотен Гц (инициализация: 3 опорных решения Elmer,
~1.8 с). В режиме `engine_mode = 1` шаг стоит ~0.55–0.7 с, поэтому период RT
не может быть меньше ~0.6 с.

Готовый пример замкнутого цикла:

```bash
cd /home/mirrage/Desktop/brig-receiver
.venv/bin/python3 sim/scripts/realtime_loop_fmu.py
```

Пример менять вход (b1) каждые 100 мс и снимает delta_n; в конце печатает
бюджет времени на шаг.

## 5. Формулы (Octave)

Что именно вычисляется и по каким формулам — в подпапке `sim/octave/`:

```bash
cd /home/mirrage/Desktop/brig-receiver/sim/octave
octave --quiet bragg_formulas.m
```

Кратко (закон Гука + фотоупругость):

```
ε_zz = (σ_zz − ν(σ_xx + σ_yy)) / E      # по слою кварца/стекла
Δn   = dn_scale_quartz · ε_zz            # по слою кварца
displacement_max = max|u|
```

Скрипт также делает аналитическую самопроверку (стержневая модель) и при
наличии `elmer_results.json` печатает сравнение с FEM. Подробности —
`sim/octave/README_formulas.md`.

## 6. CAD для FreeCAD

В подпапке `sim/cad/` лежат параметрическая модель `bragg_cell.FCStd` и
`bragg_cell.step` (размеры в мм: 15×5, стекло 2.91 мм, кварц 0.09 мм):

```bash
freecad sim/cad/bragg_cell.FCStd
```

После правки геометрии в `bragg_inputs.yaml` перегенерировать CAD:

```bash
cd /home/mirrage/Desktop/brig-receiver
.venv/bin/python3 sim/cad/refresh_cad.py
```

Подробности — `sim/cad/README_cad.md`.

## 7. Важно

- **Окружение:** все команды запускаются из venv проекта (`.venv/bin/python3`).
- **Привязка к машине:** FMU и скрипты используют пути этой сборки Elmer
  (`/home/mirrage/Desktop/elmer_installed/bin/ElmerSolver_mpi`) и жёстко
  привязаны к корню `/home/mirrage/Desktop/brig-receiver` (можно переопределить
  через `BRAGG_SIM_PROJECT`).
- **Косметика:** pythonfmu при завершении процесса может печатать
  `corrupted double-linked list` — известная особенность выгрузки встроенного
  Python, на результаты не влияет.
- **Промежуточные файлы:** папку `sim/work/` можно удалять — всё пересоздаётся;
  `sim/archive_old/` — мусор прежних версий, можно удалить целиком.