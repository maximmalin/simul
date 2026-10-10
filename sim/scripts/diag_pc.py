#!/usr/bin/env python3
"""Where is the firmware actually stopped?

Reads PC and the stack pointer over the GDB stub and maps the PC back to a
source line, so "setup() never returns" becomes a specific function rather than
a guess. arm-none-eabi-addr2line comes from the PlatformIO toolchain that
built the firmware.
"""
import glob
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gdb_remote import GdbRemote

ELF = "/mnt/ext4data/kristall-radio/firmware/.pio/build/cosim/firmware.elf"


def tool(name):
    hits = glob.glob(f"/home/mirrage/.platformio/**/arm-none-eabi-{name}",
                     recursive=True)
    return hits[0] if hits else None


def main() -> int:
    qemu = subprocess.Popen(
        ["qemu-system-arm", "-M", "netduinoplus2", "-nographic", "-S",
         "-kernel", ELF, "-gdb", "tcp:127.0.0.1:1234"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        g = GdbRemote(port=1234)
        pc0 = g.read_register(15)
        print(f"PC at reset      : 0x{pc0:08x}")

        g.continue_()
        time.sleep(3)
        g.interrupt()
        pc = g.read_register(15)
        sp = g.read_register(13)
        print(f"PC after 3 s     : 0x{pc:08x}")
        print(f"SP               : 0x{sp:08x}")

        regs = g._cmd("g")
        if regs:
            print("registers        :", regs[:96], "...")

        for name in ("addr2line", "objdump"):
            t = tool(name)
            if not t:
                continue
            if name == "addr2line":
                r = subprocess.run([t, "-f", "-C", "-e", ELF, f"0x{pc:x}"],
                                   capture_output=True, text=True)
                print(f"addr2line        :\n{r.stdout.strip()}")
            else:
                r = subprocess.run([t, "-d", "--start-address=0x%x" % pc,
                                    "--stop-address=0x%x" % (pc + 32), ELF],
                                   capture_output=True, text=True)
                tail = [ln for ln in r.stdout.splitlines()
                        if re.match(r"\s+[0-9a-f]+:", ln)]
                print(f"disassembly at PC:\n" + "\n".join(tail[:8]))
        g.close()
        return 0
    finally:
        qemu.kill()
        qemu.wait(timeout=10)


if __name__ == "__main__":
    sys.exit(main())
