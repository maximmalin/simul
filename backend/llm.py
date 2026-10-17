"""Слой доступа к LLM — переключаемый, внешний и локальный.

Ключевая идея: **всё общение идёт по одному протоколу**
`POST {base}/chat/completions`. Его понимают и облачные провайдеры
(OpenAI, DeepSeek, Groq, Mistral), и локальные рантаймы (Ollama,
LM Studio, llama.cpp, vLLM). Поэтому переключение между «облаком»
и «своей машиной» — это смена конфигурации, а не смена кода.

Включение и выключение
----------------------
    KR_LLM_ENABLED=1|0        главный выключатель (по умолчанию 1)
    KR_LLM_PROVIDER=<имя>     кто отвечает: openai|deepseek|groq|
                              ollama|lmstudio|llamacpp|custom
    KR_LLM_FALLBACK=auto|<имя>| резервный провайдер, `auto` — локальный

Роли
-----
Разные задачи требуют разного качества. Планирование заказа — сложная
задача, сводка результатов — рутинная. Поэтому у провайдера есть две
роли, и каждую можно переопределить отдельно:

    KR_LLM_MODEL          модель для планирования
    KR_LLM_MODEL_FAST     модель для сводок

Пример: облако для планирования, локальная модель для сводок.

    KR_LLM_ENABLED=1
    KR_LLM_PROVIDER=openai
    KR_LLM_API_KEY=sk-...
    KR_LLM_FAST_PROVIDER=ollama
    KR_LLM_FAST_MODEL=qwen2.5:7b-instruct

Зачем разделение ролей
----------------------
LLM в этом проекте **не исполняет цели и не выдумывает числа**. Он
разбирает намерение заказа и формулирует сводку по фактам из `out/*.json`.
Ошибиться он может только в формулировке, а не в физике — поэтому
сводить его на дешёвую локальную модель безопасно, а планирование,
где нужен аккуратный разбор неоднозначной формулировки, лучше
отдать модели посильнее.

Что здесь важно
---------------
* `Authorization` **не отправляется**, если ключа нет. Локальные
  серверы (Ollama, llama.cpp) отвергают запрос с чужим Bearer —
  из-за этого локальный режим раньше не работал в принципе.
* Есть `probe()` — проверка живости провайдера по `/v1/models`.
  Её использует `make llm-check`.
* Есть `status()` — что включено и чем реально отвечает. Показывается
  в интерфейсе через `/api/llm`.
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out"

#: главный выключатель
ENABLED = os.environ.get("KR_LLM_ENABLED", "1").strip().lower() not in (
    "0", "false", "no", "off", "нет", "выкл")


# ==========================================================================
#  Провайдеры
# ==========================================================================
@dataclass(frozen=True)
class Provider:
    """Описание одного источника LLM."""

    key: str
    title: str
    base: str                  # без /chat/completions
    key_env: str               # имя переменной с ключом
    key_required: bool         # локальным ключ не нужен
    default_model: str
    models_hint: str = ""      # подсказка, что обычно доступно


#: Суффикс `:free` в каталоге не гарантия: проверено обращением к API —
#: часть моделей возвращает 429 по fair-share лимиту, а одна заявленная
#: бесплатной отвечает «no longer free». Поэтому откат идёт и по моделям,
#: и список ниже задан порядком проверки, а не алфавитом.
NOUS_FREE_DEFAULT = "poolside/laguna-xs-2.1:free"

#: резервные бесплатные модели: если основная занята или исчезла,
#: берётся следующая. Порядок — по результатам проверки живости.
NOUS_FREE_FALLBACKS = (
    "upstage/solar-mini4:free",
    "stepfun/step-5-preview:free",
    "meituan/longcat-2.5-preview:free",
    "poolside/laguna-s-2.1:free",
    "stepfun/step-3.7-flash:free",
)


def _model_dead_key(pk: str, model: str) -> str:
    return f"{pk}/{model}"


PROVIDERS: dict[str, Provider] = {
    "openai": Provider(
        "openai", "OpenAI", "https://api.openai.com/v1",
        "KR_LLM_API_KEY", True, "gpt-4o-mini",
        "gpt-4o-mini · gpt-4o"),
    "deepseek": Provider(
        "deepseek", "DeepSeek", "https://api.deepseek.com/v1",
        "KR_LLM_API_KEY", True, "deepseek-chat",
        "deepseek-chat · deepseek-reasoner"),
    "groq": Provider(
        "groq", "Groq", "https://api.groq.com/openai/v1",
        "KR_LLM_API_KEY", True, "llama-3.3-70b-versatile",
        "llama-3.3-70b-versatile · qwen-2.5-72b"),
    "mistral": Provider(
        "mistral", "Mistral", "https://api.mistral.ai/v1",
        "KR_LLM_API_KEY", True, "mistral-small-latest",
        "mistral-small-latest"),
    # --- локальные: ключ не нужен, Authorization не шлём ---
    "ollama": Provider(
        "ollama", "Ollama (локально)",
        os.environ.get("KR_LLM_OLLAMA_HOST", "http://127.0.0.1:11434/v1"),
        "KR_LLM_OLLAMA_KEY", False, "qwen2.5:7b-instruct",
        "qwen2.5:7b-instruct · llama3.1:8b · gemma3:12b"),
    "lmstudio": Provider(
        "lmstudio", "LM Studio (локально)",
        os.environ.get("KR_LLM_LMSTUDIO_HOST", "http://127.0.0.1:1234/v1"),
        "KR_LLM_LMSTUDIO_KEY", False, "local-model",
        "любая загруженная модель"),
    "llamacpp": Provider(
        "llamacpp", "llama.cpp (локально)",
        os.environ.get("KR_LLM_LLAMACPP_HOST", "http://127.0.0.1:8080/v1"),
        "KR_LLM_LLAMACPP_KEY", False, "local-model",
        "любая загруженная GGUF"),
    "vllm": Provider(
        "vllm", "vLLM (локально)",
        os.environ.get("KR_LLM_VLLM_HOST", "http://127.0.0.1:8000/v1"),
        "KR_LLM_VLLM_KEY", False, "local-model",
        "любая обслуживаемая модель"),
    "custom": Provider(
        "custom", "Произвольный endpoint",
        os.environ.get("KR_LLM_BASE", "http://127.0.0.1:8080/v1"),
        "KR_LLM_API_KEY", False, "local-model", "указывается явно"),
    # --- OpenCode: агентный интерфейс с доступными моделями ---
    "opencode": Provider(
        "opencode", "OpenCode",
        os.environ.get("KR_LLM_OPENCODE_HOST", "http://127.0.0.1:3001/v1"),
        "KR_LLM_OPENCODE_KEY", False, "auto",
        "локальные модели OpenCode (автоопределение)"),
    # --- Nous Portal: облако с бесплатным тарифом ---
    # OpenAI-совместимый API с моделями, помеченными суффиксом `:free`.
    # Ключ бесплатный, карта не нужна. Это единственное облако, которое
    # здесь реально применимо: локальная 3B модель на CPU отвечает около
    # минуты на ход, а агентному циклу это не подходит.
    "nous": Provider(
        "nous", "Nous Portal (бесплатный тариф)",
        "https://inference-api.nousresearch.com/v1",
        "NOUS_PORTAL_API_KEY", True, NOUS_FREE_DEFAULT,
        "stepfun/step-3.7-flash:free · poolside/laguna-s-2.1:free · "
        "meituan/longcat-2.5-preview:free · upstage/solar-mini4:free"),
}

#: провайдеры без ключа — их имеет смысл пробовать при авто-откате
LOCAL_KEYS = tuple(k for k, p in PROVIDERS.items() if not p.key_required)


# ==========================================================================
#  Автоопределение локального сервера
# ==========================================================================
#  Привязка к одному рантайму — это хрупко: у человека может стоять
#  llama.cpp, а не Ollama; или LM Studio; или vLLM; или собственный
#  opencode-сервер. Все они говорят один и тот же протокол
#  `/v1/chat/completions`, поэтому перечисляем типовые адреса и берём
#  тот, который реально отвечает.
# ==========================================================================
CANDIDATE_HOSTS: tuple[tuple[str, int], ...] = (
    (11434, 11434),     # Ollama
    (8080, 8080),       # llama.cpp server, llamafile
    (1234, 1234),       # LM Studio
    (8000, 8000),       # vLLM
    (5000, 5000),       # любой локальный OpenAI-совместимый
    (5001, 5001),       # text-generation-webui
    (1234 + 0, 1234),
    (7000, 7000),       # LM Studio (alt)
    (3001, 3001),       # opencode / локальные агенты
)

_DISCOVERED: dict[str, str] = {}      # provider -> base url
_DISCOVERED_AT = 0.0


def discover(force: bool = False) -> dict[str, dict]:
    """Найти локальные OpenAI-совместимые серверы.

    Проверяются типовые порты запросом `/v1/models`. Найденное
    кэшируется на минуту: автоопределение не должно превращаться
    в сетевую проверку на каждый вызов модели.
    """
    global _DISCOVERED_AT, _DISCOVERED
    now = time.monotonic()
    if not force and _DISCOVERED and now - _DISCOVERED_AT < 60:
        return _DISCOVERED
    _DISCOVERED_AT = now

    found: dict[str, dict] = {}
    for _port in _unique_ports():
        base = f"http://127.0.0.1:{_port}/v1"
        try:
            req = urllib.request.Request(base + "/models", method="GET")
            with urllib.request.urlopen(req, timeout=1.5) as r:
                data = json.loads(r.read().decode("utf-8"))
        except Exception:                                        # noqa: BLE001
            continue
        names = [m.get("id") for m in (data.get("data") or [])
                 if isinstance(m, dict) and m.get("id")]
        key = _guess_provider(_port)
        found[key] = {"base": base, "port": _port, "models": names}
    _DISCOVERED = found
    return found


def _unique_ports() -> list[int]:
    seen: list[int] = []
    for _label, port in CANDIDATE_HOSTS:
        if port not in seen:
            seen.append(port)
    return seen


def _guess_provider(port: int) -> str:
    return {11434: "ollama", 8080: "llamacpp", 1234: "lmstudio",
            8000: "vllm", 7000: "lmstudio"}.get(port, f"local:{port}")


# ==========================================================================
#  Пресеты
# ==========================================================================
#: Готовые связки «провайдер + модель» под типовые случаи.
#: Переключаются одной переменной: KR_LLM_PRESET=nous
# ==========================================================================
PRESETS: dict[str, dict[str, str]] = {
    # --- локально ---
    # Hermes намеренно нет в списке. Документация Nous Research прямо
    # предупреждает: семейство Hermes 4 «настроено под чат и рассуждения,
    # а не под быстрый цикл вызова инструментов, на который агент опирается».
    # Для агентного цикла Hermes — худший выбор из открытых.
    "qwen": {"provider": "ollama", "model": "qwen2.5:3b-instruct",
             "hint": "Qwen2.5 3B — быстрый вариант для CPU без видеокарты"},
    "llama": {"provider": "ollama", "model": "llama3.1:8b",
              "hint": "Llama 3.1 8B — универсальная открытая модель"},
    "gemma": {"provider": "ollama", "model": "gemma3:4b",
              "hint": "Gemma 3 4B — хороший вызов инструментов на CPU"},
    "mistral": {"provider": "ollama", "model": "ministral:3b",
                "hint": "Mistral 3B — очень быстрый на CPU"},
    # --- OpenCode: агентный интерфейс с доступными моделями ---
    "opencode": {"provider": "opencode", "model": "auto",
                "hint": "OpenCode — агентный сервер с локальными моделями"},
    # --- облако с бесплатным тарифом (ключ бесплатный, карта не нужна) ---
    "nous": {"provider": "nous", "model": "stepfun/step-3.7-flash:free",
             "hint": "Nous Portal, бесплатно. Нужен ключ NOUS_PORTAL_API_KEY"},
    "nous-laguna": {"provider": "nous", "model": "poolside/laguna-s-2.1:free",
                     "hint": "Nous Portal, бесплатно. Нужен ключ NOUS_PORTAL_API_KEY"},
    "nous-longcat": {"provider": "nous", "model": "meituan/longcat-2.5-preview:free",
                     "hint": "Nous Portal, бесплатно. Нужен ключ NOUS_PORTAL_API_KEY"},
}


def apply_preset(name: str) -> dict | None:
    """Включить пресет. Возвращает применённые настройки или None."""
    p = PRESETS.get(name)
    if not p:
        return None
    os.environ["KR_LLM_PROVIDER"] = p["provider"]
    os.environ["KR_LLM_MODEL"] = p["model"]
    PROVIDERS.setdefault(p["provider"], Provider(
        p["provider"], f"{p['provider']} (автоопределение)",
        p.get("base", "http://127.0.0.1:8080/v1"),
        "KR_LLM_LOCAL_KEY", False, p["model"]))
    return p


def provider_key(pk: str) -> str:
    return os.environ.get(PROVIDERS[pk].key_env, "").strip()


# ==========================================================================
#  Ключи, уже имеющиеся на машине
# ==========================================================================
#  Если Nous Portal уже авторизован через Hermes, отдельный ключ
#  заводить не нужно: он лежит в ~/.hermes/auth.json. Движок подхватывает
#  его сам — требовать от пользователя дублировать учётные данные было бы
#  лишним. Явно заданная переменная окружения всегда важнее.
# ==========================================================================
HERMES_AUTH = Path(os.environ.get(
    "KR_HERMES_AUTH", Path.home() / ".hermes" / "auth.json"))

_CRED_CACHE: dict[str, str] = {}


def _hermes_credential() -> str:
    """Токен Nous Portal из локального входа Hermes.

    В файле два токена: access_token и agent_key. Оба короткоживущие
    (порядка часа), поэтому результат кэшируется на время жизни процесса,
    а перед использованием проверяется срок — иначе запрос уйдёт с
    заведомо мёртвым токеном и молча уйдёт в паузу.
    """
    if not HERMES_AUTH.is_file():
        return ""
    cached = _CRED_CACHE.get("nous")
    if cached:
        return cached
    try:
        data = json.loads(HERMES_AUTH.read_text(encoding="utf-8"))
        nous = data.get("providers", {}).get("nous", {})
    except (OSError, ValueError):
        return ""

    # agent_key живёт дольше access_token, берём его первым
    for field in ("agent_key", "access_token"):
        tok = nous.get(field)
        exp = nous.get(f"{field}_expires_at") or nous.get("expires_at")
        if not tok:
            continue
        if exp and _expired(exp):
            continue
        _CRED_CACHE["nous"] = tok
        return tok
    # срок неизвестен или истёк — отдаём как есть: лучше 401, чем «нет ключа»
    tok = nous.get("agent_key") or nous.get("access_token") or ""
    if tok:
        _CRED_CACHE["nous"] = tok
    return tok


def _expired(stamp: str) -> bool:
    """Истёк ли срок в ISO-строке."""
    try:
        from datetime import datetime, timezone
        t = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return (t - datetime.now(timezone.utc)).total_seconds() <= 0
    except (ValueError, TypeError):
        return False


def credential_source(pk: str) -> str:
    """Откуда взят ключ провайдера — для честного отчёта о настройках."""
    if provider_key(pk):
        return f"{PROVIDERS[pk].key_env} (переменная окружения)"
    if pk == "nous":
        if HERMES_AUTH.is_file():
            return f"{HERMES_AUTH} (вход Hermes, найден автоматически)"
        return "не задан — войдите через Hermes или задайте NOUS_PORTAL_API_KEY"
    return "не задан"


# ==========================================================================
#  Конфигурация (с учётом переопределений переменных)
# ==========================================================================
def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _resolve_model(pk: str, wanted: str) -> str:
    """Подставить модель, которая реально есть на этом сервере.

    Заданная по умолчанию модель часто не установлена: пользователь
    настроил один рантайм, а дефолт указывает на другой набор весов.
    Тогда вместо ошибки «model not found» берём то, что сервер
    действительно перечислил.
    """
    info = discover().get(pk)
    if not info or not info.get("models"):
        return wanted
    have = info["models"]
    if wanted in have:
        return wanted
    # совпадение по имени тега: «qwen2.5:7b-instruct» -> «qwen2.5:3b-instruct»
    stem = wanted.split(":")[0]
    for name in have:
        if name.split(":")[0] == stem:
            return name
    return have[0]


def routes() -> list[tuple[str, str]]:
    """Список (провайдер, модель) для планирования и для сводок.

    Первая пара — основная, дальше — резерв по порядку. Если провайдер
    не задан, подставляется локальный: так проект остаётся рабочим
    без единого ключа, просто без облачного интеллекта.
    """
    preset = _env("KR_LLM_PRESET")
    if preset:
        apply_preset(preset)

    out: list[tuple[str, str]] = []

    # Дефолт — бесплатная модель в облаке. Раньше стоял ollama с
    # несуществующей 7B, из-за чего первый же заказ уходил в паузу.
    # Nous Portal даёт рабочие вызовы инструментов бесплатно, а локальный
    # сервер остаётся откатом, если сеть недоступна.
    default_pk = "nous" if _hermes_credential() else "ollama"
    main_pk = _env("KR_LLM_PROVIDER", default_pk)
    main_model = _env("KR_LLM_MODEL") or PROVIDERS.get(
        main_pk, PROVIDERS[main_pk] if main_pk in PROVIDERS
        else PROVIDERS["ollama"]).default_model
    out.append((main_pk, _resolve_model(main_pk, main_model)))

    # запасные модели того же провайдера: KR_LLM_MODELS=a,b,c.
    # Суффикс :free не гарантирует доступность — модель может быть
    # занята по fair-share лимиту, пока соседняя отвечает.
    extra = [m.strip() for m in _env("KR_LLM_MODELS").split(",") if m.strip()]
    if not extra and main_pk == "nous":
        # у Nous бесплатные модели делят один канал с лимитом по
        # fair-share: в момент пика половина списка отдаёт 429, поэтому
        # откат по моделям здесь не запасной вариант, а основной
        extra = [m for m in NOUS_FREE_FALLBACKS if m != main_model]
    for m in extra:
        out.append((main_pk, _resolve_model(main_pk, m)))

    fast_pk = _env("KR_LLM_FAST_PROVIDER")
    fast_model = _env("KR_LLM_MODEL_FAST")
    if fast_pk or fast_model:
        out.append((fast_pk or main_pk,
                    _resolve_model(fast_pk or main_pk,
                                   fast_model or main_model)))

    fb = _env("KR_LLM_FALLBACK", "auto")
    if fb == "auto":
        # откат берём из живых серверов, а не из молчаливого дефолта:
        # отправлять запрос в пустой порт — значит ждать таймаут
        found = discover()
        added = False
        for pk, info in found.items():
            if pk != out[0][0]:
                _register_discovered(pk, info)
                out.append((pk, info["models"][0] if info["models"]
                            else main_model))
                added = True
                break
        if not added:
            for pk in LOCAL_KEYS:
                if pk != out[0][0]:
                    out.append((pk, PROVIDERS[pk].default_model))
                    break
    elif fb and fb != out[0][0]:
        out.append((fb, PROVIDERS.get(fb, PROVIDERS["ollama"]).default_model))

    # дедуп по паре
    seen: set[tuple[str, str]] = set()
    uniq = []
    for pk, m in out:
        if (pk, m) not in seen:
            seen.add((pk, m))
            uniq.append((pk, m))
    return uniq


def _register_discovered(key: str, info: dict) -> None:
    """Запомнить найденный сервер как провайдера."""
    if key in PROVIDERS:
        return
    PROVIDERS[key] = Provider(
        key, f"{key} (найден на :{info['port']})", info["base"],
        "KR_LLM_LOCAL_KEY", False,
        info["models"][0] if info["models"] else "local-model")


def enabled() -> bool:
    """Включён ли LLM и есть ли хоть один пригодный маршрут."""
    if not ENABLED:
        return False
    for pk, _m in routes():
        if provider_key(pk) or not PROVIDERS[pk].key_required:
            return True
        # Nous Portal: вход через Hermes тоже считается ключом
        if pk == "nous" and _hermes_credential():
            return True
    return False


# ==========================================================================
#  HTTP
# ==========================================================================
_LOCK = threading.Lock()
_LAST_OK: dict[str, float] = {}       # провайдер -> время последнего успеха
_LAST_ERR: dict[str, str] = {}

#: Провайдер -> (время отказа, причина). Пока локальный сервер не поднят,
#: каждый вызов ждёт таймаут соединения. Это и шумно, и медленно, поэтому
#: после отказа «соединения отказано» провайдер уходит в паузу и не
#: опрашивается какое-то время.
_COOLDOWN_S = int(os.environ.get("KR_LLM_COOLDOWN", "90"))
_DEAD_UNTIL: dict[str, float] = {}
_DEAD_REASON: dict[str, str] = {}


def _is_dead(pk: str, model: str = "") -> bool:
    """Маршрут не отвечает и ещё не вышел из паузы."""
    key = _model_dead_key(pk, model) if model else pk
    return time.monotonic() < _DEAD_UNTIL.get(key, 0.0)


def _mark_dead(pk: str, reason: str, model: str = "") -> None:
    key = _model_dead_key(pk, model) if model else pk
    with _LOCK:
        _DEAD_UNTIL[key] = time.monotonic() + _COOLDOWN_S
        _DEAD_REASON[key] = reason[:160]


def _mark_alive(pk: str, model: str = "") -> None:
    key = _model_dead_key(pk, model) if model else pk
    with _LOCK:
        _DEAD_UNTIL.pop(key, None)
        _DEAD_REASON.pop(key, None)


def _post(base: str, path: str, payload: dict, headers: dict,
          timeout: float) -> dict:
    """POST с честной обработкой ошибок. urllib — без лишних зависимостей."""
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(base.rstrip("/") + path, data=body,
                                 headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        raise RuntimeError(f"HTTP {e.code}: {detail}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"не отвечает: {e.reason}") from e
    except TimeoutError as e:
        raise RuntimeError(f"таймаут {timeout:.0f}s") from e


def _headers(pk: str) -> dict:
    """Заголовки запроса.

    Ключ добавляется **только если он есть**. Раньше Bearer отправлялся
    всегда, и локальные серверы отвечали 401 — с Ollama не работало.
    """
    h = {"Content-Type": "application/json"}
    key = provider_key(pk) or (_hermes_credential() if pk == "nous" else "")
    if key:
        h["Authorization"] = f"Bearer {key}"
    return h


def probe(pk: str, timeout: float = 4.0) -> tuple[bool, str]:
    """Жив ли провайдер. (ok, описание)."""
    p = PROVIDERS.get(pk)
    if not p:
        return False, f"неизвестный провайдер «{pk}»"
    if p.key_required and not (provider_key(pk)
                               or (pk == "nous" and _hermes_credential())):
        return False, f"нет ключа ({p.key_env})"
    try:
        req = urllib.request.Request(p.base.rstrip("/") + "/models",
                                     headers=_headers(pk))
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
        names = [m.get("id", "?") for m in data.get("data", [])]
        return True, (", ".join(names[:4]) or "модели не перечислены")
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}"
    except Exception as exc:                                    # noqa: BLE001
        return False, str(exc)[:120]


# ==========================================================================
#  Вызов с инструментами
# ==========================================================================
@dataclass
class ToolCall:
    """Один вызов инструмента, предложенный моделью."""

    id: str
    name: str
    args: dict


@dataclass
class Turn:
    """Ответ модели на один ход."""

    text: str = ""
    calls: list[ToolCall] = field(default_factory=list)
    provider: str = ""
    model: str = ""
    raw: dict = field(default_factory=dict)


def chat(messages: list[dict], *, tools: list[dict] | None = None,
         fast: bool = False, temperature: float = 0.0,
         max_tokens: int = 900, timeout: float = 120.0) -> Turn | None:
    """Один ход диалога с необязательными инструментами.

    Аргументы приходят уже разобранными — провайдер проверяет их по
    схеме. Это заменяет прежнюю схему «вежливо попросить вернуть JSON
    и вытащить его регуляркой»: мусор в ответе больше не ломает разбор,
    а несуществующую цель провайдер не пропустит.
    """
    if not enabled():
        return None

    chain = routes()
    if fast and len(chain) > 1:
        chain = chain[1:] + chain[:1]

    errors: list[str] = []
    for pk, model in chain:
        if pk not in PROVIDERS:
            continue
        if PROVIDERS[pk].key_required and not (
                provider_key(pk) or (pk == "nous" and _hermes_credential())):
            errors.append(f"{pk}: нет ключа")
            continue
        if _is_dead(pk, model):
            errors.append(f"{pk}/{model}: "
                          f"{_DEAD_REASON.get(_model_dead_key(pk, model), 'недоступно')} "
                          f"(пауза {_COOLDOWN_S}s)")
            continue

        payload: dict = {
            "model": model, "messages": messages,
            "temperature": temperature, "max_tokens": max_tokens,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        try:
            data = _post(PROVIDERS[pk].base, "/chat/completions",
                         payload, _headers(pk), timeout)
            _mark_alive(pk, model)
            with _LOCK:
                _LAST_OK[_model_dead_key(pk, model)] = time.time()
                _LAST_ERR.pop(_model_dead_key(pk, model), None)
            return Turn(**_parse_turn(data, pk, model))
        except Exception as exc:                                # noqa: BLE001
            msg = f"{pk}/{model}: {exc}"[:200]
            errors.append(msg)
            with _LOCK:
                _LAST_ERR[pk] = msg
            low = str(exc).lower()
            if any(x in low for x in ("connection refused", "no route to host",
                                      "name or service not known",
                                      "failed to establish")):
                _mark_dead(pk, low, model)

    if errors and not all("пауза" in e for e in errors):
        print(f"[llm] все маршруты не сработали -> {errors[-1]}")
    return None


def chat_one(messages: list[dict], *, pk: str, model: str,
             tools: list[dict] | None = None,
             temperature: float = 0.0, max_tokens: int = 900,
             timeout: float = 120.0) -> Turn | None:
    """Один ход у конкретного маршрута (провайдер, модель).

    Выделено из chat(), чтобы гонка (backend/llm_race.py) использовала
    ровно ту же логику запроса: заголовки, парсинг хода, отметка
    «жив/мёртв» — всё то же, без обходных путей.
    """
    if pk not in PROVIDERS:
        return None
    if PROVIDERS[pk].key_required and not (
            provider_key(pk) or (pk == "nous" and _hermes_credential())):
        return None
    if _is_dead(pk, model):
        return None
    payload: dict = {
        "model": model, "messages": messages,
        "temperature": temperature, "max_tokens": max_tokens,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    try:
        data = _post(PROVIDERS[pk].base, "/chat/completions",
                     payload, _headers(pk), timeout)
        _mark_alive(pk, model)
        with _LOCK:
            _LAST_OK[_model_dead_key(pk, model)] = time.time()
            _LAST_ERR.pop(_model_dead_key(pk, model), None)
        return Turn(**_parse_turn(data, pk, model))
    except Exception as exc:                                    # noqa: BLE001
        msg = f"{pk}/{model}: {exc}"[:200]
        with _LOCK:
            _LAST_ERR[pk] = msg
        low = str(exc).lower()
        if any(x in low for x in ("connection refused", "no route to host",
                                  "name or service not known",
                                  "failed to establish")):
            _mark_dead(pk, low, model)
        return None


def _parse_turn(data: dict, pk: str, model: str) -> dict:
    """Разобрать ответ провайдера в Turn.

    Разные OpenAI-совместимые серверы отдают вызовы инструментов чуть
    по-разному: у Ollama поле называется `function.arguments`, у части
    провайдеров аргументы приходят уже объектом, а не строкой JSON.
    Здесь это приводится к одному виду, чтобы верхнему коду не знать
    про особенности каждого.
    """
    choice = (data.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    text = (msg.get("content") or "").strip()

    calls: list[ToolCall] = []
    for raw in msg.get("tool_calls") or []:
        fn = raw.get("function") or {}
        name = fn.get("name") or ""
        args = fn.get("arguments")
        if isinstance(args, str):
            try:
                args = json.loads(args or "{}")
            except ValueError:
                args = {"_raw": args}
        if not isinstance(args, dict):
            args = {}
        calls.append(ToolCall(id=raw.get("id") or f"call_{len(calls)}",
                              name=name, args=args))

    # некоторые локальные серверы вместо tool_calls возвращают <tool_call>
    if not calls and text.startswith("<tool_call>"):
        text = ""
        for chunk in text.split("<tool_call>")[1:]:
            body = chunk.split("</tool_call>")[0].strip()
            try:
                obj = json.loads(body)
            except ValueError:
                continue
            args = obj.get("arguments") or obj.get("parameters") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except ValueError:
                    args = {}
            calls.append(ToolCall(id=obj.get("id", f"call_{len(calls)}"),
                                  name=obj.get("name", ""),
                                  args=args if isinstance(args, dict) else {}))

    return {"text": text, "calls": calls, "provider": pk, "model": model,
            "raw": data}


# ==========================================================================
#  Вызов
# ==========================================================================
def complete(user: str, context: str = "", *, fast: bool = False,
             system: str = "", max_tokens: int = 800,
             temperature: float = 0.2,
             timeout: float = 90.0) -> str | None:
    """Вызов LLM с откатом по маршрутам. None — если не вышло ни разу.

    `fast=True` пропускает основной маршрут, если для этой роли задан
    отдельный (обычно локальный и дешёвый).
    """
    if not enabled():
        return None

    chain = routes()
    if fast and len(chain) > 1:
        chain = chain[1:] + chain[:1]     # сперва быстрый, потом основной

    errors: list[str] = []
    for pk, model in chain:
        if pk not in PROVIDERS:
            continue
        if PROVIDERS[pk].key_required and not (
                provider_key(pk) or (pk == "nous" and _hermes_credential())):
            errors.append(f"{pk}: нет ключа")
            continue
        if _is_dead(pk, model):
            errors.append(f"{pk}/{model}: "
                          f"{_DEAD_REASON.get(_model_dead_key(pk, model), 'недоступно')} "
                          f"(пауза {_COOLDOWN_S}s)")
            continue
        msgs = [{"role": "system", "content": system or _DEFAULT_SYSTEM}]
        if context:
            msgs.append({"role": "user", "content": f"Контекст:\n{context}"})
        msgs.append({"role": "user", "content": user})
        try:
            data = _post(PROVIDERS[pk].base, "/chat/completions",
                         {"model": model, "messages": msgs,
                          "temperature": temperature, "max_tokens": max_tokens},
                         _headers(pk), timeout)
            text = data["choices"][0]["message"]["content"].strip()
            with _LOCK:
                _LAST_OK[pk] = time.time()
                _LAST_ERR.pop(pk, None)
            return text
        except Exception as exc:                                # noqa: BLE001
            msg = f"{pk}/{model}: {exc}"[:200]
            errors.append(msg)
            with _LOCK:
                _LAST_ERR[pk] = msg
            # «соединение отказано» и «нет такого хоста» — это не
            # ошибка запроса, а отсутствие сервера: гасим маршрут,
            # иначе каждый вызов будет ждать таймаут
            text_err = str(exc).lower()
            if ("connection refused" in text_err
                    or "no route to host" in text_err
                    or "name or service not known" in text_err
                    or "failed to establish" in text_err):
                _mark_dead(pk, text_err)

    # молчим, если все отказы — это провайдеры в паузе: о повторной
    # неудаче одного и того же недоступного сервиса сообщать не нужно
    if errors and not all("пауза" in e for e in errors):
        print(f"[llm] все маршруты не сработали -> {errors[-1]}")
    return None


_DEFAULT_SYSTEM = (
    "Ты — инженерный ассистент проекта Krystal Radio (zero-IF SDR, "
    "диапазонное преобразование, STM32F103C8T6). Отвечай по-русски, "
    "кратко и по делу. Опирайся только на переданные факты и артефакты: "
    "не выдумывай числовые значения, которых нет в контексте."
)


# ==========================================================================
#  Сбор фактов из артефактов
# ==========================================================================
def _read_json(name: str) -> dict | None:
    p = OUT / name
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _flatten(obj, prefix: str = "", depth: int = 0) -> dict:
    """Плоский список «путь.к.ключ = значение» — LLM читает это лучше,
    чем вложенный JSON, и не выдумывает вложенную структуру."""
    flat: dict[str, object] = {}
    if depth > 3:
        return flat
    if isinstance(obj, dict):
        for k, v in obj.items():
            key = f"{prefix}{k}"
            if isinstance(v, (dict, list)):
                sub = _flatten(v, f"{key}.", depth + 1)
                flat.update(sub or {key: f"<{type(v).__name__}>"})
            else:
                flat[key] = v
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:12]):
            sub = _flatten(v, f"{prefix}[{i}].", depth + 1)
            flat.update(sub)
        if len(obj) > 12:
            flat[f"{prefix}…"] = f"ещё {len(obj) - 12} элементов"
    return flat


def facts(outcome: dict | None = None) -> str:
    """Сводка фактов по артефактам `out/` — единственный источник чисел.

    Модели передаётся именно эта строка. Так отчёт опирается на
    измерения, а не на правдоподобные догадки.
    """
    lines: list[str] = []
    sources = [
        ("link_budget.json", "дальность приёма"),
        ("thermal_fast.json", "тепловой режим"),
        ("reliability.json", "надёжность (MTBF)"),
        ("direction_finding.json", "пеленгация"),
        ("virtual_rx_spectrum.json", "спектр виртуального приёмника"),
        ("enclosure.json", "корпус"),
    ]
    for name, title in sources:
        data = _read_json(name)
        if not data:
            continue
        flat = _flatten(data)
        # только осмысленные скаляры, без служебного мусора
        good = {k: v for k, v in flat.items()
                if isinstance(v, (int, float, str, bool))
                and len(str(v)) < 90
                and not k.lower().endswith(("_path", "_file", "_ts", "_at"))}
        if not good:
            continue
        lines.append(f"[{title}] out/{name}")
        for k, v in list(good.items())[:28]:
            lines.append(f"  {k} = {v}")
        lines.append("")

    if outcome:
        lines.append("[результат заказа]")
        for step in outcome.get("steps", [])[:24]:
            lines.append(f"  {step.get('status'):8s} {step.get('title')} "
                         f"({step.get('agent_id')})")
            if step.get("error"):
                lines.append(f"           ошибка: {step['error'][:200]}")
        lines.append("")

    return "\n".join(lines) if lines else "Артефакты в out/ не найдены."


# ==========================================================================
#  Состояние для интерфейса
# ==========================================================================
def status(probe_live: bool = False) -> dict:
    """Что включено, чем реально отвечает — для `/api/llm`."""
    chain = [{"provider": pk, "model": m,
              "title": PROVIDERS[pk].title if pk in PROVIDERS else pk,
              "base": PROVIDERS[pk].base if pk in PROVIDERS else "",
              "local": pk in LOCAL_KEYS,
              "key_set": bool(provider_key(pk) or _hermes_credential()),
              "key_source": credential_source(pk),
              "cooldown": _is_dead(pk),
              "cooldown_reason": _DEAD_REASON.get(pk),
              "last_ok": _LAST_OK.get(pk),
              "last_error": _LAST_ERR.get(pk)}
             for pk, m in routes()]

    if probe_live:
        for item in chain:
            ok, note = probe(item["provider"])
            item["alive"] = ok
            item["note"] = note
            if ok:
                _mark_alive(item["provider"])
            else:
                _mark_dead(item["provider"], note)

    return {
        "enabled": enabled(),
        "master_switch": ENABLED,
        "chain": chain,
        "available": sorted(PROVIDERS),
        "hints": {k: (p.models_hint or p.default_model)
                  for k, p in PROVIDERS.items()},
        "vllm_langgraph": _langgraph_available(),
    }


def _langgraph_available() -> bool:
    try:
        import langgraph  # noqa: F401
        return True
    except ImportError:
        return False


# ==========================================================================
#  CLI: make llm-check
# ==========================================================================
def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(
        description="Проверка настроек LLM: master switch, маршруты, живость")
    ap.add_argument("--probe", action="store_true",
                    help="обратиться к каждому провайдеру (/v1/models)")
    args = ap.parse_args()

    print(f"Krystal Radio — LLM\n")
    print(f"  главный выключатель KR_LLM_ENABLED : "
          f"{'ВКЛ' if ENABLED else 'ВЫКЛ'}")
    print(f"  эффективно включён              : {'да' if enabled() else 'нет'}")
    print(f"  LangGraph в этом интерпретаторе : "
          f"{'есть' if _langgraph_available() else 'нет'}")
    preset = _env("KR_LLM_PRESET")
    if preset:
        print(f"  пресет                          : {preset}")
    print()

    # --- локальные серверы: на чём реально можно работать ---
    found = discover(force=True)
    print("  локальные OpenAI-совместимые серверы:")
    if found:
        for k, v in found.items():
            models = ", ".join(v["models"][:4]) or "(модели не перечислены)"
            print(f"    {k:12s} :{v['port']:<6} {models}")
    else:
        print("    ничего не найдено — поднимите любой из них:")
        print("      ollama serve · llama-server · LM Studio · vLLM")
    print()

    print("  пресеты свободных моделей:")
    for name, p in PRESETS.items():
        mark = "← включён" if name == preset else ""
        print(f"    {name:9s} {p['model']:26s} {p['hint']} {mark}")
    print()

    print("  маршруты (по порядку, с откатом):")
    for i, (pk, model) in enumerate(routes()):
        p = PROVIDERS.get(pk)
        kind = "локальный" if (pk in LOCAL_KEYS or pk.startswith("local:")) \
            else "облако"
        mark = "основной" if i == 0 else f"откат {i}"
        src = credential_source(pk)
        print(f"    {i}. {mark:9s} {pk:12s} [{kind}] {model}")
        print(f"       {p.title if p else pk} · {p.base if p else ''}")
        print(f"       ключ: {src}")
        if args.probe:
            ok, note = probe(pk)
            print(f"       {'ЖИВ' if ok else 'НЕ ОТВЕЧАЕТ'}: {note}")
    print()

    if not enabled():
        print("  Итог: LLM выключен — план строится по правилам, "
              "сводки структурные.")
        print("       Включить: KR_LLM_ENABLED=1 и KR_LLM_PROVIDER "
              "с ключом, либо поднимите локальный сервер.")
    else:
        src = credential_source(routes()[0][0])
        print(f"\n  Ключ основного маршрута: {src}")

    if not (provider_key("nous") or _hermes_credential()):
        print("\n  Бесплатное облако без карты — Nous Portal:")
        print("    1. зарегистрироваться на portal.nousresearch.com")
        print("    2. создать ключ в разделе API keys")
        print("    3. export NOUS_PORTAL_API_KEY=<ключ>")
        print("    4. KR_LLM_PRESET=nous make llm-check")
        print("    Модели помечены суффиксом :free и не тарифицируются.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())