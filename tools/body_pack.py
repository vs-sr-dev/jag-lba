#!/usr/bin/env python3
"""Pack one LBA1 body into a cart-friendly binary blob.

Workflow:
  1. Load extracted/BODY/entry_NNNN.bin (post-HQR raw body data)
  2. Parse using render_body.parse_body_full
  3. Apply hierarchical rest-pose transforms (no rotation; T-pose)
  4. Emit cart-friendly fixed-record layout:

JagLBA cart V5 BODY blob layout (per body):
  Header (20 byte, BE):
    +$00 char[2]  magic 'BD'
    +$02 u8       version (1)
    +$03 u8       flags  (bit 0 = animated source, bit 1 = static source)
    +$04 i16      bbox_xmin
    +$06 i16      bbox_xmax
    +$08 i16      bbox_ymin
    +$0A i16      bbox_ymax
    +$0C i16      bbox_zmin
    +$0E i16      bbox_zmax
    +$10 u16      nb_vertices
    +$12 u16      nb_polys

  Vertex array (6 byte/entry, BE, signed; rest-pose world-space):
    +0 i16 X, +2 i16 Y, +4 i16 Z

  Polygon array (16 byte/entry — fixed stride for fast 68k indexing):
    +$00 u8       mat        (0..10; LBA1 material code)
    +$01 u8       nbp        (vertex count, 3..6)
    +$02 u8       coul1      (palette idx primary)
    +$03 u8       coul2      (palette idx secondary; 0 if N/A)
    +$04 u16[6]   vidx[0..5] (vertex indices 0..nb_vertices-1; unused = $FFFF)

  Polys with nbp > 6 are tessellated into fans of (nbp-2) triangles using vertex 0
  as the common pivot. This keeps the cart-side rasterizer simple (V0 = flat
  triangles + quads), and matches LBA1's typical body topology where polys are
  3-4-5-6 sided with a few outliers.

V0 only flat shading is implemented in the cart-side rasterizer; poly mats
9-10 (gouraud) and 0-6 (pattern fills) will be drawn as flat using coul1
as a graceful fallback. The packer preserves the original mat byte so
later versions can dispatch correctly.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import lba_paths
import sys, os, struct, argparse
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from render_body import parse_body_full, compute_rest_pose

MAT_NAMES = {
    0:'TRISTE', 1:'PIERRE', 2:'COPPER', 3:'BOPPER', 4:'MARBRE',
    5:'TRANS', 6:'TRAME', 7:'FLAT', 8:'GRANIT', 9:'GOURAUD', 10:'DITHER',
}

POLY_RECORD_SIZE = 16
MAX_NBP = 6
HEADER_SIZE = 20

def fan_triangulate(indices):
    """Tessellate poly into (nbp-2) triangles using vertex 0 as pivot.
    Returns list of [v0, vN, vN+1] triples.
    For nbp=3: just [indices].
    For nbp=4: [[v0,v1,v2], [v0,v2,v3]].
    """
    if len(indices) <= 3:
        return [list(indices)]
    return [[indices[0], indices[i], indices[i+1]] for i in range(1, len(indices)-1)]

def pack_body(payload):
    info = parse_body_full(payload)
    vertices = info['vertices']
    if info['animated']:
        vertices = compute_rest_pose(vertices, info['groups'])
    nb_v = len(vertices)
    if nb_v > 0xFFFF:
        raise ValueError(f"Too many vertices: {nb_v} > 65535")

    # Expand polys: keep nbp<=6 as-is, tessellate nbp>6 into fan
    expanded = []
    for poly in info['polys']:
        n = len(poly['indices'])
        if n <= MAX_NBP:
            expanded.append(poly)
        else:
            for tri in fan_triangulate(poly['indices']):
                expanded.append({'mat': poly['mat'], 'coul1': poly['coul1'],
                                 'coul2': poly['coul2'], 'indices': tri})
    nb_p = len(expanded)
    if nb_p > 0xFFFF:
        raise ValueError(f"Too many polys: {nb_p} > 65535")

    # Validate vertex indices
    for i, p in enumerate(expanded):
        for vi in p['indices']:
            if vi >= nb_v:
                raise ValueError(f"Poly {i} references vertex {vi} but only {nb_v} verts present")

    # Header
    flags_byte = (1 if info['animated'] else 0)
    bbox = info['bbox']
    out = bytearray()
    out += b'BD'
    out += bytes([1, flags_byte])
    out += struct.pack('>6h', *bbox)
    out += struct.pack('>HH', nb_v, nb_p)
    assert len(out) == HEADER_SIZE, f"Header size mismatch: {len(out)}"

    # Vertex array (BE i16 X, Y, Z)
    for x, y, z in vertices:
        out += struct.pack('>3h', x, y, z)
    assert len(out) == HEADER_SIZE + nb_v * 6

    # Poly array (16 byte fixed)
    for p in expanded:
        nbp = len(p['indices'])
        rec = bytearray(POLY_RECORD_SIZE)
        rec[0] = p['mat']
        rec[1] = nbp
        rec[2] = p['coul1']
        rec[3] = p['coul2']
        for i in range(MAX_NBP):
            vi = p['indices'][i] if i < nbp else 0xFFFF
            struct.pack_into('>H', rec, 4 + i*2, vi)
        out += bytes(rec)

    return {
        'blob': bytes(out),
        'nb_vertices': nb_v,
        'nb_polys_orig': len(info['polys']),
        'nb_polys_packed': nb_p,
        'bbox': bbox,
        'animated': info['animated'],
        'materials_orig': Counter(p['mat'] for p in info['polys']),
        'materials_packed': Counter(p['mat'] for p in expanded),
        'tessellated_count': nb_p - len(info['polys']),
    }

def report(body_idx, result):
    print(f"=== Body {body_idx} packed ===")
    print(f"  Animated source:    {result['animated']}")
    print(f"  Bbox X/Y/Z:         {result['bbox']}")
    print(f"  Vertices:           {result['nb_vertices']}")
    print(f"  Polys original:     {result['nb_polys_orig']}")
    print(f"  Polys after tessel: {result['nb_polys_packed']} (+{result['tessellated_count']} from fan)")
    print(f"  Blob size:          {len(result['blob'])} bytes "
          f"({HEADER_SIZE} hdr + {result['nb_vertices']*6} vtx + "
          f"{result['nb_polys_packed']*POLY_RECORD_SIZE} poly)")
    print(f"  Materials (packed):")
    total = sum(result['materials_packed'].values())
    for m in sorted(result['materials_packed']):
        c = result['materials_packed'][m]
        bucket = ('Pattern' if m < 7 else 'Flat' if m < 9 else 'Gouraud')
        print(f"    mat {m:>2} {MAT_NAMES.get(m,'?'):<8} [{bucket}]: {c:>4}  ({100*c/total:>4.1f}%)")

def verify_roundtrip(blob, expected_nb_v, expected_nb_p):
    """Sanity-check: re-parse the packed blob and confirm counts."""
    assert blob[0:2] == b'BD', "Magic mismatch"
    version = blob[2]
    flags = blob[3]
    bbox = struct.unpack('>6h', blob[4:16])
    nb_v, nb_p = struct.unpack('>HH', blob[16:20])
    assert nb_v == expected_nb_v, f"Vertex count mismatch: {nb_v} vs {expected_nb_v}"
    assert nb_p == expected_nb_p, f"Poly count mismatch: {nb_p} vs {expected_nb_p}"
    expected_size = HEADER_SIZE + nb_v*6 + nb_p*POLY_RECORD_SIZE
    assert len(blob) == expected_size, f"Blob size mismatch: {len(blob)} vs {expected_size}"
    return True

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--body', type=int, default=0, help='Body index (default: 0 = Twinsen)')
    ap.add_argument('--in-dir', default=lba_paths.repo(r'extracted\BODY'),
                    help='Directory with entry_NNNN.bin extracted bodies')
    ap.add_argument('--out-dir', default=lba_paths.repo(r'extracted\BODY_PACK'),
                    help='Output directory for packed body blobs')
    args = ap.parse_args()

    in_path = os.path.join(args.in_dir, f'entry_{args.body:04d}.bin')
    if not os.path.isfile(in_path):
        print(f"ERROR: {in_path} not found", file=sys.stderr)
        sys.exit(1)

    with open(in_path, 'rb') as f:
        payload = f.read()

    result = pack_body(payload)
    report(args.body, result)

    verify_roundtrip(result['blob'], result['nb_vertices'], result['nb_polys_packed'])
    print(f"  Roundtrip verify:   OK")

    os.makedirs(args.out_dir, exist_ok=True)
    out_path = os.path.join(args.out_dir, f'body_{args.body:03d}.bin')
    with open(out_path, 'wb') as f:
        f.write(result['blob'])
    print(f"  Written:            {out_path}")

if __name__ == '__main__':
    main()
