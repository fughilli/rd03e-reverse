#!/usr/bin/env python3
"""Resolve ARM literal-pool constants (Ghidra `DAT_08xxxxxx`) to their values.

Ghidra's decompiler leaves Cortex-M literal-pool loads as `DAT_<addr>` symbols
without showing the loaded word. This reads the firmware image (Intel HEX),
builds a sparse address->byte map, and for each flash address given prints the
32-bit little-endian word stored there -- which is the real peripheral base or
config constant the code uses. If that word is itself a known GD32E230
peripheral register, it is decoded.

Usage:
  tools/hexconst.py <firmware.hex> 0x080047cc 0x080082a8 ...
  tools/hexconst.py <firmware.hex> --grep out/2268/decomp/FUN_08004758@*.c   # pull DAT_ addrs from a decomp file
"""
import re
import sys

PERI = [
    (0x40000400, "TIMER2"), (0x40001000, "TIMER5"), (0x40002000, "TIMER13"),
    (0x40003800, "SPI1"), (0x40004400, "USART1"), (0x40005400, "I2C0"),
    (0x40005800, "I2C1"), (0x40006c00, "PMU"), (0x40007000, "DAC"),
    (0x40010000, "SYSCFG"), (0x40010400, "EXTI"), (0x40012400, "ADC"),
    (0x40012c00, "TIMER0"), (0x40013000, "SPI0"), (0x40013800, "USART0"),
    (0x40014400, "TIMER15"), (0x40014800, "TIMER16"), (0x40020000, "DMA"),
    (0x40021000, "RCU"), (0x40022000, "FMC"),
    (0x48000000, "GPIOA"), (0x48000400, "GPIOB"), (0x48000800, "GPIOC"),
    (0x48001400, "GPIOF"),
]


def load_hex(path):
    mem = {}
    base = 0
    for line in open(path):
        line = line.strip()
        if not line.startswith(":"):
            continue
        n = int(line[1:3], 16)
        addr = int(line[3:7], 16)
        typ = int(line[7:9], 16)
        data = bytes.fromhex(line[9:9 + n * 2])
        if typ == 0x04:  # extended linear address: upper 16 bits
            base = int.from_bytes(data, "big") << 16
        elif typ == 0x02:
            base = int.from_bytes(data, "big") << 4
        elif typ == 0x00:
            for i, b in enumerate(data):
                mem[base + addr + i] = b
    return mem


def word(mem, a):
    try:
        return mem[a] | mem[a + 1] << 8 | mem[a + 2] << 16 | mem[a + 3] << 24
    except KeyError:
        return None


def decode(v):
    if v is None:
        return "<not in image>"
    for lo, name in sorted(PERI, reverse=True):
        if lo <= v < lo + 0x400:
            off = v - lo
            return "%s+0x%02x" % (name, off) if off else name
    if 0x08000000 <= v < 0x08010000:
        return "flash ptr"
    if 0x20000000 <= v < 0x20002000:
        return "SRAM ptr"
    return ""


def main():
    hexf = sys.argv[1]
    mem = load_hex(hexf)
    args = sys.argv[2:]
    addrs = []
    if args and args[0] == "--grep":
        for f in args[1:]:
            txt = open(f, errors="replace").read()
            addrs += [int(x, 16) for x in sorted(set(re.findall(r'DAT_(08[0-9a-fA-F]{6})', txt)))]
        addrs = sorted(set(addrs))
    else:
        addrs = [int(a, 16) for a in args]
    for a in addrs:
        v = word(mem, a)
        vs = "0x%08x" % v if v is not None else "?"
        print("DAT_%08x -> %s  %s" % (a, vs, decode(v)))


if __name__ == "__main__":
    main()
