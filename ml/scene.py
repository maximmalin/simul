"""
Виртуальные сцены «эфира» для обучения и проверки ML-детектора.

Сцена — это набор источников (`SignalSource`), каждый из которых активен
только в своём временном окне (burst), имеет расстройку от центра ПЧ,
занимаемую полосу и закон модуляции из фиксированного списка классов:

    cw    — немодулированная несущая (узкая линия);
    am    — амплитудная модуляция (несущая + две боковые);
    fm    — угловая модуляция (по Карсону занимает 2·(Δf + Fм));
    noise — шумовой источник (занимает заданную полосу).

Сцены синтезируются по той же физике, что и `virtual_iq.VirtualScene`
(zero-IF, комплексный I/Q, тепловой шум по SNR, квантование АЦП), но с
временным стробированием источников. Это позволяет получить для каждого
сигнала честную ограничивающую рамку (freq × time) — разметку для YOLO.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

import numpy as np

CLASSES: tuple[str, ...] = ("cw", "am", "fm", "noise")

# Радиофизические параметры виртуального тракта (см. virtual_iq.py).
K_BOLTZMANN = 1.380649e-23
T0_KELVIN = 290.0
DEFAULT_NF_DB = 3.5
DEFAULT_ADC_BITS = 12

# Полоса виртуального приёма для ML подобрана так, чтобы боковые полосы
# AM/FM были разрешены окном STFT (fs/nperseg ≈ 2 кГц).
DEFAULT_FS = 250e3


@dataclass
class SignalSource:
    """Один источник в полосе приёма (burst во времени)."""

    offset_hz: float
    amplitude: float = 0.1
    modulation: str = "cw"
    mod_index: float = 0.5
    mod_rate_hz: float = 4_000.0
    t_start: float = 0.0
    t_stop: float = 1e9
    bandwidth_hz: float = 0.0   # только для modulation == "noise"

    def __post_init__(self) -> None:
        if self.modulation not in CLASSES:
            raise ValueError(f"unknown modulation: {self.modulation}")
        if self.amplitude < 0:
            raise ValueError("amplitude must be >= 0")
        if self.t_stop <= self.t_start:
            raise ValueError("t_stop must be > t_start")

    def occupied_bandwidth_hz(self, freq_resolution_hz: float) -> float:
        """Занятая полоса (для бокса и метрик)."""
        if self.modulation == "cw":
            return 3.0 * freq_resolution_hz
        if self.modulation == "am":
            return 2.0 * self.mod_rate_hz
        if self.modulation == "fm":
            # Правило Карсона: B ≈ 2·(Δf + f_m), Δf = index · f_m.
            return 2.0 * (self.mod_index + 1.0) * self.mod_rate_hz
        return max(self.bandwidth_hz, 3.0 * freq_resolution_hz)

    def component(self, t: np.ndarray, rng: np.random.Generator) -> np.ndarray:
        """Комплексная огибающая источника (без шума и гейта)."""
        phase = 2.0 * np.pi * self.offset_hz * t
        a = self.amplitude
        if self.modulation == "cw":
            return a * np.exp(1j * phase)
        if self.modulation == "am":
            return a * (1.0 + self.mod_index * np.cos(
                2.0 * np.pi * self.mod_rate_hz * t)) * np.exp(1j * phase)
        if self.modulation == "fm":
            return a * np.exp(1j * (phase + self.mod_index * np.sin(
                2.0 * np.pi * self.mod_rate_hz * t)))
        # noise: комплексный белый шум; полосовая фильтрация — в generate()
        # (через band_limit), т.к. ей нужна частота дискретизации сцены.
        n = t.size
        return rng.standard_normal(n) + 1j * rng.standard_normal(n)


def band_limit(noise: np.ndarray, fs: float, center_hz: float,
               bandwidth_hz: float) -> np.ndarray:
    """Ограничить белый шум прямоугольной полосой вокруг center_hz."""
    n = noise.size
    spec = np.fft.fft(noise)
    freqs = np.fft.fftfreq(n, d=1.0 / fs)
    mask = np.abs(freqs - center_hz) <= bandwidth_hz / 2.0
    spec *= mask
    return np.fft.ifft(spec)


def noise_floor_dbm(bandwidth_hz: float, nf_db: float = 0.0,
                    temp_k: float = T0_KELVIN) -> float:
    """Тепловой шум k·T·B в дБм (+ NF)."""
    if bandwidth_hz <= 0:
        raise ValueError("bandwidth_hz must be > 0")
    return 10.0 * np.log10(K_BOLTZMANN * temp_k * bandwidth_hz * 1000.0) + nf_db


@dataclass
class SignalScene:
    """Генератор виртуального кадра (I/Q) с аддитивным шумом и АЦП."""

    fs: float = DEFAULT_FS
    n_samples: int = 8256
    sources: tuple[SignalSource, ...] = ()
    snr_db: float = 25.0
    seed: int = 0
    adc_bits: int = DEFAULT_ADC_BITS
    dc_offset: complex = 0j
    nf_db: float = DEFAULT_NF_DB
    freq_resolution_hz: float = 0.0

    def __post_init__(self) -> None:
        if self.fs <= 0:
            raise ValueError("fs must be > 0")
        self.sources = tuple(self.sources)
        if self.freq_resolution_hz <= 0:
            self.freq_resolution_hz = self.fs / 128.0

    @property
    def duration_s(self) -> float:
        return self.n_samples / self.fs

    def generate(self, rng: np.random.Generator | None = None) -> np.ndarray:
        """Сгенерировать комплексный кадр (np.complex64), уже с шумом и АЦП."""
        rng = rng if rng is not None else np.random.default_rng(self.seed)
        n = self.n_samples
        t = np.arange(n, dtype=np.float64) / self.fs
        x = np.zeros(n, dtype=np.complex128)
        signal_power = 0.0

        for src in self.sources:
            comp = src.component(t, rng)
            if src.modulation == "noise":
                comp = band_limit(comp, self.fs, src.offset_hz,
                                  src.occupied_bandwidth_hz(
                                      self.freq_resolution_hz))
                # Нормируем так, чтобы мощность не зависела от полосы.
                p = float(np.mean(np.abs(comp) ** 2))
                if p > 0:
                    comp *= src.amplitude / np.sqrt(p)
            gate = ((t >= src.t_start) & (t < src.t_stop)).astype(np.float64)
            comp = comp * gate
            x += comp
            signal_power += float(np.mean(np.abs(comp) ** 2))

        # Аддитивный комплексный гауссов шум под заданный SNR.
        ref = np.sqrt(signal_power) if signal_power > 0 else 1.0
        sigma = ref / np.sqrt(10.0 ** (self.snr_db / 10.0))
        x = x + sigma / np.sqrt(2.0) * (
            rng.standard_normal(n) + 1j * rng.standard_normal(n))
        x = x + self.dc_offset

        x = np.clip(x.real, -1.0, 1.0) + 1j * np.clip(x.imag, -1.0, 1.0)
        if self.adc_bits and self.adc_bits > 0:
            levels = 2 ** int(self.adc_bits)
            step = 2.0 / levels
            x = np.round(x / step) * step
        return x.astype(np.complex64)

    def boxes(self, mapper) -> list[tuple[float, float, float, float]]:
        """Истинные YOLO-боксы (cx, cy, w, h) для каждого источника."""
        return [mapper.box_of(s.offset_hz,
                              s.occupied_bandwidth_hz(mapper.freq_resolution_hz),
                              s.t_start, s.t_stop) for s in self.sources]

    def classes(self) -> list[int]:
        from .dataset import CLASSES
        return [CLASSES.index(s.modulation) for s in self.sources]
