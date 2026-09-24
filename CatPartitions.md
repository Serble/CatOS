# Partitioning System

This is the standard partitioning system that all of this relies on for disks.

Disk layout
-----------
Block 0 (512 bytes) is the partition table:
    offset  size  field
    0x000      8  magic number (51)
    0x008      4  version
    0x00C      4  partition count
    0x010     16  UUID
    0x020    12*  partition entries (type:u32, startBlock:u32, blockCount:u32)
    0x1FC      4  checksum (not always respected)

Block 0 therefore holds at most 39 entries ((0x1FC - 0x20) / 12), which is the
limit both the CatOS tools and the host tools enforce.

Partition type 0 is a raw application: the block range simply contains the
application bytes, zero-padded to a whole number of 512-byte blocks.
Partition type 1 is a CatFS filesystem (see `CatFS.md`).

Inside CatOS, `parts` lists a table and `catpart` creates one; on the host,
`CatFirmware/make_disk.py` builds a partitioned image and
`CatOS/tools/mkfs_catfs.py` formats a partition in it.

