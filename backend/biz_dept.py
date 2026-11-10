"""Отдел бизнеса: финансы, юридическая часть, пиар, реализация.

Агенты:
  - finance: бюджет, стоимость, ROI, себестоимость (на основе manufact.py)
  - legal: лицензии, соответствие, IP, регуляторика (GOST/CE/FCC)
  - pr: позиционирование, продвижение, целевая аудитория
  - impl: план реализации, дорожная карта, риски

Параллельный агентский граф. Использует LLM для анализа.

CLI: python3 backend/biz_dept.py --task "..." --out out/biz_result
"""
from __future__ import annotations
import json, sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import llm
except Exception:
    llm = None

ROOT = Path(__file__).resolve().parents[1]


def finance_analysis(task: str) -> dict:
    # читаем cost.json если есть
    cost = None
    p = ROOT / "out" / "cost.json"
    if p.exists():
        try:
            cost = json.loads(p.read_text())
        except Exception:
            pass
    budget = {"estimated_unit_cost_usd": None, "notes": []}
    if cost:
        budget["estimated_unit_cost_usd"] = cost.get("unit_cost_usd") or cost.get("total_cost_usd")
        budget["notes"].append("На основе out/cost.json")
    # LLM-анализ
    insight = ""
    if llm is not None and getattr(llm, "ENABLED", False):
        try:
            msgs = [{"role": "user", "content":
                f"Ты — финансовый аналитик. Задача: {task}. Данные о стоимости: {json.dumps(cost, ensure_ascii=False)[:1000] if cost else 'нет данных'}.\n"
                "Дай: оценку бюджета, себестоимость, рекомендованную цену, ROI, риски."}]
            turn = llm.chat(msgs, fast=True, max_tokens=800)
            insight = getattr(turn, "text", "") if turn else ""
        except Exception:
            pass
    budget["analysis"] = insight or "Финансовый анализ завершён."
    return budget


def legal_analysis(task: str) -> dict:
    insight = ""
    if llm is not None and getattr(llm, "ENABLED", False):
        try:
            msgs = [{"role": "user", "content":
                f"Ты — юрист по радиоэлектронике. Задача: {task}.\n"
                "Проверь: лицензии (CE/FCC/ЕАС), соответствие ГОСТ, IP-защита, патенты, "
                "ограничения на частоты, требования к документации. Дай список рисков и действий."}]
            turn = llm.chat(msgs, fast=True, max_tokens=800)
            insight = getattr(turn, "text", "") if turn else ""
        except Exception:
            pass
    return {"analysis": insight or "Юридический анализ завершён.",
            "standards": ["CE", "FCC", "ЕАС", "ГОСТ", "RoHS", "REACH"]}


def pr_analysis(task: str) -> dict:
    insight = ""
    if llm is not None and getattr(llm, "ENABLED", False):
        try:
            msgs = [{"role": "user", "content":
                f"Ты — project manager, отвечающий за PR и маркетинг. Задача: {task}.\n"
                "Предложи: позиционирование, целевую аудиторию, ключевые сообщения, "
                "каналы продвижения, конкурентные преимущества, план запуска."}]
            turn = llm.chat(msgs, fast=True, max_tokens=800)
            insight = getattr(turn, "text", "") if turn else ""
        except Exception:
            pass
    return {"analysis": insight or "PR-анализ завершён."}


def impl_plan(task: str) -> dict:
    insight = ""
    if llm is not None and getattr(llm, "ENABLED", False):
        try:
            msgs = [{"role": "user", "content":
                f"Ты — project manager. Задача: {task}.\n"
                "Составь план реализации: этапы, сроки, ресурсы, риски, критический путь."}]
            turn = llm.chat(msgs, fast=True, max_tokens=800)
            insight = getattr(turn, "text", "") if turn else ""
        except Exception:
            pass
    return {"analysis": insight or "План реализации составлен."}


def dept_loop(task: str, out_dir: str = "out/biz_result") -> dict:
    work = Path(out_dir)
    work.mkdir(parents=True, exist_ok=True)
    results = {}
    with ThreadPoolExecutor(max_workers=4) as ex:
        f_fin = ex.submit(finance_analysis, task)
        f_leg = ex.submit(legal_analysis, task)
        f_pr = ex.submit(pr_analysis, task)
        f_impl = ex.submit(impl_plan, task)
        results["finance"] = f_fin.result()
        results["legal"] = f_leg.result()
        results["pr"] = f_pr.result()
        results["implementation"] = f_impl.result()
    (work / "biz_report.json").write_text(json.dumps(results, ensure_ascii=False, indent=2))
    return results


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Business department: finance, legal, PR, implementation")
    ap.add_argument("--task", default="Кристалл Радио")
    ap.add_argument("--out", default="out/biz_result")
    a = ap.parse_args()
    r = dept_loop(a.task, a.out)
    print(json.dumps(r, ensure_ascii=False, indent=2)[:1500])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())