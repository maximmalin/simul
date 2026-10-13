#!/usr/bin/env python3
"""init_project.py — создать свежий проект на основе текущего.

Что считается определением проекта: список файлов под контролем версий.
Именно он содержит структуру, скрипты автоматизации, компоненты и данные,
которые не пересобираются (3D-модели, FMU, датащиты, книги). Всё остальное —
сборки и кэш — восстанавливается командами из карты проекта и в git не лежит.

Инициализация делает четыре вещи:
  1. копирует отслеживаемые файлы в целевой каталог;
  2. создаёт репозиторий и первый коммит;
  3. по желанию создаёт ЛОКАЛЬНЫЙ bare-репозиторий и назначает его origin,
     так что у нового проекта есть настоящее удалённое хранилище;
  4. проверяет результат и печатает, что делать дальше.

Запуск:
    python3 tools/init_project.py ../brig-receiver-2
    python3 tools/init_project.py ../brig-receiver-2 --remote ~/git/brig-receiver-2.git
    python3 tools/init_project.py ../brig-receiver-2 --from /path/to/source
    python3 tools/init_project.py ../brig-receiver-2 --dry-run
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Что должно существовать в готовом проекте, иначе инициализация неполна
REQUIRED = [
    "hardware/pipeline.py",
    "hardware/kicad/generate_brig_receiver.py",
    "hardware/kicad/kicad_lib.py",
    "hardware/tscircuit/build_pcb.mjs",
    "hardware/tscircuit/build_derived.py",
    "hardware/tscircuit/resolve_parts.py",
    "hardware/tscircuit/models",
    "sim/scripts/bragg_sim.py",
    "sim/scripts/antenna_sim.py",
    "sim/fmu",
    "package.json",
    "package-lock.json",
    "PROJECT_MAP.md",
    "manifest.json",
    "tools/dump_manifest.py",
    "tools/init_project.py",
]

# Каталоги, которые создаются скриптами и не должны копироваться
NEVER_COPY = ("node_modules/", ".git/", ".venv/", "hardware/tscircuit/out/",
              "sim/work/", "dist/", ".tscircuit/")


def run(*args: str, cwd: Path | None = None, check: bool = False) -> tuple[int, str]:
    r = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=1800)
    if check and r.returncode != 0:
        raise RuntimeError(f"{' '.join(args)} -> {r.stderr.strip()[:200]}")
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def tracked_files(src: Path) -> list[str]:
    """Файлы под контролем версий: это и есть определение проекта."""
    code, out = run("git", "-C", str(src), "ls-files", "-z")
    if code == 0 and out:
        return [p for p in out.split("\0") if p]
    # каталог не под git — берём всё, кроме исключённого и служебного
    skip = set(NEVER_COPY) | {".git", ".venv", "__pycache__", ".pytest_cache"}
    found = []
    for p in src.rglob("*"):
        rel = p.relative_to(src)
        if any(str(rel).startswith(s) for s in skip):
            continue
        if p.is_file() and not p.name.endswith((".pyc", ".h5")):
            found.append(str(rel))
    return sorted(found)


def human(n: int) -> str:
    return f"{n / 1048576:.1f} МБ" if n >= 1048576 else f"{n // 1024} КБ"


def copy_tree(src: Path, dst: Path, files: list[str], dry: bool) -> tuple[int, int]:
    count = size = 0
    for rel in files:
        s = src / rel
        if not s.is_file():
            continue
        d = dst / rel
        if dry:
            count += 1
            size += s.stat().st_size
            continue
        d.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(s, d)
        count += 1
        size += s.stat().st_size
    return count, size


def init_git(dst: Path, name: str, remote: str | None, dry: bool) -> list[str]:
    steps = []
    if dry:
        return ["git init -b main", "git add -A", "git commit", f"git remote add origin {remote or '<путь>'}"]
    run("git", "init", "-b", "main", cwd=dst)
    steps.append("создан репозиторий, ветка main")

    run("git", "config", "user.name", name, cwd=dst)
    run("git", "config", "user.email", f"{name}@localhost", cwd=dst)
    run("git", "config", "commit.gpgsign", "false", cwd=dst)
    steps.append(f"пользователь репозитория: {name}")

    run("git", "add", "-A", cwd=dst, check=True)
    steps.append("файлы добавлены в индекс")

    code, out = run("git", "commit", "-q", "-m",
                    "Начальный проект: схема, плата, физика и автоматизация", cwd=dst)
    if code != 0:
        steps.append(f"ВНИМАНИЕ: коммит не создан: {out.strip()[:120]}")
    else:
        n = run("git", "rev-list", "--count", "HEAD", cwd=dst)[1].strip()
        steps.append(f"первый коммит создан, всего коммитов: {n}")

    if remote:
        rp = Path(remote).expanduser()
        rp.parent.mkdir(parents=True, exist_ok=True)
        if not rp.exists():
            run("git", "init", "--bare", "-b", "main", str(rp), check=True)
            steps.append(f"локальный удалённый репозиторий: {rp}")
        run("git", "remote", "add", "origin", str(rp), cwd=dst)
        run("git", "push", "-u", "origin", "main", cwd=dst)
        steps.append(f"origin назначен на {rp}, ветка отправлена")
    return steps


def verify(dst: Path) -> tuple[bool, list[str]]:
    """Обязательные файлы на месте и проект не пустой."""
    notes, ok = [], True
    missing = [r for r in REQUIRED if not (dst / r).exists()]
    if missing:
        ok = False
        notes.append("НЕТ обязательного: " + ", ".join(missing[:6]))
    else:
        notes.append(f"все {len(REQUIRED)} обязательных путей на месте")

    code, out = run("git", "-C", str(dst), "rev-list", "--count", "HEAD")
    if code == 0 and out.strip() not in ("", "0"):
        notes.append(f"коммитов в новом репозитории: {out.strip()}")
    code, out = run("git", "-C", str(dst), "remote", "get-url", "origin")
    if code == 0:
        notes.append(f"origin: {out.strip()}")
    else:
        notes.append("origin не задан (это допустимо)")
    return ok, notes


def main() -> int:
    ap = argparse.ArgumentParser(description="создать свежий проект")
    ap.add_argument("target", type=Path, help="каталог нового проекта")
    ap.add_argument("--from", dest="source", type=Path, default=ROOT,
                    help="исходный проект (по умолчанию текущий)")
    ap.add_argument("--name", default=None, help="имя проекта и пользователя git")
    ap.add_argument("--remote", default=None, help="путь к локальному bare-репозиторию")
    ap.add_argument("--dry-run", action="store_true", help="только показать план")
    args = ap.parse_args()

    src = args.source.resolve()
    dst = args.target.resolve()
    name = args.name or dst.name

    if not src.is_dir():
        print(f"исходный проект не найден: {src}", file=sys.stderr)
        return 1
    if dst.exists() and any(dst.iterdir()):
        print(f"целевой каталог не пуст: {dst}", file=sys.stderr)
        print("  освободите его или укажите другой путь", file=sys.stderr)
        return 1
    if dst == src:
        print("целевой каталог совпадает с исходным", file=sys.stderr)
        return 1

    files = tracked_files(src)
    skipped = [f for f in files if any(f.startswith(s) for s in NEVER_COPY)]

    print(f"исходник: {src}")
    print(f"цель:     {dst}")
    print(f"файлов под контролем версий: {len(files)}")

    if not args.dry_run:
        dst.mkdir(parents=True, exist_ok=True)
    count, size = copy_tree(src, dst, files, args.dry_run)
    print(f"скопировано: {count} файлов, {human(size)}")
    if skipped:
        print(f"пропущено служебных: {len(skipped)}")

    steps = init_git(dst, name, args.remote, args.dry_run)
    print("\n--- git ---")
    for s in steps:
        print(f"  {s}")

    if args.dry_run:
        print("\n--- проверка ---")
        print("  пропущена: файлы не записывались")
        print("\nпробный запуск, файлы не создавались")
        print(f"\nвремя: {datetime.now(timezone.utc).isoformat(timespec='seconds')}")
        return 0

    ok, notes = verify(dst)
    print("\n--- проверка ---")
    for n in notes:
        print(f"  {n}")

    if ok and not args.dry_run:
        print(f"\nПроект создан: {dst}")
        print("Дальше по карте проекта, раздел 6:")
        print("  cd " + str(dst))
        print("  npm ci")
        print("  python3 hardware/tscircuit/build_derived.py")
        print("  python3 hardware/pipeline.py")
        print("  python3 tools/dump_manifest.py      # ожидается 73 / 46 / 190")
    elif args.dry_run:
        print("\nпробный запуск, файлы не записывались")
    else:
        print("\nпроект создан НЕ полностью, смотрите проверку выше", file=sys.stderr)
        return 2

    print(f"\nвремя: {datetime.now(timezone.utc).isoformat(timespec='seconds')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())