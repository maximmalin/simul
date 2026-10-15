"""Общие приспособления для тестов ML-конвейера (REQ-ANA-03)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return ROOT


@pytest.fixture(scope="session")
def ml_modules():
    """Импортирует пакет ml; пропускает тесты, если numpy недоступен."""
    ml = pytest.importorskip("ml", reason="пакет ml недоступен")
    pytest.importorskip("numpy", reason="numpy не установлен")
    return ml


@pytest.fixture(scope="session")
def dataset_root() -> Path:
    return ROOT / "ml_data"
