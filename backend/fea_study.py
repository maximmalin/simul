"""FEA structural study wrapper (text-to-cad/fea).

Linear-static first-pass check: CalculiX (ccx) + netgen mesh of quadratic
tetrahedra. Study JSON: fixtures/loads/material/margin/checks.
If ccx/netgen are missing, produce an *analytical* beam estimate and say so
explicitly (never fake a FEM). Units are mm/N/MPa.

Adapted from text-to-cad/skills/fea/SKILL.md + study-file schema (MIT).
"""
from __future__ import annotations
import json, math, shutil
from pathlib import Path

def which_ccx() -> str | None:
    return shutil.which("ccx")

def analytical_cantilever(length_mm, width_mm, thickness_mm, load_N, E_GPa=70.0):
    """Cantilever plate under tip load: deflection delta = F L^3 / (3 E I)."""
    L=length_mm; I=width_mm*thickness_mm**3/12.0  # mm^4
    E=E_GPa*1000.0  # MPa
    delta=load_N*L**3/(3*E*I) if I>0 else float("inf")
    sigma_max=6*load_N*L/(width_mm*thickness_mm**2) if width_mm>0 else float("inf")  # MPa
    return {"deflection_mm": delta, "peak_mpa": sigma_max, "E_GPa":E_GPa}

def run_study(step_path=None, study_path=None, out_dir="out/fea", load_N=500.0, length_mm=50.0, width_mm=20.0, thickness_mm=2.0, yield_MPa=240.0, margin=2.0):
    out=Path(out_dir); out.mkdir(parents=True, exist_ok=True)
    study=json.loads(Path(study_path).read_text()) if study_path and Path(study_path).exists() else {"fixtures":[{"type":"fixed"}],"loads":[{"type":"force","force_N":load_N}],"material":{"E_GPa":70,"yield_MPa":yield_MPa},"margin":margin}
    ccx=which_ccx()
    if ccx:
        report={"solver":"ccx","ccx_path":ccx,"study":study,"step":step_path,"note":"CalculiX solve delegated to text-to-cad cad-server; run `cad step solve` for full FEM. This wrapper reports readiness."}
    else:
        a=analytical_cantilever(length_mm, width_mm, thickness_mm, load_N)
        sf=yield_MPa/a["peak_mpa"] if a["peak_mpa"]>0 else float("inf")
        report={"solver":"analytical-cantilever (ccx missing)","ccx_path":None,"study":study,"step":step_path,
                "deflection_mm":a["deflection_mm"],"peak_mpa":a["peak_mpa"],"safety_factor":sf,"margin_required":margin,
                "pass":sf>=margin,"note":"First-pass engineering check, NOT certification. Vibration/buckling/heat/fatigue not covered."}
    (out/"fea_result.json").write_text(json.dumps(report, indent=2))
    return report

def main() -> int:
    import argparse
    ap=argparse.ArgumentParser(description="FEA structural study (CalculiX wrapper, analytical fallback)")
    ap.add_argument("--step"); ap.add_argument("--study"); ap.add_argument("--out", default="out/fea")
    ap.add_argument("--load-N", type=float, default=500.0)
    a=ap.parse_args()
    r=run_study(a.step, a.study, a.out, a.load_N)
    print(json.dumps(r, indent=2))
    return 0

if __name__=="__main__": raise SystemExit(main())
