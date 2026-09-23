#!/usr/bin/env python3
"""Le frecce INVISIBLE: dove sono, tutte, e con che contorno.

A COSA SERVE
------------
In S34 e' saltato fuori che 79 attori del gioco puntano allo stesso corpo (la
fiche FILE3D 18 -> BODY.HQR 37), un cuneo a punta di 8 vertici e 4 poligoni, e
che TUTTI hanno la bandiera INVISIBLE: il gioco non li disegna mai. Nella scena
2 il porting ne disegnava uno per sbaglio, e da li' e' partita la domanda "cosa
sono".

Questo file risponde alla PRIMA meta' della domanda - dove stanno e con che
dati - e serve da elenco a `tools/wedge_render.py`, che disegna le scene.

Non si fida dello slot 37 come definizione: cerca il corpo per FORMA, cioe'
prende la geometria dello slot 37 e vede quali altri slot di BODY.HQR hanno gli
stessi vertici. Se il gioco ha piu' di una freccia (per isola, per dimensione)
uno slot scritto a mano la nasconderebbe.

USO
    python tools/wedge_census.py                # elenco + build/wedge_census.csv
    python tools/wedge_census.py --scene 40     # solo una scena, con i vicini
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import scene_parse
import body_ref
from render_body import parse_body_full

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

WEDGE_SLOT = 37          # il seme della ricerca, non la definizione
INVISIBLE = 512
NO_SHADOW = 4096
SPRITE_3D = 1024


def body_shape(payload):
    """Firma di forma di un corpo: i vertici e quanti poligoni/normali/linee.
    Due corpi con la stessa firma sono lo stesso oggetto anche se stanno in due
    slot diversi."""
    if payload is None:
        return None
    try:
        full = parse_body_full(payload)
    except Exception:
        return None
    return (tuple(full['vertices']), len(full['polys']),
            len(full.get('normals', ())))


def find_wedge_slots(buf, seed=WEDGE_SLOT, limit=256):
    """Tutti gli slot di BODY.HQR con la forma dello slot seme."""
    ref = body_shape(body_ref.body_payload(buf, seed))
    if ref is None:
        raise SystemExit('lo slot %d non si legge' % seed)
    slots = []
    for slot in range(limit):
        p = body_ref.body_payload(buf, slot)
        if p is None:
            continue
        if body_shape(p) == ref:
            slots.append(slot)
    return slots, ref


def beta_deg(beta):
    """Beta di LBA: 1024 = giro intero."""
    return (beta % 1024) * 360.0 / 1024.0


class Hit(object):
    pass


def census(buf, scenes, slots):
    """Ogni attore del gioco che ha uno di quei corpi, scena per scena."""
    hits = []
    for num, s in enumerate(scenes):
        if s is None:
            continue
        actors = scene_parse.resolve_actors(buf, s)
        for i, a in enumerate(actors):
            if i == 0 or a.body not in slots:
                continue
            src = s.actors[i - 1]        # l'eroe occupa lo slot 0 di ListObjet
            h = Hit()
            h.scene = num
            h.actor = i                  # indice in ListObjet, come sul Jaguar
            h.body = a.body
            h.file3d = src.index_file3d
            h.gen_body = src.gen_body
            h.gen_anim = src.gen_anim
            # Quali animazioni della fiche 18 gli script della scena gli
            # assegnano: e' quel che DECIDE la raffica (vedi fiche_dump.py).
            # Ricerca per motivo, non un interprete: TM_ANIM/LM_ANIM seguito
            # da un generico nell'intervallo dei lanciatori.
            pay = s.payload
            h.anims = sorted(set(
                b[i + 1]
                for (b, op) in ((pay[src.track_off:src.track_off + src.track_len], 3),
                                (pay[src.life_off:src.life_off + src.life_len], 19))
                for i in range(len(b) - 1)
                if b[i] == op and 90 <= b[i + 1] <= 110))
            h.flags = a.flags & 0xFFFF
            h.x, h.y, h.z = a.x, a.y, a.z
            h.beta = a.beta
            h.src = src
            hits.append(h)
    return hits


def print_hit(h, verbose=False):
    a = h.src
    print('  scena %3d  attore %2d  mondo (%6d,%6d,%6d)  cella (%2d,%2d,%2d)  '
          'beta %4d (%5.1f gradi)  flags %04X  anim dagli script %s'
          % (h.scene, h.actor, h.x, h.y, h.z,
             h.x // 512, h.y // 256, h.z // 512,
             h.beta, beta_deg(h.beta), h.flags,
             ','.join(str(v) for v in h.anims) if h.anims else '-'))
    if verbose:
        print('        file3d %d gen_body %d -> BODY %d | move %d srot %d '
              'hit %d optflags %04X'
              % (h.file3d, h.gen_body, h.body, a.move, a.srot, a.hit_force,
                 a.option_flags & 0xFFFF))
        print('        info %s | bonus %d coul %d armure %d life %d | '
              'script track %d byte, life %d byte'
              % (a.info, a.nb_bonus, a.coul, a.armure, a.life,
                 a.track_len, a.life_len))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--scene', type=int, default=None)
    ap.add_argument('--csv', default=os.path.join(REPO, 'build',
                                                  'wedge_census.csv'))
    args = ap.parse_args()

    buf, _entries, scenes = scene_parse.load_all()
    slots, ref = find_wedge_slots(buf)
    verts, npoly, nnorm = ref

    xs = [v[0] for v in verts]; ys = [v[1] for v in verts]; zs = [v[2] for v in verts]
    print('LA FORMA (slot seme %d)' % WEDGE_SLOT)
    print('  %d vertici, %d poligoni, %d normali' % (len(verts), npoly, nnorm))
    print('  ingombro X %d  Y %d  Z %d   (una cella e\' 512x256x512)'
          % (max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs)))
    print('  slot di BODY.HQR con questa forma: %s' % (slots,))

    hits = census(buf, scenes, slots)
    per_scene = {}
    for h in hits:
        per_scene.setdefault(h.scene, []).append(h)

    print()
    print('IN TUTTO IL GIOCO: %d attori in %d scene'
          % (len(hits), len(per_scene)))
    n_inv = sum(1 for h in hits if h.flags & INVISIBLE)
    print('  con INVISIBLE: %d su %d' % (n_inv, len(hits)))
    fl = {}
    for h in hits:
        fl[h.flags] = fl.get(h.flags, 0) + 1
    print('  bandiere distinte: %s'
          % ', '.join('%04X x%d' % (k, v) for k, v in sorted(fl.items())))

    if args.scene is not None:
        s = scenes[args.scene]
        print('\nSCENA %d' % args.scene)
        for h in per_scene.get(args.scene, []):
            print_hit(h, verbose=True)
        print('  --- gli altri attori della stessa scena ---')
        actors = scene_parse.resolve_actors(buf, s)
        for i, a in enumerate(actors):
            if i == 0 or a.body in slots:
                continue
            src = s.actors[i - 1]
            print('  attore %2d  BODY %3d sprite %3d flags %04X  '
                  'mondo (%6d,%6d,%6d) beta %4d  life %d byte'
                  % (i, a.body, a.sprite, a.flags & 0xFFFF, a.x, a.y, a.z,
                     a.beta, src.life_len))
        return

    print()
    print('PER SCENA (le scene senza frecce non compaiono)')
    for num in sorted(per_scene):
        hs = per_scene[num]
        print('scena %3d: %d' % (num, len(hs)))
        for h in hs:
            print_hit(h)

    os.makedirs(os.path.dirname(args.csv), exist_ok=True)
    with open(args.csv, 'w') as f:
        f.write('scene,actor,body,file3d,gen_body,flags,x,y,z,cx,cy,cz,'
                'gen_anim,anim_da_script,'
                'beta,beta_deg,move,srot,hit_force,option_flags,'
                'info0,info1,info2,info3,nb_bonus,coul,armure,life,'
                'track_len,life_len\n')
        for h in hits:
            a = h.src
            f.write('%d,%d,%d,%d,%d,%04X,%d,%d,%d,%d,%d,%d,%d,%s,%d,%.1f,'
                    '%d,%d,%d,%04X,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d\n'
                    % (h.scene, h.actor, h.body, h.file3d, h.gen_body, h.flags,
                       h.x, h.y, h.z, h.x // 512, h.y // 256, h.z // 512,
                       h.gen_anim, ' '.join(str(v) for v in h.anims),
                       h.beta, beta_deg(h.beta), a.move, a.srot, a.hit_force,
                       a.option_flags & 0xFFFF, a.info[0], a.info[1], a.info[2],
                       a.info[3], a.nb_bonus, a.coul, a.armure, a.life,
                       a.track_len, a.life_len))
    print('\nscritto %s' % args.csv)


if __name__ == '__main__':
    main()
