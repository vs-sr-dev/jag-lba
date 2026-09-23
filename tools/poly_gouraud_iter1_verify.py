#!/usr/bin/env python3
"""S14 A3 / F4.4-iter1: gouraud raster ground truth for single triangle.

Same triangle as F4.2c (S12) iter 1 verify -- (50,50), (270,50), (160,180) --
plus per-corner intensities (0, 8, 15) in 4-bit shade range mapped to Q16.16.

Extends poly_raster_iter1_verify.py with:
  - Edge build: adds i0_q + di_dy_q per edge (intensity slope along Y).
  - Scan outer: adds i_l_q + i_r_q per scanline (intensity at left/right).
  - Hline:     emits per-pixel intensity_q + final shade_idx (=intensity >> 16).

Used as bit-exact ground truth for GPU TOM GpuRasterGouraud (C2 + C3 + C4).
The Jaguar memdump after each phase is shown explicitly under --gpu-mode for
direct comparison with GPU_RASTER_EDGE_BUF + GPU_RASTER_SCAN_BUF + FB writes.

Format choices:
  - Intensity stored Q16.16 (low 16 = HALF for pixel center on Y, scan-outer-style).
  - Slopes di_dy: max |slope| = (15-0)/(180-50) = 0.115. Bit15 of lower16 unset.
                  No imult bit15 trap; mult unsigned single-instruction valid.
                  (Compare to dx_dy slope in S12 = ~0.846 -> same regime.)
  - Slopes di_dx (per-scanline): even smaller; safe.
  - Final per-pixel shade: clut_idx = coul1 + (intensity_q >> 16). Clamped to
                           ramp boundary [coul1 & 0xF0 .. coul1 & 0xF0 + 15].
"""

import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from poly_clip_prep import (
    ONE, HALF, to_q, from_q,
    VIEWPORT_XMIN, VIEWPORT_XMAX, VIEWPORT_YMIN, VIEWPORT_YMAX,
)
from poly_fill_prep import _ceil_minus_half

# Triangle corners and per-corner intensity (4-bit shade 0..15).
CORNERS = [
    (50,  50,  0),    # top-left, dark
    (270, 50,  8),    # top-right, mid
    (160, 180, 15),   # bottom, full bright
]
COUL1 = 66            # ramp 4 [64..79], offset 2 (typical body 0 gouraud poly)


def hex32(v):
    return f"${v & 0xFFFFFFFF:08X}"


def build_edges_gouraud_gpu(corners_q):
    """Edge build with intensity slope. Each edge: (y0_q, y1_q, x0_q, dx_dy_q,
    i0_q, di_dy_q). All Q16.16, dx_dy/di_dy use GPU TOM semantics (unsigned div
    + sign tracker via abs+neg, matching feedback_gpu_q1616_mult.md).
    """
    edges = []
    n = len(corners_q)
    for i in range(n):
        x0, y0, i0 = corners_q[i]
        x1, y1, i1 = corners_q[(i + 1) % n]
        if y0 == y1:
            continue
        if y0 > y1:
            x0, y0, i0, x1, y1, i1 = x1, y1, i1, x0, y0, i0
        dy = y1 - y0
        # dx slope (S12 pattern: |dx|<<16 / |dy|, neg if dx<0)
        dx = x1 - x0
        sign_dx = (dx < 0)
        adx = -dx if sign_dx else dx
        dx_dy = (adx << 16) // dy
        if sign_dx: dx_dy = -dx_dy
        # intensity slope (same pattern, |di|<<16 / |dy|, neg if di<0)
        di = i1 - i0
        sign_di = (di < 0)
        adi = -di if sign_di else di
        di_dy = (adi << 16) // dy
        if sign_di: di_dy = -di_dy
        edges.append((y0, y1, x0, dx_dy, i0, di_dy))
    return edges


def gpu_mult_q1616(K, slope):
    """Replicate GPU TOM Q16.16 mult: K * slope using unsigned mult + sign tracker.
    K is integer >= 0. slope is signed Q16.16 (bit31 = sign).
    Returns prod (signed Q16.16, K * slope).
    """
    sign_neg = (slope < 0)
    aslope = -slope if sign_neg else slope
    # Split lo/hi for |slope| (S13 lo/hi extension).
    lo = aslope & 0xFFFF
    hi = (aslope >> 16) & 0xFFFF
    prod = (K * lo) + ((K * hi) << 16)
    prod &= 0xFFFFFFFF
    if sign_neg:
        prod = (-prod) & 0xFFFFFFFF
    if prod & 0x80000000:
        prod -= 0x100000000
    return prod


def main():
    gpu_mode = ('--gpu-mode' in sys.argv)
    print("=== F4.4 iter 1 verify (gouraud edge build + scan outer + hline) ===")
    print(f"  Triangle: {[(x,y) for (x,y,i) in CORNERS]}")
    print(f"  Per-corner intensity (4-bit): {[i for (x,y,i) in CORNERS]}")
    print(f"  Coul1: {COUL1} (ramp {COUL1 // 16}, offset {COUL1 & 0x0F})")
    print(f"  VIEWPORT: x [{VIEWPORT_XMIN}..{VIEWPORT_XMAX}], "
          f"y [{VIEWPORT_YMIN}..{VIEWPORT_YMAX}]")
    print(f"  Mode: {'GPU TOM bit-exact' if gpu_mode else 'Python reference'}")
    print()

    corners_q = [(to_q(x), to_q(y), to_q(i)) for (x, y, i) in CORNERS]
    edges = build_edges_gouraud_gpu(corners_q)

    # ---- Edge dump ----
    print(f"  Edge records (post-sort y0<y1, post-skip horizontal): {len(edges)}")
    print(f"  {'idx':>3}  {'y0_q':>10}  {'y1_q':>10}  {'x0_q':>10}  "
          f"{'dx_dy_q':>10}  {'i0_q':>10}  {'di_dy_q':>10}")
    for i, (y0, y1, x0, dxdy, i0, didy) in enumerate(edges):
        print(f"    {i:>3}  {hex32(y0):>10}  {hex32(y1):>10}  {hex32(x0):>10}  "
              f"{hex32(dxdy):>10}  {hex32(i0):>10}  {hex32(didy):>10}")
    print()

    if gpu_mode:
        # GPU EDGE_BUF entries are 24 bytes each (vs 16 in S12 flat):
        # +0 y0_q  +4 y1_q  +8 x0_q  +12 dx_dy  +16 i0_q  +20 di_dy
        print("  GPU memdump expected at GPU_RASTER_EDGE_BUF ($2500), 24 B/edge:")
        for i, (y0, y1, x0, dxdy, i0, didy) in enumerate(edges):
            base = 0x2500 + i * 24
            print(f"    ${base:04X}: y0={hex32(y0)} y1={hex32(y1)}")
            print(f"    ${base+8:04X}: x0={hex32(x0)} dxdy={hex32(dxdy)}")
            print(f"    ${base+16:04X}: i0={hex32(i0)} didy={hex32(didy)}")
        print()

    # ---- Scan outer ----
    ymin_int = min(_ceil_minus_half(y0) for (y0,_,_,_,_,_) in edges)
    ymax_int = max(_ceil_minus_half(y1) - 1 for (_,y1,_,_,_,_) in edges)
    print(f"  Scan range: Y_int [{ymin_int}..{ymax_int}] "
          f"({ymax_int - ymin_int + 1} scanlines)")
    print()

    print(f"  Per-scanline (showing first 6 + last 6):")
    print(f"    {'Y':>3}  {'xl_q':>10}  {'xr_q':>10}  "
          f"{'il_q':>10}  {'ir_q':>10}  {'didx_q':>10}  "
          f"{'xl':>3}  {'xr':>3}  {'shadeL':>6}  {'shadeR':>6}")

    ramp_base = COUL1 & 0xF0
    coul1_off = COUL1 & 0x0F

    rows = []
    for y_int in range(ymin_int, ymax_int + 1):
        y_q = (y_int << 16) | HALF
        # For each active edge: x_q + i_q at this scanline.
        # Use decomposition K*slope + slope/2 (S12) since y0_q is integer-aligned
        # for this triangle and Y_q has lower16 = HALF.
        active = []
        for (y0, y1, x0, dxdy, i0, didy) in edges:
            if y0 <= y_q < y1:
                K = (y_q - y0) >> 16
                if gpu_mode:
                    x_contrib = gpu_mult_q1616(K, dxdy) + (dxdy >> 1)
                    i_contrib = gpu_mult_q1616(K, didy) + (didy >> 1)
                else:
                    x_contrib = ((y_q - y0) * dxdy) >> 16
                    i_contrib = ((y_q - y0) * didy) >> 16
                x_q = x0 + x_contrib
                i_q = i0 + i_contrib
                active.append((x_q, i_q))
        if len(active) < 2:
            rows.append((y_int, None))
            continue
        active.sort(key=lambda a: a[0])
        (xl_q, il_q), (xr_q, ir_q) = active[0], active[1]
        xl = _ceil_minus_half(xl_q)
        xr = _ceil_minus_half(xr_q) - 1
        if xl < VIEWPORT_XMIN: xl = VIEWPORT_XMIN
        if xr > VIEWPORT_XMAX: xr = VIEWPORT_XMAX
        if xl > xr:
            rows.append((y_int, None))
            continue
        # intensity step per pixel: (ir - il) / (xr - xl + 1) Q16.16
        span = xr - xl + 1
        if span > 0:
            di_total = ir_q - il_q
            sign_neg = (di_total < 0)
            adi = -di_total if sign_neg else di_total
            didx_q = adi // span
            if sign_neg: didx_q = -didx_q
        else:
            didx_q = 0
        # final shades at left and right pixel
        shadeL = max(0, min(15, il_q >> 16))
        shadeR = max(0, min(15, (il_q + didx_q * (span - 1)) >> 16))
        rows.append((y_int, (xl_q, xr_q, il_q, ir_q, didx_q, xl, xr, shadeL, shadeR)))

    head_n = 6
    tail_n = 6
    show_idx = sorted(set(list(range(head_n)) + list(range(len(rows) - tail_n, len(rows)))))
    last = -1
    for idx in show_idx:
        if last != -1 and idx > last + 1:
            print(f"    ...")
        y_int, payload = rows[idx]
        if payload is None:
            print(f"    {y_int:>3}  (no active edges or empty span)")
        else:
            xl_q, xr_q, il_q, ir_q, didx_q, xl, xr, sL, sR = payload
            print(f"    {y_int:>3}  {hex32(xl_q):>10}  {hex32(xr_q):>10}  "
                  f"{hex32(il_q):>10}  {hex32(ir_q):>10}  {hex32(didx_q):>10}  "
                  f"{xl:>3}  {xr:>3}  {sL:>6}  {sR:>6}")
        last = idx
    print()

    # ---- Hline pixel emit ----
    # Show first 4 pixels of first 4 fully-active scanlines for FB-write validation.
    print(f"  Hline first-4-pixels of first-4-active-scanlines:")
    print(f"    {'Y':>3}  {'X':>3}  {'intensity_q':>11}  {'shade':>5}  "
          f"{'clut_idx':>8}  (= ramp_base {ramp_base} + offset)")
    n_shown = 0
    total_pixels = 0
    for y_int, payload in rows:
        if payload is None: continue
        xl_q, xr_q, il_q, ir_q, didx_q, xl, xr, sL, sR = payload
        span = xr - xl + 1
        total_pixels += span
        if n_shown >= 4: continue
        n_shown += 1
        for k in range(min(4, span)):
            iq = il_q + didx_q * k
            shade = max(0, min(15, iq >> 16))
            idx = ramp_base + max(0, min(15, coul1_off + shade))
            print(f"    {y_int:>3}  {xl + k:>3}  {hex32(iq):>11}  {shade:>5}  "
                  f"{idx:>8}")
    print()
    print(f"  Total pixels in fill: {total_pixels}")


if __name__ == '__main__':
    main()
