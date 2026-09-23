#!/usr/bin/env python3
"""F4.2c-2 iter 1 verify -- edge build + scan outer ground truth.

Test triangle interno al viewport (no clip needed):
  (50, 50), (270, 50), (160, 180)

Output:
  - Edge records (Q16.16 hex) bit-exact, post-sort y0<y1, post-skip horizontal
  - Per-scanline table: Y_int, x0_q, x1_q (Q16.16 raw), x_l_int, x_r_int (top-left)
  - Hline call summary: hline count, total pixels, bbox

Used as ground truth memdump cross-check for S12 GPU asm GpuRasterMain.

Design notes:
  - dx_dy_q computed two ways:
      Python (default):  (dx << 16) // dy          — signed floor division.
      GPU mode:          |dx| / |dy|  then negate  — matches GPU TOM div
                         (G_DIVCTRL=1 unsigned 16.16 mode + external sign tracker
                          via abs+neg, canonical: feedback_gpu_q1616_mult.md).
    For dx<0 these differ by 1 LSB (Python floor rounds toward -inf, GPU rounds
    toward 0). Use --gpu-mode to dump the bit-exact ground truth for memdump
    cross-check against GpuRasterMain.
  - Y_q = (Y_int << 16) | HALF (pixel center 0.5).
  - x_q = x0_q + ((Y_q - y0_q) * dx_dy_q) >> 16. Mult is Q16.16*Q16.16 -> 64-bit,
    shift produces Q16.16 result. GPU TOM: imult bit15-trap-aware mult unsigned
    + sign tracker (canonical: feedback_gpu_q1616_mult.md).
  - Active edge test: y0_q <= Y_q < y1_q (half-open). Convex => exactly 2 edges.
  - Top-left integer rounding:
      x_l_int = (x_l_q + (HALF-1)) >> 16
      x_r_int = ((x_r_q + (HALF-1)) >> 16) - 1
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from poly_clip_prep import (
    ONE, HALF, to_q, from_q,
    VIEWPORT_XMIN, VIEWPORT_XMAX, VIEWPORT_YMIN, VIEWPORT_YMAX,
)
from poly_fill_prep import build_edges, _ceil_minus_half

POLY_INT = [(50, 50), (270, 50), (160, 180)]
COLOR = 200


def build_edges_gpu(poly_q):
    """GPU TOM semantics: dx_dy_q = (|dx|<<16)//|dy| then negate if dx<0.
    Differs from Python `(dx<<16)//dy` by 1 LSB when dx<0 (floor vs truncate).
    Returns same shape as build_edges: list of (y0_q, y1_q, x0_q, dx_dy_q).
    Used as bit-exact ground truth for GPU memdump cross-check."""
    edges = []
    n = len(poly_q)
    if n < 3:
        return edges
    for i in range(n):
        x0, y0 = poly_q[i]
        x1, y1 = poly_q[(i + 1) % n]
        if y0 == y1:
            continue
        if y0 > y1:
            x0, y0, x1, y1 = x1, y1, x0, y0
        dy = y1 - y0           # always > 0 post-swap
        dx = x1 - x0           # signed
        sign_neg = (dx < 0)
        adx = -dx if sign_neg else dx
        slope = (adx << 16) // dy        # unsigned div (positive), truncates toward 0
        if sign_neg:
            slope = -slope                # signed Python int; hex32() masks at display
        edges.append((y0, y1, x0, slope))
    return edges


def hex32(v):
    return f"${v & 0xFFFFFFFF:08X}"


def main():
    gpu_mode = ('--gpu-mode' in sys.argv)
    print("=== F4.2c-2 iter 1 verify (edge build + scan outer) ===")
    print(f"  Test poly (int): {POLY_INT}")
    print(f"  VIEWPORT: x in [{VIEWPORT_XMIN}..{VIEWPORT_XMAX}], "
          f"y in [{VIEWPORT_YMIN}..{VIEWPORT_YMAX}]")
    print(f"  Color: {COLOR}")
    print(f"  Mode: {'GPU TOM (|dx|/|dy| + neg)' if gpu_mode else 'Python (signed floor)'}")
    print()

    poly_q = [(to_q(x), to_q(y)) for (x, y) in POLY_INT]

    # --- Edge build ---
    edges_py  = build_edges(poly_q)
    edges_gpu = build_edges_gpu(poly_q)
    edges = edges_gpu if gpu_mode else edges_py
    print(f"  Edge records (post-sort y0<y1, post-skip horizontal): {len(edges)}")
    print(f"  {'idx':>3}  {'y0_q':>10}  {'y1_q':>10}  {'x0_q':>10}  "
          f"{'dx_dy_py':>10}  {'dx_dy_gpu':>10}  diff")
    for i, ((y0, y1, x0, dxdy_py), (_, _, _, dxdy_gpu)) in enumerate(zip(edges_py, edges_gpu)):
        diff = ((dxdy_gpu - dxdy_py) & 0xFFFFFFFF)
        # Show as signed delta in [-2..+2] LSB range for readability
        if diff > 0x7FFFFFFF:
            diff -= 0x100000000
        marker = "" if diff == 0 else f" ({diff:+d} LSB)"
        print(f"    {i:>3}  {hex32(y0):>10}  {hex32(y1):>10}  {hex32(x0):>10}  "
              f"{hex32(dxdy_py):>10}  {hex32(dxdy_gpu):>10}  {marker}")
        # Check: edge for horizontal (50,50)->(270,50) skipped.
        # E1 ((270,50)->(160,180)): y0=50, y1=180, x0=270, dx_dy ~ -0.846
        # E2 ((160,180)->(50,50)): swap -> y0=50, y1=180, x0=50, dx_dy ~ +0.846
    print()
    if gpu_mode:
        print("  GPU memdump expected at GPU_RASTER_EDGE_BUF ($2500):")
        for i, (y0, y1, x0, dxdy) in enumerate(edges_gpu):
            base = 0x2500 + i * 16
            print(f"    ${base:04X}: {hex32(y0)} {hex32(y1)} {hex32(x0)} {hex32(dxdy)}")
        print(f"  GPU_RASTER_EDGE_COUNT ($24E8) expected = {len(edges_gpu)}")
        print()

        # F4.2c-2b: scan-outer expected dump (first 6 scanlines, GPU semantics).
        # GPU computes per-edge x_q via: K = (Y_q - y0_q) >> 16,
        # contrib = K * slope_q + (slope_q >> 1),  x_q = x0_q + contrib.
        # Decomposition valid because Y_q has lower16=HALF and y0_q is
        # integer-aligned for this test triangle.
        print("  GPU memdump expected at GPU_RASTER_SCAN_BUF ($2580):")
        ymin_int = min(_ceil_minus_half(y0) for (y0, _, _, _) in edges_gpu)
        for k in range(6):
            y_int = ymin_int + k
            y_q = (y_int << 16) | HALF
            xs = []
            for (y0, y1, x0, dxdy) in edges_gpu:
                if y0 <= y_q < y1:
                    K = (y_q - y0) >> 16
                    # GPU: mult unsigned + neg + sharq #1
                    sign_neg = (dxdy < 0)
                    aslope = -dxdy if sign_neg else dxdy
                    prod = (K * (aslope & 0xFFFF)) & 0xFFFFFFFF
                    if sign_neg:
                        prod = (-prod) & 0xFFFFFFFF
                    if prod & 0x80000000:
                        prod -= 0x100000000
                    frac = dxdy >> 1            # signed arith shift in Python
                    contrib = prod + frac
                    x_q = (x0 + contrib) & 0xFFFFFFFF
                    if x_q & 0x80000000:
                        x_q_signed = x_q - 0x100000000
                    else:
                        x_q_signed = x_q
                    xs.append(x_q_signed)
            xs.sort()
            base = 0x2580 + k * 8
            if len(xs) >= 2:
                xl, xr = xs[0], xs[1]
                print(f"    ${base:04X}: {hex32(xl)} {hex32(xr)}    "
                      f"(Y={y_int})")
            else:
                print(f"    ${base:04X}: <only {len(xs)} active>")
        print()

    # --- Scan outer ---
    ymin_int = min(_ceil_minus_half(y0) for (y0, _, _, _) in edges)
    ymax_int = max(_ceil_minus_half(y1) - 1 for (_, y1, _, _) in edges)
    print(f"  Scan range: Y_int in [{ymin_int}..{ymax_int}] "
          f"({ymax_int - ymin_int + 1} scanlines)")
    print()

    print(f"  Per-scanline (showing 6 first + 6 last; full list = {ymax_int - ymin_int + 1} rows):")
    print(f"    {'Y':>3}  {'Y_q':>10}  "
          f"{'xl_q':>10}  {'xr_q':>10}  "
          f"{'xl':>3}  {'xr':>3}  {'len':>3}")
    rows = []
    total_pixels = 0
    hline_calls = 0
    for y_int in range(ymin_int, ymax_int + 1):
        y_q = (y_int << 16) | HALF
        xs = []
        for (y0, y1, x0, dxdy) in edges:
            if y0 <= y_q < y1:
                x_q = x0 + ((y_q - y0) * dxdy >> 16)
                xs.append(x_q)
        if len(xs) < 2:
            rows.append((y_int, y_q, None, None, None, None, 0))
            continue
        xs.sort()
        xl_q, xr_q = xs[0], xs[1]
        xl = _ceil_minus_half(xl_q)
        xr = _ceil_minus_half(xr_q) - 1
        if xl < VIEWPORT_XMIN: xl = VIEWPORT_XMIN
        if xr > VIEWPORT_XMAX: xr = VIEWPORT_XMAX
        if xl > xr:
            rows.append((y_int, y_q, xl_q, xr_q, xl, xr, 0))
            continue
        n = xr - xl + 1
        total_pixels += n
        hline_calls += 1
        rows.append((y_int, y_q, xl_q, xr_q, xl, xr, n))

    head_n = 6
    tail_n = 6
    show = list(range(head_n)) + list(range(len(rows) - tail_n, len(rows)))
    show = sorted(set(show))
    last_idx = -1
    for idx in show:
        if last_idx != -1 and idx > last_idx + 1:
            print(f"    ...")
        y_int, y_q, xl_q, xr_q, xl, xr, n = rows[idx]
        if xl_q is None:
            print(f"    {y_int:>3}  {hex32(y_q):>10}   (no active edges)")
        else:
            print(f"    {y_int:>3}  {hex32(y_q):>10}  "
                  f"{hex32(xl_q):>10}  {hex32(xr_q):>10}  "
                  f"{xl:>3}  {xr:>3}  {n:>3}")
        last_idx = idx
    print()

    # --- Top-of-triangle special check ---
    print("  Top scanline check (Y=50, narrowest part):")
    for r in rows:
        if r[0] == 50:
            print(f"    Y=50: xl_q={hex32(r[2])} xr_q={hex32(r[3])}  "
                  f"xl_int={r[4]} xr_int={r[5]} (len={r[6]})")
            break
    print()

    print("  Bottom scanline check (Y=179, widest part):")
    for r in rows:
        if r[0] == 179:
            print(f"    Y=179: xl_q={hex32(r[2])} xr_q={hex32(r[3])}  "
                  f"xl_int={r[4]} xr_int={r[5]} (len={r[6]})")
            break
    print()

    # --- BBox + total ---
    nonempty = [r for r in rows if r[6] > 0]
    if nonempty:
        ymin_filled = nonempty[0][0]
        ymax_filled = nonempty[-1][0]
        xmin_filled = min(r[4] for r in nonempty)
        xmax_filled = max(r[5] for r in nonempty)
        print(f"  Summary:")
        print(f"    hline calls       : {hline_calls}")
        print(f"    total pixels      : {total_pixels}")
        print(f"    fill bbox (int)   : ({xmin_filled}, {ymin_filled}) - ({xmax_filled}, {ymax_filled})")
        print(f"    bbox size         : {xmax_filled - xmin_filled + 1} x "
              f"{ymax_filled - ymin_filled + 1}")
    else:
        print("  No pixels filled.")
    print()

    # --- Cross-reference: what should GPU TOM produce ---
    print("  GPU asm S12 expectation:")
    print(f"    - {len(edges)} edge records to build/store (skip horizontal between top verts)")
    print(f"    - Slope div per edge: {len(edges)} GPU div")
    print(f"    - Y_int loop: {ymax_int - ymin_int + 1} iterations "
          f"({ymin_int}..{ymax_int})")
    print(f"    - Per Y: 2 active edges, 2 mult Q16.16 (sign-aware), 1 sort, 1 hline call")
    print(f"    - Hline_fill total dispatches: {hline_calls}")
    print(f"    - Total pixels filled: {total_pixels} (color {COLOR})")
    print()
    print("  S12 GPU asm validation strategy:")
    print("    1. Edge records dump in DRAM at known addr -> compare hex bit-exact above")
    print("    2. Per-scanline xl/xr trace (optional first 6 rows in scratch DRAM) for diff")
    print("    3. Final visual: filled triangle on BG_A (~ {} px tall, ~{} px base)".format(
        ymax_filled - ymin_filled + 1 if nonempty else 0,
        xmax_filled - xmin_filled + 1 if nonempty else 0))


if __name__ == '__main__':
    main()
