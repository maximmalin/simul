"""DFM analysis engine — 12 checks (IPC-2221), adapted from AI-PCB-Generator.

Works on our tscircuit `circuit.json` (no Board dataclass needed).
All checks are pure-python (stdlib only). score 0-100 like upstream.

Source: ai-pcb-generator/src/pcb/dfm.py (MIT, 22507260).
Adaptation: traces/pads/vias/board extracted from circuit.json elements.
"""
from __future__ import annotations
import json, math
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

_TRACE_CURRENT = [(0.10,0.3),(0.15,0.4),(0.20,0.5),(0.25,0.7),(0.30,0.8),(0.40,1.0),(0.50,1.2),(0.75,1.6),(1.00,2.0),(1.50,2.8),(2.00,3.5),(3.00,4.8),(5.00,7.0)]
_POWER_NETS = {"VCC","VDD","GND","VSS","5V","3V3","3.3V","12V","+5V","+3.3V","+12V","VIN","V+","V-","VOUT","VBAT","PWR","GND_3V3"}

def _max_current(w: float) -> float:
    if w <= _TRACE_CURRENT[0][0]: return _TRACE_CURRENT[0][1]
    if w >= _TRACE_CURRENT[-1][0]: return _TRACE_CURRENT[-1][1]
    for (w0,c0),(w1,c1) in zip(_TRACE_CURRENT, _TRACE_CURRENT[1:]):
        if w0 <= w <= w1:
            return c0 + (w-w0)/(w1-w0)*(c1-c0)
    return _TRACE_CURRENT[-1][1]

@dataclass
class DFMIssue:
    code: str; title: str; severity: str; description: str; recommendation: str = ""; refs: list = None
    def to_dict(self):
        d = asdict(self); d["refs"] = self.refs or []; return d

@dataclass
class BoardGeom:
    components: list; traces: list; vias: list; pads: list; board: dict

def extract_board(circuit: list[dict[str,Any]]) -> BoardGeom:
    comps = [c for c in circuit if c.get("type")=="pcb_component"]
    traces = [t for t in circuit if t.get("type")=="pcb_trace"]
    vias = [v for v in circuit if v.get("type")=="pcb_via"]
    pads = [p for p in circuit if p.get("type") in ("pcb_smtpad","pcb_plated_hole","pcb_pad")]
    board = next((b for b in circuit if b.get("type")=="pcb_board"), {}) or {}
    # enrich pads with net names via source_net if present
    return BoardGeom(comps, traces, vias, pads, board)

def _trace_segments(traces):
    segs = []
    for t in traces:
        route = t.get("route") or []
        pts = [(r["x"], r["y"]) for r in route if "x" in r and "y" in r]
        width = next((r.get("width",0.2) for r in route if r.get("width")), 0.2)
        layer = next((r.get("layer","top") for r in route if r.get("layer")), "top")
        name = t.get("connection_name","") or t.get("source_net_id","")
        for (x1,y1),(x2,y2) in zip(pts, pts[1:]):
            segs.append(dict(x1=x1,y1=y1,x2=x2,y2=y2,width=width,layer=layer,net=name))
    return segs

def run_dfm(circuit: list[dict[str,Any]]) -> list[DFMIssue]:
    B = extract_board(circuit); issues: list[DFMIssue] = []
    segs = _trace_segments(B.traces)
    board_w = float(B.board.get("width", 88.6)); board_h = float(B.board.get("height", 85.08)); thick = float(B.board.get("thickness",1.6)); layers = int(B.board.get("num_layers",4))
    # DFM-001 narrow power traces (<0.4mm). net name from connection_name may be source_net_*; skip unknown.
    for s in segs:
        if s["net"].upper() in _POWER_NETS and s["width"] < 0.4:
            issues.append(DFMIssue("DFM-001","Narrow power trace","warning",f"Net {s['net']} width {s['width']:.2f}mm (max ~{_max_current(s['width']):.1f}A)","Increase width to >=0.5mm for power lines."))
    # DFM-002 annular ring via/pad
    for v in B.vias:
        hole = float(v.get("hole_diameter",0.3)); outer = float(v.get("outer_diameter",0.6)); ring=(outer-hole)/2
        if ring < 0.15:
            issues.append(DFMIssue("DFM-002","Insufficient annular ring (via)","critical",f"Via ring {ring:.3f}mm < 0.15mm","Increase via diameter or decrease drill."))
    for p in B.pads:
        if p.get("type") != "pcb_plated_hole":
            continue  # SMT pads have no drill; annular ring N/A
        d = float(p.get("hole_diameter", p.get("drill_diameter", 0)) or 0)
        w = float(p.get("rect_pad_width", p.get("width", 0)) or 0)
        h = float(p.get("rect_pad_height", p.get("height", w)) or w)
        if d>0 and w>0 and h>0:
            ring=(min(w,h)-d)/2
            if ring < 0.15:
                issues.append(DFMIssue("DFM-002","Insufficient annular ring (pad)","warning",f"Pad drill {d:.3f}mm ring {ring:.3f}mm <0.15mm","Increase pad or decrease drill."))
    # DFM-003 acid traps
    for i,a in enumerate(segs):
        for b in segs[i+1:]:
            if abs(a["x2"]-b["x1"])<0.01 and abs(a["y2"]-b["y1"])<0.01:
                va=(a["x2"]-a["x1"],a["y2"]-a["y1"]); vb=(b["x2"]-b["x1"],b["y2"]-b["y1"])
                la=math.hypot(*va); lb=math.hypot(*vb)
                if la<0.01 or lb<0.01: continue
                cos=(va[0]*vb[0]+va[1]*vb[1])/(la*lb); cos=max(-1,min(1,cos)); ang=math.degrees(math.acos(cos))
                if ang<45:
                    issues.append(DFMIssue("DFM-003","Acid trap detected","warning",f"Junction at ({a['x2']:.1f},{a['y2']:.1f}) angle {ang:.0f}deg","Use 45deg or 90deg junctions."))
    # DFM-005 silk over pad (approx: silk text overlapping pad)
    silk = [x for x in circuit if x.get("type")=="pcb_silkscreen_text"]
    for s in silk:
        pos = s.get("center") or s.get("x") or {}
        if isinstance(pos, dict):
            sx, sy = pos.get("x"), pos.get("y")
        else:
            sx, sy = None, None
        if sx is None: continue
        for p in B.pads:
            px, py = p.get("x"), p.get("y")
            if px is not None and math.hypot(sx-px, sy-py) < float(p.get("width",1))/2*0.8:
                issues.append(DFMIssue("DFM-005","Silkscreen over pad","warning",f"Silk text near pad ({px:.1f},{py:.1f})","Move silkscreen away from exposed copper."))
                break
    # DFM-006 component spacing
    cs = B.components
    for i,a in enumerate(cs):
        for b in cs[i+1:]:
            ax, ay = a["center"]["x"], a["center"]["y"]; bx, by = b["center"]["x"], b["center"]["y"]
            dist = math.hypot(ax-bx, ay-by)
            ra = math.hypot(float(a.get("width",2))/2, float(a.get("height",2))/2); rb = math.hypot(float(b.get("width",2))/2, float(b.get("height",2))/2)
            edge = dist - ra - rb
            if 0 <= edge < 1.0:
                issues.append(DFMIssue("DFM-006","Tight component spacing","warning",f"Component spacing ~{edge:.2f}mm <1.0mm min for assembly","Increase spacing for pick-and-place."))
    # DFM-007 board size
    if board_w < 10 or board_h < 10:
        issues.append(DFMIssue("DFM-007","Very small board","warning",f"Board {board_w:.1f}x{board_h:.1f}mm <10x10","Consider panelization."))
    elif board_w > 500 or board_h > 500:
        issues.append(DFMIssue("DFM-007","Large board","warning",f"Board {board_w:.1f}x{board_h:.1f}mm","Check max board size."))
    # DFM-008 via aspect ratio
    for v in B.vias:
        hole=float(v.get("hole_diameter",0.3))
        if hole>0:
            aspect=thick/hole
            if aspect>8:
                issues.append(DFMIssue("DFM-008","High via aspect ratio","critical",f"Via aspect {aspect:.1f}:1 >8:1","Increase drill or reduce thickness."))
    # DFM-009 copper balance
    if layers>=2 and segs:
        from collections import Counter
        c=Counter(s["layer"] for s in segs); tot=sum(c.values()); mx, mxc = c.most_common(1)[0]
        if mxc/tot > 0.85 and tot>10:
            issues.append(DFMIssue("DFM-009","Unbalanced copper","info",f"{mxc/tot*100:.0f}% on {mx}","Distribute copper across layers."))
    # DFM-004 thermal relief (heuristic)
    for p in B.pads:
        if p.get("type") == "pcb_smtpad":
            w=float(p.get("width",0)); h=float(p.get("height",0))
            if max(w,h) > 3.0:
                issues.append(DFMIssue("DFM-004","Consider thermal relief for large pads","info",f"Large pad {max(w,h):.2f}mm may need thermal relief when connected to plane","Use thermal relief spokes on plane connections."))
    # DFM-010 solder bridge (SMT pads, ignore same comp)
    smt = [p for p in B.pads if p.get("type")=="pcb_smtpad"]
    for i,a in enumerate(smt):
        for b in smt[i+1:]:
            if a.get("pcb_component_id")==b.get("pcb_component_id"): continue
            dist=math.hypot(a["x"]-b["x"], a["y"]-b["y"]); edge=dist-(float(a.get("width",1))+float(b.get("width",1)))/2
            if 0 <= edge < 0.2:
                issues.append(DFMIssue("DFM-010","Solder bridge risk","warning",f"SMT gap {edge:.3f}mm <0.2mm","Increase spacing or mask dam."))
    # DFM-011 min drill (only plated holes / vias have drills)
    for p in B.pads:
        if p.get("type") != "pcb_plated_hole":
            continue
        d=float(p.get("hole_diameter", p.get("drill_diameter",0)) or 0)
        if 0 < d < 0.2:
            issues.append(DFMIssue("DFM-011","Drill too small","critical",f"Drill {d:.3f}mm <0.2mm","Increase drill to >=0.2mm."))
    # DFM-012 diff-pair length mismatch — only for recognizable P/N pairs
    # (_P/_N, +/-, _I/_D). Synthetic tscircuit net names are skipped to
    # avoid false positives (upstream lesson: no noisy checks).
    net_len: dict[str,float] = {}
    for s in segs:
        net_len[s["net"]] = net_len.get(s["net"],0)+math.hypot(s["x2"]-s["x1"], s["y2"]-s["y1"])
    names=set(net_len)
    pairs=[]
    for n in names:
        for sfx_p, sfx_n in (("_P","_N"), ("+","-"), ("_I","_D"), ("_H","_L")):
            if n.endswith(sfx_p):
                m = n[:-len(sfx_p)] + sfx_n
                if m in names: pairs.append((n,m)); break
    for p,n in pairs:
        diff=abs(net_len[p]-net_len[n])
        if diff > 5.0:
            issues.append(DFMIssue("DFM-012","Differential pair length mismatch","warning",
                f"{p} vs {n}: {diff:.1f}mm skew > 5mm","Match pair lengths (tune meander)."))
    return issues

def compute_score(issues):
    # Cap per-code contribution to 3 so a systematic autorouter pattern
    # (e.g. 100x acid-trap junctions from tscircuit zigzags) does not
    # collapse the score to 0 — same intent as upstream, which only
    # sees hand-laid boards with few issues.
    from collections import Counter
    per_code = Counter(i.code for i in issues)
    s = 100
    counted: Counter = Counter()
    for i in issues:
        if counted[i.code] >= 3:
            continue
        counted[i.code] += 1
        s -= 15 if i.severity=="critical" else 5 if i.severity=="warning" else 1
    return max(0, s)

def main() -> int:
    import argparse
    ap=argparse.ArgumentParser(description="DFM 12 checks")
    ap.add_argument("--circuit", default="out/circuit.json"); ap.add_argument("--out", default="out/dfm.json")
    a=ap.parse_args()
    try:
        from utils import load_circuit_json
        d = load_circuit_json(a.circuit)
    except Exception:
        d=json.loads(Path(a.circuit).read_text())
        if isinstance(d, dict): d=d.get("elements",[d])
    issues=run_dfm(d)
    report={"score": compute_score(issues), "issue_count": len(issues), "issues": [i.to_dict() for i in issues],
            "note": "DFM-004 thermal relief heuristic added; DFM-012 diff-pair skipped/partial when synthetic net names used"}
    try:
        from utils import save_json
        save_json(a.out, report)
    except Exception:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    return 0

if __name__=="__main__": raise SystemExit(main())
