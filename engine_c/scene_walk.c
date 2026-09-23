/* scene_walk.c - quel che resta dopo M3a: le ZONE e i due valori derivati.
 *
 * Fino a M2 questo file conteneva anche il cammino della struct SCENE, ed era
 * una duplicazione DICHIARATA di `LoadScene`, con la condizione di rimozione
 * scritta nella sua intestazione: serviva perche' M2 aveva bisogno delle zone -
 * che nella struct stanno DOPO gli attori - prima che il sistema degli attori
 * esistesse.
 *
 * A M3a la condizione e' soddisfatta: il cammino vero sta in `scene_load.c`,
 * riempie `ListObjet[]`, e di qui resta solo quel che il fondo e la camera
 * ricavano dalla scena gia' letta. La promessa e' stata mantenuta invece di
 * essere rinnovata, che e' il punto di scriverla.
 */
#include "hal.h"
#include "le.h"

/* COMMON.H */
#define SIZE_BRICK_XZ   512
#define DEMI_BRICK_XZ   256
#define SIZE_BRICK_Y    256
#define SIZE_T_ZONE      24

/* OBJECT.C:3436, lo switch su `ptrz->Type` */
#define ZONE_CHANGE_CUBE  0
#define ZONE_SET_CAMERA   1

T_SCENE Scene;

/* Decodifica una zona dai byte del blob. Upstream faceva
 * `ListZone = (T_ZONE *)PtrSce`, cioe' puntava la struct dentro il blob; qui i
 * campi si COPIANO decodificandoli, altrimenti ogni accesso successivo
 * erediterebbe disallineamento e ordine dei byte sbagliato (vedi le.h). */
static void zone_at(const le_byte *p, T_ZONE *z)
{
    z->X0   = LE16(p +  0);
    z->Y0   = LE16(p +  2);
    z->Z0   = LE16(p +  4);
    z->X1   = LE16(p +  6);
    z->Y1   = LE16(p +  8);
    z->Z1   = LE16(p + 10);
    z->Type = LE16(p + 12);
    z->Num  = LE16(p + 14);
    z->Info0 = LE16(p + 16);
    z->Info1 = LE16(p + 18);
    z->Info2 = LE16(p + 20);
    z->Info3 = LE16(p + 22);
}

int Scene_Zone(int index, T_ZONE *out)
{
    if (!Scene.valid || index < 0 || index >= Scene.NbZones)
        return 0;
    zone_at(Scene.ZonesRaw + (long)index * SIZE_T_ZONE, out);
    return 1;
}

/* ---------------------------------------------------------------------------
 * La bonifica dei due placeholder (M2), piu' la posizione dell'eroe (M3a).
 * -------------------------------------------------------------------------*/

void Scene_Derive(void)
{
    T_ZONE z;
    int i;

    Scene.NbChangeCube = 0;
    Scene.FirstCubeTarget = -1;

    if (!Scene.valid) {
        Scene.CamGX = Scene.CamGZ = Scene.CamGY = 0;
        return;
    }

    /* L'EROE PRIMA DELLA CAMERA, ed e' l'ordine di upstream, non un dettaglio.
     * ChangeCube (OBJECT.C:518-537) mette l'eroe nella sua nuova posizione e
     * SOLO DOPO ne ricava Start[XYZ]Cube. Entrando da una porta `CubeStartX/Y/Z`
     * e' gia' stata sostituita dalla posizione d'ingresso (Bg_EnterAt), quindi
     * qui l'eroe finisce dove dice la zona e la camera lo segue. Invertire i
     * due passi darebbe la stanza giusta inquadrata dal posto sbagliato. */
    ListObjet[NUM_PERSO].PosObjX = Scene.CubeStartX;
    ListObjet[NUM_PERSO].PosObjY = Scene.CubeStartY;
    ListObjet[NUM_PERSO].PosObjZ = Scene.CubeStartZ;
    ListObjet[NUM_PERSO].OldPosX = Scene.CubeStartX;
    ListObjet[NUM_PERSO].OldPosY = Scene.CubeStartY;
    ListObjet[NUM_PERSO].OldPosZ = Scene.CubeStartZ;

    /* Camera iniziale. La regola vera e' quella con cui il gioco la ricava
     * dalla posizione dell'oggetto seguito (OBJECT.C:577, PERSO.C:773):
     *
     *     StartXCube = (PosObjX + DEMI_BRICK_XZ) / SIZE_BRICK_XZ
     *
     * cioe' un arrotondamento al PIU' VICINO, non un troncamento. Il piano di
     * M2 diceva "camera init dai 120 valori del CSV", ma il CSV di S20 ha le
     * celle troncate (`world >> 9`): per la scena 0 darebbe (52, 53) invece di
     * (53, 54), un off-by-one su tutte e 120 le scene. La regola batte la
     * tabella.
     *
     * Una zona di tipo 1 (set pos camera) sovrascrive questo valore quando
     * l'eroe ci entra dentro, ma NON e' il valore iniziale: all'inizio l'eroe
     * non e' ancora stato testato contro le zone. */
    Scene.CamGX = (WORD)((Scene.CubeStartX + DEMI_BRICK_XZ) / SIZE_BRICK_XZ);
    Scene.CamGZ = (WORD)((Scene.CubeStartZ + DEMI_BRICK_XZ) / SIZE_BRICK_XZ);

    /* StartYCube, riga 578, subito sotto le altre due - e con un'ASIMMETRIA che
     * sembra un refuso e non lo e', perche' e' la stessa in tutte le copie del
     * gioco: la Y somma un brick INTERO dove X e Z ne sommano mezzo. Non e'
     * quindi un arrotondamento al piu' vicino, e' un troncamento alzato di un
     * livello. Per l'eroe della scena 0 (Y = 768) da' 4, cioe' il numero che
     * fino a S29 stava scritto a mano in CAM_GY_FIXED. Adesso e' una regola, e
     * vale anche dove l'eroe non parte a quell'altezza. */
    Scene.CamGY = (WORD)((Scene.CubeStartY + SIZE_BRICK_Y) / SIZE_BRICK_Y);

    /* Transizioni vere: le zone di tipo 0. `Num` e' la scena di destinazione,
     * `Info0/1/2` la posizione d'ingresso. Sostituiscono la mappa ciclica
     * N -> N+1, che per la scena 0 azzeccava per caso (le sue due zone puntano
     * entrambe alla scena 1) e sbagliava altrove. */
    for (i = 0; i < Scene.NbZones; i++) {
        if (!Scene_Zone(i, &z))
            break;
        if (z.Type != ZONE_CHANGE_CUBE)
            continue;
        if (Scene.NbChangeCube == 0) {
            Scene.FirstCubeTarget = z.Num;
            /* Dove si arriva, non solo dove si va. ChangeCube (OBJECT.C:522)
             * mette l'eroe in `posEroe - zona.X0 + zona.Info0` per asse, cioe'
             * conserva lo scostamento DENTRO la zona, e solo dopo ne ricava la
             * camera. Entrando dall'angolo della zona quello scostamento e'
             * zero e la posizione e' esattamente Info0/1/2. */
            Scene.FirstCubeEnterX = z.Info0;
            Scene.FirstCubeEnterY = z.Info1;
            Scene.FirstCubeEnterZ = z.Info2;
        }
        Scene.NbChangeCube++;
    }
}
