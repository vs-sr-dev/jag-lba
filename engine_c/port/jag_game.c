/* jag_game.c - where the transplanted engine takes over.
 *
 * engine_frame calls Jag_ForkStart once the front-end is done ("New game"),
 * and it never returns: the fork's MainLoop and every modal inside it are
 * blocking loops (JAGUAR_PORT.md, section 1). From here on:
 *   - time and the pad move in the video interrupt (Jag_Vbl, via engine_vbl);
 *   - the fork's own lba_main does the allocations and loads the resources,
 *     with PORT_JAG where the Jaguar differs (PERSO.C), and then comes back
 *     here, to Jag_M5Game, instead of the main menu - the front-end already
 *     showed that one.
 *
 * Jag_M5Game is PSX-LBA's psx_m5.c:PORT_M5_Game: the new game on cube 0,
 * with no autosave (there is nowhere to save to yet).
 */
#include "hal.h"

extern void (*Jag_VblHook)(void);
void Jag_Vbl(void);
void Jag_VideoInit(void);
void lba_main(int argc, UBYTE *argv[]);

void InitGame(int argc, UBYTE *argv[]);
LONG MainLoop(void);
extern WORD NumCube, NewCube;
extern WORD DisableAutoSave;

void Jag_ForkStart(void)
{
    static UBYTE name[] = "LBA";
    static UBYTE *argv[2] = { name, 0 };

    Jag_VideoInit();
    Jag_VblHook = Jag_Vbl;
    lba_main(1, argv);
    TheEnd(TE_JAG_TODO, "lba_main returned");
}

void Jag_M5Game(void)
{
    DisableAutoSave = 1;
    InitGame(1, 0);
    NewCube = 0;
    NumCube = -1;
    MainLoop();
    TheEnd(TE_JAG_TODO, "MainLoop returned");
}
