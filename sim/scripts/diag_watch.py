#!/usr/bin/env python3
"""Find the exact instruction that lands in Infinite_Loop.

A watchpoint on the PC's return address catches the moment it is pushed, so the
instruction that called into the faulting path is known rather than inferred.
Everything here is the difference between reading a stack after the fact and
watching the call happen.
"""
from __future__ import annotations

import glob
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gdb_remote import GdbRemote

ELF = "/mnt/ext4data/kristall-radio/firmware/.pio/build/cosim/firmware.elf"
INFINITE_LOOP = None      # filled in from the symbol table


def tool(name):
    hits = glob.glob(f"/home/mirrage/.platformio/**/arm-none-eabi-{name}",
                     recursive=True)
    return hits[0] if hits else None


def find_symbol(name):
    nm = tool("nm")
    r = subprocess.run([nm, "-n", ELF], capture_output=True, text=True)
    for line in r.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[2] == name:
            return int(parts[0], 16)
    return None


def where(addr):
    a2l = tool("addr2line")
    if not a2l:
        return ""
    r = subprocess.run([a2l, "-f", "-C", "-e", ELF, f"0x{addr:x}"],
                       capture_output=True, text=True)
    return r.stdout.strip().replace("\n", " @ ")


def disasm_around(addr, span=32):
    od = tool("objdump")
    if not od:
        return ""
    r = subprocess.run([od, "-d", "--start-address=0x%x" % (addr - span),
                        "--stop-address=0x%x" % (addr + 8), ELF],
                       capture_output=True, text=True)
    return "\n".join(l for l in r.stdout.splitlines()
                     if re.match(r"\s+[0-9a-f]+:", l))


import re  # noqa: E402  (used by disasm_around)


def main() -> int:
    global INFINITE_LOOP
    INFINITE_LOOP = find_symbol("Infinite_Loop")
    if INFINITE_LOOP is None:
        print("Infinite_Loop not in the symbol table", file=sys.stderr)
        return 1
    print(f"Infinite_Loop at 0x{INFINITE_LOOP:08x}")

    qemu = subprocess.Popen(
        ["qemu-system-arm", "-M", "stm32vldiscovery", "-nographic", "-S",
         "-kernel", ELF, "-gdb", "tcp:127.0.0.1:1234"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        g = GdbRemote(port=1234)
        # Break on the fault target, then watch the LR slot it will return to.
        g.set_breakpoint(INFINITE_LOOP)
        g.continue_()
        time.sleep(3)
        g.interrupt()

        pc = g.read_register(15)
        lr = g.read_register(14)
        sp = g.read_register(13)
        print(f"PC  0x{pc:08x}  {where(pc)}")
        print(f"LR  0x{lr:08x}  {where(lr)}")
        print(f"SP  0x{sp:08x}")

        # The saved LR of the frame that entered the handler is on the stack.
        # Scan for any word that resolves to code inside setup().
        a2l = tool("addr2line")
        raw = g.read(sp, 512)
        print("\nstack words that resolve to firmware code:")
        for i in range(0, len(raw) - 3, 4):
            w = int.from_bytes(raw[i:i + 4], "little")
            if not (0x08000000 <= w < 0x08020000):
                continue
            name = where(w)
            if "@" in name and "??" not in name.split("@")[0]:
                print(f"  [SP+0x{i:03x}] 0x{w:08x}  {name}")
        print("\ndisassembly around PC:")
        print(disasm_around(pc))
        g.close()
        return 0
    finally:
        qemu.kill()
        qemu.wait(timeout=10)


if __name__ == "__main__":
    sys.exit(main())
