"""Разбор моделей SPICE — готовой библиотекой spicelib, не своими regex.

Задача модуля — дать библиотекарю факты о модели:

* где она объявлена (файл во внутренней базе);
* подсхема это или карточка прибора;
* сколько у подсхемы выводов и как они названы;
* сам текст модели (чтобы включить её в деку).

Разбор делает spicelib (открытая библиотека, тот же, что читает нетлисты
для LTspice/ngspice). Свой разбор грамматики SPICE в проекте не
заводим: он был бы источником ошибок — и против правила «только готовые
решения».

Индекс модели кэшируется на диске: база моделей большая (десятки тысяч
объявлений), а перечитывать её каждый раз дорого.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

#: корень проекта
ROOT = Path(__file__).resolve().parents[2]

#: расширения файлов, в которых объявляют модели
MODEL_SUFFIXES = (".lib", ".mod", ".cir", ".spi")

#: имя файла кэша индекса (рядом с базой моделей)
INDEX_NAME = ".spice_index.json"

#: индекс в памяти: имя модели → {"file": путь, "вид": "subckt"|"model"}
_INDEX: dict[str, dict] | None = None


def _vendor_ready() -> bool:
    """Доступен ли spicelib (вспомогательная библиотека рядом с пакетом)."""
    try:
        import spicelib.editor.spice_file      # noqa: PLC0415,F401
        return True
    except Exception:                                # noqa: BLE001
        return False


def _spice_file(path: Path):
    """Открыть файл модели средствами spicelib."""
    from spicelib.editor.spice_file import SpiceFile   # noqa: PLC0415
    return SpiceFile(path, "utf-8", errors="ignore")


def _model_names_in_file(path: Path) -> list[tuple[str, str]]:
    """Имена моделей в файле: [(имя, вид)].

    Имена подсхем spicelib отдаёт структурно. Имена карточек прибора
    (.model) spicelib держит строкой — берём из неё только имя: разбор
    грамматики делает библиотека, мы лишь читаем её результат.
    """
    out: list[tuple[str, str]] = []
    try:
        f = _spice_file(path)
    except Exception:                                # noqa: BLE001
        return out
    for name in (f.get_subcircuit_names() or []):
        out.append((str(name), "subckt"))
    for prim in (getattr(f, "netlist", None) or []):
        line = str(getattr(prim, "obj", "") or "")
        if not line.lower().lstrip().startswith(".model"):
            continue
        tokens = line.split()
        if len(tokens) >= 2:
            out.append((tokens[1], "model"))
    return out


def _index_path(lib: Path) -> Path:
    return lib / INDEX_NAME


def build_index(lib: Path, use_cache: bool = True) -> dict[str, dict]:
    """Индекс моделей внутренней базы: имя → файл и вид.

    Кэш на диске: при неизменных файлах индекс не перестраивается.
    """
    global _INDEX
    cache = _index_path(lib)
    if use_cache and cache.is_file():
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("модели"):
                _INDEX = data["модели"]
                return _INDEX
        except (OSError, json.JSONDecodeError):
            pass
    idx: dict[str, dict] = {}
    if lib.is_dir():
        for path in sorted(lib.rglob("*")):
            if path.suffix.lower() not in MODEL_SUFFIXES:
                continue
            if path.name.startswith("."):
                continue
            rel = str(path.relative_to(lib))
            for name, kind in _model_names_in_file(path):
                # первое объявление выигрывает: порядок обхода —
                # алфавитный, поведение предсказуемо
                idx.setdefault(name.lower(), {"file": rel, "вид": kind,
                                              "имя": name})
    try:
        cache.write_text(json.dumps({"модели": idx}, ensure_ascii=False),
                         encoding="utf-8")
    except OSError:
        pass
    _INDEX = idx
    return idx


def index(lib: Path) -> dict[str, dict]:
    """Индекс с кэшем в памяти."""
    if _INDEX is None:
        build_index(lib)
    return _INDEX or {}


def reset_index() -> None:
    """Сбросить индекс (база изменилась)."""
    global _INDEX
    _INDEX = None
    lib = ROOT / "design" / "spice_lib"
    cache = _index_path(lib)
    try:
        cache.unlink(missing_ok=True)
    except OSError:
        pass


def model_text(name: str, lib: Path) -> str:
    """Текст модели по имени: карточка прибора или подсхема целиком."""
    idx = index(lib)
    hit = idx.get(str(name or "").strip().lower())
    if not hit:
        return ""
    path = lib / hit["file"]
    if not path.is_file():
        return ""
    if hit.get("вид") == "subckt":
        try:
            f = _spice_file(path)
            sc = f.get_subcircuit_named(hit.get("имя") or name)
        except Exception:                            # noqa: BLE001
            return ""
        if sc is None:
            return ""
        lines = [str(getattr(p, "obj", "") or "") for p in (sc.netlist or [])]
        body = "\n".join(lines)
        return body if body.lower().lstrip().startswith(".subckt") else body
    # карточка прибора: .model ... и её продолжения (+)
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""
    lines = text.splitlines()
    out: list[str] = []
    started = False
    for ln in lines:
        s = ln.strip()
        if not started:
            if re.match(r"(?i)^\s*\.model\s+" + re.escape(str(name)) +
                        r"\s+\S", s):
                started = True
                out.append(s)
            continue
        if s.startswith("+"):
            out.append(s)
            continue
        break
    return "\n".join(out)


def subckt_decl_line(name: str, lib: Path) -> str:
    """Строка объявления подсхемы: «.subckt <имя> <узлы...>».

    Нужна настоящая строка: spicelib нормализует имя подсхемы (у
    «LM358/NS» остаётся «LM358»), а в деке прибор вызывается под
    полным именем — как оно объявлено в модели.
    """
    idx = index(lib)
    hit = idx.get(str(name or "").strip().lower())
    if not hit or hit.get("вид") != "subckt":
        return ""
    path = lib / hit["file"]
    if not path.is_file():
        return ""
    try:
        f = _spice_file(path)
        sc = f.get_subcircuit_named(hit.get("имя") or name)
    except Exception:                                # noqa: BLE001
        return ""
    if sc is None:
        return ""
    for prim in (sc.netlist or []):
        line = str(getattr(prim, "obj", "") or "")
        if line.lower().lstrip().startswith(".subckt"):
            return line.strip()
    return ""


def subckt_real_name(name: str, lib: Path) -> str:
    """Имя подсхемы, под которым её вызывает дек.

    Обычно совпадает с искомым, но у вендоров бывают суффиксы
    («LM358/NS»): в деке вызов идёт под полным именем.
    """
    line = subckt_decl_line(name, lib)
    if not line:
        return str(name or "")
    tokens = line.split()
    return tokens[1] if len(tokens) >= 2 else str(name or "")


def file_subckt_names(name: str, lib: Path) -> list[str]:
    """Все подсхемы, объявленные в файле модели.

    Модель, скачанная из интернета, часто называется иначе, чем деталь
    (LT1117-2.85, LT1117-5V0) — вызов в деке идёт под именем из файла,
    и библиотекарь должен выбрать нужный вариант.
    """
    idx = index(lib)
    hit = idx.get(str(name or "").strip().lower())
    if not hit:
        return []
    path = lib / hit["file"]
    if not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    # имена подсхем — из заголовков объявлений: по ним прибор вызывается
    # в деке, и у скачанной модели это может быть LT1117-2.85, LT1117-5V0
    out: list[str] = []
    for m in re.finditer(r"(?im)^\s*\.subckt\s+(\S+)", text):
        nm = m.group(1)
        if nm not in out:
            out.append(nm)
    return out


def subckt_pins(name: str, lib: Path) -> list[str]:
    """Имена выводов подсхемы — по объявлению .subckt.

    Порядок выводов важен: он и есть распиновка модели. Соглашение
    KiCad: выводы подсхемы перечисляются в том же порядке, что и выводы
    символа, — тогда сопоставление выводов тождественное.
    """
    idx = index(lib)
    hit = idx.get(str(name or "").strip().lower())
    if not hit or hit.get("вид") != "subckt":
        return []
    path = lib / hit["file"]
    if not path.is_file():
        return []
    try:
        f = _spice_file(path)
        sc = f.get_subcircuit_named(hit.get("имя") or name)
    except Exception:                                # noqa: BLE001
        return []
    if sc is None:
        return []
    for prim in (sc.netlist or []):
        line = str(getattr(prim, "obj", "") or "")
        if line.lower().lstrip().startswith(".subckt"):
            tokens = line.split()
            # .subckt <имя> <узлы...> [params: ...]
            nodes = tokens[2:]
            if nodes and nodes[0].lower() == "params:":
                return []
            return nodes
    return []
