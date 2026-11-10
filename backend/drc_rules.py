"""Design Rule Check (DRC) rules engine — adapted from AI-PCB-Generator.

Works on our tscircuit `circuit.json` (no Board dataclass). Provides
programmatic DRC checks complementing `kicad-cli pcb drc`.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

@dataclass
class DRCViolation:
    rule: str = ""
    message: str = ""
    severity: str = "error"  # error, warning
    x_mm: float = 0.0
    y_mm: float = 0.0
    refs: list = None

    def to_dict(self):
        d = asdict(self)
        d["refs"] = self.refs or []
        return d


class DRCEngine:
    def __init__(self, circuit: list[dict[str, Any]], constraints: dict = None):
        self.circuit = circuit
        self.constraints = constraints or {
            "min_trace_width_mm": 0.127,
            "min_clearance_mm": 0.127,
            "min_via_drill_mm": 0.2,
            "board_edge_margin_mm": 0.5,
        }
        self.violations: list[DRCViolation] = []

    def run_all(self) -> list[DRCViolation]:
        self.violations.clear()
        self._check_trace_width()
        self._check_clearance_pad_to_pad()
        self._check_clearance_trace_to_pad()
        self._check_via_drill()
        self._check_board_boundary()
        self._check_unconnected()
        return self.violations

    @property
    def has_errors(self):
        return any(v.severity == "error" for v in self.violations)

    def _check_trace_width(self):
        min_w = self.constraints["min_trace_width_mm"]
        for t in self.circuit:
            if t.get("type") != "pcb_trace":
                continue
            for r in t.get("route") or []:
                if r.get("width") and r["width"] < min_w - 1e-6:
                    self.violations.append(DRCViolation(
                        rule="min_trace_width",
                        message=f"Trace width {r['width']:.3f}mm < minimum {min_w:.3f}mm",
                        severity="error",
                        x_mm=r.get("x", 0), y_mm=r.get("y", 0),
                    ))
                    break

    def _check_clearance_pad_to_pad(self):
        min_clr = self.constraints["min_clearance_mm"]
        pads = [p for p in self.circuit if p.get("type") in ("pcb_smtpad", "pcb_plated_hole", "pcb_pad")]
        for i in range(len(pads)):
            for j in range(i + 1, len(pads)):
                a, b = pads[i], pads[j]
                if a.get("pcb_component_id") == b.get("pcb_component_id"):
                    continue
                if not all(k in a and k in b for k in ("x", "y")):
                    continue
                dist = math.hypot(a["x"] - b["x"], a["y"] - b["y"])
                edge = dist - (float(a.get("width", 1)) + float(b.get("width", 1))) / 2
                if edge < min_clr - 1e-6:
                    self.violations.append(DRCViolation(
                        rule="pad_clearance",
                        message=f"Pad clearance {edge:.3f}mm < {min_clr:.3f}mm",
                        severity="error",
                        x_mm=(a["x"] + b["x"]) / 2, y_mm=(a["y"] + b["y"]) / 2,
                        refs=[a.get("pcb_component_id"), b.get("pcb_component_id")],
                    ))

    def _check_clearance_trace_to_pad(self):
        min_clr = self.constraints["min_clearance_mm"]
        pads = [p for p in self.circuit if p.get("type") in ("pcb_smtpad", "pcb_plated_hole", "pcb_pad") and "x" in p]
        for t in self.circuit:
            if t.get("type") != "pcb_trace": continue
            route = t.get("route") or []
            for i in range(len(route) - 1):
                if "x" not in route[i] or "x" not in route[i + 1]: continue
                x1, y1, x2, y2 = route[i]["x"], route[i]["y"], route[i + 1]["x"], route[i + 1]["y"]
                w = route[i].get("width", 0.2)
                for pad in pads:
                    dist = self._pt_seg_dist(pad["x"], pad["y"], x1, y1, x2, y2)
                    edge = dist - float(pad.get("width", 1)) / 2 - w / 2
                    if edge < min_clr - 1e-6:
                        self.violations.append(DRCViolation(
                            rule="trace_pad_clearance",
                            message=f"Trace-pad clearance {edge:.3f}mm < {min_clr:.3f}mm",
                            severity="error",
                            x_mm=pad["x"], y_mm=pad["y"],
                        ))
                        break

    def _check_via_drill(self):
        min_drill = self.constraints["min_via_drill_mm"]
        for v in self.circuit:
            if v.get("type") != "pcb_via": continue
            drill = float(v.get("hole_diameter", v.get("drill_diameter", 0)) or 0)
            if drill and drill < min_drill:
                self.violations.append(DRCViolation(
                    rule="via_drill", message=f"Via drill {drill:.3f}mm < {min_drill:.3f}mm",
                    severity="error", x_mm=v.get("x", 0), y_mm=v.get("y", 0),
                ))
            outer = float(v.get("outer_diameter", 0) or 0)
            if drill and outer and drill >= outer:
                self.violations.append(DRCViolation(
                    rule="via_annular_ring", message="Via drill >= diameter — no annular ring",
                    severity="error", x_mm=v.get("x", 0), y_mm=v.get("y", 0),
                ))

    def _check_board_boundary(self):
        board = next((e for e in self.circuit if e.get("type") == "pcb_board"), {})
        if not board: return
        bw = float(board.get("width", 100)); bh = float(board.get("height", 100))
        margin = self.constraints["board_edge_margin_mm"]
        for p in self.circuit:
            if p.get("type") not in ("pcb_smtpad", "pcb_plated_hole", "pcb_pad"): continue
            if "x" not in p or "y" not in p: continue
            if abs(p["x"]) > bw/2 - margin or abs(p["y"]) > bh/2 - margin:
                self.violations.append(DRCViolation(
                    rule="board_boundary", message="Pad too close to or outside board edge",
                    severity="warning", x_mm=p["x"], y_mm=p["y"],
                ))

    def _check_unconnected(self):
        # Check for pads not referenced in traces - rough check
        pass

    @staticmethod
    def _pt_seg_dist(px, py, ax, ay, bx, by):
        dx, dy = bx - ax, by - ay
        seg2 = dx*dx + dy*dy
        if seg2 < 1e-12:
            return math.hypot(px - ax, py - ay)
        t = ((px - ax)*dx + (py - ay)*dy) / seg2
        t = max(0.0, min(1.0, t))
        projx, projy = ax + t*dx, ay + t*dy
        return math.hypot(px - projx, py - projy)

def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="DRC rules engine (AI-PCB-Generator style)")
    ap.add_argument("--circuit", default="out/circuit.json")
    ap.add_argument("--out", default="out/drc_rules.json")
    a = ap.parse_args()
    try:
        from utils import load_circuit_json
        d = load_circuit_json(a.circuit)
    except Exception:
        d = json.loads(Path(a.circuit).read_text())
        if isinstance(d, dict): d = d.get("elements", [d])
    eng = DRCEngine(d)
    vs = eng.run_all()
    report = {"violation_count": len(vs), "errors": sum(1 for v in vs if v.severity=="error"),
              "warnings": sum(1 for v in vs if v.severity=="warning"), "violations": [v.to_dict() for v in vs]}
    try:
        from utils import save_json
        save_json(a.out, report)
    except Exception:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
