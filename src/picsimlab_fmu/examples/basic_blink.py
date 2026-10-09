#!/usr/bin/env python3
"""examples/basic_blink.py — STM32 GPIO toggle → ngspice LED driver.

Demonstrates the minimal co-simulation loop:
  • PicSimLab runs STM32 firmware that toggles a GPIO in a loop
  • wrapper reads the GPIO pin state at each sync
  • pushes the logic level to an ngspice behavioral source (B-element)
  • ngspice simulates an LED + resistor driven by that logic level

The B-element in the .cir netlist is named B_MCU_PE0 (example).

Prerequisites:
  1. PicSimLab running with rcontrol enabled (port 5000)
  2. STM32 Blue Pill board loaded with a .hex that toggles PE0
  3. circuit.cir placed in the same directory as this script
"""

import sys
from pathlib import Path

# Make src/ importable without installing
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from picsimlab_fmu import PicSimLabFMU, PinType, NgspiceBridge


def main():
    print("=== PicSimLab + ngspice co-simulation: basic blink ===\n")

    # 1. Circuit netlist path (B-element "B_MCU_PE0" is driven by the wrapper)
    cir_path = Path(__file__).resolve().parent / "circuit_blink.cir"
    if not cir_path.exists():
        print(f"ERROR: netlist {cir_path} not found. Create it from the template below.")
        print()
        print("* B-link LED driver — circuit_blink.cir")
        print("Vcc VCC 0 DC 3.3")
        print("R1 VCC LED_ANODE 330")
        print("D1 LED_ANODE LED_CATHODE D_1N4148")
        print("B_MCU_PE0 LED_CATHODE 0 V=0")
        print(".model D_1N4148 D(IS=2.52e-9 RS=0.568 N=1.752 CJO=4p M=0.4 TT=20n)")
        print(".tran 1u 10m")
        print(".end")
        return

    # 2. Connect to PicSimLab rcontrol
    fmu = PicSimLabFMU(board="stm32_bluepill", host="127.0.0.1", port=5000)
    print("Connecting to PicSimLab rcontrol ...")
    fmu.connect()
    print("Connected.")

    # Show supported boards (sanity check)
    print("\n-- Supported boards --")
    print(fmu.cmd_blist())

    # 3. Request pin: PE0 as digital output
    print("\n-- Pin allocation --")
    allocations = fmu.request_pins([
        (PinType.DIGITAL, "MC"),
    ])
    print(f"Allocation: {allocations}")

    # 4. Start PicSimLab MCU sim
    print("\nStarting PicSimLab simulation ...")
    fmu.cmd_sim_start()

    # 5. Load netlist into ngspice
    print(f"Loading netlist {cir_path} ...")
    with NgspiceBridge() as ng:
        ng.load_netlist(str(cir_path))
        print("Netlist loaded.")

        # Run 1000 steps of 1 µs each (total 10 ms)
        print("\n=== Running 1000 co-sim steps (1 µs each) ===")
        results = fmu.run(
            ngspice_bridge=ng,
            tstep=1e-6,
            steps=1000,
            progress_cb=lambda i, n, r: (
                print(f"  step {i}/{n}: PE0={r['pin_voltages'].get('PE0', 0):.2f} V"
                      f"  elapsed={r.get('elapsed_seconds', 0):.4f}s")
                if i % 100 == 0 else None
            ),
        )

        # Print summary
        pin_voltages = [r["pin_voltages"].get("PE0", 0.0) for r in results]
        elapsed = [r["elapsed_seconds"] for r in results]
        print(f"\n=== Summary ===")
        print(f"Steps: {len(results)}")
        print(f"PE0 min/max voltage: {min(pin_voltages):.3f} / {max(pin_voltages):.3f} V")
        print(f"Avg step elapsed: {sum(elapsed)/len(elapsed):.4f}s")
        print(f"Total elapsed: {sum(elapsed):.2f}s")

    # 6. Stop PicSimLab
    fmu.cmd_sim_stop()
    fmu.disconnect()
    print("\nDone.")


if __name__ == "__main__":
    main()
