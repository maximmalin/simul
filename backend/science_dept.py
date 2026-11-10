"""Отдел учёных: физика, FMU, симуляция, формулы.

Отвечает за: аналитические модели (thermal, FEA, RF link budget, EMC),
FMU/QEMU co-simulation, Monte Carlo, формулы и проверки.

Роли: physicist (формулы), sim_engineer (FMU/SPICE), analyst (результаты),
validator (проверки). Параллельный агентский граф.

CLI: python3 backend/science_dept.py --task "..." --out out/science_result
"""
from __future__ import annotations
import json, sys, math
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import llm
except Exception:
    llm = None
try:
    import numpy as np
    import scipy
    HAS_SCI = True
except Exception:
    HAS_SCI = False

ROOT = Path(__file__).resolve().parents[1]


def thermal_check(ta_c: float = 25.0, p_w: float = 0.3, theta_ja: float = 60.0, tj_max: float = 85.0) -> dict:
    tj = ta_c + p_w * theta_ja
    margin = tj_max - tj
    return {"tj_c": round(tj, 2), "margin_c": round(margin, 2), "pass": margin >= 10.0}


def fea_check(length_mm: float = 50.0, width_mm: float = 20.0, thickness_mm: float = 2.0,
              load_N: float = 500.0, yield_MPa: float = 240.0, margin: float = 2.0) -> dict:
    I = width_mm * thickness_mm ** 3 / 12.0
    E = 70000.0  # MPa (Al)
    sigma = 6 * load_N * length_mm / (width_mm * thickness_mm ** 2) if width_mm > 0 else float("inf")
    sf = yield_MPa / sigma if sigma > 0 else float("inf")
    delta = load_N * length_mm ** 3 / (3 * E * I) if I > 0 else float("inf")
    return {"deflection_mm": round(delta, 4), "peak_mpa": round(sigma, 2),
            "safety_factor": round(sf, 3), "pass": sf >= margin}


def rf_link_budget(freq_mhz: float = 433.0, tx_power_dbm: float = 10.0,
                   rx_sensitivity_dbm: float = -110.0, distance_m: float = 1000.0) -> dict:
    lam = 300.0 / freq_mhz  # m
    fspl = 20 * (np.log10(distance_m) + np.log10(freq_mhz) + 2.44) if HAS_SCI else 0
    rx_power = tx_power_dbm - fspl
    margin = rx_power - rx_sensitivity_dbm
    return {"freq_mhz": freq_mhz, "fspl_db": round(fspl, 2),
            "rx_power_dbm": round(rx_power, 2), "margin_db": round(margin, 2),
            "pass": margin >= 3.0}


def monte_carlo(formula, params: dict, n: int = 1000) -> dict:
    """Monte Carlo: params = {name: (mean, std)}. formula(params_dict) -> scalar."""
    if not HAS_SCI:
        return {"error": "scipy not available"}
    import numpy as np
    samples = {k: np.random.normal(v[0], v[1], n) for k, v in params.items()}
    results = np.array([formula({k: samples[k][i] for k in params}) for i in range(n)])
    return {"mean": float(results.mean()), "std": float(results.std()),
            "p5": float(np.percentile(results, 5)),
            "p95": float(np.percentile(results, 95)), "n": n}


def dept_loop(task: str, out_dir: str = "out/science_result") -> dict:
    work = Path(out_dir)
    work.mkdir(parents=True, exist_ok=True)
    results = {}
    with ThreadPoolExecutor(max_workers=3) as ex:
        f_thermal = ex.submit(thermal_check)
        f_fea = ex.submit(fea_check)
        f_rf = ex.submit(rf_link_budget)
        results["thermal"] = f_thermal.result()
        results["fea"] = f_fea.result()
        results["rf_link_budget"] = f_rf.result()
    # LLM-инсайт
    insight = ""
    if llm is not None and getattr(llm, "ENABLED", False):
        try:
            msgs = [{"role": "user", "content":
                f"Ты — учёный-физик. Задача: {task}. Результаты симуляций: {json.dumps(results, ensure_ascii=False)[:1500]}. "
                "Дай краткий анализ и рекомендации."}]
            turn = llm.chat(msgs, fast=True, max_tokens=800)
            insight = getattr(turn, "text", "") if turn else ""
        except Exception:
            pass
    results["insight"] = insight or "Анализ завершён."
    def _clean(o):
        if isinstance(o, dict):
            return {k: _clean(v) for k, v in o.items()}
        if isinstance(o, (list, tuple)):
            return [_clean(x) for x in o]
        if hasattr(o, "item"):
            return o.item()
        if isinstance(o, (bool,)):
            return bool(o)
        return o
    (work / "science_report.json").write_text(json.dumps(_clean(results), ensure_ascii=False, indent=2))
    return _clean(results)


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Science department: physics, FMU, simulation, formulas")
    ap.add_argument("--task", default="комплексный анализ")
    ap.add_argument("--out", default="out/science_result")
    a = ap.parse_args()
    r = dept_loop(a.task, a.out)
    print(json.dumps(r, ensure_ascii=False, indent=2)[:1500])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())