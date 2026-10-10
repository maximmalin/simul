#!/usr/bin/env python3
"""PicSimLab <-> ngspice co-simulation for the SDR direct-conversion receiver.

The division of labour:

    SKiDL          connectivity only; emits sim/netlists/sdr_skidl_ngspice.cir
    ngspice        the analog receiver chain (LNA, coupler, mixer, buffers)
    PicSimLab      the STM32 firmware -- NOT simulated here

The analog/digital boundary is ngspice's own XSPICE node bridges, which the
netlist already contains:

    A_DAC_LO [LO_SRC] [LO]      m_dac    digital pin -> analog mixer LO
    A_ADC_I  [ADC_I]  [ADC_I_D] m_adc    analog -> digital, for the firmware

So this script's job is narrow and specific: read the MCU pin states from
PicSimLab, write them onto the *digital* nodes the dac_bridge watches, run one
ngspice transient step, and read the analog nodes back so the firmware can
sample them. It must not try to drive analog nodes directly -- the bridge owns
the conversion, including the edge rate and the pin loading.

Prerequisites:
    1. ngspice on PATH
    2. PicSimLab running with Tools -> Remote Control on port 5000
    3. STM32 Blue Pill loaded with the SDR firmware, simulation started

Run:
    python3 sim/scripts/sdr_picsimlab_cosim.py --steps 500 --tstep 5e-6
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT / "src"))

from picsimlab_fmu import PicSimLabFMU, PinType  # noqa: E402

# Firmware pin -> the ngspice source that models it.
#
# Digital -> analog does NOT use a dac_bridge. Its input is an event-driven
# digital node and an external host cannot drive it: `alter LO_SRC = 3.3` fails
# with "no such device or model name". Each pin is instead an alterable source
# sitting behind the pin's real output impedance (R_PIN_LO / C_PIN_LO in the
# deck), which is what makes the boundary impedance matched rather than ideal.
LO_PIN, LO_SOURCE = "PA6", "V_MCU_LO"
GAIN_PIN, GAIN_SOURCE = "PA7", "V_MCU_GAIN"

# Analog -> digital does use adc_bridge, which thresholds properly.
ADC_I_DIGITAL = "ADC_I_D"      # PA0, ADC1_IN0
ADC_Q_DIGITAL = "ADC_Q_D"      # PA1, ADC1_IN1
ADC_I_ANALOG = "ADC_I"
ADC_Q_ANALOG = "ADC_Q"

PIN_SOURCES = {LO_PIN: LO_SOURCE, GAIN_PIN: GAIN_SOURCE}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--netlist", default=None,
                    help="ngspice deck (default: the SKiDL-exported one)")
    ap.add_argument("--port", type=int, default=5000, help="rcontrol port")
    ap.add_argument("--steps", type=int, default=200)
    ap.add_argument("--tstep", type=float, default=5e-6,
                    help="ngspice transient step, seconds")
    ap.add_argument("--firmware", default=None,
                    help="hex to load into PicSimLab before starting")
    args = ap.parse_args()

    netlist = Path(args.netlist) if args.netlist else \
        PROJECT / "sim" / "netlists" / "sdr_skidl_ngspice.cir"
    if not netlist.exists():
        print(f"missing netlist {netlist}\n"
              f"run: source /home/mirrage/.venv/bin/activate\n"
              f"     python3 sim/scripts/export_ngspice.py", file=sys.stderr)
        return 1

    text = netlist.read_text()
    # Check what the deck actually has. The driven pins are host-alterable
    # sources, so they start a line; the adc_bridge outputs only ever appear as
    # bracketed port vectors (ngspice bug #146 requires the brackets despite
    # the manual showing them unbracketed). Checking for digital names on the
    # driven side is what made this report "no digital node 'GAIN_SRC'" -- a
    # node a correctly built deck never has, because a dac_bridge input cannot
    # be driven from a host.
    missing = [s for s in PIN_SOURCES.values()
               if not re.search(rf"^\s*{re.escape(s)}\b", text, re.M)]
    missing += [d for d in (ADC_I_DIGITAL, ADC_Q_DIGITAL)
                if f"[{d}]" not in text]
    if missing:
        print(f"netlist is missing {', '.join(missing)}; the deck and this "
              f"script disagree -- regenerate with export_ngspice.py",
              file=sys.stderr)
        return 1

    print("=== SDR receiver co-simulation ===")
    print(f"netlist : {netlist}")
    print(f"pins    : " + ", ".join(f"{p} -> {s}" for p, s in PIN_SOURCES.items()))
    print(f"bridge  : {ADC_I_ANALOG}/{ADC_Q_ANALOG} "
          f"-> {ADC_I_DIGITAL}/{ADC_Q_DIGITAL}")
    print(f"steps   : {args.steps} x {args.tstep:g}s\n")

    fmu = PicSimLabFMU(board="stm32_bluepill", host="127.0.0.1", port=args.port)
    try:
        fmu.connect()
    except Exception as exc:
        print(f"cannot reach PicSimLab: {exc}\n"
              f"Start it, then Tools -> Remote Control (port {args.port}).",
              file=sys.stderr)
        return 1
    print("connected to PicSimLab")

    if args.firmware:
        print(f"loading firmware {args.firmware}")
        print(fmu.cmd_loadhex(args.firmware))

    print("allocating pins")
    fmu.request_pins([
        (PinType.DIGITAL, "LO"),
        (PinType.DIGITAL, "GAIN"),
        (PinType.ANALOG, "ADC_I"),
        (PinType.ANALOG, "ADC_Q"),
    ])

    print("starting MCU simulation")
    fmu.cmd_sim_start()

    # Import the bridge late so a missing ngspice still reports cleanly.
    from picsimlab_fmu import NgspiceBridge

    try:
        with NgspiceBridge() as ng:
            ng.load_netlist(str(netlist))
            print("ngspice netlist loaded\n")

            def on_step(i, n, r):
                if (i + 1) % 50:
                    return
                lo = r["pin_voltages"].get("PA6", 0.0)
                print(f"  step {i + 1:4d}/{n}  PA6(LO)={lo:.2f}V  "
                      f"elapsed={r.get('elapsed_seconds', 0):.3f}s")

            results = fmu.run(ngspice_bridge=ng, tstep=args.tstep,
                              steps=args.steps, progress_cb=on_step)
    except Exception as exc:
        print(f"co-simulation failed: {exc}", file=sys.stderr)
        fmu.cmd_sim_stop()
        fmu.disconnect()
        return 1

    total = sum(r.get("elapsed_seconds", 0.0) for r in results)
    print(f"\ncompleted {len(results)} steps in {total:.2f}s "
          f"({total / max(len(results), 1) * 1e3:.2f} ms/step)")

    fmu.cmd_sim_stop()
    fmu.disconnect()
    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())