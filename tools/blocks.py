"""SDR Direct-Conversion Receiver Specification

Blocks for zero-IF SDR receiver (wideband 0.5-30 MHz)
"""
import os

# Block definitions for the SDR receiver
BLOCKS = [
    {
        "id": "L_LNA1",
        "name": "Low Noise Amplifier 1",
        "components": [
            {"type": "LNA", "model": "2N7002", "role": "LNA", "description": "First low-noise amplifier"} 
        ],
        "inputs": ["IN+", "IN-"],
        "outputs": ["OUT+", "OUT-"],
        "notes": "High-impedance input, low-noise FET amplifier"
    },
    {
        "id": "Q_LNA1",
        "name": "Quarter-Wave Coupler",
        "components": [
            {"type": "QWC", "model": "LPJ-1", "role": "coupler", "description": "Quarter-wave coupler for signal splitting"}
        ],
        "inputs": ["IN+", "IN-"],
        "outputs": ["OUT+", "OUT-"],
        "notes": "Splits signal between detector and mixer"
    },
    {
        "id": "R_LNA1",
        "name": "Low Noise Amplifier 2",
        "components": [
            {"type": "LNA", "model": "2N7002", "role": "LNA", "description": "Second LNA for gain enhancement"}
        ],
        "inputs": ["IN+", "IN-"],
        "outputs": ["OUT+", "OUT-"],
        "notes": "Provides additional gain before mixer"
    },
    {
        "id": "C_LNA1",
        "name": "LC Bandstop Filter",
        "components": [
            {"type": "LC", "model": "C1+L1", "role": "filter", "description": "Bandstop filter at 1 MHz"}
        ],
        "inputs": ["IN+", "IN-"],
        "outputs": ["OUT+", "OUT-"],
        "notes": "Removes interference near carrier frequency"
    },
    {
        "id": "R_LPO1",
        "name": "Low-Power Power Supply",
        "components": [
            {"type": "LDO", "model": "AMS1117-3.3", "role": "power", "description": "3.3V LDO for DC power"}
        ],
        "inputs": ["IN+", "IN-"],
        "outputs": ["VCC"],
        "notes": "Stabilizes voltage for sensitive stages"
    },
    {
        "id": "U_MIX1",
        "name": "Mixer (LT5560)",
        "components": [
            {"type": "Mixer", "model": "LT5560", "role": "mixer", "description": "Direct conversion mixer for IF downshift"}
        ],
        "inputs": ["IN+", "IN-"],
        "outputs": ["IF+", "IF-"],
        "notes": "Direct conversion mixer for HF band"
    },
    {
        "id": "U1",
        "name": "Microcontroller (STM32F103)",
        "components": [
            {"type": "MCU", "model": "STM32F103C8T6", "role": "controller", "description": "Cortex-M3 microcontroller"}
        ],
        "inputs": ["U_MIX1/IF+", "U_MIX1/IF-"],
        "outputs": ["U1_OUT"],
        "notes": "Handles modulation, timing, and communication"
    },
    {
        "id": "U2",
        "name": "OPA2134 Audio Amplifier",
        "components": [
            {"type": "OpAmp", "model": "OPA2134", "role": "audio", "description": "High-power audio amplifier"}
        ],
        "inputs": ["U1_OUT"],
        "outputs": ["OUTPUT"],
        "notes": "Drives speaker output"
    }
]

def get_block(id):
    """Return block info by ID"""
    for blk in BLOCKS:
        if blk["id"] == id:
            return blk
    return None

def print_blocks():
    """Print all blocks"""
    for blk in BLOCKS:
        print(f"Block: {blk["id"]} - {blk["name"]}")
        print(f"  Inputs: {blk["inputs"]}")
        print(f"  Outputs: {blk["outputs"]}")
        print()

if __name__ == "__main__":
    print_blocks()
