/* hal_log.c - il canale COM8 (S53, fase 0).
 *
 * Il Jaguar dice da solo i suoi numeri: una riga al secondo sulla porta seriale
 * della GameDrive, che il PC registra (tools/comlog.py) e riassume
 * (tools/comlog_sum.py). Prima di S53 i numeri si leggevano fotografando le
 * pagine di diagnostica, cioe' l'utente faceva da oscilloscopio.
 *
 * LE TRE REGOLE DEL CANALE (JaguarDocs metodi/gamedrive_usb.md):
 *   - una riga ferma il 68000 per 2-5 ms: si accumula in RAM e si spedisce una
 *     volta al secondo. Una ROM con il log e una senza devono dare gli stessi
 *     fps, e il costo della riga e' misurato (campo P della riga dopo);
 *   - mai GD_DebugString del BIOS: blocca senza tetto se nessuno legge COM8.
 *     Si passa da GdSendC (src/gd.s), che ha un tetto su ogni attesa e conta
 *     le righe scartate (dr=) e i byte in timeout (to=);
 *   - senza GameDrive (BigPEmu, un'altra flash) GD_SEND_FN e' 0 e non si manda
 *     niente: il resto del gioco non se ne accorge.
 *
 * IL TEMPO E' IN MEZZE RIGHE (31,778 us in NTSC, 525 per campo): lo stesso
 * orologio di JaguarMI e JagSkyroads, monotono, letto da VBL_TICK e da VC. Il
 * tick da solo (1/60 s) e' troppo grosso per misurare uno stadio da 2 ms.
 *
 * LA RIGA AL SECONDO (una lettera, poi campi chiave=esadecimale):
 *   S  f quadri disegnati, t tick video, A mezze righe della finestra,
 *      L logica (dalla fine di un AffScene all'inizio del successivo),
 *      W attesa di Jag_FrameDelay, G Jag_SceneFromFork, B Bg_Show,
 *      D Body_DrawAll, F dissolvenza, P costo della riga PRECEDENTE,
 *      X = A meno tutte le fasi: deve stare vicino a zero, e' il controllo
 *      "sommare le parti e confrontarle col totale" fatto dal programma;
 *      c scena, h GPU che non hanno alzato DONE, dr/to del canale,
 *      q/i interruzioni I2S e giri di riposo del DSP nella finestra, s fame.
 *   D  gli stadi dei corpi nella finestra (BodyStat.t_stage, ora in mezze
 *      righe): po ve sh cu so em gp, piu' ov = M3d.
 *   R  il profiler a campionamento (ViHandler): ogni PROF_SECS secondi una
 *      riga "R n= o=" e poi, una riga per quadro, i cassetti non vuoti come
 *      indice:conteggio (indice = (PC - ROM_CODE) / 64). tools/prof_sum.py li
 *      somma e li mappa su engine_blob.sym.
 *   B  al boot; E da TheEnd. EXC la manda il lato RMAC (ExcCommon).
 */
#include "hal.h"

#ifndef JAG_FORK
#define JAG_FORK 0
#endif
#if JAG_FORK
extern WORD NumCube;            /* fork/game/GLOBAL.C */
#else
static const WORD NumCube = 0;  /* il percorso S50 non ha un numero di scena qui */
#endif

#define VC_REG  (*(volatile UWORD *)0xF00006UL)

typedef void (*gd_send_fn)(const char *);

/* ---- l'orologio --------------------------------------------------------- */

ULONG Jag_Time(void)
{
    static ULONG last;
    UWORD hz = JAG_HAL_TICK_HZ;
    ULONG field = (hz == 50 || hz == 25) ? 625UL : 525UL;
    /* Sul ferro un interrupt per CAMPO; su BigPEmu uno per QUADRO (S27). */
    ULONG per_tick = (hz == 30 || hz == 25) ? field * 2 : field;
    ULONG t0, t1, t;
    LONG vc;

    do {
        t0 = JAG_HAL_VBL_TICK;
        vc = (LONG)(VC_REG & 0x7FF);
        t1 = JAG_HAL_VBL_TICK;
    } while (t0 != t1);

    /* L'interrupt scatta a VC = VI = VDE (+ la parita'): la fase si conta da li'. */
    vc -= (LONG)JAG_HAL_VDE_VAL;
    if (vc < 0)
        vc += (LONG)field;
    t = t0 * per_tick + (ULONG)vc;

    /* Fra VC che raggiunge VI e l'ISR che conta il tick la fase e' ripartita
     * ma il tick no: una lettura li' cade indietro di un campo. Un passo
     * indietro piu' corto di un tick e' quello, e si tiene l'ultimo valore. */
    if (t < last && last - t < per_tick)
        return last;
    last = t;
    return t;
}

/* ---- le righe ----------------------------------------------------------- */

static ULONG lines_sent;

static void log_line(const char *s)
{
    ULONG fn = JAG_HAL_GD_SEND_FN;

    if (fn) {
        ((gd_send_fn)fn)(s);
        lines_sent++;
    }
}

static char *put_hex(char *o, ULONG v, int digits)
{
    int i;

    for (i = digits - 1; i >= 0; i--)
        *o++ = "0123456789ABCDEF"[(v >> (4 * i)) & 15];
    return o;
}

static char *put_kv(char *o, const char *key, ULONG v, int digits)
{
    *o++ = ' ';
    while (*key)
        *o++ = *key++;
    *o++ = '=';
    return put_hex(o, v, digits);
}

/* ---- le fasi del quadro ------------------------------------------------- */

#define PROF_SECS   10          /* ogni quanti secondi si scaricano i cassetti */
#define PROF_PER_LINE 10        /* voci per riga R */

static char  lg_buf[160];
static ULONG lg_sum[LG_N];
static ULONG lg_t0, lg_at, lg_out, lg_mark;
static ULONG lg_frames, lg_hang;
static int   lg_started, lg_in_frame;
static ULONG st0[BODY_TSTAGES], ov0;
static ULONG dsp_irq0, dsp_idle0;
static int   lg_secs;
static ULONG prof_cursor;       /* != 0: scarico in corso, prossimo cassetto + 1 */

static ULONG gpu_hangs(void)
{
    return BodyStat.gpuhang + BodyStat.shadehang + BodyStat.fronthang;
}

static void stage_snapshot(void)
{
    int i;

    for (i = 0; i < BODY_TSTAGES; i++)
        st0[i] = BodyStat.t_stage[i];
    ov0 = BodyStat.t_over;
}

static void prof_clear(void)
{
    volatile UWORD *bin = (volatile UWORD *)JAG_PROF_BINS;
    ULONG i;

    volatile UWORD *ram = (volatile UWORD *)JAG_PROF_BINS_RAM;

    JAG_HAL_PROF_ON = 0;
    for (i = 0; i < JAG_PROF_BINS_SIZE / 2; i++)
        bin[i] = 0;
    for (i = 0; i < JAG_PROF_BINS_RAM_SIZE / 2; i++)
        ram[i] = 0;
    JAG_HAL_PROF_N = 0;
    JAG_HAL_PROF_OUT = 0;
}

/* Una riga R di cassetti non vuoti, a partire dal cursore; ciascuno si azzera
 * appena letto, quindi quel che l'ISR conta nel frattempo va alla finestra
 * dopo (al piu' si perde il campione che cade fra la lettura e l'azzeramento). */
/* Indici: 0..$FFF i cassetti della cartuccia (PC = ROM_CODE + i*64), da $1000
 * quelli della .fast in DRAM (PC = C_ARENA + (i-$1000)*64). */
#define PROF_ROM_N  (JAG_PROF_BINS_SIZE / 2)
#define PROF_RAM_N  (JAG_PROF_BINS_RAM_SIZE / 2)

static volatile UWORD *prof_bin_at(ULONG i)
{
    if (i < PROF_ROM_N)
        return (volatile UWORD *)JAG_PROF_BINS + i;
    return (volatile UWORD *)JAG_PROF_BINS_RAM + (i - PROF_ROM_N);
}

static void prof_dump_line(void)
{
    ULONG nbins = PROF_ROM_N + PROF_RAM_N;
    ULONG i = prof_cursor - 1;
    char *o = lg_buf;
    int k = 0;

    *o++ = 'R';
    while (i < nbins && k < PROF_PER_LINE) {
        volatile UWORD *bin = prof_bin_at(i);
        UWORD n = *bin;
        if (n) {
            *bin = 0;
            *o++ = ' ';
            o = put_hex(o, i, i < 0x1000 ? 3 : 4);
            *o++ = ':';
            o = put_hex(o, n, n > 0xFF ? 4 : 2);
            k++;
        }
        i++;
    }
    *o = 0;
    prof_cursor = (i < nbins) ? i + 1 : 0;
    if (k)
        log_line(lg_buf);
}

static void line_s(ULONG now)
{
    ULONG a = now - lg_t0, x = a;
    ULONG tick = JAG_HAL_VBL_TICK;
    ULONG irq = JAG_HAL_DSP_IRQ_CNT, idle = JAG_HAL_DSP_IDLE_CNT;
    char *o = lg_buf;
    static const char keys[LG_N] = { 'L', 'W', 'G', 'B', 'D', 'F', 'P' };
    int i;

    *o++ = 'S';
    o = put_kv(o, "f", lg_frames, 2);
    o = put_kv(o, "t", tick - lg_mark, 2);
    o = put_kv(o, "A", a, 5);
    for (i = 0; i < LG_N; i++) {
        char key[2] = { keys[i], 0 };
        o = put_kv(o, key, lg_sum[i], 5);
        x -= lg_sum[i];
    }
    o = put_kv(o, "X", x, 4);
    o = put_kv(o, "c", (ULONG)(UWORD)NumCube, 2);
    o = put_kv(o, "h", lg_hang, 2);
    o = put_kv(o, "dr", JAG_HAL_GD_DROP, 2);
    o = put_kv(o, "to", JAG_HAL_GD_TIMEOUT, 2);
    o = put_kv(o, "q", irq - dsp_irq0, 5);
    o = put_kv(o, "i", idle - dsp_idle0, 5);
    o = put_kv(o, "s", JAG_HAL_DSP_STARVE, 4);
    *o = 0;
    log_line(lg_buf);
    dsp_irq0 = irq;
    dsp_idle0 = idle;
    lg_mark = tick;

    /* Gli stadi dei corpi, solo se ci sono stati corpi. */
    if (lg_frames) {
        static const char *sk[BODY_TSTAGES] = { "po", "ve", "sh", "cu", "so", "em", "gp" };
        o = lg_buf;
        *o++ = 'D';
        for (i = 0; i < BODY_TSTAGES; i++)
            o = put_kv(o, sk[i], BodyStat.t_stage[i] - st0[i], 5);
        o = put_kv(o, "ov", BodyStat.t_over - ov0, 5);
        *o = 0;
        log_line(lg_buf);
    }
    stage_snapshot();

    if (++lg_secs >= PROF_SECS && !prof_cursor) {
        lg_secs = 0;
        o = lg_buf;
        *o++ = 'R';
        o = put_kv(o, "n", JAG_HAL_PROF_N, 4);
        o = put_kv(o, "o", JAG_HAL_PROF_OUT, 4);
        *o = 0;
        log_line(lg_buf);
        JAG_HAL_PROF_N = 0;
        JAG_HAL_PROF_OUT = 0;
        prof_cursor = 1;
    }
}

/* Chiude la finestra se e' passato un secondo. Il costo della riga va nella
 * fase P della finestra DOPO, cosi' A torna. */
static void maybe_emit(void)
{
    ULONG now = Jag_Time(), t1;
    UWORD hz = JAG_HAL_TICK_HZ;
    int i;

    if (prof_cursor) {
        prof_dump_line();
        t1 = Jag_Time();
        lg_sum[LG_SEND] += t1 - now;
        lg_out = t1;
        return;
    }
    if (JAG_HAL_VBL_TICK - lg_mark < (ULONG)(hz ? hz : 60))
        return;
    line_s(now);
    for (i = 0; i < LG_N; i++)
        lg_sum[i] = 0;
    lg_frames = 0;
    lg_hang = 0;
    lg_t0 = now;
    t1 = Jag_Time();
    lg_sum[LG_SEND] = t1 - now;
    lg_out = t1;
}

void Log_FrameIn(void)
{
    ULONG now = Jag_Time();

    if (!lg_started) {
        lg_started = 1;
        lg_t0 = now;
        lg_mark = JAG_HAL_VBL_TICK;
        dsp_irq0 = JAG_HAL_DSP_IRQ_CNT;
        dsp_idle0 = JAG_HAL_DSP_IDLE_CNT;
        stage_snapshot();
        prof_clear();
        JAG_HAL_PROF_ON = 1;
    } else {
        lg_sum[LG_LOGIC] += now - lg_out;
    }
    lg_at = now;
    lg_in_frame = 1;
}

void Log_Mark(int phase)
{
    ULONG now = Jag_Time();

    lg_sum[phase] += now - lg_at;
    lg_at = now;
}

void Log_FrameOut(void)
{
    lg_frames++;
    lg_hang += gpu_hangs();
    lg_out = Jag_Time();
    lg_in_frame = 0;
    maybe_emit();
}

/* Dai cicli modali del fork (dialoghi, menu: chiamano Vsync e non AffScene):
 * la riga continua ad uscire, con f=0 e tutto il tempo in L. */
void Log_Idle(void)
{
    if (!lg_started || lg_in_frame)
        return;
    maybe_emit();
}

void Log_Boot(void)
{
    char *o = lg_buf;

    *o++ = 'B';
    *o++ = ' ';
    {
        const char *s = "jaglba";
        while (*s)
            *o++ = *s++;
    }
    o = put_kv(o, "gs", JAG_HAL_GD_STATE, 1);
    o = put_kv(o, "gv", JAG_HAL_GD_VER, 8);
    o = put_kv(o, "hz", JAG_HAL_TICK_HZ, 2);
    o = put_kv(o, "vde", JAG_HAL_VDE_VAL, 3);
    o = put_kv(o, "cal", 0x0F0F5A5AUL, 8);
    *o = 0;
    log_line(lg_buf);
}

/* Una riga "I tag v0 v1 ...": il contesto di un guasto, mandato SUBITO prima
 * di TheEnd, che da solo dice cosa ma non chi. */
void Log_Vals(const char *tag, const ULONG *v, int n)
{
    char *o = lg_buf;
    int i;

    *o++ = 'I';
    *o++ = ' ';
    while (*tag && o < lg_buf + 40)
        *o++ = *tag++;
    for (i = 0; i < n && i < 12; i++) {
        *o++ = ' ';
        o = put_hex(o, v[i], 8);
    }
    *o = 0;
    log_line(lg_buf);
}

void Log_End(WORD code, const char *msg)
{
    char *o = lg_buf;
    volatile ULONG d;
    int n = 0;

    /* La risposta della riga precedente, se c'era, ha tempo di arrivare: GdSendC
     * la aspetta solo 100 giri prima di scartare la riga nuova. */
    for (d = 0; d < 20000UL; d++)
        ;
    *o++ = 'E';
    o = put_kv(o, "code", (ULONG)(UWORD)code, 4);
    o = put_kv(o, "at", (ULONG)msg, 8);
    *o++ = ' ';
    while (msg && *msg && n++ < 100)
        *o++ = *msg++;
    *o = 0;
    log_line(lg_buf);
}
