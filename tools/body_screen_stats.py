#!/usr/bin/env python3
"""F4.3-alpha pre-build static analysis: project body 0 vertices with the same
iso math as 68k TransformBody, then compute per-poly + per-edge stats to decide
if we hit GpuRasterMain constraints (viewport bounds + |slope|<1).

Iso math (matches scene_viewer.s TransformBody):
  sx = ((X - Z) * 24) >> 9 + Xc
  sy = ((X + Z) * 12 - Y * 30) >> 9 + Yc
  signed asr.l #9 == Python >> 9 on signed int (both floor).

Reports:
  - vertex bbox in screen space
  - poly viewport-in / viewport-out / partial counts
  - per-edge |dx| vs |dy| slope-violation distribution
  - dispatchable poly count (all verts in viewport, all edges |dx|<|dy| or dy=0)
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import lba_paths
import sys, os, struct, argparse
from collections import Counter

VIEWPORT_W = 320
VIEWPORT_H = 200          # PAL/NTSC effective FB height (BG_H equivalent)

def parse_packed_body(path):
    with open(path, 'rb') as f:
        blob = f.read()
    assert blob[0:2] == b'BD'
    nb_v, nb_p = struct.unpack('>HH', blob[16:20])
    verts = []
    off = 20
    for _ in range(nb_v):
        x, y, z = struct.unpack('>3h', blob[off:off+6])
        verts.append((x, y, z))
        off += 6
    polys = []
    for _ in range(nb_p):
        rec = blob[off:off+16]
        mat, nbp, coul1, coul2 = rec[0], rec[1], rec[2], rec[3]
        vidx = list(struct.unpack('>6H', rec[4:16]))[:nbp]
        polys.append({'mat':mat, 'nbp':nbp, 'coul1':coul1, 'coul2':coul2,
                      'vidx':vidx})
        off += 16
    return verts, polys

def iso_project(x, y, z, xc, yc):
    sx = ((x - z) * 24) >> 9
    sy = ((x + z) * 12 - y * 30) >> 9
    return sx + xc, sy + yc

def in_viewport(sx, sy):
    return 0 <= sx < VIEWPORT_W and 0 <= sy < VIEWPORT_H

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--body', type=int, default=0)
    ap.add_argument('--xc', type=int, default=160)
    ap.add_argument('--yc', type=int, default=100)
    ap.add_argument('--in-dir', default=lba_paths.repo(r'extracted\BODY_PACK'))
    args = ap.parse_args()

    path = os.path.join(args.in_dir, f'body_{args.body:03d}.bin')
    verts, polys = parse_packed_body(path)
    print(f"=== body {args.body} ===")
    print(f"  verts: {len(verts)}  polys: {len(polys)}")
    print(f"  anchor: ({args.xc}, {args.yc})  viewport: {VIEWPORT_W}x{VIEWPORT_H}")

    # Project all verts
    proj = [iso_project(x, y, z, args.xc, args.yc) for (x, y, z) in verts]

    sx_arr = [p[0] for p in proj]
    sy_arr = [p[1] for p in proj]
    print(f"\n[verts]")
    print(f"  sx range: [{min(sx_arr)}..{max(sx_arr)}]  span={max(sx_arr)-min(sx_arr)}")
    print(f"  sy range: [{min(sy_arr)}..{max(sy_arr)}]  span={max(sy_arr)-min(sy_arr)}")
    n_v_in = sum(1 for p in proj if in_viewport(*p))
    print(f"  in-viewport: {n_v_in}/{len(verts)}  ({100*n_v_in/len(verts):.1f}%)")

    # Per-poly classification
    poly_full_in = 0
    poly_full_out = 0
    poly_partial = 0
    nbp_dist = Counter()
    mat_bucket = Counter()
    for p in polys:
        nbp_dist[p['nbp']] += 1
        m = p['mat']
        bucket = ('Pattern' if m < 7 else 'Flat' if m < 9 else 'Gouraud')
        mat_bucket[bucket] += 1
        v_in = sum(1 for vi in p['vidx'] if in_viewport(*proj[vi]))
        if v_in == p['nbp']:
            poly_full_in += 1
        elif v_in == 0:
            poly_full_out += 1
        else:
            poly_partial += 1

    total = len(polys)
    print(f"\n[polys nbp dist]: {dict(sorted(nbp_dist.items()))}")
    print(f"[polys material bucket]: {dict(mat_bucket)}")
    print(f"\n[viewport classification]")
    print(f"  fully in:    {poly_full_in:>4}  ({100*poly_full_in/total:.1f}%)  -- dispatchable")
    print(f"  partially:   {poly_partial:>4}  ({100*poly_partial/total:.1f}%)  -- need clip OR skip")
    print(f"  fully out:   {poly_full_out:>4}  ({100*poly_full_out/total:.1f}%)  -- skip")

    # Per-edge slope analysis on FULLY-IN polys (ones we'd actually dispatch)
    n_edges_total = 0
    n_edges_horizontal = 0
    n_edges_slope_ok = 0          # |dx| < |dy| (constraint OK)
    n_edges_slope_violate = 0     # |dx| >= |dy| with dy != 0
    edge_slope_max = 0
    polys_with_violation = 0
    polys_dispatchable_full = 0   # fully-in AND no slope violation

    for p in polys:
        if any(not in_viewport(*proj[vi]) for vi in p['vidx']):
            continue
        nbp = p['nbp']
        violated = False
        for i in range(nbp):
            v0 = proj[p['vidx'][i]]
            v1 = proj[p['vidx'][(i+1) % nbp]]
            dx = abs(v1[0] - v0[0])
            dy = abs(v1[1] - v0[1])
            n_edges_total += 1
            if dy == 0:
                n_edges_horizontal += 1
            elif dx < dy:
                n_edges_slope_ok += 1
            else:
                n_edges_slope_violate += 1
                violated = True
                slope_int = dx // max(dy, 1)
                edge_slope_max = max(edge_slope_max, slope_int)
        if violated:
            polys_with_violation += 1
        else:
            polys_dispatchable_full += 1

    fully_in_polys = poly_full_in
    print(f"\n[edge analysis on {fully_in_polys} fully-in polys]")
    print(f"  edges total:        {n_edges_total}")
    print(f"  horizontal (dy=0):  {n_edges_horizontal:>4}  ({100*n_edges_horizontal/max(1,n_edges_total):.1f}%)  -- skipped at edge build")
    print(f"  |dx|<|dy| OK:       {n_edges_slope_ok:>4}  ({100*n_edges_slope_ok/max(1,n_edges_total):.1f}%)  -- decomp valid")
    print(f"  |dx|>=|dy| violate: {n_edges_slope_violate:>4}  ({100*n_edges_slope_violate/max(1,n_edges_total):.1f}%)  -- decomp WRONG (truncated)")
    print(f"  worst slope int:    {edge_slope_max} (edges with |dx|/|dy| up to this)")

    print(f"\n[fully-in polys]")
    print(f"  any-edge violation: {polys_with_violation:>4}  ({100*polys_with_violation/max(1,fully_in_polys):.1f}%)  -- would render WRONG")
    print(f"  fully dispatchable: {polys_dispatchable_full:>4}  ({100*polys_dispatchable_full/max(1,fully_in_polys):.1f}%)  -- safe to ship in step 1")

    # Summary verdict
    print(f"\n[verdict]")
    pct_violate = 100 * n_edges_slope_violate / max(1, n_edges_total)
    pct_poly_bad = 100 * polys_with_violation / max(1, fully_in_polys)
    if pct_poly_bad < 10:
        print(f"  Slope violation acceptable ({pct_poly_bad:.1f}% of dispatchable polys).")
        print(f"  Step 1 ottimista safe: ship DrawBodyFlat unchanged, fix later.")
    elif pct_poly_bad < 35:
        print(f"  Slope violation moderate ({pct_poly_bad:.1f}% of polys).")
        print(f"  Step 1 will show visible glitches; budget for step 1.5 fix.")
    else:
        print(f"  Slope violation severe ({pct_poly_bad:.1f}% of polys).")
        print(f"  Step 1.5 (extend inner loop for |slope|>=1) MANDATORY before F4.3-alpha test.")

if __name__ == '__main__':
    main()
