#!/bin/sh
# S24: compila il blob C m68k per il 68000 Jaguar dentro Docker marsdev-32x.
# Da Windows:
#   docker run --rm -v "D:\Homebrew5\JagLBA:/work" -w /work/engine_c marsdev-32x:latest sh build_c.sh
# Output: poc_blob.bin (incbin lato RMAC) + poc_blob.sym/.lst per debug.
set -e

BIN=/marsdev/mars/m68k-elf/bin
LIBGCC=$("$BIN/m68k-elf-gcc" -m68000 -print-libgcc-file-name)
CFLAGS="-m68000 -O2 -fomit-frame-pointer -ffreestanding -fno-builtin -Wall -Wextra"
# NOTA: niente -mshort. int resta a 32 bit come sul porting SH-2 (engine_src
# e' stato debuggato con int=32; -mshort cambierebbe la semantica testata).

"$BIN/m68k-elf-gcc" $CFLAGS -c poc_main.c -o poc_main.o
"$BIN/m68k-elf-as" -m68000 crt_entry.s -o crt_entry.o
"$BIN/m68k-elf-ld" -T jag_c.ld crt_entry.o poc_main.o "$LIBGCC" -o poc_blob.elf
"$BIN/m68k-elf-objcopy" -O binary poc_blob.elf poc_blob.bin
"$BIN/m68k-elf-nm" -n poc_blob.elf > poc_blob.sym
"$BIN/m68k-elf-objdump" -d poc_blob.elf > poc_blob.lst
ls -l poc_blob.bin
