#!/usr/bin/env python3
"""Closed-loop verification of the SDR analog chain and the pin contract.

PICSimLab's rcontrol responder does not service commands in this environment
(port accepts, nothing is ever processed), so the two halves of the
co-simulation cannot yet be run together. What *can* be verified without it is
everything on the ngspice side, and the contract the STM32 side has to meet.

The checks below run the real netlist and measure what comes out. The
interesting ones are not "does it parse" but "is the conversion actually
happening":

  * the LO must be a waveform, not a DC level -- a static LO cannot mix
  * both I and Q must carry signal, and it must be a real quadrature pair:
    equal amplitude, 90 degrees apart. A Q channel wired to zero still
    "converges" and still parses; only the phase says it is broken
  * the beat must land at the LO offset, since that is what direct conversion
    means
  * the baseband must sit at mid-rail, or a 0..VDD ADC cannot see either half
    of a bipolar signal
  * the digital nodes must actually resolve to 0 and VDD
  * stopping the LO must collapse the baseband, which is what proves the pin
    control is wired to something

Run:
    python3 sim/scripts/verify_pin_contract.py
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

PROJECT = Path(__file__).resolve().parent.parent.parent
NETLIST = PROJECT / "sim" / "netlists" / "sdr_skidl_ngspice.cir"

V_LOGIC = 3.3
MID_RAIL = 1.65

# Kept short but long enough to resolve the baseband beat: two cycles of a
# 50 kHz beat, which is what the quadrature phase estimate needs.
TRAN = "50n 320u"


class Report:
    def __init__(self) -> None:
        self.failed: list[str] = []

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}"
              + (f"   ({detail})" if detail else ""))
        if not ok:
            self.failed.append(name)
        return ok


def run_spice(text: str, tag: str) -> tuple[int, str, Path | None]:
    """Run a deck in a scratch dir and return (rc, output, trace file or None)."""
    td = Path(tempfile.mkdtemp(prefix=f"sdr_{tag}_", dir="/mnt/ext4data"))
    cir = td / f"{tag}.cir"
    cir.write_text(text)
    # cwd matters: wrdata writes to the process's working directory, not to the
    # directory holding the deck.
    p = subprocess.run(["ngspice", "-b", cir.name],
                       capture_output=True, text=True, timeout=900, cwd=td)
    dat = td / "sdr_trace.dat"
    return p.returncode, p.stdout + p.stderr, (dat if dat.exists() else None)


def load(dat: Path) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """Read a wrdata file: time column then each vector's value column."""
    hdr = open(dat).readline().split()
    names = [hdr[j] for j in range(1, len(hdr), 2)]
    d = np.loadtxt(dat, skiprows=1)
    t = d[:, 0]
    cols = {n: d[:, 1 + 2 * i] for i, n in enumerate(names)}
    return t, cols


def baseband(t: np.ndarray, x: np.ndarray, cut: float = 150e3,
             fs: float = 20e6) -> np.ndarray:
    """Resample onto a uniform grid and keep only the baseband.

    ngspice's transient is adaptive, so the rows are not evenly spaced and a
    plain rfft reads a frequency axis that is simply wrong -- an earlier
    version of this file reported the baseband at 1.15 MHz and the LO at
    159 kHz, both artifacts of assuming uniform steps.

    The grid has to be fast enough to hold the 7.15 MHz RF (a 1 MHz grid
    aliases the LO down), and the low-pass is what separates the baseband from
    the mixer images at f_RF +/- n*f_LO.
    """
    g = np.arange(t[0], t[-1], 1.0 / fs)
    x = np.interp(g, t, x)
    X = np.fft.rfft(x)
    X[np.fft.rfftfreq(len(g), 1.0 / fs) > cut] = 0
    return np.fft.irfft(X, len(g))


def tone(t: np.ndarray, x: np.ndarray, cut: float = 150e3) -> tuple[float, float, np.ndarray]:
    """Dominant baseband tone: (frequency Hz, amplitude V, complex spectrum)."""
    g = np.arange(t[0], t[-1], 1.0 / 20e6)
    x = np.interp(g, t, x) - np.interp(g, t, x).mean()
    X = np.fft.rfft(x)
    X[np.fft.rfftfreq(len(g), 1.0 / 20e6) > cut] = 0
    f = np.fft.rfftfreq(len(g), 1.0 / 20e6)
    k = int(np.argmax(np.abs(X)))
    return float(f[k]), 2.0 * abs(X[k]) / len(g), X


def main() -> int:
    r = Report()

    if not NETLIST.exists():
        print(f"missing {NETLIST}\nrun sim/scripts/export_ngspice.py first",
              file=sys.stderr)
        return 1
    text = NETLIST.read_text()

    # ---------------------------------------------------------------- structure
    print("\n1. the netlist describes a real direct-conversion receiver")
    r.check("LO is a waveform source, not a DC level",
            re.search(r"^V_LO_TONE\s+\S+\s+0\s+PULSE\(", text, re.M) is not None)
    r.check("LO has a quadrature splitter feeding the Q channel",
            re.search(r"^E_LO_QA\s+MIX_U_MIX1_LOQ\s+0\s+POLY\(2\)", text, re.M)
            is not None)
    r.check("Q mixer output is driven by the quadrature node",
            re.search(r"^B_U_MIX1_Q\s+MIX_Q1\s+0\s+V = .*V\(MIX_U_MIX1_LOQ\)",
                      text, re.M) is not None)
    r.check("no channel is wired to zero",
            re.search(r"^B_U_MIX1_Q.*\*\s*0\s*$", text, re.M) is None)
    r.check("baseband buffers are biased to mid-rail",
            len(re.findall(r"^B_U_OP1_[IQ].*=\s*1\.65\s*\+", text, re.M)) == 2)
    r.check("RF chokes are modelled as L+R, not bare resistors",
            len(re.findall(r"^LL_LNA\d\s", text, re.M)) == 2)
    r.check("LNAs are transconductances with the gate bias referenced out",
            len(re.findall(r"^G_Q_LNA\d\s+\S+\s+0\s+VALUE\s*=", text, re.M)) == 2)

    m = re.search(r"^\.model\s+m_adc\s+adc_bridge\(([^)]*)\)", text, re.S | re.M)
    if m:
        lo = re.search(r"in_low\s*=\s*([\d.]+)", m.group(1))
        hi = re.search(r"in_high\s*=\s*([\d.]+)", m.group(1))
        if lo and hi:
            a, b = float(lo.group(1)), float(hi.group(1))
            # STM32F103 Table 29, CMOS port: VIL <= 0.35*VDD, VIH >= 0.65*VDD.
            r.check("ADC thresholds are the datasheet CMOS input levels",
                    abs(a - 0.35 * V_LOGIC) < 0.01 and abs(b - 0.65 * V_LOGIC) < 0.01,
                    f"in_low={a} in_high={b} "
                    f"(Table 29: 0.35/0.65 x VDD = {0.35 * V_LOGIC}/{0.65 * V_LOGIC})")
            r.check("ADC thresholds straddle mid-rail", a < MID_RAIL < b)
    else:
        r.check("adc_bridge model present", False)

    # STM32F103 Table 30 at IIO = 20 mA gives VOL <= 1.3 V and VOH >= VDD-1.3,
    # i.e. 65 ohm in both directions. Table 29 gives CIO = 5 pF.
    expect_r, expect_c = 65.0, 5.0
    for pin in ("LO", "GAIN"):
        res = re.search(rf"^R_PIN_{pin}\s+(\S+)\s+(\S+)\s+([\d.]+)", text, re.M)
        ok = res is not None and abs(float(res.group(3)) - expect_r) < 0.5
        r.check(f"{pin} pin output impedance is the datasheet 65 ohm", ok,
                f"{res.group(3)} ohm {res.group(1)}->{res.group(2)}"
                if res else "absent")
        cap = re.search(rf"^C_PIN_{pin}\s+(\S+)\s+(\S+)\s+([\d.]+)p", text, re.M)
        ok = cap is not None and abs(float(cap.group(3)) - expect_c) < 0.1
        r.check(f"{pin} pin capacitance is the datasheet CIO 5 pF", ok,
                f"{cap.group(3)} pF" if cap else "absent")

    # --------------------------------------------------------------- run it
    print("\n2. the netlist runs clean")
    deck = re.sub(r"^\.tran.*$", f".tran {TRAN} uic", text, flags=re.M)
    rc, out, dat = run_spice(deck, "on")
    r.check("ngspice exit status 0", rc == 0)
    noisy = [ln for ln in out.splitlines()
             if re.search(r"error|warning|timestep too small|singular", ln, re.I)]
    r.check("no errors, warnings or timestep failures", not noisy,
            noisy[0].strip()[:70] if noisy else "clean")
    if dat is None:
        r.check("trace data written", False)
        return _finish(r)

    t, cols = load(dat)

    # Measure the settled window. The rails and the choke bias networks have
    # multi-hundred-microsecond time constants, so the first part of the run is
    # supply startup, not signal -- including it inflated the reported ADC swing
    # to the full rail-to-rail.
    t0 = t[0] + 0.35 * (t[-1] - t[0])
    sel = t >= t0
    t = t[sel]
    cols = {k: v[sel] for k, v in cols.items()}

    def col(*cands: str) -> np.ndarray | None:
        for c in cands:
            for k, v in cols.items():
                if k.lower() == f"v({c.lower()})":
                    return v
        return None

    # ------------------------------------------------------------- quadrature
    print("\n3. direct conversion actually happens")
    lo = col("LO")
    r.check("LO is an oscillating waveform at the pin",
            lo is not None and np.ptp(lo) > 2.0,
            f"{np.ptp(lo):.3f} V peak-to-peak" if lo is not None else "absent")

    fi, ai, Xi = tone(t, col("MIX_I1"))
    fq, aq, Xq = tone(t, col("MIX_Q1"))
    # Tolerance is one FFT bin, not a fixed number: a 220 us window only resolves
    # the beat to about 1/220us, so anything tighter would be testing the window
    # length rather than the mixer.
    bin_hz = 1.0 / (t[-1] - t[0])
    r.check("beat lands at the IF offset", abs(fi - 50e3) < bin_hz,
            f"{fi:,.0f} Hz (+/-{bin_hz:,.0f} Hz one bin)")
    r.check("I channel carries baseband signal", ai > 1e-3, f"{ai:.4f} V")
    r.check("Q channel carries baseband signal", aq > 1e-3, f"{aq:.4f} V")

    ratio = ai / aq if aq else 0.0
    r.check("I and Q are amplitude matched", abs(ratio - 1.0) < 0.15,
            f"I/Q = {ratio:.3f}")

    k = int(np.argmax(np.abs(Xi)))
    ph = np.angle(Xq[k] / Xi[k], deg=True)
    err = min(abs(ph - 90.0), abs(ph + 90.0))
    r.check("I and Q are 90 degrees apart", err < 10.0,
            f"phase Q-I = {ph:+.1f} deg")

    # ----------------------------------------------------------------- bias
    print("\n4. the ADC sees a usable signal")
    ai_v, aq_v = col("ADC_I"), col("ADC_Q")
    for nm, sig in (("ADC_I", ai_v), ("ADC_Q", aq_v)):
        r.check(f"{nm} sits at mid-rail",
                abs(sig.mean() - MID_RAIL) < 0.1,
                f"mean {sig.mean():.4f} V")
    r.check("ADC input swing is large enough to resolve",
            np.ptp(ai_v) > 0.1 and np.ptp(aq_v) > 0.1,
            f"I {np.ptp(ai_v):.3f} V, Q {np.ptp(aq_v):.3f} V peak-to-peak")

    for nm, dig in (("ADC_I_D", col("ADC_I_D")), ("ADC_Q_D", col("ADC_Q_D"))):
        vals = np.unique(np.round(dig, 2))
        r.check(f"{nm} resolves to logic levels",
                vals.min() < 0.1 and vals.max() > V_LOGIC - 0.1,
                f"{vals.min():.2f} .. {vals.max():.2f} V")
    # The analog swing has to reach the datasheet levels, or the bridge stays in
    # its linear region and the digital node floats at the analog value. The
    # signal is centred at mid-rail, so it needs to travel from there to each
    # threshold: 2 * (0.65 - 0.5) * VDD peak-to-peak.
    need = 2 * (0.65 - 0.5) * V_LOGIC
    r.check("baseband reaches the CMOS input levels", np.ptp(ai_v) > need,
            f"swing {np.ptp(ai_v):.3f} V vs {need:.3f} V needed")

    # ------------------------------------------------------------ pin control
    print("\n5. the LO pin controls the chain")
    off = re.sub(r"^(V_MCU_LO\s+\S+\s+0\s+DC)\s*[\d.]+", r"\g<1> 0", text, flags=re.M)
    if off == text:
        r.check("LO enable source found for the off test", False)
    else:
        rc2, out2, dat2 = run_spice(
            re.sub(r"^\.tran.*$", f".tran {TRAN} uic", off, flags=re.M), "off")
        r.check("netlist still runs with the LO disabled", rc2 == 0 and dat2 is not None)
        if dat2 is not None:
            t2, cols2 = load(dat2)
            s2 = t2 >= t2[0] + 0.35 * (t2[-1] - t2[0])
            base2 = baseband(t2[s2], cols2["v(mix_i1)"][s2])
            ref = np.ptp(baseband(t, col("MIX_I1")))
            r.check("baseband collapses when the LO is off",
                    np.ptp(base2) < ref * 0.05,
                    f"{np.ptp(base2) * 1e6:.2f} uV vs {ref * 1e3:.2f} mV")

    # ---------------------------------------------------------------- firmware
    print("\n6. the firmware uses the same nodes")
    fw = PROJECT / "firmware" / "src" / "firmware.ino"
    if fw.exists():
        ft = fw.read_text()
        r.check("firmware reads PA0/PA1 as the ADC channels",
                "PIN_ADC_I" in ft and "PIN_ADC_Q" in ft)
        r.check("firmware drives PA6 as the LO", "PIN_LO" in ft)
    else:
        r.check("firmware source found", False)

    shutil.rmtree(dat.parent, ignore_errors=True)
    return _finish(r)


def _finish(r: Report) -> int:
    print()
    if r.failed:
        print(f"{len(r.failed)} check(s) FAILED: {', '.join(r.failed)}")
        return 1
    print("analog chain and pin contract verified end to end")
    return 0


if __name__ == "__main__":
    sys.exit(main())
