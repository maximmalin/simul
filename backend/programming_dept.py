"""Отдел программирования (Bolt-style, БЕЗ StackBlitz/WebContainers).

Самостоятельный агентский флоу: генерация кода на любом языке через локальный
тулчейн (интерпретаторы/компиляторы в системе), с ролями:
  - coder: пишет код по задаче
  - tester: запускает тесты/проверки
  - reviewer: ревью кода (LLM)
  - debugger: исправляет ошибки

Всё локально, без внешних сервисов. Поддерживает языки из bolt_agent.RUNNERS.

CLI: python3 backend/programming_dept.py --task "..." --lang python --out out/prog_dept
"""
from __future__ import annotations
import json, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bolt_agent import generate_code, run_code, RUNNERS

try:
    import llm
except Exception:
    llm = None

ROOT = Path(__file__).resolve().parents[1]


def review_code(code: str, lang: str, task: str) -> str:
    if llm is None or not getattr(llm, "ENABLED", False):
        return "review skipped (no LLM)"
    msgs = [{"role": "user", "content":
        f"Ты — senior code reviewer. Проверь {lang}-код на задачу:\n{task}\n\n"
        f"Код:\n{code}\n\n"
        "Найди баги, проблемы стиля, крайние случаи. Верни краткий список замечаний "
        "(или 'OK' если всё хорошо)."}]
    try:
        turn = llm.chat(msgs, fast=True, max_tokens=800)
        return getattr(turn, "text", "") if turn else "no response"
    except Exception as e:
        return f"review error: {e}"


def write_tests(code: str, lang: str, task: str) -> str:
    if llm is None or not getattr(llm, "ENABLED", False):
        return ""
    msgs = [{"role": "user", "content":
        f"Напиши тесты для {lang}-кода, решающего задачу:\n{task}\n\n"
        f"Код:\n{code}\n\n"
        "Верни ТОЛЬКО код тестов, без markdown."}]
    try:
        turn = llm.chat(msgs, fast=True, max_tokens=1200)
        text = getattr(turn, "text", "") if turn else ""
        if text.startswith("```"):
            lines = text.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            text = "\n".join(lines)
        return text
    except Exception:
        return ""


def run_tests(lang: str, test_code: str, workdir: Path) -> tuple[int, str]:
    if not test_code.strip():
        return 0, "no tests generated"
    ext = RUNNERS[lang][0]
    # tests in same file for simplicity
    src = workdir / f"test_main.{ext}"
    src.write_text(test_code, encoding="utf-8")
    binp = workdir / "test_bin"
    cmd_template = RUNNERS[lang][1]
    cmd = [c.format(file=src.name, bin=binp.name) for c in cmd_template]
    shell_cmd = " ".join(cmd)
    try:
        p = subprocess.run(shell_cmd, shell=True, cwd=str(workdir), capture_output=True,
                           text=True, timeout=60)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except Exception as e:
        return 1, str(e)


def dept_loop(task: str, lang: str, iters: int = 5, out_dir: str = "out/prog_dept") -> dict:
    work = Path(out_dir)
    work.mkdir(parents=True, exist_ok=True)
    history = []
    code = ""
    review_notes = ""
    test_code = ""

    # 1. coder
    code = generate_code(task, lang)
    # 2. reviewer
    review_notes = review_code(code, lang, task)
    # 3. tester: генерируем тесты
    test_code = write_tests(code, lang, task)

    for it in range(iters):
        rc, output = run_code(lang, code, work)
        # запускаем тесты если есть
        trc, tout = run_tests(lang, test_code, work) if test_code else (0, "no tests")
        history.append({"iter": it, "run_rc": rc, "test_rc": trc,
                        "output": output[-500:], "test_output": tout[-500:]})
        if rc == 0 and trc == 0:
            (work / "final_code.txt").write_text(code, encoding="utf-8")
            (work / "final_tests.txt").write_text(test_code, encoding="utf-8")
            (work / "review.txt").write_text(review_notes, encoding="utf-8")
            (work / "result.json").write_text(json.dumps(
                {"task": task, "lang": lang, "success": True, "iters": it + 1,
                 "review": review_notes[:500]}, ensure_ascii=False, indent=2))
            return {"success": True, "iters": it + 1, "review": review_notes[:500]}
        # debugger: исправляем на основе ошибок
        err = output if rc != 0 else tout
        code = generate_code(task, lang, error=err)
        review_notes = review_code(code, lang, task)

    (work / "final_code.txt").write_text(code, encoding="utf-8")
    (work / "result.json").write_text(json.dumps(
        {"task": task, "lang": lang, "success": False, "iters": iters}, ensure_ascii=False, indent=2))
    return {"success": False, "iters": iters}


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Programming department (Bolt-style, no StackBlitz)")
    ap.add_argument("--task", required=True)
    ap.add_argument("--lang", default="python", choices=list(RUNNERS.keys()))
    ap.add_argument("--iters", type=int, default=5)
    ap.add_argument("--out", default="out/prog_dept")
    a = ap.parse_args()
    r = dept_loop(a.task, a.lang, a.iters, a.out)
    print(json.dumps(r, ensure_ascii=False, indent=2))
    return 0 if r["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())