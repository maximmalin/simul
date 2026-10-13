#!/usr/bin/env python3
"""dump_manifest.py — build a reproducible, machine-readable project manifest.

Writes:
    manifest.json    machine description: layout, scripts, dependencies, components

The component/net counts come from the SKiDL-derived ngspice deck, which is the
project's connectivity source of truth (`sim/scripts/sdr_skidl_circuit.py` ->
`sim/netlists/sdr_skidl_ngspice.cir`). If a populated KiCad schematic is ever
present, its official export is preferred; otherwise the deck is parsed.

Human-readable companion: AGENT_MAP.md.

Run:
    python3 tools/dump_manifest.py
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------------------
# Directory / file roles. kind: source (keep) / derived (regenerated) / data
# ---------------------------------------------------------------------------
LAYOUT: dict[str, tuple[str, str]] = {
    "sim/scripts": ("Physics, netlist, co-simulation, antenna and diagnostic drivers", "source"),
    "sim/netlists": ("ngspice decks produced by export_ngspice.py", "derived"),
    "sim/inputs": ("YAML parameter files (one per antenna)", "source"),
    "sim/outputs": ("Physics results (JSON/PNG)", "derived"),
    "sim/fmu": ("Built FMUs (SdrNgspiceFMU, antenna/Bragg blocks)", "data"),
    "sim/work": ("Solver scratch (openEMS, Elmer, gmsh)", "derived"),
    "sim/web_gui": ("Flask I/Q web GUI for the FMU", "source"),
    "sim/nec": ("NEC antenna results", "derived"),
    "sim/cad": ("FreeCAD / CAD geometry", "source"),
    "sim/octave": ("Octave formula checks", "source"),
    "firmware": ("STM32F103C8T6 firmware, cosim mailbox, PlatformIO config", "source"),
    "firmware/src": ("firmware.ino and cosim.h", "source"),
    "src/picsimlab_fmu": ("PicSimLab rcontrol <-> ngspice bridge package", "source"),
    "hardware": ("Hardware pipeline orchestrator", "source"),
    "hardware/kicad": ("KiCad schematic generator and symbol/S-expression library", "source"),
    "hardware/tscircuit": ("PCB emitter, 3D models, part selection, viewer", "source"),
    "hardware/antenna": ("Antenna panel geometry (FreeCAD)", "source"),
    "tools": ("Project spec and manifest/init tooling", "source"),
    "tools/tscircuit": ("Legacy placeholder PCB generator", "source"),
    "tests": ("Requirement-matrix and stress tests", "source"),
    "requirements": ("Requirement matrix and per-block requirements", "source"),
    "plan": ("Build plan", "source"),
    "work": ("Technical specs and schematic assembly scratch", "source"),
    "references": ("Formulas and sources", "source"),
    "config": ("Module parameters", "source"),
    "docs": ("Documentation (currently empty; see AGENT_MAP.md)", "source"),
    "local_footprints": ("KiCad footprint libraries", "data"),
    "out": ("Generated board artifacts (Gerbers/BOM/GLB/STEP)", "derived"),
    "node_modules": ("npm dependencies", "derived"),
}

# ---------------------------------------------------------------------------
# Automation: script -> purpose
# ---------------------------------------------------------------------------
AUTOMATION: dict[str, str] = {
    # signal path
    "sim/scripts/sdr_skidl_circuit.py": "SKiDL netlist — the single source of truth (60 parts)",
    "sim/scripts/export_ngspice.py": "SKiDL netlist -> ngspice deck",
    "sim/scripts/antenna_fdtd.py": "Antenna YAML -> FDTD -> S11/Zin -> Touchstone",
    "sim/scripts/mesh.py": "openEMS mesh sizing with a memory budget",
    "sim/scripts/export_antenna_ngspice.py": "Antenna results -> ngspice S-parameter networks",
    # co-simulation
    "sim/scripts/sdr_ngspice_fmu.py": "Self-contained FMI 2.0 ngspice FMU source",
    "sim/scripts/build_fmu.py": "Rebuild the FMU from SKiDL (no drift)",
    "sim/scripts/cosim_fmu_stm32.py": "Hard-lockstep firmware model <-> ngspice FMU",
    "sim/scripts/test_fmu_cosim.py": "FMU regression through the fmpy FMI master",
    "sim/scripts/cosim_real_stm32.py": "Real firmware.elf on QEMU <-> ngspice",
    "sim/scripts/gdb_remote.py": "Minimal GDB remote-serial-protocol client",
    "sim/scripts/build_cosim_firmware.py": "Build the COSIM_MODE firmware ELF",
    # PicSimLab
    "sim/scripts/start_picsimlab.py": "Launch PicSimLab NOGUI, poll rcontrol",
    "sim/scripts/sdr_picsimlab_cosim.py": "PicSimLab <-> ngspice co-sim driver",
    "sim/scripts/rcontrol.py": "Probe PicSimLab rcontrol",
    "sim/scripts/rcontrol_analog.py": "Drive PicSimLab analog pins over rcontrol",
    "sim/scripts/rcontrol_session.py": "One rcontrol session, all evidence at once",
    # tests
    "sim/scripts/test_sdr_netlist.py": "22 regression checks on the SKiDL->ngspice export",
    "sim/scripts/verify_pin_contract.py": "31 closed-loop analog + pin-contract checks",
    "sim/scripts/test_ngspice_nets.py": "Smoke tests for the ngspice decks",
    # firmware
    "firmware/scripts/hex.py": "Emit Intel HEX for PicSimLab loadhex",
    # hardware / manufacturing (transferred from brig-receiver)
    "tools/dump_manifest.py": "Build this manifest",
    "tools/init_project.py": "Create a fresh project from tracked files",
    "hardware/pipeline.py": "Hardware build pipeline",
    "hardware/kicad/kicad_lib.py": "Read/write KiCad symbols and schematics",
    "hardware/kicad/generate_brig_receiver.py": "Generate a full single-sheet .kicad_sch from code",
    "hardware/kicad/test_any_schematic.py": "Prove the generator is schematic-agnostic",
    "hardware/kicad/verify_nets.py": "Cross-check KiCad netlist against the generator tables",
    "hardware/tscircuit/build_pcb.mjs": "Netlist -> TSX, placement, model_offsets.json",
    "hardware/tscircuit/build_derived.py": "Build local derived tables from the jlcparts DB",
    "hardware/tscircuit/resolve_parts.py": "Substitute parts by value and footprint",
    "hardware/tscircuit/step2stl.py": "STEP -> STL with orientation normalization",
    "hardware/tscircuit/check_models3d.py": "Check glb against circuit.json, write offsets",
    "hardware/tscircuit/make_viewer.py": "Build the local 3D viewer page",
    "hardware/tscircuit/check_net.py": "Verify the viewer makes no external requests",
    "hardware/tscircuit/site_assets.py": "Move 3D models into the static site",
    "hardware/tscircuit/fetch_vendor_models.mjs": "Fetch vendor 3D models",
    "scripts/download-datasheets.js": "Fetch MPN datasheets via kicad-tools",
    "scripts/convert-kicad-to-circuit-json.js": "KiCad -> circuit.json",
    "block0_bus_power_calc.py": "5 V bus / buck / LC filter calculation",
    "gen_spice_from_kicad.py": "KiCad .kicad_sch -> SPICE netlist",
    "gost_pipeline.py": "STEP -> cleaned -> GOST drawing",
}

# External data without which some paths do not run
EXTERNAL_DATA: dict[str, str] = {
    "/usr/share/kicad/symbols": "KiCad symbol libraries",
    "/usr/share/kicad/footprints": "KiCad footprint libraries",
    "/home/mirrage/opt/openEMS": "openEMS install (antenna FDTD)",
    "/mnt/ext4data/jlcparts": "JLCPCB parts database (part resolver, 279 MB)",
    "/mnt/ext4data/downloads": "PicSimLab NOGUI AppImages",
}

SYSTEM_TOOLS = [
    "python3", "ngspice", "kicad-cli", "node", "npm",
    "pio", "qemu-system-arm", "gdb-multiarch", "gmsh", "ElmerSolver",
]


def sh(*args: str) -> str:
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=300)
        return (r.stdout or "").strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def tool_version(tool: str) -> str:
    if not shutil.which(tool):
        return "not installed"
    # ngspice needs -v: --version prints a banner of stars.
    v = sh(tool, "-v") or sh(tool, "--version") or sh(tool, "version")
    if not v:
        return "installed"
    for line in v.splitlines():
        if line.count("*") > 3:
            continue
        m = re.search(r"\d+\.\d+(\.\d+)?", line)
        if m:
            return m.group(0)
    return v.splitlines()[0][:80]


def dir_size(p: Path) -> str:
    total = 0
    for f in p.rglob("*"):
        if f.is_file() and not f.is_symlink():
            try:
                total += f.stat().st_size
            except OSError:
                pass
    return f"{total / 1024 / 1024:.1f} MB" if total else "0"


def collect_netlist() -> tuple[list[dict], dict]:
    """Parse the SKiDL-derived ngspice deck for the component/net inventory."""
    deck = ROOT / "sim/netlists/sdr_skidl_ngspice.cir"
    if not deck.is_file():
        return [], {"error": "no ngspice deck at sim/netlists/sdr_skidl_ngspice.cir"}

    elements: list[dict] = []
    nets: set[str] = set()
    skidl_parts = None
    for raw in deck.read_text(encoding="utf-8", errors="replace").splitlines():
        s = raw.strip()
        if not s:
            continue
        if s.startswith("*"):
            m = re.search(r"\((\d+)\s+parts\)", s)
            if m:
                skidl_parts = int(m.group(1))
            continue
        if s.startswith(".") or s.startswith("+"):
            continue
        toks = s.split()
        ref = toks[0]
        if not re.match(r"^[A-Za-z]", ref):
            continue
        # First two tokens after the ref are the terminals (skip value/expr).
        node_toks = [t for t in toks[1:3] if "=" not in t]
        nets.update(node_toks)
        elements.append({"ref": ref, "nodes": node_toks})

    by_prefix = Counter(e["ref"][0].upper() for e in elements)
    return elements, {
        "netlist_elements": len(elements),
        "net_labels_seen": len(nets),
        "skidl_parts": skidl_parts,
        "by_prefix": dict(sorted(by_prefix.items())),
    }


def collect_kicad_components() -> tuple[list[dict], dict]:
    """Official KiCad export, if the schematic actually contains symbols."""
    sch = ROOT / "hardware/kicad/sdr_receiver.kicad_sch"
    if not sch.is_file() or "symbol" not in sch.read_text(encoding="utf-8", errors="replace"):
        return [], {}
    tmp = ROOT / "out" / "_manifest.net.xml"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    if subprocess.run(
        ["kicad-cli", "sch", "export", "netlist", "--format", "kicadxml",
         str(sch), "-o", str(tmp)],
        capture_output=True, text=True, timeout=600,
    ).returncode != 0:
        return [], {"error": "kicad-cli could not export the netlist"}
    tree = ET.parse(tmp)
    tmp.unlink(missing_ok=True)
    items = []
    for c in tree.getroot().iter("comp"):
        props = {p.get("name", ""): (p.text or "").strip() for p in c.iter("property")}
        items.append({
            "ref": (c.get("ref") or "").strip(),
            "value": (c.findtext("value") or "").strip(),
            "footprint": (c.findtext("footprint") or "").strip(),
            "lcsc": props.get("LCSC", ""),
        })
    return items, {"kicad_components": len(items)}


def build() -> dict:
    layout = {}
    for rel, (role, kind) in LAYOUT.items():
        p = ROOT / rel
        layout[rel] = {
            "role": role,
            "kind": kind,
            "exists": p.exists(),
            "size": dir_size(p) if p.is_dir() else "",
        }

    automation = {
        rel: {"purpose": purpose, "exists": (ROOT / rel).exists()}
        for rel, purpose in AUTOMATION.items()
    }

    pkg = {}
    pj = ROOT / "package.json"
    if pj.is_file():
        try:
            d = json.loads(pj.read_text(encoding="utf-8"))
            pkg = {"dependencies": d.get("dependencies", {})}
        except (OSError, json.JSONDecodeError):
            pkg = {}

    components, verified = collect_netlist()
    kicad_components, kicad_summary = collect_kicad_components()
    if kicad_summary:
        verified.update(kicad_summary)

    return {
        "project": "kristall-radio",
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "purpose": "Zero-IF SDR direct-conversion receiver with STM32 Blue Pill; "
                   "code-generated hardware with ngspice/FMI co-simulation",
        "source_of_truth": "sim/scripts/sdr_skidl_circuit.py",
        "map": "AGENT_MAP.md",
        "status": "sim/COSIM_STATUS.md",
        "verified": verified,
        "layout": layout,
        "automation": automation,
        "dependencies": {
            "system_tools": {t: tool_version(t) for t in SYSTEM_TOOLS},
            "node": pkg,
            "external_data": {p: note for p, note in EXTERNAL_DATA.items() if Path(p).exists()},
            "external_data_missing": [p for p in EXTERNAL_DATA if not Path(p).exists()],
        },
        "components": kicad_components or components,
    }


def main() -> int:
    m = build()
    out = ROOT / "manifest.json"
    out.write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")
    v = m["verified"]
    print(f"manifest.json written: elements {v.get('netlist_elements', '?')}, "
          f"net labels {v.get('net_labels_seen', '?')}, "
          f"SKiDL parts {v.get('skidl_parts', '?')}")
    print(f"layout entries: {len(m['layout'])}, scripts: {len(m['automation'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
