/* jag_sys.c - the fork's system floor: time and input (TIMER_A.C and
 * KEYBOARD.C on DOS; sdl_sys.c on DS-LBA; psx_sys.c on PSX-LBA).
 *
 * WHY THIS RUNS IN THE VIDEO INTERRUPT
 * ------------------------------------
 * The fork busy-waits: on TimerRef in the frame regulator and while a
 * dialogue types its letters, on Key and Fire in every menu and every "press
 * a key" prompt. On DOS the timer and keyboard IRQs moved those variables
 * underneath the loop; the Jaguar has to do the same, or the first dialogue
 * hangs. So Jag_Vbl() is called by ViHandler once per video interrupt, and
 * the main thread never touches the pad hardware again - it only reads what
 * the interrupt left (engine_c/fork/JAGUAR_PORT.md, section 1).
 *
 * The pad-to-LBA mapping is hal_input.c's, verified on hardware in S42: it
 * reads the same matrix through Hal_ReadPad and produces the DOS raw state
 * here instead of MyJoy/MyFire/MyKey, which the fork derives by itself at the
 * top of MainLoop (PERSO.C:406).
 */
#include "hal.h"

/* ---- time ---------------------------------------------------------------- */

/* TimerRef itself is hal_time.c's (volatile, and the linker will place it at
 * a fixed HAL address in step 2). TimerSystem is the clock the game never
 * pauses: RestoreTimer rewinds TimerRef after a menu, not this one. */
volatile ULONG TimerSystem = 0;
UWORD CmptFrame = 0;

static ULONG vbl_acc;           /* 50 Hz from 60/50/30/25: see hal_time.c */
static UWORD fps_ticks;

/* ---- input: the DOS raw state -------------------------------------------- */

volatile UWORD Key = 0;         /* one scan code at a time, like the PC's   */
volatile UWORD Joy = 0;         /* J_UP/J_DOWN/J_LEFT/J_RIGHT               */
volatile UWORD Fire = 0;        /* F_SPACE/F_RETURN/F_CTRL/F_ALT/F_SHIFT    */
volatile UWORD FuncKey = 0;     /* F1..F12 as bits                          */
UWORD AsciiMode = 0;

volatile LONG Click = 0;
volatile LONG Mouse_X = 0;
volatile LONG Mouse_Y = 0;

static void pad_to_dos(void)
{
    UWORD s = PadState, k = PadKeys;
    UWORD joy = 0, fire = 0, key = 0;

    if (s & PAD_UP)     joy |= J_UP;
    if (s & PAD_DOWN)   joy |= J_DOWN;
    if (s & PAD_LEFT)   joy |= J_LEFT;
    if (s & PAD_RIGHT)  joy |= J_RIGHT;

    if (s & PAD_A)      fire |= F_SPACE;    /* action / talk            */
    if (s & PAD_B)      fire |= F_CTRL;     /* weapon / throw           */
    if (s & PAD_C)      fire |= F_SHIFT;    /* the behaviour's action   */
    if (s & PAD_OPTION) fire |= F_RETURN;   /* inventory                */

    if (k & (1u << 1))       key = SC_1;
    else if (k & (1u << 2))  key = SC_2;
    else if (k & (1u << 3))  key = SC_3;
    else if (k & (1u << 4))  key = SC_4;
    else if (s & PAD_PAUSE)  key = SC_ESC;

    Joy = joy;
    Fire = fire;
    Key = key;
}

/* Called from ViHandler, once per video interrupt, with interrupts masked.
 * Hal_ReadPad is safe here because nobody else strobes the pad any more once
 * this is wired; its edge fields (PadEdge, PadKeysEdge) become per-interrupt,
 * which is why the fork's own edge logic (LastFire) is the one that counts. */
void Jag_Vbl(void)
{
    UWORD hz = JAG_HAL_TICK_HZ;

    if (hz == 0)
        hz = 60;

    vbl_acc += 50;
    while (vbl_acc >= hz) {
        vbl_acc -= hz;
        TimerRef++;
        TimerSystem++;
        if (++fps_ticks >= 50) {
            fps_ticks = 0;
            NbFramePerSecond = (WORD)CmptFrame;
            CmptFrame = 0;
        }
    }

    Hal_ReadPad();
    pad_to_dos();
}

/* ---- the keyboard's ASCII ring: no keyboard ------------------------------ */

UWORD GetAscii(void)
{
    return 0;
}
