"""
Krystal Radio — Операционная документация (Operational Documentation)

Автоматическая генерация руководства по эксплуатации, ремонту и техобслуживанию
SDR-приемника Krystal Radio (zero-IF, direct-conversion).

Форматы вывода:
  * HTML-страница (self-contained, data:URI, встроенные схемы)
  * PDF-документ (для печати и архивации)
  * Markdown-отчет (для интеграции в системы документации)

Содержимое документации:
  1.  Введение и описание приемника
  2.  Технические характеристики (частотный диапазон, NF, MDS, динамический диапазон)
  3.  Инструкция по установке (питание, антенна, соединения)
  3.  Интерфейсы управления (GNU Radio CLI, Makefile targets, Python API)
  4.  Режим работы:
      • Виртуальный приём (numpy, без GNU Radio)
      • GNU Radio co-simulation (водопад, спектр)
      • YOLO-распознавание сигналов (если установлен TensorFlow)
  5.  Рекомендации по EMC (экранирование, заземление, погрешности из REQ-21..25)
  6.  Рекомендации по температурной эксплуатации (Tj ≤ 85°C, см. out/thermal_*.json)
  7.  Рекомендации по диагностике неисправностей (нет сигнала, дисторшн, overheating)
  8.  Справочник по командам make
  9.  Список справочных документов (requirements/, references/)

Генерация:
  python3 scripts/gen_op_docs.py --out out/krystal-radio_operational.html
  python3 scripts/gen_op_docs.py --format pdf --out out/krystal-radio_operational.pdf
  python3 scripts/gen_op_docs.py --format markdown --out - > docs/operational.md
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

# Collect data from out/ and other artifacts
OUT = Path("out")

def _safe(d: dict, *keys, default=None):
    """Nested dict get without raising KeyError."""
    d = d
    for k in keys:
        if isinstance(d, dict) and k in d:
            d = d[k]
        else:
            return default
    return d


def collect() -> dict:
    """Собрать все данные для шаблона отчета."""
    data: dict = {
        "project": "Krystal Radio — zero-IF SDR direct-conversion receiver",
        "version": "2.0",
        "generated": __import__("datetime").datetime.now().isoformat(" "),

        # Basic specs (hard-coded from the project's REQs)
        "specifications": {
            "nf_db": 3.5,                     # REQ-04
            "mds_dbm": -126.16,               # noise floor + MDS
            "noise_floor_dbm": -136.16,       # kTB + NF
            "eirp_dbm": 30,                   # reference
            "samp_rate_hz": 250_000,
            "adc_bits": 12,
        },

        # Thermal compliance
        "thermal": _safe(json.loads, OUT / "thermal_fast.json", default={}),
        "mtbf": _safe(json.loads, OUT / "reliability.json", default={}),

        # Link budget per channel
        "link_budget": _safe(json.loads, OUT / "link_budget.json", default={}),

        # Direction finding
        "direction": _safe(json.loads, OUT / "direction_finding.json", default={}),

        # YOLO dataset info (optional)
        "yolo": {
            "dataset_root": str(Path("ml_data").resolve()),
            "classes": ["cw", "am", "fm", "noise"],
        },

        # Makefile targets summary
        "make_targets": [
            "make all                — всё (netlist→PCB→docs→тесты)",
            "make page               — одна страница (3D+рисунки+водопад)",
            "make gnuradio           — водопад + спектр",
            "make link-budget        — энергетика тракта + дальность",
            "make direction          — пеленгация (NEC-2/AntennaSim)",
            "make reliability        — MTBF Octave (REQ-26)",
            "make thermal            — fast + FEM thermal check",
            "make emc                — 16 тестов REQ-21..25",
            "make test               — full test suite",
        ],
    }
    return data


def render_html(data: dict, out_path: Path) -> None:
    """Render a self-contained HTML operational manual."""
    from jinja2 import Environment, FileSystemLoader, select_autoescape

    env = Environment(
        loader=FileSystemLoader(searchpath=None),
        autoescape=select_autoescape(["html", "xml"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    template = env.from_string("""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{{ project }} — Операционная документация</title>
  <style>
    body {font-family: Arial, Helvetica, sans-serif; line-height: 1.6; margin: 40px; color: #222;}
    h1, h2, h3 {color: #1a73e8;}
    pre {background: #f5f5f5; padding: 10px; overflow: auto;}
    .req {color: #d93025; font-weight: bold;}
    table {border-collapse: collapse; width: 100%; margin: 20px 0;}
    th, td {border: 1px solid #ddd; padding: 8px; text-align left;}
    th {background: #eef;}
  </style>
</head>
<body>
  <h1>{{ project }} <small>версия {{ version }}</small></h1>

  <section>
    <h2>1. Введение</h1>
    <p>Krystal Radio — компактный zero-IF SDR-direct-conversion receiver для HF-диапазона (0–7 ГГц).
    Построен вокруг одной платы (PCB), экспортируемой tscircuit. Все derive-артефакты находятся в папке <code>out/</code>.</p>
  </section>

  <section>
    <h2>2. Технические характеристики</h2>
    <table>
      <tr><th>Характеристика</th><th>Значение</th><th>Вымога</th></tr>
      <tr><td>Шумная дорожка (NF)</td><td>{{ specifications.nf_db }} дБ</td><td>REQ-04</td></tr>
      <tr><td>МДС (minimum detectable signal)</td><td>{{ specifications.mds_dbm }} дБм</td><td>REQ-36/37</td></tr>
      <tr><td>Тепловой шум kTB</td><td>{{ specifications.noise_floor_dbm }} дБм</td><td>теоретический минимум</td></tr>
      <tr><td>EIRP ссылка</td><td>{{ specifications.eirp_dbm }} дБм</td><td>тал baseline</td></tr>
      <tr><td>Частотный диапазон</td><td>0–7 ГГц (3 канала)</td><td>антенна система</td></tr>
      <tr><td>Разрядность АЦП</td><td>{{ specifications.adc_bits }} бит</td><td>типичное значение SDR</td></tr>
    </table>
  </section>

  <section>
    <h2>3. Установка и подключение</h1>
    <ol>
      <li>Подать питание через <code>make gnuradio</code> или <code>.venv/bin/python src/gnuradio_krystal/krystal_virtual_rx.py</code>.</li>
      <li>Подключить антенну через SMA (канал А: змейка 0–0.9 ГГц, Б: патч 1.2–3 ГГц, В: матрица 4×4 4–7 ГГц).</li>
      <li>Запустить водопад: <code>make gnuradio</code> (откроется окно GNU Radio с sink<code>waterfall_sink_c</code>).</li>
      <li>Виртуальный приём без GNU Radio: <code>python3 src/gnuradio_krystal/krystal_virtual_rx.py --out out --nsamples 262144</code>.</li>
    </ol>
  </section>

  <section>
    <h2>4. Режимы работы</h2>
    <h3>4.1 Виртуальный приём (без GNU Radio)</h3>
    <p>Используется модуль <code>src/gnuradio_krystal/virtual_iq.py</code>. Генерация I/Q потока,
    добавление теплового шума, квантование АЦП (12 бит). Результат: комплексный массив <code>np.complex64</code>.</p>

    <h3>4.2 GNU Radio co-simulation</h3>
    <p>Запуск <code>make gnuradio</code> открывает живой водопад (<code>qtgui.waterfall_sink_c</code>).
    Также доступен безGUI-режим: <code>python3 src/gnuradio_krystal/krystal_virtual_rx.py --out out --nsamples 262144</code>,
    который сохраняет <code>out/waterfall.png</code> и <code>out/virtual_rx_spectrum.json</code>.</p>

    <h3>4.3 YOLO-распознавание сигналов (опционально)</h3>
    <p>Если установлен TensorFlow 2.x, можно обучить детектор на синтетическом датасете (см. <code>ml/</code>):
    <code>python3 -c "from ml.train import train; train(epochs=6)"</code>.</p>
  </section>

  <section>
    <h2>5. EMC — электромагнитная совместимость</h2>
    <p>Приемник соответствует требованиям REQ-21..REQ-25:</p>
    <ul>
      <li><span class="req">REQ-21</span>: Экранирование корпуса (Al, δ ≈ 2.6 мкм@1 ГГц), SE ≥ 40 дБ.</li>
      <li><span class="req">REQ-22</span>: Иммунитет к внешним ЭМ-полям (IEC 61000-4-3, уровень 2, 3 В/м, 80–2700 МГц).</li>
      <li><span class="req">REQ-23</span>: Уровень радиопомех (CISPR 22 Class B); экранированный Wi‑Fi ≤ −30 дБм/м.</li>
      <li><span class="req">REQ-24</span>: Фильтры питания (fc = 1/(2π√(LC)), ferrite beads R ≈ 100 Ом@100 МГц).</li>
      <li><span class="req">REQ-25</span>: Зонирование экранирования (3 зоны: ВЧ/оптика/цифра; галиваническая изоляция).</li>
    </ul>
    <p>Обращайтесь к <code>tests/requirements/emc/</code> для автоматизированных проверок.</p>
  </section>

  <section>
    <h2>6. Терморежим эксплуатации</h2>
    <p>Максимальная температура Junction (Tj) рассчитывается по формуле:
    <code>Tj = Ta + P · θJA</code>, где <code>Ta</code> — температура окружающей среды,
    <code>P</code> — мощность dissiпации, <code>θJA</code> — теплоемкость перехода junction‑air.</p>
    <p>Результаты см. в файлах:</p>
    <ul>
      <li><code>out/thermal_fast.json</code> — быстрое оценка (worst Tj ≈ 31.6 °C при Ta=25°C).</li>
      <li><code>out/thermal_fem.json</code> — глубокая FEM-модель (Tmax ≈ 27.2 °C).</li>
    </ul>
    <p>Условие эксплуатации: <code>Tj ≤ 85 °C</code> (консервативно).</p>
  </section>

  <section>
    <h2>7. Диагностика неисправностей</h2>
    <table>
      <tr><td>Симптом</td><td>Возможная причина</td><td>Действие</td></tr>
      <tr><td>Нет сигнала на водопаде</td><td>Неправильная частота, отсутствие антенны</td><td>Проверьте freq, подключите антенну</td></tr>
      <tr><td>Сильная дисторшн</td><td>Перегрузка входного тракта</td><td>Уменьшите сигнал antennas, добавьте шумоподавление</td></tr>
      <tr><td>Tj > 85 °C</td><td>Недостаточное охлаждение, высокое Ta</td><td>Улучшитевентиляцию, снизьте нагрузку</td></tr>
    </table>
  </section>

  <section>
    <h2>8. Команды make</h2>
    <pre><code>{{ make_targets | join("\n") }}</code></pre>
  </section>

  <footer>
    <p>Сгенерировано автоматически: {{ generated }} | Krystal Radio v{{ version }}</p>
  </footer>
</body>
</html>""")
    rendered = template.render(data)
    out_path.write_text(rendered, encoding="utf-8")
    print(f"HTML-отчет сохранен: {out_path}")


def main(argv: list[str] | None = None) -> int:
    data = collect()
    out_arg = None
    fmt = "html"
    i = 0
    while i < len(argv or []):
        if argv[i] == "--out" and i + 1 < len(argv):
            out_arg = argv[i + 1]
            i += 2
        elif argv[i] == "--format" and i + 1 < len(argv):
            fmt = argv[i + 1].lower()
            i += 2
        else:
            i += 1

    if fmt == "html":
        out_path = Path(out_arg) if out_arg else Path("out/krystal-radio_operational.html")
        render_html(data, out_path)
        print(f"HTML-отчет сохранен: {out_path}")
    elif fmt == "markdown":
        #简单的 markdown rendering — вставляем заголовки и текст
        html_path = Path("out/tmp_md_bridge.html")
        render_html(data, html_path)
        import subprocess
        result = subprocess.run(["pandoc", str(html_path), "-f", "html", "-t", "markdown", "-o", out_arg or "operational.md"],
                                capture_output=True, text=True)
        if result.returncode != 0:
            print("pandoc error:", result.stderr, file=sys.stderr)
        else:
            print(f"Markdown-отчет сохранен: {out_arg or 'operational.md'}")
        Path(html_path).unlink(missing_ok=True)
    elif fmt == "pdf":
        html_path = Path("out/tmp_pdf_bridge.html")
        render_html(data, html_path)
        import subprocess
        result = subprocess.run(["soffice", "--headless", "--convert-to", "pdf", str(html_path), "--outdir", str(Path(out_arg).parent or ".")],
                                capture_output=True, text=True)
        if result.returncode != 0:
            print("soffice error:", result.stderr, file=sys.stderr)
        else:
            pdf_out = Path(out_arg) if out_arg else Path("out/krystal-radio_operational.pdf")
            # soffice puts the file in the output dir; locate it
            for p in Path(".").glob("*.pdf"):
                if "krystal-radio" in p.name:
                    p.rename(pdf_out)
                    break
            print(f"PDF-отчет сохранен: {pdf_out}")
        Path(html_path).unlink(missing_ok=True)
    else:
        print(f"Unknown format: {fmt}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))