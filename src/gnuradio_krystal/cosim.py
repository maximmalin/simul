"""
GNU Radio co-simulation виртуального приёма Krystal Radio.

Что происходит:
    1. `virtual_source` синтезирует комплексный виртуальный эфир
       (источники + шум по заданному SNR, NF из REQ-04);
    2. поток прогоняется через GNU Radio top_block;
    3. результат анализируется (спектр) и рисуется водопад (PNG).

Два режима:
    * headless — пишет `virtual_rx_iq.c64`, `waterfall.png` и
      `virtual_rx_spectrum.json` (цель `make gnuradio` и HTML-страница);
    * `--gui`  — открывает живой водопад GNU Radio
      (`qtgui.waterfall_sink_c`), поток с троттлингом в реальном времени,
      так что водопад можно смотреть «в эфире».

Если GNU Radio нет, headless-режим всё равно работает: тот же
`VirtualScene` (тот же поток отсчётов) вызывается напрямую, а водопад
рисуется numpy+Pillow. Это позволяет собирать страницу и гонять тесты
даже в окружении без GNU Radio.

Запуск:
    python3 src/gnuradio_krystal/krystal_virtual_rx.py --out out
    python3 src/gnuradio_krystal/krystal_virtual_rx.py --gui
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

import numpy as np

try:  # GNU Radio стоит только под системным python3, в venv его нет
    from gnuradio import blocks, gr

    HAVE_GNU_RADIO = True
except Exception:  # pragma: no cover — зависит от окружения
    blocks = gr = None  # type: ignore[assignment]
    HAVE_GNU_RADIO = False

from gnuradio_krystal.virtual_iq import (
    DEFAULT_ADC_BITS,
    DEFAULT_NF_DB,
    DEFAULT_SAMP_RATE,
    Emitter,
    VirtualScene,
    deterministic_samples,
    emitters_from_spec,
    measured_snr_db,
    noise_floor_dbm,
    snr_db_from_reference,
    spectrum,
)

# Виртуальная сцена по умолчанию: несколько сигналов в полосе 2.4 МГц —
# демонстрационный «эфир» из CW/AM/FM на разных расстройках.
DEFAULT_SCENE: list[dict] = [
    {"offset_hz": 150e3, "amplitude": 0.08, "modulation": "cw"},
    {"offset_hz": -220e3, "amplitude": 0.05, "modulation": "am",
     "mod_rate_hz": 1000.0, "mod_index": 0.8},
    {"offset_hz": 320e3, "amplitude": 0.04, "modulation": "fm",
     "mod_rate_hz": 800.0, "mod_index": 2.0},
    {"offset_hz": -80e3, "amplitude": 0.02, "modulation": "cw"},
]


if HAVE_GNU_RADIO:

    class iq_collector(gr.sync_block):  # type: ignore[misc]
        """Приёмник-накопитель: собирает отсчёты в память и/или файл."""

        def __init__(self, path: str | Path | None = None):
            gr.sync_block.__init__(self, name="iq collector",
                                   in_sig=[np.complex64], out_sig=None)
            self._chunks: list[np.ndarray] = []
            self._path = Path(path) if path else None
            self._fh = None
            if self._path:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                self._fh = open(self._path, "wb")

        def work(self, input_items, output_items):
            x = np.array(input_items[0], dtype=np.complex64, copy=True)
            self._chunks.append(x)
            if self._fh:
                self._fh.write(x.tobytes())
            return len(x)

        def close(self) -> None:
            if self._fh:
                self._fh.close()
                self._fh = None

        @property
        def samples(self) -> np.ndarray:
            if not self._chunks:
                return np.zeros(0, dtype=np.complex64)
            return np.concatenate(self._chunks)


def _write_c64(path: str | Path | None, x: np.ndarray) -> None:
    if not path:
        return
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(np.asarray(x, dtype=np.complex64).tobytes())


def run_headless(samp_rate: float, emitters: Sequence[Emitter], snr_db: float,
                 seed: int, adc_bits: int, nsamples: int,
                 iq_path: str | Path | None = None,
                 nf_db: float = DEFAULT_NF_DB) -> tuple[np.ndarray, VirtualScene]:
    """Синтезировать поток виртуального приёма.

    Возвращает (отсчёты np.complex64, сцена). Если GNU Radio установлен,
    поток идёт через настоящий top_block (source -> head -> collector);
    иначе тот же `VirtualScene` вызывается напрямую. Генерация инвариантна
    к размеру блока, поэтому оба пути дают один и тот же поток отсчётов.
    """
    if HAVE_GNU_RADIO:
        from gnuradio_krystal.virtual_source import virtual_source

        tb = gr.top_block("krystal_virtual_rx")
        src = virtual_source(samp_rate=samp_rate, emitters=tuple(emitters),
                             snr_db=snr_db, seed=seed, adc_bits=adc_bits,
                             nf_db=nf_db)
        sink = iq_collector(path=None)      # в файл пишем сами, ниже
        head = blocks.head(gr.sizeof_gr_complex, int(nsamples))
        tb.connect(src, head, sink)
        tb.run()
        tb.wait()
        sink.close()
        x = sink.samples
        _write_c64(iq_path, x)
        return x, src.scene

    scene = VirtualScene(samp_rate=samp_rate, emitters=tuple(emitters),
                         snr_db=snr_db, seed=seed, adc_bits=adc_bits,
                         nf_db=nf_db)
    x = scene.generate(int(nsamples))
    _write_c64(iq_path, x)
    return x, scene


def build_gui(samp_rate: float, emitters: Sequence[Emitter], snr_db: float,
              seed: int, adc_bits: int, fftsize: int = 2048,
              title: str = "Krystal virtual RX waterfall",
              nf_db: float = DEFAULT_NF_DB):
    """Живой водопад GNU Radio: virtual_source -> throttle -> waterfall_sink."""
    if not HAVE_GNU_RADIO:  # pragma: no cover — проверяется в main()
        raise RuntimeError("GNU Radio недоступен: GUI-водопад не запустить")
    from gnuradio import fft, qtgui

    from gnuradio_krystal.virtual_source import virtual_source

    tb = gr.top_block("krystal_virtual_rx_gui")
    src = virtual_source(samp_rate=samp_rate, emitters=tuple(emitters),
                         snr_db=snr_db, seed=seed, adc_bits=adc_bits,
                         nf_db=nf_db, name="Krystal virtual RX source")
    thr = blocks.throttle(gr.sizeof_gr_complex, samp_rate, True)
    wf = qtgui.waterfall_sink_c(int(fftsize), fft.window.WIN_HANN,
                                0.0, float(samp_rate), title)
    wf.set_update_time(0.10)
    tb.connect(src, thr, wf)
    return tb, src, wf


def analyze(x: np.ndarray, samp_rate: float, scene: VirtualScene) -> dict:
    """Спектральная сводка виртуального потока.

    Точный SNR считается вычитанием известной детерминированной части:
    остаток — шум (и квантование АЦП). `snr_time_db` оставлен для сверки:
    при больших SNR он смещён (вычитание близких величин).
    """
    freqs, power = spectrum(x, samp_rate)
    peak_i = int(np.argmax(power))
    reference = deterministic_samples(scene.emitters, x.size, samp_rate)
    return {
        "samp_rate": float(samp_rate),
        "nsamples": int(x.size),
        "adc_bits": int(scene.adc_bits),
        "nf_db": float(scene.nf_db),
        "snr_target_db": float(scene.snr_db),
        "noise_floor_dbm": float(noise_floor_dbm(samp_rate, scene.nf_db)),
        "peak_freq_hz": float(freqs[peak_i]),
        "peak_dbfs": float(power[peak_i]),
        "snr_measured_db": float(snr_db_from_reference(x, reference, scene.dc_offset)),
        "snr_time_db": float(measured_snr_db(x, scene.signal_power)),
        "rms": float(np.sqrt(np.mean(np.abs(x.astype(np.complex128)) ** 2))),
        "emitters": [
            {"offset_hz": float(e.offset_hz), "amplitude": float(e.amplitude),
             "modulation": e.modulation}
            for e in scene.emitters
        ],
    }


def _load_scene(path: str | None) -> list[dict]:
    if not path:
        return list(DEFAULT_SCENE)
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("emitters", [])
    return list(data)


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Krystal Radio GNU Radio co-simulation (virtual RX -> waterfall)")
    p.add_argument("--out", default="out", help="каталог для артефактов")
    p.add_argument("--samp-rate", type=float, default=DEFAULT_SAMP_RATE)
    p.add_argument("--snr-db", type=float, default=35.0, help="SNR виртуального эфира")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--adc-bits", type=int, default=DEFAULT_ADC_BITS)
    p.add_argument("--nf-db", type=float, default=DEFAULT_NF_DB)
    p.add_argument("--nsamples", type=int, default=1 << 18)
    p.add_argument("--nfft", type=int, default=1024)
    p.add_argument("--scene", default=None, help="JSON-файл со сценой (список источников)")
    p.add_argument("--title", default="Krystal Radio — virtual RX waterfall")
    p.add_argument("--gui", action="store_true",
                   help="живой водопад GNU Radio (требуется дисплей)")
    return p.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    emitters = emitters_from_spec(_load_scene(args.scene))

    if args.gui:
        if not HAVE_GNU_RADIO:
            print("[gnuradio] GNU Radio недоступен — GUI-водопад не запустить.\n"
                  "           Установите gnuradio или используйте headless-режим "
                  "(он нарисует out/waterfall.png).", flush=True)
            return 3
        print(f"[gnuradio] GUI waterfall: fc=0, bw={args.samp_rate/1e6:.2f} МГц, "
              f"SNR={args.snr_db} дБ, источников {len(emitters)}", flush=True)
        tb, _src, _wf = build_gui(args.samp_rate, emitters, args.snr_db,
                                  args.seed, args.adc_bits, args.nfft,
                                  args.title, args.nf_db)
        tb.run()   # блокирует до закрытия окна
        tb.wait()
        return 0

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    iq_path = out / "virtual_rx_iq.c64"

    x, scene = run_headless(args.samp_rate, emitters, args.snr_db, args.seed,
                            args.adc_bits, args.nsamples, iq_path, args.nf_db)
    summary = analyze(x, args.samp_rate, scene)
    summary["iq_file"] = str(iq_path)
    summary["waterfall_png"] = str(out / "waterfall.png")
    summary["title"] = args.title

    # Водопад: numpy + Pillow, без дисплея.
    from gnuradio_krystal.waterfall import save_waterfall

    save_waterfall(out / "waterfall.png", x, args.samp_rate,
                   nfft=args.nfft, height=420, title=args.title)
    (out / "virtual_rx_spectrum.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    backend = "GNU Radio" if HAVE_GNU_RADIO else "numpy (GNU Radio недоступен)"
    print(f"[gnuradio] backend: {backend}")
    print(f"[gnuradio] samples={summary['nsamples']} "
          f"({summary['nsamples']/summary['samp_rate']*1e3:.2f} мс), "
          f"пик {summary['peak_freq_hz']/1e3:+.1f} кГц @ {summary['peak_dbfs']:.1f} дБFS")
    print(f"[gnuradio] SNR (модель) {summary['snr_target_db']:.1f} дБ, "
          f"измерено {summary['snr_measured_db']:.1f} дБ, "
          f"шум {summary['noise_floor_dbm']:.1f} дБм")
    print(f"[gnuradio] waterfall: {summary['waterfall_png']}")
    print(f"[gnuradio] iq: {iq_path}  spectrum: {out/'virtual_rx_spectrum.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
