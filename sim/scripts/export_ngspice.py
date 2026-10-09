#!/usr/bin/env python3
"""Export the simulated Brig antennas as ngspice-ready S-parameter networks.

Reads ``sim/outputs/openems_results.json`` (the Zin/S11 sweeps produced by
``antenna_sim.py`` / ``radiation.py``) and writes, per channel, under
``sim/outputs/ngspice/``:

    ant_ch<X>.s1p      1-port Touchstone: feed reflection (exact, from Zin)
    ant_ch<X>.s2p      2-port Touchstone: port1 = feed, port2 = radiated wave
    ant_sparams.lib    ngspice subcircuits (XSPICE ``xfer``) wrapping the files
    antenna_model.net  demo netlist: test source + antenna network (SP analysis)
    README.md          port conventions and usage

Two-port convention
-------------------
::

        port 1 (feed)  ---[ ANT_CHx ]---  port 2 (radiated wave)
         to RF front-end                   test signal / free space

* **Port 1** is the antenna feed, referenced to 50 ohm.  Its reflection
  coefficient is taken directly from the FDTD input impedance::

      S11(f) = (Zin(f) - 50) / (Zin(f) + 50)

* **Port 2** is the radiated-wave ("space") port, a matched sink (S22 = 0).
  It carries the power the antenna actually radiates::

      S21 = S12 = sqrt(eta) * sqrt(1 - |S11|^2) * exp(j * arg(S11))

  where ``eta`` is the radiation efficiency (FDTD ``Prad/P_acc`` at
  resonance; 1.0 for the ideal analytic Channel A).

This is the standard "antenna as a two-port transducer" used for
circuit/system co-simulation: drive port 2 with a test source to emulate the
received wave and read port 1 into the receiver front-end, or drive port 1 to
load the transmitter.

Why no FMU is needed
--------------------
ngspice 44 reads Touchstone data natively through the XSPICE ``xfer`` code
model (same mechanism as the shipped example
``/usr/share/doc/ngspice/examples/sp/file.cir``).  An FMU wrapper would only
add a co-simulation boundary and is therefore not required for this model.

Usage
-----
::

    .venv/bin/python sim/scripts/export_ngspice.py            # export
    .venv/bin/python sim/scripts/export_ngspice.py --validate # + ngspice check
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT / "sim" / "outputs" / "openems_results.json"
OUTDIR = ROOT / "sim" / "outputs" / "ngspice"
Z0 = 50.0

# (json key, short name, human label) -- order defines the lib/netlist order
CHANNELS = [
    ("channel_a", "A", "Ch A: printed dipole, 0-900 MHz"),
    ("channel_b", "B", "Ch B: patch 2.45 GHz"),
    ("channel_c", "C", "Ch C: patch 5.8 GHz"),
]


def z_to_s11(Z: np.ndarray, z0: float = Z0) -> np.ndarray:
    """Complex S11 of a series (loaded) impedance Z."""
    return (Z - z0) / (Z + z0)


def _g(v: float) -> str:
    return f"{v:.10g}"


def write_s1p(path: Path, freqs, s11, comment: str) -> None:
    lines = [f"! {comment}", "# Hz S RI R 50"]
    for f, s in zip(freqs, s11):
        lines.append(f"{_g(f)} {_g(s.real)} {_g(s.imag)}")
    path.write_text("\n".join(lines) + "\n")


def write_s2p(path: Path, freqs, s11, s12, s21, s22, comment: str) -> None:
    lines = [f"! {comment}", "# Hz S RI R 50"]
    for f, a, b, c, d in zip(freqs, s11, s12, s21, s22):
        lines.append(" ".join([
            _g(f),
            _g(a.real), _g(a.imag),
            _g(b.real), _g(b.imag),
            _g(c.real), _g(c.imag),
            _g(d.real), _g(d.imag),
        ]))
    path.write_text("\n".join(lines) + "\n")


def load_channel(key: str):
    """Return (freqs, Zin) for a channel, or None if absent."""
    data = json.loads(RESULTS.read_text())
    ch = data.get(key)
    if not ch or "freq_hz" not in ch:
        return None, None
    f = np.asarray(ch["freq_hz"], dtype=float)
    Z = np.asarray(ch["Z_real"], dtype=float) + 1j * np.asarray(ch["Z_imag"], dtype=float)
    order = np.argsort(f)
    return f[order], Z[order]


def build_networks(eta: float | None = None):
    """Per channel: (short, label, freqs, s11, s12, s21, s22, f_res, S11_min, eta).

    ``eta`` overrides the per-channel radiation efficiency read from the
    results json (``eta_rad``); pass ``None`` to use the simulated value.
    """
    out = []
    data = json.loads(RESULTS.read_text())
    for key, short, label in CHANNELS:
        f, Z = load_channel(key)
        if f is None or len(f) < 3:
            print(f"  [skip] {key}: no sweep in results json")
            continue
        ch = data[key]
        eta_ch = eta if eta is not None else float(ch.get("eta_rad", 1.0))
        eta_ch = float(np.clip(eta_ch, 0.0, 1.0))
        rho = z_to_s11(Z)
        # Enforce passivity.  Far out of band the FDTD Zin can give |S11|
        # marginally greater than 1 (numerical); sqrt(1-|S11|^2) would then be
        # exactly 0, which makes ngspice's db() of the received voltage fail.
        # Clip the magnitude only (phase preserved) with a tiny margin so S21
        # stays finite.
        mag_raw = np.abs(rho)
        n_over = int(np.count_nonzero(mag_raw > 1.0))
        if n_over:
            print(f"  [{key}] clipped {n_over} point(s) with |S11|>1 "
                  f"down to 1-1e-9 (passivity)")
        mag = np.clip(mag_raw, 0.0, 1.0 - 1e-9)
        rho = mag * np.exp(1j * np.angle(rho))
        tau = np.sqrt(np.clip(1.0 - mag ** 2, 0.0, None)) * np.exp(1j * np.angle(rho))
        tau = tau * np.sqrt(eta_ch)
        s22 = np.zeros_like(rho)
        f_res = float(ch.get("f_res", f[np.argmin(mag)]))
        ires = int(np.argmin(np.abs(f - f_res)))
        smin = 20.0 * np.log10(max(float(mag[ires]), 1e-9))
        out.append((short, label, f, rho, tau, tau, s22, f_res, smin, eta_ch))
    return out


def write_lib(networks, lib: Path) -> None:
    blocks = [
        "* Brig Receiver antenna S-parameter subcircuits (ngspice 44+)",
        "* Generated by sim/scripts/export_ngspice.py -- do not edit by hand.",
        "* 2-port: pin 1 = feed (to RF front-end), pin 2 = radiated-wave port,",
        "*         pin 3 = reference (GND).  Pass the matching Touchstone file:",
        "*             Xant feed space 0 ANT_CHB touchstone=\"ant_chB.s2p\"",
        "",
    ]
    for short, label, *_ in networks:
        blocks += [
            f"* --- {label} ---",
            f".SUBCKT ANT_CH{short} 1 2 3 touchstone={{touchstone}}",
            "* pin 3 is the reference plane (connect to GND)",
            "* Z1 = Z2 = 50 ohm; the six controlled sources synthesise the 2-port",
            "R1N 1 100 -5.000000e+01",
            "R1P 100 101 100.000000",
            "R2N 2 200 -5.000000e+01",
            "R2P 200 201 100.000000",
            "",
            "* S11",
            "A0101 %vd 100 3 %vd 101 102 m_a0101",
            ".model m_a0101 xfer file=touchstone span=9",
            "",
            "* S12",
            "A0102 %vd 200 3 %vd 102 3 m_a0102",
            ".model m_a0102 xfer file=touchstone span=9 offset=3",
            "",
            "* S21",
            "A0201 %vd 100 3 %vd 201 202 m_a0201",
            ".model m_a0201 xfer file=touchstone span=9 offset=5",
            "",
            "* S22",
            "A0202 %vd 200 3 %vd 202 3 m_a0202",
            ".model m_a0202 xfer file=touchstone span=9 offset=7",
            ".ENDS",
            "",
        ]
    lib.write_text("\n".join(blocks) + "\n")


def write_demo(networks, net: Path) -> None:
    short = networks[1][0] if len(networks) > 1 else networks[0][0]
    fmin = networks[1][2].min() if len(networks) > 1 else networks[0][2].min()
    fmax = networks[1][2].max() if len(networks) > 1 else networks[0][2].max()
    net.write_text(f"""* Demo: SP analysis of the exported Ch {short} antenna network.
* Two ports: 1 = feed, 2 = radiated wave.  ngspice reports S_1_1 / S_2_1.
*
* Run from the repository root:
*     ngspice -b sim/outputs/ngspice/antenna_model.net

V1        feed  0  dc 0 ac 1 portnum 1 z0 50
V2        space 0  dc 0 ac 0 portnum 2 z0 50
Xant_ch{short}  feed space 0 ANT_CH{short} touchstone="sim/outputs/ngspice/ant_ch{short.lower()}.s2p"

.include sim/outputs/ngspice/ant_sparams.lib

.control
sp lin 101 {fmin:.6g} {fmax:.6g}
print S_1_1 S_2_1
.endc
.END
""")


def _sp_run(netlist: str, workdir: Path) -> str:
    cir = workdir / "check.cir"
    cir.write_text(netlist)
    proc = subprocess.run(["ngspice", "-b", str(cir)], cwd=workdir,
                          capture_output=True, text=True)
    return proc.stdout + proc.stderr


def validate(networks, workdir: Path) -> None:
    shutil.copy(OUTDIR / "ant_sparams.lib", workdir / "ant_sparams.lib")
    print("\nngspice validation (model vs FDTD):")
    for short, label, f, rho, s12, s21, s22, f_res, smin, *_ in networks:
        shutil.copy(OUTDIR / f"ant_ch{short.lower()}.s2p",
                    workdir / f"ant_ch{short.lower()}.s2p")
        fmin, fmax = float(f.min()), float(f.max())
        netlist = f"""* validation {short}
V1 a 0 dc 0 ac 1 portnum 1 z0 50
Xant a b 0 ANT_CH{short} touchstone="ant_ch{short.lower()}.s2p"
V2 b 0 dc 0 ac 0 portnum 2 z0 50
.include ant_sparams.lib
.control
sp lin 61 {fmin:.6g} {fmax:.6g}
wrdata chk_{short}.dat S_1_1
.endc
.END
"""
        out = _sp_run(netlist, workdir)
        dat = workdir / f"chk_{short}.dat"
        if not dat.exists():
            print(f"  {label}: ngspice failed\n{out[-500:]}")
            continue
        arr = np.loadtxt(dat)
        fsim = arr[:, 0]
        s11_ng = arr[:, 1] + 1j * arr[:, 2]
        s11_fdtd = np.interp(fsim, f, rho, left=rho[0], right=rho[-1])
        err = np.max(np.abs(s11_ng - s11_fdtd))
        print(f"  {label}: max|dS11| = {err:.3e}   "
              f"(f_res={f_res/1e9:.3f} GHz, S11_min={smin:.1f} dB)")
        dat.unlink(missing_ok=True)


def write_readme(networks, path: Path) -> None:
    rows = "\n".join(
        f"| {s} | `ant_ch{s.lower()}.s1p` | `ant_ch{s.lower()}.s2p` | "
        f"{f_res/1e9:.3f} | {'ideal' if smin < -60 else format(smin, '.1f')} | "
        f"{eta*100:.0f}% |"
        for s, _, f, rho, s12, s21, s22, f_res, smin, eta in networks
    )
    path.write_text(f"""# Brig Receiver antennas -> ngspice S-parameter networks

Generated by `sim/scripts/export_ngspice.py` from the openEMS FDTD results in
`sim/outputs/openems_results.json`.  Reference impedance 50 ohm everywhere.

## Data provenance

* **Ch B / Ch C** - single-patch FDTD (openEMS, FR-4 eps_r=4.5, tan_d=0.02,
  h=0.8 mm, PML_8 absorbing boundaries).  The patch resonant length and the
  probe offset were retuned from an initial simulation so that each element
  resonates at its design frequency and presents ~50 ohm at the feed.
  ``Zin(f)`` is the simulated input impedance; ``S11`` is exact.  Radiation
  efficiency is the FDTD ``Prad/P_acc`` ratio at resonance.
* **Ch A** - analytic printed-dipole model (0-900 MHz).  Not an FDTD result;
  efficiency is assumed ideal.  Replace with an FDTD run if a
  frequency-dependent dipole model is required.

## Files

| Ch | 1-port | 2-port | f_res (GHz) | S11 @ f_res (dB) | eta_rad |
|----|--------|--------|-------------|------------------|---------|
{rows}

* `ant_ch<X>.s1p` - antenna feed reflection only (exact FDTD `Zin`).
* `ant_ch<X>.s2p` - 2-port: port 1 = feed, port 2 = radiated-wave port.
* `ant_sparams.lib` - ngspice subcircuits `ANT_CHA` / `ANT_CHB` / `ANT_CHC`.
* `antenna_model.net` - demo netlist (unit source at the space port).

## Two-port definition

```
 port 1 (feed)  ---[ ANT_CHx ]---  port 2 (radiated wave)
  to RF front-end                   test signal / free space
```

* `S11(f) = (Zin(f) - 50) / (Zin(f) + 50)` - from the FDTD input impedance.
* `S22 = 0` - matched radiation sink.
* `S21 = S12 = sqrt(eta) * sqrt(1 - |S11|^2) * exp(j*arg(S11))`.

Power balance: driving port 1 gives `|S11|^2 + |S21|^2 = eta` (the accepted
power is radiated with efficiency `eta`; the remainder is dielectric and
conductor loss).  On receive, driving port 2 delivers `eta*(1 - |S11|^2)` of
the incident power to the feed.

To emulate a received wave, drive port 2 and read port 1; to load a
transmitter, drive port 1 into the receiver front-end.

## Usage in ngspice

```spice
.include ant_sparams.lib
V_test   space 0 dc 0 ac 1
Xant_chB feed  space 0 ANT_CHB touchstone="ant_chB.s2p"
Rfeed    feed  0 50
.control
sp lin 101 1e9 4e9
print S_1_1 S_2_1
.endc
```

Run the demo: `ngspice -b sim/outputs/ngspice/antenna_model.net`

## Why no FMU

ngspice 44 reads Touchstone natively via the XSPICE `xfer` code model
(see `/usr/share/doc/ngspice/examples/sp/file.cir`).  No FMU wrapper is
needed for this network.

## Notes / limitations

* The model reproduces the simulated feed impedance exactly.
* Radiation efficiency is the FDTD `Prad/P_acc` ratio at resonance.  Pass
  `--eta <x>` to the exporter to override every channel with a fixed value.
* `|S11|` is clipped to `< 1` (passivity) at a handful of far-out-of-band
  points where the raw FDTD impedance gives a marginal `|S11| > 1`.
* The transmission phase is taken as `arg(S11)` (lossless reciprocal 2-port
  convention).  Magnitudes are exact; absolute far-field phase is not
  calibrated (that needs a two-antenna reference measurement).
""")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--eta", type=float, default=None,
                    help="override the per-channel radiation efficiency "
                         "multiplying S21/S12 (0..1); default uses each "
                         "channel's simulated eta_rad")
    ap.add_argument("--validate", action="store_true",
                    help="re-read the exported .s2p with ngspice and compare S11")
    args = ap.parse_args()

    if not RESULTS.exists():
        print(f"missing {RESULTS}", file=sys.stderr)
        return 1

    OUTDIR.mkdir(parents=True, exist_ok=True)
    networks = build_networks(eta=args.eta)
    if not networks:
        print("no channels with Zin data", file=sys.stderr)
        return 1

    for short, label, f, rho, s12, s21, s22, f_res, smin, *_ in networks:
        write_s1p(OUTDIR / f"ant_ch{short.lower()}.s1p", f, rho,
                  f"{label} 1-port (feed reflection), generated from FDTD Zin")
        write_s2p(OUTDIR / f"ant_ch{short.lower()}.s2p", f, rho, s12, s21, s22,
                  f"{label} 2-port: p1=feed p2=space")
        print(f"  {label}: {len(f)} pts -> ant_ch{short.lower()}.s1p/.s2p")

    write_lib(networks, OUTDIR / "ant_sparams.lib")
    write_demo(networks, OUTDIR / "antenna_model.net")
    write_readme(networks, OUTDIR / "README.md")
    print(f"  -> {OUTDIR}/ant_sparams.lib, antenna_model.net, README.md")

    if args.validate:
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            validate(networks, Path(td))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
