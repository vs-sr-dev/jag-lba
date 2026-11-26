#!/usr/bin/env python3
"""JagLBA (S53): il profiler a campionamento, sommato per funzione.

    python tools/prof_sum.py build/logs/ROM_hhmmss.log [--from SECS] [--top N]

ViHandler (src/main_v6.s) conta il PC interrotto in cassetti da 64 byte su tutto
il codice in cartuccia, da ROM_CODE ($802000) alla fine di ROM_CBLOB ($842000),
e da S53 (fase 1) anche i primi 64 KB di C_ARENA, dove sta la .fast (indici da $1000);
engine_c/hal_log.c li scarica come righe "R iii:nn ..." (indice del cassetto e
conteggio, esadecimali). Qui si sommano su tutto il giro e si attribuiscono:

  - $812000 in su: la funzione C che contiene l'INIZIO del cassetto, da
    engine_c/engine_blob.sym (lo stesso blob della ROM: rifare la build cambia
    gli indirizzi, quindi il log va letto con il .sym della sua build);
  - sotto: l'etichetta RMAC precedente in build/main_v6.lst.

Un cassetto a cavallo di due funzioni va tutto alla prima: con 64 byte e
funzioni calde lunghe e' un errore piccolo, ma per le funzioni corte guardare
anche la colonna dei cassetti.

Il campionamento e' al VI, quindi agganciato alla fase del video: dice DOVE sta
il 68000, non quanto ci mette (per quello le righe S e D in mezze righe).
"""
import bisect
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROM_CODE, ROM_CBLOB = 0x802000, 0x812000
C_ARENA = 0x153000          # S53 fase 1: la .fast in DRAM, indici da $1000
RAM_IDX = 0x1000
BIN = 64


def c_symbols():
    syms = []
    for line in open(os.path.join(REPO, 'engine_c', 'engine_blob.sym')):
        p = line.split()
        if len(p) == 3 and p[1] in 'TtWw':
            syms.append((int(p[0], 16), p[2]))
    syms.sort()
    return syms


def rmac_symbols():
    # Il listato di RMAC: numero di riga, indirizzo (scostamento da ROM_CODE),
    # byte, e il sorgente dalla colonna 40. Un'etichetta da sola su una riga
    # prende l'indirizzo della prima riga con codice che la segue.
    syms = []
    path = os.path.join(REPO, 'build', 'main_v6.lst')
    pending = None
    for line in open(path, encoding='latin-1'):
        m = re.match(r'([A-Za-z_][A-Za-z0-9_]*):', line[40:])
        if m:
            pending = m.group(1)
        am = re.match(r'^\s*\d+\s+([0-9A-F]{8})\s', line)
        if am and pending:
            addr = int(am.group(1), 16)
            if addr < 0x10000:
                addr += ROM_CODE
            if ROM_CODE <= addr < ROM_CBLOB:
                syms.append((addr, pending))
            pending = None
    syms.sort()
    return syms


def owner(syms, keys, addr):
    i = bisect.bisect_right(keys, addr) - 1
    return syms[i][1] if i >= 0 else f'${addr:06X}'


def main(argv):
    if not argv:
        sys.exit(__doc__)
    path, start, top = argv[0], 0.0, 40
    if '--from' in argv:
        start = float(argv[argv.index('--from') + 1])
    if '--top' in argv:
        top = int(argv[argv.index('--top') + 1])
    bins, n_tot, n_out = {}, 0, 0
    for line in open(path, encoding='latin-1'):
        try:
            t, text = float(line[:8]), line[9:].strip()
        except ValueError:
            continue
        if not text.startswith('R ') or t < start:
            continue
        for item in text[2:].split():
            if item.startswith('n='):
                n_tot += int(item[2:], 16)
            elif item.startswith('o='):
                n_out += int(item[2:], 16)
            elif ':' in item:
                i, c = item.split(':')
                bins[int(i, 16)] = bins.get(int(i, 16), 0) + int(c, 16)
    total = sum(bins.values())
    if not total:
        print('nessun campione')
        return
    csyms, rsyms = c_symbols(), rmac_symbols()
    ckeys, rkeys = [s[0] for s in csyms], [s[0] for s in rsyms]
    per, nb = {}, {}
    for i, c in bins.items():
        if i >= RAM_IDX:
            a = C_ARENA + (i - RAM_IDX) * BIN
            name = owner(csyms, ckeys, a) + ' [DRAM]'
        else:
            a = ROM_CODE + i * BIN
            name = owner(csyms, ckeys, a) if a >= ROM_CBLOB else 'rmac:' + owner(rsyms, rkeys, a)
        per[name] = per.get(name, 0) + c
        nb[name] = nb.get(name, 0) + 1
    print(f'{total} campioni nei cassetti (dichiarati {n_tot}, fuori dal codice in cartuccia {n_out})')
    print(f"{'%':>6} {'camp':>6} {'cass':>4}  funzione")
    acc = 0
    for name, c in sorted(per.items(), key=lambda x: -x[1])[:top]:
        acc += c
        print(f'{100 * c / total:6.1f} {c:6d} {nb[name]:4d}  {name}   (cum {100 * acc / total:.0f}%)')


if __name__ == '__main__':
    main(sys.argv[1:])
