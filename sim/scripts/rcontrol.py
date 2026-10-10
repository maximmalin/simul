#!/usr/bin/env python3
"""Talk to PICSimLab's rcontrol on the port the config actually specifies.

The port is read from ~/.picsimlab/picsimlab.ini rather than assumed. That
assumption cost a lot of time: the local config uses 5002, not the upstream
default 5000, so every probe of 5000 returned "the listener never opens" while
the interface was up and answering on 5002 the whole time.
"""
from __future__ import annotations

import pathlib
import re
import socket
import sys
import time


def configured_port(default: int = 5000) -> int:
    ini = pathlib.Path.home() / ".picsimlab" / "picsimlab.ini"
    if not ini.exists():
        return default
    m = re.search(r'^picsimlab_remotecp\s*=\s*"?(\d+)"?',
                  ini.read_text(), re.M)
    return int(m.group(1)) if m else default


def drain(sock, deadline_s: float = 4.0) -> str:
    buf = b""
    end = time.time() + deadline_s
    while time.time() < end:
        sock.settimeout(max(0.3, end - time.time()))
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


def command(sock, cmd: str, wait: float = 3.0) -> str:
    sock.sendall((cmd + "\n").encode())
    time.sleep(0.3)
    return drain(sock, wait)


def main() -> int:
    port = configured_port()
    cmds = sys.argv[1:] or ["version", "sim", "pinsl"]
    try:
        sock = socket.create_connection(("127.0.0.1", port), timeout=10)
    except OSError as exc:
        print(f"cannot reach rcontrol on port {port}: {exc}")
        return 1
    sock.settimeout(10)
    # The banner arrives unsolicited on connect.
    banner = drain(sock, 3.0)
    print(f"connected to port {port}; banner: "
          f"{'yes' if 'Remote Control' in banner else 'no'}")
    try:
        for c in cmds:
            r = command(sock, c, 6.0 if c == "pinsl" else 3.0)
            # Keep it to the interesting lines; pinsl is 48 rows.
            lines = [ln for ln in r.splitlines()
                     if ln.strip() and not ln.startswith(("  Type", "PICSimLab Remote"))]
            if c == "pinsl":
                keep = [ln for ln in lines if "pins [" in ln or "PA6" in ln
                        or "PA0" in ln or "PA1" in ln]
            else:
                keep = lines
            print(f">>> {c}")
            for ln in keep[:8]:
                print("    " + ln.strip()[:100])
    finally:
        sock.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
