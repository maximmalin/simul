#!/usr/bin/env python3
"""make_bragg_cell_fc.py — параметрическая 3D-модель ячейки Брэгга в FreeCAD.

Создаёт документ BraggCell: два Part-бокса (Glass снизу, Quartz сверху),
размеры берутся из cad_dims.json (мм), лежащего рядом с этим скриптом.
Сохраняет bragg_cell.FCStd и экспортирует bragg_cell.step.
Размеры можно менять в дереве FreeCAD (свойства Length/Width/Height /
Placement объекта) — модель пересчитается.

Запуск из командной строки (freecadcmd):
    /usr/bin/freecadcmd make_bragg_cell_fc.py

Либо в GUI: меню «Макросы → Выполнить макрос», выбрать этот файл (sim/cad/).
"""
import json
import os

import FreeCAD as App  # noqa: F401 (внутренний модуль FreeCAD)
import Part  # noqa: F401

HERE = os.path.dirname(os.path.abspath(__file__))
DIMS = {"lx": 15.0, "ly": 5.0, "glass_lz": 2.91, "quartz_lz": 0.09}
dims_path = os.path.join(HERE, "cad_dims.json")
if os.path.exists(dims_path):
    DIMS.update(json.load(open(dims_path)))

OUT_FCSTD = os.path.join(HERE, "bragg_cell.FCStd")
OUT_STEP = os.path.join(HERE, "bragg_cell.step")

doc = App.newDocument("BraggCell")

glass = doc.addObject("Part::Box", "Glass")
glass.Length = DIMS["lx"]            # мм
glass.Width = DIMS["ly"]
glass.Height = DIMS["glass_lz"]
glass.Placement = App.Placement(App.Vector(0, 0, 0), App.Rotation())

quartz = doc.addObject("Part::Box", "Quartz")
quartz.Length = DIMS["lx"]
quartz.Width = DIMS["ly"]
quartz.Height = DIMS["quartz_lz"]
quartz.Placement = App.Placement(App.Vector(0, 0, DIMS["glass_lz"]),
                                 App.Rotation())

# Цвета для наглядности (RGB); в headless-режиме ViewObject может быть None
if App.GuiUp:
    for obj in doc.Objects:
        obj.ViewObject.ShapeColor = (0.55, 0.75, 0.85, 0.0)  # стекло — голубоватый
    quartz.ViewObject.ShapeColor = (0.85, 0.65, 0.50, 0.0)    # кварц — песочный

doc.recompute()

# --- сохранение FCStd (параметрический документ) ---
doc.saveAs(OUT_FCSTD)
print(f"saved: {OUT_FCSTD}")

# --- экспорт STEP (кварц сдвигаем вверх на высоту стекла) ---
quartz_shape = quartz.Shape.copy()
quartz_shape.translate(App.Vector(0, 0, DIMS["glass_lz"]))
Part.export([glass.Shape, quartz_shape], OUT_STEP)
print(f"saved: {OUT_STEP}")

print(f"objects: {[o.Name for o in doc.Objects]}")
print(f"units: mm (lx={DIMS['lx']}, ly={DIMS['ly']}, "
      f"glass_lz={DIMS['glass_lz']}, quartz_lz={DIMS['quartz_lz']})")
App.closeDocument(doc.Name)