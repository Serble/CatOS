#!/usr/bin/env python3
"""catfs_import - populate a CatFS partition from the host.

Copies host files into a CatFS filesystem and creates directories, so the build
can lay the user-mode programs into /bin (and the /dev, /sys mount points) on the
on-disk root filesystem before the VM boots.

  # into partition 1 of a CatVM disk
  python catfs_import.py --disk disk.img --partition 1 \
      --mkdir /bin --mkdir /dev --mkdir /sys \
      --put build/init.bin:/bin/init --put build/hello.bin:/bin/hello

  # into a standalone image
  python catfs_import.py fs.img --mkdir /bin --put a.bin:/bin/a

Directories are created mkdir -p style and existing entries are replaced, so
re-running is idempotent. See ../../CatFS.md for the format.
"""

from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

import catfs
from catfs import BLOCK_SIZE, T_DIR, T_FILE, ROOT_INODE

PT_MAGIC = 51
PT_COUNT_OFF = 0x0C
PT_ENTRIES_OFF = 0x20
PT_ENTRY_SIZE = 12


def _partition_base(f, index: int) -> int:
    f.seek(0)
    hdr = f.read(BLOCK_SIZE)
    (magic,) = struct.unpack_from("<I", hdr, 0)
    if magic != PT_MAGIC:
        raise SystemExit("disk has no valid partition table")
    (count,) = struct.unpack_from("<I", hdr, PT_COUNT_OFF)
    if index >= count:
        raise SystemExit(f"partition {index} out of range (count {count})")
    off = PT_ENTRIES_OFF + index * PT_ENTRY_SIZE
    _typ, start, _blocks = struct.unpack_from("<III", hdr, off)
    return start


def _split(path: str):
    return [p for p in path.split("/") if p]


def ensure_dir(fs: catfs.CatFS, path: str) -> int:
    """mkdir -p; returns the directory's inode."""
    ino = ROOT_INODE
    for part in _split(path):
        parent = fs.read_inode(ino)
        if parent["type"] != T_DIR:
            raise SystemExit(f"{path}: {part} parent is not a directory")
        child = fs.dir_lookup(parent, part)
        if child == 0:
            child = fs.make_node(ino, part, T_DIR)
        ino = child
    return ino


def put_file(fs: catfs.CatFS, src: str, dst: str) -> int:
    data = Path(src).read_bytes()
    parts = _split(dst)
    if not parts:
        raise SystemExit(f"bad destination path: {dst}")
    name = parts[-1]
    parent_ino = ensure_dir(fs, "/" + "/".join(parts[:-1]))
    parent = fs.read_inode(parent_ino)
    if fs.dir_lookup(parent, name):        # replace any existing entry
        fs.unlink(parent_ino, name)
    ino = fs.make_node(parent_ino, name, T_FILE)
    node = fs.read_inode(ino)
    fs.write_file(node, 0, data)           # offline: writes straight through
    return len(data)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Populate a CatFS partition.")
    ap.add_argument("image", type=Path, help="image or disk file")
    ap.add_argument("--disk", action="store_true",
                    help="treat `image` as a partitioned CatVM disk")
    ap.add_argument("--partition", type=int, default=0,
                    help="partition index (with --disk)")
    ap.add_argument("--mkdir", action="append", default=[], metavar="DIR",
                    help="create a directory (repeatable, mkdir -p)")
    ap.add_argument("--put", action="append", default=[], metavar="SRC:DST",
                    help="copy a host file to DST (repeatable)")
    args = ap.parse_args(argv)

    with open(args.image, "r+b") as f:
        base = _partition_base(f, args.partition) if args.disk else 0
        dev = catfs.BlockDev(f, base_block=base)
        fs = catfs.CatFS(dev)
        fs.recover()                       # replay any pending journal first

        for d in args.mkdir:
            ensure_dir(fs, d)
            print(f"mkdir  {d}")
        for spec in args.put:
            if ":" not in spec:
                ap.error(f"--put needs SRC:DST, got {spec!r}")
            src, dst = spec.split(":", 1)
            n = put_file(fs, src, dst)
            print(f"put    {src} -> {dst} ({n} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
