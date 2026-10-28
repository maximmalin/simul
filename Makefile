# ===========================================================================
#  Krystal Radio — zero-IF HF SDR direct-conversion receiver
#
#  One entry point that builds every derived artifact of the project:
#  the SKiDL netlist, the tscircuit board (autorouted PCB, gerbers, 3D),
#  the antenna CAD + GOST drawings, the documentation — and runs the test
#  suite, the electrical rules check (ERC) and the design rules check (DRC).
#
#  Everything here is the SDR (direct conversion). The legacy Bragg-cell
#  "brig" board is not part of this build.
#
#      make              # everything (see `all`)
#      make help         # list targets
#
#  Override tools on the command line, e.g.
#      make PY=python3 NODE=node
# ===========================================================================

SHELL      := /bin/bash
.DEFAULT_GOAL := all

# --- toolchain -------------------------------------------------------------
VENV       ?= .venv
PY         ?= $(VENV)/bin/python
PYTHON     ?= python3
PYTEST     ?= pytest
NODE       ?= node
ROLLUP     ?= node_modules/.bin/rollup
KICAD_CLI  ?= kicad-cli
PANDOC     ?= pandoc
SOFFICE    ?= soffice
FREECAD    ?= freecadcmd
OCTAVE     ?= octave

# --- canonical paths -------------------------------------------------------
# SKiDL netlist (tscircuit input)
NETLIST    := hardware/kicad/sdr_receiver.kicad_sch
# ngspice deck
SPICE      := sim/netlists/sdr_skidl_ngspice.cir
# tscircuit export directory
TSOUT      := hardware/tscircuit/out
# bundled three.js viewer for the single-page report
VIEWER_DIR := hardware/tscircuit/viewer
VIEWER     := $(VIEWER_DIR)/dist/krystal-viewer.js
# antenna STEP/STL geometry
ANT_GEOM   := hardware/antenna/geometry
# GOST drawings
CAD_FIG    := sim/cad/figures
# published deliverables
OUT        := out
# ТЗ source & derived docs
DOC        := technical_task

.PHONY: all help \
        netlist spice \
        pcb publish \
        erc drc erc-kicad \
        cad drawings board-svg \
        docs manifest \
        viewer page \
        gnuradio gnuradio-gui \
        link-budget direction \
        reliability thermal enclosure \
        agents agent-api agent-web frontend \
        test test-all \
        schematics schematics_check schematics_bom \
        clean distclean

# ===========================================================================
#  all
# ===========================================================================
all: netlist spice schematics schematics_check schematics_bom pcb publish erc drc board-svg drawings docs viewer gnuradio link-budget direction page test reliability thermal enclosure manifest
	@echo
	@echo "Krystal Radio build complete — see $(OUT)/ and $(CAD_FIG)/"

# ===========================================================================
#  circuit
# ===========================================================================
netlist: ## SKiDL source -> KiCad netlist (tscircuit input)
	$(PY) sim/scripts/sdr_skidl_circuit.py

spice: ## KiCad netlist -> ngspice deck
	$(PY) sim/scripts/export_ngspice.py

# ===========================================================================
#  Схемотехника (агентная фаза): ESKD -> TЗ блоков -> SKiDL -> KiCad
# ===========================================================================
schematics: ## проектирование схемотехники: ESKD -> ТЗ блоков -> SKiDL
	$(PY) hardware/kicad/schematics_design.py

schematics_check: ## проверка схемотехники: ERC, соответствие блоков
	$(PY) hardware/kicad/schematics_check.py

schematics_bom: ## библиотекарь: подбор реальных компонентов по SKiDL
	$(PY) hardware/kicad/schematics_bom.py

# ===========================================================================
#  PCB — the tscircuit part (autorouter), NOT a hand-drawn KiCad board
# ===========================================================================
pcb: netlist ## autoroute + export board/gerbers/3D through tscircuit
	$(NODE) hardware/tscircuit/build_pcb.mjs

publish: pcb ## copy the tscircuit artifacts into out/
	@mkdir -p $(OUT)
	cp -f $(TSOUT)/board.kicad_pcb       $(OUT)/board.kicad_pcb
	cp -f $(TSOUT)/board.kicad_sch       $(OUT)/board.kicad_sch
	cp -f $(TSOUT)/board.step            $(OUT)/board.step
	cp -f $(TSOUT)/board.glb             $(OUT)/board.glb
	cp -f $(TSOUT)/gerbers.zip           $(OUT)/gerbers.zip
	cp -f $(TSOUT)/board.net             $(OUT)/board.net
	cp -f $(TSOUT)/circuit.json          $(OUT)/circuit.json
	cp -f $(TSOUT)/board_schematic.pdf   $(OUT)/board_schematic.pdf
	cp -f $(TSOUT)/krystal-radio-kicad.zip $(OUT)/krystal-radio-kicad.zip
	@echo "published: $(OUT)/"

# ===========================================================================
#  electrical / design rules
# ===========================================================================
erc: netlist ## SKiDL electrical rules check -> $(OUT)/erc.rpt
	$(PY) sim/scripts/erc_sdr.py --report $(OUT)/erc.rpt

drc: pcb ## KiCad DRC on the routed tscircuit board -> $(OUT)/drc.rpt
	@mkdir -p $(OUT)
	$(KICAD_CLI) pcb drc --format report --severity-error \
	    --output $(OUT)/drc.rpt $(TSOUT)/board.kicad_pcb
	@echo "DRC report: $(OUT)/drc.rpt"

erc-kicad: pcb ## informational KiCad ERC on the tscircuit-exported schematic
	$(KICAD_CLI) sch erc --format report --severity-error \
	    --output $(OUT)/erc-kicad.rpt $(TSOUT)/board.kicad_sch
	@echo "KiCad ERC report (informational): $(OUT)/erc-kicad.rpt"

# ===========================================================================
#  CAD / GOST drawings
# ===========================================================================
cad: ## rebuild the antenna panels (STEP/STL) headless in FreeCAD
	$(FREECAD) $(ANT_GEOM)/build_brig_antennas_fc.py

drawings: cad ## STEP -> cleaned -> GOST drawing (DXF/SVG/PDF)
	$(PYTHON) gost_pipeline.py $(ANT_GEOM)/band_a.step
	$(PYTHON) gost_pipeline.py $(ANT_GEOM)/band_b.step
	$(PYTHON) gost_pipeline.py $(ANT_GEOM)/band_c.step
	@echo "GOST drawings: $(CAD_FIG)/"

board-svg: publish ## PCB technical drawing (SVG) from the routed board
	$(KICAD_CLI) pcb export svg --output $(OUT)/board.svg \
	    --layers "F.Cu,B.Cu,F.Silkscreen,B.Silkscreen,Edge.Cuts" \
	    --black-and-white $(OUT)/board.kicad_pcb
	@echo "PCB drawing: $(OUT)/board.svg"

# ===========================================================================
#  single-page report: 3D model + technical drawings
# ===========================================================================
viewer: ## bundle the offline three.js model viewer (rollup)
	$(ROLLUP) -c $(VIEWER_DIR)/rollup.config.mjs
	@echo "viewer bundle: $(VIEWER)"

page: publish board-svg drawings viewer gnuradio link-budget direction ## 3D + drawings + waterfall + дальность/пеленгация на одной странице
	$(PYTHON) tools/build_page.py --out $(OUT)/krystal-radio.html
	@echo "report page: $(OUT)/krystal-radio.html"

# ===========================================================================
#  Корпус (FreeCAD, headless) — REQ-MECH-01/02
# ===========================================================================
enclosure: ## корпус Al с 3 зонами -> FCStd/STEP/STL + out/enclosure.json
	$(FREECAD) hardware/enclosure/build_enclosure_fc.py

# ===========================================================================
#  Agent bridge — HTTP-мост между LLM-агентами и веб-интерфейсом
#  (frontend/ = EngineeringTeam). Фронтенд говорит с проектом только через
#  этот мост: /api/chain (цепочка сборки), /api/projects, /api/metrics,
#  /api/agents, POST /api/agent/run, POST /api/ask.
# ===========================================================================
BRIDGE_PORT ?= 8787
UI_PORT     ?= 5173
#: доп. аргументы для `make run`, напр. make run RUN_ARGS="--prebuilt --host 0.0.0.0"
RUN_ARGS   ?=

agents: ## список локальных агентов (AGENT_PROMPTS.md + AGENT_ENCLOSURE.md)
	$(PYTHON) backend/agent_runtime.py --list

agent-api: ## запустить агентный мост (по умолчанию :8787)
	@echo "agent bridge: http://127.0.0.1:$(BRIDGE_PORT)"
	$(VENV_PY) backend/agent_bridge.py --port $(BRIDGE_PORT)

# --- агентно-функциональный граф -------------------------------------------
# Граф живёт в .venv: там langgraph. VENV_PY — интерпретатор с ним,
# если venv есть, иначе системный (тогда граф идёт запасным путём).
VENV_PY  := $(shell test -x .venv/bin/python && echo .venv/bin/python || echo $(PYTHON))
#: пакет agents лежит в backend/, а make запускается из корня
BACKEND  := $(CURDIR)/backend

llm-check: ## настройки LLM: выключатель, маршруты, живость провайдеров
	PYTHONPATH=$(BACKEND) $(VENV_PY) backend/llm.py --probe

llm-off: ## подсказка: выключить LLM принудительно (KR_LLM_ENABLED=0)
	@echo "export KR_LLM_ENABLED=0"

agents-prompts: ## перегенерировать AGENT_PROMPTS.md из agents.project.yaml
	PYTHONPATH=$(BACKEND) $(VENV_PY) -m agents prompts --root . --write AGENT_PROMPTS.md
agents-check: ## что движок увидел в проекте: раннер, цели, артефакты
	PYTHONPATH=$(BACKEND) $(VENV_PY) -m agents check --root .

agents-graph: ## узлы графа и порядок их вызова
	PYTHONPATH=$(BACKEND) $(VENV_PY) -m agents graph

agents-ask: ## разобрать заказ в цели, ничего не запуская (TEXT="...")
	PYTHONPATH=$(BACKEND) $(VENV_PY) -m agents ask --root . "$(TEXT)"

agents-run: ## выполнить заказ через граф (TEXT="...")
	PYTHONPATH=$(BACKEND) $(VENV_PY) -m agents run --root . "$(TEXT)"

# --- репозиторий без веб-интерфейса ---------------------------------------
# Создание, публикация и удаление репозитория на Forgejo из терминала.
# Учётные данные берутся из ~/.git-credentials — отдельный токен не нужен:
# Forgejo принимает пароль по HTTP Basic и для git, и для API.
REPO_NAME ?= $(shell basename $(CURDIR))
REPO_DESC ?=

repo-init: ## создать репозиторий на Forgejo и запушить (REPO_NAME=..., REPO_DESC=...)
	./scripts/repo.sh init $(REPO_NAME) "$(REPO_DESC)"

repo-list: ## какие репозитории есть на сервере
	./scripts/repo.sh list

repo-push: ## отправить текущую ветку на forgejo
	./scripts/repo.sh push forgejo

frontend: ## Vite dev-сервер интерфейса (проксирует /api на мост)
	cd frontend && npm run dev

run: ## весь проект по одному адресу: мост + интерфейс (Ctrl-C — остановка)
	$(PYTHON) scripts/launch.py $(RUN_ARGS)

run-build: ## то же, но собранный dist/ без HMR (быстрее, стабильнее)
	$(PYTHON) scripts/launch.py --prebuilt $(RUN_ARGS)

# ===========================================================================
#  Дальность приёма и пеленгация (link budget + direction finding)
# ===========================================================================
link-budget: ## энергетика тракта -> out/link_budget.json + out/link_budget.svg
	$(PYTHON) sim/scripts/link_budget.py --out $(OUT)
	@echo "link budget: $(OUT)/link_budget.json"

direction: ## пеленгация (NEC-2/AntennaSim) -> out/direction_finding.json + .svg
	$(PYTHON) sim/scripts/direction_finding.py --out $(OUT)
	@echo "direction finding: $(OUT)/direction_finding.json"

# ===========================================================================
#  GNU Radio co-simulation (виртуальный приём -> водопад)
# ===========================================================================
gnuradio: ## виртуальный приём -> out/waterfall.png + out/virtual_rx_spectrum.json
	$(PYTHON) src/gnuradio_krystal/krystal_virtual_rx.py --out $(OUT) --nsamples 262144
	@echo "waterfall: $(OUT)/waterfall.png"

gnuradio-gui: ## живой водопад GNU Radio (нужен дисплей; выход — закрыть окно)
	$(PYTHON) src/gnuradio_krystal/krystal_virtual_rx.py --gui

# ===========================================================================
#  docs
# ===========================================================================
docs: ## ТЗ -> HTML / DOCX / PDF
	$(PANDOC) $(DOC).md -o $(DOC).html --standalone --toc \
	    --metadata title="Техническое задание — Krystal Radio"
	$(PANDOC) $(DOC).md -o $(DOC).docx
	$(SOFFICE) --headless --convert-to pdf --outdir . $(DOC).docx >/dev/null 2>&1
	@echo "docs: $(DOC).html $(DOC).docx $(DOC).pdf"

manifest: ## write manifest.json
	$(PYTHON) tools/dump_manifest.py

# ===========================================================================
#  tests
# ===========================================================================
test: ## requirement tests + netlist & pin-contract checks
	$(PYTEST) tests/requirements -q
	$(PY) sim/scripts/test_sdr_netlist.py
	$(PY) sim/scripts/verify_pin_contract.py

test-all: ## full pytest tree (includes known WIP / co-sim tests)
	-$(PYTEST) tests -q

# ===========================================================================
#  reliability / thermal compliance
# ===========================================================================
reliability: ## Octave parts-count MTBF (REQ-26) -> $(OUT)/reliability.json
	@mkdir -p $(OUT)
	$(OCTAVE) --quiet --eval "addpath('sim/octave'); reliability_model('sim/octave/reliability_parts.tsv','$(OUT)/reliability.json')"

thermal: ## fast + FEM thermal check (PCB CAD + ngspice) -> sim/outputs
	$(PYTHON) sim/scripts/thermal_pcb.py --out sim/outputs
	@mkdir -p $(OUT)
	cp -f sim/outputs/thermal_fast.json sim/outputs/thermal_fem.json $(OUT)/
	@echo "thermal artifacts: $(OUT)/thermal_{fast,fem}.json"

# ===========================================================================
#  housekeeping
# ===========================================================================
clean: ## remove derived outputs (keeps sources)
	rm -rf $(TSOUT)
	rm -rf $(VIEWER_DIR)/dist
	rm -f $(OUT)/erc.rpt $(OUT)/drc.rpt $(OUT)/erc-kicad.rpt
	rm -f $(OUT)/board.svg $(OUT)/krystal-radio.html
	rm -f $(OUT)/waterfall.png $(OUT)/virtual_rx_iq.c64 $(OUT)/virtual_rx_spectrum.json
	rm -f $(OUT)/link_budget.json $(OUT)/link_budget.svg
	rm -f $(OUT)/direction_finding.json $(OUT)/direction_finding.svg $(OUT)/df_array.nec
	rm -f $(OUT)/enclosure.json
	rm -rf hardware/enclosure/*.FCStd hardware/enclosure/*.step hardware/enclosure/*.stl
	rm -f $(DOC).html $(DOC).docx $(DOC).pdf
	rm -rf $(CAD_FIG)
	find . -path ./.venv -prune -o -path ./node_modules -prune -o \
	    -name '__pycache__' -type d -print -exec rm -rf {} + 2>/dev/null || true
	@echo "cleaned"

distclean: clean ## clean + drop out/, dependencies and venv
	rm -rf $(OUT) node_modules $(VENV)

help: ## show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
	    awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'
