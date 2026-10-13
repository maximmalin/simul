#!/usr/bin/env python3
"""
Перенос 3D-моделей в статический сайт dist/.

Зачем. `tsci build --site` кладёт рядом с circuit.json только его самого и
бандл runframe. В circuit.json у cad_component поле model_stl_url содержит
ПУТЬ В ФАЙЛОВОЙ СИСТЕМЕ (например /home/.../models/C_0603_1608Metric.stl),
потому что модель пришла в код как ES-импорт. Статический сайт по такому
адресу обратится к http://localhost:PORT/home/... и получит 404, то есть
интерактивная 3D-сцена в браузере останется без деталей (превью-картинка
3d.png при этом есть, она построена на этапе сборки, когда файл ещё был
доступен).

Что делает скрипт:
  1. находит в dist/**/circuit.json элементы cad_component;
  2. копирует каждый упомянутый model_stl_url в dist/models/ под коротким
     именем (столкновения имён невозможны: имя = хвост пути);
  3. переписывает model_stl_url на ./models/<файл>.

Результат: dist/ самодостаточен, никаких обращений ни в файловую систему,
ни в сеть. Скрипт идемпотентен и годится для любой схемы: список моделей
берётся из данных, а не из кода.

Запуск:
    python3 hardware/tscircuit/site_assets.py [--dist dist] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def find_circuit_jsons(dist: Path) -> list[Path]:
    return sorted(dist.rglob("circuit.json"))


def main() -> int:
    ap = argparse.ArgumentParser(description="перенос 3D-моделей в dist/")
    ap.add_argument("--dist", default=str(ROOT / "dist"))
    ap.add_argument("--dry-run", action="store_true",
                    help="только показать, что изменится")
    a = ap.parse_args()

    dist = Path(a.dist)
    if not dist.is_dir():
        print(f"НЕТ {dist} — сначала соберите сайт: tsci build <файл> --site")
        return 1

    jsons = find_circuit_jsons(dist)
    if not jsons:
        print(f"В {dist} нет circuit.json")
        return 1

    models_dir = dist / "models"
    copied: dict[str, str] = {}     # исходный путь -> имя в dist/models
    rewritten = 0
    missing: list[str] = []

    for cj in jsons:
        try:
            circuit = json.loads(cj.read_text())
        except Exception as e:                          # noqa: BLE001
            print(f"  ! {cj.name}: не разбирается: {e}")
            return 1

        changed = False
        for e in circuit:
            if not isinstance(e, dict):
                continue
            if e.get("type") != "cad_component":
                continue
            url = e.get("model_stl_url")
            if not isinstance(url, str) or not url:
                continue
            # Уже относительный — ничего делать не нужно.
            if not url.startswith("/"):
                continue

            if url not in copied:
                src = Path(url)
                if not src.is_file():
                    missing.append(url)
                    continue
                copied[url] = src.name

            if not a.dry_run:
                models_dir.mkdir(parents=True, exist_ok=True)
                dst = models_dir / copied[url]
                if not dst.is_file() or dst.stat().st_size != src_size(url):
                    shutil.copy2(url, dst)
            e["model_stl_url"] = f"./models/{copied[url]}"
            rewritten += 1
            changed = True

        if changed and not a.dry_run:
            cj.write_text(json.dumps(circuit, ensure_ascii=False))

    print(f"circuit.json в сайте: {len(jsons)}")
    print(f"ссылок model_stl_url переписано: {rewritten}")
    print(f"моделей скопировано в {models_dir}: {len(copied)}")
    if missing:
        print(f"НЕТ ФАЙЛА ({len(missing)}):")
        for m in missing[:10]:
            print(f"   {m}")
        return 1
    if a.dry_run:
        print("(--dry-run: файлы не изменялись)")
    return 0


def src_size(url: str) -> int:
    try:
        return Path(url).stat().st_size
    except OSError:
        return -1


if __name__ == "__main__":
    sys.exit(main())
