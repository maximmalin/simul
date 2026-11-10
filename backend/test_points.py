"""Test-point recommender for PCB prototypes.

Универсально: анализирует circuit.json, находит критические сети (питание,
земля, тактирование, сброс, отладка, RF) и предлагает тестовые точки
(TestPoint_Pad) для зондирования. Идея из форумов KiCad: test mode = TP на
каждой критической ноде, чтобы прототип и финальная плата вели себя
одинаково (отладка паразитов/земляных петель).

CLI: python3 backend/test_points.py [--out out/test_points.json]
"""
from __future__ import annotations
import json, re, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from utils import load_circuit_json as load_circuit
except Exception:
    from pcb_score import load_circuit

CRITICAL_NAME_PATTERNS = [
    (re.compile(r"^(vdd|vcc|vbat|vin|p3v3|p5v|p1v8|avdd|dvdd|vref)", re.I), "power"),
    (re.compile(r"gnd|ground|vss|agnd|dgnd", re.I), "ground"),
    (re.compile(r"clk|clock|x?tal|osc", re.I), "clock"),
    (re.compile(r"rst|reset|nreset", re.I), "reset"),
    (re.compile(r"dbg|debug|swdio|swclk|jtag|uart|tx|rx|spi|scl|sda|i2c", re.I), "debug"),
    (re.compile(r"ant|rf|lna|mix", re.I), "rf"),
]

PRIORITY = {"power": 1, "ground": 1, "clock": 2, "reset": 2, "debug": 3, "rf": 2}


def _net_kind(net: dict) -> str | None:
    if net.get("is_power"):
        return "power"
    if net.get("is_ground"):
        return "ground"
    name = str(net.get("name", ""))
    for pat, kind in CRITICAL_NAME_PATTERNS:
        if pat.search(name):
            return kind
    return None


def recommend(circuit) -> list[dict]:
    nets = [e for e in circuit if e.get("type") == "source_net"]
    out = []
    seen = set()
    for net in nets:
        kind = _net_kind(net)
        if kind is None:
            continue
        name = net.get("name")
        if name in seen:
            continue
        seen.add(name)
        out.append({
            "net": name,
            "kind": kind,
            "priority": PRIORITY.get(kind, 4),
            "footprint": "TestPoint:TestPoint_Pad_1.0x1.0mm",
            "layer": "F.Cu",
            "note": f"Тестовая точка для зондирования сети {name} ({kind})",
        })
    out.sort(key=lambda t: (t["priority"], t["net"]))
    return out


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Recommend test points for PCB prototypes")
    ap.add_argument("--circuit", default="out/circuit.json")
    ap.add_argument("--out", default="out/test_points.json")
    a = ap.parse_args()
    circuit = load_circuit(Path(a.circuit))
    recs = recommend(circuit)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps({"count": len(recs), "test_points": recs},
                                     ensure_ascii=False, indent=2))
    print(json.dumps({"count": len(recs)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())