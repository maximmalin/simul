#!/usr/bin/env python3
"""Minimal GDB remote-serial-protocol client for talking to qemu-system-arm.

Why not the human monitor: HMP can read memory with `xp` but has no write
command at all -- `wp` answers "unknown command". The GDB stub can both read and
write, and can also single-step, which is what lockstep co-simulation needs.

The protocol is small enough to implement directly rather than depend on a gdb
binary that is not installed here:

    packet   $<hex payload>#<two-digit checksum>
    ack      +  or  -
    read     m<addr>,<len>          -> <len bytes of hex>
    write    M<addr>,<len>:<hex>
    continue c                       -> reply stops at the next stop
    step     s                       -> single instruction

Addresses are sent as raw target-endian bytes, which for a Cortex-M in QEMU is
little endian.
"""
from __future__ import annotations

import socket
import time
from typing import Optional


class GdbError(RuntimeError):
    pass


class GdbRemote:
    def __init__(self, host: str = "127.0.0.1", port: int = 1234,
                 timeout: float = 60.0, little_endian: bool = True):
        self.addr = (host, port)
        self.little_endian = little_endian
        self.sock: Optional[socket.socket] = None
        self.buf = b""
        end = time.time() + timeout
        last = None
        while time.time() < end:
            try:
                self.sock = socket.create_connection(self.addr, timeout=2)
                self.sock.settimeout(5)
                break
            except OSError as exc:
                last = exc
                time.sleep(0.4)
        if self.sock is None:
            raise GdbError(f"gdbstub never appeared on {port}: {last}")
        # The stub sends nothing until spoken to, so the first exchange is the
        # stop reason. It is discarded: this client drives `continue` itself.
        self._recv(timeout=5.0)

    # -- framing ---------------------------------------------------------
    @staticmethod
    def _checksum(payload: str) -> int:
        return sum(payload.encode("ascii")) & 0xFF

    def _send(self, payload: str) -> None:
        assert self.sock is not None
        pkt = f"${payload}#{self._checksum(payload):02x}".encode("ascii")
        self.sock.sendall(pkt)

    def _recv(self, timeout: float = 6.0) -> Optional[str]:
        """Read one packet payload, or None if the peer went away.

        QEMU prefixes each reply with a bare '+' ack before the '$' packet, so
        the buffer has to be scanned for the '$' rather than split on '#'
        immediately -- otherwise head is "+$00500020..." and the payload is
        discarded as malformed, which looks exactly like a timeout.
        """
        assert self.sock is not None
        self.sock.settimeout(timeout)
        end = time.time() + timeout
        while True:
            start = self.buf.find(b"$")
            if start >= 0:
                rest = self.buf[start:]
                end_idx = rest.find(b"#")
                if end_idx >= 0 and len(rest) >= end_idx + 3:
                    payload = rest[1:end_idx].decode("ascii", errors="replace")
                    self.buf = rest[end_idx + 3:]
                    try:
                        self.sock.sendall(b"+")
                    except OSError:
                        pass
                    return payload
            if time.time() > end:
                return None
            try:
                chunk = self.sock.recv(4096)
            except socket.timeout:
                return None
            except OSError:
                return None
            if not chunk:
                return None
            self.buf += chunk

    def _cmd(self, payload: str, timeout: float = 6.0) -> Optional[str]:
        self._send(payload)
        return self._recv(timeout)

    # -- operations ------------------------------------------------------
    def read(self, addr: int, length: int) -> bytes:
        """Read `length` bytes of memory."""
        raw = self._cmd(f"m{addr:x},{length:x}")
        if raw is None:
            raise GdbError(f"read 0x{addr:x}+{length} timed out")
        try:
            return bytes.fromhex(raw)
        except ValueError as exc:
            raise GdbError(f"bad read reply {raw!r}: {exc}") from exc

    def write(self, addr: int, data: bytes) -> None:
        """Write memory. QEMU answers OK or E<err>."""
        if not data:
            return
        rep = self._cmd(f"M{addr:x},{len(data):x}:{data.hex()}")
        if rep != "OK":
            raise GdbError(f"write 0x{addr:x} failed: {rep!r}")

    def read_u32(self, addr: int) -> int:
        return int.from_bytes(self.read(addr, 4), "little" if self.little_endian else "big")

    def write_u32(self, addr: int, value: int) -> None:
        self.write(addr, (value & 0xFFFFFFFF).to_bytes(
            4, "little" if self.little_endian else "big"))

    def continue_(self) -> None:
        """Resume the core.

        While the target is running the stub services no other packets, so a
        co-simulation master has to interrupt() before it can read or write
        anything. Without that, the first write after continue() times out --
        which looks like a broken mailbox but is just the protocol.
        """
        assert self.sock is not None
        self._send("c")
        # Swallow the immediate reply if the core is still stopped at reset;
        # once running, `c` produces nothing until the next stop.
        self._recv(timeout=0.4)

    def interrupt(self, timeout: float = 5.0) -> Optional[str]:
        """Stop a running core with the protocol's break byte.

        Returns the stop reply, e.g. 'S05' or 'T05...'. Retries once, because a
        break that lands while the stub is mid-packet is simply dropped.
        """
        assert self.sock is not None
        for _ in range(2):
            try:
                self.sock.sendall(b"\x03")
            except OSError:
                return None
            self.sock.sendall(b"+")
            rep = self._recv(timeout=timeout)
            if rep and (rep.startswith("S") or rep.startswith("T")
                        or rep.startswith("X")):
                return rep
        return None

    def step(self) -> None:
        self._send("s")

    def set_breakpoint(self, addr: int) -> None:
        self._cmd(f"Z0,{addr:x},2")

    def read_register(self, index: int) -> int:
        """index 15 == PC on ARM."""
        raw = self._cmd(f"p{index:x}")
        if not raw:
            raise GdbError(f"register {index} unreadable")
        return int.from_bytes(bytes.fromhex(raw),
                             "little" if self.little_endian else "big")

    def close(self) -> None:
        try:
            if self.sock:
                self.sock.sendall(b"$k#6b")
                self.sock.close()
        except OSError:
            pass
        self.sock = None
