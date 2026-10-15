"""Тесты ML-конвейера распознавания сигналов (REQ-ANA-03).

Проверяется физика, на которой стоит YOLO-детектор:

  * STFT и его разрешение по частоте — боковые полосы АМ должны быть
    разрешимы, иначе «am» неотличим от «cw» (это и определило выбор
    fs = 250 кГц);
  * занятая полоса каждого класса модуляции (правило Карсона для ЧМ);
  * соответствие истинных рамок (YOLO-разметка) реальной энергии
    спектрограммы;
  * обратимость отображения (freq,time) <-> пиксель;
  * формат датасета и совместимость с YOLO-конвенцией.

Тесты не требуют TensorFlow: работают на numpy + Pillow.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from ml import dataset as ds
from ml import scene as sc
from ml import spectrogram as sp

CLASSES = ds.CLASSES


# ===========================================================================
# STFT и разрешение
# ===========================================================================
def test_stft_shape():
    x = np.exp(1j * 2 * np.pi * 10e3 * np.arange(4096) / 250e3)
    S = sp.stft(x, nperseg=128, hop=64)
    assert S.shape[0] == 128, "строк должно быть столько же, сколько точек БПФ"
    n_frames = 1 + (4096 - 128) // 64
    assert S.shape[1] == n_frames


def test_stft_picks_tone_frequency():
    """Несущая 20 кГц при fs = 250 кГц должна попасть на бин ~10.24."""
    fs, f0 = 250e3, 20e3
    x = np.exp(1j * 2 * np.pi * f0 * np.arange(4096) / fs)
    S = sp.spectrogram_db(x)
    row = int(np.argmax(S.max(axis=1)))
    f_bin = (row / 127 - 0.5) * fs
    assert abs(f_bin - f0) < fs / 128, f"пик на {f_bin:.0f} Гц вместо {f0:.0f}"


def test_frequency_resolution_matches_am_sidebands():
    """Боковые полосы АМ с БМ 8 кГц должны разрешаться: Δf_bin ≈ 1.95 кГц."""
    fs = sc.DEFAULT_FS
    res = fs / sp.DEFAULT_NPERSEG
    assert 1.5e3 < res < 2.5e3, f"разрешение {res:.0f} Гц"
    # боковая полоса должна попадать на отдельный бин
    assert (2 * 8e3) / res > 6, "боковые полосы не разрешаются окном БПФ"


def test_hann_window_reduces_leakage():
    """Окно Ханна подавляет боковые лепестки сильнее прямоугольного."""
    fs = 250e3
    n = 4096
    f0 = 20e3
    # тон ровно между бинами -> максимальная утечка
    x = np.exp(1j * 2 * np.pi * (f0 + fs / 256) * np.arange(n) / fs)
    db_hann = sp.spectrogram_db(x, win="hann")
    db_none = sp.spectrogram_db(x, win="none")
    peak_hann = db_hann.max()
    peak_none = db_none.max()
    # главный лепесток должен быть сопоставим, а хвост — ниже
    tail_hann = np.percentile(db_hann, 90) - peak_hann
    tail_none = np.percentile(db_none, 90) - peak_none
    assert tail_hann <= tail_none + 1.0


def test_image_size_and_range(ml_modules):
    """Картинка имеет заданный размер и нормирована в [0,1]."""
    s = sc.SignalScene(
        fs=sc.DEFAULT_FS, n_samples=4096,
        sources=(sc.SignalSource(offset_hz=20e3, amplitude=0.3,
                                 modulation="cw"),),
        snr_db=30.0, seed=1,
    )
    img, mapper, boxes = ds.render_scene(s)
    assert img.shape == sp.DEFAULT_IMAGE
    assert 0.0 <= img.min() and img.max() <= 1.0


def test_mapper_roundtrip():
    """Отображение (частота, время) <-> пиксель обратимо."""
    m = sp.mapper_for(sc.DEFAULT_FS, 4096)
    for f in (-80e3, 0.0, 75e3):
        assert abs(m.freq_of(m.row_of(f)) - f) < 1.0
    for t in (0.0, 0.008, 0.016):
        assert abs(m.time_of(m.col_of(t)) - t) < 1e-4


# ===========================================================================
# Классы модуляции и занимаемая полоса
# ===========================================================================
@pytest.mark.parametrize("mod,rate,expect", [
    ("am", 8e3, 16e3),      # AM: 2·f_mod
    ("fm", 5e3, 60e3),      # FM по Карсону: 2·(Δf+f_mod), Δf=5·f_mod
])
def test_occupied_bandwidth(mod, rate, expect):
    src = sc.SignalSource(offset_hz=0.0, modulation=mod, mod_rate_hz=rate,
                          mod_index=5.0 if mod == "fm" else 0.5)
    bw = src.occupied_bandwidth_hz(1953.0)
    assert abs(bw - expect) / expect < 0.25, f"{mod}: {bw:.0f} вместо {expect:.0f}"


def test_cw_is_a_line():
    src = sc.SignalSource(offset_hz=0.0, modulation="cw")
    bw = src.occupied_bandwidth_hz(1953.0)
    assert bw < 10e3, f"CW слишком широк: {bw:.0f} Гц"


def test_class_order_is_stable():
    assert CLASSES == ("cw", "am", "fm", "noise")


# ===========================================================================
# Разметка соответствует спектру
# ===========================================================================
@pytest.mark.parametrize("mod,rate,mod_index", [
    ("cw", 0, 0), ("am", 8e3, 0.7), ("fm", 4e3, 4.0), ("noise", 0, 0),
])
def test_label_box_matches_spectral_energy(ml_modules, mod, rate, mod_index):
    """Энергия спектрограммы должна лежать внутри размеченной рамки."""
    src = sc.SignalSource(
        offset_hz=40e3, amplitude=0.35, modulation=mod,
        mod_rate_hz=rate or 1e3, mod_index=mod_index,
        bandwidth_hz=30e3 if mod == "noise" else 0.0,
        t_start=0.004, t_stop=0.012,
    )
    s = sc.SignalScene(fs=sc.DEFAULT_FS, n_samples=4096, sources=(src,),
                       snr_db=34.0, seed=7)
    img, mapper, boxes = ds.render_scene(s)
    (cx, cy, w, h), = boxes

    m = sp.mapper_for(sc.DEFAULT_FS, 4096)
    rows = slice(max(0, int(m.row_of(src.offset_hz - src.occupied_bandwidth_hz(
        m.freq_resolution_hz) / 2))),
        min(img.shape[0], int(m.row_of(src.offset_hz + src.occupied_bandwidth_hz(
            m.freq_resolution_hz) / 2)) + 1))
    cols = slice(max(0, int(m.col_of(0.004))),
                 min(img.shape[1], int(m.col_of(0.012)) + 1))

    if rows.stop <= rows.start or cols.stop <= cols.start:
        pytest.skip("окно слишком узкое для данной сетки")

    inside = img[rows, cols]
    outside = img.copy()
    outside[rows, cols] = 0.0
    assert inside.mean() > outside.mean(), (
        f"{mod}: энергия внутри рамки {inside.mean():.3f} "
        f"не выше, чем снаружи {outside.mean():.3f}")

    # центр рамки — на частоте источника.
    # cy нормализован к высоте изображения, freq_of() ждёт номер строки.
    row = cy * (m.h - 1)
    assert abs(m.freq_of(row) - src.offset_hz) < 0.06 * sc.DEFAULT_FS, (
        f"центр рамки {m.freq_of(row):.0f} Гц, источник {src.offset_hz:.0f} Гц")


def test_yolo_targets_shape_and_markers(ml_modules):
    boxes = [(0.5, 0.25, 0.4, 0.1), (0.2, 0.75, 0.2, 0.05)]
    classes = [0, 2]
    t = ds.yolo_targets(boxes, classes, grid=16, n_classes=len(CLASSES))
    assert t.shape == (16, 16, 5 + len(CLASSES))
    assert t[..., 4].sum() == 2.0, "два объекта — две отмеченные ячейки"
    assert t[..., 5 + 0].sum() == 1.0
    assert t[..., 5 + 2].sum() == 1.0
    # все значения боксов нормализованы
    assert 0.0 <= t[..., 0:4].min() and t[..., 0:4].max() <= 1.0


def test_yolo_targets_skip_occupied_cells(ml_modules):
    """Два объекта в одной ячейке: второй отбрасывается, а не ломает сетку."""
    t = ds.yolo_targets([(0.02, 0.02, 0.01, 0.01),
                         (0.04, 0.04, 0.01, 0.01)],
                        [0, 1], grid=16, n_classes=4)
    assert t[..., 4].sum() == 1.0


# ===========================================================================
# Формат датасета
# ===========================================================================
def test_scene_generation_is_deterministic(ml_modules):
    a = ds.random_scene(np.random.default_rng(1234))
    b = ds.random_scene(np.random.default_rng(1234))
    assert len(a.sources) == len(b.sources)
    assert [s.offset_hz for s in a.sources] == [s.offset_hz for s in b.sources]


def test_scene_has_valid_sources(ml_modules):
    for seed in range(12):
        s = ds.random_scene(np.random.default_rng(seed))
        assert 1 <= len(s.sources) <= 4
        for src in s.sources:
            assert src.modulation in CLASSES
            assert src.t_stop > src.t_start
            assert src.amplitude >= 0


def test_generated_scene_iq_shape(ml_modules):
    s = ds.random_scene(np.random.default_rng(7))
    iq = s.generate()
    assert iq.shape == (s.n_samples,)
    assert np.iscomplexobj(iq)
    assert np.abs(iq).max() <= 1.0 + 1e-6, "сигнал не должен выходить за полную шкалу"


def test_dataset_format_if_present(ml_modules, dataset_root):
    """Если датасет собран — проверяем, что он в формате YOLO."""
    d = dataset_root
    if not (d / "data.yaml").exists():
        pytest.skip("датасет не собран (make ml-dataset)")
    text = (d / "data.yaml").read_text(encoding="utf-8")
    assert "train:" in text and "val:" in text
    for split in ("train", "val"):
        imgs = list((d / "images" / split).glob("*.png"))
        if not imgs:
            continue
        lbl = d / "labels" / split / f"{imgs[0].stem}.txt"
        assert lbl.exists(), f"нет разметки для {imgs[0].name}"
        for line in lbl.read_text(encoding="utf-8").splitlines():
            parts = line.split()
            assert len(parts) == 5, f"неверный формат строки: {line!r}"
            cls = int(parts[0])
            assert 0 <= cls < len(CLASSES)
            for v in map(float, parts[1:]):
                assert 0.0 <= v <= 1.0, "координаты вне [0,1]"


# ===========================================================================
# Numpy-детектор (работает без TensorFlow)
# ===========================================================================
def test_numpy_detector_localizes_signal(ml_modules):
    """Numpy-детектор надёжно локализует сигнал во времени (REQ-ANA-03).

    Классификация модуляции здесь эвристическая — её проверяет обученная
    сеть, поэтому проверяем только геометрию рамок и попадание burst'а
    в интервал активности источника.
    """
    from ml import detector as det
    src = sc.SignalSource(offset_hz=-30e3, amplitude=0.4, modulation="cw",
                          t_start=0.002, t_stop=0.014)
    s = sc.SignalScene(fs=sc.DEFAULT_FS, n_samples=4096, sources=(src,),
                       snr_db=30.0, seed=3)
    img, _, _ = ds.render_scene(s)
    found, peak = det.numpy_detect(img, grid=16)

    assert isinstance(found, list) and found, "сигнал должен быть найден"
    assert 0.0 < peak <= 1.0
    for d in found:
        assert d.cls in CLASSES
        # все координаты — нормализованные [0, 1]
        assert 0.0 <= d.cx <= 1.0, f"cx вне [0,1]: {d.cx}"
        assert 0.0 <= d.cy <= 1.0, f"cy вне [0,1]: {d.cy}"
        assert 0.0 < d.w <= 1.0
        assert 0.0 < d.h <= 1.0

    # burst должен попасть в окно источника (с запасом на края)
    src_lo = src.t_start / s.duration_s
    src_hi = src.t_stop / s.duration_s
    assert any(src_lo - 0.15 <= d.cx <= src_hi + 0.15 for d in found), (
        f"окно источника [{src_lo:.2f}…{src_hi:.2f}] не покрыто: "
        + ", ".join(f"cx={d.cx:.2f}" for d in found))


def test_numpy_detector_finds_nothing_in_blank_image(ml_modules):
    """Пустая спектрограмма не должна давать ложных срабатываний."""
    from ml import detector as det
    found, peak = det.numpy_detect(np.zeros((64, 64)))
    assert found == [] and peak == 0.0


def test_iou_of_identical_boxes_is_one(ml_modules):
    from ml import detector as det
    b = (0.5, 0.5, 0.2, 0.1)
    assert det._iou(b, b) == pytest.approx(1.0, abs=1e-9)


def test_iou_of_disjoint_boxes_is_zero(ml_modules):
    from ml import detector as det
    assert det._iou((0.1, 0.1, 0.1, 0.1), (0.9, 0.9, 0.1, 0.1)) == 0.0


def test_nms_keeps_highest_scoring(ml_modules):
    """NMS оставляет лучший по score из перекрывающейся пары.

    Ящики 0 и 1 перекрываются (IoU ≈ 0.82 > 0.5); у 1 score 0.9,
    поэтому остаётся 1, а 0 подавляется. Ящик 2 не перекрывается ни с кем.
    """
    from ml import detector as det
    boxes = np.array([[0.5, 0.5, 0.2, 0.2],
                      [0.51, 0.51, 0.2, 0.2],
                      [0.9, 0.9, 0.2, 0.2]])
    scores = np.array([0.4, 0.9, 0.7])
    keep = sorted(det.nms(boxes, scores, iou_thresh=0.5).tolist())
    assert keep == [1, 2], f"ожидались ящики 1 и 2, получены {keep}"
    assert det._iou((0.5, 0.5, 0.2, 0.2), (0.51, 0.51, 0.2, 0.2)) > 0.5


# ===========================================================================
# Модель (только если TensorFlow есть)
# ===========================================================================
def test_model_builds_when_tensorflow_available(ml_modules):
    tf_test = pytest.importorskip("tensorflow", reason="TensorFlow не установлен")
    from ml import detector as det
    m = det.build_yolo_model(input_shape=(128, 128, 1), grid=16, n_classes=4)
    assert m is not None
    out = m.output_shape
    # ветка Dense + Reshape -> (None, grid, grid, 5 + n_classes)
    assert out == (None, 16, 16, 5 + 4), f"форма выхода {out}"
    assert m.count_params() > 0