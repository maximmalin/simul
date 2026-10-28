"""Инструменты агента — то, что модель вызывает как настоящие tools.

Ключевой сдвиг по сравнению с «верни JSON и я вытащу регуляркой»:
модель получает **типизированные инструменты** и сама вызывает их,
а ответ приходит уже разобранным. Это не косметика:

* мусор в ответе (````json` fences, пояснения перед JSON) больше не ломает
  разбор — там нечего разбирать;
* провайдер проверяет аргументы по схеме и не даст выдумать несуществующую
  цель;
* один и тот же набор инструментов работает и с облаком, и с Ollama,
  потому что обе понимают поле `tools` в OpenAI-совместимом API.

Инструменты объявлены один раз здесь и используются и планировщиком,
и агентным циклом, и MCP-сервером — одна правда, три потребителя.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Callable

from .profile import collect
from .types import Project

#: максимум символов в аргументе — чтобы модель не утащила весь лог
MAX_ARG = 4000


# ==========================================================================
#  Схемы
# ==========================================================================
def _schema(props: dict[str, dict], required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required,
            "additionalProperties": False}


# ==========================================================================
#  Уровни инструментов
# ==========================================================================
#  Заимствовано у WebBrain (браузерный агент) — там это называется
#  compact / mid / full и решает ту же задачу: слабой локальной модели
#  нельзя показывать все инструменты сразу. Модель на 3B, увидев шесть
#  схем, начинает выбирать неверно или вообще не вызывает ничего.
#
#  compact — два инструмента, которых хватает для большинства заказов;
#  mid    — плюс чтение артефактов и уточнение у человека;
#  full   — всё, включая describe_target.
# ==========================================================================
TIERS: dict[str, tuple[str, ...]] = {
    "compact": ("list_targets", "submit_plan", "run_target",
                "write_artifact", "kicad_libs"),
    "mid": ("list_targets", "submit_plan", "run_target",
            "read_artifacts", "write_artifact", "kicad_libs",
            "part_search"),
    "full": ("list_targets", "describe_target", "submit_plan",
             "run_target", "read_artifacts", "write_artifact",
             "kicad_libs", "part_search"),
    "libs": ("kicad_libs", "part_search"),
}

#: уровень по умолчанию; переопределяется KR_TOOL_TIER
DEFAULT_TIER = os.environ.get("KR_TOOL_TIER", "mid").strip().lower()
if DEFAULT_TIER not in TIERS:
    DEFAULT_TIER = "mid"


def tier_names(tier: str = "") -> tuple[str, ...]:
    return TIERS.get((tier or DEFAULT_TIER).lower(), TIERS[DEFAULT_TIER])


def tool_schemas(project: Project, tier: str = "") -> list[dict]:
    """Объявления инструментов в формате OpenAI, по уровню.

    Аргументы приходят уже разобранными и проверенными по схеме, поэтому
    мусор в ответе модели больше не ломает разбор.
    """
    return [s for s in _ALL_SCHEMAS(project) if s["function"]["name"] in
            tier_names(tier)]


def _ALL_SCHEMAS(project: Project) -> list[dict]:
    n = len(project.buildable())
    return [
        {
            "type": "function",
            "function": {
                "name": "list_targets",
                "description": (
                    f"Что можно собрать в проекте «{project.name}». "
                    f"Доступно целей: {n}. Вызывай это, если не знаешь "
                    "список целей или не уверен в названии."),
                "parameters": _schema({}, []),
            },
        },
        {
            "type": "function",
            "function": {
                "name": "describe_target",
                "description": "Описание цели и от чего она зависит.",
                "parameters": _schema(
                    {"name": {"type": "string",
                              "description": "Имя цели ровно как в list_targets"}},
                    ["name"]),
            },
        },
        {
            "type": "function",
            "function": {
                "name": "submit_plan",
                "description": (
                    "Зафиксировать план: какие цели выполнить. Выбирай "
                    "минимальный достаточный набор. Если выбрана цель "
                    "верхнего уровня, её зависимости соберутся сами — "
                    "перечислять их отдельно не нужно."),
                "parameters": _schema(
                    {"targets": {"type": "array",
                                 "items": {"type": "string"},
                                 "description": "Имена целей сборки"},
                     "intent": {"type": "string",
                                "description": "Короткий тег: что делает заказ"}},
                    ["targets"]),
            },
        },
        {
            "type": "function",
            "function": {
                "name": "run_target",
                "description": (
                    "Собрать одну цель. Возвращает код возврата и хвост "
                    "вывода. Долгая операция."),
                "parameters": _schema(
                    {"name": {"type": "string", "description": "Имя цели"}},
                    ["name"]),
            },
        },
        {
            "type": "function",
            "function": {
                "name": "read_artifacts",
                "description": (
                    "Прочитать результаты расчётов: числовые значения из "
                    "файлов сборки. Отсюда берутся числа для отчёта."),
                "parameters": _schema(
                    {"limit": {"type": "integer",
                               "description": "Сколько файлов прочитать",
                               "minimum": 1, "maximum": 20}},
                    []),
            },
        },
        {
            "type": "function",
            "function": {
                "name": "write_artifact",
                "description": (
                    "Записать текстовый файл в песочнице заказа: "
                    "спецификацию схемы design/schematic.json, структурную "
                    "схему design/structure.drawio, расчёты design/*.json. "
                    "Формат schematic.json: parts (ref, lib_id, value), "
                    "nets (name, conns=[[ref,pin],…], power для шин питания), "
                    "blocks (title, sub, refs). Имена символам и пинам брать "
                    "инструментом kicad_libs, не придумывать."),
                "parameters": _schema(
                    {"path": {"type": "string",
                              "description": "путь внутри песочницы, "
                                             "напр. design/schematic.json"},
                     "content": {"type": "string",
                                 "description": "содержимое файла целиком"}},
                    ["path", "content"]),
            },
        },
        {
            "type": "function",
            "function": {
                "name": "kicad_libs",
                "description": (
                    "Справочник реальных библиотек KiCad: символы, их выводы "
                    "и корпуса. action=pins → выводы lib_id; action=search → "
                    "символы библиотеки по фрагменту имени; action=footprints "
                    "→ проверка корпуса; action=libs → список библиотек. "
                    "action=fp_search → поиск корпуса по подстроке. "
                    "action=create_symbol → создать символ в local_symbols "
                    "(symbol_name, symbol_value, symbol_footprint). "
                    "Всегда сверяйте сюда каждое имя символа из спецификации."),
                "parameters": _schema(
                    {"action": {"type": "string",
                                "enum": ["libs", "search", "pins",
                                         "footprints", "fp_search",
                                         "create_symbol"],
                                "description": "тип запроса"},
                     "lib": {"type": "string",
                             "description": "имя библиотеки: Device, Relay реестр"},
                     "lib_id": {"type": "string",
                                "description": "для pins: Device:LED"},
                     "pattern": {"type": "string",
                                 "description": "фильтр поиска (для search/fp_search): подстрока в имя"},
                     "fp": {"type": "string",
                            "description": "для footprints: lib:Имя"},
                     "symbol_name": {"type": "string",
                                     "description": "для create_symbol: имя символа (Q5001)"},
                     "symbol_value": {"type": "string",
                                      "description": "для create_symbol: значение символа (BSS138)"},
                     "symbol_footprint": {"type": "string",
                                          "description": "для create_symbol: корпус (Package_TO_SOT_SMD:SOT-23)"}},
                    ["action"]),
            },
        },
        {
            "type": "function",
            "function": {
                "name": "part_search",
                "description": (
                    "Поиск реальных деталей и готовых модулей во внешнем "
                    "каталоге поставщика (локальный срез JLC/LCSC): "
                    "LCSC-код, MPN, производитель, описание, корпус, "
                    "запас. Модули ищи по функции («TP4056 charging», "
                    "«BMS», «buck module»), детали — по имени. Найденный "
                    "LCSC-код передавай part_fetch за символом и "
                    "документом: выдуманных кодов не бывает."),
                "parameters": _schema(
                    {"query": {"type": "string",
                               "description": "функция или имя/MPN"},
                     "limit": {"type": "integer",
                               "description": "кандидатов, 1..20 (по "
                                              "умолчанию 8)"}},
                    ["query"]),
            },
        },
        {
            "type": "function",
            "function": {
                "name": "ask_human",
                "description": (
                    "Остановиться и спросить оператора. Используй, когда "
                    "дальше идти нельзя: нужен доступ, недостаточно данных "
                    "или решение инженера. Граф встанет и продолжится после "
                    "ответа."),
                "parameters": _schema(
                    {"question": {"type": "string",
                                  "description": "Вопрос оператору"}},
                    ["question"]),
            },
        },
    ]


# ==========================================================================
#  Реализации
# ==========================================================================
#: каталог поставщика: локальный срез JLC/LCSC (обновляется jlcparts)
_CATALOG_DB = os.environ.get(
    "KR_JLCPARTS_DB", "/mnt/ext4data/jlcparts/cache/cache.sqlite3")


def _catalog_search(query: str, limit: int = 8) -> str:
    """Реальные детали внешнего каталога по словам запроса.

    Это «внешний каталог» агента: готовые модули (платы зарядки, BMS,
    DC-DC) и обычные детали ищутся по функции или имени. Ничего не
    выдумывается — возвращаются только записи локального среза каталога
    поставщика; LCSC-код дальше уходит в part_fetch за символом и
    документом.
    """
    import sqlite3

    words = [w for w in re.split(r"\s+", str(query or "").strip())
             if w][:6]
    if not words:
        return ("нужен текстовый запрос: функция или имя "
                "(«TP4056 charging», «buck module», «STM32F103»)")
    if not Path(_CATALOG_DB).is_file():
        return (f"каталог деталей недоступен ({_CATALOG_DB} не найден) — "
                "SKU бери у библиотекаря, MPN указывай честно")
    try:
        n = max(1, min(int(limit or 8), 20))
    except (TypeError, ValueError):
        n = 8
    where = " AND ".join(
        "(description LIKE ? OR mfr LIKE ? OR manufacturer LIKE ? "
        "OR category LIKE ? OR subcategory LIKE ?)" for _ in words)
    args: list[str] = []
    for w in words:
        pat = f"%{w}%"
        args += [pat] * 5
    try:
        con = sqlite3.connect(f"file:{_CATALOG_DB}?mode=ro", uri=True)
        try:
            rows = con.execute(
                "SELECT lcsc, mfr, manufacturer, description, package, "
                "stock, datasheet FROM jlc_components WHERE " + where
                + " ORDER BY CASE WHEN stock > 0 THEN 0 ELSE 1 END, "
                  "stock DESC, lcsc ASC LIMIT ?",
                (*args, n)).fetchall()
        finally:
            con.close()
    except Exception as exc:                                # noqa: BLE001
        return f"поиск в каталоге не удался: {exc}"
    if not rows:
        return (f"в каталоге нет деталей по «{query}»: переформулируй "
                "(функция/MPN) или собери узел из компонентов")
    out = [f"Каталог поставщика (JLC/LCSC): по «{query}» — "
           f"{len(rows)} кандидатов:"]
    for lcsc, mfr, man, desc, pkg, stock, ds in rows:
        line = (f"- C{lcsc} | {str(mfr).strip()} | "
                f"{str(man).strip() or '—'} | {str(pkg).strip()} | "
                f"запас {stock} | {str(desc).strip()[:80]}")
        if str(ds or "").strip():
            line += f" | datasheet: {str(ds).strip()}"
        out.append(line)
    out.append("Кандидат — не решение: сверь параметры с требованиями "
               "блока и возьми документ (part_fetch / библиотекарь).")
    return "\n".join(out)


def make_bindings(project: Project, run_one: Callable[[str], dict],
                  on_plan: Callable[[list[str], str], None] | None = None,
                  tier: str = "") -> dict[str, Callable[..., str]]:
    """Собрать функции инструментов, привязанные к проекту."""
    from .reporter import collect_facts

    def list_targets() -> str:
        rows = []
        for t in project.buildable():
            dep = f" (тянет: {', '.join(t.depends_on[:5])})" if t.depends_on else ""
            rows.append(f"- {t.name}: {(t.title or '—')}{dep}")
        head = f"Проект «{project.name}». Собираемые цели:\n"
        return head + "\n".join(rows[:120])

    def describe_target(name: str) -> str:
        t = project.target(name)
        if not t:
            avail = ", ".join(x.name for x in project.buildable()[:20])
            return (f"Цели «{name}» нет. Доступные: {avail}")
        try:
            from .runner import get as runner_get
            cmd = (runner_get(t.runner).command(Path(project.root), t)
                   if t.runner else runner_get(project.runner)
                   .command(Path(project.root), t))
        except Exception as exc:                                # noqa: BLE001
            cmd = f"(команду собрать не удалось: {exc})"
        return (f"Цель: {t.name}\nописание: {t.title or '—'}\n"
                f"команда: {cmd}\nзависит: {', '.join(t.depends_on) or '—'}\n"
                f"служебная: {'да' if t.service else 'нет'}")

    def submit_plan(targets: list[str], intent: str = "") -> str:
        by_name = project.by_name()
        good = [t for t in targets if t in by_name]
        bad = [t for t in targets if t not in by_name]
        if not good:
            avail = ", ".join(x.name for x in project.buildable()[:25])
            return (f"Ни одна цель не подошла. Несуществующие: {bad}. "
                    f"Доступные: {avail}")
        good = _minimise(good, project)
        # Обязательный инженерный цикл: kicad → all → test — сами,
        # независимо от того, что назвала модель.
        from .planner import ensure_cycle                      # поздний имп.
        good, added = ensure_cycle(good, by_name)
        if on_plan:
            on_plan(good, intent or "llm")
        out = f"План принят: {good}"
        if added:
            out += (f"\nПлан дополнен обязательными этапами цикла: {added}"
                    f" (порядок: kicad → all → test)")
        if bad:
            out += f"\nОтброшены как несуществующие: {bad}"
        return out

    def run_target(name: str) -> str:
        if project.target(name) is None:
            return f"Цели «{name}» нет — собирать нечего"
        res = run_one(name)
        head = f"{name}: код {res.get('exit_code')} за {res.get('duration_s', 0)}s"
        tail = res.get("log") or []
        if res.get("status") != "done" and tail:
            return head + "\nпоследние строки:\n" + "\n".join(tail[-10:])
        return head

    def read_artifacts(limit: int = 6) -> str:
        facts, files = collect_facts(project)
        if not files:
            return "Артефактов нет — сборка ещё не выполнялась"
        return f"Файлов с результатами: {len(files)}\n\n{facts[:MAX_ARG]}"

    def write_artifact(path: str, content: str) -> str:
        """Записать файл агента в песочницу (design/, out/, scripts/)."""
        from pathlib import Path as _Path
        from .engine import current_workdir                # поздний импорт

        work = current_workdir()
        base = _Path(work) if work else Path(project.root)
        base = base.resolve()

        rel = str(path or "").strip().lstrip("/")
        if not rel or ".." in rel.split("/") or rel.startswith("/"):
            return ("Путь должен быть относительным внутри песочницы."
                    if work else
                    "Песочница не активна: запись запрещена, стартуйте заказ")
        if not re.search(r"\.(json|md|drawio|xml|txt|yaml|yml|scm|skidl)$",
                         rel, re.I):
            return ("Расширение файла не разрешено: пишите .json .md "
                    ".drawio .xml .txt .yaml .scm")
        if len(content) > 300_000:
            return "Содержимое слишком большое (>300 КБ) — разделите файл"
        dst = (base / rel).resolve()
        if not dst.is_relative_to(base):
            return "Путь выходит за пределы песочницы"
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(content, encoding="utf-8")
        size = dst.stat().st_size
        return f"записано: {rel} ({size:,} байт)"

    def kicad_libs(action: str, lib: str = "", lib_id: str = "",
                   pattern: str = "", fp: str = "",
                   symbol_name: str = "", symbol_value: str = "",
                   symbol_footprint: str = "") -> str:
        """Ответы на запросы к реальным библиотекам KiCad.

        action=create_symbol — создать новый символ в локальной библиотеке.
        Требуется symbol_name (имя символа), symbol_value (значение/номер
        детали) и symbol_footprint (корпус). Символ создаётся в каталоге
        local_symbols/ с базовыми выводами для транзистора (G, D, S).
        """
        import json as _json
        import sys as _sys
        from pathlib import Path as _Path

        root = _Path(__file__).resolve().parents[2]
        kit = root / "hardware" / "kicad"
        sym = _Path("/usr/share/kicad/symbols")
        try:
            _sys.path.insert(0, str(kit))
            import kicad_lib_brig as _K                 # noqa: PLC0415
        except ImportError:
            _K = None

        def names_of_file(path: _Path) -> list[str]:    # top-level symbol "X"
            if not path.is_file():
                return []
            node = _K.parse_sexp(path.read_text(encoding="utf-8",
                                                errors="ignore"))
            out = []
            for s in node.items:
                if getattr(s, "items", None) and s.head == "symbol":
                    n = s.items[1] if len(s.items) > 1 else ""
                    if not str(n).endswith(("_0_0", "_0_1", "_1_1")):
                        out.append(str(n))
            return out

        try:
            if action == "libs":
                rows = sorted(p.stem for p in sym.glob("*.kicad_sym")
                              if re.search(pattern or ".", p.stem, re.I))
                return _json.dumps({"count": len(rows),
                                    "libs": rows[:80]}, ensure_ascii=False)
            if action == "search":
                path = sym / f"{lib}.kicad_sym"
                if _K is None or not path.is_file():
                    return f"библиотека {lib} не найдена; проверьте через action=libs"
                rows = [n for n in names_of_file(path)
                        if re.search(pattern or ".", n, re.I)]
                return _json.dumps({"lib": lib, "count": len(rows),
                                    "symbols": rows[:60]}, ensure_ascii=False)
            if action == "pins":
                if _K is None:
                    return "парсер библиотек недоступен"
                try:
                    S = _K.lib_symbol(lib_id)
                except Exception as exc:                       # noqa: BLE001
                    return _json.dumps(
                        {"error": f"{lib_id}: {exc}"}, ensure_ascii=False)
                out: dict[str, list[str]] = {}
                for unit in (1, 2, 3, 4):
                    try:
                        pos = _K.pin_positions(S, unit)
                    except Exception:                          # noqa: BLE001
                        continue
                    if pos:
                        out[f"unit{unit}"] = sorted(str(x) for x in pos)
                return _json.dumps({"lib_id": lib_id, "pins": out}
                                   if out else
                                   {"error": f"пины {lib_id} не найдены"},
                                   ensure_ascii=False)
            if action == "footprints":
                if ":" not in fp:
                    return ("формат корпуса: «Библиотека:Имя»; для поиска "
                            "кандидатов используйте action=fp_search, "
                            "pattern=текст")
                FPL = ["/usr/share/kicad/footprints",
                       str(root / "local_footprints")]
                gl, gn = fp.split(":", 1)
                hits = []
                for base in FPL:
                    if (_Path(base) / f"{gl}.pretty"
                            / f"{gn}.kicad_mod").is_file():
                        hits.append(base)
                return _json.dumps({"footprint": fp, "exists": bool(hits),
                                    "in": hits}, ensure_ascii=False)
            if action == "fp_search":
                q = (pattern or fp or "").lower().strip()
                if not q:
                    return ("укажите pattern (например 'SOT 23', 'battery 18650', "
                            "'R 0603')")
                FPL = ["/usr/share/kicad/footprints",
                       str(root / "local_footprints")]
                rows = []
                for base in FPL:
                    basep = _Path(base)
                    if not basep.is_dir():
                        continue
                    for pretty in basep.glob("*.pretty"):
                        for mod in pretty.glob("*.kicad_mod"):
                            name = mod.stem
                            hay = (pretty.name + " " + name).lower()
                            if q in name.lower().replace("_", " ") or q in hay:
                                rows.append({"library": pretty.stem,
                                             "footprint": name,
                                             "qualified": f"{pretty.stem}:{name}"})
                                if len(rows) >= 60:
                                    break
                        if len(rows) >= 60:
                            break
                return _json.dumps({"query": q, "count": len(rows),
                                    "footprints": rows}, ensure_ascii=False)
            if action == "create_symbol":
                # Create a new symbol in local_symbols library
                if not symbol_name:
                    return _json.dumps({"error": "symbol_name обязателен"}, ensure_ascii=False)
                local_dir = _Path("/mnt/ext4data/kristall-radio/local_symbols")
                local_dir.mkdir(parents=True, exist_ok=True)
                symbol_file = local_dir / f"{symbol_name}.kicad_sym"
                # Check if symbol already exists
                if symbol_file.exists():
                    return _json.dumps({"created": False,
                                        "path": str(symbol_file),
                                        "message": "символ уже существует"},
                                       ensure_ascii=False)
                symbol_lines = [
                    f'(symbol "{symbol_name}"',
                    '  (in_bus)',
                    '  (on_board)',
                    '  (dnp no)',
                    f'  (property "Reference" "U")',
                    f'  (property "Value" "{symbol_value}")',
                    f'  (property "Footprint" "{symbol_footprint}")',
                    '  (property "Datasheet" "~")',
                    '  (pins',
                    '    (pin passive line (at -2.54 0 90) (length 1.27) (name "1"))',
                    '    (pin passive line (at 0 2.54 180) (length 1.27) (name "2"))',
                    '    (pin passive line (at 0 -2.54 0) (length 1.27) (name "3"))',
                    '  )',
                    ')',
                ]
                symbol_text = '\n'.join(symbol_lines) + '\n'
                try:
                    symbol_file.write_text(symbol_text, encoding="utf-8")
                    return _json.dumps({"created": True,
                                        "path": str(symbol_file),
                                        "symbol": symbol_name,
                                        "footprint": symbol_footprint},
                                       ensure_ascii=False)
                except Exception as exc:
                    return _json.dumps({"error": f"не удалось создать символ: {exc}"},
                                       ensure_ascii=False)
            return "action: libs | search | pins | footprints | fp_search | create_symbol"
        except Exception as exc:                              # noqa: BLE001
            return f"ошибка справочника: {exc}"

    def ask_human(question: str) -> str:
        # фактический останов делает узел, читающий этот маркер;
        # здесь только фиксируем намерение
        return f"__ASK_HUMAN__{question}"

    all_bindings = {
        "list_targets": list_targets,
        "describe_target": describe_target,
        "submit_plan": submit_plan,
        "run_target": run_target,
        "read_artifacts": read_artifacts,
        "write_artifact": write_artifact,
        "kicad_libs": kicad_libs,
        "part_search": _catalog_search,
        "ask_human": ask_human,
    }
    # Уровень ограничивает не только показ схем, но и вызов: скрытый
    # инструмент недоступен даже если модель назвала его напрямую.
    # Иначе «компактный» набор был бы украшением.
    keep = tier_names(tier)
    return {k: v for k, v in all_bindings.items() if k in keep}


def _minimise(targets: list[str], project: Project) -> list[str]:
    """Убрать цели, покрытые выбранной верхнеуровневой.

    Считается транзитивно — см. объяснение в planner._covered_by.
    """
    from .planner import _covered_by
    covered = _covered_by(targets, project)
    return [t for t in targets if t not in covered]


# ==========================================================================
#  Вызов
# ==========================================================================
def invoke(bindings: dict[str, Callable[..., str]], name: str,
           args: dict[str, Any]) -> str:
    """Вызвать инструмент по имени. Ошибки не поднимаем — отдаём текстом."""
    fn = bindings.get(name)
    if fn is None:
        return (f"Инструмента «{name}» нет. Доступные: "
                f"{', '.join(sorted(bindings))}")
    try:
        out = fn(**args) if args else fn()
    except TypeError as exc:
        return f"Неверные аргументы для «{name}»: {exc}"
    except Exception as exc:                                    # noqa: BLE001
        return f"Инструмент «{name}» упал: {exc}"
    return str(out)[:MAX_ARG]


ASK_MARKER = "__ASK_HUMAN__"


def is_ask_human(text: str) -> str | None:
    """Вопрос оператору, если инструмент его задал."""
    if text.startswith(ASK_MARKER):
        return text[len(ASK_MARKER):].strip()
    return None

# ==========================================================================
#  SPICE-библиотека (добавлено для совместимости с _stage_spice_models)
# ==========================================================================
def _spice_lib_dir() -> "Path":
    """Каталог с моделями SPICE: песочница заказа или корень проекта."""
    try:
        from . import engine as _eng
        wd = _eng.current_workdir()
    except Exception:  # noqa: BLE001
        wd = None
    if wd:
        p = Path(wd) / "design" / "spice_lib"
        if p.is_dir():
            return p
    # fallback: корень проекта
    from .types import Project as _P  # noqa: F401
    root = Path(__file__).resolve().parents[2]
    return root / "design" / "spice_lib"


# module-level alias used as T.SPICE_LIB_DIR (Path-like). We expose a
# lightweight proxy object so attribute access returns a fresh Path.
class _SpiceLibDirProxy:
    def __fspath__(self):
        return str(_spice_lib_dir())
    def __str__(self):
        return str(_spice_lib_dir())
    def __truediv__(self, other):
        return _spice_lib_dir() / other
    def is_dir(self):
        return _spice_lib_dir().is_dir()
    def rglob(self, *a, **kw):
        return _spice_lib_dir().rglob(*a, **kw)


SPICE_LIB_DIR = _SpiceLibDirProxy()


def _spice_model_text(model: str) -> str:
    """Текст модели SPICE по имени (через spice_models)."""
    try:
        from . import spice_models as SM
        return SM.model_text(str(model or "").strip(), _spice_lib_dir())
    except Exception:  # noqa: BLE001
        return ""
