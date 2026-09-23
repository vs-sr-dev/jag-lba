#!/usr/bin/env python3
"""Le frecce INVISIBLE, DISEGNATE, ognuna nella sua scena.

A COSA SERVE
------------
Il gioco non le disegna mai (OBJECT.C:2617), quindi nessuno le ha mai viste in
posizione: si sa che ci sono 79 attori col corpo 37 e la bandiera INVISIBLE
(elenco in `tools/wedge_census.py`) ma non che aspetto abbia una stanza CON
dentro la freccia. Questo file toglie quella bandiera - solo qui, solo per
guardare - e produce due immagini per freccia:

  sNNN_aKK.png        il quadro intero 640x592, camera centrata sulla freccia
                      con la regola del gioco, per il contesto;
  sNNN_aKK_zoom.png   il ritaglio attorno alla freccia, ingrandito a blocchi,
                      col riquadro magenta che dice QUALE dei cosi' e'.

Il riquadro si calcola disegnando la freccia da sola su un quadro vuoto e
prendendone l'ingombro: cosi' non copre l'oggetto (una crocetta sull'ancora lo
copriva).

Piu' `--shape`, che disegna il corpo 37 da solo a otto orientamenti: serve a
far vedere la FORMA, che dal contesto si intuisce male perche' a schermo se ne
vedono due poligoni su quattro.

Questo file NON tocca l'oracolo: rifa' il giro degli attori con le primitive di
body_ref invece di cambiargli una condizione.

USO
    python tools/wedge_render.py                    # tutte e 79
    python tools/wedge_render.py --scene 8 --scene 40
    python tools/wedge_render.py --shape
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bg_ref
import body_ref
import scene_parse
import wedge_census
from render_slide import normalize_palette, write_png
from hqr_decompress import extract_entry

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(REPO, 'build', 'wedges')

MARK = (255, 0, 255)


def to_rgb(fb, palette, x0, y0, x1, y1):
    x0 = max(0, x0); y0 = max(0, y0)
    x1 = min(bg_ref.BG_W, x1); y1 = min(bg_ref.BG_H, y1)
    rows = []
    for y in range(y0, y1):
        base = y * bg_ref.BG_W
        rows.append([palette[fb[base + x]] for x in range(x0, x1)])
    return rows, x0, y0


def draw_box(rows, ox, oy, box, pad=3, colour=MARK):
    """Riquadro aperto attorno all'ingombro: due segmenti per lato, con un
    buco in mezzo, cosi' si vede anche cosa c'e' sotto il bordo."""
    if box is None:
        return
    x0 = box[0] - ox - pad; y0 = box[1] - oy - pad
    x1 = box[2] - ox + pad; y1 = box[3] - oy + pad
    h = len(rows); w = len(rows[0]) if h else 0

    def put(x, y):
        if 0 <= x < w and 0 <= y < h:
            rows[y][x] = colour

    for x in range(x0, x1 + 1):
        q = (x - x0) * 4 // max(1, (x1 - x0))
        if q in (1, 2):
            continue
        put(x, y0); put(x, y1)
    for y in range(y0, y1 + 1):
        q = (y - y0) * 4 // max(1, (y1 - y0))
        if q in (1, 2):
            continue
        put(x0, y); put(x1, y)


def write_zoom(path, rows, factor):
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


def drawn_box(fb):
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


def render_scene_with(buf, common, num, enter, slots, hero=False):
    """Il fondo con sopra TUTTI gli attori che hanno un corpo, frecce comprese.

    Le frecce si disegnano per ultime perche' qui non c'e' l'intreccio di
    profondita' (M3d): l'ultimo disegnato sta sopra, e la freccia e' quel che si
    vuole vedere."""
    fb, info = bg_ref.render(num, common, enter=enter)
    scenes = scene_parse.load_all()[2]
    s = scenes[num]
    actors = scene_parse.resolve_actors(buf, s, enter)
    light = body_ref.set_light_vector(s.alpha_light, s.beta_light, 0)
    avx = info['vx'] - bg_ref.CAM_CX + body_ref.ACT_CX
    avy = info['vy'] - bg_ref.CAM_CY + body_ref.ACT_CY
    g, sh, od = body_ref.Fletch(), body_ref.Fletch(), body_ref.Fletch()

    order = [i for i, a in enumerate(actors)
             if a.body >= 0 and a.body not in slots and (hero or i)]
    order += [i for i, a in enumerate(actors) if a.body in slots and i]

    for i in order:
        a = actors[i]
        payload = body_ref.body_payload(buf, a.body)
        if payload is None:
            continue
        anchor = body_ref.proj_iso(a.x, a.y, a.z, avx, avy)
        # Il preclip resta acceso: e' geometrico e non toglie niente di
        # visibile. La bandiera INVISIBLE invece qui NON si guarda: e' il
        # motivo per cui esiste questo file.
        if not body_ref.actor_preclip(anchor, a.flags):
            continue
        body_ref.draw_actor(fb, body_ref.prepare_body(payload), a, light,
                            avx, avy, g, sh, od)
    return fb, info, actors, light, avx, avy


def wedge_only(buf, actor, light, avx, avy):
    """La freccia da sola su un quadro vuoto: serve solo per l'ingombro."""
    solo = bytearray(bg_ref.BG_W * bg_ref.BG_H)
    payload = body_ref.body_payload(buf, actor.body)
    body_ref.draw_actor(solo, body_ref.prepare_body(payload), actor, light,
                        avx, avy, body_ref.Fletch(), body_ref.Fletch(),
                        body_ref.Fletch(), cull=False)
    return drawn_box(solo)


def render_shape(buf, common, palette, hits):
    """Il corpo 37 da solo, otto orientamenti, faccia nascosta disattivata."""
    from types import SimpleNamespace
    h = hits[0]
    s = scene_parse.load_all()[2][h.scene]
    light = body_ref.set_light_vector(s.alpha_light, s.beta_light, 0)
    cx, cy = bg_ref.BG_W // 2, bg_ref.BG_H // 2
    payload = body_ref.body_payload(buf, h.body)
    body = body_ref.prepare_body(payload)

    strips = []
    for k in range(8):
        beta = k * 128
        fb = bytearray(bg_ref.BG_W * bg_ref.BG_H)
        a = SimpleNamespace(x=0, y=0, z=0, beta=beta, flags=0, body=h.body)
        avx, avy = cx, cy
        body_ref.draw_actor(fb, body, a, light, avx, avy, body_ref.Fletch(),
                            body_ref.Fletch(), body_ref.Fletch(), cull=False)
        box = drawn_box(fb)
        if box is None:
            continue
        rows, _ox, _oy = to_rgb(fb, palette, cx - 40, cy - 40, cx + 40, cy + 40)
        strips.append((beta, rows))

    # Affiancate, con una riga di separazione, in un'immagine sola.
    h0 = max(len(r) for _b, r in strips)
    w0 = max(len(r[0]) for _b, r in strips)
    out = [[(0, 0, 0)] * ((w0 + 1) * len(strips)) for _ in range(h0)]
    for k, (_beta, rows) in enumerate(strips):
        for y, row in enumerate(rows):
            for x, px in enumerate(row):
                out[y][k * (w0 + 1) + x] = px
    path = os.path.join(OUT, 'shape_body37.png')
    write_zoom(path, out, 5)
    print('scritto %s  (beta 0,128,...,896 da sinistra a destra)' % path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--scene', type=int, action='append', default=None,
                    help='limita a queste scene (ripetibile)')
    ap.add_argument('--zoom', type=int, default=4)
    ap.add_argument('--margin', type=int, default=100)
    ap.add_argument('--hero', action='store_true',
                    help='disegna anche l\'eroe, che la camera porta sul posto')
    ap.add_argument('--shape', action='store_true')
    ap.add_argument('--no-full', action='store_true',
                    help='solo i ritagli ingranditi')
    args = ap.parse_args()

    os.makedirs(OUT, exist_ok=True)
    buf, _entries, scenes = scene_parse.load_all()
    common = bg_ref.load_common(bg_ref.GOG_COMMON)
    ress, ress_off = common['ress']
    palette = normalize_palette(extract_entry(ress, 0, ress_off))

    slots, _ref = wedge_census.find_wedge_slots(buf)
    hits = wedge_census.census(buf, scenes, slots)
    if args.scene:
        hits = [h for h in hits if h.scene in args.scene]
    if not hits:
        raise SystemExit('nessuna freccia da disegnare')

    if args.shape:
        render_shape(buf, common, palette, hits)
        return

    for h in hits:
        enter = (h.x, h.y, h.z)
        fb, _info, actors, light, avx, avy = render_scene_with(
            buf, common, h.scene, enter, slots, hero=args.hero)
        a = actors[h.actor]
        box = wedge_only(buf, a, light, avx, avy)
        anchor = body_ref.proj_iso(a.x, a.y, a.z, avx, avy)
        base = os.path.join(OUT, 's%03d_a%02d' % (h.scene, h.actor))

        if not args.no_full:
            rows, ox, oy = to_rgb(fb, palette, 0, 0, bg_ref.BG_W, bg_ref.BG_H)
            draw_box(rows, ox, oy, box, pad=8)
            write_zoom(base + '.png', rows, 1)

        m = args.margin
        cx, cy = (anchor if box is None
                  else ((box[0] + box[2]) // 2, (box[1] + box[3]) // 2))
        rows, ox, oy = to_rgb(fb, palette, cx - m, cy - m, cx + m, cy + m)
        draw_box(rows, ox, oy, box, pad=5)
        write_zoom(base + '_zoom.png', rows, args.zoom)
        print('scena %3d attore %2d  beta %4d  ingombro a schermo %s  -> %s'
              % (h.scene, h.actor, h.beta,
                 'nessuno' if box is None
                 else '%dx%d' % (box[2] - box[0] + 1, box[3] - box[1] + 1),
                 os.path.basename(base)))


if __name__ == '__main__':
    main()
