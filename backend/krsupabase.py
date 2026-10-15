"""
Krystal Radio — клиент Supabase (PostgREST) без внешних зависимостей.

Фронтенд (EngineeringTeam) читает данные через Supabase. Чтобы агентная
часть видела те же данные и могла их обновлять, здесь реализован минимальный
клиент поверх PostgREST API — того же протокола, что использует
`@supabase/supabase-js`. Зависимость `supabase-py` не нужна: хватает `httpx`.

Переменные окружения:
    SUPABASE_URL           — https://<ref>.supabase.co  (или http://127.0.0.1:54321)
    SUPABASE_ANON_KEY      — anon-ключ (для чтения/записи с RLS)
    SUPABASE_SERVICE_KEY   — service_role-ключ (обход RLS; для агентов)

Все методы возвращают `dict | list | None` и не бросают исключений —
вызывающая сторона решает, что делать при недоступности.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

URL = (os.environ.get("SUPABASE_URL") or "").rstrip("/")
ANON = os.environ.get("SUPABASE_ANON_KEY", "")
SERVICE = os.environ.get("SUPABASE_SERVICE_KEY", "") or ANON

TIMEOUT = float(os.environ.get("SUPABASE_TIMEOUT", "15"))


def configured() -> bool:
    """Настроен ли доступ к Supabase."""
    return bool(URL) and bool(SERVICE)


def _headers() -> dict[str, str]:
    h = {
        "apikey": SERVICE,
        "Authorization": f"Bearer {SERVICE}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }
    return h


class KrSupabase:
    """Тонкая обёртка над PostgREST."""

    def __init__(self, url: str = URL, key: str = SERVICE,
                 timeout: float = TIMEOUT) -> None:
        self.url = url.rstrip("/")
        self.key = key
        self.timeout = timeout

    # ------------------------------------------------------------------
    @property
    def ok(self) -> bool:
        return bool(self.url) and bool(self.key)

    def _client(self) -> httpx.Client:
        return httpx.Client(base_url=self.url, headers=_headers(),
                            timeout=self.timeout)

    def request(self, method: str, path: str, *, params: dict | None = None,
                json_body: Any = None, prefer: str | None = None) -> Any:
        """Один REST-вызов. Возвращает распарсенный JSON или None при ошибке."""
        if not self.ok:
            return None
        headers = {}
        if prefer:
            headers["Prefer"] = prefer
        try:
            with self._client() as c:
                r = c.request(method, f"/rest/v1/{path}", params=params,
                              json=json_body, headers=headers)
            if r.status_code >= 400:
                print(f"[supabase] {method} {path} -> {r.status_code} "
                      f"{r.text[:180]}")
                return None
            if r.status_code == 204 or not r.content:
                return None
            return r.json()
        except httpx.HTTPError as exc:
            print(f"[supabase] {method} {path} -> transport error: {exc}")
            return None

    # --- чтение --------------------------------------------------------
    def select(self, table: str, *, select: str = "*", order: str | None = None,
               limit: int | None = None, eq: dict | None = None,
               single: bool = False) -> Any:
        params: dict[str, Any] = {"select": select}
        if order:
            params["order"] = order
        if limit:
            params["limit"] = str(limit)
        for k, v in (eq or {}).items():
            params[k] = f"eq.{v}"
        path = f"{table}" + ("?limit=1" if single else "")
        data = self.request("GET", path, params=params)
        return data

    def count(self, table: str, eq: dict | None = None) -> int:
        params = {"select": "id", "limit": "1"}
        for k, v in (eq or {}).items():
            params[k] = f"eq.{v}"
        if not self.ok:
            return 0
        try:
            with self._client() as c:
                r = c.get(f"/rest/v1/{table}", params=params,
                          headers={"Prefer": "count=exact",
                                   "Range": "0-0"})
            m = r.headers.get("content-range", "")
            if "/" in m:
                return int(m.split("/")[-1])
        except (httpx.HTTPError, ValueError):
            pass
        return 0

    # --- запись --------------------------------------------------------
    def insert(self, table: str, row: dict) -> Any:
        if isinstance(row, list):
            return self.request("POST", table, json_body=row)
        return self.request("POST", table, json_body=row,
                            prefer="return=representation")

    def update(self, table: str, eq: dict, patch: dict) -> Any:
        params = {k: f"eq.{v}" for k, v in eq.items()}
        return self.request("PATCH", table, params=params, json_body=patch)

    def upsert(self, table: str, row: dict, on_conflict: str) -> Any:
        params = {"on_conflict": on_conflict}
        return self.request("POST", table, params=params, json_body=row,
                            prefer="return=representation,resolution=merge-duplicates")

    def delete(self, table: str, eq: dict) -> Any:
        params = {k: f"eq.{v}" for k, v in eq.items()}
        return self.request("DELETE", table, params=params)


#: готовый синглтон (None, если Supabase не сконфигурирован)
SB: KrSupabase | None = KrSupabase() if configured() else None


def sb() -> KrSupabase | None:
    return SB if (SB and SB.ok) else None