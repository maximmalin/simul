"""
Krystal Radio — агентный рантайм (agent runtime).

Это «мозг» агентной части: он превращает 23 агента из AGENT_PROMPTS.md +
AGENT_ENCLOSURE.md в живых исполнителей, которые реально управляют проектом.

Что делает рантайм
------------------
1. **Регистрация** — сопоставляет локальных агентов (ID, цель, файлы, команда)
   с ролями баuryeu-ростер (`bureau_agents`) и записывает/обновляет их.
2. **Исполнение** — запускает make-цель или python-скрипт, стримит лог,
   пишет прогресс в `bureau_tasks`, статус в `bureau_agents`.
3. **Результаты** — по завершении задачи создаёт `bureau_deliverables`
   (артефакт + метаданные) и `bureau_messages` (отчёт в канал Comms).
4. **Метрики** — держит `bureau_projects.progress` в актуальном состоянии,
   считая завершённые задачи.
5. **Ответы** — LLM (OpenAI-совместимый) вместо захардкоженных шаблонов;
   при отсутствии ключа — детерминированный оффлайн-ответ.

Переменные окружения
--------------------
    SUPABASE_URL / SUPABASE_ANON_KEY / SUPABASE_SERVICE_KEY
    KR_LLM_BASE   — база OpenAI-совместимого API (default https://api.openai.com/v1)
    KR_LLM_MODEL  — модель (default gpt-4o-mini)
    KR_LLM_KEY    — ключ (если нет — оффлайн-режим)
    KR_ROOT       — корень репозитория
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import agent_bridge as BR                      # noqa: E402
import krsupabase                              # noqa: E402

ROOT = BR.ROOT
OUT = ROOT / "out"

LLM_BASE = os.environ.get("KR_LLM_BASE", "https://api.openai.com/v1").rstrip("/")
LLM_MODEL = os.environ.get("KR_LLM_MODEL", "gpt-4o-mini")
LLM_KEY = os.environ.get("KR_LLM_KEY", "")

# --------------------------------------------------------------------------
# Сопоставление локальных агентов на роли bureau-ростера
# bureau role_key: chief | theorist | designer | analyst | tester | optimizer
# --------------------------------------------------------------------------
ROLE_MAP: dict[str, tuple[str, str, str]] = {
    # id -> (role_key, role_title, designation)
    "AGENT-MK-01": ("chief", "Build Commander", "Начальник сборки"),
    "AGENT-MK-02": ("chief", "Build Commander", "Начальник сборки"),
    "AGENT-MK-03": ("designer", "Report Integrator", "Интегратор отчёта"),
    "AGENT-TST-01": ("tester", "Verification Lead", "Ведущий проверки"),
    "AGENT-TST-02": ("tester", "Verification Lead", "Ведущий проверки"),
    "AGENT-GNUR-01": ("analyst", "RF Analyst", "Аналитик РЧ"),
    "AGENT-TH-01": ("analyst", "Thermal Analyst", "Тепловой аналитик"),
    "AGENT-RL-01": ("analyst", "Reliability Analyst", "Аналитик надёжности"),
    "AGENT-DF-01": ("designer", "Direction-Finding Designer", "Проектировщик пеленгации"),
    "AGENT-DF-02": ("designer", "Antenna CAD Designer", "Проектировщик антенн"),
    "AGENT-ML-01": ("designer", "ML Dataset Designer", "Проектировщик ML-датасета"),
    "AGENT-ML-02": ("optimizer", "ML Trainer", "Тренер модели"),
    "AGENT-ML-03": ("optimizer", "ML Inference Runner", "Прогон инференса"),
    "AGENT-DOC-01": ("designer", "Docs Author", "Автор документации"),
    "AGENT-DOC-02": ("designer", "Docs Author", "Автор документации"),
    "AGENT-DOC-03": ("tester", "Compliance Tracker", "Учётчик соответствия"),
    "AGENT-GIT-01": ("optimizer", "Release Engineer", "Инженер релиза"),
    "AGENT-GIT-02": ("optimizer", "Release Engineer", "Инженер релиза"),
    "AGENT-KICAD-01": ("designer", "Schematic Generator", "Генератор схем"),
    "AGENT-KICAD-02": ("designer", "PCB Emitter", "Эмиттер PCB"),
    "AGENT-KICAD-03": ("tester", "Netlist Verifier", "Проверяющий нетлиста"),
    "AGENT-KICAD-04": ("theorist", "Spec Author", "Автор ТЗ"),
    "AGENT-ENC-01": ("designer", "Enclosure Engineer", "Инженер корпуса"),
}

CHANNEL_BY_ROLE = {
    "chief": "command",
    "theorist": "research",
    "designer": "engineering",
    "analyst": "optimization",
    "tester": "testing",
    "optimizer": "optimization",
}

#: make-цель -> описание результата (для deliverable)
TARGET_DELIVERABLE = {
    "page": ("Отчёт на одной странице", "html", "out/krystal-radio.html"),
    "gnuradio": ("Водопад GNU Radio", "png", "out/waterfall.png"),
    "link-budget": ("Энергетика тракта и дальность", "json", "out/link_budget.json"),
    "direction": ("Диаграмма пеленгации", "json", "out/direction_finding.json"),
    "thermal": ("Тепловой расчёт", "json", "out/thermal_fast.json"),
    "reliability": ("Расчёт надёжности MTBF", "json", "out/reliability.json"),
    "pcb": ("Плата и gerbers", "kicad_pcb", "out/board.kicad_pcb"),
    "drawings": ("GOST-чертежи", "svg", "out/board.svg"),
    "test": ("Результат тестов", "txt", None),
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ==========================================================================
# Локальные агенты (парс��тся из Markdown, см. agent_bridge)
# ==========================================================================
def local_agents() -> list[dict]:
    agents = BR.parse_agents()
    enc = BR.parse_enclosure_agent()
    if enc:
        agents.append(enc)
    return agents


def agent_codename(agent_id: str) -> str:
    """AGENT-MK-01 -> MK-01 (совпадает с форматом codename баureau)."""
    return agent_id.replace("AGENT-", "")


# ==========================================================================
# Регистрация в bureau_agents
# ==========================================================================
def sync_agents(sb: krsupabase.KrSupabase) -> dict:
    """Записать/обновить локальных агентов в bureau_agents."""
    if not sb.ok:
        return {"ok": False, "reason": "supabase not configured"}
    result = {"ok": True, "created": [], "updated": [], "skipped": []}

    existing = sb.select("bureau_agents", select="id,codename,role_key")
    by_codename = {a.get("codename"): a for a in (existing or [])}

    for la in local_agents():
        aid = la.get("id", "")
        role, role_title, designation = ROLE_MAP.get(
            aid, ("analyst", "Project Agent", "Исполнитель проекта"))
        codename = agent_codename(aid)
        goal = la.get("goal") or ""
        payload = {
            "codename": codename,
            "role_key": role,
            "role_title": role_title,
            "designation": designation,
            "status": "idle",
            "specialty": (goal[:220] or la.get("title", "")),
            "bio": la.get("title", ""),
            "quote": f"«{la.get('command', '')[:180]}»",
            "traits": [role, "krystal-radio"],
            "psych_profile": la.get("files", "")[:400],
            "zone_callsign": aid,
            "efficiency": 85,
            "cycles_completed": 0,
        }
        prev = by_codename.get(codename)
        if prev:
            sb.update("bureau_agents", {"id": prev["id"]}, payload)
            result["updated"].append(codename)
        else:
            sb.insert("bureau_agents", payload)
            result["created"].append(codename)
    return result


def set_agent_status(sb: krsupabase.KrSupabase, agent_id: str, status: str,
                     current_task: str | None = None,
                     bump_cycle: bool = False) -> None:
    """Обновить статус агента (status/current_task/cycles_completed)."""
    codename = agent_codename(agent_id)
    patch: dict = {"status": status}
    if current_task is not None:
        patch["current_task"] = current_task
    if bump_cycle:
        # увеличить cycles_completed на 1 через RPC-подобный запрос невозможен,
        # поэтому читаем и пишем обратно
        row = sb.select("bureau_agents", select="id,cycles_completed",
                        eq={"codename": codename})
        if row:
            patch["cycles_completed"] = (row[0].get("cycles_completed") or 0) + 1
    sb.update("bureau_agents", {"codename": codename}, patch)


# ==========================================================================
# Задачи (bureau_tasks)
# ==========================================================================
def ensure_project(sb: krsupabase.KrSupabase, code: str = "KRYSTAL-01",
                   title: str = "Krystal Radio (zero-IF SDR)") -> str | None:
    """Найти или создать проект под krystal-radio."""
    rows = sb.select("bureau_projects", select="id,code", eq={"code": code})
    if rows:
        return rows[0]["id"]
    row = sb.insert("bureau_projects", {
        "code": code,
        "title": title,
        "objective": "Построить zero-IF SDR приёмник 0–7 ГГц: схема, PCB, "
                     "чертежи, тесты, reliability/thermal, EMC, пеленгация, "
                     "ML-распознавание сигналов.",
        "status": "active",
        "priority": "high",
        "progress": 0,
    })
    return row[0]["id"] if row else None


def create_task(sb: krsupabase.KrSupabase, project_id: str, agent_id: str,
                title: str, directive: str, priority: str = "high",
                status: str = "in_progress") -> str | None:
    """Создать задачу (агент -> проект) и вернуть её id."""
    rows = sb.select("bureau_agents", select="id", eq={"codename": agent_codename(agent_id)})
    aid = rows[0]["id"] if rows else None
    existing = sb.select("bureau_tasks", select="sort_order",
                         eq={"project_id": project_id}, order="sort_order.desc",
                         limit=1)
    so = ((existing[0].get("sort_order") if existing else 0) or 0) + 1
    row = sb.insert("bureau_tasks", {
        "project_id": project_id, "agent_id": aid, "title": title[:120],
        "directive": directive, "status": status, "priority": priority,
        "sort_order": so,
    })
    return row[0]["id"] if row else None


def finish_task(sb: krsupabase.KrSupabase, task_id: str, status: str = "done") -> None:
    if task_id:
        sb.update("bureau_tasks", {"id": task_id}, {"status": status})


# ==========================================================================
# Deliverables + Comms
# ==========================================================================
def publish_deliverable(sb: krsupabase.KrSupabase, project_id: str,
                        agent_id: str, title: str, description: str,
                        content: str, fmt: str = "txt",
                        status: str = "final") -> None:
    """Создать результат работы агента."""
    rows = sb.select("bureau_agents", select="id", eq={"codename": agent_codename(agent_id)})
    aid = rows[0]["id"] if rows else None
    sb.insert("bureau_deliverables", {
        "project_id": project_id, "agent_id": aid, "title": title[:120],
        "description": description[:400], "file_content": content[:200_000],
        "file_format": fmt, "status": status,
    })


def post_message(sb: krsupabase.KrSupabase, agent_id: str, content: str,
                 channel: str | None = None) -> None:
    """Отчёт агента в общий эфир (to_agent_id=null -> broadcast)."""
    rows = sb.select("bureau_agents", select="id", eq={"codename": agent_codename(agent_id)})
    if not rows:
        return
    aid = rows[0]["id"]
    role = ROLE_MAP.get(agent_id, ("analyst",))[0]
    sb.insert("bureau_messages", {
        "from_agent_id": aid, "to_agent_id": None, "content": content[:4000],
        "channel": channel or CHANNEL_BY_ROLE.get(role, "command"),
    })


# ==========================================================================
# LLM
# ==========================================================================
SYSTEM_PROMPT = """Ты — агент бюро Krystal Radio. Отвечай кратко, по-русски, \
технически точно. Ты работаешь над проектом zero-IF SDR-приёмника: схема, \
PCB (tscircuit), ngspice, антенны, GNU Radio ко-симуляция, надёжность \
(REQ-26), тепло (Tj ≤ 85 °C), EMC (REQ-21..25), пеленгация (NEC-2/AntennaSim), \
YOLO-распознавание сигналов. Не выдумывай числа: если данных нет — так и скажи \
и предложи команду, которая их получит."""


def llm_complete(user: str, context: str = "") -> str | None:
    """Вызов через llm.chat (поддержка всех провайдеров). None — если ключа нет или ошибка."""
    import llm as llm_mod
    if not llm_mod.enabled():
        return None
    msgs = [{"role": "system", "content": SYSTEM_PROMPT}]
    if context:
        msgs.append({"role": "user", "content": f"Контекст:\n{context}"})
    msgs.append({"role": "user", "content": user})
    turn = llm_mod.chat(msgs, temperature=0.2, max_tokens=700, timeout=60.0)
    if turn and turn.text:
        return turn.text.strip()
    return None


def offline_answer(prompt: str, agent: dict) -> str:
    """Детерминированный ответ без LLM — по данным проекта."""
    t = agent.get("title", "")
    goal = agent.get("goal", "")
    cmd = agent.get("command", "")
    tail = ("Ключ LLM не задан (KR_LLM_KEY) — работаю в оффлайн-режиме. "
            "Фактические данные беру из артефактов проекта.")
    return (f"{t}\n\nЦель: {goal}\n\n{tail}\n\n"
            f"Команда агента:\n{cmd}")


# ==========================================================================
# Исполнение задачи: make-цель / скрипт
# ==========================================================================
@dataclass
class RunResult:
    agent_id: str
    target: str
    ok: bool
    exit_code: int
    log: list[str] = field(default_factory=list)
    duration_s: float = 0.0
    artifact: str | None = None


def run_target(agent_id: str, target: str, *, sb: krsupabase.KrSupabase | None = None,
               timeout: int = 3600) -> RunResult:
    """Запустить make-цель и (если настроен Supabase) отразить это в UI."""
    if target not in BR.ALLOWED_TARGETS:
        return RunResult(agent_id, target, False, -2,
                         [f"target not allowed: {target}"])

    project_id = ensure_project(sb) if sb and sb.ok else None
    task_id = None
    if sb and sb.ok:
        task_id = create_task(sb, project_id, agent_id,
                              title=f"make {target}",
                              directive=f"Выполнить цель сборки make {target}")
        set_agent_status(sb, agent_id, "processing", f"make {target}")

    set_agent_status(sb, agent_id, "processing", f"make {target}") if sb and sb.ok else None

    job = BR.spawn_job(f"make {target}", ["make", target])
    t0 = time.monotonic()
    seen = 0
    while True:
        time.sleep(1.0)
        d = BR.JOBS.get(job.id)
        if not d:
            break
        seen = len(d.log)
        if d.status in ("done", "failed"):
            break
        if time.monotonic() - t0 > timeout:
            break

    d = BR.JOBS.get(job.id)
    log = d.log if d else []
    ok = bool(d and d.status == "done" and d.exit_code == 0)
    dur = time.monotonic() - t0
    res = RunResult(agent_id, target, ok, d.exit_code if d else -1, log, dur)

    # артефакт по умолчанию
    title, fmt, rel = TARGET_DELIVERABLE.get(
        target, (f"Вывод make {target}", "txt", None))
    if rel and (ROOT / rel).is_file():
        res.artifact = rel

    if sb and sb.ok:
        finish_task(sb, task_id, "done" if ok else "review")
        if ok:
            set_agent_status(sb, agent_id, "active", None, bump_cycle=True)
            body = (f"make {target} — успешно за {dur:.1f} c"
                    + (f", артефакт {rel}" if rel else "") + ".")
            if res.artifact:
                try:
                    content = (ROOT / res.artifact).read_text(
                        encoding="utf-8", errors="replace")
                except OSError:
                    content = ""
                publish_deliverable(sb, project_id, agent_id, title,
                                     f"Результат `make {target}`", content, fmt)
            post_message(sb, agent_id, body)
        else:
            set_agent_status(sb, agent_id, "idle", f"make {target}: ошибка")
            post_message(sb, agent_id,
                         f"make {target} — провал (код {res.exit_code}).\n"
                         + "\n".join(log[-8:]))
        update_progress(sb, project_id)

    return res


def run_script(agent_id: str, script: str, args: list[str] | None = None, *,
               sb: krsupabase.KrSupabase | None = None) -> RunResult:
    """Запустить python-скрипт из белого списка."""
    if script not in BR.ALLOWED_SCRIPTS:
        return RunResult(agent_id, script, False, -2,
                         [f"script not allowed: {script}"])
    rel, _d = BR.ALLOWED_SCRIPTS[script]
    argv = [sys.executable, rel] + list(args or [])
    job = BR.spawn_job(rel, argv)
    t0 = time.monotonic()
    while True:
        time.sleep(1.0)
        d = BR.JOBS.get(job.id)
        if d and d.status in ("done", "failed"):
            break
        if time.monotonic() - t0 > 3600:
            break
    d = BR.JOBS.get(job.id)
    ok = bool(d and d.status == "done" and d.exit_code == 0)
    res = RunResult(agent_id, f"py:{script}", ok, d.exit_code if d else -1,
                    d.log if d else [], time.monotonic() - t0)
    if sb and sb.ok:
        post_message(sb, agent_id,
                     f"{script} — {'ок' if ok else 'ошибка'} "
                     f"(код {res.exit_code}).")
    return res


# ==========================================================================
# Прогресс проекта
# ==========================================================================
def update_progress(sb: krsupabase.KrSupabase, project_id: str | None) -> int | None:
    """Пересчитать progress = % завершённых задач."""
    if not sb.ok or not project_id:
        return None
    rows = sb.select("bureau_tasks", select="id,status", eq={"project_id": project_id})
    if not rows:
        return None
    total = len(rows)
    done = sum(1 for r in rows if r.get("status") == "done")
    pct = int(round(100 * done / total)) if total else 0
    sb.update("bureau_projects", {"id": project_id}, {"progress": pct})
    return pct


# ==========================================================================
# Ответ на директиву оператора (для ChatTab)
# ==========================================================================
def answer_directive(prompt: str, project_id: str | None = None,
                     sb: krsupabase.KrSupabase | None = None) -> dict:
    """Ответить оператору: выбрать агента, ответить, создать задачу."""
    agent = _route(prompt)
    ctx = _project_context()
    text = llm_complete(prompt, ctx) or offline_answer(prompt, agent)
    task_created = False
    if sb and sb.ok:
        pid = project_id or ensure_project(sb)
        rows = sb.select("bureau_agents", select="id",
                         eq={"codename": agent_codename(agent["id"])})
        aid = rows[0]["id"] if rows else None
        existing = sb.select("bureau_tasks", select="sort_order",
                             eq={"project_id": pid}, order="sort_order.desc", limit=1)
        so = ((existing[0].get("sort_order") if existing else 0) or 0) + 1
        sb.insert("bureau_tasks", {
            "project_id": pid, "agent_id": aid, "title": prompt[:80],
            "directive": prompt, "status": "pending", "priority": "high",
            "sort_order": so,
        })
        post_message(sb, agent["id"],
                     f"Новая директива оператора: «{prompt[:100]}». "
                     "Задача поставлена в очередь.")
        task_created = True
    return {
        "agent_id": agent["id"],
        "agent_title": agent.get("title", ""),
        "response": text,
        "task_created": task_created,
    }


ROUTE_RULES: list[tuple[str, str]] = [
    ("analyst", r"\b(analy|telemetr|metric|measure|statistic|pattern|plot|график|метрик)"),
    ("tester", r"\b(test|stress|validat|break|check|verify|quality|test|тест|провер)"),
    ("optimizer", r"\b(optim|tune|improve|faster|efficien|performance|оптимиз)"),
    ("designer", r"\b(design|build|construct|architect|structure|chassis|topology|корпус|пеленга|модел)"),
    ("theorist", r"\b(theor|research|model|equation|formula|deriv|math|теори|расчет|формул)"),
]


def _route(prompt: str) -> dict:
    """Выбрать локального агента по ключевым словам."""
    p = prompt.lower()
    agents = {a["id"]: a for a in local_agents()}
    for role, rx in ROUTE_RULES:
        if re.search(rx, p):
            for aid, a in agents.items():
                if ROLE_MAP.get(aid, ("",))[0] == role:
                    return {"id": aid, **a}
    return {"id": "AGENT-MK-01", **agents.get("AGENT-MK-01", {})}


def _project_context() -> str:
    """Краткая сводка по артефактам — чтобы LLM не выдумывал числа."""
    bits: list[str] = []
    lb = BR.load_json(OUT / "link_budget.json")
    if lb:
        n = lb.get("noise", {})
        bits.append(f"шум {n.get('noise_floor_calc_dbm'):.1f} дБм, "
                    f"MDS {n.get('sensitivity_calc_dbm'):.1f} дБм"
                    if n.get("noise_floor_calc_dbm") is not None else "")
    th = BR.load_json(OUT / "thermal_fast.json")
    if th:
        bits.append(f"термо fast: {json.dumps(th, ensure_ascii=False)[:200]}")
    arts = len(BR.list_artifacts())
    bits.append(f"артефактов в out/: {arts}")
    return "\n".join(b for b in bits if b)


# ==========================================================================
# CLI
# ==========================================================================
def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description="Krystal Radio agent runtime")
    ap.add_argument("--sync", action="store_true",
                    help="зарегистрировать агентов в bureau_agents")
    ap.add_argument("--list", action="store_true", help="показать локальных агентов")
    ap.add_argument("--run", metavar="TARGET", help="выполнить make-цель")
    ap.add_argument("--script", metavar="NAME", help="выполнить скрипт из списка")
    ap.add_argument("--args", nargs="*", default=[], help="аргументы скрипта")
    ap.add_argument("--ask", metavar="PROMPT", help="ответить на директиву")
    ap.add_argument("--no-db", action="store_true", help="без Supabase")
    a = ap.parse_args()

    sb = None if a.no_db else krsupabase.sb()

    if a.list:
        for ag in local_agents():
            role = ROLE_MAP.get(ag["id"], ("analyst",))[0]
            print(f"{ag['id']:<18} {role:<10} {ag.get('title','')[:60]}")
        return

    if a.sync:
        r = sync_agents(sb) if sb else {"ok": False,
                                         "reason": "supabase не настроен"}
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return

    if a.run:
        r = run_target("AGENT-MK-01", a.run, sb=sb)
        print(f"\n[{a.run}] {'OK' if r.ok else 'FAIL'} "
              f"exit={r.exit_code} {r.duration_s:.1f}s")
        for line in r.log[-15:]:
            print("   ", line)
        return

    if a.script:
        r = run_script("AGENT-ML-01", a.script, a.args, sb=sb)
        print(f"\n[{a.script}] {'OK' if r.ok else 'FAIL'} exit={r.exit_code}")
        for line in r.log[-15:]:
            print("   ", line)
        return

    if a.ask:
        ans = answer_directive(a.ask, sb=sb)
        print(f"\n{ans['agent_id']} ({ans['agent_title']}):\n")
        print(ans["response"])
        return

    ap.print_help()


if __name__ == "__main__":
    main()