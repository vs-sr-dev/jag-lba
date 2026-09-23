#!/usr/bin/env python3
"""ANIM.HQR: i fotogrammi, e la posa fra due fotogrammi.

E' l'oracolo di M4, gemello di quel che engine_c/body_draw.c fa sul Jaguar.
Trascrive LIB386/LIB_3D/P_ANIM.C, con UNA differenza dichiarata: dove il gioco
legge l'orologio, qui il passo dell'interpolazione e' un ARGOMENTO.

PERCHE' IL TEMPO DIVENTA UN ARGOMENTO
-------------------------------------
Tutto il nostro collaudo e' a fotogramma fermo: l'oracolo calcola le attese di
UN passo e il banco confronta numeri, non filmati. L'animazione introduce il
tempo, e un'attesa che dipende da quando la si guarda non e' un'attesa.

Non e' pero' una nostra invenzione: il gioco ha gia' separato le due cose.
`SetInterDepObjet` (OBJECT.C:1712, passo di logica) possiede l'orologio, scrive
`memoTicks` e fa avanzare `Frame`; `SetInterAnimObjet2` (OBJECT.C:3005, passo di
disegno) calcola SOLO la posa, dallo stato che l'altra ha lasciato. La posa e'
gia' una funzione pura di

    (animazione, fotogramma di partenza, fotogramma d'arrivo, passo, durata)

e qui la si chiama cosi'. Sul Jaguar `passo` vale `TimerRef - memoTicks`, e la
funzione e' la stessa.

FORMATO DI UNA VOCE DI ANIM.HQR
-------------------------------
    +0  numero di fotogrammi
    +2  numero di gruppi
    +4  fotogramma di ricomincio (GetBouclageAnim)
    +8  i fotogrammi, lunghi `nb_groups * 8 + 8` l'uno

e un fotogramma:

    +0  durata in tick (timeToFrame)
    +2  spostamento X, Y, Z della RADICE - non e' posa, e' quanto l'attore si
        sposta nel mondo mentre l'animazione gira (il passo del cammino)
    +8  il record del gruppo 0: rotazione MAESTRA, alpha, beta, gamma
    +16 un record da 8 byte per ogni gruppo successivo: tipo, tre valori

Il record k del fotogramma pilota il gruppo k del corpo. Quello del gruppo 0 non
entra nella posa: e' la rotazione maestra, che agisce sul Beta dell'ATTORE
(OBJECT.C:1718) e quindi passa da un'altra strada.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import lba_paths
import os, sys, struct

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

TYPE_ROTATE    = 0
TYPE_TRANSLATE = 1

ANGLE_MASK = 1023
ANGLE_HALF = 512


def parse_anim(payload):
    """L'intestazione e i fotogrammi. Non copia niente: torna gli scostamenti."""
    nb_frames, nb_groups, _, loop = struct.unpack('<4H', payload[0:8])
    stride = nb_groups * 8 + 8
    need = 8 + stride * nb_frames
    if nb_frames == 0 or nb_groups == 0 or need > len(payload):
        return None
    return {'nb_frames': nb_frames, 'nb_groups': nb_groups, 'loop': loop,
            'stride': stride, 'data': payload}


def frame_off(anim, frame):
    return 8 + anim['stride'] * frame


def frame_time(anim, frame):
    """timeToFrame: quanti tick per arrivare a questo fotogramma."""
    o = frame_off(anim, frame)
    return struct.unpack('<h', anim['data'][o:o+2])[0]


def frame_step(anim, frame):
    """Lo spostamento della RADICE (AnimStepX/Y/Z). Serve al cammino, non alla
    posa: e' quel che sposta l'attore nel mondo mentre l'animazione gira."""
    o = frame_off(anim, frame)
    return struct.unpack('<3h', anim['data'][o+2:o+8])


def frame_master(anim, frame):
    """Il record del gruppo 0: rotazione maestra, alpha, beta, gamma."""
    o = frame_off(anim, frame)
    return struct.unpack('<4h', anim['data'][o+8:o+16])


def _rec(anim, frame, g):
    """Il record da 8 byte del gruppo `g` nel fotogramma `frame`."""
    o = frame_off(anim, frame) + 8 + g * 8
    return struct.unpack('<4h', anim['data'][o:o+8])


def _idiv(a, b):
    """La divisione del C TRONCA VERSO LO ZERO, il `//` di Python arrotonda
    verso -infinito. Qui i numeratori negativi sono la norma - un angolo che
    cala, un valore che scende - quindi la differenza non e' un caso limite: e'
    un'unita' di scarto a ogni interpolazione all'indietro. Stessa trappola
    delle tratte tagliate di S37."""
    q = abs(a) // abs(b)
    return q if (a < 0) == (b < 0) else -q


def patch_inter_step(last, new, cur, tot):
    """PatchInterStep, P_ANIM.C:98. Un valore che scivola da `last` a `new`."""
    if new != last:
        last = last + _idiv((new - last) * cur, tot)
    return ((last + 0x8000) & 0xFFFF) - 0x8000


def patch_inter_angle(last, target, cur, tot):
    """PatchInterAngle, P_ANIM.C:121. Un ANGOLO che scivola, per la via CORTA.

    Il giro e' 1024. Senza la correzione a meta' giro, passare da 1000 a 20
    farebbe percorrere 980 unita' all'indietro invece di 44 in avanti: il
    braccio girerebbe dalla parte sbagliata."""
    base = last & ANGLE_MASK
    diff = (target & ANGLE_MASK) - base
    if diff != 0:
        if diff < -ANGLE_HALF:
            diff += ANGLE_MASK + 1
        elif diff > ANGLE_HALF:
            diff -= ANGLE_MASK + 1
        base = (base + _idiv(diff * cur, tot)) & ANGLE_MASK
    return base


def frame_pose(anim, dest, src, cur, nb_groups_body):
    """SetInterAnimObjet2, P_ANIM.C:466. La posa dei gruppi, interpolata.

    `dest` e `src` sono numeri di fotogramma (nel gioco `src` e' un puntatore,
    perche' puo' stare in un'ALTRA animazione - e' cosi' che LBA1 raccorda due
    animazioni diverse; qui il banco resta dentro una sola).

    Torna `(posa, nuovo_fotogramma)`. `posa` e' una lista `(tipo, v1, v2, v3)`
    lunga quanto i gruppi del CORPO: quelli che l'animazione non pilota restano
    a riposo, che e' anche quel che l'archivio ha nei corpi grezzi.
    """
    nbg = min(anim['nb_groups'], nb_groups_body)
    pose = [(TYPE_ROTATE, 0, 0, 0)] * nb_groups_body
    tot = frame_time(anim, dest)

    if cur >= tot:
        # Il fotogramma e' arrivato: i record si copiano tali e quali.
        for g in range(nbg):
            pose[g] = _rec(anim, dest, g)
        return pose, True

    if tot == 0:
        tot = 1

    # Il gruppo 0 non si interpola: la sua rotazione e' quella MAESTRA e non
    # entra nella posa (P_ANIM.C:553-557, `objGroupPtr += 38`).
    for g in range(1, nbg):
        typ, d1, d2, d3 = _rec(anim, dest, g)
        _st, s1, s2, s3 = _rec(anim, src, g)

        # IL TIPO VIENE DAL FOTOGRAMMA D'ARRIVO, non da quello di partenza
        # (PatchType, P_ANIM.C:85): e' il fotogramma verso cui si va a dire
        # come si legge la terna.
        #
        # Ha una conseguenza VISIBILE e va tenuta: se un gruppo cambia tipo da
        # un fotogramma all'altro, i tre valori di PARTENZA si leggono con la
        # regola di ARRIVO. Percorso tutto l'archivio, capita una volta sola su
        # 3028 coppie - animazione 438, fotogramma 13, gruppo 1 - dove uno
        # spostamento di -109 diventa l'angolo 915 per la durata di
        # un'interpolazione. E' un difetto del gioco, non della trascrizione;
        # ripulirlo qui vorrebbe dire che il Jaguar mostra una posa che il DOS
        # non mostra.
        if typ == TYPE_ROTATE:
            pose[g] = (typ,
                       patch_inter_angle(s1, d1, cur, tot),
                       patch_inter_angle(s2, d2, cur, tot),
                       patch_inter_angle(s3, d3, cur, tot))
        elif typ == TYPE_TRANSLATE:
            pose[g] = (typ,
                       patch_inter_step(s1, d1, cur, tot),
                       patch_inter_step(s2, d2, cur, tot),
                       patch_inter_step(s3, d3, cur, tot))
        else:
            raise SystemExit('tipo di trasformazione %d: ANIM.HQR non ne '
                             'contiene' % typ)

    return pose, False


def search_anim(fiche, numanim):
    """SearchAnim, FICHE.C:529. Gemella di SearchBody: stessa fiche, comando 3
    invece di 1. L'attore nomina un'animazione GENERICA e la fiche dice quale
    voce di ANIM.HQR le corrisponde.

    Torna `(slot, ha_azioni)`, o `(-1, False)`."""
    i = 0
    while i < len(fiche):
        command = fiche[i]; i += 1
        if command == 255:
            break
        if command == 3:                       # ANIM
            numgene = fiche[i]; i += 1
            if numgene == numanim:
                i += 1                         # salta la dimensione
                slot = struct.unpack('<h', fiche[i:i+2])[0]
                return slot, bool(fiche[i+2])
            i += fiche[i]
            continue
        i += 1                                 # generico
        i += fiche[i]                          # dimensione
    return -1, False


if __name__ == '__main__':
    from hqr_decompress import parse_offsets, extract_entry

    path = sys.argv[1] if len(sys.argv) > 1 else \
        lba_paths.common('ANIM.HQR')
    with open(path, 'rb') as f:
        data = f.read()
    offs = parse_offsets(data)
    print('%d voci' % len(offs))
    for idx in [int(a) for a in sys.argv[2:]] or [0, 1, 2]:
        a = parse_anim(extract_entry(data, idx, offs))
        if not a:
            print('  %3d: non valida' % idx)
            continue
        print('  %3d: %3d fotogrammi, %2d gruppi, ricomincio %d, durate %s'
              % (idx, a['nb_frames'], a['nb_groups'], a['loop'],
                 [frame_time(a, f) for f in range(min(8, a['nb_frames']))]))
