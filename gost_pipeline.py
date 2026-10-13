#!/usr/bin/env python3
"""
gost_pipeline.py — полный пайплайн: STEP → очистка (Analysis Situs) → ГОСТ-чертёж.

ЭТАПЫ
  1. Генерация 3D-геометрии (параметрическая модель из bragg_inputs.yaml)
     → корректный STEP через FreeCAD Import.export (не Part.export — он даёт
       битый STEP без геометрии).
  2. Очистка геометрии в Analysis Situs (batch, без GUI):
       load-step → check-validity → heal-small-edges → maximize-faces → save-brep
     (save-as *.step в этой сборке не пишет файл, save-brep работает)
  3. FreeCAD: читает очищенный BREP → сохраняет чистый STEP → строит страницу
     TechDraw: фронтальная + профильная + изометрия, габариты по ГОСТ 2.307,
     отметка центра тяжести, техтребования.
  4. Экспорт страницы в DXF / SVG / PDF (DXF открывается в LibreCAD с
     ГОСТ-шаблонами и шрифтами OpenGOST).

Запуск:
    cd /home/mirrage/Desktop/brig-receiver
    .venv/bin/python3 gost_pipeline.py                      # параметрическая модель
    .venv/bin/python3 gost_pipeline.py file.step            # очистить свой STEP
    .venv/bin/python3 gost_pipeline.py file.FCStd           # взять геометрию из FCStd
"""
import json
import os
import shutil
import subprocess
import time
import sys
from pathlib import Path

PROJECT = Path("/home/mirrage/Desktop/brig-receiver")
CAD = PROJECT / "sim" / "cad"
FIG = CAD / "figures"
WORK = PROJECT / "sim" / "work" / "gost"
YAML = PROJECT / "sim" / "inputs" / "bragg_inputs.yaml"
TPL_DIR = "/usr/share/freecad/Mod/TechDraw/Templates"
ASI = "/mnt/ext4data/home-mirrage/Desktop/analysis-situs/AnalysisSitus-2025.2.AppImage"

for d in (FIG, WORK):
    d.mkdir(parents=True, exist_ok=True)


# --------------------------------------------------------------------------- #
# НАСТРОЙКИ ЧЕРТЕЖА — применяются к ЛЮБОЙ модели
# (тексты можно менять здесь, не трогая логику построения)
# --------------------------------------------------------------------------- #
DEFAULTS = {
    # --- надписи на листе ---
    "title":        "",          # "" -> имя модели (имя файла)
    "extra_title":  "",          # доп. строка под названием
    "tech_notes": [             # технические требования (ГОСТ 2.307)
        u"Размеры в миллиметрах.",
        u"Неуказанные предельные отклонения: ±0,5 мм.",
    ],
    "note_show_center": True,   # выводить пояснение к отметке ⊕
    "center_symbol":   u"⊕",  # знак центра тяжести (ГОСТ 2.305)
    "note_show_dims":   True,   # выводить строку с габаритами

    # --- настройки проекций ---
    "front_dir":   (0, -1, 0),
    "profile_dir": (1, 0, 0),
    "iso_dir":     (0.577, -0.577, 0.577),
    "gap":         18.0,        # зазор между видами, мм листа

    # --- оформление ---
    "show_dims":     True,      # проставлять габариты
    "show_center":   True,      # отмечать центр тяжести
    "template":      "",        # "" -> автовыбор ISO5457
    "autoscale":     True,      # True -> подобрать масштаб по листу
    "fixed_scale":    1.0,      # используется при autoscale=False
}


def make_cfg(src_name: str, overrides: dict | None = None) -> dict:
    """Собирает настройки чертежа для конкретной модели."""
    cfg = dict(DEFAULTS)
    cfg["tech_notes"] = list(DEFAULTS["tech_notes"])
    cfg["title"] = src_name
    if overrides:
        cfg.update(overrides)
    if isinstance(overrides, dict) and "tech_notes" in overrides:
        cfg["tech_notes"] = list(overrides["tech_notes"])
    return cfg


def load_params() -> dict:
    """Геометрия из bragg_inputs.yaml в миллиметрах."""
    try:
        import yaml
        cfg = yaml.safe_load(YAML.read_text()) or {}
    except Exception:
        cfg = {}
    g = cfg.get("geometry", {})
    return {
        "lx": float(g.get("lx", 15e-3)) * 1000.0,
        "ly": float(g.get("ly", 5e-3)) * 1000.0,
        "glass_lz": float(g.get("glass_lz", 2.91e-3)) * 1000.0,
        "quartz_lz": float(g.get("quartz_lz", 0.09e-3)) * 1000.0,
    }


# --------------------------------------------------------------------------- #
# ЭТАП 1 — генерация корректного STEP
# --------------------------------------------------------------------------- #
GEN_STEP = r'''
import FreeCAD as App, Import, json, os, sys

SRC  = __SRC__      # "" | FCStd | STEP
OUT  = __OUT__
P    = __PARAMS__

if SRC.endswith(".FCStd"):
    src = App.openDocument(SRC)
    parts = [o for o in src.Objects
             if getattr(o, "Shape", None) is not None and not o.Shape.isNull()
             and not o.isDerivedFrom("TechDraw::DrawView")]
    doc = App.newDocument("GEN")
    keep = []
    for p in parts:
        c = doc.addObject("Part::Feature", p.Name); c.Shape = p.Shape; c.Label = p.Label
        keep.append(c)
    App.closeDocument(src.Name)
elif SRC.endswith((".step", ".stp")):
    import Part
    doc = App.newDocument("GEN")
    s = Part.Shape(); s.read(SRC)
    o = doc.addObject("Part::Feature", "Import"); o.Shape = s
    doc.recompute()
    keep = [o]
else:
    doc = App.newDocument("GEN")
    g = doc.addObject("Part::Box", "Glass")
    g.Length, g.Width, g.Height = P["lx"], P["ly"], P["glass_lz"]; g.Label = "Стекло"
    q = doc.addObject("Part::Box", "Quartz")
    q.Length, q.Width, q.Height = P["lx"], P["ly"], P["quartz_lz"]; q.Label = "Кварц"
    q.Placement = App.Placement(App.Vector(0, 0, P["glass_lz"]), App.Rotation())
    doc.recompute()
    keep = [g, q]

Import.export(keep, OUT)
print("STEP_OK:", OUT)
'''


# --------------------------------------------------------------------------- #
# ЭТАП 2 — очистка в Analysis Situs (TCL)
# --------------------------------------------------------------------------- #
CLEAN_TCL = """load-step "{step}"
get-summary nc ncs ns nsh nf nw ne nv
puts "BEFORE solids=$ns faces=$nf edges=$ne"
check-validity
check-contours
check-open-edges
heal-small-edges 1e-4 1.0
maximize-faces
misc-repair-gaps
get-summary nc ncs ns2 nsh nf2 nw ne2 nv
puts "AFTER solids=$ns2 faces=$nf2 edges=$ne2"
check-validity
save-brep "{brep}"
puts "CLEAN_OK"
exit
"""


# --------------------------------------------------------------------------- #
# ЭТАП 3+4 — TechDraw: проекции, габариты, экспорт
# --------------------------------------------------------------------------- #
EXPORT_DXF = r'''
# --- headless-экспорт DXF с реальными проекциями (для LibreCAD) ---
import FreeCAD as App, Part, TechDraw, Import, os

BREP = __BREP__
DXF  = __DXF__
TITLE = __TITLE__

shape = Part.Shape()
shape.read(BREP)
bb = shape.BoundBox
DX, DY, DZ = bb.XLength, bb.YLength, bb.ZLength

def auto_scale(m, target=90.0):
    for s in (10, 5, 2, 1, 0.5, 0.2, 0.1, 0.05, 0.02, 0.01):
        if m * s <= target:
            return s
    return 0.01

S = auto_scale(max(DX, DY, DZ))

def projection(dirv):
    """HLR-проекция: объединение видимых контуров."""
    res = TechDraw.project(shape, App.Vector(*dirv))
    edges = []
    for r in res:
        if r is None or r.isNull():
            continue
        edges.extend(r.Edges)
    if not edges:
        return None
    comp = Part.Compound(edges)
    # центрируем по габариту проекции и масштабируем
    b = comp.BoundBox
    c = App.Vector(b.Center)
    comp.translate(App.Vector(-c.x, -c.y, 0))
    return comp

# раскладка листа A4 landscape (297 x 210 мм), поля 10 мм
FRONT   = (0, -1, 0)
PROFILE = (1, 0, 0)
ISO     = (0.577, -0.577, 0.577)

doc = App.newDocument("DXF")
objs = []
LAYOUT = (("FrontView",     FRONT,  S,      App.Vector(90, 150, 0)),
          ("ProfileView",   PROFILE, S,     App.Vector(200, 150, 0)),
          ("IsometricView", ISO,    S * 0.8, App.Vector(140, 60, 0)))
for name, d, sc, place in LAYOUT:
    pr = projection(d)
    if pr is None:
        continue
    pr.scale(sc)
    pr.translate(place)
    o = doc.addObject("Part::Feature", name)
    o.Shape = pr
    o.Label = name
    objs.append(o)

doc.recompute()
import importDXF
importDXF.export(objs, DXF)
print("DXF_OK:", DXF, "scale:", S, "objects:", len(objs))
'''


# --------------------------------------------------------------------------- #
# GUI-СЕССИЯ (xvfb) — здесь TechDraw реально считает HLR-проекции
# --------------------------------------------------------------------------- #
GUI_SESSION = r'''
import os, glob, time, traceback

STATUS = __STATUS__
lines = []
def say(s):
    lines.append(s)

try:
    import FreeCAD as App
    import FreeCADGui
    import Part, TechDraw, TechDrawGui

    BREP, FCSTD = __BREP__, __FCSTD__
    EXPDIR, STEM = __EXPDIR__, __STEM__

    def fmt(v):
        r = round(float(v), 2)
        if abs(r - round(r)) < 1e-9:
            return str(int(round(r)))
        return ("%.2f" % r).rstrip("0").rstrip(".")

    def auto_scale(m, target=150.0):
        """Множитель масштаба для чертежа (ГОСТ 2.305).
        Сначала увеличения (2:1, 5:1, 10:1 …), затем уменьшения (1:2, 1:5 …)."""
        for mag in (50, 20, 10, 5, 2, 1):
            if m * mag <= target:
                return float(mag)
        for s in (2, 5, 10, 20, 50, 100):
            if m / s <= target:
                return 1.0 / s
        return 0.01

    def scale_label(sc):
        return u"%g:1" % sc if sc >= 1 else u"1:%g" % (1.0 / sc)

    shape = Part.Shape(); shape.read(BREP)
    if shape.isNull():
        raise RuntimeError("empty BREP")

    doc = App.newDocument("GOST")
    cell = doc.addObject("Part::Feature", "Cell")
    cell.Shape = shape; cell.Label = STEM
    doc.recompute()

    bb = shape.BoundBox
    DX, DY, DZ = bb.XLength, bb.YLength, bb.ZLength
    vol = shape.Volume

    com = None
    solids = shape.Solids
    acc, tv = None, 0.0
    for s in solids:
        try:
            v = s.Volume; c = s.CenterOfMass
        except Exception:
            continue
        if v <= 1e-12:
            continue
        w = App.Vector(c.x*v, c.y*v, c.z*v)
        acc = w if acc is None else acc + w
        tv += v
    com = App.Vector(acc.x/tv, acc.y/tv, acc.z/tv) if (acc and tv > 1e-12) \
          else App.Vector(bb.Center)
    say("METRICS: %.1fx%.1fx%.1f V=%.1f solids=%d" % (DX, DY, DZ, vol, len(solids)))
    say("COM: (%.2f, %.2f, %.2f)" % (com.x, com.y, com.z))

    CFG = __CFG__

    tpl_file = CFG.get("template") or ""
    if not tpl_file:
        cands = sorted(glob.glob("/usr/share/freecad/Mod/TechDraw/Templates"
                                 + "/*A4_Landscape*.svg"))
        tpl_file = next((c for c in cands
                         if "ISO5457_advanced" in os.path.basename(c)),
                        cands[0] if cands else "")

    page = doc.addObject("TechDraw::DrawPage", "Page")
    tpl = doc.addObject("TechDraw::DrawSVGTemplate", "Template")
    tpl.Template = tpl_file
    page.Template = tpl
    doc.recompute()
    page.KeepUpdated = True

    # адаптивная раскладка: измеряем реальные проекции и подбираем масштаб
    __SCALER__
    VIEWS_DEF = [("FrontView",     CFG["front_dir"],   0),
                 ("ProfileView",   CFG["profile_dir"], 0),
                 ("IsometricView", CFG["iso_dir"],     1)]
    if CFG.get("autoscale", True):
        SCALE, PLACED, FOOT = adaptive_layout(shape, VIEWS_DEF,
                                              gap=CFG.get("gap", 18.0))
    else:
        SCALE = float(CFG.get("fixed_scale", 1.0))
        n = len(VIEWS_DEF)
        PLACED = [(nmm, USABLE_X0 + USABLE_W * (i + 0.5) / n,
                   USABLE_Y0 + USABLE_H * 0.6)
                  for i, (nmm, _d, _l) in enumerate(VIEWS_DEF)]
        FOOT = (USABLE_W, USABLE_H)
    pos = {n: (x, y) for n, x, y in PLACED}
    say("SCALE: " + scale_label(SCALE) + " footprint=%.0fx%.0f mm" % FOOT)

    LABELS = {"FrontView": u"\u0424\u0440\u043e\u043d\u0442\u0430\u043b\u044c\u043d\u0430\u044f",
              "ProfileView": u"\u041f\u0440\u043e\u0444\u0438\u043b\u044c\u043d\u0430\u044f",
              "IsometricView": u"\u0418\u0437\u043e\u043c\u0435\u0442\u0440\u0438\u044f"}

    def add_view(name, direction, sc):
        """ВАЖНО: page.addView() сбрасывает X/Y в центр страницы —
        позиционировать нужно ПОСЛЕ добавления."""
        v = doc.addObject("TechDraw::DrawViewPart", name)
        v.Source = [cell]
        v.Direction = direction
        page.addView(v)
        x, y = pos[name]
        v.X, v.Y, v.Scale = float(x), float(y), float(sc)
        v.Label = LABELS.get(name, name)
        doc.recompute()
        return v

    front = add_view("FrontView", VIEWS_DEF[0][1], SCALE)
    profile = add_view("ProfileView", VIEWS_DEF[1][1], SCALE)
    iso = add_view("IsometricView", VIEWS_DEF[2][1], SCALE)

    FreeCADGui.updateGui()
    for _ in range(60):
        n = sum(len(v.getVisibleEdges()) for v in (front, profile, iso))
        if n > 0:
            break
        FreeCADGui.updateGui()
        time.sleep(0.25)

    edges = {v.Name: len(v.getVisibleEdges()) for v in (front, profile, iso)}
    say("HLR_EDGES: " + ", ".join("%s=%d" % kv for kv in edges.items()))

    # габариты (ГОСТ 2.307) — только если включено в настройках
    if CFG.get("show_dims", True):
        def dim(name, view, kind, value, x, y):
            d = doc.addObject("TechDraw::DrawViewDimension", name)
            d.Type = kind
            d.References2D = [(view, "Edge0")]
            d.Arbitrary = True
            d.FormatSpec = str(value)
            page.addView(d)                  # сначала вставить на лист
            d.X, d.Y = float(x), float(y)    # затем сместить
            doc.recompute()
            return d

        span_top = max((abs(float(v.X) - float(front.X)) for v in page.Views
                        if v.isDerivedFrom("TechDraw::DrawViewPart")), default=40.0)
        dim("DimWidthX",  front,   "DistanceX", fmt(DX), -(span_top / 2.0 + 14), 12)
        dim("DimHeightZ", front,   "DistanceY", fmt(DZ),  14, -10)
        dim("DimDepthY",  profile, "DistanceX", fmt(DY), -20, 12)

    # отметка центра тяжести
    if CFG.get("show_center", True):
        sym = CFG.get("center_symbol", u"\u2295")
        for nm, view, (ax, sx) in (("CoM_front", front, (com.x, bb.Center.x)),
                                   ("CoM_profile", profile, (com.y, bb.Center.y))):
            a = doc.addObject("TechDraw::DrawViewAnnotation", nm)
            a.Text = [sym]
            page.addView(a)
            a.X = float(view.X) + (ax - sx) * SCALE
            a.Y = float(view.Y) + (com.z - bb.Center.z) * SCALE
            a.TextSize = 6.0
            doc.recompute()

    # технические требования
    notes = list(CFG.get("tech_notes") or [])
    if CFG.get("note_show_dims", True):
        notes.append(u"Габариты: %.1f×%.1f×%.1f мм, V=%.1f мм³."
                     % (DX, DY, DZ, vol))
    if CFG.get("note_show_center", True):
        notes.append(u"%s — центр тяжести (%.1f, %.1f, %.1f)."
                     % (CFG.get("center_symbol", u"\u2295"), com.x, com.y, com.z))
    if notes:
        tt = doc.addObject("TechDraw::DrawViewAnnotation", "TechReq")
        tt.Text = notes
        page.addView(tt)
        tt.TextSize = 3.0                     # мелкий шрифт, чтобы влезло в полосу
        tt.X = NOTE_X0 + NOTE_W / 2.0        # X — центр блока текста
        tt.Y = NOTE_Y + 6.0
        doc.recompute()

    # заголовок — ВНУТРИ листа, над зоной видов
    ttl_lines = [CFG.get("title") or STEM]
    if CFG.get("extra_title"):
        ttl_lines.append(CFG["extra_title"])
    ttl_lines.append(u"Масштаб " + scale_label(SCALE))
    title = doc.addObject("TechDraw::DrawViewAnnotation", "MainTitle")
    title.Text = ttl_lines
    page.addView(title)
    title.TextSize = 5.0
    title.X = USABLE_X0 + USABLE_W / 2.0
    title.Y = USABLE_Y1 - 5.0
    doc.recompute()

    # заполняем штамп (редактируемые поля SVG-шаблона)
    try:
        ed = dict(tpl.EditableTexts)
        def put(keys, value):
            for k in keys:
                if k in ed:
                    ed[k] = value
                    return True
            return False
        put(("Scale",), scale_label(SCALE))
        put(("TITLE", "FC-Title", "Title"), CFG.get("title") or STEM)
        put(("FC-SH", "Drawing sheet", "Sheet"), STEM)
        put(("FC-SC", "Scale",), scale_label(SCALE))
        put(("FC-MN", "Material", "FC-MAT"), CFG.get("material", ""))
        put(("FC-DA", "Date",), __DATE__)
        put(("DN", "Drawing number"), CFG.get("drawing_no", ""))
        put(("FC-DY", "Drawn by"), CFG.get("drawn_by", ""))
        tpl.EditableTexts = ed
        doc.recompute()
    except Exception as e:
        say("TPL_FIELDS_SKIPPED: %s" % e)

    doc.recompute()
    FreeCADGui.updateGui()
    doc.saveAs(FCSTD)
    say("SAVED: " + os.path.basename(FCSTD))

    base = os.path.join(EXPDIR, os.path.splitext(os.path.basename(FCSTD))[0])
    for ext, fn in (("svg", "exportPageAsSvg"), ("pdf", "exportPageAsPdf")):
        try:
            getattr(TechDrawGui, fn)(page, base + "." + ext)
            say("EXPORTED: " + base + "." + ext)
        except Exception as e:
            say("EXPORT_FAIL %s: %s" % (ext, e))
    say("GUI_OK")

except Exception:
    lines.append("GUI_ERROR")
    lines.append(traceback.format_exc())

open(STATUS, "w").write("\n".join(lines))
'''


# --------------------------------------------------------------------------- #
# АДАПТИВНЫЙ СКЕЙЛЕР — общий модуль для консольного и GUI-режима
# --------------------------------------------------------------------------- #
SCALER_SRC = r'''
# --- геометрия листа A4 (ГОСТ 2.601 / 2.305) ---
SHEET_W, SHEET_H = 297.0, 210.0
MARGIN_L, MARGIN_R, MARGIN_T, MARGIN_B = 20.0, 10.0, 10.0, 10.0
# основная надпись (штамп) в правом нижнем углу
TITLEBLOCK_W, TITLEBLOCK_H = 180.0, 55.0
# поля для текстовых примечаний (техтребования) снизу слева
NOTE_H = 42.0

USABLE_X0 = MARGIN_L
USABLE_X1 = SHEET_W - MARGIN_R
USABLE_Y0 = MARGIN_B
USABLE_Y1 = SHEET_H - MARGIN_T
USABLE_W = USABLE_X1 - USABLE_X0
USABLE_H = USABLE_Y1 - USABLE_Y0

# Основная надпись (штамп) — правый нижний угол
TB_X0 = USABLE_X1 - TITLEBLOCK_W
TB_Y0 = USABLE_Y0
TB_Y1 = USABLE_Y0 + TITLEBLOCK_H

# Зона видов — выше нижней полосы (штамп + техтребований) и ниже заголовка
TITLE_H = 16.0
VIEW_Y1 = USABLE_Y1 - TITLE_H
VIEW_Y0 = TB_Y1 + 6.0
VIEW_H = VIEW_Y1 - VIEW_Y0

# Полоса под видами: слева техтребования, справа штамп
NOTE_X0 = USABLE_X0
NOTE_X1 = TB_X0
NOTE_Y = USABLE_Y0 + 4.0
NOTE_W = TB_X0 - USABLE_X0

def scale_label(sc):
    return u"%g:1" % sc if sc >= 1 else u"1:%g" % (1.0 / sc)

def gost_scales():
    """Стандартный ряд масштабов ГОСТ 2.305 (множители)."""
    return [1/100., 1/50., 1/20., 1/10., 1/5., 1/2.,
            1., 2., 5., 10., 20., 50.]

def project_extent(shape, direction):
    """Габариты реальной HLR-проекции: (ширина, высота) в мм модели."""
    import TechDraw, Part
    res = TechDraw.project(shape, App.Vector(*direction))
    edges = []
    for r in res:
        if r is None or r.isNull():
            continue
        edges.extend(r.Edges)
    if not edges:
        return 0.0, 0.0
    b = Part.Compound(edges).BoundBox
    return b.XLength, b.YLength

def _rows_of(views, layout):
    """Раскладка 'rows': верхний ряд = первые два вида, нижний = изометрия.
    Раскладка 'line': все виды в один ряд."""
    if layout == "line":
        return [list(views)]
    return [[v for v in views if v[2] == 0], [v for v in views if v[2] == 1]]


def adaptive_layout(shape, views, gap=16.0):
    """Подбирает масштаб и координаты видов так, чтобы всё влезло на A4.

    Пробует две раскладки (в один ряд и 2+1) и выбирает ту, что даёт
    БОЛЬШИЙ масштаб — как в SolidWorks при автоматической компоновке.
    Все виды получают один масштаб (ГОСТ 2.305).
    Возвращает (scale, placements, footprint); placements — [(имя, x, y)].
    """
    ext = {}
    for name, d, _lvl in views:
        ext[name] = project_extent(shape, d)

    avail_w, avail_h = USABLE_W, VIEW_H

    def measure(rows, sc):
        rw = [sum(ext[n][0] for n, _d, _l in row) * sc + gap * (len(row) - 1)
              for row in rows if row]
        rh = [max(ext[n][1] for n, _d, _l in row) * sc for row in rows if row]
        return max(rw), sum(rh) + gap * (len(rh) - 1)

    # перебираем ВСЕ раскладки и берём ту, что даёт наибольший масштаб
    best_plan = None
    for layout in ("rows", "line"):
        rows = _rows_of(views, layout)
        for sc in sorted(gost_scales(), reverse=True):
            w, h = measure(rows, sc)
            if w <= avail_w and h <= avail_h:
                best_plan = (sc, rows, layout, w, h)
                break          # масштабы перебираются от крупного — берём первый подходящий
        # раскладку проверяем целиком: если нашли масштаб, сравниваем с текущим лучшим

    if best_plan is None:                  # не влезло ни одной раскладкой
        denom = max(1.0, max(max(v) for v in ext.values()))
        sc = max(0.001, min(avail_w, avail_h) / denom * 0.9)
        rows = _rows_of(views, "rows")
        w, h = measure(rows, sc)
        best_plan = (sc, rows, "rows", w, h)

    best, rows, layout, foot_w, foot_h = best_plan

    # размещение: центрируем композицию по вертикали в зоне видов
    tops = []
    y = VIEW_Y1 - max(0.0, (VIEW_H - foot_h) / 2.0)
    for row in rows:
        if not row:
            tops.append(None)
            continue
        h = max(ext[n][1] for n, _d, _l in row) * best
        cy = y - h / 2.0
        tops.append(cy)
        y -= h + gap

    placements = []
    for row, cy in zip(rows, tops):
        if not row:
            continue
        w_row = sum(ext[n][0] for n, _d, _l in row) * best + gap * (len(row) - 1)
        x = USABLE_X0 + (avail_w - w_row) / 2.0
        for name, _d, _l in row:
            w = ext[name][0] * best
            placements.append((name, x + w / 2.0, cy))
            x += w + gap

    return best, placements, (foot_w, foot_h)
'''


MAKE_DRAWING = r'''
import FreeCAD as App, Part, Import, glob, os, sys

BREP = __BREP__
STEP_OUT = __STEPOUT__
FCSTD_OUT = __FCSTD_OUT__
TPL = __TPL__
EXPDIR = __EXPDIR__
TITLE = __TITLE__

def pick_template(folder):
    cands = sorted(glob.glob(os.path.join(folder, "*A4_Landscape*.svg")))
    for pref in ("ISO5457_advanced", "TD", "blank"):
        for c in cands:
            if pref in os.path.basename(c):
                return c
    return cands[0] if cands else os.path.join(folder, "A4_Landscape_blank.svg")

def fmt(v):
    r = round(float(v), 2)
    if abs(r - round(r)) < 1e-9:
        return str(int(round(r)))
    return ("%.2f" % r).rstrip("0").rstrip(".")

def auto_scale(m, target=160.0):
    for s in (10, 5, 2, 1, 0.5, 0.2, 0.1, 0.05, 0.02, 0.01):
        if m * s <= target:
            return s
    return 0.01

# ---------- геометрия из очищенного BREP ----------
shape = Part.Shape()
shape.read(BREP)
if shape.isNull():
    print("ERROR_EMPTY_BREP"); sys.exit(4)

doc = App.newDocument("GOST")
cell = doc.addObject("Part::Feature", "Cell")
cell.Shape = shape
cell.Label = TITLE
doc.recompute()

# чистый STEP на выход
Import.export([cell], STEP_OUT)
print("CLEAN_STEP_OK:", STEP_OUT)

# ---------- метрики ----------
bb = shape.BoundBox
DX, DY, DZ = bb.XLength, bb.YLength, bb.ZLength
vol = shape.Volume

# центр тяжести: считаем по твёрдым телам (у Shape без массы атрибута нет)
com = None
try:
    solids = shape.Solids
except Exception:
    solids = []
if solids:
    acc = None
    tv = 0.0
    for s in solids:
        try:
            v = s.Volume
            c = s.CenterOfMass
        except Exception:
            continue
        if v <= 1e-12:
            continue
        w = App.Vector(c.x * v, c.y * v, c.z * v)
        acc = w if acc is None else acc + w
        tv += v
    if acc is not None and tv > 1e-12:
        com = App.Vector(acc.x / tv, acc.y / tv, acc.z / tv)
if com is None:
    com = App.Vector(bb.Center)

print("METRICS: DX=%.3f DY=%.3f DZ=%.3f V=%.3f solids=%d"
      % (DX, DY, DZ, vol, len(solids)))
print("COM: (%.4f, %.4f, %.4f)" % (com.x, com.y, com.z))

# ---------- страница ----------
page = doc.addObject("TechDraw::DrawPage", "Page")
tpl = doc.addObject("TechDraw::DrawSVGTemplate", "Template")
tpl.Template = pick_template(TPL)
page.Template = tpl
doc.recompute()

SCALE = auto_scale(max(DX, DY, DZ))
print("SCALE:", SCALE)

def add_view(name, direction, x, y, scale, label):
    v = doc.addObject("TechDraw::DrawViewPart", name)
    v.Source = [cell]
    v.Direction = direction
    v.X, v.Y, v.Scale = x, y, scale
    v.Label = label
    page.addView(v)
    doc.recompute()
    return v

front   = add_view("FrontView",   (0, -1, 0), 90, 180, SCALE, "Фронтальная проекция")
profile = add_view("ProfileView", (1, 0, 0),  250, 180, SCALE, "Профильная проекция")
iso     = add_view("IsometricView", (0.577, -0.577, 0.577), 170, 62,
                   SCALE * 0.8, "Изометрическая проекция")

# ---------- габариты (ГОСТ 2.307) ----------
def dim(name, view, kind, value, x, y):
    d = doc.addObject("TechDraw::DrawViewDimension", name)
    d.Type = kind
    d.References2D = [(view, "Edge0")]
    d.Arbitrary = True
    d.FormatSpec = str(value)
    d.X, d.Y = x, y
    page.addView(d)
    doc.recompute()
    return d

dim("DimWidthX",  front,   "DistanceX", fmt(DX), -38, 34)
dim("DimHeightZ", front,   "DistanceY", fmt(DZ),  40, 26)
dim("DimDepthY",  profile, "DistanceX", fmt(DY), -30, 34)

# ---------- центр тяжести (отметка ц.т.) ----------
# фронтальный вид: экранные оси = X (вправо), Z (вверх)
fx = (com.x - bb.Center.x) * front.Scale
fy = (com.z - bb.Center.z) * front.Scale
cm1 = doc.addObject("TechDraw::DrawViewAnnotation", "CoM_front")
cm1.Text = [u"\u2295"]
cm1.X, cm1.Y = float(front.X) + fx, float(front.Y) + fy
cm1.TextSize = 6.0
page.addView(cm1)

# профильный вид: экранные оси = Y (вправо), Z (вверх)
px = (com.y - bb.Center.y) * profile.Scale
py = (com.z - bb.Center.z) * profile.Scale
cm2 = doc.addObject("TechDraw::DrawViewAnnotation", "CoM_profile")
cm2.Text = [u"\u2295"]
cm2.X, cm2.Y = float(profile.X) + px, float(profile.Y) + py
cm2.TextSize = 6.0
page.addView(cm2)
doc.recompute()

# ---------- техтребования и заголовок ----------
tt = doc.addObject("TechDraw::DrawViewAnnotation", "TechReq")
tt.Text = [
    u"\u0420\u0430\u0437\u043c\u0435\u0440\u044b \u0432 \u043c\u0438\u043b\u043b\u0438\u043c\u0435\u0442\u0440\u0430\u0445.",
    u"\u041d\u0435\u0443\u043a\u0430\u0437\u0430\u043d\u043d\u044b\u0435 \u043f\u0440\u0435\u0434\u0435\u043b\u044c\u043d\u044b\u0435 "
    u"\u043e\u0442\u043a\u043b\u043e\u043d\u0435\u043d\u0438\u044f: \u00b1100\u00b10,5 \u043c\u043c.",
    u"\u0413\u0430\u0431\u0430\u0440\u0438\u0442\u044b: %.1f\u00d7%.1f\u00d7%.1f \u043c\u043c, V=%.1f \u043c\u043c\u00b3."
    % (DX, DY, DZ, vol),
    u"\u2295 \u2014 \u0446\u0435\u043d\u0442\u0440 \u0442\u044f\u0436\u0435\u0441\u0442\u0438 \u0434\u0435\u0442\u0430\u043b\u0438 "
    u"(%.1f, %.1f, %.1f)." % (com.x, com.y, com.z),
]
tt.X, tt.Y = 90, 22
page.addView(tt)

title = doc.addObject("TechDraw::DrawViewAnnotation", "MainTitle")
title.Text = [TITLE, u"\u041c\u0430\u0441\u0448\u0442\u0430\u0431 1:%.0f" % (1 / SCALE)]
title.X, title.Y = 90, 250
page.addView(title)

doc.recompute()
doc.saveAs(FCSTD_OUT)
print("DRAWING_OK:", FCSTD_OUT)

# ---------- экспорт ----------
base = os.path.splitext(os.path.basename(FCSTD_OUT))[0]

# DXF — headless (работает без GUI; открывается в LibreCAD)
try:
    import importDXF
    t = os.path.join(EXPDIR, base + ".dxf")
    importDXF.export([page], t)
    print("EXPORTED:", t)
except Exception as e:
    print("EXPORT_FAIL dxf", e)

# SVG/PDF — требуют GUI-модуль, поэтому запускаемся под xvfb-run
if os.environ.get("GOST_XVFB") == "1":
    try:
        import TechDrawGui
        for ext, fn in (("svg", "exportPageAsSvg"), ("pdf", "exportPageAsPdf")):
            t = os.path.join(EXPDIR, base + "." + ext)
            try:
                getattr(TechDrawGui, fn)(page, t)
                print("EXPORTED:", t)
            except Exception as e:
                print("EXPORT_FAIL", ext, e)
    except Exception as e:
        print("EXPORT_FAIL gui", e)
'''


def run(cmd: list, label: str, timeout: int = 240, cwd: str = str(PROJECT)):
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd)
    return r


def stage_generate(src: str, out_step: Path) -> bool:
    s = GEN_STEP.replace("__SRC__", repr(src))
    s = s.replace("__OUT__", repr(str(out_step)))
    s = s.replace("__PARAMS__", json.dumps(load_params()))
    sp = WORK / "_gen_step.py"
    sp.write_text(s)
    r = run(["freecadcmd", str(sp)], "gen")
    return "STEP_OK" in r.stdout


def stage_clean(in_step: Path, out_brep: Path) -> bool:
    tcl = CLEAN_TCL.format(step=str(in_step), brep=str(out_brep))
    tp = WORK / "_clean.tcl"
    tp.write_text(tcl)
    r = run([ASI, f"/runscript={tp}"], "clean", timeout=300)
    for line in r.stdout.splitlines():
        if line.startswith(("BEFORE", "AFTER", "sound", "CLEAN_OK")):
            print("   ", line)
    return "CLEAN_OK" in r.stdout and out_brep.exists()


def stage_draw(brep: Path, stem: str, cfg: dict) -> bool:
    step_out = FIG / f"{stem}_clean.step"
    fcstd_out = FIG / f"{stem}_GOST.FCStd"
    s = MAKE_DRAWING
    s = s.replace("__BREP__", repr(str(brep)))
    s = s.replace("__STEPOUT__", repr(str(step_out)))
    s = s.replace("__FCSTD_OUT__", repr(str(fcstd_out)))
    s = s.replace("__TPL__", repr(TPL_DIR))
    s = s.replace("__EXPDIR__", repr(str(FIG)))
    s = s.replace("__TITLE__", repr(stem))
    sp = WORK / "_draw.py"
    sp.write_text(s)

    # 1) основной проход headless: BREP → чистый STEP + страница TechDraw + DXF
    r = run(["freecadcmd", str(sp)], "draw", timeout=300)
    ok = "DRAWING_OK" in r.stdout
    for line in r.stdout.splitlines():
        if line.startswith(("METRICS", "COM", "SCALE", "CLEAN_STEP_OK", "DRAWING_OK",
                            "EXPORTED", "EXPORT_FAIL", "ERROR_")):
            print("   ", line)

    # 2) DXF для LibreCAD: реальные HLR-проекции (headless)
    dx = WORK / "_dxf.py"
    sdx = EXPORT_DXF
    sdx = sdx.replace("__BREP__", repr(str(brep)))
    sdx = sdx.replace("__DXF__", repr(str(FIG / f"{stem}_projections.dxf")))
    sdx = sdx.replace("__TITLE__", repr(stem))
    dx.write_text(sdx)
    rd = run(["freecadcmd", str(dx)], "dxf", timeout=300)
    for line in rd.stdout.splitlines():
        if line.startswith(("DXF_OK", "Error", "Traceback")):
            print("   ", line)

    # 3) GUI-сессия под xvfb: здесь реально работает HLR (в консоли виды пустые)
    if ok and shutil.which("xvfb-run"):
        run_gui_session(brep, stem, fcstd_out, cfg)
    return ok


def run_gui_session(brep: Path, stem: str, fcstd_out: Path, cfg: dict) -> bool:
    """Создаёт страницу TechDraw в GUI-сессии (xvfb), где работает HLR.

    FreeCAD GUI не завершается сам — запускаем в фоне, ждём файл-статус
    и глушим процесс. Таймаутов нет: ждём появления статуса, затем kill.
    """
    status = WORK / "_gui_status.txt"
    if status.exists():
        status.unlink()
    log = WORK / "_gui.log"
    ex = WORK / "_gui.py"
    ex.write_text(SCALER_SRC + "\n\n" +          # модуль скейлера — уровень модуля
                  GUI_SESSION.replace("__BREP__", repr(str(brep)))
                  .replace("__FCSTD__", repr(str(fcstd_out)))
                  .replace("__EXPDIR__", repr(str(FIG)))
                  .replace("__STEM__", repr(stem))
                  .replace("__CFG__", repr(cfg))
                  .replace("__DATE__", repr(time.strftime("%Y-%m-%d")))
                  .replace("__SCALER__", "")      # уже вставлен выше
                  .replace("__STATUS__", repr(str(status))))

    proc = subprocess.Popen(
        ["xvfb-run", "-a", "freecad", str(ex)],
        stdout=open(log, "w"), stderr=subprocess.STDOUT,
        start_new_session=True, cwd=str(PROJECT))

    ok = False
    for _ in range(600):                     # опрос 600 × 0.5 c = 5 мин
        if status.exists():
            ok = True
            break
        if proc.poll() is not None:
            break
        time.sleep(0.5)

    try:
        os.killpg(os.getpgid(proc.pid), 9)   # глушим GUI/Xvfb
    except Exception:
        pass
    proc.wait(timeout=30)

    if status.exists():
        for line in status.read_text().splitlines():
            if line.strip():
                print("   ", line)
    else:
        print("    ⚠️  GUI-сессия не дала статус — проекции могут быть пустыми")
        tail = log.read_text(errors="ignore")[-400:] if log.exists() else ""
        if tail.strip():
            print("    ", tail.replace("\n", " ")[:300])
    return ok


def discover_models(extra_dirs=None) -> list:
    """Ищет модели для обработки: *.step/*.stp/*.FCStd в каталогах проекта."""
    roots = [CAD, PROJECT / "hardware", PROJECT / "sim"]
    if extra_dirs:
        roots = [Path(d) if os.path.isabs(d) else PROJECT / d for d in extra_dirs]
    found = []
    for root in roots:
        if not root.exists():
            continue
        for pat in ("*.FCStd", "*.step", "*.stp", "*.STEP", "*.STP", "*.brep"):
            found.extend(sorted(root.rglob(pat)))
    # убираем служебные/сгенерированные файлы
    skip = ("_clean", "_raw", "_GOST", "figures", "archive_old", "elmer_installed")
    out = [f for f in found
           if not any(s in str(f) for s in skip) and f.suffix.lower() != ".fcbak"]
    return out


def build_model(src_path: Path, cfg: dict) -> Path | None:
    """Полный цикл для одной модели: STEP → очистка → чертёж. Возвращает BREP."""
    stem = src_path.stem
    print("\n" + "-" * 66)
    print(f"МОДЕЛЬ: {src_path.name}")
    print("-" * 66)

    if src_path.suffix.lower() in (".step", ".stp"):
        raw_step = src_path
    elif src_path.suffix.lower() == ".brep":
        raw_step = None
    else:
        raw_step = WORK / f"{stem}_raw.step"
        print(f"[1/3] Извлечение геометрии → STEP")
        if not stage_generate(str(src_path), raw_step):
            print("   ❌ не удалось сгенерировать STEP")
            return None

    if raw_step is not None:
        print(f"[2/3] Очистка в Analysis Situs: {raw_step.name}")
        brep = WORK / f"{stem}_clean.brep"
        if not stage_clean(raw_step, brep):
            print("   ❌ очистка не удалась")
            return None
    else:
        print("[2/3] BREP — очистка не требуется")
        brep = src_path

    print(f"[3/3] ГОСТ-чертёж (3 проекции, адаптивный масштаб)")
    ok = stage_draw(brep, stem, cfg)
    return brep if ok else None


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(
        description="STEP/FCStd → очистка Analysis Situs → ГОСТ-чёртеж (TechDraw)")
    ap.add_argument("inputs", nargs="*",
                    help="файлы .step/.stp/.FCStd/.brep (без аргументов — "
                         "автопоиск моделей в sim/cad и hardware/)")
    ap.add_argument("--dir", action="append", default=[],
                    help="каталог для автопоиска (можно указать несколько раз)")
    ap.add_argument("--all", action="store_true",
                    help="обработать все найденные модели")
    ap.add_argument("--title", help="заголовок чертежа")
    ap.add_argument("--scale", type=float,
                    help="фиксированный масштаб (напр. 1 или 0.5), "
                         "по умолчанию — автоподбор по листу")
    ap.add_argument("--no-dims", action="store_true", help="без габаритов")
    ap.add_argument("--no-center", action="store_true", help="без отметки ц.т.")
    ap.add_argument("--template", help="путь к SVG-шаблону TechDraw")
    ap.add_argument("--note", action="append", default=[],
                    help="своя строка техтребований (можно указать несколько раз)")
    ap.add_argument("--params", help="JSON-файл с настройками (перекрывает default)")
    args = ap.parse_args()

    print("=" * 66)
    print("ПАЙПЛАЙН: STEP → Analysis Situs (очистка) → ГОСТ-чертёж")
    print("=" * 66)

    overrides = {}
    if args.params:
        overrides.update(json.loads(Path(args.params).read_text()))
    if args.title:
        overrides["title"] = args.title
    if args.scale:
        overrides["autoscale"] = False
        overrides["fixed_scale"] = args.scale
    if args.template:
        overrides["template"] = args.template
    if args.no_dims:
        overrides["show_dims"] = False
    if args.no_center:
        overrides["show_center"] = False
        overrides["note_show_center"] = False
    if args.note:
        overrides["tech_notes"] = list(args.note)

    if args.inputs:
        models = []
        for a in args.inputs:
            p = Path(a) if os.path.isabs(a) else PROJECT / a
            if not p.exists():
                print("Не найден:", p)
                return 1
            models.append(p)
    else:
        models = discover_models(args.dir)
        if not args.all and len(models) > 1:
            print(f"Найдено моделей: {len(models)} (используйте --all для всех)")
            for m in models:
                print("   ", m.relative_to(PROJECT))
            return 1

    if not models:
        print("Модели не найдены. Укажите файл: gost_pipeline.py model.step")
        return 1

    ok = 0
    for m in models:
        cfg = make_cfg(m.stem, overrides)
        if build_model(m, cfg):
            ok += 1

    print("\n" + "=" * 66)
    print(f"ОБРАБОТАНО {ok}/{len(models)}")
    print("=" * 66)
    print("Результаты в", FIG)
    for f in sorted(FIG.iterdir()):
        print(f"  {f.name:44s} {f.stat().st_size:>10d} байт")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())