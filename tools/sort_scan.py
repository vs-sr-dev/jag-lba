"""sort_scan.py - QUANTO COSTA DAVVERO BT_SORT, prima di riscriverlo.

PERCHE'
-------
`docs/todo_s47.md` metteva SORT come prossimo bersaglio a 2,3 tick; S50 ha
scoperto che quel profilo era contaminato e che SORT e' ormai una frazione di
1,03 tick. Prima di riscrivere un ordinamento conviene quindi sapere su quante
voci gira, quanto lavoro fa e quanto ne farebbe un'alternativa - e si puo'
sapere tutto su PC, perche' la lista e le chiavi sono le stesse del Jaguar.

COME PRENDE I DATI, senza toccare una riga di quel che va sul Jaguar
-------------------------------------------------------------------
`body_ref.py` ordina con `sorted(ents, key=...)`: `ents` e' la lista NELL'ORDINE
IN CUI IL C LA COSTRUISCE (poligoni sopravvissuti, poi linee, poi sfere) e la
chiave e' la stessa. Che siano davvero le stesse lo dimostrano le sonde
ORDER1/ORDER2, che tornano fra banco e riferimento.

Quindi qui si mette un `sorted` nello spazio dei nomi di body_ref - i globali di
un modulo vincono sui builtin - che registra (lista, chiavi) e poi delega al
vero. Zero modifiche al riferimento, zero al motore.

USO
    python tools/sort_scan.py
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bg_ref
import body_ref
import scene_parse


# ---------------------------------------------------------------------------
# I DUE ORDINAMENTI, contati.
# ---------------------------------------------------------------------------
def shell_knuth(pairs):
    """Lo Shell sort di body_draw.c:3194, distanze di Knuth, riga per riga.

    `pairs` e' [(chiave, indice_globale)] nell'ordine di costruzione. Torna
    (confronti, spostamenti, risultato). Un confronto qui e' quel che nel C e'
    una lettura di `wlist[(j-gap)*2]` piu' il ramo; uno spostamento sono due
    long copiati."""
    a = list(pairs)
    n = len(a)
    cmps = movs = 0
    gap = 1
    while gap < n // 3:
        gap = gap * 3 + 1
    while gap >= 1:
        for k in range(gap, n):
            kk = a[k]
            j = k
            while j >= gap:
                cmps += 1
                if a[j - gap] <= kk:
                    break
                a[j] = a[j - gap]
                movs += 1
                j -= gap
            a[j] = kk
        gap //= 3
    return cmps, movs, a


def insertion(pairs):
    """L'inserzione semplice sulla stessa lista: e' lo Shell con gap 1 solo, ed
    e' il termine di paragone che dice quanto la lista arriva gia' ordinata."""
    a = list(pairs)
    cmps = movs = 0
    for k in range(1, len(a)):
        kk = a[k]
        j = k
        while j >= 1:
            cmps += 1
            if a[j - 1] <= kk:
                break
            a[j] = a[j - 1]
            movs += 1
            j -= 1
        a[j] = kk
    return cmps, movs, a


def bucket(pairs, nb):
    """Ordinamento a secchielli sulla chiave, poi UNA passata di inserzione.

    Perche' e' corretto senza aggiungere un confronto sull'indice: lo
    spargimento e' STABILE - si percorrono le voci nell'ordine in cui sono
    costruite, che e' gia' l'ordine dell'indice globale - quindi dentro un
    secchiello restano ordinate per indice. L'inserzione finale confronta la
    sola chiave e non tocca i pareggi, che e' esattamente la regola del pareggio
    che il riferimento pretende.

    Costo contato in ACCESSI: azzerare i conti, contare, somme progressive,
    spargere, e i confronti dell'inserzione. Non e' un cronometro - e' il
    numero di operazioni che il 68000 dovra' fare, che e' quel che si puo'
    sapere su PC."""
    n = len(pairs)
    if n < 2:
        return 0, list(pairs)
    ks = [p[0] for p in pairs]
    lo, hi = min(ks), max(ks)
    ops = n                                 # la passata di min/max
    shift = 0
    while (hi - lo) >> shift >= nb:
        shift += 1

    cnt = [0] * nb
    ops += nb                               # azzeramento
    for k in ks:
        cnt[(k - lo) >> shift] += 1
        ops += 1
    pos = [0] * nb
    s = 0
    for i in range(nb):                     # somme progressive
        pos[i] = s
        s += cnt[i]
        ops += 1
    out = [None] * n
    for p in pairs:                         # spargimento, stabile
        b = (p[0] - lo) >> shift
        out[pos[b]] = p
        pos[b] += 1
        ops += 1

    # L'inserzione finale: ogni voce e' gia' dentro il suo secchiello, quindi
    # non puo' spostarsi piu' in la' del secchiello stesso.
    for k in range(1, n):
        kk = out[k]
        j = k
        while j >= 1:
            ops += 1
            if out[j - 1][0] <= kk[0]:
                break
            out[j] = out[j - 1]
            ops += 1
            j -= 1
        out[j] = kk
    return ops, out


def inversions(pairs):
    """Quante coppie sono fuori ordine. E' la misura di quanto la lista arriva
    gia' ordinata, e anche il numero ESATTO di spostamenti che farebbe
    l'inserzione semplice: se e' piccolo, un algoritmo adattivo vince; se e'
    quadratico, no."""
    a = [p for p in pairs]
    n = len(a)
    if n < 2:
        return 0
    # merge sort che conta, per non fare n^2 su liste da 300
    def rec(v):
        if len(v) < 2:
            return v, 0
        m = len(v) // 2
        l, cl = rec(v[:m])
        r, cr = rec(v[m:])
        out = []
        c = cl + cr
        i = j = 0
        while i < len(l) and j < len(r):
            if l[i] <= r[j]:
                out.append(l[i])
                i += 1
            else:
                out.append(r[j])
                j += 1
                c += len(l) - i
        out.extend(l[i:])
        out.extend(r[j:])
        return out, c
    return rec(a)[1]


# ---------------------------------------------------------------------------
def record(buf, common, steps):
    """Registra ogni ordinamento che il riferimento esegue."""
    runs = []
    real = sorted

    def spy(seq, key=None, reverse=False):
        items = list(seq)
        if key is not None and len(items) > 1:
            runs.append((spy.scene, [key(i) for i in items]))
        return real(items, key=key, reverse=reverse)

    body_ref.sorted = spy
    try:
        for step_i, (num, enter) in steps:
            spy.scene = num
            body_ref.render_step(buf, common, num, enter, step_i)
    finally:
        del body_ref.sorted
    return runs


# ---------------------------------------------------------------------------
# LA COERENZA FRA QUADRI - l'idea di docs/todo_s49.md.
#
# "insertion sort su una lista quasi ordinata dal quadro precedente": la lista
# in INGRESSO non e' quasi ordinata (le inversioni qui sopra lo dicono), ma
# l'USCITA del quadro prima potrebbe esserlo per il quadro dopo, se la posa
# cambia poco. Se lo e', si riparte da quella e l'inserzione costa pochissimo.
#
# Si prova facendo avanzare l'animazione un fotogramma alla volta: si sostituisce
# `scene_parse.actor_pose_frames`, che e' il posto UNICO da cui passano i due
# fotogrammi e il cursore. Fra due fotogrammi CHIAVE il salto e' il piu' grande
# che l'animazione faccia - quindi questa e' la prova pessimista: a 7 fps i
# quadri renderizzati stanno in mezzo, e cambiano meno.
# ---------------------------------------------------------------------------
def coherence(buf, common, nframes):
    runs = []
    real_apf = scene_parse.actor_pose_frames

    def fake(b, actor, step_i):
        r = real_apf(b, actor, step_i)
        if r is None:
            return None
        nbf = scene_parse.anim_frames(b, actor.anim)
        d = fake.f % nbf
        return d, (d - 1 if d > 0 else d), 0

    real_sorted = sorted
    frames = []

    def spy(seq, key=None, reverse=False):
        items = list(seq)
        if key is not None and len(items) > 1 and spy.grab:
            frames.append([(key(i), i) for i in items])
            spy.grab = False
        return real_sorted(items, key=key, reverse=reverse)

    scene_parse.actor_pose_frames = fake
    body_ref.sorted = spy
    try:
        for f in range(nframes):
            fake.f = f
            spy.grab = True                  # solo il PRIMO attore, cioe' l'eroe
            body_ref.render_step(buf, common, 0, None, 0)
    finally:
        scene_parse.actor_pose_frames = real_apf
        del body_ref.sorted

    print('%-7s %6s %6s %6s %8s   %9s %9s'
          % ('quadro', 'voci', 'nuove', 'spar.', 'inv/voce',
             'SEME acc.', 'Shell acc.'))
    print('-' * 62)
    tot_inv = tot_n = tot_seed = tot_shell = 0
    for i in range(1, len(frames)):
        prev = [e for (_k, e) in real_sorted(frames[i - 1])]
        cur = {e: k for (k, e) in frames[i]}
        pset = set(prev)
        kept = [e for e in prev if e in cur]
        new = [(k, e) for (k, e) in frames[i] if e not in pset]
        gone = len(prev) - len(kept)
        inv = inversions([(cur[e], e) for e in kept])

        # L'ALGORITMO VERO: il seme e' l'ordine del quadro prima ripulito di
        # chi non c'e' piu', con in coda le voci NUOVE nel loro ordine di
        # indice. Poi una sola inserzione. Le voci nuove sono la parte che puo'
        # mangiarsi il vantaggio, perche' partono in fondo.
        seed = [(cur[e], e) for e in kept] + new
        sc_seed, sm_seed, out_seed = insertion(seed)
        sc, sm, out_shell = shell_knuth(frames[i])
        # LA PROPRIETA' CHE RENDE TUTTO QUESTO SICURO: il seme cambia solo il
        # COSTO. L'ordine (chiave, indice) e' totale, quindi il risultato e'
        # unico e non dipende da da dove si parte.
        if out_seed != real_sorted(frames[i]) or out_shell != out_seed:
            raise SystemExit('quadro %d: il seme cambia il risultato!' % i)

        tot_inv += inv
        tot_n += len(frames[i])
        tot_seed += sc_seed + sm_seed
        tot_shell += sc + sm
        print('%-7d %6d %6d %6d %8.1f   %9d %9d'
              % (i, len(frames[i]), len(new), gone,
                 inv / float(max(1, len(kept))), sc_seed + sm_seed, sc + sm))
    print('-' * 62)
    print('%-7s %6d %6s %6s %8.1f   %9d %9d'
          % ('TOT', tot_n, '', '', tot_inv / float(max(1, tot_n)),
             tot_seed, tot_shell))
    print()
    print('inversioni per voce fra quadri consecutivi: %.1f, contro le 40,4 '
          'della lista in ingresso' % (tot_inv / float(max(1, tot_n))))
    print('accessi per voce: seme %.1f, Shell %.1f  ->  %.1fx'
          % (tot_seed / float(tot_n), tot_shell / float(tot_n),
             tot_shell / float(max(1, tot_seed))))
    print('il risultato e\' IDENTICO in tutti i quadri: l\'ordine (chiave, '
          'indice) e\' totale, quindi il seme cambia solo il costo')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--gog', default=bg_ref.GOG_COMMON)
    ap.add_argument('--coherence', type=int, default=0, metavar='N',
                    help='fa avanzare l\'animazione N fotogrammi e misura '
                         'quanto l\'ordine del quadro prima serve a quello dopo')
    args = ap.parse_args()

    common = bg_ref.load_common(args.gog)
    buf = scene_parse.load_all()[0]
    steps = list(enumerate(scene_parse.walk_steps(0)))

    if args.coherence:
        coherence(buf, common, args.coherence)
        return

    runs = record(buf, common, steps)
    print('%d ordinamenti registrati\n' % len(runs))

    print('%-6s %6s %8s %8s   %8s %8s %9s'
          % ('scena', 'voci', 'shell_c', 'shell_m', 'inser_c', 'inser_m',
             'inversioni'))
    print('-' * 66)
    tot = dict(n=0, sc=0, sm=0, ic=0, im=0, inv=0)
    kmin, kmax = None, None
    for (scene, keys) in runs:
        sc, sm, out1 = shell_knuth(keys)
        ic, im, out2 = insertion(keys)
        if out1 != out2 or out1 != sorted(keys):
            raise SystemExit('i due ordinamenti non danno la stessa lista')
        inv = inversions(keys)
        if im != inv:
            raise SystemExit('inversioni %d != spostamenti inserzione %d'
                             % (inv, im))
        tot['n'] += len(keys)
        tot['sc'] += sc
        tot['sm'] += sm
        tot['ic'] += ic
        tot['im'] += im
        tot['inv'] += inv
        ks = [k[0] for k in keys]
        kmin = min(ks) if kmin is None else min(kmin, min(ks))
        kmax = max(ks) if kmax is None else max(kmax, max(ks))
        print('%-6d %6d %8d %8d   %8d %8d %9d'
              % (scene, len(keys), sc, sm, ic, im, inv))

    print('-' * 66)
    print('%-6s %6d %8d %8d   %8d %8d %9d'
          % ('TOT', tot['n'], tot['sc'], tot['sm'], tot['ic'], tot['im'],
             tot['inv']))
    print()
    n = tot['n']
    print('media per attore: %.0f voci, %.0f confronti e %.0f spostamenti '
          'di Shell' % (n / len(runs), tot['sc'] / len(runs),
                        tot['sm'] / len(runs)))
    print('confronti per voce: Shell %.1f, inserzione semplice %.1f'
          % (tot['sc'] / n, tot['ic'] / n))
    print('inversioni per voce: %.1f  (0 = lista gia\' ordinata, ~n/4 = '
          'sparsa a caso)' % (tot['inv'] / n))
    print('gamma delle chiavi: %d .. %d  (%d valori distinti in %d voci)'
          % (kmin, kmax, len(set(k[0] for (_s, ks) in runs for k in ks)), n))

    # Il termine di paragone: Shell contato negli STESSI accessi del secchiello
    # (un confronto e' un accesso, uno spostamento e' un accesso), o il
    # confronto non vorrebbe dire niente.
    shell_ops = tot['sc'] + tot['sm']
    print()
    print('%-10s %10s %10s %8s' % ('secchielli', 'accessi', 'per voce', 'vs Shell'))
    print('-' * 42)
    print('%-10s %10d %10.1f %8s' % ('Shell', shell_ops, shell_ops / n, '-'))
    for nb in (16, 32, 64, 128, 256):
        ops = 0
        for (_scene, keys) in runs:
            o, out = bucket(keys, nb)
            if out != sorted(keys):
                raise SystemExit('secchielli %d: lista diversa' % nb)
            ops += o
        print('%-10d %10d %10.1f %7.1fx'
              % (nb, ops, ops / n, shell_ops / float(ops)))


if __name__ == '__main__':
    main()
