# kristall-radio — Agent Map

A single, comprehensive map of what this project is, how its pieces fit, how to
run each part, and what is verified vs. still open. Written so that any agent
(or human) can pick the project up without re-deriving the architecture.

Machine-readable companion: `manifest.json` (generate with `python3 tools/dump_manifest.py`).
Status of the co-simulation specifically: `sim/COSIM_STATUS.md`.

---

## 0. TL;DR

**What it is.** A code-first, zero-IF (direct-conversion) SDR receiver built
around an STM32F103C8T6 "Blue Pill". The board, the analog chain, the firmware,
and the antennas are all described by code and verified by simulation. The
headline capability is a **working analog/digital co-simulation**: a real ngspice
netlist wrapped as an FMI 2.0 FMU, hard-locked to a firmware model (and, on the
QEMU path, to the real firmware binary).

**The spine (memorize this):**

```
sim/scripts/sdr_skidl_circuit.py   SKiDL = the single source of truth for connectivity
        │
        ▼
sim/scripts/export_ngspice.py      → sim/netlists/sdr_skidl_ngspice.cir (ngspice deck)
        │
        ▼
sim/scripts/build_fmu.py           → sim/fmu/SdrNgspiceFMU.fmu  (FMI 2.0 slave)
        │
        ▼
sim/scripts/cosim_fmu_stm32.py     hard-lockstep: firmware model  <->  ngspice
sim/scripts/test_fmu_cosim.py      regression through fmpy's FMI master
sim/scripts/cosim_real_stm32.py    the same, but the MCU is the real firmware.elf on QEMU
```

Separately, antennas have their own YAML-driven physics path:

```
sim/inputs/antenna_*.yaml → sim/scripts/antenna_fdtd.py → S11/Zin
                          → sim/scripts/export_antenna_ngspice.py → Touchstone (ngspice)
```

**Fastest way to confirm the project is healthy:**

```bash
cd <repo>
.venv/bin/python sim/scripts/test_sdr_netlist.py     # 22/22 checks
.venv/bin/python sim/scripts/verify_pin_contract.py  # 31/31 checks
```

---

## 1. System overview

```
                         ┌─────────────────────────────────────────────┐
                         │            HOST / OFF-BOARD                  │
                         │                                              │
  sim/scripts/           │  SKiDL → ngspice deck → FMI FMU              │
  sdr_skidl_circuit.py   │        │                                     │
  export_ngspice.py      │        └── sim/netlists/sdr_skidl_ngspice.cir │
  build_fmu.py           │                    │                         │
                         │                    ▼                         │
                         │        sim/fmu/SdrNgspiceFMU.fmu             │
                         │                    │  FMI 2.0 setReal/doStep │
                         └────────────────────┼─────────────────────────┘
                                              │
              ┌───────────────────────────────┼───────────────────────────────┐
              │                               │                               │
     cosim_fmu_stm32.py             cosim_real_stm32.py            sdr_picsimlab_cosim.py
     (firmware MODEL in Python)     (REAL firmware.elf on QEMU)    (PicSimLab rcontrol)
              │                               │                               │
              │                     qemu-system-arm + GDB RSP          PicSimLab NOGUI
              │                     firmware/src/cosim.h mailbox       src/picsimlab_fmu/*
              ▼                               ▼                               ▼
        ┌──────────────────────── analog boundary (pins) ────────────────────────┐
        │ PA0 = ADC_I   PA1 = ADC_Q   PA6 = LO (TIM3_CH1)   PA7 = GAIN           │
        └────────────────────────────────────────────────────────────────────────┘
```

The MCU is never "inside" ngspice. The boundary is explicit at the STM32 pins:

* **analog → digital**: ngspice native `adc_bridge` (thresholds at the datasheet
  CMOS levels). Nodes `ADC_I` / `ADC_Q` → `ADC_I_D` / `ADC_Q_D`.
* **digital → analog**: a `dac_bridge` input is event-driven and cannot be driven
  from outside ngspice (`alter LO_SRC = 3.3` fails). So the pin is modelled
  explicitly: a host-alterable `V_MCU_*` ideal source behind the pin's datasheet
  output resistance (65 Ω) and load capacitance (5 pF), producing analog nodes
  `LO` / `GAIN`.

This decision and its justification are documented at length in
`sim/netlists/sdr_skidl_ngspice.cir` (header) and `sim/COSIM_STATUS.md`.

---

## 2. Verified state (as of `sim/COSIM_STATUS.md`)

| Stage | Evidence |
|---|---|
| SKiDL netlist | 60 parts, 38 nets, KiCad schematic written |
| ngspice deck | `sim/netlists/sdr_skidl_ngspice.cir`, 0 warnings / 0 errors |
| **Direct conversion** | LO is a real 7.2 MHz timer waveform; beat lands at 48.1 kHz (IF offset 50 kHz); I/Q ratio 0.965; phase +91.6° |
| **Q channel** | driven by a real 90° all-pass on the LO, not wired to zero |
| STM32 pin model | 65 Ω @20 mA (DS5319 Table 30); C<sub>io</sub> 5 pF, VIL/VIH 0.35/0.65·VDD (Table 29) |
| Regression tests | `sim/scripts/test_sdr_netlist.py` — 22/22 |
| Analog + pin contract | `sim/scripts/verify_pin_contract.py` — 31/31 |
| Firmware | `pio run` clean; `firmware.bin` 23 KB; `.hex` via `pio run -t hex` |
| Antennas | all real openEMS FDTD: dipole 6.90 MHz / −15.7 dB / η 98 %; patch 2442.5 MHz / −3.6 dB / 31 %; meander reactive, no in-band match, η 0 % |
| PicSimLab NOGUI rcontrol | answers `version`, `pinsl`, `sim`, `loadhex` |
| STM32 backend | `qemu-stm32 -M stm32-f103c8-picsimlab-new`, 48 pins |

---

## 3. Repository layout (roles)

Roles: **source** = keep and edit; **derived** = regenerated by scripts; **data** = external/binary.

| Path | Role | What it is |
|---|---|---|
| `sim/scripts/` | source | All physics, netlist, co-sim, antenna, diagnostics scripts |
| `sim/netlists/` | derived | ngspice decks (`.cir`) produced by `export_ngspice.py` |
| `sim/inputs/` | source | YAML parameter files (one per antenna) |
| `sim/outputs/` | derived | Physics result JSON/PNG |
| `sim/fmu/` | data | Built FMUs (`SdrNgspiceFMU.fmu`, antenna/Bragg blocks) |
| `sim/work/` | derived | Solver scratch (openEMS, Elmer, gmsh) |
| `sim/web_gui/` | source | Flask I/Q web GUI for the FMU |
| `firmware/` | source | STM32 Arduino sketch, cosim mailbox, PlatformIO config |
| `firmware/src/` | source | `firmware.ino`, `cosim.h` |
| `src/picsimlab_fmu/` | source | PicSimLab rcontrol ↔ ngspice bridge package |
| `hardware/` | source | KiCad generation, PCB/3D pipeline, antenna geometry |
| `hardware/kicad/` | source | Schematic generator + KiCad symbol/S-expression library |
| `hardware/tscircuit/` | source | PCB emitter, 3D model resolution, part selection, viewer |
| `tools/` | source | Spec (`spec.py`, `blocks.py`), manifest/init tooling |
| `tools/tscircuit/` | source | Legacy placeholder PCB generator (`build_pcb.mjs`) |
| `tests/` | source | Requirement-matrix + stress tests |
| `requirements/`, `plan/`, `work/`, `tz/`, `references/`, `config/`, `issues/` | source | Requirements, plan, TZ, formulas, parameters |
| `local_footprints/` | data | KiCad footprint libraries |
| `out/` | derived | Generated board artifacts (gerbers/BOM/GLB/STEP) |
| `node_modules/`, `.venv/` | derived | Dependency installs |
| `books/`, `datasheets/` | data | Reference material (from brig-receiver) |
| `.forgejo/` | source | CI workflow definitions (from brig-receiver) |

---

## 4. Subsystems

### 4.1 Circuit source of truth — SKiDL

* **`sim/scripts/sdr_skidl_circuit.py`** — the unified SKiDL netlist generator.
  This is the *only* place connectivity is defined. 60 parts, 38 nets.
* Outputs the KiCad schematic and the netlist consumed downstream.
* **Never hand-edit a generated netlist**; change this file and regenerate.

### 4.2 Analog netlist & ngspice

* **`sim/scripts/export_ngspice.py`** (871 lines) — emits the behavioral
  zero-IF receiver chain to `sim/netlists/sdr_skidl_ngspice.cir`:
  * LNA stages as transconductance macromodels (not ideal VCVS),
  * quadrature LO derived from a real 90° all-pass (`H = 1 − 2/(1+sRC)`),
  * zero-IF mixer with mid-rail-biased limiting baseband buffers,
  * RF chokes as L+R (ferrite bead uses its DC resistance),
  * the STM32 pin boundary (see §1).
* Other decks: `sim/netlists/sdr_receiver.cir`, `sdr_simplified.cir`,
  `sim/netlists/bridge_check.cir`.

### 4.3 Verification suite

| Script | What it proves |
|---|---|
| `sim/scripts/test_sdr_netlist.py` | 22 regression checks on the SKiDL→ngspice export |
| `sim/scripts/verify_pin_contract.py` | 31 closed-loop analog + pin-contract checks |
| `sim/scripts/test_ngspice_nets.py` | smoke tests for the ngspice decks |
| `sim/scripts/test_fmu_cosim.py` | drives the FMU through fmpy's FMI master, checks the pin boundary |
| `tests/requirements/**` | requirement-matrix tests (RF, antenna, AO, system) |
| `tests/stress/test_co_simulation.py` | Bragg FMU + ngspice antenna 2-port stress test |

### 4.4 FMI 2.0 co-simulation (the FMU)

* **`sim/scripts/sdr_ngspice_fmu.py`** (609 lines) — a self-contained FMI 2.0
  co-simulation slave bundling an embedded ngspice deck. Self-contained on
  purpose: `pythonfmu` bundles only this one file, so nothing outside it may be
  imported at run time.
* **`sim/scripts/build_fmu.py`** — regenerates the netlist from SKiDL and splices
  it into the FMU source so the FMU and the deck are the same circuit *by
  construction* (the old hand-embedded deck had drifted).
* **`sim/fmu/SdrNgspiceFMU.fmu`** — the built slave.
* **`sim/scripts/cosim_fmu_stm32.py`** — hard-lockstep driver: STM32 firmware
  *model* ↔ ngspice FMU. Real FMI calls (`setReal`/`doStep`/`getReal`) every step;
  pulses cross the boundary at half an LO period; PA6 drives the mixer through
  the 65 Ω + 5 pF pin model.
* Build: `<venv>/bin/pythonfmu build -f sim/scripts/sdr_ngspice_fmu.py -d sim/fmu`
  (or `python3 sim/scripts/build_fmu.py`).

### 4.5 Real-firmware co-simulation on QEMU

The most ambitious path: the *actual* `firmware.elf` on an emulated Cortex-M in
lockstep with real ngspice.

* **`sim/scripts/cosim_real_stm32.py`** — orchestrator (206 lines).
* **`sim/scripts/gdb_remote.py`** — a minimal GDB remote-serial-protocol client.
  Needed because QEMU's human monitor can read memory (`xp`) but has **no write
  command**, while the GDB stub can read *and* write *and* single-step.
* **`firmware/src/cosim.h`** — the RAM **mailbox** both sides agree on
  (magic, `host_lo`/`host_gain` in, `mcu_stamp`/`adc_i`/`adc_q`/`adc_stamp` out).
* **`sim/scripts/build_cosim_firmware.py`** — builds the `COSIM_MODE` ELF.
* **`sim/scripts/diag_*.py`** — the diagnostic trail that found the two blockers
  (USB CDC enumeration, PLL wait) and the current stop point.

Status: the core reaches `ADC1_2_IRQHandler` and QEMU's STM32F103 peripheral
model starves `loop()` (ADC/DMA fidelity limit of the emulator). Everything
around it — mailbox, exchange, netlist, FMU — is verified. See `sim/COSIM_STATUS.md`.

### 4.6 PicSimLab bridge package

* **`src/picsimlab_fmu/`** — a small library (originally from brig-receiver):
  * `wrapper.py` — `PicSimLabFMU` co-simulation wrapper (`step()` / `run()`),
  * `cosim_bridge.py` — TCP rcontrol ↔ ngspice lockstep loop (+ `parse_pinsl`, `MAX_SAMPLES`),
  * `ngspice_bridge.py` — prompt-synced ngspice CLI pipe (`-p`) bridge,
  * `pin_mapper.py` — board pin allocation (`ARDUINO_UNO`, `STM32_BLUE_PILL`, `ESP32_DEVKITC`),
  * `live_scope.py` — live co-sim oscilloscope (POSIX SHM + semaphores + matplotlib).
* **`sim/scripts/start_picsimlab.py`** — launches PicSimLab NOGUI and polls until
  the rcontrol port accepts (the board comes from the `.ini`; there is **no
  `board` command** in rcontrol).
* **`sim/scripts/sdr_picsimlab_cosim.py`** — the SDR-specific driver.
* **`sim/scripts/rcontrol*.py`** — low-level rcontrol probing.
* Config: `firmware/picsimlab_sdr.ini`.

Blocked upstream: with the QEMU backend running, rcontrol never binds (0.9.2);
0.9.3 has rcontrol but no `libqemu-stm32.so`. Full evidence in `sim/COSIM_STATUS.md`.

### 4.7 Firmware

* **`firmware/src/firmware.ino`** (135 lines) — minimal firmware that reaches
  `loop()` and services the mailbox.
  * Pins: `PA0`=ADC_I, `PA1`=ADC_Q, `PA6`=LO (TIM3_CH1), `PA7`=GAIN.
  * Under `COSIM_MODE`, overrides `SystemClock_Config()` to skip the PLL wait
    (QEMU never sets `PLLRDY`).
* **`firmware/src/cosim.h`** — mailbox layout (see §4.5).
* **`firmware/platformio.ini`** — two environments:
  * `bluepill_f103c8` — the shipped image (USB CDC on);
  * `cosim` — USB compiled out (`Serial.begin()` blocks forever without a USB host).
  * Deliberately separate so the USB-less image can never be flashed to a bench.
* **`firmware/scripts/hex.py`** — post-build `objcopy` to emit Intel HEX for
  PicSimLab's `loadhex`.

### 4.8 Antenna physics (openEMS FDTD from YAML)

* **`sim/inputs/antenna_*.yaml`** — one file per antenna (adding an antenna = add
  a YAML file, no code edits). Present: dipole 7 MHz, meander 200 MHz, patch 2.45 GHz.
* **`sim/scripts/antenna_fdtd.py`** — YAML → S11/Zη → Touchstone. All three
  shipped antennas run the **real openEMS FDTD** solver (`fdtd: true`); setting
  `fdtd: false` falls back to an analytic thin-wire / transmission-line estimate.
* **`sim/scripts/mesh.py`** — openEMS mesh sizing with a hard memory budget.
* **`sim/scripts/export_antenna_ngspice.py`** — results → ngspice S-parameter networks.
* Needs openEMS: `export LD_LIBRARY_PATH=$HOME/opt/openEMS/lib:$LD_LIBRARY_PATH`
  and an openEMS-enabled interpreter.

### 4.9 Web GUI

* **`sim/web_gui/app.py`** — Flask app that runs `SdrNgspiceFMU.fmu` via `fmpy`,
  plots I/Q live, exposes `stim_level` / `lo_frequency`, and can export an
  `sdr_cosim.xcos` diagram for Xcos.
* **`sim/web_gui/templates/index.html`** — Plotly dark dashboard.
* Run: `python3 sim/web_gui/app.py` → http://localhost:5000.

### 4.10 Diagnostics

`sim/scripts/diag_*.py` (`infinite`, `literal`, `mailbox`, `pc`, `regs`, `watch`)
are the forensic scripts used to find the QEMU blockers. Keep them: they are the
reproducible evidence trail, not scratch.

### 4.11 Hardware / PCB / 3D toolchain

* `tools/spec.py`, `tools/blocks.py` — the project spec (6 blocks, 24 parts) used
  by the generators.
* `hardware/pipeline.py` — pipeline orchestrator.
* `hardware/kicad/kicad_lib.py` — KiCad symbol/schematic helper.
* **`hardware/kicad/build_board.py`** — deterministic code-first board build
  (`sim/scripts/sdr_skidl_circuit.py` → `kinet2pcb` → `kicad-cli`). It writes the
  derived `out/` set (`board.kicad_pcb`, `gerbers/`, `drill/`, `gerbers.zip`,
  `board.{step,glb,svg,pdf,dxf}`, `pnp.csv`, `bom.csv`) and nothing there is
  hand-edited. The board is a placed, net-complete definition; routing is manual.
* `tools/tscircuit/build_pcb.mjs` — legacy placeholder generator, superseded by
  `hardware/kicad/build_board.py` for kristall.
* Transferred from brig-receiver, the *real* manufacturing toolchain now lives in
  `hardware/tscircuit/` and `scripts/` (see §4.12).

### 4.12 Transferred brig-receiver toolchain (new)

The following capability was ported in from `brig-receiver` (a sibling project by
the same author) and is **additive** — no existing kristall-radio file was
modified. It provides the hardware/manufacturing/repro layer that kristall-radio
lacked.

| Area | Files |
|---|---|
| Project manifest & init | `tools/dump_manifest.py`, `tools/init_project.py` |
| KiCad code-generation & verification | `hardware/kicad/generate_brig_receiver.py`, `test_any_schematic.py`, `verify_nets.py` |
| tscircuit PCB/3D/BOM | `hardware/tscircuit/{build_pcb.mjs,build_derived.py,resolve_parts.py,step2stl.py,check_models3d.py,check_net.py,site_assets.py,fetch_vendor_models.mjs,fetch_3dmodels*.sh}`, `tscircuit.config.{ts,json}` |
| Part selection / datasheets | `scripts/{download-datasheets.js,convert-kicad-to-circuit-json.js,post-build-datasheets.js}`, `scripts/lib/*` |
| Antenna geometry (FreeCAD) | `hardware/antenna/**`, `sim/cad/**` |
| FEM / structural (Elmer) | `sim/scripts/{msh_to_elmer.py,bragg_sim.py,bragg_fmu.py}`, `sim/octave/**` |
| Radiation / NEC | `sim/scripts/radiation.py`, `sim/nec/**` |
| openEMS antenna sim | `sim/scripts/antenna_sim.py`, `sim/scripts/smoke_tutorial.py` |
| GOST drawings | `gost_pipeline.py`, `block0_bus_power_calc.py`, `gen_spice_from_kicad.py` |
| PicSimLab RT wrapper | `picsimlab_rt_wrapper.py` |
| Requirements / plan / TZ | `requirements/**`, `plan/**`, `work/tz-*`, `references/**`, `issues/**` |
| CI | `.forgejo/workflows/*` |
| Tests | `tests/requirements/**`, `tests/stress/**`, `tests/test_principle_schematic.py` |

Some transferred files are brig-domain-specific (Bragg cell acousto-optics, FPAA,
ESP32 firmware). They are kept for completeness; treat them as a toolbox, not as
part of the SDR signal path.

**Preserved (not overwritten).** These files exist in both projects with
different, project-specific content and were deliberately left as kristall's:

* `sim/scripts/export_ngspice.py` — kristall's SKiDL→ngspice export (brig's
  antenna export is already covered by `export_antenna_ngspice.py`).
* `hardware/kicad/kicad_lib.py`, `hardware/pipeline.py`,
  `hardware/tscircuit/make_viewer.py` — kristall's own implementations.
* `package.json`, `package-lock.json` — kristall's npm identity.
* `sim/scripts/antenna_fmu.py`, `sim/scripts/realtime_loop_fmu.py`,
  `sim/scripts/test_fmu.py` — kristall's variants already present.

### 4.13 Antenna FDTD + second brig transfer

**All three antennas are now real openEMS FDTD** (`model: fdtd` in
`sim/outputs/*.json`), driven by `sim/scripts/antenna_fdtd.py`:

* dipole 7 MHz — `build_dipole` (centre-fed wire in a PML box): f<sub>res</sub>
  6.90 MHz, S11 −15.7 dB, η 98 %.
* patch 2.45 GHz — `build_patch`: f<sub>res</sub> 2442.5 MHz, S11 −3.6 dB, η 31 %.
* meander 200 MHz — `build_meander` (three-arm boustrophedon on a 150 × 150 mm
  panel over a full bottom ground, mirroring `build_brig_antennas_fc.py`):
  reactive, no in-band match, η 0 %.

Set `fdtd: false` in the YAML to use the analytic fallback instead.

Additional material pulled from `brig-receiver` (source/docs only; the derived
outputs are regenerated by the already-transferred scripts and were not copied):

| Item | Location |
|---|---|
| End-to-end system calculation (Octave notebook) | `calculations/antenna_system_calc{,_executed}.ipynb` |
| Runnable Octave extraction | `sim/octave/antenna_system_calc.m` |
| Structural block diagram | `references/structural_block.drawio` |

**Removed as duplicates.** Only *functionality* was transferred; brig files whose
content already exists in kristall-radio were deleted rather than copied:

* `references/brig_project_map.md` → `AGENT_MAP.md` is kristall's own project map.
* `references/antenna_system_calc.md`, `references/techtask_notes.md` →
  `sim/octave/antenna_system_calc.m`, `technical_task.md`.
* `firmware/picsimlab_brig_receiver_workspace.xml` → `firmware/picsimlab_sdr.ini`.

---

## 5. Entry points — command cheat-sheet

```bash
# --- Environment ---
.venv/bin/python ...                 # project venv (skidl, pythonfmu, fmpy, ...)
python3 ...                          # system python is fine for pure-stdlib tools

# --- Circuit → netlist ---
.venv/bin/python sim/scripts/sdr_skidl_circuit.py     # regenerate SKiDL netlist
.venv/bin/python sim/scripts/export_ngspice.py        # regenerate ngspice deck

# --- Verify ---
.venv/bin/python sim/scripts/test_sdr_netlist.py      # 22/22
.venv/bin/python sim/scripts/verify_pin_contract.py   # 31/31
.venv/bin/python sim/scripts/test_ngspice_nets.py

# --- FMU co-simulation ---
python3 sim/scripts/build_fmu.py                      # rebuild FMU from SKiDL
.venv/bin/python sim/scripts/cosim_fmu_stm32.py       # lockstep firmware-model ↔ ngspice
.venv/bin/python sim/scripts/test_fmu_cosim.py        # fmpy regression

# --- Real firmware on QEMU ---
.venv/bin/python sim/scripts/build_cosim_firmware.py  # COSIM_MODE ELF
.venv/bin/python sim/scripts/cosim_real_stm32.py      # real firmware ↔ ngspice

# --- PicSimLab ---
.venv/bin/python sim/scripts/start_picsimlab.py       # launch NOGUI, wait for rcontrol
.venv/bin/python sim/scripts/sdr_picsimlab_cosim.py   # drive co-sim

# --- Antennas ---
export LD_LIBRARY_PATH=$HOME/opt/openEMS/lib:$LD_LIBRARY_PATH
<venv-with-openEMS>/bin/python sim/scripts/antenna_fdtd.py sim/inputs/antenna_patch_2450mhz.yaml
.venv/bin/python sim/scripts/export_antenna_ngspice.py sim/outputs/<result>.json

# --- Firmware build ---
cd firmware && pio run                # shipped image
cd firmware && pio run -e cosim       # USB-less co-sim image
cd firmware && pio run -t hex         # emit Intel HEX

# --- Web GUI ---
python3 sim/web_gui/app.py            # http://localhost:5000

# --- Hardware / manifest ---
node tools/tscircuit/build_pcb.mjs            # legacy placeholder
node hardware/tscircuit/build_pcb.mjs         # real tscircuit emitter (transferred)
python3 hardware/tscircuit/build_derived.py   # local part table
python3 hardware/tscircuit/resolve_parts.py   # part substitution
python3 tools/dump_manifest.py                # write manifest.json
node scripts/download-datasheets.js           # fetch MPN datasheets
python3 gost_pipeline.py                       # STEP → cleaned → GOST drawing
```

---

## 6. Environment

**System tools:** `python3`, `ngspice` (44.2), `kicad-cli` (9.0.2), `node`/`npm`,
`pio` (PlatformIO), `qemu-system-arm`, `gdb-multiarch`, `gmsh`, Elmer, openEMS.

**Python venv (`.venv`):** `skidl`, `pythonfmu`, `fmpy`, `numpy`, `flask`, `matplotlib`.
Antenna FDTD needs a *separate* interpreter with openEMS bindings.

**External data / installs:**
* `/usr/share/kicad/symbols`, `/usr/share/kicad/footprints`
* `$HOME/opt/openEMS` — openEMS install (library path above)
* `/mnt/ext4data/jlcparts` — JLCPCB parts DB used by the transferred part resolver
* Elmer / gmsh for the FEM path

**PicSimLab:** NOGUI AppImages in `/mnt/ext4data/downloads/`
(`PICSimLab_NOGUI-0.9.2` has the STM32 backend; `0.9.3` has a working rcontrol but
ships no `libqemu-stm32.so`). See `sim/COSIM_STATUS.md`.

---

## 7. Known limitations / open items

1. **QEMU real-firmware path** stops at `ADC1_2_IRQHandler` — QEMU's STM32F103
   ADC/DMA model is not faithful enough for `loop()` to be serviced. Emulator
   fidelity limit, not a project defect.
2. **PicSimLab rcontrol never binds while QEMU runs** (0.9.2), and **0.9.3 has no
   STM32 backend**. Needs an upstream fix / a 0.9.3-built `libqemu-stm32.so`.
3. **`tools/tscircuit/build_pcb.mjs` is a placeholder** — the real emitter is
   `hardware/tscircuit/build_pcb.mjs` (transferred).
4. **`out/` artifacts are text templates**, not manufacturing outputs.
5. Several transferred brig scripts assume brig's paths/data (e.g. `config/params.yaml`
   points at `/home/mirrage/brig-receiver/...`); adapt before use.
6. `docs/` is empty; this file plus `sim/COSIM_STATUS.md` and `manifest.json` are
   the authoritative documentation.

---

## 8. Conventions for agents

* **One source of truth per thing.** Connectivity = SKiDL; physics = the
  `sim/scripts/*` entry points; parameters = YAML in `sim/inputs/`.
* **Don't hand-edit derived files** (`sim/netlists/`, `sim/outputs/`, `out/`).
* **Pin boundary is a contract.** PA0/PA1 = ADC I/Q, PA6 = LO, PA7 = GAIN. This is
  asserted by `verify_pin_contract.py` and by `firmware.ino`.
* **Datasheet numbers, not convenience.** Pin impedance and thresholds come from
  DS5319; keep it that way.
* **Prove it.** Any claim about the analog chain should be backed by
  `test_sdr_netlist.py` / `verify_pin_contract.py` / `test_fmu_cosim.py`.
* **Regenerate, don't patch.** `build_fmu.py` exists precisely so the FMU can't
  drift from SKiDL.

See `sim/COSIM_STATUS.md` for the co-simulation narrative and evidence.
