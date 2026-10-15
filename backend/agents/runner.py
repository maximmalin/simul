"""Исполнители: как запустить цель сборки.

Движку всё равно, чем собирается проект. Он спрашивает раннер:
«дай команду для цели `page` и скажи, какие цели от неё зависят».

Встроенные раннеры покрывают большинство проектов:

    make      Makefile / GNUmakefile
    npm       package.json + scripts
    just      justfile / Justfile
    shell     произвольная команда из описания цели

Свой раннер добавляется регистрацией функции — движок подхватит его
автоматически:

    @register("gradle")
    class Gradle(Runner):
        detect = ("build.gradle", "settings.gradle")
        ...
"""

from __future__ import annotations

import os
import re
import subprocess
import time
from pathlib import Path
from typing import Protocol

from .types import Target


class BuildError(RuntimeError):
    """Система сборки найдена, но цель выполнить не удалось."""


class Runner(Protocol):
    """Что должен уметь любой раннер."""

    name: str
    #: файлы, по которым раннер узнаёт себя
    detect: tuple[str, ...]

    @classmethod
    def detect_in(cls, root: Path) -> bool: ...

    @classmethod
    def targets(cls, root: Path) -> list[Target]: ...

    @classmethod
    def command(cls, root: Path, target: Target) -> str: ...

    def as_dict(self) -> dict:
        return asdict(self)
    """Что должен уметь любой раннер."""

    name: str
    #: файлы, по которым раннер узнаёт себя
    detect: tuple[str, ...]

    @classmethod
    def detect_in(cls, root: Path) -> bool: ...

    @classmethod
    def targets(cls, root: Path) -> list[Target]: ...

    @classmethod
    def command(cls, root: Path, target: Target) -> str: ...


#: реестр раннеров; расширяется вызовом register()
REGISTRY: dict[str, type] = {}


def register(cls: type) -> type:
    """Регистрация раннера. Декоратор над классом."""
    REGISTRY[cls.name] = cls
    return cls


def get(name: str) -> type:
    if name not in REGISTRY:
        raise KeyError(f"неизвестный раннер «{name}»; есть: "
                       f"{', '.join(sorted(REGISTRY))}")
    return REGISTRY[name]


# ==========================================================================
#  Запуск
# ==========================================================================
def run(root: Path, command: str, *, timeout: int = 1800,
        jobs: int = 1, env: dict | None = None,
        on_line: Any = None) -> tuple[int, list[str], float]:
    """Выполнить команду в корне проекта.

    Возвращает (код возврата, вывод, секунды). Команда выполняется в
    собственной группе процессов, чтобы по таймауту можно было убить
    вместе со всеми потомками — иначе `make` оставляет детей-сирот.
    """
    t0 = time.monotonic()
    full_env = {**os.environ, **(env or {})}
    p = subprocess.Popen(
        command, cwd=root, shell=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, errors="replace", env=full_env,
        start_new_session=True,
    )
    lines: list[str] = []
    try:
        assert p.stdout is not None
        for line in p.stdout:
            lines.append(line.rstrip("\n"))
            if on_line is not None:
                try:
                    on_line(lines[-1])
                except Exception:                            # noqa: BLE001
                    pass
            if len(lines) > 600:        # лог не должен расти без границ
                del lines[:300]
        p.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_group(p)
        return -1, lines + [f"— таймаут {timeout}s —"], time.monotonic() - t0
    except KeyboardInterrupt:
        _kill_group(p)
        raise
    return p.returncode, lines, time.monotonic() - t0


def _kill_group(p: subprocess.Popen) -> None:
    import signal
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(os.getpgid(p.pid), sig)
        except (ProcessLookupError, PermissionError, OSError):
            return
        try:
            p.wait(timeout=3)
            return
        except subprocess.TimeoutExpired:
            continue


# ==========================================================================
#  make
# ==========================================================================
@register
class Make(Runner):
    name = "make"
    detect = ("Makefile", "makefile", "GNUmakefile")

    @classmethod
    def detect_in(cls, root: Path) -> bool:
        return any((root / f).is_file() for f in cls.detect)

    #: цель начинается в начале строки, двоеточие, без отступов
    _RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._%/-]*)\s*:(?!=)")
    _DESC = re.compile(r"##\s*(.+?)\s*$")

    @classmethod
    def targets(cls, root: Path) -> list[Target]:
        mk = next((root / f for f in cls.detect if (root / f).is_file()), None)
        if mk is None:
            return []
        out: list[Target] = []
        for raw in mk.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.rstrip()
            if not line or line[0] in " \t#":
                continue
            m = cls._RE.match(line)
            if not m:
                continue
            name = m.group(1)
            rest = line[m.end():]
            deps = rest.split("#", 1)[0].split()
            deps = [d for d in deps if re.fullmatch(r"[\w.%/-]+", d)]
            dm = cls._DESC.search(raw)
            out.append(Target(
                name=name,
                title=dm.group(1).strip() if dm else "",
                depends_on=deps,
                tags=["make"],
            ))
        return out

    @classmethod
    def command(cls, root: Path, target: Target) -> str:
        if target.command:
            return target.command
        jobs = max(1, int(os.environ.get("KR_MAX_PARALLEL", "1")))
        return f"make{'' if jobs == 1 else f' -j{jobs}'} {target.name}"


# ==========================================================================
#  npm
# ==========================================================================
@register
class Npm(Runner):
    name = "npm"
    detect = ("package.json",)

    @classmethod
    def detect_in(cls, root: Path) -> bool:
        return (root / "package.json").is_file()

    @classmethod
    def _pkg(cls, root: Path) -> dict:
        import json
        try:
            return json.loads((root / "package.json")
                              .read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    @classmethod
    def targets(cls, root: Path) -> list[Target]:
        scripts = cls._pkg(root).get("scripts", {})
        return [Target(name=name, title="", tags=["npm"],
                       command=f"npm run {name}")
                for name in scripts]

    @classmethod
    def command(cls, root: Path, target: Target) -> str:
        return target.command or f"npm run {target.name}"


@register
class MakeNpm(Npm):
    """npm-скрипты, но команды вида `make x` — если в проекте есть Makefile."""

    name = "make+npm"
    detect = ("Makefile", "package.json")

    @classmethod
    def detect_in(cls, root: Path) -> bool:
        return Make.detect_in(root) and Npm.detect_in(root)

    @classmethod
    def targets(cls, root: Path) -> list[Target]:
        seen: dict[str, Target] = {}
        for t in Npm.targets(root) + Make.targets(root):
            seen.setdefault(t.name, t)
        return list(seen.values())

    @classmethod
    def command(cls, root: Path, target: Target) -> str:
        if target.command:
            return target.command
        if (root / "package.json").is_file() and target.name in \
                Npm._pkg(root).get("scripts", {}):
            return f"npm run {target.name}"
        return Make.command(root, target)


# ==========================================================================
#  just
# ==========================================================================
@register
class Just(Runner):
    name = "just"
    detect = ("justfile", "Justfile", ".justfile")

    @classmethod
    def detect_in(cls, root: Path) -> bool:
        return any((root / f).is_file() for f in cls.detect)

    _RE = re.compile(r"^([a-zA-Z0-9][\w.-]*)(?:\s+[^:=]*)?:(?!=)")

    @classmethod
    def targets(cls, root: Path) -> list[Target]:
        jf = next((root / f for f in cls.detect if (root / f).is_file()), None)
        if jf is None:
            return []
        out: list[Target] = []
        for raw in jf.read_text(encoding="utf-8", errors="replace").splitlines():
            if raw[0] in " \t#":
                continue
            m = cls._RE.match(raw)
            if m:
                out.append(Target(name=m.group(1), tags=["just"],
                                  title=raw[m.end():].strip().lstrip("- ")
                                  or ""))
        return out

    @classmethod
    def command(cls, root: Path, target: Target) -> str:
        return target.command or f"just {target.name}"


# ==========================================================================
#  shell — команды прямо в описании проекта
# ==========================================================================
@register
class Shell(Runner):
    """Универсальный запасной вариант.

    Цели берутся из описания проекта: у каждой есть готовая команда.
    Если команд нет — проект нечем собирать, и это видно сразу.
    """

    name = "shell"
    detect = ()

    @classmethod
    def detect_in(cls, root: Path) -> bool:
        return True                  # всегда подходит как последний вариант

    @classmethod
    def targets(cls, root: Path) -> list[Target]:
        return []

    @classmethod
    def command(cls, root: Path, target: Target) -> str:
        if not target.command:
            raise BuildError(
                f"у цели «{target.name}» нет команды: нечего выполнять")
        return target.command


# ==========================================================================
#  Автоопределение
# ==========================================================================
#: от более специфичного к общему
DETECTION_ORDER = ("make+npm", "just", "make", "npm", "shell")


def autodetect(root: Path) -> str:
    """Какой раннер подходит этому проекту."""
    for name in DETECTION_ORDER:
        if name in REGISTRY and REGISTRY[name].detect_in(root):
            return name
    return "shell"


def available() -> list[str]:
    return sorted(REGISTRY)