#!/usr/bin/env python3
"""Sutherland-Hodgman 4-edge polygon clip against viewport.

F4.1 prep for the GPU TOM flat polygon rasterizer.

Q-format: Q16.16 signed fixed-point throughout.
  Maps cleanly to GPU TOM imult (32x32 -> 64-bit signed) + add/sub.
  GPU div is 32-cycle, used once per edge (slope reciprocal). All other
  per-pixel math is mul + add only.

Helpers:
  to_q(x)     int -> Q16.16              (x << 16)
  from_q(xq)  Q16.16 -> int (rounded)    ((xq + 0x8000) >> 16)
  mul_q(a,b)  Q16.16 * Q16.16 -> Q16.16  ((a * b) >> 16)
  div_q(n,d)  Q16.16 / Q16.16 -> Q16.16  ((n << 16) // d)

Viewport: 320 x 200 (LBA scene viewer fixed). Closed bounds [0..319] x [0..199].

Inside half-plane tests:
  EDGE_LEFT  : x_q >= XMIN_Q
  EDGE_RIGHT : x_q <= XMAX_Q
  EDGE_TOP   : y_q >= YMIN_Q
  EDGE_BOT   : y_q <= YMAX_Q

Sutherland-Hodgman per edge:
  in_count = N
  out = []
  for i in 0..N-1:
    S = poly[i]
    E = poly[(i+1) mod N]
    inS = inside(S, edge)
    inE = inside(E, edge)
    if inE:
      if not inS:
        out.append(intersect(S, E, edge))
      out.append(E)
    else:
      if inS:
        out.append(intersect(S, E, edge))

Worst-case output count after 4 edges:
  triangle (3) -> up to 7 verts
  quad     (4) -> up to 8 verts

GPU asm mapping notes (for F4.2):
  - Each clip pass reads one input vertex pair (S, E) and emits 0/1/2 output verts.
  - Working buffers in GPU local RAM: ping-pong (poly_a, poly_b) of 8 verts max
    each, 8 bytes/vert (xq long + yq long) = 64 bytes per buffer.
  - intersect() uses 1 div + 1 mul + 2 add. 4 edges * (up to N intersects each)
    means at most ~16 div per poly clipped from quad input (divs are the dominant
    cost — 32 cycles each on GPU TOM).
"""
import sys

ONE = 1 << 16
HALF = ONE >> 1

def to_q(x):
    """int -> Q16.16."""
    return int(x) << 16

def from_q(xq):
    """Q16.16 -> int (rounded to nearest)."""
    return (xq + HALF) >> 16

def mul_q(a, b):
    """Q16.16 * Q16.16 -> Q16.16."""
    return (a * b) >> 16

def div_q(num, den):
    """Q16.16 / Q16.16 -> Q16.16. Caller guarantees den != 0."""
    return (num << 16) // den

# Viewport — closed integer bounds
VIEWPORT_XMIN = 0
VIEWPORT_XMAX = 319
VIEWPORT_YMIN = 0
VIEWPORT_YMAX = 199

XMIN_Q = to_q(VIEWPORT_XMIN)
XMAX_Q = to_q(VIEWPORT_XMAX)
YMIN_Q = to_q(VIEWPORT_YMIN)
YMAX_Q = to_q(VIEWPORT_YMAX)

EDGE_LEFT  = 0
EDGE_RIGHT = 1
EDGE_TOP   = 2
EDGE_BOT   = 3
EDGE_NAMES = {EDGE_LEFT:'L', EDGE_RIGHT:'R', EDGE_TOP:'T', EDGE_BOT:'B'}

def inside(pt_q, edge):
    xq, yq = pt_q
    if edge == EDGE_LEFT:  return xq >= XMIN_Q
    if edge == EDGE_RIGHT: return xq <= XMAX_Q
    if edge == EDGE_TOP:   return yq >= YMIN_Q
    if edge == EDGE_BOT:   return yq <= YMAX_Q
    raise ValueError(edge)

def intersect(S_q, E_q, edge):
    """Compute Q16.16 intersection of segment S->E with `edge`'s line.
    Endpoints are assumed to straddle the edge (one inside, one outside)."""
    sx, sy = S_q
    ex, ey = E_q
    if edge == EDGE_LEFT or edge == EDGE_RIGHT:
        clip_x = XMIN_Q if edge == EDGE_LEFT else XMAX_Q
        den = ex - sx
        if den == 0:
            # Parallel to clip line — degenerate; return S unchanged.
            return (clip_x, sy)
        t_q = div_q(clip_x - sx, den)
        y_q = sy + mul_q(t_q, ey - sy)
        return (clip_x, y_q)
    else:
        clip_y = YMIN_Q if edge == EDGE_TOP else YMAX_Q
        den = ey - sy
        if den == 0:
            return (sx, clip_y)
        t_q = div_q(clip_y - sy, den)
        x_q = sx + mul_q(t_q, ex - sx)
        return (x_q, clip_y)

def clip_against_edge(poly_q, edge):
    """One Sutherland-Hodgman pass."""
    if not poly_q:
        return []
    out = []
    n = len(poly_q)
    for i in range(n):
        S = poly_q[i]
        E = poly_q[(i + 1) % n]
        inS = inside(S, edge)
        inE = inside(E, edge)
        if inE:
            if not inS:
                out.append(intersect(S, E, edge))
            out.append(E)
        else:
            if inS:
                out.append(intersect(S, E, edge))
    return out

def clip_poly_int(poly_int):
    """Clip poly given as list of (int x, int y) against viewport.
    Returns Q16.16 [(xq, yq), ...]; empty list = poly fully outside."""
    poly_q = [(to_q(x), to_q(y)) for (x, y) in poly_int]
    for edge in (EDGE_LEFT, EDGE_RIGHT, EDGE_TOP, EDGE_BOT):
        poly_q = clip_against_edge(poly_q, edge)
        if not poly_q:
            return []
    return poly_q

def clip_poly_q(poly_q):
    """Clip poly already in Q16.16 form."""
    out = list(poly_q)
    for edge in (EDGE_LEFT, EDGE_RIGHT, EDGE_TOP, EDGE_BOT):
        out = clip_against_edge(out, edge)
        if not out:
            return []
    return out

# --- Smoke test ---

def _format_q(p_q):
    return [(from_q(x), from_q(y)) for (x, y) in p_q]

def smoke():
    cases = [
        # (name, input_int, expected count range, expect_empty)
        ("triangle fully inside",        [(50, 50), (200, 50), (125, 150)],     (3, 3),  False),
        ("triangle straddling LEFT",     [(-50, 100), (100, 50), (100, 150)],   (3, 4),  False),
        ("triangle fully outside RIGHT", [(400, 50), (500, 100), (450, 150)],   (0, 0),  True),
        ("triangle two-edge cut",        [(-50, -50), (200, -50), (-50, 200)],  (3, 6),  False),
        ("quad encompassing viewport",   [(-50, -50), (400, -50), (400, 250), (-50, 250)], (4, 8), False),
        ("triangle vertex on left edge", [(0, 100), (200, 50), (200, 150)],     (3, 4),  False),
        ("degenerate segment",           [(50, 50), (200, 50), (50, 50)],       (0, 3),  False),
        ("triangle below viewport",      [(50, 250), (200, 250), (125, 300)],   (0, 0),  True),
    ]
    print("=== poly_clip_prep smoke test ===")
    print(f"  Viewport: [{VIEWPORT_XMIN}..{VIEWPORT_XMAX}] x [{VIEWPORT_YMIN}..{VIEWPORT_YMAX}]")
    print(f"  Q-format: Q16.16 (ONE=0x{ONE:08X}, HALF=0x{HALF:04X})\n")

    fails = 0
    for name, p_int, (lo, hi), expect_empty in cases:
        clipped = clip_poly_int(p_int)
        verts_int = _format_q(clipped)
        nv = len(clipped)
        if expect_empty:
            ok = (nv == 0)
        else:
            ok = (lo <= nv <= hi)
        status = "OK  " if ok else "FAIL"
        if not ok:
            fails += 1
        print(f"  [{status}] {name}: nverts={nv}, range=[{lo}..{hi}]")
        print(f"           clipped int: {verts_int}")

    # Boundary check: all clipped vertices must be inside viewport.
    print("\n  Boundary invariant:")
    for name, p_int, _, expect_empty in cases:
        if expect_empty:
            continue
        clipped = clip_poly_int(p_int)
        for (xq, yq) in clipped:
            if not (XMIN_Q <= xq <= XMAX_Q and YMIN_Q <= yq <= YMAX_Q):
                print(f"    FAIL: {name}: vertex ({from_q(xq)},{from_q(yq)}) outside viewport")
                fails += 1
                break
        else:
            print(f"    OK   {name}: all {len(clipped)} verts inside")

    print(f"\n  Total fails: {fails}")
    return fails

if __name__ == '__main__':
    sys.exit(smoke())
