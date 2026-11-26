/* hal_mem.c - le due arene del motore, piu' memset/memcpy.
 *
 * LBA1 su PC chiamava DosMalloc/LoadMalloc su un heap DOS e non controllava mai
 * il valore di ritorno: se la memoria finiva, finiva il gioco. Quella
 * disinvoltura qui va benissimo, purche' l'esaurimento si VEDA - percio'
 * l'arena piena chiama TheEnd invece di tornare NULL a un chiamante che non lo
 * guarderebbe.
 *
 * DUE ARENE, DUE VITE DIVERSE
 * ---------------------------
 *   Malloc      persistente. Twinsen, le tabelle sempre caricate, lo stato di
 *               gioco. Cresce dal BASSO dell'heap e non torna piu' indietro
 *               (salvo il rilascio LIFO qui sotto).
 *   LoadMalloc  per-cubo. Tutto quello che appartiene alla stanza corrente.
 *               Cresce dall'ALTO, e HQM_Free_All la azzera in un colpo: e'
 *               esattamente cosa fa ChangeCube upstream, quindi non serve un
 *               allocatore vero per ottenere la semantica giusta.
 *
 * Le due si incontrano in mezzo, cosi' nessuna delle due ha un tetto fisso da
 * indovinare adesso: la ripartizione la decide l'uso.
 *
 * Free() non e' un allocatore generale. Rilascia solo l'ULTIMO blocco
 * persistente, il caso che upstream produce davvero (alloca, usa, rilascia
 * subito); qualunque altro Free e' un no-op dichiarato. Un allocatore con lista
 * libera qui sarebbe codice non esercitato, cioe' codice che non funziona e non
 * lo sappiamo.
 */
#include "hal.h"

/* Simboli del linker script (engine_c/jag_engine.ld, generato dal master della
 * mappa). Dichiarati come array per usarne l'indirizzo senza deferenza. */
extern unsigned char __heap_start[];
extern unsigned char __heap_end[];

static unsigned char *lo;       /* prima cella libera dell'arena persistente */
static unsigned char *hi;       /* una oltre l'ultima libera della per-cubo   */
static unsigned char *last;     /* ultimo blocco persistente, per Free()      */

static unsigned char *heap_lo;  /* base fissa, per i conti della diagnostica  */
static unsigned char *heap_hi;

void Hal_MemInit(void)
{
    heap_lo = __heap_start;
    heap_hi = __heap_end;
    lo = heap_lo;
    hi = heap_hi;
    last = 0;
}

static ULONG round4(ULONG n)
{
    return (n + 3UL) & ~3UL;
}

void *Malloc(ULONG size)
{
    unsigned char *p;

    size = round4(size);
    if ((ULONG)(hi - lo) < size)
        TheEnd(TE_OUT_OF_MEM, "Malloc");
    p = lo;
    lo += size;
    last = p;
    return p;
}

void Free(void *ptr)
{
    /* Solo LIFO sull'arena persistente: vedi la nota in testa al file. */
    if (ptr && ptr == (void *)last) {
        lo = last;
        last = 0;
    }
}

void *LoadMalloc(ULONG size)
{
    size = round4(size);
    if ((ULONG)(hi - lo) < size)
        TheEnd(TE_OUT_OF_MEM, "LoadMalloc");
    hi -= size;
    return hi;
}

/* Come LoadMalloc, ma il blocco comincia su una PHRASE (S39).
 *
 * Serve a `norm_native`: la GPU legge un record di normale con LOADP, che vuole
 * l'indirizzo allineato a 8. L'aritmetica sul puntatore sta QUI e non nel
 * chiamante apposta - `body_draw.c` si compila anche sul banco a 64 bit, dove un
 * `(ULONG)puntatore` tronca e manda a spasso l'indirizzo. Qui il bersaglio e'
 * sempre il 68000, dove ULONG e' esattamente largo come un puntatore.
 *
 * L'arena per-cubo cresce verso il BASSO, quindi si allinea dopo aver sottratto:
 * scendere non puo' invadere il blocco precedente. Gli 8 byte in piu' nel
 * controllo di capienza coprono i fino a 7 che l'allineamento consuma. */
void *LoadMallocPhrase(ULONG size)
{
    size = round4(size);
    if ((ULONG)(hi - lo) < size + 8UL)
        TheEnd(TE_OUT_OF_MEM, "LoadMallocPhrase");
    hi -= size;
    hi = (unsigned char *)((ULONG)hi & ~7UL);
    return hi;
}

void HQM_Free_All(void)
{
    hi = heap_hi;
    /* Le voci HQR decompresse vivono QUI dentro, quindi muoiono adesso. Se la
     * cache sopravvivesse all'arena tornerebbe puntatori validi su memoria
     * riassegnata a qualcos'altro. */
    HQR_CacheReset();
}

ULONG Size_HQM_Memory(void)
{
    return (ULONG)(hi - lo);
}

/* Quanto e' stata consumata ciascuna arena. Serve solo alla diagnostica, ma
 * serve: un'arena e' l'unica risorsa del progetto che si esaurisce in silenzio. */
ULONG Hal_MemUsedPersistent(void)
{
    return (ULONG)(lo - heap_lo);
}

ULONG Hal_MemUsedCube(void)
{
    return (ULONG)(heap_hi - hi);
}

/* ---------------------------------------------------------------------------
 * memset/memcpy: GCC le genera da se' anche con -ffreestanding (trasformando
 * un ciclo di riempimento in una chiamata), quindi vanno fornite comunque. E
 * al motore servono per conto proprio.
 * -------------------------------------------------------------------------*/
/* S53: A LONG, non piu' a byte. Il profiler del ferro le dava al 4,4% del
 * tempo (le chiamano AffMask, AffGraph, Box, Cls e i riempitori di poligoni
 * del fork, una volta per segmento). Sul 68000 una long va scritta a indirizzo
 * PARI, non multiplo di quattro: basta allineare a pari. Il ciclo srotolato a
 * quattro long e' quello che GCC non riscrive in una chiamata a memset grazie
 * a -fno-tree-loop-distribute-patterns (altrimenti sarebbe ricorsione). */
void *memset(void *dst, int c, unsigned long n)
{
    unsigned char *p = (unsigned char *)dst;
    unsigned char b = (unsigned char)c;

    if (n >= 16) {
        ULONG w, k;
        ULONG *q;

        if ((ULONG)p & 1) {
            *p++ = b;
            n--;
        }
        w = (ULONG)b | ((ULONG)b << 8);
        w |= w << 16;
        q = (ULONG *)p;
        for (k = n >> 4; k; k--) {
            q[0] = w; q[1] = w; q[2] = w; q[3] = w;
            q += 4;
        }
        for (k = (n >> 2) & 3; k; k--)
            *q++ = w;
        p = (unsigned char *)q;
        n &= 3;
    }
    while (n--)
        *p++ = b;
    return dst;
}

void *memcpy(void *dst, const void *src, unsigned long n)
{
    unsigned char *d = (unsigned char *)dst;
    const unsigned char *s = (const unsigned char *)src;

    /* A long solo se i due hanno la stessa parita': dopo al piu' un byte sono
     * entrambi pari. Altrimenti resta a byte (il 68000 non legge long dispari). */
    if (n >= 16 && !(((ULONG)d ^ (ULONG)s) & 1)) {
        ULONG k;
        ULONG *dq;
        const ULONG *sq;

        if ((ULONG)d & 1) {
            *d++ = *s++;
            n--;
        }
        dq = (ULONG *)d;
        sq = (const ULONG *)s;
        for (k = n >> 4; k; k--) {
            dq[0] = sq[0]; dq[1] = sq[1]; dq[2] = sq[2]; dq[3] = sq[3];
            dq += 4;
            sq += 4;
        }
        for (k = (n >> 2) & 3; k; k--)
            *dq++ = *sq++;
        d = (unsigned char *)dq;
        s = (const unsigned char *)sq;
        n &= 3;
    }
    while (n--)
        *d++ = *s++;
    return dst;
}
