"""Настройка OpenCode: бесплатные модели Nous Portal + наш MCP.

Зачем
----
OpenCode — единственный установленный здесь CLI-агент, и он умеет любой
OpenAI-совместимый провайдер. То есть он может работать на тех же
бесплатных моделях, что и наш собственный движок, — из того же ключа,
который уже лежит в `~/.hermes/auth.json`.

Ключ **не вписывается в конфиг**. Он живёт около часа, поэтому
захардкодить его — значит через час получить 401 и не понять почему.
Вместо этого:

* конфиг объявляет провайдера через `env: ["NOUS_PORTAL_API_KEY"]`;
* `scripts/oc.sh` берёт токен из `~/.hermes/auth.json` и подставляет
  его в окружение перед запуском.

Список моделей не зашит: скрипт спрашивает `/v1/models` и оставляет
только помеченные `:free`. Они у Nous меняются — через месяц список
будет другим, и обновлять конфиг руками не придётся.

Запуск
------
    ./scripts/oc.sh                       разово, пишет opencode.json
    ./scripts/oc.sh run "собери всё"      сразу с задачей
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "opencode.json"
HERMES_AUTH = Path(os.environ.get(
    "KR_HERMES_AUTH", Path.home() / ".hermes" / "auth.json"))

PORTAL = "https://inference-api.nousresearch.com/v1"
PROVIDER_ID = "nous-free"

#: человеческие названия для известных моделей; неизвестные берут
#: собственное имя, но читаемее — по части после слеша
NICE_NAMES = {
    "stepfun/step-3.7-flash": "Step 3.7 Flash",
    "stepfun/step-5-preview": "Step 5 Preview",
    "poolside/laguna-s-2.1": "Laguna S 2.1",
    "poolside/laguna-xs-2.1": "Laguna XS 2.1",
    "meituan/longcat-2.0": "LongCat 2.0",
    "meituan/longcat-2.5-preview": "LongCat 2.5 Preview",
    "upstage/solar-mini4": "Solar Mini 4",
}


def _expired(stamp: str) -> bool:
    try:
        t = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return (t - datetime.now(timezone.utc)).total_seconds() <= 0
    except (ValueError, TypeError):
        return False


def credential() -> str:
    """Токен Nous Portal из локального входа Hermes."""
    env = os.environ.get("NOUS_PORTAL_API_KEY", "").strip()
    if env:
        return env
    if not HERMES_AUTH.is_file():
        return ""
    try:
        nous = json.loads(HERMES_AUTH.read_text(encoding="utf-8")) \
            .get("providers", {}).get("nous", {})
    except (OSError, ValueError):
        return ""
    for field in ("agent_key", "access_token"):
        tok = nous.get(field)
        exp = nous.get(f"{field}_expires_at") or nous.get("expires_at")
        if tok and not (exp and _expired(exp)):
            return tok
    return nous.get("agent_key") or nous.get("access_token") or ""


def free_models(key: str) -> list[str]:
    """Модели, помеченные :free — как их перечисляет каталог."""
    if not key:
        return []
    import urllib.error
    import urllib.request
    req = urllib.request.Request(
        f"{PORTAL}/models", headers={"Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception:                                           # noqa: BLE001
        return []
    return sorted(m["id"] for m in data.get("data", [])
                  if str(m.get("id", "")).endswith(":free"))


def alive_models(key: str, models: list[str]) -> list[str]:
    """Какие из перечисленных действительно отвечают.

    Каталог врёт. Проверено: из семи моделей с суффиксом `:free` в
    момент проверки одна отдавала «no longer free», а несколько —
    429 по fair-share лимиту. Если отдать их все в конфиг, OpenCode
    по умолчанию попадёт на нерабочую и покажет «Payment required» —
    выглядит так, будь ключ не тот.

    Проверка дешёвая: один короткий запрос на модель.
    """
    import urllib.error
    import urllib.request
    ok: list[str] = []
    for mid in models:
        body = json.dumps({"model": mid,
                           "messages": [{"role": "user", "content": "ok"}],
                           "max_tokens": 4}).encode()
        req = urllib.request.Request(
            f"{PORTAL}/chat/completions", data=body,
            headers={"Authorization": f"Bearer {key}",
                     "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60):
                ok.append(mid)
        except urllib.error.HTTPError as e:
            code = e.code
            why = e.read().decode("utf-8", "replace")[:70]
            print(f"    ✗ {mid:34s} HTTP {code}: {why}")
        except Exception as exc:                                # noqa: BLE001
            print(f"    ✗ {mid:34s} {type(exc).__name__}")
    return ok


def build_provider(models: list[str]) -> dict:
    """Блок провайдера в формате OpenCode V2.

    Ключ подставляется через `settings.apiKey`, а не через `env`. Разница
    практическая: `env` — это список имён для собственного хранилища
    учётных данных OpenCode (`/connect`), и пакет
    openai-compatible берёт ключ из `settings.apiKey`. С одним лишь `env`
    провайдер поднимался, но запросы уходили без заголовка и Portal
    отвечал «Payment required».
    """
    return {
        "name": "Nous Portal (бесплатные модели)",
        "env": ["NOUS_PORTAL_API_KEY"],
        "package": "@opencode/ai/providers/openai-compatible",
        "settings": {
            "baseURL": PORTAL,
            "apiKey": "{env:NOUS_PORTAL_API_KEY}",
        },
        "models": {
            mid: {
                "name": NICE_NAMES.get(mid.replace(":free", ""),
                                       mid.replace(":free", "")),
                # modelID — то, что уходит провайдеру; ключ карты — то,
                # по чему модель выбирают в /models
                "modelID": mid,
                "cost": {"input": 0, "output": 0},
            }
            for mid in models
        },
    }


def merge_config(models: list[str]) -> dict:
    """Обновить opencode.json, не потеряв остальные настройки."""
    cfg: dict = {}
    if CONFIG.is_file():
        try:
            cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
        except ValueError:
            cfg = {}

    cfg.setdefault("$schema", "https://opencode.ai/config.json")
    cfg.setdefault("mcp", {}).setdefault("servers", {}).setdefault(
        "krystal",
        {"type": "remote", "url": "http://127.0.0.1:8787/mcp"})

    providers = cfg.setdefault("providers", {})
    providers[PROVIDER_ID] = build_provider(models)

    # модель по умолчанию — тоже бесплатная
    if models:
        cfg["model"] = f"{PROVIDER_ID}/{models[0]}"
    return cfg


def write(models: list[str]) -> dict:
    cfg = merge_config(models)
    CONFIG.write_text(
        json.dumps(cfg, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8")
    return cfg


def main(argv: list[str]) -> int:
    key = credential()
    if not key:
        print("Ключ Nous Portal не найден.", file=sys.stderr)
        print("  Один из вариантов:", file=sys.stderr)
        print("    export NOUS_PORTAL_API_KEY=…  (ключ с portal.nousresearch.com)")
        print(f"    или войти через Hermes — появится {HERMES_AUTH}",
              file=sys.stderr)
        return 1

    listed = free_models(key)
    if not listed:
        print("Список бесплатных моделей получить не удалось — "
              "проверьте ключ и сеть.", file=sys.stderr)
        return 1

    # каталог обещает больше, чем отвечает: проверяем живыми запросами,
    # иначе модель по умолчанию может оказаться нерабочей
    if "--no-probe" not in argv:
        print(f"  проверяю {len(listed)} моделей каталога…")
        models = alive_models(key, listed)
    else:
        models = listed

    if not models:
        print("  ни одна бесплатная модель сейчас не отвечает.", file=sys.stderr)
        print("  Скорее всего, исчерпан общий лимит. Попробуйте позже.",
              file=sys.stderr)
        return 1

    cfg = write(models)
    print(f"\n  {CONFIG.relative_to(ROOT)} обновлён")
    print(f"  провайдер : {PROVIDER_ID} ({len(models)} отвечающих из "
          f"{len(listed)})")
    for m in models:
        print(f"    ✓ {m}")
    print(f"  модель по умолчанию: {cfg['model']}")
    print("  MCP: krystal -> http://127.0.0.1:8787/mcp")

    rest = [a for a in argv if a not in ("--write", "--no-probe")]
    if "--write" in argv or not rest:
        return 0

    env = {**os.environ, "NOUS_PORTAL_API_KEY": key}
    print("\n  запускаю opencode…\n")
    return subprocess.call(["opencode", *rest], cwd=ROOT, env=env)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))