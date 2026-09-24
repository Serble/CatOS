#!/usr/bin/env python3
"""Pack the text console font (src/kernel/drivers/font.txt) into a 1bpp blob.

The source draws each 8x16 glyph as a `glyph <code>` line followed by 16 rows
of 8 characters, '#' for a set pixel and '.' for a clear one. Lines starting
with ';' and blank lines are ignored. Glyphs must be listed in order from 0,
since the code is the glyph's index in the output (and its PPU tile number).

The output is 1 bit per pixel, MSB = leftmost column, one byte per row, 16
bytes per glyph:

    glyph N bytes = font.bin[N*16 .. N*16+16)
    row R of glyph N = font.bin[N*16 + R]; bit (7 - x) is pixel (x, R)

Usage: python mkfont.py <font.txt> <font.bin>
"""

import sys

GLYPH_W = 8
GLYPH_H = 16


def fail(src: str, lineno: int, msg: str) -> None:
    print(f"{src}:{lineno}: {msg}", file=sys.stderr)
    sys.exit(1)


def main() -> int:
    src, dst = sys.argv[1], sys.argv[2]
    out = bytearray()
    glyphs = 0
    rows = GLYPH_H                       # rows still owed by the current glyph

    for lineno, line in enumerate(open(src), 1):
        line = line.rstrip("\n")
        if not line.strip() or line.startswith(";"):
            continue
        if line.startswith("glyph"):
            if rows != GLYPH_H:
                fail(src, lineno, f"glyph {glyphs - 1} has {rows} rows, "
                                  f"want {GLYPH_H}")
            parts = line.split()
            if len(parts) < 2 or not parts[1].isdigit():
                fail(src, lineno, "expected `glyph <code>`")
            if int(parts[1]) != glyphs:
                fail(src, lineno, f"glyph {parts[1]} out of order, "
                                  f"expected {glyphs}")
            glyphs += 1
            rows = 0
            continue
        if rows == GLYPH_H:
            fail(src, lineno, "pixel row outside a glyph")
        if len(line) != GLYPH_W or set(line) - {"#", "."}:
            fail(src, lineno, f"row must be {GLYPH_W} of '#' / '.'")
        b = 0
        for x, c in enumerate(line):
            if c == "#":
                b |= 1 << (7 - x)
        out.append(b)
        rows += 1

    if rows != GLYPH_H:
        fail(src, lineno, f"glyph {glyphs - 1} has {rows} rows, want {GLYPH_H}")

    with open(dst, "wb") as f:
        f.write(out)
    print(f"{glyphs} glyphs -> {len(out)} bytes ({GLYPH_W}x{GLYPH_H}, 1bpp)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
