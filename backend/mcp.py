"""MCP-сервер: инструменты над проектом для внешних агентов.

Зачем это нужно
----------------
OpenCode, Claude Code и Codex умеют говорить с внешним сервером по
протоколу MCP. Один раз подняв этот сервер, агент получает ровно тот же
набор возможностей, что и веб-интерфейс: разобрать заказ в цели,
собрать, прочитать результаты, спросить человека.

Это не копия веб-интерфейса и не обёртка над промптом. Узлы графа,
конфликты целей и запрет исполнять что попало остаются теми же —
агент получает те же типизированные инструменты, а не свободу писать
в системе сборки.

    opencode.json / .mcp.json:
      { "mcp": { "krystal": {
          "type": "http", "url": "http://127.0.0.1:8787/mcp" } } }

Реализация намеренно на стандартной библиотеке: MCP — обычный
JSON-RPC поверх HTTP, тянуть ради него клиентскую библиотеку
незачем.

Протокол
--------
    POST /mcp          JSON-RPC 2.0
      initialize       -> capabilities
      tools/list       -> описания инструментов
      tools/call       -> вызов инструмента
      ping             -> {}

Ответы — по правилам JSON-RPC: `result` либо `error` с кодом.
"""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents import load_project, tools as T          # noqa: E402
from agents.types import Project                     # noqa: E402

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "krystal-radio"

#: инструменты, которые сервер отдаёт наружу. Набор тот же, что и у
#: внутреннего агента, — иначе внешний агент получил бы больше прав,
#: чем внутренний, а это ровно то, чего мы избегаем.
EXPOSED = ("list_targets", "describe_target", "submit_plan",
           "run_target", "read_artifacts")


# ==========================================================================
#  Проект
# ==========================================================================
def project_root() -> str:
    import os
    return os.environ.get("KR_PROJECT_ROOT", str(Path.cwd()))


_CACHE: dict[str, Project] = {}

#: папка песочницы на сессию агента: переиспользуется между вызовами
_SESSION = uuid.uuid4().hex[:8]


def get_project() -> Project:
    """Описание проекта перечитывается при изменении mtime профиля.

    Иначе агент, поднятый один раз, видел бы устаревшие цели после
    того, как Makefile поправили.
    """
    root = Path(project_root()).resolve()
    prof = next((root / n for n in ("agents.project.yaml",
                                    "agents.project.json")
                 if (root / n).is_file()), None)
    stamp = 0.0
    if prof:
        try:
            stamp = prof.stat().st_mtime
        except OSError:
            stamp = 0.0
    key = f"{root}"
    if _CACHE.get(key) is None or _CACHE[key + ":mtime"] != stamp:  # type: ignore
        _CACHE[key] = load_project(root)
        _CACHE[key + ":mtime"] = stamp            # type: ignore[index]
    return _CACHE[key]


# ==========================================================================
#  Исполнение инструментов
# ==========================================================================
def _run_target(name: str) -> dict:
    """Собрать одну цель тем же способом, что и внутренний граф.

    В песочнице. Внешний агент — OpenCode, Codex — получает ровно те же
    ограничения, что и внутренний: он выбирает из объявленных целей и не
    пишет в исходники проекта. Иначе MCP стал бы обходным путём к
    файловой системе мимо всех задуманных границ.
    """
    from agents import engine, sandbox

    proj = get_project()
    if proj.target(name) is None:
        return {"status": "failed", "exit_code": -2, "log": [],
                "error": f"цели «{name}» нет в проекте"}

    # папка на сессию агента: следующий вызов переиспользует её,
    # а не плодит копии исходников на каждый вызов
    box = _sandbox_for(proj)
    engine.set_workdir(box)
    try:
        step = {"id": "mcp", "target": name,
                "title": proj.target(name).title or name,
                "agent": proj.agent_owner.get(name, "agent.build")}
        res = engine._execute_one(proj, step)
    finally:
        engine.set_workdir(None)

    if box:
        try:
            sandbox.collect_artifacts(f"mcp-{_SESSION}", proj.root)
        except Exception:                                       # noqa: BLE001
            pass
    return res


def _sandbox_for(proj: Project) -> str | None:
    """Песочница сессии агента; создаётся при первом вызове."""
    from agents import sandbox

    path = Path(proj.root).resolve() / "work" / "orders" / f"mcp-{_SESSION}"
    if path.is_dir():
        return str(path)
    try:
        return str(sandbox.create(f"mcp-{_SESSION}", proj.root))
    except Exception:                                           # noqa: BLE001
        return None


def call_tool(name: str, args: dict) -> dict:
    """Вызвать инструмент и вернуть результат в виде MCP-контента."""
    proj = get_project()
    bindings = T.make_bindings(proj, _run_target, tier="full")

    if name not in EXPOSED:
        return _content(f"Инструмент «{name}» не выставлен наружу. "
                        f"Доступны: {', '.join(EXPOSED)}", is_error=True)

    out = T.invoke(bindings, name, args or {})
    return _content(out)


def _content(text: str, is_error: bool = False) -> dict:
    return {"content": [{"type": "text", "text": text}],
            "isError": bool(is_error)}


# ==========================================================================
#  Описания инструментов для tools/list
# ==========================================================================
def tool_list() -> list[dict]:
    """Схемы в формате MCP (не OpenAI): обёртка inputSchema."""
    schemas = T.tool_schemas(get_project(), tier="full")
    out = []
    for s in schemas:
        fn = s["function"]
        if fn["name"] not in EXPOSED:
            continue
        out.append({
            "name": fn["name"],
            "description": fn["description"],
            "inputSchema": fn["parameters"],
        })
    return out


# ==========================================================================
#  Обработка JSON-RPC
# ==========================================================================
def handle(payload: dict) -> dict | None:
    """Разобрать запрос. None — уведомление (ответа не требуется)."""
    method = payload.get("method")
    rid = payload.get("id")
    params = payload.get("params") or {}

    def ok(result: Any) -> dict:
        return {"jsonrpc": "2.0", "id": rid, "result": result}

    def err(code: int, text: str) -> dict:
        return {"jsonrpc": "2.0", "id": rid,
                "error": {"code": code, "message": text}}

    if method == "initialize":
        return ok({
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME,
                           "version": _project_version()},
        })

    if method in ("notifications/initialized", "initialized"):
        return None                     # уведомление, ответа нет

    if method == "ping":
        return ok({})

    if method == "tools/list":
        return ok({"tools": tool_list()})

    if method == "tools/call":
        name = params.get("name", "")
        args = params.get("arguments") or {}
        if not isinstance(args, dict):
            return err(-32602, "arguments должны быть объектом")
        return ok(call_tool(name, args))

    if method == "shutdown":
        return ok({})

    return err(-32601, f"метод не реализован: {method}")


def _project_version() -> str:
    try:
        import importlib.metadata as md
        return md.version("krystal-radio")
    except Exception:                                    # noqa: BLE001
        return "0.0.0"


# ==========================================================================
#  HTTP
# ==========================================================================
def handle_http(path: str, body: bytes,
                headers: dict) -> tuple[int, dict, bytes]:
    """Точка входа HTTP."""
    if path == "/mcp" and headers.get("post", "").lower() == "options":
        return 204, {}, b""              # CORS-предполёт для браузерных клиентов

    if path not in ("/mcp", "/"):
        return 404, {"Content-Type": "application/json"}, \
            b'{"error":"not found"}'

    if headers.get("post", "").lower() != "post":
        return 405, {"Content-Type": "application/json"}, \
            b'{"error":"use POST with JSON-RPC"}'

    try:
        payload = json.loads(body.decode("utf-8") or "{}")
    except ValueError:
        return 200, {"Content-Type": "application/json"}, json.dumps({
            "jsonrpc": "2.0", "id": None,
            "error": {"code": -32700, "message": "parse error"},
        }).encode()

    # пакетный запрос — список
    if isinstance(payload, list):
        replies = [r for r in (handle(p) for p in payload) if r]
        data = json.dumps(replies, ensure_ascii=False).encode()
        return 200, {"Content-Type": "application/json"}, data

    reply = handle(payload if isinstance(payload, dict) else {})
    if reply is None:
        return 202, {}, b""               # уведомление принято

    data = json.dumps(reply, ensure_ascii=False).encode()
    return 200, {"Content-Type": "application/json"}, data


# ==========================================================================
#  CLI
# ==========================================================================
def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(
        description="MCP-сервер над проектом Krystal Radio")
    ap.add_argument("--check", action="store_true",
                    help="показать инструменты и выйти")
    ap.add_argument("--json", action="store_true",
                    help="вывести список инструментов в JSON")
    opts = ap.parse_args()

    if opts.check or opts.json:
        proj = get_project()
        if opts.json:
            print(json.dumps({"project": proj.name,
                              "tools": tool_list()},
                             indent=2, ensure_ascii=False))
            return 0
        print(f"MCP-сервер проекта «{proj.name}»\n")
        print(f"  корень : {proj.root}")
        print(f"  раннер : {proj.runner}")
        print(f"  целей  : {len(proj.targets)}\n")
        print("  инструменты наружу:")
        for t in tool_list():
            req = t["inputSchema"].get("required", [])
            args = ", ".join(f"{k}{'' if k in req else '?'}" for k in
                             t["inputSchema"].get("properties", {}))
            print(f"    {t['name']:16s} ({args or 'без аргументов'})")
            print(f"        {t['description'][:88]}")
        print("\n  подключение к агенту:")
        print('    { "mcp": { "krystal": {')
        print('        "type": "http",')
        print('        "url": "http://127.0.0.1:8787/mcp" } } }')
        return 0

    print("MCP работает как маршрут агентного моста: "
          "`make agent-api`, затем POST на /mcp", file=sys.stderr)
    print("Проверить: python3 backend/mcp.py --check", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())