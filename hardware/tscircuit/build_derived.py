#!/usr/bin/env python3
"""
Сборка локальных производных таблиц в стиле tscircuit/jlcsearch.

Зачем не ходить в базу напрямую. Штатный движок деталей tscircuit
(JlcPcbPartsEngine в @tscircuit/eval) обращается к своему сервису
jlcsearch.tscircuit.com/{resistors,capacitors,...}/list и ждёт плоские
числовые поля:

    {"lcsc": 21190, "mfr": "0603WAF1001T5E", "package": "0603",
     "resistance": 1000, "tolerance_fraction": 0.01,
     "power_watts": 100, "stock": 31485061, "price1": 0.000814286}

Сервис этот умеет так потому, что репозиторий tscircuit/jlcsearch
пересобирает исходную базу jlcparts в производные таблицы с числовыми
колонками (lib/db/derivedtables/resistor.ts, capacitor.ts и другие):
значения разбираются один раз при сборке, дальше идёт индексированный
запрос по числу.

Здесь то же самое, но локально: исходная база — cache.sqlite3 от
yaqwsx/jlcparts, разбор значений делают её же нормализаторы
readResistance / readCapacitance / readInductance (это питоновский
эквивалент parseAndConvertSiUnit из jlcsearch), а результат кладётся в
out/jlcsearch.db с теми же именами колонок.

Названия колонок и правила взяты из спецификаций jlcsearch, чтобы наш
findPart и штатный JlcPcbPartsEngine говорили на одном языке.

Запуск:
    python3 hardware/tscircuit/build_derived.py
"""

from __future__ import annotations

import json
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC_DB = "/mnt/ext4data/jlcparts/cache/cache.sqlite3"
OUT_DB = ROOT / "hardware/tscircuit/out/jlcsearch.db"
JLCPARTS = Path("/mnt/ext4data/jlcparts")

sys.path.insert(0, str(JLCPARTS))
from jlcparts.attributes import (  # noqa: E402
    readCapacitance,
    readCurrent,
    readFrequency,
    readInductance,
    readResistance,
)


def num(read, raw):
    """Число из строки каталога; None если разобрать нельзя."""
    if raw in (None, ""):
        return None
    try:
        v = read(str(raw))
    except Exception:                                    # noqa: BLE001
        return None
    if v is None or isinstance(v, str) or v != v:        # noqa: PLR0124
        return None
    return float(v)


def price1(raw) -> float | None:
    """
    Минимальная цена в минимальной партии — аналог extractMinQPrice.

    Цена в базе приходит строкой вида "1-9:0.0132,10-11:0.0132,12-…:…".
    Берём самый нижний диапазон количества, как это делает jlcsearch.
    """
    if not raw:
        return None
    first = str(raw).split(",")[0]
    if ":" not in first:
        return None
    try:
        return float(first.split(":")[1])
    except (ValueError, IndexError):
        return None


def is_smd(pkg: str) -> bool:
    p = (pkg or "").lower()
    return "smd" in p or "插件" not in p


# Таблицы повторяют спецификации jlcsearch: категория из jlcparts плюс
# разобранные в числа характеристики.
TABLES: dict[str, dict] = {
    "resistor": {
        "category": "Resistors",
        "primary": "resistance",
        "cols": """
            resistance REAL, tolerance_fraction REAL, power_watts REAL,
            is_potentiometer INTEGER, is_surface_mount INTEGER
        """,
        "fields": [
            ("resistance", readResistance, "Resistance"),
            ("tolerance_fraction", readResistance, "Tolerance"),
            ("power_watts", readResistance, "Power(Watts)"),
        ],
        "flags": {
            "is_potentiometer": lambda r: int("potentiometer" in (r["desc"] or "").lower()),
            "is_surface_mount": lambda r: int(is_smd(r["pkg"])),
        },
    },
    "capacitor": {
        "category": "Capacitors",
        "primary": "capacitance_farads",
        "cols": """
            capacitance_farads REAL, tolerance_fraction REAL,
            voltage_rating REAL, is_polarized INTEGER,
            is_surface_mount INTEGER, capacitor_type TEXT
        """,
        "fields": [
            ("capacitance_farads", readCapacitance, "Capacitance"),
            ("tolerance_fraction", readResistance, "Tolerance"),
            ("voltage_rating", readCurrent, "Rated Voltage"),
        ],
        "flags": {
            "is_polarized": lambda r: int(any(
                k in (r["desc"] or "").lower()
                for k in ("electrolytic", "tantalum", "polymer"))),
            "is_surface_mount": lambda r: int(is_smd(r["pkg"])),
            "capacitor_type": lambda r: next(
                (t for t in ("ceramic", "electrolytic", "tantalum", "film",
                             "polymer", "mica", "variable")
                 if t in (r["desc"] or "").lower()), "unknown"),
        },
    },
    "inductor": {
        "category": "Inductors, Coils, Chokes",
        "primary": "inductance_henries",
        "cols": """
            inductance_henries REAL, dc_resistance_ohms REAL,
            current_rating_amps REAL, is_surface_mount INTEGER
        """,
        "fields": [
            ("inductance_henries", readInductance, "Inductance"),
            ("dc_resistance_ohms", readResistance, "DC Resistance(DCR)"),
            ("current_rating_amps", readCurrent, "Current Rating"),
        ],
        "flags": {"is_surface_mount": lambda r: int(is_smd(r["pkg"]))},
    },
    "diode": {
        "category": "Diodes",
        # ищем по корпусу и категории, поэтому ни одно поле не обязательно
        "primary": None,
        "cols": """
            voltage_dc REAL, current_avg REAL, is_surface_mount INTEGER
        """,
        "fields": [
            ("voltage_dc", readCurrent, "Voltage - Forward (Vf)"),
            ("current_avg", readCurrent, "Current - Average (Io)"),
        ],
        "flags": {"is_surface_mount": lambda r: int(is_smd(r["pkg"]))},
    },
    "led": {
        "category": "Optoelectronics",
        "primary": None,
        "subcategory_like": "LED",
        "cols": "wavelength_nm REAL, is_surface_mount INTEGER",
        "fields": [],
        "flags": {"is_surface_mount": lambda r: int(is_smd(r["pkg"]))},
    },
    "fuse": {
        "category": "Circuit Protection",
        "primary": "current_rating_amps",
        "subcategory_like": "Fuse",
        "cols": "current_rating_amps REAL, voltage_rating REAL",
        "fields": [
            ("current_rating_amps", readCurrent, "Current Rating"),
            ("voltage_rating", readCurrent, "Voltage Rating"),
        ],
    },
    "crystal": {
        "category": "Crystals, Oscillators, Resonators",
        "subcategory": "Crystals",
        "primary": "frequency_hz",
        "cols": """
            frequency_hz REAL, frequency_tolerance_ppm REAL,
            load_capacitance_f REAL
        """,
        "fields": [
            ("frequency_hz", readFrequency, "Frequency"),
            ("frequency_tolerance_ppm", readCurrent,
             "Frequency Stability"),
            ("load_capacitance_f", readCapacitance, "Load Capacitance"),
        ],
    },
}

BASE = """
    lcsc INTEGER PRIMARY KEY, mfr TEXT, package TEXT, description TEXT,
    category TEXT, subcategory TEXT, stock INTEGER, price1 REAL,
    in_stock INTEGER, is_basic INTEGER, is_preferred INTEGER
"""


def main() -> int:
    if not Path(SRC_DB).is_file():
        print(f"НЕТ исходной базы {SRC_DB}")
        return 1
    src = sqlite3.connect(f"file:{SRC_DB}?mode=ro", uri=True)
    if OUT_DB.is_file():
        OUT_DB.unlink()
    out = sqlite3.connect(OUT_DB)

    total = 0
    for name, spec in TABLES.items():
        q = ("SELECT lcsc, mfr, package, description, category, subcategory, "
             "attributes, stock, price, preferred, assembly FROM "
             "jlc_components WHERE category = ?")
        args: list = [spec["category"]]
        if spec.get("subcategory"):
            q += " AND subcategory = ?"
            args.append(spec["subcategory"])
        elif spec.get("subcategory_like"):
            q += " AND subcategory LIKE ?"
            args.append(f"%{spec['subcategory_like']}%")

        out.execute(f"CREATE TABLE {name} ({BASE}, {spec['cols']})")
        # Индексы ровно по тем полям, по которым идёт подбор: номинал и
        # корпус. Без них запрос по номиналу сканирует таблицу.
        out.execute(f"CREATE INDEX idx_{name}_mfr ON {name}(mfr)")
        numcols = [c for c, _r, _a in spec["fields"]] or ["lcsc"]
        out.execute(f"CREATE INDEX idx_{name}_pkg ON {name}(package)")
        for c in numcols:
            out.execute(f"CREATE INDEX idx_{name}_{c} ON {name}({c})")

        rows, kept = [], 0
        for (lcsc, mfr, pkg, desc, cat, sub, attrs, stock, price, pref,
             asm) in src.execute(q, args):
            a_ = json.loads(attrs or "{}")
            r = {"desc": desc or "", "pkg": pkg or ""}
            rec = {
                "lcsc": lcsc, "mfr": mfr, "package": pkg or "",
                "description": desc, "category": cat, "subcategory": sub,
                "stock": stock or 0, "price1": price1(price),
                "in_stock": int((stock or 0) > 0),
                "is_basic": int(bool(asm)),
                "is_preferred": int(bool(pref)),
            }
            for col, read, attr in spec["fields"]:
                rec[col] = num(read, a_.get(attr))
            # Отбрасывается только строка без поля, по которому идёт
            # подбор. Обязательным делается одно поле — то, что ищет
            # findPart. Если требовать все сразу, то теряются, например,
            # конденсаторы без указанного Tolerance: у 0603 их тысячи, и
            # категория пустеет целиком.
            primary = spec.get("primary")
            if primary and rec.get(primary) is None:
                continue
            for col, fn in spec.get("flags", {}).items():
                rec[col] = fn(r)
            rows.append(rec)
            kept += 1

        if rows:
            keys = list(rows[0].keys())
            out.executemany(
                f"INSERT OR REPLACE INTO {name} ({','.join(keys)}) "
                f"VALUES ({','.join('?' * len(keys))})",
                [tuple(x[k] for k in keys) for x in rows])
        total += kept
        print(f"  {name:<10} {kept:>7} строк   "
              f"(категория «{spec['category']}»"
              + (f", {spec['subcategory']}" if spec.get("subcategory") else "")
              + (", *%s*" % spec["subcategory_like"]
                 if spec.get("subcategory_like") else "") + ")")

    # Общая таблица для поиска по артикулу. Там интересует сам mfr, а не
    # характеристики, поэтому фильтровать по разобранным значениям нельзя:
    # деталь с неразобранным номиналом в part попадает, в resistor — нет.
    out.execute(f"CREATE TABLE part ({BASE})")
    cols = ("lcsc, mfr, package, description, category, subcategory, "
            "stock, price1, in_stock, is_basic, is_preferred")
    for name in TABLES:
        out.execute(f"INSERT OR REPLACE INTO part SELECT {cols} FROM {name}")
    out.execute("CREATE INDEX idx_part_mfr ON part(mfr)")
    n_part = out.execute("select count(*) from part").fetchone()[0]
    print(f"  {'part':<10} {n_part:>7} строк   (общая, для поиска по артикулу)")

    out.commit()
    out.execute("VACUUM")
    out.close()
    print(f"\nготово: {OUT_DB}  {OUT_DB.stat().st_size // 1024 // 1024} МБ")
    return 0


if __name__ == "__main__":
    sys.exit(main())
