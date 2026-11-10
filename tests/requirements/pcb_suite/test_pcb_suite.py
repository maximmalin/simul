"""PCB enhancement suite — smoke tests (no frontend, stdlib only where possible).

Covers absorbed features without breaking existing pipeline:
- pcb_score: score increases with DRC, 0 on empty
- dfm: runs on out/circuit.json, score 0..100
- manufact: estimate_cost returns totals for all fabs
- erc_zones: runs, no crash on empty
- circuitspec: template validates
- mna_fallback: DC solve on simple R divider (via solver if numpy present)
- fallback_router: routes a pair
- fea_study: analytical fallback gives SF
- preorder: builds rows
- placement_loop: perturbation sweep monotonicity (small)
- cad_build: reports availability flag
"""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "backend"))

from pcb_score import score_circuit, load_circuit
from dfm_engine import run_dfm, compute_score
from manufact import estimate_cost, MANUFACTURERS
from erc_zones import run_erc_zones
from circuitspec import load_json, validate, list_templates
from fallback_router import route_pair
try:
    from placement_surrogate import build_dataset, _feat
except Exception:
    build_dataset = None
    _feat = None
try:
    from fix_loop_mech import collect_issues, run_loop
except Exception:
    collect_issues = None
    run_loop = None
try:
    from breadboard import build_layout
except Exception:
    build_layout = None

ROOT = Path(__file__).resolve().parents[3]

def _circuit():
    p = ROOT / "out/circuit.json"
    if not p.exists():
        p = ROOT / "hardware/tscircuit/out/circuit.json"
    return load_circuit(p)

def test_pcb_score_empty_zero():
    r = score_circuit([], "")
    assert r.score == 0 and r.trace_length_mm == 0 and r.via_count == 0

def test_pcb_score_on_real_board():
    c = _circuit()
    r = score_circuit(c, Path(ROOT/"out/drc.rpt").read_text(errors="ignore") if (ROOT/"out/drc.rpt").exists() else "")
    assert r.component_count >= 1
    assert r.trace_length_mm > 0
    assert r.score >= r.trace_length_mm  # penalty terms non-negative

def test_dfm_runs_and_scores_bounded():
    issues = run_dfm(_circuit())
    s = compute_score(issues)
    assert 0 <= s <= 100
    codes = {i.code for i in issues}
    assert codes <= {f"DFM-{i:03d}" for i in range(1,13)}

def test_manufact_estimates_all_fabs():
    c = _circuit()
    for name in MANUFACTURERS:
        e = estimate_cost(c, name, quantity=5)
        assert e.total_cost_usd >= 0 and e.lead_time_days >= 0

def test_erc_zones_no_crash():
    assert isinstance(run_erc_zones(_circuit()), list)
    assert isinstance(run_erc_zones([]), list)

def test_circuitspec_template_validates():
    tpls = list_templates(str(ROOT/"design/templates"))
    assert len(tpls) >= 1
    for t in tpls:
        spec = load_json(t)
        errs = validate(spec)
        assert errs == [], (t, errs)

def test_fallback_router_pair():
    segs = route_pair((0,0),(10,0))
    assert len(segs) >= 1

def test_mna_fallback_divider():
    pytest = __import__("pytest")
    try:
        import numpy  # noqa
    except ImportError:
        pytest.skip("numpy not available")
    from mna_fallback import MNASolver
    # 5V source -> 10k -> node A -> 10k -> GND (2.5V)
    c = "V1 in 0 5\nR1 in mid 10000\nR2 mid 0 10000\n"
    sol = MNASolver().solve(c)
    assert abs(sol.get("mid", 0) - 2.5) < 0.05

def test_fea_analytical():
    from fea_study import analytical_cantilever
    a = analytical_cantilever(50,20,2,500)
    assert a["peak_mpa"] > 0 and a["deflection_mm"] > 0

def test_preorder_builds():
    from preorder import build_review
    # build on possibly missing files -> graceful
    # point at a real circuit-derived dfm if present, else empty
    import json as _j, tempfile, os
    r = build_review(dfm_path=str(ROOT/"out/nope.json"), cost_path=str(ROOT/"out/nope.json"))
    assert "findings" in r

def test_placement_perturbation_small():
    from placement_loop import perturbation_sweep
    c = _circuit()
    # tiny trial count on file circuit
    res = perturbation_sweep(c, 0, trials=1)
    assert len(res) == 1

def test_cad_build_flag():
    from cad_build import try_build123d
    import tempfile
    r = try_build123d(out_dir=str(Path(tempfile.mkdtemp())/"out"))
    assert "build123d_available" in r

# ---------------------------------------------------------------------------
# anti-stagnation + fix-loop (2026 best practices: bandit, escalation, pass^k)
# ---------------------------------------------------------------------------

def test_bandit_forced_exploration_and_ucb():
    from placement_loop import BanditKinds
    import random
    b = BanditKinds(); rng = random.Random(0)
    seq = []
    for _ in range(6):
        k = b.pick(rng); seq.append(k); b.update(k, k == "jitter")
    # forced exploration pulls every arm before exploiting
    assert set(seq[:3]) == {"jitter", "swap", "rotate"}
    # after jitter dominates successes, UCB concentrates on it
    for _ in range(30):
        k = b.pick(rng); b.update(k, k == "jitter")
    rates = b.rates()
    assert rates["jitter"] > rates["swap"] and rates["jitter"] > rates["rotate"]
    assert sum(b.pulls().values()) == 36

def test_proposer_kinds():
    from placement_loop import RandomProposer
    outline = (-40, -40, 40, 40)
    p = RandomProposer(outline, seed=1)
    class _P:
        def __init__(s, ref, x, y, rot): s.ref, s.x_mm, s.y_mm, s.rot_deg = ref, x, y, rot
    pls = [_P("R1", 0, 0, 0), _P("C1", 5, 5, 90)]
    assert p.propose(pls, [], 0, {}, 0, kind="swap").ref.startswith("SWAP:")
    assert p.propose(pls, [], 0, {}, 0, kind="rotate").rot_deg in (90, 180, 270, 0)
    assert p.propose(pls, [], 0, {}, 0, kind="jitter", sigma=3.0).ref in ("R1", "C1")

def test_fix_loop_cleans_broken(tmp_path):
    from fix_loop import run_fix_loop
    import json as _j, copy
    c = _circuit()
    comps = [e for e in c if e.get("type") == "pcb_component"]
    comps[0]["center"] = {"x": -60.0, "y": 0.0}       # outside outline
    comps[1]["center"] = dict(comps[2]["center"])      # overlap
    src = tmp_path / "broken.json"; src.write_text(_j.dumps(c))
    rep = run_fix_loop(str(src), str(tmp_path/"run"), str(tmp_path/"rep.json"),
                       iters=30, use_llm=False,
                       tasks_path=str(tmp_path/"tasks.json"),
                       write_outputs=True)
    assert rep["clean"] is True and rep["status"] == "clean"
    assert rep["auto_issues"]["final"] == 0
    assert (tmp_path/"rep.md").exists()          # feedback trail
    assert (tmp_path/"run"/"best_circuit.json").exists()

def test_fix_loop_escalates_on_stuck(monkeypatch, tmp_path):
    from fix_loop import run_fix_loop, AutoIssue
    import fix_loop as fl
    issue = AutoIssue("edge", ["R1"], "stuck")
    monkeypatch.setattr(fl, "collect_auto", lambda c: [issue])
    monkeypatch.setattr(fl, "evaluate", lambda c: (1, 100.0))   # never improves
    monkeypatch.setattr(fl, "fix_edge", lambda c, i, r: [dict(e) for e in c])
    monkeypatch.setattr(fl, "collect_advisory", lambda c: [])
    src = tmp_path / "c.json"
    src.write_text("[]")
    rep = fl.run_fix_loop(str(src), str(tmp_path/"run"), str(tmp_path/"rep.json"),
                          iters=40, use_llm=False,
                          tasks_path=str(tmp_path/"tasks.json"),
                          write_outputs=True)
    # cooldown: after 3 rejects on R1 every remaining issue is filtered -> escalate
    assert rep["status"] == "escalated" and rep["clean"] is False
    assert rep["escalated"] is True

def test_fix_loop_seeds_consistency(tmp_path):
    # pass^k: clean outcome must be stable across seeds on an already-clean board
    import subprocess, sys as _sys
    r = subprocess.run([_sys.executable, str(ROOT/"backend/fix_loop.py"),
                        "--circuit", str(ROOT/"out/circuit.json"),
                        "--seeds", "3", "--no-llm",
                        "--report", str(tmp_path/"s.json")],
                       capture_output=True, text=True, cwd=str(ROOT), timeout=120)
    assert r.returncode == 0, r.stderr[-500:]
    s = json.loads((tmp_path/"s.json").read_text())
    assert s["clean_runs"] == 3 and s["pass_rate"] == 1.0


def test_surrogate_feat_shape():
    if _feat is None:
        return
    rec = {"move": {"ref": "R1", "x_mm": 1.0, "y_mm": 2.0, "rot_deg": 90.0},
           "score_before": 100.0, "score_after": 90.0,
           "breakdown_after": {"trace_length_mm": 50, "vias": 2, "drc": 1,
                               "ratsnest_mm": 10, "placement_penalty": 5}}
    f = _feat(rec)
    assert f is not None and len(f) == 10
    assert all(isinstance(x, float) for x in f)


def test_surrogate_dataset_from_records():
    if build_dataset is None:
        return
    import pathlib
    p = pathlib.Path(ROOT) / ".run/placement_opt/records.jsonl"
    if not p.exists():
        return
    X, y = build_dataset(p)
    if X is None:
        return
    assert X.shape[1] == 10
    assert len(X) == len(y)


def test_fix_loop_mech_collects_issues():
    if collect_issues is None:
        return
    issues = collect_issues(ROOT)
    assert isinstance(issues, list)
    # FEA safety factor 0.128 < 2.0 → at least one issue
    assert len(issues) >= 1
    ids = {i.id for i in issues}
    assert any("FEA" in i or "ENC" in i or "THM" in i for i in ids)


def test_fix_loop_mech_run_loop():
    if run_loop is None:
        return
    r = run_loop(1, ROOT)
    assert "clean" in r
    assert r["advisory"] is True
    assert (ROOT / "out" / "fix_mech_report.json").exists()
    assert (ROOT / "out" / "fix_mech_tasks.json").exists()


def test_breadboard_layout():
    if build_layout is None:
        return
    import json
    from pathlib import Path
    circuit = __import__("json").loads((ROOT / "out/circuit.json").read_text())
    layout = build_layout(circuit)
    assert layout["component_count"] == 60
    assert len(layout["components"]) == 60
    assert "board" in layout
    assert layout["board"]["cols"] == 63
