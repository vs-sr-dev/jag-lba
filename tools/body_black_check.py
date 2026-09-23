#!/usr/bin/env python3
"""I corpi SOLI su fondo nero: le stesse tre somme che host_body.c stampa.

A COSA SERVE
------------
Chiude un buco del banco di prova su PC. Li' le sonde del COMPOSTO
(BBOX/NONZERO/SUM1/SUM2) non si confrontano, perche' il banco non ha il fondo:
finora "il motore scrive gli stessi pixel dell'oracolo" lo diceva soltanto
BigPEmu, e le sonde di pipeline dicevano una cosa piu' debole - che i numeri
INTERMEDI coincidono.

Su fondo nero il confronto si puo' fare subito, e non e' lo stesso controllo dei
pixel contati: due disegni possono avere lo stesso NUMERO di pixel in posti
diversi. Queste tre somme cadono se anche uno solo si sposta.

USO
    python tools/body_black_check.py

    poi si confronta con la riga

        corpi <s1> <s2>  pixel <n>

    che engine_c/host_body.c stampa per ogni passo (la stampa build_v6.bat al
    punto [2/5]). Devono coincidere passo per passo.

NON STA DENTRO build_v6.bat apposta: rifa' il disegno degli attori una seconda
volta e la build lo paga a ogni giro, mentre questo controllo serve quando si
tocca il DISEGNO - linee, sfere, riempimento, ordine - non a ogni ricompilazione.

M3d NON CI ENTRA, ED E' VOLUTO. Qui si chiama `draw_actor` direttamente invece
di `render_step`, quindi il ridisegno delle tessere davanti (DrawOverBrick) non
avviene - esattamente come nel banco, dove e' uno stub perche' portarsi dietro i
pixel di tutti i brick della stanza costerebbe 576 KB per verificare un effetto
che si misura sul composto. I due lati restano confrontabili PERCHE' saltano
tutti e due la stessa cosa; se un giorno il banco imparasse a ridisegnarle,
questo file va cambiato insieme a lui.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bg_ref
import body_ref
import scene_parse
import sprite_ref


def sums(fb):
    """Le stesse tre grandezze di host_measure(), long per long e big-endian:
    e' quel che il 68000 legge, quindi si contano cosi' anche qui."""
    s1 = s2 = 0
    nz = 0
    for i in range(0, len(fb), 4):
        w = (fb[i] << 24) | (fb[i + 1] << 16) | (fb[i + 2] << 8) | fb[i + 3]
        s1 = (s1 + w) & 0xFFFFFFFF
        s2 = (s2 + s1) & 0xFFFFFFFF
        if w:
            nz += sum(1 for k in range(4) if (w >> ((3 - k) * 8)) & 0xFF)
    return s1, s2, nz


def step_black(buf, common, zv, scenes, num, enter):
    """Gli attori del passo su un quadro vuoto: gli STESSI di render_step, con
    lo stesso preclip e lo stesso ciclo unico per corpi e sprite. Il fondo si
    renderizza lo stesso perche' e' da li' che vengono vx/vy."""
    _bg, info = bg_ref.render(num, common, enter=enter)
    fb = bytearray(bg_ref.BG_W * bg_ref.BG_H)
    actors = scene_parse.resolve_actors(buf, scenes[num], enter)
    s = scenes[num]
    light = body_ref.set_light_vector(s.alpha_light, s.beta_light, 0)
    avx = info['vx'] - bg_ref.CAM_CX + body_ref.ACT_CX
    avy = info['vy'] - bg_ref.CAM_CY + body_ref.ACT_CY
    g, sh, od = body_ref.Fletch(), body_ref.Fletch(), body_ref.Fletch()

    for a in actors:
        is_spr = bool(a.flags & body_ref.SPRITE_3D)
        if is_spr:
            if a.sprite < 0:
                continue
        elif a.body < 0:
            continue
        if a.flags & body_ref.INVISIBLE:
            continue
        anchor = body_ref.proj_iso(a.x, a.y, a.z, avx, avy)
        if not body_ref.actor_preclip(anchor, a.flags):
            continue
        if is_spr:
            sprite_ref.draw_sprite(fb, bg_ref.BG_W, bg_ref.BG_H, buf, zv, a,
                                   avx, avy, body_ref.proj_iso)
            continue
        payload = body_ref.body_payload(buf, a.body)
        if payload is None:
            continue
        body_ref.draw_actor(fb, body_ref.prepare_body(payload), a, light,
                            avx, avy, g, sh, od)
    return fb


def main():
    buf = scene_parse.load_all()[0]
    scenes = scene_parse.load_all()[2]
    common = bg_ref.load_common(bg_ref.GOG_COMMON)
    zv = sprite_ref.load_zv(buf)

    print('confronta queste righe con quelle di host_body.c, passo per passo:')
    for step, (num, enter) in enumerate(scene_parse.walk_steps(0)):
        fb = step_black(buf, common, zv, scenes, num, enter)
        s1, s2, nz = sums(fb)
        print('passo %d\n  corpi %08X %08X  pixel %d' % (step, s1, s2, nz))


if __name__ == '__main__':
    main()
