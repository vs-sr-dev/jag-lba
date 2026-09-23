/* host_colband.c - fa girare l'INDICE PER COLONNA di scene_bg.c su un PC.
 *
 * PERCHE' ESISTE
 * --------------
 * tools/m3d_scan.py ha provato l'ALGORITMO su 175.860 rettangoli: la banda per
 * colonna visita le stesse tessere della scansione completa, nello stesso
 * ordine. Quel che non ha provato e' la TRASCRIZIONE IN C, che e' dove stanno i
 * difetti veri di questo pezzo: il verso dell'arrotondamento (il C tronca verso
 * lo zero, Python arrotonda verso il basso, e i numeratori sono negativi quasi
 * sempre) e il taglio a 16 bit delle bande.
 *
 * Quindi qui NON c'e' un gemello: si compila `engine_c/scene_bg.c` e si chiamano
 * `col_open`/`col_next` vere. L'unica sostituzione e' la base della lista celle,
 * che scene_bg.c lascia sostituire apposta (CELL_LIST_BASE), piu' gli stub del
 * ferro - che non vengono mai chiamati: nessuna delle funzioni sotto esame
 * tocca il Blitter, la cartuccia o un registro.
 *
 * IL DATO arriva dal riferimento Python (tools/m3d_scan.py --dump), che e' anche
 * chi calcola l'attesa: celle e indice di colonna delle stanze vere.
 *
 * I DUE -Wno- NON SONO UN TAPPETO SOTTO CUI NASCONDERE. scene_bg.c mette i
 * puntatori ai brick dentro `handles[]`, che e' ULONG: sul 68000 sono 32 bit e
 * il cast e' esatto, su x86-64 un puntatore ne vuole 64 e il cast TRONCA. Quel
 * codice qui sarebbe sbagliato - ed e' precisamente perche' sarebbe sbagliato
 * che gli stub qui sopra fermano il banco invece di lasciarlo proseguire: il
 * disegno non e' sotto esame, l'indice si'.
 *
 * USO
 *     python tools/m3d_scan.py --dump build/colband
 *     gcc -O2 -Wall -Wno-pointer-to-int-cast -Wno-int-to-pointer-cast \
 *         -o host_colband tools/host_colband.c
 *     ./host_colband build/colband 11
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "../engine_c/mem_map.h"

/* La lista celle diventa un array. E' la stessa sostituzione che host_body.c fa
 * con il framebuffer: una riga, e l'indirizzo assoluto del Jaguar non c'entra
 * piu' niente. */
static unsigned char host_cells[JAG_CELL_SLOTS * 8];
#define CELL_LIST_BASE ((unsigned long)(void *)host_cells)

/* Gli stub del ferro. Nessuno di questi viene chiamato: servono al LINKER,
 * perche' scene_bg.c contiene anche il disegno e il disegno nomina il Blitter e
 * la cartuccia. Se uno di loro esegue, il banco si ferma invece di andare
 * avanti su un dato inventato. */
static void host_stub(const char *who)
{
    fprintf(stderr, "host_colband: %s NON deve eseguire\n", who);
    exit(2);
}

#include "../engine_c/hal.h"

void TheEnd(WORD code, const char *msg)
{
    fprintf(stderr, "TheEnd %04X: %s\n", (unsigned)code, msg);
    exit(2);
}
/* `Blit_Wait` non si stub: e' `static inline` dentro jag_hw.h e arriva da se'.
 * Su x86 leggerebbe un indirizzo che non esiste e girerebbe per sempre - un
 * motivo in piu' perche' niente qui dentro chiami il disegno. */
const void *HQR_Get(hqr_id_t id, int i) { (void)id; (void)i;
                                          host_stub("HQR_Get"); return 0; }
ULONG HQR_Size(hqr_id_t id, int i)      { (void)id; (void)i;
                                          host_stub("HQR_Size"); return 0; }
int  HQR_Slots(hqr_id_t id)             { (void)id;
                                          host_stub("HQR_Slots"); return 0; }
ULONG Hal_MemUsedCube(void)             { host_stub("Hal_MemUsedCube");
                                          return 0; }
void *LoadMallocPhrase(ULONG size)      { (void)size;
                                          host_stub("LoadMallocPhrase");
                                          return 0; }
void HQM_Free_All(void)                 { host_stub("HQM_Free_All"); }

/* Lo stato del gioco che scene_bg.c nomina (la camera lo segue, lo scorrimento
 * legge la scena). Qui resta a zero e non lo legge nessuno: le funzioni che lo
 * usano - Bg_CameraFollow, Bg_ScrollTo - non sono sotto esame. */
T_OBJET ListObjet[MAX_OBJETS];
T_SCENE Scene;

#include "../engine_c/scene_bg.c"

/* ------------------------------------------------------------------------- */

#define BG_W 640
#define BG_H 592

static int rd32(FILE *f, long *out)
{
    unsigned char b[4];
    if (fread(b, 1, 4, f) != 4)
        return 0;
    *out = (long)((int)(b[0] | (b[1] << 8) | (b[2] << 16) |
                        ((unsigned)b[3] << 24)));
    return 1;
}

/* L'indice di colonna e le celle devono raccontare la stessa cosa: ogni cella
 * dentro l'intervallo della colonna (gz,gx) deve avere gxz = gx+gz e
 * px = (gx-gz)*ISO_X. Non e' pedanteria - e' l'unico pezzo che il dump porta
 * gia' fatto invece di calcolarlo qui, quindi e' l'unico che potrebbe arrivare
 * sbagliato senza che nessuno se ne accorga. */
static int check_index(void)
{
    int gz, gx;
    for (gz = 0; gz < CUBE_Z; gz++)
        for (gx = 0; gx < CUBE_X; gx++) {
            int ci = gz * CUBE_X + gx;
            UWORD k;
            if (colstart[ci] > colstart[ci + 1])
                return fprintf(stderr, "colstart non monotono a %d\n", ci), 0;
            for (k = colstart[ci]; k < colstart[ci + 1]; k++)
                if ((int)celllist[k].gxz != gx + gz ||
                    (int)celllist[k].px != (gx - gz) * ISO_X)
                    return fprintf(stderr, "cella %u fuori colonna (%d,%d)\n",
                                   (unsigned)k, gz, gx), 0;
        }
    return 1;
}

int main(int argc, char **argv)
{
    static const int sizes[3][2] = { {96, 88}, {128, 96}, {24, 38} };
    static const int yms[3] = { 0, 5, 12 };
    const char *dir = argc > 1 ? argv[1] : "build/colband";
    int scene;
    long step = argc > 2 ? atol(argv[2]) : 11;

    for (scene = 0; scene < 3; scene++) {
        char path[512];
        FILE *f;
        long vx, vy, spanx, spany, ncell, i;
        unsigned int s1 = 0, s2 = 0;
        long cols = 0, cells = 0, rects = 0;
        int si, yi, x0, y0;

        sprintf(path, "%s/scene%d.bin", dir, scene);
        f = fopen(path, "rb");
        if (!f) {
            fprintf(stderr, "manca %s (lanciare prima m3d_scan.py --dump)\n",
                    path);
            return 1;
        }
        if (!rd32(f, &vx) || !rd32(f, &vy) || !rd32(f, &spanx) ||
            !rd32(f, &spany) || !rd32(f, &ncell)) {
            fprintf(stderr, "%s: intestazione corta\n", path);
            return 1;
        }
        if (ncell > (long)JAG_CELL_SLOTS) {
            fprintf(stderr, "%s: %ld celle, ne stanno %d\n",
                    path, ncell, (int)JAG_CELL_SLOTS);
            return 1;
        }
        if (fread(host_cells, 8, (size_t)ncell, f) != (size_t)ncell) {
            fprintf(stderr, "%s: celle corte\n", path);
            return 1;
        }
        for (i = 0; i <= CUBE_COLS; i++) {
            unsigned char b[2];
            if (fread(b, 1, 2, f) != 2) {
                fprintf(stderr, "%s: indice corto\n", path);
                return 1;
            }
            colstart[i] = (UWORD)(b[0] | (b[1] << 8));
        }
        fclose(f);

        BgStat.vx = vx;
        BgStat.vy = vy;
        BgStat.spanx = (UWORD)spanx;
        BgStat.spany = (UWORD)spany;
        BgStat.celllist = (UWORD)ncell;
        if (!check_index())
            return 1;

        /* LO SWEEP, con gli stessi passi e le stesse taglie di m3d_scan.py: il
         * rettangolo passeggia su tutto lo schermo e per ognuno si accumula la
         * SEQUENZA degli intervalli aperti. Il numero finale non e' un
         * conteggio - due bande diverse che aprono lo stesso numero di colonne
         * darebbero lo stesso conteggio e somme diverse. */
        for (si = 0; si < 3; si++) {
            int w = sizes[si][0], h = sizes[si][1];
            for (y0 = -h; y0 < BG_H; y0 += (int)step)
                for (x0 = -w; x0 < BG_W; x0 += (int)step) {
                    int cx0 = x0 < 0 ? 0 : x0;
                    int cy0 = y0 < 0 ? 0 : y0;
                    int cx1 = x0 + w - 1, cy1 = y0 + h - 1;

                    if (cx1 > BG_W - 1) cx1 = BG_W - 1;
                    if (cy1 > BG_H - 1) cy1 = BG_H - 1;
                    if (cx0 > cx1 || cy0 > cy1)
                        continue;
                    for (yi = 0; yi < 3; yi++) {
                        T_CBAND b;
                        UWORD k0, k1;

                        rects++;
                        s1 = (s1 + 0x9E3779B9u) ^ (unsigned)(cx0 * 8191 + cy0);
                        if (!col_open(&b, vx, vy, cx0, cy0, cx1, cy1,
                                      yms[yi]))
                            continue;
                        while (col_next(&b, &k0, &k1)) {
                            cols++;
                            cells += k1 - k0;
                            s1 += (unsigned)k0 * 65537u + (unsigned)k1;
                            s2 += s1;
                        }
                    }
                }
        }
        printf("scena %d: %ld celle, %ld rettangoli, %ld colonne aperte, "
               "%ld celle percorse, somma %08X %08X\n",
               scene, ncell, rects, cols, cells, s1, s2);
    }
    return 0;
}
