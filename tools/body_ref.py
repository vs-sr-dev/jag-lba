#!/usr/bin/env python3
"""JagLBA V6 / M3b - riferimento offline degli ATTORI disegnati sul fondo.

A COSA SERVE
------------
Lo stesso mestiere doppio di tools/bg_ref.py, e per la stessa ragione:

  1. renderizza il fondo CON SOPRA i corpi degli attori e ne scrive un PNG, per
     il confronto a occhio contro il PC DOS ([[feedback_visual_over_metric]]);
  2. calcola le attese in engine_c/body_expect.h, cosi' il confronto meccanico
     lo fa il programma e non l'occhio.

"Si vede Twinsen" non e' un criterio. Si vede un Twinsen anche con la luce dal
lato sbagliato, con la posa a riposo non assemblata, con l'angolo Beta ignorato,
con l'ordine del pittore invertito - e ognuno di quei quattro sbagli produce una
figura riconoscibile. Le sonde si dividono il lavoro di dire QUALE.

DA DOVE VIENE OGNI PEZZO
------------------------
Niente qui e' inventato, e i tre valori che in S14 lo erano sono stati sostituiti
dalla loro regola:

  luce         SetLightVector(AlphaLight, BetaLight, 0) su tools/lba_trigo.py,
               trascritta da P_TRIGO.ASM:466. Gli angoli vengono dalla SCENA
               (DISKFUNC.C:81), non da una costante: in S14 era (-1,-1,-1)/sqrt3,
               che per le scene 0/1/2 e' a 118 gradi dalla luce vera.
  intensita'   dot(normale ruotata, luce) / prenormalized_range, negativo -> 0.
               P_OB_ISO.ASM:1687-1712. E' una divisione INTERA: la scala del
               risultato viene da li'.
  posa         gerarchia dei gruppi a riposo, tools/render_body.py.
  corpo        SearchBody sulla fiche FILE3D dell'attore, cioe' la stessa regola
               che gira sul Jaguar (tools/scene_parse.py).
  proiezione   Proj_ISO con le stesse costanti del fondo, cosi' un attore e la
               tessera su cui poggia non possono divergere.

QUEL CHE RESTA UNA RICOSTRUZIONE, DICHIARATA
--------------------------------------------
La mappatura intensita' -> indice CLUT (`coul1 & 0xF0` come base di rampa piu'
sedici sfumature) viene da S14 ed e' stata validata a occhio sul Jaguar, ma NON
e' ancora stata letta da S_POLY.ASM. E' l'unico pezzo di questa pipeline che
poggia su un confronto visivo invece che su una riga di sorgente. Va chiuso
prima di dichiarare M3, o dichiarato aperto insieme a M3.

USO
    python tools/body_ref.py              # tutti i passi di walk_steps()
    python tools/body_ref.py --step 2     # solo un passo, senza scrivere attese
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bg_ref
import anim_ref
import scene_parse
import sprite_ref
from lba_trigo import (identity, rot, rot_mat_index2, set_light_vector,
                       normal_intensity)
from render_body import parse_body_full, compute_groups
from body_lighting import parse_body as parse_body_normals

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

BG_W, BG_H = bg_ref.BG_W, bg_ref.BG_H
ISO_X, ISO_Y = bg_ref.ISO_X, bg_ref.ISO_Y

# Proj_ISO, P_TRIGO.ASM:1474. Il coefficiente della Y e' 30 in unita' di mondo,
# che diviso 512 da' i 15 pixel per livello di brick del fondo: e' lo stesso
# numero, espresso nelle due unita'.
ISO_Y_WORLD = 30
ISO_DIV = 512

# S32 - LO SCOSTAMENTO DELL'ANCORA, E PERCHE' ERA COSTANTE
#
# Gli attori e le tessere NON usano lo stesso centro di proiezione. Sono due
# costanti diverse scritte in due posti diversi, e finche' il riferimento ne
# usava una sola per entrambi, i corpi cadevano fuori posto della differenza -
# la stessa in ogni stanza, con ogni camera, che e' esattamente quel che
# l'utente aveva visto su tutti e tre i PNG.
#
#   tessere   Map2Screen, GRILLE_A.ASM:453/465  -> 320-8-23-1 = 288,  240-25 = 215
#   attori    Init3DGame, GAMEMENU.C:226        -> 320-8-1    = 311,  240    = 240
#             (identica in GERELIFE.C:1365, opcode LM_PROJ_ISO)
#
# Differenza: +23 in X, +25 in Y. Il -23/-25 delle tessere non e' il hot spot
# del brick - quello AffGraph lo aggiunge a parte, e il riferimento del fondo lo
# aggiunge a parte (bg_ref: sx = sx_col + hx). E' proprio un centro diverso.
#
# Qui NON si somma un delta: si sostituisce la costante. Sommare 23 e 25
# funzionerebbe uguale e nasconderebbe da dove vengono.
ACT_CX = 320 - 8 - 1
ACT_CY = 240

# Materiali, P_OB_ISO.ASM:330-334. Tre secchi, non dieci.
MAT_GOURAUD_MIN = 9
MAT_FLAT_MIN = 7

# IL PRECLIP PER ATTORE, AffScene (OBJECT.C:2620-2634).
#
# Il gioco disegna gli attori in DUE passate: la prima scarta e riempie la lista
# ordinata, la seconda disegna solo chi e' rimasto. Lo scarto prova UN SOLO
# PUNTO - l'ancora dell'attore proiettata - contro una finestra allargata, e chi
# non passa non entra mai in AffObjetIso: niente vertici, niente normali,
# niente ordinamento. Dentro AffObjetIso invece non c'e' nessuna uscita
# anticipata (verificato in P_OB_ISO.ASM:219): una volta entrati si trasforma
# tutto.
#
# I margini sono generosi APPOSTA: e' un test su un punto che fa le veci di un
# test su un volume, quindi un modello alto la cui ancora sta appena sotto il
# bordo si disegna lo stesso. Non sono un margine di sicurezza inventato - sono
# DEFINES.H:99-102, duplicati in COMMON.H:63-66.
VIEW_X0, VIEW_Y0 = -50, -30
VIEW_X1, VIEW_Y1 = 680, 580

# Le porte (SPRITE_CLIP, COMMON.H:152) hanno una finestra ancora piu' larga.
# NON E' ESERCITATA: nelle tre stanze del riferimento nessun attore con un corpo
# ha quella bandiera. Si porta lo stesso perche' e' la regola del gioco, ma le
# sonde non la coprono e va detto.
VIEW_CLIP_X0, VIEW_CLIP_Y0 = -112, -50
VIEW_CLIP_X1, VIEW_CLIP_Y1 = 640 + 112, 480 + 171
SPRITE_CLIP = 8
# Da M3e la finestra larga E' esercitata: le porte sono sprite, e adesso gli
# sprite si disegnano.
SPRITE_3D = 1024

# INVISIBLE (COMMON.H:158, commentato "not drawn but all computed"). Nel gioco
# NON e' un test a se': sta nella STESSA condizione dello scarto per corpo
# (OBJECT.C:2617, `(Body != -1) AND (!(Flags & INVISIBLE))`), quindi cade PRIMA
# della proiezione dell'ancora e prima del preclip. Si trascrive dov'e', non
# accanto al preclip che gli somiglia: sono due scarti di natura diversa - il
# preclip e' geometrico e non toglie un pixel, questo toglie roba che si vede.
INVISIBLE = 512

# LA FINESTRA E' QUELLA DELLO SCHERMO DOS, NON QUELLA DEL NOSTRO BUFFER.
# Il fondo e' 640x592 ma il rifiuto del gioco e' tarato su 640x480, e le righe
# in piu' esistono solo perche' un corpo ancorato in basso vada scritto per
# intero invece che ritagliato a meta'. E' la stessa scelta gia' fatta per il
# taglio delle tessere (bg_ref.DOS_CLIP_*, AffBrickBlock): riscalare i margini
# sulle 592 righe vorrebbe dire tenere attori che il PC DOS scarta, cioe'
# smettere di essere una trascrizione proprio ai bordi, che e' l'unico posto
# dove la differenza si vede.


def actor_preclip(anchor, flags):
    """True se l'attore va disegnato. `anchor` e' la sua ancora GIA' proiettata,
    cioe' l'equivalente di ProjettePoint(PosObj - WorldCube)."""
    ax, ay = anchor
    if (flags & 0xFFFF) & SPRITE_CLIP:
        return (VIEW_CLIP_X0 < ax < VIEW_CLIP_X1
                and VIEW_CLIP_Y0 < ay < VIEW_CLIP_Y1)
    return VIEW_X0 < ax < VIEW_X1 and VIEW_Y0 < ay < VIEW_Y1


def proj_iso(x, y, z, vx, vy):
    return ((x - z) * ISO_X // ISO_DIV + vx,
            ((x + z) * ISO_Y - y * ISO_Y_WORLD) // ISO_DIV + vy)


_BODY_HQR = None


def body_payload(buf, slot):
    """Il corpo nel formato ORIGINALE di LBA1, dal file GOG.

    Fino a S43 si leggeva dal contenitore, ed era la stessa cosa perche' il
    packer vi depositava i byte di LBA1. Adesso il contenitore porta i blob
    JBD pre-digeriti (tools/body_digest.py), e l'oracolo deve continuare a
    ragionare sull'originale: cosi' il banco su PC (che mangia il digest) e
    queste attese (calcolate dall'originale) stanno ai DUE LATI della
    digestione, e un difetto del digest fa cadere le sonde invece di
    propagarsi identico su tutti e due i lati - il buco simmetrico
    dell'oracolo, tappato per costruzione. `buf` resta nella firma per i
    tredici call site che lo passano; qui non serve piu'."""
    global _BODY_HQR
    if _BODY_HQR is None:
        import hqr_decompress as lzss
        with open(os.path.join(bg_ref.GOG_COMMON, 'BODY.HQR'), 'rb') as f:
            data = f.read()
        _BODY_HQR = (data, lzss.parse_offsets(data))
    data, offsets = _BODY_HQR
    if slot < 0 or slot >= len(offsets) or offsets[slot] == 0:
        return None
    import hqr_decompress as lzss
    return lzss.extract_entry(data, slot, offsets)


def prepare_body(payload):
    """Vertici in posa a riposo + normali + poligoni, da UN solo payload letto
    da due parser diversi.

    I due parser esistono gia' e leggono cose complementari: render_body legge i
    gruppi (e quindi puo' assemblare la posa), body_lighting legge le normali e
    gli indici di normale per angolo. Che entrambi contino gli STESSI vertici e'
    il controllo gratuito che tiene onesta questa doppia lettura."""
    full = parse_body_full(payload)
    lit = parse_body_normals(payload)

    if len(full['vertices']) != len(lit['verts']):
        raise SystemExit('i due parser del corpo non concordano sui vertici: '
                         '%d contro %d' % (len(full['vertices']),
                                           len(lit['verts'])))

    # M4: i vertici restano quelli ORIGINALI e i gruppi vengono con loro. La
    # posa non e' piu' una proprieta' del corpo (era vera solo finche' esisteva
    # una posa sola, quella a riposo): e' una proprieta' dell'ATTORE, che ha una
    # sua animazione e un suo fotogramma. La assembla `compute_groups` dentro
    # draw_actor, insieme alla rotazione dell'attore.
    return {'verts': full['vertices'], 'groups': full['groups'],
            'normals': lit['normals'], 'polys': lit['polys'],
            'lines': full['lines'], 'spheres': full['spheres']}


def shade_index(coul1, intensity_raw):
    """S32: NON e' piu' una ricostruzione. E' una somma, e basta.

        gouraud   P_OB_ISO.ASM:492   add  dl, ch        (ch = coul1)
        flat      P_OB_ISO.ASM:375   add  bp, [List_Normal+eax*2]

    Il byte che finisce nel riempimento e' `coul1 + intensita'`. Non c'e'
    rampa, non c'e' riscalatura, non c'e' bloccaggio dentro i sedici: il
    troncamento a un byte e' l'unico limite che il gioco applica.

    Perche' 0..15 ci sta senza doverlo imporre: l'intensita' e'
    dot(normale ruotata, luce) / range_prenormalizzato, e il range che ogni
    normale si porta dietro e' tarato perche' il quoziente stia in una rampa.
    Misurato sul corpo 1 con la luce della scena: 0..14 su 135 normali. Il
    bloccaggio a 15 della vecchia formula non serviva a niente, e la
    riscalatura *15/59 invece FACEVA danno - schiacciava 0..14 in 0..3, cioe'
    i corpi erano quasi piatti. Il 59 veniva dalla lunghezza del vettore luce,
    che con questa divisione non c'entra: e' gia' dentro il quoziente."""
    return (coul1 + intensity_raw) & 0xFF


def poly_depth(poly, sums):
    """Chiave del pittore, P_OB_ISO.ASM. NON e' piu' l'euristica di S13.

    Il gioco calcola per OGNI punto proiettato il valore `-(X+Y+Z)` in
    coordinate ruotate e traslate (boucleproj: `sub ebp,ecx` poi `sub bp,bx`,
    righe 1288 e 1291), lo tiene per poligono al MASSIMO (`jle nozmaxf` /
    `jle nozmaxg`), e ordina le entita' per quel valore DECRESCENTE (permut2
    scambia quando `[esi] < [ebp]`).

    Massimo di -(X+Y+Z) decrescente = minimo di X+Y+Z crescente, ed e' quello
    che si torna qui.

    Tre differenze dall'euristica di S13, e ognuna cambia il disegno:
      - il MINIMO su tutti gli angoli, non la SOMMA sui primi tre;
      - coordinate RUOTATE dal Beta dell'attore, non a riposo;
      - il valore e' per punto, quindi due poligoni che condividono lo spigolo
        piu' vicino pareggiano - e li separa il criterio di parita' qui sotto.
    """
    return min(sums[c[0]] for c in poly['corners'])


def poly_visible(xs, ys):
    """Faccia nascosta, `testpoly` (P_OB_ISO.ASM:507-527). E' un prodotto
    vettoriale a DUE dimensioni sui primi tre angoli GIA' PROIETTATI - non una
    normale di faccia nello spazio:

        (X0-X2)*(Y1-Y0) - (Y0-Y2)*(X1-X0)   <  0   ->  si disegna
                                            >= 0   ->  `jnl badpoly`

    Il riferimento di M3b non lo faceva affatto, e non era innocuo: senza,
    ogni corpo disegna anche le facce di dietro, e con l'ordine del pittore
    una faccia di dietro piu' lontana copre quella davanti."""
    if len(xs) < 3:
        return False
    return ((xs[0] - xs[2]) * (ys[1] - ys[0])
            - (ys[0] - ys[2]) * (xs[1] - xs[0])) < 0


def build_edges(xs, ys, shades):
    """I bordi di un poligono come li costruisce `ComputePoly_A`
    (LIB_SVGA/S_POLY.ASM:519-705). Uno per lato non orizzontale.

    IL VERSO DI Y DECIDE IL LATO, NON L'ORDINE DELLE X
    --------------------------------------------------
    Il gioco tiene DUE tabelle, `TabVerticG` e `TabVerticD`, e un bordo va
    nell'una o nell'altra a seconda che Y scenda o salga nell'ordine del
    poligono (`jl y_monte`, riga 556). Non c'e' nessun ordinamento: la faccia
    nascosta ha gia' garantito il verso di avvolgimento, quindi il bordo dove
    Y scende E' quello di sinistra. E' anche il motivo per cui a `xD < xG` il
    gioco SALTA la riga (`sub cx,ax / jc nextlig`) invece di scambiare i due:
    se sono incrociati il poligono non ha spessore li'.

    IL DDA PARTE DALL'ESTREMO CON X ESTREMA
    ---------------------------------------
    Sinistra dall'estremo con X minore, destra da quello con X maggiore, e
    l'altro estremo segue (`xchg bx,dx` + `xchg ax,cx`, con le intensita' che
    seguono a ruota). Cosi' il delta X e' SEMPRE positivo e la `div` SEMPRE
    senza segno: il verso decide il segno, esattamente come in
    `GouraudInverse`. Sinistra somma il passo, destra lo sottrae (`sbb`).

    IL BIAS DI MEZZO PIXEL
    ----------------------
    La frazione iniziale e' `resto/2 + 32767` a sinistra e `32767 - resto/2` a
    destra (`shr ax,1 / add ax,7FFFh`, con un `neg ax` in mezzo a destra). Il
    resto e' quello della divisione della pendenza. Vale un arrotondamento al
    pixel piu' vicino invece del troncamento, e senza di esso il bordo cade un
    pixel piu' dentro sul 44% delle campate.

    L'accumulo del gioco e' una catena di `adc` su un registro dove la
    frazione sta in alto e l'intero in basso, cioe' il riporto della frazione
    entra nell'intero con un giro di ritardo. Il risultato e' esattamente
    `base + ((frazione_iniziale +- k*pendenza) >> 16)`, che e' quel che si
    calcola qui: la rotazione dei registri e' un modo di scriverlo, non una
    semantica in piu'.

    Ogni bordo copre k = 0..dy, cioe' dy+1 righe: il conteggio srotolato del
    gioco (`add cx,2 / shr cx,1` con i due punti d'ingresso) ne fa dy+1 in
    tutti e due i casi di parita'.
    """
    n = len(xs)
    edges = []
    for e in range(n):
        xa, ya, ia = xs[e], ys[e], shades[e]
        j = (e + 1) % n
        xb, yb, ib = xs[j], ys[j], shades[j]
        if ya == yb:
            continue                       # same_y: il gioco non stocca nulla

        right = yb < ya
        dy = ya - yb if right else yb - ya
        # base = estremo con X maggiore (destra) o minore (sinistra)
        if (xa >= xb) if right else (xa <= xb):
            bx, by, i1, i2, oy = xa, ya, ia, ib, yb
        else:
            bx, by, i1, i2, oy = xb, yb, ib, ia, ya
        dxa = abs(xb - xa)
        sy = 1 if oy > by else -1

        slope, rem = divmod(dxa << 16, dy)
        fx = 0x7FFF - (rem >> 1) if right else 0x7FFF + (rem >> 1)

        # Intensita' di bordo: 8.8, stesso schema. `shr al,1` lavora sul solo
        # byte basso del resto e `add al,7Fh` non propaga il riporto, percio'
        # il mascheramento a 8 bit fa parte della regola.
        di = i2 - i1
        istep, irem = divmod((di if di >= 0 else -di) << 8, dy)
        ifr = (((irem & 0xFF) >> 1) + 0x7F if di >= 0
               else (0x7F - ((irem & 0xFF) >> 1)) & 0xFF)

        edges.append((right, by, sy, dy, bx, fx, slope,
                      (i1 << 8) + ifr, istep, di < 0))
    return edges


def fill_poly(fb, xs, ys, shades):
    """Riempimento a scansione: `ComputePoly_A` + `FillVertic_A`.

    Il campo di righe e' `Ymin..Ymax` INCLUSIVO - `sub edx,eax / inc edx` in
    `FillVertic_A` (S_FILLV.ASM:120). Contava: la regola semiaperta che questo
    file usava fino a S32 lasciava fuori l'ultima riga di OGNI poligono, cioe'
    669 righe sulle tre stanze del riferimento.

    Il gradiente lungo la riga viene da `SVGAPolyGouraud` (S_FILLV.ASM:846):
    intensita' in 8.8, passo `(i2-i1)*256/dx` con divisione intera, si accumula
    e si scrive il byte ALTO, e la campata e' `xr-xl+1` pixel, estremi
    compresi (`inc cx / shr cx,1`). Il verso decide il segno anche qui
    (`GouraudInverse`), cosi' l'accumulatore non esce mai dall'intervallo delle
    intensita' degli angoli - l'invariante che in S32 ha trovato i puntini
    bianchi che le somme di controllo non vedevano.

    QUEL CHE RESTA FUORI, DICHIARATO: il gioco RITAGLIA il poligono prima
    (ClipGauche/Droit/Haut/Bas) e quindi il suo campo di righe e' gia' dentro
    lo schermo. Qui il ritaglio non c'e': si limitano le righe e le colonne
    scritte, il che sposta il gradiente di nulla (l'accumulatore avanza lo
    stesso, come nel gioco) ma non ricostruisce i vertici tagliati. Sulle tre
    stanze del riferimento nessun poligono esce dallo schermo, quindi la
    differenza non e' misurabile qui.
    CONDIZIONE DI RIMOZIONE: il ritaglio Sutherland-Hodgman c'e' gia' scritto e
    validato in src/gpu_raster.s (GpuClipMain, 4 passate); si accende quando
    serve, cioe' quando un attore arriva davvero a bordo schermo.
    """
    if len(xs) < 3:
        return 0
    edges = build_edges(xs, ys, shades)
    drawn = 0
    for y in range(max(0, min(ys)), min(BG_H - 1, max(ys)) + 1):
        xl = xr = sl = sr = None
        for (right, by, sy, dy, bx, fx, slope, iacc0, istep, isub) in edges:
            k = (y - by) * sy
            if k < 0 or k > dy:
                continue
            x = bx + ((fx - k * slope) >> 16 if right
                      else (fx + k * slope) >> 16)
            s = iacc0 - k * istep if isub else iacc0 + k * istep
            if right:
                xr, sr = x, s
            else:
                xl, sl = x, s
        if xl is None or xr is None or xr < xl:
            continue                       # jc nextlig: nessuno spessore
        if xr < 0 or xl >= BG_W:
            continue
        drawn += _fill_span(fb, y, xl, xr, sl, sr)
    return drawn


# ---------------------------------------------------------------------------
# LINEE E SFERE (E_LIGNE / E_SPHERE)
#
# La finestra di ritaglio e' quella di UnSetClip (INITSVGA.ASM:266): tutto lo
# schermo, `0..Screen_X-1` e `0..Screen_Y-1`. Il nostro schermo e' il buffer,
# quindi 0..639 e 0..591 - non le 480 righe del DOS. Non e' la stessa scelta
# fatta per il preclip degli attori, e la differenza ha una ragione: il preclip
# decide SE un attore esiste (e li' la finestra e' quella del gioco), il
# ritaglio decide DOVE si puo' scrivere, e si puo' scrivere ovunque ci sia
# buffer, esattamente come per le tessere e per il riempimento dei poligoni.
#
# Per linee e sfere il ritaglio NON e' un limite messo in fondo: sposta gli
# estremi PRIMA della rasterizzazione, quindi cambia quali pixel escono. Va
# trascritto dov'e', non sostituito da un taglio a valle.
CLIP_X0, CLIP_Y0 = 0, 0
CLIP_X1, CLIP_Y1 = BG_W - 1, BG_H - 1


def sphere_radius(rayon):
    """Raggio a schermo di una E_SPHERE, P_OB_ISO.ASM:928 (ramo `isosph`):

        rs = (rayon * 34) >> 9

    34 e' `racine(sx^2+sy^2)` e 512 e' IsoScale, cioe' lo stesso divisore della
    proiezione dei vertici: il raggio e' nella scala dei vertici perche' usa la
    scala dei vertici. Il ramo prospettico (TYPE_3D, righe 915-920) non serve:
    la scena di gioco e' isometrica."""
    return (rayon * 34) >> 9


def line_runs(x0, y0, x1, y1):
    """`Line_A` (S_LINE.ASM:46) - ritaglio di Cohen-Sutherland e poi Bresenham.

    Torna le TRATTE ORIZZONTALI, `(y, xl, xr)`, non i singoli pixel. Non e' una
    riscrittura: una linea di Bresenham scrive per ogni riga pixel CONTIGUI (il
    ramo orizzontale avanza X a ogni passo e cambia riga solo al riporto, il
    ramo verticale scrive un pixel per riga), quindi le tratte sono un modo
    esatto di dire gli stessi pixel. Serve perche' sul Jaguar il riempimento lo
    fa la GPU a tratte: vedi engine_c/body_draw.c.

    Il ritaglio sposta gli estremi sul bordo con una divisione intera, e da li'
    riparte il test: e' un ciclo, non una passata (`jmp onceagain`).
    """
    # `cmp eax,ecx / jle sensok`: la linea si percorre sempre da X minore.
    if x0 > x1:
        x0, x1 = x1, x0
        y0, y1 = y1, y0

    for _ in range(8):                      # il ciclo converge in <= 4 tagli
        code = 0
        if x0 < CLIP_X0:
            code |= 1
        elif x0 > CLIP_X1:
            return []                       # notseen: si parte da X minore
        if y0 < CLIP_Y0:
            code |= 8
        elif y0 > CLIP_Y1:
            code |= 4
        code <<= 8
        if x1 < CLIP_X0:
            return []                       # notseen
        if x1 > CLIP_X1:
            code |= 2
        if y1 < CLIP_Y0:
            code |= 8
        elif y1 > CLIP_Y1:
            code |= 4

        # `test si,bp`: i due estremi fuori dallo STESSO lato -> invisibile.
        hi = code >> 8
        if hi & code:
            return []
        if ((hi | code) & 0xFF) == 0:
            break                           # `jz draw`

        dx = x1 - x0
        dy = y1 - y0
        if code & 0x100:                    # x0 a sinistra
            y0 += _idiv((CLIP_X0 - x0) * dy, dx)
            x0 = CLIP_X0
        elif code & 0x800:                  # y0 sopra
            x0 += _idiv((CLIP_Y0 - y0) * dx, dy)
            y0 = CLIP_Y0
        elif code & 0x400:                  # y0 sotto
            x0 += _idiv((CLIP_Y1 - y0) * dx, dy)
            y0 = CLIP_Y1
        elif code & 0x002:                  # x1 a destra
            y1 = y0 + _idiv((CLIP_X1 - x0) * dy, dx)
            x1 = CLIP_X1
        elif code & 0x008:                  # y1 sopra
            x1 = x0 + _idiv((CLIP_Y0 - y0) * dx, dy)
            y1 = CLIP_Y0
        elif code & 0x004:                  # y1 sotto
            x1 = x0 + _idiv((CLIP_Y1 - y0) * dx, dy)
            y1 = CLIP_Y1
        else:
            return []
    else:
        return []

    # --- `draw`: Bresenham, un pixel per passo, la riga cambia al riporto.
    dx = x1 - x0
    dy = y1 - y0
    sy = 1
    if dy < 0:
        sy = -1
        dy = -dy

    runs = []
    x, y = x0, y0
    if dx >= dy:                            # `horizontal`
        err = dx
        run_x0 = x
        for _ in range(dx + 1):
            err -= 2 * dy
            if err < 0:
                err += 2 * dx
                runs.append((y, run_x0, x))
                y += sy
                run_x0 = x + 1
            x += 1
        if run_x0 <= x - 1:
            runs.append((y, run_x0, x - 1))
    else:                                   # `vertical`
        err = dy
        for _ in range(dy + 1):
            runs.append((y, x, x))
            err -= 2 * dx
            if err < 0:
                err += 2 * dy
                x += 1                      # `adc edi,esi`: riga E colonna
            y += sy
    return runs


def _idiv(a, b):
    """`idiv`: divisione intera che TRONCA VERSO LO ZERO, non verso il basso.
    Il `//` di Python arrotonda verso -infinito e su un numeratore negativo -
    che qui e' la norma, il ritaglio sottrae coordinate - darebbe un pixel di
    differenza."""
    q = abs(a) // abs(b)
    return -q if (a < 0) != (b < 0) else q


def sphere_runs(cx, cy, rs):
    """`ComputeSphere_A` (S_POLY.ASM:112) - il cerchio di Bresenham che riempie
    TabVerticG/TabVerticD, cioe' le stesse due tabelle che ComputePoly_A riempie
    per un poligono. Il riempimento poi e' `FillVertic_A`, la STESSA dei
    poligoni: qui si torna solo le tratte.

    L'accumulatore parte a `-rayon` e il passo e' `sum += y`, col riporto
    dell'add senza segno al posto del test di segno: quando la somma torna
    positiva la X cala di uno e la somma si riporta indietro di quella X. E' un
    cerchio piu' grasso di quello vero ai poli - sui raggi di 2-4 pixel che
    hanno le mani di Twinsen e' un pallino, ed e' IL pallino che disegna il DOS.

    Il ramo di ritaglio (`clipping`, riga 214) e' trascritto ma NON ESERCITATO:
    nelle tre stanze del riferimento nessuna sfera tocca il bordo. Dove il DOS
    lascerebbe leggere righe mai scritte dentro Ymin..Ymax, qui la riga si
    salta: quel che il DOS ci trova e' memoria vecchia, che non e' riproducibile
    e non e' una regola.
    """
    if rs < 0:
        return []

    clip = 0
    ymin = cy - rs
    if ymin > CLIP_Y1:
        return []
    if ymin < CLIP_Y0:
        clip += 1
        ymin = CLIP_Y0
    ymax = cy + rs
    if ymax < CLIP_Y0:
        return []
    if ymax > CLIP_Y1:
        clip += 1
        ymax = CLIP_Y1
    if cx + rs < CLIP_X0:
        return []
    if cx + rs > CLIP_X1:
        clip += 1
    if cx - rs > CLIP_X1:
        return []
    if cx - rs < CLIP_X0:
        clip += 1

    tab = {}

    def put(row, xg, xd):
        """Una riga delle due tabelle. Nel ramo senza ritaglio il DOS scrive e
        basta; nel ramo con ritaglio limita le X al bordo e, se la tratta cade
        tutta FUORI da un lato, stringe Ymin o Ymax invece di scrivere."""
        nonlocal ymin, ymax
        if not clip:
            tab[row] = (xg, xd)
            return True
        if row > ymax or row < ymin:
            return True
        g, d = xg, xd
        if g > CLIP_X1:                     # tutta a destra dello schermo
            return False
        if g < CLIP_X0:
            g = CLIP_X0
        if d < CLIP_X0:                     # tutta a sinistra
            return False
        if d > CLIP_X1:
            d = CLIP_X1
        tab[row] = (g, d)
        return True

    px, py, s = rs, 0, -rs
    while py <= px:
        # `nccs0` / `cs0`: le due righe a distanza py, larghe px.
        if not put(cy + py, cx - px, cx + px):
            ymax = cy + py - 1
        if not put(cy - py, cx - px, cx + px):
            ymin = cy - py + 1
        s += py
        if s >= 0:
            # il riporto: le due righe a distanza px, larghe py.
            if not put(cy + px, cx - py, cx + py):
                ymax = cy + px - 1
            if not put(cy - px, cx - py, cx + py):
                ymin = cy - px + 1
            px -= 1
            s -= px
        py += 1

    if clip and ymin >= ymax:               # `finclip`: jge nosphere
        return []
    return [(y, tab[y][0], tab[y][1]) for y in range(ymin, ymax + 1)
            if y in tab]


# S47, LA SORVEGLIANZA DELL'INDICE ZERO - e costa zero sul Jaguar.
#
# L'attore-sprite si compone via OP, e il TRANS dell'hardware cancella i pixel
# a INDICE 0 (netlist verilog/tom/obdata.v: in mode8 `zero8` = NOR degli otto
# bit, e il write-enable del line buffer si spegne). Un corpo che rendesse un
# pixel a 0 si bucherebbe da solo, e il buco si vedrebbe solo a occhio, su una
# posa, in una stanza.
#
# MISURATO in S47 sulla scena 0: l'eroe non produce NEMMENO UN pixel a zero
# (i "1201 neri" che si erano ipotizzati erano sovrascritture dell'ordine del
# pittore: 2705 SCRITTURE contro 1504 pixel distinti). Quindi l'alias del nero
# - l'indice 31, che nella CLUT di LBA1 e' gia' (0,0,0) identico a CLUT[0] -
# NON serve: non c'e' niente da rimappare.
#
# Ma "oggi non capita" non e' "non puo' capitare": qui si CONTA, l'attesa e'
# zero, e il conteggio lo fa l'ORACOLO invece del Jaguar - il ciclo caldo della
# GPU non paga un confronto per pixel per una condizione che non si verifica.
# Se un corpo o una posa un giorno la producesse, lo dice questo numero.
ZERO_PIX = [0]


def fill_runs(fb, runs, colour):
    """Le tratte di una linea o di una sfera, scritte a colore COSTANTE.

    E' `FillVertic_A` col secchio piatto: linee e sfere non hanno una normale,
    quindi non c'e' intensita' da sommare e non c'e' gradiente lungo la riga.
    Il colore e' `coul1` nudo - `mov ax,[esi+1]` in aff_E_SPHERE e il byte basso
    di `couleur` in Line_A - ed e' lo stesso trattamento che i poligoni del
    secchio "motivo" (materiali 0..6) ricevono gia'.

    Sulle 1015 linee e 272 sfere di BODY.HQR la matiere e' 0 in tutte le linee e
    in 259 sfere su 272; le altre tredici chiedono Tele/Copper/Bopper, effetti
    che il porting non disegna nemmeno sui poligoni."""
    drawn = 0
    if colour == 0:
        ZERO_PIX[0] += 1               # una tratta intera, contata una volta
    for (y, xl, xr) in runs:
        row = y * BG_W
        for x in range(xl, xr + 1):
            fb[row + x] = colour
            drawn += 1
    return drawn


def _fill_span(fb, y, xl, xr, sl, sr):
    """Una riga, `SVGAPolyGouraud`. L'accumulatore parte dall'estremo di
    intensita' piu' bassa con un passo non negativo, e quando la campata esce
    dallo schermo avanza lo stesso sui pixel scartati: tagliare la campata non
    deve sfasare il gradiente su quelli che restano."""
    dx = xr - xl
    row = y * BG_W
    drawn = 0
    if sr >= sl:
        step = (sr - sl) // dx if dx > 0 else 0
        acc, x = sl, xl
        if x < 0:
            acc += step * (-x)
            x = 0
        xend = xr if xr < BG_W else BG_W - 1
        while x <= xend:
            v = (acc >> 8) & 0xFF
            if v == 0:
                ZERO_PIX[0] += 1
            fb[row + x] = v
            acc += step
            x += 1
            drawn += 1
    else:
        step = (sl - sr) // dx if dx > 0 else 0
        acc, x = sr, xr
        if x > BG_W - 1:
            acc += step * (x - (BG_W - 1))
            x = BG_W - 1
        xend = xl if xl > 0 else 0
        while x >= xend:
            v = (acc >> 8) & 0xFF
            if v == 0:
                ZERO_PIX[0] += 1
            fb[row + x] = v
            acc += step
            x -= 1
            drawn += 1
    return drawn


class Fletch(object):
    """Le due somme, nella stessa forma del C (`s1 += w; s2 += s1`), tenute in
    32 bit. Servono a coprire un ARRAY con due numeri: s1 dice il contenuto,
    s2 l'ordine, quindi due voci scambiate non si compensano."""

    def __init__(self):
        self.s1 = 0
        self.s2 = 0

    def add(self, w):
        self.s1 = (self.s1 + (w & 0xFFFFFFFF)) & 0xFFFFFFFF
        self.s2 = (self.s2 + self.s1) & 0xFFFFFFFF


def draw_actor(fb, body, actor, light, vx, vy, g, sh, od, cull=True):
    """Un attore sul framebuffer.

    `g`, `sh`, `od` sono tre accumulatori di Fletcher che attraversano tutti
    gli attori del passo e coprono TRE STADI DIVERSI della pipeline:

        g   i vertici proiettati  -> posa, matrice Beta, proiezione
        sh  le intensita'         -> luce, rotazione delle normali, divisione
        od  l'ordine di disegno   -> faccia nascosta, chiave, ordinamento

    Sono separati apposta. Una somma sola sui pixel direbbe soltanto "no", e
    quattro sbagli diversi (posa non assemblata, luce dal lato sbagliato, Beta
    ignorato, ordine invertito) producono tutti una figura riconoscibile: e'
    proprio quando il disegno sembra plausibile che serve sapere QUALE stadio
    ha ceduto. E' la stessa divisione del lavoro delle sonde del fondo.
    """
    # La matrice dell'attore: solo Beta, cioe' l'angolo attorno a Y che la
    # scena gli assegna.
    m = rot_mat_index2(identity(), 0, actor.beta, 0)

    # LA POSA (M4). I vertici escono di qui gia' ruotati, uno per gruppo con la
    # matrice del proprio gruppo; a valle resta solo traslare e proiettare.
    #
    # Un corpo senza gruppi (bit INFO_ANIM spento) non passa da qui: e' il ramo
    # ComputeNuage del gioco, una matrice sola per tutto.
    if body['groups']:
        verts, mats = compute_groups(body['verts'], body['groups'],
                                     identity(), 0, actor.beta, 0,
                                     getattr(actor, 'pose', None))
    else:
        verts = [rot(m, *v) for v in body['verts']]
        mats = None

    px = []
    py = []
    pz = []
    # IL RETTANGOLO DELL'ATTORE (M3d), P_OB_ISO.ASM:231-236 e 1270-1332. Si
    # accumula su TUTTI i vertici proiettati - lo fa il ciclo di proiezione,
    # prima ancora della faccia nascosta - e le sfere lo allargano del raggio.
    box = [32767, 32767, -32767, -32767]
    for (rx, ry, rz) in verts:
        wx, wy, wz = rx + actor.x, ry + actor.y, rz + actor.z
        sx, sy = proj_iso(wx, wy, wz, vx, vy)
        px.append(sx)
        py.append(sy)
        pz.append(wx + wy + wz)
        box[0] = min(box[0], sx)
        box[1] = min(box[1], sy)
        box[2] = max(box[2], sx)
        box[3] = max(box[3], sy)
        g.add(((sx & 0xFFFF) << 16) | (sy & 0xFFFF))

    # LE INTENSITA'. Anche le normali sono raggruppate: il conteggio sta a +18
    # nel record del gruppo, le normali si consumano in ordine e ognuna si ruota
    # con la matrice del PROPRIO gruppo (ComputeAnimNormal, P_OB_ISO.ASM:1436).
    #
    # Finche' la posa e' quella a riposo tutte le matrici sono uguali e questo
    # ciclo da' quel che dava prima. Con un'animazione no: un braccio alzato
    # sarebbe illuminato come se non si fosse mosso.
    inten = []
    if mats is None:
        norm_mat = [m] * len(body['normals'])
    else:
        norm_mat = []
        for gi, gr in enumerate(body['groups']):
            norm_mat.extend([mats[gi]] * gr['nb_norm'])
        # I conteggi per gruppo sommano al totale su tutti i corpi
        # dell'archivio; se un giorno non fosse cosi', le normali in eccesso
        # devono avere UNA matrice dichiarata invece di dipendere dall'ordine.
        while len(norm_mat) < len(body['normals']):
            norm_mat.append(mats[0])

    for i, (nx, ny, nz, rng) in enumerate(body['normals']):
        v = normal_intensity(nx, ny, nz, rng, light, norm_mat[i])
        inten.append(v)
        sh.add(v)

    # LA FACCIA NASCOSTA SI TOGLIE PRIMA DI ORDINARE, come nel gioco: `testpoly`
    # sta dentro la costruzione del poligono e chi non passa non entra mai nella
    # lista che il quicksort ordinera'. Sull'insieme dei sopravvissuti l'ordine
    # e' lo stesso in un verso e nell'altro (la chiave non dipende da chi c'e'),
    # ma cosi' il Jaguar ordina la meta' delle voci invece di tutte.
    # --- LA LISTA E' UNA SOLA, ed e' il punto di tutta questa voce.
    #
    # `TotalEntite` conta poligoni PIU' linee PIU' sfere e `PointeurListTri` e'
    # lo stesso puntatore per tutte e tre (P_OB_ISO.ASM:559, 611): il gioco le
    # ordina INSIEME. Metterle in una passata a parte le farebbe finire tutte
    # davanti o tutte dietro - cioe' le mani di Twinsen sopra la tunica.
    #
    # L'indice che rompe i pareggi e' quello GLOBALE, nell'ordine in cui il
    # gioco riempie List_Tri: prima i poligoni, poi le linee, poi le sfere.
    #
    # Linee e sfere NON passano dalla faccia nascosta: `testpoly` prova tre
    # angoli proiettati, e una linea ne ha due e una sfera uno. Entrano tutte.
    nb_poly = len(body['polys'])
    nb_line = len(body['lines'])
    live = []
    for i, p in enumerate(body['polys']):
        xs = [px[c[0]] for c in p['corners']]
        ys = [py[c[0]] for c in p['corners']]
        # `cull=False` serve SOLO a tools/actor_zoom.py, per far vedere tutta
        # la geometria di un corpo quando a schermo se ne vede una fetta. Il
        # riferimento non lo usa mai: la faccia nascosta e' parte della regola.
        if (not cull) or poly_visible(xs, ys):
            live.append(i)

    def ent_depth(gi):
        """La chiave del pittore per l'entita' globale `gi`.

        Poligono: il minimo di X+Y+Z sugli angoli (poly_depth).
        Linea:    il minimo sui DUE estremi. Il gioco tiene `-(X+Y+Z)` e prende
                  il MASSIMO fra i due (P_OB_ISO.ASM:584, `jl zok0`), che e' lo
                  stesso numero letto nell'altro verso.
        Sfera:    il valore del suo UNICO punto (riga 624). Nient'altro."""
        if gi < nb_poly:
            return poly_depth(body['polys'][gi], pz)
        if gi < nb_poly + nb_line:
            ln = body['lines'][gi - nb_poly]
            return min(pz[_v(ln['v0'])], pz[_v(ln['v1'])])
        return pz[_v(body['spheres'][gi - nb_poly - nb_line]['v'])]

    def _v(i):
        return i if i < len(pz) else 0

    ents = live + list(range(nb_poly, nb_poly + nb_line + len(body['spheres'])))

    # L'ordine e' un ordine TOTALE: chiave, poi indice dell'entita'.
    #
    # Il gioco ordina con un quicksort che stabile non e' (P_OB_ISO.ASM:700), e
    # sui pareggi sceglie in un modo che dipende dai pivot. I pareggi ci sono
    # eccome, perche' la chiave e' il minimo su un vertice CONDIVISO. Aggiungere
    # l'indice li rompe sempre allo stesso modo, di qua e sul Jaguar: e' una
    # scelta DICHIARATA, l'unico punto in cui questa pipeline non puo' essere
    # identica al gioco perche' il gioco stesso non e' identico a se' stesso.
    order = sorted(ents, key=lambda i: (ent_depth(i), i))
    for i in order:
        od.add(((ent_depth(i) & 0xFFFF) << 16) | i)

    drawn = skipped = pixels = 0
    ent_pixels = 0
    first_sphere = None
    skipped += nb_poly - len(live)
    for gi in order:
        # --- una LINEA ---
        if nb_poly <= gi < nb_poly + nb_line:
            ln = body['lines'][gi - nb_poly]
            a, b = _v(ln['v0']), _v(ln['v1'])
            ent_pixels += fill_runs(fb, line_runs(px[a], py[a], px[b], py[b]),
                                    ln['coul1'])
            continue
        # --- una SFERA ---
        if gi >= nb_poly + nb_line:
            sp = body['spheres'][gi - nb_poly - nb_line]
            v = _v(sp['v'])
            rs = sphere_radius(sp['rayon'])
            # La sfera ALLARGA il rettangolo del suo raggio (P_OB_ISO:936-956):
            # il centro e' gia' un vertice proiettato, ma il pallino sporge.
            box[0] = min(box[0], px[v] - rs)
            box[1] = min(box[1], py[v] - rs)
            box[2] = max(box[2], px[v] + rs)
            box[3] = max(box[3], py[v] + rs)
            if first_sphere is None:
                first_sphere = (px[v], py[v], rs)
            ent_pixels += fill_runs(fb, sphere_runs(px[v], py[v], rs),
                                    sp['coul1'])
            continue

        i = gi
        p = body['polys'][i]
        xs = [px[c[0]] for c in p['corners']]
        ys = [py[c[0]] for c in p['corners']]
        if max(xs) < 0 or min(xs) >= BG_W or max(ys) < 0 or min(ys) >= BG_H:
            skipped += 1
            continue

        if p['mat'] >= MAT_GOURAUD_MIN:
            shades = []
            for c in p['corners']:
                ni = c[1] if len(c) > 1 else None
                raw = inten[ni] if (ni is not None and ni < len(inten)) else 0
                shades.append(shade_index(p['coul1'], raw))
        else:
            ni = p.get('flat_normal_idx')
            raw = inten[ni] if (ni is not None and ni < len(inten)) else 0
            # Il secchio "motivo" (materiali 0..6, il 2,3% dei poligoni) non ha
            # normale di faccia, quindi raw resta 0 e il colore e' coul1 nudo -
            # che e' anche quel che il gioco stocca per quel secchio
            # (P_OB_ISO.ASM:337, coul1/coul2 senza somma). Resta un ripiego
            # DICHIARATO sul motivo vero e proprio: coul2 e la trama non si
            # disegnano, si prende solo coul1.
            flat = shade_index(p['coul1'], raw)
            shades = [flat] * len(p['corners'])

        n = fill_poly(fb, xs, ys, shades)
        if n:
            drawn += 1
            pixels += n
        else:
            skipped += 1

    ax, ay = proj_iso(actor.x, actor.y, actor.z, vx, vy)
    # `tot` e' TotalEntite, cioe' tutte e tre le sezioni: e' il numero che il
    # gioco conta e quello che la sonda CULL deve dire. `live` e' quel che e'
    # finito nella lista ordinata.
    tot = nb_poly + nb_line + len(body['spheres'])
    ent = {'lines': nb_line, 'spheres': len(body['spheres']),
           'pixels': ent_pixels, 'first': first_sphere, 'box': box}
    return (drawn, skipped, pixels + ent_pixels, (ax, ay), tot, len(order),
            ent)


def actor_pose(buf, actor, step_i, nb_groups_body):
    """La posa che questo attore assume al passo `step_i`, o None se e' fermo.

    I due fotogrammi e il passo dell'interpolazione li decide scene_parse, in un
    posto solo, perche' il motore C deve assumere ESATTAMENTE gli stessi: la
    tabella finisce dentro body_expect.h e il Jaguar la legge da li'."""
    fr = scene_parse.actor_pose_frames(buf, actor, step_i)
    if fr is None:
        return None
    payload = scene_parse.anim_payload(buf, actor.anim)
    info = anim_ref.parse_anim(payload) if payload else None
    if info is None:
        return None
    dest, src, cur = fr
    return anim_ref.frame_pose(info, dest, src, cur, nb_groups_body)[0]


def render_step(buf, common, num, enter, step_i=0):
    """Il fondo del passo, con sopra gli attori che hanno un corpo."""
    fb, info = bg_ref.render(num, common, enter=enter)
    scenes = scene_parse.load_all()[2]
    actors = scene_parse.resolve_actors(buf, scenes[num], enter)

    s = scenes[num]
    light = set_light_vector(s.alpha_light, s.beta_light, 0)

    g, sh, od = Fletch(), Fletch(), Fletch()
    stats = {'light': light, 'alpha': s.alpha_light, 'beta': s.beta_light,
             'vx': info['vx'], 'vy': info['vy'], 'actors': [],
             'drawn': 0, 'polys': 0, 'pixels': 0, 'tot': 0, 'live': 0,
             'clipped': 0, 'invis': 0,
             'spr': 0, 'spr_clip': 0, 'spr_pixels': 0, 'spr_first': None,
             'ent_lines': 0, 'ent_spheres': 0, 'ent_pixels': 0,
             'ent_first': None, 'over': 0,
             'g': g, 'sh': sh, 'od': od}

    # Lo scostamento di schermo degli ATTORI, che non e' quello delle tessere:
    # stessa camera, centro di proiezione diverso (vedi ACT_CX/ACT_CY).
    avx = info['vx'] - bg_ref.CAM_CX + ACT_CX
    avy = info['vy'] - bg_ref.CAM_CY + ACT_CY
    stats['avx'], stats['avy'] = avx, avy

    zv = sprite_ref.load_zv(buf)

    for i, a in enumerate(actors):
        # UN SOLO CICLO PER I DUE PERCORSI, ed e' la struttura del gioco: la
        # passata di raccolta mette corpi e sprite nella STESSA lista e li
        # distingue con TYPE_OBJ_3D / TYPE_OBJ_SPRITE (OBJECT.C:2646). Due
        # cicli separati vorrebbero dire due ordini di disegno.
        is_spr = bool(a.flags & SPRITE_3D)
        if is_spr:
            if a.sprite < 0:
                continue
        elif a.body < 0:
            continue
        # INVISIBLE, la seconda meta' dello stesso test. Vale per TUTTI E DUE i
        # percorsi: per uno sprite il campo Body e' l'indice dello sprite
        # (InitSprite, OBJECT.C:1120), quindi la condizione unica del gioco
        # `(Body != -1) AND !(Flags & INVISIBLE)` copre anche lui. Sono le 79
        # frecce dei lanciatori (vedi reference_lba_launcher_fiche).
        if a.flags & INVISIBLE:
            stats['invis'] += 1
            continue
        if is_spr:
            anchor = proj_iso(a.x, a.y, a.z, avx, avy)
            if not actor_preclip(anchor, a.flags):
                stats['clipped'] += 1
                continue
            r = sprite_ref.draw_sprite(fb, bg_ref.BG_W, bg_ref.BG_H, buf, zv,
                                       a, avx, avy, proj_iso)
            if r is None:
                stats['spr_clip'] += 1
                continue
            written, box, clip = r
            stats['spr'] += 1
            stats['spr_pixels'] += written
            if stats['spr_first'] is None:
                stats['spr_first'] = (i, a.sprite, box)
            # M3d per gli sprite (OBJECT.C:3072-3090). Due differenze dai
            # corpi: una PORTA si posiziona con AnimStep, cioe' dov'e' l'ANTA
            # adesso e non lo stipite, e nel suo ramo il gioco ha COMMENTATO il
            # `ym++` della collisione. Si copia com'e'.
            if a.flags & SPRITE_CLIP:
                # AnimStep = PosObj finche' l'animazione non c'e'
                # (InitObject, OBJECT.C:124-129).
                xm, ym, zm = bg_ref.actor_cells(a.x, a.y, a.z, 0)
            else:
                # Xmax/Zmax sono la ZV dello sprite, PtrZvExtra[num*8+3] e
                # [num*8+7] (InitSprite, OBJECT.C:1122-1130).
                zxmax = zv[a.sprite * 8 + 3]
                zzmax = zv[a.sprite * 8 + 7]
                xm, ym, zm = bg_ref.actor_cells(a.x + zxmax, a.y,
                                                a.z + zzmax, 0)
            stats['over'] += bg_ref.draw_over_brick(fb, info, xm, ym, zm,
                                                    clip, doors=True)
            continue
        payload = body_payload(buf, a.body)
        if payload is None:
            continue
        # IL PRECLIP, prima di qualsiasi lavoro sui vertici. E' anche il motivo
        # per cui l'ancora si calcola QUI e non dentro draw_actor: nel gioco
        # sono due passate, e questa e' la prima.
        anchor = proj_iso(a.x, a.y, a.z, avx, avy)
        if not actor_preclip(anchor, a.flags):
            stats['actors'].append((i, a.body, anchor, 0, 0, 0, True))
            stats['clipped'] += 1
            continue

        body = prepare_body(payload)
        # LA POSA (M4), che nel gioco e' `SetInterAnimObjet2(Frame, ptranim,
        # ptrbody)` chiamata subito prima di AffObjetIso (OBJECT.C:3005).
        a.pose = actor_pose(buf, a, step_i, len(body['groups']))
        d, sk, px, anchor, tot, live, ent = draw_actor(fb, body, a, light,
                                                       avx, avy, g, sh, od)
        if d:
            stats['drawn'] += 1
        stats['polys'] += d
        stats['pixels'] += px
        stats['tot'] += tot
        stats['live'] += live
        stats['ent_lines'] += ent['lines']
        stats['ent_spheres'] += ent['spheres']
        stats['ent_pixels'] += ent['pixels']
        if stats['ent_first'] is None and ent['first'] is not None:
            stats['ent_first'] = ent['first']

        # M3d: LE TESSERE DAVANTI, SOPRA DI LUI.
        #
        # Va QUI e non dopo il ciclo: il gioco lo fa attore per attore
        # (OBJECT.C:3023), subito dopo averlo disegnato. Farlo alla fine
        # rimetterebbe le tessere sopra TUTTI gli attori, e quel che si
        # vedrebbe non e' "un personaggio dietro un muro" ma un personaggio
        # dietro il muro di un altro.
        #
        # `Col` e' la cella di collisione, che il gioco aggiorna a ogni
        # fotogramma: qui il fotogramma e' uno solo e non c'e' collisione,
        # quindi vale zero e il `ym++` non scatta mai. E' un buco SIMMETRICO,
        # dichiarato, e sparira' da se' quando ci sara' la collisione.
        rect = bg_ref.clip_rect(*ent['box'])
        if rect is not None:
            xm, ym, zm = bg_ref.actor_cells(a.x, a.y, a.z, 0)
            stats['over'] += bg_ref.draw_over_brick(fb, info, xm, ym, zm, rect)

        stats['actors'].append((i, a.body, anchor, d, sk, px, False))

    return fb, info, stats


def gen_expect(fb, info, stats):
    s1, s2 = bg_ref.fletcher(fb)
    minx, miny, maxx, maxy, nz = bg_ref.bbox(fb)
    lx, ly, lz = stats['light']
    # Il PRIMO attore con un corpo, scartato dal preclip o no: e' la stessa
    # regola del lato Jaguar, dove la bandiera si alza prima del taglio. Se
    # dipendesse dai poligoni usciti, la sonda descriverebbe l'ultimo attore
    # invece del primo (la ragione sta in engine_c/body_draw.c).
    first = (stats['actors'][0] if stats['actors']
             else (0, 0, (0, 0), 0, 0, 0, True))
    spr_first = stats['spr_first']
    return [
        ('LIGHT', ((lx & 0xFF) << 16) | ((ly & 0xFF) << 8) | (lz & 0xFF),
         'vettore luce X|Y|Z, un byte per asse'),
        ('AVXY', ((stats['avx'] & 0xFFFF) << 16) | (stats['avy'] & 0xFFFF),
         'offset di schermo degli ATTORI (non quello delle tessere)'),
        ('GEOM1', stats['g'].s1, 'Fletcher s1 dei vertici proiettati'),
        ('GEOM2', stats['g'].s2, 'Fletcher s2 dei vertici proiettati'),
        ('SHADE1', stats['sh'].s1, 'Fletcher s1 delle intensita\' per normale'),
        ('SHADE2', stats['sh'].s2, 'Fletcher s2 delle intensita\''),
        # `tot` e' TotalEntite (poligoni + linee + sfere) e `live` sono le voci
        # entrate in List_Tri: da S37 comprendono linee e sfere, che la faccia
        # nascosta non prova nemmeno. Se questa sonda NON si muove aggiungendo
        # le entita', vuol dire che sono finite in una lista a parte.
        ('CULL', ((stats['tot'] & 0xFFFF) << 16) | (stats['live'] & 0xFFFF),
         'entita\' totali | entrate nella lista ordinata'),
        ('ORDER1', stats['od'].s1, 'Fletcher s1 di (chiave, indice) in ordine'),
        ('ORDER2', stats['od'].s2, 'Fletcher s2 dell\'ordine di disegno'),
        ('DRAWN', ((stats['drawn'] & 0xFFFF) << 16) | (stats['polys'] & 0xFFFF),
         'attori disegnati | poligoni disegnati'),
        ('PIXELS', stats['pixels'], 'pixel scritti dai corpi'),
        ('FIRST', ((first[0] & 0xFF) << 24) | ((first[1] & 0xFF) << 16)
         | (first[3] & 0xFFFF),
         'primo attore: n | slot | poligoni'),
        ('FIRST_XY', ((first[2][0] & 0xFFFF) << 16) | (first[2][1] & 0xFFFF),
         'ancora a schermo di quell\'attore'),
        # Le tre sonde degli SPRITE (M3e), scelte per dare tre diagnosi
        # diverse invece di tre volte la stessa: quante ne passano il
        # ritaglio, DOVE finisce il primo (cioe' la tabella PtrZvExtra e la
        # proiezione), e quanti pixel escono (cioe' l'RLE e il rettangolo
        # fisso della porta). Una somma sola direbbe soltanto "no".
        ('SPR', ((stats['spr'] & 0xFFFF) << 16) | (stats['spr_clip'] & 0xFFFF),
         'sprite disegnati | scartati dal ritaglio'),
        ('SPR_XY', (((spr_first[2][0] if spr_first else 0) & 0xFFFF) << 16)
         | ((spr_first[2][1] if spr_first else 0) & 0xFFFF),
         'angolo alto-sinistro del primo sprite disegnato'),
        ('SPR_PIX', stats['spr_pixels'], 'pixel scritti dagli sprite'),
        # Le tre sonde di LINEE e SFERE (S37), sullo stesso schema: tre
        # diagnosi diverse, non tre volte la stessa.
        #   ENT      quante entrano nella lista       -> il parser delle due
        #            sezioni in coda ai poligoni
        #   ENT_XY   dove cade il centro della prima sfera -> il vertice giusto
        #            e la proiezione
        #   ENT_PIX  raggio a schermo | pixel usciti  -> la regola (r*34)>>9,
        #            il cerchio di Bresenham e il ritaglio di Line_A
        ('ENT', ((stats['ent_lines'] & 0xFFFF) << 16)
         | (stats['ent_spheres'] & 0xFFFF),
         'linee | sfere entrate nella lista'),
        ('ENT_XY', (((stats['ent_first'][0] if stats['ent_first'] else 0)
                     & 0xFFFF) << 16)
         | ((stats['ent_first'][1] if stats['ent_first'] else 0) & 0xFFFF),
         'centro a schermo della prima sfera'),
        ('ENT_PIX', (((stats['ent_first'][2] if stats['ent_first'] else 0)
                      & 0xFF) << 24)
         | (stats['ent_pixels'] & 0xFFFFFF),
         'raggio a schermo di quella sfera | pixel di linee e sfere'),
        ('BBOX_MIN', ((minx & 0xFFFF) << 16) | (miny & 0xFFFF),
         'riquadro del composto: minx | miny'),
        ('BBOX_MAX', ((maxx & 0xFFFF) << 16) | (maxy & 0xFFFF),
         'riquadro: maxx | maxy'),
        ('NONZERO', nz, 'pixel non trasparenti del composto'),
        ('SUM1', s1, 'Fletcher s1 del composto'),
        ('SUM2', s2, 'Fletcher s2 del composto'),
    ]


def gen_expect_file(rows):
    names = [n for n, _v, _l in rows[0][1]]
    out = ['/* GENERATO DA tools/body_ref.py - NON MODIFICARE A MANO.\n',
           ' *\n',
           ' * Attese di M3b/M3c: il fondo CON SOPRA i corpi degli attori.\n',
           ' * Sono misurate sul composto, non sui soli corpi, perche\' e\'\n',
           ' * quello che il Jaguar ha in memoria: un framebuffer solo.\n',
           ' * Le sonde del fondo (bg_expect.h) restano valide e si misurano\n',
           ' * PRIMA di disegnare i corpi, cosi\' un fondo giusto con i corpi\n',
           ' * sbagliati si distingue da un fondo gia\' sbagliato.\n',
           ' */\n',
           '#ifndef JAG_BODY_EXPECT_H\n#define JAG_BODY_EXPECT_H\n\n']
    for i, n in enumerate(names):
        out.append('#define BD_%-10s %d\n' % (n, i))
    out.append('\n#define BD_EXPECT_COUNT %d\n' % len(names))
    out.append('#define BD_EXPECT_ALL   0x%03XUL\n' % ((1 << len(names)) - 1))
    out.append('#define BD_EXPECT_STEPS %d\n\n' % len(rows))
    out.append('static const unsigned long BodyExpect[BD_EXPECT_STEPS]'
               '[BD_EXPECT_COUNT] = {\n')
    for desc, r in rows:
        out.append('    { /* %s */\n' % desc)
        for name, value, label in r:
            out.append('        0x%08XUL,  /* %-9s %s */\n'
                       % (value & 0xFFFFFFFF, name, label))
        out.append('    },\n')
    out.append('};\n\n')

    # LA POSA DEL PASSO (M4). Le attese qui sopra sono calcolate CON questa
    # posa: se il Jaguar ne assumesse un'altra, ogni sonda della geometria
    # cadrebbe senza dire perche'. Quindi la tabella non si ricopia a mano nel C
    # - esce da qui, dallo stesso posto che ha generato le attese.
    out.append('/* La posa che ogni passo assegna agli attori: fotogramma\n'
               ' * d\'arrivo e passo dell\'interpolazione. Il fotogramma di\n'
               ' * partenza e\' quello prima, MODULO la lunghezza\n'
               ' * dell\'animazione di ciascuno. I tre passi coprono i tre rami\n'
               ' * (fotogramma arrivato / passo zero / meta\' strada): vedi\n'
               ' * ANIM_STEPS in tools/scene_parse.py. */\n')
    out.append('#define BD_ANIM_STEPS %d\n\n' % len(rows))
    out.append('static const unsigned short BodyAnimStep[BD_ANIM_STEPS][2] = {\n')
    for i in range(len(rows)):
        frame, cur = scene_parse.anim_step_params(i)
        out.append('    { %4d, %4d },\n' % (frame, cur))
    out.append('};\n\n#endif\n')
    return ''.join(out)


def gen_sintab_file():
    """La tabella dei seni per il Jaguar, presa da P_SINTAB.C.

    Generata invece che ricopiata a mano per la stessa ragione di tutto il
    resto: 1024 numeri copiati sono 1024 occasioni di sbagliarne uno, e uno
    sbagliato darebbe un corpo storto di poco in una posa sola. lba_trigo
    controlla gia' che la REGOLA `round(sin(i*2pi/1024)*16384)` li riproduca
    tutti e 1024, quindi qui non c'e' nemmeno una tabella di cui fidarsi: c'e'
    una regola verificata contro il sorgente del gioco."""
    from lba_trigo import P_SinTab, SINTAB_MISMATCH, ONE, FIX, ANGLE_FULL
    if SINTAB_MISMATCH:
        raise SystemExit('P_SINTAB.C non e\' riprodotta dalla regola: %d valori'
                         % len(SINTAB_MISMATCH))
    out = ['/* GENERATO DA tools/body_ref.py - NON MODIFICARE A MANO.\n',
           ' *\n',
           ' * P_SINTAB.C del gioco, 1024 valori in Q%d (ampiezza %d).\n' % (FIX, ONE),
           ' * 1024 = giro intero; il coseno e\' seno(a + 256).\n',
           ' * Il passo di `Rot` e `RotMatIndex2` e\' >>%d - i commenti del\n' % FIX,
           ' * gioco dicono >>15, le istruzioni dicono `sar 14`, vince il codice.\n',
           ' */\n',
           '#ifndef JAG_SINTAB_H\n#define JAG_SINTAB_H\n\n',
           '#define LBA_ANGLE_FULL    %d\n' % ANGLE_FULL,
           '#define LBA_ANGLE_QUARTER %d\n' % (ANGLE_FULL // 4),
           '/* M4: PatchInterAngle interpola gli angoli per la via CORTA, e i\n'
           ' * due numeri che le servono sono il giro meno uno e mezzo giro. */\n',
           '#define LBA_ANGLE_MASK    %d\n' % (ANGLE_FULL - 1),
           '#define LBA_ANGLE_HALF    %d\n' % (ANGLE_FULL // 2),
           '#define LBA_TRIG_FIX      %d\n' % FIX,
           '#define LBA_TRIG_ONE      %d\n\n' % ONE,
           'static const short P_SinTab[LBA_ANGLE_FULL] = {\n']
    for i in range(0, ANGLE_FULL, 8):
        out.append('    ' + ', '.join('%6d' % v for v in P_SinTab[i:i + 8]) + ',\n')
    out.append('};\n\n#endif\n')
    return ''.join(out)


def check_limits(buf):
    """I quattro numeri che dimensionano BODY_WORK, ricontati sull'archivio.

    Sono in tools/memory_map.py perche' e' quel file a garantire che ci stiano
    nella regione; sono ricontati QUI perche' una misura fatta una volta e poi
    creduta per sempre e' un'assunzione travestita da misura."""
    from memory_map import (BODY_MAX_VERTS, BODY_MAX_NORMALS, BODY_MAX_POLYS,
                            BODY_MAX_CORNERS)
    ents = scene_parse.container_entries(buf, scene_parse.HQR_BODY)
    mv = mn = mp = mc = 0
    for slot in range(len(ents)):
        payload = body_payload(buf, slot)
        if not payload:
            continue
        b = prepare_body(payload)
        mv = max(mv, len(b['verts']))
        mn = max(mn, len(b['normals']))
        mp = max(mp, len(b['polys']))
        for p in b['polys']:
            mc = max(mc, len(p['corners']))
    rows = (('vertici', mv, BODY_MAX_VERTS), ('normali', mn, BODY_MAX_NORMALS),
            ('poligoni', mp, BODY_MAX_POLYS), ('angoli', mc, BODY_MAX_CORNERS))
    bad = [r for r in rows if r[1] > r[2]]
    for name, got, cap in rows:
        print('  %-9s misurati %4d   dichiarati %4d   %s'
              % (name, got, cap, 'SFORA' if got > cap else 'ok'))
    if bad:
        raise SystemExit('memory_map.py: BODY_MAX_* troppo bassi, BODY_WORK '
                         'verrebbe scritta oltre')
    print('  %d voci di BODY.HQR percorse, nessuna sfora' % len(ents))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--step', type=int, default=None,
                    help='solo questo passo, senza scrivere le attese')
    ap.add_argument('--limits', action='store_true',
                    help='ricontrolla BODY_MAX_* contro tutto BODY.HQR ed esci')
    ap.add_argument('--gog', default=bg_ref.GOG_COMMON)
    args = ap.parse_args()

    common = bg_ref.load_common(args.gog)
    from render_slide import normalize_palette
    from hqr_decompress import extract_entry
    ress, ress_off = common['ress']
    palette = normalize_palette(extract_entry(ress, 0, ress_off))

    buf = scene_parse.load_all()[0]
    if args.limits:
        check_limits(buf)
        return

    # L'INDICE del passo viaggia col passo. `--step N` ne isola uno, e se
    # l'indice si perdesse qui quel passo verrebbe reso con la posa del passo 0:
    # cioe' `--step 2` direbbe numeri diversi da quelli che lo stesso passo da'
    # in una passata completa.
    steps = list(enumerate(scene_parse.walk_steps(0)))
    if args.step is not None:
        steps = [steps[args.step]]

    rows = []
    zero_before = ZERO_PIX[0]
    for step_i, (num, enter) in steps:
        fb, info, stats = render_step(buf, common, num, enter, step_i)
        how = 'iniziale' if enter is None else 'ingresso da change-cube'
        print('scena %d (%s): luce (%d,%d,%d) da alpha/beta %d/%d'
              % (num, how, stats['light'][0], stats['light'][1],
                 stats['light'][2], stats['alpha'], stats['beta']))
        print('  %d attori disegnati, %d poligoni, %d pixel, '
              '%d scartati dal preclip, %d INVISIBLE'
              % (stats['drawn'], stats['polys'], stats['pixels'],
                 stats['clipped'], stats['invis']))
        for (i, slot, anchor, d, sk, px, clipped) in stats['actors']:
            if clipped:
                print('    attore %2d  BODY %3d  ancora (%4d,%4d)  '
                      'PRECLIP: fuori dalla finestra'
                      % (i, slot, anchor[0], anchor[1]))
                continue
            print('    attore %2d  BODY %3d  ancora (%4d,%4d)  '
                  '%3d poligoni disegnati, %3d fuori, %6d pixel'
                  % (i, slot, anchor[0], anchor[1], d, sk, px))

        png = os.path.join(REPO, 'build', 'body_ref_scene%03d.png' % num)
        bg_ref.write_preview(png, fb, palette)
        print('  scritto %s' % os.path.relpath(png, REPO))

        r = gen_expect(fb, info, stats)
        for name, value, label in r:
            print('    %-9s %08X  %s' % (name, value & 0xFFFFFFFF, label))
        rows.append(('scena %d, %s' % (num, how), r))

    # LA SORVEGLIANZA DELL'INDICE ZERO (S47). Il TRANS dell'OP cancella i
    # pixel a zero: un corpo che ne rendesse uno si bucherebbe da solo una
    # volta diventato sprite. Misurato zero su tutti i passi; se un giorno
    # smette di esserlo, la cura e' l'alias del nero (indice 31, che nella
    # CLUT di LBA1 e' gia' identico a CLUT[0]).
    zpix = ZERO_PIX[0] - zero_before
    print('  pixel a INDICE 0 resi dai corpi: %d  -> %s'
          % (zpix, 'ok, il TRANS non ha niente da cancellare' if zpix == 0
             else 'ATTENZIONE: da sprite si bucherebbero, serve l\'alias 31'))

    if args.step is not None:
        print('  (attese NON scritte: passo singolo)')
        return
    hdr = os.path.join(REPO, 'engine_c', 'body_expect.h')
    with open(hdr, 'w', newline='\n') as f:
        f.write(gen_expect_file(rows))
    print('  scritto engine_c/body_expect.h (%d passi)' % len(rows))

    sin = os.path.join(REPO, 'engine_c', 'sintab.h')
    with open(sin, 'w', newline='\n') as f:
        f.write(gen_sintab_file())
    print('  scritto engine_c/sintab.h')


if __name__ == '__main__':
    main()
