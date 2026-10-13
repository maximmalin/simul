# Карта проекта brig-receiver

Аппаратный приёмник на ячейке Брэгга. Схема и плата **генерируются кодом**, ручной
правки файлов нет. Машиночитаемая версия этой карты — `manifest.json`, собирается
командой ниже.

```
python3 tools/dump_manifest.py
```

Дублирует структуру? Нет: `manifest.json` собирается из тех же путей официальными
инструментами, поэтому карта не расходится с реальностью.

---

## 1. Что это

| | |
|---|---|
| Назначение | приём РЭБ-сигнала, СВЧ-понижение до 38 МГц, акустооптическая ячейка, оцифровка, выдача по Wi-Fi |
| Плата | 152 × 195.63 мм, 4 слоя, 561 сегмент трасс, 135 переходных отверстий |
| Компонентов | 73 |
| Сетей / узлов | 46 / 190 |
| Подобрано деталей | 28 из 73 |

## 2. Проверенное состояние

Эти числа не декларация — они выгружаются из официального экспорта KiCad при каждом
запуске `dump_manifest.py`:

```
компонентов 73   сетей 46   узлов 190
```

## 3. Структура

Роли: **source** — держим, **derived** — пересобирается скриптами, **data** — внешние данные.

### Исходники

| Путь | Что внутри |
|---|---|
| `hardware/kicad/` | генератор `.kicad_sch`, библиотека символов |
| `hardware/tscircuit/` | эмиттер TSX, 3D-модели, подбор деталей, вьюер |
| `hardware/antenna/` | геометрия антенной решётки |
| `hardware/pipeline.py` | оркестратор, 13 стадий с проверкой каждой |
| `sim/scripts/` | физика: Брэгг, антенна, излучение, экспорт в ngspice |
| `sim/inputs/` | параметры расчётов |
| `work/tz-a…tz-e/` | техзадания по блокам |
| `work/subdevices/` | подсхемы устройств |
| `calculations/` | расчёты сквозного тракта |

### Пересобираемое — можно удалить

| Путь | Чем восстанавливается |
|---|---|
| `hardware/tscircuit/out/` | `build_derived.py`, `tsci build` |
| `sim/outputs/` | `sim/scripts/*.py` |
| `sim/work/` | gmsh, Elmer, openEMS |
| `sim/nec/`, `sim/octave/` | NEC, Octave |
| `dist/` | `make_viewer.py` |
| `node_modules/` | `npm install` |

### Данные

| Путь | Что |
|---|---|
| `hardware/tscircuit/models/` | 3D-модели STEP/GLB, разрешаются локально |
| `sim/fmu/` | `AntennaBlock.fmu`, `BraggBlock.fmu` |
| `books/` | источники по акустооптике и FEM |
| `sim/archive_old/` | предыдущие версии расчётов |
| `elmer_installed/` | локальная установка Elmer |
| `datasheets/` | 8 PDF датащитов микросхем, скачаны через `kicad-tools` |

## 4. Автоматизация

| Скрипт | Назначение |
|---|---|
| `hardware/pipeline.py` | полный конвейер, 13 стадий, каждая с проверкой |
| `hardware/kicad/generate_brig_receiver.py` | генератор схемы |
| `hardware/kicad/kicad_lib.py` | чтение и запись `.kicad_sym`, `.kicad_sch` |
| `hardware/tscircuit/build_pcb.mjs` | нетлист → TSX, размещение, `model_offsets.json` |
| `hardware/tscircuit/build_derived.py` | локальная таблица подбора из jlcparts |
| `hardware/tscircuit/resolve_parts.py` | подбор по номиналу и корпусу |
| `hardware/tscircuit/step2stl.py` | STEP → STL, нормализация ориентации |
| `hardware/tscircuit/check_models3d.py` | сверка glb против circuit.json |
| `hardware/tscircuit/make_viewer.py` | сборка локальной 3D-страницы |
| `hardware/tscircuit/check_net.py` | проверка вьюера на внешние запросы |
| `sim/scripts/bragg_sim.py` | gmsh → Elmer → JSON |
| `sim/scripts/antenna_sim.py` | расчёт антенны |
| `sim/scripts/radiation.py` | излучение и S-параметры |
| `sim/scripts/export_ngspice.py` | прямая запись подсхем ngspice, Touchstone |
| `sim/scripts/bragg_fmu.py` | сборка `BraggBlock.fmu` |
| `sim/scripts/antenna_fmu.py` | сборка `AntennaBlock.fmu` |
| `sim/scripts/realtime_loop_fmu.py` | цикл реального времени поверх FMU |
| `hardware/tscircuit/site_assets.py` | ресурсы сайта вьюера |
| `hardware/tscircuit/fetch_vendor_models.mjs` | загрузка 3D-моделей производителей |
| `block0_bus_power_calc.py` | расчёт шины питания 5 В |
| `gen_spice_from_kicad.py` | генерация SPICE-нетлиста из схемы |
| `gost_pipeline.py` | конвейер расчётов и сборки |
| `picsimlab_rt_wrapper.py` | мост к PicSimLab для реального времени |
| `tools/dump_manifest.py` | сборка этой карты и манифеста |

## 5. Зависимости

### Системные инструменты

| Инструмент | Версия | Зачем |
|---|---|---|
| `kicad-cli` | 9.0.2 | экспорт нетлиста, ERC, SPICE |
| `ngspice` | 44.2 | симуляция |
| `gmsh` | 4.13.1 | сетка для FEM |
| `python3` | 3.13.5 | генераторы |
| `node` | 22.23.2 | tscircuit |
| `npm` | 10.9.8 | зависимости |

Elmer ставится локально, в `PATH` не входит: `elmer_installed/bin/ElmerSolver`.

### KiCad-автоматизация и датащиты

| Инструмент | Назначение |
|---|---|
| `kicad-tools` (`kct`) | готовый набор: ERC, DRC, BOM, анализ, датащиты |
| `kct datasheet download <MPN> -o datasheets/` | скачивание датащитов по артикулу |

Ставится через `pip install 'kicad-tools[parts]'`. Источники — LCSC и Octopart,
плюс локальный кэш. Скачано 8 PDF: STM32F103C8Tx, ESP32-WROOM-32, LM78L05,
MP2303, TL061, TL082.

Прямые адреса производителей из свойств `Datasheet` символов KiCad тоже
используются: в 224 библиотеках их 19 179.

### Внешние данные

| Путь | Что |
|---|---|
| `/mnt/ext4data/jlcparts` | база деталей, 279 МБ, `cache/cache.sqlite3` |
| `/usr/share/kicad/symbols` | 224 символьные библиотеки |
| `/usr/share/kicad/footprints` | библиотеки посадочных мест |
| `/usr/share/kicad/demos/simulation` | 21 файл `.lib` с моделями SPICE |

Все четыре на месте. Отсутствие любого ломает конвейер.

### Прочее

Зависимости Python — в `requirements/`, npm — в `package.json`. Полный список
с точными версиями в `manifest.json`.

## 6. Как воспроизвести проект заново

Порядок для чистой машины:

```
1.  kicad-cli 9, ngspice, gmsh, Elmer, node 22, python 3.13
2.  развернуть /mnt/ext4data/jlcparts
3.  git clone <репозиторий> && cd brig-receiver
4.  npm install                                    → node_modules
5.  python3 hardware/kicad/generate_brig_receiver.py → .kicad_sch
6.  python3 hardware/tscircuit/build_derived.py   → out/jlcsearch.db
7.  python3 hardware/tscircuit/resolve_parts.py   → подбор деталей
8.  python3 hardware/pipeline.py                  → плата, 3D, экспорты
9.  python3 tools/dump_manifest.py                → проверка: 73 / 46 / 190
```

Шаги 6–9 восстанавливают всё пересобираемое. Что не восстанавливается само:
`hardware/tscircuit/models/` и `sim/fmu/` — это данные, они в репозитории или
собираются скриптами.

Проверка, что воспроизведение удалось:

```
python3 tools/dump_manifest.py
```

Должно напечатать `компонентов 73, сетей 46, узлов 190`.

## 7. Ограничения, которые надо знать

| Что | Статус |
|---|---|
| SPICE-модели деталей | не привязаны, 27 элементов выходят заглушками |
| `kicad-cli sch export netlist --format spice` | ломает формат примитивов, обходим прямой генерацией |
| Смесители и усилители в библиотеках KiCad | отсутствуют, символы пишем сами |
| Подбор деталей | 28 из 73, остальным нужны правила |
| Модели советских деталей | в базе LCSC их нет и не будет |