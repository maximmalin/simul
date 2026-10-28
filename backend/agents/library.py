"""Инструменты библиотекаря: модели SPICE и даташиты деталей.

Задача библиотекаря — довести каждую деталь схемы до состояния, в
котором она участвует в симуляции:

* найти готовую модель в библиотеке;
* если модели нет — взять даташит и собрать макромодель самому;
* положить результат во внутреннюю библиотеку (она растёт);
* согласовать выводы модели с выводами символа KiCad;
* проверить, что модель разбирается симулятором.

Правило согласования выводов (одно на все случаи, из опыта ngspice и
KiCad): узлы ``.subckt`` объявляются в том же порядке, что и выводы
символа KiCad. Тогда сопоставление выводов — тождественное, и KiCad не
путает распиновку. Если модель объявляет меньше выводов, чем символ,
пишется обёртка ``.subckt`` с распиновкой символа, а внутрь ставится
исходная модель.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import urllib.request
from pathlib import Path

#: корень проекта (design/, data/, tools/ лежат рядом)
ROOT = Path(__file__).resolve().parents[2]

#: реестр источников — дополняется без правки кода
SOURCES_JSON = ROOT / "data" / "model_sources.json"


def _sources() -> dict:
    try:
        return json.loads(SOURCES_JSON.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _rules() -> dict:
    return (_sources().get("правила") or {})


def _lib_dir() -> Path:
    """Внутренняя библиотека моделей."""
    libs = _sources().get("библиотеки") or []
    if libs:
        p = ROOT / str(libs[0].get("путь") or "design/spice_lib")
        if p.is_dir():
            return p
    return ROOT / "design" / "spice_lib"


def _http_get(url: str, timeout: int = 60) -> bytes:
    """Загрузка по HTTP с браузерным User-Agent."""
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/120 Safari/537.36",
        "Accept": "*/*",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return bytes(r.read())


def _http_get_impersonated(url: str, timeout: int = 60) -> bytes:
    """Загрузка с отпечатком браузера (обход антибот-фильтров)."""
    try:
        from curl_cffi import requests as creq        # noqa: PLC0415
        r = creq.get(url, impersonate="chrome", timeout=timeout,
                     allow_redirects=True)
        if r.status_code == 200:
            return bytes(r.content)
    except Exception:                                  # noqa: BLE001
        pass
    return _http_get(url, timeout=timeout)


# ==========================================================================
#  Модели: положить, скачать, проверить
# ==========================================================================
def _reset_index() -> None:
    """Сбросить кэши индекса моделей: библиотека изменилась.

    Кэшей два: у модуля spice_models (он строит индекс) и у tools
    (он им пользуется при поиске). Сбрасывать надо оба, иначе скачанная
    модель не появится в поиске до перезапуска процесса.
    """
    try:
        from . import spice_models as SM               # noqa: PLC0415
        SM.reset_index()
    except Exception:                                  # noqa: BLE001
        pass
    try:
        from . import tools as T                      # noqa: PLC0415
        T._SPICE_INDEX = None
        T._SPICE_INDEX_TXT = None
    except Exception:                                  # noqa: BLE001
        pass


def _spice_put(text: str, name: str = "") -> str:
    """Положить модель во внутреннюю библиотеку.

    Собранная библиотекарём макромодель — не временный файл, а часть
    базы: с следующего раза она доступна всем изделиям.
    """
    text = (text or "").strip()
    if not text:
        return json.dumps({"error": "пустой текст модели"}, ensure_ascii=False)
    rules = _rules()
    sub = str(rules.get("каталог_собранных") or "design/spice_lib/collected")
    d = ROOT / sub
    d.mkdir(parents=True, exist_ok=True)
    stem = re.sub(r"[^A-Za-z0-9_.+\-]+", "_", name or "model").strip("_")
    if not stem:
        stem = "model"
    path = d / f"{stem}.lib"
    body = text if text.lower().startswith("*") else f"* модель {stem}\n{text}"
    path.write_text(body if body.endswith("\n") else body + "\n",
                    encoding="utf-8")
    _reset_index()
    return json.dumps({"ok": True, "файл": str(path.relative_to(ROOT)),
                       "подключить": f".include \"{path}\""},
                      ensure_ascii=False)


def _raw_url(url: str) -> str:
    """Ссылка на файл в репозитории → прямая ссылка на файл.

    У GitHub страница файла отдаёт HTML, а нужен сам текст модели,
    поэтому blob-ссылка превращается в raw-ссылку.
    """
    u = str(url or "").strip()
    for marker in ("github.com/", "gitlab.com/"):
        if marker not in u:
            continue
        head, _, tail = u.partition(marker)
        parts = tail.split("/")
        if len(parts) >= 5 and parts[2] in ("blob", "raw", "-/blob"):
            host = "raw.githubusercontent.com" if "github.com" in u \
                else "gitlab.com"
            # owner/repo остаются, дальше — путь к файлу
            rest = "/".join(parts[:2] + parts[3:])
            if parts[2] == "-/blob":
                rest = "/".join(parts[:2] + parts[4:])
            return f"{head}{host}/{rest}"
    return u


def _spice_fetch(url: str, name: str = "") -> str:
    """Скачать готовую модель по ссылке и положить во внутреннюю БД."""
    url = (url or "").strip()
    if not url:
        return json.dumps({"error": "нужен url"}, ensure_ascii=False)
    url = _raw_url(url)
    try:
        data = _http_get_impersonated(url)
    except Exception as exc:                          # noqa: BLE001
        return json.dumps({"error": f"не скачалось: {exc}"}, ensure_ascii=False)
    text = data.decode("utf-8", "ignore")
    if ".model" not in text.lower() and ".subckt" not in text.lower():
        return json.dumps({"error": "по ссылке не модель SPICE",
                           "байт": len(data)}, ensure_ascii=False)
    stem = name or Path(url.split("?")[0]).stem or "model"
    out = _spice_put(text, stem)
    try:
        res = json.loads(out)
    except json.JSONDecodeError:
        return out
    res["источник"] = url
    res["найдено_моделей"] = len(re.findall(r"(?im)^\s*\.(?:model|subckt)\s",
                                            text))
    return json.dumps(res, ensure_ascii=False)


def _spice_check(name: str, deck: str = "") -> str:
    """Проверить модель: разбирается ли симулятором и сколько выводов.

    Проверка нативная: модель включается в деку и прогоняется через
    ngspice. Модель, которую симулятор не понял, в изделии работать не
    будет — это надо знать до сборки схемы.

    Для подсхемы каркас собирает проверку сам (вызов X с её выводами).
    Для карточки прибора вид вызова знает только инженер: каркас
    спрашивает агента — за что отвечает прибор (D, Q, M…), — либо
    агент присылает готовую проверочную деку.
    """
    name = (name or "").strip()
    if not name:
        return json.dumps({"error": "нужно имя модели"}, ensure_ascii=False)
    from . import tools as T                         # noqa: PLC0415
    from . import spice_models as SM                # noqa: PLC0415
    lib = T.SPICE_LIB_DIR
    text = SM.model_text(name, lib)
    if not text:
        return json.dumps({"ok": False, "модель": name,
                           "ошибка": "модели нет в библиотеке"},
                          ensure_ascii=False)
    nodes = SM.subckt_pins(name, lib)
    res: dict = {"модель": name, "узлы": nodes,
                 "подсхема": bool(nodes),
                 "текст": text[:4000]}
    if not nodes:
        res["подсказка"] = ("это карточка прибора (.model): вызови её "
                             "в проверочной деке сам — вид вызова зависит "
                             "от типа прибора, он указан в тексте модели")
    ng = shutil.which("ngspice") or shutil.which("ngspice-bin")
    if not ng:
        res["проверка"] = "ngspice не установлен — модель не проверена"
        return json.dumps(res, ensure_ascii=False)
    work = ROOT / "design" / ".model_check"
    work.mkdir(parents=True, exist_ok=True)
    libfile = work / "m.lib"
    libfile.write_text(text, encoding="utf-8")
    deckfile = work / "d.cir"
    if deck:
        body = deck
    elif nodes:
        body = (f"X1 {' '.join(str(i + 1) for i in range(len(nodes)))} "
                f"{name}\n")
    else:
        body = f"* проверка карточки {name}\n"
    deckfile.write_text(
        "* проверка модели\n"
        f".include \"{libfile}\"\n"
        f"{body}"
        ".op\n"
        ".end\n", encoding="utf-8")
    try:
        r = subprocess.run([ng, "-b", str(deckfile)], capture_output=True,
                           text=True, timeout=60, cwd=str(work))
        out = (r.stdout or "") + (r.stderr or "")
    except Exception as exc:                          # noqa: BLE001
        res["проверка"] = f"ngspice не отработал: {exc}"
        return json.dumps(res, ensure_ascii=False)
    bad = [ln for ln in out.splitlines()
           if re.search(r"(?i)\berror\b|unknown|not found|cannot|too (few|many)",
                        ln)]
    res["ok"] = not bad
    if bad:
        res["ошибка"] = "; ".join(bad[:3])[:300]
    else:
        res["проверка"] = "ngspice разобрал модель"
    return json.dumps(res, ensure_ascii=False)


# ==========================================================================
#  Даташиты
# ==========================================================================
def _pdf_text(path: Path) -> str:
    """Текст PDF: poppler pdftotext с сохранением колонок.

    ``-layout`` нужен для таблиц «Electrical Characteristics»: без него
    колонки слипаются и параметры не читаются.
    """
    exe = shutil.which("pdftotext")
    if not exe:
        return ""
    out = path.with_suffix(".txt")
    try:
        subprocess.run([exe, "-layout", str(path), str(out)],
                       capture_output=True, text=True, timeout=180)
    except Exception:                                  # noqa: BLE001
        return ""
    try:
        return out.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def _datasheet_resolve(part: str) -> str:
    """Найти ссылку на даташит детали.

    Путь универсальный: свойство Datasheet символа KiCad (оно есть у
    почти всех деталей каталога), затем каталог заказа, затем LCSC.
    """
    part = (part or "").strip()
    if not part:
        return json.dumps({"error": "нужно имя детали"}, ensure_ascii=False)
    out: list[dict] = []
    try:
        from . import tools as T                       # noqa: PLC0415
        d = json.loads(T._resolve_part(part))
        for v in (d.get("варианты") or [])[:4]:
            url = str(v.get("Datasheet") or "").strip()
            if url:
                out.append({"источник": "свойство символа",
                            "lib_id": v.get("lib_id"), "url": url})
    except Exception:                                  # noqa: BLE001
        pass
    if not out:
        out = _datasheet_from_catalog(part)
    if not out:
        return json.dumps({"деталь": part, "найдено": 0,
                           "подсказка": "ссылки нет — ищи сам: прямые PDF "
                                       "вендоров и каталоги с даташитами"},
                          ensure_ascii=False)
    return json.dumps({"деталь": part, "найдено": len(out),
                       "варианты": out}, ensure_ascii=False)


def _datasheet_from_catalog(part: str) -> list[dict]:
    """Ссылка на даташит из каталога заказа (design/libs)."""
    res: list[dict] = []
    key = part.lower()
    for base in (ROOT / "design" / "libs", ROOT / "design" / "parts"):
        if not base.is_dir():
            continue
        for p in base.rglob("*.json"):
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            blob = json.dumps(d, ensure_ascii=False).lower()
            if key in blob and "datasheet" in blob:
                m = re.search(r'"datasheet"\s*:\s*"([^"]+)"', blob)
                if m:
                    res.append({"источник": "каталог", "url": m.group(1),
                                "файл": str(p.relative_to(ROOT))})
    return res[:5]


def _datasheet_text(file: str) -> str:
    """Текст уже скачанного даташита (PDF → текст с колонками)."""
    p = Path(file)
    if not p.is_absolute():
        p = ROOT / file
    if not p.is_file():
        return json.dumps({"error": f"нет файла {file}"}, ensure_ascii=False)
    if p.suffix.lower() == ".pdf":
        text = _pdf_text(p)
        if not text:
            return json.dumps({"ошибка": "pdftotext не справился"},
                              ensure_ascii=False)
        return json.dumps({"файл": str(p), "символов": len(text),
                           "текст": text[:200000]}, ensure_ascii=False)
    text = p.read_text(encoding="utf-8", errors="ignore")
    return json.dumps({"файл": str(p), "символов": len(text),
                       "текст": text[:200000]}, ensure_ascii=False)


def _datasheet_fetch(part: str = "", url: str = "", file: str = "") -> str:
    """Скачать даташит и вернуть его текст.

    Ссылка берётся из resolve, если не задана; скачивание идёт с
    браузерным отпечатком TLS (вендорские сайты часто отбивают
    обычный urllib).
    """
    if file:
        return _datasheet_text(file)
    if not url:
        r = json.loads(_datasheet_resolve(part))
        variants = r.get("варианты") or []
        if not variants:
            return json.dumps(r, ensure_ascii=False)
        url = str(variants[0].get("url") or "")
    try:
        data = _http_get_impersonated(url)
    except Exception as exc:                          # noqa: BLE001
        return json.dumps({"ошибка": f"не скачалось: {exc}",
                           "url": url}, ensure_ascii=False)
    rules = _rules()
    sub = str(rules.get("каталог_загруженных") or "design/spice_lib/downloaded")
    d = ROOT / sub
    d.mkdir(parents=True, exist_ok=True)
    stem = re.sub(r"[^A-Za-z0-9_.+\-]+", "_",
                  part or Path(url.split("?")[0]).stem or "datasheet")
    if data[:5] == b"%PDF-":
        path = d / f"{stem}.pdf"
        path.write_bytes(data)
        text = _pdf_text(path)
        return json.dumps({"файл": str(path.relative_to(ROOT)),
                           "url": url, "символов": len(text),
                           "текст": text[:200000]}, ensure_ascii=False)
    path = d / f"{stem}.html"
    path.write_bytes(data)
    text = data.decode("utf-8", "ignore")
    return json.dumps({"файл": str(path.relative_to(ROOT)), "url": url,
                       "символов": len(text), "текст": text[:200000]},
                      ensure_ascii=False)
