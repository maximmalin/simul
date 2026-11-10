"""Breadboard/fretboard прототипирование (ОТДЕЛЬНО от PCB).

Генерирует раскладку компонентов на макетной плате (breadboard) из circuit.json.
Не меняет PCB — это отдельный артефакт для отладки и «test mode».
Идея из форумов KiCad: breadboard builder plugin + test points для прототипов.

Выход: out/breadboard_layout.json — позиции компонентов/ножек/проводов на сетке
макетной платы (для 3D-визуализации three.js).

CLI: python3 backend/breadboard.py [--out out/breadboard_layout.json]
"""
from __future__ import annotations
import json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from utils import load_circuit_json as load_circuit
except Exception:
    from pcb_score import load_circuit

ROOT = Path(__file__).resolve().parents[1]

# Размеры макетной платы (универсально: 830 точек, 63 колонки × 10 рядов + rails)
COLS = 63
ROWS = 10  # a..j (5+5 с разрывом по центру)
RAIL_TOP = -2
RAIL_BOTTOM = ROWS + 1


def _components(circuit) -> list[dict]:
    seen = set()
    out = []
    for e in circuit:
        if e.get("type") not in ("pcb_component", "source_component"):
            continue
        ref = e.get("source_component_id") or e.get("refdes")
        if ref in seen:
            continue
        seen.add(ref)
        out.append(e)
    return out


def _net_of(comp, pin_idx) -> str | None:
    # circuit.json не содержит явной привязки pin->net в pcb_component;
    # используем имя компонента как ключ группы (упрощённо).
    return comp.get("source_component_id")


def build_layout(circuit) -> dict:
    comps = _components(circuit)
    # размещаем компоненты по строкам/колонкам, избегая пересечений
    placed = []
    col = 1
    row = 0
    grid_w = 5  # ширина компонента в колонках
    for c in comps:
        ref = c.get("source_component_id") or c.get("refdes") or "?"
        pins = int(c.get("pin_count", 2) or 2)
        # IC: straddle center gap (rows 4/5); discrete: single row
        is_ic = pins >= 4
        if is_ic:
            half = (pins // 2)
            r_top = 4 - (half // 2)
            r_bot = 5 + (half // 2)
            positions = []
            for i in range(half):
                positions.append({"pin": i + 1, "row": r_top, "col": col + i})
            for i in range(half):
                positions.append({"pin": half + i + 1, "row": r_bot, "col": col + (half - 1 - i)})
        else:
            positions = [{"pin": i + 1, "row": (row + i) % ROWS, "col": col + i} for i in range(pins)]
        placed.append({
            "ref": ref,
            "pins": pins,
            "is_ic": is_ic,
            "col": col,
            "positions": positions,
        })
        col += max(grid_w, pins + 2)
        if col > COLS - 5:
            col = 1
            row = (row + 3) % (ROWS - 2)
    # провода: для каждой пары компонентов на одной сети — прямая линия
    # (упрощённо: соединяем первые ножки соседних компонентов)
    wires = []
    for i in range(len(placed) - 1):
        a = placed[i]["positions"][0]
        b = placed[i + 1]["positions"][0]
        wires.append({"from": [a["row"], a["col"]], "to": [b["row"], b["col"]]})

    return {
        "board": {"cols": COLS, "rows": ROWS, "rail_top": RAIL_TOP, "rail_bottom": RAIL_BOTTOM},
        "components": placed,
        "wires": wires,
        "component_count": len(placed),
    }


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Generate breadboard prototype layout")
    ap.add_argument("--circuit", default="out/circuit.json")
    ap.add_argument("--out", default="out/breadboard_layout.json")
    a = ap.parse_args()
    circuit = load_circuit(Path(a.circuit))
    layout = build_layout(circuit)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(layout, ensure_ascii=False, indent=2))
    print(json.dumps({"components": layout["component_count"], "wires": len(layout["wires"])},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())