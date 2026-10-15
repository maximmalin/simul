"""Песочница заказа: агент работает в дочерней папке, проект не страдает.

Зачем
----
Проект — это **опора** работы агентов, а не пустое пространство под
правку. Исходники (`sim/scripts`, `backend`, `tools`, `tests`,
`requirements`) — то, на чём держится всё остальное. Если агент выполнит
`make` прямо в корне, он сможет:

* перезаписать исходник, попутно испортив и расчёты, и документацию;
* снести `out/` предыдущего заказа, лишив отчётности;
* оставить дерево в состоянии, которое невозможно объяснить.

Поэтому каждый заказ получает собственную дочернюю папку. Оригинал
остаётся нетронутым: агент пишет в песочницу, результаты забираются
наружу только явным действием.

Как устроено
------------
    work/orders/<id>/
        Makefile, sim/, backend/, tools/ …   ← копия исходников
        out/                                  ← своя, чистая
        .venv -> ../../../.venv              ← общая, только читается
        frontend/node_modules -> …            ← то же

Что копируется, а что переиспользуется:

* исходники — настоящие копии, их можно свободно менять;
* `.venv` и `node_modules` — символические ссылки: это сотни мегабайт
  зависимостей, которые никто не правит, и дублировать их ради каждого
  заказа бессмысленно;
* `.git` не копируется вовсе: песочница не репозиторий, и история
  проекта оттуда не нужна.

Почему не git worktree: он восстанавливает **все** отслеживаемые файлы
подряд, а это 139 МБ на заказ. После удаления из git файлов вывода
ngspice источники — это десятки мегабайт, и копия укладывается в
доли секунды.

Заказ в песочнице всё равно умеет собирать проект: там есть Makefile и
все скрипты, а зависимости переиспользованы ссылками.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

#: не переносим: либо зависимости (ссылками), либо кэши, либо мусор
SKIP_DIRS = {
    ".git", ".venv", "node_modules", "local_footprints", "__pycache__",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", ".run",
    "work",            # вложенные песочницы не копируются
    "frontend/vendor", # подмодуль, 24 МБ, и он не участвует в сборке
}

#: каталоги, которые заказ создаёт заново
FRESH_DIRS = ("out", "sim/outputs", "sim/netlists", "sim/nec", "sim/work")

#: что переиспользовать символической ссылкой вместо копии
LINK_DIRS = (
    ".venv",
    "frontend/node_modules",
    "node_modules",
)

#: файлы крупнее этого копируются как есть (жёсткой ссылки рискованно:
#: сборка может перезаписать файл, а жёсткая ссылка пишет в оригинал)
BIG_FILE = 64 * 1024 * 1024


def _ignore(_dir: str, names: list[str]) -> set[str]:
    skip = set()
    for n in names:
        if n in SKIP_DIRS or n.endswith(".pyc"):
            skip.add(n)
    return skip


def _link_into(sandbox: Path, project: Path, rel: str) -> None:
    """Символическая ссылка на каталог с зависимостями."""
    src = project / rel
    dst = sandbox / rel
    if not src.is_dir() or dst.exists() or dst.is_symlink():
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        dst.symlink_to(os.path.relpath(src, dst.parent))
    except OSError:
        pass          # нет прав на symlink — сборка просто не найдёт
                        # зависимости, но оригинал цел, а это важнее


def create(order_id: str, project: str | Path) -> Path:
    """Создать песочницу заказа и вернуть её путь."""
    project = Path(project).resolve()
    root = project / "work" / "orders"
    sandbox = root / (order_id or "adhoc")

    if sandbox.exists():
        shutil.rmtree(sandbox, ignore_errors=True)
    sandbox.mkdir(parents=True, exist_ok=True)

    t0 = time.monotonic()
    shutil.copytree(project, sandbox, ignore=_ignore, symlinks=True,
                    dirs_exist_ok=True)
    # служебное внутри песочницы не нужно, а .run может содержать
    # чекпоинт основного движка — не даём ему пережить переезд
    shutil.rmtree(sandbox / ".run", ignore_errors=True)

    for rel in FRESH_DIRS:
        if (sandbox / rel).exists():
            shutil.rmtree(sandbox / rel, ignore_errors=True)
        (sandbox / rel).mkdir(parents=True, exist_ok=True)

    for rel in LINK_DIRS:
        _link_into(sandbox, project, rel)

    shutil.rmtree(root / order_id if False else sandbox / "work",
                  ignore_errors=True)
    (sandbox / "work").mkdir(exist_ok=True)

    size = _size(sandbox)
    print(f"[sandbox] {sandbox}  "
          f"({size:.0f} МБ, {time.monotonic() - t0:.1f} с)")
    return sandbox


def _size(p: Path) -> float:
    """Размер с учётом того, что ссылки не занимают места."""
    total = 0
    for root, dirs, files in os.walk(p, followlinks=False):
        for f in files:
            fp = Path(root) / f
            try:
                if fp.is_symlink():
                    continue
                total += fp.stat().st_size
            except OSError:
                pass
    return total / 1024 / 1024


def drop(order_id: str, project: str | Path, keep: bool = False) -> None:
    """Убрать песочницу. keep=True оставляет на диске."""
    if keep:
        return
    sandbox = Path(project).resolve() / "work" / "orders" / order_id
    shutil.rmtree(sandbox, ignore_errors=True)


def collect_artifacts(order_id: str, project: str | Path,
                      patterns: tuple[str, ...] = ("out/**/*",)) -> list[str]:
    """Забрать артефакты из песочницы в основной проект.

    Копирование, а не перенос: оригинал остаётся целым и после него
    видно, чьи это результаты. Вызывается явно — не молча.
    """
    project = Path(project).resolve()
    sandbox = project / "work" / "orders" / order_id
    if not sandbox.is_dir():
        return []
    brought: list[str] = []
    for pattern in patterns:
        for src in sorted(sandbox.glob(pattern)):
            if not src.is_file():
                continue
            dst = project / src.relative_to(sandbox)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            brought.append(str(src.relative_to(project)))
    return brought


def list_sandboxes(project: str | Path) -> list[dict]:
    """Что накопилось в work/orders — для уборки и разбора."""
    root = Path(project).resolve() / "work" / "orders"
    if not root.is_dir():
        return []
    out = []
    for d in sorted(root.iterdir()):
        if not d.is_dir():
            continue
        arts = list((d / "out").glob("*")) if (d / "out").is_dir() else []
        out.append({
            "id": d.name,
            "path": str(d),
            "size_mb": round(_size(d), 1),
            "artifacts": len(arts),
            "modified": time.strftime("%Y-%m-%d %H:%M",
                                      time.localtime(d.stat().st_mtime)),
        })
    return out


def prune(project: str | Path, keep_last: int = 10) -> list[str]:
    """Оставить последние N песочниц, старые убрать."""
    items = list_sandboxes(project)
    if len(items) <= keep_last:
        return []
    removed = []
    for item in items[:-keep_last]:
        shutil.rmtree(item["path"], ignore_errors=True)
        removed.append(item["id"])
    return removed