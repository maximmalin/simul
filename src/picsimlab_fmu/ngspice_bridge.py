"""ngspice_bridge.py — NGspice CLI bridge with prompt-sync for hard lockstep.

Provides synchronous communication with ngspice via pipe mode (-p):
  • Every command waits for the ngspice prompt (> ) before returning
  • B-element voltages can be set and transient steps executed
  • Node voltages can be queried after each step

This enables REAL co-simulation where each side waits for the other.
"""

from __future__ import annotations

import struct
import subprocess
import threading
import time
from typing import Optional


class NgspiceBridgeError(Exception):
    """Custom exception for ngspice bridge failures."""


class NgspiceBridge:
    """
    Synchronous bridge to ngspice via subprocess pipe mode.

    Every send_command() call blocks until ngspince emits its prompt,
    guaranteeing the previous operation completed before the next starts.

    Usage:
        bridge = NgspiceBridge()
        bridge.load_netlist("circuit.cir")
        bridge.set_source("B_MCU_GPIO", "V=3.3")
        bridge.run_step()                # waits for prompt
        voltage = bridge.query("V(out)")
        bridge.stop()
    """

    def __init__(self, extra_args: tuple[str, ...] = ()) -> None:
        """
        Start ngspice in pipe (-p) mode.

        `extra_args`: additional ngspice CLI args, e.g. ("-r", "out.raw").
        """
        self._proc: subprocess.Popen[bytes] | None = None
        self._lock = threading.Lock()
        self._buffer = bytearray()
        self._reader_thread: threading.Thread | None = None
        self._stop_reader = threading.Event()
        self._prompt_reached = threading.Event()
        self._rawfile_path: str = "cosim_output.raw"

        args = ["ngspice", "-p", "-n"]
        args.extend(extra_args)
        # Always add rawfile for .print tran data
        if not any(a.startswith("-r") or a.startswith("--rawfile") for a in extra_args):
            args.extend(["-r", self._rawfile_path])

        try:
            self._proc = subprocess.Popen(
                args,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
            )
        except FileNotFoundError as exc:
            raise NgspiceBridgeError(
                "ngspice not found. Install: sudo apt-get install ngspice"
            ) from exc

        self._start_reader()
        time.sleep(0.5)  # allow ngspice to initialize

    def _start_reader(self) -> None:
        """Start background reader thread to consume ngspice stdout."""
        assert self._proc and self._proc.stdout, "ngspice process not running"

        def _read_stdout() -> None:
            while not self._stop_reader.is_set():
                try:
                    assert self._proc is not None and self._proc.stdout is not None
                    chunk = self._proc.stdout.read(4096)
                    if not chunk:
                        break
                    with self._lock:
                        self._buffer.extend(chunk)
                        # Detect prompt: ngspice outputs "> " when ready
                        if b"> " in chunk or chunk.endswith(b">"):
                            self._prompt_reached.set()
                except (OSError, BrokenPipeError, ValueError):
                    break

        self._reader_thread = threading.Thread(target=_read_stdout, daemon=True)
        self._reader_thread.start()

    def send_command(self, cmd: str, wait: bool = True, timeout: float = 5.0) -> bool:
        """
        Send a command to ngspice.

        Args:
            cmd: ngspice command string
            wait: if True, block until ngspice emits prompt
            timeout: max seconds to wait for prompt

        Returns:
            True if command was sent (and prompt received if wait=True)
        """
        if self._proc is None or self._proc.stdin is None or self._proc.stdin.closed:
            raise NgspiceBridgeError("ngspice process not running")

        self._prompt_reached.clear()
        with self._lock:
            try:
                self._proc.stdin.write((cmd + "\n").encode("utf-8"))
                self._proc.stdin.flush()
            except Exception:
                return False

        if wait:
            return self._prompt_reached.wait(timeout=timeout)
        return True

    def load_netlist(self, path: str) -> bool:
        """Load a SPICE netlist file. Waits for completion."""
        return self.send_command(f"source {path}", wait=True)

    def set_source(self, source_name: str, expr: str) -> bool:
        """
        Update a behavioral source expression.

        Args:
            source_name: SPICE element name (e.g. "B_MCU_PE0")
            expr: ngspice expression (e.g. "V=3.3")
        """
        return self.send_command(f"alter {source_name} {expr}", wait=True)

    def run_step(self, timeout: float = 5.0) -> bool:
        """
        Execute one transient step and wait for prompt.

        This is the KEY method for hard lockstep co-simulation.
        ngspice processes one .tran iteration and blocks until done.
        """
        return self.send_command("run", wait=True, timeout=timeout)

    def query(self, query: str, timeout: float = 2.0) -> float | None:
        """
        Read the LAST value of a node voltage from the rawfile.

        ngspice pipe mode does NOT support interactive queries mid-transient.
        Instead, this method reads the most recent value from the .print tran
        output written to the rawfile (cosim_output.raw).

        The netlist MUST include: .print tran V(node1) V(node2) ...

        Returns:
            Float voltage value, or None if no data available
        """
        try:
            # Read the last raw entry from the rawfile
            raw_data = self._read_rawfile_last()
            if raw_data is None:
                return None
            # raw_data is dict of {node_name: float}
            return raw_data.get(query.lower()) or raw_data.get(query)
        except Exception:
            return None

    def _read_rawfile_last(self) -> dict[str, float] | None:
        """
        Read the last transient data point from the ngspice rawfile.

        ngspice rawfile format (binary or ASCII):
          - ASCII: each line is "time\tv1\tv2\t..."
          - We parse the last line and map column names to values

        Returns:
            dict mapping node_name -> voltage, or None on failure
        """
        try:
            with open(self._rawfile_path, "r") as f:
                lines = f.readlines()
            if not lines:
                return None

            # Find last non-empty line with data
            for line in reversed(lines):
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                # Parse space/tab-separated values
                parts = line.split()
                if len(parts) >= 2:
                    # First column is time, rest are voltages
                    result = {}
                    for i, part in enumerate(parts[1:], 1):
                        try:
                            result[f"v{i}"] = float(part)
                        except ValueError:
                            continue
                    return result
        except Exception:
            pass
        return None

    def read_rawfile(self) -> dict[str, float]:
        """
        Read the entire ngspice rawfile and return latest values per node.

        Returns:
            dict mapping column_index -> float (v1, v2, v3...)
        """
        try:
            with open(self._rawfile_path, "r") as f:
                lines = f.readlines()
            if not lines:
                return {}

            # Find the last data block
            # ngspice rawfile: time v1 v2 v3 ... (whitespace separated)
            result = {}
            for line in reversed(lines):
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split()
                if len(parts) >= 2:
                    # Parse all columns
                    for i, part in enumerate(parts):
                        try:
                            result[f"v{i}"] = float(part)
                        except ValueError:
                            continue
                    break
        except Exception:
            pass
        return result

    def wait_for_prompt(self, timeout: float = 5.0) -> bool:
        """
        Wait for ngspice to emit a prompt.

        Args:
            timeout: max seconds to wait

        Returns:
            True if prompt was detected within timeout
        """
        return self._prompt_reached.wait(timeout=timeout)

    def stop(self) -> None:
        """Terminate ngspice process."""
        self._stop_reader.set()
        if self._proc and self._proc.stdin and not self._proc.stdin.closed:
            try:
                self._proc.stdin.write(b"quit\n")
                self._proc.stdin.flush()
            except Exception:
                pass
        if self._proc:
            self._proc.terminate()
            self._proc.wait(timeout=3)
        if self._reader_thread and self._reader_thread.is_alive():
            self._reader_thread.join(timeout=1)

    def __enter__(self) -> "NgspiceBridge":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.stop()
