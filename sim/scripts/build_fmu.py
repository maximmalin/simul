#!/usr/bin/env python3
"""Build SdrNgspiceFMU.fmu with the current netlist baked in.

The FMU used to carry a hand-written netlist that had drifted from the real
one -- it still had the Q channel scaled by 0.7 instead of shifted in phase, a
static LO phasor instead of a timer waveform, and no mid-rail bias on the ADC
input, i.e. every defect that was fixed in export_ngspice.py. Anything embedded
by hand will drift again.

So this regenerates the netlist from the SKiDL source of truth, splices it into
the FMU source, and builds. The FMU and sim/netlists/sdr_skidl_ngspice.cir are
then the same circuit by construction.

    python3 sim/scripts/build_fmu.py
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent.parent
SCRIPTS = PROJECT / "sim" / "scripts"
FMU_SRC = SCRIPTS / "sdr_ngspice_fmu.py"
NETLIST = PROJECT / "sim" / "netlists" / "sdr_skidl_ngspice.cir"
OUT_DIR = PROJECT / "sim" / "fmu"

# The deck drives its own simulation and writes sdr_trace.dat from a .control
# block. Inside an FMU that is pure overhead on every run -- the .control block
# also prints a table, which is what the old 80-column truncation bug came from.
STRIP_CONTROL = re.compile(r"^\.control$.*?^\.endc$\n?", re.M | re.S)


def export_netlist(python: str) -> None:
    print("1/4  exporting netlist from SKiDL")
    r = subprocess.run([python, str(SCRIPTS / "export_ngspice.py")],
                       capture_output=True, text=True)
    tail = [ln for ln in r.stdout.splitlines()
            if ln and not ln.startswith("WARNING")]
    for ln in tail:
        print("    " + ln)
    if r.returncode != 0:
        sys.exit("netlist export failed:\n" + r.stderr[-800:])


def splice(python: str) -> None:
    print("2/4  embedding netlist into the FMU source")
    deck = NETLIST.read_text()
    deck = STRIP_CONTROL.sub("", deck)
    if not deck.strip():
        sys.exit("netlist came out empty after stripping .control")

    src = FMU_SRC.read_text()
    start = src.index('EMBEDDED_NETLIST = """')
    end = src.index('"""', start + len('EMBEDDED_NETLIST = """')) + 3
    body = deck.replace("\\", "\\\\")
    if '"""' in body:
        body = body.replace('"""', '\\"\\"\\"')
    new = src[:start] + f'EMBEDDED_NETLIST = """{body}"""' + src[end:]
    FMU_SRC.write_text(new)
    print(f"    embedded {len(deck)} bytes of deck "
          f"(source now {len(new)} bytes)")
    print(f"    python can parse it: {python} -c compile check", end=" ")
    r = subprocess.run([python, "-c",
                        f"compile(open({str(FMU_SRC)!r}).read(), 'x', 'exec')"],
                       capture_output=True, text=True)
    print("ok" if r.returncode == 0 else "FAILED\n" + r.stderr[-600:])
    if r.returncode != 0:
        sys.exit(1)


def build(python: str) -> None:
    print("3/4  building FMU")
    if OUT_DIR.exists():
        for f in OUT_DIR.glob("*.fmu"):
            f.unlink()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    r = subprocess.run([python, "-m", "pythonfmu", "build",
                        "-f", str(FMU_SRC), "-d", str(OUT_DIR)],
                       capture_output=True, text=True)
    print("    " + (r.stdout.strip().splitlines() or ["(no output)"])[-1])
    if r.returncode != 0:
        print(r.stdout[-1500:])
        print(r.stderr[-1500:])
        sys.exit("pythonfmu build failed")
    for f in OUT_DIR.glob("*.fmu"):
        print(f"    {f.name}  {f.stat().st_size} bytes")


def main() -> int:
    build_python = sys.argv[1] if len(sys.argv) > 1 else sys.executable
    # SKiDL and pythonfmu live in different environments here: the netlist
    # exporter needs skidl, the FMU builder needs pythonfmu. Ask the build
    # interpreter first, then fall back to the project venv.
    export_python = build_python
    if subprocess.run([build_python, "-c", "import skidl"],
                      capture_output=True).returncode != 0:
        cand = PROJECT / ".venv" / "bin" / "python"
        if not cand.exists():
            sys.exit(f"no interpreter with skidl found; tried {build_python} "
                     f"and {cand}")
        export_python = str(cand)
    if not shutil.which("ngspice"):
        print("ngspice not on PATH", file=sys.stderr)
        return 1
    print(f"export with {export_python}")
    print(f"build   with {build_python}")
    export_netlist(export_python)
    splice(export_python)
    build(build_python)
    print("4/4  done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
