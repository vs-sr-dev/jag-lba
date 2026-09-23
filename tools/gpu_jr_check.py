"""Portata dei salti corti nelle sezioni .gpu, prima di chiamare l'assemblatore.

PERCHE' ESISTE
--------------
Il `jr` della GPU TOM porta -16..+15 PAROLE, e una `movei` ne occupa TRE. Basta
infilare qualche istruzione dentro un ramo per far uscire un salto dalla sua
portata, e l'assemblatore lo dice solo con un numero di riga: nessun indizio su
quale etichetta, quanto manca, o cosa l'ha spostato.

In S33 sedici `nop` aggiunti per l'attesa della divisione hanno spinto due
salti da 4 a 20 parole. Il rimedio non era allungare il salto ma spostare
l'etichetta sul percorso comune - e per capirlo serviva sapere DI QUANTO si era
sforato, che e' esattamente quel che l'assemblatore non dice.

Costa un decimo di secondo e si mette prima del passo [4/5] di build_v6.bat.

USO
---
    python tools/gpu_jr_check.py [file.s ...]

Senza argomenti controlla src/gpu_body.s. Esce con 1 se qualcosa e' fuori
portata, cosi' si incatena nel .bat con `if errorlevel 1`.
"""
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JR_MIN, JR_MAX = -16, 15


def check(path):
    """Torna il numero di salti fuori portata, e stampa il dettaglio."""
    lines = open(path, encoding='utf-8').read().split('\n')

    # Solo dentro .gpu: nel 68000 le distanze non contano allo stesso modo.
    # Le etichette LOCALI (.nome) valgono fino alla prossima globale: si
    # registrano come "globale.nome", cosi' due scope che riusano lo stesso
    # nome non si pestano (S45: gpu_front.s ne ha parecchie).
    ingpu = False
    labels = {}
    ins = []
    w = 0
    curglob = ''
    for n, raw in enumerate(lines, 1):
        t = raw.split(';')[0].strip()
        if not t:
            continue
        low = t.lower()
        if low.startswith('.gpu'):
            ingpu = True
            continue
        if low.startswith('.68000') or low.startswith('.dsp'):
            ingpu = False
            continue
        if not ingpu:
            continue
        m = re.match(r'^(\.?[A-Za-z_][A-Za-z0-9_]*):', t)
        if m:
            name = m.group(1)
            if name.startswith('.'):
                labels[curglob + name] = w
            else:
                labels[name] = w
                curglob = name
            t = t[m.end():].strip()
            if not t:
                continue
        if t.startswith('.'):
            continue                    # direttive (.org, .assert, .if ...)
        ins.append((w, n, t, curglob))
        # movei porta un long di immediato: tre parole, non una.
        w += 3 if t.split()[0].lower() == 'movei' else 1

    bad = 0
    for wi, n, t, glob in ins:
        m = re.match(r'^jr\s+\w+\s*,\s*(\.?[A-Za-z_][A-Za-z0-9_]*)', t, re.I)
        if not m:
            continue
        tgt = m.group(1)
        key = glob + tgt if tgt.startswith('.') else tgt
        if key not in labels:
            print('  %s:%d  jr -> %s: etichetta non trovata' % (path, n, tgt))
            bad += 1
            continue
        # il salto e' relativo alla parola DOPO la casella di ritardo
        off = labels[key] - (wi + 2)
        if not (JR_MIN <= off <= JR_MAX):
            print('  %s:%d  jr -> %-24s scarto %+d parole, la portata e\' %+d..%+d'
                  % (path, n, tgt, off, JR_MIN, JR_MAX))
            print('       spostare l\'etichetta sul percorso comune, oppure'
                  ' movei + jump (rN)')
            bad += 1
    return bad, len(labels)


def main():
    args = sys.argv[1:] or [os.path.join(REPO, 'src', 'gpu_body.s'),
                            os.path.join(REPO, 'src', 'gpu_shade.s'),
                            os.path.join(REPO, 'src', 'gpu_front.s')]
    bad = 0
    for f in args:
        b, nl = check(f)
        bad += b
        if not b:
            print('%s: salti corti tutti in portata (%d etichette)'
                  % (os.path.relpath(f, REPO), nl))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
