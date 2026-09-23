/* hal_time.c - TimerRef a 50 Hz.
 *
 * LBA1 misura TUTTO su TimerRef: durata dei fotogrammi di animazione, attesa
 * dei dialoghi, timeout degli script, tempo di volo delle sfere magiche. Su PC
 * era il timer di sistema riprogrammato a ~50 Hz, e le costanti numeriche
 * sparse nel codice portato sono tarate su quella cadenza. Quindi la cadenza
 * NON si puo' cambiare: si puo' solo derivare correttamente.
 *
 * LA CADENZA DEGLI INTERRUPT QUI NON E' UNA COSTANTE
 * --------------------------------------------------
 * Il porting 32X poteva scrivere TimerRef = vblank * 5 / 6 perche' li' il
 * vblank e' 60 Hz e basta. Su Jaguar, con il 480i, gli interrupt arrivano a
 * QUATTRO cadenze diverse a seconda di due cose indipendenti:
 *
 *              NTSC   PAL
 *   Jaguar vero  60     50     (VI alternato: un interrupt per CAMPO)
 *   BigPEmu      30     25     (un interrupt per QUADRO, misurato in S27)
 *
 * NTSC/PAL si sa solo a runtime, e la riga della tabella la sceglie TARGET_HW.
 * Il lato RMAC risolve entrambe in InitVideoTiming e pubblica il risultato in
 * JAG_HAL_TICK_HZ, cosi' qui non c'e' niente da indovinare.
 *
 * PERCHE' PER DIFFERENZA E NON PER DIVISIONE
 * ------------------------------------------
 * Si potrebbe fare TimerRef = tick * 50 / hz. Due ragioni per non farlo: una
 * divisione a 32 bit sul 68000 e' una chiamata a libgcc, e la moltiplicazione
 * sfonda i 32 bit dopo un paio di settimane di accensione. Consumare invece la
 * DIFFERENZA di tick con un accumulatore non divide mai, non sfora, e da' la
 * stessa media esatta.
 *
 * Consumare la differenza (e non contare le chiamate) e' anche la ragione per
 * cui un quadro perso non fa perdere tempo di gioco: se MainLoop salta un tick
 * perche' la logica ha sforato, al giro dopo la differenza vale 2 e TimerRef
 * recupera. Il gioco rallenta a schermo ma non rallenta nel tempo.
 */
#include "hal.h"

volatile ULONG TimerRef = 0;
volatile WORD  NbFramePerSecond = 0;

static ULONG last_tick;         /* ultimo JAG_HAL_VBL_TICK consumato */
static ULONG acc;               /* resto della conversione, in unita' di hz */

void Hal_UpdateTimerRef(void)
{
    ULONG tick = JAG_HAL_VBL_TICK;
    UWORD hz   = JAG_HAL_TICK_HZ;
    ULONG delta;

    /* Se il lato RMAC non l'ha pubblicata siamo in una build incoerente:
     * meglio scorrere a 60 che dividere per zero o fermare il tempo. */
    if (hz == 0)
        hz = 60;

    delta = tick - last_tick;   /* corretto anche a cavallo del riavvolgimento */
    last_tick = tick;

    acc += delta * 50UL;
    while (acc >= hz) {
        acc -= hz;
        TimerRef++;
    }

    NbFramePerSecond = (WORD)hz;
}
