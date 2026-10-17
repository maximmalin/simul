#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PCB-паспорт деталей по даташитам: задание, выдержки, проверка.

Зачем. Жизненно важная развилка платы — «стандартный корпус или нет» — не
должна решаться догадкой по имени корпуса. Её решают данные даташита:
агент-читатель по каждой детали извлекает структурированный PCB-блок
(корпус, число выводов, монтаж, шаг, габариты) со ссылкой на страницу и
раздел (провенанс), а эмиттер tscircuit применяет:

  * стандартный корпус (есть генератор в tscircuit) → родной корпус и
    родное 3D-тело, ни KiCad-файлов, ни чужой модели;
  * нестандартный корпус → наш KiCad-путь: локальная пара
    символ/корпус/3D из design/libs (part_fetch).

Этот инструмент делает МЕХАНИЧЕСКУЮ часть и ни одного инженерного
решения не принимает:
  --job    собирает задание: по каждой детали — выдержки из её PDF
           (страницы, таблицы, ключевые строки) и метаданные; пишет
           design/datasheets/pcb_job.json и <даташит>.txt.
  --check  проверяет ответ агента design/parts_pcb.json: покрытие всех
           деталей, схема полей, число выводов против нетлиста, провенанс;
           нормализует package_std в строку-генератор tscircuit.
  --show   сводка паспортов: что уйдёт в эмиттер платы.

Схема полей опирается на практику извлечения из даташитов (kicad-happy,
MIT): у каждой величины — evidence {page, section, confidence, method},
у значения — условия и единицы. PCB-блок — контракт именно для платы;
электрические параметры и модели живут отдельными блоками (sim-стадия,
datasheet2spice).

Запуск (обычно из каталога заказа):

    python3 tools/part_pcb.py --job
    # …агент читает pcb_job.json и пишет design/parts_pcb.json…
    python3 tools/part_pcb.py --check
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# ── ключевые слова, по которым из многостраничного PDF вырезаются
#    выдержки для агента: корпус, механика, выводы, монтаж, размеры.
KEYWORDS = (
    "package", "корпус", "outline", "mechanical", "land pattern",
    "recommended land", "footprint",
    "pin", "вывод", "pitch", "шаг", "mount", "монтаж", "solder", "пайк",
    "dimension", "габарит", "размер", "body", "mm", "sod", "sot", "qfn",
    "soic", "lqfp", "bga", "dpak", "to-", "sc-", "chip", "smd", "tht",
    "through hole", "surface mount",
)

#: типоразмеры EIA (имперские коды) — единственное, что совпадает у всех
#: производителей и всех библиотек корпусов.
EIA_SIZES = ("0201", "0402", "0603", "0805", "1206", "1210",
             "1812", "2010", "2512")

MOUNTINGS = ("smd", "tht", "mixed")


# ═══════════════════════════════════════════════════════════ вспомогательное

def load_json(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:                                   # noqa: BLE001
        return None


def write_json(p: Path, data) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                 encoding="utf-8")


def rel(box: Path, p: Path) -> str:
    try:
        return str(p.relative_to(box))
    except ValueError:
        return str(p)


# ═══════════════════════════════════════════════ сбор задания (режим --job)

def schematic_parts(box: Path) -> list[dict]:
    """Детали из спецификации схемы: ref, lib_id, value, footprint, lcsc.
    Плюс режим детали: module и placement — модуль вне платы на PCB не
    ставится и корпуса не требует."""
    spec = load_json(box / "design/schematic.json")
    out = []
    for p in (spec or {}).get("parts", []) if isinstance(spec, dict) else []:
        if not isinstance(p, dict) or not p.get("ref"):
            continue
        out.append({
            "ref": str(p.get("ref")),
            "lib_id": str(p.get("lib_id") or ""),
            "value": str(p.get("value") or ""),
            "footprint": str(p.get("footprint") or ""),
            "lcsc": str(p.get("lcsc") or "").lstrip("C") and
                    ("C" + str(p.get("lcsc")).lstrip("C")),
            "mpn": str(p.get("mpn") or p.get("mfr") or ""),
            "module": bool(p.get("module")),
            "placement": str(p.get("placement") or "").strip().lower(),
        })
    return out


def resolved_parts(box: Path) -> dict:
    """Подбор деталей конвейера платы: ref -> {lcsc, mpn, datasheet, package}."""
    for cand in (box / "out/hardware/tscircuit/resolved_parts.json",
                 box / "out/hardware/resolved_parts.json"):
        data = load_json(cand)
        if isinstance(data, dict) and isinstance(data.get("parts"), dict):
            return data["parts"]
    return {}


def netlist_pins(box: Path) -> dict[str, int]:
    """ref -> число выводов из нетлиста KiCad (сколько площадок ждать)."""
    net = None
    man = load_json(box / "out/hardware/generate_result.json")
    if isinstance(man, dict) and man.get("netlist"):
        p = box / str(man["netlist"])
        if p.is_file():
            net = p
    if net is None:
        hw = box / "out/hardware"
        cands = sorted(hw.glob("*.net")) if hw.is_dir() else []
        net = cands[0] if cands else None
    if net is None:
        return {}
    txt = net.read_text(errors="ignore")
    out: dict[str, set] = {}
    # в KiCad-нетлисте выводы — это узлы: (node (ref "X") (pin "N"))
    for m in re.finditer(r'\(node\s+\(ref\s+"([^"]+)"\)\s+\(pin\s+"([^"]+)"',
                         txt):
        out.setdefault(m.group(1), set()).add(m.group(2))
    return {ref: len(pins) for ref, pins in out.items() if pins}


def find_pdf(box: Path, part: dict, resolved: dict) -> Path | None:
    """Даташит детали: сначала путь из подбора, затем по LCSC/MPN в каталоге."""
    ddir = box / "design/datasheets"
    rec = resolved.get(part["ref"]) or {}
    ds = str(rec.get("datasheet") or "")
    if ds:
        cand = box / ds
        if cand.is_file():
            return cand
    keys = [k for k in (part["lcsc"], part["mpn"]) if k]
    if ddir.is_dir():
        for k in keys:
            hits = sorted(ddir.glob(f"{k}*.pdf"))
            if hits:
                return hits[0]
    return None


def pdf_text_and_tables(pdf: Path, txt_out: Path, budget: int) -> dict:
    """Текст по страницам + таблицы с ключевыми словами; пишет .txt."""
    try:
        import fitz  # PyMuPDF
    except Exception as exc:                            # noqa: BLE001
        return {"ok": False, "error": f"нет PyMuPDF: {exc}"}
    pages: list[str] = []
    excerpt: list[str] = []
    tables_kept = 0
    try:
        with fitz.open(str(pdf)) as doc:
            for i, page in enumerate(doc, 1):
                text = page.get_text("text")
                pages.append(text)
                if len("\n".join(excerpt)) >= budget:
                    continue
                lines = [ln.strip() for ln in text.splitlines()
                         if ln.strip()]
                keep = [ln for ln in lines
                        if any(k in ln.lower() for k in KEYWORDS)]
                if keep:
                    excerpt.append(f"[стр. {i}] " + " | ".join(keep[:24]))
                # таблицы — только там, где уже нашлись ключевые слова:
                # find_tables на 100-страничном PDF не должен сканировать всё
                if keep and tables_kept < 3:
                    try:
                        for t in page.find_tables().tables:
                            rows = t.extract()[:8]
                            body = " ; ".join(
                                " | ".join(str(c or "").replace("\n", " ")
                                           for c in row[:6])
                                for row in rows)
                            excerpt.append(f"[стр. {i} таблица] {body}")
                            tables_kept += 1
                            if tables_kept >= 3:
                                break
                    except Exception:               # noqa: BLE001
                        pass
    except Exception as exc:                            # noqa: BLE001
        return {"ok": False, "error": f"чтение PDF не удалось: {exc}"}
    full = "\n".join(f"== стр. {i} ==\n{t}" for i, t in enumerate(pages, 1))
    txt_out.write_text(full, encoding="utf-8")
    return {"ok": True, "pages": len(pages), "tables": tables_kept,
            "excerpt": "\n".join(excerpt)[:budget]}


def cmd_job(box: Path, budget: int) -> int:
    parts = schematic_parts(box)
    if not parts:
        print("НЕТ design/schematic.json с parts — сначала спецификация")
        return 1
    resolved = resolved_parts(box)
    pins = netlist_pins(box)
    # метаданные из подбора дополняют схему (LCSC/MPN могут жить только там)
    for p in parts:
        rec = resolved.get(p["ref"]) or {}
        if not p["lcsc"]:
            v = rec.get("jlcpcb") or rec.get("lcsc")
            if isinstance(v, list) and v:
                v = v[0]
            if isinstance(v, str) and v:
                p["lcsc"] = v if v.startswith("C") else "C" + v
        if not p["mpn"] and rec.get("mfr"):
            p["mpn"] = str(rec["mfr"])
    out_parts = []
    missing = []          # закупные детали (есть LCSC/MPN) без даташита — дефект
    soft = []             # безродные (гребёнки и т.п.) — корпус по футпринту
    for p in parts:
        pdf = find_pdf(box, p, resolved)
        entry = dict(p)
        entry["pins_in_netlist"] = pins.get(p["ref"])
        entry["expected_datasheet"] = bool(p["lcsc"] or p["mpn"])
        if pdf is None:
            ident = p["lcsc"] or p["mpn"] or p["footprint"] or "?"
            name = f"{p['ref']} ({ident})"
            if p.get("placement") == "offboard":
                name += " [вне платы: документ модуля обязателен]"
            (missing if entry["expected_datasheet"] else soft).append(name)
            entry["datasheet"] = None
            out_parts.append(entry)
            continue
        txt = pdf.with_suffix(".txt")
        got = pdf_text_and_tables(pdf, txt, budget)
        if not got.get("ok"):
            (missing if entry["expected_datasheet"] else soft).append(
                f"{p['ref']}: {got.get('error')}")
            entry["datasheet"] = rel(box, pdf)
            out_parts.append(entry)
            continue
        entry.update({
            "datasheet": rel(box, pdf),
            "datasheet_txt": rel(box, txt),
            "datasheet_pages": got["pages"],
            "excerpt": got["excerpt"],
        })
        out_parts.append(entry)
    job = {
        "what": "PCB-паспорт по каждой детали: корпус, выводы, монтаж, шаг, "
                "габариты — с провенансом (страница/раздел даташита). "
                "Стандартный корпус — строка генератора tscircuit "
                "(0603, sod523, pinheader2, soic8, lqfp48…); нестандартный "
                "или неясный — null (пойдёт наш KiCad-путь).",
        "schema": {
            "parts": {
                "<ref>": {
                    "package_std": "строка-генератор tscircuit или null",
                    "package_text": "как написано в даташите",
                    "mounting": "smd|tht|mixed|null",
                    "pins": "число выводов (int) — сверить с нетлистом",
                    "pitch_mm": "шаг выводов, мм (number|null)",
                    "body_mm": {"l": 0, "w": 0, "h": 0},
                    "evidence": {
                        "page": "номер страницы (null только для method=footprint)",
                        "section": "раздел/подпись таблицы или имя футпринта",
                        "confidence": "high|medium|low",
                        "method": "table|text|drawing|footprint",
                    },
                    "notes": "необязательные оговорки",
                },
            },
        },
        "parts": out_parts,
    }
    write_json(box / "design/datasheets/pcb_job.json", job)
    ok = [e for e in out_parts if e.get("datasheet_txt")]
    print(f"[PCB-паспорт] задание: design/datasheets/pcb_job.json")
    print(f"  деталей {len(out_parts)}, с текстом даташита {len(ok)}")
    for e in out_parts:
        if e.get("datasheet_txt"):
            print(f"    {e['ref']:<5} {e.get('lcsc') or '':<10} "
                  f"{e.get('mpn') or '':<24} стр. {e['datasheet_pages']:<3} "
                  f"выводов в нетлисте: {e.get('pins_in_netlist')}")
        else:
            print(f"    {e['ref']:<5} НЕТ ДАТАШИТА — корпус по футпринту "
                  f"({e.get('footprint') or 'футпринт не задан'})")
    if soft:
        print(f"  ! без закупочного кода и без даташита {len(soft)}: "
              + "; ".join(soft[:10]))
        print("    правило линии: у КАЖДОЙ детали есть даташит. Значит, "
              "реальную закупочную позицию подбирает библиотекарь "
              "(LCSC/MPN в design/schematic.json), а PDF добирает "
              "tools/part_fetch.py --datasheet-only")
    if missing:
        print(f"  ! закупные детали без даташита/текста {len(missing)}: "
              + "; ".join(missing[:10]))
        print("    добор: python3 tools/part_fetch.py --datasheet-only "
              "--lcsc C… [--mpn …] — цепочка LCSC CDN → готовый скрапер "
              "kicad-happy → веб-поиск с проверкой MPN по тексту PDF")
    if missing or soft:
        return 1
    return 0


# ═══════════════════════════════════ нормализация «стандартного» корпуса
#
# Правила — учебник корпусов (EIA/JEDEC/IPC), не список деталей. Результат
# обязан быть строкой, которую принимает генератор tscircuit (footprinter);
# проверка самой строки и совпадения площадок — в эмиттере (build_pcb.mjs,
# там генератор доступен). Здесь — только каноническая форма.

def normalize_package(text) -> str | None:
    if not text:
        return None
    t = str(text).upper()
    m = re.search(r"(?<![0-9A-Z])(" + "|".join(EIA_SIZES) + r")(?![0-9])", t)
    if m:
        return m.group(1)
    if (m := re.search(r"SOD[-\s_]?(\d{2,3})[A-Z]?", t)):
        return f"sod{m.group(1)}"
    if "MINIMELF" in t:
        return "minimelf"
    if re.search(r"(?<!MINI)MELF", t):
        return "melf"
    for name in ("SMA", "SMB", "SMC"):
        if re.search(rf"(?<![A-Z]){name}(?![A-Z])", t):
            return name.lower()
    if re.search(r"SOT[-\s_]?23[-\s_]?5(?![0-9])", t):
        return "sot25"
    for pat, tok in ((r"SOT[-\s_]?23W(?![0-9])", "sot23w"),
                     (r"SOT[-\s_]?23(?![0-9])", "sot23"),
                     (r"SOT[-\s_]?223(?![0-9])", "sot223"),
                     (r"SOT[-\s_]?323(?![0-9])", "sot323"),
                     (r"SOT[-\s_]?363(?![0-9])", "sot363"),
                     (r"SOT[-\s_]?563(?![0-9])", "sot563"),
                     (r"SOT[-\s_]?89(?![0-9])", "sot89")):
        if re.search(pat, t):
            return tok
    if (m := re.search(r"SOIC[-\s_]?(\d+)", t)):
        return f"soic{m.group(1)}"
    if (m := re.search(r"TSSOP[-\s_]?(\d+)", t)):
        return f"tssop{m.group(1)}"
    if (m := re.search(r"QFN[-\s_]?(\d+)", t)):
        return f"qfn{m.group(1)}"
    if (m := re.search(r"LQFP[-\s_]?(\d+)", t)):
        return f"lqfp{m.group(1)}"
    if re.search(r"DPAK|TO[-\s_]?252", t):
        return "dpak"
    if re.search(r"TO[-\s_]?92", t):
        return "to92"
    if re.search(r"TO[-\s_]?220", t):
        return "to220"
    # штыревые/гребёнки: PinHeader_1x02, HDR-2x5, pinrow4 …
    if re.search(r"PINHEAD|PINROW|PIN\s?HEADER|HDR", t):
        m = re.search(r"(\d+)X(\d+)", t) or re.search(r"X(\d+)", t)
        if m:
            n = m.group(2) if m.lastindex == 2 else m.group(1)
            return f"pinheader{n}"
        m = re.search(r"(?:PINROW|PINHEAD|HDR|PIN)[-\s_]?(\d+)", t)
        if m:
            return f"pinheader{m.group(1)}"
    return None


# ══════════════════════════════════════════════ проверка ответа (--check)

def cmd_check(box: Path) -> int:
    parts = schematic_parts(box)
    pcb_path = box / "design/parts_pcb.json"
    data = load_json(pcb_path)
    if not isinstance(data, dict) or not isinstance(data.get("parts"), dict):
        print("НЕТ design/parts_pcb.json (parts-словарь) — агент его ещё не написал")
        return 1
    pins_net = netlist_pins(box)
    refs = {p["ref"] for p in parts}
    # модули вне платы: ставится не на PCB — паспорт корпуса не требуется
    offboard = {p["ref"] for p in parts if p.get("placement") == "offboard"}
    errs: list[str] = []
    warns: list[str] = []
    norm_changed = 0
    have = data["parts"]
    for ref in sorted(refs):
        if ref not in have:
            errs.append(f"{ref}: нет записи в parts_pcb.json")
    for ref, e in sorted(have.items()):
        if ref not in refs:
            warns.append(f"{ref}: записи нет в спецификации — игнорирую")
            continue
        if not isinstance(e, dict):
            errs.append(f"{ref}: запись не объект")
            continue
        if ref in offboard:
            # документ модуля проверяет библиотекарь (design/datasheets);
            # корпус, монтаж и площадки на PCB для него не существуют
            continue
        pkg = e.get("package_std")
        if pkg:
            norm = normalize_package(pkg) or normalize_package(
                e.get("package_text"))
            if norm is None:
                warns.append(f"{ref}: package_std {pkg!r} не распознан — "
                             "пойдёт наш KiCad-путь")
                e["package_std_raw"] = pkg
                e["package_std"] = None
                norm_changed += 1
            elif norm != pkg:
                e["package_std"] = norm
                norm_changed += 1
            # провенанс обязателен для заявленного корпуса (кроме случая
            # «безродной» детали: evidence из имени библиотечного футпринта)
            ev = e.get("evidence")
            by_fp = isinstance(ev, dict) and ev.get("method") == "footprint"
            if not isinstance(ev, dict):
                errs.append(f"{ref}: для package_std нет провенанса "
                            "(evidence)")
            elif not by_fp and not ev.get("page"):
                errs.append(f"{ref}: для package_std нет провенанса "
                            "(evidence.page)")
            elif by_fp and not ev.get("section"):
                errs.append(f"{ref}: evidence.method=footprint без "
                            "evidence.section (имя футпринта)")
            elif str(ev.get("confidence") or "") not in ("high", "medium",
                                                          "low"):
                warns.append(f"{ref}: evidence.confidence "
                             f"{ev.get('confidence')!r} — ожидаю "
                             "high|medium|low")
        elif not e.get("package_text"):
            warns.append(f"{ref}: ни package_std, ни package_text — "
                         "KiCad-путь вслепую")
        mount = e.get("mounting")
        if mount is not None and mount not in MOUNTINGS:
            errs.append(f"{ref}: mounting {mount!r} — ожидаю "
                        + "|".join(MOUNTINGS) + "|null")
        pins = e.get("pins")
        if pins is not None and not isinstance(pins, int):
            errs.append(f"{ref}: pins {pins!r} — нужно целое")
        elif pins is not None and ref in pins_net and pins != pins_net[ref]:
            errs.append(f"{ref}: pins {pins} не совпали с нетлистом "
                        f"({pins_net[ref]}) — паспорт противоречит схеме")
        if pins is None and ref in pins_net:
            e["pins_in_netlist"] = pins_net[ref]
    if norm_changed:
        write_json(pcb_path, data)
    print("[PCB-паспорт] проверка design/parts_pcb.json")
    std = sum(1 for e in have.values()
              if isinstance(e, dict) and e.get("package_std"))
    print(f"  деталей {len(have)}: стандартных корпусов {std}, "
          f"нестандартных/неясных {len(have) - std}")
    for ref, e in sorted(have.items()):
        if not isinstance(e, dict):
            continue
        print(f"    {ref:<5} {str(e.get('package_std') or '—'):<12} "
              f"{str(e.get('package_text') or '')[:34]:<34} "
              f"pins={e.get('pins') if e.get('pins') is not None else '?'} "
              f"{(e.get('evidence') or {}).get('page', '—')}")
    for w in warns:
        print(f"  ! {w}")
    if errs:
        print(f"\nОШИБКА: {len(errs)} дефект(ов) паспорта:")
        for e in errs:
            print(f"   {e}")
        return 1
    print("\nOK: паспорт покрывает все детали, провенанс на месте")
    return 0


def cmd_show(box: Path) -> int:
    data = load_json(box / "design/parts_pcb.json")
    if not isinstance(data, dict):
        print("НЕТ design/parts_pcb.json")
        return 1
    for ref, e in sorted((data.get("parts") or {}).items()):
        if not isinstance(e, dict):
            continue
        print(f"{ref:<5} {str(e.get('package_std') or '—'):<12} "
              f"{str(e.get('package_text') or '')[:40]:<40} "
              f"pins={e.get('pins')} {e.get('mounting')}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="PCB-паспорт деталей по даташитам")
    ap.add_argument("--project", default=".", metavar="КАТАЛОГ",
                    help="каталог заказа (по умолчанию текущий)")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--job", action="store_true",
                   help="собрать задание и выдержки из даташитов")
    g.add_argument("--check", action="store_true",
                   help="проверить design/parts_pcb.json")
    g.add_argument("--show", action="store_true", help="сводка паспортов")
    ap.add_argument("--excerpt-chars", type=int, default=2600,
                    help="бюджет выдержки из даташита на деталь")
    args = ap.parse_args()
    box = Path(args.project).resolve()
    if args.job:
        return cmd_job(box, args.excerpt_chars)
    if args.check:
        return cmd_check(box)
    return cmd_show(box)


if __name__ == "__main__":
    sys.exit(main())
