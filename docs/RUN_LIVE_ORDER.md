# Живой прогон заказа — памятка агенту

Как реально запускаются заказы в Krystal Radio. Восстановлено по следам
прошлых прогонов (живой мост, `GET /api/orders`, песочницы в
`.krystal/projects/*/work/orders/`, `.run/graph.db`), а не выдумано заново.

**Живой прогон** — это заказ *текстом*, который проходит через **сервис**
(песочница, блокировка проекта, архив), а не одиночный скрипт и не
ручная сборка каркаса.

---

## 1. Где что лежит

| Что | Где |
|---|---|
| Мост (HTTP + UI-прокси) | `backend/agent_bridge.py`, по умолчанию `127.0.0.1:8787` |
| Сервис заказов | `backend/agents/service.py` (песочница, сторож, архив) |
| Стадии конвейера | `backend/agents/stages.py` (15 стадий) |
| Старый граф (fallback без LLM) | `backend/agents/engine.py`, чекпоинты в `.run/graph.db` |
| Проекты | `.krystal/projects/<code>/` |
| Активный проект | `.krystal/current_project.json` (`{"code": ...}`) |
| Песочница заказа | `<проект>/work/orders/<order_id>/` |
| Архив заказа | `<проект>/out/archive/<slug>/<order_id>/manifest.json` |

Если активного проекта нет — корень заказа = сам репозиторий.

---

## 2. Предусловия

1. **Ключ LLM.** Порядок: переменные окружения (`NOUS_PORTAL_API_KEY` и
   др.) → локальный вход Hermes `~/.hermes/auth.json` (`KR_HERMES_AUTH`) →
   локальные серверы (Ollama/LM Studio/vLLM…). Проверка: `make llm-check`.
   Мост **сам `.env` не читает** — ключ должен быть в окружении процесса
   моста либо в Hermes-файле. На этой машине `~/.hermes/auth.json` живой,
   `llm.enabled() → True`, маршрут `nous / poolside/laguna-xs-2.1:free`.
2. **Аппаратный каркас проекта.** Цели `all/tz/kicad/spice/sim/gost/test`
   должны быть объявлены в `agents.project.yaml` **и** иметь рецепты в
   `Makefile`. Образцы: `.krystal/projects/TESTLIGHT2`, `flashlight1`.
3. **Инструменты.** `.venv`, `node` (nvm), `kicad-cli`, `ngspice`.

---

## 3. Способ 1 — штатный, через мост

Поднять, если не жив (обычно уже поднят):

```bash
cd <корень репозитория> && .venv/bin/python backend/agent_bridge.py \
  --port 8787 --host 127.0.0.1 > /tmp/scratchpad/bridge.log 2>&1 &
# или всё сразу (мост + интерфейс): make run
curl -s http://127.0.0.1:8787/api/health          # проверка
```

Заказ (корень указываю **явно**, чтобы не зависеть от активного проекта):

```bash
curl -s -X POST http://127.0.0.1:8787/api/order \
  -H 'Content-Type: application/json' \
  -d '{"text":"Спроектировать драйвер светодиода 1 Вт от Li-Ion 18650 с зарядкой от USB-C",
       "root":"<абс. путь к проекту>"}'
```

Наблюдение:

```bash
curl -s http://127.0.0.1:8787/api/order/<id>          # снимок
curl -N http://127.0.0.1:8787/api/order/<id>/stream   # SSE: state/log/step/done
curl -s http://127.0.0.1:8787/api/orders              # все заказы
curl -s http://127.0.0.1:8787/api/orders/waiting      # ждут человека
```

Оператор и управление:

```bash
curl -s -X POST .../api/order/<id>/resume -d '{"answer":"..."}'   # ответить
curl -s -X POST .../api/order/<id>/cancel                        # отменить
```

Создание проекта (автоматически, не руками):

```bash
curl -s -X POST .../api/projects -d '{"code":"myproj","title":"...","objective":"..."}'
#  → scripts/project_init.py --init --code ... --title ... --objective ...
#  → проект становится активным
```

---

## 4. Способ 2 — headless CLI (без защиты)

```bash
make agents-check                       # что движок увидел в проекте
make agents-ask  TEXT="<заказ>"         # разобрать, ничего не запуская
make agents-run  TEXT="<заказ>"         # корень = репозиторий
PYTHONPATH=backend .venv/bin/python -m agents run --root <проект> "<заказ>"
```

CLI идёт мимо сервиса: **без песочницы, без блокировки, без архива** —
пишет прямо в проект. Для живого прогона штатный путь — мост (способ 1).

---

## 5. Что происходит внутри заказа

```
service.submit → поток _run
  sandbox.create(<проект>/work/orders/<id>)   копия исходников + SYSTEM_TOOLS
  project lock                                 проект закрыт на запись
  engine.run → llm.enabled() ? stages.run : старый граф
  make-цели выполняются в песочнице
  collect_artifacts + _archive_order_outputs   перенос в проект и архив
  unlock
```

Стадии (`_PLAN`):

| # | Стадия | Род |
|---|---|---|
| 1 | tz | make |
| 2 | consultant | LLM |
| 3 | calc (расчётчик) | LLM |
| 4 | library (библиотекарь) | LLM |
| 5 | tz_write (писатель ТЗ) | LLM |
| 6 | checklist (контролёр) | LLM |
| 7 | schematic (дизайнер) | LLM |
| 8 | kicad (SKiDL-блоки → нетлист) | make (спец.) |
| 9 | spice | make |
| 10 | sim | make |
| 11 | sizing (размерщик) | LLM |
| 12 | packager (упаковщик) | LLM |
| 13 | gost (плата tscircuit + чертежи) | make |
| 14 | all | make |
| 15 | test | make |

**make-стадия выполняется только если цель объявлена в `agents.project.yaml`
проекта** (иначе пропускается).

---

## 6. Ручки (env)

`KR_EFFORT=lite|standard|intensive` · `KR_LLM_ENABLED=0` (план по правилам) ·
`KR_TOOL_TURNS` · `KR_MAX_TURNS` · `KR_SANDBOX=0` (без песочницы — опасно) ·
`KR_LOCK_PROJECT=0` · `KR_TAKE_ARTIFACTS=0` · `KR_CHECK_INTEGRITY=0` ·
`KR_KEEP_SANDBOXES` (по умолчанию 10) · `KR_JLCPARTS_DB` (каталог деталей
для `part_search`) · `KR_STALL_S` (сторож).

---

## 7. Следы прошлых прогонов

* Живой мост: `ps` → `backend/agent_bridge.py --port 8787`,
  лог `/tmp/scratchpad/bridge.log`.
* Заказы 16.10 (фонарик 15 Вт) видны в `GET /api/orders`.
* Песочницы: `speaker1` — 7 заказов, `flashlight1`/`heater1`/`transmission1` —
  по одному.
* Чекпоинты старого графа: `.run/graph.db`.
* Архив: `out/archive/`.

---

## 8. Чего не делать

* **Не копировать проекты руками** и не подкладывать симлинки «чтобы
  собралось» — чинить генератор (`scripts/project_init.py`) или сам
  конвейер. Автосоздание проекта уже есть в мосте.
* **Не гонять make-цели аппаратного конвейера вне песочницы сервиса.**
* **Не путать** CLI-прогон (без песочницы) со штатным сервисным.

---

## 9. Известные зазоры (чинить в коде, не обходить руками)

1. `scripts/project_init.py` (автосоздание проекта) генерирует
   универсальный каркас без аппаратных целей → стадии
   `kicad/spice/sim/gost` у такого проекта не запускаются. Аппаратный
   каркас надо добавить в сам генератор. Обходной путь на сейчас —
   запускать заказ на уже готовом аппаратном проекте (`TESTLIGHT2`,
   `flashlight1`).
2. В песочницу `.venv` и `node_modules` подключаются симлинками **из корня
   проекта**, а у `.krystal/projects/*` их нет → стадия `gost`
   (tscircuit/node) не соберётся. Тянуть зависимости из корня репозитория
   (`REPO_ROOT`) / `DEPS_CACHE`, а не из проекта.
