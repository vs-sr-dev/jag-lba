/* jag_video.c - the fork's video floor (LIB_SVGA's INITSVGA/S_PAL/S_PHYS),
 * on the Jaguar.
 *
 * The same set of symbols DS-LBA's sdl_video.c and PSX-LBA's psx_video.c
 * provide, with a much shorter body, because the Jaguar needs no present
 * step: `Log` IS the background framebuffer the Object Processor shows, 8 bpp
 * with LBA1's palette, visible rows 0..479 at a 640 pitch (the 112 rows below
 * exist only so a brick anchored at 479 is written whole). Whatever the fork
 * draws into Log is on screen at the next field. Flip and CopyBlockPhys
 * therefore have nothing to copy.
 *
 * engine_c/fork/JAGUAR_PORT.md, section 1.
 */
#include "hal.h"
#include "jag_hw.h"

#define SCR_W 640
#define SCR_H 480

UBYTE *Log  = (UBYTE *)JAG_BG_FB;
UBYTE *Phys = 0;                /* the 320x200 MCGA page: FLA only, later */

WORD Screen_X = SCR_W;
WORD Screen_Y = SCR_H;

UBYTE Text_Ink   = 15;
UBYTE Text_Paper = 0;

/* The fork declares this as a scalar and indexes it like the ASM did; the
 * symbol's address is what matters, and 481 is what INITSVGA.ASM reserved. */
ULONG TabOffLine[481];

WORD ClipXmin = 0, ClipYmin = 0;
WORD ClipXmax = SCR_W - 1, ClipYmax = SCR_H - 1;

static WORD MemoClipXmin, MemoClipYmin, MemoClipXmax, MemoClipYmax;

/* The palette as the game last set it, 8 bits per component. The fork's own
 * fades read PtrPal and write through PalMulti; this copy is for whoever
 * needs the current state back (nobody yet, but the PSX port found one). */
UBYTE PORT_PalRGB[768];

/* S52: copies to or from `Screen` the fork asked for and did not get
 * (JAGUAR_PORT.md #11). Only a probe: read it from BigPEmu. */
long Jag_ScreenSkips = 0;

void Jag_VideoInit(void)
{
    int y;

    for (y = 0; y <= SCR_H; y++)
        TabOffLine[y] = (ULONG)y * JAG_BG_W;
    Log = (UBYTE *)JAG_BG_FB;
    Screen_X = SCR_W;
    Screen_Y = SCR_H;
}

/* ---- modes: there is one ------------------------------------------------ */

void SimpleInitSvga(void)
{
    Screen_X = SCR_W;
    Screen_Y = SCR_H;
}

void InitMcgaMode(void)
{
    /* The 320x200 zoom (cube 59) and the FLA player. Neither is wired yet;
     * the surface stays 640x480, which is what the PSX port found the engine
     * actually needs (its M7 notes). */
}

/* ---- clip (INITSVGA.ASM), semantics from psx_video.c --------------------- */

void SetClip(LONG x0, LONG y0, LONG x1, LONG y1)
{
    ClipXmin = (WORD)((x0 < 0) ? 0 : x0);
    ClipYmin = (WORD)((y0 < 0) ? 0 : y0);
    ClipXmax = (WORD)((x1 >= Screen_X) ? Screen_X - 1 : x1);
    ClipYmax = (WORD)((y1 >= Screen_Y) ? Screen_Y - 1 : y1);
}

void UnSetClip(void)
{
    ClipXmin = 0;
    ClipYmin = 0;
    ClipXmax = Screen_X - 1;
    ClipYmax = Screen_Y - 1;
}

void MemoClip(void)
{
    MemoClipXmin = ClipXmin;
    MemoClipYmin = ClipYmin;
    MemoClipXmax = ClipXmax;
    MemoClipYmax = ClipYmax;
}

void RestoreClip(void)
{
    ClipXmin = MemoClipXmin;
    ClipYmin = MemoClipYmin;
    ClipXmax = MemoClipXmax;
    ClipYmax = MemoClipYmax;
}

/* ---- palette (S_PAL.ASM) ------------------------------------------------- *
 * The DOS code wrote 8-bit components to a 6-bit VGA DAC (>>2). The Jaguar
 * CLUT is R5 B5 G6 - green in the LOW bits, not RGB565 (jag_hw.h). Going
 * 8 -> 6 -> 5/6 is the same as 8 -> 5/6, so the colours are the DAC's.
 * front_menu.c:pal_apply writes the same format. */
static void clut_range(int first, int n)
{
    int i;

    for (i = first; i < first + n && i < 256; i++) {
        int r = PORT_PalRGB[i * 3];
        int g = PORT_PalRGB[i * 3 + 1];
        int b = PORT_PalRGB[i * 3 + 2];

        HW_W(JAG_CLUT + i * 2) = (UWORD)(((r >> 3) << 11) | ((b >> 3) << 6)
                                         | (g >> 2));
    }
}

void Palette(UBYTE *pal)
{
    int i;

    for (i = 0; i < 768; i++)
        PORT_PalRGB[i] = pal[i];
    clut_range(0, 256);
}

void PalMulti(WORD startcoul, WORD nbcoul, UBYTE *pal)
{
    int i;

    if (startcoul < 0 || nbcoul <= 0 || startcoul + nbcoul > 256)
        return;
    for (i = 0; i < nbcoul * 3; i++)
        PORT_PalRGB[startcoul * 3 + i] = pal[i];
    clut_range(startcoul, nbcoul);
}

void PalOne(UBYTE coul, UBYTE r, UBYTE v, UBYTE b)
{
    PORT_PalRGB[coul * 3]     = r;
    PORT_PalRGB[coul * 3 + 1] = v;
    PORT_PalRGB[coul * 3 + 2] = b;
    clut_range(coul, 1);
}

/* ---- present: nothing to copy -------------------------------------------- */

/* One video interrupt. Bounded, because a wait with no exit turns every
 * failure into the same frozen screen ([[feedback_hang_diventa_dato]]): if the
 * counter has not moved after far longer than a field, the interrupt is dead,
 * and that is worth a code on screen. */
void Vsync(void)
{
    ULONG t0 = JAG_HAL_VBL_TICK;
    ULONG spin = 0;

    while (JAG_HAL_VBL_TICK == t0) {
        if (++spin > 4000000UL)
            TheEnd(TE_VBL_DEAD, "Vsync");
    }

    /* S53: dialoghi e menu non passano da AffScene; la riga al secondo esce
     * lo stesso, con f=0 (hal_log.c). Dentro AffScene non fa niente. */
    Log_Idle();
}

void Flip(void)
{
}

void CopyBlockPhys(LONG x0, LONG y0, LONG x1, LONG y1)
{
    (void)x0; (void)y0; (void)x1; (void)y1;
}

void CopyBlockPhysClip(LONG x0, LONG y0, LONG x1, LONG y1)
{
    (void)x0; (void)y0; (void)x1; (void)y1;
}
