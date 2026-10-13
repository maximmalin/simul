"""
Waterfall (сонограмма) виртуального приёма: numpy + Pillow, без GUI.

GNU Radio умеет рисовать waterfall на экране (qtgui.waterfall_sink_c), но
это требует дисплея. Для headless-сборки и для встраивания картинки в
единую HTML-страницу водопад считается здесь: STFT -> дБ -> RGB -> PNG.

Ориентация как у обычного SDR-водопада: частота по горизонтали
(слева — нижняя граница полосы), время по вертикали (свежие отсчёты внизу).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

# Палитра водопада: чёрный пол — шум, дальше синий→зелёный→жёлтый→красный.
# Опорные точки интерполируются в LUT из 256 цветов.
_PALETTE_ANCHORS = [
    (0.00, (0, 0, 0)),
    (0.12, (0, 0, 90)),
    (0.30, (0, 90, 200)),
    (0.50, (0, 190, 190)),
    (0.68, (60, 220, 70)),
    (0.84, (255, 220, 0)),
    (1.00, (255, 40, 20)),
]


def _lut(n: int = 256) -> np.ndarray:
    """Таблица цветов палитры, n×3 uint8."""
    xs = np.array([a[0] for a in _PALETTE_ANCHORS])
    cols = np.array([a[1] for a in _PALETTE_ANCHORS], dtype=np.float64)
    t = np.linspace(0.0, 1.0, n)
    lut = np.empty((n, 3), dtype=np.uint8)
    for c in range(3):
        lut[:, c] = np.clip(np.interp(t, xs, cols[:, c]), 0, 255).astype(np.uint8)
    return lut


_LUT = _lut()


def spectrogram(x: np.ndarray, samp_rate: float, nfft: int = 1024,
                overlap: float = 0.5, window: str = "hann"):
    """STFT комплексного сигнала.

    Возвращает (freqs_hz, power_dbfs): freqs длиной nfft (весь диапазон
    [-fs/2, +fs/2)), power — матрица (nfft, n_frames) в дБ относительно
    полной шкалы (тон амплитуды 1.0 -> 0 дБ).
    """
    x = np.asarray(x, dtype=np.complex128).ravel()
    n = x.size
    if n < nfft:
        x = np.concatenate([x, np.zeros(nfft - n, dtype=np.complex128)])
        n = nfft
    hop = max(1, int(round(nfft * (1.0 - overlap))))
    if window == "hann":
        w = np.hanning(nfft)
    elif window in ("none", None):
        w = np.ones(nfft)
    else:
        raise ValueError(f"unknown window: {window}")

    n_frames = 1 + (n - nfft) // hop
    idx = np.arange(nfft)[None, :] + hop * np.arange(n_frames)[:, None]
    frames = x[idx] * w[None, :]
    X = np.fft.fftshift(np.fft.fft(frames, axis=1), axes=1)
    amp = np.abs(X) / np.sum(w)
    power = 20.0 * np.log10(np.maximum(amp, 1e-12))
    freqs = np.fft.fftshift(np.fft.fftfreq(nfft, d=1.0 / samp_rate))
    # (n_frames, nfft) -> (nfft, n_frames): строки — частота, столбцы — время.
    return freqs, power.T


def to_rgb(power: np.ndarray, dynamic_range_db: float = 70.0) -> np.ndarray:
    """Матрица дБ -> RGB uint8 (H, W, 3) по палитре водопада."""
    vmax = float(np.nanmax(power))
    vmin = vmax - float(dynamic_range_db)
    t = np.clip((power - vmin) / max(vmax - vmin, 1e-9), 0.0, 1.0)
    return _LUT[(t * (len(_LUT) - 1)).astype(np.uint8)]


def render_waterfall(x: np.ndarray, samp_rate: float, *, nfft: int = 1024,
                     overlap: float = 0.5, dynamic_range_db: float = 70.0,
                     height: int | None = None, title: str = "",
                     label: bool = True) -> Image.Image:
    """Собрать изображение водопада (PIL.Image, RGB)."""
    freqs, power = spectrogram(x, samp_rate, nfft=nfft, overlap=overlap)
    # power: (nfft, n_frames) — строки частота, столбцы время.
    rgb = to_rgb(power, dynamic_range_db)
    # Низкие частоты — вниз: переворачиваем по частоте, затем поворачиваем
    # так, чтобы время шло сверху вниз, а частота — слева направо.
    img = Image.fromarray(np.flipud(rgb), mode="RGB").rotate(-90, expand=True)

    if height and img.height != height:
        ratio = height / img.height
        img = img.resize((max(1, int(img.width * ratio)), height), Image.BILINEAR)

    if label:
        img = _annotate(img, x, samp_rate, freqs, power)
    if title:
        img = _title_bar(img, title)
    return img


def _annotate(img: Image.Image, x, samp_rate, freqs, power) -> Image.Image:
    """Рамка, подписи частоты и грубая шкала уровня."""
    pad_l, pad_b, pad_t = 42, 26, 8
    canvas = Image.new("RGB", (img.width + pad_l + 8, img.height + pad_b + pad_t),
                       (16, 18, 22))
    canvas.paste(img, (pad_l, pad_t))
    d = ImageDraw.Draw(canvas)
    grey = (120, 126, 134)
    txt = (200, 205, 212)

    n_marks = 5
    for i in range(n_marks):
        f = freqs[0] + (freqs[-1] - freqs[0]) * i / (n_marks - 1)
        px = pad_l + int((img.width - 1) * i / (n_marks - 1))
        d.line([(px, pad_t + img.height), (px, pad_t + img.height + 4)], fill=grey)
        d.text((px - 16, pad_t + img.height + 6), f"{f/1e3:+.0f}k", fill=txt)

    dur_ms = 1000.0 * np.asarray(x).size / samp_rate
    d.text((2, pad_t + img.height - 12), f"{dur_ms:.1f} ms", fill=txt)
    d.text((2, pad_t + 2), "dB", fill=txt)
    vmax = float(np.nanmax(power))
    d.text((2, pad_t + 16), f"{vmax:+.0f}", fill=txt)
    vmin = vmax - 70.0
    d.text((2, pad_t + img.height - 26), f"{vmin:+.0f}", fill=txt)
    return canvas


def _title_bar(img: Image.Image, title: str) -> Image.Image:
    bar = Image.new("RGB", (img.width, 22), (22, 26, 32))
    ImageDraw.Draw(bar).text((6, 5), title, fill=(230, 235, 240))
    out = Image.new("RGB", (img.width, img.height + bar.height), (16, 18, 22))
    out.paste(bar, (0, 0))
    out.paste(img, (0, bar.height))
    return out


def save_waterfall(path: str | Path, x: np.ndarray, samp_rate: float, **kw) -> Path:
    """Посчитать водопад и сохранить PNG. Возвращает путь."""
    img = render_waterfall(x, samp_rate, **kw)
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    img.save(p)
    return p
