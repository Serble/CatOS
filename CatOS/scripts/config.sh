#!/bin/bash
# Shared configuration for the CatOS build/run scripts. Sourced by the other
# scripts; not meant to be run directly. Defines where things live and how the
# user-mode programs installed into /bin are found.
#
# ROOT is the project root (the directory that contains src/, tools/ and
# build/). Every path below is relative to ROOT, and the scripts cd into ROOT
# before doing anything, so external references (../CatFirmware, ...) resolve
# the same way regardless of where the script was invoked from.

# Resolve ROOT as the parent of this scripts/ directory.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# --- source locations -------------------------------------------------------
KERNEL_SRC="src/kernel/boot.cat"     # top-level kernel unit (pulls in the rest)
USER_SRC_DIR="src/user"              # the user-mode programs, found rather than
                                     # listed (see below)
CONF_SRC_DIR="src/conf"              # files installed into the root fs /conf. Every
                                     # file here is installed under its own name, so
                                     # a program's settings file is added by adding
                                     # the file - see src/conf/editor.conf.
DRIVERS_SRC_DIR="src/drivers"        # loadable drivers installed into /drivers,
                                     # found like the programs (below, .cat only)
APPS_SRC_DIR="src/apps"              # application entries installed into /apps.
                                     # Every file here is installed under its own
                                     # name, so adding an application to the window
                                     # managers' launcher is adding a file - see
                                     # src/apps/term.app for the format.

# The user-mode programs installed into /bin are whatever USER_SRC_DIR holds,
# each built as its own unit so its addresses are 0-based (correct once loaded at
# user vaddr 0):
#   <name>.cat / <name>.nip        a single-file program, installed as /bin/<name>
#   <name>/main.cat / main.nip     a multi-file program, installed as /bin/<name>;
#                                  the directory's other files are its includes
# .cat is assembled with catasm, .nip compiled with nipcompile. A directory with
# no main is not a program, which is what keeps lib/ - the shared includes - out
# of /bin. Two sources for the same name stop the build.

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
