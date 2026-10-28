"""Сборка базы PCB-паспортов из прошлых заказов.

Паспорт детали (корпус, монтаж, габариты, выводы, ссылка на страницу
даташита) — это факты по конкретной детали. Если паспорт уже был
получен в прошлом заказе, повторно разбирать даташит незачем: факты
можно взять из локальной базы.

Скрипт собирает паспорта из всех заказов проекта в единый индекс:
``design/passports/index.json`` + ``design/passports/<ключ>.json``.

Запуск:  python3 tools/import_passports.py [корень проекта]
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path


def _key_for(part: dict) -> str:
    """Ключ паспорта: по обозначению детали и её корпусу.

    Один и тот же корпус у разных деталей — разные паспорта по габаритам
    не всегда, но корпус и выводы определяют посадочное место, а оно
    и есть суть паспорта. Поэтому ключ — от корпуса и числа выводов.
    """
    std = str(part.get("package_std") or "").strip()
    text = str(part.get("package_text") or "").strip()
    pins = part.get("pins")
    basis = "|".join([std, text, str(pins)])
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()[:16]


def collect(root: Path) -> dict:
    """Собрать паспорта из всех найденных parts_pcb.json."""
    found: dict[str, dict] = {}
    sources = list(root.glob("work/orders/*/**/parts_pcb.json"))
    sources += list(root.glob(".krystal/projects/*/work/orders/*/**/parts_pcb.json"))
    for path in sources:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        parts = (data.get("parts") or {}) if isinstance(data, dict) else {}
        if not isinstance(parts, dict):
            continue
        for ref, part in parts.items():
            if not isinstance(part, dict):
                continue
            key = _key_for(part)
            entry = found.setdefault(key, dict(part))
            entry.setdefault("refs", [])
            if ref not in entry["refs"]:
                entry["refs"].append(ref)
            entry.setdefault("источники", [])
            rel = str(path.relative_to(root)) if path.is_relative_to(root) \
                else str(path)
            if rel not in entry["источники"]:
                entry["источники"].append(rel)
    return found


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    out = root / "design" / "passports"
    out.mkdir(parents=True, exist_ok=True)
    found = collect(root)
    index: dict[str, dict] = {}
    for key, part in sorted(found.items()):
        (out / f"{key}.json").write_text(
            json.dumps(part, ensure_ascii=False, indent=2), encoding="utf-8")
        index[key] = {
            "refs": part.get("refs", []),
            "package_std": part.get("package_std", ""),
            "package_text": part.get("package_text", ""),
            "mounting": part.get("mounting", ""),
            "pins": part.get("pins", 0),
        }
    (out / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"паспортов собрано: {len(index)} → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
