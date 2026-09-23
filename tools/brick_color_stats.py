#!/usr/bin/env python3
"""Color usage statistics across all bricks in LBA_BRK.HQR.

Decodes every brick, counts unique palette indices per brick, and produces:
  - Histogram: bricks vs unique color count
  - Threshold analysis: how many bricks fit in <=N colors
  - Top-K most-used global palette indices
  - Coverage analysis: % of pixels covered by top-N indices
  - Per-size breakdown: do dim categories share color profile?

This informs the cart re-encoding strategy:
  - If most bricks use <=16 colors: per-brick 4bpp palette is viable
  - If average is 30-50: per-zone palette + clustering needed
  - If extreme spread: CRY16 + dithering may be the only path
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import lba_paths
import sys, os
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hqr_decompress import parse_offsets, extract_entry
from render_brick import decode_brick

def main(brk_path):
    with open(brk_path, 'rb') as f:
        data = f.read()
    offsets = parse_offsets(data)

    n_bricks = 0
    total_opaque = 0
    total_transparent = 0
    color_count_hist = Counter()      # unique-colors-per-brick -> num bricks
    global_use = [0] * 256            # how often each palette idx is used globally
    size_color_map = defaultdict(list) # (dx,dy) -> list of unique-count per brick

    for i in range(len(offsets)):
        payload = extract_entry(data, i, offsets)
        if payload is None:
            continue
        dx, dy, _, _, pixels, alpha, _ = decode_brick(payload)
        n_bricks += 1
        seen = set()
        for y in range(dy):
            for x in range(dx):
                if alpha[y][x]:
                    p = pixels[y][x]
                    seen.add(p)
                    global_use[p] += 1
                    total_opaque += 1
                else:
                    total_transparent += 1
        color_count_hist[len(seen)] += 1
        size_color_map[(dx, dy)].append(len(seen))

    print(f"=== Brick color stats ({n_bricks} bricks) ===")
    print(f"Total opaque pixels:      {total_opaque:>12,}")
    print(f"Total transparent pixels: {total_transparent:>12,}")
    print(f"Unique palette indices used (any brick): {sum(1 for c in global_use if c > 0)} of 256")
    print()

    print("--- Distribution: unique palette indices per brick ---")
    print(f"  {'colors':>6}  {'bricks':>6}  {'cum%':>6}")
    cum = 0
    for k in sorted(color_count_hist):
        cum += color_count_hist[k]
        print(f"  {k:>6}  {color_count_hist[k]:>6}  {100*cum/n_bricks:>5.1f}%")

    print()
    print("--- Threshold analysis: bricks fitting in <=N colors ---")
    for thresh in [4, 8, 12, 16, 20, 24, 32, 48, 64]:
        fit = sum(c for k, c in color_count_hist.items() if k <= thresh)
        print(f"  <= {thresh:>3} colors: {fit:>5} bricks ({100*fit/n_bricks:>5.1f}%)")

    print()
    print("--- Top-16 most-used global palette indices ---")
    ranked = sorted(range(256), key=lambda i: -global_use[i])
    for rank, idx in enumerate(ranked[:16]):
        if global_use[idx] == 0:
            break
        pct = 100 * global_use[idx] / total_opaque
        print(f"  #{rank+1:>2}: pal[{idx:>3}] -> {global_use[idx]:>10,} pixels ({pct:>5.2f}%)")

    print()
    print("--- Pixel coverage by top-N global palette indices ---")
    for top_n in [4, 8, 16, 32, 48, 64, 96, 128]:
        cum_n = sum(global_use[idx] for idx in ranked[:top_n])
        print(f"  top {top_n:>3}: {100*cum_n/total_opaque:>5.2f}% of opaque pixels")

    print()
    print("--- Avg unique colors per brick, by dimension (top 8 sizes) ---")
    sized = sorted(size_color_map.items(), key=lambda kv: -len(kv[1]))[:8]
    print(f"  {'DX':>4}x{'DY':<4}  {'count':>6}  {'avg':>6}  {'min':>4}  {'max':>4}")
    for (dx, dy), lst in sized:
        avg = sum(lst) / len(lst)
        print(f"  {dx:>4}x{dy:<4}  {len(lst):>6}  {avg:>6.1f}  {min(lst):>4}  {max(lst):>4}")

if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else lba_paths.common('LBA_BRK.HQR'))
