# CatFS — a simple journaling filesystem

CatFS is the native read/write filesystem for CatOS. It is deliberately small
and block-oriented, in the same spirit as the rest of CatVM: fixed 512-byte
blocks, little-endian integers, and a handful of plain structures you can read
in a hex dump. It adds just enough machinery — bitmaps, fixed-size inodes, and a
**write-ahead redo journal** — to survive a crash in the middle of an update
without corrupting its metadata.

It lives inside a single partition (see `CatPartitions.md`). A CatFS partition
uses **partition type `1`** in the partition table. All block numbers in the
on-disk structures below are *partition-relative*: block 0 is the first block of
the partition.

* Block size: **512 bytes** (matches the disk device).
* Byte order: **little-endian** (matches CatVM).
* All integers are unsigned 32-bit unless noted.

--------------------------------------------------------------------------------

## 1. On-disk layout

A freshly formatted partition is laid out in this order:

```
 block 0                                  superblock
 [journal_start,     +journal_blocks)     journal
 [ibitmap_start,     +ibitmap_blocks)     inode bitmap
 [dbitmap_start,     +dbitmap_blocks)     data bitmap
 [inode_table_start, +itable_blocks )     inode table
 [data_start,        +data_blocks   )     data region
```

The superblock records the start block and length of every region, so a driver
never hard-codes offsets — it reads the superblock and follows the pointers.

--------------------------------------------------------------------------------

## 2. Superblock (block 0)

| offset | size | field              | notes                                   |
|-------:|-----:|--------------------|-----------------------------------------|
| 0x00   | 4    | `magic`            | `0x46746143` — the bytes `43 61 74 46` = "CatF" |
| 0x04   | 4    | `version`          | `1`                                     |
| 0x08   | 4    | `block_size`       | `512`                                   |
| 0x0C   | 4    | `total_blocks`     | partition size, in blocks               |
| 0x10   | 4    | `inode_count`      | number of inodes in the table           |
| 0x14   | 4    | `ibitmap_start`    | inode bitmap, first block               |
| 0x18   | 4    | `ibitmap_blocks`   |                                         |
| 0x1C   | 4    | `dbitmap_start`    | data bitmap, first block                |
| 0x20   | 4    | `dbitmap_blocks`   |                                         |
| 0x24   | 4    | `inode_table_start`| inode table, first block                |
| 0x28   | 4    | `inode_table_blocks`|                                        |
| 0x2C   | 4    | `data_start`       | data region, first block                |
| 0x30   | 4    | `data_blocks`      | number of data blocks                   |
| 0x34   | 4    | `journal_start`    | journal, first block                    |
| 0x38   | 4    | `journal_blocks`   | journal length (>= 3)                   |
| 0x3C   | 4    | `root_inode`       | always `1`                              |
| 0x40   | 4    | `state`            | `0` clean, `1` mounted (informational)  |
| 0x44   | ...  | reserved           | zero                                    |

--------------------------------------------------------------------------------

## 3. Inodes

Inodes are **64 bytes**, so **8 inodes per block**. Inode numbers are 1-based;
inode `0` is reserved as the "null" pointer and is never allocated.

Inode `N` lives at:

```
 block  = inode_table_start + (N / 8)
 offset = (N % 8) * 64
```

| offset | size | field         | notes                                        |
|-------:|-----:|---------------|----------------------------------------------|
| 0x00   | 4    | `type`        | `0` free, `1` file, `2` directory            |
| 0x04   | 4    | `size`        | logical size in bytes                        |
| 0x08   | 4    | `links`       | link count (1 for files, dirs use 1)         |
| 0x0C   | 4    | `flags`       | reserved bitfield                            |
| 0x10   | 32   | `direct[8]`   | 8 direct block pointers                      |
| 0x30   | 4    | `indirect`    | pointer to a block of 128 block pointers     |
| 0x34   | 12   | reserved      | zero                                         |

Block pointers are **partition-relative block numbers**; `0` means "no block"
(a hole reads as zeros). The indirect block, when present, holds up to 128
`u32` block pointers.

Maximum file size: `(8 + 128) * 512 = 69632` bytes (~68 KiB). This is a
deliberate simplicity limit; a double-indirect pointer could be added later
(the reserved inode bytes leave room).

--------------------------------------------------------------------------------

## 4. Directories

A directory's data is a flat array of **32-byte directory entries** (16 per
block). The directory inode's `size` covers all slots up to the high-water mark,
including freed ones.

| offset | size | field   | notes                                    |
|-------:|-----:|---------|------------------------------------------|
| 0x00   | 4    | `inode` | target inode, or `0` for a free slot     |
| 0x04   | 28   | `name`  | null-padded name, max 27 chars           |

`.` and `..` are **not** stored on disk. Both drivers synthesize them: the
CatOS VFS resolves them through the in-memory parent pointer, and the FUSE
driver emits them from `readdir`. This keeps the on-disk directory a plain list
of real children.

Creating an entry reuses the first free slot (`inode == 0`) if one exists,
otherwise appends. Removing an entry clears its slot to `inode == 0`.

--------------------------------------------------------------------------------

## 5. Bitmaps

Two bitmaps track free space, one bit per object, LSB-first within each byte
(bit `b` = byte `b/8`, mask `1 << (b%8)`). A set bit means **allocated**.

* **Inode bitmap** — bit `i` tracks inode `i`. Bit `0` is permanently set
  (the reserved null inode); bit `1` is set for the root directory.
* **Data bitmap** — bit `d` tracks data-region block `d` (0-based). The
  corresponding partition-relative block is `data_start + d`.

--------------------------------------------------------------------------------

## 6. Journal (write-ahead redo log)

Every metadata *and* data change a driver makes is grouped into a **transaction**
and written to the journal first. Because the disk device completes each write
before the next is issued (writes are synchronous from the driver's point of
view), the on-disk order below is guaranteed, which is what makes the scheme
crash-safe.

The journal occupies `journal_blocks` blocks starting at `journal_start`:

```
 journal_start + 0     header
 journal_start + 1     descriptor  (u32 target[0..count-1], up to 128)
 journal_start + 2     data for target[0]
 journal_start + 3     data for target[1]
 ...
```

**Header** (block `journal_start`):

| offset | size | field   | notes                                  |
|-------:|-----:|---------|----------------------------------------|
| 0x00   | 4    | `magic` | `0x316C6E4A` — bytes `4A 6E 6C 31` = "Jnl1" |
| 0x04   | 4    | `state` | `0` EMPTY, `1` COMMITTED                |
| 0x08   | 4    | `count` | number of block-writes in the txn      |
| 0x0C   | 4    | `seq`   | monotonically increasing txn counter   |

The **descriptor** block holds `count` little-endian `u32` target block numbers
— the real (partition-relative) destination of each journalled data block.

A transaction can hold at most `min(128, journal_blocks - 2)` block writes. A
single high-level operation (create, mkdir, one chunk of a write, unlink,
rename) always fits; large file writes are split into several transactions (so a
big write is not globally atomic, but the filesystem is always consistent
between them).

A filesystem may sit inside a file on another CatFS. Nothing in the on-disk format
changes for that case - the inner image is an ordinary partitioned file - but the driver
has to keep the two instances' scratch buffers apart, which is what `catfs_bounce` in
`fs/catfs.cat` is for. The practical limit is the file size cap: 136 blocks.

A rename is one transaction covering both halves — clearing the old directory
entry and writing the new one — so a crash mid-rename replays both or neither
and the entry can never be lost or duplicated. Because a directory stores no
`.` or `..` entry, moving a directory rewrites nothing inside it: only the two
parent directories' entry tables change, and the inode is untouched.

### 6.1 Commit protocol

To commit a transaction with writes `(target_i -> data_i)`:

1. Write each `data_i` into its journal data slot (`journal_start + 2 + i`).
2. Write the descriptor block (the `target_i` list) to `journal_start + 1`.
3. Write the header with `state = COMMITTED`, `count`, `seq+1`.  ← **commit point**
4. **Checkpoint:** copy each `data_i` from its journal slot to `target_i`.
5. Write the header with `state = EMPTY`.

Steps 1–3 must land before 4 begins; step 3 is the atomic commit point.

### 6.2 Recovery (run at mount)

1. Read the header. If `state != COMMITTED`, there is nothing to do.
2. Otherwise read the descriptor and replay: copy each journal data slot to its
   target block (steps 4).
3. Write the header with `state = EMPTY`.

Replay is idempotent: a crash during replay just replays again with the same
data. A crash *before* the commit point leaves targets untouched, so the partial
transaction is simply discarded.

--------------------------------------------------------------------------------

## 7. Reference implementations

* **`CatOS/tools/catfs.py`** — the format: constants, structure packing, block
  allocation, inode/dirent handling, and the journal engine.
* **`CatOS/tools/mkfs_catfs.py`** — format a raw image or an existing partition.
* **`CatOS/tools/catfs_fuse.py`** — a FUSE driver so a CatFS image can be
  mounted on a normal Linux host for creating and inspecting filesystems.
* **`CatOS/catfs.cat`** — the CatOS driver: a VFS filesystem that can serve as
  the root `/`. Several instances can be mounted at once, including one inside a
  file on another.
* **`CatOS/src/user/catpart.cat`** — `catpart mkfs`, the in-OS formatter. It is
  the userland twin of `mkfs_catfs.py` and its layout arithmetic mirrors
  `plan_layout()`, so a partition formatted inside CatOS mounts on the host and
  vice versa.

All five share this document as their single source of truth for the format.

--------------------------------------------------------------------------------

## 8. Limits (v1)

* Max file size ~68 KiB (8 direct + 128 indirect blocks).
* Max name length 27 bytes.
* Single journalled transaction ≤ `min(128, journal_blocks-2)` blocks.
* No timestamps, permissions, or symlinks yet (fields reserved for growth).
