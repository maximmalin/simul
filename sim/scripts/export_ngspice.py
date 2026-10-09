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

# Zero-IF mixer: multiplies the filtered RF by the quadrature LO pair. The LO
# is modelled as a fixed-amplitude phasor since the STM32 drives it from a
# timer; its frequency is swept by .tran with a parametrised period.
MIX_GAIN = 0.5

# Baseband buffer: unity-gain voltage follower, limited to the ADC input range.
BB_GAIN = 1.0
BB_CLIP = 1.65


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
                passives.append(f"L{ref} {a} {b} {si_value(value)}")
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
            actives.append(
                f"B_{ref}_I {op_} 0 V = limit({BB_GAIN}*V({d1}), {BB_CLIP})"
                f"  $ I-channel baseband buffer"
            )
            actives.append(
                f"B_{ref}_Q {oq} 0 V = limit({BB_GAIN}*V({d2}), {BB_CLIP})"
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

* ---- local oscillator -----------------------------------------------------
* The STM32 drives LO from its timer on PA6. Standalone (no co-simulation)
* this is an idealised unit sinusoid at 7.15 MHz; under co-simulation the PicSimLab
* bridge replaces LO_SRC with the real MCU pin state, so the mixer sees
* an actual square/timer waveform rather than a clean tone.
V_LO_SRC LO_SRC 0 DC 0
V_LO LO 0 SIN(0 1.0 7.15Meg 0 0)

* ---- RF input -------------------------------------------------------------
* S11-based antenna networks from export_antenna_ngspice.py can be substituted
* here; this is a matched source standing in for a flat test signal.
V_ANT RF_IN 0 SIN(0 1m 7.15Meg 0 0)
R_TERM_SRC RF_IN GND 50
"""

    # Note: no str.join() here. Wrapping an already-joined string in join()
    # iterates its characters and emits one per line.
    body = "\n".join(body_lines)

    analysis = """
* ---- transient analysis ---------------------------------------------------
.tran 1u 20u

.control
run
print v(ADC_I) v(ADC_Q)
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