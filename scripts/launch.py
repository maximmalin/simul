#!/usr/bin/env python3
"""Единый запуск проекта Krystal Radio — один адрес для всего.

Что поднимает:

    backend/agent_bridge.py   агентный HTTP-мост   (по умолчанию :8787)
    frontend/ (vite)          интерфейс            (по умолчанию :5173)

Интерфейс сам проксирует `/api` и `/out` на мост (vite.config.ts), поэтому
пользователю нужен **один** адрес — тот, что у Vite:

    http://127.0.0.1:5173/

Мост при этом остаётся доступен напрямую для отладки и для агентов.

Запуск:

    python3 scripts/launch.py                 # dev, один адрес
    python3 scripts/launch.py --host 0.0.0.0  # виден в локальной сети
    python3 scripts/launch.py --port 8080     # другой порт интерфейса
    python3 scripts/launch.py --prebuilt      # раздать собранный dist/
    python3 scripts/launch.py --no-open       # не открывать браузер

Остановка: Ctrl-C — дети завершаются, временные каталоги подчищаются.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"

# Восстановление прав, если прошлый запуск (заказ) упал, не unlocking.
try:
    sys.path.insert(0, str(ROOT / "backend"))
    from agents import sandbox as _sb  # type: ignore
    _n = _sb.restore_if_locked(ROOT)
    if _n:
        print(f"[lock] восстановлено {_n} путей после сбоя", file=sys.stderr)
except Exception as exc:  # noqa: BLE001
    print(f"[lock] проверка пропущена: {exc}", file=sys.stderr)


def _pick_python() -> str:
    """Интерпретатор для моста.

    Предпочитаем `.venv`: туда поставлен langgraph, без него граф
    работает на запасном пути и теряется чекпоинт. Если venv нет,
    берём системный — проект обязан работать и без него.
    """
    venv = ROOT / ".venv" / "bin" / "python"
    if venv.is_file():
        return str(venv)
    return sys.executable


PYTHON = _pick_python()

#: каталог для pid-файлов и логов (в .gitignore)
RUN_DIR = ROOT / ".run"


# ==========================================================================
#  Утилиты
# ==========================================================================
def info(msg: str) -> None:
    print(f"  {msg}", flush=True)


def ok(msg: str) -> None:
    print(f"  \033[32m✓\033[0m {msg}", flush=True)


def warn(msg: str) -> None:
    print(f"  \033[33m!\033[0m {msg}", flush=True)


def die(msg: str) -> "NoReturn":  # type: ignore[valid-type]
    print(f"\n\033[31mОшибка:\033[0m {msg}\n", file=sys.stderr, flush=True)
    raise SystemExit(1)


# ==========================================================================
#  Порты
# ==========================================================================
def port_busy(port: int, host: str = "127.0.0.1") -> bool:
    """Занят ли порт (проверяем и 127.0.0.1, и 0.0.0.0-слушатели)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.connect((host if host != "0.0.0.0" else "127.0.0.1", port))
            return True
        except (ConnectionRefusedError, OSError):
            return False


def free_port(start: int, host: str, limit: int = 40) -> int:
    """Первый свободный порт начиная с `start`."""
    for port in range(start, start + limit):
        if not port_busy(port, host):
            return port
    die(f"не нашёл свободный порт в диапазоне {start}–{start + limit}")


# ==========================================================================
#  Проверки окружения
# ==========================================================================
def check_env() -> None:
    if not (ROOT / "backend" / "agent_bridge.py").exists():
        die("не найден backend/agent_bridge.py — запускайте из корня репозитория")
    if not (FRONTEND / "package.json").exists():
        die("не найден frontend/package.json")

    info("проверка окружения…")
    info(f"python {sys.version.split()[0]} → мост: "
         f"{'venv (есть langgraph)' if '.venv' in PYTHON else 'системный'}")

    # langgraph стоит в .venv; если интерпретатор не тот — предупреждаем
    try:
        import subprocess as _sp
        _chk = _sp.run([PYTHON, "-c", "import langgraph"], capture_output=True)
        if _chk.returncode != 0:
            warn("в выбранном интерпретаторе нет langgraph — "
                 "граф пойдёт запасным путём (без чекпоинтов)")
    except Exception:                                            # noqa: BLE001
        pass

    node = shutil.which("node")
    npm = shutil.which("npm")
    if not (node and npm):
        die("не найден node/npm — нужен для интерфейса (Node 18+)")
    ver = subprocess.run([node, "-v"], capture_output=True, text=True).stdout.strip()
    info(f"node {ver}")
    if int(ver.lstrip("v").split(".")[0]) < 18:
        warn("node старше 18 — vite может не запуститься")

    if not (FRONTEND / "node_modules").exists():
        warn("frontend/node_modules отсутствует — ставлю зависимости (npm ci)…")
        r = subprocess.run(["npm", "ci"], cwd=FRONTEND)
        if r.returncode != 0 or not (FRONTEND / "node_modules").exists():
            # у новых lock-файлов нет integrity-поля, ci не работает
            warn("npm ci не справился, пробую npm install…")
            r = subprocess.run(["npm", "install"], cwd=FRONTEND)
            if r.returncode != 0:
                die("не удалось поставить зависимости фронтенда")
        ok("зависимости установлены")


# ==========================================================================
#  Ожидание готовности
# ==========================================================================
def wait_http(url: str, timeout: float = 25.0, interval: float = 0.25) -> bool:
    """Ждём, пока endpoint начнёт отвечать 2xx/3xx/4xx (не 5xx-ошибку)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status < 500:
                    return True
        except urllib.error.HTTPError as e:
            if e.code < 500:
                return True
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(interval)
    return False


def wait_port(port: int, timeout: float = 40.0) -> bool:
    """Ждём, пока порт начнёт принимать соединения (vite может быть медленным)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if port_busy(port):
            return True
        time.sleep(0.25)
    return False


# ==========================================================================
#  Процессы
# ==========================================================================
class Services:
    """Держит дочерние процессы и гарантированно их убивает."""

    def __init__(self) -> None:
        self.procs: list[tuple[str, subprocess.Popen]] = []

    def spawn(self, name: str, argv: list[str], *, cwd: Path,
              env: dict | None = None) -> subprocess.Popen:
        log_path = RUN_DIR / f"{name}.log"
        log = log_path.open("w", encoding="utf-8", errors="replace")
        p = subprocess.Popen(
            argv, cwd=cwd, env=env,
            stdout=log, stderr=subprocess.STDOUT,
            start_new_session=True,      # своя группа — убьём целиком
        )
        self.procs.append((name, p))
        info(f"{name}: pid {p.pid}, лог {log_path.relative_to(ROOT)}")
        return p

    def alive(self, name: str) -> bool:
        return any(n == name and p.poll() is None for n, p in self.procs)

    def tail(self, name: str, lines: int = 12) -> str:
        path = RUN_DIR / f"{name}.log"
        if not path.exists():
            return "(лога нет)"
        text = path.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(text[-lines:])

    def stop(self) -> None:
        for name, p in reversed(self.procs):
            if p.poll() is not None:
                continue
            with contextlib.suppress(ProcessLookupError, PermissionError):
                # сначала вся группа: у vite/npm есть внуки
                os.killpg(os.getpgid(p.pid), signal.SIGTERM)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and any(p.poll() is None
                                                  for _, p in self.procs):
            time.sleep(0.15)
        for name, p in self.procs:
            if p.poll() is None:
                with contextlib.suppress(ProcessLookupError, PermissionError):
                    os.killpg(os.getpgid(p.pid), signal.SIGKILL)


# ==========================================================================
#  Режим --serve-static: раздача собранного dist/ + прокси к мосту
# ==========================================================================
#  Нужен для `launch.py --prebuilt`. Работает на стандартной библиотеке,
#  чтобы не тянуть vite-http-proxy ради одной статики.
class _ProxyHandler(__import__("http.server", fromlist=["BaseHTTPRequestHandler"])
                    .SimpleHTTPRequestHandler):
    """Отдаёт dist/, а /api и /out проксирует на агентный мост.

    SPA-маршруты (/order, /krystal, …) не совпадают с файлами, поэтому
    неизвестное отдаём index.html — так же, как это делает history-fallback
    в vite для одностраничных приложений.
    """

    bridge = "http://127.0.0.1:8787"
    dist = FRONTEND / "dist"
    proxied = ("/api", "/out")

    def _proxy(self) -> None:
        import urllib.parse
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None
        url = self.bridge + urllib.parse.quote(self.path)
        headers = {k: v for k, v in self.headers.items()
                   if k.lower() not in ("host", "content-length",
                                        "connection", "accept-encoding")}
        req = urllib.request.Request(url, data=body, headers=headers,
                                     method=self.command)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                payload, status = r.read(), r.status
                ctype = r.headers.get("Content-Type", "application/octet-stream")
        except urllib.error.HTTPError as e:
            payload, status = e.read(), e.code
            ctype = e.headers.get("Content-Type", "application/json")
        except (urllib.error.URLError, OSError) as e:
            payload = str(e).encode()
            status, ctype = 502, "text/plain; charset=utf-8"
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        with contextlib.suppress(BrokenPipeError, ConnectionResetError):
            self.wfile.write(payload)

    def _resolve(self) -> Path:
        rel = self.path.split("?", 1)[0].split("#", 1)[0].lstrip("/")
        target = (self.dist / rel).resolve()
        # защита от выхода за пределы dist/ через ../
        if not str(target).startswith(str(self.dist.resolve())):
            target = self.dist / "index.html"
        if target.is_dir():
            target = target / "index.html"
        return target if target.is_file() else self.dist / "index.html"

    def do_GET(self) -> None:                                    # noqa: N802
        if self.path.startswith(self.proxied):
            return self._proxy()
        super().do_GET()

    def do_POST(self) -> None:                                   # noqa: N802
        if self.path.startswith(self.proxied):
            return self._proxy()
        self.send_error(405, "Method Not Allowed")

    def do_HEAD(self) -> None:                                   # noqa: N802
        if self.path.startswith(self.proxied):
            return self._proxy()
        super().do_HEAD()

    def log_message(self, fmt: str, *args) -> None:
        pass  # тишина: логи пишутся в .run/ui.log

    def translate_path(self, path: str) -> str:
        return str(self._resolve())


def serve_static(port: int, host: str, bridge: str, dist: Path) -> None:
    """Точка входа для подпроцесса статики (`--serve-static`)."""
    import http.server

    _ProxyHandler.bridge = bridge.rstrip("/")
    _ProxyHandler.dist = dist
    httpd = http.server.ThreadingHTTPServer((host, port), _ProxyHandler)
    httpd.daemon_threads = True
    print(f"static: {dist} -> http://{host}:{port}/", flush=True)
    httpd.serve_forever()


# ==========================================================================
#  main
# ==========================================================================
def main() -> None:
    # Внутренний режим: подпроцесс статики для --prebuilt. Запускается
    # лаунчером, вручную трогать не нужно.
    if "--serve-static" in sys.argv:
        argv = sys.argv[sys.argv.index("--serve-static") + 1:]
        kv = dict(a.split("=", 1) for a in argv if "=" in a)
        serve_static(port=int(kv.get("port", "5173")),
                     host=kv.get("host", "127.0.0.1"),
                     bridge=kv.get("bridge", "http://127.0.0.1:8787"),
                     dist=Path(kv.get("dist", str(FRONTEND / "dist"))))
        return

    ap = argparse.ArgumentParser(
        description="Запуск Krystal Radio: мост + интерфейс по одному адресу")
    ap.add_argument("--host", default="127.0.0.1",
                    help="интерфейс слушает здесь (0.0.0.0 — вся сеть)")
    ap.add_argument("--port", type=int, default=5173,
                    help="порт интерфейса")
    ap.add_argument("--bridge-port", type=int, default=8787,
                    help="порт агентного моста")
    ap.add_argument("--no-open", action="store_true",
                    help="не открывать браузер")
    ap.add_argument("--prebuilt", action="store_true",
                    help="раздавать собранный dist/ без HMR (быстрее, стабильнее)")
    args = ap.parse_args()

    RUN_DIR.mkdir(exist_ok=True)
    check_env()

    host = args.host
    ui_port = args.port
    bridge_port = args.bridge_port

    # --- порты -----------------------------------------------------------
    if port_busy(ui_port, host):
        alt = free_port(ui_port + 1, host)
        warn(f"порт {ui_port} занят, интерфейс встанет на {alt}")
        ui_port = alt
    if port_busy(bridge_port):
        alt = free_port(bridge_port + 1, host)
        warn(f"порт {bridge_port} занят, мост встанет на {alt}")
        bridge_port = alt

    ui_addr = "localhost" if host in ("127.0.0.1", "0.0.0.0") else host
    url = f"http://{ui_addr}:{ui_port}/"
    if host == "0.0.0.0":
        url = f"http://127.0.0.1:{ui_port}/"

    svc = Services()
    stopping = False

    def shutdown(signum: int, _frame) -> None:
        nonlocal stopping
        if stopping:
            return
        stopping = True
        print("\nостанавливаю…", flush=True)
        svc.stop()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    try:
        # --- агентный мост -------------------------------------------------
        print("\n\033[1mKrystal Radio\033[0m — запуск проекта\n", flush=True)
        info("поднимаю агентный мост…")
        svc.spawn("bridge", [PYTHON, "backend/agent_bridge.py",
                             "--port", str(bridge_port), "--host",
                             "127.0.0.1" if host == "0.0.0.0" else host],
                  cwd=ROOT)
        if not wait_http(f"http://127.0.0.1:{bridge_port}/api/health", 25):
            warn("мост не отвечает на /api/health — последние строки лога:")
            for line in svc.tail("bridge", 15).splitlines():
                print(f"      {line}", flush=True)
            die("агентный мост не поднялся")
        ok(f"мост готов: http://127.0.0.1:{bridge_port}/api/health")

        # --- интерфейс ------------------------------------------------------
        bridge_url = f"http://127.0.0.1:{bridge_port}"
        env = {**os.environ, "KR_BRIDGE": bridge_url}

        info("поднимаю интерфейс…")
        if args.prebuilt:
            if not (FRONTEND / "node_modules").exists():
                die("frontend/node_modules отсутствует — сначала npm install")
            info("сборка интерфейса (vite build)…")
            r = subprocess.run(["npm", "run", "build"], cwd=FRONTEND,
                               capture_output=True, text=True)
            if r.returncode != 0:
                print(r.stdout[-2000:], file=sys.stderr)
                print(r.stderr[-2000:], file=sys.stderr)
                die("vite build не удался")
            dist = FRONTEND / "dist"
            if not (dist / "index.html").exists():
                die("vite build отработал, но не собрал dist/index.html")
            ok(f"статика собрана: {dist.relative_to(ROOT)}")
            svc.spawn("ui", [PYTHON, str(Path(__file__).resolve()),
                             "--serve-static", f"port={ui_port}",
                             f"host={'127.0.0.1' if host == '0.0.0.0' else host}",
                             f"bridge={bridge_url}", f"dist={dist}"],
                      cwd=ROOT)
        else:
            svc.spawn("ui", ["npm", "run", "dev", "--", "--port", str(ui_port),
                             "--host", host, "--strictPort"],
                      cwd=FRONTEND, env=env)

        if not wait_port(ui_port, 60):
            warn("интерфейс не слушает порт — последние строки лога:")
            for line in svc.tail("ui", 20).splitlines():
                print(f"      {line}", flush=True)
            svc.stop()
            raise SystemExit(1)

        ok(f"интерфейс готов: {url}")

        # --- сводка ---------------------------------------------------------
        print(f"""
  \033[1mОткрывайте:\033[0m  {url}

  \033[2minтерфейс\033[0m      {url}
  \033[2mмост (API)\033[0m     http://127.0.0.1:{bridge_port}/api/
  \033[2mлоги\033[0m            {RUN_DIR.relative_to(ROOT)}/bridge.log, ui.log
  \033[2mостановка\033[0m        Ctrl-C
""", flush=True)

        if not args.no_open:
            with contextlib.suppress(Exception):
                webbrowser.open(url)

        # --- ждём Ctrl-C, попутно поднимая упавшее -----------------------
        while not stopping:
            time.sleep(1)
            if not svc.alive("bridge"):
                print("\n\033[31mмост упал\033[0m — последние строки:", flush=True)
                for line in svc.tail("bridge", 15).splitlines():
                    print(f"      {line}", flush=True)
                break
            if not svc.alive("ui"):
                print("\n\033[31mинтерфейс упал\033[0m — последние строки:",
                      flush=True)
                for line in svc.tail("ui", 20).splitlines():
                    print(f"      {line}", flush=True)
                break
    finally:
        svc.stop()


if __name__ == "__main__":
    main()