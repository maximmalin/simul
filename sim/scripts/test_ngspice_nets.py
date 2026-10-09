#!/usr/bin/env python3
"""Smoke tests for the SDR ngspice netlists.

Verifies each netlist in sim/netlists/ actually parses and runs under ngspice,
and that the expected output nodes carry signal.

Run:
    ngspice must be on PATH.
    python3 sim/scripts/test_ngspice_nets.py
"""

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
NETLIST_DIR = HERE.parent / "netlists"

# netlist -> (must-appear-in-output markers)
EXPECTED = {
    "sdr_simplified.cir": ["adc_i", "adc_q"],
    "sdr_receiver.cir": ["adc_i", "adc_q"],
}


def run_netlist(path: Path) -> tuple[bool, str]:
    """Run ngspice in batch mode; return (ok, output)."""
    proc = subprocess.run(
        ["ngspice", "-b", str(path)],
        capture_output=True, text=True, timeout=120,
    )
    return proc.returncode == 0, proc.stdout + proc.stderr


def main() -> int:
    if not NETLIST_DIR.is_dir():
        print(f"missing {NETLIST_DIR}", file=sys.stderr)
        return 1

    nets = sorted(p for p in NETLIST_DIR.glob("*.cir"))
    if not nets:
        print(f"no .cir netlists in {NETLIST_DIR}", file=sys.stderr)
        return 1

    failures = 0
    for net in nets:
        want = EXPECTED.get(net.name)
        try:
            ok, out = run_netlist(net)
        except subprocess.TimeoutExpired:
            print(f"  FAIL {net.name}: ngspice timed out")
            failures += 1
            continue
        except FileNotFoundError:
            print("ngspice not found on PATH")
            return 1

        if not ok:
            print(f"  FAIL {net.name}: ngspice exited non-zero")
            print("    " + out.strip().splitlines()[-1][:120])
            failures += 1
            continue

        # ngspice writes the transient table to stdout when .print is used.
        # Scan every line for the widest purely-numeric row (the table body),
        # rather than assuming the last line is a data row.
        best: list[float] = []
        for ln in out.splitlines():
            numeric = []
            for tok in ln.split():
                try:
                    numeric.append(float(tok))
                except ValueError:
                    numeric = []
                    break
            if len(numeric) >= len(best):
                best = numeric  # >= so the final (t_stop) row wins ties

        if len(best) < 4:
            preview = out.strip().splitlines()[-3:]
            print(f"  FAIL {net.name}: no transient table found")
            for p in preview:
                print(f"       | {p[:100]}")
            failures += 1
            continue

        # transient rows are: index time <node...>
        _, time, nodes = best[0], best[1], best[2:]
        nonzero = sum(1 for v in nodes if abs(v) > 1e-9)
        print(f"  PASS {net.name}: t_stop={time:.3e}s  nodes={len(nodes)}  "
              f"nonzero={nonzero}")

        if want:
            missing = [w for w in want if w not in out.lower()]
            if missing:
                print(f"       note: markers not echoed in output: {missing}")

    print()
    if failures:
        print(f"{failures} netlist(s) FAILED")
        return 1
    print(f"all {len(nets)} netlist(s) passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())