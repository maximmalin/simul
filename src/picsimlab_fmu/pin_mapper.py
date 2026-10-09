"""pin_mapper.py — MCU pin mapping with configurable allocation.

Maps logical pin names (e.g. "GPIO_0", "ADC_0", "PWM_0") to physical MCU
ports (e.g. "PA0", "PE8", "GPIO14") depending on target board.

Supports multiple MCU boards with pin surplus (запас пинов) — default
allocation can be overridden per-board or per-project.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Optional


class PinType(IntEnum):
    DIGITAL = 0
    ANALOG = 1
    PWM = 2
    INTERRUPT = 3
    SPI_MOSI = 4
    SPI_MISO = 5
    SPI_SCK = 6
    UART_TX = 7
    UART_RX = 8


@dataclass
class PinAllocation:
    """Pin allocation for one MCU board."""

    name: str                       # e.g. "STM32_BluePill", "Arduino_Uno", "ESP32_DevKitC"
    digital_pins: int = 16          # total available digital GPIO pins
    analog_pins: int = 6            # total available analog input pins
    pwm_pins: int = 4               # total available PWM outputs
    spi_ports: int = 2              # SPI peripherals
    uart_ports: int = 2             # UART/USART peripherals
    i2c_ports: int = 1              # I2C peripherals

    # Reserved pins (not available for 2-port assignment)
    # e.g. {"PA13": "SWDIO", "PA14": "SWCLK"} for STM32
    reserved: dict[str, str] = field(default_factory=dict)

    # Pin capability map: which ports have which capabilities
    # e.g. {"PA0": ["ANALOG", "DIGITAL"], "PA5": ["PWM", "SPI_SCK"], ...}
    capability_map: dict[str, list[str]] = field(default_factory=dict)

    def is_reserved(self, port: str) -> bool:
        """Check if a physical port pin is reserved."""
        return port in self.reserved


class PinMapper:
    """
    Maps logical pin names to physical MCU port-pin combinations.

    Logical names: "GPIO_0", ..., "GPIO_N", "ADC_0", ..., "ADC_M",
    "PWM_0", ..., "PWM_K", "INT_0", ..., "INT_J"

    Physical names depend on board (from PinAllocation):
      - Arduino Uno:   "D0".."D13" digital, "A0".."A5" analog
      - STM32 BluePill: "PA0".."PA15", "PB0".."PB15"
      - ESP32:        "GPIO0".."GPIO39"
    """

    def __init__(self, allocation: PinAllocation) -> None:
        self.allocation = allocation

        # Bitmap of availability per pin type
        self._digital_avail: list[str] = []
        self._analog_avail: list[str] = []
        self._pwm_avail: list[str] = []
        self._int_avail: list[str] = []
        self._spi_avail: list[str] = []
        self._uart_avail: list[str] = []

        # Currently assigned: logical name -> physical port
        self._assigned: dict[str, str] = {}
        self._port_owner: dict[str, str] = {}  # physical port -> logical name

        self._enumerate_pins()

    def _enumerate_pins(self) -> None:
        """
        Enumerate available physical pins from allocation.
        Build availability lists excluding reserved pins.
        """
        # ---- Build pin names based on board name ----
        board = self.allocation.name.lower()

        digital = []
        analog = []
        pwm = []
        interrupt = []
        spi = []
        uart = []

        if "arduino" in board or "uno" in board:
            # Arduino Uno: D0..D13, A0..A5
            digital = [f"D{i}" for i in range(14)]
            analog = [f"A{i}" for i in range(6)]
            # PWM on D3, D5, D6, D9, D10, D11
            pwm_pins = ["D3", "D5", "D6", "D9", "D10", "D11"]
            # INT on D2, D3 (external interrupt)
            int_pins = ["D2", "D3"]
            # SPI on D10..D13
            spi_pins = ["D10", "D11", "D12", "D13"]
            # UART on D0, D1
            uart_pins = ["D0", "D1"]

        elif "stm32" in board or "bluepill" in board or "f103" in board:
            # STM32 Blue Pill (F103C8T6): PA0..PA15, PB0..PB15
            ports_a = [f"PA{i}" for i in range(16)]
            ports_b = [f"PB{i}" for i in range(16)]
            digital = ports_a + ports_b
            # ADC: PA0..PA7, PB0..PB1
            analog = ports_a[:8] + ports_b[:2]
            # PWM: PA0..PA3 (TIM2), PA6..PA7 (TIM3), PA8..PA11 (TIM1)
            pwm = ["PA0", "PA1", "PA2", "PA3", "PA6", "PA7", "PA8", "PA9", "PA10", "PA11"]
            # INT: all GPIO
            interrupt = digital
            # SPI: PA4..PA7 (SPI1), PB12..PB15 (SPI2)
            spi = ["PA4", "PA5", "PA6", "PA7", "PB12", "PB13", "PB14", "PB15"]
            # UART: PA9, PA10 (USART1), PB10, PB11 (USART2)
            uart = ["PA9", "PA10", "PB10", "PB11"]

        elif "esp32" in board:
            # ESP32 DevKitC: GPIO0..GPIO39
            digital = [f"GPIO{i}" for i in range(40)]
            # ADC: GPIO0, GPIO2, GPIO4, GPIO12-15, GPIO25-27, GPIO32-39
            analog = ["GPIO0", "GPIO2", "GPIO4", "GPIO12", "GPIO13", "GPIO14", "GPIO15",
                       "GPIO25", "GPIO26", "GPIO27", "GPIO32", "GPIO33", "GPIO34",
                       "GPIO35", "GPIO36", "GPIO37", "GPIO38", "GPIO39"]
            # PWM: LEDC channels on any GPIO
            pwm = digital
            # INT: all GPIO
            interrupt = digital
            # VSPI: GPIO18, GPIO19, GPIO23, GPIO5
            spi = ["GPIO18", "GPIO19", "GPIO23", "GPIO5"]
            # UART0: GPIO1, GPIO3, GPIO16, GPIO17
            uart = ["GPIO1", "GPIO3", "GPIO16", "GPIO17"]

        else:
            # Generic MCU
            digital = [f"GPIO_{i}" for i in range(self.allocation.digital_pins)]
            analog = [f"ADC_{i}" for i in range(self.allocation.analog_pins)]
            pwm = [f"PWM_{i}" for i in range(self.allocation.pwm_pins)]
            interrupt = digital
            spi = [f"SPI_{i}" for i in range(self.allocation.spi_ports * 4)]
            uart = [f"UART_{i}" for i in range(self.allocation.uart_ports * 2)]

        # Filter out reserved pins
        for pin_name in self.allocation.reserved:
            if pin_name in digital:
                digital.remove(pin_name)
            if pin_name in analog:
                analog.remove(pin_name)
            if pin_name in pwm:
                pwm.remove(pin_name)
            if pin_name in interrupt:
                interrupt.remove(pin_name)
            if pin_name in spi:
                spi.remove(pin_name)
            if pin_name in uart:
                uart.remove(pin_name)

        self._digital_avail = digital
        self._analog_avail = analog
        self._pwm_avail = pwm
        self._int_avail = interrupt
        self._spi_avail = spi
        self._uart_avail = uart

        # Also respect explicit capability_map if provided
        if self.allocation.capability_map:
            for port_str, caps in self.allocation.capability_map.items():
                if self.allocation.is_reserved(port_str):
                    continue
                port_caps = [str(c) for c in caps]
                if "DIGITAL" in port_caps and port_str not in digital:
                    digital.append(port_str)
                if "ANALOG" in port_caps and port_str not in analog:
                    analog.append(port_str)
                if "PWM" in port_caps and port_str not in pwm:
                    pwm.append(port_str)

                # Add capabilities
                for cap in port_caps:
                    cap_lower = cap.lower()
                    if cap_lower.startswith("spi_") and port_str not in spi:
                        spi.append(port_str)
                    if cap_lower.startswith("uart_") and port_str not in uart:
                        uart.append(port_str)
                    if cap_lower.startswith("i2c_"):
                        pass  # I2C not currently mapped

    def request_pin(self, pin_type: PinType, logical_name: Optional[str] = None) -> str | None:
        """
        Request a physical pin of the given type.

        Args:
            pin_type: type from `PinType` enum.
            logical_name: optional logical name (auto-generated if not given).

        Returns:
            Physical port string (e.g. "PA0", "D5", "GPIO14") or None
            if no pin of that type is available.
        """
        if logical_name is None:
            logical_name = f"{pin_type.name.lower()}_{len(self._assigned)}"

        # Return already-assigned
        if logical_name in self._assigned:
            return self._assigned[logical_name]

        # Pick appropriate pool
        pool: list[str] = []
        match pin_type:
            case PinType.DIGITAL:
                pool = self._digital_avail
            case PinType.ANALOG:
                pool = self._analog_avail
            case PinType.PWM:
                pool = self._pwm_avail
            case PinType.INTERRUPT:
                pool = self._int_avail
            case PinType.SPI_MOSI | PinType.SPI_MISO | PinType.SPI_SCK:
                pool = self._spi_avail
            case PinType.UART_TX | PinType.UART_RX:
                pool = self._uart_avail

        for port in pool:
            if port not in self._port_owner:
                self._assigned[logical_name] = port
                self._port_owner[port] = logical_name
                return port

        return None

    def unassign(self, logical_name: str) -> bool:
        """
        Release an assigned pin.

        Returns:
            True if pin was released, False if not found.
        """
        if logical_name not in self._assigned:
            return False

        port = self._assigned.pop(logical_name)
        self._port_owner.pop(port, None)
        return True

    def to_mcu_name(self, logical_name: str) -> str | None:
        """
        Get MCU pin name (physical) from logical wrapper name.

        Returns:
            Physical pin name or None if not assigned.
        """
        return self._assigned.get(logical_name)

    def from_mcu_name(self, physical_name: str) -> str | None:
        """
        Get wrapper logical name from MCU physical pin name.

        Returns:
            Logical name or None if pin not assigned.
        """
        return self._port_owner.get(physical_name)

    @property
    def assigned(self) -> dict[str, str]:
        """Return all assigned pins as dict."""
        return dict(self._assigned)


# ---- Built-in PinAllocation profiles ----

ARDUINO_UNO = PinAllocation(
    name="Arduino_Uno",
    digital_pins=14,
    analog_pins=6,
    pwm_pins=6,
    spi_ports=1,
    uart_ports=1,
    i2c_ports=1,
)

STM32_BLUE_PILL = PinAllocation(
    name="STM32_BluePill",
    digital_pins=32,
    analog_pins=10,
    pwm_pins=10,
    spi_ports=2,
    uart_ports=2,
    i2c_ports=1,
    reserved={"PA13": "SWDIO", "PA14": "SWCLK", "PB2": "BOOT1"},
)

ESP32_DEVKITC = PinAllocation(
    name="ESP32_DevKitC",
    digital_pins=40,
    analog_pins=18,
    pwm_pins=40,
    spi_ports=3,
    uart_ports=3,
    i2c_ports=1,
)


def get_allocation(board_name: str) -> PinAllocation:
    """
    Get a known PinAllocation profile.

    Args:
        board_name: board identifier, e.g. "arduino_uno", "stm32_bluepill", "esp32_devkitc".

    Returns:
        PinAllocation instance.

    Raises:
        ValueError for unknown board names.
    """
    name = board_name.lower().strip()
    match name:
        case "arduino_uno" | "arduino" | "uno":
            return ARDUINO_UNO
        case "stm32_bluepill" | "stm32" | "bluepill" | "f103" | "f103c8t6":
            return STM32_BLUE_PILL
        case "esp32_devkitc" | "esp32" | "esp32_wroom":
            return ESP32_DEVKITC
        case _:
            raise ValueError(
                f"Unknown MCU board: {board_name!r}. "
                f"Known boards: 'arduino_uno', 'stm32_bluepill', 'esp32_devkitc'."
            )
