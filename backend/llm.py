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
import re
from concurrent.futures import ThreadPoolExecutor
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out"


def _load_dotenv() -> None:
    """Прочитать .env корня в os.environ (не затирая заданное снаружи).

    Ключи живут в `.env` (в .gitignore): они меняются каждые пару часов,
    и каждый процесс — мост, сервис, CLI — должен брать их оттуда.
    """
    p = ROOT / ".env"
    if not p.is_file():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


_load_dotenv()

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
    # --- фри-провайдеры без ключа: работают сразу, без регистрации ---
    "pollinations": Provider(
        "pollinations", "Pollinations (анонимный фри-тариф)",
        "https://text.pollinations.ai/openai",
        "KR_LLM_POLLINATIONS_KEY", False, "gpt-oss-20b",
        "gpt-oss-20b · openai-fast"),
    "ovh": Provider(
        "ovh", "OVHcloud AI Endpoints (анонимный фри-тариф)",
        "https://oai.endpoints.kepler.ai.cloud.ovh.net/v1",
        "KR_LLM_OVH_KEY", False, "Qwen3.8-27B",
        "Qwen3.8-27B · gpt-oss-20b · gpt-oss-120b · Qwen3.5-9B"),
    "llm7": Provider(
        "llm7", "LLM7 (нужен ключ для этой модели)",
        "https://api.llm7.io/v1",
        "KR_LLM_LLM7_KEY", True, "gpt-4.1",
        "DeepSeek-V4.1-Flash · Inkling-Small · L3-8B-Lunaris-v1-Turbo"),
    # --- OmniRoute: локальный омни-шлюз, пробрасывает модели провайдеров ---
    # Живёт рядом с машиной (порт 20128), ключ — из opencode-конфига
    # (~/.config/opencode/config.json). REST — OpenAI-совместимый /v1.
    "omniroute": Provider(
        "omniroute", "OmniRoute (локальный омни-шлюз)",
        os.environ.get("KR_LLM_OMNIROUTE_HOST", "http://127.0.0.1:20128/v1"),
        "KR_OMNIROUTE_API_KEY", True, "kc/openrouter/free",
        "kc/openrouter/free · auto/best-coding · auto/reasoning · "
        "auto/fast · auto/chat"),
    "opencode-proxy": Provider(
        "opencode-proxy", "OpenCode Proxy (:8788)",
        os.environ.get("KR_LLM_OPENCODE_PROXY_HOST", "http://127.0.0.1:8788/v1"),
        "KR_LLM_OPENCODE_PROXY_KEY", False, "mimo-v2.6-flash-free",
        "mimo-v2.6-flash-free · beam-free · flint-40b-free · "
        "nemotron-3.5-lightning-free · deepseek-v4.1-flash-free · "
        "glm-5.3-flash-free · nemotron-3.5-super-vl-free"),
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
    """Ключ провайдера.

    Явно заданная переменная окружения важнее всего. Если ключа нет,
    берём его из уже настроенного на машине конфига opencode: отдельная
    учётная запись не нужна, всё уже есть рядом.
    """
    env = os.environ.get(PROVIDERS[pk].key_env, "").strip()
    if env:
        return env
    if pk == "omniroute":
        return _opencode_key()
    return ""


def _opencode_key() -> str:
    """Ключ из конфига opencode (там уже настроен локальный омни-шлюз)."""
    path = Path(os.environ.get(
        "KR_OPENCODE_CONFIG",
        str(Path.home() / ".config" / "opencode" / "config.json")))
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    def _find(node, depth: int = 0) -> str:
        """Искать ключ вглубь: в конфиге он лежит во вложенных узлах."""
        if depth > 4 or not isinstance(node, dict):
            return ""
        for k, v in node.items():
            if isinstance(v, str) and v.strip().startswith("sk-"):
                return v.strip()
        for k, v in node.items():
            if isinstance(v, dict):
                got = _find(v, depth + 1)
                if got:
                    return got
        return ""

    for section in ("providers", "provider"):
        block = data.get(section) or {}
        if not isinstance(block, dict):
            continue
        for name, v in block.items():
            if not isinstance(v, dict):
                continue
            if "omni" in str(name).lower():
                key = _find(v)
                if key:
                    return key
    return _find(data)


def _ensure_omniroute() -> bool:
    """Поднять локальный омни-шлюз, если он не отвечает.

    Шлюз даёт доступ к моделям opencode (в том числе к бесплатным
    маршрутам openrouter), поэтому заказ не должен зависеть от одного
    только бесплатного портала: поднимаем шлюз сами.
    """
    import socket
    import subprocess
    host = os.environ.get("KR_LLM_OMNIROUTE_HOST",
                          "http://127.0.0.1:20128/v1")
    port = 20128
    for part in host.split("/"):
        if part.isdigit():
            port = int(part)
            break
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1.5):
            return True
    except OSError:
        pass
    exe = os.environ.get("KR_OMNIROUTE_BIN", "omniroute")
    try:
        subprocess.Popen([exe, "serve", "--no-open"],
                         stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL,
                         start_new_session=True)
    except Exception:                                        # noqa: BLE001
        return False
    for _ in range(30):
        time.sleep(1.0)
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1.5):
                return True
        except OSError:
            continue
    return False


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
    (порядка часа), поэтому результат кэшируется — но кэш живёт только
    до конца срока токена. Как только он истёк, файл читается заново:
    Hermes сам обновляет токен на диске, и следующий запрос обязан
    взять свежий, иначе уйдёт 401 и агент замрёт.

    Файл перечитывается и когда Hermes обновил токен раньше срока —
    поэтому сравниваем не только срок, но и сам токен в файле.
    """
    if not HERMES_AUTH.is_file():
        return ""
    cached = _CRED_CACHE.get("nous")
    cached_exp = _CRED_CACHE.get("nous_exp") or ""
    try:
        data = json.loads(HERMES_AUTH.read_text(encoding="utf-8"))
        nous = data.get("providers", {}).get("nous", {})
    except (OSError, ValueError):
        return cached or ""

    # agent_key живёт дольше access_token, берём его первым
    for field in ("agent_key", "access_token"):
        tok = nous.get(field)
        exp = nous.get(f"{field}_expires_at") or nous.get("expires_at")
        if not tok:
            continue
        if exp and _expired(exp):
            continue
        # файл обновился (новый токен) или кэш протух — берём из файла
        if cached and cached == tok and not (cached_exp and _expired(cached_exp)):
            return cached
        _CRED_CACHE["nous"] = tok
        _CRED_CACHE["nous_exp"] = exp or ""
        return tok
    # срок неизвестен или истёк — отдаём как есть: лучше 401, чем «нет ключа»
    tok = nous.get("agent_key") or nous.get("access_token") or ""
    if tok:
        _CRED_CACHE["nous"] = tok
        _CRED_CACHE["nous_exp"] = ""
    return tok or cached or ""


def _refresh_hermes_credential() -> str:
    """Обновить токен Nous через гейтвей Hermes и вернуть свежий.

    Токен живёт около часа, а заказ длится дольше. Держать в руках
    протухший ключ бессмысленно: Hermes умеет обновить его сам
    (`hermes auth refresh nous`), и обновлённый токен сразу оказывается
    в auth.json. Ошибка обновления не молчит — она возвращается тем же
    текстом, что был, чтобы вызов упал с настоящей причиной.
    """
    if not HERMES_AUTH.is_file():
        return ""
    try:
        import subprocess
        subprocess.run(["hermes", "auth", "refresh", "nous"],
                       capture_output=True, timeout=45, check=False)
    except Exception:                                        # noqa: BLE001
        pass
    _CRED_CACHE.pop("nous", None)
    _CRED_CACHE.pop("nous_exp", None)
    return _hermes_credential()


def _nous_key() -> str:
    """Ключ Nous Portal: сначала живой вход Hermes, потом переменная окружения.

    Переменная окружения — запасной путь, но ставить её выше нельзя:
    токен Hermes обновляется на диске каждый час, а записанный в .env
    ключ устаревает и молча уводит агентов в 401. Поэтому источник
    выбирается по живости, а не по порядку объявления.

    Если вход Hermes протух — обновляем его через гейтвей и берём
    свежий токен. Только после этого пробуем переменную окружения.
    """
    hermes = _hermes_credential()
    if hermes:
        return hermes
    refreshed = _refresh_hermes_credential()
    if refreshed:
        return refreshed
    return provider_key("nous")


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
    if pk == "nous":
        if _hermes_credential():
            return f"{HERMES_AUTH} (вход Hermes, обновляется автоматически)"
        if provider_key(pk):
            return f"{PROVIDERS[pk].key_env} (переменная окружения)"
        return "не задан — войдите через Hermes или задайте NOUS_PORTAL_API_KEY"
    if provider_key(pk):
        return f"{PROVIDERS[pk].key_env} (переменная окружения)"
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


#: реестр маршрутов: data/llm_routes.json (дополняется без правки кода)
_ROUTES_JSON = Path(os.environ.get(
    "KR_LLM_ROUTES_JSON",
    str(Path(__file__).resolve().parents[1] / "data" / "llm_routes.json")))


def _routes_registry() -> list[tuple[str, str]]:
    """Маршруты из реестра: [(провайдер, модель), …] в порядке объявления."""
    try:
        data = json.loads(_ROUTES_JSON.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    out: list[tuple[str, str]] = []
    for row in (data.get("маршруты") or []):
        if not isinstance(row, dict):
            continue
        pk = str(row.get("провайдер") or "").strip()
        model = str(row.get("модель") or "").strip()
        if pk and model:
            out.append((pk, model))
    return out


def _profiles_registry() -> dict[str, list[str]]:
    """Профили маршрутов: data/llm_routes.json, раздел «профили».

    Профиль — это порядок моделей для круга задач: творческому этапу
    нужна сильная модель, механическому (сбор моделей, паспорта,
    упаковка) — быстрая. Задаётся данными, код не меняется.
    """
    try:
        data = json.loads(_ROUTES_JSON.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    prof = data.get("профили")
    if not isinstance(prof, dict):
        return {}
    out: dict[str, list[str]] = {}
    for name, rows in prof.items():
        if isinstance(rows, list):
            out[str(name)] = [str(x) for x in rows if str(x).strip()]
    return out


def _profile_chain(profile: str,
                   chain: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Порядок маршрутов для профиля.

    В профиле модель названа строкой «провайдер/модель». Если такого
    маршрута нет (модель выключена, ключа нет) — берётся следующий по
    списку; если не нашлось ни одного — остаётся исходная цепочка.
    """
    want = _profiles_registry().get(str(profile or ""))
    if not want:
        return chain
    by_key = {f"{pk}/{m}": (pk, m) for pk, m in chain}
    out: list[tuple[str, str]] = []
    for item in want:
        row = by_key.get(item)
        if row and row not in out:
            out.append(row)
    return out or chain


#: где хранится результат последнего скана моделей
_SCAN_PATH = Path(os.environ.get(
    "KR_LLM_SCAN_JSON",
    str(Path(__file__).resolve().parents[1] / ".krystal" / "llm_scan.json")))


#: публичные списки фри-провайдеров: из них берутся адреса, у которых
#: есть каталог моделей (/models). Списки обновляются каждый день.
FREE_LISTS: tuple[str, ...] = (
    "https://raw.githubusercontent.com/open-free-llm-api/"
    "awesome-freellm-apis/main/README.md",
    "https://raw.githubusercontent.com/nejib1/Free-LLM/main/README.md",
    "https://raw.githubusercontent.com/ClawLabsAI/free-ai-models/main/README.md",
)

_BASES_PATH = Path(os.environ.get(
    "KR_LLM_BASES_JSON",
    str(Path(__file__).resolve().parents[1] / ".krystal" / "llm_bases.json")))
_CATALOG_PATH = Path(os.environ.get(
    "KR_LLM_CATALOG_JSON",
    str(Path(__file__).resolve().parents[1] / ".krystal" / "llm_catalog.json")))


def _fetch_text(url: str, timeout: float = 15.0) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "curl/8"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "ignore")


def free_bases(max_age: float = 86400.0) -> list[str]:
    """Адреса фри-провайдеров из публичных списков (с кэшем на сутки)."""
    try:
        data = json.loads(_BASES_PATH.read_text(encoding="utf-8"))
        if time.time() - float(data.get("at") or 0) < max_age:
            return list(data.get("адреса") or [])
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        pass
    out: set[str] = set()
    for url in FREE_LISTS:
        try:
            text = _fetch_text(url)
        except Exception:                                     # noqa: BLE001
            continue
        for m in re.finditer(r"https?://[A-Za-z0-9._\-]+"
                             r"(?::\d+)?(?:/[A-Za-z0-9._\-/]*)?", text):
            base = m.group(0).rstrip("/.,;)")
            if not base.startswith("http") or "github.com" in base:
                continue
            if any(x in base for x in ("/images", "/audio", "/embedding",
                                       "/moderation", "/docs.", "/wiki",
                                       "/pricing", "/blog")):
                continue
            out.add(base)
    bases = sorted(out)
    try:
        _BASES_PATH.parent.mkdir(parents=True, exist_ok=True)
        _BASES_PATH.write_text(json.dumps({"at": time.time(),
                                            "адреса": bases},
                                           ensure_ascii=False),
                                encoding="utf-8")
    except OSError:
        pass
    return bases


def harvest_catalog(*, timeout: float = 6.0, max_bases: int = 400
                     ) -> dict:
    """Собрать каталог моделей: локальные провайдеры + публичные списки.

    Каталог — это все известные адреса и модели, которые можно позвать
    без ключа. Он большой (тысячи позиций), но вызывается редко; в
    работе используется его живая часть — результат скана.
    """
    catalog: dict[str, dict] = {}

    # 1) провайдеры, которые уже настроены
    for pk in ("nous", "omniroute", "pollinations", "ovh", "llm7",
               "opencode", "ollama", "lmstudio", "llamacpp", "vllm"):
        prov = PROVIDERS.get(pk)
        if prov is None:
            continue
        models = provider_models(pk, free_only=False)
        if models:
            catalog[prov.base] = {"провайдер": pk, "модели": models,
                                  "источник": "провайдер"}
    # 2) адреса из публичных списков: у каждого спрашиваем /models.
    # Запросы параллельны: список большой, по одному — часы.
    def _models_at(base: str) -> tuple[str, list[str]]:
        try:
            req = urllib.request.Request(base.rstrip("/") + "/models",
                                         headers={"User-Agent": "curl/8"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = json.loads(r.read().decode("utf-8", "ignore"))
        except Exception:                                     # noqa: BLE001
            return base, []
        rows = data.get("data") if isinstance(data, dict) else data
        ids: list[str] = []
        for row in rows or []:
            if isinstance(row, str):
                mid = row.strip()
            else:
                mid = str((row or {}).get("id")
                          or (row or {}).get("name") or "").strip()
            if mid:
                ids.append(mid)
        return base, ids

    todo = [b for b in free_bases()[:max_bases] if b not in catalog]
    if todo:
        with ThreadPoolExecutor(max_workers=16) as pool:
            for base, ids in pool.map(_models_at, todo):
                if ids:
                    catalog[base] = {"провайдер": "", "модели": ids,
                                     "источник": "публичный список"}
    payload = {"at": time.time(),
               "адресов": len(catalog),
               "моделей": sum(len(v["модели"]) for v in catalog.values()),
               "каталог": catalog}
    try:
        _CATALOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        _CATALOG_PATH.write_text(json.dumps(payload, ensure_ascii=False),
                                 encoding="utf-8")
    except OSError:
        pass
    return payload


def catalog_entries() -> list[tuple[str, str, str]]:
    """(база, провайдер, модель) по всему собранному каталогу."""
    try:
        data = json.loads(_CATALOG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    out: list[tuple[str, str, str]] = []
    for base, row in (data.get("каталог") or {}).items():
        pk = str(row.get("провайдер") or "")
        for mid in (row.get("модели") or []):
            out.append((base, pk, str(mid)))
    return out


def provider_models(pk: str, free_only: bool = True) -> list[str]:
    """Модели провайдера по его /models (OpenAI-совместимый список).

    У облаков берутся только бесплатные (с суффиксом :free) — платные
    без ключа всё равно не вызвать, а перебирать их — тратить время.
    """
    prov = PROVIDERS.get(pk)
    if prov is None:
        return []
    key = provider_key(pk)
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    try:
        with urllib.request.urlopen(
                urllib.request.Request(prov.base.rstrip("/") + "/models",
                                       headers=headers), timeout=12) as r:
            data = json.loads(r.read().decode("utf-8", "ignore"))
    except Exception:                                         # noqa: BLE001
        return []
    rows = data.get("data") if isinstance(data, dict) else data
    out: list[str] = []
    for row in rows or []:
        if isinstance(row, str):
            mid = row.strip()
        else:
            mid = str((row or {}).get("id") or (row or {}).get("name") or "").strip()
        if not mid:
            continue
        if (free_only and pk not in ("pollinations", "ovh")
                and ":free" not in mid and "free" not in mid.lower()):
            continue
        out.append(mid)
    return out


def register_catalog_providers(max_bases: int = 60) -> dict[str, str]:
    """Завести провайдеров по собранному каталогу.

    Каждый адрес каталога становится провайдером без ключа: так
    подхватываются и те сервисы, которых не было в реестре. Возвращает
    соответствие «адрес → имя провайдера».
    """
    mapping: dict[str, str] = {}
    try:
        data = json.loads(_CATALOG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return mapping
    n = 0
    for base, row in list((data.get("каталог") or {}).items())[:max_bases]:
        if base in PROVIDERS:
            mapping[base] = base
            continue
        models = [m for m in (row.get("модели") or [])
                  if ":free" in str(m) or str(m).startswith(("gpt-oss", "Qwen"))]
        if not models:
            continue
        name = "cat%d" % n
        n += 1
        PROVIDERS[name] = Provider(
            name, f"каталог {base[:48]}", base, "", False, models[0],
            " · ".join(models[:4]))
        mapping[base] = name
    return mapping


def catalog_scan_slice(size: int = 40) -> list[tuple[str, str, str]]:
    """Срез каталога для проверки: каждая модель попадает в проверку
    по очереди (сдвиг), чтобы большой каталог был осмотрен целиком."""
    entries = catalog_entries()
    if not entries:
        return []
    start = int(time.time() // 60) % max(1, len(entries))
    out: list[tuple[str, str, str]] = []
    for i in range(min(size, len(entries))):
        out.append(entries[(start + i) % len(entries)])
    return out


def scan_routes(*, timeout: float = 10.0, extra: int = 68, quick: bool = False) -> list[dict]:
    """Просканировать модели всех провайдеров: кто отвечает сейчас.

    Результат кэшируется на диске: гонка маршрутов берёт из него живые
    модели, а мёртвые (лимит, отказ) не тратит на них бюджет хода.
    """
    fresh = live_routes(max_age=900.0)
    cached = {k: v for k, v in fresh.items()
              if v.get("источник") != "каталог"}
    candidates: list[tuple[str, str]] = list(_routes_registry())
    # каталог бесплатных моделей шлюза — тоже источник: один облачный
    # провайдер не должен быть единственным, иначе его лимит или
    # протухший токен останавливают весь заказ
    if extra and not quick:
        for pk in ("omniroute", "nous", "ovh", "llm7", "pollinations"):
            for mid in provider_models(pk)[:extra]:
                row = (pk, mid)
                if row not in candidates:
                    candidates.append(row)
    # срез большого каталога: адреса и модели, которые ещё не проверялись
    # Срез каталога: если живых моделей хватает, проверяем немного (каждая
    # проверка — запрос к чужому бесплатному пулу, и это тратит его квоту).
    # Если живых нет — проверяем широко: иначе заказ встанет.
    alive_now = sum(1 for r in live_routes().values() if r.get("живая"))
    slice_size = 4 if alive_now >= 3 else 30
    if quick:
        slice_size = 0
    cat_map = register_catalog_providers() if not quick else {}
    for base, pk, mid in catalog_scan_slice(slice_size):
        name = cat_map.get(base) or pk
        row = (name, mid)
        if row not in candidates and name in PROVIDERS:
            candidates.append(row)
    probe = [{"role": "user",
              "content": "Ответь ТОЛЬКО JSON: {\"ok\": true}"}]
    results: list[dict] = []
    # уже проверенные модели переиспользуем: повторная проверка съедает
    # лимиты бесплатных пулов, а ответы не меняются
    done = {f"{pk}/{m}" for pk, m in candidates}
    for key, row in cached.items():
        if key in done and row.get("живая"):
            results.append(row)

    if quick:
        # проверяем только самые быстрые известные модели: три запроса
        # не съедают квоту, а сигнал «жив/мёртв» дают
        known = [(k, v) for k, v in live_routes().items()
                 if v.get("живая")]
        known.sort(key=lambda kv: float(kv[1].get("секунд") or 99))
        have = {f"{r.get('провайдер')}/{r.get('модель')}" for r in results}
        for key, _row in known:
            pk, _, mid = key.partition("/")
            if pk not in PROVIDERS or not mid:
                continue
            if (pk, mid) not in candidates and key not in have:
                candidates.append((pk, mid))
            if len(candidates) >= 3:
                break
    todo = [c for c in candidates
            if f"{c[0]}/{c[1]}" not in {f"{r.get('провайдер')}/{r.get('модель')}"
                                        for r in results}]

    def _probe(item: tuple[str, str]) -> dict:
        pk, model = item
        t0 = time.monotonic()
        why = ""
        try:
            turn = chat_one(probe, pk=pk, model=model, temperature=0.0,
                            max_tokens=40, timeout=timeout, probe=True)
        except Exception as exc:                              # noqa: BLE001
            turn = None
            why = f"{type(exc).__name__}: {exc}"[:120]
        dt = round(time.monotonic() - t0, 2)
        text = (turn.text or "") if turn else ""
        if turn is None and not why:
            why = "нет ответа (лимит, отказ или таймаут)"
        ok = bool(text.strip())
        json_ok = False
        if ok:
            try:
                json.loads(text)
                json_ok = True
            except (ValueError, TypeError):
                json_ok = bool(json_tail(text))
        return {"провайдер": pk, "модель": model, "живая": ok,
                "json": json_ok, "секунд": dt, "причина": why,
                "текст": text[:120]}

    if todo:
        with ThreadPoolExecutor(max_workers=3) as pool:
            results.extend(pool.map(_probe, todo))
    # накопительный список живых: каталог большой, проверка идёт срезом
    alive_path = _SCAN_PATH.with_name("llm_alive.json")
    known: dict[str, dict] = {}
    try:
        known = json.loads(alive_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        known = {}
    for row in results:
        if row.get("живая"):
            known[f"{row['провайдер']}/{row['модель']}"] = row
    try:
        alive_path.write_text(json.dumps(known, ensure_ascii=False),
                              encoding="utf-8")
    except OSError:
        pass
    payload = {"at": time.time(), "модели": results}
    try:
        _SCAN_PATH.parent.mkdir(parents=True, exist_ok=True)
        _SCAN_PATH.write_text(json.dumps(payload, ensure_ascii=False),
                              encoding="utf-8")
    except OSError:
        pass
    return results


def live_routes(max_age: float = 900.0) -> dict[str, dict]:
    """Живые модели: свежий скан + накопленный список.

    Накопленный список хранит модели, которые однажды ответили: каталог
    большой, проверка идёт срезом, и знание о живой модели не должно
    пропадать между прогонами.
    """
    out: dict[str, dict] = {}
    alive_path = _SCAN_PATH.with_name("llm_alive.json")
    try:
        known = json.loads(alive_path.read_text(encoding="utf-8"))
        if isinstance(known, dict):
            out.update({k: v for k, v in known.items()
                        if isinstance(v, dict) and v.get("живая")})
    except (OSError, json.JSONDecodeError):
        pass
    try:
        data = json.loads(_SCAN_PATH.read_text(encoding="utf-8"))
        if time.time() - float(data.get("at") or 0) <= max_age:
            for row in data.get("модели") or []:
                if isinstance(row, dict):
                    out[f"{row.get('провайдер')}/{row.get('модель')}"] = row
    except (OSError, json.JSONDecodeError):
        pass
    return out


def _apply_scan(chain: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Живые маршруты — вперёд, мёртвые — следом.

    Если в цепочке не осталось ни одного живого (у профиля все модели на
    паузе или лимит исчерпан), добавляются любые живые модели из скана —
    заказ не должен вставать из-за одного провайдера.
    """
    scan = live_routes()
    if not scan:
        return chain
    alive = [r for r in chain if scan.get(f"{r[0]}/{r[1]}",
                                           {}).get("живая")]
    if not alive:
        others = [(pk, model) for key, row in scan.items()
                  if row.get("живая") for pk, model in
                  (key.split("/", 1) if "/" in key else (key, ""))]
        others = [r for r in others if r[1] and r not in chain]
        others.sort(key=lambda r: float(
            scan.get(f"{r[0]}/{r[1]}", {}).get("секунд") or 99))
        alive = others[:8]
    dead = [r for r in chain if r not in alive]
    return alive + dead


def routes(profile: str = "") -> list[tuple[str, str]]:
    """Список (провайдер, модель) для планирования и для сводок.

    Первая пара — основная, дальше — резерв по порядку. Если провайдер
    не задан, подставляется локальный: так проект остаётся рабочим
    без единого ключа, просто без облачного интеллекта.

    `profile` — имя профиля из реестра: он меняет порядок моделей
    (например, на быстрые) для этого вызова.
    """
    preset = _env("KR_LLM_PRESET")
    if preset:
        apply_preset(preset)

    out: list[tuple[str, str]] = []

    # Реестр маршрутов в data/llm_routes.json: он задаёт порядок
    # «основной → резерв» и дополняется без правки кода.
    reg = _routes_registry()
    if reg:
        for pk, model in reg:
            if pk not in PROVIDERS:
                continue
            if PROVIDERS[pk].key_required and not provider_key(pk) \
                    and not (pk == "nous" and _nous_key()):
                continue
            if pk == "omniroute":
                _ensure_omniroute()
            out.append((pk, model))
        if out:
            return _apply_scan(_profile_chain(profile, out))

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
        if pk == "nous" and _nous_key():
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
_COOLDOWN_S = int(os.environ.get("KR_LLM_COOLDOWN", "25"))
_DEAD_UNTIL: dict[str, float] = {}
_DEAD_REASON: dict[str, str] = {}


def _is_dead(pk: str, model: str = "") -> bool:
    """Маршрут не отвечает и ещё не вышел из паузы.

    Проверяется и модель, и провайдер целиком: шлюз, который сказал
    «ждать 1400 с», закрывает все свои модели сразу — гонка не должна
    тратить на них бюджет хода.
    """
    now = time.monotonic()
    if model:
        if now < _DEAD_UNTIL.get(_model_dead_key(pk, model), 0.0):
            return True
    return now < _DEAD_UNTIL.get(pk, 0.0)


def _mark_dead(pk: str, reason: str, model: str = "") -> None:
    """Пауза маршрута. Уже начатая пауза НЕ продлевается.

    Иначе неудачные попытки подпитывают паузу сами себя: маршрут, который
    не отвечает, помечается мёртвым, потом пробуется снова (и снова
    не отвечает) — и не выходит из паузы никогда.
    """
    key = _model_dead_key(pk, model) if model else pk
    with _LOCK:
        now = time.monotonic()
        if now < _DEAD_UNTIL.get(key, 0.0):
            return
        _DEAD_UNTIL[key] = now + _COOLDOWN_S
        _DEAD_REASON[key] = reason[:160]


def _mark_dead_until(pk: str, reason: str, seconds: float) -> None:
    """Поставить провайдера на паузу ровно на названный шлюзом срок.

    Пауза действует на все модели провайдера: шлюз, который сказал
    «ждать 1400 с», закрывает их все.
    """
    with _LOCK:
        # Пауза провайдера ограничена: иначе один лимит уводит все модели
        # провайдера из строя на четверть часа, и заказ встаёт.
        until = time.monotonic() + min(max(5.0, float(seconds)), 30.0)
        _DEAD_UNTIL[pk] = until
        _DEAD_REASON[pk] = reason[:160]
        for key in list(_DEAD_UNTIL):
            if key.startswith(f"{pk}/"):
                _DEAD_UNTIL[key] = min(_DEAD_UNTIL[key], until)
                _DEAD_REASON.setdefault(key, reason[:160])


def dead_in(pk: str, model: str = "") -> float:
    """Сколько секунд ещё осталось до конца паузы маршрута."""
    now = time.monotonic()
    left = 0.0
    if model:
        left = max(left, _DEAD_UNTIL.get(_model_dead_key(pk, model), 0.0) - now)
    left = max(left, _DEAD_UNTIL.get(pk, 0.0) - now)
    return max(0.0, left)


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
        if e.code in (402, 429):
            # лимит шлюза: это не поломка, а «слишком часто». Сообщаем
            # отдельно, чтобы гонка подождала, а не долбила шлюз
            retry = ""
            try:
                retry = e.headers.get("Retry-After") or ""
            except Exception:                          # noqa: BLE001
                retry = ""
            if not retry:
                m = re.search(r"(\d+)\s*(?:с|sec|seconds?)", detail)
                retry = m.group(1) if m else ""
            raise RuntimeError(f"HTTP {e.code}: лимит запросов шлюза"
                               + (f" (ждать {retry} с)" if retry else "")
                               ) from e
        raise RuntimeError(f"HTTP {e.code}: {detail}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"не отвечает: {e.reason}") from e
    except TimeoutError as e:
        raise RuntimeError(f"таймаут {timeout:.0f}s") from e


def _post_stream(base: str, path: str, payload: dict, headers: dict,
                  timeout: float, on_chunk: Callable[[str], None] | None = None
                  ) -> str:
    """Потоковый POST (SSE). Возвращает полный текст ответа.

    Каждый чанк вызывает on_chunk(delta) — для живого лога.
    Жёсткий лимит по времени: если модель ушла в бесконечное
    рассуждение, прерываемся по timeout, а не ждём вечно.
    """
    import time as _t
    payload = dict(payload, stream=True)
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(base.rstrip("/") + path, data=body,
                                 headers=headers, method="POST")
    full = ""
    t0 = _t.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            for raw in r:
                if _t.monotonic() - t0 > timeout:
                    raise TimeoutError(f"таймаут {timeout:.0f}s (прервано)")
                line = raw.decode("utf-8", "replace").strip()
                if not line or line.startswith(":"):
                    continue
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    obj = json.loads(data)
                except (json.JSONDecodeError, ValueError):
                    continue
                choice = (obj.get("choices") or [{}])[0]
                delta = choice.get("delta") or {}
                piece = delta.get("content") or delta.get("reasoning") or ""
                if piece:
                    full += piece
                    if on_chunk:
                        try:
                            on_chunk(piece)
                        except Exception:               # noqa: BLE001
                            pass
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        if e.code in (402, 429):
            # лимит шлюза: это не поломка, а «слишком часто». Сообщаем
            # отдельно, чтобы гонка подождала, а не долбила шлюз
            retry = ""
            try:
                retry = e.headers.get("Retry-After") or ""
            except Exception:                          # noqa: BLE001
                retry = ""
            if not retry:
                m = re.search(r"(\d+)\s*(?:с|sec|seconds?)", detail)
                retry = m.group(1) if m else ""
            raise RuntimeError(f"HTTP {e.code}: лимит запросов шлюза"
                               + (f" (ждать {retry} с)" if retry else "")
                               ) from e
        raise RuntimeError(f"HTTP {e.code}: {detail}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"не отвечает: {e.reason}") from e
    except TimeoutError as e:
        raise RuntimeError(f"таймаут {timeout:.0f}s") from e
    return full


def _headers(pk: str) -> dict:
    """Заголовки запроса.

    Ключ добавляется **только если он есть**. Раньше Bearer отправлялся
    всегда, и локальные серверы отвечали 401 — с Ollama не работало.
    """
    h = {"Content-Type": "application/json"}
    key = _nous_key() if pk == "nous" else provider_key(pk)
    if key:
        h["Authorization"] = f"Bearer {key}"
    return h


def probe(pk: str, timeout: float = 4.0) -> tuple[bool, str]:
    """Жив ли провайдер. (ok, описание)."""
    p = PROVIDERS.get(pk)
    if not p:
        return False, f"неизвестный провайдер «{pk}»"
    if p.key_required and not (provider_key(pk)
                               or (pk == "nous" and _nous_key())):
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
    #: текст взят из поля рассуждения, а не из поля ответа: у
    #: reasoning-моделей content часто пуст
    reasoning_only: bool = False


def chat(messages: list[dict], *, tools: list[dict] | None = None,
         fast: bool = False, temperature: float = 0.0,
         max_tokens: int = 900, timeout: float = 120.0,
         stream: bool = False, profile: str = "",
         response_format: dict | None = None,
         on_chunk: Callable[[str], None] | None = None) -> Turn | None:
    """Один ход диалога с необязательными инструментами.

    Аргументы приходят уже разобранными — провайдер проверяет их по
    схеме. Это заменяет прежнюю схему «вежливо попросить вернуть JSON
    и вытащить его регуляркой»: мусор в ответе больше не ломает разбор,
    а несуществующую цель провайдер не пропустит.
    """
    if not enabled():
        return None

    chain = routes(profile)
    if fast and len(chain) > 1:
        chain = chain[1:] + chain[:1]

    errors: list[str] = []
    for pk, model in chain:
        if pk not in PROVIDERS:
            continue
        if PROVIDERS[pk].key_required and not (
                provider_key(pk) or (pk == "nous" and _nous_key())):
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
        if response_format:
            # строгий JSON по схеме: модель не может ответить прозой,
            # а разбор не падает. Провайдер, который этого не умеет,
            # получит повтор без схемы (см. ниже)
            payload["response_format"] = response_format

        try:
            if stream:
                text = _post_stream(PROVIDERS[pk].base, "/chat/completions",
                                    payload, _headers(pk), timeout, on_chunk)
                _mark_alive(pk, model)
                with _LOCK:
                    _LAST_OK[_model_dead_key(pk, model)] = time.time()
                    _LAST_ERR.pop(_model_dead_key(pk, model), None)
                # вызов инструмента, написанный текстом, — тоже вызов
                got, rest = _text_tool_calls(text)
                return Turn(text=rest, provider=pk, model=model,
                            calls=got,
                            raw={"choices": [{"message": {"content": text}}]},
                            reasoning_only=bool(rest and _is_reasoning(rest)))
            try:
                data = _post(PROVIDERS[pk].base, "/chat/completions",
                             payload, _headers(pk), timeout)
            except Exception as schema_exc:
                # провайдер не умеет строгий JSON — повторяем без схемы
                low = str(schema_exc).lower()
                if response_format and ("response_format" in low
                                        or "json_schema" in low
                                        or "schema" in low):
                    payload.pop("response_format", None)
                    data = _post(PROVIDERS[pk].base, "/chat/completions",
                                 payload, _headers(pk), timeout)
                else:
                    raise
            except Exception as first_exc:                       # noqa: BLE001
                # 401 от Nous — протухший токен: обновляем через гейтчей
                # Hermes и повторяем. Иначе заказ молча умирает на середине.
                if pk == "nous" and "401" in str(first_exc):
                    fresh = _refresh_hermes_credential()
                    if not fresh:
                        raise first_exc
                    data = _post(PROVIDERS[pk].base, "/chat/completions",
                                 payload, _headers(pk), timeout)
                else:
                    raise first_exc
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


#: сколько запросов к LLM может уйти одновременно из процесса. Бесплатные
#: пулы имеют свою очередь: десяток одновременных запросов не ускоряет
#: дело, а ставит их в очередь с таймаутами.
_HTTP_SLOTS = threading.Semaphore(
    max(1, int(os.environ.get("KR_LLM_HTTP_CONCURRENCY", "4"))))


def chat_one(messages: list[dict], *, pk: str, model: str,
             tools: list[dict] | None = None,
             temperature: float = 0.0, max_tokens: int = 900,
             timeout: float = 120.0,
             stream: bool = False, profile: str = "",
             response_format: dict | None = None, probe: bool = False,
             on_chunk: Callable[[str], None] | None = None
             ) -> Turn | None:
    """Один ход у конкретного маршрута (провайдер, модель).

    Выделено из chat(), чтобы гонка (backend/llm_race.py) использовала
    ровно ту же логику запроса: заголовки, парсинг хода, отметка
    «жив/мёртв» — всё то же, без обходных путей.
    """
    if pk not in PROVIDERS:
        with _LOCK:
            _LAST_ERR[pk] = "провайдер не заведён"
        return None
    if PROVIDERS[pk].key_required and not (
            provider_key(pk) or (pk == "nous" and _nous_key())):
        with _LOCK:
            _LAST_ERR[pk] = "нет ключа провайдера"
        return None
    if _is_dead(pk, model):
        with _LOCK:
            _LAST_ERR[pk] = ("на паузе: "
                             + _DEAD_REASON.get(_model_dead_key(pk, model), "")
                             )[:160]
        return None
    payload: dict = {
        "model": model, "messages": messages,
        "temperature": temperature, "max_tokens": max_tokens,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    if response_format:
        payload["response_format"] = response_format
    try:
        if stream:
            text = _post_stream(PROVIDERS[pk].base, "/chat/completions",
                                payload, _headers(pk), timeout, on_chunk)
            _mark_alive(pk, model)
            with _LOCK:
                _LAST_OK[_model_dead_key(pk, model)] = time.time()
                _LAST_ERR.pop(_model_dead_key(pk, model), None)
            return Turn(text=text, provider=pk, model=model,
                        raw={"choices": [{"message": {"content": text}}]})
        try:
            with _HTTP_SLOTS:
                data = _post(PROVIDERS[pk].base, "/chat/completions",
                             payload, _headers(pk), timeout)
        except Exception as exc:
            # провайдер не умеет строгий JSON — повторяем без схемы
            low = str(exc).lower()
            if response_format and ("response_format" in low
                                    or "json_schema" in low
                                    or "schema" in low):
                payload.pop("response_format", None)
                data = _post(PROVIDERS[pk].base, "/chat/completions",
                             payload, _headers(pk), timeout)
            elif pk == "nous" and "401" in low:
                # токен Hermes протух: обновляем через гейтвей и повторяем.
                # Без этого гонка молча теряет все ходы подряд.
                fresh = _refresh_hermes_credential()
                if not fresh:
                    raise
                data = _post(PROVIDERS[pk].base, "/chat/completions",
                             payload, _headers(pk), timeout)
            else:
                raise
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
        elif "429" in low or "лимит запросов" in low:
            # шлюз сам называет срок («ждать 1067 с»): уважаем его и
            # переключаемся на другой маршрут немедленно, а не ждём.
            # Лимит шлюза (весь провайдер) и лимит модели (fair-share) —
            # разные вещи: модельная пауза не должна гасить провайдера.
            if probe:
                return None
            m = re.search(r"ждать\s+(\d+)\s*с", str(exc))
            if m and "шлюз" in str(exc):
                # лимит пула: ждём названный срок (но не дольше минуты) и
                # переходим к другой модели
                _mark_dead(pk, low, model)
                _mark_dead_until(pk, str(exc)[:160], float(m.group(1)))
            else:
                _mark_dead(pk, low, model)
        return None


def json_tail(text: str) -> str:
    """Достать JSON-объект из текста, даже если перед ним рассуждение.

    Многие модели отвечают так: «The user asks… Hmm… Therefore the answer
    is {…}». Такой ответ полезен: объект в конце — это и есть результат.
    Без этой функции гонка выбрасывала бы такой ответ целиком, а агент
    ждал бы следующий маршрут впустую.
    """
    s = str(text or "")
    start = s.find("{")
    while start >= 0:
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(s)):
            ch = s[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    chunk = s[start:i + 1]
                    try:
                        json.loads(chunk)
                    except (ValueError, TypeError):
                        break
                    return chunk
        start = s.find("{", start + 1)
    return ""


def _is_reasoning(text: str) -> bool:
    """Похоже ли содержимое на рассуждение, а не на ответ.

    Reasoning-модели отдают вместо ответа поток мыслей: такой текст
    бесполезен там, где ждут JSON или короткий ответ.
    """
    s = str(text or "").strip()
    if not s:
        return False
    marks = ("Let me", "Let me think", "Okay, the user", "I need to",
             "First, I", "Let me start", "Hmm,", "Let's think",
             "The user says", "The user wants", "The user is asking",
             "The user asks", "The user", "We need to", "I will",
             "I'll ", "To answer", "The task", "According to the")
    head = s[:120]
    return any(head.startswith(m) or m in head for m in marks)


#: теги вызова инструмента, которые пишут текстом: с неразрывным
#: пробелом внутри, поэтому ищем по «пустому» варианту
_TOOL_OPEN = ("<\u200btool_call>", "<tool_call>")
_TOOL_CLOSE = ("</\u200btool_call>", "</tool_call>")
_ARG_KEY = ("<\u200barg_key>", "<arg_key>")
_ARG_VAL = ("<\u200barg_value>", "<arg_value>")


def _text_tool_calls(text: str) -> tuple[list["ToolCall"], str]:
    """Вызовы инструментов, написанные текстом: [(вызовы), остаток текста].

    Модели разных семейств пишут вызов по-разному: кто-то结构но,
    кто-то тегом с парами «ключ/значение». Здесь оба варианта приводятся
    к общему виду — иначе агент теряет инструмент посреди работы.
    """
    out: list[ToolCall] = []
    rest = str(text or "")
    for open_tag in _TOOL_OPEN:
        if open_tag not in rest:
            continue
        close_tag = ("</\u200btool_call>" if "\u200b" in open_tag
                     else "</tool_call>")
        chunks = rest.split(open_tag)[1:]
        for chunk in chunks:
            body = chunk.split(close_tag)[0]
            name, args = _parse_call_body(body)
            if name:
                out.append(ToolCall(id=f"call_{len(out)}", name=name,
                                    args=args))
        rest = rest.split(open_tag)[0]
    return out, rest


def _parse_call_body(body: str) -> tuple[str, dict]:
    """Имя инструмента и аргументы из тела текстового вызова."""
    text = str(body or "").strip()
    if not text:
        return "", {}
    if text.startswith("{"):
        try:
            obj = json.loads(text)
        except ValueError:
            return "", {}
        args = obj.get("arguments") or obj.get("parameters") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except ValueError:
                args = {}
        return str(obj.get("name") or ""), (args if isinstance(args, dict)
                                             else {})
    # форма «имя<arg_key>ключ</arg_key><arg_value>значение</arg_value>…»
    name = text.split("<", 1)[0].strip()
    args: dict[str, str] = {}
    for key_open, val_open in zip(_ARG_KEY, _ARG_VAL):
        key_close = key_open.replace("arg_key", "arg_key").replace(
            "<arg_key>", "</arg_key>")
        val_close = val_open.replace("arg_value", "arg_value").replace(
            "<arg_value>", "</arg_value>")
        for part in text.split(key_open)[1:]:
            key = part.split(key_close)[0].strip()
            val = part.split(val_open)[1].split(val_close)[0].strip() \
                if val_open in part else ""
            if key:
                args[key] = val
    return name, args


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
    # reasoning-модели (kc/openrouter/free и подобные) отдают текст в
    # reasoning, а content оставляют пустым — иначе ход выглядит как
    # «модель не ответила», хотя ответ есть, просто в другом поле
    reasoning_only = False
    if not text:
        text = (msg.get("reasoning") or "").strip()
        # reasoning-модель отдаёт рассуждение вместо ответа: брать его
        # как ответ нельзя — в JSON-задаче это ломает разбор, а в
        # обычной реплике выглядит как «модель не ответила»
        reasoning_only = bool(text)

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

    # некоторые серверы (и некоторые модели в длинной сессии) вместо
    # tool_calls пишут вызов инструмента текстом — такой вызов надо
    # понять, иначе агент «ходит» по библиотеке вслепую
    if not calls:
        got, rest = _text_tool_calls(text)
        if got:
            calls.extend(got)
            text = rest

    return {"text": text, "calls": calls, "provider": pk, "model": model,
            "raw": data, "reasoning_only": reasoning_only}


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
                provider_key(pk) or (pk == "nous" and _nous_key())):
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
            try:
                data = _post(PROVIDERS[pk].base, "/chat/completions",
                             {"model": model, "messages": msgs,
                              "temperature": temperature,
                              "max_tokens": max_tokens},
                             _headers(pk), timeout)
            except Exception as first_exc:                       # noqa: BLE001
                # 401 от Nous — протухший токен. Обновляем через гейтчей
                # Hermes и повторяем запрос: заказ не должен падать из-за
                # того, что час пролетел с прошлого вызова.
                if pk == "nous" and "401" in str(first_exc):
                    fresh = _refresh_hermes_credential()
                    if fresh:
                        data = _post(PROVIDERS[pk].base, "/chat/completions",
                                     {"model": model, "messages": msgs,
                                      "temperature": temperature,
                                      "max_tokens": max_tokens},
                                     _headers(pk), timeout)
                    else:
                        raise first_exc
                else:
                    raise first_exc
            # reasoning-модели (например kc/openrouter/free) могут отдать
            # текст в reasoning, а content оставить null — иначе ответ
            # выглядит как «модель не ответила»
            m = data["choices"][0]["message"]
            text = (m.get("content") or "").strip() \
                or (m.get("reasoning") or "").strip()
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
              "key_set": bool(provider_key(pk) or _nous_key()),
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

    if not _nous_key():
        print("\n  Бесплатное облако без карты — Nous Portal:")
        print("    1. зарегистрироваться на portal.nousresearch.com")
        print("    2. создать ключ в разделе API keys")
        print("    3. export NOUS_PORTAL_API_KEY=<ключ>")
        print("    4. KR_LLM_PRESET=nous make llm-check")
        print("    Модели помечены суффиксом :free и не тарифицируются.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())