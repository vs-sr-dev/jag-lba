#!/usr/bin/env python3
"""Render an LBA1 body as iso-projected wireframe PNG.

Pipeline:
  1. Parse body (header + zone + vertices + groups[anim] + normals + polys with v_indices)
  2. Iso-project each vertex (rest pose — no group transforms)
  3. Draw poly edges as Bresenham lines, color-coded by material bucket:
       white=gouraud (mat 9-10), green=flat (mat 7-8), red=pattern (mat 0-6)

Vertex indices in the file are stored as byte offsets into List_Point (stride 6),
so we divide by 6 to get vertex number.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import lba_paths
import sys, os, struct, argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hqr_decompress import parse_offsets, extract_entry
from render_brick import write_png_rgba
from lba_trigo import rot_mat_index2, rot

INFO_ANIM = 0x0002

def parse_body_full(payload):
    flags = struct.unpack('<H', payload[0:2])[0]
    bbox = struct.unpack('<6h', payload[2:14])
    zone_info_size = struct.unpack('<H', payload[14:16])[0]
    pos = 16 + zone_info_size

    nb_points = struct.unpack('<H', payload[pos:pos+2])[0]
    pos += 2
    vertices = []
    for _ in range(nb_points):
        x, y, z = struct.unpack('<3h', payload[pos:pos+6])
        vertices.append((x, y, z))
        pos += 6

    nb_groups = 0
    groups = []
    if flags & INFO_ANIM:
        nb_groups = struct.unpack('<H', payload[pos:pos+2])[0]
        pos += 2
        for g in range(nb_groups):
            gp = pos + g * 38
            start_byte   = struct.unpack('<H', payload[gp+0:gp+2])[0]
            nb_pts       = struct.unpack('<H', payload[gp+2:gp+4])[0]
            org_point_b  = struct.unpack('<H', payload[gp+4:gp+6])[0]
            org_groupe_b = struct.unpack('<H', payload[gp+6:gp+8])[0]
            # +8..+15 = la POSA del gruppo (tipo, tre valori). Nell'archivio
            # grezzo sono (0,0,0,0) in tutti e 1844 i record: un gruppo che
            # nessuna animazione pilota resta a riposo, cioe' eredita la
            # matrice del padre. Non li leggiamo da qui - il DOS ce li SCRIVE,
            # e per noi il corpo e' in cartuccia: vedi compute_groups.
            #
            # +18 = quante NORMALI appartengono al gruppo. Le normali si
            # consumano in ordine, gruppo per gruppo, e ognuna si ruota con la
            # matrice del PROPRIO gruppo (ComputeAnimNormal, P_OB_ISO.ASM:1436).
            # Verificato: la somma di questi conteggi da' esattamente il numero
            # totale di normali su tutti e 132 i corpi dell'archivio.
            nb_norm = struct.unpack('<H', payload[gp+18:gp+20])[0]
            groups.append({
                'start_v':   start_byte // 6,
                'nb_v':      nb_pts,
                'anchor_v':  org_point_b // 6,
                'parent_g':  None if org_groupe_b == 0xFFFF else (org_groupe_b // 38),
                'nb_norm':   nb_norm,
            })
        pos += nb_groups * 38

    nb_normals = struct.unpack('<H', payload[pos:pos+2])[0]
    pos += 2 + nb_normals * 8

    nb_polys = struct.unpack('<H', payload[pos:pos+2])[0]
    pos += 2

    polys = []
    for _ in range(nb_polys):
        matiere = payload[pos]
        nbp = payload[pos+1]
        coul1 = payload[pos+2]
        coul2 = payload[pos+3]
        pos += 4
        indices = []
        if matiere >= 9:
            for _v in range(nbp):
                _norm = struct.unpack('<H', payload[pos:pos+2])[0]
                vidx = struct.unpack('<H', payload[pos+2:pos+4])[0]
                indices.append(vidx // 6)
                pos += 4
        elif matiere >= 7:
            pos += 2  # face normal idx
            for _v in range(nbp):
                vidx = struct.unpack('<H', payload[pos:pos+2])[0]
                indices.append(vidx // 6)
                pos += 2
        else:
            for _v in range(nbp):
                vidx = struct.unpack('<H', payload[pos:pos+2])[0]
                indices.append(vidx // 6)
                pos += 2
        polys.append({'mat': matiere, 'coul1': coul1, 'coul2': coul2, 'indices': indices})

    # --- Le due sezioni in CODA ai poligoni: E_LIGNE ed E_SPHERE.
    #
    # Sono voci a lunghezza fissa di 8 byte, contate ognuna da una UWORD che le
    # precede (P_OB_ISO.ASM:551 e :606). Il gioco NON le disegna a parte: le
    # aggiunge alla stessa List_Tri dei poligoni, quindi si ordinano insieme.
    #
    #   E_LIGNE   BYTE matiere, coul1, coul2, ?   UWORD v0*6, UWORD v1*6
    #   E_SPHERE  BYTE matiere, coul1, coul2, ?   UWORD rayon, UWORD v*6
    #
    # Il formato e' quello letto e verificato in S15 (tools/body_sphere_dump.py);
    # qui sta dentro il parser che l'oracolo usa davvero, cosi' non ci sono due
    # letture dello stesso archivio che possono divergere.
    lines = []
    if pos + 2 <= len(payload):
        nb_lines = struct.unpack('<H', payload[pos:pos+2])[0]
        pos += 2
        for _ in range(nb_lines):
            if pos + 8 > len(payload):
                break
            lines.append({
                'mat':   payload[pos],
                'coul1': payload[pos+1],
                'coul2': payload[pos+2],
                'v0':    struct.unpack('<H', payload[pos+4:pos+6])[0] // 6,
                'v1':    struct.unpack('<H', payload[pos+6:pos+8])[0] // 6,
            })
            pos += 8

    spheres = []
    if pos + 2 <= len(payload):
        nb_spheres = struct.unpack('<H', payload[pos:pos+2])[0]
        pos += 2
        for _ in range(nb_spheres):
            if pos + 8 > len(payload):
                break
            spheres.append({
                'mat':   payload[pos],
                'coul1': payload[pos+1],
                'coul2': payload[pos+2],
                'rayon': struct.unpack('<H', payload[pos+4:pos+6])[0],
                'v':     struct.unpack('<H', payload[pos+6:pos+8])[0] // 6,
            })
            pos += 8

    return {
        'flags': flags, 'bbox': bbox, 'animated': bool(flags & INFO_ANIM),
        'nb_points': nb_points, 'nb_groups': nb_groups, 'nb_normals': nb_normals,
        'vertices': vertices, 'groups': groups, 'polys': polys,
        'lines': lines, 'spheres': spheres,
        'consumed': pos, 'total': len(payload),
    }

TYPE_ROTATE    = 0
TYPE_TRANSLATE = 1
TYPE_ZOOM      = 2


def _w16(v):
    """Il DOS ci mette una WORD (`mov word ptr[edi], si`), quindi tronca.

    Sui corpi veri non si arriva mai a 32767 e il troncamento non scatta mai -
    ma il C ci arriva GRATIS assegnando a un `WORD`, e due lati che divergono
    solo su un caso che non capita sono due lati che divergeranno il giorno in
    cui capita."""
    return ((v + 0x8000) & 0xFFFF) - 0x8000


def compute_groups(vertices, groups, mat_world, alpha, beta, gamma, pose=None):
    """AnimNuage, P_OB_ISO.ASM:1155-1225. La posa dei gruppi, in generale.

    Torna `(punti, matrici)`: i vertici GIA' RUOTATI nel sistema dell'attore, e
    la matrice di ogni gruppo - che serve dopo, perche' anche le NORMALI si
    ruotano per gruppo.

    `pose` e' `[(tipo, v1, v2, v3)]`, una per gruppo, oppure None per la posa a
    riposo. Il gruppo 0 non la usa: i suoi angoli sono quelli dell'attore.

    RAPPORTO CON compute_rest_pose, CHE QUESTA SOSTITUISCE
    -----------------------------------------------------
    A riposo tutti gli angoli locali sono zero, e `rot_mat_index2` con tre
    angoli nulli COPIA la matrice sorgente (P_TRIGO.ASM:981-987): ogni gruppo
    eredita quindi la matrice dell'attore. Siccome ruotare e' lineare,
    `M(v + ancora) == Mv + M(ancora)`, cioe' esattamente quel che facevano
    `compute_rest_pose` piu' una rotazione unica a valle. Le sonde di M3c non
    devono muoversi di un bit: e' il collaudo di questa funzione.

    I vertici sorgente sono sempre quelli ORIGINALI del corpo (`Def_Point`), mai
    quelli gia' trasformati; ad accumularsi lungo la catena sono le ANCORE, che
    si leggono dal risultato del padre - ed e' lecito perche' i gruppi sono in
    ordine topologico.
    """
    pts = [list(v) for v in vertices]
    mats = []

    for gi, g in enumerate(groups):
        if gi == 0:
            # RotateGroupe con orggroupe == -1: sorgente LMatriceWorld, nessuna
            # ancora, e gli angoli sono quelli che il chiamante ha passato.
            typ, v1, v2, v3 = TYPE_ROTATE, alpha, beta, gamma
            src = mat_world
            x0 = y0 = z0 = 0
        else:
            typ, v1, v2, v3 = (pose[gi] if pose else (TYPE_ROTATE, 0, 0, 0))
            src = mats[g['parent_g']]
            x0, y0, z0 = pts[g['anchor_v']]

        if typ == TYPE_ROTATE:
            m = rot_mat_index2(src, v1, v2, v3)
            dx = dy = dz = 0
        elif typ == TYPE_TRANSLATE:
            # TranslateGroupe: la matrice e' quella del padre COPIATA (`rep
            # movsd`), e i tre valori sono uno spostamento che si somma al
            # vertice PRIMA di ruotarlo (TransRotList, P_TRIGO.ASM:313).
            m = [row[:] for row in src]
            dx, dy, dz = v1, v2, v3
        else:
            # ZoomGroupe sta dentro `ifdef Poubelle` e il DOS non lo chiama
            # nemmeno: cade in `nolocalrot`, cioe' lascia i vertici del gruppo a
            # quel che c'era prima nella lista. Su tutte e 516 le animazioni
            # dell'archivio il tipo 2 non compare MAI (43653 rotazioni, 4985
            # traslazioni). Un dato che ci arriva qui non e' un caso da
            # riprodurre: e' un archivio diverso da quello su cui il porting e'
            # stato scritto.
            raise SystemExit('gruppo con tipo di trasformazione %d: '
                             'ANIM.HQR non ne contiene' % typ)

        mats.append(m)

        for i in range(g['start_v'], min(g['start_v'] + g['nb_v'], len(pts))):
            vx, vy, vz = vertices[i]
            rx, ry, rz = rot(m, vx + dx, vy + dy, vz + dz)
            pts[i] = [_w16(rx + x0), _w16(ry + y0), _w16(rz + z0)]

    return [tuple(v) for v in pts], mats


def compute_rest_pose(vertices, groups):
    """Apply hierarchical translations along parent->child chain (rest pose, no rotation).
    For root group (parent=None): vertex_world = vertex_def
    For child group: vertex_world[i] = vertex_def[i] + anchor_world
                     where anchor_world = vertex_world[group.anchor_v]
                     (already transformed because parent index is always < current index — topological order)
    """
    world = [list(v) for v in vertices]  # mutable copy
    for g in groups:
        if g['parent_g'] is None:
            # root: no translation
            ax, ay, az = 0, 0, 0
        else:
            ax, ay, az = world[g['anchor_v']]
        for i in range(g['start_v'], g['start_v'] + g['nb_v']):
            if i < len(world):
                world[i][0] += ax
                world[i][1] += ay
                world[i][2] += az
    return [tuple(v) for v in world]

def iso_project(x, y, z, xc, yc):
    sx = xc + ((x - z) * 24) // 512
    sy = yc + ((x + z) * 12 - y * 30) // 512
    return sx, sy

def draw_line(rgba, w, h, x0, y0, x1, y1, color):
    r, g, b, a = color
    x0, y0, x1, y1 = int(x0), int(y0), int(x1), int(y1)
    dx = abs(x1 - x0)
    dy = abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    err = dx - dy
    while True:
        if 0 <= x0 < w and 0 <= y0 < h:
            p = (y0 * w + x0) * 4
            rgba[p]   = r
            rgba[p+1] = g
            rgba[p+2] = b
            rgba[p+3] = a
        if x0 == x1 and y0 == y1:
            break
        e2 = 2 * err
        if e2 > -dy:
            err -= dy; x0 += sx
        if e2 < dx:
            err += dx; y0 += sy

def render(body_idx, body_path, out_path, scale, xc, yc, W, H):
    with open(body_path, 'rb') as f:
        data = f.read()
    offsets = parse_offsets(data)
    payload = extract_entry(data, body_idx, offsets)
    info = parse_body_full(payload)
    print(f"Body {body_idx}: anim={info['animated']}, bbox={info['bbox']}")
    print(f"  verts={info['nb_points']}, groups={info['nb_groups']}, normals={info['nb_normals']}, polys={len(info['polys'])}")
    if info['groups']:
        print(f"  group 0: parent={info['groups'][0]['parent_g']} start_v={info['groups'][0]['start_v']} nb_v={info['groups'][0]['nb_v']} anchor_v={info['groups'][0]['anchor_v']}")
        print(f"  group 1: parent={info['groups'][1]['parent_g']} start_v={info['groups'][1]['start_v']} nb_v={info['groups'][1]['nb_v']} anchor_v={info['groups'][1]['anchor_v']}")

    # Apply hierarchical rest-pose transforms
    world_verts = compute_rest_pose(info['vertices'], info['groups']) if info['animated'] else info['vertices']

    # Project all (world-space) vertices
    proj = [iso_project(x, y, z, 0, 0) for x, y, z in world_verts]
    if not proj:
        print("  no vertices, skipping")
        return
    # Find bbox of projected points to auto-center
    xs = [p[0] for p in proj]
    ys = [p[1] for p in proj]
    bbx_min, bbx_max = min(xs), max(xs)
    bby_min, bby_max = min(ys), max(ys)
    pwidth = bbx_max - bbx_min
    pheight = bby_max - bby_min
    print(f"  projected bbox: x={bbx_min}..{bbx_max} ({pwidth}px), y={bby_min}..{bby_max} ({pheight}px)")

    # Center: place bbox center at canvas center
    canvas_w, canvas_h = W * scale, H * scale
    cx = (canvas_w // 2) - ((bbx_min + bbx_max) // 2) * scale
    cy = (canvas_h // 2) - ((bby_min + bby_max) // 2) * scale
    proj_canvas = [(p[0] * scale + cx, p[1] * scale + cy) for p in proj]

    # Allocate canvas
    rgba = bytearray(canvas_w * canvas_h * 4)
    for i in range(canvas_w * canvas_h):
        p = i * 4
        rgba[p]   = 28; rgba[p+1] = 28; rgba[p+2] = 36; rgba[p+3] = 255

    # Color by bucket
    COL_GOURAUD = (220, 220, 220, 255)  # white
    COL_FLAT    = (100, 200, 100, 255)  # green
    COL_PATTERN = (220, 100, 100, 255)  # red

    edges_drawn = 0
    for poly in info['polys']:
        m = poly['mat']
        color = COL_GOURAUD if m >= 9 else (COL_FLAT if m >= 7 else COL_PATTERN)
        idx = poly['indices']
        n = len(idx)
        if n < 2:
            continue
        for i in range(n):
            v0 = idx[i]
            v1 = idx[(i + 1) % n]
            if v0 >= len(proj_canvas) or v1 >= len(proj_canvas):
                continue
            x0, y0 = proj_canvas[v0]
            x1, y1 = proj_canvas[v1]
            draw_line(rgba, canvas_w, canvas_h, x0, y0, x1, y1, color)
            edges_drawn += 1

    write_png_rgba(out_path, canvas_w, canvas_h, bytes(rgba))
    print(f"  drew {edges_drawn} edges, wrote {out_path} ({canvas_w}x{canvas_h})")

if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--body', type=int, default=0)
    ap.add_argument('--brk', default=lba_paths.common('BODY.HQR'))
    ap.add_argument('--out', default=None)
    ap.add_argument('--scale', type=int, default=2)
    ap.add_argument('--xc', type=int, default=0)
    ap.add_argument('--yc', type=int, default=0)
    ap.add_argument('--w', type=int, default=320)
    ap.add_argument('--h', type=int, default=240)
    args = ap.parse_args()
    out = args.out or lba_paths.repo(rf'extracted\BODY_render\body_{args.body:03d}_wireframe_x{args.scale}.png')
    os.makedirs(os.path.dirname(out), exist_ok=True)
    render(args.body, args.brk, out, args.scale, args.xc, args.yc, args.w, args.h)
