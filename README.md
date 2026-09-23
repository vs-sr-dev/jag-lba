# JagLBA

**Little Big Adventure (1994) on the Atari Jaguar**, as a 6 MB cartridge.

The original game engine is Adeline's C source, released under the GPL by
2.21 and maintained by the community. It runs on the Jaguar's 68000. Around
it sits a Jaguar-specific renderer: the isometric background at 640×480
interlaced, 3D bodies on the GPU (Tom), and the hero as an Object Processor
sprite.

**This is a work in progress, far from playable.** The section below says
exactly where it stands.

**No game data is included**, and none is needed to read the code. To build
a ROM, you need your own copy of LBA1 (see [Building](#building)).

---

## Status (September 2026)

Tested on **BigPEmu** only. The current code has **not been run on real
hardware yet**. An earlier renderer-only build did run on a real Jaguar
(details below).

### What works (emulator)

- Boot → Adeline logo → main menu (a 1:1 port of `GAMEMENU.C`) → *New game*.
- The first room, the Citadel prison cell, is loaded by the original engine
  (`ChangeCube`/`LoadScene`): 23 actors and 31 zones.
- The original **life and track scripts** run. Twinsen dresses as a
  prisoner through his own script, the nurse follows her track, and zones
  trigger.
- Walking with the pad through the original `DoDir`/`DoAnim`. Walls block.
- The opening sequence plays up to the **first dialogue**. Switching to
  *Aggressive* summons the nurse on the hover platform: *"Stop
  squirming, prisoner Twinsen!"*. The text uses the original font, from the
  original text archive.
- All 120 scenes' data fits in the 6 MB cartridge without bank switching.
  Not all of it can be reached yet.

### What does not work yet

- **You cannot leave the cell yet.** This path (dialogues → door → room
  change) is what is being worked on now.
- **Actors flicker.** The background has a single visible framebuffer, and
  a moving actor is erased and redrawn in front of the viewer. On the
  emulator this shows as striped actors. The fix is to compose off-screen
  and copy with one blit; it needs DRAM that is not free yet.
- **The full-screen snapshot the engine uses around menus and dialogue
  windows** (`Screen`, 300 KB) does not exist. Those copies are skipped,
  so a window leaves traces until the next full redraw.
- The behaviour menu opens, but the four Twinsen poses in it are not drawn.
- **No sound.** An OPL2 music synthesiser on the DSP (Jerry) exists and was
  measured on hardware, but it is not connected to the game yet.
- Text: English only, and only the system texts, the general game texts and
  the first island (Citadel) are in the cartridge.
- No intro slides, no FLA movies, no holomap, no inventory models, no save
  or load.
- Speed: the renderer alone measured **7.5 fps** on a real Jaguar (August
  2026). The full engine has not been measured, and performance work is
  paused until the game can be played through its first rooms.

### Verified on real hardware (earlier builds)

- 640×480 interlaced output (checked with a RetroTink 4K: 525 lines at
  59.95 Hz).
- The background renderer, GPU body pipeline and OP sprite hero, at 7.5 fps
  in the first room.
- Cartridge boot from a GameDrive, with scene loading in about 8 s.

---

## Controls (Jaguar pad)

| Button | Action |
|---|---|
| D-pad | walk / turn |
| A | action of the current behaviour (talk/search, jump, fight, hide) |
| B | throw the magic ball |
| C | talk / act, in any behaviour |
| Option | inventory |
| Pause | game menu |
| Keypad 1-4 | Normal, Athletic, Aggressive, Discreet |
| Keypad 5-8 | weapons 1-4 |
| Keypad `*` | behaviour menu |
| Keypad `#` | recentre the camera |

The layout is provisional (`engine_c/port/jag_sys.c`).

---

## Building

The build runs on Windows. Everything that depends on your machine is
configured in one file: `local.cfg`.

### You need

- **Your own copy of Little Big Adventure.** The GOG edition works. The
  build reads the `.HQR` archives in its `Common` folder, and `TEXT.HQR` in
  `CommonClassic`, then packs them into the cartridge image on your machine.
- **Python 3** (tested with 3.14). The build uses only the standard
  library.
- **Docker**, with an image called `marsdev-32x` that holds the m68k-elf
  toolchain: gcc 15.2 plus newlib. [`docker/Dockerfile.marsdev`](docker/Dockerfile.marsdev)
  explains how to build it from [marsdev](https://github.com/andwn/marsdev).
- **[RMAC](https://rmac.is-slick.com/)** on your `PATH`, and `jaguar.inc`,
  the Jaguar developer header from Atari's SDK.
- Optional: **BigPEmu**, to run the ROM. With the scripting-enabled build,
  `tools/emu_run.ps1` can drive it.

### Steps

```bat
copy local.cfg.example local.cfg
rem edit local.cfg: LBA1_DIR, JAG_INC (and BIGPEMU_DIR)
build_v6.bat
```

The build writes two ROMs:
- `build\jaglba_v6_emu.j64` for BigPEmu;
- `build\jaglba_v6_ferro.j64` for real hardware (GameDrive and similar; *ferro* is Italian for "iron", the project's word for the real console).

They differ only in a few timing and interlace details that the emulator
does not reproduce.

**Do not distribute these ROMs.** They contain the game's data, taken from
your copy. This repository only distributes the code.

A clean clone plus `local.cfg` produces a byte-identical ROM, which was
checked when this README was written.

---

## Where things are

| Path | What |
|---|---|
| `engine_c/fork/` | the LBA1 engine (C), transplanted. [`JAGUAR_PORT.md`](engine_c/fork/JAGUAR_PORT.md) documents the design of the seam and lists **every change** made to the engine, with the reason |
| `engine_c/port/` | the platform layer under the engine: video, input, time, archives, memory |
| `engine_c/*.c` | the Jaguar renderer: background (`scene_bg.c`), bodies (`body_draw.c`), sprites and doors, front-end |
| `src/` | 68000 boot and interrupt code, and the GPU/DSP kernels (RMAC) |
| `tools/` | cartridge packer, reference renderers and parsers used as oracles, the life-script disassembler, emulator drivers |
| `docs/` | design notes and the per-session development diaries (`todo_sNN.md`, in Italian) |

---

## License

JagLBA is released under the **GNU General Public License v2.0** (see
[LICENSE](LICENSE)). It is a derivative of the LBA1 engine source, which 2.21
released under the same license.

The license covers the code only. **Little Big Adventure's data (graphics,
text, music, levels) is not part of this project and is not covered by the
GPL.** It remains the property of its rights holders. That is why the build
reads it from your copy.

## Credits

- **Adeline Software International**, for the game. **[2.21](https://github.com/2point21/lba1-classic)**,
  for releasing its source code.
- The **[lba1-classic-community](https://github.com/2point21/lba1-classic-community)**
  contributors, for the maintained engine and the C translations of its
  assembly modules. The engine was imported through its DS and PSX ports;
  see [`engine_c/fork/PROVENANCE.md`](engine_c/fork/PROVENANCE.md).
- **Zerosquare**, for the interlaced video recipe for real hardware.
- **tursilion/makefastboot** (BSD), which `tools/cart_header.py` follows
  for the cartridge boot header.
- The **RMAC** authors, **marsdev**, and **BigPEmu**, whose scripting made
  automated testing possible.
