#!/usr/bin/env python3
"""Reconstruct the GD32E230 firmware's initialized .data/SRAM image.

armlink stores .data compressed in flash and unpacks it at boot via a scatter
region table of 4-word entries {src_lma, dst_vma, size, decompress_fn}. This
reads that table from the Intel HEX and applies the vendor's exact decompressor
(decompiled `FUN_08000a30`: nibble-encoded copy-run / zero-run RLE) to rebuild
the runtime SRAM image, so absolute-addressed tables (the S3KM111L register
tables at 0x20000010 etc.) can be read directly.

Usage:
  tools/unpack_data.py <firmware.hex>              # list regions, write sram.bin
  tools/unpack_data.py <firmware.hex> --at 0x20000010 --stride 4 --n 128
"""
import argparse
import os

# Reuse load_hex()/word()/decode() from hexconst.py in the same directory.
_hc = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hexconst.py")
exec(open(_hc).read().split("def main")[0])


def rle_decompress(src, out_size):
    """Exact port of FUN_08000a30: copy-run (low nibble) + zero-run (high
    nibble); a 0 nibble escapes to a following full-byte count. Each run length
    is (count - 1), matching the vendor's pre-decrement loops."""
    out = bytearray()
    i = 0
    while len(out) < out_size:
        b = src[i]; i += 1
        copy = b & 0xF
        if copy == 0:
            copy = src[i]; i += 1
        zero = b >> 4
        if zero == 0:
            zero = src[i]; i += 1
        for _ in range(copy - 1):
            out.append(src[i]); i += 1
        out.extend(b"\x00" * (zero - 1))
    return bytes(out[:out_size])


def find_region_table(mem):
    """Scan flash for 4-word scatter entries {src, dst(SRAM), size, fn}."""
    regs = []
    for a in range(0x08000000, 0x08010000 - 16, 4):
        src = word(mem, a); dst = word(mem, a + 4)
        size = word(mem, a + 8); fn = word(mem, a + 12)
        if src is None or dst is None:
            continue
        if (0x08000000 <= src < 0x08010000 and 0x20000000 <= dst < 0x20002000
                and size and size < 0x2000 and fn and 0x08000000 <= fn < 0x08010000):
            regs.append((a, src, dst, size, fn))
    return regs


def build_sram(mem, regs):
    sram = bytearray(0x2000)
    for _a, src, dst, size, _fn in regs:
        raw = bytes(mem.get(src + k, 0) for k in range(size * 2))  # upper bound on compressed len
        data = rle_decompress(raw, size)
        off = dst - 0x20000000
        sram[off:off + size] = data
    return sram


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("hex")
    ap.add_argument("--at", help="SRAM addr to dump as a {reg,pad,val16} table")
    ap.add_argument("--stride", type=lambda x: int(x, 0), default=4)
    ap.add_argument("--n", type=int, default=128)
    ap.add_argument("--out", default="sram.bin")
    a = ap.parse_args()
    mem = load_hex(a.hex)
    regs = find_region_table(mem)
    print("# scatter regions:")
    for _a, src, dst, size, fn in regs:
        print("#   src=0x%08x dst=0x%08x size=0x%-4x fn=0x%08x" % (src, dst, size, fn))
    sram = build_sram(mem, regs)
    open(a.out, "wb").write(sram)
    print("# wrote %s (0x2000 bytes, SRAM image @0x20000000)" % a.out)
    if a.at:
        base = int(a.at, 0) - 0x20000000
        print("\n# table @ %s  (stride %d)" % (a.at, a.stride))
        print("idx  reg   val     dec")
        for i in range(a.n):
            o = base + i * a.stride
            reg = sram[o]; pad = sram[o + 1]; val = sram[o + 2] | sram[o + 3] << 8
            if reg == 0 and pad == 0 and val == 0:
                print("[%3d] <end>" % i); break
            print("[%3d] 0x%02x  0x%04x  %d%s" % (i, reg, val, val, "  pad=0x%02x" % pad if pad else ""))


if __name__ == "__main__":
    main()
