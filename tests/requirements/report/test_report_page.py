"""
Тесты единой HTML-страницы Krystal Radio (3D-модель + технические чертежи).

Страница `out/krystal-radio.html` собирается целью `make page` и должна быть
самодостаточной: 3D-модель платы (board.glb, base64), чертежи
(SVG из gost_pipeline.py + kicad-cli, data:-URI) и водопад виртуального
приёма (GNU Radio co-simulation, PNG) в одном файле, без CDN.

Запуск:
    pytest tests/requirements/report/ -v
"""

import base64
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
PAGE = ROOT / "out" / "krystal-radio.html"
RENDER_CHECK = ROOT / "tools" / "page_render_check.mjs"

CHROME = (shutil.which("google-chrome") or shutil.which("google-chrome-stable")
          or shutil.which("chromium") or shutil.which("chromium-browser"))
NODE = shutil.which("node")


@pytest.fixture(scope="module")
def html() -> str:
    if not PAGE.exists():
        pytest.skip("out/krystal-radio.html not built — run `make page`")
    return PAGE.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# самодостаточность и состав
# ---------------------------------------------------------------------------

def test_page_exists():
    """Страница — артефакт сборки, цель `make page`"""
    assert PAGE.exists(), "нет out/krystal-radio.html — запустите `make page`"


def test_page_is_single_file(html):
    """Внешних ресурсов нет: бандл, модель и чертежи встроены (офлайн)"""
    assert not re.search(r'<script[^>]+src\s*=\s*["\']https?://', html), "внешний <script src>"
    assert not re.search(r'<link[^>]+href\s*=\s*["\']https?://', html), "внешний <link>"
    assert not re.search(r'<img[^>]+src\s*=\s*["\']https?://', html), "внешняя картинка"
    assert "@import" not in html, "внешний @import"


def test_page_has_3d_viewer_mount(html):
    """На странице есть модуль просмотрщика и вызов монтирования"""
    assert "KrystalViewer" in html
    assert "KrystalViewer.mount" in html
    assert 'id="viewer"' in html


# ---------------------------------------------------------------------------
# 3D-модель
# ---------------------------------------------------------------------------

def test_page_embeds_glb_model(html):
    """board.glb встроен как base64 и действительно является glTF"""
    m = re.search(r'id="glb-data"[^>]*>(.*?)</script>', html, re.S)
    assert m, "нет контейнера с GLB"
    raw = base64.b64decode(m.group(1).strip())
    assert raw[:4] == b"glTF", "встроенные данные не являются GLB"
    assert len(raw) > 10_000, "модель подозрительно мала"
    # версия контейнера glTF v2
    assert int.from_bytes(raw[4:8], "little") == 2
    assert int.from_bytes(raw[8:12], "little") == len(raw), "длина GLB в заголовке не совпадает"


def test_glb_matches_published_model(html):
    """Встроенная модель совпадает с out/board.glb"""
    glb = ROOT / "out" / "board.glb"
    if not glb.exists():
        pytest.skip("out/board.glb отсутствует")
    m = re.search(r'id="glb-data"[^>]*>(.*?)</script>', html, re.S)
    assert base64.b64decode(m.group(1).strip()) == glb.read_bytes()


# ---------------------------------------------------------------------------
# технические чертежи
# ---------------------------------------------------------------------------

def test_page_has_technical_drawings(html):
    """Технические чертежи встроены как SVG data:-URI"""
    uris = re.findall(r'data:image/svg\+xml;base64,([A-Za-z0-9+/=]+)', html)
    assert len(uris) >= 2, f"чертежей на странице: {len(uris)}"
    for u in uris:
        svg = base64.b64decode(u)
        assert b"<svg" in svg, "data-URI не содержит SVG"


def test_page_has_tabs_for_drawings(html):
    """Каждому техническому чертежу соответствует вкладка и панель"""
    panels = re.findall(r'id="dwg-\d+"[^>]*>(.*?)</div></div>', html, re.S)
    n_tabs = len(re.findall(r'class="tab(?:\s|")', html))
    n_buttons = len(re.findall(r'data-tab="dwg-', html))
    n_panels = len(re.findall(r'id="dwg-', html))
    n_dwg_uris = sum(p.count("data:image/svg+xml;base64") for p in panels)
    assert n_tabs == n_buttons == n_panels == n_dwg_uris, (
        f"вкладки {n_tabs}, кнопки {n_buttons}, панели {n_panels}, чертежи {n_dwg_uris}")


def test_expected_drawings_present(html):
    """Чертёж платы и ГОСТ-чертежи антенн присутствуют на странице"""
    joined = " ".join(html.split())  # нормализуем пробелы/переводы строк
    assert "Плата" in joined, "нет вкладки с чертежом платы"
    assert "Антенна A" in joined, "нет ГОСТ-чертежа антенны A"


# ---------------------------------------------------------------------------
# дальность приёма и пеленгация
# ---------------------------------------------------------------------------

def test_page_has_reception_distance(html):
    """Раздел «Дальность приёма» отражает out/link_budget.json"""
    joined = " ".join(html.split())
    assert "Дальность приёма" in joined, "нет раздела дальности приёма"
    lb = ROOT / "out" / "link_budget.json"
    if lb.exists():
        data = json.loads(lb.read_text(encoding="utf-8"))
        ch = {c["key"]: c for c in data["channels"]}
        km_a = int(round(ch["channel_a"]["range_realistic_m"] / 1000))
        assert f"{km_a:,}".replace(",", " ") in joined, "дальность канала A не на странице"


def test_page_has_direction_finding(html):
    """Раздел «Пеленгация» ссылается на NEC-2 / AntennaSim"""
    joined = " ".join(html.split())
    assert "Пеленгация" in joined, "нет раздела пеленгации"
    assert "AntennaSim" in joined, "нет ссылки на AntennaSim"


# ---------------------------------------------------------------------------
# водопад (GNU Radio co-simulation)
# ---------------------------------------------------------------------------

def test_page_embeds_waterfall(html):
    """Водопад виртуального приёма встроен как PNG data:-URI"""
    m = re.search(r'data:image/png;base64,([A-Za-z0-9+/=]+)', html)
    assert m, "нет водопада на странице (запустите `make gnuradio`)"
    png = base64.b64decode(m.group(1))
    assert png[:8] == b"\x89PNG\r\n\x1a\n", "data-URI не является PNG"
    assert len(png) > 5_000, "водопад подозрительно мал"


def test_waterfall_disclaimer_is_self_contained(html):
    """Водопад — картинка на самой странице, а не ссылка на файл"""
    assert "waterfall" in html.lower()
    assert not re.search(r'src\s*=\s*["\'][^"\']*waterfall\.png', html), (
        "страница ссылается на waterfall.png, а не встраивает его")


# ---------------------------------------------------------------------------
# headless-рендер (детерминированно, через DevTools Protocol)
#
# Chrome с `--virtual-time-budget --dump-dom` завершается по виртуальному
# времени и не дожидается асинхронного разбора GLB (декодирование текстур
# идёт в отдельном потоке), поэтому раньше результат был нестабилен
# (~40 % успешных прогонов). tools/page_render_check.mjs делает то же самое
# через CDP: ждёт реального события `model-loaded` и печатает JSON
# {state, canvas, error?}.
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def rendered() -> dict:
    if not PAGE.exists():
        pytest.skip("out/krystal-radio.html not built")
    if CHROME is None:
        pytest.skip("Chrome/Chromium не найден — рендер не проверяется")
    if NODE is None:
        pytest.skip("node не найден — рендер не проверяется")
    r = subprocess.run(
        [NODE, str(RENDER_CHECK), PAGE.as_uri()],
        capture_output=True, text=True, timeout=180,
    )
    if r.returncode == 2:
        pytest.skip(f"рендер недоступен: {r.stderr.strip()}")
    lines = [ln for ln in r.stdout.strip().splitlines() if ln.startswith("{")]
    assert lines, f"нет JSON от page_render_check: {r.stdout!r} {r.stderr!r}"
    return json.loads(lines[-1])


def test_page_renders_3d_canvas(rendered):
    """Рендер: WebGL-контекст создан, three.js смонтировал <canvas>"""
    assert rendered.get("canvas") is True, f"нет <canvas>: {rendered}"


def test_page_renders_model_loaded(rendered):
    """Рендер: GLB распарсен и добавлен в сцену (событие model-loaded)"""
    assert rendered.get("state") != "error", f"ошибка просмотрщика: {rendered}"
    assert rendered.get("state") == "loaded", f"модель не загрузилась: {rendered}"
