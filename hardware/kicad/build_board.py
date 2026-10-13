#!/usr/bin/env python3
"""Build the Krystal SDR board and its manufacturing outputs.

Code-first pipeline: ``sim/scripts/sdr_skidl_circuit.py`` (the SKiDL circuit)
is the single source of truth. Running this script regenerates every derived
board artifact:

    hardware/kicad/sdr_receiver.kicad_sch   KiCad netlist           (SKiDL)
    out/board.kicad_pcb                     board definition        (kinet2pcb)
    out/gerbers/, out/drill/, out/gerbers.zip                       (kicad-cli)
    out/board.step, out/board.glb           3D models               (kicad-cli)
    out/board.svg, out/board.pdf, out/board.dxf, out/pnp.csv        (kicad-cli)
    out/bom.csv                             grouped bill of materials

Nothing here is hand-edited; delete ``out/`` and re-run to rebuild it.

Prerequisites: the project virtualenv (``.venv``) with SKiDL + kinet2pcb,
the KiCad CLI (``kicad-cli``) and the stock KiCad footprint libraries.

Usage:  .venv/bin/python hardware/kicad/build_board.py
"""

from __future__ import annotations

import csv
import os
import re
import subprocess
import sys
import zipfile
from collections import defaultdict
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
OUT = PROJECT / "out"
NETLIST = PROJECT / "hardware" / "kicad" / "sdr_receiver.kicad_sch"
PCB = OUT / "board.kicad_pcb"
SKIDL = PROJECT / "sim" / "scripts" / "sdr_skidl_circuit.py"

VENV_BIN = Path(sys.executable).parent
KICAD_FP_ROOT = os.environ.get("KICAD_FP_ROOT", "/usr/share/kicad/footprints")

# Copper only, and the full drawing set (copper + silk + mask + outline).
COPPER = "F.Cu,B.Cu"
DRAWING = "F.Cu,B.Cu,F.Silkscreen,B.Silkscreen,F.Mask,B.Mask,Edge.Cuts"

# A KiCad netlist component block. The optional ``description`` field is
# emitted by SKiDL between ``value`` and ``footprint``.
_COMP = re.compile(
    r'\(comp\s+\(ref\s+"([^"]+)"\)\s+'
    r'\(value\s+"([^"]*)"\)'
    r'(?:\s+\(description\s+"[^"]*"\))?'
    r'\s+\(footprint\s+"([^"]*)"\)'
)


def run(cmd: list, **kwargs) -> subprocess.CompletedProcess:
    printable = " ".join(str(c) for c in cmd)
    print(f"  $ {printable}")
    return subprocess.run([str(c) for c in cmd], check=True, **kwargs)


def build_netlist() -> None:
    print("[1/6] SKiDL netlist")
    run([sys.executable, SKIDL])


def build_pcb() -> None:
    print("[2/6] Board definition (kinet2pcb)")
    kinet2pcb = VENV_BIN / "kinet2pcb"
    if not kinet2pcb.exists():
        raise SystemExit(f"kinet2pcb not found at {kinet2pcb}")
    # kinet2pcb prints a harmless gtk/action_plugin assert on stderr and can
    # return non-zero while still writing the board, so verify the file.
    subprocess.run(
        [
            str(kinet2pcb),
            "--input", str(NETLIST),
            "--output", str(PCB),
            "--libraries", KICAD_FP_ROOT,
            "--overwrite", "--nobackup",
        ],
        check=False,
    )
    if not PCB.exists() or PCB.stat().st_size == 0:
        raise SystemExit("kinet2pcb did not produce a board")


def _kicad(*args: str) -> None:
    run(["kicad-cli", "pcb", "export", *args])


def build_fabrication() -> None:
    print("[3/6] Gerbers + drill")
    gerbers = OUT / "gerbers"
    drill = OUT / "drill"
    _kicad("gerbers", "-o", str(gerbers), str(PCB))
    _kicad("drill", "-o", str(drill), str(PCB))
    with zipfile.ZipFile(OUT / "gerbers.zip", "w", zipfile.ZIP_DEFLATED) as z:
        for folder in (gerbers, drill):
            for f in sorted(folder.glob("*")):
                if f.is_file():
                    z.write(f, f.name)


def build_3d() -> None:
    print("[4/6] 3D models (STEP + GLB)")
    _kicad("step", "-o", str(OUT / "board.step"), str(PCB))
    _kicad("glb", "-o", str(OUT / "board.glb"), str(PCB))


def build_drawings() -> None:
    print("[5/6] Drawings (SVG, PDF, DXF) + placement")
    _kicad("svg", "--mode-single", "-l", DRAWING, "-o", str(OUT / "board.svg"), str(PCB))
    _kicad("pdf", "--mode-single", "-l", DRAWING, "--include-border-title",
          "-o", str(OUT / "board.pdf"), str(PCB))
    _kicad("dxf", "--mode-single", "-l", DRAWING, "-o", str(OUT / "board.dxf"), str(PCB))
    _kicad("pos", "--format", "csv", "--units", "mm", "-o", str(OUT / "pnp.csv"), str(PCB))


def _natural(key: str):
    return [int(p) if p.isdigit() else p for p in re.split(r"(\d+)", key)]


def build_bom() -> None:
    print("[6/6] Bill of materials")
    text = NETLIST.read_text(encoding="utf-8", errors="replace")
    groups: dict[tuple, list] = defaultdict(list)
    for ref, value, footprint in _COMP.findall(text):
        groups[(value, footprint)].append(ref)

    rows = []
    for (value, footprint), refs in groups.items():
        refs.sort(key=_natural)
        rows.append((refs, value, footprint))
    rows.sort(key=lambda r: _natural(r[0][0]))

    with (OUT / "bom.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh, quoting=csv.QUOTE_ALL)
        writer.writerow(["Refs", "Value", "Footprint", "Qty"])
        for refs, value, footprint in rows:
            writer.writerow([",".join(refs), value, footprint, len(refs)])
    print(f"      {len(rows)} BOM lines, {sum(len(r[0]) for r in rows)} parts")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    build_netlist()
    build_pcb()
    build_fabrication()
    build_3d()
    build_drawings()
    build_bom()
    print("\nDone. Board artifacts in out/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
