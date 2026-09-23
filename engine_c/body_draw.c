/* body_draw.c - M3c mossa 1: i corpi degli attori disegnati sul fondo.
 *
 * COSA FA, E IN CHE ORDINE
 * ------------------------
 * Per ogni attore con un corpo, e per ogni corpo letto DIRETTAMENTE dalla
 * cartuccia:
 *
 *   1. parse       il corpo dov'e', senza copie e senza blob pre-cotti
 *   2. posa        la gerarchia dei gruppi a riposo
 *   3. matrice     dal solo Beta che la scena assegna all'attore
 *   4. vertici     ruotati, traslati, proiettati in isometrica
 *   5. intensita'  una per normale: normale ruotata . luce / range
 *   6. faccia      quella nascosta si toglie, in coordinate di SCHERMO
 *   7. entita'     linee e sfere entrano nella STESSA lista dei poligoni
 *   8. ordine      chiave del pittore, ordinamento
 *   9. pixel       riempimento a scansione con gradiente 8.8
 *
 * NIENTE BLOB PRE-COTTI, ED E' LA LEZIONE DI S21
 * ----------------------------------------------
 * La V5 impacchettava il corpo 0 in tre blob separati (geometria, intensita',
 * sfere) generati a monte. Cambiare corpo voleva dire cambiarne TRE, e se se ne
 * dimenticava uno il risultato non era un corpo mancante ma un corpo con le
 * texture di un altro - un guasto che sembra un problema di rendering. Qui il
 * corpo si legge a runtime dallo stesso byte che il packer ci ha messo, e
 * cambiarlo e' cambiare un indice.
 *
 * DOVE STANNO I NUMERI CHE QUESTO FILE USA
 * ----------------------------------------
 * Da nessuna parte qui dentro. La geometria isometrica sta in hal.h con la sua
 * riga di sorgente accanto; la tabella dei seni in sintab.h, generata da
 * P_SINTAB.C; le capienze in mem_map.h, generate dalla mappa. L'unica cosa che
 * questo file decide e' l'ORDINE delle operazioni, che e' quello di
 * AffObjetIso.
 *
 * COME SI VERIFICA
 * ----------------
 * tools/body_ref.py fa le stesse otto cose in Python sugli stessi byte e
 * produce engine_c/body_expect.h. Le sonde non sono una sola: coprono TRE
 * stadi separati (vertici proiettati, intensita', ordine di disegno) piu' i
 * pixel. Una somma sola direbbe soltanto "no", e i quattro sbagli classici
 * della pipeline dei corpi - posa non assemblata, luce dal lato sbagliato,
 * Beta ignorato, ordine invertito - producono tutti una figura riconoscibile.
 *
 * QUEL CHE NON C'E' ANCORA, DICHIARATO
 * ------------------------------------
 * 1. INTRECCIO DI PROFONDITA' COL FONDO. Gli attori si disegnano SOPRA le
 *    tessere. Nel gioco DrawOverBrick li alterna per profondita', percio' un
 *    personaggio dietro un muro sparisce dietro il muro. Qui no. E' M3d.
 * 2. RITAGLIO DEI POLIGONI. Il gioco ritaglia il poligono contro il bordo
 *    prima di riempirlo; qui si limitano solo le righe e le colonne scritte.
 *    Vedi fill_poly. La regola dei BORDI invece non e' piu' un buco: da S33 e'
 *    la trascrizione di ComputePoly_A, bias di mezzo pixel compreso. Linee e
 *    sfere il ritaglio ce l'hanno: per loro NON e' un limite a valle, sposta
 *    gli estremi e cambia quali pixel escono, quindi andava trascritto.
 *
 * CHIUSO IN S37: linee e sfere. Un corpo LBA1 non e' solo poligoni - dopo di
 * essi il file elenca E_LIGNE ed E_SPHERE, che il gioco ordina INSIEME ai
 * poligoni nella stessa lista. Il corpo 1 ne ha 21 e 5, e le sfere sono le mani
 * e l'ornamento del casco.
 */
#include "hal.h"
#include "le.h"
#include "be.h"
#include "sintab.h"

/* Le due basi si possono sostituire dall'esterno, e non e' una comodita': e'
 * quel che permette di far girare QUESTO file, non una sua copia, su un PC con
 * i dati veri e di confrontarne le sonde con body_expect.h prima ancora di
 * costruire la cartuccia. Vedi engine_c/host_body.c. Un difetto di pipeline
 * trovato qui costa un secondo; trovato su BigPEmu costa una build. */
#ifndef BODY_WORK_BASE
#define BODY_WORK_BASE JAG_BODY_WORK
#endif
#ifndef BODY_FB_BASE
#define BODY_FB_BASE   JAG_BG_FB
#endif
/* La terza base e' il flusso dei lavori (S34). Sostituibile per la stessa
 * ragione delle altre due: sul banco di prova diventa un array normale, e cosi'
 * il modello C legge BYTE PER BYTE lo stesso flusso che leggera' la GPU. Se
 * fosse una struttura diversa il banco proverebbe un'altra cosa. */
#ifndef JOB_STREAM_BASE
#define JOB_STREAM_BASE JAG_JOB_STREAM
#endif
#ifndef JOB_STREAM_SIZE
#define JOB_STREAM_SIZE JAG_JOB_STREAM_SIZE
#endif
/* La quarta base (S45): il DESCRITTORE DI POSA, il contratto del kernel
 * front-end. Sul Jaguar vive dentro GPU_VARS (lo dichiara la mappa); sul banco
 * di prova e' un array normale, per la stessa ragione delle altre tre basi. */
#ifndef POSE_DESC_BASE
#define POSE_DESC_BASE JAG_GPU_POSE_DESC
#endif
/* La quinta base (S49): il BUFFER DELLO SPRITE dell'attore. Sostituibile per la
 * stessa ragione delle altre quattro - ma qui la ragione e' piu' stretta ancora:
 * S47 aveva archiviato l'attore-sprite come "roba da Object Processor" e per
 * questo non aveva mai provato il banco. La decisione che accende lo sprite
 * (sotto, dopo la fase V) NON tocca l'OP: e' aritmetica su un riquadro, e su
 * x86 si stampa in un secondo. */
#ifndef ACTOR_SPRITES_BASE
#define ACTOR_SPRITES_BASE JAG_ACTOR_SPRITES
#endif

/* Chi riempie: la GPU TOM sul Jaguar, il 68000 (cioe' l'x86 del banco di
 * prova) altrove. NON sono due algoritmi: `build_edges` e' lo stesso, e il
 * ciclo sulle righe qui sotto e' il MODELLO che src/gpu_body.s trascrive.
 * Tenerlo compilabile e' quel che permette a host_body.c di verificare su PC
 * l'aritmetica che la GPU eseguira', e di dire quale dei due ha torto quando i
 * pixel non combaciano. */
#ifndef BODY_FILL_ON_GPU
#define BODY_FILL_ON_GPU 1
#endif

/* ---------------------------------------------------------------------------
 * L'ATTORE COME SPRITE DELL'OP (S47). Il sasso da 9,75 tick.
 *
 * L'eroe non si disegna piu' NEL fondo: si disegna in un buffer suo, e
 * l'Object Processor lo compone sopra la scena in fase di scansione. Quel che
 * si compra e' tutto il lavoro di INTEGRAZIONE col fondo, che il gioco paga a
 * ogni quadro e che i motori 3D dell'epoca non pagavano affatto:
 *   - M3d (i brick che ricoprono l'attore)        5,25 tick
 *   - il ripristino del rettangolo                 4,5 tick
 * Il TRANS dell'OP e' provato sul ferro (S47), e i corpi non rendono nemmeno
 * un pixel a indice 0 (misurato, e sorvegliato da ZERO_PIX in body_ref.py).
 *
 * LA BASE E' VIRTUALE, E QUI STA IL TRUCCO CHE RENDE TUTTO QUESTO ECONOMICO.
 * Il buffer NON e' largo 128: e' largo 640 come il fondo, alto poche decine di
 * righe, e la sua base si dichiara SPOSTATA INDIETRO di y0 righe:
 *
 *     body_fb_base = ACTOR_SPRITES - y0 * 640
 *
 * cosi' il kernel, che calcola `fb + y*640 + x` col 640 COTTO DENTRO
 * (gpu_body.s: shlq #9 + shlq #7, tre punti) e taglia a BODY_XMAX/YMAX,
 * atterra da solo su `ACTOR_SPRITES + (y - y0)*640 + x`. Conseguenze:
 *   1. il kernel GPU non cambia di una riga - quello provato sul ferro in
 *      S44/S45/S46 resta bit per bit quello;
 *   2. l'attore si proietta alle coordinate DI SEMPRE: GEOM, SHADE, ORDER,
 *      DRAWN e PIXELS non cambiano di un bit, e l'oracolo non deve imparare
 *      nessuna traslazione. Cambiano solo le quattro sonde del COMPOSTO, ed e'
 *      giusto che cambino: l'eroe non e' piu' nel framebuffer;
 *   3. una riga larga 640 fa da rete di sicurezza in X - un pixel oltre la
 *      finestra visibile finisce in un byte del buffer che nessuno guarda,
 *      non nella memoria del vicino.
 * Il BMobj ne mostra 128 px perche' IWIDTH (quanto legge per riga) e DWIDTH
 * (quanto avanza fra le righe) sono campi INDIPENDENTI.
 *
 * In Y la rete non c'e', quindi la mette il C: se il riquadro dell'attore non
 * ci sta nel buffer si torna a disegnarlo nel fondo, che e' sempre corretto.
 * ------------------------------------------------------------------------- */
/* ACCESO DA S49. La diagnosi di S47 ("l'eroe non compare") si e' chiusa in due
 * pezzi, e nessuno dei due era dove S47 aveva guardato:
 *
 *   1. la DECISIONE qui sotto non e' "roba da Object Processor": e' aritmetica
 *      su un riquadro. Messa sul banco su PC (host_body.c), passa tutte e
 *      quattro le condizioni in tutte e tre le stanze del riferimento -
 *      x0 &= ~7 compreso, che era il sospettato piu' sottile;
 *   2. le quattro scritture HAL erano avvolte in HW_L su nomi della mappa che
 *      sono GIA' l'lvalue. Doppia dereferenza: il C non ha mai scritto quelle
 *      phrase. E la sonda di S47 non poteva vederlo, perche' il valore di
 *      SPEGNIMENTO che il C calcola coincide bit per bit con quello che
 *      InitOpList lascia al boot - "scritto spento" e "non scritto" davano lo
 *      stesso campione ([[feedback_buco_simmetrico_oracolo]]).
 *
 * L'interruttore resta perche' resta utile: a zero il motore e' quello
 * collaudato sul ferro fino a S48, sonde del composto comprese. */
#ifndef BODY_ACTOR_SPRITE
#define BODY_ACTOR_SPRITE 1
#endif

/* Il bersaglio del riempimento per l'attore in corso. Vale BODY_FB_BASE per
 * tutti; per l'attore-sprite e' la base VIRTUALE del suo buffer, cioe' un
 * indirizzo che da solo non si dereferenzia mai - ci si arriva sempre dopo
 * aver aggiunto y*640, con y >= spr_y0. */
static UBYTE *body_fb_base = (UBYTE *)BODY_FB_BASE;

/* Lo stato dello sprite di QUESTA passata, scritto dopo il disegno. y0 e' la
 * riga di framebuffer che il buffer rappresenta; a `on` zero l'oggetto resta
 * spento e la scena si vede senza attore (che e' il comportamento giusto
 * quando e' fuori campo, non un ripiego). */
static WORD spr_x0, spr_y0, spr_y1;
static int  spr_on;

/* IL BANCO IN CUI SI STA DISEGNANDO (S49). L'altro e' quello che l'Object
 * Processor sta mostrando in questo momento.
 *
 * Con un banco solo l'eroe spariva per i tre quarti del quadro, MISURATO sul
 * ferro, e non era un ordinamento sbagliato delle scritture: e' che il disegno
 * di una posa dura quasi un quadro mentre l'OP legge il buffer a ogni campo, e
 * le phrase possono puntare a un'immagine COMPLETA o a niente - mai a una a
 * meta'. Quindi lo stato intermedio si vedeva per costruzione.
 *
 * Si disegna dietro e si scambia scrivendo SPR_DATA_A/B in coda a draw_actor,
 * dove le phrase si compongono gia'. Il banco che si lascia resta intatto fino
 * al prossimo giro: e' quello che l'OP continua a comporre nel frattempo. */
static int spr_bank;

/* Le righe trasparenti riservate in testa al buffer. Le consuma il bias del
 * campo pari di ViHandler (FIELD_A_ROW_BIAS, 2 sul ferro e 0 su BigPEmu);
 * tenerle sempre costa due righe di zeri e vale non far viaggiare quella
 * costante fin qui. */
#define SPR_TOP_MARGIN 2

/* La base del banco in cui si disegna ADESSO.
 *
 * NIENTE CAST A ULONG QUI. `ULONG` e' `unsigned int`, cioe' 32 bit sui due
 * bersagli - ma sul banco su PC la base e' l'indirizzo di un array, che a 64
 * bit non ci sta: il cast lo troncherebbe e il primo pixel scritto sarebbe un
 * segmentation fault. Sul Jaguar `ACTOR_SPRITES_BASE` e' gia' una costante a
 * 32 bit, quindi lasciarlo del suo tipo e' giusto su tutti e due. */
#define SPR_BANK_BASE (ACTOR_SPRITES_BASE \
                       + (unsigned long)spr_bank * JAG_SPR_BANK_BYTES)

#if BODY_ACTOR_SPRITE
/* PERCHE' spr_on E' RESTATO ZERO: i numeri della decisione, presi mentre si
 * prende. Non e' strumentazione di lusso - e' che "non si accende" ha quattro
 * cause diverse e indistinguibili dall'esterno, e in S47 si e' ipotizzato tre
 * volte invece di misurare una. `spr_why` ha un bit per condizione, acceso se
 * quella condizione PASSA: 0xF vuol dire acceso, e qualsiasi altro valore dice
 * subito quale delle quattro ha detto no.
 *
 * SI AGGANCIA ALL'EROE, non all'ultimo attore passato: altrimenti in una
 * stanza popolata la fotografia sarebbe di un NPC qualsiasi e direbbe sempre
 * "non e' l'eroe". E parte da -1, che e' la QUINTA causa - quella che le
 * quattro condizioni non sanno esprimere: l'eroe non e' mai arrivato fin qui,
 * perche' preclippato, INVISIBLE, o senza corpo. */
#define SPR_WHY_HERO  1     /* e' l'eroe (ListObjet[0])   */
#define SPR_WHY_BOX   2     /* il riquadro e' valido      */
#define SPR_WHY_ROWS  4     /* ci sta in altezza          */
#define SPR_WHY_COLS  8     /* ci sta in larghezza        */
/* E l'ESITO, che non e' ridondante con gli altri quattro: `spr_on` lo azzera
 * OGNI attore in testa alla decisione - deve farlo, o gli NPC salterebbero
 * M3d - quindi a fine passata vale l'esito dell'ULTIMO attore, non dell'eroe.
 * Un bit a parte, scritto solo dall'eroe, e' quel che rende la fotografia
 * leggibile in una stanza popolata. */
#define SPR_WHY_ON   16
static int  spr_why;
static WORD spr_bb[4];      /* act_x0, act_x1, act_y0, act_y1 come li ha visti */
static WORD spr_want_x0;    /* la x0 calcolata, arrotondamento compreso        */
#endif

#if BODY_ACTOR_SPRITE
/* La finestra VISIBILE del buffer, azzerata prima di disegnarci dentro: e'
 * lo zero che il TRANS legge come "qui non c'e' niente, fammi vedere il
 * fondo". Si azzera solo la finestra di QUESTO quadro perche' e' esattamente
 * quel che si vedra': al quadro dopo la finestra sara' un'altra e verra'
 * azzerata a sua volta. Fuori restano residui che nessuno guarda mai. */
static void spr_clear(void)
{
    WORD y;

    for (y = spr_y0; y <= spr_y1; y++) {
        ULONG *p = (ULONG *)(void *)(body_fb_base
                                     + (LONG)y * JAG_SPR_PITCH + spr_x0);
        int k = JAG_SPR_VIS_W / 4;

        while (k--)
            *p++ = 0;
    }
}
#endif
/* LO STADIO DELL'INTENSITA' SULLA GPU (S40, src/gpu_shade.s).
 *
 * Interruttore separato da BODY_FILL_ON_GPU, e non e' pedanteria: il piano e'
 * portare UNO stadio alla volta e chiedere alle sonde se e' ancora bit-exact
 * (voce 3 di docs/todo_s39.md). Con un interruttore solo, il primo lancio
 * dovrebbe essere giusto tutto insieme e i colpevoli possibili sarebbero sette.
 *
 * Metterlo a 0 fa tornare in servizio il modello in C, sulla stessa cartuccia:
 * e' la bisezione fra "l'aritmetica e' sbagliata" e "il Jaguar non la esegue
 * come previsto", e costa una build invece di una sessione. */
#ifndef BODY_SHADE_ON_GPU
#define BODY_SHADE_ON_GPU 1
#endif
/* IL FRONT-END SULLA GPU (S45, src/gpu_front.s): posa dei vertici +
 * proiezione (fase V) e faccia nascosta + chiave del pittore (fase C), in UN
 * lancio per attore. Le matrici dei gruppi restano al 68000: sono al piu' 25
 * per attore contro centinaia di vertici, e tenerle di la' evita di portare la
 * tabella dei seni nella SRAM della GPU.
 *
 * Stesso patto degli altri due interruttori: a 0 girano i modelli C qui sotto
 * (vert_stage/cull_stage), che sono la TRASCRIZIONE SORGENTE del kernel, sulla
 * stessa cartuccia. E' la bisezione, costa una build. */
#ifndef BODY_FRONT_ON_GPU
#define BODY_FRONT_ON_GPU 1
#endif
/* L'EMISSIONE SULLA GPU (S46, fase E di src/gpu_body.s): il riempitore legge
 * la lista ordinata e il corpo DA SOLO. wlist porta gia' (idx<<16|off) e
 * wscr porta gia' il long che il flusso voleva a +0 del vertice: al 68000
 * restano le TRATTE di linee e sfere, accodate nel flusso come BLOCCHI
 * [conteggio][tratte] in ordine di wlist - quando il walker della GPU trova
 * una voce entita' consuma il blocco successivo, e l'ordine del pittore si
 * conserva per costruzione.
 *
 * Stesso patto degli altri tre interruttori: a 0 torna il percorso di S34-S45
 * (poligoni imboccati nel flusso), sulla stessa cartuccia. E' la bisezione,
 * costa una build. Il kernel tiene TUTTI E DUE i modi e li sceglie a runtime
 * con GPU_EM_MODE, quindi i due lati non possono divergere per una build. */
#ifndef BODY_EMIT_ON_GPU
#define BODY_EMIT_ON_GPU 1
#endif
#if BODY_FILL_ON_GPU || BODY_SHADE_ON_GPU || BODY_FRONT_ON_GPU
#include "jag_hw.h"
#endif

/* ---------------------------------------------------------------------------
 * L'OROLOGIO DELLA MISURA (S34).
 *
 * E' il tick del vblank, lo stesso che scene_bg.c usa per gli stadi del fondo.
 * Non e' una scelta di comodo: e' l'UNICO orologio gia' validato su tutti e due
 * i bersagli. Un contatore piu' fine si ricaverebbe da VC (~64 us invece di
 * 16,7 ms), ma BigPEmu implementa solo in parte i registri di scansione
 * (feedback_bigpemu_interlace_limits), e passare la sessione a validare lo
 * strumento invece di misurare e' il modo classico di non misurare niente.
 *
 * La risoluzione che manca si COMPRA RIPETENDO: il profilo rifa la pipeline
 * finche' il totale non supera una soglia di tick, poi divide. Funziona perche'
 * Body_DrawAll e' idempotente - azzera le sue sonde in testa e riscrive gli
 * stessi pixel - quindi ripeterla e' fedele e l'ultima passata lascia comunque
 * le sonde giuste. Ed e' autoregolante: si adatta da se' ai 60 Hz del ferro e
 * ai 30 di BigPEmu senza sapere dove sta girando.
 *
 * Sul banco di prova su PC l'orologio non c'e' e non serve: li' si verificano i
 * NUMERI, non i tempi.
 * ------------------------------------------------------------------------- */
#if BODY_FILL_ON_GPU
#define BODY_CLOCK  ((ULONG)JAG_HAL_VBL_TICK)
#else
#define BODY_CLOCK  0UL
#endif

/* Formato del corpo: dal S43 e' il blob JBD pre-digerito al packing, e il
 * padrone del layout e' tools/body_digest.py - campi, offset e RAGIONI stanno
 * la', in un posto solo. Qui restano le costanti che il reader usa e i tre
 * secchi dei materiali, che sono ancora quelli del gioco.
 *
 * Quel che il digest ha tolto da questo file: little-endian composto a byte,
 * record dei poligoni a lunghezza variabile da CAMMINARE, indici di vertice
 * da dividere per 6 a ogni angolo. Due terzi del quadro erano LETTURA
 * (reference_profilo_corpi_s41); adesso ogni accesso e' una `move.w` su un
 * array a passo fisso - la stessa forma che la GPU scorrera' con LOAD/LOADP. */
#define MAT_GOURAUD_MIN        9      /* P_OB_ISO.ASM:330, `cmp cl, 9`  */
#define MAT_FLAT_MIN           7      /* P_OB_ISO.ASM:333, `cmp cl, 7`  */
#define JBD_MAGIC         0x4A44      /* 'JD' - se manca, packer e reader
                                       * parlano due formati diversi */
#define JBD_VSTRIDE            8      /* x, y, z, pad: una phrase        */
#define JBD_GSTRIDE           16
#define JBD_PSTRIDE            8
#define JBD_CSTRIDE            4      /* normale, vertice - gia' divisi  */

/* Come si trasforma un gruppo, LIB_3D.H:6-8. Il record di posa dice quale dei
 * tre, e i tre valori che seguono si leggono di conseguenza: angoli per
 * ROTATE, spostamenti per TRANSLATE. ZOOM non compare in nessuna delle 516
 * animazioni dell'archivio, e nel gioco il suo ramo e' commentato. */
#define TYPE_ROTATE            0
#define TYPE_TRANSLATE         1
#define TYPE_ZOOM              2
/* Il record di posa dentro il gruppo: tipo, tre valori. Nel DOS sta ai byte
 * +8..+15 del record da 38; qui in `wpose`, perche' il corpo e' in cartuccia. */
#define POSE_WORDS             4

/* ---------------------------------------------------------------------------
 * IL PRECLIP PER ATTORE, AffScene (OBJECT.C:2620-2634).
 *
 * Il gioco disegna gli attori in DUE passate: la prima scarta e riempie la
 * lista ordinata, la seconda disegna solo chi e' rimasto. Lo scarto prova UN
 * SOLO PUNTO - l'ancora dell'attore proiettata - contro una finestra allargata,
 * e chi non passa non entra mai in AffObjetIso.
 *
 * NON E' UN'OTTIMIZZAZIONE, E' UNA TRASCRIZIONE CHE MANCAVA. E costa: nella
 * scena 0 dieci attori su undici cadono fuori, e prima di S34 il porting pagava
 * per loro posa, vertici, intensita', faccia nascosta e ordinamento per intero
 * prima di scoprire che non disegnavano niente - 149 tick su 177, l'84% del
 * tempo dei corpi (reference_body_profile_s34).
 *
 * I margini sono generosi APPOSTA: e' un test su un PUNTO che fa le veci di un
 * test su un volume, quindi un modello alto la cui ancora sta appena sotto il
 * bordo si disegna lo stesso. Sono DEFINES.H:99-102, presi verbatim.
 *
 * LA FINESTRA E' QUELLA DELLO SCHERMO DOS (640x480), NON QUELLA DEL NOSTRO
 * BUFFER (640x592). Le righe in piu' esistono solo perche' un corpo ancorato in
 * basso vada scritto per intero invece che ritagliato; riscalare i margini su
 * 592 vorrebbe dire tenere attori che il PC DOS scarta, cioe' smettere di
 * essere una trascrizione proprio ai bordi - l'unico posto dove si vedrebbe.
 * E' la stessa scelta gia' fatta per il taglio delle tessere (bg_ref).
 * ------------------------------------------------------------------------- */
#define VIEW_X0        (-50)
#define VIEW_Y0        (-30)
#define VIEW_X1          680
#define VIEW_Y1          580
/* Le porte, COMMON.H:152. NON ESERCITATA: nelle tre stanze del riferimento
 * nessun attore con un corpo ha questa bandiera. */
/* COMMON.H:158, "not drawn but all computed". Nel gioco non e' un test a se':
 * sta nella STESSA condizione dello scarto per corpo (OBJECT.C:2617), quindi
 * cade PRIMA della proiezione dell'ancora, non accanto al preclip che gli
 * somiglia. Sono due scarti di natura diversa: il preclip e' geometrico e non
 * toglie un pixel, questo toglie roba che oggi si disegna - le 79 frecce
 * direzionali che il gioco usa come marcatori per gli script. */
#define INVISIBLE        512
#define VIEW_CLIP_X0  (-112)
#define VIEW_CLIP_Y0   (-50)
#define VIEW_CLIP_X1  (640 + 112)
#define VIEW_CLIP_Y1  (480 + 171)

static int actor_preclip(WORD ax, WORD ay, WORD flags)
{
    if (flags & SPRITE_CLIP)
        return ax > VIEW_CLIP_X0 && ax < VIEW_CLIP_X1
               && ay > VIEW_CLIP_Y0 && ay < VIEW_CLIP_Y1;
    return ax > VIEW_X0 && ax < VIEW_X1 && ay > VIEW_Y0 && ay < VIEW_Y1;
}

/* Lunghezza del vettore luce, P_DEFINE.ASH:2 meno cinque. Il commento
 * dell'autore accanto e' "je sais je sais...": non e' un errore da correggere,
 * e' il numero con cui il gioco ha calcolato tutte le sue intensita'. */
#define LIGHT_LEN             59

T_BODYSTAT BodyStat;


/* ---------------------------------------------------------------------------
 * BODY_WORK. Il lavoro su UN corpo alla volta, in una regione che la mappa
 * dimensiona e che il parse controlla di non sforare.
 *
 * L'ordine dei campi NON e' arbitrario: `wscr` e `wlist` sono coppie di long
 * e vanno allineati; `wv` e' una phrase per vertice. Con queste capienze ogni
 * blocco cade allineato (lo verifica tools/memory_map.py, che e' anche il
 * file che decide che ci stanno).
 * ------------------------------------------------------------------------- */
#define MAXV  JAG_BODY_MAX_VERTS
#define MAXN  JAG_BODY_MAX_NORMALS
#define MAXP  JAG_BODY_MAX_POLYS
#define MAXG  JAG_BODY_MAX_GROUPS

/* S45: LA FORMA E' QUELLA CHE LA GPU LEGGE E SCRIVE, ed e' la stessa lezione
 * di SHADE_SPAN e dei record di bordo: se il banco verificasse una disposizione
 * che sul Jaguar non esiste, non verificherebbe niente.
 *
 *   wv     posa locale: x,y,z,pad - QUATTRO word, cioe' una phrase per
 *          vertice. Il pad compra l'allineamento: la GPU scrive due long
 *          invece di tre word disallineate.
 *   wscr   il record di SCHERMO, due long per vertice:
 *            +0  (sx<<16) | (sy & $FFFF)   che e' anche ESATTAMENTE il long
 *                che il flusso dei lavori vuole a +0 del vertice: l'emissione
 *                lo copia, non lo ricompone
 *            +4  la profondita' x+y+z (la chiave del pittore)
 *   wlist  la lista da ordinare, due long per voce:
 *            +0  la chiave (minimo della profondita' sugli angoli)
 *            +4  (indice<<16) | scostamento - l'indice GLOBALE rompe i
 *                pareggi come faceva widx, lo scostamento indirizza dentro
 *                la propria sezione come faceva woff. L'ordinamento scambia
 *                due long alla stessa base invece di tre array. */
static WORD  *const wv   = (WORD *)BODY_WORK_BASE;         /* posa x,y,z,pad */
static LONG  *const wscr = (LONG *)((WORD *)BODY_WORK_BASE + MAXV * 4);
static WORD  *const wint = (WORD *)BODY_WORK_BASE + MAXV * 8;
static LONG  *const wlist = (LONG *)((WORD *)BODY_WORK_BASE + MAXV * 8 + MAXN);

/* ---------------------------------------------------------------------------
 * IL SEME DELL'ORDINAMENTO (S50). Vedi la nota lunga sul ciclo del sort.
 *
 * Otto posti, uno per i primi otto attori della lista. Non cento: le stanze del
 * riferimento ne disegnano da uno a tre, e un attore fuori dagli otto usa la
 * strada FREDDA - che e' quella di sempre, quindi degrada e non si rompe.
 *
 * `seed_body` e' la chiave di validita': lo stesso posto con un corpo diverso
 * non ha niente da dire. Non serve altro - vedi la nota: un seme stale produce
 * comunque una lista ordinata, solo piu' lentamente, quindi l'invalidazione e'
 * un'ottimizzazione, non una condizione di correttezza.
 * ------------------------------------------------------------------------- */
#define SORT_SEED_SLOTS 8
#define SORT_SEED_MAX   512     /* poligoni + linee + sfere di UN corpo */

/* SPENTO. S50 l'ha consegnato acceso e sul ferro Twinsen e' uscito a triangoli
 * e colori casuali. Il banco su PC diceva "uscita identica con e senza seme" -
 * e non dimostrava NIENTE: non era stato verificato che la strada calda venisse
 * presa, quindi i due binari potevano coincidere per costruzione
 * ([[feedback_sonda_intenzione_vs_copertura]]).
 *
 * Prima di riaccenderlo servono, in quest'ordine:
 *   1. un CONTATORE di quante volte `sort_seed` torna 1, stampato dal banco.
 *      Se e' zero, la prova di trasparenza non vale e va rifatta;
 *   2. la stessa prova col contatore diverso da zero;
 *   3. il sospettato numero uno: sul Jaguar `BODY_FRONT_ON_GPU` e' 1, quindi
 *      `wlist` la riempie gpu_front.s e non il C. La word ALTA di
 *      `wlist[k*2+1]` - l'indice globale su cui si costruisce il seme - va
 *      verificata sul BERSAGLIO, perche' il banco quella strada non la
 *      percorre. Il resto della pipeline la usa solo per rileggere il poligono;
 *      il seme la usa come CHIAVE fra un quadro e l'altro, che e' un uso nuovo.
 */
#ifndef BODY_SORT_SEED
#define BODY_SORT_SEED 0
#endif

static UWORD seed_ord[SORT_SEED_SLOTS][SORT_SEED_MAX];  /* l'ordine di ieri  */
static UWORD seed_n[SORT_SEED_SLOTS];
static WORD  seed_body[SORT_SEED_SLOTS];
static UWORD seed_pos[SORT_SEED_MAX];                   /* globale -> posto  */
static LONG  seed_tmp[SORT_SEED_MAX * 2];

/* Il posto di questo attore, o -1. */
static int sort_seed_slot(const T_OBJET *o)
{
    int n = (int)(o - ListObjet);

    return (n >= 0 && n < SORT_SEED_SLOTS) ? n : -1;
}

/* Rimette wlist nell'ordine del quadro prima. Torna 1 se ci e' riuscita, cioe'
 * se il ciclo puo' partire da gap 1.
 *
 * Le voci che c'erano vengono per prime, nell'ordine di ieri; quelle NUOVE - da
 * zero a cinque per quadro, misurate - in coda, nel loro ordine di indice, che
 * e' il caso peggiore per l'inserzione ed e' gia' dentro il 3,3x. */
static int sort_seed(const T_OBJET *o, UWORD nb_polys, UWORD nb_line,
                     UWORD nb_sph, int live)
{
    int slot = sort_seed_slot(o);
    UWORD gmax = (UWORD)(nb_polys + nb_line + nb_sph);
    int j, w = 0, k;

    /* A zero il motore torna a Shell sempre. Serve alla PROVA: la stessa
     * animazione con e senza seme deve dare un'uscita identica riga per riga,
     * ed e' l'unico modo di dimostrare che il seme e' trasparente invece di
     * prometterlo ([[feedback_buco_simmetrico_oracolo]]). */
    if (!BODY_SORT_SEED)
        return 0;
    if (slot < 0 || live < 2 || !seed_n[slot] || seed_body[slot] != o->Body)
        return 0;
    if (gmax > SORT_SEED_MAX || live > SORT_SEED_MAX)
        return 0;

    for (j = 0; j < (int)gmax; j++)
        seed_pos[j] = 0;
    for (k = 0; k < live; k++)
        seed_pos[(UWORD)((ULONG)wlist[k * 2 + 1] >> 16)] = (UWORD)(k + 1);

    for (j = 0; j < (int)seed_n[slot]; j++) {
        UWORD g = seed_ord[slot][j];
        UWORD p;

        if (g >= gmax)
            continue;                  /* il corpo di ieri era piu' grande */
        p = seed_pos[g];
        if (!p)
            continue;                  /* sparita dalla faccia nascosta    */
        seed_tmp[w * 2]     = wlist[(p - 1) * 2];
        seed_tmp[w * 2 + 1] = wlist[(p - 1) * 2 + 1];
        seed_pos[g] = 0;               /* consumata: non e' piu' "nuova"   */
        w++;
    }
    for (k = 0; k < live; k++) {
        UWORD g = (UWORD)((ULONG)wlist[k * 2 + 1] >> 16);

        if (g < gmax && seed_pos[g]) {
            seed_tmp[w * 2]     = wlist[k * 2];
            seed_tmp[w * 2 + 1] = wlist[k * 2 + 1];
            w++;
        }
    }
    if (w != live)
        return 0;                      /* qualcosa non torna: strada fredda */

    for (k = 0; k < live * 2; k++)
        wlist[k] = seed_tmp[k];
    return 1;
}

/* L'ordine appena prodotto diventa il seme del quadro dopo. */
static void sort_seed_keep(const T_OBJET *o, int live)
{
    int slot = sort_seed_slot(o);
    int k;

    if (slot < 0)
        return;
    if (live < 2 || live > SORT_SEED_MAX) {
        seed_n[slot] = 0;
        return;
    }
    for (k = 0; k < live; k++)
        seed_ord[slot][k] = (UWORD)((ULONG)wlist[k * 2 + 1] >> 16);
    seed_n[slot] = (UWORD)live;
    seed_body[slot] = o->Body;
}


/* Spacchettare un record di schermo: la word alta e' sx, la bassa sy, tutte e
 * due firmate. Il cast a WORD rifirma, come faceva la lettura di wsx/wsy. */
#define SCRX(i)  ((WORD)((ULONG)wscr[(i) * 2] >> 16))
#define SCRY(i)  ((WORD)wscr[(i) * 2])

/* M4: la posa dei gruppi.
 *
 * `wmat` e' TabMat (P_OB_ISO.ASM:1188): la matrice di OGNI gruppo, non solo di
 * quello in corso. Deve restare tutta perche' la matrice di un figlio si
 * calcola da quella del padre, e i figli di uno stesso padre non sono
 * consecutivi.
 *
 * `wpose` e' il pezzo che nel DOS sta DENTRO il corpo, ai byte +8..+15 di ogni
 * record da 38 (tipo e tre valori). Li' `SetInterAnimObjet2` ce li scrive; qui
 * il corpo e' un puntatore dentro la CARTUCCIA, e scriverci non e' una cosa che
 * si possa fare. Non e' un ripiego: nel DOS quei byte sono condivisi da tutti
 * gli attori che usano lo stesso corpo, e due guardie identiche si pestano la
 * posa a vicenda. Qui e' uno scratch dell'attore in corso.
 *
 * Nessuno dei due si legge prima di essere scritto: si riempiono per gruppo
 * nell'ordine topologico, che e' l'ordine in cui l'archivio li elenca. */
static LONG  *const wmat  = (LONG *)((WORD *)BODY_WORK_BASE
                                     + MAXV * 8 + MAXN + MAXP * 4);
static WORD  *const wpose = (WORD *)BODY_WORK_BASE
                            + MAXV * 8 + MAXN + MAXP * 4 + MAXG * 18;

/* Il descrittore di posa (S45): il contratto di gpu_front.s, una voce per
 * gruppo, quattro long - la forma sta in tools/memory_map.py. Lo scrive
 * pose_mats_desc, lo consuma vert_stage (il modello C) o la GPU. */
static LONG *const pose_desc = (LONG *)POSE_DESC_BASE;

typedef struct {
    const be_byte *verts;        /* nb_points  x 8: x, y, z, pad          */
    const be_byte *groups;       /* nb_groups  x 16                       */
    const be_byte *normals;      /* nb_normals x 8: x, y, z, rng - NATIVE */
    const be_byte *polys;        /* nb_polys   x 8: intestazioni fisse    */
    const be_byte *corners;      /* somma nbp  x 4: normale, vertice      */
    const be_byte *lines;        /* nb_lines   x 8                        */
    const be_byte *spheres;      /* nb_spheres x 8                        */
    UWORD nb_points, nb_groups, nb_normals, nb_polys;
    UWORD nb_lines, nb_spheres;
} T_BODY3D;


/* ---------------------------------------------------------------------------
 * Trigonometria di LBA1. 1024 = giro intero, tabella in Q14.
 *
 * `& (LBA_ANGLE_FULL - 1)` funziona anche sugli angoli negativi: in complemento
 * a due i bit bassi di un negativo sono gia' il resto positivo, che e' anche
 * quel che fa il `%` di Python nel riferimento.
 * ------------------------------------------------------------------------- */
static LONG sin_a(LONG a) { return P_SinTab[a & (LBA_ANGLE_FULL - 1)]; }
static LONG cos_a(LONG a)
{
    return P_SinTab[(a + LBA_ANGLE_QUARTER) & (LBA_ANGLE_FULL - 1)];
}

/* RotXY / Rotate, P_TRIGO.ASM:1265 e :1316. Ruota un punto sul piano XZ e
 * lascia il risultato in due globali, come l'originale - i chiamanti scrivono
 * `Rotate(a, b, t); a = X0; b = Y0;` e riprodurre quella forma evita di
 * ricontrollare ogni sito d'uso.
 *
 * Il verso: X0 prende `+z*sin`, Y0 prende `-x*sin`. Non e' la rotazione
 * "canonica" e non va normalizzata: e' quella con cui il gioco porta lo
 * spostamento di un'animazione dal sistema del personaggio a quello del mondo,
 * e invertirne il segno farebbe camminare Twinsen all'indietro. */
static LONG rot_X0, rot_Y0;

static void rotate_xz(LONG x, LONG z, LONG t)
{
    if (t) {
        LONG s = sin_a(t);
        LONG c = cos_a(t);

        rot_X0 = ((x * c) + (z * s)) >> LBA_TRIG_FIX;
        rot_Y0 = ((z * c) - (x * s)) >> LBA_TRIG_FIX;
    } else {
        rot_X0 = x;
        rot_Y0 = z;
    }
}

/* Matrice [riga*3 + colonna], come `Rot` la legge. */
typedef LONG T_MAT[9];

/* RotMatIndex2, P_TRIGO.ASM:833.
 *
 * Ogni blocco lascia intatta UNA colonna e ricombina le altre due: alpha
 * mescola le colonne 1 e 2 (rotazione attorno a X), gamma le colonne 0 e 1
 * (attorno a Z), beta le colonne 0 e 2 (attorno a Y). Un angolo a zero salta il
 * proprio blocco, ed e' il motivo per cui il gioco si porta dietro una matrice
 * `Dummy` invece di moltiplicare sempre.
 *
 * Il passo e' >>14 e non >>15: i commenti del gioco dicono 15, le istruzioni
 * dicono `sar edi, 14`, e vince il codice. Con >>15 la matrice non sarebbe
 * normalizzata e ogni corpo ruotato si rimpicciolirebbe.
 *
 * La moltiplicazione e' a DESTRA, quindi l'ordine effettivo e'
 * Rx(alpha) . Rz(gamma) . Ry(beta): beta si applica per ULTIMO pur essendo il
 * secondo argomento.
 *
 * LA SORGENTE E' UN ARGOMENTO (M4). Il DOS l'ha sempre avuta - EBP la matrice
 * di partenza, EAX quella di arrivo - e finora gliela passavamo sempre
 * identita', perche' l'unica matrice era quella dell'attore. Con i gruppi la
 * sorgente e' la matrice del PADRE, ed e' quel che rende la posa una catena.
 *
 * Il caso "tutti e tre gli angoli a zero" non e' degenere ed e' anzi quello che
 * gira di piu': il DOS COPIA la sorgente (P_TRIGO.ASM:981-987), cioe' un gruppo
 * fermo eredita la matrice del padre. Qui la copia iniziale lo da' gratis. */
static void rot_mat_index2(T_MAT m, const T_MAT src,
                           LONG alpha, LONG beta, LONG gamma)
{
    LONG s, c, a, b;
    int r;

    for (r = 0; r < 9; r++)
        m[r] = src[r];

    if (alpha) {
        s = sin_a(alpha); c = cos_a(alpha);
        for (r = 0; r < 3; r++) {
            a = m[r * 3 + 1]; b = m[r * 3 + 2];
            m[r * 3 + 1] = (c * a + s * b) >> LBA_TRIG_FIX;
            m[r * 3 + 2] = (c * b - s * a) >> LBA_TRIG_FIX;
        }
    }
    if (gamma) {
        s = sin_a(gamma); c = cos_a(gamma);
        for (r = 0; r < 3; r++) {
            a = m[r * 3 + 0]; b = m[r * 3 + 1];
            m[r * 3 + 0] = (c * a + s * b) >> LBA_TRIG_FIX;
            m[r * 3 + 1] = (c * b - s * a) >> LBA_TRIG_FIX;
        }
    }
    if (beta) {
        s = sin_a(beta); c = cos_a(beta);
        for (r = 0; r < 3; r++) {
            a = m[r * 3 + 0]; b = m[r * 3 + 2];
            m[r * 3 + 0] = (c * a - s * b) >> LBA_TRIG_FIX;
            m[r * 3 + 2] = (c * b + s * a) >> LBA_TRIG_FIX;
        }
    }
}

/* Rot, P_TRIGO.ASM:502. */
static void rot_vec(const T_MAT m, LONG x, LONG y, LONG z, LONG *o)
{
    o[0] = (m[0] * x + m[1] * y + m[2] * z) >> LBA_TRIG_FIX;
    o[1] = (m[3] * x + m[4] * y + m[5] * z) >> LBA_TRIG_FIX;
    o[2] = (m[6] * x + m[7] * y + m[8] * z) >> LBA_TRIG_FIX;
}

/* SetLightVector, P_TRIGO.ASM:466. La luce e' un DATO DI SCENA: 77 scene su
 * 120 usano (896, 950), che e' anche la coppia che GAMEMENU.C:751 scrive a
 * mano. Il vettore inventato in S14 - (-1,-1,-1)/sqrt(3) - e' a 118 gradi da
 * questo: i corpi erano illuminati dal lato sbagliato. */
static const T_MAT mat_identity = { LBA_TRIG_ONE, 0, 0,
                                    0, LBA_TRIG_ONE, 0,
                                    0, 0, LBA_TRIG_ONE };

static void light_vector(WORD alpha, WORD beta, LONG *o)
{
    T_MAT m;
    rot_mat_index2(m, mat_identity, alpha, beta, 0);
    rot_vec(m, 0, 0, LIGHT_LEN, o);
}


/* ---------------------------------------------------------------------------
 * Il corpo dov'e'. Nessuna copia: si tengono i puntatori dentro la voce, che
 * vive nell'arena per-cubo quanto la stanza. Dal S43 la voce e' il blob JBD:
 * niente da camminare, l'intestazione dichiara conteggi e offset di ogni
 * array e questo parse e' quattordici `move.w`, qualunque corpo sia.
 * ------------------------------------------------------------------------- */
static int body_parse(const UBYTE *p, T_BODY3D *b)
{
    const be_byte *q = (const be_byte *)p;

    if (!q)
        return 0;

    /* Un magic sbagliato non e' un corpo strano: e' un packer e un reader che
     * parlano due formati. Fermarsi col codice parlante, non disegnare rumore. */
    if (BE16u(q) != JBD_MAGIC)
        TheEnd(TE_BODY_BIG, "corpo non digerito (magic JBD)");

    b->nb_points  = BE16u(q + 2);
    b->nb_groups  = BE16u(q + 4);
    b->nb_normals = BE16u(q + 6);
    b->nb_polys   = BE16u(q + 8);
    b->nb_lines   = BE16u(q + 10);
    b->nb_spheres = BE16u(q + 12);
    b->verts   = q + BE16u(q + 14);
    b->groups  = q + BE16u(q + 16);
    b->normals = q + BE16u(q + 18);
    b->polys   = q + BE16u(q + 20);
    b->corners = q + BE16u(q + 22);
    b->lines   = q + BE16u(q + 24);
    b->spheres = q + BE16u(q + 26);

    /* Sforare BODY_WORK vorrebbe dire scrivere dentro ACTOR_SPRITES, cioe' un
     * guasto che si manifesta altrove. I tre numeri sono misurati su tutte e
     * 132 le voci di BODY.HQR (`python tools/body_ref.py --limits`), quindi
     * arrivare qui vuol dire che l'archivio non e' quello di prima. */
    if (b->nb_points > MAXV || b->nb_normals > MAXN || b->nb_polys > MAXP
        || b->nb_groups > MAXG)
        TheEnd(TE_BODY_BIG, "corpo oltre BODY_WORK");
    return 1;
}


/* ---------------------------------------------------------------------------
 * LA POSA: la gerarchia dei gruppi. AnimNuage, P_OB_ISO.ASM:1155-1225.
 *
 * Ogni gruppo ha una MATRICE - quella del padre composta con la propria
 * rotazione locale - e un vertice di ANCORAGGIO nel gruppo padre. I vertici del
 * gruppo escono ruotati da quella matrice e traslati su quell'ancora, che a sua
 * volta e' gia' ruotata e traslata. I gruppi sono in ordine topologico (il
 * padre ha sempre indice minore), quindi una passata sola basta.
 *
 * I VERTICI SORGENTE SONO SEMPRE QUELLI ORIGINALI, mai quelli gia' trasformati:
 * ad accumularsi lungo la catena sono le ancore. Il DOS lo rende evidente
 * tenendo due liste separate, `Def_Point` in sola lettura e `List_Anim_Point`.
 * Qui la sorgente e' la cartuccia e la destinazione e' `wv`, il che da' la
 * stessa separazione senza pagare una copia.
 *
 * COSA CAMBIA RISPETTO ALLA POSA A RIPOSO CHE C'ERA PRIMA (S13-S37)
 * ----------------------------------------------------------------
 * Prima questa funzione sommava solo le ancore, e la rotazione dell'attore si
 * applicava DOPO, una volta per vertice, dentro il ciclo di proiezione. Con
 * tutti gli angoli locali a zero le due cose coincidono: le matrici dei gruppi
 * sono tutte quella dell'attore, e ruotare e' lineare, quindi
 * `M(v + ancora) == Mv + M(ancora)`. Le 24 sonde di M3c non si muovono di un
 * bit - e' cosi' che questa riscrittura si collauda.
 *
 * DA S45 LA FUNZIONE E' SPEZZATA IN DUE, sul confine che separa i due
 * processori:
 *
 *   pose_mats_desc   il 68000: le MATRICI dei gruppi (al piu' 25, con la
 *                    tabella dei seni) e il DESCRITTORE di posa - una voce
 *                    per gruppo, la forma sta in tools/memory_map.py;
 *   vert_stage       il ciclo sui VERTICI: rotazione con la matrice del
 *                    gruppo, ancora, PROIEZIONE isometrica e rettangolo
 *                    dell'attore, tutto in una passata sola (la fusione 3c-iii
 *                    di todo_s39: prima anim_pose scriveva wv e il ciclo dopo
 *                    lo rileggeva). E' il MODELLO che gpu_front.s trascrive.
 *
 * IL TRONCAMENTO A WORD NON C'E' PIU', ED E' DICHIARATO. Prima wv si scriveva
 * (WORD)(r + ancora) e la proiezione rileggeva la word; ora il valore resta in
 * un long. Sui dati veri e' la stessa cosa - la posa di un corpo LBA1 sta in
 * poche migliaia di unita' - e il banco su PC lo PROVA con una sonda di limite
 * (FrontBound, |posa| <= 32767 su tutte le pose percorse): se un giorno un
 * corpo la sfora, cade il banco, non il Jaguar. Serve alla GPU, dove i
 * moltiplicatori guardano solo i 16 bit bassi.
 * ------------------------------------------------------------------------- */
static int pose_mats_desc(const T_BODY3D *b, WORD alpha, WORD beta, WORD gamma)
{
    UWORD g;
    LONG *d;

    /* Corpo senza gruppi: il ramo ComputeNuage del gioco, una matrice sola per
     * tutto e una voce sola di descrittore. */
    if (b->nb_groups == 0) {
        rot_mat_index2(wmat, mat_identity, alpha, beta, gamma);
        d = pose_desc;
        d[0] = (LONG)b->nb_points;               /* moff 0, tutti i vertici  */
        d[1] = 0xFFFFL;                          /* vstart 0, nessuna ancora */
        d[2] = 0;
        d[3] = 0;
        return 1;
    }

    for (g = 0; g < b->nb_groups; g++) {
        const be_byte *gp = b->groups + (long)g * JBD_GSTRIDE;
        UWORD start  = BE16u(gp + 0);        /* gia' indici, non offset */
        UWORD nbv    = BE16u(gp + 2);
        UWORD anchor = BE16u(gp + 4);
        LONG *m = wmat + (long)g * 9;
        const LONG *src;
        WORD typ, v1, v2, v3;
        WORD dx = 0, dy = 0, dz = 0;
        UWORD end;

        if (g == 0) {
            /* RotateGroupe con orggroupe == -1: la sorgente e' la matrice del
             * mondo (per la proiezione iso e' l'identita'), l'ancora e'
             * l'origine, e gli angoli sono quelli dell'ATTORE. Il record di
             * posa del gruppo 0 non si legge: l'animazione lo usa per la
             * rotazione MAESTRA, che entra da un'altra parte (il Beta
             * dell'attore, OBJECT.C:1718). */
            typ = TYPE_ROTATE; v1 = alpha; v2 = beta; v3 = gamma;
            src = mat_identity;
            anchor = 0xFFFF;
        } else {
            /* Nel file DOS il padre era un OFFSET in byte da dividere per 38
             * (upstream lo riscriveva addirittura con PatchObjet); nel blob
             * JBD e' gia' un indice, diviso una volta per tutte al packing. */
            UWORD parent = BE16u(gp + 6);

            if (parent >= g)
                TheEnd(TE_BODY_BIG, "gruppi non in ordine topologico");
            src = wmat + (long)parent * 9;

            typ = wpose[g * 4 + 0];
            v1  = wpose[g * 4 + 1];
            v2  = wpose[g * 4 + 2];
            v3  = wpose[g * 4 + 3];

            /* L'ancora fuori campo diventa "nessuna": e' la stessa regola di
             * prima (ax,ay,az restavano a zero), resa un dato invece di un
             * ramo dentro il ciclo caldo. */
            if (anchor >= b->nb_points)
                anchor = 0xFFFF;
        }

        if (typ == TYPE_ROTATE) {
            rot_mat_index2(m, src, v1, v2, v3);
        } else if (typ == TYPE_TRANSLATE) {
            /* TranslateGroupe: la matrice e' quella del padre COPIATA, e i tre
             * valori sono uno spostamento che si somma al vertice PRIMA di
             * ruotarlo (TransRotList, P_TRIGO.ASM:313-330). */
            int r;
            for (r = 0; r < 9; r++)
                m[r] = src[r];
            dx = v1; dy = v2; dz = v3;
        } else {
            /* ZoomGroupe sta dentro `ifdef Poubelle` e il DOS non lo chiama
             * nemmeno: cadrebbe in `nolocalrot`, lasciando i vertici del gruppo
             * a quel che c'era prima. Su tutte e 516 le animazioni
             * dell'archivio il tipo 2 non compare mai (43653 rotazioni, 4985
             * traslazioni). Fermarsi qui e' meglio che disegnare un pezzo di
             * attore preso dall'attore precedente. */
            TheEnd(TE_BODY_BIG, "tipo di trasformazione sconosciuto");
            return 0;
        }

        /* Il limite di capienza si applica QUI, una volta, invece che nel
         * ciclo dei vertici: il descrittore porta gia' il conteggio giusto. */
        end = start + nbv;
        if (end > b->nb_points)
            end = b->nb_points;
        nbv = (end > start) ? (UWORD)(end - start) : 0;

        d = pose_desc + (long)g * JAG_GPU_POSE_DESC_LONGS;
        d[0] = ((LONG)g * 9 << 16) | nbv;
        d[1] = ((LONG)start << 16) | anchor;
        d[2] = ((LONG)(UWORD)dx << 16) | (UWORD)dy;
        d[3] = ((LONG)(UWORD)dz << 16);
    }
    return (int)b->nb_groups;
}

#if !BODY_FRONT_ON_GPU
/* ---------------------------------------------------------------------------
 * I LIMITI SU CUI IL KERNEL FRONT-END SCOMMETTE - misurati, non argomentati.
 * Stessa idea di ShadeBound: `imultn`/`imacn` guardano solo i 16 bit bassi, e
 * il C moltiplica a 32. host_body.c confronta questi massimi con 32767: il
 * buco simmetrico dell'oracolo, tappato dall'unico lato che puo' vederlo.
 *   vin    |vertice + spostamento TRANSLATE|  (operando della matrice)
 *   pose   |componente ruotata + ancora|      (quel che prima si troncava)
 *   scr    |sx|,|sy| proiettati               (vanno impacchettati in 16 bit)
 * `cover` conta i vertici coperti dal descrittore: se un corpo lasciasse buchi
 * la vecchia pipeline proiettava roba vecchia e la nuova no - deve valere
 * nb_points a ogni attore, e lo controlla il banco. */
typedef struct {
    LONG vin;
    LONG pose;
    LONG scr;
    LONG cover_bad;    /* attori il cui descrittore non copre tutti i vertici */
} T_FRONTBOUND;
static T_FRONTBOUND FrontBound;

static void fbound_hit(LONG *slot, LONG v)
{
    if (v < 0)
        v = -v;
    if (v > *slot)
        *slot = v;
}
#endif

/* ---------------------------------------------------------------------------
 * LA FASE V: i vertici. Rotazione con la matrice del PROPRIO gruppo, ancora,
 * proiezione isometrica e rettangolo dell'attore in UNA passata (S45).
 *
 * Consuma SOLO il descrittore e gli argomenti che la GPU ricevera': e' il
 * contratto, e il banco su PC lo verifica byte per byte prima che esista una
 * riga di assembly. `bb` torna il rettangolo: minx, maxx, miny, maxy.
 * ------------------------------------------------------------------------- */
#if !BODY_FRONT_ON_GPU
static void vert_stage(int nd, const be_byte *verts, UWORD npts,
                       LONG posx, LONG posy, LONG posz,
                       LONG vx, LONG vy, WORD *bb)
{
    WORD bx0 = 0x7FFF, by0 = 0x7FFF, bx1 = -0x7FFF, by1 = -0x7FFF;
    int s;
    ULONG covered = 0;

    for (s = 0; s < nd; s++) {
        const LONG *d = pose_desc + (long)s * JAG_GPU_POSE_DESC_LONGS;
        UWORD moff   = (UWORD)((ULONG)d[0] >> 16);
        UWORD nbv    = (UWORD)((ULONG)d[0] & 0xFFFF);
        UWORD vstart = (UWORD)((ULONG)d[1] >> 16);
        UWORD anchor = (UWORD)((ULONG)d[1] & 0xFFFF);
        WORD  dx     = (WORD)((ULONG)d[2] >> 16);
        WORD  dy     = (WORD)((ULONG)d[2] & 0xFFFF);
        WORD  dz     = (WORD)((ULONG)d[3] >> 16);
        const LONG *m = wmat + moff;
        const be_byte *v = verts + (long)vstart * JBD_VSTRIDE;
        LONG ax = 0, ay = 0, az = 0;
        UWORD k;

        if (anchor != 0xFFFF) {
            ax = wv[anchor * 4 + 0];
            ay = wv[anchor * 4 + 1];
            az = wv[anchor * 4 + 2];
        }

        for (k = 0; k < nbv; k++) {
            UWORD i = (UWORD)(vstart + k);
            LONG x = BE16(v) + dx;
            LONG y = BE16(v + 2) + dy;
            LONG z = BE16(v + 4) + dz;
            LONG t0 = ((m[0] * x + m[1] * y + m[2] * z) >> LBA_TRIG_FIX) + ax;
            LONG t1 = ((m[3] * x + m[4] * y + m[5] * z) >> LBA_TRIG_FIX) + ay;
            LONG t2 = ((m[6] * x + m[7] * y + m[8] * z) >> LBA_TRIG_FIX) + az;
            LONG px, py, pz, sx, sy;

            fbound_hit(&FrontBound.vin, x);
            fbound_hit(&FrontBound.vin, y);
            fbound_hit(&FrontBound.vin, z);
            fbound_hit(&FrontBound.pose, t0);
            fbound_hit(&FrontBound.pose, t1);
            fbound_hit(&FrontBound.pose, t2);

            wv[i * 4 + 0] = (WORD)t0;
            wv[i * 4 + 1] = (WORD)t1;
            wv[i * 4 + 2] = (WORD)t2;

            px = t0 + posx;
            py = t1 + posy;
            pz = t2 + posz;
            sx = (((px - pz) * ISO_X) >> ISO_SHIFT) + vx;
            sy = ((((px + pz) * ISO_Y) - py * ISO_Y_WORLD) >> ISO_SHIFT) + vy;
            fbound_hit(&FrontBound.scr, sx);
            fbound_hit(&FrontBound.scr, sy);
            wscr[i * 2]     = ((LONG)(UWORD)(WORD)sx << 16) | (UWORD)(WORD)sy;
            wscr[i * 2 + 1] = px + py + pz;

            if ((WORD)sx < bx0) bx0 = (WORD)sx;
            if ((WORD)sx > bx1) bx1 = (WORD)sx;
            if ((WORD)sy < by0) by0 = (WORD)sy;
            if ((WORD)sy > by1) by1 = (WORD)sy;
            v += JBD_VSTRIDE;
            covered++;
        }
    }
    if (covered != npts)
        FrontBound.cover_bad++;

    bb[0] = bx0; bb[1] = bx1; bb[2] = by0; bb[3] = by1;
}

/* ---------------------------------------------------------------------------
 * LA FASE C: la faccia nascosta e la chiave del pittore, `testpoly`
 * (P_OB_ISO.ASM:507-527) piu' il minimo di X+Y+Z sugli angoli. Scrive le
 * coppie (chiave, indice) in wlist e torna quante sono. E' il modello della
 * fase C di gpu_front.s: legge le intestazioni JBD a passo fisso e i record di
 * schermo impacchettati, esattamente come fara' la GPU.
 * ------------------------------------------------------------------------- */
static LONG cull_stage(const be_byte *polys, UWORD npolys,
                       const be_byte *corners, UWORD npts)
{
    LONG live = 0;
    UWORD i;

    for (i = 0; i < npolys; i++) {
        const be_byte *h = polys + (ULONG)i * JBD_PSTRIDE;
        UWORD nbp = h[1];
        const be_byte *c = corners + (ULONG)BE16u(h + 6) * JBD_CSTRIDE;
        LONG p0, p1, p2, key;
        UWORD v0, v1, v2, k;

        if (nbp < 3)
            continue;

        v0 = BE16u(c + 2);
        v1 = BE16u(c + JBD_CSTRIDE + 2);
        v2 = BE16u(c + 2 * JBD_CSTRIDE + 2);
        if (v0 >= npts) v0 = 0;
        if (v1 >= npts) v1 = 0;
        if (v2 >= npts) v2 = 0;
        p0 = wscr[v0 * 2];
        p1 = wscr[v1 * 2];
        p2 = wscr[v2 * 2];

        /* Il prodotto vettoriale di poly_visible, sugli stessi numeri: le
         * word alte sono le x, le basse (rifirmate) le y. */
        {
            LONG x0 = p0 >> 16, y0 = (WORD)p0;
            LONG x1 = p1 >> 16, y1 = (WORD)p1;
            LONG x2 = p2 >> 16, y2 = (WORD)p2;

            if ((x0 - x2) * (y1 - y0) - (y0 - y2) * (x1 - x0) >= 0)
                continue;
        }

        /* Chiave del pittore: il MINIMO della profondita' sugli angoli. La
         * guardia `v < npts` SALTA l'angolo fuori campo invece di mandarlo al
         * vertice 0: e' la regola di prima, tale e quale. */
        key = 0x7FFFFFFFL;
        for (k = 0; k < nbp; k++) {
            UWORD v = BE16u(c + (ULONG)k * JBD_CSTRIDE + 2);
            if (v < npts && wscr[v * 2 + 1] < key)
                key = wscr[v * 2 + 1];
        }
        wlist[live * 2]     = key;
        wlist[live * 2 + 1] = ((LONG)i << 16) | i;
        live++;
    }
    return live;
}
#endif /* !BODY_FRONT_ON_GPU */

#if BODY_FRONT_ON_GPU
/* ---------------------------------------------------------------------------
 * LE DUE FASI SULLA GPU TOM (S45). Il codice e' src/gpu_front.s; qui ci sono
 * gli argomenti e la stretta di mano, come per shade_gpu. UN lancio per
 * attore copre vertici E faccia nascosta: la fase C parte quando la V ha
 * finito, dentro lo stesso kernel, quindi non c'e' una seconda stretta di
 * mano da pagare ne' da sbagliare.
 *
 * Torna `live` (quante coppie ha scritto in wlist), o -1 se il guardiano e'
 * scattato: il chiamante salta l'attore, esattamente come un lancio del
 * riempitore fallito vale zero pixel. `bb` torna il rettangolo dell'attore
 * che la fase V ha accumulato.
 * ------------------------------------------------------------------------- */
static LONG front_gpu(const T_BODY3D *b, int nd,
                      LONG posx, LONG posy, LONG posz,
                      LONG vx, LONG vy, WORD *bb)
{
    ULONG bbx, bby;

    /* Gia' fallito una volta: non si riprova (stessa regola del riempitore).*/
    if (BodyStat.fronthang) {
        BodyStat.fronthang++;
        return -1;
    }

    HW_L(JAG_GPU_FR_STAGE)   = 2;
    HW_L(JAG_GPU_FR_DESC)    = (unsigned long)POSE_DESC_BASE;
    HW_L(JAG_GPU_FR_ND)      = (unsigned long)nd;
    HW_L(JAG_GPU_FR_VERTS)   = (unsigned long)(const void *)b->verts;
    HW_L(JAG_GPU_FR_NPTS)    = (unsigned long)b->nb_points;
    HW_L(JAG_GPU_FR_MATS)    = (unsigned long)(void *)wmat;
    HW_L(JAG_GPU_FR_WV)      = (unsigned long)(void *)wv;
    HW_L(JAG_GPU_FR_WSCR)    = (unsigned long)(void *)wscr;
    HW_L(JAG_GPU_FR_POSX)    = (unsigned long)posx;
    HW_L(JAG_GPU_FR_POSY)    = (unsigned long)posy;
    HW_L(JAG_GPU_FR_POSZ)    = (unsigned long)posz;
    HW_L(JAG_GPU_FR_VX)      = (unsigned long)vx;
    HW_L(JAG_GPU_FR_VY)      = (unsigned long)vy;
    HW_L(JAG_GPU_FR_POLYS)   = (unsigned long)(const void *)b->polys;
    HW_L(JAG_GPU_FR_NPOLY)   = (unsigned long)b->nb_polys;
    HW_L(JAG_GPU_FR_CORNERS) = (unsigned long)(const void *)b->corners;
    HW_L(JAG_GPU_FR_WLIST)   = (unsigned long)(void *)wlist;
    HW_L(JAG_GPU_FR_LIVE)    = 0;

    if (!Gpu_Run(JAG_GPU_FRONT_ENTRY, JAG_GPU_FR_DONE,
                 GPU_RUN_GUARD_BASE
                 + GPU_RUN_GUARD_PER_VERT
                   * ((ULONG)b->nb_points + (ULONG)b->nb_polys))) {
        /* Quel che la GPU ha LETTO contro quel che il 68000 ha scritto: separa
         * "sbaglia i conti" da "non ha mai visto i dati giusti" (S33). */
        BodyStat.frontsaw = (HW_L(JAG_GPU_FR_SAWD) << 16)
                            | (HW_L(JAG_GPU_FR_SAWN) & 0xFFFFUL);
        BodyStat.fronthang++;
        return -1;
    }

    bbx = HW_L(JAG_GPU_FR_BBX);
    bby = HW_L(JAG_GPU_FR_BBY);
    bb[0] = (WORD)(bbx >> 16);
    bb[1] = (WORD)bbx;
    bb[2] = (WORD)(bby >> 16);
    bb[3] = (WORD)bby;
    return (LONG)HW_L(JAG_GPU_FR_LIVE);
}
#endif /* BODY_FRONT_ON_GPU */


/* ---------------------------------------------------------------------------
 * Un bordo del poligono, nella forma di `ComputePoly_A` (S_POLY.ASM:519-705).
 *
 * IL VERSO DI Y DECIDE IL LATO, NON L'ORDINE DELLE X
 * --------------------------------------------------
 * Il gioco tiene DUE tabelle, `TabVerticG` e `TabVerticD`, e un bordo va
 * nell'una o nell'altra a seconda che Y scenda o salga nell'ordine del
 * poligono (`jl y_monte`, S_POLY.ASM:556). Non ordina niente: la faccia
 * nascosta ha gia' garantito il verso di avvolgimento, quindi il bordo dove Y
 * scende E' quello di sinistra. Ed e' anche il motivo per cui a `xr < xl` il
 * gioco SALTA la riga (`sub cx,ax / jc nextlig`) invece di scambiare i due: se
 * sono incrociati, li' il poligono non ha spessore.
 *
 * IL DDA PARTE DALL'ESTREMO CON X ESTREMA
 * ---------------------------------------
 * Sinistra dall'estremo con X minore, destra da quello con X maggiore, e
 * l'altro estremo segue con la sua intensita' (`xchg bx,dx` + `xchg ax,cx`,
 * con le intensita' scambiate a ruota). Cosi' il delta X e' SEMPRE positivo e
 * la `div` SEMPRE senza segno: il verso decide il segno, come in
 * `GouraudInverse`. Sinistra somma il passo, destra lo sottrae (`sbb`).
 *
 * IL BIAS DI MEZZO PIXEL
 * ----------------------
 * La frazione iniziale e' `resto/2 + 32767` a sinistra e `32767 - resto/2` a
 * destra (`shr ax,1 / add ax,7FFFh`, con un `neg ax` in mezzo a destra). Vale
 * un arrotondamento al pixel piu' vicino invece del troncamento: senza, il
 * bordo cade un pixel piu' dentro sul 44% delle campate delle tre stanze del
 * riferimento.
 *
 * L'accumulo del gioco e' una catena di `adc` su un registro con la frazione
 * in alto e l'intero in basso, cioe' il riporto entra nell'intero con un giro
 * di ritardo. Il risultato e' esattamente `base + ((fx +- k*slope) >> 16)`: la
 * rotazione dei registri e' un modo di scriverlo, non una semantica in piu'.
 *
 *
 * LA FORMA E' QUELLA CHE LA GPU COSTRUISCE (S44)
 * ----------------------------------------------
 * I campi non sono `(base, verso, pendenza)` come li scrive il gioco, ma la
 * stessa cosa gia' riassociata su `j = y - ytop`:
 *
 *     x(y) = bx + ((A0 + j*m) >> 16)          m con segno
 *     i(y) =        I0 + j*mi                 mi con segno, 8.8
 *
 * Il gioco parte dall'estremo con X estrema e va su o giu'; qui la partenza e'
 * SEMPRE la riga piu' alta del bordo, e il verso finisce dentro il segno di
 * `m` e `mi`. E' algebra, non una regola diversa - lo dimostra il fatto che le
 * sonde non si muovono - e serve perche' la GPU TOM percorre le righe in un
 * verso solo e non puo' permettersi un ramo per bordo.
 *
 * Fino a S43 questa funzione girava sul 68000 e i record viaggiavano nel
 * flusso; da S44 nel flusso viaggiano i VERTICI e i bordi li costruisce la GPU
 * nella sua SRAM, record da 8 long nell'ordine qui sotto (right, ytop, ybot,
 * bx, a0, m, i0, mi). Questa e' la TRASCRIZIONE SORGENTE di quel pezzo di
 * src/gpu_body.s: scrive long per indice, non una struttura, perche' la GPU
 * scrive esattamente questi store nella sua tabella.
 *
 * LIMITI, VERIFICATI E NON SPERATI: `j <= dy` e `slope = (dxa<<16)/dy`, quindi
 * `|j*m| <= dxa<<16`, cioe' 41943040 su 640 colonne - ci sta in un long con
 * sei bit di margine. `|mi| <= 255<<8` sta in 16 bit, percio' sulla GPU il
 * prodotto dell'intensita' e' una `mult` sola mentre quello della x va spezzato
 * in lo16/hi16 (|m| arriva a 26 bit).
 * ------------------------------------------------------------------------- */
#if !BODY_FILL_ON_GPU
/* Indici del record di bordo, 8 long. */
#define EDGE_RIGHT 0           /* 1 = bordo di destra (Y sale nel poligono)  */
#define EDGE_YTOP  1           /* campo di righe coperto, estremi compresi   */
#define EDGE_YBOT  2
#define EDGE_BX    3           /* x dell'estremo da cui parte il DDA         */
#define EDGE_A0    4           /* frazione 16.16 alla riga ytop              */
#define EDGE_M     5           /* passo della frazione, con segno            */
#define EDGE_I0    6           /* intensita' 8.8 alla riga ytop              */
#define EDGE_MI    7           /* passo dell'intensita', con segno           */

static int build_edges(const WORD *xs, const WORD *ys, const WORD *sh, int n,
                       LONG *out)
{
    int e, ne = 0;

    for (e = 0; e < n; e++) {
        int f = (e + 1 == n) ? 0 : e + 1;
        WORD xa = xs[e], ya = ys[e], ia = sh[e];
        WORD xb = xs[f], yb = ys[f], ib = sh[f];
        LONG *g = out + ne * JAG_GPU_EDGE_LONGS;
        LONG dxa, di, dy, by, sy, slope, fx, istep, iacc0;
        LONG right, i1, i2, oy;

        if (ya == yb)
            continue;                 /* same_y: il gioco non stocca nulla */

        right = (yb < ya);
        dy = right ? (LONG)ya - yb : (LONG)yb - ya;
        if (right ? (xa >= xb) : (xa <= xb)) {
            g[EDGE_BX] = xa; by = ya; i1 = ia; i2 = ib; oy = yb;
        } else {
            g[EDGE_BX] = xb; by = yb; i1 = ib; i2 = ia; oy = ya;
        }
        sy = (oy > by) ? 1 : -1;

        /* IL RESTO NON SI DIVIDE DUE VOLTE (S41).
         *
         * Sul 68000 la divisione a 32 bit non esiste: `/` e `%` sono due
         * chiamate SEPARATE a libgcc (__divsi3 e __modsi3, viste nel
         * disassemblato), ~250-350 cicli l'una. Questo ciclo ne faceva QUATTRO
         * per bordo, e i bordi sono qualche centinaio per attore per quadro:
         * e' il grosso del 55% che il profilo attribuisce a BT_EMIT.
         *
         * Il resto pero' e' gia' in mano: `r = num - q*dy`. Si paga una
         * __mulsi3 (~60 cicli) al posto di una divisione intera. Due divisioni
         * per bordo invece di quattro, bit per bit lo stesso risultato -
         * `num >= 0` e `dy > 0` sempre, quindi il troncamento e' quello.
         *
         * E si passa a SENZA SEGNO: gli operandi sono non negativi per
         * costruzione (dxa e' un valore assoluto, dy e' positivo perche'
         * ya != yb), e __udivsi3 costa meno di __divsi3. */
        dxa = (xb > xa) ? (LONG)xb - xa : (LONG)xa - xb;
        {
            ULONG unum = (ULONG)dxa << 16;
            ULONG udy  = (ULONG)dy;
            ULONG uq   = unum / udy;
            ULONG ur   = unum - uq * udy;

            slope = (LONG)uq;
            fx = right ? 0x7FFFL - (LONG)(ur >> 1)
                       : 0x7FFFL + (LONG)(ur >> 1);
        }

        /* Intensita' di bordo, 8.8. `shr al,1` lavora sul solo byte basso del
         * resto e `add al,7Fh` non propaga il riporto: il mascheramento a 8
         * bit fa parte della regola, non e' una semplificazione nostra.
         *
         * QUI LA DIVISIONE CI STA IN SEDICI BIT, e il 68000 ce l'ha in
         * silicio: `sh` esce da shade_index, che maschera a 0..255, quindi
         * |di| <= 255 e il numeratore |di|<<8 <= 65280; `dy` e' al massimo
         * l'altezza del buffer. Numeratore, divisore e quoziente stanno tutti
         * in una word, cioe' e' una `divu.w` da ~140 cicli invece di una
         * chiamata. I tipi lo dicono al compilatore: e' l'unico modo che ha di
         * saperlo. */
        di = (LONG)i2 - i1;
        {
            UWORD inum = (UWORD)((((di >= 0) ? di : -di) << 8));
            UWORD idy  = (UWORD)dy;
            UWORD iq   = (UWORD)(inum / idy);
            UWORD ir   = (UWORD)(inum - (UWORD)(iq * idy));

            istep = (LONG)iq;
            iacc0 = ((LONG)i1 << 8)
                    + ((di >= 0) ? (LONG)((ir & 0xFF) >> 1) + 0x7F
                                 : (0x7F - (LONG)((ir & 0xFF) >> 1)) & 0xFF);
        }

        /* Riassociazione su j = y - ytop. Il verso di Y entra nel segno del
         * passo; il lato (sinistra somma, destra sottrae) pure. */
        if (di < 0)
            istep = -istep;
        if (right)
            slope = -slope;
        g[EDGE_RIGHT] = right;
        if (sy > 0) {
            g[EDGE_YTOP] = by;
            g[EDGE_A0] = fx;               g[EDGE_M]  = slope;
            g[EDGE_I0] = iacc0;            g[EDGE_MI] = istep;
        } else {
            g[EDGE_YTOP] = by - dy;
            g[EDGE_A0] = fx + slope * dy;    g[EDGE_M]  = -slope;
            g[EDGE_I0] = iacc0 + istep * dy; g[EDGE_MI] = -istep;
        }
        g[EDGE_YBOT] = g[EDGE_YTOP] + dy;
        ne++;
    }
    return ne;
}
#endif /* !BODY_FILL_ON_GPU */


#if !BODY_FILL_ON_GPU
/* ---------------------------------------------------------------------------
 * Una riga, `SVGAPolyGouraud` (S_FILLV.ASM:846).
 *
 * L'accumulatore parte dall'estremo di intensita' piu' bassa e somma un passo
 * non negativo (`GouraudInverse` riempie dall'altro capo), cosi' non esce mai
 * dall'intervallo delle intensita' degli angoli - l'invariante che in S32 ha
 * trovato i puntini bianchi che le somme di controllo non vedevano. La campata
 * e' `xr-xl+1` pixel, estremi COMPRESI (`inc cx / shr cx,1`).
 *
 * Quando la campata esce dallo schermo l'accumulatore avanza lo stesso sui
 * pixel scartati: tagliarla non deve sfasare il gradiente su quelli che
 * restano.
 * ------------------------------------------------------------------------- */
static ULONG fill_span(int y, LONG xl, LONG xr, LONG sl, LONG sr)
{
    UBYTE *row = body_fb_base + (LONG)y * JAG_BG_W;
    LONG dx = xr - xl;
    LONG step, acc, x, xe;
    ULONG drawn = 0;

    if (sr >= sl) {
        step = (dx > 0) ? (sr - sl) / dx : 0;
        acc = sl;
        x = xl;
        if (x < 0) {
            acc += step * (-x);
            x = 0;
        }
        xe = (xr < JAG_BG_W) ? xr : JAG_BG_W - 1;
        for (; x <= xe; x++) {
            row[x] = (UBYTE)((acc >> 8) & 0xFF);
            acc += step;
            drawn++;
        }
    } else {
        step = (dx > 0) ? (sl - sr) / dx : 0;
        acc = sr;
        x = xr;
        if (x > JAG_BG_W - 1) {
            acc += step * (x - (JAG_BG_W - 1));
            x = JAG_BG_W - 1;
        }
        xe = (xl > 0) ? xl : 0;
        for (; x >= xe; x--) {
            row[x] = (UBYTE)((acc >> 8) & 0xFF);
            acc += step;
            drawn++;
        }
    }
    return drawn;
}
#endif


/* ---------------------------------------------------------------------------
 * IL FLUSSO DEI LAVORI - un lancio per ATTORE, non per poligono.
 *
 * Fino a S33 ogni poligono era una stretta di mano a se': ~320 per stanza, cioe'
 * 320 occasioni perche' i due processori si pestino i piedi. S33 ne ha chiuse
 * QUATTRO, tutte viste sul ferro e nessuna visibile su BigPEmu; il guaio e' che
 * quattro difetti tappati non dicono niente sui non visti. La soluzione non e'
 * tapparne altri: e' non correre. Il C prepara il flusso completo dell'attore e
 * lancia la GPU UNA volta.
 *
 * Un lavoro e' l'intestazione seguita dai suoi VERTICI (S44; fino a S43 il
 * flusso portava i bordi gia' risolti, 8 long l'uno, costruiti dal 68000 -
 * erano i due terzi dei byte e quattro __udivsi3 per bordo, il 49% del tempo
 * corpi. Ora i bordi li costruisce chi consuma: la GPU, o jobs_run_c qui
 * sotto, che ne resta il modello):
 *
 *     [0] quanti vertici   0 = terminatore; 2 = tratta (una riga, colore
 *                          piatto); >=3 = poligono
 *     [1] ymin             gia' limitata allo schermo
 *     [2] ymax             compresa
 *     [3] la GPU ci scrive il TOTALE CORRENTE dei pixel a fine lavoro
 *     [4..] i vertici, JAG_GPU_VTX_LONGS long l'uno:
 *           +0  (x<<16) | (y & 0xFFFF)   due word firmate in un long
 *           +1  intensita' 0..255 nuda (la tinta, per le tratte)
 *
 * Il long [3] e' un totale corrente e non un totale del singolo lavoro perche'
 * cosi' la GPU non ha bisogno di un registro in piu' - e sulla GPU TOM i
 * registri sono tutti occupati. La differenza fra due totali consecutivi da'
 * i pixel del poligono, che e' quel che serve a BodyStat.polys.
 *
 * SVUOTAMENTO A PEZZI, E NON E' UN RIPIEGO. Nessuna taglia fissa puo' garantire
 * un lancio solo: un corpo puo' avere 384 poligoni da 16 angoli, cioe' 202 KB.
 * Il flusso si svuota quando e' pieno e riprende, quindi e' corretto per
 * qualunque attore; la taglia decide QUANTI lanci, non se il disegno e' giusto.
 * A 16 KB l'attore piu' grosso delle tre stanze (12704 byte, misurato) sta in
 * un lancio solo.
 * ------------------------------------------------------------------------- */
#define JOB_HDR   JAG_GPU_JOB_HDR_LONGS
#define JOB_VTX   JAG_GPU_VTX_LONGS
#define JOB_CAP   ((LONG)(JOB_STREAM_SIZE / 4))

/* VOLATILE, E NON PER ABITUDINE. Il long [3] di ogni intestazione lo scrive
 * l'ALTRO processore. Il C ci mette zero all'emissione e lo rilegge dopo il
 * lancio; fra le due cose c'e' solo Gpu_Run, che fa accessi volatili su
 * puntatori diversi da questo. Senza `volatile` il compilatore ha tutto il
 * diritto di inoltrare il proprio zero alla rilettura e di non guardare mai la
 * DRAM - e allora nessun poligono risulterebbe disegnato.
 *
 * Sul banco di prova non si vedrebbe MAI, perche' li' quel long lo scrive lo
 * stesso C che poi lo legge. E' la classe di difetti di S33 in versione
 * compilatore invece che hardware: visibile solo dove non si guarda. */
static volatile LONG *const job_buf = (volatile LONG *)JOB_STREAM_BASE;
static LONG job_used;           /* long gia' occupati                        */
static LONG job_count;          /* lavori nel flusso, terminatore escluso    */
/* Quante TRATTE di linea o di sfera ci sono fra questi lavori. Serve a non far
 * contare le entita' come poligoni: il conteggio dei poligoni disegnati si
 * ricava dai lavori che hanno prodotto pixel, e una tratta e' un lavoro. */
static LONG job_runs;
/* Byte di flusso accodati dalle entita' dell'attore corrente. Vanno dentro
 * `batchmax`, che e' il numero su cui si decide la taglia della regione: se le
 * tratte non ci finissero, la riga 29 direbbe che c'e' posto proprio mentre lo
 * si sta finendo. */
static ULONG run_bytes;

#if !BODY_FILL_ON_GPU
/* ---------------------------------------------------------------------------
 * Il MODELLO: quel che src/gpu_body.s trascrive, sullo STESSO flusso.
 *
 * Da S44 il flusso porta VERTICI: qui si spacchettano, si chiama build_edges
 * e il riempimento legge la tabella dei bordi per indice - la stessa tabella
 * da 8 long a bordo che la GPU costruisce nella sua SRAM. `el` sta in pila e
 * non nel flusso apposta: sul Jaguar quei record non toccano mai la DRAM.
 *
 * Una tratta (2 vertici) non passa da build_edges - due vertici sulla stessa
 * riga non producono nessun bordo (same_y) - ed e' il ramo che anche la GPU
 * prende: una campata sola, colore piatto, alla riga ymin.
 * ------------------------------------------------------------------------- */
/* UN lavoro: dalla sua intestazione all'ultimo pixel. Torna il puntatore al
 * lavoro successivo. E' il pezzo che S46 stacca da jobs_run_c per poterlo
 * chiamare anche dal walker del modo lista (list_run_c): stesso codice per i
 * due modi, cosi' il modello non puo' divergere da se' stesso. */
static volatile LONG *job_one_c(volatile LONG *p, ULONG *ptotal)
{
    LONG nv = p[0], ymin = p[1], ymax = p[2];
    const volatile LONG *vb = p + JOB_HDR;
    LONG el[JAG_GPU_EDGE_MAX * JAG_GPU_EDGE_LONGS];
    WORD xs[JAG_GPU_EDGE_MAX], ys[JAG_GPU_EDGE_MAX], sh[JAG_GPU_EDGE_MAX];
    ULONG total = *ptotal;
    LONG ne, y;
    int v;

    if (nv == 2) {
        LONG xl = vb[0] >> 16;
        LONG xr = vb[JOB_VTX] >> 16;
        LONG sl = vb[1] << 8;
        LONG sr = vb[JOB_VTX + 1] << 8;

        /* Le stesse guardie del ramo generale: per costruzione una tratta
         * accodata le passa sempre, ma la regola e' una sola. */
        if (!(xr < xl || xr < 0 || xl >= JAG_BG_W))
            total += fill_span((int)ymin, xl, xr, sl, sr);
        p[3] = (LONG)total;
        *ptotal = total;
        return p + JOB_HDR + 2 * JOB_VTX;
    }

    for (v = 0; v < nv; v++) {
        LONG w = vb[v * JOB_VTX];
        xs[v] = (WORD)(w >> 16);
        ys[v] = (WORD)w;
        sh[v] = (WORD)vb[v * JOB_VTX + 1];
    }
    ne = build_edges(xs, ys, sh, (int)nv, el);

    for (y = ymin; y <= ymax; y++) {
        LONG xl = 0, xr = 0, sl = 0, sr = 0;
        int have_l = 0, have_r = 0, e;

        for (e = 0; e < ne; e++) {
            const LONG *g = el + e * JAG_GPU_EDGE_LONGS;
            LONG j = y - g[1];              /* y - ytop                  */
            LONG x, s;

            if (j < 0 || y > g[2])          /* fuori dal campo del bordo */
                continue;
            x = g[3] + ((g[4] + j * g[5]) >> 16);
            s = g[6] + j * g[7];
            if (g[0]) {
                xr = x; sr = s; have_r = 1;
            } else {
                xl = x; sl = s; have_l = 1;
            }
        }
        /* `jc nextlig`: se manca un lato o sono incrociati la riga non si
         * disegna. Il gioco non scambia mai i due estremi. */
        if (!have_l || !have_r || xr < xl)
            continue;
        if (xr < 0 || xl >= JAG_BG_W)
            continue;
        total += fill_span((int)y, xl, xr, sl, sr);
    }
    p[3] = (LONG)total;
    *ptotal = total;
    return p + JOB_HDR + nv * JOB_VTX;
}

#if !BODY_EMIT_ON_GPU
static void jobs_run_c(void)
{
    volatile LONG *p = job_buf;
    ULONG total = 0;
    int i;

    for (i = 0; i < job_count; i++)
        p = job_one_c(p, &total);
}
#endif
#endif


/* Svuota il flusso: lo fa girare, poi conta. Torna senza contare se la GPU non
 * ha risposto - le intestazioni sono azzerate all'emissione, quindi un lancio
 * fallito vale zero pixel e zero poligoni, che e' esattamente quel che valeva
 * prima di S34 un poligono su cui il guardiano era scattato. */
#if !BODY_EMIT_ON_GPU
static void jobs_flush(void)
{
    volatile LONG *p;
    ULONG prev = 0;
    ULONG t0;
    LONG drew = 0;
    int i;

    if (!job_count)
        return;

    /* IL LANCIO E' LA PRIMA SCRITTURA SUL FRAMEBUFFER di questa passata, ed e'
     * qui che la cancellazione del disegno precedente deve scattare: tutto il
     * calcolo (posa, proiezione, intensita', ordine, bordi - il 95% del tempo)
     * e' avvenuto con l'attore ancora a schermo. Cancellare in testa alla
     * passata lasciava il buco per tutto quel tempo. Idempotente: dalla seconda
     * chiamata in poi non fa niente. */
    Bg_ClsPending();

    t0 = BODY_CLOCK;

    /* Il terminatore. Il posto c'e' sempre: jobs_emit svuota tenendone conto. */
    job_buf[job_used] = 0;

    BodyStat.launches++;

#if BODY_FILL_ON_GPU
    /* GIA' FALLITO UNA VOLTA: NON SI RIPROVA.
     *
     * Se la GPU non ha risposto una volta non rispondera' nemmeno alle altre, e
     * ogni tentativo costa un guardiano intero. La prima ha gia' registrato
     * tutto quel che serve; le altre si contano e basta, e quel conteggio dice
     * anche a che punto della stanza si e' rotto. */
    if (BodyStat.gpuhang) {
        BodyStat.gpuhang++;
        job_used = 0;
        job_count = 0;
        job_runs = 0;
        BodyStat.t_stage[BT_GPU] += BODY_CLOCK - t0;
        return;
    }

    HW_L(JAG_GPU_FB)     = (unsigned long)body_fb_base;
    HW_L(JAG_GPU_JOBS)   = (unsigned long)JOB_STREAM_BASE;
    HW_L(JAG_GPU_NJOB)   = (unsigned long)job_count;
    HW_L(JAG_GPU_PIXELS) = 0;
    /* Il kernel e' bimodale (S46) e il modo si sceglie QUI, a ogni lancio:
     * senza questo zero la build di bisezione erediterebbe l'ultimo modo
     * scritto - o il garbage del boot. */
    HW_L(JAG_GPU_EM_MODE) = 0;

    if (!Gpu_Run(JAG_GPU_BODY_ENTRY, JAG_GPU_DONE,
                 GPU_RUN_GUARD_BASE + GPU_RUN_GUARD_PER_JOB * (ULONG)job_count)) {
        /* Solo il PRIMO si registra: il secondo in poi e' quasi sempre una
         * conseguenza. */
        if (!BodyStat.gpuhang) {
            BodyStat.hangwhere = (BodyStat.polys << 8) | (ULONG)job_count;
            BodyStat.hangwhat  = (ULONG)job_used;
            /* Quel che la GPU ha LETTO, riscritto da lei prima di qualsiasi
             * ciclo. Se non combacia con quel che il 68000 ha scritto, non e'
             * la GPU a sbagliare i conti: non ha mai visto i dati giusti.
             * E' il paio di long che in S33 ha trovato tutti e quattro i
             * difetti della stretta di mano. */
            BodyStat.hangsaw   = HW_L(JAG_GPU_SAWJ);
            BodyStat.hangsawc  = HW_L(JAG_GPU_SAWC);
        }
        BodyStat.gpuhang++;
        job_used = 0;
        job_count = 0;
        job_runs = 0;
        BodyStat.t_stage[BT_GPU] += BODY_CLOCK - t0;
        return;
    }
#else
    jobs_run_c();
#endif

    /* Il conteggio: un lavoro conta se il totale corrente e' cresciuto.
     *
     * Da S37 non tutti i lavori sono poligoni: le tratte di linee e sfere sono
     * lavori a tutti gli effetti, e una linea ripida ne fa uno per riga. Si
     * sottraggono, perche' `polys` deve restare "poligoni disegnati" - se
     * contasse anche le tratte, la sonda DRAWN direbbe un numero che non e' ne'
     * i poligoni ne' le entita'. Ogni tratta accodata disegna per costruzione
     * almeno un pixel (gli estremi sono gia' dentro la finestra), quindi la
     * sottrazione e' esatta e non una stima. */
    p = job_buf;
    for (i = 0; i < job_count; i++) {
        ULONG cur = (ULONG)p[3];
        if (cur != prev)
            drew++;
        prev = cur;
        p += JOB_HDR + p[0] * JOB_VTX;
    }
    if (drew > job_runs)
        BodyStat.polys += (ULONG)(drew - job_runs);
    BodyStat.pixels += prev;

    job_used = 0;
    job_count = 0;
    job_runs = 0;
    BodyStat.t_stage[BT_GPU] += BODY_CLOCK - t0;
}


#endif /* !BODY_EMIT_ON_GPU */


/* Accoda un lavoro - i suoi VERTICI, da S44 - svuotando prima se non ci sta
 * (modo flusso; nel modo lista S46 una tratta che non ci sta si scarta).
 * Il posto per il terminatore e' sempre riservato. */
static void jobs_emit(const WORD *xs, const WORD *ys, const WORD *sh, int n,
                      int ymin, int ymax)
{
    volatile LONG *p;
    int i;

    if (n <= 0)
        return;
    /* Non puo' scattare: `n` e' gia' limitato a JAG_BODY_MAX_CORNERS in
     * draw_actor, e memory_map.py dimensiona il flusso su QUELLA costante. Se
     * scattasse vorrebbe dire che i due numeri hanno smesso di essere lo
     * stesso, ed e' meglio disegnare un poligono monco che scrivere oltre. */
    if (n > JAG_GPU_EDGE_MAX)
        n = JAG_GPU_EDGE_MAX;

#if BODY_EMIT_ON_GPU
    /* Nel modo lista il flusso porta BLOCCHI in ordine di wlist e si consuma
     * in UN lancio a valle: svuotarlo a meta' blocco spezzerebbe la
     * corrispondenza voce-blocco. Una tratta che non ci sta si SCARTA e si
     * conta: il massimo misurato e' ~3 KB contro i 32 del flusso, quindi
     * `rundrop` che sale e' un dato nuovo, non un'evenienza da gestire.
     *
     * La riserva e' MAXP long e non uno: il TERMINATORE di un blocco si
     * scrive sempre - il walker ne consuma uno per voce entita', e uno
     * mancante gli farebbe consumare il blocco del vicino - e le entita' di
     * un corpo sono al piu' MAXP (la guardia TE_BODY_BIG sopra). Riservare
     * il caso peggiore rende la scrittura del terminatore incondizionata. */
    if (job_used + JOB_HDR + n * JOB_VTX + 1 + MAXP > JOB_CAP) {
        BodyStat.rundrop++;
        return;
    }
#else
    if (job_used + JOB_HDR + n * JOB_VTX + 1 > JOB_CAP)
        jobs_flush();
#endif

    p = job_buf + job_used;
    p[0] = n;
    p[1] = ymin;
    p[2] = ymax;
    p[3] = 0;                   /* la GPU ci scrive; zero = non ha disegnato */
    p += JOB_HDR;
    for (i = 0; i < n; i++) {
        p[0] = ((LONG)(UWORD)xs[i] << 16) | (UWORD)ys[i];
        p[1] = sh[i];
        p += JOB_VTX;
    }
    job_used += JOB_HDR + n * JOB_VTX;
    job_count++;
    BodyStat.jobs++;
}


/* ---------------------------------------------------------------------------
 * Riempimento a scansione: `ComputePoly_A` + `FillVertic_A`.
 *
 * Il campo di righe e' `Ymin..Ymax` INCLUSIVO - `sub edx,eax / inc edx` in
 * `FillVertic_A` (S_FILLV.ASM:120). Contava: la regola semiaperta usata fino a
 * S32 lasciava fuori l'ultima riga di OGNI poligono, cioe' 669 righe sulle tre
 * stanze del riferimento.
 *
 * QUEL CHE RESTA FUORI, DICHIARATO: il gioco RITAGLIA il poligono prima
 * (ClipGauche/Droit/Haut/Bas), quindi il suo campo di righe e' gia' dentro lo
 * schermo. Qui il ritaglio non c'e': si limitano le righe e le colonne
 * scritte, il che non sposta il gradiente (l'accumulatore avanza lo stesso,
 * come nel gioco) ma non ricostruisce i vertici tagliati. Sulle tre stanze del
 * riferimento nessun poligono esce dallo schermo, quindi qui la differenza non
 * e' misurabile - il che vuol dire che le sonde NON la coprono.
 * CONDIZIONE DI RIMOZIONE: il ritaglio Sutherland-Hodgman e' gia' scritto e
 * validato in src/gpu_raster.s (GpuClipMain, 4 passate, S10); si accende
 * quando un attore arriva davvero a bordo schermo.
 * ------------------------------------------------------------------------- */
/* Da S34 questa non disegna: ACCODA. Da S44 non risolve nemmeno i bordi: il
 * campo di righe arriva gia' calcolato dal chiamante (draw_actor lo misura
 * comunque per il rettangolo dell'attore) e nel flusso finiscono i vertici
 * nudi. Le quattro divisioni per bordo che stavano qui - il 49% del tempo
 * corpi, profilo S43 - le fa il divisore hardware della GPU. Torna i byte
 * accodati, che servono solo a misurare quanto e' grande il flusso di un
 * attore.
 *
 * Un poligono con tutte le Y uguali entra nel flusso e non disegna niente:
 * zero bordi attivi su ogni riga, esattamente come il `ne == 0` che fino a
 * S43 lo scartava qui. Pagare n confronti su OGNI poligono per risparmiare
 * l'emissione di un caso raro sarebbe il conto al rovescio. */
#if !BODY_EMIT_ON_GPU
static ULONG emit_poly(const WORD *xs, const WORD *ys, const WORD *sh, int n,
                       int ymin, int ymax)
{
    ULONG te0;

    if (n < 3)
        return 0;
    if (ymin < 0) ymin = 0;
    if (ymax > JAG_BG_H - 1) ymax = JAG_BG_H - 1;

    /* Il lancio della GPU si SOTTRAE, come si fa gia' per BT_EMIT: se il flusso
     * si riempie a meta' ciclo, jobs_emit lancia da dentro e quel tempo e' gia'
     * di BT_GPU. Contarlo due volte farebbe sembrare cara la copia in DRAM,
     * cioe' manderebbe la prossima ottimizzazione sul pezzo sbagliato. */
    te0 = BODY_CLOCK;
    {
        ULONG g0 = BodyStat.t_stage[BT_GPU];

        jobs_emit(xs, ys, sh, n, ymin, ymax);
        BodyStat.t_stream += (BODY_CLOCK - te0)
                             - (BodyStat.t_stage[BT_GPU] - g0);
    }
    return (ULONG)(JOB_HDR + n * JOB_VTX) * 4;
}
#endif /* !BODY_EMIT_ON_GPU */


/* ---------------------------------------------------------------------------
 * LINEE E SFERE: LE TRATTE ORIZZONTALI, E PERCHE' NON SERVE UN SECONDO
 * RIEMPITORE SULLA GPU.
 *
 * `ComputeSphere_A` non e' un riempitore: riempie TabVerticG/TabVerticD - le
 * stesse due tabelle che `ComputePoly_A` riempie per un poligono - e poi chiama
 * `FillVertic_A`, la STESSA dei poligoni. Le linee sono lo stesso discorso al
 * contrario: `Line_A` scrive per ogni riga pixel CONTIGUI (il ramo orizzontale
 * avanza X a ogni passo e cambia riga solo al riporto, quello verticale scrive
 * un pixel per riga), quindi anche una linea E' un elenco di tratte.
 *
 * Il flusso dei lavori sa gia' disegnare una tratta: un lavoro di UNA riga con
 * due bordi verticali e pendenza zero. Con l'intensita' uguale ai due capi il
 * gradiente si annulla da se' e il colore resta costante, che e' esattamente il
 * secchio piatto con cui il gioco riempie linee e sfere. Percio' src/gpu_body.s
 * NON si tocca: le entita' passano dal riempitore che c'e' gia', nell'ORDINE
 * del pittore, perche' i lavori si eseguono nell'ordine in cui sono accodati.
 *
 * IL PREZZO, MISURATO E NON STIMATO: 8 long di lavoro per una riga sola (erano
 * 20 quando il flusso portava i bordi risolti, fino a S43). Sulle tre stanze
 * del riferimento sono 378 righe in tutto, cioe' ~2,8 KB in piu' per l'eroe.
 * Il flusso e' 16 KB: con il formato a vertici anche l'eroe con le tratte
 * resta comodamente in un lancio. E' un costo che la riga 29 della pagina 2
 * (byte del piu' grosso attore) fa vedere da se'.
 * ------------------------------------------------------------------------- */
/* La finestra di ritaglio e' quella di UnSetClip (INITSVGA.ASM:266): tutto lo
 * schermo, 0..Screen_X-1 e 0..Screen_Y-1. Il nostro schermo e' il BUFFER,
 * quindi 592 righe e non le 480 del DOS - la stessa scelta gia' fatta per il
 * riempimento dei poligoni e per il taglio delle tessere. Non e' la finestra
 * del preclip, che decide se un attore esiste ed e' quella del gioco: questa
 * decide dove si puo' scrivere, e si puo' scrivere dove c'e' buffer. */
#define CLIP_X0 0
#define CLIP_Y0 0
#define CLIP_X1 (JAG_BG_W - 1)
#define CLIP_Y1 (JAG_BG_H - 1)

/* Quante tratte sono state accodate dall'ultimo svuotamento. Serve a NON far
 * contare le entita' come poligoni: il conteggio dei poligoni disegnati si
 * ricava dai lavori che hanno prodotto pixel, e una tratta e' un lavoro. */
/* Una tratta: un lavoro di UNA riga a colore piatto, cioe' DUE vertici sulla
 * stessa Y - il conteggio 2 e' il marcatore che il consumatore riconosce,
 * perche' un poligono ne ha almeno tre e due vertici alla stessa riga non
 * producono nessun bordo (same_y). Il colore viaggia nell'intensita' dei due
 * vertici, nudo come quello dei poligoni piatti. */
static void emit_run(LONG y, LONG xl, LONG xr, UBYTE colour)
{
    WORD vx[2], vy[2], vs[2];

    if (xr < xl)
        return;
    vx[0] = (WORD)xl;  vx[1] = (WORD)xr;
    vy[0] = (WORD)y;   vy[1] = (WORD)y;
    vs[0] = colour;    vs[1] = colour;

    jobs_emit(vx, vy, vs, 2, (int)y, (int)y);
    job_runs++;
    run_bytes += (ULONG)(JOB_HDR + 2 * JOB_VTX) * 4;
    BodyStat.entpix += (ULONG)(xr - xl + 1);
}


/* `idiv`: la divisione del 386 TRONCA VERSO LO ZERO. In C la divisione intera
 * fa gia' cosi' da C99, ma il ritaglio di Line_A divide quasi sempre un
 * numeratore negativo e il riferimento in Python ha dovuto scriverselo a mano:
 * questa funzione esiste perche' i due file dicano la stessa cosa nello stesso
 * posto, non perche' il C ne abbia bisogno. */
static LONG idiv_z(LONG a, LONG b)
{
    return a / b;
}


/* `Line_A` (S_LINE.ASM:46): ritaglio di Cohen-Sutherland, poi Bresenham.
 *
 * Il ritaglio SPOSTA gli estremi sul bordo con una divisione intera e da li'
 * ricomincia il test (`jmp onceagain`): non e' un limite applicato ai pixel a
 * valle, cambia proprio quali pixel escono. Va dov'e'. */
static void draw_line_ent(WORD x0, WORD y0, WORD x1, WORD y1, UBYTE colour)
{
    LONG ax = x0, ay = y0, bx = x1, by = y1;
    LONG dx, dy, err, sy, runx0;
    int guard;

    /* `cmp eax,ecx / jle sensok`: si percorre sempre da X minore. */
    if (ax > bx) {
        LONG t;
        t = ax; ax = bx; bx = t;
        t = ay; ay = by; by = t;
    }

    for (guard = 0; ; guard++) {
        LONG code = 0, hi;

        if (guard > 8)
            return;
        if (ax < CLIP_X0)
            code |= 1;
        else if (ax > CLIP_X1)
            return;                         /* notseen */
        if (ay < CLIP_Y0)
            code |= 8;
        else if (ay > CLIP_Y1)
            code |= 4;
        code <<= 8;
        if (bx < CLIP_X0)
            return;                         /* notseen */
        if (bx > CLIP_X1)
            code |= 2;
        if (by < CLIP_Y0)
            code |= 8;
        else if (by > CLIP_Y1)
            code |= 4;

        /* `test si,bp`: i due estremi fuori dallo STESSO lato = invisibile. */
        hi = code >> 8;
        if (hi & code)
            return;
        if (((hi | code) & 0xFF) == 0)
            break;                          /* `jz draw` */

        dx = bx - ax;
        dy = by - ay;
        if (code & 0x100) {
            ay += idiv_z((CLIP_X0 - ax) * dy, dx);
            ax = CLIP_X0;
        } else if (code & 0x800) {
            ax += idiv_z((CLIP_Y0 - ay) * dx, dy);
            ay = CLIP_Y0;
        } else if (code & 0x400) {
            ax += idiv_z((CLIP_Y1 - ay) * dx, dy);
            ay = CLIP_Y1;
        } else if (code & 0x002) {
            by = ay + idiv_z((CLIP_X1 - ax) * dy, dx);
            bx = CLIP_X1;
        } else if (code & 0x008) {
            bx = ax + idiv_z((CLIP_Y0 - ay) * dx, dy);
            by = CLIP_Y0;
        } else if (code & 0x004) {
            bx = ax + idiv_z((CLIP_Y1 - ay) * dx, dy);
            by = CLIP_Y1;
        } else {
            return;
        }
    }

    /* `draw`: un pixel per passo, la riga cambia al riporto. */
    dx = bx - ax;
    dy = by - ay;
    sy = 1;
    if (dy < 0) {
        sy = -1;
        dy = -dy;
    }

    if (dx >= dy) {                         /* `horizontal` */
        LONG i;
        err = dx;
        runx0 = ax;
        for (i = 0; i <= dx; i++) {
            err -= 2 * dy;
            if (err < 0) {
                err += 2 * dx;
                emit_run(ay, runx0, ax, colour);
                ay += sy;
                runx0 = ax + 1;
            }
            ax++;
        }
        if (runx0 <= ax - 1)
            emit_run(ay, runx0, ax - 1, colour);
    } else {                                /* `vertical` */
        LONG i;
        err = dy;
        for (i = 0; i <= dy; i++) {
            emit_run(ay, ax, ax, colour);
            err -= 2 * dx;
            if (err < 0) {
                err += 2 * dy;
                ax++;                       /* `adc edi,esi`: riga E colonna */
            }
            ay += sy;
        }
    }
}


/* `ComputeSphere_A` (S_POLY.ASM:112): il cerchio di Bresenham che riempie le
 * due tabelle di tratte.
 *
 * L'accumulatore parte a -rayon e il passo e' `sum += y`, con il RIPORTO
 * dell'add senza segno al posto del test di segno: quando la somma torna
 * positiva la X cala di uno e la somma si riporta indietro di quella X. Ai
 * raggi di 2-4 pixel che hanno le mani di Twinsen ne esce un pallino quadrato,
 * ed e' IL pallino che disegna il DOS.
 *
 * Le tratte si accodano MAN MANO invece di passare da una tabella per righe:
 * l'ordine dentro una sfera non conta (le righe non si sovrappongono) e una
 * tabella di 592 voci per un pallino da cinque righe sarebbe memoria e tempo
 * spesi per riprodurre un dettaglio di come il DOS parla col suo riempitore,
 * non un dettaglio di quel che disegna.
 *
 * IL RAMO DI RITAGLIO (riga 214) e' trascritto ma NON ESERCITATO: nelle tre
 * stanze del riferimento nessuna sfera tocca il bordo. Dove il DOS riempirebbe
 * righe mai scritte dentro Ymin..Ymax leggendo memoria vecchia, qui la riga si
 * salta: quella memoria non e' riproducibile e non e' una regola. */
static void draw_sphere_ent(WORD cx, WORD cy, WORD rs, UBYTE colour)
{
    LONG ymin, ymax, px, py, s;
    int clip = 0;

    if (rs < 0)
        return;

    ymin = (LONG)cy - rs;
    if (ymin > CLIP_Y1)
        return;
    if (ymin < CLIP_Y0) {
        clip++;
        ymin = CLIP_Y0;
    }
    ymax = (LONG)cy + rs;
    if (ymax < CLIP_Y0)
        return;
    if (ymax > CLIP_Y1) {
        clip++;
        ymax = CLIP_Y1;
    }
    if ((LONG)cx + rs < CLIP_X0)
        return;
    if ((LONG)cx + rs > CLIP_X1)
        clip++;
    if ((LONG)cx - rs > CLIP_X1)
        return;
    if ((LONG)cx - rs < CLIP_X0)
        clip++;

    px = rs;
    py = 0;
    s  = -(LONG)rs;

    /* LE RIGHE SCRITTE DUE VOLTE, e perche' qui vanno SALTATE.
     *
     * Il DOS scrive in una tabella indicizzata per riga: riscrivere la stessa
     * riga con gli stessi valori non costa niente. Qui una riga e' un lavoro
     * nel flusso, quindi scriverla due volte la disegnerebbe due volte e ne
     * conterebbe i pixel due volte. Le coincidenze sono tre, tutte esatte:
     *
     *   py == 0    le due righe cy+py e cy-py sono la stessa;
     *   px == 0    idem per la coppia del riporto (succede a raggio zero);
     *   px == py   la coppia del riporto E' la coppia appena scritta, con la
     *              stessa larghezza - e capita a ogni raggio piccolo (r=2).
     *
     * In tutti e tre i casi i valori coincidono, quindi saltare la seconda
     * scrittura da' esattamente la tabella del DOS. */
    while (py <= px) {
        LONG rows[4], xg[4], xd[4];
        int up[4];
        int k, n = 0;

        rows[n] = (LONG)cy + py;
        xg[n] = (LONG)cx - px; xd[n] = (LONG)cx + px; up[n] = 0; n++;
        if (py != 0) {
            rows[n] = (LONG)cy - py;
            xg[n] = (LONG)cx - px; xd[n] = (LONG)cx + px; up[n] = 1; n++;
        }
        s += py;
        if (s >= 0) {
            if (px != py) {
                rows[n] = (LONG)cy + px;
                xg[n] = (LONG)cx - py; xd[n] = (LONG)cx + py; up[n] = 0; n++;
                if (px != 0) {
                    rows[n] = (LONG)cy - px;
                    xg[n] = (LONG)cx - py; xd[n] = (LONG)cx + py; up[n] = 1;
                    n++;
                }
            }
            px--;
            s -= px;
        }

        for (k = 0; k < n; k++) {
            LONG g = xg[k], d = xd[k];

            if (!clip) {
                emit_run(rows[k], g, d, colour);
                continue;
            }
            /* Il ramo CON ritaglio: una tratta tutta fuori da un lato non si
             * disegna e STRINGE Ymin o Ymax, che e' quel che il DOS fa
             * scrivendo [Ymin]/[Ymax] in mezzo al ciclo. Le righe SOPRA il
             * centro stringono Ymin, quelle sotto Ymax. */
            if (rows[k] > ymax || rows[k] < ymin)
                continue;
            if (g > CLIP_X1 || d < CLIP_X0) {
                if (up[k])
                    ymin = rows[k] + 1;
                else
                    ymax = rows[k] - 1;
                continue;
            }
            if (g < CLIP_X0)
                g = CLIP_X0;
            if (d > CLIP_X1)
                d = CLIP_X1;
            emit_run(rows[k], g, d, colour);
        }
        py++;
    }
}


/* La faccia nascosta (`testpoly`, P_OB_ISO.ASM:507-527) sta dentro
 * cull_stage / gpu_front.s: un prodotto vettoriale a DUE dimensioni sui primi
 * tre angoli GIA' PROIETTATI, e si tiene solo il risultato NEGATIVO. */

/* Un poligono del blob JBD: intestazione a passo fisso, indicizzabile per
 * numero, e angoli tutti della stessa forma (normale, vertice) qualunque sia
 * il materiale. La decodifica doppia (chiave e disegno) resta - e' piu'
 * economica di una lista di angoli in memoria - ma adesso "decodificare" e'
 * cinque `move.w`, non un ramo per materiale su un flusso da camminare. */
typedef struct {
    UBYTE mat;
    UBYTE nbp;
    UBYTE coul1;
    UWORD flat_normal;
    const be_byte *corners;      /* nel vettore degli angoli, 4 byte l'uno */
} T_POLY;

#if !BODY_EMIT_ON_GPU
static void poly_at(const T_BODY3D *b, UWORD i, T_POLY *o)
{
    const be_byte *h = b->polys + (ULONG)i * JBD_PSTRIDE;

    o->mat   = h[0];
    o->nbp   = h[1];
    o->coul1 = h[2];
    o->flat_normal = BE16u(h + 4);
    o->corners = b->corners + (ULONG)BE16u(h + 6) * JBD_CSTRIDE;
}

/* Indice del vertice dell'angolo k: il packer l'ha gia' diviso per 6. */
static UWORD corner_vert(const T_POLY *p, int k)
{
    return BE16u(p->corners + k * JBD_CSTRIDE + 2);
}

static UWORD corner_normal(const T_POLY *p, int k)
{
    return BE16u(p->corners + k * JBD_CSTRIDE);
}
#endif /* !BODY_EMIT_ON_GPU */


/* ---------------------------------------------------------------------------
 * L'indice di CLUT di un angolo.
 *
 * Non e' piu' la ricostruzione di S14 (rampa dal nibble alto + riscalatura
 * *15/59 + bloccaggio a 15): e' una somma, e basta.
 *
 *     gouraud   P_OB_ISO.ASM:492   add  dl, ch                (ch = coul1)
 *     flat      P_OB_ISO.ASM:375   add  bp, [List_Normal+..]
 *
 * Perche' 0..15 ci sta senza doverlo imporre: l'intensita' e'
 * dot(normale ruotata, luce) / range_prenormalizzato, e il range che ogni
 * normale si porta dietro e' tarato perche' il quoziente stia in una rampa.
 * La vecchia riscalatura schiacciava 0..14 in 0..3, cioe' i corpi erano quasi
 * piatti. Il troncamento a un byte e' l'unico limite che il gioco applica.
 * ------------------------------------------------------------------------- */
/* Sul bersaglio col modo lista la tinta la somma la GPU (fase E): questa
 * resta per il ciclo legacy e per il modello del banco. */
#if !BODY_EMIT_ON_GPU || !BODY_FILL_ON_GPU
static WORD shade_index(UBYTE coul1, WORD intensity)
{
    return (WORD)((coul1 + intensity) & 0xFF);
}
#endif


/* ---------------------------------------------------------------------------
 * UNA ENTITA' della lista ordinata: una linea o una sfera, rasterizzata in
 * tratte. E' il ramo che fino a S45 viveva dentro il ciclo di disegno di
 * draw_actor; fattorizzato in S46 perche' lo percorrono TUTTI E DUE i modi -
 * il ciclo legacy quando incontra la voce, il walk delle entita' quando
 * prepara i blocchi - e un ramo copiato in due posti e' un ramo che diverge.
 *
 * Il colore e' `coul1` NUDO, e non e' un ripiego: linee e sfere non hanno una
 * normale, quindi non c'e' intensita' da sommare ne' gradiente lungo la riga.
 * Sulle 1015 linee e 272 sfere di BODY.HQR la matiere e' 0 in tutte le linee
 * e in 259 sfere su 272; le tredici che restano chiedono Tele/Copper/Bopper,
 * effetti che il porting non disegna nemmeno sui poligoni.
 * ------------------------------------------------------------------------- */
static void draw_entity(const T_BODY3D *b, UWORD gi, UWORD off, UWORD nb_line,
                        WORD *ax0, WORD *ay0, WORD *ax1, WORD *ay1)
{
    if (gi < b->nb_polys + nb_line) {
        const be_byte *e = b->lines + (ULONG)off * 8;
        UWORD v0 = BE16u(e + 4);
        UWORD v1 = BE16u(e + 6);

        if (v0 >= b->nb_points) v0 = 0;
        if (v1 >= b->nb_points) v1 = 0;
        draw_line_ent(SCRX(v0), SCRY(v0), SCRX(v1), SCRY(v1), e[1]);
    } else {
        const be_byte *e = b->spheres + (ULONG)off * 8;
        UWORD v = BE16u(e + 6);
        WORD  rs, sx, sy;

        if (v >= b->nb_points) v = 0;
        sx = SCRX(v);
        sy = SCRY(v);
        /* P_OB_ISO.ASM:928, ramo `isosph`: 34 e' racine(sx^2+sy^2) e
         * 512 e' IsoScale, cioe' lo stesso divisore della proiezione
         * dei vertici. Il ramo prospettico (TYPE_3D) non serve: la
         * scena di gioco e' isometrica. */
        rs = (WORD)(((LONG)BE16u(e + 4) * 34) >> 9);
        /* La sfera ALLARGA il rettangolo dell'attore del suo raggio
         * (P_OB_ISO.ASM:936-956): il suo centro e' gia' un vertice
         * proiettato, ma il pallino sporge. */
        if (sx - rs < *ax0) *ax0 = (WORD)(sx - rs);
        if (sx + rs > *ax1) *ax1 = (WORD)(sx + rs);
        if (sy - rs < *ay0) *ay0 = (WORD)(sy - rs);
        if (sy + rs > *ay1) *ay1 = (WORD)(sy + rs);
        if (!BodyStat.havesphere) {
            BodyStat.havesphere = 1;
            BodyStat.firstsx = sx;
            BodyStat.firstsy = sy;
            BodyStat.firstsr = (UWORD)rs;
        }
        draw_sphere_ent(sx, sy, rs, e[1]);
    }
}


#if BODY_EMIT_ON_GPU
/* ---------------------------------------------------------------------------
 * S46: I POLIGONI DISEGNATI SI CONTANO DALLE CHIAVI MORTE DI WLIST.
 *
 * Dopo l'ordinamento la chiave del pittore e' dato consumato: il kernel (e
 * il modello) ci lasciano il TOTALE CORRENTE dei pixel all'uscita di ogni
 * voce - per i poligoni e' la scrittura di JobNext stessa, deviata con
 * HDRSLOT = LISTP-20, quindi la GPU non paga niente. Il 68000 ricostruisce
 * "quali poligoni hanno prodotto pixel" confrontando totali consecutivi,
 * esattamente come faceva coi totali del flusso: le voci entita' portano
 * anch'esse il totale (serve la continuita') ma non si contano.
 * ------------------------------------------------------------------------- */
static ULONG list_count_drawn(const T_BODY3D *b, int live)
{
    ULONG prev = 0, drawn = 0;
    int k;

    for (k = 0; k < live; k++) {
        ULONG cur = (ULONG)wlist[k * 2];

        if ((UWORD)((ULONG)wlist[k * 2 + 1] >> 16) < b->nb_polys
            && cur != prev)
            drawn++;
        prev = cur;
    }
    return drawn;
}

#if !BODY_FILL_ON_GPU
/* ---------------------------------------------------------------------------
 * S46: LA RACCOLTA DI UN POLIGONO - il MODELLO della fase E di gpu_body.s,
 * op per op quel che il kernel fa con LOAD e maschere. Riempie l'area di
 * regia `stage` nella STESSA forma di un lavoro del flusso (4 long di
 * intestazione + vertici da 2 long), cosi' il riempitore per-lavoro che
 * esiste gia' (job_one_c / fasi A+B del kernel) la consuma senza sapere da
 * dove arriva. Torna 0 se il poligono si salta (degenerato o fuori dalle
 * righe dello schermo).
 *
 * DUE DIVERGENZE DICHIARATE dal ciclo legacy, tutte e due invisibili ai
 * pixel:
 *   - il test X ("tutto a sinistra / tutto a destra dello schermo") non
 *     c'e' piu': il riempitore taglia gia' ogni campata alle colonne 0..639,
 *     quindi un poligono fuori in X produce zero pixel comunque - si paga
 *     il giro delle righe invece del confronto, su un caso raro;
 *   - il salto in Y usa ymin/ymax GIA' limitati: maxy<0 o miny>591 diventano
 *     ymin>ymax dopo il limite, che e' lo stesso insieme di poligoni.
 * ------------------------------------------------------------------------- */
static int gather_poly(const T_BODY3D *b, UWORD off, LONG *stage)
{
    const be_byte *h = b->polys + (ULONG)off * JBD_PSTRIDE;
    UWORD mat  = h[0];
    UWORD nbp  = h[1];
    UBYTE coul = h[2];
    UWORD fn   = BE16u(h + 4);
    const be_byte *c = b->corners + (ULONG)BE16u(h + 6) * JBD_CSTRIDE;
    LONG miny = 0x7FFF, maxy = -0x7FFF;
    LONG ymin, ymax;
    WORD raw = 0;
    int gouraud = (mat >= MAT_GOURAUD_MIN);
    int n, i;

    n = nbp;
    if (n > JAG_GPU_EDGE_MAX)
        n = JAG_GPU_EDGE_MAX;
    if (n < 3)
        return 0;
    if (!gouraud && mat >= MAT_FLAT_MIN && fn < b->nb_normals)
        raw = wint[fn];

    for (i = 0; i < n; i++) {
        UWORD ni = BE16u(c);
        UWORD v  = BE16u(c + 2);
        LONG xy;
        WORD y;

        c += JBD_CSTRIDE;
        if (v >= b->nb_points)
            v = 0;
        /* Il long impacchettato di wscr E' il long che il flusso voleva a +0
         * del vertice: si copia, non si ricompone. */
        xy = wscr[v * 2];
        y = (WORD)xy;
        if (y < miny) miny = y;
        if (y > maxy) maxy = y;
        stage[JOB_HDR + i * JOB_VTX] = xy;
        stage[JOB_HDR + i * JOB_VTX + 1] =
            shade_index(coul, gouraud
                              ? (ni < b->nb_normals ? wint[ni] : 0)
                              : raw);
    }

    ymin = miny < 0 ? 0 : miny;
    ymax = maxy > JAG_BG_H - 1 ? (LONG)(JAG_BG_H - 1) : maxy;
    if (ymin > ymax)
        return 0;
    stage[0] = n;
    stage[1] = ymin;
    stage[2] = ymax;
    stage[3] = 0;
    return n;
}

/* ---------------------------------------------------------------------------
 * S46: IL WALKER - il MODELLO di cio' che il kernel fa in modo lista. Cammina
 * wlist ordinata: una voce poligono si raccoglie nell'area di regia e passa
 * dal riempitore per-lavoro; una voce entita' consuma il BLOCCO successivo
 * del flusso ([conteggio][tratte]), che il 68000 ha scritto nello stesso
 * ordine - la corrispondenza e' posizionale, e l'ordine del pittore si
 * conserva per costruzione.
 * ------------------------------------------------------------------------- */
static void list_run_c(const T_BODY3D *b, int live)
{
    volatile LONG *rp = job_buf;
    LONG stage[JOB_HDR + JAG_GPU_EDGE_MAX * JOB_VTX];
    ULONG total = 0;
    int k;

    for (k = 0; k < live; k++) {
        UWORD gi  = (UWORD)((ULONG)wlist[k * 2 + 1] >> 16);
        UWORD off = (UWORD)wlist[k * 2 + 1];

        if (gi >= b->nb_polys) {
            while (*rp != 0)
                rp = job_one_c(rp, &total);
            rp++;                   /* oltre il terminatore del blocco */
        } else if (gather_poly(b, off, stage)) {
            job_one_c(stage, &total);
        }
        /* Il totale nella chiave morta, come il kernel: per il poligono
         * tuffato e' la scrittura di JobNext (qui p[3] della regia), per i
         * saltati e per le entita' e' questa riga. Scriverlo sempre e' la
         * regola piu' semplice che tiene la continuita'. */
        wlist[k * 2] = (LONG)total;
    }
    BodyStat.polys += list_count_drawn(b, live);
    BodyStat.pixels += total;
}
#endif /* !BODY_FILL_ON_GPU */

#if BODY_FILL_ON_GPU
/* ---------------------------------------------------------------------------
 * S46: IL LANCIO DEL MODO LISTA. Uno per attore, come jobs_flush, ma la GPU
 * riceve la lista ordinata e il corpo invece del flusso di vertici: EM_MODE=1
 * la manda nella fase E. Il flusso porta solo i blocchi di tratte.
 * ------------------------------------------------------------------------- */
static void list_gpu(const T_BODY3D *b, int live)
{
    ULONG t0;

    if (live <= 0) {
        job_used = 0;
        job_count = 0;
        job_runs = 0;
        return;
    }

    /* IL LANCIO E' LA PRIMA SCRITTURA SUL FRAMEBUFFER di questa passata
     * (stessa regola di jobs_flush): cancellare prima, col disegno vecchio
     * rimasto a schermo per tutto il calcolo. */
    Bg_ClsPending();

    t0 = BODY_CLOCK;
    BodyStat.launches++;

    /* Gia' fallito una volta: non si riprova (stessa regola del flusso). */
    if (BodyStat.gpuhang) {
        BodyStat.gpuhang++;
        job_used = 0;
        job_count = 0;
        job_runs = 0;
        BodyStat.t_stage[BT_GPU] += BODY_CLOCK - t0;
        return;
    }

    HW_L(JAG_GPU_FB)         = (unsigned long)body_fb_base;
    HW_L(JAG_GPU_JOBS)       = (unsigned long)JOB_STREAM_BASE;
    HW_L(JAG_GPU_NJOB)       = (unsigned long)job_count;
    HW_L(JAG_GPU_PIXELS)     = 0;
    HW_L(JAG_GPU_EM_MODE)    = 1;
    HW_L(JAG_GPU_EM_LIST)    = (unsigned long)(void *)wlist;
    HW_L(JAG_GPU_EM_LIVE)    = (unsigned long)live;
    HW_L(JAG_GPU_EM_POLYS)   = (unsigned long)(const void *)b->polys;
    HW_L(JAG_GPU_EM_NPOLY)   = (unsigned long)b->nb_polys;
    HW_L(JAG_GPU_EM_CORNERS) = (unsigned long)(const void *)b->corners;
    HW_L(JAG_GPU_EM_WSCR)    = (unsigned long)(void *)wscr;
    HW_L(JAG_GPU_EM_WINT)    = (unsigned long)(void *)wint;
    HW_L(JAG_GPU_EM_NPTS)    = (unsigned long)b->nb_points;
    HW_L(JAG_GPU_EM_NNORM)   = (unsigned long)b->nb_normals;
    HW_L(JAG_GPU_EM_SAWL)    = 0;

    /* Il guardiano scala col lavoro vero: le voci della lista piu' le
     * tratte, che sono lavori a tutti gli effetti. */
    if (!Gpu_Run(JAG_GPU_BODY_ENTRY, JAG_GPU_DONE,
                 GPU_RUN_GUARD_BASE
                 + GPU_RUN_GUARD_PER_JOB * ((ULONG)live + (ULONG)job_count))) {
        if (!BodyStat.gpuhang) {
            BodyStat.hangwhere = (BodyStat.polys << 8) | (ULONG)job_count;
            BodyStat.hangwhat  = (ULONG)live;
            /* Quel che la GPU ha LETTO: in modo lista SAWJ/SAWC echeggiano
             * base della lista e voci, EM_SAWL il modo|live della fase E. */
            BodyStat.hangsaw   = HW_L(JAG_GPU_SAWJ);
            BodyStat.hangsawc  = HW_L(JAG_GPU_SAWC);
        }
        BodyStat.gpuhang++;
    } else {
        BodyStat.polys  += list_count_drawn(b, live);
        BodyStat.pixels += HW_L(JAG_GPU_PIXELS);
    }
    job_used = 0;
    job_count = 0;
    job_runs = 0;
    BodyStat.t_stage[BT_GPU] += BODY_CLOCK - t0;
}
#endif /* BODY_FILL_ON_GPU */
#endif /* BODY_EMIT_ON_GPU */


/* ---------------------------------------------------------------------------
 * Un attore.
 * ------------------------------------------------------------------------- */
static void fletch(ULONG *s, ULONG w)
{
    s[0] += w;
    s[1] += s[0];
}

/* ---------------------------------------------------------------------------
 * LA POSA DELL'ATTORE dentro `wpose`, appena prima di disegnarlo.
 *
 * E' il posto che nel gioco occupa `SetInterAnimObjet2(Frame, ptranim,
 * ptrbody)`, chiamata subito prima di `AffObjetIso` (OBJECT.C:3005): tutto il
 * resto della pipeline resta dov'e' ed e' per questo che l'animazione entra
 * senza toccare niente a valle.
 *
 * Un attore senza animazione (`Anim == -1`, ed e' ancora il caso di tutti)
 * resta a riposo: tipo ROTATE e tre angoli a zero, che e' esattamente quel che
 * l'archivio ha nei byte +8..+15 di ogni corpo - verificato, 1844 record su
 * 1844. Non e' quindi una nostra convenzione di ripiego, e' il dato.
 * ------------------------------------------------------------------------- */

/* La divisione del C TRONCA VERSO LO ZERO. Il gioco ci conta - i numeratori
 * negativi sono la norma, un angolo che cala o un valore che scende - e sul
 * 68000 `/` fa gia' cosi'. Sta qui in chiaro perche' l'oracolo Python deve fare
 * lo stesso e il suo `//` fa l'opposto: e' la trappola di S37, ripresentata. */
static LONG inter_scale(LONG delta, WORD cur, WORD tot)
{
    return (delta * (LONG)cur) / (LONG)tot;
}

/* PatchInterStep, P_ANIM.C:98. Un valore che scivola da `last` a `new`. */
static WORD patch_inter_step(WORD last, WORD nw, WORD cur, WORD tot)
{
    if (nw != last)
        last = (WORD)(last + inter_scale((LONG)nw - (LONG)last, cur, tot));
    return last;
}

/* PatchInterAngle, P_ANIM.C:121. Un ANGOLO che scivola, per la via CORTA.
 *
 * Il giro e' 1024. Senza la correzione a meta' giro, andare da 1000 a 20
 * percorrerebbe 980 unita' all'indietro invece di 44 in avanti: il braccio
 * girerebbe dalla parte sbagliata, lentamente, per tutta l'interpolazione. */
static WORD patch_inter_angle(WORD last, WORD target, WORD cur, WORD tot)
{
    LONG base = last & LBA_ANGLE_MASK;
    LONG diff = (LONG)(target & LBA_ANGLE_MASK) - base;

    if (diff != 0) {
        if (diff < -LBA_ANGLE_HALF)
            diff += LBA_ANGLE_FULL;
        else if (diff > LBA_ANGLE_HALF)
            diff -= LBA_ANGLE_FULL;
        base = (base + inter_scale(diff, cur, tot)) & LBA_ANGLE_MASK;
    }
    return (WORD)base;
}

static void pose_rest(const T_BODY3D *b)
{
    UWORD g;

    for (g = 0; g < b->nb_groups; g++) {
        wpose[g * POSE_WORDS + 0] = TYPE_ROTATE;
        wpose[g * POSE_WORDS + 1] = 0;
        wpose[g * POSE_WORDS + 2] = 0;
        wpose[g * POSE_WORDS + 3] = 0;
    }
}

/* S47: la fotografia del cursore anim dell'EROE, presa DENTRO pose_load.
 *
 * E' la verifica residua della diagnosi S46: le sonde SHADE/SUM cadono con
 * wint a +1 sul ~40%, e la catena di prove indica una POSA diversa fra il
 * Jaguar e le attese - ma il pin di set_step_pose dovrebbe renderla
 * deterministica. Questo blocco non deduce: MOSTRA il cursore che la posa ha
 * usato davvero (host, BigPEmu e ferro leggono gli stessi campi), e la terna
 * del gruppo 2, cioe' il gruppo su cui la caccia ha chiuso il conto. */
/* IL LATCH: senza, la cattura e' dell'ULTIMA posa e non della sonda. La
 * modalita' gioco parte subito e Twinsen respira, quindi una lettura a 50 s
 * mostra il respiro in corso (misurato in S47: frm=6 cur=12 tref=1890) e non
 * la passata del boot - lo stesso vizio che ha inquinato le letture wint di
 * S46. Si arma prima della passata che si vuole fotografare, scatta UNA
 * volta sulla prima posa dell'eroe, e si disarma da solo. */
static int pin_armed;

void Body_PinArm(void)
{
    pin_armed = 1;
}

static void pin_capture(const T_OBJET *o, ULONG elapsed, WORD tot, int arrived)
{
    if (!pin_armed || o != &ListObjet[0])
        return;
    pin_armed = 0;
    BodyStat.pinfrm  = (UWORD)o->Frame;
    BodyStat.pinsrc  = (UWORD)o->AnimSrcFrame;
    BodyStat.pincur  = elapsed;
    BodyStat.pintot  = (UWORD)tot;
    BodyStat.pinarr  = (UWORD)arrived;
    BodyStat.pintref = TimerRef;
    BodyStat.ping2a  = ((ULONG)(UWORD)wpose[2 * POSE_WORDS + 0] << 16)
                     | (UWORD)wpose[2 * POSE_WORDS + 1];
    BodyStat.ping2b  = ((ULONG)(UWORD)wpose[2 * POSE_WORDS + 2] << 16)
                     | (UWORD)wpose[2 * POSE_WORDS + 3];
}

static void pose_load(const T_OBJET *o, const T_BODY3D *b)
{
    const le_byte *anim, *fd, *fs;
    const WORD *snap;
    UWORD nbf, nba, stride, nbg, g;
    WORD tot, cur;
    ULONG elapsed;

    pose_rest(b);

    if (o->Anim < 0 || b->nb_groups == 0)
        return;

    anim = (const le_byte *)HQR_Get(HQR_ANIMS, o->Anim);
    if (!anim)
        return;

    nbf = LE16u(anim);
    nba = LE16u(anim + 2);
    if (nbf == 0 || nba == 0)
        return;
    /* Il gioco NON controlla il fotogramma qui (`SetAnimObjet` si', P_ANIM.C:272,
     * ma questa no): con un Frame fuori dall'animazione leggerebbe oltre la
     * voce. A noi la voce arriva dalla cartuccia, dove oltre c'e' un altro
     * archivio: e' una lettura che non fa rumore e sposta un braccio. */

    /* LA SORGENTE PUO' NON ESSERE UN FOTOGRAMMA (M6). Quando un'animazione ne
     * sostituisce un'altra, `InitAnim` fotografa la posa corrente e mette
     * `AnimSrcFrame` a -1: da li' il raccordo parte dalla fotografia, che non
     * appartiene a NESSUNA delle due animazioni. E' `StockInterAnim`
     * (P_ANIM.C:669), e senza di essa il cambio di animazione partirebbe da un
     * fotogramma della vecchia scelto a caso - il gomito che salta.
     *
     * Il controllo del fotogramma va quindi fatto DOPO aver escluso questo caso:
     * `(UWORD)-1` sarebbe sempre fuori dall'animazione e fermerebbe il gioco. */
    snap = (o->AnimSrcFrame < 0) ? o->PtrPoseSrc : 0;

    if ((UWORD)o->Frame >= nbf || (!snap && (UWORD)o->AnimSrcFrame >= nbf))
        TheEnd(TE_ANIM_BAD, "fotogramma fuori dall'animazione");

    stride = nba * 8 + 8;
    fd = anim + 8 + (long)stride * (UWORD)o->Frame;
    fs = snap ? fd : anim + 8 + (long)stride * (UWORD)o->AnimSrcFrame;

    nbg = nba < b->nb_groups ? nba : b->nb_groups;
    tot = LE16(fd);

    /* IL TEMPO TRASCORSO SI CONFRONTA A 32 BIT SENZA SEGNO, e la riduzione a
     * WORD viene DOPO. P_ANIM.C:517-518 lo fa cosi' e non e' pignoleria:
     *
     *     elapsed = TimerRef - memoTicks;        <- ULONG
     *     if (elapsed >= (ULONG)timeToFrame)     <- confronto a 32 bit
     *
     * e `currentStep = (WORD)elapsed` compare solo nel ramo dove si e' gia'
     * stabilito che elapsed sta SOTTO la durata, cioe' dove ci sta davvero.
     *
     * Restringere prima e' costato due cicli sul ferro (S38). Con una posa
     * ferma a lungo la differenza supera 32767, come short con segno diventa
     * NEGATIVA, il confronto dice "non ancora arrivato" e si interpola con un
     * passo di -32768: gli angoli finiscono a caso, il corpo si scompone e il
     * suo riquadro esplode. Si vedeva come le tessere M3d che passavano da 2 a
     * 30-38 - cioe' NON dove il difetto era.
     *
     * `tot` viene dall'archivio e potrebbe essere zero: in quel caso qualunque
     * trascorso e' >= e si prende il fotogramma pieno, che e' esattamente quel
     * che fa il gioco (e rende morta la sua riga `if (totalSteps == 0)`).
     *
     * DAL S41 QUESTA RIGA E' COPERTA DAL BANCO SU PC, e prima non lo era pur
     * avendo un passo scelto apposta (0x9000, oltre 32767): quel passo stava sul
     * fotogramma ZERO, dove il fotogramma di partenza e' lo stesso di arrivo e
     * interpolare non muove niente - i due rami davano lo stesso risultato e la
     * scelta fra loro non era osservabile. Spostato su un fotogramma con una
     * partenza diversa (tools/scene_parse.py:ANIM_STEPS), rimettere `cur >= tot`
     * fa cadere undici sonde del passo 0, corpo compreso.
     *
     * Quel che il banco ancora NON prova: un trascorso oltre 65535, perche' nel
     * dump il campo e' a 16 bit. Li' anche il solo restringere il temporaneo
     * (senza toccare il confronto) diventa un difetto. */
    elapsed = TimerRef - o->AnimMemoTicks;
    cur = (WORD)elapsed;                /* valido solo nel ramo interpolato */

    if (elapsed >= (ULONG)tot) {
        /* Il fotogramma e' ARRIVATO: i record si copiano tali e quali. */
        for (g = 0; g < nbg; g++) {
            const le_byte *r = fd + 8 + (long)g * 8;
            wpose[g * POSE_WORDS + 0] = LE16(r);
            wpose[g * POSE_WORDS + 1] = LE16(r + 2);
            wpose[g * POSE_WORDS + 2] = LE16(r + 4);
            wpose[g * POSE_WORDS + 3] = LE16(r + 6);
        }
        pin_capture(o, elapsed, tot, 1);
        return;
    }

    if (tot == 0)
        tot = 1;

    /* Il gruppo 0 NON si interpola: la sua terna e' la rotazione MAESTRA, che
     * agisce sul Beta dell'attore e non sulla posa (P_ANIM.C:553-557). */
    for (g = 1; g < nbg; g++) {
        const le_byte *rd = fd + 8 + (long)g * 8;
        const le_byte *rs = fs + 8 + (long)g * 8;
        /* IL TIPO VIENE DAL FOTOGRAMMA D'ARRIVO (PatchType, P_ANIM.C:85): e' il
         * fotogramma verso cui si va a dire come si legge la terna. */
        WORD typ = LE16(rd);
        int k;

        wpose[g * POSE_WORDS + 0] = typ;
        for (k = 0; k < 3; k++) {
            WORD dv = LE16(rd + 2 + k * 2);
            /* La fotografia e' gia' in ordine di byte NATIVO e nella stessa
             * forma di `wpose`: e' roba nostra, scritta in DRAM, non un record
             * letto dalla cartuccia. */
            WORD sv = snap ? snap[g * POSE_WORDS + 1 + k]
                           : LE16(rs + 2 + k * 2);

            if (typ == TYPE_ROTATE)
                wpose[g * POSE_WORDS + 1 + k] = patch_inter_angle(sv, dv, cur, tot);
            else if (typ == TYPE_TRANSLATE)
                wpose[g * POSE_WORDS + 1 + k] = patch_inter_step(sv, dv, cur, tot);
            else
                TheEnd(TE_ANIM_BAD, "tipo di trasformazione sconosciuto");
        }
    }
    pin_capture(o, elapsed, tot, 0);
}


/* ---------------------------------------------------------------------------
 * LA FOTOGRAFIA DELLA POSA - `StockInterAnim`, P_ANIM.C:669.
 *
 * L'ANELLO E' DI UPSTREAM, NON UNA NOSTRA IDEA: 5000 byte condivisi da tutti
 * gli attori, allocati a scorrimento a ogni cambio di animazione e mai
 * liberati (`PtrBufferAnim`, OBJECT.C:1242). Funziona perche' una fotografia
 * vive poco: dura il raccordo, cioe' fino al primo fotogramma che arriva. Una
 * fotografia per attore costerebbe 26 KB per tenere in vita, quasi sempre,
 * cento pose ferme.
 *
 * La forma e' quella di `wpose`, non quella dell'archivio: tipo e tre valori
 * per gruppo, word native. E' il dato che `pose_load` rilegge, e sta in DRAM.
 *
 * Il corpo si ri-percorre qui invece di riusare `wpose`: `wpose` e' lo scratch
 * dell'attore disegnato PER ULTIMO, e chi cambia animazione non e' quasi mai
 * lui. Costa una passata di posa per cambio di animazione, cioe' niente.
 * ------------------------------------------------------------------------- */
#define ANIM_SNAP_RING   2500        /* 5000 byte, come BufferAnim upstream */

static WORD anim_snap[ANIM_SNAP_RING];
static UWORD anim_snap_ptr;

void Body_SnapPose(WORD numobj)
{
    T_OBJET *o = &ListObjet[numobj];
    T_BODY3D b;
    UWORD n, g;

    /* IL PUNTATORE VECCHIO NON SI AZZERA QUI, e il perche' e' costato un
     * TheEnd sul bersaglio (S42).
     *
     * `pose_load` qui sotto calcola la posa CORRENTE, e la posa corrente puo'
     * essere essa stessa un raccordo, cioe' avere per sorgente una fotografia
     * precedente (`AnimSrcFrame == -1`). Azzerando il puntatore prima, quella
     * lettura trova sorgente negativa e puntatore nullo: `(UWORD)-1` e' fuori
     * da qualunque animazione e il motore si ferma con TE_ANIM_BAD.
     *
     * Non e' un caso di laboratorio: `Game_Frame` chiama InitAnim DUE VOLTE
     * nello stesso quadro ogni volta che il pad cambia mentre una direzione e'
     * premuta - prima GEN_ANIM_RIEN, poi il cammino. Fotografare una
     * fotografia e' quindi la regola, non l'eccezione. */
    if (o->Body < 0 || o->Anim < 0) {
        o->PtrPoseSrc = 0;
        return;
    }
    if (!body_parse(PtrBody[o->Body], &b)) {
        o->PtrPoseSrc = 0;
        return;
    }

    n = b.nb_groups;
    if (n > MAXG)
        n = MAXG;

    /* Upstream gira quando lo spazio residuo scende sotto 512 byte; qui la
     * misura e' la fotografia stessa, che si conosce. */
    if ((ULONG)anim_snap_ptr + n * POSE_WORDS > ANIM_SNAP_RING)
        anim_snap_ptr = 0;

    pose_load(o, &b);

    for (g = 0; g < n * POSE_WORDS; g++)
        anim_snap[anim_snap_ptr + g] = wpose[g];

    o->PtrPoseSrc = &anim_snap[anim_snap_ptr];
    anim_snap_ptr = (UWORD)(anim_snap_ptr + n * POSE_WORDS);
}


/* ---------------------------------------------------------------------------
 * LO STADIO DELL'INTENSITA', NELLA FORMA CHE ESEGUIRA' LA GPU (S39, punto 1).
 *
 * Stessa aritmetica di S38, riscritta come UN ciclo piatto su array a passo
 * fisso. Non e' cosmesi: e' il CONTRATTO che `gpu_body.s` dovra' trascrivere, e
 * scriverlo prima in C vuol dire che il banco su PC lo verifica bit per bit
 * PRIMA che esista una riga di assembly. E' lo stesso metodo di
 * BODY_FILL_ON_GPU, che esiste perche' il C e' il modello del riempitore.
 *
 * QUEL CHE CAMBIA: la gerarchia dei gruppi sparisce dal ciclo caldo.
 * Sopravviveva solo per decidere QUALE matrice e QUANTE normali - due numeri per
 * gruppo - e leggerli costava un accesso a passo 38 dentro il record del gruppo,
 * per ogni gruppo, dentro il giro. Ora li appiattisce il 68000 una volta sola in
 * un descrittore di al piu' 33 voci, e il ciclo non tocca piu' `b->groups`.
 *
 * Una GPU con 4 KB di RAM che insegue record a lunghezza variabile in DRAM e' lo
 * scenario peggiore per quel silicio; scorrere due array a passo fisso e' quel
 * che sa fare (voce 3c-i di docs/todo_s39.md).
 *
 * QUESTO PASSO NON TOCCA IL PROCESSORE, sposta solo la FORMA. Le sonde devono
 * restare identiche al bit: e' il solo modo di sapere che il contratto e' giusto
 * prima di scommetterci sopra dell'assembly che non si puo' passo-passo.
 * ------------------------------------------------------------------------- */
/* ---------------------------------------------------------------------------
 * LE NORMALI IN FORMA NATIVA - e dal S43 la forma nativa e' quella del BLOB.
 *
 * La cache di S39 (norm_be) esisteva per pagare UNA volta la conversione
 * little-endian -> nativo. Il packer adesso deposita x,y,z,rng gia' big
 * endian, a passo 8 e con l'array allineato alla phrase (body_digest.py
 * allinea l'offset, hal_hqr.c decomprime su base LoadMallocPhrase): sul
 * Jaguar il puntatore nudo E' la forma nativa, e la cache sparisce insieme
 * alla copia in arena che pagava.
 *
 * SUL BANCO SU PC la conversione resta: un x86 little endian non puo' leggere
 * il big endian in place, quindi il ramo !JAG_BE_NATIVE copia come prima -
 * con BE16 al posto di LE16. Stessi valori da tutte e due le strade, e sono
 * le sonde a provarlo.
 *
 * IL SEGNO E' PARTE DEL CONTRATTO: tutti e quattro i campi - `rng` compreso -
 * sono CON segno, come nell'originale.
 * ------------------------------------------------------------------------- */
#if JAG_BE_NATIVE

void Body_NormCacheReset(void)
{
    /* Niente cache da azzerare: i puntatori stanno nel blob, che vive e muore
     * con l'arena per-cubo insieme a tutto il resto del corpo. */
}

static const WORD *norm_native(WORD bodyidx, const T_BODY3D *b)
{
    (void)bodyidx;
    if (b->nb_normals == 0)
        return 0;                 /* nessuna normale: il ciclo non gira */
    /* La GPU legge questo array con load allineate: se il packer o
     * l'allocatore perdono l'allineamento, meglio un codice parlante subito
     * di un kernel che legge phrase spostate. */
    if (((ULONG)(const void *)b->normals) & 7)
        TheEnd(TE_BODY_BIG, "normali non allineate alla phrase");
    return (const WORD *)(const void *)b->normals;
}

#else  /* banco su PC: si converte, una volta per corpo */

static const WORD *norm_be[MAX_BODYS];

void Body_NormCacheReset(void)
{
    int i;

    for (i = 0; i < MAX_BODYS; i++)
        norm_be[i] = 0;
}

static const WORD *norm_native(WORD bodyidx, const T_BODY3D *b)
{
    const be_byte *p;
    WORD *q;
    UWORD k;

    if (bodyidx < 0 || bodyidx >= MAX_BODYS)
        TheEnd(TE_BODY_BIG, "indice di corpo fuori dalla cache normali");
    if (norm_be[bodyidx])
        return norm_be[bodyidx];
    if (b->nb_normals == 0)
        return 0;                 /* nessuna normale: il ciclo non gira */

    q = (WORD *)LoadMallocPhrase((ULONG)b->nb_normals * 8UL);

    p = b->normals;
    for (k = 0; k < b->nb_normals; k++) {
        q[k * 4 + 0] = BE16(p);
        q[k * 4 + 1] = BE16(p + 2);
        q[k * 4 + 2] = BE16(p + 4);
        q[k * 4 + 3] = BE16(p + 6);      /* rng, CON segno come l'originale */
        p += 8;
    }
    norm_be[bodyidx] = q;
    return q;
}

#endif /* JAG_BE_NATIVE */


/* UNA VOCE = UN LONG, e non una struttura di due word (S40).
 *
 * La GPU vede un vettore di long: se qui ci fosse una `struct` il banco di
 * prova verificherebbe una disposizione che sul Jaguar non esiste. E' la stessa
 * ragione per cui `jobs_run_c` legge i bordi per indice invece che attraverso
 * T_EDGE, ed e' il motivo per cui questo cambio di forma va fatto PRIMA di
 * scrivere l'assembly e non dopo: le sonde devono restare identiche al bit
 * mentre l'unica cosa che cambia e' la disposizione.
 *
 *     moff<<16 | count       moff = scostamento in LONG dentro wmat (g*9)
 *                            count = normali consecutive con quella matrice
 *
 * Un long e' anche l'unita' che la GPU carica con una `load` sola, allineata
 * per costruzione. */
#define SHADE_SPAN(moff, count)  (((LONG)(moff) << 16) | (LONG)(count))
#define SHADE_SPAN_MOFF(v)       ((UWORD)(((ULONG)(v) >> 16) & 0xFFFFUL))
#define SHADE_SPAN_COUNT(v)      ((UWORD)((ULONG)(v) & 0xFFFFUL))

/* Fuori dalla pila apposta: draw_actor gia' ci tiene tre array di angoli, e su
 * un 68000 la pila non e' il posto dove mettere 132 byte che si possono avere
 * gratis. Nessuna ricorsione, un attore alla volta: e' scratch, si scrive prima
 * di leggerlo a ogni attore.
 *
 * ALLINEATO A 4, ED E' UN REQUISITO DELLA GPU, NON UNA FINEZZA (S47). Questo
 * buffer si consegna al kernel shade (GPU_SH_SPANS) e la GPU TOM IGNORA i due
 * bit bassi dell'indirizzo nelle `load` esterne a 32 bit: su un indirizzo
 * 2-mod-4 legge il long allineato IN GIU', cioe' [coda di BodyStat|moff]
 * invece di [moff|count] - moff e count si scambiano di posto in OGNI voce e
 * ogni gruppo prende la matrice di un altro. A riposo le matrici differiscono
 * di pochissimo, quindi il sintomo era subdolo: intensita' a +-1 sul ~40%
 * (SHADE1/2 e SUM1/2 giu', tutto il resto verde). E' successo in S46 senza
 * toccare lo shade: pad46+rundrop hanno allungato BodyStat di 2 byte netti e
 * questo buffer, che in BSS sta subito dopo, e' scivolato da 0x...4C a
 * 0x...4E. L'ABI m68k allinea i LONG a 2, quindi il linker non protegge:
 * OGNI buffer il cui puntatore finisce in un registro/porta della GPU deve
 * dichiararsi allineato a 4 da solo. Gli altri (wscr, wint, wlist, wmat,
 * pose_desc, normali, flusso) vivono su basi fisse della mappa o escono da
 * LoadMallocPhrase, quindi sono gia' a posto. */
static LONG shade_span_buf[MAXG + 1] __attribute__((aligned(4)));

static int shade_spans(const T_BODY3D *b, LONG *out)
{
    int n, ns = 0;
    ULONG seen = 0;

    /* Corpo senza gruppi: una matrice sola per tutte le normali. */
    if (b->nb_groups == 0) {
        out[0] = SHADE_SPAN(0, b->nb_normals);
        return 1;
    }

    for (n = 0; n < (int)b->nb_groups; n++) {
        UWORD cnt = BE16u(b->groups + (long)n * JBD_GSTRIDE + 8);
        out[ns] = SHADE_SPAN(n * 9, cnt);
        seen += cnt;
        ns++;
    }

    /* LA CODA: normali oltre la somma dei conteggi. Nell'archivio non ne
     * esistono (verificato su tutti e 132 i corpi in S38), ma se un giorno ne
     * arrivasse una deve avere una matrice DICHIARATA invece di prendersi
     * quella dell'ultimo giro - ed e' la matrice del gruppo 0, come in S38.
     *
     * Se i conteggi SFORANO, la coda vale zero e il taglio lo fa la guardia
     * `i < nb_normals` dentro il ciclo, esattamente come prima. */
    out[ns] = SHADE_SPAN(0, (seen < b->nb_normals ? b->nb_normals - seen : 0));
    ns++;
    return ns;
}

#if !BODY_SHADE_ON_GPU
/* ---------------------------------------------------------------------------
 * I LIMITI SU CUI IL KERNEL SULLA GPU SCOMMETTE - misurati, non argomentati.
 *
 * `imultn`/`imacn` guardano SOLO i sedici bit bassi dei due operandi ("the
 * bottom 16-bits of each of the source and destination registers"). Il C qui
 * moltiplica a 32 bit: se una voce di matrice, una componente ruotata o `rng`
 * uscissero da 16 bit con segno, il Jaguar calcolerebbe un altro numero e
 * questo banco NON se ne accorgerebbe, perche' il difetto starebbe solo
 * dall'altra parte - il buco simmetrico di feedback_buco_simmetrico_oracolo,
 * qui tappato dall'unico lato che puo' vederlo.
 *
 * Sono tre massimi in valore assoluto sui dati VERI di tutte le pose del banco,
 * e host_body.c li confronta con 32767. Non costano niente sul Jaguar: questo
 * blocco esiste solo quando lo stadio gira in C.
 * ------------------------------------------------------------------------- */
typedef struct {
    LONG mat;      /* |voce di matrice|                      */
    LONG rot;      /* |componente della normale ruotata|     */
    LONG rng;      /* |rng| letto dal corpo                  */
    LONG dot;      /* |dot|, solo per sapere quanto e' largo */
} T_SHADEBOUND;
static T_SHADEBOUND ShadeBound;

static void bound_hit(LONG *slot, LONG v)
{
    if (v < 0)
        v = -v;
    if (v > *slot)
        *slot = v;
}

static void shade_stage(const WORD *normals, UWORD nb_normals,
                        const LONG *mats, const LONG *spans, int ns,
                        const LONG *light, WORD *out)
{
    const WORD *p = normals;
    UWORD i = 0;
    int s;

    if (!p)
        return;

    for (s = 0; s < ns; s++) {
        const LONG *gm = mats + SHADE_SPAN_MOFF(spans[s]);
        UWORD cnt = SHADE_SPAN_COUNT(spans[s]);
        UWORD j;

        for (j = 0; j < cnt && i < nb_normals; j++, i++) {
            LONG r[3], dot;
            /* La divisione e' intera e il negativo diventa zero PRIMA di
             * dividere (`or dx,dx / js nointensity`, P_OB_ISO.ASM:1687): una
             * faccia che guarda dall'altra parte non e' un'intensita' da
             * ribaltare, e' buio. */
            LONG rng = p[3];

            rot_vec(gm, p[0], p[1], p[2], r);
            dot = r[0] * light[0] + r[1] * light[1] + r[2] * light[2];
            {
                int k;
                for (k = 0; k < 9; k++)
                    bound_hit(&ShadeBound.mat, gm[k]);
                for (k = 0; k < 3; k++)
                    bound_hit(&ShadeBound.rot, r[k]);
                bound_hit(&ShadeBound.rng, rng);
                bound_hit(&ShadeBound.dot, dot);
            }
            if (dot < 0 || rng == 0)
                out[i] = 0;
            else
                out[i] = (WORD)(dot / rng);
            p += 4;                    /* 8 byte = una phrase */
        }
    }
}
#endif

#if BODY_SHADE_ON_GPU
/* ---------------------------------------------------------------------------
 * LO STESSO STADIO, SULLA GPU TOM (S40). Il codice e' src/gpu_shade.s.
 *
 * Qui non c'e' aritmetica: ci sono nove long di argomenti e una stretta di
 * mano. Tutto quel che si poteva sbagliare nei numeri e' gia' stato sbagliato o
 * no sul banco di prova su PC, dove gira `shade_stage` qui sopra contro
 * l'oracolo Python - e quando le sonde SHADE cadranno sul ferro senza cadere su
 * PC, la domanda sara' una sola invece di due.
 *
 * IL GUARDIANO SI TARA SUL LAVORO, come per il riempitore: una base per la
 * stretta di mano piu' un tanto per normale. Il kernel spende ~60 tick per
 * normale piu' i sedici del divisore, cioe' ~25000 tick per un corpo da 320
 * normali; il 68000 in attesa fa un giro ogni ~40 tick della GPU. Quaranta giri
 * per normale sono percio' un fattore sei di margine, non un numero grande per
 * prudenza (che e' l'errore del milione di S33).
 * ------------------------------------------------------------------------- */
static void shade_gpu(const WORD *normals, UWORD nb_normals,
                      const LONG *mats, const LONG *spans, int ns,
                      const LONG *light, WORD *out)
{
    if (!normals || !nb_normals || ns <= 0)
        return;

    /* Gia' fallito una volta: non si riprova. Ogni tentativo costa un guardiano
     * intero e la prima volta ha gia' registrato tutto quel che serve. */
    if (BodyStat.shadehang) {
        BodyStat.shadehang++;
        return;
    }

    HW_L(JAG_GPU_SH_NORM)  = (unsigned long)normals;
    HW_L(JAG_GPU_SH_NN)    = (unsigned long)nb_normals;
    HW_L(JAG_GPU_SH_OUT)   = (unsigned long)out;
    HW_L(JAG_GPU_SH_SPANS) = (unsigned long)spans;
    HW_L(JAG_GPU_SH_NS)    = (unsigned long)ns;
    HW_L(JAG_GPU_SH_MATS)  = (unsigned long)mats;
    HW_L(JAG_GPU_SH_LX)    = (unsigned long)light[0];
    HW_L(JAG_GPU_SH_LY)    = (unsigned long)light[1];
    HW_L(JAG_GPU_SH_LZ)    = (unsigned long)light[2];
    HW_L(JAG_GPU_SH_LEFT)  = 0;

    if (!Gpu_Run(JAG_GPU_SHADE_ENTRY, JAG_GPU_SH_DONE,
                 GPU_RUN_GUARD_BASE
                 + GPU_RUN_GUARD_PER_NORM * (ULONG)nb_normals)) {
        BodyStat.shadehang++;
        /* Quel che la GPU ha LETTO contro quel che il 68000 ha scritto: separa
         * "sbaglia i conti" da "non ha mai visto i dati giusti". */
        BodyStat.shadesaw = (HW_L(JAG_GPU_SH_SAWN) << 16)
                            | (HW_L(JAG_GPU_SH_SAWS) & 0xFFFFUL);
        return;
    }

    /* Normali che il descrittore non ha coperto. Deve valere zero: la voce di
     * coda di `shade_spans` esiste apposta. Si accumula invece di sovrascriversi
     * perche' un attore solo che sfora non deve sparire dietro i dieci che
     * seguono. */
    BodyStat.shadeleft += HW_L(JAG_GPU_SH_LEFT);
}
#endif


static void draw_actor(const T_OBJET *o, const T_BODY3D *b,
                       const LONG *light, LONG vx, LONG vy)
{
    UWORD i;
    int live, k, nd;
    ULONG actor_bytes;
    ULONG t0, t1, t0_gpu, t0_over, t0_cls;
    UWORD nb_line, nb_sph;
#if BODY_FRONT_ON_GPU
    LONG front_live;
#endif
    /* Il rettangolo dell'attore, `ScreenXmin/Ymin/Xmax/Ymax`. Parte a 32767 e
     * -32767 come in P_OB_ISO.ASM:231-236 - qui lo torna la fase V, che lo
     * accumula su TUTTI i vertici proiettati come faceva il ciclo di
     * proiezione (P_OB_ISO.ASM:1270-1332). */
    WORD act_x0, act_y0, act_x1, act_y1;
    WORD bb[4];

    /* --- posa: le MATRICI dei gruppi e il descrittore (S45).
     *
     * `wpose` e' gia' pronto: lo riempie il chiamante (Body_DrawAll) prima di
     * arrivare qui, perche' e' li' che si sa quale animazione e quale fotogramma
     * ha questo attore. Tutto zero = posa a riposo. */
    t0 = BODY_CLOCK;
    nd = pose_mats_desc(b, 0, o->Beta, 0);
    t1 = BODY_CLOCK; BodyStat.t_stage[BT_POSE] += t1 - t0; t0 = t1;

    /* --- fase V (e C, sulla GPU): vertici ruotati per gruppo, proiettati,
     * rettangolo. Sulla GPU il lancio copre anche la faccia nascosta: `live`
     * esce da qui e la finestra BT_CULL misura solo le entita'. --- */
#if BODY_FRONT_ON_GPU
    front_live = front_gpu(b, nd, o->PosObjX, o->PosObjY, o->PosObjZ,
                           vx, vy, bb);
    if (front_live < 0) {
        /* Guardiano scattato: nessun vertice esiste, si salta l'attore. Il
         * primo fallimento ha gia' registrato la diagnostica. */
        BodyStat.t_stage[BT_VERT] += BODY_CLOCK - t0;
        return;
    }
#else
    vert_stage(nd, b->verts, b->nb_points,
               o->PosObjX, o->PosObjY, o->PosObjZ, vx, vy, bb);
#endif
    act_x0 = bb[0]; act_x1 = bb[1]; act_y0 = bb[2]; act_y1 = bb[3];

#if BODY_ACTOR_SPRITE
    /* L'EROE VA NEL BUFFER SUO, non nel fondo - e la decisione si prende QUI,
     * non prima: solo adesso il riquadro esiste, e il riquadro e' l'unica cosa
     * che dice se l'attore ci sta davvero. Se non ci sta si disegna nel fondo
     * come sempre: non e' un ripiego, e' la strada vecchia che resta giusta. */
    spr_on = 0;
    if (o == &ListObjet[0]) {
        spr_why = SPR_WHY_HERO;
        spr_bb[0] = act_x0; spr_bb[1] = act_x1;
        spr_bb[2] = act_y0; spr_bb[3] = act_y1;
        spr_want_x0 = 0;
        if (act_x0 <= act_x1 && act_y0 <= act_y1)
            spr_why |= SPR_WHY_BOX;
    }
    if (o == &ListObjet[0] && act_x0 <= act_x1 && act_y0 <= act_y1) {
        WORD y0 = act_y0 < 0 ? 0 : act_y0;
        WORD y1 = act_y1 > JAG_BG_H - 1 ? (WORD)(JAG_BG_H - 1) : act_y1;
        LONG cx = ((LONG)act_x0 + act_x1) >> 1;
        LONG x0 = cx - JAG_SPR_VIS_W / 2;

        if (x0 < 0)
            x0 = 0;
        if (x0 > JAG_BG_W - JAG_SPR_VIS_W)
            x0 = JAG_BG_W - JAG_SPR_VIS_W;
        /* x0 a multipli di 8: il DATA di un BMobj e' un indirizzo di PHRASE.
         * y0 PARI: il campo lo decide la parita' della riga, e spostare lo
         * sprite di una riga sola scambierebbe i due campi - cioe' lo
         * farebbe tremolare a 30 Hz mentre il fondo sta fermo. */
        x0 &= ~7L;
        /* IL MARGINE IN TESTA, che il bias del campo pari si mangia al posto
         * della prima riga dell'attore. Sul ferro ViHandler fa partire il campo
         * A due righe piu' avanti nel buffer (FIELD_A_ROW_BIAS, la stessa
         * correzione che il fondo porta da S27), quindi le prime righe del
         * buffer non si vedono: se lo lasciassimo attaccato all'attore, gli si
         * taglierebbe la cima della testa.
         *
         * Si riserva SEMPRE, anche a bias zero: sono due righe trasparenti e
         * non si vedono, e cosi' la costante del bersaglio resta di la' invece
         * di attraversare la mappa solo per questo. */
        y0 = (WORD)(y0 - SPR_TOP_MARGIN);
        if (y0 < 0)
            y0 = 0;
        y0 = (WORD)(y0 & ~1);

        spr_want_x0 = (WORD)x0;
        /* Il limite e' l'altezza del buffer MENO il margine: il campo A legge
         * a partire dalla riga SPR_TOP_MARGIN, quindi l'ultima riga toccata e'
         * il margine piu' le righe del riquadro. Senza sottrarlo, un attore
         * alto quanto il buffer farebbe leggere all'OP una riga oltre. */
        if (y1 - y0 < JAG_SPR_ROWS - SPR_TOP_MARGIN)
            spr_why |= SPR_WHY_ROWS;
        if (act_x0 >= (WORD)x0 && act_x1 < (WORD)(x0 + JAG_SPR_VIS_W))
            spr_why |= SPR_WHY_COLS;

        /* LA DECISIONE LEGGE LE STESSE CONDIZIONI CHE HA APPENA REGISTRATO,
         * invece di riscriverle. Erano duplicate, e la duplicazione si e' fatta
         * viva subito: stringendo il limite in altezza per il margine, la copia
         * qui sotto e' rimasta indietro - la sonda diceva "non ci sta" mentre
         * il codice lo accettava lo stesso. Cosi' non possono piu' divergere. */
        if ((spr_why & (SPR_WHY_ROWS | SPR_WHY_COLS))
            == (SPR_WHY_ROWS | SPR_WHY_COLS)) {
            spr_x0 = (WORD)x0;
            spr_y0 = y0;
            spr_y1 = y1;
            spr_on = 1;
            spr_why |= SPR_WHY_ON;
            /* LA BASE VIRTUALE: il kernel aggiungera' y*640, e y non scende
             * mai sotto spr_y0 (e' il riquadro che l'ha appena prodotto). */
            body_fb_base = (UBYTE *)SPR_BANK_BASE
                           - (LONG)y0 * JAG_SPR_PITCH;
            spr_clear();
        }
    }
#endif

    /* La sonda fuori dal ciclo caldo, come per lo shade: il record di schermo
     * impacchettato E' il valore che la sonda GEOM sommava, nello stesso
     * ordine. La GPU non deve calcolare somme di Fletcher per farsi
     * verificare. */
    for (i = 0; i < b->nb_points; i++)
        fletch(BodyStat.geom, (ULONG)wscr[i * 2]);

    t1 = BODY_CLOCK; BodyStat.t_stage[BT_VERT] += t1 - t0; t0 = t1;

    /* --- intensita': una per normale, P_OB_ISO.ASM:1687 ---
     * La divisione e' intera e il negativo diventa zero PRIMA di dividere
     * (`or dx,dx / js nointensity`): una faccia che guarda dall'altra parte
     * non e' un'intensita' da ribaltare, e' buio. */
    /* ANCHE LE NORMALI SONO RAGGRUPPATE (ComputeAnimNormal, P_OB_ISO.ASM:1436).
     * Il conteggio sta a +18 nel record del gruppo, si consumano in ordine, e
     * ognuna si ruota con la matrice del PROPRIO gruppo. Verificato
     * sull'archivio: la somma dei conteggi da' il totale su tutti e 132 i corpi.
     *
     * Finche' la posa e' quella a riposo le matrici sono tutte uguali e questo
     * ciclo da' quel che dava prima. Con un'animazione no: un braccio alzato
     * resterebbe illuminato come se non si fosse mosso. */
    {
        int ns = shade_spans(b, shade_span_buf);

#if BODY_SHADE_ON_GPU
        shade_gpu(norm_native(o->Body, b), b->nb_normals, wmat,
                  shade_span_buf, ns, light, wint);
#else
        shade_stage(norm_native(o->Body, b), b->nb_normals, wmat,
                    shade_span_buf, ns, light, wint);
#endif
    }

    /* LA SONDA FUORI DAL CICLO CALDO, e si puo' dimostrare che e' la stessa.
     *
     * Fino a S38 `fletch` stava dentro, e la sequenza che sommava era wint[0],
     * wint[1], ... nell'ordine in cui il ciclo scriveva - cioe' `i` crescente,
     * sempre, perche' `i++` e' l'unico modo in cui si muove. E ogni indice da 0
     * a nb_normals-1 viene scritto esattamente una volta: la coda copre per
     * costruzione quel che i conteggi non coprono. Quindi una passata separata
     * su wint[0..nb_normals-1] somma la STESSA sequenza nello STESSO ordine.
     *
     * Non e' una micro-ottimizzazione: la GPU non deve calcolare una somma di
     * Fletcher per farsi verificare. Il 68000 la rifa' dopo, su un array che ha
     * gia' in mano, e le sonde continuano a valere quando lo stadio traslochera'
     * davvero. */
    for (i = 0; i < b->nb_normals; i++)
        fletch(BodyStat.shade, (ULONG)wint[i]);

    t1 = BODY_CLOCK; BodyStat.t_stage[BT_SHADE] += t1 - t0; t0 = t1;

    /* --- faccia nascosta PRIMA dell'ordinamento, come nel gioco: chi non
     * passa `testpoly` non entra mai nella lista che il quicksort ordinera'.
     * Sull'insieme dei sopravvissuti l'ordine e' lo stesso in un verso e
     * nell'altro, ma cosi' si ordina la meta' delle voci.
     *
     * La chiave e' il MINIMO di X+Y+Z sugli angoli. Il gioco tiene per punto
     * `-(X+Y+Z)` e per poligono il MASSIMO, poi ordina decrescente (permut2
     * scambia quando `[esi] < [ebp]`): massimo di -(X+Y+Z) decrescente =
     * minimo di X+Y+Z crescente. Il gioco lo tiene in una WORD e qui e' un
     * LONG: X+Y+Z arriva a 56644 sulle nostre stanze, quindi il DOS lo tronca
     * e un attore i cui poligoni cadono a cavallo di 32767 li ordinerebbe a
     * rovescio - un difetto del gioco, non una convenzione da riprodurre.
     *
     * Da S45 il ciclo sta in cull_stage (o nella fase C della GPU, che in quel
     * caso ha gia' consegnato `live` col lancio della fase V). --- */
#if BODY_FRONT_ON_GPU
    live = (int)front_live;
#else
    live = (int)cull_stage(b->polys, b->nb_polys, b->corners, b->nb_points);
#endif

    /* --- LE DUE SEZIONI IN CODA AI POLIGONI: E_LIGNE ed E_SPHERE.
     *
     * Nel blob JBD sono due array a passo fisso dichiarati in intestazione:
     * niente piu' flusso da percorrere per trovarle.
     *
     * ENTRANO NELLA STESSA LISTA DEI POLIGONI, ed e' il punto di tutta questa
     * voce: `TotalEntite` le conta insieme e `PointeurListTri` e' lo stesso
     * puntatore, quindi si ordinano insieme. In una passata a parte finirebbero
     * tutte davanti o tutte dietro - cioe' le mani di Twinsen sopra la tunica.
     *
     * Non passano dalla faccia nascosta: `testpoly` prova tre angoli
     * proiettati, e una linea ne ha due e una sfera uno. La word ALTA della
     * coppia in wlist e' l'indice GLOBALE nell'ordine in cui il gioco riempie
     * List_Tri (prima i poligoni, poi le linee, poi le sfere), che e' anche
     * quel che rompe i pareggi; la BASSA e' l'indice DENTRO la propria
     * sezione, per tutte e tre. */
    nb_line = b->nb_lines;
    nb_sph  = b->nb_spheres;

    /* Sforare vorrebbe dire ordinare oltre la fine di wlist, cioe' scrivere
     * dentro wmat. Il massimo misurato su tutto BODY.HQR e' 319 entita'
     * (body 94), contro le 384 dichiarate. */
    if ((ULONG)live + nb_line + nb_sph > MAXP)
        TheEnd(TE_BODY_BIG, "entita' oltre BODY_WORK");

    for (i = 0; i < nb_line; i++) {
        const be_byte *e = b->lines + (long)i * 8;
        UWORD v0 = BE16u(e + 4);
        UWORD v1 = BE16u(e + 6);
        LONG k0, k1;

        if (v0 >= b->nb_points) v0 = 0;
        if (v1 >= b->nb_points) v1 = 0;
        /* Il gioco tiene `-(X+Y+Z)` e prende il MASSIMO fra i due estremi
         * (P_OB_ISO.ASM:584, `jl zok0`): e' il minimo di X+Y+Z. */
        k0 = wscr[v0 * 2 + 1]; k1 = wscr[v1 * 2 + 1];
        wlist[live * 2]     = (k0 < k1) ? k0 : k1;
        wlist[live * 2 + 1] = ((LONG)(b->nb_polys + i) << 16) | i;
        live++;
    }
    for (i = 0; i < nb_sph; i++) {
        const be_byte *e = b->spheres + (long)i * 8;
        UWORD v = BE16u(e + 6);

        if (v >= b->nb_points) v = 0;
        /* La chiave di una sfera e' il valore del suo UNICO punto, riga 624. */
        wlist[live * 2]     = wscr[v * 2 + 1];
        wlist[live * 2 + 1] = ((LONG)(b->nb_polys + nb_line + i) << 16) | i;
        live++;
    }
    BodyStat.lines   = (UWORD)(BodyStat.lines + nb_line);
    BodyStat.spheres = (UWORD)(BodyStat.spheres + nb_sph);

    BodyStat.tot += b->nb_polys + nb_line + nb_sph;   /* TotalEntite */
    BodyStat.live += live;
    t1 = BODY_CLOCK; BodyStat.t_stage[BT_CULL] += t1 - t0; t0 = t1;

    /* --- ordinamento: chiave crescente, poi indice.
     *
     * L'indice non e' cosmesi. Il gioco ordina con un quicksort che stabile non
     * e', e i pareggi ci sono eccome perche' la chiave e' il minimo su un
     * vertice CONDIVISO fra due facce. Aggiungere l'indice rompe i pareggi
     * sempre allo stesso modo, qui e nel riferimento: e' l'unico punto in cui
     * questa pipeline non puo' essere identica al gioco, perche' il gioco non
     * e' identico a se' stesso.
     *
     * Shell sort: 300 voci con l'inserzione semplice sarebbero 45000 confronti
     * per attore sul 68000. Le distanze sono la successione di Knuth. --- */
    {
        int gap = 1, j;
        int warm = sort_seed(o, b->nb_polys, nb_line, nb_sph, live);

        /* IL SEME (S50), e cambia UNA riga: da dove parte il gap.
         *
         * Misurato sul ferro: BT_SORT e' 2,40 tick a passata, il 25-30% del
         * quadro, sia con l'attore-sprite sia senza - non dipende da lui. Ed e'
         * gia' al limite: 7,6 confronti per voce con n=128, contro il minimo
         * teorico log2(128)=7,0 di QUALUNQUE ordinamento a confronti. Non c'e'
         * niente da guadagnare cambiando algoritmo (i secchielli danno 1,8x, e
         * solo perche' le chiavi sono ammassate).
         *
         * Quel che si guadagna e' cambiare il PUNTO DI PARTENZA. La lista in
         * ingresso e' praticamente casuale - 40,4 inversioni per voce - ma
         * l'USCITA del quadro prima e' quasi ordinata per quello dopo: 0,5
         * inversioni per voce, misurate facendo avanzare l'animazione un
         * fotogramma CHIAVE alla volta, che e' il salto piu' grande che
         * l'animazione faccia (tools/sort_scan.py --coherence). Con quel seme
         * l'inserzione semplice batte Shell di 3,3x, code delle voci nuove
         * compresa.
         *
         * PERCHE' E' SICURO, e non serve nessuna dimostrazione di sovrainsieme
         * come per l'indice per colonna: l'ordine (chiave, indice) e' TOTALE,
         * quindi il risultato e' unico e il seme puo' cambiare solo il COSTO.
         * Da qui anche il fatto che non serve invalidare niente: un seme stale
         * produce una lista comunque ordinata, solo piu' lentamente.
         *
         * E il ciclo qui sotto NON si tocca. Freddo: le distanze di Knuth come
         * sempre. Caldo: si parte da gap 1, cioe' l'inserzione semplice, che e'
         * lo stesso codice con la sequenza di distanze piu' corta possibile.
         * Due strade non ci sono, quindi non possono divergere. */
        if (!warm)
            while (gap < live / 3)
                gap = gap * 3 + 1;
        for (; gap >= 1; gap /= 3) {
            for (k = gap; k < live; k++) {
                LONG kk = wlist[k * 2];
                LONG ki = wlist[k * 2 + 1];
                j = k;
                /* Il pareggio si rompe sull'indice GLOBALE, che sta nella
                 * word alta della coppia: confrontare il long impacchettato
                 * senza segno E' confrontare widx, perche' l'indice domina e
                 * due voci non hanno mai lo stesso indice. */
                while (j >= gap
                       && (wlist[(j - gap) * 2] > kk
                           || (wlist[(j - gap) * 2] == kk
                               && (ULONG)wlist[(j - gap) * 2 + 1] > (ULONG)ki))) {
                    wlist[j * 2]     = wlist[(j - gap) * 2];
                    wlist[j * 2 + 1] = wlist[(j - gap) * 2 + 1];
                    j -= gap;
                }
                wlist[j * 2]     = kk;
                wlist[j * 2 + 1] = ki;
            }
        }
    }
    sort_seed_keep(o, live);

    for (k = 0; k < live; k++)
        fletch(BodyStat.order,
               (((ULONG)wlist[k * 2] & 0xFFFFUL) << 16)
               | (UWORD)((ULONG)wlist[k * 2 + 1] >> 16));

    t1 = BODY_CLOCK; BodyStat.t_stage[BT_SORT] += t1 - t0; t0 = t1;
    t0_gpu = BodyStat.t_stage[BT_GPU];
    t0_over = BodyStat.t_over;
    t0_cls = BgStat.t_cls;

    /* --- disegno --- */
    actor_bytes = 0;
    run_bytes = 0;
#if BODY_EMIT_ON_GPU
    /* S46: il 68000 cammina la lista SOLO per le entita'. Ogni voce entita'
     * diventa un BLOCCO nel flusso - le sue tratte, poi un TERMINATORE a
     * zero - nello stesso ordine in cui il walker della GPU la incontrera':
     * la corrispondenza voce-blocco e' posizionale, quindi il terminatore si
     * scrive SEMPRE, anche quando le tratte sono zero (linea tutta fuori
     * dallo schermo). Il terminatore e non un conteggio in testa: cosi' il
     * blocco lo consuma il ciclo del flusso che il kernel ha gia', senza un
     * secondo ciclo da scrivere e da provare. I poligoni non passano di
     * qui: li legge la GPU da sola, da wlist e dal corpo. */
    for (k = 0; k < live; k++) {
        UWORD gi  = (UWORD)((ULONG)wlist[k * 2 + 1] >> 16);
        UWORD off = (UWORD)wlist[k * 2 + 1];

        if (gi < b->nb_polys)
            continue;
        draw_entity(b, gi, off, nb_line,
                    &act_x0, &act_y0, &act_x1, &act_y1);
        job_buf[job_used++] = 0;    /* il terminatore del blocco */
        actor_bytes += 4;
    }

#if BODY_FILL_ON_GPU
    list_gpu(b, live);
#else
    /* Il modello del walker, nella stessa finestra di misura del lancio. */
    {
        ULONG t0g = BODY_CLOCK;

        if (live > 0) {
            Bg_ClsPending();
            BodyStat.launches++;
            list_run_c(b, live);
        }
        job_used = 0;
        job_count = 0;
        job_runs = 0;
        BodyStat.t_stage[BT_GPU] += BODY_CLOCK - t0g;
    }
#endif
#else /* !BODY_EMIT_ON_GPU: il percorso S34-S45, poligoni imboccati nel flusso */
    for (k = 0; k < live; k++) {
        T_POLY q;
        WORD xs[JAG_BODY_MAX_CORNERS], ys[JAG_BODY_MAX_CORNERS];
        WORD sh[JAG_BODY_MAX_CORNERS];
        int minx, maxx, miny, maxy;
        int n;
        UWORD gi  = (UWORD)((ULONG)wlist[k * 2 + 1] >> 16);
        UWORD off = (UWORD)wlist[k * 2 + 1];

        /* Una LINEA o una SFERA: il ramo condiviso coi blocchi del modo
         * lista, vedi draw_entity. */
        if (gi >= b->nb_polys) {
            draw_entity(b, gi, off, nb_line,
                        &act_x0, &act_y0, &act_x1, &act_y1);
            continue;
        }

        poly_at(b, off, &q);

        n = q.nbp;
        if (n > JAG_BODY_MAX_CORNERS)
            n = JAG_BODY_MAX_CORNERS;
        minx = miny = 0x7FFF;
        maxx = maxy = -0x7FFF;
        for (i = 0; i < (UWORD)n; i++) {
            UWORD v = corner_vert(&q, i);
            if (v >= b->nb_points)
                v = 0;
            xs[i] = SCRX(v);
            ys[i] = SCRY(v);
            if (xs[i] < minx) minx = xs[i];
            if (xs[i] > maxx) maxx = xs[i];
            if (ys[i] < miny) miny = ys[i];
            if (ys[i] > maxy) maxy = ys[i];
        }
        if (maxx < 0 || minx >= JAG_BG_W || maxy < 0 || miny >= JAG_BG_H)
            continue;

        if (q.mat >= MAT_GOURAUD_MIN) {
            for (i = 0; i < (UWORD)n; i++) {
                UWORD ni = corner_normal(&q, i);
                sh[i] = shade_index(q.coul1,
                                    ni < b->nb_normals ? wint[ni] : 0);
            }
        } else {
            /* Il secchio "motivo" (materiali 0..6) non ha normale di faccia:
             * `flat_normal` resta 0 e il colore e' coul1 nudo, che e' anche
             * quel che il gioco stocca per quel secchio (P_OB_ISO.ASM:337).
             * Resta un ripiego DICHIARATO sul motivo vero e proprio: coul2 e
             * la trama non si disegnano. */
            WORD raw = 0;
            if (q.mat >= MAT_FLAT_MIN && q.flat_normal < b->nb_normals)
                raw = wint[q.flat_normal];
            for (i = 0; i < (UWORD)n; i++)
                sh[i] = shade_index(q.coul1, raw);
        }

        actor_bytes += emit_poly(xs, ys, sh, n, miny, maxy);
    }

    /* IL LANCIO. Uno per attore, non uno per poligono - salvo che il flusso si
     * sia gia' dovuto svuotare per pienezza, e allora sono due o tre. */
    jobs_flush();
#endif /* !BODY_EMIT_ON_GPU */

    /* --- M3d: LE TESSERE DAVANTI, SOPRA DI LUI.
     *
     * Va QUI e non dopo il ciclo degli attori: il gioco lo fa attore per attore
     * (OBJECT.C:3023), subito dopo averlo disegnato. Farlo alla fine
     * rimetterebbe le tessere sopra TUTTI gli attori, e quello che si vedrebbe
     * non e' "un personaggio dietro un muro" ma un personaggio dietro il muro
     * di un altro.
     *
     * Il rettangolo si limita allo schermo come SetClip (INITSVGA.ASM:266): i
     * due minimi non scendono sotto zero e i due massimi non salgono sopra il
     * buffer. Se dopo il taglio e' vuoto, il gioco non chiama DrawOverBrick -
     * ed e' lo stesso test `ClipXmin <= ClipXmax` di OBJECT.C:3015. */
    {
        WORD cx0 = act_x0 < 0 ? 0 : act_x0;
        WORD cy0 = act_y0 < 0 ? 0 : act_y0;
        WORD cx1 = act_x1 > JAG_BG_W - 1 ? (WORD)(JAG_BG_W - 1) : act_x1;
        WORD cy1 = act_y1 > JAG_BG_H - 1 ? (WORD)(JAG_BG_H - 1) : act_y1;
        ULONG t_ov0 = BODY_CLOCK;
        ULONG t_ovcls = BgStat.t_cls;   /* il ripristino puo' scattare qui dentro */

#if BODY_ACTOR_SPRITE
        /* ED ECCO IL SASSO. Un attore che vive nel buffer suo non ha sporcato
         * il fondo: non c'e' NIENTE DA RIPRISTINARE (niente Bg_AddBox, cioe' i
         * 4,50 tick spariscono per intero) e niente da ricoprire NEL FONDO.
         *
         * Le tessere che gli passano davanti pero' vanno ricoperte lo stesso, o
         * l'eroe cammina DAVANTI ai muri - e si vede al primo passo. Si
         * ricoprono dentro il buffer, con le stesse tessere, lo stesso ordine e
         * lo stesso ritaglio: cambia solo dove il Blitter scrive. Quindi M3d si
         * paga ancora, ed e' onesto dirlo - quel che questa strada compra per
         * intero e' il ripristino, non tutti e due. */
        if (spr_on) {
            if (cx0 <= cx1 && cy0 <= cy1) {
                WORD xm = (WORD)((o->PosObjX + DEMI_BRICK_XZ) / SIZE_BRICK_XZ);
                WORD ym = (WORD)(o->PosObjY / SIZE_BRICK_Y);
                WORD zm = (WORD)((o->PosObjZ + DEMI_BRICK_XZ) / SIZE_BRICK_XZ);

                if (o->Col & 127)
                    ym++;
                /* La stessa base virtuale che il kernel GPU ha appena usato:
                 * il buffer e' largo 640 come il fondo, quindi il rettangolo
                 * di ritaglio non va traslato di un pixel. */
                Bg_DrawOverBrickSpr(xm, ym, zm, cx0, cy0, cx1, cy1,
                                    SPR_BANK_BASE
                                    - (ULONG)((LONG)spr_y0 * JAG_SPR_PITCH));
            }
            /* Niente Bg_AddBox: e' esattamente quel che non si paga piu'. E
             * niente sottrazione di t_cls, perche' Bg_ClsPending qui dentro non
             * puo' scattare - questa passata non scrive nel framebuffer. */
            BodyStat.t_over += BODY_CLOCK - t_ov0;
        } else
#endif
        {
        if (cx0 <= cx1 && cy0 <= cy1) {
            WORD xm = (WORD)((o->PosObjX + DEMI_BRICK_XZ) / SIZE_BRICK_XZ);
            WORD ym = (WORD)(o->PosObjY / SIZE_BRICK_Y);
            WORD zm = (WORD)((o->PosObjZ + DEMI_BRICK_XZ) / SIZE_BRICK_XZ);

            if (o->Col & 127)
                ym++;
            Bg_DrawOverBrick(xm, ym, zm, cx0, cy0, cx1, cy1);
        }
        /* AddPhysBox (OBJECT.C:3025), che nel gioco e' la riga ACCANTO a
         * DrawOverBrick e prende lo stesso identico rettangolo: quel che
         * l'attore ha sporcato e' quel che il prossimo quadro dovra' rifare.
         * Fuori dall'`if` perche' un rettangolo vuoto lo scarta Bg_AddBox, e
         * perche' cosi' la lista non dipende dalla condizione di M3d. */
        Bg_AddBox(cx0, cy0, cx1, cy1);
        /* Il tempo di M3d, che fino a S38 finiva dentro BT_EMIT e lo faceva
         * sembrare tutto guadagnabile dal trasloco su GPU. Si accumula qui e si
         * sottrae sotto, esattamente come si fa gia' con la GPU: la finestra di
         * BT_EMIT resta quella, cambia solo cosa le si attribuisce.
         *
         * Il clock si legge anche quando il rettangolo e' vuoto: cosi' la riga
         * D vale zero per costruzione se DrawOverBrick non e' mai stato
         * chiamato, invece di valere "non misurato". */
        BodyStat.t_over += (BODY_CLOCK - t_ov0)
                           - (BgStat.t_cls - t_ovcls);
        }
    }

#if BODY_ACTOR_SPRITE
    /* LE DUE PHRASE PER L'OP. Le compone il C perche' e' il C a sapere dove
     * sta l'attore; il gestore di interrupt le ricopia e basta, che dentro
     * un'interruzione e' l'unica cosa che si deve fare.
     *
     * HEIGHT e' in righe PER CAMPO (meta' delle righe vere, come il fondo:
     * l'OP con DWIDTH doppio ne salta una a ogni linea disegnata) e YPOS e'
     * una SOGLIA in mezze righe, cioe' VDB + la riga di framebuffer.
     * A HEIGHT zero l'oggetto e' spento, ed e' cosi' che l'attore fuori campo
     * sparisce senza toccare la catena della lista. */
    if (spr_on) {
        /* LO SCAMBIO DEI BANCHI E' QUESTA RIGA. Fino a un'istruzione fa l'OP
         * stava componendo il banco di prima; da qui in poi compone quello che
         * abbiamo appena finito di disegnare, e nessuno dei due e' mai stato
         * incompleto mentre veniva mostrato. */
        ULONG data = SPR_BANK_BASE + (ULONG)spr_x0;
        ULONG rows = (ULONG)(spr_y1 - spr_y0 + 1) >> 1;
        ULONG link = (JAG_OP_TAIL_STOP >> 3) & 0xFFUL;

        /* SENZA HW_L. I nomi JAG_HAL_* della mappa sono GIA' l'lvalue
         * (`*(volatile unsigned long *)(HAL_VARS+off)`), non l'indirizzo:
         * avvolgerli in HW_L dereferenzia DUE VOLTE, cioe' scrive all'indirizzo
         * che sta nel campo invece che nel campo. Con i valori di InitOpList
         * quelle scritture finivano in $135000 (il buffer stesso) e - dopo il
         * troncamento a 24 bit del 68000 - dentro la tabella dei vettori.
         * E' questo il motivo per cui in S47 le due phrase "non si
         * accendevano": il C non le ha mai scritte. */
        JAG_HAL_SPR_DATA_A   = data;
        JAG_HAL_SPR_DATA_B   = data + JAG_SPR_PITCH;
        JAG_HAL_OP_SPR_PH0LO =
            ((ULONG)(JAG_HAL_VDB_VAL + spr_y0) << 3)
            | (rows << 14) | (link << 24);
        JAG_HAL_OP_SPR_PH1LO =
            (ULONG)spr_x0
            | (3UL << 12)                       /* 8 bit per pixel      */
            | (1UL << 15)                       /* PITCH: dati contigui */
            | (((ULONG)(JAG_SPR_PITCH * 2) / 8) << 18)   /* DWIDTH       */
            | ((((ULONG)JAG_SPR_VIS_W / 8) & 0xFUL) << 28);
        /* E il prossimo giro si disegna nell'ALTRO, cioe' non in quello che
         * l'OP ha appena cominciato a mostrare. Si gira solo quando si e'
         * disegnato davvero: se l'attore e' tornato nel fondo, il banco buono
         * resta quello e non c'e' niente da alternare. */
        spr_bank ^= 1;
    }
    body_fb_base = (UBYTE *)BODY_FB_BASE;
#endif

    /* Il tempo dei bordi e' quel che resta TOLTA la GPU, e la sottrazione non e'
     * pignoleria: se il flusso si riempie a meta' ciclo, jobs_emit lancia da
     * dentro il ciclo e quel tempo cadrebbe dentro tutti e due gli stadi. Un
     * costo contato due volte fa sembrare caro lo stadio sbagliato, che e'
     * esattamente il modo in cui una misura fa perdere tempo invece di farne
     * risparmiare. jobs_flush accumula da se' in BT_GPU. */
    t1 = BODY_CLOCK;
    BodyStat.t_stage[BT_EMIT] += (t1 - t0)
                                 - (BodyStat.t_stage[BT_GPU] - t0_gpu)
                                 - (BodyStat.t_over - t0_over)
                                 - (BgStat.t_cls - t0_cls);

    actor_bytes += run_bytes;
    BodyStat.batchtot += actor_bytes;
    if (actor_bytes > BodyStat.batchmax)
        BodyStat.batchmax = actor_bytes;
}


/* ---------------------------------------------------------------------------
 * Tutti gli attori della stanza, nell'ordine di ListObjet[].
 *
 * L'ordine fra attori e' quello, non uno ordinato per profondita': il gioco
 * chiama AffObjetIso una volta per attore mentre percorre la lista, e ogni
 * chiamata ordina solo i poligoni DEL SUO corpo. Due attori che si
 * sovrappongono si coprono quindi per indice, non per distanza - ed e' un
 * comportamento del gioco, non una semplificazione nostra.
 * ------------------------------------------------------------------------- */
void Body_ProfileReset(void)
{
    int i;

    for (i = 0; i < BODY_TSTAGES; i++)
        BodyStat.t_stage[i] = 0;
    BodyStat.t_reps = 0;
    BodyStat.t_over = 0;
    BodyStat.t_edges = 0;
    BodyStat.t_stream = 0;
    BgStat.t_cls = 0;
}


/* ---------------------------------------------------------------------------
 * L'OROLOGIO (S41): il fotogramma avanza da se'.
 *
 * Fin qui la posa era una funzione del PASSO - `set_step_pose` scriveva
 * `Frame`, `AnimSrcFrame` e `AnimMemoTicks` e nessuno li muoveva piu'. Questa
 * funzione e' l'altra meta': il tempo che passa fa arrivare il fotogramma e ne
 * comincia uno nuovo. Fra le due, `pose_load` non cambia di una riga - la posa
 * era gia' una funzione del trascorso, mancava solo qualcuno che lo lasciasse
 * crescere.
 *
 * DA DOVE VIENE, RIGA PER RIGA
 *   P_ANIM.C:627-641  `SetInterDepObjet` - trascorso >= durata: la SORGENTE
 *                     diventa il fotogramma raggiunto (`state+0`) e l'orologio
 *                     riparte da adesso (`state+4 = TimerRef`), poi torna 1;
 *   OBJECT.C:1734-1780 il chiamante, che sul suo 1 fa `Frame++` e, arrivato in
 *                     fondo, riparte da `GetBouclageAnim` (P_ANIM.C:254, la
 *                     word a +4 dell'archivio).
 *
 * L'ORDINE E' QUELLO DEL GIOCO: avanzare PRIMA di disegnare. Sul fotogramma
 * appena arrivato il trascorso vale zero, quindi `pose_load` interpola dello
 * 0% verso il nuovo arrivo e mostra esattamente la posa raggiunta. Invertire
 * l'ordine darebbe la stessa animazione in ritardo di un quadro, ma con la
 * sorgente e la destinazione scambiate per un quadro su N - cioe' uno scatto.
 *
 * S42: DA "OROLOGIO" A `DoAnim` INTERA (OBJECT.C:1700-1790). Il nome resta
 * perche' i chiamanti sono gli stessi, ma adesso la funzione fa le tre cose che
 * upstream fa in quel punto, e le fa perche' NON SONO SEPARABILI:
 *
 *   1. il fotogramma avanza (quel che c'era);
 *   2. la ROTAZIONE MAESTRA: `Beta += AnimStepBeta - o->AnimStepBeta`. E' la
 *      rotazione che sta DENTRO l'animazione, ed e' l'unica cosa che fa girare
 *      Twinsen quando gira sul posto - li' il pad non scrive `Beta`, lancia
 *      l'animazione di giro e lascia ruotare lei;
 *   3. lo SPOSTAMENTO della radice: il passo dell'animazione, ruotato nel
 *      sistema del mondo e sommato alla posizione. E' quel che sposta l'eroe
 *      davvero, e viene dai dati - la lunghezza del passo la decide ANIM.HQR,
 *      non una velocita' scelta da noi.
 *
 * I tre valori escono tutti da `SetInterDepObjet` (P_ANIM.C:588-667), che li
 * legge dallo stesso fotogramma in due forme: INTERI quando il fotogramma e'
 * arrivato, in PROPORZIONE al trascorso mentre ci si va. La differenza col
 * valore accumulato (`o->AnimStep*`) e' l'incremento di QUESTO quadro, ed e' il
 * motivo per cui il passo resta lo stesso a 2 fps come a 30: e' il tempo a
 * dividerlo, non il numero di quadri.
 *
 * QUEL CHE ANCORA NON C'E': la COLLISIONE. `Nxw/Nyw/Nzw` diventano la posizione
 * senza passare da `CheckBrickCol` (GRILLE.C), quindi Twinsen cammina
 * attraverso i muri. Resta il solo argine di upstream, la "protection sortie du
 * cube" (OBJECT.C:2061-2072), che tiene la posizione dentro la griglia 64x64:
 * senza, si esce dal cubo e si legge una cella che non c'e'.
 * ------------------------------------------------------------------------- */
/* ---------------------------------------------------------------------------
 * S51: `SetInterDepObjet` (P_ANIM.C:595), with the state in the ACTOR.
 *
 * The fork's DoAnim calls it with the body; the Jaguar keeps the animation
 * state in T_OBJET (AnimSrcFrame, AnimMemoTicks, PtrPoseSrc - hal.h), so the
 * fork's call goes through engine_c/port/jag_anim.c to this. It was the first
 * half of Body_AnimTick, cut out whole: the bench (host_body.c --clock) now
 * exercises the very function the game calls.
 *
 * Returns -1 when the actor has no usable animation this frame (the fork
 * checks the same things before calling), 0 when the current frame is not
 * reached yet, 1 when it is - and then, like the original, records the
 * reached frame and the time as the new interpolation source.
 * ------------------------------------------------------------------------- */
int Body_InterDep(T_OBJET *o, T_ANIMSTEP *st)
{
    const le_byte *anim, *fd;
    UWORD nbf, nba, stride;
    ULONG elapsed;
    WORD tot, cur;
    WORD msrot, stepbeta, stepx, stepy, stepz;
    int arrived;

    anim = (const le_byte *)HQR_Get(HQR_ANIMS, o->Anim);
    if (!anim)
        return -1;
    nbf = LE16u(anim);
    nba = LE16u(anim + 2);
    if (nbf == 0 || nba == 0 || (UWORD)o->Frame >= nbf)
        return -1;

    stride = (UWORD)(nba * 8 + 8);
    fd = anim + 8 + (long)stride * (UWORD)o->Frame;
    tot = LE16(fd);

    /* Lo STESSO confronto a 32 bit di pose_load, e per lo stesso motivo:
     * ridurre il trascorso a WORD prima di confrontarlo lo fa diventare
     * negativo dopo 32767 tick di posa ferma. Qui il rischio e' maggiore
     * che li', perche' una posa ferma per dieci minuti la produce
     * l'orologio, non il banco. */
    elapsed = TimerRef - o->AnimMemoTicks;
    arrived = (elapsed >= (ULONG)tot);

    /* L'intestazione del fotogramma e il record del gruppo 0:
     *   +0 durata   +2 X  +4 Y  +6 Z
     *   +8 rotazione maestra  +10 alpha  +12 beta  +14 gamma
     * La rotazione maestra e' una BANDIERA e non si scala mai; gli altri
     * sei si prendono interi se il fotogramma e' arrivato, in proporzione
     * altrimenti (P_ANIM.C:640-666). */
    msrot = LE16(fd + 8);

    if (arrived) {
        stepx    = LE16(fd + 2);
        stepy    = LE16(fd + 4);
        stepz    = LE16(fd + 6);
        stepbeta = LE16(fd + 12);
    } else {
        WORD t = tot ? tot : 1;

        cur      = (WORD)elapsed;
        stepbeta = (WORD)(((LONG)LE16(fd + 12) * cur) / t);
        stepx    = (WORD)(((LONG)LE16(fd + 2) * cur) / t);
        stepy    = (WORD)(((LONG)LE16(fd + 4) * cur) / t);
        stepz    = (WORD)(((LONG)LE16(fd + 6) * cur) / t);
    }


    if (arrived) {
        o->AnimSrcFrame = o->Frame;
        o->AnimMemoTicks = TimerRef;
    }
    st->anim  = anim;
    st->nbf   = nbf;
    st->msrot = msrot;
    st->beta  = stepbeta;
    st->x     = stepx;
    st->y     = stepy;
    st->z     = stepz;
    return arrived;
}


void Body_AnimTick(void)
{
    WORD n;

    for (n = 0; n < NbObjets; n++) {
        T_OBJET *o = &ListObjet[n];
        const le_byte *anim;
        UWORD nbf;
        WORD msrot, stepbeta, stepx, stepy, stepz;
        LONG nxw, nyw, nzw;
        int arrived;

        /* PERSO.C:934, fra DoDir e DoAnim: la posizione di PRIMA di questo
         * quadro. Non la legge ancora nessuno - la vuole la collisione, che
         * confronta la cella vecchia con la nuova - ma sta qui perche' qui e'
         * il punto in cui il gioco la scrive, e per tutti gli attori. */
        o->OldPosX = o->PosObjX;
        o->OldPosY = o->PosObjY;
        o->OldPosZ = o->PosObjZ;

        /* `DoAnim` esce SUBITO su un attore senza corpo (OBJECT.C:1548), e non
         * e' una guardia difensiva: senza, un attore che ha un'animazione ma
         * nessun corpo - ne esiste uno per stanza, mai disegnato - si prende lo
         * spostamento della radice e se ne va per i fatti suoi. Non si
         * vedrebbe (non ha corpo), ma la sua posizione e' quella che le zone e
         * gli script leggeranno a M5. Trovato dal banco su PC al primo colpo,
         * con `--clock`. */
        if (o->Body == -1)
            continue;

        if (o->Anim < 0)
            continue;
        /* Gli SPRITE_3D (le porte) prendono l'altro ramo di DoAnim, che li
         * muove con RealAngle e non con l'animazione: e' M5. */
        if (o->Flags & SPRITE_3D)
            continue;
        {
            T_ANIMSTEP st;
            int r = Body_InterDep(o, &st);

            if (r < 0)
                continue;
            arrived  = r;
            anim     = st.anim;
            nbf      = st.nbf;
            msrot    = st.msrot;
            stepbeta = st.beta;
            stepx    = st.x;
            stepy    = st.y;
            stepz    = st.z;
        }

        if (msrot)
            o->WorkFlags |= ANIM_MASTER_ROT;
        else
            o->WorkFlags &= (WORD)~ANIM_MASTER_ROT;

        o->Beta = (WORD)((o->Beta + stepbeta - o->AnimStepBeta) & LBA_ANGLE_MASK);
        o->AnimStepBeta = stepbeta;

        /* Il passo e' nel sistema del PERSONAGGIO: "avanti" e' avanti per lui.
         * Ruotarlo di Beta e' quel che lo rende una direzione nel mondo. */
        rotate_xz(stepx, stepz, o->Beta);
        stepx = (WORD)rot_X0;
        stepz = (WORD)rot_Y0;

        nxw = (LONG)o->PosObjX + stepx - o->AnimStepX;
        nyw = (LONG)o->PosObjY + stepy - o->AnimStepY;
        nzw = (LONG)o->PosObjZ + stepz - o->AnimStepZ;

        o->AnimStepX = stepx;
        o->AnimStepY = stepy;
        o->AnimStepZ = stepz;

        o->WorkFlags &= (WORD)~(ANIM_END + NEW_FRAME);

        if (arrived) {
            /* AnimSrcFrame and AnimMemoTicks: Body_InterDep already did. */
            o->Frame = (WORD)(o->Frame + 1);
            o->WorkFlags |= NEW_FRAME;

            if ((UWORD)o->Frame >= nbf) {
                o->WorkFlags &= (WORD)~OK_HIT;

                if (o->FlagAnim == ANIM_REPEAT) {
                    o->Frame = LE16(anim + 4);          /* GetBouclageAnim */
                } else {
                    /* ANIM[_ALL]_THEN: finita, si passa a quella prenotata.
                     * Il raccordo riparte dal fotogramma zero della nuova, e
                     * NON dalla fotografia: la posa raggiunta e' l'ultimo
                     * fotogramma di questa, che e' un fotogramma vero. */
                    o->GenAnim = o->NextGenAnim;
                    o->Anim = SearchAnim(o->GenAnim, n);
                    if (o->Anim == -1) {
                        o->Anim = SearchAnim(GEN_ANIM_RIEN, n);
                        o->GenAnim = GEN_ANIM_RIEN;
                    }
                    o->FlagAnim = ANIM_REPEAT;
                    o->Frame = 0;
                    o->AnimSrcFrame = 0;
                    o->PtrPoseSrc = 0;
                    o->HitForce = 0;
                }

                /* Il bouclage viene dall'archivio: se puntasse fuori, il
                 * controllo di pose_load fermerebbe il gioco con TE_ANIM_BAD
                 * invece di ripartire. Meglio l'inizio dell'animazione che
                 * TheEnd. */
                if ((UWORD)o->Frame >= nbf) {
                    o->Frame = 0;
                    o->AnimSrcFrame = 0;
                }
                o->WorkFlags |= ANIM_END;
            }

            o->AnimStepBeta = 0;
            o->AnimStepX = 0;
            o->AnimStepY = 0;
            o->AnimStepZ = 0;
        }

        /* LA COLLISIONE (M6). La posizione che l'animazione propone passa di
         * qui prima di diventare quella dell'attore: i muri la respingono, le
         * rampe la alzano, e il cubo la contiene. Dentro c'e' anche la
         * "protection sortie du cube" che stava scritta qui fino a un'ora fa. */
        Move_Collide(n, &nxw, &nyw, &nzw);

        o->PosObjX = (WORD)nxw;
        o->PosObjY = (WORD)nyw;
        o->PosObjZ = (WORD)nzw;
    }
}


void Body_DrawAll(void)
{
    LONG light[3];
    LONG vx, vy;
    WORD n;
    int i;

    /* La lista dei rettangoli sporchi appartiene a UNA passata: qui comincia.
     * Vedi Bg_BoxReset - senza, il profilo con OPTION la farebbe traboccare da
     * solo e ogni quadro dopo tornerebbe al fondo intero. */
    Bg_BoxReset();

#if BODY_ACTOR_SPRITE
    /* LO SPRITE NON SI SPEGNE PIU' IN TESTA, e questa e' la riga che teneva
     * l'eroe invisibile quasi sempre.
     *
     * Spegnerlo qui sembrava prudente - "lo riaccende chi lo disegna, cosi'
     * l'eroe fuori campo sparisce da se'" - ma misura il tempo con l'orologio
     * sbagliato. Fra lo spegnimento e la riaccensione c'e' TUTTO il disegno
     * della posa, cioe' quasi un quadro intero, mentre l'OP ricompone la scena
     * 60 volte al secondo. L'eroe restava acceso solo dalla fine del suo
     * draw_actor alla testa della passata dopo - e siccome l'eroe e' l'attore
     * 0, quella coda e' quasi niente. Sul ferro si vedeva esattamente per quel
     * che era: invisibile di regola, un lampo ogni tanto.
     *
     * Adesso lo sprite resta ACCESO con le phrase della passata precedente, e
     * il cambio avviene quando l'immagine nuova e' pronta. E' l'altra meta' del
     * doppio banco: i due banchi rendono possibile lasciarlo acceso mentre si
     * disegna, questa riga lo lascia acceso davvero.
     *
     * Lo spegnimento resta, ma va in CODA (vedi sotto): li' si sa se l'eroe ha
     * disegnato oppure no, che e' l'informazione che serve per decidere. */
    spr_on = 0;
    spr_why = -1;               /* l'eroe non e' ancora passato da draw_actor */
#endif

    for (i = 0; i < 2; i++) {
        BodyStat.geom[i] = 0;
        BodyStat.shade[i] = 0;
        BodyStat.order[i] = 0;
    }
    BodyStat.drawn = 0;
    BodyStat.clipped = 0;
    BodyStat.invis = 0;
    /* M3d: le tessere ridisegnate davanti agli attori. Sta in BgStat perche' e'
     * un numero del fondo, ma a riempirlo e' QUESTA passata - qui gli attori,
     * e gli sprite delle porte piu' sotto. Azzerarlo nel fondo lo rendeva un
     * accumulatore ogni volta che le due passate si separavano, ed e' quel che
     * fa il profilo per stadio (OPTION) da S34. */
    BgStat.overbrick = 0;
    BodyStat.polys = 0;
    BodyStat.pixels = 0;
    BodyStat.tot = 0;
    BodyStat.live = 0;
    BodyStat.first = 0;
    BodyStat.firstslot = 0;
    BodyStat.firstpolys = 0;
    BodyStat.gpuhang = 0;
    BodyStat.hangwhere = 0;
    BodyStat.hangwhat = 0;
    BodyStat.hangsaw = 0;
    BodyStat.hangsawc = 0;
    /* Il secondo kernel (S40). Si azzerano QUI e non altrove: sono riempiti da
     * questa passata, e un contatore azzerato da una passata diversa da quella
     * che lo riempie e' un accumulatore in attesa che qualcuno le separi - che
     * e' esattamente quel che il profilo per stadio ha fatto a `overbrick`. */
    BodyStat.shadehang = 0;
    BodyStat.shadesaw = 0;
    BodyStat.shadeleft = 0;
    BodyStat.fronthang = 0;
    BodyStat.frontsaw = 0;
    BodyStat.batchmax = 0;
    BodyStat.batchtot = 0;
    BodyStat.launches = 0;
    BodyStat.jobs = 0;
    BodyStat.lines = 0;
    BodyStat.spheres = 0;
    BodyStat.entpix = 0;
    BodyStat.firstsx = 0;
    BodyStat.firstsy = 0;
    BodyStat.firstsr = 0;
    BodyStat.havesphere = 0;
    BodyStat.rundrop = 0;
    BodyStat.t_reps++;
    job_used = 0;
    job_count = 0;
    job_runs = 0;
    /* Le sonde degli sprite si azzerano insieme a queste, e per la stessa
     * ragione: Body_DrawAll e' idempotente e va potuta ripetere. */
    Sprite_StatReset();

    light_vector(Scene.AlphaLight, Scene.BetaLight, light);
    BodyStat.light = (((ULONG)light[0] & 0xFF) << 16)
                     | (((ULONG)light[1] & 0xFF) << 8)
                     | ((ULONG)light[2] & 0xFF);

    /* Lo scostamento degli ATTORI: stessa camera del fondo, centro di
     * proiezione diverso. Si ricava da quello delle tessere sostituendo la
     * costante, non sommando (23,25): la differenza si vede da dove viene. */
    vx = (LONG)BgStat.vx - CAM_CX + ACT_CX;
    vy = (LONG)BgStat.vy - CAM_CY + ACT_CY;
    BodyStat.avx = (WORD)vx;
    BodyStat.avy = (WORD)vy;

    {
        int have_first = 0;
        int first_n = -1;

        for (n = 0; n < NbObjets; n++) {
            T_OBJET *o = &ListObjet[n];
            T_BODY3D b;
            ULONG before;
            WORD ax, ay;

            /* L'ALTRO RAMO di AffScene, e sta QUI dentro apposta: il gioco
             * percorre una lista sola e sceglie il ramo per attore
             * (OBJECT.C:2646). Un secondo ciclo dopo questo darebbe tutti gli
             * sprite sopra tutti i corpi, cioe' una porta davanti a chi ci
             * passa dietro. Vedi engine_c/sprite_draw.c. */
            if (o->Flags & SPRITE_3D) {
                WORD sx, sy;
                if (o->Sprite < 0)
                    continue;
                if (o->Flags & INVISIBLE) {
                    BodyStat.invis++;
                    continue;
                }
                sx = (WORD)(((((LONG)o->PosObjX - o->PosObjZ) * ISO_X)
                             >> ISO_SHIFT) + vx);
                sy = (WORD)(((((LONG)o->PosObjX + o->PosObjZ) * ISO_Y
                              - (LONG)o->PosObjY * ISO_Y_WORLD)
                             >> ISO_SHIFT) + vy);
                if (!actor_preclip(sx, sy, o->Flags)) {
                    BodyStat.clipped++;
                    continue;
                }
                Sprite_DrawOne(o, sx, sy, vx, vy);
                continue;
            }

            if (o->Body < 0 || o->Body >= NbBodys)
                continue;

            /* La seconda meta' del test di AffScene. Sopra la bandiera di
             * `have_first` apposta: il riferimento fa uscire l'attore dalla
             * lista come se non avesse un corpo, quindi i due devono
             * concordare anche su CHI e' il primo. */
            if (o->Flags & INVISIBLE) {
                BodyStat.invis++;
                continue;
            }

            /* L'ancora proiettata: e' la ProjettePoint(PosObj - WorldCube) del
             * gioco. La sottrazione dell'origine del cubo sta dentro vx/vy. */
            ax = (WORD)(((((LONG)o->PosObjX - o->PosObjZ) * ISO_X)
                         >> ISO_SHIFT) + vx);
            ay = (WORD)(((((LONG)o->PosObjX + o->PosObjZ) * ISO_Y
                          - (LONG)o->PosObjY * ISO_Y_WORLD)
                         >> ISO_SHIFT) + vy);

            /* Il PRIMO attore con un corpo, TAGLIATO O NO.
             *
             * La bandiera esiste perche' il primo e' quasi sempre l'eroe, che
             * e' ListObjet[0]: un `if (!BodyStat.first)` non lo riconoscerebbe
             * mai come "gia' preso" e la sonda finirebbe per descrivere
             * l'ultimo attore invece del primo. Va alzata PRIMA del preclip:
             * il riferimento prende il primo con un corpo a prescindere, e se
             * qui dipendesse dal taglio i due diverrebbero appena il primo
             * attore di una stanza fosse fuori campo. */
            if (!have_first) {
                have_first = 1;
                first_n = (int)n;
                BodyStat.first = (UWORD)n;
                BodyStat.firstslot = (UWORD)HqrBodySlotOf(o->Body);
                BodyStat.firstpolys = 0;   /* se e' tagliato resta zero */
                BodyStat.firstx = ax;
                BodyStat.firsty = ay;
            }

            /* IL PRECLIP. Prima di body_parse, come nel gioco, che dopo il test
             * su Body scarta e passa oltre senza nemmeno interpolare
             * l'animazione. */
            if (!actor_preclip(ax, ay, o->Flags)) {
                BodyStat.clipped++;
                continue;
            }

            if (!body_parse(PtrBody[o->Body], &b))
                continue;

            pose_load(o, &b);

            before = BodyStat.polys;
            draw_actor(o, &b, light, vx, vy);
            if (BodyStat.polys != before)
                BodyStat.drawn++;
            if ((int)n == first_n)
                BodyStat.firstpolys = (UWORD)(BodyStat.polys - before);
        }
    }

    /* La rete di sicurezza: una passata che non ha scritto un pixel - tutti
     * preclippati, o la stanza vuota - deve comunque cancellare quel che
     * c'era, altrimenti l'ultimo attore uscito di scena resterebbe li'. */
    Bg_ClsPending();

#if BODY_ACTOR_SPRITE
    /* LO SPEGNIMENTO, ADESSO IN CODA. Se l'eroe non ha disegnato in questa
     * passata - preclippato, INVISIBLE, fuori campo, o riquadro troppo grande
     * per la finestra - l'OP non deve continuare a comporre l'ultima posa sopra
     * una scena che non la contiene piu'. `spr_why` e' l'unica cosa che lo sa:
     * `spr_on` lo azzera ogni attore, quindi a fine passata varrebbe l'esito
     * dell'ULTIMO invece che quello dell'eroe.
     *
     * A HEIGHT zero l'oggetto e' spento senza toccare la catena della lista. */
    if (!(spr_why & SPR_WHY_ON))
        JAG_HAL_OP_SPR_PH0LO =
            ((ULONG)JAG_HAL_VDB_VAL << 3)
            | ((((ULONG)JAG_OP_TAIL_STOP >> 3) & 0xFFUL) << 24);

    /* La fotografia della decisione, impacchettata per una riga di schermo:
     * why | larghezza | altezza | x0>>3. Vedi T_BODYSTAT.sprstat in hal.h per
     * come si legge il byte alto. La larghezza e l'altezza si tagliano a 255
     * apposta: se un riquadro le supera, quel byte a FF e' gia' la risposta. */
    {
        LONG w = (LONG)spr_bb[1] - spr_bb[0] + 1;
        LONG h = (LONG)spr_bb[3] - spr_bb[2] + 1;

        if (w < 0)   w = 0;
        if (w > 255) w = 255;
        if (h < 0)   h = 0;
        if (h > 255) h = 255;
        BodyStat.sprstat = ((ULONG)(spr_why & 0xFF) << 24)
                           | ((ULONG)w << 16) | ((ULONG)h << 8)
                           | (((ULONG)spr_want_x0 >> 3) & 0xFFUL);
    }
#endif
}
