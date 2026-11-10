"""Bolt-стиль агентский флоу для программистов: генерация кода на любом языке
с запуском и автоисправлением (open-source-идея bolt.new: prompt -> run -> fix).

Поддерживает языки: python, javascript, typescript, bash, c, cpp, go, rust
(через выбор интерпретатора/компилятора). Работает с локальным LLM (llm.py)
или детерминированным офлайн-шаблоном.

CLI: python3 backend/bolt_agent.py --task "..." --lang python --out out/bolt_result
"""
from __future__ import annotations
import json, subprocess, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import llm
except Exception:
    llm = None

ROOT = Path(__file__).resolve().parents[1]

import urllib.request, urllib.parse, json as _json

def search_repos(task: str, lang: str, top_k: int = 5) -> list[dict]:
    """Поиск готовых решений на GitHub (не писать с нуля)."""
    q = urllib.parse.quote(f"{task} language:{lang}")
    url = f"https://api.github.com/search/repositories?q={q}&sort=stars&order=desc&per_page={top_k}"
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                                  "User-Agent": "krystal-radio-agent"})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = _json.loads(r.read())
        out = []
        for it in data.get("items", [])[:top_k]:
            out.append({"name": it.get("full_name"), "url": it.get("html_url"),
                        "stars": it.get("stargazers_count", 0),
                        "description": (it.get("description") or "")[:200]})
        return out
    except Exception:
        return []


RUNNERS = {
    "python": ("py", ["python3", "{file}"]),
    "javascript": ("js", ["node", "{file}"]),
    "typescript": ("ts", ["npx", "ts-node", "{file}"]),
    "bash": ("sh", ["bash", "{file}"]),
    "c": ("c", ["gcc", "-fsyntax-only", "{file}"]),
    "cpp": ("cpp", ["g++", "-fsyntax-only", "{file}"]),
    "go": ("go", ["go", "run", "{file}"]),
    "rust": ("rs", ["rustc", "{file}", "-o", "{bin}", "&&", "{bin}"]),
}


def generate_code(task: str, lang: str, error: str | None = None) -> str:
    runner = RUNNERS.get(lang, RUNNERS["python"])
    ext = runner[0]
    if llm is not None and getattr(llm, "ENABLED", False):
        msgs = [{"role": "system", "content": "You are a code generator. Output ONLY a fenced code block. No explanations outside."},
                {"role": "user", "content":
            f"Write {lang} code solving: {task}\n"
            f"Output ONLY a fenced ```{lang} code block. Nothing else.\n"
            + (f"\nPrevious error:\n{error}\nFix it." if error else "")}]
        try:
            turn = llm.chat(msgs, fast=True, max_tokens=4000)
            text = getattr(turn, "text", None) if turn is not None else None
            if text:
                # strip markdown fences
                text = text.strip()
                import re as _re
                if "```" in text:
                    m = _re.search(r"```(?:\w+)?\n(.*?)```", text, _re.S)
                    if m:
                        text = m.group(1).strip()
                return text
        except Exception:
            pass
    # fallback: echo template
    if lang == "python":
        return f'# task: {task}\nprint("hello from bolt agent")\n'
    return f"// task: {task}\n"


def run_code(lang: str, code: str, workdir: Path) -> tuple[int, str]:
    ext, cmd_template = RUNNERS[lang]
    src = workdir / f"main.{ext}"
    src.write_text(code, encoding="utf-8")
    binp = workdir / "main_bin"
    cmd = [c.format(file=src.name, bin=binp.name) for c in cmd_template]
    shell_cmd = " ".join(cmd)
    try:
        p = subprocess.run(shell_cmd, shell=True, cwd=str(workdir), capture_output=True,
                           text=True, timeout=60)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, "TIMEOUT"
    except Exception as e:
        return 1, str(e)


def agent_loop(task: str, lang: str, iters: int = 5, out_dir: str = "out/bolt_result") -> dict:
    work = Path(out_dir)
    work.mkdir(parents=True, exist_ok=True)
    history = []
    # сначала ищем готовые решения в репозиториях
    repos = search_repos(task, lang)
    repo_hint = ""
    if repos:
        repo_hint = "\n\nНайдены готовые решения (используй как reference, не копируй слепо):\n" + "\n".join(f"- {r['name']} ({r['stars']}★): {r['description']} — {r['url']}" for r in repos)
    code = generate_code(task + repo_hint, lang)
    for it in range(iters):
        rc, output = run_code(lang, code, work)
        history.append({"iter": it, "rc": rc, "output": output[-1000:]})
        if rc == 0:
            (work / "final_code.txt").write_text(code, encoding="utf-8")
            (work / "result.json").write_text(json.dumps(
                {"task": task, "lang": lang, "success": True, "iters": it + 1,
                 "output": output[-500:]}, ensure_ascii=False, indent=2))
            return {"success": True, "iters": it + 1, "output": output[-500:]}
        # fix
        code = generate_code(task, lang, error=output)
    (work / "final_code.txt").write_text(code, encoding="utf-8")
    (work / "result.json").write_text(json.dumps(
        {"task": task, "lang": lang, "success": False, "iters": iters,
         "output": history[-1]["output"]}, ensure_ascii=False, indent=2))
    return {"success": False, "iters": iters, "output": history[-1]["output"]}


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Bolt-style code agent (multi-language)")
    ap.add_argument("--task", required=True)
    ap.add_argument("--lang", default="python", choices=list(RUNNERS.keys()))
    ap.add_argument("--iters", type=int, default=5)
    ap.add_argument("--out", default="out/bolt_result")
    a = ap.parse_args()
    r = agent_loop(a.task, a.lang, a.iters, a.out)
    print(json.dumps(r, ensure_ascii=False, indent=2))
    return 0 if r["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())