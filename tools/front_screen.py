"""Le schermate del FRONT-END, preparate per la cartuccia (S48).

PERCHE' UN ARCHIVIO NOSTRO E NON I SOLITI SLOT DI RESS
------------------------------------------------------
Le nove schermate a pieno formato di RESS pesano 727 KB compressi e stanno
fuori dalla cartuccia per regola (hqr_pack.drop_fullscreen_slides). Il
front-end ne chiede due:

  RESS 14  il fondo del menu    61,7 KB   una fotografia: si tiene com'e'
  RESS 27  il logo Adeline      41,6 KB   il 92% dei suoi pixel e' UN SOLO
                                          indice (255, il bianco)

Sul logo quei 41,6 KB pagano soprattutto la cornice di bianco: il disegno vero
sta in 291x379 su 640x480. Ritagliato e ricompresso col nostro LZSS - lo stesso
che gira in hal_hqr.c - scende a 17,7 KB. Ventiquattro KB su un margine di
cartuccia che ne aveva cento: e' la differenza fra "ci sta" e "non ci sta".

Ricomprimere NON e' gratis in generale, ed e' misurato: sulle stesse slide
INTERE il nostro compressore fa PEGGIO dell'originale Adeline (42,7 KB contro
41,6 sul logo, 67,6 contro 61,7 sul menu). Il guadagno viene tutto dal
ritaglio, non dal compressore - per questo il menu resta uno slot RESS
ordinario e solo il logo passa di qui.

IL FORMATO (magic 'FSCR', tutto big-endian: lo legge il 68000)

    +0   'FSCR'
    +4   UWORD w, h        dimensioni del ritaglio
    +8   UWORD x0, y0      dove va nello schermo 640x480
    +12  UBYTE fill        indice con cui riempire tutto il resto
    +13  UBYTE pal         slot RESS della palette (255 = quella globale)
    +14  UWORD 0           allineamento
    +16  w*h byte          i pixel, riga per riga

Il colore di riempimento non e' un'assunzione: e' l'indice piu' frequente
dell'immagine intera, e il ritaglio e' il rettangolo di tutto quel che NON e'
quell'indice. Le due cose insieme rendono la ricostruzione esatta al bit, e
tools/menu_ref.py la verifica ricostruendo l'immagine e confrontandola con
l'originale prima di scrivere qualsiasi attesa.
"""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import hqr_decompress as lzss

MAGIC = b'FSCR'
HDR = 16
SLIDE_W = 640
SLIDE_H = 480

# Gli slot del nostro archivio FRONT. Indici NOSTRI, non di LBA1: qui non c'e'
# un originale da rispettare, e un elenco esplicito e' quel che serve al C.
FRONT_LOGO = 0

# (slot FRONT, slot RESS dell'immagine, slot RESS della palette)
SCREENS = [
    (FRONT_LOGO, 27, 28),
]


def crop(img, w=SLIDE_W, h=SLIDE_H):
    """(x0, y0, w, h, fill) del rettangolo utile, col colore di fondo."""
    counts = [0] * 256
    for b in img:
        counts[b] += 1
    fill = counts.index(max(counts))

    x0, y0, x1, y1 = w, h, -1, -1
    for y in range(h):
        row = img[y * w:(y + 1) * w]
        if row.count(fill) == w:
            continue
        if y0 > y:
            y0 = y
        y1 = y
        for x in range(w):
            if row[x] != fill:
                if x < x0:
                    x0 = x
                if x > x1:
                    x1 = x
    if x1 < 0:                       # immagine di un colore solo: caso limite
        return 0, 0, 1, 1, fill
    return x0, y0, x1 - x0 + 1, y1 - y0 + 1, fill


def pack(img, pal_slot, w=SLIDE_W, h=SLIDE_H):
    """Il blob FSCR di un'immagine a pieno formato."""
    x0, y0, cw, ch, fill = crop(img, w, h)
    out = bytearray(struct.pack('>4sHHHHBBH', MAGIC, cw, ch, x0, y0,
                                fill, pal_slot, 0))
    for y in range(y0, y0 + ch):
        out += img[y * w + x0:y * w + x0 + cw]
    return bytes(out)


def unpack(blob, w=SLIDE_W, h=SLIDE_H):
    """L'immagine a pieno formato ricostruita. E' l'ORACOLO del lato C:
    front_menu.c fa esattamente questo, e menu_ref.py confronta il risultato
    con l'originale prima di calcolare qualunque attesa."""
    magic, cw, ch, x0, y0, fill, pal, _ = struct.unpack_from('>4sHHHHBBH',
                                                             blob, 0)
    if magic != MAGIC:
        raise ValueError('non e\' un blob FSCR')
    img = bytearray([fill]) * (w * h)
    for y in range(ch):
        src = HDR + y * cw
        dst = (y0 + y) * w + x0
        img[dst:dst + cw] = blob[src:src + cw]
    return bytes(img), pal


def build_entries(ress, ress_off, store_lzss=True):
    """Le voci dell'archivio FRONT, nell'ordine dei suoi slot.

    `store_lzss` deposita la voce come una voce HQR ordinaria (intestazione di
    10 byte + carico compresso), cioe' nella stessa forma che hal_hqr.c sa gia'
    espandere: nessun percorso nuovo sul Jaguar.
    """
    n = max(s[0] for s in SCREENS) + 1
    entries = [None] * n
    for slot, ress_img, ress_pal in SCREENS:
        img = lzss.extract_entry(ress, ress_img, ress_off)
        blob = pack(img, ress_pal)
        # Verifica di andata e ritorno, qui e non altrove: se il ritaglio
        # perdesse un pixel, il difetto uscirebbe come "il logo ha una riga
        # sbagliata" a tre strati di distanza da chi l'ha prodotto.
        back, _ = unpack(blob)
        if back != img:
            raise SystemExit('FSCR: il roundtrip del ritaglio non torna '
                             '(slot RESS %d)' % ress_img)
        # L'incapsulamento e la verifica di andata e ritorno LZSS li fa
        # body_digest.hqr_entry: e' la stessa funzione che confeziona i corpi,
        # quindi il formato di voce non puo' divergere fra i due archivi.
        import body_digest
        entries[slot] = body_digest.hqr_entry(blob, lzss) if store_lzss else blob
    return entries


def unpack_raw(entry, store):
    """Il payload LOGICO di una voce: quel che HQR_Get restituisce al 68000.

    Per un archivio 'raw' la voce E' il blob; per uno compresso va tolta
    l'intestazione da 10 byte e srotolato il carico.
    """
    if entry is None:
        return None
    if store != 'lzss':
        return entry
    orig, comp, ctype = struct.unpack_from('<IIH', entry, 0)
    body = entry[10:10 + comp]
    return body if ctype == 0 else lzss.decompress_lzss(body, orig, ctype)
