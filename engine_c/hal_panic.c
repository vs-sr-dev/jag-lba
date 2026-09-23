/* hal_panic.c - TheEnd.
 *
 * Upstream stampava un messaggio a schermo e tornava al DOS. Qui non c'e' un
 * DOS a cui tornare, e la lezione strutturale della V4 e' che un guasto muto
 * costa mesi (docs/skeleton_design.md 3.6). Percio' TheEnd fa tre cose e non
 * torna:
 *
 *   1. scrive un motivo riconoscibile in cima allo schermo, cosi' si vede che
 *      e' TheEnd e non un blocco qualsiasi;
 *   2. scrive il codice e il puntatore al messaggio nelle righe di
 *      diagnostica, dove sono leggibili con calma da uno screenshot;
 *   3. si ferma.
 *
 * Il marcatore di vita si ferma insieme, perche' MainLoop non gira piu': e'
 * quello il segnale che si nota anche senza saper leggere il codice. Non c'e'
 * nessuna forma di "prosegui comunque": un TheEnd raggiunto vuol dire che uno
 * stato che il motore da' per certo non c'e'.
 *
 * Non si usa il percorso a lampi del lato RMAC (ExcCommon) perche' quello serve
 * alle eccezioni della CPU, dove lo stack puo' essere distrutto e il
 * framebuffer non e' garantito. Qui invece siamo in C, con tutto in piedi:
 * spendere lo schermo e' meglio che contare lampi.
 */
#include "hal.h"

/* Banda a scacchi in cima al fondo: una forma, non una tinta (la lezione CRY16
 * di S24 vale anche qui). Larga tutto lo schermo, quindi non la si puo'
 * scambiare per il marcatore o per una riga di diagnostica. */
static void panic_band(void)
{
    int x;

    Dbg_Fill(0, 0, JAG_BG_W, 6, 0);
    for (x = 0; x < JAG_BG_W; x += 32)
        Dbg_Fill(x, 0, 16, 6, 255);
}

void TheEnd(WORD code, const char *msg)
{
    panic_band();
    Dbg_Value(0, 0xDEADDEADUL);              /* "questa e' TheEnd"          */
    Dbg_Value(1, (ULONG)(UWORD)code);        /* il codice                   */
    Dbg_Value(2, (ULONG)msg);                /* dove sta la stringa in ROM: */
                                             /* identifica il call site     */
    for (;;)
        ;
}
