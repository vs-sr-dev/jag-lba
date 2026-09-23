#!/usr/bin/env python3
"""Gli SPRITE di LBA1: decodifica, PNG, e chi li usa.

PERCHE' ESISTE
--------------
Perche' fino a S34 il porting non disegnava NESSUNO sprite, e la cosa non era
nemmeno dichiarata come buco. Sono 348 attori su 1118 in tutto il gioco - quasi
un terzo - e 138 di essi sono PORTE. Nelle stanze costruite finora l'assenza si
vede: le porte non ci sono.

IL FORMATO, RICAVATO DA GRAPH_A.ASM E VERIFICATO
------------------------------------------------
Una voce di SPRITES.HQR non e' un'immagine: e' un BANCO.

    +0   tabella di scostamenti a 32 bit, uno per grafica del banco
         (GetDxDyGraph: `add esi, [esi+eax*4]`, GRAPH_A.ASM:302)
    off  dx, dy, hotx, hoty          quattro byte
    +4   le righe, una dopo l'altra

Ogni riga comincia con un byte "quanti opcode" (AffGraph, `mov bl,[esi]`), e gli
opcode sono gli STESSI TRE dei brick:

    00xxxxxx  salta  n+1 pixel (trasparente)
    10xxxxxx  ripeti n+1 volte il byte che segue
    01/11     copia  n+1 byte letterali

Cioe' il disegno degli sprite e quello delle tessere sono la stessa routine
(AffGraph), e il decodificatore dei brick serve quasi tale e quale.

LA PROVA CHE L'INTESTAZIONE E' DI 4 BYTE non e' una deduzione: provando 2, 3 e 4
solo con 4 la decodifica consuma ESATTAMENTE il payload, byte per byte, su tutti
gli sprite provati. Un formato sbagliato si ferma prima o sfora.

QUEL CHE QUESTO FILE NON FA
---------------------------
Non disegna le porte come le disegna il gioco. Per quelle AffScene usa un
rettangolo di ritaglio FISSO preso da Info/Info1/Info2/Info3 dell'attore
(OBJECT.C:3056, la "zone de clip fixe" del commento di COMMON.H:152): la porta
scorre lungo il suo asse e il rettangolo la taglia, cosi' sembra rientrare nello
stipite. Qui lo sprite si vede intero.

USO
    python tools/sprite_dump.py --sprite 15
    python tools/sprite_dump.py --doors        # tutte le porte delle 3 stanze
"""
import argparse
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bg_ref
import scene_parse as sp
from render_slide import normalize_palette, write_png
from hqr_decompress import extract_entry

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

HQR_SPRITES = 10
SPRITE_3D = 1024
SPRITE_CLIP = 8


def decode_sprite(payload, num=0):
    """(dx, dy, hotx, hoty, righe di indici). 0 = trasparente, come nei brick:
    l'opcode di salto lascia il pixel intatto e qui resta 0."""
    off = struct.unpack('<I', payload[num * 4:num * 4 + 4])[0]
    dx, dy, hx, hy = payload[off], payload[off + 1], payload[off + 2], payload[off + 3]
    pos = off + 4
    rows = []
    for _ in range(dy):
        nb = payload[pos]
        pos += 1
        row = [0] * dx
        col = 0
        for _ in range(nb):
            op = payload[pos]
            pos += 1
            count = (op & 0x3F) + 1
            mode = op & 0xC0
            if mode == 0x00:
                col += count
            elif mode == 0x80:
                pix = payload[pos]
                pos += 1
                for i in range(count):
                    if col + i < dx:
                        row[col + i] = pix
                col += count
            else:
                for i in range(count):
                    if col + i < dx:
                        row[col + i] = payload[pos]
                    pos += 1
                col += count
        if col != dx:
            raise ValueError('riga %d: %d pixel invece di %d' % (len(rows), col, dx))
        rows.append(row)
    return dx, dy, hx, hy, rows, pos


def sprite_payload(buf, idx):
    ents = sp.container_entries(buf, HQR_SPRITES)
    if idx < 0 or idx >= len(ents):
        return None
    off, size = ents[idx]
    return sp.container_payload(buf, HQR_SPRITES, off, size)


def users(scenes, idx):
    """Chi usa questo sprite, e se e' una porta."""
    out = []
    for n, s in enumerate(scenes):
        for j, a in enumerate(s.actors):
            f = a.flags & 0xFFFF
            if (f & SPRITE_3D) and a.sprite == idx:
                out.append((n, j + 1, bool(f & SPRITE_CLIP)))
    return out


def render(path, rows, palette, zoom):
    h = len(rows) * zoom
    w = len(rows[0]) * zoom
    out = bytearray()
    for row in rows:
        line = bytearray()
        for idx in row:
            r, g, b = palette[idx]
            line.extend((r, g, b) * zoom)
        for _ in range(zoom):
            out.extend(line)
    write_png(path, w, h, bytes(out))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sprite', type=int, action='append')
    ap.add_argument('--doors', action='store_true',
                    help='tutte le porte delle stanze di walk_steps()')
    ap.add_argument('--zoom', type=int, default=3)
    ap.add_argument('--out-dir', default='build')
    args = ap.parse_args()

    buf, _ents, scenes = sp.load_all()
    common = bg_ref.load_common(bg_ref.GOG_COMMON)
    ress, ress_off = common['ress']
    palette = normalize_palette(extract_entry(ress, 0, ress_off))

    want = list(args.sprite or [])
    if args.doors:
        for num, _enter in sp.walk_steps(0):
            for a in scenes[num].actors:
                f = a.flags & 0xFFFF
                if (f & SPRITE_3D) and (f & SPRITE_CLIP) and a.sprite not in want:
                    want.append(a.sprite)
    if not want:
        raise SystemExit('serve --sprite N oppure --doors')

    for idx in want:
        pl = sprite_payload(buf, idx)
        if pl is None:
            print('sprite %d: non c\'e\' nel contenitore' % idx)
            continue
        dx, dy, hx, hy, rows, end = decode_sprite(pl)
        us = users(scenes, idx)
        porte = sum(1 for u in us if u[2])
        print('sprite %3d  %3dx%-3d  hot(%3d,%3d)  payload %5d  consumato %5d %s'
              % (idx, dx, dy, hx, hy, len(pl), end,
                 'ESATTO' if end == len(pl) else '<<<< AVANZO'))
        print('           usato da %d attori in %d scene, di cui %d porte  %s'
              % (len(us), len(set(u[0] for u in us)), porte,
                 ' '.join('s%d/a%d%s' % (u[0], u[1], '*' if u[2] else '')
                          for u in us[:8])))
        path = os.path.join(REPO, args.out_dir, 'sprite_%03d.png' % idx)
        render(path, rows, palette, args.zoom)
        print('           scritto %s' % path)


if __name__ == '__main__':
    main()
