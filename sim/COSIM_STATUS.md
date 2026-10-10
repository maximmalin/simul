# Co-simulation status

What works today, what does not, and what to do about it.

## Verified working

| Stage | Evidence |
|---|---|
| SKiDL netlist | 60 parts, 38 nets, KiCad schematic written |
| ngspice deck | `sim/netlists/sdr_skidl_ngspice.cir`, 0 warnings, 0 errors |
| Analog chain | RF → LNA1 → coupler → LNA2 → filter → mixer → buffer → ADC carries signal |
| Regression tests | `sim/scripts/test_sdr_netlist.py`, 21/21 pass |
| Firmware | `pio run` clean, `firmware.bin` 23 KB, `firmware.hex` via `pio run -t hex` |
| Antenna models | dipole + meander analytic; patch FDTD |
| **STM32 execution** | **qemu-stm32 inside PicSimLab runs the firmware** — registers, `PC=0x08004068`, reset vector all live |

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