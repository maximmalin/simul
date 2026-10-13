#!/usr/bin/env python3
"""
Проверка, что страница просмотра не ходит в сеть.

Зачем измерять, а не читать исходники. Требование звучит буквально: «никаких
вызовов не должно наблюдаться». Поэтому здесь запускается настоящий
браузер, грузится страница и читается его сетевой лог. Ответ получается
фактом, а не разбором исходников на предмет строк http://.

Что делает:
  1. поднимает локальный сервер на каталоге страницы (если не указан
     готовый адрес);
  2. запускает headless Chrome с флагами, отключающими его собственную
     фоновую активность (проверки компонентов, синхронизация, телеметрия
     времени), иначе в логе оказываются адреса самого Chrome;
  3. собирает все URL из событий URL_REQUEST_START_JOB;
  4. требует, чтобы каждый запрос шёл на localhost либо был служебным
     (data:, blob:, about:).

Ничего не скачивается и никуда не отправляется: страница проверяется на
локальном сервере, а наружу Chrome не ходит именно потому, что его
фоновые запросы выключены.

Запуск:
    python3 hardware/tscircuit/check_net.py --url http://localhost:8137/
Код возврата 0, если внешних запросов нет.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VIEW = ROOT / "dist/view"

CHROME = "/usr/bin/google-chrome"

# Служебные схемы: они не покидают машину.
LOCAL_SCHEMES = ("data:", "blob:", "about:", "chrome-extension:", "devtools:")

# Флаги, гасящие собственную активность Chrome. Без них в лог попадают
# clients2.google.com/time/... и подобное, что к странице отношения не имеет.
QUIET_FLAGS = [
    "--headless=new",
    "--disable-gpu",
    "--no-sandbox",
    "--hide-scrollbars",
    "--disable-background-networking",
    "--disable-component-update",
    "--disable-client-side-phishing-detection",
    "--disable-default-apps",
    "--disable-domain-reliability",
    "--disable-sync",
    "--no-first-run",
    "--no-pings",
    "--metrics-recording-only",
    "--safebrowsing-disable-auto-update",
    "--password-store=basic",
    "--no-default-browser-check",
    "--disable-breakpad",
    "--disable-features=Translate,OptimizationHints,MediaRouter,"
    "InterestFeedContentSuggestions,CertificateTransparencyComponentUpdater",
    # Внешние имена не резолвятся вообще. Запросы страницы идут на
    # 127.0.0.1, поэтому им это не мешает, а любой обращение наружу
    # заведомо не может состояться.
    "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1",
]


class QuietHandler(SimpleHTTPRequestHandler):
    """Сервер без баннера и журнала — иначе они в stdout и мешают."""

    def log_message(self, *_args) -> None:  # noqa: D102
        pass


def serve(directory: Path) -> tuple[ThreadingHTTPServer, str]:
    handler = partial(QuietHandler, directory=str(directory))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_port}/"


def requested_urls(netlog: Path) -> list[tuple[str, str]]:
    """
    Все URL из сетевого лога вместе с инициатором: [(url, initiator)].

    Инициатор в сетевом логе Chrome — признак структурный, а не эвристический.
    Собственные запросы браузера помечены как 'not an origin', у запросов же
    страницы инициатором указан её origin, например http://127.0.0.1:34033.
    Поэтому отделять служебное от нашего можно точно, без перечня адресов,
    который устаревает вместе с версией Chrome.
    """
    data = json.loads(netlog.read_text())
    names = data.get("constants", {}).get("logEventTypes", {})
    want = {v: k for k, v in names.items() if k == "URL_REQUEST_START_JOB"}
    urls: list[tuple[str, str]] = []
    for e in data.get("events", []):
        if e.get("type") not in want:
            continue
        params = e.get("params") or {}
        u = params.get("url")
        if u:
            urls.append((u, params.get("initiator", "")))
    return urls


def capture(url: str) -> list[str]:
    """Прогон Chrome и список запрошенных URL."""
    with tempfile.TemporaryDirectory() as td:
        netlog = Path(td) / "net.json"
        cmd = [
            CHROME, *QUIET_FLAGS,
            "--window-size=1200,900",
            "--virtual-time-budget=60000",
            f"--log-net-log={netlog}",
            "--net-log-capture-mode=IncludeSensitive",
            url,
        ]
        r = subprocess.run(cmd, capture_output=True, timeout=300)
        if r.returncode != 0:
            print("Chrome не отработал, код", r.returncode)
            raise SystemExit(1)
        if not netlog.is_file():
            print("Chrome не записал сетевой лог")
            raise SystemExit(1)
        return requested_urls(netlog)


def main() -> int:
    ap = argparse.ArgumentParser(description="проверка сетевых запросов страницы")
    ap.add_argument("--url", help="адрес уже запущенной страницы")
    ap.add_argument("--view", default=str(VIEW), help="каталог страницы")
    a = ap.parse_args()

    if not Path(a.view).is_dir():
        print(f"НЕТ {a.view} — соберите страницу: make_viewer.py")
        return 1
    if not Path(CHROME).is_file():
        print(f"НЕТ {CHROME} — проверка невозможна")
        return 1

    httpd = None
    url = a.url
    if not url:
        httpd, url = serve(Path(a.view))
    try:
        page = capture(url)
    finally:
        if httpd is not None:
            httpd.shutdown()

    # Инициатор 'not an origin' — это браузер, а не страница.
    origin = url.rstrip("/") + "/"
    mine_pairs = [(u, i) for u, i in page if i != "not an origin"]
    chrome_pairs = [(u, i) for u, i in page if i == "not an origin"]

    page_urls = list(dict.fromkeys(u for u, _ in mine_pairs))
    mine = page_urls
    chrome_own = list(dict.fromkeys(u for u, _ in chrome_pairs))

    local = [u for u in mine
             if u.startswith(("http://127.0.0.1", "http://localhost"))
             or u.startswith(LOCAL_SCHEMES)]
    external = [u for u in mine if u not in local]

    print("=" * 70)
    print("СЕТЕВЫЕ ЗАПРОСЫ СТРАНИЦЫ")
    print("=" * 70)
    print(f"  адрес: {url}")
    print(f"  запросов страницы: {len(mine)}   локальных: {len(local)}   "
          f"внешних: {len(external)}")
    print(f"  служебных запросов самого Chrome (initiator='not an origin'): "
          f"{len(chrome_own)}")
    for u in sorted(set(local)):
        print(f"    локальный  {u}")
    if chrome_own:
        print()
        print("  служебные, относятся к Chrome, а не к странице:")
        for u in sorted(set(chrome_own))[:6]:
            print(f"    chrome  {u[:88]}")

    if external:
        print()
        print("ВНЕШНИЕ ЗАПРОСЫ ОТ СТРАНИЦЫ:")
        for u in sorted(set(external)):
            print(f"    {u}")
        print()
        print("ОШИБКА: страница обращается наружу")
        return 1

    print()
    print("OK: страница не обращается наружу — запросы только к localhost")
    return 0


if __name__ == "__main__":
    sys.exit(main())
