#!/usr/bin/env python3
# ===========================================================================
#  Krystal Radio — сборка единой HTML-страницы с 3D-моделью и чертежами.
#
#  Страница самодостаточна (один файл, работает офлайн):
#    * 3D-модель платы board.glb встроена в base64 и показывается
#      интерактивным просмотрщиком three.js (бандл собран rollup'ом из
#      локального node_modules/three, внешних CDN нет);
#    * технические чертежи (SVG из gost_pipeline.py и чертёж платы из
#      kicad-cli) встроены как data:-URI и открываются на вкладках;
#    * сводка по надёжности (Octave) и теплу (ngspice + FEM) читается из
#      out/reliability.json и out/thermal_{fast,fem}.json.
#
#  Запуск:  python3 tools/build_page.py [--out out/krystal-radio.html]
# ===========================================================================
from __future__ import annotations

import argparse
import base64
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out"
CAD_FIG = ROOT / "sim" / "cad" / "figures"


def b64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("ascii")


def read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def fmt_int(n: float) -> str:
    return f"{int(round(n)):,}".replace(",", " ")


def fmt_float(n: float, nd: int = 1) -> str:
    return f"{n:.{nd}f}"


# --------------------------------------------------------------------------
#  Сводные карточки
# --------------------------------------------------------------------------
def summary_cards() -> str:
    cards: list[str] = []

    rel = read_json(OUT / "reliability.json")
    if rel:
        ok = bool(rel.get("pass"))
        cards.append(
            card(
                "MTBF (REQ-26)",
                f"{fmt_int(rel['mtbf_h'])} ч",
                f"цель ≥ {fmt_int(rel['mtbf_target_h'])} ч · λ = "
                f"{rel['lambda_per_hour']:.3e} 1/ч · деталей {rel['total_qty']}",
                ok,
            )
        )

    fem = read_json(OUT / "thermal_fem.json")
    fast = read_json(OUT / "thermal_fast.json")
    if fem and fast:
        ok = bool(fast["fast"].get("pass_"))
        fem_t = fem["fem"].get("t_max_c")
        cards.append(
            card(
                "Тепло (Tj / FEM)",
                f"Tj {fmt_float(fast['fast']['worst_tj_c'])} °C",
                f"FEM Tmax {fmt_float(fem_t)} °C · P = "
                f"{fmt_float(fem['fem'].get('q_total_w', 0.0), 3)} Вт · "
                f"{fem['fem'].get('nodes', '?')} узлов",
                ok,
            )
        )

    # DRC / ERC — считаем ошибки в отчётах kicad-cli / SKiDL.
    drc = OUT / "drc.rpt"
    if drc.exists():
        txt = drc.read_text(errors="replace")
        errs = txt.count("Violation") if "Violation" in txt else sum(
            txt.lower().count(t) for t in ("error",)
        )
        cards.append(card("DRC (kicad-cli)", f"{errs}", "проверка зазоров/колец/сверловки", errs == 0))
    erc = OUT / "erc.rpt"
    if erc.exists():
        txt = erc.read_text(errors="replace")
        e = w = None
        import re

        m = re.search(r"errors:\s*(\d+)", txt)
        if m:
            e = int(m.group(1))
        m = re.search(r"warnings:\s*(\d+)", txt)
        if m:
            w = int(m.group(1))
        if e is not None:
            cards.append(
                card("ERC (SKiDL)", f"{e} ошибок", f"предупреждений: {w if w is not None else '?'}", e == 0)
            )

    lb = read_json(OUT / "link_budget.json")
    if lb:
        ch = {c.get("key"): c for c in lb.get("channels", [])}
        km = lambda k: fmt_int(ch.get(k, {}).get("range_realistic_m", 0) / 1000)  # noqa: E731
        ok = bool(lb.get("noise", {}).get("noise_floor_pass")) and bool(
            lb.get("noise", {}).get("mds_pass"))
        cards.append(card(
            "Дальность приёма",
            f"{km('channel_a')} / {km('channel_b')} / {km('channel_c')} км",
            "каналы A / B / C · Friis + шумовая дорожка · MDS ≤ −110 дБм",
            ok))

    df = read_json(OUT / "direction_finding.json")
    if df:
        cards.append(card(
            "Пеленгация (DoA)",
            f"θ̂ {fmt_float(df.get('theta_est_deg', 0))}° "
            f"± {fmt_float(df.get('sigma_theta_deg', 0))}°",
            f"зона однозначности ±{fmt_float(df.get('fov_deg', 0), 0)}° · "
            "фазовый интерферометр NEC-2 / AntennaSim",
            True))

    if not cards:
        return ""
    return '<section class="cards">' + "".join(cards) + "</section>"


def card(title: str, value: str, sub: str, ok: bool) -> str:
    cls = "ok" if ok else "warn"
    badge = "PASS" if ok else "CHECK"
    return (
        f'<div class="card {cls}">'
        f'<div class="card-head"><span>{title}</span><span class="badge">{badge}</span></div>'
        f'<div class="card-value">{value}</div>'
        f'<div class="card-sub">{sub}</div>'
        f"</div>"
    )


# --------------------------------------------------------------------------
#  Вкладки с чертежами
# --------------------------------------------------------------------------
def drawing_tabs() -> str:
    drawings: list[tuple[str, Path]] = []
    board = OUT / "board.svg"
    if board.exists():
        drawings.append(("Плата (PCB)", board))
    for key, label in (("a", "Антенна A"), ("b", "Антенна B"), ("c", "Антенна C")):
        p = CAD_FIG / f"band_{key}_GOST.svg"
        if p.exists():
            drawings.append((label, p))
    if not drawings:
        return '<p class="muted">Чертежи не найдены — запустите <code>make drawings</code>.</p>'

    buttons = []
    panels = []
    for i, (label, path) in enumerate(drawings):
        active = " active" if i == 0 else ""
        hidden = "" if i == 0 else " hidden"
        uri = "data:image/svg+xml;base64," + b64(path)
        buttons.append(
            f'<button class="tab{active}" data-tab="dwg-{i}" type="button">{label}</button>'
        )
        panels.append(
            f'<div class="dwg-panel{hidden}" id="dwg-{i}">'
            f'<img class="dwg" src="{uri}" alt="Чертёж: {label}" '
            f'title="Клик — открыть в отдельной вкладке">'
            f'<div class="dwg-cap">{label} · <span class="muted">{path.name}</span> · '
            f"клик — открыть в отдельной вкладке</div></div>"
        )
    return (
        '<div class="tabs">'
        + "".join(buttons)
        + "</div>"
        + "".join(panels)
    )


# --------------------------------------------------------------------------
#  Водопад виртуального приёма (GNU Radio co-simulation)
# --------------------------------------------------------------------------
def waterfall_section() -> str:
    """Встроить PNG-водопад (out/waterfall.png) как data:-URI с подписью.

    Картинка рисуется headless-целью `make gnuradio`: GNU Radio прогоняет
    виртуальный эфир, а numpy+Pillow считают сонограмму. Живой водопад
    (`--gui`) открывается отдельно и на страницу не встраивается.
    """
    png = OUT / "waterfall.png"
    if not png.exists():
        return ('<p class="muted">Водопад ещё не посчитан — запустите '
                "<code>make gnuradio</code>.</p>")

    spec = read_json(OUT / "virtual_rx_spectrum.json") or {}
    parts: list[str] = []
    if spec.get("peak_freq_hz") is not None:
        parts.append(f"пик {spec['peak_freq_hz'] / 1e3:+.0f} кГц "
                     f"@ {spec.get('peak_dbfs', 0.0):.1f} дБFS")
    if spec.get("snr_target_db") is not None:
        meas = spec.get("snr_measured_db")
        snr = f"SNR {spec['snr_target_db']:.0f} дБ"
        if meas is not None:
            snr += f" (измерено {meas:.1f})"
        parts.append(snr)
    if spec.get("noise_floor_dbm") is not None:
        parts.append(f"шум {spec['noise_floor_dbm']:.1f} дБм")
    if spec.get("samp_rate"):
        parts.append(f"полоса {spec['samp_rate'] / 1e6:.2f} МГц")
    if spec.get("nsamples"):
        parts.append(f"{fmt_int(spec['nsamples'])} отсч.")
    if spec.get("emitters"):
        parts.append(f"источников {len(spec['emitters'])}")

    caption = " · ".join(parts) if parts else "виртуальный эфир"
    live = ("Живой водопад: <code>python3 "
            "src/gnuradio_krystal/krystal_virtual_rx.py --gui</code>")
    return (
        '<div class="waterfall-wrap">'
        f'<img class="waterfall" alt="Водопад виртуального приёма" '
        f'title="Водопад виртуального приёма" '
        f'src="data:image/png;base64,{b64(png)}">'
        f'<div class="waterfall-cap">{caption}</div>'
        f'<div class="waterfall-cap muted" style="margin-top:4px">{live}</div>'
        "</div>"
    )


# --------------------------------------------------------------------------
#  Дальность приёма (link budget)
# --------------------------------------------------------------------------
def link_budget_section() -> str:
    """Сводка дальности приёма по каналам + диаграмма (SVG data:-URI)."""
    rep = read_json(OUT / "link_budget.json")
    if not rep:
        return ('<p class="muted">Дальность приёма не посчитана — запустите '
                "<code>make link-budget</code>.</p>")
    ref = rep.get("reference", {})
    n = rep.get("noise", {})
    body = "".join(
        f"<tr><td>{c['title']}</td><td>{fmt_float(c['f_mhz'])} МГц</td>"
        f"<td>{fmt_float(c['g_rx_dbi'])} дБи</td>"
        f"<td>{fmt_int(c['range_free_space_m'] / 1000)} км</td>"
        f"<td>{fmt_int(c['range_realistic_m'] / 1000)} км</td></tr>"
        for c in rep.get("channels", [])
    )
    return (
        '<table class="kwt"><thead><tr>'
        "<th>Канал</th><th>f</th><th>G_rx</th>"
        "<th>Радиовидимость</th><th>С запасом 20 дБ</th></tr></thead>"
        f"<tbody>{body}</tbody></table>"
        f'<div class="kwcap">шумовая дорожка '
        f'{"OK" if n.get("noise_floor_pass") else "?"} · MDS '
        f'{"OK" if n.get("mds_pass") else "?"} · '
        f'EIRP {fmt_float(ref.get("eirp_dbm", 0))} дБм · '
        f'B {fmt_float(ref.get("bw_hz", 0) / 1000)} кГц (SSB) · '
        f'NF {fmt_float(ref.get("nf_db", 0))} дБ</div>'
        + (f'<img class="dwg" alt="Диаграмма дальности приёма" '
           f'src="data:image/svg+xml;base64,{b64(OUT / "link_budget.svg")}">'
           if (OUT / "link_budget.svg").exists() else "")
    )


# --------------------------------------------------------------------------
#  Пеленгация (direction finding, NEC-2 / AntennaSim)
# --------------------------------------------------------------------------
def direction_section() -> str:
    """Диаграмма «направление + дальность» (SVG data:-URI)."""
    sc = read_json(OUT / "direction_finding.json")
    if not sc:
        return ('<p class="muted">Пеленгация не посчитана — запустите '
                "<code>make direction</code>.</p>")
    ch = sc.get("channel", {})
    cap = (
        f"пеленг θ̂ = {fmt_float(sc.get('theta_est_deg', 0), 1)}° · "
        f"σ_θ = {fmt_float(sc.get('sigma_theta_deg', 0), 1)}° · "
        f"зона однозначности ±{fmt_float(sc.get('fov_deg', 0), 0)}° · "
        f"λ = {fmt_float(sc.get('lambda_m', 0) * 1000, 1)} мм, "
        f"d = {fmt_float(sc.get('baseline_half_wave_m', 0) * 1000, 1)} мм · "
        f"{ch.get('title', '')}"
    )
    svg = OUT / "direction_finding.svg"
    img = ('<img class="dwg" alt="Диаграмма пеленгации" '
           f'src="data:image/svg+xml;base64,{b64(svg)}">') if svg.exists() else ""
    return (
        f'<div class="dwg-cap" style="color:var(--muted);text-align:left">{cap}</div>'
        + img
        + '<div class="dwg-cap muted">NEC-2 (nec2c) / '
          'AntennaSim (EA1FUO) — <code>sim/nec/df_array.nec</code></div>'
    )


# --------------------------------------------------------------------------
#  HTML-шаблон
# --------------------------------------------------------------------------
def build_html(viewer_js: str, glb_b64: str, glb_name: str, kind: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Krystal Radio — zero-IF SDR — 3D и чертежи</title>
<style>
  :root {{
    --bg: #0d1117; --panel: #161b22; --panel2: #1c232d;
    --fg: #e6edf3; --muted: #8b949e; --line: #30363d;
    --accent: #58a6ff; --ok: #3fb950; --warn: #d29922;
  }}
  * {{ box-sizing: border-box; }}
  html, body {{ margin: 0; background: var(--bg); color: var(--fg);
    font: 15px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "DejaVu Sans", sans-serif; }}
  header {{ padding: 20px 26px 12px; border-bottom: 1px solid var(--line); }}
  h1 {{ margin: 0 0 4px; font-size: 20px; font-weight: 600; }}
  h1 .dot {{ color: var(--accent); }}
  .sub {{ color: var(--muted); font-size: 13px; }}
  main {{ padding: 18px 22px 48px; max-width: 1400px; margin: 0 auto; }}
  .cards {{ display: grid; gap: 12px; margin: 0 0 18px;
           grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); }}
  .card {{ background: var(--panel); border: 1px solid var(--line);
    border-left: 3px solid var(--muted); border-radius: 10px; padding: 12px 14px; }}
  .card.ok {{ border-left-color: var(--ok); }}
  .card.warn {{ border-left-color: var(--warn); }}
  .card-head {{ display: flex; justify-content: space-between; align-items: center;
    font-size: 12px; text-transform: uppercase; letter-spacing: .04em; color: var(--muted); }}
  .badge {{ font-size: 10px; padding: 1px 6px; border-radius: 999px;
    background: var(--panel2); color: var(--ok); border: 1px solid var(--line); }}
  .card.warn .badge {{ color: var(--warn); }}
  .card-value {{ font-size: 22px; font-weight: 600; margin: 6px 0 2px; }}
  .card-sub {{ font-size: 12px; color: var(--muted); }}
  h2 {{ font-size: 15px; margin: 22px 0 10px; color: var(--fg);
    display: flex; align-items: center; gap: 8px; }}
  h2::before {{ content: ""; width: 8px; height: 8px; background: var(--accent);
    border-radius: 2px; display: inline-block; }}
  .viewer-wrap {{ position: relative; border: 1px solid var(--line); border-radius: 12px;
    overflow: hidden; background: var(--bg); height: min(62vh, 640px); }}
  #viewer {{ position: absolute; inset: 0; }}
  .viewer-fallback {{ padding: 24px; color: var(--muted); }}
  .overlay {{ position: absolute; right: 12px; top: 12px; display: flex; gap: 6px; flex-wrap: wrap;
    max-width: 60%; justify-content: flex-end; }}
  .overlay button {{ background: rgba(22,27,34,.85); color: var(--fg); border: 1px solid var(--line);
    border-radius: 8px; padding: 6px 10px; font-size: 12px; cursor: pointer; }}
  .overlay button:hover {{ border-color: var(--accent); color: var(--accent); }}
  .hint {{ position: absolute; left: 12px; bottom: 10px; font-size: 11px; color: var(--muted);
    background: rgba(13,17,23,.7); padding: 3px 8px; border-radius: 6px; }}
  .tabs {{ display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 10px; }}
  .tab {{ background: var(--panel); color: var(--muted); border: 1px solid var(--line);
    border-radius: 8px; padding: 6px 12px; font-size: 13px; cursor: pointer; }}
  .tab.active {{ color: var(--fg); border-color: var(--accent); background: var(--panel2); }}
  .dwg-panel {{ border: 1px solid var(--line); border-radius: 12px; background: #fff;
    padding: 10px; overflow: auto; max-height: 78vh; }}
  .dwg {{ display: block; width: 100%; height: auto; background: #fff; }}
  .dwg-cap {{ margin-top: 8px; font-size: 12px; color: #333; text-align: center; }}
  .dwg-cap .muted {{ color: #777; }}
  table.kwt {{ width: 100%; border-collapse: collapse; margin: 0 0 10px; font-size: 13px; }}
  table.kwt th, table.kwt td {{ border-bottom: 1px solid var(--line); padding: 6px 8px; text-align: left; }}
  table.kwt th {{ color: var(--muted); font-weight: 500; }}
  .kwcap {{ color: var(--muted); font-size: 12px; margin: 0 0 12px; }}
  .waterfall-wrap {{ border: 1px solid var(--line); border-radius: 12px;
    overflow: auto; background: #05070a; padding: 10px; }}
  .waterfall {{ display: block; width: 100%; height: auto; border-radius: 6px; }}
  .waterfall-cap {{ margin-top: 8px; font-size: 12px; color: var(--muted); text-align: center; }}
  .muted {{ color: var(--muted); }}
  code {{ background: var(--panel2); padding: 1px 5px; border-radius: 5px; font-size: 12px; }}
  footer {{ margin-top: 26px; font-size: 12px; color: var(--muted); }}
</style>
</head>
<body>
<header>
  <h1>Krystal Radio <span class="muted">·</span> zero-IF SDR <span class="muted">direct conversion</span></h1>
  <div class="muted">Сборка: {stamp} · артефакты <code>out/</code> · самодостаточная страница (офлайн)</div>
</header>
<main>
  {summary_cards()}

  <h2>3D-модель платы</h2>
  <div class="viewer-wrap">
    <div id="viewer"></div>
    <div class="overlay">
      <button type="button" data-act="reset">Сброс вида</button>
      <button type="button" data-act="rot">Автоповорот</button>
      <button type="button" data-act="wire">Каркас</button>
    </div>
    <div class="hint">ЛКМ — вращение · колесо — зум · ПКМ — панорама</div>
  </div>

  <h2>Водопад виртуального приёма (GNU Radio)</h2>
  {waterfall_section()}

  <h2>Дальность приёма (link budget)</h2>
  {link_budget_section()}

  <h2>Пеленгация: направление и дальность (NEC-2 / AntennaSim)</h2>
  {direction_section()}

  <h2>Технические чертежи</h2>
  {drawing_tabs()}

  <footer>Модель: <code>{glb_name}</code> ({kind}) · просмотрщик: three.js r179 (локальный бандл)</footer>
</main>

<script id="glb-data" type="application/octet-stream">{glb_b64}</script>
<script>{viewer_js}</script>
<script>
(function () {{
  var el = document.getElementById('viewer');
  var raw = document.getElementById('glb-data').textContent.trim();
  var url = 'data:model/gltf-binary;base64,' + raw;
  var viewer = null;
  try {{
    viewer = window.KrystalViewer.mount(el, url);
  }} catch (e) {{
    el.innerHTML = '<div class="viewer-fallback">Ошибка инициализации 3D: ' + e.message + '</div>';
  }}
  // Сигнал «модель разобрана и добавлена в сцену» — используется
  // автотестом страницы (headless-проверка), без него отличить
  // «renderer создан» от «GLB распарсен» нельзя.
  el.addEventListener('model-loaded', function () {{ el.setAttribute('data-model-loaded', '1'); }});
  document.querySelectorAll('.overlay button').forEach(function (b) {{
    b.addEventListener('click', function () {{
      if (!viewer) return;
      var a = b.getAttribute('data-act');
      if (a === 'reset') viewer.reset();
      else if (a === 'rot') {{ b.classList.toggle('on'); viewer.setAutoRotate(b.classList.contains('on')); }}
      else if (a === 'wire') {{ b.classList.toggle('on'); viewer.setWireframe(b.classList.contains('on')); }}
    }});
  }});
  document.querySelectorAll('.tab').forEach(function (t) {{
    t.addEventListener('click', function () {{
      document.querySelectorAll('.tab').forEach(function (x) {{ x.classList.remove('active'); }});
      document.querySelectorAll('.dwg-panel').forEach(function (p) {{ p.hidden = true; }});
      t.classList.add('active');
      var panel = document.getElementById(t.getAttribute('data-tab'));
      if (panel) panel.hidden = false;
    }});
  }});
  document.querySelectorAll('img.dwg').forEach(function (img) {{
    img.addEventListener('click', function () {{
      var w = window.open('', '_blank');
      if (!w) return;
      w.document.write('<title>' + (img.alt || 'drawing') + '</title>' +
        '<body style="margin:0;background:#fff">' +
        '<img src="' + img.src + '" style="width:100%">');
      w.document.close();
    }});
  }});
}})();
</script>
</body>
</html>
"""


def main() -> int:
    ap = argparse.ArgumentParser(description="Собрать единую HTML-страницу с 3D и чертежами")
    ap.add_argument("--out", default=str(OUT / "krystal-radio.html"))
    ap.add_argument("--glb", default=str(OUT / "board.glb"))
    ap.add_argument("--viewer", default=str(ROOT / "hardware" / "tscircuit" / "viewer" / "dist" / "krystal-viewer.js"))
    args = ap.parse_args()

    glb = Path(args.glb)
    viewer = Path(args.viewer)
    if not glb.exists():
        sys.exit(f"build_page: нет 3D-модели {glb} — запустите make pcb")
    if not viewer.exists():
        sys.exit(
            f"build_page: нет бандла просмотрщика {viewer} — запустите "
            "`make viewer` (rollup из hardware/tscircuit/viewer/main.js)"
        )

    html = build_html(
        viewer_js=viewer.read_text(encoding="utf-8"),
        glb_b64=b64(Path(glb)),
        glb_name=glb.name,
        kind=f"{glb.stat().st_size / 1024:.0f} КиБ",
    )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"page: {out} ({out.stat().st_size / 1024:.0f} КиБ)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
