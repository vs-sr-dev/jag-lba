#!/usr/bin/env python3
"""JagLBA V6 / M2 - parser di riferimento della struct SCENE di LBA1.

A COSA SERVE
------------
Due cose, e la seconda e' quella che conta:

  1. e' la verita' di riferimento contro cui si valida il `LoadScene` scritto in
     C per il 68000: se i due parser non danno gli stessi numeri, uno dei due
     sbaglia e si sa subito quale guardare;
  2. genera `engine_c/scene_expect.h`, cosi' il confronto lo fa il programma e
     non l'occhio (stesso schema di M1, vedi tools/hqr_pack.py).

DA DOVE VIENE IL FORMATO
------------------------
NON da reverse engineering e non da TwinE: da `LoadScene()` in
`lba1-classic-community-main/SOURCES/DISKFUNC.C`, cioe' dal codice che scriveva
questi byte. Gli offset non sono un'ipotesi da confermare, sono una lettura.

Le due strutture in coda vengono da COMMON.H e DEFINES.H:
    T_ZONE  = 12 WORD = 24 byte   (X0 Y0 Z0 X1 Y1 Z1 Type Num Info0..3)
    T_TRACK =  3 WORD =  6 byte   (X Y Z)

IL CONTROLLO CHE RENDE IL PARSER CREDIBILE
------------------------------------------
La struct e' una sequenza di campi a lunghezza variabile (script Track e Life
per ogni attore, ognuno preceduto dalla sua dimensione). Percio' un errore di
un solo byte nel layout sfasa tutto il resto e il cursore non arriva in fondo.

Alla fine restano solo i T_TRACK, che nessuno salta: quindi i byte residui
devono valere **esattamente** `NbBrickTrack * 6`. Preteso su tutte e 120 le
scene, e' un controllo che un layout sbagliato non puo' superare per caso.

ORDINE DEI BYTE E ALLINEAMENTO: UNA SOLA DECISIONE
--------------------------------------------------
Questa struct alterna byte e word, quindi le word cadono a offset DISPARI. Sul
386 del 1994 non costava niente; sul 68000 una `move.w` a indirizzo dispari e'
un Address Error.

Il 68000 deve percio' leggere queste word **byte per byte** comunque, per
allineamento, indipendentemente dall'ordine dei byte. E se le legge byte per
byte, decodificarle little endian costa ZERO in piu': sono gli stessi due
accessi. Ecco perche' la questione LE->BE rinviata da M1 si chiude qui, e si
chiude con gli accessori a runtime: uno swap offline avrebbe richiesto un parser
per formato E avrebbe lasciato in piedi il problema dell'allineamento.

USO
    python tools/scene_parse.py            # valida tutte le scene + genera
    python tools/scene_parse.py --scene 0  # dettaglio di una scena
"""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import anim_ref

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONTAINER = os.path.join(REPO, 'cart', 'data_v6.bin')

HQR_SCENE = 0
HQR_BODY = 1
HQR_FILE3D = 4
HDR_SIZE = 16
ARCH_DIR_ENTRY = 12
ENTRY_SIZE = 8

SIZE_BRICK_XZ = 512        # COMMON.H
DEMI_BRICK_XZ = 256
SIZE_BRICK_Y = 256
SIZE_CUBE_X = 64

ZONE_CHANGE_CUBE = 0       # OBJECT.C:3436 "case 0: change cube perso"
ZONE_SET_CAMERA  = 1       # OBJECT.C           "case 1: set pos camera"

SPRITE_3D = 1024           # COMMON.H:160 - non e' un corpo 3D, e' una bitmap
FILE_3D_NORMAL = 0         # DEFINES.H:73 - fiche dell'eroe, comportamento normale
NO_BODY = 255              # sentinella "nessun corpo" nel campo GenBody

# Il generico che l'eroe porta a inizio partita. NON e' GEN_BODY_NORMAL (che
# sulla fiche 0 e' la tunica): la camiciola da prigioniero e' il generico 3, e a
# metterlo e' lo script LIFE dell'eroe (`LM_BODY 3`, opcode 17, in fondo alla
# scena 0). L'interprete LIFE arriva a M5; fino ad allora questo numero e' un
# SEGNAPOSTO dichiarato, scritto uguale qui e in engine_c/scene_load.c.
HERO_GEN_BODY_START = 3

# Le scene in coda ai due passi che M2 gia' percorreva. La 2 e' quella su cui si
# verifica il criterio di M3: la scena 0 non ha NESSUN attore dentro i 640x480
# (misurato: 0 su 22, l'eroe e' nella sua stanza e gli altri stanno altrove) e
# la 1, entrandoci dalla porta, ne mostra due ma sono entrambi SPRITE_3D. La 2
# ne ha tre con corpo 3D piu' tre sprite.
EXTRA_STEPS = (2,)

HQR_ANIMS = 2

# ---------------------------------------------------------------------------
# LA POSA DI OGNI PASSO (M4). LA RISPOSTA ALLA DOMANDA DEL TEMPO.
# ---------------------------------------------------------------------------
# Tutto il collaudo e' a fotogramma fermo: l'oracolo calcola le attese di UN
# passo e il banco confronta numeri, non filmati. L'animazione introduce il
# tempo, e un'attesa che dipende da quando la si guarda non e' un'attesa.
#
# La coppia qui sotto rende la posa una funzione del PASSO, come gia' lo sono
# scena e ingresso: `(fotogramma d'arrivo, passo dell'interpolazione)`. Il
# fotogramma di partenza e' quello prima, e i tick si aggiustano perche'
# `TimerRef - AnimMemoTicks` valga esattamente il passo scelto - cosi' nessuno
# deve falsificare l'orologio.
#
# I TRE PASSI COPRONO I TRE RAMI, apposta:
#   scena 0  passo oltre la durata -> il fotogramma e' ARRIVATO, i record si
#            copiano tali e quali (e' anche il ramo di SetAnimObjet)
#   scena 1  passo ZERO            -> l'interpolazione non si e' mossa, e la
#            posa deve venire identica a quella del fotogramma di PARTENZA.
#            E' l'invariante che distingue "interpola" da "interpola a caso"
#   scena 2  passo a meta'         -> interpolazione vera, con gli angoli che
#            girano per la via corta e le divisioni che troncano
#
# Il movimento vero si guarda a occhio sul Jaguar; la CORRETTEZZA si verifica
# su queste tre pose scelte.
# Il passo della scena 0 e' 0x9000 e NON un numero comodo: e' deliberatamente
# oltre 32767. Il ramo "fotogramma arrivato" si esercitava anche con 999, ma solo
# un valore che SFORA la word con segno prova la riduzione a 16 bit che il C fa
# sul trascorso - ed e' esattamente il difetto che in S38 e' costato due cicli
# sul ferro (posa tenuta ferma abbastanza a lungo -> trascorso negativo ->
# "non ancora arrivato" -> interpolazione con passo -32768). Il banco non poteva
# vederlo perche' non lascia mai passare il tempo fra il fissare la posa e il
# disegnarla: questo numero e' il suo sostituto.
#
# E IL FOTOGRAMMA DI QUEL PASSO NON PUO' ESSERE LO ZERO (S41). Con dest 0 il
# fotogramma di PARTENZA e' lo stesso di arrivo (vedi actor_pose_frames), e
# interpolare da se' a se' da' lo stesso risultato del ramo "arrivato": i due
# rami COINCIDONO, quindi la scelta fra loro non e' osservabile e il 0x9000 non
# provava niente. Misurato: con `if (cur >= tot)` al posto del confronto a 32
# bit - cioe' il difetto di S38 rimesso per intero - il banco passava tutte e
# ventiquattro le sonde. Con dest 3 (partenza 2, record diversi) cade, ed e'
# quel che rende questa riga una sonda invece di un'intenzione.
ANIM_STEPS = ((3, 0x9000), (3, 0), (3, 5))


def anim_step_params(i):
    """La coppia del passo `i`, con l'ultima ripetuta se i passi crescono."""
    return ANIM_STEPS[i] if i < len(ANIM_STEPS) else ANIM_STEPS[-1]


def anim_frames(buf, slot):
    """Quanti fotogrammi ha la voce `slot` di ANIM.HQR, 0 se non e' valida."""
    a = anim_payload(buf, slot)
    if a is None:
        return 0
    info = anim_ref.parse_anim(a)
    return info['nb_frames'] if info else 0


def anim_payload(buf, slot):
    if slot < 0:
        return None
    ents = container_entries(buf, HQR_ANIMS)
    if slot >= len(ents):
        return None
    off, size = ents[slot]
    return container_payload(buf, HQR_ANIMS, off, size)


def actor_pose_frames(buf, actor, step_i):
    """I due fotogrammi e il passo che questo attore assume al passo `step_i`.

    Il fotogramma si prende MODULO la lunghezza della propria animazione: ogni
    attore ne ha una sua e "fotogramma 3" non esiste per tutti. E' una scelta
    del banco, non del gioco - li' ogni attore avanza per conto suo - ed e' quel
    che rende il passo una cosa sola invece di una tabella per attore.

    Torna `(dest, src, passo)`, o None se l'attore non ha animazione.
    """
    if getattr(actor, 'anim', -1) < 0:
        return None
    nbf = anim_frames(buf, actor.anim)
    if nbf == 0:
        return None
    frame, cur = anim_step_params(step_i)
    dest = frame % nbf
    # Il fotogramma di PARTENZA e' quello prima. All'inizio dell'animazione il
    # gioco non ne ha uno (Offset_Source == 0) e usa lo stesso di arrivo,
    # P_ANIM.C:502-506: interpolare da se' a se' non muove niente, che e'
    # giusto - un'animazione appena partita non viene da nessuna parte.
    src = dest - 1 if dest > 0 else dest
    return dest, src, cur


# ---------------------------------------------------------------------------
# Lettore del contenitore JHQR (le stesse regole che usa il 68000)
# ---------------------------------------------------------------------------
def container_entries(buf, archive):
    toff, n, _fl = struct.unpack_from('>III', buf,
                                      HDR_SIZE + archive * ARCH_DIR_ENTRY)
    out = []
    for i in range(n):
        off, size = struct.unpack_from('>II', buf, toff + i * ENTRY_SIZE)
        out.append((off, size))
    return out


# Bit 0 del campo flag: i payload dell'archivio sono LZSS con la loro
# intestazione da 10 byte. Da S30 il contenitore non e' piu' tutto in chiaro,
# e chi lo legge senza saperlo interpreta byte compressi come struct - cioe'
# non fallisce, peggio: produce numeri plausibili.
ARCH_FLAG_LZSS = 1


def container_payload(buf, archive, off, size):
    """La voce come la vede il 68000 dopo HQR_Get: decompressa se serve."""
    _toff, _n, fl = struct.unpack_from('>III', buf,
                                       HDR_SIZE + archive * ARCH_DIR_ENTRY)
    if size == 0:
        return None
    if not (fl & ARCH_FLAG_LZSS):
        return buf[off:off + size]
    import hqr_decompress as lzss
    orig, comp, ctype = lzss.read_entry_header(buf, off)
    body = buf[off + 10:off + 10 + comp]
    return body[:orig] if ctype == 0 else lzss.decompress_lzss(body, orig, ctype)


class Reader:
    """Legge la struct come la leggeva il gioco: sequenziale, little endian,
    senza vincoli di allineamento."""

    def __init__(self, b):
        self.b = b
        self.p = 0

    def u8(self):
        v = self.b[self.p]
        self.p += 1
        return v

    def u16(self):
        v = self.b[self.p] | (self.b[self.p + 1] << 8)
        self.p += 2
        return v

    def s16(self):
        v = self.u16()
        return v - 0x10000 if v >= 0x8000 else v

    def skip(self, n):
        self.p += n


class Scene:
    pass


def parse(payload):
    """Traduzione 1:1 di LoadScene(), DISKFUNC.C:43-241."""
    r = Reader(payload)
    s = Scene()

    s.island        = r.u8()          # Island
    s.game_over     = r.u8()          # GameOverCube
    r.s16(); r.s16()                  # due GET_WORD scartati anche upstream
    s.alpha_light   = r.s16()
    s.beta_light    = r.s16()

    s.ambience = []
    for _ in range(4):
        s.ambience.append((r.s16(), r.s16(), r.s16()))   # sample, repeat, rnd
    s.second_min    = r.s16()
    s.second_ecart  = r.s16()
    s.jingle        = r.u8()          # CubeJingle (musica della scena)

    s.hero_x        = r.s16()         # CubeStartX
    s.hero_y        = r.s16()
    s.hero_z        = r.s16()

    r.skip(r.s16())                   # script Track dell'eroe
    r.skip(r.s16())                   # script Life dell'eroe

    s.nb_objets     = r.s16()
    s.actors = []
    for _ in range(1, s.nb_objets):
        a = Scene()
        a.flags        = r.s16()
        a.index_file3d = r.s16()
        a.gen_body     = r.u8()
        a.gen_anim     = r.u8()
        a.sprite       = r.s16()
        a.x            = r.s16()
        a.y            = r.s16()
        a.z            = r.s16()
        a.hit_force    = r.u8()
        a.option_flags = r.s16()
        a.beta         = r.s16()
        a.srot         = r.s16()
        a.move         = r.s16()
        a.info         = [r.s16() for _ in range(4)]
        a.nb_bonus     = r.u8()
        a.coul         = r.u8()
        a.armure       = r.u8()
        a.life         = r.u8()
        # Gli script si SALTANO come prima; qui si annota solo dove stanno e
        # quanto sono lunghi. Serve a chiedere "questo attore ha un
        # comportamento?" senza rileggere il payload con un secondo parser
        # (S36: l'indagine sulle frecce INVISIBLE). Nessun campo letto cambia.
        a.track_len    = r.s16(); a.track_off = r.p; r.skip(a.track_len)
        a.life_len     = r.s16(); a.life_off  = r.p; r.skip(a.life_len)
        s.actors.append(a)

    s.nb_zones = r.s16()
    s.zones = []
    for _ in range(s.nb_zones):
        z = Scene()
        (z.x0, z.y0, z.z0, z.x1, z.y1, z.z1,
         z.type, z.num, z.i0, z.i1, z.i2, z.i3) = [r.s16() for _ in range(12)]
        s.zones.append(z)

    s.nb_tracks = r.s16()
    s.track_off = r.p
    s.tail = len(payload) - r.p
    s.exact = (s.tail == s.nb_tracks * 6)
    s.payload = payload           # per leggere gli script agli offset annotati
    return s


def camera_init(s):
    """Camera iniziale, con la regola VERA del gioco.

    OBJECT.C:577 e PERSO.C:773 la ricavano dalla posizione dell'oggetto seguito:
        StartXCube = (PosObjX + DEMI_BRICK_XZ) / SIZE_BRICK_XZ

    E' un arrotondamento al piu' vicino, NON un troncamento. Il CSV di S20 ha le
    celle troncate (`world >> 9`), quindi per la camera darebbe una cella di
    meno: per la scena 0 cella (52,53) ma camera (53,54). Prendere il CSV
    ("camera init dai 120 valori del CSV", come diceva il piano) avrebbe
    introdotto un off-by-one su tutte e 120 le scene.

    Una zona di tipo 1 (set pos camera) sovrascrive questo valore quando l'eroe
    ci entra dentro, ma NON e' il valore iniziale: all'inizio l'eroe non e'
    ancora stato testato contro le zone.
    """
    return camera_init_xz(s.hero_x, s.hero_z)


def camera_init_xz(x, z):
    """La stessa regola applicata a una posizione qualunque.

    Non e' un'astrazione gratuita: entrando in una stanza da una change-cube
    l'eroe NON e' al punto di partenza della stanza (OBJECT.C:522), quindi la
    camera si ricava da dove e' finito davvero.
    """
    return ((x + DEMI_BRICK_XZ) // SIZE_BRICK_XZ,
            (z + DEMI_BRICK_XZ) // SIZE_BRICK_XZ)


def camera_init_y(s):
    """Il livello verticale della camera, StartYCube (OBJECT.C:578).

    Stessa riga delle altre due ma con una ASIMMETRIA che sembra un errore di
    battitura e non lo e', perche' e' cosi' in tutte e tre le copie del gioco:

        StartXCube = (PosObjX + DEMI_BRICK_XZ) / SIZE_BRICK_XZ   <- mezzo brick
        StartYCube = (PosObjY + SIZE_BRICK_Y)  / SIZE_BRICK_Y    <- brick INTERO
        StartZCube = (PosObjZ + DEMI_BRICK_XZ) / SIZE_BRICK_XZ   <- mezzo brick

    Quindi la Y non e' arrotondata al piu' vicino: e' troncata e poi alzata di
    un livello. Per l'eroe della scena 0 (Y = 768) da' 4, che e' il valore che
    fino a S29 stava scritto a mano in CAM_GY_FIXED. Adesso e' una regola, e
    vale anche per le scene in cui l'eroe non parte a quell'altezza.
    """
    return camera_init_y_of(s.hero_y)


def camera_init_y_of(y):
    return (y + SIZE_BRICK_Y) // SIZE_BRICK_Y


def change_cubes(s):
    """Le transizioni VERE: zone di tipo 0. Sostituiscono la mappa ciclica
    N -> N+1 inventata in tools/cart_pack_all.py (righe 348-360)."""
    return [z for z in s.zones if z.type == ZONE_CHANGE_CUBE]


# ---------------------------------------------------------------------------
# I CORPI: dalla fiche FILE3D, non da una scelta nostra
# ---------------------------------------------------------------------------
# Un attore non nomina uno slot di BODY.HQR: nomina un corpo GENERICO, e la sua
# fiche FILE3D dice a quale slot corrisponde. Trascrizione di SearchBody
# (FICHE.C:585), la stessa che gira sul Jaguar in engine_c/scene_load.c.
#
# Formato della fiche, un blocco alla volta fino al terminatore 255:
#     [comando][generico][dimensione][carico...]
# comando 1 = BODY. Trovato il generico giusto, i due byte dopo la dimensione
# sono lo slot HQR.
def search_body(fiche, numbody):
    """Slot di BODY.HQR per un corpo generico, -1 se la fiche non ce l'ha."""
    if fiche is None:
        return -1
    p = 0
    while p < len(fiche) and fiche[p] != 255:
        cmd = fiche[p]; p += 1
        if cmd == 1:
            numgene = fiche[p]; p += 1
            if numgene == numbody:
                p += 1                       # salta la dimensione
                return fiche[p] | (fiche[p + 1] << 8)
            p += fiche[p]
            continue
        p += 1                               # generico
        p += fiche[p]                        # dimensione
    return -1


def _fiche(buf, index):
    if index is None or index < 0:
        return None
    ents = container_entries(buf, HQR_FILE3D)
    if index >= len(ents):
        return None
    off, size = ents[index]
    return container_payload(buf, HQR_FILE3D, off, size)


class Actor:
    pass


def resolve_actors(buf, s, enter=None):
    """ListObjet[] come lo costruisce il Jaguar: l'eroe in slot 0 e gli attori
    della scena dietro, ognuno col corpo che la sua fiche dichiara.

    `enter` sostituisce la posizione dell'eroe quando si entra da una porta,
    esattamente come fa Bg_EnterAt prima di Scene_Derive.
    """
    out = []

    # L'eroe NON e' un attore della scena: e' stato persistente. `ChangeCube`
    # (OBJECT.C:518-537) azzera Move, ZoneSce e i tre offset degli script, e non
    # tocca nient'altro - in particolare non tocca Beta, che infatti nel gioco
    # si conserva attraversando le porte (si esce verso NE guardando NE, e nella
    # stanza nuova si guarda ancora NE).
    #
    # Qui vale 0 perche' finora nessuno lo ha girato: l'eroe non cammina fino a
    # M6. Da li' in poi questo riferimento dovra' PORTARSELO da un passo al
    # successivo invece di ricostruirlo, altrimenti dira' che il Jaguar sbaglia
    # proprio dove il Jaguar fa la cosa giusta.
    hero = Actor()
    hero.flags = 0                            # InitObject: nessun flag
    hero.gen_body = HERO_GEN_BODY_START       # segnaposto, vedi la costante
    hero.gen_anim = 0                         # GEN_ANIM_RIEN
    hero.sprite = 0
    hero.beta = 0
    hero.x, hero.y, hero.z = (enter if enter is not None
                              else (s.hero_x, s.hero_y, s.hero_z))
    hero.info = [0, 0, 0, 0]
    hero.body = search_body(_fiche(buf, FILE_3D_NORMAL), hero.gen_body)
    hero.anim = anim_ref.search_anim(_fiche(buf, FILE_3D_NORMAL),
                                     hero.gen_anim)[0]
    out.append(hero)

    for a in s.actors:
        o = Actor()
        o.flags = a.flags
        o.gen_body = a.gen_body
        o.gen_anim = a.gen_anim
        o.sprite = a.sprite
        o.beta = a.beta
        o.x, o.y, o.z = a.x, a.y, a.z
        # Info0..3: per una PORTA sono il rettangolo di ritaglio fisso
        # (OBJECT.C:3056). Per tutti gli altri non li legge nessuno.
        o.info = list(a.info)
        o.anim = -1
        if (a.flags & SPRITE_3D) or a.gen_body == NO_BODY:
            o.body = -1                       # percorso sprite, o nessun corpo
        else:
            fiche = _fiche(buf, a.index_file3d)
            o.body = search_body(fiche, a.gen_body)
            # M4: l'animazione esce dalla STESSA fiche del corpo, comando 3
            # invece di 1 (SearchAnim, FICHE.C:529). Se il generico non c'e',
            # il gioco ripiega su GEN_ANIM_RIEN come a fine animazione
            # (OBJECT.C:1765-1769); se manca anche quella l'attore resta fermo.
            o.anim = anim_ref.search_anim(fiche, o.gen_anim)[0]
            if o.anim < 0 and o.gen_anim != 0:
                o.gen_anim = 0
                o.anim = anim_ref.search_anim(fiche, 0)[0]
        out.append(o)
    return out


def actor_probes(actors):
    """Le sonde di M3a. Due somme che coprono OGNI campo letto, piu' cinque
    valori isolati che dicono DOVE guardare quando una somma non torna: una
    somma sola direbbe soltanto "no"."""
    s1 = s2 = 0
    for o in actors:
        for w in (((o.flags & 0xFFFF) << 16) | ((o.gen_body & 0xFF) << 8)
                  | (o.gen_anim & 0xFF),
                  ((o.x & 0xFFFF) << 16) | (o.z & 0xFFFF),
                  ((o.y & 0xFFFF) << 16) | (o.beta & 0xFFFF),
                  ((o.body & 0xFFFF) << 16) | (o.sprite & 0xFFFF)):
            s1 = (s1 + w) & 0xFFFFFFFF
            s2 = (s2 + s1) & 0xFFFFFFFF

    n3d = sum(1 for o in actors if o.body >= 0)
    nspr = sum(1 for o in actors if o.flags & SPRITE_3D)
    hero = actors[0]

    # Il primo attore CON UN CORPO che non sia l'eroe. E' quello su cui il
    # criterio di M3 si gioca: due corpi diversi a schermo insieme.
    first = next((i for i, o in enumerate(actors) if i and o.body >= 0), 0)
    fo = actors[first]

    return [
        ('COUNTS',  ((len(actors) & 0xFFFF) << 16) | n3d,
         'NbObjets<<16 | attori con un corpo'),
        ('SPRITES', ((nspr & 0xFFFF) << 16) | (len(actors) - n3d - nspr),
         'SPRITE_3D<<16 | senza corpo ne\' sprite'),
        ('HERO',    ((hero.body & 0xFFFF) << 16) | (hero.gen_body & 0xFFFF),
         'eroe: slot BODY.HQR<<16 | generico'),
        ('HERO_XZ', ((hero.x & 0xFFFF) << 16) | (hero.z & 0xFFFF),
         'eroe: X<<16 | Z'),
        ('FIRST',   ((first & 0xFF) << 24) | ((fo.body & 0xFF) << 16)
         | (fo.beta & 0xFFFF),
         'primo attore con corpo: n<<24 | slot<<16 | Beta'),
        ('FIRST_XZ', ((fo.x & 0xFFFF) << 16) | (fo.z & 0xFFFF),
         'quell\'attore: X<<16 | Z'),
        ('SUM1',    s1, 'Fletcher s1 su tutti gli attori'),
        ('SUM2',    s2, 'Fletcher s2 su tutti gli attori'),
    ]


def walk_steps(first=0):
    """I passi che il Jaguar percorre col tasto A, in UN SOLO POSTO.

    Li consumano due generatori diversi (bg_ref.py per il fondo, questo file per
    gli attori) e devono descrivere la stessa sequenza: due liste separate che
    divergono darebbero due maschere piene di due stanze diverse, che e'
    esattamente il guasto che l'array di passi era stato introdotto per
    rendere visibile.

    Torna [(scena, ingresso o None)]. `None` = stanza caricata da capo dal suo
    punto di partenza; una terna = ingresso da una change-cube.
    """
    _buf, _entries, scenes = load_all()
    steps = [(first, None)]
    s = scenes[first]
    if s is not None:
        cc = change_cubes(s)
        if cc:
            steps.append((cc[0].num, (cc[0].i0, cc[0].i1, cc[0].i2)))
    for num in EXTRA_STEPS:
        steps.append((num, None))
    return steps


def gen_actor_expect(buf, scenes, steps):
    rows = []
    for num, enter in steps:
        s = scenes[num]
        rows.append(('scena %d, %s' % (num, 'iniziale' if enter is None
                                       else 'ingresso da change-cube %s'
                                            % (enter,)),
                     actor_probes(resolve_actors(buf, s, enter))))

    names = [n for n, _v, _l in rows[0][1]]
    out = ['/* GENERATO DA tools/scene_parse.py - NON MODIFICARE A MANO.\n',
           ' *\n',
           ' * Attese delle sonde di M3a: gli attori che il LoadScene in C deve\n',
           ' * ricavare dalla cartuccia, un passo per stanza visitata. I corpi\n',
           ' * NON sono scelti qui: escono da SearchBody sulla fiche FILE3D di\n',
           ' * ogni attore, che e\' la stessa regola che gira sul Jaguar.\n',
           ' */\n',
           '#ifndef JAG_ACTOR_EXPECT_H\n#define JAG_ACTOR_EXPECT_H\n\n']
    for i, n in enumerate(names):
        out.append('#define AC_%-10s %d\n' % (n, i))
    out.append('\n#define AC_EXPECT_COUNT %d\n' % len(names))
    out.append('#define AC_EXPECT_ALL   0x%02XUL\n' % ((1 << len(names)) - 1))
    out.append('#define AC_EXPECT_STEPS %d\n\n' % len(rows))
    for i, (desc, _r) in enumerate(rows):
        out.append('/* passo %d: %s */\n' % (i, desc))
    out.append('\nstatic const unsigned long ActorExpect[AC_EXPECT_STEPS]'
               '[AC_EXPECT_COUNT] = {\n')
    for desc, r in rows:
        out.append('    { /* %s */\n' % desc)
        for name, value, label in r:
            out.append('        0x%08XUL,  /* %-8s %s */\n'
                       % (value & 0xFFFFFFFF, name, label))
        out.append('    },\n')
    out.append('};\n\n#endif\n')
    return ''.join(out)


def load_all():
    with open(CONTAINER, 'rb') as f:
        buf = f.read()
    entries = container_entries(buf, HQR_SCENE)
    scenes = []
    for off, size in entries:
        p = container_payload(buf, HQR_SCENE, off, size)
        scenes.append(parse(p) if p else None)
    return buf, entries, scenes


def report(s, idx):
    gx, gz = camera_init(s)
    cc = change_cubes(s)
    cam_zones = [z for z in s.zones if z.type == ZONE_SET_CAMERA]
    print('scena %d' % idx)
    print('  isola %d, musica %d, luce alpha/beta %d/%d'
          % (s.island, s.jingle, s.alpha_light, s.beta_light))
    print('  eroe mondo (%d, %d, %d) -> cella (%d, %d, %d)'
          % (s.hero_x, s.hero_y, s.hero_z,
             s.hero_x // SIZE_BRICK_XZ, s.hero_y // 256, s.hero_z // SIZE_BRICK_XZ))
    print('  camera iniziale (gx, gz) = (%d, %d)' % (gx, gz))
    print('  %d attori, %d zone (%d change-cube, %d camera), %d track'
          % (s.nb_objets, s.nb_zones, len(cc), len(cam_zones), s.nb_tracks))
    for z in cc:
        print('     change-cube -> scena %3d   box x %d..%d  z %d..%d   '
              'ingresso (%d, %d, %d)'
              % (z.num, z.x0, z.x1, z.z0, z.z1, z.i0, z.i1, z.i2))
    print('  coda: %d byte residui, %d track x 6 = %d  -> %s'
          % (s.tail, s.nb_tracks, s.nb_tracks * 6,
             'ESATTO' if s.exact else 'NON TORNA'))


def gen_expect(scenes):
    """Attese per le sonde di M2, sulla scena 0."""
    s = scenes[0]
    gx, gz = camera_init(s)
    cc = change_cubes(s)
    first = cc[0] if cc else None
    lines = [
        '/* GENERATO DA tools/scene_parse.py - NON MODIFICARE A MANO.\n',
        ' *\n',
        ' * Attese delle sonde di M2: quel che il LoadScene in C deve ricavare\n',
        ' * dalla scena 0 leggendo la cartuccia. Calcolate dal parser di\n',
        ' * riferimento, che a sua volta e\' la trascrizione di LoadScene() in\n',
        ' * lba1-classic-community-main/SOURCES/DISKFUNC.C.\n',
        ' */\n',
        '#ifndef JAG_SCENE_EXPECT_H\n#define JAG_SCENE_EXPECT_H\n\n',
        '#define SC_EXPECT_HERO_XZ    0x%08XUL  /* eroe X<<16 | Z */\n'
        % (((s.hero_x & 0xFFFF) << 16) | (s.hero_z & 0xFFFF)),
        '#define SC_EXPECT_HERO_Y     0x%08XUL\n' % (s.hero_y & 0xFFFF),
        '#define SC_EXPECT_CAM_GXGZ   0x%08XUL  /* camera gx<<16 | gz */\n'
        % ((gx << 16) | gz),
        '#define SC_EXPECT_COUNTS     0x%08XUL  /* attori<<16 | zone */\n'
        % ((s.nb_objets << 16) | s.nb_zones),
        '#define SC_EXPECT_TRACKS     0x%08XUL  /* change-cube<<16 | track */\n'
        % ((len(cc) << 16) | s.nb_tracks),
        '#define SC_EXPECT_CC0        0x%08XUL  /* prima change-cube: scena<<16 | tipo */\n'
        % (((first.num & 0xFFFF) << 16) if first else 0),
        '#define SC_EXPECT_JINGLE     0x%08XUL  /* isola<<16 | musica */\n'
        % ((s.island << 16) | s.jingle),
        '#define SC_EXPECT_TAIL_OK    0x%08XUL  /* 1 = il parser e\' arrivato in fondo esatto */\n'
        % (1 if s.exact else 0),
        '\n#define SC_EXPECT_COUNT 8\n',
        '#define SC_EXPECT_ALL   0x%02XUL\n' % 0xFF,
        '\n#endif\n',
    ]
    return ''.join(lines)


def main():
    buf, entries, scenes = load_all()

    if '--scene' in sys.argv:
        idx = int(sys.argv[sys.argv.index('--scene') + 1])
        report(scenes[idx], idx)
        return

    bad = []
    tot_zones = tot_cc = tot_actors = 0
    for i, s in enumerate(scenes):
        if s is None:
            continue
        if not s.exact:
            bad.append(i)
        tot_zones += s.nb_zones
        tot_cc += len(change_cubes(s))
        tot_actors += s.nb_objets

    n = sum(1 for s in scenes if s)
    print('scene_parse: %d scene lette dal contenitore' % n)
    print('  cursore in fondo ESATTO: %d su %d' % (n - len(bad), n))
    if bad:
        print('  NON TORNA su: %s' % bad)
        print('  Il layout e\' sbagliato: un errore di un byte sfasa tutto il')
        print('  resto, quindi non e\' un problema di quelle scene.')
        raise SystemExit(1)
    print('  %d attori, %d zone di cui %d change-cube, in totale'
          % (tot_actors, tot_zones, tot_cc))
    print()
    report(scenes[0], 0)

    path = os.path.join(REPO, 'engine_c', 'scene_expect.h')
    with open(path, 'w', newline='\n') as f:
        f.write(gen_expect(scenes))
    print()
    print('scene_parse: scritto engine_c/scene_expect.h')

    steps = walk_steps(0)
    print()
    print('attori, un passo per stanza visitata:')
    for num, enter in steps:
        actors = resolve_actors(buf, scenes[num], enter)
        n3d = sum(1 for o in actors if o.body >= 0)
        nspr = sum(1 for o in actors if o.flags & SPRITE_3D)
        print('  scena %3d %-28s %2d attori, %d con corpo, %d sprite, '
              'eroe -> BODY %d'
              % (num, 'iniziale' if enter is None else 'da change-cube',
                 len(actors), n3d, nspr, actors[0].body))
        for i, o in enumerate(actors):
            if i and o.body >= 0:
                print('       attore %2d -> BODY %3d  generico %d  '
                      'mondo (%d, %d, %d) beta %d'
                      % (i, o.body, o.gen_body, o.x, o.y, o.z, o.beta))

    path = os.path.join(REPO, 'engine_c', 'actor_expect.h')
    with open(path, 'w', newline='\n') as f:
        f.write(gen_actor_expect(buf, scenes, steps))
    print()
    print('scene_parse: scritto engine_c/actor_expect.h (%d passi)' % len(steps))


if __name__ == '__main__':
    main()
