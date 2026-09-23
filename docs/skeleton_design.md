# JagLBA V6 — skeleton design
*Written in session 24 (2026-07-29) and extended through S30; translated from the Italian original. Later work is recorded in `engine_c/fork/JAGUAR_PORT.md`.*

Architecture document written in S24 (2026-07-29), after the diagnosis that
closed V4/V5. It replaces the "scene viewer" framework with a skeleton
designed around the derived LBA1 engine.

---

## 1. Why redo the skeleton

Three facts established in S24, not opinions:

1. **The data/rendering engine is sound.** The boot markers certified them one
   by one: grid decoding, RLE decoding of the bricks, GPU TOM rasterizer
   (clip + scanline + gouraud + spheres), isometric projection, camera,
   blit. All of this works and must be **kept**.
2. **The plumbing layer is broken and cannot be diagnosed at a reasonable cost.**
   Address Error from a fetch at an odd address on entering `OPSetup`, with a
   healthy stack. It is exactly the part that a rebuild rewrites anyway.
3. **There was no boot checkpoint.** The project stayed broken for three
   months without anyone noticing. This is a defect of *architecture*, not of
   code, and it is the first one to fix.

To which the demonstrated constraint is added: with the current map only **8 KB
remain free** in DRAM, not enough for actors, animations and game state.

---

## 2. Hardware calibration (Jaguar vs 32X)

This needs to be brought into focus because it drives the choices, and one
point of the initial intuition must be corrected.

| | Jaguar | 32X |
|---|---|---|
| CPU hosting the logic | **68000 @ 13.3 MHz**, CISC, no cache, **last in bus priority** | **SH-2 @ 23 MHz**, RISC, 4 KB cache |
| Coprocessors | Tom (RISC GPU 26.6 MHz, 4 KB local) + Jerry (DSP 26.6 MHz, 8 KB local) + Blitter + Object Processor | second SH-2 |
| Bus to DRAM | **64 bit** | 16-bit SDRAM |
| RAM | 2 MB | 256 KB |
| Target resolution | 640x480i | 320x224 |

**Important correction:** the Jaguar's 68000 is indeed ~1.7x the Genesis one,
but on the 32X the game logic **does not run on the 68000** — it runs on the
SH-2s. As the *host of the engine* the Jaguar's 68000 is therefore
**significantly weaker** than the SH-2: RISC versus CISC, 23 versus 13.3
MHz, with cache versus without, and on top of that last in bus priority.

Where the Jaguar really wins is in the **aggregate**: 64-bit bandwidth, and above
all composition and filling in hardware (OP, Blitter, GPU), which on the 32X are
the CPU's burden. This is not a detail: it is precisely the most expensive part
of an isometric game.

**Design implication, non-negotiable:** the 68000 gets **only the logic**
(LIFE/TRACK interpreters, actors, grid collisions) — that is, exactly the
load that ran on a 386. Every per-pixel or per-vertex cycle goes to
GPU/Blitter/OP. No loops over pixels on the 68000, ever.

**On the cost of 640x480i:** it is 4x the pixels of the 32X, but **not 4x the
cost per frame**. LBA rendering is a static background (drawn once per room) +
moving actors. The steady-state cost is proportional to the **area of the
actors**, not to that of the screen. The factor of 4 weighs on scene loading and
on full-screen blits, not on the steady state. This is why the OP sprite
architecture (discovered in S23) is the right one and must be generalized.

---

## 3. Architectural decisions

### 3.1 The engine's C runs from the cart, not from DRAM

The 68000 executes from the cart natively (the current asm code sits at `$802000`).
So the C blob **is not copied to DRAM**: it is executed in place, and only
`.data` and `.bss` end up in DRAM. This removes at the root the problem of the
only 8 KB free that had blocked the S24 PoC.

Linker script with a ROM/RAM split: `.text`+`.rodata` at a fixed cart address,
`.data`/`.bss` in DRAM, and a startup that copies `.data` and zeroes `.bss`.

### 3.2 Python decides the cart layout, not RMAC

Today RMAC concatenates everything with `.incbin`, so the addresses depend on the
size of the code — and that is why the C blob could not have a fixed address.
This is inverted: **the Python packer owns the layout** and places the asm/HAL
blob, the engine's C blob and the data at fixed addresses. RMAC produces only
its own piece.

Cart **V6 with bank switching from the start**: ANIM.HQR is on its way and the
6 MB are already full.

### 3.3 Data comes from the cart, not baked into C arrays

On the 32X `engine_src` contains ~31,000 lines of compiled data (grid, anims,
body, texts). On Jaguar **they are not needed**: we already have
`cart_pack_all.py` and `HQR_Get` can return pointers into the cart directly.

The real logic code to port is about **5,000 lines**:
`object.c` (1557), `p_anim.c` (639), `gerelife.c` (606), `geretrak.c` (413),
`hqr_scene.c` (408), `p_func.c` (272), `globals.c` (192), `diskfunc.c` (191),
`message.c` (191), `fiche.c` (189), `grille_a.c` (176), `mainloop.c` (107),
plus headers. `p_sintab.c` (1028) is a table, it goes in ROM.

### 3.4 A single background framebuffer + actors as OP objects

It is the most important simplification, and it is enabled precisely by the
discovery of S23. If the actors are sprites composed by the Object Processor
**on top of** the background, then the background never needs to be redrawn per
frame — and **double buffering is no longer needed**. Updating an actor's
position means writing a few longs in the OP list, an atomic operation if done
in VBLANK.

This frees ~757 KB (via `BG_PRISTINE` and `BG_B`) and deletes all the
complexity of `PENDING_SWAP`, the sync of the two buffers, and the `Sync*`
routines (which were already dead code anyway). It also eliminates Blitter/OP
contention in the steady state, because in the steady state the Blitter does
not touch the background.

### 3.5 DRAM map: one file, one owner

The two collisions of S24 (blob inside the grid, `VBL_TICK` on top of
`GPU_ARG_OUT_COUNT`) arose because the map is implicit and scattered over two
sources. **A single `include/dram_map.inc`**, included by everyone, with every
region declared with base and size, and no address hardcoded anywhere else.

Indicative budget (2 MB):

| Region | Size |
|---|---|
| Vectors, OP list, scratch | ~64 KB |
| Background 640x592 8bpp (single) | 379 KB |
| Decoded brick buffer (scene loading) | 576 KB |
| Cube grid | 200 KB |
| Actor sprite bitmaps (~20 x 4 KB) | 80 KB |
| Engine arenas (`.data`/`.bss`, RAM copies of the bodies for anim, `ListObjet`) | ~200 KB |
| Stack | 64 KB |
| **Total** | **~1.56 MB** — ~450 KB of margin remain |

### 3.6 The boot must say it reached the end

Architecture requirement. At the end of init: a sign of life that is
**unambiguous and not dependent on colours** (CRY16 lesson from S24). An indicator pulsing at frame rate, or a
visible counter: something whose stopping is noticed immediately.

In addition: a **fully initialized** vector table with an exception handler
that signals instead of jumping into the void. In V4 only `$100` and the seven
autovectors were written; everything else was cold-boot garbage, and that is
why an Address Error showed up as a mute black screen.

### 3.7 What is kept and what is rewritten

**Kept (becomes a HAL library in RMAC):** complete GPU TOM rasterizer,
RLE brick and grid decoding, isometric projection and camera, 480i interlace,
OP sprite architecture, the whole Python extraction and packing pipeline,
joypad strobe.

**Rewritten:** boot and init, DRAM map, `OPSetup` and OP list management,
`ViHandler`, the main loop (moves to the engine's C), the cart layout.

**Imported from the 32X:** the logic `engine_src` (~5,000 lines, already derived
**and already debugged** — the S17 mines must not be rediscovered) and the shape
of the `hal.h` contract (322 lines, already written).

---

## 4. Milestones

Each with an explicit verification criterion. None is declared closed without it.

| # | Goal | Verification |
|---|---|---|
| **M0** | Skeleton: RMAC boot + C blob from the cart in place + `dram_map.inc` + complete vectors + sign of life | Pulsing indicator; an exception forced by hand produces a signal, not black |
| **M1** | HAL core: `TimerRef` 50 Hz from VBL, joypad input, `HQR_Get` on the cart, `Malloc` arenas, `TheEnd` | The C reads an HQR record from the cart and shows a known value from it — **closed S28** |
| **M2** | Background: real `LoadScene` + **placeholder cleanup** (real ChangeCube zones from SCENE.HQR, camera init from the game's rule) | Scene 0 identical to PC DOS; transition to the *right* room, not N+1 — **CLOSED S30**, 13 probes out of 13 on both steps |
| **M3** | Actors as OP sprites: `AffScene` as an iterator over `ListObjet`, body rendered into the sprite bitmap via GPU | Twinsen + the prisoner visible together, positioned by the scene data |
| **M4** | Animations: ANIM.HQR extraction + `p_anim.c` | Twinsen performs a real idle animation |
| **M5** | Scripts: `object.c` + `gerelife.c` + `geretrak.c` | An NPC moves and talks according to its script, not according to a hardcoded trigger |
| **M6** | Hero: `DoDir` + `grille_a.c` | Twinsen walks and the walls stop him |
| **M7** | Audio: soft-synth on the Jerry DSP | Citadel music |

M0-M2 are the real content of "skeleton". From M3 onwards it is porting that
follows the path of the 32X.

### M0 status: CLOSED (criteria in S25, video layer in S27)

Both M0 criteria were satisfied in S25; the video defect was not an M0
criterion but was noted as **"to be closed before M1"**, and it is closed in
S27 — on BigPEmu *and* on a real Jaguar (see below).

Built and verified on BigPEmu:

- `tools/memory_map.py` is the sole owner of the map and generates
  `include/dram_map.inc`, `include/cart_map.inc`, `engine_c/jag_engine.ld` and
  `engine_c/mem_map.h`. Holes and overlaps are build errors.
- `src/main_v6.s` initializes the **whole** vector table; every exception
  flashes its own vector number instead of vanishing into black.
- The C blob runs **in place from the cartridge** at `$842000`, with `.data`
  copied and `.bss` zeroed in DRAM. Three section probes make the boot fail
  in a telling way if the linker script is wrong.
- The cartridge layout is decided by `tools/cart_build.py` at fixed offsets.
- **Both criteria satisfied**: the marker advances by one slot per tick, that
  is, the C is executed from the cart every frame; and an exception forced by
  hand (Address Error, the same one that killed V4) produces **3 flashes**,
  that is, the number of its vector, instead of a mute black screen.

**Also verified on hardware (S27):** both the sign of life and the **fault**
path. Two different exceptions with different expected counts were tried —
illegal instruction = **4 flashes**, Address Error (the one that killed V4) =
**3 flashes** — and both confirmed on a real Jaguar. Two tests instead of one
because a single count does not distinguish "the mechanism reads the vector
number" from "something flashes": had they given the same number, the counter
would be broken and we would not know it.

**M0 is therefore closed on both platforms**, criteria and video layer.

### Video defect: CLOSED in S27, on emulator AND on real hardware

The defect that from S23 to S26 was described as "one field out of two not
drawn" is resolved. The diagnosis that was written here (lack of
synchronization between the parity written in `VI` and the field drawn) was
**wrong**, like the other three that preceded it.

**The real cause, measured:** on BigPEmu there is no per-field interrupt — 30.0
per second versus 60 fields/s, that is, one per **frame**, with a histogram of
the deltas `0:730 1:499 2:0 3:0 4+:0` (regular rhythm, no interrupt lost).
Between field A and field B there is therefore **no instant** at which to
restore the object, and no scheme that swaps `DATA` in the handler can work:
neither alternating `VI` nor fixed `VI`. That was why they gave the very same
frame.

**Solution on BigPEmu:** no interrupt is needed to know when an OP pass has
finished, because **the OP declares it by zeroing `HEIGHT`**. The handler arms
field A, watches that field and arms field B as soon as it goes to zero. The
detection threshold cuts the misalignment with a **1:1** relationship in field
lines (calibrated on four points; `HEIGHT < 12` cancels it).

**On real hardware the opposite premises hold**, and Zerosquare's recipe (field
parity from bit 11 of `VC`, `VI` re-armed for the next field) **works** — it
was the path that was impossible to validate on BigPEmu, because there bit 11
does not alternate (measured low on 344 interrupts out of 344).

**Validation on a real Jaguar** (RGB SCART -> RetroTink 4K), three independent
measurements:
- RetroTink: `1716x525i (1440x480)`, 27.0024 MHz, H = 15.74 kHz, V = 59.95 Hz,
  detected mode `CP 480i` — **canonical** NTSC 480i, not a timing tolerated by
  chance;
- on-screen geometry: row-300 marker at 62.5% of the height (on a 240-line
  display it would not exist);
- per-row ramp: 3.75 bands = 480/128.

No green stripe: both fields drawn.

Also fixed an **off-by-one of the even field** present **only on hardware** (the
even field showed row `L-2` at output row `L`), isolated with two blocks of
opposite edge parity and closed with a 2-row bias. It also eliminated the
vertical wobble of the marker.

**A single switch, `TARGET_HW`**, derives the six values of the two
configurations: mixing them silently reproduces the S23-S26 defect.

|  | `TARGET_HW=0` (BigPEmu) | `TARGET_HW=1` (real Jaguar) |
|---|---|---|
| `FIELD_SRC` | 0 (software counter) | 1 (bit 11 of `VC`) |
| `VI_ALTERNATE` | 0 | 1 |
| `SECOND_FIELD_POLL` | 1 | 0 |
| `POLL_THRESH` | 3 | irrelevant |
| `BG_RELEASE` | 1 | 0 |
| `FIELD_A_ROW_BIAS` | 0 | 2 |

Dead ends not to walk down again, with the reason they are dead ends:
- **two objects with `YPOS` of opposite parity**: `YPOS` is a threshold (`>=`),
  not an equality comparison, so both are active in both fields — and in any
  case field A consumes the `HEIGHT` of both;
- **all 18 timing registers**: tried, no change (kept because they are hygiene,
  and Zerosquare's values are verified with a logic analyzer);
- **`DWIDTH` at 80**: makes the artifact disappear only because it halves the
  vertical to 240 lines. A workaround disguised as a fix;
- **`RELEASE = 1` as a cure for the latency**: it is not (it contributes ~1.5
  lines, not the 8 being looked for).

Things to re-verify later: `POLL_THRESH` and `BG_RELEASE = 1` are
**BigPEmu-specific** calibrations; when the Blitter comes back in M2/M3,
`BG_RELEASE = 1` must be re-verified against the contention that in S22
produced garbage on the scanlines.

### M1 status: CLOSED (S28)

All five services up, criterion satisfied on the first boot.

**The criterion, and why it was tightened.** Taken literally ("the C reads an
HQR record from the cart and shows a known value from it") it would be content
with digits on screen, and digits on screen prove nothing: without a reference
a correct read cannot be told from a plausible one. It is the same trap as the
S23-S26 diagnoses, where an uncalibrated instrument produced four wrong
diagnoses in a row. So the comparison **is not done by eye**:
`tools/hqr_pack.py` computes the expected values when it packs the data and
writes them to `engine_c/hqr_expect.h`; the C blob re-reads the cartridge,
compares and shows a mask with one bit per probe. `row 0 = 000000FF` = eight
probes out of eight. Rows 1..8 show the values **read** anyway, so a failed
probe is diagnosed from the same screenshot instead of requiring another round.

Measured on BigPEmu:

| | read | expected |
|---|---|---|
| probe mask | `000000FF` | `000000FF` |
| container magic | `4A485152` | `'JHQR'` |
| version \| archives | `0001000B` | 11 archives |
| size | `0046B9B8` | 4,635,064 bytes |
| checksum | `1D543102` | same |
| `HQR_Size(SCENE,0)` | `000018E5` | 6373 bytes |
| `SCENE[0]` bytes 4..7 | `00008003` | same |
| `HQR_Size(BODY,0)` | `00001A04` | 6660 bytes |
| `BODY[0]` bytes 0..3 | `0300EFFE` | same |
| interrupts/s \| `NbFramePerSecond` | `001E001E` | 30 \| 30 |
| arenas used | `01000200` | `$100` \| `$200` |

The two live rows (interrupt counter and `TimerRef`) advance with a ratio of
5 to 3, that is, 50/30: it is the expected ratio, and it is verified **without a
stopwatch** by comparing any two captures. All the other rows stay still, which
is just as informative: a probe that moved would be a probe reading the wrong
thing.

**What was built.**

- **`tools/hqr_pack.py`** decompresses the original .HQR files and packs them
  into a "JHQR" container with two index levels, all big endian (native 68000)
  and with offsets **relative to the base** — so the container can be moved
  without rebuilding it, which is what M2's bank switching will need. Seven
  archives, 4.42 MB, 84% of `ROM_DATA`.
- **`HQR_Get` returns a pointer into the cartridge**: no copy to DRAM, no
  decompression at runtime. It is decision 3.3, and by itself it deletes the
  problem that had blocked the S24 PoC (the data did not fit in the 8 KB of
  DRAM that remained).
- **`TimerRef` at 50 Hz derived, not assumed.** The 32X port could write
  `vblank * 5 / 6` because there vblank is 60 Hz and that's it. Here interrupts
  arrive at **four** rates (60/50 on hardware with alternating VI, 30/25 on
  BigPEmu, which gives one per frame), NTSC/PAL is only known at runtime and the
  table row is chosen by `TARGET_HW`. The RMAC side resolves both and publishes
  `TICK_HZ`; the C consumes the **difference** of the ticks with an accumulator
  — no division, no 32-bit overflow, and a lost frame does not lose game time.
- **Two arenas** (persistent from the bottom, per-cube from the top,
  `HQM_Free_All` zeroing the second) tested by writing and reading back: an
  arena is the only resource in the project that runs out silently, so running
  out is a `TheEnd`, not a `NULL` to a caller that would not check it.
- **8x8 hex font drawn at 2x** (`engine_c/hal_debug.c`). It replaces M0's 32
  little squares per value, which had to be aligned by hand against a
  calibration row and where a misalignment was mistaken for data. The
  **vertical** 2x is not aesthetics: in 480i a glyph 8 lines tall would have
  half of its lines in one field and half in the other, that is, it would
  flicker at 30 Hz. By doubling, every line ends up in both fields and the
  glyph stays still. Cost 512 bytes of ROM, and from M2 on it is the tool with
  which everything is read.
- **`HAL_VARS` now has a single owner.** In M0 its fields were written by hand
  in `src/main_v6.s` **and** in `engine_main.c`: it worked as long as they were
  not touched, but it is the same failure shape that in S24 put `VBL_TICK` on
  top of `GPU_ARG_OUT_COUNT`. Now `tools/memory_map.py` declares them, emits
  them again for both sides and rejects overlaps and misalignments.

**Input: how it is verified, and what it gave.** Rows 12, 13 and 14
**accumulate** the bits ever seen pressed and never go back to zero, so the
controller is tried by hand and the screen is looked at afterwards, without
having to make the press coincide with taking a screenshot. Three rows and not
one because they test three layers that break separately:

| row | layer | read |
|---|---|---|
| 12 | strobe matrix, all 4 rows | `PadSeen = 01FF` = all nine buttons |
| 13 | mapping onto `MyJoy`/`MyFire` | `0F` \| `27` = all four `J_*`, `F_SPACE\|F_RETURN\|F_CTRL\|F_SHIFT` |
| 14 | numeric keypad | `PadKeysSeen = 03FF` = all ten digits 0-9 |

`MyFireSeen = 27` is not just any number: it is exactly
`F_SPACE|F_RETURN|F_CTRL|F_SHIFT` = 1+2+4+32, that is, the four bits onto which
A, Option, B and C are mapped and no others. The mapping is verified bit by
bit, not "it seems to respond".

`*` and `#` were not pressed, so they remain the only two controller keys never
exercised.

The accumulators also have a second use: **they prove absence**. Twenty seconds
of running without touching anything leave all three rows at zero, that is, in
~600 pad reads not a single spurious bit came in. An instantaneous value could
not say that: it would show zero even if one frame in a hundred were wrong.

The read uses a **long access** to `$F14000` (JOYSTICK in the high word,
JOYBUTS in the low one), which is the sequence verified on a real Jaguar
(`move.l JOYSTICK, d0`). Two separate word reads work too, but they sample the
two registers at different instants relative to the strobe: there is no reason
to depart from a sequence verified on real hardware.

Dead end noted: automating the press with `keybd_event` on the arrow keys
(`emu_shot.ps1 -Hold`) **does not verify the input** — the arrows are not
BigPEmu's default mapping, so the game sees nothing and the screen shows zero,
which is mistaken for "the pad cannot be read".

Open and deferred: the button -> PC key mapping (`A`->`F_SPACE`,
`B`->`F_CTRL`, `C`->`F_SHIFT`, `Option`->`F_RETURN`) is **provisional**. It
must be compared with what `DoDir` and its callers actually test in
`object.c`, and that happens at M6 when the hero starts walking.

**Byte order of the payloads: decision deferred to M2, deliberately.** The
payloads stay verbatim little endian as the 1994 Watcom wrote them. The
container is a container: if it converted, it would need a parser for every
resource format and a conversion error would become indistinguishable from an
engine error. At M2 the choice will be between `le16`/`le32` accessors on the C
side (cost on every read, zero risk) and per-format offline swapping (zero cost
at runtime, needs a parser per format); the 32X port used offline swapping for
SCENE and TEXT, so the precedent exists for both roads.

### M2 status: HALF DONE (S28) — the data path, not yet the background

Split into two halves that are verified in different ways: reading the real
scene, and drawing it. The first is closed, the second is not.

**Done: the data path, verified on the first boot.** The C walks the real SCENE
struct from the cartridge and the right-hand column of the diagnostics gives 8
probes out of 8: hero `(26880, 768, 27626)`, camera `(53, 54)`, 23 actors, 31
zones, 2 change-cubes to scene 1, music 18, and the walk reaching exactly the
end.

**The format was not guessed.** `tools/scene_parse.py` and
`engine_c/scene_walk.c` are the transcription of `LoadScene()` from
`lba1-classic-community-main/SOURCES/DISKFUNC.C:43-241`, that is, of the code
that **wrote** those bytes, plus `T_ZONE` (COMMON.H, 12 WORD) and `T_TRACK`
(DEFINES.H, 3 WORD). The offsets are a reading, not a hypothesis to confirm.

**The check that makes the walk credible.** The struct is variable-length: every
actor carries two scripts preceded by their size, so an error of ONE byte
shifts everything that follows. At the tail only the `T_TRACK` entries remain,
which nobody skips, so the residual bytes must equal **exactly**
`NbBrickTrack * 6`. Required on **all 120 scenes: 120 out of 120**. A wrong
layout does not pass it by chance, and the same condition is probe 7 on the
Jaguar side — if it fails, the other seven values are not "a bit wrong", they
are read from the wrong place.

**Cleanup of the two placeholders, with a surprise.**

- *Transitions:* zones of **type 0** (`OBJECT.C:3436`, "change cube perso")
  give the destination scene in `Num` and the entry position in `Info0/1/2`.
  They replace the cyclic `N -> N+1` map. Note: for scene 0 that map **was
  right by chance** (its two zones both point to scene 1), so it could not be
  discovered by looking at scene 0.
- *Camera:* the plan said "camera init from the 120 values of the CSV".
  **Wrong**, and by an off-by-one on all 120 scenes. The game's real rule
  (`OBJECT.C:577`, `PERSO.C:773`) is
  `StartXCube = (PosObjX + DEMI_BRICK_XZ) / SIZE_BRICK_XZ`, that is, rounding
  to the **nearest**; the S20 CSV has the cells **truncated** (`world >> 9`).
  For scene 0: cell `(52, 53)` but camera `(53, 54)`. The rule beats the table.

**Closed the decision deferred from M1 on byte order: runtime accessors**
(`engine_c/le.h`), and not out of taste. LBA1's structs alternate one-byte and
two-byte fields, so words fall at **odd** offsets: `CubeStartX` follows the
single byte `CubeJingle`. On the 386 it cost nothing, on the 68000 it is an
Address Error. The 68000 must therefore read them byte by byte **anyway**, for
alignment, and at that point composing them little endian costs **zero
extra** — they are the same two accesses. Offline swapping instead would have
required a parser for every format **and would not have solved the alignment**.
The 32X port reached the same conclusion for the same two reasons
(`diskfunc.c`, points 2 and 3), on a CPU that is also big endian and intolerant
of misalignment.

Corollary that holds elsewhere too: where upstream does
`ListZone = (T_ZONE *)PtrSce` the fields must be **copied while decoding them**,
not pointed to, otherwise every later access inherits both problems.

### M2b status (S29): the background — DRAWN, 12 probes out of 12 on BigPEmu

The second half of M2. **Scene 0 is visible**, and it is verified against the
offline reference: row 32 = `00000FFF`, that is, all twelve probes, Fletcher
included. The Jaguar's framebuffer is `build/bg_ref_scene000.png` byte for
byte.

The **loading time** remains open, measured and not small: see the end of this
section.

**The background data enter the JHQR container, and the parallel format dies.**
`LBA_GRI`, `LBA_BLL` and `LBA_BRK` are HQR archives like all the others: they
are now archives 11, 12 and 13, with LBA1's **original indices**. With them
`tools/cart_pack_all.py` and its "JAGLBA03" format disappear, which had its own
directory, a remapping of the bricks and the two invented placeholders.
Keeping the original indices is not laziness: it means a number read on the
Jaguar can be compared with the same number printed by the Python tools.

**The subset, and why it comes before bank switching.** With all 134 scenes the
container asks for ~10.8 MB against the window's 6. But the background of scene
0 uses 526 bricks out of 8715 = 250 KB, and in `ROM_DATA` there were 841 free:
`hqr_pack.py --scenes 0` produces 4.73 MB, 90%. It is not postponing a problem,
it is separating two: if the background did not show on a freshly written
banked cartridge, one would not know whether the defect lies in the renderer or
in the banks. Bank switching will be exactly what turns `--scenes 0` into
`--scenes tutte`, and at that point it will have a working reference to be
verified against.

**No new RMAC.** Grid, bricks and composition all sit in
`engine_c/scene_bg.c`. The rule "no loop over pixels on the 68000" applies to
the steady state, and in any case the 68000 here does not touch a single screen
pixel: it decodes RLE (~460 KB of sources that the Blitter then uses 756 times)
and programs the Blitter, which is nine long writes per cell. Programming the
Blitter is not a loop over pixels: the loop is done by the Blitter.

**Correction found by measuring, not on screen: the cell window no longer
existed.** V4 walked `camera +- (9,12,12,14)` cells. The Python reference says
that for scene 0 the cells producing visible pixels reach `gz-15`: that window
lost **three columns of them, 2307 pixels**, and not at the edges but inside
the image, where a hole is mistaken for a missing tile. It was not a
calibration to fix. The visible region in `(gx,gz)` is a **rhombus**, because
the screen coordinates are `gx-gz` and `gx+gz`, and no axis-aligned box contains
it without cutting or wasting Recalibrating the
four numbers would have made them come back here and fail in another room. Now
the whole cube is walked with a **per-column** rejection on X, which is the
exact cut: the reference requires the result to be byte-for-byte identical to a
brute force with no rejection at all, and it is.

**The Blitter width table is derived, not copied.** V4 had 64 values written by
hand. The TRM Rev.8 encoding is `1.mm x 2^e`, that is,
`larghezza = (4+m) << (e-2)` with field `eeee mm`; cross-checked against the
manual's two examples (20 -> `17`, 640 -> `37`, and `37` is precisely the WID
that V4 uses for the full-screen clear). The function reproduces V4's table
**64 out of 64**, and the C version and the Python version are compared value by
value.

**How it is verified.** `tools/bg_ref.py` does two jobs: it renders the
background with the same three decodings and writes a PNG of it, and it computes
the expected values into `engine_c/bg_expect.h`. "The scene is visible" is not
a criterion — *a* scene is visible even with the camera wrong by one cell, with
one tile in ten missing, with the painter's order inverted. The twelve probes
split the job of saying **what** went wrong: different bounding box = shifted
image; same bounding box and different count = missing tiles; everything equal
and different Fletcher sums = right pixels in the wrong places. The **C** key
removes and restores the diagnostics, so the same boot produces both the mask
and the clean screenshot for the comparison by eye.

Expected values for scene 0: camera `(53,54)`, `vx=347 vy=-1033`, 526 tiles,
447 KB of work buffer, 756 cells, 238,901 non-transparent pixels,
Fletcher `86950838` / `C722F284`.

**Open: 24 pixels of framing.** The camera from the game's rule is `(53,54)`;
the centring constant `CAM_CY = 191` was calibrated in S20 against PC DOS
**with the truncated camera `(52,53)`**. The two choices give the same image
shifted by 24 rows, and one of the two absorbs the off-by-one that S28
corrected. It is decided with a real DOS screenshot, not by reasoning about it:
`bg_ref.py --cam 52,53` produces the other variant for the comparison.

**Two defects found on the first boot, and the probes found them.** The mask
came out `000000FF` instead of `00000FFF`, that is, 8 out of 12, and the three
failed probes said by themselves what it was about:

- `NONZERO` **exactly 256 more** = 16x16 = the little square of the phase
  indicator, drawn after `render()` and before `measure()`. `SUM1` differed by
  exactly 64, which are 64 longs of `FFFFFFFF`: the same pixels. General
  lesson: an instrument that writes into the framebuffer must be turned off
  **before** measuring the framebuffer.
- `CLUT` differed by `FFFF-4210`, that is, by index 3 alone: the CLUT on screen
  was right, it was the *sum* that was computed before the forcing. A sum that
  does not describe what is in memory is worse than no sum.

Neither of the two would have been seen by looking at the screen, and that is
why the mask exists.

**The real defect, and its diagnosis.** The first boot gave a **uniform green
screen** - the BG colour, that is, the OP drawing nothing - even though the
scene was drawn perfectly underneath. The cause is the S27 discovery applied to
a new case: **the OP consumes the object by zeroing its `HEIGHT`**, and it is
`ViHandler` that restores it at every interrupt. `engine_init` runs with interrupts
masked: as long as it lasted milliseconds nothing was seen, but now it lasts
seconds, so after the first field the background disappeared for the whole
loading. **The background is therefore built from the first round of
`MainLoop`, not from `engine_init`** - and there is also the only place from
which the duration can be measured, by counting interrupts.

**The duration, measured per phase** (BigPEmu, 30 interrupts/s):

| phase | interrupts | seconds |
|---|---|---|
| `grid_decode` | 50 | 1.7 |
| `mark_bricks` | 71 | 2.4 |
| `decode_bricks` | **213** | **7.1** |
| `render` (756 blits) | 70 | 2.3 |
| `measure` (diagnostics only) | 191 | 6.4 |
| **total** | **595** | **19.8** |

Removing `measure`, which will not exist in the game, **13.5 s per room**
remain: not shippable. The bulk is the RLE expansion, 458 KB written byte by
byte by the 68000. `mark_bricks` and `render` each walk 102,400 cells, and
they can be fused into a single pass with a precomputed BLL block table.

### Real hardware (S29): 12 probes out of 12, and 3.1 stands

Tried on a real Jaguar with `TARGET_HW=1` (`build/jaglba_v6_M2b_HW.j64`), RGB
SCART output -> RetroTink 4K. **Mask `00000FFF`**: the background on real
hardware is bit-for-bit identical to the reference computed in Python, Fletcher
included. It is also the first time a game image, instead of ramps and
calibration markers, goes through the 480i pipeline validated in S27.

Times, compared in seconds (hardware 60 interrupts/s versus BigPEmu's 30):

| phase | BigPEmu | hardware | ratio |
|---|---|---|---|
| `grid_decode` | 50 -> 1.67 s | 120 -> 2.00 s | 1.20 |
| `mark_bricks` | 71 -> 2.37 s | 148 -> 2.47 s | 1.04 |
| `decode_bricks` | 213 -> 7.10 s | 529 -> 8.82 s | 1.24 |
| `render` (756 blits) | 70 -> 2.33 s | 110 -> **1.83 s** | **0.79** |
| `measure` (diagnostics) | 191 -> 6.37 s | 446 -> 7.43 s | 1.17 |
| **total** | 595 -> 19.8 s | 1353 -> 22.6 s | 1.14 |

**A hypothesis refuted by measurement, and it must be written down.** During
the session it had been hypothesized that the bulk of the cost was *fetching
instructions from the cartridge*, that is, decision 3.1. If that were true, the
hardware with 10 wait clocks - which BigPEmu ignores entirely - should be much
slower than the model. It is **14%** slower. The cartridge waits cost at most
one fifth of the time, not an order of magnitude: **3.1 is not the problem and
copying the C blob into DRAM is not the cure.** The cost is the work itself,
458 KB of RLE expanded one byte at a time by a 68000.

**Side data point that confirms the design:** `render` is 21% FASTER on real
hardware than in the model. It is the only phase dominated by the Blitter, and
the real Blitter beats its emulation — that is, the rule "the 68000 does not
touch pixels" is rewarded exactly where it matters.

**Where to attack** (removing `measure`, which will not exist in the game: 907
ticks = 15.1 s remain): `decode_bricks` **58%**, `mark_bricks`+`render`
**28%**, `grid_decode` **13%**. In order of leverage: inner loops of the RLE
expansion (count clamp outside the loop, pointer instead of index) for 2-3x;
expansion on the **Tom GPU**, which is the real leap and already has the
infrastructure from V4; fusion of `mark_bricks` and `render` into a single pass
with a precomputed BLL block table, instead of an `LE32u` from ROM plus a
three-factor multiplication per cell.

Expanding the bricks **offline** would zero the phase but takes them from 4.04
to ~8 MB, and crashes head-on into the 6 MB window.

**Cartridge speed: chapter CLOSED on three sides.** Taking `CART_CONFIG` from
`$04` (32 bit / 10 clocks) to `$1C` (32 bit / 5 clocks):

- on **BigPEmu** not a single tick changes: it ignores that configuration;
- on a **real Jaguar** the cartridge **does not start at all** - red screen at
  BIOS boot, before even reaching our code. The two images differ in **exactly
  4 bytes** (`$400-$403`), so the attribution is unambiguous: it is not the
  code, it is the timing. The cartridge cannot sustain accesses at that speed;
- and even if it could, the ceiling was ~20%, bus contention included.

It follows that **decision 3.1 stands** (the C runs in place from the
cartridge) and that the loading work goes entirely into REDUCING THE WORK, not
into speeding up memory. 8 and 6 clocks were not tried: expected gain in the
order of 5% on a number that the RLE optimization will change by 3-5x.

**Not done, and must not be passed off as done.** The M2 criterion ("scene 0
identical to PC DOS; transition to the right room") **is not satisfied**:
the comparison against DOS awaits a real screenshot (the 24 pixels above), the
transitions remain read and unused, and loading takes thirteen seconds.

What follows is the state at the end of S28 and remains valid except where the
paragraph above supersedes it.

- the **background** is not drawn: grid, brick decoding and blit are not yet
  hooked up (the code exists since V4 in `src/gpu_raster.s` and
  `tools/cart_pack_all.py`, it must be brought back under the new
  architecture);
- **bank switching** is not there. *(S30: measured, and NOT needed — see the
  section at the end of this milestone.)*
- the transitions are **read, not used**: nobody changes scene yet;
- `scene_walk.c` is a **declared** duplication of the walk in `diskfunc.c`,
  with a written removal condition: it is needed because the zones come after
  the actors and M2 needs them before the actor system exists. When `T_OBJET`
  and `ListObjet[]` arrive at M3, the 32X port's `diskfunc.c` is imported
  (decision 3.7) and of this file only `Scene_Derive` remains.

**Still open from S27, not an M1 regression:** drawing concurrently with the
display. The 68000 writes into the framebuffer while the OP is reading it, so
digits that change from one frame to the next come out split between the two
fields. It is visible only on the live rows (10 and 11) because `Dbg_Value`
redraws only what changes. It is contained and diagnostic for now, but it must
be closed before M3 puts moving actors on screen.

Budget note: V4's `cart/scene_all.bin` (5.80 MB) is not reusable.
*(S30: and the 768 KB reserved for code and blob are the real lever, not the
data — see «Bank switching: measured, and NOT needed» at the end of the
milestone.)*

---

### M2 status (S30): CLOSED — all three criteria, 13 probes out of 13

The M2 criterion asked for two things, and the second contained a third:
*«Scene 0 identical to PC DOS; transition to the right room, not N+1»*.

**1. Identical to PC DOS: closed by the rule, not by a screenshot.** The 24 px
of framing left open in S29 were waiting for a comparison by eye with a DOS
capture. It was not needed: the mapping is in `Map2Screen`
(`SOURCES/GRILLE_A.ASM:446`, identical in `GRILLE_A.C:209`) and it is exact.

```
XScreen = (xm - zm)*24            + 320-8-23-1   = ... + 288
YScreen = (xm + zm)*12 - ym*15    + 240-25       = ... + 215
```

The constants were `CAM_CX = 323` and `CAM_CY = 191`, calibrated by eye in S20.

- **The Y is explained completely, and it is the nicest confirmation of the
  session:** 191 was *right* for the truncated cell `(52,53)`; with the cell
  given by the game's rule, `(53,54)`, it becomes `191 + 24 = 215`, that is,
  exactly the `240-25` of `Map2Screen`. The two errors cancelled each other out
  — and that is why a calibration by eye could look perfect while resting on a
  wrong camera.
- **The X instead was wrong by 35 px** with nothing to compensate it: it started
  from «320 = half the screen», which is an assumption and not a measurement.
  With 288 the room is centred, the left wall comes back in and the floor is no
  longer cut.

`StartYCube` stops being `CAM_GY_FIXED = 4` written by hand and becomes the
rule of `OBJECT.C:578`, which has an **asymmetry that looks like a typo and is
not**: Y adds a WHOLE brick where X and Z add half of one.

**Added the game's per-tile rejection** (`AffBrickBlock`, `GRILLE.C:631`:
`XScreen >= -24 AND < 640 AND YScreen >= -38 AND < 480`) in place of
`CELL_MARGIN = 128`, which was an invented safety margin. Measured: out of 799
cells it rejects 4 and changes **zero pixels** inside the 480 visible rows —
the 1409 pixels of difference are all below the edge. It is not a change of
image, it is a rule in place of a number.

**2. The transition: closed with the TRIGGER pressed by hand, not with the
destination.** In the real game that change-cube is at the far end of the room
and it is reached after the first fights, that is, after M3, M5 and M6. The A
key crosses it. What is skipped is the *way* of entering it: destination scene
and entry position keep coming from the type-0 zone read from the cartridge —
if they were hardcoded, the criterion would no longer prove anything.

**The easiest point to get wrong, and the reason it was read instead of
deduced:** entering through a door, the camera is NOT the starting camera of
the destination room. `ChangeCube` (`OBJECT.C:518-537`) does

```
NewPos = heroPos - zone.X0 + zone.Info0        (per axis)
ListObjet[0].PosObj = NewPos
...and ONLY THEN Start[XYZ]Cube from that position
```

that is, it preserves the hero's offset *inside* the zone. Without a hero
(arriving at M3) one enters from the corner, `posEroe = zona.X0`, which gives
exactly `Info0/1/2`. `Bg_EnterAt` applies it **between** `Scene_Walk` and
`Scene_Derive`, which is the same order as the original: applying it afterwards
would give the right room framed from the wrong place.

**Verified**: scene 1 (the Citadel courtyard with FunFrock's banners) with
camera `(37,13)`, 531 bricks, 8290 cells resolved, 1190 drawn,
Fletcher `5C482A3A`/`7FE34F42` — **13 probes out of 13**, identical to the
reference computed offline.

`engine_c/bg_expect.h` is no longer a handful of defines but an **array of
steps**: step 0 = the initial scene, step 1 = the room being entered. A full
mask of the wrong room and a full mask would be indistinguishable without it;
row 29 shows the step next to the result.

**3. Loading: 404 → 218 ticks, 1.85x.** Measured on the SAME machine
(BigPEmu) by re-running the S29 build, not by comparing different platforms.

| stage | S29 | S30 | |
|---|---|---|---|
| `grid_decode` | 50 | 31 | 1.6x |
| cube → cells | 71 | 45 | 1.6x |
| `decode_bricks` (RLE) | 213 | 100 | 2.1x |
| `render` | 70 | 42 | 1.7x |
| **total without `measure`** | **404** | **218** | **1.85x** |

Three interventions, all decided by a measurement taken beforehand:

- **The compact cell list.** The cube has 102400 cells of which 8781 name a
  brick: 91% of the visits produce nothing, and up to S29 that 91% was paid
  *twice*, in `mark_bricks` and in `render`. Now the cube is walked once and
  produces a list in painter's order of 6 bytes per cell (`px`, `py` already
  computed **without camera**, plus the brick index). `render` no longer
  touches the cube or the BLL. It is also what will make M6's camera snap
  cheap, where the background is redrawn but the grid does not change.
  Sized on a measurement of **all 133 grids**: the heaviest is scene 8 (the
  Temple of Bu) with 9852 cells; `CELL_SLOTS = 12288` holds them with a 25%
  margin, and `bg_ref.py` fails the **build** of whoever overflows instead of
  letting it be discovered with a `TheEnd`.
- **The precomputed BLL table.** Every cell paid an `LE32u` inside the
  cartridge plus two 32-bit multiplications — which on the 68000 are calls to
  `__mulsi3`, not instructions — for a value that depends only on the block.
  Now it is computed once per scene, at most 255 times.
- **The inner RLE loops with pointers, and zeroing by the Blitter.** The guard
  was per pixel and became per *run*. Skips are 45.4% of the pixels (200,041
  bytes in 18,043 runs of 11 pixels on average) and on an already-zeroed buffer
  they are a pointer addition. **The first attempt zeroed with a loop of longs
  on the 68000: it worked but it moved the work instead of removing it**
  (458 KB remain 458 KB, written by the slowest CPU in the machine). With the
  Blitter, `decode_bricks` drops further from 115 to 100 — that is, zeroing was
  not the bottleneck, *reading* the RLE from the cartridge is.

**The green stripe is closed**, and not by hiding it: `BG` goes to black when
`Bg_Build` succeeds, so green = loading or the OP is dead, black = running.

### On real Jaguar (S30): 8.0 s measured, and the transition verified on real hardware

`build/jaglba_v6_M2_HW.j64`. Loading **without `measure`**, which will not be
in the game:

| stage | S29 hw | S30 hw |
|---|---|---|
| `grid_decode` | 120 | 72 |
| cube -> cells | 148 | 111 |
| `decode_bricks` | 529 | 249 |
| `render` | 110 | 48 |
| **total** | **907** = 15.1 s | **480** = **8.0 s** (1.89x) |

After the A key: step 1, courtyard loaded in 499 ticks = 8.3 s, and **row 32 =
`00001FFF`**, that is, thirteen probes out of thirteen *on hardware*. It is the
stronger proof of the two: step 1 does everything step 0 does plus the
transition, on a scene that fills the work buffer to 92% (529 KB out of
576) and draws 1190 cells instead of 795. First room change ever verified on a
real Jaguar.

**BigPEmu <-> hardware calibration**, which makes future measurements
predictive and not only comparative. Ticks are NOT worth the same (30/s on the
emulator, 60/s on real hardware), so the comparison is done in seconds:

| | BigPEmu | Jaguar |
|---|---|---|
| 68000 work (grid + cells + RLE) | 5.9 s | 7.2 s |
| `render` (Blitter) | 1.40 s | **0.80 s** |

On the 68000 real hardware is ~20% slower; on the Blitter it is almost **twice
as fast** as BigPEmu models it. It also holds on the S29 data (0.79 versus
1.24), so it is not an artifact of the single measurement.

Cross-check that the instrument behaves as it should: `measure` costs 568 ticks
on the courtyard versus ~464 on the hospital, and it is exactly the ratio of
non-transparent pixels (316,444 versus 254,388: 1.24 versus 1.22).

**What remains open after M2**

- **Loading is 8.0 s on real hardware**, measured (see below). The big piece is
  the RLE expansion, 4.15 s by itself, and the real lever is no longer the C:
  it is the **Tom GPU**, with the infrastructure already ready from V4. A
  separate work item.
- **`measure()` costs as much as everything else** (203 ticks versus 218) and
  it is pure instrumentation: it will not be in the game. It is the reason why
  the *perceived* loading is worse than the real one.
- **Bank switching: MEASURED AND NOT NEEDED.** See the section below.
- Drawing concurrently with the display, open since S27.

### Bank switching (S30): measured, and NOT needed — the cartridge holds them ALL

**Status: done, not planned.** `build_v6.bat` packs `--scenes tutte`:
5,901,532 bytes out of 6,021,120, and on the Jaguar the probes all hold (row 0
`000000FF`, row 32 `00001FFF`, row 3 `005A0CDC` = the whole container). Loading
is identical to that of the two-scene cartridge: 120 scenes in ROM cost
nothing, because only what is needed is touched.

On the C side an LZSS decompressor in `hal_hqr.c` (~50 lines) was enough, plus
a 64-entry cache in the per-cube arena, invalidated by `HQM_Free_All`.

**Verified on a real Jaguar**, in both rooms. The M1 mask (`000000FF`) stops
being a formality: those eight probes read `SCENE[0]` and `BODY[0]` through
`HQR_Get`, so they are the proof of the decompressor. Cost on real hardware:
room construction is **unchanged** (480 -> 481 ticks on the hospital, 499 ->
500 on the citadel) because decompression falls outside the timed stages; the
bill is in the total, ~40 ticks = **about half a second per room**.

Taken as mandatory since M0 («the 6 MB are tight», 3.2). It was measured
instead of assumed, and the result overturns the decision: **the complete game
fits in 6 MB without a mapper**. `tools/hqr_pack.py --scenes tutte --compress`
does not estimate it, it builds it: it actually lays out the bytes and writes
`cart/data_v6_compresso.bin`.

**From 11061 KB to 5763 KB, in three moves — all rules, no hand-made list.**

*1. The bloat was the container, not the data.* JHQR has always stored the
payloads DECOMPRESSED, because `HQR_Get` returns a pointer into the cartridge
and the data is used in place. Measured per archive, the cost is not spread
evenly:

| | compressed | decompressed | bloat |
|---|---|---|---|
| BRICK | 3883 KB | 4138 KB | **1.07x** |
| RESS | 932 KB | 2872 KB | 3.08x |
| GRID | 587 KB | 1645 KB | 2.80x |
| BLL | 54 KB | 771 KB | 14.16x |

Hence the **per-archive** policy rather than a global one (`STORE` in the
packer). For 'lzss' LBA1's original bytes are copied **as they are**, 10-byte
header included: no compressor is needed and the data is bit-for-bit identical
to what the game shipped.

*2. Deduplication, 442 KB, and an inconsistency revealed it.* `LBA_BLL.HQR` is
54 KB on disk but the sum of its 120 entries comes to 493 KB: the scenes
**share** the block libraries, and the HQR offsets said so by pointing to the
same place. The container copied them one by one. Deduplicating is free and
**invisible to the Jaguar**: two slots point to the same payload, and `HQR_Get`
returns two equal pointers that are both valid. Not a single line of C.

*3. Pruning by rule.* RESS has nine entries of **exactly** 640x480 bytes — the
full screens (intro, menu, holomap, game over): 727 KB out of 931, and none is
needed to be inside a room. They are recognized by their size, which is a
verifiable rule and not a list to keep up to date. They must be put back for
the intro, but not in this form. And listing the scenes instead of saying "keep
everything" prunes by itself the 247 bricks that no scene names and the 14
surplus GRID/BLL slots.

**The verdict, and the knob that remains**

| BRICK | container | code+blob 512 KB | 384 KB | 256 KB |
|---|---|---|---|---|
| uncompressed | 5.63 MB | -140 KB | -12 KB | **+116 KB** |
| compressed | 5.35 MB | **+145 KB** | +273 KB | +401 KB |

**BRICK stays uncompressed**: it would gain 285 KB but would add an LZSS of
~230 KB per room on the loading path, that is, it would pay for precisely the
8.0 s just gained. It remains as a **documented reserve**, to be pulled if
audio asks for space.

The number to keep an eye on is no longer `ROM_DATA` but **code+blob**: today
768 KB are reserved and 11 are used, and they are what decides whether the data
fits. At 384 KB it overflows by 12 KB.

**What this proves and what it does not.** It proves that the bytes, actually
laid out, fit. It does NOT prove that the 68000 can read them: that needs an
LZSS decompressor on the C side and per-room decompression inside the cube
arena (which is exactly what the arena is for; the decompressed set per room is
a few KB).

**And audio does not fit.** Measured in the same round, because it was the
remaining unknown:

| | |
|---|---|
| `SAMPLES.HQR` (effects) | 230 entries, **2371 KB** compressed, 3483 decompressed |
| `Vox/` (speech) | 99 MB, all languages |
| music | mp3 only (GOG CD audio); the XMIDIs are on the floppy |

Speech is out of the question and not needed: LBA1 has subtitles. But the
sound effects alone ask for 2371 KB against the 116 of margin. So the picture
is clear-cut: **without audio the complete game fits in 6 MB; with the full
effects ~8 MB are needed.**

Three roads, none to be decided now because audio is M7 and touches nothing
from M3 to M6: redo the samples for the Jaguar (the DSP plays 8-bit PCM
directly, and a 4:1 ADPCM would bring 3483 KB to ~870 — which is still not
enough), keep a subset of them, or the mapper. Building goes on on the 6 MB
base without a mapper, which is proven for everything else, and the cartridge
will be decided by audio at M7 with the requirement already in hand instead of
discovered then.

**If the mapper were needed, the granularity is the ISLAND.** Measured:
partitioning the bricks by island would cost **114% more** (8054 KB versus
3757), because they are heavily shared — so as a way to make the data fit,
islands make things worse. But the hungriest island wants only **1311 KB** of
bricks (island 0, 18 scenes, 3036 bricks), so a 2 MB bank covers any one of
them, and the switch would fall on the boat trips: rare, with a natural loading
moment, never inside a room. It is a much simpler contract than banking per
scene.

---

## 5. Genuinely new work

The only subsystem we will not inherit from anywhere is **audio**: the
Jaguar has no hardware synthesis, so XMIDI music requires a wavetable
soft-synth on Jerry (26.6 MHz, 8 KB local — feasible) or offline conversion
to a stream. The 32X does not lead the way here because it comes with the
YM2612. Deferred to M7, but the DRAM must be reserved right away.

---

## 6. References

- HAL contract to satisfy: `D:\Homebrew5\32XLBA\m10-engine\hal\hal.h`
- Derived engine to import: `D:\Homebrew5\32XLBA\m10-engine\engine_src\`
- Derivation method: `D:\Homebrew5\32XLBA\docs\engine_port_arch.md`
- Function-by-function audit: `D:\Homebrew5\32XLBA\docs\derivation_audit_s17.md`
- Original source: `lba1-classic-community-main/SOURCES/`
- Code to recover: `src/gpu_raster.s`, `src/scene_viewer.s`, `tools/*.py`
- m68k C toolchain: Docker image `marsdev-32x`, `/marsdev/mars/m68k-elf/bin`
