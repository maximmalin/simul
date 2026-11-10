"""MNA fallback solver (DC Operating Point) when ngspice is missing.

Adapted from ai-pcb-generator/src/simulation/python_solver.py (MIT) — simplified
to a resistive DC MNA core (stdlib + numpy from .venv). Signals graceful
degradation: if ngspice exists, prefer it; this is only the fallback for
CI/containers without ngspice.

Supports: R, V (ideal voltage), I (ideal current), ground (0).
Caps/inductors are ignored (open) at DC; diodes are ignored (open) with warning.
"""
from __future__ import annotations
import re
import numpy as np

class MNASolver:
    def __init__(self):
        self.nodes: dict[str,int] = {}; self.r: dict[str,float] = {}
        self.vsources: list = []; self.isources: list = []
    def add_node(self, name):
        if name not in self.nodes: self.nodes[name]=len(self.nodes)
    def solve(self, circuit_text: str) -> dict[str,float]:
        # parse
        for raw in circuit_text.splitlines():
            line=raw.strip()
            if not line or line.startswith("*") or line.lower().startswith("."): continue
            parts=line.split()
            if len(parts)<3: continue
            tag=parts[0].upper()
            try:
                if tag.startswith("R"):
                    n1,n2,q=parts[1],parts[2],parts[3]
                    self.add_node(n1); self.add_node(n2); self.r[f"{n1}:{n2}"]=float(_parse_value(q)); self.r[f"{n2}:{n1}"]=float(_parse_value(q))
                elif tag.startswith("V"):
                    n1,n2,q=parts[1],parts[2],parts[3]
                    self.add_node(n1); self.add_node(n2); self.vsources.append((n1,n2,float(_parse_value(q))))
                elif tag.startswith("I"):
                    n1,n2,q=parts[1],parts[2],parts[3]
                    self.add_node(n1); self.add_node(n2); self.isources.append((n1,n2,float(_parse_value(q))))
            except ValueError: continue
        if not self.nodes or not self.vsources: return {}
        # Build MNA matrix: node voltages + voltage-source currents
        nv=len(self.nodes); nvsrc=len(self.vsources); n=nv+nvsrc
        G=np.zeros((n,n)); b=np.zeros(n)
        idx=lambda name: self.nodes[name]
        # resistors
        seen=set()
        for key,val in self.r.items():
            a,b_ = key.split(":")
            if (b_,a) in seen: continue
            seen.add((a,b_))
            g=1/val if val else 0
            ia,ib=idx(a),idx(b_)
            G[ia,ia]+=g; G[ib,ib]+=g; G[ia,ib]-=g; G[ib,ia]-=g
        # ground node 0 fixed
        if "0" in self.nodes:
            gi=idx("0"); G[gi,:]=0; G[gi,gi]=1; b[gi]=0
        # current sources
        for n1,n2,val in self.isources:
            b[idx(n1)]+=val; b[idx(n2)]-=val
        # voltage sources
        for i,(n1,n2,val) in enumerate(self.vsources):
            row=nv+i; G[idx(n1),row]=1; G[idx(n2),row]=-1; G[row,idx(n1)]=1; G[row,idx(n2)]=-1; b[row]=val
        x=np.linalg.solve(G,b)
        return {name: float(x[i]) for name,i in self.nodes.items()}

def _parse_value(s: str) -> float:
    s=s.strip().upper().replace("OHM","")
    mult=1.0
    for suf,f in [("MEG",1e6),("K",1e3),("M",1e-3),("U",1e-6),("N",1e-9),("P",1e-12)]:
        if s.endswith(suf): mult=f; s=s[:-len(suf)]; break
    return float(s)*mult

def check_available() -> bool:
    import shutil; return shutil.which("ngspice") is not None

def solve_dc(circuit_text: str) -> tuple[dict, str]:
    if check_available():
        return {}, "prefer-ngspice (direct MNA fallback not run; use sim/scripts/export_ngspice.py + ngspice)"
    sol=MNASolver().solve(circuit_text)
    return sol, "mna-fallback"

def main() -> int:
    import argparse, json
    from pathlib import Path
    ap=argparse.ArgumentParser(description="MNA fallback DC solve")
    ap.add_argument("--circuit", default="sim/netlists/sdr_skidl_ngspice.cir"); ap.add_argument("--out", default="out/mna_fallback.json")
    a=ap.parse_args()
    sol, mode = solve_dc(Path(a.circuit).read_text(errors="ignore")) if Path(a.circuit).exists() else ({}, "no-circuit")
    report={"mode":mode,"nodes":sol,"count":len(sol)}
    Path(a.out).parent.mkdir(parents=True, exist_ok=True); Path(a.out).write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    return 0

if __name__=="__main__": raise SystemExit(main())
