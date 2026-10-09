#!/usr/bin/env python3
"""Mesh sizing for openEMS antenna FDTD, with a hard memory budget.

Sizing the mesh correctly takes two independent constraints, and getting
either wrong produces either a silent failure or an OOM:

1. **Geometric**: the air box must fully contain the structure with `lambda/10`
   of clearance on every side. Two failures are possible here and both are
   silent: a sub-wavelength box cannot radiate at all, and a box smaller than
   the element truncates it. A 7 MHz dipole is 20 m long, so its box is ~28 m
   across even though the same `air_mm` value is ample for a 2.45 GHz patch.

2. **Resolution**: the box must contain enough cells for the solver to be
   meaningful. What matters is cells *across the box*, not cells per
   wavelength. A box that is `lambda/10` across contains only
   `cells_per_wavelength / 10` cells, so asking for 20 cells/lambda yields a
   4x4x4 grid -- which runs in milliseconds and returns `nan`.

So the cell size is the *smaller* of two limits:

    cell <= 2*air / cells_across_box      enough cells to resolve the volume
    cell <= lambda / max_cells_per_lambda not coarser than the physics allows

Both limits are checked before anything is allocated, against `max_gb`, so an
over-large model fails in a second with an actionable message instead of
consuming all RAM and being OOM-killed.

Metal and dielectric features are deliberately NOT used to size the mesh. A
patch or wire is placed with `AddEdges2Grid`, which snaps geometry onto mesh
lines and refines locally; a substrate is a material region defined by its z
extents. Sizing the base mesh from the thinnest feature is what made an earlier
revision of this project try to allocate 27 GB to model a 7 MHz dipole.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

C0 = 299792458.0

# Bytes per cell for openEMS (complex E/H plus the compressed operator).
# Measured at ~75 B/cell on this machine; 128 errs high on purpose so the
# budget guard trips before the OOM killer does.
BYTES_PER_CELL = 128


class MeshBudgetError(RuntimeError):
    """Raised when the requested mesh would exceed the memory budget."""


@dataclass
class MeshPlan:
    cell_mm: float
    air_mm: float
    cells: int
    est_gb: float
    cells_across: int
    cells_per_wavelength: float
    lambda_mm: float

    @property
    def grid(self) -> tuple[int, int, int]:
        n = max(int(round(2 * self.air_mm / self.cell_mm)), 1)
        return n, n, n

    def summary(self) -> str:
        nx, ny, nz = self.grid
        return (f"cell={self.cell_mm:.4g} mm  air={self.air_mm:.4g} mm  "
                f"grid={nx}x{ny}x{nz}={self.cells:,}  est={self.est_gb:.2f} GB  "
                f"{self.cells_across} cells/box  "
                f"{self.cells_per_wavelength:.0f} cells/lambda  "
                f"(lambda={self.lambda_mm:.4g} mm)")


def resolve_mesh(*, f0: float,
                 cells_across_box: int = 32,
                 max_cells_per_lambda: float = 100.0,
                 air_mm: float = 60.0,
                 air_lambda_min: float = 0.1,
                 structure_half_extent_mm: float = 0.0,
                 air_clearance_mm: float | None = None,
                 extents_mm: tuple[float, float, float] | None = None,
                 max_gb: float = 3.0) -> MeshPlan:
    """Size the FDTD mesh from the box geometry and the wavelength.

    Args:
        f0: centre frequency, Hz.
        cells_across_box: cells spanning the full air box on each axis. This is
            the primary quality knob. Rough guidance: a large radiating
            structure needs few cells across (16-32); a small patch with fine
            features needs many (100-250). Cell count goes as the cube.
        max_cells_per_lambda: coarse-mesh safety net. The cell is never coarser
            than `lambda / this`. Kept low (100 = a deliberately coarse mesh)
            so it almost never binds and `cells_across_box` stays the real
            control; raise it only if a specific case demands it.
        air_mm: requested air box half-extent, mm. Grown if it is too small to
            hold the structure or is sub-wavelength.
        air_lambda_min: minimum free space around the structure in wavelengths.
            0.1 means lambda/10.
        structure_half_extent_mm: largest half-extent of the modelled structure,
            mm. The box must fully contain it; a dipole is ~lambda/2 long, so
            its box is far larger than the "air_mm" a patch would need.
        air_clearance_mm: free space beyond the structure, mm. Defaults to
            `lambda * air_lambda_min`.
        extents_mm: (dx, dy, dz) structure bounding box, mm. Defaults to a cube
            of side `2*air_mm`.
        max_gb: refuse to build above this many GB.

    Returns:
        MeshPlan describing the chosen mesh.

    Raises:
        ValueError: non-physical arguments.
        MeshBudgetError: the mesh does not fit the budget.
    """
    if f0 <= 0:
        raise ValueError("f0 must be positive")
    if cells_across_box < 4:
        raise ValueError("cells_across_box must be >= 4 to be meaningful")
    if max_cells_per_lambda <= 0:
        raise ValueError("max_cells_per_lambda must be positive")
    if air_mm <= 0:
        raise ValueError("air_mm must be positive")
    if structure_half_extent_mm < 0:
        raise ValueError("structure_half_extent_mm must be >= 0")
    if not 0 < air_lambda_min < 0.5:
        raise ValueError("air_lambda_min must be in (0, 0.5)")
    if max_gb <= 0:
        raise ValueError("max_gb must be positive")

    lambda_mm = C0 / f0 * 1e3
    clearance = (lambda_mm * air_lambda_min if air_clearance_mm is None
                 else float(air_clearance_mm))

    # The box must (a) hold the structure with clearance on every side and
    # (b) not be sub-wavelength, or the element cannot radiate.
    required = float(structure_half_extent_mm) + clearance
    air = max(float(air_mm), required, lambda_mm * air_lambda_min)

    # `cells_across_box` is the primary quality knob; `max_cells_per_lambda` is
    # a safety cap that only bites if someone asks for an absurdly coarse mesh.
    cell_box = 2.0 * air / cells_across_box
    cell_wl = lambda_mm / max_cells_per_lambda
    cell = min(cell_box, cell_wl)

    if extents_mm is None:
        dx = dy = dz = 2.0 * air
    else:
        dx, dy, dz = (float(v) for v in extents_mm)

    n_x = max(int(math.ceil(dx / cell)), 1)
    n_y = max(int(math.ceil(dy / cell)), 1)
    n_z = max(int(math.ceil(dz / cell)), 1)
    cells = n_x * n_y * n_z
    est_gb = cells * BYTES_PER_CELL / 1e9

    if est_gb > max_gb:
        raise MeshBudgetError(
            f"mesh needs ~{est_gb:.1f} GB ({n_x}x{n_y}x{n_z} = {cells:,} cells "
            f"at {cell:.4g} mm); budget is {max_gb:.1f} GB.\n"
            f"  f0={f0 / 1e6:.4g} MHz  lambda={lambda_mm:.4g} mm  "
            f"cells/box={cells_across_box}  air={air:.4g} mm\n"
            f"  Cell count scales as cells_across_box^3, so that knob is the\n"
            f"  cheapest one to turn. Fix one of:\n"
            f"    - lower cells_across_box (halving it cuts RAM by ~8x)\n"
            f"    - shrink air_mm, unless it is already pinned by the structure\n"
            f"      size or the lambda/10 clearance rule\n"
            f"    - raise max_gb only if the machine genuinely has that RAM"
        )

    return MeshPlan(
        cell_mm=cell,
        air_mm=air,
        cells=cells,
        est_gb=est_gb,
        cells_across=max(int(round(2 * air / cell)), 1),
        cells_per_wavelength=lambda_mm / cell,
        lambda_mm=lambda_mm,
    )


def self_test() -> int:
    """Check the sizing rules against the cases that actually bit us."""
    print("mesh self-test\n")

    # --- 1. The original bug: absolute 0.2 mm floor in a 60 mm box at 7 MHz.
    lam7 = C0 / 7.15e6 * 1e3
    bad_cell = min(20.0, 60.0 / 20.0, 0.2)
    bad_cells = int(2 * 60.0 / bad_cell) ** 3
    bad_gb = bad_cells * BYTES_PER_CELL / 1e9
    print(f"  old rule  min(cpw, air/20, min_cell_mm) at 7.15 MHz:")
    print(f"    cell={bad_cell} mm  air=60 mm  cells={bad_cells:,}  est={bad_gb:.1f} GB")
    assert bad_cell == 0.2 and bad_gb > 20, "regression case changed"

    # --- 2. The second bug: 20 cells/lambda over a lambda/10 box is a 4-cell
    #        grid, which returns nan. The grid must actually be populated.
    old = lam7 / 20.0
    old_cells = int(2 * lam7 * 0.1 / old)
    print(f"\n  old rule  cell=lambda/20 over a lambda/10 box at 7.15 MHz:")
    print(f"    cell={old:.0f} mm  air={lam7 / 10:.0f} mm  grid="
          f"{old_cells}^3={old_cells ** 3:,} cells")
    assert old_cells < 8, "the 4-cell failure mode must be reproduced here"

    p = resolve_mesh(f0=7.15e6, cells_across_box=32, air_mm=60.0)
    print(f"\n  7.15 MHz dipole, 32 cells/box:")
    print(f"    {p.summary()}")
    nx, ny, nz = p.grid
    assert p.air_mm >= lam7 * 0.1, "air box must reach lambda/10"
    assert min(nx, ny, nz) >= 20, f"grid must be populated, got {nx}^3"
    assert p.est_gb < 0.2, f"HF model must stay cheap, got {p.est_gb:.3f} GB"

    # --- 3. A sub-wavelength air box must be raised, not accepted.
    lam2450 = C0 / 2.45e9 * 1e3
    p2 = resolve_mesh(f0=2.45e9, cells_across_box=32, air_mm=1.0)
    print(f"\n  2.45 GHz, air_mm=1 (deliberately sub-wavelength):")
    print(f"    {p2.summary()}")
    assert p2.air_mm >= lam2450 * 0.1, "sub-wavelength air box must be raised"

    # --- 4. cell count scales as cells_across_box^3. Compare 64 vs 32, where the
    #        box knob is the binding constraint at both ends.
    a = resolve_mesh(f0=7.15e6, cells_across_box=64, air_mm=60.0, max_gb=1e9)
    b = resolve_mesh(f0=7.15e6, cells_across_box=32, air_mm=60.0, max_gb=1e9)
    ratio = a.cells / b.cells
    print(f"\n  7.15 MHz dipole: 64 cells/box -> {a.cells:,} cells, "
          f"32 cells/box -> {b.cells:,} cells  ({ratio:.1f}x)")
    assert 7.0 < ratio < 9.0, f"expected ~8x, got {ratio:.1f}x"
    assert a.cells_across == 64 and b.cells_across == 32, \
        "box knob must actually set the cell size"

    # --- 5. Budget must refuse before allocating, and be satisfiable.
    try:
        resolve_mesh(f0=7.15e6, cells_across_box=200, air_mm=60.0, max_gb=0.01)
    except MeshBudgetError as exc:
        print("\n  budget guard refused an over-fine mesh:")
        for line in str(exc).splitlines()[:2]:
            print(f"    {line}")
    else:
        raise AssertionError("budget guard did not fire")

    p3 = resolve_mesh(f0=7.15e6, cells_across_box=100, air_mm=60.0, max_gb=1.0)
    print(f"\n  same model at 60 cells/box now fits:\n    {p3.summary()}")
    assert p3.est_gb <= 1.0

    # --- 6. Containment: a 7 MHz dipole is 20 m long, so an air_mm sized for a
    #        small patch silently truncates it. The box must grow to hold the
    #        structure plus clearance.
    half = lam7 / 2 * 0.95
    p4 = resolve_mesh(f0=7.15e6, cells_across_box=48, air_mm=60.0,
                      structure_half_extent_mm=half)
    print(f"\n  7.15 MHz dipole (half-length {half / 1000:.1f} m) with air_mm=60:")
    print(f"    {p4.summary()}")
    assert p4.air_mm >= half + lam7 * 0.1, \
        "box must hold the dipole plus lambda/10 clearance"
    assert p4.air_mm > 13000, f"expected a box far larger than 60 mm, got {p4.air_mm:.0f}"

    # An explicit air_mm larger than the requirement must be honoured, not shrunk.
    p5 = resolve_mesh(f0=7.15e6, cells_across_box=48, air_mm=40000.0,
                      structure_half_extent_mm=half)
    assert p5.air_mm == 40000.0, "explicit air_mm must be honoured"

    # ...and one smaller than the requirement is still grown to fit.
    p6 = resolve_mesh(f0=7.15e6, cells_across_box=48, air_mm=21000.0,
                      structure_half_extent_mm=half)
    assert p6.air_mm > 21000.0, "too-small air_mm must grow to hold the structure"

    # --- 7. Argument validation.
    for bad in (dict(f0=0, cells_across_box=32, air_mm=60),
                dict(f0=1e6, cells_across_box=2, air_mm=60),
                dict(f0=1e6, cells_across_box=32, air_mm=-1),
                dict(f0=1e6, cells_across_box=32, air_mm=60, structure_half_extent_mm=-1),
                dict(f0=1e6, cells_across_box=32, air_mm=60, air_lambda_min=0.9),
                dict(f0=1e6, cells_across_box=32, air_mm=60, max_gb=0)):
        try:
            resolve_mesh(**bad)
        except ValueError:
            continue
        raise AssertionError(f"accepted invalid args: {bad}")
    print("\n  argument validation OK")

    print("\nall mesh self-tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(self_test())