#!/usr/bin/env python3
"""SDR direct-conversion receiver - single unified SKiDL netlist generator.

SKiDL's only job here is to be the source of truth for connectivity and to emit
netlists. It performs **no** simulation and is **not** part of the
co-simulation loop.

Pipeline position:

    SKiDL (this file)
        |  emits sim/netlists/sdr_skidl_ngspice.net
        v
    ngspice / FMU  <--  co-simulation lives here
        |             (src/picsimlab_fmu/, sim/scripts/sdr_picsimlab_cosim.py)
        v
    antenna S-parameter models (sim/scripts/antenna_fdtd.py)

Written against the SKiDL 2.3.0 API
(`SchLib(tool=SKIDL).add_parts(Part(..., dest=TEMPLATE, pins=[Pin(...)]))`),
which differs substantially from 1.x: there is no `Part('R', value=...)`
constructor and no `C`/`R`/`L` shorthand.

Outputs
    hardware/kicad/sdr_receiver.kicad_sch    KiCad schematic
    sim/netlists/sdr_skidl_ngspice.net        ngspice netlist

Run
    source /home/mirrage/.venv/bin/activate
    python3 sim/scripts/sdr_skidl_circuit.py
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT / "src"))

from skidl import (  # noqa: E402
    Alias, Circuit, Net, Part, Pin, SchLib, SKIDL, TEMPLATE, Pin as SkidlPin,
    generate_netlist, logger as skidl_logging,
)
from skidl.pin import pin_types  # noqa: E402

PWR_IN = pin_types.PWRIN
PWR_OUT = pin_types.PWROUT
INPUT = pin_types.INPUT
OUTPUT = pin_types.OUTPUT
BIDIR = pin_types.BIDIR
PASSIVE = pin_types.PASSIVE
NC = pin_types.NOCONNECT

# ---------------------------------------------------------------------------
# Part library
# ---------------------------------------------------------------------------

def _part(name, ref_prefix, footprint, description, pins, datasheet="",
          keywords=""):
    """Build one TEMPLATE Part with an explicit pin list."""
    return Part(**{
        "name": name,
        "dest": TEMPLATE,
        "tool": SKIDL,
        "aliases": Alias({name}),
        "ref_prefix": ref_prefix,
        "fplist": [footprint],
        "footprint": footprint,
        "description": description,
        "datasheet": datasheet,
        "keywords": keywords,
        "pins": [
            Pin(num=str(i + 1), name=p[0], func=p[1])
            for i, p in enumerate(pins)
        ],
    })


SDR_PARTS = SchLib(tool=SKIDL).add_parts(*[
    # --- passives -------------------------------------------------------
    _part("C", "C", "Capacitor_SMD:C_0805_2012Metric", "Capacitor",
          [("1", PASSIVE), ("2", PASSIVE)]),
    _part("R", "R", "Resistor_SMD:R_0805_2012Metric", "Resistor",
          [("1", PASSIVE), ("2", PASSIVE)]),
    _part("L", "L", "Inductor_SMD:L_1210_3225Metric", "Inductor",
          [("1", PASSIVE), ("2", PASSIVE)]),
    _part("FB", "FB", "Inductor_SMD:L_0805_2012Metric", "Ferrite bead",
          [("1", PASSIVE), ("2", PASSIVE)]),

    # --- RF front end ---------------------------------------------------
    _part("2N7002", "Q", "Package_TO_SOT_SMD:SOT-23",
          "N-channel MOSFET 60 V, used as the common-gate LNA",
          [("D", PWR_OUT), ("G", INPUT), ("S", PWR_OUT)],
          keywords="FET LNA"),
    _part("LPJ-1", "X", "Connector:Pin_4_1.27mm",
          "90 degree hybrid coupler",
          [("J1", PASSIVE), ("J2", PASSIVE), ("CP", PASSIVE), ("GND", PWR_IN)]),
    _part("LT5560", "U", "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm",
          "Double-balanced active mixer 500 MHz - 2.5 GHz",
          [("IN+", INPUT), ("IN-", INPUT),
           ("IOUT1", OUTPUT), ("IOUT2", OUTPUT),
           ("QOUT1", OUTPUT), ("QOUT2", OUTPUT),
           ("LO", INPUT), ("VCC", PWR_IN), ("GND", PWR_IN)],
          datasheet="https://www.analog.com/media/en/technical-documentation/data-sheets/5560fc.pdf",
          keywords="mixer zero-IF"),
    _part("OPA2134", "U", "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm",
          "Low-noise JFET dual op-amp, I/Q baseband buffers",
          [("1OUT", OUTPUT), ("1IN+", INPUT), ("1IN-", INPUT),
           ("2OUT", OUTPUT), ("2IN+", INPUT), ("2IN-", INPUT),
           ("V+", PWR_IN), ("V-", PWR_IN)],
          datasheet="https://www.ti.com/lit/ds/symlink/opa2134.pdf",
          keywords="opamp buffer"),

    # --- power ----------------------------------------------------------
    _part("AMS1117-3.3", "U", "Package_TO_SOT_SMD:SOT-223-3_TabPin2",
          "3.3 V low-dropout regulator from the 5 V LPO rail",
          [("GND", PWR_IN), ("VOUT", PWR_OUT), ("VIN", PWR_IN)],
          datasheet="https://www.ams1117.com/ams1117-3.3.html",
          keywords="LDO regulator 3.3V"),

    # --- MCU ------------------------------------------------------------
    # Only the pins this design actually uses are declared; the full 48-pin
    # LQFP symbol lives in the KiCad library.
    _part("STM32F103C8T6", "U", "Package_QFP:LQFP-48_7x7mm_P0.5mm",
          "STM32F103C8T6 Cortex-M3 72 MHz - ADC + USB CDC I/Q output",
          [("VDD", PWR_IN), ("VSS", PWR_IN), ("3V3", PWR_OUT),
           ("PA0", BIDIR), ("PA1", BIDIR), ("PA2", BIDIR), ("PA3", BIDIR),
           ("PA6", BIDIR), ("PA7", BIDIR),
           ("D+", BIDIR), ("D-", BIDIR)],
          datasheet="https://www.st.com/resource/en/datasheet/stm32f103c8.pdf",
          keywords="STM32 MCU ADC USB"),

    # --- connectors / misc ---------------------------------------------
    _part("SMA_EDGE", "J", "Connector_Coaxial:SMA_Amphenol_132289_EdgeMount",
          "SMA edge-mount antenna feed", [("SIG", PASSIVE), ("GND", PWR_IN)]),
    _part("USB_B", "J", "Connector:USB_B_Receptacle",
          "USB Type-B receptacle for I/Q data to the host",
          [("VBUS", PWR_IN), ("D+", BIDIR), ("D-", BIDIR), ("GND", PWR_IN)]),
    _part("XTAL8MHz", "Y", "Crystal:Crystal_SMD_3225-4Pin",
          "8 MHz crystal for the STM32",
          [("1", PASSIVE), ("2", PWR_IN), ("3", PASSIVE), ("4", PWR_IN)]),
    _part("TESTPOINT", "TP", "TestPoint:TestPoint_Pad_D1.5mm",
          "Test point", [("1", PASSIVE)]),
])


# SKiDL 2.3.0 does not route parts to a newly created Circuit automatically;
# `circuit=` must be passed explicitly or they land in the previous default.
_CIRCUIT: Circuit | None = None


def net(name: str):
    """Create a Net bound to the circuit under construction.

    Nets also need an explicit `circuit=` in 2.3.0, otherwise they attach to a
    different (unnamed) circuit and wiring raises.
    """
    return Net(name, circuit=_CIRCUIT)


def inst(part_name: str, ref: str, value: str | None = None, **kw):
    """Instantiate a part from the SDR_PARTS library by template name.

    SKiDL 2.3.0 instantiates a template with `Part(lib, name, ...)`.
    """
    attrs = {"ref": ref, "circuit": _CIRCUIT}
    if value is not None:
        attrs["value"] = value
    attrs.update(kw)
    return Part(SDR_PARTS, part_name, **attrs)


def two_pin(part_name: str, ref: str, value: str, a, b):
    """Instantiate a 2-terminal passive and wire it between two nets.

    SKiDL 2.3.0 has no `part.connect(a, b)`; pins are wired with `+=`.
    """
    p = inst(part_name, ref, value)
    p["1"] += a
    p["2"] += b
    return p


def one_pin(part_name: str, ref: str, net):
    """Instantiate a single-terminal part (test point) on one net."""
    p = inst(part_name, ref)
    p["1"] += net
    return p


# ---------------------------------------------------------------------------
# Circuit
# ---------------------------------------------------------------------------

def build_circuit() -> Circuit:
    # SKiDL 2.3.0 exposes a plain logging module, not a set_level() helper.
    import logging
    logging.getLogger("skidl").setLevel(logging.ERROR)
    global _CIRCUIT
    c = Circuit(name="SDR_Receiver")
    _CIRCUIT = c

    # ---- rails ---------------------------------------------------------
    gnd = net("GND")
    vin5 = net("VIN_5V")
    v33 = net("+3V3")
    ana33 = net("+3V3_ANA")

    # 5 V LPO rail in (from the low-power supply block / USB VBUS).
    j_pwr = inst("USB_B", "J_PWR", "5V_IN")
    j_pwr.VBUS += vin5
    j_pwr.GND += gnd
    j_pwr["D+"] += net("PWR_D+")     # unpopulated on the power-only header
    j_pwr["D-"] += net("PWR_D-")

    # AMS1117-3.3 LDO.
    ldo = inst("AMS1117-3.3", "U_LDO", "AMS1117-3.3")
    ldo.VIN += vin5
    ldo.GND += gnd
    ldo.VOUT += v33
    two_pin("C", "C_LDO1", "100u", v33, gnd)
    two_pin("C", "C_LDO2", "100n", v33, gnd)

    # Ferrite-isolated analog rail.
    two_pin("FB", "FB1", "600R@100MHz", v33, ana33)
    two_pin("C", "C_ANA1", "10u", ana33, gnd)
    two_pin("C", "C_ANA2", "100n", ana33, gnd)

    # ---- RF input ------------------------------------------------------
    rf_in = net("RF_IN")
    j_ant = inst("SMA_EDGE", "J_ANT")
    j_ant.SIG += rf_in
    j_ant.GND += gnd
    two_pin("R", "R_TERM", "50", rf_in, gnd)
    one_pin("TESTPOINT", "TP_ANT", rf_in)

    # ---- LNA 1 (common-gate 2N7002) ------------------------------------
    lna1_d = net("LNA1_D")
    lna1_g = net("LNA1_G")
    lna1_s = net("LNA1_S")

    q1 = inst("2N7002", "Q_LNA1", "2N7002")
    q1.D += lna1_d
    q1.G += lna1_g
    q1.S += lna1_s

    # Gate biased from the RF input: common-gate, so Zin ~ 1/gm.
    two_pin("C", "C_IN", "100p", rf_in, lna1_g)
    two_pin("R", "R_G1", "10k", v33, lna1_g)
    two_pin("R", "R_G2", "10k", lna1_g, gnd)
    # Drain load is an RF choke; source degenerates into a resistor.
    two_pin("L", "L_LNA1", "18u", v33, lna1_d)
    two_pin("R", "R_S1", "1k", lna1_s, gnd)
    two_pin("C", "C_LNA1", "100p", lna1_s, gnd)
    one_pin("TESTPOINT", "TP_LNA1_D", lna1_d)

    # ---- quarter-wave coupler -------------------------------------------
    qwc_in = net("QWC_IN")
    qwc_j2 = net("QWC_J2")
    qwc_cp = net("QWC_CP")

    x_qwc = inst("LPJ-1", "X_QWC1", "LPJ-1")
    x_qwc.J1 += qwc_in
    x_qwc.J2 += qwc_j2
    x_qwc.CP += qwc_cp
    x_qwc.GND += gnd
    two_pin("C", "C_QWC", "100p", lna1_d, qwc_in)
    two_pin("R", "R_QWC", "1k", qwc_cp, gnd)

    # ---- LNA 2 ----------------------------------------------------------
    lna2_d = net("LNA2_D")
    lna2_g = net("LNA2_G")
    lna2_s = net("LNA2_S")

    q2 = inst("2N7002", "Q_LNA2", "2N7002")
    q2.D += lna2_d
    q2.G += lna2_g
    q2.S += lna2_s

    x_qwc.J2 += lna2_g           # coupler output drives the second gate
    two_pin("R", "R_G3", "4k7", v33, lna2_g)
    two_pin("R", "R_G4", "2k2", lna2_g, gnd)
    two_pin("L", "L_LNA2", "18u", ana33, lna2_d)
    two_pin("R", "R_S2", "1k", lna2_s, gnd)
    one_pin("TESTPOINT", "TP_LNA2_D", lna2_d)

    # ---- LC bandstop filter ---------------------------------------------
    fil_in = net("FIL_IN")
    fil_out = net("FIL_OUT")
    two_pin("C", "C_BS1", "100p", lna2_d, fil_in)
    two_pin("L", "L_BS1", "100u", fil_in, fil_out)
    two_pin("C", "C_BS2", "10p", fil_out, gnd)
    two_pin("R", "R_BS", "1k", fil_out, gnd)
    one_pin("TESTPOINT", "TP_FIL", fil_out)

    # ---- mixer (zero-IF) --------------------------------------------------
    mix_in_p = net("MIX_IN_P")
    mix_in_n = net("MIX_IN_N")
    mix_i1 = net("MIX_I1")
    mix_i2 = net("MIX_I2")
    mix_q1 = net("MIX_Q1")
    mix_q2 = net("MIX_Q2")
    lo = net("LO")
    vcc_mix = net("VCC_MIX")

    mix = inst("LT5560", "U_MIX1", "LT5560")
    # Pin names contain '+'/'-', so they must be reached by subscript.
    mix["IN+"] += mix_in_p
    mix["IN-"] += mix_in_n
    mix.IOUT1 += mix_i1
    mix.IOUT2 += mix_i2
    mix.QOUT1 += mix_q1
    mix.QOUT2 += mix_q2
    mix.LO += lo
    mix.VCC += vcc_mix
    mix.GND += gnd

    two_pin("C", "C_MIX1", "100p", fil_out, mix_in_p)
    two_pin("C", "C_MIX2", "100p", gnd, mix_in_n)
    two_pin("FB", "L_MIX", "600R@100MHz", ana33, vcc_mix)
    two_pin("C", "C_MIX3", "100n", vcc_mix, gnd)

    # ---- baseband buffers -------------------------------------------------
    adc_i = net("ADC_I")
    adc_q = net("ADC_Q")

    op = inst("OPA2134", "U_OP1", "OPA2134")
    op["V+"] += ana33
    op["V-"] += gnd
    op["1IN+"] += mix_i1
    op["1IN-"] += mix_i2
    op["1OUT"] += adc_i
    op["2IN+"] += mix_q1
    op["2IN-"] += mix_q2
    op["2OUT"] += adc_q

    two_pin("R", "R_OPI", "10k", mix_i1, mix_i2)
    two_pin("C", "C_OPI", "100p", adc_i, gnd)
    two_pin("R", "R_OPQ", "10k", mix_q1, mix_q2)
    two_pin("C", "C_OPQ", "100p", adc_q, gnd)

    # ---- anti-alias into the ADC -------------------------------------------
    adc_i_f = net("ADC_I_F")
    adc_q_f = net("ADC_Q_F")
    two_pin("R", "R_AF_I", "10k", adc_i, adc_i_f)
    two_pin("C", "C_AF_I", "100p", adc_i_f, gnd)
    two_pin("R", "R_AF_Q", "10k", adc_q, adc_q_f)
    two_pin("C", "C_AF_Q", "100p", adc_q_f, gnd)

    # ---- MCU ---------------------------------------------------------------
    lo_src = net("LO_SRC")
    ctrl = net("CTRL")
    usb_dp = net("USB_D+")
    usb_dn = net("USB_D-")

    mcu = inst("STM32F103C8T6", "U1", "STM32F103C8T6")
    mcu.VDD += v33
    mcu.VSS += gnd
    mcu["3V3"] += v33              # pin name starts with a digit
    mcu["PA0"] += adc_i_f          # I channel into ADC1_IN0
    mcu["PA1"] += adc_q_f          # Q channel into ADC1_IN1
    mcu["PA6"] += lo_src           # timer output -> mixer LO
    mcu["PA7"] += ctrl
    mcu["D+"] += usb_dp
    mcu["D-"] += usb_dn
    one_pin("TESTPOINT", "TP_ADC_I", adc_i_f)
    one_pin("TESTPOINT", "TP_ADC_Q", adc_q_f)

    two_pin("C", "C_MCU1", "100n", v33, gnd)
    two_pin("C", "C_MCU2", "100n", v33, gnd)
    two_pin("C", "C_MCU3", "4u7", v33, gnd)

    # LO from the MCU to the mixer.
    two_pin("R", "R_LO", "50", lo_src, lo)
    two_pin("C", "C_LO", "100p", lo, gnd)

    # ---- crystal -------------------------------------------------------------
    osc_in = net("OSC_IN")
    osc_out = net("OSC_OUT")
    xtal = inst("XTAL8MHz", "Y1", "8MHz")
    xtal["1"] += osc_in
    xtal["2"] += gnd
    xtal["3"] += osc_out
    xtal["4"] += gnd
    two_pin("C", "C_OSC1", "22p", osc_in, gnd)
    two_pin("C", "C_OSC2", "22p", osc_out, gnd)

    # ---- USB data to host ----------------------------------------------------
    j_usb = inst("USB_B", "J_USB", "USB_CDC")
    j_usb.VBUS += vin5
    j_usb.GND += gnd
    j_usb["D+"] += usb_dp
    j_usb["D-"] += usb_dn
    two_pin("R", "R_USB1", "22", usb_dp, net("USB_D+_R"))
    two_pin("R", "R_USB2", "22", usb_dn, net("USB_D-_R"))

    return c


def main() -> int:
    c = build_circuit()

    kicad_dir = PROJECT / "hardware" / "kicad"
    kicad_dir.mkdir(parents=True, exist_ok=True)
    sch = kicad_dir / "sdr_receiver.kicad_sch"
    try:
        generate_netlist(file=str(sch))
        print(f"KiCad schematic: {sch}")
    except Exception as exc:
        print(f"KiCad schematic FAILED: {exc}")

    net_dir = PROJECT / "sim" / "netlists"
    net_dir.mkdir(parents=True, exist_ok=True)
    spice = net_dir / "sdr_skidl_ngspice.net"
    try:
        generate_netlist(file=str(spice), tool="spice")
        print(f"SPICE netlist:    {spice}")
    except Exception as exc:
        print(f"SPICE netlist FAILED: {exc}")

    # The generated files are the deliverable, so verify they landed rather
    # than trusting the "0 errors" line.
    for path, what in ((sch, "schematic"), (spice, "SPICE netlist")):
        if not path.exists():
            print(f"  WARNING: {what} not written to {path}", file=sys.stderr)
        else:
            print(f"  {what}: {path.stat().st_size} bytes")

    print(f"\nparts: {len(c.parts)}")
    print(f"nets:  {len(c.nets)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())