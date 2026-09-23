/* host_front.c - fa girare front_menu.c su un PC, con i byte veri della
 * cartuccia.
 *
 * PERCHE' ESISTE
 * --------------
 * Stessa ragione di host_body.c: separare "il disegno e' quello giusto?" da
 * "il Jaguar lo esegue come previsto?". Il front-end tocca tre cose nuove - il
 * ritaglio FSCR, il font mascherato di LBA1 e la tabella dei testi - e tutte e
 * tre sono formati letti da un file. Un errore di formato qui si vede in un
 * secondo; su BigPEmu costa una build della cartuccia e un giro d'emulatore, e
 * si presenta come "il menu ha il testo storto" senza dire quale dei tre.
 *
 * `#include "front_menu.c"`: il file compilato e' quello che va sul Jaguar.
 * Le uniche sostituzioni sono gli indirizzi assoluti - framebuffer, area di
 * lavoro, CLUT e base della cartuccia - piu' i due servizi di memoria che qui
 * non hanno un'arena vera dietro.
 *
 * USO (lo lancia build_v6.bat dentro l'immagine Docker)
 *     gcc -O2 -I. -o host_front host_front.c && ./host_front cart/data_v6.bin
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "mem_map.h"

/* La cartuccia: il file del packer, caricato in un array. L'indirizzo di un
 * array globale e' una costante, quindi hal_hqr.c puo' continuare a scriversi
 * `cart = (const unsigned char *)JAG_ROM_DATA` come fa sul Jaguar. */
static unsigned char host_cart[JAG_ROM_DATA_SIZE];
static unsigned char host_fb[640 * 592];
static unsigned char host_work[JAG_BRICK_WORK_SIZE];
static unsigned short host_clut[256];

/* L'arena: qui il font e i testi. Non e' l'arena vera - quella e' hal_mem.c,
 * che vive sul Jaguar - ma ha la stessa forma d'uso: si alloca e non si libera
 * finche' il front-end e' vivo. */
static unsigned char host_arena[512 * 1024];
static unsigned long host_arena_used;

#undef JAG_ROM_DATA
#define JAG_ROM_DATA    ((unsigned long)(void *)host_cart)

#include "jag_hw.h"
#undef JAG_CLUT
#define JAG_CLUT        ((unsigned long)(void *)host_clut)

/* L'x86 e' little endian e il Jaguar no. I due punti in cui la differenza si
 * vede sono la lettura dei long BIG endian del contenitore e quella del
 * framebuffer per le somme: entrambi i file li lasciano sostituire apposta, e
 * quel che si sostituisce e' la LETTURA, non la logica. */
static unsigned long be32(const void *p)
{
    const unsigned char *b = (const unsigned char *)p;
    return ((unsigned long)b[0] << 24) | ((unsigned long)b[1] << 16)
           | ((unsigned long)b[2] << 8) | b[3];
}

#define HQR_RD32(p)     be32((const void *)(p))
#define FRONT_RD32BE(p) be32((const void *)(p))

#define FRONT_FB_BASE   ((unsigned long)(void *)host_fb)
/* La simulazione del fuoco: sul Jaguar e' spenta (vedi la nota in front_menu.c
 * - 0,3 fps), qui e' accesa perche' e' QUI che si verifica. Il banco non ha un
 * bus da contendere e i sessanta quadri girano in un istante. */
#define FRONT_FIRE_ANIM 1
#define FRONT_WORK_BASE ((unsigned long)(void *)host_work)
#define FRONT_WORK_SIZE ((unsigned long)sizeof host_work)

#include "hal.h"

void *LoadMalloc(ULONG size)
{
    void *p = host_arena + host_arena_used;
    host_arena_used += ((unsigned long)size + 7) & ~7UL;
    if (host_arena_used > sizeof host_arena) {
        printf("host_front: arena esaurita\n");
        exit(1);
    }
    return p;
}

void *LoadMallocPhrase(ULONG size) { return LoadMalloc(size); }

void TheEnd(WORD code, const char *why)
{
    printf("host_front: TheEnd %d (%s)\n", (int)code, why ? why : "");
    exit(1);
}

/* Il pad non serve: il banco chiama il disegno, non la macchina a stati. */
UWORD PadState, PadEdge, PadKeys, PadKeysEdge, PadSeen, PadKeysSeen;
void Hal_ReadPad(void) { }

#include "hal_hqr.c"
#include "front_menu.c"

#include "menu_expect.h"

static const char *const NAMES[MN_EXPECT_COUNT] = {
    "LOGO_SUM1", "LOGO_SUM2", "LOGO_GEOM", "LOGO_POS", "LOGO_FILL",
    "MENU_SUM1", "MENU_SUM2", "MENU_TPIX", "MENU_TSUM", "MENU_W01", "MENU_W23"
};

/* Il PNG di quel che il banco ha DAVVERO disegnato. Non e' decorazione: il
 * confronto numerico dice "diverso", questo dice "diverso COME" - ed e'
 * l'unica differenza fra dieci minuti e un pomeriggio quando a sbagliare e' un
 * offset dentro un glifo. Scrive un PGM, che sono tre righe di codice: gli
 * indici di palette senza la palette non sono un'immagine, ma la FORMA delle
 * lettere si legge benissimo. */
static void dump_pgm(const char *path)
{
    FILE *f = fopen(path, "wb");

    if (!f)
        return;
    fprintf(f, "P5\n%d %d\n255\n", SCR_W, SCR_H);
    fwrite(host_fb, 1, (size_t)SCR_W * SCR_H, f);
    fclose(f);
}

int main(int argc, char **argv)
{
    static const ULONG want[MN_EXPECT_COUNT + 1] = MN_EXPECT_LIST;
    const char *path = (argc > 1) ? argv[1] : "cart/data_v6.bin";
    FILE *f = fopen(path, "rb");
    size_t n;
    int i, bad = 0;

    if (!f) {
        printf("host_front: manca %s\n", path);
        return 1;
    }
    n = fread(host_cart, 1, sizeof host_cart, f);
    fclose(f);
    printf("host_front: cartuccia %s, %lu byte\n", path, (unsigned long)n);

    if (!HQR_Init()) {
        printf("host_front: il contenitore non si apre\n");
        return 1;
    }

    font_load();
    text_load();
    if (!font_blob || !text_ids || !text_body) {
        printf("host_front: font o testi mancanti (font %p, ids %p, testi %p)\n",
               (void *)font_blob, (void *)text_ids, (void *)text_body);
        return 1;
    }
    printf("  font: %lu glifi dichiarati\n", (unsigned long)font_n);
    for (i = 0; i < MN_NB_ITEMS; i++)
        printf("  voce %d (testo %3d) \"%s\"\n", i, menu_text_id[i],
               text_get(menu_text_id[i]));

    logo_show();
    dump_pgm("build/host_front_logo.pgm");
    menu_show();
    dump_pgm("build/host_front_menu.pgm");
    probes_finish();

    /* IL GIRO COMPLETO DELLA SELEZIONE, e perche' e' un INVARIANTE e non un
     * valore atteso in piu'.
     *
     * ShadeBox scurisce quel che TROVA: applicarlo due volte sulla stessa voce
     * la lascia piu' scura delle altre, e il difetto si vede solo dopo aver
     * mosso il cursore - cioe' mai, in un confronto fatto sul primo disegno.
     * Qui la selezione fa un giro intero e torna al punto di partenza: se il
     * ripristino dal fondo intatto funziona, il framebuffer deve tornare
     * IDENTICO, e "identico a se stesso" non dipende da nessun numero scritto
     * altrove ([[feedback_buco_simmetrico_oracolo]]). */
    {
        ULONG a1 = mn_got[MNX_MENU_SUM1], a2 = mn_got[MNX_MENU_SUM2];
        ULONG b1, b2;
        int k;

        for (k = 0; k < MN_NB_ITEMS; k++)
            menu_move(1);
        fb_sums(&b1, &b2);
        printf("  giro della selezione: %08lX %08lX -> %08lX %08lX  %s\n",
               (unsigned long)a1, (unsigned long)a2,
               (unsigned long)b1, (unsigned long)b2,
               (a1 == b1 && a2 == b2) ? "identico" : "DIVERSO");
        if (a1 != b1 || a2 != b2) {
            dump_pgm("build/host_front_menu_after.pgm");
            bad++;
        }
        /* E anche all'indietro: il verso non deve fare differenza. */
        for (k = 0; k < MN_NB_ITEMS; k++)
            menu_move(-1);
        fb_sums(&b1, &b2);
        if (a1 != b1 || a2 != b2) {
            printf("  giro all'indietro: DIVERSO\n");
            bad++;
        }
    }

    /* IL FUOCO (S50). Un effetto animato non si verifica con una somma di
     * controllo, si guarda ([[feedback_visual_over_metric]]) - ma quel che si
     * PUO' verificare qui e' che il C sia la stessa simulazione del
     * riferimento, e allora i PNG gia' guardati in S48 valgono anche per lui.
     *
     * La sequenza dev'essere identica a tools/fire_ref.py, tag 'fork': seme
     * RESS 51, pseudocasuale a 1, e per ogni quadro `spark` poi `compute`. Le
     * attese sono quelle che quello strumento stampa, ai suoi stessi quadri.
     * Non stanno in menu_expect.h apposta: non vengono dal menu, vengono da
     * un'altra sorgente, e mescolarle nasconderebbe quale delle due ha ceduto. */
    {
        static const struct { int n; int mean10; int max; } want_fire[] = {
            { 1, 102, 255 }, { 5, 104, 255 }, { 20, 103, 255 }, { 60, 112, 255 }
        };
        int k, f = 0;

        if (!fire_ready) {
            printf("  fuoco: SEME ASSENTE (RESS %d), la voce resta piena\n",
                   MN_RESS_PLASMA);
            bad++;
        } else {
            fire_init();                       /* riparte dal seme, seed = 1 */
            for (k = 1; k <= 60; k++) {
                ULONG s = 0;
                int mx = 0, m10;
                long j;

                fire_spark();
                fire_step();
                if (f >= 4 || want_fire[f].n != k)
                    continue;
                for (j = 0; j < (long)FIRE_W * FIRE_H; j++) {
                    s += FIRE_IMG[j];
                    if (FIRE_IMG[j] > mx)
                        mx = FIRE_IMG[j];
                }
                m10 = (int)((s * 10 + FIRE_W * FIRE_H / 2)
                            / ((ULONG)FIRE_W * FIRE_H));
                printf("  fuoco quadro %2d: media %d.%d  max %3d  "
                       "atteso %d.%d / %3d  %s\n", k, m10 / 10, m10 % 10, mx,
                       want_fire[f].mean10 / 10, want_fire[f].mean10 % 10,
                       want_fire[f].max,
                       (m10 == want_fire[f].mean10 && mx == want_fire[f].max)
                       ? "ok" : "NO");
                if (m10 != want_fire[f].mean10 || mx != want_fire[f].max)
                    bad++;
                f++;
            }
            fire_init();                       /* si lascia com'era trovato */
        }
    }

    for (i = 0; i < MN_EXPECT_COUNT; i++) {
        int ok = (mn_got[i] == want[i]);
        printf("  %-9s %08lX  atteso %08lX  %s\n", NAMES[i],
               (unsigned long)mn_got[i], (unsigned long)want[i],
               ok ? "ok" : "NO");
        if (!ok)
            bad++;
    }
    printf("maschera %08lX (attesa %08lX)\n", (unsigned long)mn_mask,
           (unsigned long)((1UL << MN_EXPECT_COUNT) - 1));
    if (bad) {
        printf("SONDE DEL FRONT-END CADUTE: %d\n", bad);
        return 1;
    }
    printf("tutte le sonde del front-end tengono\n");
    return 0;
}
