#!/usr/bin/env python3
"""
Brig Receiver — конвертер KiCad .kicad_sch → SPICE netlist (.cir) для ngspice.
Парсит KiCad S-Expression файл, извлекает компоненты и пины, генерирует SPICE.
"""

import os
import sys
import re
import uuid

sys.path.insert(0, '/home/mirrage/Desktop/brig-receiver/.venv/lib/python3.13/site-packages')

from faebryk.libs.sexp.dataclass_sexp import Symbol, SEXP_File
from faebryk.libs.kicad.fileformats_sch import C_kicad_sch_file
from faebryk.libs.kicad.fileformats_common import C_xy, C_xyr, gen_uuid

OUT = "/home/mirrage/Desktop/brig-receiver/hardware/kicad"
SPICE_OUT = "/home/mirrage/Desktop/brig-receiver/hardware/spice"
os.makedirs(SPICE_OUT, exist_ok=True)

# Сопоставление типов компонентов KiCad → SPICE-типы
KICAD_TO_SPICE = {
    "Device:Buck": "X",   # Buck regulator
    "Device:LDO": "X",    # LDO regulator
    "Device:Battery": "V",  # voltage source
    "Device:VCC": "V",    # voltage source
    "Device:Capacitor": "C",
    "Device:Inductor": "L",
    "Device:Resistor": "R",
    "Device:Diode": "D",
    "Device:PIN_Diode": "D",
    "Device:BJT": "Q",
    "Device:Photodiode": "D",
    "Device:OpAmp": "X",  # op-amp modeled as X
    "Device:Microcontroller": "X",
    "Device:Mixer": "X",  # mixer
    "Device:RF_Module": "X",
    "Device:Antenna": "E",  # behavioral source
    "Device:Fuse": "R",   # fuse modeled as low-R resistor
    "Device:STMicro": "X",
    "Device:ESP32": "X",
    "Device:Connector": "X",
    "Device:GND": "0",    # ground
    "Device:Crystal": "X",
}

# Сопоставление имён пинов → SPICE-имена (упрощённо — индексы)
PIN_ALIAS = {
    "VCC_IN": "1",
    "GND": "0",
    "VIN": "1",
    "VOUT": "2",
    "SW": "3",
    "FB": "4",
    "EN": "5",
    "1": "1",
    "2": "2",
    "3": "3",
    "4": "4",
    "5": "5",
    "NonInverting+": "1",
    "Inverting-": "2",
    "VCC+": "3",
    "VCC-": "4",
    "Output": "6",
    "Compensation": "7",
    "NC": "8",
    "Cathode": "1",
    "Anode": "2",
    "Base": "1",
    "Emitter": "2",
    "Collector": "3",
    "RF_IN_A": "1",
    "RF_IN_B": "1",
    "RF_IN_C": "1",
    "RF_IN": "1",
    "RF_OUT": "2",
    "VCC": "3",
    "GND_pin": "0",
    "PA0": "1",
    "PA1": "2",
    "3.3V": "3",
    "5V": "1",
    "5V_sterile": "1",
    "5V_digital": "1",
    "MOSI": "1",
    "MISO": "2",
    "SCK": "3",
    "CS": "4",
    "GPIO0": "1",
    "GPIO2": "2",
    "VDD": "3",
    "SPI_MOSI": "1",
    "SPI_MISO": "2",
    "SPI_SCK": "3",
    "SPI_CS": "4",
    "RF": "1",
    "3.3V_pin": "3",
    "GND_pin2": "0",
}

NET_NAMES = {}  # отслеживаем присвоенные имена net


def parse_kicad_sch(path):
    """Парсинг .kicad_sch файла через Faebryk dataclasses"""
    sch_file = C_kicad_sch_file.loads(path)
    return sch_file.kicad_sch


def extract_components(kicad_sch):
    """Извлечь компоненты (symbols) из C_kicad_sch"""
    comps = []
    for sym in kicad_sch.symbols:
        ref = sym.propertys.get("Reference", None)
        val = sym.propertys.get("Value", None)
        fp = sym.propertys.get("Footprint", None)
        
        if ref and val:
            ref_str = ref.value
            val_str = val.value
            lib_id = sym.lib_id
            
            # Определить SPICE-тип
            spice_type = KICAD_TO_SPICE.get(lib_id, "X")
            
            comps.append({
                "ref": ref_str,
                "value": val_str,
                "lib_id": lib_id,
                "footprint": fp.value if fp else "",
                "spice_type": spice_type,
                "pins": [(p.name, p.uuid) for p in sym.pins] if sym.pins else [],
            })
    
    return comps


def generate_spice(components, output_path):
    """Генерация SPICE netlist из списка компонентов"""
    spice_lines = []
    spice_lines.append("* Brig Receiver — SPICE netlist from KiCad .kicad_sch")
    spice_lines.append(f"* Generated from: {output_path}")
    spice_lines.append("")
    
    # Анализ соединений по проводам
    wire_nets = {}
    net_counter = 1
    
    # Определяем net для каждого пина
    pin_nets = {}  # (ref, pin_name) -> net_name
    
    # Простой подход: нумеруем nets по порядку
    # Компоненты подключаются через общие GND и другие связи
    
    for i, comp in enumerate(components):
        spice_type = comp["spice_type"]
        ref = comp["ref"]
        val = comp["value"]
        
        pins = comp["pins"]
        num_pins = len(pins)
        
        if spice_type == "X":
            # Subcircuit component (Buck, LDO, OpAmp, MCU, etc.)
            # Создаём X-компонент с pin-ами
            pin_strs = []
            for j, (pin_name, _) in enumerate(pins):
                if j == 0:
                    net_name = f"{ref}_pin{j+1}"
                else:
                    net_name = f"{ref}_pin{j+1}"
                pin_strs.append(net_name)
                pin_nets[(ref, pin_name)] = net_name
            
            spice_lines.append(
                f"{ref} {' '.join(pin_strs)} {spice_type} {val}"
            )
        
        elif spice_type == "V":
            # Voltage source
            if num_pins >= 2:
                pos_net = f"{ref}_pin1"
                neg_net = f"{ref}_pin2"
                pin_nets[(ref, pins[0][0])] = pos_net
                pin_nets[(ref, pins[1][0])] = neg_net
                spice_lines.append(
                    f"{ref} {pos_net} {neg_net} DC {val}"
                )
            elif num_pins == 1:
                net = f"{ref}_pin1"
                pin_nets[(ref, pins[0][0])] = net
                spice_lines.append(
                    f"{ref} {net} 0 DC {val}"
                )
        
        elif spice_type == "C":
            if num_pins >= 2:
                net1 = f"{ref}_pin1"
                net2 = f"{ref}_pin2"
                pin_nets[(ref, pins[0][0])] = net1
                pin_nets[(ref, pins[1][0])] = net2
                spice_lines.append(f"C{ref} {net1} {net2} {val}")
        
        elif spice_type == "L":
            if num_pins >= 2:
                net1 = f"{ref}_pin1"
                net2 = f"{ref}_pin2"
                pin_nets[(ref, pins[0][0])] = net1
                pin_nets[(ref, pins[1][0])] = net2
                spice_lines.append(f"L{ref} {net1} {net2} {val}")
        
        elif spice_type == "R":
            if num_pins >= 2:
                net1 = f"{ref}_pin1"
                net2 = f"{ref}_pin2"
                pin_nets[(ref, pins[0][0])] = net1
                pin_nets[(ref, pins[1][0])] = net2
                spice_lines.append(f"R{ref} {net1} {net2} {val}")
        
        elif spice_type == "D":
            if num_pins >= 2:
                net1 = f"{ref}_pin1"
                net2 = f"{ref}_pin2"
                pin_nets[(ref, pins[0][0])] = net1
                pin_nets[(ref, pins[1][0])] = net2
                spice_lines.append(f"D{ref} {net1} {net2} {val}")
        
        elif spice_type == "Q":
            if num_pins >= 3:
                base_net = f"{ref}_pin1"
                em_net = f"{ref}_pin2"
                col_net = f"{ref}_pin3"
                pin_nets[(ref, pins[0][0])] = base_net
                pin_nets[(ref, pins[1][0])] = em_net
                pin_nets[(ref, pins[2][0])] = col_net
                spice_lines.append(f"Q{ref} {col_net} {base_net} {em_net} {val}")
        
        elif spice_type == "E":
            if num_pins >= 2:
                in_net = f"{ref}_pin1"
                out_net = f"{ref}_pin2"
                pin_nets[(ref, pins[0][0])] = in_net
                pin_nets[(ref, pins[1][0])] = out_net
                spice_lines.append(f"E{ref} {out_net} 0 {in_net} 1")
        
        elif spice_type == "0":
            # GND — resolve to ground net
            for j, (pin_name, _) in enumerate(pins):
                pin_nets[(ref, pin_name)] = "0"
        
        spice_lines.append("")
    
    # Теперь добавляем соединения (провода между компонентами)
    # В KiCad провода определяют соединённые ноды
    # Для упрощения: все GND-пины → 0, остальные — уникальные
    
    # Смотрим провод lines в файле (нужно перепарсить)
    # Для нашей генерации — просто присваиваем уникальные nets
    
    # Определяем соединения по общим GND
    spice_lines.append("* === Net definitions (GND = 0) ===")
    spice_lines.append("")
    
    # Добавляем анализ .TRAN для ngspice
    spice_lines.append("* === Transient analysis ===")
    spice_lines.append(".TRAN 0.1u 1m")
    spice_lines.append(".END")
    
    # Записываем файл
    with open(output_path, 'w') as f:
        f.write("\n".join(spice_lines))
    
    return pin_nets


def convert_sch_to_spice(sch_path, output_name):
    """Конвертировать .kicad_sch в .cir SPICE netlist"""
    print(f"  Парсинг: {sch_path}")
    try:
        sch = parse_kicad_sch(sch_path)
        comps = extract_components(sch)
        if not comps:
            print(f"    Нет компонентов — пустой файл?")
            return []
        
        print(f"    Найдено компонентов: {len(comps)}")
        for c in comps:
            print(f"      {c['ref']}: {c['value']} ({c['spice_type']}) — {[p[0] for p in c['pins']]}")
        
        out_path = os.path.join(SPICE_OUT, output_name)
        generate_spice(comps, out_path)
        print(f"    Сохранено: {out_path}")
        return comps
    except Exception as e:
        print(f"    Ошибка: {e}")
        import traceback
        traceback.print_exc()
        return []


if __name__ == "__main__":
    print("\n=== Конвертация KiCad .kicad_sch → SPICE .cir для ngspice ===")
    
    blocks = [
        ("block0", "block0_5v_bus.kicad_sch", "block0_spice.cir"),
        ("block1", "block1_rf_frontend.kicad_sch", "block1_spice.cir"),
        ("block3", "block3_fpaa_stm32.kicad_sch", "block3_spice.cir"),
        ("block4", "block4_esp32_wifi.kicad_sch", "block4_spice.cir"),
    ]
    
    for block, sch_name, spice_name in blocks:
        sch_path = os.path.join(OUT, block, sch_name)
        print(f"\n=== Блок {block}: {sch_name} ===")
        
        if os.path.exists(sch_path):
            convert_sch_to_spice(sch_path, spice_name)
        else:
            print(f"    Файл не найден: {sch_path}")

    print("\n=== Готово! SPICE файлы в hardware/spice/ ===")
    for f in os.listdir(SPICE_OUT):
        path = os.path.join(SPICE_OUT, f)
        print(f"  {path} ({os.path.getsize(path)} bytes)")
