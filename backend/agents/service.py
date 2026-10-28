"""Сервис заказов: хранение, запуск и выдача в формате интерфейса.

Между графом и HTTP-мостом. Граф возвращает своё состояние, а
интерфейс ждёт поля, которые он уже умеет рисовать. Здесь эти две формы
сводятся, чтобы фронтенд не пришлось переписывать.

Соответствие полей:

    граф (types.Step)              интерфейс
    ---------------------------    ---------------------------
    target                         target
    title                          title
    agent                          agent_id
    status                         status
    exit_code, duration_s, error   exit_code, duration_s, error
    log_tail                       log_tail
    started_at / finished_at       started_at / finished_at
"""

from __future__ import annotations

import datetime
import os
import json
import re
from pathlib import Path
import shutil
import threading
import time
import traceback
from typing import Any, Callable

from . import engine, sandbox

from .runner import available as runners_available
from .profile import load as load_project
from .engine import have_langgraph
from .types import Project, new_state, now_iso, progress_of

#: работать в песочнице или прямо в проекте.
#: По умолчанию — в песочнице: исходники проекта являются опорой работы
#: агентов, и заказ не должен иметь возможности их переписать.
USE_SANDBOX = os.environ.get("KR_SANDBOX", "1").strip().lower() not in (
    "0", "false", "no", "off")

#: забирать ли результаты из песочницы в основной out/
TAKE_ARTIFACTS = os.environ.get("KR_TAKE_ARTIFACTS", "1").strip().lower() not in (
    "0", "false", "no", "off")

#: сколько песочниц хранить, прежде чем убрать старые
KEEP_SANDBOXES = int(os.environ.get("KR_KEEP_SANDBOXES", "10"))

#: закрывать ли основной проект на запись на время заказа.
#: Песочница изолирует по файлам; это — по правам: пока проект закрыт,
#: агент не может изменить исходник даже извне своей пасочницы.
LOCK_PROJECT = os.environ.get("KR_LOCK_PROJECT", "1").strip().lower() not in (
    "0", "false", "no", "off")

#: сверять ли хеши исходников до и после заказа
CHECK_INTEGRITY = os.environ.get("KR_CHECK_INTEGRITY", "1").strip().lower() \
    not in ("0", "false", "no", "off")

#: заказы в памяти; сколько хранить
ORDERS: dict[str, dict] = {}
MAX_ORDERS = 60
_LOCK = threading.Lock()


# ==========================================================================
#  Внешняя форма
# ==========================================================================
def _step_view(s: Any) -> dict:
    """Шаг графа -> шаг интерфейса.

    Имена полей в графе и в интерфейсе различаются исторически:
    граф говорит ``agent``, интерфейс ждёт ``agent_id``. Переименование
    делается здесь, один раз, чтобы ни граф, ни компонент не пришлось
    трогать.
    """
    d = s.as_dict() if hasattr(s, "as_dict") else dict(s)
    d["agent_id"] = d.get("agent", "agent.build")
    d["kind"] = "make"
    d.setdefault("title", d.get("target", ""))
    d.setdefault("job_id", None)
    if d.get("log_tail") is None:
        d["log_tail"] = d.get("log") or []
    if isinstance(d.get("log_tail"), str):
        d["log_tail"] = d["log_tail"].splitlines()
    return d


def _order_view(o: dict) -> dict:
    """Заказ в том виде, в котором его читает интерфейс."""
    steps = [_step_view(s) for s in o.get("steps", [])]
    return {
        "id": o["id"],
        "text": o["text"],
        "status": o.get("status", "queued"),
        "intent": o.get("intent", ""),
        "planner": o.get("planner", "rules"),
        "progress": o.get("progress", 0),
        "verdict": o.get("verdict", ""),
        "question": o.get("question", ""),
        "note": o.get("note", ""),
        "project": o.get("project", ""),
        "created_at": o["created_at"],
        "finished_at": o.get("finished_at"),
        "steps": steps,
        "report": o.get("report", ""),
        "logs": o.get("logs", []),
        "engine": "langgraph" if have_langgraph() else "plain",
        "sandbox": o.get("sandbox"),
        "sandboxed": bool(o.get("sandbox")),
        "integrity": o.get("integrity"),
        "archive": o.get("archive"),
    }


#: заказы переживают перезапуск моста — иначе после рестарта нечего
#: продолжать: список пуст, и пауза/продолжение теряют смысл
_ORDERS_FILE = ".krystal/orders.json"


def _root_dir() -> Path:
    """Корень, где лежат заказы (там же бутстрап моста держит проекты)."""
    return Path(os.environ.get("KR_PROJECT_ROOT")
                or Path(__file__).resolve().parents[2])


def _persist_orders() -> None:
    """Записать заказы на диск. Молча: это страховка, а не результат."""
    try:
        p = _root_dir() / _ORDERS_FILE
        p.parent.mkdir(parents=True, exist_ok=True)
        with _LOCK:
            data = {k: {kk: vv for kk, vv in v.items() if kk != "logs"}
                    for k, v in ORDERS.items()}
        p.write_text(json.dumps(data, ensure_ascii=False, indent=1,
                                default=str), encoding="utf-8")
    except OSError:
        pass


def load_orders() -> int:
    """Поднять заказы после перезапуска. Возвращает, сколько поднято."""
    with _LOCK:
        return _load_orders_locked()


def _load_orders_locked() -> int:
    """То же без захвата _LOCK: вызывается изнутри уже запертых мест.

    _LOCK — обычный Lock, он не реентрантный: взять его дважды в одной
    нитке означает встать насмерть. Поэтому вариант «с замком» и вариант
    «без замка» раздельны.
    """
    p = _root_dir() / _ORDERS_FILE
    if not p.is_file():
        return 0
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return 0
    if not isinstance(data, dict):
        return 0
    n = 0
    for oid, o in data.items():
        if not isinstance(o, dict) or not o.get("text"):
            continue
        o.setdefault("logs", [])
        o.setdefault("steps", [])
        # заказ, оставшийся «работающим», после рестарта не работает:
        # честно помечаем, чтобы продолжение шло из его точки отсчёта
        if o.get("status") in ("running", "queued"):
            o["status"] = "interrupted"
            o["note"] = "прерван перезапуском — продолжите с места остановки"
        ORDERS[oid] = o
        n += 1
    return n


def _order(order_id: str) -> dict | None:
    """Заказ из памяти; если мост перезапустился — с диска.

    Без этого продолжение заказа после перезапуска моста не работает:
    заказ лежит в файле, но в памяти его нет.
    """
    with _LOCK:
        o = ORDERS.get(order_id)
        if o is None:
            _load_orders_locked()
            o = ORDERS.get(order_id)
    return o


def get(order_id: str) -> dict | None:
    o = _order(order_id)
    return _order_view(o) if o else None


def list_orders(limit: int = 20) -> list[dict]:
    with _LOCK:
        if not ORDERS:
            # мост перезапустился — поднимаем заказы с диска, чтобы
            # пауза/продолжение нашли свою точку отсчёта
            _load_orders_locked()
        items = sorted(ORDERS.values(), key=lambda o: o["created_at"],
                       reverse=True)[:limit]
    return [{"id": o["id"], "text": o["text"], "status": o.get("status"),
             "intent": o.get("intent"), "planner": o.get("planner"),
             "progress": o.get("progress", 0), "verdict": o.get("verdict", ""),
             "created_at": o["created_at"], "finished_at": o.get("finished_at"),
             "steps_total": len(o.get("steps", []))}
            for o in items]


def _trim() -> None:
    """Не растим память бесконечно."""
    if len(ORDERS) <= MAX_ORDERS:
        return
    oldest = sorted(ORDERS.values(), key=lambda o: o["created_at"])
    for o in oldest[:len(ORDERS) - MAX_ORDERS]:
        ORDERS.pop(o["id"], None)


# ==========================================================================
#  Приём заказа
# ==========================================================================
def submit(text: str, root: str = ".", profile: str = "",
           project_id: str | None = None) -> dict:
    """Принять заказ, запустить в фоне, вернуть начальное состояние."""
    text = (text or "").strip()
    proj = load_project(root, profile=profile)
    watchdog_start()          # надзор: зависание объявляется само

    order = {
        "id": new_state(text)["order_id"],
        "text": text,
        "project": proj.name,
        "project_id": project_id,
        "status": "queued",
        "intent": "",
        "planner": "rules",
        "progress": 0,
        "verdict": "",
        "question": "",
        "note": "",
        "created_at": now_iso(),
        "finished_at": None,
        "steps": [],
        "report": "",
        "logs": [],
    }
    with _LOCK:
        ORDERS[order["id"]] = order
        _trim()

    threading.Thread(target=_run, args=(order["id"], proj), daemon=True).start()
    return _order_view(order)



def _archive_order_outputs(order_id: str, proj: Any, order: dict) -> None:
    """Сложить результат заказа в отдельную папку по проекту/заказу.

    Архивация нужна, чтобы каждый публичный заказ формировал свой комплект
    выходных (чертижи, 3D-модели, Gerber и т. д.), а не делил с остальными
    каталог `out/`. Ошибки архивации не отменяют успешный заказ, но
    пишутся в журнал.
    """
    root = Path(proj.root)
    slug = re.sub(r"[^\w.-]+", "-", str(proj.name or "").lower()).strip("-") or "project"
    archive_root = root / "out" / "archive" / slug
    target = archive_root / order_id
    src = root / "out"
    try:
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True, exist_ok=True)
        for item in src.iterdir():
            if item.name == "archive":
                continue
            dst = target / item.name
            if item.is_dir():
                shutil.copytree(item, dst, dirs_exist_ok=True)
            elif item.is_file():
                shutil.copy2(item, dst)
        # список файлов для фронтенда
        entries = []
        for f in sorted(target.rglob("*")):
            if not f.is_file():
                continue
            rel = f.relative_to(target).as_posix()
            try:
                size = f.stat().st_size
            except OSError:
                size = 0
            def _human(n: int) -> str:
                for unit in ("B", "KB", "MB"):
                    if n < 1024:
                        return f"{n} {unit}"
                    n //= 1024
                return f"{n} GB"
            entries.append({
                "name": f.name, "path": rel, "size": size,
                "size_h": _human(size), "ext": f.suffix.lower(),
                "url": f"/api/artifact/out/archive/{slug}/{order_id}/{rel}",
            })
        manifest = {
            "order_id": order_id, "project": proj.name, "count": len(entries),
            "files": entries,
        }
        (target / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        order["archive"] = f"out/archive/{slug}/{order_id}"
        _log(order, f"архив артефактов: {len(entries)} файлов → out/archive/{slug}/{order_id}")
    except Exception as exc:                                    # noqa: BLE001
        _log(order, f"архив не создан: {exc}")


def _run(order_id: str, proj: Project) -> None:
    """Прогнать заказ и записать результат обратно в хранилище.

    Заказ выполняется в собственной песочнице: проект — опора работы
    агентов, и он не должен быть переписан тем, что в нём исполняется.
    """
    with _LOCK:
        order = ORDERS.get(order_id)
    if order is None:
        return
    order["status"] = "running"
    _mark_progress(order)

    try:
        from agents import engine as _eng
        _eng.set_order(order)
        _eng.set_active_state(None)
    except Exception:                                    # noqa: BLE001
        pass

    # До создания песочницы гарантируем, что проект не висит залоченным
    # (например, после падения предыдущего заказа) — иначе cp -a упрётся
    # в каталоги без права записи.
    try:
        sandbox.restore_if_locked(proj.root)
    except Exception:                                    # noqa: BLE001
        pass

    box = None
    if USE_SANDBOX:
        try:
            box = str(sandbox.create(order_id, proj.root))
            order["sandbox"] = box
            _apply_project_makefile(project_id, box)
        except Exception as exc:                                # noqa: BLE001
            # Откатываться к корню проекта нельзя. Именно этим путём
            # заказ однажды выполнил distclean и снёс out/, .venv и
            # node_modules: песочница не создалась, а команда ушла в
            # проект. Лучше честный отказ, чем чужой заказ, который
            # стирает опору.
            order["status"] = "failed"
            _log(order, f"песочница не создана: {exc}")
            order["report"] = (
                "Заказ остановлен: не удалось создать изолированную "
                f"песочницу.\n\n{exc}\n\n"
                "Выполнять в корне проекта нельзя — исходники проекта "
                "являются опорой работы агентов, и заказ не имеет "
                "права их переписывать.\n\n"
                "Что делать:\n"
                "  * проверить место в work/orders — там должны хватать "
                "прав и места;\n"
                "  * освободить место на диске;\n"
                "  * запустить заказ вручную, минуя агентов.")
            order["finished_at"] = now_iso()
            return

    # Песочница готова — запираем проект. Дальше агент не сможет
    # изменить исходник, даже если выйдет за пределы своей папки.
    before = (sandbox.source_manifest(proj.root)
              if CHECK_INTEGRITY else {})

    locked = False
    if LOCK_PROJECT:
        try:
            sandbox.lock(proj.root)
            locked = True
            _log(order, "проект закрыт на запись до конца заказа")
        except Exception as exc:                                # noqa: BLE001
            _log(order, f"проект не закрыт ({exc}) — защита ограничена песочницей")

    engine.set_workdir(box)
    try:
        res = (engine.run(order["text"], proj, order_id) if have_langgraph()
               else engine.run_plain(order["text"], proj, order_id))
    except Exception as exc:                                    # noqa: BLE001
        order["status"] = "failed"
        order["report"] = (f"Заказ не выполнен: {exc}\n\n"
                           f"{traceback.format_exc()[-600:]}")
        order["finished_at"] = now_iso()
        engine.set_workdir(None)
        if locked:
            sandbox.unlock(proj.root)
        return
    finally:
        engine.set_workdir(None)
        try:
            engine.set_order(None)
            engine.set_active_state(None)
        except Exception:                                # noqa: BLE001
            pass
        if locked:
            # артефакты забираются позже, поэтому здесь только права
            sandbox.unlock(proj.root)

    with _LOCK:
        order.update({
            "status": res.get("status", "done"),
            "intent": res.get("intent", ""),
            "planner": res.get("planner", "rules"),
            "progress": res.get("progress", 0) or progress_of(res.get("steps", [])),
            "verdict": res.get("verdict", ""),
            "question": res.get("question", ""),
            "note": res.get("note", ""),
            "steps": res.get("steps", []),
            "report": res.get("report", ""),
            "logs": list(order.get("log") or []) + list(res.get("log", [])),
            "finished_at": res.get("finished_at") or now_iso(),
        })

    # Артефакты забираются наружу только при успехе: неудачный заказ
    # мог оставить в out/ обрывки, и молчаливый перенос испортил бы
    # отчётность предыдущих сборок.
    # Сверка целостности. Запрет разрушающих целей и блокировка прав —
    # меры предупреждающие; «даже случайно» ими не закрывается, поэтому
    # расхождение обнаруживается здесь и попадает в отчёт.

    # ============== МУЛЬТИ-АГЕНТСКАЯ АРХИТЕКТУРА ==============
    # Анализ заказа и подбор агентов на основе типового анализа текста
    order_text = order.get("text", "").lower()
    proj_name = order.get("project", "").lower()

    # --- Вспомогательные функции ---

    def _match_agents_for_project(order_text):
        """Подбор агентов на основе ключевых слов в тексте заказа.

        Матч по содержимому запроса: агенты включаются по ключам,
        которые действительно присутствуют в запросе. Никаких
        захардкоженных по проектам.
        """
        agent_keywords = {
            "chief": ["chief", "главный", "начальник", "управление", "координация"],
            "consultant": ["consultant", "консультант", "уточнение", "вопрос", "интерфейс"],
            "theorist": ["theorist", "теоретик", "математика", "физика", "модели", "расчет", "анализ"],
            "designer": ["designer", "конструктор", "конструкция", "проектирование", "чертеж", "дизайн"],
            "analyst": ["analyst", "аналитик", "данные", "статистика", "трассировка", "исследование"],
            "tester": ["tester", "испытатель", "тестирование", "надежность", "валидация", "проверка"],
            "optimizer": ["optimizer", "оптимизация", "эффективность", "усиление", "настройка"]
        }

        matched_agents = []
        for agent_key, keywords in agent_keywords.items():
            if any(kw in order_text for kw in keywords):
                matched_agents.append((agent_key, keywords))

        return matched_agents

    # --- Выполнение ---

    # Подбор агентов — по тексту заказа, без привязки к типу проекта
    matched_agents = _match_agents_for_project(order_text)

    # Генерируем роль-specific техническое задание (ТЗ) и логи агентов
    if matched_agents:
        agent_descriptions = {
            "chief": "Главный инженер координирует все аспекты проекта, обеспечивает стратегическое планирование и распределение ресурсов.",
            "consultant": "Главный наставник: объясняет, как устроен весь цикл разработки, читает проект «Кристалл-Радио» как эталон структуры ТЗ и требований, и обучает остальных агентов работать пошагово. Консультант определяет, какая детализация требуется (размеры разделов, уровни подпунктов), и следит, чтобы агенты не допустили упрощений относительно эталона.",
            "theorist": "Теоретик разрабатывает математические модели и теоретические основы проекта, проводит анализ отклонений и физические расчеты.",
            "designer": "Конструктор разрабатывает структурную схему, чертежи и макеты изделия с учетом конструктивных ограничений и материалов.",
            "analyst": "Аналитик обрабатывает telemetry данные, выявляет паттерны отклонений и формирует отчеты по надежности и производительности.",
            "tester": "Испытатель разрабатывает протоколы стресс-тестирования, проверяет надежность и валидирует результаты проектирования на соответствие требованиям.",
            "optimizer": "Оптимизатор настраивает параметры системы для максимальной эффективности, минимизирует потери энергии и усиливает производительность изделия."
        }
        
        # Добавляем заметки о matched агентах в лог заказа
        # Динамический порядок этапов (consultant → theorist → designer → analyst → ...)
        cycle_order = ["consultant", "theorist", "designer", "analyst", "tester", "optimizer", "chief"]
        keys = []
        for at in matched_agents:
            keys.append(at[0] if isinstance(at, tuple) else at)
        # упорядочиваем по cycle_order, при этом предполагаемый консультант — первым всегда
        keys = list(dict.fromkeys(keys))
        keys.sort(key=lambda a: cycle_order.index(a) if a in cycle_order else 999)
        phase_names = {
            "consultant": "этап 1 — уточнение требований",
            "theorist": "этап 2 — математическое моделирование",
            "designer": "этап 3 — проектирование схем/чертежей",
            "analyst": "этап 4 — анализ данных и верификация",
            "tester": "этап 5 — испытания и валидация (make test, ERC/DRC)",
            "optimizer": "этап 6 — оптимизация (thermals / оптика / freq)",
            "chief": "стратегия проекта",
        }
        for idx, agent_key in enumerate(keys, start=1):
            _log(order, f"🤖 Агент [выполняет {phase_names.get(agent_key, agent_key)}] :: " f"{agent_descriptions.get(agent_key, '')}")
    
    # Динамический анализ физики по содержанию заказа.
    # Никаких заготовленных фраз на тип устройства: область определяется
    # по ключевым словам, а параметры берутся ИЗ ТЕКСТА ЗАКАЗА числами.
    ot = order.get("text", "").lower()
    _log(order, "🔬 Анализ заказа: извлечение параметров и областей физики")

    # --- области физики: доменный классификатор (слова области, не прибора)
    DOMAINS = (
        ("оптический",  ("свет", "светодиод", "люмен", "лм", "lux", "оптик",
                         "illumination", "лампа", "освет")),
        ("акустический", ("акуст", "звук", "динамик", "колонк", "speaker",
                          "громкоговор", "шум", "резонанс", "твитер")),
        ("тепловой",    ("тепл", "терм", "нагрев", "охлажд", " thermal",
                         " temp", "радиатор", "кулер")),
        ("радиочастотный", ("антенн", "радио", "rf", "модуляц", "вч",
                            "свч", "частот", "приёмник", "передатчик")),
    )
    domains = [d for d, kws in DOMAINS if any(k in ot for k in kws)]
    if domains:
        for d in domains:
            _log(order, f"   • область расчёта: {d}")
    else:
        _log(order, "   • универсальный инженерный расчёт (область не "
                     "уточнена — полная инженерная проверка в make test)")

    # --- универсальное извлечение числовых параметров из текста заказа
    _PARAM_PATTERNS: tuple[tuple[str, str], ...] = (
        ("мощность",        r"(\d+(?:[.,]\d+)?)\s*(?:вт|w|ватт|ват)"),
        ("световой поток",  r"(\d+(?:[.,]\d+)?)\s*(?:лм|lm|люмен)"),
        ("напряжение",      r"(\d+(?:[.,]\d+)?)\s*(?:в\b|v\b|вольт)"),
        ("сила тока",       r"(\d+(?:[.,]\d+)?)\s*(?:а\b|a\b|ампер|мк|мкм)"),
        ("частота",         r"(\d+(?:[.,]\d+)?)\s*(?:гц|hz|кгц|мгц|ггц)"),
        ("сопротивление",   r"(\d+(?:[.,]\d+)?)\s*(?:ом\b|ohm|\bom\b)"),
        ("звуковое давление", r"(\d+(?:[.,]\d+)?)\s*(?:дб\s?spl|db spl|дб)"),
        ("защита",          r"ip\s?(6[0-9]|5[0-9]|[0-9]{2})"),
        ("ёмкость batteries", r"(\d+(?:[.,]\d+)?)\s*(?:м[аa]ч|mah)"),
    )
    seen_params: set[str] = set()
    params_found = []
    for label, pat in _PARAM_PATTERNS:
        m = re.search(pat, ot)
        if m:
            raw = m.group(1).replace(",", ".")
            params_found.append(f"{label}={raw}")
            seen_params.add(label)
    if params_found:
        _log(order, "   • параметры из заказа: " + ", ".join(params_found))
    else:
        _log(order, "   • числовые параметры не указаны — агент рассчитает "
                     "из эталонной структуры ТЗ")

    _log(order, "   • расчёты делает агент-теоретик (design/calc.json); "
             "схему — дизайнер (design/schematic.json); приём замысла и "
             "решение о микроконтроллере — консультант "
             "(design/acceptance.json); сборку и ERC — скрипты")

    # Артефакты забираются наружу только при успехе
    if CHECK_INTEGRITY and before:
        verdict = sandbox.verify_intact(before, proj.root)
        order["integrity"] = verdict
        if verdict["intact"]:
            _log(order, "исходники не изменились")
        else:
            what = []
            if verdict["changed"]:
                what.append(f"изменено: {', '.join(verdict['changed'][:5])}")
            if verdict["removed"]:
                what.append(f"удалено: {', '.join(verdict['removed'][:5])}")
            _log(order, "ВНИМАНИЕ: " + "; ".join(what))
            order["report"] = ("\n\n!! ИСХОДНИКИ ПРОЕКТА ИЗМЕНЕНЫ !!\n"
                               + "\n".join(what)
                               + "\n\nПроверьте состояние репозитория.")

    if box and order["status"] in ("done", "partial") and TAKE_ARTIFACTS:
        try:
            brought = sandbox.collect_artifacts(order_id, proj.root)
            if brought:
                _log(order, f"взято артефактов из песочницы: {len(brought)}")
        except Exception as exc:                                # noqa: BLE001
            _log(order, f"артефакты не перенесены: {exc}")

    if order["status"] in ("done", "partial"):
        try:
            _archive_order_outputs(order_id, proj, order)
        except Exception as exc:                            # noqa: BLE001
            _log(order, f"архив не создан: {exc}")

def cancel(order_id: str) -> bool:
    """Пометить заказ отменённым.

    Процессы make при этом не прерываются: они запускаются в своих
    группах и не отменяются безопасно. Заказ просто перестаёт
    показываться как активный — результат, если он появится, не
    перезапишет отмену.
    """
    o = _order(order_id)
    if not o or o.get("status") in ("done", "failed", "partial"):
        return False
    with _LOCK:
        o["status"] = "cancelled"
        o["finished_at"] = now_iso()
        return True


# ==========================================================================
#  Прерывание и возобновление
# ==========================================================================
def pause(order_id: str) -> bool:
    """Пауза: конвейер дорабатывает текущую стадию и не начинает следующую."""
    from . import stages                                      # поздний импорт
    with _LOCK:
        o = ORDERS.get(order_id)
    if not o or o.get("status") != "running":
        return False
    stages.request_pause()
    with _LOCK:
        o["note"] = "пауза по запросу оператора"
    return True


def continue_order(order_id: str, root: str = ".",
                   answer: str = "") -> dict:
    """Продолжить заказ с места остановки (в фоне, как при подаче заказа).

    Уже выполненные стадии берутся из design/_pipeline.json и повторно
    НЕ выполняются: агентам не приходится заново переписывать ТЗ, расчёты
    и схему. Упавшие и не начатые стадии проходят заново — иначе заказ
    не сдвинется с места, где остановился.
    """
    with _LOCK:
        o = ORDERS.get(order_id)
        if o is None:
            # мост перезапустился — заказ лежит на диске
            _load_orders_locked()
            o = ORDERS.get(order_id)
    if o is None:
        return {"error": "unknown order", "id": order_id}
    box = _existing_sandbox(order_id, o, root) or root
    if not str(o.get("text") or ""):
        return {"error": "у заказа нет текста — продолжать нечего"}
    o["status"] = "running"
    o["note"] = "продолжение с места остановки"
    try:
        from . import sandbox as SB
        updated = SB.sync(root, box)
        updated += _apply_project_makefile(o.get("project_id"), box)
        if updated:
            o["logs"] = (o.get("logs") or []) + [
                "песочница: обновлены инструменты (" +
                ", ".join(updated[:6]) + ")"]
    except Exception as exc:                              # noqa: BLE001
        o["logs"] = (o.get("logs") or []) + [
            f"песочница: не удалось обновить инструменты: {exc}"]
    threading.Thread(target=_run_continue,
                     args=(order_id, box, answer), daemon=True).start()
    return {"ok": True, "id": order_id, "status": "running",
            "note": o["note"],
            "sandbox": box}


def _run_continue(order_id: str, box: str, answer: str = "") -> None:
    """Фоновое продолжение: конвейер идёт, запрос уже вернул оператору."""
    from . import stages                                      # поздний импорт
    with _LOCK:
        o = ORDERS.get(order_id)
    if o is None:
        return
    text = str(o.get("text") or "")
    engine.set_workdir(box)
    try:
        # заказ должен быть виден движку: иначе состояние стадий не
        # публикуется наружу и заказ кажется «висящим» без прогресса
        engine.set_order(o)
        engine.set_active_state(None)
        proj = engine.load_profile(box)
        out = stages.continue_pipeline(text, proj, order_id=order_id,
                                      answer=answer)
    except Exception as exc:                                  # noqa: BLE001
        # след ошибки — в лог заказа: иначе тихий обрыв конвейера
        # выглядит как «ничего не происходит»
        import traceback
        tb = traceback.format_exc()
        with _LOCK:
            o["status"] = "failed"
            o["note"] = f"продолжение не удалось: {exc}"[:200]
            o["logs"] = (o.get("logs") or []) + [tb[-1500:]]
        return
    finally:
        engine.set_workdir(None)
    with _LOCK:
        o.update({k: out.get(k) for k in
                  ("status", "progress", "note", "report", "steps", "log",
                   "решение_менеджера")
                  if k in out})
        o["finished_at"] = out.get("finished_at")


def question_of(order_id: str) -> str:
    """Вопрос, на котором граф стоит, — для интерфейса."""
    o = get(order_id)
    return (o or {}).get("question", "")


def resume(order_id: str, answer: str, root: str = ".") -> dict:
    """Продолжить прерванный заказ после ответа оператора.

    Заказ ведётся конвейером стадий, и он помнит, что уже выполнено
    (design/_pipeline.json в песочнице заказа). Поэтому продолжение
    идёт с места остановки: уже написанные ТЗ, расчёты и схема не
    переписываются заново, а ответ оператора уходит агенту как вводные.

    Если песочницы нет (заказ не начинали) — заказ запускается целиком.
    """
    o = _order(order_id)
    if o is None:
        return {"error": "unknown order", "id": order_id}
    box = _existing_sandbox(order_id, o, root)
    if not box:
        # песочницы нет — запускаем заказ заново
        return submit(str(o.get("text") or ""), root=root,
                      project_id=o.get("project_id"))
    o["status"] = "running"
    o["note"] = "продолжение с места остановки"
    # инструменты песочницы синхронизируются с проектом: скрипты мы
    # правим по ходу работы, и заказ должен считать на свежей версии
    try:
        from . import sandbox as SB
        updated = SB.sync(root, box)
        updated += _apply_project_makefile(o.get("project_id"), box)
        if updated:
            o["logs"] = (o.get("logs") or []) + [
                "песочница: обновлены инструменты (" +
                ", ".join(updated[:6]) + ")"]
    except Exception as exc:                              # noqa: BLE001
        o["logs"] = (o.get("logs") or []) + [
            f"песочница: не удалось обновить инструменты: {exc}"]
    threading.Thread(target=_run_continue,
                     args=(order_id, box, answer), daemon=True).start()
    return {"ok": True, "id": order_id, "status": "running",
            "note": o["note"], "sandbox": box}


def _project_dir(project_id: str | None) -> Path | None:
    """Каталог проекта заказа (там лежит его Makefile с целями сборки)."""
    pid = str(project_id or "").strip()
    if not pid:
        return None
    base = Path(__file__).resolve().parents[2] / ".krystal" / "projects"
    d = base / pid
    return d if d.is_dir() else None


def _apply_project_makefile(project_id: str | None, box: str) -> list[str]:
    """Положить в песочницу Makefile проекта заказа.

    Сборка заказа идёт по целям проекта (tz, kicad, spice, sim, gost,
    all, test). Если в песочнице окажется Makefile чужого проекта, эти
    цели просто не найдутся — и сборка не запустится.
    """
    src_dir = _project_dir(project_id)
    if src_dir is None:
        return []
    src = src_dir / "Makefile"
    if not src.is_file():
        return []
    dst = Path(box) / "Makefile"
    try:
        if dst.is_file() and dst.read_bytes() == src.read_bytes():
            return []
        shutil.copy2(src, dst)
    except OSError:
        return []
    return ["Makefile"]


def _existing_sandbox(order_id: str, order: dict, root: str) -> str | None:
    """Песочница заказа, если она есть.

    Возобновление обязано продолжить работу в той же папке: чекпоинт
    графа лежит по идентификатору заказа, а результаты прежних шагов
    остались именно там.
    """
    if not USE_SANDBOX:
        return None
    recorded = order.get("sandbox")
    if recorded and Path(recorded).is_dir():
        return recorded
    base = Path(root).resolve()
    # песочница живёт рядом с проектом заказа: её путь строится от
    # project_id, а не от корня репозитория
    candidates = [base / "work" / "orders" / order_id]
    project_id = str(order.get("project_id") or "").strip()
    if project_id:
        candidates.append(base / ".krystal" / "projects" / project_id
                         / "work" / "orders" / order_id)
    for candidate in candidates:
        if candidate.is_dir():
            order["sandbox"] = str(candidate)
            return str(candidate)
    return None


def waiting() -> list[dict]:
    """Заказы, на которых граф стоит и ждёт оператора."""
    with _LOCK:
        items = [o for o in ORDERS.values()
                 if o.get("verdict") == "needs_human" or
                 (o.get("question") and o.get("status") == "running")]
    return [{"id": o["id"], "text": o["text"], "question": o.get("question", ""),
             "done": [s["target"] for s in o.get("steps", [])
                      if s.get("status") == "done"],
             "failed": [s["target"] for s in o.get("steps", [])
                        if s.get("status") == "failed"]}
            for o in items]


# ==========================================================================
#  Поток событий
# ==========================================================================
#  Без потока интерфейс узнаёт о работе только по завершении заказа:
#  пользователь смотрит на пустой прогресс минуту, пока считает дальность.
#  Поток отдаёт те же события по мере их появления.
# ==========================================================================
# ==========================================================================
#  Сторож заказа: объявляет зависание сам, без ручной проверки
# ==========================================================================
#: заказ без прогресса дольше этого (сек) — завис; watchdog переводит
#: его в failed с честным диагнозом, чтобы конвейер не жил вечно
STALL_S = float(os.environ.get("KR_STALL_S", "900"))
_watchdog: threading.Thread | None = None


#: как часто заказы сбрасываются на диск (защита от рестарта моста).
#: Пишем не на каждое обновление: прогон идёт быстро, а файл большой.
_PERSIST_EVERY = 2.0
_last_persist = 0.0


def _mark_progress(o: dict) -> None:
    global _last_persist
    o["_seen_at"] = time.time()
    now = time.time()
    if now - _last_persist >= _PERSIST_EVERY:
        _last_persist = now
        threading.Thread(target=_persist_orders, daemon=True).start()


def _log(o: dict, text: str) -> None:
    """Строка журнала заказа с меткой времени — единый формат для всех
    источников (стадии агентов, служебные события, сторож)."""
    stamp = datetime.datetime.now().strftime("%H:%M:%S")
    o["logs"] = (o.get("logs") or []) + [f"[{stamp}] {text}"]


def _watchdog_loop() -> None:
    """Фоновый надзор: заказ без прогресса объявляется зависшим сам."""
    while True:
        time.sleep(20)
        now = time.time()
        with _LOCK:
            stuck = [o for o in ORDERS.values()
                     if o.get("status") == "running"
                     and now - float(o.get("_seen_at")
                                     or o.get("_started_at_ts", now))
                     > STALL_S]
            for o in stuck:
                # какая стадия зависла — по шагам, а не наугад
                cur = next((str(s.get("title") or s.get("target"))
                            for s in (o.get("steps") or [])
                            if s.get("status") == "running"), "?")
                last = (o.get("logs") or ["(пусто)"])[-1][:200]
                o["status"] = "failed"
                o["finished_at"] = now_iso()
                o["logs"] = (o.get("logs") or []) + [
                    f"СТОРОЖ: нет прогресса более {int(STALL_S)} с на "
                    f"стадии «{cur}». Последняя строка журнала: {last}"]
                o["report"] = (
                    "Заказ остановлен сторожем: более "
                    f"{int(STALL_S)} секунд без единого изменения состояния "
                    f"на стадии «{cur}».\n\n"
                    f"Последнее событие журнала:\n  {last}\n\n"
                    "Вероятные причины и что делать:\n"
                    "  * модель не отвечает (маршрут молчит) — проверить "
                    "доступность; KR_RACE=1 включает гонку маршрутов;\n"
                    "  * стадия ждёт внешний инструмент (make-цель);\n"
                    "  * полный ход работы агентов — design/agents_"
                    "transcript.md в песочнице заказа.")


def resume_interrupted(root: str = ".") -> list[str]:
    """Продолжить заказы, прерванные перезапуском моста.

    Мост может перезапуститься посреди заказа (перезагрузка машины,
    правка кода). Заказ не должен от этого рассыпаться: он помнит
    сделанное в песочнице, поэтому достаточно продолжить его с места
    остановки.
    """
    with _LOCK:
        if not ORDERS:
            _load_orders_locked()
        items = [(oid, o) for oid, o in ORDERS.items()
                 if o.get("status") in ("running", "interrupted", "queued")
                 and str(o.get("text") or "")]
    started: list[str] = []
    for oid, o in items:
        try:
            res = continue_order(oid, root=root)
        except Exception:                             # noqa: BLE001
            continue
        if not res.get("error"):
            started.append(oid)
    return started


def watchdog_start() -> None:
    global _watchdog
    if _watchdog is None or not _watchdog.is_alive():
        _watchdog = threading.Thread(target=_watchdog_loop, daemon=True)
        _watchdog.start()


def stream(order_id: str, poll: float = 0.4):
    """Отдаёт заказ по мере изменений — конечный генератор для SSE.

    События:
        ``state`` — весь заказ (первое и при существенных изменениях)
        ``log``   — новая строка журнала графа
        ``step``  — изменился статус шага
        ``done``  — заказ завершён, поток закрывается
    """
    import json
    last_log = 0
    last_steps: dict[str, str] = {}
    last_status = ""
    idle = 0

    while True:
        o = get(order_id)
        if o is None:
            yield ("done", {"error": "unknown order", "id": order_id})
            return

        status = o.get("status", "")
        if status != last_status:
            last_status = status
            idle = 0
            yield ("state", o)
        else:
            idle += poll

        for i, line in enumerate(o.get("logs") or []):
            if i >= last_log:
                yield ("log", {"text": line})
                last_log = i + 1

        steps = {s.get("id", str(i)): s.get("status", "")
                 for i, s in enumerate(o.get("steps", []))}
        if steps != last_steps:
            changed = [s for s in o.get("steps", [])
                       if last_steps.get(s.get("id", "")) != s.get("status")]
            last_steps = steps
            yield ("step", {"steps": changed, "progress": o.get("progress", 0)})

        if status in ("done", "failed", "partial", "cancelled"):
            yield ("done", o)
            return

        # заказ завис и ничего не меняется — отдаём последнее известное
        # и закрываем поток, чтобы вкладка не висела вечно
        if idle > 900:
            yield ("done", {**o, "note": "поток закрыт по таймауту ожидания"})
            return
        time.sleep(poll)


# ==========================================================================
#  Что вообще умеет движок — для интерфейса
# ==========================================================================
def capabilities(root: str = ".", profile: str = "") -> dict:
    import llm
    from . import planner

    proj = load_project(root, profile=profile)
    tops = [t for t in proj.buildable() if t.top_level][:10]
    return {
        "project": proj.as_dict(),
        "agents": engine.graph_shape(),
        "runners": runners_available(),
        "engine": "langgraph" if have_langgraph() else "plain",
        "verify": proj.verify,
        "suggestions": [t.name for t in tops],
        "llm": llm.status(),
        "sandbox": {
            "enabled": USE_SANDBOX,
            "locked": LOCK_PROJECT,
            "integrity_check": CHECK_INTEGRITY,
            "take_artifacts": TAKE_ARTIFACTS,
            "keep": KEEP_SANDBOXES,
            "root": str(Path(proj.root) / "work" / "orders"),
            "existing": sandbox.list_sandboxes(proj.root),
        },
    }


def plan_only(text: str, root: str = ".", profile: str = "") -> dict:
    """Спланировать без выполнения — для предпросмотра в интерфейсе."""
    from . import planner
    proj = load_project(root, profile=profile)
    r = planner.plan(text, proj)
    return {k: v for k, v in r.items()}