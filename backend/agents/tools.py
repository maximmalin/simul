"""Инструменты агента — то, что модель вызывает как настоящие tools.

Ключевой сдвиг по сравнению с «верни JSON и я вытащу регуляркой»:
модель получает **типизированные инструменты** и сама вызывает их,
а ответ приходит уже разобранным. Это не косметика:

* мусор в ответе (````json` fences, пояснения перед JSON) больше не ломает
  разбор — там нечего разбирать;
* провайдер проверяет аргументы по схеме и не даст выдумать несуществующую
  цель;
* один и тот же набор инструментов работает и с облаком, и с Ollama,
  потому что обе понимают поле `tools` в OpenAI-совместимом API.

Инструменты объявлены один раз здесь и используются и планировщиком,
и агентным циклом, и MCP-сервером — одна правда, три потребителя.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from .profile import collect
from .types import Project

#: максимум символов в аргументе — чтобы модель не утащила весь лог
MAX_ARG = 4000


# ==========================================================================
#  Схемы
# ==========================================================================
def _schema(props: dict[str, dict], required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required,
            "additionalProperties": False}


def tool_schemas(project: Project) -> list[dict]:
    """Объявления инструментов в формате OpenAI."""
    n = len(project.buildable())
    return [
        {
            "type": "function",
            "function": {
                "name": "list_targets",
                "description": (
                    f"Что можно собрать в проекте «{project.name}». "
                    f"Доступно целей: {n}. Вызывай это, если не знаешь "
                    "список целей или не уверен в названии."),
                "parameters": _schema({}, []),
            },
        },
        {
            "type": "function",
            "function": {
                "name": "describe_target",
                "description": "Описание цели и от чего она зависит.",
                "parameters": _schema(
                    {"name": {"type": "string",
                              "description": "Имя цели ровно как в list_targets"}},
                    ["name"]),
            },
        },
        {
            "type": "function",
            "function": {
                "name": "submit_plan",
                "description": (
                    "Зафиксировать план: какие цели выполнить. Выбирай "
                    "минимальный достаточный набор. Если выбрана цель "
                    "верхнего уровня, её зависимости соберутся сами — "
                    "перечислять их отдельно не нужно."),
                "parameters": _schema(
                    {"targets": {"type": "array",
                                 "items": {"type": "string"},
                                 "description": "Имена целей сборки"},
                     "intent": {"type": "string",
                                "description": "Короткий тег: что делает заказ"}},
                    ["targets"]),
            },
        },
        {
            "type": "function",
            "function": {
                "name": "run_target",
                "description": (
                    "Собрать одну цель. Возвращает код возврата и хвост "
                    "вывода. Долгая операция."),
                "parameters": _schema(
                    {"name": {"type": "string", "description": "Имя цели"}},
                    ["name"]),
            },
        },
        {
            "type": "function",
            "function": {
                "name": "read_artifacts",
                "description": (
                    "Прочитать результаты расчётов: числовые значения из "
                    "файлов сборки. Отсюда берутся числа для отчёта."),
                "parameters": _schema(
                    {"limit": {"type": "integer",
                               "description": "Сколько файлов прочитать",
                               "minimum": 1, "maximum": 20}},
                    []),
            },
        },
        {
            "type": "function",
            "function": {
                "name": "ask_human",
                "description": (
                    "Остановиться и спросить оператора. Используй, когда "
                    "дальше идти нельзя: нужен доступ, недостаточно данных "
                    "или решение инженера. Граф встанет и продолжится после "
                    "ответа."),
                "parameters": _schema(
                    {"question": {"type": "string",
                                  "description": "Вопрос оператору"}},
                    ["question"]),
            },
        },
    ]


# ==========================================================================
#  Реализации
# ==========================================================================
def make_bindings(project: Project, run_one: Callable[[str], dict],
                  on_plan: Callable[[list[str], str], None] | None = None,
                  ) -> dict[str, Callable[..., str]]:
    """Собрать функции инструментов, привязанные к проекту."""
    from .reporter import collect_facts

    def list_targets() -> str:
        rows = []
        for t in project.buildable():
            dep = f" (тянет: {', '.join(t.depends_on[:5])})" if t.depends_on else ""
            rows.append(f"- {t.name}: {(t.title or '—')}{dep}")
        head = f"Проект «{project.name}». Собираемые цели:\n"
        return head + "\n".join(rows[:120])

    def describe_target(name: str) -> str:
        t = project.target(name)
        if not t:
            avail = ", ".join(x.name for x in project.buildable()[:20])
            return (f"Цели «{name}» нет. Доступные: {avail}")
        try:
            from .runner import get as runner_get
            cmd = (runner_get(t.runner).command(Path(project.root), t)
                   if t.runner else runner_get(project.runner)
                   .command(Path(project.root), t))
        except Exception as exc:                                # noqa: BLE001
            cmd = f"(команду собрать не удалось: {exc})"
        return (f"Цель: {t.name}\nописание: {t.title or '—'}\n"
                f"команда: {cmd}\nзависит: {', '.join(t.depends_on) or '—'}\n"
                f"служебная: {'да' if t.service else 'нет'}")

    def submit_plan(targets: list[str], intent: str = "") -> str:
        by_name = project.by_name()
        good = [t for t in targets if t in by_name]
        bad = [t for t in targets if t not in by_name]
        if not good:
            avail = ", ".join(x.name for x in project.buildable()[:25])
            return (f"Ни одна цель не подошла. Несуществующие: {bad}. "
                    f"Доступные: {avail}")
        good = _minimise(good, project)
        if on_plan:
            on_plan(good, intent or "llm")
        out = f"План принят: {good}"
        if bad:
            out += f"\nОтброшены как несуществующие: {bad}"
        return out

    def run_target(name: str) -> str:
        if project.target(name) is None:
            return f"Цели «{name}» нет — собирать нечего"
        res = run_one(name)
        head = f"{name}: код {res.get('exit_code')} за {res.get('duration_s', 0)}s"
        tail = res.get("log") or []
        if res.get("status") != "done" and tail:
            return head + "\nпоследние строки:\n" + "\n".join(tail[-10:])
        return head

    def read_artifacts(limit: int = 6) -> str:
        facts, files = collect_facts(project)
        if not files:
            return "Артефактов нет — сборка ещё не выполнялась"
        return f"Файлов с результатами: {len(files)}\n\n{facts[:MAX_ARG]}"

    def ask_human(question: str) -> str:
        # фактический останов делает узел, читающий этот маркер;
        # здесь только фиксируем намерение
        return f"__ASK_HUMAN__{question}"

    return {
        "list_targets": list_targets,
        "describe_target": describe_target,
        "submit_plan": submit_plan,
        "run_target": run_target,
        "read_artifacts": read_artifacts,
        "ask_human": ask_human,
    }


def _minimise(targets: list[str], project: Project) -> list[str]:
    """Убрать цели, покрытые выбранной верхнеуровневой.

    Считается транзитивно — см. объяснение в planner._covered_by.
    """
    from .planner import _covered_by
    covered = _covered_by(targets, project)
    return [t for t in targets if t not in covered]


# ==========================================================================
#  Вызов
# ==========================================================================
def invoke(bindings: dict[str, Callable[..., str]], name: str,
           args: dict[str, Any]) -> str:
    """Вызвать инструмент по имени. Ошибки не поднимаем — отдаём текстом."""
    fn = bindings.get(name)
    if fn is None:
        return (f"Инструмента «{name}» нет. Доступные: "
                f"{', '.join(sorted(bindings))}")
    try:
        out = fn(**args) if args else fn()
    except TypeError as exc:
        return f"Неверные аргументы для «{name}»: {exc}"
    except Exception as exc:                                    # noqa: BLE001
        return f"Инструмент «{name}» упал: {exc}"
    return str(out)[:MAX_ARG]


ASK_MARKER = "__ASK_HUMAN__"


def is_ask_human(text: str) -> str | None:
    """Вопрос оператору, если инструмент его задал."""
    if text.startswith(ASK_MARKER):
        return text[len(ASK_MARKER):].strip()
    return None