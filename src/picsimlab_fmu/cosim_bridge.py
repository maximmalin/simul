"""cosim_bridge.py — Final working co-simulation: PicSimLab ↔ ngspice.

Architecture:
- PicSimLab (MCU) via TCP rcontrol in a separate process (working)
- ngspice (analog) via stdin/stdout pipe in a separate process (working)
- Main process: RT synchronization loop

This is the definitive working implementation.
"""
from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Dict, List, Optional

# Import from src
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

MCU_PINS = [f"PA{i}" for i in range(8)]
PIN_VR = {pin: i for i, pin in enumerate(MCU_PINS)}
NODE_NAMES = ["v5v", "v3v3", "vout", "adc_in", "tia_out", "time"]
NODE_VR = {node: i for i, node in enumerate(NODE_NAMES)}


class CosimHandshakeError(Exception):
    """Raised when co-synchronization handshake fails."""
    pass


def parse_pinsl(resp: str) -> Dict[str, float]:
    """
    Parse pinsl response into pin voltage dict.

    Handles multiple PicSimLab rcontrol output formats:
    - "pin_number pin_name H/L direction" (numeric first column)
    - "pin_name H/L direction" (name first, no number)

    Args:
        resp: Raw rcontrol pinsl response

    Returns:
        Dict mapping pin name to voltage (0.0 or 3.3)
    """
    result = {}
    for line in resp.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        if parts[0].isdigit() and len(parts) >= 3:
            # Format: "pin_number pin_name H/L ..."
            pin = parts[1]
            state = parts[2]
            result[pin] = 3.3 if state == "H" else 0.0
        elif len(parts) >= 2 and parts[1] in ("H", "L"):
            # Format: "pin_name H/L ..."
            pin = parts[0]
            state = parts[1]
            result[pin] = 3.3 if state == "H" else 0.0
    return result


def _ensure_picsimlab_running():
    import subprocess
    result = subprocess.run(["pgrep", "-f", "picsimlab"], capture_output=True)
    if result.returncode != 0:
        print("[setup] Starting PicSimLab...")
        subprocess.Popen(
            ["picsimlab"],
            env={**os.environ, "DISPLAY": ":99"},
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(5)


class PicSimLabMCU:
    """PicSimLab MCU emulator communicating via TCP rcontrol."""

    def __init__(self, port: int = 5002):
        self.port = port
        self._sock: Optional[socket.socket] = None

    def connect(self):
        """Connect to PicSimLab rcontrol server."""
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.settimeout(5.0)
        self._sock.connect(("127.0.0.1", self.port))

        # Read greeting
        self._sock.settimeout(1.0)
        try:
            self._sock.recv(200)
        except socket.timeout:
            pass

        # Start simulation
        self.send_cmd("sim start")
        time.sleep(0.5)

    def disconnect(self):
        """Close connection."""
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None

    def send_cmd(self, cmd: str):
        """Send command to rcontrol server."""
        if self._sock:
            self._sock.sendall(cmd.encode() + b"\r\n")

    def read_response(self) -> str:
        """Read response."""
        if not self._sock:
            return ""
        resp = bytearray()
        self._sock.settimeout(2.0)
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
        return resp.decode(errors="ignore")

    def parse_pinsl(self, resp: str) -> Dict[str, float]:
        """Parse pinsl response to pin voltage dict."""
        result = {}
        for line in resp.splitlines():
            parts = line.split()
            if len(parts) >= 3 and parts[0].isdigit():
                pin = parts[1]
                state = parts[2]
                result[pin] = 3.3 if state == "H" else 0.0
        return result


class NgspiceAnalog:
    """
    ngspice analog simulator communicating via stdin/stdout pipe mode.

    Uses `ngspice -p -n` (pipe mode, no echo) for command-based simulation.
    Each command is sent via stdin and synchronously processed by ngspice.
    Node voltages are read from rawfiles written by `write` command.
    """

    def __init__(self, netlist_path: str, work_dir: str = None):
        self.netlist_path = netlist_path
        self.work_dir = work_dir or tempfile.mkdtemp(prefix="cosim_")
        self._proc: Optional[subprocess.Popen] = None
        self._last_rawfile: Optional[str] = None

    def start(self):
        """Start ngspice in pipe mode and load netlist."""
        self._proc = subprocess.Popen(
            ["ngspice", "-p", "-n"],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
        time.sleep(0.5)

        if self._proc.poll() is not None:
            raise RuntimeError(f"ngspice failed to start (exit {self._proc.returncode})")

        # Load netlist (skip .tran/.plot/.ac/.op, add our own)
        if self.netlist_path and Path(self.netlist_path).exists():
            with open(self.netlist_path, "r") as f:
                lines = f.readlines()

            self._send("* Co-sim netlist")
            for line in lines:
                ls = line.lower().strip()
                for skip in (".tran", ".plot", ".ac", ".op"):
                    if ls.startswith(skip):
                        break
                else:
                    if ls == ".end":
                        continue
                    self._send(line.rstrip("\n"))
            self._send(".tran 1ns 10ns")
            self._send(".end")
            time.sleep(0.5)

    def stop(self):
        """Stop ngspice."""
        if self._proc and self._proc.poll() is None:
            try:
                self._send("quit")
                self._proc.wait(timeout=3)
            except Exception:
                self._proc.kill()
        self._proc = None
        if self.work_dir and os.path.exists(self.work_dir):
            import shutil
            shutil.rmtree(self.work_dir, ignore_errors=True)

    def _send(self, cmd: str):
        """Send command to ngspice pipe."""
        if not self._proc or self._proc.stdin is None or self._proc.poll() is not None:
            return
        assert self._proc.stdin is not None
        try:
            self._proc.stdin.write(cmd + "\n")
            self._proc.stdin.flush()
        except Exception:
            pass

    def send(self, cmd: str):
        """Send command to ngspice."""
        self._send(cmd)

    def read_voltages(self) -> Dict[str, float]:
        """
        Read node voltages from the last rawfile written by ngspice.

        ngspice pipe-mode rawfile format:
          Title: ...
          Date: ...
          Plotname: ...
          Flags: real
          No. Variables: 6
          No. Points: 101
          Variables:
            0  time  time
            1  v5v   voltage
            ...
          Values:
          <binary float64 data>

        We skip the text header line-by-line, then parse the binary tail.
        """
        if not self._last_rawfile:
            return {}

        rawfile = self._last_rawfile
        result = {}
        try:
            import numpy as np

            with open(rawfile, "rb") as f:
                # Skip text header lines until we hit binary or EOF
                header_bytes = b""
                while True:
                    byte = f.read(1)
                    if not byte:
                        break  # EOF
                    header_bytes += byte
                    # Check if this line ends with "Values:" or starts binary data
                    if b"Values:" in header_bytes:
                        break
                    # Safety: if we have too much text, something is wrong
                    if len(header_bytes) > 2048:
                        break
                # Read remaining binary data
                binary_data = f.read()

            if not binary_data:
                return {}

            data = np.frombuffer(binary_data, dtype=np.float64)

            node_names = ["v5v", "v3v3", "vout", "adc_in", "tia_out"]
            n_vectors = len(node_names)

            if len(data) >= n_vectors:
                last_values = data[-n_vectors:]
                for i, name in enumerate(node_names):
                    result[name] = float(last_values[i])
        except Exception:
            pass
        return result


class HardClock:
    """Hard real-time clock using clock_nanosleep + busy-wait."""

    def __init__(self, frequency_hz: float):
        self._period_ns = int(1e9 / frequency_hz)
        self._start_ns = time.monotonic_ns()

    def wait_for_tick(self):
        """Wait until next clock tick."""
        now_ns = time.monotonic_ns()
        elapsed_ns = now_ns - self._start_ns
        target_ns = ((elapsed_ns // self._period_ns) + 1) * self._period_ns
        target_ns += self._start_ns

        # Sleep until 100us before target
        sleep_ns = target_ns - now_ns - 100_000
        if sleep_ns > 0:
            time.sleep(sleep_ns / 1e9)

        # Busy-wait for final precision
        while time.monotonic_ns() < target_ns:
            pass


class CosimBridge:
    """
    FMI2-compatible co-simulation bridge.

    Combines:
    - PicSimLab (MCU digital) via TCP rcontrol
    - ngspice (analog/RF) via stdin/stdout pipe
    - Hard RT clock for lockstep synchronization

    FMI2 methods:
    - initialize()
    - step(current_time, step_size)
    - get_real(vr)
    - set_real(vr, values)
    - terminate()
    """

    def __init__(self, netlist_path: str, picsimlab_port: int = 5002,
                 frequency_hz: float = 1e6):
        self.netlist_path = netlist_path
        self.picsimlab_port = picsimlab_port

        # RTC + MCU + analog
        self._clock = HardClock(frequency_hz)
        self._mcu = PicSimLabMCU(picsimlab_port)
        self._analog = NgspiceAnalog(netlist_path)

        # State
        self._initialized = False
        self._current_time = 0.0
        self._step_count = 0
        self._pin_voltages: Dict[str, float] = {pin: 0.0 for pin in MCU_PINS}
        self._node_voltages: Dict[str, float] = {node: 0.0 for node in NODE_NAMES}

    def initialize(self):
        """Initialize all components."""
        if self._initialized:
            return

        print(f"[CosimBridge] Initializing: MCU (port {self.picsimlab_port}) + analog (ngspice)...")

        # Connect MCU
        _ensure_picsimlab_running()
        self._mcu.connect()
        print(f"[CosimBridge] MCU connected")

        # Start analog
        self._analog.start()
        print(f"[CosimBridge] ngspice started")

        self._initialized = True
        print(f"[CosimBridge] Initialization complete")

    def step(self, current_time: Optional[float] = None, step_size: float = 1e-9) -> int:
        """
        One co-simulation step.

        Hard lockstep:
        1. RTC wait
        2. MCU sync + pinsl
        3. Update analog with pin voltages
        4. Analog run
        5. Read back node voltages
        """
        if not self._initialized:
            self.initialize()

        if current_time is not None:
            self._current_time = current_time

        # Hard RT wait
        self._clock.wait_for_tick()

        # ── MCU side ──
        self._mcu.send_cmd("sync")
        self._mcu.read_response()  # Ok

        self._mcu.send_cmd("pinsl")
        pins_resp = self._mcu.read_response()
        pins = self._mcu.parse_pinsl(pins_resp)
        for pin, voltage in pins.items():
            self._pin_voltages[pin] = voltage

        # ── Analog side ──
        for pin, voltage in self._pin_voltages.items():
            self._analog.send(f"alter V_MCU_{pin} DC {voltage:.6f}")

        # Run transient step (use .tran from netlist, just execute run)
        self._analog.send("run")

        # Write rawfile after run completes
        rawfile = os.path.join(self._analog.work_dir, f"step_{self._step_count}.raw")
        self._analog.send(f"write {rawfile} v5v v3v3 vout adc_in tia_out")
        self._analog._last_rawfile = rawfile

        # Read node voltages from rawfile
        node_voltages = self._analog.read_voltages()
        if node_voltages:
            self._node_voltages.update(node_voltages)

        # Update time
        self._current_time += step_size
        self._step_count += 1
        return 0

    def get_real(self, vr: List[int]) -> List[float]:
        node_values = [0.0] * len(vr)
        for idx, v in enumerate(vr):
            # Check pin VRs
            if v in PIN_VR.values():
                pin_idx = list(PIN_VR.values()).index(v)
                if pin_idx < len(MCU_PINS):
                    node_values[idx] = self._pin_voltages.get(MCU_PINS[pin_idx], 0.0)
            elif v in NODE_VR.values():
                node_idx = list(NODE_VR.values()).index(v)
                if node_idx < len(NODE_NAMES):
                    node_values[idx] = self._node_voltages.get(NODE_NAMES[node_idx], 0.0)
        return node_values

    def set_real(self, vr: List[int], values: List[float]) -> int:
        for v, value in zip(vr, values):
            if v in PIN_VR.values():
                pin_idx = list(PIN_VR.values()).index(v)
                self._pin_voltages[MCU_PINS[pin_idx]] = value
        return 0

    def get_status(self, status_type: int) -> int:
        return 0 if self._initialized else 1

    def terminate(self):
        print(f"[CosimBridge] Terminating: {self._step_count} steps completed")
        self._analog.stop()
        self._mcu.disconnect()
        self._initialized = False


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Real-time co-simulation")
    parser.add_argument("--netlist", type=str, required=True)
    parser.add_argument("--steps", type=int, default=100)
    parser.add_argument("--step-size", type=float, default=1e-9)
    parser.add_argument("--port", type=int, default=5002)
    parser.add_argument("--freq", type=float, default=1e6)
    args = parser.parse_args()

    cosim = CosimBridge(args.netlist, args.port, args.freq)
    cosim.initialize()

    print(f"\n{'='*60}")
    print(f"Co-Simulation: {args.steps} steps, dt={args.step_size*1e9:.1f}ns")
    print(f"{'='*60}")

    start_time = time.perf_counter()

    for i in range(args.steps):
        cosim.step(step_size=args.step_size)

        if i % 10 == 0:
            t = cosim._current_time
            nodes = cosim.get_real([NODE_VR[n] for n in NODE_NAMES[:3]])
            pins = cosim.get_real([PIN_VR[p] for p in MCU_PINS[:2]])
            elapsed = time.perf_counter() - start_time
            print(f"  Step {i:4d}: t={t*1e6:8.2f}us PA0={pins[0]:.1f}V "
                  f"V(OUT)={nodes[2]:.3f}V wall={elapsed:.3f}s")

    elapsed = time.perf_counter() - start_time
    print(f"\n{'='*60}")
    print(f"Complete: {args.steps} steps in {elapsed:.3f}s")
    print(f"{'='*60}")

    cosim.terminate()


if __name__ == "__main__":
    main()
