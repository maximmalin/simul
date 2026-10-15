"""Агентно-функциональный граф — современная схема исполнения.

Что здесь и почему именно так
------------------------------
**Инструменты, а не JSON из текста.** Раньше планировщик просил модель
«верни JSON», а движок вытаскивал его регуляркой. Мусор в ответе ломал
разбор. Теперь модель получает типизированные инструменты (см.
`tools.py`) и вызывает их сама; аргументы приходят разобранными и
проверенными провайдером.

**Цикл «думаю — действую — смотрю», а не один план.** Порядок заказа
заранее неизвестен: заказ «посчитай дальность и если тесты упадут —
почини» требует увидеть результат первого шага до того, как выбрать
второй. Узлы `supervisor` и `tools` образуют этот цикл.

**Настоящее прерывание человека.** Когда оператора нужно спросить,
вызывается `interrupt()` — граф встаёт, состояние сохраняется, и после
ответа продолжается **с того же места**. Прежний флаг `needs_human`
просто завершал заказ.

**Долговременный чекпоинт.** `MemorySaver` умирал вместе с процессом,
поэтому обещание «продолжится после сбоя» было неправдой. Теперь
состояние лежит в SQLite и переживает перезапуск.

**Исполнитель не умеет произвольный код.** Узлы вызывают только
объявленные в описании проекта цели. Это осознанное ограничение: в
инженерном проекте модель выбирает, что собрать, а собирает система
сборки. Агент с правом писать что угодно здесь был бы не помощью,
а источником порчи.
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import llm

from . import planner, reporter, runner as R, tools as T
from .profile import load as load_profile
from .types import (GraphState, Project, Step, final_status, new_state,
                    now_iso, progress_of)

MAX_PARALLEL = max(1, int(os.environ.get("KR_MAX_PARALLEL", "2")))
STEP_TIMEOUT = int(os.environ.get("KR_STEP_TIMEOUT", "1800"))
MAX_TURNS = int(os.environ.get("KR_MAX_TURNS", "8"))

#: Таймауты ходов модели. Первый ход — короткий: если локальная модель
#: на CPU не уложилась, заказ лучше увести на правила, чем держать
#: оператора в ожидании. Локальная 3B модель на 8 ядрах без GPU отвечает
#: около минуты — это слишком долго для первого ответа.
_FIRST_TURN_TIMEOUT = float(os.environ.get("KR_LLM_FIRST_TIMEOUT", "25"))
_TURN_TIMEOUT = float(os.environ.get("KR_LLM_TURN_TIMEOUT", "120"))

#: куда складывать состояние графов
DB_PATH = Path(os.environ.get(
    "KR_GRAPH_DB", Path(os.environ.get("KR_ROOT", ".")) / ".run" / "graph.db"))


# ==========================================================================
#  Реестр агентных функций
# ==========================================================================
@dataclass
class AgentFn:
    name: str
    title: str
    run: Callable[[GraphState], GraphState]
    after: list[str] = field(default_factory=list)
    provides: list[str] = field(default_factory=list)
    kind: str = "generic"


REGISTRY: dict[str, AgentFn] = {}
ORDER: list[str] = []


def agent(name: str, *, title: str = "", after: tuple[str, ...] = (),
          provides: tuple[str, ...] = (), kind: str = "generic"):
    """Декоратор: зарегистрировать функцию как узел графа."""
    def wrap(fn: Callable[[GraphState], GraphState]):
        if name in REGISTRY:
            raise ValueError(f"агент «{name}» уже зарегистрирован")
        REGISTRY[name] = AgentFn(
            name=name, title=title or name, run=fn,
            after=list(after), provides=list(provides), kind=kind)
        ORDER.append(name)
        return fn
    return wrap


def graph_shape() -> list[dict]:
    return [{"name": n, "title": REGISTRY[n].title, "after": REGISTRY[n].after,
             "provides": REGISTRY[n].provides, "kind": REGISTRY[n].kind}
            for n in ORDER]


# ==========================================================================
#  Проект на время прогона
# ==========================================================================
class _Ctx(threading.local):
    project: Project | None = None
    #: рабочий каталог заказа (песочница). None — работаем в корне.
    workdir: str | None = None


_CTX = _Ctx()


def current_project() -> Project:
    if _CTX.project is None:
        _CTX.project = load_profile(os.environ.get("KR_PROJECT_ROOT", "."))
    return _CTX.project


def set_project(proj: Project) -> None:
    _CTX.project = proj


def set_workdir(path: str | Path | None) -> None:
    """Задать каталог исполнения заказа.

    Пусто — команды идут в корень проекта. Так остаются прямые вызовы
    из командной строки, где заказа как такового нет и песочница
    не нужна.
    """
    _CTX.workdir = str(Path(path).resolve()) if path else None


def current_workdir() -> str | None:
    return _CTX.workdir


# ==========================================================================
#  Узел 1: supervisor — модель решает, что делать
# ==========================================================================
SUPERVISOR = """Ты — руководитель сборки проекта «{name}».

{desc}

Твои инструменты:
{tools}

Правила работы:
- сначала вызови list_targets, если не знаешь, что можно собрать;
- выбирай минимальный достаточный набор целей и фиксируй его через
  submit_plan;
- цели верхнего уровня сами тянут зависимости — не перечисляй их;
- запускай цель через run_target и **смотри на результат** прежде чем
  решать следующий шаг;
- числа бери из read_artifacts, не выдумывай;
- если продолжать нельзя (нужен доступ, нет данных, нужно решение
  инженера) — вызови ask_human.

Когда работа закончена и всё сделано — ответь одним словом: ГОТОВО.
"""


@agent("supervisor", title="Руководитель: выбор следующего действия",
       provides=("intent", "turns"))
def fn_supervisor(state: GraphState) -> GraphState:
    """Модель получает инструменты и решает: спланировать, собрать,
    прочитать факты, спросить человека или закрыть заказ."""
    proj = current_project()
    if not llm.enabled():
        state["mode"] = "rules"
        state["log"] = state.get("log", []) + [
            "LLM недоступен — работаю по правилам"]
        return state

    state["mode"] = "llm"
    tools = T.tool_schemas(proj)
    bindings = T.make_bindings(proj, lambda n: _run_single(state, proj, n),
                              on_plan=lambda tgts, intent: _set_plan(state, tgts,
                                                                    intent, proj))

    history = list(state.get("history", []))
    system = SUPERVISOR.format(
        name=proj.name,
        desc=proj.description[:400] if proj.description else "",
        tools="\n".join(f"- {s['function']['name']}: "
                        f"{s['function']['description'][:90]}" for s in tools))

    messages = [{"role": "system", "content": system}]
    messages += history[-12:]
    if not history:
        messages.append({"role": "user", "content": f"Заказ: {state.get('text', '')}"})
    elif state.get("pending_user"):
        messages.append({"role": "user", "content": state["pending_user"]})
        state["pending_user"] = ""

    turn = llm.chat(messages, tools=tools, temperature=0.0, max_tokens=900,
                    timeout=_FIRST_TURN_TIMEOUT if not history
                    else _TURN_TIMEOUT)
    if turn is None:
        state["mode"] = "rules"
        state["log"] = state.get("log", []) + [
            "модель не ответила — перехожу на правила"]
        return state

    state["turns"] = state.get("turns", 0) + 1
    state["model"] = f"{turn.provider}/{turn.model}"
    state["last_turn"] = turn.text
    state["tool_calls"] = [{"name": c.name, "args": c.args} for c in turn.calls]

    if turn.calls:
        msgs = [{"role": "assistant", "content": turn.text or None,
                 "tool_calls": [
                     {"id": c.id, "type": "function",
                      "function": {"name": c.name,
                                   "arguments": __import__("json").dumps(c.args)}}
                     for c in turn.calls]}]
        state["history"] = history + msgs
        state["pending_tool"] = [c.id for c in turn.calls]
        return state

    # вызовов нет — модель ответила текстом
    upper = (turn.text or "").strip().upper()
    if upper.startswith("ГОТОВО") or upper.startswith("DONE"):
        state["verdict"] = "done"
        state["log"] = state.get("log", []) + ["модель объявила работу завершённой"]
    state["history"] = history + [{"role": "assistant", "content": turn.text}]
    state["pending_tool"] = []
    return state


def _set_plan(state: GraphState, targets: list[str], intent: str,
              proj: Project) -> None:
    """Инструмент submit_plan принял набор целей."""
    by_name = proj.by_name()
    state["steps"] = [
        Step(id=f"s{i}", target=t, title=by_name[t].title or t,
             agent=proj.agent_owner.get(t, "agent.build")).as_dict()
        for i, t in enumerate(targets)]
    state["intent"] = intent or state.get("intent", "")
    state["planner"] = "llm"
    state["progress"] = progress_of(state["steps"])
    state["log"] = state.get("log", []) + [f"план от модели: {targets}"]


def _run_single(state: GraphState, proj: Project, name: str) -> dict:
    """Инструмент run_target: собрать одну цель и обновить шаг."""
    steps = [dict(s) for s in state.get("steps", [])]
    found = next((s for s in steps if s["target"] == name), None)
    if found is None:
        found = Step(id=f"x{len(steps)}", target=name,
                     title=proj.target(name).title if proj.target(name) else name,
                     agent=proj.agent_owner.get(name, "agent.build")).as_dict()
        steps.append(found)
        state["steps"] = steps
    res = _execute_one(proj, found)
    steps[steps.index(found)] = res
    state["steps"] = steps
    state["progress"] = progress_of(steps)
    return res


def _execute_one(proj: Project, s: dict) -> dict:
    """Собрать одну цель.

    Команда выполняется в **рабочем каталоге заказа** (песочнице), а не в
    корне проекта: исходники проекта — опора работы агентов, и заказ не
    должен иметь возможности их переписать. Команда при этом собирается
    по описанию настоящего проекта — она одинакова в обоих местах.
    """
    target = proj.target(s["target"])
    if target is None:
        s.update(status="failed", exit_code=-2, finished_at=now_iso(),
                 error=f"цели «{s['target']}» нет в проекте")
        return s
    try:
        cmd = (R.get(target.runner).command(Path(proj.root), target)
               if target.runner else R.get(proj.runner)
               .command(Path(proj.root), target))
    except R.BuildError as exc:
        s.update(status="failed", exit_code=-2, finished_at=now_iso(),
                 error=str(exc))
        return s

    cwd = _workdir(proj)
    s.update(status="running", started_at=now_iso(), command=cmd,
             attempt=s.get("attempt", 0) + 1)
    if cwd != Path(proj.root):
        s["sandbox"] = str(cwd)
    rc, out, dur = R.run(cwd, cmd, timeout=STEP_TIMEOUT,
                         jobs=MAX_PARALLEL)
    s.update(status="done" if rc == 0 else "failed", exit_code=rc,
             duration_s=round(dur, 2), finished_at=now_iso(), log=out[-80:])
    if rc != 0:
        tail = [ln for ln in out[-6:] if ln.strip()]
        s["error"] = "; ".join(tail)[:400] or f"код возврата {rc}"
    return s


def _workdir(proj: Project) -> Path:
    """Где выполнять команды заказа.

    По умолчанию — корень проекта: без заказа и без песочницы так
    верно, и это же поведение у прямых вызовов из CLI. Когда заказ
    идёт через сервис, тот создаёт песочницу и кладёт путь сюда.
    """
    wd = _CTX.workdir
    return Path(wd) if wd else Path(proj.root)


# ==========================================================================
#  Узел 2: tools — исполнение вызовов модели
# ==========================================================================
@agent("tools", title="Исполнение вызовов инструментов", after=("supervisor",),
       provides=("steps", "progress"))
def fn_tools(state: GraphState) -> GraphState:
    """Выполнить то, что модель попросила, и вернуть ей результат."""
    proj = current_project()
    calls = state.get("tool_calls") or []
    ids = state.get("pending_tool") or [f"call_{i}" for i in range(len(calls))]

    if not calls:
        state["pending_tool"] = []
        return state

    bindings = T.make_bindings(proj, lambda n: _run_single(state, proj, n),
                              on_plan=lambda tgts, intent: _set_plan(state, tgts,
                                                                    intent, proj))
    results: list[dict] = []
    ask: str | None = None

    for cid, call in zip(ids, calls):
        out = T.invoke(bindings, call.name, call.args)
        question = T.is_ask_human(out)
        if question:
            ask = question
        results.append({"id": cid, "name": call.name, "content": out[:1500]})

    history = list(state.get("history", []))
    for r in results:
        history.append({"role": "tool", "tool_call_id": r["id"],
                        "name": r["name"], "content": r["content"]})
    state["history"] = history[-16:]
    state["pending_tool"] = []
    state["progress"] = progress_of(state.get("steps", []))

    if ask:
        state["question"] = ask
        state["verdict"] = "needs_human"
        state["log"] = state.get("log", []) + [f"запрос оператору: {ask[:120]}"]
    return state


# ==========================================================================
#  Узел 3: interrupt — настоящая остановка человека
# ==========================================================================
@agent("await_human", title="Ожидание решения оператора",
       after=("tools",), provides=("answer",))
def fn_await_human(state: GraphState) -> GraphState:
    """Граф встаёт и ждёт ответа оператора.

    Состояние сохранено в чекпоинте, поэтому после ответа работа
    продолжится с этого места, а не начнётся заново. Ответ оператора
    кладётся в состояние через `Command(resume=...)`.
    """
    try:
        from langgraph.types import interrupt
    except ImportError:
        state["answer"] = ""
        return state

    answer = interrupt({
        "kind": "ask_human",
        "question": state.get("question", ""),
        "order": state.get("text", ""),
        "done": [s["target"] for s in state.get("steps", [])
                 if s.get("status") == "done"],
        "failed": [s["target"] for s in state.get("steps", [])
                   if s.get("status") == "failed"],
    })
    state["answer"] = str(answer or "")
    state["question"] = ""
    state["verdict"] = "continue"
    state["pending_user"] = f"Ответ оператора: {state['answer']}"
    state["log"] = state.get("log", []) + [
        f"получен ответ оператора: {state['answer'][:120]}"]
    return state


# ==========================================================================
#  Узел 4: fallback — планирование правилами
# ==========================================================================
@agent("plan_rules", title="Планирование правилами", provides=("steps",))
def fn_plan_rules(state: GraphState) -> GraphState:
    """Работает, когда модель недоступна или не дала плана."""
    proj = current_project()
    if state.get("steps"):
        state["mode"] = state.get("mode") or "rules"
        return state
    r = planner.plan(state.get("text", ""), proj)
    state.update(intent=r["intent"], planner=r["planner"],
                 steps=r["steps"], progress=progress_of(r["steps"]))
    state["log"] = state.get("log", []) + [
        f"план по правилам [{r['planner']}]: {r.get('targets')}"]
    return state


@agent("execute_rules", title="Исполнение плана", after=("plan_rules",),
       provides=("steps", "progress", "cursor"), kind="parallel")
def fn_execute_rules(state: GraphState) -> GraphState:
    """Выполнить весь план. Конфликтующие цели — строго по очереди."""
    proj = current_project()
    steps: list[dict] = [dict(s) for s in state.get("steps", [])]
    if not steps:
        return state
    for grp in _conflict_groups(steps, proj):
        if len(grp) == 1:
            _execute_one(proj, steps[grp[0]])
        else:
            with ThreadPoolExecutor(max_workers=min(len(grp), MAX_PARALLEL)) as ex:
                list(ex.map(lambda i: _execute_one(proj, steps[i]), grp))
        state["log"] = state.get("log", []) + ["выполнено: " + ", ".join(
            f"{steps[i]['target']}={steps[i]['status']}" for i in grp)]
    state["steps"] = steps
    state["cursor"] = len(steps)
    state["progress"] = progress_of(steps)
    return state


@agent("review", title="Проверка результата", after=("execute_rules",),
       provides=("verdict", "question"))
def fn_review(state: GraphState) -> GraphState:
    """Решить: всё сделано, продолжать или нужен человек."""
    steps = state.get("steps", [])
    done = sum(1 for s in steps if s.get("status") == "done")
    bad = [s for s in steps if s.get("status") in ("failed", "blocked")]
    if bad:
        state["verdict"] = "needs_human"
        state["question"] = (f"Цель «{bad[0].get('target')}» завершилась "
                             f"с ошибкой. {str(bad[0].get('error') or '')[:200]}")
    else:
        state["verdict"] = "done"
    state["note"] = f"{done}/{len(steps)} целей выполнено"
    return state


# ==========================================================================
#  Узел 5: отчёт
# ==========================================================================
@agent("report", title="Факты и отчёт", provides=("facts", "report", "artifacts"),
       kind="terminal")
def fn_report(state: GraphState) -> GraphState:
    proj = current_project()
    reporter.node(state, proj)
    state["status"] = final_status(state.get("steps", []))
    state["finished_at"] = now_iso()
    return state


# ==========================================================================
#  Конфликты целей
# ==========================================================================
def _conflict_groups(steps: list[dict], proj: Project) -> list[list[int]]:
    """Группы, которые можно выполнять одновременно.

    Внутри группы нижние цели не пересекаются — значит пишут в разные
    файлы. Группы выполняются последовательно. Именно поэтому планировщику
    не нужно знать граф: конфликт считается по транзитивным зависимостям.
    """
    by_name = proj.by_name()

    def footprint(name: str) -> set[str]:
        out, stack = {name}, [name]
        while stack:
            tgt = by_name.get(stack.pop())
            if not tgt:
                continue
            for d in tgt.depends_on:
                if d not in out:
                    out.add(d)
                    stack.append(d)
        return out

    fps = [footprint(s["target"]) for s in steps]
    groups: list[list[int]] = []
    placed: set[int] = set()
    for i in range(len(steps)):
        if i in placed:
            continue
        grp, used = [i], set(fps[i])
        for j in range(i + 1, len(steps)):
            if j in placed or (fps[j] & used):
                continue
            grp.append(j)
            used |= fps[j]
            placed.add(j)
        placed.add(i)
        groups.append(grp)
    return groups


# ==========================================================================
#  Сборка и запуск
# ==========================================================================
def _has_langgraph() -> bool:
    try:
        import langgraph.graph  # noqa: F401
        return True
    except ImportError:
        return False


def have_langgraph() -> bool:
    return _has_langgraph()


def _checkpointer():
    """Долговременный чекпоинт; при неудаче — в памяти.

    SQLite переживает перезапуск процесса: прерванный заказ можно
    продолжить после падения моста. Если файл недоступен, работаем
    в памяти и не делаем вид, что состояние сохраняется.
    """
    try:
        from langgraph.checkpoint.sqlite import SqliteSaver
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        saver = SqliteSaver(conn)
        saver.setup() if hasattr(saver, "setup") else None
        return saver, True
    except Exception:                                            # noqa: BLE001
        try:
            from langgraph.checkpoint.memory import MemorySaver
            return MemorySaver(), False
        except ImportError:
            return None, False


def build():
    """Собрать StateGraph. None — если нет langgraph."""
    try:
        from langgraph.graph import END, START, StateGraph
    except ImportError:
        return None

    g = StateGraph(GraphState)
    for name in ORDER:
        g.add_node(name, _wrap(name))

    # Маршрут зависит от режима: с моделью работает цикл
    # «руководитель → инструменты», без неё — линейный план.
    def after_supervisor(s: GraphState) -> str:
        if s.get("verdict") == "needs_human":
            return "await_human"
        if s.get("pending_tool"):
            return "tools"
        if s.get("mode") == "llm":
            return "report" if _llm_says_done(s) else "tools"
        return "plan_rules"

    def after_tools(s: GraphState) -> str:
        if s.get("verdict") == "needs_human":
            return "await_human"
        return "report" if _turns_exhausted(s) else "supervisor"

    def after_await(s: GraphState) -> str:
        return "supervisor"

    def after_rules(s: GraphState) -> str:
        # без модели: план -> исполнение -> проверка
        return "execute_rules" if s.get("steps") else "report"

    def after_review(s: GraphState) -> str:
        return "await_human" if s.get("verdict") == "needs_human" else "report"

    g.add_edge(START, "supervisor")
    g.add_conditional_edges("supervisor", after_supervisor, {
        "tools": "tools", "await_human": "await_human",
        "plan_rules": "plan_rules", "report": "report"})
    g.add_conditional_edges("tools", after_tools, {
        "supervisor": "supervisor", "await_human": "await_human",
        "report": "report"})
    g.add_edge("await_human", "supervisor")
    g.add_conditional_edges("plan_rules", after_rules, {
        "execute_rules": "execute_rules", "report": "report"})
    g.add_edge("execute_rules", "review")
    g.add_conditional_edges("review", after_review,
                            {"await_human": "await_human", "report": "report"})
    g.add_edge("report", END)
    return g


def _llm_says_done(s: GraphState) -> bool:
    txt = (s.get("last_turn") or "").strip().upper()
    return txt.startswith("ГОТОВО") or txt.startswith("DONE")


def _turns_exhausted(s: GraphState) -> bool:
    return s.get("turns", 0) >= MAX_TURNS


def _wrap(name: str):
    fn = REGISTRY[name].run

    def run(state: GraphState) -> GraphState:
        try:
            return fn(dict(state))
        except Exception as exc:                                # noqa: BLE001
            out = dict(state)
            out["log"] = out.get("log", []) + [
                f"узел «{name}» упал: {exc}",
                traceback.format_exc()[-500:]]
            out["status"] = "failed"
            return out

    return run


def run(text: str, proj: Project, order_id: str | None = None,
        resume: str | None = None) -> dict:
    """Прогнать заказ по графу."""
    g = build()
    if g is None:
        raise RuntimeError("langgraph не установлен")

    from langgraph.checkpoint.memory import MemorySaver  # noqa: F401

    saver, durable = _checkpointer()
    app = g.compile(checkpointer=saver) if saver else g.compile()

    set_project(proj)
    state = new_state(text, order_id, proj.name)
    state["durable"] = durable
    cfg = {"configurable": {"thread_id": resume or state["order_id"]}}
    try:
        final = app.invoke(state, cfg)
    finally:
        pass
    return dict(final)


def resume(thread_id: str, answer: str = "", root: str | None = None) -> dict:
    """Продолжить прерванный заказ после ответа оператора.

    Состояние лежит в чекпоинте, поэтому работа продолжается с места
    остановки, а не начинается заново.
    """
    try:
        from langgraph.types import Command
    except ImportError as exc:
        raise RuntimeError("langgraph не установлен") from exc

    g = build()
    if g is None:
        raise RuntimeError("langgraph не установлен")
    saver, _durable = _checkpointer()
    app = g.compile(checkpointer=saver) if saver else g.compile()

    proj = load_profile(root or os.environ.get("KR_PROJECT_ROOT", "."))
    set_project(proj)
    out = app.invoke(Command(resume=answer),
                     {"configurable": {"thread_id": thread_id}})
    return dict(out)


def run_plain(text: str, proj: Project, order_id: str | None = None) -> dict:
    """Тот же ход без langgraph: узлы вызываются по порядку.

    Запасной путь. Теряются чекпоинты и прерывание, но проект работает
    без установленного langgraph.
    """
    set_project(proj)
    state = new_state(text, order_id, proj.name)
    state["mode"] = "plain"
    for name in ("plan_rules", "execute_rules", "review", "report"):
        state = REGISTRY[name].run(dict(state))
    return dict(state)