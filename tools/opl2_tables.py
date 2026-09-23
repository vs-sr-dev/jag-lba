#!/usr/bin/env python3
"""Le due tabelle dell'OPL2, generate dalla regola e VERIFICATE.

PERCHE' NON SI COPIANO
----------------------
Sono 512 numeri. Copiati a mano sono 512 occasioni di sbagliarne uno, e uno
sbagliato darebbe un timbro leggermente sporco in un punto solo della scala:
il difetto piu' difficile da vedere che esista. Stessa ragione per cui
`P_SinTab` del gioco si genera invece di ricopiarla (tools/body_ref.py).

COME FUNZIONA UN OPERATORE OPL2
-------------------------------
Il chip non moltiplica mai. Lavora nel dominio LOGARITMICO: la sinusoide sta in
tabella come -log2(sin), le attenuazioni (inviluppo, total level, modulazione)
si SOMMANO invece di moltiplicarsi, e una seconda tabella riporta il risultato
in lineare. E' anche il motivo per cui l'OPL2 sta in un budget da poche decine
di istruzioni: nessuna moltiplicazione per campione.

    logsin[i] = round(-log2(sin((i+0.5) * pi/512)) * 256)      i = 0..255
    exp[i]    = round((2^(i/256) - 1) * 1024)                  i = 0..255

La tabella logsin copre un QUARTO d'onda; gli altri tre si ottengono
specchiando l'indice (bit 8 della fase) e negando l'uscita (bit 9). Il seno
completo e' quindi 1024 passi di fase con 256 voci in tabella.

La ricostruzione, che e' quel che il DSP eseguira':

    idx  = fase & 255,  specchiato se bit 8
    att  = logsin[idx] + attenuazione_totale
    mag  = (exp[(att & 255) ^ 255] | 1024) >> (att >> 8)
    out  = -mag se bit 9, altrimenti +mag

LA VERIFICA
-----------
Con attenuazione zero quella catena DEVE riprodurre una sinusoide. Non e' una
somiglianza qualitativa: si confronta campione per campione con
round(sin * ampiezza) e si stampa lo scarto massimo. Se la regola o le tabelle
fossero sbagliate, lo scarto esploderebbe. E' lo stesso metodo di lba_trigo,
dove la regola si prova contro il sorgente del gioco: qui il sorgente non c'e',
quindi la prova e' l'invariante matematico.
"""
import math
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

LOGSIN = [int(round(-math.log2(math.sin((i + 0.5) * math.pi / 512)) * 256))
          for i in range(256)]
EXP = [int(round((2.0 ** (i / 256.0) - 1.0) * 1024))
       for i in range(256)]


def opl_out(phase, att_extra=0):
    """Un campione di operatore, ESATTAMENTE come lo calcolera' il DSP."""
    idx = phase & 0xFF
    if phase & 0x100:
        idx ^= 0xFF                      # quarto d'onda specchiato
    att = LOGSIN[idx] + att_extra
    if att > 0xFFF:
        att = 0xFFF                      # silenzio: satura invece di traboccare
    mag = (EXP[(att & 0xFF) ^ 0xFF] | 0x400) >> (att >> 8)
    return -mag if (phase & 0x200) else mag


def selfcheck():
    """Con attenuazione zero la catena deve dare una sinusoide."""
    # L'ampiezza di picco della catena, misurata invece che dedotta.
    peak = max(abs(opl_out(p)) for p in range(1024))
    worst = 0
    worst_p = 0
    for p in range(1024):
        got = opl_out(p)
        want = peak * math.sin((p + 0.5) * 2.0 * math.pi / 1024.0)
        d = abs(got - want)
        if d > worst:
            worst, worst_p = d, p
    rel = worst / peak
    print('ampiezza di picco della catena: %d' % peak)
    print('scarto massimo dalla sinusoide: %.2f su %d = %.3f%%  (fase %d)'
          % (worst, peak, rel * 100.0, worst_p))
    # Un quarto d'onda in 256 passi con uscita a 10 bit non puo' essere esatto:
    # la soglia e' la quantizzazione, non lo zero.
    if rel > 0.01:
        raise SystemExit('opl2_tables: la catena NON riproduce una sinusoide')
    print('la catena riproduce la sinusoide: le tabelle sono giuste')
    return peak


def emit(peak):
    out = ['; GENERATO DA tools/opl2_tables.py - NON MODIFICARE A MANO.\n',
           ';\n',
           '; Le due tabelle dell\'operatore OPL2, in RAM locale del DSP.\n',
           '; Sono a WORD (non a long): 256+256 voci = 1 KB sugli 8 di Jerry, e\n',
           '; `loadw` le legge in un colpo. Tutti e due i valori sono senza segno,\n',
           '; quindi l\'estensione a zero di loadw e\' quella giusta.\n',
           ';\n',
           '; logsin sta su 12 bit (max %d), exp su 10 (max %d).\n'
           % (max(LOGSIN), max(EXP)),
           '; Ampiezza di picco della catena a piena scala: %d.\n' % peak,
           '\n']
    out.append('OPL_PEAK        EQU     %d\n\n' % peak)
    for name, tab in (('OplLogSin', LOGSIN), ('OplExp', EXP)):
        out.append('%s:\n' % name)
        for i in range(0, 256, 8):
            out.append('    dc.w    ' +
                       ', '.join('%5d' % v for v in tab[i:i + 8]) + '\n')
        out.append('\n')
    return ''.join(out)


def main():
    peak = selfcheck()
    path = os.path.join(REPO, 'include', 'opl2_tables.inc')
    with open(path, 'w', newline='\n') as f:
        f.write(emit(peak))
    print('scritto %s' % os.path.relpath(path, REPO))


if __name__ == '__main__':
    main()
