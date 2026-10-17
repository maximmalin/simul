#!/usr/bin/env python3
"""Поблочная сборка принципиальной схемы в SKiDL (стадии 7–8 заказа).

Каждый блок design/schematic.json собирается отдельным «прибором» SKiDL:
  * парты блока — реальные символы KiCad/EasyEDA (выводы читает
    kicad_lib_brig, наследование extends развёрнуто);
  * внутренние сети блока — узлы спецификации, в которых ≥2 вывода блока;
  * одиночные выводы — клеммы блока (куда блок стыкуется с остальными).

Результат: design/blocks/b<номер>.net — нетлист KiCad от независимого
движка (SKiDL), плюс design/blocks/index.json со сведениями о блоках.
Нетлист сверяется со спецификацией: расхождение = ошибка каркаса.

Схемотехника узлов — целиком работа дизайнера: движок не подсказывает
топологии и не сверяет её с типовыми решениями. Он проверяет ровно то,
что спецификация физически собираема: выводы существуют в реальном
символе и каждый вывод стоит ровно в одной сети.

Запуск:
    python3 tools/skidl_blocks.py --project BOX          # собрать блоки
    python3 tools/skidl_blocks.py --project BOX --check  # только проверки
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# --------------------------------------------------------------------------- #
#  Библиотека символов: реальные выводы деталей
# --------------------------------------------------------------------------- #
_KIT = None


def load_kit(kit_dir=None):
    """Модуль kicad_lib_brig (чтение символов KiCad/EasyEDA)."""
    global _KIT
    if _KIT is not None:
        return _KIT
    cands = []
    if kit_dir:
        cands.append(Path(kit_dir))
    cands += [ROOT / "hardware" / "kicad", Path.cwd() / "hardware" / "kicad"]
    for cand in cands:
        if (cand / "kicad_lib_brig.py").is_file():
            sys.path.insert(0, str(cand))
            import kicad_lib_brig as K                     # noqa: PLC0415
            _KIT = K
            return K
    raise FileNotFoundError(
        "нет hardware/kicad/kicad_lib_brig.py — укажите --kit-dir")


def _add_box_libs(K, box) -> None:
    """Символы песочницы: design/libs (EasyEDA), libs, local_symbols."""
    for d in (Path(box) / "design" / "libs", Path(box) / "libs",
              Path(box) / "local_symbols"):
        if d.is_dir():
            K.add_search_dir(d)


def symbol_pins(K, lib_id) -> list[dict]:
    """Выводы символа: [{'num', 'name', 'type'}] (extends развёрнут)."""
    sym = K.lib_symbol(str(lib_id))
    out: dict[str, dict] = {}
    for unit in sym.find_all("symbol"):
        for p in unit.find_all("pin"):
            num = p.val("number")
            if num is None:
                continue
            name = p.val("name")
            etype = (str(p.items[1]) if len(p.items) > 1
                     and isinstance(p.items[1], str) else "passive")
            out.setdefault(str(num), {"num": str(num),
                                      "name": str(name if name is not None
                                                  else "~"),
                                      "type": etype})
    return list(out.values())


def _norm(s: str) -> str:
    """Имя вывода для сравнения: регистр, пробелы, подчёркивания, слэши."""
    return re.sub(r"[\s_/]+", "", str(s).strip().lower())


def resolve_pin(pins, want):
    """Вывод символа по имени или номеру (карточка/спецификация)."""
    w = str(want).strip()
    for p in pins:
        if p["num"] == w:
            return p
    for p in pins:
        if p["name"] == w:
            return p
    nw = _norm(w)
    for p in pins:
        if _norm(p["name"]) == nw:
            return p
    return None


def _pins_hint(pins) -> str:
    return ", ".join(f"{p['num']}:{p['name']}" for p in pins)


def block_slug(idx: int) -> str:
    """Имя блока для файлов и индекса: b<номер> в порядке спецификации.

    Заголовок (title) остаётся человекочитаемым в отчёте и index.json: в
    имя файла он не попадает — таблицы транслита/замен не нужны, а
    переименование блока не плодит старые файлы."""
    return f"b{idx}"


# --------------------------------------------------------------------------- #
#  Проверка: выводы реальны, одиночны, блоки ссылаются на детали
# --------------------------------------------------------------------------- #
def check_spec(spec, K, box=None, strict: bool = False) -> dict:
    """Структурные проверки спецификации без сборки:
    каждый вывод существует в реальном символе и стоит ровно в одной
    сети; блоки ссылаются на существующие детали."""
    errors: list[str] = []
    warnings: list[str] = []
    blocks_info: list[dict] = []
    if box:
        _add_box_libs(K, box)
    if not isinstance(spec, dict):
        return {"errors": ["нужен JSON-объект спецификации"],
                "warnings": [], "blocks": []}
    parts = spec.get("parts") or []
    nets = spec.get("nets") or []
    blocks = spec.get("blocks") or []

    by_ref = {str(p.get("ref")): p for p in parts if p.get("ref")}
    pins_by_lib: dict[str, list | None] = {}

    def pins_of(ref):
        """Выводы детали ref; один раз сообщает о нечитаемом символе."""
        p = by_ref.get(ref)
        if p is None:
            return None
        lib = str(p.get("lib_id") or "")
        if lib not in pins_by_lib:
            try:
                pins_by_lib[lib] = symbol_pins(K, lib)
            except Exception as exc:                       # noqa: BLE001
                pins_by_lib[lib] = None
                errors.append(f"{ref}: символ {lib} не читается — {exc}")
        return pins_by_lib[lib]

    # ---- 1. выводы спецификации: существуют и одиночны ------------------
    pin_net: dict[tuple[str, str], str] = {}
    for n in nets:
        netname = str(n.get("name") or "?")
        for c in (n.get("conns") or []):
            if not (isinstance(c, (list, tuple)) and len(c) == 2):
                continue
            ref, want = str(c[0]), str(c[1])
            if ref not in by_ref:
                errors.append(f"сеть {netname}: вывод [{ref},{want}] — "
                              f"детали {ref} нет в parts")
                continue
            pins = pins_of(ref)
            if pins is None:
                continue
            got = resolve_pin(pins, want)
            if got is None:
                errors.append(f"{ref}.{want} не найден в символе "
                              f"{by_ref[ref].get('lib_id')} — есть: "
                              f"{_pins_hint(pins)}")
                continue
            key = (ref, got["num"])
            if key in pin_net and pin_net[key] != netname:
                errors.append(f"вывод {ref}.{got['num']} в двух сетях: "
                              f"{pin_net[key]} и {netname} — правило: один "
                              "вывод ровно в одной сети")
            else:
                pin_net[key] = netname

    # ---- 2. блоки: состав ссылается на существующие детали --------------
    for idx, b in enumerate(blocks, 1):
        title = str(b.get("title") or "БЛОК")
        refs_b = [str(r) for r in (b.get("refs") or [])]
        info = {"slug": block_slug(idx), "title": title, "refs": refs_b}
        blocks_info.append(info)
        for r in refs_b:
            if r not in by_ref:
                errors.append(f"{title}: ref {r} не существует в parts")
    if strict:
        errors = errors + warnings
        warnings = []
    return {"errors": errors, "warnings": warnings, "blocks": blocks_info}


# --------------------------------------------------------------------------- #
#  Сборка: SKiDL по блокам + сверка нетлиста со спецификацией
# --------------------------------------------------------------------------- #
_SKIDL: dict | None = None

#: служебные файлы SKiDL (логи/ERC/склиб) — пути фиксируются при импорте
_SKIDL_JUNK: list[Path] = []


def _clean_skidl_junk() -> None:
    """Убрать служебные файлы SKiDL, созданные этим процессом."""
    for p in list(_SKIDL_JUNK):
        try:
            if p.is_file():
                p.unlink()
        except OSError:
            pass


def _pin_func_map(pin_info, pin_types):
    """Карта «электрический тип вывода KiCad → функция SKiDL» из данных
    самого SKiDL — без зашитых соответствий.

    У каждого типа в pin_info есть текстовые имена (function «POWER-IN»,
    func_str «PWRIN»); KiCad пишет типы в своём стиле («power_in»,
    «bidirectional»). Сравнение идёт по нормализованному тексту (только
    буквы/цифры, верхний регистр), поэтому карта следует за версией
    SKiDL. Незнакомый тип → UNSPEC."""
    def norm(s):
        return "".join(ch for ch in str(s).upper() if ch.isalnum())

    table = {}
    for func, info in pin_info.items():
        for key in (info.get("function"), info.get("func_str")):
            if key:
                table.setdefault(norm(key), func)

    def lookup(etype):
        return table.get(norm(etype), pin_types.UNSPEC)
    return lookup


def _import_skidl() -> dict:
    """SKiDL 2.3.0: TEMPLATE-парты + явный circuit= у партов и сетей.

    Логгеры настраиваются ТОЛЬКО после импорта (обращение к
    logging.getLogger("skidl") раньше ломает инициализацию пакета).
    Имена не зашиты: до импорта снимается снимок реестра logging, после —
    глушатся только НОВЫЕ логгеры SKiDL с хендлерами; пути их файловых
    хендлеров убираются — см. _clean_skidl_junk. Сводки SKiDL — шум для
    отчёта, ошибки ловятся исключениями и сверкой нетлиста.
    """
    global _SKIDL, _SKIDL_JUNK
    if _SKIDL is None:
        import contextlib                                   # noqa: PLC0415
        import io                                           # noqa: PLC0415
        import logging                                      # noqa: PLC0415
        sniff = io.StringIO()
        known = set(logging.root.manager.loggerDict)
        with contextlib.redirect_stderr(sniff):
            import skidl as S                               # noqa: PLC0415
            from skidl import (Part, Pin, Net, Circuit,     # noqa: PLC0415
                               SchLib, SKIDL, TEMPLATE, Alias)
            from skidl.pin import pin_info, pin_types       # noqa: PLC0415
            from skidl.logger import get_script_name        # noqa: PLC0415
        junk: list[Path] = []
        for name in sorted(set(logging.root.manager.loggerDict) - known):
            lg = logging.getLogger(name)
            if not lg.handlers:
                continue                    # логгеры зависимостей не трогаем
            for h in list(lg.handlers):
                fn = getattr(h, "baseFilename", None)
                if fn:
                    junk.append(Path(fn))
                try:
                    h.close()
                except Exception:                           # noqa: BLE001
                    pass
            lg.handlers.clear()
            lg.propagate = False
            lg.setLevel(logging.CRITICAL)
        junk.append(Path(f"{get_script_name()}_sklib.py"))
        _SKIDL_JUNK = junk
        _clean_skidl_junk()
        _SKIDL = {"S": S, "Part": Part, "Pin": Pin, "Net": Net,
                  "Circuit": Circuit, "SchLib": SchLib, "SKIDL": SKIDL,
                  "TEMPLATE": TEMPLATE, "Alias": Alias,
                  "pin_func": _pin_func_map(pin_info, pin_types)}
    return _SKIDL


def _ref_prefix(ref: str) -> str:
    m = re.match(r"[A-Za-z_]+", str(ref))
    return m.group(0) if m else "U"


def parse_netlist(path) -> dict[str, list[tuple[str, str]]]:
    """Нетлист KiCad (skidl) → {сеть: [(ref, пин)]}."""
    K = load_kit()
    root = K.parse_sexp(Path(path).read_text(errors="ignore"))
    out: dict[str, list[tuple[str, str]]] = {}
    for nets in root.find_all("nets"):
        for net in nets.find_all("net"):
            nm = net.find("name")
            if nm is None or len(nm.items) < 2:
                continue
            pins = []
            for node in net.find_all("node"):
                r, p = node.find("ref"), node.find("pin")
                if r is not None and p is not None:
                    pins.append((str(r.items[1]), str(p.items[1])))
            out[str(nm.items[1])] = pins
    return out


def build(box=None, spec=None, out_dir=None,
          strict: bool = False) -> dict:
    """Проверки + сборка SKiDL по блокам + нетлисты + index.json.

    Спецификация — spec (прямой путь) или box/design/schematic.json.
    При дефектах спецификации сборка не начинается: возвращаются ошибки
    (агенту на исправление), артефакты прошлого прогона не трогаются.
    """
    if spec is not None:
        spec_p = Path(spec)
        box = Path(box) if box else spec_p.parent
    elif box is not None:
        box = Path(box)
        spec_p = box / "design" / "schematic.json"
    else:
        raise ValueError("нужен box или spec")
    spec = json.loads(spec_p.read_text(encoding="utf-8"))
    K = load_kit()
    rep = check_spec(spec, K, box=box, strict=strict)
    rep["built"] = []
    if rep["errors"]:
        return rep

    out = Path(out_dir) if out_dir else spec_p.parent / "blocks"
    try:
        S = _import_skidl()
    except Exception as exc:                              # noqa: BLE001
        _clean_skidl_junk()
        rep["warnings"].append(
            f"SKiDL недоступен ({exc}) — пооблочные нетлисты не собраны; "
            "сшивание схемы и ERC работают без него")
        return rep

    by_ref = {str(p.get("ref")): p
              for p in (spec.get("parts") or []) if p.get("ref")}
    out.mkdir(parents=True, exist_ok=True)
    built: list[dict] = []
    used_tnames: set[str] = set()

    for idx, b in enumerate(spec.get("blocks") or [], 1):
        title = str(b.get("title") or "БЛОК")
        refs_b = [str(r) for r in (b.get("refs") or [])]
        slug = block_slug(idx)
        info = {"slug": slug, "title": title,
                "params": b.get("params") or {},
                "parts": [], "nets": [], "terminals": [],
                "netlist": "", "skidl": {}}

        circuit = S["Circuit"](name=slug)
        schlib = S["SchLib"](tool=S["SKIDL"])
        templates: dict[str, str] = {}
        insts: dict[str, object] = {}
        errs_b: list[str] = []
        for ref in refs_b:
            p = by_ref.get(ref)
            if p is None:
                errs_b.append(f"{ref} нет в parts")
                continue
            lib_id = str(p.get("lib_id") or "")
            try:
                pins = symbol_pins(K, lib_id)
            except Exception as exc:                       # noqa: BLE001
                errs_b.append(f"{ref}: символ {lib_id} не читается — {exc}")
                continue
            tname = templates.get(lib_id)
            if tname is None:
                tname = re.sub(r"[^0-9A-Za-z_]", "_",
                               lib_id.split(":", 1)[-1]) or f"P{idx}"
                base = tname
                n = 2
                while tname in used_tnames:
                    tname = f"{base}_{n}"
                    n += 1
                used_tnames.add(tname)
                template = S["Part"](**{
                    "name": tname, "dest": S["TEMPLATE"],
                    "tool": S["SKIDL"], "aliases": S["Alias"]({tname}),
                    "ref_prefix": _ref_prefix(ref),
                    "footprint": str(p.get("footprint") or ""),
                    "description": str(p.get("value") or ""),
                    "datasheet": str(p.get("datasheet") or ""),
                    "pins": [S["Pin"](num=pp["num"], name=pp["name"],
                                      func=S["pin_func"](pp["type"]))
                             for pp in pins],
                })
                schlib.add_parts(template)
                templates[lib_id] = tname
            attrs = {"ref": ref, "circuit": circuit,
                     "value": str(p.get("value") or "")}
            if p.get("footprint"):
                attrs["footprint"] = str(p["footprint"])
            insts[ref] = S["Part"](schlib, tname, **attrs)
            info["parts"].append(
                {"ref": ref, "lib_id": lib_id,
                 "value": str(p.get("value") or ""),
                 "footprint": str(p.get("footprint") or ""),
                 "module": bool(p.get("module")),
                 "placement": str(p.get("placement") or "")})

        expected: dict[str, list[tuple[str, str]]] = {}
        for n in spec.get("nets") or []:
            pairs = [(str(c[0]), str(c[1]))
                     for c in (n.get("conns") or [])
                     if isinstance(c, (list, tuple)) and len(c) == 2
                     and str(c[0]) in insts]
            if not pairs:
                continue
            netname = str(n.get("name") or "?")
            net = S["Net"](netname, circuit=circuit)
            mkeys: list[tuple[str, str]] = []
            for ref, want in pairs:
                pins = symbol_pins(K, str(by_ref[ref].get("lib_id") or ""))
                got = resolve_pin(pins, want)
                if got is None:
                    errs_b.append(f"{ref}.{want}: вывод не найден при сборке")
                    continue
                insts[ref][got["num"]] += net
                mkeys.append((ref, got["num"]))
            expected[netname] = sorted(mkeys)
            entry = {"name": netname,
                     "pins": [f"{r}.{w}" for r, w in sorted(pairs)]}
            if len(pairs) >= 2:
                info["nets"].append(entry)
            else:
                info["terminals"].append(entry)

        if not errs_b:
            net_file = out / f"{slug}.net"
            try:
                # SKiDL печатает свои сводки мимо логгера — на время
                # генерации вывод перехватывается и возвращается только
                # при сбое (диагностика), чтобы отчёт оставался чистым.
                import contextlib
                import io
                sniff = io.StringIO()
                with contextlib.redirect_stdout(sniff), \
                        contextlib.redirect_stderr(sniff):
                    circuit.generate_netlist(file=str(net_file))
                try:
                    info["netlist"] = str(net_file.relative_to(box))
                except ValueError:
                    info["netlist"] = str(net_file)
            except Exception as exc:                       # noqa: BLE001
                tail = "; ".join(sniff.getvalue().strip().splitlines()[-4:])
                errs_b.append(f"нетлист не собрался — {exc}"
                              + (f" | skidl: {tail}" if tail else ""))
            else:
                got = parse_netlist(net_file)
                for netname, mem in expected.items():
                    gn = got.get(netname)
                    if gn is None:
                        if len(mem) == 1:
                            continue                   # клемма без узла
                        errs_b.append(f"сеть {netname}: исчезла в нетлисте")
                    elif sorted(gn) != mem:
                        errs_b.append(
                            f"сеть {netname}: нетлист SKiDL разошёлся со "
                            f"спецификацией: {sorted(gn)} ≠ {mem}")
                info["skidl"] = {"parts": len(insts),
                                 "nets": len(expected)}
        for e in errs_b:
            rep["errors"].append(f"блок {title}: {e}")
        built.append(info)

    rep["built"] = built
    # нетлисты прошлых прогонов с прежними именами блоков не остаются
    slugs = {str(b.get("slug")) for b in built}
    for stale in out.glob("*.net"):
        if stale.stem not in slugs:
            try:
                stale.unlink()
            except OSError:
                pass
    (out / "index.json").write_text(
        json.dumps({"spec": str(spec_p), "blocks": built,
                    "errors": rep["errors"], "warnings": rep["warnings"]},
                   ensure_ascii=False, indent=1),
        encoding="utf-8")
    _clean_skidl_junk()
    return rep


# --------------------------------------------------------------------------- #
#  CLI
# --------------------------------------------------------------------------- #
def _print_report(rep: dict, check_only: bool = False) -> None:
    mode = "проверка" if check_only else "сборка"
    print(f"== поблочная {mode}: блоков {len(rep.get('blocks') or [])}, "
          f"ошибок {len(rep.get('errors') or [])}, "
          f"предупреждений {len(rep.get('warnings') or [])}")
    for b in rep.get("blocks") or []:
        print(f"  [{b.get('slug', '?')}] {b.get('title')}: деталей "
              f"{len(b.get('refs') or [])}")
    for b in rep.get("built") or []:
        if b.get("netlist"):
            print(f"   нетлист {b['netlist']}: сетей "
                  f"{len(b.get('nets') or [])}, клемм "
                  f"{len(b.get('terminals') or [])}")
    for e in rep.get("errors") or []:
        print("ошибка: " + str(e))
    for w in rep.get("warnings") or []:
        print("предупреждение: " + str(w))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Поблочная сборка схемы в SKiDL: нетлисты блоков по "
                    "спецификации")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--project", help="каталог заказа "
                                     "(BOX/design/schematic.json)")
    g.add_argument("--spec", help="прямой путь к спецификации")
    ap.add_argument("--out", default=None, help="каталог артефактов "
                    "(по умолчанию design/blocks рядом со спецификацией)")
    ap.add_argument("--check", action="store_true",
                    help="только проверки, без сборки нетлистов")
    ap.add_argument("--strict", action="store_true",
                    help="предупреждения считать ошибками")
    ap.add_argument("--kit-dir", default=None)
    ap.add_argument("--json", action="store_true", help="машинный отчёт")
    args = ap.parse_args(argv)

    if args.spec:
        spec_p = Path(args.spec).resolve()
        box = spec_p.parent
    elif args.project:
        box = Path(args.project).resolve()
        spec_p = box / "design" / "schematic.json"
    else:
        box = Path.cwd()
        spec_p = (box / "design" / "schematic.json")
    if not spec_p.is_file():
        print(f"нет спецификации {spec_p}", file=sys.stderr)
        return 2
    try:
        K = load_kit(args.kit_dir)
    except Exception as exc:                              # noqa: BLE001
        print(f"не готов инструмент: {exc}", file=sys.stderr)
        return 2
    if args.check:
        spec = json.loads(spec_p.read_text(encoding="utf-8"))
        rep = check_spec(spec, K, box=box, strict=args.strict)
    else:
        rep = build(box=box, spec=spec_p, out_dir=args.out,
                    strict=args.strict)
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=1))
    else:
        _print_report(rep, check_only=args.check)
    return 1 if rep.get("errors") else 0


if __name__ == "__main__":
    sys.exit(main())
