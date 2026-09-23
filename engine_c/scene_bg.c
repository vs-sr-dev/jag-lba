/* scene_bg.c - il FONDO: griglia, brick, composizione. Seconda meta' di M2.
 *
 * COS'E' UN FONDO IN LBA1
 * -----------------------
 * Non e' un'immagine. E' un cubo di 64x25x64 celle; ogni cella non vuota nomina
 * un blocco della libreria della scena (LBA_BLL) e una posizione dentro quel
 * blocco; quella coppia nomina un BRICK (LBA_BRK), che e' una tessera
 * isometrica gia' disegnata e compressa RLE. Comporre il fondo vuol dire
 * percorrere il cubo nell'ordine del pittore e appoggiare 756 tessere una
 * sull'altra.
 *
 * Quindi tre decodifiche, tutte trascritte dal codice che quei byte li ha
 * SCRITTI, non dedotte guardandoli:
 *   colonne del cubo -> DecompColonne  (GRILLE_A.ASM)
 *   blocchi          -> il consumatore in GRILLE.C
 *   brick            -> GRAPHMSK.ASM
 * Le stesse tre stanno in tools/bg_ref.py, che calcola le attese.
 *
 * CHI FA COSA, E PERCHE'
 * ----------------------
 * La regola "nessun ciclo su pixel sul 68000, mai" vale a REGIME. Qui siamo al
 * caricamento di una stanza, che avviene una volta, e la divisione e' netta:
 *
 *   il 68000 decodifica  (~460 KB di RLE espansa, decine di millisecondi)
 *   il Blitter riempie   (756 blit, ~236.000 pixel)
 *
 * Il 68000 non tocca un solo pixel del framebuffer: li scrive tutti il Blitter.
 * L'RLE espansa NON e' lavoro per-pixel di schermo, e' preparazione di sorgenti
 * che il Blitter poi usa 756 volte.
 *
 * IL DISEGNO CONCORRENTE, ATTESO E NON UN GUASTO
 * ----------------------------------------------
 * Durante questa costruzione il Blitter e l'Object Processor si contendono il
 * bus, quindi il display mostra spazzatura per il tempo del caricamento
 * ([[feedback_blitter_bus_contention]]). E' il display a soffrire, non il
 * framebuffer: l'OP perde le sue letture, i byte scritti restano quelli
 * giusti. Infatti la somma di controllo si calcola dopo, sul framebuffer, e
 * torna. A regime il Blitter non tocchera' piu' il fondo (decisione 3.4), e li'
 * la contesa non esiste proprio.
 */
#include "hal.h"
#include "le.h"
#include "jag_hw.h"
#include "bg_expect.h"        /* i due indici CLUT della diagnostica */

#define CUBE_X 64
#define CUBE_Y 25
#define CUBE_Z 64

/* Geometria isometrica. NON e' una taratura: e' Map2Screen (GRILLE_A.ASM:446),
 * cioe' la routine con cui il gioco decide dove finisce ogni tessera.
 *
 *   XScreen = (xm - zm)*24         + 320-8-23-1
 *   YScreen = (xm + zm)*12 - ym*15 + 240-25
 *
 * Il rapporto 2:1 fra ISO_X e ISO_Y e' l'invariante dell'isometrica di LBA1
 * ([[feedback_lba1_iso_invariant]]) e qui si vede da dove viene.
 *
 * S30 - le due costanti di centratura erano 323 e 191, tarate a occhio in S20
 * contro una schermata del PC DOS. La Y si spiega per intero: 191 era giusta
 * per la cella TRONCATA (52,53) e, con la cella che da' la regola del gioco
 * (53,54), diventa 191+24 = 215 = il 240-25 di Map2Screen. I due errori si
 * annullavano, ed e' il motivo per cui la taratura sembrava perfetta. La X
 * invece era sbagliata di 35 px senza niente che la compensasse, perche'
 * partiva da "320 = mezzo schermo": un'assunzione, non una misura.
 *
 * Gli stessi valori stanno in tools/bg_ref.py e le attese ne dipendono, quindi
 * cambiarne uno da una parte sola fa cadere la sonda VXY - che e' il punto.
 *
 * S32: le cinque costanti sono passate in hal.h, perche' da M3c le usa anche
 * body_draw.c. Due copie in due file sarebbero due cose che possono divergere,
 * ed e' il difetto che qui si era appena finito di chiudere. */

/* Finestra di rifiuto per tessera, da AffBrickBlock (GRILLE.C:631):
 *
 *   if (XScreen >= -24 AND XScreen < 640 AND YScreen >= -38 AND YScreen < 480)
 *
 * Il confronto e' sull'ancoraggio PRIMA del hot spot, che AffGraph aggiunge per
 * conto suo. Sostituisce il CELL_MARGIN = 128 di S29, che era un margine di
 * sicurezza inventato: questo e' il taglio che il gioco fa davvero.
 *
 * MISURATO sulla scena 0: rifiuta 4 celle su 799 e cambia ZERO pixel dentro le
 * 480 righe visibili - i 1409 pixel di differenza stanno tutti sotto il bordo,
 * dove non li vede nessuno. Quindi non e' un cambiamento d'immagine, e' 4 blit
 * risparmiati e una regola al posto di un numero.
 *
 * La soglia Y usa 480, l'altezza dello schermo DOS, non le JAG_BG_H righe del
 * buffer: quelle in piu' esistono solo perche' una tessera ancorata a 479 va
 * scritta intera invece che tagliata a meta'. */
#define DOS_CLIP_X0  (-24)
#define DOS_CLIP_X1   640
#define DOS_CLIP_Y0  (-38)
#define DOS_CLIP_Y1   480

T_BGSTAT BgStat;

static UWORD *const cube    = (UWORD *)JAG_CUBE_DRAM;
static ULONG *const handles = (ULONG *)JAG_BRICK_HANDLES;
static UBYTE *const usedbmp = (UBYTE *)JAG_BRICK_USED_BMP;

/* Record di un brick decodificato nel buffer di lavoro. Ricalca quello di V4
 * perche' e' la forma che il Blitter vuole: i flag di A2 gia' composti,
 * cosi' il ciclo di composizione non ricalcola niente per cella. */
#define BH_FLAGS   0        /* long: A2_FLAGS (WID della riga allargata)   */
#define BH_DX      4
#define BH_DY      5
#define BH_HOTX    6
#define BH_HOTY    7
#define BH_PIXELS  8

/* La libreria di blocchi della scena, puntata dentro la cartuccia.
 *
 * I blocchi sono al massimo 255 perche' la cella del cubo nomina il blocco con
 * UN BYTE: quelli oltre non sarebbero raggiungibili comunque, e troncare qui
 * rende la cosa esplicita invece che implicita.
 *
 * La tabella si precalcola una volta per scena (S30). Prima ogni cella pagava
 * una LE32u dentro la cartuccia piu' due moltiplicazioni a 32 bit per il
 * conteggio delle celle del blocco - e sul 68000 una moltiplicazione a 32 bit
 * e' una chiamata a __mulsi3, non un'istruzione. Erano 11137 volte per stanza
 * per un valore che dipende solo dal blocco. */
#define BLL_MAX 255

static const le_byte *bll_base;
static int            bll_count;
static const le_byte *bll_ptr[BLL_MAX];
static ULONG          bll_cells[BLL_MAX];

/* Una cella del cubo che nomina un brick, gia' risolta.
 *
 * px e py sono lo scostamento di schermo SENZA la camera: (gx-gz)*ISO_X e
 * (gx+gz)*ISO_Y - gy*ISO_GY. Sono quindi invarianti per la stanza, e ogni
 * ridisegno costa due somme per cella invece di due moltiplicazioni piu' la
 * risoluzione della BLL. E' quel che rendera' economico lo scatto di camera di
 * M6, dove il fondo si ridisegna ma la griglia non cambia.
 *
 * gy e gxz sono le DUE grandezze che DrawOverBrick confronta (GRILLE.C:518,
 * Ym >= ym e (Xm+Zm) > (xm+zm)), e stanno qui perche' da px e py non si
 * ricavano: due equazioni, tre incognite. Costano due byte per cella e portano
 * la voce a otto esatti, senza riempimento. La terza coordinata che serve alle
 * PORTE (DrawOverBrick3, che vuole gx e gz separati) non si stocca: gx-gz e'
 * px / ISO_X, e con un divisore costante il compilatore non chiama libgcc. */
typedef struct {
    WORD  px;
    WORD  py;
    UWORD ref;                  /* indice LBA_BRK, gia' 0-based */
    UBYTE gy;                   /* livello nel cubo                          */
    UBYTE gxz;                  /* gx+gz: la profondita' in celle            */
} T_CELL;

/* La base si lascia sostituire, come body_draw.c fa con BODY_FB_BASE e le
 * altre: e' quel che permette a un banco su PC di compilare QUESTO file invece
 * di un gemello (tools/host_colband.c). Sul Jaguar resta l'indirizzo di mappa. */
#ifndef CELL_LIST_BASE
#define CELL_LIST_BASE JAG_CELL_LIST
#endif
static T_CELL *const celllist = (T_CELL *)CELL_LIST_BASE;

/* Il fondo e' cambiato sotto i piedi degli attori (stanza nuova, ridisegno,
 * scorrimento di camera): la prossima passata deve ridisegnarli TUTTI, anche
 * quelli che non si muovono mai. Si consuma in Bg_BoxReset. Vale 1 all'avvio
 * perche' la prima passata in assoluto e' esattamente questo caso. */
static UWORD bg_full = 1;


/* ---------------------------------------------------------------------------
 * L'INDICE SULLA LISTA DELLE CELLE.
 *
 * Chi disegna dentro un RETTANGOLO - il ripristino e le tessere davanti agli
 * attori - deve trovare le celle che lo toccano. Fino a S41 le percorreva
 * tutte: 8781 voci per il ripristino a ogni passata, e altre 8781 per OGNI
 * attore in DrawOverBrick. Misurato allora: 20 tick + 15 tick, cioe' quanto
 * BT_EMIT intero, per scartarne il 99%.
 *
 * S41 rispose con un indice a BLOCCHI di 64 voci consecutive, saltate in un
 * colpo quando il loro riquadro non toccava il rettangolo. S50 l'ha misurato
 * sui rettangoli veri e l'ha tolto: 64 celle consecutive coprono una trentina
 * di colonne di griglia, cioe' ~720 pixel, quindi il riquadro di un blocco
 * interseca quasi sempre quello di un attore e il filtro non filtrava - 499
 * celle guardate per ogni tessera disegnata. Quel che resta e' qui sotto.
 * ------------------------------------------------------------------------- */

/* ---------------------------------------------------------------------------
 * L'INDICE PER COLONNA (S50), e perche' quello a blocchi rende poco.
 *
 * MISURATO, non dedotto (tools/m3d_scan.py, sui rettangoli veri che body_ref.py
 * produce per gli attori delle tre stanze di riferimento): una passata di M3d
 * percorre 3357 celle in media, ne porta 1211 fino a `blit_cell`, e ne disegna
 * SETTE. Cioe' 499 celle guardate per ogni tessera che finisce a schermo.
 *
 * La causa e' la taglia del blocco: 64 celle consecutive coprono una trentina
 * di colonne di griglia, cioe' ~720 pixel di schermo, quindi il riquadro di un
 * blocco interseca quasi sempre il rettangolo di un attore e il filtro non
 * filtra. Non e' un difetto dell'idea - e' che il blocco e' l'unita' sbagliata.
 *
 * MA LA LISTA NON E' DISORDINATA: `build_cells` la scrive con gz esterno, gx, e
 * gy interno, quindi le celle di una COLONNA (gz,gx) sono contigue. E una
 * colonna ha una x di schermo SOLA, perche' `px = (gx-gz)*ISO_X` non dipende da
 * gy. Bastano quindi 4096 offset di partenza - uno per colonna, monotoni - e da
 * un rettangolo si ricavano due bande:
 *
 *     d1 = gx-gz   dalla X.  Stretta: ~6 valori per un attore.
 *     d2 = gx+gz   dalla Y.  Larga, perche' una colonna e' alta CUBE_Y*ISO_GY.
 *
 * L'ORDINE DEL PITTORE SI CONSERVA PER COSTRUZIONE, ed e' la ragione per cui
 * questo si puo' fare e un riordino no: si itera gz crescente e dentro gx
 * crescente, che e' l'ordine in cui build_cells le ha scritte. Non si riordina
 * niente, si SALTA - come i blocchi, ma con l'unita' giusta.
 *
 * Misurato sugli stessi rettangoli: celle percorse 36928 -> 1608, chiamate a
 * blit_cell 13325 -> 526, cioe' -96% su entrambe, e la SEQUENZA delle tessere
 * disegnate identica - non solo il conteggio - su 4650 rettangoli di prova
 * confrontata sia con l'indice a blocchi sia con la scansione completa.
 * ------------------------------------------------------------------------- */
#define CUBE_COLS (CUBE_X * CUBE_Z)

static UWORD colstart[CUBE_COLS + 1];

/* Divisione con arrotondamento verso il BASSO e verso l'ALTO. Il C tronca verso
 * lo zero, e qui i numeratori sono negativi quasi sempre (ci sta dentro -vx).
 * Sbagliare il verso di uno stringe la banda di una colonna, e quel che si
 * perde e' una tessera al bordo del rettangolo: un difetto che si vede una
 * volta ogni tanto, in un punto della stanza, cioe' il peggiore da inseguire. */
static long fdiv(long a, long b) { return a >= 0 ? a / b : -((-a + b - 1) / b); }
static long cdiv(long a, long b) { return a >= 0 ? (a + b - 1) / b : -((-a) / b); }

typedef struct {
    WORD gz, gz1;                /* cursore di riga e ultima riga     */
    WORD gx, gxhi;               /* cursore di colonna e ultima       */
    WORD d1lo, d1hi, d2lo, d2hi; /* le due bande, estremi INCLUSIVI   */
} T_CBAND;

/* La riga gz corrente: l'intervallo di gx e' l'intersezione delle due bande. */
static void col_row(T_CBAND *b)
{
    WORD lo = (WORD)(b->gz + b->d1lo), hi = (WORD)(b->gz + b->d1hi);
    WORD lo2 = (WORD)(b->d2lo - b->gz), hi2 = (WORD)(b->d2hi - b->gz);

    if (lo2 > lo) lo = lo2;
    if (hi2 < hi) hi = hi2;
    if (lo < 0) lo = 0;
    if (hi > CUBE_X - 1) hi = CUBE_X - 1;
    b->gx = lo;
    b->gxhi = hi;
}

/* Le due bande per un rettangolo. Torna 0 se e' vuota - e allora il chiamante
 * non ha niente da fare, il che e' gia' un risparmio rispetto a 138 blocchi.
 *
 * `ymin` e' il filtro di profondita' che il chiamante applichera' comunque per
 * cella (`gy < ym` -> scarta): usarlo QUI stringe la banda in Y invece di
 * scoprirlo dopo. Chi non ne ha uno passa 0.
 *
 * Da  x0 - spanx <  px + vx  <=  x1          (px = d1*ISO_X)
 * e   y0 - spany <  py + vy  <=  y1          (py = d2*ISO_Y - gy*ISO_GY)
 * con gy in [ymin, CUBE_Y-1]. L'estremo destro non toglie l'hotspot perche'
 * BH_HOTX/BH_HOTY si leggono come UBYTE: non sono mai negativi, quindi zero e'
 * gia' il valore conservativo (misurato: il minimo e' proprio 0 in tutte e tre
 * le stanze di riferimento). */
static int col_open(T_CBAND *b, long vx, long vy,
                    long x0, long y0, long x1, long y1, int ymin)
{
    long d1lo = cdiv(x0 - (long)BgStat.spanx + 1 - vx, ISO_X);
    long d1hi = fdiv(x1 - vx, ISO_X);
    long d2lo = cdiv(y0 - (long)BgStat.spany + 1 - vy
                     + (long)ymin * ISO_GY, ISO_Y);
    long d2hi = fdiv(y1 - vy + (long)(CUBE_Y - 1) * ISO_GY, ISO_Y);
    long gz0, gz1;

    /* Si stringe agli estremi che la griglia puo' produrre PRIMA di scendere a
     * 16 bit: una camera lontana da' numeratori che in WORD non ci stanno, e
     * `gz + d1lo` andrebbe a capo invece di dare una banda vuota. */
    if (d1lo < -(CUBE_X - 1)) d1lo = -(CUBE_X - 1);
    if (d1hi >   CUBE_X - 1)  d1hi =   CUBE_X - 1;
    if (d2lo < 0)             d2lo = 0;
    if (d2hi > 2 * (CUBE_X - 1)) d2hi = 2 * (CUBE_X - 1);
    if (d1lo > d1hi || d2lo > d2hi)
        return 0;

    /* La riga gz e' utile solo se l'intersezione non e' vuota:
     * gz + d1hi >= d2lo - gz  e  d2hi - gz >= gz + d1lo. */
    gz0 = cdiv(d2lo - d1hi, 2);
    gz1 = fdiv(d2hi - d1lo, 2);
    if (gz0 < 0) gz0 = 0;
    if (gz1 > CUBE_Z - 1) gz1 = CUBE_Z - 1;
    if (gz0 > gz1)
        return 0;

    b->d1lo = (WORD)d1lo; b->d1hi = (WORD)d1hi;
    b->d2lo = (WORD)d2lo; b->d2hi = (WORD)d2hi;
    b->gz  = (WORD)gz0;
    b->gz1 = (WORD)gz1;
    col_row(b);
    return 1;
}

/* Il prossimo intervallo di celle NON VUOTO della banda, in ordine di pittore.
 *
 * Il percorso sta qui e non nei chiamanti apposta: i chiamanti sono due oggi e
 * tre quando ci passera' anche il ripristino, e una banda scritta tre volte
 * diverge appena la si tocca - la lezione che S49 ha pagato per intero. */
static int col_next(T_CBAND *b, UWORD *pk0, UWORD *pk1)
{
    while (b->gz <= b->gz1) {
        UWORD ci;

        if (b->gx > b->gxhi) {                 /* riga finita: la prossima */
            b->gz++;
            if (b->gz > b->gz1)
                break;
            col_row(b);
            continue;
        }
        ci = (UWORD)((int)b->gz * CUBE_X + b->gx);
        b->gx++;
        if (colstart[ci] != colstart[ci + 1]) {
            *pk0 = colstart[ci];
            *pk1 = colstart[ci + 1];
            return 1;
        }
    }
    return 0;
}


/* ---------------------------------------------------------------------------
 * Larghezza sorgente rappresentabile dal Blitter.
 *
 * A2 codifica la larghezza come `1.mm x 2^e` (TRM Rev.8): non tutte le
 * larghezze esistono, e la riga del brick va allargata alla prossima
 * rappresentabile con riempimento trasparente. E' l'unica ragione per cui il
 * buffer di lavoro non ha DX byte per riga.
 *
 * V4 aveva due tabelle di 64 voci scritte a mano. Qui si calcola, con la
 * stessa funzione che sta in tools/bg_ref.py: `--check-lut` pretende che
 * riproduca la tabella di V4 valore per valore, quindi non e' "una formula che
 * sembra giusta", e' quella formula verificata contro una configurazione che
 * ha girato a schermo.
 * ------------------------------------------------------------------------- */
static UWORD pad_width(int dx, ULONG *flags)
{
    int e, m, bestw = 0, bestf = 0;

    if (dx < 2)
        dx = 2;
    for (e = 0; e < 12; e++)              /* il TRM: esponente valido 0..11 */
        for (m = 0; m < 4; m++) {
            int num = (4 + m) << e;
            int w;
            if (num & 3)
                continue;                 /* non intero dopo /4             */
            w = num >> 2;
            if (w >= dx && !(w & 1) && (bestw == 0 || w < bestw)) {
                bestw = w;
                bestf = (e << 2) | m;
            }
        }
    *flags = 0x00010018UL | ((ULONG)bestf << 9);   /* PITCH1|PIXEL8|XADDPIX */
    return (UWORD)bestw;
}


/* ---------------------------------------------------------------------------
 * Palette. RESS slot 0 = 256 terne VGA a 6 bit; il Jaguar vuole R5 B5 G6.
 *
 * I due indici della diagnostica si forzano DOPO, sempre, anche quando la
 * palette non si carica: se l'unico modo di leggere un guasto dipendesse dal
 * dato che manca, il guasto sarebbe muto. Sono due dei quattordici slot che
 * LBA1 riserva alla UI riempiendoli di bianco (misurato: indici 2..15 e 26..30
 * bianchi in RESS, e nessun brick della scena li usa - tools/bg_ref.py
 * --check-clut), quindi la diagnostica non ruba un colore alla scena.
 * ------------------------------------------------------------------------- */
void Bg_ClutDiag(void)
{
    HW_W(JAG_CLUT + BG_DBG_IDX_FG * 2)  = 0xFFFF;   /* bianco */
    HW_W(JAG_CLUT + BG_DBG_IDX_LBL * 2) = 0x4210;   /* grigio medio */
}

ULONG Bg_LoadPalette(void)
{
    const unsigned char *p = (const unsigned char *)HQR_Get(HQR_RESS, 0);
    ULONG sum = 0;
    int i, six_bit = 1;

    if (!p || HQR_Size(HQR_RESS, 0) < 768) {
        Bg_ClutDiag();
        return 0;
    }
    /* Sei bit o otto? Lo dice il massimo, come in render_slide.normalize_palette.
     * Deciderlo a priori e' il genere di assunzione che produce un'immagine
     * quattro volte troppo scura senza dire niente. */
    for (i = 0; i < 768; i++)
        if (p[i] > 63) {
            six_bit = 0;
            break;
        }
    for (i = 0; i < 256; i++) {
        int c, v[3];
        UWORD w;
        for (c = 0; c < 3; c++) {
            int raw = p[i * 3 + c];
            v[c] = six_bit ? ((raw << 2) | (raw >> 4)) : raw;
        }
        w = (UWORD)(((v[0] >> 3) << 11) | ((v[2] >> 3) << 6) | (v[1] >> 2));
        /* I due indici della diagnostica si forzano QUI, dentro il ciclo, non
         * dopo. Forzandoli dopo la CLUT sarebbe comunque giusta ma la somma no,
         * perche' avrebbe contato i valori della palette: e' successo davvero,
         * e la sonda l'ha beccato con una differenza di FFFF-4210 esatti. Una
         * somma che non descrive quel che c'e' in memoria e' peggio di nessuna
         * somma, perche' costa un giro di build per scoprire che il difetto
         * era nello strumento. */
        if (i == BG_DBG_IDX_FG)
            w = 0xFFFF;
        else if (i == BG_DBG_IDX_LBL)
            w = 0x4210;
        HW_W(JAG_CLUT + i * 2) = w;
        sum += w;
    }
    return sum;
}


/* ---------------------------------------------------------------------------
 * Griglia: 4096 colonne RLE -> cubo in DRAM.
 *
 * Torna il numero di colonne che non producono esattamente CUBE_Y celle. Non
 * e' una curiosita': una colonna troppo lunga scriverebbe nella colonna dopo,
 * e il risultato sarebbe un fondo quasi giusto con qualche tessera sbagliata -
 * il genere di guasto che si passa per "un brick manca".
 * ------------------------------------------------------------------------- */
static int grid_decode(const le_byte *g)
{
    int gz, gx, odd = 0;

    for (gz = 0; gz < CUBE_Z; gz++)
        for (gx = 0; gx < CUBE_X; gx++) {
            const le_byte *p = g + LE16u(g + (gx + gz * CUBE_Z) * 2);
            UWORD *dst = cube + ((ULONG)(gz * CUBE_X + gx) * CUBE_Y);
            UWORD *dp = dst;
            UWORD *const dend = dst + CUBE_Y;
            int n = *p++;
            int w = 0;

            /* Stessa guardia per RUN e non per cella di decode_bricks: `w`
             * conta comunque tutte le celle prodotte, perche' e' lui a dire se
             * la colonna e' fuori misura, ma lo fa con una somma per run. */
            while (n--) {
                int op = *p++;
                int count = (op & 0x3F) + 1;
                int mode = op & 0xC0;
                UWORD *e = dp + count;

                w += count;
                if (e > dend)
                    e = dend;
                if (mode == 0x00) {
                    while (dp < e)
                        *dp++ = 0;
                } else if (mode == 0x80) {
                    UWORD v = LE16u(p);
                    p += 2;
                    while (dp < e)
                        *dp++ = v;
                } else {
                    const le_byte *s = p;
                    p += (ULONG)count * 2;
                    while (dp < e) {
                        *dp++ = LE16u(s);
                        s += 2;
                    }
                }
            }
            if (w != CUBE_Y)
                odd++;
            while (dp < dend)
                *dp++ = 0;
        }
    return odd;
}


/* La tabella dei blocchi, una volta per scena. */
static void bll_prepare(void)
{
    int i;

    bll_count = (int)(LE32u(bll_base) / 4);
    if (bll_count > BLL_MAX)
        bll_count = BLL_MAX;
    for (i = 0; i < bll_count; i++) {
        const le_byte *b = bll_base + LE32u(bll_base + i * 4);
        bll_ptr[i] = b;
        bll_cells[i] = (ULONG)b[0] * b[1] * b[2];
    }
}

/* Il brick nominato da una cella. 0 = nessuno.
 * Il controllo su `posblock` contro dx*dy*dz del blocco non e' pedanteria: e'
 * la stessa condizione che il riferimento Python applica, e senza di essa i due
 * darebbero risultati diversi sulle celle malformate invece che uguali. */
static UWORD bll_ref(int numblock, int posblock)
{
    if (numblock < 1 || numblock > bll_count)
        return 0;
    if ((ULONG)posblock >= bll_cells[numblock - 1])
        return 0;
    return LE16u(bll_ptr[numblock - 1] + 3 + posblock * 4 + 2);
}


/* ---------------------------------------------------------------------------
 * Passata 1: il cubo -> la lista compatta delle celle.
 *
 * Fa due mestieri in una passata sola: segna quali brick servono (che e' quel
 * che faceva mark_bricks) e scrive la lista in ORDINE DI PITTORE, che e' quel
 * che permette a render() di non toccare piu' il cubo.
 *
 * Perche' conta (misurato sulla scena 0): il cubo ha 102400 celle, di cui 8781
 * nominano un brick. Il 91% delle visite non produce niente, e fino a S29 quel
 * 91% si pagava DUE volte, una qui e una in render. Adesso il cubo si percorre
 * una volta per stanza; ogni ridisegno successivo percorre 8781 voci di 6 byte.
 *
 * px e py si ricavano per somme: sono costanti sulla colonna tranne il termine
 * di gy, che scende di ISO_GY per livello. Nessuna moltiplicazione nel ciclo
 * interno.
 * ------------------------------------------------------------------------- */
static UWORD build_cells(void)
{
    T_CELL *out = celllist;
    T_CELL *const outend = celllist + JAG_CELL_SLOTS;
    long i;
    int gz, gx, gy;

    for (i = 0; i < (long)JAG_BRICK_USED_BMP_SIZE; i++)
        usedbmp[i] = 0;
    for (i = 0; i < (long)JAG_BRICK_HANDLES_SIZE / 4; i++)
        handles[i] = 0;

    for (gz = 0; gz < CUBE_Z; gz++)
        for (gx = 0; gx < CUBE_X; gx++) {
            const UWORD *col = cube + ((ULONG)(gz * CUBE_X + gx) * CUBE_Y);
            WORD px = (WORD)((gx - gz) * ISO_X);
            WORD py = (WORD)((gx + gz) * ISO_Y);

            /* L'indice per colonna si riempie QUI, dove la colonna e' nota per
             * costruzione: due righe, nessuna passata in piu' e nessun modo di
             * dedurre (gz,gx) sbagliato da px/py dopo. */
            colstart[gz * CUBE_X + gx] = (UWORD)(out - celllist);

            for (gy = 0; gy < CUBE_Y; gy++, py -= ISO_GY) {
                UWORD v = col[gy];
                UWORD ref;
                if (!v)
                    continue;
                ref = bll_ref(v & 0xFF, v >> 8);
                if (!ref)
                    continue;
                ref--;
                usedbmp[ref >> 3] |= (UBYTE)(1 << (ref & 7));
                if (out >= outend)
                    TheEnd(TE_BG_CELLFULL, "celle");
                out->px  = px;
                out->py  = py;
                out->ref = ref;
                out->gy  = (UBYTE)gy;
                out->gxz = (UBYTE)(gx + gz);
                out++;
            }
        }
    colstart[CUBE_COLS] = (UWORD)(out - celllist);   /* chiude l'ultima */
    return (UWORD)(out - celllist);
}


/* Passata 2: RLE -> pixel, nel buffer di lavoro, in ordine di indice.
 * L'ordine conta solo perche' fissa il valore atteso di WORKLEN, ed e' lo
 * stesso che percorre il riferimento Python. */
static ULONG decode_bricks(void)
{
    UBYTE *cur = (UBYTE *)JAG_BRICK_WORK;
    const UBYTE *end = cur + JAG_BRICK_WORK_SIZE;
    int slots = HQR_Slots(HQR_BRICK);
    int i;

    BgStat.bricks = 0;
    BgStat.spanx = 0;
    BgStat.spany = 0;
    if (slots > (int)(JAG_BRICK_HANDLES_SIZE / 4))
        TheEnd(TE_BG_TOO_MANY, "brick");

    /* Il buffer di lavoro si azzera TUTTO in un colpo, e lo azzera il BLITTER.
     *
     * Serve azzerarlo perche' cosi' i salti dell'RLE - il 45,4% dei pixel,
     * 200.041 byte in 18.043 corse da 11 pixel medi, misurati sulla scena 0 -
     * diventano una somma di puntatore invece di undici scritture con la loro
     * guardia; e l'allargamento a WID perde il suo ciclo di coda.
     *
     * Il primo tentativo lo azzerava tessera per tessera con un ciclo di long
     * sul 68000. Funzionava, ma spostava il lavoro invece di toglierlo: 458 KB
     * restano 458 KB, e scritti dalla CPU piu' lenta della macchina. Il Blitter
     * li fa in una manciata di millisecondi ed e' esattamente il mestiere per
     * cui c'e' - la regola "nessun ciclo su pixel sul 68000" vale anche quando
     * i pixel non sono a schermo.
     *
     * Il buffer non e' un'immagine, ma per il Blitter lo diventa: WID2048 per
     * il numero di righe che copre la regione. Vedi A1_FLAGS_WORK. */
    HW_L(A1_BASE)  = JAG_BRICK_WORK;
    HW_L(A1_FLAGS) = A1_FLAGS_WORK;
    HW_L(A1_PIXEL) = 0;
    HW_L(A1_STEP)  = 0x00010000UL | ((ULONG)(-WORK_FILL_W) & 0xFFFFUL);
    HW_L(B_PATD)     = 0;
    HW_L(B_PATD + 4) = 0;
    HW_L(B_COUNT)  = ((JAG_BRICK_WORK_SIZE / WORK_FILL_W) << 16) | WORK_FILL_W;
    HW_L(B_CMD)    = B_CMD_FILL;
    Blit_Wait();

    for (i = 0; i < slots; i++) {
        const le_byte *src;
        UBYTE *row;
        ULONG flags;
        UWORD pw;
        int dx, dy, r;

        if (!(usedbmp[i >> 3] & (1 << (i & 7))))
            continue;
        src = (const le_byte *)HQR_Get(HQR_BRICK, i);
        if (!src || HQR_Size(HQR_BRICK, i) < 4)
            continue;                     /* slot assente: la cella restera' vuota */

        dx = src[0];
        dy = src[1];
        pw = pad_width(dx, &flags);
        if (cur + BH_PIXELS + (ULONG)pw * dy + 8 > end)
            TheEnd(TE_BG_WORKFULL, "brick");

        handles[i] = (ULONG)cur;
        *(ULONG *)(cur + BH_FLAGS) = flags;
        cur[BH_DX]   = src[0];
        cur[BH_DY]   = src[1];
        cur[BH_HOTX] = src[2];
        cur[BH_HOTY] = src[3];

        /* L'ingombro peggiore della stanza, per il rifiuto grezzo di
         * Bg_ClsBoxes. Si misura qui perche' qui le tessere passano tutte e una
         * volta sola, e perche' e' l'unico posto dove HOTX/HOTY e dx/dy si
         * vedono insieme: la tessera occupa [px, px + hot + d), quindi il
         * margine e' la somma, non il massimo dei due. */
        if ((UWORD)(src[2] + dx) > BgStat.spanx)
            BgStat.spanx = (UWORD)(src[2] + dx);
        if ((UWORD)(src[3] + dy) > BgStat.spany)
            BgStat.spany = (UWORD)(src[3] + dy);

        row = cur + BH_PIXELS;
        src += 4;
        for (r = 0; r < dy; r++) {
            int nb = *src++;
            UBYTE *rp = row;
            UBYTE *const rend = row + pw;

            /* La guardia non e' piu' per pixel ma per RUN: si taglia la fine
             * della corsa una volta sola. `rp` non supera mai `rend`, quindi
             * una riga piu' lunga di WID smette semplicemente di scrivere -
             * che e' esattamente quel che faceva la guardia `w < pw`. */
            while (nb--) {
                int op = *src++;
                int count = (op & 0x3F) + 1;
                int mode = op & 0xC0;
                UBYTE *e;

                if (mode == 0x00) {                    /* salto = trasparente */
                    rp += count;
                    if (rp > rend)
                        rp = rend;
                    continue;
                }
                e = rp + count;
                if (e > rend)
                    e = rend;
                if (mode == 0x80) {                    /* ripeti un pixel     */
                    UBYTE pix = *src++;
                    while (rp < e)
                        *rp++ = pix;
                } else {                               /* copia grezza        */
                    const le_byte *s = src;
                    src += count;                      /* la sorgente avanza  */
                    while (rp < e)                     /* tutta, anche tagliata */
                        *rp++ = *s++;
                }
            }
            row += pw;
        }
        cur = (UBYTE *)(((ULONG)cur + BH_PIXELS + (ULONG)pw * dy + 7) & ~7UL);
        BgStat.bricks++;
    }
    return (ULONG)cur - JAG_BRICK_WORK;
}


/* ---------------------------------------------------------------------------
 * Composizione. Il Blitter appoggia una tessera per cella, con l'indice 0
 * trasparente, nell'ordine del pittore (gz, poi gx, poi gy dal basso).
 *
 * NON C'E' UNA FINESTRA DI CELLE ATTORNO ALLA CAMERA, e la sua assenza e' una
 * correzione. V4 percorreva `camera +- (9,12,12,14)`: misurato sulla scena 0,
 * le celle che producono pixel visibili arrivano a gz-15, quindi quella
 * finestra ne perdeva tre colonne - 2307 pixel, e non ai bordi ma dentro
 * l'immagine. Non era una taratura da correggere: la regione visibile in
 * (gx,gz) e' un ROMBO, perche' le due coordinate di schermo sono gx-gz e
 * gx+gz, e nessuna scatola allineata agli assi lo contiene senza tagliare o
 * sprecare ([[feedback_iso_bounds_pitfall]]). Ritarare i quattro numeri li
 * avrebbe fatti tornare qui e cadere in un'altra stanza.
 *
 * E NON SI PERCORRE PIU' NEMMENO IL CUBO (S30): si percorre la lista compatta
 * che build_cells ha gia' messo in ordine di pittore. Il rifiuto per tessera
 * resta - ed e' quello del gioco, non piu' un margine inventato - ma si applica
 * a 8781 voci invece che a 102400 celle, e senza rileggere la BLL.
 * ------------------------------------------------------------------------- */
/* UNA cella, ritagliata a un rettangolo qualsiasi invece che allo schermo.
 *
 * E' il corpo del ciclo di render() estratto, e il ritaglio non e' codice nuovo:
 * lsk/tsk/w/h c'erano gia' per lo schermo, qui i quattro limiti diventano
 * argomenti. Con (0,0,639,591) fa esattamente quel che faceva prima; con il
 * rettangolo di un attore e' il `CopyMask` sotto `SetClip` di DrawOverBrick.
 *
 * TAGLIATA IN DUE IN S50, e non per eleganza. La prima meta' dipende solo dalla
 * CELLA: le due somme della camera, il ritaglio DOS, la lettura del manico e i
 * quattro byte dell'intestazione del brick - cinque accessi in memoria. La
 * seconda dipende dal rettangolo. `render_rects` tiene il ciclo dei rettangoli
 * DENTRO quello delle celle (e deve: e' l'ordine di pittore per ciascun
 * rettangolo), quindi con `count` rettangoli rifaceva la prima meta' `count`
 * volte per la stessa cella. Misurato (tools/m3d_scan.py --rects): in una
 * stanza con sei attori, 2916 preparazioni dove ne bastano 486.
 *
 * `blit_cell` resta e diventa le due meta' in fila, quindi NON c'e' una seconda
 * copia della logica da tenere d'accordo: chi ha un rettangolo solo - render(),
 * over_bricks, le porte - chiama questa e non si accorge di niente.
 *
 * Gli estremi sono INCLUSIVI, come ClipXmax/ClipYmax del gioco.
 * Torna 1 se ha disegnato. */
typedef struct {
    const UBYTE *b;                 /* il brick decodificato          */
    long sx, sy;                    /* angolo, in coordinate schermo   */
    int  dx, dy;                    /* il suo ingombro                 */
} T_CPREP;

/* La meta' che dipende dalla cella. Torna 0 se la cella non disegna comunque,
 * quale che sia il rettangolo. */
static int cell_prep(T_CPREP *p, const T_CELL *c, long vx, long vy)
{
    long sx_col, sy_cell;

    sx_col = (long)c->px + vx;
    if (sx_col < DOS_CLIP_X0 || sx_col >= DOS_CLIP_X1)
        return 0;                              /* AffBrickBlock, meta' sulla X */
    sy_cell = (long)c->py + vy;
    if (sy_cell < DOS_CLIP_Y0 || sy_cell >= DOS_CLIP_Y1)
        return 0;                              /* AffBrickBlock, meta' sulla Y */

    p->b = (const UBYTE *)handles[c->ref];
    if (!p->b)
        return 0;

    p->dx = p->b[BH_DX];
    p->dy = p->b[BH_DY];
    p->sx = sx_col + p->b[BH_HOTX];
    p->sy = sy_cell + p->b[BH_HOTY];
    return 1;
}

/* La meta' che dipende dal rettangolo. */
static int cell_emit(const T_CPREP *p, long cx0, long cy0, long cx1, long cy1)
{
    const UBYTE *b = p->b;
    long sx = p->sx, sy = p->sy, lsk, tsk, w, h;
    int dx = p->dx, dy = p->dy;

    if (sx + dx <= cx0 || sx > cx1)
        return 0;
    if (sy + dy <= cy0 || sy > cy1)
        return 0;

    lsk = 0;
    if (sx < cx0) {
        lsk = cx0 - sx;
        sx = cx0;
    }
    tsk = 0;
    if (sy < cy0) {
        tsk = cy0 - sy;
        sy = cy0;
    }
    w = sx + dx - lsk;                         /* bordo destro, escluso */
    if (w > cx1 + 1)
        w = cx1 + 1;
    w -= sx;
    if (w <= 0)
        return 0;
    h = sy + dy - tsk;
    if (h > cy1 + 1)
        h = cy1 + 1;
    h -= sy;
    if (h <= 0)
        return 0;

    HW_L(A1_PIXEL) = ((ULONG)sy << 16) | (ULONG)sx;
    HW_L(A1_STEP)  = 0x00010000UL | ((ULONG)(-w) & 0xFFFFUL);
    HW_L(A2_STEP)  = 0x00010000UL | ((ULONG)(-w) & 0xFFFFUL);
    HW_L(A2_BASE)  = (ULONG)(b + BH_PIXELS);
    HW_L(A2_FLAGS) = *(const ULONG *)(b + BH_FLAGS);
    HW_L(A2_PIXEL) = ((ULONG)tsk << 16) | (ULONG)lsk;
    HW_L(B_COUNT)  = ((ULONG)h << 16) | (ULONG)w;
    HW_L(B_CMD)    = B_CMD_SPRITE;
    Blit_Wait();
    return 1;
}

/* Le due meta' in fila: quel che blit_cell ha sempre fatto, per chi ha un
 * rettangolo solo. */
static int blit_cell(const T_CELL *c, long vx, long vy,
                     long cx0, long cy0, long cx1, long cy1)
{
    T_CPREP p;

    if (!cell_prep(&p, c, vx, vy))
        return 0;
    return cell_emit(&p, cx0, cy0, cx1, cy1);
}


/* ---------------------------------------------------------------------------
 * M3d: LE TESSERE DAVANTI ALL'ATTORE, RIDISEGNATE SOPRA DI LUI.
 *
 * `DrawOverBrick` (GRILLE.C:500) NON intreccia i due cicli: il fondo si disegna
 * tutto, poi l'attore sopra, e poi le tessere che gli stanno DAVANTI si
 * ridisegnano sopra l'attore, ritagliate al suo rettangolo. E' anche il motivo
 * per cui va d'accordo con il fondo intatto che abbiamo: nessuna delle due
 * passate esistenti cambia forma.
 *
 * Il ritaglio al rettangolo dell'attore NON e' un'ottimizzazione: senza, una
 * tessera ridisegnata per intero cancellerebbe l'attore DISEGNATO PRIMA che le
 * stava sotto in un altro punto.
 *
 * Il gioco limita anche le colonne e le righe da percorrere (startcol/endcol,
 * `Ys+38 > ClipYmin`): sono filtri di ingombro, e chi passa il filtro senza
 * toccare il rettangolo non scrive comunque un pixel. Qui il filtro lo fa
 * blit_cell con la geometria vera, quindi i pixel scritti sono gli stessi; la
 * lista si percorre tutta perche' non e' divisa in colonne.
 * ------------------------------------------------------------------------- */
/* Il CICLO, estratto perche' da S49 ha due destinazioni: il framebuffer e il
 * buffer dello sprite dell'eroe. Estrarlo non cambia una riga di quel che fa -
 * cambia che le due destinazioni non possono divergere, che e' la stessa
 * ragione per cui render_rects e' stato estratto da Bg_ClsPending. */
static void over_bricks(WORD xm, WORD ym, WORD zm,
                        WORD cx0, WORD cy0, WORD cx1, WORD cy1)
{
    long vx = BgStat.vx, vy = BgStat.vy;
    int xz = (int)xm + zm;
    T_CBAND b;
    UWORD k0, k1, k;

    /* L'indice per COLONNA (S50). Il filtro di profondita' (gy, gxz) resta per
     * cella - non e' una proprieta' della colonna - ma `ym` entra nella banda,
     * quindi le colonne che nemmeno lo raggiungono non si aprono. */
    if (!col_open(&b, vx, vy, cx0, cy0, cx1, cy1, ym))
        return;

    while (col_next(&b, &k0, &k1)) {
        for (k = k0; k < k1; k++) {
            const T_CELL *c = &celllist[k];

            if ((int)c->gy < ym)
                continue;
            if ((int)c->gxz <= xz)
                continue;
            BgStat.overbrick += (UWORD)blit_cell(c, vx, vy, cx0, cy0, cx1, cy1);
        }
    }
}

void Bg_DrawOverBrick(WORD xm, WORD ym, WORD zm,
                      WORD cx0, WORD cy0, WORD cx1, WORD cy1)
{
    /* Scrive nel framebuffer: se la cancellazione del disegno precedente e'
     * ancora armata, e' adesso o mai piu'. */
    Bg_ClsPending();
    over_bricks(xm, ym, zm, cx0, cy0, cx1, cy1);
}

/* ---------------------------------------------------------------------------
 * LE STESSE TESSERE, DENTRO IL BUFFER DELLO SPRITE (S49).
 *
 * Con l'eroe promosso a oggetto dell'OP, quel che l'OP compone sta SOPRA il
 * fondo per costruzione: senza questa passata Twinsen camminerebbe davanti ai
 * muri invece che dietro, che e' un artefatto visivo, cioe' un bug.
 *
 * NON C'E' UNA SECONDA GEOMETRIA, e non e' un risparmio di righe: le tessere
 * da ridisegnare, l'ordine e il ritaglio devono essere gli stessi ESATTI del
 * percorso vecchio, o il giorno che l'attore torna nel fondo (fuori campo, o
 * troppo largo per la finestra) l'immagine cambierebbe fra un quadro e il
 * successivo. Cambia UN registro: dove il Blitter scrive.
 *
 * `base` e' la stessa base VIRTUALE del kernel GPU - ACTOR_SPRITES meno
 * y0 righe - quindi A1_PIXEL resta in coordinate di SCHERMO e il ritaglio
 * (cx0..cy1) resta quello di sempre. Resta phrase-allineata perche' una riga
 * e' 640 byte, cioe' 80 phrase esatte.
 *
 * A1 si programma per intero invece di ereditarlo: il percorso vecchio si
 * appoggia a quel che ha lasciato l'ultimo che ha scritto (render, oppure
 * scroll_row, che le FLAGS le mette diverse), e una destinazione nuova non e'
 * il posto dove continuare a fidarsi di uno stato ambientale.
 *
 * Bg_ClsPending NON si chiama, ed e' il punto di tutto: questa passata non
 * tocca il framebuffer, quindi non c'e' niente da cancellare prima. La rete di
 * sicurezza in coda a Body_DrawAll resta a garantire che il ripristino armato
 * venga comunque fatto.
 * ------------------------------------------------------------------------- */
void Bg_DrawOverBrickSpr(WORD xm, WORD ym, WORD zm,
                         WORD cx0, WORD cy0, WORD cx1, WORD cy1,
                         ULONG base)
{
    HW_L(A1_BASE)  = base;
    HW_L(A1_FLAGS) = A1_FLAGS_BG;
    over_bricks(xm, ym, zm, cx0, cy0, cx1, cy1);
    HW_L(A1_BASE)  = JAG_BG_FB;
    HW_L(A1_FLAGS) = A1_FLAGS_BG;
}


/* La variante delle PORTE (GRILLE.C:570). Prova gx e gz SEPARATI invece della
 * loro somma, e li ricava: `gx-gz` e' `px / ISO_X` esatto, quindi
 * gx = (gxz + d)/2 e gz = (gxz - d)/2. Le due condizioni del gioco sono
 * disgiunte (se le due coordinate sono uguali, nessuna delle due e' maggiore),
 * quindi qui sono un `or`. */
void Bg_DrawOverBrick3(WORD xm, WORD ym, WORD zm,
                       WORD cx0, WORD cy0, WORD cx1, WORD cy1)
{
    long vx = BgStat.vx, vy = BgStat.vy;
    T_CBAND b;
    UWORD k0, k1, k;

    Bg_ClsPending();                     /* come sopra: si sta per scrivere   */

    /* Stessa banda dei corpi: la geometria che la ricava e' quella della
     * CELLA, non quella del filtro di profondita', quindi vale identica per la
     * variante delle porte - che di diverso ha solo il confronto per cella. */
    if (!col_open(&b, vx, vy, cx0, cy0, cx1, cy1, ym))
        return;

    while (col_next(&b, &k0, &k1)) {
        for (k = k0; k < k1; k++) {
            const T_CELL *c = &celllist[k];
            int d, gx, gz;

            if ((int)c->gy < ym)
                continue;
            d  = c->px / ISO_X;
            gx = ((int)c->gxz + d) >> 1;
            gz = ((int)c->gxz - d) >> 1;
            if (!((gz == zm && gx == xm) || gz > zm || gx > xm))
                continue;
            BgStat.overbrick += (UWORD)blit_cell(c, vx, vy, cx0, cy0, cx1, cy1);
        }
    }
}


/* ---------------------------------------------------------------------------
 * LO SCORRIMENTO DEL FONDO (S42) - il fondo NON si rifa' da capo quando la
 * camera si muove.
 *
 * IL COSTO CHE SI EVITA: un ridisegno intero e' 756 tessere, ~3,4 s misurati in
 * S41. Ma quel che il fondo contiene DOPO un passo di camera e' quasi tutto
 * quel che conteneva PRIMA, solo spostato: la regola di camera di LBA1 salta
 * quando l'eroe esce dal riquadro centrale, e il salto lascia scoperto un
 * bordo, non lo schermo. Quindi si sposta col Blitter quel che c'e' gia' e si
 * ridisegna la sola BANDA che entra.
 *
 * PERCHE' E' CORRETTO, e non e' ovvio: la banda si ridisegna con la STESSA
 * macchina del ripristino a rettangolo (S41) - le celle che TOCCANO la banda,
 * nell'ordine del PITTORE, ritagliate alla banda. Una tessera che sta a cavallo
 * del confine si ridisegna per la sua parte dentro, e la parte fuori e' gia'
 * giusta perche' viene dallo stesso ordine di pittore di prima. E una tessera
 * che sta tutta nella parte conservata ma il cui INGOMBRO entra nella banda
 * viene ridisegnata anche lei, perche' il test e' sull'ingombro: e' quel che
 * impedisce a una tessera "dietro" di coprire una "davanti" sul confine.
 *
 * RIGA PER RIGA, E ATTRAVERSO UN APPOGGIO. La copia si sovrappone a se stessa:
 * il Blitter percorre una riga da sinistra a destra, quindi spostando verso
 * DESTRA rileggerebbe byte che ha appena scritto. Una riga per volta dentro un
 * appoggio da 640 byte toglie il problema per costruzione, senza dipendere dal
 * verso in cui il Blitter percorre una riga - che e' esattamente il genere di
 * dettaglio che su BigPEmu funziona e sul ferro no
 * ([[feedback_gpu_handshake_hw]]). L'ordine delle RIGHE invece lo decido io, e
 * quello conta: scendendo si parte dal basso.
 *
 * Costo: 1184 blit da 640 byte, cioe' qualche millisecondo contro 3,4 s.
 * ------------------------------------------------------------------------- */
static ULONG scrollrow[JAG_BG_W / 4 + 2];

/* Copia UNA riga, da (sx,sy) a (dx,dy), larga w, passando dall'appoggio.
 * `A1_FLAGS`/`A2_FLAGS` a $00004A18 e i passi a $0001FD80 sono la
 * configurazione gia' verificata sul ferro per le copie fondo->fondo
 * (src/scene_viewer.s:1010-1016, S22B), presa verbatim: un bit sbagliato qui
 * non da' un errore, da' un'immagine quasi giusta. */
static void scroll_row(long sx, long sy, long dx, long dy, long w)
{
    ULONG buf = ((ULONG)(void *)scrollrow + 7) & ~7UL;
    ULONG step = 0x00010000UL | ((ULONG)(-w) & 0xFFFFUL);

    /* fondo -> appoggio */
    HW_L(A1_BASE)  = buf;
    HW_L(A1_FLAGS) = 0x00004A18UL;
    HW_L(A1_PIXEL) = 0;
    HW_L(A1_STEP)  = step;
    HW_L(A2_BASE)  = JAG_BG_FB;
    HW_L(A2_FLAGS) = 0x00004A18UL;
    HW_L(A2_PIXEL) = ((ULONG)sy << 16) | ((ULONG)sx & 0xFFFFUL);
    HW_L(A2_STEP)  = step;
    HW_L(B_COUNT)  = (1UL << 16) | (ULONG)w;
    HW_L(B_CMD)    = B_CMD_OPAQUE;
    Blit_Wait();

    /* appoggio -> fondo */
    HW_L(A1_BASE)  = JAG_BG_FB;
    HW_L(A1_FLAGS) = 0x00004A18UL;
    HW_L(A1_PIXEL) = ((ULONG)dy << 16) | ((ULONG)dx & 0xFFFFUL);
    HW_L(A1_STEP)  = step;
    HW_L(A2_BASE)  = buf;
    HW_L(A2_FLAGS) = 0x00004A18UL;
    HW_L(A2_PIXEL) = 0;
    HW_L(A2_STEP)  = step;
    HW_L(B_COUNT)  = (1UL << 16) | (ULONG)w;
    HW_L(B_CMD)    = B_CMD_OPAQUE;
    Blit_Wait();
}

static void render(long vx, long vy)
{
    const T_CELL *c = celllist;
    UWORD n = BgStat.celllist;

    /* Il fondo riparte da zero: quel che gli attori avevano scritto sopra non
     * c'e' piu', quindi la prossima passata li deve rifare TUTTI - anche quelli
     * che non si muovono mai. Vedi Bg_BoxDamaged. */
    bg_full = 1;

    /* Azzeramento del fondo a motivo. 379 KB in un colpo solo. */
    HW_L(A1_BASE)  = JAG_BG_FB;
    HW_L(A1_FLAGS) = A1_FLAGS_BG;
    HW_L(A1_PIXEL) = 0;
    HW_L(A1_STEP)  = 0x0001FD80UL;                 /* y +1, x -640 */
    HW_L(B_PATD)     = 0;
    HW_L(B_PATD + 4) = 0;
    HW_L(B_COUNT)  = ((ULONG)JAG_BG_H << 16) | JAG_BG_W;
    HW_L(B_CMD)    = B_CMD_FILL;
    Blit_Wait();

    BgStat.cells = 0;
    /* `overbrick` NON si azzera qui, e la ragione e' scritta nella riga che
     * questa sostituisce: era azzerata dal fondo "finche' le due passate
     * restano in quest'ordine, cioe' finche' nessuno le separa".
     *
     * Il profilo per stadio (tasto OPTION) le separa da S34: rifa' Body_DrawAll
     * in ciclo SENZA ridisegnare il fondo, quindi il contatore sommava due
     * tessere per passata e non tornava piu' indietro. Si vedeva come una riga
     * che "cambia con la posa" - cambiava col numero di passate.
     *
     * Ora lo azzera la passata che lo RIEMPIE (Body_DrawAll), che e' anche
     * l'unica che ci scrive: `render` incrementa `cells`, non questo. */

    for (; n--; c++)
        BgStat.cells += (UWORD)blit_cell(c, vx, vy,
                                         0, 0, JAG_BG_W - 1, JAG_BG_H - 1);
}


/* ---------------------------------------------------------------------------
 * Misura del risultato. Una passata sola sul framebuffer che produce tutte e
 * cinque le sonde: le due somme di Fletcher (`s1` da' il contenuto, `s2`
 * l'ordine, quindi due pixel scambiati non si compensano), il riquadro dei
 * pixel non trasparenti e il loro numero.
 *
 * Sono diagnosi che si escludono a vicenda, ed e' il motivo per cui sono
 * cinque e non una: riquadro diverso = immagine traslata (camera, hot spot,
 * centratura); riquadro uguale e conteggio diverso = tessere mancanti; tutto
 * uguale e somme diverse = pixel giusti nei posti sbagliati.
 * ------------------------------------------------------------------------- */
/* ---------------------------------------------------------------------------
 * LA COLLISIONE DEL DECORO (M6) - `WorldColBrick`, GRILLE_A.C:114.
 *
 * Sta in questo file e non in object_move.c perche' qui vivono le due cose che
 * legge: il CUBO (la griglia 64x25x64) e la libreria dei BLOCCHI. Sono le
 * stesse identiche strutture che il fondo percorre per disegnare - non una
 * copia per la collisione: la cella che nomina un brick e quella che dice se ci
 * si sbatte contro sono la stessa cella.
 *
 * Il codice di collisione e' il PRIMO byte della voce da quattro del blocco;
 * `bll_ref` piu' sopra legge la word a +2 della stessa voce, che e' il brick da
 * disegnare. Un dato, due letture.
 *
 *   0     = si passa
 *   1     = muro
 *   2..13 = rampe e scale: non fermano, ALZANO. Le gestisce ReajustPos.
 *
 * `XMap/YMap/ZMap` sono globali in upstream e restano globali qui: ReajustPos
 * le legge per sapere di QUALE cella sta calcolando la rampa, e passarle come
 * argomenti vorrebbe dire cambiare la firma di tutti i chiamanti per un dato
 * che il gioco tratta come stato condiviso.
 * ------------------------------------------------------------------------- */
LONG XMap, YMap, ZMap;

UBYTE Grid_ColBrick(LONG xw, LONG yw, LONG zw)
{
    LONG xm = (xw + DEMI_BRICK_XZ) >> 9;
    LONG ym = yw >> 8;
    LONG zm = (zw + DEMI_BRICK_XZ) >> 9;
    UWORD v;
    int block, pos;

    XMap = xm;
    YMap = ym;
    ZMap = zm;

    if (xm < 0 || xm >= CUBE_X || zm < 0 || zm >= CUBE_Z)
        return 0;

    /* Sotto il pavimento e' MURO, non vuoto: e' quel che impedisce di cadere
     * fuori dal mondo (GRILLE_A.C:139). */
    if (ym <= -1)
        return 1;
    if (ym > CUBE_Y - 1)
        return 0;

    v = cube[((ULONG)(zm * CUBE_X + xm) * CUBE_Y) + ym];
    block = v & 0xFF;
    pos   = v >> 8;

    if (block) {
        if (block > bll_count)
            return 0;
        if ((ULONG)pos >= bll_cells[block - 1])
            return 0;
        return (UBYTE)bll_ptr[block - 1][3 + pos * 4];
    }
    /* Cella senza blocco: il byte alto E' il codice, non un indice. */
    return (UBYTE)pos;
}

/* ---------------------------------------------------------------------------
 * `WorldColBrickFull`, GRILLE_A.C:35. Come sopra, ma guarda anche SOPRA la
 * testa: `ymax` e' l'altezza dell'attore, e se una cella occupata sta dentro
 * quell'altezza il passaggio e' murato anche se il pavimento sarebbe buono.
 *
 * E' la differenza fra Twinsen e tutti gli altri: solo l'eroe la usa (e solo se
 * non ha COL_BASSE), ed e' quel che gli impedisce di infilarsi sotto una
 * sporgenza in cui non ci sta.
 * ------------------------------------------------------------------------- */
UBYTE Grid_ColBrickFull(LONG xw, LONG yw, LONG zw, LONG ymax)
{
    LONG xm = (xw + DEMI_BRICK_XZ) >> 9;
    LONG ym = yw >> 8;
    LONG zm = (zw + DEMI_BRICK_XZ) >> 9;
    const UWORD *ptc;
    LONG height_check;
    UWORD v;
    int block, pos;
    UBYTE al;

    XMap = xm;
    YMap = ym;
    ZMap = zm;

    if (xm < 0 || xm >= CUBE_X || zm < 0 || zm >= CUBE_Z)
        return 0;
    if (ym <= -1)
        return 1;
    if (ym > CUBE_Y - 1)
        return 0;

    ptc = cube + ((ULONG)(zm * CUBE_X + xm) * CUBE_Y) + ym;
    v = *ptc;
    block = v & 0xFF;
    pos   = v >> 8;

    if (block) {
        if (block > bll_count || (ULONG)pos >= bll_cells[block - 1])
            al = 0;
        else
            al = (UBYTE)bll_ptr[block - 1][3 + pos * 4];
    } else {
        al = (UBYTE)pos;
    }

    height_check = (ymax + 255) >> 8;
    while (ym < CUBE_Y - 1 && height_check > 0) {
        ptc++;
        ym++;
        if (*ptc != 0)
            return 1;
        height_check--;
    }
    return al;
}

/* ---------------------------------------------------------------------------
 * GLI ACCESSORI GREZZI PER IL FORK (S51).
 *
 * `GetShadow` e `WorldCodeBrick` di GRILLE.C leggono il cubo e la libreria dei
 * blocchi direttamente, e scrivono globali del motore trapiantato (Nxw,
 * ShadowCol, ReajustPos). Il cubo e la libreria stanno qui dentro, i globali
 * di la': quindi qui si espone solo la LETTURA, e le due funzioni vivono in
 * engine_c/port/jag_grille.c, trascritte riga per riga.
 *
 * Con i controlli di confine che l'originale non aveva: GetShadow indirizza il
 * cubo senza guardare se la cella esiste, e sul PC una lettura fuori dal
 * buffer tornava un byte qualunque; qui torna "cella vuota".
 * ------------------------------------------------------------------------- */

/* La cella come UWORD: blocco nel byte basso, posizione nel blocco (o codice,
 * se il blocco e' zero) nel byte alto. */
UWORD Grid_Cell(LONG xm, LONG ym, LONG zm)
{
    if (xm < 0 || xm >= CUBE_X || zm < 0 || zm >= CUBE_Z
        || ym < 0 || ym >= CUBE_Y)
        return 0;
    return cube[((ULONG)(zm * CUBE_X + xm) * CUBE_Y) + ym];
}

/* Il byte `off` della voce del blocco `block` (base 1, come nel cubo), contato
 * dall'inizio della voce BLL: +0..+2 intestazione, poi quattro byte per cella.
 * Zero fuori dalla voce. */
UBYTE Grid_BlockByte(int block, ULONG off)
{
    if (block < 1 || block > bll_count)
        return 0;
    if (off >= 3 + bll_cells[block - 1] * 4)
        return 0;
    return (UBYTE)bll_ptr[block - 1][off];
}


void Bg_Measure(void)
{
    const ULONG *p = (const ULONG *)JAG_BG_FB;
    ULONG s1 = 0, s2 = 0, nz = 0;
    int minx = JAG_BG_W, miny = JAG_BG_H, maxx = -1, maxy = -1;
    int y;

    for (y = 0; y < JAG_BG_H; y++) {
        int i;
        for (i = 0; i < JAG_BG_W / 4; i++) {
            ULONG w = *p++;
            s1 += w;
            s2 += s1;
            if (w) {
                int k;
                for (k = 0; k < 4; k++)
                    if ((w >> ((3 - k) * 8)) & 0xFF) {
                        int x = i * 4 + k;
                        nz++;
                        if (x < minx)
                            minx = x;
                        if (x > maxx)
                            maxx = x;
                        if (y < miny)
                            miny = y;
                        if (y > maxy)
                            maxy = y;
                    }
            }
        }
    }
    BgStat.sum1 = s1;
    BgStat.sum2 = s2;
    BgStat.nonzero = nz;
    BgStat.bbminx = (UWORD)minx;
    BgStat.bbminy = (UWORD)miny;
    BgStat.bbmaxx = (UWORD)maxx;
    BgStat.bbmaxy = (UWORD)maxy;
}


/* ---------------------------------------------------------------------------
 * Costruzione completa del fondo di una scena.
 * ------------------------------------------------------------------------- */
/* Indicatore di fase.
 *
 * La costruzione dura circa un secondo con gli interrupt mascherati, e in quel
 * secondo il marcatore di vita non batte ancora: un blocco dentro Bg_Build
 * sarebbe indistinguibile da uno schermo che ci mette un po'. Il quadratino
 * dice fin dove si e' arrivati (decisione 3.6: il boot deve dire di essere
 * arrivato in fondo).
 *
 * Non si usa il registro BG per questo, che sarebbe la via solita
 * ([[feedback_debug_border_milestones]]): il BMobj del fondo copre tutto
 * l'active display, quindi BG non si vedrebbe. I quadratini finiscono sul
 * framebuffer nero e li cancella l'azzeramento del Blitter di render(), che e'
 * a sua volta un'informazione: nessun quadratino e nessuna scena = siamo dentro
 * la composizione; scena senza righe esadecimali = siamo dentro measure().
 *
 * NIENTE quadratino dopo render(): ci ha provato, e measure() lo ha CONTATO.
 * Sono stati 256 pixel non trasparenti in piu' e 64 long da FFFFFFFF nella
 * somma - le tre sonde sono cadute tutte insieme e con differenze esatte, il
 * che e' il modo migliore in cui poteva andare, ma la lezione vale: uno
 * strumento che scrive nel framebuffer va spento PRIMA di misurare il
 * framebuffer. */
static void stage(int n)
{
    Dbg_Fill(8 + n * 24, 4, 16, 16, 255);
}

/* Posizione d'ingresso valida per la prossima costruzione. Vedi Bg_EnterAt. */
static int  entry_set;
static long entry_x, entry_y, entry_z;

void Bg_EnterAt(long x, long y, long z)
{
    entry_set = 1;
    entry_x = x;
    entry_y = y;
    entry_z = z;
}

#if !defined(JAG_FORK) || !JAG_FORK
/* Il percorso di S50: con il fork collegato, LoadScene qui sotto sarebbe il
 * SUO (DISKFUNC.C), che fa un'altra cosa. Vedi engine_main.c, JAG_FORK. */
int Bg_Build(int numscene)
{
    /* L'ARENA PER-CUBO MUORE QUI, ed e' il posto in cui muore anche upstream
     * (ChangeCube, OBJECT.C:518). Fino a M2 nessuno la liberava e non si
     * notava: le uniche voci decompresse erano scena, griglia e BLL, e tre
     * stanze ci stavano lo stesso. Da M3a ogni stanza aggiunge anche le fiche
     * FILE3D e i corpi dei suoi attori, quindi senza questa riga l'arena
     * crescerebbe di stanza in stanza fino a un TheEnd - che arriverebbe alla
     * terza porta e sembrerebbe un guasto della terza porta.
     *
     * Va PRIMA di LoadScene, perche' e' LoadScene a ripopolarla: gli script
     * degli attori e le loro fiche sono puntatori dentro queste voci. */
    HQM_Free_All();
    BgStat.arena0 = Hal_MemUsedCube();      /* deve essere 0, per ogni stanza */

    BgStat.clut_sum = Bg_LoadPalette();

    if (!LoadScene(numscene))
        return 0;

    /* L'ingresso da una porta si applica QUI: dopo che LoadScene ha letto il
     * punto di partenza della stanza e PRIMA che Scene_Derive ne ricavi la
     * camera. E' lo stesso ordine di ChangeCube (OBJECT.C:518-537), e l'ordine
     * e' tutto: applicarlo dopo darebbe la stanza giusta inquadrata dal posto
     * sbagliato, cioe' il difetto che non si vede finche' non si confronta. */
    if (entry_set) {
        Scene.CubeStartX = (WORD)entry_x;
        Scene.CubeStartY = (WORD)entry_y;
        Scene.CubeStartZ = (WORD)entry_z;
        entry_set = 0;
    }
    Scene_Derive();

    if (!Bg_LoadRoom(numscene))
        return 0;
    Bg_Show(Scene.CamGX, Scene.CamGY, Scene.CamGZ);
    return 1;
}
#endif

/* ---------------------------------------------------------------------------
 * BG_BUILD IN DUE (S51), per il motore trapiantato.
 *
 * Il fork carica e disegna in due momenti diversi: ChangeCube chiama
 * InitGrille (OBJECT.C:540) PRIMA di fissare la camera (StartXCube, :597), e
 * il fondo compare solo col primo AffScene(TRUE). Bg_Build faceva tutte e due
 * le cose insieme, quindi si taglia qui: Bg_LoadRoom e' InitGrille, Bg_Show e'
 * il fondo di AffScene(TRUE). Bg_Build resta la loro composizione, con le
 * stesse righe e nello stesso ordine di prima (le due funzioni sono il suo
 * corpo spostato, non una copia), cosi' il percorso vecchio non cambia.
 *
 * `bg_shown` a zero dopo un caricamento: il primo Bg_Show disegna tutto, i
 * successivi scorrono (Bg_ScrollTo), che e' la differenza fra un cambio di
 * stanza e un passo di camera.
 * ------------------------------------------------------------------------- */
static int bg_shown;

int Bg_LoadRoom(int numscene)
{
    const le_byte *grid;
    ULONG t0, t;

    /* Stanza nuova: la memoria di dov'erano gli sprite non vale piu'. */
    Sprite_ForgetBoxes();
    bg_shown = 0;

    grid = (const le_byte *)HQR_Get(HQR_GRID, numscene);
    bll_base = (const le_byte *)HQR_Get(HQR_BLL, numscene);
    if (!grid || !bll_base)
        return 0;
    bll_prepare();

    /* Cronometro senza cronometro: si contano gli interrupt video, che a
     * questo punto girano (Bg_Build e' chiamata dal MainLoop, non da
     * engine_init - vedi il commento in engine_main.c). */
    t0 = JAG_HAL_VBL_TICK;
    BgStat.gridodd = (UWORD)grid_decode(grid);   stage(0);
    t = JAG_HAL_VBL_TICK; BgStat.t_grid = (UWORD)(t - t0); t0 = t;

    /* L'indice per colonna lo riempie build_cells stessa, dentro il ciclo che
     * scrive la lista: e' li' che (gz,gx) e' noto per costruzione invece che
     * da dedurre. Una volta per stanza, come prima. */
    BgStat.celllist = build_cells();             stage(1);
    t = JAG_HAL_VBL_TICK; BgStat.t_mark = (UWORD)(t - t0); t0 = t;

    BgStat.worklen = decode_bricks();            stage(2);
    t = JAG_HAL_VBL_TICK; BgStat.t_brick = (UWORD)(t - t0);
    return 1;
}

/* La camera in CELLE (StartX/Y/ZCube del fork, CamGX/GY/GZ nostri: la stessa
 * formula, OBJECT.C:597-599 contro scene_walk.c). */
void Bg_Show(WORD cgx, WORD cgy, WORD cgz)
{
    long nvx = -(long)(cgx - cgz) * ISO_X + CAM_CX;
    long nvy = -(long)(cgx + cgz) * ISO_Y + CAM_CY + (long)cgy * ISO_GY;
    ULONG t0;

    Scene.CamGX = cgx;
    Scene.CamGY = cgy;
    Scene.CamGZ = cgz;

    /* UN PASSO DI CAMERA E' UN FONDO RIFATTO, come nell'originale: AffScene
     * (TRUE) fa Cls + AffGrille, e la port GBA misura lo stesso (4,4 fps sui
     * quadri di scatto). Lo scorrimento col Blitter (Bg_ScrollTo) e' una
     * ottimizzazione NOSTRA che S42 aveva lasciato non verificata, e col fork
     * - che muove la camera davvero - ha mostrato attori a righe, frammenti di
     * muro e fasce vuote. Torna quando lo si mette a punto, nel lavoro sulle
     * prestazioni; fino ad allora si ridisegna, che e' corretto per costruzione.
     * Anche a camera ferma: AffScene(TRUE) arriva pure dopo le finestre modali
     * (dialoghi, menu), che hanno disegnato sopra il fondo, e l'originale li'
     * ridisegna sempre. */

    BgStat.camgx = cgx;
    BgStat.camgz = cgz;
    BgStat.vx = (WORD)nvx;
    BgStat.vy = (WORD)nvy;

    t0 = JAG_HAL_VBL_TICK;
    render(BgStat.vx, BgStat.vy);   /* l'azzeramento cancella i quadratini */
    BgStat.t_render = (UWORD)(JAG_HAL_VBL_TICK - t0);

    Bg_Measure();
    bg_shown = 1;

    /* Il fondo c'e': BG smette di dire "sto caricando". Vedi JAG_BG_REG. */
    HW_W(JAG_BG_REG) = 0x0000;
}


/* Ridisegna il solo fondo, senza rifare griglia e brick. Serve a cancellare la
 * diagnostica sovrapposta quando si vuole uno screenshot della scena e basta:
 * il confronto contro il PC DOS si fa a occhio su immagini pulite
 * ([[feedback_visual_over_metric]]), e sedici righe esadecimali sopra la scena
 * lo rendono impossibile. Costa i soli 756 blit. */
void Bg_Redraw(void)
{
    render(BgStat.vx, BgStat.vy);
}


/* ---------------------------------------------------------------------------
 * IL RETTANGOLO INVECE DELLA STANZA (S41).
 *
 * `AddPhysBox` + `ClsBoxes`, ma con il fondo RIFATTO dalle tessere invece che
 * copiato da una pagina pulita: quella pagina sarebbe 370 KB, e la mappa non li
 * ha (BRICK_WORK ne vuole gia' 589 su 576 nelle stanze peggiori). Le tessere
 * decodificate invece sono gia' li' e residenti per tutta la stanza.
 *
 * UNA LISTA SOLA, non due. Il gioco ne ha due perche' ha due pagine fisiche e
 * ognuna si sporca per conto suo (FLIPBOX.C, FlipOptList); qui il framebuffer
 * e' uno, quindi la lista che si trova all'inizio del quadro E' quella del
 * disegno precedente. Le due liste sarebbero un difetto, non una fedelta':
 * ripristinerebbero due volte e sempre con un quadro di ritardo.
 *
 * IL RIFIUTO GREZZO PRIMA DEL RITAGLIO, e non e' prematuro: la lista delle
 * celle ha 8781 voci e i rettangoli sono qualche unita'. Senza, ogni quadro
 * chiamerebbe blit_cell trentacinquemila volte per farla tornare indietro
 * trentaquattromilanovecento - e sul 68000 quella e' la spesa, non i blit. Il
 * confronto e' sulla UNIONE dei rettangoli, allargata dell'ingombro massimo di
 * una tessera (BgStat.spanx/spany, misurato sui brick veri della stanza).
 * ------------------------------------------------------------------------- */
/* QUANDO si cancella, ed e' la differenza fra "si vede l'animazione" e "si vede
 * un buco".
 *
 * Il primo tentativo cancellava in testa al quadro, e il buco durava quasi
 * tutto il quadro: i pixel dell'attore li scrive la GPU alla FINE (BT_GPU e' il
 * 5% del tempo, il resto e' posa, proiezione, ordine e bordi). Sullo schermo
 * l'attore c'era per un ventesimo del tempo.
 *
 * Adesso la cancellazione e' ARMATA in testa e SCATTA alla prima scrittura sul
 * framebuffer della passata - il lancio della GPU, uno sprite, o le tessere
 * davanti a un attore. Il calcolo intero avviene con l'attore ancora a schermo,
 * e il buco si riduce al riempimento vero.
 *
 * ED E' QUI CHE SERVONO DAVVERO LE DUE LISTE del gioco (FLIPBOX.C, FlipOptList),
 * che a S41 avevo scartato: se la cancellazione scatta DENTRO la passata, la
 * lista che si sta riempendo e quella da cancellare esistono nello stesso
 * momento. Non e' la doppia pagina a chiederle - e' la cancellazione differita.
 * ------------------------------------------------------------------------- */
#define BG_MAX_BOXES 48

typedef struct { WORD x0, y0, x1, y1; } T_BOX;

static T_BOX boxlist[BG_MAX_BOXES];      /* quel che la passata sta sporcando */
static UWORD nbox;
static UWORD boxover;
static WORD  ux0, uy0, ux1, uy1;         /* unione dei rettangoli             */

static T_BOX pendlist[BG_MAX_BOXES];     /* quel che la passata PRIMA ha sporcato */
static UWORD npend;
static UWORD pendover;
static UWORD pendarm;                    /* c'e' una cancellazione da fare    */
static WORD  px0, py0, px1, py1;

/* ---------------------------------------------------------------------------
 * IL FONDO DENTRO UNA LISTA DI RETTANGOLI - il cuore del ripristino di S41, ora
 * anche della banda che scopre uno scorrimento di camera.
 *
 * Era il corpo di `Bg_ClsPending`. Estrarlo non cambia una riga di quel che fa:
 * cambia che adesso ha DUE chiamanti, e i due devono per forza produrre lo
 * stesso fondo - se divergessero, un pezzo di stanza sarebbe disegnato in un
 * ordine dopo uno scorrimento e in un altro dopo un attore.
 *
 * (bx0..by1) e' il riquadro che RACCHIUDE la lista: serve solo a saltare i
 * blocchi dell'indice in un colpo. Il ritaglio vero e' rettangolo per
 * rettangolo, qui sotto.
 * ------------------------------------------------------------------------- */
static UWORD render_rects(const T_BOX *list, UWORD count,
                          WORD bx0, WORD by0, WORD bx1, WORD by1)
{
    long vx = BgStat.vx, vy = BgStat.vy;
    long spanx = BgStat.spanx, spany = BgStat.spany;
    UWORD cells = 0;
    T_CBAND b;
    UWORD k0, k1;
    int i;

    if (!count)
        return 0;

    /* Colonne fuori, celle dentro: l'ordine del pittore resta quello e il 99%
     * delle celle non viene nemmeno guardato.
     *
     * `ymin` e' 0, non c'e' un filtro di profondita' qui: la banda in Y e'
     * quindi piu' larga che per M3d. E il riquadro puo' essere GRANDE - la
     * banda di uno scorrimento di camera vale anche il 70-80% dello schermo -
     * che e' il caso in cui l'indice per colonna rischiava di perdere, perche'
     * ci finiscono dentro quasi tutte le colonne. Misurato invece che dedotto
     * (tools/m3d_scan.py --rects): a schermo INTERO sono 917 celle percorse
     * contro 3597, e i passi in piu' sono due letture d'array, non un confronto
     * di riquadro. Vince anche li', quindi niente soglia da tarare. */
    if (!col_open(&b, vx, vy, bx0, by0, bx1, by1, 0))
        return 0;

    while (col_next(&b, &k0, &k1)) {
        UWORD k;

        for (k = k0; k < k1; k++) {
            long sx = (long)celllist[k].px + vx;
            long sy = (long)celllist[k].py + vy;
            T_CPREP p;

            if (sx + spanx <= bx0 || sx > bx1)
                continue;
            if (sy + spany <= by0 || sy > by1)
                continue;

            /* La meta' che dipende dalla cella, UNA volta: e' l'unico punto
             * del motore in cui la stessa cella incontra piu' rettangoli. */
            if (!cell_prep(&p, &celllist[k], vx, vy))
                continue;

            /* Le celle vengono in ordine di pittore e il ciclo dei rettangoli
             * sta DENTRO: per ogni rettangolo l'ordine resta quello, che e' la
             * ragione per cui quel che esce e' identico al fondo intero.
             * Invertire i due cicli darebbe lo stesso numero di blit e
             * l'ordine sbagliato. */
            for (i = 0; i < (int)count; i++)
                cells += (UWORD)cell_emit(&p, list[i].x0, list[i].y0,
                                          list[i].x1, list[i].y1);
        }
    }
    return cells;
}

/* ---------------------------------------------------------------------------
 * I rettangoli sporchi traslati insieme al fondo (S42, vedi Bg_ScrollTo).
 *
 * IL TAGLIO ALLO SCHERMO NON E' PRUDENZA: un rettangolo che esce dal
 * framebuffer diventa ROVESCIATO (x0 > x1) una volta ritagliato, e `blit_cell`
 * un rettangolo rovesciato non lo rifiuta - lo interpreta, e scrive fuori dal
 * fondo. Chi resta fuori del tutto si toglie dalla lista compattandola, invece
 * di lasciarci un rettangolo vuoto che i cicli percorrerebbero comunque.
 * ------------------------------------------------------------------------- */
static UWORD shift_clip(T_BOX *list, UWORD count, WORD dx, WORD dy)
{
    UWORD i, out = 0;

    for (i = 0; i < count; i++) {
        long x0 = list[i].x0 + dx, x1 = list[i].x1 + dx;
        long y0 = list[i].y0 + dy, y1 = list[i].y1 + dy;

        if (x0 < 0) x0 = 0;
        if (y0 < 0) y0 = 0;
        if (x1 > JAG_BG_W - 1) x1 = JAG_BG_W - 1;
        if (y1 > JAG_BG_H - 1) y1 = JAG_BG_H - 1;
        if (x0 > x1 || y0 > y1)
            continue;                    /* uscito di schermo: non esiste piu' */

        list[out].x0 = (WORD)x0; list[out].y0 = (WORD)y0;
        list[out].x1 = (WORD)x1; list[out].y1 = (WORD)y1;
        out++;
    }
    return out;
}

static void shift_clip_union(WORD *ax0, WORD *ay0, WORD *ax1, WORD *ay1,
                             WORD dx, WORD dy)
{
    long x0 = *ax0 + dx, y0 = *ay0 + dy;
    long x1 = *ax1 + dx, y1 = *ay1 + dy;

    if (x0 < 0) x0 = 0;
    if (y0 < 0) y0 = 0;
    if (x1 > JAG_BG_W - 1) x1 = JAG_BG_W - 1;
    if (y1 > JAG_BG_H - 1) y1 = JAG_BG_H - 1;

    *ax0 = (WORD)x0; *ay0 = (WORD)y0;
    *ax1 = (WORD)x1; *ay1 = (WORD)y1;
}

static void Bg_ShiftBoxes(WORD dx, WORD dy)
{
    nbox  = shift_clip(boxlist, nbox, dx, dy);
    npend = shift_clip(pendlist, npend, dx, dy);
    shift_clip_union(&ux0, &uy0, &ux1, &uy1, dx, dy);
    shift_clip_union(&px0, &py0, &px1, &py1, dx, dy);
}

/* ---------------------------------------------------------------------------
 * IL FONDO E' STATO SPORCATO QUI? (S42)
 *
 * Risponde a chi disegna qualcosa che NON e' cambiato - una porta - e deve
 * sapere se ridisegnarsi comunque perche' qualcun altro gli e' passato sopra.
 * Guarda due liste, e servono tutte e due:
 *
 *   - `pendlist`, cioe' quel che sta per tornare fondo in questa passata: chi
 *     la tocca perde i propri pixel e va rifatto;
 *   - `boxlist`, cioe' quel che gli attori GIA' disegnati in questa passata
 *     hanno sporcato: e' il caso dell'attore che passa davanti alla porta.
 *
 * `damage_all` copre il caso in cui il fondo intero e' cambiato sotto i piedi -
 * stanza nuova, ridisegno, scorrimento di camera. Li' non c'e' niente da
 * confrontare: tutto va rifatto una volta.
 * ------------------------------------------------------------------------- */
static UWORD damage_all;        /* la passata in corso deve ridisegnare tutti  */

static int box_hits(const T_BOX *b, WORD x0, WORD y0, WORD x1, WORD y1)
{
    return !(x1 < b->x0 || x0 > b->x1 || y1 < b->y0 || y0 > b->y1);
}

int Bg_BoxDamaged(WORD x0, WORD y0, WORD x1, WORD y1)
{
    UWORD i;

    if (damage_all)
        return 1;
    /* Lista traboccata: si sa solo che l'unione e' sporca, e basta a decidere. */
    if (pendover && !(x1 < px0 || x0 > px1 || y1 < py0 || y0 > py1))
        return 1;
    if (boxover && !(x1 < ux0 || x0 > ux1 || y1 < uy0 || y0 > uy1))
        return 1;

    for (i = 0; i < npend; i++)
        if (box_hits(&pendlist[i], x0, y0, x1, y1))
            return 1;
    for (i = 0; i < nbox; i++)
        if (box_hits(&boxlist[i], x0, y0, x1, y1))
            return 1;
    return 0;
}

void Bg_BoxReset(void)
{
    UWORD i;

    /* Il fondo cambiato vale per UNA passata: si consuma qui, in testa a
     * quella che deve rifare tutto. */
    damage_all = bg_full;
    bg_full = 0;

    /* La lista di questa passata diventa quella da cancellare. Non si cancella
     * ancora: si arma. */
    for (i = 0; i < nbox; i++)
        pendlist[i] = boxlist[i];
    npend = nbox;
    pendover = boxover;
    px0 = ux0; py0 = uy0; px1 = ux1; py1 = uy1;
    pendarm = 1;

    nbox = 0;
    boxover = 0;
}

void Bg_AddBox(WORD x0, WORD y0, WORD x1, WORD y1)
{
    T_BOX *b;

    /* Il rettangolo vuoto non si accoda: e' lo stesso test `ClipXmin <=
     * ClipXmax` con cui il gioco decide se l'attore ha scritto qualcosa. */
    if (x0 > x1 || y0 > y1)
        return;

    /* L'UNIONE SI AGGIORNA PRIMA del test di pienezza, e vale anche per i
     * rettangoli rifiutati: e' quel che rende il ripiego sicuro. Se la lista si
     * riempie, l'unione copre comunque TUTTO quel che e' stato sporcato,
     * quindi ripristinarla e' corretto - solo piu' grossolano. */
    if (nbox == 0 && !boxover) {
        ux0 = x0; uy0 = y0; ux1 = x1; uy1 = y1;
    } else {
        if (x0 < ux0) ux0 = x0;
        if (y0 < uy0) uy0 = y0;
        if (x1 > ux1) ux1 = x1;
        if (y1 > uy1) uy1 = y1;
    }

    if (nbox >= BG_MAX_BOXES) {
        /* Non si perde un rettangolo in silenzio: perderlo vorrebbe dire
         * lasciare una scia, cioe' un artefatto visivo, cioe' un difetto.
         *
         * IL RIPIEGO NON E' PIU' `render()`, ed e' una correzione: il fondo
         * intero rifatto QUI vorrebbe dire rifarlo a meta' passata, cioe'
         * cancellare gli attori gia' disegnati e ridisegnare 756 tessere dentro
         * la finestra di misura di un altro stadio. Si ripristina l'UNIONE, che
         * copre tutto per costruzione ed e' limitata allo schermo. */
        boxover++;
        return;
    }

    b = &boxlist[nbox];
    b->x0 = x0; b->y0 = y0; b->x1 = x1; b->y1 = y1;
    nbox++;
}

void Bg_ClsPending(void)
{
    UWORD cells = 0;
    ULONG tc0;

    if (!pendarm)
        return;                          /* gia' fatta in questa passata      */
    pendarm = 0;

    BgStat.boxes = npend;
    BgStat.boxfull = pendover;

    if (npend == 0 && !pendover) {
        BgStat.boxcells = 0;
        return;
    }

    /* IL TEMPO DEL RIPRISTINO SI MISURA A PARTE, e non e' un di piu': questa
     * funzione scatta DENTRO la finestra di un altro stadio - `jobs_flush`
     * (BT_EMIT) o `Bg_DrawOverBrick` (M3d) - a seconda di chi scrive per primo
     * nel framebuffer. Senza scorporarlo, il ripristino si travestirebbe da
     * aritmetica dei bordi, e la misura che deve scegliere la prossima
     * ottimizzazione manderebbe il lavoro sul pezzo sbagliato
     * ([[feedback_spostare_non_e_togliere]]). Chi lo racchiude lo sottrae. */
    tc0 = (ULONG)JAG_HAL_VBL_TICK;

    /* Lista traboccata: si ripristina l'unione invece dei singoli rettangoli.
     * Copre tutto per costruzione (Bg_AddBox aggiorna l'unione anche per quelli
     * rifiutati), costa qualche cella in piu' e non tocca il resto dello
     * schermo. */
    if (pendover) {
        npend = 1;
        pendlist[0].x0 = px0; pendlist[0].y0 = py0;
        pendlist[0].x1 = px1; pendlist[0].y1 = py1;
        pendover = 0;
    }

    cells = render_rects(pendlist, npend, px0, py0, px1, py1);

    BgStat.boxcells = cells;
    npend = 0;
    BgStat.t_cls += (ULONG)JAG_HAL_VBL_TICK - tc0;
}


/* ---------------------------------------------------------------------------
 * LA CAMERA CHE SEGUE L'EROE - PERSO.C:1132-1158, e lo scorrimento che la rende
 * sostenibile.
 *
 * La regola e' quella del gioco, parola per parola: si proietta l'eroe rispetto
 * alla cella di camera e, se il punto esce dal riquadro centrale
 * (80..539 x 80..429 di uno schermo 640x480), la camera si sposta sulla cella
 * dell'eroe PIU' META' dello scarto - cioe' anticipa nella direzione in cui si
 * sta andando invece di limitarsi a centrare. E' il motivo per cui in LBA1 la
 * camera si muove a scatti e poi sta ferma a lungo: dopo un salto l'eroe e'
 * lontano dal bordo del riquadro.
 *
 * Il salto e' voluto: senza il mezzo scarto la camera rincorrerebbe l'eroe a
 * ogni passo, cioe' uno scorrimento a ogni quadro.
 * ------------------------------------------------------------------------- */
void Bg_CameraFollow(void)
{
    const T_OBJET *o = &ListObjet[NUM_PERSO];
    long ax, ay;
    WORD xm, ym, zm;
    WORD ngx, ngy, ngz;

    if (!BgStat.celllist)
        return;                          /* nessuna stanza costruita          */

    /* La proiezione dell'eroe: e' la stessa di body_draw.c, e non e' una copia
     * per comodita' - `BgStat.vx/vy` contengono gia' la camera, quindi questo
     * E' il punto di schermo su cui Twinsen viene disegnato. Confrontarlo col
     * riquadro e' letteralmente il test di upstream. */
    ax = ((((long)o->PosObjX - o->PosObjZ) * ISO_X) >> ISO_SHIFT) + BgStat.vx;
    ay = (((((long)o->PosObjX + o->PosObjZ) * ISO_Y
            - (long)o->PosObjY * ISO_Y_WORLD)) >> ISO_SHIFT) + BgStat.vy;

    if (ax >= 80 && ax <= 539 && ay >= 80 && ay <= 429)
        return;

    xm = (WORD)((o->PosObjX + DEMI_BRICK_XZ) / SIZE_BRICK_XZ);
    ym = (WORD)(o->PosObjY / SIZE_BRICK_Y);
    zm = (WORD)((o->PosObjZ + DEMI_BRICK_XZ) / SIZE_BRICK_XZ);

    ngx = (WORD)(xm + (xm - Scene.CamGX) / 2);
    ngy = ym;
    ngz = (WORD)(zm + (zm - Scene.CamGZ) / 2);

    if (ngx >= CUBE_X) ngx = CUBE_X - 1;
    if (ngz >= CUBE_Z) ngz = CUBE_Z - 1;
    /* Upstream non blocca il basso perche' li' la posizione non ci arriva mai;
     * qui la collisione non e' completa (manca la caduta), e una camera
     * negativa leggerebbe la lista delle celle con uno scostamento sbagliato -
     * cioe' un fondo storto invece di un fondo mancante. */
    if (ngx < 0) ngx = 0;
    if (ngz < 0) ngz = 0;
    if (ngy < 0) ngy = 0;

    if (ngx == Scene.CamGX && ngy == Scene.CamGY && ngz == Scene.CamGZ)
        return;

    Scene.CamGX = ngx;
    Scene.CamGY = ngy;
    Scene.CamGZ = ngz;

    Bg_ScrollTo((long)(-(long)(ngx - ngz) * ISO_X + CAM_CX),
                (long)(-(long)(ngx + ngz) * ISO_Y + CAM_CY
                       + (long)ngy * ISO_GY));
}


/* ---------------------------------------------------------------------------
 * Bg_ScrollTo: la camera nuova, senza rifare il fondo.
 * ------------------------------------------------------------------------- */
void Bg_ScrollTo(long nvx, long nvy)
{
    long dx = nvx - BgStat.vx;
    long dy = nvy - BgStat.vy;
    long adx = dx < 0 ? -dx : dx;
    long ady = dy < 0 ? -dy : dy;
    T_BOX band[2];
    UWORD nb = 0;
    WORD bx0, by0, bx1, by1;
    long y, w, h, sx, sy, dstx, dsty;

    if (!dx && !dy)
        return;

    BgStat.vx = (WORD)nvx;
    BgStat.vy = (WORD)nvy;
    BgStat.camgx = Scene.CamGX;
    BgStat.camgz = Scene.CamGZ;

    /* Tutto lo schermo si e' spostato: nessuna posizione di attore vale piu'
     * quella di prima, nemmeno per chi non si muove. */
    bg_full = 1;

    /* Salto piu' grande dello schermo: non c'e' niente da conservare, e la
     * copia costerebbe senza risparmiare. */
    /* S51: la soglia verticale e' l'altezza VISIBILE (DOS_CLIP_Y1, 480), non
     * JAG_BG_H: vedi le bande qui sotto. */
    if (adx >= JAG_BG_W || ady >= DOS_CLIP_Y1) {
        render(nvx, nvy);
        return;
    }

    /* I RETTANGOLI SPORCHI SI SPOSTANO CON IL FONDO. Sono le impronte degli
     * attori del quadro precedente, e la copia le ha appena traslate insieme a
     * tutto il resto: lasciarli dov'erano vorrebbe dire ripulire il posto
     * sbagliato e lasciare a schermo un pezzo di Twinsen. */
    Bg_ShiftBoxes((WORD)dx, (WORD)dy);

    w = JAG_BG_W - adx;
    h = JAG_BG_H - ady;
    sx   = dx < 0 ? adx : 0;
    dstx = dx < 0 ? 0   : adx;

    /* SCENDENDO SI PARTE DAL BASSO: la riga che si legge non deve essere una
     * riga gia' riscritta. */
    if (dy > 0) {
        for (y = h - 1; y >= 0; y--) {
            sy   = y;
            dsty = y + ady;
            scroll_row(sx, sy, dstx, dsty, w);
        }
    } else {
        for (y = 0; y < h; y++) {
            sy   = y + ady;
            dsty = y;
            scroll_row(sx, sy, dstx, dsty, w);
        }
    }

    /* Le bande scoperte, con la camera NUOVA. Al massimo due: una verticale e
     * una orizzontale. Si sovrappongono nell'angolo, e ridisegnare due volte un
     * angolo e' corretto (stesse tessere, stesso ordine) - separarle
     * costerebbe piu' codice di quel che risparmia. */
    if (dx > 0) {
        band[nb].x0 = 0; band[nb].y0 = 0;
        band[nb].x1 = (WORD)(dx - 1); band[nb].y1 = JAG_BG_H - 1;
        nb++;
    } else if (dx < 0) {
        band[nb].x0 = (WORD)(JAG_BG_W + dx); band[nb].y0 = 0;
        band[nb].x1 = JAG_BG_W - 1; band[nb].y1 = JAG_BG_H - 1;
        nb++;
    }
    /* S51, LA BANDA DI SOTTO PARTE DA 480, NON DA 592. Solo le prime
     * DOS_CLIP_Y1 righe del fondo sono un'immagine intera: le 112 sotto hanno
     * soltanto le code delle tessere ancorate in fondo (render taglia a 480).
     * Salendo, la copia porta quelle righe nel visibile; la banda contata dal
     * fondo del buffer le lasciava li', e a ogni scatto successivo la
     * spazzatura risaliva verso il centro dello schermo. Non si vedeva prima
     * del fork perche' la camera della cella non scorreva mai (S42: "camera
     * NON verificata"). Si ridisegna da 480+dy fino in fondo al buffer, cosi'
     * anche le righe sotto il visibile tornano coerenti per il prossimo passo. */
    if (dy > 0) {
        band[nb].x0 = 0; band[nb].y0 = 0;
        band[nb].x1 = JAG_BG_W - 1; band[nb].y1 = (WORD)(dy - 1);
        nb++;
    } else if (dy < 0) {
        band[nb].x0 = 0; band[nb].y0 = (WORD)(DOS_CLIP_Y1 + dy);
        band[nb].x1 = JAG_BG_W - 1; band[nb].y1 = JAG_BG_H - 1;
        nb++;
    }

    bx0 = band[0].x0; by0 = band[0].y0;
    bx1 = band[0].x1; by1 = band[0].y1;
    if (nb > 1) {
        if (band[1].x0 < bx0) bx0 = band[1].x0;
        if (band[1].y0 < by0) by0 = band[1].y0;
        if (band[1].x1 > bx1) bx1 = band[1].x1;
        if (band[1].y1 > by1) by1 = band[1].y1;
    }

    render_rects(band, nb, bx0, by0, bx1, by1);
}
