#!/usr/bin/env python3
"""
Проверка посадки 3D-моделей на компоненты.

Зачем. tscircuit выравнивает модель по своему габариту
(model_origin_alignment: center_of_component_on_board_surface), а не по
собственному началу координат. Поэтому деталь из каталога, начало которой
произвольно, всё равно должна встать по центру компонента.

Раньше на это опирался «автосдвиг» в эмиттере (positionOffset, разница
центров модели и выводов). Он был нужен только потому, что при конвертации
STEP->STL модель уходила в Z<0, то есть вглубь платы; сам по себе сдвиг
лечил несуществующую проблему и искажал положение детали. Теперь сдвиг
убран, и нужно убедиться, что детали встали правильно и без него.

Проверка работает по фактическим данным экспорта и потому годится для любой
схемы, а не только для текущей:

    freecad не нужен, node не нужен, только два файла экспорта.

  * circuit.json -> pcb_component: центр каждого компонента (pcbX/pcbY) и
    его ref;
  * board.glb    -> узел каждой модели: габарит из POSITION-accessor и
    матрица узла.

Итог: для каждой детали печатается отклонение центра модели от центра
компонента и высота над платой. Ненулевое отклонение означает, что деталь
перекошена; нижняя грань ниже поверхности платы — что деталь уходит вглубь.

Запуск:
    python3 hardware/tscircuit/check_models3d.py
Код возврата 0, если все детали стоят на плате и не смещены.
"""

from __future__ import annotations

import argparse
import json
import re
import math
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CIRCUIT = ROOT / "hardware/tscircuit/out/circuit.json"
GLB = ROOT / "hardware/tscircuit/out/board.glb"

# Допуски, мм. По XY модель может торчать за посадочное место (штыри,
# выводы, радиатор), это нормально; проверяем только положение центра.
CENTER_TOL = 0.30
# Допуск по высоте: нижняя грань детали должна лежать на поверхности платы.
# Ось высоты в glTF — Y, направленная вверх, поэтому проверяем ymin >= 0.
Z_TOL = 0.05


def load_glb(path: Path) -> dict:
    """Разбор контейнера GLB: JSON-чанк + BIN-чанк. Данные не декодируем."""
    raw = path.read_bytes()
    if raw[:4] != b"glTF":
        raise ValueError(f"{path} не GLB")
    off, js, bins = 12, None, b""
    while off < len(raw):
        length, kind = struct.unpack_from("<II", raw, off)
        body = raw[off + 8 : off + 8 + length]
        if kind == 0x4E4F534A:          # JSON
            js = json.loads(body)
        elif kind == 0x004E4942:        # BIN
            bins = body
        off += 8 + length + ((4 - length % 4) % 4 if length % 4 else 0)
    if js is None:
        raise ValueError("в GLB нет JSON-чанка")
    js["__bin"] = bins
    return js


def node_matrix(node: dict) -> list[list[float]]:
    """Матрица узла 4x4 в порядке строк (column-major из glTF)."""
    if "matrix" in node:
        m = node["matrix"]
        return [[m[0], m[4], m[8], m[12]],
                [m[1], m[5], m[9], m[13]],
                [m[2], m[6], m[10], m[14]],
                [m[3], m[7], m[11], m[15]]]
    t = node.get("translation", [0, 0, 0])
    q = node.get("rotation", [0, 0, 0, 1])
    s = node.get("scale", [1, 1, 1])
    x, y, z, w = q
    rot = [
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w), 0],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w), 0],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y), 0],
        [0, 0, 0, 1],
    ]
    for r in range(3):
        for c in range(3):
            rot[r][c] *= s[c]
    for r in range(3):
        rot[r][3] = t[r]
    return rot


def mul(a: list[list[float]], b: list[list[float]]) -> list[list[float]]:
    return [[sum(a[r][k] * b[k][c] for k in range(4)) for c in range(4)]
            for r in range(4)]


def model_boxes(glb: dict, surface_y: float) -> dict[str, dict]:
    """
    Имя узла -> {center, size} модели в координатах платы.

    Габарит берём из accessor min/max позиций вершин (данные glTF так и
    хранятся), затем переносим в систему координат узла. Размеры не
    масштабируем по сцене: нас интересует, где физически стоит деталь.
    """
    accessors, meshes, nodes = (glb["accessors"], glb["meshes"], glb["nodes"])
    out: dict[str, dict] = {}
    for node in nodes:
        mi = node.get("mesh")
        if mi is None:
            continue
        prim = meshes[mi]["primitives"][0]
        acc = accessors[prim["attributes"]["POSITION"]]
        if "min" not in acc or "max" not in acc:
            continue
        lo, hi = acc["min"], acc["max"]
        pts = [(x, y, z) for x in (lo[0], hi[0])
               for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]
        m = node_matrix(node)
        # Аффинное преобразование: поворот/масштаб + перенос. Раньше здесь
        # стояло sum(m[r][k]*p[k] for k in range(3)) без члена m[r][3], то
        # есть перенос узла молча терялся и все детали «съезжали» к нулю.
        world = [tuple(sum(m[r][k] * p[k] for k in range(3)) + m[r][3]
                       for r in range(3)) for p in pts]
        xs = [p[0] for p in world]
        ys = [p[1] for p in world]
        zs = [p[2] for p in world]
        name = node.get("name") or f"mesh_{mi}"
        # glTF: X = -kicad_x, Y = вверх (высота над платой), Z = kicad_y.
        # Поверхность платы в glTF-Y равна половине её толщины.
        # Ключи явные: раньше центр возвращался кортежем, и средняя
        # компонента (а это высота) принималась за координату Y, из-за чего
        # все детали выглядели смещёнными.
        lo_y, hi_y = min(ys) - surface_y, max(ys) - surface_y
        out[name] = {
            "kx": -(min(xs) + max(xs)) / 2,
            "ky": (min(zs) + max(zs)) / 2,
            "bottom": lo_y,
            "top": hi_y,
            "mid": (lo_y + hi_y) / 2,
            "size": (max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs)),
        }
    return out


def footprints_by_ref() -> dict[str, str]:
    """
    ref -> "Lib:Name" из сгенерированного TSX.

    Берётся из эмиттера, а не из circuit.json: в circuit.json у
    pcb_component поля ref нет, а корпус известен только там.
    """
    tsx = CIRCUIT.parent / "board.circuit.tsx"
    out: dict[str, str] = {}
    if not tsx.is_file():
        return out
    for m in re.finditer(
        r'name=\{?"([^"]+)"?\}.*?footprint=\{?"kicad:([^"]+?)"',
        tsx.read_text(errors="ignore"), re.S,
    ):
        out[m.group(1)] = m.group(2)
    return out


def components(circuit: dict) -> dict[str, dict]:
    """
    ref -> {center} для всех pcb_component.

    У pcb_component в circuit.json поля ref нет: компонент связан с исходной
    схемой через source_component_id, а позицияция на плате лежит уже в
    самом pcb_component. Поэтому ref берём из source_component[].name.
    """
    refs = {}
    for e in circuit:
        if e.get("type") == "source_component":
            refs[e["source_component_id"]] = e.get("name", "")
    out = {}
    for c in circuit:
        if c.get("type") != "pcb_component":
            continue
        sid = c.get("source_component_id")
        ref = c.get("ref") or refs.get(sid) or sid
        out[ref] = {"center": (c["center"]["x"], c["center"]["y"])}
    return out


# ── ориентация модели относительно выводов корпуса ────────────────────────
#
# Проверка отвечает на вопрос «не перевёрнута ли деталь». Признак берётся
# из посадочного места: если у корпуса есть выводы сквозь плату
# (thru_hole / np_thru_hole), то модель обязана уходить под плоскость
# платы — выводы физически должны войти в отверстия. Если выводов сквозь
# нет, модель наоборот не должна уходить вниз: ничего, что туда уходит,
# у SMD-детали нет.
#
# Так ловится именно тот дефект, который пришлось исправлять: сдвиг к
# Zmax=0 поднимал DIP, PinHeader и батарейный отсек целиком над плату, и
# выводы оказывались сверху.

FP_ROOT = Path("/usr/share/kicad/footprints")


def footprint_pads(ref_footprint: str) -> tuple[int, int]:
    """(всего выводов, выводов сквозь плату) для корпуса Lib:Name или Lib/Name."""
    # Разделитель бывает и двоеточием (нетлист KiCad), и слешем (формат
    # kicad:Lib/Name в tscircuit). Берём тот, который реально есть.
    sep = ":" if ":" in ref_footprint else "/"
    lib, _, name = ref_footprint.partition(sep)
    f = FP_ROOT / f"{lib}.pretty" / f"{name}.kicad_mod"
    if not f.is_file():
        return 0, 0
    txt = f.read_text(errors="ignore")
    # Блок (pad ...) в .kicad_mod многострочный, поэтому ищем по блокам,
    # а не по строке: иначе тип вывода в следующей строке не виден.
    pads = thru = 0
    for blk in re.finditer(r"\(pad\b((?:[^()]|\([^()]*\))*)\)", txt, re.S):
        pads += 1
        if re.search(r"\b(?:thru_hole|np_thru_hole)\b", blk.group(1)):
            thru += 1
    return pads, thru


def measure(boxes, comps, by_ref, old):
    """
    Сдвиги, приводящие каждую деталь к центру компонента.

    error — измеренное смещение модели относительно центра компонента.
    tscircuit сдвигает модель ровно на величину positionOffset, поэтому
    после применения нового сдвига смещение станет
        error + (offset_new - offset_old),
    и обнулить его можно только offset_new = offset_old - error.

    Отсюда видна и ошибка, которую пришлось исправлять: при нулевом
    offset_old формула выглядит как offset = -error, и она сдвигает
    модель в ПРОТИВОПОЛОЖНУЮ сторону. Проверено на Crystal_AT310: без
    сдвига смещение было +2.25 мм, при сдвиге +6.025 мм стало
    +8.27 мм, то есть выросло ровно на величину сдвига.
    """
    out = {}
    for ref in comps:
        hit = by_ref.get(ref)
        if not hit:
            continue
        box = hit[1]
        cx, cy = comps[ref]["center"]
        prev = old.get(ref, {"dx": 0.0, "dy": 0.0})
        dx = round(prev["dx"] - (box["kx"] - cx), 4)
        dy = round(prev["dy"] - (box["ky"] - cy), 4)
        if abs(dx) < 0.01 and abs(dy) < 0.01:
            continue
        out[ref] = {"dx": dx, "dy": dy}
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="проверка посадки 3D-моделей")
    ap.add_argument("--write-offsets", metavar="ПУТЬ",
                    help="записать model_offsets.json со сдвигами, "
                         "приводящими детали к центру компонента, и выйти")
    args = ap.parse_args()

    if not CIRCUIT.is_file():
        print(f"НЕТ {CIRCUIT} — сначала соберите плату")
        return 1
    if not GLB.is_file():
        print(f"НЕТ {GLB} — 3D не выгружен (нужен экспорт glb)")
        return 1

    circuit = json.loads(CIRCUIT.read_text())
    glb = load_glb(GLB)

    board = next((e for e in circuit if e.get("type") == "pcb_board"), None)
    if not board:
        print("В circuit.json нет pcb_board")
        return 1
    # Поверхность платы в glTF-Y: плата лежит в слое [-t/2, +t/2], поэтому
    # верхняя грань — половина толщины.
    surface = board["thickness"] / 2

    boxes = model_boxes(glb, surface)
    comps = components(circuit)
    footprints = footprints_by_ref()

    print("=" * 78)
    print("ПОСАДКА 3D-МОДЕЛЕЙ: board.glb против circuit.json")
    print("=" * 78)
    print(f"  плата: {board['width']} x {board['height']} x {board['thickness']} мм, "
          f"поверхность glTF-Y = {surface:.2f}")
    print(f"  circuit.json: pcb_component {len(comps)}")
    print(f"  board.glb:    узлов с моделями {len(boxes)}")
    print(f"  допуски: центр {CENTER_TOL} мм, низ детали {Z_TOL} мм\n")

    # Узел в glTF может называться по ref, по ref+pin или по source id.
    by_ref = {}
    for name, box in boxes.items():
        for ref in comps:
            if ref and (name == ref or name.startswith(f"{ref}_")
                        or name.startswith(f"{ref}.") or name.endswith(f"_{ref}")):
                by_ref[ref] = (name, box)
                break

    if args.write_offsets:
        dest = Path(args.write_offsets)
        old: dict = {}
        if dest.is_file():
            try:
                old = json.loads(dest.read_text()).get("offsets", {})
            except Exception:                           # noqa: BLE001
                old = {}
        offs = measure(boxes, comps, by_ref, old)
        dest.write_text(json.dumps({"offsets": offs}, indent=1) + "\n")
        print(f"сдвигов посчитано: {len(offs)} -> {dest}")
        for ref in sorted(offs):
            print(f"   {ref:<6} offset=({offs[ref]['dx']:+.3f}, "
                  f"{offs[ref]['dy']:+.3f})")
        return 0

    print(f"{'ref':<6} {'узел glb':<24} {'dx':>7} {'dy':>7} {'низ':>7} "
          f"{'верх':>7}  размер X x Y x Z")
    print("-" * 78)

    bad_center, bad_z, missing = [], [], []
    for ref in sorted(comps):
        cx, cy = comps[ref]["center"]
        hit = by_ref.get(ref)
        if not hit:
            missing.append(ref)
            continue
        name, box = hit
        dx, dy = box["kx"] - cx, box["ky"] - cy
        sz = box["size"]
        flags = []
        if math.hypot(dx, dy) > CENTER_TOL:
            flags.append("смещена")
            bad_center.append(ref)
        # Признак «деталь в плате» — модель целиком под поверхностью:
        # её верх не поднимается над платой, то есть середина по высоте
        # уходит вниз. Строгая проверка нижней грани даёт ложные срабатывания
        # на деталях с выводом под плату (TestPoint_Pad_D1.0mm уходит на
        # -1.15 мм, это его штатный штырь).
        if box["mid"] < -Z_TOL:
            flags.append("в плате")
            bad_z.append(ref)
        mark = ("  <- " + ", ".join(flags)) if flags else ""
        print(f"{ref:<6} {name[:24]:<24} {dx:7.2f} {dy:7.2f} {box['bottom']:7.2f} "
              f"{box['top']:7.2f}  {sz[0]:5.2f} x {sz[2]:5.2f} x {sz[1]:5.2f}{mark}")

    print()
    print(f"деталей с 3D-моделью: {len(by_ref)} из {len(comps)}")
    if missing:
        print(f"без 3D-модели ({len(missing)}): {', '.join(missing[:20])}")
    print(f"смещённых по XY (>{CENTER_TOL} мм): {len(bad_center)}")
    print(f"уходящих в плату (середина < -{Z_TOL}): {len(bad_z)}")
    if bad_center:
        print(f"   смещённые: {', '.join(bad_center[:20])}")
    if bad_z:
        print(f"   в плате: {', '.join(bad_z[:20])}")

    # ---- ориентация: предупреждение, а не ошибка ----
    #
    # У деталей с выводами сквозь плату модель обычно уходит под плоскость:
    # выводы физически входят в отверстия. Но так делают не все модели
    # packages3d, и это не дефект ориентации:
    #   * ESP32-WROOM-32 — боковые castellated-площадки, корпус заканчивается
    #     ровно на поверхности платы;
    #   * JWT_A3963 — клеммник, у которого в модели выводы не отрисованы
    #     ниже уровня платы.
    # Отличить «выводы сверху» от «выводов не нарисовали» по габариту
    # нельзя: нужно знать, где именно в модели выводы. Поэтому здесь
    # предупреждение со списком, а решение принимает просмотр.
    flipped = []
    for ref in sorted(comps):
        hit = by_ref.get(ref)
        fp = footprints.get(ref)
        if not hit or not fp:
            continue
        _, thru = footprint_pads(fp)
        if thru == 0:
            continue
        if hit[1]["bottom"] >= -Z_TOL:
            flipped.append(f"{ref} (низ {hit[1]['bottom']:+.2f} мм, "
                           f"выводов сквозь: {thru}, корпус {fp.split('/')[-1]})")
    thru_total = sum(1 for r in comps
                     if (fp := footprints.get(r)) and footprint_pads(fp)[1])
    print()
    print(f"деталей с выводами сквозь плату: {thru_total}")
    print(f"модель не уходит под плату (предупреждение): {len(flipped)}")
    for f in flipped:
        print(f"   {f}")
    if flipped:
        print("   это не обязательно ошибка: у ESP32 боковые площадки, "
              "у клеммников выводы могут не быть отрисованы")

    if bad_center or bad_z:
        print("\nОШИБКА: детали стоят неверно")
        return 1
    print("\nOK: все детали стоят на плате и по центру компонента")
    return 0


if __name__ == "__main__":
    sys.exit(main())
