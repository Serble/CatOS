# CatFS host tools

Host-side tools for the CatFS filesystem (format spec: `../../CatFS.md`).

* `catfs.py` — the format: constants, allocator, inode/dirent handling, and the
  write-ahead journal engine. Imported by the other two.
* `mkfs_catfs.py` — format a CatFS image or a partition inside a CatVM disk.
* `catfs_import.py` — populate a CatFS partition from the host: create
  directories and copy host files in. `scripts/build-disk.sh` uses it to lay the
  user-mode programs into `/bin` (and create the `/dev`, `/sys` mount points) on
  the on-disk root filesystem before the VM boots.
* `catfs_fuse.py` — mount a CatFS image on Linux via FUSE.
* `replace_kernel.py` — splice a rebuilt kernel into a disk's raw partition
  without touching the CatFS partition (used by `scripts/build-disk.sh --keep`).

## Setup

`catfs_fuse.py` needs [fusepy](https://pypi.org/project/fusepy/) and libfuse.
`mkfs_catfs.py` needs only the standard library.

```sh
python3 -m venv .venv
.venv/bin/pip install fusepy
```

## Examples

```sh
# format a standalone 4 MiB image
python3 mkfs_catfs.py --size 4M fs.img

# format partition 1 of a CatVM disk (rewrites its type to 1 = CatFS)
python3 mkfs_catfs.py --disk disk.img --partition 1

# mount it (Ctrl-C or `fusermount -u <mnt>` to unmount)
.venv/bin/python catfs_fuse.py fs.img /mnt/catfs
.venv/bin/python catfs_fuse.py --disk disk.img --partition 1 /mnt/catfs
```

The build is driven from the project root (one level up from `tools/`):

* `scripts/build-disk.sh` builds CatOS and lays it on a disk beside a freshly
  `mkfs`-formatted CatFS partition, populating `/bin` with the userland.
* `scripts/run-vm.sh` boots the firmware against that disk image.
* `packandrun.sh` is the one-command wrapper that runs both.

Pass `--keep` (to `build-disk.sh` or `packandrun.sh`) to reuse the existing disk
image (and its CatFS contents) across reboots, which is how persistence and
journal recovery are demonstrated end to end.
