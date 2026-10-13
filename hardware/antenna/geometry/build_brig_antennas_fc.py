# -*- coding: utf-8 -*-
"""Brig Receiver: build 3 antenna panels in FreeCAD and export STEP + STL.

Run headless:  freecadcmd hardware/antenna/geometry/build_brig_antennas_fc.py

Result files (mm, panel 150x150, FR-4 0.8 mm, copper 0.035 mm):
  hardware/antenna/geometry/
    band_a_meander.step/.stl    - Ch A: 200 MHz meander (3 arms, 63.5 mm)
    band_b_2x2.step/.stl        - Ch B: 2x2 patch array @2.45 GHz (36.89x28.35)
    band_c_4x4.step/.stl        - Ch C: 4x4 patch array @5.8 GHz (15.59x11.62)

The patch resonant lengths are the *retuned* values from the openEMS FDTD
(``sim/outputs/openems_results.json`` -> ``geometry``); they differ from the
raw Balanis estimate so that the element resonates at the design frequency
with a matching probe offset of ``probe_offset_mm`` (see README).
"""
import os, FreeCAD, Part
from FreeCAD import Base
import Mesh, MeshPart

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)))
PANEL = 150.0      # panel size mm
SUB_H = 0.8        # FR-4 thickness
CU_H  = 0.035      # copper thickness
Z_SUB0 = 0.0       # substrate bottom
Z_CU_TOP = SUB_H   # top copper bottom
Z_CU_BOT = -CU_H   # bottom copper (ground) bottom

def panel_shape():
    """FR-4 substrate box."""
    return Part.makeBox(PANEL, PANEL, SUB_H, Base.Vector(0, 0, Z_SUB0))

def ground_shape():
    """Bottom copper ground plane."""
    return Part.makeBox(PANEL, PANEL, CU_H, Base.Vector(0, 0, Z_CU_BOT))

def trace_box(cx, cy, dx, dy):
    """Copper trace rectangle centered (cx,cy) with size dx x dy, top layer."""
    return Part.makeBox(dx, dy, CU_H,
                        Base.Vector(cx - dx / 2, cy - dy / 2, Z_CU_TOP))

def trace_seg(x1, y1, x2, y2, w):
    """Axis-aligned trace segment from (x1,y1) to (x2,y2), width w (all in mm)."""
    if y1 == y2:                      # horizontal
        return trace_box((x1 + x2) / 2, y1, abs(x2 - x1), w)
    return trace_box(x1, (y1 + y2) / 2, w, abs(y2 - y1))

def fence(boxes, doc, name):
    """Fuse all boxes into one solid, add to document (returns doc object)."""
    solid = boxes[0]
    for b in boxes[1:]:
        solid = solid.fuse(b)
    solid = solid.removeSplitter()
    return Part.show(solid, name)

# ---------------------------------------------------------------- Channel A
def build_channel_a(doc):
    """200 MHz meander monopole: 3 arms of 63.5 mm, pitch 15 mm, w=1.2 mm."""
    cw = 1.2
    gap = 15.0
    arm = 63.5
    cx0 = (PANEL - arm) / 2            # 43.25
    cy0 = PANEL / 2 - gap              # 60
    # centerline (feed bottom-left -> open end top-right)
    pts = [(cx0, cy0), (cx0 + arm, cy0), (cx0 + arm, cy0 + gap),
           (cx0, cy0 + gap), (cx0, cy0 + 2 * gap), (cx0 + arm, cy0 + 2 * gap)]
    boxes = []
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        boxes.append(trace_seg(x1, y1, x2, y2, cw))
    # corner fill squares
    for (x, y) in pts[1:5]:
        boxes.append(trace_box(x, y, cw, cw))
    # feed line down to the bottom edge (GCPW transition zone)
    fx, fy = pts[0]
    boxes.append(trace_seg(fx, fy, fx, 15.0, cw))
    boxes.append(trace_box(fx, 15.0, cw, 6.0))     # feed pad
    tr = fence(boxes, doc, "band_a_copper")
    return tr

# ------------------------------------------------------ Channel B: 2x2 array
def build_channel_b(doc):
    # W, L retuned by openEMS FDTD (sim/outputs/openems_results.json)
    W, L, spacing = 36.894, 28.350, 91.7
    cols, rows = 2, 2
    boxes = []
    for i in range(cols):
        for j in range(rows):
            cx = (PANEL - (cols - 1) * spacing) / 2 + i * spacing
            cy = (PANEL - (rows - 1) * spacing) / 2 + j * spacing
            boxes.append(trace_box(cx, cy, W, L))
    return fence(boxes, doc, "band_b_copper")

# ------------------------------------------------------ Channel C: 4x4 array
def build_channel_c(doc):
    # W, L retuned by openEMS FDTD (sim/outputs/openems_results.json)
    W, L, spacing = 15.585, 11.616, 38.77
    cols, rows = 4, 4
    boxes = []
    for i in range(cols):
        for j in range(rows):
            cx = (PANEL - (cols - 1) * spacing) / 2 + i * spacing
            cy = (PANEL - (rows - 1) * spacing) / 2 + j * spacing
            boxes.append(trace_box(cx, cy, W, L))
    return fence(boxes, doc, "band_c_copper")

def export(doc, objs, name):
    step = os.path.join(OUT_DIR, name + ".step")
    stl  = os.path.join(OUT_DIR, name + ".stl")
    import Import
    Import.export(objs, step)
    total = None
    for o in objs:
        m = MeshPart.meshFromShape(o.Shape, 0.15)
        if total is None:
            total = m
        else:
            total.addMesh(m)
    total.write(stl)
    print("exported:", step, "|", stl)

def main():
    for name, builder in [("band_a", build_channel_a),
                          ("band_b", build_channel_b),
                          ("band_c", build_channel_c)]:
        try:
            doc = FreeCAD.newDocument(name)
            panel_o = Part.show(panel_shape(), name + "_fr4")
            gnd_o   = Part.show(ground_shape(), name + "_gnd")
            copper_o = builder(doc)
            FreeCAD.ActiveDocument.recompute()
            export(doc, [panel_o, gnd_o, copper_o], name)
            FreeCAD.closeDocument(name)
        except Exception as e:
            print("FAIL", name, ":", repr(e))
    print("All geometry exported to", OUT_DIR)

main()