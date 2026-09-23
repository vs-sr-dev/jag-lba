#!/usr/bin/env python3
"""F4.2b iter 3 mini — 2-edge ping-pong verify (LEFT then RIGHT).

Replicates the 2-pass GPU clip pipeline:
  IN_BUF -> [LEFT clip] -> PING_BUF -> [RIGHT clip] -> OUT_BUF

Test triangle straddles BOTH edges:
  poly = [(-50, 100), (400, 80), (160, 180)]
  v0 outside LEFT, v1 outside RIGHT, v2 inside both.

Prints int + Q16.16 hex for ground-truth memory dump comparison vs
BigPEmu OUT_BUF + PING_BUF.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from poly_clip_prep import (
    EDGE_LEFT, EDGE_RIGHT, ONE,
    XMAX_Q,
    to_q, from_q,
    clip_against_edge,
)

POLY_INT = [(-50, 100), (400, 80), (160, 180)]

PING_ADDR = 0x2380
OUT_ADDR  = 0x2340

def fmt_buf(verts, base):
    lines = []
    for i, (xq, yq) in enumerate(verts):
        a_x = base + i * 8
        a_y = a_x + 4
        lines.append(f"    [${a_x:04X}] = ${xq & 0xFFFFFFFF:08X}    "
                     f"[${a_y:04X}] = ${yq & 0xFFFFFFFF:08X}    "
                     f"= ({from_q(xq):4d}, {from_q(yq):4d})")
    return "\n".join(lines)

def main():
    print("=== F4.2b iter 3 mini (LEFT + RIGHT ping-pong) verify ===")
    print(f"  Input poly (int): {POLY_INT}")
    print(f"  XMAX_Q = ${XMAX_Q:08X}  (= {from_q(XMAX_Q)})")
    print()

    poly_q = [(to_q(x), to_q(y)) for (x, y) in POLY_INT]

    print("  Pass 1 -- LEFT clip (clip_x = 0)")
    pass1 = clip_against_edge(poly_q, EDGE_LEFT)
    print(f"    output count: {len(pass1)}")
    print(f"    PING_BUF expected (${PING_ADDR:04X}+):")
    print(fmt_buf(pass1, PING_ADDR))
    print()

    print("  Pass 2 -- RIGHT clip (clip_x = XMAX_Q)")
    pass2 = clip_against_edge(pass1, EDGE_RIGHT)
    print(f"    output count: {len(pass2)}")
    print(f"    OUT_BUF expected (${OUT_ADDR:04X}+):")
    print(fmt_buf(pass2, OUT_ADDR))
    print()

    print(f"  [$2290 OUT_COUNT] = {len(pass2)}")
    print()

    print("  Visual rendering (16x16 block per vert with bounds x<304, y<184):")
    visible = 0
    for i, (xq, yq) in enumerate(pass2):
        x = from_q(xq); y = from_q(yq)
        in_x = (0 <= x < 304)
        in_y = (0 <= y < 184)
        ok = in_x and in_y
        flag = "VIS" if ok else "OOB"
        if ok: visible += 1
        print(f"    out{i}: ({x:3d}, {y:3d})  {flag}")
    print(f"  Total visible blocks: {visible} of {len(pass2)} verts + 1 sentinel")

if __name__ == '__main__':
    main()
