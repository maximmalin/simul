#!/usr/bin/env python3
"""Drive PicSimLab's analog pins over rcontrol, using the documented protocol.

The remote control interface exposes analog pins directly:

    set apin[N] <volts>    drive an analog input pin
    get apin[N]            read an analog pin's voltage

That is the whole co-simulation boundary and it needs no invented side channel.
Earlier attempts built a USB-CDC path and a RAM mailbox and a V_MCU_* source in
the ngspice deck, none of which is what the interface is for.

ESP32_DevKitC is used because 0.9.3 NOGUI ships libqemu-xtensa.so: the STM32
backend is absent from every 0.9.3 package, which is why the Blue Pill could
not run here at all.
"""
from __future__ import annotations

import pathlib
import re
import socket
import subprocess
import sys
import time

INI = pathlib.Path.home() / ".picsimlab" / "picsimlab.ini"
APPNAME = "/mnt/ext4data/downloads/squashfs-root/AppRun"
BOARD = "ESP32_DevKitC"
PORT = 5002


def note(m):
    print(m, flush=True)


def set_board(board: str) -> None:
    for p in sorted(INI.parent.glob("picsimlab*.ini")):
        t = p.read_text()
        t = re.sub(r'^picsimlab_lab\s*=.*$', f'picsimlab_lab\t\t= "{board}"',
                   t, flags=re.M)
        t = re.sub(r'^picsimlab_lser\s*=.*$',
                   'picsimlab_lser\t\t= "/dev/null"', t, flags=re.M)
        p.write_text(t)


class Rcontrol:
    """One TCP connection. 0.9.x serves a single client at a time."""

    def __init__(self, port: int, timeout: float = 60.0):
        self.sock = None
        end = time.time() + timeout
        while time.time() < end:
            try:
                self.sock = socket.create_connection(("127.0.0.1", port),
                                                     timeout=2)
                break
            except OSError:
                time.sleep(0.4)
        if self.sock is None:
            raise RuntimeError(f"rcontrol never opened on {port}")
        self.sock.settimeout(6)
        self._drain()

    def _drain(self):
        try:
            self.sock.recv(65536)
        except OSError:
            pass

    def cmd(self, c: str, wait: float = 3.0) -> str:
        self.sock.sendall((c + "\r\n").encode())
        time.sleep(0.3)
        buf = b""
        end = time.time() + wait
        while time.time() < end:
            self.sock.settimeout(max(0.2, end - time.time()))
            try:
                chunk = self.sock.recv(65536)
            except socket.timeout:
                break
            except OSError:
                break
            if not chunk:
                break
            buf += chunk
            if buf.rstrip().endswith(b">"):
                break
        return buf.decode(errors="replace").replace("\r", "")


def main() -> int:
    subprocess.run(["pkill", "-9", "-f", "picsimlab"], capture_output=True)
    time.sleep(1.5)
    set_board(BOARD)
    subprocess.Popen([APPNAME], stdout=open("/mnt/ext4data/esp_r.log", "w"),
                     stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                     start_new_session=True)
    note(f"launching PICSimLab NOGUI, board {BOARD}")
    try:
        r = Rcontrol(PORT)
    except RuntimeError as exc:
        note(f"{exc}")
        log = pathlib.Path("/mnt/ext4data/esp_r.log")
        if log.exists():
            note(log.read_text()[-700:])
        return 1

    info = r.cmd("info")
    note("--- info (board / processor) ---")
    for ln in info.splitlines():
        if any(k in ln for k in ("Board:", "Processor:", "Frequency:")):
            note("   " + ln.strip())

    pins = r.cmd("pinsl")
    note("\n--- analog pins (A type) ---")
    apins = []
    for ln in pins.splitlines():
        m = re.match(r'\s*pin\[(\d+)\]\s+A\s+\S+\s+\S+\s+\S+\s+([\d.]+)\s+"(.*?)"',
                     ln)
        if m:
            apins.append((int(m.group(1)), m.group(3).strip(),
                          float(m.group(2))))
            note(f"   pin[{m.group(1):>2}]  {m.group(3).strip():<12} "
                 f"{float(m.group(2)):.3f} V")
    if not apins:
        note("   (none reported)")
        return 1

    # Drive one analog input and read it back. This is the co-simulation
    # boundary in its documented form: the host writes a voltage onto a pin,
    # and the firmware's ADC reads it.
    first = apins[0][0]
    note(f"\n--- set/get on pin[{first}] ---")
    for volts in (0.0, 1.65, 3.3):
        r.cmd(f"set apin[{first}] {volts}")
        got = r.cmd(f"get apin[{first}]")
        m = re.search(rf"apin\[{first}\]\s*=\s*([-\d.]+)", got)
        read = float(m.group(1)) if m else None
        note(f"   set {volts:5.2f} V -> read {read}")

    r.sock.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
