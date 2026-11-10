"""Fix-loop: an agent fixes checker findings until the output is clean.

Pattern (auto-pcb eval-loop applied to our checkers):
  run checks -> collect issues -> (LLM | heuristic) proposes fixes -> apply
  -> re-check -> keep if improved, else revert -> repeat until clean / max iters.

Two lanes:
  * **auto** — geometric fixes safe to apply to `circuit.json`:
      - components outside the board outline  -> move inside (apply_move)
      - too-close component pairs (DFM-006)   -> push apart
      - narrow power traces (DFM-001)         -> widen to >= 0.5 mm
    Evaluator: auto-issue count first, pcb_score second (keep/revert).
  * **advisory** — semantic findings (ERC, DFM acid traps, diff pairs) that
    need a design decision: written as structured tasks to `out/fix_tasks.json`
    for the engineer / upstream agent. Never silently "fixed".

Outputs:
  out/fix_loop_report.json   {clean, iterations, fixed, remaining, tasks, score...}
  out/fix_tasks.json         advisory tasks (if any)
  .run/fix_loop/best_circuit.json   best state found (never clobbers out/circuit.json;
                                     pass --write-back to overwrite it)

Source ideas: auto-pcb loop.py (keep/revert + records), ai-pcb-generator
dfm.py (check codes), upstream parser.py (auto-repair philosophy, MIT).
"""
from __future__ import annotations
import json, math, random, time
from dataclasses import dataclass, asdict
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))

from pcb_score import score_circuit
from dfm_engine import run_dfm, extract_board
try:
    from utils import load_circuit_json as load_circuit, save_json
except Exception:
    from pcb_score import load_circuit
    save_json = None

MIN_EDGE_MM = 1.0        # DFM-006 component spacing
MIN_POWER_W_MM = 0.5     # DFM-001 power trace width
EDGE_MARGIN_MM = 0.5     # keep-out from outline


@dataclass
class AutoIssue:
    kind: str          # "edge" | "spacing" | "power_width"
    refs: list
    detail: str
    def to_dict(self): return asdict(self)


def _outline(circuit):
    board = next((e for e in circuit if e.get("type") == "pcb_board"), {})
    w = float(board.get("width", 88.6)); h = float(board.get("height", 85.08))
    return (-w / 2, -h / 2, w / 2, h / 2)


def _half_diag(e):
    return math.hypot(float(e.get("width", 2)) / 2, float(e.get("height", 2)) / 2)


def collect_auto(circuit) -> list[AutoIssue]:
    """Geometric issues that are safe to auto-fix."""
    issues: list[AutoIssue] = []
    ox0, oy0, ox1, oy1 = _outline(circuit)
    comps = [e for e in circuit if e.get("type") == "pcb_component"]
    # edge violations (body must fit inside outline)
    for e in comps:
        x, y = e["center"]["x"], e["center"]["y"]
        r = _half_diag(e)
        if (x - r < ox0 + EDGE_MARGIN_MM or x + r > ox1 - EDGE_MARGIN_MM or
                y - r < oy0 + EDGE_MARGIN_MM or y + r > oy1 - EDGE_MARGIN_MM):
            issues.append(AutoIssue("edge", [e.get("source_component_id", "?")],
                f"{e.get('source_component_id')} body crosses board outline"))
    # too-close pairs
    for i, a in enumerate(comps):
        ax, ay = a["center"]["x"], a["center"]["y"]; ra = _half_diag(a)
        for b in comps[i + 1:]:
            bx, by = b["center"]["x"], b["center"]["y"]; rb = _half_diag(b)
            edge = math.hypot(ax - bx, ay - by) - ra - rb
            if edge < MIN_EDGE_MM:
                issues.append(AutoIssue("spacing",
                    [a.get("source_component_id", "?"), b.get("source_component_id", "?")],
                    f"gap {edge:.2f}mm < {MIN_EDGE_MM}mm"))
    # narrow power traces
    power = {"VCC", "VDD", "GND", "VSS", "5V", "3V3", "3.3V", "12V", "+5V",
             "+3.3V", "+12V", "VIN", "V+", "V-", "VOUT", "VBAT", "PWR"}
    for t in circuit:
        if t.get("type") != "pcb_trace":
            continue
        net = (t.get("connection_name") or t.get("source_net_id") or "").upper()
        if not any(p in net for p in power):
            continue
        for r in (t.get("route") or []):
            if r.get("width", 1.0) < MIN_POWER_W_MM:
                issues.append(AutoIssue("power_width", [t.get("source_trace_id", "?")],
                    f"net {net} width {r.get('width')} < {MIN_POWER_W_MM}"))
                break
    return issues


def collect_advisory(circuit) -> list[dict]:
    """Semantic findings needing a design decision -> tasks, not silent fixes."""
    tasks: list[dict] = []
    for i in run_dfm(circuit):
        if i.code in ("DFM-006", "DFM-001"):
            continue  # auto-fixable, handled by collect_auto
        tasks.append({"source": "dfm", "code": i.code, "severity": i.severity,
                      "message": i.title, "recommendation": i.recommendation,
                      "refs": i.refs or []})
    # ERC from KiCad netlist, if present
    netlist = Path("hardware/kicad/schematic.net")
    if netlist.exists():
        try:
            from erc_zones import parse_kicad_netlist, run_erc_netlist
            for i in run_erc_netlist(parse_kicad_netlist(netlist)):
                tasks.append({"source": "erc", "code": i.code, "severity": i.severity,
                              "message": i.message, "recommendation": i.auto_fix,
                              "refs": i.refs or []})
        except Exception:
            pass
    return tasks


# ---------------------------------------------------------------------------
# Fixers (each returns a new circuit; None if not applicable)
# ---------------------------------------------------------------------------

def fix_edge(circuit, issue: AutoIssue, rng) -> list | None:
    from placement_loop import get_placements, apply_move, Move
    ox0, oy0, ox1, oy1 = _outline(circuit)
    ref = issue.refs[0]
    pl = next((p for p in get_placements(circuit) if p.ref == ref), None)
    if pl is None:
        return None
    # target: nearest valid interior point with margin for body
    comp = next((e for e in circuit if e.get("type") == "pcb_component"
                 and e.get("source_component_id") == ref), None)
    r = _half_diag(comp) if comp else 1.0
    tx = min(max(pl.x_mm, ox0 + r + EDGE_MARGIN_MM), ox1 - r - EDGE_MARGIN_MM)
    ty = min(max(pl.y_mm, oy0 + r + EDGE_MARGIN_MM), oy1 - r - EDGE_MARGIN_MM)
    if abs(tx - pl.x_mm) < 1e-6 and abs(ty - pl.y_mm) < 1e-6:
        tx = ox0 + r + EDGE_MARGIN_MM + rng.uniform(0, 2)
        ty = oy0 + r + EDGE_MARGIN_MM + rng.uniform(0, 2)
    return apply_move(circuit, Move(ref, tx, ty, pl.rot_deg, reason="fix-edge"))


def fix_spacing(circuit, issue: AutoIssue, rng) -> list | None:
    from placement_loop import get_placements, apply_move, Move
    ox0, oy0, ox1, oy1 = _outline(circuit)
    if len(issue.refs) < 2:
        return None
    pl = {p.ref: p for p in get_placements(circuit)}
    a, b = pl.get(issue.refs[0]), pl.get(issue.refs[1])
    if a is None or b is None:
        return None
    dx, dy = a.x_mm - b.x_mm, a.y_mm - b.y_mm
    dist = math.hypot(dx, dy)
    if dist < 1e-6:
        dx, dy, dist = 1.0, 0.0, 1.0
    # push each apart by half the needed extra, with a little randomness
    need = MIN_EDGE_MM + 1.0 - _gap(a, b, circuit)
    if need <= 0:
        return None
    step = max(need / 2, 0.5) + rng.uniform(0, 0.5)
    ux, uy = dx / dist, dy / dist
    cand = circuit
    for ref, sign in ((a.ref, +1), (b.ref, -1)):
        p = pl[ref]
        nx = min(max(p.x_mm + sign * ux * step, ox0 + 1), ox1 - 1)
        ny = min(max(p.y_mm + sign * uy * step, oy0 + 1), oy1 - 1)
        cand = apply_move(cand, Move(ref, nx, ny, p.rot_deg, reason="fix-spacing"))
    return cand


def _gap(a, b, circuit) -> float:
    ra = _half_diag(next((e for e in circuit if e.get("type") == "pcb_component"
                          and e.get("source_component_id") == a.ref), {}) or {})
    rb = _half_diag(next((e for e in circuit if e.get("type") == "pcb_component"
                          and e.get("source_component_id") == b.ref), {}) or {})
    return math.hypot(a.x_mm - b.x_mm, a.y_mm - b.y_mm) - ra - rb


def fix_power_widths(circuit) -> tuple[list, int]:
    """Deterministic: widen power-net trace segments to MIN_POWER_W_MM."""
    fixed = []
    for t in circuit:
        if t.get("type") != "pcb_trace":
            continue
        net = (t.get("connection_name") or "").upper()
        if not any(p in net for p in ("VCC", "VDD", "GND", "5V", "3V3", "12V", "VIN", "VBAT")):
            continue
        changed = False
        for r in (t.get("route") or []):
            if r.get("width", 1.0) < MIN_POWER_W_MM:
                r["width"] = MIN_POWER_W_MM
                changed = True
        if changed:
            fixed.append(t.get("source_trace_id", "?"))
    return circuit, len(fixed)


# ---------------------------------------------------------------------------
# LLM fixer (optional) — proposes issue-driven moves, falls back to heuristic
# ---------------------------------------------------------------------------

def llm_propose(circuit, issues: list[AutoIssue], outline, playbook: str = "") -> list | None:
    try:
        import llm
        if not getattr(llm, "ENABLED", False):
            return None
        comps = [{"ref": e.get("source_component_id"), "x": e["center"]["x"],
                  "y": e["center"]["y"]}
                 for e in circuit if e.get("type") == "pcb_component"]
        prompt = (
            "You are fixing PCB layout issues. Board outline: "
            f"x[{outline[0]:.1f},{outline[2]:.1f}] y[{outline[1]:.1f},{outline[3]:.1f}].\n"
            "Issues to fix:\n" + json.dumps([i.to_dict() for i in issues], indent=1) +
            "\nComponents:\n" + json.dumps(comps[:60]) +
            (f"\nLessons from past runs (playbook):\n{playbook[:800]}\n" if playbook else "") +
            "\nPropose up to 5 moves as JSON array: "
            "[{\"ref\":\"...\",\"x_mm\":..,\"y_mm\":..,\"reason\":\"...\"}] — "
            "keep bodies fully inside the outline, increase gaps >= 1mm."
        )
        turn = llm.chat([{"role": "user", "content": prompt}], fast=True, max_tokens=512)
        text = (turn.text or "").strip() if turn else ""
        m = __import__("re").search(r"\[.*\]", text, __import__("re").S)
        if not m:
            return None
        moves = json.loads(m.group(0))
        if not isinstance(moves, list):
            return None
        from placement_loop import get_placements, apply_move, Move
        valid = {p.ref for p in get_placements(circuit)}
        cand = circuit
        applied = 0
        for mv in moves[:5]:
            ref = str(mv.get("ref", ""))
            if ref not in valid:
                continue
            x, y = float(mv.get("x_mm", 0)), float(mv.get("y_mm", 0))
            if not (outline[0] < x < outline[2] and outline[1] < y < outline[3]):
                continue
            pl = next(p for p in get_placements(cand) if p.ref == ref)
            cand = apply_move(cand, Move(ref, x, y, pl.rot_deg, reason="llm-fix"))
            applied += 1
        return cand if applied else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Cross-run memory (playbook), judge pass, feedback trail, history
# ---------------------------------------------------------------------------

PLAYBOOK_PATH = Path(".run/fix_playbook.md")

def read_playbook() -> str:
    return PLAYBOOK_PATH.read_text() if PLAYBOOK_PATH.exists() else ""

def distill_fix_playbook(records: list, path: Path = PLAYBOOK_PATH) -> list[str]:
    """Rule-based lesson distillation (no LLM needed): what worked, what are traps."""
    lessons: list[str] = []
    by_kind: dict[str, dict] = {}
    reject_refs: dict[str, int] = {}
    for r in records:
        if "accepted" not in r:
            continue
        k = r.get("issue", {}).get("kind", "?")
        d = by_kind.setdefault(k, {"ok": 0, "no": 0})
        d["ok" if r["accepted"] else "no"] += 1
        if not r["accepted"]:
            for ref in r.get("issue", {}).get("refs", []):
                reject_refs[ref] = reject_refs.get(ref, 0) + 1
    for k, d in by_kind.items():
        lessons.append(f"{k}: {d['ok']} fixed / {d['no']} rejected this run")
    for ref, n in sorted(reject_refs.items(), key=lambda x: -x[1]):
        if n >= 3:
            lessons.append(f"trap: {ref} rejected {n}x — deprioritize, try a different kind of move")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# Fix-loop playbook (auto-distilled, persists across runs)\n\n"
                    + "\n".join(f"- {x}" for x in lessons) + "\n")
    return lessons

def judge_plan(tasks: list, path: Path = Path("out/fix_plan.md")) -> bool:
    """LLM-as-Judge (passive -> prioritized plan): rank advisory tasks into an
    actionable fix plan. Never auto-applies risky changes (controlled autonomy)."""
    if not tasks:
        return False
    try:
        import llm
        if not getattr(llm, "ENABLED", False):
            return False
        top = sorted(tasks, key=lambda t: {"error": 0, "critical": 0, "warning": 1, "info": 2}
                     .get(t.get("severity", "info"), 3))[:30]
        prompt = (
            "You are the judge reviewing PCB check findings. Produce a prioritized "
            "markdown fix plan: for each group of findings give root cause, concrete "
            "design change, and effort (S/M/L). Be specific and concise.\n\nFindings:\n"
            + json.dumps(top, indent=1, ensure_ascii=False)
        )
        turn = llm.chat([{"role": "user", "content": prompt}], fast=True, max_tokens=1500)
        text = (turn.text or "").strip() if turn else ""
        if text and len(text) > 40:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# Fix plan (LLM-as-Judge, advisory only)\n\n" + text + "\n")
            return True
    except Exception:
        pass
    return False

def write_log(path: Path, report: dict) -> None:
    """Human-readable feedback trail (2026 transparency practice)."""
    lines = ["# Fix-loop log", "",
             f"status: **{report.get('status')}**  clean: {report.get('clean')}",
             f"init: auto_issues={report['auto_issues']['start']} score={report['score']['start']}",
             f"final: auto_issues={report['auto_issues']['final']} score={report['score']['final']}",
             f"iterations: {report['iterations']}, fixed: {report['fixed']}, "
             f"reheats/escalations: {report.get('escalated', False)}", "",
             "## What was checked", "- geometric: board outline, component spacing >= 1mm, power trace width",
             "- advisory (not auto-fixed): DFM + ERC findings -> fix_tasks.json", "",
             "## Changes applied"]
    for a in report.get("fixed_actions", []):
        lines.append(f"- iter {a.get('iter')}: **{a.get('action')}** {a.get('refs', '')} {a.get('detail', '')}")
    rejected = [r for r in report.get("records", []) if r.get("accepted") is False]
    if rejected:
        lines.append("")
        lines.append("## Rejected attempts (kept state)")
        for r in rejected[:20]:
            lines.append(f"- iter {r.get('iter')}: {r.get('issue', {}).get('kind')} "
                         f"{r.get('issue', {}).get('refs')} — auto {r.get('auto_before')}→{r.get('auto_after')}")
    lines += ["", "## Remaining / escalation",
              f"- auto-fixable left: {len(report.get('remaining_auto', []))}",
              f"- advisory tasks: {report.get('task_count', 0)} (see fix_tasks.json, plan: fix_plan.md)"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")

def append_history(path: Path, report: dict) -> None:
    """Trend across runs (drift monitoring)."""
    entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
             "clean": report.get("clean"), "status": report.get("status"),
             "auto": report.get("auto_issues"), "score": report.get("score"),
             "tasks": report.get("task_count"), "iterations": report.get("iterations")}
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(entry) + "\n")


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def evaluate(circuit) -> tuple[int, float]:
    auto = len(collect_auto(circuit))
    score = score_circuit(circuit).score
    return auto, score


def run_fix_loop(circuit_path="out/circuit.json", out_dir=".run/fix_loop",
                 report_path="out/fix_loop_report.json", iters=40, seed=0,
                 use_llm=True, write_back=False, tasks_path="out/fix_tasks.json",
                 write_outputs=True):
    t0 = time.time()
    rng = random.Random(seed)
    circuit = load_circuit(Path(circuit_path))
    playbook = read_playbook()
    tasks = collect_advisory(circuit)
    auto_n, cur_score = evaluate(circuit)
    start_auto, start_score = auto_n, cur_score
    best_auto, best_score, best_circuit = auto_n, cur_score, [dict(e) for e in circuit]
    records = []
    fixed_actions = []
    # anti-stagnation: per-ref cooldown after repeated rejects + stale escalation
    ref_fail: dict[str, int] = {}
    stale = 0
    status = "max_iters"
    print(f"[fix-loop init] auto_issues={auto_n} score={cur_score:.1f} advisory={len(tasks)} playbook={'yes' if playbook else 'no'}")

    for it in range(iters):
        if auto_n == 0:
            status = "clean"
            break
        issues = collect_auto(circuit)
        if not issues:
            status = "clean"
            break
        # cooldown: refs rejected >=3 times are deprioritized (local traps)
        fresh = [i for i in issues
                 if not all(ref_fail.get(r, 0) >= 3 for r in i.refs)]
        if not fresh:
            status = "escalated"   # everything stuck -> human/design change
            break
        # power widths: deterministic, apply once per iteration head
        pw = [i for i in fresh if i.kind == "power_width"]
        if pw:
            circuit, n = fix_power_widths(circuit)
            if n:
                fixed_actions.append({"iter": it, "action": "power_width", "count": n})
                stale = 0
            auto_n, cur_score = evaluate(circuit)
            continue
        # choose issue: rotate through kinds for diversity
        issue = fresh[it % len(fresh)]
        # propose fix: LLM first (with playbook memory), heuristic fallback
        cand = None
        if use_llm:
            cand = llm_propose(circuit, [issue], _outline(circuit), playbook)
        if cand is None:
            if issue.kind == "edge":
                cand = fix_edge(circuit, issue, rng)
            elif issue.kind == "spacing":
                cand = fix_spacing(circuit, issue, rng)
        if cand is None:
            records.append({"iter": it, "issue": issue.to_dict(), "accepted": False,
                            "note": "no fix available"})
            stale += 1
            for r in issue.refs:
                ref_fail[r] = ref_fail.get(r, 0) + 1
            if stale >= 10:
                status = "escalated"
                break
            continue
        new_auto, new_score = evaluate(cand)
        accepted = (new_auto < auto_n) or (new_auto == auto_n and new_score < cur_score)
        records.append({"iter": it, "issue": issue.to_dict(), "accepted": accepted,
                        "auto_before": auto_n, "auto_after": new_auto,
                        "score_before": cur_score, "score_after": new_score})
        if accepted:
            circuit = cand
            auto_n, cur_score = new_auto, new_score
            stale = 0
            for r in issue.refs:
                ref_fail.pop(r, None)   # success clears the cooldown
            fixed_actions.append({"iter": it, "action": issue.kind, "refs": issue.refs,
                                  "detail": issue.detail})
            if (auto_n, cur_score) < (best_auto, best_score):
                best_auto, best_score = auto_n, cur_score
                best_circuit = [dict(e) for e in circuit]
            print(f"[fix {it}] {issue.kind} {issue.refs} -> auto={auto_n} score={cur_score:.1f} KEEP")
        else:
            stale += 1
            for r in issue.refs:
                ref_fail[r] = ref_fail.get(r, 0) + 1
            print(f"[fix {it}] {issue.kind} {issue.refs} REVERT (auto={new_auto}, fails={ref_fail})")
            if stale >= 10:               # no progress -> escalate, don't spin
                status = "escalated"
                break
    else:
        status = "max_iters"

    if auto_n == 0:
        status = "clean"

    # refresh advisory tasks after fixes (DFM re-run on final state)
    tasks = collect_advisory(circuit)
    clean = auto_n == 0
    lessons = distill_fix_playbook(records) if write_outputs else []
    report = {
        "clean": clean,
        "status": status,
        "clean_note": {
            "clean": "all auto-fixable issues resolved; advisory tasks need a "
                     "designer/agent decision — see fix_tasks.json",
            "escalated": "stuck: repeated rejects / cooldown on every remaining issue "
                         "— needs a human or upstream design change",
            "max_iters": "iteration budget spent with auto-fixable issues remaining",
        }.get(status, status),
        "iterations": len([r for r in records if "accepted" in r]),
        "fixed": len(fixed_actions),
        "fixed_actions": fixed_actions,
        "escalated": status == "escalated",
        "remaining_auto": [i.to_dict() for i in collect_auto(circuit)],
        "task_count": len(tasks),
        "auto_issues": {"start": start_auto, "final": auto_n, "best": best_auto},
        "score": {"start": round(start_score, 1), "final": round(cur_score, 1),
                  "best": round(best_score, 1)},
        "records": records,
        "lessons": lessons,
        "wall_time_s": round(time.time() - t0, 2),
        "seed": seed,
        "llm_used": use_llm,
    }
    if write_outputs:
        out = Path(out_dir); out.mkdir(parents=True, exist_ok=True)
        (out / "best_circuit.json").write_text(json.dumps(best_circuit, indent=2))
        (out / "records.jsonl").write_text("\n".join(json.dumps(r) for r in records) + "\n")
        Path(report_path).parent.mkdir(parents=True, exist_ok=True)
        Path(report_path).write_text(json.dumps(report, indent=2, ensure_ascii=False))
        if tasks:
            Path(tasks_path).parent.mkdir(parents=True, exist_ok=True)
            Path(tasks_path).write_text(json.dumps(
                {"task_count": len(tasks), "tasks": tasks}, indent=2, ensure_ascii=False))
        elif Path(tasks_path).exists():
            Path(tasks_path).unlink()
        write_log(Path(report_path).with_suffix(".md"), report)
        append_history(Path("out/fix_loop_history.jsonl"), report)
        if tasks and use_llm:
            judge_plan(tasks)
        if write_back:
            src = Path(circuit_path)
            bak = src.with_suffix(src.suffix + ".bak")
            if not bak.exists():
                import shutil
                shutil.copy(src, bak)   # protected backup before autonomous write
            src.write_text(json.dumps(best_circuit, indent=2))
            report["write_back"] = str(src)
            report["backup"] = str(bak)
    print(json.dumps({k: report[k] for k in
                      ("clean", "status", "iterations", "fixed", "auto_issues",
                       "score", "task_count", "wall_time_s")}, indent=2))
    return report


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Agent fix-loop until output is clean")
    ap.add_argument("--circuit", default="out/circuit.json")
    ap.add_argument("--out", default=".run/fix_loop")
    ap.add_argument("--report", default="out/fix_loop_report.json")
    ap.add_argument("--iters", type=int, default=40)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--seeds", type=int, default=1,
                    help="pass^k: run N seeds, report consistency of clean outcome")
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--write-back", action="store_true",
                    help="overwrite input circuit with best state (backup made first)")
    a = ap.parse_args()
    if not Path(a.circuit).exists():
        print(json.dumps({"error": f"{a.circuit} not found — run `make pcb` first"}))
        return 1
    if a.seeds > 1:
        # pass^k consistency: same input, N seeds — is 'clean' stable?
        runs = [run_fix_loop(a.circuit, a.out, a.report, a.iters, s,
                             use_llm=not a.no_llm, write_back=False,
                             write_outputs=False)
                for s in range(a.seed, a.seed + a.seeds)]
        clean_k = sum(1 for r in runs if r["clean"])
        summary = {"seeds": a.seeds, "clean_runs": clean_k,
                   "pass_rate": round(clean_k / a.seeds, 2),
                   "statuses": [r["status"] for r in runs],
                   "final_auto": [r["auto_issues"]["final"] for r in runs]}
        Path(a.report).parent.mkdir(parents=True, exist_ok=True)
        Path(a.report).write_text(json.dumps(summary, indent=2))
        print(json.dumps(summary, indent=2))
        return 0
    run_fix_loop(a.circuit, a.out, a.report, a.iters, a.seed,
                 use_llm=not a.no_llm, write_back=a.write_back)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
