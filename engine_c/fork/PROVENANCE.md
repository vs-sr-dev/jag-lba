# engine_c/fork — where this code comes from

The LBA1 game engine, as C: the community fork `lba1-classic-community`
(GPL-2.0), with its 16 x86 ASM modules translated to C (`translate/`), as
carried by the sibling ports.

Imported verbatim from **PSX-LBA @ df25964** (2026-09-08), `engine/` and
`translate/`. PSX-LBA descends from DS-LBA @ 0225c44 and already contains
every DS engine fix plus the 2 MB memory diet; it also adds the renderer seams
(`PORT_ActorBegin/End`, `PORT_Depth*`) this port hooks into.

The import commit touches nothing. Every Jaguar change after it is a separate
commit, so `git log -- engine_c/fork` is the full list of what differs from
the PSX tree. Fixes carried over from HS-LBA @ 913e41b get their own commits
too, each naming the HS file and line.

The three ports named above are public:
[PSX-LBA](https://github.com/vs-sr-dev/psx-lba),
[DS-LBA](https://github.com/vs-sr-dev/ds-lba),
[HS-LBA](https://github.com/vs-sr-dev/hs-lba). The engine itself is
[lba1-classic-community](https://github.com/2point21/lba1-classic-community)
(GPL-2.0), from 2.21's
[source release](https://github.com/2point21/lba1-classic).
