"""
Спектрограмма I/Q-потока для ML-детектора сигналов (YOLO).

Модуль не тянет за собой TensorFlow: только `numpy` (ядро) и `Pillow`
(сохранение PNG). Поэтому спектрограмму можно строить и проверять обычным
pytest'ом в venv, а нейросеть — отдельным (опциональным) шагом.

Соглашения по системе координат (важно для YOLO-разметки):
    * строка 0 = нижняя частота (-fs/2), строка H-1 = верхняя (+fs/2);
    * столбец 0 = начало записи (t=0), столбец W-1 = конец;
    * YOLO-бокс нормирован в [0,1]: (cx, cy, w, h), cy считается от верха,
      поэтому `cy = (f + fs/2) / fs`.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# Размер картинки: (H = число частотных бинов, W = число временных кадров).
DEFAULT_IMAGE = (128, 128)
# Окно STFT по умолчанию совпадает с числом частотных бинов.
DEFAULT_NPERSEG = 128
DEFAULT_HOP = 64


def window(n: int, kind: str = "hann") -> np.ndarray:
    """Оконная функция (только то, что реально нужно)."""
    if kind == "hann":
        return np.hanning(n)
    if kind in ("none", None):
        return np.ones(n)
    raise ValueError(f"unknown window: {kind}")


def stft(x: np.ndarray, nperseg: int = DEFAULT_NPERSEG,
         hop: int | None = None, win: str = "hann") -> np.ndarray:
    """STFT комплексного сигнала.

    Возвращает массив (n_freq, n_time) = (nperseg, n_frames) с частотами
    после `fftshift`, то есть строка 0 — самая низкая частота.
    """
    x = np.asarray(x, dtype=np.complex128).ravel()
    if hop is None:
        hop = nperseg // 2
    if hop <= 0:
        raise ValueError("hop must be > 0")
    if x.size < nperseg:
        x = np.pad(x, (0, nperseg - x.size))
    w = window(nperseg, win)
    frames = np.lib.stride_tricks.sliding_window_view(x, nperseg)[::hop]
    X = np.fft.fftshift(np.fft.fft(frames * w, axis=1), axes=1)
    # (n_frames, n_freq) -> (n_freq, n_time)
    return X.T


def spectrogram_db(x: np.ndarray, nperseg: int = DEFAULT_NPERSEG,
                   hop: int | None = None, win: str = "hann") -> np.ndarray:
    """Спектрограмма в дБ относительно полной шкалы АЦП (0 дБ = FS)."""
    X = stft(x, nperseg, hop, win)
    scale = np.sum(window(nperseg, win))
    amp = np.abs(X) / scale
    return 20.0 * np.log10(np.maximum(amp, 1e-12))


def _interp_axis(a: np.ndarray, n: int, axis: int) -> np.ndarray:
    """Линейная интерполяция массива до `n` отсчётов по оси axis."""
    m = a.shape[axis]
    if m == n:
        return a
    src = np.linspace(0.0, 1.0, m)
    dst = np.linspace(0.0, 1.0, n)
    return np.apply_along_axis(lambda v: np.interp(dst, src, v), axis, a)


def to_image(p_db: np.ndarray, size: tuple[int, int] = DEFAULT_IMAGE,
             vmin: float | None = None, vmax: float | None = None) -> np.ndarray:
    """Привести спектрограмму к картинке (H, W) в диапазоне [0, 1].

    `vmin`/`vmax` — границы дБ; по умолчанию берутся робастные процентили,
    чтобы слабые сигналы не утонули в выбросах.
    """
    h, w = size
    img = _interp_axis(np.asarray(p_db, dtype=np.float64), h, axis=0)
    img = _interp_axis(img, w, axis=1)
    if vmin is None:
        vmin = float(np.percentile(img, 5.0))
    if vmax is None:
        vmax = float(np.percentile(img, 99.5))
    if vmax <= vmin:
        vmax = vmin + 1.0
    return np.clip((img - vmin) / (vmax - vmin), 0.0, 1.0)


# --- сохрание PNG ----------------------------------------------------------

# Компактная палитра (viridis-подобная), чтобы PNG читался человеком.
_CMAP = np.array([
    [68, 1, 84], [72, 40, 120], [62, 74, 137], [49, 104, 142],
    [38, 130, 142], [31, 158, 137], [53, 183, 121], [109, 205, 89],
    [180, 222, 44], [253, 231, 37],
], dtype=np.uint8)


def to_rgb(img: np.ndarray) -> np.ndarray:
    """[0,1] (H,W) -> RGB (H,W,3) uint8 через палитру _CMAP."""
    idx = np.clip(np.rint(np.asarray(img) * (len(_CMAP) - 1)), 0,
                  len(_CMAP) - 1).astype(np.int64)
    return _CMAP[idx]


def save_png(path, img: np.ndarray, rgb: bool = True) -> None:
    """Сохранить (H,W) [0,1] как PNG (RGB или оттенки серого)."""
    from PIL import Image

    arr = np.clip(np.asarray(img) * 255.0, 0, 255).astype(np.uint8)
    if rgb:
        Image.fromarray(to_rgb(np.asarray(img)), mode="RGB").save(path)
    else:
        Image.fromarray(arr, mode="L").save(path)


# --- отображение координат (freq/t -> пиксель) -----------------------------

@dataclass(frozen=True)
class SpecMapper:
    """Отображение (частота, время) <-> (строка, столбец) картинки."""

    fs: float
    nperseg: int
    hop: int
    n_frames: int
    image: tuple[int, int] = DEFAULT_IMAGE

    @property
    def h(self) -> int:
        return self.image[0]

    @property
    def w(self) -> int:
        return self.image[1]

    @property
    def freq_resolution_hz(self) -> float:
        return self.fs / self.nperseg

    @property
    def duration_s(self) -> float:
        return ((self.n_frames - 1) * self.hop) / self.fs

    def t_center(self, frame: int) -> float:
        return (frame * self.hop + self.nperseg / 2.0) / self.fs

    def row_of(self, freq_hz: float) -> float:
        return (freq_hz + self.fs / 2.0) / self.fs * (self.h - 1)

    def col_of(self, t_s: float) -> float:
        return t_s / self.duration_s * (self.w - 1)

    def freq_of(self, row: float) -> float:
        return row / (self.h - 1) * self.fs - self.fs / 2.0

    def time_of(self, col: float) -> float:
        return col / (self.w - 1) * self.duration_s

    def box_of(self, offset_hz: float, bandwidth_hz: float,
               t_start: float, t_stop: float) -> tuple[float, float, float, float]:
        """YOLO-бокс (cx, cy, w, h) для сигнала в полосе."""
        cy = (offset_hz + self.fs / 2.0) / self.fs
        hh = max(bandwidth_hz, 1.5 * self.freq_resolution_hz) / self.fs
        cx = (t_start + t_stop) / 2.0 / self.duration_s
        ww = max(t_stop - t_start, 1e-3) / self.duration_s
        return (float(cx), float(cy), float(ww), float(hh))


def mapper_for(fs: float, n_samples: int, nperseg: int = DEFAULT_NPERSEG,
               hop: int = DEFAULT_HOP, image: tuple[int, int] = DEFAULT_IMAGE
               ) -> SpecMapper:
    """Построить `SpecMapper` для сигнала длиной n_samples."""
    n_frames = 1 + (max(n_samples, nperseg) - nperseg) // hop
    return SpecMapper(fs=fs, nperseg=nperseg, hop=hop, n_frames=n_frames,
                      image=image)
