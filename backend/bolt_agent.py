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

RUNNERS = {
    "python": ("py", ["python3", "{file}"]),
    "javascript": ("js", ["node", "{file}"]),
    "typescript": ("ts", ["npx", "ts-node", "{file}"]),
    "bash": ("sh", ["bash", "{file}"]),
    "c": ("c", ["gcc", "{file}", "-o", "{bin}", "&&", "{bin}"]),
    "cpp": ("cpp", ["g++", "{file}", "-o", "{bin}", "&&", "{bin}"]),
    "go": ("go", ["go", "run", "{file}"]),
    "rust": ("rs", ["rustc", "{file}", "-o", "{bin}", "&&", "{bin}"]),
}


def generate_code(task: str, lang: str, error: str | None = None) -> str:
    runner = RUNNERS.get(lang, RUNNERS["python"])
    ext = runner[0]
    if llm is not None and getattr(llm, "ENABLED", False):
        msgs = [{"role": "user", "content":
            f"Ты — senior-разработчик. Напиши {lang}-код, решающий задачу:\n{task}\n"
            f"Верни ТОЛЬКО код, без markdown-обёртки, без пояснений.\n"
            + (f"\nПредыдущая ошибка:\n{error}\nИсправь её." if error else "")}]
        try:
            turn = llm.chat(msgs, fast=True, max_tokens=2000)
            text = getattr(turn, "text", None) if turn is not None else None
            if text:
                # strip markdown fences
                text = text.strip()
                if text.startswith("```"):
                    lines = text.splitlines()
                    if lines and lines[0].startswith("```"):
                        lines = lines[1:]
                    if lines and lines[-1].startswith("```"):
                        lines = lines[:-1]
                    text = "\n".join(lines)
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
    code = generate_code(task, lang)
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