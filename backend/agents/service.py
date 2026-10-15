"""Сервис заказов: хранение, запуск и выдача в формате интерфейса.

Между графом и HTTP-мостом. Граф возвращает своё состояние, а
интерфейс ждёт поля, которые он уже умеет рисовать. Здесь эти две формы
сводятся, чтобы фронтенд не пришлось переписывать.

Соответствие полей:

    граф (types.Step)              интерфейс
    ---------------------------    ---------------------------
    target                         target
    title                          title
    agent                          agent_id
    status                         status
    exit_code, duration_s, error   exit_code, duration_s, error
    log_tail                       log_tail
    started_at / finished_at       started_at / finished_at
"""

from __future__ import annotations

import threading
import time
import traceback
from typing import Any, Callable

from . import engine
from .runner import available as runners_available
from .profile import load as load_project
from .engine import have_langgraph
from .types import Project, new_state, now_iso, progress_of

#: заказы в памяти; сколько хранить
ORDERS: dict[str, dict] = {}
MAX_ORDERS = 60
_LOCK = threading.Lock()


# ==========================================================================
#  Внешняя форма
# ==========================================================================
def _step_view(s: Any) -> dict:
    """Шаг графа -> шаг интерфейса.

    Имена полей в графе и в интерфейсе различаются исторически:
    граф говорит ``agent``, интерфейс ждёт ``agent_id``. Переименование
    делается здесь, один раз, чтобы ни граф, ни компонент не пришлось
    трогать.
    """
    d = s.as_dict() if hasattr(s, "as_dict") else dict(s)
    d["agent_id"] = d.get("agent", "agent.build")
    d["kind"] = "make"
    d.setdefault("title", d.get("target", ""))
    d.setdefault("job_id", None)
    return d


def _order_view(o: dict) -> dict:
    """Заказ в том виде, в котором его читает интерфейс."""
    steps = [_step_view(s) for s in o.get("steps", [])]
    return {
        "id": o["id"],
        "text": o["text"],
        "status": o.get("status", "queued"),
        "intent": o.get("intent", ""),
        "planner": o.get("planner", "rules"),
        "progress": o.get("progress", 0),
        "verdict": o.get("verdict", ""),
        "question": o.get("question", ""),
        "note": o.get("note", ""),
        "project": o.get("project", ""),
        "created_at": o["created_at"],
        "finished_at": o.get("finished_at"),
        "steps": steps,
        "report": o.get("report", ""),
        "logs": o.get("logs", []),
        "engine": "langgraph" if have_langgraph() else "plain",
    }


def get(order_id: str) -> dict | None:
    with _LOCK:
        o = ORDERS.get(order_id)
    return _order_view(o) if o else None


def list_orders(limit: int = 20) -> list[dict]:
    with _LOCK:
        items = sorted(ORDERS.values(), key=lambda o: o["created_at"],
                       reverse=True)[:limit]
    return [{"id": o["id"], "text": o["text"], "status": o.get("status"),
             "intent": o.get("intent"), "planner": o.get("planner"),
             "progress": o.get("progress", 0), "verdict": o.get("verdict", ""),
             "created_at": o["created_at"], "finished_at": o.get("finished_at"),
             "steps_total": len(o.get("steps", []))}
            for o in items]


def _trim() -> None:
    """Не растим память бесконечно."""
    if len(ORDERS) <= MAX_ORDERS:
        return
    oldest = sorted(ORDERS.values(), key=lambda o: o["created_at"])
    for o in oldest[:len(ORDERS) - MAX_ORDERS]:
        ORDERS.pop(o["id"], None)


# ==========================================================================
#  Приём заказа
# ==========================================================================
def submit(text: str, root: str = ".", profile: str = "",
           project_id: str | None = None) -> dict:
    """Принять заказ, запустить в фоне, вернуть начальное состояние."""
    text = (text or "").strip()
    proj = load_project(root, profile=profile)

    order = {
        "id": new_state(text)["order_id"],
        "text": text,
        "project": proj.name,
        "project_id": project_id,
        "status": "queued",
        "intent": "",
        "planner": "rules",
        "progress": 0,
        "verdict": "",
        "question": "",
        "note": "",
        "created_at": now_iso(),
        "finished_at": None,
        "steps": [],
        "report": "",
        "logs": [],
    }
    with _LOCK:
        ORDERS[order["id"]] = order
        _trim()

    threading.Thread(target=_run, args=(order["id"], proj), daemon=True).start()
    return _order_view(order)


def _run(order_id: str, proj: Project) -> None:
    """Прогнать заказ и записать результат обратно в хранилище."""
    with _LOCK:
        order = ORDERS.get(order_id)
    if order is None:
        return
    order["status"] = "running"
    try:
        res = (engine.run(order["text"], proj, order_id) if have_langgraph()
               else engine.run_plain(order["text"], proj, order_id))
    except Exception as exc:                                    # noqa: BLE001
        order["status"] = "failed"
        order["report"] = f"Заказ не выполнен: {exc}\n\n{traceback.format_exc()[-600:]}"
        order["finished_at"] = now_iso()
        return

    with _LOCK:
        order.update({
            "status": res.get("status", "done"),
            "intent": res.get("intent", ""),
            "planner": res.get("planner", "rules"),
            "progress": res.get("progress", 0) or progress_of(res.get("steps", [])),
            "verdict": res.get("verdict", ""),
            "question": res.get("question", ""),
            "note": res.get("note", ""),
            "steps": res.get("steps", []),
            "report": res.get("report", ""),
            "logs": res.get("log", []),
            "finished_at": res.get("finished_at") or now_iso(),
        })


def cancel(order_id: str) -> bool:
    """Пометить заказ отменённым.

    Процессы make при этом не прерываются: они запускаются в своих
    группах и не отменяются безопасно. Заказ просто перестаёт
    показываться как активный — результат, если он появится, не
    перезапишет отмену.
    """
    with _LOCK:
        o = ORDERS.get(order_id)
        if not o or o.get("status") in ("done", "failed", "partial"):
            return False
        o["status"] = "cancelled"
        o["finished_at"] = now_iso()
        return True


# ==========================================================================
#  Прерывание и возобновление
# ==========================================================================
def question_of(order_id: str) -> str:
    """Вопрос, на котором граф стоит, — для интерфейса."""
    o = get(order_id)
    return (o or {}).get("question", "")


def resume(order_id: str, answer: str, root: str = ".") -> dict:
    """Продолжить прерванный заказ после ответа оператора.

    Граф сохранил состояние в чекпоинте, поэтому работа продолжается
    с места остановки: уже собранные цели не пересобираются заново,
    а модели уходит ответ оператора как вводные.

    Если чекпоинта нет (мост перезапустился до того, как состояние
    записалось, или langgraph не установлен), заказ перезапускается
    целиком — об этом честно сообщаем в ответе.
    """
    with _LOCK:
        o = ORDERS.get(order_id)
    if o is None:
        return {"error": "unknown order", "id": order_id}

    try:
        res = engine.resume(order_id, answer, root=root)
    except Exception as exc:                                    # noqa: BLE001
        return {"error": f"возобновление не удалось: {exc}"}

    with _LOCK:
        o.update({
            "status": res.get("status", "done"),
            "intent": res.get("intent", o.get("intent", "")),
            "progress": res.get("progress", o.get("progress", 0)),
            "verdict": res.get("verdict", ""),
            "question": res.get("question", ""),
            "note": res.get("note", ""),
            "steps": res.get("steps", o.get("steps", [])),
            "report": res.get("report", ""),
            "logs": res.get("log", o.get("logs", [])),
            "finished_at": res.get("finished_at") or now_iso(),
        })
    return _order_view(o)


def waiting() -> list[dict]:
    """Заказы, на которых граф стоит и ждёт оператора."""
    with _LOCK:
        items = [o for o in ORDERS.values()
                 if o.get("verdict") == "needs_human" or
                 (o.get("question") and o.get("status") == "running")]
    return [{"id": o["id"], "text": o["text"], "question": o.get("question", ""),
             "done": [s["target"] for s in o.get("steps", [])
                      if s.get("status") == "done"],
             "failed": [s["target"] for s in o.get("steps", [])
                        if s.get("status") == "failed"]}
            for o in items]


# ==========================================================================
#  Поток событий
# ==========================================================================
#  Без потока интерфейс узнаёт о работе только по завершении заказа:
#  пользователь смотрит на пустой прогресс минуту, пока считает дальность.
#  Поток отдаёт те же события по мере их появления.
# ==========================================================================
def stream(order_id: str, poll: float = 0.4):
    """Отдаёт заказ по мере изменений — конечный генератор для SSE.

    События:
        ``state`` — весь заказ (первое и при существенных изменениях)
        ``log``   — новая строка журнала графа
        ``step``  — изменился статус шага
        ``done``  — заказ завершён, поток закрывается
    """
    import json
    last_log = 0
    last_steps: dict[str, str] = {}
    last_status = ""
    idle = 0

    while True:
        o = get(order_id)
        if o is None:
            yield ("done", {"error": "unknown order", "id": order_id})
            return

        status = o.get("status", "")
        if status != last_status:
            last_status = status
            idle = 0
            yield ("state", o)
        else:
            idle += poll

        for i, line in enumerate(o.get("logs") or []):
            if i >= last_log:
                yield ("log", {"text": line})
                last_log = i + 1

        steps = {s.get("id", str(i)): s.get("status", "")
                 for i, s in enumerate(o.get("steps", []))}
        if steps != last_steps:
            changed = [s for s in o.get("steps", [])
                       if last_steps.get(s.get("id", "")) != s.get("status")]
            last_steps = steps
            yield ("step", {"steps": changed, "progress": o.get("progress", 0)})

        if status in ("done", "failed", "partial", "cancelled"):
            yield ("done", o)
            return

        # заказ завис и ничего не меняется — отдаём последнее известное
        # и закрываем поток, чтобы вкладка не висела вечно
        if idle > 900:
            yield ("done", {**o, "note": "поток закрыт по таймауту ожидания"})
            return
        time.sleep(poll)


# ==========================================================================
#  Что вообще умеет движок — для интерфейса
# ==========================================================================
def capabilities(root: str = ".", profile: str = "") -> dict:
    import llm
    from . import planner

    proj = load_project(root, profile=profile)
    tops = [t for t in proj.buildable() if t.top_level][:10]
    return {
        "project": proj.as_dict(),
        "agents": engine.graph_shape(),
        "runners": runners_available(),
        "engine": "langgraph" if have_langgraph() else "plain",
        "verify": proj.verify,
        "suggestions": [t.name for t in tops],
        "llm": llm.status(),
    }


def plan_only(text: str, root: str = ".", profile: str = "") -> dict:
    """Спланировать без выполнения — для предпросмотра в интерфейсе."""
    from . import planner
    proj = load_project(root, profile=profile)
    r = planner.plan(text, proj)
    return {k: v for k, v in r.items()}