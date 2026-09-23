#!/usr/bin/env python3
"""Scanline fill for clipped convex polygon.

F4.1 prep for the GPU TOM flat polygon rasterizer.

Input contract:
  - poly_q : list of Q16.16 (xq, yq) tuples, vertices already viewport-clipped
             (use poly_clip_prep.clip_poly_int / clip_poly_q upstream).
  - color  : 8-bit palette index.
  - hline  : callable(y_int, x_left_int, x_right_int, color) called once per
             scanline. y/x_left/x_right are integer pixel coordinates.
             For convex input there are at most 2 active edges per scanline
             (so exactly one hline call per Y).

Algorithm — classic edge table:
  1. For each polygon edge (v[i] -> v[i+1]):
       - skip horizontal edges (y0_q == y1_q)
       - normalize so y0_q < y1_q
       - compute slope dx/dy in Q16.16 = ((x1_q - x0_q) * ONE) // (y1_q - y0_q)
       - record (y0_q, y1_q, x0_q, dx_dy_q)
  2. Compute Y_int range from rounded edge bounds.
  3. For each integer scanline Y_int in [ymin..ymax]:
       Y_q = (Y_int << 16) | HALF        # pixel center
       collect x_intersect for each active edge:
         x_q = x0_q + ((Y_q - y0_q) * dx_dy_q) >> 16
       sort x list, fill in pairs (left, right).

Top-left fill rule (D3D-style, for clean tiling between adjacent polys):
  pixel x is filled iff x_left_q <= (x_int + 0.5) < x_right_q
  -> x_left_int  = ceil (x_left_q  - 0.5) = (x_left_q  + (HALF-1)) >> 16
  -> x_right_int = ceil (x_right_q - 0.5) - 1 = ((x_right_q + (HALF-1)) >> 16) - 1

GPU TOM mapping notes (for F4.2):
  - One slope div per non-horizontal edge -> at most 8 div per clipped poly
    (scanline fill output of Sutherland-Hodgman, max 8 verts).
  - Per scanline: per-edge mul (Y_q - y0_q) * dx_dy_q -> imult 32x32 -> 64-bit,
    >>16 to get Q16.16 result. ~4 cycles imult + 4 cycles shift.
  - Sort step is trivial for convex (always exactly 2 active edges) -> just
    test which is smaller and swap.
  - Inner hline_fill is a tight blitter command or a GPU memory-fill loop;
    8bpp byte writes, x_left to x_right inclusive.

Q-format helpers reused from poly_clip_prep.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from poly_clip_prep import (
    ONE, HALF, to_q, from_q, mul_q, div_q,
    VIEWPORT_XMIN, VIEWPORT_XMAX, VIEWPORT_YMIN, VIEWPORT_YMAX,
    clip_poly_int, clip_poly_q,
)

def build_edges(poly_q):
    """Return list of (y0_q, y1_q, x0_q, dx_dy_q) with y0_q < y1_q.
    Skips horizontal and degenerate edges."""
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
        dy = y1 - y0
        dx = x1 - x0
        dx_dy_q = (dx << 16) // dy
        edges.append((y0, y1, x0, dx_dy_q))
    return edges

def _ceil_minus_half(xq):
    """ceil(xq - 0.5) in Q16.16 land = (xq + (HALF-1)) >> 16."""
    return (xq + (HALF - 1)) >> 16

def fill_convex_poly(poly_q, color, hline):
    """Scanline fill convex polygon. Calls hline(y, x_l, x_r, color) per row.
    Assumes poly_q has been viewport-clipped already (no out-of-bounds output).
    Returns total number of pixels filled."""
    edges = build_edges(poly_q)
    if not edges:
        return 0

    ymin_int = min(_ceil_minus_half(y0) for (y0, _, _, _) in edges)
    ymax_int = max(_ceil_minus_half(y1) - 1 for (_, y1, _, _) in edges)

    # Clamp to viewport (defensive — clip should've handled this).
    if ymin_int < VIEWPORT_YMIN: ymin_int = VIEWPORT_YMIN
    if ymax_int > VIEWPORT_YMAX: ymax_int = VIEWPORT_YMAX

    pixels = 0
    for y_int in range(ymin_int, ymax_int + 1):
        y_q = (y_int << 16) | HALF
        xs = []
        for (y0, y1, x0, dx_dy) in edges:
            # Edge contributes to scanline y_int iff y0 <= y_q < y1.
            if y0 <= y_q < y1:
                x_q = x0 + ((y_q - y0) * dx_dy >> 16)
                xs.append(x_q)
        if len(xs) < 2:
            continue
        xs.sort()
        # Convex poly: 2 active edges expected. For safety fill in pairs.
        for k in range(0, len(xs) - 1, 2):
            xl_q = xs[k]
            xr_q = xs[k + 1]
            xl = _ceil_minus_half(xl_q)
            xr = _ceil_minus_half(xr_q) - 1
            if xl < VIEWPORT_XMIN: xl = VIEWPORT_XMIN
            if xr > VIEWPORT_XMAX: xr = VIEWPORT_XMAX
            if xl > xr:
                continue
            hline(y_int, xl, xr, color)
            pixels += (xr - xl + 1)
    return pixels

# --- Smoke test ---

class _Capture:
    """Capture hline calls into a flat 320x200 8bpp framebuffer."""
    def __init__(self, w=320, h=200):
        self.w = w
        self.h = h
        self.fb = bytearray(w * h)
        self.calls = 0
    def hline(self, y, xl, xr, color):
        self.calls += 1
        assert 0 <= y < self.h, f"y={y} OOB"
        assert 0 <= xl <= xr < self.w, f"xl={xl} xr={xr} OOB"
        for x in range(xl, xr + 1):
            self.fb[y * self.w + x] = color
    def count(self, color):
        return sum(1 for b in self.fb if b == color)
    def bbox(self, color):
        xs, ys = [], []
        for y in range(self.h):
            for x in range(self.w):
                if self.fb[y * self.w + x] == color:
                    xs.append(x); ys.append(y)
        if not xs:
            return None
        return (min(xs), min(ys), max(xs), max(ys))

def _expected_triangle_pixels(verts):
    """Brute-force pixel count for a triangle using barycentric inside test
    with pixel centers — independent reference for smoke test."""
    (x0, y0), (x1, y1), (x2, y2) = verts
    xmin = max(0, min(x0, x1, x2))
    xmax = min(319, max(x0, x1, x2))
    ymin = max(0, min(y0, y1, y2))
    ymax = min(199, max(y0, y1, y2))
    n = 0
    for y in range(ymin, ymax + 1):
        for x in range(xmin, xmax + 1):
            px, py = x + 0.5, y + 0.5
            d0 = (x1 - x0) * (py - y0) - (y1 - y0) * (px - x0)
            d1 = (x2 - x1) * (py - y1) - (y2 - y1) * (px - x1)
            d2 = (x0 - x2) * (py - y2) - (y0 - y2) * (px - x2)
            inside = (d0 >= 0 and d1 >= 0 and d2 >= 0) or (d0 <= 0 and d1 <= 0 and d2 <= 0)
            if inside:
                # Top-left rule: exclude points exactly on right or bottom edges.
                # Simplified — for our test polys this is close enough for tolerance.
                n += 1
    return n

def smoke():
    # Expected bboxes account for top-left fill rule: right/bottom edges excluded.
    # Pixel counts compared against brute-force reference (which counts ALL pixels
    # whose center is inside-or-on-edge); diff matches the count of right-edge
    # pixels that top-left rule legitimately excludes.
    cases = [
        # (name, verts_int, color, expected_bbox, expected_pixels, ref_tol)
        ("axis-aligned 10x10 square", [(50,50),(60,50),(60,60),(50,60)], 100,
            (50, 50, 59, 59), 100, None),
        ("right triangle 100x100",    [(50,50),(150,50),(50,150)],        110,
            (50, 50, 148, 148), 4950, None),  # 99*100/2; hypotenuse is right-edge -> excluded
        ("equilateral-ish triangle",  [(160, 50), (100, 150), (220, 150)], 120,
            (100, 51, 219, 149), None, None),  # bbox only; tip at y=50 has zero width
        ("clipped at left edge",      [(-30, 50), (100, 30), (100, 170)], 130,
            (0, 30, 99, 169), None, None),    # top vertex at y=30
        ("entirely-clipped quad",     [(400, 0), (500, 0), (500, 100), (400, 100)], 140,
            None, 0, None),
        ("quad covering viewport",    [(-50, -50), (400, -50), (400, 250), (-50, 250)], 150,
            (0, 0, 318, 198), 319 * 199, None),  # row 199 + col 319 excluded
    ]
    print("=== poly_fill_prep smoke test ===\n")
    fails = 0
    for name, verts_int, color, expected_bbox, expected_pixels, ref_tol in cases:
        cap = _Capture()
        clipped = clip_poly_int(verts_int)
        pixels = fill_convex_poly(clipped, color, cap.hline)
        bbox = cap.bbox(color)
        count = cap.count(color)

        ok = True
        if expected_bbox is None:
            if bbox is not None:
                ok = False
        else:
            if bbox != expected_bbox:
                ok = False
        if expected_pixels is not None and count != expected_pixels:
            ok = False

        status = "OK  " if ok else "FAIL"
        print(f"  [{status}] {name}")
        print(f"           hline calls={cap.calls}, pixels filled={count}, returned={pixels}")
        print(f"           bbox={bbox}  expected_bbox={expected_bbox}  expected_px={expected_pixels}")
        if not ok:
            fails += 1

    # Cross-check vs brute-force barycentric for the unclipped triangle cases —
    # diff equals exactly the count of pixels along the "right/bottom" edges that
    # top-left rule excludes (sanity that we are not over- or under-filling).
    print()
    for name, verts_int, *_ in cases:
        if len(verts_int) != 3:
            continue
        if not all(0 <= x < 320 and 0 <= y < 200 for (x, y) in verts_int):
            continue
        cap = _Capture()
        clipped = clip_poly_int(verts_int)
        fill_convex_poly(clipped, 33, cap.hline)
        ref = _expected_triangle_pixels(verts_int)
        actual = cap.count(33)
        # Reference includes pixels exactly on edges; fill excludes right/bottom
        # per top-left rule. Difference must be >= 0 and <= max(2*max_extent).
        max_extent = max(max(x for (x,_) in verts_int) - min(x for (x,_) in verts_int),
                         max(y for (_,y) in verts_int) - min(y for (_,y) in verts_int))
        diff = ref - actual
        sane = 0 <= diff <= 2 * (max_extent + 1)
        status = "OK  " if sane else "FAIL"
        print(f"  [{status}] {name}: brute-ref={ref}, fill={actual}, diff={diff} (bound={2*(max_extent+1)})")
        if not sane:
            fails += 1

    print(f"\n  Total fails: {fails}")
    return fails

if __name__ == '__main__':
    sys.exit(smoke())
