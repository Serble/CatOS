#!/bin/bash
# Build the CatOS boot disk image.
#
# Produces build/disk.img: a two-partition drive where partition 0 is the raw
# kernel image (mounted at /sys) and partition 1 is a CatFS filesystem mounted
# as the root "/", with every user-mode program installed under /bin and every
# loadable driver under /drivers.
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

# The user-mode programs, as parallel name / source lists, filled in by
# find_units from the layout config.sh describes.
PROG_NAMES=()
PROG_SRCS=()

add_program() {
    local i
    for i in "${!PROG_NAMES[@]}"; do
        if [ "${PROG_NAMES[$i]}" = "$1" ]; then
            echo "error: /bin/$1 has two sources: ${PROG_SRCS[$i]} and $2" >&2
            exit 1
        fi
    done
    PROG_NAMES+=("$1")
    PROG_SRCS+=("$2")
}

# The drivers installed into /drivers, found the same way (drivers are .cat only:
# the image has to open with the descriptor, which only the assembler lays out).
DRV_NAMES=()
DRV_SRCS=()

add_driver() {
    local i
    for i in "${!DRV_NAMES[@]}"; do
        if [ "${DRV_NAMES[$i]}" = "$1" ]; then
            echo "error: /drivers/$1 has two sources: ${DRV_SRCS[$i]} and $2" >&2
            exit 1
        fi
    done
    DRV_NAMES+=("$1")
    DRV_SRCS+=("$2")
}

# find_units DIR ADD EXT...: hand every unit in DIR to ADD as (name, source) -
# a <name>.<ext> file directly in DIR, or a <name>/ directory holding
# main.<ext>. A directory without a main (lib/) is not a unit.
find_units() {
    local dir="$1" add="$2" f d n e
    shift 2
    for e in "$@"; do
        for f in "$dir"/*."$e"; do
            [ -f "$f" ] || continue
            n="$(basename "$f")"
            "$add" "${n%.*}" "$f"
        done
    done
    for d in "$dir"/*/; do
        d="${d%/}"
        for e in "$@"; do
            f="$d/main.$e"
            if [ -f "$f" ]; then
                "$add" "$(basename "$d")" "$f"
            fi
        done
    done
}

# Build every user-mode program into build/<name>.bin. The assemblers' own
# errors name only the file, which for a directory program is usually main.*,
# so say which program it was.
build_user() {
    local i name src out
    for i in "${!PROG_NAMES[@]}"; do
        name="${PROG_NAMES[$i]}"
        src="${PROG_SRCS[$i]}"
        out="$ROOT/$BUILD_DIR/$name.bin"
        case "$src" in
            *.cat) catasm "$src" -o "$out" ;;
            *.nip) nipcompile "$ROOT/$src" -o "$out" ;;  # rooted, or Catnip resolves
                                                          # its includes against ROOT
        esac || {
            echo "error: failed to build /bin/$name from $src" >&2
            exit 1
        }
    done
}

# The --put SRC:DST list that lays every program into /bin.
bin_puts() {
    local u
    for u in "${PROG_NAMES[@]}"; do
        printf ' --put %s/%s.bin:/bin/%s' "$BUILD_DIR" "$u" "$u"
    done
}

# Build every driver into build/drivers/<name>.
build_drivers() {
    local i name src
    mkdir -p "$BUILD_DIR/drivers"
    for i in "${!DRV_NAMES[@]}"; do
        name="${DRV_NAMES[$i]}"
        src="${DRV_SRCS[$i]}"
        catasm "$src" -o "$ROOT/$BUILD_DIR/drivers/$name" || {
            echo "error: failed to build /drivers/$name from $src" >&2
            exit 1
        }
    done
}

# The --put list that lays every driver into /drivers.
drv_puts() {
    local d
    for d in "${DRV_NAMES[@]}"; do
        printf ' --put %s/drivers/%s:/drivers/%s' "$BUILD_DIR" "$d" "$d"
    done
}

# The --put list for /apps: every application entry, under its own file name.
app_puts() {
    for a in "$APPS_SRC_DIR"/*; do
        [ -f "$a" ] || continue
        printf ' --put %s:/apps/%s' "$a" "$(basename "$a")"
    done
}

# The --put list for /conf: every configuration file, under its own name, so
# adding one is adding a file (init.conf, editor.conf, ...).
conf_puts() {
    for c in "$CONF_SRC_DIR"/*; do
        [ -f "$c" ] || continue
        printf ' --put %s:/conf/%s' "$c" "$(basename "$c")"
    done
}

if [ "$KEEP" = 1 ] && [ -f "$DISK_IMG" ]; then
    # Persistent path: leave the CatFS drive (and its /bin) untouched; only
    # rebuild the kernel and splice it back into partition 0.
    build_font
    build_kernel
    python3 tools/replace_kernel.py "$DISK_IMG" "$KERNEL_BIN" 0
else
    # before the wipe, so a clash between two sources leaves build/ alone
    find_units "$USER_SRC_DIR" add_program cat nip
    find_units "$DRIVERS_SRC_DIR" add_driver cat
    rm -rf "$BUILD_DIR"
    mkdir -p "$BUILD_DIR"
    cp "$FIRMWARE_SRC" "$FIRMWARE_BIN"
    build_font
    build_kernel
    build_user
    build_drivers
    # a blank payload that becomes the CatFS root partition
    python3 -c "open('$CATFS_PART','wb').truncate(4*1024*1024)"
    python3 "$MAKE_DISK" -o "$DISK_IMG" "$KERNEL_BIN" "$CATFS_PART"
    # format partition 1 as CatFS, then create the standard directories and
    # install the userland into /bin and the drivers into /drivers
    python3 tools/mkfs_catfs.py --disk "$DISK_IMG" --partition "$CATFS_PARTITION"
    python3 tools/catfs_import.py --disk "$DISK_IMG" --partition "$CATFS_PARTITION" \
        --mkdir /bin --mkdir /dev --mkdir /sys --mkdir /home --mkdir /conf \
        --mkdir /apps --mkdir /drivers \
        $(conf_puts) $(app_puts) $(bin_puts) $(drv_puts)
fi

echo "Built $DISK_IMG"
