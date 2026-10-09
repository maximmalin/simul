#!/usr/bin/env python3
"""live_scope.py — Live co-sim oscilloscope: PicSimLab ↔ ngspice (hard lockstep).

Real-time handshake via POSIX shared memory + semaphore:
  PicSimLab sync (master clock, 1-5 ms timer event)
    → Thread-A: pinsl → write to SHM → semaphore_pic.release()
    → Thread-B: wait → set_source → run → query → write to SHM → semaphore_spice.release()
    → Thread-C: read SHM → deques → matplotlib FuncAnimation (30 FPS)

Scope layout (two panels, ngspice-style):
  Panel 1: MCU pins (PicSimLab digital signals, step-plot)
  Panel 2: ngspice nodes (analog transient waveforms)

Usage:
  python live_scope.py --board stm32_bluepill --pin PE0 --node V(LED_ANODE)
"""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation

from .cosim_bridge import CosimBridge, MAX_SAMPLES, parse_pinsl
from .ngspice_bridge import NgspiceBridge
from .pin_mapper import PinType
from .wrapper import PicSimLabFMU
import time


def _update_plot(
    frame,
    ax1: plt.Axes,
    ax2: plt.Axes,
    sim_data,
    spice_data,
    pin_names: list[str],
    node_names: list[str],
):
    """FuncAnimation callback — refresh both plots from deques."""
    # MCU pins (digital-style step plot)
    ax1.clear()
    ax1.set_title("MCU Pins (PicSimLab rcontrol)", fontsize=10, loc="left")
    ax1.grid(True, alpha=0.3, linestyle="--")
    ax1.set_ylabel("Voltage (V)")
    ax1.set_ylim(-0.2, 3.5)

    if sim_data:
        times = [d[0] for d in sim_data]
        for pin in pin_names:
            vals = [d[1].get(pin, 0.0) for d in sim_data]
            ax1.step(times, vals, where="post", label=pin, linewidth=1.2)
        ax1.legend(loc="upper right", fontsize=8)
        if times:
            ax1.set_xlim(times[0], times[-1])

    # ngspice nodes (analog transient)
    ax2.clear()
    ax2.set_title("ngspice Nodes (analog transient)", fontsize=10, loc="left")
    ax2.grid(True, alpha=0.3, linestyle="--")
    ax2.set_ylabel("Voltage (V)")
    ax2.set_xlabel("Time (s)")

    if spice_data:
        times = [d[0] for d in spice_data]
        for node in node_names:
            vals = [d[1].get(node, 0.0) for d in spice_data]
            ax2.plot(times, vals, label=node, linewidth=1.0)
        ax2.legend(loc="upper right", fontsize=8)
        if times:
            ax2.set_xlim(times[0], times[-1])

    return []


def run_live_scope(
    board: str,
    physical_pin: str,
    spice_nodes: list[str],
    netlist_path: str,
    host: str = "127.0.0.1",
    port: int = 5000,
    fps: int = 30,
):
    """Run live co-sim oscilloscope with hard lockstep via CosimBridge."""
    print("=== Live Co-sim Scope (hard lockstep via shared memory + semaphore) ===")
    print(f"  Board: {board}")
    print(f"  MCU pin: {physical_pin}")
    print(f"  ngspice nodes: {spice_nodes}")
    print(f"  Netlist: {netlist_path}")
    print(f"  Plot: {fps} FPS")
    print()

    # 1. Connect to PicSimLab rcontrol
    fmu = PicSimLabFMU(board=board, host=host, port=port)
    print("Connecting to PicSimLab rcontrol ...")
    fmu.connect()
    print("Connected.")

    # Map logical pin name from physical
    mapper = fmu.mapper
    if mapper is None:
        raise RuntimeError("Pin mapper not initialized")
    logical_pin = mapper.from_mcu_name(physical_pin)
    if logical_pin is None:
        mapper.request_pin(PinType.DIGITAL, f"SCOPE_{physical_pin}")
        logical_pin = mapper.from_mcu_name(physical_pin)
        if logical_pin is None:
            raise RuntimeError(f"Cannot map pin {physical_pin}")

    print(f"  Pin mapping: logical={logical_pin} → physical={physical_pin}")

    # 2. Start PicSimLab simulation
    print("Starting PicSimLab simulation ...")
    fmu.cmd_sim_start()
    time.sleep(0.2)  # let MCU boot

    # 3. Open ngspice bridge
    print(f"Opening ngspice with netlist: {netlist_path}")
    bridge = NgspiceBridge()
    bridge.send_command("set noopiter")
    bridge.send_command("set rshunt=1e8")

    # 4. Create and initialize CosimBridge
    cosim = CosimBridge()
    cosim.initialize(
        fmu=fmu,
        bridge=bridge,
        netlist_path=netlist_path,
        physical_pin=physical_pin,
        node_names=spice_nodes,
    )

    print(f"  Shared memory: {cosim._shm.name if cosim._shm else 'FAIL'}")
    print(f"  Semaphores: pic={cosim._sem_pic}, spice={cosim._sem_spice}")

    # 5. Start worker threads
    cosim.start()
    print("Worker threads started.")

    # 6. Matplotlib setup (ngspice-style two-panel scope)
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 7))
    fig.suptitle(
        f"Co-sim Scope: {board} pin={physical_pin} ↔ ngspice {', '.join(spice_nodes)}",
        fontsize=11,
    )
    fig.canvas.manager.set_window_title("PicSimLab ↔ ngspice Live Scope")

    anim = FuncAnimation(
        fig,
        _update_plot,
        fargs=(ax1, ax2, cosim.sim_data, cosim.spice_data, [physical_pin], spice_nodes),
        interval=1000 // fps,
        blit=False,
        cache_frame_data=False,
    )

    plt.tight_layout()
    plt.show(block=True)

    # Cleanup
    print("\nClosing scope...")
    cosim.stop()
    fmu.cmd_sim_stop()
    fmu.disconnect()
    print(f"Total lockstep cycles: {cosim.sync_counter}")
    print("Done.")


def main():
    parser = argparse.ArgumentParser(
        description="Live co-sim oscilloscope: PicSimLab ↔ ngspice (hard lockstep)",
    )
    parser.add_argument("--board", default="stm32_bluepill",
                        help="MCU board: stm32_bluepill, arduino_uno, esp32_devkitc")
    parser.add_argument("--pin", default="PE0",
                        help="MCU physical pin (e.g. PE0, D5, GPIO14)")
    parser.add_argument("--node", action="append", default=["V(out)"],
                        help="ngspice node (repeatable)")
    parser.add_argument("--netlist", default=None,
                        help="Path to .cir netlist")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--fps", type=int, default=30)

    args = parser.parse_args()

    netlist_path = args.netlist
    if netlist_path is None:
        here = Path(__file__).resolve().parent
        candidates = [
            Path.cwd() / "circuit_blink.cir",
            here / "examples" / "circuit_blink.cir",
            here / "circuit_blink.cir",
        ]
        for c in candidates:
            if c.exists():
                netlist_path = str(c)
                break

    if netlist_path is None or not Path(netlist_path).exists():
        print("ERROR: Netlist not found. Specify --netlist or place circuit_blink.cir")
        return

    run_live_scope(
        board=args.board,
        physical_pin=args.pin,
        spice_nodes=args.node,
        netlist_path=netlist_path,
        host=args.host,
        port=args.port,
        fps=args.fps,
    )


if __name__ == "__main__":
    main()
