/* hal.h - contratto HAL di JagLBA, lato Jaguar.
 *
 * E' il sottoinsieme di D:\Homebrew5\32XLBA\m10-engine\hal\hal.h che questa
 * milestone implementa davvero. La forma (nomi, tipi, semantica) e' quella,
 * deliberatamente: i file di engine_src del porting 32X sono gia' scritti e
 * DEBUGGATI contro quella superficie, e cambiare i nomi qui vorrebbe dire
 * riscoprire le mine di S17 una per una (docs/skeleton_design.md 3.7).
 *
 * I nomi restano quelli originali di LBA1 (CamelCase alla francese) perche' il
 * codice di gioco portato li chiama cosi' e non va toccato.
 *
 * Cresce una milestone alla volta. Quello che non c'e' ancora NON e' dichiarato:
 * un prototipo senza implementazione diventa un errore di link, che e' il
 * momento giusto per accorgersene.
 *
 * M1: tempo, input, HQR, arene, TheEnd.
 */
#ifndef JAG_HAL_H
#define JAG_HAL_H

#include "mem_map.h"

/* ===== Tipi di LBA1 (larghezze di DEFINES.H) =====
 * m68k-elf-gcc senza -mshort da' le stesse larghezze del Watcom DOS386 e
 * dell'sh-elf del porting 32X: int e long a 32 bit, short a 16. */
typedef signed char    BYTE;
typedef unsigned char  UBYTE;
typedef short          WORD;
typedef unsigned short UWORD;
/* LONG e' `int`, non `long`, ed e' una correzione di S32 con un motivo preciso.
 *
 * Su m68k-elf i due sono la stessa cosa (32 bit), quindi per il Jaguar non
 * cambia niente. Ma da S32 lo STESSO body_draw.c gira anche su un PC per
 * confrontarsi con le attese prima di costruire la cartuccia (host_body.c), e
 * li' `long` e' 64 bit: gli array che si dividono BODY_WORK a scostamenti fissi
 * si sovrapporrebbero, e il banco di prova misurerebbe la propria corruzione
 * invece della pipeline. E' successo al primo giro.
 *
 * `int` e' 32 bit su tutti e due, che e' anche la larghezza che LONG ha in
 * DEFINES.H di LBA1. */
typedef int            LONG;
typedef unsigned int   ULONG;

#ifndef TRUE
#define TRUE  1
#endif
#ifndef FALSE
#define FALSE 0
#endif

/* ===== TEMPO =====
 * TimerRef e' il contatore a 50 Hz su cui LBA1 misura tutto: durata delle
 * animazioni, attesa dei dialoghi, timeout degli script. Upstream veniva dal
 * timer del PC; qui lo deriva Hal_UpdateTimerRef dagli interrupt video.
 *
 * La conversione NON e' un rapporto fisso, perche' la cadenza degli interrupt
 * dipende da due cose indipendenti: NTSC contro PAL, e un interrupt per campo
 * (Jaguar vero) contro uno per quadro (BigPEmu). Sono quattro cadenze possibili
 * (60/50/30/25), e il lato RMAC le pubblica in JAG_HAL_TICK_HZ. Vedi hal_time.c.
 */
extern volatile ULONG TimerRef;
extern volatile WORD  NbFramePerSecond;

void Hal_UpdateTimerRef(void);   /* una volta per quadro, prima della logica */

/* ===== INPUT =====
 * Il motore legge queste tre variabili in cima al MainLoop. Le riempie
 * Hal_ReadPad dal controller Jaguar. Non sono volatile: le scrive e le legge
 * lo stesso filo di esecuzione, come in GLOBAL.C upstream.
 * Codici e bit sono verbatim da LIB386/LIB_SYS/LIB_SYS.H (DoDir li testa
 * direttamente). */
extern WORD MyJoy;
extern WORD MyFire;
extern WORD MyKey;
extern WORD LastFire;

#define J_UP    1
#define J_DOWN  2
#define J_LEFT  4
#define J_RIGHT 8

#define F_SPACE  1
#define F_RETURN 2
#define F_CTRL   4
#define F_ALT    8
#define F_SUPPR  16
#define F_SHIFT  32

/* Scancodi PC dei tasti che LBA1 legge da MyKey. Il tastierino numerico del
 * Jaguar li produce direttamente: e' l'uso per cui l'utente lo voleva
 * ([[reference_jaguar_joypad]]), e in LBA1 i tasti 1-4 sono i comportamenti
 * (normale / atletico / aggressivo / discreto). */
#define SC_1     0x02
#define SC_2     0x03
#define SC_3     0x04
#define SC_4     0x05
#define SC_ESC   0x01

void Hal_ReadPad(void);

/* Stato grezzo del controller, attivo ALTO (1 = premuto), per la diagnostica e
 * per i pezzi di HAL che vogliono i pulsanti e non la mappatura LBA. */
#define PAD_UP      0x0001
#define PAD_DOWN    0x0002
#define PAD_LEFT    0x0004
#define PAD_RIGHT   0x0008
#define PAD_A       0x0010
#define PAD_B       0x0020
#define PAD_C       0x0040
#define PAD_OPTION  0x0080
#define PAD_PAUSE   0x0100

extern UWORD PadState;      /* pulsanti premuti adesso     */
extern UWORD PadEdge;       /* premuti IN QUESTO quadro    */
extern UWORD PadKeys;       /* tastierino: bit 0..9 = '0'..'9', 10 = '*', 11 = '#' */
/* Il fronte del TASTIERINO, gemello di PadEdge. Serve da S42: il tasto che
 * entra e esce dalla diagnostica non puo' essere di livello, o il primo quadro
 * dopo la pressione rientrerebbe. */
extern UWORD PadKeysEdge;

#define PADKEY_STAR  (1u << 10)
#define PADKEY_HASH  (1u << 11)

/* Accumulatori: ogni bit mai visto premuto resta alto. Servono a verificare
 * l'input senza dover far coincidere la pressione con lo scatto di uno
 * screenshot - vedi hal_input.c. */
extern UWORD PadSeen;
extern UBYTE MyJoySeen;
extern UBYTE MyFireSeen;
extern UWORD PadKeysSeen;

/* ===== HQR =====
 * Le risorse stanno nella cartuccia, dentro il contenitore "JHQR" scritto da
 * tools/hqr_pack.py (il formato e' documentato in testa a quel file). Percio'
 * HQR_Get torna un puntatore DIRETTO alla ROM: nessuna copia in DRAM, nessuna
 * decompressione a runtime, nessuna pressione su una cache che non c'e'.
 *
 * L'ordine dell'enum e' quello del porting 32X per i primi dieci valori.
 * SPRITES e' in coda perche' li' non c'era e cambiare i valori esistenti
 * avrebbe invalidato i dati gia' estratti. */
typedef enum {
    HQR_SCENE = 0,
    HQR_BODY,
    HQR_ANIMS,
    HQR_RESS,
    HQR_FILE3D,
    HQR_TEXT,
    HQR_SAMPLES,
    HQR_MIDI_MI,
    HQR_MIDI_SB,
    HQR_INVOBJ,
    HQR_SPRITES,
    /* M2b: i tre archivi del fondo, con gli indici ORIGINALI di LBA1. In V4
     * avevano un formato proprio e una rimappatura dei brick; qui sono HQR come
     * gli altri, cosi' un numero letto sul Jaguar e' confrontabile con lo stesso
     * numero stampato dagli strumenti Python. */
    HQR_GRID,
    HQR_BLL,
    HQR_BRICK,
    /* S48: l'unico archivio che non viene da un file di LBA1. Contiene le
     * schermate del front-end preparate da tools/front_screen.py (formato
     * 'FSCR', documentato li'). Sta in coda perche' i valori esistenti non si
     * spostano mai: sono scritti dentro la cartuccia gia' impacchettata. */
    HQR_FRONT,
    HQR_LAST
} hqr_id_t;

const void *HQR_Get (hqr_id_t id, int index);
ULONG       HQR_Size(hqr_id_t id, int index);
int         HQR_Slots(hqr_id_t id);     /* voci dichiarate, 0 = archivio assente */

/* La voce espansa DOVE DICE IL CHIAMANTE, senza passare dalle arene ne' dalla
 * cache. Torna i byte scritti, 0 se la voce non c'e' o non ci sta in `cap`.
 *
 * Esiste per una risorsa sola ma decisiva: una schermata a pieno formato pesa
 * 307200 byte, cioe' piu' dell'arena per-cubo intera, e la sua destinazione
 * naturale e' il framebuffer - dove HQR_Get non puo' consegnarla perche'
 * decide lui dove mettere le cose. */
ULONG HQR_Expand(hqr_id_t id, int index, void *dst, ULONG cap);

/* Valida l'intestazione. Da chiamare una volta all'avvio: se torna 0 ogni
 * HQR_Get tornera' NULL, che il motore sa gestire, invece di un puntatore in
 * mezzo al nulla che non saprebbe gestire nessuno. */
int HQR_Init(void);
int HQR_Ready(void);

/* Le voci degli archivi COMPRESSI si decomprimono nell'arena per-cubo e si
 * tengono in cache finche' l'arena vive. La chiama HQM_Free_All. */
void HQR_CacheReset(void);

/* Un long dell'intestazione, indice in parole da 32 bit:
 *   0 = magic, 1 = versione|archivi, 2 = dimensione, 3 = somma di controllo.
 * Riletto dalla cartuccia a ogni chiamata: e' quello che lo rende una prova e
 * non una copia di se stesso. */
ULONG HQR_Header(int word_index);

/* ===== ARENE DI MEMORIA =====
 * Due arene sull'heap dichiarato dal linker script (__heap_start/__heap_end,
 * dentro C_ARENA):
 *   Malloc      = persistente, cresce dal basso. Vive per tutta la partita.
 *   LoadMalloc  = per-cubo, cresce dall'alto. HQM_Free_All la azzera a
 *                 ChangeCube, che e' esattamente la semantica upstream.
 * Le due si incontrano in mezzo e l'esaurimento e' un TheEnd, non un ritorno
 * NULL silenzioso: il codice portato non controlla i NULL di Malloc. */
void  Hal_MemInit(void);
void *Malloc(ULONG size);
void  Free(void *ptr);
void *LoadMalloc(ULONG size);
void  HQM_Free_All(void);
ULONG Size_HQM_Memory(void);
ULONG Hal_MemUsedPersistent(void);
ULONG Hal_MemUsedCube(void);

/* ===== ARRESTO =====
 * Upstream stampava un messaggio DOS e usciva. Qui non c'e' un DOS a cui
 * tornare: TheEnd disegna il codice a schermo e si ferma. Il marcatore di vita
 * si blocca insieme, quindi si vede che e' successo qualcosa anche senza
 * saper leggere il codice. Non torna mai. */
void TheEnd(WORD code, const char *msg);

/* Codici di TheEnd assegnati dal lato Jaguar. Upstream usava numeri
 * arbitrari nei call site; questi sono i nostri, tenuti alti per non
 * collidere. */
#define TE_HQR_MISSING   0x100      /* HQR_Get su un archivio che non c'e'   */
#define TE_OUT_OF_MEM    0x101      /* arene esaurite                        */
#define TE_ARENA_BAD     0x102      /* l'arena non rilegge quel che si scrive */
#define TE_BG_WORKFULL   0x103      /* BRICK_WORK piena: scena troppo ricca  */
#define TE_BG_TOO_MANY   0x104      /* piu' brick di quanti handle esistano  */
#define TE_BG_CELLFULL   0x105      /* CELL_LIST piena: griglia inattesa     */
#define TE_HQR_CACHE     0x106      /* troppe voci compresse vive insieme    */
#define TE_BODY_BIG      0x107      /* corpo oltre la capienza di BODY_WORK  */
#define TE_ANIM_BAD      0x108      /* gruppo o fotogramma che l'archivio non ha */

/* ===== SCENA (M2) =====
 * `T_ZONE` e' la struct di COMMON.H:240-255, verbatim: 12 WORD. La si decodifica
 * dai byte della cartuccia invece di puntarla, vedi le.h.
 * Type 0 = change cube, Type 1 = set pos camera (OBJECT.C:3436). */
typedef struct {
    WORD X0, Y0, Z0;
    WORD X1, Y1, Z1;
    WORD Type;
    WORD Num;
    WORD Info0, Info1, Info2, Info3;
} T_ZONE;

/* Quel che il cammino della struct SCENE ricava per il fondo e la camera.
 * Gli attori NON stanno qui: stanno in ListObjet[], vedi la sezione M3. */
typedef struct {
    WORD num;
    int  valid;                 /* 0 = il cammino non e' arrivato in fondo esatto */
    long tail;                  /* byte residui, per diagnosticare un valid = 0   */

    UBYTE Island, GameOverCube, CubeJingle;
    WORD  AlphaLight, BetaLight;
    WORD  CubeStartX, CubeStartY, CubeStartZ;
    WORD  NbObjets, NbZones, NbBrickTrack;

    const unsigned char *ZonesRaw;    /* dentro la cartuccia, non copiate */
    const unsigned char *TracksRaw;

    /* Derivati: sono i due valori che a M2 sostituiscono i placeholder. */
    WORD CamGX, CamGZ, CamGY;
    WORD NbChangeCube;
    WORD FirstCubeTarget;             /* -1 se la scena non ne ha */
    WORD FirstCubeEnterX, FirstCubeEnterY, FirstCubeEnterZ;
} T_SCENE;

extern T_SCENE Scene;

int  Scene_Zone(int index, T_ZONE *out);
void Scene_Derive(void);

/* ===== ATTORI (M3a) =====
 * Da M3 la struct SCENE non si percorre piu' saltando gli attori: si legge
 * `LoadScene` per intero e gli attori finiscono in ListObjet[], che e' il nome
 * e la forma di upstream (DISKFUNC.C:43-241, T_OBJET in DEFINES.H).
 *
 * `Scene_Walk` non esiste piu'. Era una duplicazione DICHIARATA del cammino,
 * con la condizione di rimozione scritta in testa al suo file: serviva perche'
 * M2 aveva bisogno delle ZONE, che stanno dopo gli attori, prima che gli
 * attori esistessero. La condizione e' soddisfatta adesso.
 */
#define MAX_OBJETS   100      /* COMMON.H:113 */
#define MAX_ZONES    255
#define MAX_TRACKS   255
#define MAX_BODYS    200
#define NUM_PERSO      0      /* COMMON.H:332: ListObjet[0] e' l'eroe */

/* Flags dell'attore, COMMON.H:160. Un attore SPRITE_3D non ha un corpo 3D: e'
 * una bitmap di SPRITES.HQR. Sono percorsi di disegno diversi, e M3 fa solo
 * quello dei corpi. */
#define SPRITE_3D   1024
/* COMMON.H:152, "(portes)". Sta qui e non piu' dentro un solo .c perche' da S37
 * lo leggono in tre: chi disegna (finestra di preclip larga), chi ridisegna le
 * tessere davanti (DrawOverBrick3 e AnimStep) e chi carica la scena. */
#define SPRITE_CLIP    8

/* Corpi GENERICI, DEFINES.H:131-133. Il gioco non nomina un indice di
 * BODY.HQR: nomina un generico, e la fiche FILE3D dell'attore lo risolve
 * (SearchBody). Per l'eroe la fiche 0 definisce SETTE generici, di cui solo
 * tre hanno un nome nel sorgente:
 *
 *     generico 0 NORMAL  -> BODY 0   tunica
 *     generico 1 TUNIQUE -> BODY 3   tunica (identica alla 0)
 *     generico 2 SABRE   -> BODY 4   tunica + spada
 *     generico 3         -> BODY 1   PRIGIONIERO (inizio partita)
 *     generico 4         -> BODY 2   travestimento da infermiera
 *     generico 5 / 6     -> BODY 5 / 6
 */
#define GEN_BODY_NORMAL   0
#define GEN_BODY_TUNIQUE  1
#define GEN_BODY_SABRE    2

/* Animazioni GENERICHE, DEFINES.H:135-141. Stessa indirezione dei corpi: la
 * fiche di ciascun personaggio dice quale voce di ANIM.HQR sia il "cammino"
 * PER LUI. */
#define NO_ANIM           255
#define GEN_ANIM_RIEN     0
#define GEN_ANIM_MARCHE   1
#define GEN_ANIM_RECULE   2
#define GEN_ANIM_GAUCHE   3
#define GEN_ANIM_DROITE   4
#define GEN_ANIM_TOMBE    7
#define GEN_ANIM_RECEPTION   8
#define GEN_ANIM_RECEPTION_2 9
#define GEN_ANIM_LANCE   15

/* Modi di concatenamento, COMMON.H:184-188. */
#define ANIM_REPEAT       0
#define ANIM_THEN         1
#define ANIM_ALL_THEN     2
#define ANIM_INSERT       3
#define ANIM_SET          4

/* WorkFlags, COMMON.H:134-145. */
#define OK_HIT            2
#define ANIM_END          4
#define NEW_FRAME         8
#define ANIM_MASTER_ROT   128
#define FALLING           256

/* Flags dell'attore, COMMON.H:149-168 (SPRITE_3D e SPRITE_CLIP stanno gia'
 * piu' sopra). Sono quelli che la collisione guarda. */
#define CHECK_OBJ_COL     1
#define CHECK_BRICK_COL   2
#define CHECK_ZONE        4
#define COL_BASSE         32      /* 1 = niente collisioni ALTE per Twinsen */
#define CHECK_CODE_JEU    64
#define OBJ_FALLABLE      2048
#define MINI_ZV           32768u  /* ZV quadra sul lato piu' corto           */

/* Fiche FILE3D dell'eroe, una per comportamento (DEFINES.H:73-77). A inizio
 * partita il comportamento e' normale, quindi la fiche e' la 0. */
#define FILE_3D_NORMAL    0

/* LIB_3D.H: valore interpolato nel tempo (rotazioni morbide). Nessuno lo usa
 * ancora, ma sta dentro T_OBJET e la struct va rispettata campo per campo. */
typedef struct {
    WORD  StartValue;
    WORD  EndValue;
    UWORD TimeValue;
    ULONG MemoTicks;
} T_REAL_VALUE;

/* T_OBJET, DEFINES.H:116-209, campo per campo e nell'ordine di upstream.
 * Non e' pedanteria: da M4 in poi entrano p_anim.c, object.c e gli interpreti
 * di script, che sono gia' scritti e gia' debuggati contro questa forma
 * (decisione 3.7). Cambiare un nome qui vuol dire riscoprire le mine di S17. */
typedef struct {
    UBYTE GenBody;              /* corpo generico, non un indice di BODY.HQR */
    UBYTE GenAnim;
    UBYTE NextGenAnim;
    UBYTE Col;

    UBYTE *PtrAnimAction;
    WORD  Sprite;
    WORD  OffsetLabelTrack;
    UBYTE *PtrFile3D;           /* fiche: risolve generico -> slot HQR */

    WORD  OptionFlags;
    WORD  NbBonus;
    WORD  Armure;
    WORD  CoulObj;

    WORD  Body;                 /* indice in PtrBody[], come upstream */

    WORD  PosObjX, PosObjY, PosObjZ;
    WORD  OldPosX, OldPosY, OldPosZ;
    WORD  Xmin, Xmax, Ymin, Ymax, Zmin, Zmax;   /* ZV */

    WORD  Beta;
    WORD  SRot;
    T_REAL_VALUE RealAngle;
    WORD  Move;

    UBYTE *PtrTrack;
    WORD  OffsetTrack;
    UBYTE *PtrLife;
    WORD  OffsetLife;

    WORD  Info, Info1, Info2, Info3;
    WORD  ObjCol, CarryBy;
    WORD  ZoneSce, LabelTrack, MemoLabelTrack;
    WORD  Flags, WorkFlags;
    WORD  HitBy, HitForce;
    WORD  LifePoint;
    WORD  AnimStepBeta, AnimStepX, AnimStepY, AnimStepZ;
    WORD  DoorWidth;
    WORD  Anim, Frame, FlagAnim;
    UBYTE CodeJeu;

    /* M4, E NON SONO IN UPSTREAM. Lo stato temporale dell'animazione: da quale
     * fotogramma si sta venendo, e a che tick ci si e' arrivati.
     *
     * Upstream li tiene DENTRO IL CORPO, ai byte +16..+23 (`get_object_anim_
     * state`, P_ANIM.C:58). Non si puo' fare per due ragioni indipendenti:
     * il corpo per noi e' un puntatore dentro la CARTUCCIA, e li' quei byte
     * sono condivisi da tutti gli attori che usano lo stesso corpo - due
     * guardie con lo stesso modello si pestano lo stato a vicenda. Qui sono per
     * attore, che e' dove appartengono.
     *
     * Aggiungere campi in CODA non rompe la regola della struttura (decisione
     * 3.7): quella vieta di rinominare o riordinare i campi che il codice
     * portato scrive come `ptrobj->Campo`, e nessuno di quelli si muove.
     *
     * `AnimSrcFrame` e' un NUMERO di fotogramma, mentre upstream tiene un
     * puntatore. La differenza si vede quando un'animazione ne sostituisce
     * un'altra: li' il puntatore resta dentro la VECCHIA e il raccordo parte
     * dall'ultima posa davvero assunta. Ci si arriva quando ci sara' un
     * comportamento a cambiare animazione; finche' l'animazione e' una sola i
     * due coincidono. */
    WORD  AnimSrcFrame;
    ULONG AnimMemoTicks;

    /* M6, E NEMMENO QUESTO E' IN UPSTREAM - ma la cosa che nomina si'.
     *
     * Quando un'animazione ne SOSTITUISCE un'altra, il raccordo non parte da un
     * fotogramma: parte dalla posa che il corpo sta tenendo in quell'istante, a
     * meta' strada fra due fotogrammi di un'animazione che sta per sparire. Il
     * gioco la fotografa con `StockInterAnim` (P_ANIM.C:669) dentro un anello di
     * 5000 byte e ci fa puntare la sorgente.
     *
     * Qui il puntatore sta nell'attore invece che nei byte +16 del corpo, per la
     * stessa ragione gia' scritta sopra per AnimSrcFrame: il corpo e' in
     * cartuccia e due attori con lo stesso modello si pesterebbero la posa.
     *
     * Vale finche' `AnimSrcFrame` e' NEGATIVO. Al primo fotogramma che arriva,
     * `Body_AnimTick` rimette li' un numero di fotogramma e la fotografia
     * smette da se' di essere la sorgente: dura esattamente il tempo del
     * raccordo, come nel gioco. */
    WORD *PtrPoseSrc;
} T_OBJET;

extern T_OBJET ListObjet[MAX_OBJETS];
extern WORD    NbObjets;

/* I corpi caricati, come upstream: `Body` di T_OBJET e' un indice QUI, non uno
 * slot di BODY.HQR. Tenerlo cosi' invece di mettere lo slot dentro `Body` non
 * e' una scelta di stile: p_anim.c e object.c del porting 32X scrivono
 * `PtrBody[ptrobj->Body]`, e da M4 arrivano loro. */
extern UBYTE  *PtrBody[MAX_BODYS];
extern WORD    NbBodys;

/* Lo slot di BODY.HQR da cui viene PtrBody[i]. Upstream non ne ha bisogno
 * perche' marca la cache scrivendo il bit 15 dentro la fiche; qui la fiche sta
 * in una voce decompressa che la cache HQR puo' riusare, e sporcarla
 * significherebbe che la seconda lettura vede un dato diverso dalla prima. */
WORD  HqrBodySlotOf(WORD bodyidx);

int   LoadScene(int numscene);        /* 0 = struct non percorsa correttamente */
void  InitObject(WORD numobj);        /* uno slot ai valori di partenza        */
/* L'eroe UNA VOLTA A PARTITA, non a ogni stanza: angolo, corpo e punti vita
 * attraversano le porte (ChangeCube non li tocca), posizione e script no. */
void  InitPerso(void);
void  StartInitAllObjs(void);         /* attacca a ogni attore il suo corpo    */

/* SearchBody, FICHE.C:585: cammina la fiche FILE3D dell'attore e risolve un
 * corpo generico in uno slot di BODY.HQR, caricandolo se e' la prima volta.
 * Torna l'indice in PtrBody[], oppure -1 se la fiche non ha quel generico. */
WORD  SearchBody(UBYTE numbody, WORD numobj);

/* SearchAnim, FICHE.C:529: la stessa fiche, comando 3. Risolve un'animazione
 * generica in uno slot di ANIM.HQR. Torna -1 se la fiche non ha quel generico. */
WORD  SearchAnim(UBYTE numanim, WORD numobj);

/* ===== M6: L'EROE SI COMANDA (object_move.c) ===== */
void  Game_Frame(void);                       /* DoDir(NUM_PERSO), OBJECT.C:2129 */
WORD  InitAnim(UBYTE gennewanim, WORD flag,   /* OBJECT.C:1140                   */
               UBYTE gennextanim, WORD numobj);
/* StockInterAnim, P_ANIM.C:669: fotografa la posa che l'attore tiene ADESSO e
 * la rende la sorgente del raccordo. La chiama InitAnim, e sta in body_draw.c
 * perche' li' c'e' chi sa calcolare una posa. */
void  Body_SnapPose(WORD numobj);

/* La COLLISIONE col decoro. `Move_Collide` e' il tratto di DoAnim fra la
 * posizione che l'animazione propone e quella che l'attore riceve: la chiama
 * Body_AnimTick, ed e' l'unica cosa che sta fra Twinsen e i muri. */
void  Move_Collide(WORD numobj, LONG *px, LONG *py, LONG *pz);

/* LA CAMERA (S42). `Bg_CameraFollow` e' la regola di PERSO.C:1132; `Bg_ScrollTo`
 * sposta il fondo col Blitter e ridisegna la sola banda che entra, invece di
 * rifare le 756 tessere. */
void  Bg_CameraFollow(void);
void  Bg_ScrollTo(long nvx, long nvy);
/* WorldColBrick / WorldColBrickFull, GRILLE_A.C:114 e :35. Il codice di
 * collisione di una cella; la seconda guarda anche sopra la testa. */
UBYTE Grid_ColBrick(LONG xw, LONG yw, LONG zw);
UBYTE Grid_ColBrickFull(LONG xw, LONG yw, LONG zw, LONG ymax);
/* La cella che l'ULTIMA interrogazione ha guardato. Globali come in upstream:
 * ReajustPos le legge per sapere di quale cella sta calcolando la rampa. */
extern LONG XMap, YMap, ZMap;
void  ManualRealAngle(T_OBJET *ptrobj);
void  ClearRealAngle(T_OBJET *ptrobj);
void  InitRealAngleConst(WORD start, WORD end, LONG duration,
                         T_REAL_VALUE *ptrstruct);
WORD  GetRealAngle(T_REAL_VALUE *ptrstruct);

extern WORD LastJoyFlag, LastMyJoy, LastMyFire;

/* ===== GEOMETRIA ISOMETRICA =====
 * Stavano dentro scene_bg.c finche' a proiettare era solo il fondo. Da M3c
 * proietta anche body_draw.c, e due copie degli stessi numeri in due file
 * sarebbero due cose che possono divergere - lo stesso difetto che la mappa di
 * memoria evita tenendo un master unico.
 *
 * I passi vengono da Map2Screen (GRILLE_A.ASM:446): sono per CELLA di griglia.
 * Il rapporto 2:1 fra X e Y e' l'invariante dell'isometrica di LBA1.
 *
 * I DUE CENTRI SONO DIVERSI, e non e' un refuso:
 *   tessere  Map2Screen                  320-8-23-1 = 288,  240-25 = 215
 *   attori   Init3DGame, GAMEMENU.C:226     320-8-1 = 311,  240
 * Un attore proiettato col centro delle tessere cade fuori posto di (23,25) in
 * ogni stanza e con ogni camera: uno scostamento COSTANTE, che si vede come
 * "il personaggio non poggia dove dovrebbe" e non come geometria storta.
 */
#define ISO_X        24     /* passo X per cella                  */
#define ISO_Y        12     /* passo Y per cella                  */
#define ISO_GY       15     /* passo Y per livello                */
#define CAM_CX      288     /* centro di proiezione delle TESSERE */
#define CAM_CY      215
#define ACT_CX      311     /* centro di proiezione degli ATTORI  */
#define ACT_CY      240

/* Il coefficiente della Y in unita' di MONDO invece che di cella: Proj_ISO lo
 * usa cosi' (P_OB_ISO.ASM:1290), e i 30 sono i 15 per livello per il rapporto
 * 512/256 fra i due passi del cubo. Stesso numero, due unita'.
 *
 * La divisione e' uno SPOSTAMENTO di 9, non una `/ 512`: sui negativi il C
 * tronca verso lo zero mentre `sar` del 386 e `//` di Python vanno verso
 * -infinito. Un pixel di differenza per vertice, solo dove x-z e' negativo. */
#define ISO_Y_WORLD  30
#define ISO_SHIFT     9

/* ===== FONDO (M2b) =====
 * Quel che la costruzione del fondo ha prodotto, in una struct sola. Non e'
 * diagnostica accessoria: sono i valori che tools/bg_ref.py calcola offline e
 * contro cui il fondo si verifica. "Si vede la scena" non e' un criterio - si
 * vede una scena anche con la camera sbagliata di una cella, con una tessera
 * su dieci mancante, con l'ordine del pittore invertito. */
typedef struct {
    WORD  camgx, camgz;         /* camera in celle, dalla regola del gioco   */
    WORD  vx, vy;               /* offset di schermo che ne derivano         */
    UWORD gridodd;              /* colonne della griglia fuori misura (0!)   */
    UWORD bricks;               /* tessere decodificate                      */
    ULONG worklen;              /* byte occupati nel buffer di lavoro        */
    UWORD celllist;             /* celle del cubo che nominano un brick      */
    UWORD cells;                /* celle passate al Blitter                  */
    UWORD overbrick;            /* tessere RIDISEGNATE davanti agli attori   */
    UWORD bbminx, bbminy;       /* riquadro dei pixel non trasparenti        */
    UWORD bbmaxx, bbmaxy;
    ULONG nonzero;
    ULONG sum1, sum2;           /* Fletcher: contenuto e ordine              */
    ULONG clut_sum;             /* somma della CLUT caricata                 */
    /* Durata di ogni fase in interrupt video. Non e' curiosita': il primo
     * caricamento e' costato 595 interrupt (~20 s) e senza scomporlo si
     * ottimizza il pezzo sbagliato. Sono contatori, non sonde: nessun valore
     * atteso, perche' dipendono dalla macchina. */
    UWORD t_grid, t_mark, t_brick, t_render, t_total;
    /* Byte occupati nell'arena per-cubo SUBITO DOPO averla liberata, cioe'
     * all'ingresso della stanza. Ha un valore atteso ed e' ZERO, sempre, per
     * qualunque stanza - ed e' l'unico numero dell'arena che si possa
     * verificare guardandolo: quello a caricamento finito dipende dalla scena,
     * quindi "e' cambiato" e "e' cresciuto" non si distinguono. */
    ULONG arena0;
    /* IL RETTANGOLO (S41). `boxes` e' quanti ne aveva la lista quando si e'
     * ripristinata, `boxcells` quante celle sono finite davvero sotto il
     * Blitter e `boxfull` quante volte la lista si e' riempita - se non e'
     * zero, quel quadro e' tornato al fondo intero e il confronto dei tempi
     * non vale. */
    UWORD boxes, boxfull;
    UWORD boxcells;
    /* Il TEMPO del ripristino. Scatta dentro la finestra di misura di un altro
     * stadio (BT_EMIT o M3d, a seconda di chi scrive per primo), quindi chi lo
     * racchiude lo SOTTRAE - come si fa gia' per la GPU e per M3d. Lo azzera
     * Body_ProfileReset insieme agli altri. */
    ULONG t_cls;
    /* Ingombro massimo di una tessera decodificata, HOTX/HOTY compresi. Lo
     * misura decode_bricks sui brick VERI della stanza invece di prendere un
     * limite generoso: e' il margine del rifiuto grezzo di Bg_ClsBoxes, e un
     * margine troppo largo li' costa un blit inutile per cella. */
    UWORD spanx, spany;
} T_BGSTAT;

extern T_BGSTAT BgStat;

int  Bg_Build(int numscene);    /* 0 = scena o risorsa mancante */

/* Rimisura il framebuffer e riscrive riquadro, conteggio e somme dentro
 * BgStat. Bg_Build la chiama gia' per conto suo; esiste come funzione perche'
 * i corpi si disegnano DOPO, e le loro sonde vogliono le stesse cinque misure
 * sul composto. Due misure separate distinguono "fondo giusto e corpi
 * sbagliati" da "fondo gia' sbagliato" - con una sola non si distinguerebbero. */
void Bg_Measure(void);

/* Posizione d'ingresso per la PROSSIMA Bg_Build, come SceneStartX/Y/Z di
 * ChangeCube (OBJECT.C:522): sostituisce il punto di partenza della stanza e
 * quindi cambia la camera. Vale una volta sola; dopo si torna al comportamento
 * normale, che e' quello di una stanza caricata da capo e non da una porta. */
void Bg_EnterAt(long x, long y, long z);
void Bg_Redraw(void);           /* solo i blit: cancella la diagnostica      */

/* ===== IL RETTANGOLO INVECE DELLA STANZA (S41) =====
 *
 * `AddPhysBox` (OBJECT.C:3025) accumula il rettangolo di ogni attore disegnato;
 * `ClsBoxes` (FLIPBOX.C:129) ripristina il fondo SOLO li' dentro. Nel gioco il
 * ripristino e' una copia da una pagina pulita tenuta a parte - su Jaguar
 * quella pagina sarebbe 370 KB che la mappa non ha (BRICK_WORK vuole gia' 589
 * KB su 576 per le stanze peggiori), quindi il fondo dei rettangoli si RIFA
 * dalle tessere, che sono gia' decodificate e residenti.
 *
 * Costa quel che costa DrawOverBrick, che gira da S37: la stessa `blit_cell`,
 * lo stesso ritaglio, gli stessi pixel. La differenza e' quali celle - li'
 * quelle DAVANTI all'attore, qui tutte quelle che toccano il rettangolo.
 *
 * Gli estremi sono INCLUSIVI.
 *
 * LE DUE CHIAMATE SI USANO IN COPPIA, e l'ordine e' il punto:
 *
 *   Bg_BoxReset()    in TESTA alla passata. La lista di quel che era disegnato
 *                    diventa "da cancellare" e la cancellazione si ARMA. Non
 *                    cancella niente: se cancellasse qui, lo schermo resterebbe
 *                    senza attori per tutto il tempo del calcolo.
 *   Bg_ClsPending()  alla PRIMA scrittura sul framebuffer, da chi sta per
 *                    scrivere. Idempotente: dopo la prima non fa piu' niente.
 *                    Da chiamare anche a fine passata, perche' una passata che
 *                    non disegna niente deve comunque cancellare quel che
 *                    c'era. */
void Bg_AddBox(WORD x0, WORD y0, WORD x1, WORD y1);
void Bg_BoxReset(void);
void Bg_ClsPending(void);

/* M3d: le tessere DAVANTI a un attore, ridisegnate sopra di lui e ritagliate al
 * suo rettangolo. Da chiamare subito dopo aver disegnato l'attore, come fa
 * AffScene (OBJECT.C:3023 per i corpi, 3078/3089 per gli sprite): e' quel che
 * fa sparire un personaggio dietro un muro e mette una porta davanti a chi ci
 * passa dietro. Gli estremi del rettangolo sono INCLUSIVI.
 *
 * Le coordinate di cella dell'attore, OBJECT.C:3019-3022:
 *     xm = (PosObjX + DEMI_BRICK_XZ) / SIZE_BRICK_XZ
 *     ym =  PosObjY / SIZE_BRICK_Y,  piu' uno se (Col & 127)
 *     zm = (PosObjZ + DEMI_BRICK_XZ) / SIZE_BRICK_XZ                       */
void Bg_DrawOverBrick(WORD xm, WORD ym, WORD zm,
                      WORD cx0, WORD cy0, WORD cx1, WORD cy1);
void Bg_DrawOverBrick3(WORD xm, WORD ym, WORD zm,
                       WORD cx0, WORD cy0, WORD cx1, WORD cy1);
/* S49: le stesse tessere dentro il buffer dello sprite dell'eroe. `base` e' la
 * base VIRTUALE (ACTOR_SPRITES - y0*640), cosi' il ritaglio resta in
 * coordinate di schermo. Vedi il commento esteso in scene_bg.c. */
void Bg_DrawOverBrickSpr(WORD xm, WORD ym, WORD zm,
                         WORD cx0, WORD cy0, WORD cx1, WORD cy1,
                         ULONG base);

/* COMMON.H:58-60. La cella del cubo in unita' di mondo. */
#define SIZE_BRICK_XZ  512
#define SIZE_BRICK_Y   256
#define DEMI_BRICK_XZ  256
void Bg_ClutDiag(void);         /* forza i due indici della diagnostica      */
ULONG Bg_LoadPalette(void);

/* ===== CORPI (M3c) =====
 * Quel che il disegno dei corpi ha prodotto. Come per il fondo, non e'
 * diagnostica accessoria: sono i valori che tools/body_ref.py calcola offline
 * sugli stessi byte, e "si vede Twinsen" non e' un criterio - si vede un
 * Twinsen anche con la posa non assemblata, con la luce dal lato sbagliato,
 * con il Beta ignorato e con l'ordine invertito.
 *
 * Le tre coppie di somme coprono TRE STADI DIVERSI, ed e' il punto: dicono
 * QUALE ha ceduto, non solo che qualcosa ha ceduto.
 *   geom   i vertici proiettati  -> posa, matrice, proiezione
 *   shade  le intensita'         -> luce, rotazione normali, divisione
 *   order  l'ordine di disegno   -> faccia nascosta, chiave, ordinamento
 * Se tutte e tre tengono e cadono solo le somme del composto, il difetto e'
 * nel riempimento - che e' anche l'unico pezzo destinato a sparire. */
/* Gli stadi della pipeline dei corpi, nell'ordine in cui girano. L'ordine e'
 * anche quello delle righe a schermo, cosi' la colonna si legge come la
 * pipeline. */
enum {
    BT_POSE = 0,        /* parse del corpo + posa a riposo della gerarchia   */
    BT_VERT,            /* rotazione, traslazione, proiezione isometrica     */
    BT_SHADE,           /* una intensita' per normale                        */
    BT_CULL,            /* faccia nascosta + chiave del pittore              */
    BT_SORT,            /* shell sort dell'ordine di disegno                 */
    BT_EMIT,            /* angoli, tinta, vertici nel flusso (S44: build_edges
                         * sta sulla GPU, dentro BT_GPU)                     */
    BT_GPU,             /* il lancio: stretta di mano + riempimento          */
    BODY_TSTAGES
};

typedef struct {
    ULONG light;                /* vettore luce X|Y|Z, un byte per asse      */
    WORD  avx, avy;             /* offset di schermo degli ATTORI            */
    ULONG geom[2], shade[2], order[2];
    UWORD tot, live;            /* poligoni totali | dopo la faccia nascosta */
    UWORD drawn;                /* attori che hanno prodotto pixel           */
    UWORD clipped;              /* attori scartati dal preclip (AffScene)    */
    UWORD invis;                /* attori scartati per INVISIBLE (stessa riga)*/
    ULONG polys;                /* poligoni che hanno prodotto pixel         */
    ULONG pixels;
    UWORD first, firstslot, firstpolys;
    WORD  firstx, firsty;
    /* La GPU che non risponde. Senza queste tre il 68000 resta nell'attesa per
     * sempre e non si vede NIENTE: ne' quale poligono, ne' con che dati, ne'
     * le altre sonde. Con un guardiano il disegno prosegue saltando quel
     * poligono, e il blocco smette di essere un sintomo e diventa un numero. */
    ULONG gpuhang;              /* quante volte la GPU non ha alzato DONE    */
    ULONG hangwhere;            /* il PRIMO: poligoni gia' fatti | bordi     */
    ULONG hangwhat;             /* il PRIMO: ymin | ymax SCRITTI dal 68000    */
    ULONG hangsaw;              /* il PRIMO: ymin | ymax che la GPU ha LETTO  */
    ULONG hangsawc;             /* il PRIMO: bordi che la GPU ha LETTO        */
    /* Il SECONDO kernel, lo stadio dell'intensita' (S40, src/gpu_shade.s).
     * Contatori suoi e non condivisi con quelli sopra: se i due kernel
     * sommassero nello stesso long, "quale dei due non risponde" tornerebbe a
     * essere una deduzione invece di una lettura.
     *
     * `shadeleft` sono le normali che il descrittore non ha coperto, sommate su
     * tutti gli attori: DEVE valere zero, e lo dice da solo senza confrontare
     * due schermate - la voce di coda di shade_spans esiste apposta. */
    ULONG shadehang;            /* quante volte il kernel non ha alzato DONE  */
    ULONG shadesaw;             /* il PRIMO: normali<<16 | voci che ha LETTO  */
    ULONG shadeleft;            /* normali scoperte all'uscita, atteso 0      */
    /* Il TERZO kernel, il front-end dei corpi (S45, src/gpu_front.s).
     * Stessa regola dei due sopra: contatori suoi, cosi' "quale dei tre non
     * risponde" resta una lettura e non una deduzione. */
    ULONG fronthang;            /* quante volte il kernel non ha alzato DONE  */
    ULONG frontsaw;             /* il PRIMO: voci desc<<16 | nb_points LETTI  */
    /* Dimensionamento del lancio singolo (S34). Un lancio per poligono sono
     * ~110 strette di mano per stanza, cioe' 110 occasioni di corsa; la
     * soluzione per costruzione e' un flusso di poligoni e UN lancio. Queste
     * due dicono quanto deve essere grande il flusso, MISURATO sui dati veri
     * invece che stimato. */
    ULONG batchmax;             /* byte di flusso del PIU' GROSSO attore      */
    ULONG batchtot;             /* byte di flusso di tutta la stanza          */
    ULONG launches;             /* quante volte e' partita la GPU             */
    ULONG jobs;                 /* poligoni accodati = i lanci di prima di S34 */
    /* Il costo per STADIO, in tick, sommato su tutti gli attori e su tutte le
     * passate del profilo. Sette e non uno perche' la lezione di S30 e' che il
     * costo vero sta dove non lo cerchi: sapere che i corpi costano N non serve
     * a niente, sapere QUALE stadio li costa si'.
     * Non li azzera Body_DrawAll: li azzera Body_ProfileReset, altrimenti il
     * profilo a ripetizione non potrebbe sommare. */
    ULONG t_stage[BODY_TSTAGES];
    ULONG t_reps;               /* passate sommate dentro t_stage             */
    /* M3d SCORPORATO DA BT_EMIT (S39). `Bg_DrawOverBrick` gira dentro la
     * finestra di BT_EMIT ma NON e' aritmetica di bordi: e' il fondo ridisegnato
     * davanti all'attore, cioe' lavoro che sulla GPU non traslochera'. Finche'
     * era dentro, il 47% di BT_EMIT sembrava tutto guadagnabile e non lo e'.
     *
     * Sta FUORI da t_stage[] apposta: aggiungere un ottavo stadio avrebbe
     * spostato la riga della GPU e sarebbe finito addosso alla prima riga
     * dell'audio. Riga sua, etichetta D, che era libera.
     *
     * Le linee e le sfere restano dentro BT_EMIT: quelle sulla GPU ci vanno
     * davvero (sono gia' lavori nel flusso da S37), quindi la loro quota va
     * contata col resto di cio' che trasloca. */
    ULONG t_over;               /* solo Bg_DrawOverBrick, gia' tolto da BT_EMIT */
    /* S49: PERCHE' L'EROE NON E' DIVENTATO UNO SPRITE DELL'OP.
     *
     * `why | larghezza | altezza | x0>>3`, un byte l'uno. Il byte alto ha un
     * bit per condizione, acceso se PASSA (1=eroe, 2=riquadro valido, 4=ci sta
     * in altezza, 8=ci sta in larghezza, 10=acceso), quindi:
     *
     *   1F  lo sprite lavora
     *   FF  l'eroe non e' mai arrivato alla decisione (preclippato, INVISIBLE,
     *       o senza corpo): e' la quinta causa, che le quattro condizioni non
     *       sanno esprimere
     *   0B  non ci sta in ALTEZZA      07  non ci sta in LARGHEZZA
     *   01  il riquadro non e' valido
     *
     * Esiste perche' su x86 la decisione passa in tutte e tre le stanze del
     * riferimento e sul Jaguar no: i due bersagli riempiono `bb[]` per strade
     * diverse (vert_stage contro il kernel front-end), ed e' l'unico punto in
     * cui possono divergere. Gli altri tre byte servono a leggere il PERCHE'
     * nello stesso colpo d'occhio invece di tornare a indovinare. */
    ULONG sprstat;
    /* BT_EMIT SPEZZATO (S41), voce che il todo di S39 chiedeva PRIMA della
     * migrazione: senza, si attribuirebbe alla GPU un guadagno che non ha fatto
     * ([[feedback_spostare_non_e_togliere]]). Restano DENTRO t_stage[BT_EMIT],
     * che continua a essere il totale: qui si legge come e' fatto.
     *
     *   t_edges   solo build_edges: le divisioni per bordo. DA S44 VALE ZERO
     *             sul Jaguar - build_edges sta sulla GPU, dentro BT_GPU - e
     *             la riga 26 che lo mostra a zero E' la misura del trasloco
     *   t_stream  solo jobs_emit: la copia dei VERTICI nel flusso in DRAM
     *   il resto  = BT_EMIT - t_edges - t_stream - t_over: angoli, tinta,
     *              poly_at, cioe' la LETTURA del corpo dalla cartuccia
     *
     * Il campionamento e' a grana di tick video come tutto il resto: il singolo
     * poligono sta sotto la risoluzione, ma la somma su centinaia di poligoni e
     * su piu' passate e' una stima onesta di come si divide il 55%. */
    ULONG t_edges, t_stream;
    /* LINEE E SFERE (S37). Tre numeri per tre diagnosi diverse, come per gli
     * sprite: quante entrano nella lista, DOVE cade il centro della prima
     * sfera (cioe' il vertice e la proiezione), e quanti pixel escono (cioe'
     * la regola (rayon*34)>>9, il cerchio di Bresenham e il ritaglio di
     * Line_A). `entpix` si conta all'accodamento e non nel flusso: e' quel che
     * il generatore di tratte CHIEDE, ed e' proprio quel che va confrontato
     * con l'oracolo. Che poi la GPU li scriva davvero lo dicono PIXELS e le
     * somme del composto. */
    UWORD lines, spheres;       /* entita' entrate nella lista ordinata       */
    ULONG entpix;               /* pixel chiesti da linee e sfere             */
    WORD  firstsx, firstsy;     /* centro a schermo della prima sfera         */
    UWORD firstsr;              /* e il suo raggio a schermo                  */
    UBYTE havesphere;
    /* S46, IN CODA APPOSTA: gli offset dei campi sopra sono negli script di
     * sonda BigPEmu, e un campo infilato in mezzo li farebbe leggere tutti
     * sfalsati. Nel modo lista il flusso non si puo' svuotare a meta' blocco:
     * una tratta che non ci sta si SCARTA e si conta qui. Atteso 0 - il
     * massimo misurato sulle tre stanze e' ~3 KB contro i 32 del flusso. */
    UBYTE pad46;
    UWORD rundrop;              /* tratte scartate a flusso pieno, atteso 0   */
    /* S47: LA VERIFICA RESIDUA DELLA DIAGNOSI S46. Il cursore anim dell'EROE
     * come pose_load lo vede DAVVERO nell'ultima passata: fotogramma,
     * sorgente, trascorso, durata, ramo preso, e la terna del gruppo 2 (il
     * gruppo che la caccia S46 ha indiziato: host inclinato dal respiro,
     * Jaguar a identita'). Riempiti da pose_load per ListObjet[0] su TUTTI i
     * bersagli, cosi' host, BigPEmu e ferro mostrano lo stesso dato preso
     * nello stesso punto. In coda per la stessa ragione di rundrop. */
    UWORD pinfrm;               /* o->Frame                                   */
    UWORD pinsrc;               /* AnimSrcFrame ($FFFF = fotografia)          */
    ULONG pincur;               /* TimerRef - AnimMemoTicks                   */
    UWORD pintot;               /* durata del fotogramma d'arrivo             */
    UWORD pinarr;               /* 1 = arrivato (copia integrale), 0 = interp */
    ULONG pintref;              /* TimerRef al momento della posa             */
    ULONG ping2a;               /* gruppo 2 di wpose: typ<<16 | (UWORD)v0     */
    ULONG ping2b;               /* gruppo 2 di wpose: v1<<16 | (UWORD)v2      */
} T_BODYSTAT;

extern T_BODYSTAT BodyStat;

/* Tutti gli attori della stanza sul fondo gia' composto. Da chiamare dopo
 * Bg_Build e prima di Bg_Measure. */
void Body_DrawAll(void);

/* Azzera i contatori per stadio. Da chiamare una volta prima di una passata
 * sola (misura grezza) o prima di un giro di profilo (misura a ripetizione). */
void Body_ProfileReset(void);

/* S47: arma la fotografia del cursore anim (i campi pin* di BodyStat).
 * Scatta UNA volta, sulla prima posa dell'eroe della passata successiva:
 * armare subito prima della passata-sonda, o la fotografia mostra il
 * respiro in corso invece della posa confrontata. */
void Body_PinArm(void);

/* L'orologio dell'animazione: fa arrivare i fotogrammi scaduti e ne comincia
 * uno nuovo. UNA volta per quadro di gioco e PRIMA di Body_DrawAll - non una
 * volta per disegno, o l'animazione andrebbe alla velocita' dei ridisegni. */
void Body_AnimTick(void);

/* Le normali convertite in forma nativa vivono nell'arena PER-CUBO. Va chiamata
 * dove si azzera NbBodys, cioe' quando quell'arena sta per essere riusata. */
void Body_NormCacheReset(void);

/* Come LoadMalloc, ma allineato alla phrase: per i dati che leggera' LOADP. */
void *LoadMallocPhrase(ULONG size);

/* ===== M3e: GLI SPRITE DEGLI ATTORI (engine_c/sprite_draw.c) =====
 * L'altro ramo di AffScene. Il ciclo e l'ordine di disegno restano di
 * Body_DrawAll: qui c'e' solo il "come si disegna uno sprite". */
typedef struct {
    UWORD drawn;                /* passati il ritaglio e disegnati            */
    UWORD clipped;              /* rettangolo di ritaglio vuoto: non disegnati*/
    UWORD still;                /* fermi e non sporcati: NON ridisegnati (S42)*/
    ULONG pixels;               /* pixel scritti nel fondo                    */
    WORD  firstx, firsty;       /* angolo alto-sinistro del primo disegnato   */
    UBYTE havefirst;
} T_SPRITE_STAT;

extern T_SPRITE_STAT SpriteStat;

/* Dov'era uno sprite l'ultima volta che e' stato disegnato. Serve a distinguere
 * "fermo" da "mosso" guardando il RETTANGOLO invece di una bandiera: quando le
 * porte cominceranno ad aprirsi (M5) la regola vale gia'. */
typedef struct {
    WORD  x0, y0, x1, y1;
    UBYTE valid;                /* disegnato almeno una volta                 */
    UBYTE moved;                /* in QUESTA passata il rettangolo e' cambiato*/
} T_SPRBOX;

void Sprite_StatReset(void);
void Sprite_ForgetBoxes(void);        /* al cambio di stanza */
void Sprite_DrawOne(const T_OBJET *o, WORD ax, WORD ay, LONG vx, LONG vy);

/* "Il fondo e' stato sporcato qui?" - lo chiede chi disegna qualcosa di fermo
 * per sapere se qualcun altro gli e' passato sopra. Vedi scene_bg.c. */
int   Bg_BoxDamaged(WORD x0, WORD y0, WORD x1, WORD y1);

/* ===== FRONT-END (S48) =====
 * La progressione d'avvio: logo Adeline, menu principale, e poi la partita.
 * Un passo per quadro, come tutto il resto del motore.
 *
 * Front_Frame torna 0 finche' il front-end e' a schermo e 1 quando l'utente ha
 * scelto "New game": da quel momento non va piu' chiamata, e il quadro
 * successivo e' quello che costruisce la stanza.
 *
 * Le sonde (front_menu.c le confronta con engine_c/menu_expect.h, generato da
 * tools/menu_ref.py) restano leggibili dopo: e' l'unico modo per verificare
 * due schermate che a quel punto non sono piu' a schermo. */
int   Front_Frame(void);
ULONG Front_Mask(void);
ULONG Front_Got(int i);

/* ===== DIAGNOSTICA A SCHERMO =====
 * Un font esadecimale 8x8 disegnato a 2x sul fondo. Non e' un lusso: da M2 in
 * poi la sola alternativa e' dedurre lo stato dai colori, ed e' la strada che
 * in S24 ha prodotto la lezione CRY16. Costa 512 byte di ROM.
 *
 * Le righe sono numerate; Dbg_Row le posiziona da se'. Dbg_Value ridisegna
 * SOLO se il valore e' cambiato, quindi una riga statica costa zero per
 * quadro (il divieto di cicli su pixel sul 68000 vale anche per la
 * diagnostica). */
/* 48 righe su tre colonne di 16, vedi hal_debug.c. */
#define DBG_ROWS 48

void Dbg_Clear(void);
void Dbg_Value(int row, ULONG value);         /* 8 cifre esadecimali */
void Dbg_Fill(int x, int y, int w, int h, UBYTE colour);

#endif /* JAG_HAL_H */
