"""
YOLO-конвейер распознавания радиосигналов (Krystal Radio).

    spectrogram.py — I/Q -> спектрограмма (numpy/Pillow, без TF)
    scene.py       — виртуальные сцены эфира и истинная разметка
    dataset.py     — генерация синтетического YOLO-датасета
    detector.py    — эталонный (numpy) детектор + метрики IoU/mAP
    model.py       — компактная YOLO-сеть на Keras/TensorFlow (опционально)
    train.py       — обучение детектора
    infer.py       — инференс + аннотированный PNG
    pipeline.py    — CLI: dataset -> train -> infer
"""

from __future__ import annotations

__all__ = ["CLASSES", "__version__"]
__version__ = "1.0.0"

CLASSES = ("cw", "am", "fm", "noise")
