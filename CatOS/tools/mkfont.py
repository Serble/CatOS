#!/usr/bin/env python3
"""Convert the x86 testos font (data/font.asm) into a compact 1bpp bitmap.

The source stores each glyph as a full 8x16 grid of 32-bit pixels (one `dd`
dword per pixel; non-zero = foreground). That is 512 bytes per glyph, far too
large to embed. This packs it to 1 bit per pixel, MSB = leftmost column, one
byte per row, 16 bytes per glyph:

    glyph N bytes = font.bin[N*16 .. N*16+16)
    row R of glyph N = font.bin[N*16 + R]; bit (7 - x) is pixel (x, R)

Usage: python mkfont.py <font.asm> <font.bin>
"""

import re
import sys

GLYPH_W = 8
GLYPH_H = 16
PIXELS_PER_GLYPH = GLYPH_W * GLYPH_H

HEX = re.compile(r"0x[0-9A-Fa-f]+")


def main() -> int:
    src, dst = sys.argv[1], sys.argv[2]
    pixels = []
    for line in open(src):
        line = line.split(";", 1)[0]           # strip comments
        if "dd" not in line:
            continue
        for tok in HEX.findall(line):
            pixels.append(1 if int(tok, 16) != 0 else 0)

    if len(pixels) % PIXELS_PER_GLYPH != 0:
        print(f"warning: {len(pixels)} pixels is not a whole number of "
              f"{PIXELS_PER_GLYPH}-pixel glyphs", file=sys.stderr)
    glyphs = len(pixels) // PIXELS_PER_GLYPH

    out = bytearray()
    for g in range(glyphs):
        base = g * PIXELS_PER_GLYPH
        for row in range(GLYPH_H):
            b = 0
            for x in range(GLYPH_W):
                if pixels[base + row * GLYPH_W + x]:
                    b |= 1 << (7 - x)
            out.append(b)

    with open(dst, "wb") as f:
        f.write(out)
    print(f"{glyphs} glyphs -> {len(out)} bytes ({GLYPH_W}x{GLYPH_H}, 1bpp)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
