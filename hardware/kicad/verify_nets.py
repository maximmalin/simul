#!/usr/bin/env python3
"""
Сверка netlist, выгруженного KiCad, с таблицей NETS генератора.

Проверка строгая: для каждой сети из NETS сравнивается ТОЧНОЕ множество
(ref, pin). Любое лишнее или потерянное соединение — расхождение.

Запуск:
    python3 hardware/kicad/verify_nets.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import generate_schematic as G   # noqa: E402

NETFILE = HERE / "schematic.net"


def parse_netlist(path: Path) -> dict[str, set[tuple[str, str]]]:
    """KiCad netlist -> {имя_сети: {(ref, pin), ...}}.

    Блок сети закрывается ")))" в конце строки последнего узла, поэтому
    искать "\n  )" бесполезно. Границы берём по позициям заголовков.
    """
    txt = path.read_text(errors="ignore")
    heads = list(re.finditer(r'\(net\s+\(code\s+"(\d+)"\)\s+\(name\s+"([^"]*)"', txt))
    nets: dict[str, set[tuple[str, str]]] = {}
    for k, h in enumerate(heads):
        end = heads[k + 1].start() if k + 1 < len(heads) else len(txt)
        block = txt[h.start():end]
        nodes = set()
        for nm in re.finditer(r'\(node\s+\(ref\s+"([^"]+)"\)\s+\(pin\s+"([^"]*)"\)', block):
            nodes.add((nm.group(1), nm.group(2)))
        nets.setdefault(h.group(2).lstrip("/"), set()).update(nodes)
    return nets


def expected() -> dict[str, set[tuple[str, str]]]:
    """Таблица NETS -> {сеть: {(ref, НОМЕР_пина), ...}}."""
    out: dict[str, set[tuple[str, str]]] = {}
    for net, conns in G.NETS:
        s = set()
        for ref, pin in conns:
            try:
                s.add((ref, G.probe[ref].resolve(pin)))
            except KeyError as e:
                print(f"!! {e}")
        out.setdefault(net, set()).update(s)
    return out


def main() -> int:
    if not NETFILE.is_file():
        print(f"НЕТ ФАЙЛА {NETFILE} — сначала запустите генератор")
        return 1

    got = parse_netlist(NETFILE)
    exp = expected()

    ok = True

    # ---- сети, которых нет вовсе ----
    lost = sorted(set(exp) - set(got))
    for net in lost:
        pins = ", ".join(f"{r}.{p}" for r, p in sorted(exp[net]))
        print(f"ПОТЕРЯНА  сеть {net}: {pins}")
        ok = False

    # ---- сети, которых быть не должно ----
    extra = sorted(set(got) - set(exp))
    for net in extra:
        pins = ", ".join(f"{r}.{p}" for r, p in sorted(got[net]))
        print(f"ЛИШНЯЯ   сеть {net}: {pins}")
        ok = False

    # ---- несовпадения узлов ----
    for net in sorted(set(exp) & set(got)):
        want, have = exp[net], got[net]
        if want == have:
            continue
        ok = False
        miss = sorted(want - have)
        add = sorted(have - want)
        print(f"РАСХОЖДЕНИЕ {net}:")
        if miss:
            print(f"    потеряно : "
                  f"{', '.join(f'{r}.{p}' for r, p in miss)}")
        if add:
            print(f"    лишнее   : "
                  f"{', '.join(f'{r}.{p}' for r, p in add)}")

    # ---- контроль: каждый вывод ровно в одной сети ----
    pin_net: dict[tuple[str, str], set[str]] = {}
    for net, nodes in got.items():
        for n in nodes:
            pin_net.setdefault(n, set()).add(net)
    multi = {p: ns for p, ns in pin_net.items() if len(ns) > 1}
    for (ref, pin), ns in sorted(multi.items()):
        print(f"ВЫВОД В НЕСКОЛЬКИХ СЕТЯХ {ref}.{pin}: {sorted(ns)}")
        ok = False

    total_nodes = sum(len(v) for v in got.values())
    print()
    print(f"Сетей в NETS        : {len(exp)}")
    print(f"Сетей в netlist     : {len(got)}")
    print(f"Узлов ожидалось     : {sum(len(v) for v in exp.values())}")
    print(f"Узлов в netlist     : {total_nodes}")
    print()
    if ok:
        print("OK  СЕТИ СОВПАДАЮТ ПОЛНОСТЬЮ — расхождений 0")
        return 0
    print("ЕСТЬ РАСХОЖДЕНИЯ")
    return 1


if __name__ == "__main__":
    sys.exit(main())