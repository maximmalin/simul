#!/usr/bin/env python3
"""Build firmware.elf with COSIM_MODE for the co-simulation.

The co-simulation build differs from the shipped one in exactly one way: USB
CDC is compiled out. Serial.begin() blocks until a USB host enumerates, and
qemu-system-arm's netduinoplus2 has no USB device model, so setup() never
returns and loop() never runs. Everything else -- the ADC, the DMA, TIM3, and
the mailbox -- is the same code.

Keeping the two builds explicit is deliberate. Overriding build_flags in
platformio.ini would make the USB-less image the one that gets flashed, which is
not what anyone wants on a bench.

    python3 sim/scripts/build_cosim_firmware.py
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent.parent
FW = PROJECT / "firmware"
ELF = FW / ".pio" / "build" / "bluepill_f103c8" / "firmware.elf"


def main() -> int:
    """Build the cosim environment.

    platformio.ini carries a separate [env:cosim] with -D COSIM_MODE rather than
    the flag being forced here, so the USB-less image can never be produced by
    an ordinary `pio run` and flashed onto a bench by mistake.
    """
    print("building firmware for co-simulation (env:cosim, -D COSIM_MODE)")
    r = subprocess.run(["platformio", "run", "-e", "cosim"], cwd=str(FW),
                       capture_output=True, text=True, timeout=900)
    if "SUCCESS" not in r.stdout:
        print(r.stdout[-2500:])
        print(r.stderr[-800:], file=sys.stderr)
        return 1
    out = FW / ".pio" / "build" / "cosim" / "firmware.elf"
    if not out.exists():
        print(f"expected {out}", file=sys.stderr)
        return 1
    print(f"co-simulation firmware: {out} ({out.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
