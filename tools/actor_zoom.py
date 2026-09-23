#!/usr/bin/env python3
"""Un attore da vicino: chi e', e cosa se ne vede a schermo.

A COSA SERVE
------------
A rispondere alla domanda "questo coso a schermo chi e'?" senza dedurlo dai
numeri. Nasce in S34 per l'attore 17 della scena 2, che ha la bandiera INVISIBLE
e che il porting disegnava lo stesso (il gioco no, OBJECT.C:2617).

Produce due immagini, che rispondono a due domande diverse:

  _scena.png   il RITAGLIO del composto attorno all'ancora dell'attore, cioe'
               esattamente quel che si vede sullo schermo, con la scena intorno
               per riconoscere il posto. Ingrandito a blocchi.
  _solo.png    l'attore DA SOLO su nero, con la faccia nascosta DISATTIVATA,
               cioe' tutta la geometria che il corpo contiene. Serve perche' a
               schermo se ne vede spesso una fetta: due poligoni su quattro non
               dicono che forma abbia l'oggetto.

L'ingrandimento e' a blocchi (nearest) apposta: interpolare inventerebbe pixel
che sul Jaguar non esistono, e questo strumento serve a riconoscere, non a fare
belle figure.

USO
    python tools/actor_zoom.py --step 2 --actor 17
    python tools/actor_zoom.py --step 0 --actor 0 --zoom 8
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bg_ref
import body_ref
import scene_parse
import body_sphere_dump
from render_body import parse_body_full
from render_slide import normalize_palette, write_png
from hqr_decompress import extract_entry

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def write_zoom(path, rows, factor):
    """Scrive il PNG ingrandito a blocchi. `rows` e' una lista di liste di
    (r,g,b); write_png vuole w, h e i byte RGB di seguito."""
    h = len(rows) * factor
    w = len(rows[0]) * factor
    out = bytearray()
    for row in rows:
        line = bytearray()
        for (r, g, b) in row:
            line.extend((r, g, b) * factor)
        for _ in range(factor):
            out.extend(line)
    write_png(path, w, h, bytes(out))


def crop_rgb(fb, palette, x0, y0, x1, y1, mark=None):
    """Ritaglia il framebuffer a indici e lo converte in RGB. `mark` e' un punto
    (x,y) da segnare con una crocetta, cioe' l'ancora dell'attore: senza, in una
    scena affollata non si sa quale dei cosi' guardare."""
    x0 = max(0, x0); y0 = max(0, y0)
    x1 = min(bg_ref.BG_W, x1); y1 = min(bg_ref.BG_H, y1)
    rows = []
    for y in range(y0, y1):
        row = []
        for x in range(x0, x1):
            row.append(palette[fb[y * bg_ref.BG_W + x]])
        rows.append(row)
    if mark is not None:
        mx, my = mark[0] - x0, mark[1] - y0
        for d in range(-4, 5):
            for (px, py) in ((mx + d, my), (mx, my + d)):
                if 0 <= px < len(rows[0]) and 0 <= py < len(rows):
                    rows[py][px] = (255, 0, 255)
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--step', type=int, default=2,
                    help='passo di walk_steps(), non numero di scena')
    ap.add_argument('--actor', type=int, required=True)
    ap.add_argument('--zoom', type=int, default=4)
    ap.add_argument('--margin', type=int, default=90,
                    help='pixel di scena attorno all\'ancora')
    ap.add_argument('--out-dir', default='build')
    args = ap.parse_args()

    buf = scene_parse.load_all()[0]
    scenes = scene_parse.load_all()[2]
    common = bg_ref.load_common(bg_ref.GOG_COMMON)
    num, enter = scene_parse.walk_steps(0)[args.step]
    actors = scene_parse.resolve_actors(buf, scenes[num], enter)
    a = actors[args.actor]

    # L'INDICE del passo va passato: da M4 decide la posa, e senza si vedrebbe
    # l'attore del passo chiesto nella posa del passo 0.
    fb, info, stats = body_ref.render_step(buf, common, num, enter, args.step)
    ress, ress_off = common['ress']
    palette = normalize_palette(extract_entry(ress, 0, ress_off))

    # --- chi e' ---
    flags = a.flags & 0xFFFF
    print('attore %d, passo %d (scena %d)' % (args.actor, args.step, num))
    print('  flags %04X   SPRITE_CLIP=%d INVISIBLE=%d SPRITE_3D=%d'
          % (flags, bool(flags & 8), bool(flags & 512), bool(flags & 1024)))
    print('  mondo (%d, %d, %d) beta %d -> cella (%d, %d, %d)'
          % (a.x, a.y, a.z, a.beta, a.x // 512, a.y // 512, a.z // 512))
    print('  BODY.HQR slot %d (generico %d)' % (a.body, a.gen_body))

    payload = body_ref.body_payload(buf, a.body)
    full = parse_body_full(payload)
    ent = body_sphere_dump.parse_body_full(payload)
    vs = full['vertices']
    xs = [v[0] for v in vs]; ys = [v[1] for v in vs]; zs = [v[2] for v in vs]
    print('  corpo: %d vertici, %d poligoni, %d linee, %d sfere'
          % (len(vs), len(full['polys']), len(ent['lines']), len(ent['spheres'])))
    print('  ingombro mondo: X %d  Y %d  Z %d   (una cella e\' 512)'
          % (max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs)))

    anchor = None
    for rec in stats['actors']:
        if rec[0] == args.actor:
            anchor = rec[2]
    if anchor is None:
        raise SystemExit('l\'attore %d non ha un corpo in questo passo'
                         % args.actor)
    print('  ancora a schermo (%d, %d)' % anchor)

    base = os.path.join(REPO, args.out_dir,
                        'actor_s%d_a%02d' % (args.step, args.actor))

    # --- 1. il ritaglio del composto: quel che si vede davvero ---
    m = args.margin
    rows = crop_rgb(fb, palette, anchor[0] - m, anchor[1] - m,
                    anchor[0] + m, anchor[1] + m, mark=anchor)
    write_zoom(base + '_scena.png', rows, args.zoom)
    print('  scritto %s_scena.png' % base)

    # --- 2. l'attore da solo, senza faccia nascosta ---
    solo = bytearray(bg_ref.BG_W * bg_ref.BG_H)
    s = scenes[num]
    light = body_ref.set_light_vector(s.alpha_light, s.beta_light, 0)
    cx, cy = bg_ref.BG_W // 2, bg_ref.BG_H // 2
    body = body_ref.prepare_body(payload)
    # Si sposta l'attore al centro del quadro sostituendo lo scostamento, non
    # muovendo l'attore: cosi' orientamento e scala restano quelli della scena.
    ax, ay = anchor
    body_ref.draw_actor(solo, body, a, light,
                        stats['avx'] + (cx - ax), stats['avy'] + (cy - ay),
                        body_ref.Fletch(), body_ref.Fletch(), body_ref.Fletch(),
                        cull=False)
    box = _drawn_box(solo)
    if box is None:
        print('  (da solo non disegna niente)')
        return
    x0, y0, x1, y1 = box
    pad = 6
    rows = crop_rgb(solo, palette, x0 - pad, y0 - pad, x1 + pad + 1, y1 + pad + 1)
    write_zoom(base + '_solo.png', rows, max(args.zoom, 6))
    print('  scritto %s_solo.png  (%dx%d pixel di disegno)'
          % (base, x1 - x0 + 1, y1 - y0 + 1))


def _drawn_box(fb):
    x0, y0, x1, y1 = bg_ref.BG_W, bg_ref.BG_H, -1, -1
    for y in range(bg_ref.BG_H):
        row = y * bg_ref.BG_W
        for x in range(bg_ref.BG_W):
            if fb[row + x]:
                if x < x0: x0 = x
                if x > x1: x1 = x
                if y < y0: y0 = y
                if y > y1: y1 = y
    return None if x1 < 0 else (x0, y0, x1, y1)


if __name__ == '__main__':
    main()
