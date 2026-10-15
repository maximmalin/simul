"""
Krystal Radio — мультиагентный оркестратор заказов.

Поток заказа
------------
    frontend  ──POST /api/order──►  Order (свободный текст)
                                        │
                                        ▼
                                 Planner  (разбор заказа)
                                        │  список шагов: агент + цель
                                        ▼
                                 Executor (топологический, параллельный)
                                        │  каждый шаг = make-цель или скрипт
                                        ▼
                                 Reporter (сводка, LLM если есть ключ)
                                        │
    frontend  ◄─GET /api/order/<id>──┘

Планировщик работает в двух режимах:

* **LLM** — если задан `KR_LLM_KEY`, модель получает список доступных
  целей и агентов и возвращает план в JSON.
* **Правила** — без ключа. Детерминированный разбор ключевых слов с
  готовыми шаблонами планов. Работает всегда, включая оффлайн.

Исполнитель запускает шаги, у которых все зависимости выполнены, параллельно
(ограничение — `KR_MAX_PARALLEL`, по умолчанию 3). Каждый шаг порождает
джобу моста (`agent_bridge.spawn_job`), поэтому лог и статус видны и во
встроенном интерфейсе, и в Krystal-вкладке.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone

import agent_bridge as BR
import krconfig
from agent_runtime import ROLE_MAP, agent_codename, local_agents, llm_complete

MAX_PARALLEL = int(os.environ.get("KR_MAX_PARALLEL", "3"))
STEP_TIMEOUT = int(os.environ.get("KR_STEP_TIMEOUT", "1800"))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ==========================================================================
# Модель
# ==========================================================================
@dataclass
class Step:
    id: str
    title: str
    agent_id: str
    kind: str                      # "make" | "script"
    target: str                    # make-цель или имя скрипта
    args: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)
    status: str = "pending"        # pending|running|done|failed|skipped
    started_at: str | None = None
    finished_at: str | None = None
    job_id: str | None = None
    exit_code: int | None = None
    error: str | None = None
    note: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class Order:
    id: str
    text: str
    planner: str = "rules"         # rules | llm
    intent: str = "generic"
    created_at: str = field(default_factory=_now)
    finished_at: str | None = None
    status: str = "queued"         # queued|running|done|failed|partial
    steps: list[Step] = field(default_factory=list)
    report: str = ""
    project_id: str | None = None

    def as_dict(self) -> dict:
        d = asdict(self)
        d["steps"] = [s.as_dict() for s in self.steps]
        d["progress"] = self.progress()
        return d

    def progress(self) -> int:
        if not self.steps:
            return 0
        done = sum(1 for s in self.steps
                   if s.status in ("done", "skipped", "failed"))
        return int(round(100 * done / len(self.steps)))


ORDERS: dict[str, Order] = {}
_LOCK = threading.Lock()


# ==========================================================================
# Планировщик
# ==========================================================================
#: Агент, отвечающий за каждую make-цель (для шаблона плана).
TARGET_OWNER: dict[str, str] = {
    "netlist": "AGENT-MK-01",
    "spice": "AGENT-MK-01",
    "pcb": "AGENT-KICAD-02",
    "publish": "AGENT-KICAD-02",
    "board-svg": "AGENT-KICAD-02",
    "drawings": "AGENT-DF-02",
    "cad": "AGENT-DF-02",
    "viewer": "AGENT-MK-03",
    "gnuradio": "AGENT-GNUR-01",
    "link-budget": "AGENT-DF-01",
    "direction": "AGENT-DF-01",
    "page": "AGENT-MK-02",
    "test": "AGENT-TST-01",
    "thermal": "AGENT-TH-01",
    "reliability": "AGENT-RL-01",
    "enclosure": "AGENT-ENC-01",
    "erc": "AGENT-MK-01",
    "drc": "AGENT-TST-02",
    "manifest": "AGENT-DOC-03",
}

#: Ключевые слова -> шаги. Первый подходящий шаблон побеждает.
INTENTS: list[tuple[str, list[tuple[str, str]]]] = [
    ("everything", [("make", "all")]),
    ("build_all", [("make", "all")]),

    ("page", [
        ("make", "publish"), ("make", "board-svg"), ("make", "drawings"),
        ("make", "viewer"), ("make", "gnuradio"), ("make", "link-budget"),
        ("make", "direction"), ("make", "page"),
    ]),
    ("report", [
        ("make", "publish"), ("make", "board-svg"), ("make", "drawings"),
        ("make", "viewer"), ("make", "page"),
    ]),

    ("waterfall", [("make", "gnuradio")]),
    ("spectrum", [("make", "gnuradio")]),
    ("cosim", [("make", "gnuradio")]),

    ("distance", [("make", "link-budget")]),
    ("range", [("make", "link-budget")]),

    ("bearing", [("make", "direction")]),
    ("antennasim", [("make", "direction")]),

    ("thermal", [("make", "thermal")]),
    ("temperature", [("make", "thermal")]),
    ("heat", [("make", "thermal")]),

    ("reliability", [("make", "reliability")]),
    ("mtbf", [("make", "reliability")]),

    ("enclosure", [("make", "enclosure")]),
    ("case", [("make", "enclosure")]),
    ("корпус", [("make", "enclosure")]),

    ("pcb", [("make", "pcb"), ("make", "publish")]),
    ("board", [("make", "pcb"), ("make", "publish")]),
    ("gerber", [("make", "pcb"), ("make", "publish")]),

    ("drawings", [("make", "cad"), ("make", "drawings")]),
    ("gost", [("make", "cad"), ("make", "drawings")]),
    ("чертеж", [("make", "cad"), ("make", "drawings")]),

    ("tests", [("make", "test")]),
    ("test", [("make", "test")]),
    ("проверк", [("make", "test")]),
    ("тест", [("make", "test")]),

    ("erc", [("make", "erc")]),
    ("drc", [("make", "drc")]),
    ("manifest", [("make", "manifest")]),
]

KEYWORDS = {
    "page": r"страниц|отчёт|отчет|page|report",
    "waterfall": r"водопад|waterfall|спектр|spectrum|gnuradio|кас-сим|ко-сим|cosim|поток",
    "distance": r"дальност|дистанц|distance|range|радиовидим|приёмк|приемк",
    "bearing": r"пеленг|направлен|bearing|antennasim|интерферометр",
    "thermal": r"теплов|thermal|нагрев|температур|temperature|охлажд",
    "reliability": r"надёжн|надежн|reliability|mtbf|отказ",
    "enclosure": r"корпус|коробк|кейс|enclosure|shell|экран",
    "pcb": r"\bpcb\b|плата|board|gerber|гepбер|разводк|трассир",
    "drawings": r"чертеж|чертеж|gost|гост|dxf|drawing",
    "tests": r"тест|проверк|test|pytest|прогон",
    "erc": r"\berc\b",
    "drc": r"\bdrc\b",
    "manifest": r"манифест|manifest|спецификац|bom",
    "everything": r"всё|все|everything|полн(ый|ая) сборк|make all|собери всё",
}


def _detect_intents(text: str) -> list[str]:
    """Наборы целей, соответствующие заказу (в порядке шаблона)."""
    t = text.lower()
    hits: list[tuple[int, str]] = []
    for name, rx in KEYWORDS.items():
        if re.search(rx, t):
            hits.append((INTENT_ORDER.index(name) if name in INTENT_ORDER
                         else 99, name))
    hits.sort()
    return [name for _, name in hits]


#: порядок шаблонов = приоритет при совпадении нескольких ключевых слов
INTENT_ORDER = [name for name, _ in INTENTS]


def plan_by_rules(text: str) -> tuple[str, list[tuple[str, str]]]:
    """Детерминированный план: (intent, [(kind, target), …]).

    Заказ может содержать несколько намерений («собери всё и проверь
    тестами»), поэтому шаблоны **объединяются**: `all` поглощает более
    узкие шаги, остальные дописываются в конце.
    """
    intents = _detect_intents(text)
    if not intents:
        return "generic", []

    merged: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def add(kind: str, target: str) -> None:
        key = (kind, target)
        if key not in seen:
            seen.add(key)
            merged.append(key)

    for name in intents:
        for iname, steps in INTENTS:
            if iname == name:
                for k, t in steps:
                    add(k, t)
                break

    # `all` делает остальные шаги избыточными: он вызывает и test,
    # и thermal, и reliability, и enclosure.
    if ("make", "all") in seen:
        merged = [("make", "all")]

    return "+".join(intents[:3]), merged


PLANNER_SYSTEM = """Ты — планировщик заказов проекта Krystal Radio (zero-IF SDR).
Ты получаешь заказ оператора и список ДОСТУПНЫХ целей сборки.
Ответь ТОЛЬКО JSON без пояснений:

{"intent": "<короткий тег>", "steps": [{"kind":"make","target":"<цель>","title":"<что делает>","depends_on":[< индексы предыдущих шагов >]}]}

Правила:
- kind: "make" (цель из списка) либо "script" (имя из списка скриптов)
- цель должна быть ровно из списка доступных
- зависимости указывай индексами шагов, которые должны завершиться раньше
- шагов должно быть столько, сколько реально нужно для заказа, обычно 1–6
"""


def plan_by_llm(text: str) -> tuple[str, list[dict]] | None:
    """План от LLM; None — если ключа нет или ответ не разобран."""
    targets = ", ".join(BR.ALLOWED_TARGETS)
    scripts = ", ".join(f"{k} -> {v[1]}" for k, v in BR.ALLOWED_SCRIPTS.items())
    ctx = (f"Доступные make-цели: {targets}\n"
           f"Доступные скрипты: {scripts}")
    out = llm_complete(text, ctx)
    if not out:
        return None
    m = re.search(r"\{.*\}", out, re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    steps = data.get("steps")
    if not isinstance(steps, list) or not steps:
        return None
    clean = []
    for s in steps:
        kind = s.get("kind", "make")
        target = str(s.get("target", ""))
        ok = (target in BR.ALLOWED_TARGETS if kind == "make"
              else target in BR.ALLOWED_SCRIPTS)
        if ok:
            clean.append({"kind": kind, "target": target,
                          "title": s.get("title") or target,
                          "depends_on": s.get("depends_on") or []})
    if not clean:
        return None
    return str(data.get("intent", "llm")), clean


def build_plan(text: str) -> tuple[str, str, list[Step]]:
    """План заказа: (planner, intent, steps)."""
    llm = plan_by_llm(text)
    if llm:
        intent, raw = llm
        steps = []
        for i, s in enumerate(raw):
            deps = [f"s{j}" for j in s.get("depends_on", [])
                    if isinstance(j, int) and 0 <= j < i]
            steps.append(Step(
                id=f"s{i}", title=s["title"],
                agent_id=TARGET_OWNER.get(s["target"], "AGENT-MK-01"),
                kind=s["kind"], target=s["target"], depends_on=deps))
        return "llm", intent, steps

    intent, targets = plan_by_rules(text)
    steps = []
    for i, (kind, target) in enumerate(targets):
        steps.append(Step(
            id=f"s{i}", title=_step_title(kind, target),
            agent_id=TARGET_OWNER.get(target, "AGENT-MK-01"),
            kind=kind, target=target))
    return "rules", intent, steps


def _step_title(kind: str, target: str) -> str:
    if kind == "make":
        return BR.ALLOWED_TARGETS.get(target, target)
    return BR.ALLOWED_SCRIPTS.get(target, ("", target))[1]


# ==========================================================================
# Исполнитель
# ==========================================================================
def _deps_ok(step: Step, by_id: dict[str, Step]) -> bool:
    return all(by_id[d].status in ("done", "skipped")
               for d in step.depends_on if d in by_id)


def _run_step(order: Order, step: Step) -> None:
    """Выполнить один шаг, дождаться завершения джобы."""
    by_id = {s.id: s for s in order.steps}
    if not _deps_ok(step, by_id):
        failed = [by_id[d].target for d in step.depends_on
                  if d in by_id and by_id[d].status == "failed"]
        step.status = "skipped"
        step.error = f"зависимость не выполнена: {', '.join(failed)}"
        return

    step.status = "running"
    step.started_at = _now()

    if step.kind == "make":
        if step.target not in BR.ALLOWED_TARGETS:
            step.status, step.error = "failed", "цель не разрешена"
            return
        job = BR.spawn_job(f"{step.agent_id}: make {step.target}",
                           ["make", step.target])
    else:
        if step.target not in BR.ALLOWED_SCRIPTS:
            step.status, step.error = "failed", "скрипт не разрешён"
            return
        rel, _d = BR.ALLOWED_SCRIPTS[step.target]
        argv = [os.sys.executable, rel] + step.args
        job = BR.spawn_job(f"{step.agent_id}: {rel}", argv)

    job.agent_id = step.agent_id
    job.target_kind = step.kind
    job.make_target = step.target
    step.job_id = job.id

    t0 = time.monotonic()
    while True:
        time.sleep(0.7)
        d = BR.JOBS.get(job.id)
        if d is None:
            break
        if d.status in ("done", "failed"):
            break
        if time.monotonic() - t0 > STEP_TIMEOUT:
            step.status, step.error = "failed", "таймаут шага"
            step.finished_at = _now()
            return

    d = BR.JOBS.get(job.id)
    step.exit_code = d.exit_code if d else -1
    step.finished_at = _now()
    if d and d.status == "done" and d.exit_code == 0:
        step.status = "done"
    else:
        step.status = "failed"
        tail = [ln for ln in (d.log[-4:] if d else []) if ln.strip()]
        step.error = "; ".join(tail)[:300] or f"код возврата {step.exit_code}"


def execute(order: Order) -> None:
    """Выполнить план: шаги без зависимостей идут параллельно."""
    order.status = "running"
    by_id = {s.id: s for s in order.steps}
    remaining = list(order.steps)

    with ThreadPoolExecutor(max_workers=MAX_PARALLEL) as pool:
        inflight: dict = {}
        while remaining or inflight:
            # запустить всё, что готово
            ready = [s for s in remaining if _deps_ok(s, by_id)]
            for s in ready:
                remaining.remove(s)
                inflight[pool.submit(_run_step, order, s)] = s
            # дождаться завершения любой задачи
            if not inflight:
                # остались только циклические зависимости
                for s in remaining:
                    s.status = "skipped"
                    s.error = "циклическая зависимость"
                break
            done = [f for f in list(inflight) if f.done()]
            for f in done:
                f.result()
                inflight.pop(f)
            if inflight and not done:
                time.sleep(0.4)

    ok = sum(1 for s in order.steps if s.status == "done")
    bad = sum(1 for s in order.steps if s.status == "failed")
    order.status = ("done" if bad == 0 else
                    "failed" if ok == 0 else "partial")
    order.finished_at = _now()
    order.report = report(order)


# ==========================================================================
# Отчёт
# ==========================================================================
def report(order: Order) -> str:
    """Сводка по заказу: LLM, если доступен, иначе структурный отчёт."""
    lines = [f"Заказ: {order.text}", ""]
    for s in order.steps:
        mark = {"done": "OK", "failed": "FAIL", "skipped": "SKIP"}.get(
            s.status, s.status.upper())
        who = agent_codename(s.agent_id)
        lines.append(f"[{mark}] {s.title} — {who} "
                     f"(make {s.target})" if s.kind == "make" else
                     f"[{mark}] {s.title} — {who} (скрипт {s.target})")
        if s.error:
            lines.append(f"        {s.error}")
    lines.append("")
    lines.append(f"Итог: {order.status}, выполнено "
                 f"{sum(1 for s in order.steps if s.status == 'done')}"
                 f"/{len(order.steps)} шагов.")

    structured = "\n".join(lines)
    if not os.environ.get("KR_LLM_KEY"):
        return structured
    summary = llm_complete(
        "Сделай краткое резюме (2–4 предложения) по результату заказа ниже.",
        structured)
    return f"{summary}\n\n---\n{structured}" if summary else structured


# ==========================================================================
# Публичный вход
# ==========================================================================
def submit(text: str, project_id: str | None = None) -> Order:
    """Принять заказ, построить план и запустить в фоне."""
    planner, intent, steps = build_plan(text)
    order = Order(id=uuid.uuid4().hex[:12], text=text, planner=planner,
                  intent=intent, steps=steps, project_id=project_id)
    if not steps:
        order.status = "failed"
        order.finished_at = _now()
        order.report = (
            "Не удалось сопоставить заказ с доступными целями.\n"
            f"Доступные make-цели: {', '.join(BR.ALLOWED_TARGETS)}\n"
            f"Доступные скрипты: {', '.join(BR.ALLOWED_SCRIPTS)}")
        with _LOCK:
            ORDERS[order.id] = order
        return order

    with _LOCK:
        ORDERS[order.id] = order
    threading.Thread(target=execute, args=(order,), daemon=True).start()
    return order


def get(order_id: str) -> Order | None:
    return ORDERS.get(order_id)


def list_orders(limit: int = 20) -> list[dict]:
    with _LOCK:
        items = sorted(ORDERS.values(), key=lambda o: o.created_at,
                       reverse=True)[:limit]
        return [{"id": o.id, "text": o.text, "status": o.status,
                 "intent": o.intent, "planner": o.planner,
                 "progress": o.progress(),
                 "created_at": o.created_at, "finished_at": o.finished_at,
                 "steps_total": len(o.steps)}
                for o in items]


def capabilities() -> dict:
    """Что вообще оркестратор умеет — для интерфейса."""
    return {
        "intents": INTENT_ORDER,
        "targets": BR.ALLOWED_TARGETS,
        "scripts": {k: v[1] for k, v in BR.ALLOWED_SCRIPTS.items()},
        "target_owner": TARGET_OWNER,
        "max_parallel": MAX_PARALLEL,
        "planner": "llm" if os.environ.get("KR_LLM_KEY") else "rules",
        "examples": [
            "Собери всё и запусти тесты",
            "Собери страницу отчёта",
            "Сгенерируй водопад",
            "Посчитай дальность приёма",
            "Построй корпус",
            "Проверь тепловое и надёжность",
        ],
    }


#: ключевые слова -> какая make-цель задействуется (для подсказок в UI)
SUGGESTIONS = [
    {"text": "Собери всё и проверь тестами", "intent": "everything"},
    {"text": "Собери страницу отчёта", "intent": "page"},
    {"text": "Сгенерируй водопад GNU Radio", "intent": "waterfall"},
    {"text": "Посчитай дальность приёма", "intent": "distance"},
    {"text": "Построй пеленгацию", "intent": "bearing"},
    {"text": "Проверь тепловой режим", "intent": "thermal"},
    {"text": "Посчитай надёжность", "intent": "reliability"},
    {"text": "Смоделируй корпус", "intent": "enclosure"},
    {"text": "Пересобери плату", "intent": "pcb"},
    {"text": "Запусти тесты", "intent": "tests"},
]