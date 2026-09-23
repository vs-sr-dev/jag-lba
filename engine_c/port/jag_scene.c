/* jag_scene.c - what the Jaguar renderer reads about the room, taken from the
 * transplanted engine's globals.
 *
 * Until S50 scene_load.c and scene_walk.c parsed SCENE.HQR themselves and
 * filled `Scene`. With the fork, DISKFUNC.C's LoadScene and OBJECT.C's
 * ChangeCube own the room: this file only keeps `Scene` - the handful of
 * fields scene_bg.c and body_draw.c read - in step with them, and loads the
 * JBD bodies the fork's SearchBody asks for.
 *
 * WIDTHS: the fork's AlphaLight/BetaLight are LONG (translate/p_trigo.c), its
 * StartX/Y/ZCube are LONG (port/jag_grille.c). Read with the wrong width on a
 * big-endian CPU they come back as their high half, i.e. zero.
 */
#include "hal.h"

T_SCENE Scene;

extern LONG AlphaLight, BetaLight;
extern LONG StartXCube, StartYCube, StartZCube;

/* ---- bodies ----------------------------------------------------------------- */

/* The BODY.HQR slot behind each PtrBody[] entry: the renderer's probes and
 * statistics name bodies by slot. */
static UWORD body_slot[MAX_BODYS];

long Jag_LoadBody(short hqrbody, long slot)
{
    const void *b;

    if (slot < 0 || slot >= MAX_BODYS)
        TheEnd(TE_OUT_OF_MEM, "PtrBody");
    /* LZSS in the cartridge, so this is always a DRAM copy in the per-room
     * arena, at a phrase boundary (hal_hqr.c): the GPU reads it with LOADP. */
    b = HQR_Get(HQR_BODY, hqrbody);
    if (!b)
        TheEnd(TE_HQR_MISSING, "BODY");
    PtrBody[slot] = (UBYTE *)b;
    body_slot[slot] = (UWORD)hqrbody;
    return 1;
}

WORD HqrBodySlotOf(WORD bodyidx)
{
    if (bodyidx < 0 || bodyidx >= NbBodys)
        return -1;
    return (WORD)body_slot[bodyidx];
}

/* ---- the room ---------------------------------------------------------------- */

/* The renderer's copy of the fork's lighting and camera. Called before every
 * draw: seven assignments, and no chance of a stale value. */
void Jag_SceneFromFork(void)
{
    Scene.valid = 1;
    Scene.AlphaLight = (WORD)AlphaLight;
    Scene.BetaLight = (WORD)BetaLight;
    Scene.CamGX = (WORD)StartXCube;
    Scene.CamGY = (WORD)StartYCube;
    Scene.CamGZ = (WORD)StartZCube;
}

/* scene_walk.c's, for Bg_Build - the S50 path, which the fork's ChangeCube
 * no longer takes (it calls Bg_LoadRoom and Bg_Show). */
void Scene_Derive(void)
{
    Jag_SceneFromFork();
}
