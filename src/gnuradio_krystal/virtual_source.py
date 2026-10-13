"""
GNU Radio Source block: виртуальный приём для Krystal Radio.

`virtual_source` — это `gr.sync_block` без входов и с одним комплексным
выходом (np.complex64). Он отдаёт поток из `VirtualScene`, поэтому его
можно подключать в любой flowgraph GNU Radio (waterfall, FFT, демодулятор,
file_sink) и наблюдать «виртуальный эфир» без реального железа.

Пример:

    from gnuradio import gr
    from gnuradio_krystal.virtual_source import virtual_source, emitters_from_spec

    tb = gr.top_block()
    src = virtual_source(samp_rate=2.4e6, snr_db=35.0,
                         emitters=emitters_from_spec([
                             {"offset_hz": 150e3, "amplitude": 0.08},
                             {"offset_hz": -220e3, "amplitude": 0.05,
                              "modulation": "am", "mod_rate_hz": 1000},
                         ]))
    tb.connect(src, <sink>)
"""

from __future__ import annotations

from typing import Iterable

import numpy as np
from gnuradio import gr

from gnuradio_krystal.virtual_iq import (
    DEFAULT_ADC_BITS,
    DEFAULT_NF_DB,
    DEFAULT_SAMP_RATE,
    Emitter,
    VirtualScene,
    emitters_from_spec,  # реэкспорт: удобно импортировать из этого модуля
    noise_floor_dbm,
)

__all__ = ["virtual_source", "emitters_from_spec"]


class virtual_source(gr.sync_block):
    """Источник комплексных отсчётов виртуального эфира."""

    def __init__(self, samp_rate: float = DEFAULT_SAMP_RATE,
                 emitters: Iterable[Emitter] = (), snr_db: float = 30.0,
                 seed: int = 0, adc_bits: int = DEFAULT_ADC_BITS,
                 dc_offset: complex = 0j, nf_db: float = DEFAULT_NF_DB,
                 name: str = "Krystal virtual RX source"):
        gr.sync_block.__init__(
            self,
            name=name,
            in_sig=None,                 # вход только синтетический
            out_sig=[np.complex64],      # комплексный I/Q поток
        )
        self.scene = VirtualScene(
            samp_rate=samp_rate,
            emitters=tuple(emitters),
            snr_db=snr_db,
            seed=seed,
            adc_bits=adc_bits,
            dc_offset=dc_offset,
            nf_db=nf_db,
        )

    # -- параметры ---------------------------------------------------------
    @property
    def samp_rate(self) -> float:
        return self.scene.samp_rate

    @property
    def noise_floor_dbm(self) -> float:
        return noise_floor_dbm(self.scene.samp_rate, self.scene.nf_db)

    def set_snr_db(self, value: float) -> None:
        self.scene.snr_db = float(value)

    def set_emitters(self, emitters: Iterable[Emitter]) -> None:
        self.scene.emitters = tuple(emitters)
        self.scene.reset()

    # -- поток -------------------------------------------------------------
    def work(self, input_items, output_items):
        out = output_items[0]
        out[:] = self.scene.generate(len(out))
        return len(out)
