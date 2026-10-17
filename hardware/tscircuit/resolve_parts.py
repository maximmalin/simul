#!/usr/bin/env python3
"""
Подбор замен деталям по локальной базе JLCPCB.

Зачем. В схеме в поле Value стоят номиналы ("10k", "100n", "22n"), а не
артикулы. Штатный движок деталей tscircuit их не находит по двум причинам:

  * он обращается к своему серверу
    https://jlcsearch.tscircuit.com/{resistors,capacitors,...}/list — то
    есть снова сеть, а работать нужно оффлайн;
  * все компоненты приходят с ftype="simple_chip", потому что схема
    собрана из <chip>. На этой ветке движок ищет «чипы по корпусу» вообще
    без номинала (см. JlcPcbPartsEngine.findPart в @tscircuit/eval), и
    пассивки там не находятся.

Здесь тот же алгоритм, но на локальной базе: тип детали выводится из
библиотеки корпуса, номинал разбирается нормализаторами библиотеки
jlcparts, а выбор идёт по категории, корпусу и номиналу с приоритетом
базовых деталей — ровно как у штатного движка с его is_basic.

Разбор номинала делают функции readResistance / readCapacitance /
readInductance из пакета jlcparts, а не самописные: там отработаны
префиксы, диапазоны и мусорные значения каталога.

Про 3D-модели. Модель в этом конвейере берётся из корпуса KiCad, а не из
артикула, поэтому артикул на её наличие не влияет. Но если у корпуса
модели нет вовсе, берётся кандидат другого корпуса, для которого модель
есть, — как и требовалось: сначала MPN, а если под него модели нет,
подбирается та, под которую модель нашлась. Список таких случаев печатается.

Дружба с part_fetch. Если агент уже выбрал деталь по коду LCSC (part_fetch
скачал символ, корпус и 3D, а в design/schematic.json записан lcsc), то
подбор по номиналу не нужен: берётся ровно этот артикул, а паспортные
данные (MPN, производитель, файл даташита) добавляются из design/datasheets.
Такие детали не подменяются по модели: корпус, символ и тело скачаны под
этот артикул, и другой корпус сломал бы согласованность схемы с платой.

Запуск:
    python3 hardware/tscircuit/resolve_parts.py [--report]
Результат: hardware/tscircuit/out/resolved_parts.json
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NETLIST = ROOT / "hardware/kicad/brig_receiver.net"
MODELS = ROOT / "hardware/tscircuit/models"
FP_ROOT = Path("/usr/share/kicad/footprints")
OUT = ROOT / "hardware/tscircuit/out/resolved_parts.json"
# Производные таблицы в стиле jlcsearch; собираются build_derived.py.
DB = ROOT / "hardware/tscircuit/out/jlcsearch.db"
JLCPARTS = Path("/mnt/ext4data/jlcparts")

# Дизайн-каталог заказа: явные артикулы агентов и паспорта их деталей.
# Если не заданы ключами, ищутся сами рядом с нетлистом
# (…/out/hardware/device.net → …/design/…).
SPEC: Path | None = None          # design/schematic.json
DS_INDEX: Path | None = None      # design/datasheets/index.json
LIBS_DIR: Path | None = None      # design/libs (.kicad_sym/.pretty/.3dshapes)

sys.path.insert(0, str(JLCPARTS))
try:
    from jlcparts.attributes import (  # noqa: E402
        readCapacitance,
        readCurrent,
        readFrequency,
        readInductance,
        readResistance,
    )
except Exception as e:                                   # noqa: BLE001
    print(f"НЕ УДАЛОСЬ подключить jlcparts из {JLCPARTS}: {e}")
    print("Разбор номиналов без него будет самописным и менее точным.")
    sys.exit(1)

# ── тип детали по библиотеке корпуса ────────────────────────────────────────
# Ключ — имя библиотеки KiCad, значение — (категория в базе, чтение номинала,
# имя поля в attributes, имя параметра). Это не список деталей, а правило
# соответствия корпуса типу, поэтому переносится на другую схему.
# Библиотека корпуса KiCad -> (производная таблица, числовая колонка,
# функция разбора номинала). Таблицы и имена колонок повторяют спецификации
# tscircuit/jlcsearch (lib/db/derivedtables), то есть запрос идёт по числу,
# как у штатного движка: resistance=1k -> resistance=1000.
RULES: dict[str, tuple[str, str, object]] = {
    "Resistor_SMD": ("resistor", "resistance", readResistance),
    "Resistor_ThroughHole": ("resistor", "resistance", readResistance),
    "Resistor_Power_SMD": ("resistor", "resistance", readResistance),
    "Capacitor_SMD": ("capacitor", "capacitance_farads", readCapacitance),
    "Capacitor": ("capacitor", "capacitance_farads", readCapacitance),
    "Inductor_SMD": ("inductor", "inductance_henries", readInductance),
    "Inductor_Power_SMD": ("inductor", "inductance_henries", readInductance),
    "Diode_SMD": ("diode", None, None),
    "Diode": ("diode", None, None),
    "LED_SMD": ("led", None, None),
    "LED_THT": ("led", None, None),
    "Fuse": ("fuse", "current_rating_amps", readCurrent),
    "Crystal": ("crystal", "frequency_hz", readFrequency),
    "Crystal_SMD": ("crystal", "frequency_hz", readFrequency),
}

# ── корпус -> регулярка пакета LCSC ──────────────────────────────────────────
# Пакеты в каталоге записаны неоднородно: LQFP-48, LQFP-48(7x7),
# SOT-23-3(TO-236), TO-252(DPAK), 插件,D6.3xL6mm. Поэтому сравнение идёт по
# регулярке, а не по точному равенству. Регулярка выводится из имени корпуса.
IMPERIAL = r"0402|0603|0805|1206|1210|1812|2010|2512|1020|2917"
FAMILY = (
    r"SOT|SOD|SOIC|LQFP|TQFP|QFN|SSOP|TSSOP|SOP|SM\d|DO-\d+|TO-\d+|"
    r"DIP|PDIP|CDIP|SMA|SMB|SMC|BGA|XQFN|SON|DFN|TSOT|SOT-23"
)
# Семейство с номером: SOT-223, SOIC-8, LQFP-48, TO-252, DO-214AC.
# Номер обязателен, иначе SOT-223 и SOT-23 становятся неразличимы.
FAMILY_NUMBERED_RE = re.compile(
    r"(?<![A-Za-z0-9])("
    r"SOT|SOD|SOIC|LQFP|TQFP|QFN|SSOP|TSSOP|SOP|SM|DO|TO|DIP|PDIP|CDIP|"
    r"SON|DFN|BGA|XQFN"
    r")[-_]?(\d+)(?![0-9])",
    re.I,
)
# Семейство без номера: SMA, SMD, SOD.
FAMILY_PLAIN_RE = re.compile(
    r"(?<![A-Za-z0-9])(SMA|SMB|SMC|PLCC|SIP|SOT)(?![A-Za-z0-9])", re.I
)
IMPERIAL_RE = re.compile(rf"^(?:{IMPERIAL})$")
METRIC_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*mm", re.I)


def package_patterns(footprint: str) -> list[str]:
    """
    Регулярки пакетов LCSC, подходящих под корпус KiCad.

    Код корпуса ищется во всём имени, а не в первом токене: в
    R_0603_1608Metric первым идёт префикс вида «R», а код 0603 стоит
    вторым. Номер семейства обязателен, иначе SOT-223 и SOT-23 дали бы
    один и тот же шаблон и подошли бы друг под друга.

    Метрический шаблон вида D6.3 добавляется только если семейство не
    найдено, иначе у LQFP-48_7x7mm подтягивается лишний «D7».
    """
    _, _, name = footprint.partition(":")
    out: list[str] = []

    imp = re.search(rf"(?<![0-9])({IMPERIAL})(?![0-9])", name)
    if imp:
        code = imp.group(1)
        out += [rf"^{code}$", rf"^{code}[-_\u2013]"]

    fam = FAMILY_NUMBERED_RE.search(name)
    if fam:
        f = fam.group(1).upper()
        n = fam.group(2)
        out += [rf"^{f}-{n}\b", rf"^{f}-{n}[-(]", rf"^{f}-{n}$"]
    else:
        plain = FAMILY_PLAIN_RE.search(name)
        if plain:
            f = re.escape(plain.group(1).upper())
            out += [rf"^{f}\b", rf"^{f}[-(]"]
        m = re.search(r"(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)", name)
        if m:
            out += [rf"D{re.escape(m.group(1))}\b", rf"{re.escape(m.group(1))}x"]

    if not out:
        out.append(rf"^{re.escape(name.split('_')[0])}")
    return out


def package_matches(lcsc_package: str, kicad_footprint: str) -> bool:
    """Совпадает ли пакет каталога с корпусом KiCad."""
    return any(re.search(p, lcsc_package or "")
               for p in package_patterns(kicad_footprint))


def find_mod(footprint: str) -> Path | None:
    """Файл корпуса: системные библиотеки KiCad, затем design/libs заказа."""
    lib, _, name = footprint.partition(":")
    for root in filter(None, (FP_ROOT, LIBS_DIR)):
        p = root / f"{lib}.pretty" / f"{name}.kicad_mod"
        if p.is_file():
            return p
    return None


def model_roots() -> list[Path]:
    """Где искать тела деталей.

    design/libs (part_fetch кладёт .step/.wrl рядом с корпусом), корень
    заказа (в корпусе EasyEDA модель записана относительным путём
    design/libs/…) и зеркало packages3d в STL.
    """
    roots: list[Path] = []
    if LIBS_DIR:
        roots += [LIBS_DIR, LIBS_DIR.parent.parent]
    roots.append(MODELS)
    return roots


def footprint_has_model(footprint: str) -> bool:
    """Есть ли локальный файл тела для корпуса.

    Сначала проектные библиотеки заказа, затем зеркало packages3d в STL.
    """
    lib, _, name = footprint.partition(":")
    cands: list[str] = []
    mod = find_mod(footprint)
    if mod:
        for m in re.finditer(r'\(model\s+"([^"]+)"',
                             mod.read_text(errors="ignore")):
            cands.append(re.sub(r"^\$\{[A-Z0-9_]+\}/", "", m.group(1)))
    # Запасной путь по соглашению packages3d: у части корпусов строки
    # (model ...) в .kicad_mod просто нет.
    cands.append(f"{lib}.3dshapes/{name}.step")
    for c in cands:
        base = re.sub(r"\.(step|stp|wrl|vrml|stl)$", "", c, flags=re.I)
        for root in model_roots():
            for ext in (".step", ".stp", ".wrl", ".stl"):
                if (root / f"{base}{ext}").is_file():
                    return True
        # Строка модели может быть записана полным относительным путём
        # (design/libs/…): проверяем имя файла в каталоге тела корпуса.
        if LIBS_DIR and (LIBS_DIR / f"{lib}.3dshapes"
                         / f"{Path(base).name}.step").is_file():
            return True
    return False


def parse_netlist() -> list[dict]:
    """ref -> {value, footprint} из нетлиста KiCad."""
    comps: list[dict] = []
    cur: dict | None = None
    for line in NETLIST.read_text(errors="ignore").splitlines():
        m = re.search(r"\(ref\s+\"?([^\"\s\)]+)\"?", line)
        if m:
            cur = {"ref": m.group(1)}
        if cur is None:
            continue
        m = re.search(r"\(value\s+\"?([^\"\)]*)\"?", line)
        if m:
            cur["value"] = m.group(1)
        m = re.search(r"\(footprint\s+\"([^\"]+)\"", line)
        if m:
            cur["footprint"] = m.group(1)
            comps.append(cur)
            cur = None
    return comps


# Номинал против артикула. В схеме в Value стоит либо «10k», «100n»,
# «38MHz», «2A» — это параметр, по которому и идёт подбор; либо «TL061»,
# «STM32F103C8Tx» — это артикул, по которому ищут напрямую.
NOMINAL_RE = re.compile(
    r"^\s*\d+(?:\.\d+)?\s*(?:[kKmMuUnNpPGGT]|meg|MEG)?\s*"
    r"(?:Ω|Ohm|ohm|F|H|V|A|W|Hz|%|\+)?\s*$",
    re.I,
)


def is_nominal(value: str) -> bool:
    """True, если Value похож на параметр, а не на артикул."""
    return bool(NOMINAL_RE.match(value or ""))


def value_ok(read, raw: str, target: float, rel: float = 1e-6) -> bool:
    """
    Совпадают ли номиналы.

    Допуск только относительный. Абсолютная прибавка в 1e-9 (как была
    сделано изначально) для ёмкостей бессмысленна: номиналы лежат в
    фарадах, и 1e-9 Ф — это 1 нФ. Из-за этого 2 пФ проходили как ответ
    и на 10 пФ, и на 33 пФ, то есть подбор молча выдавал не тот номинал.
    """
    if read is None or target is None or raw in (None, ""):
        return False
    try:
        v = read(str(raw))
    except Exception:                                    # noqa: BLE001
        return False
    if v is None or isinstance(v, str) or v != v:        # noqa: PLR0124
        return False
    try:
        v, target = float(v), float(target)
    except (TypeError, ValueError):
        return False
    return abs(v - target) <= abs(target) * rel


def find_by_mpn(con, value: str, pats: list[str]) -> list[dict]:
    """
    Прямой поиск по артикулу в производной таблице part.

    Штатный движок этого не делает: он ищет по категории и номиналу, а
    артикул использует только как фильтр по корпусу. Из-за этого, к
    примеру, MP2303 и BLM18 в нём не находится, хотя в каталоге они есть.
    """
    like = f"%{value.strip()}%"
    rows = con.execute(
        "SELECT lcsc, mfr, package, description, stock, price1, in_stock, "
        "       is_basic, is_preferred FROM part "
        "WHERE mfr = ? OR mfr LIKE ?", (value.strip(), like)).fetchall()
    out = []
    for r in rows:
        match = any(re.search(p, r[2] or "") for p in pats)
        out.append({
            "lcsc": f"C{r[0]}", "mfr": r[1], "package": r[2] or "",
            "description": (r[3] or "")[:90], "stock": r[4] or 0,
            "price1": r[5], "in_stock": bool(r[6]),
            "assembly": bool(r[7]), "preferred": bool(r[8]),
            "package_match": match,
        })
    # Корпус обязателен. Без этого фильтра подбор молча выдавал деталь
    # другого корпуса: на 1000 мкФ в 6.3 мм он приносил D13xL20 мм, а на
    # кварц 38 МГц в корпусе THT — SMD1612. Это хуже, чем «не найдено».
    return [r for r in out if r["package_match"]] or out


def find_by_nominal(con, table: str, column: str, read, value: str,
                    pats: list[str]) -> list[dict]:
    """
    Подбор по номиналу и корпусу в производной таблице.

    Значения в таблице уже числа, поэтому сравнение точное и индексированное
    — ровно то, ради чего jlcsearch и пересобирает базу. Разбор идёт по
    относительному допуску: абсолютная прибавка в 1e-9 для ёмкостей
    бессмысленна, это 1 нФ, и из-за неё 2 пФ проходили как ответ и на
    10 пФ, и на 33 пФ.
    """
    try:
        target = float(read(value))
        if target != target:
            target = None
    except Exception:                                    # noqa: BLE001
        target = None
    if target is None:
        return []

    sql = (f"SELECT lcsc, mfr, package, description, stock, price1, "
           f"in_stock, is_basic, is_preferred FROM {table} WHERE {column} = ?")
    out = []
    for r in con.execute(sql, (target,)):
        match = any(re.search(p, r[2] or "") for p in pats)
        out.append({
            "lcsc": f"C{r[0]}", "mfr": r[1], "package": r[2] or "",
            "description": (r[3] or "")[:90], "stock": r[4] or 0,
            "price1": r[5], "in_stock": bool(r[6]),
            "assembly": bool(r[7]), "preferred": bool(r[8]),
            "package_match": match,
        })
    return [r for r in out if r["package_match"]]


def find_by_package(con, table: str, pats: list[str]) -> list[dict]:
    """Подбор только по корпусу, когда номинала нет или он не задан."""
    out = []
    for r in con.execute(
            f"SELECT lcsc, mfr, package, description, stock, price1, "
            f"in_stock, is_basic, is_preferred FROM {table}"):
        if not any(re.search(p, r[2] or "") for p in pats):
            continue
        out.append({
            "lcsc": f"C{r[0]}", "mfr": r[1], "package": r[2] or "",
            "description": (r[3] or "")[:90], "stock": r[4] or 0,
            "price1": r[5], "in_stock": bool(r[6]),
            "assembly": bool(r[7]), "preferred": bool(r[8]),
            "package_match": True,
        })
    return out


def discover_design(netlist: Path) -> tuple[Path | None, Path | None, Path | None]:
    """Дизайн-каталог заказа рядом с нетлистом.

    Нетлист лежит в <заказ>/out/hardware/device.net, а спецификация,
    даташиты и библиотеки — в <заказ>/design. Ключи перекрывают поиск.
    """
    for p in (netlist.parent, *list(netlist.parents)[:4]):
        d = p / "design"
        if not d.is_dir():
            continue
        spec = d / "schematic.json"
        ds = d / "datasheets" / "index.json"
        libs = d / "libs"
        return (spec if spec.is_file() else None,
                ds if ds.is_file() else None,
                libs if libs.is_dir() else None)
    return None, None, None


def load_spec_lcsc(path: Path | None) -> dict[str, dict]:
    """ref → выбор агента из design/schematic.json (LCSC и паспорт).

    Явный LCSC — единственный достоверный способ связать схему с
    конкретной деталью: агент скачал под неё символ, корпус и тело,
    поэтому такой выбор приоритетнее любого подбора по номиналу.
    """
    if not path or not path.is_file():
        return {}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as e:
        print(f"спецификация {path} не читается: {e}")
        return {}
    out: dict[str, dict] = {}
    for p in doc.get("parts", []):
        ref = str(p.get("ref") or "").strip()
        if not ref:
            continue
        raw = str(p.get("lcsc") or "").strip().upper().lstrip("C")
        out[ref] = {
            "lcsc": f"C{raw}" if raw.isdigit() else "",
            "mpn": str(p.get("mpn") or ""),
            "manufacturer": str(p.get("manufacturer") or ""),
        }
    return out


def load_datasheet_index(path: Path | None) -> dict[str, dict]:
    """LCSC → паспорт и файл даташита (этот индекс пишет part_fetch)."""
    if not path or not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return {}


def lcsc_row(con, lcsc: str) -> dict:
    """Строка каталога по точному коду LCSC — артикул выбран агентом."""
    if not re.fullmatch(r"C\d+", lcsc or ""):
        return {}
    r = con.execute(
        "SELECT mfr, package, description, stock, price1, in_stock, "
        "       is_basic, is_preferred FROM part WHERE lcsc = ?",
        (int(lcsc[1:]),)).fetchone()
    if not r:
        return {}
    return {"mfr": r[0] or "", "package": r[1] or "",
            "description": (r[2] or "")[:90], "stock": r[3] or 0,
            "price1": r[4], "in_stock": bool(r[5]),
            "assembly": bool(r[6]), "preferred": bool(r[7])}


def main() -> int:
    ap = argparse.ArgumentParser(description="подбор замен по локальной базе")
    ap.add_argument("--netlist", default="",
                    help="нетлист KiCad (.net); умолчание — бриг-приёмник")
    ap.add_argument("--out", default="",
                    help="кудa писать resolved_parts.json")
    ap.add_argument("--db", default="",
                    help="локальная база производных таблиц jlcsearch.db")
    ap.add_argument("--models", default="",
                    help="каталог STL-моделей корпусов")
    ap.add_argument("--spec", default="",
                    help="design/schematic.json: явные LCSC агентов")
    ap.add_argument("--datasheets", default="",
                    help="design/datasheets/index.json: паспорта и даташиты")
    ap.add_argument("--libs", default="",
                    help="design/libs: символы/корпуса/3D EasyEDA заказа")
    ap.add_argument("--report", action="store_true",
                    help="подробный отчёт по каждому компоненту")
    a = ap.parse_args()

    # Пути — только параметры; их значения по умолчанию задают константы
    # выше. Всё, что кладут ключи, перекрывает их: скрипт один и тот же
    # для приёмника, фонаря и любого другого изделия.
    global NETLIST, OUT, MODELS, FP_ROOT, DB, SPEC, DS_INDEX, LIBS_DIR
    if a.netlist:
        NETLIST = Path(a.netlist).resolve()
    if a.out:
        OUT = Path(a.out).resolve()
    if a.db:
        DB = Path(a.db).resolve()
    if a.models:
        MODELS = Path(a.models).resolve()

    # Дизайн-каталог заказа: ключи перекрывают, иначе он ищется сам рядом
    # с нетлистом (…/out/hardware/device.net → …/design/…).
    auto_spec, auto_ds, auto_libs = discover_design(NETLIST)
    SPEC = Path(a.spec).resolve() if a.spec else auto_spec
    DS_INDEX = Path(a.datasheets).resolve() if a.datasheets else auto_ds
    LIBS_DIR = Path(a.libs).resolve() if a.libs else auto_libs

    if not DB.is_file():
        print(f"НЕТ {DB} — сначала соберите производные таблицы: "
              f"build_derived.py (заготовка делается один раз, "
              f"конвейер базы не пересобирает)")
        return 1
    if not NETLIST.is_file():
        print(f"НЕТ нетлиста {NETLIST} — сначала make kicad "
              f"(компилятор schematic_from_json)")
        return 1
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)

    explicit = load_spec_lcsc(SPEC)
    ds_index = load_datasheet_index(DS_INDEX)
    if SPEC or DS_INDEX or LIBS_DIR:
        print(f"дизайн заказа: {SPEC or '—'} | {DS_INDEX or '—'} | "
              f"{LIBS_DIR or '—'}")

    comps = parse_netlist()
    # Корпуса из этой же схемы, для которых модель нашлась: по ним ищем
    # замену, когда у исходного корпуса модели нет.
    modelled_footprints = {c["footprint"] for c in comps
                           if footprint_has_model(c.get("footprint", ""))}
    resolved: dict[str, dict] = {}
    unresolved: list[str] = []
    substituted: list[str] = []
    picked_explicit: list[str] = []
    no_model_explicit: list[str] = []

    for c in comps:
        ref, fp = c["ref"], c.get("footprint", "")
        choice = explicit.get(ref, {})
        if choice.get("lcsc"):
            # Деталь уже выбрана агентом: part_fetch скачал под этот LCSC
            # символ, корпус и тело. Подбор по номиналу тут вреден — он
            # мог бы принести чужой корпус и рассогласовать схему с платой.
            row = lcsc_row(con, choice["lcsc"])
            dsi = ds_index.get(choice["lcsc"], {})
            mfr = (row.get("mfr") or choice.get("mpn")
                   or dsi.get("mpn") or "")
            has_model = footprint_has_model(fp)
            resolved[ref] = {
                "jlcpcb": [choice["lcsc"]],
                "mfr": mfr,
                "manufacturer": (dsi.get("manufacturer")
                                 or choice.get("manufacturer") or ""),
                "package": row.get("package") or dsi.get("package", ""),
                "description": row.get("description", ""),
                "stock": row.get("stock"),
                "price1": row.get("price1"),
                "assembly": bool(row.get("assembly")),
                "preferred": bool(row.get("preferred")),
                "candidates": 1,
                "how": "из схемы (выбор агента)",
                "source": "schematic",
                "datasheet": dsi.get("file", ""),
                "footprint_has_model": has_model,
                "moved_for_model": False,
            }
            picked_explicit.append(f"{ref}: {choice['lcsc']} {mfr} ({fp})")
            if not has_model:
                no_model_explicit.append(
                    f"{ref}: {choice['lcsc']} — у корпуса {fp} локального "
                    f"тела нет (part_fetch его мог не скачать)")
            continue
        lib = fp.partition(":")[0]
        value = (c.get("value") or "").strip()
        pats = package_patterns(fp)
        cands: list[dict] = []
        how = ""

        # Порядок повторяет штатный движок: сначала артикул, если он есть,
        # затем номинал, затем один корпус.
        if value and not is_nominal(value):
            cands = find_by_mpn(con, value, pats)
            if cands:
                how = "по артикулу"

        if not cands:
            rule = RULES.get(lib)
            if not rule:
                unresolved.append(
                    f"{ref}: нет правила для библиотеки {lib or '?'} "
                    f"(Value «{value or '—'}»)")
                continue
            table, column, read = rule
            if column and value:
                cands = find_by_nominal(con, table, column, read, value, pats)
                how = f"по номиналу ({table}.{column})"
            else:
                cands = find_by_package(con, table, pats)
                how = f"по корпусу ({table})"
            if not cands:
                unresolved.append(
                    f"{ref}: нет в базе ({table}, корпус {fp}, "
                    f"номинал {value or '—'})")
                continue

        # Порядок выбора тот же, что у штатного движка: базовые детали
        # вперёд (у него сортировка по is_basic), плюс совпадение корпуса.
        cands.sort(key=lambda r: (not r["package_match"],
                                  -int(bool(r["assembly"] or r["preferred"])),
                                  -int(bool(r["in_stock"])),
                                  -r["stock"], str(r["mfr"] or "")))
        pick = cands[:3]

        # Требование: сначала MPN, а если под его корпус 3D-модели нет,
        # подобрать ту, под которую модель нашлась. Модели лежат по корпусам
        # KiCad, а не по артикулам, поэтому сверяется корпус.
        has_model = footprint_has_model(fp)
        moved = False
        if not has_model:
            with_model = [r for r in cands
                          if any(package_matches(r["package"], fp2)
                                 for fp2 in modelled_footprints)]
            if with_model:
                pick = with_model[:3]
                moved = True
            substituted.append(
                f"{ref}: у корпуса {fp} модели нет; "
                + (f"взят {pick[0]['lcsc']} ({pick[0]['package']})"
                   if moved
                   else "подходящего корпуса с моделью в схеме нет"))

        resolved[ref] = {
            "jlcpcb": [r["lcsc"] for r in pick],
            "mfr": pick[0]["mfr"],
            "package": pick[0]["package"],
            "description": pick[0]["description"],
            "stock": pick[0]["stock"],
            "price1": pick[0]["price1"],
            "assembly": bool(pick[0]["assembly"]),
            "preferred": bool(pick[0]["preferred"]),
            "candidates": len(cands),
            "how": how,
            "footprint_has_model": has_model,
            "moved_for_model": moved,
        }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"parts": resolved}, ensure_ascii=False, indent=1))

    ok = len(resolved)
    print(f"подобрано {ok} из {len(comps)}")
    print(f"файл: {OUT}")
    if picked_explicit:
        print(f"\nвыбрано агентами — LCSC из схемы ({len(picked_explicit)}):")
        for s in picked_explicit:
            print(f"   {s}")
    if no_model_explicit:
        print(f"\nвыбранные агентами без локального тела "
              f"({len(no_model_explicit)}):")
        for s in no_model_explicit:
            print(f"   {s}")
    if unresolved:
        print(f"\nне подобрано ({len(unresolved)}):")
        for u in unresolved:
            print(f"   {u}")
    if substituted:
        print(f"\nкорпуса без локальной 3D-модели ({len(substituted)}):")
        for s in substituted[:10]:
            print(f"   {s}")

    if a.report:
        print()
        for ref in sorted(resolved):
            r = resolved[ref]
            print(f"  {ref:<6} {','.join(r['jlcpcb']):<26} {str(r['mfr'])[:26]:<28}"
                  f" {r['package'][:18]:<20} вариантов {r['candidates']:<4}"
                  f" склад {r['stock']}")
    return 0 if not unresolved else 0


if __name__ == "__main__":
    sys.exit(main())
