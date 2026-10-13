#!/usr/bin/env python3
"""Brig Receiver: far-field radiation patterns (NF2FF) for Ch B & Ch C.

Re-runs the single-element patch FDTD with the tutorial's auto NF2FF recording
box, computes the far-field at the resonant frequency, and saves:

    sim/outputs/openems_patterns.png     - elevation + azimuth + 3D pattern
    sim/outputs/openems_results.json     - merged Dmax / pattern data

Usage:
    python sim/scripts/radiation.py            # full: FDTD + NF2FF
"""
import json, time, sys, traceback, subprocess
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, str(Path(__file__).resolve().parent))
import antenna_sim as A

OUT = A.OUT


def plot_pattern(res, ch_label, f_res, Dmax_dBi):
    """Polar elevation cuts + azimuth cut + normalised 3D surface."""
    th = np.rad2deg(np.asarray(res.theta))      # radians -> deg
    ph = np.rad2deg(np.asarray(res.phi))
    E = np.squeeze(np.asarray(res.E_norm[0]))   # (n_theta, n_phi)
    if E.ndim == 1:
        E = E[:, None]

    i_th90 = int(np.argmin(np.abs(th - 90.0)))
    i_ph0 = int(np.argmin(np.abs(ph - 0.0)))
    i_ph90 = int(np.argmin(np.abs(ph - 90.0)))

    fig = plt.figure(figsize=(16, 5.5))
    fig.suptitle(f"{ch_label}  far-field @ {f_res / 1e9:.3f} GHz  "
                 f"Dmax = {Dmax_dBi:.1f} dBi", fontsize=13, fontweight="bold")

    # elevation cuts, theta in -180..180
    ax1 = fig.add_subplot(1, 3, 1, polar=True)
    e0 = E[:, i_ph0]
    ax1.plot(np.deg2rad(th), e0 / (np.max(e0) + 1e-30), lw=1.6, label="phi=0 (xz)")
    if E.shape[1] > 1:
        e90 = E[:, i_ph90]
        ax1.plot(np.deg2rad(th), e90 / (np.max(e90) + 1e-30), "--", lw=1.6,
                 label="phi=90 (yz)")
    ax1.set_theta_zero_location("N")
    ax1.set_theta_direction(-1)
    ax1.set_title("Elevation cuts")
    ax1.legend(loc="lower right", fontsize=8)

    # azimuth cut at theta=90
    ax2 = fig.add_subplot(1, 3, 2, polar=True)
    eaz = E[i_th90, :]
    ax2.plot(np.deg2rad(ph), eaz / (np.max(eaz) + 1e-30), lw=1.6, color="C1")
    ax2.set_theta_zero_location("N")
    ax2.set_theta_direction(-1)
    ax2.set_title("Azimuth (theta=90)")

    # normalised 3D pattern
    ax3 = fig.add_subplot(1, 3, 3, projection="3d")
    TH, PH = np.meshgrid(np.deg2rad(th), np.deg2rad(ph), indexing="ij")
    s = np.max(E) + 1e-30
    X = E * np.sin(TH) * np.cos(PH)
    Y = E * np.sin(TH) * np.sin(PH)
    Z = E * np.cos(TH)
    surf = ax3.plot_surface(X / s, Y / s, Z / s, cmap="jet", rstride=3,
                            cstride=3, linewidth=0, alpha=0.92)
    ax3.set_title("3D radiation")
    ax3.set_box_aspect((1, 1, 1))
    fig.colorbar(surf, ax=ax3, shrink=0.6, label="E norm (norm.)")

    plt.tight_layout()
    out_png = OUT / "openems_patterns.png"
    plt.savefig(out_png, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  -> {out_png}")
    return out_png


def run_channel(f0, label, dir_name, G_rx_dBi, air, nn_air, reuse=False,
                probe_frac=0.19, L_override=None):
    b = A.build_patch(f0, label, dir_name, air=air, probe_frac=probe_frac,
                      nn_air=nn_air, sub_cells=4, NrTS=40000, EndCriteria=1e-4,
                      clean=not reuse, L_override=L_override)

    # Tutorial-style auto NF2FF box: grid/boundary adapted (grid-aligned).
    nf2ff = b["FDTD"].CreateNF2FFBox()

    if reuse and (Path(b["d"]) / "nf2ff_E_0.h5").exists():
        print("  reusing cached FDTD fields (no re-run)")
    else:
        t0 = time.time()
        print(f"  Running FDTD (mesh_base={b['mesh_base']:.2f}mm)...")
        b["FDTD"].Run(str(b["d"]), cleanup=True, verbose=False)
        print(f"  FDTD done: {time.time() - t0:.0f}s")
    print("  dumps:", sorted(p.name for p in Path(b["d"]).iterdir()
                             if p.suffix == ".h5" or p.name in ("et", "ht")))

    # Restrict the reported resonance to a window around the design frequency
    # (avoids locking onto spurious higher-order nulls).
    s = A.post_s11(b["port"], b["d"], b["f0"], G_rx_dBi=G_rx_dBi, search_frac=0.25)
    f_res = s["f_res"]

    th = np.arange(-180.0, 181.0, 2.0)
    ph = np.arange(0.0, 360.0, 2.0)
    # CalcNF2FF expects the phase centre in METRES (openEMS is SI internally),
    # i.e. geometry coordinate * DeltaUnit.
    center = [0, 0, A.H_SUB * 1e-3]
    print(f"  CalcNF2FF @ {f_res / 1e9:.3f} GHz  grid {len(th)} x {len(ph)} ...")
    res = nf2ff.CalcNF2FF(str(b["d"]), f_res, th, ph, center=center,
                          radius=1.0, read_cached=False, outfile="nf2ff_3d.h5",
                          verbose=1)

    Dmax_lin = float(np.atleast_1d(res.Dmax)[0])
    if Dmax_lin <= 0:
        raise RuntimeError("NF2FF returned zero field (recording box empty)")
    Dmax_dBi = 10 * np.log10(Dmax_lin)
    Prad = float(np.atleast_1d(res.Prad)[0])
    # Physical radiation efficiency.  openEMS normalises both the port DFT and
    # the NF2FF fields by the excitation spectrum, so neither Prad nor P_acc is
    # in physical watts -- but they share the same normalisation and their
    # ratio is the true radiation efficiency (this is exactly the convention
    # used by the openEMS tutorials, e.g. Helical_Antenna.py / Bent_Patch).
    P_acc = float(s.get("P_acc_res", 0.0))
    eta_rad = float(np.clip(Prad / P_acc, 0.0, 1.0)) if P_acc > 0 else 1.0
    eta_nominal = Dmax_lin / (10 ** (G_rx_dBi / 10))
    print(f"  Dmax={Dmax_dBi:.1f} dBi  Prad={Prad:.3e} W  P_acc={P_acc:.3e} W  "
          f"eta_rad={eta_rad * 100:.0f}%  (Dmax/G_nom={eta_nominal:.2f})")

    plot_pattern(res, label, f_res, Dmax_dBi)

    s["patterns"] = {
        "theta_deg": np.rad2deg(np.asarray(res.theta)).tolist(),
        "phi_deg": np.rad2deg(np.asarray(res.phi)).tolist(),
        "E_norm": np.squeeze(np.asarray(res.E_norm[0])).tolist(),
        "Prad_W": Prad,
        "P_acc_W": P_acc,
        "efficiency_rad": eta_rad,
        "Dmax_dBi": Dmax_dBi,
        "efficiency_nominal": float(eta_nominal),
    }
    s["eta_rad"] = eta_rad
    s["geometry"] = {"W_mm": b["W"], "L_mm": b["L"], "eps_eff": b["eps_eff"],
                     "probe_offset_mm": b["feed_pos"], "substrate_mm": b["sub_box"]}
    return s


def main():
    reuse = "--reuse" in sys.argv
    results = {}
    if (OUT / "openems_results.json").exists():
        results = json.load(open(OUT / "openems_results.json"))

    for key, f0, label, dname, G, air, nn, prm, Lmm in [
        ("channel_b", 2.45e9, "Ch B: 2.45 GHz Patch", "openems_b_nf", 6.0, 60.0, 1.2, 0.3131, 28.350),
        ("channel_c", 5.8e9, "Ch C: 5.8 GHz Patch", "openems_c_nf", 12.0, 40.0, 0.8, 0.2258, 11.616),
    ]:
        print(f"\n>> {key} (radiation){' [reuse]' if reuse else ''}")
        t1 = time.time()
        try:
            r = run_channel(f0, label, dname, G, air, nn, reuse=reuse,
                            probe_frac=prm, L_override=Lmm)
            results[key] = r
            results.pop(key + "_pattern_error", None)
            print(f"  OK {time.time() - t1:.0f}s")
        except Exception:
            traceback.print_exc()
            results[key + "_pattern_error"] = traceback.format_exc()[-400:]
        json.dump(results, open(OUT / "openems_results.json", "w"),
                  indent=2, default=str)

    # Regenerate the ngspice-ready S-parameter networks from the fresh results.
    try:
        subprocess.run([sys.executable, str(Path(__file__).with_name("export_ngspice.py"))],
                       check=True)
    except Exception:
        traceback.print_exc()

    print("\nDone.")


if __name__ == "__main__":
    main()
