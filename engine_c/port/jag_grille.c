/* jag_grille.c - the fork's GRILLE.C / GRILLE_A.C API, served by scene_bg.c.
 *
 * The cube, the block library, the brick bank, the background and the bricks
 * drawn in front of an actor all belong to scene_bg.c, which has had them
 * since M2 (and whose collision, Grid_ColBrick, is WorldColBrick transcribed).
 * GRILLE.C and GRILLE_A.C are therefore not compiled, and this file maps
 * their names onto ours - JAGUAR_PORT.md, section 5.
 *
 * Two functions are transcribed here instead of mapped, line by line, because
 * they read our cube and write the fork's globals: GetShadow and
 * WorldCodeBrick. The originals also read LE data natively (WorldCodeBrick
 * takes TabBlock's offsets as a native ULONG), which a big-endian CPU gets
 * wrong; going through Grid_Cell/Grid_BlockByte fixes that for free.
 *
 * What is not written yet is LOUD: JAG_TODO puts the function's name on
 * screen through TheEnd, instead of silently doing nothing.
 */
#include "hal.h"

#define JAG_TODO(name) TheEnd(TE_JAG_TODO, name)

#define HEADER_BLOCK 3          /* GRILLE.C:16: BLL entry header, then 4 bytes a cell */

/* ---- the camera: the fork's globals, from GRILLE.C:31-40 ------------------ *
 * The cube the camera shows (StartX/Y/ZCube, in cells) and its world origin
 * (WorldX/Y/ZCube), and the screen position of the origin (XpOrgw/YpOrgw).
 * The fork's MainLoop moves them; the renderer will read them (bring-up
 * step 4). */
LONG StartXCube = 0;
LONG StartYCube = 0;
LONG StartZCube = 0;
LONG WorldXCube = 0;
LONG WorldYCube = 0;
LONG WorldZCube = 0;
WORD XpOrgw;
WORD YpOrgw;

/* The fork's, from OBJECT.C and GLOBAL.C. */
extern WORD Nxw, Nyw, Nzw;
extern WORD ShadowX, ShadowY, ShadowZ;
extern UBYTE ShadowCol;
extern WORD ClipXmin, ClipYmin, ClipXmax, ClipYmax;
void ReajustPos(UBYTE col);

/* ---- collision: scene_bg.c's transcriptions -------------------------------- */

UBYTE WorldColBrick(LONG xw, LONG yw, LONG zw)
{
    return Grid_ColBrick(xw, yw, zw);
}

UBYTE WorldColBrickFull(LONG xw, LONG yw, LONG zw, LONG ymax)
{
    return Grid_ColBrickFull(xw, yw, zw, ymax);
}

/* GRILLE_A.C:168. The code of the BLOCK under a point: the second byte of the
 * block's FIRST cell, whatever cell of the block the point is in - that is
 * what the original reads (block_adr[1] after the header, the position byte
 * is never used). 0xF0 when there is no block. */
UBYTE WorldCodeBrick(LONG xw, LONG yw, LONG zw)
{
    LONG xm, ym, zm;
    UWORD v;

    xm = (xw + DEMI_BRICK_XZ) >> 9;
    XMap = xm;
    if (yw <= -1)
        return 0xF0;
    ym = yw >> 8;
    YMap = ym;
    zm = (zw + DEMI_BRICK_XZ) >> 9;
    ZMap = zm;

    v = Grid_Cell(xm, ym, zm);
    if (v & 0xFF)
        return Grid_BlockByte(v & 0xFF, HEADER_BLOCK + 1);
    return 0xF0;
}

/* GRILLE.C:444. The first solid cell at or below the point, and the floor
 * height on top of it: that is where the shadow goes, and ReajustPos adjusts
 * it for slopes. The loop stops at y = 0 without testing it, then the cell it
 * stopped on is used either way - as in the original. Divisions, not shifts,
 * for the same reason: WORD division truncates toward zero. */
void GetShadow(WORD xw, WORD yw, WORD zw)
{
    WORD xm, ym, zm, y;
    UWORD v;

    xm = (WORD)((xw + DEMI_BRICK_XZ) / SIZE_BRICK_XZ);
    ym = (WORD)(yw / SIZE_BRICK_Y);
    zm = (WORD)((zw + DEMI_BRICK_XZ) / SIZE_BRICK_XZ);

    for (y = ym; y > 0; y--)
        if (Grid_Cell(xm, y, zm) != 0)
            break;
    v = Grid_Cell(xm, y, zm);

    XMap = xm;
    YMap = y;
    ZMap = zm;

    Nxw = xw;
    Nyw = (WORD)((y + 1) * SIZE_BRICK_Y);
    Nzw = zw;

    ShadowCol = 0;
    if (v & 0xFF) {                 /* ptc[0]: the block, base 1 */
        ShadowCol = Grid_BlockByte(v & 0xFF, HEADER_BLOCK + (ULONG)(v >> 8) * 4);
        ReajustPos(ShadowCol);
    }

    ShadowX = Nxw;
    ShadowY = Nyw;
    ShadowZ = Nzw;
}

/* ---- the bricks in front of an actor --------------------------------------- *
 * The fork calls these right after SetClip(ScreenXmin..ScreenYmax) on the
 * actor's box, so the box is the current clip. scene_bg.c takes it as
 * arguments (the column index needs it before it looks at a single cell). */
void DrawOverBrick(WORD xm, WORD ym, WORD zm)
{
    Bg_DrawOverBrick(xm, ym, zm, ClipXmin, ClipYmin, ClipXmax, ClipYmax);
}

void DrawOverBrick3(WORD xm, WORD ym, WORD zm)
{
    Bg_DrawOverBrick3(xm, ym, zm, ClipXmin, ClipYmin, ClipXmax, ClipYmax);
}

/* ---- the whole background --------------------------------------------------- */

void AffGrille(void)
{
    Bg_Redraw();
}

/* ---- loading a cube: bring-up step 3 ---------------------------------------- *
 * InitGrille is where ChangeCube hands over to scene_bg.c (Bg_Build), and
 * that hand-over needs the camera and the palette settled first. Loud until
 * it is written. */
LONG InitGrille(UWORD numcube)
{
    (void)numcube;
    JAG_TODO("InitGrille");
    return 0;
}

void FreeGrille(void)
{
    /* scene_bg.c's buffers are static; nothing to give back. */
}

void InitBufferCube(void)
{
    /* The cube lives at a fixed DRAM address (JAG_CUBE_DRAM). */
}

/* The GRM overlays: a zone that rewrites part of the cube while the hero is
 * in it (IncrustGrm), and the copy back (CopyMapToCube). */
void IncrustGrm(WORD numgrm)
{
    (void)numgrm;
    JAG_TODO("IncrustGrm");
}

void CopyMapToCube(void)
{
    JAG_TODO("CopyMapToCube");
}

/* ---- the renderer brackets (PSX seam names): nothing to bracket yet ---------- */

void PORT_ActorBegin(void)
{
}

void PORT_ActorEnd(void)
{
}
