#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Запуск tscircuit-конвейера платы для любого заказа.

Единственный вход — манифест, который написал компилятор схемы
(``generate_result.json`` после ``make kicad``): в нём уже есть

    netlist      путь к нетлисту KiCad,
    stem         базовое имя изделия (без расширения),
    power_nets   сети питания — решил агент-схемотехник,
    ground       земля — по символу питания агента,
    title        имя изделия.

И если рядом с нетлистом есть каталог design/ (заказ): спецификация
design/schematic.json с явными LCSC агентов, индекс даташитов и библиотеки
EasyEDA (design/libs) уходят в resolve_parts.py, а KICAD_LIBS_ROOT — в
build_pcb.mjs. Так подбор деталей и плата идут по тем же артикулам, что
выбрал и скачал агент.

Скрипт НЕ проектирует плату и НЕ содержит знания об устройстве: он
только передаёт эти факты штатным скриптам конвейера и честно
рапортует о статусе готовности заготовок.

Политика зависимостей (см. docs/SCRIPTS.md, «Заготовки»):
всё тяжёлое готовится один раз и живёт в корне репозитория
(jlcsearch.db, зеркала моделей, node_modules с tsci). Здесь ничего
не собирается и не устанавливается: заготовки нет — шаг пропускается
с внятной строкой в stdout, а не установкой из сети.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TSK = ROOT / "hardware" / "tscircuit"


def main() -> int:                                    # noqa: C901
    ap = argparse.ArgumentParser(
        description="нетлист + манифест → tscircuit (автороут, экспорт)")
    ap.add_argument("--manifest", required=True,
                    help="generate_result.json из make kicad")
    ap.add_argument("--full", action="store_true",
                    help="полный экспорт (все форматы); по умолчанию "
                         "быстрый ROUTING_ONLY (только circuit.json)")
    args = ap.parse_args()

    manifest = Path(args.manifest).resolve()
    if not manifest.is_file():
        print(f"НЕТ манифеста {manifest} — сначала make kicad")
        return 1
    mf = json.loads(manifest.read_text(encoding="utf-8"))
    netlist = Path(mf["netlist"]).resolve()
    if not netlist.is_file():
        print(f"НЕТ нетлиста {netlist}")
        return 1

    # каталог сборки платы — рядом с нетлистом, по базовому имени
    out_dir = manifest.parent / "tscircuit"
    out_dir.mkdir(parents=True, exist_ok=True)

    env = dict(os.environ)
    env["NETLIST"] = str(netlist)
    env["MANIFEST"] = str(manifest)
    env.setdefault("OUT_DIR", str(out_dir))

    # --- дизайн заказа: явные артикулы агентов и их паспорта.
    # Дизайнер выбирал детали по коду LCSC (part_fetch скачал под них символ,
    # корпус и 3D), поэтому подбор парт-номеров идёт от спецификации, а не
    # мимо неё: по номиналу доподбирается только то, что агент не выбирал явно.
    design = None
    for p in (netlist.parent, *list(netlist.parents)[:4]):
        if (p / "design").is_dir():
            design = p / "design"
            break
    design_args: list[str] = []
    if design:
        spec = design / "schematic.json"
        if spec.is_file():
            design_args += ["--spec", str(spec)]
        ds = design / "datasheets" / "index.json"
        if ds.is_file():
            design_args += ["--datasheets", str(ds)]
        libs = design / "libs"
        if libs.is_dir():
            design_args += ["--libs", str(libs)]
            # build_pcb.mjs: корпуса и тела EasyEDA — из design/libs
            env["KICAD_LIBS_ROOT"] = str(libs)
    if design_args:
        print(f"[детали] дизайн заказа: {design}")

    # --- заготовка 1: локальная база деталей (jlcsearch.db, один раз)
    db = TSK / "out" / "jlcsearch.db"
    if db.is_file():
        cmd = [sys.executable, str(TSK / "resolve_parts.py"),
               "--netlist", str(netlist), "--db", str(db),
               "--out", str(out_dir / "resolved_parts.json"),
               *design_args]
        rl = env.get("KICAD_MODELS_ROOT") or (TSK / "models")
        if Path(rl).is_dir():
            cmd += ["--models", str(rl)]
        rc = subprocess.run(cmd).returncode
        if rc != 0:
            print(f"[детали] подбор не удался (код {rc}) — плата пойдёт "
                  "с корпусами из нетлиста, без парт-номеров")
    else:
        print("[детали] нет заготовки jlcsearch.db "
              "(собирается build_derived.py один раз) — подбор "
              "парт-номеров пропущен, плата строится по корпусам нетлиста")

    # --- заготовка 2: модели 3D; без неё по умолчанию быстрый прогон,
    #     но полный экспорт можно потребовать явно (KR_PCB_STEPS=1):
    #     чертежам ГОСТ нужен board.step, корпуса без стл-моделей просто
    #     останутся без тел — это честная нехватка заготовки, а не сбой.
    if not (TSK / "models").is_dir():
        if os.environ.get("KR_PCB_STEPS", "0").strip().lower() in (
                "1", "true", "yes", "on"):
            print("[3D] нет заготовки моделей — полный экспорт по "
                  "KR_PCB_STEPS=1; родные корпуса (footprinter) тело "
                  "получат от tscircuit, нестандартным может не хватить")
        else:
            env.setdefault("ROUTING_ONLY", "1")
            print("[3D] нет заготовки моделей (fetch_3dmodels.sh + "
                  "step2stl.py, один раз) — быстрый прогон ROUTING_ONLY")

    # --- плата: эмиттер TSX + tsci (node_modules — готовая, без установки)
    cmd = ["node", str(TSK / "build_pcb.mjs")]
    if args.full:
        env.pop("ROUTING_ONLY", None)
    rc = subprocess.run(cmd, env=env).returncode
    if rc != 0:
        print(f"[плата] build_pcb.mjs вернул {rc}")
        return rc

    # --- посадка 3D-моделей — по факту экспорта: центры моделей в
    #     board.glb против центров компонентов в circuit.json. Смещение
    #     центра — не косметика: деталь физически стоит не там, где её
    #     посадят на плату, и чертёж/сборка из board.step разойдутся.
    if (out_dir / "board.glb").is_file():
        rc3d = subprocess.run(
            [sys.executable, str(TSK / "check_models3d.py"),
             "--out", str(out_dir)], env=env).returncode
        if rc3d != 0:
            print("[3D] проверка посадки моделей не прошла (см. отчёт выше)")
            return rc3d

    # --- тела board.step — у него отдельный сборщик и отдельная проверка:
    #     модель, не пересекающую z=0, он сажает низом на всю толщину
    #     платы (парение). Нужен FreeCAD; без него шаг честно пропускается.
    step_out = out_dir / "board.step"
    if step_out.is_file():
        rc4 = subprocess.run(
            [sys.executable, str(TSK / "check_board_step.py"),
             "--step", str(step_out), "--out", str(out_dir)],
            env=env).returncode
        if rc4 != 0:
            print("[3D] проверка тел board.step не прошла (см. отчёт выше)")
            return rc4

    cj = out_dir / "circuit.json"
    print(f"[готово] {cj}{' + полное семейство экспортов' if args.full else ''}"
          if cj.is_file() else "[готово] (circuit.json не обнаружен)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
