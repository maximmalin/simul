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
            {"type": "LDO", "model": "AMS1117-3.3", "role": "LPO", "description": "3.3V LDO regulator"} 
        ],
        "inputs": ["VIN"],
        "outputs": ["VOUT"],
        "notes": "Power supply from battery"
    },
    {
        "id": "U_MIX1",
        "name": "Mixer",
        "components": [
            {"type": "MIXER", "model": "LT5560", "role": "Mixer", "description": "Double-balanced mixer"} 
        ],
        "inputs": ["IN+", "IN-"],
        "outputs": ["I_OUT", "Q_OUT"],
        "notes": "Zero-IF mixer for SDR signal conversion"
    },
    {
        "id": "U1",
        "name": "MCU",
        "components": [
            {"type": "MCU", "model": "STM32F103C8T6", "role": "MCU", "description": "STM32 blue-pill microcontroller"} 
        ],
        "inputs": ["PA0", "PA1"],
        "outputs": ["PA6", "PA7"],
        "notes": "STM32 blue-pill, USB I/Q output"
    },
    {
        "id": "U2",
        "name": "Amplifier",
        "components": [
            {"type": "OPAMP", "model": "OPA2134", "role": "Amplifier", "description": "Dual op-amp for signal conditioning"} 
        ],
        "inputs": ["IN+", "IN-"],
        "outputs": ["OUT+", "OUT-"],
        "notes": "Signal conditioning and filtering"
    }
]

# Net definitions connecting all blocks
NETS_DEFINITION = {
    "ant_in": {
        "name": "ANT",
        "nodes": ["IN+", "IN-"],
        "description": "Antenna input"
    },
    "lna1_in": {
        "name": "LNA1_IN",
        "nodes": ["L_LNA1", "Q_LNA1"],
        "description": "LNA 1 input"
    },
    "lna1_out": {
        "name": "LNA1_OUT",
        "nodes": ["R_LNA1", "C_LNA1"],
        "description": "LNA 1 output"
    },
    "qc_out": {
        "name": "QC_OUT",
        "nodes": ["Q_LNA1", "R_LNA1"],
        "description": "Quarter-wave coupler output"
    },
    "filter_in": {
        "name": "FILTER_IN",
        "nodes": ["C_LNA1", "R_LNA1"],
        "description": "Filter input"
    },
    "ldo_in": {
        "name": "LDO_IN",
        "nodes": ["R_LPO1"],
        "description": "LDO regulator input"
    },
    "ldo_out": {
        "name": "LDO_OUT",
        "nodes": ["R_LPO1"],
        "description": "LDO regulator output"
    },
    "mixer_in": {
        "name": "MIXER_IN",
        "nodes": ["IN+", "IN-", "U_MIX1"],
        "description": "Mixer input"
    },
    "mixer_i_out": {
        "name": "I_OUT",
        "nodes": ["U_MIX1"],
        "description": "I channel output"
    },
    "mixer_q_out": {
        "name": "Q_OUT",
        "nodes": ["U_MIX1"],
        "description": "Q channel output"
    },
    "mcu_i": {
        "name": "PA6",
        "nodes": ["U1"],
        "description": "STM32 PA6 pin"
    },
    "mcu_q": {
        "name": "PA7",
        "nodes": ["U1"],
        "description": "STM32 PA7 pin"
    },
    "mcu_adc0": {
        "name": "PA0",
        "nodes": ["U1"],
        "description": "STM32 PA0 pin"
    },
    "mcu_adc1": {
        "name": "PA1",
        "nodes": ["U1"],
        "description": "STM32 PA1 pin"
    },
    "gnd": {
        "name": "GND",
        "nodes": ["VSS", "PGND"],
        "description": "Ground"
    },
    "vcc": {
        "name": "VCC",
        "nodes": ["VDD"],
        "description": "3.3V supply"
    },
    "vcc_3v3": {
        "name": "3V3",
        "nodes": ["AMS1117-3.3", "VOUT"],
        "description": "3.3V regulated supply"
    }
}

def get_blocks():
    """Return the block definitions."""
    return BLOCKS

def get_nets():
    """Return the net definitions."""
    return NETS_DEFINITION

def get_block_by_id(block_id):
    """Get a block definition by its ID."""
    for block in BLOCKS:
        if block["id"] == block_id:
            return block
    return None

def get_net_by_name(net_name):
    """Get a net definition by name."""
    for net in NETS_DEFINITION.values():
        if net["name"] == net_name:
            return net
    return None

if __name__ == "__main__":
    print("SDR Receiver Blocks:")
    for block in BLOCKS:
        print(f"  - {block['id']}: {block['name']} ({len(block['components'])} components)")
    print(f"\nNets: {len(NETS_DEFINITION)} defined")
    print(f"Total parts: {sum(len(block['components']) for block in BLOCKS)}")