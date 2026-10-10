#!/usr/bin/env python3
"""Break on Infinite_Loop and find out what called into it.

The core reaches Default_Handler / Infinite_Loop rather than the main loop, so
something faults or asserts. Reading the stack at the breakpoint names the
caller, which is the difference between guessing and knowing.
"""
import glob
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


def symbol_for(addr):
    t = tool("addr2line")
    if not t:
        return ""
    r = subprocess.run([t, "-f", "-C", "-e", ELF, f"0x{addr:x}"],
                       capture_output=True, text=True)
    return r.stdout.strip().replace("\n", " @ ")


def main() -> int:
    qemu = subprocess.Popen(
        ["qemu-system-arm", "-M", "stm32vldiscovery", "-nographic", "-S",
         "-kernel", ELF, "-gdb", "tcp:127.0.0.1:1234"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        g = GdbRemote(port=1234)
        # 0x080018f8 is Default_Handler / Infinite_Loop in this image.
        print("breakpoint on Infinite_Loop (0x080018f8)")
        g.set_breakpoint(0x080018F8)
        g.continue_()
        time.sleep(3)
        rep = g.interrupt()
        print("stop reply:", rep)
        pc = g.read_register(15)
        lr = g.read_register(14)
        sp = g.read_register(13)
        print(f"PC = 0x{pc:08x}  -> {symbol_for(pc)}")
        print(f"LR = 0x{lr:08x}  -> {symbol_for(lr)}")
        print(f"SP = 0x{sp:08x}")
        print("\nstack:")
        raw = g.read(sp, 64)
        for i in range(0, len(raw), 4):
            w = int.from_bytes(raw[i:i + 4], "little")
            if w:
                s = symbol_for(w)
                print(f"  [SP+0x{i:02x}] 0x{w:08x}  {s}")
        g.close()
        return 0
    finally:
        qemu.kill()
        qemu.wait(timeout=10)


if __name__ == "__main__":
    sys.exit(main())
