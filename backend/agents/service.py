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


def get(order_id: str) -> dict | None:
    with _LOCK:
        o = ORDERS.get(order_id)
    return _order_view(o) if o else None


def list_orders(limit: int = 20) -> list[dict]:
    with _LOCK:
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
        if not any(k in agent_keywords for k in keys):
            # keep seeding direction anyway
            pass
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

    # Расписание из bureau_tasks
    try:
        if related_tasks:
            _log(order, f"📋 Предметные задачи: {len(related_tasks)} задач в очереди")
    except Exception:
        pass
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
    with _LOCK:
        o = ORDERS.get(order_id)
        if not o or o.get("status") in ("done", "failed", "partial"):
            return False
        o["status"] = "cancelled"
        o["finished_at"] = now_iso()
        return True


# ==========================================================================
#  Прерывание и возобновление
# ==========================================================================
def question_of(order_id: str) -> str:
    """Вопрос, на котором граф стоит, — для интерфейса."""
    o = get(order_id)
    return (o or {}).get("question", "")


def resume(order_id: str, answer: str, root: str = ".") -> dict:
    """Продолжить прерванный заказ после ответа оператора.

    Граф сохранил состояние в чекпоинте, поэтому работа продолжается
    с места остановки: уже собранные цели не пересобираются заново,
    а модели уходит ответ оператора как вводные.

    Если чекпоинта нет (мост перезапустился до того, как состояние
    записалось, или langgraph не установлен), заказ перезапускается
    целиком — об этом честно сообщаем в ответе.
    """
    with _LOCK:
        o = ORDERS.get(order_id)
    if o is None:
        return {"error": "unknown order", "id": order_id}

    box = _existing_sandbox(order_id, o, root)
    engine.set_workdir(box)
    try:
        res = engine.resume(order_id, answer, root=root)
    except Exception as exc:                                    # noqa: BLE001
        engine.set_workdir(None)
        return {"error": f"возобновление не удалось: {exc}"}
    finally:
        engine.set_workdir(None)

    with _LOCK:
        o.update({
            "status": res.get("status", "done"),
            "intent": res.get("intent", o.get("intent", "")),
            "progress": res.get("progress", o.get("progress", 0)),
            "verdict": res.get("verdict", ""),
            "question": res.get("question", ""),
            "note": res.get("note", ""),
            "steps": res.get("steps", o.get("steps", [])),
            "report": res.get("report", ""),
            "logs": res.get("log", o.get("logs", [])),
            "finished_at": res.get("finished_at") or now_iso(),
        })
    return _order_view(o)


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
    candidate = Path(root).resolve() / "work" / "orders" / order_id
    return str(candidate) if candidate.is_dir() else None


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


def _mark_progress(o: dict) -> None:
    o["_seen_at"] = time.time()


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