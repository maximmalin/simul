#!/usr/bin/env python3
"""
Пакетная конвертация 3D-моделей STEP -> STL.

Зачем конвертация. tscircuit экспортирует 3D через circuit-json-to-gltf,
который грузит модель как MESH. Проверено экспериментально:
  * modelUrl = абсолютный путь  -> записывается в circuit.json, но в STEP/GLB
                                    НЕ попадает (BREP 32 и без модели, и с ней);
  * modelUrl = ES-импорт .step  -> то же самое, модель теряется;
  * modelUrl = ES-импорт .stl  -> модель ПОПАДАЕТ в экспорт
                                    (GLB 58352 байт против 48680 без модели).
Причина в том, что бандлер tsci умеет инлайнить известные форматы
(glb/gltf/obj/stl), а .step проходит мимо. Поэтому модель приводится
к STL — единственному формату, который и FreeCAD умеет писать, и tscircuit
умеет читать.

Ориентация STL зависит от пары эмиссии, и это не косметика. Сборщик glb
(circuit-json-to-gltf, getDefaultModelTransform) выбирает перевод осей для
STL по наличию stepUrl у детали:

  * пара STL+STEP (наш заказ: build_pcb.mjs эмитит оба адреса) — STL
    читается с переводом STEP-ветки, (x, y, z)_STL -> (x, +z, y)_glTF.
    Поэтому STL обязан совпадать по ориентации с STEP: тело в плюсе z,
    низ — на ZMIN (чуть ниже нуля). Плоскость Z=0 модели и есть
    поверхность платы: узел ставится в cad_component.position.
  * STL без STEP (зеркало packages3d: у системных корпусов STEP рядом нет)
    — перевод Z_UP_TO_Y_UP_USB_FIX: (x, y, z)_STL -> (x, -z, y)_glTF,
    то есть STL должен быть зеркальным (тело в минусе z). Это измерено на
    стенде hardware/tscircuit/out/probe3d.circuit.tsx (одна деталь, без
    stepUrl, экспорт glb):

        STL Z[ 0.00 .. 0.80] -> деталь целиком под платой, Y[-0.10 .. 0.70]
        STL Z[-0.80 .. 0.00] -> деталь стоит на плате,     Y[ 0.70 .. 1.50]

Перепутанная ориентация видна сразу: деталь уходит внутрь платы. Ловится
hardware/tscircuit/check_models3d.py (board.glb против circuit.json): каждая
деталь должна стоять на поверхности и по центру компонента; пары STL+STEP
сверяются ещё до экспорта в build_pcb.mjs.

Запуск (только freecadcmd, обычный python3 не подходит):
    freecadcmd hardware/tscircuit/step2stl.py

Каталоги берутся из окружения MODELS_DIR (где искать *.step) и STL_DIR
(куда класть *.stl), по умолчанию — зеркало kicad-packages3d. Так этим же
скриптом конвертируются и проектные библиотеки заказа: tools/part_fetch.py
зовёт его с MODELS_DIR=STL_DIR=design/libs/<nick>.3dshapes сразу после
скачивания детали. Уже сконвертированные и не устаревшие STL пропускаются,
поэтому повторный вызов безвреден.

NORMALIZE_Z=1 (это ставит part_fetch) добавляет посадку по z: STEP
выравнивается так, чтобы низ тела был чуть ниже плоскости платы — см.
комментарий к NORMALIZE_Z. Для зеркала kicad-packages3d режим не нужен и
не включается.

MIRROR_Z задаёт зеркальную запись STL (z -> -z вместе с нормалями). По
умолчанию включён ровно тогда, когда NORMALIZE_Z выключен: STL без пары
STEP (зеркало packages3d) должен быть зеркальным, STL в паре со STEP —
совпадать с ним по ориентации.
"""

import os
import struct
import sys
from pathlib import Path

import FreeCAD
import Mesh
import MeshPart
import Part

# Векторы для отражения/переноса геометрии (см. main).
App = FreeCAD

MODELS = Path(os.environ.get("MODELS_DIR", "/mnt/ext4data/kicad3d/9.0.2"))
OUT = Path(os.environ.get(
    "STL_DIR",
    "/home/mirrage/Desktop/krystal/hardware/tscircuit/models",
))

# Качество сетки: LinearDeflection в мм. 0.05 мм — мельче допуска
# производителя (обычно 0.1 мм), поэтому визуально гладко, а файл небольшой.
LIN = float(os.environ.get("MESH_LINEAR", "0.05"))
ANG = float(os.environ.get("MESH_ANGULAR", "0.3"))
# Допуск нормализации, мм. После переноса Zmax должен быть около нуля:
# выше TOL — деталь торчит над уровнем размещения, ниже -TOL — уходит вверх.
TOL = float(os.environ.get("MESH_Z_TOL", "0.05"))

# Посадка по z для каталогов заказа (NORMALIZE_Z=1; так зовёт part_fetch).
#
# Выгрузки EasyEDA приходят кто куда. Примеры, снятые с одной и той же
# детали: SOD-523 с точками z=[-0.71, 0.00] — тело целиком ниже плоскости
# платы, и в board.step оно тонет в плате (замер: z=[0.10, 0.80]); та же
# деталь в другой выгрузке — точки [-0.71, 0.05] при собственном размещении
# внутри файла, которое поднимает тело на [-0.01, 0.75]. Поэтому посадка
# считается по ЭФФЕКТИВНОМУ габариту (BoundBox, размещения учтены), а
# геометрию правит сам FreeCAD: если тело не пересекает z=0, оно
# сдвигается так, чтобы низ встал на ZMIN, и STEP переписывается.
#
# Почему низ на -0.01: у сборщика board.step посадка зависит от того,
# пересекает ли модель z=0. Пересекающие (низ -0.01 тоже пересекает)
# садятся переводом boardThickness/2 ровно на верхнюю грань платы: замер
# board.step R0603 z=[0.79, 1.24] при грани 0.80. Модель целиком выше
# нуля (C_0603 из кэша, z=[0.00, 0.80]) уходит в другую ветку, где
# смещение поверхности складывается дважды (t/2 - minZ + position.z, а
# position.z у tscircuit уже = t/2): низ встаёт на z=1.60 вместо 0.80 —
# замер board.step z=[1.60, 2.40]. Тела со сквозными выводами пересекают
# ноль по построению — их не трогаем. Сдвиг идемпотентен.
NORMALIZE_Z = os.environ.get("NORMALIZE_Z") == "1"
ZMIN = float(os.environ.get("MESH_Z_MIN", "-0.01"))
TH_Z_TOL = 0.001

# Зеркальная запись STL (z -> -z вместе с нормалями). Нужна только для
# STL БЕЗ пары STEP (зеркало packages3d): там glb читает STL через
# USB-ветку перевода осей (y=-z). В заказе (NORMALIZE_Z=1) STL идёт в
# паре со STEP и обязан совпадать с ним по ориентации — зеркало
# перевернуло бы деталь. MIRROR_Z перекрывает выбор при необходимости.
MIRROR = os.environ.get("MIRROR_Z", "0" if NORMALIZE_Z else "1") == "1"

# Тип детали от part_fetch: "0" — SMD, "1" — со сквозными выводами,
# пусто — неизвестно. Карта KR_PART_THRU_MAP (строки «имя_модели TAB 0/1»)
# позволяет одной конвертации каталога обслужить и SMD, и сквозные
# вперемешку: для SMD с чужим якорем низ переносится на ZMIN, сквозные не
# трогаются (пересечение нуля у них — выводы, а не середина корпуса).
PART_THRU_MAP: dict[str, str] = {}
_pm = os.environ.get("KR_PART_THRU_MAP", "")
if _pm and Path(_pm).is_file():
    for _line in Path(_pm).read_text().splitlines():
        _stem, _, _val = _line.partition("\t")
        if _stem and _val in ("0", "1"):
            PART_THRU_MAP[_stem] = _val


def part_thru_for(stem: str) -> str:
    """Тип детали для модели: из карты; иначе общий KR_PART_THRU."""
    return PART_THRU_MAP.get(stem) or os.environ.get("KR_PART_THRU", "").strip()


def stl_bbox(path: Path):
    """Габариты STL: (xmin, xmax, ymin, ymax, zmin, zmax) в мм."""
    try:
        buf = path.read_bytes()
    except OSError:
        return None
    zs: list[float] = []
    add = lambda a, b, c: (zs.append(c),)
    if buf[:5] == b"solid":
        import re as _re
        for m in _re.finditer(rb"vertex\s+([-\d.eE+]+)\s+([-\d.eE+]+)\s+([-\d.eE+]+)", buf):
            add(0, 0, float(m.group(3)))
        return (None, None, None, None, min(zs), max(zs)) if zs else None
    n = int.from_bytes(buf[80:84], "little")
    off = 84
    for _ in range(n):
        if off + 48 > len(buf):
            break
        import struct as _s
        v = _s.unpack_from("<12f", buf, off)
        for k in (3, 6, 9):
            zs.append(v[k + 2])
        off += 50
    return (None, None, None, None, min(zs), max(zs)) if zs else None


def stl_orientation_ok(path: Path, mirrored: bool) -> bool:
    """Быстрая сверка ориентации готового STL с конвенцией эмиссии.

    mirrored=False (пара STL+STEP): тело обязано подниматься в плюс z.
    mirrored=True (STL без STEP): тело в минусе z, то есть -zmin > TOL.
    Полнота проверки (НЕТ ТЕЛА/ПЕРЕВЕРНУТО) — в main; здесь только то,
    что нужно для решения «пересобирать или файл уже в конвенции».
    """
    bb = stl_bbox(path)
    if bb is None:
        return False
    up = -bb[4] if mirrored else bb[5]
    return up > TOL


def stl_anchor_ok(path: Path, stem: str) -> bool:
    """Свежий STL уже посажен по текущей конвенции?

    Для SMD-деталей заказа (NORMALIZE_Z=1, тип «0») низ обязан стоять на
    ZMIN. Без этой сверки смена правила посадки тихо пережила бы
    пересборку: STL «свежее» STEP и старой ориентации, а якорь у него
    прежний — так и остался бы полуутопленным C0603 из EasyEDA.
    """
    if not (NORMALIZE_Z and not MIRROR and part_thru_for(stem) == "0"):
        return True
    bb = stl_bbox(path)
    return bb is not None and ZMIN - TOL <= bb[4] <= ZMIN + TOL


def mirror_stl_z(path: Path) -> bool:
    """
    Отразить геометрию STL по Z: z -> -z.

    Нормали отражаются вместе с вершинами: иначе они перестанут быть
    перпендикулярны граням, и при расчёте освещения поверхности будут
    перепутаны.

    Возвращает True, если файл перезаписан. ASCII-STL не поддерживается и
    обходится с предупреждением: FreeCAD пишет бинарный формат, а на
    входе всегда его же.
    """
    buf = path.read_bytes()
    if buf[:5] == b"solid":
        print(f"  ! {path.name}: ASCII-STL не поддерживается, пропускаю отражение")
        return False
    n = int.from_bytes(buf[80:84], "little")
    if 84 + n * 50 != len(buf):
        print(f"  ! {path.name}: размер не сходится с числом треугольников, "
              f"отражение пропущено")
        return False
    out = bytearray(buf[:84])
    for i in range(n):
        rec = bytearray(buf[84 + 50 * i : 84 + 50 * (i + 1)])
        v = list(struct.unpack_from("<12f", rec, 0))
        for k in (2, 5, 8, 11):        # нормаль и три вершины
            v[k] = -v[k]
        struct.pack_into("<12f", rec, 0, *v)
        out += rec
    path.write_bytes(bytes(out))
    return True


def main() -> int:
    steps = sorted(MODELS.rglob("*.step"))
    if not steps:
        print(f"НЕТ STEP в {MODELS}")
        return 1

    print(f"STEP найдено: {len(steps)}")
    print(f"из них уже STL: ", end="")
    print(sum(1 for s in steps if (OUT / s.relative_to(MODELS).with_suffix('.stl')).is_file()))
    print()

    ok = 0
    done = 0
    bad = []
    for src in steps:
        rel = src.relative_to(MODELS).with_suffix(".stl")
        dst = OUT / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        # STL свежее STEP — оставляем; если STEP перезалили или
        # перенормализовали (tools/part_fetch.py), конвертируем заново.
        # Ориентация тоже проверяется: смена конвенции зеркала (STL без
        # STEP против пары STL+STEP) обязана пересобрать файл, даже если
        # он «свежее» — иначе старая ориентация тихо доживёт до board.glb.
        if (dst.is_file() and dst.stat().st_size > 0
                and dst.stat().st_mtime >= src.stat().st_mtime
                and stl_orientation_ok(dst, MIRROR)
                and stl_anchor_ok(dst, src.stem)):
            done += 1
            continue
        try:
            shape = Part.read(str(src))
            if NORMALIZE_Z:
                bb = shape.BoundBox
                straddles = (bb.ZMin < -TH_Z_TOL and bb.ZMax > TH_Z_TOL)
                # SMD с чужим якорем: EasyEDA отдаёт и тела, отцентрованные
                # по z (C0603 [-0.40, 0.40] при высоте 0.8) — низ на ZMIN.
                # Сквозные (тип «1»/неизвестно) при пересечении нуля не
                # трогаются: у них это выводы, а не середина корпуса.
                smd_misanchored = (part_thru_for(src.stem) == "0"
                                   and bb.ZMin < ZMIN - TOL)
                if ((not straddles or smd_misanchored)
                        and (bb.ZMax - bb.ZMin) > 1e-6):
                    dz = ZMIN - bb.ZMin
                    if abs(dz) > 0.005:
                        mat = App.Matrix()
                        mat.move(App.Vector(0, 0, dz))
                        shape = shape.transformShape(mat, True)
                        shape.exportStep(str(src))
                        print(f"  ПОСАДКА {rel.name[:44]:<44} сдвиг {dz:+.2f} мм, "
                              f"было z[{bb.ZMin:.2f}, {bb.ZMax:.2f}]")
            mesh = MeshPart.meshFromShape(
                Shape=shape, LinearDeflection=LIN,
                AngularDeflection=ANG, Relative=False,
            )
            # Ориентация: сдвига нет — плоскость Z=0 модели и есть
            # поверхность платы (узел ставится в cad_component.position).
            # Зеркало — только для STL без пары STEP (MIRROR; см.
            # «Ориентация STL» в заголовке файла).
            #
            # Каталог kicad-packages3d и выгрузки EasyEDA соблюдают одну
            # конвенцию: корпус выше Z=0, выводы сквозь плату — ниже
            # (C_0603 0..0.80, PinHeader_1x06 -3.11..8.65,
            # SOD-523 -0.01..0.75). Для пары STL+STEP она сохраняется как
            # есть; для зеркала packages3d без STEP — отражается, чтобы
            # компенсировать USB-ветку перевода осей tscircuit
            # ((x, y, z)_STL -> (x, -z, y)_glTF).
            #
            # Отражение выполняется не через FreeCAD, а записью STL: у
            # FreeCAD 1.0 mesh.transform(Matrix) геометрию разрушает
            # (габарит схлопывается в куб, 148 граней превращаются в одну).
            # Чистого зеркала Z через Placement не сделать: поворот на 180
            # градусов всегда затрагивает вторую ось.
            mesh.write(str(dst))
            if MIRROR:
                mirror_stl_z(dst)
            bb2 = stl_bbox(dst)
            if bb2 is None:
                print(f"  ПУСТО {rel}: не читается получившийся STL")
                bad.append(rel)
                continue
            # Признак верной ориентации — «корпус вверх»: для пары STL+STEP
            # это плюс z (Zmax), для зеркального STL — минус z (тогда
            # «вверх» = -Zmin). «Под плату» — противоположный край: длина
            # выводов сквозь плату (или ≈0 у SMD).
            if MIRROR:
                up, down = -bb2[4], bb2[5]
            else:
                up, down = bb2[5], -bb2[4]
            print(f"  {rel.name[:44]:<44} корпус вверх {up:6.2f}, "
                  f"под плату {down:5.2f}")
            if up <= TOL:
                print(f"  НЕТ ТЕЛА: корпус не поднимается над платой "
                      f"(z=[{bb2[4]:.2f}, {bb2[5]:.2f}]), модель лежит не на плоскости")
                bad.append(rel)
            if up - down > 4 * max(up, 1.0):
                print(f"  ПЕРЕВЕРНУТО: {down:.2f} мм уходит вниз против "
                      f"{up:.2f} вверх, похоже, выводы оказались сверху")
                bad.append(rel)

            if dst.is_file() and dst.stat().st_size > 0:
                ok += 1
            else:
                print(f"  ПУСТО {rel}")
                bad.append(rel)
        except Exception as e:                      # noqa: BLE001
            print(f"  ОШИБКА {rel}: {type(e).__name__}: {e}")
            bad.append(rel)

    total = len(steps)
    print()
    print(f"готово (уже было) {done}, сконвертировано {ok}, всего {total}")
    print(f"каталог STL: {OUT}")
    if bad:
        print(f"НЕ КОНТРОЛИРУЕТСЯ (тело не вверх или перевёрнуто): {len(bad)}")
        for b in bad[:10]:
            print(f"   {b}")
        return 1
    return 0 if (ok + done) == total else 1


# freecadcmd импортирует скрипт как модуль, а не запускает как __main__,
# поэтому блок if __name__ == "__main__" здесь не срабатывает. Вызываем явно.
_rc = main()
sys.stdout.flush()
sys.exit(_rc)
