"""Планировщик: заказ → список целей сборки.

Два режима, оба работают с любым проектом:

**LLM** — модели показывается список целей с описаниями, она отвечает
JSON'ом. Понимает неточные формулировки и несколько намерений сразу.

**Правила** — когда модели нет или она ответила ерундой. Никаких
регулярок под конкретные слова проекта: используются только
собственные имена целей, словарь синонимов из описания проекта и
пересечение слов. Поэтому перенос на другой проект не требует правок —
новые цели подхватываются сами.

Порядок работы правил, от сильного к слабому:

1. **Прямое упоминание** — в тексте заказа есть имя цели («make page»,
   «запусти test»). Берём самые длинные совпадения, чтобы `board-svg`
   не превратился в `board`.
2. **Словарь проекта** — описание проекта объявило синонимы
   (`waterfall: [водопад, спектр]`), они сопоставляются с заказом.
3. **Пересечение слов** — цель подбирается по общим словам с описанием.
4. **Верхние цели** — ничего не разобралось, предлагаем то, что обычно
   запускают целиком.

Синонимы живут в `vocabulary` описания проекта, а не в коде здесь: это
позволяет объяснять проекту его собственные слова без правки движка.
"""

from __future__ import annotations

import json
import re
from typing import Iterable

import llm

from .types import Project, Step

#: слова, которые ничего не различают между целями
STOP = {
    "make", "run", "запуск", "запусти", "запустить", "собери", "собрать",
    "сборка", "собери", "построй", "построить", "build", "target", "цель",
    "цели", "goal", "the", "and", "или", "плюс", "а", "и", "в", "к", "по",
    "test", "tests", "проверка", "проверь", "всё", "все", "all", "please",
    "пожалуйста", "нужно", "надо", "мне", "для", "the", "a", "an", "to",
}

_WORD = re.compile(r"[\w][\w.-]*", re.UNICODE)


def _words(text: str) -> list[str]:
    return [w.lower() for w in _WORD.findall(text)]


def _content_words(text: str) -> set[str]:
    return {w for w in _words(text) if len(w) > 2 and w not in STOP}


# ==========================================================================
#  Системные промпты
# ==========================================================================
def _system(proj: Project) -> str:
    """Промпт собирается из описания проекта — значит объясняет
    конкретику этого проекта, а не абстрактного «собери всё»."""
    return (
        f"Ты — планировщик сборки проекта «{proj.name}».\n"
        + (f"Проект: {proj.description}\n" if proj.description else "")
        + (f"Задача проекта:\n{proj.system}\n" if proj.system else "")
        + "\nПолучаешь заказ оператора и список ДОСТУПНЫХ целей сборки.\n"
        "Ответь ТОЛЬКО JSON, без пояснений и без markdown:\n\n"
        '{"intent": "<короткий тег>", "targets": ["<цель>", ...]}\n\n'
        "Правила:\n"
        "- каждая цель — ровно из списка доступных, иначе будет отвергнута;\n"
        "- выбирай минимальный достаточный набор целей;\n"
        f"- помеченные «{_top_marker(proj)}» цели верхнего уровня: они сами "
        "подтягивают всё, от чего зависят. Не добавляй их зависимости "
        "отдельно — получится гонка за общие файлы;\n"
        "- если заказ требует того, чего нет в списке, верни пустой targets "
        "и опиши проблему в intent."
    )


def _top_marker(proj: Project) -> str:
    tops = [t.name for t in proj.top_level()][:4]
    return ", ".join(tops) if tops else "(нет)"


def _catalogue(proj: Project, only_top: bool = False) -> str:
    """Список целей для модели: имя + описание + пометка верхнего уровня."""
    items = proj.buildable() if only_top else proj.described()
    if not items:
        items = proj.buildable() or proj.targets
    lines = []
    for t in items:
        mark = "*" if t.top_level else " "
        dep = f" ← {', '.join(t.depends_on[:4])}" if t.depends_on else ""
        lines.append(f"  {mark} {t.name}: {t.title or '—'}{dep}")
    return "\n".join(lines) or "(целей нет)"


# ==========================================================================
#  Планирование правилами
# ==========================================================================
def plan_by_rules(text: str, proj: Project) -> tuple[str, list[str]]:
    """Детерминированный план. Всегда доступен."""
    picked: list[str] = []
    tags: list[str] = []
    low = text.lower()
    by_name = proj.by_name()

    def add(name: str, why: str) -> None:
        if name in by_name and name not in picked:
            picked.append(name)
            tags.append(f"{name}:{why}")

    # 1. прямое упоминание имени цели — от длинных к коротким,
    #    чтобы «board-svg» не распалось на «board»
    names = sorted(by_name, key=len, reverse=True)
    for n in names:
        if len(n) < 3:
            continue
        if re.search(rf"(?<![\w-]){re.escape(n)}(?![\w-])", low):
            add(n, "упомянута")

    # 2. словарь проекта: синонимы, объявленные описанием
    for synonym, tgts in (proj.vocabulary or {}).items():
        if synonym.lower() in low:
            for t in tgts:
                add(t, f"синоним «{synonym}»")

    # 3. пересечение слов с описанием цели
    if not picked:
        want = _content_words(text)
        scored: list[tuple[float, str]] = []
        for t in (t for t in proj.buildable() if t.top_level):
            if not t.title:
                continue
            tw = _content_words(f"{t.name} {t.title}")
            if not tw:
                continue
            score = len(want & tw)
            if t.name.lower() in want:
                score += 3
            if score:
                scored.append((score, t.name))
        scored.sort(reverse=True)
        for score, n in scored[:2]:          # максимум две — больше уже не заказ
            add(n, f"совпадение слов ({score})")

    # 4. Ничего не разобралось — НЕ угадываем.
    #    Раньше здесь выбиралась первая попавшаяся верхняя цель, и заказ
    #    «непонятно» молча запускал `make all`. Тихая сборка всего по
    #    невнятному запросу хуже, чем честный отказ: пустой план
    #    превращается в сообщение со списком целей и подсказками,
    #    и решение остаётся за оператором.

    intent = "+".join(tags[:3]) if tags else "generic"
    return intent, picked


# ==========================================================================
#  Планирование моделью
# ==========================================================================
def plan_by_llm(text: str, proj: Project) -> tuple[str, list[str]] | None:
    """None — если модель недоступна или ответила чем-то непригодным."""
    prompt = (
        f"Заказ оператора:\n{text}\n\n"
        f"Цели сборки:\n{_catalogue(proj)}\n\n"
        "Верни JSON с intent и targets."
    )
    raw = llm.complete(prompt, system=_system(proj), fast=False,
                       max_tokens=500, temperature=0.0)
    if not raw:
        return None

    data = _extract_json(raw)
    if not data:
        return None

    picked = [str(t) for t in data.get("targets", []) if str(t) in proj.by_name()]
    if not picked:
        return None

    picked = _minimise(picked, proj)
    return str(data.get("intent") or "llm"), picked


def _covered_by(targets: list[str], proj) -> set[str]:
    """Цели, которые уже тянутся выбранными.

    Считается по **транзитивным** зависимостям, а не по прямым.
    Иначе `all` (который тянет `app`, а тот — `build`) не считался бы
    покрывающим `build`, и модель могла бы получить план «all + build» —
    тогда `build` собирался бы дважды, параллельно с `all`.
    """
    by_name = proj.by_name()

    def walk(name: str, out: set[str]) -> set[str]:
        if name in out:
            return out
        out.add(name)
        tgt = by_name.get(name)
        for d in (tgt.depends_on if tgt else []):
            walk(d, out)
        return out

    covered: set[str] = set()
    for t in targets:
        tgt = by_name.get(t)
        if not (tgt and tgt.top_level):
            continue
        before = len(covered)
        walk(t, covered)
        # сама верхнеуровневая цель в «покрытые» не входит: её-то и
        # собираем. В покрытые попадает только то, что она тянет.
        covered -= {t}
        if len(covered) == before and t in covered:
            continue
    return covered


def _minimise(targets: list[str], proj: Project) -> list[str]:
    """Убрать цели, уже покрытые выбранной верхнеуровневой.

    «всё и тесты» не должны превращаться в 19 параллельных make:
    `all` и так вызывает `test`. Дубли убираем — иначе две копии
    одного процесса будут писать в один и тот же файл.
    """
    covered = _covered_by(targets, proj)
    by_name = proj.by_name()
    return [t for t in targets
            if t not in covered or t not in by_name]


def _extract_json(raw: str) -> dict | None:
    """Достать JSON: сначала целиком, потом первый {...} в тексте."""
    for cand in (raw.strip(), _braces(raw)):
        if not cand:
            continue
        try:
            data = json.loads(cand)
            if isinstance(data, dict):
                return data
        except ValueError:
            continue
    return None


def _braces(raw: str) -> str:
    a, b = raw.find("{"), raw.rfind("}")
    return raw[a:b + 1] if a >= 0 and b > a else ""


# ==========================================================================
#  Точка входа
# ==========================================================================
def plan(text: str, proj: Project) -> dict:
    """Спланировать заказ. Возвращает {intent, targets, planner, note}."""
    intent, picked = plan_by_rules(text, proj)
    note = "правила"

    if llm.enabled():
        llm_plan = plan_by_llm(text, proj)
        if llm_plan:
            intent, picked = llm_plan
            note = "llm"
        else:
            note = "правила (llm не ответил)"

    if not picked:
        return {"intent": intent, "targets": [], "planner": note,
                "note": "заказ не сопоставлен ни с одной целью",
                # steps обязателен даже когда пуст: движок читает его
                # без проверки, и отсутствие ключа роняло запуск
                "steps": [],
                "available": [t.name for t in proj.targets[:60]]}

    by_name = proj.by_name()
    steps = [
        Step(id=f"s{i}", target=n, title=by_name[n].title or n,
             agent=proj.agent_owner.get(n, "agent.build")).as_dict()
        for i, n in enumerate(picked)
    ]
    return {"intent": intent, "targets": picked, "planner": note,
            "note": note, "steps": steps}


def suggest(proj: Project, limit: int = 8) -> list[str]:
    """Примеры заказов для интерфейса — строятся из целей проекта."""
    tops = [t.name for t in proj.top_level()][:limit]
    return tops