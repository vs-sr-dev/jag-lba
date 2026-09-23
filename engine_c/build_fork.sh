#!/bin/sh
# JagLBA - the transplanted LBA1 engine (engine_c/fork), compiled for the
# 68000 and linked into a TEST ELF next to the real blob.
#
# Step 1 of the bring-up (engine_c/fork/JAGUAR_PORT.md): the real blob is
# untouched and the build stays green. This script only answers two questions
# with the linker's own words - which symbols the seam still misses, and how
# big the result is against ROM_CBLOB.
#
# Runs inside the marsdev-32x image, like build_engine.sh:
#   docker run --rm -v "D:\Homebrew5\JagLBA:/work" -w /work/engine_c \
#          marsdev-32x:latest "sh build_fork.sh"
set -e

# MODE: "test" (default) links obj_fork/fork_test.elf with the fork's entry
# points as roots and reports; "blob" builds THE engine blob (engine_blob.bin)
# with the fork inside and JAG_FORK=1 on our side, and fails on any missing
# symbol. build_v6.bat calls "blob" when JAG_FORK=1.
MODE=${1:-test}

BIN=/marsdev/mars/m68k-elf/bin
LIBGCC=$("$BIN/m68k-elf-gcc" -m68000 -print-libgcc-file-name)
LIBC=$("$BIN/m68k-elf-gcc" -m68000 -print-file-name=libc.a)
F=fork
O=obj_fork
mkdir -p $O

# The 1994 code wants the permissive dialect it was written in; gcc 15 turns
# these into errors by default. -fcommon: K&R duplicate tentative definitions.
FORK_CFLAGS="-m68000 -O2 -fomit-frame-pointer -ffunction-sections -fdata-sections \
    -fsigned-char -fcommon -std=gnu89 -include port/jag_compat.h -w \
    -I$F -I$F/game -I$F/LIB_SYS -I$F/LIB_3D -I$F/LIB_SVGA -I$F/LIB_MENU \
    -I$F/LIB_CD -I$F/LIB_MIX -I$F/LIB_SAMP -I$F/LIB_MIDI -I$F/translate \
    -Iport/compat/inc"

# What goes in, and why the rest does not. Everything left out is served by
# the Jaguar side or stubbed in engine_c/port (JAGUAR_PORT.md, the five seams).
#   game/GRILLE, GRILLE_A, CPYMASK   the cube, collision, bricks in front:
#                                    scene_bg.c serves their API
#   game/FIRE                        the menu fire: front_menu.c
#   game/MCGA, ADFLI_A, PLAYFLA      FLA movies and the 320x200 mode: later
#   LIB_SYS files, pools, DLLs,      the cartridge replaces them: port/jag_hqr.c,
#   Adeline setup, HQ_MEM, DEF_FILE  jag_config.c, jag_stubs.c
#   LIB_SVGA GIF, PCX, LBM, SAVEPCX  image files
#   LIB_CD, LIB_MIX, LIB_MIDI        drivers: stubbed
GAME="AMBIANCE BALANCE BUBSORT DISKFUNC EXTRA FICHE FLIPBOX FUNC GAMEMENU \
      GERELIFE GERETRAK GLOBAL HOLOMAP INCRUST MESSAGE MSG_CUST OBJECT PERSO \
      VERSION"
LIB3D="P_ANIM P_FUNC P_SINTAB"
LIBSYS="BUFFER_A DIVERS EXPAND"
LIBSVGA="BLOC FONT8X8 FONT_A MASKGPH RECT S_SCREEN S_TEXT"
LIBMENU="MENU MENUFUNC"
TRANSLATE="graph_a graphmsk mask_a p_ob_iso p_trigo s_block s_block2 s_block3 \
      s_box s_fillv s_line s_plot s_poly s_string texture zoom"

# Cold files - menus, holomap, dialogue, inventory: they run while the game
# waits for a key, not once per frame - are compiled for size. The frame's
# hot path (OBJECT, GERELIFE, GERETRAK, EXTRA, P_ANIM...) stays at -O2. On
# 2026-09-23 the whole link was 1.9 KB under ROM_CBLOB before this.
COLD=" GAMEMENU HOLOMAP MESSAGE MSG_CUST FLIPBOX AMBIANCE MENU MENUFUNC \
       S_TEXT texture p_ob_iso s_poly s_fillv zoom "

OBJS=""
cc() {   # $1 source, $2 object
    base=$(basename "$1"); base=${base%.*}
    case "$COLD" in *" $base "*) opt=-Os ;; *) opt= ;; esac
    "$BIN/m68k-elf-gcc" $FORK_CFLAGS $opt -x c -c "$1" -o "$2" 2> "$2.err" \
        || { echo "FAIL $1"; head -5 "$2.err"; exit 1; }
    OBJS="$OBJS $2"
}
for n in $GAME;      do cc $F/game/$n.C     $O/game_$n.o; done
for n in $LIB3D;     do cc $F/LIB_3D/$n.C   $O/l3d_$n.o;  done
for n in $LIBSYS;    do cc $F/LIB_SYS/$n.C  $O/sys_$n.o;  done
for n in $LIBSVGA;   do cc $F/LIB_SVGA/$n.C $O/svga_$n.o; done
for n in $LIBMENU;   do cc $F/LIB_MENU/$n.C $O/menu_$n.o; done
for n in $TRANSLATE; do cc $F/translate/$n.c $O/tr_$n.o;  done
# The fork's half of the layout invariant (port/jag_layout.h).
cc port/jag_layout_fork.c $O/port_jag_layout_fork.o

# The Jaguar side, minus the three files the fork replaces (JAGUAR_PORT.md,
# bring-up step 3): scene_load.c, scene_walk.c, object_move.c. Compiled HERE
# with build_engine.sh's flags, into obj_fork/: reusing that script's objects
# would link whatever it built last, i.e. a Jaguar side that may predate the
# renames in hal.h.
JAG_CFLAGS="-m68000 -O2 -fomit-frame-pointer -ffreestanding -fno-builtin \
    -fno-tree-loop-distribute-patterns -ffunction-sections -fdata-sections \
    -Wall -Wextra -I."
[ "$MODE" = blob ] && JAG_CFLAGS="$JAG_CFLAGS -DJAG_FORK=1"
JAG="hal_debug hal_mem hal_time hal_input hal_hqr hal_panic scene_bg \
     body_draw sprite_draw front_menu engine_main"
PORT="jag_video jag_sys jag_hqr jag_stubs jag_config jag_libc jag_grille jag_layout_ours \
      jag_anim jag_scene jag_render jag_game"
JOBJS=""
for n in $JAG; do
    "$BIN/m68k-elf-gcc" $JAG_CFLAGS -c $n.c -o $O/jag_$n.o 2> $O/jag_$n.err \
        || { echo "FAIL $n.c"; head -5 $O/jag_$n.err; exit 1; }
    JOBJS="$JOBJS $O/jag_$n.o"
done
for n in $PORT; do
    "$BIN/m68k-elf-gcc" $JAG_CFLAGS -Iport -c port/$n.c -o $O/port_$n.o \
        2> $O/port_$n.err || { echo "FAIL port/$n.c"; head -8 $O/port_$n.err; exit 1; }
    JOBJS="$JOBJS $O/port_$n.o"
done
# Our side's warnings are worth reading: print them, do not fail on them.
cat $O/jag_*.err $O/port_*.err 2>/dev/null | grep -A2 "warning" | head -40 || true
"$BIN/m68k-elf-as" -m68000 crt0.s -o $O/crt0.o

"$BIN/m68k-elf-size" -t $OBJS | tail -1

if [ "$MODE" = blob ]; then
    # THE blob. The roots are the entry table's (engine_init, engine_frame,
    # engine_vbl); --gc-sections drops what nothing reaches, including the
    # S50 game path that JAG_FORK leaves uncalled.
    "$BIN/m68k-elf-ld" -T jag_engine.ld --gc-sections         -Map=engine_blob.map $O/crt0.o $JOBJS $OBJS "$LIBC" "$LIBGCC"         -o engine_blob.elf
    "$BIN/m68k-elf-objcopy" -O binary engine_blob.elf engine_blob.bin
    "$BIN/m68k-elf-nm" -n engine_blob.elf > engine_blob.sym
    "$BIN/m68k-elf-objdump" -d engine_blob.elf > engine_blob.lst
    head -3 engine_blob.sym
    "$BIN/m68k-elf-size" engine_blob.elf
    ls -l engine_blob.bin
    exit 0
fi

# The test link. --noinhibit-exec writes the ELF even with errors, so the size
# is measurable while the seam is still open; the errors are the seam.
# -u: the fork's entry points are the roots of what gets kept. Nothing on
# the Jaguar side calls them yet, and without these --gc-sections would throw
# the whole fork away and report a size that means nothing.
"$BIN/m68k-elf-ld" -T jag_engine.ld --gc-sections --noinhibit-exec \
    -u MenuInitGame -u MainLoop -u lba_main \
    -Map=$O/fork_test.map $O/crt0.o $JOBJS $OBJS "$LIBC" "$LIBGCC" \
    -o $O/fork_test.elf 2> $O/link.txt || true

# One line per missing symbol, and one per duplicate, sorted.
grep -o "undefined reference to \`[^']*'" $O/link.txt | sed "s/.*\`//;s/'//" \
    | sort -u > $O/undefined.txt
grep -o "multiple definition of \`[^']*'" $O/link.txt | sed "s/.*\`//;s/'//" \
    | sort -u > $O/duplicate.txt
echo "undefined: $(wc -l < $O/undefined.txt)   duplicate: $(wc -l < $O/duplicate.txt)"
"$BIN/m68k-elf-size" $O/fork_test.elf
