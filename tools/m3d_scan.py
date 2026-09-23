"""m3d_scan.py - QUANTO COSTA TROVARE LE TESSERE DAVANTI, prima di toccare il C.

PERCHE'
-------
`docs/todo_s50.md` dice che M3d e' il 64% del quadro con `overbrick = 2`, e ne
deduce che il costo e' TROVARE le tessere, non disegnarle. E' una deduzione dal
codice, non una misura: questo script la mette alla prova sui dati veri, su PC,
senza una riga di sonda sul Jaguar.

Non modella "un attore plausibile": aggancia `bg_ref.draw_over_brick` mentre
`body_ref.render_step` disegna le scene di riferimento, quindi (xm,ym,zm) e il
rettangolo sono quelli che il Jaguar riceve davvero - stessa proiezione, stessa
posa, stessi attori, sprite e porte compresi.

Poi rigioca ogni chiamata registrata attraverso il MODELLO DEL C
(engine_c/scene_bg.c: build_blocks + block_hits + over_bricks + blit_cell), che
il riferimento Python non ha, e conta dove finisce il lavoro:

    blocchi provati -> blocchi passati -> celle viste -> celle a blit_cell
        -> respinte dal ritaglio DOS -> respinte dal rettangolo -> DISEGNATE

USO
    python tools/m3d_scan.py
    python tools/m3d_scan.py --gog "I:\\GOG\\Little Big Adventure\\Common"
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bg_ref
import body_ref
import scene_parse


CELL_BLOCK = 64          # engine_c/scene_bg.c


# ---------------------------------------------------------------------------
# IL MODELLO DEL C. Non e' il riferimento Python: e' l'ACCELERATORE che sta solo
# nel C (l'indice a blocchi di S41), trascritto riga per riga per poterlo
# contare. Se un giorno diverge dal C, e' questo file a essere sbagliato.
# ---------------------------------------------------------------------------
def build_blocks(cell_list):
    """S41: i limiti in coordinate di CELLA di ogni blocco di 64 voci
    consecutive. Consecutive e non riordinate: l'ordine e' quello del pittore e
    saltare blocchi interi lo conserva per costruzione."""
    blocks = []
    for i in range(0, len(cell_list), CELL_BLOCK):
        chunk = cell_list[i:i + CELL_BLOCK]
        xs = [c[0] for c in chunk]
        ys = [c[1] for c in chunk]
        blocks.append((min(xs), min(ys), max(xs), max(ys)))
    return blocks


def spans(bricks_px):
    """BgStat.spanx/spany: l'ingombro massimo di una tessera oltre la sua
    cella (decode_bricks, scene_bg.c:543). hx/hy sono l'hotspot."""
    sx = sy = 0
    for (dx, dy, hx, hy, _rows) in bricks_px.values():
        sx = max(sx, hx + dx)
        sy = max(sy, hy + dy)
    return sx, sy


def block_hits(q, vx, vy, x0, y0, x1, y1, spanx, spany):
    if q[2] + vx + spanx <= x0 or q[0] + vx > x1:
        return False
    if q[3] + vy + spany <= y0 or q[1] + vy > y1:
        return False
    return True


def blit_verdict(cell, bricks, vx, vy, cx0, cy0, cx1, cy1):
    """blit_cell senza i pixel: torna 'dos', 'rect' o 'blit'. I rifiuti sono
    quelli del C nello stesso ordine, perche' il loro COSTO e' diverso e la
    domanda e' proprio dove si spende."""
    px, py, ref = cell[0], cell[1], cell[2]
    sx_col = px + vx
    if sx_col < bg_ref.DOS_CLIP_X0 or sx_col >= bg_ref.DOS_CLIP_X1:
        return 'dos'
    sy_cell = py + vy
    if sy_cell < bg_ref.DOS_CLIP_Y0 or sy_cell >= bg_ref.DOS_CLIP_Y1:
        return 'dos'
    b = bricks.get(ref)
    if b is None:
        return 'dos'
    dx, dy, hx, hy, _rows = b
    sx = sx_col + hx
    sy = sy_cell + hy
    if sx + dx <= cx0 or sx > cx1:
        return 'rect'
    if sy + dy <= cy0 or sy > cy1:
        return 'rect'
    return 'blit'


def scan_full(cell_list, bricks, vx, vy, xm, ym, zm, rect, doors):
    """LA SEMANTICA, senza nessun indice: la lista percorsa tutta, che e'
    esattamente quel che fa bg_ref.draw_over_brick. Serve come terzo termine:
    l'indice a blocchi e' gia' sul ferro, quindi confrontare il nuovo SOLO con
    lui direbbe 'sono d'accordo fra loro', non 'sono giusti'
    ([[feedback_buco_simmetrico_oracolo]])."""
    cx0, cy0, cx1, cy1 = rect
    xz = xm + zm
    drawn = []
    for k, cell in enumerate(cell_list):
        if cell[3] < ym:
            continue
        if doors:
            d = cell[0] // bg_ref.ISO_X
            gx = (cell[4] + d) >> 1
            gz = (cell[4] - d) >> 1
            if not ((gz == zm and gx == xm) or gz > zm or gx > xm):
                continue
        elif cell[4] <= xz:
            continue
        if blit_verdict(cell, bricks, vx, vy, cx0, cy0, cx1, cy1) == 'blit':
            drawn.append(k)
    return drawn


def scan(cell_list, blocks, bricks, vx, vy, spanx, spany,
         xm, ym, zm, rect, doors):
    """over_bricks (o Bg_DrawOverBrick3), contato. Torna il conteggio e la
    LISTA DEGLI INDICI che hanno disegnato, in ordine: e' quella che qualunque
    ottimizzazione futura dovra' riprodurre identica."""
    cx0, cy0, cx1, cy1 = rect
    xz = xm + zm
    c = dict(blk=0, blk_hit=0, cell=0, depth=0, call=0, dos=0, rect=0, blit=0)
    drawn = []

    for bi, q in enumerate(blocks):
        c['blk'] += 1
        if not block_hits(q, vx, vy, cx0, cy0, cx1, cy1, spanx, spany):
            continue
        c['blk_hit'] += 1
        k0 = bi * CELL_BLOCK
        k1 = min(k0 + CELL_BLOCK, len(cell_list))
        for k in range(k0, k1):
            cell = cell_list[k]
            c['cell'] += 1
            if cell[3] < ym:
                continue
            if doors:
                d = cell[0] // bg_ref.ISO_X
                gx = (cell[4] + d) >> 1
                gz = (cell[4] - d) >> 1
                if not ((gz == zm and gx == xm) or gz > zm or gx > xm):
                    continue
            elif cell[4] <= xz:
                continue
            c['depth'] += 1
            c['call'] += 1
            v = blit_verdict(cell, bricks, vx, vy, cx0, cy0, cx1, cy1)
            c[v] += 1
            if v == 'blit':
                drawn.append(k)
    return c, drawn


# ---------------------------------------------------------------------------
# LA STRADA NUOVA: L'INDICE PER COLONNA.
#
# Perche' l'indice a blocchi rende poco lo dicono i suoi stessi numeri: 64 celle
# consecutive coprono ~30 colonne di griglia, cioe' 720 pixel di schermo, quindi
# il riquadro di un blocco interseca quasi sempre il rettangolo di un attore.
#
# Ma la lista NON e' disordinata: build_cells la scrive con gz esterno, gx, e gy
# interno, quindi le celle di una COLONNA (gz,gx) sono contigue. E una colonna ha
# una x di schermo SOLA - px = (gx-gz)*ISO_X - perche' px non dipende da gy.
#
# Quindi si puo' indicizzare per colonna: 4096 offset di partenza, monotoni, e
# per un rettangolo si ricavano le due bande
#
#     d1 = gx-gz   dalla X       (stretta: ~6 valori per un attore)
#     d2 = gx+gz   dalla Y       (larga: una colonna e' alta 25*ISO_GY)
#
# e si percorrono SOLO le colonne dentro le due bande. L'ordine di pittore si
# conserva perche' si itera gz crescente e dentro gx crescente, che e' l'ordine
# in cui build_cells le ha scritte: non e' un riordino, e' un salto.
# ---------------------------------------------------------------------------
def build_columns(cell_list):
    """colstart[gz*CUBE_X + gx] = primo indice della colonna; l'ultimo elemento
    chiude l'ultima colonna. Monotono per costruzione."""
    ncol = bg_ref.CUBE_X * bg_ref.CUBE_Z
    colstart = [0] * (ncol + 1)
    k = 0
    for cidx in range(ncol):
        colstart[cidx] = k
        gz, gx = divmod(cidx, bg_ref.CUBE_X)
        while k < len(cell_list):
            px, py, _ref, gy, gxz = cell_list[k]
            if gxz != gx + gz or px != (gx - gz) * bg_ref.ISO_X:
                break
            k += 1
    colstart[ncol] = k
    if k != len(cell_list):
        raise SystemExit('la lista non e\' in ordine (gz,gx,gy): %d/%d'
                         % (k, len(cell_list)))
    return colstart


def col_intervals(colstart, vx, vy, rect, ym, spanx, spany,
                  include_empty=False):
    """`col_open` + `col_next` di engine_c/scene_bg.c, riga per riga.

    Le due bande sono in d1 = gx-gz e d2 = gx+gz, estremi INCLUSIVI:

        cx0 - spanx <  px + vx  <=  cx1        (px = d1*ISO_X)
        cy0 - spany <  py + vy  <=  cy1        (py = d2*ISO_Y - gy*ISO_GY)

    con gy in [ym, CUBE_Y-1], perche' sotto ym la cella e' gia' respinta dal
    filtro di profondita'. L'estremo destro non toglie l'hotspot: BH_HOTX e
    BH_HOTY si leggono come UBYTE, quindi non sono mai negativi e zero e' gia'
    il valore conservativo.

    Torna gli intervalli [k0,k1) delle colonne NON VUOTE, in ordine di pittore.
    Genera (gz crescente, gx crescente) che e' l'ordine in cui build_cells ha
    scritto la lista: non si riordina niente, si salta."""
    ix, iy, igy = bg_ref.ISO_X, bg_ref.ISO_Y, bg_ref.ISO_GY
    nx, nz, ny = bg_ref.CUBE_X, bg_ref.CUBE_Z, bg_ref.CUBE_Y
    cx0, cy0, cx1, cy1 = rect

    d1lo = -((-(cx0 - spanx + 1 - vx)) // ix)      # ceil
    d1hi = (cx1 - vx) // ix                        # floor
    d2lo = -((-(cy0 - spany + 1 - vy + ym * igy)) // iy)
    d2hi = (cy1 - vy + (ny - 1) * igy) // iy

    # Lo stesso taglio agli estremi della griglia che fa il C prima di scendere
    # a 16 bit: senza, una camera lontana darebbe numeri che in WORD non stanno.
    d1lo = max(d1lo, -(nx - 1))
    d1hi = min(d1hi, nx - 1)
    d2lo = max(d2lo, 0)
    d2hi = min(d2hi, 2 * (nx - 1))
    if d1lo > d1hi or d2lo > d2hi:
        return

    gz_lo = max(0, -((-(d2lo - d1hi)) // 2))
    gz_hi = min(nz - 1, (d2hi - d1lo) // 2)

    for gz in range(gz_lo, gz_hi + 1):
        gx_lo = max(0, gz + d1lo, d2lo - gz)
        gx_hi = min(nx - 1, gz + d1hi, d2hi - gz)
        for gx in range(gx_lo, gx_hi + 1):
            cidx = gz * nx + gx
            k0, k1 = colstart[cidx], colstart[cidx + 1]
            if k0 != k1 or include_empty:
                yield k0, k1


def scan_col(cell_list, colstart, bricks, vx, vy, spanx, spany,
             xm, ym, zm, rect, doors):
    """over_bricks con l'indice per colonna. Stessi test per cella, stesso
    ordine: cambia solo QUALI colonne si guardano."""
    cx0, cy0, cx1, cy1 = rect
    xz = xm + zm
    c = dict(col=0, col_hit=0, cell=0, depth=0, call=0, dos=0, rect=0, blit=0)
    drawn = []

    for k0, k1 in col_intervals(colstart, vx, vy, rect, ym, spanx, spany):
        c['col'] += 1
        c['col_hit'] += 1
        for k in range(k0, k1):
            cell = cell_list[k]
            c['cell'] += 1
            if cell[3] < ym:
                continue
            if doors:
                d = cell[0] // bg_ref.ISO_X
                ggx = (cell[4] + d) >> 1
                ggz = (cell[4] - d) >> 1
                if not ((ggz == zm and ggx == xm) or ggz > zm or ggx > xm):
                    continue
            elif cell[4] <= xz:
                continue
            c['depth'] += 1
            c['call'] += 1
            v = blit_verdict(cell, bricks, vx, vy, cx0, cy0, cx1, cy1)
            c[v] += 1
            if v == 'blit':
                drawn.append(k)
    return c, drawn


# ---------------------------------------------------------------------------
# LA REGISTRAZIONE. body_ref chiama bg_ref.draw_over_brick per attore; qui la si
# intercetta e si lascia proseguire, cosi' l'immagine di riferimento resta
# quella di sempre e i numeri arrivano gratis.
# ---------------------------------------------------------------------------
def record_calls(buf, common, steps):
    calls = []
    orig = bg_ref.draw_over_brick

    def spy(fb, info, xm, ym, zm, rect, doors=False):
        calls.append(dict(scene=spy.scene, xm=xm, ym=ym, zm=zm,
                          rect=tuple(rect), doors=doors, info=info))
        return orig(fb, info, xm, ym, zm, rect, doors)

    bg_ref.draw_over_brick = spy
    try:
        for step_i, (num, enter) in steps:
            spy.scene = num
            body_ref.render_step(buf, common, num, enter, step_i)
    finally:
        bg_ref.draw_over_brick = orig
    return calls


def sweep(per_scene, calls, step, verbose):
    """LA PROVA VERA. Undici chiamate sono un campione, non una verifica: qui il
    rettangolo si passeggia su TUTTO lo schermo, con tre taglie e tutta la gamma
    utile di ym e xm+zm, e per ognuno si pretende che le tre scansioni diano la
    stessa identica lista.

    Le taglie coprono i casi che contano: l'eroe (~96x88), la finestra dello
    sprite (128x96) e una tessera sola (24x38), che e' il caso in cui una banda
    sbagliata di un pixel si vede subito."""
    sizes = [(96, 88), (128, 96), (24, 38)]
    bad = tot = 0
    seen_scene = {}
    for call in calls:
        seen_scene[call['scene']] = call['info']

    for key in sorted(seen_scene):
        info = seen_scene[key]
        blocks, colstart, sx, sy = per_scene[key]
        cl, bp = info['cell_list'], info['bricks_px']
        vx, vy = info['vx'], info['vy']
        n = 0
        for (w, h) in sizes:
            for y0 in range(-h, bg_ref.BG_H, step):
                for x0 in range(-w, bg_ref.BG_W, step):
                    r = bg_ref.clip_rect(x0, y0, x0 + w - 1, y0 + h - 1)
                    if r is None:
                        continue
                    for (ym, xm, zm, doors) in ((0, 20, 20, False),
                                                (5, 32, 32, False),
                                                (12, 45, 45, False),
                                                (5, 32, 32, True),
                                                (12, 20, 45, True)):
                        a = scan_full(cl, bp, vx, vy, xm, ym, zm, r, doors)
                        b = scan(cl, blocks, bp, vx, vy, sx, sy,
                                 xm, ym, zm, r, doors)[1]
                        c = scan_col(cl, colstart, bp, vx, vy, sx, sy,
                                     xm, ym, zm, r, doors)[1]
                        tot += 1
                        n += 1
                        if a != b or a != c:
                            bad += 1
                            if verbose and bad <= 10:
                                print('  DIVERGE scena %d rect %r ym %d '
                                      'xz %d porte %d: pieno %d, blocchi %d, '
                                      'colonne %d'
                                      % (key, r, ym, xm + zm, doors,
                                         len(a), len(b), len(c)))
        print('  scena %d: %d rettangoli provati' % (key, n))
    print('sweep: %d confronti, %d divergenze' % (tot, bad))
    return bad == 0


# ---------------------------------------------------------------------------
# L'ALTRO CONSUMATORE DELLO STESSO INDICE: IL RIPRISTINO (render_rects).
#
# Stessa forma - blocchi fuori, celle dentro - ma due differenze che contano:
# niente filtro di profondita' (quindi la banda in Y e' piu' larga, ymin = 0) e
# il rettangolo passato all'indice e' il RIQUADRO CHE RACCHIUDE la lista, non un
# rettangolo di attore.
#
# Ed e' proprio li' che l'indice per colonna puo' PERDERE: con un riquadro
# grande - la banda che scopre uno scorrimento di camera puo' valere il 70-80%
# dello schermo - quasi tutte le colonne cadono dentro la banda, e allora si
# pagano migliaia di passi di colonna per non saltare nemmeno una cella, mentre i
# blocchi restano 138. Va misurato, non deciso a tavolino.
# ---------------------------------------------------------------------------
def rects_blocks(cell_list, blocks, bricks, vx, vy, spanx, spany, boxes, bx):
    bx0, by0, bx1, by1 = bx
    c = dict(step=0, cell=0, rough=0, call=0, dos=0, rect=0, blit=0)
    drawn = []
    for bi, q in enumerate(blocks):
        c['step'] += 1
        if not block_hits(q, vx, vy, bx0, by0, bx1, by1, spanx, spany):
            continue
        k0 = bi * CELL_BLOCK
        k1 = min(k0 + CELL_BLOCK, len(cell_list))
        for k in range(k0, k1):
            c['cell'] += 1
            cell = cell_list[k]
            sx, sy = cell[0] + vx, cell[1] + vy
            if sx + spanx <= bx0 or sx > bx1:
                continue
            if sy + spany <= by0 or sy > by1:
                continue
            c['rough'] += 1
            for r in boxes:
                c['call'] += 1
                v = blit_verdict(cell, bricks, vx, vy, *r)
                c[v] += 1
                if v == 'blit':
                    drawn.append((k, r))
    return c, drawn


def rects_cols(cell_list, colstart, bricks, vx, vy, spanx, spany, boxes, bx):
    c = dict(step=0, cell=0, rough=0, call=0, dos=0, rect=0, blit=0)
    drawn = []
    bx0, by0, bx1, by1 = bx
    for k0, k1 in col_intervals(colstart, vx, vy, bx, 0, spanx, spany):
        c['step'] += 1
        for k in range(k0, k1):
            c['cell'] += 1
            cell = cell_list[k]
            sx, sy = cell[0] + vx, cell[1] + vy
            if sx + spanx <= bx0 or sx > bx1:
                continue
            if sy + spany <= by0 or sy > by1:
                continue
            c['rough'] += 1
            for r in boxes:
                c['call'] += 1
                v = blit_verdict(cell, bricks, vx, vy, *r)
                c[v] += 1
                if v == 'blit':
                    drawn.append((k, r))
    return c, drawn


def cols_walked(colstart, vx, vy, bx, spanx, spany):
    """I passi di colonna VERI, comprese quelle vuote: col_next le salta ma le
    guarda, e il costo di guardarle e' quel che puo' far perdere l'indice su un
    riquadro grande. col_intervals torna solo le piene, quindi il conto va
    rifatto qui."""
    n = 0
    for _k0, _k1 in col_intervals(colstart, vx, vy, bx, 0, spanx, spany,
                                  include_empty=True):
        n += 1
    return n


def rects_report(per_scene, calls):
    """Il ripristino sui casi veri (i rettangoli degli attori del quadro) e su
    quelli grandi (le bande di uno scorrimento di camera)."""
    seen = {}
    for call in calls:
        seen.setdefault(call['scene'], []).append(call)

    rows = []
    print('%-28s %8s %8s %6s   %8s %8s %6s'
          % ('caso', 'passi', 'celle', 'blit_c', 'passi', 'celle', 'blit_c'))
    print('%-28s %-24s   %-24s' % ('', '--- BLOCCHI', '--- COLONNE'))
    print('-' * 78)

    for key in sorted(seen):
        info = seen[key][0]['info']
        blocks, colstart, sx, sy = per_scene[key]
        cl, bp = info['cell_list'], info['bricks_px']
        vx, vy = info['vx'], info['vy']

        cases = []
        boxes = [c['rect'] for c in seen[key]]
        bx = (min(b[0] for b in boxes), min(b[1] for b in boxes),
              max(b[2] for b in boxes), max(b[3] for b in boxes))
        cases.append(('attori del quadro (%d)' % len(boxes), boxes, bx))
        for hh in (40, 120, 240):
            b = (0, 0, bg_ref.BG_W - 1, hh - 1)
            cases.append(('banda di scorrimento h=%d' % hh, [b], b))
        b = (0, 0, bg_ref.BG_W - 1, bg_ref.BG_H - 1)
        cases.append(('schermo intero', [b], b))

        for (name, bl, bb) in cases:
            c1, d1 = rects_blocks(cl, blocks, bp, vx, vy, sx, sy, bl, bb)
            c2, d2 = rects_cols(cl, colstart, bp, vx, vy, sx, sy, bl, bb)
            if d1 != d2:
                raise SystemExit('ripristino: le due strade divergono su %s'
                                 % name)
            c2['step'] = cols_walked(colstart, vx, vy, bb, sx, sy)
            print('s%d %-25s %8d %8d %6d   %8d %8d %6d'
                  % (key, name, c1['step'], c1['cell'], c1['call'],
                     c2['step'], c2['cell'], c2['call']))
            rows.append((key, name, len(bl), c2))
    print()
    print('"passi" = block_hits provati / colonne guardate (anche le vuote)')
    return rows


# ---------------------------------------------------------------------------
# IL PROLOGO RIFATTO PER OGNI RETTANGOLO.
#
# `render_rects` tiene il ciclo dei rettangoli DENTRO quello delle celle, e
# deve: e' l'ordine di pittore per ciascun rettangolo, e invertirli darebbe lo
# stesso numero di blit nell'ordine sbagliato.
#
# Ma `blit_cell` prende una CELLA e un rettangolo, e la prima meta' del suo
# corpo dipende solo dalla cella:
#
#     sx_col = px + vx        e il ritaglio DOS sulla X      (2 confronti)
#     sy_cell = py + vy       e il ritaglio DOS sulla Y      (2 confronti)
#     b = handles[c->ref]     piu' il controllo di nullo     (1 lettura lunga)
#     dx, dy, hotx, hoty                                     (4 letture byte)
#
# Con `count` rettangoli quel prologo si rifa' `count` volte per la stessa
# cella, cinque accessi in memoria compresi. Solo la seconda meta' - i quattro
# confronti di sovrapposizione e il ritaglio - dipende davvero dal rettangolo.
#
# Qui si conta quante volte si rifa'. Non e' un cambio di algoritmo ne' di
# ordine: e' lo stesso lavoro, calcolato una volta invece di `count`.
# ---------------------------------------------------------------------------
def prologue_report(rows):
    print()
    print('%-28s %5s %8s %8s %9s %8s'
          % ('caso', 'rett', 'prologhi', 'se estr.', 'risparmio', 'blit'))
    print('-' * 72)
    tp = te = 0
    for (key, name, count, c) in rows:
        pro, est = c['call'], c['rough']
        tp += pro
        te += est
        print('s%d %-25s %5d %8d %8d %8d%% %8d'
              % (key, name, count, pro, est,
                 100 * (pro - est) // max(1, pro), c['blit']))
    print('-' * 72)
    print('%-34s %8d %8d %8d%%' % ('TOTALE', tp, te,
                                   100 * (tp - te) // max(1, tp)))
    print()
    print('"prologhi" = quante volte si ricalcola la meta\' della cella')
    print('"se estratto" = quante volte basterebbe, cioe\' una per cella')


# ---------------------------------------------------------------------------
# IL BANCO DEL C. Fin qui si e' provato l'ALGORITMO; quel che resta scoperto e'
# la TRASCRIZIONE, cioe' il verso degli arrotondamenti (il C tronca verso lo
# zero, qui si arrotonda verso il basso, e i numeratori sono negativi quasi
# sempre) e il taglio a 16 bit delle bande.
#
# Quindi si scrivono le celle e l'indice su file, e tools/host_colband.c - che
# compila il VERO engine_c/scene_bg.c - rifa' lo stesso sweep e stampa la stessa
# somma. Due numeri da confrontare, e nessun gemello di mezzo.
# ---------------------------------------------------------------------------
SWEEP_SIZES = ((96, 88), (128, 96), (24, 38))
SWEEP_YMS = (0, 5, 12)
M32 = 0xFFFFFFFF


def band_signature(colstart, vx, vy, spanx, spany, step):
    """La stessa passeggiata di host_colband.c, con la stessa somma.

    Non e' un conteggio: due bande diverse che aprono lo stesso NUMERO di
    colonne darebbero lo stesso totale e somme diverse, che e' proprio quel che
    serve quando l'errore atteso e' una colonna in piu' o in meno."""
    s1 = s2 = 0
    cols = cells = rects = 0
    for (w, h) in SWEEP_SIZES:
        for y0 in range(-h, bg_ref.BG_H, step):
            for x0 in range(-w, bg_ref.BG_W, step):
                cx0 = max(0, x0)
                cy0 = max(0, y0)
                cx1 = min(x0 + w - 1, bg_ref.BG_W - 1)
                cy1 = min(y0 + h - 1, bg_ref.BG_H - 1)
                if cx0 > cx1 or cy0 > cy1:
                    continue
                for ym in SWEEP_YMS:
                    rects += 1
                    s1 = ((s1 + 0x9E3779B9) & M32) ^ ((cx0 * 8191 + cy0) & M32)
                    for k0, k1 in col_intervals(colstart, vx, vy,
                                                (cx0, cy0, cx1, cy1), ym,
                                                spanx, spany):
                        cols += 1
                        cells += k1 - k0
                        s1 = (s1 + k0 * 65537 + k1) & M32
                        s2 = (s2 + s1) & M32
    return rects, cols, cells, s1, s2


def dump(per_scene, calls, outdir, step):
    import struct

    seen = {}
    for call in calls:
        seen[call['scene']] = call['info']
    if not os.path.isdir(outdir):
        os.makedirs(outdir)

    for key in sorted(seen):
        info = seen[key]
        _blocks, colstart, sx, sy = per_scene[key]
        cl = info['cell_list']
        vx, vy = info['vx'], info['vy']
        path = os.path.join(outdir, 'scene%d.bin' % key)
        with open(path, 'wb') as f:
            f.write(struct.pack('<5i', vx, vy, sx, sy, len(cl)))
            for (px, py, ref, gy, gxz) in cl:
                f.write(struct.pack('<hhHBB', px, py, ref, gy, gxz))
            f.write(struct.pack('<%dH' % len(colstart), *colstart))
        r, c, ce, s1, s2 = band_signature(colstart, vx, vy, sx, sy, step)
        print('scena %d: %d celle, %d rettangoli, %d colonne aperte, '
              '%d celle percorse, somma %08X %08X'
              % (key, len(cl), r, c, ce, s1, s2))
    print('scritto in %s; ora:' % outdir)
    print('  docker run --rm -v "%%CD%%:/work" -w /work marsdev-32x:latest '
          '"gcc -O2 -o /tmp/hcb tools/host_colband.c && /tmp/hcb %s %d"'
          % (outdir, step))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--gog', default=bg_ref.GOG_COMMON)
    ap.add_argument('--dump', metavar='DIR', default=None,
                    help='scrive celle e indice per tools/host_colband.c, e '
                         'stampa la somma attesa')
    ap.add_argument('--sweep', type=int, default=0, metavar='PASSO',
                    help='passeggia il rettangolo su tutto lo schermo con '
                         'questo passo in pixel e confronta le tre scansioni')
    ap.add_argument('-v', '--verbose', action='store_true')
    ap.add_argument('--rects', action='store_true',
                    help='misura anche il RIPRISTINO (render_rects), dove '
                         'l\'indice per colonna puo\' perdere')
    args = ap.parse_args()

    common = bg_ref.load_common(args.gog)
    buf = scene_parse.load_all()[0]
    steps = list(enumerate(scene_parse.walk_steps(0)))

    print('registro le chiamate a M3d dei passi di riferimento...')
    calls = record_calls(buf, common, steps)
    print('%d chiamate registrate su %d passi\n' % (len(calls), len(steps)))

    # L'indice e gli span dipendono dalla STANZA, non dall'attore: si
    # costruiscono una volta per scena, come sul Jaguar.
    per_scene = {}
    tot = dict(blk=0, blk_hit=0, cell=0, depth=0, call=0, dos=0, rect=0, blit=0)
    tot2 = dict(col=0, col_hit=0, cell=0, depth=0, call=0, dos=0, rect=0,
                blit=0)
    print('%-6s %-5s %8s %7s %7s %5s   %8s %7s %7s %5s'
          % ('scena', 'tipo',
             'celle', 'blit_c', 'dos+ret', 'DIS',
             'celle', 'blit_c', 'dos+ret', 'DIS'))
    print('%-6s %-5s %8s %7s %7s %5s   %8s %7s %7s %5s'
          % ('', '', '--- BLOCCHI (oggi)', '', '', '',
             '--- COLONNE (nuovo)', '', '', ''))
    print('-' * 78)

    for call in calls:
        info = call['info']
        key = call['scene']
        if key not in per_scene:
            sx, sy = spans(info['bricks_px'])
            per_scene[key] = (build_blocks(info['cell_list']),
                              build_columns(info['cell_list']), sx, sy)
        blocks, colstart, sx, sy = per_scene[key]

        c, drawn = scan(info['cell_list'], blocks, info['bricks_px'],
                        info['vx'], info['vy'], sx, sy,
                        call['xm'], call['ym'], call['zm'],
                        call['rect'], call['doors'])
        c2, drawn2 = scan_col(info['cell_list'], colstart, info['bricks_px'],
                              info['vx'], info['vy'], sx, sy,
                              call['xm'], call['ym'], call['zm'],
                              call['rect'], call['doors'])

        # L'INVARIANTE, e non e' un confronto di conteggi: la SEQUENZA degli
        # indici disegnati dev'essere identica, stessi elementi e stesso
        # ordine. Se lo e', i pixel sono identici per costruzione - blit_cell
        # e' deterministica dato (cella, camera, rettangolo) - e non serve
        # confrontare un framebuffer per saperlo.
        if drawn != drawn2:
            print('DIVERGE scena %d %s: %r != %r'
                  % (key, call['rect'], drawn, drawn2))
            raise SystemExit('l\'indice per colonna NON e\' un sovrainsieme')

        for k in tot:
            tot[k] += c[k]
        for k in tot2:
            tot2[k] += c2[k]
        print('%-6d %-5s %8d %7d %7d %5d   %8d %7d %7d %5d'
              % (key, 'porta' if call['doors'] else 'corpo',
                 c['cell'], c['call'], c['dos'] + c['rect'], c['blit'],
                 c2['cell'], c2['call'], c2['dos'] + c2['rect'], c2['blit']))

    print('-' * 78)
    print('%-12s %8d %7d %7d %5d   %8d %7d %7d %5d'
          % ('TOTALE', tot['cell'], tot['call'], tot['dos'] + tot['rect'],
             tot['blit'], tot2['cell'], tot2['call'],
             tot2['dos'] + tot2['rect'], tot2['blit']))

    print()
    print('sequenza dei blit IDENTICA su tutte le %d chiamate' % len(calls))
    for key in sorted(per_scene):
        blocks, colstart, sx, sy = per_scene[key]
        print('scena %d: %d celle, %d blocchi da %d, span (%d,%d)'
              % (key, colstart[-1], len(blocks), CELL_BLOCK, sx, sy))
    print()
    print('celle viste per tessera disegnata:  blocchi %.0f  ->  colonne %.0f'
          % (tot['cell'] / max(1, tot['blit']),
             tot2['cell'] / max(1, tot2['blit'])))
    print('chiamate a blit_cell:               blocchi %d  ->  colonne %d '
          '(-%.0f%%)'
          % (tot['call'], tot2['call'],
             100.0 * (1 - tot2['call'] / max(1, tot['call']))))
    print('celle percorse:                     blocchi %d  ->  colonne %d '
          '(-%.0f%%)'
          % (tot['cell'], tot2['cell'],
             100.0 * (1 - tot2['cell'] / max(1, tot['cell']))))
    print('salti d\'indice per chiamata:        blocchi %.0f  ->  colonne %.0f'
          % (tot['blk'] / len(calls), tot2['col'] / len(calls)))

    if args.rects:
        print()
        print('IL RIPRISTINO (render_rects), stesso indice, riquadri diversi:')
        prologue_report(rects_report(per_scene, calls))

    if args.sweep:
        print()
        print('sweep con passo %d px...' % args.sweep)
        if not sweep(per_scene, calls, args.sweep, args.verbose):
            raise SystemExit(1)

    if args.dump:
        print()
        print('dump per il banco del C (passo %d px)...' % (args.sweep or 11))
        dump(per_scene, calls, args.dump, args.sweep or 11)


if __name__ == '__main__':
    main()
