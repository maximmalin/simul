#!/usr/bin/env python3
"""Regression tests for the SKiDL -> ngspice SDR export.

Covers the defects that produced silently-wrong output rather than loud
failures. Run:

    source /home/mirrage/.venv/bin/activate
    python3 sim/scripts/test_sdr_netlist.py
"""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent.parent
sys.path.insert(0, str(HERE))

NETLIST = PROJECT / "sim" / "netlists" / "sdr_skidl_ngspice.cir"

NUM = r"[-+]?[0-9]*\.?[0-9]+(?:[eE][-+]?[0-9]+)?"


def run(text: str, tag: str = "t") -> tuple[int, str]:
    with tempfile.TemporaryDirectory() as td:
        cir = Path(td) / f"{tag}.cir"
        cir.write_text(text)
        proc = subprocess.run(["ngspice", "-b", str(cir)],
                              capture_output=True, text=True, timeout=180)
        return proc.returncode, proc.stdout + proc.stderr


def widest_row(out: str) -> list[float] | None:
    """Return the widest purely-numeric row in ngspice output."""
    best: list[float] = []
    for line in out.splitlines():
        nums: list[float] = []
        for tok in line.split():
            try:
                nums.append(float(tok))
            except ValueError:
                nums = []
                break
        if len(nums) >= len(best):
            best = nums
    return best or None


def column(out: str, idx: int) -> list[float]:
    """Every value of one column of ngspice's transient print table."""
    vals: list[float] = []
    for line in out.splitlines():
        nums: list[float] = []
        for tok in line.split():
            try:
                nums.append(float(tok))
            except ValueError:
                nums = []
                break
        if len(nums) > idx:
            vals.append(nums[idx])
    return vals


def excursion(out: str, idx: int) -> float:
    """Peak-to-peak of a column over the settled part of the run.

    Peak-to-peak rather than peak, because the baseband buffer is biased to
    mid-rail: with the chain quiet the node still sits at 1.65 V, so an absolute
    peak says nothing about whether signal is passing.
    """
    vals = column(out, idx)
    if len(vals) < 10:
        return 0.0
    tail = vals[len(vals) // 2:]
    return max(tail) - min(tail)


class Checks:
    def __init__(self) -> None:
        self.failed: list[str] = []

    def check(self, name: str, ok: bool, detail: str = "") -> None:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}"
              + (f"  -- {detail}" if detail and not ok else ""))
        if not ok:
            self.failed.append(name)


def main() -> int:
    c = Checks()

    if not NETLIST.exists():
        print(f"missing {NETLIST}; run sim/scripts/export_ngspice.py first",
              file=sys.stderr)
        return 1
    text = NETLIST.read_text()
    lines = text.splitlines()

    print("\nnetlist structure")
    # str.join() over an already-joined string splits it per character. That
    # produced a file of one-letter lines that still "looked" plausible.
    longest = max(len(l) for l in lines)
    c.check("no character-per-line corruption", longest > 40,
            f"longest line is {longest} chars")

    c.check("ngspice node names do not start with '+'",
            not any(re.match(r"^[RCLEVB]\S*\s+\+", l) for l in lines))

    # USB_D+ and USB_D- must not collapse onto one node, which would short the
    # differential pair.
    usb = re.findall(r"^R\S+\s+(\S+)\s+(\S+)", "\n".join(lines), re.M)
    usb_nodes = {n for pair in usb for n in pair if n.upper().startswith("USB_")}
    c.check("USB_D+ and USB_D- stay distinct", len(usb_nodes) >= 4,
            f"found {sorted(usb_nodes)}")

    # Ambiguous SI values: ngspice reads the trailing digit of "4u7" as part of
    # the mantissa's unit handling and reports "can't find model".
    c.check("no ambiguous SI values like 4u7",
            not re.search(r"^[CL]\S*\s+\S+\s+\S+\s+\d+[a-zA-Z]\d+\s*$",
                          "\n".join(lines), re.M))

    print("\nimpedance-matched pin boundary")
    # Only the analog->digital direction uses a native XSPICE bridge. The
    # digital->analog direction cannot: a dac_bridge input is an event-driven
    # digital node, and `alter LO_SRC = 3.3` fails with "no such device or
    # model name". The pin is modelled explicitly instead.
    c.check("adc_bridge used for analog->digital",
            ".model m_adc adc_bridge" in text)
    c.check("bridge port vectors are bracketed",
            all(re.search(r"^A_\w+\s+\[\S+\]\s+\[\S+\]", l)
                for l in lines if l.startswith("A_")))
    c.check("no unusable dac_bridge", ".model m_dac" not in text,
            "a dac_bridge cannot be driven by the co-simulation host")
    c.check("MCU output pin has a host-alterable source",
            re.search(r"^V_MCU_\w+\s+\S+\s+0\s+DC\s+0", text, re.M) is not None)
    c.check("pin output impedance modelled",
            re.search(r"^R_PIN_\w+\s+\S+\s+\S+\s+\d+", text, re.M) is not None)
    c.check("pin load capacitance modelled",
            re.search(r"^C_PIN_\w+\s+\S+\s+\S+\s+[\d.]+p", text, re.M) is not None)

    print("\nbehavioural macromodels")
    # Both LNAs are transconductances with the gate's DC bias referenced out.
    # An ideal VCVS gain here is not a stage model: it amplified the 1.65 V gate
    # bias as signal and put ~20 V on the drain.
    c.check("common-gate LNA stages are transconductances",
            len(re.findall(r"^G_Q_LNA\d\s+\S+\s+0\s+VALUE\s*=.*V\(LNA\d_G\)",
                           text, re.M)) == 2)
    c.check("LNA gate bias is referenced out, not amplified",
            len(re.findall(r"^G_Q_LNA\d.*-\s*[\d.]+\)", text, re.M)) == 2)
    c.check("quarter-wave coupler modelled", "90-degree hybrid coupler" in text)
    c.check("coupler quadrature term is 1j, not bare j",
            "1j" in text and not re.search(r"V\(.*\)\s*\*\s*j\b", text))
    c.check("baseband clip is tanh, not single-arg limit",
            "tanh(" in text and not re.search(r"limit\(\s*[^,)]*\s*\)", text))
    c.check("LNA drain bias present", "drain bias load" in text)

    print("\nngspice run")
    code, out = run(text, "sdr")
    c.check("exits cleanly", code == 0, f"rc={code}")
    problems = sorted({ln.strip() for ln in out.splitlines()
                       if re.search(r"singular|fatal error|Undefined parameter",
                                    ln, re.I)})
    c.check("no singular matrix / fatal error", not problems,
            "; ".join(problems[:3]))

    row = widest_row(out)
    c.check("transient table produced", row is not None and len(row) >= 4,
            "no numeric row with >=4 columns")
    if row and len(row) >= 4:
        c.check("stop time reached", row[1] > 0, f"t_stop={row[1]:.3e}")

    print("\nsignal path reaches the ADC")
    # The deck ships with the LO running, so the standing run already proves the
    # chain carries signal. What still has to hold is that the pin is host
    # drivable, which now means driving it *low* and watching the chain go
    # quiet -- the same alter the co-simulation driver issues.
    muted = re.sub(r"^(V_MCU_LO\s+\S+\s+0\s+DC)\s*[\d.]+\s*$", r"\g<1> 0",
                   text, flags=re.M)
    changed = muted != text
    c.check("LO pin is host-drivable for the test", changed)
    if changed:
        code2, out2 = run(muted, "sdr_lo")
        quiet = excursion(out2, 2)          # v(ADC_I)
        loud = excursion(out, 2)             # the standing run, LO running
        c.check("ADC output goes quiet with the LO muted",
                quiet < 0.01 * loud,
                f"excursion {quiet:.3e} V muted vs {loud:.3e} V with LO running")

    print()
    if c.failed:
        print(f"{len(c.failed)} check(s) FAILED: {', '.join(c.failed)}")
        return 1
    print("all netlist regression checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())