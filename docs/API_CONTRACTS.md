# API-контракты агентного моста

`backend/agent_bridge.py` — единственный HTTP-шлюз фронтенда и агентов.
Все ответы — JSON (`Content-Type: application/json`), CORS `*`.
Долгие операции — через механизм джобов (`POST /api/build` → `GET /api/build/<id>`),
потоки — SSE (`text/event-stream`).

> Фронтенд ходит через `frontend/src/lib/bridge.ts` (типы + функции).
> В dev Vite проксирует `/api` на мост (см. `vite.config.ts`).

---

## 1. Состояние и дашборд

| Метод | Путь | Ответ | Назначение |
|---|---|---|---|
| GET | `/api/health` | `{ok, root, time, artifacts, has_page, has_waterfall, has_glb, jobs}` | живость моста |
| GET | `/api/snapshot` | `{health, chain, zone, metrics, projects, specs, tests, jobs[], updated}` | **один запрос на весь дашборд** (вместо 6 опросов) |
| GET | `/api/zone` | `ZoneTelemetry` | телеметрия для шапки |
| GET | `/api/metrics` | `Metrics` | физика: link_budget, direction, thermal, reliability, receiver, emc |
| GET | `/api/chain` | `Chain {stages[], done, total, progress}` | 12 стадий `make all` по наличию артефактов |
| GET | `/api/tests` | `{ok, passed, failed, tail[]}` | последний прогон REQ-сьюта |
| GET | `/api/config` | `KrConfig` | роли, языки, склад, спеки проектов |

## 2. Рилтайм-флоу (SSE)

```
GET /api/flow/stream?interval=1.5&max=0
→ text/event-stream
event: flow
data: {"chain": Chain, "jobs": FlowJob[], "running": N, "updated": "..."}

event: ping            # heartbeat каждые ~15 с, когда изменений нет
data: {"t": "..."}
```

`FlowJob = {id, target, status, agent_id, exit_code, started_at, finished_at, tail}`.
Поток шлёт **полный снимок только при изменении** (diff по `sort_keys` JSON).
`interval` — секунды опроса состояния (0.5…10), `max` — лимит событий
(0 = без лимита, для тестов). Клиент закрывает `EventSource` — сервер
ловит `BrokenPipe` и тихо выходит.

Фронт: `watchFlow(onFlow, onPing?, onError?)` → `() => close()`.

## 3. ТЗ и конструкторская часть

| Метод | Путь | Ответ |
|---|---|---|
| GET | `/api/tz` | `{file, title, chars, section_count, sections[], mech, variants[]}` |
| GET | `/api/placement/latest` | сводка последнего `make placement-opt` |
| GET | `/api/circspec/templates` | `{templates: string[]}` |

`TzSection = {n, level, title, body, chars}` — разбор `technical_task.md` по `#…####`.
`mech` — из `mech_spec.py`: `{has_mechanical, categories[], requirement_count,
requirements[{category,label,section,line,excerpt}], note}`.
Конструкторская часть **делегируется инженеру** (FreeCAD авторитетен):
`make cad-brief` → `out/mech_constructor_brief.md`.

## 4. Симулятор

```
GET /api/sim
→ { built, total, progress, items[], metrics{link_budget,direction,thermal,reliability},
    run_hint, updated }
```

`SimItem = {name, title, desc, kind: png|json|glb|html, exists, url, size,
modified, summary?}` — 9 артефактов ко-симуляции (водопад, спектр, 3D, отчёт,
link budget, пеленгация, тепло ×2, надёжность). `url` ведёт на `/api/artifact/<name>`.

## 5. PCB-сьют (чтение `out/*.json`)

| Метод | Путь | Файл сборки | Ответ |
|---|---|---|---|
| GET | `/api/dfm` | `out/dfm.json` | DFM-анализ, `score`, `issues[]` |
| GET | `/api/cost` | `out/cost.json` | JLCPCB/PCBWay, цена, сроки |
| GET | `/api/erc-zones` | `out/erc_zones.json` | ERC001–008 по нетлисту |
| GET | `/api/preorder` | `out/preorder.json` | pre-order review |
| GET | `/api/pcb-score` | `out/pcb_score.json` | score + breakdown |
| GET | `/api/fix-loop` | `out/fix_loop_report.json` | отчёт петли исправления |
| GET | `/api/fea` | `out/fea/fea_result.json` | прочность (FEM/аналитика) |
| GET | `/api/cad-build` | `out/cad/cad_build.json` | build123d-эскиз |
| GET | `/api/drc-rules` | `out/drc_rules.json` | DRC-правила |
| GET | `/api/mna-fallback` | `out/mna_fallback.json` | MNA-фолбэк |

Не собрано → `404 {error: "not built (run: make <цель>)", file}`.

## 6. Агенты, проекты, запуск

| Метод | Путь | Назначение |
|---|---|---|
| GET | `/api/agents` | пакет агентов из `AGENT_PROMPTS.md` |
| GET | `/api/agent/<id>` | один агент |
| GET | `/api/targets` | `ALLOWED_TARGETS` + `ALLOWED_SCRIPTS` (белый список) |
| GET | `/api/runtime/agents` | локальный рантайм-ростер |
| GET | `/api/runtime/context` | контекст проекта для агента |
| POST | `/api/agent/run` | `{agent_id, target}` → `Job` (цель из белого списка) |
| POST | `/api/agent/script` | `{agent_id, script, args[]}` → `Job` |
| POST | `/api/prompt` | `{agent_id}` → `{prompt}` |
| GET/POST/DELETE | `/api/projects` | CRUD проектов |
| GET | `/api/specs/<code>` | спецификация изделия по коду |

### Джобы

```
POST /api/build   {target}        → {id, ...}   # цель из ALLOWED_TARGETS
POST /api/run     {script, args}  → {id, ...}   # скрипт из ALLOWED_SCRIPTS
GET  /api/build/<id>?since=N      → {status, exit_code, log[]}   # лог с offset N
GET  /api/jobs                    → {jobs[]}    # без лога (последние 20 строк)
```

## 7. Заказ (агентный оркестратор)

| Метод | Путь | Назначение |
|---|---|---|
| GET | `/api/order/capabilities` | что умеет оркестратор |
| GET | `/api/orders?limit=N` | история заказов |
| GET | `/api/orders/waiting` | ожидающие |
| POST | `/api/order` | создать заказ из текста ТЗ |
| POST | `/api/order/plan` | `{text}` → план целей (без запуска) |
| POST | `/api/order/<id>/resume` | продолжить после паузы |
| GET | `/api/order/<id>/stream` | **SSE-поток событий заказа** |

## 8. Прочее

| Метод | Путь | Назначение |
|---|---|---|
| GET | `/api/artifacts?ext=png,json` | список артефактов `out/` |
| GET | `/api/artifact/<rel>` | отдать файл (MIME по расширению) |
| GET | `/api/krystal-projects` | проекты Krystal |
| GET | `/api/llm` | статус LLM-слоя |
| GET | `/api/graph` | граф агентов |
| GET | `/api/ask` + POST | вопрос агенту |
| GET/POST/DELETE | `/api/bureau/*` | бюро-таблицы (Supabase-совместимые) |
| POST | `/api/prompt` | промпт агента |

---

## Паттерны для фронтенда

1. **Один запрос вместо многих** — дашборд читает `/api/snapshot`,
   а не шесть отдельных GET.
2. **Рилтайм через SSE, не опрос** — цепочка/заказ идут по
   `/api/flow/stream` и `/api/order/<id>/stream`; опрос (`setInterval`)
   — только фолбэк, когда `EventSource` упал.
3. **Долгое — через джобы** — `POST /api/build` мгновенно возвращает `id`,
   лог тянется `GET /api/build/<id>?since=` или `watchJob()`.
4. **Честные 404** — «не собрано» отдаётся с подсказкой `run: make <цель>`,
   а не пустым объектом.
