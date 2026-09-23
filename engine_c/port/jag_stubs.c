/* jag_stubs.c - the drivers the Jaguar build does not have yet, and the DOS
 * file layer it will never have.
 *
 * SILENT BY DESIGN. Everything here answers the way DOS answered on a PC with
 * no sound card, no CD-ROM and no writable disk, which the engine already
 * handles: Wave_Driver_Enable and Midi_Driver_Enable at FALSE route every
 * sound call around the driver, DriveCDR at -1 keeps MESSAGE.C away from the
 * voice files, and a NULL from OpenRead is the "file not found" path. Same
 * choices as DS-LBA's platform/sdl/stubs.c.
 *
 * What is NOT silent lives elsewhere: a seam function that must exist but is
 * not written yet calls JAG_TODO (jag_grille.c), so if it ever runs it names
 * itself on screen instead of doing nothing.
 */
#include "hal.h"
#include <stdio.h>

/* ---- sound: Wave (samples), Mixer, MIDI - M7 ------------------------------ */

LONG  Wave_Driver_Enable = 0;
WORD  Midi_Driver_Enable = 0;
char  MidiFM = 1;               /* the SB/AdLib path, when it exists (M7) */
LONG  MaxVolume = 100;

ULONG WavePlay(UWORD handle, UWORD pitch, UWORD repeat, UBYTE follow,
               UWORD volleft, UWORD volright)
{
    (void)handle; (void)pitch; (void)repeat; (void)follow;
    (void)volleft; (void)volright;
    return 0;
}
void WaveStop(void) { }
void WaveStopOne(UWORD handle) { (void)handle; }
int  WaveInList(UWORD handle) { (void)handle; return 0; }
int  WavePause(void) { return 0; }
void WaveContinue(void) { }
void WaveGiveInfo0(ULONG longhandle, ULONG *a, ULONG *b, ULONG *c)
{
    (void)longhandle;
    if (a) *a = 0;
    if (b) *b = 0;
    if (c) *c = 0;
}

void MixerChangeVolume(LONG w, LONG m, LONG cd, LONG line, LONG master)
{
    (void)w; (void)m; (void)cd; (void)line; (void)master;
}
void MixerGetVolume(LONG *w, LONG *m, LONG *cd, LONG *line, LONG *master)
{
    if (w) *w = 255;
    if (m) *m = 255;
    if (cd) *cd = 255;
    if (line) *line = 255;
    if (master) *master = 255;
}
void MixerGetInfo(LONG *w, LONG *m, LONG *cd, LONG *line, LONG *master)
{
    /* All five sliders present, as on an SB16 (sdl_sys.c does the same). */
    if (w) *w = 1;
    if (m) *m = 1;
    if (cd) *cd = 1;
    if (line) *line = 1;
    if (master) *master = 1;
}

void PlayMidi(UBYTE *ptr) { (void)ptr; }
void StopMidi(void) { }
long IsMidiPlaying(void) { return 0; }
void FadeMidiDown(WORD n) { (void)n; }
void VolumeMidi(WORD v) { (void)v; }
void DoLoopMidi(void) { }

/* ---- CD-ROM: the floppy version never had one ---------------------------- */

WORD DriveCDR = -1;
LONG FileCD_Start = 0;
LONG FileCD_Sect = 0;
LONG FileCD_Size = 0;

LONG InitCDR(char *nameid) { (void)nameid; return 0; }
void ClearCDR(void) { }
void StopCDR(void) { }
LONG PlayTrackCDR(LONG track) { (void)track; return 0; }
LONG GetLengthTrackCDR(LONG track) { (void)track; return 0; }
LONG GetFileCDR(char *name) { (void)name; return 0; }
LONG ReadLongCDR(LONG start, LONG nbsect, void *buffer)
{
    (void)start; (void)nbsect; (void)buffer;
    return 0;
}

/* ---- the Adeline system layer (ADELINE.C): config and DLL setup ---------- */

char PathConfigFile[260] = "lba.cfg";

void InitAdelineSystem(char *name, LONG inits) { (void)name; (void)inits; }
void ClearAdelineSystem(void) { }

/* DOS conventional memory. On DOS this held the voice buffer and the VESA
 * bank tables; here it is just memory. */
void *DosMalloc(LONG size, ULONG *handle)
{
    if (handle)
        *handle = 0;
    if (size < 0)
        return (void *)Jag_Size_HQM_Memory();   /* Malloc(-1) semantics: free */
    return Jag_Malloc((ULONG)size);
}

/* ---- files: there is no writable disk ------------------------------------ *
 * Saves are a later milestone (EEPROM or the GameDrive's SD); until then a
 * save fails the way a full or read-only disk failed. Every read the fork
 * makes of game data goes through jag_hqr.c instead. */

FILE *OpenRead(char *name)  { (void)name; return 0; }
FILE *OpenWrite(char *name) { (void)name; return 0; }
void  Close(FILE *handle)   { (void)handle; }
ULONG Read(FILE *h, void *buf, ULONG n)  { (void)h; (void)buf; (void)n; return 0; }
ULONG Write(FILE *h, void *buf, ULONG n) { (void)h; (void)buf; (void)n; return 0; }
LONG  Seek(FILE *h, LONG pos, LONG mode) { (void)h; (void)pos; (void)mode; return -1; }
LONG  Delete(char *name)    { (void)name; return 0; }
LONG  Exists(UBYTE *name)   { (void)name; return 0; }
ULONG FileSize(char *name)  { (void)name; return 0; }
LONG  Copy(char *src, char *dst) { (void)src; (void)dst; return 0; }
void  Touch(char *name)     { (void)name; }

/* DOS path and directory calls, from DISKFUNC.C and the save menus. */
void AddExt(char *name, char *ext)
{
    char *p = name, *dot = 0;

    for (; *p; p++) {
        if (*p == '.')
            dot = p;
        else if (*p == '\\' || *p == '/')
            dot = 0;
    }
    if (!dot)
        dot = p;
    *dot++ = '.';
    while (*ext == '.')
        ext++;
    while ((*dot++ = *ext++) != 0)
        ;
}

void _splitpath(const char *path, char *drive, char *dir, char *fname,
                char *ext)
{
    (void)path;
    if (drive) drive[0] = 0;
    if (dir)   dir[0] = 0;
    if (fname) fname[0] = 0;
    if (ext)   ext[0] = 0;
}

char *getcwd(char *buf, unsigned int size)
{
    if (buf && size > 1) {
        buf[0] = '\\';
        buf[1] = 0;
    }
    return buf;
}

int chdir(const char *path) { (void)path; return 0; }

unsigned SYS_GetDrive(void) { return 2; }                 /* C: */
void SYS_SetDrive(unsigned drive, unsigned *total)
{
    (void)drive;
    if (total)
        *total = 3;
}
unsigned long SYS_GetDiskFreeSpace(void) { return 0; }
unsigned long SYS_ComputeTime(void) { return 0; }

/* The save list scans the directory; an empty directory is an empty list. */
int  SYS_FindFirst(const char *pattern, unsigned attr, void *info)
{
    (void)pattern; (void)attr; (void)info;
    return -1;
}
int  SYS_FindNext(void *info) { (void)info; return -1; }
void SYS_FindClose(void *info) { (void)info; }

/* ---- files that are not compiled yet -------------------------------------- */

/* PLAYFLA.C: the floppy version shows GIF slides where the CD plays FLA
 * movies (FLA_GIF.HQR, floppy only). A later milestone; skipping them is
 * what the game does when the file is missing. */
void PlayAnimFla(char *name) { (void)name; }

/* FIRE.C: the fire behind the selected menu entry. The main menu is
 * front_menu.c's; this one would only be the in-game menus'. */
void DoFire(LONG y, LONG c) { (void)y; (void)c; }
