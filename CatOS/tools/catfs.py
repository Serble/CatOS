"""CatFS - the format, allocator, and journal engine.

Reference implementation shared by mkfs_catfs.py and catfs_fuse.py. See
../../CatFS.md for the on-disk format this file is the source of truth for.

Everything is 512-byte blocks, little-endian. A CatFS lives inside one
partition; block numbers here are partition-relative (block 0 = superblock).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

BLOCK_SIZE = 512
CATFS_MAGIC = 0x46746143        # bytes 43 61 74 46 = "CatF"
JOURNAL_MAGIC = 0x316C6E4A      # bytes 4A 6E 6C 31 = "Jnl1"
VERSION = 1

# inode types
T_FREE = 0
T_FILE = 1
T_DIR = 2

INODE_SIZE = 64
INODES_PER_BLOCK = BLOCK_SIZE // INODE_SIZE      # 8
DIRECT = 8                                        # direct block pointers / inode
PTRS_PER_BLOCK = BLOCK_SIZE // 4                  # 128 pointers in an indirect blk
MAX_FILE = (DIRECT + PTRS_PER_BLOCK) * BLOCK_SIZE

DIRENT_SIZE = 32
DIRENTS_PER_BLOCK = BLOCK_SIZE // DIRENT_SIZE     # 16
NAME_MAX = 27

ROOT_INODE = 1

# journal states
JSTATE_EMPTY = 0
JSTATE_COMMITTED = 1

# inode field offsets
I_TYPE, I_SIZE, I_LINKS, I_FLAGS = 0x00, 0x04, 0x08, 0x0C
I_DIRECT = 0x10                                   # DIRECT * u32
I_INDIRECT = 0x30

# superblock field offsets
S_MAGIC, S_VERSION, S_BLOCK_SIZE, S_TOTAL_BLOCKS = 0x00, 0x04, 0x08, 0x0C
S_INODE_COUNT = 0x10
S_IBITMAP_START, S_IBITMAP_BLOCKS = 0x14, 0x18
S_DBITMAP_START, S_DBITMAP_BLOCKS = 0x1C, 0x20
S_ITABLE_START, S_ITABLE_BLOCKS = 0x24, 0x28
S_DATA_START, S_DATA_BLOCKS = 0x2C, 0x30
S_JOURNAL_START, S_JOURNAL_BLOCKS = 0x34, 0x38
S_ROOT_INODE, S_STATE = 0x3C, 0x40


class BlockDev:
    """Partition-relative block access over an underlying seekable file."""

    def __init__(self, f, base_block=0):
        self.f = f
        self.base = base_block

    def read_block(self, blk: int) -> bytearray:
        self.f.seek((self.base + blk) * BLOCK_SIZE)
        data = self.f.read(BLOCK_SIZE)
        if len(data) < BLOCK_SIZE:
            data = data + b"\x00" * (BLOCK_SIZE - len(data))
        return bytearray(data)

    def write_block(self, blk: int, data) -> None:
        b = bytes(data).ljust(BLOCK_SIZE, b"\x00")
        assert len(b) == BLOCK_SIZE, "block write must be <= 512 bytes"
        self.f.seek((self.base + blk) * BLOCK_SIZE)
        self.f.write(b)
        self.f.flush()


@dataclass
class Layout:
    total_blocks: int
    journal_start: int
    journal_blocks: int
    ibitmap_start: int
    ibitmap_blocks: int
    dbitmap_start: int
    dbitmap_blocks: int
    itable_start: int
    itable_blocks: int
    data_start: int
    data_blocks: int
    inode_count: int


def _ceil_div(a, b):
    return (a + b - 1) // b


def plan_layout(total_blocks: int, journal_blocks: int = 64) -> Layout:
    """Work out region sizes for a partition of `total_blocks` blocks."""
    if total_blocks < 16:
        raise ValueError("partition too small for CatFS")
    journal_blocks = max(3, min(journal_blocks, total_blocks // 4))

    # one inode per ~4 data blocks, at least 64, rounded to a full block.
    inode_count = max(64, total_blocks // 4)
    inode_count = _ceil_div(inode_count, INODES_PER_BLOCK) * INODES_PER_BLOCK
    itable_blocks = inode_count // INODES_PER_BLOCK
    ibitmap_blocks = _ceil_div(inode_count, BLOCK_SIZE * 8)

    journal_start = 1
    ibitmap_start = journal_start + journal_blocks
    dbitmap_start = ibitmap_start + ibitmap_blocks

    fixed = dbitmap_start  # blocks used before the data bitmap
    avail = total_blocks - fixed - itable_blocks
    if avail <= 1:
        raise ValueError("partition too small for CatFS")
    # each data-bitmap block covers BLOCK_SIZE*8 data blocks; solve the
    # circular dependency between bitmap size and data size.
    dbitmap_blocks = _ceil_div(avail, BLOCK_SIZE * 8 + 1)
    itable_start = dbitmap_start + dbitmap_blocks
    data_start = itable_start + itable_blocks
    data_blocks = total_blocks - data_start
    if data_blocks <= 0:
        raise ValueError("partition too small for CatFS")

    return Layout(
        total_blocks=total_blocks,
        journal_start=journal_start, journal_blocks=journal_blocks,
        ibitmap_start=ibitmap_start, ibitmap_blocks=ibitmap_blocks,
        dbitmap_start=dbitmap_start, dbitmap_blocks=dbitmap_blocks,
        itable_start=itable_start, itable_blocks=itable_blocks,
        data_start=data_start, data_blocks=data_blocks,
        inode_count=inode_count,
    )


class CatFS:
    """A mounted CatFS. All mutations go through a journalled transaction."""

    def __init__(self, dev: BlockDev):
        self.dev = dev
        self._txn = None          # dict[block]->bytearray while in a transaction
        self.seq = 0
        self._read_superblock()

    # -- superblock ---------------------------------------------------------
    def _read_superblock(self):
        sb = self.dev.read_block(0)
        (magic, version, bs, self.total_blocks, self.inode_count,
         self.ibitmap_start, self.ibitmap_blocks,
         self.dbitmap_start, self.dbitmap_blocks,
         self.itable_start, self.itable_blocks,
         self.data_start, self.data_blocks,
         self.journal_start, self.journal_blocks,
         self.root_inode, self.state) = struct.unpack_from("<17I", sb, 0)
        if magic != CATFS_MAGIC:
            raise ValueError(f"not a CatFS partition (magic {magic:#x})")
        if bs != BLOCK_SIZE:
            raise ValueError("unsupported block size")
        self.max_txn = min(PTRS_PER_BLOCK, self.journal_blocks - 2)

    # -- block cache / transactions ----------------------------------------
    def _get(self, blk: int) -> bytearray:
        if self._txn is not None and blk in self._txn:
            return self._txn[blk]
        return self.dev.read_block(blk)

    def _put(self, blk: int, data):
        b = bytearray(bytes(data).ljust(BLOCK_SIZE, b"\x00"))
        if self._txn is None:
            self.dev.write_block(blk, b)   # only mkfs writes outside a txn
        else:
            self._txn[blk] = b

    def begin(self):
        assert self._txn is None, "nested transaction"
        self._txn = {}

    def _pending(self) -> int:
        return 0 if self._txn is None else len(self._txn)

    def commit(self):
        dirty = self._txn
        self._txn = None
        if not dirty:
            return
        targets = list(dirty.keys())
        if len(targets) > self.max_txn:
            raise ValueError("transaction too large for the journal")
        self._journal_commit(targets, [dirty[t] for t in targets])

    def _write_jheader(self, state, count, seq):
        h = bytearray(BLOCK_SIZE)
        struct.pack_into("<IIII", h, 0, JOURNAL_MAGIC, state, count, seq)
        self.dev.write_block(self.journal_start, h)

    def _journal_commit(self, targets, datas):
        js = self.journal_start
        # 1. data slots
        for i, d in enumerate(datas):
            self.dev.write_block(js + 2 + i, d)
        # 2. descriptor
        desc = bytearray(BLOCK_SIZE)
        for i, t in enumerate(targets):
            struct.pack_into("<I", desc, i * 4, t)
        self.dev.write_block(js + 1, desc)
        # 3. commit point
        self.seq += 1
        self._write_jheader(JSTATE_COMMITTED, len(targets), self.seq)
        # 4. checkpoint to the real locations
        for t, d in zip(targets, datas):
            self.dev.write_block(t, d)
        # 5. done
        self._write_jheader(JSTATE_EMPTY, 0, self.seq)

    def recover(self):
        """Replay a committed-but-not-checkpointed transaction, if any."""
        h = self.dev.read_block(self.journal_start)
        magic, state, count, seq = struct.unpack_from("<IIII", h, 0)
        self.seq = seq
        if magic != JOURNAL_MAGIC or state != JSTATE_COMMITTED:
            return False
        desc = self.dev.read_block(self.journal_start + 1)
        for i in range(count):
            (target,) = struct.unpack_from("<I", desc, i * 4)
            data = self.dev.read_block(self.journal_start + 2 + i)
            self.dev.write_block(target, data)
        self._write_jheader(JSTATE_EMPTY, 0, seq)
        return True

    # -- bitmaps ------------------------------------------------------------
    def _bit_get(self, bitmap_start, idx):
        blk = self._get(bitmap_start + idx // (BLOCK_SIZE * 8))
        off = (idx % (BLOCK_SIZE * 8)) // 8
        return (blk[off] >> (idx % 8)) & 1

    def _bit_set(self, bitmap_start, idx, value):
        bno = bitmap_start + idx // (BLOCK_SIZE * 8)
        blk = bytearray(self._get(bno))
        off = (idx % (BLOCK_SIZE * 8)) // 8
        if value:
            blk[off] |= (1 << (idx % 8))
        else:
            blk[off] &= ~(1 << (idx % 8)) & 0xFF
        self._put(bno, blk)

    def alloc_block(self) -> int:
        for d in range(self.data_blocks):
            if not self._bit_get(self.dbitmap_start, d):
                self._bit_set(self.dbitmap_start, d, 1)
                blk = self.data_start + d
                self._put(blk, bytearray(BLOCK_SIZE))  # zero fresh blocks
                return blk
        raise OSError("no free data blocks")

    def free_block(self, blk: int):
        if blk == 0:
            return
        self._bit_set(self.dbitmap_start, blk - self.data_start, 0)

    def alloc_inode(self) -> int:
        for i in range(1, self.inode_count):
            if not self._bit_get(self.ibitmap_start, i):
                self._bit_set(self.ibitmap_start, i, 1)
                return i
        raise OSError("no free inodes")

    def free_inode(self, ino: int):
        self._bit_set(self.ibitmap_start, ino, 0)

    # -- inodes -------------------------------------------------------------
    def _inode_loc(self, ino):
        return (self.itable_start + ino // INODES_PER_BLOCK,
                (ino % INODES_PER_BLOCK) * INODE_SIZE)

    def read_inode(self, ino: int) -> dict:
        bno, off = self._inode_loc(ino)
        blk = self._get(bno)
        typ, size, links, flags = struct.unpack_from("<4I", blk, off)
        direct = list(struct.unpack_from(f"<{DIRECT}I", blk, off + I_DIRECT))
        (indirect,) = struct.unpack_from("<I", blk, off + I_INDIRECT)
        return {"ino": ino, "type": typ, "size": size, "links": links,
                "flags": flags, "direct": direct, "indirect": indirect}

    def write_inode(self, node: dict):
        bno, off = self._inode_loc(node["ino"])
        blk = bytearray(self._get(bno))
        struct.pack_into("<4I", blk, off, node["type"], node["size"],
                         node["links"], node["flags"])
        struct.pack_into(f"<{DIRECT}I", blk, off + I_DIRECT, *node["direct"])
        struct.pack_into("<I", blk, off + I_INDIRECT, node["indirect"])
        self._put(bno, blk)

    # -- file block mapping -------------------------------------------------
    def _map_block(self, node, fbi, allocate=False):
        """Return the partition block backing file-block-index `fbi`."""
        if fbi < DIRECT:
            blk = node["direct"][fbi]
            if blk == 0 and allocate:
                blk = self.alloc_block()
                node["direct"][fbi] = blk
                self.write_inode(node)
            return blk
        fbi -= DIRECT
        if fbi >= PTRS_PER_BLOCK:
            raise OSError("file too large")
        ind = node["indirect"]
        if ind == 0:
            if not allocate:
                return 0
            ind = self.alloc_block()
            node["indirect"] = ind
            self.write_inode(node)
        ptrs = self._get(ind)
        (blk,) = struct.unpack_from("<I", ptrs, fbi * 4)
        if blk == 0 and allocate:
            blk = self.alloc_block()
            ptrs = bytearray(self._get(ind))
            struct.pack_into("<I", ptrs, fbi * 4, blk)
            self._put(ind, ptrs)
        return blk

    def read_file(self, node, offset, length) -> bytes:
        size = node["size"]
        if offset >= size:
            return b""
        length = min(length, size - offset)
        out = bytearray()
        while length > 0:
            fbi = offset // BLOCK_SIZE
            bo = offset % BLOCK_SIZE
            n = min(length, BLOCK_SIZE - bo)
            blk = self._map_block(node, fbi, allocate=False)
            chunk = bytes(BLOCK_SIZE) if blk == 0 else self._get(blk)
            out += chunk[bo:bo + n]
            offset += n
            length -= n
        return bytes(out)

    def write_file(self, node, offset, data: bytes) -> int:
        if offset + len(data) > MAX_FILE:
            raise OSError("file too large")
        written = 0
        pos = offset
        mv = memoryview(data)
        while written < len(data):
            fbi = pos // BLOCK_SIZE
            bo = pos % BLOCK_SIZE
            n = min(len(data) - written, BLOCK_SIZE - bo)
            blk = self._map_block(node, fbi, allocate=True)
            buf = bytearray(self._get(blk))
            buf[bo:bo + n] = mv[written:written + n]
            self._put(blk, buf)
            pos += n
            written += n
            # keep each transaction inside the journal's capacity
            if self._pending() >= self.max_txn - 4:
                if pos > node["size"]:
                    node["size"] = pos
                self.write_inode(node)
                self.commit()
                self.begin()
        if pos > node["size"]:
            node["size"] = pos
        self.write_inode(node)
        return written

    def truncate_file(self, node, newsize):
        # free blocks beyond the new size
        first_free = _ceil_div(newsize, BLOCK_SIZE)
        last = _ceil_div(node["size"], BLOCK_SIZE)
        for fbi in range(first_free, last):
            if fbi < DIRECT:
                if node["direct"][fbi]:
                    self.free_block(node["direct"][fbi])
                    node["direct"][fbi] = 0
            else:
                idx = fbi - DIRECT
                if node["indirect"] and idx < PTRS_PER_BLOCK:
                    ptrs = bytearray(self._get(node["indirect"]))
                    (b,) = struct.unpack_from("<I", ptrs, idx * 4)
                    if b:
                        self.free_block(b)
                        struct.pack_into("<I", ptrs, idx * 4, 0)
                        self._put(node["indirect"], ptrs)
        node["size"] = newsize
        self.write_inode(node)

    # -- directories --------------------------------------------------------
    def dir_entries(self, node):
        """Yield (name, inode) for every live entry in a directory."""
        data = self.read_file(node, 0, node["size"])
        for i in range(0, len(data), DIRENT_SIZE):
            (ino,) = struct.unpack_from("<I", data, i)
            if ino == 0:
                continue
            raw = data[i + 4:i + 4 + NAME_MAX]
            name = raw.split(b"\x00", 1)[0].decode("utf-8", "replace")
            yield name, ino

    def dir_lookup(self, node, name):
        for n, ino in self.dir_entries(node):
            if n == name:
                return ino
        return 0

    def dir_add(self, dnode, name, ino):
        if len(name.encode()) > NAME_MAX:
            raise OSError("name too long")
        ent = bytearray(DIRENT_SIZE)
        struct.pack_into("<I", ent, 0, ino)
        ent[4:4 + len(name.encode())] = name.encode()
        # reuse a free slot if there is one
        data = self.read_file(dnode, 0, dnode["size"])
        for i in range(0, len(data), DIRENT_SIZE):
            (slot,) = struct.unpack_from("<I", data, i)
            if slot == 0:
                self.write_file(dnode, i, bytes(ent))
                return
        self.write_file(dnode, dnode["size"], bytes(ent))

    def dir_remove(self, dnode, name):
        data = self.read_file(dnode, 0, dnode["size"])
        for i in range(0, len(data), DIRENT_SIZE):
            (ino,) = struct.unpack_from("<I", data, i)
            if ino == 0:
                continue
            raw = data[i + 4:i + 4 + NAME_MAX]
            n = raw.split(b"\x00", 1)[0].decode("utf-8", "replace")
            if n == name:
                self.write_file(dnode, i, bytes(DIRENT_SIZE))  # clear the slot
                return ino
        return 0

    def dir_empty(self, node):
        return next(self.dir_entries(node), None) is None

    # -- high-level namespace helpers (each is one or more transactions) ----
    def make_node(self, parent_ino, name, typ):
        self.begin()
        parent = self.read_inode(parent_ino)
        ino = self.alloc_inode()
        node = {"ino": ino, "type": typ, "size": 0, "links": 1,
                "flags": 0, "direct": [0] * DIRECT, "indirect": 0}
        self.write_inode(node)
        self.dir_add(parent, name, ino)
        self.commit()
        return ino

    def unlink(self, parent_ino, name):
        self.begin()
        parent = self.read_inode(parent_ino)
        ino = self.dir_remove(parent, name)
        if ino:
            node = self.read_inode(ino)
            self.truncate_file(node, 0)
            if node["indirect"]:
                self.free_block(node["indirect"])
            node["type"] = T_FREE
            self.write_inode(node)
            self.free_inode(ino)
        self.commit()
        return ino
