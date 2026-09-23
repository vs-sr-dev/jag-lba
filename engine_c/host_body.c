/* host_body.c - fa girare body_draw.c su un PC, con i dati veri.
 *
 * PERCHE' ESISTE
 * --------------
 * Perche' un difetto della pipeline dei corpi trovato qui costa un secondo, e
 * trovato su BigPEmu costa una build della cartuccia piu' un giro d'emulatore.
 * E soprattutto perche' separa DUE DOMANDE che altrimenti arriverebbero
 * insieme: "la pipeline calcola i numeri giusti?" e "il Jaguar la esegue come
 * previsto?". Se le sonde tornano qui e non la', il difetto non e' nella
 * geometria: e' nella mappa, nell'allineamento, nell'ordine dei byte o nella
 * cartuccia.
 *
 * NON E' UNA COPIA DEL MOTORE
 * ---------------------------
 * `#include "body_draw.c"` - il file compilato e' quello che va sul Jaguar.
 * L'unica differenza sono le due basi di memoria, che body_draw.c lascia
 * sostituire apposta. Se fosse una riscrittura non proverebbe niente: si
 * verificherebbe il gemello invece dell'originale.
 *
 * Il dump lo produce tools/body_host_dump.py e contiene solo quel che
 * Body_DrawAll legge dall'esterno, payload dei corpi compresi, byte per byte
 * come stanno nella cartuccia.
 *
 * USO
 *     python tools/body_host_dump.py
 *     cc -O2 -o host_body host_body.c && ./host_body
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* Il framebuffer e l'area di lavoro diventano due array normali. Sono le due
 * sole cose che body_draw.c tocca fuori da se' stesso. */
static unsigned char host_fb[640 * 592];
static unsigned char host_work[0x3000];
/* Il flusso dei lavori (S34), con la STESSA taglia della regione JOB_STREAM.
 * Non e' un dettaglio: se qui fosse piu' grande, lo svuotamento a pezzi non
 * verrebbe mai esercitato sul banco e si scoprirebbe sul Jaguar. La taglia si
 * PRENDE dalla mappa invece di riscriverla: in S37 la regione e' raddoppiata e
 * un numero copiato a mano avrebbe fatto misurare al banco un flusso che sul
 * Jaguar non esiste. */
#include "mem_map.h"
static int host_jobs[JAG_JOB_STREAM_SIZE / 4];

#define BODY_FB_BASE    ((unsigned long)(unsigned char *)host_fb)
/* Anche gli sprite scrivono nel fondo, e la base va sostituita QUI come le
 * altre: senza, sprite_draw.c scriverebbe all'indirizzo assoluto del Jaguar e
 * il banco morirebbe di segmentation fault invece di dare una sonda. */
#define SPRITE_FB_BASE  BODY_FB_BASE
#define BODY_WORK_BASE  ((unsigned long)(unsigned char *)host_work)
#define JOB_STREAM_BASE ((unsigned long)(int *)host_jobs)
#define JOB_STREAM_SIZE (sizeof host_jobs)

/* Qui la GPU TOM non c'e'. Il riempimento resta quello in C, che non e' un
 * ripiego: e' il MODELLO che src/gpu_body.s trascrive, e farlo girare qui e'
 * quel che permette di dire "l'aritmetica e' giusta, e' il Jaguar che non la
 * esegue come previsto" invece di avere le due domande insieme. */
#define BODY_FILL_ON_GPU 0
/* E per la stessa ragione lo stadio dell'intensita' (S40): qui gira il MODELLO
 * che src/gpu_shade.s trascrive. Le sonde SHADE1/SHADE2 che il banco confronta
 * con l'oracolo sono le stesse che il Jaguar mostrera' a schermo, quindi
 * verificano l'aritmetica PRIMA che esista una riga di assembly - ed e' quel
 * che rende attribuibile il difetto quando cadranno la' e non qui. */
#define BODY_SHADE_ON_GPU 0
/* E il front-end (S45): qui girano vert_stage e cull_stage, i modelli che
 * src/gpu_front.s trascrive, sul descrittore di posa vero. */
#define BODY_FRONT_ON_GPU 0
/* L'EMISSIONE (S46) invece resta nella forma NUOVA: qui gira il MODELLO del
 * walker (list_run_c + gather_poly), che e' quel che la fase E di gpu_body.s
 * trascrive. E' sovrascrivibile apposta: `gcc -DBODY_EMIT_ON_GPU=0` compila
 * il percorso S34-S45 sulla stessa sorgente, e il confronto fra le due
 * uscite E' la verifica d'identita' del cambio di forma. */
#ifndef BODY_EMIT_ON_GPU
#define BODY_EMIT_ON_GPU 1
#endif

/* L'ATTORE-SPRITE. S47 lo aveva archiviato come "roba da Object Processor",
 * cioe' hardware che l'x86 non ha, e per questo non aveva mai provato il banco.
 * Era un errore di classificazione, ed e' costato una sessione: la DECISIONE
 * che accende lo sprite non tocca l'OP: e' aritmetica su un riquadro, e il
 * riquadro qui esce da vert_stage esattamente come sul Jaguar esce dalla GPU.
 * Quel che l'x86 non puo' fare e' COMPORRE l'immagine - ma per sapere perche'
 * spr_on resta zero non serve comporre niente.
 *
 * Le tre sostituzioni che servono: il buffer dello sprite (un array), i campi
 * HAL (un array, sotto) e i pixel, che qui atterrano in host_spr e nessuno
 * guarda - perche' il banco verifica la decisione, non la composizione. */
#define BODY_ACTOR_SPRITE 1
static unsigned char host_spr[JAG_ACTOR_SPRITES_SIZE];
#define ACTOR_SPRITES_BASE ((unsigned long)(unsigned char *)host_spr)

/* I campi che il C scrive per il gestore di interrupt. Sul Jaguar stanno a
 * indirizzo fisso dentro HAL_VARS; qui la regione diventa un array e i nomi
 * della mappa ci puntano dentro da soli, perche' sono definiti IN TERMINI di
 * JAG_HAL_VARS e l'espansione avviene al punto d'uso. Una riga, e le quattro
 * scritture dell'attore-sprite smettono di essere un segmentation fault. */
static unsigned char host_hal[JAG_HAL_VARS_SIZE];
#undef JAG_HAL_VARS
#define JAG_HAL_VARS ((unsigned long)(unsigned char *)host_hal)

/* E LA LARGHEZZA DELLA PAROLA, che qui non e' pignoleria. La mappa dichiara i
 * campi `unsigned long`, che sul 68000 sono 32 bit e su x86-64 sono SESSANTA-
 * QUATTRO: una scrittura a HAL_VARS+$44 ne copre otto, cioe' si porta via
 * anche il campo a +$48. Le quattro scritture dell'attore-sprite stanno in
 * sedici byte consecutivi, quindi si demolirebbero a vicenda e il banco
 * mostrerebbe phrase che sul Jaguar non esisteranno mai.
 *
 * Si sostituisce la LARGHEZZA, non la logica - come host_front.c sostituisce
 * la LETTURA big endian e non il formato. E' la stessa trappola di
 * [[feedback_host_harness_before_hw]], su un'altra superficie. */
#undef JAG_HAL_OP_SPR_PH0LO
#undef JAG_HAL_OP_SPR_PH1LO
#undef JAG_HAL_SPR_DATA_A
#undef JAG_HAL_SPR_DATA_B
#define HAL_L32(off) (*(volatile unsigned int *)(host_hal + (off)))
#define JAG_HAL_OP_SPR_PH0LO HAL_L32(0x44)
#define JAG_HAL_OP_SPR_PH1LO HAL_L32(0x48)
#define JAG_HAL_SPR_DATA_A   HAL_L32(0x4C)
#define JAG_HAL_SPR_DATA_B   HAL_L32(0x50)

/* La base virtuale che Bg_DrawOverBrickSpr ha ricevuto nell'ultima chiamata:
 * il banco non ha tessere da disegnare, ma il NUMERO lo puo' verificare. */
static unsigned int host_spr_base;

/* Il descrittore di posa: sul Jaguar vive dentro GPU_VARS, qui e' un array. */
static int host_pose_desc[33 * 4];
#define POSE_DESC_BASE  ((unsigned long)(int *)host_pose_desc)

#include "hal.h"

/* Lo stato che il motore vero riempie, qui riempito dal dump. */
T_OBJET  ListObjet[MAX_OBJETS];
LONG     NbObjets;
UBYTE   *PtrBody[MAX_BODYS];
LONG     NbBodys;
T_SCENE  Scene;
T_BGSTAT BgStat;
static WORD hqr_slot[MAX_BODYS];

WORD HqrBodySlotOf(WORD b)
{
    if (b < 0 || b >= NbBodys)
        return -1;
    return hqr_slot[b];
}

void TheEnd(WORD code, const char *msg)
{
    fprintf(stderr, "TheEnd %04X: %s\n", (unsigned)code, msg);
    exit(2);
}

/* M6: `Body_AnimTick` la chiama nel ramo ANIM_THEN, cioe' quando
 * un'animazione FINISCE e ne comincia una prenotata. La vera SearchAnim sta in
 * scene_load.c e legge la fiche FILE3D dell'attore, che nel dump non c'e': il
 * dump porta le voci di ANIM.HQR risolte, non le fiche.
 *
 * Qui quindi FERMA IL BANCO invece di tornare un valore. Non e' pigrizia: se il
 * banco arrivasse davvero in questo ramo, un -1 di comodo lo farebbe proseguire
 * su un'animazione sbagliata e le sonde cadrebbero altrove, lontano dalla
 * causa. Il banco percorre solo animazioni ANIM_REPEAT (`--anim` compreso),
 * quindi questa riga non deve mai eseguire; il giorno che esegue, e' il dump
 * che va esteso alle fiche. */
WORD SearchAnim(UBYTE numanim, WORD numobj)
{
    fprintf(stderr, "SearchAnim(%u, %d): il banco non ha le fiche\n",
            (unsigned)numanim, (int)numobj);
    exit(2);
    return -1;
}

/* L'arena per-cubo, quel tanto che basta a `norm_native` (S39). Sul Jaguar la
 * fornisce hal_mem.c, che qui non si compila. Non c'e' una free: il banco fa tre
 * passi e poi esce, e un allocatore vero sarebbe codice non esercitato - la
 * stessa ragione per cui hal_mem.c non ne ha uno. */
void *LoadMallocPhrase(ULONG size)
{
    /* `malloc` torna gia' allineato ben oltre gli 8 byte che servono, quindi qui
     * non c'e' nessuna aritmetica sul puntatore da sbagliare - che e' tutto il
     * punto di tenerla fuori da body_draw.c. */
    void *p = malloc((size_t)size);

    if (!p)
        TheEnd(TE_OUT_OF_MEM, "LoadMallocPhrase del banco");
    return p;
}

/* Gli SPRITE (M3e). Il banco li esercita davvero: le tre sonde SPR non
 * dipendono dal fondo, quindi qui si confrontano come le altre. Le risorse
 * arrivano dal dump invece che dalla cartuccia, e HQR_Get qui sotto e' la
 * sola differenza fra questo file e il Jaguar. */
static const unsigned char *host_zv;
static const unsigned char *host_bank[256];

/* M4: le animazioni. Il dump ne porta le voci che i passi usano davvero,
 * indicizzate per SLOT di ANIM.HQR - lo stesso numero che sul Jaguar finisce in
 * `ptrobj->Anim` e che HQR_Get risolve in cartuccia. */
#define HOST_ANIM_SLOTS 1024
static const unsigned char *host_anim[HOST_ANIM_SLOTS];

/* TimerRef: sul Jaguar lo muove Hal_UpdateTimerRef dagli interrupt video, qui
 * sta fermo. E' quel che rende il banco deterministico SENZA falsificare
 * l'orologio: e' `AnimMemoTicks` a essere messo indietro del passo voluto, e
 * `TimerRef - AnimMemoTicks` da' esattamente quel passo. */
volatile ULONG TimerRef = 0x10000000UL;
volatile WORD  NbFramePerSecond = 60;

const void *HQR_Get(hqr_id_t id, int index)
{
    if (id == HQR_RESS)
        return host_zv;
    if (id == HQR_SPRITES && index >= 0 && index < 256)
        return host_bank[index];
    if (id == HQR_ANIMS && index >= 0 && index < HOST_ANIM_SLOTS)
        return host_anim[index];
    return 0;
}

/* M3d: LE TESSERE DAVANTI ALL'ATTORE NON SI RIDISEGNANO QUI, E VA DETTO.
 *
 * Non e' una semplificazione di comodo: e' un limite del banco. Ridisegnarle
 * vorrebbe dire portarsi dietro la lista delle celle E i pixel decodificati di
 * tutti i brick della stanza - la regione BRICK_WORK e' 576 KB - per verificare
 * un effetto che si misura sul COMPOSTO, e il composto qui non c'e' comunque
 * (manca il fondo, e le sonde BBOX/NONZERO/SUM non si confrontano).
 *
 * Quel che il banco continua a provare e' tutto il resto: la pipeline dei corpi
 * fino ai pixel dell'attore, che e' esattamente cio' che M3d NON cambia. La
 * prova di M3d e' l'oracolo Python piu' BigPEmu.
 *
 * CONSEGUENZA DA RICORDARE: tools/body_black_check.py confronta i pixel dei
 * corpi soli con quelli del banco, quindi anche LUI deve saltare il ridisegno.
 * Lo fa, e lo dice. */
void Bg_DrawOverBrick(WORD xm, WORD ym, WORD zm,
                      WORD cx0, WORD cy0, WORD cx1, WORD cy1)
{
    (void)xm; (void)ym; (void)zm;
    (void)cx0; (void)cy0; (void)cx1; (void)cy1;
}

void Bg_DrawOverBrick3(WORD xm, WORD ym, WORD zm,
                       WORD cx0, WORD cy0, WORD cx1, WORD cy1)
{
    (void)xm; (void)ym; (void)zm;
    (void)cx0; (void)cy0; (void)cx1; (void)cy1;
}

/* S49: la variante che cuoce le tessere davanti DENTRO il buffer dello sprite.
 * Vuota per la stessa ragione delle altre due - qui il fondo non c'e', quindi
 * non ci sono tessere - ma la `base` si controlla: e' l'unico numero che questa
 * chiamata calcola invece di ricevere, ed e' quello che sbagliato scriverebbe
 * fuori dal buffer. Atteso: ACTOR_SPRITES_BASE - spr_y0 * 640. */
void Bg_DrawOverBrickSpr(WORD xm, WORD ym, WORD zm,
                         WORD cx0, WORD cy0, WORD cx1, WORD cy1,
                         ULONG base)
{
    (void)xm; (void)ym; (void)zm;
    (void)cx0; (void)cy0; (void)cx1; (void)cy1;
    host_spr_base = base;
}

/* S41: i rettangoli sporchi. Qui il fondo non c'e' - il banco disegna i corpi
 * su nero - quindi non c'e' niente da ripristinare e la lista non serve. Resta
 * la firma, perche' body_draw.c e sprite_draw.c la chiamano davvero: se un
 * giorno il banco si prendesse anche il fondo, il ripristino andrebbe qui e non
 * in un gemello. */
void Bg_AddBox(WORD x0, WORD y0, WORD x1, WORD y1)
{
    (void)x0; (void)y0; (void)x1; (void)y1;
}

void Bg_BoxReset(void)
{
}

void Bg_ClsPending(void)
{
}

/* M6: LA GRIGLIA DEL CUBO NON C'E' NEL DUMP, e la collisione si compila lo
 * stesso.
 *
 * `Grid_ColBrick` vive in scene_bg.c, che qui non si compila: vorrebbe il
 * Blitter, i registri video e la decodifica dei brick. Il dump porta i CORPI,
 * non la stanza.
 *
 * La scelta e' fra due cose diverse: sostituire `Move_Collide` con un finto, o
 * compilare quella VERA su una griglia che risponde "aria" dappertutto. La
 * seconda e' meglio e non di poco: la funzione vera esercita l'aritmetica dei
 * quattro spigoli, l'uso della ZV e l'accumulatore Save*, quindi un errore di
 * segno o un attore spostato da una collisione che non c'e' cadono QUI. Un
 * finto proverebbe soltanto se stesso.
 *
 * Quel che resta scoperto e' esattamente una cosa: che il muro fermi. Per
 * averla anche qui il dump dovrebbe portarsi dietro il cubo e la libreria dei
 * blocchi - voce aperta, non un dettaglio: e' la differenza fra verificare i
 * muri da soli e chiederlo all'utente. */
UBYTE Grid_ColBrick(LONG xw, LONG yw, LONG zw)
{
    (void)xw; (void)yw; (void)zw;
    return 0;
}

UBYTE Grid_ColBrickFull(LONG xw, LONG yw, LONG zw, LONG ymax)
{
    (void)xw; (void)yw; (void)zw; (void)ymax;
    return 0;
}

LONG XMap, YMap, ZMap;

/* "Il fondo e' sporco qui?" - sul banco la risposta e' SEMPRE SI', e non e' un
 * ripiego: il banco disegna una passata sola su un fondo che non esiste, quindi
 * la domanda "qualcuno mi e' passato sopra dall'ultima volta" non ha un
 * significato. Rispondendo si', ogni sprite viene disegnato come prima di S42 e
 * le sonde restano confrontabili con le attese gia' calcolate - che e' il
 * punto: il salto di chi sta fermo e' una scelta di QUANDO ridisegnare, non di
 * COSA disegnare, e non deve poter cambiare un pixel atteso. */
int Bg_BoxDamaged(WORD x0, WORD y0, WORD x1, WORD y1)
{
    (void)x0; (void)y0; (void)x1; (void)y1;
    return 1;
}

/* Il pad: sul Jaguar lo riempie hal_input.c, qui resta a zero. `Game_Frame` si
 * compila comunque - e con tutti i tasti liberi non fa niente, che e' l'unica
 * risposta sensata su un banco senza giocatore. */
WORD MyJoy, MyFire, MyKey, LastFire;

#include "body_draw.c"
#include "sprite_draw.c"
#include "object_move.c"
#include "body_expect.h"

static const unsigned char *dp;

static unsigned long rd32(void)
{
    unsigned long v = ((unsigned long)dp[0] << 24) | ((unsigned long)dp[1] << 16)
                      | ((unsigned long)dp[2] << 8) | dp[3];
    dp += 4;
    return v;
}

static short rd16(void)
{
    short v = (short)(((unsigned short)dp[0] << 8) | dp[1]);
    dp += 2;
    return v;
}

/* Le stesse cinque misure di Bg_Measure, che qui non si puo' linkare: sta in
 * scene_bg.c insieme al Blitter. Il calcolo e' identico riga per riga, e se
 * divergesse lo direbbero le sonde del COMPOSTO cadendo tutte insieme. */
static void host_measure(void)
{
    unsigned long s1 = 0, s2 = 0, nz = 0;
    int minx = 640, miny = 592, maxx = -1, maxy = -1;
    int y, i, k;
    const unsigned char *p = host_fb;

    for (y = 0; y < 592; y++) {
        for (i = 0; i < 640 / 4; i++) {
            unsigned long w = ((unsigned long)p[0] << 24)
                              | ((unsigned long)p[1] << 16)
                              | ((unsigned long)p[2] << 8) | p[3];
            p += 4;
            s1 += w;
            s2 += s1;
            if (w) {
                for (k = 0; k < 4; k++)
                    if ((w >> ((3 - k) * 8)) & 0xFF) {
                        int x = i * 4 + k;
                        nz++;
                        if (x < minx) minx = x;
                        if (x > maxx) maxx = x;
                        if (y < miny) miny = y;
                        if (y > maxy) maxy = y;
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

static const char *const NAMES[] = {
    "LIGHT", "AVXY", "GEOM1", "GEOM2", "SHADE1", "SHADE2", "CULL",
    "ORDER1", "ORDER2", "DRAWN", "PIXELS", "FIRST", "FIRST_XY",
    "SPR", "SPR_XY", "SPR_PIX",
    "ENT", "ENT_XY", "ENT_PIX",
    "BBOX_MIN", "BBOX_MAX", "NONZERO", "SUM1", "SUM2"
};

int main(int argc, char **argv)
{
    const char *path = (argc > 1) ? argv[1] : "../build/body_host.bin";
    unsigned char *blob;
    long size;
    unsigned long nsteps, step;
    int bad = 0;
    /* `--anim`: percorre TUTTA l'animazione invece della sola posa del passo.
     * Fuori dalla build normale, che deve restare un si'/no leggibile. */
    int sweep = (argc > 2 && strcmp(argv[2], "--anim") == 0);
    /* `--clock N`: fa scorrere il tempo per N tick chiamando `Body_AnimTick`,
     * cioe' la funzione che sul Jaguar muove tutto (S42). Vedi run_clock. */
    int clock_ticks = (argc > 3 && strcmp(argv[2], "--clock") == 0)
                      ? atoi(argv[3]) : 0;
    FILE *f = fopen(path, "rb");

    if (!f) {
        fprintf(stderr, "manca %s: lanciare prima "
                        "python tools/body_host_dump.py\n", path);
        return 1;
    }
    fseek(f, 0, SEEK_END);
    size = ftell(f);
    fseek(f, 0, SEEK_SET);
    blob = malloc(size);
    if (fread(blob, 1, size, f) != (size_t)size)
        return 1;
    fclose(f);

    dp = blob;
    if (rd32() != 0x424F4435UL) {   /* 'BOD5', vedi tools/body_host_dump.py */
        fprintf(stderr, "non e' un dump dei corpi (o e' del formato vecchio,"
                        " senza le animazioni: rifallo con body_host_dump.py)\n");
        return 1;
    }
    nsteps = rd32();

    {   /* La tabella ZV una volta sola, poi i banchi degli sprite usati. Non
         * e' un dettaglio del banco: e' la stessa PtrZvExtra che sul Jaguar
         * arriva da RESS.HQR voce 3, letta dagli stessi byte. */
        unsigned long zvlen = rd32();
        unsigned int nb, b;
        host_zv = dp;
        dp += zvlen;
        nb = (unsigned short)rd16();
        for (b = 0; b < nb; b++) {
            unsigned int idx = (unsigned short)rd16();
            unsigned long len = rd32();
            if (idx < 256)
                host_bank[idx] = dp;
            dp += len;
        }
        nb = (unsigned short)rd16();          /* M4: le animazioni */
        for (b = 0; b < nb; b++) {
            unsigned int idx = (unsigned short)rd16();
            unsigned long len = rd32();
            if (idx < HOST_ANIM_SLOTS)
                host_anim[idx] = dp;
            dp += len;
        }
    }

    for (step = 0; step < nsteps; step++) {
        ULONG got[BD_EXPECT_COUNT];
        unsigned int n, na;
        int i;

        Scene.AlphaLight = rd16();
        Scene.BetaLight  = rd16();
        BgStat.vx = rd16();
        BgStat.vy = rd16();
        na = (unsigned short)rd16();

        NbObjets = (WORD)na;
        NbBodys = 0;
        Body_NormCacheReset();
        for (n = 0; n < na; n++) {
            unsigned long len;
            int kind;
            memset(&ListObjet[n], 0, sizeof(T_OBJET));
            ListObjet[n].Body = -1;
            ListObjet[n].PosObjX = rd16();
            ListObjet[n].PosObjY = rd16();
            ListObjet[n].PosObjZ = rd16();
            ListObjet[n].Beta    = rd16();
            /* Le bandiere dell'attore (S35): senza di queste INVISIBLE non
             * scatterebbe mai qui e il banco approverebbe una pipeline che sul
             * Jaguar disegna un attore in piu'. */
            ListObjet[n].Flags   = rd16();
            kind = (unsigned short)rd16();
            if (kind) {                       /* SPRITE_3D */
                ListObjet[n].Sprite = rd16();
                ListObjet[n].Info   = rd16();
                ListObjet[n].Info1  = rd16();
                ListObjet[n].Info2  = rd16();
                ListObjet[n].Info3  = rd16();
                continue;
            }
            hqr_slot[NbBodys]    = (WORD)(unsigned short)rd16();
            /* M4: l'animazione e la posa che il passo assegna a questo attore.
             * `cur` non si scrive da nessuna parte: si sottrae da TimerRef,
             * cosi' `TimerRef - AnimMemoTicks` vale esattamente il passo
             * voluto e pose_load non ha bisogno di sapere che e' un banco. */
            ListObjet[n].Anim         = rd16();
            ListObjet[n].Frame        = rd16();
            ListObjet[n].AnimSrcFrame = rd16();
            ListObjet[n].AnimMemoTicks = TimerRef - (unsigned short)rd16();
            len = rd32();
            PtrBody[NbBodys] = (UBYTE *)dp;
            dp += len;
            ListObjet[n].Body = NbBodys++;
        }

        /* Il fondo non c'e': l'host prova i CORPI. Le sonde del composto
         * misurano quindi i soli corpi su nero, ed e' per questo che
         * BBOX/NONZERO/SUM non si confrontano qui - hanno un valore diverso e
         * legittimo. Le prime tredici invece sono le stesse. */
        memset(host_fb, 0, sizeof(host_fb));
        Body_PinArm();          /* stessa regola del Jaguar: pin della sonda */
        Body_DrawAll();
        host_measure();

        got[BD_LIGHT]    = BodyStat.light;
        got[BD_AVXY]     = ((ULONG)(UWORD)BodyStat.avx << 16) | (UWORD)BodyStat.avy;
        got[BD_GEOM1]    = BodyStat.geom[0];
        got[BD_GEOM2]    = BodyStat.geom[1];
        got[BD_SHADE1]   = BodyStat.shade[0];
        got[BD_SHADE2]   = BodyStat.shade[1];
        got[BD_CULL]     = ((ULONG)BodyStat.tot << 16) | BodyStat.live;
        got[BD_ORDER1]   = BodyStat.order[0];
        got[BD_ORDER2]   = BodyStat.order[1];
        got[BD_DRAWN]    = ((ULONG)BodyStat.drawn << 16) | (BodyStat.polys & 0xFFFFUL);
        got[BD_PIXELS]   = BodyStat.pixels;
        got[BD_FIRST]    = ((ULONG)(BodyStat.first & 0xFF) << 24)
                           | ((ULONG)(BodyStat.firstslot & 0xFF) << 16)
                           | BodyStat.firstpolys;
        got[BD_FIRST_XY] = ((ULONG)(UWORD)BodyStat.firstx << 16) | (UWORD)BodyStat.firsty;
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

        printf("passo %lu\n", step);
        /* Fino a ENT_PIX incluso: le sonde degli sprite e quelle di linee e
         * sfere non dipendono dal fondo, quindi qui valgono quanto le altre. */
        for (i = 0; i < BD_ENT_PIX + 1; i++) {
            ULONG want = (ULONG)BodyExpect[step][i];
            int ok = (got[i] == want);
            if (!ok)
                bad++;
            printf("  %-9s %08lX  atteso %08lX  %s\n",
                   NAMES[i], (unsigned long)got[i], (unsigned long)want,
                   ok ? "ok" : "<<<< NO");
        }
        printf("  (le sonde del composto non si confrontano qui: manca il fondo)\n");
        printf("  corpi %08lX %08lX  pixel %lu\n",
               (unsigned long)got[BD_SUM1], (unsigned long)got[BD_SUM2],
               (unsigned long)got[BD_NONZERO]);
        /* S49: LA DECISIONE DELL'ATTORE-SPRITE, con i numeri che l'hanno
         * presa. `why` ha un bit per condizione, acceso se PASSA: F = sprite
         * acceso, e qualunque altra cifra dice subito quale ha detto no.
         * I valori sono quelli dell'ULTIMO attore passato da draw_actor, che
         * nella scena del riferimento e' l'eroe. */
        /* Lo STESSO long che il Jaguar mostra su pagina 1, colonna destra,
         * etichetta F. Stamparlo qui non e' un di piu': e' quel che rende
         * confrontabili le due letture senza ricomporre a mano i quattro byte,
         * e in questa caccia il banco dice 1F dove il ferro dice altro. */
        printf("  sprstat: %08lX\n", (unsigned long)BodyStat.sprstat);
        printf("  spr: why=%02X (h%d b%d r%d c%d ON%d)  bb=[%d..%d]x[%d..%d]"
               "  w=%d h=%d  x0=%d finestra=[%d..%d]\n",
               spr_why & 0xFF,
               (spr_why & SPR_WHY_HERO) != 0, (spr_why & SPR_WHY_BOX) != 0,
               (spr_why & SPR_WHY_ROWS) != 0, (spr_why & SPR_WHY_COLS) != 0,
               (spr_why & SPR_WHY_ON) != 0,
               (int)spr_bb[0], (int)spr_bb[1], (int)spr_bb[2], (int)spr_bb[3],
               (int)(spr_bb[1] - spr_bb[0] + 1), (int)(spr_bb[3] - spr_bb[2] + 1),
               (int)spr_want_x0, (int)spr_want_x0,
               (int)(spr_want_x0 + JAG_SPR_VIS_W - 1));
        /* LE QUATTRO PHRASE COME LE LEGGERA' IL GESTORE DI INTERRUPT. E' la
         * sonda che a S47 e' mancata: la' si guardavano i campi HAL sul Jaguar
         * SENZA sapere che valore avrebbero dovuto avere, e il valore di
         * "spento" coincide con quello che InitOpList lascia al boot. Qui i
         * campi partono da un array AZZERATO, quindi qualsiasi cosa diversa da
         * zero e' una scrittura che e' arrivata davvero: "il C non ha scritto"
         * e "il C ha scritto lo spegnimento" non danno piu' lo stesso campione.
         * HEIGHT (bit 14-23 di PH0LO) e' in righe PER CAMPO, cioe' meta'. */
        printf("  hal: ph0=%08lX ph1=%08lX dataA=%08lX dataB=%08lX"
               "  ypos=%lu height=%lu xpos=%lu\n",
               (unsigned long)JAG_HAL_OP_SPR_PH0LO,
               (unsigned long)JAG_HAL_OP_SPR_PH1LO,
               /* I due DATA si stampano come SCOSTAMENTI dalla base: qui la
                * base e' un array e il suo indirizzo non dice niente, mentre
                * lo scostamento e' lo stesso numero che il Jaguar avra'
                * (atteso: spr_x0, e spr_x0 + una riga per il campo dispari). */
               (unsigned long)(JAG_HAL_SPR_DATA_A
                               - (unsigned int)ACTOR_SPRITES_BASE),
               (unsigned long)(JAG_HAL_SPR_DATA_B
                               - (unsigned int)ACTOR_SPRITES_BASE),
               (unsigned long)((JAG_HAL_OP_SPR_PH0LO >> 3) & 0x7FFUL),
               (unsigned long)((JAG_HAL_OP_SPR_PH0LO >> 14) & 0x3FFUL),
               (unsigned long)(JAG_HAL_OP_SPR_PH1LO & 0xFFFUL));
        /* E la base virtuale delle tessere davanti (S49): il buffer e' alto
         * JAG_SPR_ROWS righe, quindi lo scostamento della PRIMA riga scritta
         * (cy0, che vale spr_y0 o una riga sotto) deve cadere fra 0 e quel
         * limite. Un segno sbagliato qui scriverebbe PRIMA del buffer, cioe'
         * dentro BODY_WORK, e si vedrebbe come un corpo rotto altrove. */
        {   /* Il confronto si fa a 32 bit su tutti e due i lati: `ULONG` e'
             * `unsigned int`, quindi la base attraversa la firma TRONCATA e un
             * puntatore host a 64 bit non tornerebbe mai. */
            /* Il banco in cui si e' DISEGNATO e' quello prima dello scambio:
             * `spr_bank` a questo punto indica gia' il prossimo. Sottrarlo e'
             * quel che fa tornare lo scostamento a -y0 righe come prima dei
             * due banchi - se non tornasse, l'aritmetica del banco sarebbe
             * sbagliata e le tessere finirebbero nell'immagine che l'OP sta
             * mostrando, cioe' proprio il difetto che i banchi tolgono. */
            int bank = spr_bank ^ 1;
            int off = (int)(host_spr_base - (unsigned int)ACTOR_SPRITES_BASE
                            - (unsigned int)bank * JAG_SPR_BANK_BYTES);
            int first = (int)spr_bb[2] < 0 ? 0 : (int)spr_bb[2];

            printf("  over: banco %d, base = spr %+d righe -> prime righe"
                   " scritte a %d..%d (buffer 0..%d)\n",
                   bank, off / JAG_SPR_PITCH, off / JAG_SPR_PITCH + first,
                   off / JAG_SPR_PITCH
                   + ((int)spr_bb[3] > JAG_BG_H - 1 ? JAG_BG_H - 1
                                                    : (int)spr_bb[3]),
                   JAG_SPR_ROWS - 1);
        }
        /* S47: il cursore anim dell'eroe come pose_load l'ha visto davvero.
         * La stessa riga la legge la sonda BigPEmu dalla coda di BodyStat:
         * il confronto e' fra due fotografie prese nello stesso punto. */
        printf("  pin: frm=%u src=%04X cur=%lu tot=%u arr=%u tref=%lu "
               "g2=%08lX %08lX\n",
               (unsigned)BodyStat.pinfrm, (unsigned)BodyStat.pinsrc,
               (unsigned long)BodyStat.pincur, (unsigned)BodyStat.pintot,
               (unsigned)BodyStat.pinarr, (unsigned long)BodyStat.pintref,
               (unsigned long)BodyStat.ping2a, (unsigned long)BodyStat.ping2b);
        /* La matrice del GRUPPO 2 dentro wmat (l'unico attore disegnato della
         * scena 0 e' l'eroe, quindi wmat a fine passata e' il suo). La caccia
         * S46 aveva confrontato solo il gruppo 0. */
        printf("  wmatg2: %08lX %08lX %08lX %08lX %08lX %08lX %08lX %08lX %08lX\n",
               (unsigned long)(ULONG)wmat[18], (unsigned long)(ULONG)wmat[19],
               (unsigned long)(ULONG)wmat[20], (unsigned long)(ULONG)wmat[21],
               (unsigned long)(ULONG)wmat[22], (unsigned long)(ULONG)wmat[23],
               (unsigned long)(ULONG)wmat[24], (unsigned long)(ULONG)wmat[25],
               (unsigned long)(ULONG)wmat[26]);
        /* Dimensionamento del lancio singolo: byte di flusso, non stimati. */
        printf("  flusso: attore piu' grosso %lu byte, stanza %lu byte, "
               "lanci %lu su %lu poligoni accodati\n",
               (unsigned long)BodyStat.batchmax,
               (unsigned long)BodyStat.batchtot,
               (unsigned long)BodyStat.launches,
               (unsigned long)BodyStat.jobs);

        /* L'OROLOGIO CHE SCORRE (S42, `--clock N`), e un invariante che il
         * banco puo' verificare da solo.
         *
         * `Body_AnimTick` non e' piu' solo l'avanzamento del fotogramma: da S42
         * applica anche la rotazione maestra e lo SPOSTAMENTO della radice,
         * cioe' scrive `Beta` e `PosObj*`.
         *
         * L'INVARIANTE NON E' "NESSUNO SI MUOVE", ed e' la prima cosa che
         * questo banco ha corretto: nella scena 0 un attore si sposta di 3480
         * unita' in Z in 600 tick, e ha ragione lui. La sua animazione (voce
         * 102 di ANIM.HQR, quella che la SUA fiche chiama "a riposo") porta
         * 58 unita' di Z su quattro fotogrammi da 10 tick, e 58 x 60 fa
         * esattamente 3480. Un'animazione "ferma" che avanza non e' un
         * controsenso: e' una creatura che striscia sul posto del suo giro.
         *
         * L'invariante vero si CALCOLA DAL DATO, ed e' a doppio senso:
         *
         *   un attore si muove SE E SOLO SE la sua animazione ha almeno un
         *   passo non nullo.
         *
         * Cosi' prende sia il segno sbagliato (si muove chi non deve) sia
         * l'accumulatore che non riparte (non si muove chi deve), e non
         * dipende da quale stanza si guarda. Un'attesa scritta a mano qui
         * direbbe solo che la scena 0 e' fatta com'e'.
         *
         * Il cammino vero NON e' raggiungibile qui (vuole la fiche, che il dump
         * non porta): questa prova copre la meccanica, non la scelta
         * dell'animazione. */
        if (clock_ticks > 0) {
            int n, wrong = 0;
            WORD *p0 = malloc(sizeof(WORD) * 4 * MAX_OBJETS);

            for (n = 0; n < NbObjets; n++) {
                p0[n * 4 + 0] = ListObjet[n].PosObjX;
                p0[n * 4 + 1] = ListObjet[n].PosObjY;
                p0[n * 4 + 2] = ListObjet[n].PosObjZ;
                p0[n * 4 + 3] = ListObjet[n].Beta;
            }

            printf("  --- orologio: %d tick\n", clock_ticks);
            for (i = 0; i < clock_ticks; i++) {
                TimerRef++;
                Body_AnimTick();
            }

            for (n = 0; n < NbObjets; n++) {
                T_OBJET *o = &ListObjet[n];
                const unsigned char *a;
                int moved, should = 0, rot = 0;
                unsigned nbf, nba, stride, f;

                moved = (o->PosObjX != p0[n * 4 + 0]
                         || o->PosObjY != p0[n * 4 + 1]
                         || o->PosObjZ != p0[n * 4 + 2]);

                /* Quel che l'animazione PRESCRIVE, letto dagli stessi byte che
                 * legge il motore. */
                if (o->Body != -1 && o->Anim >= 0 && !(o->Flags & SPRITE_3D)
                    && (a = HQR_Get(HQR_ANIMS, o->Anim)) != 0) {
                    nbf = a[0] | ((unsigned)a[1] << 8);
                    nba = a[2] | ((unsigned)a[3] << 8);
                    stride = nba * 8 + 8;
                    for (f = 0; f < nbf; f++) {
                        const unsigned char *fd = a + 8 + stride * f;
                        int k;
                        for (k = 2; k < 8; k += 2)
                            if ((short)(fd[k] | (fd[k + 1] << 8)))
                                should = 1;
                        if ((short)(fd[8] | (fd[9] << 8)))
                            rot = 1;
                    }
                }

                if (moved != should || (!rot && o->Beta != p0[n * 4 + 3])) {
                    printf("  attore %2d: mosso %d, atteso %d   "
                           "%d,%d,%d beta %d -> %d,%d,%d beta %d  "
                           "[body %d anim %d]\n",
                           n, moved, should,
                           p0[n * 4 + 0], p0[n * 4 + 1], p0[n * 4 + 2],
                           p0[n * 4 + 3],
                           o->PosObjX, o->PosObjY, o->PosObjZ, o->Beta,
                           o->Body, o->Anim);
                    wrong++;
                } else if (moved) {
                    printf("  attore %2d si muove, e il suo dato lo dice: "
                           "%d,%d,%d -> %d,%d,%d\n", n,
                           p0[n * 4 + 0], p0[n * 4 + 1], p0[n * 4 + 2],
                           o->PosObjX, o->PosObjY, o->PosObjZ);
                }
            }
            printf("  attori in disaccordo col proprio dato: %d (atteso 0)\n",
                   wrong);
            if (wrong)
                bad++;
            free(p0);
        }

        /* LA FOTOGRAFIA DI UNA FOTOGRAFIA (S42), e perche' e' una prova e non
         * una precauzione.
         *
         * Sul bersaglio il motore si fermava con TE_ANIM_BAD dopo pochi passi.
         * La sequenza che lo produce non e' rara: `Game_Frame` chiama InitAnim
         * DUE volte nello stesso quadro ogni volta che il pad cambia mentre una
         * direzione e' premuta (prima GEN_ANIM_RIEN, poi il cammino). La seconda
         * chiamata fotografa una posa che e' gia' un raccordo.
         *
         * Qui si riproduce esattamente quello stato, senza pad e senza fiche:
         * due fotografie di fila con `AnimSrcFrame` a -1 in mezzo. Se
         * `Body_SnapPose` azzera il puntatore prima di calcolare la posa,
         * `pose_load` chiama TheEnd e il banco muore con codice 2 - cioe' la
         * prova FALLISCE, invece di passare e lasciare il difetto al Jaguar. */
        {
            T_OBJET *h = &ListObjet[NUM_PERSO];

            if (h->Body >= 0 && h->Anim >= 0) {
                Body_SnapPose(NUM_PERSO);
                h->AnimSrcFrame = -1;
                Body_SnapPose(NUM_PERSO);      /* <- qui moriva */
                Body_DrawAll();
                printf("  raccordo su raccordo: regge\n");
                h->AnimSrcFrame = 0;
                h->PtrPoseSrc = 0;
            }
        }

        /* L'ANIMAZIONE INTERA, FOTOGRAMMA PER FOTOGRAMMA (S41, `--anim`).
         *
         * Nasce da un sintomo che sull'emulatore costava un giro di build e un
         * paio d'occhi: con l'orologio acceso "una delle pose e' vuota". Qui la
         * stessa domanda costa un secondo, perche' e' lo STESSO body_draw.c a
         * rispondere - se una posa non disegna, non disegna anche qui.
         *
         * Percorre l'animazione di OGNI attore fino alla sua lunghezza, un
         * fotogramma per passata, con il fotogramma di partenza e il passo che
         * usa il gioco: `src = dest-1` (o se stesso all'inizio) e tre passi
         * dell'interpolazione, cioe' i due rami di pose_load. */
        if (sweep) {
            ULONG f, nbf = 0;
            int n;

            /* La lunghezza da percorrere e' la piu' lunga della stanza: ogni
             * attore prende comunque il suo fotogramma modulo la sua. */
            for (n = 0; n < NbObjets; n++) {
                const unsigned char *a;
                ULONG len;
                if (ListObjet[n].Anim < 0)
                    continue;
                a = HQR_Get(HQR_ANIMS, ListObjet[n].Anim);
                if (!a)
                    continue;
                len = (ULONG)(a[0] | ((ULONG)a[1] << 8));
                if (len > nbf)
                    nbf = len;
            }
            printf("  --- animazione: %lu fotogrammi\n", (unsigned long)nbf);
            for (f = 0; f < nbf; f++) {
                static const unsigned short CURS[3] = { 0, 7, 0x7FFF };
                int k;

                for (k = 0; k < 3; k++) {
                    for (n = 0; n < NbObjets; n++) {
                        T_OBJET *o = &ListObjet[n];
                        const unsigned char *a;
                        UWORD len;

                        if (o->Anim < 0)
                            continue;
                        a = HQR_Get(HQR_ANIMS, o->Anim);
                        if (!a)
                            continue;
                        len = (UWORD)(a[0] | ((UWORD)a[1] << 8));
                        if (len == 0)
                            continue;
                        o->Frame = (WORD)(f % len);
                        o->AnimSrcFrame = o->Frame > 0 ? (WORD)(o->Frame - 1)
                                                       : o->Frame;
                        o->AnimMemoTicks = TimerRef - CURS[k];
                    }
                    memset(host_fb, 0, sizeof(host_fb));
                    Body_DrawAll();
                    printf("      fr %2lu  cur %5u -> attori %u  poligoni %lu  "
                           "pixel %lu  primo (%d,%d)%s\n",
                           (unsigned long)f, CURS[k],
                           (unsigned)BodyStat.drawn,
                           (unsigned long)BodyStat.polys,
                           (unsigned long)BodyStat.pixels,
                           BodyStat.firstx, BodyStat.firsty,
                           BodyStat.drawn ? "" : "   <<<< NESSUN ATTORE");
                }
            }
        }
    }

    /* I LIMITI A 16 BIT DEL KERNEL DELL'INTENSITA' (S40, src/gpu_shade.s).
     * `imultn`/`imacn` sulla GPU usano solo i sedici bit bassi con segno: qui
     * si misura, sui dati veri e su tutte le pose del banco, che i tre operandi
     * ci stiano davvero. Se un giorno non ci stessero, il Jaguar darebbe un
     * altro numero e nessuna sonda potrebbe dire perche'. */
#if !BODY_SHADE_ON_GPU
    {
        int over = (ShadeBound.mat > 32767) || (ShadeBound.rot > 32767)
                   || (ShadeBound.rng > 32767);
        printf("limiti a 16 bit del kernel intensita': "
               "|matrice| %ld  |ruotata| %ld  |rng| %ld  |dot| %ld  -> %s\n",
               (long)ShadeBound.mat, (long)ShadeBound.rot,
               (long)ShadeBound.rng, (long)ShadeBound.dot,
               over ? "SFORANO, il kernel GPU darebbe altri numeri" : "ok");
        if (over)
            bad++;
    }
#endif

    /* I LIMITI DEL KERNEL FRONT-END (S45, src/gpu_front.s). Tre operandi a 16
     * bit con segno (vertice+spostamento, posa+ancora, coordinate proiettate)
     * piu' la copertura del descrittore: se le voci non coprissero tutti i
     * vertici, la vecchia pipeline proiettava roba vecchia e la nuova no - e
     * nessuna sonda saprebbe dire perche'. */
#if !BODY_FRONT_ON_GPU
    {
        int over = (FrontBound.vin > 32767) || (FrontBound.pose > 32767)
                   || (FrontBound.scr > 32767) || FrontBound.cover_bad;
        printf("limiti a 16 bit del kernel front-end: "
               "|v+d| %ld  |posa| %ld  |schermo| %ld  copertura %s  -> %s\n",
               (long)FrontBound.vin, (long)FrontBound.pose,
               (long)FrontBound.scr,
               FrontBound.cover_bad ? "BUCATA" : "ok",
               over ? "SFORANO, il kernel GPU darebbe altri numeri" : "ok");
        if (over)
            bad++;
    }
#endif

    printf("%s\n", bad ? "SONDE CADUTE" : "tutte le sonde di pipeline tengono");
    return bad ? 1 : 0;
}
