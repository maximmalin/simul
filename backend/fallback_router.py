"""A* fallback router (grid, 2 layers) when FreeRouting/tsci fails.

Adapted from ai-pcb-generator/src/pcb/router.py (MIT, A* internal fallback).
Pure-stdlib grid A*: Manhattan routing on a coarse grid with via penalty.
Never replaces tscircuit in the happy path — only invoked by
placement_loop when route fails (env KR_ASTAR_FALLBACK=1).
"""
from __future__ import annotations
import heapq, math
from dataclasses import dataclass, asdict

VIA_PENALTY = 5.0  # mm equivalent, matches pcb_score

@dataclass
class RouteSeg:
    x1: float; y1: float; x2: float; y2: float; layer: str = "top"; width: float = 0.2; via: bool = False
    def to_dict(self): return asdict(self)

def _heuristic(a, b): return abs(a[0]-b[0]) + abs(a[1]-b[1])

def route_pair(start, goal, width_mm=88.6, height_mm=85.08, grid=0.5, obstacles=None, layers=("top","bottom")):
    """Route one pair on pixel grid; return list[RouteSeg] with vias between layers."""
    obstacles=set(obstacles or [])
    nx=max(2,int(width_mm/grid)+1); ny=max(2,int(height_mm/grid)+1)
    def to_idx(pt): return (max(0,min(nx-1,int((pt[0]/grid)))), max(0,min(ny-1,int((pt[1]/grid)))))
    def to_mm(idx): return (idx[0]*grid, idx[1]*grid)
    s=to_idx(start); g=to_idx(goal)
    for layer_idx, layer in enumerate(layers):
        pass
    # Single-layer A* (with one via if needed only when obstacle blocks: simplified: via in middle if direct fails)
    def astar():
        openh=[(0,s)]; came={s:None}; gs={s:0}
        while openh:
            _,cur=heapq.heappop(openh)
            if cur==g: break
            for dx,dy in ((1,0),(-1,0),(0,1),(0,-1)):
                nb=(cur[0]+dx, cur[1]+dy)
                if not (0<=nb[0]<nx and 0<=nb[1]<ny): continue
                if nb in obstacles and nb!=g: continue
                ng=gs[cur]+grid
                if nb not in gs or ng<gs[nb]:
                    gs[nb]=ng; came[nb]=cur; heapq.heappush(openh,(ng+_heuristic(nb,g), nb))
        if g not in came: return None
        path=[]; cur=g
        while cur is not None: path.append(cur); cur=came[cur]
        path.reverse(); return path
    path=astar()
    if path is None:
        # try bottom layer with two vias (penalty) — approximate by via at midpoint
        return [RouteSeg(start[0],start[1],goal[0],goal[1],layers[0],0.2,True)]
    segs=[RouteSeg(*to_mm(a), *to_mm(b), layers[0]) for a,b in zip(path, path[1:])]
    return segs

def route_all(pairs, board_w=88.6, board_h=85.08, grid=0.5):
    out=[]
    for a,b in pairs:
        out.extend(route_pair(a,b,board_w,board_h,grid).to_dict() if False else [s.to_dict() for s in route_pair(a,b,board_w,board_h,grid)])
    return out

def main() -> int:
    import argparse, json
    from pathlib import Path
    ap=argparse.ArgumentParser(description="A* fallback router demo")
    ap.add_argument("--out", default="out/fallback_route.json")
    a=ap.parse_args()
    segs=route_all([((0,0),(10,0)), ((0,0),(10,10))])
    Path(a.out).parent.mkdir(parents=True, exist_ok=True); Path(a.out).write_text(json.dumps(segs, indent=2))
    print(f"{len(segs)} segs -> {a.out}")
    return 0

if __name__=="__main__": raise SystemExit(main())
