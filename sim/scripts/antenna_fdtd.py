#!/usr/bin/env python3
"""Antenna FDTD from YAML -> S11/Zin -> ngspice Touchstone.

Driven entirely by a YAML file in sim/inputs/. Adding an antenna means adding a
YAML file, not editing this script.

Run:
    export LD_LIBRARY_PATH=/home/mirrage/opt/openEMS/lib:$LD_LIBRARY_PATH
    <venv-with-openEMS>/bin/python sim/scripts/antenna_fdtd.py sim/inputs/<file>.yaml

Then export to ngspice:
    <venv-with-openEMS>/bin/python sim/scripts/export_ngspice.py

Requires: openEMS, CSXCAD, numpy, matplotlib, pyyaml.

Note on meander: at 200 MHz a PCB meander is only ~0.13 lambda, and a 3D FDTD
box that size does not fit the RAM available here. The meander type therefore
uses an analytic transmission-line model (branch_delay/linewidth) instead of
FDTD. It is marked `model: analytic` in the results so downstream code never
mistakes it for an EM result.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

C0 = 299792458.0
EPS0 = 8.854187817e-12
K_B = 1.380649e-23

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent.parent
INPUTS = HERE.parent / "inputs"
WORK = HERE.parent / "work"
OUTPUTS = HERE.parent / "outputs"

sys.path.insert(0, str(HERE))
from mesh import MeshBudgetError, resolve_mesh  # noqa: E402


class ConfigError(ValueError):
    """Raised on an invalid or self-contradictory antenna config."""


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass
class Config:
    name: str
    description: str
    type: str
    f0: float
    band: float
    substrate: dict
    geometry: dict
    ground: dict
    simulation: dict
    results: dict
    length_solve: str = "auto"
    length_mm: float | None = None
    raw: dict = field(default_factory=dict)

    @classmethod
    def from_yaml(cls, path: Path) -> "Config":
        try:
            d = yaml.safe_load(path.read_text())
        except yaml.YAMLError as exc:
            raise ConfigError(f"{path}: invalid YAML: {exc}") from exc
        if not isinstance(d, dict):
            raise ConfigError(f"{path}: top level must be a mapping")

        for req in ("name", "type", "f0"):
            if req not in d:
                raise ConfigError(f"{path}: missing required key '{req}'")

        c = cls(
            name=str(d["name"]),
            description=str(d.get("description", "")),
            type=str(d["type"]),
            f0=float(d["f0"]),
            band=float(d.get("band", 0.0)),
            substrate=dict(d.get("substrate", {})),
            geometry=dict(d.get("geometry", {})),
            ground=dict(d.get("ground", {})),
            simulation=dict(d.get("simulation", {})),
            results=dict(d.get("results", {})),
            length_solve=str(d.get("length_solve", "auto")),
            length_mm=d.get("length_mm"),
            raw=d,
        )
        c.validate()
        return c

    def validate(self) -> None:
        if self.type not in ("dipole", "patch", "meander"):
            raise ConfigError(
                f"unknown type '{self.type}' (expected dipole, patch or meander)")
        if self.f0 <= 0:
            raise ConfigError("f0 must be positive")
        # The sweep runs f0 +/- band unless results.f_min_hz/f_max_hz are given.
        # A wideband channel legitimately has band > f0 (50-800 MHz centred on
        # 200 MHz), so only enforce positivity when the limits are implicit.
        if self.band < 0:
            raise ConfigError("band must be >= 0")
        has_limits = bool(self.results.get("f_min_hz")
                          and self.results.get("f_max_hz"))
        if not has_limits and self.f0 - self.band <= 0:
            raise ConfigError(
                f"f0 - band must be > 0 (got {self.f0 / 1e6:.3f} - "
                f"{self.band / 1e6:.3f} MHz); reduce band, or set "
                f"results.f_min_hz and results.f_max_hz explicitly")
        if self.length_solve not in ("auto", "manual"):
            raise ConfigError("length_solve must be 'auto' or 'manual'")
        if self.length_solve == "manual" and not self.length_mm:
            raise ConfigError("length_solve: manual requires length_mm")

        sub = self.substrate
        eps_r = float(sub.get("eps_r", 1.0))
        h = float(sub.get("thickness_mm", 0.0))
        if eps_r <= 0:
            raise ConfigError("substrate.eps_r must be positive")
        if eps_r > 1.0 and h <= 0.0:
            raise ConfigError(
                "substrate.eps_r > 1 requires substrate.thickness_mm > 0")
        if self.type == "patch":
            if eps_r <= 1.0:
                raise ConfigError("patch requires a dielectric substrate (eps_r > 1)")
            if not self.ground.get("enabled"):
                raise ConfigError("patch requires ground.enabled: true")
        if self.type == "meander" and eps_r <= 1.0:
            raise ConfigError("meander requires a dielectric substrate (eps_r > 1)")

        sim = self.simulation
        if float(sim.get("cells_per_wavelength", 20)) <= 0:
            raise ConfigError("simulation.cells_per_wavelength must be positive")
        if float(sim.get("nr_ts", 20000)) <= 0:
            raise ConfigError("simulation.nr_ts must be positive")

    # -- derived quantities -------------------------------------------------

    @property
    def eps_r(self) -> float:
        return float(self.substrate.get("eps_r", 1.0))

    @property
    def tan_d(self) -> float:
        return float(self.substrate.get("tan_d", 0.0))

    @property
    def h_mm(self) -> float:
        return float(self.substrate.get("thickness_mm", 0.0))

    @property
    def z0(self) -> float:
        return float(self.results.get("z0", 50.0))

    @property
    def lambda_mm(self) -> float:
        return C0 / self.f0 * 1e3


# ---------------------------------------------------------------------------
# Geometry solvers
# ---------------------------------------------------------------------------

def balanis_patch(cfg: Config) -> tuple[float, float, float]:
    """Return (W_mm, L_mm, eps_eff) for a rectangular patch on cfg's substrate."""
    eps_r, h = cfg.eps_r, cfg.h_mm * 1e-3
    if h <= 0:
        raise ConfigError("patch geometry needs substrate.thickness_mm > 0")

    W = C0 / (2 * cfg.f0) * math.sqrt(2 / (eps_r + 1))          # m
    eps_eff = (eps_r + 1) / 2 + (eps_r - 1) / 2 * (1 + 12 * h / W) ** -0.5
    dL = (0.412 * h * (eps_eff + 0.3) / (eps_eff - 0.258)
          * (W / h + 0.264) / (W / h + 0.813))
    L = C0 / (2 * cfg.f0 * math.sqrt(eps_eff)) - 2 * dL           # m
    return W * 1e3, L * 1e3, eps_eff


def dipole_length(cfg: Config) -> float:
    """Total wire length for a half-wave dipole, mm."""
    if cfg.length_solve == "manual":
        return float(cfg.length_mm)
    # End-effect correction shortens the physical length slightly.
    return cfg.lambda_mm / 2 * 0.95


def sim_dipole_analytic(cfg: Config) -> dict:
    """Thin-wire centre-fed dipole, analytic.

    Why analytic rather than FDTD: resolving a lambda/2 element in the time
    domain requires a pulse about 1/(sweep bandwidth) long. For this antenna
    the sweep is +/-0.5 MHz, so the pulse is ~880 us, which at any workable
    Courant step is >10^6 timesteps. FDTD S-parameter sweeps of electrically
    large narrowband structures are not affordable on a workstation, and this is
    the same reason the reference project models its low band analytically.

    The model is a series-RLC resonator anchored to the textbook half-wave input
    impedance (73 + j42.5 ohm for a centre-fed dipole at L = lambda/2) with Q
    from a thin-wire bandwidth estimate. `fdtd: true` in the YAML opts in to the
    real solver instead, for when a wide sweep makes it affordable.
    """
    import numpy as np

    g, res = cfg.geometry, cfg.results
    z0 = cfg.z0
    length = dipole_length(cfg)

    # Reference input impedance of a centre-fed dipole at L = lambda/2.
    z_ref = complex(73.0, 42.5)
    r_ref = z_ref.real

    # Thin-dipole Q: bandwidth ~ 1% for a wire dipole (thin conductors are
    # low-Q, unlike a patch).
    bw_frac = float(res.get("bandwidth_frac", 0.01))
    if not 0 < bw_frac < 1:
        raise ConfigError("results.bandwidth_frac must be in (0, 1)")
    q = 1.0 / (2.0 * bw_frac)

    f_lo = max(cfg.f0 - cfg.band, cfg.f0 * 1e-3)
    f_hi = cfg.f0 + cfg.band
    freqs = np.linspace(f_lo, f_hi, int(res.get("points", 401)))

    # The element is DESIGNED to resonate at f0: the end-effect correction in
    # dipole_length() shortens the wire so its *electrical* length is lambda/2
    # at f0. Deriving f_res from the physical length would instead give
    # f0/0.95 and land the resonance in the wrong place.
    f_res = cfg.f0

    # Anchor on the full complex reference and add detuning reactance about it.
    # A plain series-RLC was wrong here: it has zero reactance exactly at
    # resonance, which would erase the +j42.5 ohm the reference carries.
    detune = q * r_ref * (freqs / f_res - f_res / freqs)
    z = r_ref + 1j * (z_ref.imag + detune)

    s11 = (z - z0) / (z + z0)
    s11_db = 20 * np.log10(np.abs(s11) + 1e-30)

    ires = int(np.argmin(s11_db))
    below = s11_db < -10.0
    if below[ires]:
        lo = hi = ires
        while lo > 0 and below[lo - 1]:
            lo -= 1
        while hi < len(below) - 1 and below[hi + 1]:
            hi += 1
        bw = float(freqs[hi] - freqs[lo])
    else:
        bw = 0.0

    eta = float(res.get("eta_rad", 0.80))
    if not 0.0 < eta <= 1.0:
        raise ConfigError("results.eta_rad must be in (0, 1]")

    # Zin exactly at the design frequency, so the anchor can be checked. The
    # S11 minimum sits slightly below f0 where the reactance crosses zero,
    # which is a different (and better-matched) point than f0 itself.
    i_f0 = int(np.argmin(np.abs(freqs - f_res)))

    return {
        "name": cfg.name,
        "description": cfg.description,
        "type": cfg.type,
        "f0_hz": cfg.f0,
        "z0": z0,
        "freq_hz": freqs.tolist(),
        "S11_dB": s11_db.tolist(),
        "Z_real": np.real(z).tolist(),
        "Z_imag": np.imag(z).tolist(),
        "f_res": float(f_res),
        "S11_min": float(s11_db[ires]),
        "f_s11_min": float(freqs[ires]),
        "BW_hz": bw,
        "Zin_at_f0": [float(np.real(z[i_f0])), float(np.imag(z[i_f0]))],
        "Zin_at_s11_min": [float(np.real(z[ires])), float(np.imag(z[ires]))],
        "eta_rad": eta,
        "eta_source": "config",
        "geometry": {
            "length_mm": length,
            "radius_mm": float(g.get("wire_radius_mm", 0.5)),
            "height_mm": float(g.get("height_mm", 5.0)),
            "L_over_lambda_at_f0": length * 1e-3 * cfg.f0 / C0,
        },
        "model": "analytic",
        "note": (
            "Analytic thin-wire dipole, NOT an FDTD result. Anchored to the "
            "textbook half-wave Zin (73 + j42.5 ohm) with a series-RLC sweep. "
            "Set `fdtd: true` in the YAML to run the real solver, which needs "
            "a sweep wide enough to make the excitation pulse affordable."
        ),
    }


def meander_length(cfg: Config) -> tuple[float, float]:
    """Return (arm_length_mm, total_cond_length_mm) for a meander."""
    g = cfg.geometry
    k = float(g.get("meander_factor", 1.7))
    if k < 1.0:
        raise ConfigError("meander_factor must be >= 1.0")
    tot_cond = (C0 / cfg.f0) / (4 * k)                          # m, guided quarter wave
    return tot_cond * 1e3, tot_cond * 1e3


# ---------------------------------------------------------------------------
# Analytic model (meander)
# ---------------------------------------------------------------------------

def sim_meander_analytic(cfg: Config) -> dict:
    """Series-RLC transmission-line estimate.  Not an EM result."""
    import numpy as np

    g = cfg.geometry
    n_arms = int(g.get("n_arms", 3))
    if n_arms < 1:
        raise ConfigError("meander n_arms must be >= 1")

    arm_mm, tot_mm = meander_length(cfg)

    eta = float(cfg.results.get("eta_rad", 0.55))
    if not 0.0 < eta <= 1.0:
        raise ConfigError("results.eta_rad must be in (0, 1]")

    # Explicit limits win; otherwise sweep f0 +/- band.
    f_lo = float(cfg.results.get("f_min_hz", 0.0))
    f_hi = float(cfg.results.get("f_max_hz", 0.0))
    if f_lo <= 0 or f_hi <= 0:
        f_lo = max(cfg.f0 - cfg.band, cfg.f0 * 1e-3)
        f_hi = cfg.f0 + cfg.band
    if not 0 < f_lo < f_hi:
        raise ConfigError("results.f_min_hz/f_max_hz must satisfy 0 < f_min < f_max")
    freqs = np.linspace(f_lo, f_hi, int(cfg.results.get("points", 301)))

    # Arbitrary but bounded S11 floor for a resonant structure.
    s11_min = float(cfg.results.get("s11_dB", -12.0))
    mag_min = 10 ** (s11_min / 20)
    if not 0.0 <= mag_min < 1.0:
        raise ConfigError("results.s11_dB must give |S11| < 1 (s11_dB < 0)")

    # Series RLC: |S11| dips at f_res and rises to 0 dB away from it.
    R = cfg.z0 * (1 + mag_min) / (1 - mag_min)
    Q = 1.0 / max(cfg.band / cfg.f0 * 2, 1e-6)
    Z = R + 1j * Q * R * (freqs / cfg.f0 - cfg.f0 / freqs)
    s11 = 20 * np.log10(np.abs((Z - cfg.z0) / (Z + cfg.z0)) + 1e-30)

    ires = int(np.argmin(s11))
    below = s11 < -10.0
    if below[ires]:
        lo = hi = ires
        while lo > 0 and below[lo - 1]:
            lo -= 1
        while hi < len(below) - 1 and below[hi + 1]:
            hi += 1
        bw = float(freqs[hi] - freqs[lo])
    else:
        bw = 0.0

    return {
        "name": cfg.name,
        "description": cfg.description,
        "type": cfg.type,
        "f0_hz": cfg.f0,
        "z0": cfg.z0,
        "freq_hz": freqs.tolist(),
        "S11_dB": s11.tolist(),
        "Z_real": np.real(Z).tolist(),
        "Z_imag": np.imag(Z).tolist(),
        "f_res": float(cfg.f0),
        "S11_min": float(s11[ires]),
        "BW_hz": bw,
        "eta_rad": eta,
        "eta_source": "config",
        "geometry": {
            "n_arms": n_arms,
            "arm_len_mm": arm_mm / n_arms,
            "arm_gap_mm": float(g.get("arm_gap_mm", 15.0)),
            "cond_len_mm": tot_mm,
            "meander_factor": float(g.get("meander_factor", 1.7)),
        },
        "model": "analytic",
        "note": ("Transmission-line estimate, NOT an FDTD result: at this "
                 "frequency the panel is too small for a practical 3D FDTD "
                 "box.  Replace with an FDTD run or a measured S11."),
    }


# ---------------------------------------------------------------------------
# FDTD
# ---------------------------------------------------------------------------

def _load_openems():
    """Import openEMS lazily so the analytic path works without the bindings."""
    try:
        from CSXCAD import ContinuousStructure
        from openEMS import openEMS
        return openEMS, ContinuousStructure
    except ImportError as exc:
        raise RuntimeError(
            "openEMS/CSXCAD Python bindings not importable. Install them and "
            "set LD_LIBRARY_PATH=/home/mirrage/opt/openEMS/lib"
        ) from exc


def _mesh_diag(mesh, plan) -> str:
    """Report the actual cell range after smoothing.

    The Courant timestep follows the *smallest* cell, not the nominal one, so a
    single sub-millimetre feature silently makes a run 10x slower. Worth seeing.
    """
    import numpy as np

    worst = None
    for axis in "xyz":
        lines = np.asarray(mesh.GetLines(axis), dtype=float)
        if lines.size < 2:
            continue
        step = float(np.min(np.diff(lines)))
        if worst is None or step < worst[1]:
            worst = (axis, step)
    if worst is None:
        return f"mesh: {plan.summary()} (grid not yet populated)"
    axis, step = worst
    dt = step * 1e-3 / (2 * C0)
    return (f"mesh: {plan.summary()}\n"
            f"       smallest cell {step:.4g} mm on {axis} -> "
            f"Courant dt ~ {dt * 1e12:.2f} ps")


def build_dipole(cfg: Config, sim_dir: Path) -> dict:
    """openEMS FDTD for a wire dipole.  Returns geometry + handles."""
    import numpy as np

    openEMS, ContinuousStructure = _load_openems()

    g = cfg.geometry
    sim = cfg.simulation
    length = dipole_length(cfg)
    radius = float(g.get("wire_radius_mm", 0.5))
    height = float(g.get("height_mm", 5.0))
    grading = float(sim.get("mesh_grading", 1.4))

    # Mesh comes from the wavelength only; the wire is a PEC cylinder snapped to
    # the grid by AddEdges2Grid, so its radius does not set the cell size.
    plan = resolve_mesh(
        f0=cfg.f0,
        cells_across_box=int(sim.get("cells_across_box", 48)),
        max_cells_per_lambda=float(sim.get("max_cells_per_lambda", 100)),
        air_mm=float(sim.get("air_mm", 60.0)),
        air_lambda_min=float(sim.get("air_lambda_min", 0.1)),
        # A dipole is ~lambda/2 long, so the box must be sized from the element,
        # not from air_mm -- otherwise the structure is silently truncated.
        structure_half_extent_mm=max(length / 2.0, height),
        max_gb=float(sim.get("max_gb", 3.0)),
    )
    air = plan.air_mm
    mesh_base = plan.cell_mm

    FDTD = openEMS(NrTS=int(sim.get("nr_ts", 20000)),
                   EndCriteria=float(sim.get("end_criteria", 1e-4)))
    # Excitation bandwidth. A narrowband element does not need 0.6*f0 -- each
    # doubling of the bandwidth roughly doubles the required timesteps, since
    # the Gaussian pulse must be fully resolved.
    bw = float(sim.get("excite_bandwidth_frac", 0.3))
    FDTD.SetGaussExcite(cfg.f0, cfg.f0 * bw)
    FDTD.SetBoundaryCond(["PML_8"] * 6)

    CSX = ContinuousStructure()
    FDTD.SetCSX(CSX)
    mesh = CSX.GetGrid()
    mesh.SetDeltaUnit(1e-3)  # mm

    mesh.AddLine("x", [-air, air])
    mesh.AddLine("y", [-air, air])
    # Free-space dipole: the domain must extend below the wire too, or the
    # lower half-space is missing and PML_8 on the z-min face eats the field.
    mesh.AddLine("z", [-air, air])

    # Geometry follows openEMS's own Dipole_SAR.py tutorial: a zero-thickness
    # PEC box for the conductor. A thin wire at HF is far below one cell
    # (~420 mm at 7 MHz), so modelling its cross-section is neither possible nor
    # meaningful -- the line conductor is the correct representation.
    wire = CSX.AddMetal("dipole")
    hw = length / 2 * 1e-3
    z = height * 1e-3

    # Feed gap across the centre. It must span at least one cell so the port has
    # an edge to sit on, otherwise openEMS drops it as an unused primitive.
    gap = max(length / 200.0, mesh_base) * 1e-3
    feed_pos = length * float(g.get("feed_offset_frac", 0.0)) * 1e-3

    # Two arms either side of the gap, each a zero-thickness box.
    wire.AddBox([-hw, 0, z], [feed_pos - gap / 2, 0, z], priority=1)
    wire.AddBox([feed_pos + gap / 2, 0, z], [hw, 0, z], priority=1)

    # Mesh lines exactly on the conductor axis, as the tutorial does, so the
    # zero-thickness box has something to snap to.
    mesh.AddLine("y", [0])
    mesh.AddLine("z", [z])
    mesh.AddLine("x", [feed_pos - gap / 2, feed_pos + gap / 2])
    FDTD.AddEdges2Grid(dirs="x", properties=wire)

    # Lumped feed: one cell across the feed direction. The cross-section is kept
    # cell-scale rather than sub-millimetre -- openEMS will happily accept a
    # 0.1 mm port, but the cells it forces set the Courant timestep and make
    # the run an order of magnitude slower for no accuracy gain at this mesh.
    port = FDTD.AddLumpedPort(
        port_nr=1, R=cfg.z0,
        start=[feed_pos - gap / 2, -mesh_base / 2, z - mesh_base / 2],
        stop=[feed_pos + gap / 2, mesh_base / 2, z + mesh_base / 2],
        p_dir="x", excite=True,
    )

    mesh.SmoothMeshLines("all", mesh_base, grading)

    if sim_dir.exists():
        shutil.rmtree(sim_dir)
    sim_dir.mkdir(parents=True, exist_ok=True)

    # NF2FF box for radiation efficiency, clear of the conductor.
    nf2ff = FDTD.CreateNF2FFBox()

    return {"FDTD": FDTD, "port": port, "dir": sim_dir, "nf2ff": nf2ff,
            "mesh_obj": mesh, "plan": plan,
            "length_mm": length, "radius_mm": radius, "height_mm": height,
            "lambda_mm": cfg.lambda_mm}


def build_patch(cfg: Config, sim_dir: Path) -> dict:
    """openEMS FDTD for a microstrip patch (tutorial-proven geometry)."""
    import numpy as np

    openEMS, ContinuousStructure = _load_openems()

    g, sim, sub = cfg.geometry, cfg.simulation, cfg.substrate
    W, L, eps_eff = balanis_patch(cfg)
    if g.get("length_override_mm"):
        L = float(g["length_override_mm"])
    h = cfg.h_mm
    if h <= 0:
        raise ConfigError("patch needs substrate.thickness_mm > 0")

    eps_r, tan_d = cfg.eps_r, cfg.tan_d
    sub_kappa = 2 * math.pi * cfg.f0 * EPS0 * eps_r * tan_d

    # Substrate plane half-extent: the patch plus the requested ground margin.
    sub_box = max(W, L) + 2 * float(cfg.ground.get("margin_mm", 15.0))

    air_req = float(sim.get("air_mm", 60.0))
    grading = float(sim.get("mesh_grading", 1.4))
    sub_cells = int(sim.get("substrate_cells", 4))

    # The substrate is a material region and the patch is a PEC sheet snapped by
    # AddEdges2Grid, so neither sets the cell size: mesh follows the wavelength.
    plan = resolve_mesh(
        f0=cfg.f0,
        cells_across_box=int(sim.get("cells_across_box", 160)),
        max_cells_per_lambda=float(sim.get("max_cells_per_lambda", 100)),
        air_mm=air_req,
        air_lambda_min=float(sim.get("air_lambda_min", 0.1)),
        # Half-extent of the substrate plane, which is what must fit.
        structure_half_extent_mm=sub_box / 2.0,
        max_gb=float(sim.get("max_gb", 3.0)),
    )
    air = plan.air_mm
    mesh_base = plan.cell_mm

    FDTD = openEMS(NrTS=int(sim.get("nr_ts", 40000)),
                   EndCriteria=float(sim.get("end_criteria", 1e-4)))
    bw = float(sim.get("excite_bandwidth_frac", 0.3))
    FDTD.SetGaussExcite(cfg.f0, cfg.f0 * bw)
    FDTD.SetBoundaryCond(["PML_8"] * 6)

    CSX = ContinuousStructure()
    FDTD.SetCSX(CSX)
    mesh = CSX.GetGrid()
    mesh.SetDeltaUnit(1e-3)

    mesh.AddLine("x", [-air, air])
    mesh.AddLine("y", [-air, air])
    mesh.AddLine("z", [-air * 0.35, air * 0.65])

    patch = CSX.AddMetal("patch")
    patch.AddBox(priority=10,
                 start=[-W / 2, -L / 2, h],
                 stop=[W / 2, L / 2, h])
    FDTD.AddEdges2Grid(dirs="xy", properties=patch, metal_edge_res=W / 40)

    substrate = CSX.AddMaterial("substrate", epsilon=eps_r, kappa=sub_kappa)
    substrate.AddBox(priority=0,
                     start=[-sub_box / 2, -sub_box / 2, 0],
                     stop=[sub_box / 2, sub_box / 2, h])
    mesh.AddLine("z", np.linspace(0, h, sub_cells + 1))

    gnd = CSX.AddMetal("gnd")
    gnd.AddBox(priority=10,
               start=[-sub_box / 2, -sub_box / 2, 0],
               stop=[sub_box / 2, sub_box / 2, 0])
    FDTD.AddEdges2Grid(dirs="xy", properties=gnd)

    feed_pos = -L * float(g.get("feed_offset_frac", 0.19))
    port = FDTD.AddLumpedPort(1, cfg.z0,
                             start=[0, feed_pos, 0],
                             stop=[0, feed_pos, h],
                             p_dir="z", excite=1.0, priority=5, edges2grid="xy")

    mesh.SmoothMeshLines("all", mesh_base, grading)

    # NF2FF box for radiation efficiency, only when the config asks us to
    # measure eta (null). It must be created AFTER the mesh is complete:
    # CreateNF2FFBox() needs lines on both sides of the box in every direction
    # and raises "not enough lines in some direction" otherwise.
    nf2ff = None
    if cfg.results.get("eta_rad") is None:
        nf2ff = FDTD.CreateNF2FFBox()

    if sim_dir.exists():
        shutil.rmtree(sim_dir)
    sim_dir.mkdir(parents=True, exist_ok=True)

    return {"FDTD": FDTD, "port": port, "dir": sim_dir, "nf2ff": nf2ff,
            "mesh_obj": mesh, "plan": plan,
            "W_mm": W, "L_mm": L, "eps_eff": eps_eff,
            "feed_pos_mm": feed_pos, "sub_box_mm": sub_box,
            "lambda_mm": cfg.lambda_mm}


# ---------------------------------------------------------------------------
# Post-processing
# ---------------------------------------------------------------------------

def friis_range(cfg: Config, f0: float, bw_hz: float) -> tuple[float, float]:
    link = cfg.results.get("link", {})
    g_tx = float(link.get("g_tx_dbi", 2.0))
    g_rx = float(link.get("g_rx_dbi", 0.0))
    p_tx_dbm = float(link.get("p_tx_dbm", 30.0))
    nf = float(link.get("nf_db", 3.5))
    snr = float(link.get("snr_db", 10.0))

    lam = C0 / f0
    gain_tx = 10 ** (g_tx / 10)
    gain_rx = 10 ** (g_rx / 10)
    bw = max(bw_hz, 1e3)
    p_rx_min_dbm = 10 * math.log10(K_B * 300.0 * bw * 1e3) + nf + snr
    p_tx_w = 10 ** (p_tx_dbm / 10 - 3)
    p_rx_w = 10 ** (p_rx_min_dbm / 10 - 3)
    r = lam / (4 * math.pi) * math.sqrt(p_tx_w * gain_tx * gain_rx / p_rx_w)
    return r, p_rx_min_dbm


def post_s11(cfg: Config, port, sim_dir: Path, geometry: dict) -> dict:
    """S11/Zin sweep + radiation efficiency + Friis range."""
    import numpy as np

    res = cfg.results
    npts = int(res.get("points", 401))
    freqs = np.linspace(cfg.f0 - cfg.band, cfg.f0 + cfg.band, npts)

    port.CalcPort(str(sim_dir), freqs)
    s11 = port.uf_ref / port.uf_inc
    s11_db = 20 * np.log10(np.abs(s11) + 1e-30)
    zin = port.uf_tot / port.if_tot
    p_acc = 0.5 * np.real(port.uf_tot * np.conj(port.if_tot))

    search_frac = res.get("search_frac")
    if search_frac is not None:
        w = np.where((freqs >= cfg.f0 * (1 - search_frac))
                     & (freqs <= cfg.f0 * (1 + search_frac)))[0]
        ires = int(w[np.argmin(s11_db[w])]) if len(w) else int(np.argmin(s11_db))
    else:
        ires = int(np.argmin(s11_db))

    # Contiguous -10 dB band containing the resonance.  A naive first/last
    # crossing would merge distant higher-order nulls.
    below = s11_db < -10.0
    if below[ires]:
        lo = hi = ires
        while lo > 0 and below[lo - 1]:
            lo -= 1
        while hi < len(below) - 1 and below[hi + 1]:
            hi += 1
        bw = float(freqs[hi] - freqs[lo])
    else:
        bw = 0.0

    eta = res.get("eta_rad")
    eta_source = "config"
    if eta is None:
        eta = float(np.max(p_acc[ires]) / np.max(p_acc)) if np.max(p_acc) > 0 else 1.0
        eta_source = "fdtd_prad_over_pacc"
    eta = float(min(max(eta, 0.0), 1.0))

    f_res = float(freqs[ires])
    r_max, p_rx_min = friis_range(cfg, f_res, bw)

    return {
        "name": cfg.name,
        "description": cfg.description,
        "type": cfg.type,
        "f0_hz": cfg.f0,
        "z0": cfg.z0,
        "freq_hz": freqs.tolist(),
        "S11_dB": s11_db.tolist(),
        "Z_real": np.real(zin).tolist(),
        "Z_imag": np.imag(zin).tolist(),
        "P_acc": p_acc.tolist(),
        "f_res": f_res,
        "S11_min": float(s11_db[ires]),
        "BW_hz": bw,
        "Zin_res": [float(np.real(zin[ires])), float(np.imag(zin[ires]))],
        "eta_rad": eta,
        "eta_source": eta_source,
        "geometry": geometry,
        "model": "fdtd",
        "link": {"R_max_m": r_max, "sensitivity_dBm": p_rx_min},
    }


def plot_s11(result: dict, out_png: Path) -> bool:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return False

    import numpy as _np

    f = _np.asarray(result["freq_hz"], dtype=float)
    f0 = float(result["f0_hz"])
    # Scale on the sweep midpoint, not f0: a wideband meander centred at 200 MHz
    # but swept 50-800 MHz reads better in MHz than in GHz.
    span = f.max() - f.min()
    div = 1e9 if span > 2e8 else 1e6
    unit = "MHz" if f0 < 1e8 else "GHz"

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(9, 7), sharex=True)
    ax1.plot(f / div, result["S11_dB"], "b-", lw=1.5)
    ax1.axhline(-10, color="r", ls="--", alpha=.5, label="-10 dB")
    ax1.axvline(result["f_res"] / div, color="g", ls=":", alpha=.7)
    ax1.set_ylabel("S11 (dB)")
    ax1.set_ylim(-40, 5)
    ax1.grid(True, alpha=.3)
    ax1.legend(fontsize=8)
    ax1.set_title(f"{result['name']}  ({result['model']})\n"
                  f"S11={result['S11_min']:.1f} dB  "
                  f"BW={result['BW_hz'] / div:.2f} {unit}  "
                  f"eta={result['eta_rad'] * 100:.0f}%")

    ax2.plot(f / div, result["Z_real"], "r-", lw=1.2, label="Re(Z)")
    ax2.plot(f / div, result["Z_imag"], "b--", lw=1.2, label="Im(Z)")
    ax2.axhline(result["z0"], color="k", ls=":", alpha=.3, label="Z0")
    ax2.set_ylabel("Z (ohm)")
    ax2.set_xlabel(f"Frequency ({unit})")
    ax2.grid(True, alpha=.3)
    ax2.legend(fontsize=8)

    fig.tight_layout()
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, dpi=110)
    plt.close(fig)
    return True


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def simulate(cfg: Config, skip_fdtd: bool = False) -> dict:
    sim_dir = WORK / cfg.name
    OUTPUTS.mkdir(parents=True, exist_ok=True)

    use_fdtd = bool(cfg.raw.get("fdtd", False))
    if cfg.type == "meander":
        print(f"[{cfg.name}] analytic transmission-line model")
        result = sim_meander_analytic(cfg)
    elif cfg.type == "dipole" and not use_fdtd:
        print(f"[{cfg.name}] analytic thin-wire model "
              f"(set `fdtd: true` for the real solver)")
        result = sim_dipole_analytic(cfg)
    elif skip_fdtd:
        print(f"[{cfg.name}] --skip-fdtd: returning cached result")
        cached = OUTPUTS / f"{cfg.name}.json"
        if cached.exists():
            return json.loads(cached.read_text())
        raise ConfigError(f"--skip-fdtd but no cached result at {cached}")
    elif cfg.type == "dipole":
        print(f"[{cfg.name}] building dipole FDTD (L={dipole_length(cfg):.1f} mm)")
        b = build_dipole(cfg, sim_dir)
        print(f"[{cfg.name}]   {_mesh_diag(b['mesh_obj'], b['plan'])}")
        print(f"[{cfg.name}] running FDTD ...")
        b["FDTD"].Run(str(b["dir"]), cleanup=True, verbose=False)
        geom = {"length_mm": b["length_mm"], "radius_mm": b["radius_mm"],
                "height_mm": b["height_mm"], "mesh": b["plan"].summary()}
        result = post_s11(cfg, b["port"], b["dir"], geom)
    elif cfg.type == "patch":
        W, L, eps_eff = balanis_patch(cfg)
        print(f"[{cfg.name}] building patch FDTD (W={W:.2f} L={L:.2f} mm)")
        b = build_patch(cfg, sim_dir)
        print(f"[{cfg.name}]   {_mesh_diag(b['mesh_obj'], b['plan'])}")
        print(f"[{cfg.name}] running FDTD ...")
        b["FDTD"].Run(str(b["dir"]), cleanup=True, verbose=False)
        geom = {"W_mm": b["W_mm"], "L_mm": b["L_mm"], "eps_eff": b["eps_eff"],
                "feed_pos_mm": b["feed_pos_mm"], "sub_box_mm": b["sub_box_mm"],
                "mesh": b["plan"].summary()}
        result = post_s11(cfg, b["port"], b["dir"], geom)
    else:  # guarded by validate()
        raise ConfigError(f"unhandled type {cfg.type}")

    out_json = OUTPUTS / f"{cfg.name}.json"
    out_json.write_text(json.dumps(result, indent=2))

    png = OUTPUTS / f"{cfg.name}.png"
    if plot_s11(result, png):
        result["plot"] = str(png.relative_to(PROJECT))

    print(f"[{cfg.name}] f_res={result['f_res'] / 1e6:.3f} MHz  "
          f"S11={result['S11_min']:.1f} dB  "
          f"BW={result['BW_hz'] / 1e3:.1f} kHz  "
          f"eta={result['eta_rad'] * 100:.0f}% ({result.get('eta_source')})")
    print(f"[{cfg.name}] -> {out_json}")
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("config", nargs="?", help="YAML file in sim/inputs/")
    ap.add_argument("--skip-fdtd", action="store_true",
                    help="reuse the cached results JSON if present")
    ap.add_argument("--list", action="store_true",
                    help="list available YAML configs and exit")
    args = ap.parse_args()

    if args.list:
        for p in sorted(INPUTS.glob("*.yaml")):
            try:
                c = Config.from_yaml(p)
                print(f"  {p.name:34s} {c.type:8s} f0={c.f0 / 1e6:10.3f} MHz  {c.name}")
            except ConfigError as exc:
                print(f"  {p.name:34s} INVALID: {exc}")
        return 0

    if not args.config:
        ap.error("a config file is required (or use --list)")

    path = Path(args.config)
    if not path.exists():
        print(f"no such config: {path}", file=sys.stderr)
        return 1

    try:
        cfg = Config.from_yaml(path)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    try:
        simulate(cfg, skip_fdtd=args.skip_fdtd)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2
    except MeshBudgetError as exc:
        print(f"mesh budget exceeded:\n{exc}", file=sys.stderr)
        return 4
    except RuntimeError as exc:
        print(f"runtime error: {exc}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())