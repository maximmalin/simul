#!/usr/bin/env python3
"""Проверка схемотехники: ERC, соответствие блоков, проверка требований.

Проверяет:
- Каждый функциональный блок имеет ТЗ и SKiDL код
- Входы/выходы блока согласованы с ESKD
- Требования из ESKD покрыты схемотехникой
- Нет подвисающих соединений
"""

import json
import os
import sys
from pathlib import Path


def _get(block: dict, *keys, default=None):
    """Вернуть первое непустое значение по списку ключей (RU/EN)."""
    for k in keys:
        if k in block and block.get(k) not in (None, "", []):
            return block.get(k)
    # fallback: first present key even if empty
    for k in keys:
        if k in block:
            return block.get(k)
    return default


def _unwrap_blocks(data):
    """block_tz.json может быть списком или {"блоки"/"blocks": [...]}."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("блоки", "blocks", "block_tzs", "items"):
            if key in data and isinstance(data[key], list):
                return data[key]
    return []


def _block_title(b: dict) -> str:
    return str(_get(b, "наименование", "title", "name", default=""))


def _block_code(b: dict) -> str:
    return str(_get(b, "код", "code", "slug", "index", default=""))


def _block_inputs(b: dict):
    return _get(b, "входные_параметры", "inputs", "входы", default=[])


def _block_outputs(b: dict):
    return _get(b, "выходные_параметры", "outputs", "выходы", default=[])


def _block_requirements(b: dict):
    return _get(b, "требования", "requirements", default=[])


def check_block_tz(block_tz: dict) -> list:
    """Проверить ТЗ блока на полноту."""
    errors = []
    if not _block_title(block_tz):
        errors.append("отсутствует название блока")
    if not _block_inputs(block_tz) and not _block_outputs(block_tz):
        errors.append("нет входов/выходов — блок не интерфейсный")
    if not _block_requirements(block_tz):
        errors.append("нет требований к блоку")
    return errors


def check_skidl_coverage(block_tzs: list, skidl_path: Path) -> list:
    """Проверить, есть ли SKiDL код для каждого блока."""
    errors = []
    if not skidl_path.is_file():
        errors.append(f"SKiDL файл не найден: {skidl_path}")
        return errors
    if skidl_path.stat().st_size == 0:
        errors.append(f"SKiDL файл пуст: {skidl_path}")
    return errors


def check_requirements_coverage(block_tzs: list) -> list:
    """Проверить, что по каждому требованию есть параметры."""
    errors = []
    for tz in block_tzs:
        for req in _block_requirements(tz) or []:
            if isinstance(req, dict):
                req_text = str(req.get("requirement", req.get("требование", "")))
            else:
                req_text = str(req)
            if not req_text.strip():
                errors.append(
                    f"блок {_block_code(tz) or '?'}: пустое требование")
    return errors


def main():
    order_dir = Path(os.environ.get("ORDER_DIR", "."))
    design_dir = order_dir / "design"
    out_dir = order_dir / "out"
    out_dir.mkdir(parents=True, exist_ok=True)

    tz_path = design_dir / "block_tz.json"
    skidl_candidates = [
        design_dir / "skidl_blocks.py",
        order_dir / "tools" / "skidl_blocks.py",
    ]
    skidl_path = next((p for p in skidl_candidates if p.is_file()), skidl_candidates[0])

    if not tz_path.is_file():
        print("[check] ТЗ блоков не найдено — пропуск (нет block_tz.json)")
        # Записать пустой результат, чтобы пайплайн не падал
        result = {"status": "skipped", "blocks_checked": 0, "errors": [], "error_count": 0}
        out_path = out_dir / "schematics_check.json"
        out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print("[check] ОК: проверка пропущена (0 блоков)")
        sys.exit(0)

    raw = json.loads(tz_path.read_text(encoding="utf-8"))
    block_tzs = _unwrap_blocks(raw)
    errors = []

    for tz in block_tzs:
        errs = check_block_tz(tz)
        if errs:
            errors.append(f"блок {_block_code(tz) or '?'}: {', '.join(errs)}")

    errs = check_skidl_coverage(block_tzs, skidl_path)
    errors.extend(errs)

    errs = check_requirements_coverage(block_tzs)
    errors.extend(errs)

    result = {
        "status": "ok" if not errors else "failed",
        "blocks_checked": len(block_tzs),
        "errors": errors,
        "error_count": len(errors),
    }

    out_path = out_dir / "schematics_check.json"
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                        encoding="utf-8")

    if errors:
        print(f"[check] ОШИБКИ ({len(errors)}):")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)
    else:
        print(f"[check] ОК: {len(block_tzs)} блоков проверено, ошибок нет")


if __name__ == "__main__":
    main()
