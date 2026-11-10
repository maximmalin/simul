"""PCB placement quality score (from auto-pcb).

score = trace_length_mm + 5*via_count + 20*drc_violations + 200*unconnected

Operates on our tscircuit `circuit.json` (list of elements) + optional
`out/drc.rpt` text. No external deps (stdlib only) so CI can run it
with system python3. The score is *soft*: DRC errors penalise, they
do not hard-fail (auto-pcb lesson: a hard "must pass DRC" rule kills
every random-start run; even the human baseline has 6 violations).
"""
from __future__ import annotations
import json, math, re
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

VIA_PENALTY_MM = 5.0
DRC_PENALTY_MM = 20.0
UNCONNECTED_PENALTY_MM = 200.0
IGNORED_DRC_TYPES = frozenset({"lib_footprint_issues", "lib_footprint_mismatch", "courtyards_overlap"})

@dataclass
class ScoreResult:
    score: float
    trace_length_mm: float
    via_count: int
    drc_violations: int
    drc_violations_raw: int
    unconnected_count: int
    component_count: int
    breakdown: dict | None = None
    def to_dict(self): return asdict(self)

def _trace_length(circuit: list[dict[str, Any]]) -> float:
    total = 0.0
    for el in circuit:
        if el.get("type") != "pcb_trace":
            continue
        route = el.get("route")
        if not route:
            continue
        # iterate pairs efficiently
        pts = route
        prev = None
        for r in pts:
            if not isinstance(r, dict) or "x" not in r or "y" not in r:
                continue
            if prev is not None:
                total += math.hypot(r["x"] - prev[0], r["y"] - prev[1])
            prev = (r["x"], r["y"])
    return total

def count_unconnected(drc_text: str) -> int:
    if not drc_text:
        return 0
    # quick search
    return drc_text.count('[unconnected') if '[' in drc_text else len(re.findall(r'^\[unconnected', drc_text, re.I | re.M))

def count_drc(drc_text: str) -> tuple[int,int]:
    if not drc_text:
        return 0, 0
    m = re.search(r'Found\s+(\d+)\s+DRC violations', drc_text, re.I)
    if m:
        raw = int(m.group(1))
    else:
        raw = drc_text.count('\n[') if '\n[' in drc_text else len(re.findall(r'^\[', drc_text, re.M))
    filtered = raw
    for t in IGNORED_DRC_TYPES:
        filtered -= drc_text.count(t)
    return max(0, filtered), raw

def _ratsnest_length(circuit: list[dict[str,Any]]) -> float:
    """Straight-line pcb_port↔pcb_port distance via pcb_trace connectsTo."""
    ports = {}
    for p in circuit:
        if p.get("type") == "pcb_port" and "x" in p and "y" in p:
            ports[p["pcb_port_id"]] = (p["x"], p["y"])
    total = 0.0
    for t in circuit:
        if t.get("type") != "pcb_trace":
            continue
        c = t.get("connectsTo") or []
        if len(c) >= 2 and c[0] in ports and c[-1] in ports:
            (x1,y1) = ports[c[0]]
            (x2,y2) = ports[c[-1]]
            total += math.hypot(x2-x1, y2-y1)
    return total

def _placement_penalty(circuit: list[dict[str,Any]]) -> tuple[float,int]:
    """Continuous proximity+edge penalties on component centres."""
    comps = [c for c in circuit if c.get("type") == "pcb_component"]
    board_el = next((e for e in circuit if e.get("type") == "pcb_board"), {})
    bw = float(board_el.get("width", 88.6))
    bh = float(board_el.get("height", 85.08))
    penalty = 0.0
    bad = 0
    n = len(comps)
    for i in range(n):
        a = comps[i]
        ax = a["center"]["x"]; ay = a["center"]["y"]
        if abs(ax) > bw/2 - 1 or abs(ay) > bh/2 - 1:
            penalty += 50.0
            bad += 1
        aw2 = float(a.get("width", 2)) / 2.0
        ah2 = float(a.get("height", 2)) / 2.0
        ra = math.hypot(aw2, ah2)
        for j in range(i+1, n):
            b = comps[j]
            bx = b["center"]["x"]; by = b["center"]["y"]
            dist = math.hypot(ax - bx, ay - by)
            rb = math.hypot(float(b.get("width", 2))/2.0, float(b.get("height", 2))/2.0)
            edge = dist - ra - rb
            if edge < 5.0:
                penalty += (5.0 - edge) * 10.0
                if edge < 1.0:
                    bad += 1
    return penalty, bad

def score_circuit(circuit: list[dict[str,Any]], drc_text: str = "", strip_traces: bool = False) -> ScoreResult:
    # Optionally strip routed traces to use ratsnest-based scoring (auto-pcb style)
    if strip_traces:
        circuit = [e for e in circuit if e.get("type") != "pcb_trace"]
    tl = _trace_length(circuit)
    via = 0
    comps = 0
    for e in circuit:
        if e.get("type") == "pcb_via":
            via += 1
        elif e.get("type") == "pcb_component":
            comps += 1
    # DRC
    if drc_text:
        drc, drc_raw = count_drc(drc_text)
    else:
        drc = drc_raw = sum(1 for e in circuit if "error" in str(e.get("type","")).lower())
    unconn = count_unconnected(drc_text)
    place_pen, place_bad = _placement_penalty(circuit)
    ratsnest = _ratsnest_length(circuit)
    score = tl + VIA_PENALTY_MM*via + DRC_PENALTY_MM*drc + UNCONNECTED_PENALTY_MM*unconn + place_pen + 0.5*ratsnest
    return ScoreResult(score=score, trace_length_mm=tl, via_count=via, drc_violations=drc, drc_violations_raw=drc_raw, unconnected_count=unconn, component_count=comps,
                       breakdown={"trace_length_mm": tl, "vias": via, "drc": drc, "unconnected": unconn, "placement_penalty": place_pen, "placement_bad": place_bad, "ratsnest_mm": ratsnest})

try:
    from utils import load_circuit_json as load_circuit
except Exception:
    def load_circuit(path: Path) -> list[dict[str,Any]]:
        d = json.loads(Path(path).read_text())
        if isinstance(d, dict):
            return d.get("elements") or d.get("circuit") or [d]
        return d

def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="PCB placement score (auto-pcb)")
    ap.add_argument("--circuit", default="out/circuit.json")
    ap.add_argument("--drc", default="out/drc.rpt")
    ap.add_argument("--out", default="out/pcb_score.json")
    a = ap.parse_args()
    circ = load_circuit(Path(a.circuit))
    drc_text = Path(a.drc).read_text(errors="ignore") if Path(a.drc).exists() else ""
    r = score_circuit(circ, drc_text)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(r.to_dict(), indent=2))
    print(json.dumps(r.to_dict(), indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
