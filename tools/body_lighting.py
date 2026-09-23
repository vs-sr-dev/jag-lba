#!/usr/bin/env python3
"""S14 A1: bake per-corner intensity for a LBA1 body using a fixed NormalLight.

LBA1 gouraud poly stores per corner a (vertex_idx, normal_idx) UWORD pair.
LBA1 flat poly stores per poly a single normal_idx (UWORD before vertex list).
LBA1 pattern poly (mat 0-6) has no normal -> no lighting computed.

Lighting formula (rest-pose, identity group rotations assumed for T-pose):
    intensity_raw = dot(normal.xyz, NormalLight) / normal.prenormalized_range
where NormalLight is a unit vector. Result is in [-1, +1]; back-facing
(< 0) is clamped to 0, then scaled to 4-bit shade [0..15].

CAVEAT: this skips per-group rotation matrices used in LBA1 ComputeAnimNormal.
For pure T-pose with identity group rotations, this is equivalent to the
DOS engine. For animated frames, F4.6 will need the group matrix premultiply.

Used by:
  - palette_ramp_analysis.py (Q3 verification, doesn't need this directly)
  - body_gouraud_render.py (A2, CPU reference renderer)
  - body_pack.py (B1, cart pack with baked intensities)

CLI: stats for body 0 (Twinsen).
"""

import os, sys, math, struct
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hqr_decompress import parse_offsets, extract_entry


# Default NormalLight: world-space unit vector pointing FROM the light source.
# Convention: dot(surface_normal, NormalLight) > 0 means surface faces the light.
# (-1, -1, -1)/sqrt(3) -> light from the upper-back-left of the world axis triple,
# generic well-distributed illumination on iso-displayed characters.
DEFAULT_NORMAL_LIGHT = (
    -1.0 / math.sqrt(3.0),
    -1.0 / math.sqrt(3.0),
    -1.0 / math.sqrt(3.0),
)


def parse_body(payload):
    """Return dict with verts, normals (X,Y,Z,range), polys.
    Each poly: {'mat', 'nbp', 'coul1', 'coul2', 'corners': [(v_idx, normal_idx) | (v_idx,)]}
    Flat poly stores one normal_idx (in 'flat_normal_idx').
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
    pos += 2
    normals = []
    for _ in range(nb_normals):
        nx, ny, nz, nr = struct.unpack('<4h', payload[pos:pos+8])
        normals.append((nx, ny, nz, nr))
        pos += 8

    nb_polys = struct.unpack('<H', payload[pos:pos+2])[0]
    pos += 2
    polys = []
    for _ in range(nb_polys):
        if pos + 4 > len(payload):
            break
        mat = payload[pos]
        nbp = payload[pos+1]
        c1 = payload[pos+2]
        c2 = payload[pos+3]
        pos += 4
        flat_n = None
        corners = []
        if mat >= 9:  # gouraud
            # CORRECT ORDER per LBA1 P_OB_ISO.ASM:485-491:
            #   first UWORD  = normal_idx (index into List_Normal, the per-frame
            #                  precomputed intensity array)
            #   second UWORD = vertex byte-offset (already premultiplied by
            #                  SIZE_LIST_POINT = 6); divide by 6 to get v_idx
            # NOTE: body_inventory.py:22 has the order inverted in its comment.
            for _ in range(nbp):
                n_idx = struct.unpack('<H', payload[pos:pos+2])[0]
                v_byte_off = struct.unpack('<H', payload[pos+2:pos+4])[0]
                v_idx = v_byte_off // 6
                corners.append((v_idx, n_idx))
                pos += 4
        elif mat >= 7:  # flat
            # flat: WORD normal_idx then WORD v_byte_off[nbp]
            # normal_idx is a WORD index into List_Normal (intensity array).
            # v_byte_off is already premultiplied by SIZE_LIST_POINT (=6).
            flat_n = struct.unpack('<H', payload[pos:pos+2])[0]
            pos += 2
            for _ in range(nbp):
                v_byte_off = struct.unpack('<H', payload[pos:pos+2])[0]
                v_idx = v_byte_off // 6
                corners.append((v_idx,))
                pos += 2
        else:  # pattern (mat 0..6)
            # pattern: WORD v_byte_off[nbp] only, no normals.
            for _ in range(nbp):
                v_byte_off = struct.unpack('<H', payload[pos:pos+2])[0]
                v_idx = v_byte_off // 6
                corners.append((v_idx,))
                pos += 2
        polys.append({
            'mat': mat, 'nbp': nbp, 'coul1': c1, 'coul2': c2,
            'flat_normal_idx': flat_n, 'corners': corners,
        })
    return {
        'flags': flags, 'nb_groups': nb_groups,
        'verts': verts, 'normals': normals, 'polys': polys,
    }


def normal_intensity(normal_xyzr, light_xyz, mode='cosine'):
    """Compute lighting intensity for a body normal.

    mode='cosine': cos(theta) using |normal| as divisor. Output in [-1, +1].
                   Assumes |light| = 1. Use this for full-ramp Jaguar-tailored
                   shading (ramp shades 0..15 used).
    mode='lba_range': LBA1 DOS asm formula = dot(normal, light) / range.
                      The 'range' field is an art-direction divisor (typically
                      5x |normal|), producing muted intensity 0..0.25 that maps
                      to ramp shades 0..3 only. Match canonical LBA1 look.
    """
    nx, ny, nz, nr = normal_xyzr
    lx, ly, lz = light_xyz
    dot = nx * lx + ny * ly + nz * lz
    if mode == 'cosine':
        mag = math.sqrt(nx*nx + ny*ny + nz*nz)
        if mag == 0:
            return 0.0
        return dot / mag
    elif mode == 'lba_range':
        if nr == 0:
            return 0.0
        return dot / nr
    else:
        raise ValueError(f"unknown mode: {mode}")


def to_4bit(intensity_raw):
    """Map intensity_raw (cosine, in [-1, +1]) to 4-bit shade [0..15].
    Negative = back-facing surface = clamp to 0.
    Positive scaled by 15, rounded, clamped.
    """
    if intensity_raw <= 0.0:
        return 0
    if intensity_raw >= 1.0:
        return 15
    return int(round(intensity_raw * 15.0))


def bake_body_intensity(body, light=DEFAULT_NORMAL_LIGHT, mode='cosine'):
    """Augment body['polys'] in place with 'intensities' list per poly.

    mode: 'cosine' (Jaguar-tailored full-ramp) or 'lba_range' (LBA1 canonical muted).
    For gouraud poly: intensities[k] = 4bit shade for corner k.
    For flat poly:    intensities = [4bit shade]  (single value).
    For pattern poly: intensities = []  (no lighting).
    """
    for p in body['polys']:
        ints4 = []
        intsRaw = []
        if p['mat'] >= 9:  # gouraud
            for (v_idx, n_idx) in p['corners']:
                if n_idx >= len(body['normals']):
                    raw = 0.0
                else:
                    raw = normal_intensity(body['normals'][n_idx], light, mode)
                intsRaw.append(raw)
                ints4.append(to_4bit(raw))
        elif p['mat'] >= 7:  # flat
            n_idx = p['flat_normal_idx']
            if n_idx is None or n_idx >= len(body['normals']):
                raw = 0.0
            else:
                raw = normal_intensity(body['normals'][n_idx], light, mode)
            intsRaw.append(raw)
            ints4.append(to_4bit(raw))
        # pattern: no lighting
        p['intensities'] = ints4
        p['intensities_raw'] = intsRaw
    return body


def bucket_of(mat):
    if mat >= 9: return 'gouraud'
    if mat >= 7: return 'flat'
    return 'pattern'


def print_stats(body, body_idx, light, mode):
    print(f"=== Body {body_idx} lighting bake (mode={mode}, NormalLight = ({light[0]:+.4f}, {light[1]:+.4f}, {light[2]:+.4f})) ===")
    print(f"  vertices: {len(body['verts'])}")
    print(f"  normals:  {len(body['normals'])}")
    print(f"  polys:    {len(body['polys'])}  ({sum(1 for p in body['polys'] if p['mat']>=9)} gouraud, "
          f"{sum(1 for p in body['polys'] if 7<=p['mat']<9)} flat, "
          f"{sum(1 for p in body['polys'] if p['mat']<7)} pattern)")

    # Normal-range stats
    ranges = [n[3] for n in body['normals']]
    if ranges:
        print(f"  normal.range: min={min(ranges)}, max={max(ranges)}, "
              f"median={sorted(ranges)[len(ranges)//2]}, n_zero={sum(1 for r in ranges if r==0)}")

    # Raw intensity distribution per bucket
    print(f"\n  Raw intensity distribution (pre-clamp, pre-quantize):")
    for bk in ('gouraud', 'flat'):
        raws = []
        for p in body['polys']:
            if bucket_of(p['mat']) == bk:
                raws.extend(p['intensities_raw'])
        if not raws:
            print(f"    {bk}: 0 corners")
            continue
        n_neg = sum(1 for r in raws if r < 0)
        n_zero = sum(1 for r in raws if r == 0.0)
        n_clipHi = sum(1 for r in raws if r >= 1.0)
        print(f"    {bk}: {len(raws)} corners, "
              f"min={min(raws):+.3f}, max={max(raws):+.3f}, "
              f"mean={sum(raws)/len(raws):+.3f}, "
              f"backface(<0)={n_neg} ({100*n_neg/len(raws):.1f}%), "
              f"clipped@1={n_clipHi}")

    # 4-bit shade histogram per bucket
    print(f"\n  4-bit shade histogram (post-clamp):")
    print(f"    {'bucket':<8}  {'shade':<3}  {'count':>5}")
    for bk in ('gouraud', 'flat'):
        hist = Counter()
        for p in body['polys']:
            if bucket_of(p['mat']) == bk:
                for s in p['intensities']:
                    hist[s] += 1
        if not hist:
            continue
        for s in range(16):
            if hist[s] > 0:
                print(f"    {bk:<8}  {s:<3}  {hist[s]:>5}")

    # Coverage check: how many gouraud polys have all-zero shading? (= entirely backface)
    n_gouraud_dark = 0
    n_gouraud_total = 0
    n_gouraud_full = 0  # all corners > 0
    for p in body['polys']:
        if p['mat'] >= 9:
            n_gouraud_total += 1
            if all(s == 0 for s in p['intensities']):
                n_gouraud_dark += 1
            elif all(s > 0 for s in p['intensities']):
                n_gouraud_full += 1
    if n_gouraud_total:
        print(f"\n  Gouraud poly classification:")
        print(f"    fully dark (all corners = 0): {n_gouraud_dark}/{n_gouraud_total} "
              f"({100*n_gouraud_dark/n_gouraud_total:.1f}%) -- candidate for cull pre-dispatch")
        print(f"    fully lit (all corners > 0):  {n_gouraud_full}/{n_gouraud_total} "
              f"({100*n_gouraud_full/n_gouraud_total:.1f}%)")
        print(f"    partial (mix of 0 and >0):    {n_gouraud_total-n_gouraud_dark-n_gouraud_full}/{n_gouraud_total}")

    # Per-ramp coverage
    print(f"\n  Per-ramp use (coul1 // 16) for gouraud polys:")
    ramp_use = Counter()
    for p in body['polys']:
        if p['mat'] >= 9:
            ramp_use[p['coul1'] // 16] += 1
    for r in sorted(ramp_use):
        print(f"    ramp {r:2d}: {ramp_use[r]} polys")


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--body-hqr', default=r'I:\GOG\Little Big Adventure\Common\BODY.HQR')
    ap.add_argument('--body-idx', type=int, default=0)
    ap.add_argument('--lx', type=float, default=DEFAULT_NORMAL_LIGHT[0])
    ap.add_argument('--ly', type=float, default=DEFAULT_NORMAL_LIGHT[1])
    ap.add_argument('--lz', type=float, default=DEFAULT_NORMAL_LIGHT[2])
    ap.add_argument('--mode', choices=['cosine', 'lba_range', 'both'], default='both',
                    help="Lighting divisor: cosine (Jaguar full-ramp) | lba_range (LBA1 muted) | both (compare)")
    args = ap.parse_args()

    light = (args.lx, args.ly, args.lz)
    norm_l = math.sqrt(light[0]**2 + light[1]**2 + light[2]**2)
    if abs(norm_l - 1.0) > 0.01:
        print(f"WARNING: NormalLight magnitude = {norm_l:.4f}, expected ~1.0. Re-normalizing.")
        light = (light[0]/norm_l, light[1]/norm_l, light[2]/norm_l)

    with open(args.body_hqr, 'rb') as f:
        data = f.read()
    offs = parse_offsets(data)
    payload = extract_entry(data, args.body_idx, offs)
    if payload is None:
        print(f"BODY entry {args.body_idx} is BLANK")
        return

    modes = ['cosine', 'lba_range'] if args.mode == 'both' else [args.mode]
    for m in modes:
        body = parse_body(payload)
        bake_body_intensity(body, light, mode=m)
        print_stats(body, args.body_idx, light, m)
        print()


if __name__ == '__main__':
    main()
