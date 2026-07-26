#!/usr/bin/env python3
"""mkfs.catfs - format a CatFS filesystem.

Two modes:

  # format a standalone raw image of a given size (creates the file)
  python mkfs_catfs.py --size 4M fs.img

  # format partition N of an existing CatVM disk image (reads its
  # partition table; the partition's type is rewritten to 1 = CatFS)
  python mkfs_catfs.py --disk disk.img --partition 0

See ../../CatFS.md for the layout this produces.
"""

from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

import catfs
from catfs import BLOCK_SIZE, CATFS_MAGIC, VERSION, T_DIR, ROOT_INODE

PT_MAGIC = 51
PT_COUNT_OFF = 0x0C
PT_ENTRIES_OFF = 0x20
PT_ENTRY_SIZE = 12
PART_TYPE_CATFS = 1


def _parse_size(s: str) -> int:
    s = s.strip().upper()
    mult = 1
    if s.endswith("K"):
        mult, s = 1024, s[:-1]
    elif s.endswith("M"):
        mult, s = 1024 * 1024, s[:-1]
    elif s.endswith("G"):
        mult, s = 1024 * 1024 * 1024, s[:-1]
    return int(float(s) * mult)


def format_region(f, base_block: int, total_blocks: int, journal_blocks: int):
    """Write a fresh CatFS into `total_blocks` blocks starting at base_block."""
    lay = catfs.plan_layout(total_blocks, journal_blocks=journal_blocks)
    dev = catfs.BlockDev(f, base_block=base_block)

    # zero every region that must start empty (bitmaps, inode table, journal
    # header). Data blocks are left as-is; the bitmap marks them free.
    for b in range(lay.journal_start, lay.data_start):
        dev.write_block(b, bytearray(BLOCK_SIZE))

    # superblock
    sb = bytearray(BLOCK_SIZE)
    struct.pack_into(
        "<17I", sb, 0,
        CATFS_MAGIC, VERSION, BLOCK_SIZE, lay.total_blocks, lay.inode_count,
        lay.ibitmap_start, lay.ibitmap_blocks,
        lay.dbitmap_start, lay.dbitmap_blocks,
        lay.itable_start, lay.itable_blocks,
        lay.data_start, lay.data_blocks,
        lay.journal_start, lay.journal_blocks,
        ROOT_INODE, 0,
    )
    dev.write_block(0, sb)

    # journal header: empty
    fs = catfs.CatFS(dev)
    fs._write_jheader(catfs.JSTATE_EMPTY, 0, 0)

    # reserve inode 0 (null) and inode 1 (root) in the inode bitmap, then build
    # an empty root directory. Done outside the journal (nothing to recover from
    # during mkfs).
    fs._bit_set(fs.ibitmap_start, 0, 1)
    fs._bit_set(fs.ibitmap_start, ROOT_INODE, 1)
    root = {"ino": ROOT_INODE, "type": T_DIR, "size": 0, "links": 1,
            "flags": 0, "direct": [0] * catfs.DIRECT, "indirect": 0}
    fs.write_inode(root)

    return lay


def read_partition(f, index: int):
    f.seek(0)
    hdr = f.read(BLOCK_SIZE)
    (magic,) = struct.unpack_from("<I", hdr, 0)
    if magic != PT_MAGIC:
        raise SystemExit("disk has no valid partition table")
    (count,) = struct.unpack_from("<I", hdr, PT_COUNT_OFF)
    if index >= count:
        raise SystemExit(f"partition {index} out of range (count {count})")
    off = PT_ENTRIES_OFF + index * PT_ENTRY_SIZE
    typ, start, blocks = struct.unpack_from("<III", hdr, off)
    return typ, start, blocks


def set_partition_type(f, index: int, new_type: int):
    off = PT_ENTRIES_OFF + index * PT_ENTRY_SIZE
    f.seek(off)
    f.write(struct.pack("<I", new_type))
    f.flush()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Format a CatFS filesystem.")
    ap.add_argument("image", type=Path, help="image or disk file")
    ap.add_argument("--size", help="size for a standalone image, e.g. 4M")
    ap.add_argument("--disk", action="store_true",
                    help="treat `image` as a partitioned CatVM disk")
    ap.add_argument("--partition", type=int, default=0,
                    help="partition index to format (with --disk)")
    ap.add_argument("--journal", type=int, default=64,
                    help="journal size in blocks (default 64)")
    args = ap.parse_args(argv)

    if args.disk:
        with open(args.image, "r+b") as f:
            typ, start, blocks = read_partition(f, args.partition)
            print(f"partition {args.partition}: type {typ}, "
                  f"start {start}, {blocks} blocks")
            lay = format_region(f, start, blocks, args.journal)
            set_partition_type(f, args.partition, PART_TYPE_CATFS)
    else:
        if not args.size:
            ap.error("--size is required unless --disk is given")
        nbytes = _parse_size(args.size)
        total_blocks = nbytes // BLOCK_SIZE
        with open(args.image, "w+b") as f:
            f.truncate(total_blocks * BLOCK_SIZE)
            lay = format_region(f, 0, total_blocks, args.journal)

    print(f"CatFS: {lay.total_blocks} blocks, {lay.inode_count} inodes, "
          f"journal {lay.journal_blocks} blk, data {lay.data_blocks} blk "
          f"(data starts at block {lay.data_start})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
