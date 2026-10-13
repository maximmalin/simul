#!/usr/bin/env python3
"""
Brig Receiver — генератор ПОЛНОЙ принципиальной схемы одним листом.

Как это работает
----------------
1. Определения символов берутся ДОСЛОВНО из /usr/share/kicad/symbols
   (kicad_lib разворачивает `(extends ...)`) и встраиваются в (lib_symbols ...)
   ровно так же, как это делает сам KiCad. Поэтому символы не «отбрасываются».
2. Все компоненты и все соединения описаны данными (PARTS / NETS) —
   ни одной координаты и ни одного провода не рисуется вручную.
3. Разводка выполняется автоматически:
     * сеть целиком внутри одного блока  -> горизонтальная шина под рядом
       компонентов + отводы от каждого вывода + одна метка слева;
     * сеть跨 блоков                     -> короткий отвод от вывода + метка;
     * шина питания (+3V3 / +5V / GND)   -> символ питания на отводе.
4. Проверка — штатным kicad-cli: (components) и (nets) в netlist непусты.

Запуск:
    .venv/bin/python3 hardware/kicad/generate_brig_receiver.py
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import kicad_lib_brig as K   # noqa: E402  # brig-receiver's KiCad lib (kristall keeps its own kicad_lib.py)

OUT_SCH = HERE / "brig_receiver.kicad_sch"
OUT_PRO = HERE / "brig_receiver.kicad_pro"
OUT_NET = HERE / "brig_receiver.net"

GRID = 1.27
COL_PITCH = 60.0        # шаг колонок компонентов, мм
RAIL_PITCH = 6.35       # шаг шин по вертикали, мм
BUS_DROP = 22.0         # насколько ниже ряда компонентов идёт первая шина
STUB = 1.27             # длина отвода под метку.
                         # ДОЛЖНА быть меньше шага между пинами (2.54 мм),
                         # иначе отвод одного вывода замыкает на соседний.
BLOCK_PAD = 14.0        # зазор между блоками
RAIL_X0 = 34.0          # левая граница шин
UNIT_DX = 13.97         # сдвиг доп. юнитов мультиюнитовых символов влево


# --------------------------------------------------------------------------- #
#  Компоненты
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Part:
    ref: str
    lib_id: str
    value: str
    footprint: str = ""


R, C, CP = "Device:R", "Device:C", "Device:C_Polarized"
L, D, LED, XTAL = "Device:L", "Device:D", "Device:LED", "Device:Crystal"
FU, BAT, FB = "Device:Fuse", "Device:Battery", "Device:FerriteBead"
ANT, NPN = "Device:Antenna", "Transistor_BJT:Q_NPN_CBE"
BUCK, LDO = "Regulator_Switching:MP2303ADN", "Regulator_Linear:LM78L05_TO92"
TL061, TL082 = "Amplifier_Operational:TL061", "Amplifier_Operational:TL082"
MCU, WIFI = "MCU_ST_STM32F1:STM32F103C8Tx", "RF_Module:ESP32-WROOM-32"
TP = "Connector:TestPoint"
def CN(n: int) -> str:
    return f"Connector_Generic:Conn_01x0{n}"


PARTS: list[Part] = [
    # ---------- БЛОК 0. СИСТЕМА ПИТАНИЯ ----------
    Part("J1", BAT, "7.4V", "Battery:BatteryHolder_Keystone_1042_1x18650"),
    Part("J2", CN(2), "Внешнее питание 8…12 В", "Connector:JWT_A3963_1x02_P3.96mm_Vertical"),
    Part("F1", FU, "2A", "Fuse:Fuse_1812_4532Metric"),
    Part("C1", CP, "220u", "Capacitor_SMD:CP_Elec_6.3x7.7"),
    Part("U1", BUCK, "MP2303", "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm"),
    Part("L1", L, "18u", "Inductor_SMD:L_12x12mm_H6mm"),
    Part("C2", CP, "470u", "Capacitor_SMD:CP_Elec_6.3x7.7"),
    Part("C3", C, "10n", "Capacitor_SMD:C_0603_1608Metric"),
    Part("C4", C, "22n", "Capacitor_SMD:C_0603_1608Metric"),
    Part("U2", LDO, "LM78L05", "Package_TO_SOT_SMD:SOT-223-3_TabPin2"),
    Part("C5", CP, "470u", "Capacitor_SMD:CP_Elec_6.3x7.7"),
    Part("C6", C, "100n", "Capacitor_SMD:C_0603_1608Metric"),
    Part("C7", C, "33p", "Capacitor_SMD:C_0603_1608Metric"),
    Part("FB1", FB, "BLM18", "Inductor_SMD:L_0603_1608Metric"),

    # ---------- БЛОК 1. СВЧ-ТРАКТ И СЕЛЕКЦИЯ ----------
    Part("A1", ANT, "ANT_CHA", "RF_Antenna:Johanson_2450AT43F0100"),
    Part("A2", ANT, "ANT_CHB", "RF_Antenna:Johanson_2450AT43F0100"),
    Part("A3", ANT, "ANT_CHC", "RF_Antenna:Johanson_2450AT43F0100"),
    Part("D1", D, "KA507", "Package_TO_SOT_SMD:SOT-23"),
    Part("D2", D, "KA507", "Package_TO_SOT_SMD:SOT-23"),
    Part("D3", D, "KA507", "Package_TO_SOT_SMD:SOT-23"),
    Part("M1", CN(4), "Смеситель 1 (2А112А-6)", "Connector:NS-Tech_Grove_1x04_P2mm_Vertical"),
    Part("M2", CN(4), "Смеситель 2 (2А112А-6)", "Connector:NS-Tech_Grove_1x04_P2mm_Vertical"),
    Part("U1B", CN(8), "ТВ-селектор KS-H-132", "Connector_PinHeader_2.54mm:PinHeader_1x08_P2.54mm_Vertical"),
    Part("Q1", NPN, "KT368", "Package_TO_SOT_SMD:SOT-23"),
    Part("R1", R, "1k", "Resistor_SMD:R_0603_1608Metric"),
    Part("X1", XTAL, "38MHz", "Crystal:Crystal_AT310_D3.0mm_L10.0mm_Horizontal"),
    Part("X2", XTAL, "2GHz", "Crystal:Crystal_AT310_D3.0mm_L10.0mm_Horizontal"),
    Part("D4", D, "KD522", "Package_TO_SOT_SMD:SOT-23"),
    Part("D5", D, "1N4148", "Diode_SMD:D_SOD-323"),
    Part("C8", C, "10p", "Capacitor_SMD:C_0603_1608Metric"),
    Part("TP1", TP, "IF_2G", "TestPoint:TestPoint_Pad_D1.0mm"),

    # ---------- БЛОК 2. АКУСТООПТИЧЕСКОЕ ЯДРО ----------
    Part("LS1", LED, "LD-14", "LED_SMD:LED_0603_1608Metric"),
    Part("U4", TL061, "TL061", "Package_DIP:DIP-8_W7.62mm"),
    Part("R2", R, "2.2", "Resistor_SMD:R_2512_6332Metric"),
    Part("M1B", CN(2), "Нож Фуко М3×0.25", "Connector:JWT_A3963_1x02_P3.96mm_Vertical"),
    Part("T1", XTAL, "KV-38", "Crystal:Crystal_AT310_D3.0mm_L10.0mm_Horizontal"),
    Part("B1", XTAL, "BRAGG", "Crystal:Crystal_AT310_D3.0mm_L10.0mm_Horizontal"),
    Part("PD1", D, "BPW34", "Package_TO_SOT_SMD:SOT-23"),
    Part("PD2", D, "BPW34", "Package_TO_SOT_SMD:SOT-23"),
    Part("PD3", D, "BPW34", "Package_TO_SOT_SMD:SOT-23"),
    Part("PD4", D, "BPW34", "Package_TO_SOT_SMD:SOT-23"),
    Part("TP2", TP, "PDT_I", "TestPoint:TestPoint_Pad_D1.0mm"),
    Part("TP3", TP, "PDT_II", "TestPoint:TestPoint_Pad_D1.0mm"),
    Part("TP4", TP, "PDT_III", "TestPoint:TestPoint_Pad_D1.0mm"),
    Part("TP5", TP, "PDT_IV", "TestPoint:TestPoint_Pad_D1.0mm"),

    # ---------- БЛОК 3. FPAA И МИКРОКОНТРОЛЛЕР ----------
    Part("U5", TL061, "TL061", "Package_DIP:DIP-8_W7.62mm"),
    Part("R5", R, "50k", "Resistor_SMD:R_0603_1608Metric"),
    Part("C9", C, "10p", "Capacitor_SMD:C_0603_1608Metric"),
    Part("U6", TL082, "TL082", "Package_DIP:DIP-8_W7.62mm"),
    Part("R6", R, "10k", "Resistor_SMD:R_0603_1608Metric"),
    Part("R7", R, "10k", "Resistor_SMD:R_0603_1608Metric"),
    Part("C10", C, "100n", "Capacitor_SMD:C_0603_1608Metric"),
    Part("R8", R, "10k", "Resistor_SMD:R_0603_1608Metric"),
    Part("R9", R, "10k", "Resistor_SMD:R_0603_1608Metric"),
    Part("C11", C, "100n", "Capacitor_SMD:C_0603_1608Metric"),
    Part("U7", MCU, "STM32F103C8Tx", "Package_QFP:LQFP-48_7x7mm_P0.5mm"),
    Part("R10", R, "10k", "Resistor_SMD:R_0603_1608Metric"),
    Part("R11", R, "10k", "Resistor_SMD:R_0603_1608Metric"),
    Part("J3", CN(6), "SPI → ESP32", "Connector_PinHeader_2.54mm:PinHeader_1x06_P2.54mm_Vertical"),
    Part("TP6", TP, "UBIAS", "TestPoint:TestPoint_Pad_D1.0mm"),
    Part("TP7", TP, "TIA_OUT", "TestPoint:TestPoint_Pad_D1.0mm"),
    Part("TP8", TP, "ADC_IN", "TestPoint:TestPoint_Pad_D1.0mm"),

    # ---------- БЛОК 4. ЦИФРОВОЙ КАНАЛ ESP32 ----------
    Part("U8", WIFI, "ESP32-WROOM-32", "RF_Module:ESP32-WROOM-32"),
    Part("C12", CP, "1000u", "Capacitor_SMD:CP_Elec_6.3x7.7"),
    Part("C13", C, "100n", "Capacitor_SMD:C_0603_1608Metric"),
    Part("C14", C, "10n", "Capacitor_SMD:C_0603_1608Metric"),
    Part("C15", C, "1u", "Capacitor_SMD:C_0603_1608Metric"),
    Part("ANT2", ANT, "IFA_2G4", "RF_Antenna:Johanson_2450AT43F0100"),
    Part("R12", R, "10k", "Resistor_SMD:R_0603_1608Metric"),
    Part("LED1", LED, "WIFI_LED", "LED_SMD:LED_0603_1608Metric"),
    Part("R13", R, "680", "Resistor_SMD:R_0603_1608Metric"),
    Part("J4", CN(6), "SPI Slave 18 МГц", "Connector_PinHeader_2.54mm:PinHeader_1x06_P2.54mm_Vertical"),
    Part("TP9", TP, "RF_OUT", "TestPoint:TestPoint_Pad_D1.0mm"),
]


# --------------------------------------------------------------------------- #
#  Соединения: (сеть, [(ref, пин)])   пин = ИМЯ или НОМЕР
# --------------------------------------------------------------------------- #
NETS: list[tuple[str, list[tuple[str, str]]]] = [
    # ======================= БЛОК 0. ПИТАНИЕ =======================
    ("VBAT", [("J1", "1"), ("J2", "1"), ("F1", "1")]),
    ("VIN_PROT", [("F1", "2"), ("C1", "1"), ("U1", "IN"), ("U1", "EN"), ("U2", "VI")]),
    ("U1_SS", [("U1", "SS"), ("C3", "1")]),
    ("U1_COMP", [("U1", "COMP"), ("C4", "1")]),
    ("SW_BUCK", [("U1", "SW"), ("L1", "1")]),
    ("V5_PRE", [("U2", "VO"), ("C5", "1")]),

    # ======================= БЛОК 1. СВЧ =======================
    ("RF_IN_A", [("A1", "1"), ("D1", "2")]),
    ("RF_IN_B", [("A2", "1"), ("D2", "2")]),
    ("RF_IN_C", [("A3", "1"), ("D3", "2")]),
    ("SW_RF", [("D1", "1"), ("D2", "1"), ("D3", "1"),
               ("M1", "1"), ("M2", "1"), ("U1B", "2")]),
    ("LO_38M", [("X1", "1"), ("M1", "2")]),
    ("LO_2G", [("X2", "1"), ("M2", "2")]),
    ("MIX1_IF", [("M1", "3"), ("C8", "1")]),
    ("MIX2_IF", [("M2", "3"), ("TP1", "1")]),
    ("GATE_BIAS", [("U1B", "3"), ("R1", "1"), ("Q1", "2")]),

    # ======================= БЛОК 2. АКУСТООПТИКА =======================
    ("LD_DRIVE", [("U4", "~"), ("R2", "1")]),
    ("LASER_A", [("R2", "2"), ("LS1", "2"), ("U4", "-")]),
    ("FB_QUARTZ", [("T1", "2"), ("B1", "1")]),
    ("FB_PMMA", [("B1", "2"), ("M1B", "1")]),
    ("PDT_II", [("PD2", "K"), ("TP3", "1")]),
    ("PDT_III", [("PD3", "K"), ("TP4", "1")]),
    ("PDT_IV", [("PD4", "K"), ("TP5", "1")]),

    # ======================= БЛОК 3. FPAA + STM32 =======================
    ("TIA_OUT", [("U5", "~"), ("R5", "1"), ("C9", "1"), ("R6", "1"), ("TP7", "1")]),
    ("SUB_IN", [("R6", "2"), ("R7", "2"), ("U6", "-")]),
    ("ADC_IN", [("U6", "~"), ("R7", "1"), ("C10", "1"), ("U7", "PA0"), ("TP8", "1")]),
    ("NRST_RC", [("U7", "NRST"), ("R10", "1")]),
    ("BOOT0", [("U7", "BOOT0"), ("R11", "1")]),
    ("SPI_SCK", [("U7", "PA5"), ("J3", "1"), ("J4", "1")]),
    ("SPI_MISO", [("U7", "PA6"), ("J3", "2"), ("J4", "2")]),
    ("SPI_MOSI", [("U7", "PA7"), ("J3", "3"), ("J4", "3")]),
    ("SPI_CS", [("U7", "PA4"), ("J3", "4"), ("J4", "4")]),

    # ======================= БЛОК 4. ESP32 =======================
    ("EN_RC", [("U8", "EN"), ("C15", "1")]),
    ("BOOT", [("U8", "IO0"), ("R12", "1")]),
    ("LED_AN", [("U8", "IO15"), ("R13", "1")]),
    ("WIFI_RF", [("ANT2", "1"), ("TP9", "1")]),
    ("UART_TX", [("U7", "PA9"), ("U8", "35")]),
    ("UART_RX", [("U7", "PA10"), ("U8", "34")]),
    ("TTL_SW_A", [("U1B", "6"), ("U8", "IO18")]),
    ("TTL_SW_B", [("U1B", "7"), ("U8", "IO19")]),
    ("TTL_2TONE", [("U1B", "8"), ("U8", "IO21")]),

    # ======================= МЕЖБЛОЧНЫЕ СЕТИ =======================
    ("+3V3_DIG", [("U1", "FB"), ("L1", "2"), ("C2", "1"),
                  ("U7", "24"), ("U7", "36"), ("U7", "48"), ("U7", "9"),
                  ("R10", "2"), ("J3", "5"), ("J4", "5"),
                  ("U8", "VDD"), ("C12", "1"), ("C13", "1"), ("C14", "1")]),
    ("+5V_ANA", [("FB1", "1"), ("C6", "1"), ("C7", "1"), ("R8", "1"),
                 ("U1B", "1"), ("U4", "V+"), ("U5", "V+"), ("U6", "V+"),
                 ("PD1", "A"), ("PD2", "A"), ("PD3", "A"), ("PD4", "A")]),
    ("VBIAS", [("R8", "2"), ("R9", "1"), ("C11", "1"),
               ("U4", "+"), ("U5", "+"), ("U6", "+"), ("TP6", "1")]),
    ("IF_38M", [("C8", "2"), ("Q1", "C"), ("U1B", "5"),
                ("T1", "1"), ("D4", "K"), ("D5", "K")]),
    ("PDT_I", [("PD1", "K"), ("U5", "-"), ("R5", "2"), ("TP2", "1")]),

    ("GND", [("J1", "2"), ("J2", "2"), ("C1", "2"), ("U1", "GND"), ("U1", "BS"),
             ("C2", "2"), ("C3", "2"), ("C4", "2"), ("U2", "GND"), ("C5", "2"),
             ("C6", "2"), ("C7", "2"), ("FB1", "2"),
             ("M1", "4"), ("M2", "4"), ("U1B", "4"), ("Q1", "E"),
             ("D4", "A"), ("D5", "A"), ("C9", "2"), ("C10", "2"), ("R13", "2"),
             ("LS1", "K"), ("U4", "V-"), ("M1B", "2"), ("R9", "2"), ("C11", "2"),
             ("U5", "V-"), ("U6", "V-"), ("R11", "2"),
             ("U7", "23"), ("U7", "35"), ("U7", "47"), ("U7", "8"), ("J3", "6"),
             ("U8", "GND"), ("C12", "2"), ("C13", "2"), ("C14", "2"),
             # У ESP32-WROOM-32 выводы GND 1/15/38/39 стоят в символе в ОДНОЙ точке,
              # поэтому KiCad соединяет их автоматически, даже если перечислен
              # только пин 1. На модуле все четыре контакта — GND, так что
              # перечисляем явно: иначе реальное требование к разводке скрыто
              # внутри молчаливого соединения совпадающих выводов.
              ("U8", "15"), ("U8", "38"), ("U8", "39"),
              ("C15", "2"), ("R12", "2"), ("LED1", "K"), ("J4", "6")]),
]

# Метки шин питания -> символ KiCad
POWER_SYM = {"GND": "power:GND", "+3V3_DIG": "power:+3V3",
             "+5V_ANA": "power:+5V", "VBIAS": "power:+5V", "VBAT": "power:+BATT"}

# Рамки блоков: заголовок, подпись, вывод в потоке, позиция
BLOCKS: list[tuple[str, str, str, list[str]]] = [
    ("БЛОК 1 — СВЧ-ТРАКТ И ТВ-БЛОК", "Узлы 1.1–1.6 · ОТСЕК 1 · развязка ≥80 дБ", "",
     ["A1", "A2", "A3", "D1", "D2", "D3", "M1", "M2", "U1B",
      "Q1", "R1", "X1", "X2", "D4", "D5", "C8", "TP1"]),
    ("БЛОК 2 — АКУСТООПТИЧЕСКОЕ ЯДРО", "Узлы 2.1–2.6 · ОТСЕК 2 · Q=10.88 · λ=650 нм", "",
     ["LS1", "U4", "R2", "M1B", "T1", "B1", "PD1", "PD2", "PD3", "PD4",
      "TP2", "TP3", "TP4", "TP5"]),
    ("БЛОК 3 — FPAA И МИКРОКОНТРОЛЛЕР", "Узлы 3.1–3.4 · ОТСЕК 3 · ≥2048 пикс. · ≥50 кГц", "",
     ["U5", "R5", "C9", "U6", "R6", "R7", "C10", "R8", "R9", "C11",
      "U7", "R10", "R11", "J3", "TP6", "TP7", "TP8"]),
    ("БЛОК 4 — ЦИФРОВОЙ КАНАЛ ESP32", "Узлы 4.1–4.4 · ОТСЕК 4 · SPI 18 МГц · CISPR 22 Class B", "",
     ["U8", "C12", "C13", "C14", "C15", "ANT2", "R12", "LED1", "R13", "J4", "TP9"]),
    ("БЛОК 0 — СИСТЕМА ПИТАНИЯ 5 В", "Узлы 5.1–5.4 · ОТСЕК 5 · фильтр ВЧ-наводок BLM18", "",
     ["J1", "J2", "F1", "C1", "U1", "L1", "C2", "C3", "C4", "U2",
      "C5", "C6", "C7", "FB1"]),
]


# --------------------------------------------------------------------------- #
#  Автоматическая разводка
# --------------------------------------------------------------------------- #
@dataclass
class Sheet:
    comps: list[K.Comp] = field(default_factory=list)
    wires: list = field(default_factory=list)
    junctions: list = field(default_factory=list)
    labels: list = field(default_factory=list)
    texts: list = field(default_factory=list)
    rects: list = field(default_factory=list)
    n_pwr: int = 0

    def power(self, lib_id: str, value: str, x: float, y: float) -> None:
        self.n_pwr += 1
        self.comps.append(K.Comp(lib_id=lib_id, ref=f"#PWR{self.n_pwr:03d}",
                                 value=value, in_bom=False, hide_ref=True,
                                 x=round(x / GRID) * GRID, y=round(y / GRID) * GRID))

    def page_w(self) -> float:
        return max((c.x for c in self.comps), default=100.0) + 40.0


def g(v: float) -> float:
    return round(v / GRID) * GRID


# «Пробные» экземпляры: только для разрешения имён пинов в номера
probe: dict[str, K.Comp] = {p.ref: K.Comp(lib_id=p.lib_id, ref=p.ref,
                                         value=p.value) for p in PARTS}


def build() -> tuple[Sheet, dict]:
    sh = Sheet()
    by_ref = {p.ref: p for p in PARTS}

    nets_by_ref: dict[str, list[str]] = {}
    for net, conns in NETS:
        for r, _ in conns:
            nets_by_ref.setdefault(r, [])
            if net not in nets_by_ref[r]:
                nets_by_ref[r].append(net)

    ref_block: dict[str, int] = {}
    for bi, (_t, _s, _f, refs) in enumerate(BLOCKS):
        for r in refs:
            ref_block[r] = bi

    # ---- какие сети локальны (внутри одного блока) ----
    local_nets: dict[int, list[str]] = {}
    for net, conns in NETS:
        blks = {ref_block[r] for r, _ in conns if r in ref_block}
        if len(blks) == 1:
            local_nets.setdefault(next(iter(blks)), []).append(net)

    # ---- какие юниты каждого символа реально задействованы ----
    units: dict[str, list[int]] = {}
    for ref, p in by_ref.items():
        sym = K.lib_symbol(p.lib_id)
        used = set()
        for _net, conns in NETS:
            for r, pin in conns:
                if r != ref:
                    continue
                used.add(K.unit_for_pin(sym, probe[ref].resolve(pin)))
        units[ref] = sorted(used) or [1]

    # ---- габариты символа: насколько выводы торчат вверх/вниз от центра ----
    # Ось Y схемы направлена вниз, а в координатах библиотеки вверх,
    # поэтому (Comp.pin_point) даёт y = cy - ly: ly > 0 -> вывод ВЫШЕ центра.
    def extent(ref: str) -> tuple[float, float]:
        p = by_ref[ref]
        above = below = 0.0
        for u in units[ref]:
            for _num, (_x, ly) in K.pin_positions(
                    K.lib_symbol(p.lib_id), u).items():
                above = max(above, ly)
                below = max(below, -ly)
        return above, below

    # ---- геометрия полос: считается из РЕАЛЬНЫХ габаритов символов ----
    # Раньше высота полосы считалась по константе (BUS_DROP + n*RAIL_PITCH).
    # Из-за этого высокие корпуса (LQFP-48 у STM32, DIP-8 у ОУ) вылезали
    # вверх за пределы своей полосы, и шина предыдущего блока проходила
    # прямо через верхний ряд выводов. Теперь полоса растягивается так,
    # чтобы верхний вывод блока и нижняя шина гарантированно помещались.
    BAND_TOP_MARGIN = 6.0
    RAIL_GAP = 5.0        # зазор от самого нижнего вывода до первой шины
    BAND_BOT_MARGIN = 6.0
    y = 26.0
    bands = []
    for bi, (title, sub, _f, refs) in enumerate(BLOCKS):
        n = len(local_nets.get(bi, []))
        above = max((extent(r)[0] for r in refs), default=0.0)
        below = max((extent(r)[1] for r in refs), default=0.0)
        y0 = y
        comp_y = g(y0 + BAND_TOP_MARGIN + above)
        # первая шина — строго ниже самого нижнего вывода блока
        ry0 = g(comp_y + max(BUS_DROP, below + RAIL_GAP))
        ry1 = g(ry0 + max(n - 1, 0) * RAIL_PITCH)
        y1 = ry1 + BAND_BOT_MARGIN
        bands.append(dict(y0=y0, y1=y1, title=title, sub=sub, refs=refs,
                          comp_y=comp_y, n=n, ry0=ry0, ry1=ry1))
        y = y1

    # ---- 1) расстановка компонентов (по юнитам) ----
    inst: dict[tuple[str, int], K.Comp] = {}

    def pin_xy(ref: str, pin: str) -> tuple[float, float]:
        p = by_ref[ref]
        num = probe[ref].resolve(pin)
        u = K.unit_for_pin(K.lib_symbol(p.lib_id), num)
        c = inst.get((ref, u)) or inst[(ref, units[ref][0])]
        return c.pin_point(num)

    for b in bands:
        x = RAIL_X0 + 12.0
        for ref in b["refs"]:
            p = by_ref[ref]
            for i, u in enumerate(units[ref]):
                inst[(ref, u)] = K.Comp(
                    lib_id=p.lib_id, ref=p.ref, value=p.value,
                    footprint=p.footprint, unit=u,
                    x=g(x - UNIT_DX * i), y=g(b["comp_y"]))
            x += COL_PITCH

    # ---- 2) рамки и заголовки ----
    for b in bands:
        sh.texts.append(K.text_node(b["title"], RAIL_X0, b["y0"] - 3.0, 4.5))
        sh.texts.append(K.text_node(b["sub"], RAIL_X0, b["y0"] + 2.0, 2.5, bold=False))
        sh.rects.append(K.rect_node(RAIL_X0 - 6.0, b["y0"] + 5.0,
                                    RAIL_X0 - 6.0 + max(COL_PITCH * len(b["refs"]), 400.0),
                                    b["y1"] - 5.0))

    # ---- карта занятых точек выводов: отвод не должен проходить через чужой вывод,
    #      иначе KiCad соединит их, и сети схлопнутся в питание ----
    all_pins: dict[tuple[float, float], str] = {}
    for (ref, u), c in inst.items():
        for num in K.pin_positions(K.lib_symbol(by_ref[ref].lib_id), u):
            try:
                all_pins[c.pin_point(num)] = f"{ref}.{num}"
            except KeyError:
                pass

    # ---- реестр УЖЕ ПРОЛОЖЕННЫХ проводов -----------------------------------
    # Одного списка выводов мало. Два вывода одного корпуса на одном краю
    # (у LQFP-48 это, например, NRST и BOOT0) требуют обхода одновременно,
    # и без учёта уже проложенных проводов оба выбирают одну и ту же
    # свободную колонку: их вертикальные отводы становятся коллинеарными,
    # KiCad видит один непрерывный провод и сливает две разные сети.
    # Поэтому проверяем не только выводы, но и провода.
    drawn: list[tuple[float, float, float, float, str]] = []

    def emit(x1: float, y1: float, x2: float, y2: float, net: str) -> None:
        drawn.append((x1, y1, x2, y2, net))

    def wire_conflicts(x1: float, y1: float, x2: float, y2: float,
                       net: str) -> bool:
        """True, если отрезок (x1,y1)-(x2,y2) слился бы с чужим проводом.

        В KiCad пересечение проводов само по себе НЕ соединение — соединение
        даёт только общий узел (или накладка отрезков). Поэтому безопасны
        поперечные пересечения, и запрещаем ровно три вещи:
          1. накладку отрезков (коллинеарное перекрытие);
          2. конец одного отрезка внутри другого;
          3. совпадение концов с концами чужого провода.
        """
        lo_x, hi_x = sorted((x1, x2))
        lo_y, hi_y = sorted((y1, y2))
        for (ax, ay, bx, by, onet) in drawn:
            if onet == net:
                continue
            o_lo_x, o_hi_x = sorted((ax, bx))
            o_lo_y, o_hi_y = sorted((ay, by))
            # 1. коллинеарное перекрытие (оба отрезка должны быть
            #    параллельны друг другу, иначе проверка не про эту ось)
            if (hi_x - lo_x <= 0.01 and o_hi_x - o_lo_x <= 0.01
                    and abs(hi_x - o_hi_x) <= 0.01 and abs(lo_x - o_lo_x) <= 0.01):
                if hi_y > o_lo_y + 0.01 and lo_y < o_hi_y - 0.01:
                    return True
            if (hi_y - lo_y <= 0.01 and o_hi_y - o_lo_y <= 0.01
                    and abs(hi_y - o_hi_y) <= 0.01 and abs(lo_y - o_lo_y) <= 0.01):
                if hi_x > o_lo_x + 0.01 and lo_x < o_hi_x - 0.01:
                    return True
            # 2/3. любой конец одного внутри другого
            ends = [(x1, y1), (x2, y2), (ax, ay), (bx, by)]
            for ex, ey in ends:
                on_seg = ((o_lo_x - 0.01 <= ex <= o_hi_x + 0.01)
                          and (o_lo_y - 0.01 <= ey <= o_hi_y + 0.01))
                in_seg = ((lo_x - 0.01 <= ex <= hi_x + 0.01)
                          and (lo_y - 0.01 <= ey <= hi_y + 0.01))
                if on_seg and in_seg:
                    return True
        return False

    def conflicts_with(x1: float, y1: float, x2: float, y2: float,
                       net: str) -> list[tuple[float, float, float, float, str]]:
        lo_x, hi_x = sorted((x1, x2))
        lo_y, hi_y = sorted((y1, y2))
        out = []
        for (ax, ay, bx, by, onet) in drawn:
            if onet == net:
                continue
            o_lo_x, o_hi_x = sorted((ax, bx))
            o_lo_y, o_hi_y = sorted((ay, by))
            if (hi_x - lo_x <= 0.01 and o_hi_x - o_lo_x <= 0.01
                    and abs(hi_x - o_hi_x) <= 0.01 and abs(lo_x - o_lo_x) <= 0.01):
                if hi_y > o_lo_y + 0.01 and lo_y < o_hi_y - 0.01:
                    out.append((ax, ay, bx, by, onet))
                    continue
            if (hi_y - lo_y <= 0.01 and o_hi_y - o_lo_y <= 0.01
                    and abs(hi_y - o_hi_y) <= 0.01 and abs(lo_y - o_lo_y) <= 0.01):
                if hi_x > o_lo_x + 0.01 and lo_x < o_hi_x - 0.01:
                    out.append((ax, ay, bx, by, onet))
                    continue
            for ex, ey in [(x1, y1), (x2, y2), (ax, ay), (bx, by)]:
                on_seg = ((o_lo_x - 0.01 <= ex <= o_hi_x + 0.01)
                          and (o_lo_y - 0.01 <= ey <= o_hi_y + 0.01))
                in_seg = ((lo_x - 0.01 <= ex <= hi_x + 0.01)
                          and (lo_y - 0.01 <= ey <= hi_y + 0.01))
                if on_seg and in_seg:
                    out.append((ax, ay, bx, by, onet))
                    break
        return out

    def blocked(px: float, y0: float, y1: float, own: str) -> list[tuple[float, float]]:
        """Выводы, лежащие на вертикальном отводе (px, y0..y1), кроме своего."""
        lo, hi = min(y0, y1), max(y0, y1)
        out = []
        for (qx, qy), who in all_pins.items():
            if abs(qx - px) > 0.01 or not (lo < qy < hi) or who == own:
                continue
            out.append((qx, qy))
        return sorted(out, key=lambda p: abs(p[1] - y0))

    # ---- 3) локальные сети: шина под рядом ----
    for bi, nets in local_nets.items():
        b = bands[bi]
        for i, net in enumerate(nets):
            ry = g(b["ry0"] + i * RAIL_PITCH)
            conns = [(r, p) for r, p in dict(NETS)[net] if ref_block.get(r) == bi]
            pts = [pin_xy(r, p) for r, p in conns]
            if not pts:
                continue
            drop_x: list[float] = []
            for (ref, pin), (px, py) in zip(conns, pts):
                if abs(py - ry) <= 0.01:
                    drop_x.append(px)
                    continue
                num = probe[ref].resolve(pin)
                own = f"{ref}.{num}"

                def pin_clear(cand: float) -> bool:
                    return all(abs(cand - qx) > 0.01
                               for (qx, qy) in all_pins
                               if py <= qy <= ry)

                # Прямой отвод годится, только если на нём нет ни чужого
                # вывода, ни уже проложенного провода.
                hits = blocked(px, py, ry, own)
                if not hits and not wire_conflicts(px, py, px, ry, net):
                    emit(px, py, px, ry, net)
                    sh.wires.append(K.wire_node(px, py, px, ry))
                    sh.junctions.append(K.junction_node(px, ry))
                    drop_x.append(px)
                    continue

                # Обход: вынос вбок на свободную колонку, затем вниз.
                # Точка выноса обязана попасть В отрезок шины, иначе отвод
                # повиснет, поэтому её добавляем в drop_x. Ищем не только
                # свободную от выводов колонку, но и свободную от проводов:
                # иначе два соседних вывода корпуса займут одну колонку.
                sx = None
                for step in range(1, 200):
                    for cand in (round(px + step * 3.81, 2),
                                 round(px - step * 3.81, 2)):
                        if not pin_clear(cand):
                            continue
                        if wire_conflicts(cand, py, cand, ry, net):
                            continue
                        if wire_conflicts(px, py, cand, py, net):
                            continue
                        sx = cand
                        break
                    if sx is not None:
                        break
                if sx is None:
                    diag = [f"      [{onet}] ({ax},{ay})-({bx},{by})"
                            for (ax, ay, bx, by, onet)
                            in conflicts_with(px, py, px, ry, net)]
                    raise RuntimeError(
                        f"сеть {net}: не нашлась свободная колонка для отвода "
                        f"{ref}.{num} (x={px}, y={py}..{ry})\n"
                        + "\n".join(diag[:25]))
                emit(px, py, sx, py, net)
                emit(sx, py, sx, ry, net)
                sh.wires.append(K.wire_node(px, py, sx, py))
                sh.wires.append(K.wire_node(sx, py, sx, ry))
                sh.junctions.append(K.junction_node(sx, ry))
                drop_x.append(sx)
            xs = sorted(set(round(v, 2) for v in drop_x))
            for a, c in zip(xs, xs[1:]):
                emit(a, ry, c, ry, net)
                sh.wires.append(K.wire_node(a, ry, c, ry))
            sym = POWER_SYM.get(net)
            if sym:
                sh.power(sym, net, xs[0] - 6.35, ry)
                emit(xs[0] - 6.35, ry, xs[0], ry, net)
                sh.wires.append(K.wire_node(xs[0] - 6.35, ry, xs[0], ry))
            else:
                # Метка обязана лежать ТОЧНО на проводе (в его узле),
                # иначе KiCad не считает её частью цепи.
                sh.labels.append(K.label_node(net, xs[0], ry))

    # ---- 4) сквозные сети: метка прямо на выводе ----
    # Отводы-провода здесь опасны: любой горизонтальный отрезок шины
    # проходит через всю ширину блока, и вертикальный отвод пересекает
    # его — а пересечение в KiCad это соединение. Поэтому для сквозных
    # сетей метка ставится ТОЧНО в точку вывода: цепь замыкается по имени
    # метки, лишних проводов нет, короткого замыкания не возникает.
    for net, conns in NETS:
        if net in [n for ns in local_nets.values() for n in ns]:
            continue
        sym = POWER_SYM.get(net)
        for ref, pin in conns:
            if ref not in by_ref:
                continue
            px, py = pin_xy(ref, pin)
            if sym:
                sh.power(sym, net, px, py)
            else:
                sh.labels.append(K.label_node(net, px, py))

    sh.comps.extend(inst.values())
    return sh, {"bands": bands, "page_h": y + 12.0,
                "page_w": max(sh.page_w(), 1189.0)}


# --------------------------------------------------------------------------- #
def main() -> int:
    print("=" * 72)
    print("ГЕНЕРАТОР ПОЛНОЙ ПРИНЦИПИАЛЬНОЙ СХЕМЫ  BRIG  RECEIVER  (единый лист)")
    print("=" * 72)

    # ---- валидация списка соединений до раскладки ----
    errs, warns = [], []
    known = {p.ref for p in PARTS}
    seen_pin: dict[tuple[str, str], str] = {}
    for net, conns in NETS:
        if len(conns) < 2:
            warns.append(f"сеть {net}: всего {len(conns)} вывод(а) — висячий конец")
        for ref, pin in conns:
            if ref not in known:
                errs.append(f"сеть {net}: неизвестный компонент {ref}")
                continue
            try:
                num = probe[ref].resolve(pin)
            except KeyError as e:
                errs.append(str(e))
                continue
            key = (ref, num)
            if key in seen_pin:
                errs.append(f"пин {ref}.{num} («{seen_pin[key]}» и «{net}») "
                            f"принадлежит двум сетям")
            seen_pin[key] = net
    if errs:
        print(f"\nОШИБКИ ({len(errs)}):")
        for e in errs[:40]:
            print("   ", e)
        return 1
    for w in warns:
        print("  !", w)

    # ---- проверка корпусов по локальной библиотеке KiCad ----
    fp_root = Path("/usr/share/kicad/footprints")
    fp_errs = []
    for p in PARTS:
        if not p.footprint or ":" not in p.footprint:
            fp_errs.append(f"{p.ref}: корпус не задан")
            continue
        lib, name = p.footprint.split(":", 1)
        if not (fp_root / f"{lib}.pretty" / f"{name}.kicad_mod").is_file():
            fp_errs.append(f"{p.ref} = {p.value}: нет корпуса {p.footprint}")
    if fp_errs:
        print(f"\nОШИБКИ КОРПУСОВ ({len(fp_errs)}):")
        for e in fp_errs[:40]:
            print("   ", e)
        return 1
    print(f"\nOK  Все корпуса найдены в {fp_root} "
          f"({len({p.footprint for p in PARTS})} уникальных)")

    sh, geo = build()
    bands = geo["bands"]

    print(f"\nБлоков на листе        : {len(bands)}")
    print(f"Размер листа           : A0  {geo['page_w']:.0f} × {geo['page_h']:.0f} мм")
    print(f"Компонентов (в BOM)    : {len(PARTS)}")
    print(f"Символов питания       : {sh.n_pwr}")
    print(f"Сетей                  : {len(NETS)}")
    print(f"Проводов               : {len(sh.wires)}")
    print(f"Меток сетей            : {len(sh.labels)}")

    node = K.build_sch(sh.comps, sh.wires, sh.junctions, sh.labels,
                       sh.texts, sh.rects, paper="A0",
                       title="Brig Receiver — принципиальная схема (единый лист)",
                       date="2026-10-05", rev="2.0")
    K.write_sch(node, OUT_SCH)
    print(f"\nЗаписан файл  : {OUT_SCH}  ({OUT_SCH.stat().st_size:,} байт)")

    pro = json.loads(json.dumps(K.MINIMAL_PRO))
    pro["meta"]["filename"] = OUT_PRO.name
    pro["sheets"] = [[K.root_uuid(node), "Root"]]
    OUT_PRO.write_text(json.dumps(pro, ensure_ascii=False, indent=2))
    print(f"Записан проект: {OUT_PRO}")

    # ---- штатная проверка kicad-cli ----
    print("\n--- Проверка: kicad-cli sch export netlist ---")
    r = subprocess.run(["kicad-cli", "sch", "export", "netlist",
                        "-o", str(OUT_NET), str(OUT_SCH)],
                       capture_output=True, text=True)
    msg = (r.stdout + r.stderr).strip()
    if msg:
        print("\n".join(msg.splitlines()[:20]))
    if r.returncode != 0:
        print("kicad-cli не смог прочитать схему")
        return 1

    txt = OUT_NET.read_text(errors="ignore")
    n_comp = txt.count("(comp (ref")
    n_net = txt.count("(net (code")
    n_node = txt.count("(node ")
    if n_comp == 0 or n_net == 0:
        print(f"netlist пуст: компонентов={n_comp}, сетей={n_net}")
        return 1
    print(f"\nOK  KiCad принял схему: компонентов={n_comp}  сетей={n_net}  "
          f"узлов={n_node}")
    print(f"    {OUT_NET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
