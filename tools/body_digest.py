#!/usr/bin/env python3
"""JagLBA S43 - il corpo PRE-DIGERITO al packing ("JBD", Jaguar Body Digest).

PERCHE' ESISTE
--------------
Misurato in S41 (reference_profilo_corpi_s41): BT_POSE 19% + BT_CULL 15% + la
lettura dentro BT_EMIT = circa DUE TERZI del quadro se ne vanno a LEGGERE il
corpo, non a fare aritmetica. Tre cause, tutte nel formato su disco di LBA1:

  1. little-endian su una macchina big-endian: ogni word e' due letture di
     byte piu' shift e or, invece di una `move.w`;
  2. record a lunghezza variabile: i poligoni non si indicizzano, si
     CAMMINANO (e' il motivo per cui esisteva `woff`);
  3. indici come scostamenti in byte, da dividere per 6 a ogni angolo.

Dal S42 il vincolo e' l'ESPERIENZA, non il formato ([[feedback_optimization_
tiers]]): il corpo si ricuoce QUI, una volta, in un formato che il 68000 legge
con `move.w` e che la GPU TOM potra' scorrere a passo fisso con LOAD/LOADP.
Divergenza di LIVELLO 1: stessi valori, disposizione diversa - le 24 sonde del
banco su PC non si toccano e dicono si'/no a ogni build.

La nota storica di engine_c/le.h ("lo swap offline avrebbe richiesto un parser
per ogni formato e non avrebbe risolto l'allineamento") resta vera per gli
ALTRI archivi: qui il parser c'era gia' (render_body.py, l'oracolo) e
l'allineamento si risolve proprio perche' il formato e' NOSTRO: tutti i campi
a offset pari, tutti gli array a passo fisso.

IL FORMATO, tutto BIG-ENDIAN, tutti gli offset dall'inizio del blob
-------------------------------------------------------------------
Intestazione, 48 byte:

    +0   UW  magic 0x4A44 ('JD')      il reader si ferma se non la trova
    +2   UW  nb_points
    +4   UW  nb_groups                0 = corpo rigido (era flags & INFO_ANIM)
    +6   UW  nb_normals
    +8   UW  nb_polys
    +10  UW  nb_lines
    +12  UW  nb_spheres
    +14  UW  off_verts
    +16  UW  off_groups
    +18  UW  off_normals              multiplo di 8: la leggera' LOADP
    +20  UW  off_polys
    +22  UW  off_corners
    +24  UW  off_lines
    +26  UW  off_spheres
    +28  6xW bbox X0,X1,Y0,Y1,Z0,Z1   (era ai byte +2..+13; serve alla ZV)
    +40  8   pad a 48

Gli array, ciascuno allineato a 8 e ciascuno a PASSO FISSO:

    verts     nb_points  x 8    x, y, z, 0        (il pad rende il record
                                                   una phrase: LOADP futuro)
    groups    nb_groups  x 16   start_v, nb_v, anchor_v, parent_g,
                                nb_norm, 0, 0, 0
                                (indici di VERTICE e di GRUPPO gia' divisi;
                                 parent 0xFFFF = radice, com'era nel file)
    normals   nb_normals x 8    x, y, z, rng      (la STESSA disposizione che
                                                   norm_native costruiva in
                                                   DRAM: adesso e' gia' cosi')
    polys     nb_polys   x 8    mat.b, nbp.b, coul1.b, 0,
                                flat_normal.w, corner_base.w
                                (corner_base = indice della prima voce nel
                                 vettore degli angoli, in VOCI)
    corners   sum(nbp)   x 4    normale.w, vertice.w
                                (vertice gia' diviso per 6; normale verbatim,
                                 0 dove il materiale non ce l'ha)
    lines     nb_lines   x 8    mat.b, coul1.b, coul2.b, 0, v0.w, v1.w
    spheres   nb_spheres x 8    mat.b, coul1.b, coul2.b, 0, rayon.w, v.w

Il blob e' riempito a multipli di 8. Le voci di BODY nel contenitore restano
marcate LZSS, quindi HQR_Get le decomprime nell'arena per-cubo: base allineata
alla phrase (hal_hqr.c usa LoadMallocPhrase da S43) + off_normals multiplo di
8 = le normali sono allineate per costruzione, e norm_native torna il
puntatore NUDO invece di copiare.

QUEL CHE NON SI FA QUI, APPOSTA
-------------------------------
- niente clamp: `vertice >= nb_points -> 0` resta una regola di RUNTIME del
  reader, come nel gioco. Il packer trascrive, non corregge;
- niente riordino dei poligoni: `widx` (l'indice globale che rompe i pareggi
  dell'ordinamento) deve restare quello del file;
- il reciproco di `rng` NON c'e': lo stadio dell'intensita' sta sulla GPU col
  divisore hardware, e cuocere il reciproco adesso vorrebbe dire toccare
  anche gpu_shade.s per un guadagno che il trasloco ha gia' assorbito
  ([[feedback_optimization_tiers]], il corollario).

Chi mi usa: tools/hqr_pack.py (la cartuccia) e tools/body_host_dump.py (il
banco su PC). L'oracolo tools/body_ref.py continua a leggere l'archivio
ORIGINALE di GOG: e' la sua indipendenza a rendere le sonde una prova.
"""
import struct

MAGIC = 0x4A44          # 'JD'
HDR_SIZE = 48
INFO_ANIM = 0x0002

# I tre secchi dei materiali, P_OB_ISO.ASM:330-334 (stessi valori del reader).
MAT_GOURAUD_MIN = 9
MAT_FLAT_MIN = 7


def _pad8(buf):
    while len(buf) % 8:
        buf.append(0)


def parse(payload):
    """Il formato su disco di LBA1, TUTTO, campi grezzi compresi.

    E' parse_body_full di tools/render_body.py con una differenza voluta: qui
    si tengono anche i campi che l'oracolo scarta (l'indice di normale di ogni
    angolo gouraud, la normale di faccia dei flat, coul2), perche' il digest
    deve trascrivere ogni byte che il reader C legge - non solo quelli che
    servono a renderizzare su PC.
    """
    flags = struct.unpack_from('<H', payload, 0)[0]
    bbox = struct.unpack_from('<6h', payload, 2)
    zone = struct.unpack_from('<H', payload, 14)[0]
    pos = 16 + zone

    nb_points = struct.unpack_from('<H', payload, pos)[0]
    pos += 2
    verts = []
    for _ in range(nb_points):
        verts.append(struct.unpack_from('<3h', payload, pos))
        pos += 6

    groups = []
    if flags & INFO_ANIM:
        nbg = struct.unpack_from('<H', payload, pos)[0]
        pos += 2
        for g in range(nbg):
            gp = pos + g * 38
            start_b, nbv, anchor_b, parent_b = struct.unpack_from(
                '<4H', payload, gp)
            nb_norm = struct.unpack_from('<H', payload, gp + 18)[0]
            groups.append((start_b // 6, nbv, anchor_b // 6,
                           0xFFFF if parent_b == 0xFFFF else parent_b // 38,
                           nb_norm))
        pos += nbg * 38

    nb_normals = struct.unpack_from('<H', payload, pos)[0]
    pos += 2
    normals = []
    for _ in range(nb_normals):
        normals.append(struct.unpack_from('<4h', payload, pos))
        pos += 8

    nb_polys = struct.unpack_from('<H', payload, pos)[0]
    pos += 2
    polys = []
    for _ in range(nb_polys):
        mat = payload[pos]
        nbp = payload[pos + 1]
        coul1 = payload[pos + 2]
        pos += 4
        flat_normal = 0
        corners = []
        if mat >= MAT_GOURAUD_MIN:
            for _c in range(nbp):
                nrm, vb = struct.unpack_from('<2H', payload, pos)
                corners.append((nrm, vb // 6))
                pos += 4
        elif mat >= MAT_FLAT_MIN:
            flat_normal = struct.unpack_from('<H', payload, pos)[0]
            pos += 2
            for _c in range(nbp):
                vb = struct.unpack_from('<H', payload, pos)[0]
                corners.append((0, vb // 6))
                pos += 2
        else:
            for _c in range(nbp):
                vb = struct.unpack_from('<H', payload, pos)[0]
                corners.append((0, vb // 6))
                pos += 2
        polys.append((mat, nbp, coul1, flat_normal, corners))

    lines = []
    if pos + 2 <= len(payload):
        nbl = struct.unpack_from('<H', payload, pos)[0]
        pos += 2
        for _ in range(nbl):
            if pos + 8 > len(payload):
                break
            mat, c1, c2 = payload[pos], payload[pos + 1], payload[pos + 2]
            v0, v1 = struct.unpack_from('<2H', payload, pos + 4)
            lines.append((mat, c1, c2, v0 // 6, v1 // 6))
            pos += 8

    spheres = []
    if pos + 2 <= len(payload):
        nbs = struct.unpack_from('<H', payload, pos)[0]
        pos += 2
        for _ in range(nbs):
            if pos + 8 > len(payload):
                break
            mat, c1, c2 = payload[pos], payload[pos + 1], payload[pos + 2]
            rayon, v = struct.unpack_from('<2H', payload, pos + 4)
            spheres.append((mat, c1, c2, rayon, v // 6))
            pos += 8

    return {'bbox': bbox, 'verts': verts, 'groups': groups,
            'normals': normals, 'polys': polys, 'lines': lines,
            'spheres': spheres}


def digest(payload):
    """Dal payload LBA1 al blob JBD. Byte deterministici: la deduplica del
    packer continua a vedere identici due corpi identici."""
    b = parse(payload)

    verts = bytearray()
    for x, y, z in b['verts']:
        verts += struct.pack('>3hH', x, y, z, 0)

    groups = bytearray()
    for start, nbv, anchor, parent, nnorm in b['groups']:
        groups += struct.pack('>5H3H', start, nbv, anchor, parent, nnorm,
                              0, 0, 0)

    normals = bytearray()
    for x, y, z, rng in b['normals']:
        normals += struct.pack('>4h', x, y, z, rng)

    polys = bytearray()
    corners = bytearray()
    cbase = 0
    for mat, nbp, coul1, flat_normal, cs in b['polys']:
        if cbase > 0xFFFF:
            raise SystemExit('body_digest: base degli angoli oltre 16 bit')
        polys += struct.pack('>4B2H', mat, nbp, coul1, 0, flat_normal, cbase)
        for nrm, v in cs:
            corners += struct.pack('>2H', nrm, v)
        cbase += len(cs)

    lines = bytearray()
    for mat, c1, c2, v0, v1 in b['lines']:
        lines += struct.pack('>4B2H', mat, c1, c2, 0, v0, v1)

    spheres = bytearray()
    for mat, c1, c2, rayon, v in b['spheres']:
        spheres += struct.pack('>4B2H', mat, c1, c2, 0, rayon, v)

    out = bytearray(HDR_SIZE)
    offs = []
    for arr in (verts, groups, normals, polys, corners, lines, spheres):
        _pad8(out)
        offs.append(len(out))
        out += arr
    _pad8(out)

    if len(out) > 0xFFFF:
        raise SystemExit('body_digest: blob %d byte, offset a 16 bit sforati'
                         % len(out))
    assert offs[2] % 8 == 0, 'normali non allineate alla phrase'

    struct.pack_into('>7H', out, 0, MAGIC, len(b['verts']), len(b['groups']),
                     len(b['normals']), len(b['polys']), len(b['lines']),
                     len(b['spheres']))
    struct.pack_into('>7H', out, 14, *offs)
    struct.pack_into('>6h', out, 28, *b['bbox'])
    return bytes(out)


def hqr_entry(blob, lzss):
    """Il blob incapsulato come voce HQR (intestazione LE da 10 byte), pronto
    per un archivio marcato ARCH_FLAG_LZSS. Comprime se rende, altrimenti
    ctype 0; in TUTTI e due i casi il roundtrip col decompressore vero e' la
    prova che il 68000 rileggera' questi byte ([[feedback_expected_values_in_
    the_build]]: l'attesa la calcola la build, e la build si controlla da
    sola)."""
    comp = lzss.compress_lzss(blob, 1)
    if len(comp) < len(blob):
        back = lzss.decompress_lzss(comp, len(blob), 1)
        if back != blob:
            raise SystemExit('body_digest: roundtrip LZSS ROTTO')
        return struct.pack('<IIH', len(blob), len(comp), 1) + comp
    return struct.pack('<IIH', len(blob), len(blob), 0) + blob
