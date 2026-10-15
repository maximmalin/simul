"""
Krystal Radio — конфигурация предметной области для интерфейса.

Фронтенд (EngineeringTeam) содержит несколько захардкоженных наборов,
которые должны приходить с backend:

  * «zone telemetry» — ZoneHeader и OverviewTab показывают строки
    `Zone Status`, `Radiation 3.4 R/h`, `Anomalies 2 Detected`,
    `Network 97.3%` — два компонента дублируют одни и те же литералы;
  * `PRODUCT_SPECS` в PipelineChain — характеристики изделия, захардкожены
    для трёх выдуманных кодов проектов (Lattice Propulsion Core и т.п.);
  * словари ролей/языков/фреймворков в DevDeptTab;
  * категории склада в WarehouseTab;
  * статусы и приоритеты.

Здесь они описаны **для нашего приёмника**, а не для вымышленных изделий.
Фронтенд забирает их через `GET /api/config` и `GET /api/zone`.
"""

from __future__ import annotations

# --------------------------------------------------------------------------
# Телеметрия «зоны»
#
# Для Krystal Radio это не вымышленные «излучения», а реальные метрики
# сборки и приёмника. Значения вычисляются из артефактов в out/,
# поэтому «Nominal / 3.4 R/h» заменяются на состояние сборки,
# прогресс тестов и тепловой режим.
# --------------------------------------------------------------------------
ZONE_STATUS_LABELS = {
    "nominal": "Nominal",
    "degraded": "Degraded",
    "critical": "Critical",
}


def zone_telemetry(health: dict, chain: dict, tests: dict | None,
                   metrics: dict) -> dict:
    """Телеметрия для ZoneHeader / OverviewTab.

    Поля сохран��ют привычную форму интерфейса, но наполнены реальными
    данными проекта:

      status    — состояние сборки (все стадии собраны / есть пропуски)
      radiation — тепловая нагрузка: Tj_max в °C (было «3.4 R/h»)
      anomalies — число стадий цепочки не в статусе done (было «2 Detected»)
      network   — доля пройденных тестов, % (было «97.3%»)
    """
    done = chain.get("done", 0)
    total = chain.get("total", 1)
    progress = chain.get("progress", 0)

    # Аномалии = стадии не готовы (stale/running/missing)
    anomalies = [
        {"key": s["key"], "label": s["label"], "state": s["state"]}
        for s in chain.get("stages", []) if s["state"] != "done"
    ]

    # «Излучение» — фактическая темпераjunction, °C
    th = metrics.get("thermal", {})
    radiation = th.get("tj_c")
    if radiation is None:
        radiation = th.get("tmax_fem_c")

    # «Сеть» — процент пройденных тестов
    if tests and tests.get("passed") is not None:
        passed, failed = tests["passed"], tests.get("failed", 0) or 0
        network = round(100.0 * passed / max(1, passed + failed), 1)
    else:
        network = float(progress)

    if not anomalies:
        status = "nominal"
    elif progress >= 70:
        status = "degraded"
    else:
        status = "critical"

    return {
        "status": status,
        "status_label": ZONE_STATUS_LABELS[status],
        "radiation_c": radiation,
        "radiation_display": (f"{radiation:.1f} °C"
                              if radiation is not None else "—"),
        "anomalies_count": len(anomalies),
        "anomalies": anomalies,
        "network_pct": network,
        "network_display": f"{network:.1f}%",
        "chain_progress_pct": progress,
        "chain_done": done,
        "chain_total": total,
        "has_page": health.get("has_page", False),
        "has_waterfall": health.get("has_waterfall", False),
        "has_glb": health.get("has_glb", False),
    }


# --------------------------------------------------------------------------
# Характеристики изделия
#
# Заменяют PRODUCT_SPECS из PipelineChain. Ключ — код проекта,
# значение — реальные параметры нашего приёмника.
# --------------------------------------------------------------------------
PRODUCT_SPECS: dict[str, dict] = {
    "KRYSTAL-01": {
        "product": "Krystal Radio — zero-IF SDR прямого преобразования",
        "specs": [
            {"label": "Диапазон", "value": "0–7 ГГц (3 канала)"},
            {"label": "MCU", "value": "STM32F103C8T6, 72 МГц"},
            {"label": "Тракт", "value": "2N7002 ×2 → LT5560 → OPA2134"},
            {"label": "Оцифровка", "value": "12 бит, ≤ 1 MSPS"},
            {"label": "NF тракта", "value": "≤ 3.5 дБ (расчёт 2.14 дБ)"},
            {"label": "Шум / MDS", "value": "−136.2 / −126.2 дБм"},
            {"label": "Полоса ПЧ", "value": "2.7 кГц"},
            {"label": "Интерфейс", "value": "USB CDC → GNU Radio"},
            {"label": "Питание", "value": "USB 5 В → AMS1117-3.3 (линейное)"},
            {"label": "Корпус", "value": "Al 0.5 мм, 3 зоны, прокладки ≤10 мОм"},
            {"label": "Тепло", "value": "Tj ≤ 85 °C"},
            {"label": "MTBF", "value": "≈ 2.05·10⁶ ч"},
        ],
    },
    # Синонимы на случай, если проект создан с другим кодом.
    "KRYSTAL-SDR": {"alias_of": "KRYSTAL-01"},
    "KRYSTAL": {"alias_of": "KRYSTAL-01"},
}


def product_specs_for(code: str) -> dict | None:
    """Характеристики по коду проекта (с учётом алиасов)."""
    if code in PRODUCT_SPECS:
        spec = PRODUCT_SPECS[code]
        return None if "alias_of" in spec else spec
    for k, v in PRODUCT_SPECS.items():
        if v.get("alias_of") == code:
            return PRODUCT_SPECS[v["alias_of"]]
    return None


# --------------------------------------------------------------------------
# Словари для выпадающих списков
# --------------------------------------------------------------------------
DEV_ROLES = [
    {"key": "lead_dev", "label": "Lead Developer", "designation": "Ведущий разработчик"},
    {"key": "frontend_dev", "label": "Frontend Developer", "designation": "Фронтенд-разработчик"},
    {"key": "backend_dev", "label": "Backend Developer", "designation": "Бэкенд-разработчик"},
    {"key": "devops_eng", "label": "DevOps Engineer", "designation": "Инженер эксплуатации"},
    {"key": "qa_eng", "label": "QA Engineer", "designation": "Инженер по качеству"},
    {"key": "sys_arch", "label": "System Architect", "designation": "Системный архитектор"},
    {"key": "rf_eng", "label": "RF Engineer", "designation": "Инженер СВЧ"},
    {"key": "emc_eng", "label": "EMC Engineer", "designation": "Инженер по ЭМС"},
]

LANGUAGES = ["TypeScript", "JavaScript", "Python", "C", "Rust", "Go", "SQL",
             "Bash", "YAML", "FreeCAD Python", "SystemVerilog"]

FRAMEWORKS = ["React", "Vite", "Supabase", "Tailwind", "GNU Radio", "Keras",
              "NumPy", "PostgreSQL", "KiCad", "tscircuit", "ngspice",
              "Octave", "FreeCAD", "Docker", "GitHub Actions"]

WAREHOUSE_CATEGORIES = [
    "resistor", "capacitor", "inductor", "diode", "transistor", "mosfet",
    "ic", "mcu", "connector", "crystal", "filter", "module", "antenna",
    "mechanical", "uncategorized",
]

WAREHOUSE_MANUFACTURERS = [
    "STMicroelectronics", "Texas Instruments", "Analog Devices", "NXP",
    "Microchip", "Infineon", "onsemi", "Panasonic", "Murata", "TDK",
    "Vishay", "Yageo", "Samsung", "Kyocera", "AVX", "LTC",
]

WAREHOUSE_MCUS = ["STM32F103C8T6", "STM32H7", "ESP32", "ATmega328P",
                  "RP2040", "nRF52840"]

PROJECT_STATUSES = ["planning", "active", "review", "completed"]
TASK_STATUSES = ["pending", "in_progress", "review", "done"]
PRIORITIES = ["critical", "high", "standard", "low"]
MESSAGE_CHANNELS = ["command", "research", "engineering", "testing", "optimization"]
DELIVERABLE_FORMATS = ["md", "json", "csv", "txt", "html", "svg", "pdf", "c"]

AGENT_STATUSES = ["active", "processing", "idle", "offline"]


def config_payload() -> dict:
    """Полный словарь для `GET /api/config`."""
    return {
        "dev_roles": DEV_ROLES,
        "languages": LANGUAGES,
        "frameworks": FRAMEWORKS,
        "warehouse": {
            "categories": WAREHOUSE_CATEGORIES,
            "manufacturers": WAREHOUSE_MANUFACTURERS,
            "mcus": WAREHOUSE_MCUS,
        },
        "enums": {
            "project_status": PROJECT_STATUSES,
            "task_status": TASK_STATUSES,
            "priority": PRIORITIES,
            "message_channel": MESSAGE_CHANNELS,
            "deliverable_format": DELIVERABLE_FORMATS,
            "agent_status": AGENT_STATUSES,
        },
        "product_specs": {k: v for k, v in PRODUCT_SPECS.items()
                          if "alias_of" not in v},
        "aliases": {k: v["alias_of"] for k, v in PRODUCT_SPECS.items()
                    if "alias_of" in v},
    }