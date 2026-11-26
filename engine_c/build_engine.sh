#!/bin/sh
# JagLBA V6 - build del blob C dell'engine per il 68000 Jaguar.
# Gira dentro l'immagine Docker marsdev-32x (m68k-elf-gcc 15.2.0 gia' presente).
#
# Da Windows lo chiama build_v6.bat:
#   docker run --rm -v "D:\Homebrew5\JagLBA:/work" -w /work/engine_c \
#          marsdev-32x:latest sh build_engine.sh
#
# Output: engine_blob.bin (piazzato dal packer a ROM_CBLOB) + .sym/.lst.
set -e

BIN=/marsdev/mars/m68k-elf/bin
LIBGCC=$("$BIN/m68k-elf-gcc" -m68000 -print-libgcc-file-name)

# Niente -mshort: int resta a 32 bit, come nel porting SH-2 gia' debuggato.
# -fno-tree-loop-distribute-patterns: impedisce a GCC di riscrivere i cicli in
# chiamate a memset/memcpy che poi dovremmo comunque fornire noi.
CFLAGS="-m68000 -O2 -fomit-frame-pointer -ffreestanding -fno-builtin \
        -fno-tree-loop-distribute-patterns -Wall -Wextra -I."

# L'ordine non conta per il linker, ma conta per leggere gli errori: prima
# l'HAL, poi chi lo usa.
SRC="hal_debug.c hal_mem.c hal_time.c hal_log.c hal_input.c hal_hqr.c hal_panic.c \
     scene_walk.c scene_load.c scene_bg.c body_draw.c sprite_draw.c \
     object_move.c front_menu.c engine_main.c"
OBJS="crt0.o"

"$BIN/m68k-elf-as" -m68000 crt0.s -o crt0.o
for f in $SRC; do
    o=$(basename "$f" .c).o
    "$BIN/m68k-elf-gcc" $CFLAGS -c "$f" -o "$o"
    OBJS="$OBJS $o"
done

"$BIN/m68k-elf-ld" -T jag_engine.ld $OBJS "$LIBGCC" -o engine_blob.elf
"$BIN/m68k-elf-objcopy" -O binary engine_blob.elf engine_blob.bin
"$BIN/m68k-elf-nm" -n engine_blob.elf > engine_blob.sym
"$BIN/m68k-elf-objdump" -d engine_blob.elf > engine_blob.lst

# La tabella di entry deve stare all'inizio del blob, altrimenti gli offset
# fissi C_ENTRY_INIT/C_ENTRY_FRAME puntano da un'altra parte.
head -3 engine_blob.sym
ls -l engine_blob.bin
