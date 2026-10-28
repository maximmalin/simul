#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Гонка моделей: первый ответ выигрывает, ожидание не суммируется.

Зачем. Все облачные маршруты проекта — бесплатные (Nous Portal), и
один из них может молчать: urlopen(timeout=) ограничивает не чтение,
а установление соединения, поэтому «зависший» провайдер держит весь
конвейер. Последовательный перебор складывает такие паузы.

Здесь все маршруты, разрешённые для гонки, стартуют одновременно в
своих потоках; побеждает первый непустой результат, остальные потоки
демонтируются. Победитель автоматически поднимается в начало цепочки
следующих запросов, поэтому последующие ходы приходят к быстрому
провайдеру сразу.

Управление:
    KR_RACE=0            выключить гонку (последовательный перебор);
    KR_RACE_WIDTH=3      сколько маршрутов одновременно (по умолчанию 3);
    KR_RACE_HOLD=120     сколько секунд победитель остаётся первым.
"""
from __future__ import annotations

import json
import os
import threading
import time

import llm

#: ширина гонки: бесплатный шлюз жёстко лимитирован, лишние маршруты
#: съедают лимит и приводят к 429 на живом заказе
_WIDTH = max(1, int(os.environ.get("KR_RACE_WIDTH", "2") or 2))
_HOLD = max(0.0, float(os.environ.get("KR_RACE_HOLD", "120") or 0))
_OFF = os.environ.get("KR_RACE", "1").strip().lower() in ("0", "false",
                                                           "no", "off")

#: маршрут, победивший последний раз -> время победы
_LAST_WIN: dict[str, float] = {}
#: наблюдение: сколько гонок выиграл каждый маршрут (диагностика в логе)
_WINS: dict[str, int] = {}


def _key(pk: str, model: str) -> str:
    return f"{pk}/{model}"


def _race_pool(exclude: set[str],
               profile: str = "") -> list[tuple[str, str]]:
    """Маршруты для гонки: победитель прошлого раза + свежие."""
    all_chain = [(pk, m) for pk, m in llm.routes(profile)
                 if _key(pk, m) not in exclude]
    # маршруты на паузе в обойму не берутся: гонка их подождёт сама
    # (пауза снимается за секунды) — и не потратит на них бюджет хода
    chain = [r for r in all_chain if not llm._is_dead(r[0], r[1])]
    if not chain:
        return []
    fresh = [r for r in chain
             if time.time() - _LAST_WIN.get(_key(*r), 0.0) > _HOLD]
    win = [r for r in chain if r not in fresh]
    pool = (win[:1] + fresh)[:_WIDTH] if win else fresh[:_WIDTH]
    # Обойма чередует провайдеров: если все три места в обойме займёт
    # один облачный пул, его лимит убьёт весь ход. Один слот на
    # провайдера — и подмена всегда есть.
    by_pk: dict[str, list] = {}
    for pk, m in pool:
        by_pk.setdefault(pk, []).append((pk, m))
    if len(by_pk) > 1:
        mixed: list = []
        order = list(by_pk)
        i = 0
        while len(mixed) < len(pool):
            pk = order[i % len(order)]
            if by_pk[pk]:
                mixed.append(by_pk[pk].pop(0))
            i += 1
            if not any(by_pk.values()):
                break
        pool = mixed
    return pool


def _runner(messages: list[dict], tools: list[dict] | None,
            temperature: float, max_tokens: int, timeout: float,
            pk: str, model: str, sink: dict,
            response_format: dict | None = None) -> None:
    try:
        turn = llm.chat_one(messages, pk=pk, model=model, tools=tools,
                           temperature=temperature, max_tokens=max_tokens,
                           timeout=timeout, response_format=response_format)
    except Exception as exc:                                   # noqa: BLE001
        sink[f"{pk}/{model}: ошибка {type(exc).__name__}"] = None
        return
    if turn and (turn.text or turn.calls):
        sink[f"{pk}/{model}"] = turn
    elif turn is None:
        # маршрут не ответил: в канал ответов кладём только None (иначе
        # строка-причина будет принята за результат), а причину смотрим
        # в llm._LAST_ERR
        sink[f"{pk}/{model}"] = None


#: сколько ходов к модели могут идти одновременно. Бесплатные шлюзы
#: ограничивают поток: без ограничения параллельные агенты упираются в
#: лимит и висят до таймаута вместо ответа.
_CHAT_SLOTS = threading.Semaphore(
    max(1, int(os.environ.get("KR_LLM_CONCURRENCY", "2"))))


def chat(messages: list[dict], *, tools: list[dict] | None = None,
         temperature: float = 0.0, max_tokens: int = 900,
         timeout: float = 120.0, note: str = "", profile: str = "",
         response_format: dict | None = None) -> llm.Turn | None:
    """Ход с гонкой маршрутов. Сигнатура совпадает с llm.chat."""
    with _CHAT_SLOTS:
        return _chat(messages, tools=tools, temperature=temperature,
                     max_tokens=max_tokens, timeout=timeout, note=note,
                     profile=profile, response_format=response_format)


def _chat(messages: list[dict], *, tools: list[dict] | None = None,
          temperature: float = 0.0, max_tokens: int = 900,
          timeout: float = 120.0, note: str = "", profile: str = "",
          response_format: dict | None = None) -> llm.Turn | None:
    # Таймаут маршрута короче таймаута хода: зависшая модель не должна
    # держать ход — за это время успевает ответить другая. Переключение
    # на замену должно быть вовремя, а не через минуту молчания.
    route_timeout = max(30.0, min(timeout, timeout / 1.5))
    if _OFF or not llm.enabled():
        return llm.chat(messages, tools=tools, temperature=temperature,
                        max_tokens=max_tokens, timeout=timeout,
                        profile=profile, response_format=response_format)

    key = _cache_key(messages, tools, temperature, max_tokens)
    if profile:
        key += "|profile:" + profile
    if response_format:
        key += "|schema"
    if profile:
        key += "|" + profile
    if response_format:
        key += "|schema"
    cached = _CACHE.get(key)
    if cached is not None:
        return cached

    excluded: set[str] = set()
    fallback: llm.Turn | None = None
    fallback_key = ""
    attempts = 0
    # Общий бюджет хода: без него гонка делает три попытки по полному
    # таймауту и ещё один финальный вызов — на медленном маршруте это
    # четыре с половиной минуты молчания вместо ответа.
    budget_end = time.monotonic() + timeout * 1.5
    while True:
        attempts += 1
        pool = _race_pool(excluded, profile)
        if not pool:
            # все маршруты на паузе (лимит шлюза): ждём до конца самой
            # короткой паузы и пробуем снова — ход не должен умирать
            # из-за лимита, который снимется через полминуты
            waits = [llm.dead_in(pk, m) for pk, m in
                     llm.routes(profile) if _key(pk, m) not in excluded]
            wait = min([w for w in waits if w > 0] or [0])
            left = budget_end - time.monotonic()
            if 0 < wait <= min(35.0, left):
                # ждём до конца паузы: лимит бесплатного пула — это
                # «позже», а не «никогда»
                time.sleep(min(wait, 25.0))
                continue
            print(f"[race] пустая обойма: профиль={profile or 'обычный'}, "
                  f"цепочка={llm.routes(profile)[:4]}, "
                  f"исключено={len(excluded)}", flush=True)
            break
        if len(pool) == 1:
            # остался один маршрут — он всё равно годится: лучше он,
            # чем пустой ход (замена подхватывается на ходу)
            pk, model = pool[0]
            turn = llm.chat_one(messages, pk=pk, model=model, tools=tools,
                                temperature=temperature, max_tokens=max_tokens,
                                timeout=route_timeout,
                                response_format=response_format)
            if turn is not None and (turn.text or turn.calls):
                _LAST_WIN[_key(pk, model)] = time.time()
                _WINS[_key(pk, model)] = _WINS.get(_key(pk, model), 0) + 1
                if len(_CACHE) >= _CACHE_MAX:
                    _CACHE.pop(next(iter(_CACHE)))
                _CACHE[key] = turn
                return turn
            excluded.update({_key(pk, model)})
            break
        left = budget_end - time.monotonic()
        if left <= 2:
            break
        sink: dict[str, llm.Turn | None] = {}
        threads = [
            threading.Thread(target=_runner,
                             args=(messages, tools, temperature, max_tokens,
                                   route_timeout, pk, model, sink,
                                   response_format),
                             daemon=True)
            for pk, model in pool
        ]
        for t in threads:
            t.start()
        winner: llm.Turn | None = None
        winner_key = ""
        fallback: llm.Turn | None = None
        fallback_key = ""
        deadline = min(time.monotonic() + route_timeout, budget_end)
        while time.monotonic() < deadline:
            for key, turn in list(sink.items()):
                if turn is None:
                    continue
                if (getattr(turn, "reasoning_only", False)
                        or llm._is_reasoning(getattr(turn, "text", ""))) \
                        and not turn.calls:
                    # модель отдала рассуждение, а не результат. Но если
                    # в конце есть JSON — это и есть ответ: отбрасывать
                    # такой ход незачем, тогда приходится ждать другой
                    # маршрут, а лимит времени уже потрачен.
                    chunk = llm.json_tail(getattr(turn, "text", ""))
                    if chunk:
                        winner, winner_key = llm.Turn(
                            text=chunk, provider=getattr(turn, "provider", ""),
                            model=getattr(turn, "model", ""),
                            raw=getattr(turn, "raw", None)), key
                        break
                    # рассуждение без ответа: маршрут рабочий, отбракован
                    # лишь этот ход
                    if fallback is None:
                        fallback, fallback_key = turn, key
                    continue
                winner, winner_key = turn, key
                break
            if winner is not None:
                break
            time.sleep(0.05)
        for t in threads:
            if t.is_alive() and winner is not None:
                # демон-поток: победителя не ждём, остальные просто
                # умрут вместе с процессом; повторные запросы идут
                # уже к быстрому маршруту
                pass
        if winner is None and fallback is not None:
            # маршрут ответил рассуждением, а не результатом: сам
            # маршрут остаётся в обойме (он не сломан), но этот ответ
            # не годится — идём к следующему маршруту
            continue
        if winner is not None:
            _LAST_WIN[winner_key] = time.time()
            _WINS[winner_key] = _WINS.get(winner_key, 0) + 1
            if len(_CACHE) >= _CACHE_MAX:
                _CACHE.pop(next(iter(_CACHE)))
            _CACHE[key] = winner
            return winner
        # все молчали — идём к следующей паре, исключая уже проверенные
        if attempts == 1:
            # первая неудача: показываем, кто и почему не ответил — иначе
            # тишина в журнале выглядит как «агент думает»
            print(f"[race {time.strftime('%H:%M:%S')}] ни один маршрут не ответил: "
                  + "; ".join(f"{k}: {str(v)[:90]}"
                              for k, v in list(sink.items())[:4])
                  + " | последние ошибки: "
                  + "; ".join(f"{pk}: {str(llm._LAST_ERR.get(pk, '-'))[:70]}"
                              for pk, _m in pool[:4]),
                  flush=True)
        excluded.update({_key(pk, m) for pk, m in pool})
        if any("429" in str(x) for x in sink.keys()):
            # шлюз сказал «слишком часто»: короткая пауза и следующая
            # пара. Долгий сон здесь съедал бы бюджет хода целиком.
            time.sleep(min(8.0, max(2.0, timeout / 20)))
        if attempts >= 6:
            break
    if fallback is not None:
        # других маршрутов не осталось — отдаём хоть это
        _CACHE[key] = fallback
        return fallback
    print(f"[race {time.strftime('%H:%M:%S')}] ход без ответа (маршрутов tried: {len(excluded)}, "
          f"профиль: {profile or 'обычный'})", flush=True)
    left = max(5.0, budget_end - time.monotonic())
    turn = llm.chat(messages, tools=tools, temperature=temperature,
                    max_tokens=max_tokens, timeout=left, profile=profile,
                    response_format=response_format)
    if turn is not None and len(_CACHE) < _CACHE_MAX:
        _CACHE[key] = turn
    return turn


#: кэш ответов: при лимите на бесплатном шлюзе повторный тот же запрос
#: не должен снова уходить в сеть. Ключ — полный состав запроса, так что
#: совпадение означает ровно ту же задачу.
_CACHE: dict[str, llm.Turn] = {}
_CACHE_MAX = 200


def _cache_key(messages: list[dict], tools: list[dict] | None,
               temperature: float, max_tokens: int) -> str:
    import hashlib
    blob = json.dumps([messages, tools, temperature, max_tokens],
                      ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()


def cache_stats() -> dict:
    return {"записей": len(_CACHE)}


def stats() -> dict[str, int]:
    """Кто выигрывает гонки (для диагностического лога)."""
    return dict(_WINS)