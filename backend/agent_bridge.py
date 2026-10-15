"""
Krystal Radio — Agent Bridge API.

Локальный HTTP-мост между LLM-агентами (backend) и веб-интерфейсом
EngineeringTeam (frontend/). Агент вызывает эндпоинты -> мост запускает
цели Makefile / python-скрипты -> результат возвращается во frontend
(и, при желании, в чат Supabase).

Эндпоинты
---------
GET  /api/health              — состояние сборки, окружения, ключевых артефактов
GET  /api/artifacts           — список всех артефактов в out/ (размер, тип, mtime)
GET  /api/artifact/<name>     — отдать конкретный файл (html/png/svg/glb/json)
GET  /api/agents              — агенты из AGENT_PROMPTS.md (парсер Markdown)
GET  /api/agent/<id>          — один агент + его команда
GET  /api/tests               — результат pytest tests/requirements
GET  /api/metrics             — сводка: thermal, reliability, link-budget, direction, emc
GET  /api/targets             — список make-целей с описаниями
POST /api/build               — запустить make-цель {target} -> job_id
GET  /api/build/<job_id>      — статус джобы (running/done/failed, лог)
POST /api/run                 — выполнить произвольную python-команду из белого списка
POST /api/prompt              — задача для агента: вернуть prompt-шаблон

Запуск
------
    python3 backend/agent_bridge.py --port 8787
    # или
    make agent-api

Конфигурация
------------
    KR_ROOT    — корень репозитория (по умолчанию: родитель backend/)
    KR_TOKEN    — если задан, требуется заголовок Authorization: Bearer <token>
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

ROOT = Path(os.environ.get("KR_ROOT", Path(__file__).resolve().parent.parent))
TOKEN = os.environ.get("KR_TOKEN")


def out_dir() -> Path:
    """Каталог артефактов (вычисляется каждый раз — ROOT может смениться)."""
    return ROOT / "out"

# --------------------------------------------------------------------------
# Белый список: только эти make-цели и команды можно запускать через API.
# Всё остальное — отказ. Это единственная точка, где агент касается shell.
# --------------------------------------------------------------------------
ALLOWED_TARGETS: dict[str, str] = {
    "all": "полная сборка: netlist → PCB → docs → тесты",
    "netlist": "SKiDL → KiCad netlist",
    "spice": "netlist → ngspice deck",
    "pcb": "tscircuit автотрассировка + экспорт",
    "publish": "копирование артефактов PCB в out/",
    "erc": "SKiDL electrical rules check",
    "drc": "kicad-cli DRC по плате",
    "cad": "пересборка STEP/STL антенн (FreeCAD)",
    "drawings": "STEP → GOST-чертежи (DXF/SVG/PDF)",
    "board-svg": "SVG платы",
    "viewer": "сборка three.js вьювера (rollup)",
    "page": "одна HTML-страница (3D + чертежи + водопад + link budget + пеленгация)",
    "gnuradio": "GNU Radio ко-симуляция → водопад + спектр",
    "link-budget": "энергетика тракта → дальность приёма",
    "direction": "пеленгация (NEC-2 / AntennaSim)",
    "reliability": "Octave MTBF (REQ-26)",
    "thermal": "fast + FEM тепловой расчёт",
    "test": "pytest tests/requirements + netlist + pin contract",
    "manifest": "manifest.json",
}

# python-скрипты: имя -> (путь относительно ROOT, описание).
# Путь жёстко задан — агент не может подменить интерпретатор или файл.
ALLOWED_SCRIPTS: dict[str, tuple[str, str]] = {
    "gen_op_docs": ("scripts/gen_op_docs.py", "эксплуатационная документация"),
    "link_budget": ("sim/scripts/link_budget.py", "энергетика тракта → дальность"),
    "direction_finding": ("sim/scripts/direction_finding.py", "пеленгация"),
    "thermal_pcb": ("sim/scripts/thermal_pcb.py", "тепловой расчёт"),
    "erc_sdr": ("sim/scripts/erc_sdr.py", "SKiDL ERC"),
    "virtual_rx": ("src/gnuradio_krystal/krystal_virtual_rx.py",
                   "GNU Radio виртуальный приём"),
    "ml_dataset": ("ml/dataset.py", "генерация YOLO-датасета"),
    "build_animation": ("animations/build_animation.py", "анимация сборки"),
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------
# Цепочка сборки: реальные стадии `make all` + маркеры артефактов
# Статус выводится из наличия/свежести артефакта и активных джоб.
# --------------------------------------------------------------------------
CHAIN: list[dict] = [
    {"n": 1,  "key": "netlist", "label": "Netlist", "sub": "SKiDL → KiCad",
     "target": "netlist", "artifacts": ["hardware/kicad/sdr_receiver.kicad_sch"]},
    {"n": 2,  "key": "spice", "label": "SPICE", "sub": "ngspice deck",
     "target": "spice", "artifacts": ["sim/netlists/sdr_skidl_ngspice.cir"]},
    {"n": 3,  "key": "pcb", "label": "PCB", "sub": "tscircuit autoroute",
     "target": "pcb", "artifacts": ["out/board.kicad_pcb", "out/gerbers.zip"]},
    {"n": 4,  "key": "drawings", "label": "Drawings", "sub": "GOST / DXF / SVG",
     "target": "drawings",
     "artifacts": ["sim/cad/figures/band_a_GOST.svg", "out/board.svg"]},
    {"n": 5,  "key": "gnuradio", "label": "Waterfall", "sub": "GNU Radio co-sim",
     "target": "gnuradio", "artifacts": ["out/waterfall.png",
                                        "out/virtual_rx_spectrum.json"]},
    {"n": 6,  "key": "link-budget", "label": "Link budget", "sub": "дальность",
     "target": "link-budget", "artifacts": ["out/link_budget.json",
                                            "out/link_budget.svg"]},
    {"n": 7,  "key": "direction", "label": "Direction", "sub": "пеленгация NEC-2",
     "target": "direction", "artifacts": ["out/direction_finding.json",
                                           "out/direction_finding.svg"]},
    {"n": 8,  "key": "page", "label": "Report", "sub": "3D + чертежи",
     "target": "page", "artifacts": ["out/krystal-radio.html"]},
    {"n": 9,  "key": "thermal", "label": "Thermal", "sub": "fast + FEM",
     "target": "thermal", "artifacts": ["out/thermal_fast.json",
                                        "out/thermal_fem.json"]},
    {"n": 10, "key": "reliability", "label": "Reliability", "sub": "Octave MTBF",
     "target": "reliability", "artifacts": ["out/reliability.json"]},
    {"n": 11, "key": "test", "label": "Tests", "sub": "REQ suite",
     "target": "test", "artifacts": []},
    {"n": 12, "key": "manifest", "label": "Manifest", "sub": "BOM / состав",
     "target": "manifest", "artifacts": ["manifest.json"]},
]

#: активные джобы по make-цели (для сопоставления со стадией цепочки)
def _active_by_target() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for j in JOBS.values():
        if j.status not in ("queued", "running"):
            continue
        # ключ — голое имя make-цели (для агентских джобов "AGENT-X: make y")
        key = j.make_target
        if not key and j.target.startswith("make "):
            key = j.target[len("make "):].strip()
        if key:
            out[key] = {"id": j.id, "agent_id": j.agent_id,
                        "started_at": j.started_at, "label": j.target}
    return out


def chain_status() -> dict:
    """Состояние цепочки сборки: done / stale / running / missing."""
    active = _active_by_target()
    stages: list[dict] = []
    for st in CHAIN:
        arts = []
        for rel in st["artifacts"]:
            p = ROOT / rel
            if p.is_file():
                mtime = p.stat().st_mtime
                arts.append({
                    "path": rel,
                    "size": p.stat().st_size,
                    "modified": datetime.fromtimestamp(
                        mtime, timezone.utc).isoformat(timespec="seconds"),
                })
        job = active.get(st["target"])
        if job:
            state = "running"
        elif st["artifacts"] and len(arts) == len(st["artifacts"]):
            state = "done"
        elif arts:
            state = "stale"
        else:
            state = "missing"
        stages.append({
            **{k: v for k, v in st.items() if k != "artifacts"},
            "state": state,
            "files": arts,
            "has_all": len(arts) == len(st["artifacts"]) if st["artifacts"] else True,
            "job": job,
        })

    done = sum(1 for s in stages if s["state"] == "done")
    running = sum(1 for s in stages if s["state"] == "running")
    return {
        "stages": stages,
        "total": len(stages),
        "done": done,
        "running": running,
        "progress": int(round(100 * done / len(stages))) if stages else 0,
        "updated": _now(),
    }


# --------------------------------------------------------------------------
# Проекты: Supabase если доступен, иначе локальный JSON-store
# --------------------------------------------------------------------------
PROJECTS_FILE = "projects.json"


def _projects_path() -> Path:
    return ROOT / ".krystal" / PROJECTS_FILE


def load_projects() -> list[dict]:
    """Локальный список проектов (используется без Supabase)."""
    p = _projects_path()
    if not p.is_file():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def save_projects(items: list[dict]) -> None:
    p = _projects_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


def projects_list() -> dict:
    """Все проекты: из Supabase, если он есть; иначе локально."""
    sys.path.insert(0, str(ROOT / "backend"))
    import krsupabase
    sb = krsupabase.sb()
    if sb and sb.ok:
        rows = sb.select("bureau_projects", select="*", order="created_at")
        if rows is not None:
            tasks = sb.select("bureau_tasks", select="id,project_id,status") or []
            by_proj: dict[str, dict[str, int]] = {}
            for t in tasks:
                b = by_proj.setdefault(t["project_id"], {"total": 0, "done": 0})
                b["total"] += 1
                if t.get("status") == "done":
                    b["done"] += 1
            for r in rows:
                b = by_proj.get(r["id"], {"total": 0, "done": 0})
                r["tasks_total"] = b["total"]
                r["tasks_done"] = b["done"]
            return {"source": "supabase", "count": len(rows), "projects": rows}
    items = load_projects()
    for it in items:
        b = it.pop("_counts", None)
        if b:
            it["tasks_total"], it["tasks_done"] = b.get("total", 0), b.get("done", 0)
    return {"source": "local", "count": len(items), "projects": items}


def project_create(payload: dict) -> dict:
    """Создать проект (Supabase или локально)."""
    code = str(payload.get("code", "")).strip().upper()
    title = str(payload.get("title", "")).strip()
    if not code or not title:
        return {"error": "code and title are required"}
    objective = str(payload.get("objective", "")).strip() or title

    sys.path.insert(0, str(ROOT / "backend"))
    import krsupabase
    sb = krsupabase.sb()
    if sb and sb.ok:
        dup = sb.select("bureau_projects", select="id", eq={"code": code})
        if dup:
            return {"error": f"project code already exists: {code}"}
        row = sb.insert("bureau_projects", {
            "code": code, "title": title, "objective": objective,
            "status": payload.get("status", "planning"),
            "priority": payload.get("priority", "standard"),
            "progress": 0,
        })
        if row:
            return {"ok": True, "source": "supabase", "project": row[0]}
        return {"error": "supabase insert failed"}

    items = load_projects()
    if any(p.get("code") == code for p in items):
        return {"error": f"project code already exists: {code}"}
    proj = {
        "id": uuid.uuid4().hex[:12],
        "code": code,
        "title": title,
        "objective": objective,
        "status": payload.get("status", "planning"),
        "priority": payload.get("priority", "standard"),
        "progress": 0,
        "created_at": _now(),
    }
    items.append(proj)
    save_projects(items)
    return {"ok": True, "source": "local", "project": proj}


# --------------------------------------------------------------------------
# Джобы сборки
# --------------------------------------------------------------------------
@dataclass
class Job:
    id: str
    target: str
    status: str = "queued"          # queued | running | done | failed
    started_at: str = field(default_factory=_now)
    finished_at: str | None = None
    exit_code: int | None = None
    log: list[str] = field(default_factory=list)
    agent_id: str | None = None     # какой агент инициировал
    target_kind: str | None = None  # make | script
    make_target: str | None = None  # имя make-цели (для сопоставления со стадией)

    def as_dict(self) -> dict:
        d = asdict(self)
        # лог в /api/jobs не нужен — он большой, отдаётся через /api/build/<id>
        d["log"] = self.log[-20:]
        return d


JOBS: dict[str, Job] = {}
_JOB_LOCK = threading.Lock()


# --------------------------------------------------------------------------
# Агентный рантайм (ленивый импорт: agent_runtime импортирует этот модуль)
# --------------------------------------------------------------------------
def _rt():
    try:
        import agent_runtime
        return agent_runtime
    except Exception:                                           # noqa: BLE001
        return None


def _runtime_roster() -> dict:
    """Локальные агенты + их роль в баureau-ростере + доступные цели."""
    rt = _rt()
    if not rt:
        return {"ok": False, "reason": "agent_runtime unavailable",
                "agents": parse_agents()}
    out = []
    for a in rt.local_agents():
        role, role_title, desig = rt.ROLE_MAP.get(
            a.get("id", ""), ("analyst", "Project Agent", "Исполнитель"))
        out.append({
            "id": a.get("id"),
            "codename": rt.agent_codename(a.get("id", "")),
            "title": a.get("title", ""),
            "goal": a.get("goal", ""),
            "files": a.get("files", ""),
            "command": a.get("command", ""),
            "role_key": role,
            "role_title": role_title,
            "designation": desig,
            "channel": rt.CHANNEL_BY_ROLE.get(role, "command"),
        })
    return {"ok": True, "count": len(out), "agents": out}


def _runtime_context() -> str:
    rt = _rt()
    return rt._project_context() if rt else ""


def _agents():
    """Пакет агентно-функционального графа (backend/agents)."""
    try:
        from agents import service
        return service
    except Exception:                                           # noqa: BLE001
        return None


def _llm():
    """Слой доступа к LLM: провайдеры, выключатель, статус."""
    try:
        import llm
        return llm
    except Exception:                                           # noqa: BLE001
        return None


def _project_root() -> str:
    """Корень проекта, которым управляет граф."""
    return os.environ.get("KR_PROJECT_ROOT", str(ROOT))


def _cfg():
    """krconfig — словари предметной области для интерфейса."""
    try:
        import krconfig
        return krconfig.config_payload()
    except Exception:                                           # noqa: BLE001
        return {"error": "krconfig unavailable"}


def _zone(health: dict) -> dict:
    import krconfig
    return krconfig.zone_telemetry(health, chain_status(), _last_tests(),
                                   collect_metrics())


def _order_logs(order) -> dict:
    """Лог каждого шага заказа — из джоб моста.

    Шаги графа выполняются напрямую (без джоб), поэтому джобы есть
    только у заказов, запущенных напрямую через `/api/agent/run`.
    Для графовых шагов лог лежит в самом шаге.
    """
    out = {}
    steps = order.get("steps", []) if isinstance(order, dict) else order.steps
    for st in steps:
        d = st if isinstance(st, dict) else st.as_dict()
        if d.get("job_id"):
            job = JOBS.get(d["job_id"])
            if job:
                out[d["id"]] = {"status": job.status, "log": job.log[-40:]}
                continue
        if d.get("log_tail"):
            out[d["id"]] = {"status": d.get("status", "?"),
                            "log": d["log_tail"]}
    return out


def _spec_for(code: str):
    import krconfig
    return krconfig.product_specs_for(code)


#: Кэш последнего прогона тестов — чтобы /api/zone не запускал pytest
#: на каждом опросе фронтенда (иначе дашборд грузит CPU).
_TESTS_CACHE: dict = {"at": 0.0, "data": None}
_TESTS_TTL = 300.0      # секунд


def _last_tests(force: bool = False) -> dict | None:
    global _TESTS_CACHE
    now = time.monotonic()
    if (not force and _TESTS_CACHE["data"] is not None
            and now - _TESTS_CACHE["at"] < _TESTS_TTL):
        return _TESTS_CACHE["data"]
    data = run_tests()
    _TESTS_CACHE = {"at": now, "data": data}
    return data


def _snapshot() -> dict:
    """Всё для дашборда одним ответом (для polling вместо 6 fetch'ей)."""
    o = out_dir()
    health = {
        "ok": True, "root": str(ROOT), "time": _now(),
        "artifacts": len(list_artifacts()),
        "has_page": (o / "krystal-radio.html").exists(),
        "has_waterfall": (o / "waterfall.png").exists(),
        "has_glb": (o / "board.glb").exists(),
        "jobs": len(JOBS),
    }
    chain = chain_status()
    tests = _last_tests()
    metrics = collect_metrics()
    projs = projects_list()

    specs = {}
    for p in projs.get("projects", []):
        s = _spec_for(p.get("code", ""))
        if s:
            specs[p["code"]] = s

    return {
        "health": health,
        "chain": chain,
        "zone": __import__("krconfig").zone_telemetry(health, chain, tests, metrics),
        "metrics": metrics,
        "projects": projs,
        "specs": specs,
        "tests": tests,
        "jobs": [{"id": j.id, "target": j.target, "status": j.status,
                  "agent_id": j.agent_id, "exit_code": j.exit_code,
                  "started_at": j.started_at}
                 for j in sorted(JOBS.values(),
                                 key=lambda x: x.started_at,
                                 reverse=True)[:10]],
        "updated": _now(),
    }


def _runtime_ask(prompt: str) -> dict:
    rt = _rt()
    if not rt:
        return {"error": "agent_runtime unavailable"}
    import krsupabase
    return rt.answer_directive(prompt, sb=krsupabase.sb())


def run_job(job: Job, argv: list[str], cwd: Path, timeout: int = 3600) -> None:
    """Выполнить команду, писать лог построчно, обновить статус."""
    job.status = "running"
    job.started_at = _now()
    try:
        proc = subprocess.Popen(
            argv, cwd=str(cwd), stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, bufsize=1,
        )
    except Exception as exc:                                  # noqa: BLE001
        job.status = "failed"
        job.exit_code = -1
        job.log.append(f"spawn error: {exc}")
        job.finished_at = _now()
        return

    start = time.monotonic()
    assert proc.stdout is not None
    for line in proc.stdout:
        line = line.rstrip("\n")
        job.log.append(line)
        # держим лог ограниченным
        if len(job.log) > 4000:
            del job.log[:1000]
        if time.monotonic() - start > timeout:
            proc.kill()
            job.log.append(f"!! timeout {timeout}s — процесс убит")
            job.status = "failed"
            job.exit_code = -9
            job.finished_at = _now()
            return
    proc.wait()
    job.exit_code = proc.returncode
    job.status = "done" if proc.returncode == 0 else "failed"
    job.finished_at = _now()


def spawn_job(target: str, argv: list[str]) -> Job:
    with _JOB_LOCK:
        job = Job(id=uuid.uuid4().hex[:12], target=target)
        JOBS[job.id] = job
    threading.Thread(target=run_job, args=(job, argv, ROOT), daemon=True).start()
    return job


# --------------------------------------------------------------------------
# Сбор данных
# --------------------------------------------------------------------------
MEDIA_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".htm": "text/html; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".gif": "image/gif",
    ".mp4": "video/mp4",
    ".glb": "model/gltf-binary",
    ".step": "model/step",
    ".stp": "model/step",
    ".csv": "text/csv; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".md": "text/markdown; charset=utf-8",
    ".zip": "application/zip",
    ".nec": "text/plain; charset=utf-8",
    ".rpt": "text/plain; charset=utf-8",
    ".dxf": "application/dxf",
    ".pdf": "application/pdf",
}


def list_artifacts(root: Path | None = None) -> list[dict]:
    """Рекурсивный список артефактов в out/ (без node_modules/venv)."""
    out: list[dict] = []
    root = root or out_dir()
    if not root.exists():
        return out
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        st = p.stat()
        rel = str(p.relative_to(ROOT))
        out.append({
            "name": p.name,
            "path": rel,
            "rel": str(p.relative_to(root)),
            "size": st.st_size,
            "size_h": human_size(st.st_size),
            "ext": p.suffix.lower(),
            "mime": MEDIA_TYPES.get(p.suffix.lower(), "application/octet-stream"),
            "modified": datetime.fromtimestamp(
                st.st_mtime, timezone.utc).isoformat(timespec="seconds"),
            "url": f"/api/artifact/{rel}",
        })
    return out


def human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} GB"


def load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:                                           # noqa: BLE001
        return {}


def collect_metrics() -> dict:
    """Сводка по физике и проверкам — то, что агенты чаще всего спрашивают."""
    o = out_dir()
    lb = load_json(o / "link_budget.json")
    df = load_json(o / "direction_finding.json")
    th_fast = load_json(o / "thermal_fast.json")
    th_fem = load_json(o / "thermal_fem.json")
    rel = load_json(o / "reliability.json")
    spec = load_json(o / "virtual_rx_spectrum.json")
    encl = load_json(o / "enclosure.json")

    # thermal_fast.json имеет вложенность {"board":…, "ngspice":…, "fast":{…}}
    fast = th_fast.get("fast", th_fast)
    devices = fast.get("devices", [])
    tj_now = max((d.get("tj_c", 0.0) for d in devices), default=None)
    tj_limit = min((d.get("tj_max_c", 1e9) for d in devices), default=None)
    fem = th_fem.get("fem", th_fem)
    tmax = fem.get("tmax_c") or fem.get("tmax")

    channels = [
        {
            "key": c.get("key"),
            "title": c.get("title"),
            "f_mhz": c.get("f_mhz"),
            "g_rx_dbi": c.get("g_rx_dbi"),
            "range_free_km": round(c.get("range_free_space_m", 0) / 1000, 1),
            "range_real_km": round(c.get("range_realistic_m", 0) / 1000, 1),
        }
        for c in lb.get("channels", [])
    ]

    return {
        "link_budget": {
            "noise_floor_dbm": lb.get("noise", {}).get("noise_floor_calc_dbm"),
            "mds_dbm": lb.get("noise", {}).get("sensitivity_calc_dbm"),
            "eirp_dbm": lb.get("reference", {}).get("eirp_dbm"),
            "bw_hz": lb.get("reference", {}).get("bw_hz"),
            "nf_db": lb.get("reference", {}).get("nf_db"),
            "channels": channels,
        },
        "direction": {
            "theta_est_deg": df.get("theta_est_deg"),
            "sigma_theta_deg": df.get("sigma_theta_deg"),
            "fov_deg": df.get("fov_deg"),
            "lambda_mm": round(df.get("lambda_m", 0) * 1000, 1),
            "baseline_mm": round(df.get("baseline_half_wave_m", 0) * 1000, 1),
            "backend": df.get("backend"),
        },
        "thermal": {
            "tj_c": tj_now,
            "tj_limit_c": tj_limit,
            "tmax_fem_c": tmax,
            "devices": [
                {"ref": d.get("ref"), "p_w": d.get("p_w"),
                 "tj_c": d.get("tj_c"), "tj_max_c": d.get("tj_max_c"),
                 "margin_c": d.get("margin_c"), "pass": d.get("pass_")
                 or d.get("pass")}
                for d in devices
            ],
            "enclosure": encl.get("shielding") if encl else None,
            "case_volume_cm3": encl.get("volume_cm3") if encl else None,
        },
        "reliability": {
            "mtbf_h": rel.get("system", {}).get("mtbf_h") or rel.get("MTBF_h"),
            "req_min_h": rel.get("requirement", {}).get("min_mtbf_h", 10_000),
            "pass": rel.get("requirement", {}).get("pass"),
            "raw": rel,
        },
        "receiver": spec,
        "emc": {
            "test_file": "tests/requirements/emc/test_emc.py",
            "expected": 16,
            "note": "REQ-21..25: shielding, immunity, emission, filters, zoning",
        },
    }


# --------------------------------------------------------------------------
# Парсер AGENT_PROMPTS.md -> список агентов
#
# Формат блока в AGENT_PROMPTS.md:
#     - **ID**: `AGENT-MK-01`
#     - **Название**: Запустить `make all` и проверить RC=0
#     - **Цель**: Убедиться, что все этапы проходят.
#     - **Ключевые файлы**: `Makefile`, `tools/build_page.py`
#     - **Команда**: `cd ... && make all`
#
# Разбор построчный: заголовок `## N.` начинает раздел, `## ` без номера —
# конец документа. Значения могут переноситься на следующие строки.
# --------------------------------------------------------------------------
ID_RE = re.compile(r"^-\s*\*\*ID\*\*:\s*`([^`]+)`\s*$")
FIELD_RE = re.compile(r"^-\s*\*\*(.+?)\*\*:\s*(.*)$")
SECTION_RE = re.compile(r"^##\s+\d")


def parse_agents(path: Path | None = None) -> list[dict]:
    """Разобрать AGENT_PROMPTS.md -> [{id, title, goal, files, command}]."""
    path = path or (ROOT / "AGENT_PROMPTS.md")
    if not path.exists():
        return []

    agents: list[dict] = []
    cur: dict | None = None
    key: str | None = None

    def flush() -> None:
        nonlocal cur, key
        if cur and cur.get("id"):
            agents.append(cur)
        cur, key = None, None

    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        # новый раздел -> наш блок мог остаться незакрытым
        if line.startswith("## "):
            flush()
            continue
        m_id = ID_RE.match(line.strip())
        if m_id:
            flush()
            cur = {"id": m_id.group(1), "title": "", "goal": "",
                   "files": "", "command": ""}
            key = None
            continue
        if cur is None:
            continue
        m_f = FIELD_RE.match(line.strip())
        if m_f:
            key = m_f.group(1)
            val = m_f.group(2).strip()
            if key == "ID":
                cur["id"] = val.strip("`")
            elif key in ("Название", "Цель", "Ключевые файлы", "Команда"):
                slot = {"Название": "title", "Цель": "goal",
                        "Ключевые файлы": "files",
                        "Команда": "command"}[key]
                cur[slot] = val
            else:
                key = None
        elif key:
            # продолжение значения (перенос строки внутри описания)
            slot = {"Название": "title", "Цель": "goal",
                    "Ключевые файлы": "files",
                    "Команда": "command"}.get(key)
            if slot:
                cur[slot] = (cur[slot] + " " + line.strip()).strip()
    flush()

    for a in agents:
        a["goal"] = re.sub(r"\s+", " ", a["goal"]).strip()
        a["title"] = re.sub(r"\s+", " ", a["title"]).strip()
    return agents


def parse_enclosure_agent() -> dict | None:
    """AGENT_ENCLOSURE.md — корпус (FreeCAD)."""
    path = ROOT / "AGENT_ENCLOSURE.md"
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8")
    title = re.search(r"##\s*Название:\s*(.+)", text)
    goal = re.search(r"##\s*Цель:\s*(.+)", text)
    files = re.search(r"##\s*Ключевые файлы\n(.*?)(?=\n##\s)", text, re.S)
    return {
        "id": "AGENT-ENC-01",
        "title": title.group(1).strip() if title else "Проектирование корпуса",
        "goal": goal.group(1).strip() if goal else "",
        "files": files.group(1).strip() if files else "",
        "cad": "FreeCAD",
        "outputs": [
            "hardware/enclosure/krystal-case.FCStd",
            "hardware/enclosure/krystal-case.step",
            "hardware/enclosure/krystal-case.stl",
        ],
    }


# --------------------------------------------------------------------------
# Тесты
# --------------------------------------------------------------------------
def run_tests() -> dict:
    argv = ["pytest", "tests/requirements", "-q", "--no-header", "-p", "no:cacheprovider"]
    try:
        proc = subprocess.run(argv, cwd=str(ROOT), capture_output=True,
                              text=True, timeout=900)
    except Exception as exc:                                   # noqa: BLE001
        return {"ok": False, "error": str(exc)}
    tail = proc.stdout.strip().splitlines()[-12:]
    m = re.search(r"(\d+)\s+passed", proc.stdout)
    m2 = re.search(r"(\d+)\s+failed", proc.stdout)
    return {
        "ok": proc.returncode == 0,
        "exit_code": proc.returncode,
        "passed": int(m.group(1)) if m else 0,
        "failed": int(m2.group(1)) if m2 else 0,
        "tail": tail,
    }


# --------------------------------------------------------------------------
# HTTP-обработчик
# --------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    server_version = "KrystalAgentBridge/1.0"

    # -- утилиты -----------------------------------------------------------
    def _send(self, code: int, body: bytes, ctype: str = "application/json",
              cache: bool = False) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers",
                         "Authorization, Content-Type")
        self.send_header("Access-Control-Allow-Methods",
                         "GET, POST, OPTIONS")
        if cache:
            self.send_header("Cache-Control", "public, max-age=300")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, obj, code: int = 200) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False,
                                    default=str).encode("utf-8"))

    def _auth_ok(self) -> bool:
        if not TOKEN:
            return True
        hdr = self.headers.get("Authorization", "")
        return hdr == f"Bearer {TOKEN}"

    def log_message(self, fmt: str, *args) -> None:            # noqa: A003
        print(f"[bridge] {self.address_string()} {fmt % args}")

    # -- маршруты ----------------------------------------------------------
    def do_OPTIONS(self) -> None:                              # noqa: N802
        self._send(204, b"", "text/plain")

    def do_GET(self) -> None:                                   # noqa: N802
        if not self._auth_ok():
            return self._json({"error": "unauthorized"}, 401)
        u = urlparse(self.path)
        path = u.path.rstrip("/") or "/"
        q = parse_qs(u.query)

        if path == "/api/health":
            arts = list_artifacts()
            o = out_dir()
            return self._json({
                "ok": True,
                "root": str(ROOT),
                "time": _now(),
                "artifacts": len(arts),
                "has_page": (o / "krystal-radio.html").exists(),
                "has_waterfall": (o / "waterfall.png").exists(),
                "has_glb": (o / "board.glb").exists(),
                "jobs": len(JOBS),
            })

        if path == "/api/artifacts":
            arts = list_artifacts()
            if "ext" in q:
                wanted = {e.lower() for e in q["ext"][0].split(",")}
                arts = [a for a in arts if a["ext"] in wanted]
            return self._json({"count": len(arts), "artifacts": arts})

        if path.startswith("/api/artifact/"):
            return self._serve_artifact(path[len("/api/artifact/"):])

        if path == "/api/agents":
            agents = parse_agents()
            enc = parse_enclosure_agent()
            if enc:
                agents.append(enc)
            return self._json({"count": len(agents), "agents": agents})

        if path.startswith("/api/agent/"):
            aid = path[len("/api/agent/"):]
            pool = parse_agents()
            enc = parse_enclosure_agent()
            if enc:
                pool.append(enc)
            for a in pool:
                if a["id"] == aid:
                    return self._json(a)
            return self._json({"error": "unknown agent", "id": aid}, 404)

        if path == "/api/targets":
            return self._json({
                "targets": [{"target": k, "desc": v}
                            for k, v in ALLOWED_TARGETS.items()],
                "scripts": [{"script": k, "path": v[0], "desc": v[1]}
                            for k, v in ALLOWED_SCRIPTS.items()],
            })

        if path == "/api/metrics":
            return self._json(collect_metrics())

        if path == "/api/runtime/agents":
            return self._json(_runtime_roster())

        if path == "/api/runtime/context":
            return self._json({"context": _runtime_context()})

        if path == "/api/chain":
            return self._json(chain_status())

        if path == "/api/config":
            return self._json(_cfg())

        if path == "/api/zone":
            o = out_dir()
            h = {"has_page": (o / "krystal-radio.html").exists(),
                 "has_waterfall": (o / "waterfall.png").exists(),
                 "has_glb": (o / "board.glb").exists()}
            return self._json(_zone(h))

        if path == "/api/snapshot":
            # Один запрос вместо нескольких: фронтенд опрашивает это
            # вместо цепочки fetch'ей, поэтому здесь всё, что нужно
            # дашборду верхнего уровня.
            return self._json(_snapshot())

        if path.startswith("/api/specs/"):
            code = path[len("/api/specs/"):]
            spec = _spec_for(code)
            if spec is None:
                return self._json({"error": "unknown project code",
                                   "code": code}, 404)
            return self._json({"code": code, **spec})

        if path == "/api/projects":
            return self._json(projects_list())

        if path == "/api/order/capabilities":
            svc = _agents()
            if not svc:
                return self._json({"error": "agents unavailable"}, 503)
            return self._json(svc.capabilities(root=_project_root()))

        if path == "/api/graph":
            """Узлы агентно-функционального графа.

            Отдельный путь, а не /api/agents: там живут агенты Бюро
            из AGENT_PROMPTS.md, здесь — функции-узлы движка.
            """
            svc = _agents()
            if not svc:
                return self._json({"error": "agents unavailable"}, 503)
            return self._json({"nodes": svc.capabilities(
                root=_project_root())["agents"]})

        if path == "/api/llm":
            """Настройки LLM: выключатель, цепочка провайдеров, живость."""
            mod = _llm()
            if not mod:
                return self._json({"error": "llm module unavailable"}, 503)
            live = q.get("probe", ["0"])[0] == "1"
            return self._json(mod.status(probe_live=live))

        if path == "/api/orders":
            svc = _agents()
            if not svc:
                return self._json({"error": "agents unavailable"}, 503)
            return self._json({"orders": svc.list_orders(
                int(q.get("limit", ["20"])[0]))})

        if path == "/api/orders/waiting":
            """Заказы, на которых граф стоит и ждёт решения оператора."""
            svc = _agents()
            if not svc:
                return self._json({"error": "agents unavailable"}, 503)
            return self._json({"waiting": svc.waiting()})

        if path.endswith("/stream"):
            # Поток событий заказа. Опрос каждые 1.2 с дал бы тот же
            # результат, но с задержкой в цикл и без возможности отдать
            # журнал построчно.
            svc = _agents()
            if not svc:
                return self._json({"error": "agents unavailable"}, 503)
            oid = path[len("/api/order/"):-len("/stream")]
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            try:
                for event, data in svc.stream(oid):
                    payload = (json.dumps(data, ensure_ascii=False,
                                          default=str) + "\n\n")
                    chunk = f"event: {event}\ndata: {payload}"
                    self.wfile.write(chunk.encode("utf-8"))
                    self.wfile.flush()
                    if event == "done":
                        break
            except (BrokenPipeError, ConnectionResetError):
                pass          # вкладка закрыта — просто перестаём писать
            return

        if path.startswith("/api/order/"):
            svc = _agents()
            if not svc:
                return self._json({"error": "agents unavailable"}, 503)
            oid = path[len("/api/order/"):]
            order = svc.get(oid)
            if order is None:
                return self._json({"error": "unknown order", "id": oid}, 404)
            if q.get("log", ["0"])[0] == "1":
                order["step_logs"] = _order_logs(order)
            return self._json(order)

        if path == "/api/tests":
            return self._json(_last_tests(force=True))

        if path.startswith("/api/build/"):
            jid = path[len("/api/build/"):]
            job = JOBS.get(jid)
            if not job:
                return self._json({"error": "unknown job", "id": jid}, 404)
            since = int(q.get("since", ["0"])[0])
            d = job.as_dict()
            d["log"] = job.log[since:]
            d["log_offset"] = since
            return self._json(d)

        if path == "/api/jobs":
            return self._json({"jobs": [j.as_dict() for j in
                                        sorted(JOBS.values(),
                                               key=lambda j: j.started_at,
                                               reverse=True)[:30]]})

        if path == "/":
            return self._json({
                "service": "krystal-radio agent bridge",
                "endpoints": ["/api/health", "/api/artifacts", "/api/artifact/<path>",
                              "/api/agents", "/api/agent/<id>", "/api/targets",
                              "/api/metrics", "/api/tests", "/api/jobs",
                              "/api/build/<job_id>",
                              "POST /api/build {target}",
                              "POST /api/run {script,args}"],
            })
        return self._json({"error": "not found", "path": path}, 404)

    def do_POST(self) -> None:                                  # noqa: N802
        # MCP живёт вне /api и вне проверки ключа: к нему обращаются
        # локальные агенты (OpenCode, Claude Code, Codex), у которых
        # своя авторизация. Мост и так слушает только 127.0.0.1.
        if urlparse(self.path).path.rstrip("/") in ("/mcp", ""):
            try:
                import mcp
            except ImportError:
                return self._json({"error": "mcp module unavailable"}, 503)
            n = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(n) if n else b""
            status, headers, data = mcp.handle_http(
                urlparse(self.path).path.rstrip("/") or "/", body,
                {"post": self.command, "content-type":
                 self.headers.get("Content-Type", "")})
            self.send_response(status)
            for k, v in headers.items():
                self.send_header(k, v)
            self.end_headers()
            if data:
                self.wfile.write(data)
            return

        if not self._auth_ok():
            return self._json({"error": "unauthorized"}, 401)
        u = urlparse(self.path)
        path = u.path.rstrip("/") or "/"
        n = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            return self._json({"error": "bad json"}, 400)

        if path == "/api/build":
            target = str(payload.get("target", "")).strip()
            if target not in ALLOWED_TARGETS:
                return self._json({
                    "error": "target not allowed",
                    "allowed": list(ALLOWED_TARGETS)}, 400)
            job = spawn_job(f"make {target}", ["make", target])
            job.make_target = target
            job.target_kind = "make"
            return self._json(job.as_dict(), 202)

        if path == "/api/run":
            script = str(payload.get("script", "")).strip()
            if script not in ALLOWED_SCRIPTS:
                return self._json({
                    "error": "script not allowed",
                    "allowed": list(ALLOWED_SCRIPTS)}, 400)
            rel, _desc = ALLOWED_SCRIPTS[script]
            argv = [sys.executable, rel]
            argv += [str(a) for a in payload.get("args", [])]
            job = spawn_job(rel, argv)
            return self._json(job.as_dict(), 202)

        if path == "/api/projects":
            return self._json(project_create(payload), 201)

        if path == "/api/order":
            text = str(payload.get("text", "")).strip()
            if not text:
                return self._json({"error": "text required"}, 400)
            svc = _agents()
            if not svc:
                return self._json({"error": "agents unavailable"}, 503)
            order = svc.submit(text, root=str(payload.get("root")
                                              or _project_root()),
                               project_id=payload.get("project_id"))
            return self._json(order, 202)

        if path == "/api/order/plan":
            """Предпросмотр: разобрать заказ в цели, ничего не запуская."""
            text = str(payload.get("text", "")).strip()
            if not text:
                return self._json({"error": "text required"}, 400)
            svc = _agents()
            if not svc:
                return self._json({"error": "agents unavailable"}, 503)
            return self._json(svc.plan_only(text, root=_project_root()))

        if path.startswith("/api/order/") and path.endswith("/resume"):
            """Продолжить прерванный заказ после ответа оператора."""
            svc = _agents()
            if not svc:
                return self._json({"error": "agents unavailable"}, 503)
            oid = path[len("/api/order/"):-len("/resume")]
            answer = str(payload.get("answer", "")).strip()
            if not answer:
                return self._json({"error": "answer required"}, 400)
            return self._json(svc.resume(oid, answer, root=_project_root()))

        if path.endswith("/cancel"):
            svc = _agents()
            if not svc:
                return self._json({"error": "agents unavailable"}, 503)
            oid = path[len("/api/order/"):-len("/cancel")]
            return self._json({"cancelled": svc.cancel(oid), "id": oid})

        if path == "/api/agent/run":
            aid = str(payload.get("agent_id", "")).strip()
            target = str(payload.get("target", "")).strip()
            if target not in ALLOWED_TARGETS:
                return self._json({"error": "target not allowed",
                                   "allowed": list(ALLOWED_TARGETS)}, 400)
            if not aid:
                return self._json({"error": "agent_id required"}, 400)
            job = spawn_job(f"{aid}: make {target}", ["make", target])
            job.agent_id = aid
            job.target_kind = "make"
            job.make_target = target
            return self._json(job.as_dict(), 202)

        if path == "/api/agent/script":
            aid = str(payload.get("agent_id", "")).strip()
            script = str(payload.get("script", "")).strip()
            if script not in ALLOWED_SCRIPTS:
                return self._json({"error": "script not allowed",
                                   "allowed": list(ALLOWED_SCRIPTS)}, 400)
            rel, _d = ALLOWED_SCRIPTS[script]
            argv = [sys.executable, rel] + [
                str(a) for a in payload.get("args", [])]
            job = spawn_job(f"{aid}: {rel}", argv)
            job.agent_id = aid
            job.target_kind = "script"
            return self._json(job.as_dict(), 202)

        if path == "/api/ask":
            prompt = str(payload.get("prompt", "")).strip()
            if not prompt:
                return self._json({"error": "prompt required"}, 400)
            ans = _runtime_ask(prompt)
            return self._json(ans, 200)

        if path == "/api/prompt":
            aid = str(payload.get("agent_id", ""))
            for a in parse_agents():
                if a["id"] == aid:
                    goal = a["goal"]
                    cmd = re.sub(r"`[^`]*`", "", a["command"]).strip()
                    return self._json({
                        "agent_id": aid,
                        "prompt": (
                            f"Ты — агент {a['id']}: {a['title']}.\n"
                            f"Цель: {goal}\n"
                            f"Файлы: {a['files']}\n"
                            f"Выполни команду и верни результат в отчёт:\n{cmd}\n"
                            "Не изменяй файлы вне указанной области."
                        ),
                    })
            return self._json({"error": "unknown agent", "id": aid}, 404)

        return self._json({"error": "not found", "path": path}, 404)

    # -- отдача файла ------------------------------------------------------
    def _serve_artifact(self, rel: str) -> None:
        rel = unquote_path(rel)
        target = (ROOT / rel).resolve()
        try:
            target.relative_to(ROOT)
        except ValueError:                                        # path traversal
            return self._json({"error": "forbidden"}, 403)
        if not target.is_file():
            return self._json({"error": "not found", "path": rel}, 404)
        ctype = MEDIA_TYPES.get(target.suffix.lower(), "application/octet-stream")
        self._send(200, target.read_bytes(), ctype, cache=True)


def unquote_path(p: str) -> str:
    from urllib.parse import unquote
    return unquote(p)


def main() -> None:
    ap = argparse.ArgumentParser(description="Krystal Radio agent bridge")
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--root", default=None)
    args = ap.parse_args()
    if args.root:
        globals()["ROOT"] = Path(args.root).resolve()
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"[bridge] krystal-radio agent bridge on "
          f"http://{args.host}:{args.port}  root={ROOT}")
    print(f"[bridge] targets: {', '.join(ALLOWED_TARGETS)}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n[bridge] stopped")


if __name__ == "__main__":
    main()