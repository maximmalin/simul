# REQ-IO-02 — Модуль GNU Radio

**Подсистема:** Интерфейс с ПК / ПО
**Реализация:** `src/gnuradio_krystal/` — `virtual_source.py` (Source block), `cosim.py`, `waterfall.py`

- **Требование:** Библиотека GNU Radio с Source block, отдающим комплексный поток, и headless-режим ко-симуляции с выводом водопада и спектра.
- **Стандарт:** GNU Radio 3.10 (проверено на 3.10.12).
- **Проверка:** `tests/requirements/gnuradio/`, `make gnuradio`.

### Состав модуля

| модуль | назначение | зависимости |
|---|---|---|
| `virtual_iq.py` | генератор I/Q, тепловой шум, квантование АЦП | только numpy |
| `waterfall.py` | headless-водопад (numpy + Pillow) | numpy, Pillow |
| `virtual_source.py` | GNU Radio Source block | GNU Radio |
| `cosim.py` | ко-симуляция, GUI-водопад | GNU Radio |

Ядро (`virtual_iq`, `waterfall`) намеренно не зависит от GNU Radio —
это позволяет проверять физику обычным pytest без установки SDR-стека.

### Артефакты

```
make gnuradio  →  out/waterfall.png
                 out/virtual_rx_spectrum.json
                 out/virtual_rx_iq.c64
make gnuradio-gui → qtgui.waterfall_sink_c (живой водопад, нужен дисплей)
```
