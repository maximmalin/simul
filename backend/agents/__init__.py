"""Агентно-функциональный граф — движок работы с заказами.

Пакет устроен так, что в нём нет ничего конкретного: ни имён целей,
ни названий артефактов, ни слов под язык проекта. Всё это приходит из
описания проекта (`agents.project.yaml` либо автоопределение), поэтому
движок одинаково управляет сборкой SDR, прошивкой, сайтом и CAD.

Слои
----
``types``      структуры данных: проект, цель, артефакт, шаг, состояние
``runner``     способы запускать цели: make, npm, just, shell + свои
``profile``    откуда взять описание проекта
``planner``    заказ → цели сборки
``reporter``   артефакты → факты → отчёт
``engine``     реестр агентных функций и сборка графа из них

Быстрый старт
-------------
    from agents import run_order, load_project

    proj = load_project("/path/to/project")
    result = run_order("собери всё и проверь тестами", proj)
    print(result["report"])

Из командной строки::

    python3 -m agents check          # что движок увидел в проекте
    python3 -m agents run "построй корпус"
    python3 -m agents ask "собери всё"
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

from .engine import (REGISTRY, ORDER, agent, graph_shape, have_langgraph,
                    run as _run, run_plain, build)
from .profile import load as _load_profile
from .reporter import build_report, collect_facts
from . import service
from .runner import REGISTRY as RUNNERS, available as runners_available
from .types import Artifact, GraphState, Project, Step, Target

__all__ = [
    "load_project", "run_order", "graph_shape", "have_langgraph",
    "service", "submit", "get", "list_orders",
    "register_agent", "agents_list", "runners", "Project", "Target",
    "Artifact", "Step", "GraphState", "REGISTRY", "RUNNERS",
]


# ==========================================================================
#  Публичный вход
# ==========================================================================
def load_project(root: str | Path = ".", name: str = "",
                 profile: str = "", **overrides) -> Project:
    """Описание проекта: из файла описания либо автоопределением."""
    return _load_profile(root, name=name, profile_path=profile, **overrides)


def run_order(text: str, proj: Project | str | Path = ".",
              order_id: str | None = None) -> dict:
    """Выполнить заказ. proj — описание проекта или путь к нему."""
    if not isinstance(proj, Project):
        proj = load_project(proj)
    if have_langgraph():
        return _run(text, proj, order_id)
    return run_plain(text, proj, order_id)


def register_agent(name: str, *, title: str = "", after=(), provides=(),
                   kind: str = "generic"):
    """Зарегистрировать свою функцию как узел графа."""
    from .engine import agent as _a
    return _a(name, title=title, after=after, provides=provides, kind=kind)


def agents_list() -> list[dict]:
    return graph_shape()


def runners() -> list[str]:
    return runners_available()


# ==========================================================================
#  CLI
# ==========================================================================
def main(argv: list[str] | None = None) -> int:
    import argparse
    import json
    import sys

    ap = argparse.ArgumentParser(
        prog="python3 -m agents",
        description="Агентно-функциональный граф: заказ → цели → сборка → отчёт")
    ap.add_argument("command", choices=("check", "run", "ask", "graph", "runners",
                         "prompts"))
    ap.add_argument("args", nargs="*")
    ap.add_argument("--root", default=".", help="корень проекта")
    ap.add_argument("--profile", default="", help="файл описания проекта")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--write", default="", help="записать в файл")
    ap.add_argument("--check", action="store_true",
            help="проверить расхождения с проектом")
    opts = ap.parse_args(argv)

    if opts.command == "runners":
        print("раннеры сборки:")
        for n in runners():
            print(f"  - {n}")
        return 0

    if opts.command == "graph":
        print("узлы графа:")
        for a in graph_shape():
            after = f" ← {', '.join(a['after'])}" if a["after"] else ""
            print(f"  {a['name']:10s} [{a['kind']:8s}] {a['title']}{after}")
            if a["provides"]:
                print(f"             даёт: {', '.join(a['provides'])}")
        print(f"\nlanggraph: {'есть' if have_langgraph() else 'нет — работает запасной путь'}")
        return 0

    proj = load_project(opts.root, profile=opts.profile)

    if opts.command == "check":
        if opts.json:
            print(json.dumps(proj.as_dict(), indent=2, ensure_ascii=False,
                             default=str))
            return 0
        print(f"проект      : {proj.name}")
        print(f"раннер      : {proj.runner}")
        print(f"целей       : {len(proj.targets)} "
              f"(верхнего уровня {len(proj.top_level())})")
        facts, files = collect_facts(proj)
        print(f"артефактов  : {len(files)}")
        if proj.description:
            print(f"описание    : {proj.description[:100]}")
        if proj.vocabulary:
            print(f"словарь     : {len(proj.vocabulary)} синонимов")
        print("\nцели верхнего уровня (их предлагает планировщик):")
        for t in proj.buildable():
            if t.top_level:
                print(f"  - {t.name:16s} {(t.title or '')[:56]}")
        skipped = [t.name for t in proj.top_level()
                   if t.name not in {x.name for x in proj.buildable()}]
        if skipped:
            print(f"\nслужебные (в планы не попадают): {', '.join(skipped)}")
        return 0

    if opts.command == "prompts":
        from . import prompts as P
        ps = P.generate(proj)
        if opts.check:
            problems = P.verify(proj, ps)
            if problems:
                for pr in problems:
                    print("ПРОБЛЕМА:", pr)
                return 1
            print(f"OK: {len(ps)} промптов, расхождений нет")
            return 0
        md = P.to_markdown(proj, ps)
        if opts.write:
            import pathlib as _p
            _p.Path(opts.write).write_text(md, encoding="utf-8")
            print(f"записано: {opts.write} ({len(ps)} промптов)")
        else:
            print(md)
        return 0

    if not opts.args:
        ap.error("нужен текст заказа")
    text = " ".join(opts.args)

    if opts.command == "ask":
        from .planner import plan
        r = plan(text, proj)
        print(json.dumps({k: v for k, v in r.items() if k != "steps"},
                         indent=2, ensure_ascii=False))
        return 0

    res = run_order(text, proj)
    if opts.json:
        print(json.dumps(res, indent=2, ensure_ascii=False, default=str))
        return 0
    print(f"intent : {res['intent']}  ({res['planner']})")
    print(f"статус : {res['status']}  {res['progress']}%\n")
    for s in res["steps"]:
        print(f"  {s['status']:8s} {s['target']:14s} код={s['exit_code']} "
              f"{s.get('duration_s', 0):.1f}s")
        if s.get("error"):
            print(f"           {s['error'][:160]}")
    if res.get("report"):
        print(f"\n{'=' * 60}\n{res['report']}")
    return 0 if res["status"] in ("done", "partial") else 1


if __name__ == "__main__":
    raise SystemExit(main())