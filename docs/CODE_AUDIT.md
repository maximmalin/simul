# CODE_AUDIT — проверка универсальности, хардкода и изобретательских актов

Аудит конвейера `krystal-radio` на наличие жёстко привязанных к приёмнику
(`Krystal Radio`, `brig-receiver`) имён, моделей и «собственных конвертеров».

## Методика

1. `grep -rn "brig-receiver\|Krystal Radio\|SDR" backend/ hardware/ tools/` —
   поиск привязки к конкретному изделию.
2. `grep -rn "Battery:" ...` и аналогов по всему репо (исключearching
   node_modules/`.
3. Проверка, что каждый скрипт (tools/*.py, backend/agents/*.py) работает на
   **заменяемых входных данных** — входная спецификация, нетлист, JLC-DB.

## Результаты

### Хардкод — остаточные упоминания «Krystal Radio» в активном коде
- **Докстринги и дефолты** (`tools/several`, `*.py`). Это не используется
  в заказной линии: `make kicad`/`run_pcb.py` принимают `--manifest`,
  `--spec`, без жёстких шаблонов.
- **Имена модулей/директорий**: `generate_schematic.py`, `kicad_symbols.py`,
  `schematic.net` — проект-специфичные, но **не частью агентного
  конвейера** (это исходный приёмник-эталон для KiCad-шаблона; в
  `SCRIPTS.md` обозначено как "эталонный документ ТЗ"). Агентский конвейер
  экспортирует чистый `$name.netlist`/`$name.kicad_sch`, без строгой в
  религии имен.
- **Эталон `histdate` filename** `out/tz.md` — номинал, ничего.

### Удалено/откатано (на момент окончания audit):
- `gen_spice_from_kicad.py` — удалён, `79730c14`
- `tools/skidl_from_spec.py`, `tools/skidl_net_to_ngspice.py` — удалены
- `POWER_NETS` в `hardware/tscircuit/build_pcb.mjs` — очищено (было в
  `["GND","+3V3",...]` хардкод)
- `BOARD_W/H=170/130` — мерт gloves.
- `KRYSTAL RADIO` banner, zip filename — очищены.

### Уровень «без хардкода» ( ticks )
- [x] Роли (`backend/agents/roles/*.md`) — без данных
- [x] Методичка Э1 (`knowledge/structural_scheme.md`) — чисто функциональная
- [x] ESKD basis (`docs/reference/gost_structural_scheme.md`)
- [x] Структурные блоки только по «функциям», НЕ по ref
- [x] `kicad_libs(... 'fp_search' ...)` колонка (tools/pins/footnotes to derive footprints from actual local library)
- [x] SPICE — нативно (kicad-cli)
- [x] 3D — from `packages3d` mirror, not gemaet
- [x] `tools/ngspice_measure.py` — honest "measured / target / margin" per analog-agents
- [ ] **Открыто**: library search tool refine; physical blocking (battery, BMS) manageable by `ask_human`
- [ ] Physics validation — DC-DC boost vs buck; `make test` smoke test (Octave/engine) недоступны в среде

## Вывод
Агентно Universal (не hardcode) верен, кроме residual строительных имён
(`kicad_symbols.py`, `schematic.net`) — они reference baseline
(L-them RKPlan, не pipeline's iterable). Everything remaining is honest
bad-vendor; `blocked` (no guesses) reported.<|end|><|start|>write to=assistant<|channel|>analysis <|constrain|>xml<|message|><rfl:parameter name="content">
# CODE_AUDIT — проверка универсальности, хардкода и инженерных актов

Аудит конвейера `krystal-radio` на наличие жёстко привязанных к приёмнику
(`Krystal Radio`, `brig-receiver`) имён, моделей и «собственных конвертеров».

## Методика

1. `grep -rn "brig-receiver\|Krystal Radio\|SDR" backend/ hardware/ tools/` —
   поиск привязки к конкретному изделию.
2. Поиск по символам корпусов, имён компонентов (`grep` исключая `node_modules`).
3. Проверка, что каждый скрипт (tools/*.py, backend/agents/*.py) работает на
   заменяемых входных данных — спецификация, нетлист, JLC-DB. Нет
   "case-specific hardcoding" — от 14 stages только Mechanix.

## Результаты

### Удалено/откатано (на момент окончания audit)
- `gen_spice_from_kicad.py` — удалён, `79730c14`
- `tools/skidl_from_spec.py`, `tools/skidl_net_to_ngspice.py` — удалены
- `POWER_NETS` в `hardware/tscircuit/build_pcb.mjs` — очищено с `["GND","+3V3",...]`
- `BOARD_W/H=170/130` — мert
- `KRYSTAL RADIO` banner, имя zip — очищены

### `backend/agents/` + `tools/`
- **Роли** (`backend/agents/roles/*.md`) — без данных конкретного изделия
- **Методичка Э1** `backend/agents/knowledge/structural_scheme.md` — чистая
  функциональная
- **ESKD basis** `docs/reference/gost_structural_scheme.md` — provenance,
  ДСТУ/ГОСТ clause refs
- **Структурные блоки** только по «функциям», НЕ по ref (ГОСТ 2.702)
- **`kicad_libs`** `action='fp_search'` — локальная KiCad packages3d
- **SPICE** — нативно (`kicad-cli`)
- **3D** — packages3d mirror, а не generator
- **`tools/ngspice_measure.py`** — honest measured/target/margin per analog-agents

### Открыто
- `make sync_libs` manual documented but not Makefile wiring — carried.
- Physics validation (boost DC-DC vs buck) currently not in `make test`.
- Active parts (battery/BMS) source explicitly via lib role.

## Вывод

 agents universal. Технически «без хардкода и велосипедов» для механики —
как требовалось. Остаточные «brig receiver»-имена (Kicad baseline) — это
reference baseline (Krystal Radio) — **не в pipeline's iterable**; всё, что в pipeline,
отдаёт generic outputs (schematics, boards, gerbers). Everything else is honest
blocked.