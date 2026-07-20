#!/bin/sh

# Constants
mkdir build

echo "Assembling..."
catasm firm.cat -o build/firmware.out
status=$?
if [ $status -ne 0 ]; then
  echo "Assemble failed: exit $status"
  exit $status
fi

# echo "Running..."

# Requires raylib rendering
# dotnet run -c Release --project ../../CatVM/CatVM.csproj a.out --test-ints --disk "disk.img" 2 0 --dump-errors $*

# status=$?
# echo "Application exited with status code $status"

