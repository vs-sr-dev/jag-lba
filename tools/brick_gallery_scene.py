#!/usr/bin/env python3
"""Render a gallery of all unique bricks USED in a specific scene.

For grid N: walks the (NumBlock, PosBlock) cells, resolves each via BLL[N], collects
unique brick indices, then renders each one at scale × in a grid layout.

Lets us inspect: are the bricks the engine WOULD select (smooth wall panels, etc.)
present in our resolution chain, or are they different entirely?
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import lba_paths
import sys, os, struct, argparse
import numpy as np
from collections import OrderedDict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hqr_decompress import parse_offsets, extract_entry
from render_brick import decode_brick, write_png_rgba
from render_slide import normalize_palette
from grid_decode import decode_grid, CUBE_X, CUBE_Y, CUBE_Z
from render_scene import parse_bll_entry

def main(grid_idx, brk_path, bll_path, gri_path, ress_path, out_path, scale, cols):
    print("Loading...")
    with open(brk_path, 'rb') as f: brk_data = f.read()
    with open(bll_path, 'rb') as f: bll_data = f.read()
    with open(gri_path, 'rb') as f: gri_data = f.read()
    with open(ress_path, 'rb') as f: ress_data = f.read()

    brk_offsets = parse_offsets(brk_data)
    bll_offsets = parse_offsets(bll_data)
    gri_offsets = parse_offsets(gri_data)
    ress_offsets = parse_offsets(ress_data)

    pal_raw = extract_entry(ress_data, 0, ress_offsets)
    palette = normalize_palette(pal_raw)
    palette_arr = np.array(palette, dtype=np.uint8)

    cube, _ = decode_grid(extract_entry(gri_data, grid_idx, gri_offsets))
    bll_blocks = parse_bll_entry(extract_entry(bll_data, grid_idx, bll_offsets))

    # Collect unique brick_idx with usage counts
    usage = {}  # brick_idx -> count
    for gz in range(CUBE_Z):
        for gx in range(CUBE_X):
            for gy in range(CUBE_Y):
                v = cube[gz][gx][gy]
                if v == 0:
                    continue
                num_block = v & 0xFF
                pos_block = v >> 8
                if num_block == 0:
                    continue
                blk = bll_blocks.get(num_block - 1)
                if blk is None:
                    continue
                _, _, _, refs = blk
                if pos_block >= len(refs):
                    continue
                br = refs[pos_block]
                if br == 0:
                    continue
                bidx = br - 1
                if 0 <= bidx < 8715:
                    usage[bidx] = usage.get(bidx, 0) + 1

    print(f"  {len(usage)} unique bricks referenced by grid {grid_idx}")

    # Sort by usage count descending (most-used first = floor/wall bases)
    sorted_bricks = sorted(usage.items(), key=lambda kv: -kv[1])
    print(f"  Top 5 most used: {sorted_bricks[:5]}")

    # Decode each brick
    cells = []
    for bidx, count in sorted_bricks:
        try:
            payload = extract_entry(brk_data, bidx, brk_offsets)
            if payload is None:
                continue
            dx, dy, hx, hy, pixels, alpha, _ = decode_brick(payload)
            cells.append((bidx, count, dx, dy, pixels, alpha))
        except Exception:
            continue

    # Layout: cols columns, max cell size used for grid stride
    max_dx = max(c[2] for c in cells) * scale
    max_dy = max(c[3] for c in cells) * scale
    GAP = 6
    rows = (len(cells) + cols - 1) // cols
    cw = cols * (max_dx + GAP) + GAP
    ch = rows * (max_dy + GAP) + GAP
    print(f"  Gallery canvas: {cw}x{ch} ({len(cells)} bricks in {cols}x{rows} grid)")

    canvas = np.zeros((ch, cw, 4), dtype=np.uint8)
    canvas[:, :, 0] = 30; canvas[:, :, 1] = 30; canvas[:, :, 2] = 40; canvas[:, :, 3] = 255

    for i, (bidx, count, dx, dy, pixels, alpha) in enumerate(cells):
        col = i % cols
        row = i // cols
        cell_x = GAP + col * (max_dx + GAP)
        cell_y = GAP + row * (max_dy + GAP)
        # Center within cell
        ox = cell_x + (max_dx - dx*scale) // 2
        oy = cell_y + (max_dy - dy*scale) // 2
        # Build brick rgba
        px_arr = np.array(pixels, dtype=np.uint8)
        a_arr = np.array(alpha, dtype=np.uint8)
        rgba = np.zeros((dy, dx, 4), dtype=np.uint8)
        rgba[:, :, 0:3] = palette_arr[px_arr]
        rgba[:, :, 3] = a_arr
        mask = a_arr > 0
        # Scale up (nearest)
        if scale > 1:
            big_rgba = np.kron(rgba, np.ones((scale, scale, 1), dtype=np.uint8))
            big_mask = np.kron(mask.astype(np.uint8), np.ones((scale, scale), dtype=np.uint8)).astype(bool)
            sh = big_rgba.shape[0]; sw = big_rgba.shape[1]
        else:
            big_rgba = rgba; big_mask = mask
            sh = dy; sw = dx
        target = canvas[oy:oy+sh, ox:ox+sw]
        if target.shape[:2] == (sh, sw):
            target[big_mask] = big_rgba[big_mask]

    write_png_rgba(out_path, cw, ch, canvas.tobytes())
    print(f"Wrote {out_path}")

if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--grid', type=int, default=0)
    ap.add_argument('--brk', default=lba_paths.common('LBA_BRK.HQR'))
    ap.add_argument('--bll', default=lba_paths.common('LBA_BLL.HQR'))
    ap.add_argument('--gri', default=lba_paths.common('LBA_GRI.HQR'))
    ap.add_argument('--ress', default=lba_paths.common('RESS.HQR'))
    ap.add_argument('--out', default=None)
    ap.add_argument('--scale', type=int, default=3)
    ap.add_argument('--cols', type=int, default=16)
    args = ap.parse_args()
    out = args.out or lba_paths.repo(rf'extracted\SCENE_render\bricks_gallery_grid{args.grid:03d}.png')
    os.makedirs(os.path.dirname(out), exist_ok=True)
    main(args.grid, args.brk, args.bll, args.gri, args.ress, out, args.scale, args.cols)
