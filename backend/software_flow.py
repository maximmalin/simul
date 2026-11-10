"""Параллельный агентский граф разработки ПО/прошивки.

Агенты (consultant, coder, tester, reviewer, debugger) работают в графе:
  consultant -> coder (ТЗ на ПО)
  coder -> tester, reviewer (параллельно)
  tester/reviewer -> debugger (при ошибках) -> coder (итерация)

Отдельный флоу, не затрагивает PCB/конструктора. Без StackBlitz.

CLI: python3 backend/software_flow.py --task "..." --lang c --out out/software_result
"""
from __future__ import annotations
import json, sys, time, threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from software_spec import write_outputs as write_sw_spec
from programming_dept import generate_code, run_code, review_code, write_tests, run_tests
try:
    import llm
except Exception:
    llm = None

ROOT = Path(__file__).resolve().parents[1]


def run_flow(task: str, lang: str = "c", iters: int = 5, out_dir: str = "out/software_result",
             use_tz: bool = False) -> dict:
    work = Path(out_dir)
    work.mkdir(parents=True, exist_ok=True)
    reqs = []
    if use_tz:
        spec = write_sw_spec(ROOT)
        reqs = spec.get("requirements", [])
    req_text = "\n".join(f"- {r['label']}: " + "; ".join(r.get("excerpts", [])[:2]) for r in reqs)
    portability = ""
    if lang in ("c", "cpp"):
        portability = ("\n\nВАЖНО: пиши переносимый C, компилируемый через gcc -fsyntax-only "
                       "(без HAL/STM32-зависимостей, используй заглушки/макросы).")
    full_task = task + portability + ("\n\nТребования из ТЗ:\n" + req_text if req_text and use_tz else "")

    code = ""
    test_code = ""
    review_notes = ""
    arch_notes = ""
    history = []

    # architect: проектирует структуру решения
    try:
        if llm is not None and getattr(llm, "ENABLED", False):
            msgs = [{"role": "user", "content":
                f"Ты — архитектор ПО. Спроектируй структуру решения для задачи:\n{full_task}\n"
                f"Язык: {lang}. Верни краткий план: модули, функции, интерфейсы (5-10 строк)."}]
            turn = llm.chat(msgs, fast=True, max_tokens=600)
            arch_notes = getattr(turn, "text", "") if turn else ""
    except Exception:
        arch_notes = ""

    for it in range(iters):
        task_with_arch = full_task + (f"\n\nАрхитектура (от архитектора):\n{arch_notes}" if arch_notes else "")
        # coder
        code = generate_code(task_with_arch, lang, error=(history[-1].get("error") if history else None))

        # tester + reviewer параллельно
        def _test():
            tc = write_tests(code, lang, full_task)
            # gcc syntax check
            trc, tout = run_code(lang, code, work)
            # PlatformIO build (если доступен и C/C++) — дополнительная проверка
            try:
                import shutil as _sh
                if _sh.which("pio") and lang in ("c", "cpp"):
                    from platformio_test import run_pio
                    pr = run_pio(code, out_dir=str(work / "pio"))
                    if pr.get("build_rc", 0) != 0:
                        trc = pr["build_rc"]
                        tout = pr.get("build_output", "")[-500:]
            except Exception:
                pass
            return tc, trc, tout
        def _review():
            return review_code(code, lang, full_task)

        with ThreadPoolExecutor(max_workers=2) as ex:
            f_test = ex.submit(_test)
            f_review = ex.submit(_review)
            test_code, trc, tout = f_test.result()
            review_notes = f_review.result()

        # run code
        rc, output = run_code(lang, code, work)
        history.append({"iter": it, "run_rc": rc, "test_rc": trc,
                        "output": output[-500:], "test_output": tout[-500:],
                        "review": review_notes[:300]})

        if rc == 0 and trc == 0:
            (work / "final_code.txt").write_text(code, encoding="utf-8")
            (work / "final_tests.txt").write_text(test_code, encoding="utf-8")
            (work / "review.txt").write_text(review_notes, encoding="utf-8")
            report = {"task": task, "lang": lang, "success": True, "iters": it + 1,
                      "review": review_notes[:500], "requirements": [r["key"] for r in reqs],
                      "parallel": True}
            (work / "flow_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
            return report

    (work / "final_code.txt").write_text(code, encoding="utf-8")
    report = {"task": task, "lang": lang, "success": False, "iters": iters,
              "review": review_notes[:500], "parallel": True}
    (work / "flow_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Parallel agent flow for software/firmware")
    ap.add_argument("--task", required=True)
    ap.add_argument("--lang", default="c")
    ap.add_argument("--iters", type=int, default=5)
    ap.add_argument("--out", default="out/software_result")
    ap.add_argument("--use-tz", action="store_true", help="использовать ТЗ проекта (SDR)")
    a = ap.parse_args()
    r = run_flow(a.task, a.lang, a.iters, a.out, use_tz=a.use_tz)
    print(json.dumps(r, ensure_ascii=False, indent=2))
    return 0 if r.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())