#!/usr/bin/env python3
"""Il minimo indispensabile perche' engine_c/body_draw.c giri su un PC.

A COSA SERVE
------------
A provare la pipeline dei corpi PRIMA di costruire la cartuccia. Il file C che
gira sul Jaguar e quello che gira qui sono LO STESSO FILE - non una copia, non
una riscrittura: body_draw.c lascia sostituire dall'esterno le due sole basi di
memoria che tocca, e host_body.c gliele passa puntate su due array normali.

Quindi quel che si verifica qui e' il codice vero. Se le sonde tornano sull'host
e non sul Jaguar, il difetto non e' nella pipeline: e' nell'ambiente (mappa,
allineamento, ordine dei byte, cartuccia). Sono due domande diverse e questo le
separa - che e' esattamente il motivo per cui le sonde del fondo sono cinque e
non una.

COSA CONTIENE IL DUMP
---------------------
Solo quello che Body_DrawAll legge dall'esterno:

    per passo:  AlphaLight, BetaLight, vx e vy del FONDO, numero di attori
    per attore: PosObjX/Y/Z, Beta, slot BODY.HQR, e il PAYLOAD del corpo tale e
                quale sta nella cartuccia

Il payload non e' rielaborato: sono gli stessi byte che HQR_Get restituirebbe.
Un ordine dei byte sbagliato qui si vedrebbe subito, invece di confondersi con
un difetto di geometria.

USO
    python tools/body_host_dump.py        # scrive build/body_host.bin
"""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bg_ref
import body_digest
import body_ref
import scene_parse
import sprite_ref
import sprite_dump

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 'BOD2': la firma sale con il formato. Il campo delle bandiere ha cambiato la
# lunghezza di ogni record, e un dump vecchio letto con il lettore nuovo non
# darebbe un errore ma NUMERI, cioe' la peggiore delle due cose.
MAGIC = 0x424F4435          # 'BOD5' - da S43 i corpi sono blob JBD digeriti


def main():
    common = bg_ref.load_common(bg_ref.GOG_COMMON)
    buf = scene_parse.load_all()[0]
    scenes = scene_parse.load_all()[2]
    steps = scene_parse.walk_steps(0)

    # PtrZvExtra una volta sola: e' la stessa per tutto il gioco.
    zvb = sprite_ref.load_zv_raw(buf)

    body = []
    banks = {}
    # Le voci di ANIM.HQR che servono, una sola volta ciascuna: sul Jaguar
    # HQR_Get le trova in cartuccia per SLOT, e il banco deve poter rispondere
    # alla stessa domanda con lo stesso numero.
    anims = {}
    for step_i, (num, enter) in enumerate(steps):
        _fb, info = bg_ref.render(num, common, enter=enter)
        s = scenes[num]

        # TUTTI gli attori disegnabili, NELL'ORDINE DI ListObjet, corpi e
        # sprite mescolati. Tenere solo i corpi (come faceva BOD2) non era una
        # semplificazione: era il banco che non poteva vedere l'ordine di
        # disegno, cioe' proprio la cosa che il ramo nuovo tocca.
        recs = []
        for a in scene_parse.resolve_actors(buf, s, enter):
            if a.flags & sprite_ref.SPRITE_3D:
                if a.sprite < 0:
                    continue
                pay = sprite_dump.sprite_payload(buf, a.sprite)
                if pay is None:
                    continue
                banks[a.sprite] = pay
                recs.append(struct.pack('>hhhhHHhhhhh', a.x, a.y, a.z, a.beta,
                                        a.flags & 0xFFFF, 1, a.sprite,
                                        a.info[0], a.info[1], a.info[2],
                                        a.info[3]))
                continue
            if a.body < 0:
                continue
            pay = body_ref.body_payload(buf, a.body)
            if pay is None:
                continue
            # S43: nel dump viaggia il blob PRE-DIGERITO, perche' e' quello
            # che body_draw.c legge adesso - su PC come sulla cartuccia. La
            # digestione la fa lo stesso modulo del packer (body_digest.py):
            # un padrone solo del formato, come per la mappa di memoria.
            pay = body_digest.digest(pay)
            # Le BANDIERE viaggiano nel dump da S35. Prima non c'erano, e
            # finche' l'unico a leggerle era il preclip (che le usa solo per
            # SPRITE_CLIP, mai vero qui) l'assenza non si vedeva: gli attori
            # arrivavano al banco con flag zero, cioe' con la risposta giusta
            # per caso.
            # M4: l'animazione dell'attore e la posa del passo. `dest`, `src` e
            # `cur` li decide scene_parse in un posto solo - lo stesso da cui
            # esce la tabella che il Jaguar legge da body_expect.h - cosi' il
            # banco e la cartuccia assumono la STESSA posa e non due pose che si
            # somigliano.
            slot = getattr(a, 'anim', -1)
            fr = scene_parse.actor_pose_frames(buf, a, step_i)
            if fr is None:
                slot, dest, src, cur = -1, 0, 0, 0
            else:
                dest, src, cur = fr
                if slot not in anims:
                    anims[slot] = scene_parse.anim_payload(buf, slot)
            recs.append(struct.pack('>hhhhHHHhhhHI', a.x, a.y, a.z, a.beta,
                                    a.flags & 0xFFFF, 0, a.body,
                                    slot, dest, src, cur, len(pay))
                        + pay)

        body.append(struct.pack('>hhhhH', s.alpha_light, s.beta_light,
                                info['vx'], info['vy'], len(recs)))
        body.extend(recs)
        print('passo scena %d: %d attori disegnabili' % (num, len(recs)))

    out = [struct.pack('>III', MAGIC, len(steps), len(zvb)), zvb,
           struct.pack('>H', len(banks))]
    for idx in sorted(banks):
        out.append(struct.pack('>HI', idx, len(banks[idx])))
        out.append(banks[idx])
    live = {k: v for k, v in anims.items() if v is not None}
    out.append(struct.pack('>H', len(live)))
    for idx in sorted(live):
        out.append(struct.pack('>HI', idx, len(live[idx])))
        out.append(live[idx])
    out.extend(body)
    print('%d banchi di sprite, %d animazioni, %d byte di tabella ZV'
          % (len(banks), len(live), len(zvb)))

    path = os.path.join(REPO, 'build', 'body_host.bin')
    with open(path, 'wb') as f:
        for chunk in out:
            f.write(chunk)
    print('scritto %s (%d byte)'
          % (os.path.relpath(path, REPO), os.path.getsize(path)))


if __name__ == '__main__':
    main()
