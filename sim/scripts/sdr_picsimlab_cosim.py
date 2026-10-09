#!/usr/bin/env python3
"""sdr_ngspice_bridge.py - Adapted ngspice bridge for SDR co-simulation

Run co-simulation between PicSimLab (STM32) and ngspice for the SDR receiver.
"""

import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from picsimlab_fmu import PicSimLabFMU, PinType, NgspiceBridge


def run_cosim(
    netlist: Path = None,
    port: int = 5000,
    steps: int = 500,
    tstep: float = 5e-6
):
    if netlist is None:
        netlist = Path(__file__).resolve().parent.parent / "netlists" / "sdr_simplified.cir"
    
    print(f"=== SDR Co-Simulation ===")
    print(f"Netlist: {netlist}")
    print(f"PicSimLab port: {port}")
    print(f"Steps: {steps}, tstep: {tstep}s\n")
    
    # Connect to PicSimLab
    fmu = PicSimLabFMU(board="stm32_bluepill", host="127.0.0.1", port=port)
    try:
        fmu.connect()
        print("✓ Connected to PicSimLab")
    except Exception as e:
        print(f"✗ Failed to connect: {e}")
        print("\nStart PicSimLab with Remote Control on port 5000")
        return 1
    
    # Allocate pins
    fmu.request_pins([
        (PinType.ANALOG, "ADC_I"),
        (PinType.ANALOG, "ADC_Q"),
    ])
    print("✓ Pins allocated (PA0/PA1 for ADC)")
    
    # Start simulation
    fmu.cmd_sim_start()
    print("✓ PicSimLab simulation started")
    
    # Run co-simulation
    with NgspiceBridge() as ng:
        if ng.load_netlist(str(netlist)):
            print("✓ ngspice loaded netlist")
        else:
            print("✗ Failed to load netlist")
            return 1
        
        print("\n--- Running co-simulation ---")
        results = fmu.run(
            ngspice_bridge=ng,
            tstep=tstep,
            steps=steps,
            progress_cb=lambda i, n, r: (
                print(f"step {i+1}/{n}: ADC_I={r['pin_voltages'].get('PA0', 0):.4f}V "
                      f"ADC_Q={r['pin_voltages'].get('PA1', 0):.4f}V")
                if (i+1) % 50 == 0 else None
            )
        )
        
        total = sum(r['elapsed_seconds'] for r in results)
        print(f"\n✓ Completed {len(results)} steps in {total:.2f}s")
        print(f"Average: {total/len(results)*1000:.2f}ms/step")
    
    fmu.cmd_sim_stop()
    fmu.disconnect()
    print("✓ Stopped and disconnected")
    return 0


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description='SDR co-simulation')
    parser.add_argument('--netlist', type=str, help='Path to netlist')
    parser.add_argument('--port', type=int, default=5000, help='PicSimLab rcontrol port')
    parser.add_argument('--steps', type=int, default=200, help='Number of steps')
    parser.add_argument('--tstep', type=float, default=5e-6, help='Step size (s)')
    args = parser.parse_args()
    
    netlist = Path(args.netlist) if args.netlist else None
    sys.exit(run_cosim(netlist, args.port, args.steps, args.tstep))
