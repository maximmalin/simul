"""Факты из артефактов и итоговый отчёт.

Главное правило: **числа в отчёте берутся из файлов, а не выдумываются
моделью**. Модели передаётся сводка фактов, и она обязана опираться
только на неё. Для инженерного проекта это не вежливость, а
требование: в требованиях этого проекта пришлось исправлять
физические ошибки, потому что значения были написаны вместо того,
чтобы быть посчитанными.

Факты собираются по glob-паттернам из описания проекта, поэтому
движок одинаково работает с JSON, XML, логами, PNG и STEP.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import llm

from . import profile as P
from .types import GraphState, Project

#: файлы, из которых разумно вытаскивать числа
_TEXTY = {".json", ".txt", ".log", ".csv", ".xml", ".yaml", ".yml",
          ".ini", ".cfg", ".rst", ".md", ".rpt", ".dat", ".ndjson"}

#: мусорные ключи — служебное, не физика
_JUNK_KEY = re.compile(
    r"(path|file|dir|folder|timestamp|_ts$|_at$|date|generated|"
    r"command|cmd$|^cmd|argv|version|hash|sha|uuid|host|user|pid)",
    re.I)

_NUM = re.compile(r"^[+-]?\d+(?:[.,]\d+)?(?:[eE][+-]?\d+)?$")


# ==========================================================================
#  Факты
# ==========================================================================
def collect_facts(proj: Project, *, limit_per: int = 30) -> tuple[str, list[str]]:
    """Сводка по находкам. Возвращает (текст, список файлов)."""
    files = P.collect(proj)
    root = Path(proj.root)
    blocks: list[str] = []
    used: list[str] = []
    per_artifact = max(1, limit_per)

    by_pattern: list[tuple[Artifact_group, list[Path]]] = []
    for art in proj.artifacts:
        hits = [f for f in files if _match(root, f, art.pattern)]
        if hits:
            by_pattern.append((art, hits))

    for art, hits in by_pattern:
        # маленькие файлы приоритетнее: в них обычно сами метрики
        hits.sort(key=lambda p: (_size(p), p.name))
        shown = 0
        for f in hits:
            if shown >= per_artifact:
                break
            text = _read_for_facts(f)
            if not text:
                continue
            used.append(str(f.relative_to(root)))
            blocks.append(f"[{art.title}] {f.relative_to(root)}")
            blocks.append(text)
            shown += 1

    return ("\n\n".join(blocks) if blocks
            else "Артефакты не найдены — числовых данных для сводки нет."), used


#: алиас, чтобы не импортировать Artifact в аннотациях
Artifact_group = Any


def _match(root: Path, f: Path, pattern: str) -> bool:
    try:
        rel = f.relative_to(root).as_posix()
    except ValueError:
        rel = f.name
    from fnmatch import fnmatch
    return fnmatch(rel, pattern) or fnmatch(f.name, pattern)


def _size(p: Path) -> int:
    try:
        return p.stat().st_size
    except OSError:
        return 10 ** 9


def _read_for_facts(f: Path) -> str:
    """Достать из файла то, что имеет смысл показать модели."""
    if f.stat().st_size > 4 * 1024 * 1024:
        return ""
    suffix = f.suffix.lower()
    if suffix == ".json":
        try:
            return _fmt_json(json.loads(f.read_text(encoding="utf-8")))
        except (ValueError, OSError):
            return ""
    if suffix in _TEXTY:
        try:
            raw = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
        return _fmt_text(raw)
    # картинки, STEP, STL и прочее — модель не читает, но полезно
    # упомянуть, что файл есть и какого он размера
    if suffix:
        return f"  (файл есть, {f.stat().st_size} байт)"
    return ""


def _fmt_json(obj: Any, prefix: str = "", depth: int = 0) -> str:
    """JSON -> плоские строки «путь = значение» в глубину до 4 уровней."""
    if depth > 4:
        return ""
    out: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if _JUNK_KEY.search(str(k)):
                continue
            path = f"{prefix}{k}"
            if isinstance(v, (dict, list)):
                sub = _fmt_json(v, f"{path}.", depth + 1)
                if sub:
                    out.append(sub)
            else:
                out.append(f"  {path} = {_scalar(v)}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:14]):
            sub = _fmt_json(v, f"{prefix}[{i}].", depth + 1)
            if sub:
                out.append(sub)
        if len(obj) > 14:
            out.append(f"  {prefix}… всего элементов: {len(obj)}")
    else:
        out.append(f"  {prefix.rstrip('.')} = {_scalar(obj)}")
    return "\n".join(out)


def _scalar(v: Any) -> str:
    s = str(v)
    return s if len(s) <= 100 else s[:97] + "…"


def _fmt_text(raw: str, limit: int = 40) -> str:
    """Из обычного текста — только строки с числами и заголовки.

    Отчёт должен опираться на измерения, поэтому строки без чисел
    (кроме заголовков и вердиктов) модели не показываются.
    """
    keep: list[str] = []
    for line in raw.splitlines():
        s = line.strip()
        if not s:
            continue
        has_num = any(_NUM.match(tok.strip(".,()[]")) for tok in s.split())
        verdict = any(k in s.lower() for k in
                      ("pass", "fail", "ok", "error", "warning", "passed",
                       "failed", "passed!", "вывод", "итог", "результат"))
        if has_num or verdict:
            keep.append(f"  {s[:150]}")
        if len(keep) >= limit:
            break
    return "\n".join(keep)


# ==========================================================================
#  Отчёт
# ==========================================================================
def _system(proj: Project) -> str:
    head = (f"Ты — инженер-отчётчик проекта «{proj.name}».\n"
            + (f"Проект: {proj.description}\n" if proj.description else ""))
    return head + (
        "\nПолучишь ФАКТЫ из артефактов сборки и результаты выполненных шагов.\n"
        "Напиши краткое резюме на русском, 3–6 предложений: что сделано, "
        "какие ключевые числа получены, что требует внимания.\n\n"
        "ЖЁСТКОЕ ПРАВИЛО: используй только числа из блока ФАКТЫ. Если нужного "
        "числа там нет — пиши «нет данных». Не выдумывай, не округляй "
        "и не переноси числа из других проектов."
    )


def steps_digest(state: GraphState) -> str:
    lines = []
    for s in state.get("steps", [])[:30]:
        lines.append(f"  {s.get('status', '?'):8s} {s.get('title') or s.get('target')}"
                     f"  ({s.get('agent')}, код {s.get('exit_code')},"
                     f" {s.get('duration_s', 0):.1f}s)")
        if s.get("error"):
            lines.append(f"           ошибка: {str(s['error'])[:220]}")
    return "\n".join(lines) or "(шагов не было)"


def structural(state: GraphState) -> str:
    """Отчёт без модели — работает всегда."""
    steps = state.get("steps", [])
    ok = sum(1 for s in steps if s.get("status") == "done")
    bad = [s for s in steps if s.get("status") in ("failed", "blocked")]
    lines = [f"Заказ: {state.get('text', '')}", ""]
    for s in steps:
        mark = {"done": "OK", "failed": "FAIL", "skipped": "SKIP",
                "blocked": "BLOCK"}.get(s.get("status", ""), s.get("status", "?"))
        title = s.get("title") or ""
        target = s.get("target") or ""
        # имя цели и её описание — разные вещи: «link-budget» и
        # «энергетика тракта». В отчёте нужны оба
        what = f"{target} — {title}" if title and title != target else (
            target or title)
        lines.append(f"[{mark}] {what} — "
                     f"{s.get('agent')} (код {s.get('exit_code')}, "
                     f"{s.get('duration_s', 0):.1f}s)")
        if s.get("error"):
            lines.append(f"        {s['error']}")
    lines += ["", f"Итог: {ok}/{len(steps)} шагов выполнено."]
    if bad:
        lines.append("Не выполнено: " + ", ".join(
            s.get("target", "?") for s in bad))
    return "\n".join(lines)


def build_report(state: GraphState, proj: Project) -> tuple[str, str, list[str]]:
    """(факты, отчёт, использованные файлы)."""
    facts, used = collect_facts(proj)
    report = structural(state)

    if llm.enabled() and facts:
        summary = llm.complete(
            "Сделай краткое резюме по результату заказа.",
            f"ФАКТЫ:\n{facts}\n\nРЕЗУЛЬТАТ ШАГОВ:\n{steps_digest(state)}",
            system=_system(proj), fast=True, max_tokens=420, temperature=0.1)
        if summary:
            report = f"{summary}\n\n{'—' * 50}\n{report}"

    if state.get("question"):
        report += f"\n\nНужна помощь: {state['question']}"

    return facts, report, used


def node(state: GraphState, proj: Project) -> GraphState:
    """Узел графа: собрать факты и отчёт."""
    facts, report, used = build_report(state, proj)
    state["facts"] = facts
    state["report"] = report
    state["artifacts"] = used
    return state