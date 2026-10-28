def gen_makefile(title: str, objective: str) -> str:
    return f'''# Generic project generator for: {title}
PY ?= python3
TITLE ?= {title}
OBJECTIVE ?= {objective}

.PHONY: all tz enclose pcb drawings page test clean kicad spice sim gost

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
kicad:
    \t$(PY) tools/schematic_from_json.py --spec design/schematic.json --out-dir out/hardware --name "$(TITLE)"
spice:
    \t$(PY) tools/spice_maker.py --scheme "out/hardware/generate_result.json" --out-dir out/hardware --name "$(TITLE)"
sim:
    \t$(PY) tools/ngspice_measure.py --cir "out/hardware/$$STEM.cir" \
      --out out/hardware/measurements.json || true
gost:
    \tKR_PCB_STEPS=1 $(PY) hardware/tscircuit/run_pcb.py --manifest out/hardware/generate_result.json
    \t@test -f out/hardware/tscircuit/board.step && \
    $(PY) gost_pipeline.py out/hardware/tscircuit/board.step \
       --title "$(TITLE)" --note "Схема и состав - по спецификации проекта" \
           || echo "[gost] нет board.step - полный экспорт платы не удался, чертежи пропущены"
test:
    \t$(PY) tools/ngspice_measure.py --cir "out/hardware/$$STEM.cir" \
      --out out/hardware/measurements.json || true
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
  - name: kicad
    title: Compile agent schematic spec into valid KiCad schematic
    top_level: true
    depends_on: [tz]
  - name: spice
    title: Export SPICE scheme from KiCad
    top_level: false
    depends_on: [kicad]
  - name: sim
    title: Run SPICE simulations
    top_level: false
    depends_on: [spice]
  - name: gost
    title: Generate PCB plate and 3D models
    top_level: true
    depends_on: [kicad]
  - name: page
    title: Generate report page
    top_level: false
    depends_on: [tz, enclose, drawings, pcb]
  artifacts:
    - out/tz.md
    - out/drawings.svg
    - out/board.svg
    - out/model.gltf
    - out/model.stl
    - out/page.html
    - out/hardware/*.kicad_sch
    - out/hardware/*.net
    - out/hardware/generate_result.json
    - design/schematic.json
    - design/calc.json
  vocabulary: {{}}
  system: "Generate deliverables for the project from its own Makefile and project description."
  verify: "make test"
  agent_owner:
    all: agent.build
    tz: agent.build
    enclose: agent.build
    drawings: agent.build
    pcb: agent.build
    kicad: agent.build
    spice: agent.build
    sim: agent.build
    gost: agent.build
    test: agent.verify
'''

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