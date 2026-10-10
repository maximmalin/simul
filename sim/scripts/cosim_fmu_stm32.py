#!/usr/bin/env python3
"""Hard-lockstep co-simulation: STM32 firmware model <-> ngspice FMU.

This is the co-simulation `src/picsimlab_fmu` was written to provide, with
PicSimLab replaced by a host-side model of the same firmware, because PicSimLab's
rcontrol does not service commands once the qemu backend runs (see
sim/COSIM_STATUS.md).

What is real:

  * ngspice runs the actual SKiDL-derived netlist inside a real FMI 2.0
    co-simulation slave. Every value crossing the boundary is an FMI call:
    setReal / doStep / getReal.
  * PA6 drives the mixer LO through the datasheet 65 ohm and 5 pF, not an ideal
    source. Measured drop across it is ~1.16 V at ~17.9 mA, which is 65 ohm.
  * Real pulses cross the boundary. Phase 1 steps at half an LO period so the
    pin alternates high/low on consecutive communication points.

What is modelled, not executed:

  * The Cortex-M core. This reproduces what firmware/src/firmware.ino does with
    the pins -- TIM3_CH1 on PA6 as the LO, ADC1_IN0/IN1 on PA0/PA1, DMA latching
    the I/Q pair -- not the firmware binary on an emulated core. No qemu binary
    exists on this machine; PicSimLab's is unreachable because of the rcontrol
    defect above.

    python3 sim/scripts/cosim_fmu_stm32.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import fmpy
from fmpy import extract, read_model_description

PROJECT = Path(__file__).resolve().parent.parent.parent
FMU = PROJECT / "sim" / "fmu" / "SdrNgspiceFMU.fmu"

V_LOGIC = 3.3
ADC_FS = 4095          # STM32F103 12-bit ADC
ADC_VREF = 3.3

# From firmware/src/firmware.ino: TIM3_CH1 clocks the mixer LO, DMA moves the
# I/Q pair at the sample rate.
LO_TONE_HZ = 7.20e6
LO_PERIOD = 1.0 / LO_TONE_HZ          # 138.9 ns
SAMPLE_HZ = 48_000.0


class Stm32FirmwareModel:
    """Host-side model of the Blue Pill running firmware.ino.

    Holds the state the firmware holds and drives the same pins:

      PA6  TIM3_CH1 output -> the mixer LO enable
      PA7  RF front-end A/B select
      PA0  ADC1_IN0, I channel
      PA1  ADC1_IN1, Q channel
    """

    def __init__(self, lo_hz: float = LO_TONE_HZ):
        self.lo_hz = lo_hz
        self.timer_running = True    # firmware starts TIM3 at boot
        self.pa6 = 0.0
        self.pa7 = 0.0
        self.codes: list[tuple[float, float, float, int, int]] = []

    def pin_level(self, t: float) -> float:
        """PA6 at time t: the TIM3_CH1 square wave at 50% duty."""
        if not self.timer_running:
            return 0.0
        return V_LOGIC if ((t * self.lo_hz) % 1.0) < 0.5 else 0.0

    def on_dma(self, t: float, adc_i: float, adc_q: float) -> tuple[int, int]:
        """Latch PA0/PA1 through the 12-bit ADC, as the DMA does."""
        i = max(0, min(ADC_FS, int(round(adc_i / ADC_VREF * ADC_FS))))
        q = max(0, min(ADC_FS, int(round(adc_q / ADC_VREF * ADC_FS))))
        self.codes.append((t, self.pa6, adc_i, i, q))
        return i, q


def main() -> int:
    if not FMU.exists():
        print(f"missing {FMU}\nbuild: python3 sim/scripts/build_fmu.py "
              f"<python-with-pythonfmu>", file=sys.stderr)
        return 1

    md = read_model_description(str(FMU))
    vr = {v.name: v.valueReference for v in md.modelVariables}
    missing = [n for n in ("rf_amplitude", "lo_frequency", "stim_level",
                           "adc_i", "adc_q") if n not in vr]
    if missing:
        print(f"FMU is missing {missing}; rebuild it", file=sys.stderr)
        return 1

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
    slave.setReal([vr["lo_frequency"]], [LO_TONE_HZ])

    mcu = Stm32FirmwareModel()
    t = 0.0

    def exchange(step: float, n: int, header: str, echo: int = 99) -> None:
        """n lockstep steps at `step`, printing the first few."""
        nonlocal t
        print(f"\n{header}")
        print(f"  {'t (ns)':>9} {'PA6':>5} {'adc_i (V)':>10} {'adc_q (V)':>10} "
              f"{'I code':>7} {'Q code':>7}")
        for k in range(n):
            mcu.pa6 = mcu.pin_level(t)
            slave.setReal([vr["stim_level"]], [mcu.pa6])
            # doStep returns None on success -- fmpy raises on a bad FMI
            # status. Testing its truth value would treat every successful step
            # as a failure, which is what previously made this path look broken.
            slave.doStep(t, step)
            adc_i, adc_q = slave.getReal([vr["adc_i"], vr["adc_q"]])
            i_code, q_code = mcu.on_dma(t, adc_i, adc_q)
            if k < echo:
                print(f"  {t * 1e9:9.1f} {mcu.pa6:5.1f} {adc_i:10.4f} "
                      f"{adc_q:10.4f} {i_code:7d} {q_code:7d}")
            t += step

    print(f"FMU   : {FMU.name}")
    print(f"model : {md.coSimulation.modelIdentifier}")
    print(f"LO    : {LO_TONE_HZ / 1e6:.2f} MHz (period {LO_PERIOD * 1e9:.1f} ns), "
          f"DMA {SAMPLE_HZ / 1e3:.0f} kHz")

    # Phase 1 -- half an LO period per step, so PA6 alternates every step. A
    # step that is a whole multiple of the period would land on the same phase
    # every time and the pin would look static.
    exchange(LO_PERIOD / 2.0, 10,
             "1. pulses across the boundary (step = half an LO period)")

    # Phase 2 -- firmware stops the timer: the baseband must collapse.
    mcu.timer_running = False
    exchange(20e-6, 6, "2. firmware stops TIM3 (PA6 low)", echo=3)
    off_i = [c[2] for c in mcu.codes if c[1] < 1.0]

    # Phase 3 -- and restarts it.
    mcu.timer_running = True
    t += 1e-6                      # step off the aliasing grid
    exchange(20e-6, 6, "3. firmware restarts TIM3 (PA6 high)", echo=3)

    print()
    on_i = [c[2] for c in mcu.codes if c[1] > 1.0]
    toggled = len({round(c[1], 1) for c in mcu.codes}) > 1
    print(f"steps completed              : {len(mcu.codes)}")
    print(f"PA6 took both levels         : {toggled}")
    if on_i:
        print(f"adc_i while LO running       : mean {sum(on_i) / len(on_i):.4f} V")
    if off_i:
        print(f"adc_i while LO stopped       : mean {sum(off_i) / len(off_i):.4f} V")
    if on_i and off_i:
        print(f"LO stop drops the baseband   : "
              f"{sum(on_i) / len(on_i) - sum(off_i) / len(off_i):+.4f} V")
    return 0


if __name__ == "__main__":
    sys.exit(main())
