/* front_menu.c - la progressione d'avvio: logo Adeline -> menu -> partita.
 *
 * COSA C'E' E COSA NON C'E'
 * -------------------------
 * LBA1 su PC accende con Adeline, Electronic Arts, il titolo RELENTLESS, il
 * filmato FLA dell'intro e poi il menu. Su questa cartuccia la progressione e'
 * decisa piu' corta: boot -> logo Adeline -> menu -> la stanza. Gli altri
 * marchi e il filmato non ci sono, ed e' una scelta, non una mancanza: sono
 * 250 KB di schermate ferme e un decodificatore video, contro un margine di
 * cartuccia che dopo queste due schermate e' di diciotto KB.
 *
 * IL MENU E' QUELLO VERO, misurato contro l'originale e non ricostruito a
 * occhio: geometria, colori e testi vengono tutti da SOURCES/GAMEMENU.C
 * attraverso engine_c/menu_expect.h, che tools/menu_ref.py genera insieme al
 * PNG di riferimento. Quattro voci, la prima attiva:
 *
 *   New game             carica la scena e comincia
 *   Continue saved game  inerte: non ci sono ancora salvataggi
 *   Option menu          inerte: le opzioni arrivano con l'audio
 *   Quit                 inerte: e' una cartuccia, non c'e' un DOS a cui
 *                        tornare
 *
 * Restano a schermo perche' la struttura autentica e' quella, e perche' una
 * voce che c'e' e non fa niente e' onesta quanto una voce assente ma dice
 * anche dove andranno le prossime.
 *
 * DOVE VIVONO LE COSE, E PERCHE' LI'
 * ----------------------------------
 * Le due schermate sono 640x480 a 8 bit, cioe' 307200 byte l'una: piu'
 * dell'arena per-cubo intera. Non passano quindi da HQR_Get - che decide lui
 * dove mettere le cose - ma da HQR_Expand, che le srotola dove diciamo noi:
 *
 *   BG_FB        il framebuffer: le 480 righe visibili partono dalla riga 0,
 *                quindi una schermata ci va dritta senza traslazioni
 *   BRICK_WORK   l'area di lavoro del front-end. Sono i 576 KB dove finiranno
 *                i brick decodificati della stanza, e finche' una stanza non
 *                esiste non c'e' niente da rispettare: il front-end finisce
 *                PRIMA della prima Bg_Build. Ci tiene il fondo intatto del
 *                menu, cosi' muovere la selezione ridisegna due riquadri
 *                copiando da li' invece di ridecomprimere 307 KB (che sul
 *                68000 sono un quarto di secondo per ogni pressione).
 *
 * IL LOGO NON E' UNO SLOT DI RESS. Il suo 92% e' bianco uniforme; ritagliato
 * del bordo costa 17,7 KB invece di 41,6 e sta nell'archivio FRONT nel formato
 * 'FSCR' (tools/front_screen.py). Qui lo si rimette a schermo: riempimento
 * pieno, poi le righe del ritaglio al loro posto.
 */
#include "hal.h"
#include "jag_hw.h"
#include "le.h"
#include "menu_expect.h"

#define SCR_W       JAG_BG_W            /* il framebuffer e' largo 640      */
#define SCR_H       480                 /* ...e ne mostra 480 dalla riga 0  */
#define SCR_BYTES   ((ULONG)SCR_W * SCR_H)

/* Le due basi sono SOSTITUIBILI, come in body_draw.c: engine_c/host_front.c
 * compila QUESTO file su x86 con i byte veri della cartuccia e ne confronta le
 * sonde con menu_expect.h. Quel banco separa due domande - "il disegno e' il
 * disegno giusto?" e "il Jaguar lo esegue come previsto?" - che altrimenti
 * arriverebbero insieme sotto forma di una maschera storta. */
#ifndef FRONT_FB_BASE
#define FRONT_FB_BASE   JAG_BG_FB
#endif
#ifndef FRONT_WORK_BASE
#define FRONT_WORK_BASE JAG_BRICK_WORK
#endif
#ifndef FRONT_WORK_SIZE
#define FRONT_WORK_SIZE JAG_BRICK_WORK_SIZE
#endif

#define FB          ((UBYTE *)FRONT_FB_BASE)
#define WORK        ((UBYTE *)FRONT_WORK_BASE)

/* Il blob FSCR: intestazione big-endian da 16 byte, poi i pixel del ritaglio.
 * I campi sono a offset multipli di 2 e li leggiamo comunque a byte, quindi
 * niente struct: una struct qui inviterebbe il compilatore a mettere del
 * riempimento che il packer non conosce. */
#define FSCR_HDR    16

/* Quanti passi ha una dissolvenza e quanto dura la posa del logo.
 *
 * I passi sono fissi e la posa e' in SECONDI: il Jaguar fa 60 interrupt al
 * secondo e BigPEmu 30, quindi una posa contata in tick durerebbe il doppio
 * sull'emulatore. La dissolvenza no - li' un passo per tick va bene su
 * entrambi, e mezzo secondo o un quarto sono tutti e due giusti. */
#define FADE_STEPS  16
#define LOGO_HOLD_S 3

enum {
    F_LOGO_IN = 0,
    F_LOGO_HOLD,
    F_LOGO_OUT,
    F_MENU_IN,
    F_MENU,
    F_START_OUT,
    F_DONE
};

static int   state;
static int   fade;                      /* 0 = nero, FADE_STEPS = pieno     */
static ULONG state_t0;                  /* tick d'ingresso nello stato      */
static int   sel;                       /* voce selezionata                 */
static int   started;                   /* Front_Init gia' passata          */

/* La palette in corso, gia' normalizzata a 8 bit: la dissolvenza la riscala a
 * ogni passo e ha bisogno dell'originale, non della CLUT (che e' di sola
 * scrittura e in RGB16). */
static UBYTE pal_now[768];

/* Il font e i testi. Puntatori DENTRO l'arena per-cubo: valgono finche' il
 * front-end vive, e il front-end muore prima della prima Bg_Build - che e'
 * anche chi libera l'arena. */
static const le_byte *font_blob;
static ULONG          font_n;
static const le_byte *text_ids;
static ULONG          text_n;
static const le_byte *text_body;

static const int menu_text_id[MN_NB_ITEMS] = MN_TEXT_IDS;

/* Le sonde, nell'ordine di menu_expect.h. */
static ULONG mn_got[MN_EXPECT_COUNT];
static ULONG mn_mask;

ULONG Front_Mask(void)  { return mn_mask; }
ULONG Front_Got(int i)  { return (i >= 0 && i < MN_EXPECT_COUNT) ? mn_got[i] : 0; }

/* --------------------------------------------------------------------------
 * Palette e dissolvenza
 * ----------------------------------------------------------------------- */
/* Sei bit o otto? Lo decide il massimo, come in Bg_LoadPalette e in
 * render_slide.normalize_palette: deciderlo a priori e' il genere di
 * assunzione che produce una schermata quattro volte troppo scura senza dire
 * niente. */
static void pal_load(int ress_slot)
{
    const UBYTE *p = (const UBYTE *)HQR_Get(HQR_RESS, ress_slot);
    int i, six = 1;

    if (!p || HQR_Size(HQR_RESS, ress_slot) < 768) {
        for (i = 0; i < 768; i++)
            pal_now[i] = 0;
        return;
    }
    for (i = 0; i < 768; i++)
        if (p[i] > 63) {
            six = 0;
            break;
        }
    for (i = 0; i < 768; i++) {
        int raw = p[i];
        pal_now[i] = (UBYTE)(six ? ((raw << 2) | (raw >> 4)) : raw);
    }
}

/* La CLUT al livello dato. Il livello scala le componenti PRIMA di stringerle
 * a 5 e 6 bit: scalarle dopo darebbe una rampa a scalini, perche' a meta'
 * dissolvenza i cinque bit di rosso ne diventerebbero quattro. */
static void pal_apply(int level)
{
    int i;

    for (i = 0; i < 256; i++) {
        int r = pal_now[i * 3];
        int g = pal_now[i * 3 + 1];
        int b = pal_now[i * 3 + 2];

        if (level < FADE_STEPS) {
            r = (r * level) / FADE_STEPS;
            g = (g * level) / FADE_STEPS;
            b = (b * level) / FADE_STEPS;
        }
        HW_W(JAG_CLUT + i * 2) = (UWORD)(((r >> 3) << 11) | ((b >> 3) << 6)
                                         | (g >> 2));
    }
}

/* --------------------------------------------------------------------------
 * Schermo
 * ----------------------------------------------------------------------- */
static void fb_fill(UBYTE colour)
{
    ULONG *p = (ULONG *)FB;
    ULONG  v = ((ULONG)colour << 24) | ((ULONG)colour << 16)
               | ((ULONG)colour << 8) | colour;
    ULONG  n = SCR_BYTES / 4;

    while (n--)
        *p++ = v;
}

static void fb_copy(const UBYTE *src)
{
    const ULONG *s = (const ULONG *)src;
    ULONG *d = (ULONG *)FB;
    ULONG  n = SCR_BYTES / 4;

    while (n--)
        *d++ = *s++;
}

/* Le due somme del framebuffer visibile, le stesse che calcola
 * tools/menu_ref.py: contenuto e ordine.
 *
 * I long si leggono BIG endian, che sul 68000 e' la lettura nativa e sul banco
 * x86 no: da qui la macro, che il banco sostituisce. Senza, il banco darebbe
 * due somme diverse su pixel identici - il caso peggiore, perche' sembrerebbe
 * un difetto del disegno. */
#ifndef FRONT_RD32BE
#define FRONT_RD32BE(p) (*(const ULONG *)(p))
#endif

static void fb_sums(ULONG *s1, ULONG *s2)
{
    const UBYTE *p = FB;
    ULONG a = 0, b = 0, n = SCR_BYTES / 4;

    while (n--) {
        a += FRONT_RD32BE(p);
        p += 4;
        b += a;
    }
    *s1 = a;
    *s2 = b;
}

static void box_fill(int x0, int y0, int x1, int y1, UBYTE colour)
{
    UBYTE *row = FB + (ULONG)y0 * SCR_W + x0;
    int w = x1 - x0 + 1, h = y1 - y0 + 1, j;

    for (j = 0; j < h; j++) {
        int i;
        for (i = 0; i < w; i++)
            row[i] = colour;
        row += SCR_W;
    }
}

/* ShadeBox di SOURCES/FUNC.C: dentro la sua banda di 16 il colore scende di
 * `dec` e si ferma al fondo della banda. La palette di LBA1 e' organizzata in
 * bande semantiche da 16, quindi questo scurisce senza cambiare tinta. */
static void box_shade(int x0, int y0, int x1, int y1, int dec)
{
    UBYTE *row = FB + (ULONG)y0 * SCR_W + x0;
    int w = x1 - x0 + 1, h = y1 - y0 + 1, j;

    for (j = 0; j < h; j++) {
        int i;
        for (i = 0; i < w; i++) {
            int v = row[i];
            int lo = v & 0x0F, hi = v & 0xF0;
            row[i] = (UBYTE)((lo >= dec) ? (hi + lo - dec) : hi);
        }
        row += SCR_W;
    }
}

/* DrawCadre: due lati chiari e due scuri, il rilievo del riquadro. */
static void box_cadre(int x0, int y0, int x1, int y1)
{
    box_fill(x0, y0, x1, y0, MN_COUL_CADRE_HI);
    box_fill(x0, y0, x0, y1, MN_COUL_CADRE_HI);
    box_fill(x1, y0 + 1, x1, y1, MN_COUL_CADRE_LO);
    box_fill(x0 + 1, y1, x1, y1, MN_COUL_CADRE_LO);
}

/* Il riquadro della voce n, e il suo centro verticale. */
static void item_rect(int n, int *x0, int *y0, int *x1, int *y1, int *yc)
{
    int y = MN_Y_CENTER
            - (MN_ITEM_H * MN_NB_ITEMS + (MN_NB_ITEMS - 1) * MN_ITEM_SPACE) / 2;

    y += n * (MN_ITEM_H + MN_ITEM_SPACE);
    *x0 = MN_CX - MN_ITEM_W / 2;
    *x1 = MN_CX + MN_ITEM_W / 2;
    *y0 = y - MN_ITEM_H / 2;
    *y1 = y + MN_ITEM_H / 2;
    *yc = y;
}

/* --------------------------------------------------------------------------
 * Font e testi
 * ----------------------------------------------------------------------- */
static void font_load(void)
{
    font_blob = (const le_byte *)HQR_Get(HQR_RESS, MN_RESS_FONT);
    font_n = 0;
    if (font_blob)
        font_n = LE32u(font_blob) / 4;      /* il banco comincia con i suoi
                                               offset: il primo dice quanti */
}

static const le_byte *glyph_of(int c)
{
    ULONG off;

    if (!font_blob || (ULONG)c >= font_n)
        return 0;
    off = LE32u(font_blob + (ULONG)c * 4);
    return font_blob + off;
}

static int text_width(const char *s)
{
    int w = 0;

    while (*s) {
        int c = (UBYTE)*s++;
        if (c == 32) {
            w += MN_INTER_SPACE;
        } else {
            const le_byte *g = glyph_of(c);
            if (g)
                w += g[0] + MN_INTER_LEAVE;
        }
    }
    return w;
}

/* Un glifo. Il formato e' quello di LIB_SVGA/MASK_A.ASM: [DX, DY, HotX, HotY],
 * poi per ogni riga il numero di blocchi e i blocchi stessi, alternati
 * "salta N" e "scrivi N". I byte scritti sono tutti dello stesso colore - e'
 * un font mascherato, non un'immagine: il disegno sta nella forma, non nei
 * valori. Torna i pixel scritti. */
static int glyph_draw(int x, int y, int c, UBYTE colour)
{
    const le_byte *g = glyph_of(c);
    int dx, dy, r, n = 0;
    const le_byte *p;

    if (!g)
        return 0;
    dx = g[0];
    dy = g[1];
    x += g[2];
    y += g[3];
    p = g + 4;

    for (r = 0; r < dy; r++, y++) {
        int nb = *p++;
        int gx = x, b = 0;

        while (b < nb) {
            gx += *p++;                         /* salta */
            b++;
            if (b >= nb)
                break;
            {
                int run = *p++;
                b++;
                if (y >= 0 && y < SCR_H) {
                    UBYTE *o = FB + (ULONG)y * SCR_W + gx;
                    int k;
                    for (k = 0; k < run; k++)
                        if (gx + k >= 0 && gx + k < SCR_W) {
                            o[k] = colour;
                            n++;
                        }
                }
                gx += run;
            }
        }
    }
    (void)dx;
    return n;
}

static int text_draw(int x, int y, const char *s, UBYTE colour)
{
    int n = 0;

    while (*s) {
        int c = (UBYTE)*s++;
        if (c == 32) {
            x += MN_INTER_SPACE;
            continue;
        }
        n += glyph_draw(x, y, c, colour);
        {
            const le_byte *g = glyph_of(c);
            if (g)
                x += g[0] + MN_INTER_LEAVE;
        }
    }
    return n;
}

/* Il dialogo 0 di TEXT.HQR, che e' la sezione di sistema: slot 0 la tabella
 * degli identificatori, slot 1 gli offset seguiti dalle stringhe. E' la
 * struttura che legge GetMultiText, e in cartuccia ci sono quei due slot soli
 * (tools/hqr_pack.py, TEXT_KEEP). */
static void text_load(void)
{
    text_ids  = (const le_byte *)HQR_Get(HQR_TEXT, 0);
    text_body = (const le_byte *)HQR_Get(HQR_TEXT, 1);
    text_n    = text_ids ? HQR_Size(HQR_TEXT, 0) / 2 : 0;
}

static const char *text_get(int id)
{
    ULONG i;

    if (!text_ids || !text_body)
        return "";
    for (i = 0; i < text_n; i++)
        if ((int)LE16u(text_ids + i * 2) == id)
            return (const char *)(text_body + LE16u(text_body + i * 2));
    return "";
}

/* --------------------------------------------------------------------------
 * IL FUOCO DELLA VOCE SELEZIONATA (S50)
 *
 * SOURCES/FIRE.C, trascritto alla lettera da tools/fire_ref.py - che a sua
 * volta e' stato GUARDATO, non dedotto: un effetto animato non si verifica con
 * una somma di controllo ([[feedback_visual_over_metric]]), e i PNG di S48
 * hanno gia' risposto alle due domande che il codice del fork poneva.
 *
 * DUE COSE CHE SEMBRANO DIFETTI E NON LO SONO
 *
 * 1. l'aritmetica dell'alimentazione e' a OTTO BIT e va sotto zero apposta:
 *    `11 - al` con al fra 12 e 15 ricasca a 252..255, cioe' quasi bianco. E'
 *    quello il combustibile; senza il ritorno a capo l'alimentazione
 *    spegnerebbe invece di accendere;
 * 2. la riga di alimentazione scrive su `img2`, che il giro dopo viene
 *    ricalcolato per intero prima di essere riletto: **in questa forma non fa
 *    niente**, e il fuoco vive dei soli punti caldi di `fire_spark`. Sembra un
 *    difetto ed e' la forma che TIENE - misurata stabile su 400 quadri, media
 *    7-8 e mai satura, mentre "correggendola" (alimentare `img` dopo la copia)
 *    satura di bianco in sessanta quadri. Si tiene alla lettera, dietro un
 *    interruttore, perche' il giorno che qualcuno la "aggiusta" deve trovare
 *    scritto perche' non va aggiustata.
 *
 * Il generatore pseudocasuale NON deve coincidere con quello di LBA1 - i punti
 * caldi sono rumore per costruzione - ma deve essere lo STESSO di fire_ref.py,
 * o il PNG di riferimento e lo schermo del Jaguar mostrerebbero due fuochi
 * diversi e non si saprebbe se e' un difetto.
 * ----------------------------------------------------------------------- */
#define FIRE_W        320
#define FIRE_H        50
#define FIRE_ROWS     25                /* le righe che finiscono a schermo  */
#define FIRE_SRC_ROW  5                 /* la prima riga disegnata           */
#define FIRE_SIM_LO   321L
#define FIRE_SIM_HI   (48L * FIRE_W - 1)
#define FIRE_FEED_LO  (39L * FIRE_W)
#define FIRE_FEED_HI  (44L * FIRE_W)
#define FIRE_RISE     (48L * FIRE_W)    /* quante celle salgono di una riga  */
#define MN_RESS_PLASMA 51               /* RESS_INIT_PLASMA, il seme         */

/* Alla lettera del fork. 1 alimenterebbe `img` dopo la copia: vedi sopra,
 * satura in sessanta quadri. */
#ifndef FIRE_FEED_ON_IMG
#define FIRE_FEED_ON_IMG 0
#endif

/* LA SIMULAZIONE NON GIRA SUL JAGUAR, e questa riga e' una lezione pagata.
 *
 * S48 aveva gia' misurato che il 68000 non ce la fa e aveva concluso "serve un
 * kernel GPU". S50 ha scritto il modello in C - che SERVE, perche' e' l'oracolo
 * che il kernel trascrivera' e l'unico modo di provare la simulazione bit per
 * bit contro tools/fire_ref.py - ma poi l'ha acceso anche sul bersaglio. Sul
 * ferro il menu e' andato a **0,3-0,4 fps**, cioe' inservibile: non si riusciva
 * nemmeno a entrare in partita.
 *
 * La stima era 5 fps. Sbagliata di QUATTORDICI VOLTE, e il modo in cui era
 * sbagliata conta piu' del numero: contava le istruzioni e le moltiplicava per
 * un costo medio per istruzione. Su questa macchina non funziona cosi' - il
 * codice sta in CARTUCCIA e i dati passano un byte alla volta per la DRAM
 * mentre l'Object Processor si porta via il bus per 640x480. Il collo non e'
 * l'unita' aritmetica, e' il bus. Contare istruzioni per prevedere un tempo qui
 * NON e' un metodo ([[feedback_bigpemu_dsp_non_taratura]] vale lo stesso).
 *
 * A zero resta il DISEGNO del buffer - una passata sola quando il menu compare,
 * ~27.000 pixel - quindi la voce selezionata mostra il seme fermo invece di un
 * rettangolo piatto. Non e' un ripiego a caso: e' quel che verifica SUL FERRO
 * tutto il resto della catena (RESS 51 in cartuccia, il raddoppio, la banda di
 * colori, il ritaglio al riquadro), cosi' il giorno del kernel resta da provare
 * solo il kernel. E le attese di menu_expect.h sono gia' calcolate a ZERO passi
 * di simulazione, quindi non si muove niente.
 *
 * Il banco su PC la accende (host_front.c), ed e' li' che la simulazione si
 * verifica. */
#ifndef FRONT_FIRE_ANIM
#define FRONT_FIRE_ANIM 0
#endif

/* I due buffer stanno DENTRO WORK, dopo la copia intatta del fondo che
 * `item_restore` rilegge: 640x592 byte sono gia' impegnati, e BRICK_WORK ne ha
 * 576 KB, quindi i 32 KB del fuoco non tolgono niente a nessuno. Durante il
 * menu i brick non esistono ancora - Bg_Build arriva dopo. */
#define FIRE_IMG    (WORK + (ULONG)JAG_BG_W * JAG_BG_H)
#define FIRE_IMG2   (FIRE_IMG + (ULONG)FIRE_W * FIRE_H)

static ULONG fire_seed = 1;
static int   fire_ready;

static UWORD fire_rnd(UWORD n)
{
    /* La maschera a 31 bit rende il risultato indipendente dalla larghezza
     * della parola: (a*b+c) mod 2^31 e' lo stesso preso da 32 bit o da 64,
     * quindi il banco su PC e il 68000 danno la STESSA sequenza. Senza la
     * maschera l'x86-64 non andrebbe a capo dove va il Jaguar. */
    fire_seed = (fire_seed * 1103515245UL + 12345UL) & 0x7FFFFFFFUL;
    return (UWORD)((fire_seed >> 16) % n);
}

static void fire_spark(void)
{
    if (fire_rnd(5) == 0)
        FIRE_IMG[fire_rnd(FIRE_W * 10) + 20 * FIRE_W] = 255;
}

static void fire_step(void)
{
    UBYTE *img = FIRE_IMG, *img2 = FIRE_IMG2;
    long i;

    /* LE COLONNE SCORREVOLI, e non e' un'approssimazione.
     *
     *     colonna(i) = img[i-320] + img[i] + img[i+320]
     *     otto vicini di i = colonna(i-1) + colonna(i) + colonna(i+1) - img[i]
     *
     * perche' le tre colonne sommano il quadrato 3x3 CENTRO COMPRESO, e il
     * centro si toglie. Scorrendo su i, due colonne su tre sono gia' in mano:
     * per pixel restano 3 letture e 2 somme invece di 8 letture e 7 somme.
     *
     * Vale anche ai bordi di colonna, dove i vicini vanno a capo sulla riga
     * accanto: l'indice e' lineare in tutte e due le forme, quindi il "difetto"
     * dell'originale si conserva identico invece di essere corretto per sbaglio.
     * Il banco lo verifica byte per byte - non e' un ragionamento, e' la sonda
     * del fuoco che confronta media e massimo con tools/fire_ref.py. */
    /* TUTTO A SEDICI BIT, e non e' pignoleria di stile: la somma piu' grande
     * possibile e' 3x3x255 = 2295, e la parola a 16 bit e' la taglia NATIVA del
     * 68000. Scritta con `ULONG` la stessa aritmetica costava 34 istruzioni per
     * pixel, per meta' `moveq #0` e `and.l #65535` messe da gcc a estendere e
     * ritagliare parole che non hanno mai avuto bisogno di 32 bit. E la
     * semantica non cambia di un bit: assegnare a un `UWORD` E' il `& 0xFFFF`
     * che la formula chiede. Il banco lo dimostra invece di prometterlo. */
    {
    const UBYTE *pu = img + FIRE_SIM_LO - 1 - FIRE_W;   /* riga sopra  */
    const UBYTE *pc = img + FIRE_SIM_LO - 1;            /* riga stessa */
    const UBYTE *pd = img + FIRE_SIM_LO - 1 + FIRE_W;   /* riga sotto  */
    UBYTE *q = img2 + FIRE_SIM_LO;
    UWORD c0 = (UWORD)(pu[0] + pc[0] + pd[0]);
    UWORD c1 = (UWORD)(pu[1] + pc[1] + pd[1]);

    for (i = FIRE_SIM_LO; i < FIRE_SIM_HI; i++) {
        UWORD c2 = (UWORD)(pu[2] + pc[2] + pd[2]);
        UWORD ax = (UWORD)(c0 + c1 + c2 - pc[1]);

        c0 = c1;
        c1 = c2;
        pu++; pc++; pd++;

        /* La media, col RESTO parcheggiato in cima alla parola: il test dopo
         * guarda i bit 13 e 14, cioe' due bit del resto. Non e' un modo
         * contorto di dividere per otto - e' quel che decide il decremento.
         *
         * E scritta a SEDICI bit e' una ROTAZIONE: gcc la compila in un
         * `ror.w #3` solo. Il che dice anche da dove viene - l'assembly 386
         * originale faceva `ror ax,3`, e la forma con lo shift e l'or e' la
         * traduzione a mano che l'ha persa per strada. Per il kernel GPU vale
         * la stessa cosa: TOM la rotazione ce l'ha. */
        ax = (UWORD)((ax >> 3) | ((ax & 7) << 13));
        if (!(ax & 0x6500)) {
            if ((ax & 0xFF) || i >= 46L * FIRE_W)
                ax = (UWORD)(ax - 1);       /* il prestito fa 0x00 -> 0xFF */
        }
        *q++ = (UBYTE)ax;
    }
    }

    /* IL FUOCO SALE: la riga r prende la riga r+1 di quel che si e' appena
     * calcolato. E' l'unico posto in cui la fiamma si muove. */
    for (i = 0; i < FIRE_RISE; i++)
        img[i] = img2[i + FIRE_W];

    {
#if FIRE_FEED_ON_IMG
        UBYTE *feed = img;
#else
        UBYTE *feed = img2;
#endif
        for (i = FIRE_FEED_LO; i < FIRE_FEED_HI; i++)
            if (feed[i] <= 15)
                feed[i] = (UBYTE)((11 - feed[i]) & 0xFF);
    }
}

/* Le 25 righe raddoppiate in orizzontale e in verticale, ritagliate al
 * rettangolo della voce. L'originale scrive 640 pixel a partire da x=0 e poi
 * copia sullo schermo il solo rettangolo (CopyBlockPhys); qui si scrive
 * direttamente quel che si vede, quindi il ritaglio va fatto adesso o il fuoco
 * sborderebbe sul fondo del menu. */
static void fire_draw(int x0, int y0, int x1, int y1)
{
    const UBYTE *img = FIRE_IMG;
    UBYTE base = (UBYTE)(MN_COUL_SELECT & 0xF0);
    UBYTE top  = (UBYTE)(base + 15);
    int r;

    for (r = 0; r < FIRE_ROWS; r++) {
        const UBYTE *src = img + (ULONG)(FIRE_SRC_ROW + r) * FIRE_W;
        int half;

        for (half = 0; half < 2; half++) {
            int y = y0 + r * 2 + half;
            UBYTE *d;
            int x;

            if (y < y0 || y > y1 || y < 0 || y >= JAG_BG_H)
                continue;
            d = FB + (ULONG)y * SCR_W;
            for (x = x0; x <= x1; x++) {
                int v = (src[x >> 1] >> 1) + base;
                d[x] = (UBYTE)(v > top ? top : v);
            }
        }
    }
}

/* Il seme e' RESS 51 (RESS_INIT_PLASMA): 16 KB gia' in cartuccia, che e'
 * esattamente 320x50. Se mancasse, il fuoco resta spento invece di disegnare
 * spazzatura - il menu senza fuoco e' un menu, il menu con rumore e' un
 * guasto. */
static void fire_init(void)
{
    const UBYTE *seed = (const UBYTE *)HQR_Get(HQR_RESS, MN_RESS_PLASMA);
    ULONG n = seed ? HQR_Size(HQR_RESS, MN_RESS_PLASMA) : 0;
    ULONG i;

    fire_ready = 0;
    fire_seed = 1;
    if (n < (ULONG)FIRE_W * FIRE_H)
        return;
    for (i = 0; i < (ULONG)FIRE_W * FIRE_H; i++) {
        FIRE_IMG[i]  = seed[i];
        FIRE_IMG2[i] = 0;
    }
    fire_ready = 1;
}


/* --------------------------------------------------------------------------
 * Il menu
 * ----------------------------------------------------------------------- */
/* Rimette il fondo intatto sotto una voce, prendendolo dalla copia in
 * BRICK_WORK. Serve perche' ShadeBox lavora SUL FONDO: applicarlo due volte
 * scurirebbe due volte, e la voce lasciata scorrendo resterebbe piu' scura di
 * quelle mai toccate. */
static void item_restore(int n)
{
    int x0, y0, x1, y1, yc, j;
    UBYTE *d;
    const UBYTE *s;

    item_rect(n, &x0, &y0, &x1, &y1, &yc);
    d = FB + (ULONG)y0 * SCR_W + x0;
    s = WORK + (ULONG)y0 * SCR_W + x0;
    for (j = y0; j <= y1; j++) {
        int i, w = x1 - x0 + 1;
        for (i = 0; i < w; i++)
            d[i] = s[i];
        d += SCR_W;
        s += SCR_W;
    }
}

static int item_draw(int n, int selected)
{
    int x0, y0, x1, y1, yc;
    const char *s = text_get(menu_text_id[n]);

    item_rect(n, &x0, &y0, &x1, &y1, &yc);
    /* La voce selezionata non e' un rettangolo pieno: e' il fuoco. Il
     * riempimento resta come ripiego per il quadro in cui il seme non c'e' -
     * cosi' la voce e' comunque leggibile invece di essere un buco nero. */
    if (selected) {
        if (fire_ready)
            fire_draw(x0, y0, x1, y1);
        else
            box_fill(x0, y0, x1, y1, MN_COUL_SELECT);
    } else
        box_shade(x0, y0, x1, y1, MN_SHADE_DEC);
    box_cadre(x0, y0, x1, y1);
    return text_draw(MN_CX - text_width(s) / 2, yc + MN_TEXT_DY, s,
                     MN_COUL_TEXT);
}

static int menu_draw_all(void)
{
    int n, pix = 0;

    for (n = 0; n < MN_NB_ITEMS; n++)
        pix += item_draw(n, n == sel);
    return pix;
}

static void menu_move(int delta)
{
    int old = sel;

    sel += delta;
    if (sel < 0)
        sel = MN_NB_ITEMS - 1;
    else if (sel >= MN_NB_ITEMS)
        sel = 0;
    if (sel == old)
        return;
    item_restore(old);
    item_draw(old, 0);
    item_restore(sel);
    item_draw(sel, 1);
}

/* Un quadro di fuoco: un passo di simulazione e la voce selezionata rifatta.
 *
 * `item_restore` NON serve: il fuoco riscrive tutto il rettangolo per intero a
 * ogni quadro, quindi non c'e' niente da ripulire sotto - a differenza di
 * `box_shade`, che scurisce quel che trova e applicato due volte scurirebbe
 * due volte. */
static void fire_frame(void)
{
#if FRONT_FIRE_ANIM
    if (!fire_ready)
        return;
    fire_spark();
    fire_step();
    item_draw(sel, 1);
#endif
}

/* --------------------------------------------------------------------------
 * Le due schermate
 * ----------------------------------------------------------------------- */
/* Il logo, dal blob FSCR: riempimento pieno e poi le righe del ritaglio.
 * Riempie ANCHE le 112 righe sotto le 480 visibili? No: il fondo del gioco e'
 * alto 592 ma il front-end tocca solo quel che si vede, e Bg_Build ripassera'
 * comunque tutto. */
static void logo_show(void)
{
    ULONG got = HQR_Expand(HQR_FRONT, MN_FRONT_LOGO, WORK, FRONT_WORK_SIZE);
    const le_byte *b = (const le_byte *)WORK;
    int w, h, x0, y0, pal;
    UBYTE fill;
    int j;

    if (got < FSCR_HDR || b[0] != 'F' || b[1] != 'S' || b[2] != 'C'
        || b[3] != 'R') {
        fb_fill(0);
        return;
    }
    w    = (b[4] << 8) | b[5];
    h    = (b[6] << 8) | b[7];
    x0   = (b[8] << 8) | b[9];
    y0   = (b[10] << 8) | b[11];
    fill = b[12];
    pal  = b[13];

    mn_got[MNX_LOGO_GEOM] = ((ULONG)w << 16) | (UWORD)h;
    mn_got[MNX_LOGO_POS]  = ((ULONG)x0 << 16) | (UWORD)y0;
    mn_got[MNX_LOGO_FILL] = ((ULONG)fill << 8) | (UBYTE)pal;

    fb_fill(fill);
    for (j = 0; j < h; j++) {
        const UBYTE *s = (const UBYTE *)b + FSCR_HDR + (ULONG)j * w;
        UBYTE *d = FB + (ULONG)(y0 + j) * SCR_W + x0;
        int i;
        for (i = 0; i < w; i++)
            d[i] = s[i];
    }
    fb_sums(&mn_got[MNX_LOGO_SUM1], &mn_got[MNX_LOGO_SUM2]);
    pal_load(pal);
}

static void menu_show(void)
{
    int pix, n;
    ULONG tsum = 0;
    int wid[MN_NB_ITEMS];

    /* Il fondo va PRIMA in BRICK_WORK e poi a schermo: la copia intatta serve
     * a ridisegnare le voci quando la selezione si muove. */
    if (HQR_Expand(HQR_RESS, MN_RESS_MENU, WORK, FRONT_WORK_SIZE)
        != SCR_BYTES) {
        fb_fill(0);
        return;
    }
    fb_copy(WORK);
    pal_load(0);

    /* PRIMA di disegnare le voci: e' `item_draw` a chiedere a `fire_ready` se
     * la voce selezionata e' un rettangolo pieno o una fiamma. */
    fire_init();

    sel = 0;
    pix = menu_draw_all();

    for (n = 0; n < MN_NB_ITEMS; n++) {
        const char *s = text_get(menu_text_id[n]);
        const char *q = s;
        wid[n] = text_width(s);
        while (*q)
            tsum += (UBYTE)*q++;
    }
    mn_got[MNX_MENU_TPIX] = (ULONG)pix;
    mn_got[MNX_MENU_TSUM] = tsum;
    mn_got[MNX_MENU_W01]  = ((ULONG)wid[0] << 16) | (UWORD)wid[1];
    mn_got[MNX_MENU_W23]  = ((ULONG)wid[2] << 16) | (UWORD)wid[3];
    fb_sums(&mn_got[MNX_MENU_SUM1], &mn_got[MNX_MENU_SUM2]);
}

static void probes_finish(void)
{
    static const ULONG want[MN_EXPECT_COUNT + 1] = MN_EXPECT_LIST;
    int i;

    mn_mask = 0;
    for (i = 0; i < MN_EXPECT_COUNT; i++)
        if (mn_got[i] == want[i])
            mn_mask |= 1UL << i;
}

/* --------------------------------------------------------------------------
 * La macchina a stati, un passo per quadro
 * ----------------------------------------------------------------------- */
static void goto_state(int s)
{
    state = s;
    state_t0 = JAG_HAL_VBL_TICK;
}

/* Torna 1 quando il front-end ha finito e la partita puo' cominciare. */
int Front_Frame(void)
{
    ULONG hold;

    if (state == F_DONE)
        return 1;

    if (!started) {
        started = 1;
        /* LA CLUT A NERO PRIMA DI DISEGNARE, e non e' pignoleria.
         *
         * `logo_show` riempie 307 KB e ne copia 110: sul 68000 e' un terzo di
         * secondo, e in quel terzo di secondo l'OP sta disegnando quel che
         * trova. Finche' la CLUT resta quella del boot - la rampa di grigi che
         * InitClut scrive nel lato RMAC - il logo si vede COMPARIRE IN BIANCO
         * E NERO, e poi sparire per rifarsi con la dissolvenza. Visto sul ferro
         * al primo collaudo di S48.
         *
         * `pal_now` qui e' ancora tutta zero (BSS), quindi questa chiamata
         * scrive nero puro: e' la stessa funzione della dissolvenza, non un
         * percorso a parte che potrebbe divergere. Il disegno avviene al buio e
         * la prima cosa che si vede e' il primo passo della dissolvenza. */
        pal_apply(0);
        font_load();
        text_load();
        logo_show();
        fade = 0;
        goto_state(F_LOGO_IN);
        return 0;
    }

    Hal_ReadPad();

    switch (state) {
    case F_LOGO_IN:
        if (++fade >= FADE_STEPS) {
            fade = FADE_STEPS;
            goto_state(F_LOGO_HOLD);
        }
        pal_apply(fade);
        break;

    case F_LOGO_HOLD:
        hold = (ULONG)JAG_HAL_TICK_HZ * LOGO_HOLD_S;
        /* Un tasto qualsiasi salta la posa. Non la dissolvenza: saltare anche
         * quella farebbe sparire il logo di scatto, che si legge come un
         * difetto invece che come una scorciatoia. */
        if (PadEdge || PadKeysEdge || (JAG_HAL_VBL_TICK - state_t0) >= hold)
            goto_state(F_LOGO_OUT);
        break;

    case F_LOGO_OUT:
        if (--fade <= 0) {
            fade = 0;
            pal_apply(0);
            menu_show();
            probes_finish();
            goto_state(F_MENU_IN);
        } else {
            pal_apply(fade);
        }
        break;

    case F_MENU_IN:
        if (++fade >= FADE_STEPS) {
            fade = FADE_STEPS;
            goto_state(F_MENU);
        }
        fire_frame();          /* la fiamma vive gia' sotto la dissolvenza */
        pal_apply(fade);
        break;

    case F_MENU:
        fire_frame();
        if (PadEdge & PAD_UP)
            menu_move(-1);
        else if (PadEdge & PAD_DOWN)
            menu_move(1);
        /* Solo la prima voce fa qualcosa. Le altre tre sono selezionabili -
         * la struttura del menu e' quella dell'originale - ma non hanno
         * ancora niente dietro. */
        else if ((PadEdge & (PAD_A | PAD_OPTION)) && sel == 0)
            goto_state(F_START_OUT);
        break;

    case F_START_OUT:
        /* A nero prima di caricare: la stanza ci mette secondi e la palette
         * gliela rimette Bg_Build. Senza questa dissolvenza il menu resterebbe
         * congelato a schermo per tutto il caricamento. */
        if (--fade <= 0) {
            fade = 0;
            pal_apply(0);
            /* E POI SI CANCELLA DAVVERO, non solo con la palette.
             *
             * La prima cosa che fa Bg_Build e' ricaricare la palette del gioco
             * (scene_bg.c: Bg_LoadPalette in testa), che e' la STESSA con cui
             * e' disegnato il menu - RESS slot 0. Il fondo invece arriva otto
             * secondi dopo. Senza questa cancellazione il menu tornerebbe
             * visibile un attimo dopo essere sparito e ci resterebbe per tutto
             * il caricamento, coi quadratini di fase sopra: la dissolvenza
             * sembrerebbe rotta proprio dove funziona.
             *
             * Si azzera il fondo INTERO, non le 480 righe visibili: le altre
             * 112 sono il margine da cui pesca lo scorrimento della camera. */
            {
                ULONG *p = (ULONG *)FB;
                ULONG n = (ULONG)JAG_BG_W * JAG_BG_H / 4;
                while (n--)
                    *p++ = 0;
            }
            goto_state(F_DONE);
            return 1;
        }
        pal_apply(fade);
        break;
    }
    return 0;
}
