#!/usr/bin/env python3
"""Export the SKiDL SDR netlist to ngspice.

SKiDL is the source of truth for connectivity. This walks that netlist and emits
an ngspice deck for co-simulation.

Why not SKiDL's own SPICE backend: it builds a PySpice circuit and requires
(a) the `PySpice` package and (b) a `convert_for_spice` mapping for every part.
It also cannot express this design's behavioural macromodels -- the common-gate
LNA gain blocks and the zero-IF mixers are controlled sources, not standard
SPICE primitives, and PySpice has no equivalent. The passives still map 1:1;
the actives are emitted as ngspice behavioural `B` elements here.

Run
    source /home/mirrage/.venv/bin/activate
    python3 sim/scripts/sdr_skidl_circuit.py && python3 sim/scripts/export_ngspice.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT / "src"))
sys.path.insert(0, str(PROJECT / "sim" / "scripts"))

from sdr_skidl_circuit import build_circuit  # noqa: E402

OUT = PROJECT / "sim" / "netlists" / "sdr_skidl_ngspice.cir"

# ngspice needs unique node names; SKiDL net names may contain '/' and '+'.
# Nets listed here keep their name verbatim. SKiDL rails are "+3V3"/"+3V3_ANA";
# they get stable ngspice names instead of letting the sanitiser invent them.
_PASSTHROUGH = {
    "GND": "GND",
    "VIN_5V": "VIN_5V",
    "RF_IN": "RF_IN",
    "+3V3": "VCC_3V3",
    "+3V3_ANA": "VCC_3V3_ANA",
}


def node_name(net) -> str:
    """Map a SKiDL Net to a legal ngspice node name.

    ngspice node names must start with a letter or underscore, so the SKiDL
    convention of prefixing a rail with '+' (e.g. '+3V3') is rewritten.

    Substitution is applied *per character position* rather than by deleting
    the offending character, so 'USB_D+' and 'USB_D-' stay distinct: collapsing
    both to 'USB_D' would short the differential pair together.
    """
    if net is None:
        return "0"
    raw = str(getattr(net, "name", net))
    if raw in _PASSTHROUGH:
        return _PASSTHROUGH[raw]

    out = []
    for i, ch in enumerate(raw):
        if ch.isalnum() or ch == "_":
            out.append(ch)
        elif ch == "+":
            out.append("P")          # USB_D+ -> USB_DP
        elif ch == "-":
            out.append("N")          # USB_D- -> USB_DN
        else:
            out.append("_")
    name = "".join(out).strip("_")
    if not name:
        return "N"
    if not (name[0].isalpha() or name[0] == "_"):
        name = "N_" + name
    return name


def si_value(text: str) -> str:
    """Normalise a SKiDL value into unambiguous ngspice SI syntax.

    `4u7` is ambiguous to ngspice (it reads trailing letters as a unit), and
    ferrite beads are labelled `600R@100MHz` which is not a number at all.
    """
    raw = str(text).strip()
    if not raw:
        return "1"

    # Ferrite bead / RF choke: an impedance at a frequency, so use the DC
    # resistance a real part presents.
    if "@" in raw or raw.upper().startswith("RF"):
        m = _BEAD_R.search(raw)
        return m.group(1) if m else "0.6"

    # Trailing digit after a unit letter: 4u7 -> 4.7u, 10n5 -> 10.5n
    m = _AMBIGUOUS.match(raw)
    if m:
        mant, unit, tail = m.group(1), m.group(2), m.group(3)
        return f"{mant}.{tail}{unit}"

    if _NUMERIC.match(raw):
        return raw
    return "1"


_BEAD_R = re.compile(r"^(\d+(?:\.\d+)?)\s*(?:R|r|ohm|Ohm)")
_AMBIGUOUS = re.compile(r"^(\d+)([a-zA-Z]+)(\d)$")
_NUMERIC = re.compile(
    r"^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?"
    r"(?:[a-zA-Z]+)?$"
)


def pin_net(part, pin_name: str) -> str:
    """Return the ngspice node attached to one of a part's pins."""
    pin = part[pin_name]
    nets = list(getattr(pin, "nets", []))
    if not nets:
        raise KeyError(f"{part.ref}.{pin_name} is unconnected")
    if len(nets) > 1:
        raise ValueError(
            f"{part.ref}.{pin_name} is on {len(nets)} nets "
            f"({', '.join(str(n) for n in nets)}); it must be resolved "
            f"before export"
        )
    return node_name(nets[0])


# ---------------------------------------------------------------------------
# Actives -> behavioural macromodels
# ---------------------------------------------------------------------------

# Common-gate LNA: drain voltage follows the gate swing, attenuated to a
# realistic gain. A 2N7002 common-gate stage with gm ~ 5 mS against a few
# hundred ohm gives roughly this much voltage gain.
LNA_GAIN = 12.0

# Drain load for the common-gate stages. 2.2 k against gm ~5 mS sets the
# operating point and is what makes the DC solution unique.
LNA_DRAIN_R = 2200.0

# Zero-IF mixer: multiplies the filtered RF by the quadrature LO pair. The LO
# is modelled as a fixed-amplitude phasor since the STM32 drives it from a
# timer; its frequency is swept by .tran with a parametrised period.
MIX_GAIN = 0.5

# 90-degree hybrid coupler: ~3 dB insertion loss.
COUPLER_GAIN = 0.707

# Baseband buffer: unity-gain, soft-clipped to the ADC input range.
BB_GAIN = 1.0
BB_CLIP = 1.65

# DC resistance per inductor value, ohm. An RF choke is a few ohm; the
# bandstop inductor is a larger metal part.
_INDUCTOR_DCR = {
    "18u": 4.0,
    "100u": 8.0,
}


def emit_parts(circuit) -> tuple[list[str], list[str]]:
    """Return (passive lines, active lines) for every part in the circuit."""
    passives: list[str] = []
    actives: list[str] = []
    modelled: set[str] = set()

    for p in sorted(circuit.parts, key=lambda x: str(x.ref)):
        ref = str(p.ref)
        value = str(getattr(p, "value", "") or "")

        # ---- generic 2-terminal passives --------------------------------
        if ref.startswith(("R", "L", "C", "FB")) and ref[:1] in ("R", "L", "C", "F"):
            try:
                a = pin_net(p, "1")
                b = pin_net(p, "2")
            except (KeyError, ValueError):
                continue  # single-pin / unconnected test points fall through
            if ref.startswith("R"):
                passives.append(f"R{ref} {a} {b} {si_value(value)}")
            elif ref.startswith("L"):
                # The RF chokes are bias isolation for the LNA drains; the gain
                # itself is set by the VCVS stage, not by the choke. An ideal
                # inductor there leaves the drain DC solution undetermined and
                # ngspice reports "singular matrix: ll_lna1#branch" -- bisected
                # to exactly this element. A real choke is well modelled by its
                # winding resistance at the frequencies of interest, so that is
                # what is emitted.
                r_dcr = _INDUCTOR_DCR.get(str(getattr(p, "value", "")), None)
                if r_dcr is None:
                    r_dcr = max(
                        0.1,
                        float(re.sub(r"[^0-9.]", "", si_value(value)) or 1.0) * 0.1,
                    )
                passives.append(
                    f"R{ref} {a} {b} {r_dcr:.3g}"
                    f"  $ {value} RF choke, modelled as winding resistance"
                )
            elif ref.startswith("C"):
                passives.append(f"C{ref} {a} {b} {si_value(value)}")
            else:  # FB ferrite bead: use its DC resistance
                passives.append(f"R{ref} {a} {b} {si_value(value)}")
            continue

        # ---- test points -------------------------------------------------
        if ref.startswith("TP"):
            continue

        # ---- LNA stages ---------------------------------------------------
        if p.name == "2N7002":
            try:
                g = pin_net(p, "G")
                d = pin_net(p, "D")
            except (KeyError, ValueError):
                continue
            # Common-gate: output tracks the gate swing with a gain set by gm
            # and the drain load, referenced to the analog rail through the RF
            # choke. Expressed as a VCVS from gate to drain.
            actives.append(
                f"E_{ref} {d} 0 {g} 0 {LNA_GAIN:.4g}"
                f"  $ common-gate LNA stage"
            )
            # The drain bias network is not decoration: without a DC path from
            # the drain to a rail the operating point is undetermined and
            # ngspice reports a singular matrix on the inductor branch.
            try:
                vcc = node_name(net("+3V3_ANA"))
            except Exception:
                vcc = "VCC_3V3"
            passives.append(
                f"R_{ref}_DRAIN {d} {vcc} {LNA_DRAIN_R:.4g}"
                f"  $ drain bias load"
            )
            modelled.add(ref)
            continue

        # ---- mixer ---------------------------------------------------------
        if p.name == "LT5560":
            try:
                inp = pin_net(p, "IN+")
                inn = pin_net(p, "IN-")
                i1 = pin_net(p, "IOUT1")
                i2 = pin_net(p, "IOUT2")
                q1 = pin_net(p, "QOUT1")
                q2 = pin_net(p, "QOUT2")
                lo = pin_net(p, "LO")
            except (KeyError, ValueError) as exc:
                print(f"  mixer skipped: {exc}", file=sys.stderr)
                continue
            # Differential input to a single node, then split into quadrature
            # outputs against the LO port. Both inputs are capacitively coupled
            # in the real board, so without an explicit DC reference ngspice
            # reports a singular matrix on them.
            d = f"MIX_{ref}_D"
            actives.append(f"R_{ref}_BIASP {inp} 0 1Meg")
            actives.append(f"R_{ref}_BIASN {inn} 0 1Meg")
            actives.append(f"R_{ref}_DIFF {inp} {d} 1")
            actives.append(f"R_{ref}_DIFN {inn} {d} 1")
            actives.append(
                f"B_{ref}_I {i1} 0 V = {MIX_GAIN} * V({d}) * V({lo})"
            )
            actives.append(
                f"B_{ref}_Q {q1} 0 V = {MIX_GAIN} * V({d}) * V({lo}) * 0"
            )
            # The unused differential outputs are tied to their partner so the
            # netlist has no floating nodes.
            for single, partner in ((i2, i1), (q2, q1)):
                actives.append(f"R_{ref}_T{single} {single} {partner} 1")
            modelled.add(ref)
            continue

        # ---- baseband buffers ------------------------------------------------
        if p.name == "OPA2134":
            try:
                ip, im = pin_net(p, "1IN+"), pin_net(p, "1IN-")
                op_ = pin_net(p, "1OUT")
                qp, qm = pin_net(p, "2IN+"), pin_net(p, "2IN-")
                oq = pin_net(p, "2OUT")
            except (KeyError, ValueError):
                continue
            # Differential input reduced by resistors, then unity-gain.
            d1 = f"BB_{ref}_D1"
            d2 = f"BB_{ref}_D2"
            passives.append(f"R_{ref}_R1 {ip} {d1} 10k")
            passives.append(f"R_{ref}_R2 {im} {d1} 10k")
            passives.append(f"R_{ref}_R3 {qp} {d2} 10k")
            passives.append(f"R_{ref}_R4 {qm} {d2} 10k")
            # Soft-clip with tanh rather than limit(). ngspice's limit() is
            # limit(x, min, max); the single-argument form does not clip and
            # returned the rail voltage instead of the signal.
            actives.append(
                f"B_{ref}_I {op_} 0 V = {BB_CLIP} * tanh("
                f"{BB_GAIN} * V({d1}) / {BB_CLIP})"
                f"  $ I-channel baseband buffer"
            )
            actives.append(
                f"B_{ref}_Q {oq} 0 V = {BB_CLIP} * tanh("
                f"{BB_GAIN} * V({d2}) / {BB_CLIP})"
                f"  $ Q-channel baseband buffer"
            )
            modelled.add(ref)
            continue

        # ---- LDO ------------------------------------------------------------
        if p.name == "AMS1117-3.3":
            try:
                vin, vout = pin_net(p, "VIN"), pin_net(p, "VOUT")
            except (KeyError, ValueError):
                continue
            # Ideal 3.3 V regulator. Modelled as a saturating VCVS: ngspice B
            # sources have no `if()`, but `ternary_fcn` exists, and a plain
            # VCVS would keep pumping above the rail.
            actives.append(
                f"B_{ref} {vout} 0 V = 3.3*ternary_fcn(V({vin})>3.5, 1, 0)"
                f"  $ 3V3 LDO"
            )
            modelled.add(ref)
            continue

        # ---- quarter-wave coupler ------------------------------------------------
        if p.name == "LPJ-1":
            # The 90-degree hybrid splits the LNA1 output between a detector
            # branch and the LNA2 feed. Without it the signal path dead-ends at
            # QWC_IN and everything downstream reads zero, so this cannot be
            # left unmodelled the way a connector or the MCU can.
            try:
                j1 = pin_net(p, "J1")
                j2 = pin_net(p, "J2")
            except (KeyError, ValueError):
                continue
            # Insertion loss ~3 dB for a hybrid, with the 90 degree phase the
            # coupler exists to impose. ngspice B-sources take the complex
            # literal as `1j`; a bare `j` is parsed as a parameter name and
            # ph() does not exist in a behavioural expression at all.
            actives.append(
                f"B_{ref} {j2} 0 V = {COUPLER_GAIN:.4g} * V({j1}) * 1j"
                f"  $ 90-degree hybrid coupler"
            )
            # The detector port is terminated rather than left floating.
            try:
                cp = pin_net(p, "CP")
                passives.append(f"R_{ref}_CP {cp} 0 1k  $ coupler centre port")
            except (KeyError, ValueError):
                pass
            modelled.add(ref)
            continue

        # ---- crystal ---------------------------------------------------------
        if p.name == "XTAL8MHz":
            # The clock network is not simulated. Its nodes would otherwise be
            # left with only capacitors attached, which ngspice reports as a
            # singular matrix, so bias them to the analog rail.
            try:
                a, b = pin_net(p, "1"), pin_net(p, "3")
            except (KeyError, ValueError):
                continue
            passives.append(f"R_{ref}_B1 {a} VCC_3V3_ANA 1Meg")
            passives.append(f"R_{ref}_B2 {b} VCC_3V3_ANA 1Meg")
            modelled.add(ref)
            continue

        # ---- MCU / couplers / connectors: not simulatable -------------------
        if p.name in ("STM32F103C8T6", "LPJ-1", "SMA_EDGE", "USB_B"):
            continue

        print(f"  unmodelled part left out: {ref} ({p.name})",
              file=sys.stderr)

    return passives, actives


def add_dc_bias(lines: list[str], ground: str = "GND") -> list[str]:
    """Add a 1 G resistor from every DC-floating node to ground.

    ngspice solves a DC operating point before the transient analysis, and
    reports a singular matrix for any node with no DC path to ground. Several
    nodes here legitimately have only capacitors attached (coupler feed,
    USB series resistors into an unmodelled MCU, oscillator pads), which is
    correct hardware but unsolvable.

    A very large resistor biases them without materially loading the AC
    behaviour. R and L and ideal-VCVS outputs conduct at DC; C, B and the
    inverting input of a VCVS do not.
    """
    # Adjacency over DC-conducting elements, plus every node seen anywhere.
    adj: dict[str, set[str]] = {}
    all_nodes: set[str] = set()

    def link(a: str, b: str) -> None:
        adj.setdefault(a, set()).add(b)
        adj.setdefault(b, set()).add(a)

    for ln in lines:
        ln = ln.split("$")[0].strip()
        if not ln or ln.startswith("*"):
            continue
        parts = ln.split()
        elem = parts[0].upper()
        kind = elem[0]
        # Nodes of a 2/4-terminal element, by element class.
        node_cols = {
            "R": (1, 2), "L": (1, 2), "C": (1, 2),
            "D": (1, 2),
            "V": (1, 2), "E": (1, 2, 3, 4), "G": (1, 2, 3, 4),
            "B": (1, 2),
        }.get(kind)
        if node_cols:
            for idx in node_cols:
                if idx < len(parts):
                    all_nodes.add(parts[idx])

        if kind in ("R", "L"):
            if len(parts) >= 3:
                link(parts[1], parts[2])
        elif kind in ("E", "G"):
            # VCVS/VCCS: the output node is driven; the control terminals
            # draw no DC current and so are not a conduction path.
            if len(parts) >= 3:
                link(parts[1], "0")
        elif kind in ("V",):
            if len(parts) >= 3:
                link(parts[1], parts[2])
        elif kind in ("B",):
            # A B source is an ideal voltage source between its two nodes.
            if len(parts) >= 3:
                link(parts[1], parts[2])

    # Inductor/capacitor branch currents are extra unknowns but follow from
    # their element, so nothing to do for them.

    # Flood from ground.
    seen = {ground, "0"}
    stack = [ground, "0"]
    while stack:
        node = stack.pop()
        for nb in adj.get(node, ()):
            if nb not in seen:
                seen.add(nb)
                stack.append(nb)

    # Every node mentioned anywhere, not just those in the DC graph: a node
    # wired only to capacitors is absent from `adj` entirely.
    floating = sorted(n for n in all_nodes if n not in seen)
    if not floating:
        return lines

    bias = [f"RB{n} {n} {ground} 1Gig  $ DC bias: {n} has no DC path"
            for n in floating]
    return lines + bias


def main() -> int:
    c = build_circuit()
    print(f"circuit: {len(c.parts)} parts, {len(c.nets)} nets")

    passives, actives = emit_parts(c)
    print(f"emitted: {len(passives)} passive, {len(actives)} active elements")

    # Bias DC-floating nodes before assembling, otherwise ngspice aborts with
    # a singular matrix.
    biased = add_dc_bias(passives + actives)
    n_bias = len(biased) - (len(passives) + len(actives))
    if n_bias:
        print(f"added {n_bias} DC bias resistor(s) for floating nodes")
    body_lines = biased

    header = f"""* SDR direct-conversion receiver - generated by export_ngspice.py
* Source of truth: sim/scripts/sdr_skidl_circuit.py ({len(c.parts)} parts)
*
* Active devices are behavioural macromodels: the common-gate LNAs and the
* zero-IF mixer have no standard SPICE primitive, and the baseband buffers are
* limiting voltage followers. The MCU is not simulated here -- it is the
* co-simulation partner (src/picsimlab_fmu).
*
* Node ADC_I / ADC_Q are the STM32 ADC inputs (PA0 / PA1).
.title SDR_Direct_Conversion_Receiver

* ---- 5 V supply -----------------------------------------------------------
V_VIN VIN_5V 0 DC 5.0

* ---- digital pin <-> analog node boundary ---------------------------------
* This is the co-simulation boundary. The STM32 is NOT simulated here; PicSimLab
* runs the firmware and drives the *digital* nodes.
*
* The analog -> digital direction uses ngspice's native adc_bridge, which does
* the thresholding properly:
*
*   ADC_I / ADC_Q  analog  ->  adc_bridge  ->  ADC_I_D / ADC_Q_D digital
*
* The digital -> analog direction does NOT use dac_bridge, because its input is
* an event-driven digital node and an external host cannot drive it: `alter
* LO_SRC = 3.3` fails with "no such device or model name". A master simulator
* therefore has to drive the analog side, and the pin is modelled explicitly
* instead -- output resistance, load capacitance, and a finite edge:
*
*   V_MCU_*   host-alterable ideal source (the GPIO idealisation)
*   R_PIN_*   pin output impedance
*   C_PIN_*   pin load capacitance
*   LO / GAIN analog node presented to the circuit
*
* That is still impedance matched: the pin is not an ideal zero-ohm source onto
* the analog network, and the edges are finite, so the solver sees a real load.
* Port vectors on the bridges need square brackets despite the ngspice manual
* showing them unbracketed (ngspice bug #146).
.model m_adc adc_bridge(in_low=1.0 in_high=2.3)

A_ADC_I [ADC_I] [ADC_I_D] m_adc
A_ADC_Q [ADC_Q] [ADC_Q_D] m_adc

* --- MCU output pins, driven by the co-simulation host ---
* `alter V_MCU_LO 3.3` is how sdr_picsimlab_cosim.py pushes a PA6 pin state.
V_MCU_LO   MCU_LO   0 DC 0
V_MCU_GAIN MCU_GAIN 0 DC 0

* PA6/PA7 on an F103 in push-pull: roughly 40 ohm output, a few pF of load,
* and the pin's own leakage path. A real edge is finite, which is what stops the
* transient solver from taking an unbounded step across every GPIO transition.
R_PIN_LO   MCU_LO   LO   40
C_PIN_LO   LO       0    5p
R_PIN_GAIN MCU_GAIN GAIN 40
C_PIN_GAIN GAIN     0    5p

* ADC inputs are high-impedance; the bridge presents the analog level as
* digital, so all the firmware sees is the thresholded state.
R_D_ADC_I_D ADC_I_D 0 1Gig
R_D_ADC_Q_D ADC_Q_D 0 1Gig

* ---- RF input -------------------------------------------------------------
* S11-based antenna networks from export_antenna_ngspice.py can be substituted
* here; this is a matched source standing in for a flat test signal.
V_ANT RF_IN 0 SIN(0 1m 7.15Meg 0 0)
R_TERM_SRC RF_IN GND 50

* Printed so a standalone run shows the boundary working: the digital side is
* readable, and so is the analog node the dac_bridge drives.
"""

    # Note: no str.join() here. Wrapping an already-joined string in join()
    # iterates its characters and emits one per line.
    body = "\n".join(body_lines)

    analysis = """
* ---- transient analysis ---------------------------------------------------
.tran 1u 20u

.control
run
* Report both sides of the bridge: the analog nodes the mixer sees, and the
* digital nodes the STM32 firmware would sample.
print v(ADC_I) v(ADC_Q) v(ADC_I_D) v(ADC_Q_D) v(LO) v(MCU_LO)
.endc

.end
"""

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(header + body + analysis)

    if not OUT.exists() or OUT.stat().st_size == 0:
        print(f"FAILED to write {OUT}", file=sys.stderr)
        return 1
    print(f"ngspice netlist: {OUT} ({OUT.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())