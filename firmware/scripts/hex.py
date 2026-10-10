"""Emit firmware.hex alongside firmware.bin/.elf.

PICSimLab's rcontrol `loadhex` takes an Intel HEX image, which this target does
not produce by default. objcopy converts the linked ELF using the same layout
the .bin already uses, so the two images are identical.

    pio run -t hex          # add firmware.hex to the build
    pio run                 # firmware.bin + firmware.elf only

The hex is a separate target rather than part of the default build because
PlatformIO's env.Default() cannot reliably attach a custom target to the
program target for this board (it reports "Do not know how to make File target"
because the target is not a file).

Registered with AddCustomTarget, the documented API for adding a build step.
AddPostAction does not fire for this board: it attaches to the program target,
which a post: script loads after that target has been scheduled.
"""

Import("env")

env.AddCustomTarget(
    name="hex",
    dependencies=["$BUILD_DIR/${PROGNAME}.elf"],
    actions=[
        '"$OBJCOPY" -O ihex '
        '"$BUILD_DIR/${PROGNAME}.elf" '
        '"$BUILD_DIR/${PROGNAME}.hex"'
    ],
    title="Produce Intel HEX for the Blue Pill bootloader / PICSimLab",
    description="objcopy -O ihex firmware.elf firmware.hex",
    always_build=True,
)