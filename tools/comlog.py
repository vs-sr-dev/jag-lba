#!/usr/bin/env python3
"""JagLBA (S53): una ROM sul Jaguar vero e quel che dice su COM8.

    python tools/comlog.py run ROM.j64 [SECS]   la ROM via USB, poi ascolta (default 120 s)
    python tools/comlog.py listen [SECS]        solo ascolto (0: fino a Ctrl+C)

UN processo solo fa entrambe le cose: due "ponti" Python aperti insieme sulla
GameDrive la incastrano e serve un ciclo di accensione (JaguarDocs
metodi/gamedrive_usb.md 6.1). Prima di partire si controlla che non ce ne siano
altri vivi. Le righe vanno a schermo e in build/logs/<rom>_<hhmmss>.log, con il
tempo del PC davanti (jitter USB ~10 ms); la riga S e' tradotta al volo
(tools/comlog_sum.py fa la tabella del giro intero).

Il flusso e' quello di JaguarMI tools/comlog.py (e prima di JagSkyroads). La
ROM da 6 MB passa in ~27 s (225 KB/s misurati), poi il BIOS e il boot.
"""
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import comlog_sum  # noqa: E402


def orphans():
    """Altri processi Python che parlano con la GameDrive (regola d'oro 1)."""
    if os.name != 'nt':
        return []
    ps = ("Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match "
          "'jaggd|comlog\\.py|bench\\.py|gdlink' -and $_.ProcessId -ne %d } | "
          "ForEach-Object { \"$($_.ProcessId) $($_.CommandLine)\" }" % os.getpid())
    out = subprocess.run(['powershell', '-NoProfile', '-Command', ps],
                         capture_output=True, text=True).stdout
    return [ln for ln in out.splitlines() if ln.strip() and 'Get-CimInstance' not in ln]


def listen(secs, log_path):
    import jaggd
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    print(f'log: {os.path.relpath(log_path, REPO)}', flush=True)
    try:
        with open(log_path, 'w', encoding='latin-1') as log:
            for t, ln in jaggd.lines(secs):
                log.write(f'{t:8.3f} {ln}\n')
                log.flush()
                extra = comlog_sum.brief(ln)
                print(f'{t:7.2f}  {ln}' + (f'\n         -> {extra}' if extra else ''),
                      flush=True)
    except KeyboardInterrupt:
        pass
    print(f'log: {os.path.relpath(log_path, REPO)}')


def main(argv):
    if not argv:
        sys.exit(__doc__)
    others = orphans()
    if others:
        print('ALTRI PROCESSI SULLA GAMEDRIVE, non parto:')
        for o in others:
            print('   ', o)
        sys.exit(2)
    stamp = time.strftime('%H%M%S')
    if argv[0] == 'run':
        import jaggd
        rom = argv[1]
        secs = int(argv[2]) if len(argv) > 2 else 120
        t0 = time.time()
        jaggd.run_rom(rom)
        print(f'caricata in {time.time() - t0:.1f} s', flush=True)
        name = os.path.splitext(os.path.basename(rom))[0]
        listen(secs, os.path.join(REPO, 'build', 'logs', f'{name}_{stamp}.log'))
    elif argv[0] == 'listen':
        secs = int(argv[1]) if len(argv) > 1 else 0
        listen(secs, os.path.join(REPO, 'build', 'logs', f'listen_{stamp}.log'))
    else:
        sys.exit(__doc__)


if __name__ == '__main__':
    main(sys.argv[1:])
