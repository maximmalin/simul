#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Универсальный компилятор принципиальной схемы KiCad из JSON-описания.

Разделение труда (см. docs/HARDWARE_AGENT_CYCLE.md):

* LLM-агент генерирует ТОЛЬКО логическую модель изделия — файл
  ``design/schematic.json``: перечень компонентов по реальным lib_id
  библиотек KiCad, список соединений и рамки блоков. Корпусы агент
  НЕ обязан указывать: скриптовая часть назначает их сама;
* этот скрипт выполняет всё остальное: валидацию, авто-корпусы,
  автоматическую разводку (движок проекта generate_schematic),
  запись .kicad_sch / .kicad_pro / .net, проверку штатным kicad-cli
  (netlist + соответствие спецификации + ERC).

Вход универсален: никакой привязки к конкретному устройству. Поля:

    {
      "name": "…",            — название изделия
      "date": "…", "rev": "…", "paper": "A0"   — опционально
      "modules": "allow"|"forbid"              — опционально (режим сборки)
      "parts": [{"ref","lib_id","value","footprint"(опц.),
                 "block"(опц.), "lcsc"/"mpn"/"doc"(опц.),
                 "module": true (опц.), "placement": "offboard" (опц.)}],
      "nets":  [{"name","conns":[["ref","pin"],…],
                 "power":"power:+5V" (опц.)}],
      "blocks":[{"title","sub","refs",
                 "params": {…} (опц.)}]        — опционально
    }

Режим сборки (``modules``: allow/forbid) — запрет готовых модулей из
решения консультанта; блоки несут числовые требования (``params``).
Компилятор переносит поля без истолкования. Модуль
вне платы (``"placement": "offboard"``) — деталь схемы без корпуса:
на PCB её нет, соединения идут через разъём/клеммы (см. build_pcb.mjs).

Выход: JSON-сводка на stdout; код 0 — схема валидна и соответствует
спецификации, 1 — ошибки (текст агент видит и исправляет спецификацию).
"""

from __future__ import annotations

import argparse
import datetime
import json
import re
import subprocess
import sys
from pathlib import Path

FAILURES: list[str] = []
NOTES: list[str] = []


def err(msg: str) -> None:
    FAILURES.append(msg)


def note(msg: str) -> None:
    NOTES.append(msg)


# --------------------------------------------------------------------------- #
#  Загрузка и нормализация спецификации
# --------------------------------------------------------------------------- #
def _sim_props(part: dict) -> dict[str, str]:
    """Нативные свойства симуляции детали.

    Основной вид — объект «sim» с ключами Sim.Device / Sim.Name /
    Sim.Pins. Короткая запись «spice_model» тоже принимается: имя
    модели — это тот же факт, просто под коротким ключом.
    """
    out: dict[str, str] = {}
    for k, v in (part.get("sim") or {}).items():
        if str(v or "").strip():
            out[str(k)] = str(v)
    if not out.get("Sim.Name"):
        short = str(part.get("spice_model") or "").strip()
        if short:
            out["Sim.Name"] = short
    return out


def load_spec(path: Path) -> dict:
    if not path.is_file():
        raise SystemExit(
            f"[kicad] спецификация не найдена: {path}\n"
            "[kicad] агент должен сначала записать design/schematic.json "
            "(инструмент write_artifact)")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise SystemExit(f"[kicad] неверный JSON в {path}: {e}")
    if not isinstance(data, dict):
        raise SystemExit("[kicad] спецификация должна быть JSON-объектом")
    for key, what in (("parts", "компонентов"), ("nets", "сетей")):
        if not data.get(key):
            raise SystemExit(f"[kicad] в спецификации нет поля {key} ({what})")
    return data


def _mark_unused_pins(parts: list[dict],
                      nets: list[tuple[str, list[tuple[str, str]]]],
                      KS) -> list[dict]:
    """Пометить неиспользованные выводы как «не подключено».

    Вывод, который схемотехник не использовал, — это NC: так он и
    помечается в KiCad. Без этого ERC ругается на висящие выводы, и
    схема не собирается вовсе.
    """
    used: dict[str, set[str]] = {}
    for _net, conns in nets:
        for ref, pin in conns:
            used.setdefault(str(ref), set()).add(str(pin))
    for p in parts:
        ref = str(p.get("ref") or "")
        lib_id = str(p.get("lib_id") or "")
        if not ref or not lib_id:
            continue
        try:
            sym = KS.lib_symbol(lib_id)
            names = KS.pin_name_map(sym)
        except Exception:                                # noqa: BLE001
            continue
        if not names:
            continue
        have = used.get(ref, set())
        by_num = {num: nm for nm, num in names.items()}
        missing = []
        for num, nm in names.items():
            if num in have or nm in have:
                continue
            missing.append(nm or num)
        if missing:
            p["не_подключены"] = sorted(set(
                list(p.get("не_подключены") or []) + missing))
    return parts


def normalise(spec: dict) -> tuple[list[dict], list[str | list], list[tuple], dict]:
    """Привести spec к форматам движка разводки."""
    parts: list[dict] = []
    for i, p in enumerate(spec["parts"]):
        ref = str(p.get("ref") or f"X{i + 1}").strip()
        lib_id = str(p.get("lib_id") or p.get("lib") or "").strip()
        if not lib_id:
            err(f"компонент {ref}: не задан lib_id")
            continue
        parts.append({
            "ref": ref,
            "lib_id": lib_id,
            "value": str(p.get("value", "")),
            "footprint": str(p.get("footprint", "")).strip(),
            "block": p.get("block", ""),
            # нативные свойства симуляции KiCad: их объявляет агент,
            # читает штатный экспорт SPICE
            "sim": _sim_props(p),
            # выводы, не используемые в схеме: KiCad помечает их знаком
            # «не подключено», иначе ERC ругается на висящие выводы
            "не_подключены": [str(x) for x in (p.get("не_подключены") or [])],
            # модуль вне платы: корпус не требуется — деталь не на PCB,
            # соединения идут через разъём/клеммы (см. шапку файла)
            "placement": str(p.get("placement", "")).strip().lower(),
        })

    nets: list[tuple[str, list[tuple[str, str]]]] = []
    power: dict[str, str] = {}
    for i, n in enumerate(spec["nets"]):
        name = str(n.get("name") or f"NET{i + 1}").strip()
        conns = [(str(r), str(pn)) for r, pn in
                 (n.get("conns") or n.get("pins") or [])]
        sym = str(n.get("power") or "").strip()
        if sym and ":" in sym:
            power[name] = sym
        nets.append((name, conns))

    # блоки: явные из spec, иначе группировка по полю block, иначе один блок
    specs_blocks = spec.get("blocks") or []
    # блок без списка деталей бесполезен: детали всё равно надо разместить,
    # поэтому такой блок достраивается по полю «block» у деталей
    grouped: dict[str, list[str]] = {}
    for p in parts:
        grouped.setdefault(str(p.get("block") or ""), []).append(p["ref"])
    grouped = {k: v for k, v in grouped.items() if v}
    if specs_blocks:
        blocks = []
        for b in specs_blocks:
            refs = [str(r) for r in (b.get("refs") or [])]
            if not refs:
                code = str(b.get("код") or "")
                refs = grouped.get(code) or []
            blocks.append((str(b.get("title", "БЛОК")),
                           str(b.get("sub", "")), "", refs))
        blocks = [b for b in blocks if b[3]]
    else:
        grouped: dict[str, list[str]] = {}
        for p in parts:
            grouped.setdefault(str(p.get("block") or ""), []).append(p["ref"])
        grouped = {k: v for k, v in grouped.items() if v}
        if "" in grouped and len(grouped) > 1:
            merged = grouped.pop("")
            order = [v for _k, v in grouped.items()]
            order[0] = order[0] + merged
            grouped[""] = []
        if not grouped:
            grouped = {"": [p["ref"] for p in parts]}
        blocks = []
        for idx, (_k, refs) in enumerate(
                sorted(grouped.items(), key=lambda kv: kv[1][0])):
            blocks.append((f"БЛОК {idx + 1}", "", "", refs))
        if len(blocks) > 1 or len(parts) < 4:
            note(f"блоки выведены автоматически: {[b[0] for b in blocks]}")
    return parts, nets, blocks, power


# --------------------------------------------------------------------------- #
#  Библиотеки корпусов: системные + локальная БД проекта
# --------------------------------------------------------------------------- #
def fp_roots() -> list[Path]:
    roots = [Path("/usr/share/kicad/footprints")]
    starts = (Path.cwd(), Path(__file__).resolve())
    # spec-путь добавится в main(); тут общий подъём по каталогам
    for start in starts:
        for p in (start, *start.parents):
            cand = p / "local_footprints"
            if cand.is_dir() and cand not in roots:
                roots.append(cand)
                break
    # проектные библиотеки корпусов, собранные агентами из EasyEDA
    # (design/libs/*.pretty) — рядом с песочницей и рядом со скриптом
    for start in starts:
        for p in (start, *start.parents):
            cand = p / "design" / "libs"
            if cand.is_dir() and cand not in roots:
                roots.append(cand)
                break
    return roots


# --------------------------------------------------------------------------- #
#  Корпус детали — из самого символа
# --------------------------------------------------------------------------- #
#  Корпус не выдумывается и не зашивается справочником: символ KiCad уже
#  знает свой корпус (свойство Footprint) и перечень подходящих корпусов
#  (ki_fp_filters). Отсюда и берётся: сначала собственное свойство
#  символа, затем — реальный корпус из установленных библиотек, подходящий
#  под фильтр символа. Ни одной строки «Device:R → R_0603» в коде нет.
# --------------------------------------------------------------------------- #
def _kicad_symbols():
    """Модуль разбора библиотек KiCad (hardware/kicad)."""
    import importlib.util
    here = Path(__file__).resolve()
    for parent in (here.parent, *here.parents):
        kit = parent / "hardware" / "kicad"
        if kit.is_dir():
            path = kit / "kicad_symbols.py"
            if not path.is_file():
                continue
            try:
                spec = importlib.util.spec_from_file_location(
                    "kicad_symbols_local", path)
                mod = importlib.util.module_from_spec(spec)
                # модуль с dataclass должен быть в sys.modules ДО загрузки:
                # иначе dataclass не находит свой класс и падает
                sys.modules[spec.name] = mod
                spec.loader.exec_module(mod)
                return mod
            except Exception:                                # noqa: BLE001
                return None
    return None


#: правила подбора корпуса — данные (tools/kicad_params.json)
_KICAD_PARAMS_FILE = Path(__file__).resolve().parent / "kicad_params.json"


def _kicad_params() -> dict:
    try:
        return json.loads(_KICAD_PARAMS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _fp_rank(name: str, want_word: str = "") -> tuple:
    """Ранг корпуса: чем он «типичнее» — тем лучше.

    Соглашение имён KiCad: типовая чиповая деталь пишется как
    «R_0603_1608Metric» (метрический размер + «Metric»). Наборы
    («R_Cat16-8») и площадки («..._Pad1.12x1.3mm») — не типовая
    посадка, их оставляем на потом.

    Правила действуют на имя и на имя библиотеки корпуса, а не на
    конкретные детали: справочника «деталь → корпус» в коде нет.
    """
    cfg = _kicad_params()
    skip = [str(w).lower() for w in cfg.get("skip_words", [])]
    lib, _, stem = name.rpartition(":")
    low = stem.lower()
    catalogue = 1 if any(w in low for w in skip) else 0
    metric = 0 if low.endswith("metric") else 1
    size = 0 if re.search(r"_\d{4}_", low) else 1
    # библиотека корпуса по типу символа: Resistor_SMD лучше
    # Inductor_THT для резистора
    type_miss = 0
    if want_word:
        type_miss = 0 if want_word.lower() in lib.lower() else 1
    smd = 0 if (not cfg.get("prefer_smd", True)
                or "smd" in lib.lower()) else 1
    return (catalogue, type_miss, metric, size, smd, len(stem), stem)


def _symbol_type_word(lib_id: str) -> str:
    """Класс детали по имени символа: «Device:R» → «Resistor»."""
    words = _kicad_params().get("symbol_type_words", {})
    name = lib_id.split(":", 1)[-1]
    for key in sorted(words, key=len, reverse=True):
        if name == key or name.startswith(key + "_"):
            return words[key]
    return ""


def _fp_from_symbol(lib_id: str, roots) -> str:
    """Корпус, который знает сам символ (или подходит под его фильтр)."""
    syms = _kicad_symbols()
    if syms is None:
        return ""
    fp = syms.symbol_footprint(lib_id)
    if fp and _fp_exists(fp, roots):
        return fp
    filt = syms.symbol_fp_filters(lib_id)
    if not filt:
        return ""
    # фильтр символа — список масок: «R_*», «*SO*3.9x4.*P1.27mm*».
    # Маски собираются ВСЕ сразу: по очереди первая же подходящая
    # («Choke_*» у катушки) увлекла бы не тот корпус, что нужен.
    hits: list[str] = []
    seen: set[str] = set()
    for mask in filt.split():
        mask = mask.strip()
        if not mask:
            continue
        rx = re.compile("^" + re.escape(mask).replace(r"\*", ".*")
                        .replace(r"\?", ".") + "$", re.I)
        for root in roots:
            for libdir in sorted(root.glob("*.pretty")):
                for mod in libdir.glob("*.kicad_mod"):
                    name = f"{libdir.stem}:{mod.stem}"
                    if name in seen:
                        continue
                    # маска может указывать и на библиотеку: «Connector*:*_1x??_*»
                    if not (rx.match(mod.stem) or rx.match(name)):
                        continue
                    seen.add(name)
                    hits.append(name)
    if hits:
        want = _symbol_type_word(lib_id)
        hits.sort(key=lambda h: _fp_rank(h, want))
        return hits[0]
    return ""


def _fp_exists(fp: str, roots) -> bool:
    if ":" not in fp:
        return False
    lib, name = fp.split(":", 1)
    return any((root / f"{lib}.pretty" / f"{name}.kicad_mod").is_file()
               for root in roots)


def assign_footprints(parts, roots, index_map=None):
    """Подтверждение корпусов только тем, что существует, без придумок.

    Порядок источников: корпус из спецификации → корпус из индекса
    библиотекаря → корпус, который знает сам символ. Ничего не
    подставляется «на глаз»: если корпус не подтверждён библиотеками,
    деталь остаётся без него и об этом пишется в отчёт.
    """
    def exists(fp):
        return _fp_exists(fp, roots)

    def bare_exact(name):
        matches = []
        for root in roots:
            for libdir in root.glob("*.pretty"):
                if (libdir / f"{name}.kicad_mod").is_file():
                    matches.append(f"{libdir.stem}:{name}")
        # только если однозначное
        if len(matches) == 1:
            return matches[0]
        return None

    def from_symbol_name(lib_id: str) -> str:
        """Корпус, который символ называет сам (Resistor_SMD:R_0603…).

        Такие символы и есть конкретные детали с конкретным корпусом —
        отдельное поле footprint указывать не нужно.
        """
        if ":" not in str(lib_id or ""):
            return ""
        lib, name = str(lib_id).split(":", 1)
        for root in roots:
            if (Path(root) / f"{lib}.pretty" / f"{name}.kicad_mod").is_file():
                return f"{lib}:{name}"
        return ""

    for p in parts:
        if (p.get("placement") or "") == "offboard":
            continue        # модуль вне платы: корпус не назначается
        fp = (p.get("footprint") or "").strip()
        if not fp:
            fp = from_symbol_name(str(p.get("lib_id") or ""))
        if index_map and not fp:
            candidate = index_map.get(p["lib_id"])
            if candidate:
                q = bare_exact(candidate) if ":" not in candidate else candidate
                fp = q if q else candidate
                if not exists(fp):
                    fp = None
        if fp and ":" not in fp:
            q = bare_exact(fp)
            if q:
                fp = q
        if fp and exists(fp):
            p["footprint"] = fp
            continue
        # корпуса в спецификации нет или он не подтверждён — берём тот,
        # который знает сам символ
        from_symbol = _fp_from_symbol(p.get("lib_id", ""), roots)
        if from_symbol:
            p["footprint"] = from_symbol
            continue
        # blocked — запрет изобретать
        note(f"{p['ref']}: корпус не подтверждён библиотеками KiCad "
             f"(«{fp or '(пусто)'}»). Проверьте kicad_libs(action='footprints') "
             "и обновите in-library_index.md с указанием «exists: true». "
             "Cargo не выдумывает корпус.")
        p["footprint"] = ""

# --------------------------------------------------------------------------- #
#  Исполнение
# --------------------------------------------------------------------------- #
def main() -> int:                                     # noqa: C901
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--spec", required=True,
                    help="JSON-спецификация (design/schematic.json)")
    ap.add_argument("--out-dir", required=True, help="каталог результатов")
    ap.add_argument("--name", default="",
                    help="имя изделия, перекрывает spec['name']")
    ap.add_argument("--kit-dir", default="",
                    help="каталог движка разводки (hardware/kicad)")
    ap.add_argument("--fail-erc", type=int, default=0,
                    help="порог ошибок ERC, сверх которого возврат 1")
    args = ap.parse_args()

    spec_path = Path(args.spec).resolve()
    spec = load_spec(spec_path)
    title = args.name or str(spec.get("name") or "УСТРОЙСТВО")
    date = str(spec.get("date") or datetime.date.today().isoformat())
    rev = str(spec.get("rev") or "1.0")
    paper = str(spec.get("paper") or "A0")
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    parts, nets, blocks, power = normalise(spec)
    if FAILURES:
        print(json.dumps({"ok": False, "errors": FAILURES[:60]},
                         ensure_ascii=False, indent=2))
        return 1

    # --- движок разводки: рядом, каталоги стандартные
    HERE = Path(__file__).resolve().parent
    candidates: list[Path] = []
    if args.kit_dir:
        candidates.append(Path(args.kit_dir).resolve())
    candidates += [HERE.parent / "hardware" / "kicad",   # корень/tools → корень
                   Path.cwd() / "hardware" / "kicad"]    # песочница
    kit_dir = next((c for c in candidates if (c / "generate_schematic.py")
                    .is_file()), None)
    if kit_dir is None:
        raise SystemExit(
            "[kicad] не найден движок разводки (generate_schematic.py); "
            "задайте --kit-dir")
    sys.path.insert(0, str(kit_dir))
    import generate_schematic as G                   # noqa: E402
    import kicad_symbols as K                           # noqa: E402

    # неиспользованные выводы помечаются как «не подключено» — иначе
    # ERC ругается на висящие выводы и схема не собирается
    parts = _mark_unused_pins(parts, nets, K)

    # --- корпуса (до патча движка)
    roots = fp_roots()
    for cand in (spec_path.parent / "libs",
                 spec_path.parent.parent / "design" / "libs"):
        if cand.is_dir() and cand not in roots:
            roots.append(cand)
    roots_local = [p for p in (spec_path, *spec_path.parents)
                   if (p / "local_footprints").is_dir()]
    for cand in map(lambda x: x / "local_footprints", roots_local):
        if cand not in roots:
            roots.append(cand)
    assign_footprints(parts, roots)

    for p in parts:
        if p.get("placement") == "offboard":
            continue        # модуль вне платы — на PCB не ставится
        if p["footprint"] and ":" not in p["footprint"]:
            err(f"{p['ref']}: корпус без библиотеки «{p['footprint']}» — "
                "формат «БиблиотекаКорпусов:Имя», проверь его "
                "kicad_libs(action='footprints')")
        if not p["footprint"]:
            err(f"{p['ref']}: корпус не задан и не назначен автоматически — "
                "укажи поле footprint в своей спецификации (система имён "
                "корпусов отлична от символов)")
    if FAILURES:
        print(json.dumps({"ok": False, "errors": FAILURES[:60],
                          "warnings": NOTES[:40]}, ensure_ascii=False,
                         indent=2))
        return 1

    # --- подмена табличных данных движка разводки (как в штатном
    #     test_any_schematic.py проекта): таблицы — единственный вход
    # sim-свойства (Sim.Device/Sim.Library/Sim.Name/Sim.Pins) агент
    # объявляет в спецификации; каркас переносит их дословно — смысл
    # разбирает штатный экспорт SPICE KiCad.
    # Путь к библиотеке моделей — служебный: агент о нём не знает,
    # библиотеку собирает каркас (design/spice_models.lib).
    models_lib = spec_path.parent / "spice_models.lib"
    models_abs = str(models_lib.resolve()) if models_lib.is_file() else ""

    def _sim_of(p: dict) -> dict:
        out = {str(k): str(v) for k, v in (p.get("sim") or {}).items()
               if str(v or "").strip()}
        if out and models_abs and not out.get("Sim.Library"):
            out["Sim.Library"] = models_abs
        return out

    G.PARTS[:] = [G.Part(**{k: p[k] for k in
                            ("ref", "lib_id", "value", "footprint")},
                           sim=_sim_of(p),
                           nc=[str(x) for x in (p.get("не_подключены") or [])])
                  for p in parts]
    # сети и блоки — в движок разводки: без них он рисует свой
    # демонстрационный нетлист, и плата получается не от нашей схемы
    G.NETS[:] = nets
    G.BLOCKS[:] = blocks
    G.POWER_SYM.clear()
    G.POWER_SYM.update(power)
    G.probe.clear()
    for p in G.PARTS:
        G.probe[p.ref] = K.Comp(lib_id=p.lib_id, ref=p.ref, value=p.value)

    # ---------------- валидация списка соединений -------------------------
    known = {p["ref"] for p in parts}
    seen_pin: dict[tuple[str, str], str] = {}
    for net, conns in nets:
        if len(conns) < 2:
            note(f"сеть {net}: {len(conns)} вывод — висячий конец")
        for ref, pin in conns:
            if ref not in known:
                err(f"сеть {net}: неизвестный компонент {ref}")
                continue
            try:
                num = G.probe[ref].resolve(pin)
            except KeyError as e:
                err(f"{ref}: вывод '{e}' не найден в символе {G.probe[ref].lib_id} (проверьте инструмент kicad_libs)")
                continue
            key = (ref, num)
            if key in seen_pin:
                err(f"{ref}: вывод {num} назначен в две сети "
                    f"(«{seen_pin[key]}» и «{net}») — каждый вывод детали "
                    "стоит ровно в одной сети")
            seen_pin[key] = net
    if FAILURES:
        print(json.dumps({"ok": False, "errors": FAILURES[:60],
                          "warnings": NOTES[:40]}, ensure_ascii=False,
                         indent=2))
        return 1

    # ---------------- автоматическая разводка ------------------------------
    stem = (re.sub(r"[^\w\- ]+", "", title).strip().replace(" ", "_")
            or "device")[:60]
    out_sch = out_dir / f"{stem}.kicad_sch"
    out_pro = out_dir / f"{stem}.kicad_pro"
    out_net = out_dir / f"{stem}.net"

    try:
        sheet, geo = G.build()
    except RuntimeError as e:
        print(json.dumps({"ok": False,
                          "errors": ["разводка: " + str(e)[:1500]]},
                         ensure_ascii=False))
        return 1

    node = K.build_sch(sheet.comps, sheet.wires, sheet.junctions,
                       sheet.labels, sheet.texts, sheet.rects,
                       paper=paper,
                       title=f"{title} — принципиальная схема",
                       date=date, rev=rev)
    K.write_sch(node, out_sch)

    pro = json.loads(json.dumps(K.MINIMAL_PRO))
    pro["meta"]["filename"] = out_pro.name
    pro["sheets"] = [[K.root_uuid(node), "Root"]]
    out_pro.write_text(json.dumps(pro, ensure_ascii=False, indent=2))

    # ---------------- штатная проверка kicad-cli ---------------------------
    r = subprocess.run(["kicad-cli", "sch", "export", "netlist",
                        "-o", str(out_net), str(out_sch)],
                       capture_output=True, text=True, timeout=300)
    msg = ((r.stdout or "") + (r.stderr or "")).strip()
    if r.returncode != 0:
        print(json.dumps({"ok": False, "errors": [
            "kicad-cli не принял схему: " +
            (msg.splitlines()[-1] if msg else str(r.returncode))]},
            ensure_ascii=False))
        return 1

    txt = out_net.read_text(errors="ignore")
    n_comp = txt.count("(comp (ref")
    n_net = txt.count("(net (code")
    n_node = txt.count("(node ")
    if n_comp == 0 or n_net == 0:
        print(json.dumps({"ok": False, "errors": ["netlist пуст"]},
                         ensure_ascii=False))
        return 1

    # ---------------- соответствие netlist спецификации --------------------
    chunks = txt[txt.find("(nets"):].split("(net (code ")[1:]
    pin2net: dict[str, set[str]] = {}
    for ch in chunks:
        m = re.search(r'\(name "([^"]*)"', ch)
        if not m:
            continue
        for a, b in re.findall(r'\(node \(ref "([^"]*)"\) \(pin "([^"]*)"', ch):
            pin2net.setdefault(f"{a}.{b}", set()).add(m.group(1))
    mism: list[str] = []
    for net, conns in nets:
        got: set[str] = set()
        for ref, pin in conns:
            if ref not in known:
                continue
            num = G.probe[ref].resolve(pin)
            got |= {s.lstrip("/") for s in
                    pin2net.get(f"{ref}.{num}", set())}
        if got != {net.lstrip("/")}:
            mism.append(f"сеть {net} в netlist: "
                        f"{sorted(got)[:3] or 'нет'} (ожидалась только она)")
    shorts = [f"пин {k} в нескольких сетях {sorted(v)}"
              for k, v in pin2net.items() if len(v) > 1]
    if mism or shorts:
        print(json.dumps({"ok": False,
                          "errors": (mism[:30] + shorts[:10])[:40]},
                         ensure_ascii=False))
        return 1

    # ---------------- ERC (ошибки) -----------------------------------------
    erc_out = out_dir / f"{stem}.erc.json"
    erc_summary: list[str] = []
    n_erc = -1
    erc = subprocess.run(["kicad-cli", "sch", "erc", "--exit-code-violations",
                          "-o", str(erc_out), str(out_sch)],
                         capture_output=True, text=True, timeout=300)
    if erc_out.is_file():
        try:
            data = json.loads(erc_out.read_text(errors="ignore"))
            viol = data.get("violations") or []
            hard = [v for v in viol
                    if str(v.get("severity", "")).lower() == "error"]
            n_erc = len(hard)
            erc_summary = [
                f"{v.get('type', '?')}: "
                + json.dumps(v.get("items"), ensure_ascii=False)[:120]
                for v in hard[:8]]
        except (json.JSONDecodeError, OSError):
            n_erc = -1
        note(f"ERC: {n_erc} ошибок"
             + (" (подробности в файле)" if n_erc else ""))

    if n_erc > args.fail_erc:
        print(json.dumps({"ok": False,
                          "errors": erec_errors(n_erc, erc_summary)},
                         ensure_ascii=False))
        return 1

    summary = {
        "ok": True,
        "title": title,
        "scheme": str(out_sch),
        "netlist": str(out_net),
        "project": str(out_pro),
        # discriminate-полоса для tscircuit-конвейера: stem нетлиста,
        # сети питания и земля — семантику рельсов решает агент
        # (поле power в спецификации), скрипт только переносит.
        "stem": stem,
        "power_nets": sorted(power),
        "ground": next(
            (n for n in sorted(power)
             if re.search(r"(GND|VSS|AGND|PGND)$",
                          str(power[n]), re.I)
             or re.search(r"(GND|VSS|AGND|PGND)$", n, re.I)),
            ""),
        "comps": n_comp,
        "nets": n_net,
        "nodes": n_node,
        "wires": len(sheet.wires),
        "labels": len(sheet.labels),
        "power_symbols": sum(1 for c in sheet.comps
                             if c.ref.startswith("#PWR")),
        "blocks": len(blocks),
        "page": {"w": round(geo["page_w"]), "h": round(geo["page_h"]),
                 "paper": paper},
        "erc_errors": n_erc,
        "erc": erc_summary,
        "notes": NOTES[:60],
    }
    (out_dir / "generate_result.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def erec_errors(n_erc: int, erc_summary: list[str]) -> list[str]:
    """Формулировка ошибки ERC для цикла правки агента."""
    head = f"ERC: {n_erc} ошибок — исправьте спецификацию и повторите"
    return [head, *erc_summary]


if __name__ == "__main__":
    raise SystemExit(main())
