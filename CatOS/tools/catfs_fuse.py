#!/usr/bin/env python3
"""catfs_fuse - mount a CatFS image on a Linux host with FUSE.

Lets you build and inspect CatFS filesystems with ordinary tools before handing
the image to CatOS. Backed by catfs.py, so it exercises exactly the same format
and journal engine the OS driver targets.

  # standalone image
  python catfs_fuse.py fs.img /mnt/catfs

  # a partition inside a CatVM disk image
  python catfs_fuse.py --disk disk.img --partition 0 /mnt/catfs

  # unmount: fusermount -u /mnt/catfs   (or Ctrl-C in the foreground)

Requires fusepy (pip install fusepy) and libfuse. See ../../CatFS.md.
"""

from __future__ import annotations

import argparse
import errno
import stat
import struct
import sys
from pathlib import Path

from fuse import FUSE, FuseOSError, Operations

import catfs
from catfs import BLOCK_SIZE, T_DIR, T_FILE, ROOT_INODE

PT_ENTRIES_OFF = 0x20
PT_ENTRY_SIZE = 12


class CatFSFuse(Operations):
    def __init__(self, fs: catfs.CatFS):
        self.fs = fs
        self.fs.recover()   # replay any committed transaction from a prior crash

    # -- path resolution ----------------------------------------------------
    def _resolve(self, path):
        ino = ROOT_INODE
        node = self.fs.read_inode(ino)
        if path in ("/", ""):
            return node
        for part in path.strip("/").split("/"):
            if node["type"] != T_DIR:
                raise FuseOSError(errno.ENOTDIR)
            child = self.fs.dir_lookup(node, part)
            if child == 0:
                raise FuseOSError(errno.ENOENT)
            node = self.fs.read_inode(child)
        return node

    def _parent(self, path):
        p = path.rstrip("/")
        parent, _, name = p.rpartition("/")
        return self._resolve(parent or "/"), name

    # -- fuse ops -----------------------------------------------------------
    def getattr(self, path, fh=None):
        node = self._resolve(path)
        if node["type"] == T_DIR:
            mode = stat.S_IFDIR | 0o755
            nlink = 2
        else:
            mode = stat.S_IFREG | 0o644
            nlink = 1
        return {
            "st_mode": mode,
            "st_nlink": nlink,
            "st_size": node["size"],
            "st_ino": node["ino"],
            "st_uid": 0, "st_gid": 0,
            "st_atime": 0, "st_mtime": 0, "st_ctime": 0,
        }

    def readdir(self, path, fh):
        node = self._resolve(path)
        if node["type"] != T_DIR:
            raise FuseOSError(errno.ENOTDIR)
        yield "."
        yield ".."
        for name, _ino in self.fs.dir_entries(node):
            yield name

    def read(self, path, size, offset, fh):
        node = self._resolve(path)
        if node["type"] != T_FILE:
            raise FuseOSError(errno.EISDIR)
        return self.fs.read_file(node, offset, size)

    def write(self, path, data, offset, fh):
        parent, name = self._parent(path)
        ino = self.fs.dir_lookup(parent, name)
        if ino == 0:
            raise FuseOSError(errno.ENOENT)
        self.fs.begin()
        node = self.fs.read_inode(ino)
        n = self.fs.write_file(node, offset, bytes(data))
        self.fs.commit()
        return n

    def create(self, path, mode, fi=None):
        parent, name = self._parent(path)
        if self.fs.dir_lookup(parent, name):
            raise FuseOSError(errno.EEXIST)
        self.fs.make_node(parent["ino"], name, T_FILE)
        return 0

    def mkdir(self, path, mode):
        parent, name = self._parent(path)
        if self.fs.dir_lookup(parent, name):
            raise FuseOSError(errno.EEXIST)
        self.fs.make_node(parent["ino"], name, T_DIR)

    def unlink(self, path):
        parent, name = self._parent(path)
        node = self._resolve(path)
        if node["type"] == T_DIR:
            raise FuseOSError(errno.EISDIR)
        self.fs.unlink(parent["ino"], name)

    def rmdir(self, path):
        parent, name = self._parent(path)
        node = self._resolve(path)
        if node["type"] != T_DIR:
            raise FuseOSError(errno.ENOTDIR)
        if not self.fs.dir_empty(node):
            raise FuseOSError(errno.ENOTEMPTY)
        self.fs.unlink(parent["ino"], name)

    def truncate(self, path, length, fh=None):
        node = self._resolve(path)
        self.fs.begin()
        node = self.fs.read_inode(node["ino"])
        self.fs.truncate_file(node, length)
        self.fs.commit()

    def rename(self, old, new):
        oparent, oname = self._parent(old)
        nparent, nname = self._parent(new)
        ino = self.fs.dir_lookup(oparent, oname)
        if ino == 0:
            raise FuseOSError(errno.ENOENT)
        self.fs.begin()
        op = self.fs.read_inode(oparent["ino"])
        self.fs.dir_remove(op, oname)
        np = self.fs.read_inode(nparent["ino"])
        # drop an existing target
        existing = self.fs.dir_lookup(np, nname)
        if existing:
            self.fs.dir_remove(np, nname)
        self.fs.dir_add(np, nname, ino)
        self.fs.commit()

    # no-ops that keep common tools happy
    def chmod(self, path, mode):
        return 0

    def chown(self, path, uid, gid):
        return 0

    def utimens(self, path, times=None):
        return 0

    def flush(self, path, fh):
        return 0

    def fsync(self, path, datasync, fh):
        return 0

    def statfs(self, path):
        fs = self.fs
        return {
            "f_bsize": BLOCK_SIZE, "f_frsize": BLOCK_SIZE,
            "f_blocks": fs.data_blocks, "f_bfree": fs.data_blocks,
            "f_bavail": fs.data_blocks,
            "f_files": fs.inode_count, "f_ffree": fs.inode_count,
            "f_namemax": catfs.NAME_MAX,
        }


def open_fs(image: Path, disk: bool, partition: int) -> catfs.CatFS:
    f = open(image, "r+b")
    base = 0
    if disk:
        f.seek(0)
        hdr = f.read(BLOCK_SIZE)
        off = PT_ENTRIES_OFF + partition * PT_ENTRY_SIZE
        _typ, start, _blocks = struct.unpack_from("<III", hdr, off)
        base = start
    return catfs.CatFS(catfs.BlockDev(f, base_block=base))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Mount a CatFS image via FUSE.")
    ap.add_argument("image", type=Path)
    ap.add_argument("mountpoint", type=Path)
    ap.add_argument("--disk", action="store_true")
    ap.add_argument("--partition", type=int, default=0)
    ap.add_argument("--foreground", "-f", action="store_true")
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args(argv)

    fs = open_fs(args.image, args.disk, args.partition)
    FUSE(CatFSFuse(fs), str(args.mountpoint),
         foreground=args.foreground or args.debug,
         nothreads=True, debug=args.debug)
    return 0


if __name__ == "__main__":
    sys.exit(main())
