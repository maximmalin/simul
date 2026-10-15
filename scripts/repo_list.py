"""Вывод списка репозиториев сервера.

Отдельным файлом, а не `python3 -c` внутри bash: f-строки с кавычками
в heredoc собираются непредсказуемо, и ошибка выглядит как проблема
скрипта, а не как синтаксис.

Читает JSON ответа API на stdin, печатает таблицу.
"""

from __future__ import annotations

import json
import sys


def main() -> int:
    raw = sys.stdin.read().strip()
    if not raw:
        print("  сервер не ответил")
        return 1
    try:
        repos = json.loads(raw)
    except ValueError as exc:
        print(f"  ответ не разобран: {exc}")
        print(f"  начало ответа: {raw[:160]}")
        return 1

    if isinstance(repos, dict):
        # Forgejo отвечает ошибкой объектом, а не списком
        print(f"  ошибка: {repos.get('message') or repos}")
        return 1
    if not repos:
        print("  репозиториев нет")
        return 0

    rows = sorted(repos, key=lambda r: r.get("updated_at") or "", reverse=True)
    print(f"  {'репозиторий':34s} {'ветка':10s} {'доступ':8s} обновлён")
    for r in rows:
        kind = "private" if r.get("private") else "public"
        updated = (r.get("updated_at") or "")[:10]
        print(f"  {r.get('full_name', '?'):34s} "
              f"{r.get('default_branch') or '-':10s} {kind:8s} {updated}")
    print(f"  всего: {len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())