/* scene_load.c - M3a: la struct SCENE letta PER INTERO, attori compresi.
 *
 * COSA CAMBIA RISPETTO A M2
 * -------------------------
 * `scene_walk.c` percorreva gli attori per saltarli: gli servivano le ZONE, che
 * stanno dopo, e il sistema degli attori non esisteva. Era una duplicazione
 * dichiarata, con la condizione di rimozione scritta in testa a quel file:
 *
 *     "quando a M3 arrivano T_OBJET e ListObjet[], si importa diskfunc.c del
 *      porting 32X e di questo file resta solo Scene_Derive"
 *
 * E' quello che succede qui. `Scene_Walk` non esiste piu'; c'e' `LoadScene`,
 * che e' il nome di upstream perche' il codice che arrivera' da M4 in poi lo
 * chiama cosi'.
 *
 * DA DOVE VIENE, E PERCHE' NON E' STATO RISCRITTO
 * ----------------------------------------------
 * Trascrizione di `LoadScene()` (SOURCES/DISKFUNC.C:43-241) presa attraverso il
 * porting 32X (`engine_src/diskfunc.c`), che quel cammino l'ha gia' fatto e gia'
 * debuggato su una CPU big endian intollerante ai disallineamenti - cioe' con
 * gli stessi due problemi del 68000 (decisione 3.7 di docs/skeleton_design.md).
 *
 * LE TRE DIFFERENZE DA UPSTREAM, TUTTE DICHIARATE
 * -----------------------------------------------
 * 1. Niente copia in RAM del blob. Upstream fa `malloc` + copia perche' gli
 *    script LIFE si automodificano (LM_OFFSET). `HQR_Get` qui torna gia' un
 *    puntatore a una voce DECOMPRESSA nell'arena per-cubo, che e' RAM
 *    scrivibile e vive esattamente quanto la stanza: la copia sarebbe una
 *    seconda copia. (Se un giorno SCENE tornasse 'raw' nel packer, il
 *    puntatore sarebbe in ROM e la copia tornerebbe obbligatoria.)
 * 2. Ordine dei byte e allineamento: ogni word passa da le.h. Vedi la' il
 *    perche' non c'era un'altra strada.
 * 3. `Body` non e' lo slot di BODY.HQR ma l'indice in `PtrBody[]`, come
 *    upstream. Il perche' sta in hal.h.
 *
 * IL CONTROLLO DI CODA RESTA, ED E' PIU' FORTE DI PRIMA
 * ----------------------------------------------------
 * La struct e' a lunghezza variabile: ogni attore porta due script preceduti
 * dalla loro dimensione, quindi un errore di UN byte sfasa tutto il resto. In
 * coda restano solo i T_TRACK, che nessuno salta, percio' i byte residui devono
 * valere ESATTAMENTE `NbBrickTrack * 6`.
 *
 * A M2 quel controllo copriva un cammino che degli attori leggeva solo le
 * dimensioni dei due script. Adesso copre ogni singolo campo di ogni attore: se
 * un campo fosse largo un byte di troppo, la coda non tornerebbe.
 */
#include "hal.h"
#include "le.h"
#include "be.h"

/* COMMON.H */
#define SIZE_BRICK_XZ   512
#define SIZE_BRICK_Y    256
#define SIZE_T_ZONE      24
#define SIZE_T_TRACK      6
#define NO_MOVE           0
#define MOVE_MANUAL       1     /* COMMON.H:356 */
#define EXTRA_GIVE_NOTHING 0x0080   /* COMMON.H, ripulito da LoadScene */

T_OBJET ListObjet[MAX_OBJETS];
LONG    NbObjets;

UBYTE  *PtrBody[MAX_BODYS];
LONG    NbBodys;

/* Slot di BODY.HQR di ogni PtrBody[]. Vedi hal.h: upstream marca la cache
 * scrivendo il bit 15 dentro la fiche, noi non possiamo sporcare una voce che
 * la cache HQR puo' restituire di nuovo. */
static UWORD loaded_hqr_body[MAX_BODYS];

WORD HqrBodySlotOf(WORD bodyidx)
{
    if (bodyidx < 0 || bodyidx >= NbBodys)
        return -1;
    return (WORD)loaded_hqr_body[bodyidx];
}

static WORD lookup_loaded_body(UWORD hqrbody)
{
    WORD i;

    for (i = 0; i < NbBodys; i++)
        if (loaded_hqr_body[i] == hqrbody)
            return i;
    return -1;
}

/* ---------------------------------------------------------------------------
 * InitObject, OBJECT.C. Azzera uno slot ai valori di partenza del motore.
 * Non e' cosmesi: `Body = -1` e `ObjCol = -1` sono sentinelle che il resto del
 * codice testa, e uno slot lasciato a zero direbbe "corpo 0" invece di "nessun
 * corpo".
 * -------------------------------------------------------------------------*/
void InitObject(WORD numobj)
{
    T_OBJET *ptrobj = &ListObjet[numobj];
    int i;

    for (i = 0; i < (int)sizeof(T_OBJET); i++)
        ((UBYTE *)ptrobj)[i] = 0;

    ptrobj->GenBody = GEN_BODY_NORMAL;
    ptrobj->GenAnim = GEN_ANIM_RIEN;

    ptrobj->PosObjY = SIZE_BRICK_Y;

    ptrobj->SRot = 40;
    ptrobj->Move = NO_MOVE;

    ptrobj->ObjCol  = -1;
    ptrobj->CarryBy = -1;
    ptrobj->ZoneSce = -1;

    ptrobj->LifePoint = 50;
    ptrobj->Armure    = 1;
    ptrobj->HitBy     = -1;

    ptrobj->Body = -1;
    ptrobj->Anim = -1;

    ptrobj->OffsetTrack = -1;
    ptrobj->OffsetLife  = 0;
}

/* ---------------------------------------------------------------------------
 * InitPerso, OBJECT.C. UNA VOLTA A PARTITA, non a ogni stanza.
 *
 * E' la distinzione che separa quel che l'eroe E' da quel che la stanza dice:
 * angolo, corpo, punti vita, inventario attraversano le porte; posizione e
 * script no. Chiamarla da LoadScene ricreerebbe Twinsen da capo a ogni
 * transizione.
 * -------------------------------------------------------------------------*/
void InitPerso(void)
{
    InitObject(NUM_PERSO);

    /* SEGNAPOSTO DICHIARATO, con la sua condizione di rimozione.
     *
     * `InitObject` mette GEN_BODY_NORMAL, che sulla fiche 0 e' la TUNICA. A
     * inizio partita Twinsen porta la camiciola da prigioniero, cioe' il
     * generico 3 -> BODY 1, e a metterlo NON e' il caricamento della scena: e'
     * lo script LIFE dell'eroe, che a offset 54 della scena 0 dice proprio
     * `LM_BODY 3` (opcode 17, COMMON.H:422).
     *
     * L'interprete LIFE arriva a M5. Fino ad allora il generico si scrive qui a
     * mano: e' un valore che il gioco calcola, scritto da noi invece che
     * calcolato, e sparisce da se' quando GereLife girera'. Non e' un dato
     * inventato - la fiche resta l'unica a dire quale slot HQR sia.
     *
     * Sta QUI e non in LoadScene proprio perche' l'eroe attraversa le porte:
     * lo script lo veste una volta, non una volta per stanza. */
    ListObjet[NUM_PERSO].GenBody = 3;

    /* M6: I FLAG DELL'EROE, `RestartPerso` (OBJECT.C:289). Nel record di scena
     * i flag ci sono solo per gli attori 1..N; quelli dell'eroe li scrive il
     * gioco all'inizio della partita, ed e' QUESTA riga che accende la
     * collisione col decoro. Senza `CHECK_BRICK_COL`, `Move_Collide` lascia
     * passare tutto e Twinsen attraversa i muri.
     *
     * Gli altri tre si scrivono insieme perche' sono la stessa riga di
     * upstream, non perche' servano adesso: `CHECK_ZONE` e `CHECK_CODE_JEU`
     * li leggera' chi non c'e' ancora (le zone, l'annegamento), `OBJ_FALLABLE`
     * la gravita'. Scriverne uno solo vorrebbe dire tornare qui tre volte. */
    ListObjet[NUM_PERSO].Flags = OBJ_FALLABLE + CHECK_ZONE + CHECK_OBJ_COL
                                 + CHECK_BRICK_COL + CHECK_CODE_JEU;
}

/* ---------------------------------------------------------------------------
 * SearchBody, FICHE.C:585.
 *
 * L'attore non nomina uno slot di BODY.HQR: nomina un corpo GENERICO (0 =
 * normale, 1 = tunica, ...) e la sua fiche FILE3D dice a quale slot
 * corrisponde. E' il motivo per cui il corpo dell'eroe non e' una nostra
 * scelta: la fiche 0 mappa il generico 3 sul BODY 1, che e' il prigioniero.
 *
 * Formato della fiche, un blocco alla volta fino a 255:
 *     [comando][generico][dimensione][carico...]
 * comando 1 = BODY. Se il generico e' quello cercato: due byte di slot HQR, poi
 * un byte "ha un'azione", e se vale 14 seguono le sei word della ZV.
 * -------------------------------------------------------------------------*/
WORD SearchBody(UBYTE numbody, WORD numobj)
{
    T_OBJET *ptrobj = &ListObjet[numobj];
    const le_byte *ptrc = (const le_byte *)ptrobj->PtrFile3D;
    UBYTE command;

    if (!ptrc)
        return -1;

    while ((command = *ptrc++) != 255) {
        if (command == 1) {                    /* BODY */
            UBYTE numgene = *ptrc++;

            if (numgene != numbody) {
                ptrc += *ptrc;                 /* dimensione: al prossimo */
                continue;
            }

            ptrc++;                            /* salta la dimensione */
            {
                UWORD hqrbody = LE16u(ptrc);
                WORD  cached;

                ptrc += 2;
                cached = lookup_loaded_body(hqrbody);
                if (cached >= 0)
                    return cached;

                if (NbBodys >= MAX_BODYS)
                    TheEnd(TE_OUT_OF_MEM, "PtrBody pieno");

                PtrBody[NbBodys] = (UBYTE *)HQR_Get(HQR_BODY, (int)hqrbody);
                if (!PtrBody[NbBodys])
                    TheEnd(TE_HQR_MISSING, "body mancante");

                /* Upstream chiama qui PatchObjet, che normalizza i gruppi
                 * dell'animazione da 38 a 36 byte di passo. Serve a p_anim.c,
                 * che arriva a M4: farlo adesso vorrebbe dire scrivere codice
                 * che nessuno esercita, e la ZV che segue lo prova gia'. */
                loaded_hqr_body[NbBodys] = hqrbody;
                return NbBodys++;
            }
        }

        ptrc++;                                /* generico */
        ptrc += *ptrc;                         /* dimensione */
    }
    return -1;
}

/* ---------------------------------------------------------------------------
 * SearchAnim, FICHE.C:529. Gemella di SearchBody: STESSA fiche, comando 3
 * invece di 1.
 *
 * L'attore non nomina una voce di ANIM.HQR: nomina un'animazione GENERICA
 * (0 = fermo, poi cammino, corsa, ...) e la fiche dice quale voce dell'archivio
 * le corrisponde per QUEL personaggio. E' lo stesso strato d'indirezione dei
 * corpi, ed e' quel che permette a un mago e a una guardia di "stare fermi"
 * ciascuno a modo proprio.
 *
 * `has_action` e' il terzo byte: dice se all'animazione sono appese delle azioni
 * per fotogramma (suoni, colpi, oggetti lanciati). Le azioni sono roba di
 * GereAnimAction e non di M4; qui si legge il byte per non doverlo riscoprire.
 * -------------------------------------------------------------------------*/
WORD SearchAnim(UBYTE numanim, WORD numobj)
{
    T_OBJET *ptrobj = &ListObjet[numobj];
    const le_byte *ptrc = (const le_byte *)ptrobj->PtrFile3D;
    UBYTE command;

    if (!ptrc)
        return -1;

    while ((command = *ptrc++) != 255) {
        if (command == 3) {                    /* ANIM */
            UBYTE numgene = *ptrc++;

            if (numgene != numanim) {
                ptrc += *ptrc;                 /* dimensione: al prossimo */
                continue;
            }

            ptrc++;                            /* salta la dimensione */
            return (WORD)LE16(ptrc);
        }

        ptrc++;                                /* generico */
        ptrc += *ptrc;                         /* dimensione */
    }
    return -1;
}

/* ---------------------------------------------------------------------------
 * StartInitAllObjs: attacca a ogni attore il corpo che la sua fiche dichiara.
 *
 * Upstream lo fa piu' tardi e per attore (StartInitObj -> InitBody ->
 * SearchBody, OBJECT.C:1029), perche' li' i corpi si caricano dal disco quando
 * servono. Qui e' una passata sola subito dopo il caricamento, e la ragione e'
 * l'arena: le voci decompresse vivono nell'arena per-cubo, che si azzera al
 * cambio di stanza, quindi il momento naturale per riempirla e' quello in cui
 * la stanza si carica.
 *
 * Due categorie non prendono un corpo, e nessuna delle due e' un errore:
 *   - SPRITE_3D (flag 1024): e' una bitmap di SPRITES.HQR, percorso diverso,
 *     fuori da M3;
 *   - GenBody 255: la sentinella "nessun corpo" (i quattro attori invisibili
 *     della scena 0 la usano).
 * -------------------------------------------------------------------------*/
void StartInitAllObjs(void)
{
    WORD n;

    for (n = 0; n < NbObjets; n++) {
        T_OBJET *ptrobj = &ListObjet[n];

        ptrobj->Body = -1;
        if (ptrobj->Flags & SPRITE_3D) {
            /* InitObject, OBJECT.C:124-129: per una PORTA il passo di
             * animazione parte DOVE STA L'ANTA, cioe' sulla posizione
             * dell'oggetto. Serve a M3d: DrawOverBrick3 usa AnimStep e non
             * PosObj proprio perche' un'anta che si apre si sposta, e la
             * profondita' va presa dov'e' adesso. Finche' l'animazione non
             * c'e' i due valori coincidono - ed e' il motivo per cui questa
             * riga va scritta ORA e non quando servira': se manca, la porta
             * finisce nella cella (0,0,0) e il confronto e' sempre falso. */
            if (ptrobj->Flags & SPRITE_CLIP) {
                ptrobj->AnimStepX = ptrobj->PosObjX;
                ptrobj->AnimStepY = ptrobj->PosObjY;
                ptrobj->AnimStepZ = ptrobj->PosObjZ;
            }
            continue;
        }
        if (ptrobj->GenBody == 255)
            continue;
        ptrobj->Body = SearchBody(ptrobj->GenBody, n);

        /* M4: e l'animazione che la stessa fiche dichiara per il suo GenAnim.
         * Upstream lo fa in InitObject (OBJECT.C:96-117) insieme al corpo, e
         * ripiega su GEN_ANIM_RIEN se il generico non c'e' - come a fine
         * animazione, OBJECT.C:1765-1769. Un attore che non ha nemmeno quella
         * resta a -1, cioe' fermo nella posa a riposo. */
        ptrobj->Anim = SearchAnim(ptrobj->GenAnim, n);
        if (ptrobj->Anim == -1 && ptrobj->GenAnim != GEN_ANIM_RIEN) {
            ptrobj->GenAnim = GEN_ANIM_RIEN;
            ptrobj->Anim = SearchAnim(GEN_ANIM_RIEN, n);
        }
        ptrobj->Frame = 0;
        ptrobj->AnimSrcFrame = 0;
        ptrobj->AnimMemoTicks = 0;

        /* M6: LA ZV, cioe' l'ingombro con cui l'attore urta i muri
         * (`InitBody`, OBJECT.C:1043-1071).
         *
         * Non e' il riquadro del corpo: la X e la Z diventano un QUADRATO di
         * lato pari alla media dei due ingombri, centrato sull'attore. E'
         * voluto - un ingombro rettangolare girerebbe insieme al personaggio e
         * la collisione andrebbe ruotata a ogni passo; un quadrato centrato e'
         * invariante per rotazione, quindi i quattro spigoli si provano cosi'
         * come stanno. La Y invece resta quella vera, perche' l'altezza non
         * ruota.
         *
         * Il caso `M_Xmin != -32000` (ZV forzata dallo script) non c'e': quei
         * globali li scrive l'interprete LIFE, che e' M5. */
        if (ptrobj->Body >= 0) {
            /* Il bbox sta a +28 nell'intestazione del blob JBD (era ai byte
             * +2..+13 del corpo LBA1), stesso ordine X0,X1,Y0,Y1,Z0,Z1 -
             * tools/body_digest.py e' il padrone del layout. */
            const be_byte *h = (const be_byte *)PtrBody[ptrobj->Body] + 28;
            WORD x0 = BE16(h), x1 = BE16(h + 2);
            WORD z0 = BE16(h + 8), z1 = BE16(h + 10);
            WORD size;

            ptrobj->Ymin = BE16(h + 4);
            ptrobj->Ymax = BE16(h + 6);

            if ((UWORD)ptrobj->Flags & MINI_ZV)
                size = (WORD)(((x1 - x0) < (z1 - z0) ? (x1 - x0)
                                                     : (z1 - z0)) / 2);
            else
                size = (WORD)(((x1 - x0) + (z1 - z0)) / 4);

            ptrobj->Xmin = (WORD)-size;
            ptrobj->Xmax = size;
            ptrobj->Zmin = (WORD)-size;
            ptrobj->Zmax = size;
        }
    }
}

/* ---------------------------------------------------------------------------
 * LoadScene, DISKFUNC.C:43-241.
 * -------------------------------------------------------------------------*/
int LoadScene(int numscene)
{
    const void *blob = HQR_Get(HQR_SCENE, numscene);
    ULONG size = HQR_Size(HQR_SCENE, numscene);
    T_OBJET *ptrobj;
    le_reader r;
    WORD n;

    Scene.valid = 0;
    Scene.num = (WORD)numscene;
    NbObjets = 0;
    NbBodys = 0;
    Body_NormCacheReset();
    if (!blob || size == 0)
        return 0;

    LE_Init(&r, blob, size);

    /* --- INFO_WORLD --- */
    Scene.Island       = LE_U8(&r);
    Scene.GameOverCube = LE_U8(&r);
    LE_S16(&r); LE_S16(&r);            /* voce holomap dell'eroe: Tier-3 */

    /* --- AMBIANCE --- */
    Scene.AlphaLight   = LE_S16(&r);
    Scene.BetaLight    = LE_S16(&r);
    for (n = 0; n < 4; n++) {          /* sample, repeat, rnd */
        LE_S16(&r); LE_S16(&r); LE_S16(&r);
    }
    LE_S16(&r);                        /* SecondMin   */
    LE_S16(&r);                        /* SecondEcart */
    Scene.CubeJingle   = LE_U8(&r);

    /* --- HERO_START ---
     * NIENTE InitObject QUI, ed e' una correzione rispetto alla prima stesura
     * di M3a. Lo slot dell'eroe SOPRAVVIVE al cambio di stanza: `ChangeCube`
     * (OBJECT.C:518-537) azzera `Move`, `ZoneSce`, `OffsetLife`, `OffsetTrack`
     * e `LabelTrack`, e non tocca nient'altro - in particolare non tocca
     * `Beta`. L'orientamento si conserva NON azzerandolo, ed e' il
     * comportamento che si vede giocando: uscendo da una stanza verso NE
     * guardando NE, nella stanza nuova si e' a SW ma ancora rivolti a NE; se si
     * esce indietreggiando, si continua a guardare indietro.
     *
     * Azzerarlo qui vorrebbe dire ricreare Twinsen da capo a ogni porta: stesso
     * angolo, stesso corpo, stessi punti vita, per sempre. Cioe' esattamente
     * "l'eroe della scena 0 messo nelle altre scene".
     *
     * Quel che invece VA rinfrescato a ogni stanza sono i tre puntatori, e non
     * per scelta: i due script stanno dentro il blob della scena nuova, e la
     * fiche sta in una voce decompressa nell'arena per-cubo, che `HQM_Free_All`
     * ha appena azzerato. Un puntatore conservato qui sarebbe un puntatore a
     * memoria riassegnata. */
    ptrobj = &ListObjet[NUM_PERSO];

    /* M6: L'EROE OBBEDISCE AL PAD, e a dirlo e' `ChangeCube` (OBJECT.C:486),
     * che a ogni stanza rimette `Move = MOVE_MANUAL` insieme ai quattro campi
     * qui sotto. Nel record della scena il `Move` c'e' solo per gli attori
     * 1..N: il blocco dell'eroe ha la posizione di partenza e i due script,
     * niente altro. Senza questa riga `DoDir` esce subito e il pad non muove
     * nulla - il campo resterebbe a NO_MOVE, che e' quel che `InitObject`
     * scrive.
     *
     * Non e' un valore inventato per far camminare Twinsen: e' la riga che il
     * gioco esegue ogni volta che si attraversa una porta. Gli altri quattro
     * campi vengono dalla stessa funzione e appartengono agli script (M5), ma
     * azzerarli adesso e' esattamente quel che upstream fa. */
    ptrobj->Move        = MOVE_MANUAL;
    ptrobj->ZoneSce     = -1;
    ptrobj->OffsetLife  = 0;
    ptrobj->OffsetTrack = -1;
    ptrobj->LabelTrack  = -1;

    Scene.CubeStartX = LE_S16(&r);
    Scene.CubeStartY = LE_S16(&r);
    Scene.CubeStartZ = LE_S16(&r);

    n = LE_S16(&r);
    ptrobj->PtrTrack = (UBYTE *)r.p;
    LE_Skip(&r, n);
    n = LE_S16(&r);
    ptrobj->PtrLife = (UBYTE *)r.p;
    LE_Skip(&r, n);

    /* La fiche dell'eroe non sta nella scena: la carica LoadFicPerso
     * (OBJECT.C:174-206) leggendo le cinque fiche, una per comportamento. A
     * inizio partita il comportamento e' quello normale, quindi la fiche e' la
     * 0. Le altre quattro servono quando il comportamento cambia: M6. */
    ptrobj->PtrFile3D = (UBYTE *)HQR_Get(HQR_FILE3D, FILE_3D_NORMAL);

    /* --- OBJECT --- */
    NbObjets = LE_S16(&r);
    if (NbObjets > MAX_OBJETS)
        NbObjets = MAX_OBJETS;
    Scene.NbObjets = NbObjets;

    for (n = 1; n < NbObjets; n++) {
        WORD indexfile3d;

        InitObject(n);
        ptrobj = &ListObjet[n];

        ptrobj->Flags = LE_S16(&r);
        indexfile3d   = LE_S16(&r);

        /* La fiche si carica solo per chi ha un corpo 3D. L'indice si consuma
         * comunque dal flusso, anche per gli SPRITE_3D che lo lasciano a -1:
         * saltarlo sfaserebbe tutto quel che segue. */
        if (!(ptrobj->Flags & SPRITE_3D) && indexfile3d >= 0)
            ptrobj->PtrFile3D = (UBYTE *)HQR_Get(HQR_FILE3D, indexfile3d);

        ptrobj->GenBody = LE_U8(&r);
        ptrobj->GenAnim = LE_U8(&r);
        ptrobj->Sprite  = LE_S16(&r);

        ptrobj->OldPosX = ptrobj->PosObjX = LE_S16(&r);
        ptrobj->OldPosY = ptrobj->PosObjY = LE_S16(&r);
        ptrobj->OldPosZ = ptrobj->PosObjZ = LE_S16(&r);

        /* Zero-esteso, non con segno (DISKFUNC.C:170): un LifePoint di 128
         * letto con segno nascerebbe negativo, cioe' "morto alla nascita". */
        ptrobj->HitForce    = (WORD)LE_U8(&r);
        ptrobj->OptionFlags = LE_S16(&r);
        ptrobj->OptionFlags &= ~EXTRA_GIVE_NOTHING;

        ptrobj->Beta = LE_S16(&r);
        ptrobj->SRot = LE_S16(&r);
        ptrobj->Move = LE_S16(&r);

        ptrobj->Info  = LE_S16(&r);
        ptrobj->Info1 = LE_S16(&r);
        ptrobj->Info2 = LE_S16(&r);
        ptrobj->Info3 = LE_S16(&r);

        ptrobj->NbBonus   = (WORD)LE_U8(&r);
        ptrobj->CoulObj   = (WORD)LE_U8(&r);
        ptrobj->Armure    = (WORD)LE_U8(&r);
        ptrobj->LifePoint = (WORD)LE_U8(&r);

        {
            WORD sizetoload = LE_S16(&r);
            ptrobj->PtrTrack = (UBYTE *)r.p;
            LE_Skip(&r, sizetoload);
            sizetoload = LE_S16(&r);
            ptrobj->PtrLife = (UBYTE *)r.p;
            LE_Skip(&r, sizetoload);
        }

        if (LE_Left(&r) < 0)
            return 0;                   /* uscito dal blob: inutile continuare */
    }

    /* --- ZONE --- */
    Scene.NbZones = LE_S16(&r);
    Scene.ZonesRaw = r.p;               /* decodificate a richiesta, vedi le.h */
    LE_Skip(&r, (long)Scene.NbZones * SIZE_T_ZONE);

    /* --- TRACK --- */
    Scene.NbBrickTrack = LE_S16(&r);
    Scene.TracksRaw = r.p;

    Scene.tail = LE_Left(&r);
    Scene.valid = (Scene.tail == (long)Scene.NbBrickTrack * SIZE_T_TRACK);
    if (!Scene.valid)
        return 0;

    StartInitAllObjs();
    return 1;
}
