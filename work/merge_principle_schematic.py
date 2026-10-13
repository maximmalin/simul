#!/usr/bin/env python3
"""
Brig Receiver — сборка единой принципиальной схемы (KiCad S-выражение)
из фрагментов: Antenna + RF Front + FPAA + DSP + Power.

Использует реальные .kicad_sch из work/subdevices/ (слова S-выражение).
Создаёт файл: work/brig_receiver_principle.kicad_sch

Это łączy_ALL блоки в единую схему:
  - Ant_A / Ant_B / Ant_C (0-900МГц / 1.2-3ГГц / 4-7ГГц)
  - PIN_Switches (коммутация каналов)
  - Mixer_1, Mixer_2, TV_Tuner, KT368A_Buffer
  - FPAA_Denoise (K140UD7), FPAA_Subtractor (K140UD8)
  - STM32_ADC, TinyML_Core, DMA buffer
  - ESP32_Buffer, ESP32_WiFi
  - Buck_Converter, LDO_Analog, Ferrite_Bead
  - Battery_LiPo
"""

import os
import re
from pathlib import Path
from typing import List, Tuple, Dict
import uuid

OUT_DIR = Path("/home/mirrage/Desktop/brig-receiver/work")
OUT_FILE = OUT_DIR / "brig_receiver_principle.kicad_sch"

SUBDEVICES_DIR = OUT_DIR / "subdevices"

# UUID генератор для уникальных элементов
def uuid4() -> str:
    return str(uuid.uuid4())

def load_sch_file(path: Path) -> str:
    """Загружает S-выражение из KiCad-файла, извлекает содержимое sheet 0."""
    content = path.read_text(encoding="utf-8")
    return content

def extract_sheet0(content: str) -> str:
    """Извлекает содержимое (sheet 0) из S-выражения KiCad 20240801."""
    # Ищем (sheet 0 "..." (0 0) (595 842))
    # и всё что внутри до закрывающей )
    pattern = r'\(sheet\s+0\s+"[^"]*"\s*\(0\s+0\)\s*\([^)]*\)\)\s*(.*?)\s*\)\s*$'
    m = re.search(pattern, content, re.DOTALL)
    if m:
        return m.group(1)
    # Если не нашлось — возвращаем всю и需 обработать
    return content

def extract_elements(content: str) -> List[Tuple[str, str, str, str, str]]:
    """
    Извлекает элементы из S-выражения:
    Возвращает список: (тип, value, description, at_coord, orient)
    """
    elements = []
    # Ищем (symbol name (value "...") (description "...") (inst 1) (at X Y) (orient R0))
    # и (text "..." (X Y) ...)
    symbol_pattern = r'\(symbol\s+(\w+)\s*\n\s*\(value\s+"([^"]*)"\)\s*\n\s*\(description\s+"([^"]*)"\)\s*\n\s*\(inst\s+(\d+)\)\s*\n\s*\(at\s+([\d.]+)\s+([\d.]+)\)\s*\n\s*\(orient\s+(\w+)\)\)'
    for m in re.finditer(symbol_pattern, content, re.DOTALL):
        el_type = m.group(1)
        value = m.group(2)
        desc = m.group(3)
        inst = m.group(4)
        x = m.group(5)
        y = m.group(6)
        orient = m.group(7)
        elements.append((el_type, value, desc, f"{x} {y}", orient, inst))
    
    # Ищем wire
    wires = []
    wire_pattern = r'\(wire\s+\(pt\s+([\d.]+)\s+([\d.]+)\)\s+\(pt\s+([\d.]+)\s+([\d.]+)\)\)'
    for m in re.finditer(wire_pattern, content):
        x1, y1, x2, y2 = m.group(1), m.group(2), m.group(3), m.group(4)
        wires.append((x1, y1, x2, y2))
    
    return elements, wires

def merge_sheets(elements_by_section: Dict, wires_by_section: Dict, offset_map: Dict) -> Tuple[List, List]:
    """
    Объединяет элементы из разных секций, применяя offset к координатам.
    """
    all_elements: List = []
    all_wires: List = []
    
    for section_name, elements in elements_by_section.items():
        offset_x, offset_y = offset_map.get(section_name, (0, 0))
        for el in elements:
            el_type, value, desc, coords, orient, inst = el
            x, y = map(float, coords.split())
            new_x = x + offset_x
            new_y = y + offset_y
            all_elements.append((el_type, value, desc, f"{new_x} {new_y}", orient, inst))
        
        for w in wires_by_section.get(section_name, []):
            x1, y1, x2, y2 = map(float, w)
            new_x1, new_y1 = x1 + offset_x, y1 + offset_y
            new_x2, new_y2 = x2 + offset_x, y2 + offset_y
            all_wires.append((str(new_x1), str(new_y1), str(new_x2), str(new_y2)))
    
    return all_elements, all_wires

def generate_principle_sch(all_elements: List, all_wires: List) -> str:
    """
    Генерирует единую S-выражение KiCad для принципиальной схемы.
    """
    lines = []
    lines.append("(kicad_sch")
    lines.append("    (version 20211123)")
    lines.append("    (generator \"brig-receiver-principle-schematic-v1.0\")")
    lines.append("    (paper \"A4\")")
    lines.append(f"    (uuid \"{uuid4()}\")")
    lines.append("    (lib_symbols)")
    lines.append("    (title_block")
    lines.append('        (title "Brig Receiver — Принципиальная схема (объединённая)")')
    lines.append('        (date "2026-09-17")')
    lines.append('        (rev "1.0")')
    lines.append('        (company "Brig Receiver Project")')
    lines.append("    )")
    
    # Генерируем уникальные UUID
    element_uuids = {}
    
    # Добавляем элементы
    for el in all_elements:
        el_type, value, desc, coords, orient, inst = el
        uid = uuid4()
        element_uuids[value] = uid
        x, y = coords.split()
        lines.append("    (symbol")
        lines.append(f"        (lib_id \"brig:receiver:{el_type}\")")
        lines.append(f"        (at {x} {y})")
        lines.append(f"        (orient {orient})")
        lines.append(f"        (unit {inst})")
        lines.append(f"        (value \"{value}\")")
        lines.append(f"        (description \"{desc}\")")
        lines.append(f"        (uuid \"{uid}\")")
        lines.append("    )")
    
    # Добавляем соединения (wires)
    for w in all_wires:
        x1, y1, x2, y2 = w
        lines.append("    (wire")
        lines.append("        (pts")
        lines.append(f"            (xy {x1} {y1})")
        lines.append(f"            (xy {x2} {y2})")
        lines.append("        )")
        lines.append("        (stroke")
        lines.append("            (width 0.00015)")
        lines.append("            (type solid)")
        lines.append("            (color 0 0 0 0)")
        lines.append("        )")
        lines.append(f"        (uuid \"{uuid4()}\")")
        lines.append("    )")
    
    lines.append(")")
    return "\n".join(lines)

def main():
    print("=" * 60)
    print("Brig Receiver — сборка единой принципиальной схемы")
    print("=" * 60)
    
    sections = {
        "antenna": SUBDEVICES_DIR / "antenna" / "kicad_antenna.kicad_sch",
        "rf_front": SUBDEVICES_DIR / "rf_front" / "kicad_rf_front.kicad_sch",
        "fpaa": SUBDEVICES_DIR / "fpaa" / "kicad_fpaa.kicad_sch",
        "dsp": SUBDEVICES_DIR / "dsp" / "kicad_dsp.kicad_sch",
        "power": SUBDEVICES_DIR / "power" / "kicad_power.kicad_sch",
    }
    
    # Смещения для размещения секций на одной странице
    offset_map = {
        "antenna": (0, 0),
        "rf_front": (0, 200),
        "fpaa": (0, 400),
        "dsp": (0, 600),
        "power": (0, 800),
    }
    
    elements_by_section = {}
    wires_by_section = {}
    
    for section_name, path in sections.items():
        if not path.exists():
            print(f"  ⚠ Файл не найден: {path}")
            continue
        
        print(f"\n  Обработка: {section_name} ({path.name})")
        content = load_sch_file(path)
        elements, wires = extract_elements(content)
        elements_by_section[section_name] = elements
        wires_by_section[section_name] = wires
        print(f"    Элементы: {len(elements)}, соединения: {len(wires)}")
    
    print("\n  Объединение секций...")
    all_elements, all_wires = merge_sheets(elements_by_section, wires_by_section, offset_map)
    print(f"    Всего элементов: {len(all_elements)}")
    print(f"    Всего соединений: {len(all_wires)}")
    
    print("\n  Генерация S-выражения...")
    sch_content = generate_principle_sch(all_elements, all_wires)
    
    OUT_FILE.write_text(sch_content, encoding="utf-8")
    print(f"\n  ✓ Создан файл: {OUT_FILE}")
    print(f"    Размер: {len(sch_content)} байт")
    print(f"    Элементов: {len(all_elements)}")
    print(f"    Соединений: {len(all_wires)}")

if __name__ == "__main__":
    main()
