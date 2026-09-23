#!/usr/bin/env python3
"""S15 A1: dump E_LIGNE / E_SPHERE / E_POINT sections of a LBA1 body.

After the polygon section, an animated body has up to 3 trailing entity sections,
each starting with a UWORD count.

Line entry (E_LIGNE, 8 bytes):
  BYTE matiere
  BYTE coul1
  BYTE coul2
  BYTE ?
  UWORD v0_byte_off  (= v0_idx * 6)
  UWORD v1_byte_off  (= v1_idx * 6)

Sphere entry (E_SPHERE, 8 bytes):
  BYTE matiere
  BYTE coul1
  BYTE coul2
  BYTE ?
  UWORD rayon_world
  UWORD vertex_byte_off  (= v_idx * 6)

Point entry (E_POINT, ?? bytes — TBD from raw remainder):
  Probably 4 bytes: BYTE matiere, BYTE coul1, BYTE coul2, BYTE ?, UWORD v_idx*6
  Or similar. We dump leftover and let the user inspect.

Iso screen radius transform (P_OB_ISO.ASM:928): rs = (rayon * 34) >> 9.
"""
import os, sys, struct
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hqr_decompress import parse_offsets, extract_entry


def parse_body_full(payload):
    """Parse a body all the way through poly section + line + sphere + point.
    Returns dict with verts, lines, spheres, points, points_raw_bytes.
    """
    flags = struct.unpack('<H', payload[0:2])[0]
    zone_info_size = struct.unpack('<H', payload[14:16])[0]
    pos = 16 + zone_info_size

    nb_points = struct.unpack('<H', payload[pos:pos+2])[0]
    pos += 2
    verts = []
    for _ in range(nb_points):
        x, y, z = struct.unpack('<3h', payload[pos:pos+6])
        verts.append((x, y, z))
        pos += 6

    nb_groups = 0
    if flags & 0x0002:
        nb_groups = struct.unpack('<H', payload[pos:pos+2])[0]
        pos += 2 + nb_groups * 38

    nb_normals = struct.unpack('<H', payload[pos:pos+2])[0]
    pos += 2 + nb_normals * 8

    nb_polys = struct.unpack('<H', payload[pos:pos+2])[0]
    pos += 2
    for _ in range(nb_polys):
        mat = payload[pos]; nbp = payload[pos+1]
        pos += 4
        if mat >= 9:
            pos += nbp * 4
        elif mat >= 7:
            pos += 2 + nbp * 2
        else:
            pos += nbp * 2

    # ---- Lines section ----
    lines = []
    if pos + 2 <= len(payload):
        nb_lines = struct.unpack('<H', payload[pos:pos+2])[0]
        pos += 2
        for _ in range(nb_lines):
            if pos + 8 > len(payload):
                break
            mat = payload[pos]; c1 = payload[pos+1]; c2 = payload[pos+2]; pad = payload[pos+3]
            v0 = struct.unpack('<H', payload[pos+4:pos+6])[0] // 6
            v1 = struct.unpack('<H', payload[pos+6:pos+8])[0] // 6
            lines.append({'mat': mat, 'coul1': c1, 'coul2': c2, 'pad': pad, 'v0': v0, 'v1': v1})
            pos += 8

    # ---- Spheres section ----
    spheres = []
    if pos + 2 <= len(payload):
        nb_spheres = struct.unpack('<H', payload[pos:pos+2])[0]
        pos += 2
        for _ in range(nb_spheres):
            if pos + 8 > len(payload):
                break
            mat = payload[pos]; c1 = payload[pos+1]; c2 = payload[pos+2]; pad = payload[pos+3]
            rayon = struct.unpack('<H', payload[pos+4:pos+6])[0]
            v_idx = struct.unpack('<H', payload[pos+6:pos+8])[0] // 6
            spheres.append({
                'mat': mat, 'coul1': c1, 'coul2': c2, 'pad': pad,
                'rayon': rayon, 'v_idx': v_idx,
            })
            pos += 8

    # ---- Points (best-effort): dump remaining raw ----
    points_raw = payload[pos:]

    return {
        'flags': flags, 'verts': verts,
        'lines': lines, 'spheres': spheres, 'points_raw': points_raw,
    }


def iso_screen_radius(rayon_world):
    """LBA1 iso sphere radius: rs = (rayon * 34) >> 9 (P_OB_ISO.ASM:928)."""
    return (rayon_world * 34) >> 9


def proj_iso(x, y, z, xc=160, yc=100):
    """LBA1 iso projection (P_TRIGO.ASM:1474):
       sx = xc + ((X - Z) * 24) >> 9
       sy = yc + ((X + Z) * 12 - Y * 30) >> 9
    """
    sx = xc + (((x - z) * 24) >> 9)
    sy = yc + (((x + z) * 12 - y * 30) >> 9)
    return sx, sy


def main():
    brk_path = sys.argv[1] if len(sys.argv) > 1 else r'I:\GOG\Little Big Adventure\Common\BODY.HQR'
    body_idx = int(sys.argv[2]) if len(sys.argv) > 2 else 0

    with open(brk_path, 'rb') as f:
        data = f.read()
    offsets = parse_offsets(data)
    payload = extract_entry(data, body_idx, offsets)
    if payload is None:
        print(f"ERROR: could not extract body {body_idx}")
        return 1

    info = parse_body_full(payload)

    print(f"=== Body {body_idx} entity dump ===")
    print(f"flags = 0x{info['flags']:04x}, nb_verts = {len(info['verts'])}")
    print()

    print(f"--- Lines ({len(info['lines'])}) ---")
    if info['lines']:
        print(f"  {'idx':>3}  {'mat':>3}  {'c1':>3}  {'c2':>3}  {'pad':>3}  {'v0':>3}  {'v1':>3}")
        for i, ln in enumerate(info['lines']):
            print(f"  {i:>3}  {ln['mat']:>3}  {ln['coul1']:>3}  {ln['coul2']:>3}  {ln['pad']:>3}  {ln['v0']:>3}  {ln['v1']:>3}")
    print()

    print(f"--- Spheres ({len(info['spheres'])}) ---")
    if info['spheres']:
        print(f"  {'idx':>3}  {'mat':>3}  {'c1':>3}  {'c2':>3}  {'pad':>3}  {'rayon':>5}  {'rs_iso':>6}  {'v_idx':>5}  {'v_world(X,Y,Z)':>22}  {'screen(sx,sy)':>14}")
        for i, sp in enumerate(info['spheres']):
            v = info['verts'][sp['v_idx']] if sp['v_idx'] < len(info['verts']) else (0,0,0)
            rs = iso_screen_radius(sp['rayon'])
            sx, sy = proj_iso(v[0], v[1], v[2])
            print(f"  {i:>3}  {sp['mat']:>3}  {sp['coul1']:>3}  {sp['coul2']:>3}  {sp['pad']:>3}  {sp['rayon']:>5}  {rs:>6}  {sp['v_idx']:>5}  ({v[0]:>5},{v[1]:>5},{v[2]:>5})  ({sx:>4},{sy:>4})")
    print()

    raw = info['points_raw']
    print(f"--- Points raw remainder ({len(raw)} bytes) ---")
    if raw:
        # likely first WORD = nb_points
        nbp = struct.unpack('<H', raw[0:2])[0] if len(raw) >= 2 else 0
        print(f"  first WORD (nb_points?) = {nbp}")
        # hex dump
        for off in range(0, min(64, len(raw)), 16):
            chunk = raw[off:off+16]
            hexs = ' '.join(f'{b:02x}' for b in chunk)
            print(f"  +{off:03x}: {hexs}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
