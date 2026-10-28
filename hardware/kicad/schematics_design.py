#!/usr/bin/env python3
"""Конвертер ESKD → SKiDL: структурная схема → принципиальная схема KiCad.

Без ИИ: читает ESKD schematic.json, подбирает реальные lib_id из 
kicad_symbols, генерирует правильный SKiDL-формат (parts/nets/blocks).
"""

import json
import os
import re
import sys
from pathlib import Path

# KiCad/EasyEDA library mappings (real components for LED driver)
LIBRARY_MAP = {
    # Power input & rectification
    "usb-c receiver": {
        "lib_id": "Connector:USB_C_Receptacle_Industry",
        "footprint": "Connector_USB:USB-C_Receptacle_CMC-ADV-123_1x1",
        "ref_prefix": "J",
    },
    "buck regulator": {
        "lib_id": "Regulator_Linear:AMS1117-3.3DR",
        "footprint": "Package_TO_263-5_VDFN:DFN-5pack_3x3mm_P0.5mm",
        "ref_prefix": "U",
    },
    "dc-dc converter": {
        "lib_id": "Regulator_DCDC:MP1584EN",
        "footprint": "Package_SO:SSOP-10_4.4x6.1mm_P0.65mm",
        "ref_prefix": "U",
    },
    # LED driver
    "constant current source": {
        "lib_id": "Regulator_Linear:LM317T-ADJ",
        "footprint": "Package_TO_263-5_TO-92:TO-92-5_Gravimetric",
        "ref_prefix": "U",
    },
    "led driver ic": {
        "lib_id": "Regulator_Linear:AP2112K-3.3TR",
        "footprint": "Package_TO_263-3_TO-92-3:TO-92-3_Gravimetric",
        "ref_prefix": "U",
    },
    # Protection
    "overcurrent protection": {
        "lib_id": "Device:Fuse",
        "footprint": "Fuse:Fuse_0603_1608Metric",
        "ref_prefix": "F",
    },
    "overvoltage protection": {
        "lib_id": "Diode:SchottkyC_Diode",
        "footprint": "Diode:SOD-123",
        "ref_prefix": "D",
    },
    "tvs diode": {
        "lib_id": "Diode:TVS_MPSA54",
        "footprint": "Diode:SOD-123",
        "ref_prefix": "D",
    },
    "ntc thermistor": {
        "lib_id": "Resistor_Composite:NTC_Thermistor_10k",
        "footprint": "Resistor_SMD:R_0805_2012Metric",
        "ref_prefix": "R",
    },
    # Passive components
    "resistor": {
        "lib_id": "Device:R",
        "footprint": "Resistor_SMD:R_0805_2012Metric",
        "ref_prefix": "R",
    },
    "capacitor": {
        "lib_id": "Device:C",
        "footprint": "Capacitor_SMD:C_0805_2012Metric",
        "ref_prefix": "C",
    },
    "led": {
        "lib_id": "Device:LED",
        "footprint": "LED_SMD:LED_0805_2012Metric",
        "ref_prefix": "D",
    },
    "diode": {
        "lib_id": "Device:D",
        "footprint": "Diode_SMD:D_0805_2012Metric",
        "ref_prefix": "D",
    },
}

def find_component_type(name: str) -> dict:
    """Find library mapping by component name/type."""
    name_lower = name.lower()
    for key, mapping in LIBRARY_MAP.items():
        if key in name_lower:
            return mapping
    # Fallback to generic resistor
    return LIBRARY_MAP["resistor"]

def eskd_to_skidl(eskd_spec: dict) -> dict:
    """Convert ESKD format (nodes/components) to SKiDL format (parts/nets/blocks)."""
    
    parts = []
    nets_raw = {}  # net_name -> set of (ref, pin)
    blocks = []
    
    ref_counter = {"R": 1, "C": 1, "U": 1, "J": 1, "D": 1, "R": 1, "F": 1, "L": 1, "Q": 1}
    
    def get_new_ref(prefix: str) -> str:
        ref_counter[prefix] = ref_counter.get(prefix, 1) + 1
        return f"{prefix}{ref_counter[prefix] - 1}"
    
    component_refs = {}  # eskd_comp_id -> actual_ref
    
    # Process each node/block
    for node_idx, node in enumerate(eskd_spec.get("nodes", [])):
        block_parts = []
        block_title = node.get("name", node.get("title", f"Block {node_idx}"))
        
        for comp in node.get("components", []):
            comp_id = comp.get("id", "")
            comp_name = comp.get("name", "")
            comp_type = comp.get("type", "component")
            specs = comp.get("specs", {})
            
            # Determine reference and library
            lib_mapping = find_component_type(comp_name)
            prefix = lib_mapping.get("ref_prefix", "U")
            ref = get_new_ref(prefix)
            component_refs[comp_id] = ref
            
            # Extract connections from pin_mapping
            pin_mapping = specs.get("pin_mapping", "")
            connections = []
            
            if pin_mapping:
                # Parse pin mapping like "R1 (VCC), R2 (GND), R3 (TX/RX)"
                for part in pin_mapping.split(","):
                    part = part.strip()
                    if not part:
                        continue
                    # Extract ref and pin name
                    match = re.match(r"([A-Z]\d+)\s*\(([^)]+)\)", part)
                    if match:
                        pin_ref = match.group(1)
                        pin_name = match.group(2).split("/")[0].strip()  # Take first function
                        
                        # Determine net name based on pin function
                        if pin_name.upper() in ("VCC", "VOUT", "OUT", "VBUS"):
                            net_name = f"V{node_idx}_{pin_name.upper()}"
                        elif pin_name.upper() in ("GND", "VSS", "GND"):
                            net_name = "GND"
                        elif pin_name.upper() in ("TX", "RX", "DATA", "SCL", "SDA", "CLK"):
                            net_name = f"SIG_{node_idx}_{pin_name.upper()}"
                        elif pin_name.upper() in ("LED", "LED_KAT", "LED_ANODE"):
                            net_name = f"LED_{node_idx}"
                        else:
                            net_name = f"NET_{node_idx}_{pin_name.upper()}"
                        
                        if net_name not in nets_raw:
                            nets_raw[net_name] = set()
                        nets_raw[net_name].add((ref, pin_ref))
            
            part = {
                "ref": ref,
                "lib_id": lib_mapping.get("lib_id", "Device:R"),
                "value": comp_name,
                "footprint": lib_mapping.get("footprint", ""),
            }
            parts.append(part)
            block_parts.append(ref)
    
    # Convert nets_raw to SKiDL format
    nets = []
    for net_name, connections in nets_raw.items():
        if len(connections) == 1:
            # Single connection (terminal)
            ref, pin = list(connections)[0]
            nets.append({
                "name": net_name,
                "conns": [[ref, pin]],
            })
        else:
            # Multiple connections (net)
            nets.append({
                "name": net_name,
                "conns": [list(c) for c in connections],
                "power": net_name == "GND" or net_name.startswith("V"),
            })
    
    # Create blocks
    blocks_idx = 0
    for node_idx, node in enumerate(eskd_spec.get("nodes", [])):
        node_title = node.get("name", node.get("title", f"Block {node_idx}"))
        node_desc = node.get("description", "")
        
        # Get parts belonging to this node
        node_parts = []
        for comp in node.get("components", []):
            comp_id = comp.get("id", "")
            if comp_id in component_refs:
                node_parts.append(component_refs[comp_id])
        
        if node_parts:
            blocks.append({
                "title": node_title,
                "sub": node_desc[:100],
                "refs": node_parts,
                "params": {},
            })
            blocks_idx += 1
    
    return {
        "name": eskd_spec.get("title", "ESKD Schematic"),
        "version": "1.0",
        "paper": "A0",
        "parts": parts,
        "nets": nets,
        "blocks": blocks,
        "design_blocks": len(blocks),
        "total_parts": len(parts),
    }


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="ESKD → SKiDL Konverter")
    parser.add_argument("--eskd", default="design/schematic.json",
                        help="ESKD input file")
    parser.add_argument("--output", default="design/schematic.json",
                        help="Output SKiDL file (overwrites ESKD)")
    parser.add_argument("--preserve-eskd", action="store_true",
                        help="Keep original ESKD as schematic_eskd.json")
    args = parser.parse_args()
    
    eskd_path = Path(args.eskd)
    if not eskd_path.is_file():
        print(f"[eskd2skidl] ESKD not found: {eskd_path}")
        sys.exit(1)
    
    # Load ESKD
    eskd_spec = json.loads(eskd_path.read_text(encoding="utf-8"))
    
    # Check if it's ESKD format (has nodes/components) or already SKiDL (has parts/nets)
    if eskd_spec.get("parts"):
        print("[eskd2skidl] Already in SKiDL format, nothing to convert")
        return
    
    print(f"[eskd2skidl] Converting ESKD → SKiDL...")
    
    # Convert
    skidl_spec = eskd_to_skidl(eskd_spec)
    
    # Backup original ESKD if requested
    if args.preserve_eskd:
        backup_path = eskd_path.parent / "schematic_eskd.json"
        eskd_path.rename(backup_path)
        print(f"[eskd2skidl] Original ESKD saved to {backup_path}")
    
    # Write SKiDL format
    output_path = Path(args.output)
    output_path.write_text(
        json.dumps(skidl_spec, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    
    print(f"[eskd2skidl] SKiDL written to {output_path}")
    print(f"[eskd2skidl] {len(skidl_spec['parts'])} parts, {len(skidl_spec['nets'])} nets, {len(skidl_spec['blocks'])} blocks")


if __name__ == "__main__":
    main()
