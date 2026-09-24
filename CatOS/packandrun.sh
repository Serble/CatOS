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
#
# --ops N and --fast are not ours; they are sorted out of the arguments and handed
# to scripts/run-vm.sh, which documents what they do to the machine's sense of
# time. Everything else goes to the build, so --keep still means what it did.
set -e
cd "$(dirname "$0")"

BUILD_ARGS=()
RUN_ARGS=()

while [ $# -gt 0 ]; do
    case "$1" in
        --fast|--ops=*)
            RUN_ARGS+=("$1")
            ;;
        --ops)
            if [ $# -lt 2 ]; then
                echo "error: --ops needs a value (instructions per second)" >&2
                exit 2
            fi
            RUN_ARGS+=("$1" "$2")
            shift
            ;;
        *)
            BUILD_ARGS+=("$1")
            ;;
    esac
    shift
done

./scripts/build-disk.sh "${BUILD_ARGS[@]}"
./scripts/run-vm.sh "${RUN_ARGS[@]}"
