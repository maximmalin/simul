#!/usr/bin/env python3
"""Why is the mailbox not responding: is the write landing, and is loop() running?

Three separate questions that all look identical from the outside (stamps stuck
at 0):
  1. does the host's write actually reach guest RAM?
  2. does the firmware's loop() execute at all?
  3. does the magic compare match what the host wrote?

Each is checked with an independent signal rather than inferred.
"""
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gdb_remote import GdbRemote

BASE = 0x20004000
MAGIC = 0x43305349
OFF_MAGIC = 0x00
OFF_MCU_STAMP = 0x10
ADC_BUF = 0x20000FBC          # from nm: _ZL7adc_buf
# cosim_canary's address is decided by the linker, so read it rather than
# hard-coding it. An earlier run watched 0x20000d50 from a stale build and
# concluded "loop is running" from a USB buffer that changes on its own.
CANARY = None


def find_canary() -> int:
    """Address of cosim_canary, straight out of the linked image."""
    import glob
    # The cosim environment: same firmware with USB compiled out.
    elf = ("/mnt/ext4data/kristall-radio/firmware/.pio/build/cosim/firmware.elf")
    for nm in glob.glob("/home/mirrage/.platformio/**/arm-none-eabi-nm",
                        recursive=True):
        r = subprocess.run([nm, elf], capture_output=True, text=True)
        for line in r.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 3 and parts[2] == "cosim_canary":
                return int(parts[0], 16)
    raise SystemExit("cosim_canary not found -- did platformio run rebuild?")


def main() -> int:
    qemu = subprocess.Popen(
        ["qemu-system-arm", "-M", "netduinoplus2", "-nographic", "-S",
         "-kernel", "/mnt/ext4data/kristall-radio/firmware/.pio/build/"
                    "cosim/firmware.elf",
         "-gdb", "tcp:127.0.0.1:1234"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        canary = find_canary()
        print(f"cosim_canary at 0x{canary:08x}\n")
        g = GdbRemote(port=1234)
        print("1. does the write land?")
        g.write_u32(BASE + OFF_MAGIC, MAGIC)
        back = g.read_u32(BASE + OFF_MAGIC)
        print(f"   wrote 0x{MAGIC:08x}, read back 0x{(back or 0):08x} -> "
              f"{'OK' if back == MAGIC else 'MISMATCH'}")

        print("2. is loop() running? (watch a word we know changes)")
        g.continue_()
        time.sleep(2)
        g.interrupt()
        a = g.read_u32(canary)
        g.continue_()
        time.sleep(2)
        g.interrupt()
        b = g.read_u32(canary)
        print(f"   0x{canary:08x}: 0x{a:08x} -> 0x{b:08x} -> "
              f"{'CHANGING (loop runs)' if a != b else 'static'}")

        print("3. with magic set, do the stamps move?")
        s0 = g.read_u32(BASE + OFF_MCU_STAMP)
        for _ in range(4):
            g.continue_()
            time.sleep(1.0)
            g.interrupt()
        s1 = g.read_u32(BASE + OFF_MCU_STAMP)
        print(f"   mcu stamp 0x{BASE + OFF_MCU_STAMP:08x}: "
              f"{s0} -> {s1} -> {'MOVING' if s1 != s0 else 'stuck'}")

        print("\nmailbox dump (16 words from base):")
        raw = g.read(BASE, 64)
        words = [int.from_bytes(raw[i:i + 4], 'little')
                 for i in range(0, len(raw), 4)]
        names = ["magic", "lo", "gain", "host_req", "mcu_stamp",
                 "adc_i", "adc_q", "adc_stamp", "", "", "", "", "", "", "", ""]
        for i, w in enumerate(words):
            label = names[i] if i < len(names) else ""
            print(f"   +0x{i * 4:02x}  0x{w:08x}  {label}")

        g.close()
        return 0
    finally:
        qemu.kill()
        qemu.wait(timeout=10)


if __name__ == "__main__":
    sys.exit(main())
