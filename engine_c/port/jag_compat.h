/*
 * jag_compat.h - force-included (gcc -include) in front of every FORK
 * translation unit, never in front of ours.
 *
 * Same job as watcom_compat.h in the DS and PSX ports: kill the Watcom
 * keywords, give back min/max, fill the gaps between the 1994 DOS libc and
 * newlib (which the toolchain has, built for m68000).
 *
 * The 68000 is the first big-endian CPU this engine runs on. The data stays
 * little-endian in ROM, as LBA1 shipped it; every read of it is explicit.
 * See engine_c/fork/JAGUAR_PORT.md, section 3.
 */
#ifndef JAG_COMPAT_H
#define JAG_COMPAT_H

#define PORT_JAG 1

/* Watcom calling-convention / memory-model keywords -> nothing */
#define cdecl
#define __far
#define __near
#define __loadds

#ifndef min
#define min(a, b) (((a) < (b)) ? (a) : (b))
#endif
#ifndef max
#define max(a, b) (((a) > (b)) ? (a) : (b))
#endif

#include <stddef.h>
#include <string.h>
#include <stdlib.h>

/* GIF.C and PCX.C use `index` as a variable name; newlib's strings.h declares
 * index(). Rename after the headers, as the DS and PSX ports do. */
#define index lba1_index

/* DOS path limits (direct.h / stdlib.h under Watcom). */
#ifndef _MAX_PATH
#define _MAX_PATH  260
#define _MAX_DRIVE 3
#define _MAX_DIR   256
#define _MAX_FNAME 256
#define _MAX_EXT   256
#endif

/* Watcom's case-insensitive string calls; newlib has the POSIX names. */
int strcasecmp(const char *, const char *);
int strncasecmp(const char *, const char *, size_t);
#define stricmp  strcasecmp
#define strcmpi  strcasecmp
#define strnicmp strncasecmp

/* Diagnostics from engine TUs. gnu89 would declare these implicitly as
 * int f(), and passing varargs through that is undefined: one prototype here
 * covers every engine TU. */
void PORT_Diag(const char *fmt, ...);

/* The renderer brackets, same names as the PSX seam (engine_c/port). */
void PORT_ActorBegin(void);
void PORT_ActorEnd(void);

/* The animation calls with the state in the actor (port/jag_anim.c). The
 * fork's T_OBJET is not declared yet at this point, hence void*: same ABI. */
long Jag_SetInterDepObjet(void *obj);
void Jag_AnimReset(void *obj);
void Jag_AnimSnap(short numobj);

/* A body from the cartridge, JBD, into PtrBody[] (port/jag_scene.c). */
long Jag_LoadBody(short hqrbody, long slot);

/* The renderer and the game start (port/jag_render.c, port/jag_game.c). */
void Jag_AffScene(long flagflip);
void Jag_M5Game(void);

/* The Jaguar's own front-end (front_menu.c) shows the Adeline logo and the
 * main menu; lba_main's copies of them are skipped. */
#define SKIP_INTRO 1

#endif /* JAG_COMPAT_H */
