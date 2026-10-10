#!/usr/bin/env python3
"""Read the CPU registers at the Infinite_Loop breakpoint.

The stacked frame says the exception was taken while executing
`ldr r1, [r2, #0>` inside setup(), where r2 should hold the GPIOA base.
Reading r2 here settles whether the fault is a wrong literal, a wrong
peripheral address, or a genuine bus error from the emulated part.
"""
import subprocess, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from gdb_remote import GdbRemote

ELF = "/mnt/ext4data/kristall-radio/firmware/.pio/build/cosim/firmware.elf"

def nm_addr(name):
    import glob
    nm = glob.glob("/home/mirrage/.platformio/**/arm-none-eabi-nm", recursive=True)[0]
    import subprocess
    r = subprocess.run([nm, "-n", ELF], capture_output=True, text=True)
    for ln in r.stdout.splitlines():
        p = ln.split()
        if len(p) >= 3 and p[2] == name:
            return int(p[0], 16)
    return None

il = nm_addr("Infinite_Loop")
print(f"Infinite_Loop 0x{il:08x}")

q = subprocess.Popen(["qemu-system-arm", "-M", "stm32vldiscovery", "-nographic",
                      "-S", "-kernel", ELF, "-gdb", "tcp:127.0.0.1:1234"],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    g = GdbRemote(port=1234)
    g.set_breakpoint(il)
    g.continue_()
    time.sleep(3)
    g.interrupt()
    for i in range(16):
        print(f"  r{i:<2} 0x{g.read_register(i):08x}")
    pc = g.read_register(15)
    print(f"  pc 0x{pc:08x}")
    g.close()
finally:
    q.kill(); q.wait(timeout=10)