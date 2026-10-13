#!/usr/bin/env python3
"""Проверка: генератор не привязан к схеме BRIG RECEIVER.

Берётся СОВЕРШЕННО ДРУГАЯ схема — другой состав элементов, другие блоки,
другие сети (в т.ч. сеть, проходящая через все три блока, и сеть,
объединяющая 6 выводов) — и прогоняется через тот же pipeline:
разводка -> .kicad_sch -> проверка штатным kicad-cli.

Утверждение, которое проверяется: таблицы PARTS/NETS/BLOCKS — это ЕДИНСТВЕННЫЙ
вход. Никакой логики, заточенной под конкретную схему, быть не должно.
"""
from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import generate_brig_receiver as G          # noqa: E402
import kicad_lib_brig as K                  # noqa: E402  # brig-receiver's KiCad lib

TMP = Path("/tmp/opencode/other_sch")


def parts():
    """Другая схема: RC-фильтр + усилитель + логика. Ничего общего с BRIG."""
    return [
        G.Part(ref="J1", lib_id="Connector_Generic:Conn_01x03", value="ВХОД",
               footprint="PinHeader_1x03_P2.54mm_Vertical"),
        G.Part(ref="R1", lib_id="Device:R", value="10k", footprint="R_0603"),
        G.Part(ref="C1", lib_id="Device:C", value="100n", footprint="C_0603"),
        G.Part(ref="U1", lib_id="Amplifier_Operational:TL061", value="TL061",
               footprint="DIP-8_W7.62mm"),
        G.Part(ref="C2", lib_id="Device:C", value="10u", footprint="C_0603"),
        G.Part(ref="R2", lib_id="Device:R", value="47k", footprint="R_0603"),
        G.Part(ref="D1", lib_id="Device:LED", value="ЗЕЛЁНЫЙ",
               footprint="LED_0603"),
        G.Part(ref="R3", lib_id="Device:R", value="330", footprint="R_0603"),
        G.Part(ref="U2", lib_id="MCU_ST_STM32F1:STM32F103C8Tx", value="MCU",
               footprint="LQFP-48_7x7mm_P0.5mm"),
        G.Part(ref="R4", lib_id="Device:R", value="1k", footprint="R_0603"),
        G.Part(ref="C3", lib_id="Device:C", value="22n", footprint="C_0603"),
        G.Part(ref="Y1", lib_id="Device:Crystal", value="8MHz",
               footprint="Crystal_DHC-49"),
        G.Part(ref="C4", lib_id="Device:C", value="22n", footprint="C_0603"),
        G.Part(ref="R5", lib_id="Device:R", value="10k", footprint="R_0603"),
        G.Part(ref="TP1", lib_id="Connector:TestPoint", value="ВЫХОД УСИЛ",
               footprint="TestPoint"),
        G.Part(ref="J2", lib_id="Connector:TestPoint", value="ВЫХОД ЦФП",
               footprint="TestPoint"),
    ]


NETS = [
    ("VIN",     [("J1", "1"), ("R1", "1")]),
    ("RC_FILT", [("R1", "2"), ("C1", "1"), ("U1", "+")]),
    ("GND",     [("J1", "3"), ("C1", "2"), ("U1", "-"), ("C2", "2"),
                 ("U1", "V+"), ("C3", "2"), ("C4", "2")]),
    ("BIAS",    [("U1", "~"), ("R2", "1"), ("C2", "1")]),
    ("LED_A",   [("R2", "2"), ("D1", "A"), ("R3", "1")]),
    ("LED_K",   [("D1", "K"), ("TP1", "1")]),
    # сквозная сеть через все три блока
    ("ADC",     [("U1", "V-"), ("R4", "1"), ("U2", "PA0"), ("J2", "1")]),
    # сквозная сеть, связывающая 6 выводов в четырёх блоках
    ("XTAL",    [("U2", "PD0"), ("U2", "PD1"), ("Y1", "1"), ("Y1", "2"),
                 ("C3", "1"), ("C4", "1")]),
    ("MCU_OUT", [("U2", "PA2"), ("R5", "1"), ("R3", "2")]),
    ("VBIAS",   [("R4", "2"), ("R5", "2"), ("U2", "VDD")]),
]

BLOCKS = [
    ("ВХОД И ФИЛЬТР", "аналоговый вход", "", ["J1", "R1", "C1"]),
    ("УСИЛИТЕЛЬ",     "OPA + смещение",   "", ["U1", "R2", "C2", "D1", "R3", "TP1"]),
    ("ЦФП",           "кварц и выход",    "", ["U2", "R4", "C3", "C4", "R5", "Y1", "J2"]),
]

POWER = {"GND": "power:GND", "VBIAS": "power:+3V3"}


def main() -> int:
    G.PARTS[:] = parts()
    G.NETS[:] = NETS
    G.BLOCKS[:] = BLOCKS
    G.POWER_SYM.clear()
    G.POWER_SYM.update(POWER)
    G.TP = "Connector:TestPoint"
    # «пробные» экземпляры строятся из PARTS на уровне модуля
    G.probe.clear()
    for p in G.PARTS:
        G.probe[p.ref] = K.Comp(lib_id=p.lib_id, ref=p.ref, value=p.value)

    sheet, meta = G.build()
    node = K.build_sch(
        sheet.comps, sheet.wires, sheet.junctions, sheet.labels,
        sheet.texts, sheet.rects,
        paper="A2", title="Проверка переносимости", date="2026-10-05", rev="1")

    TMP.mkdir(parents=True, exist_ok=True)
    sch = TMP / "other.kicad_sch"
    K.write_sch(node, sch)

    # сколько символов питания добавилось при разводке
    print(f"блоков           : {len(BLOCKS)}")
    print(f"компонентов      : {len([c for c in sheet.comps if c.ref != 'TP1'])}")
    print(f"символов питания : {len([c for c in sheet.comps if c.ref.startswith('#PWR')])}")
    print(f"сетей в таблице  : {len(NETS)}")
    print(f"проводов         : {len(sheet.wires)}")
    print(f"меток            : {len(sheet.labels)}")

    # штатная проверка KiCad
    net = TMP / "other.net"
    r = subprocess.run(
        ["kicad-cli", "sch", "export", "netlist", "-o", str(net), str(sch)],
        capture_output=True, text=True)
    msg = (r.stdout + r.stderr).strip()
    print(f"\nkicad-cli rc={r.returncode}: {msg[:80]}")
    if r.returncode != 0:
        return 1

    t = net.read_text(errors="ignore")
    chunks = t[t.find("(nets"):].split("(net (code ")[1:]
    pin2net: dict[str, set[str]] = {}
    for ch in chunks:
        name = re.search(r'\(name "([^"]*)"', ch).group(1)
        for a, b in re.findall(
                r'\(node \(ref "([^"]*)"\) \(pin "([^"]*)"', ch):
            pin2net.setdefault(f"{a}.{b}", set()).add(name)

    bad = 0
    for net_name, conns in NETS:
        found: set[str] = set()
        for ref, pin in conns:
            found |= pin2net.get(f"{ref}.{pin}", set())
        got = {n.lstrip("/") for n in found}
        if got != {net_name.lstrip("/")}:
            print(f"  РАСХОЖДЕНИЕ: {net_name} -> {found or 'нет сети'}")
            bad += 1
    multi = [k for k, v in pin2net.items() if len(v) > 1]
    print(f"сетей получено   : {len(chunks)} из {len(NETS)}")
    print(f"расхождений      : {bad}")
    print(f"узлов в 2 сетях  : {len(multi)}")
    print(f"компонентов      : {t.count('(comp (ref')}")
    print(f"узлов            : {t.count('(node ')}")
    return 0 if (bad == 0 and not multi and len(chunks) == len(NETS)) else 1


if __name__ == "__main__":
    raise SystemExit(main())
