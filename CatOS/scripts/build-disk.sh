#!/bin/bash
# Build the CatOS boot disk image.
#
# Produces build/disk.img: a two-partition drive where partition 0 is the raw
# kernel image (mounted at /sys) and partition 1 is a CatFS filesystem mounted
# as the root "/", with every user-mode program installed under /bin.
#
# Usage:
#   scripts/build-disk.sh          full rebuild: kernel + user apps + a fresh
#                                   CatFS drive with a freshly populated /bin
#   scripts/build-disk.sh --keep   reuse the existing drive - do NOT reformat or
#                                   repopulate it, only rebuild the kernel and
#                                   splice it into partition 0 (keeps the
#                                   filesystem and anything written to it)
set -e
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/config.sh"
cd "$ROOT"

KEEP=0
[ "$1" = "--keep" ] && KEEP=1

# Assemble the console font the kernel embeds (dfile in raylibppu.cat).
build_font() {
    python3 tools/mkfont.py "$FONT_SRC" "$FONT_BIN"
}

# Assemble the kernel. catasm cd's into the input file's directory, so the -o
# path must be absolute.
build_kernel() {
    catasm "$KERNEL_SRC" -o "$ROOT/$KERNEL_BIN"
}

# Assemble every user-mode program into build/<name>.bin.
build_user() {
    for u in $APPS; do
        catasm "$USER_SRC_DIR/$u.cat" -o "$ROOT/$BUILD_DIR/$u.bin"
    done
}

# The --put SRC:DST list that lays every app into /bin.
bin_puts() {
    for u in $APPS; do
        printf ' --put %s/%s.bin:/bin/%s' "$BUILD_DIR" "$u" "$u"
    done
}

if [ "$KEEP" = 1 ] && [ -f "$DISK_IMG" ]; then
    # Persistent path: leave the CatFS drive (and its /bin) untouched; only
    # rebuild the kernel and splice it back into partition 0.
    build_font
    build_kernel
    python3 tools/replace_kernel.py "$DISK_IMG" "$KERNEL_BIN" 0
else
    rm -rf "$BUILD_DIR"
    mkdir -p "$BUILD_DIR"
    cp "$FIRMWARE_SRC" "$FIRMWARE_BIN"
    build_font
    build_kernel
    build_user
    # a blank payload that becomes the CatFS root partition
    python3 -c "open('$CATFS_PART','wb').truncate(4*1024*1024)"
    python3 "$MAKE_DISK" -o "$DISK_IMG" "$KERNEL_BIN" "$CATFS_PART"
    # format partition 1 as CatFS, then create the standard directories and
    # install the userland into /bin
    python3 tools/mkfs_catfs.py --disk "$DISK_IMG" --partition "$CATFS_PARTITION"
    python3 tools/catfs_import.py --disk "$DISK_IMG" --partition "$CATFS_PARTITION" \
        --mkdir /bin --mkdir /dev --mkdir /sys $(bin_puts)
fi

echo "Built $DISK_IMG"
