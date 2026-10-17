#!/usr/bin/env python3
"""
Проверка посадки 3D-моделей на компоненты.

Зачем. tscircuit выравнивает модель по своему габариту
(model_origin_alignment: center_of_component_on_board_surface), а не по
собственному началу координат: центр габарита модели встаёт в центр
корпуса. Поэтому деталь из каталога, начало координат которой
произвольно, всё равно должна встать по центру компонента — и проверять
это нужно по факту, а не по предположению.

Раньше на это опирался «автосдвиг» в эмиттере (positionOffset из
model_offsets.json). Он не нужен: tscircuit центрирует модели сам, а
любой positionOffset сдвинул бы деталь из центра ровно на свою
величину. Механизм сдвигов удалён, и эта проверка — единственный
источник истины о посадке.

Проверка работает по фактическим данным экспорта и потому годится для любой
схемы, а не только для текущей:

    freecad не нужен, node не нужен, только два файла экспорта.

  * circuit.json -> pcb_component: центр каждого компонента (pcbX/pcbY) и
    его ref;
  * board.glb    -> узел каждой модели: габарит из POSITION-accessor
    (объединение всех примитивов меша) и матрица узла.

Итог: для каждой детали печатается отклонение центра модели от центра
компонента и высота над платой. Ненулевое отклонение означает, что деталь
перекошена; нижняя грань ниже поверхности платы — что деталь уходит вглубь.

Запуск (--out — каталог экспортов, по умолчанию штатный out репозитория;
run_pcb.py передаёт сюда каталог заказа):

    python3 hardware/tscircuit/check_models3d.py [--out DIR]
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
# Штатный каталог экспортов репозитория. В песочнице заказа каталог другой
# (out/hardware/tscircuit) — его передаёт run_pcb.py аргументом --out.
DEFAULT_OUT = ROOT / "hardware/tscircuit/out"

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
    хранятся), объединяя ВСЕ примитивы меша, затем переносим в систему
    координат узла. Размеры не масштабируем по сцене: нас интересует, где
    физически стоит деталь.
    """
    accessors, meshes, nodes = (glb["accessors"], glb["meshes"], glb["nodes"])
    out: dict[str, dict] = {}
    for node in nodes:
        mi = node.get("mesh")
        if mi is None:
            continue
        # Раньше брался только первый примитив. Меш бывает нарезан на
        # несколько (у PinHeader kicad-mod-cache корпус и выводы приходят
        # отдельными примитивами): по первому низ/верх показывали только
        # корпус (0.00…2.54 вместо -3.00…8.54), и выводы сквозь плату не
        # попадали в проверку ориентации.
        prims = meshes[mi]["primitives"]
        los, his = [], []
        for p in prims:
            a = accessors[p["attributes"]["POSITION"]]
            if "min" in a and "max" in a:
                los.append(a["min"])
                his.append(a["max"])
        if not los:
            continue
        lo = [min(v[i] for v in los) for i in range(3)]
        hi = [max(v[i] for v in his) for i in range(3)]
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
        # Число вершин всех примитивов нужно, чтобы отличить настоящую
        # геометрию от заглушки: если локальную модель прочитать не
        # удалось, экспортёр подставляет коробку по габаритам корпуса —
        # у неё ровно 24 вершины. Настоящие меши деталей крупнее.
        verts = sum(accessors[p["attributes"]["POSITION"]].get("count", 0)
                    for p in prims)
        out[name] = {
            "kx": -(min(xs) + max(xs)) / 2,
            "ky": (min(zs) + max(zs)) / 2,
            "bottom": lo_y,
            "top": hi_y,
            "mid": (lo_y + hi_y) / 2,
            "size": (max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs)),
            "verts": verts,
        }
    return out


def footprints_by_ref(tsx_dir: Path) -> dict[str, str]:
    """
    ref -> значение поля footprint из сгенерированного TSX.

    Берётся из эмиттера, а не из circuit.json: в circuit.json у
    pcb_component поля ref нет, а корпус известен только там.

    Значение встречается в трёх видах, и все три нужны:
      * "kicad:Capacitor_SMD/C_0603_1608Metric" — стандартный корпус
        (tscircuit сам берёт для него модель из своего кэша);
      * абсолютный путь "…/easyeda.pretty/SOD-523_….kicad_mod" — своя
        посадочная площадка заказа из design/libs (партия part_fetch);
      * "Lib:Name" — формат нетлиста KiCad.

    Раньше шаблон требовал «kicad:» и, не найдя его, перескакивал к
    следующему компоненту: D1 с путём к своему .kicad_mod получал корпус
    J1, и предупреждение об ориентации указывало не на ту деталь.
    """
    tsx = tsx_dir / "board.circuit.tsx"
    out: dict[str, str] = {}
    if not tsx.is_file():
        return out
    # Порядок свойств у эмиттера фиксирован — name, затем footprint
    # (build_pcb.mjs, emitTsx), поэтому пары восстанавливаются по порядку
    # следования: запоминаем имя, следующий footprint после него — корпус.
    pending: str | None = None
    for m in re.finditer(
        r'name=\{?"([^"]+)"?\}|footprint=\{?"([^"]+?)"\}',
        tsx.read_text(errors="ignore"),
    ):
        if m.group(1) is not None:
            pending = m.group(1)
        elif pending is not None:
            out[pending] = m.group(2)
            pending = None
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


def native_refs(circuit: dict) -> set[str]:
    """
    Детали с родным телом tscircuit: у cad_component НЕТ URL модели.

    Такие детали эмитятся строкой генератора (footprint="0603" и т.п.):
    корпус и 3D-тело строит сам tscircuit, и меш в board.glb — настоящая
    геометрия. Проверка «коробка-заглушка» относится только к моделям по
    URL (STL/STEP), поэтому родные детали из неё исключаются.
    """
    refs = {e["source_component_id"]: e.get("name", "")
            for e in circuit if e.get("type") == "source_component"}
    pcb_ref: dict[str, str] = {}
    for c in circuit:
        if c.get("type") == "pcb_component":
            pcb_ref[c.get("pcb_component_id")] = \
                refs.get(c.get("source_component_id"), "")
    out: set[str] = set()
    for e in circuit:
        if e.get("type") != "cad_component":
            continue
        url = (e.get("model_stl_url") or e.get("model_step_url")
               or e.get("model_obj_url") or e.get("model_url"))
        if url:
            continue
        ref = pcb_ref.get(e.get("pcb_component_id"), "")
        if ref:
            out.add(ref)
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
    """
    (всего выводов, выводов сквозь плату).

    Корпус принимается в любом виде, в каком его пишут наши файлы:
    "kicad:Lib/Name" и "Lib:Name" ищутся в системной библиотеке KiCad,
    путь к файлу .kicad_mod (свои площадки заказа из design/libs)
    читается прямо по пути. Файл не найден — (0, 0): проверка ориентации
    молча пропустит деталь, ошибок не выдумываем.
    """
    txt = None
    s = ref_footprint.strip()
    p = Path(s) if s.endswith(".kicad_mod") else None
    if p and p.is_file():
        txt = p.read_text(errors="ignore")
    if txt is None:
        # Разделитель бывает и двоеточием (нетлист KiCad), и слешем (формат
        # kicad:Lib/Name в tscircuit). Берём тот, который реально есть.
        s = s.removeprefix("kicad:")
        sep = ":" if ":" in s else "/"
        lib, _, name = s.partition(sep)
        f = FP_ROOT / f"{lib}.pretty" / f"{name}.kicad_mod"
        if f.is_file():
            txt = f.read_text(errors="ignore")
    if txt is None:
        return 0, 0
    # Блок (pad ...) в .kicad_mod многострочный, поэтому ищем по блокам,
    # а не по строке: иначе тип вывода в следующей строке не виден.
    pads = thru = 0
    for blk in re.finditer(r"\(pad\b((?:[^()]|\([^()]*\))*)\)", txt, re.S):
        pads += 1
        if re.search(r"\b(?:thru_hole|np_thru_hole)\b", blk.group(1)):
            thru += 1
    return pads, thru


def main() -> int:
    ap = argparse.ArgumentParser(description="проверка посадки 3D-моделей")
    ap.add_argument("--out", metavar="КАТАЛОГ",
                    help="каталог экспортов (circuit.json, board.glb, "
                         "board.circuit.tsx); по умолчанию "
                         "hardware/tscircuit/out репозитория")
    args = ap.parse_args()

    out_dir = Path(args.out).resolve() if args.out else DEFAULT_OUT
    circuit_path = out_dir / "circuit.json"
    glb_path = out_dir / "board.glb"

    if not circuit_path.is_file():
        print(f"НЕТ {circuit_path} — сначала соберите плату")
        return 1
    if not glb_path.is_file():
        print(f"НЕТ {glb_path} — 3D не выгружен (нужен экспорт glb)")
        return 1

    circuit = json.loads(circuit_path.read_text())
    glb = load_glb(glb_path)

    board = next((e for e in circuit if e.get("type") == "pcb_board"), None)
    if not board:
        print("В circuit.json нет pcb_board")
        return 1
    # Поверхность платы в glTF-Y: плата лежит в слое [-t/2, +t/2], поэтому
    # верхняя грань — половина толщины.
    surface = board["thickness"] / 2

    boxes = model_boxes(glb, surface)
    comps = components(circuit)
    footprints = footprints_by_ref(out_dir)
    native = native_refs(circuit)   # родные тела tscircuit (footprinter)

    print("=" * 78)
    print("ПОСАДКА 3D-МОДЕЛЕЙ: board.glb против circuit.json")
    print("=" * 78)
    print(f"  плата: {board['width']} x {board['height']} x {board['thickness']} мм, "
          f"поверхность glTF-Y = {surface:.2f}")
    print(f"  circuit.json: pcb_component {len(comps)}, "
          f"из них родных (footprinter) {len(native)}")
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
        print("   это ошибка: у каждой детали должно быть тело — родное "
              "(footprinter) или модель из design/libs")
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
            fp_short = (fp.rsplit("/", 1)[-1].removeprefix("kicad:")
                        .removesuffix(".kicad_mod"))
            flipped.append(f"{ref} (низ {hit[1]['bottom']:+.2f} мм, "
                           f"выводов сквозь: {thru}, корпус {fp_short})")
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

    # ---- заглушки: модель в glb — коробка, а не тело детали ----
    #
    # tscircuit подставляет коробку по габаритам корпуса (24 вершины),
    # когда модель по её URL прочитать не удалось. Для локальных STEP это
    # штатная ситуация: конвертер gltf читает только STL/GLB/OBJ и
    # сетевые STEP. Поэтому рядом с каждым design/libs/*.step должен
    # лежать *.stl в той же ориентации (его делает tools/part_fetch.py
    # зовом step2stl.py) — иначе в board.glb вместо детали будет коробка.
    # Родные тела (footprinter) исключены: их геометрию строит сам
    # tscircuit, коробкой-заглушкой там и не пахнет.
    boxed = [f"{ref} ({hit[1]['verts']} верш.)"
             for ref in sorted(comps) if ref not in native
             and (hit := by_ref.get(ref)) and hit[1]["verts"] <= 36]
    print()
    print(f"моделей-заглушек (коробка вместо тела): {len(boxed)} "
          f"(проверяются только модели по URL, родных {len(native)})")
    for b in boxed:
        print(f"   {b}")
    if boxed:
        print("   для локальных STEP нужен STL рядом (в той же "
              "ориентации): python3 tools/part_fetch.py --lcsc <C…> "
              "(довложит) или freecadcmd step2stl.py (NORMALIZE_Z=1)")

    if bad_center or bad_z or boxed or missing:
        print("\nОШИБКА: детали стоят неверно")
        return 1
    print("\nOK: все детали стоят на плате и по центру компонента")
    return 0


if __name__ == "__main__":
    sys.exit(main())
