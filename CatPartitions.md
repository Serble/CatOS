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

Partition type 0 is a raw application: the block range simply contains the
application bytes, zero-padded to a whole number of 512-byte blocks.

