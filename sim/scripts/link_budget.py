#!/usr/bin/env python3
"""Дальность приёма (link budget) — энергетика тракта и радиус уверенного приёма.

Модуль считает «дальность приёма» (reception distance) для каждого канала
антенной системы: это максимальное расстояние, на котором приёмник ещё
различает сигнал с заданным отношением сигнал/шум.

Модель:
    1. Шумовая дорожка приёмника (REQ-37):
           N [дБм] = 10·log10(k·T₀·B·1000) + NF
       при T₀ = 290 K, NF = 3.5 дБ (REQ-04).
    2. Порог обнаружимости (MDS, REQ-36/37):
           S_min = N + SNR_треб  (SNR_треб = 10 дБ на пороге).
    3. Уравнение Friis -> максимальная дальность радиовидимости:
           R = λ/(4π) · 10^( (EIRP + G_rx − S_min) / 20 ).
    4. Дальность «в реальной обстановке» — свободная дальность с запасом
       на помехи/многолучёвость/рельеф (по умолчанию 20 дБ).

Тот же расчёт используется в `sim/scripts/antenna_sim.py` (Friis) и
`sim/nec/run_nec.py`; здесь он сведён к одной таблице по всем требованиям
и сохранён в `out/link_budget.json` + диаграмма `out/link_budget.svg`.

Запуск:
    python3 sim/scripts/link_budget.py --out out
"""
from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from pathlib import Path

C0 = 299792458.0          # скорость света, м/с
K_B = 1.380649e-23        # постоянная Больцмана, Дж/К
T0_KELVIN = 290.0         # опорная температура, К

# Требования-источники (см. requirements/blocks/):
NF_DB = 3.5               # REQ-04: коэффициент шума входного тракта
BW_SSB_HZ = 2700.0        # REQ-36/37: полоса измерения 2.7 кГц (SSB)
SNR_DETECT_DB = 10.0      # REQ-36/37: S+N/N на пороге обнаружимости
MDS_DBM = -110.0          # REQ-37: минимальный различимый сигнал
NOISE_FLOOR_MAX_DBM = -120.0   # REQ-37: норма собственного шума


@dataclass(frozen=True)
class Channel:
    """Канал антенной системы (см. hardware/antenna/README.md)."""

    key: str
    title: str
    f_hz: float
    g_rx_dbi: float
    panel_mm: float
    standard: str


# Три канала покрытия 0–7 ГГц (REQ-ANT-ALL / REQ-01).
CHANNELS: tuple[Channel, ...] = (
    Channel("channel_a", "Канал А — змейка 200 МГц", 200e6, 2.15, 150.0,
            "SWR≤2.0, 0–900 МГц"),
    Channel("channel_b", "Канал Б — решётка 2×2, 2.45 ГГц", 2.45e9, 6.0, 150.0,
            "SWR≤2.0, 1.2–3.0 ГГц"),
    Channel("channel_c", "Канал В — решётка 4×4, 5.8 ГГц", 5.8e9, 12.0, 150.0,
            "SWR≤2.0, 3.0–7.0 ГГц"),
)


# ---------------------------------------------------------------------------
#  Энергетика тракта
# ---------------------------------------------------------------------------
def noise_floor_dbm(bw_hz: float, nf_db: float = NF_DB,
                    t_kelvin: float = T0_KELVIN) -> float:
    """Приведённый ко входу шум согласованной нагрузки с учётом NF."""
    if bw_hz <= 0:
        raise ValueError("bw_hz must be > 0")
    return 10.0 * math.log10(K_B * t_kelvin * bw_hz * 1e3) + nf_db


def sensitivity_dbm(bw_hz: float = BW_SSB_HZ, nf_db: float = NF_DB,
                    snr_db: float = SNR_DETECT_DB) -> float:
    """MDS: минимальная мощность, различимая при SNR ≥ snr_db."""
    return noise_floor_dbm(bw_hz, nf_db) + snr_db


def fspl_db(range_m: float, f_hz: float) -> float:
    """Потери в свободном пространстве (Friis), дБ."""
    return 20.0 * math.log10(4.0 * math.pi * range_m * f_hz / C0)


def max_range_m(eirp_dbm: float, g_rx_dbi: float, f_hz: float,
                bw_hz: float = BW_SSB_HZ, nf_db: float = NF_DB,
                snr_db: float = SNR_DETECT_DB) -> float:
    """Максимальная дальность радиовидимости (Friis + порог приёмника).

    EIRP — эквивалентная изотропно излучаемая мощность передатчика
    (P_tx + G_tx), дБм. G_rx — усиление приёмной антенны, дБи.
    """
    sens = sensitivity_dbm(bw_hz, nf_db, snr_db)
    fspl_budget = eirp_dbm + g_rx_dbi - sens          # допустимые потери, дБ
    lam = C0 / f_hz
    return lam / (4.0 * math.pi) * 10.0 ** (fspl_budget / 20.0)


def realistic_range_m(range_m: float, margin_db: float = 20.0) -> float:
    """Свободная дальность с запасом на помехи/многолучёвость (дБ)."""
    if margin_db <= 0:
        return range_m
    return range_m / 10.0 ** (margin_db / 20.0)


def channel_budget(ch: Channel, eirp_dbm: float, margin_db: float,
                   bw_hz: float = BW_SSB_HZ, nf_db: float = NF_DB,
                   snr_db: float = SNR_DETECT_DB) -> dict:
    """Полный бюджет одного канала."""
    n = noise_floor_dbm(bw_hz, nf_db)
    sens = n + snr_db
    r_fs = max_range_m(eirp_dbm, ch.g_rx_dbi, ch.f_hz, bw_hz, nf_db, snr_db)
    r_real = realistic_range_m(r_fs, margin_db)
    return {
        "key": ch.key,
        "title": ch.title,
        "f_hz": ch.f_hz,
        "f_mhz": ch.f_hz / 1e6,
        "lambda_m": C0 / ch.f_hz,
        "g_rx_dbi": ch.g_rx_dbi,
        "noise_floor_dbm": n,
        "sensitivity_dbm": sens,
        "fspl_at_1km_db": fspl_db(1000.0, ch.f_hz),
        "range_free_space_m": r_fs,
        "range_realistic_m": r_real,
        "standard": ch.standard,
    }


def build_report(eirp_dbm: float = 30.0, margin_db: float = 20.0,
                 bw_hz: float = BW_SSB_HZ, nf_db: float = NF_DB,
                 snr_db: float = SNR_DETECT_DB) -> dict:
    """Свести бюджет по всем каналам + проверка требований шума."""
    channels = [channel_budget(ch, eirp_dbm, margin_db, bw_hz, nf_db, snr_db)
                for ch in CHANNELS]
    n_calc = noise_floor_dbm(bw_hz, nf_db)
    return {
        "model": "Friis + шумовая дорожка приёмника (свободное пространство)",
        "reference": {
            "eirp_dbm": eirp_dbm,
            "eirp_w": 10.0 ** (eirp_dbm / 10.0 - 3.0),
            "clutter_margin_db": margin_db,
            "bw_hz": bw_hz,
            "nf_db": nf_db,
            "snr_db": snr_db,
            "t_kelvin": T0_KELVIN,
        },
        "noise": {
            "noise_floor_calc_dbm": n_calc,
            "noise_floor_max_dbm": NOISE_FLOOR_MAX_DBM,
            "mds_required_dbm": MDS_DBM,
            "sensitivity_calc_dbm": n_calc + snr_db,
            "noise_floor_pass": n_calc <= NOISE_FLOOR_MAX_DBM,
            "mds_pass": (n_calc + snr_db) <= MDS_DBM,
        },
        "channels": channels,
    }


# ---------------------------------------------------------------------------
#  Диаграмма (SVG)
# ---------------------------------------------------------------------------
def render_svg(report: dict, path: str | Path) -> Path:
    """Столбчатая диаграмма дальностей + формулы бюджета (SVG)."""
    chans = report["channels"]
    ref = report["reference"]
    W, H = 920, 520
    left, top, plot_w, plot_h = 90, 90, 760, 300
    r_max = max(c["range_free_space_m"] for c in chans) * 1.15
    bar_w = plot_w / (len(chans) * 1.6)

    def y_of(r: float) -> float:
        return top + plot_h - plot_h * (r / r_max)

    parts: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
        f'viewBox="0 0 {W} {H}" font-family="DejaVu Sans, Arial, sans-serif">',
        '<rect width="100%" height="100%" fill="#0f1319"/>',
        f'<text x="{W/2}" y="36" fill="#e6ebf2" font-size="20" '
        f'text-anchor="middle">Дальность приёма (дальность радиовидимости)</text>',
        f'<text x="{W/2}" y="60" fill="#8b95a3" font-size="13" '
        f'text-anchor="middle">Friis + шумовая дорожка: EIRP = '
        f'{ref["eirp_dbm"]:.0f} дБм ({ref["eirp_w"]:.1f} Вт), NF = '
        f'{ref["nf_db"]:.1f} дБ, B = {ref["bw_hz"]/1e3:.1f} кГц, SNR = '
        f'{ref["snr_db"]:.0f} дБ, запас {ref["clutter_margin_db"]:.0f} дБ</text>',
    ]
    # оси
    for i in range(5):
        r = r_max * i / 4
        y = y_of(r)
        parts.append(f'<line x1="{left}" y1="{y:.1f}" x2="{left+plot_w}" '
                     f'y2="{y:.1f}" stroke="#232a35" stroke-width="1"/>')
        parts.append(f'<text x="{left-8}" y="{y+4:.1f}" fill="#6f7987" '
                     f'font-size="11" text-anchor="end">{r/1000:.0f} км</text>')

    for i, c in enumerate(chans):
        cx = left + plot_w * (i + 0.5) / len(chans)
        x = cx - bar_w / 2
        y_fs = y_of(c["range_free_space_m"])
        y_re = y_of(c["range_realistic_m"])
        parts.append(f'<rect x="{x:.1f}" y="{y_fs:.1f}" width="{bar_w:.1f}" '
                     f'height="{top+plot_h-y_fs:.1f}" fill="#2f6fdb" rx="3"/>')
        parts.append(f'<rect x="{x:.1f}" y="{y_re:.1f}" width="{bar_w:.1f}" '
                     f'height="{top+plot_h-y_re:.1f}" fill="#29b6a3" rx="3" '
                     f'opacity="0.9"/>')
        parts.append(f'<text x="{cx:.1f}" y="{top+plot_h+20:.0f}" fill="#c9d2dd" '
                     f'font-size="12" text-anchor="middle">{c["title"]}</text>')
        parts.append(f'<text x="{cx:.1f}" y="{top+plot_h+38:.0f}" fill="#8b95a3" '
                     f'font-size="11" text-anchor="middle">'
                     f'{c["f_mhz"]:.0f} МГц · G={c["g_rx_dbi"]:.1f} дБи</text>')
        parts.append(f'<text x="{cx:.1f}" y="{y_fs-6:.1f}" fill="#7fa8f0" '
                     f'font-size="11" text-anchor="middle">'
                     f'{c["range_free_space_m"]/1000:.0f} км</text>')
    # легенда
    parts += [
        f'<rect x="{left}" y="{H-70}" width="14" height="14" fill="#2f6fdb"/>',
        f'<text x="{left+20}" y="{H-59}" fill="#c9d2dd" font-size="12">'
        f'радиовидимость (свободное пространство)</text>',
        f'<rect x="{left+320}" y="{H-70}" width="14" height="14" fill="#29b6a3"/>',
        f'<text x="{left+340}" y="{H-59}" fill="#c9d2dd" font-size="12">'
        f'с запасом {ref["clutter_margin_db"]:.0f} дБ (помехи/многолучёвость)</text>',
        f'<text x="{W/2}" y="{H-24}" fill="#6f7987" font-size="11" '
        f'text-anchor="middle">R = λ/(4π)·10^((EIRP+G_rx−S_min)/20), '
        f'S_min = kT₀B·NF + SNR</text>',
        "</svg>",
    ]
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(parts), encoding="utf-8")
    return p


def _parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Krystal Radio — дальность приёма")
    p.add_argument("--out", default="out", help="каталог для артефактов")
    p.add_argument("--eirp-dbm", type=float, default=30.0)
    p.add_argument("--margin-db", type=float, default=20.0)
    p.add_argument("--bw-hz", type=float, default=BW_SSB_HZ)
    p.add_argument("--nf-db", type=float, default=NF_DB)
    p.add_argument("--snr-db", type=float, default=SNR_DETECT_DB)
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    report = build_report(args.eirp_dbm, args.margin_db, args.bw_hz,
                          args.nf_db, args.snr_db)
    (out / "link_budget.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    svg = render_svg(report, out / "link_budget.svg")
    print(f"[link-budget] шум {report['noise']['noise_floor_calc_dbm']:.1f} дБм, "
          f"чувствительность {report['noise']['sensitivity_calc_dbm']:.1f} дБм")
    for c in report["channels"]:
        print(f"  {c['title']:34s} R_свободн={c['range_free_space_m']/1000:6.1f} км  "
              f"R_реальн={c['range_realistic_m']/1000:6.1f} км")
    print(f"[link-budget] -> {out/'link_budget.json'}, {svg}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
