"""Движок подбора деталей и моделей симуляции — ПО ПАРАМЕТРАМ.

Задача этапа: по требованиям расчёта («MOSFET, I ≥ 1.5 А, Vds ≥ 20 В,
Rds(on) ≤ 0.5 Ом») назвать конкретную деталь и модель, по которой её можно
посчитать в симуляции.

Источники — только те, что работают без регистрации:

  * локальная база JLCPCB (`jlcparts`) — параметрический поиск по
    номиналу, корпусу, категории и характеристикам; из неё же берутся
    реальные артикул, MPN, запас и цена;
  * jlcsearch (tscircuit) — тот же поиск в сети, когда в локальной базе
    нужного класса нет;
  * LCSC wmsc — карточка детали с полным списком параметров
    (`paramVOList`), доступна без ключа;
  * KiCad-Spice-Library — модели SPICE реальных приборов; модель ищется
    по номеру изготовителя, а если номер не найден — по параметрам самой
    модели (карточки .model/.subckt).

Ничего не выдумывается: каждая найденная деталь — запись базы с
существующим артикулом, каждая модель — текст из библиотеки моделей.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

#: локальная база JLCPCB (yaqwsx/jlcparts) — параметрический поиск
JLCPARTS_DB = Path(os.environ.get(
    "KR_JLCPARTS_DB", "/mnt/ext4data/jlcparts/cache/cache.sqlite3"))

#: производные таблицы jlcsearch — параметры по классам
JLCSEARCH_DB = Path(os.environ.get(
    "KR_JLCSEARCH_DB", str(ROOT / "hardware" / "tscircuit" / "out"
                           / "jlcsearch.db")))

#: публичный API jlcsearch — без ключа
JLCSEARCH_API = "https://jlcsearch.tscircuit.com"

#: карточка детали LCSC — без ключа
LCSC_DETAIL = "https://wmsc.lcsc.com/ftps/wm/product/detail?productCode="

#: библиотека моделей SPICE
SPICE_LIB = Path(os.environ.get(
    "KR_SPICE_LIB", str(ROOT / "design" / "spice_lib")))

_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


# --------------------------------------------------------------------------- #
#  Параметры подбора — в JSON, не в коде
# --------------------------------------------------------------------------- #
#: файл параметров: алиасы параметров, колонки таблиц, правила сравнения.
#: Открыт для дополнения: новый параметр добавляется строкой в JSON.
PARAMS_FILE = Path(os.environ.get(
    "KR_PARTS_PARAMS", Path(__file__).resolve().parent / "parts_params.json"))

_CFG: dict = {}


def _deep_merge(base: dict, over: dict) -> dict:
    """Слияние словарей: значения over заменяют base, словари — рекурсивно."""
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _load_cfg() -> dict:
    """Правила подбора из JSON (с дополнением из KR_PARTS_PARAMS)."""
    global _CFG
    if _CFG:
        return _CFG
    data: dict = {}
    for path in (PARAMS_FILE,):
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        break
    extra = os.environ.get("KR_PARTS_PARAMS_EXTRA")
    if extra and Path(extra).is_file():
        try:
            data = _deep_merge(data, json.loads(Path(extra).read_text("utf-8")))
        except (OSError, ValueError):
            pass
    _CFG = data
    return _CFG


def cfg(key: str, default):
    """Раздел конфигурации подбора."""
    return _load_cfg().get(key, default)

# --------------------------------------------------------------------------- #
#  Числовые величины: из текста параметра — число с единицей
# --------------------------------------------------------------------------- #
_UNITS: dict[str, float] = {
    "v": 1.0, "volt": 1.0, "в": 1.0, "вольт": 1.0,
    "a": 1.0, "amp": 1.0, "а": 1.0, "ампер": 1.0,
    "w": 1.0, "watt": 1.0, "вт": 1.0, "ватт": 1.0,
    "hz": 1.0, "гц": 1.0,
    "f": 1.0, "ф": 1.0, "фарад": 1.0,
    "h": 1.0, "гн": 1.0, "гн": 1.0,
    "ohm": 1.0, "ом": 1.0,
    "%": 1.0, "процент": 1.0,
}
#: множители порядков (k, м, u, n…) — из data/units.json
def _scales() -> dict:
    return _units_cfg().get("scale") or {
        "k": 1e3, "к": 1e3, "m": 1e-3, "м": 1e-3, "u": 1e-6, "µ": 1e-6,
        "μ": 1e-6, "мк": 1e-6, "n": 1e-9, "н": 1e-9, "p": 1e-12, "п": 1e-12,
        "g": 1e9, "г": 1e9, "meg": 1e6,
    }

_NUM_RE = re.compile(
    r"(-?\d+(?:[.,]\d+)?)\s*"
    r"(к|мк|м|n|н|p|п|g|г|u|µ|μ|m|k|M|MEG)?\s*"
    r"(ом|Ω|%|вт|ватт|вольт|ампер|фарад|гн|f|hz|гц|w|a|v|f|h|ом|ohm)?",
    re.I)


def parse_number(text: str) -> float | None:
    """Число из текста параметра: «1.5 A», «600 mW», «20 В»."""
    m = _NUM_RE.search(str(text or ""))
    if not m:
        return None
    try:
        val = float(m.group(1).replace(",", "."))
    except ValueError:
        return None
    prefix = (m.group(2) or "").lower()
    val *= _scales().get(prefix, 1.0)
    unit = (m.group(3) or "").lower()
    val *= _UNITS.get(unit, 1.0)
    return val


#: единицы измерения и их канонический вид — общий файл (data/units.json).
#: Кириллица и латиница — одна и та же единица («А» и «A», «Вт» и «W»),
#: поэтому сравнивать их надо по канону, а не по строке.
UNITS_FILE = Path(os.environ.get(
    "KR_UNITS_FILE", Path(__file__).resolve().parents[2] / "data" / "units.json"))


def _units_cfg() -> dict:
    """Справочник единиц измерения (data/units.json)."""
    try:
        return json.loads(UNITS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def unit_key(text: str) -> str:
    """Каноническая единица измерения по её записи («Вт» → «W»)."""
    cfg = _units_cfg()
    canon = cfg.get("canonical") or {}
    s = re.sub(r"[^a-zа-я%ωΩ]+", "", str(text or "").lower())
    if not s:
        return ""
    if s in canon:
        return canon[s]
    best = ""
    for k, v in canon.items():
        if len(k) < 2 or not k:
            continue
        if (s.startswith(k) or s.endswith(k)) and len(k) > len(best):
            best = k
            best = v
    return best or s


def _norm(text: str) -> str:
    """Ключ параметра: без пробелов, в нижнем регистре."""
    return re.sub(r"[^a-zа-я0-9]+", "", str(text or "").lower())


#: синонимы названий параметров → канонический ключ
def param_key(name: str) -> str:
    """Канонический ключ параметра (алиасы — в parts_params.json)."""
    n = _norm(name)
    aliases = cfg("aliases", {}).get("requirement", {})
    return aliases.get(n, n)


# --------------------------------------------------------------------------- #
#  Локальная база JLCPCB
# --------------------------------------------------------------------------- #
def _jlc_rows(sql: str, args: tuple) -> list[dict]:
    """Строки из основной базы JLCPCB.

    Ошибка запроса не молчит: пустой результат без причины выглядит
    как «деталей нет», и тогда молча уходишь в сеть мимо своей базы.
    """
    if not JLCPARTS_DB.is_file():
        return []
    try:
        con = sqlite3.connect(f"file:{JLCPARTS_DB}?mode=ro", uri=True)
        try:
            con.row_factory = sqlite3.Row
            return [dict(r) for r in con.execute(sql, args).fetchall()]
        finally:
            con.close()
    except sqlite3.Error as exc:
        print(f"[parts] запрос к базе JLCPCB не удался: {exc}")
        return []


def _jlcsearch_rows(table: str, sql: str, args: tuple) -> list[dict]:
    """Строки из производных таблиц jlcsearch."""
    if not JLCSEARCH_DB.is_file():
        return []
    try:
        con = sqlite3.connect(f"file:{JLCSEARCH_DB}?mode=ro", uri=True)
        try:
            con.row_factory = sqlite3.Row
            return [dict(r) for r in con.execute(sql, args).fetchall()]
        finally:
            con.close()
    except sqlite3.Error as exc:
        print(f"[parts] запрос к производным таблицам не удался: {exc}")
        return []


def _table_for(cat: str) -> str:
    """Класс детали → таблица (parts_params.json)."""
    return cfg("tables", {}).get(cat, "part")

def _col_filter(cat: str) -> tuple:
    """Колонки, по которым фильтруют таблицу класса (parts_params.json)."""
    return tuple(cfg("filters", {}).get(cat, ()))


def _col_range(col: str) -> tuple | None:
    """Допустимый диапазон значения колонки (parts_params.json)."""
    r = cfg("ranges", {}).get(col)
    return (float(r[0]), float(r[1])) if r else None


def _value_col(cat: str) -> str | None:
    """Колонка со значением номинала для класса (parts_params.json)."""
    return cfg("value_col", {}).get(cat)


#: названия параметров в карточке детали → канонический ключ.
#: Источник — поле attributes базы JLCPCB: там лежат настоящие
#: характеристики («Drain Source Voltage(Vdss)», «Rds On (Max)@Id,Vgs»).
def _attr_key(name: str) -> str:
    """Канонический ключ по названию характеристики.

    Сначала точное совпадение с алиасом (parts_params.json). Если его
    нет — самый длинный алиас, входящий в название: у изготовителей
    одна и та же величина называется по-разному («Voltage(DC) Reverse
    (VR)», «Voltage Rating», «Rated Voltage»), и разбирать каждое
    написание в коде бессмысленно.
    """
    n = _norm(name)
    aliases = cfg("aliases", {}).get("attribute", {})
    if n in aliases:
        return aliases[n]
    best, best_len = "", 0
    for alias, key in aliases.items():
        if len(alias) < 4 or alias not in n:
            continue
        if len(alias) > best_len:
            best, best_len = key, len(alias)
    if best:
        return best
    # обратное совпадение: алиас длиннее названия
    for alias, key in aliases.items():
        if len(alias) >= 4 and n in alias and len(alias) > best_len:
            best, best_len = key, len(alias)
    return best or n


def _attrs_of(row: dict) -> dict[str, str]:
    """Параметры детали из поля attributes (JSON со строками вида «1.2kΩ»)."""
    raw = row.get("attributes")
    if not raw:
        return {}
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except (ValueError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {_attr_key(k): str(v) for k, v in data.items()}


def select(category: str, value: str = "", package: str = "",
           params: dict | None = None, limit: int = 8,
           in_stock: bool = True) -> list[dict]:
    """Подбор детали по параметрам.

    category — класс детали: r, c, l, d, led, fuse, crystal, part
    value    — номинал в любой записи: «10k», «100n», «1 A»
    params   — требования: {"vds": "20 V", "id": "1.5 A", ...}
    package  — корпус: «0805», «SOT-23», «0603»

    Возвращает реальные записи с артикулом, MPN, корпусом, запасом и
    описанием. Фильтры уходят в SQL, а параметры сверяются по полю
    attributes: там лежат настоящие характеристики детали.

    Если локальной базы нет или она пуста — идёт в сеть (jlcsearch, без
    ключа).
    """
    params = {param_key(k): v for k, v in (params or {}).items()}
    # класс приводится к имени таблицы: «r» → «resistor», «mosfet» → «part»
    cat = _table_for(_norm(category))
    table = cat

    # --- 1. основная база: у каждой детали есть карточка характеристик ---
    mwhere: list[str] = []
    margs: list = []
    if in_stock:
        mwhere.append("COALESCE(stock, 0) > 0")
    if package:
        mwhere.append("LOWER(package) LIKE ?")
        margs.append(f"%{package.lower()}%")
    if cat != "part":
        mwhere.append("LOWER(category) LIKE ?")
        margs.append(f"%{_category_hint(cat)}%")
    rows = _jlc_rows(
        "SELECT lcsc, mfr, manufacturer, package, description, category, "
        "subcategory, stock, price, datasheet, attributes "
        "FROM jlc_components WHERE "
        + (" AND ".join(mwhere) if mwhere else "1=1")
        + " LIMIT ?", tuple(margs + [max(limit * 60, 600)]))
    if rows:
        for r in rows:
            r["source"] = "локальная база"
        out = _match_by_attrs(rows, params, limit, cat, value)
        if out:
            return out

    # --- 2. производные таблицы jlcsearch: параметры приведены к типу ---
    where: list[str] = []
    args: list = []
    if in_stock:
        where.append("COALESCE(stock, 0) > 0")
    if package:
        where.append("LOWER(package) LIKE ?")
        args.append(f"%{package.lower()}%")
    lo_hi = _value_bounds(cat, value)
    vcol = _value_col(cat)
    if lo_hi and vcol:
        lo, hi = lo_hi
        where.append(f"{vcol} BETWEEN ? AND ?")
        args += [lo * 0.98, hi * 1.02]
    for key, want in params.items():
        wv = parse_number(want)
        if wv is None:
            continue
        wv = _as_fraction(key, wv)
        col = _sql_col(cat, key)
        if not col:
            continue
        cmp_sql, cmp_args = _cmp_sql(col, key, wv)
        where.append(cmp_sql)
        args += cmp_args
    sql = f'SELECT * FROM "{table}"'
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY is_preferred DESC, is_basic DESC, stock DESC LIMIT ?"
    args.append(max(limit * 8, 40))
    rows = _jlcsearch_rows(table, sql, tuple(args))
    if rows:
        out: list[dict] = []
        for r in rows:
            score = _score_row(cat, r, params)
            if score is None:
                continue
            out.append({**r, "score": score, "source": "производные таблицы"})
        if out:
            out.sort(key=lambda x: (-x["score"], -int(x.get("stock") or 0)))
            return out[:limit]
    # --- 3. сеть: jlcsearch, без ключа ---
    return _select_web(category, value, package, params, limit, in_stock)


def _category_hint(cat: str) -> str:
    """Класс детали → подсказка категории в основной базе."""
    return cfg("category_hint", {}).get(cat, cat)


def _match_by_attrs(rows: list[dict], params: dict, limit: int,
                    cat: str = "", value: str = "") -> list[dict]:
    """Отбор по характеристикам детали (поле attributes).

    Номинал сверяется тоже по характеристикам: у резистора это
    «Resistance», у конденсатора «Capacitance» — в карточке они записаны
    так же, как в требовании («10kΩ», «100nF»), и разбирает их один и тот
    же parse_number.
    """
    want_val = parse_number(value) if value else None
    val_key = {"resistor": "r", "capacitor": "c", "inductor": "l"}.get(cat)
    if not params and want_val is None:
        return rows[:limit]
    out: list[dict] = []
    for r in rows:
        attrs = _attrs_of(r)
        score = 0
        ok = True
        if want_val is not None and val_key:
            got = parse_number(attrs.get(val_key, ""))
            if got is None or abs(got - want_val) > max(
                    abs(want_val) * 0.02, 1e-12):
                continue
        for key, want in params.items():
            wv = parse_number(want)
            if wv is None:
                continue
            wv = _as_fraction(key, wv)
            got = parse_number(attrs.get(key, ""))
            if got is None:
                ok = False
                break
            got = _as_fraction(key, got)
            if not _cmp_ok(key, got, wv):
                ok = False
                break
            score += 1
        if ok:
            out.append({**r, "score": score, "attrs": attrs})
    out.sort(key=lambda x: (-x["score"], -int(x.get("stock") or 0)))
    return out[:limit]


def _sql_col(cat: str, key: str) -> str | None:
    """Колонка таблицы для параметра (parts_params.json)."""
    return cfg("columns", {}).get(cat, {}).get(key)


def _value_bounds(cat: str, value: str) -> tuple[float, float] | None:
    """Диапазон номинала: «10k» → ровно 10 кОм, «4.7u..10u» → диапазон."""
    if not value:
        return None
    if ".." in value:
        a, _, b = value.partition("..")
        lo, hi = parse_number(a), parse_number(b)
        if lo is not None and hi is not None:
            return (min(lo, hi), max(lo, hi))
        return None
    v = parse_number(value)
    return (v, v) if v is not None else None


def _row_value(cat: str, r: dict) -> float | None:
    """Номинал детали из строки таблицы (в базовых единицах)."""
    col = _value_col(cat)
    if col and r.get(col) is not None:
        try:
            return float(r[col])
        except (TypeError, ValueError):
            pass
    return None


def _score_row(cat: str, r: dict, params: dict) -> int | None:
    """Годится ли деталь по требованиям. None — не годится."""
    if not params:
        return 0
    for key, want in params.items():
        wv = parse_number(want)
        if wv is None:
            continue
        wv = _as_fraction(key, wv)
        got = _row_param(cat, r, key)
        if got is None:
            return None                      # параметр неизвестен — не рискуем
        lim = _col_range(key)
        if lim and wv < lim[0]:
            continue
        if not _cmp_ok(key, got, wv):
            return None
    return len(params)


#: единица измерения → канонический параметр
_UNIT_KEY: dict[str, str] = {
    "a": "i", "ma": "i", "ua": "i", "na": "i",
    "v": "v", "mv": "v", "kv": "v",
    "w": "power", "mw": "power", "uw": "power",
    "nm": "wl", "um": "c", "nf": "c", "pf": "c", "uf": "c", "mf": "c",
    "r": "r", "k": "r", "m": "r", "ohm": "r", "ω": "r",
    "hz": "f", "khz": "f", "mhz": "f", "ghz": "f",
}

#: число с единицей в свободном тексте: «350mA», «40 V», «455nm»
_NUM_UNIT_RE = re.compile(
    r"(-?\d+(?:[.,]\d+)?)\s*"
    r"(к|мк|м|n|н|p|п|g|г|u|µ|μ|m|k|M)?\s*"
    r"(A|mA|uA|nA|V|mV|kV|W|mW|uW|nm|um|nF|pF|uF|mF|k|M|Ω|ohm|Hz|kHz|MHz|GHz)?",
    re.I)


def _numbers_by_unit(text: str) -> dict[str, list[float]]:
    """Числа из свободного текста, разложенные по единицам измерения.

    В описании позиции характеристики записаны вперемешку («350mA,
    2.15V, 455nm~470nm»). Единица измерения говорит, о какой величине
    речь, — по ней и выбирается значение параметра.
    """
    out: dict[str, list[float]] = {}
    for m in _NUM_UNIT_RE.finditer(str(text or "")):
        num = m.group(1).replace(",", ".")
        prefix = (m.group(2) or "").lower()
        unit = (m.group(3) or "").lower()
        if not unit:
            continue
        try:
            val = float(num) * _SCALES.get(prefix, 1.0)
        except ValueError:
            continue
        key = _UNIT_KEY.get(unit)
        if key:
            out.setdefault(key, []).append(val)
    return out


def _row_param(cat: str, r: dict, key: str) -> float | None:
    """Значение параметра детали из строки таблицы.

    Сначала — своя колонка параметра (по карте parts_params.json),
    потом — любая колонка класса с тем же каноническим ключом, потом —
    разбор описания: «60 V», «2.5 A» прямо в тексте позиции.
    """
    col = _sql_col(cat, key)
    if col and r.get(col) is not None:
        try:
            return float(r[col])
        except (TypeError, ValueError):
            pass
    for c2 in _col_filter(cat):
        if r.get(c2) is None:
            continue
        for k2, cc in cfg("columns", {}).get(cat, {}).items():
            if cc == c2 and k2 == key:
                try:
                    return float(r[c2])
                except (TypeError, ValueError):
                    break
    # параметры в описании/названии: «60 V», «2.5 A»
    blob = f"{r.get('mfr') or ''} {r.get('description') or ''}"
    # сначала — по единице измерения: «350mA» это ток, «455nm» — длина
    by_unit = _numbers_by_unit(blob)
    if key in by_unit and by_unit[key]:
        return max(by_unit[key]) if key in ("i", "vr", "v", "vds", "power",
                                           "vth", "ic", "id") \
            else min(by_unit[key])
    pat = {
        "vds": r"(\d+(?:\.\d+)?)\s*V\b", "id": r"(\d+(?:\.\d+)?)\s*A\b",
        "ic": r"(\d+(?:\.\d+)?)\s*A\b", "i": r"(\d+(?:\.\d+)?)\s*A\b",
        "vr": r"(\d+(?:\.\d+)?)\s*V\b", "vf": r"(\d+(?:\.\d+)?)\s*V\b",
        "v": r"(\d+(?:\.\d+)?)\s*V\b",
        "power": r"(\d+(?:\.\d+)?)\s*W\b", "rdson": r"(\d+(?:\.\d+)?)\s*(?:m?ohm|Ω)",
        "c": r"(\d+(?:\.\d+)?)\s*(?:p|n|u|m)?F\b",
        "r": r"(\d+(?:\.\d+)?)\s*(?:k|m|u|M)?[Ωohm]\b",
        "tol": r"[±+\-]\s*(\d+(?:\.\d+)?)\s*%",
    }.get(key)
    if pat:
        m = re.search(pat, blob, re.I)
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                return None
    return None


def _direction(key: str) -> str:
    """Направление требования: min (не меньше), max (не больше), eq."""
    d = cfg("direction", {})
    if key in d.get("max", ()):
        return "max"
    if key in d.get("min", ()):
        return "min"
    return "eq"


def _cmp_ok(key: str, got: float, want: float) -> bool:
    """Годится ли значение детали под требование."""
    direction = _direction(key)
    if direction == "max":
        return got <= want
    if direction == "min":
        return got >= want
    return abs(got - want) <= max(abs(want) * 0.15, 1e-12)


def _cmp_sql(col: str, key: str, want: float) -> tuple[str, list]:
    """SQL-условие по требованию."""
    direction = _direction(key)
    if direction == "max":
        return f"({col} IS NOT NULL AND {col} <= ?)", [want]
    if direction == "min":
        return f"({col} IS NOT NULL AND {col} >= ?)", [want]
    return (f"({col} IS NOT NULL AND ABS({col} - ?) <= ?)",
            [want, max(abs(want) * 0.15, 1e-12)])


def _as_fraction(key: str, value: float) -> float:
    """Допуск в требовании дан в процентах, в базе — доля."""
    if key in cfg("fraction_keys", ["tol", "tolerance"]):
        return value / 100.0 if value > 1.0 else value
    return value


def _select_web(category: str, value: str, package: str,
                params: dict, limit: int, in_stock: bool) -> list[dict]:
    """Подбор в сети: jlcsearch (без ключа)."""
    q = " ".join(x for x in (value, package, category) if x).strip()
    if not q:
        return []
    url = f"{JLCSEARCH_API}/api/search?" + urllib.parse.urlencode(
        {"q": q, "limit": max(limit * 2, 10)})
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=25) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception:                                        # noqa: BLE001
        return []
    comps = data.get("components") or []
    out: list[dict] = []
    for c in comps:
        if in_stock and not int(c.get("stock") or 0):
            continue
        if package and package.lower() not in str(c.get("package") or "").lower():
            continue
        out.append({**c, "source": "jlcsearch (сеть)"})
    return out[:limit]


# --------------------------------------------------------------------------- #
#  Карточка детали LCSC — полный список параметров
# --------------------------------------------------------------------------- #
def lcsc_detail(code: str) -> dict:
    """Карточка детали LCSC по коду C… — с параметрами и ссылкой на PDF."""
    code = str(code or "").strip().upper()
    if not code.startswith("C"):
        code = "C" + code.lstrip("C")
    try:
        req = urllib.request.Request(
            LCSC_DETAIL + code, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=25) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception:                                        # noqa: BLE001
        return {}
    res = data.get("result") or {}
    params: dict[str, str] = {}
    for p in (res.get("paramVOList") or []):
        if isinstance(p, dict):
            name = str(p.get("paramNameEn") or p.get("paramName") or "")
            val = p.get("paramValueEn") or p.get("paramValue") or ""
            if name:
                params[name] = str(val)
    return {
        "lcsc": code,
        "mfr": str(res.get("productModel") or ""),
        "manufacturer": str(res.get("brandNameEn") or ""),
        "description": str(res.get("productIntroEn") or ""),
        "package": str(res.get("encapStandard") or ""),
        "stock": int(res.get("stockNumber") or 0),
        "catalog": str(res.get("catalogName") or ""),
        "datasheet": str(res.get("pdfUrl") or ""),
        "params": params,
    }


# --------------------------------------------------------------------------- #
#  Модели SPICE
# --------------------------------------------------------------------------- #
_SPICE_CACHE: dict | None = None


def spice_index() -> dict:
    """Индекс моделей: имя → {путь, текст, параметры}."""
    global _SPICE_CACHE
    if _SPICE_CACHE is not None:
        return _SPICE_CACHE
    idx: dict[str, dict] = {}
    root = SPICE_LIB
    if root.is_dir():
        for path in sorted(root.rglob("*")):
            if path.suffix.lower() not in (".lib", ".mod", ".cir", ".spi"):
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            rel = str(path.relative_to(root))
            for m in re.finditer(
                    r"(?im)^\s*\.model\s+([A-Za-z0-9_.+\-/]+)([^\n]*)"
                    r"(.*?)(?=^\s*\.(?:model|ends|end)\b|\Z)", text, re.S):
                nm = m.group(1)
                params = _model_params(m.group(2) + " " + m.group(3))
                idx.setdefault(nm.lower(), {
                    "name": nm, "file": rel, "params": params,
                    "text": m.group(0).strip()[:4000],
                })
            for m in re.finditer(
                    r"(?im)^\s*\.subckt\s+([A-Za-z0-9_.+\-/]+)([^\n]*)"
                    r"(.*?^\s*\.ends\b)", text, re.S | re.M):
                nm = m.group(1)
                params = _model_params(m.group(3))
                idx.setdefault(nm.lower(), {
                    "name": nm, "file": rel, "params": params,
                    "text": m.group(0).strip()[:4000],
                })
    _SPICE_CACHE = idx
    return idx


def _model_params(text: str) -> dict[str, float]:
    """Числовые параметры карточки модели: VTO, KP, IS, RD, BF…"""
    out: dict[str, float] = {}
    for m in re.finditer(r"(?i)\b([A-Za-z_]{2,6})\s*=\s*"
                         r"(-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)", text):
        try:
            out[m.group(1).lower()] = float(m.group(2))
        except ValueError:
            continue
    return out


def models_for(mfr: str, limit: int = 12) -> list[dict]:
    """Модели по номеру изготовителя: точное, по семейству, по префиксу."""
    idx = spice_index()
    s = str(mfr or "").strip().lower()
    if not s:
        return []
    if s in idx:
        return [idx[s]]
    # семейство: отбросить суффиксы упаковки/партии (1N4148WSS → 1N4148W)
    for cut in range(len(s), 3, -1):
        stem = s[:cut]
        if stem in idx:
            return [idx[stem]]
    # по префиксу: 2n7002 → 2n7002, 2n7002new, 2n7002/plp
    pref = [v for k, v in idx.items()
            if k.startswith(s[:max(5, min(len(s), 8))])]
    pref.sort(key=lambda v: len(v["name"]))
    return pref[:limit]


def pick_model(mfr: str, params: dict | None = None) -> dict | None:
    """Модель для детали: по номеру, а при его отсутствии — по параметрам.

    params — требования к прибору ({"vth": "2 V", "id": "1.5 A"}); из них
    выбирается модель с подходящей карточкой, если номера нет.
    """
    cands = models_for(mfr)
    if not cands:
        return None
    if len(cands) == 1:
        return cands[0]
    want = {param_key(k): v for k, v in (params or {}).items()}
    want = {k: v for k, v in want.items()
            if parse_number(v) is not None}
    if not want:
        return cands[0]
    best, best_score = None, -1
    for c in cands:
        score = 0
        for key, wtext in want.items():
            wv = parse_number(wtext)
            got = c["params"].get(key)
            if got is None:
                # параметр модели может называться иначе
                for alias, cands2 in (("rdson", ("rd", "rs")),
                                      ("vth", ("vto",)),
                                      ("id", ("is", "bf")),
                                      ("vf", ("is",))):
                    if key == alias:
                        for c2 in cands2:
                            if c2 in c["params"]:
                                got = c["params"][c2]
                                break
                if got is None:
                    continue
            if key in ("rdson", "rth"):
                score += 1 if got <= wv else 0
            else:
                score += 1 if got >= wv * 0.5 else 0
        if score > best_score:
            best, best_score = c, score
    return best or cands[0]


def model_text(name: str) -> str:
    """Текст модели по имени (для включения в деку)."""
    idx = spice_index()
    rec = idx.get(str(name or "").lower())
    return rec["text"] if rec else ""


# --------------------------------------------------------------------------- #
#  Сводка для агента
# --------------------------------------------------------------------------- #
def part_card(code: str) -> dict:
    """Карточка детали: MPN, параметры, запас, даташит, модель SPICE."""
    info = lcsc_detail(code)
    if not info:
        return {}
    mfr = info.get("mfr") or ""
    model = pick_model(mfr, info.get("params"))
    info["spice_model"] = model["name"] if model else ""
    info["spice_file"] = model["file"] if model else ""
    info["spice_params"] = model["params"] if model else {}
    return info


def summary(item: dict) -> str:
    """Короткая строка по детали — для журнала заказа."""
    bits = [str(item.get("lcsc") or ""), str(item.get("mfr") or ""),
            str(item.get("package") or "")]
    line = " | ".join(x for x in bits if x)
    stock = item.get("stock")
    if stock:
        line += f" | запас {stock}"
    src = item.get("source")
    if src:
        line += f" | {src}"
    return line
