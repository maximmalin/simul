"""Агентская петля исправлений для КОНСТРУКТОРСКИХ (механических) ошибок.

Аналог fix_loop.py, но для механики: FEA (коэффициент запаса), посадка
платы в корпус, термические запасы, IP/уплотнение, масса, допуски.
Петля ТОЛЬКО advisory: никогда не правит FreeCAD молча — пишет структурированные
задачи в out/fix_mech_tasks.json и план через LLM в out/fix_mech_plan.md.
Конструктор (FreeCAD) остаётся авторитетным источником.

CLI: python3 backend/fix_loop_mech.py --iters 5
"""
from __future__ import annotations
import json, math, os, time
from dataclasses import dataclass, asdict
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    import llm
except Exception:
    llm = None

ROOT = Path(__file__).resolve().parents[1]


@dataclass
class MechIssue:
    id: str
    category: str          # fea | enclosure | thermal | ip | mass | tolerance | drawing
    severity: str          # high | medium | low
    message: str
    suggested_fix: str
    evidence: dict
    def to_dict(self): return asdict(self)


def _read_json(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def collect_issues(root: Path = ROOT) -> list[MechIssue]:
    issues: list[MechIssue] = []
    out = root / "out"

    # --- FEA: коэффициент запаса ---
    fea = _read_json(out / "fea" / "fea_result.json")
    if fea:
        sf = fea.get("safety_factor")
        margin = fea.get("margin_required", 2.0)
        if sf is not None and sf < margin:
            issues.append(MechIssue(
                id="MECH-FEA-001", category="fea", severity="high",
                message=f"Коэффициент запаса {sf:.2f} < требуемого {margin:.1f} (пик {fea.get('peak_mpa')} МПа)",
                suggested_fix="Увеличить толщину стенки/добавить рёбра жёсткости, либо сменить материал на более прочный (Al 7075 / сталь).",
                evidence={"sf": sf, "margin": margin, "peak_mpa": fea.get("peak_mpa")},
            ))
        if fea.get("pass") is False:
            issues.append(MechIssue(
                id="MECH-FEA-002", category="fea", severity="high",
                message="FEA first-pass не пройден (analytical cantilever)",
                suggested_fix="Проверить нагрузку/граничные условия; при наличии ccx запустить полный FEM.",
                evidence={"solver": fea.get("solver")},
            ))

    # --- Посадка платы в корпус ---
    enc = _read_json(out / "enclosure.json")
    if enc:
        board = enc.get("board_mm", {})
        case = enc.get("case_mm", {})
        bw, bh = board.get("w", 0), board.get("h", 0)
        cw, ch = case.get("w", 0), case.get("h", 0)
        wall = case.get("wall", 0)
        wall_min = float(os.environ.get("MECH_WALL_MIN_MM", "1.0"))
        gap_min = float(os.environ.get("MECH_GAP_MIN_MM", "1.0"))
        if bw > 0 and cw > 0 and (cw - bw) < 2 * wall + gap_min:
            issues.append(MechIssue(
                id="MECH-ENC-001", category="enclosure", severity="high",
                message=f"Плата {bw}×{bh} мм едва входит в корпус {cw}×{ch} мм (зазор < {2*wall+gap_min} мм)",
                suggested_fix="Увеличить габариты корпуса или уменьшить плату; проверить доступ к разъёмам.",
                evidence={"board": board, "case": case},
            ))
        if wall < wall_min:
            issues.append(MechIssue(
                id="MECH-ENC-002", category="enclosure", severity="medium",
                message=f"Толщина стенки {wall} мм < {wall_min} мм",
                suggested_fix=f"Увеличить толщину стенки до ≥{wall_min} мм (жёсткость + технологичность).",
                evidence={"wall": wall, "wall_min": wall_min},
            ))

    # --- Термические запасы ---
    therm = _read_json(out / "thermal_fast.json")
    if therm:
        for dev in therm.get("fast", {}).get("devices", []):
            margin = dev.get("margin_c")
            therm_min = float(os.environ.get("MECH_THERMAL_MARGIN_C", "10.0"))
            if margin is not None and margin < therm_min:
                issues.append(MechIssue(
                    id="MECH-THM-001", category="thermal", severity="high",
                    message=f"{dev.get('ref')}: термозапас {margin:.1f} °C < {therm_min} °C",
                    suggested_fix="Добавить теплоотвод/увеличить площадь рассеяния или сменить корпус на металл.",
                    evidence={"device": dev.get("ref"), "margin_c": margin},
                ))

    # --- IP / уплотнение ---
    if enc and not enc.get("gasket"):
        issues.append(MechIssue(
            id="MECH-IP-001", category="ip", severity="medium",
            message="В out/enclosure.json нет данных об уплотнении (gasket)",
            suggested_fix="Добавить уплотнение (MIL-DTL-83528) и проверить IP-требование из ТЗ.",
            evidence={},
        ))

    # --- Масса (грубая оценка) ---
    if enc:
        # масса корпуса Al ~ 2.7e-6 кг/мм3 * объём стенок (упрощённо)
        case = enc.get("case_mm", {})
        w, h, ht = case.get("w", 0), case.get("h", 0), case.get("h_total", 0)
        wall = case.get("wall", 0.5)
        vol = (w * h * ht - (w - 2*wall)*(h - 2*wall)*(ht - 2*wall)) * 2.7e-6
        mass_limit = float(os.environ.get("MECH_MASS_LIMIT_KG", "0.5"))
        if vol > mass_limit:
            issues.append(MechIssue(
                id="MECH-MASS-001", category="mass", severity="low",
                message=f"Оценочная масса корпуса {vol:.3f} кг > {mass_limit} кг",
                suggested_fix="Оптимизировать толщину стенок/добавить облегчающие вырезы.",
                evidence={"mass_kg": round(vol, 3)},
            ))

    # --- Допуски: проверка чертежа ---
    dwg = root / "sim" / "cad" / "figures" / "krystal-case_GOST.svg"
    if not dwg.exists():
        issues.append(MechIssue(
            id="MECH-DWG-001", category="drawing", severity="medium",
            message="Чертёж корпуса не сгенерирован",
            suggested_fix="Запустить: make drawings",
            evidence={},
        ))

    return issues


def build_llm_plan(issues: list[MechIssue]) -> str:
    if not issues:
        return "# План исправлений конструктора\n\nЗамечаний нет — конструкция чиста.\n"
    if llm is None or not getattr(llm, "ENABLED", False):
        lines = ["# План исправлений конструктора (эвристика)", ""]
        for i in issues:
            lines.append(f"- **{i.id}** [{i.severity}] {i.message}\n  → {i.suggested_fix}")
        return "\n".join(lines) + "\n"
    try:
        msgs = [{"role": "user", "content":
            "Ты — инженер-конструктор. Ниже список замечаний по механической части "
            "приёмника Кристалл. Предложи конкретный план исправлений (по пунктам, "
            "с указанием что менять в FreeCAD-модели/параметрах). Не меняй файлы сам.\n\n"
            + "\n".join(f"- {i.id} [{i.severity}] {i.message} (подсказка: {i.suggested_fix})" for i in issues)}]
        turn = llm.chat(msgs, fast=True, max_tokens=1500)
        text = getattr(turn, "text", None) if turn is not None else None
        if text:
            return "# План исправлений конструктора (LLM)\n\n" + text + "\n"
    except Exception as e:
        pass
    lines = ["# План исправлений конструктора (эвристика, LLM недоступен)", ""]
    for i in issues:
        lines.append(f"- **{i.id}** [{i.severity}] {i.message}\n  → {i.suggested_fix}")
    return "\n".join(lines) + "\n"


def run_loop(iters: int = 5, root: Path = ROOT) -> dict:
    root = Path(root)
    out = root / "out"
    out.mkdir(parents=True, exist_ok=True)
    run_dir = root / ".run" / "fix_loop_mech"
    run_dir.mkdir(parents=True, exist_ok=True)
    history_path = run_dir / "history.jsonl"
    report_path = out / "fix_mech_report.json"
    tasks_path = out / "fix_mech_tasks.json"
    plan_path = out / "fix_mech_plan.md"

    all_issues: list[MechIssue] = []
    for it in range(iters):
        issues = collect_issues(root)
        all_issues = issues
        # advisory tasks
        tasks = [i.to_dict() for i in issues]
        tasks_path.write_text(json.dumps(tasks, ensure_ascii=False, indent=2), encoding="utf-8")
        # LLM plan
        plan = build_llm_plan(issues)
        plan_path.write_text(plan, encoding="utf-8")
        # history
        with history_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"iter": it, "n_issues": len(issues),
                                "ids": [i.id for i in issues]}, ensure_ascii=False) + "\n")
        # mechanical fixes cannot be auto-applied (constructor authoritative)
        # → advisory only; stop after first pass (no auto lane)
        break

    report = {
        "clean": len(all_issues) == 0,
        "iterations": 1,
        "issue_count": len(all_issues),
        "issues": [i.to_dict() for i in all_issues],
        "advisory": True,
        "note": "Конструктор (FreeCAD) остаётся авторитетным; задачи advisory — применяет инженер.",
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Agent loop for mechanical/constructor errors (advisory)")
    ap.add_argument("--iters", type=int, default=1)
    ap.add_argument("--root", default=str(ROOT))
    a = ap.parse_args()
    r = run_loop(a.iters, Path(a.root))
    print(json.dumps(r, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
