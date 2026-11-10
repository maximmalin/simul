"""CircuitSpec — NL→structured circuit contract (from AI-PCB-Generator).

Adapted from ai-pcb-generator/src/ai/schemas.py (MIT), but **stdlib-only**
(dataclasses instead of pydantic) so it runs with system python3.
Validation + repair (coerce pin numbers to str, defaults).
Loads JSON templates from design/templates/ or data/templates/.
"""
from __future__ import annotations
import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

@dataclass
class PinSpec:
    number: str; name: str = ""; electrical_type: str = "passive"
    def __post_init__(self): self.number=str(self.number)

@dataclass
class ComponentSpec:
    ref: str; value: str; category: str = "other"; package: str = ""
    description: str = ""; manufacturer: str = ""; manufacturer_pn: str = ""
    pins: list = field(default_factory=list); x_mm: float = 0.0; y_mm: float = 0.0; rotation_deg: float = 0.0
    def to_dict(self): return asdict(self)

@dataclass
class NetSpec:
    name: str; connections: list = field(default_factory=list)  # [{ref,pin}]
    def to_dict(self): return asdict(self)

@dataclass
class BoardSpec:
    width_mm: float = 100.0; height_mm: float = 100.0; layers: int = 2; thickness_mm: float = 1.6; shape: str = "rectangular"

@dataclass
class CircuitSpec:
    name: str; description: str = ""
    components: list = field(default_factory=list); nets: list = field(default_factory=list)
    board: BoardSpec = field(default_factory=BoardSpec)
    @property
    def component_count(self): return len(self.components)
    @property
    def net_count(self): return len(self.nets)
    def get_component(self, ref): return next((c for c in self.components if c.ref==ref), None)
    def get_nets_for_component(self, ref): return [n for n in self.nets if any(p.get("ref")==ref for p in n.connections)]
    def to_dict(self):
        return {"name":self.name,"description":self.description,"components":[c.to_dict() for c in self.components],
                "nets":[n.to_dict() for n in self.nets],"board":asdict(self.board)}

def validate(spec: CircuitSpec) -> list[str]:
    errs=[]
    if not spec.name: errs.append("name is required")
    seen=set()
    for c in spec.components:
        if c.ref in seen: errs.append(f"duplicate ref {c.ref}")
        seen.add(c.ref)
        for p in c.pins:
            if not str(p.number): errs.append(f"{c.ref} empty pin number")
    for n in spec.nets:
        if len(n.connections)<2: errs.append(f"net {n.name} has <2 connections")
    return errs


# ---------------------------------------------------------------------------
# CircuitValidator — full port of ai-pcb-generator/src/ai/parser.py (MIT):
# auto-repair of AI-generated specs (pins, refs, net refs, degenerate nets,
# power/bypass sanity, grid placement).
# ---------------------------------------------------------------------------

@dataclass
class ValidationWarning:
    message: str; severity: str = "warning"  # "warning" | "info"
    def __repr__(self): return f"[{self.severity.upper()}] {self.message}"


class CircuitValidator:
    """Validate and auto-fix a CircuitSpec. Returns corrected spec + warnings."""

    _DEFAULT_PIN_COUNTS: dict[str, int] = {
        "resistor": 2, "capacitor": 2, "inductor": 2,
        "diode": 2, "led": 2, "fuse": 2, "crystal": 2, "switch": 2,
        "transistor": 3, "mosfet": 3, "sensor": 3,
        "opamp": 5, "regulator": 3,
        "connector": 2, "relay": 4, "transformer": 4,
    }

    def __init__(self, spec: CircuitSpec):
        self.spec = spec
        self.warnings: list[ValidationWarning] = []

    def validate(self) -> CircuitSpec:
        self._ensure_component_pins()
        self._check_unique_refs()
        self._check_net_references()
        self._clean_degenerate_nets()
        if not self.spec.nets:
            self.warnings.append(ValidationWarning(
                "No nets were generated — all components are disconnected. "
                "Try increasing Max Tokens (8192+) in Settings and retry."))
        self._check_power_nets()
        self._check_bypass_caps()
        self._assign_positions()
        return self.spec

    def _ensure_component_pins(self) -> None:
        """Auto-generate pins for components with an empty pins list."""
        for comp in self.spec.components:
            if comp.pins:
                continue
            cat = str(getattr(comp, "category", "other"))
            n_pins = self._DEFAULT_PIN_COUNTS.get(cat, 2)
            # Package digits are a pin count only for IC-like packages
            # (DIP-8, SOIC-16, QFN-32) — NOT for passives: upstream had a bug
            # where package "0805" produced 805 pins.
            if cat in ("ic", "microcontroller", "opamp", "regulator"):
                pkg = (comp.package or "").upper()
                for token in pkg.replace("-", " ").replace("_", " ").split():
                    if token.isdigit() and 2 <= int(token) <= 64:
                        n_pins = int(token)
                        break
            comp.pins = [PinSpec(number=str(i + 1), name=str(i + 1)) for i in range(n_pins)]
            self.warnings.append(ValidationWarning(
                f"Component '{comp.ref}' had no pins — auto-generated {n_pins} pins",
                severity="info"))

    def _check_unique_refs(self) -> None:
        seen: set[str] = set()
        for ref in [c.ref for c in self.spec.components]:
            if ref in seen:
                self.warnings.append(ValidationWarning(
                    f"Duplicate reference designator: {ref}"))
            seen.add(ref)

    def _check_net_references(self) -> None:
        """Verify every net pin-ref points to an existing component + pin.

        Auto-fix: match pin by name -> assign first unused pin -> remove
        connections to non-existent components.
        """
        comp_pins: dict[str, set[str]] = {}
        comp_pin_name_map: dict[str, dict[str, str]] = {}
        for comp in self.spec.components:
            comp_pins[comp.ref] = {str(p.number) for p in comp.pins}
            name_map: dict[str, str] = {}
            for p in comp.pins:
                if p.name:
                    name_map[p.name.lower()] = str(p.number)
                name_map[str(p.number).lower()] = str(p.number)
            comp_pin_name_map[comp.ref] = name_map

        used_pins: dict[str, set[str]] = {ref: set() for ref in comp_pins}

        for net in self.spec.nets:
            conns_to_remove = []
            for conn in net.connections:
                ref = conn.get("ref") if isinstance(conn, dict) else getattr(conn, "ref", "")
                pin = str(conn.get("pin") if isinstance(conn, dict) else getattr(conn, "pin", ""))
                if ref not in comp_pins:
                    self.warnings.append(ValidationWarning(
                        f"Net '{net.name}': removing unknown component '{ref}'"))
                    conns_to_remove.append(conn)
                    continue
                available = comp_pins[ref]
                if pin in available:
                    used_pins[ref].add(pin)
                    continue
                matched = comp_pin_name_map[ref].get(pin.lower())
                if matched and matched in available:
                    self._fix_pin(conn, net.name, ref, pin, matched, "name match")
                    used_pins[ref].add(matched)
                    continue
                unused = available - used_pins[ref]
                if unused:
                    assigned = sorted(unused)[0]
                    self._fix_pin(conn, net.name, ref, pin, assigned, "first unused")
                    used_pins[ref].add(assigned)
                elif available:
                    fallback = sorted(available)[0]
                    self._fix_pin(conn, net.name, ref, pin, fallback, "fallback, all used")
            for bad in conns_to_remove:
                net.connections.remove(bad)

    @staticmethod
    def _fix_pin(conn, net_name: str, ref: str, old: str, new: str, how: str) -> None:
        if isinstance(conn, dict):
            conn["pin"] = new
        else:
            conn.pin = new

    def _clean_degenerate_nets(self) -> None:
        """Remove nets with <2 connections after validation."""
        keep = []
        for net in self.spec.nets:
            if len(net.connections) < 2:
                self.warnings.append(ValidationWarning(
                    f"Net '{net.name}' has {len(net.connections)} connection(s) — removing"))
            else:
                keep.append(net)
        self.spec.nets = keep

    def _check_power_nets(self) -> None:
        net_names = {n.name.upper().lstrip("+") for n in self.spec.nets}
        if not any(nn.startswith(("GND", "VSS", "AGND", "DGND")) or nn in ("GROUND", "0V")
                   for nn in net_names):
            self.warnings.append(ValidationWarning(
                "No GND/VSS net found — most circuits need a ground reference."))
        if not any(nn.startswith(("VCC", "VDD", "3V3", "5V", "12V", "VIN"))
                   for nn in net_names):
            self.warnings.append(ValidationWarning(
                "No power supply net found (VCC/VDD/VIN)."))

    def _check_bypass_caps(self) -> None:
        ic_refs = {c.ref for c in self.spec.components
                   if str(c.category) in ("ic", "regulator", "opamp", "microcontroller")}
        if not ic_refs:
            return
        cap_refs = {c.ref for c in self.spec.components if str(c.category) == "capacitor"}
        for net in self.spec.nets:
            refs = {c.get("ref") if isinstance(c, dict) else getattr(c, "ref", "")
                    for c in net.connections}
            nu = net.name.upper().lstrip("+")
            if (refs & ic_refs) and nu in ("VCC", "VDD", "3V3", "5V") and not (refs & cap_refs):
                self.warnings.append(ValidationWarning(
                    f"Power net '{net.name}' has ICs but no bypass capacitor.",
                    severity="info"))

    def _assign_positions(self) -> None:
        """Grid positions for components still at (0,0)."""
        unplaced = [c for c in self.spec.components if c.x_mm == 0 and c.y_mm == 0]
        if not unplaced:
            return
        cols = max(3, int(len(unplaced) ** 0.5) + 1)
        spacing, margin = 25.0, 10.0
        for i, comp in enumerate(unplaced):
            comp.x_mm = margin + (i % cols) * spacing
            comp.y_mm = margin + (i // cols) * spacing
        self.warnings.append(ValidationWarning(
            f"Auto-placed {len(unplaced)} components on a {cols}-column grid",
            severity="info"))


def validate_circuit(spec: CircuitSpec) -> tuple[CircuitSpec, list[ValidationWarning]]:
    """Convenience: validate+repair and return (spec, warnings)."""
    v = CircuitValidator(spec)
    return v.validate(), v.warnings

def from_dict(d: dict) -> CircuitSpec:
    comps=[ComponentSpec(**{**{k:v for k,v in c.items() if k in ComponentSpec.__dataclass_fields__}, "pins":[PinSpec(**p) if isinstance(p,dict) else p for p in c.get("pins",[])]}) for c in d.get("components",[])]
    nets=[NetSpec(**{k:v for k,v in n.items() if k in NetSpec.__dataclass_fields__}) if isinstance(n,dict) else n for n in d.get("nets",[])]
    board=BoardSpec(**d.get("board",{})) if isinstance(d.get("board",{}),dict) else BoardSpec()
    return CircuitSpec(name=d.get("name","untitled"), description=d.get("description",""), components=comps, nets=nets, board=board)

def load_json(path) -> CircuitSpec:
    return from_dict(json.loads(Path(path).read_text()))

def list_templates(root="design/templates") -> list[Path]:
    p=Path(root)
    if not p.exists(): return []
    return sorted(p.rglob("*.json"))

def main() -> int:
    import argparse
    ap=argparse.ArgumentParser(description="CircuitSpec validate / templates")
    ap.add_argument("--file"); ap.add_argument("--list-templates", default="design/templates"); ap.add_argument("--out")
    a=ap.parse_args()
    if a.file:
        spec=load_json(a.file); errs=validate(spec)
        print(json.dumps({"file":a.file,"components":spec.component_count,"nets":spec.net_count,"errors":errs}, indent=2, ensure_ascii=False))
        if a.out: Path(a.out).write_text(json.dumps(spec.to_dict(), indent=2))
        return 1 if errs else 0
    tpl=list_templates(a.list_templates)
    print(json.dumps({"templates":[str(p) for p in tpl]}, indent=2))
    return 0

if __name__=="__main__": raise SystemExit(main())
