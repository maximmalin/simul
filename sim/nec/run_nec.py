#!/usr/bin/env python3
"""NEC-2: расчёт 3 антенн + диаграммы + дальность.

Запуск:
    cd /home/mirrage/Desktop/krystal
    .venv/bin/python sim/nec/run_nec.py
"""
import subprocess
import json
import re
import os
import sys
from pathlib import Path
import numpy as np

C0 = 299792458.0
K_B = 1.380649e-23
T_SYS = 300.0
NEC = "/usr/bin/nec2c"
OUT = Path(__file__).resolve().parents[1] / "outputs"
OUT.mkdir(parents=True, exist_ok=True)

# ====================================================================
# Генерация .nec файлов
# ====================================================================

def write_channel_a():
    """Meander line 200 МГц: 3 рукава по 130мм, шаг 20мм, дорожка 1.2мм."""
    f0 = 200.0  # МГц
    n_arms = 3
    arm = 0.130   # м
    gap = 0.020   # м
    cw = 0.0012   # м (эквивалентный радиус провода)
    r_wire = cw / 2  # радиус провода NEC
    y0 = 0.030    # начальная Y
    x0 = 0.030    # начальная X
    z = 0.001     # над землёй (模拟 подложка)

    lines = [
        "CM Channel A: Meander Line 200 MHz",
        "CM 3 arms x 130mm, gap 20mm, wire_r=0.6mm",
        "CE",
    ]
    tag = 1
    for i in range(n_arms):
        y = y0 + i * (cw + gap)
        if i % 2 == 0:
            xa, xb = x0, x0 + arm
        else:
            xa, xb = x0 + arm, x0
        lines.append(f"GW {tag} 10 {xa:.4f} {y:.4f} {z:.4f} {xb:.4f} {y:.4f} {z:.4f} {r_wire:.6f}")
        tag += 1
        if i < n_arms - 1:
            yn = y0 + (i + 1) * (cw + gap)
            lines.append(f"GW {tag} 5 {xb:.4f} {y+cw:.4f} {z:.4f} {xb:.4f} {yn:.4f} {z:.4f} {r_wire:.6f}")
            tag += 1

    lines += [
        "GN 1 0 0 0 1.0 0.0",
        f"EX 0 1 1 1.0 0.0 0.0 0.0 0.0 0.0 0.0",
        f"FR 0 1 0 0 {f0:.1f} 0.0",
        "RP 0 181 361 1000 0.0 360.0 0.0 360.0",
        "EN",
    ]
    p = OUT.parent / "nec" / "channel_a.nec"
    p.write_text("\n".join(lines) + "\n")
    return p, f0


def write_channel_b():
    """2×2 patch array 2.45 ГГц: W=36.9mm L=28.7mm step=91.7mm.
    Wire grid: 7 поперечных + 7 продольных проводов на каждый патч."""
    f0 = 2450.0
    W = 0.0369
    L = 0.0287
    step = 0.0917
    z = 0.001
    r_w = 0.0003
    cx, cy = 0.100, 0.100
    nx, ny = 7, 7

    lines = [
        "CM Channel B: 2x2 Patch Array 2.45 GHz",
        f"CM Patch W={W*1e3:.1f}mm L={L*1e3:.1f}mm step={step*1e3:.1f}mm",
        "CE",
    ]
    tag = 1
    for ix in range(2):
        for iy in range(2):
            px = cx + (ix - 0.5) * step - W / 2
            py = cy + (iy - 0.5) * step - L / 2
            for j in range(ny):
                yy = py + j * L / (ny - 1)
                lines.append(f"GW {tag} 5 {px:.4f} {yy:.4f} {z:.4f} {px+W:.4f} {yy:.4f} {z:.4f} {r_w:.6f}")
                tag += 1
            for j in range(nx):
                xx = px + j * W / (nx - 1)
                lines.append(f"GW {tag} 5 {xx:.4f} {py:.4f} {z:.4f} {xx:.4f} {py+L:.4f} {z:.4f} {r_w:.6f}")
                tag += 1

    lines += [
        "GN 1 0 0 0 1.0 0.0008",
        "EX 0 1 1 1.0 0.0 0.0 0.0 0.0 0.0 0.0",
        f"FR 0 1 0 0 {f0:.1f} 0.0",
        "RP 0 181 361 1000 0.0 360.0 0.0 360.0",
        "EN",
    ]
    p = OUT.parent / "nec" / "channel_b.nec"
    p.write_text("\n".join(lines) + "\n")
    return p, f0


def write_channel_c():
    """4×4 patch array 5.8 ГГц: W=15.6mm L=12.0mm step=38.8mm."""
    f0 = 5800.0
    W = 0.0156
    L = 0.0120
    step = 0.0388
    z = 0.001
    r_w = 0.0002
    cx, cy = 0.080, 0.080
    nx, ny = 5, 5

    lines = [
        "CM Channel C: 4x4 Patch Array 5.8 GHz",
        f"CM Patch W={W*1e3:.1f}mm L={L*1e3:.1f}mm step={step*1e3:.1f}mm",
        "CE",
    ]
    tag = 1
    for ix in range(4):
        for iy in range(4):
            px = cx + (ix - 1.5) * step - W / 2
            py = cy + (iy - 1.5) * step - L / 2
            for j in range(ny):
                yy = py + j * L / (ny - 1)
                lines.append(f"GW {tag} 3 {px:.5f} {yy:.5f} {z:.4f} {px+W:.5f} {yy:.5f} {z:.4f} {r_w:.6f}")
                tag += 1
            for j in range(nx):
                xx = px + j * W / (nx - 1)
                lines.append(f"GW {tag} 3 {xx:.5f} {py:.5f} {z:.4f} {xx:.5f} {py+L:.5f} {z:.4f} {r_w:.6f}")
                tag += 1

    lines += [
        "GN 1 0 0 0 1.0 0.0008",
        "EX 0 1 1 1.0 0.0 0.0 0.0 0.0 0.0 0.0",
        f"FR 0 1 0 0 {f0:.1f} 0.0",
        "RP 0 181 361 1000 0.0 360.0 0.0 360.0",
        "EN",
    ]
    p = OUT.parent / "nec" / "channel_c.nec"
    p.write_text("\n".join(lines) + "\n")
    return p, f0


# ====================================================================
# Запуск nec2c и парсинг
# ====================================================================

def run_nec(nec_file):
    """Запуск nec2c, возврат stdout."""
    r = subprocess.run(
        [NEC, "-i", str(nec_file)],
        capture_output=True, text=True, timeout=120,
    )
    return r.stdout + r.stderr


def parse_impedance(text):
    """Парсинг Z_in из вывода nec2c."""
    # Ищем строки вида:  1  2.4500E+03  ...  R(X)=...  I(X)=...
    z_real, z_imag = None, None
    for m in re.finditer(
        r"IMPEDANCE.*?(\d+)\s+[\d.E+-]+\s+[\d.E+-]+\s+[\d.E+-]+\s+"
        r"([\d.E+-]+)\s+([\d.E+-]+)", text, re.IGNORECASE
    ):
        z_real = float(m.group(2))
        z_imag = float(m.group(3))
    if z_real is None:
        for m in re.finditer(r"Z\s*=\s*\(([^,]+),\s*([^)]+)\)", text):
            z_real = float(m.group(1))
            z_imag = float(m.group(2))
    if z_real is None:
        for m in re.finditer(r"INPUT IMPEDANCE.*?([\d.E+-]+)\s+([\d.E+-]+)", text, re.IGNORECASE):
            z_real = float(m.group(1))
            z_imag = float(m.group(2))
    return z_real, z_imag


def parse_gain(text):
    """Парсинг максимального усиления из вывода nec2c."""
    gain = None
    for m in re.finditer(r"TOTAL\s+GAIN\s*[:=]\s*([\d.E+-]+)", text, re.IGNORECASE):
        gain = float(m.group(1))
    if gain is None:
        for m in re.finditer(r"MAXIMUM\s+GAIN.*?([\d.E+-]+)\s*dB", text, re.IGNORECASE):
            gain = float(m.group(1))
    return gain


def parse_radiation(text, n_theta=181, n_phi=361):
    """Парсинг диаграммы направленности."""
    theta = []
    gain_theta = []
    in_rp = False
    for line in text.split("\n"):
        if "RADIATION" in line.upper() and "PATTERN" in line.upper():
            in_rp = True
            continue
        if in_rp:
            nums = re.findall(r"[-\d.E+-]+", line)
            if len(nums) >= 2:
                try:
                    t = float(nums[0])
                    g = float(nums[1])
                    if 0 <= t <= 180:
                        theta.append(t)
                        gain_theta.append(g)
                except ValueError:
                    pass
    return np.array(theta), np.array(gain_theta)


def s11_from_z(z_in, z0=50.0):
    """S11 из входного импеданса."""
    gamma = (z_in - z0) / (z_in + z0)
    return 20 * np.log10(abs(gamma) + 1e-30)


def friis_range(G_tx_dB, G_rx_dB, f0_Hz, P_tx_dBm=30, NF_dB=3.5, SNR_dB=10):
    lam = C0 / f0_Hz
    G_tx = 10 ** (G_tx_dB / 10)
    G_rx = 10 ** (G_rx_dB / 10)
    P_rx_min_dBm = 10 * np.log10(K_B * T_SYS * 1e3) + 10 * np.log10(1e6) + NF_dB + SNR_dB
    P_tx = 10 ** (P_tx_dBm / 10 - 3)
    P_rx_min = 10 ** (P_rx_min_dBm / 10 - 3)
    R = lam / (4 * np.pi) * np.sqrt(P_tx * G_tx * G_rx / P_rx_min)
    return R


# ====================================================================
# Multi-frequency sweep (ручной sweep по FR)
# ====================================================================

def sweep_nec(nec_file, freqs_mhz):
    """Запуск NEC на нескольких частотах, сбор Z(f)."""
    results = []
    for f in freqs_mhz:
        txt = nec_file.read_text()
        txt = re.sub(r"FR 0 1 0 0 [\d.E+-]+ 0.0", f"FR 0 1 0 0 {f:.2f} 0.0", txt)
        tmp = nec_file.parent / f"_sweep_{nec_file.stem}_{f:.0f}.nec"
        tmp.write_text(txt)
        out = run_nec(tmp)
        zr, zi = parse_impedance(out)
        g = parse_gain(out)
        tmp.unlink(missing_ok=True)
        if zr is not None:
            z = complex(zr, zi)
            s11 = s11_from_z(z)
            results.append({"freq_mhz": f, "Z_real": zr, "Z_imag": zi, "S11_dB": s11, "gain_dBi": g})
    return results


# ====================================================================
# Main
# ====================================================================

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def main():
    print("╔══════════════════════════════════════════════════════╗")
    print("║  NEC-2: расчёт 3 антенн Brig Receiver                ║")
    print("╚══════════════════════════════════════════════════════╝")

    channels = [
        ("channel_a", "Канал А: Meander 200 МГц", write_channel_a,  np.linspace(50, 900, 18)),
        ("channel_b", "Канал Б: 2×2 Patch 2.45 ГГц", write_channel_b, np.linspace(1500, 3500, 21)),
        ("channel_c", "Канал В: 4×4 Patch 5.8 ГГц", write_channel_c, np.linspace(3500, 8000, 26)),
    ]

    all_results = {}

    for key, title, writer, freqs in channels:
        print(f"\n{'='*60}")
        print(f"  {title}")
        nec_file, f0_mhz = writer()
        print(f"  .nec: {nec_file}")

        sweep = sweep_nec(nec_file, freqs)
        if not sweep:
            print(f"  ОШИБКА: nec2c не вернул результатов")
            all_results[key] = {"error": "nec2c failed"}
            continue

        freqs_arr = np.array([s["freq_mhz"] for s in sweep]) * 1e6
        s11_arr = np.array([s["S11_dB"] for s in sweep])
        zr_arr = np.array([s["Z_real"] for s in sweep])
        zi_arr = np.array([s["Z_imag"] for s in sweep])
        gain_arr = np.array([s.get("gain_dBi", 0) or 0 for s in sweep])

        idx = np.argmin(s11_arr)
        f_res = freqs_arr[idx]
        s11_min = s11_arr[idx]
        bw_mask = s11_arr < -10
        bw = float(freqs_arr[bw_mask][-1] - freqs_arr[bw_mask][0]) if np.sum(bw_mask) > 1 else 0
        G_rx = float(gain_arr[idx]) if gain_arr[idx] > 0 else 2.15
        R_max = friis_range(G_tx_dB=2, G_rx_dB=G_rx, f0_Hz=f_res)

        print(f"  f_res = {f_res/1e6:.1f} МГц  S11 = {s11_min:.1f} дБ  BW = {bw/1e6:.0f} МГц")
        print(f"  Z = {zr_arr[idx]:.1f} + j{zi_arr[idx]:.1f} Ом")
        print(f"  G = {G_rx:.1f} дБи  R_max = {R_max:.0f} м (1Вт, G_tx=2дБи)")

        all_results[key] = {
            "freq_hz": freqs_arr.tolist(),
            "S11_dB": s11_arr.tolist(),
            "Z_real": zr_arr.tolist(),
            "Z_imag": zi_arr.tolist(),
            "gain_dBi": gain_arr.tolist(),
            "f_res_hz": float(f_res),
            "S11_min_dB": float(s11_min),
            "BW_hz": bw,
            "G_rx_dBi": G_rx,
            "R_max_m": R_max,
        }

    # --- Графики ---
    fig, axes = plt.subplots(3, 2, figsize=(16, 14))
    fig.suptitle("NEC-2: S₁₁, импеданс, диаграмма — Brig Receiver", fontsize=14, fontweight="bold")

    for row, (key, title, _, _) in enumerate(channels):
        r = all_results.get(key, {})
        if "error" in r:
            axes[row, 0].text(0.5, 0.5, f"ОШИБКА", ha="center", transform=axes[row, 0].transAxes)
            axes[row, 1].text(0.5, 0.5, f"ОШИБКА", ha="center", transform=axes[row, 1].transAxes)
            continue
        freqs = np.array(r["freq_hz"])
        f_div = 1e6 if freqs[-1] < 1e9 else 1e9
        unit = "МГц" if f_div == 1e6 else "ГГц"

        ax1 = axes[row, 0]
        ax1.plot(freqs / f_div, r["S11_dB"], "b-", lw=1.5)
        ax1.axhline(-10, color="r", ls="--", alpha=0.5, label="-10 дБ")
        ax1.axvline(r["f_res_hz"] / f_div, color="g", ls=":", alpha=0.5)
        ax1.set_ylabel("S₁₁ (дБ)")
        ax1.set_title(f"{title}  f_res={r['f_res_hz']/f_div:.1f} {unit}")
        ax1.set_ylim(-30, 5)
        ax1.legend(fontsize=8)
        ax1.grid(True, alpha=0.3)
        ax1.set_xlabel(unit)

        ax2 = axes[row, 1]
        ax2.plot(freqs / f_div, r["Z_real"], "r-", label="Re(Z)", lw=1.2)
        ax2.plot(freqs / f_div, r["Z_imag"], "b--", label="Im(Z)", lw=1.2)
        ax2.axhline(50, color="k", ls=":", alpha=0.3, label="50 Ом")
        ax2.set_ylabel("Z (Ом)")
        ax2.set_title(f"Импеданс  G={r['G_rx_dBi']:.1f} дБи  R={r['R_max_m']:.0f}м")
        ax2.legend(fontsize=8)
        ax2.grid(True, alpha=0.3)
        ax2.set_xlabel(unit)

    plt.tight_layout()
    plt.savefig(OUT / "nec_results.png", dpi=150, bbox_inches="tight")
    plt.close()
    print(f"\n  → {OUT / 'nec_results.png'}")

    with open(OUT / "nec_results.json", "w") as f:
        json.dump(all_results, f, indent=2, ensure_ascii=False)
    print(f"  → {OUT / 'nec_results.json'}")

    print("\n  СВОДКА:")
    for key, title, _, _ in channels:
        r = all_results.get(key, {})
        if "error" in r:
            print(f"  {title}: ОШИБКА")
        else:
            print(f"  {title}: f_res={r['f_res_hz']/1e6:.1f} МГц  S11={r['S11_min_dB']:.1f} дБ  G={r['G_rx_dBi']:.1f} дБи  R={r['R_max_m']:.0f}м")


if __name__ == "__main__":
    main()
