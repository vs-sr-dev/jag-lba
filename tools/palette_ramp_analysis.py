#!/usr/bin/env python3
"""S14 pre-flight Q3: verify if RESS_PAL slot 0 (LBA1 VGA palette) is structured
in 16 ramps x 16 shades for gouraud lookup, and how body coul1 values map onto it.

Three checks:
  1. Per group of 16 consecutive palette entries, test if luma is monotonic
     and chroma (Cb/Cr) is stable -> "well-formed gouraud ramp".
  2. Body 0 (Twinsen) coul1 distribution per material bucket: are coul1 values
     aligned to multiples of 16 (= ramp boundary)? Which ramps are used?
  3. Aggregate coul1 distribution across all 132 BODY.HQR bodies, per bucket.

Output answers:
  - Q3.A: is the RESS palette already a gouraud-ready 16x16 ramp grid?
  - Q3.B: do bodies systematically point coul1 at ramp boundaries?
  - Q3.C: which ramps are "body palette" (need to stay alive) vs "scene only"?
"""

import os, sys, math, struct
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hqr_decompress import parse_offsets, extract_entry
from render_slide import normalize_palette


def rgb_to_yuv(r, g, b):
    """ITU-R BT.601 luma + chroma."""
    y  = 0.299 * r + 0.587 * g + 0.114 * b
    cb = -0.169 * r - 0.331 * g + 0.500 * b
    cr =  0.500 * r - 0.419 * g - 0.081 * b
    return y, cb, cr


def test_ramp_structure(palette):
    """Return n_well_formed (0..16) and per-ramp summary list."""
    print("=== Q3.A: Ramp structure test (16 ramps x 16 shades) ===")
    print(f"  {'ramp':>4}  {'range':>11}  {'Y_min':>5}  {'Y_max':>5}  {'Cb_std':>6}  {'Cr_std':>6}  {'mono':>4}  {'OK':>2}")
    n_well = 0
    summary = []
    for r in range(16):
        ramp = palette[r*16 : r*16 + 16]
        ys, cbs, crs = [], [], []
        for (R, G, B) in ramp:
            y, cb, cr = rgb_to_yuv(R, G, B)
            ys.append(y); cbs.append(cb); crs.append(cr)
        diffs = [ys[i+1] - ys[i] for i in range(15)]
        n_inc  = sum(1 for d in diffs if d >  0.5)
        n_dec  = sum(1 for d in diffs if d < -0.5)
        n_flat = sum(1 for d in diffs if abs(d) <= 0.5)
        monotonic = (n_inc + n_flat == 15) or (n_dec + n_flat == 15)
        mean_cb = sum(cbs) / 16
        mean_cr = sum(crs) / 16
        std_cb = math.sqrt(sum((c - mean_cb) ** 2 for c in cbs) / 16)
        std_cr = math.sqrt(sum((c - mean_cr) ** 2 for c in crs) / 16)
        # Chroma stable if std < ~15 (empirical threshold for "same hue")
        chroma_stable = std_cb < 15 and std_cr < 15
        well = monotonic and chroma_stable
        if well:
            n_well += 1
        flag = 'OK' if well else '--'
        rng = f"{r*16:3d}..{r*16+15:3d}"
        mono = 'Y' if monotonic else 'N'
        print(f"  {r:>4}  {rng:>11}  {min(ys):>5.0f}  {max(ys):>5.0f}  {std_cb:>6.1f}  {std_cr:>6.1f}  {mono:>4}  {flag:>2}")
        summary.append({'idx': r, 'well': well, 'monotonic': monotonic,
                        'chroma_stable': chroma_stable, 'y_min': min(ys), 'y_max': max(ys)})
    print(f"\n  Well-formed ramps (monotonic luma + stable chroma): {n_well}/16")
    return n_well, summary


def parse_body_polys(payload):
    """Return list of dicts per polygon."""
    flags = struct.unpack('<H', payload[0:2])[0]
    zone_info_size = struct.unpack('<H', payload[14:16])[0]
    pos = 16 + zone_info_size
    nb_points = struct.unpack('<H', payload[pos:pos+2])[0]
    pos += 2 + nb_points * 6
    if flags & 0x0002:  # INFO_ANIM
        nb_groups = struct.unpack('<H', payload[pos:pos+2])[0]
        pos += 2 + nb_groups * 38
    nb_normals = struct.unpack('<H', payload[pos:pos+2])[0]
    pos += 2 + nb_normals * 8
    nb_polys = struct.unpack('<H', payload[pos:pos+2])[0]
    pos += 2
    polys = []
    for _ in range(nb_polys):
        if pos + 4 > len(payload):
            break
        matiere = payload[pos]; nbp = payload[pos+1]
        coul1 = payload[pos+2]; coul2 = payload[pos+3]
        pos += 4
        v_idx, n_idx, intens = [], [], []
        if matiere >= 9:
            for _ in range(nbp):
                if pos + 4 > len(payload): break
                v = struct.unpack('<H', payload[pos:pos+2])[0]
                i = struct.unpack('<H', payload[pos+2:pos+4])[0]
                v_idx.append(v); intens.append(i); pos += 4
        elif matiere >= 7:
            if pos + 2 > len(payload): break
            n = struct.unpack('<H', payload[pos:pos+2])[0]
            n_idx.append(n); pos += 2
            for _ in range(nbp):
                if pos + 2 > len(payload): break
                v = struct.unpack('<H', payload[pos:pos+2])[0]
                v_idx.append(v); pos += 2
        else:
            for _ in range(nbp):
                if pos + 2 > len(payload): break
                v = struct.unpack('<H', payload[pos:pos+2])[0]
                v_idx.append(v); pos += 2
        polys.append({'mat': matiere, 'nbp': nbp, 'coul1': coul1, 'coul2': coul2,
                      'v': v_idx, 'n': n_idx, 'i': intens})
    return polys


def bucket_of(mat):
    if mat >= 9:  return 'gouraud'
    if mat >= 7:  return 'flat'
    return 'pattern'


def analyze_body0(body_data, body_offs):
    print("\n=== Q3.B: Body 0 (Twinsen) coul1 distribution per bucket ===")
    body0 = extract_entry(body_data, 0, body_offs)
    polys = parse_body_polys(body0)
    by_bucket = {'pattern': [], 'flat': [], 'gouraud': []}
    for p in polys:
        by_bucket[bucket_of(p['mat'])].append(p['coul1'])
    for bk in ('pattern', 'flat', 'gouraud'):
        c1s = by_bucket[bk]
        if not c1s:
            print(f"\n  {bk}: 0 polys (skip)")
            continue
        ramps = Counter(c // 16 for c in c1s)
        offsets = Counter(c % 16 for c in c1s)
        aligned = sum(1 for c in c1s if c % 16 == 0)
        print(f"\n  {bk}: {len(c1s)} polys")
        print(f"    coul1 aligned to ramp boundary (mod 16 == 0): {aligned}/{len(c1s)}  ({100*aligned/len(c1s):.1f}%)")
        print(f"    ramps used: {dict(sorted(ramps.items()))}")
        print(f"    coul1 mod 16 histogram (offset within ramp):")
        for off in sorted(offsets):
            print(f"      offset {off:>2}: {offsets[off]:>4} polys")

    print("\n=== Body 0 gouraud per-vertex intensity stats ===")
    all_i = []
    for p in polys:
        if p['mat'] >= 9:
            all_i.extend(p['i'])
    if all_i:
        print(f"  Total gouraud (v_idx, intensity) pairs: {len(all_i)}")
        print(f"  Intensity range: min={min(all_i)}, max={max(all_i)}")
        upper_byte = Counter(i >> 8 for i in all_i)
        lower_byte = Counter(i & 0xFF for i in all_i)
        print(f"  Upper byte distribution (top 5): {upper_byte.most_common(5)}")
        print(f"  Lower byte unique values: {len(lower_byte)}, range [{min(lower_byte)}..{max(lower_byte)}]")
        # If upper byte is 0 mostly, intensity is just the lower byte (0..255)
        # If upper byte is non-trivial, it might be a normal_idx instead of an intensity
        print(f"  Hypothesis check:")
        if upper_byte.get(0, 0) >= len(all_i) * 0.95:
            print(f"    -> Upper byte is ~always 0: intensity is a plain 8-bit value 0..255")
        else:
            print(f"    -> Upper byte has variation: this is likely a UWORD index, not a clamped intensity")


def analyze_all_bodies(body_data, body_offs):
    print("\n=== Q3.C: Aggregate coul1 distribution across all bodies ===")
    all_buckets = {'pattern': Counter(), 'flat': Counter(), 'gouraud': Counter()}
    aligned_count = {'pattern': [0, 0], 'flat': [0, 0], 'gouraud': [0, 0]}  # [aligned, total]
    for bidx in range(len(body_offs)):
        payload = extract_entry(body_data, bidx, body_offs)
        if payload is None:
            continue
        try:
            polys = parse_body_polys(payload)
        except Exception:
            continue
        for p in polys:
            bk = bucket_of(p['mat'])
            all_buckets[bk][p['coul1'] // 16] += 1
            aligned_count[bk][1] += 1
            if p['coul1'] % 16 == 0:
                aligned_count[bk][0] += 1
    for bk in ('pattern', 'flat', 'gouraud'):
        ramps = all_buckets[bk]
        total = sum(ramps.values())
        a, t = aligned_count[bk]
        print(f"\n  {bk}: {total} polys total")
        if t:
            print(f"    aligned to ramp boundary: {a}/{t}  ({100*a/t:.1f}%)")
        if total:
            for r in sorted(ramps):
                pct = 100 * ramps[r] / total
                print(f"    ramp {r:>2d} [{r*16:>3d}..{r*16+15:>3d}]: {ramps[r]:>6,} polys ({pct:>4.1f}%)")


def main():
    GOG = r'I:\GOG\Little Big Adventure\Common'
    pal_path  = os.path.join(GOG, 'RESS.HQR')
    body_path = os.path.join(GOG, 'BODY.HQR')

    print(f"Palette source: {pal_path}")
    print(f"Body source:    {body_path}\n")

    with open(pal_path, 'rb') as f:
        pal_data = f.read()
    pal_offs = parse_offsets(pal_data)
    pal_raw = extract_entry(pal_data, 0, pal_offs)
    palette = normalize_palette(pal_raw)
    print(f"Loaded RESS_PAL slot 0: {len(palette)} entries\n")

    test_ramp_structure(palette)

    with open(body_path, 'rb') as f:
        body_data = f.read()
    body_offs = parse_offsets(body_data)

    analyze_body0(body_data, body_offs)
    analyze_all_bodies(body_data, body_offs)


if __name__ == '__main__':
    main()
