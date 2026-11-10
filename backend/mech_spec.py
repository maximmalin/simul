"""Extract mechanical / enclosure requirements from the technical task (TZ).

Scans technical_task.md (RU) / technical_task_en.md (EN) for constructor-
relevant requirements: enclosure, dimensions, mounting, ingress protection,
thermal, materials, tolerances. Feeds `cad_delegate.py` so the constructor
part of the design is delegated to the engineer — not silently auto-generated.

Source ideas: text-to-cad engineering workflow (read the spec first, then
model), our GOST pipeline (TZ sections as first-class references). MIT.
"""
from __future__ import annotations
import json, re
from pathlib import Path
from typing import Any

TZ_CANDIDATES = ["technical_task.md", "technical_task_en.md", "out/tz.md"]

# keyword -> (category, ru label). Matched case-insensitively on RU+EN text.
KEYWORDS: list[tuple[str, str, str]] = [
    ("enclosure", "корпус", r"корпус|кожух|шасси|enclosure|housing|case\b"),
    ("dimensions", "габариты", r"габарит|размер[ыа]?\b|dimensions?|size\b|высот[аы]|ширин[аы]|толщин[аы]"),
    ("mounting", "крепление/монтаж", r"креплени|монтаж|установк[аи]|mounting|installation|крепеж|серьг"),
    ("ingress", "защита IP", r"\bIP\d{2}\b|влагозащ|пылезащ|герметич|ingress|weatherproof"),
    ("thermal", "тепло/охлаждение", r"теплоотвод|охлаждени|радиатор|рассеиван|thermal|heat sink|temperature range|температур"),
    ("material", "материал", r"материал|алюмини|сталь|пластик|ABS|PCB material|materials?\b"),
    ("vibration", "вибрация/удар", r"вибрац|ударн|shock|vibration|падени"),
    ("tolerance", "допуски", r"допуск|tolerance|точност[ьи] изготовл"),
    ("gasket", "уплотнение", r"уплотнител|прокладк|gasket|силикон"),
    ("fastener", "винты/герметизация", r"винт|саморез|bolt|screw|герметиз"),
]

SECTION_RE = re.compile(r"^(#{2,4})\s+(.+)$", re.M)


def find_tz(root: str | Path = ".") -> Path | None:
    root = Path(root)
    for c in TZ_CANDIDATES:
        p = root / c
        if p.exists():
            return p
    return None


def scan_mech_requirements(text: str) -> list[dict[str, Any]]:
    """Return matched requirements: {category, label, section, line, excerpt}."""
    lines = text.splitlines()
    # section attribution: line no -> current heading
    sections: list[tuple[int, str]] = []
    for i, ln in enumerate(lines, 1):
        m = SECTION_RE.match(ln)
        if m:
            sections.append((i, m.group(2).strip()))
    def section_of(lineno: int) -> str:
        cur = "(начало)"
        for lno, title in sections:
            if lno <= lineno:
                cur = title
            else:
                break
        return cur

    found: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for cat, label, pat in KEYWORDS:
        rx = re.compile(pat, re.I)
        for i, ln in enumerate(lines, 1):
            if rx.search(ln):
                key = (cat, i)
                if key in seen:
                    continue
                seen.add(key)
                excerpt = ln.strip()
                if len(excerpt) > 240:
                    excerpt = excerpt[:240] + "…"
                found.append({"category": cat, "label": label,
                              "section": section_of(i), "line": i,
                              "excerpt": excerpt})
    return found


def build_mech_spec(root: str | Path = ".") -> dict[str, Any]:
    tz = find_tz(root)
    if tz is None:
        return {"tz_found": False, "has_mechanical": False, "requirements": [],
                "note": "TZ file not found (technical_task.md / out/tz.md)"}
    text = tz.read_text(errors="ignore")
    reqs = scan_mech_requirements(text)
    categories = sorted({r["category"] for r in reqs})
    # enclosure/mounting/IP/material presence => constructor work exists
    heavy = {"enclosure", "mounting", "ingress", "material", "dimensions"}
    return {
        "tz_found": True,
        "tz_file": str(tz),
        "has_mechanical": bool(heavy & set(categories)),
        "categories": categories,
        "requirement_count": len(reqs),
        "requirements": reqs,
        "note": ("mechanical requirements present — delegate constructor part "
                 "to the engineer (run: make cad-brief)" if heavy & set(categories)
                 else "no heavy mechanical requirements in TZ"),
    }


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Extract mechanical requirements from TZ")
    ap.add_argument("--root", default=".")
    ap.add_argument("--out", default="out/mech_spec.json")
    a = ap.parse_args()
    spec = build_mech_spec(a.root)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(spec, indent=2, ensure_ascii=False))
    print(json.dumps({k: spec[k] for k in
                      ("tz_found", "tz_file", "has_mechanical", "categories",
                       "requirement_count", "note")}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
