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
FRESH_DIRS = ("out", "sim/outputs", "sim/netlists", "sim/nec", "sim/work",
              "design")

#: системные инструменты, которые получает КАЖДЫЙ заказ (копируются в
#: песочницу — агенту позволено их править; оригиналы в корне нетронуты):
#: универсальный компилятор принципиальной схемы и штатный движок разводки.
#: Путь — имя → фактическое место в корне репозитория.
SYSTEM_TOOLS: tuple[tuple[str, str], ...] = (
    ("tools/schematic_from_json.py", "tools/schematic_from_json.py"),
    # реальная деталь по LCSC: символ/корпус/3D EasyEDA + даташит
    ("tools/part_fetch.py", "tools/part_fetch.py"),
    # готовый скрапер даташитов (kicad-happy, MIT) — фоллбек-цепочка
    # part_fetch зовёт его для деталей без прямой ссылки
    ("tools/vendor/kicad_happy/fetch_datasheet_lcsc.py",
     "tools/vendor/kicad_happy/fetch_datasheet_lcsc.py"),
    # PCB-паспорт деталей: задание по даташитам и проверка (упаковщик)
    ("tools/part_pcb.py", "tools/part_pcb.py"),
    # поблочная сборка SKiDL: нетлисты блоков по спецификации дизайнера
    ("tools/skidl_blocks.py", "tools/skidl_blocks.py"),
    ("hardware/kicad/kicad_lib_brig.py",
     "hardware/kicad/kicad_lib_brig.py"),
    ("hardware/kicad/generate_brig_receiver.py",
     "hardware/kicad/generate_brig_receiver.py"),
    # звено платы: тонкий раннер + эмиттер tscircuit + подбор деталей.
    # node_modules с tsci приходит из SHARED_DIRS, базы — из DEPS_CACHE.
    ("hardware/tscircuit/run_pcb.py", "hardware/tscircuit/run_pcb.py"),
    ("hardware/tscircuit/build_pcb.mjs",
     "hardware/tscircuit/build_pcb.mjs"),
    ("hardware/tscircuit/resolve_parts.py",
     "hardware/tscircuit/resolve_parts.py"),
    # механика чертежей: STEP → очистка Analysis Situs → FreeCAD TechDraw
    ("gost_pipeline.py", "gost_pipeline.py"),
)

#: Готовые заготовки зависимостей платы (источник — корень репозитория).
#: Политика: собираются один раз по инструкциям docs/SCRIPTS.md и живут
#: в корне постоянно; конвейер заказа их НИКОГДА не пересобирает и не
#: устанавливает — только берёт готовое. Заготовки нет — это честная
#: нехватка, о которой сообщает сам конвейер, а не повод запускать
#: сетевую установку внутри заказа.
DEPS_CACHE: tuple[tuple[str, str], ...] = (
    # локальная база деталей в стиле jlcsearch — собирает
    # hardware/tscircuit/build_derived.py ровно один раз
    ("hardware/tscircuit/out/jlcsearch.db",
     "hardware/tscircuit/out/jlcsearch.db"),
)

#: Тяжёлый кэш 3D-моделей корпусов (зеркало packages3d, приведённое к
#: STL: hardware/tscircuit/fetch_3dmodels.sh + step2stl.py). Включается
#: переменной KR_DEPS_3D=1: нужен только заказам с 3D-экспортом платы.
if os.environ.get("KR_DEPS_3D", "0").strip().lower() in ("1", "true",
                                                         "yes", "on"):
    DEPS_CACHE = DEPS_CACHE + (
        ("hardware/tscircuit/models", "hardware/tscircuit/models"),
    )

#: корень репозитория (backend/agents/sandbox.py → backend → корень)
REPO_ROOT = Path(__file__).resolve().parents[2]

#: Каталоги с зависимостями: `.venv` и `node_modules`.
#:
#: Три варианта были рассмотрены, и два из них оказались дырой:
#:
#: * симлинк — указывает внутрь проекта, `npm install` пишет в оригинал;
#: * жёсткие ссылки (`cp -al`) — места не занимают, но разделяют inode
#:   с оригиналом: правка «на месте» в песочнице меняет проект, а
#:   `chmod` по песочнице меняет права настоящих файлов.
#:
#: Остаётся единственное, что действительно изолирует, — настоящая
#: копия. Она стоит около гигабайта на заказ, поэтому песочницы
#: подчищаются (KR_KEEP_SANDBOXES), а перед заказом снимается хеш
#: исходников: даже если что-то пройдёт мимо всех запретов,
# �� расхождение будет замечено, а не обнаружено через неделю.
SHARED_DIRS = (
    ".venv",
    "frontend/node_modules",
    "node_modules",
)

#: жёсткие ссылки можно включить явно — экономит до гигабайта,
#: но безопасности не даёт (см. выше)
USE_LINKS = os.environ.get("KR_SANDBOX_LINKS", "0").strip().lower() in (
    "1", "true", "yes", "on")

#: файлы крупнее этого копируются как есть
BIG_FILE = 64 * 1024 * 1024


def _ignore(_dir: str, names: list[str]) -> set[str]:
    skip = set()
    for n in names:
        if n in SKIP_DIRS or n.endswith(".pyc"):
            skip.add(n)
    return skip


def _link_into(sandbox: Path, project: Path, rel: str) -> None:
    """Настоящая копия каталога с зависимостями.

    Копия, а не ссылка: см. объяснение у SHARED_DIRS. Жёсткие ссылки
    доступны только при явном KR_SANDBOX_LINKS=1 и по умолчанию выключены.
    """
    src = project / rel
    dst = sandbox / rel
    if not src.is_dir() or dst.exists() or dst.is_symlink():
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    flag = "-al" if USE_LINKS else "-a"
    try:
        subprocess.run(["cp", flag, str(src), str(dst)],
                       check=True, capture_output=True, timeout=1800)
    except (subprocess.SubprocessError, OSError):
        return
    if USE_LINKS:
        return          # права не трогаем: inode общий с оригиналом,
                        # chmod здесь снял бы запись и с настоящих файлов
    # в песочнице запись в зависимости запрещаем: инструмент, которому
    # нужно писать, должен получить отказ, а не тихо изменить песочницу
    for root, dirs, files in os.walk(dst):
        for name in files + dirs:
            p = Path(root) / name
            if p.is_symlink() or not p.exists():
                continue
            try:
                os.chmod(p, p.stat().st_mode & ~0o222)
            except OSError:
                pass


def _find_links_into(sandbox: Path, project: Path) -> list[str]:
    """Симлинки из песочницы в проект — их быть не должно.

    Найденные показываются и удаляются: именно они делают песочницу
    проницаемой для записи.
    """
    bad: list[str] = []
    project = Path(project)
    root = project.resolve()
    for dirpath, _dirs, files in os.walk(sandbox, followlinks=False):
        for name in files + list(_dirs):
            p = Path(dirpath) / name
            if not p.is_symlink():
                continue
            try:
                target = p.resolve()
            except OSError:
                continue
            if str(target).startswith(str(root)) and not str(p.resolve()) \
                    .startswith(str(sandbox.resolve())):
                bad.append(str(p.relative_to(sandbox)))
                p.unlink()
    return bad


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

    # системные инструменты: универсальный компилятор схемы + движок
    # разводки. Сумма копируемых файлов под килобайт/мегабайт — не мешает.
    for src_rel, dst_rel in SYSTEM_TOOLS:
        src = REPO_ROOT / src_rel
        dst = sandbox / dst_rel
        if not src.is_file() or dst.is_file():
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

    # готовые заготовки зависимостей (DEPS_CACHE): копия уже собранного
    # из корня репозитория; установки/сборки в цикле заказа не бывает
    for src_rel, dst_rel in DEPS_CACHE:
        src = REPO_ROOT / src_rel
        dst = sandbox / dst_rel
        if not src.exists() or dst.exists():
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            subprocess.run(["cp", "-a", str(src), str(dst)],
                           check=True, capture_output=True, timeout=1800)
        else:
            shutil.copy2(src, dst)

    for rel in SHARED_DIRS:
        _link_into(sandbox, project, rel)

    leaked = _find_links_into(sandbox, project)
    if leaked:
        print(f"[sandbox] удалено проницаемых ссылок: {len(leaked)}")

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


# ==========================================================================
#  Контроль целостности
# ==========================================================================
#  Запретить разрушающие цели — мера предупреждающая. Но «даже случайно»
#  предупреждением не закрывается: любой инструмент может испортить файл,
#  не будучи «разрушающей целью». Поэтому источники проекта перед
#  заказом и после него сверяются по контрольной сумме, и расхождение
#  показывается явно.
# ==========================================================================
def source_manifest(project: str | Path) -> dict[str, str]:
    """Хеши отслеживаемых файлов — снимок состояния проекта.

    Берутся файлы под контролем git: они и есть опора проекта.
    """
    import hashlib
    import subprocess as sp

    project = Path(project).resolve()
    try:
        names = sp.run(["git", "ls-files"], cwd=project, capture_output=True,
                       text=True, timeout=60).stdout.split("\n")
    except (sp.SubprocessError, OSError):
        names = []
    out: dict[str, str] = {}
    for name in names:
        name = name.strip()
        if not name:
            continue
        f = project / name
        try:
            if not f.is_file():
                out[name] = "нет"
                continue
            h = hashlib.sha256()
            with f.open("rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    h.update(chunk)
            out[name] = h.hexdigest()[:16]
        except OSError:
            out[name] = "недоступен"
    return out


def verify_intact(before: dict[str, str],
                  project: str | Path) -> dict:
    """Сравнить снимки и сказать, что именно изменилось."""
    after = source_manifest(project)
    changed = [n for n in before
               if n in after and after[n] != before[n]]
    gone = [n for n in before if n not in after]
    added = [n for n in after if n not in before]
    return {"intact": not (changed or gone),
            "changed": changed[:20], "removed": gone[:20],
            "added": added[:20]}


# ==========================================================================
#  Блокировка проекта на время работы агентов
# ==========================================================================
#  Песочница изолирует по файлам, но пользователь просит более сильной
#  гарантии: «основной проект только читаемый для агентов». Без прав
#  root недоступны chattr +i и mount -o ro, поэтому остаётся POSIX.
#
#  Что делается: у всех файлов под git и у каталогов снимается право
#  записи. После этого агент не может ни изменить исходник, ни создать
#  новый файл рядом с ним — даже если он выйдет за пределы песочницы.
#
#  Восстановление обязано быть безусловным, поэтому состояние
#  записывается в файл: если процесс упал и права не вернулись, их
#  вернёт следующий запуск (см. `restore_if_locked`), а проект не
#  останется в нечитаемом состоянии.
# ==========================================================================
#: каталоги, которые запирать нельзя: там пишут песочницы и журналы
_LOCK_EXCLUDE = {"work", ".run", ".git", "node_modules", ".venv",
                 "__pycache__", ".pytest_cache", "frontend"}

LOCK_STATE = ".run/project-lock.json"


def _lock_targets(project: Path) -> list[Path]:
    """Что запираем: файлы под git плюс каталоги верхнего уровня.

    Каталоги нужны, чтобы агент не мог создать новый файл там, где его
    быть не должно: запрет на файл не мешает создать соседний.
    """
    import subprocess as sp

    targets: list[Path] = []
    try:
        names = sp.run(["git", "ls-files"], cwd=project, capture_output=True,
                       text=True, timeout=60).stdout.splitlines()
    except (sp.SubprocessError, OSError):
        names = []
    for name in names:
        p = project / name.strip()
        if p.is_file() and not p.is_symlink():
            targets.append(p)

    for child in project.iterdir():
        if child.is_dir() and child.name not in _LOCK_EXCLUDE:
            targets.append(child)
    return targets


def lock(project: str | Path) -> dict:
    """Снять право записи с проекта. Возвращает состояние для отката."""
    project = Path(project).resolve()
    state = LOCK_STATE
    state_path = project / state

    # незакрытый прошлый запуск — сначала доводим до конца
    restore_if_locked(project)

    targets = _lock_targets(project)
    saved: list[dict] = []
    for p in targets:
        try:
            mode = p.stat().st_mode
            if mode & 0o222:
                os.chmod(p, mode & ~0o222)
                saved.append({"p": str(p.relative_to(project)),
                              "mode": oct(mode)})
        except OSError:
            continue

    state_path.parent.mkdir(parents=True, exist_ok=True)
    import json
    state_path.write_text(json.dumps(saved, ensure_ascii=False),
                          encoding="utf-8")
    print(f"[lock] {len(saved)} путей без права записи")
    return {"locked": len(saved), "state": state}


def unlock(project: str | Path) -> int:
    """Вернуть права по сохранённому состоянию."""
    project = Path(project).resolve()
    state_path = project / LOCK_STATE
    if not state_path.is_file():
        return 0
    import json
    try:
        saved = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state_path.unlink(missing_ok=True)
        return 0

    n = 0
    for item in saved:
        p = project / item["p"]
        try:
            if p.exists():
                os.chmod(p, int(item["mode"], 8))
                n += 1
        except OSError:
            continue
    state_path.unlink(missing_ok=True)
    if n:
        print(f"[lock] права возвращены: {n} путей")
    return n


def restore_if_locked(project: str | Path) -> int:
    """Открыть проект, если он остался закрытым после падения.

    Вызывается лаунчером при старте: иначе проект, закрытый упавшим
    заказом, остался бы нечитаемым до следующего успешного запуска.
    """
    return unlock(project)


def is_locked(project: str | Path) -> bool:
    return (Path(project).resolve() / LOCK_STATE).is_file()


# ==========================================================================
#  Уборка
# ==========================================================================
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