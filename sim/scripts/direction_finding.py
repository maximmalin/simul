#!/usr/bin/env python3
"""Пеленгация (direction finding) и диаграмма «направление + дальность».

Модуль считает направление на источник по разности фаз между элементами
антенной решётки (фазовый интерферометр) и рисует единую диаграмму:
азимут (bearing) источника + кольца дальности приёма.

Физика:
    Плоская волна под углом θ к нормали решётки. Разность хода между
    соседними элементами с базой d: ΔR = d·sinθ, разность фаз
        Δφ = 2π·d·sinθ / λ,   λ = c/f,
    откуда оценка направления
        θ = asin( λ·Δφ / (2π·d) ).
    При d ≤ λ/2 пеленг однозначен; при d > λ/2 — зона однозначности
    ±asin(λ/2d).
    СКО оценки угла при фазовом шуме σ_φ: σ_θ = λ/(2π·d·cosθ)·σ_φ.

AntennaSim (https://github.com/EA1FUO/AntennaSim) — тот же движок NEC2.
Сгенерированный `.nec` импортируется в AntennaSim (`POST /api/v1/convert/import`)
и считается локальным `nec2c`; при запущенном сервере — через
`POST /api/v1/simulate` (опция `--antennasim-url`).

Запуск:
    python3 sim/scripts/direction_finding.py --out out
    python3 sim/scripts/direction_finding.py --antennasim-url http://localhost:8000
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
from pathlib import Path

import link_budget as LB

C0 = LB.C0
NEC_BIN = "/usr/bin/nec2c"


# ---------------------------------------------------------------------------
#  Фазовый интерферометр
# ---------------------------------------------------------------------------
def phase_difference(theta_rad: float, f_hz: float, baseline_m: float) -> float:
    """Разность фаз Δφ (рад) между элементами для угла θ (рад от нормали)."""
    lam = C0 / f_hz
    return 2.0 * math.pi * baseline_m * math.sin(theta_rad) / lam


def doa_from_phase(dphi_rad: float, f_hz: float, baseline_m: float,
                   ambiguity_order: int = 0) -> float | None:
    """Оценка угла θ (рад) по разности фаз; None — вне зоны однозначности."""
    lam = C0 / f_hz
    s = lam * (dphi_rad + 2.0 * math.pi * ambiguity_order) / (2.0 * math.pi * baseline_m)
    if abs(s) > 1.0:
        return None
    return math.asin(s)


def doa_sigma_rad(sigma_phi_rad: float, theta_rad: float, f_hz: float,
                  baseline_m: float) -> float:
    """СКО оценки угла (рад) при фазовом шуме σ_φ (рад)."""
    lam = C0 / f_hz
    denom = 2.0 * math.pi * baseline_m * math.cos(theta_rad)
    if abs(denom) < 1e-9:
        return math.inf
    return abs(lam / denom) * sigma_phi_rad


def unambiguous_fov_deg(f_hz: float, baseline_m: float) -> float:
    """Полуширина зоны однозначности ±θ_max (град)."""
    lam = C0 / f_hz
    if baseline_m <= lam / 2.0:
        return 90.0
    return math.degrees(math.asin(min(1.0, lam / (2.0 * baseline_m))))


def array_factor(theta_deg: float, f_hz: float, baseline_m: float,
                 n_elem: int = 4) -> float:
    """Множитель решётки |AF| (≤1) для угла θ (град)."""
    psi = 2.0 * math.pi * baseline_m * math.sin(math.radians(theta_deg)) / (C0 / f_hz)
    denom = n_elem * math.sin(psi / 2.0)
    if abs(denom) < 1e-9:
        return 1.0
    return abs(math.sin(n_elem * psi / 2.0) / denom)


def array_gain_dbi(f_hz: float, baseline_m: float, n_elem: int = 4,
                   element_gain_dbi: float = 2.15, theta_deg: float = 0.0) -> float:
    """Аналитическое усиление решётки (диполи + множитель решётки).

    Используется как резерв, когда локальный `nec2c` недоступен/отказывает;
    авторитетный расчёт — NEC-2 в AntennaSim.
    """
    af = array_factor(theta_deg, f_hz, baseline_m, n_elem)
    return element_gain_dbi + 10.0 * math.log10(max(n_elem, 1)) + 20.0 * math.log10(max(af, 1e-6))


# ---------------------------------------------------------------------------
#  NEC2 / AntennaSim
# ---------------------------------------------------------------------------
def write_nec_array(path: Path, f_hz: float, baseline_m: float,
                    n_elem: int = 4, dipole_frac: float = 0.5) -> Path:
    """Записать решётку интерферометра в формате NEC-2 (совместим с AntennaSim)."""
    f_mhz = f_hz / 1e6
    lam = C0 / f_hz
    half = lam * dipole_frac / 2.0
    r_wire = 0.001
    lines = [
        "CM Krystal Radio DF interferometer array",
        f"CM {n_elem} half-wave dipoles, baseline={baseline_m*1000:.1f}mm @ {f_mhz:.1f}MHz",
        "CE",
    ]
    tag = 1
    for i in range(n_elem):
        x = (i - (n_elem - 1) / 2.0) * baseline_m
        lines.append(f"GW {tag} 11 {x:.4f} 0.0 0.0 {x:.4f} 0.0 {2*half:.4f} {r_wire:.5f}")
        tag += 1
    lines += [
        "GN 1 0 0 0 1.0 0.0",
        "EX 0 1 6 1.0 0.0 0.0 0.0 0.0 0.0 0.0",
        f"FR 0 1 0 0 {f_mhz:.4f} 0.0",
        "RP 0 181 361 1000 0.0 360.0 0.0 360.0",
        "EN",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def wires_json(f_hz: float, baseline_m: float, n_elem: int = 4) -> list[dict]:
    """Решётка как wires JSON (формат AntennaSim `/api/v1/simulate`)."""
    lam = C0 / f_hz
    half = lam * 0.5 / 2.0
    wires = []
    for i in range(n_elem):
        x = (i - (n_elem - 1) / 2.0) * baseline_m
        wires.append({"tag": i + 1, "segments": 11, "x1": x, "y1": 0.0, "z1": 0.0,
                      "x2": x, "y2": 0.0, "z2": 2 * half, "radius": 0.001})
    return wires


def run_nec(nec_file: Path, timeout: float = 60.0) -> str:
    try:
        r = subprocess.run([NEC_BIN, "-i", str(nec_file)], capture_output=True,
                           text=True, timeout=timeout)
        return (r.stdout or "") + (r.stderr or "")
    except (OSError, subprocess.SubprocessError):
        return ""


def parse_gain_dbi(text: str) -> float | None:
    best = None
    for line in text.splitlines():
        if "GAIN" not in line.upper():
            continue
        for tok in line.replace("=", " ").replace(",", " ").split():
            try:
                v = float(tok)
            except ValueError:
                continue
            if 0.0 < v < 60.0 and (best is None or v > best):
                best = v
    return best


def antennasim_simulate(url: str, wires: list[dict], f_hz: float,
                        timeout: float = 30.0) -> dict | None:
    """Считать решётку на живом AntennaSim; None, если сервер недоступен."""
    import urllib.request
    body = {
        "wires": wires,
        "excitations": [{"wire_tag": 1, "segment": 6,
                         "voltage_real": 1.0, "voltage_imag": 0.0}],
        "ground": {"ground_type": "free_space"},
        "frequency": {"start_mhz": f_hz / 1e6, "stop_mhz": f_hz / 1e6, "steps": 1},
    }
    try:
        req = urllib.request.Request(
            url.rstrip("/") + "/api/v1/simulate",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None


# ---------------------------------------------------------------------------
#  Диаграмма (SVG): направление + дальность
# ---------------------------------------------------------------------------
def _pol(theta_deg: float, frac: float, cx: float, cy: float,
         radius: float) -> tuple[float, float]:
    a = math.radians(theta_deg)
    return cx + radius * frac * math.sin(a), cy - radius * frac * math.cos(a)


def render_svg(scenario: dict, path: str | Path) -> Path:
    """Полярная диаграмма азимута + кольца дальности + геометрия базы."""
    ch = scenario["channel"]
    d = scenario["baseline_half_wave_m"]
    theta_est = scenario["theta_est_deg"]
    r_km = ch["range_realistic_m"] / 1000.0

    W, H = 1000, 660
    cx, cy, R = 260, 350, 190
    s: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
        f'viewBox="0 0 {W} {H}" font-family="DejaVu Sans, Arial, sans-serif">',
        '<defs><marker id="arr" markerWidth="9" markerHeight="9" refX="7" refY="3" '
        'orient="auto"><path d="M0,0 L7,3 L0,6 z" fill="#ffb020"/></marker></defs>',
        '<rect width="100%" height="100%" fill="#0f1319"/>',
        f'<text x="{W/2}" y="34" fill="#e6ebf2" font-size="20" text-anchor="middle">'
        f'Пеленгация: направление и дальность приёма</text>',
        f'<text x="{W/2}" y="56" fill="#8b95a3" font-size="12" text-anchor="middle">'
        f'фазовый интерферометр NEC-2 (совместим с AntennaSim EA1FUO) · '
        f'f = {ch["f_mhz"]:.0f} МГц</text>',
    ]
    # полярная сетка
    for frac in (0.25, 0.5, 0.75, 1.0):
        s.append(f'<circle cx="{cx}" cy="{cy}" r="{R*frac:.1f}" fill="none" '
                 f'stroke="#232a35" stroke-width="1"/>')
        s.append(f'<text x="{cx+5:.0f}" y="{cy-R*frac+12:.0f}" fill="#6f7987" '
                 f'font-size="10">{r_km*frac:.0f} км</text>')
    for ang in range(0, 360, 30):
        x2, y2 = _pol(ang, 1.0, cx, cy, R)
        s.append(f'<line x1="{cx}" y1="{cy}" x2="{x2:.1f}" y2="{y2:.1f}" '
                 f'stroke="#1a212b" stroke-width="1"/>')
        lx, ly = _pol(ang, 1.13, cx, cy, R)
        s.append(f'<text x="{lx:.1f}" y="{ly:.1f}" fill="#6f7987" font-size="10" '
                 f'text-anchor="middle">{ang}°</text>')
    # эмиттеры
    for e in scenario["emitters"]:
        ex, ey = _pol(e["bearing_deg"], e["range_frac"], cx, cy, R)
        col = "#29b6a3" if e.get("is_target") else "#2f6fdb"
        s.append(f'<line x1="{cx}" y1="{cy}" x2="{ex:.1f}" y2="{ey:.1f}" '
                 f'stroke="{col}" stroke-width="{2.2 if e.get("is_target") else 1.3}" '
                 f'opacity="0.9"/>')
        s.append(f'<circle cx="{ex:.1f}" cy="{ey:.1f}" '
                 f'r="{5.5 if e.get("is_target") else 4}" fill="{col}"/>')
        s.append(f'<text x="{ex+9:.1f}" y="{ey-6:.1f}" fill="#c9d2dd" font-size="11">'
                 f'{e["label"]}  θ={e["bearing_deg"]:.0f}°</text>')
    s.append(f'<circle cx="{cx}" cy="{cy}" r="6" fill="#e6ebf2"/>')
    s.append(f'<text x="{cx}" y="{cy+22:.0f}" fill="#8b95a3" font-size="10" '
             f'text-anchor="middle">приёмник</text>')
    s.append(f'<text x="{cx}" y="{cy-R-14:.0f}" fill="#8b95a3" font-size="11" '
             f'text-anchor="middle">нормаль решётки (0°)</text>')
    s.append(f'<text x="{ex+9:.1f}" y="{ey-22:.1f}" fill="#9fe6dc" font-size="12">'
             f'пеленг θ̂ = {theta_est:.1f}°</text>')

    # --- геометрия базы ---
    gx, gy = 640, 270
    e1, e2 = (gx, gy), (gx + 150, gy)
    s.append(f'<text x="{gx+75}" y="{gy-165}" fill="#e6ebf2" font-size="14" '
             f'text-anchor="middle">Геометрия фазового интерферометра</text>')
    s.append(f'<line x1="{e1[0]}" y1="{e1[1]}" x2="{e2[0]}" y2="{e2[1]}" '
             f'stroke="#8b95a3" stroke-width="2"/>')
    for (px, py), lab in ((e1, "эл. 1"), (e2, "эл. 2")):
        s.append(f'<circle cx="{px}" cy="{py}" r="5" fill="#2f6fdb"/>')
        s.append(f'<text x="{px:.0f}" y="{py+20}" fill="#8b95a3" font-size="11" '
                 f'text-anchor="middle">{lab}</text>')
    s.append(f'<line x1="{e1[0]}" y1="{e1[1]+40}" x2="{e2[0]}" y2="{e2[1]+40}" '
             f'stroke="#29b6a3" stroke-width="1.2"/>')
    s.append(f'<text x="{(e1[0]+e2[0])/2:.0f}" y="{e1[1]+36}" fill="#9fe6dc" '
             f'font-size="11" text-anchor="middle">d = {d*1000:.1f} мм (λ/2)</text>')
    ang = math.radians(scenario["theta_true_deg"])
    for k in range(6):
        off = k * 24
        x0 = gx - 160 - off * math.sin(1.2) + 120 * math.sin(0)
        dx, dy = math.sin(math.radians(90) - 0), 0
        # фронты волны: перпендикулярны направлению волны (угол theta к нормали)
        ux, uy = math.sin(math.radians(scenario["theta_true_deg"])), \
            math.cos(math.radians(scenario["theta_true_deg"]))
        px = gx - 150 + 40 * (1 - k) - 90 * math.sin(math.radians(scenario["theta_true_deg"]))
        py = gy - 120 + 90 * math.cos(math.radians(scenario["theta_true_deg"]))
        s.append(f'<line x1="{px:.1f}" y1="{py:.1f}" '
                 f'x2="{px+70*math.cos(math.radians(theta_est)):.1f}" '
                 f'y2="{py+70*-math.sin(math.radians(theta_est)):.1f}" '
                 f'stroke="#3a4656" stroke-width="1"/>')
    s.append(f'<line x1="{gx-120:.0f}" y1="{gy-130:.0f}" x2="{e1[0]}" y2="{e1[1]}" '
             f'stroke="#ffb020" stroke-width="1.4" marker-end="url(#arr)"/>')
    s.append(f'<text x="{gx-120}" y="{gy-140}" fill="#ffb020" font-size="11">'
             f'волна, θ = {scenario["theta_true_deg"]:.0f}°</text>')

    eq_y = 470
    eqs = [
        "ΔR = d·sinθ        Δφ = 2π·d·sinθ / λ        θ = asin( λ·Δφ / (2π·d) )",
        f"λ = {scenario['lambda_m']*1000:.1f} мм;  d = {d*1000:.1f} мм;  "
        f"Δφ = {scenario['dphi_deg']:.1f}°;  θ̂ = {theta_est:.1f}°;  "
        f"σ_θ = {scenario['sigma_theta_deg']:.1f}° (σ_φ = {scenario['sigma_phi_deg']:.0f}°)",
        f"зона однозначности ±{scenario['fov_deg']:.0f}°;  "
        f"дальность приёма (реальная) ≈ {r_km:.0f} км;  "
        f"G = {ch['g_rx_dbi']:.1f} дБи;  f = {ch['f_mhz']:.0f} МГц",
    ]
    for i, e in enumerate(eqs):
        s.append(f'<text x="40" y="{eq_y+i*22}" fill="#c9d2dd" font-size="13">{e}</text>')
    s.append(f'<text x="40" y="{H-16}" fill="#6f7987" font-size="11">'
             f'источник геометрии — NEC-2 (nec2c); файл {scenario["nec_file"]} '
             f'импортируется в AntennaSim (EA1FUO/AntennaSim)</text>')
    s.append("</svg>")
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(s), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
#  Сценарий
# ---------------------------------------------------------------------------
def build_scenario(channel_key: str = "channel_b", theta_true_deg: float = 35.0,
                   sigma_phi_deg: float = 10.0, use_array_baseline: bool = False) -> dict:
    ch = {c.key: c for c in LB.CHANNELS}[channel_key]
    f_hz = ch.f_hz
    lam = C0 / f_hz
    d = lam / 2.0
    if use_array_baseline:
        d = {"channel_b": 0.0917, "channel_c": 0.0388,
             "channel_a": 0.0150}.get(channel_key, lam / 2.0)
    theta_true = math.radians(theta_true_deg)
    dphi = phase_difference(theta_true, f_hz, d)
    theta_est = doa_from_phase(dphi, f_hz, d)
    theta_est_deg = math.degrees(theta_est) if theta_est is not None else float("nan")
    sigma_theta_deg = math.degrees(
        doa_sigma_rad(math.radians(sigma_phi_deg), theta_true, f_hz, d))
    budget = LB.channel_budget(ch, eirp_dbm=30.0, margin_db=20.0)
    return {
        "channel": budget,
        "lambda_m": lam,
        "baseline_half_wave_m": d,
        "theta_true_deg": theta_true_deg,
        "theta_est_deg": theta_est_deg,
        "dphi_deg": math.degrees(math.atan2(math.sin(phase_difference(
            math.radians(theta_true_deg), f_hz, d)), math.cos(phase_difference(
            math.radians(theta_true_deg), f_hz, d)))),
        "sigma_phi_deg": sigma_phi_deg,
        "sigma_theta_deg": math.degrees(doa_sigma_rad(
            math.radians(sigma_phi_deg), math.radians(theta_true_deg), f_hz, d)),
        "fov_deg": unambiguous_fov_deg(f_hz, d),
        "nec_file": "out/df_array.nec",
        "emitters": [
            {"label": "источник", "bearing_deg": theta_true_deg,
             "range_frac": 0.72, "is_target": True},
            {"label": "мешающий", "bearing_deg": 128.0, "range_frac": 0.45,
             "is_target": False},
            {"label": "слабый", "bearing_deg": 292.0, "range_frac": 0.30,
             "is_target": False},
        ],
    }


def _parse_args(argv=None):
    p = argparse.ArgumentParser(description="Krystal Radio — пеленгация и диаграмма")
    p.add_argument("--out", default="out")
    p.add_argument("--channel", default="channel_b",
                   choices=[c.key for c in LB.CHANNELS])
    p.add_argument("--theta-deg", type=float, default=35.0)
    p.add_argument("--sigma-phi-deg", type=float, default=10.0)
    p.add_argument("--antennasim-url", default=None)
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    sc = build_scenario(args.channel, args.theta_deg, args.sigma_phi_deg)
    ch = sc["channel"]
    f_hz = ch["f_hz"]
    d = sc["baseline_half_wave_m"]

    nec_out = out / "df_array.nec"
    write_nec_array(nec_out, f_hz, d)
    if Path("sim/nec").is_dir():
        write_nec_array(Path("sim/nec") / "df_array.nec", f_hz, d)

    gain_dbi = None
    backend = "nec2c"
    if args.antennasim_url:
        res = antennasim_simulate(args.antennasim_url, wires_json(f_hz, d), f_hz)
        if res:
            gain_dbi = res.get("gain_dbi") or res.get("gain")
            backend = "AntennaSim"
    if gain_dbi is None:
        gain_dbi = parse_gain_dbi(run_nec(nec_out))
    if gain_dbi is None:
        gain_dbi = array_gain_dbi(f_hz, d, n_elem=4)
        backend = "analytic array factor (nec2c недоступен)"

    sc["gain_dbi"] = gain_dbi
    sc["backend"] = backend
    (out / "direction_finding.json").write_text(
        json.dumps(sc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    svg = render_svg(sc, out / "direction_finding.svg")

    print(f"[df] backend: {backend}")
    print(f"[df] λ={sc['lambda_m']*1000:.1f} мм  d={d*1000:.1f} мм  "
          f"Δφ={sc['dphi_deg']:.1f}°  θ̂={sc['theta_est_deg']:.1f}°  "
          f"σ_θ={sc['sigma_theta_deg']:.1f}°  зона ±{sc['fov_deg']:.0f}°")
    print(f"[df] -> {out/'direction_finding.json'}, {svg}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
