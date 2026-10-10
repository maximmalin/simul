# Co-simulation status

What works today, what does not, and what to do about it.

## Verified working

| Stage | Evidence |
|---|---|
| SKiDL netlist | 60 parts, 38 nets, KiCad schematic written |
| ngspice deck | `sim/netlists/sdr_skidl_ngspice.cir`, 0 warnings, 0 errors |
| **Direct conversion** | **real**: LO is a 7.2 MHz timer waveform, beat lands at 48.1 kHz (IF offset 50 kHz), I/Q amplitude ratio 0.965, phase +91.6 deg |
| **Q channel** | **driven by a real 90 deg all-pass on the LO, not wired to zero** |
| STM32 pin model | datasheet: 65 ohm from Table 30 at 20 mA; CIO 5 pF and CMOS levels 0.35/0.65*VDD from Table 29 |
| Regression tests | `sim/scripts/test_sdr_netlist.py`, 22/22 |
| Analog + pin contract | `sim/scripts/verify_pin_contract.py`, 31/31 |
| Firmware | `pio run` clean, `firmware.bin` 23 KB, `firmware.hex` via `pio run -t hex` |
| Antenna models | dipole + meander analytic; patch FDTD |
| **STM32 execution** | **qemu-stm32 inside PicSimLab runs the firmware** -- registers, `PC=0x08004068`, reset vector all live |

## The analog chain, and what was wrong with it

The receiver previously simulated without converting anything. Five defects,
each of which had to be fixed before any of the numbers meant anything:

1. **The Q channel was wired to zero.** `B_U_MIX1_Q` ended in `* 0`, so the
   quadrature output was identically 0 V while still converging and still
   parsing. The LT5560's internal 90 degree phase shifter is now modelled as a
   real all-pass, `H = 1 - 2/(1+sRC)`, with `RC = 1/(2*pi*f_LO)`.
2. **There was no LO.** `V_MCU_LO` was a DC source, so the mixer multiplied the
   RF by a constant, which is not mixing. PA6 is a TIM3_CH1 output, so it now
   carries a real square wave with finite edges, gated by the host-alterable
   pin state.
3. **The ADC input was centred on 0 V.** A bipolar baseband signal cannot be
   sampled by a 0..VDD ADC. Both buffers are now biased to mid-rail.
4. **The LNAs amplified their own DC bias.** The common-gate stages were ideal
   VCVS gains from the gate voltage, but the gate is DC-biased and AC-coupled
   from the RF input, so 1.65 V went through the gain as though it were signal:
   ~20 V on the drain, and the mixer's input reached 9.8 V from a 1 mV tone.
   They are now transconductances `gm*(V_g - V_bias)` with the bias read from
   each stage's *own* divider -- the two are biased differently (10k/10k and
   4.7k/2.2k), and assuming VCC/2 for both put a -4.6 V offset on the second.
5. **RF chokes were modelled as bare resistors.** At 7 MHz an 18 uH choke is
   j809 ohm, not the 4 ohm of copper at DC. That 4 ohm across the drain is what
   forced the LNA to be an ideal source at all, which is how the drain reached
   -163 V and dragged the analog rail to -1.45 V. Chokes are now L+R in series,
   and the ferrite bead uses its DC resistance, not its 600 R@100 MHz rating.

Two measurement traps were also hiding results. `print` wraps its table at 80
columns and silently drops every vector past the fifth -- the dead Q channel sat
behind a table that looked complete. And ngspice's adaptive timestep means the
rows are not evenly spaced, so an FFT over them reads a frequency axis that is
simply wrong; the first version of the verifier reported the baseband at
1.15 MHz and the LO at 159 kHz, both artifacts of assuming uniform steps.

### STM32 pin model

Taken from the STM32F103x8/xB datasheet (DS5319), not chosen for convenience:

| Parameter | Value | Source |
|---|---|---|
| Output impedance | 65 ohm | Table 30, IIO = 20 mA: VOL <= 1.3 V and VOH >= VDD-1.3 V give 65 ohm on both edges |
| Pin capacitance CIO | 5 pF | Table 29 |
| Input low VIL | 0.35 x VDD = 1.155 V | Table 29, CMOS port |
| Input high VIH | 0.65 x VDD = 2.145 V | Table 29, CMOS port |
| Input leakage | 1 uA | Table 29, standard I/O |

The 8 mA row of Table 30 is *not* a resistance: VOH is floored at 2.4 V for CMOS
compatibility rather than tracking the drop, so it implies 112 ohm on the
pull-up edge against 50 ohm on the pull-down edge. The 20 mA row is the
self-consistent one.

Note PA0 and PA1 are analog inputs, so strictly no digital threshold applies to
them. ngspice cannot hand an analog level into an event-driven domain, so the
`adc_bridge` is a 1-bit view of the sampled value -- not a claim about the ADC.
The analog nodes ADC_I / ADC_Q carry the full amplitude.

## The blocker: PicSimLab rcontrol does not respond

PicSimLab 0.9.2 is installed (`/usr/bin/picsimlab`) and was verified running
with the Blue Pill:

```
PICSimLab: Using board "Blue Pill"
PICSimLab: Remote Control Port 5000
```

The socket accepts connections but commands are never serviced. The decisive
evidence is the socket state, not the absence of a reply:

```
CLOSE-WAIT 120   127.0.0.1:5000  127.0.0.1:36032
```

`Recv-Q 120` means the server **received 120 bytes and never processed them**.
Combined with a listen backlog that fills to 6 within 40 s of a fresh start and
then sits there, the rcontrol worker thread is not draining its queue. Since
v0.8.8 upstream runs "remote control in one separated thread", that thread
appears not to be running in this build.

Earlier confusion: one test *did* return `Ok` for `version`. That was before
repeated probing filled the backlog; every test since has been starved by it.
Sending `quit` (a documented rcontrol command) also permanently exits the
rcontrol interface, so a raw socket that tries several commands can kill the
responder for good. Use `PicSimLabFMU` rather than a raw socket.

Not a protocol error on our side — `\n`, `\r\n` and a bare command were all
tried, over both `nc` and raw sockets, with a 12 s read timeout and zero bytes
returned. `PICSimLab_NOGUI`, the build intended for headless use, is
**not available here** — `/mnt/ext4data/PICSimLab_NOGUI.AppImage` and `.deb`
are both **0 bytes**.

### To unblock

1. **GUI route (works now, needs a human):** launch `picsimlab`, load the Blue
   Pill, then System → Remote Control. `ss -ltn | grep 5000` confirms the
   socket; the menu action starts the responder.
2. **NOGUI route:** obtain a real `PICSimLab_NOGUI` build. It exists upstream
   and is the supported way to run rcontrol from a terminal.
3. **Automated GUI route:** install `xdotool` (or `python-xlib`) and script the
   menu activation. Neither is currently installed.

## Board config

The board is named **`Blue_Pill`** in PicSimLab, not `generic_STM32F103C8`.
`firmware/picsimlab_sdr.ini` has the correct values. Note `picsimlab_lser`
defaults to `/dev/tnt2`, which does not exist on this machine; set it to
`/dev/null` to silence the error.

## Layout

```
firmware/picsimlab_sdr.ini        board + port config for PicSimLab
sim/scripts/sdr_picsimlab_cosim.py co-simulation driver
sim/scripts/export_ngspice.py      SKiDL netlist -> ngspice deck
sim/scripts/test_sdr_netlist.py    21 regression checks
src/picsimlab_fmu/                 rcontrol + ngspice bridge (from brig-receiver)
```

## Pin boundary

Only the analog→digital direction uses an ngspice XSPICE bridge
(`adc_bridge`, which thresholds properly). The digital→analog direction cannot:
a `dac_bridge` input is an event-driven digital node and an external host
cannot write it — `alter LO_SRC = 3.3` fails with *"no such device or model
name"*. That direction is modelled explicitly instead: a host-alterable
`V_MCU_*` source behind the pin's output resistance and load capacitance.