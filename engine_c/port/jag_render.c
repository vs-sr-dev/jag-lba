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

void Jag_AffScene(LONG flagflip)
{
    Jag_SceneFromFork();

    if (flagflip) {
        SaveTimer();
        Bg_Show(Scene.CamGX, Scene.CamGY, Scene.CamGZ);
    }

    Body_DrawAll();

    if (flagflip)
        RestoreTimer();

    if (FlagFade) {
        if (FlagPalettePcx)
            FadeToPal(PalettePcx);
        else
            FadeToPal(PtrPal);
        FlagFade = FALSE;
    }
}
