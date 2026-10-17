#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Замер: SPICE-дека → ngspice → measurements.json.

Универсальный прибор, а не помощник по проектированию. Он ничего не
знает об устройстве и ничего не подставляет в схему: только запускает
симулятор на деке, которую сделал агент, и переписывает ЧТО ИМЕННО
измерилось. Если ngspice не может посчитать (нет моделей транзисторов,
не разрешены подсхемы) — это фиксируется как есть: `blocked`.

РЕШЕНИЕ КАРКАСА (осознанное, 2026-10): подсхемы НЕ превращаются в
автоматические заглушки. Модели реальных приборов публикуются
изготовителями (datasheet → .model/.lib), и какая модель соответствует
какому символу — инженерное решение, а не механическое. Заглушка молча
сломала бы смысл замера (нулевые токи, фиктивные узлы) и выдала бы
агенту «измерения», которых нет. Поэтому каркас:
  * НЕ синтезирует .SUBCKT-заглушки в чужую деку;
  * лишь механически ПОДКЛЮЧАЕТ готовые библиотеки моделей, если они
    есть на диске: проектные design/spice_models.lib, spice/models.lib
    и библиотеку KiCad Simulation_SPICE.sp (kicad_builtin_*);
  * агент приносит нужные модели сам (скачивает с сайта изготовителя
    или пишет модель в проектную библиотеку) — это его работа.
Неразрешённые ссылки ngspice попадают в отчёт `blocked` как факт.

Вход : .cir (нативный экспорт KiCad)
Выход: measurements.json = {measured:[{name,value,unit}], blocked:[...],
       includes:[...], log: <путь к логу ngspice>}
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from pathlib import Path

UNIT_BY_PREFIX = {"v": "V", "i": "A", "r": "ohm", "g": "S",
                  "p": "W"}


def parse_out(text: str) -> tuple[list[dict], list[str]]:
    """Строки результатов ngspice: «v(out) =  1.234e+00», «i(v1) = …».

    Разбор чисто механический: имя, знак, число. Никакой семантики.
    """
    measured: list[dict] = []
    for m in re.finditer(
            r"^\s*([a-zA-Z])\(([^)]+)\)\s*=\s*"
            r"([+-]?[0-9.eE+-]+)\s*([a-zA-Z°%]*)\s*$",
            text, re.M):
        kind, name, val, unit = m.groups()
        try:
            v = float(val)
        except ValueError:
            continue
        measured.append({"name": f"{kind.lower()}({name})",
                         "kind": kind.lower(),
                         "value": v,
                         "unit": unit or UNIT_BY_PREFIX.get(kind.lower(), "")})
    blocked: list[str] = []
    for m in re.finditer(r"(?:^|\n).*?(?:Unable to find|Unknown subcircuit|"
                         r"no such|Error on line|undefined)[^\n]*",
                         text):
        blocked.append(m.group(0).strip()[:200])
    return measured, blocked


def model_libs(cir: Path) -> list[Path]:
    """Готовые библиотеки моделей — подключаются, если существуют.

    Порядок и состав НЕ определяют, какая модель нужна схеме: это
    исключительно решение агента. Здесь только механика: нашли файлы —
    подключили к рабочей копии деки (сама дека агента не меняется).
    """
    cands = [
        cir.parent / "spice_models.lib",
        cir.parent.parent / "design" / "spice_models.lib",
        cir.parent.parent / "spice" / "models.lib",
        Path("/usr/share/kicad/symbols/Simulation_SPICE.sp"),
    ]
    return [c for c in cands if c.is_file()]


def main() -> int:
    ap = argparse.ArgumentParser(description="измеритель: .cir → ngspice → json")
    ap.add_argument("--cir", required=True, help="SPICE-дека")
    ap.add_argument("--out", required=True, help="куда писать measurements.json")
    ap.add_argument("--timeout", type=int, default=120)
    a = ap.parse_args()

    cir = Path(a.cir).resolve()
    if not cir.is_file():
        print(json.dumps({"measured": [], "blocked": [f"нет деки {cir}"]}))
        raise SystemExit(1)
    ng = shutil.which("ngspice") or shutil.which("ngspice-bin")
    if not ng:
        out = Path(a.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(
            {"measured": [], "blocked": ["ngspice не установлен"],
             "deck": str(cir)}, ensure_ascii=False, indent=1),
            encoding="utf-8")
        print("[sim] ngspice не установлен — измерений нет")
        return 1

    work = cir.parent / (cir.stem + "_sim")
    work.mkdir(exist_ok=True)
    log = work / "ngspice.log"

    # рабочая копия деки: к ней механически добавляем .include готовых
    # библиотек моделей (если есть). Дека агента не изменяется.
    deck_for_sim = cir
    libs = model_libs(cir)
    if libs:
        deck_for_sim = work / (cir.stem + "_with_libs.cir")
        head = "".join(f'.include "{l}"\n' for l in libs)
        deck_for_sim.write_text(head + cir.read_text(errors="ignore"),
                                encoding="utf-8")
    try:
        r = subprocess.run([ng, "-b", str(deck_for_sim)], capture_output=True,
                           text=True, timeout=a.timeout, cwd=str(work))
        out_text = r.stdout + "\n" + r.stderr
    except subprocess.TimeoutExpired:
        out_text = "timeout: симуляция не уложилась в лимит"
    log.write_text(out_text, encoding="utf-8")

    measured, blocked = parse_out(out_text)
    report = {
        "deck": cir.name,
        "measured": measured,
        "blocked": blocked,
        "includes": [str(l) for l in libs],
        "log": str(log),
        "simulator": "ngspice",
    }
    Path(a.out).write_text(json.dumps(report, ensure_ascii=False, indent=1),
                           encoding="utf-8")
    print(f"[sim] измерено {len(measured)} величин, "
          f"неразрешённых мест {len(blocked)} → {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())