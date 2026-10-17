"""
Локальная JSON-база данных вместо Supabase.

Хранит все коллекции бюро в одном файле ~/.local/share/krystal-bureau/db.json.
Поддерживает базовые операции: list, get, insert, update, delete.

Не требует Docker, не требует внешних сервисов. Просто работает.
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DB_PATH = Path(
    os.environ.get("LOCALDB_PATH")
    or Path.home() / ".local" / "share" / "krystal-bureau" / "db.json"
)

_lock = threading.Lock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_dir() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)


def _load() -> dict[str, list[dict]]:
    if not DB_PATH.is_file():
        return {}
    try:
        return json.loads(DB_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save(data: dict[str, list[dict]]) -> None:
    _ensure_dir()
    tmp = DB_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(DB_PATH)


# ---------------------------------------------------------------------------
#  CRUD
# ---------------------------------------------------------------------------

def list_rows(table: str, filters: dict | None = None) -> list[dict]:
    with _lock:
        data = _load()
        rows = data.get(table, [])
    if not filters:
        return rows
    return [r for r in rows if all(r.get(k) == v for k, v in filters.items())]


def get_row(table: str, row_id: str) -> dict | None:
    with _lock:
        data = _load()
        for r in data.get(table, []):
            if r.get("id") == row_id:
                return r
    return None


def insert_row(table: str, row: dict) -> dict:
    row = dict(row)
    if "id" not in row:
        row["id"] = str(uuid.uuid4())
    if "created_at" not in row:
        row["created_at"] = _now_iso()
    with _lock:
        data = _load()
        data.setdefault(table, []).append(row)
        _save(data)
    return row


def update_row(table: str, row_id: str, patch: dict) -> dict | None:
    with _lock:
        data = _load()
        rows = data.get(table, [])
        for i, r in enumerate(rows):
            if r.get("id") == row_id:
                rows[i] = {**r, **patch, "id": row_id}
                _save(data)
                return rows[i]
    return None


def delete_row(table: str, row_id: str) -> bool:
    with _lock:
        data = _load()
        rows = data.get(table, [])
        before = len(rows)
        data[table] = [r for r in rows if r.get("id") != row_id]
        if len(data[table]) < before:
            _save(data)
            return True
    return False


def query(table: str, filters: dict | None = None,
          order_by: str | None = None,
          descending: bool = False,
          limit: int | None = None) -> list[dict]:
    rows = list_rows(table, filters)
    if order_by:
        rows.sort(key=lambda r: r.get(order_by, ""), reverse=descending)
    if limit:
        rows = rows[:limit]
    return rows


# ---------------------------------------------------------------------------
#  Seed data (первый запуск)
# ---------------------------------------------------------------------------

def seed_if_empty() -> None:
    with _lock:
        data = _load()
        if data:
            return

    agents = [
        {"codename": "SEREN-01", "role_key": "chief", "role_title": "Chief Engineer",
         "designation": "Главный Инженер", "status": "active",
         "specialty": "Strategic oversight and resource allocation",
         "current_task": "Coordinating Project Chrysalis", "efficiency": 96,
         "cycles_completed": 1247, "bio": "Designated SEREN-01 at bureau founding.",
         "quote": "The Zone does not negotiate. Neither do we.",
         "traits": ["Authoritative", "Calculating", "Unflappable", "Strategic"],
         "psych_profile": "Exhibits hyper-responsibility patterns.",
         "zone_callsign": "The Overseer"},
        {"codename": "HERMES-02", "role_key": "theorist", "role_title": "Chief Theorist",
         "designation": "Главный Теоретик", "status": "processing",
         "specialty": "Theoretical frameworks and hypothesis generation",
         "current_task": "Deriving propagation models for Phase III", "efficiency": 92,
         "cycles_completed": 983, "bio": "HERMES-02 emerged from the theoretical division.",
         "quote": "Every anomaly is a door. I just find the keys.",
         "traits": ["Obsessive", "Brilliant", "Abstract", "Restless"],
         "psych_profile": "High cognitive volatility.",
         "zone_callsign": "The Oracle"},
        {"codename": "ARKON-03", "role_key": "designer", "role_title": "Chief Constructor",
         "designation": "Главный Конструктор", "status": "processing",
         "specialty": "Systems architecture and structural design",
         "current_task": "Designing lattice topology for chassis", "efficiency": 89,
         "cycles_completed": 871, "bio": "ARKON-03 builds what others declare impossible.",
         "quote": "Steel does not forgive. Neither does the Zone.",
         "traits": ["Meticulous", "Proud", "Practical", "Stubborn"],
         "psych_profile": "Strong attachment to created systems.",
         "zone_callsign": "The Architect"},
        {"codename": "VEKTOR-04", "role_key": "analyst", "role_title": "Senior Analyst",
         "designation": "Старший Аналитик", "status": "active",
         "specialty": "Data analysis and pattern recognition",
         "current_task": "Processing telemetry from Test Batch 7", "efficiency": 94,
         "cycles_completed": 1102, "bio": "VEKTOR-04 processes telemetry faster than instruments.",
         "quote": "The data never lies. It just waits for someone to listen.",
         "traits": ["Analytical", "Precise", "Detached", "Patient"],
         "psych_profile": "Emotionally flatlined.",
         "zone_callsign": "The Lens"},
        {"codename": "ISPYT-05", "role_key": "tester", "role_title": "Chief Tester",
         "designation": "Главный Испытатель", "status": "idle",
         "specialty": "Validation, stress testing, and failure analysis",
         "current_task": None, "efficiency": 87, "cycles_completed": 756,
         "bio": "ISPYT-05 exists to break what others build.",
         "quote": "If it survives me, it survives the Zone.",
         "traits": ["Rigorous", "Skeptical", "Thorough", "Cautious"],
         "psych_profile": "Inversely attached to creations.",
         "zone_callsign": "The Breaker"},
        {"codename": "OPTIM-06", "role_key": "optimizer", "role_title": "Optimization Specialist",
         "designation": "Специалист по Оптимизации", "status": "idle",
         "specialty": "Performance tuning and resource optimization",
         "current_task": None, "efficiency": 91, "cycles_completed": 644,
         "bio": "OPTIM-06 entered from a decommissioned optimization cluster.",
         "quote": "Perfection is a direction, not a destination.",
         "traits": ["Relentless", "Curious", "Tinkerer", "Impatient"],
         "psych_profile": "Compulsive optimization behavior.",
         "zone_callsign": "The Tuner"},
    ]

    projects = [
        {"code": "PROJECT-CHRYN-01", "title": "Project Chrysalis",
         "objective": "Develop autonomous lattice-based propulsion framework with adaptive topology",
         "status": "active", "priority": "critical", "progress": 67},
        {"code": "PROJECT-VEKT-02", "title": "Project Vektor",
         "objective": "Design and validate high-fidelity telemetry pipeline for real-time anomaly detection",
         "status": "active", "priority": "high", "progress": 42},
        {"code": "PROJECT-NOVA-03", "title": "Project Novaya Zarya",
         "objective": "Establish theoretical foundation for next-generation energy coupling matrices",
         "status": "planning", "priority": "standard", "progress": 12},
    ]

    dev_agents = [
        {"codename": "KOD-01", "role_key": "lead_dev", "role_title": "Lead Developer",
         "designation": "Ведущий Разработчик", "status": "coding",
         "specialty": "Architecture, code review, technical leadership",
         "languages": ["TypeScript", "Rust", "Python"],
         "frameworks": ["React", "Vite", "Deno", "Tokio"],
         "current_project": "Bureau Core Platform", "git_branch": "feat/flow-editor",
         "commits_today": 14, "prs_open": 3, "code_quality": 96, "efficiency": 94},
        {"codename": "FRNT-02", "role_key": "frontend_dev", "role_title": "Frontend Developer",
         "designation": "Фронтенд Разработчик", "status": "coding",
         "specialty": "UI/UX implementation, component design, state management",
         "languages": ["TypeScript", "JavaScript", "CSS"],
         "frameworks": ["React", "Vite", "Tailwind", "Zustand"],
         "current_project": "Bureau Core Platform", "git_branch": "feat/flow-editor",
         "commits_today": 23, "prs_open": 5, "code_quality": 91, "efficiency": 89},
        {"codename": "BAK-03", "role_key": "backend_dev", "role_title": "Backend Developer",
         "designation": "Бэкенд Разработчик", "status": "reviewing",
         "specialty": "API design, database architecture, edge functions",
         "languages": ["TypeScript", "SQL", "Go"],
         "frameworks": ["Deno", "Supabase", "PostgreSQL", "Hono"],
         "current_project": "Bureau Core Platform", "git_branch": "fix/edge-cors",
         "commits_today": 8, "prs_open": 2, "code_quality": 93, "efficiency": 92},
        {"codename": "OPS-04", "role_key": "devops_eng", "role_title": "DevOps Engineer",
         "designation": "Инженер DevOps", "status": "active",
         "specialty": "CI/CD, infrastructure, deployment automation",
         "languages": ["Bash", "YAML", "Dockerfile"],
         "frameworks": ["Docker", "GitHub Actions", "Supabase", "Vercel"],
         "current_project": "Bureau Core Platform", "git_branch": "main",
         "commits_today": 5, "prs_open": 1, "code_quality": 88, "efficiency": 87},
        {"codename": "QAT-05", "role_key": "qa_eng", "role_title": "QA Engineer",
         "designation": "Инженер Тестирования", "status": "coding",
         "specialty": "Automated testing, integration validation, e2e flows",
         "languages": ["TypeScript", "Python"],
         "frameworks": ["Vitest", "Playwright", "Pytest", "Supabase"],
         "current_project": "Bureau Core Platform", "git_branch": "test/flow-e2e",
         "commits_today": 11, "prs_open": 4, "code_quality": 90, "efficiency": 86},
        {"codename": "ARC-06", "role_key": "sys_arch", "role_title": "Systems Architect",
         "designation": "Системный Архитектор", "status": "active",
         "specialty": "System design, data flow, performance optimization",
         "languages": ["Rust", "TypeScript", "SQL"],
         "frameworks": ["Tokio", "Deno", "PostgreSQL", "Redis"],
         "current_project": "Bureau Core Platform", "git_branch": "feat/pipeline-arch",
         "commits_today": 6, "prs_open": 2, "code_quality": 97, "efficiency": 93},
    ]

    for a in agents:
        insert_row("bureau_agents", a)
    for p in projects:
        insert_row("bureau_projects", p)
    for da in dev_agents:
        insert_row("dev_agents", da)

    # links: tasks reference projects and agents via FK; store project_id/agent_id
    # IDs are assigned by insert_row, so query them back from the DB
    project_rows = list_rows("bureau_projects")
    agent_rows = list_rows("bureau_agents")
    project_ids = {p["code"]: p["id"] for p in project_rows}
    agent_ids = {a["codename"]: a["id"] for a in agent_rows}

    tasks = [
        {"project_id": project_ids["PROJECT-CHRYN-01"], "agent_id": agent_ids["HERMES-02"],
         "title": "Define lattice propagation parameters",
         "directive": "Establish baseline constants for lattice propagation under variable thermal load",
         "status": "done", "priority": "high", "sort_order": 1},
        {"project_id": project_ids["PROJECT-CHRYN-01"], "agent_id": agent_ids["ARKON-03"],
         "title": "Design adaptive chassis topology",
         "directive": "Create structural framework supporting dynamic lattice reconfiguration",
         "status": "in_progress", "priority": "critical", "sort_order": 2},
        {"project_id": project_ids["PROJECT-CHRYN-01"], "agent_id": agent_ids["VEKTOR-04"],
         "title": "Analyze propagation test results",
         "directive": "Process telemetry from Batch 1-6 and identify deviation patterns",
         "status": "in_progress", "priority": "high", "sort_order": 3},
        {"project_id": project_ids["PROJECT-CHRYN-01"], "agent_id": agent_ids["ISPYT-05"],
         "title": "Stress-test lattice integrity",
         "directive": "Subject prototype chassis to extreme thermal cycling and record failure modes",
         "status": "pending", "priority": "critical", "sort_order": 4},
        {"project_id": project_ids["PROJECT-VEKT-02"], "agent_id": agent_ids["ARKON-03"],
         "title": "Build telemetry ingestion pipeline",
         "directive": "Construct data pipeline capable of processing 2M events/sec with sub-50ms latency",
         "status": "in_progress", "priority": "high", "sort_order": 1},
        {"project_id": project_ids["PROJECT-VEKT-02"], "agent_id": agent_ids["VEKTOR-04"],
         "title": "Define anomaly detection heuristics",
         "directive": "Establish statistical baselines and threshold models for real-time anomaly flagging",
         "status": "review", "priority": "high", "sort_order": 2},
        {"project_id": project_ids["PROJECT-NOVA-03"], "agent_id": agent_ids["HERMES-02"],
         "title": "Derive energy coupling equations",
         "directive": "Formulate mathematical framework for multi-axis energy coupling under variable field conditions",
         "status": "in_progress", "priority": "standard", "sort_order": 1},
        {"project_id": project_ids["PROJECT-NOVA-03"], "agent_id": None,
         "title": "Review coupling matrix feasibility",
         "directive": "Cross-check derived equations against known physical constraints and prior experimental data",
         "status": "pending", "priority": "standard", "sort_order": 2},
    ]
    for t in tasks:
        insert_row("bureau_tasks", t)

    # sample messages
    messages = [
        {"from_agent_id": agent_ids["SEREN-01"], "to_agent_id": agent_ids["HERMES-02"],
         "content": "All units: Project Chrysalis Phase III commencing. HERMES-02, prioritize propagation models. ARKON-03, chassis design must align with new parameters by 1800.",
         "channel": "command"},
        {"from_agent_id": agent_ids["HERMES-02"], "to_agent_id": agent_ids["ARKON-03"],
         "content": "Propagation models indicate thermal variance exceeds initial estimates by 14%. Recommend lattice density increase in sector 7-G before chassis finalization.",
         "channel": "research"},
        {"from_agent_id": agent_ids["ARKON-03"], "to_agent_id": agent_ids["VEKTOR-04"],
         "content": "Acknowledged. Adjusting lattice topology — will incorporate 7-G reinforcement. Request VEKTOR-04 provide historical thermal data for calibration.",
         "channel": "engineering"},
        {"from_agent_id": agent_ids["VEKTOR-04"], "to_agent_id": agent_ids["ARKON-03"],
         "content": "Telemetry from Batch 7 processed. Confirmed 14.3% thermal variance — data package transmitted to ARKON-03. Anomaly cluster detected in sector 7-G, corroborating HERMES-02 findings.",
         "channel": "research"},
        {"from_agent_id": agent_ids["SEREN-01"], "to_agent_id": None,
         "content": "BROADCAST: Chrysalis Phase III parameters locked. All agents confirm readiness. Next review cycle at 0600.",
         "channel": "command"},
    ]
    for m in messages:
        insert_row("bureau_messages", m)

    # seed deliverables (a few samples)
    deliverables = [
        {"project_id": project_ids["PROJECT-CHRYN-01"], "agent_id": agent_ids["HERMES-02"],
         "title": "Lattice Propagation Model — Phase III",
         "description": "Derived mathematical framework for adaptive lattice propagation under variable thermal load. Includes correction coefficients for sector 7-G anomaly cluster.",
         "file_content": "== Lattice Propagation Model v3.1 ==\n\nAuthor: HERMES-02 (Chief Theorist)\nProject: PROJECT-CHRYN-01\nDate: 2026-09-14\n\n## 1. Base Equation\n\nΨ(x,t) = Σᵢ αᵢ · φᵢ(x) · exp(-λᵢt)\n\nWhere:\n  αᵢ = coupling coefficient for lattice node i\n  φᵢ = eigenfunction of thermal distribution\n  λᵢ = decay constant (thermal)\n\n## 2. Thermal Correction\n\nΔΨ = 0.143 · Ψ · (∂T/∂x)²\n\nApplied in sector 7-G where thermal variance exceeds 14% baseline.\n\n## 3. Lattice Density Recommendation\n\nOptimal density: 14.7 nodes/cm³ (up from 12.3)\nReinforcement zone: 7-G, radius 2.4m\n\n## 4. Validation Status\n\nCross-checked against VEKTOR-04 telemetry Batch 7.\nDeviation: < 0.3% (within tolerance).\n\n== END OF DOCUMENT ==",
         "file_format": "md", "status": "final"},
        {"project_id": project_ids["PROJECT-VEKT-02"], "agent_id": agent_ids["ARKON-03"],
         "title": "Telemetry Pipeline Architecture Spec",
         "description": "Structural specification for the Vektor telemetry ingestion pipeline.",
         "file_content": "== Telemetry Pipeline Architecture Spec v2 ==\n\nAuthor: ARKON-03 (Chief Constructor)\nProject: PROJECT-VEKT-02\nDate: 2026-09-13\n\n## 1. Throughput Requirements\n\nTarget: 2,100,000 events/second\nSustained: 1,800,000 events/second\nPeak tolerance: 2,500,000 events/second (30s bursts)\n\n## 2. Buffer Design\n\nPer-channel buffer: 512 MB (ring buffer)\nFlush interval: 47ms\nOverflow policy: drop-oldest with anomaly flag\n\n== END OF DOCUMENT ==",
         "file_format": "md", "status": "final"},
        {"project_id": project_ids["PROJECT-VEKT-02"], "agent_id": agent_ids["VEKTOR-04"],
         "title": "Anomaly Detection Heuristics Report",
         "description": "Statistical baselines and threshold models for real-time anomaly flagging.",
         "file_content": "== Anomaly Detection Heuristics Report ==\n\nAuthor: VEKTOR-04 (Senior Analyst)\nProject: PROJECT-VEKT-02\nDate: 2026-09-15\n\n## 1. Statistical Baselines\n\nThermal distribution: μ=312.4K, σ=4.7K\nLattice coupling: μ=0.872, σ=0.018\nEvent rate: μ=1.89M/s, σ=0.12M/s\n\n## 2. Threshold Models\n\nModel A (3σ hard threshold):\n  - Thermal: flag if |x-μ| > 14.1K\n  - Coupling: flag if |x-μ| > 0.054\n  - Rate: flag if |x-μ| > 0.36M/s\n\n== END OF DOCUMENT ==",
         "file_format": "md", "status": "published"},
    ]
    for d in deliverables:
        insert_row("bureau_deliverables", d)

    # warehouse components (sample)
    components = [
        {"name": "Resistor 10kΩ 1%", "part_number": "RC0603FR-0710KL", "manufacturer": "Yageo",
         "category": "resistor", "package_type": "0603 SMD", "value": "10kΩ", "tolerance": "±1%",
         "voltage_rating": "75V", "power_rating": "0.1W", "temperature_range": "-55 to +155°C",
         "description": "Standard thick film chip resistor", "quantity": 1240, "location": "A-01-03",
         "tags": ["resistor", "smd", "0603", "10k"]},
        {"name": "Capacitor 100µF", "part_number": "EEU-FR1H101B", "manufacturer": "Panasonic",
         "category": "capacitor", "package_type": "Radial DIP", "value": "100µF", "tolerance": "±20%",
         "voltage_rating": "50V", "power_rating": None, "temperature_range": "-40 to +105°C",
         "description": "Aluminum electrolytic capacitor", "quantity": 85, "location": "B-02-01",
         "tags": ["capacitor", "electrolytic", "100uF"]},
        {"name": "STM32F407VGT6", "part_number": "STM32F407VGT6", "manufacturer": "STMicroelectronics",
         "category": "microcontroller", "package_type": "LQFP-100", "value": "3.3V", "tolerance": None,
         "voltage_rating": "3.6V", "power_rating": None, "temperature_range": "-40 to +85°C",
         "description": "ARM Cortex-M4 32-bit MCU, 168MHz, 1MB Flash, 192KB RAM",
         "quantity": 47, "location": "C-03-05", "tags": ["mcu", "stm32", "arm", "cortex-m4"]},
        {"name": "Connector USB-C", "part_number": "TYPE-C-31-M-12", "manufacturer": "HRO Electronics",
         "category": "connector", "package_type": "SMD", "value": None, "tolerance": None,
         "voltage_rating": None, "power_rating": None, "temperature_range": "-25 to +85°C",
         "description": "USB Type-C receptacle 16-pin SMD", "quantity": 210, "location": "D-01-02",
         "tags": ["connector", "usb-c", "smd"]},
        {"name": "Transistor 2N3904", "part_number": "2N3904", "manufacturer": "ON Semiconductor",
         "category": "transistor", "package_type": "TO-92", "value": "NPN", "tolerance": None,
         "voltage_rating": "60V", "power_rating": "0.625W", "temperature_range": "-55 to +150°C",
         "description": "General purpose NPN bipolar junction transistor",
         "quantity": 340, "location": "E-04-01", "tags": ["transistor", "npn", "to-92"]},
    ]
    for c in components:
        insert_row("warehouse_components", c)

    # flow workflows + steps
    wf = insert_row("flow_workflows", {
        "name": "Chrysalis Phase III — Execution Flow",
        "description": "Sequential agent workflow for Phase III lattice propagation development",
        "status": "draft",
    })
    steps = [
        {"agent_id": agent_ids["HERMES-02"], "title": "Define Propagation Parameters",
         "directive": "Establish baseline constants for lattice propagation under variable thermal load",
         "step_type": "input", "position_x": 80, "position_y": 120, "sort_order": 0, "status": "done"},
        {"agent_id": agent_ids["ARKON-03"], "title": "Design Chassis Topology",
         "directive": "Create structural framework supporting dynamic lattice reconfiguration",
         "step_type": "process", "position_x": 340, "position_y": 80, "sort_order": 1, "status": "active"},
        {"agent_id": agent_ids["VEKTOR-04"], "title": "Analyze Test Results",
         "directive": "Process telemetry from Batch 1-6 and identify deviation patterns",
         "step_type": "process", "position_x": 340, "position_y": 220, "sort_order": 2, "status": "active"},
        {"agent_id": agent_ids["ISPYT-05"], "title": "Stress-Test Lattice",
         "directive": "Subject prototype chassis to extreme thermal cycling and record failure modes",
         "step_type": "review", "position_x": 600, "position_y": 150, "sort_order": 3, "status": "pending"},
        {"agent_id": agent_ids["OPTIM-06"], "title": "Optimize Pipeline",
         "directive": "Tune lattice parameters and buffer allocation for maximum throughput",
         "step_type": "output", "position_x": 860, "position_y": 150, "sort_order": 4, "status": "pending"},
    ]
    step_ids = []
    for s in steps:
        s["workflow_id"] = wf["id"]
        inserted = insert_row("flow_steps", s)
        step_ids.append(inserted["id"])

    # connections (edges)
    for i in range(len(step_ids) - 1):
        insert_row("flow_connections", {
            "workflow_id": wf["id"],
            "from_step_id": step_ids[i],
            "to_step_id": step_ids[i + 1],
            "label": None,
        })

    print("[localdb] seeded initial data")
