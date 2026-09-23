/* object_move.c - M6: l'eroe si comanda col pad.
 *
 * DA DOVE VIENE, RIGA PER RIGA
 *   SOURCES/OBJECT.C:2129   `DoDir` - il ramo MOVE_MANUAL, cioe' l'unico attore
 *                           che obbedisce al giocatore;
 *   SOURCES/OBJECT.C:2088   `ManualRealAngle` - da MyJoy all'angolo di arrivo;
 *   LIB386/LIB_3D/P_FUNC.C  `InitRealAngleConst` / `GetRealAngle` - la rotazione
 *                           interpolata nel tempo.
 *
 * PERCHE' UN FILE NUOVO E NON UNA FUNZIONE IN engine_main.c: da qui in poi
 * arriva OBJECT.C, che e' 3587 righe. `engine_main.c` e' il MainLoop e la
 * diagnostica; la logica degli attori appartiene al suo file, come upstream.
 *
 * LA ROTAZIONE NON E' UN ANGOLO, E' UN INTERVALLO. Il pad non scrive `Beta`:
 * arma un'interpolazione (angolo di partenza, angolo di arrivo, durata) e ogni
 * quadro ne legge il valore raggiunto. La durata la decide `SRot`, che per
 * l'eroe vale 40 (`InitObject`, OBJECT.C:65). E' il motivo per cui Twinsen gira
 * come una nave e non come una torretta, e si vede.
 */
#include "hal.h"

/* COMMON.H:355-362. Solo quelli che questo file guarda: il resto dei modi di
 * movimento (FOLLOW, TRACK, RANDOM) arriva con gli script, a M5. */
#define NO_MOVE       0
#define MOVE_MANUAL   1

/* P_FUNC.C:5. Il giro intero e' 1024, non 360. */
#define ANGLE_MASK    1023

/* OBJECT.C:16-18. Lo stato del pad al quadro precedente: serve a distinguere
 * "il tasto e' appena cambiato" da "e' tenuto premuto", che e' la differenza
 * fra rilanciare un'animazione e lasciarla scorrere. */
WORD LastJoyFlag = 0;
WORD LastMyJoy   = 0;
WORD LastMyFire  = 0;

/* OBJECT.C:10-13, e restano GLOBALI come li' - vedi Move_Collide. */
static WORD Nxw, Nyw, Nzw;
static WORD SaveNxw, SaveNyw, SaveNzw;
static WORD OldX, OldY, OldZ;
static WORD Col1;
static T_OBJET *APtObj;

/* ---------------------------------------------------------------------------
 * BoundRegleTrois, P_FUNC.C:104. Una regola del tre con gli estremi bloccati:
 * e' quel che fa salire il pavimento GRADUALMENTE lungo una rampa invece che
 * di scatto a meta' cella.
 * -------------------------------------------------------------------------*/
static LONG bound_regle_trois(LONG val1, LONG val2, LONG nbstep, LONG step)
{
    if (step <= 0)
        return val1;
    if (step >= nbstep)
        return val2;
    return val1 + ((val2 - val1) * step) / nbstep;
}

/* ---------------------------------------------------------------------------
 * ReajustPos, OBJECT.C:1301. LE RAMPE.
 *
 * Non ferma niente: ALZA. Il codice di collisione 2..5 dice su quale dei
 * quattro versi la cella sale, e la Y nuova esce da una regola del tre fra il
 * pavimento della cella e quello di sopra, in funzione di QUANTO si e' dentro
 * la cella. I codici 6..13 sono diagonali: il primo switch li riduce a uno dei
 * quattro versi guardando da che parte dello spigolo ci si trova.
 *
 * E' la ragione per cui in LBA1 si sale una scala camminando, senza saltare.
 * -------------------------------------------------------------------------*/
static void ReajustPos(UBYTE col)
{
    WORD xw, yw, zw;

    if (!col)
        return;

    xw = (WORD)(XMap * SIZE_BRICK_XZ - DEMI_BRICK_XZ);
    yw = (WORD)(YMap * SIZE_BRICK_Y);
    zw = (WORD)(ZMap * SIZE_BRICK_XZ - DEMI_BRICK_XZ);

    switch (col) {                      /* collisioni complesse */
    case 6:  col = (Nxw - xw < Nzw - zw) ? 3 : 2; break;
    case 7:  col = (Nxw - xw < Nzw - zw) ? 5 : 4; break;
    case 10: col = (Nxw - xw < Nzw - zw) ? 2 : 3; break;
    case 11: col = (Nxw - xw < Nzw - zw) ? 4 : 5; break;
    case 8:  col = (SIZE_BRICK_XZ - (Nxw - xw) > Nzw - zw) ? 4 : 2; break;
    case 9:  col = (SIZE_BRICK_XZ - (Nxw - xw) > Nzw - zw) ? 5 : 3; break;
    case 12: col = (SIZE_BRICK_XZ - (Nxw - xw) > Nzw - zw) ? 2 : 4; break;
    case 13: col = (SIZE_BRICK_XZ - (Nxw - xw) > Nzw - zw) ? 3 : 5; break;
    default: break;
    }

    switch (col) {                      /* collisioni di base */
    case 2:
        Nyw = (WORD)(yw + bound_regle_trois(0, SIZE_BRICK_Y,
                                            SIZE_BRICK_XZ, Nxw - xw));
        break;
    case 3:
        Nyw = (WORD)(yw + bound_regle_trois(0, SIZE_BRICK_Y,
                                            SIZE_BRICK_XZ, Nzw - zw));
        break;
    case 4:
        Nyw = (WORD)(yw + bound_regle_trois(SIZE_BRICK_Y, 0,
                                            SIZE_BRICK_XZ, Nzw - zw));
        break;
    case 5:
        Nyw = (WORD)(yw + bound_regle_trois(SIZE_BRICK_Y, 0,
                                            SIZE_BRICK_XZ, Nxw - xw));
        break;
    default:
        break;
    }
}

/* ---------------------------------------------------------------------------
 * DoCornerReajust, OBJECT.C:1435, e la sua gemella per l'eroe (:1485).
 *
 * IL MURO NON FERMA IL CENTRO, FERMA UNO SPIGOLO. La posizione nuova si prova
 * quattro volte, una per spigolo della ZV dell'attore, e ogni prova che finisce
 * dentro un muro riporta INDIETRO UNA SOLA delle due coordinate: quella da cui
 * si veniva. E' il motivo per cui in LBA1 si SCIVOLA lungo i muri invece di
 * incollarcisi - camminando in diagonale contro una parete, la componente
 * parallela sopravvive.
 *
 * Quale delle due si annulla lo decide una seconda interrogazione: se anche
 * tornando alla Z vecchia si e' ancora nel muro, allora e' la X a essere
 * colpevole; altrimenti e' la Z.
 *
 * `SaveNxw/SaveNyw/SaveNzw` sono l'accumulatore comune ai quattro spigoli: ogni
 * spigolo scrive le sue rinunce li' e rimette Nxw/Nyw/Nzw da quello, cosi' il
 * quarto spigolo vede quel che i primi tre hanno gia' concesso.
 * -------------------------------------------------------------------------*/
static void DoCornerReajust(WORD nx, WORD ny, WORD nz, WORD coin)
{
    UBYTE col, orgcol;

    orgcol = Grid_ColBrick(Nxw, Nyw, Nzw);

    Nxw = (WORD)(Nxw + nx);
    Nyw = (WORD)(Nyw + ny);
    Nzw = (WORD)(Nzw + nz);

    if (Nxw >= 0 && Nzw >= 0
        && Nxw <= 63 * SIZE_BRICK_XZ && Nzw <= 63 * SIZE_BRICK_XZ) {
        ReajustPos(orgcol);

        if ((col = Grid_ColBrick(Nxw, Nyw, Nzw)) != 0) {
            if (col == 1) {
                Col1 |= coin;
                if (Grid_ColBrick(Nxw, Nyw, (WORD)(OldZ + nz)) == 1) {
                    if (Grid_ColBrick((WORD)(OldX + nx), Nyw, Nzw) != 1)
                        SaveNxw = OldX;
                } else {
                    SaveNzw = OldZ;
                }
            }
        }
    }

    Nxw = SaveNxw;
    Nyw = SaveNyw;
    Nzw = SaveNzw;
}

static void DoCornerReajustTwinkel(WORD nx, WORD ny, WORD nz, WORD coin)
{
    UBYTE col, orgcol;
    LONG ymax = APtObj->Ymax;

    orgcol = Grid_ColBrick(Nxw, Nyw, Nzw);

    Nxw = (WORD)(Nxw + nx);
    Nyw = (WORD)(Nyw + ny);
    Nzw = (WORD)(Nzw + nz);

    if (Nxw >= 0 && Nzw >= 0
        && Nxw <= 63 * SIZE_BRICK_XZ && Nzw <= 63 * SIZE_BRICK_XZ) {
        ReajustPos(orgcol);

        if ((col = Grid_ColBrickFull(Nxw, Nyw, Nzw, ymax)) != 0) {
            if (col == 1) {
                Col1 |= coin;
                if (Grid_ColBrickFull(Nxw, Nyw, (WORD)(OldZ + nz), ymax) == 1) {
                    if (Grid_ColBrickFull((WORD)(OldX + nx), Nyw, Nzw, ymax) != 1)
                        SaveNxw = OldX;
                } else {
                    SaveNzw = OldZ;
                }
            }
        }
    }

    Nxw = SaveNxw;
    Nyw = SaveNyw;
    Nzw = SaveNzw;
}

/* ---------------------------------------------------------------------------
 * Move_Collide - il tratto di `DoAnim` che sta fra la posizione calcolata
 * dall'animazione e la posizione scritta nell'attore (OBJECT.C:1824-2078).
 *
 * E' una funzione a se' e non e' inline in Body_AnimTick per un motivo di
 * confine: la collisione appartiene al mondo (la griglia, i blocchi), non al
 * disegno del corpo. Upstream le ha nello stesso file perche' li' OBJECT.C
 * contiene tutto.
 *
 * QUEL CHE MANCA, DICHIARATO: `CheckObjCol` (attore contro attore), il portato
 * (`CarryBy`), la CADUTA (`FALLING` + `ReceptionObj`) e il "choc si on court".
 * Le prime tre vogliono roba che arriva con M5 e con la gravita'; la quarta
 * vuole i comportamenti. Restano fuori PER INTERO invece che a meta': una
 * collisione fra attori fatta per meta' fermerebbe Twinsen contro NPC che non
 * sanno ancora spostarsi.
 * -------------------------------------------------------------------------*/
void Move_Collide(WORD numobj, LONG *px, LONG *py, LONG *pz)
{
    T_OBJET *ptrobj = &ListObjet[numobj];

    APtObj = ptrobj;

    OldX = ptrobj->OldPosX;
    OldY = ptrobj->OldPosY;
    OldZ = ptrobj->OldPosZ;

    Nxw = (WORD)*px;
    Nyw = (WORD)*py;
    Nzw = (WORD)*pz;

    if (ptrobj->Flags & CHECK_BRICK_COL) {
        UBYTE col;

        /* Prima si sistema la Y sulla cella in cui si E' GIA', e non su quella
         * dove si sta andando: e' la rampa sotto i piedi adesso. */
        if ((col = Grid_ColBrick(OldX, OldY, OldZ)) != 0) {
            if (col == 1) {
                /* Dentro un muro: si viene espulsi verso l'alto di una cella.
                 * Upstream ci stampa pure un messaggio di guasto ("currentpos
                 * dans col 1 ?"), cioe' non dovrebbe succedere mai. */
                Nyw = (WORD)((Nyw / SIZE_BRICK_Y) * SIZE_BRICK_Y + SIZE_BRICK_Y);
                ptrobj->PosObjY = Nyw;
            } else {
                ReajustPos(col);
            }
        }

        SaveNxw = Nxw;
        SaveNyw = Nyw;
        SaveNzw = Nzw;
        Col1 = 0;

        if ((numobj == NUM_PERSO) && ((ptrobj->Flags & COL_BASSE) == 0)) {
            DoCornerReajustTwinkel(ptrobj->Xmin, ptrobj->Ymin, ptrobj->Zmin, 1);
            DoCornerReajustTwinkel(ptrobj->Xmax, ptrobj->Ymin, ptrobj->Zmin, 2);
            DoCornerReajustTwinkel(ptrobj->Xmax, ptrobj->Ymin, ptrobj->Zmax, 4);
            DoCornerReajustTwinkel(ptrobj->Xmin, ptrobj->Ymin, ptrobj->Zmax, 8);
        } else {
            DoCornerReajust(ptrobj->Xmin, ptrobj->Ymin, ptrobj->Zmin, 1);
            DoCornerReajust(ptrobj->Xmax, ptrobj->Ymin, ptrobj->Zmin, 2);
            DoCornerReajust(ptrobj->Xmax, ptrobj->Ymin, ptrobj->Zmax, 4);
            DoCornerReajust(ptrobj->Xmin, ptrobj->Ymin, ptrobj->Zmax, 8);
        }

        if (Col1)
            ptrobj->Col |= 128;
    }

    /* "Protection sortie du cube", OBJECT.C:2061-2072. */
    if (Nxw < 0) Nxw = 0;
    if (Nyw < 0) Nyw = 0;
    if (Nzw < 0) Nzw = 0;
    if (Nxw > 63 * SIZE_BRICK_XZ) Nxw = 63 * SIZE_BRICK_XZ;
    if (Nzw > 63 * SIZE_BRICK_XZ) Nzw = 63 * SIZE_BRICK_XZ;

    *px = Nxw;
    *py = Nyw;
    *pz = Nzw;
}

/* ---------------------------------------------------------------------------
 * InitRealAngleConst, P_FUNC.C:56. Tradotta alla lettera, compreso il giro
 * apparentemente inutile di `<< 6` e `>> 6`: su WORD quello tiene i dieci bit
 * bassi della differenza e ne prende il valore assoluto, cioe' e' il modo in
 * cui il gioco misura una distanza angolare su un cerchio da 1024. Riscriverlo
 * "meglio" vorrebbe dire scegliere un altro verso di rotazione sui giri lunghi.
 *
 * "Const" sta per velocita' costante: la durata non e' data, si calcola dalla
 * distanza da percorrere, cosi' un quarto di giro impiega il doppio di un
 * ottavo.
 * -------------------------------------------------------------------------*/
void InitRealAngleConst(WORD start, WORD end, LONG duration,
                        T_REAL_VALUE *ptrstruct)
{
    WORD t;

    ptrstruct->StartValue = (WORD)(start & ANGLE_MASK);
    ptrstruct->EndValue   = (WORD)(end & ANGLE_MASK);

    t = (WORD)((ptrstruct->StartValue - ptrstruct->EndValue) << 6);
    if (t < 0)
        t = (WORD)(-t);
    t = (WORD)((UWORD)t >> 6);

    ptrstruct->TimeValue = (UWORD)((t * duration) >> 8);
    ptrstruct->MemoTicks = TimerRef;
}

/* ---------------------------------------------------------------------------
 * GetRealAngle, P_FUNC.C:78. Il valore raggiunto adesso, e a interpolazione
 * finita `TimeValue` si azzera da se': da li' in poi torna l'arrivo senza piu'
 * dividere.
 *
 * I due rami a +-512 sono quel che rende la rotazione la piu' CORTA delle due:
 * senza, girare da 1000 a 20 farebbe il giro lungo all'indietro.
 * -------------------------------------------------------------------------*/
WORD GetRealAngle(T_REAL_VALUE *ptrstruct)
{
    if (ptrstruct->TimeValue) {
        LONG delta = (LONG)TimerRef - (LONG)ptrstruct->MemoTicks;

        if (delta < (LONG)ptrstruct->TimeValue) {
            LONG value = (LONG)ptrstruct->EndValue - (LONG)ptrstruct->StartValue;

            if (value < -512)
                value += 1024;
            else if (value > 512)
                value -= 1024;

            value = (value * delta) / (LONG)ptrstruct->TimeValue;
            value += ptrstruct->StartValue;
            return (WORD)value;
        }

        ptrstruct->TimeValue = 0;
    }

    return ptrstruct->EndValue;
}

/* ---------------------------------------------------------------------------
 * ManualRealAngle, OBJECT.C:2088.
 *
 * +-256 e' un QUARTO di giro, e non e' l'angolo a cui si arriva: e' l'arrivo
 * ricalcolato a ogni quadro a partire dal Beta corrente. Tenendo premuto, il
 * bersaglio scappa in avanti di un quarto di giro ogni volta e la rotazione non
 * finisce mai - e' cosi' che un valore interpolato diventa una rotazione
 * continua. Lasciando il tasto, `angle` vale 0, l'arrivo coincide con la
 * partenza e Twinsen si ferma dov'e'.
 * -------------------------------------------------------------------------*/
void ManualRealAngle(T_OBJET *ptrobj)
{
    WORD angle = 0;

    if (MyJoy & J_LEFT)
        angle = +256;
    if (MyJoy & J_RIGHT)
        angle = -256;

    InitRealAngleConst(ptrobj->Beta,
                       (WORD)(ptrobj->Beta + angle),
                       ptrobj->SRot,
                       &ptrobj->RealAngle);
}

/* ---------------------------------------------------------------------------
 * ClearRealAngle, OBJECT.C:2105. Ferma la rotazione dove si trova.
 * -------------------------------------------------------------------------*/
void ClearRealAngle(T_OBJET *ptrobj)
{
    InitRealAngleConst(ptrobj->Beta, ptrobj->Beta, 0, &ptrobj->RealAngle);
}

/* ---------------------------------------------------------------------------
 * InitAnim, OBJECT.C:1140. Il cambio di animazione.
 *
 * LA RIGA CHE CONTA E' LA PRIMA USCITA: se il generico richiesto e' gia' quello
 * in corso non si fa NIENTE. Senza, tenendo premuto avanti l'animazione
 * ripartirebbe dal fotogramma zero a ogni quadro e Twinsen resterebbe congelato
 * sul primo passo, muovendosi comunque - il difetto che sembra "il cammino non
 * si anima".
 *
 * QUEL CHE NON C'E', dichiarato: le AZIONI per fotogramma (`PtrAnimAction` e
 * `GereAnimAction`, i suoni e i colpi appesi a un fotogramma). La nostra
 * `SearchAnim` legge il byte che dice se ci sono ma non ne espone il puntatore,
 * e chi le esegue e' roba di M5. Qui il campo si azzera in chiaro invece di
 * restare un puntatore vecchio: un puntatore alla fiche della stanza
 * PRECEDENTE sarebbe memoria riassegnata.
 * -------------------------------------------------------------------------*/
WORD InitAnim(UBYTE gennewanim, WORD flag, UBYTE gennextanim, WORD numobj)
{
    T_OBJET *ptrobj = &ListObjet[numobj];
    WORD newanim;

    if (ptrobj->Body == -1)
        return FALSE;
    if (ptrobj->Flags & SPRITE_3D)
        return FALSE;

    if ((gennewanim == ptrobj->GenAnim) && (ptrobj->Anim != -1))
        return TRUE;

    if (gennextanim == NO_ANIM) {
        if (ptrobj->FlagAnim != ANIM_ALL_THEN)
            gennextanim = ptrobj->GenAnim;
    }

    newanim = SearchAnim(gennewanim, numobj);
    if (newanim == -1)
        newanim = SearchAnim(GEN_ANIM_RIEN, numobj);

    /* Un'animazione ANIM_ALL_THEN non si interrompe: la nuova si PRENOTA e
     * partira' quando quella in corso sara' finita (il ramo in Body_AnimTick). */
    if (flag != ANIM_SET) {
        if (ptrobj->FlagAnim == ANIM_ALL_THEN) {
            ptrobj->NextGenAnim = gennewanim;
            return FALSE;
        }
    }

    if (flag == ANIM_INSERT) {
        flag = ANIM_ALL_THEN;
        gennextanim = ptrobj->GenAnim;

        if ((gennextanim == GEN_ANIM_LANCE)
            || (gennextanim == GEN_ANIM_TOMBE)
            || (gennextanim == GEN_ANIM_RECEPTION)
            || (gennextanim == GEN_ANIM_RECEPTION_2))
            gennextanim = GEN_ANIM_RIEN;
    }

    if (flag == ANIM_SET)
        flag = ANIM_ALL_THEN;

    if (ptrobj->Anim == -1) {
        /* `SetAnimObjet(0, ...)`: non c'e' una posa precedente da cui
         * raccordarsi, quindi la sorgente e' il fotogramma stesso. */
        ptrobj->AnimSrcFrame = 0;
        ptrobj->PtrPoseSrc = 0;
    } else {
        /* PRIMA di cambiare `Anim`: la fotografia e' della posa VECCHIA. */
        Body_SnapPose(numobj);
        /* E la sorgente diventa la fotografia SOLO se c'e' davvero. Se
         * `Body_SnapPose` ha rinunciato, un -1 qui sarebbe un numero di
         * fotogramma impossibile e fermerebbe il motore: meglio un raccordo
         * meno morbido che un TheEnd. */
        ptrobj->AnimSrcFrame = ptrobj->PtrPoseSrc ? -1 : 0;
    }
    ptrobj->AnimMemoTicks = TimerRef;

    ptrobj->Anim = newanim;
    ptrobj->GenAnim = gennewanim;
    ptrobj->NextGenAnim = gennextanim;
    ptrobj->PtrAnimAction = 0;

    ptrobj->FlagAnim = flag;
    ptrobj->Frame = 0;

    ptrobj->WorkFlags &= (WORD)~(OK_HIT + ANIM_END);
    ptrobj->WorkFlags |= NEW_FRAME;

    /* L'accumulatore del passo riparte da zero insieme all'animazione: senza,
     * il primo quadro della nuova sottrarrebbe il passo dell'ultima e Twinsen
     * farebbe un salto all'indietro grande quanto mezzo passo. */
    ptrobj->AnimStepBeta = 0;
    ptrobj->AnimStepX = 0;
    ptrobj->AnimStepY = 0;
    ptrobj->AnimStepZ = 0;

    return TRUE;
}

/* ---------------------------------------------------------------------------
 * Game_Frame - `DoDir(NUM_PERSO)`, ramo MOVE_MANUAL.
 *
 * Upstream `DoDir` gira su OGNI attore (PERSO.C:932, dentro il ciclo del
 * MainLoop) e i modi non manuali - FOLLOW, TRACK, RANDOM - li comanda lo
 * script. Qui c'e' solo l'eroe perche' gli altri modi vogliono l'interprete
 * LIFE, che e' M5. Non e' una semplificazione del ramo manuale: quello e'
 * tradotto per intero, meno le voci che dipendono da meccaniche che non
 * esistono ancora (armi, comportamenti, inventario).
 *
 * LE DUE ROTAZIONI, che e' la cosa da capire di questo ramo. Girando SUL POSTO
 * (`GenAnim == GEN_ANIM_RIEN`) il gioco non scrive `Beta`: lancia l'animazione
 * di giro e lascia che sia LEI a ruotare la radice, tramite la rotazione
 * maestra che `Body_AnimTick` applica. Girando MENTRE si cammina, invece,
 * l'angolo lo scrive `GetRealAngle` e l'animazione continua per conto suo. Sono
 * due meccanismi diversi sullo stesso tasto, e il discrimine e' l'animazione in
 * corso.
 *
 * NON C'E' ANCORA: i comportamenti (atletico/aggressivo/discreto, tasti 1-4 del
 * tastierino) e tutto quel che ne dipende - salto, colpi, arma, sabbia magica.
 * Il comportamento normale e' quello di inizio partita e non richiede
 * `SetComportement`, quindi `ActionNormal` si calcola come nel suo `case`.
 * `FlagClimbing` (la scala) e la caduta (`FALLING`) vogliono la collisione.
 * -------------------------------------------------------------------------*/
void Game_Frame(void)
{
    T_OBJET *ptrobj = &ListObjet[NUM_PERSO];
    WORD action_normal = FALSE;

    if (ptrobj->Body == -1)
        return;

    if (ptrobj->Move != MOVE_MANUAL)
        return;

    /* `case C_NORMAL` del comportamento, OBJECT.C:2196-2201. */
    if (MyFire & F_SPACE)
        action_normal = TRUE;

    if (!MyFire || action_normal) {
        if (MyJoy & (J_UP | J_DOWN))
            LastJoyFlag = FALSE;

        /* Il pad e' CAMBIATO mentre un'animazione comandata dal pad era in
         * corso: si torna a fermi. E' quel che ferma il cammino quando si
         * lascia il tasto - senza, Twinsen camminerebbe per sempre. */
        if (((MyJoy != LastMyJoy) || (MyFire != LastMyFire)) && LastJoyFlag)
            InitAnim(GEN_ANIM_RIEN, ANIM_REPEAT, NO_ANIM, NUM_PERSO);

        LastJoyFlag = FALSE;

        if (MyJoy & J_UP) {
            InitAnim(GEN_ANIM_MARCHE, ANIM_REPEAT, NO_ANIM, NUM_PERSO);
            LastJoyFlag = TRUE;
        }

        if (MyJoy & J_DOWN) {
            InitAnim(GEN_ANIM_RECULE, ANIM_REPEAT, NO_ANIM, NUM_PERSO);
            LastJoyFlag = TRUE;
        }

        if (MyJoy & J_LEFT) {
            LastJoyFlag = TRUE;
            if (ptrobj->GenAnim == GEN_ANIM_RIEN) {
                InitAnim(GEN_ANIM_GAUCHE, ANIM_REPEAT, NO_ANIM, NUM_PERSO);
            } else {
                if (!(ptrobj->WorkFlags & ANIM_MASTER_ROT))
                    ptrobj->Beta = GetRealAngle(&ptrobj->RealAngle);
            }
        }

        if (MyJoy & J_RIGHT) {
            LastJoyFlag = TRUE;
            if (ptrobj->GenAnim == GEN_ANIM_RIEN) {
                InitAnim(GEN_ANIM_DROITE, ANIM_REPEAT, NO_ANIM, NUM_PERSO);
            } else {
                if (!(ptrobj->WorkFlags & ANIM_MASTER_ROT))
                    ptrobj->Beta = GetRealAngle(&ptrobj->RealAngle);
            }
        }
    }

    /* In fondo, come upstream: l'arrivo per il quadro PROSSIMO si arma dopo
     * aver letto il valore di QUESTO. Invertire i due farebbe leggere
     * un'interpolazione appena armata, cioe' sempre il punto di partenza. */
    ManualRealAngle(ptrobj);
    LastMyJoy  = MyJoy;
    LastMyFire = MyFire;
}
