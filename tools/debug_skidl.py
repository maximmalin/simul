#!/usr/bin/env python3
"""Отладочный стенд стадии «схемотехника блоков».

Берёт артефакты частичного прогона (structure.json, block_tz.json,
calc.json) и гоняет ТОЛЬКО проектировщика блоков — без конвейера, без
ТЗ, без сборки. Так правки промпта и проверок видны за секунды, а не за
полчаса полного прогона.

    python3 tools/debug_skidl.py --fixture DIR [--block G1] [--tries 6]

Фикстура — каталог с design/ (structure.json, block_tz.json, calc.json).
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from agents import engine, stages            # noqa: E402
from agents.types import Project             # noqa: E402


def build_project(root: Path) -> Project:
    """Описание проекта-опоры из песочницы фикстуры."""
    return engine.load_profile(str(root))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fixture", required=True,
                    help="каталог с design/ (structure.json, block_tz.json)")
    ap.add_argument("--root", default="",
                    help="корень проекта-опоры (по умолчанию e2e-led)")
    ap.add_argument("--block", default="",
                    help="отлаживать один блок (иначе — все)")
    ap.add_argument("--tries", type=int, default=0,
                    help="попыток проектировщика (0 — как в конвейере)")
    ap.add_argument("--keep", action="store_true",
                    help="не удалять артефакты фикстуры после прогона")
    a = ap.parse_args()

    fixture = Path(a.fixture).resolve()
    if not (fixture / "design" / "block_tz.json").is_file():
        print(f"в фикстуре нет design/block_tz.json: {fixture}")
        return 2
    if a.tries:
        stages.SKIDL_TRIES = a.tries

    root = Path(a.root).resolve() if a.root else \
        ROOT / ".krystal" / "projects" / "e2e-led"
    proj = build_project(root)

    # песочница стенда: копия фикстуры, чтобы прогоны не мешали друг другу
    box = Path("/tmp/opencode/skidl_debug")
    if box.exists():
        shutil.rmtree(box)
    box.mkdir(parents=True)
    shutil.copytree(fixture / "design", box / "design")
    engine.set_workdir(str(box))
    engine.set_active_state(None)

    state = {"order_id": "debug", "text": "отладка схемотехника",
             "intent": "hardware", "planner": "pipeline", "mode": "stages",
             "model": "", "steps": [], "verdict": "", "question": "",
             "note": "", "log": [], "status": "running", "progress": 0,
             "turns": 0, "nudges": 0, "artifacts": [], "report": ""}
    engine.set_active_state(state)

    tasks = json.loads(
        (box / "design" / "block_tz.json").read_text(encoding="utf-8")
    ).get("блоки") or []
    if a.block:
        tasks = [t for t in tasks if str(t.get("код")) == a.block]
        if not tasks:
            print(f"блока {a.block} нет; есть: "
                  f"{[t.get('код') for t in tasks]}")
            return 2

    t0 = time.monotonic()
    res = stages._stage_skidl("отладка схемотехника", proj, state)
    dt = time.monotonic() - t0
    print(f"\n=== результат: {res['status']} за {dt:.0f} с")
    if res.get("error"):
        print(res["error"][:2000])
    for line in state.get("log", []):
        print("  ·", str(line)[:200])
    blocks = box / "design" / "blocks"
    if blocks.is_dir():
        for f in sorted(blocks.glob("*.json")):
            data = json.loads(f.read_text(encoding="utf-8"))
            print(f"\n--- {f.name}: деталей {len(data.get('parts') or [])}, "
                  f"сетей {len(data.get('nets') or [])}")
            for n in data.get("nets") or []:
                print("   ", n.get("name"), n.get("conns"))
    return 0 if res["status"] == "done" else 1


if __name__ == "__main__":
    raise SystemExit(main())
