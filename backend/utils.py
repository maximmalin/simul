"""Common utilities for backend modules.

Shared helpers to reduce duplication across the codebase.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_circuit_json(path: str | Path) -> list[dict[str, Any]]:
    """Load circuit.json in various formats (tscircuit output)."""
    p = Path(path)
    d = json.loads(p.read_text())
    if isinstance(d, dict):
        return d.get("elements") or d.get("circuit") or [d]
    return d


def save_json(path: str | Path, data: Any, indent: int = 2) -> None:
    """Save data as JSON with parent dirs created."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=indent, ensure_ascii=False))


def load_json(path: str | Path, default: Any = None) -> Any:
    """Load JSON file with optional default."""
    p = Path(path)
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text())
    except Exception:
        return default


def ensure_dir(path: str | Path) -> Path:
    """Ensure directory exists and return Path."""
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p
