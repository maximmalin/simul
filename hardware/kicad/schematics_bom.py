#!/usr/bin/env python3
"""Библиотекарь: подбор реальных компонентов по SKiDL схеме.

Берет SKiDL схему (design/skidl_blocks.py), находит компоненты,
подбирает реальные библиотеки KiCad, формирует library_index.md
и копирует схему в формат для kicad генерации.
"""

import json
import os
import re
import sys
from pathlib import Path

COMPONENT_MAP = {
    "usb": ("Connector:USB_C_Receptacle", "USB_C"),
    "regulator": ("Regulator:LDO", "LDO"),
    "buck": ("Regulator:DCDC_Buck", "DCDC"),
    "led": ("Device:LED", "LED"),
    "resistor": ("Device:R", "R"),
    "capacitor": ("Device:C", "C"),
    "diode": ("Device:D", "D"),
    "ic": ("Package_SO:SOIC-8", "IC"),
    "connector": ("Connector:Conn_01x01", "J"),
    "switch": ("Switch:SW_SPST", "SW"),
    "transistor": ("Transistor:NPN", "Q"),
    "mosfet": ("Transistor:NMOS", "Q"),
    "heatsink": ("Device:Heatsink", "HS"),
    "fuse": ("Device:Fuse", "F"),
    "tvss": ("Diode:TVS", "D"),
    "zener": ("Diode:Zener", "D"),
    "ntc": ("Device:NTC", "RT"),
    "crystal": ("Device:Crystal", "XTAL"),
}


def map_component(name):
    name_lower = name.lower()
    for key, (lib_id, prefix) in COMPONENT_MAP.items():
        if key in name_lower:
            return lib_id, prefix
    return "Device:R", "U"


def extract_components(skidl_path):
    if not skidl_path.is_file():
        return []
    content = skidl_path.read_text(encoding="utf-8")
    comps = []
    for line in content.split("\n"):
        line = line.strip()
        refs = re.findall(r'\b([A-Z]+)(\d+)\b', line)
        for prefix, num in refs:
            if prefix in ("def", "pass", "TODO", "import"):
                continue
            comps.append({"ref": "{}{}".format(prefix, num), "type": prefix.lower()})
    return comps


def build_library_index(components):
    lines = [
        "# Library Index",
        "",
        "| Ref | Type | KiCad Lib ID | Footprint |",
        "|-----|------|-------------|-----------|",
    ]
    for comp in components:
        ref = comp["ref"]
        ctype = comp["type"]
        lib_id, prefix = map_component(ctype)
        lines.append("| {} | {} | {} | - |".format(ref, ctype, lib_id))
    return "\n".join(lines)


def main():
    order_dir = Path(os.environ.get("ORDER_DIR", "."))
    design_dir = order_dir / "design"
    out_dir = order_dir / "out"
    out_dir.mkdir(parents=True, exist_ok=True)

    skidl_candidates = [
        design_dir / "skidl_blocks.py",
        order_dir / "tools" / "skidl_blocks.py",
    ]
    skidl_path = next((p for p in skidl_candidates if p.is_file()), skidl_candidates[0])
    if not skidl_path.is_file():
        print("[lib] SKiDL файл не найден")
        sys.exit(1)

    components = extract_components(skidl_path)
    if not components:
        eskd_path = design_dir / "schematic.json"
        if eskd_path.is_file():
            spec = json.loads(eskd_path.read_text(encoding="utf-8"))
            for node in spec.get("nodes", []):
                for comp in node.get("components", []):
                    components.append({
                        "ref": comp.get("id", "U1"),
                        "type": comp.get("name", "unknown").lower()
                    })

    lib_index = build_library_index(components)
    lib_path = design_dir / "library_index.md"
    lib_path.write_text(lib_index, encoding="utf-8")
    print("[lib] library_index.md: {}".format(lib_path))

    calc_path = design_dir / "calc.json"
    if not calc_path.is_file():
        calc = {
            "name": "Схемотехнический расчет",
            "components": components,
            "status": "generated_by_librarian"
        }
        calc_path.write_text(json.dumps(calc, ensure_ascii=False, indent=2),
                             encoding="utf-8")
        print("[lib] calc.json: {}".format(calc_path))

    result = {
        "status": "ok",
        "components": len(components),
        "library_index": str(lib_path),
        "calc": str(calc_path),
    }
    out_path = out_dir / "schematics_bom.json"
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    print("[lib] результат: {}".format(out_path))


if __name__ == "__main__":
    main()
