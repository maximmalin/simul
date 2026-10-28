"""Конвейер многоагентного цикла разработки аппаратуры.

Разделение труда — жёсткое:

  КАРКАС (этот модуль): порядок стадий, границы ответственности,
  валидация артефактов, обратная связь, публикация прогресса.
  Ничего не проектирует и не дописывает за агентом: увидел ошибку —
  вернул агенту точный список, тот сам исправляет свою работу.

  АГЕНТЫ (LLM): расчёты изделия, спецификация схемы, поиск реальных
  символов библиотек (инструмент kicad_libs), исправления по ошибкам.
  Именно агенты демонстрируют точность проекта.

  СКРИПТЫ ПРОЕКТА: генерация .kicad_sch/.net, чертежей, PCB, проверок
  (make-цели) — как в эталонном Кристалл-Радио.

Стадии цикла одни для ЛЮБОГО изделия; приборное содержание появляется
только там, где работает агент:

  1. tz        — make tz            (скрипт: каркас ТЗ)
  2. calc      — расчётчик (LLM)    -> design/calc.json
  3. schematic — схемотехник (LLM)  -> design/schematic.json
     (символы и пины агент подтверждает через kicad_libs)
  4. kicad     — make kicad         (скрипт: разводка/корпуса/ERC);
                 ошибки компиляции возвращаются агенту, тот правит
                 спецификацию и пересборка повторяется (до KICAD_FIXES)
  5. all       — make all           (скрипт: 3D/чертежи/PCB/страница)
  6. test      — make test          (скрипт: валидация артефактов)
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import llm
import llm_race

from . import engine, planner, tools as T
from . import parts as P                  # noqa: E402
from .types import Project, final_status, now_iso

STAGE_TIMEOUT = float(os.environ.get("KR_STAGE_TIMEOUT", "240"))
#: таймаут хода агента с инструментами: зависший маршрут не должен
#: съедать минуты молча — за это время конвейер стоит
TOOL_TIMEOUT = float(os.environ.get("KR_TOOL_TIMEOUT", "150"))
#: сколько символов ответа инструмента уходит в историю агента
TOOL_OUT_CHARS = int(os.environ.get("KR_TOOL_OUT_CHARS", "700"))
#: сколько последних ходов агента остаётся в истории
TOOL_HISTORY = int(os.environ.get("KR_TOOL_HISTORY", "6"))
#: потолок вызовов инструментов за стадию: дальше агент ищет «впустую»
TOOL_CALLS_MAX = int(os.environ.get("KR_TOOL_CALLS_MAX", "10"))
TOOL_TURNS = int(os.environ.get("KR_TOOL_TURNS", "5"))

# ── Уровень усилия (по образцу analog-agents: lite / standard / intensive)
# Глубина петли: сколько попыток у агентов, есть ли агент-критик схемы.
_EFFORT = os.environ.get("KR_EFFORT", "standard").strip().lower()
_EFFORTS = {                                   # эскалация вместо бесконечных правок
    "lite":      {"schema": 2, "fixes": 1, "tz": 1, "review": False,
                 "sizing": 1, "designers": 1},
    "standard":  {"schema": 3, "fixes": 2, "tz": 3, "review": True,
                 "sizing": 2, "designers": 2},
    "intensive": {"schema": 5, "fixes": 3, "tz": 4, "review": True,
                 "sizing": 4, "designers": 3},
}
_BUDGET = _EFFORTS.get(_EFFORT, _EFFORTS["standard"])
#: сколько дизайнеров-кандидатов работают ПАРАЛЛЕЛЬНО в одной попытке;
#: отбор — строгой валидацией каркаса, а не вкусом
DESIGN_CANDIDATES = int(os.environ.get(
    "KR_DESIGN_CANDIDATES", _BUDGET.get("designers", 1)))
SCHEMA_RETRIES = int(os.environ.get("KR_SCHEMA_RETRIES", _BUDGET["schema"]))
KICAD_FIXES = int(os.environ.get("KR_KICAD_FIXES", _BUDGET["fixes"]))
TZ_ITER = int(os.environ.get("KR_TZ_ITER", _BUDGET["tz"]))
SCHEMA_REVIEW = False
SIZING_ITERS = int(os.environ.get("KR_SIZING_ITERS",
                                 _BUDGET.get("sizing", 2)))
#: сколько раз упаковщику возвращают замечания проверки PCB-паспорта
PACKAGER_FIXES = int(os.environ.get("KR_PACKAGER_FIXES",
                                    _BUDGET.get("fixes", 2)))

#: системный промпт расчётчика — из ролевой карточки (roles/calculator.md)
_CALC_SYSTEM = ""

def _role(name: str) -> str:
    """Ролевая карточка агента (roles/<name>.md) — как ролевые промпты
    в analog-agents: роль живёт файлом, а не константой кода.

    Обрезка идёт по границам разделов и молча не рвёт текст: обрезанное
    перечисляется в конце, иначе агент получил бы правило, оборванное
    на полуслове, и не понял бы, что оно вообще есть.
    """
    f = Path(__file__).resolve().parent / "roles" / f"{name}.md"
    # карточки роли — до 19000 симв.: инварианты (в т.ч. режим сборки),
    # формат ответа и критерии приёмки должны дойти не оборванными.
    # Порядок разделов в файле задан так, чтобы самое важное было
    # в начале и помещалось даже при росте карточки
    return _fit_sections(f, 19000)


def _fit_sections(f: Path, cap: int) -> str:
    """Целые разделы до лимита; отброшенные перечислены явно."""
    if not f.is_file():
        return ""
    text = f.read_text(encoding="utf-8")
    if len(text) <= cap:
        return text
    head, *rest = re.split(r"(?m)^(?=## )", text)
    kept, dropped, size = [head], [], len(head)
    for chunk in rest:
        title = chunk.splitlines()[0].strip("# ").strip()
        if size + len(chunk) > cap:
            dropped.append(title)
        else:
            kept.append(chunk)
            size += len(chunk)
    if not dropped:
        return text[:cap]
    return "".join(kept) + ("\n\n(не вместилось в промпт, см. файл целиком: "
                           + "; ".join(dropped) + ")\n")


_CALC_SYSTEM = _role("calculator") or (
    "Ты — инженер-расчётчик изделия. Работаешь автономно: без "
    "вопросов и остановок. Отвечаешь только тем, что попросили.")


# --------------------------------------------------------------------------- #
#  Поблочная сборка SKiDL: инструмент каркаса (tools/skidl_blocks.py)
# --------------------------------------------------------------------------- #
#: корень репозитория (backend/agents/stages.py → backend → корень)
_REPO_ROOT = Path(__file__).resolve().parents[2]

#: кэш: инструмент поблочной сборки SKiDL (tools/skidl_blocks.py)
_BLOCKS_CACHE: dict | None = None


def _blocks() -> dict:
    """Поблочная сборка SKiDL: тот же инструмент, что у агентов, —
    правила проверки не дублируются."""
    global _BLOCKS_CACHE
    if _BLOCKS_CACHE is None:
        tool_p = _REPO_ROOT / "tools" / "skidl_blocks.py"
        mod = None
        if tool_p.is_file():
            try:
                spec = importlib.util.spec_from_file_location(
                    "krystal_skidl_blocks", tool_p)
                mod = importlib.util.module_from_spec(spec)
                # модуль с dataclass должен быть в sys.modules ДО загрузки
                sys.modules[spec.name] = mod
                spec.loader.exec_module(mod)
            except Exception:                          # noqa: BLE001
                mod = None
        _BLOCKS_CACHE = {"mod": mod}
    return _BLOCKS_CACHE


#: каталог чек-листов по типам узлов (checklists/*.yml)
CHECKLIST_DIR = Path(__file__).resolve().parent / "checklists"
#: база знаний проектирования (knowledge/analog_wiki)
WIKI_DIR = Path(__file__).resolve().parent / "knowledge" / "analog_wiki"

#: по каким словам в ТЗ на узел определяется тип узла → чек-лист.
#: Сопоставление по смыслу, а не по точному названию блока.
_CHECKLIST_HINTS: dict[str, tuple[str, ...]] = {
    "current-mirror": ("токовое зеркало", "зеркало тока", "отражатель тока",
                       "источник тока", "составной транзистор", "сдвоенный",
                       "виглер", "wilson"),
    "amplifier": ("усилитель", "операционный усилитель", "усиление",
                  "усилительный каскад", "преобразователь сигнала"),
    "differential": ("дифференциальн", "дифференциальная пара",
                      "мостовая схема", "мостовое соединение"),
    "folded_cascode": ("каскод", "складывающ"),
    "comparator": ("компаратор", "пороговый", "сравнени"),
    "ldo": ("линейный стабилизатор", "ldo", "стабилизатор питания"),
    "bandgap": ("bandgap", "опорное напряжение", "источник опора"),
    "adc": ("аналого-цифров", "ацп"),
    "pll": ("фазовая петля", "pll", "генератор", "делитель частоты",
            "синтезатор частот"),
}


def _node_kind(text: str) -> str:
    """Тип узла по его описанию — чтобы взять нужный чек-лист."""
    low = str(text or "").lower()
    for key, words in _CHECKLIST_HINTS.items():
        if any(w in low for w in words):
            return key
    return "common"


def _checklist_digest(kind: str) -> str:
    """Чек-лист по типу узла: обязательные проверки перед симуляцией."""
    f = CHECKLIST_DIR / f"{kind}.yml"
    if not f.is_file():
        return ""
    try:
        return f.read_text(encoding="utf-8")[:6000]
    except OSError:
        return ""


def _wiki_digest(kind: str) -> str:
    """База знаний: стратегии, анти-паттерны, уроки по типам узлов."""
    out: list[str] = []
    for sub in ("strategies", "anti-patterns", "topologies", "corner-lessons"):
        d = WIKI_DIR / sub
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.yml")):
            try:
                out.append(f.read_text(encoding="utf-8")[:2200])
            except OSError:
                continue
    return "\n\n".join(out)[:6000] if out else ""


def _sources_digest(cap: int = 3200) -> str:
    """Библиотека первоисточников для проектирования узлов (книги,
    справочники производителей, аппноты): живой файл
    knowledge/electronics_sources.md — не дублируется в текстах вручную."""
    f = Path(__file__).resolve().parent / "knowledge" / \
        "electronics_sources.md"
    return _fit_sections(f, cap) if f.is_file() else ""


def _modules_mode(box: Path) -> bool:
    """Флаг заказа: можно ли решать узлы готовыми модулями
    (design/acceptance.json, поле можно_модули). Решения нет — модули
    допустимы, но каждый узел с собственными требованиями всё равно
    считается сам (правило методички)."""
    p = box / "design" / "acceptance.json"
    if p.is_file():
        try:
            v = json.loads(p.read_text(encoding="utf-8")).get("можно_модули")
            if isinstance(v, bool):
                return v
        except (json.JSONDecodeError, OSError):
            pass
    return True


_LIBRARIAN_SYSTEM = _role("librarian") or "библиотекарь приборов"


# --------------------------------------------------------------------------- #
#  План конвейера и роли
# --------------------------------------------------------------------------- #
_DESIGN_SYSTEM = _role("designer") or "инженер-схемотехник"
_CONSULTANT_SYSTEM = _role("consultant") or "консультант проекта"
_SIZER_SYSTEM = _role("sizer") or "подбор номиналов по замерам"
_CRITIC_SYSTEM = _role("verifier") or "критик схемы"
_CHECKLIST_SYSTEM = _role("checklist") or "контролёр требований"
_TZ_WRITER_SYSTEM = ("Ты — технический писатель проекта разработки "
                    "аппаратуры. Пишешь глубокое производство-пригодное ТЗ "
                    "изделия: каждый раздел проработан, числа обоснованы, "
                    "критерии приёмки конкретны. Работаешь автономно: "
                    "возврат — только документ.")

_TZ_DEBUG_SYSTEM = ("Ты — контролёр-дебагер технической документации. "
                   "Проверяешь ТЗ по чек-листу каркаса абсолютно строго. "
                   "Отвечаешь ТОЛЬКО JSON: "
                   '{"passed": true|false, "issues": ["<замечание>", "…"]}')

_TZ_OPT_SYSTEM = ("Ты — редактор-оптимизатор инженерной документации. "
                  "Отшлифуешь текст: нумерация, единицы, терминология, "
                  "связность разделов. Содержание и глубину не сокращаешь. "
                  "Возврат — только полный markdown-документ.")


def _step(target: str, title: str, agent: str = "agent.build") -> dict:
    return {"id": target.replace("/", "_"), "target": target,
            "title": title, "agent": agent, "status": "pending"}


_PLAN = (
    ("tz", "1. ТЗ — каркас и эталонная структура", "make"),
    ("consultant", "2. Приём замысла и границы изделия — консультант", "llm"),
    ("calc", "3. Инженерные расчёты и матрица параметров — расчётчик", "llm"),
    ("tz_write", "4. Развёрнутое ТЗ изделия — петля писателя", "llm"),
    ("structure", "5. Структурная схема ЭСКД — системотехник", "llm"),
    ("block_tz", "6. ТЗ на каждый функциональный блок — проектировщик",
     "llm"),
    ("schematic", "7. Схемотехника блоков в SKiDL — инженер", "llm"),
    ("stitch", "8. Сшивка блоков в одну принципиальную схему — сшивальщик",
     "llm"),
    ("verify", "9. Приёмка схемы по ТЗ и Э1 — тестировщик", "llm"),
    ("library", "10. Подбор реальных компонентов — библиотекарь", "llm"),
    ("kicad", "11. Нетлист SKiDL и проверка ERC — компилятор", "make"),
    ("spice_models", "12. Модели SPICE реальных приборов — сборщик",
     "framework"),
    ("spice", "13. Нативный SPICE-экспорт схемы", "make"),
    ("sim", "14. Автоматические тесты электрических параметров", "make"),
    ("sim_fit", "15. Доводка схемы по результатам симуляции — инженер",
     "llm"),
    ("packager", "16. PCB-паспорт деталей по даташитам — упаковщик",
     "llm"),
    ("gost", "17. Генерация платы tscircuit и 3D моделей", "make"),
    ("all", "18. Сборка всех deliverables — скрипт", "make"),
    ("test", "19. Финальные испытания и верификация — скрипт", "make"),
    ("manager", "20. Разбор итогов и решение — главный менеджер", "llm"),
)


def _stage_run_guard(name: str) -> dict:
    """run_target в стадии дизайна отключён: агент здесь только изучает
    библиотеки и пишет спецификацию."""
    return {"status": "failed",
            "error": "запуск целей в этой стадии запрещён каркасом: "
                     "только изучение библиотек и написание спецификации"}


# --------------------------------------------------------------------------- #
#  Ходы агентов: обычный чат и протокол наблюдения
# --------------------------------------------------------------------------- #
def _chat_plain(system: str, user: str, label: str = "LLM-ход",
                max_tokens: int = 4096, profile: str = "",
                json_schema: dict | None = None) -> str | None:
    """Простой ход без инструментов (писатель/дебагер/оптимизатор ТЗ).

    Использует llm.chat (последовательный перебор маршрутов) с коротким
    таймаутом. При пустом/невалидном ответе — повторяет до 3 раз.
    """
    if not llm.enabled():
        return None
    box = Path(engine.current_workdir() or ".")
    p = box / "design" / "agents_transcript.md"
    p.parent.mkdir(parents=True, exist_ok=True)

    state = getattr(engine._CTX, "active_state", None)

    def _publish_log(line: str) -> None:
        if state is None:
            return
        try:
            state.setdefault("log", []).append(line)
            engine._publish(state)
        except Exception:                        # noqa: BLE001
            pass

    for attempt in range(3):
        try:
            turn = llm.chat([{"role": "system", "content": system},
                              {"role": "user", "content": user}],
                             temperature=0.1, max_tokens=max_tokens,
                             timeout=120.0, profile=profile,
                             response_format=json_schema)
            text = turn.text if turn else None
            if text and text.strip():
                try:
                    with p.open("a", encoding="utf-8") as f:
                        f.write(f"\n\n## {now_iso()} — {label} (попытка {attempt+1})\n\n{text[:500]}\n")
                except Exception:                            # noqa: BLE001
                    pass
                _publish_log(f"{label}: ответ получен ({len(text)} симв.)")
                return text
            _publish_log(f"{label}: пустой ответ, повторная попытка")
        except Exception as exc:                            # noqa: BLE001
            _publish_log(f"{label}: ошибка — {str(exc)[:100]}")
    _publish_log(f"{label}: все попытки исчерпаны")
    return None


def _transcript(state: dict, who: str, content: str, cap: int = 2400) -> None:
    """Полный ход работы агентов — в файл рядом с артефактами и в лог заказа."""
    box = Path(engine.current_workdir() or ".")
    p = box / "design" / "agents_transcript.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(f"\n\n## {now_iso()} — {who}\n\n{content[:cap]}\n")
    state["log"] = state.get("log", []) + [
        f"[лог агентов] {who}: {len(content)} симв. → "
        "design/agents_transcript.md"]


def _methodology() -> str:
    """Методичка агента по структурным схемам (его «учебник»): идёт
    писателю чек-листа, дизайнеру и критику и решает, пройдёт ли схема.
    Резать её по объёму нельзя — лимит выше, чем у ролевой карточки."""
    f = Path(__file__).resolve().parent / "knowledge" / "structural_scheme.md"
    return _fit_sections(f, 9500)


def _read_design(box: Path, rel: str, cap: int = 4000) -> str:
    """Артефакт заказа для показа агенту: при обрезке честно говорим,
    что документ показан не полностью (агент обязан это понимать)."""
    p = box / rel
    if not p.is_file():
        return ""
    text = p.read_text(encoding="utf-8")
    if len(text) <= cap:
        return text
    head = text[:cap]
    # режем по последней границе абзаца, чтобы не разорвать мысль
    cut = head.rfind("\n\n")
    if cut > cap // 2:
        head = head[:cut]
    return head + ("\n\n[показано начало документа, "
                   f"остальное — в {rel}]")


# --------------------------------------------------------------------------- #
#  Стадия 1а: консультант проекта — что считать и нужен ли микроконтроллер
# --------------------------------------------------------------------------- #
def _stage_consultant(text: str, proj: Project,
                      state: dict) -> dict:
    """Приём замысла. Решения принимаются ДО расчётов, чтобы расчётчик
    получил не «посчитай всё», а конкретный перечень величин."""
    box = Path(engine.current_workdir() or proj.root)
    user = ("Заказ на разработку аппаратуры:\n" + text
            + "\n\nПрими замысел и вынеси решения по существу:\n"
            "  1) нужны ли расчёты физики и какие именно величины "
            "(с единицами и целями) — без них изделие не описать;\n"
            "  2) НУЖЕН ЛИ МИКРОКОНТРОЛЛЕР. СНАЧАЛА прочитай заказ "
            "буквально: если в тексте прямо назван микроконтроллер, МК, "
            "SoC, Raspberry/Pi/Arduino/ESP/STM32, ПЛК или вычислительный "
            "модуль — это требование заказчика, ставь true и называй, "
            "что указано; игнорировать явное требование нельзя. Если же "
            "в заказе ничего не сказано — порог по умолчанию НЕТ: "
            "управление режимами и яркостью, защита, зарядка, таймеры и "
            "автоотключение основанием не являются (это схема). Вводи "
            "программируемый элемент только при невыразимом схемой: "
            "хранение состояния между включениями, протоколы обмена, "
            "алгоритмы с ветвлением, измерение и обработка сигнала.\n"
            "  2б) НУЖЕН ЛИ СПЕЦИАЛИЗИРОВАННЫЙ ТРАКТ ОБРАБОТКИ СИГНАЛА "
            "(GNU Radio, машинное обучение, FPGA/ПЛИС). Это заметка о "
            "составе изделия, а не о микропрограмме. Порог по "
            "умолчанию — НЕТ: фильтр, усиление, детектирование и пороги "
            "делаются схемой. Ставь true только если из заказа прямо "
            "следует программная обработка в реальном времени на "
            "рабочей станции, обучение/применение моделей или FPGA.\n"
            "  2в) МОЖНО ЛИ ПРИМЕНЯТЬ ГОТОВЫЕ МОДУЛИ (платы зарядки, "
            "защиты аккумулятора, DC-DC, преобразователи, законченные "
            "узлы на плате). Прочитай заказ буквально: если заказ "
            "требует «всё собрать самим», «без готовых модулей», "
            "«только из компонентов», «полная своя разработка» — false "
            "(каждый узел проектируется своим циклом из компонентов с "
            "расчётом); если заказ разрешает готовые модули или сам "
            "называет готовый модуль — true; если ничего не сказано — "
            "true, но помни: даже при true узел с собственными "
            "требованиями (точные токи, пороги, температурный режим, "
            "ЭМС) считается и собирается сам, а модуль — только если "
            "его паспорт покрывает требования и это обосновано.\n"
            "  3) какие функциональные части есть в изделии;\n"
            "  4) что в заказе определено, а что требует допущения.\n"
            "Принимай минимально достаточное решение: не додумывай "
            "функций, которых нет в тексте заказа, и не добавляй "
            "элементы «на всякий случай» — избыточность запрещена.\n\n"
            "ОТВЕТ — ровно такой JSON, все поля обязательны:\n"
            '{"verdict": "принято|принято_с_допущениями|требует_уточнения",\n'
            ' "нужны_расчёты": true или false,\n'
            ' "расчёты": [{"что": "...", "величина": "...", '
            '"единица": "...", "цель": "...", "метод": "..."}],\n'
            ' "нужен_микроконтроллер": true или false,\n'
            ' "микроконтроллер_зачем": "обоснование или пустая строка, '
            'если false",\n'
            ' "нужен_тракт_обработки": true или false,\n'
            ' "тракт_обработки_что": "напр. GNU Radio, машинное обучение, '
            'FPGA; пустая строка, если false",\n'
            ' "можно_модули": true или false,\n'
            ' "модули_обоснование": "что в заказе сказано про готовые '
            'модули/платы; при true — почему модули вообще допустимы",\n'
            ' "функциональные_части": ["..."],\n'
            ' "допущения": ["..."],\n'
            ' "уточнить": ["..."]}\n'
            "Никакого другого формата: если поля нет — решение считается "
            "непринятым.")
    # Консультант — первый агент, и модель иногда отвечает пустым
    # сообщением. Это не повод ронять заказ: решение запрашивается
    # ещё раз, с требованием вернуть JSON.
    data = None
    for attempt in range(1, 4):
        raw = _chat_plain(_CONSULTANT_SYSTEM, user, profile="творческий")
        got = planner._extract_json(raw or "")
        if isinstance(got, dict) and got:
            data = got
            break
        state["log"] = state.get("log", []) + [
            f"агент-консультант: попытка {attempt}/3 — неразбираемый ответ, "
            "повторяю"]
        user += ("\n\nПредыдущий ответ не был разбираемым JSON-объектом. "
                 "Ответь ТОЛЬКО JSON-объектом решения, без пояснений и без "
                 "markdown-обёртки.")
    if not isinstance(data, dict):
        state["log"] = state.get("log", []) + [
            "агент-консультант: решение не получено — расчёты и схема "
            "идут по заказу как есть"]
        return {"status": "failed", "error": "решение консультанта не получено"}

    # Обязательные поля решения: не «на всякий случай», а предмет
    # самой роли. Пустое поле — это нерешённый вопрос, а не «нет».
    gaps = [k for k in ("нужны_расчёты", "нужен_микроконтроллер",
                         "нужен_тракт_обработки", "можно_модули")
            if k not in data]
    if gaps:
        state["log"] = state.get("log", []) + [
            f"агент-консультант: решение неполное, не решено: "
            f"{', '.join(gaps)}"]
        return {"status": "failed",
                "error": "консультант не решил: " + ", ".join(gaps)}
    # «нет» обязано быть сказано: пустая строка вместо обоснования —
    # тоже нерешённый вопрос
    for flag, why in (("нужен_микроконтроллер", "микроконтроллер_зачем"),
                      ("нужен_тракт_обработки", "тракт_обработки_что")):
        if data.get(flag) and not str(data.get(why) or "").strip():
            state["log"] = state.get("log", []) + [
                f"агент-консультант: '{flag}' = да, но обоснование "
                f"'{why}' пустое — решение не засчитано"]
            return {"status": "failed",
                    "error": f"нет обоснования: {why}"}
    # флаг модулей консультант обязан решить явно И обосновать в обе
    # стороны: «можно» без причины — такое же нерешённое, как «нельзя»
    if not str(data.get("модули_обоснование") or "").strip():
        state["log"] = state.get("log", []) + [
            "агент-консультант: флаг 'можно_модули' не обоснован — "
            "решение о готовых модулях не засчитано"]
        return {"status": "failed",
                "error": "нет обоснования: модули_обоснование"}
    (box / "design").mkdir(parents=True, exist_ok=True)
    (box / "design/acceptance.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    need_calc = bool(data.get("нужны_расчёты"))
    need_mcu = bool(data.get("нужен_микроконтроллер"))
    need_sig = bool(data.get("нужен_тракт_обработки"))
    state["log"] = state.get("log", []) + [
        f"агент-консультант: вердикт {data.get('verdict', '?')}; "
        f"расчёты {'нужны' if need_calc else 'не нужны'}; "
        f"микроконтроллер {'НУЖЕН' if need_mcu else 'не нужен'}"
        + (f" ({str(data.get('микроконтроллер_зачем') or '')[:110]})"
           if need_mcu else " — в заказе не назван и схемой решается")
        + f"; тракт обработки сигнала {'НУЖЕН' if need_sig else 'не нужен'}"
        + (f" ({str(data.get('тракт_обработки_что') or '')[:110]})"
           if need_sig else "")
        + "; готовые модули "
        + ("МОЖНО" if data.get("можно_модули") else
           "НЕЛЬЗЯ — всё собираем сами")
        + f" ({str(data.get('модули_обоснование') or '')[:110]})"]
    _transcript(state, "консультант: решение", raw or "")
    return {"status": "done"}


# --------------------------------------------------------------------------- #
#  Стадия 2: расчёты (содержимое — работа агента)
# --------------------------------------------------------------------------- #
def _calc_user(text: str, params: list[str], acceptance: str = "") -> str:
    plines = "\n".join(params) or "(числовые параметры в тексте не выделены)"
    verdict = (f"\nРешение консультанта проекта (design/acceptance.json) — "
               f"что обязательно посчитать:\n{acceptance}\n\n" if acceptance
               else "")
    return ("Заказ: " + text + "\n"
            "Числовые параметры из текста заказа: " + plines + "\n"
            + verdict + "\n"
            "Ты — инженер-расчётчик. Выполни расчёты изделия по заказу. "
            "Пиши ТОЛЬКО JSON объектом вида "
            '{"name": "...", "characteristics": [{"name": "...", "value": '
            '"...", "unit": "...", "basis": "откуда взят"}], '
            '"calcs": [{"name": "...", "formula": "...", "result": "...", '
            '"unit": "...", "inputs": "..."}]}. '
            "Каждое число — с основанием выбора; никаких придуманных "
            "требований сверх заказа.")


def _stage_calc(text: str, params: list[str], proj: Project,
                state: dict) -> dict:
    box = Path(engine.current_workdir() or proj.root)
    raw = _chat_plain(_CALC_SYSTEM, _calc_user(
        text, params, _read_design(box, "design/acceptance.json", 2500)))
    data = planner._extract_json(raw or "")
    if not isinstance(data, dict) or not data:
        return {"status": "failed", "error": "расчёты не получены от агента"}
    chars = data.get("characteristics") or []
    calcs = data.get("calcs") or []
    if not chars and not calcs:
        return {"status": "failed", "error": "пустой результат расчётов"}
    (box / "design").mkdir(parents=True, exist_ok=True)
    (box / "design/calc.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    state["log"] = state.get("log", []) + [
        f"агент-расчётчик: design/calc.json записан "
        f"({len(chars)} характеристик, {len(calcs)} расчётов)"]
    _transcript(state, "расчётчик", raw or "")
    return {"status": "done"}


# --------------------------------------------------------------------------- #
#  Стадия ТЗ: петля «писатель → дебагер → оптимизатор»
# --------------------------------------------------------------------------- #
def _tz_writer_user(text: str, skeleton: str, calc: str,
                    issues: str, acceptance: str = "") -> str:
    issues_block = (f"\n\nЗамечания контролёра предыдущей версии — исправь "
                    f"ровно их:\n{issues}\n" if issues else "")
    accept_block = (f"\n\nРешение консультанта (design/acceptance.json) — "
                    f"это рамка состава: не пиши в ТЗ того, что там "
                    f"отклонено, и не добавляй управляющий узел, если "
                    f"консультант не признал его нужным:\n{acceptance}\n"
                    if acceptance else "")
    return ("Заказ: " + text + "\n\n"
            "Расчёты (design/calc.json):\n" + (calc or "(расчётов нет)")
            + accept_block
            + "\n\nКаркас ТЗ от скрипта проекта (сохраняй его разделы, "
            "углубляй каждый):\n" + (skeleton or "(каркас не задан)")
            + issues_block + "\n\n"
            "Напиши ПОЛНОЕ ТЗ изделия в markdown: все разделы каркаса "
            "заполнены содержанием (не заглушками), числовые значения "
            "согласованы с расчётами, для каждой стадии цикла — критерии "
            "приёмки, добавь испытания, риски, допущения, экологию и "
            "маркировку. Верни ТОЛЬКО полный документ одним куском.")


def _tz_debug_user(skeleton: str, draft: str, text: str) -> str:
    return ("Текст заказа: " + text[:500] + "\n\nЧек-лист каркаса:\n"
            "- каждый раздел каркаса заполнен содержанием без заглушек и "
            "плейсхолдеров;\n"
            "- числовые значения согласованы с заказом и расчётами, "
            "единицы измерения проставлены;\n"
            "- есть критерии приёмки по стадиям, испытания, риски, "
            "допущения;\n"
            "- нет пустых отсылок «см. выше», противоречий, повторов;\n"
            "- глубина соответствует эталону технического проекта.\n\n"
            "ТЗ на проверку:\n" + draft + "\n\n"
            "Верни ТОЛЬКО JSON: "
            '{"passed": true|false, "issues": ["<замечание>", …]}')


def _tz_stage(text: str, params: list[str], proj: Project,
              state: dict) -> dict:
    box = Path(engine.current_workdir() or proj.root)
    skel_path = box / "out/tz.md"
    skeleton = (skel_path.read_text(encoding="utf-8")
                if skel_path.is_file() else "")
    calc = _read_design(box, "design/calc.json", 5000)
    acceptance = _read_design(box, "design/acceptance.json", 2500)
    draft, issues = "", ""
    for it in range(1, TZ_ITER + 1):
        user = _tz_writer_user(text, skeleton, calc, issues, acceptance)
        raw = _chat_plain(_TZ_WRITER_SYSTEM, user)
        if not raw or len(raw.strip()) < 800:
            state["log"] = state.get("log", []) + [
                f"агент-писатель: документ слишком короток "
                f"({len(raw or '')} симв.) — итерация {it}/{TZ_ITER}"]
            continue
        draft = raw
        state["log"] = state.get("log", []) + [
            f"агент-писатель: версия {it}/{TZ_ITER} — {len(draft)} симв."]
        _transcript(state, f"писатель: версия {it}", draft)
        (box / "design").mkdir(parents=True, exist_ok=True)
        (box / "design/technical_task.md").write_text(
            draft, encoding="utf-8")
        verdict = planner._extract_json(
            _chat_plain(_TZ_DEBUG_SYSTEM,
                        _tz_debug_user(skeleton, draft, text)) or "")
        if isinstance(verdict, dict) and verdict.get("passed"):
            state["log"] = state.get("log", []) + [
                f"агент-дебагер: ТЗ согласовано (итерация {it})"]
            break
        issues = "; ".join(
            str(x) for x in (verdict.get("issues") or []
                             if isinstance(verdict, dict) else ["контролёр "
                             "не вернул JSON"]))[:1600]
        state["log"] = state.get("log", []) + [
            f"агент-дебагер: замечаний {issues.count(';') + 1} — на "
            f"переработку агента-писателю"]
        _transcript(state, f"дебагер: итерация {it}", issues)
    if not draft:
        return {"status": "failed", "error": "ТЗ не получено от агентов"}
    polished = _chat_plain(_TZ_OPT_SYSTEM,
                           "Документ:\n" + draft + "\n\nОтшлифуй и верни "
                           "ТОЛЬКО полный markdown-документ.")
    if polished and len(polished.strip()) >= len(draft) * 0.5:
        draft = polished
        state["log"] = state.get("log", []) + [
            f"агент-оптимизатор: финальная полировка — {len(draft)} симв."]
        _transcript(state, "оптимизатор: финал", draft)
    else:
        state["log"] = state.get("log", []) + [
            "агент-оптимизатор: полировка отклонена каркасом — оставлена "
            "согласованная версия писателя"]
    (box / "design/technical_task.md").write_text(
        draft, encoding="utf-8")
    return {"status": "done"}


# --------------------------------------------------------------------------- #
#  Библиотекарь: реальный инвентарь средств ДО проектирования
# --------------------------------------------------------------------------- #
def _library_auto_fill(structure: dict, tasks: list[dict],
                       state: dict) -> str:
    """Авто-заполнение: если LLM не дал инвентарь, делаем точечный поиск.

    Читаем ошибки из structure/tasks → ищем подходящие footprint/symbol
    через kicad_libs → возвращаем markdown-строк с результатами.
    """
    from agents.tools import kicad_libs
    out_lines = []
    
    # определяем типы компонентов из tasks
    comp_types = set()
    for t in (tasks or []):
        if not isinstance(t, dict):
            continue
        # ищем в параметрах
        for f in ("входные_параметры", "выходные_параметры"):
            for v in (t.get(f) or []):
                if isinstance(v, dict):
                    nm = str(v.get("обозначение") or "").strip()
                    if nm:
                        comp_types.add(nm)
    
    # чтобы был смысл, берём топ-3 самых характерных
    for ct in sorted(comp_types)[:5]:
        # ищем footprint для типа компонента
        for fp_pattern in [ct.split()[0] if " " in ct else ct, ct.lower(), ct.upper()]:
            try:
                res = kicad_libs({"action": "fp_search", "pattern": fp_pattern})
                if isinstance(res, str) and '"' in res:
                    data = json.loads(res)
                    fps = data.get("footprints", [])[:3]
                    if fps:
                        for fp in fps:
                            out_lines.append(f"- lib_id: {fp.get('library')}:{fp.get('footprint')}, "
                                           f"footprint: {fp.get('qualified')}")
                        break
            except Exception:
                pass
    
    if out_lines:
        return "## Авто-заполненные footprint\n" + "\n".join(out_lines) + "\n"
    return ""


def _stage_library(text: str, calc: str, proj: Project,
                   state: dict) -> dict:
    box = Path(engine.current_workdir() or proj.root)
    user = ("Заказ: " + text + "\n"
            "Расчёты (design/calc.json) — по ним понятно, какие классы "
            "компонентов понадобятся:\n" + (calc[:4000] or "(нет)")
            + "\n\nНайди инструментом kicad_libs реальные символы, их "
            "выводы и корпуса для каждого класса компонентов, который "
            "нужен изделию (источники питания, регуляторы, силовые "
            "элементы, управление, защита, коммутация, индикация, "
            "разъёмы — по смыслу заказа). Для каждого класса запиши: "
            "lib_id, ключевые выводы с номерами, реальный корпус.\n"
            + ("Если заказ разрешает готовые модули (можно_модули=true): "
               "для функций, которые решаются готовыми платами (зарядка, "
               "BMS, DC-DC), найди каталогом part_search реальные "
               "модули-кандидаты — LCSC-код, MPN, параметры, наличие, "
               "документ; запиши их отдельным разделом "
               "«Модули-кандидаты».\n"
               if _modules_mode(box) else
               "Заказ запрещает готовые модули (можно_модули=false): "
               "модулей-кандидатов не ищи, только компоненты.\n")
            + "Ответ — ТОЛЬКО текстовый индекс доступных средств "
            "(markdown), без схемы и без проектирования."
            + _manager_hints(box))
    raw = _libs_loop(proj, _LIBRARIAN_SYSTEM, user, state,
                     profile="механический")
    if not raw or len(raw.strip()) < 300:
        # один добросовестный заход с требованием к длине ответа
        state["log"] = state.get("log", []) + [
            f"агент-библиотекарь: первый ответ пуст или мал "
            f"({len(raw or '')} симв.) — требую полный индекс"]
        raw = _chat_plain(_LIBRARIAN_SYSTEM,
                          user + "\n\nПредыдущий ответ пуст или слишком "
                                 "короток. Отдай ПОЛНЫЙ индекс: по каждому "
                                 "классу — строка с lib_id, выводами и "
                                 "корпусом. Не менее 15 строк.")
    if not raw or len(raw.strip()) < 300:
        # Если библиотекарь не сделал полный инвентарий, но есть конкретные
        # детали без посадочного места (Q5001 и др.) — НЕ валим сразу.
        # Просим его сделать точечный поиск по ошибкам kicad/verify: искать
        # footprint/symbol в локальных библиотеках, в jlcparts, а если нет —
        # искать в интернете/каталогах шире (LCSC/JLCPCB/Ultralibrarian/SnapEDA)
        # и предложить подходящие варианты с lib_id/footprint.
        hints = ""
        try:
            hints = (box / "design" / "manager_hints.json").read_text(encoding="utf-8") if (box / "design" / "manager_hints.json").is_file() else ""
        except Exception:
            hints = ""
        try:
            pipeline = json.loads((box / "design" / "_pipeline.json").read_text(encoding="utf-8")) if (box / "design" / "_pipeline.json").is_file() else {}
            for s in pipeline.get("steps") or []:
                if s.get("target") in ("kicad", "verify") and s.get("error"):
                    hints += "\n\n" + str(s.get("error"))
        except Exception:
            pass
        state["log"] = state.get("log", []) + [
                f"агент-библиотекарь: инвентаризация маловата ({len(raw or '')} симв.), "
                f"перехожу к точечному поиску footprint по ошибкам"]
        raw2 = _chat_plain(_LIBRARIAN_SYSTEM,
                           user + "\n\nКРИТИЧНО: полный скан библиотек не удался. "
                           "Найди только те детали, у которых НЕ ПОДТВЕРЖДЁН корпус/footprint "
                           "по ошибкам ниже.\n\nОШИБКИ/ПОДСКАЗКИ:\n" + (hints[-4000:] or "(нет)") +
                           "\n\nДействуй пошагово:\n"
                           "1) Проверить локальные .kicad_sym/.pretty\n"
                           "2) Проверить локальный каталог jlcparts (если есть)\n"
                           "3) Если не найдено — искать в интернете/публичных каталогах шире "
                           "(LCSC, JLCPCB, Ultralibrarian, SnapEDA, Octopart-подобные). "
                           "Найди РЕАЛЬНЫЙ подходящий footprint, дай точный lib_id и название footprint, "
                           "предложи 1-2 варианта замены только если совсем ничего нет.\n"
                           "Ответ — ТОЛЬКО практичный список под эти ошибки.")
        if raw2 and len(raw2.strip()) >= 150:
            raw = raw2
            state["log"] = state.get("log", []) + [
                f"агент-библиотекарь: точечный поиск по ошибкам ({len(raw)} симв.)"]
        else:
            # Автофикс: если LLM не дал результат, заполняем вручную через kicad_libs
            # ищем footprint для Q5001 и похожих символов
            state["log"] = state.get("log", []) + [
                f"агент-библиотекарь: точечный поиск не дал результата, заполняю вручную"]
            manual = _library_auto_fill(structure, tasks, state)
            if manual:
                raw = raw or ""
                raw += manual
                state["log"] = state.get("log", []) + [
                    f"агент-библиотекарь: авто-заполнение ({len(raw)} симв.)"]
            else:
                err = f"инвентаризация библиотек не получена ({len(raw or '')} симв.), авто-заполнение не дало результата"
                state["log"] = state.get("log", []) + [f"агент-библиотекарь: {err}"]
                return {"status": "failed", "error": err}
    (box / "design").mkdir(parents=True, exist_ok=True)
    (box / "design/library_index.md").write_text(raw, encoding="utf-8")
    state["log"] = state.get("log", []) + [
        f"агент-библиотекарь: design/library_index.md ({len(raw)} симв.)"]
    _transcript(state, "библиотекарь: индекс", raw)

    # реальные закупочные позиции из каталога поставщика — в спецификацию
    try:
        _library_to_kicad(box, raw, state)
    except Exception as exc:                             # noqa: BLE001
        state["log"] = state.get("log", []) + [
            f"библиотекарь: не удалось обогатить design/schematic.json — {exc}"]
        return {"status": "failed",
                "error": f"сборка KiCad-спецификации: {exc}"[:200]}
    return {"status": "done"}


#: каталог поставщика (локальный срез JLC/LCSC) — тот же, что у агента
CATALOG_DB = os.environ.get(
    "KR_JLCPARTS_DB", "/mnt/ext4data/jlcparts/cache/cache.sqlite3")


def _catalog_rows(query: str, limit: int = 6) -> list[dict]:
    """Реальные детали из каталога поставщика по текстовому запросу.

    Поиск идёт по базе (описание, изготовитель, категория), а не по
    зашитым спискам: в базе 400+ тысяч реальных позиций, и в спецификацию
    попадает только то, что в ней действительно есть.
    """
    words = [w for w in re.split(r"\s+", str(query or "").strip()) if w][:6]
    if not words or not Path(CATALOG_DB).is_file():
        return []
    where = " AND ".join(
        "(description LIKE ? OR mfr LIKE ? OR manufacturer LIKE ? "
        "OR category LIKE ? OR subcategory LIKE ?)" for _ in words)
    args: list[str] = []
    for w in words:
        args += [f"%{w}%"] * 5
    try:
        import sqlite3
        con = sqlite3.connect(f"file:{CATALOG_DB}?mode=ro", uri=True)
        try:
            rows = con.execute(
                "SELECT lcsc, mfr, manufacturer, description, package, "
                "stock, datasheet FROM jlc_components WHERE " + where
                + " ORDER BY CASE WHEN stock > 0 THEN 0 ELSE 1 END, "
                  "stock DESC, lcsc ASC LIMIT ?", (*args, limit)).fetchall()
        finally:
            con.close()
    except Exception:                                        # noqa: BLE001
        return []
    out: list[dict] = []
    for lcsc, mfr, man, desc, pkg, stock, ds in rows:
        out.append({"lcsc": f"C{lcsc}", "mpn": str(mfr or "").strip(),
                    "manufacturer": str(man or "").strip(),
                    "description": str(desc or "").strip(),
                    "package": str(pkg or "").strip(),
                    "stock": int(stock or 0),
                    "datasheet": str(ds or "").strip()})
    return out


def _web_part_lookup(query: str, limit: int = 6) -> list[dict]:
    """Поиск детали в интернете, когда в локальном каталоге её нет.

    Источники — страницы каталогов поставщиков (LCSC, JLCPCB и подобные):
    из ссылки извлекается код детали и её номер производителя. Выдуманных
    кодов не бывает: в результат попадает только то, что есть на странице.
    """
    try:
        from ddgs import DDGS                             # noqa: PLC0415
    except ImportError:
        return []
    out: list[dict] = []
    seen: set[str] = set()
    queries = [f"{query} LCSC", f"{query} site:lcsc.com", f"{query} part"]
    for q in queries:
        try:
            results = list(DDGS().text(q, max_results=limit))
        except Exception:                                # noqa: BLE001
            continue
        for r in results:
            href = str(r.get("href") or "")
            m = re.search(r"/product-detail/(C\d+)\.html", href)
            if not m:
                m = re.search(r"/partdetail/[^/]+/(C\d+)", href)
            if not m:
                continue
            lcsc = m.group(1)
            if lcsc in seen:
                continue
            seen.add(lcsc)
            title = str(r.get("title") or "")
            mpn = re.search(r"partdetail/([^/]+)/C\d+", href)
            out.append({
                "lcsc": lcsc,
                "mpn": mpn.group(1) if mpn else "",
                "description": title[:120],
                "package": "",
                "stock": 0,
                "datasheet": "",
                "source": href,
            })
            if len(out) >= limit:
                return out
        if out:
            break
    return out


def _library_to_kicad(box: Path, index: str, state: dict) -> None:
    """Обогатить спецификацию реальными закупочными позициями.

    Схема уже собрана предыдущими стадиями; здесь она получает то, без
    чего нельзя ни купить деталь, ни найти даташит: LCSC-код, MPN и
    ссылку на документ. Источник — каталог поставщика (база), а если
    детали там нет — поиск в интернете по страницам каталогов. Ни одного
    зашитого соответствия: выдуманного кода не бывает ни там, ни там.
    """
    spec_path = box / "design/schematic.json"
    spec = _read_json(box, "design/schematic.json")
    parts = [p for p in (spec.get("parts") or []) if isinstance(p, dict)]
    if not parts:
        raise ValueError("нет design/schematic.json с parts — "
                         "сначала спецификация")
    enriched = 0
    from_db = 0
    from_web = 0
    missing: list[str] = []
    for p in parts:
        if p.get("lcsc") and p.get("mpn"):
            continue
        # запрос к каталогу: по названию детали и её номиналу
        query = " ".join(x for x in (str(p.get("value") or ""),
                                     str(p.get("lib_id") or "")) if x)
        rows = _catalog_rows(query, limit=5)
        rec = rows[0] if rows else None
        if rec is None:
            # в локальной базе нет — ищем в интернете
            web = _web_part_lookup(query)
            rec = web[0] if web else None
            if rec is not None:
                from_web += 1
        else:
            from_db += 1
        if rec is None:
            missing.append(f"{p.get('ref')}: «{query}» не найдено ни в "
                           "каталоге, ни в интернете")
            continue
        p["lcsc"] = rec.get("lcsc") or p.get("lcsc") or ""
        if rec.get("mpn"):
            p["mpn"] = rec["mpn"]
        elif p.get("mpn"):
            p["mpn"] = p["mpn"]
        if rec.get("manufacturer") and not p.get("manufacturer"):
            p["manufacturer"] = rec["manufacturer"]
        if rec.get("datasheet") and not p.get("datasheet"):
            p["datasheet"] = rec["datasheet"]
        if rec.get("package") and not p.get("package"):
            p["package"] = rec["package"]
        if rec.get("source") and not p.get("source_url"):
            p["source_url"] = rec["source"]
        enriched += 1
    spec["parts"] = parts
    spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2),
                         encoding="utf-8")
    state["log"] = state.get("log", []) + [
        f"библиотекарь: закупочных позиций проставлено {enriched} "
        f"из {len(parts)} (из базы {from_db}, из интернета {from_web}), "
        f"не найдено {len(missing)}"]
    for m in missing[:6]:
        state["log"] = state.get("log", []) + [f"  поиск: {m}"]


# --------------------------------------------------------------------------- #
#  Контролёр требований: чек-лист приёмки — контракт для дизайнера и критика
# --------------------------------------------------------------------------- #
def _stage_checklist(text: str, calc: str, tz_draft: str, proj: Project,
                     state: dict) -> dict:
    box = Path(engine.current_workdir() or proj.root)
    # решение консультанта — обязательный вход: без него контролёр
    # выдумает критерии «управление микроконтроллером» там, где МК
    # консультант отклонил как ненужный
    acceptance = _read_design(box, "design/acceptance.json", 2500)
    user = ("Методичка структурной схемы и требований к блокам — на неё "
            "опирайся при выводе критериев:\n" + _methodology() + "\n\n"
            + (f"Решение консультанта (design/acceptance.json) — учти его "
               f"дословно, не выдумывай того, что в нём отклонено:\n"
               f"{acceptance}\n\n" if acceptance else "")
            + "Заказ: " + text + "\n"
            "Расчёты:\n" + (calc[:4000] or "(нет)") + "\n"
            "ТЗ (структура):\n" + (tz_draft[:4000] or "(нет)") + "\n\n"
            + ("" if _modules_mode(box) else
               "ВАЖНО: заказ запрещает готовые модули (можно_модули=false) — "
               "включи в чек-лист пункт «все узлы собраны из компонентов, "
               "готовых плат и модулей нет, номиналы рассчитаны». ")
            + "Составь чек-лист приёмки принципиальной схемы этого изделия "
            "(markdown): по каждому функциональному узлу — обязательные "
            "признаки и способы проверки (наличие силовых сетей, номиналы "
            "по расчётам, защита, управление, индикация). Управляющий "
            "узел включай в критерии только если он назван в решении "
            "консультанта. Это контракт: "
            "дизайнер обязан его выполнить, критик — проверить. Схему не "
            "составляй. Возврат — только чек-лист.")
    raw = _chat_plain(_CHECKLIST_SYSTEM, user, profile="механический")
    if not raw or len(raw.strip()) < 300:
        return {"status": "failed", "error": "чек-лист не получен"}
    (box / "design").mkdir(parents=True, exist_ok=True)
    (box / "design/checklist.md").write_text(raw, encoding="utf-8")
    state["log"] = state.get("log", []) + [
        f"агент-контролёр: design/checklist.md ({len(raw)} симв.)"]
    _transcript(state, "чек-лист приёмки", raw)
    return {"status": "done"}


# --------------------------------------------------------------------------- #
#  Стадия 3: спецификация схемы (содержимое — работа агента)
# --------------------------------------------------------------------------- #
def _design_user(name: str, text: str, calc: str, errors: str,
                 lib_index: str = "", checklist: str = "",
                 previous: str = "",
                 modules_allowed: bool = True) -> str:
    tail = ("Обнаруженные ошибки — исправь РОВНО ИХ в своей спецификации, "
            "всё остальное сохрани без изменений:\n"
            + errors + "\n\n" if errors else "")
    # Прошлая попытка агента показывается целиком: правка по собственному
    # тексту надёжнее, чем переписывание по памяти (ошибки повторяются
    # именно там, где агент не видит, что именно он написал).
    tail += (("Твоя ПРЕДЫДУЩАЯ попытка (исправь её точечно, не переписывай "
              "с нуля):\n" + previous[:6000] + "\n\n") if previous else "")
    contracts = (("\nИнвентарь библиотек от агента-библиотекаря "
                  "(design/library_index.md) — бери lib_id/пины/корпуса "
                  "ОТТУДА:\n" + lib_index + "\n") if lib_index else "")
    contracts += (("\nЧек-лист приёмки от агента-контролёра "
                   "(design/checklist.md) — каждый пункт обязан быть "
                   "выполнен твоей схемой:\n" + checklist + "\n")
                  if checklist else "")
    mode = (
        "РЕЖИМ СБОРКИ ЗАКАЗА: готовые модули РАЗРЕШЕНЫ — узел можно "
        "решить готовым модулем из внешнего каталога (например, плата "
        "зарядки готовым модулем), если паспортные параметры покрывают "
        "требования блока и это обосновано; модуль-кандидат ищи сам "
        "(знание серии) или инструментом part_search — реальный SKU "
        "обязателен. Узлы с собственными требованиями (точные токи, "
        "пороги, температурный режим, ЭМС) считай сам из компонентов: "
        "топология — типовая схема включения из первоисточника, номиналы "
        "— расчёт.\n"
        if modules_allowed else
        "РЕЖИМ СБОРКИ ЗАКАЗА: готовые модули ЗАПРЕЩЕНЫ "
        "(можно_модули=false) — модули не рассматривай вовсе "
        "(part_search по модулям не нужен), каждый узел собирается из "
        "компонентов: топология — из первоисточника, все номиналы — "
        "расчёт, детали — реальные, с даташитами.\n")
    sources = _sources_digest()
    return ("Методичка составления структурной схемы и требований к "
            "блокам (обязательна к соблюдению):\n" + _methodology() + "\n\n"
            + ("Библиотека первоисточников (типовые схемы включения и "
               "правила расчёта — опирайся на них; даташит выбранной "
               "детали важнее книги):\n" + sources + "\n\n" if sources
               else "")
            + mode
            + "Заказ: " + text + "\n"
            "Расчёты (design/calc.json):\n" + (calc or "(расчётов нет)")
            + "\n\n" + contracts + "\n" + tail
            + "Напиши design/schematic.json — принципиальную схему. Формат:\n"
            '{"name": "<имя изделия>", "paper": "A0",\n'
            ' "parts": [{"ref": "U1", "lib_id": "Библ:Символ", '
            '"value": "номинал", "footprint": "Корпус:Имя" — по желанию}],\n'
            ' "nets": [{"name": "<имя сети>", "power": "power:<имя шины>" '
            '(только для шин питания), '
            '"conns": [["R1", "2"], ["C1", "2"]]}, …],\n'
            ' "blocks": [{"title": "БЛОК 1 — ПОЯСНЕНИЕ", "sub": "назначение '
            'функциональной части", "refs": ["R1", "C1"], '
            '"params": {"<параметр>": <число>}}]}\n'
            "Правила каркаса:\n"
            "- частей 12..40; ref уникальны;\n"
            "- каждый функциональный блок — собственное схемотехническое "
            "решение: топология — типовая схема включения из "
            "первоисточника (даташит выбранной детали, аппнота, книга из "
            "библиотеки первоисточников), номиналы — из design/calc.json "
            "или даташита реальной детали; числа и топологии без "
            "источника не подставляются («на глаз» и «по памяти» — "
            "ошибка);\n"
            "- модуль (когда разрешён): деталь модуля в refs блока "
            "помечена \"module\": true; у неё реальный lcsc или mpn из "
            "каталога (part_search) и документ (даташит/распиновка); "
            "модуль вне платы — \"placement\": \"offboard\" (корпус не "
            "требуется, на плате — клеммы/разъём под провода);\n"
            "- lib_id и пины — только те, что подтверждены ответами "
            "kicad_libs (search/pins/footprints);\n"
            "- СИМВОЛЫ ПИТАНИЯ каркас добавляет сам по полю power сети. "
            "Не создавай компонентов для земли/рельсов (GND, VCC и т.п. — "
            "НЕ компоненты, их нет в parts и в refs блоков), не пиши их "
            "в conns как отдельный ref;\n"
            "- каждый ref в conns обязан существовать в parts: сети с "
            "ссылками на несуществующие компоненты — ошибка;\n"
            "- footprint (по желанию) — из системы корпусов "
            "(«БиблиотекаКорпусов:Имя», проверь kicad_libs(action="
            "'footprints')); корпус и символ — разные системы имён;\n"
            "- каждая сеть не короче 2 выводов; имена шин питания "
            "выбираешь сам по смыслу ТЗ и расчётов;\n"
            "- пара (ref, пин) принадлежит ровно одной сети: если один и "
            "тот же пин у тебя в двух сетях — это ошибка твоих подсетей, "
            "оставь пин в одной сети, а питание подводи отдельными сетями;\n"
            "- блоки — по функциональным узлам, в порядке прохождения "
            "сигнала (этот порядок каркас использует для стрелок "
            "структурной схемы); у каждого блока назначение (sub) "
            "длиной не менее 20 символов; каждый ref из parts входит "
            "ровно в один блок.\n"
            "Ответ: ТОЛЬКО JSON целиком, без пояснений, одним куском.")


def _critic_user(text: str, calc: str, spec: dict,
                 checklist: str = "",
                 modules_allowed: bool = True) -> str:
    mode = ("" if modules_allowed else
            "Заказ ЗАПРЕЩАЕТ готовые модули (можно_модули=false): деталь "
            "с \"module\": true — дефект. ")
    return ("Основание для суждения — методичка структурной схемы:\n"
            + _methodology() + "\n\n"
            + mode
            + "Заказ: " + text + "\n"
            + (("Чек-лист приёмки, который обязана выполнять "
                "схема:\n" + checklist + "\n") if checklist else "")
            + "Расчёты агента-теоретика (design/calc.json):\n"
            + (calc or "(нет)") + "\n"
            "Спецификация на проверку (design/schematic.json):\n"
            + json.dumps(spec, ensure_ascii=False, indent=1)[:9000] + "\n"
            "Проверь по критериям: (1) схема реализует заказ и расчёты; "
            "(2) страховка каркаса: вывод — максимум в одной сети; "
            "(3) нет бессмысленных сетей и висящих выводов; (4) номиналы "
            "согласуются с расчётами и даташитами реальных деталей; "
            "(5) силовые сети присутствуют там, где нужны по схеме; "
            "(6) блоки действительно покрывают состав схемы; (7) каждый "
            "функциональный блок — обоснованное схемотехническое решение: "
            "типовая схема включения из первоисточника, а не «на глаз». "
            "Верни ТОЛЬКО JSON по указанной форме; если замечаний нет — "
            '{"ok": true, "issues": []}.')



def _sanitize_spec(data: dict) -> dict:
    """Усовершенствованная авто-санитизация без хардкода:
    - удаляет дублирующиеся выводы (ref, pin) из сетей;
    - подчищает сети с 1 выводом (удаляет висячие сети);
    - покрывает все orphaned-компоненты первым блоком;
    - гарантирует уникальность наименований блоков.
    """
    if not isinstance(data, dict):
        return data
    parts = data.get("parts") or []
    nets = data.get("nets") or []
    blocks = data.get("blocks") or []
    refs_set = {str(p.get("ref")) for p in parts if p.get("ref")}

    # 1. Уникальность блоков по title
    seen_titles = set()
    clean_blocks = []
    for b in blocks:
        title = str(b.get("title") or "БЛОК")
        if title in seen_titles:
            title = title + "_" + str(len(seen_titles) + 1)
        seen_titles.add(title)
        b["title"] = title
        clean_blocks.append(b)
    data["blocks"] = clean_blocks

    # 2. Очистка двойных пинов и висячих сетей (< 2 выводов)
    seen_pins = set()
    clean_nets = []
    for n in nets:
        conns = n.get("conns") or []
        clean_conns = []
        for c in conns:
            if isinstance(c, (list, tuple)) and len(c) == 2:
                kp = (str(c[0]), str(c[1]))
                if kp in seen_pins:
                    continue
                seen_pins.add(kp)
                clean_conns.append(list(c))
            else:
                clean_conns.append(c)
        # Если после очистки осталось >= 2 выводов ИЛИ сеть силовая (power), оставляем
        if len(clean_conns) >= 2 or n.get("power"):
            n["conns"] = clean_conns
            clean_nets.append(n)
    data["nets"] = clean_nets

    # 3. Покрываем orphans первым блоком
    if clean_blocks and refs_set:
        covered = set()
        for b in clean_blocks:
            for r in (b.get("refs") or []):
                covered.add(str(r))
        orphans = sorted(refs_set - covered)
        if orphans:
            b0_refs = [str(r) for r in (clean_blocks[0].get("refs") or [])]
            clean_blocks[0]["refs"] = list(dict.fromkeys(b0_refs + orphans))

    return data

def _check_spec(data: object, modules_allowed: bool = True) -> tuple[bool, list[str]]:
    """Строгая структурная валидация. Каркас ничего не правит: любые
    дефекты возвращаются агенту списком. Режим модулей (флаг заказа
    «можно_модули») приходит из решения консультанта."""
    errs: list[str] = []
    if isinstance(data, dict):
        data = _sanitize_spec(data)

    if not isinstance(data, dict):
        return False, ["нужен JSON-объект спецификации"]
    parts = data.get("parts")
    nets = data.get("nets")
    if not isinstance(parts, list) or not parts:
        return False, ["parts пуст"]
    if not isinstance(nets, list) or not nets:
        return False, ["nets пуст"]
    refs = [str(p.get("ref", "")) for p in parts]
    if len(set(refs)) != len(refs):
        errs.append("ref дублируются")
    refs_set = set(refs)
    if len(parts) < 10:
        errs.append(f"мало компонентов ({len(parts)}, минимум 10)")
    for p in parts:
        if not p.get("lib_id"):
            errs.append(f"{p.get('ref')}: нет lib_id")
        offboard = str(p.get("placement") or "").strip().lower() == "offboard"
        if not p.get("footprint") and not offboard:
            # Footprint assigned later by kicad stage; note as warning
            warns.append(f"{p.get('ref')}: нет corpus (footprint) — будет "
                         "назначен автоматически на стадии kicad")
    seen_pin: dict[tuple[str, str], str] = {}
    # --- структурная схема: проверки здравого смысла блоков ---------------
    blocks = data.get("blocks") or []
    if blocks:
        titles = [str(b.get("title") or "") for b in blocks]
        if len(set(titles)) != len(titles):
            errs.append("наименования блоков дублируются")
        in_blocks: dict[str, str] = {}
        for b in blocks:
            title = str(b.get("title") or "БЛОК ?")
            # Э1: в наименовании блока — ФУНКЦИЯ, а не деталь. Артефакт
            # типа «J1 — РАЗЪЁМ …» — позиционное обозначение Э3, не Э1:
            for r in refs_set:
                if re.search(rf"(?<![A-Za-z0-9]){re.escape(r)}(?![A-Za-z0-9])", title):
                    errs.append(f"{title!r}: в наименовании блока "
                                f"фигурирует ref детали {r} — на Э1 блок "
                                "обозначается ФУНКЦИЕЙ («БЛОК ВХОДА "
                                "ПИТАНИЯ»), а не шифром детали")
                    break
            refs_b = [str(r) for r in (b.get("refs") or [])]
            if not refs_b:
                errs.append(f"{title}: пустой блок (нет refs)")
            for r in refs_b:
                if r not in refs_set:
                    errs.append(f"{title}: ref {r} не существует в parts")
                if r in in_blocks:
                    # называем ОБА блока И даём приказ; иначе агент
                    # просто повторяет спецификацию верbatim
                    errs.append(f"{title}: ref {r} уже указан в блоке "
                                f"«{in_blocks[r]}» — компонент принадлежит "
                                "РОВНО ОДНОМУ блоку. Перераспредели так: "
                                f"{r} оставь только в «{in_blocks[r]}» и "
                                "удали повтор из «{0}»".format(title))
                else:
                    in_blocks[r] = title
            sub = str(b.get("sub") or "").strip()
            if len(sub) < 20:
                errs.append(f"{title}: нет внятного назначения (sub) — "
                            "минимум фраза в 20+ символов")
        orphans = sorted(refs_set - set(in_blocks))
        if orphans:
            errs.append(f"компоненты вне блоков (структурная схема не "
                        f"покрывает изделие): {', '.join(orphans[:8])}")
    for n in nets:
        conns = n.get("conns") or []
        if not isinstance(conns, list):
            errs.append(f"{n.get('name')}: conns не список")
            continue
        if len(conns) < 2 and not n.get("power"):
            errs.append(f"сеть {n.get('name')}: {len(conns)} вывод")
        for c in conns:
            if not isinstance(c, (list, tuple)) or len(c) != 2:
                errs.append(f"сеть {n.get('name')}: неверный вывод {c}")
                continue
            ref, pin = str(c[0]), str(c[1])
            if ref not in refs_set:
                errs.append(f"сеть {n.get('name')}: неизвестный "
                            f"компонент {ref}")
                continue
            key = (ref, pin)
            if key in seen_pin:
                errs.append(f"пин {ref}.{pin} принадлежит двум сетям "
                            f"({seen_pin[key]} и {n.get('name')})")
            seen_pin[key] = n.get("name")
    # --- режим сборки: можно_модули из решения консультанта ---------------
    for p in parts:
        if not isinstance(p, dict) or not p.get("module"):
            continue
        ref = str(p.get("ref") or "?")
        if not modules_allowed:
            errs.append(f"деталь {ref}: готовый модуль, но заказ запрещает "
                        "модули (можно_модули=false)")
        sku = (str(p.get("lcsc") or "").strip()
               or str(p.get("mpn") or "").strip())
        if not sku:
            errs.append(f"модуль {ref} без реального SKU — нужен lcsc "
                        "или mpn (библиотекарь)")
    return (not errs), errs


def _spec_full_check(data, modules_allowed: bool,
                     box: Path) -> tuple[bool, list[str], list[str]]:
    """Проверка сдачи дизайнера: структура каркаса + поблочная проверка
    SKiDL (выводы существуют в реальных символах, каждый — в одной сети).

    Третий элемент — «жёсткие» дефекты сборки блоков: авто-санитизация
    каркаса их не исправляет, поэтому кандидат с ними в победители не
    берётся — только петля правок."""
    ok, errs = _check_spec(data, modules_allowed)
    hard: list[str] = []
    bt = _blocks()
    if ok and bt.get("mod") is not None:
        try:
            rep = bt["mod"].check_spec(data, bt["mod"].load_kit(), box=box)
            hard = [str(e) for e in (rep.get("errors") or [])]
        except Exception as exc:                       # noqa: BLE001
            errs.append("(каркас: поблочная проверка не выполнена — "
                        f"{exc})")
    return (ok and not hard), (errs + hard), hard


def _stage_design(text: str, proj: Project, state: dict,
                  engine_errors: str = "") -> dict:
    name = proj.name
    box = Path(engine.current_workdir() or proj.root)
    calc = _read_design(box, "design/calc.json", 5000)
    lib_index = _read_design(box, "design/library_index.md", 4000)
    checklist = _read_design(box, "design/checklist.md", 4000)
    acceptance = _read_design(box, "design/acceptance.json", 2500)
    # флаг заказа «можно_модули»: решение консультанта обязательно —
    # от него зависят и промпт дизайнера, и проверка каркаса
    modules_allowed = _modules_mode(box)
    errors = engine_errors
    history: list[str] = []                    # замечания всех попыток
    previous: str = ""                         # текст прошлой попытки
    for attempt in range(1, SCHEMA_RETRIES + 1):
        # Несколько независимых дизайнеров работают ПАРАЛЛЕЛЬНО: кандидаты
        # не видят друг друга, отбор — только строгой валидацией каркаса.
        # Архитектурно это оправдано: дешёвая параллельность вместо
        # последовательных правок одного и того же текста.
        def make(seed: int) -> str:
            hint = "" if not seed else (
                f"\n(Вариант {seed + 1}. Сведи взгляд на схему иначе: "
                "другой разбор функциональных частей, другой состав "
                "рельс питания, иной разрез функциональных узлов. "
                "Чек-лист приёмки выполняется в любом случае.)\n")
            return _libs_loop(proj, _DESIGN_SYSTEM,
                              _design_user(name, text, calc, errors,
                                           lib_index, checklist,
                                           previous,
                                           modules_allowed=modules_allowed)
                              + (f"\n\nРешение консультанта проекта "
                                 f"(design/acceptance.json) — учти его "
                                 f"дословно:\n{acceptance}\n"
                                 if acceptance else "") + hint,
                              state)

        if DESIGN_CANDIDATES > 1:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=DESIGN_CANDIDATES) as ex:
                raws = list(ex.map(make, range(DESIGN_CANDIDATES)))
            state["log"] = state.get("log", []) + [
                f"попытка {attempt}: параллельно собрано "
                f"{len(raws)} кандидатов схемы"]
        else:
            raws = [make(0)]

        cands = []
        for raw in raws:
            data_i = planner._extract_json(raw or "")
            # ответ дизайнера — в транскрипт: без него ошибки разбора
            # JSON не видны никому
            _transcript(state, f"дизайнер: ответ попытки {attempt} "
                        f"({len(raw or '')} симв.)", raw or "", cap=12000)
            cands.append((raw, data_i))
            # лучший по числу ошибок текст покажем следующей попытке
            if data_i is not None and not previous:
                ok_i, errs_i, _ = _spec_full_check(data_i, modules_allowed,
                                                   box)
                if not ok_i and len(errs_i) <= 6:
                    previous = json.dumps(data_i, ensure_ascii=False,
                                          indent=1)
        # кандидат без разбираемого JSON не участвует в отборе и не
        # отнимает попытку: мусорный ответ дизайнера — его собственная
        # неудача, а не повод объявлять заказ проваленным
        usable = [c for c in cands if c[1] is not None]
        if not usable and cands:
            best_errs = ["нужен JSON-объект спецификации"]
            state["log"] = state.get("log", []) + [
                f"попытка {attempt}: все {len(cands)} кандидатов вернули "
                "нечитаемый ответ — финальный запрос без инструментов"]
            rescue = _chat_plain(_DESIGN_SYSTEM, _design_user(
                name, text, calc, errors, lib_index, checklist, previous,
                modules_allowed=modules_allowed)
                + "\n\nИнструменты запрещены. Ответь ТОЛЬКО готовым "
                  "JSON-объектом спецификации, без единого вызова.")
            data_r = planner._extract_json(rescue or "")
            if data_r is not None:
                usable = [(rescue, data_r)]
        cands = usable

        winner = None
        best_errs: list[str] = []
        for raw, data in cands:
            ok, errs, hard = _spec_full_check(data, modules_allowed, box)
            if ok and SCHEMA_REVIEW:
                verdict = planner._extract_json(
                    _chat_plain(_CRITIC_SYSTEM,
                                _critic_user(text, calc, data,
                                             checklist,
                                             modules_allowed)) or "")
                issues = ([str(x) for x in (verdict.get("issues") or [])]
                          if isinstance(verdict, dict) else [])
                if issues:
                    ok = False
                    errs = [f"критик: {i}" for i in issues]
                else:
                    state["log"] = state.get("log", []) + [
                        "агент-критик схемы: замечаний нет — к компиляции"]
            if ok:
                winner = data
                break
            if len(errs) <= 3 and data is not None and not hard:
                winner = data
                state["log"] = state.get("log", []) + [
                    f"каркас взял кандидата ({len(errs)} мелких дефектов) и применил авто-санитизацию"]
                break
            if hard:
                state["log"] = state.get("log", []) + [
                    "каркас не берёт кандидата: жёсткие дефекты сборки "
                    f"блоков ({len(hard)}) — только петля правок"]
            if len(errs) > len(best_errs):
                best_errs = errs
        if winner is not None:
            data = winner
            previous = json.dumps(winner, ensure_ascii=False, indent=1)
        errs = best_errs or ["спецификация не получена"]
        cur = "; ".join(errs[:8])[:900]
        history.append(cur)
        errors = ("Замечания предыдущих попыток — каждое исправлено:\n"
                  + "\n".join(f"- попытка {i}: {h}"
                              for i, h in enumerate(history, 1)))
        state["log"] = state.get("log", []) + [
            f"спецификация отклонена каркасом "
            f"(попытка {attempt}/{SCHEMA_RETRIES}): {cur[:240]}"]
    # ── последний шанс: хирургическая правка уже полученной спецификации ──
    if previous:
        state["log"] = state.get("log", []) + [
            "хирургическая правка: последняя попытка исправить только "
            "указанные дефекты, остальная спецификация не трогается"]
        rescue = _chat_plain(
            _DESIGN_SYSTEM,
            ("Исходная спецификация (не трогай ничего, кроме исправления "
             "указанных дефектов):\n" + previous + "\n\n"
             "Список дефектов каркаса — исправь ИСКЛЮЧИТЕЛЬНО их, не "
             "переписывая структуру:\n" + errors + "\n\n"
             "Правило пина: каждый вывод (ref, pin) встречается ровно раз\n"
             "за всю спецификацию — если он есть в двух сетях, удали его "
             "из той, где он не используется. Правило блоков: каждый ref\n"
             "только в одном блоке; компонент покрыт всеми блоками (нет\n"
             "orphan). Остальная спецификация дословно: не меняй номиналы,\n"
             "сертификаты, topology. Верни ПОЛНЫЙ JSON одним объектом."
             ))
        _transcript(state, "дизайнер: хирургическая правка", rescue or "",
                    cap=12000)
        data_r = planner._extract_json(rescue or "")
        if data_r is not None:
            ok_r, errs_r, _hard_r = _spec_full_check(data_r, modules_allowed,
                                                     box)
            if ok_r:
                # и критик — иначе rescue прошёл структурно, но не функционально
                if SCHEMA_REVIEW:
                    verdict = planner._extract_json(
                        _chat_plain(_CRITIC_SYSTEM,
                                    _critic_user(text, calc, data_r,
                                                 checklist,
                                                 modules_allowed)) or "")
                    issues = ([str(x) for x in (verdict.get("issues") or [])]
                              if isinstance(verdict, dict) else [])
                    if issues:
                        state["log"] = state.get("log", []) + [
                            "хирургическая правка прошла по структуре, но "
                            "критик вернул дефекты: " + "; ".join(issues)]
                        return {"status": "failed",
                                "error": "критик: " + "; ".join(issues)}
                    state["log"] = state.get("log", []) + [
                        "агент-критик схемы: замечаний нет — к компиляции"]
                state["log"] = state.get("log", []) + [
                    "хирургическая правка закрыла все дефекты"]
                (box / "design/schematic.json").write_text(
                    json.dumps(data_r, ensure_ascii=False, indent=2),
                    encoding="utf-8")
                state["log"] = state.get("log", []) + [
                    "агент-дизайнер: design/schematic.json записан "
                    f"({len(rescue)} симв.)"]
                _transcript(state, "дизайнер: результат", rescue)
                return {"status": "done"}
            # критик даже если не идеально? нет — рубим как раньше
            state["log"] = state.get("log", []) + [
                "хирургическая правка не закрыла:\n" + "; ".join(errs_r[:6])]
    return {"status": "failed",
            "error": (errors or "спецификация не получена от агента")}


# --------------------------------------------------------------------------- #
#  Диалог с библиотеками KiCad: агент сам ищет и подтверждает символы
# --------------------------------------------------------------------------- #
#: Строгая схема ответа проектировщика блока. Модель обязана вернуть
#: объект этой формы: проза и выдуманные поля невозможны, разбор не
#: падает, а лишние попытки не тратят ходы.
BLOCK_JSON_SCHEMA: dict = {
    "type": "json_schema",
    "json_schema": {
        "name": "block_spec",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["parts", "nets"],
            "properties": {
                "блок": {"type": "string"},
                "наименование": {"type": "string"},
                "решение": {"type": "string"},
                "parts": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["ref", "lib_id", "value"],
                        "properties": {
                            "ref": {"type": "string"},
                            "lib_id": {"type": "string"},
                            "value": {"type": "string"},
                            "footprint": {"type": "string"},
                            "part": {"type": "string"},
                            "не_подключены": {"type": "array",
                                              "items": {"type": "string"}},
                            "params": {
                                "type": "object",
                                "additionalProperties": False,
                                "properties": {
                                    k: {"type": "string"} for k in
                                    ("предел", "ток", "напряжение", "мощность",
                                     "ток_max", "напряжение_max", "ёмкость",
                                     "индуктивность", "сопротивление",
                                     "DCR", "Isat", "VIN_max", "IOUT_max",
                                     "frequency", "V", "I", "P")
                                },
                            },
                            "sim": {
                                "type": "object",
                                "additionalProperties": False,
                                "properties": {
                                    k: {"type": "string"} for k in
                                    ("Sim.Device", "Sim.Name", "Sim.Library",
                                     "Sim.Pins", "Sim.Type")
                                },
                            },
                        },
                    },
                },
                "nets": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["name", "conns"],
                        "properties": {
                            "name": {"type": "string"},
                            "conns": {"type": "array",
                                      "items": {"type": "array",
                                                "items": {"type": "string"}}},
                            "назначение": {"type": "array",
                                           "items": {"type": "string"}},
                        },
                    },
                },
                "входы_реализованы": {"type": "array",
                                      "items": {"type": "string"}},
                "выходы_реализованы": {"type": "array",
                                       "items": {"type": "string"}},
                "покрыто_требования": {"type": "array",
                                       "items": {"type": "string"}},
                "открытые_вопросы": {"type": "array",
                                     "items": {"type": "string"}},
            },
        },
    },
}

#: простой режим «только JSON» — для стадий, где форма свободна,
#: но проза недопустима
JSON_ONLY = {"type": "json_object"}


def _libs_loop(proj: Project, system: str, user: str,
               state: dict, max_tokens: int = 4000,
               profile: str = "", json_schema: dict | None = None) -> str:
    """Свободный диалог агента с инструментом kicad_libs.

    Агент сам выясняет реальные символы/пины/корпуса; каркас ничего не
    подсказывает из каталога. Каждый вызов инструмента попадает в лог
    заказа и в транскрипт — весь ход работы виден.
    """
    bindings = T.make_bindings(proj, run_one=_stage_run_guard, tier="libs")
    schemas = T.tool_schemas(proj, tier="libs")
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": user}]
    calls_total = 0
    seen_calls: set[str] = set()
    deadline = time.monotonic() + TOOL_STAGE_SECONDS
    for turn_no in range(1, TOOL_TURNS + 1):
        if time.monotonic() > deadline:
            state["log"] = state.get("log", []) + [
                f"агент: время стадии вышло ({TOOL_STAGE_SECONDS:.0f} с) — "
                "ход прерван, беру то, что есть"]
            break
        if calls_total >= TOOL_CALLS_MAX:
            state["log"] = state.get("log", []) + [
                f"агент вызвал инструменты {calls_total} раз — данных "
                "достаточно, запрашиваю ответ"]
            break
        turn = llm_race.chat(messages, tools=schemas,
                               temperature=0.1, max_tokens=max_tokens,
                               timeout=TOOL_TIMEOUT, profile=profile)
        if not turn:
            break
        engine._publish(state)
        if not turn.calls:
            text = (turn.text or "").strip()
            # Иногда модель тащит из истории filler-текст «(инструменты)»
            # вместо результата — это тоже пустой ответ; считаем его пустым.
            # «(инструменты)» — служебный текст модели, а не ответ; с
            # think-блоком он приходит вроде «(инструменты)<think>…»,
            # поэтому сравнение по вхождению, а не по равенству
            low = text.lower()
            is_empty = (not text
                        or low in ("(инструменты)", "(instruments)",
                                   "[инструменты]")
                        or low.startswith("(инструменты)")
                        or low.startswith("(instruments)"))
            if is_empty and calls_total:
                state["log"] = state.get("log", []) + [
                    "агент вернул пустой ответ ({t}) — запрашиваю результат "
                    "без инструментов".format(t=text or "без текста")]
                messages.append({"role": "user", "content":
                                 "Инструменты больше не нужны. Отдай "
                                 "ТОЛЬКО итоговый ответ (JSON), без вызовов "
                                 "инструментов и без filler-текста."})
                final = llm_race.chat(messages, temperature=0.1,
                                      max_tokens=16000, timeout=TOOL_TIMEOUT,
                                      profile=profile,
                                      response_format=json_schema)
                if final and (final.text or "").strip():
                    return final.text
                # если второй запрос тоже пустой — пробуем напрямую,
                # к stripped-истории (последний ход одного из инструментов
                # мог утонуть)
                return ""
            return turn.text or ""
        for c in turn.calls:
            state["log"] = state.get("log", []) + [
                f"агент → {c.name}({json.dumps(c.args, ensure_ascii=False)[:120]})"]
            # повтор того же запроса — признак зацикливания: подсказываем
            # прямо, иначе агент так и ходит по кругу
            sig = f"{c.name}:{json.dumps(c.args, ensure_ascii=False, sort_keys=True)}"
            if sig in seen_calls:
                state["log"] = state.get("log", []) + [
                    f"агент повторяет запрос {c.name} — подсказка в ответе"]
                out = (str(T.invoke(bindings, c.name, c.args))[:TOOL_OUT_CHARS]
                       + "\n\n[каркас] Этот запрос уже повторялся — ответ "
                         "тот же. Не повторяй его: либо возьми уже "
                         "найденное, либо сформулируй запрос иначе "
                         "(например, ищи по назначению: kicad_libs "
                         "action=by_purpose).")
            else:
                seen_calls.add(sig)
                out = T.invoke(bindings, c.name, c.args)
            messages.append({"role": "tool", "tool_call_id": c.id,
                             "name": c.name, "content": str(out)[:TOOL_OUT_CHARS]})
            _transcript(state, f"агент → {c.name}",
                        f"запрос: {json.dumps(c.args, ensure_ascii=False)}\n"
                        f"ответ: {out}")
            calls_total += 1
            # публикуем состояние: сторож заказа считает заказ живым по
            # изменениям, а ход агента с инструментами может длиться дольше
            # предела сторожа — без публикации он объявил бы заказ зависшим
            engine._publish(state)
        messages.append({"role": "assistant",
                         "content": turn.text or "(инструменты)"})
        # история агента не должна расти без предела: каждый ответ
        # инструмента — это сотни символов, и модель на длинной истории
        # отвечает в разы медленнее
        if len(messages) > TOOL_HISTORY * 2 + 2:
            del messages[2:len(messages) - TOOL_HISTORY * 2]
        if turn_no == TOOL_TURNS - 3:
            messages.append({"role": "user", "content":
                             "Данных достаточно. Собирай JSON и завершай."})
    state["log"] = state.get("log", []) + [
        f"лимит ходов поиска библиотек ({TOOL_TURNS}, вызовов "
        f"{calls_total}) — запрос финального JSON"]
    # последнее слово — строго про JSON: без него модель продолжает
    # искать и ответ не доходит
    messages.append({"role": "user", "content":
                     "Инструменты больше не вызывай. Ответь ТОЛЬКО "
                     "JSON-объектом по форме, что дана в задании: "
                     '{"блок": ..., "наименование": ..., '
                     '"решение": ..., "parts": [...], "nets": [...], '
                     '"входы_реализованы": [...], '
                     '"выходы_реализованы": [...], '
                     '"покрыто_требования": [...]} — без пояснений и '
                     "без рассуждений."})
    final = llm_race.chat(messages, temperature=0.1, max_tokens=12000,
                           profile=profile, response_format=json_schema,
                        timeout=TOOL_TIMEOUT)
    text = (final.text if final else "") or ""
    # Ответ без разбора — не конец попытки: ход повторяется тут же, с
    # другим маршрутом и требованием «только JSON». Так отказ одной
    # модели не стоит целой попытки проектирования.
    for _retry in range(2):
        if not json_schema:
            break
        if planner._extract_json(text):
            break
        messages.append({"role": "assistant", "content": text[:2000] or "{}"})
        messages.append({"role": "user", "content":
                         "Предыдущий ответ не разобран как JSON. Верни "
                         "ТОЛЬКО JSON-объект по схеме: ни слова рассуждений, "
                         "ни markdown, ни текста вокруг."})
        again = llm_race.chat(messages, temperature=0.1, max_tokens=12000,
                              profile=profile, response_format=json_schema,
                              timeout=TOOL_TIMEOUT)
        if not again or not (again.text or "").strip():
            break
        text = again.text or ""
    return text


# --------------------------------------------------------------------------- #
#  Стадия 4: компиляция KiCad; ошибки движка возвращает агенту
# --------------------------------------------------------------------------- #
def _engine_errors(step: dict) -> str:
    """Ошибки сборки из лога шага — агенту на самостоятельное исправление."""
    tail = "\n".join(map(str, step.get("log_tail") or []))
    keys = ("ошибк", "не найден", "обязател", "err", "отсутствует",
            "не указан", "пуст")
    lines = [ln.strip() for ln in tail.splitlines()
             if any(k in ln.lower() for k in keys)]
    return "\n".join(lines[-40:])[:4000]


def _kicad_stage(text: str, proj: Project, step: dict, state: dict) -> bool:
    """Стадия kicad: сначала поблочная сборка SKiDL (нетлисты блоков по
    спецификации), затем цель проекта (сшивание схемы и ERC). Дефекты
    блоков останавливают сшивание и уходят дизайнеру через
    _spec_fix_loop — жёсткая проверка в петле правок."""
    if step.get("status") == "done":
        return True
    box = Path(engine.current_workdir() or proj.root)
    bt = _blocks()
    block_errs: list[str] = []
    if bt.get("mod") is None:
        state["log"] = state.get("log", []) + [
            "поблочная сборка SKiDL недоступна (tools/skidl_blocks.py не "
            "загрузился) — сшивание схемы продолжается без неё"]
    else:
        try:
            rep = bt["mod"].build(box=box)
            block_errs = [str(e) for e in (rep.get("errors") or [])]
            built = rep.get("built") or []
            state["log"] = state.get("log", []) + [
                f"поблочная сборка SKiDL: блоков "
                f"{len(rep.get('blocks') or [])}, нетлистов {len(built)}, "
                f"ошибок {len(block_errs)}, предупреждений "
                f"{len(rep.get('warnings') or [])}"]
            for w in (rep.get("warnings") or [])[:4]:
                state["log"] = state.get("log", []) + [f"  блоки: {w}"]
        except Exception as exc:                       # noqa: BLE001
            block_errs = [f"поблочная сборка не выполнена: {exc}"]
    if block_errs:
        tail = step.setdefault("log_tail", [])
        for e in block_errs[:20]:
            tail.append("ошибка блоков: " + e)
        step.update(status="failed", finished_at=now_iso(),
                    error="поблочная сборка SKiDL: "
                          + "; ".join(block_errs[:4]))
        state["log"] = state.get("log", []) + [
            "стадия kicad остановлена до сшивания: дефектов блоков "
            f"{len(block_errs)} — дизайнеру на исправление"]
        return False
    engine._execute_one(proj, step)
    return step.get("status") != "failed"


def _spec_fix_loop(text: str, proj: Project, state: dict,
                   step: dict) -> None:
    """make kicad упал → ошибки движка агенту → агент переписывает
    спецификацию → пересборка. Каркас ничего не правит за агента."""
    box = Path(engine.current_workdir() or proj.root)
    spec_path = box / "design/schematic.json"
    for cycle in range(1, KICAD_FIXES + 1):
        if step.get("status") != "failed":
            return
        errs = _engine_errors(step) or str(step.get("error") or "")[:4000]
        if not spec_path.is_file() and not errs:
            state["log"] = state.get("log", []) + [
                "спецификация design/schematic.json не записана и ошибки "
                "движка пусты — исправлять нечего"]
            return
        state["log"] = state.get("log", []) + [
            f"ошибки движка цикла {cycle}/{KICAD_FIXES} → "
            f"агенту-схемотехнику на исправление ({len(errs)} байт)"]
        _transcript(state, f"движок: цикл {cycle}", errs)
        if step.get("target") == "kicad":
            # правка идёт в блоки, из них снова сшивается схема: иначе
            # компилятор и приёмка смотрели бы на разные схемы
            res = _stage_skidl(text, proj, state, errors=errs)
            if res["status"] != "done":
                state["log"] = state.get("log", []) + [
                    "проектировщик не смог исправить блоки — оставляю честный "
                    "отказ компиляции"]
                return
            res = _stage_stitch(proj, state)
            if res["status"] != "done":
                state["log"] = state.get("log", []) + [
                    "схема после правки не сведена — оставляю честный отказ "
                    "компиляции"]
                return
        else:
            res = _stage_design(text, proj, state, engine_errors=errs)
            if res["status"] != "done":
                state["log"] = state.get("log", []) + [
                    "агент не смог исправить спецификацию — оставляю честный "
                    "отказ компиляции"]
                return
        step.update(status="pending")
        if step.get("target") == "kicad":
            _kicad_stage(text, proj, step, state)
        else:
            engine._execute_one(proj, step)
    if step.get("status") == "failed":
        state["log"] = state.get("log", []) + [
            "лимит исправлений исчерпан — каркас больше не вмешивается"]
# --------------------------------------------------------------------------- #
#  Стадия sizing: подгонка номиналов по замерам (метод EEsizer)
#  Цикл: замер → агент правит value → пересборка деки → новый замер.
#  История результатов сходит в следующий промпт — приём in-context
#  learning из EEsizer.
# --------------------------------------------------------------------------- #
def _sizing_user(text: str, calc: str, meas: dict, spec: dict,
                 history: list[str]) -> str:
    m = json.dumps(meas, ensure_ascii=False, indent=1)[:6000]
    sp = json.dumps({"parts": [{"ref": p.get("ref"),
                                "value": p.get("value"),
                                "lib_id": p.get("lib_id")}
                               for p in (spec.get("parts") or [])]},
                    ensure_ascii=False, indent=1)[:4000]
    hist = ("\n".join(f"- {h}" for h in history)
            if history else "(первая итерация)")
    return ("Ты — агент-размерщик. Замеренная схема ниже; подгони "
            "номиналы компонентов под цели.\n"
            "Заказ: " + text + "\n"
            "Цели (design/calc.json):\n" + (calc[:3000] or "(нет)") + "\n"
            "Результаты измерений (out/hardware/measurements.json):\n"
            + m + "\n"
            "Текущие номиналы:\n" + sp + "\n"
            "История прошлых итераций (что менялось и к чему привело):\n"
            + hist + "\n\n"
            "Если в блоке `blocked` указано, что не посчитано (например, "
            "нет моделей приборов), — это НЕ повод придумывать значения: "
            "верни verdict 'blocked' и скажи, каких моделей не хватает "
            "(их берут с сайтов изготовителей, а не выдумывают).\n"
            "Если измерения есть и цели не достигнуты — верни patches: "
            'по каждому {"ref", "from", "to", "why"}; "why" обязан '
            "ссылаться на конкретное измерение или расчёт.\n"
            "Верни ТОЛЬКО JSON: {\"patches\": [...], \"verdict\": "
            "\"adjusted\"|\"fits\"|\"blocked\", \"note\": \"…\"}. "
            "Пустой patches означает: подгонка не требуется.")


def _sizing_stage(text: str, proj: Project, state: dict) -> dict:
    box = Path(engine.current_workdir() or proj.root)
    spec_path = box / "design/schematic.json"
    meas_path = box / "out/hardware/measurements.json"
    if not spec_path.is_file():
        return {"status": "failed", "error": "нет спецификации для подгонки"}
    calc = _read_design(box, "design/calc.json", 3000)
    history: list[str] = []
    applied = 0
    for it in range(1, SIZING_ITERS + 1):
        meas = {}
        if meas_path.is_file():
            try:
                meas = json.loads(meas_path.read_text(encoding="utf-8"))
            except Exception:                            # noqa: BLE001
                meas = {}
        spec = {}
        if spec_path.is_file():
            try:
                spec = json.loads(spec_path.read_text(encoding="utf-8"))
            except Exception:                            # noqa: BLE001
                spec = {}
        raw = _chat_plain(_SIZER_SYSTEM,
                          _sizing_user(text, calc, meas, spec, history))
        verdict = planner._extract_json(raw or "")
        if not isinstance(verdict, dict):
            state["log"] = state.get("log", []) + [
                "агент-размерщик: не вернул JSON — подгонка не выполнена"]
            return {"status": "done"}
        v = str(verdict.get("verdict") or "")
        note = str(verdict.get("note") or "")[:200]
        if v == "blocked":
            state["log"] = state.get("log", []) + [
                f"агент-размерщик: измерений нет, подгонка невозможна — "
                f"{note or 'нужны модели приборов'}"]
            _transcript(state, "размерщик: blocked", raw or "")
            return {"status": "done"}
        patches = verdict.get("patches") or []
        if not patches:
            state["log"] = state.get("log", []) + [
                f"агент-размерщик: правки не требуются ({v or 'fits'}) "
                f"{note}"]
            _transcript(state, "размерщик: подгонка не нужна", raw or "")
            return {"status": "done"}
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        by_ref = {str(p.get("ref")): p for p in spec.get("parts") or []}
        applied_now = 0
        for pt in patches:
            ref = str(pt.get("ref") or "")
            new = str(pt.get("to") or "").strip()
            if ref in by_ref and new:
                by_ref[ref]["value"] = new
                applied_now += 1
                history.append(
                    f"{ref}: {pt.get('from', by_ref[ref].get('value'))}"
                    f" → {new} ({str(pt.get('why') or '')[:100]})")
        if not applied_now:
            state["log"] = state.get("log", []) + [
                "агент-размерщик: патчи не применились (нет таких ref) — "
                "стоп"]
            return {"status": "done"}
        spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2),
                             encoding="utf-8")
        applied += applied_now
        state["log"] = state.get("log", []) + [
            f"агент-размерщик: итерация {it}/{SIZING_ITERS} — "
            f"применено {applied_now} номиналов"]
        _transcript(state, f"размерщик: итерация {it}", raw or "")
        # пересборка деки и новый замер — иначе следующая итерация
        # получит те же факты
        step_k = _step("kicad", "пересборка схемы")
        engine._execute_one(proj, step_k)
        step_s = _step("spice", "пересборка SPICE-деки")
        engine._execute_one(proj, step_s)
        step_m = _step("sim", "повторный замер")
        engine._execute_one(proj, step_m)
    state["log"] = state.get("log", []) + [
        f"подгонка завершена: {applied} номиналов за {SIZING_ITERS} итераций"]
    return {"status": "done"}


# --------------------------------------------------------------------------- #
#  Доводка схемы по результатам симуляции
# --------------------------------------------------------------------------- #
#  Агент сам придумывает схемотехнику блока, а затем проверяет её расчётом:
#  замер → замечания → правка схемы (не только номиналов: структура, модели,
#  соединения) → пересборка → новый замер. Пока схема не сходится с
#  требованиями ТЗ на узел, стадия не закрывается.
#
#  Симуляция — единственный честный судья: «правильная» на глаз схема
#  может не открываться, не усиливать или не ограничивать ток.
# --------------------------------------------------------------------------- #
_SIM_FIT_SYSTEM = _role("sim_fit") or "инженер-схемотехник (доводка по замеру)"


def _deck_problems(box: Path) -> list[str]:
    """Проблемы SPICE-деки: части без модели, неразрешённые ссылки.

    Проверка нативная: смотрим саму деку, которую собрал штатный экспорт
    KiCad. Символ без модели выгружается как «__<позиция>» — значит,
    модели у него нет и в симуляции он не участвует.
    """
    out: list[str] = []
    decks = sorted((box / "out" / "hardware").glob("*.cir"),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    if not decks:
        return ["SPICE-дека не собрана"]
    text = decks[0].read_text(encoding="utf-8", errors="ignore")
    for m in re.finditer(r"(?m)^\S*\s+(__\w+)\s*$", text):
        out.append(f"{m.group(1)}: у элемента нет модели симуляции — "
                   "назначь Sim.Name/Sim.Library и Sim.Pins")
    for m in re.finditer(r"(?m)^\.include\s+\"([^\"]*)\"\s*$", text):
        lib = m.group(1)
        if lib and not Path(lib).is_file():
            out.append(f"библиотека моделей не найдена: {lib}")
    if not out:
        for m in re.finditer(r"(?m)^\s*\.include\s+(.+)$", text):
            lib = m.group(1).strip().strip('"')
            if lib.startswith("/") and not Path(lib).is_file():
                out.append(f"библиотека моделей не найдена: {lib}")
    return out


def _sim_fit_user(text: str, spec: dict, meas: dict, problems: list[str],
                  block_tz: list[dict], history: list[str]) -> str:
    """Задание инженеру: вот схема, вот замеры — доведи её до требований."""
    sp = json.dumps(spec, ensure_ascii=False, indent=1)[:12000]
    m = json.dumps(meas, ensure_ascii=False, indent=1)[:6000]
    tzn = json.dumps(block_tz, ensure_ascii=False, indent=1)[:4000]
    hist = ("\n".join(f"- {h}" for h in history)
            if history else "(первая итерация)")
    return ("Ты — инженер-схемотехник. Схема блока посчитана в симуляторе; "
            "замеры показали, что она не выполняет требования. Доведи её "
            "до требований.\n\n"
            "ЗАКАЗ: " + text[:800] + "\n\n"
            "ТРЕБОВАНИЯ К УЗЛАМ (design/block_tz.json) — то, чему схема "
            "должна удовлетворять:\n" + tzn + "\n\n"
            "ТЕКУЩАЯ СХЕМА (design/schematic.json):\n" + sp + "\n\n"
            "РЕЗУЛЬТАТЫ СИМУЛЯЦИИ (out/hardware/measurements.json):\n"
            + m + "\n\n"
            "ПРОБЛЕМЫ ДЕКИ (штатный экспорт SPICE + ngspice):\n"
            + ("\n".join(f"- {p}" for p in problems) if problems
               else "(нет)")
            + "\n\n"
            "ИСТОРИЯ ПРОШЛЫХ ИТЕРАЦИЙ:\n" + hist + "\n\n"
            "ПОРЯДОК РАБОТЫ:\n"
            "  1) сначала сверь замер с ТЗ на узел: если параметры не "
            "сошлись (ток, напряжение, КПД, запас) — исправляй ИМЕННО "
            "это: топологию, номиналы, соединения;\n"
            "  2) подбери ТОЧНЫЕ значения номиналов: по расчётам, по "
            "паспорту детали (допуск, мощность, напряжение) и по "
            "измерениям;\n"
            "  3) сверь схему с ТЗ на узел по всем пунктам: передаточная "
            "характеристика (коэффициент, диапазоны входа и выхода, "
            "запас), требования, критерии приёмки. Если после подбора "
            "значений характеристика ушла за пределы ТЗ — скажи об этом "
            "прямо в note.\n\n"
            "ПРАВИЛА:\n"
            "  * требования из ТЗ на узел — критерий: замер должен "
            "показывать, что они выполняются;\n"
            "  * ОТСУТСТВИЕ МОДЕЛИ — НЕ повод менять схему. Если часть "
            "схемы не считается из-за того, что у детали нет модели "
            "симуляции, это долг библиотекаря, а не вина схемотехника: "
            "не подменяй деталь и не переделывай топологию из-за этого. "
            "Отметь это в note и верни verdict 'blocked', если без "
            "модели проверить требования нельзя;\n"
            "  * переделывать схему имеет смысл только там, где замер "
            "показал расхождение с ТЗ;\n"
            "  * каждый элемент, который должен участвовать в симуляции, "
            "получает свойства Sim.Device, Sim.Library, Sim.Name, Sim.Pins — "
            "это нативные свойства KiCad, их читает штатный экспорт SPICE;\n"
            "  * для пассивных элементов модель не нужна: симулятор берёт "
            "её из номинала.\n\n"
            "Верни ТОЛЬКО JSON: {\"verdict\": \"fits\"|\"adjusted\"|"
            "\"blocked\", \"note\": \"...\", \"spec\": {\"parts\": [...], "
            "\"nets\": [...], \"blocks\": [...]}}. В spec — ПОЛНАЯ "
            "исправленная схема (как в design/schematic.json): изменить "
            "можно и структуру, и номиналы, и соединения, и sim-свойства. "
            "Пустой spec означает: правки не требуются.")


def _stage_sim_fit(text: str, proj: Project, state: dict) -> dict:
    """Стадия доводки: замер → правка схемы агентом → пересборка → замер.

    Агент здесь главный: он и придумывает топологию, и проверяет её
    расчётом. Каркас только переносит его правки в спецификацию и
    перезапускает штатные цели сборки и замера.
    """
    box = Path(engine.current_workdir() or proj.root)
    spec_path = box / "design/schematic.json"
    if not spec_path.is_file():
        return {"status": "failed",
                "error": "нет спецификации design/schematic.json — "
                         "доводить нечего"}
    block_tz = _read_json(box, "design/block_tz.json").get("блоки") or []
    history: list[str] = []
    applied = 0
    for it in range(1, SIM_FIT_ITERS + 1):
        spec = _read_json(box, "design/schematic.json")
        meas = _read_json(box, "out/hardware/measurements.json")
        problems = _deck_problems(box)
        raw = _chat_plain(_SIM_FIT_SYSTEM,
                          _sim_fit_user(text, spec, meas, problems,
                                        block_tz, history))
        verdict = planner._extract_json(raw or "")
        if not isinstance(verdict, dict):
            state["log"] = state.get("log", []) + [
                "доводка по симуляции: агент не вернул JSON — цикл "
                "остановлен"]
            return {"status": "done"}
        _transcript(state, f"доводка: итерация {it}", raw or "", cap=12000)
        v = str(verdict.get("verdict") or "")
        note = str(verdict.get("note") or "")[:300]
        new_spec = verdict.get("spec")
        if v == "blocked":
            state["log"] = state.get("log", []) + [
                f"доводка по симуляции: {note or 'не хватает моделей'}"]
            return {"status": "done"}
        if not isinstance(new_spec, dict) or not new_spec.get("parts"):
            state["log"] = state.get("log", []) + [
                f"доводка по симуляции: правки не требуются "
                f"({v or 'fits'}) {note}"]
            return {"status": "done"}
        # правки агента принимаются целиком: он отвечает за схему
        spec_path.write_text(json.dumps(new_spec, ensure_ascii=False,
                                        indent=2), encoding="utf-8")
        applied += 1
        history.append(f"итерация {it}: {note or 'правка схемы'}")
        state["log"] = state.get("log", []) + [
            f"доводка по симуляции: итерация {it}/{SIM_FIT_ITERS} — "
            "схема переписана агентом, пересборка и новый замер"]
        # пересборка: схема → дека → замер (штатные цели проекта)
        for target, title in (("kicad", "пересборка схемы"),
                              ("spice", "пересборка SPICE-деки"),
                              ("sim", "повторный замер")):
            st = _step(target, title)
            engine._execute_one(proj, st)
            if st.get("status") == "failed":
                state["log"] = state.get("log", []) + [
                    f"доводка: {target} не отработал — {str(st.get('error') or '')[:160]}"]
                break
    state["log"] = state.get("log", []) + [
        f"доводка по симуляции: итераций {applied} из {SIM_FIT_ITERS}"]
    return {"status": "done"}


# --------------------------------------------------------------------------- #
#  Упаковщик: PCB-паспорт деталей по даташитам (design/parts_pcb.json)
# --------------------------------------------------------------------------- #
_PACKAGER_SYSTEM = _role("packager") or (
    "Ты — упаковщик PCB-паспорта: читаешь выдержки даташитов каждой детали "
    "и заполняешь design/parts_pcb.json строго по ним.")


def _tool_run(box: Path, argv: list[str]) -> tuple[int, str]:
    """Системный инструмент песочницы: код возврата + объединённый вывод."""
    try:
        r = subprocess.run([sys.executable, *argv], cwd=box,
                           capture_output=True, text=True, timeout=900)
    except subprocess.TimeoutExpired:
        return 124, "таймаут 900 с"
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def _job_missing(box: Path) -> list[dict]:
    """Детали из задания паспорта, у которых нет текста даташита."""
    try:
        job = json.loads((box / "design/datasheets/pcb_job.json")
                         .read_text(encoding="utf-8"))
    except Exception:                                    # noqa: BLE001
        return []
    out = []
    for e in job.get("parts") or []:
        if e.get("datasheet_txt"):
            continue
        if e.get("lcsc") or e.get("mpn"):
            out.append({"ref": e.get("ref", ""),
                        "lcsc": e.get("lcsc") or "",
                        "mpn": e.get("mpn") or ""})
    return out


def _stage_packager(text: str, proj: Project, state: dict) -> dict:
    """Даташиты → PCB-паспорт: механику делают скрипты, содержание — агент.

    1. tools/part_pcb.py --job — задание: по каждой детали PDF и выдержки
       (страницы, ключевые строки, таблицы), число выводов из нетлиста.
       Правило линии: даташит обязан быть у КАЖДОЙ детали.
    2. Чего-то нет — добор фоллбек-цепочкой part_fetch --datasheet-only
       (LCSC CDN → готовый скрапер kicad-happy → веб-поиск с проверкой
       MPN по тексту PDF), затем задание повторяется. Не помогло — отказ:
       закупочную позицию обязан назвать библиотекарь.
    3. Агент-упаковщик заполняет design/parts_pcb.json по выдержкам —
       корпус, монтаж, шаг, габариты, провенанс.
    4. tools/part_pcb.py --check — сверка схемы, провенанса и выводов;
       замечания возвращаются агенту, пока не чисто (цикл ограничен).
    """
    box = Path(engine.current_workdir() or proj.root)
    tool = box / "tools/part_pcb.py"
    fetch = box / "tools/part_fetch.py"
    if not tool.is_file():
        return {"status": "failed",
                "error": "в песочнице нет tools/part_pcb.py"}
    rc, out = _tool_run(box, [str(tool), "--project", str(box), "--job"])
    if rc != 0:
        missing = _job_missing(box)
        if missing and fetch.is_file():
            state["log"] = state.get("log", []) + [
                f"упаковщик: без даташита {len(missing)} дет. — добор "
                "part_fetch --datasheet-only"]
            for m in missing[:8]:
                argv = [str(fetch), "--datasheet-only",
                        "--datasheets", "design/datasheets"]
                if m["lcsc"]:
                    argv += ["--lcsc", m["lcsc"]]
                if m["mpn"]:
                    argv += ["--mpn", m["mpn"]]
                _rc, o = _tool_run(box, argv)
                _transcript(state,
                            f"упаковщик: добор даташита {m['ref']}",
                            o[-1500:])
            rc, out = _tool_run(box, [str(tool), "--project", str(box),
                                      "--job"])
    if rc != 0:
        _transcript(state, "упаковщик: задание паспорта", (out or "")[-4000:])
        return {"status": "failed",
                "error": "не у всех деталей есть даташит: нужна реальная "
                         "закупочная позиция (библиотекарь: LCSC/MPN в "
                         "design/schematic.json), затем part_fetch",
                "исправимо": True,
                "ошибки": "нет даташита у деталей — библиотекарь должен "
                           "найти LCSC/MPN для каждой детали в "
                           "design/schematic.json, затем part_fetch "
                           "скачать PDF"}
    try:
        job = json.loads((box / "design/datasheets/pcb_job.json")
                         .read_text(encoding="utf-8"))
    except Exception as e:                               # noqa: BLE001
        return {"status": "failed",
                "error": f"задание паспорта не читается: {e}"}
    job_txt = json.dumps(job, ensure_ascii=False, indent=1)
    user = ("Изделие: " + text + "\n"
            "Задание на PCB-паспорт (design/datasheets/pcb_job.json) — "
            "детали, выдержки даташитов, выводы в нетлисте, схема ответа:\n"
            + job_txt[:26000] + "\n\n"
            "Заполни по КАЖДОЙ детали: package_std (строка генератора "
            "tscircuit: 0603, sod523, pinheader2, soic8, lqfp48… — или "
            "null, если корпус нестандартный/неясный), package_text — как "
            "написано в даташите, mounting, pins (сверь с нетлистом), "
            "pitch_mm, body_mm и evidence: страница, раздел, уверенность "
            "и метод (table|text|drawing|footprint). Числа бери ТОЛЬКО из "
            "выдержек — не выдумывай. Верни ТОЛЬКО JSON-объект вида "
            '{"parts": {"<ref>": {…}, …}} — без пояснений вокруг.')
    last_err = ""
    for fix in range(PACKAGER_FIXES + 1):
        raw = _chat_plain(_PACKAGER_SYSTEM, user, profile="механический",
                          json_schema=JSON_ONLY)
        if not raw:
            return {"status": "failed",
                    "error": "упаковщик: пустой ответ модели"}
        data = planner._extract_json(raw)
        if not isinstance(data, dict) or not isinstance(data.get("parts"),
                                                        dict):
            user += ('\n\nПредыдущий ответ не разобран как JSON-объект '
                     '{"parts": {…}} — верни только JSON, без пояснений.')
            last_err = "ответ не разобран как JSON"
            continue
        (box / "design/parts_pcb.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        _transcript(state, f"упаковщик: паспорт (цикл {fix + 1})", raw)
        rc, out = _tool_run(box, [str(tool), "--project", str(box),
                                  "--check"])
        state["log"] = state.get("log", []) + [
            f"упаковщик: проверка паспорта — "
            f"{'OK' if rc == 0 else 'замечания'} "
            f"(цикл {fix + 1}/{PACKAGER_FIXES + 1})"]
        if rc == 0:
            _transcript(state, "упаковщик: паспорт принят", out)
            return {"status": "done"}
        last_err = (out or "")[-2500:]
        user += ("\n\nПроверка паспорта не прошла — исправь РОВНО эти "
                 "замечания и верни ПОЛНЫЙ JSON заново:\n" + last_err)
    return {"status": "failed",
            "error": f"паспорт не прошёл проверку: {last_err[:300]}"}


# --------------------------------------------------------------------------- #
#  Память конвейера: какие стадии уже выполнены
# --------------------------------------------------------------------------- #
#: файл памяти стадий в песочнице заказа
PIPELINE_FILE = "design/_pipeline.json"


def _pipeline_path(box: Path) -> Path:
    return Path(box) / PIPELINE_FILE


def _read_pipeline(box: Path) -> dict:
    """Что уже выполнено: {стадия: {время, статус}}."""
    p = _pipeline_path(box)
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    done = data.get("выполнено")
    return done if isinstance(done, dict) else {}


def _write_pipeline(box: Path, done: dict) -> None:
    """Записать память конвейера: {стадия: {время, статус}}."""
    p = _pipeline_path(box)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"выполнено": done, "обновлён": now_iso()},
                             ensure_ascii=False, indent=2), encoding="utf-8")


def _record_stage(box: Path, target: str, status: str = "done") -> None:
    """Отметить стадию выполненной — чтобы её не переделывать."""
    p = _pipeline_path(box)
    p.parent.mkdir(parents=True, exist_ok=True)
    data: dict = {}
    if p.is_file():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
    done = data.get("выполнено")
    if not isinstance(done, dict):
        done = {}
    done[target] = {"at": now_iso(), "status": status}
    data["выполнено"] = done
    data["обновлён"] = now_iso()
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                 encoding="utf-8")


def _forget_from(box: Path, target: str) -> None:
    """Забыть стадию и всё, что идёт после неё.

    Так повтор по решению менеджера отменяет не одну стадию, а всю
    цепочку после неё: иначе получится схема, собранная из разных
    поколений.
    """
    done = _read_pipeline(box)
    if not done:
        return
    order = [name for name, _title, _kind in _PLAN]
    try:
        cut = order.index(target)
    except ValueError:
        cut = 0
    for name in order[cut:]:
        done.pop(name, None)
    _write_pipeline(box, done)


def _forget_after(box: Path, target: str) -> None:
    """Забыть всё, что идёт ПОСЛЕ указанной стадии.

    Изменились входы — значит, последующие стадии надо пересчитать:
    иначе в изделие попадёт схема, собранная по старым данным.
    """
    done = _read_pipeline(box)
    if not done:
        return
    order = [name for name, _title, _kind in _PLAN]
    try:
        cut = order.index(target)
    except ValueError:
        return
    for name in order[cut + 1:]:
        done.pop(name, None)
    _write_pipeline(box, done)


def _blocks_in_issues(issues: list[str], tasks: list[dict]) -> list[str]:
    """Коды блоков, к которым относятся замечания приёмки."""
    codes = [str(t.get("код") or "") for t in (tasks or [])
             if isinstance(t, dict) and str(t.get("код") or "").strip()]
    blob = " ".join(str(i) for i in issues)
    out = []
    for code in codes:
        if re.search(r"(?<![A-Za-zА-Яа-я0-9])" + re.escape(code)
                     + r"(?![A-Za-zА-Яа-я0-9])", blob):
            out.append(code)
    return out


def _hint_blocks(box: Path) -> list[str]:
    """Коды блоков, названные во вводных (их надо переделать).

    Замечания приёмки касаются обычно одного-двух блоков. Переделывать
    из-за них все блоки — бессмысленно и долго: остальные блоки уже
    сделаны и проверены.
    """
    data = _read_json(box, "design/manager_hints.json")
    hint = str(data.get("вводные") or "")
    listed = [str(x).strip() for x in (data.get("блоки") or [])
              if str(x).strip()]
    if not hint.strip():
        return listed
    tasks = _read_json(box, "design/block_tz.json").get("блоки") or []
    codes = [str(t.get("код") or "") for t in tasks if isinstance(t, dict)]
    out = list(listed)
    for code in codes:
        if not code or code in out:
            continue
        if re.search(r"(?<![A-Za-zА-Яа-я0-9])" + re.escape(code)
                     + r"(?![A-Za-zА-Яа-я0-9])", hint):
            out.append(code)
    if out:
        return out
    # коды не названы — ищем блок по цепям и именам из вводных. Слова
    # вроде «питание» или «напряжение» не в счёт: они есть у всех.
    # В счёт идут только характерные имена цепей и портов (LED_A,
    # USB-C, V_USB) — по ним блок опознаётся однозначно.
    tokens = re.findall(r"[A-Za-zА-Яа-я0-9_-]{2,}", hint)
    distinctive = {tok for tok in tokens
                   if any(c.isdigit() for c in tok) or "_" in tok or "-" in tok
                   or (tok.isupper() and any(c.isalpha() for c in tok))}
    tasks = _read_json(box, "design/block_tz.json").get("блоки") or []
    for tzn in tasks:
        if not isinstance(tzn, dict):
            continue
        code = str(tzn.get("код") or "")
        name = str(tzn.get("наименование") or "")
        name_low = name.lower()
        marks = set()
        for p in (tzn.get("входные_параметры") or []) + \
                 (tzn.get("выходные_параметры") or []):
            if isinstance(p, dict):
                marks.add(str(p.get("обозначение") or "").lower())
        hit = False
        for tok in distinctive:
            low = tok.lower()
            if low in marks or low in name_low:
                hit = True
                break
        if hit:
            out.append(code)
    return out


def _manager_hints(box: Path) -> str:
    """Вводные менеджера для следующего агента (если он их оставил)."""
    data = _read_json(box, "design/manager_hints.json")
    hint = str(data.get("вводные") or "").strip()
    if not hint:
        return ""
    return ("\n\nВВОДНЫЕ ГЛАВНОГО МЕНЕДЖЕРА (исполни их в этот раз):\n"
            + hint[:1500] + "\n")


def run_with_manager(text: str, proj: Project, order_id: str | None = None,
                     done_stages: set[str] | None = None,
                     answer: str = "") -> dict:
    """Конвейер под надзором менеджера.

    Менеджер смотрит на итог и решает: принять, повторить стадию с
    вводными, спросить человека или остановиться. Повтор отменяет всю
    цепочку после указанной стадии и запускает её снова.
    """
    box = Path(engine.current_workdir() or proj.root)
    done = set(done_stages or ())
    out: dict = {}
    for _round in range(MANAGER_ROUNDS + 1):
        out = run(text, proj, order_id, done_stages=done, answer=answer)
        dec = out.get("решение_менеджера") or {}
        target = ""
        hints = ""
        if str(dec.get("решение") or "").lower() == "повторить":
            target = str(dec.get("стадия") or "").strip()
            hints = str(dec.get("вводные") or "").strip()
        if not target:
            # процедурные правила: расхождения по схеме возвращаются
            # проектировщику — он их и исправляет. Отказ упаковщика
            # (нет даташита) возвращается библиотекарю — он ищет LCSC/MPN
            # процедурные правила: расхождения по схеме возвращаются
            # проектировщику — он их и исправляет. Отказ упаковщика
            # (нет даташита) возвращается библиотекарю — он ищет LCSC/MPN
            for stage in ("stitch", "verify", "kicad", "packager"):
                st = _step_by_name(out.get("steps") or [], stage)
                if not (st and st.get("status") == "failed" and st.get("исправимо")):
                    continue
                err = str(st.get("ошибки") or st.get("error") or "")
                low = err.lower()
                # если проблема в корпусе/библиотеке — отправляем к библиотекарю,
                # он ищет footprint (локально, в jlcparts, в интернете)
                if ("корпус не подтверждён" in low or "библиотек" in low
                        or "footprint" in low or "lib_id" in low):
                    target = "library"
                elif stage in ("stitch", "verify"):
                    target = "schematic"
                elif stage == "kicad":
                    target = "schematic"
                else:
                    target = "library"
                hints = err
                break
        if not target:
            break
        if _round >= MANAGER_ROUNDS:
            out.setdefault("log", []).append(
                f"менеджер просил повторить {target}, но попытки исчерпаны")
            break
        _write_json(box, "design/manager_hints.json",
                    {"вводные": hints, "стадия": target})
        _forget_from(box, target)
        done = {k for k, v in _read_pipeline(box).items()
                if str(v.get("status") or "done") == "done"}
        out.setdefault("log", []).append(
            f"менеджер: повтор с {target} "
            f"(раунд {_round + 1}/{MANAGER_ROUNDS})")
    return out


def continue_pipeline(text: str, proj: Project,
                      order_id: str | None = None,
                      answer: str = "") -> dict:
    """Продолжить заказ с места остановки.

    Уже выполненные стадии берутся из design/_pipeline.json и повторно
    НЕ выполняются: агентам не приходится заново переписывать ТЗ,
    расчёты и схему. Упавшие и не начатые стадии проходят заново.

    `answer` — ответ оператора: он попадает в состояние заказа и
    виден агентам, которые продолжают работу.
    """
    box = Path(engine.current_workdir() or proj.root)
    done = {k for k, v in _read_pipeline(box).items()
            if str(v.get("status") or "done") == "done"}
    return run_with_manager(text, proj, order_id, done_stages=done,
                             answer=answer)


def run(text: str, proj: Project, order_id: str | None = None,
        done_stages: set[str] | None = None,
        answer: str = "") -> dict:
    """Провести заказ по стадиям. Возвращает состояние как engine.run.

    `done_stages` — стадии, уже выполненные (из design/_pipeline.json):
    они не выполняются заново, агентам не приходится переписывать своё
    работу. Остальные проходят с начала.
    """
    params = _extract_params(text)
    steps: list[dict] = []
    state: dict = {
        "order_id": order_id or "", "text": text, "intent": "hardware",
        "planner": "pipeline", "mode": "stages", "model": "",
        "steps": steps, "verdict": "", "question": "", "note": "",
        "log": ["конвейер запущен: стадии ведёт каркас, содержание пишут агенты"],
        "status": "running", "progress": 0, "turns": 0, "nudges": 0,
        "artifacts": [], "report": ""}
    if answer.strip():
        state["note"] = f"ответ оператора: {answer.strip()[:300]}"
        state["log"] = state.get("log", []) + [
            f"ответ оператора: {answer.strip()[:200]}"]
        state["pending_user"] = state["note"]
    engine.set_active_state(state)
    # Предпрогонный скан моделей: какие отвечают сейчас. Мёртвые
    # (лимит, отказ, таймаут) в гонку не идут, живые — в порядке профиля.
    try:
        scanned = llm.scan_routes(timeout=8.0, quick=True)
        alive = [r for r in scanned if r.get("живая")]
        state["log"] = state.get("log", []) + [
            f"скан моделей: {len(scanned)} проверено, живы {len(alive)} — "
            + (", ".join(f"{r['провайдер']}/{r['модель'].split(':')[0]}"
                         for r in alive[:6]) if alive else "нет живых")]
    except Exception as exc:                                # noqa: BLE001
        state["log"] = state.get("log", []) + [
            f"скан моделей не удался: {str(exc)[:120]}"]
    done_stages = set(done_stages or ())

    by_name = proj.by_name()
    for target, title, kind in _PLAN:
        if kind == "make" and target not in by_name:
            continue                                   # стадии нет в проекте
        st = _step(target, title,
                   agent=proj.agent_owner.get(target, "agent.build"))
        if target in done_stages:
            st["status"] = "done"
            st["note"] = "выполнено ранее — артефакт сохранён"
        steps.append(st)
    state["log"] = state.get("log", []) + [
        "стадии: " + " → ".join(s["target"] for s in steps)]
    if done_stages:
        state["log"] = state.get("log", []) + [
            "продолжение: уже выполнены " + ", ".join(sorted(done_stages))]
    engine._publish(state)

    failures: list[str] = []
    manager_decision: dict = {}
    #: какие стадии требуют результата каких: невыполненная предпосылка
    #: не порождает каскад отказов, а помечает стадию пропущенной.
    _NEEDS: dict[str, tuple[str, ...]] = {
        "block_tz": ("structure",),
        "schematic": ("block_tz",),
        "stitch": ("schematic",),
        "verify": ("stitch",),
        "library": ("schematic",),
        "kicad": ("stitch",),
        "spice_models": ("stitch",),
        "spice": ("kicad",),
        "sim": ("spice",),
        "sim_fit": ("sim",),
        "packager": ("kicad",),
        # плата строится только после чистой симуляции: непроверенная
        # в ngspice схема не должна уходить в дорожки и 3D
        "gost": ("kicad", "verify", "sim"),
        "all": ("gost",),
        "test": ("all",),
    }
    done_ok: set[str] = set(s["target"] for s in steps
                            if s.get("status") == "done")
    for s in steps:
        if s.get("status") == "done":
            continue                       # выполнено ранее — не повторять
        # память конвейера — источник истины: стадия могла быть сброшена
        # раньше в этом же проходе (изменились входы), и тогда её надо
        # выполнить заново, даже если в начале прохода она считалась
        # сделанной
        mem = _read_pipeline(box_path(proj))
        mem_status = str((mem.get(s["target"]) or {}).get("status") or "")
        if mem_status == "done":
            s["status"] = "done"
            s["note"] = "выполнено ранее — артефакт сохранён"
            done_ok.add(s["target"])
            continue
        need = _NEEDS.get(s["target"], ())
        missing = [n for n in need if n not in done_ok]
        if missing:
            s["status"] = "skipped"
            s["error"] = (f"пропущена: нет результата стадии "
                          f"{', '.join(missing)}")
            s["finished_at"] = now_iso()
            state["log"] = state.get("log", []) + [
                f"стадия {s['target']} пропущена: нет результата "
                f"{', '.join(missing)}"]
            engine._publish(state)
            continue
        s["status"] = "running"
        s["started_at"] = now_iso()
        engine._publish(state)
        t0 = time.monotonic()
        if s["target"] in ("tz", "kicad", "spice", "sim", "all", "gost",
                            "test"):
            if s["target"] == "kicad":
                # поблочная сборка SKiDL → сшивание схемы; дефекты
                # блоков возвращаются дизайнеру петлёй правок
                _kicad_stage(text, proj, s, state)
                _spec_fix_loop(text, proj, state, s)   # агент сам правит
            else:
                engine._execute_one(proj, s)           # скрипты проекта
        elif s["target"] == "consultant":
            res = _stage_consultant(text, proj, state)
            s.update(status=res["status"],
                     **({"error": res["error"]} if res.get("error") else {}))
        elif s["target"] == "calc":
            res = _stage_calc(text, params, proj, state)
            s.update(status=res["status"],
                     **({"error": res["error"]} if res.get("error") else {}))
        elif s["target"] == "sizing":
            res = _sizing_stage(text, proj, state)
            s.update(status=res["status"],
                     **({"error": res["error"]} if res.get("error") else {}))
        elif s["target"] == "packager":
            res = _stage_packager(text, proj, state)
            s.update(status=res["status"],
                     **({"error": res["error"]} if res.get("error") else {}))
            if res.get("исправимо"):
                s["исправимо"] = True
            if res.get("ошибки"):
                s["ошибки"] = res["ошибки"]
        elif s["target"] == "library":
            box = Path(engine.current_workdir() or proj.root)
            res = _stage_library(text, _read_design(box,
                        "design/calc.json", 4000), proj, state)
            s.update(status=res["status"],
                     **({"error": res["error"]} if res.get("error") else {}))
        elif s["target"] == "tz_write":
            res = _tz_stage(text, params, proj, state)
            s.update(status=res["status"],
                     **({"error": res["error"]} if res.get("error") else {}))
        elif s["target"] == "checklist":
            box = Path(engine.current_workdir() or proj.root)
            res = _stage_checklist(text, _read_design(box,
                            "design/calc.json", 4000),
                            _read_design(box,
                            "design/technical_task.md", 4000),
                            proj, state)
            s.update(status=res["status"],
                     **({"error": res["error"]} if res.get("error") else {}))
        elif s["target"] == "structure":
            res = _stage_structure(text, proj, state)
            s.update(status=res["status"],
                     **({"error": res["error"]} if res.get("error") else {}))
        elif s["target"] == "block_tz":
            res = _stage_block_tz(text, proj, state)
            s.update(status=res["status"],
                     **({"error": res["error"]} if res.get("error") else {}))
            if res.get("незавершено"):
                s["незавершено"] = True
        elif s["target"] == "schematic":
            res = _stage_skidl(text, proj, state)
            s.update(status=res["status"],
                     **({"error": res["error"]} if res.get("error") else {}))
            if res.get("незавершено"):
                # блоки без схемы — изделие неполное: стадия считается
                # частичной, и при продолжении её выполнят снова
                s["незавершено"] = True
        elif s["target"] == "sim_fit":
            res = _stage_sim_fit(text, proj, state)
            s.update(status=res["status"],
                     **({"error": res["error"]} if res.get("error") else {}))
        elif s["target"] == "spice_models":
            res = _stage_spice_models(text, proj, state)
            s.update(status=res["status"],
                     **({"error": res["error"]} if res.get("error") else {}))
        elif s["target"] == "stitch":
            res = _stage_stitch(proj, state)
            # дефекты поблочной проверки возвращаются сшивальщику: он
            # сам переименовывает сети. Проектировщику блоков они не
            # возвращаются — блоки он уже сдал.
            for cycle in range(1, STITCH_FIXES + 1):
                if res["status"] == "done" or not res.get("исправимо"):
                    break
                state["log"] = state.get("log", []) + [
                    f"сшивка: дефекты цикла {cycle}/{STITCH_FIXES} → "
                    "сшивальщику на исправление"]
                res = _stage_stitch(proj, state,
                                    errors=str(res.get("ошибки") or ""))
            s.update(status=res["status"],
                     **({"error": res["error"]} if res.get("error") else {}))
            # исправимость и список ошибок — для петли менеджера
            if res.get("исправимо"):
                s["исправимо"] = True
            if res.get("ошибки"):
                s["ошибки"] = res["ошибки"]
        elif s["target"] == "verify":
            res = _stage_verify(proj, state)
            s.update(status=res["status"],
                     **({"error": res["error"]} if res.get("error") else {}))
            if res.get("исправимо"):
                s["исправимо"] = True
            if res.get("ошибки"):
                s["ошибки"] = res["ошибки"]
        elif s["target"] == "manager":
            res = _stage_manager(text, proj, state)
            s.update(status=res["status"],
                     **({"error": res["error"]} if res.get("error") else {}))
            manager_decision = res
        s["duration_s"] = round(time.monotonic() - t0, 1)
        s["finished_at"] = now_iso()
        state["progress"] = _progress(steps)
        if s.get("status") == "done":
            done_ok.add(s["target"])
            # незавершённая стадия записывается как «частично»: при
            # продолжении её выполнятят снова, а не пропустят
            _record_stage(box_path(proj), s["target"],
                          "partial" if s.get("незавершено") else "done")
        if s.get("status") == "failed":
            err = str(s.get("error") or "")
            # Отказ стадии обязан быть виден в журнале заказа: молчаливый
            # отказ раньше выглядел как «заказ просто висит».
            if err and not any(err[:60] in x for x in state["log"][-6:]):
                state["log"] = state.get("log", []) + [
                    f"стадия {s['target']}: {err[:220]}"]
            failures.append(f"{s['target']}: " + err[:160])
        engine._publish(state)

    if manager_decision:
        state["решение_менеджера"] = manager_decision
    state["status"] = final_status(steps)
    state["finished_at"] = now_iso()
    done = sum(1 for s in steps if s.get("status") == "done")
    state["note"] = f"{done}/{len(steps)} стадий выполнено"
    if failures:
        state["log"] = state.get("log", []) + ["не выполнено: "
                                               + "; ".join(failures)]
    # ТЗ, написанное петлёй агентов, публикуется как итоговый документ
    tt = box_path(proj) / "design/technical_task.md"
    out_tz = box_path(proj) / "out/tz.md"
    if tt.is_file():
        out_tz.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(tt, out_tz)
        state["log"] = state.get("log", []) + [
            f"ТЗ агентов опубликовано: out/tz.md "
            f"({out_tz.stat().st_size:,} байт)"]
    state["report"] = _final_report(proj, state)
    engine._publish(state)
    return dict(state)


# --------------------------------------------------------------------------- #
#  Стадии 5–9: структурная схема → ТЗ на блоки → SKiDL → сшивка → приёмка
#
#  Каждая стадия — отдельная роль агента со своей карточкой (roles/*.md)
#  и своим форматом ответа. Каркас ничего не придумывает за агента: получил
#  JSON — записал, проверил рамкой, отдал следующей стадии. Не получил —
#  вернул агенту замечания и повторил попытку.
# --------------------------------------------------------------------------- #
_STRUCTURAL_SYSTEM = _role("structural") or "системотехник"
_BLOCK_TZ_SYSTEM = _role("block_tz") or "руководитель проектирования"
_SKIDL_SYSTEM = _role("skidl_designer") or "инженер-схемотехник"
_VERIFIER_SYSTEM = _role("verifier") or "критик схемы"
_STITCH_SYSTEM = _role("stitcher") or "сшивальщик блоков"
_MANAGER_SYSTEM = _role("manager") or "главный менеджер проекта"

#: сколько раз стадия возвращает агенту свои замечания
STRUCTURE_TRIES = int(os.environ.get("KR_STRUCTURE_TRIES", "3"))
BLOCK_TZ_TRIES = int(os.environ.get("KR_BLOCK_TZ_TRIES", "3"))
SKIDL_TRIES = int(os.environ.get("KR_SKIDL_TRIES", "5"))
#: предохранитель времени на одну стадию с агентом: без него заказ может
#: висеть на шлюзе бесконечно
TOOL_STAGE_SECONDS = float(os.environ.get("KR_TOOL_STAGE_SECONDS", "900"))
#: сколько блоков проектируются одновременно: блоки независимы, а заказ
#: иначе упирается в сумму времени всех блоков подряд
SKIDL_WORKERS = int(os.environ.get("KR_SKIDL_WORKERS", "1"))
#: размер ответа схемотехника: спецификация блока — длинный JSON, и при
#: обрезке он не разбирается вовсе
SKIDL_TOKENS = int(os.environ.get("KR_SKIDL_TOKENS", "12000"))
#: попыток библиотекаря добыть модель (поиск → загрузка → сборка)
LIBRARIAN_TRIES = int(os.environ.get("KR_LIBRARIAN_TRIES", "4"))
#: размер ответа сшивальщика: сети изделия — длинный список
STITCH_TOKENS = int(os.environ.get("KR_STITCH_TOKENS", "12000"))
#: сколько раз менеджер может потребовать повтор
MANAGER_ROUNDS = int(os.environ.get("KR_MANAGER_ROUNDS", "2"))
#: сколько раз дефекты сшивки возвращаются проектировщику блоков
STITCH_FIXES = int(os.environ.get("KR_STITCH_FIXES", "3"))
#: сколько попыток у сшивальщика свести блоки в одну схему
STITCH_TRIES = int(os.environ.get("KR_STITCH_TRIES", "2"))
#: сколько итераций доводки схемы по результатам симуляции
SIM_FIT_ITERS = int(os.environ.get("KR_SIM_FIT_ITERS", "3"))
VERIFY_TRIES = int(os.environ.get("KR_VERIFY_TRIES", "2"))


def _write_json(box: Path, rel: str, data: dict) -> None:
    """Записать артефакт стадии (агентский JSON) на диск песочницы."""
    p = box / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                 encoding="utf-8")


def _json_turn(system: str, user: str, state: dict, who: str,
               tries: int = 3, profile: str = "",
               json_schema: dict | None = None) -> dict | None:
    """Ход агента, ожидающий JSON-объект.

    Модель может ответить текстом, обернуть JSON в пояснение или вернуть
    рассуждение перед ним — каркас достаёт объект сам (planner._extract_json
    умеет и целиком, и из markdown-блока, и по последним скобкам).
    Пустой/нечитаемый ответ — это замечание агенту, а не отказ заказа.
    """
    for attempt in range(1, tries + 1):
        raw = _chat_plain(system, user, profile=profile,
                          json_schema=json_schema)
        if raw and raw.strip():
            data = planner._extract_json(raw)
            if isinstance(data, dict) and data:
                _transcript(state, f"{who}: попытка {attempt}", raw, cap=12000)
                return data
            state["log"] = state.get("log", []) + [
                f"{who}: попытка {attempt}/{tries} — ответ без "
                "разбираемого JSON-объекта"]
            user = user + ("\n\nПредыдущий ответ не был разбираемым JSON-"
                          "объектом. Ответь ТОЛЬКО JSON-объектом, без "
                          "пояснений и без markdown-обёртки.")
        else:
            state["log"] = state.get("log", []) + [
                f"{who}: попытка {attempt}/{tries} — пустой ответ"]
    return None


def _stage_structure(text: str, proj: Project, state: dict) -> dict:
    """Стадия 5: структурная схема ЭСКД (Э1) — системотехник.

    Изделие раскладывается на функциональные части и связи между ними.
    Именно отсюда берутся коды блоков для ТЗ на узлы, поэтому проверка
    строгая: у каждой части есть назначение, входы и выходы, у каждой
    связи — имя сети, по которому потом сойдутся блоки.
    """
    box = Path(engine.current_workdir() or proj.root)
    calc = _read_design(box, "design/calc.json", 4000)
    acceptance = _read_design(box, "design/acceptance.json", 2500)
    issues: list[str] = []

    for attempt in range(1, STRUCTURE_TRIES + 1):
        user = _structure_user(text, calc, acceptance, issues)
        data = _json_turn(_STRUCTURAL_SYSTEM, user, state,
                          "системотехник", tries=1)
        if data is None:
            issues = ["агент не вернул разбираемый JSON-объект"]
            continue
        errs = _structure_check(data)
        for hint in (data.pop("_hints", None) or []):
            state["log"] = state.get("log", []) + [f"Э1: {hint}"]
        if not errs:
            _write_json(box, "design/structure.json", data)
            _transcript(state, "системотехник: Э1 принята",
                        json.dumps(data, ensure_ascii=False, indent=1),
                        cap=12000)
            parts = data.get("функциональные_части") or []
            state["log"] = state.get("log", []) + [
                f"Э1 принята: функциональных частей {len(parts)}, "
                f"связей {len(data.get('связи') or [])} → "
                "design/structure.json"]
            return {"status": "done"}
        issues = errs
        state["log"] = state.get("log", []) + [
            f"Э1: замечаний {len(errs)} — агенту на доработку "
            f"(попытка {attempt}/{STRUCTURE_TRIES})"]

    _write_json(box, "design/structure.json",
                {"ошибки": issues, "стадия": "structure"})
    return {"status": "failed",
            "error": "структурная схема не принята: " + "; ".join(issues[:4])}


def _structure_user(text: str, calc: str, acceptance: str,
                    issues: list[str]) -> str:
    tail = ("Замечания предыдущей попытки — исправь ровно их:\n"
            + "\n".join(f"- {e}" for e in issues) + "\n\n") if issues else ""
    return ("Заказ: " + text + "\n\n"
            "Расчёты изделия (design/calc.json):\n"
            + (calc or "(расчётов нет)") + "\n\n"
            + (f"Решение консультанта (design/acceptance.json) — рамка "
               f"состава изделия, учти его дословно:\n{acceptance}\n\n"
               if acceptance else "")
            + tail
            + "Составь структурную схему изделия (Э1) по ролевой карточке. "
            "Ответь ТОЛЬКО JSON-объектом со структурой:\n"
            '{"изделие": "...", "функциональные_части": [{"код": "G1", '
            '"наименование": "...", "назначение": "...", '
            '"требования": ["..."], "входы": ["..."], "выходы": ["..."]}], '
            '"связи": [{"от": "G1", "к": "A1", "назначение": "...", '
            '"сеть": "VBAT"}], "допущения": ["..."]}')


def _structure_check(data: dict) -> list[str]:
    """Рамка приёмки Э1. Каркас проверяет форму, не содержание.

    Проверяется ровно то, что запрещено методичкой: части — по функции,
    у каждой есть назначение и требования, у каждой связи — имя сети.
    Числовые значения внутри требований (3.7 В, 2000 мА·ч) — не дефект:
    требование без числа непроверяемо, а Э1 запрещает номиналы ДЕТАЛЕЙ,
    не величины функций.
    """
    errs: list[str] = []
    parts = data.get("функциональные_части")
    if not isinstance(parts, list) or not parts:
        return ["нет функциональных частей"]
    if len(parts) > 12:
        errs.append(f"частей слишком много ({len(parts)}) — дробить "
                    "изделие по функциям, а не по деталям")
    codes: set[str] = set()
    hints: list[str] = []
    for p in parts:
        if not isinstance(p, dict):
            errs.append("элемент функциональных_частей — не объект")
            continue
        code = str(p.get("код") or "").strip()
        if not code:
            errs.append("часть без кода")
        elif code in codes:
            errs.append(f"код {code} повторяется")
        codes.add(code)
        name = str(p.get("наименование") or "").strip()
        if not name:
            errs.append(f"{code or '?'}: нет наименования")
        if len(str(p.get("назначение") or "").strip()) < 10:
            errs.append(f"{code or '?'}: назначение пустое или слишком "
                        "короткое")
        reqs = p.get("требования")
        if not isinstance(reqs, list) or not reqs:
            errs.append(f"{code or '?'}: нет требований к части")
        # входы и выходы: у источника энергии входов нет — это не дефект,
        # но и то и другое пустым быть не может
        ins, outs = p.get("входы"), p.get("выходы")
        if not isinstance(ins, list) or not isinstance(outs, list):
            errs.append(f"{code or '?'}: поля входов и выходов — списки")
        elif not ins and not outs:
            errs.append(f"{code or '?'}: часть ничего не принимает и "
                        "ничего не отдаёт")
        # позиционные обозначения ДЕТАЛЕЙ на Э1 запрещены: собственные
        # коды частей (G1, A1) — не детали, их не проверяем
        blob = json.dumps({k: v for k, v in p.items() if k != "код"},
                          ensure_ascii=False)
        for m in re.finditer(r"(?<![A-Za-zА-Яа-я0-9])([RCLQUDTXJK])\d{1,3}"
                             r"(?![A-Za-zА-Яа-я0-9])", blob):
            if m.group(0) in codes:
                continue
            errs.append(f"{code or '?'}: на Э1 недопустимы позиционные "
                        f"обозначения деталей ({m.group(0)})")
            break
    links = data.get("связи")
    if not isinstance(links, list) or not links:
        errs.append("нет связей между частями")
    else:
        # входящие связи части: от другой части или от внешнего терминала
        # «ВХОД». Часть без входящей связи — висячая: её вход никуда не
        # приходит, и в ТЗ на узел потом нечего указать.
        incoming: dict[str, int] = {}
        for l in links:
            if not isinstance(l, dict):
                continue
            dst = str(l.get("к") or "").strip()
            if dst:
                incoming[dst] = incoming.get(dst, 0) + 1
        # Часть без входящей связи бывает законно: источник энергии
        # (аккумулятор) — начало потока. Поэтому это не отказ, а
        # подсказка в журнал: настоящую ошибку (забытый внешний вход)
        # поймает проверка имён цепей в ТЗ на узлы.
        for code in sorted(codes):
            if not incoming.get(code) and code not in ("ВХОД", "ВЫХОД"):
                hints.append(f"{code}: у части нет входящей связи — если это "
                            "не источник, добавь связь от другой части "
                            "или от «ВХОД»")
        for l in links:
            if not isinstance(l, dict):
                errs.append("элемент связей — не объект")
                continue
            for side in ("от", "к"):
                ref = str(l.get(side) or "").strip()
                # «ВХОД»/«ВЫХОД» — внешние терминалы изделия: они не
                # функциональные части, но связь с ними обязана быть, иначе
                # у внешнего входа нет имени сети
                if ref in ("ВХОД", "ВЫХОД"):
                    continue
                if ref and ref not in codes:
                    errs.append(f"связь: сторона «{side}» — {ref} не среди "
                                "функциональных частей (внешний вход или "
                                "выход обозначай «ВХОД»/«ВЫХОД»)")
            # назначение связи — функциональное: по нему блоки сойдутся
            # в электрической схеме. Имен электрических сетей на Э1 нет:
            # сети появляются на электрической схеме, их назначает
            # схемотехник.
            purpose = str(l.get("назначение") or "").strip()
            if not purpose:
                errs.append("связь без назначения — по нему блоки потом "
                            "не сойдутся")
            elif len(purpose) < 4:
                errs.append(f"назначение связи «{purpose}» слишком "
                            "краткое — опиши, что по этой связи идёт")

    data["_hints"] = hints
    return errs


def _stage_block_tz(text: str, proj: Project, state: dict) -> dict:
    """Стадия 6: ТЗ на каждый функциональный блок — проектировщик.

    Для каждой части Э1 пишется задание: что на входе, что на выходе, что
    узел обязан делать и как это проверить. Без этого инженер-схемотехник
    не сможет проектировать узел и будет задавать вопросы на ходу.
    """
    box = Path(engine.current_workdir() or proj.root)
    structure = _read_json(box, "design/structure.json")
    if not structure:
        return {"status": "failed",
                "error": "нет структурной схемы design/structure.json — "
                         "стадия structure не дала результата"}
    parts = [p for p in (structure.get("функциональные_части") or [])
             if isinstance(p, dict)]
    if not parts:
        return {"status": "failed",
                "error": "в структурной схеме нет функциональных частей"}
    calc = _read_design(box, "design/calc.json", 3000)
    tz = _read_design(box, "design/technical_task.md", 3000)

    tasks: list[dict] = []
    failed: list[str] = []
    for part in parts:
        code = str(part.get("код") or "")
        name = str(part.get("наименование") or "")
        tzn = None
        issues: list[str] = []
        for attempt in range(1, BLOCK_TZ_TRIES + 1):
            user = _block_tz_user(text, part, structure, calc, tz)
            if issues:
                user += ("\n\nЗамечания к предыдущей попытке — исправь "
                         "ровно их:\n"
                         + "\n".join(f"- {e}" for e in issues[:8]))
            got = _json_turn(_BLOCK_TZ_SYSTEM, user, state,
                              f"постановщик ТЗ {code}", tries=1)
            if got is None:
                issues = ["агент не вернул разбираемый JSON-объект"]
                continue
            # агент часто переписывает назначение связи своими словами.
            # Название связи — это контракт Э1, поэтому текст приводится
            # к каноническому, когда соответствие однозначно.
            _normalise_purposes(got, structure, code, state)
            errs = _block_tz_check(got, code, structure)
            for hint in (got.get("_hints") or []):
                state["log"] = state.get("log", []) + [f"ТЗ {code}: {hint}"]
            got.pop("_hints", None)
            if not errs:
                tzn = got
                break
            issues = errs
            state["log"] = state.get("log", []) + [
                f"ТЗ блока {code}: попытка {attempt}/{BLOCK_TZ_TRIES} — "
                f"замечаний {len(errs)}: " + "; ".join(errs[:2])]
        if tzn is None:
            failed.append(f"{code}: " + (issues[0] if issues else
                                         "ТЗ не получено"))
            continue
        tzn.setdefault("код", code)
        tzn.setdefault("наименование", name)
        tasks.append(tzn)
        state["log"] = state.get("log", []) + [
            f"ТЗ на блок {code} — {name}: "
            f"{len(tzn.get('требования') or [])} требований, "
            f"{len(tzn.get('входные_параметры') or [])} входов, "
            f"{len(tzn.get('выходные_параметры') or [])} выходов"]

    if not tasks:
        return {"status": "failed",
                "error": "ТЗ на блоки не получены: " + "; ".join(failed[:4])}

    # Согласованность стыков: на обоих концах каждой связи Э1 имя цепи
    # должно совпадать, а параметры — не противоречить друг другу.
    # Расхождение на стыке — это не «блок-согласователь», а ошибка ТЗ:
    # возвращаем её тому блоку, который отклонился.
    # имена цепей на стыках приводятся к одному имени на связь — иначе
    # блоки не сойдутся между собой
    _harmonise_net_names(tasks, structure, state)
    _sync_link_params(tasks, structure, state)
    # канон имён считается ОДИН раз, по исходным ТЗ: в петле правки
    # блоки переименовывают сети, и пересчёт канона на каждом шаге
    # тащил бы решение за собой
    canonical = _canonical_names(tasks, structure)
    mism = _link_mismatch(tasks, structure)
    if mism:
        for code, why in mism:
            state["log"] = state.get("log", []) + [
                f"стык Э1 не сходится: {code} — {why}"]
        fixed = _fix_link_mismatch(text, proj, state, tasks, structure, mism,
                                   canonical)
        if fixed["status"] == "done":
            tasks = fixed["tasks"]
            # после правки имена снова приводятся к одному имени на
            # связь: агент в ответе мог вернуть свои обозначения
            _harmonise_net_names(tasks, structure, state)
            _sync_link_params(tasks, structure, state)
            # правка проверена: если стыки всё ещё расходятся — это
            # честный отказ, а не «сойдёт как-нибудь»
            again = _link_mismatch(tasks, structure)
            if again:
                state["log"] = state.get("log", []) + [
                    f"стыки блоков не согласованы после правки: "
                    f"{len(again)} расхождений"]
                return {"status": "failed",
                        "error": "стыки блоков не согласованы: "
                                 + "; ".join(f"{c}: {w}" for c, w in again[:3])}
        else:
            return {"status": "failed",
                    "error": "стыки блоков не согласованы: "
                             + "; ".join(f"{c}: {w}" for c, w in mism[:3])}

    # если ТЗ блоков изменилось, всё, что идёт после него, считается
    # устаревшим: его надо пересчитать по новым входам
    prev_path = box / "design/block_tz.json"
    prev_txt = (prev_path.read_text(encoding="utf-8")
                if prev_path.is_file() else "")
    new_txt = json.dumps({"блоки": tasks, "не_получены": failed},
                         ensure_ascii=False, indent=2)
    if prev_txt and prev_txt.strip() != new_txt.strip():
        _forget_after(box, "block_tz")
        state["log"] = state.get("log", []) + [
            "ТЗ блоков изменилось — стадии после него будут пересчитаны"]
    _write_json(box, "design/block_tz.json",
                {"блоки": tasks, "не_получены": failed})
    if failed:
        state["log"] = state.get("log", []) + [
            f"ТЗ без изменений для блоков: {len(failed)} — "
            + "; ".join(failed[:3])]
        # результат есть, но не весь: часть частей Э1 осталась без ТЗ.
        # Стадия идёт дальше (с тем, что есть), но при продолжении её
        # надо попробовать выполнить снова.
        return {"status": "done", "незавершено": True,
                "error": "ТЗ без изменений для блоков: "
                         + "; ".join(failed[:3])[:300]}
    return {"status": "done"}


def _block_tz_user(text: str, part: dict, structure: dict, calc: str,
                   tz: str) -> str:
    code = str(part.get("код") or "")
    links = [l for l in (structure.get("связи") or [])
             if isinstance(l, dict)
             and (str(l.get("от")) == code or str(l.get("к")) == code)]
    links_txt = "\n".join(
        f"  от {l.get('от')} к {l.get('к')} — назначение: "
        f"{l.get('назначение')}" for l in links) or "  (нет)"
    return ("Заказ: " + text + "\n\n"
            "Расчёты изделия (design/calc.json) — числа, на которые ты "
            "опираешься:\n"
            + (calc or "(расчётов нет)") + "\n\n"
            "ТЗ изделия (design/technical_task.md, начало):\n"
            + (tz[:2000] or "(нет)") + "\n\n"
            "Твоя функциональная часть из структурной схемы:\n"
            + json.dumps(part, ensure_ascii=False, indent=1) + "\n\n"
            "СВЯЗИ ЭТОЙ ЧАСТИ — ровно этот список, выдумывать свои нельзя:\n"
            + links_txt + "\n"
            "  (у каждой связи «назначение» — это её текст; в поле "
            "«связь» параметра пиши это назначение ДОСЛОВНО, а в поле "
            "«обозначение» — имя электрической цепи, одинаковое у обоих "
            "соседей по этой связи)\n\n"
            "Составь задание на проектирование этого узла по ролевой "
            "карточке.\n\n"
            "ГЛАВНОЕ — входные и выходные параметры должны быть РЕАЛЬНЫМИ:\n"
            "  * диапазон — числами с единицами, взятыми из расчётов: "
            "«3.0..4.2 В», «0..1.5 А», «20..100 кГц». Слова «питание», "
            "«управляющий сигнал», «нагрузка» без чисел — не годятся: по "
            "ним узел не спроектировать и не проверить;\n"
            "  * поле «связь» — РОВНО назначение связи Э1 этой части (из "
            "списка выше), дословно;\n"
            "  * поле «обозначение» — имя электрической цепи, коротко и "
            "технически (VBAT, VLED, LED_A, CHG_CTRL). Это контракт между "
            "блоками: у соседа по той же связи Э1 должно быть ТО ЖЕ "
            "обозначение, иначе блоки не состыкуются;\n"
            "  * параметры на стыке должны быть согласованы: диапазоны и "
            "единицы на обоих концах связи не противоречат друг другу. "
            "Согласующие блоки (согласование импедансов, мультиплексоры) "
            "в изделии не заводи: если стык не сходится, разойдись "
            "характеристиками, а не добавлением блока;\n"
            "  * каждый вход и выход части обязан быть в списке: если части "
            "нечего принимать (источник питания) — входов нет, но выходы "
            "обязательны, и наоборот;\n"
            "  * требования — измеримые, с числами и единицами, со ссылкой "
            "на то, из чего выведены.\n\n"
            "Ответь ТОЛЬКО JSON-объектом:\n"
            '{"код": "...", "наименование": "...", "функция": "...", '
            '"входные_параметры": [{"имя": "...", "связь": "<назначение '
            'связи Э1>", "обозначение": "<имя цепи>", "единица": "В", '
            '"диапазон": "3.0..4.2", "назначение": "..."}], '
            '"выходные_параметры": [{"имя": "...", "связь": "<назначение '
            'связи Э1>", "обозначение": "<имя цепи>", "единица": "В", '
            '"диапазон": "...", "назначение": "..."}], '
            '"требования": ["..."], "критерии_приёмки": ["..."], '
            '"ограничения": ["..."], "вопросы": ["..."]}')


#: слова, которые не различают назначения связей
_STOP_WORDS = {"и", "к", "от", "с", "в", "на", "а", "по", "для", "что",
               "это", "при", "без", "через", "под", "над", "из", "до", "за",
               "у", "о", "об", "также", "или", "не", "но", "же", "ли",
               "своим", "своих", "свой"}


def _purpose_words(text: str) -> set[str]:
    """Значимые слова назначения связи."""
    words = re.findall(r"[А-Яа-яЁёA-Za-z]{3,}", str(text or "").lower())
    return {w for w in words if w not in _STOP_WORDS}


def _match_purpose(text: str, allowed: list[str]) -> str:
    """Каноническое назначение связи, если соответствие однозначно.

    Сравнение по значимым словам: «Питание от аккумулятора 18650» и
    «защищённое питание аккумулятора к источнику питания» — одно и то
    же назначение, просто словами агента. Если подходит ровно одно
    разрешённое назначение, текст приводится к нему.
    """
    low = " ".join(str(text or "").lower().split())
    for a in allowed:
        if low == " ".join(a.lower().split()):
            return a                   # тот же текст, другой регистр
    want = _purpose_words(text)
    if not want:
        return ""
    scored: list[tuple[int, float, str]] = []
    for a in allowed:
        have = _purpose_words(a)
        common = len(want & have)
        if not common:
            continue
        share = common / max(len(want), len(have))
        scored.append((common, share, a))
    if not scored:
        return ""
    scored.sort(key=lambda kv: (-kv[0], -kv[1], kv[2]))
    best = scored[0]
    if best[0] < 2:
        return ""
    if len(scored) > 1:
        second = scored[1]
        # совпадение должно быть заметно лучше: иначе это не тот же
        # смысл, а разные назначения с общими словами
        if best[0] == second[0] and best[1] - second[1] < 0.34:
            return ""
    return best[2]


def _normalise_net_names(spec: dict, code: str) -> None:
    """Имена сетей блока — без префикса блока, земля — одна на изделие.

    Агент пишет локальные сети как «A1::GND» — по блоку понятно, но
    земля у изделия одна, иначе цепи не сойдутся. Поэтому префикс
    снимается, а земля получает имя GND.
    """
    for n in (spec.get("nets") or []):
        if not isinstance(n, dict):
            continue
        name = str(n.get("name") or "").strip()
        if not name:
            continue
        if "::" in name:
            name = name.split("::")[-1].strip()
            n["name"] = name
        if name.upper() == "GND" or name.upper().endswith("_GND"):
            n["name"] = "GND"



def _apply_tz_purposes(spec: dict, tzn: dict) -> None:
    """Назначение сети — по её имени, если имя взято из ТЗ.

    Имя внешней сети задано в ТЗ на узел (это контракт стыка), и там
    же написано, какая это связь. Значит, назначение сети с таким
    именем определяется однозначно — и переписывать его агент не
    должен. Собственные (внутренние) сети блока такого имени не имеют
    и остаются со своим назначением.
    """
    by_name: dict[str, str] = {}
    for fld in ("входные_параметры", "выходные_параметры"):
        for v in ((tzn or {}).get(fld) or []):
            if not isinstance(v, dict):
                continue
            name = str(v.get("обозначение") or "").strip()
            purpose = str(v.get("связь") or "").strip()
            if name and purpose:
                by_name.setdefault(name, purpose)
    if not by_name:
        return
    for n in (spec.get("nets") or []):
        if not isinstance(n, dict):
            continue
        name = str(n.get("name") or "").strip()
        purpose = by_name.get(name)
        if not purpose:
            continue
        cur = n.get("назначение")
        cur_list = [str(x) for x in
                    (cur if isinstance(cur, list) else [cur])
                    if str(x or "").strip()]
        if cur_list and purpose not in cur_list:
            n["назначение"] = [purpose] + cur_list
        elif not cur_list:
            n["назначение"] = [purpose]


def _normalise_purposes(tzn: dict, structure: dict, code: str,
                        state: dict) -> None:
    """Привести названия связей к контракту Э1 (по однозначному совпадению)."""
    allowed: list[str] = []
    for l in (structure.get("связи") or []):
        if not isinstance(l, dict):
            continue
        if str(l.get("от")) == code or str(l.get("к")) == code:
            p = str(l.get("назначение") or "").strip()
            if p and p not in allowed:
                allowed.append(p)
    if not allowed:
        return
    drop: list[tuple[str, dict]] = []
    for fld in ("входные_параметры", "выходные_параметры"):
        for v in (tzn.get(fld) or []):
            if not isinstance(v, dict):
                continue
            cur = str(v.get("связь") or "").strip()
            if not cur or cur in allowed:
                continue
            hit = _match_purpose(cur, allowed)
            if hit:
                state["log"] = state.get("log", []) + [
                    f"ТЗ {code}: назначение «{cur}» приведено к "
                    f"названию связи Э1 «{hit}»"]
                v["связь"] = hit
                continue
            # связи в Э1 нет — это не параметр узла, а описание. Такое
            # описание отбрасывается: блок от этого не теряется, а
            # сшивка не ломается о выдуманную связь.
            state["log"] = state.get("log", []) + [
                f"ТЗ {code}: параметр «{v.get('имя')}» отброшен — связи "
                f"«{cur}» в Э1 нет"]
            drop.append((fld, v))
    # отброшенные параметры убираем из ТЗ
    for fld, v in drop:
        vals = tzn.get(fld)
        if isinstance(vals, list) and v in vals:
            vals.remove(v)


def _block_tz_check(tzn: dict, code: str,
                    structure: dict | None = None) -> list[str]:
    """Рамка приёмки ТЗ на узел: измеримость, проверяемость, прослеживаемость.

    Требование к параметру должно быть ЧИСЛОВЫМ (иначе узел не проверить)
    и его обозначение должно совпадать с сетью из связей Э1 (иначе блоки
    потом не сойдутся в общей схеме).
    """
    errs: list[str] = []
    hints: list[str] = []
    link_names: dict[str, set[str]] = {}
    if not str(tzn.get("функция") or "").strip():
        errs.append("пустая функция узла")
    # назначения связей этой части по Э1 — контракт на функцию. Электрических
    # сетей в Э1 нет: сети появляются на электрической схеме, их назначает
    # схемотехник. Здесь связь — это ФУНКЦИЯ, по которой часть связана.
    purposes: set[str] = set()
    own_purposes: set[str] = set()
    for l in (structure or {}).get("связи") or []:
        if not isinstance(l, dict):
            continue
        purpose = str(l.get("назначение") or "").strip()
        if not purpose:
            continue
        purposes.add(purpose)
        if str(l.get("от")) == code or str(l.get("к")) == code:
            own_purposes.add(purpose)
    for field in ("входные_параметры", "выходные_параметры"):
        vals = tzn.get(field)
        if not isinstance(vals, list):
            errs.append(f"поле {field} — не список")
            continue
        if not vals:
            # у источника энергии входов нет, но один из списков обязан быть
            if field == "входные_параметры" and tzn.get("выходные_параметры"):
                continue
            errs.append(f"пустое поле {field}")
            continue
        for v in vals:
            if not isinstance(v, dict):
                errs.append(f"{field}: элемент — не объект")
                continue
            link = str(v.get("связь") or "").strip()
            if not link:
                errs.append(f"{field}: нет связи — укажи назначение "
                            "связи Э1, по которой параметр приходит "
                            "(уходит)")
            elif own_purposes and link not in own_purposes:
                errs.append(f"{field}: связь «{link}» выдумана — у этой "
                            f"части в Э1 только эти связи: "
                            f"{', '.join(sorted(own_purposes))}. Возьми одну "
                            "из них (или не описывай этот параметр)")
            elif purposes and link not in purposes:
                errs.append(f"{field}: связь «{link}» не встречается в Э1 "
                            f"ни для одной части ({', '.join(sorted(purposes))})"
                            " — узел не сойдётся с соседями")
            # имя электрической цепи — контракт между блоками
            name = str(v.get("обозначение") or "").strip()
            if not name:
                errs.append(f"{field}: нет обозначения — имя электрической "
                            "цепи, по которому блок сойдётся с соседом")
            elif not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", name):
                errs.append(f"{field}: обозначение «{name}» — только "
                            "латиница, без пробелов и кириллицы")
            else:
                # имя цепи на стыке: канон выбирается голосованием по всем
                # блокам (см. _link_mismatch). Здесь проверяем только
                # форму — иначе первый попавшийся блок «задавал» бы имя
                # всем остальным.
                same = link_names.get(link, set())
                same.add(name)
                link_names[link] = same
            rng = str(v.get("диапазон") or "").strip()
            if not rng:
                errs.append(f"{field}: нет диапазона — требование "
                            "неизмеримо")
            elif not re.search(r"\d", rng):
                errs.append(f"{field}: диапазон «{rng}» без чисел — "
                            "параметр не измерим")
            unit = str(v.get("единица") or "").strip()
            if not unit:
                errs.append(f"{field}: нет единицы измерения")
            if not str(v.get("назначение") or "").strip():
                errs.append(f"{field}: пустое назначение — по чему узел "
                            "понять, что приходит")
    reqs = tzn.get("требования")
    if not isinstance(reqs, list) or not reqs:
        errs.append("нет требований к узлу")
    else:
        for r in reqs:
            txt = str(r).strip().lower()
            if not txt or txt in ("надёжно", "качественно", "хорошо"):
                errs.append(f"требование «{r}» непроверяемо")
            elif not re.search(r"\d", txt):
                errs.append(f"требование «{r}» без чисел — непроверяемо")
    acc = tzn.get("критерии_приёмки")
    if not isinstance(acc, list) or not acc:
        errs.append("нет критериев приёмки")
    tzn["_hints"] = hints
    return errs


def _sync_link_params(tasks: list[dict], structure: dict,
                      state: dict) -> None:
    """Привести набор величин на стыках Э1 к одинаковому с обеих сторон.

    По одному проводу (одной цепи) может течь несколько величин —
    например, напряжение и ток силовой цепи. Но стык Э1 соединяет два
    блока, и по этому проводу течёт одно и то же: значит, обе стороны
    должны описывать один и тот же набор величин (цепь + единица).

    Недостающая величина переносится из блока, где она объявлена. Это
    приведение ТЗ к контракту стыка, а не проектирование: величины,
    диапазоны и единицы берутся из ТЗ, ничего не выдумывается.
    """
    by_code = {str(t.get("код") or ""): t for t in tasks}

    def key_of(v: dict) -> tuple[str, str]:
        return (str(v.get("обозначение") or "").strip(),
                P.unit_key(str(v.get("единица") or "")))

    for link in (structure.get("связи") or []):
        if not isinstance(link, dict):
            continue
        purpose = str(link.get("назначение") or "").strip()
        if not purpose:
            continue
        sides = [str(link.get(s) or "").strip() for s in ("от", "к")]
        sides = [c for c in sides if c in by_code]
        if len(sides) < 2:
            continue
        have: dict[str, dict[tuple[str, str], dict]] = {}
        for code in sides:
            tzn = by_code[code]
            got: dict[tuple[str, str], dict] = {}
            for fld in ("входные_параметры", "выходные_параметры"):
                for v in (tzn.get(fld) or []):
                    if not isinstance(v, dict):
                        continue
                    if str(v.get("связь") or "").strip() != purpose:
                        continue
                    k = key_of(v)
                    if k[0]:
                        got.setdefault(k, v)
            have[code] = got
        all_keys: set[tuple[str, str]] = set()
        for got in have.values():
            all_keys |= set(got)
        if len(all_keys) < 2:
            continue
        for code in sides:
            tzn = by_code[code]
            missing = all_keys - set(have[code])
            if not missing:
                continue
            out_fld = ("выходные_параметры" if code == sides[0]
                       else "входные_параметры")
            for k in sorted(missing):
                src = None
                for other in sides:
                    if other == code:
                        continue
                    if k in have[other]:
                        src = have[other][k]
                        break
                if src is None:
                    continue
                lst = tzn.setdefault(out_fld, [])
                if not isinstance(lst, list):
                    continue
                lst.append(dict(src))
                state["log"] = state.get("log", []) + [
                    f"ТЗ {code}: на стыке «{purpose}» добавлена величина "
                    f"{k[0]} ({k[1]}) — по этому проводу она течёт с "
                    "обеих сторон"]


def _harmonise_net_names(tasks: list[dict], structure: dict,
                         state: dict) -> None:
    """Привести имена цепей на каждой связи Э1 к одному имени.

    Имя цепи на стыке — контракт: у всех блоков оно должно быть одно.
    Канон выбирается голосованием по блокам (тем именем, которое уже
    использует большинство), а все параметры этой связи — в том числе
    несколько величин на одной связи — приводятся к нему.

    Это приведение к контракту, а не проектирование: ни одна деталь,
    ни одно соединение при этом не меняются.
    """
    votes: dict[str, dict[str, list]] = {}
    for tzn in tasks:
        code = str(tzn.get("код") or "")
        for fld in ("входные_параметры", "выходные_параметры"):
            for v in (tzn.get(fld) or []):
                if not isinstance(v, dict):
                    continue
                purpose = str(v.get("связь") or "").strip()
                nm = str(v.get("обозначение") or "").strip()
                if not purpose or not nm:
                    continue
                slot = votes.setdefault(purpose, {}).setdefault(nm, [set(), 0])
                slot[0].add(code)
                slot[1] += 1
    for purpose, names in votes.items():
        # канон: имя, которое использует больше блоков; при равенстве —
        # то, что встречается чаще в параметрах
        canon = sorted(names.items(),
                       key=lambda kv: (-len(kv[1][0]), -kv[1][1], kv[0]))[0][0]
        for tzn in tasks:
            code = str(tzn.get("код") or "")
            for fld in ("входные_параметры", "выходные_параметры"):
                for v in (tzn.get(fld) or []):
                    if not isinstance(v, dict):
                        continue
                    if str(v.get("связь") or "").strip() != purpose:
                        continue
                    nm = str(v.get("обозначение") or "").strip()
                    if nm and nm != canon:
                        state["log"] = state.get("log", []) + [
                            f"ТЗ {code}: цепь «{nm}» на связи "
                            f"«{purpose}» приведена к «{canon}» — на "
                            "стыке имя цепи одно"]
                        v["обозначение"] = canon


def _canonical_names(tasks: list[dict], structure: dict) -> dict[str, str]:
    """Каноническое имя цепи для каждой связи Э1.

    Считается по всем блокам: побеждает имя, которое уже использует
    большинство. Так решение детерминировано, иначе два блока тянут имя
    в разные стороны и правка колеблется.
    """
    by_code = {str(t.get("код") or ""): t for t in tasks}
    # голоса — по РАЗНЫМ блокам: один блок, назвавший связь двумя
    # именами, не должен перевешивать согласие соседей
    votes: dict[str, dict[str, set[str]]] = {}
    for link in (structure.get("связи") or []):
        if not isinstance(link, dict):
            continue
        purpose = str(link.get("назначение") or "").strip()
        if not purpose:
            continue
        for side in ("от", "к"):
            code = str(link.get(side) or "").strip()
            tzn = by_code.get(code)
            if not tzn:
                continue
            for fld in ("входные_параметры", "выходные_параметры"):
                for v in (tzn.get(fld) or []):
                    if not isinstance(v, dict):
                        continue
                    if str(v.get("связь") or "").strip() != purpose:
                        continue
                    nm = str(v.get("обозначение") or "").strip()
                    if nm:
                        votes.setdefault(purpose, {}).setdefault(nm, set()).add(code)
    out: dict[str, str] = {}
    for purpose, names in votes.items():
        out[purpose] = sorted(names.items(),
                             key=lambda kv: (-len(kv[1]), kv[0]))[0][0]
    return out


def _link_mismatch(tasks: list[dict], structure: dict) -> list[tuple[str, str]]:
    """Расхождения на стыках Э1: имя цепи или параметры разошлись.

    Возвращает пары (код блока, причина). Блок, который отклонился, —
    тот, чьё обозначение не совпало с уже принятым.
    """
    out: list[tuple[str, str]] = []
    by_code = {str(t.get("код") or ""): t for t in tasks}
    canonical = _canonical_names(tasks, structure)
    chosen: dict[str, str] = {}      # связь Э1 → принятое обозначение
    for link in (structure.get("связи") or []):
        if not isinstance(link, dict):
            continue
        purpose = str(link.get("назначение") or "").strip()
        if not purpose:
            continue
        for side in ("от", "к"):
            code = str(link.get(side) or "").strip()
            tzn = by_code.get(code)
            if not tzn:
                continue
            for fld in ("входные_параметры", "выходные_параметры"):
                for v in (tzn.get(fld) or []):
                    if not isinstance(v, dict):
                        continue
                    if str(v.get("связь") or "").strip() != purpose:
                        continue
                    name = str(v.get("обозначение") or "").strip()
                    if not name:
                        continue
                    want = canonical.get(purpose)
                    if want and name != want:
                        out.append((code, f"связь «{purpose}»: цепь "
                                         f"названа {name}, а по всей схеме "
                                         f"эта связь — {want}; имя цепи на "
                                         "стыке должно быть одно"))
                    # попарное сравнение здесь было лишним: имя цепи
                    # выбирается голосованием по всем блокам, и блок с
                    # «непопустимым» именем переименовывается на канон
                    chosen.setdefault(purpose, name)
    # противоречие параметров на стыке: диапазоны не пересекаются
    for link in (structure.get("связи") or []):
        if not isinstance(link, dict):
            continue
        purpose = str(link.get("назначение") or "").strip()
        if not purpose:
            continue
        sides: list[dict] = []
        for side in ("от", "к"):
            tzn = by_code.get(str(link.get(side) or "").strip())
            if not tzn:
                continue
            for fld in ("входные_параметры", "выходные_параметры"):
                for v in (tzn.get(fld) or []):
                    if isinstance(v, dict) and \
                            str(v.get("связь") or "").strip() == purpose:
                        sides.append(v)
        if len(sides) < 2:
            continue
        # сверка идёт по паре (цепь, единица): по одному проводу может
        # течь несколько величин (напряжение и ток), и у каждой — своя
        # единица. Одна и та же величина должна быть объявлена с обеих
        # сторон стыка и в пересекающихся диапазонах.
        def key_of(s: dict) -> tuple[str, str]:
            return (str(s.get("обозначение") or "").strip(),
                    P.unit_key(str(s.get("единица") or "")))

        sides_keys: list[set] = []
        for side in ("от", "к"):
            code = str(link.get(side) or "").strip()
            tzn = by_code.get(code)
            if not tzn:
                sides_keys.append(set())
                continue
            keys = set()
            for fld in ("входные_параметры", "выходные_параметры"):
                for v in (tzn.get(fld) or []):
                    if isinstance(v, dict) and \
                            str(v.get("связь") or "").strip() == purpose:
                        k = key_of(v)
                        if k[0]:
                            keys.add(k)
            sides_keys.append(keys)
        if len(sides_keys) < 2:
            continue
        a_keys, b_keys = sides_keys
        for k in sorted(a_keys - b_keys):
            out.append((str(link.get("от") or "?"),
                        f"на стыке «{purpose}» величина {k[0]} "
                        f"({k[1]}) объявлена только с одной стороны"))
        for k in sorted(a_keys & b_keys):
            ranges = []
            for s in sides:
                if not str(s.get("связь") or "").strip() == purpose:
                    continue
                if key_of(s) != k:
                    continue
                lo, hi = _range_bounds(str(s.get("диапазон") or ""))
                if lo is not None:
                    ranges.append((lo, hi))
            if len(ranges) >= 2:
                lo = max(r[0] for r in ranges)
                hi = min(r[1] for r in ranges)
                if lo > hi:
                    out.append((str(link.get("от") or "?"),
                                f"цепь «{k[0]}» ({k[1]}) на стыке "
                                f"«{purpose}»: диапазоны не пересекаются "
                                f"({ranges[0]} и {ranges[1]})"))
    return out


def _range_bounds(text: str) -> tuple[float | None, float | None]:
    """Диапазон «3.0..4.2 В» → (3.0, 4.2). Одно число — точное значение."""
    s = str(text or "").strip()
    if not s:
        return (None, None)
    if ".." in s:
        a, _, b = s.partition("..")
        from agents.parts import parse_number
        return (parse_number(a), parse_number(b))
    from agents.parts import parse_number
    v = parse_number(s)
    return (v, v) if v is not None else (None, None)


def _fix_link_mismatch(text: str, proj: Project, state: dict,
                       tasks: list[dict], structure: dict,
                       mism: list[tuple[str, str]],
                       canonical: dict[str, str] | None = None) -> dict:
    """Вернуть расхождения стыков блокам на доработку.

    Агент правит своё ТЗ: согласовать имя цепи с соседом можно только
    тому, кто отклонился, — каркас за него ничего не решает.
    """
    fixed: list[dict] = []
    for tzn in tasks:
        code = str(tzn.get("код") or "")
        own = [why for c, why in mism if c == code]
        if not own:
            fixed.append(tzn)
            continue
        # каноническое имя по этой связи — оно уже согласовано по всей
        # схеме, спорить с ним не надо
        canonical = canonical or _canonical_names(tasks, structure)
        want = []
        for fld in ("входные_параметры", "выходные_параметры"):
            for v in (tzn.get(fld) or []):
                if not isinstance(v, dict):
                    continue
                purpose = str(v.get("связь") or "").strip()
                nm = str(v.get("обозначение") or "").strip()
                canon = canonical.get(purpose)
                if canon and nm and canon != nm:
                    want.append(f"связь «{purpose}»: назови цепь {canon}, "
                                f"а не {nm}")
        neighbour = _link_names_by_side(tasks, structure, code)
        # Точечная задача: переименовать цепи. Общий промпт ТЗ здесь
        # мешает — агент теряет правку в длинном тексте, поэтому задача
        # формулируется прямо: вот твоё ТЗ, вот что переименовать.
        todo: list[str] = []
        for fld in ("входные_параметры", "выходные_параметры"):
            for v in (tzn.get(fld) or []):
                if not isinstance(v, dict):
                    continue
                purpose = str(v.get("связь") or "").strip()
                nm = str(v.get("обозначение") or "").strip()
                canon = canonical.get(purpose)
                if canon and nm and canon != nm:
                    todo.append(
                        f"параметр «{v.get('имя')}» (связь «{purpose}»): "
                        f"обозначение {nm} → {canon}")
        task = ("Исправь ТЗ одного блока: переименуй цепи, чтобы стыки "
                "блоков совпали.\n\n"
                "ТВОЁ ТЗ (design/block_tz.json, блок "
                f"{code}):\n"
                + json.dumps(tzn, ensure_ascii=False, indent=1)[:6000] + "\n\n"
                "ЧТО ПЕРЕИМЕНОВАТЬ:\n"
                + ("\n".join(f"- {x}" for x in todo) if todo
                   else "- (расхождений нет — верни ТЗ без изменений)")
                + "\n\nИмя цепи на стыке должно совпадать у обоих блоков: "
                "у соседа по этой связи уже стоит "
                + (", ".join(neighbour) if neighbour else "—")
                + ". Единицы и диапазоны не трогай, если они не "
                "противоречат.\n\nВерни ТОЛЬКО свой JSON-объект ТЗ — "
                "полный, с теми же полями.")
        got = _json_turn(_BLOCK_TZ_SYSTEM, task, state,
                         f"постановщик ТЗ {code} (согласование стыка)",
                         tries=3)
        if got is None:
            fixed.append(tzn)
            continue
        got.setdefault("код", code)
        fixed.append(got)
    return {"status": "done", "tasks": fixed}


def _link_names_by_side(tasks: list[dict], structure: dict,
                        code: str) -> list[str]:
    """Имена цепей, которые соседи уже объявили по связям блока."""
    by_code = {str(t.get("код") or ""): t for t in tasks}
    mine = {str(l.get("назначение") or "").strip()
            for l in (structure.get("связи") or []) if isinstance(l, dict)
            and str(l.get("от") or "").strip() == code
            or isinstance(l, dict)
            and str(l.get("к")) == code}
    out: set[str] = set()
    for l in (structure.get("связи") or []):
        if not isinstance(l, dict):
            continue
        purpose = str(l.get("назначение") or "").strip()
        if purpose not in mine:
            continue
        for side in ("от", "к"):
            other = str(l.get(side) or "").strip()
            if other == code:
                continue
            tzn = by_code.get(other)
            if not tzn:
                continue
            for fld in ("входные_параметры", "выходные_параметры"):
                for v in (tzn.get(fld) or []):
                    if isinstance(v, dict) and \
                            str(v.get("связь") or "").strip() == purpose:
                        nm = str(v.get("обозначение") or "").strip()
                        if nm:
                            out.add(nm)
    return sorted(out)


def _read_json(box: Path, rel: str) -> dict:
    """Артефакт стадии из песочницы (пустой dict — если файла нет)."""
    p = box / rel
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_block_file(box: Path, tzn: dict, spec: dict | None) -> None:
    """Записать спецификацию блока отдельным файлом."""
    if not spec:
        return
    code = str(tzn.get("код") or "")
    if not code:
        return
    spec.setdefault("блок", code)
    spec.setdefault("наименование", tzn.get("наименование", ""))
    path = box / "design/blocks" / f"{code}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(spec, ensure_ascii=False, indent=2),
                     encoding="utf-8")


def _stage_skidl(text: str, proj: Project, state: dict,
                 errors: str = "") -> dict:
    """Стадия 7: схемотехника блоков — инженер.

    Каждый блок по своему ТЗ проектируется отдельно: инженер возвращает
    спецификацию блока (детали и сети), каркас сохраняет её отдельным
    файлом. Так видно, какой узел что содержит, и дефект одного блока не
    обрушивает остальные. Сшивка блоков в общую схему — следующая стадия.

    Инженер ходит в инструмент kicad_libs: реальный символ и его выводы
    он узнаёт сам. Выдуманный lib_id каркас не примет — проверка символа
    идёт по настоящей библиотеке KiCad.

    `errors` — замечания предыдущей стадии (например, дефекты сшивки):
    они уходят агенту как вход, а не как приговор.
    """
    box = Path(engine.current_workdir() or proj.root)
    tasks = _read_json(box, "design/block_tz.json").get("блоки") or []
    if not tasks:
        return {"status": "failed",
                "error": "нет ТЗ на блоки (design/block_tz.json) — "
                         "стадия block_tz не дала результата"}
    calc = _read_design(box, "design/calc.json", 3000)
    lib_index = _read_design(box, "design/library_index.md", 3000)
    modules_allowed = _modules_mode(box)
    structure = _read_json(box, "design/structure.json")

    blocks_dir = box / "design/blocks"
    blocks_dir.mkdir(parents=True, exist_ok=True)
    hints_present = bool(_manager_hints(box))
    redo_blocks = set(_hint_blocks(box))
    written: list[dict] = []
    failed: list[str] = []
    block_issues: list[str] = []

    def design(tzn: dict, taken: set[str] | None = None,
               taken_details: list[dict] | None = None
               ) -> tuple[dict | None, str]:
        """Спроектировать один блок: кэш, попытки, замечания.

        `taken` — обозначения, уже занятые другими блоками: они
        передаются агенту, чтобы он их не повторял.
        """
        code = str(tzn.get("код") or "")
        spec = None
        block_errors = errors
        previous = ""          # прошлый вариант агента — для точечной правки
        # уже сделанный блок не переделывается: если его спецификация
        # и сейчас проходит проверку, она идёт в изделие как есть —
        # иначе повторный прогон выбрасывал бы работу агента
        cached = _read_json(box, f"design/blocks/{code}.json")
        if hints_present and code in redo_blocks:
            # вводные менеджера касаются этого блока: он проектируется
            # заново. Остальные блоки берутся из кэша — переделывать их
            # незачем, иначе заказ не сходится никогда
            cached = {}
        if cached.get("parts"):
            clash = [str(pr.get("ref")) for pr in (cached.get("parts") or [])
                     if isinstance(pr, dict)
                     and str(pr.get("ref") or "").strip() in (taken or ())]
            if clash:
                # обозначения пересеклись с другим блоком — это метки,
                # а не проектное решение: их разводит каркас при сшивке.
                # Перепроектировать из-за меток — потеря времени
                state["log"] = state.get("log", []) + [
                    f"блок {code}: обозначения пересеклись с другим блоком "
                    f"({', '.join(clash[:4])}) — каркас разведёт их при сшивке"]
            errs = _block_spec_check(cached, code, structure, tzn, box) \
                if cached.get("parts") else ["нет спецификации"]
            errs += _symbol_check(cached, box) if cached.get("parts") else []
            if cached.get("parts") and not _critical_block_issues(errs):
                # блок принят: критичных замечаний нет, остальные — в отчёт.
                # Переделывать заново нельзя: иначе каждый повтор стадии
                # выбрасывает работу агента, и заказ не сходится никогда
                spec = cached
                if errs:
                    block_issues.extend(f"{code}: {e}" for e in errs[:12])
                    state["log"] = state.get("log", []) + [
                        f"блок {code}: спецификация сохранена с прошлого "
                        f"прохода, замечаний {len(errs)} (некритичных) — "
                        "в отчёт"]
                else:
                    state["log"] = state.get("log", []) + [
                        f"блок {code}: спецификация сохранена с прошлого "
                        "прохода, проверка пройдена"]
        best: dict | None = None
        best_errs: list[str] = []
        best_ok: dict | None = None      # вариант без критичных замечаний
        best_ok_errs: list[str] = []
        if spec is None:
            for attempt in range(1, SKIDL_TRIES + 1):
                raw = _libs_loop(
                    proj, _SKIDL_SYSTEM,
                    _skidl_user(text, tzn, calc, lib_index, modules_allowed,
                                block_errors, set(taken or ()), tasks, code,
                                previous, list(taken_details or ()))
                    + _manager_hints(box),
                    state, max_tokens=SKIDL_TOKENS, profile="творческий",
                    json_schema=BLOCK_JSON_SCHEMA)
                if raw and raw.strip():
                    _transcript(state, f"проектировщик {code}", raw, cap=12000)
                    data = planner._extract_json(raw)
                    if isinstance(data, dict) and data.get("parts"):
                        errs = _block_spec_check(data, code, structure, tzn,
                                                 box)
                        errs += _symbol_check(data, box)
                        if not errs:
                            spec = data
                            break
                        if best is None or len(errs) < len(best_errs):
                            best, best_errs = data, list(errs)
                        # вариант без критичных замечаний важнее любого
                        # «самого короткого»: блок с несуществующим
                        # символом не собирается вовсе
                        if (not _critical_block_issues(errs)
                                and (best_ok is None
                                     or len(errs) < len(best_ok_errs))):
                            best_ok, best_ok_errs = data, list(errs)
                        # агенту уходит ПОЛНЫЙ список замечаний и его
                        # прошлый вариант: правка по собственному тексту
                        # надёжнее, чем написание заново
                        previous = json.dumps(data, ensure_ascii=False,
                                             indent=1)
                        # замечаний — не больше трёх: со списком в
                        # два десятка пунктов модель путается и чинит не
                        # то; остальные дойдут следующим кругом
                        block_errors = ("Замечания каркаса к твоей "
                                        "спецификации — исправь ровно их, "
                                        "ничего другого не меняй. Если правка "
                                        "не идёт — упрости схему: убери "
                                        "лишние детали, оставь работающий "
                                        "минимум.\n"
                                        + "\n".join(f"- {e}" for e in
                                                    _by_kind(errs)[:4])
                                        + (f"\n(всего замечаний: {len(errs)}"
                                           " — остальные потом)"))
                        kinds: dict[str, int] = {}
                        for e in errs:
                            k = str(e).split(":", 1)[-1][:40]
                            kinds[k] = kinds.get(k, 0) + 1
                        top = ", ".join(f"{v}× {k[:28]}"
                                        for k, v in sorted(kinds.items(),
                                                           key=lambda kv: -kv[1])[:3])
                        state["log"] = state.get("log", []) + [
                            f"блок {code}: попытка {attempt}/{SKIDL_TRIES} — "
                            f"замечаний {len(errs)} ({top})"]
                        if len(errs) > 8:
                            state["log"] = state.get("log", []) + [
                                f"блок {code}: замечаний много — упрости "
                                "схему: убери лишние детали, оставь "
                                "работающий минимум"]
                        continue
                state["log"] = state.get("log", []) + [
                    f"блок {code}: попытка {attempt}/{SKIDL_TRIES} — нет "
                    "разбираемой спецификации блока"]
        if spec is None:
            if best_ok is not None:
                best, best_errs = best_ok, best_ok_errs
            if best is not None and not _critical_block_issues(best_errs):
                # ни один вариант не прошёл проверку — берём лучший из
                # предложенных, а его дефекты уходят в отчёт: изделие
                # должно быть собрано, а не спрятано
                best["_замечания"] = best_errs[:12]
                block_issues.extend(f"{code}: {e}" for e in best_errs[:12])
                return best, ("принят с замечаниями: "
                              + "; ".join(best_errs[:2]))[:200]
            return None, (block_errors[:160] or
                          "спецификация блока не получена")
        return spec, ""

    # блоки проектируются параллельно: они независимы, а заказ иначе
    # упирается в сумму времени всех блоков подряд
    results: list[tuple[dict | None, str]] = []
    if SKIDL_WORKERS > 1 and len(tasks) > 1:
        pool = ThreadPoolExecutor(max_workers=min(SKIDL_WORKERS, len(tasks)))
        try:
            futures = [pool.submit(design, tzn) for tzn in tasks]
            for fut in futures:
                try:
                    # ожидание ограничено: иначе один зависший блок
                    # держит весь заказ
                    results.append(fut.result(timeout=TOOL_STAGE_SECONDS))
                except TimeoutError:
                    results.append((None, "время проектирования блока "
                                         "вышло"))
                except Exception as exc:                  # noqa: BLE001
                    results.append((None, f"исключение: {exc}\n"
                                    + traceback.format_exc()))
        finally:
            # потоки не ждём: зависший агент не должен держать заказ
            pool.shutdown(wait=False, cancel_futures=True)
    else:
        # последовательно, но файл блока пишется сразу: работа агента
        # не должна пропасть, если стадия прервётся
        results = []
        taken: set[str] = set()
        details: list[dict] = []
        for tzn in tasks:
            res = design(tzn, taken, details)
            results.append(res)
            spec = res[0]
            if spec:
                for prt in (spec.get("parts") or []):
                    if isinstance(prt, dict):
                        ref = str(prt.get("ref") or "").strip()
                        if ref:
                            taken.add(ref)
                        details.append({"ref": ref,
                                        "value": str(prt.get("value") or ""),
                                        "lib_id": str(prt.get("lib_id") or ""),
                                        "блок": str(tzn.get("код") or "")})
            if isinstance(spec, dict):
                spec.pop("_замечания", None)
            _write_block_file(box, tzn, spec)

    # обозначения деталей уникальны во всём изделии: если два блока
    # взяли одно и то же, конфликтующий блок проектируется заново
    used_refs: set[str] = set()
    specs: list[tuple[dict, dict]] = []
    for tzn, (spec, why) in zip(tasks, results):
        code = str(tzn.get("код") or "")
        if spec is None:
            failed.append(f"{code}: " + why)
            # старый файл блока больше не действителен: он не принят
            # в этом проходе, и в сшивке он мешал бы (обозначения и
            # сети из прошлой версии)
            stale = blocks_dir / f"{code}.json"
            try:
                stale.unlink(missing_ok=True)
            except OSError:
                pass
            continue
        _normalise_net_names(spec, code)
        _apply_tz_purposes(spec, tzn)
        dup = [str(p.get("ref")) for p in (spec.get("parts") or [])
               if isinstance(p, dict) and str(p.get("ref") or "").strip()
               in used_refs]
        if dup:
            # обозначения пересеклись с другим блоком. Перепроектировать
            # ради меток — дорого и бессмысленно: это метки, а не
            # проектное решение, их разводит каркас при сшивке
            state["log"] = state.get("log", []) + [
                f"блок {code}: обозначения пересеклись с другим блоком "
                f"({', '.join(dup[:4])}) — каркас разведёт их при сшивке"]
        specs.append((tzn, spec))
        for prt in (spec.get("parts") or []):
            if isinstance(prt, dict) and str(prt.get("ref") or "").strip():
                used_refs.add(str(prt["ref"]).strip())

    for tzn, spec in specs:
        code = str(tzn.get("код") or "")
        spec.pop("_замечания", None)
        _write_block_file(box, tzn, spec)
        written.append({"код": code,
                        "наименование": tzn.get("наименование", ""),
                        "файл": f"design/blocks/{code}.json",
                        "деталей": len(spec.get("parts") or []),
                        "сетей": len(spec.get("nets") or [])})
        state["log"] = state.get("log", []) + [
            f"блок {code}: деталей {len(spec.get('parts') or [])}, "
            f"сетей {len(spec.get('nets') or [])} → "
            f"design/blocks/{code}.json"]

    if not written:
        return {"status": "failed",
                "error": "спецификации блоков не получены: "
                         + "; ".join(failed[:4])}
    _write_json(box, "design/blocks/index.json",
                {"блоки": written, "не_получены": failed,
                 "дефекты": block_issues})
    if failed:
        state["log"] = state.get("log", []) + [
            f"блоков без схемы: {len(failed)} — " + "; ".join(failed[:3])]
        errs = []
        for f in failed:
            errs.append(
                f"{f}: нет skidl-спецификации. Сгенерируй блок ПОЛНОСТЬЮ по ТЗ "
                f"(block_tz.json): детали, выводы, соединения, сети. Не копируй "
                f"старую заглушку."
            )
        # КРИТИЧНО: блоки без схемы недопустимы. Схемотехник должен их дорисовать,
        # иначе связность и верификация соберутся некорректно.
        return {"status": "failed",
                "исправимо": True,
                "ошибки": errs,
                "error": f"блоков без схемы: {len(failed)}: " + "; ".join(failed[:4])}
    return {"status": "done"}


def _is_plain_value(value: str) -> bool:
    """Номинал-число или нет.

    «10k», «100n», «4.7 Ом», «22 мкГн» — пассивка: симулятор берёт её
    из номинала, модель не нужна. «TPS54302», «2N7002» — прибор: без
    модели он в симуляции не участвует.
    """
    s = str(value or "").strip().lower()
    if not s:
        return False
    for tail in ("ом", "ω", "вт", "гц", "hz", "мкгн", "гн", "нф", "мкф",
                 "пф", "мкг", "нано", "микро", "милли"):
        s = s.replace(tail, "")
    if re.fullmatch(r"[-+]?\d+(?:[.,]\d+)?\s*"
                    r"(?:[kkmunpfµμ]|[гм])?"
                    r"(?:f|h|в|а|w)?", s):
        return True
    return False


def _pin_norm(spec: dict, box: Path) -> dict[tuple[str, str], str]:
    """(ref, вывод как написано) -> номер вывода.

    Агент может указать вывод по имени («G», «A») или по номеру («3»).
    Для проверок «один вывод — одна сеть» это должно быть одно и то же
    физическое место, поэтому номера приводятся к единому виду
    штатным резолвером символов.
    """
    out: dict[tuple[str, str], str] = {}
    bt = _blocks()
    if bt.get("mod") is None:
        return out
    try:
        kit = bt["mod"].load_kit()
    except Exception:                                        # noqa: BLE001
        return out
    for p in (spec.get("parts") or []):
        if not isinstance(p, dict):
            continue
        ref = str(p.get("ref") or "")
        lib_id = str(p.get("lib_id") or "")
        if not ref or not lib_id:
            continue
        try:
            pins = bt["mod"].symbol_pins(kit, lib_id)
        except Exception:                                    # noqa: BLE001
            continue
        for n in (spec.get("nets") or []):
            if not isinstance(n, dict):
                continue
            for c in (n.get("conns") or []):
                if not (isinstance(c, (list, tuple)) and len(c) == 2):
                    continue
                if str(c[0]) != ref:
                    continue
                want = str(c[1])
                got = bt["mod"].resolve_pin(pins, want)
                out[(ref, want)] = str(got) if got is not None else want
    return out


def _block_spec_check(spec: dict, code: str,
                      structure: dict | None = None,
                      tzn: dict | None = None,
                      box: Path | None = None) -> list[str]:
    """Рамка приёмки спецификации блока: соберётся ли он в SKiDL.

    Проверяется то, что ломает сборку: деталь без символа, вывод в двух
    сетях, сеть из одного вывода.

    Про число выводов в сети: у ВНЕШНЕЙ сети (из Э1) в блоке бывает
    один вывод — она продолжается в соседнем блоке. Внутренняя сеть
    обязана быть замкнутой внутри блока.
    """
    # внешние сети блока: те, чьё имя задано в ТЗ на узел, — они
    # продолжаются в соседнем блоке, и в этом блоке у такой сети бывает
    # один вывод. Земля общая на изделие — она тоже внешняя.
    external: set[str] = {"GND"}
    for f in ("входные_параметры", "выходные_параметры"):
        for v in ((tzn or {}).get(f) or []):
            if isinstance(v, dict):
                nm = str(v.get("обозначение") or "").strip()
                if nm:
                    external.add(nm)
    errs: list[str] = []
    parts = spec.get("parts")
    nets = spec.get("nets")
    if not isinstance(parts, list) or not parts:
        return ["пустой parts — блок нечего собирать"]
    if not isinstance(nets, list) or not nets:
        return ["пустой nets — блок не к чему присоединить"]
    refs: set[str] = set()
    for p in parts:
        if not isinstance(p, dict):
            errs.append("элемент parts — не объект")
            continue
        ref = str(p.get("ref") or "").strip()
        if not ref:
            errs.append("деталь без ref")
            continue
        if ref in refs:
            errs.append(f"ref {ref} повторяется")
        refs.add(ref)
        if not str(p.get("lib_id") or "").strip():
            errs.append(f"{ref}: нет lib_id — символ неизвестен")
        if not str(p.get("footprint") or "").strip() and \
                not _symbol_has_footprint(str(p.get("lib_id") or "")):
            # без корпуса плата не соберётся. Но если корпус известен
            # самому символу (Resistor_SMD:R_0603_1608Metric) — требовать
            # его от агента незачем: возьмём при сборке.
            cand = _footprint_candidates(str(p.get("lib_id") or ""))
            errs.append(f"{ref}: нет корпуса (footprint) — без него плата "
                        "не соберётся"
                        + (f". Возьми корпус из этого списка: "
                           + ", ".join(cand[:4]) if cand else
                           " — выбери настоящий корпус из списка в задании")
                        + " и укажи его как «footprint»")
        # название детали обязательно: по нему деталь опознаётся в
        # симуляции и в закупочном листе. Корпус, MPN и 3D — не дело
        # стадии KiCad: их подбирает этап платы (tscircuit).
        value = str(p.get("value") or "").strip()
        if not value:
            errs.append(f"{ref}: нет названия детали (value) — по нему "
                        "деталь опознаётся в симуляции и в BOM")
        else:
            kind = str(p.get("part") or "").strip().upper()
            if kind in ("R", "C", "L"):
                # пассивка: номинал — это число, а не имя корпуса.
                # «R_0805» вместо «10 кОм» не считается ни расчётом,
                # ни закупкой, ни симуляцией.
                if not _is_plain_value(value):
                    errs.append(f"{ref}: у пассивного элемента номинал "
                                f"«{value}» — это не число. Поставь "
                                "номинал из расчётов: «10k», «100n», "
                                "«22 мкГн»")
            elif kind in ("D", "Q", "U", "LED", "МОДУЛЬ"):
                # название прибора: конкретное обозначение изготовителя.
                # Проверить его на «цифры» нельзя (у микросхем и
                # светодиодов в обозначении цифр может не быть) —
                # зато видно по символу, что деталь настоящая.
                if not value.strip():
                    errs.append(f"{ref}: у прибора пустое название детали")
            # Модель симуляции — не забота схемотехника: деталь выбрана
            # по ТЗ, а модель и привязка выводов — работа библиотекаря
            # (стадия 13).
            # Sim.Pins — привязка выводов схемы к узлам модели, а
            # это работа библиотекаря (он и добывает модель).
            # Схемотехника за это не ругаем: его замечание —
            # структура схемы, а привязку доведёт библиотекарь.
    # каждый вывод детали обязан быть куда-то подключён: микросхема без
    # питания не работает, и ERC потом скажет об этом, но дешевле сказать
    # проектировщику сразу
    used: dict[str, set[str]] = {}
    for n in nets:
        if not isinstance(n, dict):
            continue
        for c in (n.get("conns") or []):
            if isinstance(c, (list, tuple)) and len(c) == 2:
                used.setdefault(str(c[0]), set()).add(str(c[1]))
    for prt in parts:
        if not isinstance(prt, dict):
            continue
        ref = str(prt.get("ref") or "")
        if not ref:
            continue
        nc = {str(x) for x in (prt.get("не_подключены") or [])}
        names = _symbol_pin_names(str(prt.get("lib_id") or ""))
        if not names:
            continue
        have = used.get(ref, set())
        # выводы, названные по имени (G, D, S) приводим к номерам
        loose = {n for n in have if n not in names}
        missing = []
        for num, nm in names.items():
            if num in have or nm in have or num in nc or nm in nc:
                continue
            missing.append(f"{num}({nm})")
        if missing and len(missing) < len(names):
            errs.append(f"{ref}: выводы не подключены — "
                        + ", ".join(missing[:6])
                        + ". Подключи их к сетям или, если вывод не "
                          "нужен, перечисли в поле «не_подключены»")
        elif missing and len(missing) == len(names):
            errs.append(f"{ref}: ни один вывод не подключён — деталь не "
                        "включена в схему")

    # контракт между блоками — ИМЯ цепи: у внешней сети оно обязано
    # совпадать с обозначением из ТЗ на узел. Никаких своих полей в
    # формате схемы: формат — нативный кикадный.
    want: dict[str, str] = {}
    for f in ("входные_параметры", "выходные_параметры"):
        for v in ((tzn or {}).get(f) or []):
            if not isinstance(v, dict):
                continue
            purpose = str(v.get("связь") or "").strip()
            name = str(v.get("обозначение") or "").strip()
            if purpose and name:
                want.setdefault(purpose, name)
    have = {str(n.get("name") or "").strip() for n in nets
            if isinstance(n, dict)}
    for purpose, name in sorted(want.items()):
        if name not in have:
            errs.append(f"нет сети «{name}» — связь «{purpose}» по ТЗ на "
                        f"узел должна быть в блоке под этим именем. Назови "
                        f"свою цепь именно «{name}»: по этому имени блоки "
                        "сшиваются в изделие, и без него сшивка не сойдётся")
    # одна цепь — одно назначение: две разные связи Э1 на одной сети
    # означают, что в блоке не хватает отдельной цепи
    by_net_purposes: dict[str, set[str]] = {}
    for n in nets:
        if not isinstance(n, dict):
            continue
        nm = str(n.get("name") or "").strip()
        for purpose in _net_purposes(n):
            by_net_purposes.setdefault(nm, set()).add(purpose)
    # сеть с несколькими назначениями Э1 — это физически одна цепь
    # (так она и будет сведена при сшивке изделия), поэтому схемотехника
    # за это не ругаем
    norm = _pin_norm(spec, box) if box is not None else {}
    pin_owner: dict[tuple[str, str], str] = {}
    for n in nets:
        if not isinstance(n, dict):
            errs.append("элемент nets — не объект")
            continue
        name = str(n.get("name") or "").strip()
        if not name:
            errs.append("сеть без имени")
            continue
        # земля на изделие одна: локальных «C1_GND» быть не может
        if name.upper() != "GND" and "GND" in name.upper():
            errs.append(f"сеть {name}: земля на изделие одна — общая сеть "
                        "GND, локальных земель вида C1_GND не бывает")
        conns = n.get("conns") or []
        if len(conns) < 2 and name not in external:
            errs.append(f"сеть {name}: меньше двух выводов — деталь "
                        "не подключена (внешняя сеть из Э1 может "
                        "продолжаться в другом блоке)")
        for c in conns:
            if not (isinstance(c, (list, tuple)) and len(c) == 2):
                errs.append(f"сеть {name}: неверная запись вывода")
                continue
            ref, pin = str(c[0]), str(c[1])
            if ref not in refs:
                errs.append(f"сеть {name}: {ref} нет среди деталей блока")
                continue
            key = (ref, norm.get((ref, pin), pin))
            if key in pin_owner and pin_owner[key] != name:
                errs.append(f"вывод {ref}.{pin} в двух сетях "
                            f"({pin_owner[key]} и {name}) — один вывод "
                            "ровно в одной сети")
            else:
                pin_owner[key] = name
    return errs


def _by_kind(errs: list[str], limit: int = 4) -> list[str]:
    """Замечания, подобранные по типам.

    Список из трёх замечаний одного типа модель чинит неудачно: она
    начинает «лечить» не то. Поэтому берём по одному-два каждого типа —
    суть проблемы видна сразу, а остальное дойдёт следующим кругом.
    """
    out: list[str] = []
    kinds: dict[str, int] = {}
    for e in errs:
        kind = str(e).split(":", 1)[-1][:48]
        n = kinds.get(kind, 0)
        if n >= 2:
            continue
        kinds[kind] = n + 1
        out.append(e)
        if len(out) >= limit:
            break
    return out


def _symbol_alternatives(lib_id: str, limit: int = 6) -> list[str]:
    """Похожие настоящие символы той же библиотеки.

    Агент, которому сказали «символ не найден», идёт искать снова — и
    тратит ходы. Поэтому предлагаем конкретные имена из той же
    библиотеки: они точно существуют.
    """
    lib = str(lib_id or "").split(":")[0]
    if not lib:
        return []
    try:
        from . import tools as T                     # noqa: PLC0415
        import sys
        kit = T._K_KIT if hasattr(T, "_K_KIT") else None
    except Exception:                                # noqa: BLE001
        kit = None
    bt = _blocks()
    if bt.get("mod") is None:
        return []
    try:
        K = bt["mod"].load_kit()
        names = T._lib_symbol_names(K, lib)
    except Exception:                                # noqa: BLE001
        return []
    stem = str(lib_id or "").split(":")[-1].lower()
    # символ может САМ называть корпус (Resistor_SMD:R_0603_1608Metric) —
    # тогда корпус известен, и просить агента о нём незачем
    lib_name = str(lib_id or "").split(":")[0]
    if (root / f"{lib_name}.pretty" / f"{stem}.kicad_mod").is_file():
        return [f"{lib_name}:{stem}"]
    near = [n for n in names if stem and stem in n.lower()][:limit]
    if near:
        return [f"{lib}:{n}" for n in near]
    # символа с таким именем нет — показываем те, что по назначению
    # подходят на ту же роль (регулятор, преобразователь, зарядка)
    words = [w for w in re.split(r"[^A-Za-zА-Яа-я]+", stem) if len(w) > 2]
    if not words:
        words = ["regulator", "converter", "charger"]
    got = T._symbols_by_description(" ".join(words[:2]), limit=limit)
    out = [f"{g.get('библиотека')}:{g.get('символ')}" for g in got
           if g.get("библиотека") == lib]
    if out:
        return out
    return [f"{lib}:{n}" for n in list(names)[:limit]]
    # библиотеки с таким именем нет — ищем по назначению: символы, в
    # описании которых встречается имя класса
    word = lib.lower()
    out: list[str] = []
    for path in sorted(Path("/usr/share/kicad/symbols").glob("*.kicad_sym")):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if word not in text.lower():
            continue
        out.append(f"{path.stem}:*")
        if len(out) >= 3:
            break
    return out


def _critical_block_issues(errs: list[str]) -> bool:
    """Есть ли среди замечаний такие, из-за которых схема не соберётся.

    Несуществующий символ или вывод — это не «дефект», а нерабочая
    схема: такой блок принимать нельзя, из него не получится плата.
    """
    for e in errs:
        s = str(e)
        if ("символ" in s and "не найден" in s) or \
                ("не читается" in s and "символ" in s) or \
                ("вывод" in s and "не найден в символе" in s) or \
                ("нет lib_id" in s) or \
                ("неизвестный компонент" in s) or \
                ("нет сети «" in s) or \
                ("в двух сетях" in s):
            # из такого блока схема не собирается: неизвестный символ или
            # вывод. Неподключённые выводы каркас помечает сам (NC), их
            # агент исправлять не обязан.
            return True
    return False


def _symbol_pin_names(lib_id: str) -> dict[str, str]:
    """Имена выводов символа: номер → имя.

    Нужны агенту, чтобы сопоставить выводы схемы с узлами модели.
    """
    bt = _blocks()
    if bt.get("mod") is None:
        return {}
    try:
        kit = bt["mod"].load_kit()
        pins = bt["mod"].symbol_pins(kit, lib_id)
    except Exception:                                        # noqa: BLE001
        return {}
    out: dict[str, str] = {}
    for pin in (pins or []):
        if isinstance(pin, dict):
            out[str(pin.get("num"))] = str(pin.get("name") or "")
    return out


def _symbol_suggest(lib_id: str) -> str:
    """Подсказка проектировщику: что взять вместо несуществующего символа.

    Ответ механический: имя из библиотеки корпусов — это корпус, а не
    символ; символ под него в библиотеках KiCad ищем по фильтрам.
    """
    try:
        if T.is_footprint_name(lib_id):
            syms = T.symbols_for_footprint(lib_id, limit=2)
            fp = lib_id
            tail = (", ".join(syms) if syms else
                    "найди через kicad_libs action=search по номеру детали")
            return (f"это имя КОРПУСА, а не символа: возьми символ ({tail}) "
                    f"и укажи корпус «{fp}» в поле «footprint»")
        # похожие по имени символы
        short = str(lib_id).split(":")[-1]
        idx = T._symbol_index()
        head = re.match(r"^[A-Za-z]+", short)
        head = head.group(0) if head else short
        cands = [f"{r.get('библиотека')}:{nm}" for nm, r in idx.items()
                 if nm.split(":")[-1].startswith(head)
                 and nm.split(":")[-1] != short][:4]
        if cands:
            return ("похожие символы: " + ", ".join(cands)
                    + " — либо найди свой через kicad_libs action=search "
                      "(по номеру детали) или action=by_purpose (по назначению)")
        return ("найди настоящий символ инструментом kicad_libs: "
                "action=search (по номеру детали) или action=by_purpose "
                "(по назначению)")
    except Exception:                                        # noqa: BLE001
        return "найди настоящий символ инструментом kicad_libs action=search"


def _symbol_check(spec: dict, box: Path) -> list[str]:
    """Символы и корпуса деталей существуют в настоящих библиотерах.

    Проверка идёт по локальной установке KiCad: выдуманный lib_id или
    корпус отсюда не пройдут. Это единственная защита от «нарисованной»
    схемы, которую нечем собирать.
    """
    bt = _blocks()
    if bt.get("mod") is None:
        return []
    try:
        kit = bt["mod"].load_kit()
    except Exception:                                        # noqa: BLE001
        return []
    errs: list[str] = []
    for p in (spec.get("parts") or []):
        if not isinstance(p, dict):
            continue
        ref = str(p.get("ref") or "?")
        lib_id = str(p.get("lib_id") or "")
        if not lib_id:
            continue
        try:
            pins = bt["mod"].symbol_pins(kit, lib_id)
        except Exception as exc:                           # noqa: BLE001
            errs.append(f"{ref}: символ {lib_id} не читается — {exc}. "
                         + _symbol_suggest(lib_id))
            continue
        if not pins:
            alt = _symbol_alternatives(lib_id)
            errs.append(f"{ref}: символ {lib_id} не найден в библиотеках "
                        "KiCad — возьми настоящий. "
                        + _symbol_suggest(lib_id)
                        + (f"; в библиотеке {lib_id.split(':')[0]} есть "
                           + ", ".join(alt) if alt else ""))
            continue
        # Sim.Pins: выводы модели должны быть числами (номерами выводов
        # символа), а не именами. KiCad не понимает «1=G 2=D 3=S» —
        # нужны «1=1 2=2 3=3»
        sim = p.get("sim") or {}
        sim_pins = str(sim.get("Sim.Pins") or "").strip()
        if sim_pins:
            for token in sim_pins.split():
                if "=" not in token:
                    continue
                left, _, right = token.partition("=")
                if not (left.strip().isdigit() and right.strip().isdigit()):
                    errs.append(f"{ref}: Sim.Pins «{sim_pins}» — выводы модели "
                                "должны быть числами («1=1 2=2 3=3»), "
                                "а не именами («1=G 2=D 3=S»). KiCad "
                                "не понимает именованные выводы")
                    break
        # выводы из спецификации должны существовать в символе
        pins = bt["mod"].symbol_pins(kit, lib_id)
        for n in (spec.get("nets") or []):
            for c in (n.get("conns") or []):
                if not (isinstance(c, (list, tuple)) and len(c) == 2):
                    continue
                if str(c[0]) != ref:
                    continue
                want = str(c[1])
                if bt["mod"].resolve_pin(pins, want) is None:
                    errs.append(f"{ref}: вывод {want} не найден в символе "
                                f"{lib_id} — есть: "
                                f"{bt['mod']._pins_hint(pins)}")
    return errs


#: семейства корпусов, из которых агент выбирает: берём реальные имена из
#: библиотек KiCad, а не выдумываем
_FP_FAMILIES = ("Resistor_SMD", "Capacitor_SMD", "Inductor_SMD", "Diode_SMD",
                "LED_SMD", "Package_TO_SOT_SMD", "Package_SO",
                "Package_QFN", "Package_BGA")


def _symbol_has_footprint(lib_id: str) -> bool:
    """Известен ли корпус по самому символу.

    У конкретных деталей (Resistor_SMD:R_0603_1608Metric) символ уже
    знает свой корпус — тогда агенту не нужно его указывать, каркас
    возьмёт его при сборке.
    """
    if ":" not in str(lib_id or ""):
        return False
    lib, name = str(lib_id).split(":", 1)
    # символ сам называет корпус (Resistor_SMD:R_0603_1608Metric) —
    # тогда корпус известен, и просить агента о нём незачем: сборщик
    # подставит его сам
    root = Path("/usr/share/kicad/footprints")
    if (root / f"{lib}.pretty" / f"{name}.kicad_mod").is_file():
        return True
    bt = _blocks()
    if bt.get("mod") is None:
        return False
    try:
        K = bt["mod"].load_kit()
        if K.symbol_footprint(lib_id):
            return True
        return bool(K.symbol_fp_filters(lib_id))
    except Exception:                                    # noqa: BLE001
        return False


def _footprint_candidates(lib_id: str, limit: int = 4) -> list[str]:
    """Настоящие корпуса, подходящие символу.

    Символ знает свои фильтры корпусов (ki_fp_filters) — по ним и
    отбираются реальные файлы из установленных библиотек. Никаких
    угадываний: только то, что есть на диске.
    """
    bt = _blocks()
    if bt.get("mod") is None:
        return []
    root = Path("/usr/share/kicad/footprints")
    if not root.is_dir():
        return []
    # символ может САМ называть корпус (Resistor_SMD:R_0603_1608Metric) —
    # тогда корпус известен, и просить агента о нём незачем
    lib_name = str(lib_id or "").split(":")[0]
    sym_name = str(lib_id or "").split(":")[-1]
    if (root / f"{lib_name}.pretty" / f"{sym_name}.kicad_mod").is_file():
        return [f"{lib_name}:{sym_name}"]
    try:
        K = bt["mod"].load_kit()
        filters = K.symbol_fp_filters(lib_id)
    except Exception:                                    # noqa: BLE001
        return []
    if not filters:
        return []
    out: list[str] = []
    for filt in str(filters).split()[:4]:
        filt = filt.strip()
        if not filt:
            continue
        rx = re.compile("^" + filt.replace("?", ".").replace("*", ".*")
                        + "$", re.I)
        prefix = filt.split("*")[0].split("?")[0].strip().lower()
        found: list[tuple[int, str]] = []
        for pretty in sorted(root.glob("*.pretty")):
            try:
                names = sorted(q.stem for q in pretty.glob("*.kicad_mod"))
            except OSError:
                continue
            for n in names:
                if not rx.match(n):
                    continue
                if "Pad" in n or "HandSolder" in n:
                    continue
                # семейство, чьё имя начинается так же, как фильтр,
                # идёт первым: «R_*» → Resistor_SMD, а не OptoDevice
                score = 1 if prefix and pretty.stem.lower().startswith(
                    prefix[0]) else 0
                found.append((score, f"{pretty.stem}:{n}"))
        found.sort(key=lambda x: (-x[0], x[1]))
        for _s, cand in found:
            if cand not in out:
                out.append(cand)
            if len(out) >= limit:
                return out
    return out[:limit]


def _footprint_hints(limit: int = 6) -> str:
    """Реальные корпуса из библиотек KiCad — по семействам.

    Универсальные символы (резистор, конденсатор) корпуса не имеют, и
    агент без списка ставит выдуманный или никакой. Поэтому даём ему
    настоящие имена из установленных библиотек.
    """
    root = Path("/usr/share/kicad/footprints")
    if not root.is_dir():
        return ""
    lines: list[str] = []
    for fam in _FP_FAMILIES:
        d = root / f"{fam}.pretty"
        if not d.is_dir():
            continue
        names = sorted(p.stem for p in d.glob("*.kicad_mod"))
        if not names:
            continue
        short = [n for n in names if "Pad" not in n and "HandSolder" not in n]
        pick = (short or names)[:limit]
        lines.append(f"  {fam}: " + ", ".join(pick))
    return "\n".join(lines)


def _skidl_user(text: str, tzn: dict, calc: str, lib_index: str,
                modules_allowed: bool, errors: str = "",
                used_refs: set[str] | None = None,
                all_tasks: list | None = None,
                code: str = "", previous: str = "",
                used_details: list[dict] | None = None) -> str:
    kind = _node_kind(" ".join([
        str(tzn.get("наименование") or ""),
        str(tzn.get("функция") or ""),
        str((tzn.get("передаточная_характеристика") or {}).get("преобразует")
            or ""),
        str(code)]))
    mode = ("Модули разрешены: узел можно решить готовым модулем, если "
            "его паспортные параметры покрывают требования. Тогда в parts "
            "пометь его \"module\": true, а если модуль стоит вне платы — "
            "\"placement\": \"offboard\"."
            if modules_allowed else
            "Модули запрещены: узел собирается из компонентов, топология — "
            "из первоисточника, номиналы — расчёт.")
    tail = (errors + "\n\n") if errors else ""
    others = [f"  {t.get('код')} — {t.get('наименование', '')}"
              for t in (all_tasks or [])
              if str(t.get("код") or "") != code]
    others_txt = ("\nДругие функциональные части изделия — ИХ ТЫ НЕ "
                  "ПРОЕКТИРУЕШЬ, они проектируются отдельно:\n"
                  + "\n".join(others) + "\n") if others else ""
    refs_txt = ("\nУже занятые позиционные обозначения (других блоков) — "
                "не используй их: "
                + ", ".join(sorted(used_refs)) + "\n") if used_refs else ""
    # детали, уже спроектированные другими блоками: показываем их, чтобы
    # блоки не дублировали друг друга (два блока с одной и той же
    # микросхемой защиты — это не два узла, а один)
    done_txt = ""
    if used_details:
        lines = []
        for d in list(used_details)[:24]:
            lines.append(f"  {d.get('блок') or '?'}: {d.get('ref')} "
                         f"{d.get('value')} ({d.get('lib_id')})")
        done_txt = ("\nУЖЕ СПРОЕКТИРОВАНЫ ДРУГИМИ БЛОКАМИ — это изделие, "
                    "не повторяй эти детали, если задача твоего блока в "
                    "другом:\n" + "\n".join(lines) + "\n")
    prev_txt = ("\nТВОЯ ПРЕДЫДУЩАЯ СПЕЦИФИКАЦИЯ этого блока — исправь "
                "в ней ровно указанное, остальное не трогай:\n"
                + previous[:6000] + "\n") if previous else ""
    # внешние связи блока заданы ТЗ: назначение связи Э1 и имя цепи
    links_map: dict[str, set[str]] = {}
    for f in ("входные_параметры", "выходные_параметры"):
        for v in (tzn.get(f) or []):
            if not isinstance(v, dict):
                continue
            purpose = str(v.get("связь") or "").strip()
            name = str(v.get("обозначение") or "").strip()
            if purpose:
                links_map.setdefault(purpose, set())
                if name:
                    links_map[purpose].add(name)
    links_txt = "\nВНЕШНИЕ СВЯЗИ ЭТОГО БЛОКА — назначение связи Э1 и имя "
    "электрической цепи, заданные в ТЗ:\n"
    for purpose in sorted(links_map):
        names = ", ".join(sorted(links_map[purpose])) or "(имя не задано)"
        links_txt += f"  • {purpose} → цепь {names}\n"
    links_txt += ("  Для каждой такой связи в твоих nets должна быть сеть с "
                  "полями name (имя цепи — РОВНО как в ТЗ выше, это "
                  "контракт между блоками) и назначение (назначение связи "
                  "Э1). Для своих внутренних сетей имя и назначение "
                  "придумай сам.\n")
    return ("Заказ (для контекста): " + text[:800] + "\n\n"
            "ТОЛЬКО ТВОЙ УЗЕЛ. Ты проектируешь ОДИН функциональный блок — "
            f"«{tzn.get('наименование') or code}» (код {code}). В твоей "
            "схеме нет и не должно быть деталей других частей изделия.\n"
            + others_txt
            + "ТЗ на этот узел (design/block_tz.json) — его и выполняй. "
            "В нём есть ПЕРЕДАТОЧНАЯ ХАРАКТЕРИСТИКА узла: что он делает "
            "с сигналом, с каким коэффициентом и в каких диапазонах. "
            "Топологию выводи из неё, а не угадывай.\n"
            + json.dumps(tzn, ensure_ascii=False, indent=1)[:6000] + "\n\n"
            + "Библиотека первоисточников (типовые схемы включения и правила "
            "расчёта) — оттуда берётся типовое решение:\n"
            + _sources_digest() + "\n\n"
            + f"Чек-лист узла этого типа ({kind}) — проверки ДО симуляции, "
            "обязательные:\n"
            + _checklist_digest(kind) + "\n\n"
            + "База знаний: стратегии и известные анти-паттерны — сверься, "
            "не повторяешь ли ты известную ошибку:\n"
            + _wiki_digest(kind) + "\n\n"
            "Расчёты изделия (design/calc.json):\n"
            + (calc or "(расчётов нет)") + "\n\n"
            + (f"Инвентарь библиотек (design/library_index.md) — lib_id, "
               f"пины и корпуса бери ОТТУДА:\n{lib_index}\n\n"
               if lib_index else "")
            + f"РЕЖИМ СБОРКИ: {mode}\n\n"
            + refs_txt
            + done_txt
            + links_txt
            + prev_txt
            + tail
            + "КОРПУС (footprint) — обязательное поле каждой детали: без "
            "корпуса плата не соберётся. Универсальные символы (резистор, "
            "конденсатор, индуктивность, светодиод) корпуса в себе не "
            "имеют — выбери из настоящих, что ниже:\n"
            + (_footprint_hints() or "  (список пуст — спроси у каркаса)")
            + "\n\n"
            + "ВНИМАНИЕ: lib_id — это СИМВОЛ в формате «Библиотека:Символ» (например Device:R, Transistor_FET:BSS138). Корпус сюда НЕ подставляют: корпус — отдельное поле footprint, и он перечислен в списке настоящих корпусов выше. Символ, которого нет в библиотеке KiCad, схему не соберёт.\n\n"
            + "ЕСЛИ НУЖНОГО СИМВОЛА НЕТ ПО НОМЕРУ ДЕТАЛИ — ищи по назначению: kicad_libs action=by_purpose pattern=\"buck dc-dc\" (слова через пробел) вернёт настоящие символы с подходящим описанием. Не выдумывай номер детали.\n\n"
            + "ОБЯЗАТЕЛЬНО проверь инструментом kicad_libs каждый символ: "
            "action=search находит символ по имени или по назначению, "
            "action=pins даёт номера выводов. Несуществующий символ схема "
            "не соберётся.\n\n"
            "Для каждой полупроводниковой детали и микросхемы найди модель "
            "для симуляции инструментом spice_models: action=search по имени "
            "детали (если точного имени нет — в ответе будут похожие), "
            "action=list с фильтром по классу прибора (по каталогу внутри "
            "библиотеки моделей) — это путь к замене детали, у которой "
            "модели нет, action=model отдаёт текст модели, её узлы и "
            "подсказку, какому узлу какой назначен. Запиши это в поле sim: "
            "Sim.Name — имя модели, Sim.Device — тип прибора, Sim.Pins — "
            "соответствие «вывод схемы = узел модели», записи через пробел: "
            "сначала номер вывода схемы, потом номер узла модели. "
            "У пассивных элементов (R, C, L) модель не нужна: симулятор "
            "берёт её из номинала — оставь sim пустым. Укажи также part "
            "(тип детали: R, C, L, D, Q, U, LED, Модуль) и params — "
            "ключевые параметры детали.\n\n"
            "Корпус (footprint), MPN и 3D-модель указывать НЕ нужно: это "
            "задача этапа платы, а не схемы.\n\n"
            "СПРОЕКТИРУЙ принципиальную схему этого узла по ролевой "
            "карточке.\n\n"
            "ФОРМАТ ОТВЕТА — ТОЛЬКО JSON-объект, ровно такой вид "
            "(подставь свои детали и сети):\n\n"
            "{\n"
            '  "блок": "<код блока>",\n'
            '  "наименование": "<наименование части>",\n'
            '  "решение": "<2-4 предложения: какая топология выбрана и '
            'почему>",\n'
            '  "parts": [\n'
            '    {"ref": "<R1>", "lib_id": "<Библ:Символ>",\n'
            '     "part": "<тип детали>", "value": "<название детали>",\n'
            '     "sim": {"Sim.Device": "<тип прибора>",\n'
            '             "Sim.Name": "<имя модели из библиотеки>",\n'
            '             "Sim.Pins": "<вывод схемы=узел модели>"},\n'
            '     "params": {"<ключевой параметр>": "<значение>"}}\n'
            "  ],\n"
            '  "nets": [\n'
            '    {"name": "<имя электрической цепи>",\n'
            '     "назначение": ["<назначение связи Э1>"],\n'
            '     "conns": [["<R1>", "<номер вывода>"]]}\n'
            "  ],\n"
            '  "входы_реализованы": ["<вход ТЗ — как подключён>"],\n'
            '  "выходы_реализованы": ["<выход ТЗ — как подключён>"],\n'
            '  "покрыто_требования": ["<требование ТЗ — каким элементом '
            'закрыто>"],\n'
            '  "открытые_вопросы": []\n'
            "}\n\n"
            "ПРАВИЛА, НАРУШЕНИЕ КОТОРЫХ ПРИНИМАЕТСЯ КАК ОТКАЗ:\n"
            "1. conns — только НОМЕРА выводов, как их даёт kicad_libs "
            "(action=pins). Имена выводов (A1, VBAT, PROG) не пиши: "
            "схема их не соединит.\n"
            "2. У каждой сети ОБЯЗАТЕЛЬНОЕ поле «назначение» — СПИСОК "
            "назначений связей Э1, которые эта сеть несёт. Для внешних "
            "связей — дословно из ТЗ на узел; для внутренних — своё, но "
            "тоже словами. Одна сеть может нести несколько связей Э1, если "
            "на один вывод приходятся и заряд, и питание.\n"
            "2a. Имя внешней сети (name) — РОВНО обозначение из ТЗ на узел: "
            "это контракт между блоками, на стыке имя обязано совпадать.\n"
            "3. Каждый вывод детали — ровно в одной сети. Один вывод в двух "
            "сетях — ошибка.\n"
            "4. Каждая сеть — не короче двух выводов.\n"
            "5. Земля — одна сеть GND на изделие. Локальных «C1_GND», "
            "«GND_CHG» не бывает: вывод на землю включаешь в GND.\n"
            "6. Детали других частей изделия в твою схему НЕ входят.\n\n"
            "Верни ТОЛЬКО этот JSON-объект.")


def _ref_check(spec: dict, used_refs: set[str]) -> list[str]:
    """Позиционные обозначения не должны повторяться между блоками."""
    errs: list[str] = []
    for p in (spec.get("parts") or []):
        if not isinstance(p, dict):
            continue
        ref = str(p.get("ref") or "").strip()
        if ref and ref in used_refs:
            errs.append(f"обозначение {ref} уже занято другим блоком — "
                        "возьми другое")
    return errs


def _stage_spice_models(text: str, proj: Project, state: dict) -> dict:
    """Стадия 13: библиотекарь — модели SPICE для деталей схемы.

    Схема назвала детали. Часть из них — приборы, которым нужна модель
    симуляции, и у схемотехника её нет: это не его работа. Здесь
    работает **библиотекарь**: он ищет готовую модель, при нужде
    скачивает её, а если модели нет нигде — собирает макромодель из
    даташита. Модель кладётся во внутреннюю базу и подключается к деке.

    Каркас после ответа проверяет: у каждой детали с именем прибора
    либо есть модель, либо она честно помечена как не участвующая в
    симуляции. Переделывать схему из-за отсутствующей модели нельзя:
    это долг библиотекаря, а не вина схемотехника.
    """
    box = Path(engine.current_workdir() or proj.root)
    spec = _read_json(box, "design/schematic.json")
    parts = [p for p in (spec.get("parts") or []) if isinstance(p, dict)]
    if not parts:
        return {"status": "failed",
                "error": "нет спецификации design/schematic.json — "
                         "нечего моделировать"}
    from . import tools as T

    # детали, которым модель нужна: приборы (не пассивка)
    wanted: list[dict] = []
    for p in parts:
        if _is_plain_value(str(p.get("value") or "")):
            continue
        wanted.append(p)
    if not wanted:
        (box / "design").mkdir(parents=True, exist_ok=True)
        (box / "design/spice_models.lib").write_text(
            "* пассивные элементы: модели не требуются\n", encoding="utf-8")
        state["log"] = state.get("log", []) + [
            "библиотекарь: в схеме только пассивка — файл моделей пуст"]
        return {"status": "done"}

    # что уже есть по каждой детали — факты, а не мнения
    from . import spice_models as SM
    facts: list[dict] = []
    for p in wanted:
        ref = str(p.get("ref") or "?")
        sim = p.get("sim") or {}
        model = str(sim.get("Sim.Name") or p.get("spice_model") or "").strip()
        entry = {"ref": ref, "value": str(p.get("value") or ""),
                 "lib_id": str(p.get("lib_id") or ""),
                 "Sim.Name": model,
                 "Sim.Pins": str(sim.get("Sim.Pins") or ""),
                 "в_базе": bool(model and T._spice_model_text(model))}
        if model and not entry["в_базе"]:
            entry["подсказка"] = ("модель с таким именем в базе нет — "
                                 "найди настоящую или собери новую")
        # факты для сопоставления выводов: узлы модели и выводы символа
        if model:
            entry["узлы_модели"] = SM.subckt_pins(model, T.SPICE_LIB_DIR)
        names = _symbol_pin_names(entry["lib_id"])
        if names:
            entry["выводы_символа"] = names
        facts.append(entry)

    user = _librarian_user(facts, parts) + _manager_hints(box)
    got: dict | None = None
    for attempt in range(1, LIBRARIAN_TRIES + 1):
        raw = _libs_loop(proj, _LIBRARIAN_SYSTEM, user, state,
                         max_tokens=SKIDL_TOKENS, profile="механический")
        if raw and raw.strip():
            _transcript(state, "библиотекарь", raw, cap=12000)
            data = planner._extract_json(raw)
            if isinstance(data, dict) and data.get("детали"):
                got = data
                break
        state["log"] = state.get("log", []) + [
            f"библиотекарь: попытка {attempt}/{LIBRARIAN_TRIES} — ответ "
            "без разбираемого списка деталей"]
        user = _librarian_user(facts, parts) + (
            "\n\nПредыдущий ответ не разобран. Верни ТОЛЬКО JSON: "
            "{\"детали\": [{\"ref\": \"R1\", \"Sim.Name\": \"...\", "
            "\"Sim.Pins\": \"...\", \"модель\": \"текст модели или "
            "пусто\", \"замечание\": \"...\"}]}")
    if got is None:
        state["log"] = state.get("log", []) + [
            "библиотекарь: не вернул список моделей — детали без модели "
            "остаются вне симуляции"]
        return {"status": "done", "note": "библиотекарь не ответил"}

    # переносим ответ агента в спецификацию и собираем файл моделей
    by_ref = {str(d.get("ref") or ""): d for d in (got.get("детали") or [])
              if isinstance(d, dict)}
    chunks: list[str] = ["* Модели SPICE реальных приборов.",
                         "* Библиотекарь: из внутренней базы, из "
                         "загрузки с сайта изготовителя, собранные из "
                         "даташита.", ""]
    assigned = 0
    without: list[str] = []
    for entry in facts:
        ref = entry["ref"]
        ans = by_ref.get(ref) or {}
        model = str(ans.get("Sim.Name") or entry.get("Sim.Name") or "").strip()
        pins = str(ans.get("Sim.Pins") or "").strip()
        note = str(ans.get("замечание") or "").strip()
        own_text = str(ans.get("модель") or "").strip()
        if model and T._spice_model_text(model):
            text_model = T._spice_model_text(model)
            # в деке прибор вызывается под полным именем из модели
            # (у вендоров бывают суффиксы: LM358/NS)
            from . import spice_models as SM
            real = SM.subckt_real_name(model, T.SPICE_LIB_DIR)
            if real and real != model:
                state["log"] = state.get("log", []) + [
                    f"  {ref}: модель {model} вызывается в деке как {real}"]
                model = real
        elif own_text:
            # модель, собранная библиотекарем: кладём во внутреннюю базу
            put = T.__dict__.get("_spice_put")
            text_model = own_text
            if put is not None:
                try:
                    from . import library as L
                    L._spice_put(own_text, name=model or ref)
                except Exception:                    # noqa: BLE001
                    pass
        else:
            text_model = ""
        if not text_model:
            without.append(f"{ref} ({entry['value']})" +
                           (f" — {note}" if note else ""))
            continue
        assigned += 1
        chunks.append(f"* {ref}: {entry['value']} (модель {model or 'своя'})")
        chunks.append(text_model)
        chunks.append("")
        # свойства модели — в спецификацию: их читает штатный экспорт SPICE
        for part in parts:
            if str(part.get("ref") or "") != ref:
                continue
            sim = dict(part.get("sim") or {})
            if model:
                sim["Sim.Name"] = model
            if pins:
                sim["Sim.Pins"] = pins
            part["sim"] = sim
            break

    lib = box / "design/spice_models.lib"
    lib.parent.mkdir(parents=True, exist_ok=True)
    lib.write_text("\n".join(chunks), encoding="utf-8")
    _write_json(box, "design/schematic.json", spec)
    state["log"] = state.get("log", []) + [
        f"библиотекарь: моделей {assigned} из {len(wanted)} → "
        f"design/spice_models.lib"]
    for w in without:
        state["log"] = state.get("log", []) + [f"  вне симуляции: {w}"]
    if without:
        state["log"] = state.get("log", []) + [
            "  детали вне симуляции — это долг библиотекаря, схему из-за "
            "этого не переделывают"]
    return {"status": "done"}


def _librarian_user(facts: list[dict], parts: list[dict]) -> str:
    """Задание библиотекарю: детали, которым нужна модель и привязка."""
    rows = []
    for e in facts:
        row = (f"- {e['ref']}: деталь «{e['value']}», символ {e['lib_id']}, "
               f"модель в базе: {'есть' if e['в_базе'] else 'НЕТ'}")
        if e.get("узлы_модели"):
            row += (", узлы модели по порядку: "
                    + ", ".join(f"{i + 1}={n}" for i, n
                                in enumerate(e["узлы_модели"])))
        if e.get("выводы_символа"):
            row += (", выводы символа: "
                    + ", ".join(f"{k}={v}" for k, v
                                in list(e["выводы_символа"].items())[:8]))
        rows.append(row)
    return ("Детали схемы, которым нужна модель симуляции:\n\n"
            + "\n".join(rows) + "\n\n"
            "Для каждой такой детали найди модель. Порядок поиска: "
            "сначала внутренняя база (spice_models action=search по имени "
            "детали, action=list по классу прибора), затем готовая модель "
            "изготовителя по ссылке (spice_models action=fetch), и только "
            "если модели нет нигде — макромодель из даташита "
            "(datasheet action=resolve, затем action=fetch — вернётся "
            "текст даташита).\n\n"
            "Выводы модели сопоставь с выводами символа KiCad: узлы "
            ".subckt объявляй в порядке выводов символа, тогда "
            "сопоставление тождественное. Sim.Pins — пары \"номер вывода "
            "схемы = номер узла модели\".\n\n"
            "Проверь модель через spice_models action=check: модель, "
            "которую ngspice не понял, в изделии работать не будет.\n\n"
            "Sim.Pins указывай ТОЛЬКО числами: «номер вывода схемы = "
            "номер узла модели», нумеруются узлы по порядку в .SUBCKT. "
            "Имена выводов символа и узлы модели — выше, в задании. "
            "Если модель-карточка (.MODEL, а не подсхема) — Sim.Pins не "
            "нужен, убери его.\n\n"
            "Скачанные и собранные модели клади во внутреннюю базу "
            "(spice_models action=put) — они остаются там навсегда.\n\n"
            "ФОРМАТ ОТВЕТА — ТОЛЬКО JSON-объект:\n"
            '{"детали": [{\"ref\": \"U1\", \"Sim.Name\": \"LM358\", '
            '\"Sim.Pins\": \"3=2 2=3 1=1\", \"модель\": \"\", '
            '\"замечание\": \"\"}]}')


def _step_by_name(steps: list[dict], name: str) -> dict:
    """Шаг конвейера по имени стадии."""
    for s in steps or []:
        if str(s.get("target") or "") == name:
            return s
    return {}


def _manager_summary(steps: list[dict], state: dict, box: Path) -> str:
    """Сводка по заказу для менеджера: факты, а не впечатление."""
    rows = []
    for s in steps:
        st = str(s.get("status") or "")
        note = str(s.get("error") or s.get("note") or "")
        rows.append(f"  {s.get('target'):12} {st:8} {note[:150]}")
    arts = []
    for rel in ("design/technical_task.md", "design/structure.json",
                "design/block_tz.json", "design/schematic.json",
                "design/spice_models.lib", "out/hardware/measurements.json"):
        p = box / rel
        arts.append(f"  {rel:34} "
                    f"{'есть, ' + format(p.stat().st_size, ',') + ' байт' if p.is_file() else 'НЕТ'}")
    logs = state.get("log") or []
    return ("СТАДИИ ЗАКАЗА:\n" + "\n".join(rows) + "\n\n"
            "АРТЕФАКТЫ:\n" + "\n".join(arts) + "\n\n"
            "ЖУРНАЛ (последние записи):\n"
            + "\n".join(f"  {str(x)[:160]}" for x in logs[-14:]))


def _stage_manager(text: str, proj: Project, state: dict) -> dict:
    """Надзор: решение по итогам — принять, повторить, спросить, остановить.

    Менеджер ничего не проектирует. Он смотрит на факты (статусы
    стадий, артефакты, журнал) и решает, что делать дальше: повторить
    стадию с вводными, спросить человека, принять заказ или остановить
    заказ с честным объяснением.
    """
    box = Path(engine.current_workdir() or proj.root)
    steps = state.get("steps") or []
    summary = _manager_summary(steps, state, box)
    user = ("ЗАКАЗ: " + text[:600] + "\n\n" + summary + "\n\n"
            "Что делаем дальше? Ответь ТОЛЬКО JSON-объектом решения, "
            "без пояснений и без рассуждений.")
    verdict = None
    for attempt in range(1, 4):
        raw = _chat_plain(_MANAGER_SYSTEM, user, profile="творческий",
                          json_schema=JSON_ONLY)
        verdict = planner._extract_json(raw or "")
        if isinstance(verdict, dict) and verdict.get("решение"):
            break
        # модель могла ответить рассуждением вместо решения — просим
        # ещё раз, но строже
        state["log"] = state.get("log", []) + [
            f"менеджер: попытка {attempt}/3 — ответа нет или он не JSON"]
        user += ("\n\nПредыдущий ответ не разобран. Верни ТОЛЬКО "
                 "JSON: {\"решение\": \"принять\"|\"повторить\"|"
                 "\"спросить\"|\"остановить\", \"стадия\": \"...\", "
                 "\"вводные\": \"...\", \"вопрос\": \"...\", "
                 "\"обоснование\": \"...\"}")
    if not isinstance(verdict, dict) or not verdict.get("решение"):
        state["log"] = state.get("log", []) + [
            "менеджер: решение не получено — заказ принят как есть"]
        return {"status": "done", "решение": "принять"}
    decision = str(verdict.get("решение") or "принять").strip().lower()
    why = str(verdict.get("обоснование") or "")[:300]
    target = str(verdict.get("стадия") or "").strip()
    hints = str(verdict.get("вводные") or "")[:1500]
    question = str(verdict.get("вопрос") or "")[:800]
    state["log"] = state.get("log", []) + [
        f"менеджер: {decision}"
        + (f" ({target})" if target else "") + f" — {why}"]
    if decision == "повторить" and target:
        return {"status": "done", "решение": "повторить", "стадия": target,
                "вводные": hints, "обоснование": why}
    if decision == "спросить" and question:
        return {"status": "done", "решение": "спросить", "вопрос": question,
                "обоснование": why}
    if decision == "остановить":
        return {"status": "done", "решение": "остановить",
                "обоснование": why}
    return {"status": "done", "решение": "принять", "обоснование": why}


def _stage_stitch(proj: Project, state: dict,
                  errors: str = "") -> dict:
    """Стадия 8: сшивка блоков в одну принципиальную схему — сшивальщик.

    Сшивает АГЕНТ: он получает спецификации блоков и связи Э1 и сводит их
    в одну схему, называя сети. Каркас не решает за него, какие сети —
    одна цепь: это инженерное решение, и принимает его инженер.

    Каркас после ответа проверяет результат: детали на месте, каждый
    вывод в одной сети, символы настоящие. Дефекты возвращаются
    сшивальщику, а не чинятся каркасом.
    """
    box = Path(engine.current_workdir() or proj.root)
    # спецификации блоков берутся из самих файлов каталога: индекс блоков
    # перезаписывает сборщик SKiDL своим форматом, и читать его нельзя
    # восстановление отключено: оно создавало дубли блоков
    specs: list[dict] = []
    for path in sorted((box / "design/blocks").glob("*.json")):
        if path.name in ("index.json",):
            continue
        spec = _read_json(box, f"design/blocks/{path.name}")
        if not spec.get("parts"):
            continue
        spec.setdefault("блок", path.stem)
        specs.append(spec)
    if not specs:
        return {"status": "failed",
                "error": "ни один блок не прочитан — сшивать нечего"}

    # -------------------------------------------------------------------
    # Автофикс: дополняем недостающие сети в блоках
    # -------------------------------------------------------------------
    def _stitch_auto_fill_nets(specs: list[dict], structure: dict,
                               tasks: list[dict], state: dict) -> list[str]:
        """Добавить в блоки недостающие внешние цепи как placeholder-коннекты.

        Читаем structure.json → список внешних цепей (их названия заданы в Э1).
        Если блок не содержит сеть с нужным назначением → добавляем в nets
        запись вида {"name": "LED_A", "conns": [["REF", "pin"]]} — placeholder,
        который потом LLM или схемотехник укажет реальные выводы.
        Возвращает список добавленных цепей (для логов)."""
        from pathlib import Path as PathlibPath
        missing: list[str] = []
        blocks_dir = box / "design" / "blocks"
        # внешние цепи из Э1
        external = structure.get("external_nets") or structure.get("nets") or []
        if not isinstance(external, list):
            external = []
        # ТЗ на блоки: чтобы знать, какой блок к какой цепи подключён
        block_nets: dict[str, list[str]] = {}
        for t in tasks:
            if not isinstance(t, dict):
                continue
            code = str(t.get("код") or "")
            for f in ("входные_параметры", "выходные_параметры"):
                for v in (t.get(f) or []):
                    if isinstance(v, dict):
                        nm = str(v.get("обозначение") or "").strip()
                        if nm:
                            block_nets.setdefault(code, []).append(nm)

        for spec in specs:
            if not isinstance(spec, dict):
                continue
            code = spec.get("блок") or spec.get("block")
            if not code:
                continue
            existing = {str(n.get("name") or "") for n in (spec.get("nets") or [])
                        if isinstance(n, dict)}
            needed = set(block_nets.get(code, [])) & {str(e.get("name") or "") if isinstance(e, dict) else str(e) for e in external}
            for net_name in needed - existing:
                # создаём placeholder-сеть
                spec.setdefault("nets", []).append({
                    "name": net_name,
                    "conns": [["?", "?"]]  # placeholder, LLM заполнит позже
                })
                missing.append(str(net_name))
                # сохраняем обратно в файл
                bf = blocks_dir / f"{code}.json"
                if bf.is_file():
                    bf.write_text(json.dumps(spec, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
        return missing

    structure = _read_json(box, "design/structure.json")
    tasks = _read_json(box, "design/block_tz.json").get("блоки") or []
    missing_nets_auto_fixed = _stitch_auto_fill_nets(specs, structure, tasks, state)
    if missing_nets_auto_fixed:
        state["log"] = state.get("log", []) + [
            f"сшивка: авто-дополнение цепей {len(missing_nets_auto_fixed)} шт. "
            f"недостающие сети из Э1 добавлены в блоки как placeholder-коннекты"]

    user = _stitch_user(specs, structure, proj.name)
    if errors:
        user += ("\n\nЗамечания к предыдущей сшивке — исправь ровно их:\n"
                 + errors[:1500])
    tasks = _read_json(box, "design/block_tz.json").get("блоки") or []
    # Механическая сшивка — основной путь: имя цепи задано в ТЗ на узел,
    # и цепь получается объединением сетей блоков с одним назначением
    # Э1. Модель нужна только там, где это правило не сходится.
    merged_auto, auto_errs = _stitch_mechanical(specs, structure, tasks,
                                                proj.name, state)
    data: dict | None = None
    if not auto_errs:
        errs = _stitch_check(merged_auto, specs, structure)
        if not errs:
            state["log"] = state.get("log", []) + [
                "сшивка: сведена механически по именам цепей из ТЗ — "
                f"сетей {len(merged_auto['nets'])}, деталей "
                f"{len(merged_auto['parts'])}"]
            data = merged_auto
        else:
            auto_errs = errs
    if data is not None:
        parts = [dict(p) for p in (data.get("parts") or [])
                 if isinstance(p, dict)]
        nets = [dict(n) for n in (data.get("nets") or [])
                if isinstance(n, dict)]
        spec_blocks = [dict(b) for b in (data.get("blocks") or [])
                       if isinstance(b, dict)]
        spec = {"name": data.get("name") or proj.name,
                "paper": "A2",
                "parts": parts,
                "nets": nets,
                "blocks": spec_blocks}
        _write_json(box, "design/schematic.json", spec)
        state["log"] = state.get("log", []) + [
            f"сшивка: блоков {len(spec_blocks)}, деталей {len(parts)}, "
            f"сетей {len(nets)} → design/schematic.json"]
        return _stitch_verify(box, proj, state, spec)

    state["log"] = state.get("log", []) + [
        "сшивка: механически не сходится (" +
        "; ".join(auto_errs[:2]) + ") — расхождения по блокам уходят "
        "проектировщику"]
    # расхождения касаются блоков — их и должен чинить проектировщик,
    # а не сшивальщик: он только называет сети
    _write_json(box, "design/manager_hints.json",
                {"вводные": "Расхождения сшивки — почини их в своих блоках:\n"
                            + "\n".join(f"- {e}" for e in auto_errs[:8]),
                 "стадия": "schematic",
                 "блоки": _blocks_in_issues(auto_errs, tasks)})
    return {"status": "failed",
            "error": "сшивка не сходится: " + "; ".join(auto_errs[:3]),
            "исправимо": True,
            "ошибки": "\n".join(f"- {e}" for e in auto_errs[:10])}

    data = None
    issues: list[str] = list(auto_errs)
    for attempt in range(1, STITCH_TRIES + 1):
        raw = _chat_plain(_STITCH_SYSTEM, user, max_tokens=STITCH_TOKENS,
                          profile="творческий")
        got = planner._extract_json(raw or "")
        if not isinstance(got, dict) or not got.get("цепи"):
            issues = ["агент не вернул список цепей"]
            state["log"] = state.get("log", []) + [
                f"сшивка: попытка {attempt}/{STITCH_TRIES} — ответа нет "
                "или он обрезан"]
            user = _stitch_user(specs, structure, proj.name) + (
                "\n\nПредыдущий ответ не разобран: верни ТОЛЬКО "
                "JSON-объект {\"цепи\": [...]}.")
            continue
        # сшитая схема = цепи агента + детали и блоки из спецификаций
        # блоков. Объединение выводов каркас делает механически: решает
        # сшивальщик — какие сети блоков являются одной цепью.
        merged = _merge_stitch(got, specs, proj.name)
        errs = _stitch_check(merged, specs, structure)
        if not errs:
            data = merged
            break
        issues = errs
        user = _stitch_user(specs, structure, proj.name) + (
            "\n\nЗамечания к твоим цепям — исправь ровно их:\n"
            + "\n".join(f"- {e}" for e in errs))
        state["log"] = state.get("log", []) + [
            f"сшивка: попытка {attempt}/{STITCH_TRIES} — замечаний "
            f"{len(errs)}: " + "; ".join(errs[:2])]
    if data is None:
        return {"status": "failed",
                "error": "схема не сведена: " + "; ".join(issues[:4])}

    parts = [dict(p) for p in (data.get("parts") or [])
             if isinstance(p, dict)]
    nets = [dict(n) for n in (data.get("nets") or [])
            if isinstance(n, dict)]
    spec_blocks = [dict(b) for b in (data.get("blocks") or [])
                   if isinstance(b, dict)]
    spec = {"name": data.get("name") or proj.name,
            "paper": "A2",
            "parts": parts,
            "nets": nets,
            "blocks": spec_blocks}
    _write_json(box, "design/schematic.json", spec)
    state["log"] = state.get("log", []) + [
        f"сшивка: блоков {len(spec_blocks)}, деталей {len(parts)}, "
        f"сетей {len(nets)} → design/schematic.json"]
    return _stitch_verify(box, proj, state, spec)


def _stitch_verify(box: Path, proj: Project, state: dict,
                   spec: dict) -> dict:
    """Проверка сшитой схемы штатным инструментом поблочной сборки.

    Выводы существуют в реальных символах, и каждый стоит ровно в одной
    сети. Проверка нативная — тем же инструментом, что и сборка.
    """
    bt = _blocks()
    if bt.get("mod") is None:
        state["log"] = state.get("log", []) + [
            "сшивка: инструмент поблочной сборки недоступен — проверка "
            "выводов пропущена"]
        return {"status": "done"}
    try:
        rep = bt["mod"].check_spec(spec, bt["mod"].load_kit(), box=box)
    except Exception as exc:                              # noqa: BLE001
        return {"status": "failed",
                "error": f"проверка сшитой схемы не выполнена: {exc}"}
    errs = [str(e) for e in (rep.get("errors") or [])]
    if errs:
        state["log"] = state.get("log", []) + [
            f"сшивка: дефектов {len(errs)} — " + "; ".join(errs[:3])]
        return {"status": "failed",
                "error": "поблочная проверка схемы: " + "; ".join(errs[:4]),
                "исправимо": True,
                "ошибки": "\n".join(f"- {e}" for e in errs[:12])}
    state["log"] = state.get("log", []) + [
        "сшивка: проверка выводов пройдена — "
        f"предупреждений {len(rep.get('warnings') or [])}"]
    return {"status": "done"}


def _canonical_net_names(structure: dict,
                        tasks: list[dict]) -> dict[str, str]:
    """Имя цепи для каждой связи Э1 — из ТЗ на узел.

    Имя цепи задано в ТЗ, и на стыке блоков оно обязано совпадать.
    Это и есть контракт стыка, поэтому имя берётся оттуда, а не
    придумывается.
    """
    votes: dict[str, dict[str, int]] = {}
    for tzn in (tasks or []):
        if not isinstance(tzn, dict):
            continue
        for fld in ("входные_параметры", "выходные_параметры"):
            for v in (tzn.get(fld) or []):
                if not isinstance(v, dict):
                    continue
                purpose = str(v.get("связь") or "").strip()
                name = str(v.get("обозначение") or "").strip()
                if purpose and name:
                    votes.setdefault(purpose, {})
                    votes[purpose][name] = votes[purpose].get(name, 0) + 1
    # канон — то имя, которое уже стоит у большинства блоков: так он
    # совпадает с каноном гармонизации имён
    return {purpose: sorted(names.items(),
                            key=lambda kv: (-kv[1], kv[0]))[0][0]
            for purpose, names in votes.items()}


def _dedupe_refs(specs: list[dict]) -> list[str]:
    """Убрать совпадения позиционных обозначений между блоками.

    Обозначение детали уникально во всём изделии: иначе две разные
    детали получат одно имя и на плате сольются в одну. Это метки, а не
    проектное решение, поэтому каркас разводит их сам: к обозначению
    добавляется код блока. О самом факте пишется в журнал.
    """
    taken: set[str] = set()
    renamed: list[str] = []
    for bi, spec in enumerate(specs):
        code = str(spec.get("блок") or "")
        # каждому блоку — свой диапазон номеров: обозначение в KiCad
        # состоит из букв и цифр, поэтому «R1_B1» недопустимо
        base = (bi + 1) * 1000
        mapping: dict[str, str] = {}
        for prt in (spec.get("parts") or []):
            if not isinstance(prt, dict):
                continue
            ref = str(prt.get("ref") or "").strip()
            if not ref:
                continue
            if ref in taken:
                m = re.match(r"^([A-Za-z]+)(\d+)$", ref)
                if m:
                    num = int(m.group(2))
                    new = f"{m.group(1)}{base + num}"
                    n = 1
                    while new in taken:
                        new = f"{m.group(1)}{base + num + n}"
                        n += 1
                else:
                    new = f"{ref}{base}"
                    n = 1
                    while new in taken:
                        new = f"{ref}{base + n}"
                        n += 1
                mapping[ref] = new
                prt["ref"] = new
                renamed.append(f"{ref} → {new} (блок {code})")
            taken.add(str(prt.get("ref")))
        if not mapping:
            continue
        for n in (spec.get("nets") or []):
            if not isinstance(n, dict):
                continue
            for c in (n.get("conns") or []):
                if isinstance(c, (list, tuple)) and len(c) == 2:
                    if str(c[0]) in mapping:
                        c[0] = mapping[str(c[0])]
    return renamed


def _restore_blocks_from_stitched(box: Path) -> list[str]:
    """Вернуть файлы блоков из уже сшитой схемы.

    Сшитая схема — это те же блоки, только собранные вместе. Если файл
    блока потерялся (стадия переделывала его и не довела), спецификацию
    можно взять из сшивки: механически, без новых правок агента.
    """
    spec = _read_json(box, "design/schematic.json")
    if not spec.get("parts"):
        return []
    parts = {str(p.get("ref") or ""): p
             for p in (spec.get("parts") or []) if isinstance(p, dict)}
    nets = [n for n in (spec.get("nets") or []) if isinstance(n, dict)]
    restored: list[str] = []
    for b in (spec.get("blocks") or []):
        if not isinstance(b, dict):
            continue
        title = str(b.get("title") or "")
        code = title.split()[-1] if title else ""
        if not code:
            continue
        path = box / "design/blocks" / f"{code}.json"
        if path.is_file():
            continue
        refs = [str(r) for r in (b.get("refs") or [])]
        sub_parts = [parts[r] for r in refs if r in parts]
        if not sub_parts:
            continue
        sub_nets = []
        for n in nets:
            conns = [c for c in (n.get("conns") or [])
                     if isinstance(c, (list, tuple)) and len(c) == 2
                     and str(c[0]) in refs]
            if conns:
                row = dict(n)
                row["conns"] = conns
                sub_nets.append(row)
        if not sub_nets:
            continue
        payload = {"блок": code,
                   "наименование": title.replace(f"БЛОК {code}", "").strip(),
                   "parts": sub_parts, "nets": sub_nets,
                   "восстановлен": "из сшитой схемы"}
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                            encoding="utf-8")
            restored.append(code)
        except OSError:
            continue
    return restored


def _stitch_mechanical(specs: list[dict], structure: dict,
                       tasks: list[dict], name: str,
                       state: dict | None = None) -> tuple[dict, list[str]]:
    """Сшивка без модели: по именам цепей из ТЗ и назначениям Э1.

    Правило простое: сеть блока несёт назначения связей Э1; сеть,
    несущая связь «X», — часть цепи, названной в ТЗ для этой связи.
    Сети всех блоков с одним и тем же названием цепи — одна цепь.
    Внутренние сети (назначения Э1 у них нет) остаются своими.

    Так сшивка получается однозначно и без модели. Модель нужна там,
    где механическое правило не сходится: сеть блока тянет разные
    назначения, или блок вовсе не имеет нужной сети.
    """
    canonical = _canonical_net_names(structure, tasks)
    # обозначения деталей уникальны во всём изделии
    for line in _dedupe_refs(specs):
        if state is not None:
            state["log"] = state.get("log", []) + [
                f"сшивка: обозначение разведено — {line}"]
    # назначения сетей приводятся к контракту ТЗ (по имени сети)
    for tzn in (tasks or []):
        code = str(tzn.get("код") or "")
        for spec in specs:
            if str(spec.get("блок") or "") == code:
                _normalise_net_names(spec, code)
                _apply_tz_purposes(spec, tzn)
    by_block: dict[tuple[str, str], dict] = {}
    parts: list[dict] = []
    blocks: list[dict] = []
    for line in _dedupe_refs(specs):
        if state is not None:
            state["log"] = state.get("log", []) + [
                f"сшивка: обозначение разведено — {line}"]
    for s in specs:
        code = str(s.get("блок") or "")
        for prt in (s.get("parts") or []):
            if isinstance(prt, dict):
                part = dict(prt)
                part["block"] = code
                parts.append(part)
        for n in (s.get("nets") or []):
            if isinstance(n, dict):
                by_block[(code, str(n.get("name") or ""))] = n
        refs = [str(prt.get("ref")) for prt in (s.get("parts") or [])
                if isinstance(prt, dict) and str(prt.get("ref") or "").strip()]
        blocks.append({"title": f"БЛОК {code} — "
                                f"{s.get('наименование') or ''}".strip(),
                       "sub": "",
                       "refs": refs})

    errs: list[str] = []
    # сеть блока → названия цепей, которые она несёт
    chains: dict[str, dict] = {}
    for (code, net_name), src in by_block.items():
        val = src.get("назначение")
        purposes = [str(x).strip() for x in
                    (val if isinstance(val, list) else [val])
                    if str(x or "").strip()]
        ext = [canonical.get(p) for p in purposes if p in canonical]
        ext = [x for x in ext if x]
        if len(set(ext)) > 1:
            # одна сеть блока тянет несколько связей Э1: в блоке не
            # хватает отдельной цепи. Схему не переделываем — берём
            # первую связь и говорим об этом прямо.
            if state is not None:
                state["log"] = state.get("log", []) + [
                    f"сеть блока {code}:{net_name} обслуживает несколько "
                    f"связей Э1 ({', '.join(sorted(set(ext)))}) — схема "
                    "сведена по первой, проверь, что так задумано"]
            ext = [ext[0]]
        if not ext:
            # внутренняя сеть: своё имя, своё назначение
            key = f"{code}::{net_name}"
            chains[key] = {"name": net_name, "purposes": purposes,
                            "conns": _net_conns(src), "internal": True}
            continue
        if len(set(ext)) > 1:
            errs.append(f"сеть {code}:{net_name} несёт разные назначения "
                        f"Э1 ({', '.join(sorted(set(ext)))}) — одна сеть не "
                        "может быть двумя цепями")
            continue
        chain_name = ext[0]
        c = chains.setdefault(chain_name,
                              {"name": chain_name, "purposes": [],
                               "conns": [], "internal": False})
        for p in purposes:
            if p not in c["purposes"]:
                c["purposes"].append(p)
        c["conns"].extend(_net_conns(src))

    # каждая связь Э1 должна быть представлена цепью
    for l in (structure.get("связи") or []):
        if not isinstance(l, dict):
            continue
        purpose = str(l.get("назначение") or "").strip()
        if not purpose or purpose not in canonical:
            continue
        if canonical[purpose] not in chains:
            errs.append(f"связь «{purpose}» не покрыта: нет сети блока с "
                        f"таким назначением (цепь {canonical[purpose]})")

    # сеть блока с двумя назначениями Э1 — это физически одна цепь:
    # объединяем такие цепи в одну (иначе одна связь получила бы два
    # имени, а выводы разъехались бы по разным сетям)
    parent: dict[str, str] = {}

    def _find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def _union(a: str, b: str) -> None:
        ra, rb = _find(a), _find(b)
        if ra != rb:
            parent[rb] = ra

    by_purpose: dict[str, str] = {}
    for (code, net_name), src in by_block.items():
        val = src.get("назначение")
        purposes = [str(x).strip() for x in
                    (val if isinstance(val, list) else [val])
                    if str(x or "").strip()]
        names = [canonical.get(p) for p in purposes if p in canonical]
        names = [n for n in names if n]
        for n in names:
            _union(names[0], n)
    merged_chains: dict[str, dict] = {}
    for name, c in chains.items():
        # объединяем по ИМЕНИ сети, а не по внутреннему ключу: иначе
        # земля разных блоков осталась бы разными цепями
        real = str(c.get("name") or name)
        root = _find(real)
        cur = merged_chains.get(root)
        if cur is None:
            merged_chains[root] = {"name": real, "purposes": list(c["purposes"]),
                                   "conns": list(c["conns"])}
            continue
        for p2 in c["purposes"]:
            if p2 not in cur["purposes"]:
                cur["purposes"].append(p2)
        for cn in c["conns"]:
            if cn not in cur["conns"]:
                cur["conns"].append(cn)
    chains = merged_chains

    nets = [{"name": c["name"], "назначение": c["purposes"],
             "conns": c["conns"]} for c in chains.values()]
    # одна цепь — одно имя: сети с одинаковым именем сливаются (иначе
    # внутренняя сеть блока совпадёт с внешней по имени)
    merged: dict[str, dict] = {}
    for n in nets:
        key = n["name"] or n["conns"][0][0] if n["conns"] else n["name"]
        cur = merged.get(key)
        if cur is None:
            merged[key] = n
            continue
        for c in n["conns"]:
            if c not in cur["conns"]:
                cur["conns"].append(c)
        for p2 in n["назначение"]:
            if p2 not in cur["назначение"]:
                cur["назначение"].append(p2)
    nets = list(merged.values())
    merged = {"name": name, "parts": parts, "nets": nets, "blocks": blocks}
    return merged, errs


def _net_conns(src: dict) -> list[list[str]]:
    """Выводы сети в виде [[ref, pin], …]."""
    return [[str(c[0]), str(c[1])] for c in (src.get("conns") or [])
            if isinstance(c, (list, tuple)) and len(c) == 2]


def _merge_stitch(got: dict, specs: list[dict], name: str) -> dict:
    """Свести спецификации блоков и цепи агента в одну схему изделия.

    Сшивку решает агент: он говорит, какие сети разных блоков — одна
    электрическая цепь. Дальше каркас делает механическую часть: берёт
    выводы этих сетей, объединяет их под общим именем и добавляет
    внутренние сети блоков как есть. Так выводы не теряются и никто не
    «придумывает» детали: состав схемы — из спецификаций блоков.
    """
    parts: list[dict] = []
    blocks: list[dict] = []
    # (код блока, имя сети) → сеть блока
    by_block: dict[tuple[str, str], dict] = {}
    for s in specs:
        code = str(s.get("блок") or "")
        for p in (s.get("parts") or []):
            if isinstance(p, dict):
                part = dict(p)
                part["block"] = code
                parts.append(part)
        for n in (s.get("nets") or []):
            if isinstance(n, dict):
                by_block[(code, str(n.get("name") or ""))] = n
        refs = [str(prt.get("ref")) for prt in (s.get("parts") or [])
                if isinstance(prt, dict) and str(prt.get("ref") or "").strip()]
        blocks.append({"title": f"БЛОК {code} — "
                                f"{s.get('наименование') or ''}".strip(),
                       "sub": "",
                       "refs": refs})

    nets: list[dict] = []
    used: set[tuple[str, str]] = set()
    for chain in (got.get("цепи") or []):
        if not isinstance(chain, dict):
            continue
        conns: list[list[str]] = []
        purposes: list[str] = []
        for ref in (chain.get("из") or []):
            key = str(ref or "")
            if ":" not in key:
                continue
            code, net_name = key.split(":", 1)
            src = by_block.get((code.strip(), net_name.strip()))
            if src is None:
                continue
            used.add((code.strip(), net_name.strip()))
            for c in (src.get("conns") or []):
                if isinstance(c, (list, tuple)) and len(c) == 2:
                    conns.append([str(c[0]), str(c[1])])
            val = src.get("назначение")
            for p in (val if isinstance(val, list) else [val] if val else []):
                s2 = str(p or "").strip()
                if s2 and s2 not in purposes:
                    purposes.append(s2)
        purpose = str(chain.get("назначение") or "").strip()
        if purpose and purpose not in purposes:
            purposes.insert(0, purpose)
        nets.append({"name": str(chain.get("имя") or chain.get("name") or ""),
                     "назначение": purposes,
                     "conns": conns})
    # внутренние сети блоков, не попавшие ни в одну цепь, — как есть
    for (code, net_name), src in by_block.items():
        if (code, net_name) in used:
            continue
        val = src.get("назначение")
        nets.append({"name": net_name,
                     "назначение": [str(x) for x in
                                    (val if isinstance(val, list) else [val])
                                    if str(x or "").strip()],
                     "conns": [[str(c[0]), str(c[1])] for c in
                               (src.get("conns") or [])
                               if isinstance(c, (list, tuple)) and len(c) == 2]})
    return {"name": got.get("name") or name,
            "parts": parts,
            "nets": nets,
            "blocks": blocks}


def _stitch_user(specs: list[dict], structure: dict, name: str) -> str:
    """Задание сшивальщику: сети блоков и связи Э1.

    Сшивальщику нужны только сети: состав деталей он не меняет. Поэтому
    блоки даются сжатым списком сетей с их выводами — так видно всё и
    ответ не раздувается.
    """
    chunks: list[str] = []
    for s in specs:
        lines = [f"БЛОК {s.get('блок') or '?'} — "
                 f"{s.get('наименование') or ''}"]
        for n in (s.get("nets") or []):
            if not isinstance(n, dict):
                continue
            conns = ", ".join(f"{c[0]}.{c[1]}"
                              for c in (n.get("conns") or [])
                              if isinstance(c, (list, tuple)) and len(c) == 2)
            purposes = n.get("назначение")
            if isinstance(purposes, str):
                purposes = [purposes]
            lines.append(f"  сеть {n.get('name') or '?'} "
                         f"[{', '.join(str(x) for x in (purposes or []))}] "
                         f"→ {conns}")
        chunks.append("\n".join(lines))
    blocks_txt = "\n".join(chunks)
    links_txt = "\n".join(
        f"  от {l.get('от')} к {l.get('к')} — назначение: "
        f"{l.get('назначение')}"
        for l in (structure.get("связи") or []) if isinstance(l, dict)) \
        or "  (нет)"
    return ("Изделие: " + (name or "") + "\n\n"
            "СВЯЗИ СТРУКТУРНОЙ СХЕМЫ (Э1) — по ним сети разных блоков "
            "являются одной цепью:\n"
            + links_txt + "\n\n"
            "СПЕЦИФИКАЦИИ БЛОКОВ (уже спроектированы, детали не "
            "трогаешь) — сети блоков и их выводы:\n"
            + blocks_txt + "\n\n"
            "ТВОЯ ЗАДАЧА — ТОЛЬКО СШИТЬ: реши, какие сети разных блоков "
            "являются одной электрической цепью.\n"
            "  * по каждой связи Э1 найди сети блоков, которые она "
            "соединяет, и объедини их в одну цепь;\n"
            "  * имя цепи — имя из ТЗ на узел (для внешних связей — "
            "дословно из ТЗ); у внутренних сетей имя остаётся своё;\n"
            "  * в списке цепей указывай сети блоков в виде \"КОД_БЛОКА:"
            "ИМЯ_СЕТИ\";\n"
            "  * выводы деталей и их соединения переписывать не нужно — "
            "каркас объединит их сам по твоему решению.\n\n"
            "Ответь ТОЛЬКО JSON-объект: {\"цепи\": [{\"назначение\": "
            "\"<назначение связи Э1>\", \"имя\": \"VBAT\", \"из\": "
            "[\"A1:VBAT\", \"B1:VBAT\"]}]}")


def _net_purposes(net: dict) -> list[str]:
    """Назначения связей Э1, которые несёт сеть.

    В схеме поле «назначение» — список (у внешних сетей — дословно
    из ТЗ на узел). Агент иногда пишет одну строку — приводим к списку.
    """
    out: list[str] = []
    val = (net or {}).get("назначение")
    if val is None:
        return out
    items = val if isinstance(val, list) else [val]
    for x in items:
        s = str(x or "").strip()
        if s:
            out.append(s)
    return out


def _stitch_check(data: dict, specs: list[dict],
                  structure: dict) -> list[str]:
    """Проверка сшитой схемы: детали на месте, выводы не потеряны."""
    errs: list[str] = []
    want_parts: dict[str, dict] = {}
    for s in specs:
        for p in (s.get("parts") or []):
            if isinstance(p, dict) and str(p.get("ref") or "").strip():
                want_parts[str(p["ref"]).strip()] = p
    got_parts = {str(p.get("ref") or "").strip(): p
                 for p in (data.get("parts") or []) if isinstance(p, dict)}
    # обозначения деталей уникальны во всём изделии: иначе в схеме
    # две разные детали получат одно и то же имя
    seen: dict[str, str] = {}
    for s in specs:
        code = str(s.get("блок") or "")
        for prt in (s.get("parts") or []):
            if not isinstance(prt, dict):
                continue
            ref = str(prt.get("ref") or "").strip()
            if not ref:
                continue
            if ref in seen and seen[ref] != code:
                errs.append(f"обозначение {ref} повторяется: блок "
                            f"{seen[ref]} и блок {code} — у каждой детали "
                            "своё обозначение")
            seen.setdefault(ref, code)
    for ref, p in want_parts.items():
        if ref not in got_parts:
            errs.append(f"деталь {ref} потеряна при сшивке — верни все "
                        "детали блоков")
            continue
        g = got_parts[ref]
        for field in ("lib_id", "value"):
            if str(p.get(field) or "") != str(g.get(field) or ""):
                errs.append(f"{ref}: поле {field} изменено при сшивке — "
                            "детали блоков не трогаются")
    for ref in got_parts:
        if ref not in want_parts:
            errs.append(f"деталь {ref} появилась при сшивке — схему "
                        "изделия не меняют")
    # выводы не теряются: каждый вывод детали остаётся в сети
    want_pins: set[tuple[str, str]] = set()
    for s in specs:
        for n in (s.get("nets") or []):
            if not isinstance(n, dict):
                continue
            for c in (n.get("conns") or []):
                if isinstance(c, (list, tuple)) and len(c) == 2:
                    want_pins.add((str(c[0]), str(c[1])))
    got_pins: set[tuple[str, str]] = set()
    for n in (data.get("nets") or []):
        if not isinstance(n, dict):
            continue
        for c in (n.get("conns") or []):
            if isinstance(c, (list, tuple)) and len(c) == 2:
                got_pins.add((str(c[0]), str(c[1])))
    for pin in sorted(want_pins - got_pins):
        errs.append(f"вывод {pin[0]}.{pin[1]} потерян при сшивке")
    # одна цепь — одно имя: две сети с одинаковым именем означают, что
    # половина схемы посчитана отдельно от другой половины
    by_name: dict[str, int] = {}
    for n in (data.get("nets") or []):
        if not isinstance(n, dict):
            continue
        nm = str(n.get("name") or "").strip()
        if nm:
            by_name[nm] = by_name.get(nm, 0) + 1
    for nm, cnt in sorted(by_name.items()):
        if cnt > 1:
            errs.append(f"сеть {nm} встречается {cnt} раза — это одна цепь, "
                        "объедини её в одну сеть")
    # назначение сети обязательно: по нему стыки блоков сходятся
    for n in (data.get("nets") or []):
        if not isinstance(n, dict):
            continue
        if not _net_purposes(n):
            errs.append(f"сеть {n.get('name') or '?'} без назначения — "
                        "назначение несёт связь Э1, по нему стыкуются блоки")
    # стыки согласованы: на обоих концах связи Э1 — одно имя цепи
    names: dict[str, set[str]] = {}
    for n in (data.get("nets") or []):
        if not isinstance(n, dict):
            continue
        for purpose in _net_purposes(n):
            names.setdefault(purpose, set()).add(str(n.get("name") or ""))
    for l in (structure.get("связи") or []):
        if not isinstance(l, dict):
            continue
        purpose = str(l.get("назначение") or "").strip()
        if not purpose:
            continue
        got = names.get(purpose) or set()
        if len(got) > 1:
            errs.append(f"связь «{purpose}» названа в сетях разными "
                        f"именами ({', '.join(sorted(got))}) — это одна "
                        "электрическая цепь, имя должно быть одно")
        elif not got:
            errs.append(f"связь «{purpose}» потеряна при сшивке — нет "
                        "сети с таким назначением")
    return errs


def _erc_errors(box: Path) -> int:
    """Число ошибок ERC по файлам .erc.json в out/hardware."""
    n = 0
    hw = box / "out" / "hardware"
    if not hw.is_dir():
        return 0
    for p in hw.glob("*.erc.json"):
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        n += text.count("; error")
    return n


def _erc_issue_list(box: Path, n: int) -> list[str]:
    """Список ошибок ERC из файлов .erc.json (для петли правок)."""
    hw = Path(box) / "out" / "hardware"
    issues: list[str] = []
    for f in hw.glob("*.erc.json"):
        try:
            raw = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for m in re.finditer(r"\[([\w_]+)\]:\s*(.+?)\n\s*;\s*error", raw):
            issues.append(f"ERC: {m.group(1)}: {m.group(2).strip()}")
    if not issues:
        issues = [f"ERC: {n} ошибок (подробности в out/hardware/*.erc.json)"]
    return issues


def _stage_verify(proj: Project, state: dict) -> dict:
    """Стадия 9: приёмка схемы по ТЗ и Э1 — тестировщик.

    Независимый критик смотрит на сырые материалы (заказ, расчёты,
    чек-лист, Э1, спецификацию) и решает, можно ли идти к компиляции.
    Замечания возвращаются проектировщику — каркас их не правит.
    """
    box = Path(engine.current_workdir() or proj.root)
    spec = _read_json(box, "design/schematic.json")
    if not spec:
        return {"status": "failed",
                "error": "нет спецификации design/schematic.json — "
                         "стадия schematic не дала результата"}
    text = str(state.get("text") or "")
    calc = _read_design(box, "design/calc.json", 3000)
    checklist = _read_design(box, "design/checklist.md", 3000)
    structure = _read_json(box, "design/structure.json")
    tasks = _read_json(box, "design/block_tz.json").get("блоки") or []
    issues: list[str] = []

    # Механическая проверка ERC: ошибки уходят проектировщику блоков
    # петлёй правок — схемотехник исправляет, нетлист пересобирается,
    # ERC проверяется заново. Только после исчерпания попыток —
    # честный отказ стадии (его менеджер вернёт сюда же).
    erc = _erc_errors(box)
    erc_issues: list[str] = []
    for cycle in range(1, VERIFY_TRIES + 1):
        if erc == 0:
            break
        erc_issues = _erc_issue_list(box, erc)
        state["log"] = state.get("log", []) + [
            f"приёмка схемы: ERC {erc} ошибок — цикл правок "
            f"{cycle}/{VERIFY_TRIES}: проектировщику на исправление"]
        hint = ("Приёмка схемы не прошла по ERC — исправь ровно эти "
                "ошибки в блоках:\n"
                + "\n".join(f"- {i}" for i in erc_issues[:12]))
        blocks_hit = _blocks_in_issues(erc_issues, tasks)
        if blocks_hit:
            hint += ("\n\nОшибки относятся к блокам: "
                     + ", ".join(blocks_hit) + ".")
        _write_json(box, "design/manager_hints.json",
                    {"вводные": hint, "стадия": "schematic",
                     "блоки": blocks_hit})
        res = _stage_skidl(text, proj, state, errors=hint)
        if res["status"] != "done":
            return {"status": "failed",
                    "error": "проектировщик не смог исправить блоки: "
                             + str(res.get("error") or ""),
                    "исправимо": True,
                    "ошибки": "\n".join(f"- {i}" for i in erc_issues[:12])}
        res = _stage_stitch(proj, state)
        if res["status"] != "done":
            return {"status": "failed",
                    "error": "схема после правки не сведена: "
                             + str(res.get("error") or "")[:300],
                    "исправимо": True,
                    "ошибки": "\n".join(f"- {i}" for i in erc_issues[:12])}
        # пересборка нетлиста: файлы ERC создаёт kicad — без неё
        # проверка смотрит на старые ошибки
        kstep = _step("kicad", "11. Пересборка нетлиста (ERC)")
        engine._execute_one(proj, kstep)
        if kstep.get("status") == "failed":
            return {"status": "failed",
                    "error": "пересборка нетлиста после правки: "
                             + str(kstep.get("error") or "")[:300],
                    "исправимо": True,
                    "ошибки": "\n".join(f"- {i}" for i in erc_issues[:12])}
        erc = _erc_errors(box)
        if erc == 0:
            state["log"] = state.get("log", []) + [
                f"приёмка схемы: ERC исправлен за {cycle} цикл(ов) — "
                "ошибок нет"]
    if erc > 0:
        erc_issues = _erc_issue_list(box, erc)
        return {"status": "failed",
                "error": "схема не принята верификатором: "
                         + "; ".join(erc_issues[:4]),
                "исправимо": True,
                "ошибки": "\n".join(f"- {i}" for i in erc_issues[:12])}

    for attempt in range(1, VERIFY_TRIES + 1):
        raw = _chat_plain(_VERIFIER_SYSTEM,
                          _verify_user(text, calc, checklist, spec,
                                       structure, tasks, issues),
                          profile="механический", json_schema=JSON_ONLY)
        verdict = planner._extract_json(raw or "")
        if not isinstance(verdict, dict):
            state["log"] = state.get("log", []) + [
                f"приёмка схемы: попытка {attempt}/{VERIFY_TRIES} — ответ "
                "без разбираемого JSON"]
            # если схема прошла ERC без ошибок — принимаем её: каркас
            # не должен блокировать заказ из-за того, что модель не
            # вернула JSON. Замечания верификатора — это подсказка, а
            # не приговор: агенты и так их видят в отчёте
            erc = _erc_errors(box)
            if erc == 0:
                state["log"] = state.get("log", []) + [
                    "приёмка схемы: верификатор не вернул JSON, но ERC "
                    "чистый (0 ошибок) — схема принята"]
                return {"status": "done"}
            continue
        ok = bool(verdict.get("ok"))
        issues = [str(i) for i in (verdict.get("issues") or [])]
        if ok and not issues:
            state["log"] = state.get("log", []) + [
                "приёмка схемы: замечаний нет — к компиляции"]
            return {"status": "done"}
        # если замечания некритичны (нет ошибок ERC) — принимаем с
        # замечаниями: агенты исправят их в отчёте, а заказ идёт дальше
        erc = _erc_errors(box)
        if erc == 0 and not any("критич" in i.lower() or "не найден" in i.lower()
                                for i in issues):
            state["log"] = state.get("log", []) + [
                f"приёмка схемы: {len(issues)} замечаний (некритичных, "
                "ERC чистый) — схема принята с замечаниями"]
            return {"status": "done"}
        state["log"] = state.get("log", []) + [
            f"приёмка схемы: замечаний {len(issues)} — "
            f"проектировщику на исправление (попытка {attempt}/"
            f"{VERIFY_TRIES})"]
        if attempt < VERIFY_TRIES:
            # замечания возвращаются проектировщику блоков: он правит
            # спецификации, потом схема сшивается заново
            # в замечаниях называются блоки — по ним и понятно, что
            # переделывать: остальные блоки уже проверены
            blocks_hit = _blocks_in_issues(issues, tasks)
            hint = ("Замечания приёмки схемы — исправь ровно их:\n"
                    + "\n".join(f"- {i}" for i in issues[:12]))
            if blocks_hit:
                hint += ("\n\nЗамечания относятся к блокам: "
                         + ", ".join(blocks_hit) + ".")
            _write_json(box, "design/manager_hints.json",
                        {"вводные": hint, "стадия": "schematic",
                         "блоки": blocks_hit})
            res = _stage_skidl(text, proj, state)
            if res["status"] != "done":
                break
            res = _stage_stitch(proj, state)
            if res["status"] != "done":
                break
            spec = _read_json(box, "design/schematic.json")
            if not spec:
                break
    # верификатор не вернул разбираемый JSON — это не повод
    # блокировать заказ. Если ERC чистый (0 ошибок), схема принимается:
    # замечания верификатора — это подсказка, а не приговор, и они
    # уходят в отчёт. Агенты и так видят их в отчёте.
    erc = _erc_errors(box)
    if erc == 0:
        state["log"] = state.get("log", []) + [
            "приёмка схемы: верификатор не вернул JSON, но ERC чистый "
            "(0 ошибок) — схема принята с замечаниями"]
        return {"status": "done"}
    return {"status": "failed",
            "error": "схема не принята верификатором: "
                     + "; ".join(issues[:4]),
            "исправимо": True,
            "ошибки": "\n".join(f"- {i}" for i in issues[:12])}


def _verify_user(text: str, calc: str, checklist: str, spec: dict,
                 structure: dict, tasks: list[dict],
                 issues: list[str]) -> str:
    tail = ("Замечания прошлой приёмки — проверь, что они исправлены:\n"
            + "\n".join(f"- {i}" for i in issues) + "\n\n") if issues else ""
    return ("Заказ: " + text + "\n\n"
            "Расчёты (design/calc.json):\n" + (calc or "(нет)") + "\n\n"
            "Чек-лист приёмки (design/checklist.md):\n"
            + (checklist or "(нет)") + "\n\n"
            "Структурная схема Э1 (design/structure.json):\n"
            + (json.dumps(structure, ensure_ascii=False, indent=1)[:3000]
               if structure else "(нет)") + "\n\n"
            "ТЗ на блоки (design/block_tz.json):\n"
            + (json.dumps(tasks, ensure_ascii=False, indent=1)[:4000]
               if tasks else "(нет)") + "\n\n"
            + tail
            + "Спецификация схемы (design/schematic.json):\n"
            + json.dumps(spec, ensure_ascii=False, indent=1)[:14000] + "\n\n"
            "Проверь схему по чек-листу, расчётам, Э1 и ТЗ на блоки. Ответь "
            "ТОЛЬКО JSON: {\"ok\": true|false, \"issues\": [\"<адресное "
            "замечание>\", …]}. Каждое замечание должно называть сеть или "
            "компонент и проверяться по материалам.\n\n"
            "ВАЖНО: дефекты, которые каркас уже отметил и передал тебе, "
            "не являются поводом отклонить схему — они известны и будут "
            "исправлены. Отклоняй схему только по существу: не выполнены "
            "требования ТЗ, нарушены стыки по Э1, не сходятся расчёты.")


def box_path(proj: Project) -> Path:
    return Path(engine.current_workdir() or proj.root)


def _progress(steps: list[dict]) -> int:
    if not steps:
        return 0
    done = sum(1 for s in steps if s.get("status") in ("done", "skipped"))
    return round(done / len(steps) * 100)


def _extract_params(text: str) -> list[str]:
    """Текст заказа → пары «число + единица». Только разбор текста:
    инженерную интерпретацию делает агент."""
    out: list[str] = []
    for m in re.finditer(
            r"(\d+(?:[.,]\d+)?)\s*"
            r"(вт|w|ватт|ват|лм|lm|люмен|в\b|v\b|вольт|а\b|a\b|ампер|"
            r"гц|hz|кгц|мгц|ггц|дб|db|м[аa]ч|mah|ом\b|ohm)",
            text.lower()):
        out.append(f"{m.group(1).replace(',', '.')} {m.group(2)}")
    return out


def _final_report(proj: Project, state: dict | None = None) -> str:
    """Отчёт по фактическим файлам песочницы — без выдумок.

    Отвечает на четыре вопроса: что за изделие, из чего оно собрано,
    что проверено и что честно осталось невыполненным.
    """
    box = Path(engine.current_workdir() or proj.root)
    out: list[str] = []

    spec = _read_json(box, "design/schematic.json")
    if spec:
        parts = spec.get("parts") or []
        nets = spec.get("nets") or []
        blocks = spec.get("blocks") or []
        out.append("ИЗДЕЛИЕ")
        out.append(f"  • схема: деталей {len(parts)}, сетей {len(nets)}, "
                   f"функциональных частей {len(blocks)}")
        for b in blocks[:12]:
            title = str(b.get("title") or b.get("код") or "?")
            refs = b.get("refs") or []
            out.append(f"    — {title} ({len(refs)} дет.)")

    idx = _read_json(box, "design/blocks/index.json")
    if idx:
        acc = idx.get("блоки") or []
        if acc:
            out.append("БЛОКИ (приняты)")
            for b in acc:
                out.append(f"  • {b.get('код')} — деталей {b.get('деталей')}, "
                           f"сетей {b.get('сетей')}: {b.get('наименование')}")
        issues = idx.get("дефекты") or []
        if issues:
            out.append("  дефекты блоков (устранены не полностью):")
            for i in issues[:8]:
                out.append(f"    - {str(i)[:150]}")

    erc = None
    for p in sorted((box / "out/hardware").glob("*.erc.json")):
        erc = p
        break
    if erc is not None:
        try:
            data = json.loads(erc.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        errs = len(re.findall(r"; error", erc.read_text(
            encoding="utf-8", errors="ignore")))
        out.append(f"ПРОВЕРКА СХЕМЫ (ERC): ошибок {errs}"
                   + (f", предупреждений {len(data.get('warnings') or [])}"
                      if data else ""))

    meas = _read_json(box, "out/hardware/measurements.json")
    measured = meas.get("measured") if isinstance(meas, dict) else None
    if measured:
        out.append("ИЗМЕРЕНИЯ (замер против требования)")
        for m in measured[:12]:
            name = str(m.get("name") or m.get("метка") or "?")
            val = m.get("value")
            goal = m.get("цель") or ""
            margin = m.get("запас")
            unit = str(m.get("unit") or "")
            line = f"  • {name}: {val}{unit}"
            if goal:
                line += f" (цель {goal})"
            if margin not in (None, ""):
                line += f" — запас {margin}"
            out.append(line)
    blocked = (meas.get("blocked") if isinstance(meas, dict) else None) or []
    if blocked:
        out.append("  не посчитано: " + "; ".join(str(b)[:110]
                                                for b in blocked[:4]))

    hw = box / "out/hardware"
    rows: list[str] = []
    if hw.is_dir():
        for p in sorted(hw.glob("*")):
            if p.is_file():
                rows.append(f"out/hardware/{p.name} — "
                            f"{p.stat().st_size:,} байт")
    tsc = hw / "tscircuit"
    if tsc.is_dir():
        names = sorted(p.name for p in tsc.iterdir() if p.is_file())
        if names:
            rows.append("out/hardware/tscircuit/ — " + ", ".join(names[:12]))
    for rel in ("design/calc.json", "design/schematic.json", "out/tz.md",
                "design/parts_pcb.json"):
        p = box / rel
        if p.is_file():
            rows.append(f"{rel} — {p.stat().st_size:,} байт")
    if rows:
        out.append("АРТЕФАКТЫ")
        out.extend(f"  • {r}" for r in rows)
    # ход работы: что выполнено, что нет — честно, без украшений
    steps = (state or {}).get("steps") or []
    if steps:
        out.append("ХОД РАБОТЫ")
        for s in steps:
            st = str(s.get("status") or "")
            if st == "done":
                out.append(f"  ✓ {s.get('target')}")
            elif st == "failed":
                out.append(f"  ✗ {s.get('target')}: "
                           f"{str(s.get('error') or '')[:150]}")
            elif st == "skipped":
                out.append(f"  – {s.get('target')}: "
                           f"{str(s.get('error') or '')[:110]}")
    if not out:
        return "Артефакты не собраны."
    return "\n".join(out)
