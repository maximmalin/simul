"""PicSimLab FMU wrapper package.

Co-simulation bridge between PicSimLab MCU firmware and ngspice analog circuits.
"""

__version__ = "0.1.0"

from .wrapper import PicSimLabFMU, PicSimLabFMUError
from .ngspice_bridge import NgspiceBridge, NgspiceBridgeError
from .cosim_bridge import CosimBridge, CosimHandshakeError, parse_pinsl
from .pin_mapper import (
    PinMapper,
    PinType,
    PinAllocation,
    get_allocation,
    ARDUINO_UNO,
    STM32_BLUE_PILL,
    ESP32_DEVKITC,
)

__all__ = [
    "PicSimLabFMU",
    "PicSimLabFMUError",
    "NgspiceBridge",
    "NgspiceBridgeError",
    "CosimBridge",
    "CosimHandshakeError",
    "parse_pinsl",
    "PinMapper",
    "PinType",
    "PinAllocation",
    "get_allocation",
    "ARDUINO_UNO",
    "STM32_BLUE_PILL",
    "ESP32_DEVKITC",
]
