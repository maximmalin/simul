#!/usr/bin/env python3
"""summarize_results.py — concise summary from the current elmer_results.json.

Reads sim/outputs/elmer_results.json and prints a short human‑readable report,
including the newly added photoacoustic fields.
"""
import json
from pathlib import Path

RESULTS = Path(__file__).parent.parent / "outputs" / "elmer_results.json"

def main():
    data = json.load(open(RESULTS))
    q = data.get("quartz_layer", {})
    dn = q.get("delta_n", {}).get("mean", "N/A")
    eps = q.get("eps_zz", {}).get("mean", "N/A")
    sigma = q.get("sigma_zz_mean", "N/A")
    disp = data.get("displacement_m", {}).get("max_abs", "N/A")
    pa = data.get("photoacoustic", {})
    pa_mean = pa.get("delta_n_mean", "N/A")
    pa_phase = pa.get("phase_shift_rad", "N/A")
    pa_current = pa.get("equivalent_photocurrent_ua", "N/A")
    pa_note = pa.get("note", "")

    print("=== bragg_sim.py — quick summary ===")
    print(f"n_nodes            : {data.get('n_nodes')}")
    print(f"displacement_max   : {disp:.3e} m")
    print(f"sigma_zz_mean (Q)  : {sigma:.3e} Pa")
    print(f"eps_zz_mean (Q)    : {eps:.3e}")
    print(f"delta_n_mean (Q)   : {dn:.3e}")
    print()
    print("Photoacoustic (mapped from structural scheme):")
    print(f"  delta_n_mean     : {pa_mean:.3e}")
    print(f"  phase_shift_rad  : {pa_phase:.6f} rad")
    print(f"  equiv. photocurrent: {pa_current:.3f} µA")
    print(f"  note             : {pa_note}")

if __name__ == "__main__":
    main()
