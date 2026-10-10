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
| Antennas | dipole -14.6 dB, meander -12.0 dB, patch FDTD 2442.5 MHz / eta_rad 30.8% |
| **PicSimLab NOGUI rcontrol** | **responds**: `version`, `pinsl`, `sim`, `loadhex` all return `Ok` |
| **STM32 backend** | **runs**: `qemu-stm32 -M stm32-f103c8-picsimlab-new`, `48 pins [stm32f103c8t6]` |

## The remaining blocker

**When the qemu backend runs, the rcontrol listener never opens.** Not "opens
and stays silent" -- it never binds. This is reproducible, and it is the whole
blocker.

Measured, same binary, same machine, board changed only in the config:

| Build | Board | rcontrol port | Backend |
|---|---|---|---|
| NOGUI 0.9.2 | Arduino Uno | listens, answers (`28 pins [atmega328p]`, `sim start` -> `Ok`) | gpsim |
| NOGUI 0.9.2 | Blue Pill | **never listens** (`ECONNREFUSED`) | `qemu-stm32 -M stm32-f103c8-picsimlab-new` runs |
| NOGUI 0.9.3 | Blue Pill | listens, answers (`48 pins [stm32f103c8t6]`, `Simulation running 1.00x`) | **none** -- library missing |
| GUI 0.9.2 | Blue Pill | listens, then resets the connection | qemu-stm32 runs |
| GUI 0.9.3 | Blue Pill | listens, never answers | library missing |

Two halves, no build with both:

- **0.9.3 NOGUI** -- rcontrol works, but `libqemu-stm32.so` is missing, so no
  firmware executes.
- **0.9.2 NOGUI** -- the STM32 backend works (the log shows `reset is called!`,
  so the firmware is being reset), but rcontrol never binds.

This supersedes the earlier claim in this file that "rcontrol goes silent
while qemu runs". That was measured against a stale instance. Repeated cleanly,
the port is not open at all.

## What brig-receiver does, and why it does not help

`brig-receiver/PICSIMLAB_MCU_INTERFACE.md` and `picsimlab_rt_wrapper.py`
describe rcontrol over a **virtual serial port** (`/dev/tnt2`, baud 921600)
using a different protocol: `M <pin> OUTPUT`, `D <pin>`, `A <pin>`, answered
with `DS:<pin> <state>` and `AD:<pin> <value>`.

That protocol **does not exist in PicSimLab**. Checked against both binaries:

| Token | In binary? |
|---|---|
| `DS:` | no -- the only hits are substrings of simavr's `CMDS:` |
| `AD:` | no |
| `921600` | no |

The real remote-control interface is the text protocol (`version`, `pinsl`,
`sim`, `loadhex`, ...), and it is TCP. "Serial Remote Tank" is a genuine
PicSimLab tool, but it is a serial *terminal*, not the command interface those
files assume. So the brig-receiver wrapper targets a protocol that was never
implemented; there is nothing to port from it.

Separately, the serial route could not be tried anyway: `/dev/tnt*` does not
exist and `tty0tty` is not installed. `socat 1.8.0.3` is available and can
stand in for it, so that path stays open if the protocol question is ever
resolved.

## What was wrong before, and what fixed it

Earlier notes here blamed a "worker thread that never drains its queue" and
recommended the GUI menu action. Both were wrong, and the diagnosis has
changed completely.

**The GUI builds are the problem, and NOGUI is the fix.** The GUI listens on
port 5000 but its responder never services commands, while NOGUI answers
immediately:

```
version -> 'Developed by L.C. Gamboa\r\n ... Version: 0.9.3 ... NOGUI Appimage\r\nOk\r\n>'
```

The `Ok` and the `>` prompt are the proof — that is the responder completing a
command, not a socket that merely accepted. NOGUI also listens with an empty
accept backlog where the GUI's fills to 6–9 and sits there.

Two things were also misread on our side:

- Sending `quit` (a documented rcontrol command) **permanently exits the
  responder**. The raw-socket probing used to diagnose this was destroying the
  thing it was measuring.
- There is **no `board` command**. `board Blue_Pill` returns `ERROR`, and the
  board silently stays whatever the config file says. `sdr_picsimlab_cosim.py`
  expected one and reported `no digital node 'GAIN_SRC'` for unrelated reasons.

## Where the NOGUI builds come from

The copies on this machine (`/mnt/ext4data/PICSimLab_NOGUI.AppImage` and
`.deb`) are **0 bytes**. Real ones are on GitHub:

| Version | File | Size |
|---|---|---|
| 0.9.3 | `PICSimLab_NOGUI-0.9.3_260920_Ubuntu_22.04.5_LTS_x86_64.AppImage` | 16.6 MB |
| 0.9.2 | `PICSimLab_NOGUI-0.9.2_241005_Ubuntu_20.04.6_LTS_x86_64.AppImage` | 21.8 MB |

`https://github.com/lcgamboa/picsimlab/releases` — NOGUI is described upstream
as "must be used on a terminal and with the remote control interface".

Downloaded to `/mnt/ext4data/downloads/`.

## The STM32 backend is missing from every 0.9.3 release

`libqemu-stm32.so` ships in the **0.9.2** package but in **none** of the 0.9.3
artifacts — AppImage, `.deb`, or `latestbuild`. All three ship only
`libqemu-riscv32.so` and `libqemu-xtensa.so`. The symptom is:

```
Message: Error loading libqemu-stm32
Incomplete: DBGGetRAMSize -> lib/board.h :522
```

Copying the 0.9.2 library into the 0.9.3 tree does not work — it is ABI
incompatible:

```
libqemu-stm32.so: error: symbol lookup error: undefined symbol: bql_lock_impl (fatal)
```

`bql_lock_impl` is a QEMU block-layer symbol the 0.9.3 binary does not export.
A 0.9.3-built library would have to be compiled from source; none is published.

## Layout

```
firmware/picsimlab_sdr.ini          board + port config for PicSimLab
sim/scripts/start_picsimlab.py      launch NOGUI, poll until the port answers
sim/scripts/sdr_picsimlab_cosim.py  co-simulation driver
sim/scripts/export_ngspice.py       SKiDL netlist -> ngspice deck
sim/scripts/test_sdr_netlist.py     22 regression checks
sim/scripts/verify_pin_contract.py  31 analog + pin-contract checks
src/picsimlab_fmu/                  rcontrol + ngspice bridge (from brig-receiver)
```

## To unblock

1. **A PicSimLab build whose rcontrol serves commands while qemu runs.** This
   is an upstream defect, not a configuration problem. Worth reporting with
   the evidence above — it is a clean reproducer (NOGUI + Blue Pill, one
   connection, `version` returns nothing; swap to Arduino Uno and it answers).
2. **A 0.9.3-built `libqemu-stm32.so`**, for the combination above.
3. **Upstream fix in the packaging** so NOGUI ships the STM32 backend at all —
   it is currently unusable for every STM32 board.

## Pin boundary

Only the analog→digital direction uses an ngspice XSPICE bridge
(`adc_bridge`, which thresholds properly). The digital→analog direction cannot:
a `dac_bridge` input is an event-driven digital node and an external host
cannot write it — `alter LO_SRC = 3.3` fails with *"no such device or model
name"*. That direction is modelled explicitly instead: a host-alterable
`V_MCU_*` source behind the pin's datasheet output resistance and load
capacitance.

## The analog chain, and what was wrong with it

The receiver previously simulated without converting anything. Five defects,
each concealed by a check that passed anyway:

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
