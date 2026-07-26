#!/usr/bin/env python3
"""Overwrite one raw partition's bytes in a CatVM disk image in place.

Used by `packandrun.sh --keep` to drop a freshly built kernel back into
partition 0 without disturbing the CatFS partition, so its contents (and any
journal state) survive across a simulated reboot.

    python3 replace_kernel.py disk.img kernel.bin <partition-index>
"""
import struct
import sys

BLOCK = 512
PT_ENTRIES_OFF = 0x20
PT_ENTRY_SIZE = 12


def main():
    disk, kernel, idx = sys.argv[1], sys.argv[2], int(sys.argv[3])
    payload = open(kernel, "rb").read()
    with open(disk, "r+b") as f:
        hdr = f.read(BLOCK)
        off = PT_ENTRIES_OFF + idx * PT_ENTRY_SIZE
        _typ, start, blocks = struct.unpack_from("<III", hdr, off)
        cap = blocks * BLOCK
        if len(payload) > cap:
            sys.exit(f"kernel ({len(payload)} B) exceeds partition {idx} "
                     f"capacity ({cap} B); run without --keep")
        f.seek(start * BLOCK)
        f.write(payload.ljust(cap, b"\x00"))
    print(f"replaced partition {idx} kernel ({len(payload)} bytes)")


if __name__ == "__main__":
    main()
