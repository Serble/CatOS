#!/bin/bash
# Shared configuration for the CatOS build/run scripts. Sourced by the other
# scripts; not meant to be run directly. Defines where things live and the list
# of user-mode programs that get installed into /bin.
#
# ROOT is the project root (the directory that contains src/, tools/ and
# build/). Every path below is relative to ROOT, and the scripts cd into ROOT
# before doing anything, so external references (../CatFirmware, ...) resolve
# the same way regardless of where the script was invoked from.

# Resolve ROOT as the parent of this scripts/ directory.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# --- source locations -------------------------------------------------------
KERNEL_SRC="src/kernel/boot.cat"     # top-level kernel unit (pulls in the rest)
USER_SRC_DIR="src/user"              # one .cat per user-mode program

# The user-mode programs installed into /bin, each assembled as its own unit so
# its addresses are 0-based (correct once loaded at user vaddr 0).
APPS="idle init sh echo ls cat touch ef condemo hello counter spin filetest argtest sigdemo time sleep"

# --- build outputs ----------------------------------------------------------
BUILD_DIR="build"
DISK_IMG="$BUILD_DIR/disk.img"
KERNEL_BIN="$BUILD_DIR/os.bin"
FONT_BIN="$BUILD_DIR/font.bin"
CATFS_PART="$BUILD_DIR/catfs.part"

# --- external dependencies (relative to ROOT) -------------------------------
FIRMWARE_SRC="../CatFirmware/build/firmware.out"
FIRMWARE_BIN="$BUILD_DIR/firm.bin"
MAKE_DISK="../CatFirmware/make_disk.py"
FONT_SRC="../../../ASM/testos/data/font.asm"   # x86 testos 8x16 console font

# CatFS lives on partition 1; the kernel raw image on partition 0.
CATFS_PARTITION=1
