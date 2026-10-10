#!/usr/bin/env python3
"""One rcontrol session, one connection, all the evidence at once.

PICSimLab 0.9.2 serves a single rcontrol connection at a time (0.9.3 raised it
to four). Every abandoned socket holds that one slot, so after a couple of
probes the interface looks dead even though it is running. This script starts
from a clean process and does everything in a single connection that it closes
properly at the end.
"""
from __future__ import annotations

import pathlib
import re
import socket
import subprocess
import sys
import time

BIN = "/mnt/ext4data/downloads/nogui092.AppImage"
HEX = "/home/mirrage/Desktop/кристалл-радио/firmware/firmware.hex"
INI = pathlib.Path.home() / ".picsimlab" / "picsimlab.ini"


def port() -> int:
    m = re.search(r'^picsimlab_remotecp\s*=\s*"?(\d+)"?', INI.read_text(), re.M)
    return int(m.group(1)) if m else 5000


def wait_port(p: int, timeout: float = 60.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        try:
            with socket.create_connection(("127.0.0.1", p), timeout=1):
                return True
        except OSError:
            time.sleep(0.25)
    return False


def drain(sock, seconds: float) -> str:
    buf = b""
    end = time.time() + seconds
    while time.time() < end:
        sock.settimeout(max(0.2, end - time.time()))
        try:
            chunk = sock.recv(65536)
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
    if "--no-restart" not in sys.argv:
        subprocess.run(["pkill", "-9", "-f", "nogui092"], capture_output=True)
        subprocess.run(["pkill", "-9", "-f", "picsimlab"], capture_output=True)
        time.sleep(1.5)
        log = open("/mnt/ext4data/pic.log", "w")
        subprocess.Popen([BIN, "Blue_Pill", "STM32F103C8", HEX],
                         stdout=log, stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL, start_new_session=True)

    p = port()
    if not wait_port(p):
        print(f"rcontrol port {p} never opened")
        return 1
    print(f"rcontrol listening on {p}")

    sock = socket.create_connection(("127.0.0.1", p), timeout=10)
    sock.settimeout(10)
    banner = drain(sock, 3.0)
    print(f"banner: {banner.strip()[:70]!r}")

    for cmd, wait in (("version", 2.5), ("sim", 2.5), ("pinsl", 5.0),
                      ("dumpr 20000000 8", 3.0)):
        sock.sendall((cmd + "\n").encode())
        time.sleep(0.3)
        r = drain(sock, wait)
        keep = [ln.strip() for ln in r.splitlines()
                if ln.strip() and "Type help" not in ln]
        print(f">>> {cmd}")
        for ln in keep[:6]:
            print("    " + ln[:96])

    sock.shutdown(socket.SHUT_RDWR)
    sock.close()
    print("session closed cleanly")
    return 0


if __name__ == "__main__":
    sys.exit(main())
