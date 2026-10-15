#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INIT = ROOT / "scripts" / "project_init.py"
GEN_SCRIPT = ROOT / "scripts" / "generic_generate.py"


def slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9_-]+", "-", s.lower()).strip("-") or "project"


def mkdir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def project_root(code: str) -> Path:
    return ROOT / ".krystal" / "projects" / code


def reset_project_root(root: Path) -> None:
    # «снеси sandbox» правило: обнулить старую локальную сборку проекта
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True, exist_ok=True)


def gen_makefile(title: str, objective: str) -> str:
    return f'''# Generic project generator for: {title}
PY ?= python3
TITLE ?= {title}
OBJECTIVE ?= {objective}

.PHONY: all tz enclose pcb drawings page test clean
all: tz enclose drawings pcb page

tz:
\t$(PY) scripts/generate.py --step tz --title "$(TITLE)" --objective "$(OBJECTIVE)"
enclose:
\t$(PY) scripts/generate.py --step enclose --title "$(TITLE)" --objective "$(OBJECTIVE)"
drawings:
\t$(PY) scripts/generate.py --step drawings --title "$(TITLE)" --objective "$(OBJECTIVE)"
pcb:
\t$(PY) scripts/generate.py --step pcb --title "$(TITLE)" --objective "$(OBJECTIVE)"
page:
\t$(PY) scripts/generate.py --step page  --title "$(TITLE)" --objective "$(OBJECTIVE)"
test:
\t$(PY) scripts/generate.py --step test  --title "$(TITLE)" --objective "$(OBJECTIVE)"
clean:
\trm -rf out work
'''


def gen_profile(title: str, objective: str) -> str:
    return f"""# Universal project description (generated)
name: {title}
description: {objective}
runner: make
build_file: Makefile
runner_opts: {{}}
targets:
  - name: all
    title: Build whole project
    top_level: true
    depends_on: [tz]
  - name: tz
    title: Generate technical task
    top_level: true
  - name: enclose
    title: Generate enclosure 3D files
    top_level: false
    depends_on: [tz]
  - name: drawings
    title: Generate engineering drawings
    top_level: false
    depends_on: [tz]
  - name: pcb
    title: Generate conceptual PCB layout
    top_level: false
    depends_on: [tz]
  - name: page
    title: Generate report page
    depends_on: [tz, enclose, drawings, pcb]
artifacts:
  - out/tz.md
  - out/board.svg
  - out/drawings.svg
  - out/model.gltf
  - out/model.stl
  - out/page.html
vocabulary: {{}}
system: "Generate deliverables for the project from its own Makefile and project description."
verify: "make test"
agent_owner:
  all: agent.build
  tz: agent.build
  enclose: agent.build
  drawings: agent.build
  pcb: agent.build
  test: agent.verify
"""


def init(code: str, title: str, objective: str, force: bool = False) -> Path:
    root = project_root(code)
    if root.exists() and force:
        shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True, exist_ok=True)
    mkdir(root / "out")
    mkdir(root / "scripts")
    # gen script copy
    gen_source = GEN_SCRIPT
    if gen_source.is_file():
        shutil.copy2(gen_source, root / "scripts" / "generate.py")
    else:  # fallback: no source file, generate from bundled text
        write(root / "scripts" / "generate.py", BUNDLED_GENERATOR)
    write(root / "Makefile", gen_makefile(title, objective))
    write(root / "agents.project.yaml", gen_profile(title, objective))
    write(root / "README.md", f"# {title}\n\n{objective}\n")
    return root


def main() -> int:
    ap = argparse.ArgumentParser(description="Generic project bootstrap")
    ap.add_argument("--init", action="store_true")
    ap.add_argument("--code", required=True)
    ap.add_argument("--title", required=True)
    ap.add_argument("--objective", default="")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()
    code = re.sub(r"[^A-Za-z0-9_-]", "-", args.code).strip("-") or "project"

    if args.list:
        base = ROOT / ".krystal" / "projects"
        if not base.is_dir():
            print("[]"); return 0
        projects = [p.name for p in base.iterdir() if p.is_dir() and (p / "agents.project.yaml").is_file()]
        print(json.dumps(projects, ensure_ascii=False))
        return 0

    if args.init:
        reset_project_root(project_root(code))
        r = init(code, args.title, args.objective, force=True)
        print(str(r))
    else:
        r = init(code, args.title, args.objective, force=False)
        print(str(r))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())

BUNDLED_GENERATOR = r'''#!/usr/bin/env python3
from __future__ import annotations
import argparse, base64, json, math, os, struct
from pathlib import Path

OUT = Path("out")
OUT.mkdir(parents=True, exist_ok=True)


def write(name: str, text: str) -> Path:
    p = OUT / name
    p.write_text(text, encoding="utf-8")
    print(f"[generate] {p}", flush=True)
    return p


def tz(title: str, objective: str):
    return write("tz.md", f"""# Техническое задание — {title}

## 1. Назначение
{objective}

## 2. Требования
- Изделие генерируется автоматически как универсальный проект.
- Итоговые артефакты: `out/tz.md`, `out/board.svg`, `out/model.gltf`, `out/page.html`.
- Для финальной инженерной детализации выполняется ручное уточнение.

## 3. Принципы
- planner: rules
- сборка в песочнице (isolation)
- артефакты упакованы в `out/` и архивируются по project/order.

## 4. Дальнейшие шаги
- Внедрить компоненты/схему.
- Выполнить проверки termo-mech.
""")


def drawings(title: str, objective: str):
    return write("drawings.svg", f"""<svg xmlns='http://www.w3.org/2000/svg' width='640' height='420' viewBox='0 0 640 420'>
  <rect x='10' y='10' width='620' height='400' fill='none' stroke='#111' stroke-width='2'/>
  <text x='30' y='40' font-family='monospace' font-size='18'>GENERIC PROJECT: {title}</text>
  <rect x='60' y='120' width='520' height='200' fill='#f5f7fa' stroke='#333'/>
  <text x='60' y='230' font-family='monospace' font-size='12'>{objective}</text>
  <text x='60' y='300' font-family='monospace' font-size='12'>View A / General composition</text>
</svg>""")


def enclose(title: str, objective: str):
    pos = [(-1,-1,1),(1,-1,1),(1,1,1),(-1,1,1),(-1,-1,-1),(1,-1,-1),(1,1,-1),(-1,1,-1)]
    idx = [0,1,2,2,3,0,4,7,6,6,5,4,0,4,5,5,1,0,2,6,7,7,3,2,3,7,4,4,0,3,1,5,6,6,2,1]
    buf = bytearray()
    buf += bytes([i & 0xff for i in idx]) # placeholder
    import array, struct
    idxarr = array.array("H", idx)
    buf = idxarr.tobytes()
    for v in pos:
        for c in v:
            buf += struct.pack("<f", float(c))
    b64 = base64.b64encode(buf).decode("ascii")
    gltf = {"asset":{"version":"2.0"},"scene":0,"scenes":[{"nodes":[0]}],
            "nodes":[{"mesh":0}],"meshes":[{"primitives":[{"attributes":{"POSITION":1},"indices":0}]}],
            "accessors":[
                {"bufferView":0,"componentType":5123,"count":len(idx),"type":"SCALAR"},
                {"bufferView":1,"componentType":5126,"count":len(pos),"type":"VEC3",
                 "min":[-1,-1,-1],"max":[1,1,1]}],
            "bufferViews":[
                {"buffer":0,"byteOffset":0,"byteLength":len(idx)*2,"target":34963},
                {"buffer":0,"byteOffset":len(idx)*2,"byteLength":len(pos)*12,"target":34962}],
            "buffers":[{"uri":"data:application/octet-stream;base64,"+b64,"byteLength":len(buf)}]}
    write("model.gltf", json.dumps(gltf, indent=2))

    stl = "solid cube\n"
    tris = [(0,1,2),(2,3,0),(0,3,7),(7,4,0),(0,4,5),(5,1,0),(1,5,6),(6,2,1),(2,6,7),(7,3,2),(4,7,6),(6,5,4)]
    for tri in tris:
        stl += f"facet normal 0 0 0\n  outer loop\n"
        for vi in tri:
            v = pos[vi]
            stl += f"    vertex {v[0]} {v[1]} {v[2]}\n"
        stl += "  endloop\nendfacet\n"
    stl += "endsolid cube\n"
    return write("model.stl", stl)


def board(title: str, objective: str):
    return write("board.svg", f"""<svg xmlns='http://www.w3.org/2000/svg' width='640' height='420' viewBox='0 0 640 420'>
  <rect width='640' height='420' fill='#111'/>
  <rect x='20' y='20' width='600' height='380' rx='12' fill='#0f3050'/>
  <text x='40' y='50' font-family='monospace' fill='#e2e8f0' font-size='14'>{title} — conceptual PCB</text>
  <g stroke='#38bdf8' stroke-width='2'>
    <line x1='40' y1='100' x2='600' y2='100'/>
    <line x1='40' y1='250' x2='600' y2='250'/>
    <rect x='100' y='160' width='140' height='50' fill='none'/>
    <rect x='400' y='160' width='140' height='50' fill='none'/>
  </g>
  <text x='40' y='389' font-family='monospace' fill='#94a3b8' font-size='10'>{objective}</text>
</svg>""")


def page(title: str, objective: str):
    return write("page.html", f"""<!doctype html>
<html lang='ru'><meta charset='utf-8'>
<title>{title} — результат</title>
<body style='font-family:monospace;background:#111;color:#eee;max-width:1000px;margin:0 auto;padding:20px'>
<h1>{title}</h1>
<p>{objective}</p>
<ul>
  <li><a href='tz.md'>ТЗ (Markdown)</a></li>
  <li><a href='drawings.svg'>Чертежи</a></li>
  <li><a href='model.gltf'>3D-модель GLTF</a></li>
  <li><a href='model.stl'>STL</a></li>
  <li><a href='board.svg'>Плата</a></li>
</ul>
<p>Сгенерировано универсальным агентным движком.</p>
</body></html>""")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--step", default="all", choices=["all", "tz", "drawings", "enclose", "pcb", "page", "test"])
    ap.add_argument("--title", default="Generic Project")
    ap.add_argument("--objective", default="Спроектировать объект.")
    args = ap.parse_args()

    mapping = {
        "tz": lambda: tz(args.title, args.objective),
        "drawings": lambda: drawings(args.title, args.objective),
        "enclose": lambda: enclose(args.title, args.objective),
        "pcb": lambda: board(args.title, args.objective),
        "page": lambda: page(args.title, args.objective),
        "test": lambda: True,
    }
    if args.step == "all":
        for name in ("tz", "drawings", "enclose", "pcb", "page", "test"):
            mapping[name]()
    else:
        mapping[args.step]()

    if args.step == "test":
        expected = ["tz.md", "drawings.svg", "model.gltf", "model.stl", "board.svg", "page.html"]
        missing = [f for f in expected if not (OUT / f).is_file()]
        if missing:
            raise SystemExit(f"missing artifacts: {missing}")
        print(f"[generate] OK: {len(expected)} artifacts present")


if __name__ == "__main__":
    raise SystemExit(main())
'''
