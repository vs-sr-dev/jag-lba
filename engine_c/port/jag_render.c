/* jag_render.c - the fork's AffScene, on the Jaguar renderer.
 *
 * FIRST VERSION, on purpose the smallest one that shows the room and its
 * actors: the background (full after a load, scrolled after a camera step)
 * and Body_DrawAll, which already draws bodies, sprites and doors, the bricks
 * in front of them, and restores the rectangles they leave. Plus the two
 * pieces of AffScene that are not drawing but that the game depends on:
 *   - the game clock stops during a full redraw (SaveTimer/RestoreTimer,
 *     OBJECT.C:2599 and :3473), so a slow compose does not jump animations;
 *   - the fade-in after a room change (OBJECT.C:3490): InitGame and
 *     ChangeCube fade to black and leave FlagFade for AffScene to undo. Left
 *     out, the room is drawn and never seen.
 *
 * Still to come from OBJECT.C:2552, with our primitives (JAGUAR_PORT.md,
 * section 5): the depth sort between actors, shadows, extras and their
 * timers, the HUD incrusts.
 */
#include "hal.h"

void Jag_SceneFromFork(void);

/* The fork's (GLOBAL.C, AMBIANCE.C, LIB_3D/P_ANIM.C). */
extern WORD FlagFade, FlagPalettePcx;
extern UBYTE PalettePcx[];
extern UBYTE *PtrPal;
void FadeToPal(UBYTE *ptrpal);
void SaveTimer(void);
void RestoreTimer(void);
void Vsync(void);

/* A bench knob, 0 in every build: extra video interrupts to wait per scene
 * frame. Poked from BigPEmu (tools/emu_run.ps1 -Poke "Jag_FrameDelay:W=N@F")
 * it makes the emulator run the game at the real Jaguar's frame rate, which
 * is what exposed the hover platform that never arrives below ~4.5 fps
 * (S52, JAGUAR_PORT.md #13). volatile: the script writes it behind the
 * compiler's back. */
volatile WORD Jag_FrameDelay = 0;

void Jag_AffScene(LONG flagflip)
{
    WORD n;

    /* S53: le fasi del quadro in mezze righe, per la riga al secondo su COM8
     * (hal_log.c). Il tempo da qui al Log_FrameOut precedente e' la LOGICA. */
    Log_FrameIn();

    for (n = Jag_FrameDelay; n > 0; n--)
        Vsync();
    Log_Mark(LG_WAIT);

    Jag_SceneFromFork();
    Log_Mark(LG_SYNC);

    if (flagflip) {
        SaveTimer();
        Bg_Show(Scene.CamGX, Scene.CamGY, Scene.CamGZ);
    }
    Log_Mark(LG_BG);

    Body_DrawAll();

    if (flagflip)
        RestoreTimer();
    Log_Mark(LG_BODY);

    if (FlagFade) {
        if (FlagPalettePcx)
            FadeToPal(PalettePcx);
        else
            FadeToPal(PtrPal);
        FlagFade = FALSE;
    }
    Log_Mark(LG_FADE);

    Log_FrameOut();
}
