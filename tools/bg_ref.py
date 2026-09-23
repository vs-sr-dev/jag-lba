#!/usr/bin/env python3
"""JagLBA V6 / M2b - il fondo di una scena, calcolato offline.

Questo file ha DUE mestieri, e non e' una comodita': e' il metodo.

  1. RENDERIZZA il fondo esattamente come lo renderizzera' il Jaguar, e ne
     scrive un PNG. Serve al confronto a occhio contro il PC DOS, che su
     contenuto texturato batte qualunque numero di errore RGB
     ([[feedback_visual_over_metric]]).

  2. Ne calcola le ATTESE e le scrive in engine_c/bg_expect.h, cosi' il
     confronto sul Jaguar non lo fa l'occhio ma il C
     ([[feedback_expected_values_in_the_build]]). "Si vede la scena" non e' un
     criterio: si vede *una* scena anche quando la camera e' sbagliata di una
     cella, quando manca un brick su dieci, quando l'ordine del pittore e'
     invertito. Un'immagine plausibile e' il modo tipico in cui questo genere di
     codice fallisce.

LE SONDE, E PERCHE' PROPRIO QUESTE
----------------------------------
Due somme di controllo Fletcher a 32 bit sui long del framebuffer (`s1 += w;
s2 += s1`): la prima da' il contenuto, la seconda l'ORDINE, quindi due pixel
scambiati non si compensano. Il Jaguar le ricalcola leggendo long, non byte:
un quarto delle iterazioni e nessun problema di allineamento.

Piu' tre sonde che, se la somma cade, dicono SUBITO di che morte si e' morti
invece di lasciare un "non torna":

  bbox dei pixel non trasparenti  -> immagine traslata (camera, hot spot, vx/vy)
  numero di pixel non trasparenti -> brick mancanti o clipping sbagliato
  celle disegnate / brick decodificati -> percorso griglia+BLL sbagliato

Un'immagine traslata di 24 righe da' bbox diverso e somme diverse; un brick
mancante da' lo stesso bbox e conteggio diverso. Sono diagnosi distinte.

USO
    python tools/bg_ref.py                  # scena 0: PNG + bg_expect.h
    python tools/bg_ref.py --scene 12
    python tools/bg_ref.py --cam 52,53      # sovrascrive la camera (confronti)
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import lba_paths
import argparse
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from hqr_decompress import parse_offsets, extract_entry
from render_slide import normalize_palette, write_png

# La capienza della lista di celle la dichiara il padrone della mappa, non
# questo file: ricopiarla vorrebbe dire poterla cambiare da una parte sola e
# scoprirlo sul Jaguar con un TheEnd.
from memory_map import CELL_SLOTS

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOG_COMMON = lba_paths.COMMON

# ---------------------------------------------------------------------------
# Geometria. NON e' piu' una taratura: e' la trascrizione di Map2Screen
# (SOURCES/GRILLE_A.ASM:446, identica in GRILLE_A.C:209), cioe' della routine
# che il gioco usa per decidere dove finisce ogni tessera.
#
#   XScreen = (xm - zm)*24            + 320-8-23-1   -> ISO_X = 24, CAM_CX = 288
#   YScreen = (xm + zm)*12 - ym*15    + 240-25       -> ISO_Y = 12, CAM_CY = 215
#
# con xm/ym/zm relativi a Start[XYZ]Cube. Il rapporto 2:1 fra i passi X e Y e'
# l'invariante dell'isometrica di LBA1 ([[feedback_lba1_iso_invariant]]) e qui
# si vede da dove viene.
#
# S30: le due costanti di centratura erano 323 e 191, tarate a occhio in S20.
# La Y si spiega tutta: 191 era giusta per la cella TRONCATA (52,53), e con la
# cella che da' la regola del gioco (53,54) diventa 191+24 = 215, cioe'
# esattamente il 240-25 di Map2Screen. I due errori si annullavano, ed e' il
# motivo per cui la taratura a occhio sembrava perfetta. La X invece era
# sbagliata di 35 px e non lo compensava niente: partiva da "320 = mezzo
# schermo", che e' un'assunzione, non una misura.
# ---------------------------------------------------------------------------
BG_W, BG_H = 640, 592

DOS_W, DOS_H = 640, 480   # lo schermo del PC DOS. BG_H e' piu' alto: vedi sotto.

ISO_X = 24          # passo X per cella di griglia          (Map2Screen: *16 + *8)
ISO_Y = 12          # passo Y per cella di griglia (2:1)    (Map2Screen: *8 + *4)
ISO_GY = 15         # passo Y per livello verticale         (Map2Screen: *16 - *1)

# COMMON.H:58-60. La cella del cubo in unita' di mondo: servono a M3d per
# passare dalla posizione di un attore alla cella su cui sta.
SIZE_BRICK_XZ = 512
SIZE_BRICK_Y = 256
DEMI_BRICK_XZ = 256
CAM_CX = 288        # 320-8-23-1, "XCentre proj"
CAM_CY = 215        # 240-25,     "YCentre proj"

# Finestra di rifiuto per tessera, presa da AffBrickBlock (GRILLE.C:631):
#
#   if (XScreen >= -24 AND XScreen < 640 AND YScreen >= -38 AND YScreen < 480)
#
# Il confronto e' sul punto di ancoraggio PRIMA del hot spot, che AffGraph
# aggiunge per conto suo. Non e' un margine di sicurezza inventato come il
# vecchio CELL_MARGIN = 128: e' il taglio che il gioco fa davvero, quindi
# riprodurlo e' l'unico modo perche' "identico al PC DOS" valga anche ai bordi.
#
# Nota sulla Y: il rifiuto usa 480, l'altezza dello schermo DOS, non le 592
# righe del nostro buffer. Le righe oltre la 480 esistono solo perche' una
# tessera ancorata a 479 va scritta per intero invece che ritagliata a meta'.
DOS_CLIP_X0, DOS_CLIP_X1 = -24, DOS_W
DOS_CLIP_Y0, DOS_CLIP_Y1 = -38, DOS_H

CUBE_X, CUBE_Y, CUBE_Z = 64, 25, 64

# Indici CLUT riservati alla diagnostica. Sono due dei quattordici slot
# "riservati UI" che LBA1 riempie di bianco e che nessun brick usa: presi da
# li', le righe esadecimali non rubano un colore alla scena. La verifica non e'
# un atto di fede, la fa --check-clut.
DBG_IDX_FG = 2
DBG_IDX_LBL = 3


# ---------------------------------------------------------------------------
# Decodifica dei tre formati. Sono trascrizioni, non ipotesi:
#   colonne della griglia  <- DecompColonne, GRILLE_A.ASM
#   blocchi BLL            <- il consumatore in GRILLE.C
#   brick RLE              <- GRAPHMSK.ASM
# Il C sul Jaguar fa le stesse tre cose sugli stessi byte (little endian
# verbatim dalla cartuccia, vedi engine_c/le.h).
# ---------------------------------------------------------------------------
def decomp_column(data, offset):
    pos = offset
    nb = data[pos]
    pos += 1
    cells = []
    for _ in range(nb):
        op = data[pos]
        pos += 1
        count = (op & 0x3F) + 1
        mode = op & 0xC0
        if mode == 0x00:
            cells.extend([0] * count)
        elif mode == 0x80:
            v = data[pos] | (data[pos + 1] << 8)
            pos += 2
            cells.extend([v] * count)
        else:
            for _i in range(count):
                cells.append(data[pos] | (data[pos + 1] << 8))
                pos += 2
    return cells


def decode_grid(payload):
    """(cube[gz][gx][gy], colonne fuori misura).

    UWORD = NumBlock (byte basso) + PosBlock (byte alto).

    Il secondo valore e' una sonda, non una statistica: una colonna che non
    produce esattamente CUBE_Y celle, sul Jaguar, scriverebbe dentro la colonna
    successiva. Il risultato sarebbe un fondo quasi giusto con qualche tessera
    sbagliata - cioe' il genere di guasto che si scambia per "manca un brick".
    """
    cube = [[None] * CUBE_X for _ in range(CUBE_Z)]
    odd = 0
    for gz in range(CUBE_Z):
        for gx in range(CUBE_X):
            i = (gx + gz * CUBE_Z) * 2
            off = payload[i] | (payload[i + 1] << 8)
            cells = decomp_column(payload, off)
            if len(cells) != CUBE_Y:
                odd += 1
            if len(cells) < CUBE_Y:
                cells = cells + [0] * (CUBE_Y - len(cells))
            cube[gz][gx] = cells[:CUBE_Y]
    return cube, odd


def clut_sum(palette):
    """La CLUT che il Jaguar caricera', e la sua somma.

    Il formato e' R5 B5 G6, NON RGB565: il verde sta in basso. Sbagliarlo
    produce un'immagine con rossi e blu plausibili e i verdi da un'altra parte,
    che e' esattamente il modo in cui questo errore si nasconde - percio' la
    somma e' una sonda, e la palette non si giudica a occhio.

    I due indici della diagnostica sono forzati qui come sul Jaguar: la CLUT
    che si confronta e' quella che esiste davvero, non quella che uscirebbe
    dalla sola conversione.
    """
    words = []
    for (r, g, b) in palette:
        words.append(((r >> 3) << 11) | ((b >> 3) << 6) | (g >> 2))
    words[DBG_IDX_FG] = 0xFFFF
    words[DBG_IDX_LBL] = 0x4210
    return sum(words) & 0xFFFFFFFF


def parse_bll(payload):
    """Lista di blocchi; ogni blocco e' la lista dei suoi brick_ref (1-based)."""
    first = struct.unpack_from('<I', payload, 0)[0]
    n = first // 4
    blocks = []
    for i in range(n):
        off = struct.unpack_from('<I', payload, i * 4)[0]
        dx, dy, dz = payload[off], payload[off + 1], payload[off + 2]
        cp = off + 3
        refs = []
        for _c in range(dx * dy * dz):
            refs.append(struct.unpack_from('<H', payload, cp + 2)[0])
            cp += 4
        blocks.append(refs)
    return blocks


def decode_brick(payload):
    """(dx, dy, hotx, hoty, righe di indici) con 0 = trasparente.

    Nel formato non esiste un indice "trasparente" separato: la trasparenza e'
    l'opcode di salto (mode 00), che lascia il pixel intatto. Qui e sul Jaguar
    quel pixel resta 0 e il blit lo salta, che e' la stessa cosa solo se
    l'indice 0 non e' anche un colore vero. In LBA1 non lo e' (pal[0] = nero di
    sfondo), ed e' la ragione per cui il packer V4 spostava a 1 gli altri neri.
    """
    dx, dy, hx, hy = payload[0], payload[1], payload[2], payload[3]
    rows = []
    pos = 4
    for _r in range(dy):
        nb = payload[pos]
        pos += 1
        row = [0] * dx
        col = 0
        for _b in range(nb):
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
                        row[col + i] = payload[pos + i]
                pos += count
                col += count
        rows.append(row)
    return dx, dy, hx, hy, rows


# ---------------------------------------------------------------------------
# Larghezza sorgente del Blitter. A2 codifica la larghezza come mantissa a 4
# bit + esponente a 4 bit, quindi NON tutte le larghezze esistono: la riga del
# brick va allargata alla prossima larghezza rappresentabile, e il riempimento
# e' trasparente. E' la ragione per cui il buffer di lavoro non ha DX byte per
# riga ma padded_DX. Le due tabelle di V4 (PadDxLut / PadFlagsLut) sono qui
# CALCOLATE invece che scritte a mano: 64 valori copiati sono 64 occasioni di
# sbagliarne uno in silenzio.
# ---------------------------------------------------------------------------
def blitter_width(dx):
    """(larghezza rappresentabile >= dx, campo WID a 6 bit).

    Codifica del TRM Rev.8 (pagina 66 e sezione "Blitter window width"): la
    larghezza e' un virgola mobile con esponente a 4 bit e mantissa a 3 bit di
    cui la prima IMPLICITA, cioe' `1.mm x 2^e`. Il campo vale `eeee mm`, e

        larghezza = (4 + m) << (e - 2)

    Controprova sui due esempi del TRM: 20 = 1.01 x 2^4 -> `0100 01` = 17, e
    640 = 1.01 x 2^9 -> `1001 01` = 37 - che e' esattamente il WID che V4 usa
    per il clear a schermo intero (`$00014A18`). Quindi la formula non e'
    ricostruita a naso.

    Le larghezze DISPARI (1, 3, 5, 7) sono rappresentabili, ma qui si scartano:
    la tabella di V4, che e' l'unica configurazione validata a schermo, si ferma
    alle pari. Si perde al massimo un byte per riga su brick minuscoli, e in
    cambio questa funzione riproduce quella tabella valore per valore
    (`--check-lut` lo pretende) invece di somigliarle.
    """
    best = None
    for e in range(0, 12):              # il TRM: esponente valido 0..11
        for m in range(0, 4):
            num = (4 + m) << e
            if num & 3:
                continue                # non intero dopo la divisione per 4
            w = num >> 2
            if w >= max(dx, 2) and w % 2 == 0 and (best is None or w < best[0]):
                best = (w, (e << 2) | m)
    return best


def a2_flags(dx):
    """A2_FLAGS del brick: PITCH1 | PIXEL8 | WID | XADDPIX.

    Confrontata con PadFlagsLut di V4 in --check-lut: se una delle due
    tabelle e' sbagliata, lo si scopre qui e non guardando lo schermo.
    """
    w, wid = blitter_width(dx)
    return w, 0x00010018 | (wid << 9)


# ---------------------------------------------------------------------------
def load_common(gog):
    out = {}
    for key, fn in (('brk', 'LBA_BRK.HQR'), ('bll', 'LBA_BLL.HQR'),
                    ('gri', 'LBA_GRI.HQR'), ('ress', 'RESS.HQR')):
        path = os.path.join(gog, fn)
        if not os.path.isfile(path):
            raise SystemExit('manca %s' % path)
        data = open(path, 'rb').read()
        out[key] = (data, parse_offsets(data))
    return out


def scene_camera(num):
    """La camera iniziale dalla regola del gioco, non da una tabella.

    Passa da tools/scene_parse.py, che e' la trascrizione di LoadScene(): cosi'
    il valore qui e il valore che il Jaguar calcola in Scene_Derive vengono
    dalla stessa fonte invece che da due copie che possono divergere.
    """
    import scene_parse
    _buf, _entries, scenes = scene_parse.load_all()
    s = scenes[num]
    if s is None:
        raise SystemExit('scena %d assente dal contenitore' % num)
    return scene_parse.camera_init(s)


def first_change_cube(num):
    """(scena di destinazione, (x, y, z) d'ingresso) della prima zona di tipo 0.

    None se la scena non ne ha. E' la stessa lista che il Jaguar percorre in
    Scene_Derive, letta dagli stessi byte."""
    import scene_parse
    _buf, _entries, scenes = scene_parse.load_all()
    s = scenes[num]
    if s is None:
        return None
    cc = scene_parse.change_cubes(s)
    if not cc:
        return None
    z = cc[0]
    return (z.num, (z.i0, z.i1, z.i2))


def scene_parse_camera_of(x, z):
    """La regola della camera applicata a una posizione qualunque, non solo a
    quella di partenza della scena. Serve per l'ingresso da una change-cube."""
    import scene_parse
    return (scene_parse.camera_init_xz(x, z))


def scene_parse_camera_y_of(y):
    import scene_parse
    return scene_parse.camera_init_y_of(y)


def scene_camera_y(num):
    """StartYCube, dalla stessa regola e dalla stessa fonte delle altre due."""
    import scene_parse
    _buf, _entries, scenes = scene_parse.load_all()
    s = scenes[num]
    if s is None:
        raise SystemExit('scena %d assente dal contenitore' % num)
    return scene_parse.camera_init_y(s)


def used_bricks(num, common):
    """Gli indici LBA_BRK referenziati da una scena. Lo usa anche hqr_pack."""
    gri, gri_off = common['gri']
    bll, bll_off = common['bll']
    cube, _odd = decode_grid(extract_entry(gri, num, gri_off))
    blocks = parse_bll(extract_entry(bll, num, bll_off))
    used = set()
    for gz in range(CUBE_Z):
        for gx in range(CUBE_X):
            col = cube[gz][gx]
            for gy in range(CUBE_Y):
                v = col[gy]
                if not v:
                    continue
                nb = v & 0xFF
                pb = v >> 8
                if nb == 0 or nb - 1 >= len(blocks):
                    continue
                refs = blocks[nb - 1]
                if pb >= len(refs) or refs[pb] == 0:
                    continue
                used.add(refs[pb] - 1)
    return used


def blit_cell(fb, bricks, cell, vx, vy, cx0, cy0, cx1, cy1):
    """UNA cella, ritagliata a un rettangolo qualsiasi invece che allo schermo.

    E' il corpo del ciclo di disegno estratto, con i quattro limiti diventati
    argomenti: con (0,0,639,591) fa quel che faceva prima, con il rettangolo di
    un attore e' il `CopyMask` sotto `SetClip` di DrawOverBrick. Gli estremi
    sono INCLUSIVI, come ClipXmax/ClipYmax del gioco.

    Torna 1 se ha disegnato, cosi' il conteggio delle celle resta quello di
    prima (la sonda CELLS non si deve muovere)."""
    px, py, ref = cell[0], cell[1], cell[2]
    sx_col = px + vx
    if sx_col < DOS_CLIP_X0 or sx_col >= DOS_CLIP_X1:
        return 0                               # AffBrickBlock, meta' sulla X
    sy_cell = py + vy
    if sy_cell < DOS_CLIP_Y0 or sy_cell >= DOS_CLIP_Y1:
        return 0                               # AffBrickBlock, meta' sulla Y
    b = bricks.get(ref)
    if b is None:
        return 0
    dx, dy, hx, hy, rows = b
    sx = sx_col + hx
    sy = sy_cell + hy
    if sx + dx <= cx0 or sx > cx1 or sy + dy <= cy0 or sy > cy1:
        return 0
    for r in range(dy):
        y = sy + r
        if y < cy0 or y > cy1:
            continue
        row = rows[r]
        base = y * BG_W
        for c in range(dx):
            p = row[c]
            if not p:
                continue                       # indice 0 = trasparente
            x = sx + c
            if cx0 <= x <= cx1:
                fb[base + x] = p
    return 1


def draw_over_brick(fb, info, xm, ym, zm, rect, doors=False):
    """M3d: le tessere DAVANTI all'attore, ridisegnate sopra di lui.

    `DrawOverBrick` (GRILLE.C:500) NON intreccia i due cicli: il fondo si
    disegna tutto, poi l'attore sopra, e poi le tessere che gli stanno davanti
    si ridisegnano sopra di lui, ritagliate al suo rettangolo. Il ritaglio non
    e' un'ottimizzazione: senza, una tessera ridisegnata per intero
    cancellerebbe l'attore disegnato PRIMA che le stava sotto altrove.

    `doors=True` e' DrawOverBrick3 (GRILLE.C:570), quella degli sprite: prova
    gx e gz SEPARATI invece della loro somma. Non si stoccano: `gx-gz` e'
    `px // ISO_X` esatto.

    Il gioco limita anche colonne e righe da percorrere (startcol/endcol,
    `Ys+38 > ClipYmin`): sono filtri di ingombro, e chi li passa senza toccare
    il rettangolo non scrive comunque un pixel."""
    cx0, cy0, cx1, cy1 = rect
    vx, vy = info['vx'], info['vy']
    n = 0
    for cell in info['cell_list']:
        gy, gxz = cell[3], cell[4]
        if gy < ym:
            continue
        if doors:
            d = cell[0] // ISO_X
            gx = (gxz + d) >> 1
            gz = (gxz - d) >> 1
            if not ((gz == zm and gx == xm) or gz > zm or gx > xm):
                continue
        elif gxz <= xm + zm:
            continue
        n += blit_cell(fb, info['bricks_px'], cell, vx, vy, cx0, cy0, cx1, cy1)
    return n


def actor_cells(x, y, z, col):
    """Le coordinate di cella di un attore, OBJECT.C:3019-3022."""
    xm = (x + DEMI_BRICK_XZ) // SIZE_BRICK_XZ
    ym = y // SIZE_BRICK_Y
    zm = (z + DEMI_BRICK_XZ) // SIZE_BRICK_XZ
    if col & 127:
        ym += 1
    return xm, ym, zm


def clip_rect(x0, y0, x1, y1):
    """SetClip (INITSVGA.ASM:266): i minimi non scendono sotto zero, i massimi
    non superano l'ultima riga/colonna dello schermo - che per noi e' il
    BUFFER. Torna None se dopo il taglio e' vuoto, che e' il test
    `ClipXmin <= ClipXmax` di OBJECT.C:3015."""
    x0 = max(0, x0)
    y0 = max(0, y0)
    x1 = min(BG_W - 1, x1)
    y1 = min(BG_H - 1, y1)
    if x0 > x1 or y0 > y1:
        return None
    return (x0, y0, x1, y1)


def render(num, common, cam=None, walk=None, enter=None):
    """Il fondo, con la stessa sequenza di operazioni del Jaguar."""
    gri, gri_off = common['gri']
    bll, bll_off = common['bll']
    brk, brk_off = common['brk']

    cube, gridodd = decode_grid(extract_entry(gri, num, gri_off))
    blocks = parse_bll(extract_entry(bll, num, bll_off))
    if enter is not None:
        # Ingresso da una change-cube: la camera NON e' quella di partenza
        # della stanza di destinazione. ChangeCube (OBJECT.C:522) mette l'eroe
        # in NewPos e solo DOPO ne ricava Start[XYZ]Cube, con le stesse tre
        # regole di sempre. Vedi il commento nel main.
        gx0, gz0 = scene_parse_camera_of(enter[0], enter[2])
        gy0 = scene_parse_camera_y_of(enter[1])
    else:
        gx0, gz0 = cam if cam else scene_camera(num)
        gy0 = scene_camera_y(num)

    # Decodifica dei soli brick referenziati, come fa LoadScene sul Jaguar.
    used = sorted(used_bricks(num, common))
    bricks = {}
    work_bytes = 0
    for bi in used:
        payload = extract_entry(brk, bi, brk_off)
        if payload is None or len(payload) < 4:
            continue
        dx, dy, hx, hy, rows = decode_brick(payload)
        pw, _flags = a2_flags(dx)
        bricks[bi] = (dx, dy, hx, hy, rows)
        work_bytes += (8 + pw * dy + 7) & ~7      # intestazione + pixel, a phrase

    vx = -(gx0 - gz0) * ISO_X + CAM_CX
    vy = -(gx0 + gz0) * ISO_Y + CAM_CY + gy0 * ISO_GY

    # NIENTE FINESTRA DI CELLE ATTORNO ALLA CAMERA.
    #
    # V4 percorreva `camera +- (9,12,12,14)` celle. Misurato: per la scena 0 le
    # celle che producono pixel visibili stanno in `gz -15..+10`, quindi quella
    # finestra ne perdeva tre colonne - 2307 pixel, e non ai bordi ma DENTRO
    # l'immagine, dove un buco si scambia per un brick mancante. Non e' una
    # taratura sbagliata, e' la forma dell'insieme: la regione visibile in
    # (gx,gz) e' un ROMBO (le due diagonali sono gx-gz e gx+gz), e nessuna
    # scatola allineata agli assi lo contiene senza sprecare o tagliare
    # ([[feedback_iso_bounds_pitfall]]). Tarare i quattro numeri li avrebbe
    # fatti tornare per la scena 0 e cadere altrove.
    #
    # Quindi si percorre tutto il cubo e si rifiuta per colonna sulla X, che e'
    # il taglio esatto invece che approssimato. Costo: 4096 iterazioni di
    # rifiuto piu' il ciclo su gy solo nella banda utile - decine di
    # millisecondi UNA VOLTA per stanza, contro una classe di difetto in meno.
    gx_lo, gx_hi, gz_lo, gz_hi = 0, CUBE_X, 0, CUBE_Z
    if walk:
        gx_lo = max(gx0 - walk[0], 0)
        gx_hi = min(gx0 + walk[1], CUBE_X)
        gz_lo = max(gz0 - walk[2], 0)
        gz_hi = min(gz0 + walk[3], CUBE_Z)

    # PASSATA 1: il cubo -> la lista compatta, in ordine di pittore.
    #
    # Stessa forma del Jaguar (build_cells in engine_c/scene_bg.c) e non per
    # eleganza: il riferimento serve a confrontarsi passo per passo, quindi se
    # la' ci sono due passate qui ce ne devono essere due. La lista contiene
    # TUTTO il cubo, senza finestra: e' invariante per la stanza, e il rifiuto
    # di AffBrickBlock si applica al disegno, che dipende dalla camera.
    #
    # px e py sono lo scostamento di schermo senza la camera, com'e' sul Jaguar.
    cell_list = []
    for gz in range(gz_lo, gz_hi):
        for gx in range(gx_lo, gx_hi):
            col = cube[gz][gx]
            px = (gx - gz) * ISO_X
            py_col = (gx + gz) * ISO_Y
            for gy in range(CUBE_Y):
                v = col[gy]
                if not v:
                    continue
                nb = v & 0xFF
                pb = v >> 8
                if nb == 0 or nb - 1 >= len(blocks):
                    continue
                refs = blocks[nb - 1]
                if pb >= len(refs) or refs[pb] == 0:
                    continue
                # gy e gx+gz viaggiano con la cella: sono le due grandezze che
                # DrawOverBrick confronta e da px/py non si ricavano (due
                # equazioni, tre incognite). Sul Jaguar sono i due byte che
                # portano T_CELL a otto (engine_c/scene_bg.c).
                cell_list.append((px, py_col - gy * ISO_GY, refs[pb] - 1,
                                  gy, gx + gz))

    if len(cell_list) > CELL_SLOTS:
        raise SystemExit(
            'scena %d: %d celle risolte, CELL_LIST ne tiene %d.\n'
            'Alzare CELL_SLOTS in tools/memory_map.py (e la regione CELL_LIST).'
            % (num, len(cell_list), CELL_SLOTS))

    # PASSATA 2: la lista -> i pixel.
    fb = bytearray(BG_W * BG_H)
    cells = 0
    for cell in cell_list:
        cells += blit_cell(fb, bricks, cell, vx, vy,
                           0, 0, BG_W - 1, BG_H - 1)
    ress, ress_off = common['ress']
    palette = normalize_palette(extract_entry(ress, 0, ress_off))
    return fb, dict(cam=(gx0, gz0), vx=vx, vy=vy, cells=cells, gridodd=gridodd,
                    celllist=len(cell_list),
                    # M3d: chi disegna gli attori ha bisogno delle celle e dei
                    # pixel dei brick per ridisegnare quelle davanti.
                    cell_list=cell_list, bricks_px=bricks,
                    bricks=len(bricks), work_bytes=work_bytes,
                    clut=clut_sum(palette),
                    walk=(gx_lo, gx_hi, gz_lo, gz_hi))


def fletcher(fb):
    """Le stesse due somme che calcolera' il 68000, sui long BE."""
    s1 = s2 = 0
    for off in range(0, len(fb), 4):
        w = (fb[off] << 24) | (fb[off + 1] << 16) | (fb[off + 2] << 8) | fb[off + 3]
        s1 = (s1 + w) & 0xFFFFFFFF
        s2 = (s2 + s1) & 0xFFFFFFFF
    return s1, s2


def bbox(fb):
    minx, miny, maxx, maxy, n = BG_W, BG_H, -1, -1, 0
    for y in range(BG_H):
        base = y * BG_W
        row = fb[base:base + BG_W]
        if not any(row):
            continue
        if y < miny:
            miny = y
        maxy = y
        for x in range(BG_W):
            if row[x]:
                n += 1
                if x < minx:
                    minx = x
                if x > maxx:
                    maxx = x
    return minx, miny, maxx, maxy, n


def gen_expect(num, fb, info):
    s1, s2 = fletcher(fb)
    minx, miny, maxx, maxy, n = bbox(fb)
    gx, gz = info['cam']
    rows = [
        ('SCENE',    num,                              'scena di riferimento'),
        ('CAM',      ((gx & 0xFFFF) << 16) | (gz & 0xFFFF), 'camera gx | gz'),
        ('VXY',      ((info['vx'] & 0xFFFF) << 16) | (info['vy'] & 0xFFFF),
                     'offset schermo vx | vy'),
        ('GRIDODD',  info['gridodd'],                  'colonne griglia anomale'),
        ('BRICKS',   info['bricks'],                   'brick decodificati'),
        ('WORKLEN',  info['work_bytes'],               'byte del buffer di lavoro'),
        ('CELLLIST', info['celllist'],                 'celle risolte del cubo'),
        ('CELLS',    info['cells'],                    'celle passate al Blitter'),
        ('BBOX_MIN', (minx << 16) | miny,              'bbox minx | miny'),
        ('BBOX_MAX', (maxx << 16) | maxy,              'bbox maxx | maxy'),
        ('NONZERO',  n,                                'pixel non trasparenti'),
        ('SUM1',     s1,                               'Fletcher s1 sul fondo'),
        ('SUM2',     s2,                               'Fletcher s2 sul fondo'),
        ('CLUT',     info['clut'],                     'somma della CLUT caricata'),
    ]
    return rows


def gen_expect_file(steps, enters):
    """L'header delle attese, per una SEQUENZA di passi.

    Un passo e' "quale fondo il Jaguar deve avere costruito a questo punto".
    Il passo 0 e' la scena iniziale; i successivi sono le stanze in cui si
    entra. Serve un array e non piu' una manciata di define perche' il criterio
    di M2 non e' "si vede la scena 0", e' anche "la transizione porta nella
    stanza giusta" - e una stanza giusta la si riconosce solo confrontandola con
    quella giusta calcolata a parte.
    """
    names = [n for n, _v, _l in steps[0][1]]
    out = ['/* GENERATO DA tools/bg_ref.py - NON MODIFICARE A MANO.\n',
           ' *\n',
           ' * Attese del fondo di M2. Le calcola il renderizzatore di\n',
           ' * riferimento in Python, che percorre gli stessi byte della\n',
           ' * cartuccia con le stesse regole; il Jaguar rilegge il proprio\n',
           ' * framebuffer e confronta. "Si vede la scena" non e\' un criterio:\n',
           ' * si vede una scena anche con la camera sbagliata di una cella.\n',
           ' *\n',
           ' * Se la maschera cade, le sonde dicono di che morte si e\' morti:\n',
           ' * bbox diverso = immagine traslata; NONZERO diverso a bbox uguale =\n',
           ' * brick mancanti; CELLS/BRICKS diversi = percorso griglia o BLL.\n',
           ' * CELLLIST diverso da CELLS separa "il cubo l\'ho letto male" da\n',
           ' * "l\'ho letto bene e ho disegnato le celle sbagliate".\n',
           ' */\n',
           '#ifndef JAG_BG_EXPECT_H\n#define JAG_BG_EXPECT_H\n\n']
    out.append('/* Le sonde confrontate, nell\'ordine dei bit della maschera.\n'
               ' * SCENE non e\' fra queste: e\' un ingresso, non un risultato. */\n')
    out.append('#define BG_EXPECT_COUNT %d\n' % (len(names) - 1))
    out.append('#define BG_EXPECT_ALL   0x%04XUL\n' % ((1 << (len(names) - 1)) - 1))
    out.append('#define BG_EXPECT_STEPS %d\n\n' % len(steps))

    for i, (desc, _rows) in enumerate(steps):
        out.append('/* passo %d: %s */\n' % (i, desc))
    out.append('\nstatic const unsigned long BgExpect[BG_EXPECT_STEPS]'
               '[BG_EXPECT_COUNT + 1] = {\n')
    for desc, rows in steps:
        out.append('    {   /* %s */\n' % desc)
        for name, value, label in rows:
            out.append('        0x%08XUL,   /* %-9s %-28s %d */\n'
                       % (value & 0xFFFFFFFF, name, label, value))
        out.append('    },\n')
    out.append('};\n')
    out.append('\n/* Come si arriva a ogni passo: 1 = si entra da una\n'
               ' * change-cube, cioe\' la posizione la da\' la zona; 0 = la\n'
               ' * stanza si carica da capo dal suo punto di partenza. Il\n'
               ' * Jaguar non lo puo\' dedurre dal numero di scena, e\n'
               ' * indovinarlo vorrebbe dire inquadrare la stanza giusta dal\n'
               ' * posto sbagliato. */\n')
    out.append('static const unsigned char BgEnter[BG_EXPECT_STEPS] = { %s };\n'
               % ', '.join('1' if e else '0' for e in enters))
    out.append('\n/* Indice del passo dentro una riga di BgExpect. */\n')
    for j, name in enumerate(names):
        out.append('#define BGX_%-9s %d\n' % (name, j))
    out.append('\n/* Indici CLUT riservati alla diagnostica: due degli slot\n'
               ' * "riservati UI" di LBA1, che nessun brick usa. */\n')
    out.append('#define BG_DBG_IDX_FG  %d\n' % DBG_IDX_FG)
    out.append('#define BG_DBG_IDX_LBL %d\n' % DBG_IDX_LBL)
    out.append('\n#endif\n')
    return ''.join(out)


def write_preview(path, fb, palette):
    rgb = bytearray(BG_W * BG_H * 3)
    for i, idx in enumerate(fb):
        r, g, b = palette[idx]
        rgb[i * 3] = r
        rgb[i * 3 + 1] = g
        rgb[i * 3 + 2] = b
    write_png(path, BG_W, BG_H, bytes(rgb))


def check_lut(_common):
    """Confronta le larghezze calcolate con le due tabelle scritte a mano di V4."""
    v4_pad = ([2, 2, 2, 4, 4, 6, 6, 8, 8, 10, 10, 12, 12, 14, 14, 16, 16]
              + [20] * 4 + [24] * 4 + [28] * 4 + [32] * 4
              + [40] * 8 + [48] * 8 + [56] * 8 + [64] * 8)
    bad = 0
    for dx in range(1, len(v4_pad)):
        w, flags = a2_flags(dx)
        if w != v4_pad[dx]:
            print('  DX=%2d: calcolato %d, V4 %d' % (dx, w, v4_pad[dx]))
            bad += 1
    print('check-lut: %d differenze su %d larghezze' % (bad, len(v4_pad) - 1))
    print('  esempio DX=24 -> larghezza %d, A2_FLAGS $%08X' % a2_flags(24))


def check_clut(common, scenes):
    """I due indici della diagnostica sono davvero inutilizzati?"""
    brk, brk_off = common['brk']
    hit = {}
    for num in scenes:
        for bi in sorted(used_bricks(num, common)):
            payload = extract_entry(brk, bi, brk_off)
            if payload is None or len(payload) < 4:
                continue
            _dx, _dy, _hx, _hy, rows = decode_brick(payload)
            for row in rows:
                for p in row:
                    if p in (DBG_IDX_FG, DBG_IDX_LBL):
                        hit[p] = hit.get(p, 0) + 1
    print('check-clut: scene %s' % (list(scenes),))
    for idx in (DBG_IDX_FG, DBG_IDX_LBL):
        print('  indice %d: %d pixel' % (idx, hit.get(idx, 0)))
    if hit:
        print('  ATTENZIONE: la diagnostica ruberebbe un colore alla scena.')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--scene', type=int, default=0)
    ap.add_argument('--gog', default=GOG_COMMON)
    ap.add_argument('--cam', default=None,
                    help='gx,gz per sovrascrivere la camera (confronti)')
    ap.add_argument('--png', default=None)
    ap.add_argument('--no-expect', action='store_true')
    ap.add_argument('--no-transition', action='store_true',
                    help='solo la scena iniziale, senza il passo di ingresso')
    ap.add_argument('--check-lut', action='store_true')
    ap.add_argument('--check-clut', type=int, nargs='*', default=None)
    args = ap.parse_args()

    common = load_common(args.gog)

    if args.check_lut:
        check_lut(common)
        return
    if args.check_clut is not None:
        check_clut(common, args.check_clut or [args.scene])
        return

    cam = None
    if args.cam:
        cam = tuple(int(v) for v in args.cam.split(','))

    ress, ress_off = common['ress']
    palette = normalize_palette(extract_entry(ress, 0, ress_off))

    # I PASSI. Il primo e' la scena chiesta; il secondo, se esiste, e' la
    # stanza in cui porta la sua PRIMA zona di tipo 0.
    #
    # La posizione d'ingresso non e' quella di partenza della stanza di
    # destinazione, ed e' il punto piu' facile da sbagliare di tutta la
    # transizione. ChangeCube (OBJECT.C:522) fa:
    #
    #     NewPos = posEroe - zona.X0 + zona.Info0        (per asse)
    #     ListObjet[0].PosObj = NewPos
    #     ...e SOLO DOPO Start[XYZ]Cube da quella posizione
    #
    # cioe' conserva lo scostamento dell'eroe DENTRO la zona. Senza un eroe
    # (arriva a M3) si entra dall'angolo della zona, posEroe = zona.X0, che da'
    # esattamente Info0/1/2: e' il caso ben definito, e non e' un valore
    # inventato - e' il dato della zona letto per quello che e'.
    # I PASSI LI DECIDE UN SOLO POSTO: scene_parse.walk_steps(). Li consuma
    # anche il generatore delle attese degli attori, e due liste che
    # divergessero darebbero due maschere piene di due stanze diverse - cioe'
    # proprio il guasto che l'array di passi esiste per rendere visibile.
    if args.cam or args.no_transition:
        steps = [(args.scene, None)]
    else:
        import scene_parse
        steps = scene_parse.walk_steps(args.scene)

    expect = []
    for num, enter in steps:
        fb, info = render(num, common, cam if enter is None else None, enter=enter)
        how = 'iniziale' if enter is None else ('ingresso da change-cube %s'
                                                % (enter,))
        print('scena %d (%s): camera %s, vx=%d vy=%d'
              % (num, how, info['cam'], info['vx'], info['vy']))
        print('  brick %d, lavoro %d byte, celle risolte %d, disegnate %d'
              % (info['bricks'], info['work_bytes'], info['celllist'],
                 info['cells']))

        png = args.png or os.path.join(
            REPO, 'build', 'bg_ref_scene%03d%s.png'
            % (num, ('_cam%d_%d' % cam) if (cam and enter is None) else ''))
        d = os.path.dirname(png)
        if d and not os.path.isdir(d):
            os.makedirs(d)
        write_preview(png, fb, palette)
        print('  scritto %s' % os.path.relpath(png, REPO))

        rows = gen_expect(num, fb, info)
        for name, value, label in rows:
            print('    %-9s %08X  %s' % (name, value & 0xFFFFFFFF, label))
        expect.append(('scena %d, %s' % (num, how), rows))

    if args.no_expect or cam:
        print('  (attese NON scritte: camera sovrascritta o --no-expect)')
        return
    hdr = os.path.join(REPO, 'engine_c', 'bg_expect.h')
    with open(hdr, 'w', newline='\n') as f:
        f.write(gen_expect_file(expect, [e is not None for _n, e in steps]))
    print('  scritto engine_c/bg_expect.h (%d passi)' % len(expect))


if __name__ == '__main__':
    main()
