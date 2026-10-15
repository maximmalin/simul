"""Генерация промптов агентов из описания проекта.

Зачем
----
Раньше промпты лежали в `AGENT_PROMPTS.md` и были написаны под одно
изделие: абсолютные пути, «169 тестов» (их давно 253), водопад,
link budget, YOLO. Перенести проект — значит переписывать все промпты
вручную, и через месяц они снова разойдутся с реальностью.

Здесь промпты **порождаются** из описания проекта:

* одна цель верхнего уровня — один агент сборки;
* один артефакт — один агент проверки;
* один тест-цель — агент верификации.

Поэтому добавление цели в `agents.project.yaml` сразу даёт рабочего
агента, а удаление — убирает. Ничего не расходится само по себе.

Что остаётся за пределами генерации
------------------------------------
То, что машина не может вывести: метрики проекта, приоритеты, особые
правила. Они живут в секции `agents:` описания и подмешиваются сюда.

Формат промпта совпадает с прежним — `ID | Название | Цель | Файлы |
Команда`, — чтобы существующие потребители продолжили работать.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .runner import get as runner_get
from .types import Artifact, Project, Target

#: разрядность идентификаторов агентов
PREFIX = {
    "build": "MK",       # сборка
    "verify": "TST",     # проверки
    "artifact": "ART",   # артефакты
    "plan": "PLN",       # планирование
    "release": "REL",    # релиз
}

#: слова в названии цели, по которым видно её роль
ROLE_HINTS = {
    "build": ("all", "build", "dist", "assemble", "сборк"),
    "verify": ("test", "check", "verify", "lint", "тест", "проверк"),
    "docs": ("docs", "doc", "tz", "readme", "документ"),
    "release": ("release", "publish", "tag", "релиз", "publish"),
}


@dataclass
class AgentPrompt:
    id: str
    name: str
    goal: str
    files: list[str] = field(default_factory=list)
    command: str = ""
    role: str = "agent.build"
    kind: str = "build"

    def as_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name, "goal": self.goal,
            "files": self.files, "command": self.command,
            "role": self.role, "kind": self.kind,
        }


def _role_of(target: Target) -> str:
    name = target.name.lower()
    for role, hints in ROLE_HINTS.items():
        if any(h in name for h in hints):
            return role
    return "build"


def _files_for(target: Target, proj: Project) -> list[str]:
    """Какие файлы агент должен знать.

    Ничего не выдумывается: названия целей уже есть в описании проекта,
    а артефакты перечислены там же.
    """
    files: list[str] = []
    root_name = Path(proj.root).name
    for f in ("Makefile", "package.json", "justfile", "agents.project.yaml"):
        if (Path(proj.root) / f).is_file():
            files.append(f)
    for dep in target.depends_on:
        if dep in proj.by_name():
            files.append(dep)
    if not files:
        files.append(root_name)
    return files[:6]


def _command_for(target: Target, proj: Project) -> str:
    """Команда запуска цели — собирается тем же раннером, что и движок."""
    try:
        if target.runner:
            return runner_get(target.runner).command(Path(proj.root), target)
        return runner_get(proj.runner).command(Path(proj.root), target)
    except Exception:                                            # noqa: BLE001
        return f"<раннер {proj.runner} не смог собрать команду>"


# ==========================================================================
#  Генерация
# ==========================================================================
def _slug(name: str) -> str:
    """Имя цели -> часть идентификатора.

    Идентификатор строится из имени цели, а не из счётчика: нумерация
    счётчиком сдвигалась бы при любой перестановке целей, и ссылки на
    агента в отчётах и переписке становились бы неверными.
    """
    out = re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-").upper()
    return out[:24] or "TASK"


def _agent_id(kind: str, name: str) -> str:
    return f"AGENT-{PREFIX.get(kind, 'GEN')}-{_slug(name)}"


def generate(proj: Project) -> list[AgentPrompt]:
    """Полный набор агентов для проекта."""
    extra = _project_agents(proj)
    prompts: list[AgentPrompt] = []

    # 1. сборка: по одной на верхнеуровневую цель
    for t in proj.buildable():
        if not t.top_level:
            continue
        role = _role_of(t)
        prompts.append(AgentPrompt(
            id=_agent_id("build", t.name),
            name=f"Собрать: {t.title or t.name}",
            goal=_build_goal(t, proj),
            files=_files_for(t, proj),
            command=_command_for(t, proj),
            role=proj.agent_owner.get(t.name, f"agent.{role}"),
            kind="build",
        ))

    # 2. проверка: отдельная цель, если она есть
    for t in proj.buildable():
        if _role_of(t) != "verify":
            continue
        prompts.append(AgentPrompt(
            id=_agent_id("verify", t.name),
            name=f"Проверить: {t.title or t.name}",
            goal=("Убедиться, что проверки проходят без ошибок. "
                  "Падение — сюрприз, а не ожидание: разобрать причину "
                  "до того, как отчитаться."),
            files=_files_for(t, proj),
            command=_command_for(t, proj),
            role="agent.verify",
            kind="verify",
        ))

    # 3. артефакты: по одному агенту на каждое правило описания
    for art in proj.artifacts:
        hits = _matching(proj, art)
        if not hits:
            continue
        prompts.append(AgentPrompt(
            id=_agent_id("artifact", art.title),
            name=f"Проверить результат: {art.title}",
            goal=(f"Убедиться, что {art.title} получен и содержит "
                  f"числовые значения. Файлы: {', '.join(hits[:4])}."),
            files=hits[:6],
            command=_inspect_command(proj, art),
            role="agent.artifact",
            kind="artifact",
        ))

    # 4. то, что описал сам проект и машина вывести не может
    for spec in extra:
        prompts.append(AgentPrompt(
            id=spec.get("id") or _agent_id(spec.get("kind", "build"),
                                            spec.get("name", "задача")),
            name=spec.get("name", "задача проекта"),
            goal=spec.get("goal", ""),
            files=list(spec.get("files", [])),
            command=spec.get("command", ""),
            role=spec.get("role", "agent.build"),
            kind=spec.get("kind", "build"),
        ))

    return prompts


def _build_goal(t: Target, proj: Project) -> str:
    """Что значит собрать эту цель — без выдумывания требований."""
    bits = []
    if t.title:
        bits.append(t.title.rstrip(".") + ".")
    if t.depends_on:
        bits.append("Система сборки сама подтянет: "
                    + ", ".join(t.depends_on[:5]) + ".")
    if not bits:
        bits.append(f"Выполнить цель «{t.name}» и убедиться в коде 0.")
    return " ".join(bits)


def _matching(proj: Project, art: Artifact) -> list[str]:
    """Файлы под правило артефакта — относительными путями."""
    from .profile import collect
    root = Path(proj.root)
    out: list[str] = []
    for f in collect(proj):
        try:
            rel = f.relative_to(root).as_posix()
        except ValueError:
            continue
        if _fnmatch(rel, art.pattern) or _fnmatch(f.name, art.pattern):
            out.append(rel)
    return out


def _fnmatch(name: str, pattern: str) -> bool:
    from fnmatch import fnmatch
    return fnmatch(name, pattern) or fnmatch(name, f"*/{pattern}")


def _inspect_command(proj: Project, art: Artifact) -> str:
    """Команда показать содержимое артефакта.

    JSON разбирается, остальное печатается: агент должен увидеть числа,
    а не молчаливый «файл существует».
    """
    pattern = art.pattern
    if pattern.endswith(".json"):
        return (f"python3 -c \"import json,glob,sys;"
                f"[print(p, json.dumps(json.load(open(p)), ensure_ascii=False)"
                f"[:800]) for p in sorted(glob.glob('{pattern}'))]\"")
    return f"ls -la {pattern} 2>/dev/null | head"


def _project_agents(proj: Project) -> list[dict]:
    """Агенты, описанные самим проектом.

    Берутся из секции `agents:` описания. Формат — тот же, что у
    `vocabulary` и `artifacts`: проект говорит на своём языке, движок
    не знает ни про что конкретное.
    """
    from .profile import _read_profile
    data = _read_profile(Path(proj.root))
    agents = data.get("agents", [])
    if isinstance(agents, dict):
        agents = [{"name": k, **v} for k, v in agents.items()]
    return [a for a in agents if isinstance(a, dict)]


# ==========================================================================
#  Вывод
# ==========================================================================
def to_markdown(proj: Project, prompts: list[AgentPrompt]) -> str:
    """Файл промптов в прежнем формате.

    Заголовок не прибит к изделию: он берётся из описания проекта.
    Прежний был «— Krystal Radio», и это была первая строчка, которая
    ломалась при переносе.
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        f"# Агенты (per-task prompts) — {proj.name}",
        "",
        "Файл **порождён** из описания проекта: `backend/agents/prompts.py`.",
        "Правьте `agents.project.yaml`, а не этот файл — иначе правки",
        "пропадут при следующей генерации.",
        "",
        f"Проект: {proj.name} · раннер: `{proj.runner}` · "
        f"целей: {len(proj.targets)} · сгенерировано: {now}",
        "",
        "Каждый агент выполняет свою задачу с минимальным контекстом:",
        "открыть репозиторий, прочитать свой блок, выполнить команду.",
        "Структура блока: `ID | Название | Цель | Ключевые файлы | Команда`.",
        "",
        f"Проверить, что промпты соответствуют проекту: "
        f"`python3 -m agents prompts --check`",
        "",
    ]

    by_kind: dict[str, list[AgentPrompt]] = {}
    for p in prompts:
        by_kind.setdefault(p.kind, []).append(p)

    titles = {
        "build": "Сборка",
        "verify": "Проверки",
        "artifact": "Результаты",
    }
    n = 0
    for kind, items in by_kind.items():
        n += 1
        lines.append(f"## {n}. {titles.get(kind, kind.title())}")
        lines.append("")
        for p in items:
            lines.append(f"- **ID**: `{p.id}`")
            lines.append(f"- **Название**: {p.name}")
            lines.append(f"- **Цель**: {p.goal}")
            lines.append(f"- **Роль**: `{p.role}`")
            lines.append(f"- **Ключевые файлы**: "
                         + ", ".join(f"`{f}`" for f in p.files))
            lines.append(f"- **Команда**: `{p.command}`")
            lines.append("")

    return "\n".join(lines)


def to_json(proj: Project, prompts: list[AgentPrompt]) -> str:
    return json.dumps(
        {"project": proj.name, "runner": proj.runner,
         "count": len(prompts),
         "agents": [p.as_dict() for p in prompts]},
        indent=2, ensure_ascii=False)


def verify(proj: Project, prompts: list[AgentPrompt]) -> list[str]:
    """Что в промптах разошлось с реальностью.

    Проверяется не «правильность текста», а конкретные вещи, которые
    расходятся чаще всего: команда не работает, файла нет, цель удалена.
    """
    problems: list[str] = []
    root = Path(proj.root)

    for p in prompts:
        if not p.command:
            problems.append(f"{p.id}: пустая команда")
            continue
        if not re.search(r"[A-Za-zА-Яа-я0-9_./-]", p.command):
            problems.append(f"{p.id}: команда не похожа на команду")
        for f in p.files:
            if f in ("Makefile", "package.json", "justfile",
                     "agents.project.yaml", root.name):
                continue
            if not (root / f).exists():
                problems.append(f"{p.id}: файла нет — {f}")

    known = {t.name for t in proj.targets}
    for p in prompts:
        for token in re.findall(r"\b(?:make|npm run|just)\s+([\w.-]+)", p.command):
            if token not in known and not token.startswith("-"):
                problems.append(f"{p.id}: команда ссылается на цель "
                                f"«{token}», которой нет в проекте")
    return problems