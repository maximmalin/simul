"""
Тесты DSP-ядра виртуального приёма (numpy, без GNU Radio).

`gnuradio_krystal.virtual_iq` — «полезная нагрузка»: генератор виртуального
I/Q и спектральный анализ. Здесь проверяется физика: частота тона, SNR,
детерминированность, инвариантность к размеру блока и квантование АЦП.

Запуск:
    pytest tests/requirements/gnuradio/test_virtual_iq.py -v
"""

import numpy as np
import pytest

from gnuradio_krystal.virtual_iq import (
    Emitter,
    VirtualScene,
    deterministic_samples,
    emitters_from_spec,
    noise_floor_dbm,
    peak_frequency,
    snr_db_from_reference,
    spectrum,
)

FS = 1.0e6


def test_noise_floor_thermal():
    """k·T·B для 2.7 кГц при 290 K ≈ −139.7 дБм; NF добавляется напрямую"""
    assert noise_floor_dbm(2700.0) == pytest.approx(-139.66, abs=0.05)
    assert noise_floor_dbm(2700.0, nf_db=3.5) == pytest.approx(-136.16, abs=0.05)


def test_noise_floor_rejects_zero_bandwidth():
    with pytest.raises(ValueError):
        noise_floor_dbm(0.0)


def test_single_tone_peak_frequency():
    """CW-тон на +100 кГц находится на своём месте (в пределах бина)"""
    sc = VirtualScene(samp_rate=FS, emitters=[Emitter(offset_hz=100e3, amplitude=0.2)],
                      snr_db=30.0, seed=1)
    x = sc.generate(1 << 16)
    assert peak_frequency(x, FS) == pytest.approx(100e3, abs=FS / (1 << 16))


def test_measured_snr_matches_target():
    """После вычитания известного тона шум даёт заданный SNR"""
    sc = VirtualScene(samp_rate=FS, emitters=[Emitter(offset_hz=50e3, amplitude=0.1)],
                      snr_db=25.0, seed=2)
    x = sc.generate(1 << 15)
    ref = deterministic_samples(sc.emitters, x.size, FS)
    assert snr_db_from_reference(x, ref) == pytest.approx(25.0, abs=0.5)


def test_deterministic_same_seed():
    """Один seed -> один и тот же поток"""
    kw = dict(samp_rate=FS, emitters=[Emitter(offset_hz=10e3)], snr_db=20.0, seed=7)
    assert np.array_equal(VirtualScene(**kw).generate(2048),
                          VirtualScene(**kw).generate(2048))


def test_different_seed_differs():
    a = VirtualScene(samp_rate=FS, emitters=[], snr_db=20.0, seed=1).generate(2048)
    b = VirtualScene(samp_rate=FS, emitters=[], snr_db=20.0, seed=2).generate(2048)
    assert not np.array_equal(a, b)


def test_block_size_invariance():
    """Поток не зависит от размера блока (важно для GNU Radio work())"""
    kw = dict(samp_rate=FS, emitters=[Emitter(offset_hz=33e3, amplitude=0.1)],
              snr_db=20.0, seed=3)
    a, b = VirtualScene(**kw), VirtualScene(**kw)
    joined = np.concatenate([a.generate(1000), a.generate(1500)])
    assert np.array_equal(joined, b.generate(2500))


def test_adc_quantization_grid():
    bits = 12
    sc = VirtualScene(samp_rate=FS, emitters=[Emitter(offset_hz=0, amplitude=0.3)],
                      snr_db=30.0, seed=4, adc_bits=bits)
    x = sc.generate(4096)
    step = 2.0 / (2 ** bits)
    assert np.allclose(x.real, np.round(x.real / step) * step, atol=1e-6)


def test_clipping_to_full_scale():
    sc = VirtualScene(samp_rate=FS, emitters=[Emitter(offset_hz=0, amplitude=5.0)],
                      snr_db=40.0, seed=5, adc_bits=0)
    x = sc.generate(1024)
    assert np.max(np.abs(x.real)) <= 1.0 + 1e-6
    assert np.max(np.abs(x.imag)) <= 1.0 + 1e-6


def test_emitters_from_spec_defaults():
    ems = emitters_from_spec([{"offset_hz": 123.0}])
    assert ems[0].offset_hz == 123.0
    assert ems[0].modulation == "cw"
    assert ems[0].amplitude == Emitter.amplitude


def test_emitters_from_spec_empty():
    assert emitters_from_spec(None) == ()
    assert emitters_from_spec([]) == ()


def test_am_expected_power():
    e = Emitter(offset_hz=0, amplitude=0.1, modulation="am", mod_index=1.0)
    assert e.expected_power() == pytest.approx(0.1 ** 2 * 1.5)


def test_bad_modulation_rejected():
    with pytest.raises(ValueError):
        Emitter(offset_hz=0, modulation="qpsk")


def test_spectrum_shape_and_range():
    sc = VirtualScene(samp_rate=FS, emitters=[Emitter(offset_hz=0)], snr_db=10.0, seed=6)
    x = sc.generate(1024)
    f, p = spectrum(x, FS)
    assert f.size == p.size == 1024
    assert f[0] == pytest.approx(-FS / 2)
    assert np.all(np.diff(f) > 0)
