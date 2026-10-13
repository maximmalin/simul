#!/usr/bin/env python3
"""refresh_cad.py — перегенерирует CAD ячейки Брэгга из входных параметров.

Из sim/inputs/bragg_inputs.yaml создаёт в sim/cad/:
  * bragg_cell.FCStd  — параметрический документ FreeCAD (Part-боксы), мм
  * bragg_cell.step   — STEP-копия той же модели (импортируется в любой CAD), мм
  * cad_dims.json     — текущие размеры (промежуточный файл)

Запуск:
    cd /home/mirrage/Desktop/brig-receiver
    .venv/bin/python3 sim/cad/refresh_cad.py

Единицы CAD — миллиметры (привычно для FreeCAD); физический размер сохраняется
(значения из yaml в метрах умножаются на 1000).
"""
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import bragg_sim  # noqa: E402  (входные параметры, конфиг)

CAD = Path(__file__).resolve().parent
FREECAD = "/usr/bin/freecadcmd"


def main():
    cfg = {**bragg_sim.INPUT_PARAMS, **bragg_sim.load_config()}
    geom = cfg["geometry"]
    mm = {k: float(v) * 1000.0 for k, v in geom.items()}   # м → мм
    print(f"[cad]  геометрия (мм): {mm}")

    dims = {"lx": mm["lx"], "ly": mm["ly"],
            "glass_lz": mm["glass_lz"], "quartz_lz": mm["quartz_lz"]}
    (CAD / "cad_dims.json").write_text(json.dumps(dims))

    macro = CAD / "make_bragg_cell_fc.py"
    r = subprocess.run([FREECAD, str(macro)],
                       capture_output=True, text=True)
    out = (r.stdout or "") + (r.stderr or "")

    fcstd = CAD / "bragg_cell.FCStd"
    step = CAD / "bragg_cell.step"
    if r.returncode != 0:
        raise RuntimeError(f"freecadcmd rc={r.returncode}:\n{out[-2000:]}")
    for f in (fcstd, step):
        if not f.exists():
            raise RuntimeError(f"{f.name} не создан:\n{out[-2000:]}")
        print(f"[cad]  {f} ({f.stat().st_size} байт)")

    # выведем последние строки freecadcmd (размеры, объекты)
    lines = [ln for ln in out.splitlines() if ln.strip()]
    for ln in lines[-6:]:
        print("        " + ln[:160])


if __name__ == "__main__":
    main()