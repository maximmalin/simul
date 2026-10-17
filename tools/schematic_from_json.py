#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Универсальный компилятор принципиальной схемы KiCad из JSON-описания.

Разделение труда (см. docs/HARDWARE_AGENT_CYCLE.md):

* LLM-агент генерирует ТОЛЬКО логическую модель изделия — файл
  ``design/schematic.json``: перечень компонентов по реальным lib_id
  библиотек KiCad, список соединений и рамки блоков. Корпусы агент
  НЕ обязан указывать: скриптовая часть назначает их сама;
* этот скрипт выполняет всё остальное: валидацию, авто-корпусы,
  автоматическую разводку (движок проекта generate_brig_receiver),
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
    if specs_blocks:
        blocks = [(str(b.get("title", "БЛОК")), str(b.get("sub", "")), "",
                   [str(r) for r in (b.get("refs") or [])])
                  for b in specs_blocks]
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


#: типовые корпуса по классу компонента — общий справочник (не по устройству)
FAMILY_FP: dict[str, str] = {
    "Device:R":              "Resistor_SMD:R_0603_1608Metric",
    "Device:C":              "Capacitor_SMD:C_0603_1608Metric",
    "Device:C_Feedthrough":  "Capacitor_SMD:C_0603_1608Metric",
    "Device:C_Polarized":    "Capacitor_SMD:CP_Elec_6.3x7.7",
    "Device:D":              "Diode_SMD:D_SOD-123",
    "Device:D_Zener":        "Diode_SMD:D_SOD-123",
    "Device:D_Schottky":     "Diode_SMD:D_SOD-128",
    "Device:LED":            "LED_SMD:LED_0603_1608Metric",
    "Device:L":              "Inductor_SMD:L_0603_1608Metric",
    "Device:FerriteBead":    "Inductor_SMD:L_0603_1608Metric",
    "Device:Fuse":           "Fuse:Fuse_1812_4532Metric",
    "Device:Polyfuse":       "Inductor_SMD:L_0603_1608Metric",
    "Device:Varistor":       "Varistor:RV_Disc_D7mm_W3.4mm_P5mm",
    "Device:Thermistor_NTC": "Resistor_SMD:R_0603_1608Metric",
    "Device:Crystal":        "Crystal:Crystal_SMD_HC49-SD",
    "Device:Battery":        "Battery:BatteryHolder_Keystone_1042_1x18650",
    "Device:Jumper_Row03":   "Connector_PinHeader_2.54mm:PinHeader_1x03_P2.54mm_Vertical",
    "Transistor_BJT:*":      "Package_TO_SOT_SMD:SOT-23",
    "Connector:TestPoint":   "TestPoint:TestPoint_Pad_D1.5mm",
    "Connector_Generic:*":   "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical",
    "Regulator_Linear:*":    "Package_TO_SOT_SMD:SOT-223-3_TabPin2",
    "Relay:*":               "Relay_THT:Relay_1-Form-C_Schrack-RYII_RM3.2mm",
}


def assign_footprints(parts, roots, index_map=None):
    """Подтверждение корпусов только тем, что существует, без придумок."""
    def exists(fp):
        if ":" not in fp:
            return False
        lib, name = fp.split(":", 1)
        return any((root / f"{lib}.pretty" / f"{name}.kicad_mod").is_file()
                   for root in roots)

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

    for p in parts:
        if (p.get("placement") or "") == "offboard":
            continue        # модуль вне платы: корпус не назначается
        fp = (p.get("footprint") or "").strip()
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
    kit_dir = next((c for c in candidates if (c / "generate_brig_receiver.py")
                    .is_file()), None)
    if kit_dir is None:
        raise SystemExit(
            "[kicad] не найден движок разводки (generate_brig_receiver.py); "
            "задайте --kit-dir")
    sys.path.insert(0, str(kit_dir))
    import generate_brig_receiver as G                   # noqa: E402
    import kicad_lib_brig as K                           # noqa: E402

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
    G.PARTS[:] = [G.Part(**{k: p[k] for k in
                            ("ref", "lib_id", "value", "footprint")})
                  for p in parts]
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
