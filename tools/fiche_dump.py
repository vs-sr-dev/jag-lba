#!/usr/bin/env python3
"""Una fiche FILE3D per intero: corpi, animazioni e le AZIONI attaccate ai
fotogrammi.

PERCHE' ESISTE
--------------
Cercando cosa fossero le 79 frecce INVISIBLE (S36) e' venuto fuori che il
lancio dei proiettili in LBA1 NON sta negli script life/track: sta nella fiche,
come azione agganciata a un fotogramma di un'animazione (FICHE.C:209, caso 8
"throw"). E la direzione del tiro e' `ptrobj->Beta + scostamento`, cioe'
l'orientamento dell'attore - che e' esattamente quel che la freccia mostra.

Quindi per sapere cosa fa un attore non basta leggerne gli script: bisogna
leggere la sua fiche. Questo file la legge.

FORMATO (SearchBody/SearchAnim, FICHE.C:585 e 529)
    blocchi [comando][generico][dimensione][carico], terminatore 255
    comando 1 = BODY  -> UWORD slot di BODY.HQR, poi un byte "ha azioni"
    comando 3 = ANIM  -> WORD slot di ANIM.HQR, poi un byte "ha azioni"
    lista di azioni:  [quante][tipo][fotogramma][parametri...] x quante

USO
    python tools/fiche_dump.py 18
    python tools/fiche_dump.py --all-throwers
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import scene_parse

# tipo -> (nome, byte di parametri DOPO il fotogramma). Da FICHE.C, ramo `else`
# di ogni caso: e' il salto che il gioco fa quando il fotogramma non e' quello,
# quindi e' la dimensione esatta.
ACTIONS = {
    5:  ('hit', 1),
    6:  ('sample', 2),
    7:  ('sample_rnd', 4),
    8:  ('throw', 11),
    9:  ('throw_magic', 7),
    10: ('sample_repeat', 4),
    11: ('throw_search', 6),
    12: ('throw_alpha', 11),
    13: ('sample_stop', 2),
    15: ('left_step', 0),
    16: ('right_step', 0),
    17: ('hit_twinkel', 1),
    18: ('throw_3D', 15),
    19: ('throw_3D_alpha', 15),
    20: ('throw_3D_search', 11),
    21: ('throw_3D_magic', 11),
}

THROWERS = (8, 9, 11, 12, 18, 19, 20, 21)


def u16(b, p):
    v = b[p] | (b[p + 1] << 8)
    return v - 0x10000 if v >= 0x8000 else v


def decode_actions(b, p):
    """Torna (lista di azioni, posizione finale). Ogni azione e'
    (tipo, nome, fotogramma, parametri grezzi)."""
    nb = b[p]; p += 1
    out = []
    for _ in range(nb):
        t = b[p]; p += 1
        if t not in ACTIONS:
            out.append((t, '???', None, b[p:p + 8]))
            return out, None            # non si sa quanto e' lungo: si ferma
        name, size = ACTIONS[t]
        frame = b[p]; p += 1
        out.append((t, name, frame, b[p:p + size]))
        p += size
    return out, p


def dump(fiche, verbose=True):
    p = 0
    blocks = []
    while p < len(fiche) and fiche[p] != 255:
        cmd = fiche[p]; p += 1
        gen = fiche[p]; p += 1
        size = fiche[p]; p += 1
        body = fiche[p:p + size]
        blocks.append((cmd, gen, size, body, p))
        p += size
    return blocks


def describe_throw(par):
    """I parametri di `throw` (FICHE.C:213): point, sprite, alpha, beta,
    vitesse, poids, force."""
    if len(par) < 11:
        return ''
    point = u16(par, 0)
    sprite = par[2]
    alpha = u16(par, 3)
    beta = u16(par, 5)
    vitesse = u16(par, 7)
    poids = par[9]
    force = par[10]
    return ('altezza %+d, sprite %d, alpha %d, beta %+d (SOMMATO al Beta '
            'dell\'attore), velocita\' %d, peso %d, forza %d'
            % (point, sprite, alpha, beta, vitesse, poids, force))


def show(index, buf, quiet=False):
    fiche = scene_parse._fiche(buf, index)
    if fiche is None:
        return []
    found = []
    if not quiet:
        print('fiche FILE3D %d - %d byte' % (index, len(fiche)))
    p = 0
    while p < len(fiche) and fiche[p] != 255:
        cmd = fiche[p]; p += 1
        gen = fiche[p]; p += 1
        # La dimensione COMPRENDE se stessa: il gioco salta al blocco dopo con
        # `ptrc += *ptrc` senza aver consumato quel byte (FICHE.C:578).
        q = p
        size = fiche[q]
        blk = fiche[q + 1:q + size]
        p = q + size
        if cmd == 1:
            slot = blk[0] | (blk[1] << 8)
            kind = 'BODY generico %d -> BODY.HQR %d' % (gen, slot)
        elif cmd == 3:
            slot = u16(blk, 0)
            kind = 'ANIM generico %d -> ANIM.HQR %d' % (gen, slot)
        else:
            kind = 'comando %d generico %d' % (cmd, gen)
        # Il byte dopo lo slot e' il CONTATORE delle azioni, non un booleano:
        # SearchAnim fa `if (*ptrc++) SearchPtrAnimAction = ptrc - 1`, cioe'
        # torna indietro di uno, e GereAnimAction legge da li' `nb = *ptrc++`
        # (FICHE.C:556 e 172). Nei blocchi BODY quel posto ha invece la scatola
        # di collisione ZV, che qui non interessa.
        acts = []
        if cmd == 3 and len(blk) > 2 and blk[2]:
            acts, _end = decode_actions(blk, 2)
        if not quiet:
            print('  %s%s' % (kind, '' if acts else '   (nessuna azione)'))
            for (t, name, frame, par) in acts:
                extra = describe_throw(par) if t == 8 else ' '.join(
                    '%02X' % c for c in par)
                print('      azione %-16s fotogramma %s   %s'
                      % (name, frame, extra))
        for (t, _n, _f, _par) in acts:
            if t in THROWERS:
                found.append((index, cmd, gen, t))
    return found


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('index', type=int, nargs='?', default=18)
    ap.add_argument('--all-throwers', action='store_true',
                    help='quali fiche del gioco lanciano qualcosa')
    args = ap.parse_args()

    buf = scene_parse.load_all()[0]
    if args.all_throwers:
        ents = scene_parse.container_entries(buf, scene_parse.HQR_FILE3D)
        print('%d fiche nel contenitore' % len(ents))
        for i in range(len(ents)):
            hits = show(i, buf, quiet=True)
            if hits:
                names = sorted(set(ACTIONS[t][0] for (_i, _c, _g, t) in hits))
                print('  fiche %3d: %s' % (i, ', '.join(names)))
        return
    show(args.index, buf)


if __name__ == '__main__':
    main()
