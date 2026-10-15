"""Описание проекта: откуда движок узнаёт, что и как собирать.

Приоритет источников:

1. **Явный файл** `agents.project.yaml` / `krystal.project.json` в корне —
   полный контроль: свои цели, команды, артефакты, словарь.
2. **Автоопределение** — по файлам в корне (`Makefile`, `package.json`,
   `justfile`) раннер подбирается сам, цели перечисляются автоматически.
3. **Заглушка** — проект без системы сборки: выполнять нечего, но граф
   работает и это видно в статусе, а не падает молча.

Ничего из этого не зашито под конкретный репозиторий: добавление нового
проекта — это положить рядом файл описания, а не править движок.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from .runner import BuildError, autodetect
from .types import (Artifact, Project, Target, is_destructive,
                      is_service)

#: имена файлов описания, которые ищутся в корне проекта
PROFILE_NAMES = (
    "agents.project.yaml",
    ".agents-project.yaml",
    "agents.project.json",
    "krystal.project.json",
)

#: куда смотрят glob-паттерны артефактов, если проект не описал иначе
DEFAULT_ARTIFACT_DIRS = ("out", "build", "dist", "output", "artifacts",
                         "target", "_build", ".output")


# ==========================================================================
#  Загрузка
# ==========================================================================
def load(root: str | Path, name: str = "", profile: str = "",
          profile_path: str = "", **overrides) -> Project:
    """Собрать описание проекта из репозитория.

    root          — корень проекта
    name          — человекочитаемое имя (иначе из описания или имени
                   каталога)
    profile       — явный файл описания
    profile_path  — синоним `profile`, оставлен для совместимости
    overrides     — точечные переопределения (runner=..., artifacts=…)
    """
    root = Path(root).resolve()
    if not root.is_dir():
        raise BuildError(f"корень проекта не найден: {root}")

    data = _read_profile(root, profile or profile_path)

    proj = Project(
        name=name or data.get("name") or root.name,
        root=str(root),
        description=data.get("description", ""),
        runner=data.get("runner") or overrides.get("runner") or autodetect(root),
        build_file=data.get("build_file", ""),
        runner_opts=data.get("runner_opts", {}),
        artifacts=[Artifact(**a) if isinstance(a, dict) else Artifact(pattern=str(a))
                   for a in data.get("artifacts", [])],
        vocabulary={k: list(v) for k, v in data.get("vocabulary", {}).items()},
        system=data.get("system", ""),
        verify=data.get("verify", ""),
        agent_owner=dict(data.get("agent_owner", {})),
        agents=list(data.get("agents", []) or []),
    )

    _load_targets(proj, root, data)
    if not proj.artifacts:
        proj.artifacts = _guess_artifacts(root)

    # служебные цели: объявленные проектом плюс очевидные по имени
    declared = set(data.get("service", []))
    for t in proj.targets:
        # разрушающая цель помечается служебной и вдобавок исключается
        # из buildable(): в планы она не попадает, и даже если агент
        # назовёт её напрямую, исполнение её отклонит
        t.service = (t.service or t.name in declared
                     or is_service(t.name) or is_destructive(t.name))

    for k, v in overrides.items():
        if v is not None and hasattr(proj, k):
            setattr(proj, k, v)

    return proj


def _read_profile(root: Path, explicit: str = "") -> dict:
    """Прочитать файл описания, если он есть. Ничего не нашли — {}."""
    candidates = [Path(explicit)] if explicit else [root / n for n in PROFILE_NAMES]
    for p in candidates:
        if not p.is_file():
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        if p.suffix == ".json":
            return json.loads(text)
        try:
            import yaml
            data = yaml.safe_load(text)
            return data if isinstance(data, dict) else {}
        except ImportError:
            return _tiny_yaml(text)
        except Exception as exc:                                # noqa: BLE001
            raise BuildError(
                f"не удалось разобрать описание проекта {p.name}: {exc}") from exc
    return {}


def _tiny_yaml(text: str) -> dict:
    """Разбор подмножества YAML: словари, списки, скаляры.

    Полноценный YAML тянет за собой PyYAML ради конфига, который
    описывается шестью полями. Поддерживается всё, что реально нужно
    в project-файле: вложенные словари, списки строк и словарей,
    числа, булевы значения и многострочные блоки `|`.
    """
    root: dict = {}
    stack: list[tuple[int, object]] = [(-1, root)]
    key = None
    block_key: str | None = None
    block: list[str] = []

    def coerce(v: str):
        v = v.strip()
        if v.startswith(("[", "{")):
            try:
                return json.loads(v)
            except ValueError:
                return v
        if v in ("true", "True"):
            return True
        if v in ("false", "False"):
            return False
        if v in ("null", "~", ""):
            return None
        try:
            return int(v)
        except ValueError:
            pass
        try:
            return float(v)
        except ValueError:
            return v

    for raw in text.splitlines():
        if block_key is not None:
            if raw.strip() == "" or raw[0] in " \t":
                block.append(raw[4:] if raw.startswith("    ") else raw.strip())
                continue
            indent, container = stack[-1]
            if isinstance(container, dict):
                container[block_key] = "\n".join(block).strip()
            block_key, block = None, []

        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue

        indent = len(line) - len(line.lstrip())
        body = line.strip()

        while len(stack) > 1 and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]

        if body.startswith("- "):
            item = body[2:].strip()
            if not isinstance(parent, list):
                continue
            if ":" in item and not item.startswith("{"):
                k, _, v = item.partition(":")
                parent.append({k.strip(): coerce(v)})
            else:
                parent.append(coerce(item))
            continue

        if ":" not in body:
            continue
        key, _, val = body.partition(":")
        key, val = key.strip(), val.strip()

        if not isinstance(parent, dict):
            continue
        if val in ("|", "|-", ">", ">-"):
            block_key, block = key, []
            continue
        if val == "":
            child: object = {}
            parent[key] = child
            stack.append((indent, child))
            # список может оказаться пустым — уточним по первой же
            # следующей строке с дефисом
            stack[-1] = (indent, _LazyList(parent, key, stack))
            continue
        parent[key] = coerce(val)

    return root


class _LazyList(dict):
    """Заглушка: пустое значение `key:` становится списком, если дальше
    пошли строки с дефисом."""

    def __init__(self, parent: dict, key: str, stack: list):
        super().__init__()
        self._parent, self._key, self._stack = parent, key, stack

    def append(self, item):                                    # pragma: no cover
        cur = self._parent.get(self._key)
        if not isinstance(cur, list):
            cur = []
            self._parent[self._key] = cur
        cur.append(item)


def _convert(container: object) -> object:
    """Разворачивает отложенные списки в обычные."""
    if isinstance(container, _LazyList):
        return container
    if isinstance(container, dict):
        return {k: _convert(v) for k, v in container.items()}
    if isinstance(container, list):
        return [_convert(v) for v in container]
    return container


def _load_targets(proj: Project, root: Path, data: dict) -> None:
    """Цели: из описания, иначе — из раннера."""
    declared = data.get("targets", [])
    by_name = {t.name: t for t in proj_targets_via_runner(proj, root)}

    if isinstance(declared, dict):
        declared = [{"name": k, **v} for k, v in declared.items()]

    if declared:
        for item in declared:
            if isinstance(item, str):
                item = {"name": item}
            tgt = Target(
                name=item.get("name", ""),
                title=item.get("title", "") or item.get("description", ""),
                command=item.get("command", ""),
                top_level=bool(item.get("top_level", False)),
                depends_on=list(item.get("depends_on", [])),
                tags=list(item.get("tags", [])),
                runner=item.get("runner", ""),
                service=bool(item.get("service", False)),
            )
            # недостающие поля добираем из автоопределения
            auto = by_name.get(tgt.name)
            if auto:
                tgt.title = tgt.title or auto.title
                tgt.depends_on = tgt.depends_on or auto.depends_on
                tgt.command = tgt.command or auto.command
            by_name[tgt.name] = tgt
        proj.targets = list(by_name.values())
    else:
        proj.targets = list(by_name.values())

    _mark_top_level(proj)


def proj_targets_via_runner(proj: Project, root: Path) -> list[Target]:
    """Спросить у раннера, какие цели он умеет."""
    from . import runner as R
    try:
        return R.get(proj.runner).targets(root)
    except Exception:                                            # noqa: BLE001
        return []


def _mark_top_level(proj: Project) -> None:
    """Пометить цели, которые покрывают зависимые.

    Планировщику нужны именно они: выбрал `page` — build-система сама
    соберёт всё, от чего `page` зависит. Дублировать эти зависимости
    в плане не нужно, иначе получится гонка за общие файлы.
    """
    dep: set[str] = set()
    for t in proj.targets:
        dep.update(d for d in t.depends_on if d in proj.by_name())
    for t in proj.targets:
        t.top_level = t.name not in dep


# ==========================================================================
#  Артефакты
# ==========================================================================
def _guess_artifacts(root: Path) -> list[Artifact]:
    """Артефакты по умолчанию: результаты в стандартных каталогах."""
    out: list[Artifact] = []
    for d in DEFAULT_ARTIFACT_DIRS:
        p = root / d
        if p.is_dir():
            out.append(Artifact(pattern=f"{d}/**/*",
                                title=f"артефакты {d}/"))
    return out


def collect(project: Project) -> list[Path]:
    """Развернуть glob-паттерны артефактов в список файлов."""
    root = Path(project.root)
    seen: dict[str, Path] = {}
    for art in project.artifacts:
        try:
            for hit in sorted(root.glob(art.pattern)):
                if hit.is_file():
                    seen.setdefault(str(hit), hit)
        except (ValueError, OSError):
            continue
    return list(seen.values())[:500]


# ==========================================================================
#  CLI
# ==========================================================================
def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(
        description="Показать описание проекта, как его увидел движок")
    ap.add_argument("root", nargs="?", default=".",
                    help="корень проекта (по умолчанию текущий)")
    ap.add_argument("--profile", default="", help="файл описания")
    ap.add_argument("--json", action="store_true", help="вывести JSON")
    args = ap.parse_args()

    proj = load(args.root, profile_path=args.profile)
    data = {**proj.as_dict(), "artifacts": [a.as_dict() for a in proj.artifacts],
            "targets": [t.as_dict() for t in proj.targets]}
    data = _convert(data)

    if args.json:
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return 0

    print(f"проект : {proj.name}")
    print(f"корень : {proj.root}")
    print(f"раннер : {proj.runner}"
          + (f"  (файл: {proj.build_file})" if proj.build_file else ""))
    print(f"целей  : {len(proj.targets)} "
          f"(верхнеуровневых {len(proj.top_level())})")
    print(f"артефактов: {len(collect(proj))} по {len(proj.artifacts)} правил")
    if proj.description:
        print(f"описание: {proj.description}")
    print("\nцели:")
    for t in proj.targets:
        flag = "★" if t.top_level else " "
        dep = f"  ← {', '.join(t.depends_on)}" if t.depends_on else ""
        print(f"  {flag} {t.name:16s} {(t.title or '')[:52]}{dep}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())