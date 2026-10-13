"""
Krystal Radio Build Process Animation

Визуализирует последовательность этапов сборки проекта:
  netlist → spice → pcb → drawings → gnuradio → link-budget → direction → page

Использует matplotlib.animation для отрисовки линейного графа этапов.
Результат сохраняется как MP4 (если установлен ffmpeg) или GIF.
"""

from __future__ import annotations

import numpy as np

import matplotlib
matplotlib.use("Agg")  # non-interactive backend

import matplotlib.pyplot as plt
import matplotlib.animation as animation

# ---------------------------------------------------------------------------
# Constants — соответствие этапов Makefile
# ---------------------------------------------------------------------------
STAGES = [
    ("netlist",       "SKiDL → netlist"),
    ("spice",         "KiCad → ngspice deck"),
    ("pcb",           "tscircuit autoroute"),
    ("drawings",      "GOST + SVG"),
    ("gnuradio",      "co-simulation"),
    ("link-budget",   "Friis + шум"),
    ("direction",     "interferometer"),
    ("page",          "single-page HTML"),
]

N = len(STAGES)


# ---------------------------------------------------------------------------
# Helper: layout nodes on two columns
# ---------------------------------------------------------------------------
def _layout(stage_count: int):
    """Return node positions (x, y) for a simple two-column DAG."""
    n_left = stage_count // 2
    n_right = stage_count - n_left
    xs = []
    ys = []
    # left column
    for i in range(n_left):
        xs.append(0.1 + i * 0.25)
        ys.append(0.8 - i * 0.15)
    # right column
    for i in range(n_right):
        xs.append(0.5 + i * 0.25)
        ys.append(0.8 - i * 0.15)
    return list(zip(xs, ys))


# ---------------------------------------------------------------------------
def _make_frame(step: int, ax, texts, conns, pos, stage_count: int):
    """Draw DAG up to `step` (inclusive)."""
    ax.clear()

    # limits
    ax.set_xlim(-0.1, 1.0)
    ax.set_ylim(-0.1, 1.0)
    ax.set_aspect("equal")
    ax.axis("off")

    # draw all edges (full connectivity visible)
    for i, (fr, to) in enumerate(conns):
        x1, y1 = pos[fr]
        x2, y2 = pos[to]
        ax.plot([x1, x2], [y1, y2], "k-", linewidth=1, alpha=0.3)

    # draw nodes
    colors = [
        "#ffdb58" if i <= step else "#cccccc" for i in range(stage_count)
    ]
    for i, (label, _desc) in enumerate(texts):
        x, y = pos[i]
        color = colors[i]
        ax.scatter(x, y, s=800, color=color, edgecolor="k", zorder=3)
        ax.text(x, y, label, ha="center", va="center", fontsize=10, fontweight="bold", color="k")

    # highlight current step edge
    if step > 0:
        fr, to = conns[step - 1]
        x1, y1 = pos[fr]
        x2, y2 = pos[to]
        ax.plot([x1, x2], [y1, y2], "r-", linewidth=3)


# ---------------------------------------------------------------------------
def build_animation(
    output: str = "animations/build_process.mp4",
    fps: int = 1,
    dpi: int = 100,
) -> None:
    """Create the build-process animation and write to *output*."""
    stage_count = N
    pos = _layout(stage_count)

    fig, ax = plt.subplots(figsize=(stage_count * 1.5 + 2, 4), dpi=dpi)

    texts = [(lbl, txt) for lbl, txt in STAGES]
    # full mesh of edges for visibility
    conns = [(i, j) for i in range(stage_count) for j in range(stage_count) if i < j]

    # initial frame (nothing highlighted)
    init_step = [0]

    def update(frame: int):
        _make_frame(frame, ax, texts, conns, pos, stage_count)

    # we want frames 0 … stage_count-1 (highlight each step)
    n_frames = max(stage_count, 1)
    writer = (
        animation.FFMpegWriter(fps=fps) if output.endswith(".mp4") else animation.PillowWriter(fps=fps)
    )

    with writer.saving(fig, output, dpi):
        for f in range(n_frames):
            update(f)
            writer.grab_frame()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys

    out = sys.argv[1] if len(sys.argv) > 1 else "build_process.mp4"
    build_animation(output=out)
    print(f"Animation saved → {out}")