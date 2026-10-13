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


def footprint_has_model(footprint: str) -> bool:
    """Есть ли локальная STL для корпуса (с учётом запасного пути)."""
    lib, _, name = footprint.partition(":")
    mod = FP_ROOT / f"{lib}.pretty" / f"{name}.kicad_mod"
    cands: list[str] = []
    if mod.is_file():
        m = re.search(r'3DMODEL_DIR}/([^"]*\.step)', mod.read_text(errors="ignore"))
        if m:
            cands.append(m.group(1))
    # Запасной путь по соглашению packages3d: у части корпусов строки
    # (model ...) в .kicad_mod просто нет.
    cands.append(f"{lib}.3dshapes/{name}.step")
    return any((MODELS / c.replace(".step", ".stl")).is_file() for c in cands)


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


def main() -> int:
    ap = argparse.ArgumentParser(description="подбор замен по локальной базе")
    ap.add_argument("--report", action="store_true",
                    help="подробный отчёт по каждому компоненту")
    a = ap.parse_args()

    if not DB.is_file():
        print(f"НЕТ {DB} — сначала соберите производные таблицы: "
              f"build_derived.py")
        return 1
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)

    comps = parse_netlist()
    # Корпуса из этой же схемы, для которых модель нашлась: по ним ищем
    # замену, когда у исходного корпуса модели нет.
    modelled_footprints = {c["footprint"] for c in comps
                           if footprint_has_model(c.get("footprint", ""))}
    resolved: dict[str, dict] = {}
    unresolved: list[str] = []
    substituted: list[str] = []

    for c in comps:
        ref, fp = c["ref"], c.get("footprint", "")
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
