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

Ориентация. Здесь модель приводится к Zmax = 0, то есть тело уходит в минус
Z. Это не догадка, а измеренное требование tscircuit: узел модели ставится в
cad_component.position (Z = толщина платы / 2) и оси переводятся как
(x, y, z)_STL -> (x, -z, y)_glTF, где glTF-Y смотрит вверх. Проверяется на
стенде hardware/tscircuit/out/probe3d.circuit.tsx (одна деталь, экспорт glb):

    STL Z[ 0.00 .. 0.80] -> деталь целиком под платой, Y[-0.10 .. 0.70]
    STL Z[-0.80 .. 0.00] -> деталь стоит на плате,     Y[ 0.70 .. 1.50]

Проверка результата — hardware/tscircuit/check_models3d.py: она сравнивает
board.glb с circuit.json и требует, чтобы каждая деталь стояла на поверхности
платы и по центру компонента.

Запуск (только freecadcmd, обычный python3 не подходит):
    freecadcmd hardware/tscircuit/step2stl.py
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
    "/home/mirrage/Desktop/brig-receiver/hardware/tscircuit/models",
))

# Качество сетки: LinearDeflection в мм. 0.05 мм — мельче допуска
# производителя (обычно 0.1 мм), поэтому визуально гладко, а файл небольшой.
LIN = float(os.environ.get("MESH_LINEAR", "0.05"))
ANG = float(os.environ.get("MESH_ANGULAR", "0.3"))
# Допуск нормализации, мм. После переноса Zmax должен быть около нуля:
# выше TOL — деталь торчит над уровнем размещения, ниже -TOL — уходит вверх.
TOL = float(os.environ.get("MESH_Z_TOL", "0.05"))


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
        if dst.is_file() and dst.stat().st_size > 0:
            done += 1
            continue
        try:
            shape = Part.read(str(src))
            mesh = MeshPart.meshFromShape(
                Shape=shape, LinearDeflection=LIN,
                AngularDeflection=ANG, Relative=False,
            )
            # Ориентация: отражение по Z, без сдвига.
            #
            # Каталог kicad-packages3d во всех моделях соблюдает одну
            # конвенцию: корпус выше Z=0, выводы, проходящие сквозь плату,
            # ниже. Проверено на выборке: SMD-корпуса Z[0..h] (C_0603
            # 0..0.80, SOIC-8 0..1.75, LQFP-48 0..1.50), а детали с
            # выводами пересекают ноль (PinHeader_1x06 -3.11..8.65,
            # DIP-8 -3.30..3.68, батарейный отсек -3.53..16.30).
            #
            # tscircuit переводит оси как (x, y, z)_STL -> (x, -z, y)_glTF,
            # где glTF-Y вверх, то есть модель оказывается перевёрнутой
            # относительно конвенции KiCad. Отражение по Z это исправляет.
            #
            # Сдвигать ничего не надо: исходная плоскость Z=0 и есть
            # поверхность платы, она уже совпадает с местом установки узла.
            # Проверено на стенде probe_orient (PinHeader_1x06, три
            # варианта на одной плате, поверхность glTF-Y = 0.70):
            #   сдвиг к Zmax=0 (то, что стояло раньше) -> Y[ 0.70 .. 12.24]
            #       тело целиком над платой, выводы в плату не заходят;
            #   отражение Z без сдвига               -> Y[-2.19 ..  9.35]
            #       тело 8.65 вверх, выводы 2.89 вниз — верно;
            #   исходник без преобразования           -> Y[-7.95 ..  3.59]
            #       тело внутри платы — неверно.
            #
            # Отражение выполняется не через FreeCAD, а записью STL: у
            # FreeCAD 1.0 mesh.transform(Matrix) геометрию разрушает
            # (габарит схлопывается в куб, 148 граней превращаются в одну).
            # Чистого зеркала Z через Placement не сделать: поворот на 180
            # градусов всегда затрагивает вторую ось.
            mesh.write(str(dst))
            bb = mesh.BoundBox
            mirrored = mirror_stl_z(dst)
            bb2 = stl_bbox(dst)
            if bb2 is None:
                print(f"  ПУСТО {rel}: не читается получившийся STL")
                bad.append(rel)
                continue
            # Признак верной ориентации: тело должно уходить в плюс Z
            # платы (после отражения минус), то есть Zmin заметно ниже нуля,
            # а Zmax около нуля. У деталей с выводами сквозь плату Zmax
            # положителен — это длина выводов, ушедших под плату.
            up = -bb2[4]
            down = bb2[5]
            print(f"  {rel.name[:44]:<44} корпус вверх {up:6.2f}, "
                  f"под плату {down:5.2f}")
            if up <= TOL:
                print(f"  НЕТ ТЕЛА: корпус не поднимается над платой "
                      f"(Zmin={bb2[4]:.2f}), модель уезжает вниз")
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
