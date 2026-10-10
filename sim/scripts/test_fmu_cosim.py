#!/usr/bin/env python3
"""Run the SDR ngspice FMU through fmpy's FMI 2.0 master and check the pin boundary.

This is the co-simulation that does not need PicSimLab. The slave is a real FMI
2.0 co-simulation FMU wrapping ngspice, driving the actual SKiDL-derived
netlist (build_fmu.py embeds sim/netlists/sdr_skidl_ngspice.cir at build time,
so the two cannot drift). The MCU pin state crosses the boundary as an FMI
input, and the baseband comes back as an FMI output.

The check that matters is the contrast, not the absolute value:

    stim_level = 0  ->  baseband sits at mid-rail, 1.65 V, no signal
    stim_level = 1  ->  baseband swings to the rails, the mixer is converting

1.65 V is exactly BB_VMID: with the LO stopped the mixer output is zero, the
soft-clipping buffer evaluates tanh(0), and the node rests at mid-rail. If the
PA6 pin were not actually reaching the deck, both cases would read the same.

    python3 sim/scripts/test_fmu_cosim.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import fmpy

PROJECT = Path(__file__).resolve().parent.parent.parent
FMU = PROJECT / "sim" / "fmu" / "SdrNgspiceFMU.fmu"

V_LOGIC = 3.3
MID_RAIL = 1.65


def run(stim: float) -> tuple[float, float, str]:
    """Run the FMU with PA6 at `stim` and return (peak adc_i, peak adc_q, msg).

    simulate_fmu returns a structured array with named fields (time, adc_i,
    adc_q, model_status). Reading it positionally picks up `time` as if it were
    adc_i, which made a working FMU look like it had a dead I channel.
    """
    res = fmpy.simulate_fmu(
        str(FMU),
        stop_time=1.0,
        output_interval=1.0,
        fmi_type="CoSimulation",
        start_values={"rf_amplitude": 50e-3, "lo_frequency": 7.20e6,
                      "stim_level": stim},
    )
    names = res.dtype.names or ()
    if "adc_i" not in names or "adc_q" not in names:
        return 0.0, 0.0, f"outputs present: {names}"
    # Peaks across every reported sample, not just the last one.
    return float(res["adc_i"].max()), float(res["adc_q"].max()), "ok"


def main() -> int:
    if not FMU.exists():
        print(f"missing {FMU}\nbuild it first:\n"
              f"    python3 sim/scripts/build_fmu.py "
              f"<python-with-pythonfmu>", file=sys.stderr)
        return 1

    md = fmpy.read_model_description(str(FMU))
    print(f"FMU            : {FMU.name}")
    print(f"modelIdentifier: {md.coSimulation.modelIdentifier}")
    print("variables      :", ", ".join(v.name for v in md.modelVariables))
    print()

    off_i, off_q, _ = run(0.0)
    on_i, on_q, _ = run(1.0)

    print(f"PA6 low  -> adc_i peak {off_i:7.4f} V   adc_q peak {off_q:7.4f} V")
    print(f"PA6 high -> adc_i peak {on_i:7.4f} V   adc_q peak {on_q:7.4f} V")
    print()

    failed = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}"
              + (f"   ({detail})" if detail else ""))
        if not ok:
            failed.append(name)

    check("LO off leaves both channels at mid-rail",
          abs(off_i - MID_RAIL) < 0.02 and abs(off_q - MID_RAIL) < 0.02,
          f"I={off_i:.4f} Q={off_q:.4f} V vs {MID_RAIL} V")
    check("PA6 high makes the mixer convert",
          max(on_i, on_q) > MID_RAIL + 0.5,
          f"peak {max(on_i, on_q):.4f} V")
    check("both channels are driven",
          on_i > MID_RAIL + 0.1 and on_q > MID_RAIL + 0.1,
          f"I {on_i:.4f} V, Q {on_q:.4f} V")
    check("I and Q are comparable, as a quadrature pair must be",
          0.5 < on_i / on_q < 2.0,
          f"I/Q = {on_i / on_q:.3f}")
    check("nothing exceeds the analog rail",
          max(on_i, on_q) <= V_LOGIC + 1e-3,
          f"max {max(on_i, on_q):.4f} V vs {V_LOGIC} V")

    print()
    if failed:
        print(f"{len(failed)} check(s) FAILED: {', '.join(failed)}")
        return 1
    print("FMU co-simulation verified: the MCU pin reaches ngspice and the "
          "baseband comes back")
    return 0


if __name__ == "__main__":
    sys.exit(main())
