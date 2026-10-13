"""
Krystal Radio — GNU Radio виртуальный приём (co-simulation).

Состав пакета:
    * virtual_iq.py     — генератор виртуального I/Q (numpy, без GNU Radio);
    * waterfall.py      — headless-водопад (numpy + Pillow);
    * virtual_source.py — GNU Radio Source block (gr.sync_block);
    * cosim.py          — co-simulation: headless PNG/JSON + GUI-водопад.

Модули `virtual_iq` и `waterfall` не зависят от GNU Radio, поэтому их можно
импортировать и тестировать обычным Python+numpy. GNU Radio нужен только
для `virtual_source`, `cosim` и `--gui`.
"""

from gnuradio_krystal.virtual_iq import (  # noqa: F401
    DEFAULT_ADC_BITS,
    DEFAULT_NF_DB,
    DEFAULT_SAMP_RATE,
    Emitter,
    VirtualScene,
    measured_snr_db,
    noise_floor_dbm,
    peak_frequency,
    spectrum,
)

__all__ = [
    "VirtualScene", "Emitter", "noise_floor_dbm", "spectrum",
    "measured_snr_db", "DEFAULT_SAMP_RATE", "DEFAULT_NF_DB", "DEFAULT_ADC_BITS",
]
__version__ = "1.0.0"
