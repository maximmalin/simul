"""Отдел Data Science / ML / AI-инженерии.

Отвечает за: сбор данных, feature engineering, обучение ML-моделей (суррогаты
размещения/аналогового дизайна), оценку моделей, MLOps (сохранение/загрузка).
Использует sklearn, scipy, pandas. Работает поверх placement_surrogate.py,
analog_sizer (будущий), fea_study.

Роли: data_engineer (сбор), ml_engineer (обучение), analyst (инсайты),
mlops (деплой). Параллельный агентский граф.

CLI: python3 backend/data_science_dept.py --task "..." --data out/placement_opt/records.jsonl
"""
from __future__ import annotations
import json, sys, time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import llm
except Exception:
    llm = None
try:
    import pandas as pd
    import numpy as np
    from sklearn.ensemble import GradientBoostingRegressor, RandomForestRegressor
    from sklearn.model_selection import cross_val_score
    from sklearn.preprocessing import StandardScaler
    import joblib
    HAS_ML = True
except Exception:
    HAS_ML = False

ROOT = Path(__file__).resolve().parents[1]


def collect_data(data_path: str | Path) -> dict:
    """Сбор и первичный анализ данных."""
    p = Path(data_path)
    if not p.exists():
        return {"error": f"file not found: {p}"}
    if p.suffix == ".jsonl":
        rows = [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
        return {"rows": len(rows), "columns": list(rows[0].keys()) if rows else [], "sample": rows[:3]}
    if p.suffix == ".json":
        d = json.loads(p.read_text())
        return {"rows": 1, "keys": list(d.keys()) if isinstance(d, dict) else len(d)}
    if p.suffix == ".csv":
        if not HAS_ML:
            return {"error": "pandas not available"}
        df = pd.read_csv(p)
        return {"rows": len(df), "columns": list(df.columns), "dtypes": {c: str(t) for c, t in df.dtypes.items()},
                "describe": df.describe().to_dict() if len(df) > 0 else {}}
    return {"error": f"unsupported format: {p.suffix}"}


def train_model(data_path: str | Path, target: str = "score", out_path: str = "out/models/ds_model.joblib") -> dict:
    """Обучение ML-модели на данных."""
    if not HAS_ML:
        return {"error": "ML deps not available"}
    p = Path(data_path)
    if p.suffix == ".jsonl":
        rows = [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
        if not rows:
            return {"error": "no data"}
        # попытка извлечь features/target
        import numpy as np
        # generic: используем числовые поля
        df = pd.json_normalize(rows)
        num_cols = df.select_dtypes(include=["number"]).columns.tolist()
        if target not in num_cols:
            target = num_cols[-1] if num_cols else None
        if target is None:
            return {"error": "no numeric target"}
        X = df[num_cols].drop(columns=[target]).fillna(0).values
        y = df[target].fillna(0).values
        scaler = StandardScaler()
        Xs = scaler.fit_transform(X)
        model = GradientBoostingRegressor(n_estimators=100, max_depth=3, random_state=42)
        model.fit(Xs, y)
        cv = cross_val_score(model, Xs, y, cv=min(5, len(X)), scoring="r2")
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump({"model": model, "scaler": scaler, "features": num_cols, "target": target}, out)
        return {"samples": len(X), "features": num_cols, "target": target,
                "cv_r2": float(cv.mean()), "model_path": str(out)}
    return {"error": f"unsupported format: {p.suffix}"}


def analyze(data_path: str | Path) -> dict:
    """Аналитический инсайт (LLM или эвристика)."""
    info = collect_data(data_path)
    if "error" in info:
        return info
    insight = ""
    if llm is not None and getattr(llm, "ENABLED", False):
        try:
            msgs = [{"role": "user", "content":
                f"Ты — data scientist. Проанализируй данные:\n{json.dumps(info, ensure_ascii=False)[:2000]}\n"
                "Дай 3-5 инсайтов и рекомендаций."}]
            turn = llm.chat(msgs, fast=True, max_tokens=800)
            insight = getattr(turn, "text", "") if turn else ""
        except Exception:
            pass
    if not insight:
        insight = f"Данных: {info.get('rows', '?')} строк. Колонки: {info.get('columns', info.get('keys', []))}"
    return {"info": info, "insight": insight}


def dept_loop(task: str, data_path: str, out_dir: str = "out/ds_result") -> dict:
    work = Path(out_dir)
    work.mkdir(parents=True, exist_ok=True)
    # параллельно: сбор данных + обучение (если есть данные)
    results = {}
    with ThreadPoolExecutor(max_workers=2) as ex:
        f_collect = ex.submit(collect_data, data_path)
        f_analyze = ex.submit(analyze, data_path)
        results["collect"] = f_collect.result()
        results["analyze"] = f_analyze.result()
    # обучение модели если данные числовые
    try:
        tr = train_model(data_path, out_path=str(work / "ds_model.joblib"))
        results["train"] = tr
    except Exception as e:
        results["train"] = {"error": str(e)}
    (work / "ds_report.json").write_text(json.dumps(results, ensure_ascii=False, indent=2))
    return results


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Data Science / ML department")
    ap.add_argument("--task", default="анализ данных")
    ap.add_argument("--data", default="out/placement_opt/records.jsonl")
    ap.add_argument("--out", default="out/ds_result")
    a = ap.parse_args()
    r = dept_loop(a.task, a.data, a.out)
    print(json.dumps(r, ensure_ascii=False, indent=2)[:1500])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())