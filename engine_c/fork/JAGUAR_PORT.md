# The transplant: the LBA1 game engine on the Jaguar

This directory is the community fork's game engine (see `PROVENANCE.md`),
imported whole from the PSX port. It brings everything the Jaguar build did
not have: life and track scripts, zones, extras, dialogues, inventory,
behaviours, holomap, saves. The Jaguar keeps what it already had and does
better than a software renderer: the brick background, the GPU body pipeline,
the actor as an OP sprite, the front-end.

Every change made to the files in here is listed in the table at the bottom,
one line each, with the reason. `git log -- engine_c/fork` shows the diffs.

## Where the two meet

Measured on 2026-09-23 by compiling the whole tree with the ROM's gcc and
diffing symbol tables against the S50 blob: 32 symbols defined on both sides,
99 platform symbols defined on neither. The seam has five parts.

### 1. The platform floor (the 99)

Video (`Log`, `Phys`, `Screen_X/Y`, `TabOffLine`, `Clip*`, `SetClip`,
`Flip`, `CopyBlockPhys`, `Palette`, `PalOne`, `PalMulti`, `Vsync`), input
(`Key`, `Joy`, `Fire`, `GetAscii`, the mouse), time (`TimerSystem`,
`CmptFrame`), and the drivers to stub (Wave, Midi, Mixer, CDR, DOS file and
path calls). The same set DS-LBA solves in `platform/sdl`; the PSX shim is the
closest model because it also has 2 MB.

- **`Log` is the background framebuffer.** It is already 8 bpp with LBA1's
  palette and already on screen, so dialogues, menus and the inventory draw
  straight into it. `Flip` and `CopyBlockPhys` have nothing to do: the OP
  shows `Log` directly.
- **`TimerRef` advances by itself, in `ViHandler`.** The fork busy-waits on it
  (the frame regulator, dialogue typing, fades), so pulling it once per frame
  from C, as S50 did, would hang the first dialogue. The linker places it at a
  fixed HAL address; `ViHandler` adds the 50 Hz step with the same
  accumulator `hal_time.c` uses. `RestoreTimer` writes it backwards: fine,
  the interrupt only adds.
- **The C owns the loop.** `engine_frame` does not return once the game
  starts: the fork's `MainLoop` and every modal inside it (dialogue, menus,
  inventory) are blocking loops that call `Flip`/`Vsync` themselves, and
  cutting them into per-frame steps is not an option. `ViHandler` keeps the
  OP list armed without help, which is what made S48's front-end work.

### 2. Memory and HQR (the fork's API over our cartridge)

The fork wants files, LRU pools (`HQR_Init_Ressource`) and a block heap
(HQM). The cartridge has every archive indexed, and `HQR_Get` already returns
a pointer in place (or an entry expanded once into the per-room arena). So
the fork's HQR functions are implemented on top of the HAL (`engine_c/port/jag_hqr.c`),
by archive name: no filesystem, no pools, and the whole eviction/compaction
class of bugs does not exist here.

**Name clash, and a trap:** our `Malloc(ULONG)`, `LoadMalloc(ULONG)`, `Free`,
`HQR_Get(id,int)` and `Size_HQM_Memory()` came from the 32X contract and carry
the fork's names with different meanings (the fork's `LoadMalloc` loads a
file; its `Size_HQM_Memory` is a variable). Ours are renamed with macros in
`hal.h`, which no fork file includes; the fork gets its own implementations.

`malloc/free` (the fork frees menu strings for real) come from newlib, which
the toolchain has built for `m68000`, on a bounded `_sbrk` region.

### 3. Endianness and alignment

The data stays little-endian in ROM, exactly as LBA1 shipped it, and every
read is explicit. This is the policy the Jaguar code already follows
(`le.h`), and it must not be mixed with pre-swapping.

- DS-LBA already routed the byte-packed streams through `LE_R16/LE_R32/LE_W*`
  (`DEFINES.H:74`: life, track, fiche, scene, bricks, anims) for ARM's
  unaligned loads. Those assemble bytes, so they are right on a 68000 as-is.
- What is left is every NATIVE read of LE data: structs laid over a data
  stream (`T_ZONE`/`T_TRACK` in `DISKFUNC.C`), `T_HEADER`, text offsets.
  Found with `-Wcast-align=strict` plus review, fixed at load time in DRAM.
- m68k-elf-gcc aligns `LONG` to 2, like Watcom `-zp2`: natural layout is the
  disk layout. No `#pragma pack(1)` (it is actively wrong for the FLA structs).

### 4. Animation (the fork's calls, our state)

The fork keeps animation state inside the body buffer and its functions take
the body, not the actor. The Jaguar renderer keeps it in the actor
(`AnimSrcFrame`, `AnimMemoTicks`, `PtrPoseSrc`, appended to `T_OBJET`), and the
GPU body pipeline is built on that. So the handful of calls in `OBJECT.C`
(`SetInterAnimObjet2`, `SetInterDepObjet`, `StockInterAnim`, `CopyInterAnim`,
`SetAnimObjet`) pass the actor to our functions. `DoAnim` itself is the
fork's: object collisions, carriers, falls and hits come with it.

### 5. The renderer (the fork's `AffScene`, our primitives)

`AffScene` (OBJECT.C:2552) builds the draw list (3D actors, sprites and doors,
shadows, extras), sorts it (with the Twinsen/door rule), draws each item
followed by `DrawOverBrick`, then runs the HUD incrusts and the extras'
timers. We keep all of that and swap the primitives, which is what the PSX
port did: bodies go to `draw_actor`, sprites to `Sprite_DrawOne`, the bricks
in front to our column-indexed M3d, the restore to `Bg_ClsPending`.
`GRILLE.C`/`GRILLE_A.C` are not compiled: `scene_bg.c` already has the cube,
the collision (`Grid_ColBrick` is `WorldColBrick` transcribed) and the
background, and serves their API.

A side gain: `Body_DrawAll` draws actors in `ListObjet` order, with no depth
sort between actors. `AffScene` has one.

## Bring-up order

Each step ends with a build that runs; the visible ones are marked.

1. The fork compiles and links into a test ELF next to the real blob; the
   linker's lists drive the seam. Size checked against `ROM_CBLOB`.
2. Platform floor, HQR/memory, `TimerRef` in `ViHandler`.
3. `LoadScene`/`ChangeCube` from the fork replace `scene_load.c` and
   `scene_walk.c`; `object_move.c` goes. **Visible: the same room, loaded by
   the fork.**
4. `MainLoop` runs, with our `AffScene`. **Visible: the actors follow their
   TRACK scripts** (the M5 criterion).
5. Life scripts, zones and walking through doors, dialogues. **Visible: the
   escape from the Citadel cell, played with the pad.**

## Changes to the fork

| # | File | What | Why |
|---|------|------|-----|
| 1 | `LIB_MENU/MENUFUNC.C` | `PORT_JAG` added to the `Message()` duplicate guard | PERSO.C defines its own, as on SDL/NDS/PSX |
| 2 | `game/PERSO.C` | `TheEnd` under `#ifndef PORT_JAG` | ours (`hal_panic.c`) draws the code on screen and stops; this one printf()s and exits to DOS |
| 3 | `game/DEFINES.H` | `T_OBJET` gets `AnimSrcFrame`, `AnimMemoTicks`, `PtrPoseSrc` at the end, under `PORT_JAG` | the animation state lives in the actor on the Jaguar; `port/jag_layout.h` proves both definitions equal (size, key offsets, `MAX_*`), checked on both sides with `_Static_assert` and seen to fail on a broken offset |
| 4 | `game/OBJECT.C` | anim calls to `port/jag_anim.c` (`SetInterDepObjet`, `SetAnimObjet`, `StockInterAnim`; `CopyInterAnim` dropped); `InitBody` ZV at JBD `+28`; `InitObject` clears our three fields; `AffScene` forwards to `Jag_AffScene` (the fork's body kept as `AffScene_Fork`) | section 4 and 5 |
| 5 | `game/FICHE.C` | `SearchBody` loads JBD through `Jag_LoadBody`, no `PatchObjet` | the cartridge's bodies are JBD |
| 6 | `game/PERSO.C` | `Screen` and `BufSpeak` at 64 KB (as PSX); `lba_main` ends in `Jag_M5Game` | 2 MB; the front-end already showed logo and menu |
| 7 | `game/DISKFUNC.C` | `LoadScene` byte-swaps `ListZone` and `ListBrickTrack` in place | read as structs of native WORDs from LE data; found by the hero's life script (prisoner clothes only inside zone 3) |
| 8 | `game/MESSAGE.C` | `InitDial` byte-swaps `BufOrder` and the offset table of `BufText` (MaxText+1 words) | LE WORDs read natively by `FindText`/`GetText`; the TODO of S51 named it |
| 9 | `game/PERSO.C` | `Introduction` loads each slide straight into `Log` (`IntroSlide`); a slide the cartridge lacks leaves black with the game palette | `Screen` is 64 KB and a slide 300 KB; the three intro slides (373 KB compressed) are not in the cartridge yet, and without a palette the intro texts ran invisible, eating the first key presses |
| 10 | `LIB_3D/P_ANIM.C`, `translate/p_ob_iso.c` | `SetAnimObjet`, `SetInterAnimObjet(2)` and `AffObjetIso` return at once on a JBD body (`JAG_IS_JBD` in `port/jag_compat.h`, magic `JD`) | the behaviour menu fed them the hero's JBD: the game hung on the first of the four poses, and P_ANIM would have written its state into the body. The four poses (and any body outside a scene) still have to be drawn by our pipeline |
| 11 | `LIB_SVGA/S_SCREEN.C`, `translate/s_block.c` | `CopyScreen`/`CopyBlock` skip any copy to or from `Screen`, counted in `Jag_ScreenSkips` | `Screen` is 64 KB (as on PSX) but the fork uses it as a 300 KB snapshot around menus, inventory and dialogue windows: `MenuComportement`'s `CopyScreen(Log, Screen)` trampled the heap (`NbFramePerSecond` read 0x0F0F0F0F). STOPGAP: the restores it feeds are missing until `Screen` has a home |
| 12 | `LIB_SVGA/FONT_A.C`, `translate/graph_a.c`, `graphmsk.c`, `mask_a.c` | the offset table at the head of a graphic bank (font, sprites extra, masks) read little-endian (`JAG_BANK_OFF` in `port/jag_compat.h`) | read as native LONGs: on the 68000 every word came out wider than the dialogue line, so `GetNextLine` divided by `NbSpace` = 0 (68000 exception 5, PC in `__divsi3` from `GetNextLine`, found with the frame `ExcCommon` now saves at `SPARE_LO`); `AffGraph` and `AffMask` drew from wild pointers |
| 13 | `game/GERETRAK.C` | `TM_GOTO_POINT_3D`: a sprite also arrives when this frame's step would reach or pass the point | a sprite moves SRot units a second, so below ~4.5 fps a step of 900 jumps over the 100-unit window: on the real Jaguar the hover platform swung round point 14 for ever, label 105 never came, nor the dialogue. Reproduced on BigPEmu with `Jag_FrameDelay` (port/jag_render.c) |
| 14 | `game/PERSO.C` | `PtrZvExtra` (8 WORDs a sprite: screen offset, collision box) byte-swapped after loading (`Jag_SwapResWords`, port/jag_hqr.c) | read natively by `InitSprite`, EXTRA.C and AffScene: the sprite carriers' boxes were byte-reversed, so the nurse was never carried by the hover platform and stayed at its departure point |

## Found while building the seam (2026-09-23), not yet acted on

Each of these would have compiled, linked and misbehaved in silence.

- **`NbObjets`/`NbBodys` are `LONG` in GLOBAL.C, were `WORD` in our hal.h.**
  A big-endian read of the wrong width returns the HIGH half: zero actors.
  Fixed on our side. Same risk for every shared global and struct: a layout
  header with `sizeof`/`offsetof` checked by `_Static_assert` on BOTH sides
  (T_OBJET first - ours appends three fields the fork's must also get).
- **The cartridge's bodies are JBD (S43), not LBA1's format.** Three fork
  sites read a body: `SearchBody` (FICHE.C:636) calls `PatchObjet` on it,
  `InitBody` (OBJECT.C:1064) reads the ZV at `+2` (JBD has it at `+28`,
  big-endian), and the P_ANIM calls. All three go through the seam, step 3.
- **`WorldCodeBrick` read `TabBlock` offsets as a native `ULONG`**: LE data,
  wrong on a 68000. Moot: it is rewritten over `Grid_BlockByte`.
- **The fork busy-waits on `Key`/`Fire` as well as `TimerRef`**: input has to
  come from the video interrupt too (`port/jag_sys.c:Jag_Vbl`, not wired yet).
- **`HQR_Flag` precedes a write into the entry** (`PatchObjet` after a fresh
  load): a pointer into ROM would drop it. Every archive but BRICK is LZSS in
  the cartridge, so `HQR_Get` always hands out a DRAM copy; keep it that way
  for anything the fork patches.
