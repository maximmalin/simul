"""wrapper.py — PicSimLab FMU co-simulation wrapper.

Implements a live FMU-style co-simulation interface:
  • connects to PicSimLab rcontrol server (GUI must be running)
  • syncs ngspice transient steps with PicSimLab timer events
  • maps MCU digital/analog pins to SPICE behavioral sources (B-elements)

Architecture:
    ngspice (CLI -p mode) ──► Python bridge ──► PicSimLab (TCP :5000)
         ▲                                              │
         └────── sync (cmd_sync timer tick) ◄────────────┘

The wrapper exposes `step()` for manual co-simulation loops or
`run()` for automatic NgspiceBridge-driven simulation.

Pin allocation is handled by PinMapper (see pin_mapper.py).
"""

from __future__ import annotations

import select as _select
import socket
import time
from pathlib import Path
from typing import Optional

import numpy as np

from .ngspice_bridge import NgspiceBridge, NgspiceBridgeError
from .pin_mapper import PinMapper, PinType, get_allocation


class PicSimLabFMUError(Exception):
    """Custom exception for PicSimLab FMU wrapper errors."""


class PicSimLabFMU:
    """
    FMU-style co-simulation wrapper for PicSimLab MCU firmware.

    NOT a compiled FMU — a Python co-simulation slave that:
      1. Connects to PicSimLab GUI via TCP rcontrol (must be started by user)
      2. Maps MCU pins to ngspice behavioral source (B-element) voltages
      3. Syncs ngspice transient steps with PicSimLab timer events

    Usage:
        fmu = PicSimLabFMU(board="stm32_bluepill")
        # User: start PicSimLab GUI, load project, enable rcontrol
        fmu.connect()
        fmu.request_pins([(PinType.DIGITAL, "MC"),
                          (PinType.ANALOG, "ADC_IN")])
        with NgspiceBridge() as ng:
            ng.load_netlist("circuit.cir")
            for step in range(1000):
                fmu.step(ng, tstep=1e-6, step=step)
    """

    def __init__(
        self,
        board: str = "stm32_bluepill",
        host: str = "127.0.0.1",
        port: int = 5000,
    ) -> None:
        """
        Args:
            board: MCU board name (from pin_mapper.get_allocation).
            host: rcontrol server host (always localhost if PicSimLab GUI).
            port: rcontrol server TCP port.
        """
        self.board_name = board
        self.host = host
        self.port = port

        try:
            self._allocation = get_allocation(board)
        except ValueError:
            self._allocation = None  # permit unknown boards without allocation?

        self._mapper: PinMapper | None = None
        if self._allocation:
            self._mapper = PinMapper(self._allocation)

        self._sock: socket.socket | None = None
        self._connected = False
        self._sim_running = False

        # Simulation state
        self._step_count = 0
        self._last_sync_time = 0.0
        self._pin_states: dict[str, float] = {}  # physical name -> voltage

        # Performance tracking
        self._step_times: list[float] = []

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def sim_running(self) -> bool:
        return self._sim_running

    @property
    def mapper(self) -> PinMapper | None:
        return self._mapper

    def connect(self, timeout: float = 5.0) -> None:
        """
        Connect to PicSimLab rcontrol server via raw TCP socket.

        Protocol: send command + \\r\\n, read until 'Ok\\r\\n>' or 'ERROR\\r\\n>'.
        Uses blocking socket with settimeout() for all operations.

        Args:
            timeout: connection timeout in seconds.

        Raises:
            PicSimLabFMUError if PicSimLab is not running with rcontrol enabled.
        """
        if self._connected:
            return

        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._sock.settimeout(5.0)
            self._sock.connect((self.host, self.port))

            # Consume greeting (blocking with timeout)
            try:
                greeting = bytearray()
                while True:
                    chunk = self._sock.recv(200)
                    if not chunk:
                        break
                    greeting.extend(chunk)
                    if b">" in chunk:
                        break
            except socket.timeout:
                pass

            # Start simulation so sync() can return
            try:
                self._sock.settimeout(2.0)
                self._sock.sendall(b"sim start\r\n")
                time.sleep(0.5)
                # Consume response
                resp = bytearray()
                try:
                    while True:
                        chunk = self._sock.recv(4096)
                        if not chunk:
                            break
                        resp.extend(chunk)
                        if resp.endswith(b"Ok\r\n>") or resp.endswith(b"ERROR\r\n>"):
                            break
                except socket.timeout:
                    pass
            except Exception:
                pass

            self._connected = True

        except (OSError, ConnectionRefusedError, socket.timeout) as exc:
            self._connected = False
            raise PicSimLabFMUError(
                f"Cannot connect to PicSimLab rcontrol at {self.host}:{self.port}"
                f" — is PicSimLab running with rcontrol enabled? ({exc})"
            ) from exc

    def disconnect(self) -> None:
        """Disconnect from PicSimLab rcontrol server."""
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
        self._sock = None
        self._connected = False
        self._sim_running = False

    # ---- rcontrol command wrappers (raw TCP socket, non-blocking) ----

    def _send_cmd(self, cmd: str, timeout: float = 3.0) -> str:
        """Send one rcontrol command, return full response string.

        Protocol: send 'CMD\\r\\n', read until 'Ok\\r\\n>' or 'ERROR\\r\\n>'.
        Uses select() for reliable non-blocking reads with timeout.
        """
        if not self._connected or self._sock is None:
            raise PicSimLabFMUError("Not connected to PicSimLab")

        try:
            self._sock.sendall(cmd.encode("utf-8"))
            self._sock.sendall(b"\r\n")
        except OSError as e:
            self._connected = False
            raise PicSimLabFMUError(f"Send failed: {e}") from e

        # Collect response until terminator using select()
        resp = bytearray()
        start = time.monotonic()
        while time.monotonic() - start < timeout:
            remaining = timeout - (time.monotonic() - start)
            if remaining <= 0:
                break
            r, _, _ = _select.select([self._sock], [], [], min(remaining, 0.2))
            if r:
                try:
                    chunk = self._sock.recv(4096)
                    if not chunk:
                        break
                    resp.extend(chunk)
                    if resp.endswith(b"Ok\r\n>") or resp.endswith(b"ERROR\r\n>"):
                        break
                except (socket.timeout, BlockingIOError, OSError):
                    break

        return resp.decode(errors="ignore")

    def cmd_help(self) -> str:
        return self._send_cmd("help")

    def cmd_version(self) -> str:
        return self._send_cmd("version")

    def cmd_blist(self) -> str:
        """List supported boards."""
        return self._send_cmd("blist")

    def cmd_buclist(self) -> str:
        """List supported MCUs."""
        return self._send_cmd("buclist")

    def cmd_loadhex(self, fname: str) -> str:
        """Load firmware .hex file."""
        return self._send_cmd(f"loadhex {fname}")

    def cmd_reset(self) -> str:
        """Reset the board."""
        return self._send_cmd("reset")

    def cmd_sim_start(self) -> str:
        """Start simulation."""
        self._sim_running = True
        return self._send_cmd("sim start")

    def cmd_sim_stop(self) -> str:
        """Stop simulation."""
        self._sim_running = False
        return self._send_cmd("sim stop")

    def cmd_sim(self) -> str:
        """Query simulation status."""
        return self._send_cmd("sim")

    def cmd_sync(self) -> str:
        """
        Wait for sync with timer event.

        This blocks until PicSimLab fires its next timer tick.
        Used for real-time lockstep co-simulation.
        """
        resp = self._send_cmd("sync")
        self._last_sync_time = time.monotonic()
        return resp

    def cmd_pins(self) -> str:
        """Show pins directions and values."""
        return self._send_cmd("pins")

    def cmd_pinsl(self) -> str:
        """Show pins formatted info."""
        return self._send_cmd("pinsl")

    def cmd_get(self, obj: str) -> str:
        """Get object value."""
        return self._send_cmd(f"get {obj}")

    def cmd_set(self, obj: str, value: str) -> str:
        """Set object value."""
        return self._send_cmd(f"set {obj} {value}")

    def cmd_clk(self, val_mhz: float = 0.0) -> str:
        """
        Show or set simulation clock.

        Args:
            val_mhz: clock speed in MHz (0 = query only).
        """
        if val_mhz > 0:
            return self._send_cmd(f"clk {val_mhz}")
        return self._send_cmd("clk")

    # ---- Pin mapping (requires PicSimLab connection) ----

    def request_pins(self, pins: list[tuple[PinType, str | None]]) -> dict[str, str | None]:
        """
        Request multiple pin allocations.

        Args:
            pins: list of (PinType, logical_name or None) tuples.

        Returns:
            dict mapping logical_name -> physical port (e.g. "PA0").
            Allocations are idempotent — requesting same logical name
            returns previously assigned physical port.
        """
        if self._mapper is None:
            raise PicSimLabFMUError("No pin allocation for this board")

        result = {}
        for pin_type, logical_name in pins:
            port = self._mapper.request_pin(pin_type, logical_name)
            result[logical_name or f"{pin_type.name}_{len(result)}"] = port

        return result

    def pin_voltage(self, physical_name: str, high: float = 3.3) -> float:
        """
        Read voltage for a physical pin from last parsed `pinsl` response.

        Args:
            physical_name: MCU port name, e.g. "PA0", "GPIO14", "D5".
            high: logic-high voltage level (default 3.3 V).

        Returns:
            Voltage (0.0 or high) based on last parsed pin state.
        """
        return self._pin_states.get(physical_name, 0.0)

    def assign_source(self, physical_name: str, ngspice_bridge: NgspiceBridge,
                      b_element_name: str, high: float = 3.3) -> None:
        """
        Push current pin voltage to ngspice via 'alter' command.

        In pipe mode: sends `alter V_MCU_NAME DC value` to ngspice stdin.

        Args:
            physical_name: MCU port name.
            ngspice_bridge: connected NgspiceBridge instance.
            b_element_name: SPICE B-element name (must match netlist).
            high: logic-high voltage.
        """
        voltage = self.pin_voltage(physical_name, high=high)
        if hasattr(ngspice_bridge, '_write_cmd'):
            ngspice_bridge._write_cmd(f"alter {b_element_name} DC {voltage:.6f}")
        elif hasattr(ngspice_bridge, 'send_command'):
            ngspice_bridge.send_command(f"alter {b_element_name} DC {voltage:.6f}")
        else:
            raise PicSimLabFMUError("NgspiceBridge does not support command interface")

    # ---- Co-simulation step ----

    def step(self, ngspice_bridge: NgspiceBridge, *, tstep: float = 1e-6,
             step: int = 0, query_pins_cmd: str = "pinsl") -> dict:
        """
        Perform one co-simulation step.

        Steps:
          1. Query MCU pin states from PicSimLab (`pinsl`).
          2. Parse pin voltages.
          3. Push voltages to ngspice B-elements via 'alter' command.
          4. Advance ngspice by tstep via pipe (alter + run).
          5. Wait for PicSimLab `sync` (timer tick).

        Args:
            ngspice_bridge: connected NgspiceBridge instance (pipe mode).
            tstep: ngspice transient timestep (seconds).
            step: current step index (for B-element source naming).
            query_pins_cmd: rcontrol command to fetch pin state.

        Returns:
            dict with step result.
        """
        t0 = time.monotonic()

        # 1. Query pin states from PicSimLab
        pin_resp = self._send_cmd(query_pins_cmd)
        pin_voltages = self._parse_pinsl(pin_resp)

        # 2. Push to ngspice as external pin voltage source
        for logical, physical in (self._mapper.assigned.items() if self._mapper else []):
            voltage = pin_voltages.get(physical, 0.0)
            old = self._pin_states.get(physical, None)
            if old is None or abs(voltage - old) > 0.001:
                try:
                    ngspice_bridge.send_command(f"alter V_{physical} DC {voltage:.6f}")
                except Exception:
                    pass

        # 3. Advance ngspice one step
        if step > 0:
            try:
                ngspice_bridge.send_command(f".tran {tstep:.6g} {tstep * (step + 2):.6g}")
            except Exception:
                pass
        try:
            ngspice_bridge.send_command("run")
        except Exception:
            pass

        # 4. Wait for PicSimLab timer sync
        try:
            self.cmd_sync()
        except PicSimLabFMUError:
            pass

        self._step_count = step + 1
        elapsed = time.monotonic() - t0
        self._step_times.append(elapsed)

        return {
            "step": step,
            "time": step * tstep,
            "pin_voltages": pin_voltages,
            "ngspice_bridge_alive": True,
            "elapsed_seconds": elapsed,
        }

    def _parse_pinsl(self, resp: str) -> dict[str, float]:
        """
        Parse `pinsl` rcontrol response into dict[physical_name, voltage].

        PicSimLab pinsl format (space-separated columns):
            pin[XX] D I L/H scale offset V "NAME"

        Returns: {NAME: voltage} — H/L mapped to 3.3/0.0, analog floats parsed.
        """
        result: dict[str, float] = {}
        for line in resp.splitlines():
            line = line.strip()
            if not line or line.startswith("pin["):
                # Header like "28 pins [atmega328p]:"
                continue
            parts = line.split()
            if len(parts) < 7:
                continue
            # Column 3 (index 2) = direction (D/P/R), column 4 (index 3) = I/O
            # Column 5 (index 4) = L/H, column 8 (index 7) = name
            state = parts[4]  # L or H or analog value
            name = parts[7].strip('"').strip()
            if name in ('+5V', 'GND', 'AREF', 'RST'):
                continue
            try:
                if state.upper() == 'H':
                    result[name] = 3.3
                elif state.upper() == 'L':
                    result[name] = 0.0
                else:
                    val = float(state)
                    result[name] = 3.3 if val > 1.65 else 0.0
            except ValueError:
                continue

        # Cache pin states for next step's delta compression
        self._pin_states = result.copy()
        return result

    # ---- FMU-style run loop ----

    def run(self, ngspice_bridge: NgspiceBridge, *, tstep: float = 1e-6,
            steps: int = 1000, progress_cb=None) -> list[dict]:
        """
        Run full co-simulation loop.

        Args:
            ngspice_bridge: connected and loaded NgspiceBridge.
            tstep: step size (seconds).
            steps: total steps to run.
            progress_cb: optional callback(step_idx, step_count, step_result)
                        for progress reporting.

        Returns:
            list of dicts with per-step results (voltage, status).
        """
        if not self._connected:
            raise PicSimLabFMUError("Not connected — call connect() first")

        results = []
        for i in range(steps):
            result = self.step(
                ngspice_bridge,
                tstep=tstep,
                step=i,
            )
            results.append(result)
            if progress_cb:
                progress_cb(i, steps, result)

            # Soft deadline: warn if step takes >2× target
            elapsed = result.get("elapsed_seconds", 0)
            if elapsed > tstep * 2:
                import sys
                print(
                    f"WARN: step {i} took {elapsed:.3g}s > 2× tstep={tstep:.3g}s",
                    file=sys.stderr,
                )

        return results

    def __enter__(self) -> "PicSimLabFMU":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.disconnect()
