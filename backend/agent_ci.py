"""Агентская CI/CD-оркестрация всего процесса.

Запускает полный конвейер: netlist → spice → pcb → drawings → тесты,
и при сбоях автоматически вызывает fix_loop (PCB) и fix_loop_mech
(конструктор), повторяя до чистого результата или max iters.

CLI: python3 backend/agent_ci.py [--iters 3] [--skip-frontend]
"""
from __future__ import annotations
import json, subprocess, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "out"

STEPS = [
    ("netlist", "python3 backend/netlist.py" if (ROOT / "backend/netlist.py").exists() else "make netlist"),
    ("spice", "make spice"),
    ("pcb", "make pcb"),
    ("publish", "make publish"),
    ("drawings", "make drawings"),
    ("tests", "python3 -m pytest tests/requirements/ -q"),
]


def run_step(cmd: str) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, shell=True, cwd=str(ROOT), capture_output=True, text=True, timeout=600)
        out = (p.stdout or "") + "\n" + (p.stderr or "")
        return p.returncode, out[-2000:]
    except subprocess.TimeoutExpired:
        return 124, "TIMEOUT"
    except Exception as e:
        return 1, str(e)


def run_fix_loops() -> dict:
    res = {}
    # PCB fix loop
    try:
        p = subprocess.run("python3 backend/fix_loop.py --iters 3", shell=True, cwd=str(ROOT),
                           capture_output=True, text=True, timeout=600)
        res["fix_loop"] = p.returncode
    except Exception as e:
        res["fix_loop"] = str(e)
    # mech fix loop
    try:
        p = subprocess.run("python3 backend/fix_loop_mech.py", shell=True, cwd=str(ROOT),
                           capture_output=True, text=True, timeout=300)
        res["fix_mech"] = p.returncode
    except Exception as e:
        res["fix_mech"] = str(e)
    return res


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Agent CI/CD orchestrator")
    ap.add_argument("--iters", type=int, default=2)
    ap.add_argument("--skip-frontend", action="store_true")
    a = ap.parse_args()

    report = {"iterations": [], "clean": False}
    for it in range(a.iters):
        iter_report = {"iter": it, "steps": {}}
        failed = False
        for name, cmd in STEPS:
            if a.skip_frontend and name in ("viewer", "page"):
                continue
            rc, tail = run_step(cmd)
            iter_report["steps"][name] = {"rc": rc, "tail": tail[-500:]}
            print(f"[iter {it}] {name}: rc={rc}")
            if rc != 0:
                failed = True
        if not failed:
            report["clean"] = True
            report["iterations"].append(iter_report)
            break
        # run fix loops
        print(f"[iter {it}] running fix loops...")
        fr = run_fix_loops()
        iter_report["fix_loops"] = fr
        report["iterations"].append(iter_report)

    (OUT).mkdir(parents=True, exist_ok=True)
    (OUT / "agent_ci_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps({"clean": report["clean"], "iterations": len(report["iterations"])}, ensure_ascii=False))
    return 0 if report["clean"] else 1


if __name__ == "__main__":
    raise SystemExit(main())