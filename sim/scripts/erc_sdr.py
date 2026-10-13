#!/usr/bin/env python3
"""Electrical Rules Check (ERC) for the Krystal Radio zero-IF SDR.

Builds the same SKiDL circuit that produces the netlist and runs SKiDL's
ERC engine over it. Violations are written to a report and printed as a
summary; the process exits non-zero when ERC reports any *error* (warnings
do not fail the build).

    .venv/bin/python sim/scripts/erc_sdr.py --report out/erc.rpt

The KiCad schematic that tscircuit exports (hardware/tscircuit/out/
board.kicad_sch) is auto-generated from the board and is not a hand-drawn
schematic, so its KiCad ERC is informational only. For this code-defined
netlist the authoritative check is the SKiDL ERC run here.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent.parent
sys.path.insert(0, str(HERE))

import sdr_skidl_circuit  # noqa: E402


class _Collector(logging.Handler):
    """Collect SKiDL ERC records, split by severity."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        msg = record.getMessage()
        if record.levelno >= logging.ERROR:
            self.errors.append(msg)
        elif record.levelno >= logging.WARNING:
            self.warnings.append(msg)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--report", default=str(PROJECT / "out" / "erc.rpt"),
                    help="where to write the report (default out/erc.rpt)")
    ap.add_argument("--strict", action="store_true",
                    help="also return non-zero when warnings are present")
    args = ap.parse_args()

    collector = _Collector()
    erc_logger = logging.getLogger("ERC_Logger")
    erc_logger.addHandler(collector)
    try:
        circuit = sdr_skidl_circuit.build_circuit()
        circuit.ERC()
    finally:
        erc_logger.removeHandler(collector)

    body = [
        "Krystal Radio — SKiDL ERC report",
        "source: sim/scripts/sdr_skidl_circuit.py",
        f"parts: {len(circuit.parts)}  nets: {len(circuit.nets)}",
        "",
        f"errors:   {len(collector.errors)}",
        f"warnings: {len(collector.warnings)}",
    ]
    if collector.errors:
        body += ["", "ERRORS:", *[f"  - {m}" for m in collector.errors]]
    if collector.warnings:
        body += ["", "WARNINGS:", *[f"  - {m}" for m in collector.warnings]]

    report = Path(args.report)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("\n".join(body) + "\n", encoding="utf-8")

    print("\n".join(body[:6]))
    print(f"report: {report}")

    failed = bool(collector.errors) or (args.strict and bool(collector.warnings))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
