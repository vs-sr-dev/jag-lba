/*
 * JagLBA V6 - M1 (HAL di base) + M2 (scena e fondo).
 *
 * Il blob non e' ancora il motore, ma da questa milestone contiene i cinque
 * servizi su cui il motore poggia: tempo a 50 Hz, input, risorse HQR dalla
 * cartuccia, arene di memoria, arresto parlante.
 *
 * CRITERIO DI VERIFICA (docs/skeleton_design.md 4, riga M1)
 * --------------------------------------------------------
 * "Il C legge un record HQR dal cart e ne mostra un valore noto."
 *
 * Preso alla lettera si accontenterebbe di cifre a schermo, e cifre a schermo
 * non provano niente: senza un riferimento non si distingue una lettura giusta
 * da una lettura plausibile. Percio' il confronto NON lo fa l'occhio:
 * tools/hqr_pack.py calcola i valori attesi quando impacchetta i dati e li
 * scrive in hqr_expect.h; qui il C rilegge la cartuccia, confronta, e mostra
 * una maschera con un bit per sonda.
 *
 *   riga 0 = 000000FF  ->  tutte e otto le sonde tornano. Criterio soddisfatto.
 *
 * Le righe 1..8 mostrano comunque i valori LETTI, cosi' una sonda caduta si
 * diagnostica dallo stesso screenshot invece di richiedere un altro giro.
 *
 * Mappa delle righe (l'indice e' disegnato in grigio a sinistra del valore):
 *
 *   0   maschera delle sonde HQR                     atteso 000000FF
 *   1   magic del contenitore                        atteso 4A485152 'JHQR'
 *   2   versione | numero di archivi
 *   3   dimensione del contenitore
 *   4   somma di controllo dei payload
 *   5   HQR_Size(HQR_SCENE, 0)
 *   6   SCENE[0] byte 4..7
 *   7   HQR_Size(HQR_BODY, 0)
 *   8   BODY[0] byte 0..3
 *   9   interrupt/s pubblicati | NbFramePerSecond
 *   10  contatore degli interrupt video              (vivo)
 *   11  TimerRef                                     (vivo, ~50 al secondo)
 *   12  pulsanti MAI VISTI premuti | premuti adesso
 *   13  [MyJoy visti][MyJoy][MyFire visti][MyFire]   un byte per campo
 *   14  tastierino MAI VISTO | premuto adesso
 *   15  arena persistente usata | arena per-cubo     atteso 01000200
 *
 * Colonna centrale (M2a, scena 0 letta dalla cartuccia):
 *
 *   16  maschera delle sonde della scena              atteso 000000FF
 *   17  eroe X | Z                                    atteso 69006BEA
 *   18  eroe Y
 *   19  camera gx | gz                                atteso 00350036
 *   20  attori | zone
 *   21  change-cube | track
 *   22  prima change-cube: scena destinazione
 *   23  isola | musica della scena
 *   24  cammino arrivato in fondo esatto (1 = si')
 *   25  scarto dei byte di coda                       atteso 00000000
 *
 * In coda alla colonna centrale, la DURATA del caricamento. Sta qui e non
 * sotto le sonde del fondo perche' la terza colonna e' piena: le sonde sono
 * tredici piu' la maschera, e le colonne tengono sedici righe ciascuna.
 *
 *   29  passo | Bg_Build ha completato | interrupt video consumati in tutto
 *   30  durata di grid_decode | build_cells        (in interrupt video)
 *   31  durata di decode_bricks | render
 *
 * Colonna di destra (M2b, il FONDO disegnato):
 *
 *   32  maschera delle sonde del fondo                atteso 00001FFF
 *   33  camera gx | gz
 *   34  offset di schermo vx | vy
 *   35  colonne di griglia anomale                    atteso 0
 *   36  tessere decodificate
 *   37  byte occupati nel buffer di lavoro
 *   38  celle risolte del cubo (la lista compatta)
 *   39  celle passate al Blitter
 *   40  riquadro dei pixel non trasparenti: minx | miny
 *   41  riquadro: maxx | maxy
 *   42  pixel non trasparenti
 *   43  Fletcher s1 del framebuffer (contenuto)
 *   44  Fletcher s2 del framebuffer (ordine)
 *   45  somma della CLUT caricata
 *
 * Le tre colonne servono INSIEME. Una sonda del fondo che cade mentre quelle
 * della scena tengono dice una cosa diversa da tutte e tre che cadono, ed e'
 * esattamente la distinzione che fa risparmiare un giro di build.
 *
 * Il tasto C toglie e rimette la diagnostica: la maschera vuole le righe
 * esadecimali, il confronto contro il PC DOS vuole la scena e nient'altro, e
 * cosi' e' LO STESSO avvio a produrre entrambe le prove.
 *
 * Il tasto B gira fra le TRE pagine: la 0 e' quella qui sopra (M1 + M2), la 1
 * quella degli attori (M3a), la 2 quella dei corpi disegnati (M3c). Le
 * quarantotto righe erano piene gia' a fine M2, e il numero di blocchi sopra il
 * righello dice quale pagina si sta guardando.
 *
 * PAGINA 2, e come si legge la sua maschera (atteso 00FFFFFF):
 *
 *   riga 0      la maschera
 *   righe 1-24  le ventiquattro sonde, nell'ordine di body_expect.h
 *   righe 25-31 la diagnostica (corpi disegnati, GPU, flusso, preclip)
 *
 * I bit si leggono a BLOCCHI, ed e' il motivo per cui sono ventiquattro e non
 * una:
 *
 *   bit 1        il centro di proiezione degli attori. Se cade, cadono tutte:
 *                gli attori e le tessere NON usano lo stesso centro (288/215
 *                contro 311/240) e sbagliarlo sposta ogni corpo di (23,25).
 *   bit 2-3      i vertici proiettati   -> posa, matrice Beta, proiezione
 *   bit 4-5      le intensita'          -> luce di scena, normali, divisione
 *   bit 6        la faccia nascosta     -> quante entita' sopravvivono
 *   bit 7-8      l'ordine di disegno    -> chiave del pittore, ordinamento
 *   bit 13-15    gli SPRITE (M3e)       -> quanti, dove, quanti pixel:
 *                13 SPR (disegnati|scartati dal ritaglio), 14 SPR_XY (angolo
 *                del primo, cioe' PtrZvExtra e proiezione), 15 SPR_PIX
 *   bit 16-18    LINEE e SFERE (S37)    -> quante, dove, quanti pixel:
 *                16 ENT (linee|sfere entrate nella lista), 17 ENT_XY (centro
 *                della prima sfera), 18 ENT_PIX (raggio a schermo | pixel)
 *   bit 19-23    il COMPOSTO            -> i pixel
 *
 * Se i bit 1..8 sono alti e i 19..23 no, il difetto e' nel RIEMPIMENTO e non
 * nella pipeline - che e' anche l'unico pezzo destinato a sparire quando il
 * riempitore passa alla GPU. Senza questa separazione la sostituzione non si
 * potrebbe verificare, perche' ogni sonda cambierebbe insieme alle altre.
 *
 * I bit 1..8 girano gia' VERDI su un PC prima ancora di costruire la
 * cartuccia: engine_c/host_body.c compila QUESTO body_draw.c su x86 con i dati
 * veri. Quindi se qui cadono, il difetto non e' nella pipeline - e' nella
 * mappa, nell'allineamento o nella cartuccia.
 *
 * Il tasto A avanza di un passo. I passi li dichiara un solo posto,
 * tools/scene_parse.py:walk_steps(), e li consumano tutti e tre i generatori di
 * attese:
 *   passo 0  scena 0, l'ospedale, caricata da capo;
 *   passo 1  la stanza in cui porta la prima change-cube, entrandoci DA quella
 *            porta - cioe' con la posizione che dice la zona (criterio di M2);
 *   passo 2  la scena 2, caricata da capo. E' li' che si verifica M3, perche'
 *            la scena 0 non ha NESSUN attore dentro i 640x480 (misurato: 0 su
 *            22) e la 1, entrandoci dalla porta, ne mostra due ma sono
 *            entrambi SPRITE_3D, cioe' bitmap e non corpi.
 *
 * Come si verifica il tempo senza cronometro: le righe 10 e 11 avanzano con
 * rapporto TimerRef/interrupt = 50/TICK_HZ. Su BigPEmu (30 interrupt/s) sono 5
 * a 3, su Jaguar NTSC (60) sono 5 a 6. Due catture a distanza qualsiasi
 * verificano il rapporto senza misurare il tempo.
 *
 * Come si verifica l'input senza avere le mani sul controller e gli occhi sullo
 * screenshot nello stesso istante: le righe 12 e 13 tengono anche i bit MAI
 * VISTI premuti, che non tornano piu' a zero. Si prova il controller, poi si
 * guarda. La riga 12 prova la matrice di strobe, la 13 prova la mappatura su
 * LBA: sono due strati e possono rompersi separatamente.
 */

#include "hal.h"
/* Solo per il divisore del clock seriale di Jerry (JAG_SCLK), che la spazzata
 * dell'audio regola a caldo. E' l'unico registro hardware che questo file
 * tocca da se': tutto il resto passa dall'HAL. */
#include "jag_hw.h"
#include "hqr_expect.h"
#include "scene_expect.h"
#include "bg_expect.h"
#include "actor_expect.h"
#include "body_expect.h"
#include "menu_expect.h"

/* Sonde di sezione. Restano da M0: sono la rete che fa fallire il boot in modo
 * parlante quando il linker script e' sbagliato, invece di lasciare che diventi
 * un guasto misterioso tre milestone piu' avanti.
 *
 * volatile obbligatorio su tutte e tre: a -O2 GCC sa che una static mai
 * scritta vale sempre il suo inizializzatore, quindi ripiegherebbe il confronto
 * e cancellerebbe la variabile - e la sonda non proverebbe piu' niente. Il
 * primo build di M0 e' uscito con .data di dimensione zero proprio per questo. */
#define ERR_DATA    0x0BAD0001UL   /* .data non copiata dalla ROM */
#define ERR_BSS     0x0BAD0002UL   /* .bss non azzerata           */
#define ERR_RODATA  0x0BAD0003UL   /* .rodata non leggibile       */

static volatile ULONG data_probe = 0x12345678UL;          /* -> .data   */
static volatile ULONG bss_probe;                          /* -> .bss    */
static const volatile ULONG rodata_probe = 0x0BADC0DEUL;  /* -> .rodata */

/* --- Marcatore di vita, invariato da M0 ------------------------------------
 * Un MOVIMENTO, non un colore: se si ferma si vede subito, e non dipende
 * dall'interpretazione di una tinta ([[feedback_debug_border_milestones]]). */
#define COL_BG       0
#define COL_MARKER   255
#define COL_RULER    160

#define MARKER_W     16
#define MARKER_H     16
#define MARKER_Y     24
#define MARKER_SLOTS 32
#define MARKER_STEP  19
#define MARKER_X0     8

static unsigned int marker_slot;

/* Dimensioni delle due allocazioni di collaudo. Sono l'unica ragione per cui
 * la riga 15 ha un valore ATTESO: senza esercitare le arene, "0 usato" sarebbe
 * indistinguibile da "le arene non ci sono". */
#define TEST_PERSIST  0x100
#define TEST_CUBE     0x200
#define TEST_TRANSIENT 0x40      /* allocato e rilasciato: collauda Free() */

/* Legge un long dal payload di una risorsa. Il contenitore allinea i payload a
 * 4 byte, quindi un offset multiplo di 4 e' allineato per costruzione. */
static ULONG hqr_word(hqr_id_t id, int index, ULONG byte_off)
{
    const unsigned char *p = (const unsigned char *)HQR_Get(id, index);

    if (!p || HQR_Size(id, index) < byte_off + 4)
        return 0;
    return *(const ULONG *)(p + byte_off);
}

/* Le otto sonde, nello stesso ordine in cui tools/hqr_pack.py le dichiara.
 * Torna la maschera dei bit superati e riempie got[] con i valori letti. */
static ULONG hqr_probe(ULONG *got)
{
    ULONG want[HQR_EXPECT_COUNT];
    ULONG mask = 0;
    int i;

    got[0]  = HQR_Header(0);              want[0] = HQR_EXPECT_MAGIC;
    got[1]  = HQR_Header(1);              want[1] = HQR_EXPECT_VERARCH;
    got[2]  = HQR_Header(2);              want[2] = HQR_EXPECT_TOTAL;
    got[3]  = HQR_Header(3);              want[3] = HQR_EXPECT_CHECKSUM;
    got[4]  = HQR_Size(HQR_SCENE, 0);     want[4] = HQR_EXPECT_SCENE0_SIZE;
    got[5]  = hqr_word(HQR_SCENE, 0, 4);  want[5] = HQR_EXPECT_SCENE0_W4;
    got[6]  = HQR_Size(HQR_BODY, 0);      want[6] = HQR_EXPECT_BODY0_SIZE;
    got[7]  = hqr_word(HQR_BODY, 0, 0);   want[7] = HQR_EXPECT_BODY0_W0;

    for (i = 0; i < HQR_EXPECT_COUNT; i++)
        if (got[i] == want[i])
            mask |= 1UL << i;
    return mask;
}

/* Le arene devono essere memoria vera, non un puntatore plausibile in mezzo al
 * nulla: si scrive e si rilegge. Un fallimento qui e' un TheEnd, perche' vuol
 * dire che il linker script colloca l'heap dove non c'e' DRAM scrivibile - e
 * tutto quello che verrebbe dopo sarebbe da buttare. */
static void arena_setup(void)
{
    volatile ULONG *a, *b;
    void *transient;

    Hal_MemInit();

    a = (volatile ULONG *)Malloc(TEST_PERSIST);
    transient = Malloc(TEST_TRANSIENT);
    Free(transient);                       /* rilascio LIFO: torna a TEST_PERSIST */
    b = (volatile ULONG *)LoadMalloc(TEST_CUBE);

    a[0] = 0xA5A5A5A5UL;
    b[0] = 0x5A5A5A5AUL;
    if (a[0] != 0xA5A5A5A5UL || b[0] != 0x5A5A5A5AUL)
        TheEnd(TE_ARENA_BAD, "arena");
}

/* --- Sonde di M2: la scena 0 letta davvero dalla cartuccia ----------------
 * Stesso schema di M1: le attese le calcola tools/scene_parse.py, che a sua
 * volta e' la trascrizione di LoadScene() dal sorgente originale. Qui il C
 * percorre la struct vera e si confronta.
 *
 * La sonda 7 (`TAIL_OK`) non e' una sonda come le altre: e' la condizione
 * perche' le prime sette valgano qualcosa. Se il cammino non arriva in fondo
 * esatto, i valori che ha prodotto non sono "un po' sbagliati", sono letti dal
 * posto sbagliato. */
static ULONG scene_probe(ULONG *got)
{
    ULONG want[SC_EXPECT_COUNT];
    ULONG mask = 0;
    int i;

    /* L'eroe si inizializza UNA VOLTA, qui, non dentro LoadScene: dalla stanza
     * viene la sua posizione, non la sua identita'. */
    InitPerso();
    LoadScene(0);
    Scene_Derive();

    got[0] = ((ULONG)(UWORD)Scene.CubeStartX << 16) | (UWORD)Scene.CubeStartZ;
    got[1] = (UWORD)Scene.CubeStartY;
    got[2] = ((ULONG)(UWORD)Scene.CamGX << 16) | (UWORD)Scene.CamGZ;
    got[3] = ((ULONG)(UWORD)Scene.NbObjets << 16) | (UWORD)Scene.NbZones;
    got[4] = ((ULONG)(UWORD)Scene.NbChangeCube << 16) | (UWORD)Scene.NbBrickTrack;
    got[5] = ((ULONG)(UWORD)Scene.FirstCubeTarget << 16);
    got[6] = ((ULONG)Scene.Island << 16) | Scene.CubeJingle;
    got[7] = Scene.valid ? 1UL : 0UL;

    want[0] = SC_EXPECT_HERO_XZ;
    want[1] = SC_EXPECT_HERO_Y;
    want[2] = SC_EXPECT_CAM_GXGZ;
    want[3] = SC_EXPECT_COUNTS;
    want[4] = SC_EXPECT_TRACKS;
    want[5] = SC_EXPECT_CC0;
    want[6] = SC_EXPECT_JINGLE;
    want[7] = SC_EXPECT_TAIL_OK;

    for (i = 0; i < SC_EXPECT_COUNT; i++)
        if (got[i] == want[i])
            mask |= 1UL << i;
    return mask;
}

/* --- Sonde di M2b: il fondo disegnato davvero -----------------------------
 * Le attese le calcola tools/bg_ref.py, che percorre gli stessi byte della
 * cartuccia con le stesse tre decodifiche e produce anche il PNG di
 * riferimento. Qui il C rilegge il PROPRIO framebuffer.
 *
 * Le dodici sonde non sono ridondanti: si dividono il lavoro di dire *cosa* e'
 * andato storto. Riquadro diverso -> immagine traslata (camera, hot spot,
 * centratura). Riquadro uguale e conteggio diverso -> tessere mancanti.
 * Tutto uguale e somme diverse -> pixel giusti nei posti sbagliati. Una somma
 * sola avrebbe detto soltanto "no". */
static ULONG bg_probe(int step, ULONG *got)
{
    const unsigned long *want = &BgExpect[step][1];   /* [0] = SCENE, che e' un ingresso */
    ULONG mask = 0;
    int i;

    got[0]  = ((ULONG)(UWORD)BgStat.camgx << 16) | (UWORD)BgStat.camgz;
    got[1]  = ((ULONG)(UWORD)BgStat.vx << 16) | (UWORD)BgStat.vy;
    got[2]  = BgStat.gridodd;
    got[3]  = BgStat.bricks;
    got[4]  = BgStat.worklen;
    got[5]  = BgStat.celllist;
    got[6]  = BgStat.cells;
    got[7]  = ((ULONG)BgStat.bbminx << 16) | BgStat.bbminy;
    got[8]  = ((ULONG)BgStat.bbmaxx << 16) | BgStat.bbmaxy;
    got[9]  = BgStat.nonzero;
    got[10] = BgStat.sum1;
    got[11] = BgStat.sum2;
    got[12] = BgStat.clut_sum;

    for (i = 0; i < BG_EXPECT_COUNT; i++)
        if (got[i] == want[i])
            mask |= 1UL << i;
    return mask;
}

/* --- Sonde di M3a: gli ATTORI letti dalla scena ---------------------------
 * Le attese le calcola tools/scene_parse.py risolvendo ogni corpo con la
 * stessa regola che gira qui (SearchBody sulla fiche FILE3D dell'attore).
 *
 * Le due somme coprono OGNI campo che il cammino legge: flag, generico, anim,
 * le tre coordinate, l'angolo, lo sprite e lo slot HQR del corpo, per tutti gli
 * attori. Un campo largo un byte di troppo le fa cadere entrambe. I sei valori
 * isolati non sono ridondanti: dicono DOVE guardare, perche' una somma sola
 * direbbe soltanto "no".
 *
 * Nota sullo slot: `Body` di T_OBJET e' un indice in PtrBody[], non uno slot di
 * BODY.HQR (e' la forma di upstream, vedi hal.h). Il confronto usa quindi
 * HqrBodySlotOf, cioe' il numero che anche Python stampa: un valore letto sul
 * Jaguar resta confrontabile con lo stesso valore stampato dagli strumenti. */
static ULONG actor_probe(int step, ULONG *got)
{
    const unsigned long *want = ActorExpect[step];
    ULONG s1 = 0, s2 = 0;
    int n3d = 0, nspr = 0, first = 0;
    int i, k;
    T_OBJET *fo;

    for (i = 0; i < NbObjets; i++) {
        T_OBJET *o = &ListObjet[i];
        UWORD slot = (UWORD)HqrBodySlotOf(o->Body);
        ULONG w[4];

        w[0] = ((ULONG)(UWORD)o->Flags << 16) | ((ULONG)o->GenBody << 8)
               | (ULONG)o->GenAnim;
        w[1] = ((ULONG)(UWORD)o->PosObjX << 16) | (UWORD)o->PosObjZ;
        w[2] = ((ULONG)(UWORD)o->PosObjY << 16) | (UWORD)o->Beta;
        w[3] = ((ULONG)slot << 16) | (UWORD)o->Sprite;
        for (k = 0; k < 4; k++) {
            s1 += w[k];
            s2 += s1;
        }

        if (o->Body >= 0) {
            n3d++;
            if (!first && i)
                first = i;
        }
        if (o->Flags & SPRITE_3D)
            nspr++;
    }

    fo = &ListObjet[first];
    got[AC_COUNTS]  = ((ULONG)(UWORD)NbObjets << 16) | (UWORD)n3d;
    got[AC_SPRITES] = ((ULONG)(UWORD)nspr << 16) | (UWORD)(NbObjets - n3d - nspr);
    got[AC_HERO]    = ((ULONG)(UWORD)HqrBodySlotOf(ListObjet[NUM_PERSO].Body) << 16)
                      | (UWORD)ListObjet[NUM_PERSO].GenBody;
    got[AC_HERO_XZ] = ((ULONG)(UWORD)ListObjet[NUM_PERSO].PosObjX << 16)
                      | (UWORD)ListObjet[NUM_PERSO].PosObjZ;
    got[AC_FIRST]   = ((ULONG)(first & 0xFF) << 24)
                      | ((ULONG)(HqrBodySlotOf(fo->Body) & 0xFF) << 16)
                      | (UWORD)fo->Beta;
    got[AC_FIRST_XZ] = ((ULONG)(UWORD)fo->PosObjX << 16) | (UWORD)fo->PosObjZ;
    got[AC_SUM1]    = s1;
    got[AC_SUM2]    = s2;

    {
        ULONG mask = 0;
        for (i = 0; i < AC_EXPECT_COUNT; i++)
            if (got[i] == want[i])
                mask |= 1UL << i;
        return mask;
    }
}

/* --- Sonde di M3c: i CORPI disegnati sul fondo ----------------------------
 * Le attese le calcola tools/body_ref.py, che percorre gli stessi byte della
 * cartuccia con le stesse otto operazioni e produce anche il PNG di
 * riferimento.
 *
 * Le diciotto sonde si dividono il lavoro come quelle del fondo, ma su STADI
 * invece che su tipi di difetto, perche' e' quello il modo in cui una pipeline
 * di corpi sbaglia:
 *
 *   AVXY               il centro di proiezione degli attori (+23,+25 rispetto
 *                      a quello delle tessere). Se cade questa, cadono tutte.
 *   GEOM1/2            posa a riposo, matrice Beta, rotazione, proiezione
 *   SHADE1/2           luce di scena, rotazione delle normali, divisione
 *   CULL               la faccia nascosta: quanti poligoni sopravvivono
 *   ORDER1/2           chiave del pittore e ordinamento
 *   DRAWN/PIXELS       quanto e' finito a schermo
 *   FIRST/FIRST_XY     chi e' il primo attore e dove cade la sua ancora
 *   BBOX/NONZERO/SUM   il COMPOSTO, cioe' fondo + corpi
 *
 * La divisione fra i primi otto e gli ultimi cinque e' quella che conta: se
 * geometria, intensita' e ordine tengono e cade solo il composto, il difetto e'
 * nel riempimento - l'unico pezzo che alla mossa 2 lascia il posto alla GPU.
 * Senza questa separazione la sostituzione del riempitore non si potrebbe
 * verificare, perche' ogni sonda cambierebbe insieme. */
static ULONG body_probe(int step, ULONG *got)
{
    const unsigned long *want = BodyExpect[step];
    ULONG mask = 0;
    int i;

    got[BD_LIGHT]    = BodyStat.light;
    got[BD_AVXY]     = ((ULONG)(UWORD)BodyStat.avx << 16) | (UWORD)BodyStat.avy;
    got[BD_GEOM1]    = BodyStat.geom[0];
    got[BD_GEOM2]    = BodyStat.geom[1];
    got[BD_SHADE1]   = BodyStat.shade[0];
    got[BD_SHADE2]   = BodyStat.shade[1];
    got[BD_CULL]     = ((ULONG)BodyStat.tot << 16) | BodyStat.live;
    got[BD_ORDER1]   = BodyStat.order[0];
    got[BD_ORDER2]   = BodyStat.order[1];
    got[BD_DRAWN]    = ((ULONG)BodyStat.drawn << 16)
                       | (BodyStat.polys & 0xFFFFUL);
    got[BD_PIXELS]   = BodyStat.pixels;
    got[BD_FIRST]    = ((ULONG)(BodyStat.first & 0xFF) << 24)
                       | ((ULONG)(BodyStat.firstslot & 0xFF) << 16)
                       | BodyStat.firstpolys;
    got[BD_FIRST_XY] = ((ULONG)(UWORD)BodyStat.firstx << 16)
                       | (UWORD)BodyStat.firsty;
    got[BD_SPR]      = ((ULONG)SpriteStat.drawn << 16) | SpriteStat.clipped;
    got[BD_SPR_XY]   = ((ULONG)(UWORD)SpriteStat.firstx << 16)
                       | (UWORD)SpriteStat.firsty;
    got[BD_SPR_PIX]  = SpriteStat.pixels;
    got[BD_ENT]      = ((ULONG)BodyStat.lines << 16) | BodyStat.spheres;
    got[BD_ENT_XY]   = ((ULONG)(UWORD)BodyStat.firstsx << 16)
                       | (UWORD)BodyStat.firstsy;
    got[BD_ENT_PIX]  = ((ULONG)(BodyStat.firstsr & 0xFF) << 24)
                       | (BodyStat.entpix & 0xFFFFFFUL);
    got[BD_BBOX_MIN] = ((ULONG)BgStat.bbminx << 16) | BgStat.bbminy;
    got[BD_BBOX_MAX] = ((ULONG)BgStat.bbmaxx << 16) | BgStat.bbmaxy;
    got[BD_NONZERO]  = BgStat.nonzero;
    got[BD_SUM1]     = BgStat.sum1;
    got[BD_SUM2]     = BgStat.sum2;

    for (i = 0; i < BD_EXPECT_COUNT; i++)
        if (got[i] == want[i])
            mask |= 1UL << i;
    return mask;
}

/* Tutto quel che le righe mostrano, tenuto qui perche' va ridisegnato quando
 * si riaccende la diagnostica dopo averla tolta per uno screenshot. */
static ULONG hqr_got[HQR_EXPECT_COUNT], hqr_mask;
static ULONG sc_got[SC_EXPECT_COUNT], sc_mask;
static ULONG bg_got[BG_EXPECT_COUNT], bg_mask;
static ULONG ac_got[AC_EXPECT_COUNT], ac_mask;
static ULONG bd_got[BD_EXPECT_COUNT], bd_mask;
static ULONG sc_tail;
static ULONG arena_used;
static int   bg_ok;

/* Quanti tick deve durare COMPLESSIVAMENTE un profilo dei corpi prima di
 * fermarsi. Non e' un numero di ripetizioni apposta: una soglia di TEMPO si
 * adatta da se' a una stanza con un attore e a una con quattro, e ai 60 Hz del
 * ferro come ai 30 di BigPEmu. 120 tick sono 2 s sul ferro e 4 su BigPEmu -
 * abbastanza perche' anche uno stadio che vale il 5% del totale abbia sotto di
 * se' una manciata di tick invece di zero o uno. */
#define BODY_PROFILE_TICKS 120UL

/* Quale delle due pagine di diagnostica e' a schermo (il tasto B le scambia).
 * Le quarantotto righe erano piene alla fine di M2: una pagina in piu' costa
 * un ramo, mentre stringere i valori esistenti per far posto vorrebbe dire
 * rendere meno leggibile quel che gia' funziona. */
static int   diag_page;

/* A quale passo di BgExpect il fondo corrente corrisponde. 0 = la scena
 * iniziale, 1 = la stanza in cui porta la prima change-cube. */
static int   bg_step;

/* PAGINA 1: gli attori (M3a).
 *
 *   0   maschera delle sonde degli attori            atteso 000000FF
 *   1   NbObjets | attori con un corpo
 *   2   SPRITE_3D | senza corpo ne' sprite
 *   3   eroe: slot BODY.HQR | generico                atteso 00010003
 *   4   eroe: X | Z
 *   5   primo attore con corpo: n | slot | Beta
 *   6   quell'attore: X | Z
 *   7   Fletcher s1 su tutti gli attori
 *   8   Fletcher s2
 *   9   passo | corpi caricati in PtrBody[]
 *
 *   16.. un attore per riga: slot BODY.HQR | generico | Beta. Serve quando le
 *        somme cadono: dicono CHE cosa non torna, questa colonna dice CHI.
 *        Uno slot FF vuol dire "nessun corpo", che per uno SPRITE_3D o per un
 *        generico 255 e' il valore giusto e non un guasto.
 */
static void draw_page1(void)
{
    unsigned int i;

    Dbg_Value(0, ac_mask);
    for (i = 0; i < AC_EXPECT_COUNT; i++)
        Dbg_Value(1 + i, ac_got[i]);
    Dbg_Value(9, ((ULONG)bg_step << 16) | (UWORD)NbBodys);
    /* Byte occupati nell'arena per-cubo a caricamento finito. Da M3a e' la
     * risorsa che una stanza consuma davvero: scena, griglia, BLL, le fiche
     * FILE3D e un corpo per attore, tutti decompressi qui dentro. NON ha un
     * valore atteso, perche' dipende dalla stanza. */
    Dbg_Value(10, Hal_MemUsedCube());
    /* Questo invece SI', e vale 0 per qualunque stanza: e' l'arena misurata
     * subito dopo averla liberata, all'ingresso. Serve perche' la riga 10 da
     * sola non e' verificabile - cambiando stanza cambia anche il suo valore
     * legittimo, quindi "riparte" e "cresce" non si distinguono a occhio. Un
     * numero diverso da zero qui vuol dire che HQM_Free_All non sta girando, e
     * lo dice senza bisogno di confrontare due schermate. */
    Dbg_Value(11, BgStat.arena0);
    /* M3d: tessere del fondo RIDISEGNATE davanti agli attori, sommate su tutti.
     * Sta qui e non sulla pagina 2 perche' li' non c'e' piu' una riga libera, e
     * perche' e' un numero del FONDO quanto degli attori. Zero vuol dire che
     * nessun attore ha niente davanti a se': legittimo in una stanza aperta,
     * sospetto in una con muri. Le tre stanze del riferimento danno 2, 22, 49.
     * Non e' una sonda di correttezza - quella e' la somma del composto - ma
     * dice a colpo d'occhio se la passata sta girando. */
    /* STRUMENTO, non diagnostica definitiva: PASSATE | tessere.
     *
     * `overbrick` si azzera in testa a Body_DrawAll, quindi dopo un profilo di
     * N passate deve valere quel che vale UNA passata. Se dopo OPTION si legge
     * un numero grande, i due pezzi di questa riga dicono subito quale delle due
     * cose sta succedendo, e sono incompatibili fra loro:
     *
     *   passate 4, tessere ~8   -> si somma ancora: l'azzeramento non arriva
     *   passate 4, tessere 139  -> NON si somma: una passata del profilo
     *                              disegna davvero 139 tessere, cioe' il
     *                              rettangolo dell'attore e' un altro rispetto
     *                              a quello del disegno normale
     *
     * Fuori dal profilo `t_reps` resta a zero, quindi la riga si legge come
     * prima. */
    Dbg_Value(12, ((ULONG)BodyStat.t_reps << 16) | BgStat.overbrick);
    /* Riga 13: il TEMPO di M3d, in tick, gia' tolto dai bordi (pagina 2,
     * etichetta 6). Sta sotto il suo conteggio di tessere e non sulla pagina 2
     * perche' li' la colonna destra e' tutta del DSP: fino a S40 questo numero
     * si scriveva sulla riga del divisore SCLK, che lo riscriveva subito dopo.
     * Le due righe si leggono insieme: quante tessere, e quanto sono costate. */
    Dbg_Value(13, BodyStat.t_over);
    /* Riga 14: le normali che il descrittore dello stadio intensita' non ha
     * coperto (S40), sommate su tutti gli attori. DEVE valere zero, e lo dice
     * da solo: la voce di coda di shade_spans copre per costruzione quel che i
     * conteggi dei gruppi non coprono. Diverso da zero vuol dire che il kernel
     * sulla GPU ha finito le voci prima delle normali, cioe' che la sonda SHADE
     * cadrebbe per un motivo che si legge qui invece di indovinarlo. */
    Dbg_Value(14, BodyStat.shadeleft);
    /* Riga 15 (etichetta F): IL RETTANGOLO. Tre pezzi in un long,
     * `traboccamenti | rettangoli | celle`:
     *
     *   00 03 0018  -> tre rettangoli, 24 celle rimesse, nessun traboccamento
     *   01 30 0000  -> la lista si e' riempita: quel quadro ha rifatto il fondo
     *                  INTERO, e i tempi di quel quadro non si confrontano
     *
     * Le celle si leggono contro le ~756 del fondo intero: e' il rapporto che
     * dice quanto costa il ripristino, e si vede senza cronometro. */
    Dbg_Value(15, ((ULONG)BgStat.boxfull << 24) | ((ULONG)BgStat.boxes << 16)
                  | BgStat.boxcells);

    /* Gli attori arrivano fino all'etichetta E della colonna destra, non piu'
     * fino alla F: l'ultima riga della pagina serve alla decisione dell'attore
     * -sprite, e una riga che a volte c'e' e a volte la copre il 32esimo attore
     * non e' una sonda - e' un'altra cosa da verificare mentre se ne verifica
     * una. Il 32esimo attore lo si legge dalla pagina 2 se servira'. */
    for (i = 0; i < 31 && (int)i < NbObjets; i++) {
        T_OBJET *o = &ListObjet[i];
        Dbg_Value(16 + i,
                  ((ULONG)(HqrBodySlotOf(o->Body) & 0xFF) << 24)
                  | ((ULONG)o->GenBody << 16) | (UWORD)o->Beta);
    }
    /* Etichetta F della colonna DESTRA: perche' l'eroe non e' uno sprite.
     * `why | larghezza | altezza | x0>>3`; 1F = lo sprite lavora, FF = l'eroe
     * non e' arrivato alla decisione. La tabella completa sta su
     * T_BODYSTAT.sprstat in hal.h. */
    Dbg_Value(47, BodyStat.sprstat);
}

static void draw_page0(void)
{
    unsigned int i;

    Dbg_Value(0, hqr_mask);
    for (i = 0; i < HQR_EXPECT_COUNT; i++)
        Dbg_Value(1 + i, hqr_got[i]);
    Dbg_Value(15, arena_used);

    Dbg_Value(16, sc_mask);
    for (i = 0; i < SC_EXPECT_COUNT; i++)
        Dbg_Value(17 + i, sc_got[i]);
    /* Byte residui del cammino: a zero quando la sonda 7 tiene, e l'unico
     * numero utile quando non tiene. */
    Dbg_Value(25, sc_tail);

    /* BT_EMIT SPEZZATO (S41). Due righe, e si leggono insieme:
     *
     *   26   bordi | flusso      quanto di BT_EMIT e' build_edges e quanto e'
     *                            la copia dei bordi in DRAM
     *   27   BT_EMIT | M3d       il totale dello stadio e la parte che e' gia'
     *                            scorporata (le tessere davanti agli attori)
     *
     * Quel che AVANZA - 27 alto meno 26 alto meno 26 basso meno 27 basso - e'
     * la lettura del corpo dalla cartuccia piu' il calcolo di angoli e tinte.
     * Serve prima della migrazione su GPU: `build_edges` sulla GPU vale il suo
     * pezzo e nient'altro, e senza questa riga il guadagno lo prenderebbe lui
     * anche quando l'ha fatto qualcun altro.
     *
     * Stanno sulla pagina 0 perche' la 2 non ha piu' una riga libera, e sono
     * numeri che si leggono una volta e poi decidono una sessione intera. */
    Dbg_Value(26, ((ULONG)BodyStat.t_edges << 16)
                  | (BodyStat.t_stream & 0xFFFFUL));
    Dbg_Value(27, ((ULONG)BodyStat.t_stage[BT_EMIT] << 16)
                  | (BodyStat.t_over & 0xFFFFUL));
    /* 28: il RIPRISTINO del rettangolo, gia' tolto dalle due righe sopra.
     * `tempo | celle`. Scatta dentro la finestra di BT_EMIT o di M3d a seconda
     * di chi scrive per primo nel framebuffer, quindi senza scorporarlo si
     * traveste da aritmetica dei bordi - ed e' quel che ha gonfiato la lettura
     * sul ferro di S41. */
    Dbg_Value(28, ((ULONG)BgStat.t_cls << 16) | BgStat.boxcells);

    /* Le tre righe della durata, in coda alla colonna centrale perche' la terza
     * e' piena di sonde. Ogni stadio in una mezza parola: nessuno si avvicina a
     * 65535 interrupt, e cosi' la scomposizione sta in tre righe invece che in
     * cinque. measure() non ha una riga sua: e' il totale meno gli altri
     * quattro, ed e' l'unico che nel gioco vero non ci sara'. */
    /* Riga 29, mezza parola alta: [passo][Bg_Build ha completato]. Il passo
     * dice CONTRO CHE COSA si sta confrontando la maschera - senza, dopo una
     * transizione una maschera piena e una maschera piena della stanza
     * sbagliata sarebbero lo stesso disegno. */
    Dbg_Value(29, ((ULONG)bg_step << 24) | ((ULONG)(bg_ok ? 1 : 0) << 16)
                  | BgStat.t_total);
    Dbg_Value(30, ((ULONG)BgStat.t_grid << 16) | BgStat.t_mark);
    Dbg_Value(31, ((ULONG)BgStat.t_brick << 16) | BgStat.t_render);

    Dbg_Value(32, bg_mask);
    for (i = 0; i < BG_EXPECT_COUNT; i++)
        Dbg_Value(33 + i, bg_got[i]);

    /* LE DUE RIGHE DEL FRONT-END (S48), le ultime libere della pagina.
     *
     * 46 = la maschera delle undici sonde di menu_expect.h, atteso 000007FF.
     *      Copre il logo (ritaglio, posizione, riempimento, le due somme del
     *      framebuffer) e il menu (le due somme, i pixel del font, la somma
     *      dei caratteri letti da TEXT.HQR, le quattro larghezze).
     * 47 = la prima sonda che NON torna, con il suo valore letto: e' il numero
     *      che dice DI QUANTO, e senza quello una maschera storta costa un
     *      giro di build per sapere se il difetto e' nel font, nel testo o nel
     *      fondo. A maschera piena vale 0.
     *
     * Si leggono a giochi fatti, quando le due schermate non sono piu' a
     * schermo: e' il motivo per cui front_menu.c le tiene invece di
     * confrontarle e buttarle. */
    Dbg_Value(46, Front_Mask());
    {
        ULONG bad = 0;
        int k;
        for (k = 0; k < MN_EXPECT_COUNT; k++)
            if (!(Front_Mask() & (1UL << k))) {
                bad = ((ULONG)(k + 1) << 24) | (Front_Got(k) & 0x00FFFFFFUL);
                break;
            }
        Dbg_Value(47, bad);
    }
}

/* QUALE PAGINA E' A SCHERMO, e perche' non basta scriverne il numero.
 *
 * Le due pagine si somigliano: sedici righe esadecimali per colonna, e alcune
 * righe hanno lo stesso indice con un significato diverso (la 10 e' il
 * contatore degli interrupt sulla 0 e i byte dell'arena sulla 1). Senza un
 * segno, un numero che cambia non dice a quale delle due appartiene - ed e' la
 * prima cosa in cui si e' inciampati provando M3a.
 *
 * Il segno e' un CONTEGGIO, non un colore e nemmeno una cifra: un blocco per la
 * pagina 0, due per la pagina 1, sopra il righello del marcatore. E' lo stesso
 * idioma dei lampi delle eccezioni (il numero di lampi e' il numero del
 * vettore), e si legge da uno screenshot senza sapere che colore aspettarsi.
 */
#define PAGE_TAG_Y     48
#define PAGE_TAG_W     16
#define PAGE_TAG_H     16
#define PAGE_TAG_X0     8
#define PAGE_TAG_STEP  20
#define PAGE_TAG_SLOTS  4

static void draw_page_tag(void)
{
    int i;

    Dbg_Fill(PAGE_TAG_X0, PAGE_TAG_Y, PAGE_TAG_STEP * PAGE_TAG_SLOTS,
             PAGE_TAG_H, COL_BG);
    for (i = 0; i <= diag_page; i++)
        Dbg_Fill(PAGE_TAG_X0 + i * PAGE_TAG_STEP, PAGE_TAG_Y,
                 PAGE_TAG_W, PAGE_TAG_H, COL_RULER);
}

/* PAGINA 2: i corpi (M3c).
 *
 *   0     maschera delle sonde dei corpi               atteso 00FFFFFF
 *   1..24 le sonde, nell'ordine di body_expect.h
 *   25..31 diagnostica
 *   32..39 costo per stadio
 *   40..47 i due numeri del DSP
 *
 * La maschera si legge a blocchi: se i bit 2..8 (geometria, intensita',
 * ordine) sono alti e i 19..23 (il composto) no, il difetto e' nel
 * riempimento e non nella pipeline. */
/* Copia del divisore del clock seriale. SCLK e' di sola scrittura, quindi il
 * valore corrente vive qui. Parte da 19, che e' quel che src/dsp_audio.s
 * programma al boot: se i due divergessero, la prima pressione di SU farebbe
 * un salto invece di un passo. */
static ULONG sclk_now = 19;

static void audio_probe(void);

static void draw_page2(void)
{
    unsigned int i;

    /* LE RIGHE 1..BD_EXPECT_COUNT SONO DELLE SONDE, e la diagnostica comincia
     * dopo. Aggiungendone tre in S36 la tabella e' arrivata a riga 21 e le due
     * righe sotto la riscrivevano: la maschera diceva "SUM1 non torna" e il
     * valore che avrebbe detto DI QUANTO non c'era piu'. Le tre di S37 (linee e
     * sfere) portano la tabella a 24, e il posto e' venuto dalle quattro righe
     * di perizia sulla GPU piantata - vedi sotto. Il blocco della diagnostica
     * parte da 25 e riempie esatta la colonna centrale (25..31). */
    Dbg_Value(0, bd_mask);
    for (i = 0; i < BD_EXPECT_COUNT; i++)
        Dbg_Value(1 + i, bd_got[i]);
    Dbg_Value(25, ((ULONG)BodyStat.drawn << 16) | (UWORD)NbBodys);
    /* La GPU che non risponde (M3c mossa 2). A zero = ha risposto sempre;
     * diversa da zero = quante volte non l'ha fatto.
     *
     * LE QUATTRO RIGHE DI PERIZIA SONO VIA (S37), e non perche' non servissero:
     * dicevano dove si era piantata la prima volta (poligoni gia' fatti, bordi,
     * ymin/ymax scritti dal 68000 e quelli LETTI dalla GPU) ed e' con loro che
     * S33 ha trovato tutti e quattro i difetti della stretta di mano. Da S34 il
     * lancio e' uno per attore e quelle quattro righe stanno a zero da tre
     * sessioni, mentre le quarantotto righe della pagina erano piene. I campi
     * di BodyStat restano riempiti: se questa riga torna a muoversi, rimetterle
     * a schermo e' una riga per una (hangwhere, hangwhat, hangsaw, hangsawc). */
    /* DUE KERNEL, DUE META' (S40): intensita' <<16 | riempimento. Sono
     * incompatibili fra loro apposta - un numero solo non direbbe QUALE dei due
     * non ha risposto, e da S40 la GPU e' lanciata due volte per attore. */
    Dbg_Value(26, (BodyStat.shadehang << 16)
                  | (BodyStat.gpuhang & 0xFFFFUL));
    /* IL LANCIO SINGOLO, MISURATO SUL FERRO (S34).
     *   27 = lanci <<16 | poligoni accodati. Il secondo numero e' quanti lanci
     *        sarebbero stati prima di S34, quindi il rapporto fra i due dice a
     *        colpo d'occhio quante strette di mano sono sparite. Un lancio per
     *        attore vuol dire che il primo numero e' la riga 25 qui sopra.
     *   28 = byte di flusso del piu' grosso attore. Se si avvicina a
     *        JOB_STREAM (32768 da S37, che le tratte di linee e sfere hanno
     *        portato l'eroe da 12704 a 19600) qualche attore si sta facendo due
     *        lanci invece di uno: non e' un guasto, e' il momento di allargare
     *        la regione. */
    Dbg_Value(27, (BodyStat.launches << 16) | (BodyStat.jobs & 0xFFFFUL));
    Dbg_Value(28, BodyStat.batchmax);
    /* Il preclip di AffScene: quanti attori con un corpo sono stati scartati
     * senza toccarne un vertice, e quanti hanno davvero prodotto pixel. Il
     * rapporto fra i due e' la misura del lavoro che prima si buttava via. */
    Dbg_Value(29, ((ULONG)BodyStat.clipped << 16) | BodyStat.drawn);
    /* 30 = attori scartati per INVISIBLE. Sta su una riga sua e non insieme al
     * preclip perche' risponde a una domanda diversa: il preclip non toglie un
     * pixel, questo si'. Un numero diverso da zero e' l'unico modo per
     * distinguere "il taglio funziona" da "in questa stanza non ce n'erano":
     * atteso 0 nelle scene 0 e 1, e 1 nella scena 2 (l'attore 17). */
    Dbg_Value(30, BodyStat.invis);
    /* 31 = tratte di linea e sfera accodate <<16 | quante ne ha l'attore piu'
     * ricco. Non e' una sonda di correttezza (quella e' ENT_PIX): dice quanto
     * costa la scelta di far passare le entita' dal riempitore dei poligoni,
     * cioe' il numero da guardare se un giorno serve un lavoro compatto per le
     * tratte invece di venti long per una riga sola. */
    Dbg_Value(31, BodyStat.entpix);

    /* IL COSTO PER STADIO (S34), colonna 2. Etichetta 0 = passate | Hz, poi i
     * sette stadi nell'ordine in cui girano, cosi' la colonna si legge come la
     * pipeline: posa, vertici, intensita', faccia nascosta, ordinamento, bordi,
     * GPU.
     *
     * SONO TICK GREZZI, sommati su tutte le passate. Non si convertono qui in
     * millisecondi apposta: il font e' esadecimale, e un tempo in centesimi di
     * millisecondo scritto in esadecimale e' un numero che nessuno legge. I
     * tick sono numeri piccoli, si leggono a colpo d'occhio, e la divisione per
     * passate e Hz si fa fuori - dove non costa una chiamata a libgcc e non si
     * puo' sbagliare la base. */
    {
        unsigned int s;
        Dbg_Value(32, (BodyStat.t_reps << 16) | (ULONG)JAG_HAL_TICK_HZ);
        for (s = 0; s < BODY_TSTAGES; s++)
            Dbg_Value(33 + (int)s, BodyStat.t_stage[s]);
        /* M3d DA SOLO (gia' tolto dai bordi, etichetta 6) NON STA QUI, e la
         * ragione e' un difetto trovato in S40: era sulla riga 45, che e' il
         * divisore SCLK dell'audio, e `audio_probe()` gira subito dopo e la
         * riscriveva. La colonna destra e' tutta del DSP da 40 a 47 - il
         * commento di S39 che diceva "le etichette E ed F restano libere"
         * guardava una pagina che non era piu' quella.
         *
         * Sta sulla PAGINA 1, riga 13, accanto al conteggio delle tessere M3d
         * che e' sulla 12: il tempo e il lavoro dello stesso stadio si leggono
         * insieme o non si leggono. */
    }

    audio_probe();
}

/* ---------------------------------------------------------------------------
 * I DUE NUMERI DEL DSP (S35), colonna 3.
 *
 * Il piano audio poggiava su una stima a mente ("~100 istruzioni per campione,
 * sembra alla portata"). Qui quella stima diventa una misura, e le due
 * grandezze si leggono a FINESTRA invece che dall'avvio: un contatore
 * cumulativo diviso per il tempo acceso e' zavorrato dal boot e SALE per
 * un'ora senza che niente sia migliorato (feedback_media_cumulativa_boot).
 *
 * Riga 40  interruzioni I2S al secondo = la frequenza di campionamento VERA.
 *          Il TRM con SCLK=19 dice ~20,8 kHz, il codice da cui l'ISR discende
 *          diceva 22050: questa riga arbitra, e nessuno dei due e' fidato.
 * Riga 41  istruzioni libere per campione. E' un LIMITE INFERIORE, non una
 *          stima: le conta un ciclo di tre istruzioni di cui una e' un salto
 *          preso, cioe' il mix piu' lento che si possa scrivere. Il
 *          sintetizzatore vero, fatto di aritmetica in fila, ne avra' di piu'.
 * Riga 42  il contatore grezzo delle interruzioni. Serve a distinguere "il DSP
 *          e' fermo" (resta a zero) da "il DSP gira ma la finestra non si e'
 *          ancora chiusa" (cresce, con le due righe sopra a zero).
 * ------------------------------------------------------------------------- */

/* Istruzioni per blocco del ciclo di riposo: DSP_IDLE_INNER * 3, e le due
 * costanti stanno in src/dsp_audio.s. Sono duplicate qui apposta e non
 * generate: e' l'unico punto di contatto e un commento sbagliato si vedrebbe
 * subito, perche' il numero uscirebbe fuori scala di un fattore tondo. */
#define DSP_IDLE_BLOCK_INSTR  (1024UL * 3UL)

static void audio_probe(void)
{
    static ULONG prev_tick, prev_irq, prev_idle;
    static ULONG rate, freeper;

    ULONG irq  = JAG_HAL_DSP_IRQ_CNT;
    ULONG idle = JAG_HAL_DSP_IDLE_CNT;
    ULONG tick = JAG_HAL_VBL_TICK;
    ULONG hz   = JAG_HAL_TICK_HZ;

    if (prev_tick == 0 || tick < prev_tick) {
        prev_tick = tick; prev_irq = irq; prev_idle = idle;
    } else if (hz && (tick - prev_tick) >= hz) {
        ULONG dt   = tick - prev_tick;
        ULONG dirq = irq - prev_irq;
        rate = (dirq * hz) / dt;
        freeper = dirq ? ((idle - prev_idle) * DSP_IDLE_BLOCK_INSTR) / dirq : 0;
        prev_tick = tick; prev_irq = irq; prev_idle = idle;
    }

    Dbg_Value(40, rate);
    Dbg_Value(41, freeper);
    Dbg_Value(42, irq);
    /* Riga 43: quante volte l'interruzione ha trovato il buffer VUOTO. Deve
     * restare a zero. E' l'unica cosa che distingue "il sintetizzatore e'
     * lento" da "il sintetizzatore e' sbagliato": suonano uguale tutti e due,
     * ma solo il primo fa salire questo numero. */
    Dbg_Value(43, JAG_HAL_DSP_STARVE);
    /* Riga 44: quanti operatori OPL2 il DSP sta calcolando per campione. Si
     * alza col tasto PAUSE, a caldo. E' l'unico modo di rispondere a "diciotto
     * ci stanno?" MISURANDO invece di moltiplicare per diciotto il costo di
     * uno: si sale finche' la riga sopra (la fame) non comincia a crescere, e
     * l'ultimo valore che la tiene a zero e' il massimo vero. Regolarlo a caldo
     * invece che con una costante fa la differenza fra una spazzata e trenta
     * masterizzazioni. */
    Dbg_Value(44, JAG_HAL_DSP_OPS);
    /* Riga 45: il divisore del clock seriale, con SU e GIU'. La frequenza che
     * ne risulta NON si legge qui ma sulla riga 8, che e' contata: cosi' se la
     * regola del TRM sbagliasse un'altra volta se ne accorgerebbe la sonda e
     * non il progetto. */
    Dbg_Value(45, sclk_now);
    /* Riga 46: la cadenza dell'inviluppo, con SINISTRA e DESTRA. Zero = a ogni
     * campione. La maschera si legge come "una volta ogni maschera+1". */
    Dbg_Value(46, JAG_HAL_DSP_ENV_MASK);
    /* Riga 47: il guadagno d'uscita in bit, col tasto A. 4 = x16 = un operatore
     * a piena scala. Se la durezza del suono dipende da qui, e' quantizzazione;
     * se non cambia, e' la forma d'onda e il sospettato e' `sh`. */
    Dbg_Value(47, JAG_HAL_DSP_GAIN);
}

static void draw_rows(void)
{
    if (diag_page == 2)
        draw_page2();
    else if (diag_page)
        draw_page1();
    else
        draw_page0();
    draw_page_tag();
}

/* Il fondo E i corpi. Serve a ogni ridisegno che non passa da Bg_Build: senza
 * la seconda riga il tasto che toglie la diagnostica per lo screenshot
 * lascerebbe la scena senza personaggi, cioe' proprio l'immagine che si voleva
 * confrontare. Le sonde non ne risentono: sono gia' state copiate in bd_got. */
static void redraw_scene(void)
{
    Bg_Redraw();
    Body_DrawAll();
}

ULONG engine_init(void)
{
    if (data_probe != 0x12345678UL)
        return ERR_DATA;
    if (bss_probe != 0)
        return ERR_BSS;
    if (rodata_probe != 0x0BADC0DEUL)
        return ERR_RODATA;

    marker_slot = 0;

    /* Il canale dell'emulatore parte vuoto: la DRAM al boot non e' zero, e un
     * bit rimasto su sarebbe un pulsante fantasma premuto per sempre. */
    JAG_HAL_EMU_PAD = 0;

    /* Prima cosa in assoluto: i due indici della diagnostica. Da M2 la CLUT
     * diventa la palette di LBA1, ma se la palette non si caricasse resterebbe
     * la rampa di grigi del lato RMAC, dove l'indice 2 e' quasi nero - cioe'
     * l'unico strumento per leggere il guasto dipenderebbe dal dato che manca.
     * Forzarli qui rende la diagnostica leggibile in ogni caso. */
    Bg_ClutDiag();

    arena_setup();
    arena_used = (Hal_MemUsedPersistent() << 16) | Hal_MemUsedCube();

    HQR_Init();
    hqr_mask = hqr_probe(hqr_got);
    sc_mask  = scene_probe(sc_got);
    sc_tail  = (ULONG)(Scene.tail - (long)(UWORD)Scene.NbBrickTrack * 6);

    /* Il fondo NON si costruisce qui. Vedi engine_frame. */
    return JAG_ENGINE_MAGIC;
}

/* Diagnostica accesa? Il tasto C la toglie e la rimette.
 *
 * Serve perche' i due modi di verificare il fondo chiedono due schermi
 * incompatibili: la maschera delle sonde vuole le righe esadecimali, il
 * confronto contro il PC DOS vuole la scena e nient'altro
 * ([[feedback_visual_over_metric]]). Spegnerla ridisegna il fondo - solo i
 * blit, la griglia e le tessere restano - e riaccenderla riscrive le righe.
 * Cosi' non servono due build, e soprattutto e' LO STESSO avvio a produrre
 * entrambe le prove: due build sono due cose che possono differire. */
static int diag_on = 1;

/* MODALITA' DIAGNOSTICA, tasto '#' del tastierino (S42).
 *
 * Da qui in poi il pad appartiene al GIOCO, e non e' una comodita': fino a S41
 * ogni tasto muoveva una sonda - A cambiava stanza, B girava le pagine, SU
 * accendeva l'orologio, SINISTRA/DESTRA scorrevano i fotogrammi - quindi
 * l'eroe non aveva un solo tasto libero per camminare. I due modi si escludono
 * a vicenda apposta: un pad che comanda due cose insieme e' un difetto che si
 * presenta come "il gioco fa qualcosa che non ho chiesto".
 *
 * Il '#' e non un pulsante: LBA1 non lo usa. Il tastierino serve ai
 * comportamenti (1-4), il resto dei tasti al gioco, e il '*' resta libero per
 * il giorno che serva una seconda leva di servizio.
 *
 * ZERO all'avvio: si parte in modalita' GIOCO, schermo pulito. Chi accende la
 * console vede la stanza e Twinsen, non sedici righe esadecimali. */
static int dbg_mode;

/* IL FONDO SI COSTRUISCE QUI, NON IN engine_init, E IL MOTIVO E' L'OP.
 *
 * Il primo tentativo lo costruiva dentro engine_init, e ha dato uno schermo
 * verde uniforme: il colore di BG, cioe' l'OP che non disegnava niente.
 *
 * La ragione e' la scoperta di S27, applicata a un caso nuovo: l'OP CONSUMA
 * l'oggetto mentre lo disegna, azzerandone HEIGHT, ed e' ViHandler a
 * ripristinarlo a ogni interrupt ([[feedback_second_field_poll_height]]).
 * engine_init gira con gli interrupt mascherati. Finche' e' durata
 * millisecondi la cosa non si e' vista; adesso dura secondi, quindi dopo il
 * primo campo HEIGHT resta a zero e il fondo sparisce per tutto il
 * caricamento. Non era un guasto della costruzione - la scena, sotto, si stava
 * disegnando benissimo.
 *
 * Spostandola al primo giro di MainLoop gli interrupt sono accesi, ViHandler
 * riarma l'oggetto e si vede il caricamento mentre avviene, quadratini di fase
 * compresi. Ed e' anche l'unico posto da cui la durata si puo' MISURARE, senza
 * cronometro: si contano gli interrupt video passati. */
static int bg_built;

/* Il front-end e' passato (S48). Separato da bg_built perche' sono due
 * transizioni diverse: questa dice "l'utente ha scelto di giocare", quella
 * dice "la stanza e' costruita". Metterle insieme vorrebbe dire caricare
 * mentre il menu e' ancora a schermo. */
static int front_done;

/* ---------------------------------------------------------------------------
 * LA POSA DEL PASSO (M4). La risposta alla domanda del tempo, lato Jaguar.
 *
 * Le attese di body_expect.h sono calcolate CON una posa precisa: se qui gli
 * attori ne assumessero un'altra, ogni sonda della geometria cadrebbe insieme e
 * nessuna direbbe perche'. Percio' la coppia (fotogramma, passo) non e' scritta
 * in questo file: esce dallo stesso generatore delle attese, in `BodyAnimStep`.
 *
 * Il PASSO non si scrive in TimerRef - che e' l'orologio del gioco e appartiene
 * all'HAL - ma si sottrae: `AnimMemoTicks = TimerRef - passo` fa si' che
 * `TimerRef - AnimMemoTicks` valga esattamente il passo voluto. pose_load non
 * ha quindi un ramo "di collaudo": e' la stessa funzione che girera' quando
 * l'orologio comincera' a muoversi da solo.
 *
 * Il fotogramma si prende modulo la lunghezza dell'animazione di CIASCUNO: ogni
 * attore ha la sua e "fotogramma 3" non esiste per tutti. La stessa regola sta
 * in tools/scene_parse.py:actor_pose_frames, ed e' la sola cosa che i due lati
 * calcolano ognuno per conto proprio: se divergessero, le sonde della geometria
 * lo direbbero al primo passo.
 * ------------------------------------------------------------------------- */
/* Fotogrammi IN PIU' rispetto alla posa del passo, mossi a mano con SINISTRA e
 * DESTRA sulla pagina 1. A zero - ed e' dove si torna tenendo premuto SINISTRA
 * - la posa e' esattamente quella su cui body_expect.h e' stato calcolato, e le
 * ventiquattro sonde valgono. Sopra lo zero si sta guardando l'animazione a
 * mano: le sonde NON si rifanno apposta, cosi' la maschera resta quella
 * dell'ultima posa confrontabile invece di diventare rossa per costruzione. */
static int anim_bump;

/* L'OROLOGIO ACCESO (S41), tasto SU fuori dalla pagina dell'audio.
 *
 * Si ACCENDE, non e' il comportamento predefinito, e la ragione non e' la
 * prudenza: con il tempo che gira il fotogramma cambia a ogni quadro, quindi
 * `body_probe` confronterebbe una posa diversa da quella su cui body_expect.h
 * e' calcolato e le ventiquattro sonde diventerebbero rosse per costruzione.
 * Spento, il gioco torna alla posa canonica del passo e le sonde tornano a
 * voler dire qualcosa: e' un A/B su un tasto, non due build.
 *
 * Fino a che il ripristino a rettangolo (`ClsBoxes`, voce 1 del todo) non c'e',
 * ogni fotogramma nuovo si porta dietro `Bg_Redraw`, cioe' il fondo rifatto
 * dai brick per tutto lo schermo. Si vedra' l'animazione a passo di lumaca: e'
 * il numero che il rettangolo dovra' smentire, e averlo sotto gli occhi PRIMA
 * e' il punto. */
static int anim_clock;

static void set_step_pose(int step)
{
    WORD frame, cur;
    int n;

    if (step < 0 || step >= BD_ANIM_STEPS)
        return;
    frame = (WORD)(BodyAnimStep[step][0] + anim_bump);
    /* Un fotogramma mosso a mano si guarda ARRIVATO, non a meta' strada: cosi'
     * si scorrono le pose che l'animazione contiene davvero. */
    cur   = anim_bump ? (WORD)0x7FFF : (WORD)BodyAnimStep[step][1];

    for (n = 0; n < NbObjets; n++) {
        T_OBJET *o = &ListObjet[n];
        const unsigned char *a;
        UWORD nbf;

        if (o->Anim < 0)
            continue;
        a = (const unsigned char *)HQR_Get(HQR_ANIMS, o->Anim);
        if (!a) {
            o->Anim = -1;
            continue;
        }
        nbf = (UWORD)(a[0] | ((UWORD)a[1] << 8));   /* little endian, come l'archivio */
        if (nbf == 0) {
            o->Anim = -1;
            continue;
        }
        o->Frame = (WORD)((UWORD)frame % nbf);
        o->AnimSrcFrame = o->Frame > 0 ? (WORD)(o->Frame - 1) : o->Frame;
        o->AnimMemoTicks = TimerRef - (ULONG)(UWORD)cur;
    }
}

/* Chiamata una volta per interrupt video da MainLoop. */
void engine_frame(void)
{
    unsigned int prev = marker_slot;

    /* IL FRONT-END PRIMA DI TUTTO (S48): logo Adeline, menu, e solo dopo la
     * stanza. Finche' non e' finito il resto del quadro non gira - il motore
     * non ha ancora una scena da far girare, e il caricamento (secondi) deve
     * cominciare quando l'utente sceglie "New game", non prima.
     *
     * Sta QUI e non in engine_init per la stessa ragione del fondo, appena
     * sotto: engine_init gira con gli interrupt mascherati, e un'immagine
     * lasciata a schermo li' sparirebbe dopo il primo campo perche' nessuno
     * riarma l'oggetto dell'OP ([[feedback_second_field_poll_height]]). */
    if (!front_done) {
        if (!Front_Frame())
            return;
        front_done = 1;
        return;                 /* la stanza comincia dal quadro dopo */
    }

    if (!bg_built) {
        ULONG t0 = JAG_HAL_VBL_TICK;
        bg_built = 1;
        bg_step = 0;
        bg_ok = Bg_Build((int)BgExpect[0][BGX_SCENE]);
        set_step_pose(bg_step);
        /* Le sonde del FONDO si leggono qui, prima che i corpi tocchino il
         * framebuffer. Dopo sarebbe troppo tardi: un fondo giusto con i corpi
         * sbagliati e un fondo gia' sbagliato darebbero lo stesso "no". */
        bg_mask = bg_probe(bg_step, bg_got);
        ac_mask = actor_probe(bg_step, ac_got);
        Body_ProfileReset();
        Body_PinArm();          /* la fotografia del cursore E' di questa passata */
        Body_DrawAll();
        Bg_Measure();                   /* le stesse cinque misure, sul composto */
        BgStat.t_total = (UWORD)(JAG_HAL_VBL_TICK - t0);
        bd_mask = body_probe(bg_step, bd_got);
        Dbg_Clear();
        if (dbg_mode && diag_on)
            draw_rows();
        return;
    }

    Hal_UpdateTimerRef();
    Hal_ReadPad();

    /* '#': si entra e si esce dalla diagnostica.
     *
     * Uscendo va ridisegnata la scena: `Dbg_Clear` azzera la cache dei valori,
     * non i pixel, quindi senza `redraw_scene` i glifi resterebbero stampati
     * sopra la stanza. E' la stessa ragione per cui il tasto C lo fa gia'. */
    if (PadKeysEdge & PADKEY_HASH) {
        dbg_mode = !dbg_mode;
        redraw_scene();
        Dbg_Clear();
        if (dbg_mode && diag_on)
            draw_rows();
        return;
    }

    /* IL QUADRO DI GIOCO (S42, M6).
     *
     * L'ordine e' quello di upstream: la gestione degli attori PRIMA del
     * disegno (PERSO.C:MainLoop -> DoDir -> ... -> AffScene). L'orologio qui e'
     * sempre acceso: `anim_clock` e' un interruttore della diagnostica, e serve
     * li' perche' con il tempo che gira le sonde della posa canonica cadrebbero
     * per costruzione. In modalita' gioco quelle sonde non si leggono. */
    if (!dbg_mode) {
        Game_Frame();
        Body_AnimTick();
        /* La camera DOPO che gli attori si sono mossi e PRIMA del disegno, come
         * in PERSO.C:1132-1164: il riquadro si prova sulla posizione nuova, e
         * quel che si disegna e' gia' inquadrato bene. Invertire i due darebbe
         * un quadro di ritardo, cioe' l'eroe che tocca il bordo e ci resta per
         * un quadro intero - a 2 fps, mezzo secondo. */
        Bg_CameraFollow();
        Body_DrawAll();
        return;
    }

    /* LA TRANSIZIONE, col grilletto premuto a mano.
     *
     * Nel gioco vero questa change-cube sta in fondo alla stanza e ci si arriva
     * dopo i primi combattimenti, cioe' dopo M3, M5 e M6. Aspettare di poterla
     * attraversare camminando vorrebbe dire lasciare l'ultimo criterio di M2
     * non verificato per tre milestone.
     *
     * Quel che si scavalca e' il MODO di entrarci, non il dato: la scena di
     * destinazione e la posizione d'ingresso continuano a venire dalla zona di
     * tipo 0 letta dalla cartuccia. Se fosse hardcodata la destinazione il
     * criterio ("la stanza GIUSTA, non N+1") non proverebbe piu' niente; cosi'
     * invece prova esattamente quel che deve. */
    /* A sulla pagina 2: il guadagno d'uscita, da x2 a x16.
     *
     * Sulla pagina dell'audio il tasto A cambia mestiere, e la change-cube (il
     * suo uso normale) resta sulle altre due pagine. Non e' una scorciatoia
     * per mancanza di tasti: e' che con un solo operatore acceso il picco e' il
     * 6% della scala del convertitore, e su BigPEmu quei gradini li nasconde il
     * ricampionamento mentre sul Jaguar vanno dritti al DAC. Questo tasto e'
     * l'esperimento che dice se il "saw" e' quantizzazione o forma d'onda. */
    if (diag_page == 2 && (PadEdge & PAD_A)) {
        ULONG g = JAG_HAL_DSP_GAIN + 1;
        JAG_HAL_DSP_GAIN = (g > 4) ? 1 : g;
    }

    if (diag_page != 2 && (PadEdge & PAD_A) && bg_step + 1 < BG_EXPECT_STEPS) {
        ULONG t0 = JAG_HAL_VBL_TICK;
        int next = bg_step + 1;
        int target = (int)BgExpect[next][BGX_SCENE];

        /* COME si arriva al passo lo dice il riferimento, non il codice.
         * `BgEnter[next]` distingue "entro da una porta" (posizione data dalla
         * zona) da "carico la stanza da capo" (posizione di partenza sua). Un
         * passo che non entra da una porta e non e' N+1 - la scena 2, dove si
         * verifica il criterio di M3 - non e' deducibile dal numero di scena,
         * e indovinarlo darebbe la stanza giusta inquadrata dal posto
         * sbagliato: esattamente il difetto che M2 ha chiuso leggendo
         * ChangeCube invece di dedurlo. */
        if (BgEnter[next] && Scene.FirstCubeTarget >= 0)
            Bg_EnterAt(Scene.FirstCubeEnterX, Scene.FirstCubeEnterY,
                       Scene.FirstCubeEnterZ);

        bg_ok = Bg_Build(target);
        bg_step = next;
        /* Il passo nuovo riparte dalla sua posa canonica: le sonde che si
         * leggono qui sotto sono calcolate su quella, e un fotogramma avanzato
         * a mano nella stanza precedente le farebbe cadere tutte. */
        anim_bump = 0;
        set_step_pose(bg_step);
        bg_mask = bg_probe(bg_step, bg_got);
        ac_mask = actor_probe(bg_step, ac_got);
        Body_ProfileReset();
        Body_DrawAll();
        Bg_Measure();
        BgStat.t_total = (UWORD)(JAG_HAL_VBL_TICK - t0);
        bd_mask = body_probe(bg_step, bd_got);
        Dbg_Clear();
        if (diag_on)
            draw_rows();
        return;
    }

    /* Il tasto B scambia le due pagine della diagnostica.
     *
     * `Bg_Redraw` PRIMA, e non e' un di piu': `Dbg_Clear` azzera la cache dei
     * valori mostrati, non i pixel. Le due pagine non scrivono le stesse righe
     * (la 1 ne lascia libere alcune della colonna sinistra), quindi senza
     * ripassare il fondo la pagina nuova resterebbe sotto i resti della
     * vecchia - ed e' esattamente il motivo per cui le due pagine sembravano
     * indistinguibili al primo collaudo. Sul tasto A il problema non si vedeva
     * perche' li' il fondo lo ridisegna Bg_Build. */
    /* PAUSE: un operatore OPL2 in piu' per campione, e si riparte da uno dopo
     * il trentaduesimo (che e' quanti ne tiene op_state in src/dsp_audio.s).
     * Il DSP rilegge questa cella a ogni campione, quindi si sente e si misura
     * all'istante. Mai zero: lo scriverebbe in un conto alla rovescia. */
    if (PadEdge & PAD_PAUSE) {
        ULONG n = JAG_HAL_DSP_OPS + 1;
        JAG_HAL_DSP_OPS = (n > 32) ? 1 : n;
    }

    /* SU e GIU' sulla pagina 2: la frequenza di campionamento, a caldo.
     *
     * Serve a misurare la SECONDA dimensione del budget invece di calcolarla.
     * Il costo dell'interruzione e' fisso per interruzione, quindi abbassare
     * Fs guadagna piu' del solo allungamento del periodo - ma di quanto di
     * piu' dipende da un numero che non abbiamo mai misurato. Con due spazzate
     * a due frequenze diverse quel numero esce da se'.
     *
     * SU alza il divisore, cioe' ABBASSA la frequenza. Il verso e' quello del
     * registro, non quello dell'orecchio: si legge il risultato sulla riga 8,
     * che e' misurata, invece di fidarsi dell'etichetta. */
    /* SINISTRA e DESTRA sulla pagina 2: ogni quanti campioni gira l'inviluppo.
     *
     * La maschera vale 0, 1, 3, 7, 15, cioe' "ogni campione" fino a "ogni
     * sedici". E' la leva che decide se l'OPL2 intero ci sta: la macchina a
     * stati dell'inviluppo costa quanto l'operatore, e l'orecchio non
     * distingue un inviluppo aggiornato 20.000 volte al secondo da uno
     * aggiornato 2.500. Con questo tasto la differenza si SENTE e si MISURA
     * nella stessa spazzata, invece di doverla dare per buona.
     *
     * Effetto collaterale voluto: rallentando la cadenza rallenta anche il
     * pizzicato, perche' i passi restano gli stessi. Serve a vedere a colpo
     * d'occhio che il tasto ha fatto qualcosa. */
    /* SINISTRA e DESTRA sulla PAGINA 1 (quella degli attori): l'animazione, un
     * fotogramma alla volta. E' la prima cosa del progetto che si guarda
     * MUOVERSI invece di leggerla in esadecimale, e non serve un orologio che
     * gira: la posa e' una funzione del fotogramma, quindi scorrere i
     * fotogrammi a mano e' gia' l'animazione, solo comandata dal pollice.
     *
     * Tenendo premuto SINISTRA si torna a zero, cioe' alla posa su cui le sonde
     * sono calcolate. */
    if (diag_page == 1 && (PadEdge & (PAD_LEFT | PAD_RIGHT))) {
        anim_bump += (PadEdge & PAD_RIGHT) ? 1 : -1;
        if (anim_bump < 0)
            anim_bump = 0;
        set_step_pose(bg_step);
        redraw_scene();
        Dbg_Clear();
        if (diag_on)
            draw_rows();
        return;
    }

    if (diag_page == 2 && (PadEdge & (PAD_LEFT | PAD_RIGHT))) {
        ULONG m = JAG_HAL_DSP_ENV_MASK;
        if (PadEdge & PAD_RIGHT)
            m = (m >= 15) ? 15 : (m << 1) | 1;
        else
            m = (m == 0) ? 0 : (m >> 1);
        JAG_HAL_DSP_ENV_MASK = m;
    }

    if (diag_page == 2 && (PadEdge & (PAD_UP | PAD_DOWN))) {
        if ((PadEdge & PAD_UP) && sclk_now < JAG_SCLK_MAX)
            sclk_now++;
        else if ((PadEdge & PAD_DOWN) && sclk_now > JAG_SCLK_MIN)
            sclk_now--;
        HW_L(JAG_SCLK) = sclk_now;
    }

    if (PadEdge & PAD_B) {
        diag_page++;
        if (diag_page > 2)
            diag_page = 0;
        redraw_scene();
        Dbg_Clear();
        if (diag_on)
            draw_rows();
    }

    /* OPTION: il profilo a ripetizione.
     *
     * Una passata sola dura pochi tick e divisa in sette stadi darebbe una fila
     * di zeri e uni - cioe' un numero che non distingue "costa poco" da "costa
     * meno della risoluzione". Qui la pipeline si rifa finche' il totale non
     * supera la soglia, e allora ogni stadio ha abbastanza tick sotto di se'
     * perche' il suo valore voglia dire qualcosa.
     *
     * Si puo' ripetere perche' Body_DrawAll e' idempotente: azzera le sue sonde
     * in testa e riscrive gli stessi pixel sullo stesso fondo. L'ultima passata
     * lascia quindi lo schermo e le sonde esattamente com'erano.
     *
     * Lo schermo resta fermo per la durata del profilo. Non e' un guasto: e' il
     * MainLoop che non gira, ed e' il prezzo dichiarato della misura. */
    if (PadEdge & PAD_OPTION) {
        ULONG t0 = JAG_HAL_VBL_TICK;

        Body_ProfileReset();
        Body_PinArm();          /* il cursore della PRIMA passata del profilo */
        do {
            Body_DrawAll();
        } while (JAG_HAL_VBL_TICK - t0 < BODY_PROFILE_TICKS);

        /* NIENTE redraw_scene qui: chiama Body_DrawAll, cioe' aggiungerebbe una
         * passata al profilo che ha appena misurato. E non serve - la pagina 2
         * scrive sempre le stesse righe, nessuna sparisce, e ogni glifo si
         * ridisegna il proprio fondo.
         *
         * body_probe invece si rifa apposta: le sonde dell'ultima passata
         * devono essere quelle di sempre. Se il profilo avesse rotto
         * l'idempotenza, bd_mask cambierebbe e si vedrebbe subito. */
        bd_mask = body_probe(bg_step, bd_got);
        Dbg_Clear();
        if (diag_on)
            draw_rows();
    }

    if (PadEdge & PAD_C) {
        diag_on = !diag_on;
        redraw_scene();
        Dbg_Clear();
        if (diag_on)
            draw_rows();
    }

    /* SU: l'orologio si accende e si spegne. Sulla pagina dell'audio no, li'
     * SU e GIU' muovono la frequenza di campionamento.
     *
     * Spegnendolo si torna alla posa canonica del passo - `anim_bump` a zero e
     * `set_step_pose` - cosi' le sonde dei corpi ridiventano confrontabili
     * senza cambiare stanza. */
    if (diag_page != 2 && (PadEdge & PAD_UP)) {
        anim_clock = !anim_clock;
        if (!anim_clock) {
            anim_bump = 0;
            set_step_pose(bg_step);
            Body_PinArm();      /* la posa canonica appena rimessa */
            redraw_scene();
            bd_mask = body_probe(bg_step, bd_got);
            Dbg_Clear();
            if (diag_on)
                draw_rows();
        }
    }

    /* IL QUADRO DI GIOCO, quando il tempo gira.
     *
     * L'avanzamento sta PRIMA del disegno perche' e' li' che sta nel gioco
     * (OBJECT.C: la gestione degli attori precede AffScene). Le sonde NON si
     * rifanno: la maschera resta quella dell'ultima posa confrontabile invece
     * di diventare rossa a ogni quadro - la stessa scelta gia' fatta per i
     * fotogrammi mossi a mano con SINISTRA e DESTRA. */
    if (anim_clock) {
        Body_AnimTick();
        /* IL RETTANGOLO al posto della stanza, e nessuna cancellazione qui:
         * `Body_DrawAll` arma in testa e cancella alla sua prima scrittura sul
         * framebuffer, cioe' dopo aver calcolato tutto. `Bg_Redraw` in questo
         * punto faceva 756 blit per muovere un braccio, e cancellare qui
         * lasciava lo schermo senza attore per tutto il calcolo. */
        Body_DrawAll();
        Dbg_Clear();
        if (diag_on)
            draw_rows();
    }

    /* GIU': LA SONDA DEL RIPRISTINO, e vale piu' di quel che sembra.
     *
     * Disegna gli attori e poi ripristina i loro rettangoli SENZA ridisegnarli:
     * quel che resta a schermo deve essere il fondo intatto, cioe' le quattro
     * misure del fondo (riquadro, non trasparenti, le due Fletcher) devono
     * tornare IDENTICHE a quelle che bg_probe ha confrontato dopo Bg_Build. E'
     * una divergenza di livello 1 verificata al bit, con l'oracolo che c'e'
     * gia'.
     *
     * E si guarda anche a occhio: gli attori spariscono del tutto. Un pezzo di
     * braccio rimasto sullo schermo E' la scia che il ripristino non copre -
     * il difetto si vede prima ancora di leggere le righe. */
    if (diag_page != 2 && (PadEdge & PAD_DOWN)) {
        /* Il fondo INTERO per primo, e non e' pigrizia: la diagnostica scrive i
         * suoi glifi dentro il framebuffer, quindi senza cancellarli le somme
         * misurerebbero il fondo PIU' sedici righe esadecimali e cadrebbero per
         * un motivo che non c'entra. Cosi' invece si parte dallo stato di
         * riferimento, esattamente quello su cui bg_probe ha gia' detto ok. */
        Bg_Redraw();            /* fondo intero e pulito              */
        Body_DrawAll();         /* si sporca, la lista si riempie     */
        Bg_BoxReset();          /* quel che ha appena disegnato       */
        Bg_ClsPending();        /*   ...si ripulisce: torni com'era   */
        Bg_Measure();
        bg_mask = bg_probe(bg_step, bg_got);
        Dbg_Clear();
        if (diag_on)
            draw_rows();
        return;
    }

    if (!diag_on)
        return;

    marker_slot++;
    if (marker_slot >= MARKER_SLOTS)
        marker_slot = 0;

    Dbg_Fill(MARKER_X0 + prev * MARKER_STEP, MARKER_Y,
             MARKER_W, MARKER_H, COL_BG);
    Dbg_Fill(MARKER_X0 + marker_slot * MARKER_STEP, MARKER_Y,
             MARKER_W, MARKER_H, COL_MARKER);

    /* Le righe vive appartengono alla PAGINA 0: sulla pagina 1 quelle stesse
     * righe sono sonde degli attori, e riscriverle per quadro le cancellerebbe
     * mostrando un tempo al posto di un corpo. */
    if (diag_page)
        return;

    /* Dbg_Value ridisegna solo se il valore e' cambiato, quindi le righe ferme
     * non costano niente: per quadro si muovono solo la 10, la 11 e - quando si
     * tocca il controller - le tre dell'input. */
    Dbg_Value(9,  ((ULONG)JAG_HAL_TICK_HZ << 16) | (UWORD)NbFramePerSecond);
    Dbg_Value(10, JAG_HAL_VBL_TICK);
    Dbg_Value(11, TimerRef);
    Dbg_Value(12, ((ULONG)PadSeen << 16) | PadState);
    Dbg_Value(13, ((ULONG)MyJoySeen << 24) | ((ULONG)(UBYTE)MyJoy << 16)
                  | ((ULONG)MyFireSeen << 8) | (UBYTE)MyFire);
    /* Tastierino: accumulato in alto, istantaneo in basso. MyKey non e' qui
     * perche' e' uno scancodice e non una maschera, quindi accumularlo non
     * vorrebbe dire niente; ma e' una funzione pura di questi bit calcolata
     * nella stessa chiamata, percio' vedere il bit 1 acceso qui vuol dire che
     * MyKey ha prodotto SC_1. */
    Dbg_Value(14, ((ULONG)PadKeysSeen << 16) | PadKeys);
}
