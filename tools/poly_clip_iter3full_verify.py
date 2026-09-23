#!/usr/bin/env python3
"""F4.2b iter 3 full -- 4-edge ping-pong verify (LEFT, RIGHT, TOP, BOT).

Replicates the 4-pass GPU clip pipeline:
  IN_BUF  -> [LEFT clip]  -> PING_BUF
  PING    -> [RIGHT clip] -> OUT_BUF
  OUT     -> [TOP clip]   -> PONG_BUF
  PONG    -> [BOT clip]   -> OUT_BUF (final)

3 test cases:
  1) Regression S9 -- triangle straddles LEFT+RIGHT only (TOP/BOT no-op).
     Expected: pass1=4, pass2=5, pass3=5 (no change), pass4=5 (no change).
  2) Stress 4-edge -- quad encompassing viewport. All 4 passes active.
     Expected: pass4 -> rectangle viewport (4 verts at corners).
  3) Octagon-ish realistic -- triangle straddles LEFT+RIGHT+BOT (no TOP).
     Expected: ~6 verts after BOT pass.

Prints int + Q16.16 hex per pass for ground-truth memory dump comparison
vs BigPEmu PING_BUF/OUT_BUF/PONG_BUF/OUT_BUF dumps.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from poly_clip_prep import (
    EDGE_LEFT, EDGE_RIGHT, EDGE_TOP, EDGE_BOT,
    XMAX_Q, YMAX_Q,
    to_q, from_q,
    clip_against_edge,
)

PING_ADDR = 0x2380
OUT_ADDR  = 0x2340
PONG_ADDR = 0x2400

TESTS = [
    ("Regression S9 (LEFT+RIGHT only)",
     [(-50, 100), (400, 80), (160, 180)]),
    ("Stress 4-edge (quad encompassing viewport)",
     [(-50, -50), (400, -50), (400, 250), (-50, 250)]),
    ("Realistic 3-edge straddle (LEFT+RIGHT+BOT)",
     [(-50, 50), (400, 50), (160, 250)]),
]


def fmt_buf(verts, base):
    if not verts:
        return f"    [${base:04X}+] = (empty)"
    lines = []
    for i, (xq, yq) in enumerate(verts):
        a_x = base + i * 8
        a_y = a_x + 4
        lines.append(f"    [${a_x:04X}] = ${xq & 0xFFFFFFFF:08X}    "
                     f"[${a_y:04X}] = ${yq & 0xFFFFFFFF:08X}    "
                     f"= ({from_q(xq):4d}, {from_q(yq):4d})")
    return "\n".join(lines)


def run_one(label, poly_int):
    print(f"=== {label} ===")
    print(f"  Input poly (int): {poly_int}")
    print()

    poly_q = [(to_q(x), to_q(y)) for (x, y) in poly_int]

    pass1 = clip_against_edge(poly_q, EDGE_LEFT)
    print(f"  Pass 1 LEFT  -> PING_BUF (count={len(pass1)})")
    print(fmt_buf(pass1, PING_ADDR))
    print()

    pass2 = clip_against_edge(pass1, EDGE_RIGHT)
    print(f"  Pass 2 RIGHT -> OUT_BUF  (count={len(pass2)})")
    print(fmt_buf(pass2, OUT_ADDR))
    print()

    pass3 = clip_against_edge(pass2, EDGE_TOP)
    print(f"  Pass 3 TOP   -> PONG_BUF (count={len(pass3)})")
    print(fmt_buf(pass3, PONG_ADDR))
    print()

    pass4 = clip_against_edge(pass3, EDGE_BOT)
    print(f"  Pass 4 BOT   -> OUT_BUF  (count={len(pass4)})  [FINAL]")
    print(fmt_buf(pass4, OUT_ADDR))
    print()

    print(f"  [$2290 OUT_COUNT] = {len(pass4)}")
    print()
    print(f"  Visual rendering (16x16 block per vert, bounds x<304, y<184):")
    visible = 0
    for i, (xq, yq) in enumerate(pass4):
        x = from_q(xq); y = from_q(yq)
        in_x = (0 <= x < 304)
        in_y = (0 <= y < 184)
        ok = in_x and in_y
        flag = "VIS" if ok else "OOB"
        if ok: visible += 1
        print(f"    out{i}: ({x:3d}, {y:3d})  {flag}")
    print(f"  Total visible blocks: {visible} of {len(pass4)} verts + 1 sentinel")
    print()
    print()


def main():
    print(f"VIEWPORT: x in [0..319], y in [0..199]")
    print(f"  XMAX_Q = ${XMAX_Q & 0xFFFFFFFF:08X}  YMAX_Q = ${YMAX_Q & 0xFFFFFFFF:08X}")
    print()
    for label, poly in TESTS:
        run_one(label, poly)


if __name__ == '__main__':
    main()
