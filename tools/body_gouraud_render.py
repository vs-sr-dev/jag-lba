#!/usr/bin/env python3
"""S14 A2: CPU gouraud renderer for body 0 -> reference PNG.

Pipeline:
  1. Parse body via body_lighting.parse_body (with the correct normal_idx ordering)
  2. Bake per-corner intensity (cosine or lba_range mode)
  3. Iso-project vertices to screen
  4. Painter sort polys by sum(Z) of first 3 corners
  5. Rasterize gouraud per poly: scanline with linear (x, intensity) interpolation
  6. CLUT lookup per pixel: clut_idx = coul1 + intensity_4bit, clamped to ramp
  7. Save PNG via PIL using LBA1 RESS_PAL slot 0 palette

Used by D3 (visual back-to-back vs Jaguar). Renders both flat and gouraud bucket.
Pattern bucket (mat 0..6) drawn flat with coul1 only (~2.3% of all body polys).
"""

import os, sys, math, struct, argparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PIL import Image
from hqr_decompress import parse_offsets, extract_entry
from render_slide import normalize_palette
from body_lighting import (
    parse_body, bake_body_intensity, DEFAULT_NORMAL_LIGHT,
)
from body_screen_stats import iso_project


WIDTH = 320
HEIGHT = 200


def project_verts(verts, xc, yc):
    return [iso_project(x, y, z, xc, yc) for (x, y, z) in verts]


def painter_key(poly, verts_world):
    """Sum of (X+Y+Z) over first 3 corners. Matches scene_viewer.s S13 build 4
    iso depth proxy: in dimetric projection all 3 axes contribute equally
    to depth-from-camera, so X+Y+Z is the canonical sort key."""
    s = 0
    for k in range(min(3, len(poly['corners']))):
        v_idx = poly['corners'][k][0]
        x, y, z = verts_world[v_idx]
        s += x + y + z
    return s


def cross_2d(ax, ay, bx, by, cx, cy):
    """2D cross of (B-A) x (C-A). >0 = CCW, <0 = CW (in math convention).
    With Y-down screen: <0 = front-facing in LBA1 winding."""
    return (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)


def rasterize_gouraud(canvas, x_arr, y_arr, i_arr, coul1, ramp_clamp=True):
    """Fill a convex N-gon with gouraud-shaded intensities.
    canvas: 2D list-of-lists [HEIGHT][WIDTH] of palette indices (overwrite).
    x_arr, y_arr: integer screen coords per corner (already projected).
    i_arr: float intensity per corner (in 0..15 range, can be 0..15.99).
    coul1: starting CLUT index for the poly's ramp (8-bit).
    ramp_clamp: if True, clamp clut_idx to coul1's 16-shade ramp boundary
                so we never spill into adjacent ramp.
    """
    n = len(x_arr)
    if n < 3:
        return

    y_min = max(0, min(y_arr))
    y_max = min(HEIGHT - 1, max(y_arr))
    if y_min > y_max:
        return

    ramp_base = coul1 & 0xF0
    coul1_off = coul1 & 0x0F

    for y in range(y_min, y_max + 1):
        # Find edge crossings at this scanline (linear interp on edges that span y).
        crossings = []  # list of (x_at_y, intensity_at_y)
        for e in range(n):
            x0, y0, i0 = x_arr[e], y_arr[e], i_arr[e]
            x1, y1, i1 = x_arr[(e+1) % n], y_arr[(e+1) % n], i_arr[(e+1) % n]
            # Process edge if scanline y is between y0 and y1.
            # Standard trick: include lower endpoint, exclude upper, to avoid double-count.
            ya, yb = (y0, y1) if y0 < y1 else (y1, y0)
            if y < ya or y >= yb:
                continue
            if y0 == y1:
                continue  # horizontal edge; handled by neighbours
            t = (y - y0) / (y1 - y0)
            x_at = x0 + t * (x1 - x0)
            i_at = i0 + t * (i1 - i0)
            crossings.append((x_at, i_at))

        if len(crossings) < 2:
            continue
        crossings.sort()

        # Pair-wise fill (works for convex; for non-convex it does even-odd fill).
        for k in range(0, len(crossings) - 1, 2):
            x_left, i_left = crossings[k]
            x_right, i_right = crossings[k+1]
            xL = max(0, int(round(x_left)))
            xR = min(WIDTH - 1, int(round(x_right)))
            if xR < xL:
                continue
            span = max(1, xR - xL)
            di = (i_right - i_left) / span
            row = canvas[y]
            for xi, x in enumerate(range(xL, xR + 1)):
                intensity = i_left + di * xi
                shade = int(round(intensity))
                if shade < 0: shade = 0
                if shade > 15: shade = 15
                idx = coul1_off + shade
                if ramp_clamp:
                    if idx < 0:    idx = 0
                    if idx > 15:   idx = 15
                    clut_idx = ramp_base + idx
                else:
                    clut_idx = (coul1 + shade) & 0xFF
                row[x] = clut_idx


def render(body_path, body_idx, out_path, light, mode, xc, yc, do_cull):
    with open(body_path, 'rb') as f:
        data = f.read()
    offs = parse_offsets(data)
    payload = extract_entry(data, body_idx, offs)
    body = parse_body(payload)
    bake_body_intensity(body, light, mode=mode)
    proj = project_verts(body['verts'], xc, yc)

    # Painter sort polys (back-to-front).
    polys_sorted = sorted(enumerate(body['polys']),
                          key=lambda ip: painter_key(ip[1], body['verts']))

    canvas = [[0] * WIDTH for _ in range(HEIGHT)]

    n_culled = 0
    n_drawn = 0
    for orig_idx, p in polys_sorted:
        v_idxs = [c[0] for c in p['corners']]
        x_arr = [proj[v][0] for v in v_idxs]
        y_arr = [proj[v][1] for v in v_idxs]

        # Back-face cull on first 3 corners (matches scene_viewer.s S13 build 3).
        if do_cull and len(v_idxs) >= 3:
            cr = cross_2d(x_arr[0], y_arr[0], x_arr[1], y_arr[1], x_arr[2], y_arr[2])
            if cr >= 0:  # CCW math + Y-down screen -> front when cross < 0
                n_culled += 1
                continue

        if p['mat'] >= 9:  # gouraud: per-corner intensity
            i_arr = [float(s) for s in p['intensities']]
        elif p['mat'] >= 7:  # flat: single intensity replicated
            i_arr = [float(p['intensities'][0])] * len(v_idxs)
        else:  # pattern: no intensity (use 0 = darkest of ramp)
            i_arr = [0.0] * len(v_idxs)

        # Triangulate non-triangle polys: fan from v0. Body 0 has 20 quads;
        # pair-wise fill on quads that project non-convex produces "exploded"
        # artifacts (e.g. belt). Splitting into triangles is also what the
        # 68k dispatcher will need to do for GPU TOM (1 dispatch = 1 triangle).
        n = len(v_idxs)
        if n == 3:
            rasterize_gouraud(canvas, x_arr, y_arr, i_arr, p['coul1'], ramp_clamp=True)
        else:
            for k in range(1, n - 1):
                tx = [x_arr[0], x_arr[k], x_arr[k+1]]
                ty = [y_arr[0], y_arr[k], y_arr[k+1]]
                ti = [i_arr[0], i_arr[k], i_arr[k+1]]
                rasterize_gouraud(canvas, tx, ty, ti, p['coul1'], ramp_clamp=True)
        n_drawn += 1

    print(f"  drawn: {n_drawn}, culled: {n_culled}")

    # Convert canvas to PIL Image with palette mode.
    flat = bytes(b for row in canvas for b in row)
    img = Image.frombytes('P', (WIDTH, HEIGHT), flat)

    # Build palette: load RESS_PAL slot 0 from RESS.HQR.
    GOG = r'I:\GOG\Little Big Adventure\Common'
    with open(os.path.join(GOG, 'RESS.HQR'), 'rb') as f:
        ress = f.read()
    ress_offs = parse_offsets(ress)
    pal_raw = extract_entry(ress, 0, ress_offs)
    pal = normalize_palette(pal_raw)
    pal_flat = []
    for r, g, b in pal:
        pal_flat.extend([r, g, b])
    img.putpalette(pal_flat)

    img.save(out_path)
    print(f"  saved: {out_path}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--body-hqr', default=r'I:\GOG\Little Big Adventure\Common\BODY.HQR')
    ap.add_argument('--body-idx', type=int, default=0)
    ap.add_argument('--out-dir', default='build')
    ap.add_argument('--lx', type=float, default=0.5774)
    ap.add_argument('--ly', type=float, default=0.5774)
    ap.add_argument('--lz', type=float, default=0.5774)
    ap.add_argument('--mode', choices=['cosine', 'lba_range', 'both'], default='both')
    ap.add_argument('--xc', type=int, default=160)
    ap.add_argument('--yc', type=int, default=100)
    ap.add_argument('--no-cull', action='store_true', help="Disable back-face cull")
    args = ap.parse_args()

    light = (args.lx, args.ly, args.lz)
    mag = math.sqrt(sum(c*c for c in light))
    if abs(mag - 1.0) > 0.01:
        light = tuple(c / mag for c in light)

    os.makedirs(args.out_dir, exist_ok=True)

    modes = ['cosine', 'lba_range'] if args.mode == 'both' else [args.mode]
    for m in modes:
        out = os.path.join(args.out_dir, f'body{args.body_idx:03d}_gouraud_{m}.png')
        print(f"=== rendering body {args.body_idx} mode={m} ===")
        render(args.body_hqr, args.body_idx, out, light, m, args.xc, args.yc,
               do_cull=not args.no_cull)


if __name__ == '__main__':
    main()
