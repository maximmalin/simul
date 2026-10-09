#!/usr/bin/env python3
"""Self-contained FMI 2.0 co-simulation FMU wrapping the SDR ngspice netlist.

Why self-contained: pythonfmu bundles only this one script. The previous
attempt imported NgspiceBridge from the repository tree, so `instantiateModel`
failed inside the FMU sandbox because the module was not there. Nothing outside
this file is imported at run time.

Build:
    <venv-with-pythonfmu>/bin/pythonfmu build \
        -f sim/scripts/sdr_ngspice_fmu.py -d sim/fmu

Drive:
    from fmpy import simulate_fmu
    simulate_fmu("sim/fmu/SDR_NgspiceFMU.fmu", stop_time=1e-3)
"""

from pythonfmu.fmi2slave import (
    Fmi2Causality,
    Fmi2Initial,
    Fmi2Slave,
    Fmi2Variability,
    Integer,
    Real,
    String,
)

import math
import os
import re
import struct
import subprocess
import tempfile

# ---------------------------------------------------------------------------
# Embedded netlist. A behavioural zero-IF receiver chain: LNA gain, mixer with
# a quadrature LO, and limiting baseband buffers. Kept inline so the FMU needs
# no data files. Point `netlist_path` at sim/netlists/sdr_skidl_ngspice.cir to
# simulate the real SKiDL-derived netlist instead.
# ---------------------------------------------------------------------------

EMBEDDED_NETLIST = """* SDR zero-IF chain (embedded in the FMU)
V_RF  RF_IN 0 SIN(0 10u 7.15Meg 0 0)
R_TERM RF_IN 0 50

* common-gate LNA, voltage gain 12
E_LNA LNA_OUT 0 RF_IN 0 12
R_LNA_D LNA_OUT 0 10k

* zero-IF mixer against the LO port
V_LO LO 0 SIN(0 1.0 7.15Meg 0 0)
R_MIX_D LNA_OUT MIX_D 1
B_MIX_I MIX_I 0 V = 0.5 * V(MIX_D) * V(LO)
B_MIX_Q MIX_Q 0 V = 0.5 * V(MIX_D) * V(LO) * 0.7

* limiting baseband buffers into the STM32 ADC.
* The clip must control on MIX_I, not on its own output node: driving
* ADC_I from V(ADC_I) is an algebraic loop, which ngspice resolves to zero and
* then drops from the output vectors entirely.
B_BB_I ADC_I 0 V = 1.65 * tanh(V(MIX_I) / 1.65)
B_BB_Q ADC_Q 0 V = 1.65 * tanh(V(MIX_Q) / 1.65)
R_LD_I ADC_I 0 10k
R_LD_Q ADC_Q 0 10k

* logic levels the co-simulation host sets to steer the LO and the gain pin
B_STIM LO_STIM 0 V = 0
R_STIM LO_STIM 0 1Meg

.tran 1u 1m
.end
"""

_NUM = r"[-+]?[0-9]*\.?[0-9]+(?:[eE][-+]?[0-9]+)?"


class SdrNgspiceFMU(Fmi2Slave):
    """SDR direct-conversion receiver, ngspice inside an FMI 2.0 slave."""

    author = "Mirrage"
    description = "SDR direct-conversion receiver chain simulated by ngspice"
    version = "1.0.0"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        # ---- inputs ----
        self.rf_amplitude = 10e-6
        self.lo_frequency = 7.15e6
        self.stim_level = 0.0          # 0 or 1, host-driven digital pin

        # ---- outputs ----
        self.adc_i = 0.0
        self.adc_q = 0.0
        self.model_status = 0
        self.status_message = "ok"

        # ---- parameters ----
        self.netlist_path = ""         # empty -> use the embedded netlist
        self.dt = 1e-6

        self._proc = None
        self._raw = None
        self._tmpdir = None
        self._start_time = 0.0
        self._t = 0.0

        def reg_real(name, causality, value, variability=None):
            if variability is None:
                variability = (Fmi2Variability.continuous
                               if causality == Fmi2Causality.input
                               else Fmi2Variability.discrete)
            kw = {"causality": causality, "variability": variability}
            if causality == Fmi2Causality.parameter:
                kw["initial"] = Fmi2Initial.exact
            self.register_variable(Real(name, start=value, **kw))

        reg_real("rf_amplitude", Fmi2Causality.input, self.rf_amplitude)
        reg_real("lo_frequency", Fmi2Causality.input, self.lo_frequency)
        reg_real("stim_level", Fmi2Causality.input, self.stim_level)
        reg_real("dt", Fmi2Causality.parameter, self.dt,
                 Fmi2Variability.fixed)

        # netlist_path is a string parameter; set it after the scalar vars so
        # the default stays empty and the embedded netlist is used.
        self.register_variable(String(
            "netlist_path",
            causality=Fmi2Causality.parameter,
            variability=Fmi2Variability.fixed,
            initial=Fmi2Initial.exact))

        for name in ("adc_i", "adc_q"):
            self.register_variable(Real(
                name, causality=Fmi2Causality.output,
                variability=Fmi2Variability.continuous,
                initial=Fmi2Initial.calculated))
        self.register_variable(Integer(
            "model_status", causality=Fmi2Causality.output,
            variability=Fmi2Variability.discrete,
            initial=Fmi2Initial.calculated))
        self.register_variable(String(
            "status_message", causality=Fmi2Causality.output,
            variability=Fmi2Variability.discrete,
            initial=Fmi2Initial.calculated))

    # -- lifecycle ---------------------------------------------------------

    def exit_initialization_mode(self):
        try:
            self._start()
        except Exception as exc:
            self.model_status = 2
            self.status_message = f"init failed: {exc}"
            raise

    def _start(self):
        if self._proc is not None:
            return
        self._tmpdir = tempfile.mkdtemp(prefix="sdr_fmu_")
        cir = os.path.join(self._tmpdir, "sdr.cir")
        text = EMBEDDED_NETLIST
        if self.netlist_path and os.path.exists(self.netlist_path):
            with open(self.netlist_path) as fh:
                text = fh.read()
        with open(cir, "w") as fh:
            fh.write(text)

        self._raw = os.path.join(self._tmpdir, "sdr.raw")
        self._dat = os.path.join(self._tmpdir, "sdr.dat")
        self._proc = subprocess.Popen(
            ["ngspice", "-b", "-r", self._raw, cir],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, cwd=self._tmpdir,
        )
        if self._proc.wait(timeout=60) != 0:
            err = self._proc.stderr.read().decode(errors="ignore")[-400:]
            raise RuntimeError(f"ngspice batch run failed: {err}")

    def terminate(self):
        if self._proc is not None:
            try:
                self._proc.kill()
            except Exception:
                pass
            self._proc = None
        if self._tmpdir and os.path.isdir(self._tmpdir):
            for name in os.listdir(self._tmpdir):
                try:
                    os.remove(os.path.join(self._tmpdir, name))
                except OSError:
                    pass
            try:
                os.rmdir(self._tmpdir)
            except OSError:
                pass
            self._tmpdir = None

    # -- co-simulation -----------------------------------------------------

    def _read_peak(self):
        """Return (peak |adc_i|, peak |adc_q|) over the whole run.

        Peak rather than the final sample: the outputs are baseband I/Q from a
        7.15 MHz signal, so whichever instant the transient happens to stop at
        is often a zero crossing and would report ~0 regardless of the signal.

        Parses the ngspice rawfile directly. Note `ngspice -a` and
        `.option filetype=ascii` do *not* make the rawfile ASCII -- both leave
        a binary data section -- so the doubles are unpacked directly. The
        header (variable names, point count) is plain text either way.
        """
        if not self._raw or not os.path.exists(self._raw):
            return 0.0, 0.0
        try:
            with open(self._raw, "rb") as fh:
                blob = fh.read()
        except OSError:
            return 0.0, 0.0

        marker = b"Binary:\n"
        cut = blob.find(marker)
        if cut < 0:
            return 0.0, 0.0
        header = blob[:cut].decode("ascii", errors="ignore")
        body = blob[cut + len(marker):]

        n_points = None
        names: list[str] = []
        # A separate flag is required: testing `names == []` would go false
        # after the first append and silently collect only `time`.
        in_vars = False
        for line in header.splitlines():
            line = line.strip()
            # The rawfile header uses "No. Points:" / "No. Variables:", not
            # "No. of Points:" / "No. of Variables:" as some docs show.
            if line.startswith("No. Points"):
                try:
                    n_points = int(line.split(":")[1])
                except (IndexError, ValueError):
                    n_points = None
            elif line.startswith("Variables:"):
                in_vars = True
            elif in_vars and "\t" in line:
                parts = line.split("\t")
                if len(parts) >= 2 and parts[0].strip().isdigit():
                    names.append(parts[1].strip().lower())

        if not names or not n_points:
            return 0.0, 0.0

        try:
            i_i = names.index("v(adc_i)")
            i_q = names.index("v(adc_q)")
        except ValueError:
            return 0.0, 0.0

        stride = len(names)
        need = stride * n_points * 8
        if len(body) < need:
            body = body[:len(body) - (len(body) % 8)]
            n_points = min(n_points, len(body) // (stride * 8))
            if n_points <= 0:
                return 0.0, 0.0
            need = stride * n_points * 8

        vals = struct.unpack("<%dd" % (stride * n_points), body[:need])

        pi = pq = 0.0
        for p in range(n_points):
            base = p * stride
            pi = max(pi, abs(vals[base + i_i]))
            pq = max(pq, abs(vals[base + i_q]))
        return pi, pq

    def _rerun(self, tstop):
        """Alter the sources and re-run the batch simulation up to tstop."""
        if self._proc is None:
            return
        # ngspice -b was already run to completion at init; re-run with the
        # inputs altered. A fresh process avoids depending on interactive
        # pipe semantics inside an FMU.
        cir = os.path.join(self._tmpdir, "sdr.cir")
        with open(cir) as fh:
            text = fh.read()
        text = re.sub(r"^V_RF\s.*$",
                      f"V_RF RF_IN 0 SIN(0 {self.rf_amplitude:.6g} "
                      f"{self.lo_frequency:.6g} 0 0)", text, flags=re.M)
        text = re.sub(r"^V_LO\s.*$",
                      f"V_LO LO 0 SIN(0 1.0 {self.lo_frequency:.6g} 0 0)",
                      text, flags=re.M)
        text = re.sub(r"^B_STIM\s.*$",
                      f"B_STIM LO_STIM 0 V = {self.stim_level:.6g}",
                      text, flags=re.M)
        text = re.sub(r"^\.tran\s.*$", f".tran {self.dt:.6g} {tstop:.6g}",
                      text, flags=re.M)
        with open(cir, "w") as fh:
            fh.write(text)

        try:
            self._proc.kill()
        except Exception:
            pass
        self._proc = subprocess.Popen(
            ["ngspice", "-b", "-r", self._raw, cir],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, cwd=self._tmpdir,
        )
        self._proc.wait(timeout=120)

    def do_step(self, current_time, step_size):
        self._t = current_time + step_size
        try:
            self._rerun(self._t)
            self.adc_i, self.adc_q = self._read_peak()
            self.model_status = 0
            self.status_message = "ok"
            return True
        except Exception as exc:
            self.model_status = 2
            self.status_message = str(exc)
            return False


if __name__ == "__main__":
    print("Build: pythonfmu build -f sim/scripts/sdr_ngspice_fmu.py -d sim/fmu")