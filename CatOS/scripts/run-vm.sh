#!/bin/bash
# Run the CatOS VM: boot the firmware against a disk image, with the Timer and
# RaylibPPU (graphics + keyboard) devices attached.
#
# Usage:
#   scripts/run-vm.sh [disk-image]
#
# With no argument it boots build/disk.img. The firmware image must already have
# been produced by scripts/build-disk.sh (it lives in build/firm.bin).
set -e
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/config.sh"
cd "$ROOT"

DISK="${1:-$DISK_IMG}"

if [ ! -f "$FIRMWARE_BIN" ]; then
    echo "error: $FIRMWARE_BIN not found - run scripts/build-disk.sh first" >&2
    exit 1
fi
if [ ! -f "$DISK" ]; then
    echo "error: disk image $DISK not found - run scripts/build-disk.sh first" >&2
    exit 1
fi

echo "Running VM against $DISK..."
catlaunch run --rom "$FIRMWARE_BIN" \
    -d Disk "file:$DISK,picosPerBlock:10" \
    -d Timer \
    -d RaylibPpu \
    --test-ints -m 1048576 --fast
