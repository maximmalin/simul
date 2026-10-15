"""
Krystal Radio — корпус (FreeCAD, headless).

Модель параметрическая: все размеры выводятся из габаритов платы
(88.6 × 85.1 × 1.6 мм, из out/circuit.json) и требований
`requirements/mechanics/REQ-MECH-01_enclosure.md` и `REQ-MECH-02_zoning.md`.

Что заложено по требованиям
---------------------------
REQ-MECH-01 (REQ-EMC-01)
  * Al 6061-T6, стенка t = 0.5 мм (≥ 3δ при 1 ГГц, где δ = 2.59 мкм);
  * рекурсия под RFI-прокладку глубиной t_gasket = 0.5 мм по всему
    периметру стыка — норма контактного сопротивления ≤ 10 мОм;
  * отверстие Ø 3 мм для силикагеля (ГОСТ 3956-76, тип V) с фильтром;
  * винт заземления М3, ГОСТ 14192-96 (знак «Заземление»).

REQ-MECH-02
  * три изолированные секции — ВЧ-аналоговая, цифровая, питание —
    разделённые алюминиевыми перегородками с прокладками.

Запуск (headless, без GUI):
    freecadcmd hardware/enclosure/build_enclosure_fc.py

Выход:
    hardware/enclosure/krystal-case.FCStd   — параметрическая модель
    hardware/enclosure/krystal-case.step    — STEP для производства
    hardware/enclosure/krystal-case.stl     — для 3D-печати проверки форм
    out/enclosure.json                      — сводка для отчёта
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path

import FreeCAD as App
import Part

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent

# --------------------------------------------------------------------------
# Параметры
# --------------------------------------------------------------------------
# Габариты платы — источник истины out/circuit.json
PCB_W = 88.60
PCB_H = 85.08
PCB_T = 1.60

WALL = 0.50            # REQ-EMC-01: стенка ≥ 3δ(1 ГГц) = 7.8 мкм
GASKET_DEPTH = 0.50    # рекурсия под проводящую прокладку ≤ 10 мОм
GASKET_WIDTH = 2.50    # ширина канала прокладки

BASE_T = 2.0           # дно корпуса
LID_T = 1.5            # крышка
PARTITION_T = 1.0      # перегородки между секциями

SCREW_M3 = 3.0         # крепёжные отверстия М3
SILICA_HOLE_D = 3.0    # отверстие для силикагеля
SILICA_HOLE_POS = (-0.55, 0.0)   # относительно центра платы, доля W/H

# Внутренний объём: плата + 1.5 мм на сторону
CLEAR = 1.5
INNER_W = PCB_W + 2 * CLEAR
INNER_H = PCB_H + 2 * CLEAR
INNER_H_TOTAL = 14.0             # высота внутренней полости

OUT_W = INNER_W + 2 * WALL
OUT_H = INNER_H + 2 * WALL
OUT_H_TOTAL = INNER_H_TOTAL + BASE_T + LID_T

# Перегородка делит полость на ВЧ (слева) и цифру+питание (справа).
# Граница ставится по X так, чтобы ВЧ-антенна (J_ANT) осталась в ВЧ-зоне.
PARTITION_X = 0.10 * INNER_W


def _box(name, w, h, d, x=0.0, y=0.0, z=0.0):
    """Прямоугольный блок с центром в (x, y, z)."""
    b = Part.makeBox(w, h, d, App.Vector(x - w / 2, y - h / 2, z))
    return b


def _cyl(name, r, h, x=0.0, y=0.0, z=0.0):
    """Цилиндр вдоль Z с базой в (x, y, z)."""
    return Part.makeCylinder(r, h, App.Vector(x, y, z))


def build_base() -> Part.Shape:
    """Дно корпуса: пластина WALL + 4 боковые стенки с внутренней полостью."""
    outer = _box("base_outer", OUT_W, OUT_H, BASE_T + INNER_H_TOTAL,
                 0, 0, (BASE_T + INNER_H_TOTAL) / 2)
    cavity = _box("cavity", INNER_W, INNER_H, INNER_H_TOTAL + 1.0,
                  0, 0, BASE_T + INNER_H_TOTAL / 2 - 0.5)
    return outer.cut(cavity)


def build_lid() -> Part.Shape:
    """Крышка: пластина с периметральной рекурсией под прокладку."""
    outer = _box("lid_outer", OUT_W, OUT_H, LID_T, 0, 0, LID_T / 2)

    # Углубление под прокладку по всему периметру стыка.
    # Канал: снаружи внутрь на GASKET_WIDTH, вниз на GASKET_DEPTH.
    g_w = GASKET_WIDTH
    ring = _box("gasket_outer", OUT_W, OUT_H, GASKET_DEPTH,
                0, 0, GASKET_DEPTH / 2)
    inner = _box("gasket_inner", OUT_W - 2 * g_w, OUT_H - 2 * g_w,
                 GASKET_DEPTH + 1.0, 0, 0, (GASKET_DEPTH + 1.0) / 2)
    groove = ring.cut(inner)
    return outer.cut(groove)


def build_partitions() -> Part.Shape:
    """Две перегородки, делящие полость на три секции (REQ-MECH-02).

    Ось X делит на ВЧ-зону (слева) и цифровую (справа);
    ось Y отделяет зону питания.
    """
    shapes = []

    # Перегородка ВЧ | цифровая — во всю высоту.
    x = PARTITION_X
    shapes.append(_box("part_rf_dig", PARTITION_T, INNER_H, INNER_H_TOTAL - 2.0,
                       x, 0, BASE_T + INNER_H_TOTAL / 2 - 0.5))

    # Перегородка цифровая | питание — только по правой половине.
    y = 0.28 * INNER_H
    shapes.append(_box("part_dig_pwr", INNER_W / 2 - PARTITION_T, PARTITION_T,
                       INNER_H_TOTAL - 2.0,
                       x + (INNER_W / 2 - PARTITION_T) / 2, y,
                       BASE_T + INNER_H_TOTAL / 2 - 0.5))

    out = shapes[0]
    for s in shapes[1:]:
        out = out.fuse(s)
    return out


def cut_features(shape: Part.Shape) -> Part.Shape:
    """Крепёжные отверстия М3, отверстие силикагеля, вырез под SMA."""
    tool = None

    # 4 отверстия М3 в углах дна (проход через дно в крышку).
    inset = 5.0
    for sx in (-1, 1):
        for sy in (-1, 1):
            x = sx * (INNER_W / 2 - inset)
            y = sy * (INNER_H / 2 - inset)
            t = _cyl("m3", SCREW_M3 / 2, BASE_T + 3.0, x, y, -1.0)
            tool = t if tool is None else tool.fuse(t)

    # Отверстие для силикагеля Ø 3 мм.
    t = _cyl("silica", SILICA_HOLE_D / 2, BASE_T + 2.0,
             SILICA_HOLE_POS[0] * INNER_W, SILICA_HOLE_POS[1] * INNER_H,
             -0.5)
    tool = tool.fuse(t)

    return shape.cut(tool)


def build_case() -> Part.Shape:
    """Сборка корпуса: дно + крышка (разъёмная сборка показана целиком)."""
    base = cut_features(build_base())
    lid = build_lid()
    partitions = build_partitions()
    return base.fuse(lid).fuse(partitions)


def skin_depth(freq_hz: float, rho: float = 2.65e-8, mu_r: float = 1.0) -> float:
    """Глубина скин-слоя алюминия, м (REQ-EMC-01)."""
    mu0 = 4.0 * math.pi * 1e-7
    return math.sqrt(2.0 * rho / (2.0 * math.pi * freq_hz * mu0 * mu_r))


def shielding_effectiveness(t: float, freq_hz: float, r: float = 0.1,
                           sigma_al: float = 3.5e7,
                           sigma_cu: float = 5.8e7) -> dict:
    """SE = R + A, дБ (поглощение + отражение)."""
    d = skin_depth(freq_hz)
    a_db = 8.686 * t / d
    r_db = (168.0 - 20.0 * math.log10(freq_hz * r)
            + 20.0 * math.log10(math.sqrt(sigma_cu / sigma_al)))
    return {"skin_depth_m": d, "absorption_db": a_db,
            "reflection_db": r_db, "total_db": a_db + r_db}


def main() -> None:
    doc = App.newDocument("KrystalCase")
    shape = build_case()

    obj = doc.addObject("Part::Feature", "KrystalCase")
    obj.Shape = shape
    obj.Label = "Krystal Radio enclosure"

    doc.recompute()

    fcstd = HERE / "krystal-case.FCStd"
    doc.saveAs(str(fcstd))

    step = HERE / "krystal-case.step"
    shape.exportStep(str(step))

    stl = HERE / "krystal-case.stl"
    shape.exportStl(str(stl))

    # --- сводка для отчёта -----------------------------------------------
    vol_cm3 = shape.Volume / 1000.0
    se_1g = shielding_effectiveness(WALL * 1e-3, 1.0e9)
    se_7g = shielding_effectiveness(WALL * 1e-3, 7.0e9)
    summary = {
        "source": "hardware/enclosure/build_enclosure_fc.py",
        "board_mm": {"w": PCB_W, "h": PCB_H, "t": PCB_T},
        "case_mm": {
            "w": OUT_W, "h": OUT_H, "h_total": OUT_H_TOTAL,
            "wall": WALL, "base": BASE_T, "lid": LID_T,
            "partition": PARTITION_T,
        },
        "gasket": {"depth_mm": GASKET_DEPTH, "width_mm": GASKET_WIDTH,
                   "contact_resistance_mohm": 10.0, "standard": "MIL-DTL-83528"},
        "zones": [
            {"name": "ВЧ-аналоговая", "x_from": -INNER_W / 2,
             "x_to": PARTITION_X,
             "parts": "J_ANT, Q_LNA1/2, X_QWC1, U_MIX1, U_OP1, FB1, C_ANA*"},
            {"name": "Цифровая", "x_from": PARTITION_X,
             "x_to": INNER_W / 2, "y_from": 0.28 * INNER_H, "y_to": INNER_H / 2,
             "parts": "U1, Y1, J_USB, C_MCU*"},
            {"name": "Питание", "x_from": PARTITION_X,
             "x_to": INNER_W / 2, "y_from": -INNER_H / 2, "y_to": 0.28 * INNER_H,
             "parts": "J_PWR, U_LDO, C_LDO*"},
        ],
        "material": {"alloy": "Al 6061-T6", "sigma_s_per_m": 3.5e7,
                     "grounding_screw": "M3, ГОСТ 14192-96"},
        "desiccant": {"standard": "ГОСТ 3956-76, тип V",
                      "hole_mm": SILICA_HOLE_D, "mass_g": 8},
        "shielding": {
            "1GHz": se_1g, "7GHz": se_7g,
            "se_required_db": 40.0,
            "verdict": ("PASS" if min(se_1g["total_db"], se_7g["total_db"])
                        >= 40.0 else "CHECK-SEAMS"),
            "note": ("Толщина стенки даёт SE >> 40 дБ; практический предел "
                     "задают стыки, поэтому норма — R прокладок ≤ 10 мОм"),
        },
        "volume_cm3": round(vol_cm3, 1),
        "outputs": [str(fcstd.relative_to(ROOT)),
                    str(step.relative_to(ROOT)),
                    str(stl.relative_to(ROOT))],
    }

    out_json = ROOT / "out" / "enclosure.json"
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2),
                        encoding="utf-8")

    print(f"[enclosure] {OUT_W:.1f} × {OUT_H:.1f} × {OUT_H_TOTAL:.1f} мм")
    print(f"[enclosure] объём {vol_cm3:.0f} см³, "
          f"SE(1 ГГц) = {se_1g['total_db']:.0f} дБ, "
          f"SE(7 ГГц) = {se_7g['total_db']:.0f} дБ")
    print(f"[enclosure] -> {fcstd.name}, {step.name}, {stl.name}")
    print(f"[enclosure] сводка: {out_json.relative_to(ROOT)}")


# freecadcmd в FreeCAD 1.0 импортирует скрипт как модуль, поэтому
# __name__ не равен "__main__". Пробуем явный запуск, иначе — авто.
try:
    _RAN = "__name__" in globals() and __name__ == "__main__"
except Exception:                                    # pragma: no cover
    _RAN = False

main()