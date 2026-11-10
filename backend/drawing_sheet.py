"""Конструкторский чертёж (engineering drawing) по ГОСТ/ISO.

Порт лучших идей text-to-cad (eng_drawing): измеренные размеры, выноски
отверстий, центровые, скрытые линии, основная надпись, таблица изменений,
самопроверки («мерит пустую бумагу», «наслоение аннотаций»). Реализация
своя: рендерер matplotlib (без ezdxf), проекции из FreeCAD (HLR), ГОСТ-рамка
и основная надпись по ГОСТ 2.104. Улучшения относительно исходника:
проекция по ГОСТ 2.305 (первый угол) по умолчанию, масса из объёма,
SVG-вывод, авто-выноски отверстий из геометрии, русские поля штампа.
"""

from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.patches import Polygon as MplPolygon

import numpy as np

__all__ = ["Sheet", "View", "SHEET_SIZES", "load_geometry", "render"]

# ---------------------------------------------------------------------------
# Константы
# ---------------------------------------------------------------------------

SHEET_SIZES = {
    "A4": (297.0, 210.0),
    "A3": (420.0, 297.0),
    "A2": (594.0, 420.0),
    "A1": (841.0, 594.0),
    "A0": (1189.0, 841.0),
}

VIEW_DIRECTIONS = {
    "top": ((0.0, 0.0, 1.0), (0.0, 1.0, 0.0)),
    "bottom": ((0.0, 0.0, -1.0), (0.0, -1.0, 0.0)),
    "front": ((0.0, -1.0, 0.0), (0.0, 0.0, 1.0)),
    "back": ((0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
    "right": ((1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
    "left": ((-1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
    "iso": ((1.0, -1.0, 1.0), (0.0, 0.0, 1.0)),
}

PROJECTIONS = ("THIRD ANGLE", "FIRST ANGLE")

_MARGIN = 10.0  # внешняя рамка (ГОСТ: слева 20, прочие 5; упрощаем до 10)
_TITLE_W, _TITLE_H = 185.0, 55.0
_NOTE_HEIGHT = 2.5
_NOTE_INSET = 3.0
_DIM_ROW = 12.0
_MIN_TEXT_MM = 1.6
_STANDARD_SCALES = (1.0, 1 / 2, 1 / 2.5, 1 / 5, 1 / 10, 1 / 20, 1 / 50, 1 / 100, 1 / 200, 1 / 500, 1 / 1000)
_CHAR_WIDTH = 0.72


def _mm_to_pt(mm: float) -> float:
    return mm * 72.0 / 25.4


def _norm(v):
    v = np.asarray(v, dtype=float)
    n = np.linalg.norm(v)
    if n < 1e-12:
        return v
    return v / n


def _basis(name: str):
    d, up = VIEW_DIRECTIONS[name]
    d = _norm(d)
    up = _norm(up)
    r = _norm(np.cross(up, d))
    u = up
    return r, u, d


def _project_point(pt, name: str):
    r, u, _ = _basis(name)
    p = np.asarray(pt, dtype=float)
    return (float(np.dot(p, r)), float(np.dot(p, u)))


def _fmt(value: float) -> str:
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return text or "0"


def _tol_suffix(tol) -> str:
    if tol is None:
        return ""
    if isinstance(tol, (int, float)):
        tol = (abs(float(tol)), abs(float(tol)))
    if not tol:
        return ""
    plus, minus = tol
    if abs(plus - minus) < 1e-9:
        return f" \u00b1{_fmt(plus)}"
    return f" +{_fmt(plus)}/-{_fmt(minus)}"


def hole_callout_text(spec: dict, *, tol=None, fit: str | None = None) -> str:
    parts = []
    lead = f"{spec['count']}\u00d7 " if spec.get("count") else ""
    size = spec["thread"] if spec.get("thread") else f"\u2300{_fmt(spec['diameter'])}"
    size += _tol_suffix(tol) + (f" {fit}" if fit else "")
    if spec.get("thru"):
        size += " THRU"
    elif spec.get("depth") is not None:
        size += f" \u21a7{_fmt(spec['depth'])}"
    parts.append(lead + size)
    if spec.get("cbore"):
        d, depth = spec["cbore"]
        parts.append(f"CB \u2300{_fmt(d)} \u21a7{_fmt(depth)}")
    if spec.get("csk"):
        d, ang = spec["csk"]
        parts.append(f"CS \u2300{_fmt(d)} \u00d7 {_fmt(ang)}\u00b0")
    if spec.get("label"):
        parts.append(str(spec["label"]))
    return "   ".join(parts)


def _p3(p):
    values = tuple(float(v) for v in p)
    if len(values) == 2:
        values = (values[0], values[1], 0.0)
    if len(values) != 3:
        raise ValueError(f"expected a 2D or 3D point, got {p!r}")
    return values


def _offset(value):
    if value is None:
        return None
    try:
        offset = float(value)
    except (TypeError, ValueError) as exc:
        raise TypeError(f"offset must be a number of sheet millimetres, got {value!r}") from exc
    if not math.isfinite(offset):
        raise ValueError(f"offset must be finite, got {value!r}")
    return offset


def _positive(name, value, *, allow_none=False, integer=False):
    if value is None:
        if allow_none:
            return None
        raise ValueError(f"{name} is required")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise TypeError(f"{name} must be a number, got {value!r}") from exc
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"{name} must be a positive number, got {value!r}")
    return int(round(number)) if integer else number


def _pair(name, value):
    if value is None:
        return None
    if isinstance(value, (str, bytes)) or not isinstance(value, Iterable):
        raise TypeError(f"{name} must be a pair of numbers, got {value!r}")
    values = tuple(value)
    if len(values) != 2:
        raise ValueError(f"{name} must be a pair of numbers, got {value!r}")
    return (_positive(f"{name}[0]", values[0]), _positive(f"{name}[1]", values[1]))


def _tol(value):
    if value is None:
        return None
    if isinstance(value, bool):
        raise TypeError(f"tol must be a number or a (plus, minus) pair, got {value!r}")
    if isinstance(value, (int, float)):
        return (abs(float(value)), abs(float(value)))
    try:
        plus, minus = value
        return (abs(float(plus)), abs(float(minus)))
    except (TypeError, ValueError) as exc:
        raise TypeError(f"tol must be a number or a (plus, minus) pair, got {value!r}") from exc


def _scale_label(scale: float, digits: str = "g") -> str:
    if abs(scale - 1) < 1e-9:
        return "1:1"
    return f"1:{1 / scale:{digits}}" if scale < 1 else f"{scale:{digits}}:1"


# ---------------------------------------------------------------------------
# Загрузка геометрии через freecadcmd
# ---------------------------------------------------------------------------

_EXTRACT_SCRIPT = r'''
import os, sys, json, Part, FreeCAD as App, Import

import os
step = os.environ["DS_STEP"]
out_json = os.environ["DS_OUT"]
doc = FreeCAD.newDocument("x")
Import.insert(step, doc.Name)
doc.recompute()
shapes = [o.Shape for o in doc.Objects if hasattr(o, "Shape") and o.Shape and not o.Shape.isNull()]
# выбрать тело с наибольшим объёмом (основная деталь), отбросить вспомогательные
if not shapes:
    sh = Part.read(step)
else:
    try:
        sh = max(shapes, key=lambda s: abs(s.Volume) if hasattr(s, "Volume") else 0)
    except Exception:
        sh = shapes[0] if len(shapes) == 1 else Part.makeCompound(shapes)
bb = sh.BoundBox

circles = []
for e in sh.Edges:
    c = e.Curve
    if type(c).__name__ == "Circle":
        circles.append({"center": [c.Center.x, c.Center.y, c.Center.z],
                        "radius": float(c.Radius),
                        "normal": [float(c.Axis.x), float(c.Axis.y), float(c.Axis.z)]})

holes = []
for f in sh.Faces:
    s = f.Surface
    if type(s).__name__ == "Cylinder":
        holes.append({"center": [float(s.Center.x), float(s.Center.y), float(s.Center.z)],
                      "radius": float(s.Radius),
                      "axis": [float(s.Axis.x), float(s.Axis.y), float(s.Axis.z)]})

# рёбра: дискретизация + смежные грани для определения касательных
edges_out = []
for e in sh.Edges:
    pts = e.discretize(Deflection=0.05)
    # смежные грани
    tang = False
    faces = getattr(e, "Faces", None) or []
    if len(faces) >= 2:
        try:
            n0 = faces[0].normalAt(faces[0].CenterOfMass)
            n1 = faces[1].normalAt(faces[1].CenterOfMass)
            if abs(float(n0.dot(n1))) > 0.995:
                tang = True
        except Exception:
            tang = False
    edges_out.append({
        "pts": [[float(p.x), float(p.y), float(p.z)] for p in pts],
        "tangent": tang,
    })

# треугольники для теста видимости
verts, tris_idx = sh.tessellate(0.1)
tris = []
for t in tris_idx:
    tris.append([[float(verts[t[0]].x), float(verts[t[0]].y), float(verts[t[0]].z)],
                 [float(verts[t[1]].x), float(verts[t[1]].y), float(verts[t[1]].z)],
                 [float(verts[t[2]].x), float(verts[t[2]].y), float(verts[t[2]].z)]])

try:
    com = sh.CenterOfMass
except Exception:
    com = sh.BoundBox.Center
out = {
    "bbox": [float(bb.XLength), float(bb.YLength), float(bb.ZLength)],
    "volume": float(sh.Volume),
    "com": [float(com.x), float(com.y), float(com.z)],
    "circles": circles,
    "holes": holes,
    "edges": edges_out,
    "tris": tris,
}
json.dump(out, open(out_json, "w"))
'''


def load_geometry(step_path: str | Path) -> dict:
    """Загрузить STEP через freecadcmd и вернуть геометрию в виде словаря."""
    step_path = str(Path(step_path).resolve())
    freecad = shutil.which("freecadcmd") or "freecadcmd"
    with tempfile.TemporaryDirectory() as td:
        script = Path(td) / "extract.py"
        script.write_text(_EXTRACT_SCRIPT)
        out_json = Path(td) / "geom.json"
        env = dict(os.environ)
        env["DS_STEP"] = step_path
        env["DS_OUT"] = str(out_json)
        proc = subprocess.run(
            [freecad, str(script)],
            capture_output=True, text=True, timeout=300, env=env,
        )
        if proc.returncode != 0 or not out_json.exists():
            raise RuntimeError(f"freecadcmd failed: {proc.stderr[-500:]}")
        data = json.loads(out_json.read_text())
    # проекции для всех видов — в python
    data["_step"] = step_path
    data["views"] = {}
    for name in VIEW_DIRECTIONS:
        data["views"][name] = _project_geometry(data, name)
    return data


def _project_geometry(data: dict, name: str) -> dict:
    r, u, d = _basis(name)
    # рёбра -> полилинии 2D + видимость
    visible_lines = []
    hidden_lines = []
    tris = np.asarray(data["tris"], dtype=float)  # (T,3,3)
    # предвычисление
    origins_cache = None
    for edge in data["edges"]:
        if edge.get("tangent"):
            continue
        pts3 = np.asarray(edge["pts"], dtype=float)
        if len(pts3) < 2:
            continue
        # длина polyline
        seglens = np.linalg.norm(np.diff(pts3, axis=0), axis=1)
        total = float(seglens.sum())
        if not math.isfinite(total) or total <= 0 or total > 1e4:
            continue
        n = max(2, min(2000, int(math.ceil(total / 0.4)) + 1))
        # параметрические точки вдоль polyline
        # строим массив точек с равным шагом по длине
        cum = np.concatenate([[0.0], np.cumsum(seglens)])
        targets = np.linspace(0, total, n)
        samples3 = []
        for t in targets:
            # найти сегмент
            idx = int(np.searchsorted(cum, t, side="right") - 1)
            idx = min(max(idx, 0), len(pts3) - 2)
            seglen = seglens[idx] if seglens[idx] > 0 else 1.0
            frac = (t - cum[idx]) / seglen if seglen > 0 else 0.0
            frac = min(max(frac, 0.0), 1.0)
            pt = pts3[idx] + frac * (pts3[idx + 1] - pts3[idx])
            samples3.append(pt)
        samples3 = np.asarray(samples3)
        # видимость каждого сэмпла
        vis_flags = _occlusion(samples3, tris, d)
        # группируем в runs
        # проекция всех сэмплов
        proj = np.column_stack([samples3 @ r, samples3 @ u])
        # runs
        cur_vis = vis_flags[0]
        run_pts = [proj[0]]
        for i in range(1, len(samples3)):
            if vis_flags[i] == cur_vis:
                run_pts.append(proj[i])
            else:
                # завершить run
                if len(run_pts) >= 2:
                    (visible_lines if cur_vis else hidden_lines).append(
                        [ [float(p[0]), float(p[1])] for p in run_pts]
                    )
                cur_vis = vis_flags[i]
                run_pts = [proj[i - 1], proj[i]]  # соединить разрыв
        if len(run_pts) >= 2:
            (visible_lines if cur_vis else hidden_lines).append(
                [ [float(p[0]), float(p[1])] for p in run_pts]
            )
    # центровые: круги, чья нормаль примерно параллельна направлению взгляда
    center_marks = []
    for c in data["circles"]:
        cn = np.asarray(c["normal"], dtype=float)
        if abs(float(np.dot(cn, d))) > 0.95:
            center_marks.append({
                "center": [float(np.dot(np.asarray(c["center"]), r)),
                           float(np.dot(np.asarray(c["center"]), u))],
                "radius": c["radius"],
            })
    # отверстия, параллельные этому виду
    hole_list = []
    for h in data["holes"]:
        axv = np.asarray(h["axis"], dtype=float)
        if abs(float(np.dot(axv, d))) > 0.95:
            ctr = np.asarray(h["center"], dtype=float)
            hole_list.append({
                "center3": [float(x) for x in h["center"]],
                "center2": [float(np.dot(ctr, r)), float(np.dot(ctr, u))],
                "radius": h["radius"],
                "axis": [float(x) for x in h["axis"]],
            })
    # bbox проекции
    allpts = []
    for e in data["edges"]:
        if e.get("tangent"):
            continue
        for p in e["pts"]:
            pp = np.asarray(p, dtype=float)
            allpts.append([float(np.dot(pp, r)), float(np.dot(pp, u))])
    if allpts:
        arr = np.asarray(allpts)
        bbox2 = [float(arr[:, 0].min()), float(arr[:, 0].max()),
                 float(arr[:, 1].min()), float(arr[:, 1].max())]
    else:
        bbox2 = [0, 0, 0, 0]
    return {
        "visible": visible_lines,
        "hidden": hidden_lines,
        "center_marks": center_marks,
        "holes": hole_list,
        "bbox": bbox2,
    }


def _occlusion(samples: np.ndarray, tris: np.ndarray, d: np.ndarray) -> np.ndarray:
    """Для каждой точки samples (M,3) — скрыта ли она (True=hidden)."""
    M = len(samples)
    if M == 0:
        return np.zeros(0, dtype=bool)
    if len(tris) == 0:
        return np.zeros(M, dtype=bool)
    eps = 1e-5
    origins = samples + eps * d
    hit = np.zeros(M, dtype=bool)
    for tri in tris:
        v0, v1, v2 = tri[0], tri[1], tri[2]
        e1 = v1 - v0
        e2 = v2 - v0
        pvec = np.cross(d, e2)
        det = float(np.dot(e1, pvec))
        if abs(det) < 1e-12:
            continue
        inv = 1.0 / det
        tvec = origins - v0  # (M,3)
        u = np.einsum("ij,j->i", tvec, pvec) * inv
        qvec = np.cross(tvec, e1)
        v = np.einsum("ij,j->i", qvec, d) * inv
        t = np.einsum("ij,j->i", qvec, e2) * inv
        mask = (u >= -eps) & (u <= 1 + eps) & (v >= -eps) & (u + v <= 1 + eps) & (t > eps)
        hit |= mask
        if hit.all():
            break
    return hit


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class _Dim:
    kind: str
    p1: tuple[float, float, float]
    p2: tuple[float, float, float] | None
    offset: float | None
    text: str | None
    orientation: str | None
    radius: float = 0.0
    angle: float = 45.0
    tol: Any = None
    fit: str | None = None
    hole: dict | None = None


@dataclass
class View:
    sheet: "Sheet"
    geometry: dict
    name: str
    at: tuple[float, float]
    label: str | None = None
    hidden: bool = True
    centre_marks: bool = True
    scale: float | None = None
    _dims: list[_Dim] = field(default_factory=list)
    _overall: bool = False
    _arranged: tuple | None = None

    def project(self, pt):
        return _project_point(pt, self.name)

    def dim(self, p1, p2, *, offset=None, text=None, orientation=None,
            tol=None, fit=None) -> "View":
        if orientation not in (None, "h", "v"):
            raise ValueError(f"orientation must be 'h', 'v' or None, got {orientation!r}")
        self._dims.append(_Dim("linear", _p3(p1), _p3(p2), _offset(offset), text, orientation,
                               tol=_tol(tol), fit=fit))
        return self

    def hole(self, center, diameter, *, depth=None, thru=False,
             cbore=None, csk=None, thread=None, count=None, angle=45.0,
             tol=None, fit=None, label=None) -> "View":
        spec = {"diameter": _positive("diameter", diameter),
                "depth": _positive("depth", depth, allow_none=True),
                "thru": bool(thru),
                "cbore": _pair("cbore", cbore),
                "csk": _pair("csk", csk),
                "thread": None if thread is None else str(thread),
                "count": None if count is None else _positive("count", count, integer=True),
                "label": None if label is None else str(label)}
        if thru and depth is not None:
            raise ValueError("a hole is either THRU or has a depth, not both")
        self._dims.append(_Dim("hole", _p3(center), None, 0.0, None, None,
                               radius=spec["diameter"] / 2, angle=float(angle),
                               tol=_tol(tol), fit=fit, hole=spec))
        return self

    def diameter(self, center, radius, *, angle=45.0, text=None) -> "View":
        self._dims.append(_Dim("diameter", _p3(center), None, 0.0, text, None,
                               radius=_positive("radius", radius), angle=float(angle)))
        return self

    def radius(self, center, radius, *, angle=45.0, text=None) -> "View":
        self._dims.append(_Dim("radius", _p3(center), None, 0.0, text, None,
                               radius=_positive("radius", radius), angle=float(angle)))
        return self

    def angle(self, vertex, p1, p2, *, offset=14.0) -> "View":
        vertex, a, b = _p3(vertex), _p3(p1), _p3(p2)
        distance = _offset(offset)
        if distance is None or distance == 0.0:
            raise ValueError(f"angle offset must be a non-zero number of sheet millimetres, got {offset!r}")
        # проверка: ноги не коллинеарны и не нулевые
        u = tuple(x - y for x, y in zip(a, vertex))
        v = tuple(x - y for x, y in zip(b, vertex))
        nu = math.sqrt(sum(c * c for c in u))
        nv = math.sqrt(sum(c * c for c in v))
        if not (nu > 0 and nv > 0):
            raise ValueError("angle needs two legs of non-zero length")
        cosine = sum(c * d for c, d in zip(u, v)) / (nu * nv)
        degrees = math.degrees(math.acos(max(-1.0, min(1.0, cosine))))
        if degrees < 0.05 or degrees > 179.95:
            raise ValueError("angle legs are collinear")
        self._dims.append(_Dim("angle", vertex, a, distance, None, None, radius=0.0, angle=0.0,
                               hole={"p2": b}))
        return self

    def note(self, text, at, *, offset=(10.0, 10.0)) -> "View":
        try:
            dx, dy = (float(offset[0]), float(offset[1]))
        except (TypeError, ValueError, IndexError) as exc:
            raise TypeError(f"note offset must be an (x, y) pair of sheet millimetres, got {offset!r}") from exc
        if not (math.isfinite(dx) and math.isfinite(dy)):
            raise ValueError(f"note offset must be finite, got {offset!r}")
        self._dims.append(_Dim("note", _p3(at), None, 0.0, str(text), None, radius=dx, angle=dy))
        return self

    def overall(self) -> "View":
        self._overall = True
        return self

    @property
    def _scale(self) -> float:
        return self.scale if self.scale is not None else self.sheet.scale

    def _view_data(self) -> dict:
        return self.sheet.geometry["views"][self.name]


@dataclass
class Sheet:
    size: str = "A3"
    scale: float = 1.0
    title: str = "UNTITLED"
    part_number: str = ""
    material: str = ""
    revision: str = "A"
    author: str = ""
    units: str = "mm"
    projection: str = "FIRST ANGLE"
    notes: Sequence[str] = ()
    ink: str = "mono"
    text_height: float = 3.5
    general_tolerance: str = ""
    revisions: Sequence[tuple[str, str, str]] = ()
    geometry: dict = field(default_factory=dict)
    views: list[View] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def __post_init__(self):
        if self.size not in SHEET_SIZES:
            raise ValueError(f"unknown sheet size {self.size!r}; one of {sorted(SHEET_SIZES)}")
        if not (self.scale > 0):
            raise ValueError("scale must be positive")
        if self.projection not in PROJECTIONS:
            raise ValueError(f"unknown projection {self.projection!r}; one of {sorted(PROJECTIONS)}.")
        for name in ("notes", "revisions"):
            if isinstance(getattr(self, name), (str, bytes)):
                raise TypeError(f"{name} must be a sequence, not a string")
        self.notes = tuple(self.notes)
        self.revisions = tuple(tuple(row) for row in self.revisions)
        for row in self.revisions:
            if isinstance(row, (str, bytes)) or len(row) != 3:
                raise ValueError(f"each revision must be a (rev, date, description) triple, got {row!r}")
        # note width guard
        room = self.width - 2 * (_MARGIN + _NOTE_INSET)
        for line in self.note_lines():
            if _text_width(line, _NOTE_HEIGHT) > room:
                raise ValueError(f"note line too wide for sheet: {line!r}")

    @property
    def width(self) -> float:
        return SHEET_SIZES[self.size][0]

    @property
    def height(self) -> float:
        return SHEET_SIZES[self.size][1]

    def drawn_notes(self) -> list[str]:
        general = [f"TOLERANCES PER {self.general_tolerance} UNLESS OTHERWISE SPECIFIED."]
        return (general if self.general_tolerance else []) + list(self.notes)

    def note_lines(self) -> list[str]:
        notes = self.drawn_notes()
        return [f"{'NOTES:  ' if i == 0 else '        '}{i + 1}. {n}"
                for i, n in enumerate(notes)]

    def three_views(self, shape=None, *, gap=None, hidden=True, centre_marks=True,
                    iso=False) -> tuple:
        size = self.geometry.get("bbox", [100, 100, 10])
        L, W, H = [x * self.scale for x in size]
        if gap is None:
            gap = 24.0 + 2 * self.text_height + 16.0
        slots = _arrange_three_views(self, size, gap, self.scale, iso, self.projection)
        views = []
        for name, at, view_scale, label in slots:
            vhidden = hidden if name != "iso" else False
            vcm = centre_marks if name != "iso" else False
            v = self.view(self.geometry, name, at=at, label=label, hidden=vhidden,
                          centre_marks=vcm, scale=view_scale)
            views.append(v)
        for v in views:
            v._arranged = (list(size), gap, iso)
        return tuple(views) if len(views) > 1 else views[0]

    def view(self, geometry, name, *, at, label=None, hidden=True, centre_marks=True,
             scale=None) -> View:
        if name not in VIEW_DIRECTIONS:
            raise ValueError(f"unknown view {name!r}; one of {sorted(VIEW_DIRECTIONS)}")
        if scale is not None and not (scale > 0):
            raise ValueError("a view's scale must be positive")
        try:
            at = (float(at[0]), float(at[1]))
        except (TypeError, ValueError, IndexError) as exc:
            raise TypeError(f"view at= must be an (x, y) pair of sheet millimetres, got {at!r}") from exc
        if not (math.isfinite(at[0]) and math.isfinite(at[1])):
            raise ValueError(f"view at= must be finite sheet millimetres, got {at!r}")
        if geometry is not None and self.geometry == {}:
            self.geometry = geometry
        v = View(self, self.geometry, name, at, label, hidden, centre_marks, scale)
        self.views.append(v)
        return v

    def auto_holes(self):
        """Авто-выноски отверстий (цилиндрических) из геометрии."""
        seen = set()
        pts_all = np.asarray([e for edge in self.geometry["edges"] for e in edge["pts"]], dtype=float)
        for h in self.geometry.get("holes", []):
            key = (round(h["radius"], 3), tuple(round(x, 2) for x in h["center"]))
            if key in seen:
                continue
            seen.add(key)
            axis = np.asarray(h["axis"], dtype=float)
            center = np.asarray(h["center"], dtype=float)
            thru = False
            depth = None
            if len(pts_all) > 0:
                dists = np.linalg.norm(pts_all - (pts_all @ axis[:, None]) * axis[None, :], axis=1)
                near = pts_all[dists < h["radius"] + 0.5]
                if len(near) > 0:
                    span = float((near @ axis).max() - (near @ axis).min())
                    part_span = float((pts_all @ axis).max() - (pts_all @ axis).min())
                    if part_span > 0 and span >= 0.9 * part_span:
                        thru = True
                    else:
                        depth = round(span, 2) if span > 0 else None
            # подсчёт одинаковых отверстий
            count = sum(1 for hh in self.geometry["holes"]
                        if abs(hh["radius"] - h["radius"]) < 1e-6)
            # выбор вида: ось отверстия параллельна взгляду
            target_view = None
            for vname in ("top", "front", "right", "left", "back", "bottom"):
                r, u, d = _basis(vname)
                if abs(float(np.dot(axis, d))) > 0.95:
                    target_view = vname
                    break
            if target_view is None:
                target_view = "top"
            for v in self.views:
                if v.name == target_view:
                    v.hole(tuple(float(x) for x in h["center"]), h["radius"] * 2,
                           depth=depth, thru=thru, count=count if count > 1 else None)


def _arrange_three_views(sheet: Sheet, size, gap, s, iso, projection):
    L, W, H = [x * s for x in size]
    m = _MARGIN
    reach = 12.0 + 2 * sheet.text_height + 8.0
    usable_w = sheet.width - 2 * m - 2 * reach
    note_rows = len(sheet.notes) + (1 if sheet.general_tolerance else 0)
    notes_h = (note_rows * 5.0 + 8.0) if note_rows else 0.0
    usable_h = sheet.height - 2 * m - _TITLE_H - notes_h - reach - 12.0
    block_w = L + gap + W
    block_h = H + gap + W
    left = m + reach + max(0.0, (usable_w - block_w) / 2)
    bottom = m + _TITLE_H + notes_h + 12.0 + max(0.0, (usable_h - block_h) / 2)
    if projection == "FIRST ANGLE":
        front_at = (left + W + gap + L / 2, bottom + W + gap + H / 2)
        top_at = (front_at[0], bottom + W / 2)
        right_at = (left + W / 2, front_at[1])
    else:
        front_at = (left + L / 2, bottom + H / 2)
        top_at = (front_at[0], bottom + H + gap + W / 2)
        right_at = (left + L + gap + W / 2, front_at[1])
    slots = [("top", top_at, None, None), ("front", front_at, None, None),
             ("right", right_at, None, None)]
    if not iso:
        return slots
    iso_w, iso_h = _view_extent(sheet.geometry, "iso", 1.0)
    upper_at = top_at if projection == "THIRD ANGLE" else front_at
    upper_h = W if projection == "THIRD ANGLE" else H
    upper_bottom = upper_at[1] - upper_h / 2
    avail_w = sheet.width - m - 6 - (upper_at[0] + L / 2 + 30.0 + 8.0 + 13 * sheet.text_height * 0.7 + 8.0)
    table_h = (len(sheet.revisions) + 1) * 6.0 + 6.0 if sheet.revisions else 0.0
    avail_h = sheet.height - m - 6 - table_h - upper_bottom
    iso_scale = s * min(1.0, max(0.2, avail_w / iso_w if iso_w else 1.0),
                        max(0.2, avail_h / iso_h if iso_h else 1.0))
    iso_w2, iso_h2 = iso_w * iso_scale, iso_h * iso_scale
    iso_x = sheet.width - m - 6 - iso_w2 / 2
    iso_y = min(upper_bottom + iso_h2 / 2, sheet.height - m - 6 - table_h - iso_h2 / 2)
    label = "ISOMETRIC" if abs(iso_scale / s - 1) < 1e-6 else f"ISOMETRIC {_scale_label(iso_scale, '.3g')}"
    slots.append(("iso", (iso_x, iso_y), None if abs(iso_scale / s - 1) < 1e-6 else iso_scale, label))
    return slots


def _view_extent(geometry: dict, name: str, scale: float) -> tuple[float, float]:
    vd = geometry.get("views", {}).get(name, {})
    bb = vd.get("bbox", [0, 0, 0, 0])
    w = (bb[1] - bb[0]) * scale
    h = (bb[3] - bb[2]) * scale
    if w <= 0:
        w = 10.0
    if h <= 0:
        h = 10.0
    return w, h


def _text_width(text: str, height: float) -> float:
    # приблизительная ширина текста в мм
    return len(text) * height * _CHAR_WIDTH


# ---------------------------------------------------------------------------
# Рендеринг
# ---------------------------------------------------------------------------


def render(sheet: Sheet, out_pdf: str | Path, out_svg: str | Path | None = None):
    """Отрендерить лист в PDF (и SVG, если указан). Возвращает список предупреждений."""
    warnings = []
    # самопроверки: "мерит пустую бумагу"
    for v in sheet.views:
        vd = v._view_data()
        bb = vd["bbox"]  # xmin,xmax,ymin,ymax
        for d in v._dims:
            if d.kind in ("linear",):
                for pt in (d.p1, d.p2):
                    x, y = v.project(pt)
                    if not (bb[0] - 2 <= x <= bb[1] + 2 and bb[2] - 2 <= y <= bb[3] + 2):
                        warnings.append(f"view {v.name}: dimension point {pt} lands outside view geometry (measures blank paper)")
            if d.kind == "hole" and d.p1 is not None:
                x, y = v.project(d.p1)
                if not (bb[0] - 2 <= x <= bb[1] + 2 and bb[2] - 2 <= y <= bb[3] + 2):
                    warnings.append(f"view {v.name}: hole centre {d.p1} outside view geometry")
    # off-frame guard
    _check_frames(sheet, warnings)
    # рендер
    fig = _draw_sheet(sheet, warnings)
    out_pdf = Path(out_pdf)
    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_pdf, format="pdf", dpi=100, metadata={"CreationDate": None, "ModDate": None})
    if out_svg is not None:
        out_svg = Path(out_svg)
        out_svg.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(out_svg, format="svg", dpi=100)
    plt.close(fig)
    sheet.warnings = warnings
    for w in warnings:
        print(f"WARNING: {w}")
    return warnings


def _check_frames(sheet: Sheet, warnings: list):
    m = _MARGIN
    frame = (m, sheet.width - m, m, sheet.height - m)
    for v in sheet.views:
        vd = v._view_data()
        bb = vd["bbox"]
        sc = v._scale
        cx, cy = v.at
        half_w = (bb[1] - bb[0]) / 2 * sc
        half_h = (bb[3] - bb[2]) / 2 * sc
        box = (cx - half_w, cx + half_w, cy - half_h, cy + half_h)
        overflow = max(frame[0]-box[0], box[1]-frame[1], frame[2]-box[2], box[3]-frame[3])
        if overflow > 2.0:
            warnings.append(f"view {v.name} runs off the frame at scale {_scale_label(sheet.scale)}")
            # предложить масштаб
            fits = None
            for cand in _STANDARD_SCALES:
                if cand < sheet.scale:
                    fits = cand
                    break
            if fits is not None:
                warnings.append(f"  try Sheet(scale={fits:g}) ({_scale_label(fits)})")


def _draw_sheet(sheet: Sheet, warnings: list) -> plt.Figure:
    fig = plt.figure(figsize=(sheet.width / 25.4, sheet.height / 25.4), dpi=100)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, sheet.width)
    ax.set_ylim(0, sheet.height)
    ax.set_aspect("equal")
    ax.axis("off")
    # рамка
    _draw_frame(ax, sheet)
    # виды
    text_items = []  # (bbox, label) for overlap check
    for v in sheet.views:
        _draw_view(ax, sheet, v, text_items, warnings)
    # основная надпись
    _draw_title_block(ax, sheet)
    # таблица изменений
    _draw_revision_table(ax, sheet)
    # заметки
    _draw_notes(ax, sheet)
    # проверка наложения аннотаций
    _check_overlaps(text_items, warnings)
    return fig


def _draw_frame(ax, sheet):
    m = _MARGIN
    # внешняя рамка
    ax.plot([m, sheet.width - m, sheet.width - m, m, m],
            [m, m, sheet.height - m, sheet.height - m, m],
            color="black", lw=_mm_to_pt(0.5), zorder=1)
    # внутренняя рамка (поля)
    ax.plot([m + 5, sheet.width - m - 5, sheet.width - m - 5, m + 5, m + 5],
            [m + 5, m + 5, sheet.height - m - 5, sheet.height - m - 5, m + 5],
            color="black", lw=_mm_to_pt(0.25), zorder=1)
    # зоны (упрощённо: цифры сверху/снизу, буквы по бокам)
    for i in range(1, 9):
        x = m + (sheet.width - 2 * m) * i / 8.0
        ax.text(x, sheet.height - m + 1, str(i), ha="center", va="bottom",
                fontsize=_mm_to_pt(2.5) * 0.8, family="DejaVu Sans")
        ax.text(x, m - 1, str(i), ha="center", va="top",
                fontsize=_mm_to_pt(2.5) * 0.8, family="DejaVu Sans")
    for j, letter in enumerate("ABCD"):
        y = m + (sheet.height - 2 * m) * (j + 0.5) / 4.0
        ax.text(m - 1, y, letter, ha="right", va="center",
                fontsize=_mm_to_pt(2.5) * 0.8, family="DejaVu Sans")
        ax.text(sheet.width - m + 1, y, letter, ha="left", va="center",
                fontsize=_mm_to_pt(2.5) * 0.8, family="DejaVu Sans")


def _draw_view(ax, sheet, v: View, text_items, warnings):
    vd = v._view_data()
    sc = v._scale
    cx, cy = v.at
    # видимые линии
    for pl in vd["visible"]:
        pts = np.asarray(pl) * sc
        pts = pts + np.array([cx, cy]) - _center_offset(vd, sc)
        ax.plot(pts[:, 0], pts[:, 1], color="black", lw=_mm_to_pt(0.5), zorder=3)
    # скрытые
    if v.hidden:
        for pl in vd["hidden"]:
            pts = np.asarray(pl) * sc
            pts = pts + np.array([cx, cy]) - _center_offset(vd, sc)
            ax.plot(pts[:, 0], pts[:, 1], color="grey", lw=_mm_to_pt(0.25),
                    linestyle=(0, (4, 2)), zorder=3)
    # центровые
    if v.centre_marks:
        for cm in vd["center_marks"]:
            c = np.asarray(cm["center"]) * sc + (np.array([cx, cy]) - _center_offset(vd, sc))
            r = cm["radius"] * sc
            # крест
            L = max(3.0, r + 2.0)
            ax.plot([c[0] - L, c[0] + L], [c[1], c[1]], color="black", lw=_mm_to_pt(0.18), zorder=3)
            ax.plot([c[0], c[0]], [c[1] - L, c[1] + L], color="black", lw=_mm_to_pt(0.18), zorder=3)
            ax.plot(c[0] + r * np.cos(np.linspace(0, 2 * np.pi, 37)),
                    c[1] + r * np.sin(np.linspace(0, 2 * np.pi, 37)),
                    color="black", lw=_mm_to_pt(0.18), zorder=3)
    # подпись вида
    if v.label:
        ax.text(cx, cy - (_center_offset(vd, sc)[1] + 5), v.label, ha="center", va="top",
                fontsize=_mm_to_pt(sheet.text_height), family="DejaVu Sans", zorder=4)
    # имя вида (если не подпись)
    else:
        name_map = {"top": "Вид сверху", "front": "Вид спереди", "right": "Вид справа",
                    "left": "Вид слева", "back": "Вид сзади", "bottom": "Вид снизу",
                    "iso": "Аксонометрия"}
        lbl = name_map.get(v.name, v.name)
        ax.text(cx, cy - (_center_offset(vd, sc)[1] + 5), lbl, ha="center", va="top",
                fontsize=_mm_to_pt(sheet.text_height * 0.8), family="DejaVu Sans", zorder=4)
    # размеры
    _draw_dimensions(ax, sheet, v, text_items)
    # выноски отверстий авто
    _draw_hole_callouts(ax, sheet, v, vd, text_items)


def _center_offset(vd, sc):
    bb = vd["bbox"]
    cx = (bb[0] + bb[1]) / 2 * sc
    cy = (bb[2] + bb[3]) / 2 * sc
    return np.array([cx, cy])


def _draw_dimensions(ax, sheet, v: View, text_items):
    vd = v._view_data()
    sc = v._scale
    off0 = _center_offset(vd, sc)
    at = np.asarray(v.at, dtype=float)
    # rows: определим для каждого дима
    # сначала соберём все линейные димы + overall
    unplaced_h = [d for d in v._dims if d.kind == "linear" and d.offset is None and
                  (d.orientation == "h" or (d.orientation is None and abs(_project_point(d.p2, v.name)[0] - _project_point(d.p1, v.name)[0]) >= abs(_project_point(d.p2, v.name)[1] - _project_point(d.p1, v.name)[1])))]
    # упрощение: назначим ряды
    # overall: если включён, добавим два псевдо-дима (ширина/высота) на внешний ряд
    # row counter
    # горизонтальные (верх): ряды 12,24,...
    h_cursor = 12.0
    v_cursor = 12.0
    # сначала назначим ряды явным offset'ам как есть
    # для димов без offset назначим по порядку
    for d in v._dims:
        if d.kind != "linear":
            continue
        p1 = np.asarray(v.project(d.p1))
        p2 = np.asarray(v.project(d.p2))
        dx = abs(p2[0] - p1[0])
        dy = abs(p2[1] - p1[1])
        is_h = (d.orientation == "h") or (d.orientation is None and dx >= dy)
        if d.offset is None:
            if is_h:
                d.offset = h_cursor
                h_cursor += _DIM_ROW
            else:
                d.offset = v_cursor
                v_cursor += _DIM_ROW
    # overall: внешний ряд
    if v._overall:
        # ширина (горизонталь) — над видом
        # высота (вертикаль) — слева от вида
        pass
    # рисуем линейные димы
    for d in v._dims:
        if d.kind != "linear":
            continue
        p1 = np.asarray(v.project(d.p1))
        p2 = np.asarray(v.project(d.p2))
        _draw_linear_dim(ax, sheet, v, d, p1, p2, text_items)
    # overall
    if v._overall:
        vd = v._view_data()
        bb = vd["bbox"]
        sc = v._scale
        # проекция bbox в экранные коords
        xmin = bb[0] * sc + (at[0] - (bb[0]+bb[1])/2*sc)
        xmax = bb[1] * sc + (at[0] - (bb[0]+bb[1])/2*sc)
        ymin = bb[2] * sc + (at[1] - (bb[2]+bb[3])/2*sc)
        ymax = bb[3] * sc + (at[1] - (bb[2]+bb[3])/2*sc)
        # ширина над видом
        row = h_cursor + _DIM_ROW
        _draw_overall_dim(ax, sheet, "h", xmin, xmax, ymax, row, f"{_fmt((bb[1]-bb[0]))}", text_items)
        # высота слева
        rowv = v_cursor + _DIM_ROW
        _draw_overall_dim(ax, sheet, "v", ymin, ymax, xmin, rowv, f"{_fmt((bb[3]-bb[2]))}", text_items)
    # отверстия (выноски)
    for d in v._dims:
        if d.kind == "hole":
            _draw_hole_callout(ax, sheet, v, d, text_items)
        elif d.kind in ("diameter", "radius"):
            _draw_diameter(ax, sheet, v, d, text_items)
        elif d.kind == "note":
            _draw_note(ax, sheet, v, d, text_items)
        elif d.kind == "angle":
            pass


def _draw_linear_dim(ax, sheet, v, d, p1, p2, text_items):
    off = d.offset if d.offset is not None else _DIM_ROW
    dx = abs(p2[0] - p1[0])
    dy = abs(p2[1] - p1[1])
    is_h = (d.orientation == "h") or (d.orientation is None and dx >= dy)
    th = sheet.text_height
    if is_h:
        # горизонтальный: линия на y = max(y1,y2) + off (если off>0) 
        y_base = max(p1[1], p2[1])
        y_line = y_base + off
        x1, x2 = sorted([p1[0], p2[0]])
        # выносные
        for x in (p1[0], p2[0]):
            ax.plot([x, x], [max(p1[1], p2[1]) + (1 if off > 0 else -1), y_line + (2 if off > 0 else -2)],
                    color="black", lw=_mm_to_pt(0.18), zorder=4)
        # размерная линия + стрелки
        _dim_line_horizontal(ax, x1, x2, y_line, off)
        # текст
        meas = math.hypot(p2[0]-p1[0], p2[1]-p1[1]) if not is_h else abs(p2[0]-p1[0])
        # true model distance
        pp1 = np.asarray(d.p1); pp2 = np.asarray(d.p2)
        model_dist = float(np.linalg.norm(pp1 - pp2)) if d.orientation is None else (abs(pp2[0]-pp1[0]) if is_h else abs(pp2[1]-pp1[1]))
        # use projected length for h/v? measured value should be model distance along that axis
        if is_h:
            model_dist = abs(float(d.p2[0] - d.p1[0])) if False else abs(pp2[0]-pp1[0])
        else:
            model_dist = abs(pp2[1]-pp1[1])
        val = _fmt(model_dist / v._scale) if False else _fmt(model_dist)
        if d.text:
            val = d.text.replace("<>", val)
        val += _tol_suffix(d.tol)
        if d.fit:
            val += f" {d.fit}"
        txt = ax.text((x1 + x2) / 2, y_line + (1.5 if off > 0 else -1.5), val,
                      ha="center", va="bottom" if off > 0 else "top",
                      fontsize=_mm_to_pt(th), family="DejaVu Sans", zorder=5)
        if d.tol:
            txt.set_fontsize(_mm_to_pt(th * 0.85))
        text_items.append((txt, val))
    else:
        x_base = max(p1[0], p2[0]) if off > 0 else min(p1[0], p2[0])
        x_line = x_base + off
        y1, y2 = sorted([p1[1], p2[1]])
        for y in (p1[1], p2[1]):
            ax.plot([x_base + (1 if off > 0 else -1), x_line + (2 if off > 0 else -2)], [y, y],
                    color="black", lw=_mm_to_pt(0.18), zorder=4)
        _dim_line_vertical(ax, x_line, y1, y2, off)
        pp1 = np.asarray(d.p1); pp2 = np.asarray(d.p2)
        model_dist = abs(pp2[1] - pp1[1])
        val = _fmt(model_dist)
        if d.text:
            val = d.text.replace("<>", val)
        val += _tol_suffix(d.tol)
        if d.fit:
            val += f" {d.fit}"
        txt = ax.text(x_line + (1.5 if off > 0 else -1.5), (y1 + y2) / 2, val,
                      ha="left" if off > 0 else "right", va="center",
                      rotation=90, fontsize=_mm_to_pt(th), family="DejaVu Sans", zorder=5)
        text_items.append((txt, val))


def _dim_line_horizontal(ax, x1, x2, y, off):
    ax.plot([x1, x2], [y, y], color="black", lw=_mm_to_pt(0.18), zorder=4)
    # стрелки
    _arrowhead(ax, (x1, y), (1, 0) if x2 > x1 else (-1, 0), length=3.0)
    _arrowhead(ax, (x2, y), (-1, 0) if x2 > x1 else (1, 0), length=3.0)


def _dim_line_vertical(ax, x, y1, y2, off):
    ax.plot([x, x], [y1, y2], color="black", lw=_mm_to_pt(0.18), zorder=4)
    _arrowhead(ax, (x, y1), (0, 1) if y2 > y1 else (0, -1), length=3.0)
    _arrowhead(ax, (x, y2), (0, -1) if y2 > y1 else (0, 1), length=3.0)


def _arrowhead(ax, tip, direction, length=3.0):
    dx, dy = direction
    n = math.hypot(dx, dy) or 1.0
    dx, dy = dx / n, dy / n
    px, py = -dy, dx
    w = 1.0
    base = (tip[0] - dx * length, tip[1] - dy * length)
    poly = [(tip[0], tip[1]),
            (base[0] + px * w / 2, base[1] + py * w / 2),
            (base[0] - px * w / 2, base[1] - py * w / 2)]
    ax.add_patch(MplPolygon(poly, closed=True, facecolor="black", edgecolor="black", lw=0, zorder=5))


def _draw_overall_dim(ax, sheet, orient, a, b, perp, row, val, text_items):
    th = sheet.text_height
    if orient == "h":
        y = perp + row
        ax.plot([a, b], [y, y], color="black", lw=_mm_to_pt(0.18), zorder=4)
        _arrowhead(ax, (a, y), (1, 0), length=3.0)
        _arrowhead(ax, (b, y), (-1, 0), length=3.0)
        # выносные
        for x in (a, b):
            ax.plot([x, x], [perp + 1, y + 2], color="black", lw=_mm_to_pt(0.18), zorder=4)
        txt = ax.text((a + b) / 2, y + 1.5, val, ha="center", va="bottom",
                      fontsize=_mm_to_pt(th), family="DejaVu Sans", zorder=5)
        text_items.append((txt, val))
    else:
        x = perp - row
        ax.plot([x, x], [a, b], color="black", lw=_mm_to_pt(0.18), zorder=4)
        _arrowhead(ax, (x, a), (0, 1), length=3.0)
        _arrowhead(ax, (x, b), (0, -1), length=3.0)
        for y in (a, b):
            ax.plot([x - 2, perp - 1], [y, y], color="black", lw=_mm_to_pt(0.18), zorder=4)
        txt = ax.text(x - 1.5, (a + b) / 2, val, ha="right", va="center",
                      rotation=90, fontsize=_mm_to_pt(th), family="DejaVu Sans", zorder=5)
        text_items.append((txt, val))


def _draw_hole_callouts(ax, sheet, v, vd, text_items):
    sc = v._scale
    off0 = _center_offset(vd, sc)
    at = np.asarray(v.at, dtype=float)
    # авто: для каждого отверстия в этом виде добавить выноску, если его центр в bbox
    for h in vd.get("holes", []):
        c2 = np.asarray(h["center2"]) * sc + (at - off0)
        # проверим, что отверстие ещё не добавлено вручную? добавим всегда (может дублировать) — лучше только если нет ручных hole димов
        if any(d.kind == "hole" for d in v._dims):
            continue
        r = h["radius"] * sc
        # выноска: текст вверх-вправо
        txt_str = f"\u2300{_fmt(h['radius'] * 2)}"
        ax.plot([c2[0] + r * 0.7, c2[0] + r + 8], [c2[1] + r * 0.7, c2[1] + r + 6],
                color="black", lw=_mm_to_pt(0.18), zorder=4)
        txt = ax.text(c2[0] + r + 8, c2[1] + r + 6, txt_str, ha="left", va="bottom",
                      fontsize=_mm_to_pt(sheet.text_height), family="DejaVu Sans", zorder=5)
        text_items.append((txt, txt_str))


def _draw_hole_callout(ax, sheet, v, d, text_items):
    vd = v._view_data()
    sc = v._scale
    off0 = _center_offset(vd, sc)
    at = np.asarray(v.at, dtype=float)
    c2 = np.asarray(v.project(d.p1))  # уже в экранных координатах относительно? project возвращает model->view (без at/scale)
    # project возвращает координаты в системе вида (мм модели). Переводим в экранные:
    c_screen = c2 * sc + (at - off0)
    r = d.radius * sc
    spec = d.hole
    txt_str = hole_callout_text(spec, tol=d.tol, fit=d.fit)
    # угол выхода
    ang = math.radians(d.angle)
    dx = math.cos(ang)
    dy = math.sin(ang)
    knee = (c_screen[0] + (r + 4) * dx, c_screen[1] + (r + 4) * dy)
    end = (knee[0] + 12 * dx, knee[1] + 8 * dy)
    ax.plot([c_screen[0] + r * dx, knee[0]], [c_screen[1] + r * dy, knee[1]],
            color="black", lw=_mm_to_pt(0.18), zorder=4)
    ax.plot([knee[0], end[0]], [knee[1], end[1]], color="black", lw=_mm_to_pt(0.18), zorder=4)
    txt = ax.text(end[0] + 1 * (1 if dx >= 0 else -1), end[1], txt_str,
                  ha="left" if dx >= 0 else "right", va="center",
                  fontsize=_mm_to_pt(sheet.text_height), family="DejaVu Sans", zorder=5)
    text_items.append((txt, txt_str))


def _draw_diameter(ax, sheet, v, d, text_items):
    vd = v._view_data()
    sc = v._scale
    off0 = _center_offset(vd, sc)
    at = np.asarray(v.at, dtype=float)
    c2 = np.asarray(v.project(d.p1))
    c_screen = c2 * sc + (at - off0)
    r = d.radius * sc
    prefix = "\u2300" if d.kind == "diameter" else "R"
    val = d.text if d.text else f"{prefix}{_fmt(d.radius * 2 if d.kind == 'diameter' else d.radius)}"
    ang = math.radians(d.angle)
    dx = math.cos(ang)
    dy = math.sin(ang)
    end = (c_screen[0] + (r + 10) * dx, c_screen[1] + (r + 10) * dy)
    ax.plot([c_screen[0] + r * dx, end[0]], [c_screen[1] + r * dy, end[1]],
            color="black", lw=_mm_to_pt(0.18), zorder=4)
    txt = ax.text(end[0], end[1], val, ha="left" if dx >= 0 else "right", va="center",
                  fontsize=_mm_to_pt(sheet.text_height), family="DejaVu Sans", zorder=5)
    text_items.append((txt, val))


def _draw_note(ax, sheet, v, d, text_items):
    vd = v._view_data()
    sc = v._scale
    off0 = _center_offset(vd, sc)
    at = np.asarray(v.at, dtype=float)
    c2 = np.asarray(v.project(d.p1))
    c_screen = c2 * sc + (at - off0)
    dx, dy = d.radius, d.angle
    end = (c_screen[0] + dx, c_screen[1] + dy)
    ax.plot([c_screen[0], end[0]], [c_screen[1], end[1]], color="black", lw=_mm_to_pt(0.18), zorder=4)
    txt = ax.text(end[0], end[1], d.text, ha="left", va="bottom",
                  fontsize=_mm_to_pt(sheet.text_height), family="DejaVu Sans", zorder=5)
    text_items.append((txt, d.text))


def _draw_title_block(ax, sheet):
    # основная надпись ГОСТ 2.104 форма 1: 185x55, справа внизу
    x0 = sheet.width - _MARGIN - _TITLE_W
    y0 = _MARGIN
    x1 = x0 + _TITLE_W
    y1 = y0 + _TITLE_H
    # внешняя рамка
    ax.plot([x0, x1, x1, x0, x0], [y0, y0, y1, y1, y0], color="black", lw=_mm_to_pt(0.5), zorder=2)
    # левая часть 65, правая 120
    split = x0 + 65
    ax.plot([split, split], [y0, y1], color="black", lw=_mm_to_pt(0.25), zorder=2)
    # левая таблица: заголовок
    hdr = ["Изм.", "Лист", "№ докум.", "Подп.", "Дата"]
    widths = [12, 12, 20, 11, 10]
    cx = x0
    for h, w in zip(hdr, widths):
        ax.text(cx + w / 2, y1 - 5, h, ha="center", va="center",
                fontsize=_mm_to_pt(2.0), family="DejaVu Sans", zorder=3)
        cx += w
    # вертикальные линии левой части
    cx = x0
    for w in widths[:-1]:
        cx += w
        ax.plot([cx, cx], [y0, y1], color="black", lw=_mm_to_pt(0.18), zorder=2)
    # горизонтальная линия под заголовком
    ax.plot([x0, split], [y1 - 9, y1 - 9], color="black", lw=_mm_to_pt(0.18), zorder=2)
    # строки ролей
    roles = ["Разраб.", "Пров.", "Т.контр.", "Н.контр.", "Утв."]
    row_h = (y1 - 9 - y0) / len(roles)
    for i, role in enumerate(roles):
        yy = y1 - 9 - (i + 0.5) * row_h
        ax.text(x0 + 2, yy, role, ha="left", va="center",
                fontsize=_mm_to_pt(2.2), family="DejaVu Sans", zorder=3)
        # линия между строками
        yl = y1 - 9 - (i + 1) * row_h
        ax.plot([x0, split], [yl, yl], color="black", lw=_mm_to_pt(0.18), zorder=2)
    # правая часть
    rx0 = split
    rx1 = x1
    # строка 1: Лист / Листов / Литера
    ax.plot([rx0, rx1], [y1 - 9, y1 - 9], color="black", lw=_mm_to_pt(0.18), zorder=2)
    ax.text(rx0 + 5, y1 - 4.5, f"Лист {1}", ha="left", va="center", fontsize=_mm_to_pt(2.2), family="DejaVu Sans", zorder=3)
    ax.text(rx0 + 60, y1 - 4.5, f"Листов {1}", ha="left", va="center", fontsize=_mm_to_pt(2.2), family="DejaVu Sans", zorder=3)
    ax.text(rx0 + 95, y1 - 4.5, "Литера И", ha="left", va="center", fontsize=_mm_to_pt(2.2), family="DejaVu Sans", zorder=3)
    # строка 2-4: наименование (большой текст)
    name_y_top = y1 - 9
    name_y_bot = y0 + 26
    ax.plot([rx0, rx1], [name_y_bot, name_y_bot], color="black", lw=_mm_to_pt(0.18), zorder=2)
    ax.text((rx0 + rx1) / 2, (name_y_top + name_y_bot) / 2, sheet.title,
            ha="center", va="center", fontsize=_mm_to_pt(sheet.text_height + 1),
            family="DejaVu Sans", fontweight="bold", zorder=3)
    # строка: обозначение
    ax.plot([rx0, rx1], [name_y_bot - 8, name_y_bot - 8], color="black", lw=_mm_to_pt(0.18), zorder=2)
    ax.text(rx0 + 3, name_y_bot - 4, f"Обозначение: {sheet.part_number}", ha="left", va="center",
            fontsize=_mm_to_pt(2.2), family="DejaVu Sans", zorder=3)
    # строка: материал / масса
    mat_y = name_y_bot - 8
    mass = ""
    if sheet.geometry.get("volume"):
        # масса при плотности 2.7e-6 кг/мм3 по умолчанию (Al); если материал указан — всё равно считаем
        mass = f"{sheet.geometry['volume'] * 2.7e-6:.3f} кг"
    ax.plot([rx0, rx1], [mat_y - 8, mat_y - 8], color="black", lw=_mm_to_pt(0.18), zorder=2)
    ax.text(rx0 + 3, mat_y - 4, f"Материал: {sheet.material}", ha="left", va="center",
            fontsize=_mm_to_pt(2.2), family="DejaVu Sans", zorder=3)
    ax.text(rx0 + 65, mat_y - 4, f"Масса: {mass}" if mass else "Масса:", ha="left", va="center",
            fontsize=_mm_to_pt(2.2), family="DejaVu Sans", zorder=3)
    # нижняя строка: масштаб / проекция / организация
    ax.text(rx0 + 3, y0 + 4, f"Масштаб {_scale_label(sheet.scale)}", ha="left", va="center",
            fontsize=_mm_to_pt(2.2), family="DejaVu Sans", zorder=3)
    ax.text(rx0 + 45, y0 + 4, sheet.projection, ha="left", va="center",
            fontsize=_mm_to_pt(2.2), family="DejaVu Sans", zorder=3)
    ax.text(rx0 + 95, y0 + 4, "КБ Кибер НИИ", ha="left", va="center",
            fontsize=_mm_to_pt(2.2), family="DejaVu Sans", zorder=3)


def _draw_revision_table(ax, sheet):
    if not sheet.revisions:
        return
    # таблица изменений над основной надписью, справа
    x0 = sheet.width - _MARGIN - 145
    y0 = _MARGIN + _TITLE_H + 4
    cols = [20, 25, 70, 30]
    # заголовок
    headers = ["Изм.", "Дата", "Описание", "Подп."]
    cx = x0
    for h, w in zip(headers, cols):
        ax.text(cx + w / 2, y0 + 4, h, ha="center", va="center",
                fontsize=_mm_to_pt(2.0), family="DejaVu Sans", zorder=3)
        cx += w
    # рамка заголовка
    ax.plot([x0, x0 + sum(cols), x0 + sum(cols), x0, x0],
            [y0, y0, y0 + 8, y0 + 8, y0], color="black", lw=_mm_to_pt(0.25), zorder=2)
    cx = x0
    for w in cols[:-1]:
        cx += w
        ax.plot([cx, cx], [y0, y0 + 8 + 6 * len(sheet.revisions)], color="black", lw=_mm_to_pt(0.18), zorder=2)
    y = y0 + 8
    for rev, date, desc in sheet.revisions:
        ax.text(x0 + cols[0] / 2, y + 3, str(rev), ha="center", va="center", fontsize=_mm_to_pt(2.0), family="DejaVu Sans", zorder=3)
        ax.text(x0 + cols[0] + cols[1] / 2, y + 3, str(date), ha="center", va="center", fontsize=_mm_to_pt(2.0), family="DejaVu Sans", zorder=3)
        ax.text(x0 + cols[0] + cols[1] + 3, y + 3, str(desc), ha="left", va="center", fontsize=_mm_to_pt(2.0), family="DejaVu Sans", zorder=3)
        ax.plot([x0, x0 + sum(cols)], [y + 6, y + 6], color="black", lw=_mm_to_pt(0.18), zorder=2)
        y += 6


def _draw_notes(ax, sheet):
    x0 = _MARGIN
    y0 = _MARGIN
    y1 = y0 + _TITLE_H
    # область заметок слева от штампа
    x_right = sheet.width - _MARGIN - _TITLE_W - 5
    # заголовок
    ax.text(x0 + 3, y1 - 4, "ТЕХНИЧЕСКИЕ ТРЕБОВАНИЯ", ha="left", va="center",
            fontsize=_mm_to_pt(2.2), family="DejaVu Sans", fontweight="bold", zorder=3)
    y = y1 - 9
    for line in sheet.note_lines():
        ax.text(x0 + 3, y, line, ha="left", va="top", fontsize=_mm_to_pt(_NOTE_HEIGHT),
                family="DejaVu Sans", zorder=3)
        y -= 5.0
    # подпись масштаба/единиц
    ax.text(x0 + 3, y0 + 4, f"Размеры в {sheet.units}.", ha="left", va="center",
            fontsize=_mm_to_pt(2.0), family="DejaVu Sans", zorder=3)


def _check_overlaps(text_items, warnings):
    # text_items: list of (Text artist, label). Измеряем bbox через renderer не доступен без canvas;
    # используем приближение: каждый текст ~ (len*height*0.6, height) мм.
    boxes = []
    for txt, label in text_items:
        try:
            pos = txt.get_position()
            fs = txt.get_fontsize() * 25.4 / 72.0  # pt -> мм (приблиз)
            w = len(str(label)) * fs * 0.55
            h = fs * 1.2
            rot = txt.get_rotation()
            boxes.append((pos[0], pos[1], w, h, label))
        except Exception:
            continue
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            x1, y1, w1, h1, l1 = boxes[i]
            x2, y2, w2, h2, l2 = boxes[j]
            # AABB
            if (abs(x1 - x2) < (w1 + w2) / 2 + 1 and abs(y1 - y2) < (h1 + h2) / 2 + 1):
                warnings.append(f"annotation overlaps: {l1!r} and {l2!r}")


# ---------------------------------------------------------------------------
# Удобный вход
# ---------------------------------------------------------------------------


def _auto_feature_dims(v: "View") -> None:
    """Авто-размеры характерных элементов: ищем внутренние выравнивания
    (рёбра/ступеньки) в проекции и ставим размер от края до них."""
    vd = v._view_data()
    bb = vd["bbox"]
    sc = 1.0
    xmin, xmax = bb[0], bb[1]
    ymin, ymax = bb[2], bb[3]
    w = xmax - xmin
    h = ymax - ymin
    if w <= 0 or h <= 0:
        return
    # собрать все точки проекции
    pts = []
    for pl in vd["visible"]:
        for p in pl:
            pts.append(p)
    for pl in vd["hidden"]:
        for p in pl:
            pts.append(p)
    if not pts:
        return
    arr = np.asarray(pts, dtype=float)
    # внутренние x-координаты (строго внутри, с отступом)
    xs = arr[:, 0]
    ys = arr[:, 1]
    margin = 0.05
    inner_xs = [x for x in xs if xmin + margin * w < x < xmax - margin * w]
    inner_ys = [y for y in ys if ymin + margin * h < y < ymax - margin * h]
    # группировка по округлению до 0.5 мм
    def best_coord(coords):
        if not coords:
            return None
        groups: dict[float, int] = {}
        for c in coords:
            key = round(c * 2) / 2.0
            groups[key] = groups.get(key, 0) + 1
        # выбрать группу с max счётчиком, но не на самом краю
        best = max(groups.items(), key=lambda kv: kv[1])
        if best[1] < 3:
            return None
        return best[0]
    fx = best_coord(inner_xs)
    if fx is not None:
        # размер от xmin до fx, над видом (внутренний ряд)
        v.dim((xmin, ymin, 0), (fx, ymin, 0), orientation="h", offset=_DIM_ROW * 0.5)
    fy = best_coord(inner_ys)
    if fy is not None:
        v.dim((xmin, ymin, 0), (xmin, fy, 0), orientation="v", offset=_DIM_ROW * 0.5)


def _load_spec(step_path: str | Path) -> dict:
    p = Path(str(step_path) + ".dims.json")
    if not p.exists():
        p = Path(step_path).with_suffix(".dims.json")
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def make_sheet_from_step(step_path, *, title="", part_number="", material="", revision="A",
                        author="", projection="FIRST ANGLE", general_tolerance="",
                        notes=(), revisions=(), size="A3", scale=1.0, iso=False,
                        auto_holes=True, spec=None) -> Sheet:
    geom = load_geometry(step_path)
    if spec is None:
        spec = _load_spec(step_path)
    if spec.get("title"):
        title = spec["title"]
    if spec.get("part_number"):
        part_number = spec["part_number"]
    if spec.get("material"):
        material = spec["material"]
    if spec.get("revision"):
        revision = spec["revision"]
    if spec.get("general_tolerance"):
        general_tolerance = spec["general_tolerance"]
    if spec.get("notes"):
        notes = spec["notes"]
    if spec.get("revisions"):
        revisions = spec["revisions"]
    if spec.get("size"):
        size = spec["size"]
    if spec.get("scale"):
        scale = spec["scale"]
    sheet = Sheet(size=size, scale=scale, title=title or Path(step_path).stem,
                  part_number=part_number, material=material, revision=revision,
                  author=author, projection=projection, general_tolerance=general_tolerance,
                  notes=notes, revisions=revisions, geometry=geom)
    views = sheet.three_views(iso=iso)
    for v in views:
        if v.name != "iso":
            v.overall()
            _auto_feature_dims(v)
    # размеры из спецификации
    view_map = {v.name: v for v in views}
    for d in spec.get("dims", []):
        vname = d.get("view", "top")
        v = view_map.get(vname)
        if v is None:
            continue
        v.dim(tuple(d["p1"]), tuple(d["p2"]),
              orientation=d.get("orientation"),
              tol=d.get("tol"),
              fit=d.get("fit"),
              text=d.get("text"),
              offset=d.get("offset"))
    for h in spec.get("holes", []):
        vname = h.get("view", "top")
        v = view_map.get(vname)
        if v is None:
            continue
        v.hole(tuple(h["center"]), h["diameter"],
               depth=h.get("depth"), thru=bool(h.get("thru", False)),
               cbore=tuple(h["cbore"]) if h.get("cbore") else None,
               csk=tuple(h["csk"]) if h.get("csk") else None,
               thread=h.get("thread"), count=h.get("count"),
               tol=h.get("tol"), fit=h.get("fit"), label=h.get("label"))
    # авто-выноски отверстий
    if auto_holes and sheet.geometry.get("holes"):
        try:
            sheet.auto_holes()
        except Exception as e:
            print(f"auto_holes failed: {e}")
    return sheet
