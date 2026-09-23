/* hal_hqr.c - HQR_Get / HQR_Size sul contenitore della cartuccia.
 *
 * Il contenitore "JHQR" lo scrive tools/hqr_pack.py, che ne documenta il
 * formato per esteso. Qui basta sapere tre cose:
 *
 *   1. tutti i campi sono BIG ENDIAN, cioe' nativi per il 68000: nessuna
 *      conversione, si leggono come long;
 *   2. tutti gli offset sono RELATIVI alla base del contenitore, mai
 *      assoluti - percio' il contenitore si puo' spostare senza rifarlo, che
 *      e' quel che servira' quando a M2 arrivera' il bank switching;
 *   3. i payload NON sono copiati da nessuna parte: HQR_Get torna un puntatore
 *      dentro la cartuccia. E' la decisione 3.3 di docs/skeleton_design.md, e
 *      da sola cancella il problema che aveva bloccato il PoC di S24 (i dati
 *      non entravano negli 8 KB di DRAM che restavano).
 *
 * Allineamento: l'intestazione e' 16 byte, il direttorio 12 byte per archivio e
 * le voci 8 - tutti multipli di 4 - e il packer allinea i payload a 4. Quindi
 * ogni long che si legge qui e' allineato per costruzione e non serve un
 * accessore byte per byte. Se il packer cambiasse quelle regole, l'Address
 * Error che ne seguirebbe darebbe 3 lampi, non uno schermo nero (M0).
 */
#include "hal.h"
#include "le.h"

#define HQR_MAGIC       0x4A485152UL    /* 'JHQR' */
#define HQR_VERSION     1

#define HDR_SIZE        16
#define ARCH_DIR_ENTRY  12
#define ENTRY_SIZE      8

/* Terzo campo del direttorio di archivio. Era sempre 0; da S30 porta i flag. */
#define ARCH_FLAG_LZSS  1

/* ---------------------------------------------------------------------------
 * ARCHIVI COMPRESSI (S30)
 * ---------------------------------------------------------------------------
 * Il punto 3 qui sopra - "i payload NON sono copiati da nessuna parte" - resta
 * vero per gli archivi marcati 'raw', e sono quelli che contano: BRICK e' il
 * 70% della cartuccia e resta in place.
 *
 * Ma il gioco completo non entra in 6 MB con tutto decompresso (11061 KB
 * misurati), e il gonfiore non e' distribuito: BLL gonfia 14x, RESS 3x, GRID
 * 2,8x, mentre BRICK solo 1,07x perche' e' gia' RLE. Percio' la politica e' per
 * archivio, decisa dal packer e dichiarata QUI da un bit, non indovinata.
 *
 * Per un archivio marcato LZSS il payload e' la voce HQR ORIGINALE di LBA1,
 * intestazione da 10 byte compresa e byte per byte quella che il gioco ha
 * spedito. HQR_Get la decomprime nell'arena per-cubo e tiene il puntatore in
 * cache: il chiamante non sa la differenza, e non deve saperla.
 *
 * L'intestazione e' LITTLE ENDIAN perche' viene dal PC e non e' stata toccata:
 * si legge con gli accessori di le.h, per la stessa ragione per cui li usa
 * scene_walk.c ([[project_session28_checkpoint]], sezione ordine dei byte).
 *
 *   +0  u32  dimensione decompressa
 *   +4  u32  dimensione compressa
 *   +8  u16  tipo: 0 = nessuna compressione, 1 e 2 = LZSS
 *   +10      i byte compressi
 *
 * Il tipo entra nel calcolo della lunghezza (len = nibble + tipo + 1), quindi
 * NON e' solo una bandiera: usare 1 dove serve 2 da' un'immagine quasi giusta,
 * che e' il modo peggiore in cui puo' rompersi. */
#define HQR_HDR_LEN     10

/* Voci compresse vive contemporaneamente dentro una stanza. Oggi ne servono
 * tre (SCENE, GRID, BLL); da M3 si aggiungono i body e le animazioni degli
 * attori della scena, che sono ~23. 64 e' largo, e sforare e' un TheEnd
 * parlante invece di una decompressione ripetuta che si mangia l'arena in
 * silenzio. */
/* S51: col motore trapiantato passano di qui anche le animazioni, gli sprite
 * e gli oggetti dell'inventario di tutti gli attori (engine_c/port/jag_hqr.c),
 * non solo SCENE/GRID/BLL e i corpi. 256 voci sono 2 KB di tabella. */
#define HQR_CACHE_MAX   256

typedef struct {
    UWORD       id;
    UWORD       index;
    const void *ptr;
} T_HQRCACHE;

static T_HQRCACHE cache[HQR_CACHE_MAX];
/* 1 se l'ultima HQR_Get ha appena prodotto la voce, 0 se era gia' pronta.
 * E' la semantica di HQR_Flag nel fork: dopo un caricamento vero, GAMEMENU e
 * INCRUST chiamano PatchObjet sulla voce, e rifarlo su una voce gia' patchata
 * la guasterebbe. */
static int hqr_fresh;
static int        cache_n;

static const unsigned char *const cart = (const unsigned char *)JAG_ROM_DATA;

/* L'unico punto di questo file che dipende dall'ordine dei byte della CPU: il
 * contenitore scrive i suoi campi in BIG endian, che sul 68000 e' la lettura
 * nativa. Su un banco x86 (engine_c/host_front.c) non lo e', e senza questa
 * macro il banco leggerebbe il magic al contrario e direbbe "contenitore
 * rotto" per un motivo che non c'entra col codice in prova.
 *
 * I payload NON passano di qui: sono dati di LBA1 in little endian e si
 * leggono byte per byte con le.h, che e' portabile per costruzione. */
#ifndef HQR_RD32
#define HQR_RD32(p) (*(const volatile ULONG *)(p))
#endif

static ULONG rd32(ULONG off)
{
    return HQR_RD32(cart + off);
}

/* Stato risolto una volta a HQR_Init. Tenerlo qui invece di rileggere
 * l'intestazione a ogni chiamata non e' ottimizzazione prematura: HQR_Get sta
 * per diventare una delle funzioni piu' chiamate del motore. */
static int   ready;
static UWORD n_archives;

int HQR_Init(void)
{
    ULONG magic = rd32(0);
    UWORD ver;

    ready = 0;
    n_archives = 0;

    if (magic != HQR_MAGIC)
        return 0;
    ver = (UWORD)(rd32(4) >> 16);
    if (ver != HQR_VERSION)
        return 0;

    n_archives = (UWORD)(rd32(4) & 0xFFFF);
    if (n_archives == 0 || n_archives > 64)
        return 0;

    ready = 1;
    return 1;
}

int HQR_Ready(void)
{
    return ready;
}

/* Intestazione del contenitore, esposta alla diagnostica. Non si tiene in
 * cache: la si legge dalla cartuccia ogni volta, ed e' esattamente questo che
 * la rende una prova. */
ULONG HQR_Header(int word_index)
{
    return rd32((ULONG)word_index * 4);
}

/* Offset della tabella delle voci di un archivio, 0 se l'archivio non c'e'. */
static ULONG table_of(hqr_id_t id)
{
    if (!ready || (int)id < 0 || (int)id >= (int)n_archives)
        return 0;
    return rd32(HDR_SIZE + (ULONG)id * ARCH_DIR_ENTRY);
}

int HQR_Slots(hqr_id_t id)
{
    if (!ready || (int)id < 0 || (int)id >= (int)n_archives)
        return 0;
    return (int)rd32(HDR_SIZE + (ULONG)id * ARCH_DIR_ENTRY + 4);
}

/* Risolve (archivio, indice) in (offset del payload, dimensione).
 * Torna 0 se lo slot e' assente o l'indice e' fuori. */
static int resolve(hqr_id_t id, int index, ULONG *off, ULONG *size)
{
    ULONG table = table_of(id);
    ULONG n, e;

    if (table == 0)
        return 0;
    n = rd32(HDR_SIZE + (ULONG)id * ARCH_DIR_ENTRY + 4);
    if (index < 0 || (ULONG)index >= n)
        return 0;

    e = table + (ULONG)index * ENTRY_SIZE;
    *off  = rd32(e);
    *size = rd32(e + 4);
    return (*size != 0);
}

/* I flag dell'archivio: dicono se i suoi payload vanno decompressi. */
static ULONG arch_flags(hqr_id_t id)
{
    if (!ready || (int)id < 0 || (int)id >= (int)n_archives)
        return 0;
    return rd32(HDR_SIZE + (ULONG)id * ARCH_DIR_ENTRY + 8);
}

/* ---------------------------------------------------------------------------
 * LZSS di LBA1. Trascritto da tools/hqr_decompress.py, che a sua volta e' la
 * lettura del formato: le due implementazioni si verificano a vicenda, perche'
 * il packer calcola le attese e il 68000 rilegge quel che ha prodotto.
 *
 * Il flusso e' una sequenza di gruppi: un byte di bandiere, poi otto elementi
 * governati dai suoi bit letti dal MENO significativo.
 *   bit 1  -> un byte letterale
 *   bit 0  -> due byte di riferimento all'indietro
 *
 * La copia all'indietro va fatta BYTE PER BYTE e in avanti: le corse si
 * autoreferenziano (offset 1, lunghezza 10 = "ripeti l'ultimo byte dieci
 * volte"), quindi una copia a blocchi darebbe il risultato sbagliato proprio
 * sui casi piu' comuni.
 * ------------------------------------------------------------------------- */
static void lzss_expand(const le_byte *src, ULONG comp, UBYTE *dst, ULONG orig,
                        int ctype)
{
    const le_byte *const send = src + comp;
    UBYTE *const dend = dst + orig;
    UBYTE *out = dst;

    while (out < dend && src < send) {
        int flag = *src++;
        int bit;

        for (bit = 0; bit < 8 && out < dend; bit++) {
            if (flag & (1 << bit)) {
                if (src >= send)
                    return;
                *out++ = *src++;
            } else {
                int b0, b1, len, back;
                const UBYTE *from;

                if (src + 2 > send)
                    return;
                b0 = *src++;
                b1 = *src++;
                len  = (b0 & 0x0F) + ctype + 1;
                back = ((b0 >> 4) | (b1 << 4)) + 1;
                from = out - back;
                if (from < dst)
                    return;                /* flusso rotto: meglio troncare */
                while (len-- && out < dend)
                    *out++ = *from++;
            }
        }
    }
}

/* La voce decompressa, presa dalla cache o prodotta adesso nell'arena. */
static const void *expand_entry(hqr_id_t id, int index, ULONG off)
{
    const le_byte *h = (const le_byte *)(cart + off);
    ULONG orig, comp;
    int   ctype, i;
    UBYTE *dst;

    hqr_fresh = 0;
    for (i = 0; i < cache_n; i++)
        if (cache[i].id == (UWORD)id && cache[i].index == (UWORD)index)
            return cache[i].ptr;
    hqr_fresh = 1;

    orig  = LE32u(h);
    comp  = LE32u(h + 4);
    ctype = (int)LE16u(h + 8);

    /* Base allineata alla PHRASE, non solo pari: dentro i blob JBD dei corpi
     * il packer allinea l'array delle normali a 8 CONTANDO su una base a 8
     * (body_digest.py), e la GPU lo legge con load allineate. Per gli altri
     * archivi i 7 byte al massimo di scarto non sono niente. */
    dst = (UBYTE *)LoadMallocPhrase(orig ? orig : 1);
    if (ctype == 0) {
        ULONG n = (comp < orig) ? comp : orig;
        const le_byte *s = h + HQR_HDR_LEN;
        for (i = 0; (ULONG)i < n; i++)
            dst[i] = s[i];
    } else {
        lzss_expand(h + HQR_HDR_LEN, comp, dst, orig, ctype);
    }

    if (cache_n >= HQR_CACHE_MAX)
        TheEnd(TE_HQR_CACHE, "cache HQR");
    cache[cache_n].id    = (UWORD)id;
    cache[cache_n].index = (UWORD)index;
    cache[cache_n].ptr   = dst;
    cache_n++;
    return dst;
}

/* Le voci decompresse vivono nell'arena per-cubo, quindi la cache muore quando
 * muore l'arena. Chiamata da HQM_Free_All: tenerle separate darebbe puntatori
 * validi su memoria riassegnata, che e' il guasto piu' difficile da leggere di
 * tutti. */
void HQR_CacheReset(void)
{
    cache_n = 0;
}

const void *HQR_Get(hqr_id_t id, int index)
{
    ULONG off, size;

    if (!resolve(id, index, &off, &size))
        return 0;
    if (arch_flags(id) & ARCH_FLAG_LZSS)
        return expand_entry(id, index, off);
    hqr_fresh = 0;
    return (const void *)(cart + off);
}

int HQR_WasFresh(void)
{
    return hqr_fresh;
}

/* La voce espansa a un indirizzo dato. Nessuna arena, nessuna cache: chi
 * chiama ha gia' deciso dove va, e per una schermata a pieno formato quel dove
 * e' il framebuffer. Il caso non compresso e' una copia, cosi' il chiamante non
 * deve sapere come e' depositato l'archivio. */
ULONG HQR_Expand(hqr_id_t id, int index, void *dst, ULONG cap)
{
    ULONG off, size;
    UBYTE *out = (UBYTE *)dst;

    if (!resolve(id, index, &off, &size))
        return 0;

    if (arch_flags(id) & ARCH_FLAG_LZSS) {
        const le_byte *h = (const le_byte *)(cart + off);
        ULONG orig  = LE32u(h);
        ULONG comp  = LE32u(h + 4);
        int   ctype = (int)LE16u(h + 8);
        ULONG i;

        if (orig > cap)
            return 0;
        if (ctype == 0) {
            const le_byte *s = h + HQR_HDR_LEN;
            ULONG n = (comp < orig) ? comp : orig;
            for (i = 0; i < n; i++)
                out[i] = s[i];
        } else {
            lzss_expand(h + HQR_HDR_LEN, comp, out, orig, ctype);
        }
        return orig;
    }

    if (size > cap)
        return 0;
    {
        const UBYTE *s = cart + off;
        ULONG i;
        for (i = 0; i < size; i++)
            out[i] = s[i];
    }
    return size;
}

ULONG HQR_Size(hqr_id_t id, int index)
{
    ULONG off, size;

    if (!resolve(id, index, &off, &size))
        return 0;
    /* Per un archivio compresso la dimensione utile e' quella DECOMPRESSA: e'
     * quel che il chiamante trova all'indirizzo che HQR_Get gli ha dato, ed e'
     * su quella che fa i suoi controlli di capienza. Tornare la dimensione
     * depositata sarebbe vero e inutile. */
    if (arch_flags(id) & ARCH_FLAG_LZSS)
        return LE32u((const le_byte *)(cart + off));
    return size;
}
