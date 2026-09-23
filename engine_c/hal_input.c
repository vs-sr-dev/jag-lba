/* hal_input.c - lettura del controller Jaguar e mappatura sulle variabili di
 * input di LBA1.
 *
 * MATRICE DI STROBE (verificata su Jaguar PAL reale, [[reference_jaguar_joypad]])
 * ---------------------------------------------------------------------------
 * Si scrive una riga in JOYSTICK e si leggono i tasti di quella riga. Tutto
 * ATTIVO BASSO: 0 = premuto.
 *
 *   scrittura   riga   JOYSTICK bit 11,10,9,8   JOYBUTS bit 1   bit 0
 *   $F1FE        0     R    L    D    U         A               Pause
 *   $81FD        1     '1'  '4'  '7'  '*'       B               -
 *   $81FB        2     '2'  '5'  '8'  '0'       C               -
 *   $81F7        3     '3'  '6'  '9'  '#'       Option          -
 *
 * Il byte basso e' la maschera attiva-bassa della colonna ($FE = bit 0 basso).
 * Il byte ALTO e' asimmetrico e non e' un capriccio: la riga 0 vuole $F1, le
 * righe del tastierino vogliono $81. L'hardware vero accetta entrambi, BigPEmu
 * NO - con $F1xx le righe del tastierino tornano $0FFF, identico a "niente
 * premuto", e in S7 e' costato una diagnosi. Il pattern $81xx e' quello di
 * JagDoom.
 *
 * La tabella di riferimento e' la composizione a 32 bit documentata in
 * jaguar.inc ("xxApxxBx RLDU147* xxCxxxox 2580369#"), da cui questa matrice e'
 * ricavata riga per riga.
 *
 * PERCHE' IN C E NON IN RMAC
 * --------------------------
 * Leggere quattro registri non e' lavoro per-pixel, quindi non viola il
 * divieto di far lavorare il 68000 (docs/skeleton_design.md 2). Farlo qui evita
 * di aggiungere campi in HAL_VARS da tenere sincronizzati fra due sorgenti, e
 * soprattutto evita di rimettere le mani in ViHandler, che e' la parte del
 * progetto che e' costata di piu' (S23-S27).
 */
#include "hal.h"

#define JOYSTICK   (*(volatile UWORD *)0xF14000UL)
/* Lettura LONG: $F14000 e $F14002 sono adiacenti, quindi un long da' JOYSTICK
 * nella word alta e JOYBUTS nella bassa in UN SOLO accesso.
 *
 * E' il pattern verificato su Jaguar reale, `move.l JOYSTICK, d0`
 * ([[reference_jaguar_joypad]]). Due letture word separate funzionavano
 * anch'esse, ma prendono i due registri in istanti diversi rispetto allo strobe:
 * non c'e' ragione di discostarsi da una sequenza verificata sul ferro per
 * scoprire in seguito perche' era fatta cosi'. */
#define JOYPAD_L   (*(volatile ULONG *)0xF14000UL)

#define STROBE_ROW0 0xF1FE
#define STROBE_ROW1 0x81FD
#define STROBE_ROW2 0x81FB
#define STROBE_ROW3 0x81F7

WORD MyJoy;
WORD MyFire;
WORD MyKey;
WORD LastFire;

UWORD PadState;
UWORD PadEdge;
UWORD PadKeys;
UWORD PadKeysEdge;

/* Accumulatori: ogni bit mai visto premuto resta alto.
 *
 * Non sono un lusso di diagnostica, sono il modo di verificare l'input SENZA
 * coordinare la pressione con lo scatto dello screenshot. Un valore istantaneo
 * si vede solo se la cattura cade nei 33 ms in cui il tasto e' premuto; questi
 * restano, quindi si prova il controller quando si vuole e si guarda lo schermo
 * dopo. Ne servono tre perche' provano tre strati diversi: la matrice di strobe
 * (PadSeen), la mappatura su LBA (MyJoySeen/MyFireSeen) e il tastierino.
 *
 * Non c'e' modo di azzerarli: solo il reset della macchina. Una funzione di
 * azzeramento senza chiamanti sarebbe codice non esercitato, e si aggiunge il
 * giorno che serve. */
UWORD PadSeen;
UBYTE MyJoySeen;
UBYTE MyFireSeen;
UWORD PadKeysSeen;

/* Tempo di assestamento fra lo strobe e la lettura. Il pattern verificato usa
 * quattro nop; qui ce ne sono otto perche' non costano niente e il margine su
 * un bus condiviso con GPU, Blitter e OP e' gratis. `asm volatile` e' anche una
 * barriera per il compilatore, quindi la lettura non puo' risalire sopra la
 * scrittura dello strobe. */
static void settle(void)
{
    __asm__ __volatile__("nop\n\tnop\n\tnop\n\tnop\n\tnop\n\tnop\n\tnop\n\tnop"
                         ::: "memory");
}

/* Legge una riga e torna i quattro tasti in bit 3..0, ATTIVI ALTI (1 =
 * premuto), piu' i due pulsanti di JOYBUTS in bit 5 (bit 1) e bit 4 (bit 0).
 * L'inversione si fa qui una volta sola: da qui in su "1 = premuto" e non c'e'
 * piu' da ricordarsi la polarita'. */
static UWORD read_row(UWORD strobe)
{
    ULONG v;

    JOYSTICK = strobe;
    settle();
    v = JOYPAD_L;                                   /* JOYSTICK:JOYBUTS */

    return (UWORD)((((~v) >> 24) & 0x0F)            /* JOYSTICK 11..8 -> 3..0 */
                   | (((~v) & 0x02) << 4)           /* JOYBUTS bit 1  -> bit 5 */
                   | (((~v) & 0x01) << 4));         /* JOYBUTS bit 0  -> bit 4 */
}

void Hal_ReadPad(void)
{
    UWORD r0, r1, r2, r3;
    UWORD state = 0, keys = 0;
    UWORD prev = PadState;
    UWORD prevk = PadKeys;

    r0 = read_row(STROBE_ROW0);
    r1 = read_row(STROBE_ROW1);
    r2 = read_row(STROBE_ROW2);
    r3 = read_row(STROBE_ROW3);

    /* riga 0: bit 3 = R, 2 = L, 1 = D, 0 = U (l'ordine di JOYSTICK 11..8) */
    if (r0 & 0x01) state |= PAD_UP;
    if (r0 & 0x02) state |= PAD_DOWN;
    if (r0 & 0x04) state |= PAD_LEFT;
    if (r0 & 0x08) state |= PAD_RIGHT;
    if (r0 & 0x20) state |= PAD_A;
    if (r0 & 0x10) state |= PAD_PAUSE;
    if (r1 & 0x20) state |= PAD_B;
    if (r2 & 0x20) state |= PAD_C;
    if (r3 & 0x20) state |= PAD_OPTION;

    /* Tastierino. PadKeys bit 0..9 = '0'..'9', bit 10 = '*', bit 11 = '#'. */
    if (r1 & 0x08) keys |= 1u << 1;    /* '1' */
    if (r1 & 0x04) keys |= 1u << 4;    /* '4' */
    if (r1 & 0x02) keys |= 1u << 7;    /* '7' */
    if (r1 & 0x01) keys |= 1u << 10;   /* '*' */
    if (r2 & 0x08) keys |= 1u << 2;    /* '2' */
    if (r2 & 0x04) keys |= 1u << 5;    /* '5' */
    if (r2 & 0x02) keys |= 1u << 8;    /* '8' */
    if (r2 & 0x01) keys |= 1u << 0;    /* '0' */
    if (r3 & 0x08) keys |= 1u << 3;    /* '3' */
    if (r3 & 0x04) keys |= 1u << 6;    /* '6' */
    if (r3 & 0x02) keys |= 1u << 9;    /* '9' */
    if (r3 & 0x01) keys |= 1u << 11;   /* '#' */

    /* Il canale dell'emulatore (S51, HAL_FIELDS EMU_PAD): pulsanti nella word
     * bassa, tastierino nell'alta. Sommati PRIMA dei fronti, cosi' uno script
     * che preme e rilascia produce gli stessi PadEdge di un pollice. */
    {
        ULONG emu = JAG_HAL_EMU_PAD;
        state |= (UWORD)emu;
        keys  |= (UWORD)(emu >> 16);
    }

    PadState = state;
    PadEdge  = (UWORD)(state & ~prev);
    PadKeys  = keys;
    PadKeysEdge = (UWORD)(keys & ~prevk);
    PadSeen     |= state;
    PadKeysSeen |= keys;

    /* --- Mappatura sulle variabili di LBA1 --------------------------------
     * LastFire prima di riscrivere MyFire: il motore lo usa per distinguere
     * "premuto adesso" da "tenuto premuto" (upstream fa lo stesso in cima al
     * MainLoop).
     *
     * La corrispondenza pulsante -> tasto PC e' PROVVISORIA. Va confrontata
     * con quello che DoDir e i suoi chiamanti testano davvero (object.c), e
     * quello succede a M6 quando l'eroe comincia a camminare. Metterla qui
     * adesso serve a provare il percorso, non a fissare i comandi. */
    LastFire = MyFire;
    MyJoy = MyFire = MyKey = 0;

    if (state & PAD_UP)     MyJoy |= J_UP;
    if (state & PAD_DOWN)   MyJoy |= J_DOWN;
    if (state & PAD_LEFT)   MyJoy |= J_LEFT;
    if (state & PAD_RIGHT)  MyJoy |= J_RIGHT;

    if (state & PAD_A)      MyFire |= F_SPACE;    /* azione / parla    */
    if (state & PAD_B)      MyFire |= F_CTRL;     /* arma / lancio     */
    if (state & PAD_C)      MyFire |= F_SHIFT;    /* azione del comportamento */
    if (state & PAD_OPTION) MyFire |= F_RETURN;   /* inventario        */

    /* MyKey e' un solo scancodice, come sul PC dove la tastiera ne dava uno
     * per volta. I comportamenti di LBA1 stanno sui tasti 1-4, che il
     * tastierino del Jaguar produce direttamente. */
    if (keys & (1u << 1))       MyKey = SC_1;
    else if (keys & (1u << 2))  MyKey = SC_2;
    else if (keys & (1u << 3))  MyKey = SC_3;
    else if (keys & (1u << 4))  MyKey = SC_4;
    else if (state & PAD_PAUSE) MyKey = SC_ESC;

    MyJoySeen  |= (UBYTE)MyJoy;
    MyFireSeen |= (UBYTE)MyFire;
}
