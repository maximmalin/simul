#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Проверка тел деталей в board.step — по граням, покомпонентно.

Зачем. board.glb и board.step собираются разным кодом, и посадка тел в
STEP раньше обманывала: сборщик FreeCAD читает тело как Compound из
Shell/Face (родные тела tscircuit), а не Solid, поэтому старый замер
«только solids» тел не видел вовсе, а у крупных компаундов встречается
bbox-мусор (±1e80).

Как надёжно. Каждый компонент из circuit.json проверяется по СВОЕМУ окну:
берутся грани board.step, чей габаритный центр попадает в окно детали
(окно = габарит корпуса + запас), с условиями:
  * грань лежит НАД поверхностью платы (zmax > surface + 0.1): так из
    выбора сами собой исчезают грани самой платы (их верх ровно на
    поверхности) — не нужны эвристики «это лицо платы»;
  * грань не тянется через всю плату (страховка от крупных накладок);
  * габарит грани не мусорный (|коорд| ≤ 1e6).
Вердикт по детали:
  * граней нет вовсе — «НЕТ ТЕЛА» (ровно провал C1: текст в STEP есть,
    тело при чтении отсутствует);
  * низ тела выше поверхности + 0.3 мм — «ПАРИТ» (двойной учёт половины
    толщины у tscircuit: тело, не пересекающее z=0, садится на всю
    толщину платы);
  * иначе — ОК (включая сквозные выводы гребёнок: они пересекают плату).

Чтение делается несколько раз с объединением граней (разбиение
Compound у FreeCAD нестабильно между процессами).

Запуск (нужен freecadcmd; без него шаг честно пропускается):

    python3 hardware/tscircuit/check_board_step.py --step board.step \
        [--out DIR]

где DIR — каталог экспортов с circuit.json; по умолчанию — каталог рядом
с board.step. Код возврата 0, если у каждой детали есть тело над платой
и ни одно не парит.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path

#: низ тела может быть чуть выше поверхности (округления STEP);
#: выше этого — уже парение (баг «двойной половины толщины» даёт +0.8).
FLOAT_TOL = 0.30
#: грань считается «над платой», если её zmax выше поверхности на столько
#: (грани платы лежат ровно на поверхности — их верх = +surface).
ABOVE_MIN = 0.10
#: сколько раз читать board.step (разбиение Compound у FreeCAD нестабильно
#: между процессами — берём объединение).
TRY = 3

HELPER = """
import json, os
import Part
shape = Part.read(os.environ.get("KR_STEP_FILE") or "")
faces, solids = [], []
garbage = [0]

def walk(s):
    t = s.ShapeType
    if t == "Face":
        bb = s.BoundBox
        vals = [bb.XMin, bb.XMax, bb.YMin, bb.YMax, bb.ZMin, bb.ZMax]
        if any((v != v) or abs(v) > 1e6 for v in vals):
            garbage[0] += 1
        else:
            faces.append([round(v, 4) for v in vals])
        return
    if t == "Solid":
        bb = s.BoundBox
        vals = [bb.XMin, bb.XMax, bb.YMin, bb.YMax, bb.ZMin, bb.ZMax]
        if not any((v != v) or abs(v) > 1e6 for v in vals):
            solids.append([round(v, 4) for v in vals])
    if t in ("Solid", "Shell", "Compound", "CompSolid"):
        for c in s.childShapes():
            walk(c)

walk(shape)
for f in faces:
    print("KR_FACE " + json.dumps(f))
for s in solids:
    print("KR_SOLID " + json.dumps(s))
print("KR_GARBAGE " + str(garbage[0]))
"""


def scan_once(step: Path):
    """Одно чтение граней и solids через freecadcmd.

    Возвращает dict(faces, solids, garbage) или None, если freecadcmd
    недоступен (без него проверка честно пропускается). RuntimeError —
    FreeCAD есть, но прочитать файл не удалось.
    """
    exe = os.environ.get("FREECADCMD", "freecadcmd")
    base = os.environ.get("KR_TMP") or "/tmp/opencode"
    tmp = Path(tempfile.mkdtemp(prefix="kr_step_",
                                dir=base if Path(base).is_dir() else None))
    helper = tmp / "scan_step.py"
    helper.write_text(HELPER)
    try:
        r = subprocess.run([exe, str(helper)], capture_output=True, text=True,
                           env={**os.environ, "KR_STEP_FILE": str(step)})
    except FileNotFoundError:
        return None
    faces, solids, garbage = [], [], 0
    for line in r.stdout.splitlines():
        if line.startswith("KR_FACE "):
            faces.append(tuple(json.loads(line[len("KR_FACE "):])))
        elif line.startswith("KR_SOLID "):
            solids.append(tuple(json.loads(line[len("KR_SOLID "):])))
        elif line.startswith("KR_GARBAGE "):
            garbage = int(line.split()[-1])
    if not faces and not solids:
        tail = (r.stderr or r.stdout).strip().splitlines()[-3:]
        raise RuntimeError(" | ".join(tail) or f"код {r.returncode}")
    return {"faces": faces, "solids": solids, "garbage": garbage}


def scan(step: Path):
    """Объединение TRY чтений: грани/solids по уникальным габаритам."""
    uniq_f: dict[tuple, tuple] = {}
    uniq_s: dict[tuple, tuple] = {}
    garbage = 0
    for _ in range(TRY):
        one = scan_once(step)
        if one is None:
            return None
        for f in one["faces"]:
            uniq_f.setdefault(f, f)
        for s in one["solids"]:
            uniq_s.setdefault(s, s)
        garbage = max(garbage, one["garbage"])
    return {"faces": list(uniq_f.values()), "solids": list(uniq_s.values()),
            "garbage": garbage}


def main() -> int:
    ap = argparse.ArgumentParser(description="посадка тел в board.step")
    ap.add_argument("--step", required=True, help="путь к board.step")
    ap.add_argument("--out", metavar="КАТАЛОГ",
                    help="каталог экспортов (circuit.json: детали и толщина "
                         "платы); по умолчанию — каталог рядом с board.step")
    args = ap.parse_args()

    step = Path(args.step).resolve()
    if not step.is_file():
        print(f"НЕТ {step} — сначала соберите board.step")
        return 1
    out_dir = Path(args.out).resolve() if args.out else step.parent

    try:
        got = scan(step)
    except RuntimeError as e:
        print(f"[3D-STEP] не удалось прочитать {step.name}: {e}")
        return 1
    if got is None:
        print("[3D-STEP] freecadcmd недоступен — проверка тел board.step "
              "пропущена (FreeCAD готовится один раз)")
        return 0

    faces, solids = got["faces"], got["solids"]
    if not solids and not faces:
        print(f"[3D-STEP] {step.name}: ни граней, ни тел — файл пуст?")
        return 1

    thickness, board_center, comps = 1.6, (0.0, 0.0), {}
    cj = out_dir / "circuit.json"
    if cj.is_file():
        circuit = json.loads(cj.read_text())
        board = next((e for e in circuit if e.get("type") == "pcb_board"), None)
        if board:
            thickness = board.get("thickness") or thickness
            if board.get("center"):
                board_center = (board["center"].get("x", 0.0),
                                board["center"].get("y", 0.0))
        refs = {e["source_component_id"]: e.get("name", "")
                for e in circuit if e.get("type") == "source_component"}
        for c in circuit:
            if c.get("type") == "pcb_component":
                ref = c.get("ref") or refs.get(c.get("source_component_id"), "?")
                comps[ref] = (c["center"]["x"], c["center"]["y"],
                              c.get("width") or 0, c.get("height") or 0)
    surface = thickness / 2

    # Рамка платы — самый крупный solid по XY; он же задаёт сдвиг между
    # координатами board.step и circuit.json.
    if solids:
        board_solid = max(solids, key=lambda s: (s[1] - s[0]) * (s[3] - s[2]))
        dx = (board_solid[0] + board_solid[1]) / 2 - board_center[0]
        dy = (board_solid[2] + board_solid[3]) / 2 - board_center[1]
        board_xspan = board_solid[1] - board_solid[0]
        board_yspan = board_solid[3] - board_solid[2]
    else:  # плата тоже могла прийти набором граней; рамка — по всем граням
        xs0 = min(f[0] for f in faces); xs1 = max(f[1] for f in faces)
        ys0 = min(f[2] for f in faces); ys1 = max(f[3] for f in faces)
        dx = (xs0 + xs1) / 2 - board_center[0]
        dy = (ys0 + ys1) / 2 - board_center[1]
        board_xspan, board_yspan = xs1 - xs0, ys1 - ys0

    print("=" * 78)
    print("ТЕЛА ДЕТАЛЕЙ В board.step: у каждой детали тело над платой, "
          "ни одно не парит")
    print("=" * 78)
    print(f"  {step.name}: граней {len(faces)} "
          f"(мусорных {got['garbage']}), solids {len(solids)}, "
          f"толщина {thickness:.2f} мм, поверхность z = {surface:.2f}")
    print(f"  сдвиг STEP↔circuit: dx {dx:+.2f}, dy {dy:+.2f}")
    print()

    missing, floating, ok = [], [], 0
    rows = []
    for ref, (cx, cy, cw, ch) in sorted(comps.items()):
        half = max(cw or 0, ch or 0) / 2 + 1.0
        wx, wy = cx + dx, cy + dy
        sel = []
        for f in faces:
            if f[5] <= surface + ABOVE_MIN:       # не выше поверхности
                continue
            if (f[1] - f[0]) > 0.7 * board_xspan and \
                    (f[3] - f[2]) > 0.7 * board_yspan:
                continue                          # накладка через всю плату
            fx, fy = (f[0] + f[1]) / 2, (f[2] + f[3]) / 2
            if abs(fx - wx) <= half and abs(fy - wy) <= half:
                sel.append(f)
        if not sel:
            missing.append(ref)
            rows.append((ref, 0, None, None, "НЕТ ТЕЛА"))
            continue
        zmin = min(f[4] for f in sel)
        zmax = max(f[5] for f in sel)
        if zmin > surface + FLOAT_TOL:
            floating.append(ref)
            rows.append((ref, len(sel), zmin, zmax,
                         f"ПАРИТ (+{zmin - surface:.2f})"))
        else:
            ok += 1
            rows.append((ref, len(sel), zmin, zmax, "OK"))

    for ref, n, zmin, zmax, verdict in rows:
        zs = (f"z[{zmin:.2f},{zmax:.2f}]" if zmin is not None else "—")
        print(f"   {ref:<6} граней {n:<4} {zs:<18} {verdict}")
    print()
    print(f"  итог: OK {ok}, без тела {len(missing)}, парят {len(floating)}")
    if missing:
        print(f"  НЕТ ТЕЛА: {', '.join(missing)} — сборщик board.step "
              "молча потерял геометрию детали")
    if floating:
        print(f"  ПАРЯТ: {', '.join(floating)} — тело не пересекает z=0: "
              "двойной учёт половины толщины платы у tscircuit; лечится "
              "моделью от part_fetch (низ -0.01) или родным корпусом")
    if missing or floating:
        print("\nОШИБКА: не у всех деталей есть посаженное тело в board.step")
        return 1
    print("\nOK: у всех деталей тела над платой, ни одно не парит")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
