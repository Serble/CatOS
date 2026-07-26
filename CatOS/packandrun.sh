#!/bin/bash
# Build a CatOS boot disk and run it - the one-command entry point.
#
# This is a thin wrapper around the two focused scripts:
#   scripts/build-disk.sh   builds build/disk.img (kernel + userland + CatFS)
#   scripts/run-vm.sh       boots the firmware against that disk image
#
# Usage:
#   ./packandrun.sh          rebuild everything (kernel + a fresh CatFS drive
#                            with a freshly populated /bin) and boot
#   ./packandrun.sh --keep   reuse the existing drive - only rebuild the kernel
#                            and splice it into partition 0, then boot. Use this
#                            to keep the filesystem across reboots.
set -e
cd "$(dirname "$0")"

./scripts/build-disk.sh "$@"
./scripts/run-vm.sh
