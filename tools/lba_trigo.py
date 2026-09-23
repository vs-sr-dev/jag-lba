#!/usr/bin/env python3
"""Trigonometria di LBA1: tabella dei seni, matrice di rotazione, vettore luce.

A COSA SERVE
------------
A togliere l'ultima invenzione rimasta nella pipeline dei corpi. `body_lighting.py`
illumina con un vettore scritto a mano in S14:

    DEFAULT_NORMAL_LIGHT = (-1, -1, -1) / sqrt(3)

col suo commento onesto ("world-space unit vector... generic well-distributed
illumination"). Ma la luce non e' una scelta: sta nella scena, due WORD chiamate
`AlphaLight` e `BetaLight` che `LoadScene` legge (DISKFUNC.C:81), e il gioco le
trasforma in un vettore con una regola precisa.

DA DOVE VIENE LA REGOLA
-----------------------
`SetLightVector(alpha, beta, gamma)`, LIB386/LIB_3D/P_TRIGO.ASM:466:

    lAlpha/lBeta/lGamma = i tre angoli
    RotMatW()                       -> MatriceRot = MatriceWorld ruotata
    Rot(0, 0, NORMAL_UNIT - 5)      -> NormalX/Y/ZLight

`NORMAL_UNIT` e' 64 (P_DEFINE.ASH:2), quindi la lunghezza e' **59**, con il
commento dell'autore accanto: "je sais je sais...". Non e' un errore da
correggere: e' il numero con cui sono state calcolate tutte le intensita' del
gioco.

`Rot` (P_TRIGO.ASM:502) e' un prodotto matrice-vettore con passo `sar 14`, e
`RotMatIndex2` (P_TRIGO.ASM:833) e' la costruzione della matrice. Trascritte qui
riga per riga, non riderivate.

DUE COSE CHE IL CODICE DICE E I COMMENTI NO
-------------------------------------------
1. I commenti dentro `RotMatIndex2` scrivono `>>15`; le istruzioni dicono
   `sar edi, 14`. Vince il codice: la tabella e' Q14 (ampiezza 16384) e
   qualunque altro passo darebbe una matrice non normalizzata.
2. La moltiplicazione e' a DESTRA (ogni blocco mescola due COLONNE lasciando la
   terza intatta), quindi l'ordine effettivo e'

       MatriceRot = MatriceWorld . Rx(alpha) . Rz(gamma) . Ry(beta)

   alpha attorno a X, gamma attorno a Z, beta attorno a Y - e beta si applica
   per ULTIMO anche se e' il secondo argomento.

Unita' degli angoli: 1024 = giro intero, e infatti l'indice del coseno e'
`(a + 256) & 1023`.
"""
import math
import os
import re

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# The engine's own table, from the GPL fork already in this repository
# (identical to lba1-classic-community's LIB386/LIB_3D/P_SINTAB.C).
SINTAB_C = os.path.join(REPO, 'engine_c', 'fork', 'LIB_3D', 'P_SINTAB.C')

ANGLE_FULL = 1024
ANGLE_QUARTER = 256
FIX = 14                       # sar 14
ONE = 1 << FIX                 # 16384 = 1.0
NORMAL_UNIT = 64               # P_DEFINE.ASH:2
LIGHT_LEN = NORMAL_UNIT - 5    # 59, "je sais je sais..."


def _sintab_from_source():
    """I 1024 valori come stanno nel sorgente del gioco."""
    with open(SINTAB_C, 'r', errors='replace') as f:
        text = f.read()
    body = text[text.index('{') + 1:text.rindex('}')]
    vals = [int(v) for v in re.findall(r'-?\d+', body)]
    if len(vals) != ANGLE_FULL:
        raise SystemExit('P_SINTAB.C: %d valori invece di %d'
                         % (len(vals), ANGLE_FULL))
    return vals


def _sintab_from_rule():
    """Gli stessi valori dalla regola che li ha generati."""
    return [int(round(math.sin(i * 2.0 * math.pi / ANGLE_FULL) * ONE))
            for i in range(ANGLE_FULL)]


def build_sintab(check=True):
    """La tabella, con la REGOLA confrontata alla tabella del gioco.

    Non e' pignoleria: e' la stessa forma di controllo della tabella delle
    larghezze del Blitter in S29. Se la regola riproduce i 1024 valori, allora
    l'ampiezza (16384, cioe' Q14) e il passo (1024 = giro) sono verificati e non
    assunti; se non li riproduce, vince il sorgente e lo si vede subito invece di
    scoprirlo da un corpo illuminato storto.
    """
    src = _sintab_from_source()
    if check:
        rule = _sintab_from_rule()
        bad = [i for i in range(ANGLE_FULL) if src[i] != rule[i]]
        if bad:
            return src, bad
        return src, []
    return src, []


P_SinTab, SINTAB_MISMATCH = build_sintab()


def sin_a(a):
    return P_SinTab[a & (ANGLE_FULL - 1)]


def cos_a(a):
    return P_SinTab[(a + ANGLE_QUARTER) & (ANGLE_FULL - 1)]


def identity():
    """MatriceWorld a riposo: identita' in Q14. Righe [r][c], come `Rot` la
    legge (X0 = (M00*x + M01*y + M02*z) >> 14)."""
    return [[ONE, 0, 0], [0, ONE, 0], [0, 0, ONE]]


def _sar14(v):
    """`sar` e' aritmetico e arrotonda verso -infinito, che in Python e' proprio
    lo shift a destra degli interi. Scritto a parte perche' un // 16384 NON
    sarebbe la stessa cosa per i valori negativi."""
    return v >> FIX


def rot_mat_index2(src, alpha, beta, gamma):
    """P_TRIGO.ASM:833, trascritta. Torna la matrice ruotata.

    Ogni blocco lascia una colonna intatta e ricombina le altre due: alpha
    mescola le colonne 1 e 2 (rotazione attorno a X), gamma le colonne 0 e 1
    (attorno a Z), beta le colonne 0 e 2 (attorno a Y). Un angolo a zero salta
    il proprio blocco - non e' un'ottimizzazione senza effetti, e' il motivo per
    cui il codice si porta dietro la matrice `Dummy`.
    """
    p = [row[:] for row in src]

    if alpha:
        s, c = sin_a(alpha), cos_a(alpha)
        d = [row[:] for row in p]
        for r in range(3):
            d[r][0] = p[r][0]
            d[r][1] = _sar14(c * p[r][1] + s * p[r][2])
            d[r][2] = _sar14(c * p[r][2] - s * p[r][1])
        p = d

    if gamma:
        s, c = sin_a(gamma), cos_a(gamma)
        d = [row[:] for row in p]
        for r in range(3):
            d[r][2] = p[r][2]
            d[r][0] = _sar14(c * p[r][0] + s * p[r][1])
            d[r][1] = _sar14(c * p[r][1] - s * p[r][0])
        p = d

    if beta:
        s, c = sin_a(beta), cos_a(beta)
        d = [row[:] for row in p]
        for r in range(3):
            d[r][1] = p[r][1]
            d[r][0] = _sar14(c * p[r][0] - s * p[r][2])
            d[r][2] = _sar14(c * p[r][2] + s * p[r][0])
        p = d

    return p


def rot(m, x, y, z):
    """P_TRIGO.ASM:502. Il vettore ruotato dalla matrice."""
    return (_sar14(m[0][0] * x + m[0][1] * y + m[0][2] * z),
            _sar14(m[1][0] * x + m[1][1] * y + m[1][2] * z),
            _sar14(m[2][0] * x + m[2][1] * y + m[2][2] * z))


def set_light_vector(alpha, beta, gamma=0, world=None):
    """P_TRIGO.ASM:466. (NormalXLight, NormalYLight, NormalZLight).

    Sono interi piccoli - il modulo vale 59 - ed e' giusto cosi': l'intensita'
    per normale e' `dot(normale ruotata, luce) / prenormalized_range`, cioe' una
    divisione INTERA (P_OB_ISO.ASM:1697), e la scala del risultato viene da qui.
    """
    m = rot_mat_index2(world or identity(), alpha, beta, gamma)
    return rot(m, 0, 0, LIGHT_LEN)


def normal_intensity(nx, ny, nz, rng, light, m=None):
    """L'intensita' di una normale, P_OB_ISO.ASM:1687-1712.

    La normale si ruota con la matrice dell'oggetto (a riposo l'identita'), poi
    il prodotto scalare con la luce, poi la divisione per il range
    prenormalizzato che la normale si porta dietro. Negativo -> 0: e' una faccia
    che guarda dall'altra parte, non un'intensita' da ribaltare.
    """
    if m is not None:
        nx, ny, nz = rot(m, nx, ny, nz)
    dot = nx * light[0] + ny * light[1] + nz * light[2]
    if dot < 0:
        return 0
    if rng == 0:
        return 0
    return dot // rng


def main():
    print('P_SinTab: %d valori, ampiezza %d (Q%d)'
          % (len(P_SinTab), max(P_SinTab), FIX))
    if SINTAB_MISMATCH:
        print('  la REGOLA non riproduce la tabella: %d valori diversi, '
              'primi %s' % (len(SINTAB_MISMATCH), SINTAB_MISMATCH[:8]))
        for i in SINTAB_MISMATCH[:5]:
            print('    [%4d] sorgente %6d  regola %6d'
                  % (i, P_SinTab[i], _sintab_from_rule()[i]))
    else:
        print('  la regola round(sin(i*2pi/1024) * 16384) li riproduce '
              'TUTTI E 1024')

    m = rot_mat_index2(identity(), 0, 0, 0)
    print('matrice a (0,0,0): %s  -> %s'
          % (m, 'identita\'' if m == identity() else 'NON identita\''))

    for a, b in ((896, 950), (776, 1026), (0, 0), (256, 0)):
        lx, ly, lz = set_light_vector(a, b)
        n = math.sqrt(lx * lx + ly * ly + lz * lz)
        print('SetLightVector(%4d, %4d, 0) -> (%4d, %4d, %4d)   modulo %.2f '
              '(atteso ~%d)' % (a, b, lx, ly, lz, n, LIGHT_LEN))


if __name__ == '__main__':
    main()
