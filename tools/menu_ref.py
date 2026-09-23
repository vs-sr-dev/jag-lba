"""Riferimento del FRONT-END: logo Adeline e menu principale (S48).

COSA FA, E PERCHE' DUE COSE INSIEME
-----------------------------------
1. Disegna le due schermate con le REGOLE DELL'ORIGINALE, lette da
   SOURCES/GAMEMENU.C, e ne scrive il PNG. E' il confronto a occhio contro il
   PC DOS: dice se le regole sono quelle giuste.
2. Ne calcola le ATTESE e le scrive in engine_c/menu_expect.h, cosi' il
   Jaguar confronta il proprio framebuffer invece di farlo guardare a noi.

Le due prove rispondono a domande diverse e servono ENTRAMBE. Il PNG dice
"queste sono le regole del menu di LBA1"; le attese dicono "il 68000 le esegue
come le esegue Python". Nessuna delle due copre l'altra: la geometria la
dichiara questo file e il C la riceve dall'header generato, quindi un errore
DENTRO la geometria passerebbe il confronto e lo prenderebbe solo l'occhio
([[feedback_buco_simmetrico_oracolo]]).

LE REGOLE, UNA PER UNA (SOURCES/GAMEMENU.C)
-------------------------------------------
  GameMainMenu[]      quattro voci, centrate su y=200, testi 20/21/23/22
  HAUTEUR_STANDARD 50, MENU_SPACE 6, LargeurMenu 550
  DrawOneChoice       x0 = 320-275, x1 = 320+275, y0 = y-25, y1 = y+25
    voce selezionata  Box(x0,y0,x1,y1, COUL_SELECT_MENU=68)
                      E' la riga che l'originale tiene COMMENTATA sopra
                      DrawFire: il fuoco animato e' il passo successivo, e
                      questo e' il fondo che ci stava prima, non un'invenzione.
    voce normale      ShadeBox(...,4): dentro ogni banda di 16 colori il
                      pixel scende di 4, fermandosi al fondo della banda
    cornice           DrawCadre: alto/sinistra 79, basso/destra 73
    testo             CoulFont(15), centrato su x=320, a y-18
  font                RESS 1: 256 glifi [DX,DY,HotX,HotY] + per riga NbBlock e
                      blocchi alternati salta/scrivi (LIB_SVGA/MASK_A.ASM)
  InterLeave 2, InterSpace 7 (MESSAGE.C, quel che il menu ha attivo)
"""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import hqr_decompress as lzss
import front_screen
from bg_ref import fletcher, normalize_palette, write_png

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOG_COMMON = r'I:\GOG\Little Big Adventure\Common'
GOG_CLASSIC = r'I:\GOG\Little Big Adventure\CommonClassic'

W, H = 640, 480

# --- le costanti dell'originale, e nessun numero magico oltre queste ---
MENU_Y_CENTER = 200
ITEM_H = 50                 # HAUTEUR_STANDARD
ITEM_SPACE = 6              # MENU_SPACE
ITEM_W = 550                # LargeurMenu
MENU_CX = 320
COUL_TEXT = 15              # COUL_TEXT_MENU
COUL_SELECT = 68            # COUL_SELECT_MENU
COUL_CADRE_HI = 79
COUL_CADRE_LO = 73
SHADE_DEC = 4
TEXT_DY = -18
INTER_LEAVE = 2
INTER_SPACE = 7

# I quattro testi del menu principale, nell'ordine di GameMainMenu[].
MENU_TEXTS = (20, 21, 23, 22)

RESS_MENU_PCR = 14
RESS_PAL = 0
RESS_FONT = 1


# ---------------------------------------------------------------------------
# Il font
# ---------------------------------------------------------------------------
def font_load(ress, ress_off):
    """Il banco dei glifi: lista di offset dentro il blob, piu' il blob."""
    blob = lzss.extract_entry(ress, RESS_FONT, ress_off)
    n = struct.unpack_from('<I', blob, 0)[0] // 4
    offs = struct.unpack_from('<%dI' % n, blob, 0)
    return blob, offs


def glyph_width(font, c):
    blob, offs = font
    if c == 32:
        return INTER_SPACE
    if c >= len(offs):
        return 0
    return blob[offs[c]] + INTER_LEAVE


def font_width(font, s):
    return sum(glyph_width(font, c) for c in s.encode('latin-1'))


def font_draw(fb, font, x, y, s, colour):
    """Il testo, un glifo alla volta. Torna i pixel scritti (sonda)."""
    blob, offs = font
    n = 0
    for c in s.encode('latin-1'):
        if c == 32:
            x += INTER_SPACE
            continue
        if c >= len(offs):
            continue
        g = offs[c]
        dx, dy, hx, hy = blob[g], blob[g + 1], blob[g + 2], blob[g + 3]
        p = g + 4
        gy = y + hy
        for _row in range(dy):
            nb = blob[p]
            p += 1
            gx = x + hx
            b = 0
            while b < nb:
                gx += blob[p]                        # salta
                p += 1
                b += 1
                if b >= nb:
                    break
                run = blob[p]
                p += 1
                b += 1
                if 0 <= gy < H:
                    for k in range(run):
                        if 0 <= gx + k < W:
                            fb[gy * W + gx + k] = colour
                            n += 1
                gx += run
            gy += 1
        x += dx + INTER_LEAVE
    return n


# ---------------------------------------------------------------------------
# I testi
# ---------------------------------------------------------------------------
def text_load(path=None):
    """Il dialogo 0 di TEXT.HQR: {identificatore: stringa}.

    Slot 0 = tabella di identificatori (UWORD), slot 1 = tabella di offset
    (UWORD, uno per identificatore) seguita dalle stringhe terminate da zero.
    E' la stessa struttura che legge GetMultiText.
    """
    path = path or os.path.join(GOG_CLASSIC, 'TEXT.HQR')
    with open(path, 'rb') as f:
        data = f.read()
    off = lzss.parse_offsets(data)
    ids = lzss.extract_entry(data, 0, off)
    body = lzss.extract_entry(data, 1, off)
    n = len(ids) // 2
    out = {}
    for i in range(n):
        num = struct.unpack_from('<H', ids, i * 2)[0]
        pos = struct.unpack_from('<H', body, i * 2)[0]
        end = body.index(b'\x00', pos)
        out[num] = body[pos:end].decode('latin-1')
    return out


# ---------------------------------------------------------------------------
# I mattoni del disegno, gli stessi di FUNC.C / GAMEMENU.C
# ---------------------------------------------------------------------------
def box(fb, x0, y0, x1, y1, colour):
    for y in range(y0, y1 + 1):
        fb[y * W + x0:y * W + x1 + 1] = bytes([colour]) * (x1 - x0 + 1)


def shade_box(fb, x0, y0, x1, y1, dec):
    for y in range(y0, y1 + 1):
        base = y * W
        for x in range(x0, x1 + 1):
            v = fb[base + x]
            lo, hi = v & 0x0F, v & 0xF0
            fb[base + x] = (hi + lo - dec) if lo >= dec else hi


def cadre(fb, x0, y0, x1, y1):
    box(fb, x0, y0, x1, y0, COUL_CADRE_HI)
    box(fb, x0, y0, x0, y1, COUL_CADRE_HI)
    box(fb, x1, y0 + 1, x1, y1, COUL_CADRE_LO)
    box(fb, x0 + 1, y1, x1, y1, COUL_CADRE_LO)


def preview(path, fb, palette):
    """Il PNG 640x480. bg_ref.write_preview e' inchiodato a 640x592, che e' il
    fondo del gioco: qui la schermata e' quella del display e basta."""
    rgb = bytearray(W * H * 3)
    for i, idx in enumerate(fb):
        r, g, b = palette[idx]
        rgb[i * 3], rgb[i * 3 + 1], rgb[i * 3 + 2] = r, g, b
    write_png(path, W, H, bytes(rgb))


def item_rect(n, nb=len(MENU_TEXTS)):
    """(x0, y0, x1, y1, ycenter) della voce n."""
    y = MENU_Y_CENTER - (ITEM_H * nb + (nb - 1) * ITEM_SPACE) // 2
    y += n * (ITEM_H + ITEM_SPACE)
    return (MENU_CX - ITEM_W // 2, y - ITEM_H // 2,
            MENU_CX + ITEM_W // 2, y + ITEM_H // 2, y)


def fire_seed_image(ress, off):
    """Il seme del fuoco: RESS 51, i primi 320x50 byte.

    L'import e' LOCALE apposta: fire_ref importa questo modulo, e un import in
    testa qui li farebbe girare in tondo. Chi arriva da fire_ref lo trova gia'
    caricato, chi arriva da qui lo carica adesso."""
    import hqr_decompress as _lzss
    import fire_ref
    blob = _lzss.extract_entry(ress, fire_ref.RESS_INIT_PLASMA, off)
    n = fire_ref.FIRE_W * fire_ref.FIRE_H
    if blob is None or len(blob) < n:
        return None
    return bytearray(blob[:n])


def draw_menu(fb, font, texts, selected, fire=None):
    """Le quattro voci sopra il fondo gia' in fb. Torna i pixel di testo.

    `fire` e' il buffer del fuoco: se c'e', la voce selezionata non e' un
    rettangolo pieno ma la fiamma, esattamente come fa `item_draw` in
    engine_c/front_menu.c. Al primo disegno il buffer e' il SEME senza passi di
    simulazione, che e' quel che il menu mostra nel quadro in cui compare."""
    import fire_ref
    npix = 0
    for n, num in enumerate(MENU_TEXTS):
        x0, y0, x1, y1, yc = item_rect(n)
        if n == selected:
            if fire is not None:
                fire_ref.draw_fire(fb, fire, y0, x0, x1, COUL_SELECT & 0xF0)
            else:
                box(fb, x0, y0, x1, y1, COUL_SELECT)
        else:
            shade_box(fb, x0, y0, x1, y1, SHADE_DEC)
        cadre(fb, x0, y0, x1, y1)
        s = texts[num]
        npix += font_draw(fb, font, MENU_CX - font_width(font, s) // 2,
                          yc + TEXT_DY, s, COUL_TEXT)
    return npix


# ---------------------------------------------------------------------------
def main():
    with open(os.path.join(GOG_COMMON, 'RESS.HQR'), 'rb') as f:
        ress = f.read()
    ress_off = lzss.parse_offsets(ress)
    font = font_load(ress, ress_off)
    texts = text_load()
    pal_game = normalize_palette(lzss.extract_entry(ress, RESS_PAL, ress_off))

    outdir = os.path.join(REPO, 'build')
    if not os.path.isdir(outdir):
        os.makedirs(outdir)

    rows = []

    # --- il logo, per la strada che percorre davvero: blob FSCR -> schermo ---
    logo_src = lzss.extract_entry(ress, 27, ress_off)
    blob = front_screen.pack(logo_src, 28)
    logo, pal_slot = front_screen.unpack(blob)
    if logo != logo_src:
        raise SystemExit('FSCR: la ricostruzione del logo non torna')
    pal_logo = normalize_palette(lzss.extract_entry(ress, pal_slot, ress_off))
    s1, s2 = fletcher(logo)
    fill = blob[12]
    nfill = logo.count(fill)
    w, h, x0, y0 = struct.unpack_from('>HHHH', blob, 4)
    print('logo Adeline: ritaglio %dx%d a (%d,%d), fondo indice %d, '
          'palette RESS %d' % (w, h, x0, y0, fill, pal_slot))
    print('  %d pixel di fondo su %d (%.1f%%)'
          % (nfill, W * H, 100.0 * nfill / (W * H)))
    preview(os.path.join(outdir, 'menu_ref_logo.png'), logo, pal_logo)
    rows += [('LOGO_SUM1', s1, 'Fletcher s1 del logo a schermo'),
             ('LOGO_SUM2', s2, 'Fletcher s2 del logo a schermo'),
             ('LOGO_GEOM', (w << 16) | h, 'ritaglio: w | h'),
             ('LOGO_POS', (x0 << 16) | y0, 'ritaglio: x0 | y0'),
             ('LOGO_FILL', (fill << 8) | pal_slot, 'riempimento | palette')]

    # --- il menu ---
    fb = bytearray(lzss.extract_entry(ress, RESS_MENU_PCR, ress_off))
    fire = fire_seed_image(ress, ress_off)
    if fire is None:
        raise SystemExit('RESS 51 (il seme del fuoco) manca o e\' corto: '
                         'l\'attesa del menu non si puo\' calcolare')
    npix = draw_menu(fb, font, texts, 0, fire)
    s1, s2 = fletcher(fb)
    widths = [font_width(font, texts[n]) for n in MENU_TEXTS]
    tsum = sum(sum(texts[n].encode('latin-1')) for n in MENU_TEXTS)
    for n, num in enumerate(MENU_TEXTS):
        x0, y0, x1, y1, yc = item_rect(n)
        print('  voce %d (testo %3d) "%s": larghezza %d, riquadro %d,%d-%d,%d'
              % (n, num, texts[num], widths[n], x0, y0, x1, y1))
    print('  pixel di testo %d, somma dei caratteri %d' % (npix, tsum))
    preview(os.path.join(outdir, 'menu_ref_main.png'), fb, pal_game)
    rows += [('MENU_SUM1', s1, 'Fletcher s1 del menu (voce 0 scelta)'),
             ('MENU_SUM2', s2, 'Fletcher s2 del menu (voce 0 scelta)'),
             ('MENU_TPIX', npix, 'pixel scritti dal font'),
             ('MENU_TSUM', tsum, 'somma dei caratteri delle quattro voci'),
             ('MENU_W01', (widths[0] << 16) | widths[1], 'larghezze 0 | 1'),
             ('MENU_W23', (widths[2] << 16) | widths[3], 'larghezze 2 | 3')]

    hdr = os.path.join(REPO, 'engine_c', 'menu_expect.h')
    with open(hdr, 'w', newline='\n') as f:
        f.write(gen_header(rows))
    print('  scritto engine_c/menu_expect.h (%d sonde)' % len(rows))
    for name, value, label in rows:
        print('    %-10s %08X  %s' % (name, value & 0xFFFFFFFF, label))


def gen_header(rows):
    out = ['/* GENERATO DA tools/menu_ref.py - NON MODIFICARE A MANO.\n',
           ' *\n',
           ' * Le attese del front-end (logo Adeline e menu principale) e la\n',
           ' * geometria con cui sono state calcolate. Il C prende ENTRAMBE da\n',
           ' * qui: due copie della stessa geometria darebbero un menu giusto\n',
           ' * in Python e storto sul Jaguar senza che nessuna sonda cada.\n',
           ' */\n',
           '#ifndef JAG_MENU_EXPECT_H\n#define JAG_MENU_EXPECT_H\n\n',
           '/* --- la geometria, da SOURCES/GAMEMENU.C --- */\n']
    for name, value in (('MN_ITEM_H', ITEM_H), ('MN_ITEM_SPACE', ITEM_SPACE),
                        ('MN_ITEM_W', ITEM_W), ('MN_CX', MENU_CX),
                        ('MN_Y_CENTER', MENU_Y_CENTER),
                        ('MN_COUL_TEXT', COUL_TEXT),
                        ('MN_COUL_SELECT', COUL_SELECT),
                        ('MN_COUL_CADRE_HI', COUL_CADRE_HI),
                        ('MN_COUL_CADRE_LO', COUL_CADRE_LO),
                        ('MN_SHADE_DEC', SHADE_DEC),
                        ('MN_TEXT_DY', TEXT_DY),
                        ('MN_INTER_LEAVE', INTER_LEAVE),
                        ('MN_INTER_SPACE', INTER_SPACE),
                        ('MN_NB_ITEMS', len(MENU_TEXTS)),
                        ('MN_RESS_MENU', RESS_MENU_PCR),
                        ('MN_RESS_FONT', RESS_FONT),
                        ('MN_FRONT_LOGO', front_screen.FRONT_LOGO)):
        out.append('#define %-18s %d\n' % (name, value))
    out.append('\n/* Gli identificatori dei testi, nell\'ordine di '
               'GameMainMenu[]. */\n')
    out.append('#define MN_TEXT_IDS { %s }\n'
               % ', '.join(str(n) for n in MENU_TEXTS))
    out.append('\n/* --- le attese --- */\n')
    for name, value, label in rows:
        out.append('#define MN_%-11s 0x%08XUL  /* %s */\n'
                   % (name, value & 0xFFFFFFFF, label))
    out.append('\n#define MN_EXPECT_COUNT %d\n' % len(rows))
    # La stessa tabella in forma di ELENCO, piu' gli indici: il C confronta in
    # un ciclo e mostra una maschera con un bit per sonda, come tutte le altre
    # milestone. Nomi e ordine escono da qui, quindi non possono scivolare.
    out.append('\n#define MN_EXPECT_LIST { \\\n')
    for name, value, _label in rows:
        out.append('    MN_%s, \\\n' % name)
    out.append('    0 }\n\n')
    out.append('enum {\n')
    for i, (name, _v, label) in enumerate(rows):
        out.append('    MNX_%-11s = %2d,   /* %s */\n' % (name, i, label))
    out.append('};\n')
    out.append('\n#endif /* JAG_MENU_EXPECT_H */\n')
    return ''.join(out)


if __name__ == '__main__':
    main()
