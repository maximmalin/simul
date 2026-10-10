#!/usr/bin/env python3
"""Diagnose the GPIOA literal in setup() by checking what's in the ELF."""
import subprocess, sys
from pathlib import Path

ELF = "/mnt/ext4data/kristall-radio/firmware/.pio/build/cosim/firmware.elf"
sys.path.insert(0, str(Path(__file__).resolve().parent))

def tool(name):
    import glob
    hits = glob.glob(f"/home/mirrage/.platformio/**/arm-none-eabi-{name}", recursive=True)
    return hits[0] if hits else None

nm = tool("nm")
od = tool("objdump")
a2l = tool("addr2line")

# Check GPIOA symbol value
print("=== GPIOA / RCC symbols in ELF ===")
r = subprocess.run([nm, "-n", ELF], capture_output=True, text=True)
for line in r.stdout.splitlines():
    if "GPIOA" in line or "RCC" in line and "=" not in line:
        print(line[:80])

# Check the literal at 0x8000ebc
print("\n=== literal pool at 0x8000ebc ===")
r2 = subprocess.run([nm, "-S", "-n", ELF], capture_output=True, text=True)
lines = r2.stdout.splitlines()
for i, line in enumerate(lines):
    if "8000eb" in line:
        print(">>>", line[:100])
        for j in range(4):
            if i+j < len(lines):
                print(lines[i+j][:80])
        break

# Check setup() disassembly  
print("\n=== setup() with symbol values ===")
r3 = subprocess.run([od, "-d", "-j", ".text", "--start-address=0x08000de8", 
                     "--stop-address=0x08000e10", ELF], capture_output=True, text=True)
print(r3.stdout[:1200])

# Check section addresses for GPIOA literal
print("\n=== section .rodata / .data ===")
r4 = subprocess.run([nm, "-n", "-S", "--print-size", ELF], capture_output=True, text=True)
for line in r4.stdout.splitlines():
    parts = line.split()
    if len(parts) >= 4 and "GPIOA" in parts[3]:
        print(line[:100])
    if len(parts) >= 4 and "0x4001" in parts[0]:
        print(line[:100])
