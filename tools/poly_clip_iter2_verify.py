#!/usr/bin/env python3
"""F4.2b iter 2 — bit-exact verification of the LEFT-edge S-H clip output.

Replicates exactly what the GPU clip filter must emit, for the specific
triangle used in the BigPEmu smoke test:

  poly = [(-50, 100), (100, 50), (50, 150)]

Single-edge LEFT clip (XMIN_Q = 0). Prints int + Q16.16 hex for each
output vertex, so the BigPEmu OUT_BUF dump can be compared byte-for-byte.

Imports the canonical math from poly_clip_prep.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from poly_clip_prep import (
    EDGE_LEFT, ONE,
    to_q, from_q, mul_q, div_q,
    clip_against_edge,
)

POLY_INT = [(-50, 100), (100, 50), (50, 150)]

def main():
    print("=== F4.2b iter 2 LEFT-edge clip verify ===")
    print(f"  Input poly (int): {POLY_INT}")
    print(f"  ONE = 0x{ONE:08X}")
    print()

    poly_q = [(to_q(x), to_q(y)) for (x, y) in POLY_INT]
    print("  Input Q16.16:")
    for i, (xq, yq) in enumerate(poly_q):
        print(f"    v{i}: xq=0x{xq & 0xFFFFFFFF:08X} ({from_q(xq):4d}), "
              f"yq=0x{yq & 0xFFFFFFFF:08X} ({from_q(yq):4d})")
    print()

    # Mirror the GPU per-edge dispatch: for each i, compute S=poly[i],
    # E=poly[(i+1) mod N], emit per S-H rules.
    out = []
    n = len(poly_q)
    print("  Per-edge S->E dispatch:")
    for i in range(n):
        S = poly_q[i]
        E = poly_q[(i + 1) % n]
        sx, sy = S
        ex, ey = E
        inS = (sx >= 0)
        inE = (ex >= 0)
        sint = f"v{i}({from_q(sx)},{from_q(sy)})"
        eint = f"v{(i+1)%n}({from_q(ex)},{from_q(ey)})"
        case = (inS, inE)
        if case == (True, True):
            print(f"    i={i}: {sint} in / {eint} in        -> emit E")
            out.append(E)
        elif case == (False, True):
            # intersect S->E with x=0
            den = ex - sx
            num = 0 - sx               # = -sx
            t_q = div_q(num, den)
            y_q = sy + mul_q(t_q, ey - sy)
            ipt = (0, y_q)
            print(f"    i={i}: {sint} OUT/ {eint} in        -> emit I + E")
            print(f"          t_q=0x{t_q & 0xFFFFFFFF:08X} ({t_q/ONE:.6f})")
            print(f"          I = (0, 0x{y_q & 0xFFFFFFFF:08X}) = "
                  f"(0, {from_q(y_q)})")
            out.append(ipt)
            out.append(E)
        elif case == (True, False):
            den = ex - sx
            num = 0 - sx
            t_q = div_q(num, den)
            y_q = sy + mul_q(t_q, ey - sy)
            ipt = (0, y_q)
            print(f"    i={i}: {sint} in / {eint} OUT       -> emit I")
            print(f"          t_q=0x{t_q & 0xFFFFFFFF:08X} ({t_q/ONE:.6f})")
            print(f"          I = (0, 0x{y_q & 0xFFFFFFFF:08X}) = "
                  f"(0, {from_q(y_q)})")
            out.append(ipt)
        else:
            print(f"    i={i}: {sint} OUT/ {eint} OUT       -> skip")

    print()
    print(f"  Output count: {len(out)}")
    print("  Output vertices (Q16.16 + int):")
    for i, (xq, yq) in enumerate(out):
        print(f"    out{i}: xq=0x{xq & 0xFFFFFFFF:08X} ({from_q(xq):4d}), "
              f"yq=0x{yq & 0xFFFFFFFF:08X} ({from_q(yq):4d})")

    # Cross-check against poly_clip_prep.clip_against_edge
    print()
    print("  Cross-check vs poly_clip_prep.clip_against_edge(LEFT):")
    ref = clip_against_edge(poly_q, EDGE_LEFT)
    if ref == out:
        print("    OK   bit-exact match")
    else:
        print("    FAIL mismatch:")
        for v in ref:
            print(f"      ref: {v}")

    print()
    print("  GPU OUT_BUF expected dump ($2340..):")
    for i, (xq, yq) in enumerate(out):
        a_x = 0x2340 + i * 8
        a_y = a_x + 4
        print(f"    [${a_x:04X}] = ${xq & 0xFFFFFFFF:08X}    "
              f"[${a_y:04X}] = ${yq & 0xFFFFFFFF:08X}")
    print(f"    [$2290 OUT_COUNT] = {len(out)}")

if __name__ == '__main__':
    main()
