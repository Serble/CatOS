#!/bin/bash
# Run the CatOS VM: boot the firmware against a disk image, with the Timer and
# RaylibPPU (graphics + keyboard) devices attached.
#
# Usage:
#   scripts/run-vm.sh [disk-image] [--ops N] [--fast]
#
# With no argument it boots build/disk.img. The firmware image must already have
# been produced by scripts/build-disk.sh (it lives in build/firm.bin).
#
# --ops sets the simulated CPU rate in instructions per second; --fast removes
# the rate limiter and lets the machine run as hard as the host will go, which
# makes --ops irrelevant.
#
# That choice is not only about speed, and the difference is worth knowing before
# you time anything. Without --fast the machine's clock counts *executed
# instructions* rather than wall time: the `uptms` instruction, and so every
# program that paces itself by it, reads a virtual clock that only advances while
# the guest is retiring instructions. Real seconds spent blocked inside a device
# are therefore invisible - and `gpresent` blocks until the display has taken the
# frame, so a compositor with nothing to redraw can spend most of a second frozen
# and never see it. The guest clock then runs at a fraction of real time, and
# anything animated crawls. --fast makes `uptms` true elapsed time and the problem
# goes away, at the price of a host core spinning flat out and of the disk's
# picosPerBlock timing no longer corresponding to anything.
set -e
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/config.sh"
cd "$ROOT"

DISK=""
OPS=10000000
EXTRA=()

while [ $# -gt 0 ]; do
    case "$1" in
        --fast)
            EXTRA+=(--fast)
            ;;
        --ops)
            if [ $# -lt 2 ]; then
                echo "error: --ops needs a value (instructions per second)" >&2
                exit 2
            fi
            OPS="$2"
            shift
            ;;
        --ops=*)
            OPS="${1#*=}"
            ;;
        -*)
            echo "usage: scripts/run-vm.sh [disk-image] [--ops N] [--fast]" >&2
            exit 2
            ;;
        *)
            DISK="$1"
            ;;
    esac
    shift
done

DISK="${DISK:-$DISK_IMG}"

if [ -z "$OPS" ]; then
    echo "error: --ops needs a value (instructions per second)" >&2
    exit 2
fi
if [ ! -f "$FIRMWARE_BIN" ]; then
    echo "error: $FIRMWARE_BIN not found - run scripts/build-disk.sh first" >&2
    exit 1
fi
if [ ! -f "$DISK" ]; then
    echo "error: disk image $DISK not found - run scripts/build-disk.sh first" >&2
    exit 1
fi

if [ ${#EXTRA[@]} -gt 0 ]; then
    echo "Running VM against $DISK (unthrottled, uptms is wall-clock time)..."
else
    echo "Running VM against $DISK (--ops $OPS)..."
fi

catlaunch run --rom "$FIRMWARE_BIN" \
    -d Disk "file:$DISK,picosPerBlock:10" \
    -d Timer \
    -d RaylibPpu \
    --test-ints -m 33554432 --ops "$OPS" "${EXTRA[@]}"
