#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Реальная деталь по коду LCSC: символ + корпус + 3D-модель и даташит.

Запускается агентом (инструмент ``part_fetch``) и никогда не выдумывает:
все артефакты скачиваются из первоисточников.

* Символ/корпус/3D — easyeda2kicad из EasyEDA по коду LCSC:
      design/libs/<nick>.kicad_sym, <nick>.pretty, <nick>.3dshapes
* 3D доводится до конвенции tscircuit: у STEP правится диалект SolidWorks,
  freecadcmd ставит посадку по z и кладёт рядом STL в той же ориентации
  (пара STL+STEP эмитится вместе — конвенция «Ориентация STL» в
  hardware/tscircuit/step2stl.py; normalize_step_dialect и convert_stls
  ниже).
* Даташит — прямая PDF-ссылка из базы JLC (jlcparts, столбец datasheet);
  если её нет — обёртка LCSC (https://www.lcsc.com/datasheet/<C>.pdf)
  разбирается до прямой ссылки datasheet.lcsc.com.

Итог — JSON на stdout; журнал конвертера — в stderr.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
DEFAULT_DB = os.environ.get("KR_JLCPARTS_DB",
                            "/mnt/ext4data/jlcparts/cache/cache.sqlite3")
E2K_TIMEOUT = 180
STL_TIMEOUT = 1800
#: готовые скраперы даташитов из kicad-happy (MIT) — см. tools/vendor/kicad_happy
VENDOR = Path(__file__).resolve().parent / "vendor" / "kicad_happy"

# Диалект выгрузки SolidWorks: пробел перед «;» в конце сущностей.
STEP_DIALECT_SP = re.compile(r"\)[ \t]+;[ \t]*$", re.M)


# --------------------------------------------------------------------------- #
#  утилиты
# --------------------------------------------------------------------------- #
def norm_lcsc(raw: str) -> str:
    """ '8734' / 'c8734' / 'C8734' → 'C8734' """
    s = str(raw).strip().upper()
    if s.startswith("C"):
        s = s[1:]
    if not s.isdigit():
        raise SystemExit(f"[part_fetch] не код LCSC: {raw!r}")
    return "C" + s


def num_lcsc(lcsc: str) -> int:
    return int(lcsc[1:])


def slug(text: str, cap: int = 60) -> str:
    s = re.sub(r"[^\w\-.()+]+", "_", str(text or "").strip())
    return s.strip("_")[:cap] or "part"


def walk_up(name: str) -> Path | None:
    """Найти каталог/файл с именем `name`, поднимаясь от cwd и от скрипта."""
    for start in (Path.cwd().resolve(), Path(__file__).resolve()):
        for p in (start, *start.parents):
            cand = p / name
            if cand.exists():
                return cand
    return None


def e2k_cmd() -> list[str]:
    """Команда запуска easyeda2kicad: брат текущего питона, .venv проекта,
    либо модуль (если установлен)."""
    exe = Path(sys.executable)
    cands = [exe.parent / "easyeda2kicad"]
    venv = walk_up(".venv")
    if venv:
        cands.append(venv / "bin" / "easyeda2kicad")
    for c in cands:
        if c.is_file():
            return [str(c)]
    return [sys.executable, "-m", "easyeda2kicad"]


def load_brig():
    """Парсер библиотек KiCad (тот же, что у компилятора)."""
    for start in (Path(__file__).resolve(), Path.cwd().resolve()):
        for p in (start, *start.parents):
            kit = p / "hardware" / "kicad" / "kicad_lib_brig.py"
            if kit.is_file():
                sys.path.insert(0, str(kit.parent))
                import kicad_lib_brig as K             # noqa: PLC0415
                return K
    return None


# --------------------------------------------------------------------------- #
#  база JLC: MPN, производитель, корпус, наличие, цена, прямая PDF-ссылка
# --------------------------------------------------------------------------- #
def db_row(lcsc: str, dbpath: str) -> dict:
    path = Path(dbpath)
    if not path.is_file():
        local = walk_up("hardware/tscircuit/out/jlcsearch.db")
        path = local if local else path
    if not path.is_file():
        return {}
    try:
        import sqlite3
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        cols = {r[1] for r in con.execute("PRAGMA table_info(jlc_components)")}
        if not cols:
            return {}
        key = "lcsc" if "lcsc" in cols else None
        if key is None:
            return {}
        row = con.execute(
            "SELECT * FROM jlc_components WHERE lcsc=?", (num_lcsc(lcsc),)
        ).fetchone()
        if row is None:
            return {}
        cur = con.execute("SELECT * FROM jlc_components LIMIT 0")
        names = [d[0] for d in cur.description]
        return dict(zip(names, row))
    except Exception:                                   # noqa: BLE001
        return {}


# --------------------------------------------------------------------------- #
#  EasyEDA → KiCad
# --------------------------------------------------------------------------- #
def run_e2k(lcscs: list[str], out_stem: Path) -> str:
    out_stem.parent.mkdir(parents=True, exist_ok=True)
    cmd = e2k_cmd() + ["--lcsc_id", *lcscs, "--full",
                       "--output", str(out_stem), "--overwrite"]
    last = ""
    for attempt in (1, 2):
        r = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=E2K_TIMEOUT)
        last = (r.stdout or "") + (r.stderr or "")
        if r.returncode == 0 and "Created Kicad symbol" in last:
            return last
        if attempt == 1:
            continue
    return last


def parse_e2k_log(text: str) -> dict[str, dict]:
    """Разбор журнала конвертера: по каждому ID — имена символа/корпуса/3D."""
    out: dict[str, dict] = {}
    cur = ""
    pats = (("symbol", r"Symbol name\s*:\s*(\S+)"),
            ("footprint", r"Footprint name\s*:\s*(\S+)"),
            ("step", r"3D model path \(step\)\s*:\s*(\S+)"),
            ("wrl", r"3D model path \(wrl\)\s*:\s*(\S+)"))
    for ln in text.splitlines():
        m = re.search(r"for ID\s*:?\s*(C?\d+)", ln)
        if m:
            cur = norm_lcsc(m.group(1))
            out.setdefault(cur, {})
        if not cur:
            continue
        for key, pat in pats:
            mm = re.search(pat, ln)
            if mm:
                out[cur].setdefault(key, mm.group(1))
    return out


def find_symbol(lib_file: Path, lcsc: str, K) -> dict:
    """Символ этой детали в накопленной библиотеке: имя, MPN, корпус, пины."""
    if K is None or not lib_file.is_file():
        return {}
    node = K.parse_sexp(lib_file.read_text(errors="ignore"))
    for sym in node.find_all("symbol"):
        props: dict[str, str] = {}
        for pr in sym.find_all("property"):
            if len(pr.items) > 2:
                props[str(pr.items[1])] = str(pr.items[2])
        if props.get("LCSC Part", "").upper() != lcsc.upper():
            continue
        pins = []
        for unit in sym.find_all("symbol"):
            for p in unit.find_all("pin"):
                nm, nu = p.val("name"), p.val("number")
                if nu is not None:
                    pins.append(f"{nu}:{nm}")
        return {"name": K.sym_name(sym),
                "mpn": props.get("MPN", ""),
                "manufacturer": props.get("Manufacturer", ""),
                "footprint_prop": props.get("Footprint", ""),
                "datasheet_prop": props.get("Datasheet", ""),
                "pin_count": len(pins),
                "pin_numbers": [p.split(":", 1)[0] for p in pins][:120],
                }
    return {}


# --------------------------------------------------------------------------- #
#  3D: нормализация STEP и STL в конвенции пары STL+STEP
# --------------------------------------------------------------------------- #
def normalize_step_dialect(path: Path) -> bool:
    """Починить диалект STEP-файла, если он выгружен SolidWorks.

    EasyEDA отдаёт STEP с CRLF и пробелом перед «;» в конце сущностей
    («) ;» вместо «);»). Сборщик board.step в tsci разбирает файл простым
    токенизатором и на таком диалекте молча бросает модель (в консоли
    «Failed to merge STEP model»). Текст правится сразу при скачивании;
    build_pcb.mjs делает то же как страховку на старые заказы.

    Посадку по высоте здесь не трогаем: у выгрузок EasyEDA бывают
    собственные размещения внутри файла, по «сырым» точкам её не измерить.
    Это делает step2stl.py по эффективной геометрии (NORMALIZE_Z=1,
    см. convert_stls).

    Возвращает True, если файл перезаписан.
    """
    try:
        txt = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    if "\r" not in txt and not STEP_DIALECT_SP.search(txt):
        return False
    out = txt.replace("\r\n", "\n").replace("\r", "\n")
    path.write_text(STEP_DIALECT_SP.sub(");", out), encoding="utf-8")
    return True


def stl_zrange(path: Path) -> tuple[float, float] | None:
    """Диапазон z бинарного STL (None, если файл не разобрать)."""
    try:
        buf = path.read_bytes()
    except OSError:
        return None
    if buf[:5] == b"solid":                      # ASCII-STL не разбираем
        return None
    if len(buf) < 84:
        return None
    n = int.from_bytes(buf[80:84], "little")
    if 84 + n * 50 != len(buf):
        return None
    zs: list[float] = []
    for i in range(n):
        v = struct.unpack_from("<12f", buf, 84 + 50 * i)
        zs.extend((v[5], v[8], v[11]))
    return (min(zs), max(zs)) if zs else None


def convert_stls(shapes: Path, thru_map_file: str = "") -> tuple[bool, str]:
    """STL из STEP — через step2stl.py (нужен freecadcmd).

    thru_map_file — файл-карта «имя_модели TAB 0/1» (тип детали по каждой
    модели: SMD садится низом на ZMIN, сквозные не трогаются).

    board.glb берёт приоритетом model_stl_url: локальный STL читается как
    настоящая геометрия, а локальный STEP — как коробка-заглушка. Поэтому
    рядом с каждым STEP в design/libs должен лежать STL. Ориентация STL
    здесь — КАК У STEP (без зеркала): в паре STL+STEP сборщик glb читает
    STL переводом STEP-ветки (y=+z), так что зеркало перевернуло бы
    деталь. Почему так и чем отличается системное зеркало — конвенция
    «Ориентация STL» в hardware/tscircuit/step2stl.py.

    NORMALIZE_Z=1 включает в step2stl посадку по z по эффективной
    геометрии (STEP правится на месте до конвертации): у выгрузок EasyEDA
    бывают собственные размещения внутри файла, и по «сырым» точкам
    посадку не измерить. MIRROR_Z здесь не ставится — режим пары.
    """
    fc = shutil.which("freecadcmd") or shutil.which("FreeCADCmd")
    if fc is None:
        return False, "freecadcmd не найден в PATH (нужен для STEP -> STL)"
    tool = walk_up("hardware/tscircuit/step2stl.py")
    if tool is None:
        return False, "hardware/tscircuit/step2stl.py не найден"
    env = {**os.environ, "MODELS_DIR": str(shapes), "STL_DIR": str(shapes),
           "NORMALIZE_Z": "1"}
    if thru_map_file:
        # Тип детали по каждой модели (SMD/сквозные): step2stl для SMD
        # переносит низ на ZMIN, даже если тело пришло отцентрованным по z
        # (C0603 EasyEDA: [-0.40, 0.40] при высоте 0.8), а сквозные модели
        # не трогает. Без карты — общий KR_PART_THRU.
        env["KR_PART_THRU_MAP"] = thru_map_file
    try:
        r = subprocess.run([fc, str(tool)], capture_output=True, text=True,
                           env=env, timeout=STL_TIMEOUT)
    except subprocess.TimeoutExpired:
        return False, f"конвертация STL не уложилась в {STL_TIMEOUT} с"
    log = ((r.stdout or "") + (r.stderr or "")).strip()
    if log:
        print(log, file=sys.stderr)
    if r.returncode != 0:
        return False, f"step2stl.py вернул {r.returncode}"
    return True, ""


# --------------------------------------------------------------------------- #
#  даташит
# --------------------------------------------------------------------------- #
def http_get(url: str, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "application/pdf,text/html,application/json,*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def lcsc_pdf_link(lcsc: str) -> str | None:
    """Обёртка LCSC → прямая ссылка на PDF datasheet.lcsc.com."""
    for page in (f"https://www.lcsc.com/datasheet/{lcsc}.pdf",
                 f"https://www.lcsc.com/product-detail/{lcsc}.html"):
        try:
            html = http_get(page).decode("utf-8", "ignore")
        except Exception:                               # noqa: BLE001
            continue
        m = re.search(
            r"https://datasheet\.lcsc\.com/datasheet/pdf/[^\"'<>\s\\]+",
            html)
        if m:
            return m.group(0).replace("&amp;", "&")
    return None


def wmsc_pdf_link(lcsc: str) -> str | None:
    """Прямая ссылка на PDF из официального JSON LCSC (без ключа).

    Тот же эндпоинт, которым пользуется готовый скрапер kicad-happy для
    обогащения: https://wmsc.lcsc.com/ftps/wm/product/detail?productCode=C…
    """
    try:
        raw = http_get("https://wmsc.lcsc.com/ftps/wm/product/detail"
                       f"?productCode={lcsc}", timeout=30)
        data = json.loads(raw.decode("utf-8", "ignore"))
        url = str(((data or {}).get("result") or {}).get("pdfUrl") or "")
        return url.strip() or None
    except Exception:                                   # noqa: BLE001
        return None


def pdf_verify(path: Path, mpn: str) -> tuple[bool, str]:
    """Есть ли MPN в тексте PDF (первые страницы).

    Проверка того, что скачали именно нашу деталь: у каталогов-обёрток и
    поисковиков легко получить чужой PDF. PyMuPDF (если доступен) или
    pdftotext (poppler). Без обоих — не мешаем: PDF-магия уже проверена.
    """
    pat = re.sub(r"[\s\-_/.]+", "", str(mpn or "")).upper()
    if not pat:
        return True, "нечего сверять"
    text = ""
    try:
        import fitz                                      # noqa: PLC0415
        with fitz.open(str(path)) as doc:
            for i, page in enumerate(doc):
                if i >= 6:
                    break
                text += page.get_text()
    except ImportError:
        exe = shutil.which("pdftotext")
        if exe:
            try:
                r = subprocess.run([exe, "-f", "1", "-l", "6", str(path), "-"],
                                   capture_output=True, text=True, timeout=60)
                text = r.stdout
            except Exception:                            # noqa: BLE001
                text = ""
    except Exception as exc:                             # noqa: BLE001
        return True, f"текст не проверен: {exc}"
    if not text:
        return True, "текст не проверен (нет fitz/pdftotext)"
    flat = re.sub(r"[\s\-_/.]+", "", text).upper()
    return (pat in flat), ("MPN найден в тексте" if pat in flat
                           else "MPN в тексте не найден")


def vendor_lcsc_fetch(query: str, dest: Path) -> dict:
    """Готовый скрапер kicad-happy: jlcsearch + LCSC CDN + проверка PDF.

    Принимаем результат только при совпадении MPN: поиск jlcsearch по
    одному MPN нечёткий (на «1N4148WT» первым пришёл BC817-40). Если
    подтверждения нет — файл удаляем и идём дальше по цепочке.
    """
    script = VENDOR / "fetch_datasheet_lcsc.py"
    if not script.is_file():
        return {"ok": False, "error": "нет tools/vendor/kicad_happy"}
    try:
        r = subprocess.run([sys.executable, str(script), "--search", query,
                            "--output", str(dest), "--json"],
                           capture_output=True, text=True, timeout=240)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "таймаут 240 с"}
    out = ""
    for ln in (r.stdout or "").splitlines():
        if ln.strip().startswith("{"):
            out = ln
    try:
        info = json.loads(out) if out else {}
    except Exception:                                   # noqa: BLE001
        info = {}
    if not dest.is_file() or dest.stat().st_size == 0:
        return {"ok": False,
                "error": str(info.get("error") or f"код {r.returncode}")[:200]}
    ver = info.get("verification") or {}
    if not ver:
        return {"ok": True, "info": info, "note": "без проверки содержимого"}
    if ver.get("verified"):
        return {"ok": True, "info": info,
                "note": str(ver.get("details") or "проверено")}
    if ver.get("mpn_found"):
        return {"ok": True, "info": info,
                "note": "MPN найден, уверенность ниже cредней"}
    dest.unlink(missing_ok=True)
    return {"ok": False,
            "error": "PDF не подтверждён по MPN ("
                     f"{ver.get('confidence', '?')}: "
                     f"{str(ver.get('details') or '')[:120]})"}


def web_search_pdfs(query: str, limit: int = 10) -> list[str]:
    """Готовый метапоиск ddgs (без ключей): прямые ссылки на PDF по MPN."""
    try:
        from ddgs import DDGS                             # noqa: PLC0415
    except ImportError:
        return []
    urls: list[str] = []
    for q in (f"{query} datasheet filetype:pdf",
              f"{query} datasheet pdf"):
        try:
            for r in DDGS().text(q, max_results=limit):
                href = str(r.get("href") or "")
                if href.lower().startswith("http") and \
                        href.lower().split("?")[0].endswith(".pdf"):
                    urls.append(href)
        except Exception:                                # noqa: BLE001
            continue
        if len(urls) >= 3:
            break
    # каталоги-обёртки и поисковики: сами PDF не отдают
    bad = ("alldatasheet", "datasheetspdf", "octopart", "findchips",
           "bing.com", "google.", "duckduckgo", "mouser.com/search",
           "digikey.com/products", "datasheets360")
    seen, out = set(), []
    for u in urls:
        if any(b in u.lower() for b in bad) or u in seen:
            continue
        seen.add(u)
        out.append(u)
    return out


def http_get_impersonated(url: str) -> bytes:
    """curl_cffi (браузерный TLS-отпечаток) — готовый обход Cloudflare."""
    try:
        from curl_cffi import requests as creq            # noqa: PLC0415
        r = creq.get(url, impersonate="chrome", timeout=60,
                     allow_redirects=True)
        if r.status_code == 200:
            return bytes(r.content)
    except Exception:                                    # noqa: BLE001
        pass
    return http_get(url)


def web_fallback(mpn: str, dest: Path) -> dict:
    """Последний рубеж: веб-поиск → PDF → строгая проверка MPN по тексту."""
    urls = web_search_pdfs(mpn)
    if not urls:
        return {"error": "поисковики не дали PDF-ссылок"}
    errors: list[str] = []
    for url in urls[:8]:
        try:
            data = http_get_impersonated(url)
        except Exception as exc:                         # noqa: BLE001
            errors.append(f"{url[:60]}: {exc}")
            continue
        if data[:5] != b"%PDF-":
            errors.append(f"{url[:60]}: не PDF")
            continue
        dest.write_bytes(data)
        ok, note = pdf_verify(dest, mpn)
        if ok:
            host = urllib.parse.urlsplit(url).hostname or "web"
            return {"source": f"web:{host}", "url": url, "bytes": len(data),
                    "note": note}
        errors.append(f"{url[:60]}: {note}")
        dest.unlink(missing_ok=True)
    return {"error": "; ".join(errors)[:400]}


def fetch_datasheet(lcsc: str, db: dict, ddir: Path,
                    rel_prefix: str, mpn: str = "") -> dict:
    """Даташит детали по цепочке источников.

    1. Прямые ссылки: база jlcparts → API LCSC (wmsc, pdfUrl) → страница
       LCSC. Каждый PDF проверяется на присутствие MPN в тексте.
    2. Готовый скрапер kicad-happy (jlcsearch + LCSC CDN + проверка
       содержимого) — принимается только при совпадении MPN.
    3. Строгий веб-поиск по MPN (ddgs + curl_cffi) с проверкой MPN по
       тексту PDF — последний рубеж, когда баз нет.

    Так «даташит на каждую деталь» — правило, а не удача: LCSC-детали
    закрывает CDN LCSC, безродные/сторонние — поисковики с проверкой.
    """
    ddir.mkdir(parents=True, exist_ok=True)
    mfr = str(db.get("mfr") or mpn or "")
    fname = f"{lcsc}_{slug(mfr)}.pdf" if lcsc else f"{slug(mfr)}.pdf"
    dest = ddir / fname
    rel = f"{rel_prefix}/{fname}" if rel_prefix else fname
    errors: list[str] = []

    # 1) прямые ссылки
    url_tries: list[tuple[str, str]] = []
    if str(db.get("datasheet") or "").strip():
        url_tries.append(("jlcparts", str(db["datasheet"]).strip()))
    link = wmsc_pdf_link(lcsc) if lcsc else None
    if link:
        url_tries.append(("lcsc-api", link))
    link = lcsc_pdf_link(lcsc) if lcsc else None
    if link:
        url_tries.append(("lcsc-datasheet", link))
    for src, url in url_tries:
        try:
            data = http_get(url)
        except Exception as exc:                        # noqa: BLE001
            errors.append(f"{src}: {exc}")
            continue
        if data[:5] == b"%PDF-":
            dest.write_bytes(data)
            ok, note = pdf_verify(dest, mfr)
            return {"file": rel, "url": url, "source": src,
                    "bytes": len(data), "verified": bool(ok),
                    "check": note}
        errors.append(f"{src}: ответ не PDF ({len(data)} байт)")

    # 2) готовый скрапер kicad-happy
    query = lcsc or mfr
    if query:
        got = vendor_lcsc_fetch(query, dest)
        if got.get("ok"):
            return {"file": rel,
                    "url": str((got.get("info") or {}).get("datasheet_url")
                               or ""),
                    "source": "kicad-happy-lcsc",
                    "bytes": dest.stat().st_size, "verified": True,
                    "check": str(got.get("note") or "")}
        errors.append("kicad-happy: " + str(got.get("error"))[:160])

    # 3) строгий веб-поиск по MPN
    if mfr:
        got = web_fallback(mfr, dest)
        if got.get("source"):
            return {"file": rel, "url": str(got.get("url") or ""),
                    "source": str(got["source"]),
                    "bytes": int(got.get("bytes") or 0), "verified": True,
                    "check": str(got.get("note") or "")}
        errors.append("web: " + str(got.get("error"))[:160])

    return {"file": "", "url": "", "source": "", "bytes": 0,
            "error": "; ".join(errors)[:400]}


def datasheet_only_mode(args) -> int:
    """Добыть даташиты без символа/корпуса/3D.

    Страховка правила «у каждой детали есть даташит»: библиотекарь или
    агент-читатель зовёт этот режим по коду LCSC и/или MPN, когда обычная
    загрузка не сработала, и получает PDF с проверкой содержимого.
    """
    lcscs = [norm_lcsc(x) for x in (args.lcsc or [])]
    mpns = [str(x) for x in (args.mpn or [])]
    if not lcscs and not mpns:
        print(json.dumps({"ok": False, "error": "нужен --lcsc и/или --mpn"},
                         ensure_ascii=False))
        return 2
    ddir = Path(args.datasheets)
    out_parts = []
    ok = True
    n = max(len(lcscs), len(mpns))
    for i in range(n):
        lcsc = lcscs[i] if i < len(lcscs) else ""
        mpn = mpns[i] if i < len(mpns) else ""
        db = db_row(lcsc, args.db) if lcsc else {}
        if not mpn:
            mpn = str(db.get("mfr") or "")
        ds = fetch_datasheet(lcsc, db, ddir, str(ddir), mpn=mpn)
        part = {"lcsc": lcsc, "mpn": mpn, **ds}
        part["ok"] = bool(ds.get("file"))
        ok = ok and part["ok"]
        name = lcsc or mpn
        if part["ok"]:
            print(f"[даташит] {name}: {ds['file']} ({ds['source']}, "
                  f"{ds['bytes']} б; {ds.get('check', '')})", file=sys.stderr)
        else:
            print(f"[даташит] {name}: НЕ ДОБЫТ — {ds.get('error')}",
                  file=sys.stderr)
        out_parts.append(part)
    # индекс — накопительный, чтобы отчёт заказа видел всё
    ddir.mkdir(parents=True, exist_ok=True)
    idx_path = ddir / "index.json"
    idx: dict = {}
    if idx_path.is_file():
        try:
            idx = json.loads(idx_path.read_text(encoding="utf-8"))
        except Exception:                                # noqa: BLE001
            idx = {}
    for part in out_parts:
        if part.get("ok"):
            idx[part["lcsc"] or part["mpn"]] = {
                "mpn": part.get("mpn", ""),
                "file": part["file"], "url": part.get("url", ""),
                "source": part.get("source", ""),
                "bytes": part.get("bytes", 0),
                "verified": part.get("verified", False),
                "check": part.get("check", ""),
            }
    idx_path.write_text(json.dumps(idx, ensure_ascii=False, indent=1),
                        encoding="utf-8")
    print(json.dumps({"ok": ok, "parts": out_parts},
                     ensure_ascii=False, indent=1))
    return 0 if ok else 1


# --------------------------------------------------------------------------- #
#  main
# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--lcsc", nargs="+",
                    help="коды LCSC, напр. C8734 C6430945")
    ap.add_argument("--mpn", nargs="+",
                    help="артикулы производителя (для фоллбек-поиска даташита)")
    ap.add_argument("--datasheet-only", action="store_true",
                    help="только добыть даташит(ы) по --lcsc/--mpn, без модели")
    ap.add_argument("--out", default="design/libs/easyeda",
                    help="основа библиотеки: <out>.kicad_sym/.pretty/.3dshapes")
    ap.add_argument("--datasheets", default="design/datasheets",
                    help="каталог для скачанных даташитов")
    ap.add_argument("--db", default=DEFAULT_DB, help="база jlcparts (sqlite)")
    ap.add_argument("--no-datasheet", action="store_true",
                    help="не скачивать даташит (только модель)")
    args = ap.parse_args()

    if args.datasheet_only:
        return datasheet_only_mode(args)
    if not args.lcsc:
        ap.error("нужен --lcsc (или --datasheet-only с --lcsc/--mpn)")

    K = load_brig()
    lcscs = [norm_lcsc(x) for x in args.lcsc]
    out_stem = Path(args.out)
    nick = out_stem.name
    log = run_e2k(lcscs, out_stem)
    e2k = parse_e2k_log(log)
    lib_file = out_stem.with_suffix(".kicad_sym")
    pretty = out_stem.with_suffix(".pretty")
    shapes = out_stem.with_suffix(".3dshapes")

    parts = []
    step_paths: dict[str, Path] = {}
    # Тип детали по модели («0» SMD / «1» сквозные) — для посадки в step2stl
    thru_map: dict[str, str] = {}
    for lcsc in lcscs:
        rec: dict = {"lcsc": lcsc}
        db = db_row(lcsc, args.db)
        sym = find_symbol(lib_file, lcsc, K)
        info = e2k.get(lcsc, {})
        if not sym and not info.get("symbol"):
            rec.update(ok=False,
                       error="EasyEDA не вернул символ (журнал: "
                             + (log.strip().splitlines() or ["пусто"])[-1][:200]
                             + ")")
            parts.append(rec)
            continue
        rec.update(ok=True)
        name = sym.get("name") or info.get("symbol", "")
        fp = (sym.get("footprint_prop", "").split(":", 1)[-1]
              or info.get("footprint", ""))
        rec.update({
            "name": name,
            "lib_id": f"{nick}:{name}",
            "footprint": f"{nick}:{fp}" if fp else "",
            "mpn": sym.get("mpn") or db.get("mfr", ""),
            "manufacturer": (sym.get("manufacturer")
                             or db.get("manufacturer", "")),
            "description": db.get("description", ""),
            "package": db.get("package", ""),
            "stock": db.get("stock"),
            "price": db.get("price"),
            "pin_count": sym.get("pin_count", 0),
        })
        fp_file = pretty / f"{fp}.kicad_mod"
        if fp and not fp_file.is_file():
            rec["footprint_error"] = (
                f"файл корпуса {fp_file} не создан — повторите запрос")
        step_file = Path(info.get("step") or (shapes / f"{fp}.step"))
        if step_file.is_file():
            rec["model3d_step"] = str(step_file)
            if fp and fp_file.is_file():
                # SMD или сквозные — по площадкам корпуса: от этого зависит
                # посадка модели в step2stl (см. KR_PART_THRU_MAP).
                ftxt = fp_file.read_text(errors="ignore")
                thru_map[step_file.stem] = ("1" if re.search(
                    r"\b(?:thru_hole|np_thru_hole)\b", ftxt) else "0")
            if normalize_step_dialect(step_file):
                print(f"[3D] {step_file.name}: нормализован диалект STEP "
                      f"(CRLF и «) ;» от SolidWorks)", file=sys.stderr)
            step_paths[lcsc] = step_file
        else:
            rec["model3d_step"] = ""
        rec["model3d_wrl"] = (info.get("wrl") or "") if info.get("wrl") else ""
        if not args.no_datasheet:
            ds = fetch_datasheet(lcsc, db, Path(args.datasheets),
                                 str(Path(args.datasheets)),
                                 mpn=str(rec.get("mpn")
                                         or db.get("mfr") or ""))
            rec["datasheet"] = ds
            if not ds.get("file"):
                rec["ok"] = False
        parts.append(rec)

    # ---- STL для board.glb (в паре со STEP): одна конвертация на каталог ----
    #
    # board.glb читает локальный STL как настоящую геометрию, а локальный
    # STEP — как коробку-заглушку, поэтому STL обязателен рядом с каждым
    # STEP; ориентация STL — как у STEP (без зеркала, конвенция в
    # step2stl.py). Если конвертация не прошла, деталь без STL в board.glb
    # станет коробкой — это фиксируем как незавершённый fetch.
    stl_err = ""
    if step_paths:
        map_file = ""
        if thru_map:
            fd, map_file = tempfile.mkstemp(prefix="kr_part_thru_",
                                            suffix=".tsv")
            with os.fdopen(fd, "w") as fh:
                fh.write("".join(f"{k}\t{v}\n"
                                 for k, v in sorted(thru_map.items())))
        try:
            stl_ok, stl_err = convert_stls(shapes, map_file)
        finally:
            if map_file:
                Path(map_file).unlink(missing_ok=True)
        if not stl_ok:
            print(f"[3D] STL-конвертация: {stl_err}", file=sys.stderr)
    for rec in parts:
        sf = step_paths.get(rec.get("lcsc", ""))
        if sf is None:
            rec["model3d_stl"] = ""
            continue
        stl = sf.with_suffix(".stl")
        rng = (stl_zrange(stl)
               if stl.is_file() and stl.stat().st_size > 0 else None)
        rec["model3d_stl"] = str(stl) if rng is not None else ""
        if rng is None:
            rec["ok"] = False
            rec["stl_error"] = stl_err or f"STL не создан или не читается: {stl}"
        else:
            # Пара STL+STEP обязана совпадать по ориентации: тело в плюсе
            # z, низ у нуля (в board.glb к STL применяется STEP-ветка
            # перевода осей, y=+z). Иначе деталь уйдёт внутрь платы.
            # SMD отдельно: низ не должен быть утоплен ниже нуля — так
            # садится тело с чужим якорем (C0603 EasyEDA без нормализации).
            sunk = (thru_map.get(sf.stem) == "0" and rng[0] < -0.06)
            if rng[1] <= 0.05 or rng[0] > 0.06 or sunk:
                rec["ok"] = False
                rec["stl_error"] = (
                    f"STL не встанет на плату: z=[{rng[0]:.2f}, {rng[1]:.2f}], "
                    f"нужно тело в плюсе z с низом на 0 (пара STL+STEP)"
                    + ("; у SMD-тела низ утоплен ниже нуля — модель с "
                       "чужим якорем не посадилась" if sunk else ""))

    # индекс даташитов — накопительный, чтобы отчёт заказа видел всё
    if not args.no_datasheet:
        idx_path = Path(args.datasheets) / "index.json"
        idx: dict = {}
        if idx_path.is_file():
            try:
                idx = json.loads(idx_path.read_text(encoding="utf-8"))
            except Exception:                            # noqa: BLE001
                idx = {}
        for rec in parts:
            if rec.get("datasheet"):
                idx[rec["lcsc"]] = {
                    "mpn": rec.get("mpn", ""),
                    "manufacturer": rec.get("manufacturer", ""),
                    "package": rec.get("package", ""),
                    "lib_id": rec.get("lib_id", ""),
                    "footprint": rec.get("footprint", ""),
                    **rec["datasheet"],
                }
        Path(args.datasheets).mkdir(parents=True, exist_ok=True)
        idx_path.write_text(json.dumps(idx, ensure_ascii=False, indent=1),
                            encoding="utf-8")

    ok = all(p.get("ok") for p in parts)
    print(json.dumps({"ok": ok,
                      "library": str(out_stem),
                      "parts": parts},
                     ensure_ascii=False, indent=1))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
