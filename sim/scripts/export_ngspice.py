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


def _ohm(text: str) -> float | None:
    """Resistance in ohms from a SKiDL value, or None if it is not one.

    The netlist uses the compact forms -- '4k7' for 4.7k, '10k', '2k2' -- so the
    value goes through si_value for normalisation before the SI suffix is
    applied. A bead's '600R@100MHz' rating is not a resistance and returns None.
    """
    raw = str(text).strip()
    if not raw or "@" in raw:
        return None
    m = re.match(r"^([0-9.]+)([a-zA-Z]*)$", si_value(raw))
    if not m:
        return None
    scale = {"": 1.0, "r": 1.0, "k": 1e3, "m": 1e6, "meg": 1e6, "g": 1e9}
    suffix = m.group(2).lower()
    if suffix not in scale:
        return None
    return float(m.group(1)) * scale[suffix]


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

# Common-gate LNA: a transconductance stage, not a voltage amplifier.
#
# gm and Iq are for a 2N7002 at a few milliamps. Against the 18 uH drain choke
# (j809 ohm at 7.15 MHz) this gives a stage gain of about 12, and Iq sets the
# drain's DC drop across the choke's winding resistance.
LNA_GM = 15.0e-3
LNA_IQ = 5.0e-3

# Drain load for the common-gate stages. 2.2 k against gm ~5 mS sets the
# operating point and is what makes the DC solution unique.
LNA_DRAIN_R = 2200.0

# Gate quiescent bias and drain quiescent voltage for the common-gate stages.
# Fallback only: the real bias is read from each stage's own divider by
# _lna_gate_bias(), because the two stages are biased differently.
LNA_GATE_BIAS = 1.65

# Zero-IF mixer: multiplies the filtered RF by the quadrature LO pair.
MIX_GAIN = 0.5

# 90-degree hybrid coupler: ~3 dB insertion loss.
COUPLER_GAIN = 0.707

# Baseband buffer: unity-gain, soft-clipped, biased to mid-rail.
#
# The bias matters: an STM32 ADC input spans 0..VDD, so a bipolar baseband
# signal cannot be digitised unless it sits at mid-rail. Without BB_VMID the
# buffer output is centred on 0 V, the adc_bridge never crosses its threshold,
# and ADC_I_D / ADC_Q_D stay low no matter what the RF chain is doing.
BB_GAIN = 1.0
BB_CLIP = 1.65
BB_VMID = 1.65

# --- LO generator and quadrature splitter -----------------------------------
# The LT5560 has an internal 90-degree phase shifter on the LO, so the LO has to
# be a real oscillating waveform for I and Q to mean anything.
#
# LO tone, chosen to sit beside the RF input so direct conversion produces a
# baseband beat the ADC can actually sample. LO = RF + LO_IF_OFFSET.
RF_FREQ = 7.15e6

# Test stimulus amplitude.
#
# This is a *test* level, not a signal a receiver would normally see: a real
# antenna delivers microvolts, and this chain's modelled stages deliver roughly
# 21 dB of gain from antenna to baseband, so a 1 mV tone lands about 5 mV on the
# ADC -- far short of the +/-0.35 V the adc_bridge needs to change state. 50 mV
# puts the baseband near 0.5 V peak-to-peak about mid-rail, which exercises the
# whole digital boundary. Raising the stimulus is honest; raising a single
# op-amp's gain to 150 would not be.
RF_LEVEL = 100.0e-3

# IF offset. Direct-conversion receivers deliberately run a small non-zero IF
# rather than converting exactly to DC: DC sits in the 1/f noise corner and right
# on the transmiter's own LO leakage. 50 kHz is an ordinary choice for a 7 MHz
# band, and it also puts several cycles of baseband inside the simulated window
# so the I/Q phase can be measured rather than assumed.
LO_IF_OFFSET = 50.0e3
LO_FREQ = RF_FREQ + LO_IF_OFFSET
V_LOGIC = 3.3

# --- STM32F103C8T6 pin model -------------------------------------------------
# Values below are taken from the datasheet, not chosen for convenience. The
# part is the STM32F103x8/xB medium-density line (DS5319).
#
# Output drive, Table 30 "Output voltage characteristics" (CMOS port,
# 2.7 V < VDD < 3.6 V). At IIO = 20 mA the datasheet gives
#     VOL <= 1.3 V      and      VOH >= VDD - 1.3 V
# which is the self-consistent test point: both edges imply the same resistance,
#     pull-down = VOL / IIO       = 1.3 / 0.020 = 65 ohm
#     pull-up   = (VDD - VOH)/IIO = 1.3 / 0.020 = 65 ohm
# The 8 mA row looks lower (VOL 0.4 V, VOH 2.4 V) but is not a resistance:
# VOH is floored at 2.4 V for CMOS compatibility rather than tracking the drop,
# so it would imply 112 ohm on the pull-up edge against 50 ohm on the pull-down
# edge. 65 ohm is the figure that means the same thing in both directions.
MCU_PIN_ROUT = 65.0

# Table 29 "I/O static characteristics": I/O pin capacitance CIO = 5 pF max.
# This is the pin's own load; the board adds to it, but the datasheet number is
# what the pin model claims.
MCU_PIN_CIO = 5.0

# Table 29, CMOS port input thresholds: VIL <= 0.35*VDD and VIH >= 0.65*VDD.
# The datasheet also lists TTL figures (VIL <= 0.8 V, VIH >= 2.0 V) and states
# the I/Os are "CMOS and TTL compliant"; the CMOS pair is the stricter one and is
# what a 3.3 V port is characterised against, so that is what the bridge uses.
MCU_VIL = 0.35 * V_LOGIC          # 1.155 V
MCU_VIH = 0.65 * V_LOGIC          # 2.145 V

# Schmitt hysteresis, Table 29: 200 mV or 5% VDD. The gap between VIL and VIH
# above is far wider than this -- the datasheet quotes the levels for DC noise
# immunity, not as a switching band.
MCU_VHYS = 0.200

# Input leakage, Table 29: +/-1 uA for standard I/O, +/-3 uA for 5 V tolerant.
# PA0/PA1/PA6 are standard (not FT), so 1 uA. Against the tens of millivolts of
# baseband swing this is negligible, but it is why the bridge input is not ideal.
MCU_ILEAK = 1.0e-6

# PA6 is modelled as the timer output it really is: a square wave with finite
# edges and a real drive impedance. Both matter -- see the notes on the emitted
# lines. The drive impedance is the MCU pin's own, from the table above.
LO_OUT_R = MCU_PIN_ROUT
LO_PERIOD = 1.0 / LO_FREQ

# Quadrature all-pass: out = in - 2/(1+sRC), i.e. H = (sRC-1)/(sRC+1). That is
# flat in magnitude and hits exactly +90 deg at omega = 1/RC, so setting
# R*C = 1/(2*pi*f_LO) puts the quadrature point on the LO fundamental. The
# 180 deg / 0 deg endpoints at DC and HF are why a single op-amp all-pass is
# the standard quadrature splitter.
LO_QA_R = 10.0e3
LO_QA_C = 1.0 / (2.0 * 3.141592653589793 * LO_FREQ * LO_QA_R)

# DC resistance per inductor value, ohm. An RF choke is a few ohm; the
# bandstop inductor is a larger metal part.
_INDUCTOR_DCR = {
    "18u": 4.0,
    "100u": 8.0,
}

# Nominal inductance per value, henry. Paired with the DCR above so the chokes
# are real L+R series elements rather than bare resistors.
_INDUCTOR_H = {
    "18u": 18e-6,
    "100u": 100e-6,
}

# Ferrite bead DC resistance, ohm. Beads are quoted by their impedance at a
# frequency, not their DC resistance; a 600R@100MHz bead is milliohms at DC.
BEAD_DCR = 0.5


def _parse_inductance(value: str) -> float:
    """Nominal inductance in henry from a value like '18u' or a '600R@100MHz'."""
    m = re.match(r"\s*([0-9.]+)\s*([numkMG]?)", value)
    if not m:
        return 1e-6
    scale = {"": 1.0, "n": 1e-9, "u": 1e-6, "m": 1e-3, "k": 1e3, "M": 1e6}
    return float(m.group(1)) * scale[m.group(2)]


def _lna_gate_bias(circuit, gate_node: str) -> float:
    """DC gate bias for a common-gate LNA, read from its own divider.

    The two stages are biased differently -- U1 from 10k/10k (VCC/2) and U2
    from 4.7k/2.2k (0.319 * VCC). Hard-coding VCC/2 put a -4.6 V offset on the
    second stage's drain, so the bias is measured from the netlist instead.

    Returns the divider's Thevenin voltage, or LNA_GATE_BIAS if the divider
    cannot be identified.
    """
    hi = lo = None
    rails = {"+3V3", "+3V3_ANA", "VCC_3V3", "VCC_3V3_ANA"}
    grounds = {"GND", "0"}
    for p in circuit.parts:
        if not str(p.ref).startswith("R"):
            continue
        r = _ohm(str(getattr(p, "value", "")))
        if r is None:
            continue
        try:
            a, b = pin_net(p, "1"), pin_net(p, "2")
        except (KeyError, ValueError):
            continue
        # pin_net returns the ngspice-mapped node name, and the two pins may be
        # in either order, so both ends are tested against each rail.
        if a == gate_node and b in rails:
            hi = r
        elif b == gate_node and a in rails:
            hi = r
        elif a == gate_node and b in grounds:
            lo = r
        elif b == gate_node and a in grounds:
            lo = r
    if not hi or not lo:
        return LNA_GATE_BIAS
    return V_LOGIC * lo / (hi + lo)


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
            elif ref.startswith("L") and "@" not in str(getattr(p, "value", "")):
                # Note the "@" test: L_MIX is a ferrite bead, not an inductor,
                # but its reference starts with L. Without this it was emitted
                # as a 600 H inductor, which shorted the mixer's supply.
                # An RF choke is an inductor with a winding resistance, and the
                # two are wildly different at 7 MHz: 18 uH is j809 ohm there but
                # a few ohm of copper at DC. Emitting only the winding resistance
                # put a 4 ohm load on the LNA drains and forced the stage to be
                # an ideal VCVS to make any gain at all -- which is what let the
                # drain swing to -163 V and drag the analog rail to -1.45 V.
                # Series L + R is the real part, and the series R also gives the
                # inductor branch the DC path it needs to avoid a singular
                # matrix.
                r_dcr = _INDUCTOR_DCR.get(str(getattr(p, "value", "")), None)
                henry = _INDUCTOR_H.get(str(getattr(p, "value", "")), None)
                if r_dcr is None:
                    r_dcr = 4.0
                if henry is None:
                    henry = _parse_inductance(value)
                mid = f"{ref}_MID"
                passives.append(f"L{ref} {a} {mid} {henry:.4g}")
                passives.append(
                    f"R{ref} {mid} {b} {r_dcr:.3g}"
                    f"  $ {value} RF choke winding resistance"
                )
            elif ref.startswith("C"):
                passives.append(f"C{ref} {a} {b} {si_value(value)}")
            else:  # FB ferrite bead
                # A bead is rated by its impedance at a frequency ("600R@100MHz"),
                # but its DC resistance is what sets the rail it feeds -- a bead
                # with 600 ohm of DC resistance is not a supply filter, it is a
                # resistor, and the analog rail never came up. Use the DC figure.
                passives.append(
                    f"R{ref} {a} {b} {BEAD_DCR:.3g}"
                    f"  $ {value} ferrite bead, DC resistance"
                )
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
            # Common-gate LNA as a transconductance into the choke-fed drain.
            #
            # An ideal VCVS here is not a stage model, it is a current source
            # with no limit: it drove the drain to -163 V and pulled enormous
            # current through the choke, dragging the analog rail to -1.45 V.
            # A real 2N7002 draws a bounded quiescent current and turns the
            # gate's AC swing into a drain current of gm * v_ac, leaving the
            # drain voltage to be whatever the choke and drain load make of it.
            #
            # The gate is DC-biased by its resistor divider and AC-coupled from
            # the RF input, so the bias must be referenced out: it is not
            # signal. The bias is read from the actual divider rather than
            # assumed -- the two stages use different dividers (10k/10k and
            # 4.7k/2.2k), and assuming VCC/2 for both put a -4.6 V offset on
            # LNA2's drain.
            bias = _lna_gate_bias(circuit, g)
            actives.append(
                f"G_{ref} {d} 0 VALUE = {{{LNA_IQ:.4g}}} + {{{LNA_GM:.4g}}}"
                f" * (V({g}) - {bias:.4g})"
                f"  $ common-gate LNA, gm into the choke-fed drain"
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
            qa = f"MIX_{ref}_LOQ"
            actives.append(f"R_{ref}_BIASP {inp} 0 1Meg")
            actives.append(f"R_{ref}_BIASN {inn} 0 1Meg")
            actives.append(f"R_{ref}_DIFF {inp} {d} 1")
            actives.append(f"R_{ref}_DIFN {inn} {d} 1")
            # The LT5560 shifts the LO 90 deg internally. That splitter is
            # modelled once for the receiver (the LOQ_SPLIT block) and the
            # mixer reads it here, so I and Q are a genuine quadrature pair
            # rather than one channel wired to zero.
            actives.append(
                f"B_{ref}_I {i1} 0 V = {MIX_GAIN} * V({d}) * V({lo})"
            )
            actives.append(
                f"B_{ref}_Q {q1} 0 V = {MIX_GAIN} * V({d}) * V({qa})"
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
            #
            # The BB_VMID term is the DC bias the real buffer needs: the mixer
            # output is a zero-mean baseband signal, but an ADC input can only
            # sample 0..VDD, so the signal has to ride at mid-rail for the
            # positive and negative halves both to be visible to the firmware.
            actives.append(
                f"B_{ref}_I {op_} 0 V = {BB_VMID} + {BB_CLIP} * tanh("
                f"{BB_GAIN} * V({d1}) / {BB_CLIP})"
                f"  $ I-channel baseband buffer, biased to mid-rail"
            )
            actives.append(
                f"B_{ref}_Q {oq} 0 V = {BB_VMID} + {BB_CLIP} * tanh("
                f"{BB_GAIN} * V({d2}) / {BB_CLIP})"
                f"  $ Q-channel baseband buffer, biased to mid-rail"
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
        # Behavioural and linear-dependent sources carry an expression after
        # their node list, and those tokens are not nodes. `G_LNA ... VALUE =
        # {0.005} + {0.015} * (V(...) - 1.65)` was contributing "VALUE", "=",
        # "+" and the brace literals as node names, and every one of them then
        # got a bias resistor -- hence "rb==gnd 1gig is not a valid resistor".
        # A POLY(n) control list is the same trap.
        if node_cols and len(parts) > 3:
            kw = parts[3].upper()
            if kw in ("V", "VALUE"):
                node_cols = (1, 2)
            elif kw.startswith("POLY"):
                node_cols = (1, 2)
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

    # Values the header text interpolates. Kept here rather than inline so the
    # LO geometry stays derived from one place.
    lo_qa_r = LO_QA_R
    lo_qa_c = LO_QA_C
    rf_freq = RF_FREQ
    rf_level = RF_LEVEL
    adc_low = MCU_VIL
    adc_high = MCU_VIH
    pin_rout = MCU_PIN_ROUT
    pin_cio = MCU_PIN_CIO
    v_logic = V_LOGIC
    lo_out_r = LO_OUT_R
    lo_period = LO_PERIOD * 1e9      # PULSE wants nanoseconds

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
* The thresholds are the STM32F103's own CMOS input levels from Table 29:
* VIL <= 0.35*VDD and VIH >= 0.65*VDD. Between in_low and in_high an adc_bridge
* is a resistance rather than a switch, so these are the digital levels the pin
* would resolve; the analog node ADC_I / ADC_Q is what the ADC actually samples
* and carries the full amplitude.
*
* Note PA0 and PA1 are analog inputs, so strictly no digital threshold applies
* to them -- ngspice cannot hand an analog level into an event-driven domain, so
* this is a 1-bit view of the sampled value, not a claim about the ADC.
.model m_adc adc_bridge(in_low={adc_low:.4g} in_high={adc_high:.4g})

A_ADC_I [ADC_I] [ADC_I_D] m_adc
A_ADC_Q [ADC_Q] [ADC_Q_D] m_adc

* --- MCU output pins, driven by the co-simulation host ---
* `alter V_MCU_LO 3.3` is how sdr_picsimlab_cosim.py pushes a PA6 pin state.
V_MCU_GAIN MCU_GAIN 0 DC 0

* --- PA6: the LO timer output ---------------------------------------------
* PA6 does not carry a logic level in this circuit, it carries a *waveform*.
* It is the TIM3_CH1 output that clocks the mixer.
*
* The square wave is a native PULSE source with finite edges rather than a
* behavioural comparator. A hard ternary_fcn threshold is an ideal voltage step
* with no impedance behind it, and into the pin's pF of capacitance ngspice
* collapses the timestep on the first transition and aborts the run -- which is
* what "timestep too small; trouble with node lo_drv" was. A real CMOS timer
* output has a finite edge and a real drive impedance, both of which PULSE plus
* R_LO_OUT reproduce.
*
* The period is the timer's: f = 1/per. The host retunes by
* `alter V_LO_TONE PULSE(0 3.3 0 2n 2n <tp> <per>)`, which is what writing the
* ARR preload register does on the MCU side.
V_LO_TONE LO_TONE 0 PULSE(0 {v_logic} 0 2n 2n {lo_period / 2:.6g}n {lo_period:.6g}n)

* V_MCU_LO is the host-alterable pin state, the API the co-simulation driver
* already uses. It ships enabled (3.3 V) so a standalone run shows the whole
* chain working; `alter V_MCU_LO 0` stops the timer and the LO with it.
* Multiplying by it either passes the waveform through unchanged or scales it to
* exactly zero, so gating adds no new discontinuity for the solver -- unlike a
* comparator would.
B_LO_GATE LO_DRV 0 V = V(LO_TONE) * V(MCU_EN) / {v_logic}
V_MCU_LO  MCU_EN 0 DC {v_logic}

* Comparator/timer output drive impedance.
R_LO_OUT LO_DRV MCU_LO {lo_out_r}

* --- LO quadrature splitter (the LT5560's internal 90 deg phase shifter) ----
* H = 1 - 2/(1+sRC) = (sRC-1)/(sRC+1): unity magnitude everywhere and exactly
* +90 deg at omega = 1/RC, so R*C = 1/(2*pi*f_LO) puts the quadrature point on
* the LO fundamental. This is the standard single-op-amp all-pass. Without it
* the Q channel has no phase shift to work with and is identically zero.
* LO_QA_LP is the RC lowpass node; the E source subtracts twice it from the
* direct path, so the driven output node is separate from the RC node.
R_LO_QA  LO LO_QA_LP {lo_qa_r:.6g}
C_LO_QA  LO_QA_LP 0 {lo_qa_c:.6g}
E_LO_QA  MIX_U_MIX1_LOQ 0 POLY(2) LO 0 LO_QA_LP 0 0 1 -2

* PA6/PA7 on an F103 in push-pull. The output impedance and the pin capacitance
* are the datasheet's own figures -- see MCU_PIN_ROUT and MCU_PIN_CIO in
* export_ngspice.py for the derivation from Table 30 and Table 29. A real edge
* is finite, which is what stops the transient solver from taking an unbounded
* step across every GPIO transition.
*
* These land on LO_SRC, which is the SKiDL net that carries PA6, not on the
* mixer's LO port. The mixer's own 50 ohm input impedance is RR_LO further along
* the chain.
R_PIN_LO   MCU_LO   LO_SRC {pin_rout}
C_PIN_LO   LO_SRC   0      {pin_cio}p
R_PIN_GAIN MCU_GAIN GAIN   {pin_rout}
C_PIN_GAIN GAIN    0      {pin_cio}p

* ADC inputs are high-impedance; the bridge presents the analog level as
* digital, so all the firmware sees is the thresholded state.
R_D_ADC_I_D ADC_I_D 0 1Gig
R_D_ADC_Q_D ADC_Q_D 0 1Gig

* ---- RF input -------------------------------------------------------------
* S11-based antenna networks from export_antenna_ngspice.py can be substituted
* here; this is a matched source standing in for a flat test signal. The tone is
* offset from the LO by LO_IF_OFFSET, so direct conversion lands the signal in
* the baseband where the ADC can sample it instead of at DC. The amplitude is a
* test level: see RF_LEVEL in export_ngspice.py.
V_ANT RF_IN 0 SIN(0 {rf_level:.4g} {rf_freq:.6g} 0 0)
R_TERM_SRC RF_IN GND 50

* Printed so a standalone run shows the boundary working: the digital side is
* readable, and so is the analog node the dac_bridge drives.
"""

    # Note: no str.join() here. Wrapping an already-joined string in join()
    # iterates its characters and emits one per line.
    body = "\n".join(body_lines)

    analysis = f"""
* ---- transient analysis ---------------------------------------------------
* Long enough to resolve the {LO_IF_OFFSET / 1e3:.0f} kHz baseband beat that direct
* conversion produces from the {RF_FREQ / 1e6:.2f} MHz tone against the LO.
*
* uic skips the DC operating point, which the LO oscillator cannot have: it is a
* relaxation oscillator, so every DC state it could rest in is unstable and
* ngspice's op solve fails with "timestep too small". Starting from the default
* all-zero state is both correct and sufficient -- C_LO_PER starts discharged,
* so the comparator starts high and the oscillator starts on its own.
.tran 20n 400u uic

.control
run
* wrdata, not print: `print` wraps its table at 80 columns and silently drops
* every vector past the fifth, which is how a dead Q channel hid behind a table
* that looked complete. wrdata writes every column it is given.
set wr_vecnames
wrdata sdr_trace.dat v(ADC_I) v(ADC_Q) v(ADC_I_D) v(ADC_Q_D)
+ v(MCU_LO) v(LO_SRC) v(LO) v(MIX_U_MIX1_LOQ)
+ v(MIX_U_MIX1_D) v(MIX_I1) v(MIX_Q1) v(BB_U_OP1_D1)
* Console summary only -- three vectors is what fits without truncation.
print v(ADC_I) v(ADC_Q) v(ADC_I_D)
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