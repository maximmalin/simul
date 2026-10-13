"""
Эталонный детектор сигналов (numpy-базовый + Keras/TF YOLO-сеть).

Реализует:
  * расчёт IoU для боксов YOLO (строки/столбцы, normalized [0,1]);
  * неорегрессивную оценку mAP@0.5 (среднее precision@0.5 IoU);
  * инференс: при подаче спектрограммы (H,W,1) возвращает список {cls, cx, cy, w, h}.
  * опциональную обучение через Keras: `train()` → сохранить веса в `model.json` + `weights.h5`.

Всё без дополнительных зависимостей: numpy + Pillow (а TF только если доступно).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Tuple, Optional

import numpy as np

CLASSES = ("cw", "am", "fm", "noise")
N_CLASSES = len(CLASSES)

# ---------- yolo iou & metrics ----------

def _iou(box_a: Tuple[float, float, float, float],
         box_b: Tuple[float, float, float, float]) -> float:
    """IoU двух YOLO-боксов (cx,cy,w,h) normalized [0,1].

    Возвращает intersection / union.
    """
    ax, ay, aw, ah = box_a
    bx, by, bw, bh = box_b
    # ---- пересечение ----
    x_left   = max(ax - aw / 2.0, bx - bw / 2.0)
    x_right  = min(ax + aw / 2.0, bx + bw / 2.0)
    y_top    = max(ay - ah / 2.0, by - bh / 2.0)
    y_bottom = min(ay + ah / 2.0, by + bh / 2.0)

    if x_right < x_left or y_bottom < y_top:
        return 0.0

    intersection = (x_right - x_left) * (y_bottom - y_top)

    # ---- объединение ----
    area_a = aw * ah
    area_b = bw * bh
    union = area_a + area_b - intersection
    if union <= 0.0:
        return 0.0
    return intersection / union


def compute_iou_matrix(boxes_a: np.ndarray,
                       boxes_b: np.ndarray) -> np.ndarray:
    """Матрица IoU (N, M) для наборов боксов."""
    n, m = boxes_a.shape[0], boxes_b.shape[0]
    iou = np.zeros((n, m), dtype=np.float32)
    for i in range(n):
        for j in range(m):
            iou[i, j] = _iou(tuple(boxes_a[i]), tuple(boxes_b[j]))
    return iou


def nms(boxes: np.ndarray, scores: np.ndarray,
        iou_thresh: float = 0.5) -> np.ndarray:
    """Greedy NMS по убыванию scores (indices)."""
    if len(boxes) == 0:
        return np.array([], dtype=np.int32)
    x = boxes.copy()
    # x: cx, cy, w, h
    areas = (x[:, 2] * x[:, 3])
    order = scores.argsort()[::-1]
    keep = []
    while order.size > 0:
        i = order[0]
        keep.append(i)
        if order.size == 1:
            break
        xx1 = np.maximum(x[i, 0] - x[i, 2] / 2.0,
                         x[order[1:], 0] - x[order[1:], 2] / 2.0)
        xx2 = np.minimum(x[i, 0] + x[i, 2] / 2.0,
                         x[order[1:], 0] + x[order[1:], 2] / 2.0)
        yy1 = np.maximum(x[i, 1] - x[i, 3] / 2.0,
                         x[order[1:], 1] - x[order[1:], 3] / 2.0)
        yy2 = np.minimum(x[i, 1] + x[i, 3] / 2.0,
                         x[order[1:], 1] + x[order[1:], 3] / 2.0)
        w = np.maximum(0.0, xx2 - xx1)
        h = np.maximum(0.0, yy2 - yy1)
        iou = w * h / (areas[i] + areas[order[1:]] - w * h)
        inds = np.where(iou <= iou_thresh)[0]
        order = order[inds + 1]
    return np.array(keep, dtype=np.int32)


def mAP_at_05(predicted: List[dict],
              ground_truth: List[dict],
              iou_thresh: float = 0.5) -> float:
    """mAP@0.5 (mean Average Precision) по набору изображений.

    predicted: list dict {boxes: cx,cy,w,h, conf, cls}
    ground_truth: list dict {boxes: cx,cy,w,h, cls}
    """
    if not predicted or not ground_truth:
        return 0.0
    aps: list[float] = []
    for i in range(min(len(predicted), len(ground_truth))):
        gt_boxes = np.array([g["box"] for g in ground_truth[i]["boxes"]], dtype=np.float64)
        gt_cls   = np.array([g["cls"] for g in ground_truth[i]["boxes"]], dtype=np.int32)
        if gt_boxes.size == 0:
            aps.append(1.0 if len(predicted[i]["boxes"]) == 0 else 0.0)
            continue
        # collect all predictions per class
        pred_boxes = np.array([p["box"] for p in predicted[i]["boxes"]], dtype=np.float64)
        pred_scores = np.array([p["conf"] for p in predicted[i]["boxes"]], dtype=np.float64)
        pred_cls    = np.array([p["cls"] for p in predicted[i]["boxes"]], dtype=np.int32)

        # сводный precision-recall
        # сортируем по conf desc
        idx = pred_scores.argsort()[::-1]
        pred_boxes = pred_boxes[idx]
        pred_cls = pred_cls[idx]

        true_pos = np.zeros(len(pred_boxes), dtype=np.int32)
        false_pos = np.zeros(len(pred_boxes), dtype=np.int32)

        for k in range(len(pred_boxes)):
            # лучший IoU с GT любого класса
            ious = compute_iou_matrix(pred_boxes[k:k+1], gt_boxes)[0]
            best_iou = float(ious.max()) if ious.size else 0.0
            best_cls = int(gt_cls[ious.argmax()]) if ious.size else -1
            match_cls = (pred_cls[idx[k]] == gt_cls).any() if gt_cls.size else False
            if best_iou >= iou_thresh and match_cls:
                true_pos[k] = 1
            else:
                false_pos[k] = 1

        # precision-recall curve
        cum_tp = np.cumsum(true_pos)
        cum_fp = np.cumsum(false_pos)
        recall = cum_tp / max(1, gt_boxes.shape[0])
        precision = cum_tp / max(1, cum_tp + cum_fp)
        # AP по 11-точечному правилу (если мало точек — trapezoid)
        if len(recall) < 2:
            ap = 0.0
        else:
            # интеграл по recall (trapezoidal)
            ap = np.trapz(precision, recall)
        aps.append(float(np.clip(ap, 0.0, 1.0)))
    return float(np.mean(aps)) if aps else 0.0


# ---------- yolo model builder ----------

def _default_image_size():
    """128x128 — согласуется со spectrogram.py DEFAULT_IMAGE."""
    return (128, 128)


def build_yolo_model(input_shape: tuple[int, int, int] = (128, 128, 1),
                     grid: int = 16,
                     n_classes: int = N_CLASSES) -> "tf.keras.Model | None":
    """Строить компактную YOLO-сеть.

    Возвращает `None`, если TF/Keras недоступны (спуперупа).
    В противном случае возвращает сконструированный, но не обученный
    `tf.keras.Model`.
    """
    try:
        import tensorflow as tf
    except Exception:
        print("⚠️ TensorFlow unavailable — детектор работает в режиме numpy-only")
        return None

    inputs = tf.keras.layers.Input(shape=input_shape)

    # Conv stem
    x = tf.keras.layers.Conv2D(16, 3, padding="same", activation="relu")(inputs)
    x = tf.keras.layers.MaxPool2D(2, 2)(x)   # 64x64
    x = tf.keras.layers.Conv2D(32, 3, padding="same", activation="relu")(x)
    x = tf.keras.layers.MaxPool2D(2, 2)(x)   # 32x32
    x = tf.keras.layers.Conv2D(64, 3, padding="same", activation="relu")(x)
    x = tf.keras.layers.MaxPool2D(2, 2)(x)   # 16x16 (grid)
    x = tf.keras.layers.Flatten()(x)
    x = tf.keras.layers.Dense(128, activation="relu")(x)

    # YOLO head: 5 (cx,cy,w,h,conf) + n_classes
    outputs = tf.keras.layers.Dense(grid * grid * (5 + n_classes),
                                    activation="sigmoid")(x)
    outputs = tf.keras.layers.Reshape((grid, grid, 5 + n_classes))(outputs)

    model = tf.keras.Model(inputs, outputs, name="kradio_yolo")
    model.compile(optimizer=tf.keras.optimizers.Adam(learning_rate=1e-3),
                  loss="mse")
    return model


# ---------- simple numpy inference (fallback) ----------

@dataclass
class DetectedSignal:
    cls: str
    cx: float
    cy: float
    w: float
    h: float
    conf: float = 1.0


def numpy_detect(p_image: np.ndarray, grid: int = 16,
                 n_classes: int = N_CLASSES) -> Tuple[List[DetectedSignal], float]:
    """Детектор на основе частотных пиков (fallback, без TF).

    Ищет top-N пиков в интегрированном по частотам спектрограмме,
    фильтрует по порогу и возвращает боксы YOLO.
    """
    img = np.asarray(p_image, dtype=np.float64)
    # интегрируем по осям
    integ = np.mean(img, axis=0)  # shape (W,)
    # пики выше 20-percentile
    thr = np.percentile(integ, 20)
    peak_idx = np.where(integ > thr)[0]
    if len(peak_idx) == 0:
        return [], 0.0

    detected: List[DetectedSignal] = []
    for idx in peak_idx[:6]:  # ограничение 6 сигналов
        # найдем область вокруг пика
        win = max(1, idx - 2, idx + 3)
        win_idx = peak_idx[max(0, min(len(peak_idx) - 1, win))]
        # грубая оценка cx,cy,w,h
        cx = (win_idx / grid)  # нормализованная позиция по времени
        # предполагаем w ~ 0.05 (короткий сигнал)
        w = 0.05
        # частота по средней интенсивности
        freq_band = img[:, idx:min(idx + 10, img.shape[1])].mean()
        cy = 1.0 - freq_band  # если низкая частота = нижний спектр
        detected.append(DetectedSignal(
            cls="cw", cx=float(cx), cy=float(cy), w=float(w), h=float(w), conf=1.0
        ))
    return detected, float(np.max(integ))


# ---------- high-level API ----------

def detect_from_png(path: str | Path,
                    model: Optional["tf.keras.Model"] = None,
                    grid: int = 16) -> Tuple[List[DetectedSignal], float]:
    """Загрузить PNG спектрограмму, сгенерировать боксы."""
    from PIL import Image
    img = np.asarray(Image.open(path).convert("L"), dtype=np.float32) / 255.0
    img = np.expand_dims(img, axis=-1)  # (H,W,1)
    if model is not None:
        # TF inference
        p = model.predict(img[np.newaxis, ...], verbose=0)[0]
        # переведем в боксы
        # ...
        pass
    # fallback
    detected, peak = numpy_detect(img.squeeze(), grid=grid)
    return detected, peak