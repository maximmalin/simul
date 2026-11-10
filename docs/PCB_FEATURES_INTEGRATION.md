# Улучшение Krystal Radio фичами AutoPCB, AI-PCB-Generator, text-to-cad

> Текстовый документ — анализ трёх внешних проектов + статус внедрения
> **(backend only, без фронтенда)**. Наши достоинства сохранены:
> SKiDL→ngspice→FMU/QEMU, tscircuit, LangGraph, GOST, thermal/reliability —
> ничего не заменено, только добавлены модули-спутники.

## Статус внедрения (реализовано, backend)

| Модуль | Источник | Файл | Make-цель | Out |
|---|---|---|---|---|
| PCB score + ratsnest + proximity | auto-pcb eval/perturbation | `backend/pcb_score.py` | `pcb-score` | `out/pcb_score.json` |
| DFM 12 чеков, score 0–100 | ai-pcb-generator/dfm | `backend/dfm_engine.py` | `dfm` | `out/dfm.json` |
| Manufacturer cost + BOM/CPL | ai-pcb-generator/manufacturing | `backend/manufact.py` | `cost` | `out/cost.json` |
| ERC 8 зондов + pin alias | ai-pcb-generator/ai_copilot | `backend/erc_zones.py` | `erc-zones` | `out/erc_zones.json` |
| CircuitSpec контракт | ai-pcb-generator/schemas | `backend/circuitspec.py` | `circspec` | `design/templates/*.json` |
| MNA fallback | ai-pcb-generator/python_solver | `backend/mna_fallback.py` | `mna-check` | `out/mna_fallback.json` |
| A* fallback router | ai-pcb-generator/router | `backend/fallback_router.py` | `fallback-route` | `out/fallback_route.json` |
| Placement loop + playbook | auto-pcb loop/proposer | `backend/placement_loop.py` | `placement-opt`/`placement-sweep` | `.run/placement_opt/` |
| FEA study (CalculiX biv wrapper + analytic) | text-to-cad/fea | `backend/fea_study.py` | `fea` | `out/fea/fea_result.json` |
| Parametric CAD (build123d wrapper) | text-to-cad/cad | `backend/cad_build.py` | `cad-build` | `out/cad/cad_build.json` |
| Pre-order review | text-to-cad/pcb review-* | `backend/preorder.py` | `preorder` | `out/preorder.json` |
| Full connectivity ERC001–008 + pin-alias | ai-pcb ai_copilot (полный порт) | `backend/erc_zones.py` | `erc-zones` | `out/erc_zones.json` |
| CircuitSpec auto-repair validator | ai-pcb parser.py CircuitValidator | `backend/circuitspec.py` | `circspec` | warnings |
| DFM-012 diff-pair + чистка DFM-002 | ai-pcb dfm.py | `backend/dfm_engine.py` | `dfm` | `out/dfm.json` |
| Production package (BOM+CPL+cost+gerber) | ai-pcb manufacturing.py | `backend/manufact.py --package` | `production` | `out/production/` |
| **Fix-loop: агент чинит до чистого** | auto-pcb loop + CRITIC/Reflexion 2026 | `backend/fix_loop.py` | `fix-loop` | `out/fix_loop_report.json` |
| **Anti-stagnation: UCB-бандит + отжиг** | IBM AAAI-2026 bandits, arXiv premature-exploitation | `backend/placement_loop.py` | `placement-opt` | `.run/placement_opt/` |

Мост (`backend/agent_bridge.py`): `GET /api/dfm`, `/api/cost`, `/api/erc-zones`,
`/api/preorder`, `/api/pcb-score`, `/api/fea`, `/api/cad-build`, `/api/mna-fallback`,
`/api/circspec/templates`, `/api/placement/latest`, `/api/fix-loop`. Тесты:
`tests/requirements/pcb_suite/` (17 passed). Проверено: score 18443 (DRC 705 доминирует), DFM 84, cost JLCPCB $134.25/5шт,
placement 4503→4463 (accept 35%), sweep фаден-монотонность показана.

## 1.2 Петля обратной связи и anti-stagnation (best practices 2026)

Реализовано по итогам обзора практик 2026 (CRITIC/Reflexion grounded
self-correction, IBM AAAI-2026 bandits+LLM, pass^k, LLM-as-Judge,
controlled autonomy):

**`backend/fix_loop.py` — «агент чинит ошибки, пока не станет чисто»:**
- Два коридора: **auto** (безопасные геометрические фиксы в circuit.json:
  выход за outline, зазоры <1mm, узкие питания) и **advisory** (ERC/DFM —
  в `out/fix_tasks.json` задачами, не тихо «чинится»).
- Keep/revert по (число auto-проблем, score) — grounded critic: судьями
  являются детерминированные чекеры, а не мнение LLM.
- **Stuck detection + кулдаун**: ref после 3 отказов — в кулдаун (урок
  auto-pcb playbook про локальные ловушки); **эскалация** после 10 итераций
  без прогресса или когда все проблемы в кулдауне → статус `escalated`
  человеку (никаких бесконечных циклов).
- **Playbook-память между прогонами**: `.run/fix_playbook.md` (правила:
  какие виды фиксов сработали, какие рефы — ловушки) подмешивается в LLM-промпт.
- **LLM-as-Judge**: после петли судья приоритизирует advisory-задачи в
  `out/fix_plan.md` (корневые причины + конкретные изменения) — пассивно,
  без автоприменения рискованных правок (controlled autonomy).
- **Feedback trail**: `out/fix_loop_report.md` — человекочитаемый лог
  «что проверили → что изменили → почему приняли/отклонили → что осталось».
- **История/дрейф**: `out/fix_loop_history.jsonl` — тренд по прогонам.
- **Бэкап перед автозаписью**: `--write-back` сначала делает `.bak`.
- **pass^k**: `--seeds N` — консистентность результата «clean» на N сидах.

**`backend/placement_loop.py` — anti-stagnation:**
- **UCB1-бандит** над видами ходов (jitter/swap/rotate) вместо фиксированных
  вероятностей: принуждённая exploresия всех рук + эксплуатация того, что
  реально принимается (принято: jitter 0.4 / swap 0.2 / rotate 0.2 @15 итераций).
- **Отжиг с перегревом**: sigma затухает ×0.9 на каждом KEEP, на плато
  (5 отказов подряд) перегревается ×2 (до 2× базы) — анти-premature-exploitation.
- События reheat пишутся в records.jsonl, сводка — в `anti_stagnation`
  summary (bandit rates/pulls, reheats, sigma_final).


## 1. Источники (скачаны сюда)

| Проект | Папка здесь | URL | Объём | Лицензия |
|---|---|---|---|---|
| AutoPCB (LLM-оптимизация размещения) | `auto-pcb/` | https://github.com/brendanhogan/autopcb | 9.4 МБ | MIT (в репо) |
| AI-PCB-Generator (NL→PCB + DFM + производство) | `ai-pcb-generator/` | https://github.com/22507260/AI-PCB-Generator | 6.7 МБ | MIT |
| text-to-cad (агентные CAD/PCB/FEA скиллы) | `text-to-cad/` | https://github.com/earthtojake/text-to-cad (v0.11.3, 25 906 ★) | 56 МБ | MIT |

Все три — open-source (MIT), что упрощает переиспользование идей и кода.
Qt-GUI из AI-PCB-Generator нам не нужен — мы веб, поэтому ниже разбираем
только переносимую логику (схемы, движки, контракты) и описываем, как
показать её в React.

## 2. Что у нас уже есть (сильные стороны — не ломать)

- **Source of truth**: SKiDL `sim/scripts/sdr_skidl_circuit.py` (60 деталей,
  38 цепей) → KiCad-нетлист → ngspice-дека → FMI 2.0 FMU → QEMU/PicSimLab.
- **PCB**: `hardware/tscircuit/build_pcb.mjs` — нетлист → TSX → официальный
  `tsci` (корпуса, capacity-autorouter, экспорт KiCad/gerbers/STEP/GLB),
  `out/` с артефактами, offline three.js-просмотрщик (`hardware/tscircuit/viewer/`).
- **Проверки**: ERC/DRC через `kicad-cli` (`make erc`, `make drc`), тесты
  `tests/requirements/` (22/22 netlist, 31/31 pin-contract), reliability MTBF,
  thermal (ngspice + FEM), link-budget (Friis), direction-finding (NEC-2),
  GNU Radio waterfall, FreeCAD-корпус и панели антенн, GOST-чертежи (`gost_pipeline.py`).
- **Веб**: React-вкладки (Overview, Krystal, Order, Personnel, Projects, Comms,
  Chat, Deliverables, Warehouse, FlowEditor, DevDept), Python-мост
  `backend/agent_bridge.py` (эндпоинты `/api/health`, `/api/artifacts`,
  `/api/build`, `/api/targets`, `/api/metrics`…), LangGraph-агенты
  (`agents.project.yaml`, `backend/llm.py`, localdb вместо Supabase).
- **Parts**: JLCPCB parts DB в `/mnt/ext4data/jlcparts`, резолвер
  `hardware/tscircuit/resolve_parts.py`.

Вывод: у нас **глубокая физика и агентный граф**, но слабая **оптимизация
геометрии/размещения, manufacturability-метрики и интерактивный CAD/PCB UX**.
Именно эти дыры закрывают три проекта.

## 3. Инвентаризация внешних проектов

### 3.1 `auto-pcb/` — LLM-цикл оптимизации размещения (Brendan Hogan)

Паттерн: **LLM предлагает ход → автопроверка скорирует → keep/revert → повтор**.
Результат на реальной плате (вакуумная лампа ecc83): score **11 123 → 1 640
за 300 итераций (~40 мин, ~$1.20)**, на 49% лучше random-jitter и в 5.8× лучше
ручного референса.

Состав:
- `autopcb/eval.py` — пайплайн: `.kicad_pcb` → Specctra DSN (через `pcbnew`
  workaround, т.к. KiCad 10 убрал DSN-экспорт из CLI) → FreeRouting
  **1.9.0** (2.x ломается — infinite recursion `PolylineTrace.combine`)
  → SES → `kicad-cli pcb drc` → скалярный score.
- **Score**: `trace_length_mm + 5×via + 20×DRC_violations + 200×unconnected`.
  Важно: *мягкий* штраф DRC (а не «ноль — иначе fail»). Trace length в одиночку
  бесполезен (автороутер зигзагует), главный сигнал — DRC-счётчик.
- `autopcb/placements.py` — S-expr парсинг/мутация `.kicad_pcb`, случайный
  старт внутри outline (базовая плата даёт только refs/footprints/netlist).
- `autopcb/proposer.py` — `RandomProposer` vs `ClaudeProposer` (Anthropic
  tool-use, prompt caching, история 12, rejected 25). `Move` = один или
  несколько `Operation(ref, x, y, rot)` (swap/group-nudge).
- `scripts/perturbation_sweep.py` — Phase-0 валидация: джиттер σ ∈
  {0, 0.1, 0.5, 1, 2, 5} мм, 60 триалов. Score монотонно растёт с σ. **Eval
  сначала надо проверить, иначе LLM некуда расти.**
- `scripts/update_playbook.py` + `playbook.md` — **cross-run память**: второй
  LLM-вызов дистиллирует 2–5 уроков из `records.jsonl`, следующий забег
  читает `playbook.md` как часть system prompt. Агент «учится» без переобучения.
- `scripts/loop.py`, `compare_loops.py`, `plot_loop.py`, `render_boards.py`
  — `records.jsonl`, `best.kicad_pcb`, `summary.json`, сравнительные графики.
- Gist-уроки michidk («что local autoPCB должен перенять от invertium»):
  негативный контроль для каждого обязательного чекера, независимая проверка
  copper-connectivity, перечень — их можно взятся бесплатно.

**Что у нас такого нет**: ни скалярного score размещения, ни цикла
hill-climbing на LLM, ни playbook-памяти, ни perturbation-свипа. Наш
capacity-autorouter в tscircuit — чёрный ящик: его результат никак не
измеряется, размещение не оптимизируется.

### 3.2 `ai-pcb-generator/` — NL → схема → PCB → SPICE → DFM → производство

Полный конвейер «опиши словами за 5 минут». Ключевое для нас — не Qt
(мы веб), а следующие модули:

- `src/ai/schemas.py` — **Pydantic-контракт `CircuitSpec`** (иерархия:
  `ComponentSpec` with `bytes` pins/footprint/placement hints, `NetSpec`,
  `PinSpec`/`PadSpec`, `LayerName`, `ComponentCategory`, уравнения
  «pin alias matching» VIN↔IN, GND↔VSS). Это то, чего у нас нет в виде
  контракта: у нас SKiDL-нетлист складывается из Python-кода, а
  NL → структура без валидации.
- `src/ai/client.py` / `prompts.py` / `parser.py` — OpenAI-совместимый
  клиент (GPT-4o / Gemini / Claude / Ollama / Groq / OpenRouter), валидация
  и ремонт LLM-ответа. У нас `backend/llm.py` уже есть, но CircuitSpec-парсер
  с few-shot примерами и самопроверкой — готовый образец.
- `src/pcb/generator.py` — `Board` dataclass (Pad/PlacedComponent/TraceSegment/
  Via/BoardOutline), grid auto-placement с группировкой, параметры
  `constraints` (min width/clearance). У нас плавающая логика в
  `build_pcb.mjs` + tscircuit; явной Board-модели Python нет.
- `src/pcb/router.py` — FreeRouting + **fallback на внутренний A*-роутер**,
  если FreeRouting упал или не дал SES (важная надёжность: у нас tscircuit
  CLI при ошибке просто падает).
- `src/pcb/rules.py` — DRC-движок (6 чеков): ширина, clearance pad-pad,
  trace-pad, via drill, boundary, unconnected. Параллель нашему `make drc`,
  но как Python-класс — можно звать из моста без shell-out.
- `src/pcb/dfm.py` — **DFM-движок (12 чеков IPC-2221)**: DFM-001 current
  capacity (таблица 1oz, 10°C rise), DFM-002 annular ring, DFM-003 acid traps,
  DFM-004 thermal relief, DFM-005 silk-over-pad, DFM-006 spacing под pick-place,
  DFM-007 board size, DFM-008 via aspect ratio, DFM-009 copper balance,
  DFM-010 solder bridge, DFM-011 min drill, DFM-012 diff-pair mismatch.
  У каждого issue: severity (Critical/Warning/Info), ссылка, рекомендация,
  ** manufacturability score 0–100**. У нас DFM нет вообще.
- `src/pcb/manufacturing.py` — профили JLCPCB/PCBWay/OSH Park (min trace/drill/
  clearance, слои, размеры, цены, lead time), **живой cost estimator**,
  Gerber ZIP + BOM CSV (JLCPCB-совместимый) + CPL (pick-place) + `.kicad_pcb`
  в одном «производственном пакете», проверка «не превышаем ли лимиты фабрики».
  У нас JLCPCB DB есть, но оценщика цены/сроков и capability-валидации нет.
- `src/simulation/` — NgSpice 46 + **встроенный MNA-солвер**
  (`python_solver.py`, 597 строк: Modified Nodal Analysis, DC/TR/AC, работает
  *без* NgSpice), `netlist_writer.py`, `result_parser.py`. У нас русская
  ngspice-цепочка уже сильная (FMU/QEMU), но fallback-солвер на случай отсутствия
  ngspice в контейнере/CI — полезный страховочный.
- `src/gui/ai_copilot.py` — AI Co-Pilot: 8 ERC-правил (unconnected power pins,
  missing resistor на LED, no ground, multiple outputs, missing decoupling…)
  + pin alias matching против false positives. У нас ERC — только KiCad CLI;
  свои 8 правил как детерминированные зонды дополняют CLI-ERC.
- `data/templates/` — 6 шаблонов (LED, voltage regulator, motor driver, sensor,
  Arduino shield, USB-C power) + `data/examples/*.apcb`. Готовые v1-фичи для
  каталога шаблонов в нашем FlowEditor.
- 3D OpenGL-просмотрщик (`gl_pcb_view.py`, `PyOpenGL`, depth-tested) — у нас
  three.js-визуализатор уже покрывает, но идея «guards/pads/drills/vias из
  текущих данных, без декоративных отверстий» у них сформулирована строже.
- Тесты: 16 тестов (dfm/drc/router/simulation/generator/manufacturing/exporter).

**Что у нас такого нет**: DFM 0–100, стоимость/сроки/проверка лимитов
под заказ, A*-fallback, MNA-fallback, CircuitSpec-контракт, pin-alias,
ERC-зонды (8), templates. Именно это — дешёвые и очень заметные пользователю
фичи.

### 3.3 `text-to-cad/` — агентные скиллы для CAD + PCB + FEA (25 906 ★)

Самая зрелая экосистема. Важно не «скопировать build123d», а **архитектура
скиллов и CAD Viewer**:

- **7 скиллов** `skills/*/SKILL.md` (OpenAI/Codex/Cursor/Gemini/Copilot/
  OpenCode/Antigravity/Cline… плагины): `cad`, `pcb`, `fea`, `engineering-drawing`,
  `print3d`, `sendcutsend`, `robot-simulation` + `packages/cad-server`,
  `packages/cad` (python `cad` сервер: build123d + OpenCASCADE, STEP/STL/3MF/
  GLB/DXF), `apps/mcp` (MCP-сервер), `apps/web` (React 19 + Vite + three.js,
  `@cad/*`, Radix UI, tailwind-merge) — **это ближайший аналог нашего
  фронтенда**, его паттерны (code-splitting, snapshot-review, meshoptimizer)
  переносить проще всего.
- `cad` — параметрические модели Python (`@model` → «модель — функция»),
  warm-worker (переиспользует ядерный кэш, повторный запуск — мгновенно),
  snapshot-цикл «модель → PNG → проверка глазами → правка», STEP/STL/3MF/GLB/
  DXF/flat-patterns, `xbf`-конвертер вендорских STEP. Наши FreeCAD-скрипты
  (`hardware/antenna/geometry/build_antennas_fc.py`,
  `hardware/enclosure/`, `gost_pipeline.py`) — разрозненные imperative-скрипты
  без warm-пересборки, без snapshot-петли и без единой модели. У нас `make cad`
  — холодный прогон FreeCAD.
- `pcb` — нативные `.kicad_pcb/.kicad_sch` как source-of-truth, edit через
  KiCad IPC API (`kicode` nightly KiCad 10.99, с апгрейдом формата),
  ERC/DRC/экспорт через `kicad-cli` (CAD_KICAD_CLI), **octolinear ручная
  трассировка, автороутер не используется (сознательно)** — противоположное
  направление от auto-pcb, but complementary как «review перед заказом»
  таблица: fab/electrical/assembly, decoupling distances, ширина для тока,
  sourcing под JLCPCB/PCBWay. У нас предзаказного review-табличного чеклиста нет.
- `fea` — линейная механика: **netgen-мешинг + CalculiX 2.23** (тот DIMENSION
  mm/N/MPa, QP-квадратичные тетраэдры), study-JSON (fixtures/loads/material/
  margin/checks), peak von Mises + safety factor + deflection с отрисовкой
  на модели, CFD/thermal/FEM у нас уже есть (`thermal` — пластинчатый FEM),
  но структурный FEA для корпуса/панели — нет. Наш enclosure: FreeCAD + Al
  с 3 зонами, без проверок на прочность.
- `engineering-drawing` — размерные PDF-листы из геометрии (виды, hidden lines,
  centre marks, hole callouts, title block). Наш GOST — SVG/PDF из STEP
  через `gost_pipeline.py`, более «цеховой», но генерация из проекций
  build123d — дополнительный источник.
- `print3d` (OrcaSlicer → Bambu Connect/Studio, допуски под печать),
  `sendcutsend` (проверка DXF/STEP по гайду фабрики), `robot-simulation`
  (URDF/SRDF/SDF) — для SDR-приёмника не нужны, кроме flat-patterns/DXF как
  заготовки корпуса.

**Что у нас такого нет**: SKILL.md-архитектура для агентов (у нас
`agents.project.yaml` + `AGENT_PROMPTS.md`, но не делегируемые AGENTS.skills
через Claude/OpenCode плагины), warm CAD server + live viewer, FEA
структурный, flat patterns/DXF, print/sendcut проверки, MCP-сервер полного
цикла, snapshot-review как рутина.

## 4. GAP-анализ — чего нам не хватает

| # | Фича у трёх проектов | Есть у нас | GAP / что брать |
|---|---|---|---|
| 1 | Скалярный score размещения + hill-climbing LLM-цикл + playbook (`auto-pcb`) | ERC/DRC pass-fail только | **score, loop, playbook, perturbation-sweep** |
| 2 | DFM 12 чеков + manufacturability 0–100 (`ai-pcb-generator`) | нет Scout | **dfm.py + score 0–100** |
| 3 | Производитель: профили, цена, lead time, capability validation, BOM/CPL/ZIP (`ai-pcb`) | JLCPCB DB без estimators | **manuator profiles + cost estimator** |
| 4 | A*-fallback роутер, когда FreeRouting/tsci падает | нет | **fallback-router** |
| 5 | MNA-fallback солвер без NgSpice | только NgSpice 44.2 | **python_solver.py как fallback** |
| 6 | CircuitSpec (Pydantic) NL→структура + parser/ремонт | SKiDL-генератор, без контракта для NL | **schemas.py + client/prompts/parser** |
| 7 | Pin-alias matching, 8 ERC-зондов, AI Co-Pilot | KiCad CLI ERC | **ERC-зонды + alias** |
| 8 | Parametric CAD: `@model`, warm-rebuild, STEP/STL/3MF/GLB/DXF/flat (`text-to-cad/cad`) | FreeCAD-скрипты, холодный прогон | **build123d сервер → корпус/антенны/флэт** |
| 9 | FEA структурный (CalculiX+netgen), deflection, safety factor | только thermal FEM | **fea skill на корпус** |
| 10 | Snapshot-review loop (PNG → глаз → правка) | статичный viewer | **snapshot PNG в UI** |
| 11 | До-заказная review-таблица (JLCPCB/PCBWay) | нет | **pre-order review (3 колонки)** |
| 12 | Шаблоны схем (6 шт) + каталог | нет | **templates в FlowEditor** |
| 13 | MCP-сервер полного цикла (apps/mcp) | `backend/mcp.py` (частично) | расширить до CAD-моделей |
| 14 | CAD Viewer React (v19+three, meshoptimizer, Radix) | three.js offline viewer | **переиспользовать паттерны, не код** |
| 15 | negative controls + copper-connectivity независимая проверка (gist) | нет | **checklist для наших ERC/DRC** |

**Что у нас есть, а у них нет (наше конкурентное преимущество — беречь)**:
закрытая физика (FMU/QEMU/PicSimLab/openEMS FDTD), antenna YAML + S11,
link-budget/direction, reliability/thermal, SKiDL source-of-truth, sandbox
заказов (`work/orders/<id>`), LangGraph supervisor/tools interrupt, GOST-цепочка.
Эти системы оберегать от упрощения «под ради детской конструктор-платы».

## 5. План внедрения (по приоритетам)

### P0 — Быстрые победы (1–2 итерации, без риска)

**P0.1 DFM 12 чеков + manufacturability 0–100**
Откуда: `ai-pcb-generator/src/pcb/dfm.py` (таблица IRC-2221 внутри,
`DFMEngine.run_all()` → `list[DFMIssue]`). Адаптация: наш `Board` приходит
из `circuit.json` (tscircuit), а не `Board` dataclass из генератора, поэтому
нужен `to_dfm_board(circuit_json)`-адаптер (pads/vias/traces/outline).
Веб: новый `DfmPanel.tsx` (таблица issue: code/severity/refs/recommendation,
полоса score 0–100), таб в `ProjectsTab`/`DeliverablesTab`. Backend:
`GET /api/dfm` на `agent_bridge.py` (вызов `DFMEngine`, результат — JSON),
Makefile-цель `dfm` → `out/dfm.json`. Критерий приёмки: текущая плата SDR
получает score и ≥1 проверенных findings (например, silk-over-pad если есть),
unit-тесты `tests/requirements/dfm/`.

**P0.2 Cost estimator + capability validation (JLCPCB/PCBWay/OSH Park)**
Откуда: `manufacturing.py` (`ManufacturerProfile`, `MANUFACTURERS`,
`CostEstimate`). Адаптация: наша JLCPCB DB (`/mnt/ext4data/jlcparts`) уже
содержит BOM-цены — соединить estimator (плата по площади + SMT по компонентам)
с нашим resolved BOM. Веб: `CostPanel.tsx` — три карточки фабрик (цена, lead time,
зелёные/красные флаги лимитов), ввод quantity/PCB q. Backend:
`GET /api/cost?qty=5` → JSON. Критерий: цена за 5 плат SDR совпадает ~с JLCPCB
(допуск ±20% для MVP) и capability-флаги работают.

**P0.3 Pin alias matching + 8 ERC-зондов**
Откуда: `schemas.py` (alias table) + `ai_copilot.py` (8 правил).
Адаптация: строки пинов из KiCad-нетлиста нормализуются (VIN→IN, GND→VSS,
PPOWER→VIN…), зонды пишутся как детерминированные функции над нетлистом
(`tools/erc_zon des.py` аналог) и кладутся в отчёт **рядом** с KiCad ERC
(`out/erc.rpt`), как отдельные строки в `OverviewTab`. Критерий: known-чистая
схема даёт 0 ложных, тест на missing-decoupling срабатывает.

**P0.4 A*-fallback роутер (страховка)**
Откуда: `ai-pcb-generator/src/pcb/router.py` (451 строка) — если FreeRouting
или `tsci` не дали геометрии, A* на сетке. Адаптация: дернуть fallback
**только** при падении `make pcb` (env `KR_ASTAR_FALLBACK=1`), не заменять
tscircuit в проде. Критерий: на битом корпусе (`random_placements_in`) — fallback
даёт хотя бы unconnected=0 при более низком score чем ничего.

### P1 — Средний (оптимизация размещения, CAD, FEA)

**P1.1 AutoPCB-цикл: score + LLM hill-climbing + Playbook**
Порты: `auto-pcb/autopcb/eval.py` (score + pipeline), `placements.py`,
`proposer.py` (Proposer ABC → наш `backend/llm.py` провайдер вместо
`anthropic` SDK), `scripts/loop.py` (records/summary/best). Но вход — **наш**
циркуит из tscircuit, не KiCad-PCB: проще всего начать не с FPGA-роутинга,
а с **traffic-light оптимизации**: evaluate(props tscircuit) → hill-climbing
over placement hints → перегенерировать TSX. Score — тот же (trace length +
via + DRC + unconnected), но DRC уже есть через `kicad-cli`.
Web: `PlacementTab.tsx` — график trajectory (plot_loop), таблица moves
(accept rate), кнопка «Итерации 50», результат: `best.circuit.tsx` + картинка
через `render_boards`-аналог (у нас SVG `board-svg`). Playbook-файл:
`.run/placement_playbook.md` (у нас аналога нет). Perturbation-sweep
обязателен **до** LLM-цикла (Phase-0 gate, как в авто-pcb paper).
Критерий: за 50 итераций на SDR-плате score падает ≥20% против текущего
tsci-placement (зафиксировать базовый score текуighteenth платы сегодня).

**P1.2 Parametric CAD для корпуса и панелей (text-to-cad/cad subset)**
Не тащить весь `cad-server` (warm worker), а внедрить только **build123d
модели корпуса/панели** как альтернативу FreeCAD-скриптов: `design/enclosure/
enclosure.py` с `@model`-функцией (ячейка, 3 зоны, отверстия под плату SDR),
экспорт STEP/STL/GLB/DXF. Snapshot-loop: `cad model snapshot` → PNG в
`DeliverablesTab` («что видит глаз»). Веб: переиспользовать React-паттерны
apps/web (client/host/shared, meshoptimizer) — не копировать код, а взять
идеи: URL вида `viewer/<docid>?token=...`, Fit/rotate hints, Tree-sitter.
Критерий: корпус пересобирается из Python скрипта за секунды без FreeCAD-GUI,
STEP открывает в KiCad 3D view.

**P1.3 FEA корпуса (CalculiX + netgen)**
Откуда: `skills/fea/SKILL.md` + `packages/cad/src/cad/_internal/fea/*`
(study-file JSON: fixtures/loads/material/margin/checks). Адаптация: наш
Al-корпус → `cad step solve` → `result.json` (peak von Mises, safety factor≥2,
deflection), рядом с `thermal`/`link-budget` экспортом. Веб: FEA-панель —
гвард-формулировки Three.js поверх результата (heatmap цветов по VTU через
meshoptimizer). Ограничение text-to-cad: только statics, не вибрация/усталость —
мы то же честно заявим в UI. Критерий: корпус при 500N/лист получает SF≥2
или честный отчёт «- документы-». Для пускового весового файла: пока
пустой study не раскатывай, минимум — воспроизводить Examples: кронштейн с
«500N», SF>2.

**P1.4 Templates + CircuitSpec (NL → структура)**
Откуда: `ai-pcb-generator/src/ai/schemas.py` + `client.py/prompts.py/parser.py`
+ `data/templates/*` + LLM-клиент OpenAI-compatible (как в `.env.example`:
Ollama/Groq/OpenRouter/LM Studio/Together/DeepSeek). Адаптация: наш `OrderTab`
уже принимает NL — под капот кладём CircuitSpec-валидацию (Pydantic nalog для
нашего Python-моста, JSON-Schema на TS-стороне), templates превращаем в
«каталог архетипов» в FlowEditor (LED/voltage-regulator/sensor/…). Замена
полной NL-генерации тscircuit — нет, только как **input для нашего SKiDL-бота**
(агент-схемотехник), иначе теряем source-of-truth. Критерий: запрос
«sensor board» → заполняется template → агент строит SKiDL без галлюцинированных
REF.

**P1.5 Pre-order review-таблица (3 секции)**
Откуда: `skills/pcb/references/review-*.md` (fab/electrical/assembly table,
«scope first, findings, coverage, pass/fail/review/unverified»). Подсоединяем
глупый, но популярный шаблон заполнения: fab-лимиты из JLCPCB (наличие),
electrical (decoupling ≤3mm по P1.1, ширина по току из DFM-001), assembly/BOM
(количество extended-parts, basic parts). Веб: новая секция `DeliverablesTab`
«Перед заказом» с таблицей Item/Evidence/Rule/Result/Fix. Критерий: если
missing-decoupling — строка «review» + ссылка на move-приём.

**P1.6 MNA-fallback (страховка без ngspice)**
Откуда: `ai-pcb-generator/src/simulation/python_solver.py` (597 строк) →
наш `backend/spice_fallback.py`. Применение: только когда `ngspice` нет
в CI/Docker (у нас есть, но для веб-деплоя на frontend-only часто нет).
MNA не заменит FMU (у нас FMU уже вещь), но DC/TR базово закроет веб-симуляцию.
Критерий: без бинарника `ngspice` — экзамен DC Operating Point сходится,
CI-джоба проходит.

### P2 — Большие (после стабилизации P0/P1)

**P2.1 Полный CAD server + live viewer (text-to-cad apps/web + packages/cad-server)**
React 19 + three.js + meshoptimizer + Radix Tweaks в embedded iframe внутри
нашего `DeliverablesTab` или отдельный `/cad` роут. Warm workers, документы
по id (`0dc50a92-...`). Тяжёлый интеграшин, но решает проблему «редактирование
корпуса — статические файлы». Оценивать по демо-уровню (поставленное
паритетность: одна ссылка `view: http://...?token=`).

**P2.2 MCP полный цикл (apps/mcp)** — превратить `backend/mcp.py` в MCP-сервер
над `cad model solve/export/inspect` + PCB-checks, как у text-to-cad
(`claude.mcp.json`, `codex.mcp.json`, `mcp.json` в корне). Наш MCP уже есть,
нужно добавить CAD-инструменты.

**P2.3 Electrical DFM/Live cost в живом режиме** — панель меняющейся стоимости
при перетаскивании компонента (как JLC-par затора: цена нового токена меняется
реально), pick-place экспорт (CPL) уже частично есть, но‌کنیم полное package.

**P2.4 Flat patterns / DXF + OrcaSlicer-печать** (только если понадобится
плоских заготовок корпуса — currently у нас корпус Al, печатаем не).

## 6. Схема интеграции в наш стек

**Backend (`backend/`, Python)** — новые модули (джоинимся к agent_bridge):

```
backend/pcb_score.py        # score(плата) = len + 5*via + 20*drc + 200*unconn (auto-pcb)
backend/placement_loop.py   # hill-climbing: proposer → eval → keep/revert → records.jsonl (auto-pcb loop.py)
backend/playbook.py         # .run/placement_playbook.md (авто-pcb update_playbook.py)
backend/dfm_engine.py       # адаптация ai-pcb-generator/dfm.py под наш circuit.json
backend/manufact.py         # ManufacturerProfile + cost + capability (manufacturing.py)
backend/erc_zones.py        # 8 ERC-зондз + pin alias (ai_copilot.py)
backend/fallback_router.py  # A* fallback (ai-pcb-generator/router.py)
backend/mna_fallback.py     # MNA solver (ai-pcb-generator/python_solver.py)
backend/cad_build.py        # build123d модели → STEP/DXF (text-to-cad/cad subset)
backend/fea_study.py        # CalculiX/netgen study (text-to-cad/fea)
backend/circuitspec.py      # Pydantic CircuitSpec (ai-pcb-generator/schemas.py) → наш SKiDL-архитектор
```

**Makefile** — новые цели (ничего не ломая из 37 уже существующих):

```
make dfm                 # python backend/dfm_engine.py → out/dfm.json
make cost                # python backend/manufact.py qty=5 → out/cost.json
make erc-zones           # python backend/erc_zones.py → out/erc_zones.json
make placement-opt       # python backend/placement_loop.py --iters 50 (P1.1)
make cad-build           # python backend/cad_build.py (P1.2)
make fea                 # python backend/fea_study.py --study enclosure_study.json (P1.3)
```

**Frontend (`frontend/`, React)** — новые компоненты (по образцу наших 16
существующих, стиль Tailwind + lucide-react, Shadcn-паттерны из text-to-cad/apps/web):

- `frontend/src/components/CostPanel.tsx` — фабрики + quantity stepper.
- `frontend/src/components/DfmPanel.tsx` — таблица DFM findings + score bar 0–100.
- `frontend/src/components/PlacementTab.tsx` — trajectory chart + moves table
   (records.jsonl → SVG/Plot через уже имеющийся Plotly-паттерн из Flask GUI).
- `frontend/src/components/FeaPanel.tsx` — result.json + Vornoi-heatmap поверх 3D.
- `frontend/src/components/CadViewer.tsx` — iframe с `viewer/<id>?token=` или
   native three.js `CAD Viewer`-подобный (iso/top/bottom, fit, zoom around pointer).
- `frontend/src/components/PreOrderReview.tsx` — таблица review-* (3 секции).
- `frontend/src/components/TemplatesPanel.tsx` — каталог из 6 (auto-pcb-P1.4)
   с кнопкой «В Order», `language={{t}}/RU` из ai-pcb i18n (у нас en/ru только).

API-контракты — JSON, задокументировать в `docs/API_CONTRACTS.md` (файл новый,
пример):
```json
GET /api/dfm            → { score: 82, issues: [{code,severity,refs,recommendation}] }
GET /api/cost?qty=5     → { jlcpcb: {price, lead_days, flags: []}, pcbway: {...} }
POST /api/placement-opt {iters: 50, proposer: "llm"|"random"} → {run_id, status}
GET /api/placement-opt/<run_id> → {records: [...], best_score, trajectory: [...]}
GET /api/fea/result     → { sf: 2.4, peak_mpa: 180, deflection_mm: 0.02 }
```
Все long-running — через существующий Job-механизм `agent_bridge.py`
(`POST /api/build` → `GET /api/build/<job_id>` + SSE-stream для логов,
как что-то делает уже `orderConsole`).

## 7. Риски и ограничения

- **Фрейворк-зоопарк**: Frorouting 1.9.0 vs 2.x, KiCad 9 vs nightly 10.99,
  Python-dev-окружение — разберём pinned-версии в `docs/VERSIONS_PIN.md`,
  иначе «работает локально» не обобщить.
- **Skills/Уведомления**: автоматический log обратн-петля LLM-по запросы
  (порядка десятков строк в запись `playbook.md`) могут быстро отравить
  playbook при слабой модели — использовать anchor-reviews не количества, а
  качественных женских заметок.
- **Флейновыйн для заблуждения**: с одной стороны — Freerouting в релизе
  1.9.0, та://урожайные размеры агента Судьба для вторичных шрифтов.
- **Intellectual**: все три — MIT, но долгаем не попкорнить, Suunto
  not опасно. UI-код text-to-cad (apps/web) использовать как «inspiration»,
  не копировать в лоб при различии лицензий-компонентов (Radix — MIT,
  three.js — MIT, но зависимости желательно только стандартные).
- **Объём**: не слить все три репо в одно ведро (запухнет дерево). Держать как
  submodule/reference в **`auto-pcb/`, `ai-pcb-generator/`, `text-to-cad/`**,
  переносить только модули по одному в `backend/`.

## 8. Метрики успеха (что считать победой)

| Метрика | Базовое значение | Целевое после |
|---|---|---|
| Score размещения SDR платы | измерено сегодня (не знам, честно зафиксировать) | −20% за 50 итераций |
| DFM Manufacturability | 0 (нет модуля) | ≥80 score на платах |
| Cost-Estimation точность vs JLCPCB цена | 0% (нет модуля) | ±20% @5 шт |
| ERC-Zone ложных срабатываний | 0 (нет) | 0 на чистой схеме |
| FEA Safety Factor корпуса | не изучен | ≥2 на номинале |
| Time-from-NL-to-SK-логи | ~юмиракта | ~? Нет новыхзаказаволжник |

## 9. Что НЕ брать (сознательно отбрасываем)

- **Qt-GUI из ai-pcb-generator** (`src/gui/*`, PySide6/QOpenGL) — у нас веб.
- **`setup_vendor.py` целиком** — скачивание вендоров каждую ранд, у нас
  докачивается раздельно (torom `/mnt/ext4data/downloads/`).
- **VRML/WRL-парсер** (`models/vrml_parser.py`) — у нас KiCad-модели уже
  резолвятся `.wrl`/`.step` иначе.
- **Quick Edit prompts / robot-simulation (URDF)** — не нужны для SDR.
- **Полный KICAD-NIghtly 10.99+** для вывода (`kicode`) — у нас KiCad 9.0.2
  стабильный, не создавать nightly-формат в репо.
- **Автоподбор ВСЕХ векторческих паттернов людей** (grid-авторазмещение
  из ai-pcb-generator как единственный источник — ломает наши Altium-grade
  правила внутри `build_pcb.mjs`).

## 10. Дорожная карта (итерационно)

1. **P0-1 (неделя)**: DFM + Cost + ERC-zones + A*-fallback + адаптеры +
   unit-tests + 3 React-панели. Пользователь видит новые вкладки
   «DFM/Cost/ERC-Zones» на чистой плате.
2. **P0-2 (неделя)**: замер базового score SDR платы, perturbation-sweep,
   playbook-skeleton, MNA-fallback, PreOrderReview-таблица.
   Если score не падает — честно фиксируем, вручную тюним веса
   (сейчас веса 5/20/200 из auto-pcb — могут не подойти под SDR).
3. **P1 (2–3 недели)**: placement-loop (LLM) + build123d корпус + FEA +
   templates + CircuitSpec + snapshot-loop PNG.
4. **P2 (после)**: полный CAD server/live viewer + MCP-CAD + живой cost.
   Оценивается только после замера воздействия на первую партию пользователей.

## 11. Ссылки

- AutoPCB: README + `auto-pcb/autopcb/{eval,placements,proposer}.py`,
  `scripts/{loop,perturbation_sweep,update_playbook}.py`, `playbook.md`.
- AI-PCB-Generator: README + `ai-pcb-generator/src/{ai/schemas.py,
  pcb/{dfm,rules,manufacturing,router,exporter}.py, simulation/python_solver.py,
  gui/ai_copilot.py}`.
- text-to-cad: `text-to-cad/skills/{cad,pcb,fea,engineering-drawing,print3d,
  sendcutsend}/SKILL.md`, `packages/{cad,cad-server,core,ui}`, `apps/{web,mcp}`.
- Наш проект: `frontend/src/components/`, `backend/agent_bridge.py`,
  `hardware/tscircuit/build_pcb.mjs`, `sim/scripts/sdr_skidl_circuit.py`,
  `Makefile`, `agents.project.yaml`, `AGENT_MAP.md`.

---
*Сгенерировано из анализа трёх склонированных репозиториев (`auto-pcb/`,
`ai-pcb-generator/`, `text-to-cad/` — shallow clones от 2026-11-09).
Стек-время: стек — релиз веб→JS, потому интеграция идёт через
React-компоненты + Python-мост (REST/SSE), не через Qt.*
