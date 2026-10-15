"""Типы агентно-функционального графа.

Здесь нет ничего конкретного: ни make, ни Krystal Radio, ни русских
регулярок. Только структура данных, общая для любого проекта —
движок одинаково работает с SDR, с прошивкой, с фронтендом и с CAD.

Ключевая мысль: **проект — это данные**. Описание проекта (`Project`)
загружается из репозитория, а код движка о проекте ничего не знает.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, TypedDict


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex[:12]}"


# ==========================================================================
#  Служебные цели
# ==========================================================================
#: Имена, за которыми почти во всех системах сборки прячутся не сборки,
#: а серверы, watch-режимы и справка. Такие цели нельзя предлагать
#: в плане «собери всё»: сервер не завершится, а `help` всегда успешен
#: и ничего не проверяет.
_SERVICE_NAMES = frozenset({
    "serve", "server", "start", "dev", "watch", "clean", "help", "usage",
    "version", "lint-fix", "fmt", "format", "docs-serve", "preview",
})

#: Цели, которые удаляют данные. Их нельзя запускать **никогда** — ни
#: из плана, ни прямым вызовом инструмента.
#:
#: Проверено на себе: `distclean`, вызванный из песочницы, снёс `out/`,
#: `.venv` и `node_modules` основного проекта. Симлинки тут ни при чём —
#: при неудачном создании песочницы код возвращался к корню проекта, а
#: разрушающая цель к этому не была готова. Поэтому запрет двойной:
#: такая цель не запускается вовсе, и если песочница не создалась,
#: заказ останавливается, а не идёт в проект.
_DESTRUCTIVE_NAMES = frozenset({
    "clean", "distclean", "purge", "reset", "wipe", "drop-all", "remove-all",
    "nuke", "destroy", "clobber", "rm-all",
})

#: подстроки, по которым цель считается разрушающей
_DESTRUCTIVE_HINTS = (
    "clean", "purge", "wipe", "reset-all", "drop-all", "remove-all",
    "destroy", "nuke", "clobber", "rm-all", "uninstall-all",
)


def is_destructive(name: str) -> bool:
    """Удаляет ли цель данные.

    Имя — основной признак. Дополнительно смотрится тело цели:
    команда с `rm -rf` внутри повод отказать, даже если называется
    безобидно.
    """
    base = name.lower()
    if base in _DESTRUCTIVE_NAMES:
        return True
    return any(h in base for h in _DESTRUCTIVE_HINTS)


def is_service(name: str) -> bool:
    """Похоже ли имя цели на служебную команду.

    Эвристика по имени: узнаётся очевидное (`serve`, `dev`, `watch`,
    `help`). Всё остальное — дело проекта: он объявляет такие цели
    списком `service:` в своём описании, потому что по имени
    отличить `run` (сервер проекта) от `run` (шаг сборки) нельзя.
    """
    base = name.lower()
    return base in _SERVICE_NAMES or base.startswith(
        ("serve", "dev-", "watch-", "start-", "run-server"))


# ==========================================================================
#  Описание проекта
# ==========================================================================
@dataclass
class Target:
    """Одна цель сборки — то, что движок умеет выполнить."""

    name: str
    title: str = ""                     # человекочитаемое описание
    command: str = ""                   # готовая команда; пусто — собирается раннером
    top_level: bool = False             # верхнеуровневая: покрывает зависимые
    depends_on: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    runner: str = ""                    # переопределение раннера для этой цели
    #: долгоживущая команда: сервер, watch, интерактив. Такие цели
    #: нельзя ставить в план — они не завершатся и «упадут» по таймауту
    service: bool = False

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class Artifact:
    """Артефакт, по которому собираются факты для отчёта."""

    pattern: str                        # glob, напр. "build/report.*"
    title: str = "Артефакты"
    limit: int = 40

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class Project:
    """Всё, что движок знает о проекте."""

    name: str = "project"
    root: str = "."                     # корень относительно cwd
    description: str = ""
    runner: str = "shell"               # make | npm | just | shell | ...
    build_file: str = ""                # на чём основании определён раннер
    runner_opts: dict[str, Any] = field(default_factory=dict)

    targets: list[Target] = field(default_factory=list)
    artifacts: list[Artifact] = field(default_factory=list)

    #: необязательные подсказки проекта: синонимы -> цель
    #: заполняется из описания, не из кода движка
    vocabulary: dict[str, list[str]] = field(default_factory=dict)

    #: системный промпт проекта — что движок вообще делает
    system: str = ""

    #: команда проверки «собралось ли всё» (обычно тесты)
    verify: str = ""

    agent_owner: dict[str, str] = field(default_factory=dict)

    def target(self, name: str) -> Target | None:
        return next((t for t in self.targets if t.name == name), None)

    def by_name(self) -> dict[str, Target]:
        return {t.name: t for t in self.targets}

    def top_level(self) -> list[Target]:
        """Цели, которые не являются зависимостью другой цели.

        Именно их планировщик предлагает: если выбрана такая цель,
        система сборки сама подтянет всё, от чего она зависит.
        """
        marked = [t for t in self.targets if getattr(t, "top_level", False)]
        if marked:
            return marked
        dep: set[str] = set()
        for t in self.targets:
            dep.update(t.depends_on)
        return [t for t in self.targets if t.name not in dep]

    def buildable(self) -> list[Target]:
        """Цели, которые имеет смысл предлагать в плане.

        Служебные (серверы, watch-режимы, справка, чистка) исключаются:
        заказ «собери всё» не должен превращаться в запуск висящего
        сервера, а падение `help` — это не ошибка сборки.
        """
        return [t for t in self.targets
                if not t.service and not is_service(t.name)]

    def described(self) -> list[Target]:
        """Цели с описанием — их и показываем модели."""
        return [t for t in self.targets if t.title]

    def as_dict(self) -> dict:
        d = asdict(self)
        d["target_count"] = len(self.targets)
        d["top_level"] = [t.name for t in self.top_level()]
        return d


# ==========================================================================
#  Состояние заказа
# ==========================================================================
@dataclass
class Step:
    """Один запуск цели."""

    id: str
    target: str
    title: str = ""
    agent: str = "agent.build"           # какая агентная функция выполнила
    status: str = "pending"              # pending|running|done|failed|skipped|blocked
    command: str = ""
    attempt: int = 0
    exit_code: int | None = None
    duration_s: float = 0.0
    error: str | None = None
    log: list[str] = field(default_factory=list)
    started_at: str | None = None
    finished_at: str | None = None

    def as_dict(self) -> dict:
        d = asdict(self)
        d["log_tail"] = self.log[-25:]
        d.pop("log", None)
        return d


class GraphState(TypedDict, total=False):
    """Состояние, ходящее по графу и попадающее в чекпоинт."""

    # заказ
    order_id: str
    text: str
    project: str

    # планирование
    planner: str                          # rules | llm | manual
    intent: str
    steps: list[dict]

    # исполнение
    cursor: int
    progress: int
    mode: str                          # llm | rules | plain
    model: str
    turns: int
    history: list[dict]
    pending_tool: list[str]
    pending_user: str
    tool_calls: list[dict]
    last_turn: str
    answer: str
    durable: bool

    # итог
    facts: str
    artifacts: list[str]
    report: str

    # ветвление
    verdict: str                         # continue | done | needs_human
    question: str
    note: str

    status: str                          # queued|running|done|partial|failed
    started_at: str
    finished_at: str
    log: list[str]


def new_state(text: str, order_id: str | None = None,
              project: str = "") -> GraphState:
    return {
        "order_id": order_id or new_id(),
        "text": text,
        "project": project,
        "planner": "rules",
        "intent": "generic",
        "steps": [],
        "cursor": 0,
        "progress": 0,
        "mode": "rules",
        "model": "",
        "turns": 0,
        "history": [],
        "pending_tool": [],
        "pending_user": "",
        "tool_calls": [],
        "last_turn": "",
        "answer": "",
        "durable": False,
        "facts": "",
        "artifacts": [],
        "report": "",
        "verdict": "",
        "question": "",
        "note": "",
        "status": "queued",
        "started_at": now_iso(),
        "finished_at": "",
        "log": [],
    }


def progress_of(steps: list[dict]) -> int:
    if not steps:
        return 0
    settled = sum(1 for s in steps
                  if s.get("status") in ("done", "failed", "skipped", "blocked"))
    return int(round(100 * settled / len(steps)))


def final_status(steps: list[dict], error: str = "") -> str:
    if error:
        return "failed"
    if not steps:
        return "failed"
    done = sum(1 for s in steps if s.get("status") == "done")
    bad = sum(1 for s in steps if s.get("status") in ("failed", "blocked"))
    if bad == 0 and done == len(steps):
        return "done"
    return "partial" if done else "failed"