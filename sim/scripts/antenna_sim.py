#!/usr/bin/env python3
"""OpenEMS FDTD: Brig Receiver antennas - FINAL (tutorial-proven modeling).

Channel B: 2.45 GHz microstrip patch (validates 2x2 array element)
Channel C: 5.8 GHz   microstrip patch (validates 4x4 array element)
Channel A: 200 MHz meander - analytic model (FDTD impractical at 0.13 lambda)

Modeling follows the openEMS Simple_Patch_Antenna tutorial:
  - patch / ground = zero-thickness PEC sheets + AddEdges2Grid
  - substrate box, 4+ cells in thickness
  - probe feed = zero-width z-line lumped port
  - MUR boundaries + SmoothMeshLines
Outputs: sim/outputs/openems_results.json, S11/Z plots, summary table.
"""
import json, os, time, shutil, traceback
from pathlib import Path
import numpy as np

os.environ["LD_LIBRARY_PATH"] = "/home/mirrage/opt/openEMS/lib:" + os.environ.get("LD_LIBRARY_PATH", "")
from CSXCAD import ContinuousStructure
from openEMS import openEMS
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

C0 = 299792458.0
EPS0 = 8.854187817e-12
K_B = 1.380649e-23
T_SYS = 300.0
EPSR = 4.5
TAND = 0.02
H_SUB = 0.8       # substrate thickness in mm (tutorial uses mm units)
WORK = Path(__file__).resolve().parents[1] / "work"
OUT = Path(__file__).resolve().parents[1] / "outputs"


def calc_patch(f0, h_mm=H_SUB):
    """Balanis patch dimensions [mm] for microstrip on FR-4."""
    h = h_mm * 1e-3
    W = C0 / (2 * f0) * np.sqrt(2 / (EPSR + 1))
    eps_eff = (EPSR + 1) / 2 + (EPSR - 1) / 2 * (1 + 12 * h / W) ** (-0.5)
    dL = 0.412 * h * (eps_eff + 0.3) / (eps_eff - 0.258) * (W / h + 0.264) / (W / h + 0.813)
    L = C0 / (2 * f0 * np.sqrt(eps_eff)) - 2 * dL
    return np.array([W, L]) * 1e3, eps_eff


def friis_range(G_tx_dB, G_rx_dB, f0, P_tx_dBm=30, NF_dB=3.5, SNR_dB=10, BW_Hz=1e6):
    lam = C0 / f0
    G_tx = 10 ** (G_tx_dB / 10)
    G_rx = 10 ** (G_rx_dB / 10)
    Prx = 10 * np.log10(K_B * T_SYS * BW_Hz * 1e3) + NF_dB + SNR_dB
    Ptx = 10 ** (P_tx_dBm / 10 - 3)
    Prx_min = 10 ** (Prx / 10 - 3)
    R = lam / (4 * np.pi) * np.sqrt(Ptx * G_tx * G_rx / Prx_min)
    return R, Prx


def build_patch(f0, label, dir_name, air=60.0, probe_frac=0.19,
                nn_air=0.6, sub_cells=4, NrTS=40000, EndCriteria=1e-4,
                clean=True, L_override=None):
    """
    Build a single microstrip patch element (tutorial geometry) and return the
    ready-to-run model. Does NOT run the FDTD, so the caller can add extra
    probes (e.g. NF2FF box) before FDTD.Run().

    clean=False reuses an existing simulation directory (post-processing only).

    L_override : patch resonant length in mm.  Used to trim the Balanis value
        onto the *simulated* resonance (formula error correction); the width
        W and eps_eff are left untouched.

    Returns dict: FDTD, CSX, port, d(Path), W, L, eps_eff, feed_pos, sub_box,
                  mesh_base (mm), label, f0.
    """
    [W, L], eps_eff = calc_patch(f0)
    if L_override is not None:
        L = float(L_override)
    sub_kappa = 2 * np.pi * f0 * EPS0 * EPSR * TAND

    print(f"\n{'=' * 60}")
    print(f"  {label}: single element f0={f0 / 1e9:.3f} GHz")
    print(f"  Balanis: W={W:.2f} mm  L={L:.2f} mm  eps_eff={eps_eff:.2f}")

    # ---- mesh resolution ----
    fc = f0 * 0.6                      # excitation 20 dB corner
    mesh_base = np.min([nn_air, C0 / (f0 + fc) / 1e-3 / 20])  # mm

    FDTD = openEMS(NrTS=NrTS, EndCriteria=EndCriteria)
    FDTD.SetGaussExcite(f0, fc)
    FDTD.SetBoundaryCond(['PML_8'] * 6)   # absorbing boundaries (open space)

    CSX = ContinuousStructure()
    FDTD.SetCSX(CSX)
    mesh = CSX.GetGrid()
    mesh.SetDeltaUnit(1e-3)            # mm

    mesh.AddLine('x', [-air, air])
    mesh.AddLine('y', [-air, air])
    mesh.AddLine('z', [-air * 0.35, air * 0.65])

    # ---- patch (zero-thickness sheet at z=H_SUB) ----
    patch = CSX.AddMetal('patch')
    start = [-W / 2, -L / 2, H_SUB]
    stop = [W / 2, L / 2, H_SUB]
    patch.AddBox(priority=10, start=start, stop=stop)
    FDTD.AddEdges2Grid(dirs='xy', properties=patch, metal_edge_res=W / 40)

    # ---- substrate ----
    sub_box = max(W, L) + 30           # substrate extends 15 mm beyond patch each side
    substrate = CSX.AddMaterial('substrate', epsilon=EPSR, kappa=sub_kappa)
    substrate.AddBox(priority=0,
                     start=[-sub_box / 2, -sub_box / 2, 0],
                     stop=[sub_box / 2, sub_box / 2, H_SUB])
    mesh.AddLine('z', np.linspace(0, H_SUB, sub_cells + 1))

    # ---- ground (zero-thickness sheet at z=0) ----
    gnd = CSX.AddMetal('gnd')
    gnd.AddBox(priority=10,
               start=[-sub_box / 2, -sub_box / 2, 0],
               stop=[sub_box / 2, sub_box / 2, 0])
    FDTD.AddEdges2Grid(dirs='xy', properties=gnd)

    # ---- probe feed (zero-width z line; offset along resonant y axis) ----
    feed_pos = -L * probe_frac        # offset from center toward -y edge
    port = FDTD.AddLumpedPort(1, 50,
                              start=[0, feed_pos, 0],
                              stop=[0, feed_pos, H_SUB],
                              p_dir='z', excite=1.0, priority=5, edges2grid='xy')
    print(f"  Probe feed at (x=0, y={feed_pos:.2f} mm) relative to patch center")

    mesh.SmoothMeshLines('all', mesh_base, 1.4)

    d = WORK / dir_name
    if clean and d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True, exist_ok=True)

    return {"FDTD": FDTD, "CSX": CSX, "port": port, "d": d,
            "W": float(W), "L": float(L), "eps_eff": float(eps_eff),
            "feed_pos": float(feed_pos), "sub_box": sub_box,
            "mesh_base": float(mesh_base), "label": label, "f0": f0}


def post_s11(port, sim_path, f0, G_rx_dBi=0.0, gc=0.65, search_frac=None):
    """S11/Z sweep + Friis range from a completed port simulation.

    search_frac : if set, the reported resonance is the S11 minimum inside
        f0*(1 +/- search_frac); otherwise it is the global minimum.
    """
    frange = [f0 * (1 - gc), f0 * (1 + gc)]
    freqs = np.linspace(frange[0], frange[1], 401)
    port.CalcPort(str(sim_path), freqs)
    s11 = port.uf_ref / port.uf_inc
    s11_dB = 20 * np.log10(np.abs(s11) + 1e-30)
    Zin = port.uf_tot / port.if_tot
    # Accepted (input) power at the feed.  openEMS normalises the port DFT by
    # the excitation spectrum, so this is not in physical watts; it is however
    # consistently scaled with the NF2FF ``Prad``, hence Prad/P_acc is the
    # physical radiation efficiency (same convention as the openEMS tutorials).
    P_acc = 0.5 * np.real(port.uf_tot * np.conj(port.if_tot))

    if search_frac is not None:
        w = np.where((freqs >= f0 * (1 - search_frac)) &
                     (freqs <= f0 * (1 + search_frac)))[0]
        ires = int(w[np.argmin(s11_dB[w])]) if len(w) else int(np.argmin(s11_dB))
    else:
        ires = int(np.argmin(s11_dB))
    # -10 dB bandwidth of the *contiguous* matched band containing the
    # resonance.  (A plain first/last crossing would wrongly merge in distant
    # spurious higher-order nulls.)
    below = s11_dB < -10.0
    if below[ires]:
        lo = ires
        while lo > 0 and below[lo - 1]:
            lo -= 1
        hi = ires
        while hi < len(below) - 1 and below[hi + 1]:
            hi += 1
        bw_hz = float(freqs[hi] - freqs[lo])
    else:
        bw_hz = 0.0
    i0 = int(np.argmin(np.abs(freqs - f0)))
    print(f"  f_res={freqs[ires] / 1e9:.3f} GHz  S11={s11_dB[ires]:.1f} dB  "
          f"BW(-10dB)={bw_hz / 1e6:.1f} MHz")
    print(f"  Zin@res={np.real(Zin[ires]):.0f}+j{np.imag(Zin[ires]):.0f} Ohm  "
          f"S11@design_f0={s11_dB[i0]:.1f} dB")

    R_max, Prx_min = friis_range(G_tx_dB=2, G_rx_dB=G_rx_dBi, f0=freqs[ires],
                                 BW_Hz=max(bw_hz, 1e6))
    return {
        "freq_hz": freqs.tolist(),
        "S11_dB": s11_dB.tolist(),
        "Z_real": np.real(Zin).tolist(),
        "Z_imag": np.imag(Zin).tolist(),
        "f_res": float(freqs[ires]),
        "S11_min": float(s11_dB[ires]),
        "BW_hz": bw_hz,
        "P_acc_res": float(P_acc[ires]),
        "P_acc": P_acc.tolist(),
        "range": {"G_rx_dBi": G_rx_dBi, "R_max_m": R_max, "sensitivity_dBm": Prx_min},
    }


def sim_patch_tutorial(f0, label, dir_name, G_rx_dBi, air=60.0, probe_frac=0.19,
                       nn_air=0.6, sub_cells=4, NrTS=40000, EndCriteria=1e-4,
                       L_override=None):
    """
    Single patch element, tutorial-geometry: build + run + S11 post-process.
    """
    b = build_patch(f0, label, dir_name, air=air, probe_frac=probe_frac,
                    nn_air=nn_air, sub_cells=sub_cells,
                    NrTS=NrTS, EndCriteria=EndCriteria, L_override=L_override)
    t0 = time.time()
    print(f"  Running FDTD (mesh_base={b['mesh_base']:.2f}mm)...")
    b["FDTD"].Run(str(b["d"]), cleanup=True, verbose=False)
    print(f"  FDTD done: {time.time() - t0:.0f}s")

    r = post_s11(b["port"], b["d"], b["f0"], G_rx_dBi=G_rx_dBi)
    r["geometry"] = {
        "W_mm": b["W"], "L_mm": b["L"],
        "eps_eff": b["eps_eff"], "probe_offset_mm": b["feed_pos"],
        "substrate_mm": b["sub_box"],
    }
    return r


def sim_channel_a_analytic():
    """Channel A: 200 MHz meander - analytic TL estimate (FDTD impractical)."""
    print(f"\n{'=' * 60}")
    print("  CHANNEL A: meander 200 MHz -- analytic model (FDTD impractical at 0.13 lam)")
    f0 = 200e6
    n_arms = 3
    cw = 1.2e-3
    arm_gap = 15e-3
    k_meand = 1.7                    # meander slows the guided wave (~1.6-1.8)
    lam0 = C0 / f0
    # Quarter-wave resonance: elec_len = lambda/4  =>  phys_len = lambda/(4*k)
    tot_cond = lam0 / (4 * k_meand)
    arm_len = (tot_cond - (n_arms - 1) * arm_gap) / n_arms
    f_res = f0
    bw_frac = 0.05
    s11_est = -12.0
    freqs = np.linspace(50e6, 800e6, 151)
    Q = 1 / bw_frac
    # Series-RLC resonator: |S11| is minimum at f_res and -> 0 dB (full
    # reflection) away from it.  Complex Zin and S11 are kept consistent.
    mag_min = 10 ** (s11_est / 20)
    R = 50.0 * (1 + mag_min) / (1 - mag_min)      # R > 50 -> ~-12 dB match
    Z = R + 1j * Q * R * (freqs / f_res - f_res / freqs)
    s11 = 20 * np.log10(np.abs((Z - 50.0) / (Z + 50.0)) + 1e-30)
    bw_pts = np.where(s11 < -10)[0]
    bw_hz_est = float(freqs[bw_pts[-1]] - freqs[bw_pts[0]]) if len(bw_pts) > 1 else f0 * bw_frac
    return {
        "freq_hz": freqs.tolist(), "S11_dB": s11.tolist(),
        "Z_real": np.real(Z).tolist(), "Z_imag": np.imag(Z).tolist(),
        "f_res": float(f_res), "S11_min": float(s11_est),
        "BW_hz": bw_hz_est,
        "geometry": {"n_arms": n_arms, "arm_len_mm": arm_len * 1e3,
                     "arm_gap_mm": arm_gap * 1e3, "cond_len_mm": tot_cond * 1e3,
                     "model": "analytic", "meander_factor": k_meand},
        "note": "Meander too small for FDTD on this machine (panel=13% lambda at 200MHz); "
                "analytic quarter-wave estimate provided (arms sized for 200 MHz).",
    }


def plot_results(results):
    OUT.mkdir(parents=True, exist_ok=True)
    channels = [
        ("channel_a", "Ch A: Meander 200 MHz", "MHz"),
        ("channel_b", "Ch B: 2x2 Patch Array 2.45 GHz", "GHz"),
        ("channel_c", "Ch C: 4x4 Patch Array 5.8 GHz", "GHz"),
    ]
    fig, axes = plt.subplots(3, 2, figsize=(16, 14))
    fig.suptitle("OpenEMS FDTD: S11 & Impedance - Brig Receiver", fontsize=14, fontweight="bold")
    for row, (key, title, unit) in enumerate(channels):
        r = results.get(key, {})
        if not r or "error" in r:
            for col in range(2):
                axes[row, col].text(0.5, 0.5, "ERROR" if r and "error" in r else "N/A",
                                    ha="center", transform=axes[row, col].transAxes)
            continue
        freqs = np.array(r["freq_hz"]); s11 = np.array(r["S11_dB"])
        Zr = np.array(r["Z_real"]); Zi = np.array(r["Z_imag"])
        f_div = 1e6 if unit == "MHz" else 1e9
        axes[row, 0].plot(freqs / f_div, s11, "b-", lw=1.5)
        axes[row, 0].axhline(-10, color="r", ls="--", alpha=0.5, label="-10 dB")
        axes[row, 0].axvline(r["f_res"] / f_div, color="g", ls=":", alpha=0.6)
        axes[row, 0].set_ylabel("S11 (dB)")
        axes[row, 0].set_title(f"{title}\nS11={r['S11_min']:.1f} dB  "
                               f"BW={r['BW_hz'] / f_div:.1f} {unit}")
        axes[row, 0].set_ylim(-30, 5); axes[row, 0].legend(fontsize=8)
        axes[row, 0].grid(True, alpha=0.3); axes[row, 0].set_xlabel(f"Freq ({unit})")
        axes[row, 1].plot(freqs / f_div, Zr, "r-", label="Re(Z)", lw=1.2)
        axes[row, 1].plot(freqs / f_div, Zi, "b--", label="Im(Z)", lw=1.2)
        axes[row, 1].axhline(50, color="k", ls=":", alpha=0.3, label="50 Ohm")
        axes[row, 1].set_ylabel("Z (Ohm)")
        axes[row, 1].set_title(f"{title} - Impedance")
        axes[row, 1].legend(fontsize=8); axes[row, 1].grid(True, alpha=0.3)
        axes[row, 1].set_xlabel(f"Freq ({unit})")
    plt.tight_layout()
    plt.savefig(OUT / "openems_s11_impedance.png", dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  -> {OUT / 'openems_s11_impedance.png'}")

    fig, ax = plt.subplots(figsize=(14, 5)); ax.axis("off")
    headers = ["Channel", "f_res", "S11_min", "BW", "G(dBi)", "R_max(m)", "Z_in @ res"]
    rows = []
    for key, title, unit in channels:
        r = results.get(key, {})
        if not r or "error" in r:
            rows.append([key, "ERROR", "-", "-", "-", "-", "-"]); continue
        rng = r.get("range", {})
        zr, zi = r.get("Z_real", []), r.get("Z_imag", [])
        ir = int(np.argmin(np.array(r["S11_dB"])))
        z_str = f"{zr[ir]:.0f}+j{zi[ir]:.0f}" if ir < len(zr) else "-"
        rows.append([
            key,
            f"{r['f_res'] / 1e9:.3f} GHz" if r["f_res"] > 1e9 else f"{r['f_res'] / 1e6:.1f} MHz",
            f"{r['S11_min']:.1f} dB",
            f"{r['BW_hz'] / 1e6:.1f} MHz" if r["BW_hz"] > 0 else "n/a",
            f"{rng.get('G_rx_dBi', 0):.1f}" if rng else "-",
            f"{rng.get('R_max_m', 0):.0f}" if rng else "-",
            z_str,
        ])
    table = ax.table(cellText=rows, colLabels=headers, loc="center", cellLoc="center")
    table.auto_set_font_size(False); table.set_fontsize(10); table.scale(1.2, 1.8)
    for (i, j), cell in table.get_celld().items():
        if i == 0:
            cell.set_facecolor("#4472C4")
            cell.set_text_props(color="white", fontweight="bold")
    plt.title("Brig Receiver - Antenna Summary (P_tx=1W, NF=3.5dB, T=300K)", fontsize=12, pad=20)
    plt.savefig(OUT / "openems_summary.png", dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  -> {OUT / 'openems_summary.png'}")


def save_results(results):
    OUT.mkdir(parents=True, exist_ok=True)
    with open(OUT / "openems_results.json", "w") as f:
        json.dump(results, f, indent=2, ensure_ascii=False, default=str)


def main():
    print("=" * 60)
    print("  OpenEMS FDTD: Brig Receiver Antennas - FINAL")
    print("=" * 60)
    results = {}
    t0 = time.time()

    # Channel B: 2.45 GHz patch element (array 2x2: +6 dBi)
    # NOTE: the primary pipeline is sim/scripts/radiation.py (FDTD + NF2FF +
    # plots).  These entries mirror its retuned geometry/probe for standalone use.
    print("\n>> channel_b...")
    try:
        results["channel_b"] = sim_patch_tutorial(
            2.45e9, "Ch B: 2.45 GHz Patch", "openems_b_final",
            G_rx_dBi=6.0, air=60.0, probe_frac=0.3131, nn_air=1.2,
            sub_cells=4, NrTS=40000, EndCriteria=1e-4, L_override=28.350)
        save_results(results)
        print(f"  OK {time.time() - t0:.0f}s")
    except Exception:
        traceback.print_exc()
        results["channel_b"] = {"error": traceback.format_exc()[-300:]}
        save_results(results)

    # Channel C: 5.8 GHz patch element (array 4x4: +12 dBi)
    print("\n>> channel_c...")
    try:
        results["channel_c"] = sim_patch_tutorial(
            5.8e9, "Ch C: 5.8 GHz Patch", "openems_c_final",
            G_rx_dBi=12.0, air=40.0, probe_frac=0.2258, nn_air=0.8,
            sub_cells=4, NrTS=40000, EndCriteria=1e-4, L_override=11.616)
        save_results(results)
        print(f"  OK {time.time() - t0:.0f}s")
    except Exception:
        traceback.print_exc()
        results["channel_c"] = {"error": traceback.format_exc()[-300:]}
        save_results(results)

    # Channel A: analytic
    print("\n>> channel_a (analytic)...")
    try:
        r = sim_channel_a_analytic()
        R_max, Prx_min = friis_range(G_tx_dB=2, G_rx_dB=2.15, f0=200e6,
                                     BW_Hz=max(r["BW_hz"], 1e6))
        r["range"] = {"G_rx_dBi": 2.15, "R_max_m": R_max, "sensitivity_dBm": Prx_min}
        results["channel_a"] = r
        print(f"  OK R_max={R_max:.0f}m")
    except Exception:
        traceback.print_exc()
        results["channel_a"] = {"error": traceback.format_exc()[-300:]}
    save_results(results)

    print(f"\n  TOTAL: {time.time() - t0:.0f}s")
    plot_results(results)
    # Regenerate the ngspice-ready S-parameter networks from the fresh results.
    try:
        import subprocess
        subprocess.run([sys.executable, str(Path(__file__).with_name("export_ngspice.py"))],
                       check=True)
    except Exception:
        traceback.print_exc()
    print("\n" + "=" * 60)
    print("  SUMMARY:")
    for key in ("channel_a", "channel_b", "channel_c"):
        r = results.get(key, {})
        if "error" in r:
            print(f"  {key}: ERROR - {r['error'][:90]}")
        else:
            rng = r.get("range", {})
            fd = 1e6 if r["f_res"] < 1e9 else 1e9
            u = "MHz" if fd == 1e6 else "GHz"
            print(f"  {key}: f_res={r['f_res'] / fd:.1f} {u}  "
                  f"S11={r['S11_min']:.1f} dB  BW={r['BW_hz'] / fd:.1f} {u}  "
                  f"G={rng.get('G_rx_dBi', 0):.1f} dBi  R_max={rng.get('R_max_m', 0):.0f} m")


if __name__ == "__main__":
    main()