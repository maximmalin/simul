"""Delegate the constructor (mechanical) part of the design to the engineer.

Reads mechanical requirements from the TZ (mech_spec.py) and produces:
  out/mech_constructor_brief.md    — human brief: TZ excerpts, scope, checklist
  out/mech_constructor_task.json   — machine task for the agent system

Principles (from the integration decision):
  * FreeCAD stays authoritative for enclosure/CAD (`make enclosure`).
  * build123d / text-to-cad are sketch/proposal tools only — never a silent
    replacement of the constructor's work.
  * The brief is generated ONLY when the TZ actually contains mechanical
    requirements; otherwise it reports "nothing to delegate".
"""
from __future__ import annotations
import json, time
from pathlib import Path

try:
    from mech_spec import build_mech_spec
except Exception:
    from backend.mech_spec import build_mech_spec  # type: ignore

EXISTING_ASSETS = [
    ("hardware/enclosure/build_enclosure_fc.py", "FreeCAD-скрипт корпуса (авторитетный источник)"),
    ("hardware/enclosure/krystal-case.step", "STEP корпуса"),
    ("out/enclosure.json", "параметры корпуса (зоны, стенки, уплотнение)"),
    ("Makefile: enclosure", "пересборка корпуса: FCStd/STEP/STL"),
    ("Makefile: drawings", "GOST-чертежи из STEP"),
    ("backend/fea_study.py", "проверка прочности (FEA, аналитический fallback)"),
    ("backend/cad_build.py", "build123d-эскиз (предложение, не истина)"),
]

CHECKLIST = [
    "Габариты и посадочные места сверены с платой (см. out/enclosure.json → board_mm)",
    "Крепёж платы и доступ к разъёмам (USB/антенны) проверены на 3D-модели",
    "Требования IP/уплотнения из ТЗ отражены в конструкции (gasket MIL-DTL-83528)",
    "Теплоотвод/вентиляция соответствует требуемому диапазону температур",
    "Материал и допуски указаны на чертеже (ГОСТ 2.105)",
    "Результат: обновлён FreeCAD-модель + STEP + out/enclosure.json",
]


def build_brief(spec: dict | None = None, root: str | Path = ".") -> dict:
    spec = spec or build_mech_spec(root)
    root = Path(root)
    if not spec.get("has_mechanical"):
        report = {"delegated": False,
                  "note": spec.get("note", "no mechanical requirements in TZ")}
        return report

    # group requirements by section for the brief
    by_section: dict[str, list] = {}
    for r in spec.get("requirements", []):
        by_section.setdefault(r["section"], []).append(r)

    lines = ["# Бриф инженеру-конструктору (механическая часть)", ""]
    lines.append(f"Источник ТЗ: `{spec.get('tz_file', '?')}`  ")
    lines.append(f"Сгенерировано: {time.strftime('%Y-%m-%d %H:%M')}  ")
    lines.append(f"Категорий требований: {len(spec.get('categories', []))} "
                 f"({', '.join(spec.get('categories', []))})")
    lines += ["", "## 1. Требования ТЗ по механике", ""]
    for sec, reqs in by_section.items():
        lines.append(f"### {sec}")
        for r in reqs:
            lines.append(f"- **[{r['label']}]** стр. {r['line']}: {r['excerpt']}")
        lines.append("")
    lines += ["## 2. Зона ответственности", ""]
    lines.append("- **FreeCAD — авторитетный инструмент** для корпуса и "
                 "конструкции (`hardware/enclosure/`). Всё, что здесь "
                 "проектируется, ведётся в FreeCAD-скрипте.")
    lines.append("- **build123d (`backend/cad_build.py`)** — только быстрый "
                 "эскиз/предложение для обсуждения, НЕ замена конструкторской работы.")
    lines.append("- **text-to-cad (FEA/чертежи)** — вспомогательные проверки "
                 "(прочность, плоские развёртки), результаты сверяются с "
                 "моделью FreeCAD.")
    lines += ["", "## 3. Существующие атласы (не дублировать)", ""]
    for path, desc in EXISTING_ASSETS:
        p = root / path.split(":")[0]
        mark = "✓" if p.exists() else "✗"
        lines.append(f"- {mark} `{path}` — {desc}")
    lines += ["", "## 4. Чек-лист приёмки", ""]
    lines += [f"{i+1}. {c}" for i, c in enumerate(CHECKLIST)]
    lines += ["", "## 5. Связанные команды", "",
              "```bash",
              "make enclosure     # пересборка корпуса (FreeCAD, авторитетно)",
              "make drawings      # GOST-чертежи из STEP",
              "make fea           # проверка прочности (аналитический/FEM)",
              "make mech-spec     # перечитать мех-требования из ТЗ",
              "```"]

    brief_md = "\n".join(lines) + "\n"
    task = {
        "task": "mechanical_design",
        "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "status": "awaiting_engineer",
        "tz_file": spec.get("tz_file"),
        "mechanical_categories": spec.get("categories", []),
        "requirements": spec.get("requirements", []),
        "authoritative_tool": "FreeCAD (hardware/enclosure/)",
        "sketch_tools": ["build123d (backend/cad_build.py)"],
        "support_checks": ["fea (backend/fea_study.py)", "drawings (GOST)"],
        "acceptance_checklist": CHECKLIST,
        "brief_md": "out/mech_constructor_brief.md",
        "delegation_rule": ("constructor work goes to the engineer; automation "
                            "may propose/sketch/check but never silently replace "
                            "the FreeCAD source of truth"),
    }
    return {"delegated": True, "brief_md": brief_md, "task": task,
            "requirement_count": spec.get("requirement_count", 0)}


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Generate constructor brief from TZ")
    ap.add_argument("--root", default=".")
    ap.add_argument("--brief", default="out/mech_constructor_brief.md")
    ap.add_argument("--task", default="out/mech_constructor_task.json")
    ap.add_argument("--spec", default="out/mech_spec.json")
    a = ap.parse_args()
    spec = None
    if Path(a.spec).exists():
        try:
            spec = json.loads(Path(a.spec).read_text())
        except Exception:
            spec = None
    rep = build_brief(spec, a.root)
    if rep.get("delegated"):
        Path(a.brief).parent.mkdir(parents=True, exist_ok=True)
        Path(a.brief).write_text(rep.pop("brief_md"))
        Path(a.task).parent.mkdir(parents=True, exist_ok=True)
        Path(a.task).write_text(json.dumps(rep.pop("task"), indent=2, ensure_ascii=False))
    print(json.dumps(rep, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
