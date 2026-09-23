#!/usr/bin/env python3
"""Parse all 132 bodies in BODY.HQR; report material distribution + per-body stats.

Body format (post-HQR-decompression), distilled from lba1-classic asm:
  Header (16 B):
    UWORD flags       (INFO_TRI=1, INFO_ANIM=2, INFO_TORTUE=4, ...)
    SWORD bbox_xmin, bbox_xmax, bbox_ymin, bbox_ymax, bbox_zmin, bbox_zmax
    UWORD zone_info_size (bytes to skip after header)
  Zone info: variable bytes (skipped).
  Vertex section: UWORD nb_points, then nb_points * (3 SWORDs = 6 bytes) = X, Y, Z
  IF flags has INFO_ANIM (bit 1, mask 0x0002):
    Group section: UWORD nb_groups, then nb_groups * 38 bytes (skeleton bones)
  Normal section: UWORD nb_normals, then nb_normals * (4 SWORDs = 8 bytes) = X, Y, Z, range
    (present for both static and animated; for animated, processed per-group via the
     NbNormal field at offset 18 within each group entry — see ComputeAnimNormal in
     P_OB_ISO.ASM:1437)
  Polygon section: UWORD nb_polys, then per poly:
    BYTE matiere, BYTE nbp, BYTE coul1, BYTE coul2  (4-byte header)
    Then variable per material:
      mat 0-6  (TRISTE..TRAME):  nbp * UWORD vertex_idx                  -> 4 + 2*nbp bytes
      mat 7-8  (FLAT, GRANIT):   UWORD normal_idx + nbp * UWORD v_idx    -> 6 + 2*nbp bytes
      mat 9-10 (GOURAUD, DITHER): nbp * 2 UWORDs (v_idx + intensity)     -> 4 + 4*nbp bytes

After polygons may come line / sphere / point sections (4 entity types: E_LIGNE / E_POLY /
E_SPHERE / E_POINT). For the inventory we count poly materials and report leftover bytes.
"""
import sys, os, struct
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hqr_decompress import parse_offsets, extract_entry

MAT_NAMES = {
    0:'TRISTE', 1:'PIERRE', 2:'COPPER', 3:'BOPPER', 4:'MARBRE',
    5:'TRANS', 6:'TRAME', 7:'FLAT', 8:'GRANIT', 9:'GOURAUD', 10:'DITHER',
}

def parse_body(payload):
    if len(payload) < 16:
        return {'error': f'too small ({len(payload)} bytes)'}
    flags = struct.unpack('<H', payload[0:2])[0]
    bbox = struct.unpack('<6h', payload[2:14])
    zone_info_size = struct.unpack('<H', payload[14:16])[0]

    pos = 16 + zone_info_size
    if pos > len(payload):
        return {'error': f'zone_info overflow: 16+{zone_info_size}={pos} > {len(payload)}'}

    if pos + 2 > len(payload):
        return {'error': 'no vertex section'}
    nb_points = struct.unpack('<H', payload[pos:pos+2])[0]
    pos += 2
    pos += nb_points * 6
    if pos > len(payload):
        return {'error': f'vertex overflow (nb_points={nb_points})'}

    INFO_ANIM = 0x0002
    nb_groups = 0
    if flags & INFO_ANIM:
        if pos + 2 > len(payload):
            return {'error': 'no group section'}
        nb_groups = struct.unpack('<H', payload[pos:pos+2])[0]
        pos += 2
        pos += nb_groups * 38
        if pos > len(payload):
            return {'error': f'group overflow (nb_groups={nb_groups})'}

    if pos + 2 > len(payload):
        return {'error': 'no normal section'}
    nb_normals = struct.unpack('<H', payload[pos:pos+2])[0]
    pos += 2
    pos += nb_normals * 8
    if pos > len(payload):
        return {'error': f'normal overflow (nb_normals={nb_normals})'}

    if pos + 2 > len(payload):
        return {'error': 'no polygon section'}
    nb_polys = struct.unpack('<H', payload[pos:pos+2])[0]
    pos += 2

    materials = Counter()
    for p in range(nb_polys):
        if pos + 4 > len(payload):
            return {'error': f'poly header overflow at poly {p}/{nb_polys} (pos={pos})'}
        matiere = payload[pos]
        nbp = payload[pos+1]
        pos += 4
        materials[matiere] += 1
        if matiere >= 9:
            pos += nbp * 4
        elif matiere >= 7:
            pos += 2 + nbp * 2
        else:
            pos += nbp * 2
        if pos > len(payload):
            return {'error': f'poly body overflow at poly {p}/{nb_polys}, mat={matiere}, nbp={nbp}'}

    return {
        'flags': flags, 'bbox': bbox, 'zone_info_size': zone_info_size,
        'nb_points': nb_points, 'nb_normals': nb_normals, 'nb_groups': nb_groups,
        'nb_polys': nb_polys,
        'materials': materials, 'consumed_after_polys': pos,
        'total_size': len(payload),
        'remaining': len(payload) - pos,
        'animated': bool(flags & 0x0002),
    }

def main(brk_path):
    with open(brk_path, 'rb') as f:
        data = f.read()
    offsets = parse_offsets(data)
    print(f"=== Body inventory: {brk_path} ===")
    print(f"Total entries: {len(offsets)}\n")

    all_mats = Counter()
    parsed = []
    errors = []
    for i in range(len(offsets)):
        payload = extract_entry(data, i, offsets)
        if payload is None:
            continue
        result = parse_body(payload)
        if 'error' in result:
            errors.append((i, result['error'], len(payload)))
            continue
        parsed.append((i, result))
        all_mats.update(result['materials'])

    print(f"Parsed OK: {len(parsed)}, Errors: {len(errors)}")
    if errors:
        print("\n--- Errors ---")
        for i, e, sz in errors[:10]:
            print(f"  body {i} (size={sz}): {e}")

    if not parsed:
        return

    total_polys = sum(all_mats.values())
    total_verts = sum(r['nb_points'] for _, r in parsed)
    total_norms = sum(r['nb_normals'] for _, r in parsed)
    total_remaining = sum(r['remaining'] for _, r in parsed)

    print(f"\n--- Aggregate stats ---")
    total_groups = sum(r.get('nb_groups', 0) for _, r in parsed)
    n_anim = sum(1 for _, r in parsed if r.get('animated'))
    print(f"  Total polys:     {total_polys:>7,}")
    print(f"  Total vertices:  {total_verts:>7,}")
    print(f"  Total normals:   {total_norms:>7,}  (across {len(parsed)-n_anim} static bodies)")
    print(f"  Total groups:    {total_groups:>7,}  (across {n_anim} animated bodies)")
    print(f"  Avg polys/body:  {total_polys/len(parsed):>7.1f}")
    print(f"  Avg verts/body:  {total_verts/len(parsed):>7.1f}")
    print(f"  Total bytes left after polys (lines/spheres/points sections): {total_remaining:,}")

    print(f"\n--- Material histogram ---")
    print(f"  {'mat':>3}  {'name':<8}  {'count':>6}  {'%':>5}")
    for m in sorted(all_mats):
        name = MAT_NAMES.get(m, f'UNK_{m}')
        c = all_mats[m]
        print(f"  {m:>3}  {name:<8}  {c:>6,}  {100*c/total_polys:>4.1f}%")

    bucket_pattern = sum(c for m, c in all_mats.items() if m < 7)
    bucket_flat = sum(c for m, c in all_mats.items() if 7 <= m < 9)
    bucket_gouraud = sum(c for m, c in all_mats.items() if m >= 9)

    print(f"\n--- 3-bucket dispatch ---")
    print(f"  Pattern (mat 0-6, FX fills):       {bucket_pattern:>6,}  {100*bucket_pattern/total_polys:>4.1f}%")
    print(f"  Flat    (mat 7-8, single color):   {bucket_flat:>6,}  {100*bucket_flat/total_polys:>4.1f}%")
    print(f"  Gouraud (mat 9-10, per-vertex):    {bucket_gouraud:>6,}  {100*bucket_gouraud/total_polys:>4.1f}%")

    print(f"\n--- Heaviest 10 bodies (most polys) ---")
    heavy = sorted(parsed, key=lambda x: -x[1]['nb_polys'])[:10]
    print(f"  {'body':>4}  {'anim':>4}  {'verts':>5}  {'norm':>4}  {'grps':>4}  {'polys':>5}  {'remain':>6}")
    for i, r in heavy:
        anim = 'Y' if r.get('animated') else 'N'
        print(f"  {i:>4}  {anim:>4}  {r['nb_points']:>5}  {r['nb_normals']:>4}  {r.get('nb_groups',0):>4}  {r['nb_polys']:>5}  {r['remaining']:>6}")

    print(f"\n--- Bodies with line/sphere/point trailing sections (remaining > 0) ---")
    trailing = [(i, r) for i, r in parsed if r['remaining'] > 0]
    print(f"  {len(trailing)} of {len(parsed)} bodies have non-poly entities (lines/spheres/points)")
    if trailing:
        print(f"  Top 5 by trailing bytes:")
        top_t = sorted(trailing, key=lambda x: -x[1]['remaining'])[:5]
        for i, r in top_t:
            print(f"    body {i}: polys={r['nb_polys']}, remaining={r['remaining']} bytes")

if __name__ == '__main__':
    path = sys.argv[1] if len(sys.argv) > 1 else r'I:\GOG\Little Big Adventure\Common\BODY.HQR'
    main(path)
