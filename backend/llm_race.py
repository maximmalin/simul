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

import os
import threading
import time

import llm

_WIDTH = max(1, int(os.environ.get("KR_RACE_WIDTH", "3") or 3))
_HOLD = max(0.0, float(os.environ.get("KR_RACE_HOLD", "120") or 0))
_OFF = os.environ.get("KR_RACE", "1").strip().lower() in ("0", "false",
                                                           "no", "off")

#: маршрут, победивший последний раз -> время победы
_LAST_WIN: dict[str, float] = {}
#: наблюдение: сколько гонок выиграл каждый маршрут (диагностика в логе)
_WINS: dict[str, int] = {}


def _key(pk: str, model: str) -> str:
    return f"{pk}/{model}"


def _race_pool(exclude: set[str]) -> list[tuple[str, str]]:
    """Маршруты для гонки: победитель прошлого раза + свежие."""
    chain = [(pk, m) for pk, m in llm.routes()
             if _key(pk, m) not in exclude]
    if not chain:
        return []
    fresh = [r for r in chain
             if time.time() - _LAST_WIN.get(_key(*r), 0.0) > _HOLD]
    win = [r for r in chain if r not in fresh]
    pool = (win[:1] + fresh)[:_WIDTH] if win else fresh[:_WIDTH]
    return pool


def _runner(messages: list[dict], tools: list[dict] | None,
            temperature: float, max_tokens: int, timeout: float,
            pk: str, model: str, sink: dict) -> None:
    try:
        turn = llm.chat_one(messages, pk=pk, model=model, tools=tools,
                           temperature=temperature, max_tokens=max_tokens,
                           timeout=timeout)
    except Exception as exc:                                   # noqa: BLE001
        sink[f"{pk}/{model}: ошибка {type(exc).__name__}"] = None
        return
    if turn and (turn.text or turn.calls):
        sink[f"{pk}/{model}"] = turn


def chat(messages: list[dict], *, tools: list[dict] | None = None,
         temperature: float = 0.0, max_tokens: int = 900,
         timeout: float = 120.0, note: str = "") -> llm.Turn | None:
    """Ход с гонкой маршрутов. Сигнатура совпадает с llm.chat."""
    if _OFF or not llm.enabled():
        return llm.chat(messages, tools=tools, temperature=temperature,
                        max_tokens=max_tokens, timeout=timeout)

    excluded: set[str] = set()
    attempts = 0
    while True:
        attempts += 1
        pool = _race_pool(excluded)
        if len(pool) < 2:
            break
        sink: dict[str, llm.Turn | None] = {}
        threads = [
            threading.Thread(target=_runner,
                             args=(messages, tools, temperature, max_tokens,
                                   timeout, pk, model, sink),
                             daemon=True)
            for pk, model in pool
        ]
        for t in threads:
            t.start()
        winner: llm.Turn | None = None
        winner_key = ""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for key, turn in list(sink.items()):
                if turn is not None:
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
        if winner is not None:
            _LAST_WIN[winner_key] = time.time()
            _WINS[winner_key] = _WINS.get(winner_key, 0) + 1
            return winner
        # все молчали — идём к следующей паре, исключая уже проверенные
        excluded.update({_key(pk, m) for pk, m in pool})
        if attempts >= 3:
            break
    return llm.chat(messages, tools=tools, temperature=temperature,
                    max_tokens=max_tokens, timeout=timeout)


def stats() -> dict[str, int]:
    """Кто выигрывает гонки (для диагностического лога)."""
    return dict(_WINS)