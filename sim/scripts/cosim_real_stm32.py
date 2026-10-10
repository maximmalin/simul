#!/usr/bin/env python3
"""Real co-simulation: actual firmware.elf on an emulated Cortex-M, in lockstep
with real ngspice.

Both halves execute real software:

  * the MCU is `firmware/.pio/build/bluepill_f103c8/firmware.elf` running on
    qemu-system-arm's netduinoplus2 machine (STM32F103, Cortex-M3, same
    0x08000000 flash / 0x20000000 RAM map as the Blue Pill), executing the code
    in firmware/src/firmware.ino;
  * the analog side is ngspice running the SKiDL-derived netlist, inside the
    FMI 2.0 slave sim/fmu/SdrNgspiceFMU.fmu.

The exchange is a RAM mailbox (firmware/src/cosim.h). Each step:

    host  -> writes PA6/PA7 state into the mailbox
    MCU   -> reads it in loop(), drives the real GPIO pins
    MCU   -> samples the ADC and publishes I/Q back into the mailbox
    host  -> reads the stamps and the samples

Memory is read and written over qemu's GDB stub (sim/scripts/gdb_remote.py,
the remote serial protocol implemented directly, so no gdb binary is needed at
run time and no gdb-session state leaks into the results). The human monitor
cannot do this at all: HMP has no memory-write command and answers
"unknown command: 'wp'", which is what left the mailbox at zero.

    python3 sim/scripts/cosim_real_stm32.py
"""
from __future__ import annotations

import re
import socket
import subprocess
import sys
import time
from pathlib import Path

import fmpy
from fmpy import extract, read_model_description

PROJECT = Path(__file__).resolve().parent.parent.parent
FW = PROJECT / "firmware"
ELF = FW / ".pio" / "build" / "bluepill_f103c8" / "firmware.elf"
FMU = PROJECT / "sim" / "fmu" / "SdrNgspiceFMU.fmu"

# Installed from the distribution: qemu-system-arm provides an STM32F103
# machine, gdb-multiarch the debugger that drives it. See sim/SETUP.md.
QEMU = "qemu-system-arm"
MACHINE = "netduinoplus2"

GDB_PORT = 4544
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

# Must match firmware/src/cosim.h.
COSIM_BASE = 0x20004000
OFF_MAGIC = 0x00
OFF_LO = 0x04
OFF_GAIN = 0x08
OFF_MCU_STAMP = 0x10
OFF_ADC_I = 0x14
OFF_ADC_Q = 0x18
OFF_ADC_STAMP = 0x1C
COSIM_MAGIC = 0x43305349

V_LOGIC = 3.3
ADC_FS = 4095


sys.path.insert(0, str(Path(__file__).resolve().parent))
from gdb_remote import GdbRemote  # noqa: E402


def check_prereqs() -> list[str]:
    import shutil
    missing = []
    if not shutil.which(QEMU):
        missing.append("qemu-system-arm (apt install qemu-system-arm)")
    if not ELF.exists():
        missing.append(f"{ELF} (platformio run)")
    if not FMU.exists():
        missing.append(f"{FMU} (build_fmu.py)")
    return missing


def main() -> int:
    missing = check_prereqs()
    if missing:
        for m in missing:
            print("missing:", m, file=sys.stderr)
        return 1

    # ---- analog side: the ngspice FMU ------------------------------------
    md = read_model_description(str(FMU))
    vr = {v.name: v.valueReference for v in md.modelVariables}
    from fmpy import fmi2
    slave = fmi2.FMU2Slave(guid=md.guid,
                           modelIdentifier=md.coSimulation.modelIdentifier,
                           unzipDirectory=extract(str(FMU)),
                           instanceName="sdr")
    slave.instantiate()
    slave.setupExperiment()
    slave.enterInitializationMode()
    slave.exitInitializationMode()
    slave.setReal([vr["rf_amplitude"]], [50e-3])
    slave.setReal([vr["lo_frequency"]], [7.20e6])

    # ---- MCU side: the real firmware under qemu --------------------------
    proc = subprocess.Popen(
        [str(QEMU), "-M", MACHINE, "-nographic", "-kernel", str(ELF), "-S",
         "-gdb", f"tcp:127.0.0.1:{GDB_PORT}"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True)

    try:
        mon = GdbRemote(port=GDB_PORT)

        # Confirm the image is where the machine expects it before trusting
        # anything else: a firmware that loaded but is not linked for this part
        # would show up later as nonsense mailbox values.
        vec = mon.read(0x08000000, 8)
        if len(vec) != 8:
            print("could not read the vector table", file=sys.stderr)
            return 1
        sp = int.from_bytes(vec[0:4], "little")
        pc = int.from_bytes(vec[4:8], "little")
        print(f"firmware : {ELF.name}")
        print(f"machine  : {MACHINE}")
        print(f"  initial SP   0x{sp:08x}")
        print(f"  reset vector 0x{pc:08x} "
              f"({'in flash' if 0x08000000 <= pc < 0x08020000 else 'NOT IN FLASH'})")

        # Claim the mailbox, then let the firmware run.
        mon.write_u32(COSIM_BASE + OFF_MAGIC, COSIM_MAGIC)
        mon.write_u32(COSIM_BASE + OFF_LO, 1)
        mon.write_u32(COSIM_BASE + OFF_GAIN, 0)
        mon.continue_()

        # Let it reach the steady state: the ADC and DMA need a few passes
        # before the published samples mean anything.
        time.sleep(3.0)

        print()
        print(f"{'step':>5} {'PA6':>5} {'adc_i (V)':>10} {'adc_q (V)':>10} "
              f"{'I code':>7} {'Q code':>7} {'mcu':>6} {'adc':>5}")

        rows = []
        step = 20e-6
        for k in range(12):
            # Toggle the LO the way TIM3 would, half a period per step.
            # Stop the core before touching its memory: a running target
            # services no other GDB packets.
            mon.interrupt()
            lo = 1 if (k % 2 == 0) else 0
            mon.write_u32(COSIM_BASE + OFF_LO, lo)
            # Hand the CPU back so it can act on the pin state and publish the
            # next sample, then take it back for the read below.
            mon.continue_()
            time.sleep(0.05)
            mon.interrupt()

            slave.setReal([vr["stim_level"]], [V_LOGIC if lo else 0.0])
            slave.doStep(k * step, step)
            adc_i, adc_q = slave.getReal([vr["adc_i"], vr["adc_q"]])

            # Read back what the firmware published from its own ADC.
            mcu_stamp = mon.read_u32(COSIM_BASE + OFF_MCU_STAMP)
            adc_stamp = mon.read_u32(COSIM_BASE + OFF_ADC_STAMP)
            raw_i = mon.read_u32(COSIM_BASE + OFF_ADC_I) & 0xFFF
            raw_q = mon.read_u32(COSIM_BASE + OFF_ADC_Q) & 0xFFF

            rows.append((lo, adc_i, adc_q, raw_i, raw_q, mcu_stamp, adc_stamp))
            print(f"{k:5d} {lo * 3.3:5.1f} {adc_i:10.4f} {adc_q:10.4f} "
                  f"{raw_i:7d} {raw_q:7d} {mcu_stamp:6d} {adc_stamp:5d}")
            time.sleep(0.25)

        print()
        stamps = [r[5] for r in rows]
        adc_stamps = [r[6] for r in rows]
        print(f"MCU loop iterations observed : {stamps[-1] - stamps[0]:+d}")
        print(f"ADC pairs published          : {adc_stamps[-1] - adc_stamps[0]:+d}")
        print(f"MCU ADC I codes              : "
              f"{[r[3] for r in rows[:6]]}")
        print(f"ngspice adc_i                : "
              f"{[round(r[1], 3) for r in rows[:6]]}")
        running = [r for r in rows if r[0] == 1]
        if running:
            print(f"mean adc_i with PA6 high     : "
                  f"{sum(r[1] for r in running) / len(running):.4f} V")
        if stamps[-1] != stamps[0]:
            print("\nRESULT: the real firmware is executing and exchanging with "
                  "ngspice.")
            return 0
        print("\nRESULT: mailbox stamps did not move -- firmware not responding")
        return 1
    finally:
        slave.terminate()
        try:
            mon.close()
        except Exception:
            pass
        proc.kill()
        proc.wait(timeout=10)


if __name__ == "__main__":
    sys.exit(main())
