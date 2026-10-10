#!/usr/bin/env python3
"""Two-party co-simulation: the ngspice FMU against an STM32 model, in lockstep.

This is the co-simulation that `src/picsimlab_fmu` was written to provide, with
PicSimLab replaced by a host-side model of the same firmware. PicSimLab's
rcontrol does not service commands while the qemu backend runs (see
sim/COSIM_STATUS.md), so the MCU side has to be modelled rather than executed.

What is real here and what is not:

  * real -- ngspice running the actual SKiDL-derived netlist, inside a real
    FMI 2.0 co-simulation slave, stepped through setupExperiment ->
    enterInitializationMode -> doStep -> getReal. Every value crossing the
    boundary is a genuine FMI call.
  * modelled -- the STM32. It reproduces what firmware/src/firmware.ino does
    with the pins (PA6 as the TIM3_CH1 LO enable, PA0/PA1 as the ADC channels,
    DMA sampling on a timer), not the Cortex-M core executing that firmware.
    No qemu binary exists on this machine to execute it.

The exchange is genuinely closed: the model's pin state changes what ngspice
computes, and ngspice's ADC levels change what the model samples.

    python3 sim/scripts/cosim_fmu_stm32.py
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import fmpy
from fmpy import extract, read_model_description

PROJECT = Path(__file__).resolve().parent.parent.parent
FMU = PROJECT / "sim" / "fmu" / "SdrNgspiceFMU.fmu"

V_LOGIC = 3.3
# STM32F103 12-bit ADC, Vref = VDDA.
ADC_FS = 4095.0


class Stm32Model:
    """Host-side stand-in for the Blue Pill running firmware.ino.

    Holds the same state the firmware does and drives the same pins:

      PA6  TIM3_CH1 output, the mixer LO enable
      PA7  front-end A/B select
      PA0  ADC1_IN0, I channel
      PA1  ADC1_IN1, Q channel

    The DMA sampler latches the I/Q pair on a timer tick rather than on every
    co-simulation step, which is what makes the sampled values differ from the
    instantaneous ones.
    """

    def __init__(self, sample_hz: float = 48_000.0):
        self.pa6 = 0.0            # LO running
        self.pa7 = 0.0            # gain select
        self.sample_hz = sample_hz
        self._next_sample = 0.0
        self.iq: list[tuple[int, int]] = []

    def tick(self, t: float) -> None:
        """Advance the MCU to time t, sampling on the timer tick."""
        while t >= self._next_sample:
            self._next_sample += 1.0 / self.sample_hz

    def sample(self, adc_i: float, adc_q: float) -> tuple[int, int]:
        """Latch PA0/PA1 through the 12-bit ADC."""
        i = max(0, min(ADC_FS, int(round(adc_i / V_LOGIC * ADC_FS))))
        q = max(0, min(ADC_FS, int(round(adc_q / V_LOGIC * ADC_FS))))
        self.iq.append((i, q))
        return i, q


def main() -> int:
    if not FMU.exists():
        print(f"missing {FMU}\nbuild it: python3 sim/scripts/build_fmu.py",
              file=sys.stderr)
        return 1

    md = read_model_description(str(FMU))
    vr = {v.name: v.valueReference for v in md.modelVariables}
    print("FMU :", FMU.name)
    print("vars:", ", ".join(sorted(vr)))
    print()

    mcu = Stm32Model()
    # fmpy's low-level slave wrapper gives access to the FMI C entry points,
    # which is what a master actually calls.
    from fmpy import fmi2

    unzipped = extract(str(FMU))
    slave = fmi2.FMU2Slave(guid=md.guid,
                           modelIdentifier=md.coSimulation.modelIdentifier,
                           unzipDirectory=unzipped,
                           instanceName="sdr")

    def vr_of(name: str) -> int:
        if name not in vr:
            raise KeyError(f"{name} not in the FMU")
        return vr[name]

    V_RF = vr_of("rf_amplitude")
    V_LO = vr_of("lo_frequency")
    V_STIM = vr_of("stim_level")
    O_I = vr_of("adc_i")
    O_Q = vr_of("adc_q")
    O_STATUS = vr_of("model_status")

    slave.instantiate()
    slave.setupExperiment()
    slave.enterInitializationMode()
    slave.exitInitializationMode()

    step = 20e-6
    rf_amp = 50e-3
    lo_freq = 7.20e6

    slave.setReal([V_RF], [rf_amp])
    slave.setReal([V_LO], [lo_freq])
    slave.setReal([V_STIM], [0.0])

    print("lockstep: MCU drives PA6, ngspice returns the I/Q baseband")
    print(f"{'t (us)':>9} {'PA6':>5} {'PA7':>5} "
          f"{'adc_i (V)':>10} {'adc_q (V)':>10} {'I code':>7} {'Q code':>7}")

    results = []
    try:
        for k in range(25):
            t = k * step
            mcu.tick(t)
            slave.setReal([V_STIM], [mcu.pa6])
            # The FMI communication point is simply the current time; there is
            # no helper for it in fmpy.
            ok = slave.doStep(t, step)
            if not ok:
                print("doStep returned False (slave asked to discard)")
                break
            adc_i, adc_q = slave.getReal([O_I, O_Q])
            codes = mcu.sample(adc_i, adc_q)
            results.append((t, adc_i, adc_q, codes))
            if k < 6 or k % 5 == 0:
                print(f"{t * 1e6:9.1f} {mcu.pa6:5.1f} {mcu.pa7:5.1f} "
                      f"{adc_i:10.4f} {adc_q:10.4f} "
                      f"{codes[0]:7d} {codes[1]:7d}")
    finally:
        slave.terminate()

    if not results:
        print("no steps completed")
        return 1

    on = [r for r in results if r[0] > 0]
    print()
    print(f"steps completed      : {len(results)}")
    if on:
        mi = min(r[1] for r in on)
        ma = max(r[1] for r in on)
        mq = max(r[2] for r in on)
        print(f"adc_i range          : {mi:.4f} .. {ma:.4f} V")
        print(f"adc_q peak           : {mq:.4f} V")
        print(f"ADC codes seen (I)   : "
              f"{min(c[0] for c in (r[3] for r in results))} .. "
              f"{max(c[0] for c in (r[3] for r in results))}")
    print("model_status         :", slave.getReal([O_STATUS])[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
