#!/bin/bash
rm -rf build
mkdir -p ./build
cd build
cp ../../CatFirmware/a.out ./firm.bin
dotnet run --project ../../../CatAssembler ../boot.cat -o ./build/os.bin
python ../../CatFirmware/make_disk.py -o ./disk.img os.bin
echo Running VM...
dotnet run --project ../../../CatLauncher run --rom "firm.bin" -d Disk file:"disk.img",picosPerBlock:10 --test-ints -m 6969 --fast

