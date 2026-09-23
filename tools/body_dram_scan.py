"""body_dram_scan.py - I CORPI SONO GIA' IN DRAM. Questo file lo dimostra.

COM'E' NATO E COM'E' FINITO
---------------------------
Nasce per la voce 5 del §4 di docs/todo_s50.md - "corpi in DRAM invece che letti
dalla cartuccia" - che viene da un dato di S41: due terzi del quadro erano
LEGGERE il corpo, non farci l'aritmetica sopra.

**La voce era gia' chiusa e nessuno se n'era accorto.** L'archivio BODY e'
impacchettato con ARCH_FLAG_LZSS, quindi `HQR_Get(HQR_BODY, ...)` non torna un
puntatore in cartuccia: passa da `expand_entry`, che decomprime in `LoadMalloc-
Phrase`, cioe' nell'arena per-cubo in DRAM, allineata alla phrase e messa in
cache (engine_c/hal_hqr.c:236-272). `PtrBody[]` punta li'. Il trasloco e'
arrivato di rimbalzo dalla compressione, e l'allineamento a phrase c'era gia'
apposta per la GPU.

Il controllo resta qui e si rifa' a ogni lancio, perche' il giorno che qualcuno
impacchettasse BODY senza compressione i corpi tornerebbero in cartuccia SENZA
CHE NIENTE SI ROMPA - solo piu' lento, che e' il modo peggiore di regredire.

Quel che il file misura resta utile per il round-robin fra attori: quanti byte
di corpo si toccano per quadro e quanti ne vuole una stanza.

USO
    python tools/body_dram_scan.py
"""
import argparse
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bg_ref
import body_ref
import scene_parse


# Quel che resta libero in DRAM: l'heap del blob C, cioe' da __bss_end alla
# fine di C_ARENA (che confina con CELL_LIST). Il numero si RILEGGE dal .sym
# invece di scriverlo qui, o il giorno che la BSS cresce questo file mentirebbe.
def heap_free():
    sym = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       '..', 'engine_c', 'engine_blob.sym')
    end = None
    try:
        with open(sym, encoding='utf-8', errors='replace') as f:
            for line in f:
                if line.rstrip().endswith(' __bss_end'):
                    end = int(line.split()[0], 16)
    except OSError:
        return None
    if end is None:
        return None
    return 0x1C9000 - end, end


def body_counts(payload):
    """Le taglie del corpo con i PARSER GIA' VALIDATI del riferimento, non con
    un terzo lettore scritto qui: `prepare_body` ne fa girare due e pretende che
    concordino sui vertici, ed e' quello che produce le sonde che tornano sul
    Jaguar. Un lettore nuovo qui avrebbe solo aggiunto un modo di sbagliare."""
    b = body_ref.prepare_body(payload)
    return dict(total=len(payload),
                verts=len(b['verts']), groups=len(b['groups']),
                normals=len(b['normals']), polys=len(b['polys']),
                lines=len(b['lines']), spheres=len(b['spheres']))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--gog', default=bg_ref.GOG_COMMON)
    ap.parse_args()

    buf = scene_parse.load_all()[0]
    scenes = scene_parse.load_all()[2]
    steps = list(enumerate(scene_parse.walk_steps(0)))

    hf = heap_free()
    if hf:
        print('heap C libero in DRAM: %d byte (%.0f KB), da %06X a 1C9000'
              % (hf[0], hf[0] / 1024.0, hf[1]))

    # IL CONTROLLO CHE CHIUDE LA VOCE, e che deve restare: se l'archivio BODY
    # perdesse la compressione, HQR_Get tornerebbe un puntatore in CARTUCCIA e
    # tutto continuerebbe a funzionare, solo piu' lento.
    toff, nent, fl = struct.unpack_from('>III', buf, scene_parse.HDR_SIZE
                                        + scene_parse.HQR_BODY
                                        * scene_parse.ARCH_DIR_ENTRY)
    lzss = bool(fl & 1)
    print('archivio BODY: %d voci, flags %08X -> %s'
          % (nent, fl,
             'LZSS, quindi expand_entry -> LoadMallocPhrase: I CORPI SONO IN '
             'DRAM, allineati a phrase' if lzss else
             'GREZZO: HQR_Get torna un puntatore IN CARTUCCIA (regressione!)'))
    print()

    # IL PER-QUADRO VERO. `prepare_body` nel riferimento si chiama SOLO dopo che
    # il preclip ha detto si' (render_step: proj_iso, actor_preclip, e solo
    # allora prepare_body), quindi agganciarla da' i corpi davvero letti e non
    # quelli che l'attore avrebbe se fosse in campo. Contare tutti gli attori
    # con un corpo gonfiava il numero di tre volte.
    real_prep = body_ref.prepare_body
    seen = []

    def spy(payload):
        seen.append(len(payload))
        return real_prep(payload)

    common = bg_ref.load_common(bg_ref.GOG_COMMON)
    print('%-6s %7s %8s %11s %9s %11s'
          % ('scena', 'letti', 'byte/quadro', 'per stanza', 'corpi',
             'a 7 fps'))
    print('-' * 62)
    allbodies = {}
    for step_i, (num, enter) in steps:
        actors = scene_parse.resolve_actors(buf, scenes[num], enter)
        uniq = {}
        for a in actors:
            if (a.flags & body_ref.INVISIBLE) or (a.flags & body_ref.SPRITE_3D):
                continue
            if a.body < 0:
                continue
            payload = body_ref.body_payload(buf, a.body)
            if payload is not None:
                uniq[a.body] = len(payload)
                allbodies[a.body] = len(payload)

        del seen[:]
        body_ref.prepare_body = spy
        try:
            body_ref.render_step(buf, common, num, enter, step_i)
        finally:
            body_ref.prepare_body = real_prep
        pf = sum(seen)
        print('%-6d %7d %11d %11d %9d %9d KB/s'
              % (num, len(seen), pf, sum(uniq.values()), len(uniq),
                 pf * 7 // 1024))

    print('-' * 66)
    tot = sum(allbodies.values())
    print('corpi distinti nelle tre stanze: %d, %d byte (%.1f KB)'
          % (len(allbodies), tot, tot / 1024.0))
    if allbodies:
        print('il piu\' grosso: %d byte; il piu\' piccolo: %d byte'
              % (max(allbodies.values()), min(allbodies.values())))

    # E il caso peggiore vero: TUTTI i corpi dell'archivio, che e' quel che
    # servirebbe se si volesse smettere di pensarci per sempre.
    ents = scene_parse.container_entries(buf, scene_parse.HQR_BODY)
    allsz = sum(sz for (_o, sz) in ents)
    print('TUTTO BODY.HQR in cartuccia: %d voci, %d byte (%.0f KB)'
          % (len(ents), allsz, allsz / 1024.0))
    if hf:
        print('ci starebbe INTERO nell\'heap? %s  (%.0f KB su %.0f liberi)'
              % ('SI' if allsz < hf[0] else 'no',
                 allsz / 1024.0, hf[0] / 1024.0))


if __name__ == '__main__':
    main()
