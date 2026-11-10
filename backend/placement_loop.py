"""Placement hill-climbing loop (auto-pcb) adapted to circuit.json.

Loop: propose move -> apply -> score (pcb_score) -> keep/revert -> records.jsonl
       -> playbook distillation (rule-based if no LLM, or via backend/llm.py).

Preserves our strength: the loop only proposes **placement hints** for
tscircuit; final routing stays authoritative inside `build_pcb.mjs`.

CLI: python3 backend/placement_loop.py --iters 20 --out .run/placement_opt
"""
from __future__ import annotations
import json, math, random, time, re
from dataclasses import dataclass, asdict
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from pcb_score import score_circuit, VIA_PENALTY_MM, DRC_PENALTY_MM
try:
    from utils import load_circuit_json as load_circuit
except Exception:
    from pcb_score import load_circuit

@dataclass
class Placement:
    ref: str; x_mm: float; y_mm: float; rot_deg: float

@dataclass
class Move:
    ref: str; x_mm: float; y_mm: float; rot_deg: float; reason: str = ""

def get_placements(circuit):
    out=[]
    for e in circuit:
        if e.get("type")=="pcb_component":
            c=e.get("center") or {}
            out.append(Placement(ref=e.get("source_component_id","?"), x_mm=float(c.get("x",0)), y_mm=float(c.get("y",0)), rot_deg=float(e.get("rotation",0))))
    return out

def _rotate_point(px, py, cx, cy, angle_deg):
    import math
    if angle_deg == 0:
        return px, py
    rad = math.radians(angle_deg)
    cos_a = math.cos(rad)
    sin_a = math.sin(rad)
    dx = px - cx
    dy = py - cy
    nx = dx * cos_a - dy * sin_a
    ny = dx * sin_a + dy * cos_a
    return cx + nx, cy + ny

def apply_move(circuit, move: Move):
    new=[dict(e) for e in circuit]
    # Handle swap
    if move.ref.startswith("SWAP:"):
        try:
            _, ref1, ref2 = move.ref.split(":")
        except Exception:
            return new
        # get centers from original
        c1=None; c2=None
        for e in circuit:
            if e.get("type")=="pcb_component" and e.get("source_component_id")==ref1:
                c1=(e["center"]["x"], e["center"]["y"]); break
        for e in circuit:
            if e.get("type")=="pcb_component" and e.get("source_component_id")==ref2:
                c2=(e["center"]["x"], e["center"]["y"]); break
        if c1 is None or c2 is None: return new
        # swap directly
        # move ref1 from c1 to c2
        dx1, dy1 = c2[0]-c1[0], c2[1]-c1[1]
        # find comp ids
        comp1=None; comp2=None
        for e in circuit:
            if e.get("type")=="pcb_component" and e.get("source_component_id")==ref1: comp1=e.get("pcb_component_id")
            if e.get("type")=="pcb_component" and e.get("source_component_id")==ref2: comp2=e.get("pcb_component_id")
        for e in new:
            if e.get("type")=="pcb_component" and e.get("source_component_id")==ref1:
                e["center"]={"x":c2[0],"y":c2[1]}
            if e.get("type")=="pcb_component" and e.get("source_component_id")==ref2:
                e["center"]={"x":c1[0],"y":c1[1]}
        for e in new:
            if e.get("type") in ("pcb_smtpad", "pcb_plated_hole", "pcb_pad", "pcb_port"):
                if comp1 and e.get("pcb_component_id")==comp1:
                    if "x" in e: e["x"]+=dx1
                    if "y" in e: e["y"]+=dy1
                elif comp2 and e.get("pcb_component_id")==comp2:
                    dx2, dy2 = c1[0]-c2[0], c1[1]-c2[1]
                    if "x" in e: e["x"]+=dx2
                    if "y" in e: e["y"]+=dy2
        return new
    # Move component + its pads/ports by the same delta; handle rotation
    # find old centre
    old=None
    old_rot=None
    comp_id=None
    for e in circuit:
        if e.get("type")=="pcb_component" and e.get("source_component_id")==move.ref:
            old=(e["center"]["x"], e["center"]["y"])
            old_rot = float(e.get("rotation",0))
            comp_id = e.get("pcb_component_id")
            break
    if old is None:
        return new
    dx=move.x_mm-old[0]; dy=move.y_mm-old[1]
    d_rot = (move.rot_deg - (old_rot or 0)) % 360
    for e in new:
        if e.get("type")=="pcb_component" and e.get("source_component_id")==move.ref:
            e["center"]={"x":move.x_mm,"y":move.y_mm}; e["rotation"]=move.rot_deg % 360
    # pads use pcb_component_id
    comp_ids=set()
    if comp_id:
        comp_ids.add(comp_id)
    for e in new:
        if e.get("type") in ("pcb_smtpad", "pcb_plated_hole", "pcb_pad", "pcb_port") and e.get("pcb_component_id") in comp_ids:
            if d_rot != 0 and "x" in e and "y" in e:
                nx, ny = _rotate_point(e["x"], e["y"], old[0], old[1], d_rot)
                e["x"] = nx + dx
                e["y"] = ny + dy
            elif "x" in e and "y" in e:
                e["x"] = e["x"] + dx
                e["y"] = e["y"] + dy
            elif "x" in e: e["x"] = e["x"] + dx
            elif "y" in e: e["y"] = e["y"] + dy
    return new

class RandomProposer:
    def __init__(self, outline, sigma_mm=2.0, seed=0):
        self.outline=outline
        self.sigma=sigma_mm
        self.rng=random.Random(seed)
    def propose(self, placements, history, current_score, breakdown, best_score, playbook="", kind=None, sigma=None):
        sig = self.sigma if sigma is None else sigma
        if kind is None:
            r = self.rng.random()
            kind = "swap" if (r < 0.1 and len(placements) >= 2) else ("rotate" if r < 0.25 else "jitter")
        # swap (like auto-pcb)
        if kind == "swap" and len(placements) >= 2:
            p1 = self.rng.choice(placements)
            p2 = self.rng.choice([p for p in placements if p.ref != p1.ref])
            return Move(f"SWAP:{p1.ref}:{p2.ref}", p2.x_mm, p2.y_mm, p1.rot_deg, reason="swap")
        p=self.rng.choice(placements)
        # rotation
        if kind == "rotate":
            rot = (p.rot_deg + self.rng.choice([90, 180, 270])) % 360
            return Move(p.ref, p.x_mm, p.y_mm, rot, reason="rotate")
        nx=max(self.outline[0]+1, min(self.outline[2]-1, p.x_mm + self.rng.gauss(0,sig)))
        ny=max(self.outline[1]+1, min(self.outline[3]-1, p.y_mm + self.rng.gauss(0,sig)))
        return Move(p.ref, nx, ny, p.rot_deg, reason="random-jitter")


class LLMProposer:
    """Wraps backend/llm.py if available; falls back to Random when disabled."""
    def __init__(self, outline, seed=0, model=None):
        self.outline=outline
        self.rng=random.Random(seed)
        self.model=model
    def propose(self, placements, history, current_score, breakdown, best_score, playbook="", kind=None, sigma=None):
        try:
            import llm
            if getattr(llm, "ENABLED", False):
                hist = []
                for h in history[-12:]:
                    if isinstance(h, dict):
                        hist.append({
                            "move": h.get("move"),
                            "score_before": h.get("score_before"),
                            "score_after": h.get("score_after"),
                            "accepted": h.get("accepted"),
                        })
                    else:
                        hist.append({"move": getattr(h, "move", None), "score_before": getattr(h, "score_before", None),
                                     "score_after": getattr(h, "score_after", None), "accepted": getattr(h, "accepted", None)})
                prompt = (
                    "You are optimizing PCB component placement for minimum score.\n"
                    "Score = trace_length + 5*via + 20*DRC + 200*unconnected + placement_penalty + 0.5*ratsnest.\n"
                    "Lower is better. Only move ONE component. Keep inside board outline.\n"
                    f"Board outline: x in [{self.outline[0]:.1f},{self.outline[2]:.1f}], y in [{self.outline[1]:.1f},{self.outline[3]:.1f}]\n"
                    f"Current score: {current_score:.1f}, best: {best_score:.1f}\n"
                    f"Breakdown: {breakdown}\n"
                    f"Playbook:\n{playbook[:2000]}\n\n"
                    f"Recent history (last {len(hist)}):\n{json.dumps(hist, indent=2)}\n\n"
                    f"Available components ({len(placements)}): " + ", ".join([p.ref for p in placements[:30]]) + ("..." if len(placements)>30 else "") + "\n\n"
                    "Suggest ONE move as JSON: {\"ref\": \"...\", \"x_mm\": ..., \"y_mm\": ..., \"rot_deg\": 0, \"reason\": \"...\"}\n"
                    "Be specific and think about clustering, edges, and DRC reduction."
                )
                try:
                    messages = [{"role": "user", "content": prompt}]
                    turn = llm.chat(messages, fast=True, max_tokens=256)
                    text = (turn.text or "") if turn else ""
                    m = re.search(r'\{[^{}]*"ref"[^{}]*\}', text)
                    if m:
                        data = json.loads(m.group(0))
                        return Move(ref=str(data["ref"]), x_mm=float(data["x_mm"]), y_mm=float(data.get("y_mm", data.get("y",0))),
                                    rot_deg=float(data.get("rot_deg", data.get("rot",0))), reason=data.get("reason","llm"))
                except Exception:
                    pass
        except Exception:
            pass
        return RandomProposer(self.outline, 2.0, seed=self.rng.randrange(1<<30)).propose(placements, history, current_score, breakdown, best_score, playbook)

class BanditKinds:
    """UCB1 over move kinds (jitter/swap/rotate) — anti-stagnation.

    2026 practice (IBM AAAI-2026 bandits+LLM): replace fixed move-type
    probabilities with a bandit that exploits what actually gets accepted
    while forced exploration (n=0) prevents premature convergence.
    """
    def __init__(self, kinds=("jitter", "swap", "rotate"), c=1.4):
        self.stats = {k: [0, 0] for k in kinds}  # [successes, pulls]
        self.c = c
    def pick(self, rng):
        for k, (s, n) in self.stats.items():
            if n == 0:
                return k  # forced exploration of unpulled arms
        total = sum(n for _, n in self.stats.values())
        best, best_v = None, -1.0
        for k, (s, n) in self.stats.items():
            v = s / n + self.c * math.sqrt(2 * math.log(total + 1) / n)
            if v > best_v:
                best_v, best = v, k
        return best
    def update(self, kind, success):
        if kind in self.stats:
            s, n = self.stats[kind]
            self.stats[kind] = [s + (1 if success else 0), n + 1]
    def rates(self):
        return {k: (s / n if n else 0.0) for k, (s, n) in self.stats.items()}
    def pulls(self):
        return {k: n for k, (_, n) in self.stats.items()}


def perturbation_sweep(circuit, sigma_mm, trials=10, seed=0):
    """Phase-0 validation from auto-pcb: jitter all placements, re-score."""
    rng=random.Random(seed); results=[]
    for t in range(trials):
        pert=[]
        for e in circuit:
            if e.get("type")=="pcb_component":
                e2=dict(e); c=dict(e["center"]); c["x"]+=rng.gauss(0,sigma_mm); c["y"]+=rng.gauss(0,sigma_mm); e2["center"]=c; pert.append(e2)
            else:
                pert.append(dict(e))
        s=score_circuit(pert)
        results.append({"trial":t,"sigma_mm":sigma_mm,"score":s.score})
    return results

def distill_playbook(records_path: Path, out_path: Path):
    recs=[json.loads(l) for l in Path(records_path).read_text().splitlines() if l.strip()]
    lessons=[]
    # rule-based distillation (no LLM needed): what actually helped
    accepted=[r for r in recs if r.get("accepted")]
    rejected=[r for r in recs if r.get("phase")=="step" and not r.get("accepted")]
    if accepted:
        best=min(accepted, key=lambda r: (r.get("score_after",1e99)))
        lessons.append(f"Best move was {best.get('move',{}).get('ref')} score {best.get('score_before')}->{best.get('score_after')}. Prioritise hub components early.")
    if rejected:
        lessons.append(f"{len(rejected)} reverts: after 2-3 consecutive REVERTs for a component switch focus or try a swap.")
    lessons.append("Push-to-perimeter heuristic: large passive components to edges reduce DRC; run perturbation sweep to verify score correlates with sigma before trusting LLM moves.")
    
    # Try LLM distillation if available (like auto-pcb update_playbook)
    try:
        import llm
        if getattr(llm, "ENABLED", False) and len(recs) > 5:
            try:
                # Build compact summary like auto-pcb
                summary_lines = []
                init = next((r for r in recs if r.get("phase") == "init"), None)
                if init:
                    summary_lines.append(f"INIT score={init.get('score')}")
                for r in recs:
                    if r.get("phase") != "step": continue
                    m = r.get("move", {})
                    ok = "KEEP" if r.get("accepted") else "REVERT"
                    summary_lines.append(f"i{r['iter']:03d} {ok} {m.get('ref')} score {r.get('score_before')}->{r.get('score_after')} reason:{m.get('reason','')[:60]}")
                prompt = (
                    "Distill 2-5 transferable strategy lessons from this PCB placement optimization run.\n"
                    "Focus on patterns that would help future runs (not board-specific coordinates).\n"
                    "Each lesson should be one sentence of actionable advice.\n\n"
                    "Run data:\n" + "\n".join(summary_lines[-60:]) + "\n\n"
                    "Return as JSON: {\"lessons\": [\"...\", \"...\"]}"
                )
                messages = [{"role": "user", "content": prompt}]
                turn = llm.chat(messages, fast=True, max_tokens=512)
                text = (turn.text or "") if turn else ""
                m = __import__('re').search(r'\{.*"lessons".*\}', text, __import__('re').S)
                if m:
                    data = json.loads(m.group(0))
                    if data.get("lessons"):
                        lessons = data["lessons"][:5]  # LLM lessons take precedence if good
            except Exception:
                pass
    except Exception:
        pass
    
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text("# Placement playbook (auto-distilled)\n\n" + "\n".join(f"- {x}" for x in lessons) + "\n")
    return lessons

def run_loop(circuit_path, out_dir, iters=20, proposer_name="random", seed=0, drc_text="", strip_traces=False):
    out=Path(out_dir); out.mkdir(parents=True, exist_ok=True)
    circuit=load_circuit(Path(circuit_path))
    placements=get_placements(circuit)
    board_el=next((e for e in circuit if e.get("type")=="pcb_board"), {})
    w=float(board_el.get("width",88.6)); h=float(board_el.get("height",85.08))
    outline=(-w/2, -h/2, w/2, h/2)
    base=score_circuit(circuit, drc_text, strip_traces=strip_traces)
    print(f"[init] score={base.score:.1f}  {base.breakdown}")
    proposer=RandomProposer(outline, seed=seed) if proposer_name=="random" else LLMProposer(outline, seed=seed)
    playbook_path=Path(".run/placement_playbook.md")
    playbook=playbook_path.read_text() if playbook_path.exists() else ""
    cur_score=base.score; cur_break=base.breakdown; records=out/"records.jsonl"
    logf=records.open("w")
    logf.write(json.dumps({"iter":-1,"phase":"init","score":cur_score,"breakdown":cur_break})+"\n"); logf.flush()
    accepted=0; t0=time.time()
    best_score = cur_score
    best_circuit = [dict(e) for e in circuit]
    # anti-stagnation: UCB bandit over move kinds + annealing with reheating
    bandit = BanditKinds()
    sigma_base = 2.0; sigma = sigma_base
    stale = 0; reheats = 0; plateaus = 0
    for it in range(iters):
        kind = bandit.pick(proposer.rng if hasattr(proposer, "rng") else random.Random(seed))
        move=proposer.propose(placements, [], cur_score, cur_break, best_score, playbook, kind=kind, sigma=sigma)
        cand=apply_move(circuit, move)
        r=score_circuit(cand, drc_text, strip_traces=strip_traces)
        acc=r.score < cur_score
        bandit.update(kind, acc)
        logf.write(json.dumps({"iter":it,"phase":"step","move":asdict(move),"kind":kind,"sigma":round(sigma,2),"score_before":cur_score,"score_after":r.score,"accepted":acc,"breakdown_after":r.breakdown})+"\n"); logf.flush()
        if acc:
            circuit=cand; placements=get_placements(circuit); cur_score=r.score; cur_break=r.breakdown; accepted+=1
            stale = 0
            sigma = max(0.5, sigma * 0.9)          # exploit: tighten the search
            if cur_score < best_score:
                best_score = cur_score
                best_circuit = [dict(e) for e in circuit]
        else:
            stale += 1
            if stale >= 5:                          # plateau -> reheat (explore)
                sigma = min(sigma_base * 2.0, sigma * 2.0)
                stale = 0; reheats += 1; plateaus += 1
                logf.write(json.dumps({"iter":it,"phase":"reheat","sigma":round(sigma,2)})+"\n"); logf.flush()
        print(f"[iter {it}] {kind:6s} {move.ref:20s} -> {r.score:.1f} ({'KEEP' if acc else 'REVERT'}) drc={r.drc_violations}")
    logf.close()
    # Save best circuit state
    try:
        (out/"best_circuit.json").write_text(json.dumps(best_circuit, indent=2))
    except Exception:
        pass
    lessons=distill_playbook(records, Path(".run/placement_playbook.md"))
    summary={"iters":iters,"accepted":accepted,"accept_rate":accepted/iters if iters else 0,"start_score":base.score,"final_score":cur_score,"best_score":best_score,"proposer":proposer_name,"seed":seed,"wall_time_s":time.time()-t0,"lessons":lessons,"strip_traces":strip_traces,
             "anti_stagnation":{"bandit_accept_rates":bandit.rates(),"bandit_pulls":bandit.pulls(),
                                "reheats":reheats,"plateaus":plateaus,"sigma_final":round(sigma,2)}}
    (out/"summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))
    return summary

def main() -> int:
    import argparse
    ap=argparse.ArgumentParser()
    ap.add_argument("--circuit", default="out/circuit.json"); ap.add_argument("--out", default=".run/placement_opt")
    ap.add_argument("--iters", type=int, default=20); ap.add_argument("--proposer", choices=["random","llm"], default="random")
    ap.add_argument("--seed", type=int, default=0); ap.add_argument("--sweep", action="store_true", help="run perturbation sweep instead of loop")
    ap.add_argument("--strip-traces", action="store_true", help="strip traces before scoring (auto-pcb style)")
    a=ap.parse_args()
    if a.sweep:
        circuit=load_circuit(Path(a.circuit))
        out=[]
        for sigma in (0,0.5,1,2,5):
            out.extend(perturbation_sweep(circuit, sigma, trials=5, seed=a.seed))
        print(json.dumps(out, indent=2))
        Path(a.out).mkdir(parents=True, exist_ok=True)
        Path(Path(a.out)/"sweep.json").write_text(json.dumps(out, indent=2))
        return 0
    run_loop(a.circuit, a.out, a.iters, a.proposer, a.seed, strip_traces=a.strip_traces)
    return 0

if __name__=="__main__": raise SystemExit(main())
