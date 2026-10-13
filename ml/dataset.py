"""
Синтетический датасет для YOLO-детектора сигналов.

Датасет целиком генерируется из физической модели эфира (`ml/scene.py`),
поэтому не требует ни ручной разметки, ни загрузки из сети. Истинные
боксы считаются аналитически из параметров источников, что даёт точную
разметку «бесплатно».

Формат на диске совместим с YOLO:

    <root>/images/<split>/<name>.png     — спектрограмма (L, 128x128)
    <root>/labels/<split>/<name>.txt     — строки "<class> cx cy w h"
    <root>/data.yaml                     — имена классов, split, размер
    <root>/scenes.json                   — параметры источников (для отчёта)
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Iterable

import numpy as np

from . import spectrogram as sp
from .scene import DEFAULT_FS, SignalScene, SignalSource

CLASSES = ("cw", "am", "fm", "noise")
GRID = 16
DEFAULT_IMAGE = sp.DEFAULT_IMAGE


def random_scene(rng: np.random.Generator, fs: float = DEFAULT_FS,
                 n_samples: int = 8256, max_sources: int = 4,
                 snr_db: float | None = None) -> SignalScene:
    """Случайная сцена: 1..max_sources источников без сильных перекрытий."""
    n = int(rng.integers(1, max_sources + 1))
    duration = n_samples / fs
    res = fs / 128.0

    occupied: list[tuple[float, float]] = []
    sources: list[SignalSource] = []

    for _ in range(n):
        modulation = str(rng.choice(CLASSES))
        mod_rate = float(rng.uniform(2_000.0, 14_000.0))
        mod_index = float(rng.uniform(1.0, 6.0))
        bw = float(rng.uniform(10_000.0, 55_000.0))
        tmp = SignalSource(offset_hz=0.0, modulation=modulation,
                           mod_rate_hz=mod_rate, mod_index=mod_index,
                           bandwidth_hz=bw)
        half = tmp.occupied_bandwidth_hz(res) / 2.0
        # Ищем расстройку, не налезающую на уже занятые полосы.
        offset = None
        for _try in range(12):
            cand = float(rng.uniform(-0.42, 0.42) * fs)
            if all(abs(cand - o) > (half + h + 3 * res)
                   for o, h in occupied):
                offset = cand
                break
        if offset is None:
            continue
        occupied.append((offset, half))

        length = float(rng.uniform(0.30, 1.0)) * duration
        t0 = float(rng.uniform(0.0, duration - length))
        sources.append(SignalSource(
            offset_hz=offset,
            amplitude=float(rng.uniform(0.06, 0.35)),
            modulation=modulation,
            mod_index=mod_index,
            mod_rate_hz=mod_rate,
            t_start=t0,
            t_stop=t0 + length,
            bandwidth_hz=bw,
        ))

    return SignalScene(
        fs=fs,
        n_samples=n_samples,
        sources=tuple(sources),
        snr_db=float(rng.uniform(18.0, 30.0) if snr_db is None else snr_db),
        seed=int(rng.integers(0, 2 ** 31 - 1)),
    )


def render_scene(scene: SignalScene, image: tuple[int, int] = DEFAULT_IMAGE
                 ) -> tuple[np.ndarray, sp.SpecMapper, list[tuple[float, float, float, float]]]:
    """Сцена -> (картинка [0,1] HxW, mapper, истинные боксы)."""
    iq = scene.generate()
    p_db = sp.spectrogram_db(iq)
    mapper = sp.mapper_for(scene.fs, scene.n_samples, image=image)
    img = sp.to_image(p_db, image)
    return img, mapper, scene.boxes(mapper)


def yolo_targets(boxes: Iterable[tuple[float, float, float, float]],
                 classes: Iterable[int], grid: int = GRID,
                 n_classes: int = len(CLASSES)) -> np.ndarray:
    """Собрать целевой тензор (grid, grid, 5 + n_classes)."""
    t = np.zeros((grid, grid, 5 + n_classes), dtype=np.float32)
    for (cx, cy, w, h), c in zip(boxes, classes):
        row = min(grid - 1, max(0, int(cy * grid)))
        col = min(grid - 1, max(0, int(cx * grid)))
        if t[row, col, 4] > 0:      # ячейка уже занята — пропускаем
            continue
        t[row, col, 0:4] = (cx, cy, w, h)
        t[row, col, 4] = 1.0
        t[row, col, 5 + int(c)] = 1.0
    return t


def generate_dataset(root: str | Path, n_train: int = 160, n_val: int = 40,
                     image: tuple[int, int] = DEFAULT_IMAGE,
                     seed: int = 1234, grid: int = GRID) -> dict:
    """Сгенерировать датасет на диск. Возвращает манифест."""
    root = Path(root)
    rng = np.random.default_rng(seed)
    scenes_meta: dict[str, list] = {}
    counts = {"train": n_train, "val": n_val}

    for split, count in counts.items():
        (root / "images" / split).mkdir(parents=True, exist_ok=True)
        (root / "labels" / split).mkdir(parents=True, exist_ok=True)
        scenes_meta[split] = []
        for i in range(count):
            scene = random_scene(rng)
            img, mapper, boxes = render_scene(scene, image)
            classes = scene.classes()
            name = f"{split}_{i:05d}"
            sp.save_png(root / "images" / split / f"{name}.png", img, rgb=False)
            lines = []
            for (cx, cy, w, h), c in zip(boxes, classes):
                lines.append(f"{c} {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}")
            (root / "labels" / split / f"{name}.txt").write_text(
                "\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
            scenes_meta[split].append({
                "name": name,
                "snr_db": scene.snr_db,
                "sources": [
                    {"modulation": s.modulation,
                     "offset_hz": s.offset_hz,
                     "bandwidth_hz": s.occupied_bandwidth_hz(
                         mapper.freq_resolution_hz)}
                    for s in scene.sources
                ],
                "boxes": boxes,
                "classes": classes,
            })

    (root / "data.yaml").write_text(data_yaml_text(root, image, grid),
                                    encoding="utf-8")
    (root / "scenes.json").write_text(
        json.dumps(scenes_meta, indent=1, ensure_ascii=False), encoding="utf-8")
    return {
        "root": str(root),
        "classes": list(CLASSES),
        "image": list(image),
        "grid": grid,
        "n_train": n_train,
        "n_val": n_val,
        "fs_hz": DEFAULT_FS,
    }


def load_split(root: str | Path, split: str,
               image: tuple[int, int] = DEFAULT_IMAGE):
    """Загрузить split как (X float32 (N,H,W,1), labels — список боксов)."""
    from PIL import Image

    root = Path(root)
    img_dir = root / "images" / split
    lbl_dir = root / "labels" / split
    names = sorted(p.stem for p in img_dir.glob("*.png"))
    X = np.zeros((len(names), image[0], image[1], 1), dtype=np.float32)
    labels: list[list[tuple[int, float, float, float, float]]] = []
    for i, name in enumerate(names):
        arr = np.asarray(Image.open(img_dir / f"{name}.png").convert("L"),
                         dtype=np.float32) / 255.0
        X[i, :, :, 0] = _resize(arr, image)
        items: list[tuple[int, float, float, float, float]] = []
        txt = lbl_dir / f"{name}.txt"
        if txt.exists():
            for line in txt.read_text(encoding="utf-8").splitlines():
                p = line.split()
                if len(p) == 5:
                    items.append((int(p[0]), *map(float, p[1:])))
        labels.append(items)
    return X, labels


def _resize(a: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    """Ресайз grayscale [0,1] до size (линейная интерполяция)."""
    h, w = size
    if a.shape == (h, w):
        return a
    img = sp._interp_axis(a.astype(np.float64), h, axis=0)
    return sp._interp_axis(img, w, axis=1)


def data_yaml_text(root: str | Path, image: tuple[int, int] = DEFAULT_IMAGE,
                   grid: int = GRID) -> str:
    """Текст data.yaml (совместим с ultralytics, если он появится)."""
    root = Path(root)
    return (
        f"# Krystal Radio — synthetic signal dataset (generated)\n"
        f"path: {root.resolve()}\n"
        f"train: images/train\n"
        f"val: images/val\n"
        f"nc: {len(CLASSES)}\n"
        f"names: {list(CLASSES)!r}\n"
        f"img_size: {list(image)}\n"
        f"grid: {grid}\n"
    )
