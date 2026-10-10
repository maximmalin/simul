#!/usr/bin/env python3
"""Launch PicSimLab_NOGUI and wait for its rcontrol port to accept.

Polls for the port instead of sleeping a fixed interval: the time to come up
varies from under a second to tens of seconds depending on board and backend,
and a fixed sleep either wastes time or races the startup.

The board comes from the config file only -- rcontrol has no `board` command,
so `board Blue_Pill` returns ERROR and the board silently stays whatever the
.ini said. PicSimLab also rewrites its .ini on exit, so the board has to be set
while nothing is running or the next start silently reverts to the old board.
"""
from __future__ import annotations

import argparse
import os
import pathlib
import re
import signal
import socket
import subprocess
import sys
import time

HOME_INI = pathlib.Path.home() / ".picsimlab"
DEFAULT_BIN = "/mnt/ext4data/downloads/nogui092.AppImage"


def set_board(board: str) -> list[pathlib.Path]:
    """Write the board into every instance config.

    All of them, not just picsimlab.ini: 0.9.x picks an instance config at
    startup and the log names which one ("Load Config from file
    .../picsimlab_1.ini"), so editing only the default file leaves the running
    instance on whatever board it had before.
    """
    touched = []
    if not HOME_INI.is_dir():
        print(f"no {HOME_INI}", file=sys.stderr)
        return touched
    for p in sorted(HOME_INI.glob("picsimlab*.ini")):
        t = p.read_text()
        t = re.sub(r'^picsimlab_lab\s*=.*$', f'picsimlab_lab\t\t= "{board}"',
                   t, flags=re.M)
        # /dev/tnt2 is the documented default and does not exist here; it makes
        # PicSimLab print "Error on Port Open /dev/tnt2!" on every start.
        t = re.sub(r'^picsimlab_lser\s*=.*$', 'picsimlab_lser\t\t= "/dev/null"',
                   t, flags=re.M)
        t = re.sub(r'^picsimlab_remotecp\s*=.*$', 'picsimlab_remotecp\t= "5000"',
                   t, flags=re.M)
        p.write_text(t)
        touched.append(p)
    return touched


def wait_port(port: int, timeout: float) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return True
        except OSError:
            time.sleep(0.25)
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bin", default=DEFAULT_BIN)
    ap.add_argument("--board", default="Blue_Pill")
    ap.add_argument("--port", type=int, default=5000)
    ap.add_argument("--timeout", type=float, default=90.0)
    ap.add_argument("--stop", action="store_true",
                    help="kill any running instance and exit")
    args = ap.parse_args()

    subprocess.run(["pkill", "-9", "-f", "picsimlab"], capture_output=True)
    subprocess.run(["pkill", "-9", "-f", "nogui"], capture_output=True)
    time.sleep(1.0)
    if args.stop:
        print("stopped")
        return 0

    if not pathlib.Path(args.bin).exists():
        print(f"missing {args.bin}", file=sys.stderr)
        return 1
    for p in set_board(args.board):
        print(f"  {p.name}: board={args.board}")

    log = open("/mnt/ext4data/nogui_run.log", "w")
    proc = subprocess.Popen([args.bin], stdout=log, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, start_new_session=True)
    print(f"launched pid {proc.pid}, waiting for port {args.port} ...")
    if not wait_port(args.port, args.timeout):
        log.flush()
        print(open("/mnt/ext4data/nogui_run.log").read()[-800:], file=sys.stderr)
        print(f"TIMEOUT: port {args.port} never accepted", file=sys.stderr)
        return 1
    print(f"port {args.port} is accepting connections")

    # Report what actually came up, so a silent failure is visible immediately.
    time.sleep(2.0)
    log.flush()
    tail = open("/mnt/ext4data/nogui_run.log").read()
    for line in tail.splitlines():
        if re.search(r'Using board|qemu-|Error loading', line):
            print("  " + line.strip()[:110])
    return 0


if __name__ == "__main__":
    sys.exit(main())
