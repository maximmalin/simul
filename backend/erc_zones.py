"""ERC zones: 8 deterministic ERC rules + pin-alias matching (from AI Co-Pilot).

Adapted from ai-pcb-generator/src/gui/ai_copilot.py run_erc (MIT) — full
connectivity-based port, not ref-prefix heuristics.

Two input models:
  1. KiCad netlist (.net) — nets + nodes(ref,pin,pinfunction,pintype) give
     real connectivity -> ERC003..ERC008 with zero false topology guesses.
     ERC001/ERC002 (unconnected pins) are not visible in a netlist (KiCad
     ERC already covers them via `make erc`).
  2. CircuitSpec (backend/circuitspec.py) — full pin lists available ->
     all 8 rules ERC001..ERC008 exactly as upstream.

Legacy path `run_erc_zones(circuit)` (tscircuit circuit.json heuristics)
is kept for backward compatibility with tests/Makefile defaults.

Deterministic companion to KiCad CLI ERC. No frontend, no Qt, stdlib only.
"""
from __future__ import annotations
import json, re
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

_GND = {"GND", "VSS", "V-", "0V", "AGND", "DGND"}
_PWR = {"VCC", "VDD", "V+", "5V", "3V3", "3.3V", "12V", "VIN", "VOUT",
        "VBAT", "VSYS", "VIN_5V", "P3V3_ANA", "P3V3", "+5V", "+3.3V", "+12V"}
# both-direction aliases (upstream ai_copilot.py)
_PIN_ALIASES = {
    "VIN": {"IN", "INPUT", "VIN", "V+", "VS", "VS+", "VCC", "VDD"},
    "VOUT": {"OUT", "OUTPUT", "VOUT", "VO"},
    "GND": {"GND", "VSS", "V-", "VS-", "GROUND", "0V", "AGND", "DGND"},
    "IN": {"VIN", "INPUT", "IN", "V+"},
    "OUT": {"VOUT", "OUTPUT", "OUT"},
}


@dataclass
class ERCZoneIssue:
    code: str; severity: str; message: str; refs: list = None; auto_fix: str = ""
    def to_dict(self):
        d = asdict(self); d["refs"] = self.refs or []; return d


# ---------------------------------------------------------------------------
# Net name helpers (tolerant of +3V3_DIG / GND_DIG / P3V3 style names)
# ---------------------------------------------------------------------------

def _norm_net(name: str) -> str:
    return (name or "").upper().lstrip("+")

def is_gnd(name: str) -> bool:
    n = _norm_net(name)
    return n in _GND or n.startswith("GND") or n.endswith("_GND") or n.startswith("AGND") or n.startswith("DGND")

def is_pwr(name: str) -> bool:
    n = _norm_net(name)
    return n in _PWR or is_gnd(name) or any(n.startswith(p) for p in
           ("VCC", "VDD", "3V3", "5V", "12V", "VIN", "VBAT", "VSYS", "P3V3", "VOUT"))


# ---------------------------------------------------------------------------
# KiCad netlist parsing (minimal sexpr walk)
# ---------------------------------------------------------------------------

@dataclass
class NetlistModel:
    components: dict   # ref -> {value, footprint, part, lib, category}
    nets: dict         # name -> [{ref, pin, pinfunction, pintype}]

_SEXPR_TOKEN = re.compile(r'"[^"]*"|\(|\)|[^\s()]+')

def _sexpr_parse(text: str):
    tokens = _SEXPR_TOKEN.findall(text)
    pos = 0
    def parse():
        nonlocal pos
        tok = tokens[pos]; pos += 1
        if tok == "(":
            lst = []
            while pos < len(tokens) and tokens[pos] != ")":
                lst.append(parse())
            pos += 1
            return lst
        return tok[1:-1] if tok.startswith('"') else tok
    while pos < len(tokens) and tokens[pos] != "(":
        pos += 1
    return parse() if pos < len(tokens) else []

def _child(node, key):
    for item in node[1:] if isinstance(node, list) else []:
        if isinstance(item, list) and item and item[0] == key:
            return item[1] if len(item) > 1 else ""
    return ""

def _children(node, key):
    return [item for item in (node[1:] if isinstance(node, list) else [])
            if isinstance(item, list) and item and item[0] == key]


# ---------------------------------------------------------------------------
# Component category inference (value / lib part / refdes)
# ---------------------------------------------------------------------------

_REGULATOR_PAT = re.compile(r"AMS1117|LM78|LM79|MP23|MP15|TPS7|TPS6|AP21|XC62|LD1117|LDO|REG", re.I)
_MCU_PAT = re.compile(r"STM32|ATSAMD|ESP32|ESP8266|ATMEGA|ATtiny|CH340|CH552|PIC1|nRF|RP2040", re.I)
_OPAMP_PAT = re.compile(r"TL0|LM35|LM324|LM741|OPA|NE555|TLC|AD8|MAX4", re.I)

def infer_category(ref: str, value: str = "", part: str = "") -> str:
    """Best-effort category from refdes + value + lib part (upstream categories)."""
    ref = (ref or "").upper(); value = value or ""; part = part or ""
    v = f"{value} {part}"
    if ref.startswith(("J", "P", "CON", "XJ")) and not ref.startswith("PD"):
        return "connector"
    if ref.startswith(("Y", "X")) and re.search(r"\d", ref):
        return "crystal"
    if ref.startswith(("K", "REL")):
        return "relay"
    if ref.startswith("F") and not ref.startswith("FB"):
        return "fuse"
    if ref.startswith("SW"):
        return "switch"
    if ref.startswith("T") and not ref.startswith("TP") and not ref.startswith("FB"):
        return "transformer"
    if ref.startswith(("Q", "VT")):
        return "mosfet" if re.search(r"MOS|2N70|AO34|IRL", v, re.I) else "transistor"
    if re.search(r"LED|СВЕТ|ВД\d", v, re.I) or ref.startswith("LED"):
        return "led"
    if ref.startswith(("D", "PD", "VD")):
        return "diode"
    if ref.startswith(("R", "RN")) and re.search(r"^R", part) or ref.startswith("R"):
        return "resistor"
    if ref.startswith("C"):
        return "capacitor"
    if ref.startswith(("L", "FB")):
        return "inductor"
    if _REGULATOR_PAT.search(v):
        return "regulator"
    if _MCU_PAT.search(v):
        return "microcontroller"
    if _OPAMP_PAT.search(v):
        return "opamp"
    if ref.startswith("U"):
        return "ic"
    if ref.startswith("ANT") or ref.startswith("A") and "Antenna" in part:
        return "other"
    return "other"


def parse_kicad_netlist(path: Path | str) -> NetlistModel:
    """Parse KiCad export netlist -> components + nets with pintype nodes."""
    text = Path(path).read_text(errors="ignore")
    root = _sexpr_parse(text)
    comps: dict = {}
    nets: dict = {}
    for section in _children(root, "components"):
        for comp in _children(section, "comp"):
            ref = _child(comp, "ref")
            if not ref:
                continue
            value = _child(comp, "value")
            footprint = _child(comp, "footprint")
            libsrc = next((i for i in comp[1:] if isinstance(i, list) and i and i[0] == "libsource"), [])
            part = _child(libsrc, "part"); lib = _child(libsrc, "lib")
            comps[ref] = {"value": value, "footprint": footprint, "part": part, "lib": lib,
                          "category": infer_category(ref, value, part)}
    for section in _children(root, "nets"):
        for net in _children(section, "net"):
            name = _child(net, "name")
            if not name:
                continue
            nodes = []
            for nd in _children(net, "node"):
                nodes.append({"ref": _child(nd, "ref"), "pin": str(_child(nd, "pin")),
                              "pinfunction": _child(nd, "pinfunction"),
                              "pintype": _child(nd, "pintype") or "passive"})
            nets[name] = nodes
    return NetlistModel(components=comps, nets=nets)


# ---------------------------------------------------------------------------
# Connectivity maps + pin-alias matching (upstream)
# ---------------------------------------------------------------------------

def _build_maps(model: NetlistModel):
    pin_to_nets: dict[str, list[str]] = {}      # "R1:1" -> [net names]
    net_pins: dict[str, list[dict]] = {}
    comp_connected_pins: dict[str, set[str]] = {}
    for name, nodes in model.nets.items():
        net_pins[name] = nodes
        for nd in nodes:
            key = f"{nd['ref']}:{nd['pin']}"
            pin_to_nets.setdefault(key, []).append(name)
            comp_connected_pins.setdefault(nd["ref"], set()).add(str(nd["pin"]).upper())
            if nd.get("pinfunction"):
                comp_connected_pins[nd["ref"]].add(nd["pinfunction"].upper())
    return pin_to_nets, net_pins, comp_connected_pins

def _pin_connected(ref: str, pin_number: str, pin_name: str,
                   pin_to_nets: dict, comp_connected_pins: dict) -> bool:
    """Check if a pin is connected via number, name, or common alias."""
    if f"{ref}:{pin_number}" in pin_to_nets:
        return True
    if pin_name and f"{ref}:{pin_name}" in pin_to_nets:
        return True
    connected = comp_connected_pins.get(ref, set())
    if not connected:
        return False
    pin_ids = {str(pin_number).upper()}
    if pin_name:
        pin_ids.add(pin_name.upper())
    for pid in pin_ids:
        if pid in connected:
            return True
        if _PIN_ALIASES.get(pid, set()) & connected:
            return True
    return False


# ---------------------------------------------------------------------------
# ERC on NetlistModel: ERC003..ERC008 (connectivity-based)
# ---------------------------------------------------------------------------

def run_erc_netlist(model: NetlistModel) -> list[ERCZoneIssue]:
    issues: list[ERCZoneIssue] = []
    if not model.components:
        return issues
    pin_to_nets, net_pins, comp_connected_pins = _build_maps(model)
    comp_cat = {r: c["category"] for r, c in model.components.items()}

    # ERC003: LED without current-limiting resistor (signal side only —
    # a resistor sitting on GND does NOT limit LED current)
    for ref, cat in comp_cat.items():
        if cat != "led":
            continue
        led_nets = {nn for pin, nns in pin_to_nets.items()
                    if pin.startswith(ref + ":") for nn in nns
                    if not is_gnd(nn)}
        has_resistor = any(
            comp_cat.get(nd["ref"]) == "resistor"
            for nn in led_nets for nd in net_pins.get(nn, []))
        if not has_resistor:
            issues.append(ERCZoneIssue("ERC003", "warning",
                f"LED {ref} without current-limiting resistor on any of its nets", [ref],
                auto_fix="add series resistor (~220Ω..1kΩ)"))

    # ERC004: no ground
    if not any(is_gnd(n) for n in model.nets):
        issues.append(ERCZoneIssue("ERC004", "error", "No ground connection", [],
                                   auto_fix="add GND net and connect all return paths"))

    # ERC005: no power source (suppressed when regulator/connector present)
    has_pwr = any(is_pwr(n) and not is_gnd(n) for n in model.nets)
    pwr_comps = [r for r, c in comp_cat.items() if c in ("regulator", "connector")]
    if not has_pwr and not pwr_comps:
        issues.append(ERCZoneIssue("ERC005", "warning", "No power source net found", []))

    # ERC006: multiple outputs driving the same net
    for name, nodes in model.nets.items():
        outputs = [f"{nd['ref']}:{nd.get('pinfunction') or nd['pin']}"
                   for nd in nodes
                   if nd.get("pintype") == "output"
                   and comp_cat.get(nd["ref"]) not in ("connector", "switch", "fuse")]
        if len(outputs) > 1:
            issues.append(ERCZoneIssue("ERC006", "error",
                f"Multiple outputs on net {name}: {', '.join(outputs)}",
                [o.split(":")[0] for o in outputs],
                auto_fix="add series resistors or use open-drain buffers"))

    # ERC007: IC/MCU/opamp/regulator without decoupling capacitor on its power net
    for ref, cat in comp_cat.items():
        if cat not in ("ic", "microcontroller", "opamp", "regulator"):
            continue
        power_nets = {nn for pin, nns in pin_to_nets.items()
                      if pin.startswith(ref + ":") for nn in nns
                      if is_pwr(nn) and not is_gnd(nn)}
        # also: nets where this IC has a power_in/power_out pintype node
        for nn in list(net_pins):
            if any(nd["ref"] == ref and nd.get("pintype") in ("power_in", "power_out")
                   for nd in net_pins[nn]):
                if is_pwr(nn) and not is_gnd(nn):
                    power_nets.add(nn)
        has_cap = any(
            comp_cat.get(nd["ref"]) == "capacitor"
            for nn in power_nets for nd in net_pins.get(nn, []))
        if power_nets and not has_cap:
            issues.append(ERCZoneIssue("ERC007", "warning",
                f"IC {ref} ({model.components[ref]['value']}) without decoupling capacitor "
                f"on power net(s): {', '.join(sorted(power_nets))}", [ref],
                auto_fix="add 100nF bypass cap close to power pins"))

    # ERC008: nets with a single connection
    for name, nodes in model.nets.items():
        if len(nodes) < 2:
            issues.append(ERCZoneIssue("ERC008", "warning",
                f"Net {name} has only {len(nodes)} connection(s)",
                [nodes[0]["ref"]] if nodes else []))
    return issues


# ---------------------------------------------------------------------------
# ERC on CircuitSpec: full ERC001..ERC008 (upstream run_erc port)
# ---------------------------------------------------------------------------

def run_erc_spec(spec) -> list[ERCZoneIssue]:
    """Full 8-rule ERC on a CircuitSpec (circuitspec.CircuitSpec)."""
    if not spec or not getattr(spec, "components", None):
        return []
    issues: list[ERCZoneIssue] = []
    pin_to_nets: dict[str, list[str]] = {}
    net_pins: dict[str, list] = {}
    comp_connected_pins: dict[str, set[str]] = {}
    for net in spec.nets:
        conns = net.connections if isinstance(net.connections, list) else []
        net_pins[net.name] = conns
        for conn in conns:
            ref = conn.get("ref") if isinstance(conn, dict) else getattr(conn, "ref", "")
            pin = conn.get("pin") if isinstance(conn, dict) else getattr(conn, "pin", "")
            key = f"{ref}:{pin}"
            pin_to_nets.setdefault(key, []).append(net.name)
            comp_connected_pins.setdefault(ref, set()).add(str(pin).upper())
    comp_map = {c.ref: c for c in spec.components}

    # ERC001/002: unconnected pins (power=error, input/output=warning)
    for comp in spec.components:
        for pin in getattr(comp, "pins", []) or []:
            pnum = str(getattr(pin, "number", ""))
            pname = str(getattr(pin, "name", "") or "")
            if _pin_connected(comp.ref, pnum, pname, pin_to_nets, comp_connected_pins):
                continue
            etype = getattr(pin, "electrical_type", "passive")
            if etype in ("power_in", "power_out"):
                issues.append(ERCZoneIssue("ERC001", "error",
                    f"Unconnected power pin {comp.ref}:{pname or pnum}", [comp.ref],
                    auto_fix="connect to the corresponding power net"))
            elif etype in ("input", "output"):
                issues.append(ERCZoneIssue("ERC002", "warning",
                    f"Unconnected {etype} pin {comp.ref}:{pname or pnum}", [comp.ref]))

    # ERC003: LED without resistor
    for comp in spec.components:
        if getattr(comp, "category", "") != "led":
            continue
        has_resistor = False
        for pin in getattr(comp, "pins", []) or []:
            for nn in pin_to_nets.get(f"{comp.ref}:{getattr(pin,'number','')}", []):
                for conn in net_pins.get(nn, []):
                    other = comp_map.get(conn.get("ref") if isinstance(conn, dict) else getattr(conn, "ref", ""))
                    if other and getattr(other, "category", "") == "resistor":
                        has_resistor = True; break
                if has_resistor: break
            if has_resistor: break
        if not has_resistor:
            issues.append(ERCZoneIssue("ERC003", "warning",
                f"LED {comp.ref} without current-limiting resistor", [comp.ref],
                auto_fix="add series resistor"))

    # ERC004: no ground
    if not any(is_gnd(n.name) for n in spec.nets) and spec.components:
        issues.append(ERCZoneIssue("ERC004", "error", "No ground connection", [],
                                   auto_fix="add GND net"))

    # ERC005: no power source
    has_pwr = any(is_pwr(n.name) and not is_gnd(n.name) for n in spec.nets)
    pwr_comps = [c for c in spec.components
                 if getattr(c, "category", "") in ("regulator", "connector")]
    if not has_pwr and not pwr_comps:
        issues.append(ERCZoneIssue("ERC005", "warning", "No power source net found", []))

    # ERC006: multiple outputs on same net
    for net in spec.nets:
        outputs = []
        for conn in net.connections:
            ref = conn.get("ref") if isinstance(conn, dict) else getattr(conn, "ref", "")
            pin = conn.get("pin") if isinstance(conn, dict) else getattr(conn, "pin", "")
            comp = comp_map.get(ref)
            if not comp or getattr(comp, "category", "") in ("connector", "switch", "fuse"):
                continue
            for p in getattr(comp, "pins", []) or []:
                if str(getattr(p, "number", "")) == str(pin) and getattr(p, "electrical_type", "") == "output":
                    outputs.append(f"{comp.ref}:{getattr(p,'name','') or pin}")
        if len(outputs) > 1:
            issues.append(ERCZoneIssue("ERC006", "error",
                f"Multiple outputs on net {net.name}: {', '.join(outputs)}",
                [o.split(":")[0] for o in outputs]))

    # ERC007: IC without decoupling capacitor
    for comp in spec.components:
        if getattr(comp, "category", "") not in ("ic", "microcontroller", "opamp"):
            continue
        power_nets = set()
        for pin in getattr(comp, "pins", []) or []:
            if getattr(pin, "electrical_type", "") in ("power_in", "power_out"):
                power_nets.update(pin_to_nets.get(f"{comp.ref}:{getattr(pin,'number','')}", []))
        has_cap = False
        for nn in power_nets:
            for conn in net_pins.get(nn, []):
                ref = conn.get("ref") if isinstance(conn, dict) else getattr(conn, "ref", "")
                other = comp_map.get(ref)
                if other and getattr(other, "category", "") == "capacitor":
                    has_cap = True; break
            if has_cap: break
        if power_nets and not has_cap:
            issues.append(ERCZoneIssue("ERC007", "warning",
                f"IC {comp.ref} without decoupling capacitor on power net(s)",
                [comp.ref], auto_fix="add 100nF bypass cap"))

    # ERC008: single-pin nets
    for net in spec.nets:
        conns = net.connections if isinstance(net.connections, list) else []
        if len(conns) < 2:
            issues.append(ERCZoneIssue("ERC008", "warning",
                f"Net {net.name} has only {len(conns)} connection(s)", []))
    return issues


# ---------------------------------------------------------------------------
# Legacy: tscircuit circuit.json heuristics (kept for tests / no-netlist runs)
# ---------------------------------------------------------------------------

def run_erc_zones(circuit: list[dict[str, Any]]) -> list[ERCZoneIssue]:
    issues = []
    nets: dict[str, list[str]] = {}
    for e in circuit:
        if e.get("type") == "source_net":
            nets[e.get("name") or e.get("source_net_id", "?")] = []
    refs = [e.get("name", "") for e in circuit if e.get("type") == "source_component"]
    if not refs:
        return issues
    has_gnd = any(is_gnd(n) for n in nets) or any(r.upper().startswith("GND") for r in refs)
    if not has_gnd and nets:
        issues.append(ERCZoneIssue("ERC004", "error", "No ground connection (ERC004)"))
    if nets and not any(is_pwr(n) for n in nets):
        issues.append(ERCZoneIssue("ERC005", "warning", "No power source (ERC005)"))
    ic_refs = [r for r in refs if r.startswith("U") or r.startswith("STM")]
    cap_refs = [r for r in refs if r.startswith("C")]
    if ic_refs and not cap_refs:
        for r in ic_refs:
            issues.append(ERCZoneIssue("ERC007", "warning",
                f"IC {r} without decoupling capacitor (ERC007)", [r]))
    led_refs = [r for r in refs if r.startswith("D") or r.startswith("LED")]
    res_refs = [r for r in refs if r.startswith("R")]
    if led_refs and not res_refs:
        for r in led_refs:
            issues.append(ERCZoneIssue("ERC003", "warning",
                f"LED {r} without current-limiting resistor (ERC003)", [r]))
    return issues


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="ERC 8 rules + pin alias matching")
    ap.add_argument("--circuit", default="out/circuit.json")
    ap.add_argument("--netlist", default="hardware/kicad/schematic.net")
    ap.add_argument("--spec", help="CircuitSpec JSON for full ERC001..ERC008")
    ap.add_argument("--out", default="out/erc_zones.json")
    a = ap.parse_args()

    issues: list[ERCZoneIssue] = []
    source = "none"

    if a.spec and Path(a.spec).exists():
        try:
            from circuitspec import load_json
            issues = run_erc_spec(load_json(a.spec))
            source = f"circuitspec:{a.spec}"
        except Exception as e:
            issues = [ERCZoneIssue("ERC000", "error", f"spec load failed: {e}")]
            source = "spec-error"
    elif Path(a.netlist).exists():
        model = parse_kicad_netlist(a.netlist)
        issues = run_erc_netlist(model)
        source = f"netlist:{a.netlist}"
    elif Path(a.circuit).exists():
        try:
            from utils import load_circuit_json
            d = load_circuit_json(a.circuit)
        except Exception:
            d = json.loads(Path(a.circuit).read_text())
            if isinstance(d, dict):
                d = d.get("elements", [d])
        issues = run_erc_zones(d)
        source = f"circuit:{a.circuit}"

    report = {
        "source": source,
        "issue_count": len(issues),
        "errors": sum(1 for i in issues if i.severity == "error"),
        "warnings": sum(1 for i in issues if i.severity == "warning"),
        "issues": [i.to_dict() for i in issues],
        "rules": ["ERC001 unconnected power", "ERC002 unconnected signal",
                  "ERC003 led-no-resistor", "ERC004 no-ground", "ERC005 no-power",
                  "ERC006 multi-output", "ERC007 no-decoupling", "ERC008 single-pin-net"],
        "coverage_note": ("netlist/spec path: ERC003-008 (ERC001/002 need full pin "
                          "lists -> use --spec; otherwise covered by kicad-cli ERC)")
                          if source.startswith("netlist") else
                          ("spec path: all 8 rules" if source.startswith("circuitspec")
                           else "legacy circuit.json heuristics"),
    }
    try:
        from utils import save_json
        save_json(a.out, report)
    except Exception:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
