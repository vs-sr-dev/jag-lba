#!/usr/bin/env python3
"""M3e - il percorso SPRITE degli attori, calcolato offline.

Sta a `tools/sprite_dump.py` come `body_ref.py` sta a `render_body.py`: quello
guarda un formato, questo e' l'ORACOLO di quel che il Jaguar dovra' fare,
trascritto da AffScene (OBJECT.C:3041-3070) invece che inventato.

LE QUATTRO COSE CHE IL GIOCO FA, IN ORDINE
------------------------------------------
  1. proietta l'ANCORA dell'attore, la stessa dei corpi (ProjettePoint);
  2. `num = ptrobj->Body` - per uno SPRITE_3D quel campo NON e' un corpo 3D,
     e' l'indice dello sprite (InitSprite, OBJECT.C:1120). Nel nostro parser
     quel valore sta gia' in `a.sprite`;
  3. l'angolo alto-sinistro NON e' l'ancora: e' l'ancora piu' uno scostamento
     PER SPRITE letto da `PtrZvExtra[num*8 + 0/1]`, cioe' RESS.HQR voce 3
     (RESS_GOODIES_GPC, PERSO.C:1533). Senza quella tabella ogni sprite del
     gioco starebbe nel posto sbagliato di una quantita' diversa;
  4. il ritaglio. Per le PORTE (bandiera SPRITE_CLIP) e' un rettangolo FISSO
     preso da Info0..3 dell'attore piu' l'origine del mondo proiettata; per
     tutti gli altri e' il riquadro dello sprite stesso, cioe' nessun taglio.

E' il rettangolo fisso a far sembrare che la porta rientri nello stipite: la
porta scorre lungo il suo asse e il ritaglio la mangia. Non e' un'animazione.

DUE COSTANTI CHE NON SI COPIANO
-------------------------------
`SetClip` (INITSVGA.ASM:234) limita il ritaglio a 0..Screen-1, e Screen_Y su
DOS e' 480. Il nostro fondo e' 640x592: si copia la REGOLA ("non scrivere fuori
dal buffer"), non il 480 ([[feedback_regola_batte_taratura]]). E l'origine del
mondo XpOrgw/YpOrgw (GRILLE.C:668) esce da ProjettePoint, che e' la proiezione
degli ATTORI: qui sono `avx, avy`, non lo scostamento delle tessere.

LA TRASPARENZA E' UN OPCODE, NON UN COLORE
------------------------------------------
Nel flusso RLE solo il salto lascia il pixel intatto: un letterale che vale 0 e'
un pixel NERO che va scritto. Percio' questo blitter percorre gli opcode e
scrive le tratte, invece di decodificare in un rettangolo e saltare gli zeri -
che e' quel che fa sprite_dump.py per fare i PNG, e che qui sarebbe un difetto
silenzioso (bordi bucati sugli sprite che hanno del nero dentro).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import scene_parse as sp
import sprite_dump

HQR_RESS = 3
RESS_GOODIES_GPC = 3          # COMMON.H:6
SPRITE_CLIP = 8               # COMMON.H:152, "(portes)"
SPRITE_3D = 1024


def load_zv_raw(buf):
    """I byte COME STANNO nella cartuccia. Li vuole il banco di prova su PC:
    dargli i valori gia' decodificati e ri-impacchettati vorrebbe dire provare
    il mio impacchettamento invece del suo lettore - ed e' esattamente cosi'
    che in S36 la tabella e' finita nel dump big-endian mentre il 68000 la
    legge little-endian."""
    ents = sp.container_entries(buf, HQR_RESS)
    off, size = ents[RESS_GOODIES_GPC]
    return sp.container_payload(buf, HQR_RESS, off, size)


def load_zv(buf):
    """PtrZvExtra: RESS.HQR voce 3, letta come WORD con segno.

    Otto parole per sprite: le prime due sono lo scostamento di schermo, le sei
    dopo la scatola di collisione (Xmin, Xmax, Ymin, Ymax, Zmin, Zmax) che
    InitSprite copia nell'attore (OBJECT.C:1122-1129).
    """
    ents = sp.container_entries(buf, HQR_RESS)
    off, size = ents[RESS_GOODIES_GPC]
    pay = sp.container_payload(buf, HQR_RESS, off, size)
    out = []
    for i in range(len(pay) // 2):
        v = pay[i * 2] | (pay[i * 2 + 1] << 8)
        out.append(v - 0x10000 if v >= 0x8000 else v)
    return out


def graph_dxdy(payload, num=0):
    """GetDxDyGraph (GRAPH_A.ASM:302): salta alla grafica `num` del banco e ne
    legge i quattro byte d'intestazione."""
    off = (payload[num * 4] | (payload[num * 4 + 1] << 8)
           | (payload[num * 4 + 2] << 16) | (payload[num * 4 + 3] << 24))
    return (payload[off], payload[off + 1], payload[off + 2],
            payload[off + 3], off + 4)


def aff_graph(fb, fb_w, fb_h, payload, x0, y0, clip, num=0):
    """AffGraph: il banco, la posizione, il ritaglio. Torna i pixel scritti.

    `clip` e' (xmin, ymin, xmax, ymax) con i massimi INCLUSI, come nel gioco.
    """
    dx, dy, _hx, _hy, pos = graph_dxdy(payload, num)
    cx0, cy0, cx1, cy1 = clip
    written = 0
    for r in range(dy):
        nb = payload[pos]
        pos += 1
        y = y0 + r
        col = x0
        vis = cy0 <= y <= cy1
        base = y * fb_w
        for _ in range(nb):
            op = payload[pos]
            pos += 1
            count = (op & 0x3F) + 1
            mode = op & 0xC0
            if mode == 0x00:
                col += count                      # salto: niente da scrivere
                continue
            if mode == 0x80:
                pix = payload[pos]
                pos += 1
                if vis:
                    for i in range(count):
                        x = col + i
                        if cx0 <= x <= cx1:
                            fb[base + x] = pix
                            written += 1
                col += count
                continue
            if vis:
                for i in range(count):
                    x = col + i
                    if cx0 <= x <= cx1:
                        fb[base + x] = payload[pos + i]
                        written += 1
            pos += count
            col += count
    return written, dx, dy


def draw_sprite(fb, fb_w, fb_h, buf, zv, actor, avx, avy, proj):
    """Un attore SPRITE_3D sul framebuffer. Torna
    (pixel, (x0, y0, dx, dy), ritaglio) oppure None se il ritaglio e' vuoto.

    `proj` e' la proiezione isometrica gia' usata dai corpi, passata da fuori
    perche' oracolo e motore devono usarne UNA sola.
    """
    num = actor.sprite
    payload = sprite_dump.sprite_payload(buf, num)
    if payload is None:
        return None

    xp, yp = proj(actor.x, actor.y, actor.z, avx, avy)
    x0 = xp + zv[num * 8 + 0]
    y0 = yp + zv[num * 8 + 1]
    dx, dy, _hx, _hy, _pos = graph_dxdy(payload, 0)

    if actor.flags & SPRITE_CLIP:
        # Il rettangolo fisso della porta, in coordinate di mondo proiettate:
        # Info0..3 piu' l'origine del cubo. Non dipende da dove sia la porta
        # in questo istante, ed e' proprio questo il punto.
        c = (actor.info[0] + avx, actor.info[1] + avy,
             actor.info[2] + avx, actor.info[3] + avy)
    else:
        c = (x0, y0, x0 + dx, y0 + dy)

    # SetClip: i minimi non scendono sotto zero, i massimi non superano l'ultima
    # riga/colonna del buffer. Poi il gioco prova che il rettangolo esista.
    cx0 = max(0, c[0]); cy0 = max(0, c[1])
    cx1 = min(fb_w - 1, c[2]); cy1 = min(fb_h - 1, c[3])
    if cx0 > cx1 or cy0 > cy1:
        return None

    written, dx, dy = aff_graph(fb, fb_w, fb_h, payload, x0, y0,
                                (cx0, cy0, cx1, cy1))
    return written, (x0, y0, dx, dy), (cx0, cy0, cx1, cy1)
