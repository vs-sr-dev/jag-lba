#!/usr/bin/env python3
"""JagLBA (S53): le righe COM8 di un giro, lette.

    python tools/comlog_sum.py build/logs/ROM_hhmmss.log [--from SECS]

Il formato lo scrive engine_c/hal_log.c (righe S, D, R, B, E) e src/main_v6.s
(EXC). Ogni campo e' chiave=esadecimale; i tempi sono in MEZZE RIGHE (31,778 us
in NTSC, 32 us in PAL; la riga B dice quale con hz=).

Per ogni finestra (una riga S, circa un secondo): fps veri (quadri disegnati
sul tempo misurato, non sui tick), ms per quadro di ogni fase, e X, la parte
della finestra che nessuna fase ha contato. Poi la media pesata del giro.
--from scarta le finestre che cominciano prima di SECS (il boot e il menu).
"""
import sys

HL_US = {60: 31.7778, 30: 31.7778, 50: 32.0, 25: 32.0}
PHASES = 'LWGBDFP'
NAMES = {'L': 'logica', 'W': 'FrameDelay', 'G': 'sync fork', 'B': 'Bg_Show',
         'D': 'corpi', 'F': 'fade', 'P': 'riga COM8'}
STAGES = ['po', 've', 'sh', 'cu', 'so', 'em', 'gp', 'ov', 'rs']

_state = {'us': 31.7778, 'f': 0}


def parse(text):
    """'S f=0A t=3C ...' -> ('S', {'f': 10, ...}); None se non e' una riga nostra."""
    parts = text.split()
    if not parts:
        return None
    kind, kv = parts[0], {}
    for p in parts[1:]:
        if '=' in p:
            k, v = p.split('=', 1)
            try:
                kv[k] = int(v, 16)
            except ValueError:
                kv[k] = v
    return kind, kv


def actors(text):
    """'A 00:01234 08:05678' -> {0: 0x1234, 8: 0x5678} (mezze righe per attore)."""
    out = {}
    for tok in text.split()[1:]:
        if ':' in tok:
            i, v = tok.split(':', 1)
            try:
                out[int(i, 16)] = int(v, 16)
            except ValueError:
                pass
    return out


def brief(text):
    """Una traduzione corta, per comlog.py mentre il giro scorre."""
    r = parse(text)
    if not r:
        return ''
    kind, kv = r
    if kind == 'B' and 'hz' in kv:
        _state['us'] = HL_US.get(kv['hz'], 31.7778)
        gs = kv.get('gs', -1)
        return ('GameDrive ok' if gs == 0 else f'GD_STATE={gs}') + \
            f", {kv['hz']} interrupt/s, cal {'OK' if kv.get('cal') == 0x0F0F5A5A else 'SBAGLIATA'}"
    if kind == 'S' and 'A' in kv:
        us = _state['us']
        f, a = kv.get('f', 0), kv['A']
        _state['f'] = f
        secs = a * us / 1e6
        fps = f / secs if secs else 0
        if f:
            ph = ' '.join(f"{p} {kv.get(p, 0) * us / 1000 / f:.1f}" for p in PHASES if kv.get(p))
            per = f'ms/quadro: {ph}'
        else:
            per = 'nessun quadro (menu/dialogo)'
        return (f'{fps:.2f} fps  {per}  X {100 * kv.get("X", 0) / a if a else 0:.1f}%'
                f"  dr={kv.get('dr', 0)} to={kv.get('to', 0)} h={kv.get('h', 0)}")
    if kind == 'D' and _state['f']:
        us, f = _state['us'], _state['f']
        return 'corpi ms/quadro: ' + ' '.join(
            f'{s} {kv.get(s, 0) * us / 1000 / f:.1f}' for s in STAGES if kv.get(s))
    if kind == 'A' and _state['f']:
        us, f = _state['us'], _state['f']
        return 'attori ms/quadro: ' + ' '.join(
            f'#{i} {v * us / 1000 / f:.1f}' for i, v in sorted(actors(text).items()))
    if kind == 'E':
        return 'TheEnd!'
    if kind == 'EXC':
        return 'ECCEZIONE 68000'
    return ''


def main(argv):
    if not argv:
        sys.exit(__doc__)
    path, start = argv[0], 0.0
    if '--from' in argv:
        start = float(argv[argv.index('--from') + 1])
    rows, last_d, us = [], {}, 31.7778
    for line in open(path, encoding='latin-1'):
        line = line.rstrip('\n')
        try:
            t, text = float(line[:8]), line[9:]
        except ValueError:
            continue
        r = parse(text)
        if not r:
            continue
        kind, kv = r
        if kind == 'B' and 'hz' in kv:
            us = HL_US.get(kv['hz'], us)
            print(f'{t:7.2f}  {text}')
        elif kind == 'S' and t >= start:
            rows.append((t, kv, {}))
        elif kind == 'D' and rows and t >= start:
            rows[-1][2].update(kv)
        elif kind == 'A' and rows and t >= start:
            rows[-1][2]['_act'] = actors(text)
        elif kind in ('E', 'EXC'):
            print(f'{t:7.2f}  {text}')

    if not rows:
        print('nessuna riga S')
        return
    hdr = f"{'t':>7} {'fps':>6} " + ' '.join(f'{p:>6}' for p in PHASES) + f" {'X%':>5}  c  h dr to"
    print('\nms per quadro, finestra per finestra')
    print(hdr)
    tot = {p: 0 for p in PHASES}
    tot_st = {s: 0 for s in STAGES}
    tf = ta = tx = 0
    for t, kv, d in rows:
        f, a = kv.get('f', 0), kv.get('A', 0)
        secs = a * us / 1e6
        fps = f / secs if secs else 0
        cells = ' '.join(f"{kv.get(p, 0) * us / 1000 / f:6.1f}" if f else f"{'-':>6}" for p in PHASES)
        print(f"{t:7.2f} {fps:6.2f} {cells} {100 * kv.get('X', 0) / a if a else 0:5.1f}"
              f"  {kv.get('c', 0):X} {kv.get('h', 0)} {kv.get('dr', 0)} {kv.get('to', 0)}")
        if f:
            tf += f
            ta += a
            tx += kv.get('X', 0)
            for p in PHASES:
                tot[p] += kv.get(p, 0)
            for s in STAGES:
                tot_st[s] += d.get(s, 0)
    if not tf:
        print('\nnessun quadro disegnato nelle finestre scelte')
        return
    secs = ta * us / 1e6
    print(f'\nGIRO (solo finestre con quadri): {tf} quadri in {secs:.1f} s = {tf / secs:.2f} fps, '
          f'{1000 * secs / tf:.1f} ms a quadro')
    for p in PHASES:
        ms = tot[p] * us / 1000 / tf
        print(f'  {p} {NAMES[p]:<11} {ms:7.1f} ms  {100 * tot[p] / ta:5.1f}%')
    print(f'  X non contato   {tx * us / 1000 / tf:7.1f} ms  {100 * tx / ta:5.1f}%')
    print('  dentro D (corpi), per quadro:')
    for s in STAGES:
        ms = tot_st[s] * us / 1000 / tf
        print(f'      {s} {ms:7.2f} ms  {100 * tot_st[s] / ta:5.1f}%')
    act = {}
    for t, kv, d in rows:
        f = kv.get('f', 0)
        if f and kv.get('B', 0) < 3000 and kv.get('F', 0) < 3000:
            for i, v in d.get('_act', {}).items():
                act.setdefault(i, []).append(v * us / 1000 / f)
    if act:
        import statistics
        print('  per attore, ms a quadro (mediana / massimo sulle finestre senza ridisegni):')
        for i in sorted(act):
            print(f'      #{i:<3} {statistics.median(act[i]):7.1f} {max(act[i]):7.1f}   ({len(act[i])} finestre)')


if __name__ == '__main__':
    main(sys.argv[1:])
