"""ML-суррогат для оценки размещения PCB (scikit-learn).

Обучается на истории оптимизации (.run/placement_opt/records.jsonl) и
предсказывает, насколько изменится score при предложенном ходе. Используется
как дополнительный пропоузер в placement_loop (--use-ml), чтобы вместо
случайных джиттеров предлагать ходы с высокой вероятностью улучшения.

Локальный венв: venv/bin/python (scikit-learn, joblib).
"""
from __future__ import annotations
import json, math, sys, pickle
from pathlib import Path
from typing import Any

try:
    import joblib
    from sklearn.ensemble import GradientBoostingRegressor, RandomForestRegressor
    from sklearn.model_selection import cross_val_score
    from sklearn.preprocessing import StandardScaler
    import numpy as np
except Exception as e:
    joblib = None
    print(f"ML deps missing: {e}", file=sys.stderr)

ROOT = Path(__file__).resolve().parents[1]


def _records(path: str | Path) -> list[dict]:
    out = []
    p = Path(path)
    if not p.exists():
        return out
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def _feat(rec: dict) -> list[float] | None:
    mv = rec.get("move")
    if not mv:
        return None
    sb = rec.get("score_before")
    sa = rec.get("score_after")
    if sb is None or sa is None:
        return None
    bd = rec.get("breakdown_after") or {}
    # component index: hash ref name to a stable bucket
    ref = str(mv.get("ref", ""))
    comp_idx = (hash(ref) % 1000) / 1000.0
    x = float(mv.get("x_mm", 0))
    y = float(mv.get("y_mm", 0))
    rot = float(mv.get("rot_deg", 0))
    # сырые значения — масштабирование делает StandardScaler
    return [
        comp_idx, x, y, rot,
        float(sb), float(bd.get("trace_length_mm", 0)),
        float(bd.get("vias", 0)), float(bd.get("drc", 0)),
        float(bd.get("ratsnest_mm", 0)),
        float(bd.get("placement_penalty", 0)),
    ]


def build_dataset(records_path: str | Path) -> tuple[Any, Any]:
    recs = _records(records_path)
    X, y = [], []
    for r in recs:
        f = _feat(r)
        if f is None:
            continue
        sb = r["score_before"]; sa = r["score_after"]
        # positive = improvement (score decreased)
        target = float(sb) - float(sa)
        X.append(f)
        y.append(target)
    if not X:
        return None, None
    return np.asarray(X, dtype=float), np.asarray(y, dtype=float)


def train(records_path: str | Path = ".run/placement_opt/records.jsonl",
          out_model: str | Path = "out/models/placement_surrogate.joblib",
          model_kind: str = "gbr") -> dict:
    if joblib is None:
        return {"error": "scikit-learn not available"}
    X, y = build_dataset(records_path)
    if X is None or len(X) < 5:
        return {"error": f"not enough data ({0 if X is None else len(X)} samples)"}
    if model_kind == "gbr":
        model = GradientBoostingRegressor(n_estimators=200, max_depth=4, learning_rate=0.05, random_state=42)
    else:
        model = RandomForestRegressor(n_estimators=200, max_depth=8, random_state=42)
    # универсальная нормализация: StandardScaler по обучающей выборке (без хардкода масштабов)
    scaler = StandardScaler()
    Xs = scaler.fit_transform(X)
    model.fit(Xs, y)
    # cross-val score (R2)
    try:
        cv = cross_val_score(model, Xs, y, cv=min(5, len(X)), scoring="r2")
        cv_mean = float(cv.mean())
    except Exception:
        cv_mean = None
    out = Path(out_model)
    out.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump({"model": model, "scaler": scaler}, out)
    report = {
        "samples": len(X),
        "cv_r2": cv_mean,
        "feature_importances": [float(x) for x in getattr(model, "feature_importances_", [])],
        "model_path": str(out),
        "model_kind": model_kind,
        "normalized": True,
    }
    (out.parent / "placement_surrogate_report.json").write_text(json.dumps(report, indent=2))
    return report


def load_model(path: str | Path = "out/models/placement_surrogate.joblib"):
    if joblib is None:
        return None
    p = Path(path)
    if not p.exists():
        return None
    obj = joblib.load(p)
    # поддержка старого формата (голая модель) и нового (dict с scaler)
    if isinstance(obj, dict) and "model" in obj:
        return obj
    return {"model": obj, "scaler": None}


def predict(model, move: dict, score_before: float, breakdown: dict | None = None) -> float:
    if model is None:
        return 0.0
    breakdown = breakdown or {}
    ref = str(move.get("ref", ""))
    comp_idx = (hash(ref) % 1000) / 1000.0
    x = float(move.get("x_mm", 0)); y = float(move.get("y_mm", 0)); rot = float(move.get("rot_deg", 0))
    feat = [comp_idx, x, y, rot, float(score_before),
            float(breakdown.get("trace_length_mm", 0)),
            float(breakdown.get("vias", 0)),
            float(breakdown.get("drc", 0)),
            float(breakdown.get("ratsnest_mm", 0)),
            float(breakdown.get("placement_penalty", 0))]
    import numpy as np
    X = np.asarray(feat, dtype=float).reshape(1, -1)
    scaler = model.get("scaler") if isinstance(model, dict) else None
    if scaler is not None:
        X = scaler.transform(X)
    m = model["model"] if isinstance(model, dict) else model
    return float(m.predict(X)[0])


def suggest(model, candidates: list[dict], score_before: float, breakdown: dict | None = None,
            top_k: int = 5) -> list[dict]:
    if model is None:
        return candidates[:top_k]
    scored = []
    for c in candidates:
        pred = predict(model, c, score_before, breakdown)
        scored.append((pred, c))
    scored.sort(key=lambda t: t[0], reverse=True)
    return [c for _, c in scored[:top_k]]


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Train placement surrogate (ML)")
    ap.add_argument("--records", default=".run/placement_opt/records.jsonl")
    ap.add_argument("--out", default="out/models/placement_surrogate.joblib")
    ap.add_argument("--kind", choices=["gbr", "rf"], default="gbr")
    a = ap.parse_args()
    r = train(a.records, a.out, a.kind)
    print(json.dumps(r, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
