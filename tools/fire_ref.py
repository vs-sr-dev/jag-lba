"""Il FUOCO del menu di LBA1, trascritto e guardato prima di portarlo (S48).

SOURCES/FIRE.C, che a sua volta e' una traduzione a mano dall'assembly 386.
Prima di scrivere una riga di C per il Jaguar si trascrive qui e si GUARDA:
un effetto animato non si verifica con una somma di controllo, si verifica a
occhio ([[feedback_visual_over_metric]]). E c'e' un motivo in piu' per non
fidarsi della lettura a mente: nel fork la "riga di alimentazione" scrive su
`Imagen2`, che il giro dopo viene riscritto per intero - se fosse davvero cosi'
il fuoco non avrebbe sorgente e si spegnerebbe. Questo strumento risponde alla
domanda invece di discuterla.

    python tools/fire_ref.py            frame di prova in build/fire_*.png

COME FUNZIONA (originale)
  due buffer 320x50, Imagen e Imagen2
  ComputeFire   per ogni pixel: somma degli 8 vicini, media, un decremento
                condizionato; poi Imagen[riga r] = Imagen2[riga r+1], cioe' il
                fuoco SALE di una riga per quadro
  alimentazione 5 righe a partire dalla 39: i valori <= 15 diventano 11-al
  DrawFire      un punto a 255 in una riga a caso della fascia 20..30, una
                volta su cinque
  DoFire        25 righe da 320 raddoppiate in orizzontale e verticale ->
                640x50 a schermo, colore = (pixel>>1) + base, clampato a
                base+15. La banda del menu e' 64..79.
"""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import hqr_decompress as lzss
from bg_ref import normalize_palette, write_png
import menu_ref

FIRE_W, FIRE_H = 320, 50
RESS_INIT_PLASMA = 51

# Il generatore pseudocasuale. Non deve coincidere con quello di LBA1 - i punti
# caldi sono rumore per costruzione - ma deve essere LO STESSO nei due lati
# (qui e in front_menu.c), altrimenti il PNG di riferimento e lo schermo del
# Jaguar mostrerebbero due fuochi diversi e non si saprebbe se e' un difetto.
_seed = 1


def rnd(n):
    global _seed
    _seed = (_seed * 1103515245 + 12345) & 0x7FFFFFFF
    return (_seed >> 16) % n


def compute_fire(img, img2, feed_on_img):
    """Un passo della simulazione. `feed_on_img` sceglie DOVE alimentare:
    False = alla lettera del fork (Imagen2, cioe' un'alimentazione che viene
    buttata), True = su Imagen dopo la copia, che e' l'unica lettura in cui
    l'alimentazione fa qualcosa."""
    for i in range(321, 48 * 320 - 1):
        ax = (img[i - 1] + img[i - 320] + img[i - 319] + img[i - 321]
              + img[i + 1] + img[i + 320] + img[i + 319] + img[i + 321])
        ax = ((ax >> 3) | ((ax & 0x07) << 13)) & 0xFFFF
        if not (ax & 0x6500):
            if (ax & 0xFF) or i >= 46 * 320:
                ax = (ax - 1) & 0xFFFF
        img2[i] = ax & 0xFF

    # Il fuoco SALE: la riga r prende la riga r+1 del buffer appena calcolato.
    img[0:48 * 320] = img2[320:320 + 48 * 320]

    feed = img if feed_on_img else img2
    for i in range(320 * 39, 320 * 44):
        if feed[i] <= 15:
            # In aritmetica a 8 bit, e non e' un dettaglio: `11 - al` con al fra
            # 12 e 15 va SOTTO ZERO e ricasca a 252..255, cioe' quasi bianco.
            # E' proprio quello il combustibile - senza il wrap l'alimentazione
            # spegnerebbe invece di accendere.
            feed[i] = (11 - feed[i]) & 0xFF


def spark(img):
    if rnd(5) == 0:
        img[rnd(320 * 10) + 20 * 320] = 255


def draw_fire(fb, img, y0, x0, x1, base):
    """Le 25 righe raddoppiate, ritagliate alla finestra del riquadro.

    L'originale scrive 640 pixel di larghezza a partire da x=0 e poi copia sullo
    schermo fisico solo il rettangolo della voce (CopyBlockPhys). Qui si scrive
    direttamente quel che si vede, quindi il ritaglio va fatto adesso: senza, il
    fuoco sborderebbe sul fondo del menu.
    """
    for r in range(25):
        src = 5 * 320 + r * 320
        for y in (y0 + r * 2, y0 + r * 2 + 1):
            if y < 0 or y >= menu_ref.H:
                continue
            row = y * menu_ref.W
            for x in range(x0, x1 + 1):
                v = (img[src + (x >> 1)] >> 1) + base
                if v > base + 15:
                    v = base + 15
                fb[row + x] = v


def main():
    with open(os.path.join(menu_ref.GOG_COMMON, 'RESS.HQR'), 'rb') as f:
        ress = f.read()
    off = lzss.parse_offsets(ress)
    pal = normalize_palette(lzss.extract_entry(ress, menu_ref.RESS_PAL, off))
    font = menu_ref.font_load(ress, off)
    texts = menu_ref.text_load()

    seed_blob = lzss.extract_entry(ress, RESS_INIT_PLASMA, off)
    print('RESS %d: %d byte (il buffer del fuoco ne vuole %d)'
          % (RESS_INIT_PLASMA, len(seed_blob), FIRE_W * FIRE_H))

    outdir = os.path.join(menu_ref.REPO, 'build')
    if not os.path.isdir(outdir):
        os.makedirs(outdir)

    x0, y0, x1, y1, _yc = menu_ref.item_rect(0)

    for tag, seeded, feed_on_img in (('fork', 1, False),
                                     ('cura', 1, True),
                                     ('vuoto', 0, True)):
        img = bytearray(seed_blob[:FIRE_W * FIRE_H]) if seeded \
            else bytearray(FIRE_W * FIRE_H)
        img2 = bytearray(FIRE_W * FIRE_H)
        global _seed
        _seed = 1

        for n in range(1, 61):
            spark(img)
            compute_fire(img, img2, feed_on_img)
            if n in (1, 5, 20, 60):
                fb = bytearray(lzss.extract_entry(ress, menu_ref.RESS_MENU_PCR,
                                                  off))
                draw_fire(fb, img, y0, x0, x1, menu_ref.COUL_SELECT & 0xF0)
                menu_ref.cadre(fb, x0, y0, x1, y1)
                s = texts[menu_ref.MENU_TEXTS[0]]
                menu_ref.font_draw(fb, font,
                                   menu_ref.MENU_CX
                                   - menu_ref.font_width(font, s) // 2,
                                   _yc + menu_ref.TEXT_DY, s,
                                   menu_ref.COUL_TEXT)
                path = os.path.join(outdir, 'fire_%s_%02d.png' % (tag, n))
                menu_ref.preview(path, fb, pal)
                print('  %s frame %2d: media %5.1f  max %3d  -> %s'
                      % (tag, n, sum(img) / len(img), max(img),
                         os.path.basename(path)))


if __name__ == '__main__':
    main()
