#!/bin/bash
# Mount a CatOS disk image on the host via FUSE so you can browse/edit its files
# from your normal file manager or shell.
#
# By default it mounts the CatFS root partition of build/disk.img at build/mnt.
# The mount runs in the foreground - press Ctrl-C (or run `fusermount -u <dir>`
# from another terminal) to unmount.
#
# Usage:
#   scripts/mount-disk.sh                       mount partition 1 of build/disk.img at build/mnt
#   scripts/mount-disk.sh <mountpoint>          mount at a custom directory
#   scripts/mount-disk.sh <mountpoint> <part>   mount a specific partition (0 = kernel raw, 1 = CatFS)
#   scripts/mount-disk.sh <mountpoint> <part> <image>   mount a specific disk image
set -e
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/config.sh"
cd "$ROOT"

MOUNTPOINT="${1:-$BUILD_DIR/mnt}"
PARTITION="${2:-$CATFS_PARTITION}"
IMAGE="${3:-$DISK_IMG}"

# Prefer the tools venv (it has fusepy); fall back to the system interpreter.
PY="tools/.venv/bin/python"
[ -x "$PY" ] || PY="python3"

if [ ! -f "$IMAGE" ]; then
    echo "error: disk image $IMAGE not found - run scripts/build-disk.sh first" >&2
    exit 1
fi

mkdir -p "$MOUNTPOINT"

echo "Mounting partition $PARTITION of $IMAGE at $MOUNTPOINT"
echo "Press Ctrl-C to unmount (or: fusermount -u $MOUNTPOINT)"
exec "$PY" tools/catfs_fuse.py "$IMAGE" "$MOUNTPOINT" \
    --disk --partition "$PARTITION" --foreground
