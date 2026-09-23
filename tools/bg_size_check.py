#!/usr/bin/env python3
"""V3.1 pre-flight: for V3 OP-scroll BG sizing, compute the iso bounding box
of cells within a (gx, gz) rectangle around the camera. Outputs BG size for
several radius choices so we pick what fits memory.

Usage: python bg_size_check.py cart/scene_000.bin
"""
import sys, os, struct
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cart_verify import (parse_header, parse_palette, parse_grid_be,
                         parse_bll_be, parse_brick_dir, CUBE_X, CUBE_Y, CUBE_Z)


def main(cart_path):
    with open(cart_path, 'rb') as f: data = f.read()
    hdr = parse_header(data)
    cube = parse_grid_be(data, hdr['grid_off'], hdr['grid_size'])
    bll_blocks = parse_bll_be(data, hdr['bll_off'], hdr['bll_size'], hdr['bll_count'])
    brick_dir = parse_brick_dir(data, hdr['bdir_off'], hdr['bdir_count'])

    drawables = []
    for gz in range(CUBE_Z):
        for gx in range(CUBE_X):
            col = cube[gz][gx]
            for gy in range(CUBE_Y):
                v = col[gy]
                if v == 0: continue
                num_block = v & 0xFF
                pos_block = v >> 8
                if num_block == 0: continue
                if num_block - 1 not in bll_blocks: continue
                _, _, _, refs = bll_blocks[num_block - 1]
                if pos_block >= len(refs): continue
                local_ref = refs[pos_block]
                if local_ref == 0: continue
                local_idx = local_ref - 1
                if local_idx >= len(brick_dir): continue
                _, dx, dy, hx, hy = brick_dir[local_idx]
                drawables.append((gx, gy, gz, dx, dy, hx, hy))
    print(f'Total drawables: {len(drawables)}')

    # Camera default
    cam_gx, cam_gz = 8, 50
    print(f'\nCamera: (gx={cam_gx}, gz={cam_gz})\n')
    print(f"{'radius':>7}  {'cells':>6}  {'iso_bbox':>20}  {'BG W x H':>12}  "
          f"{'RGB16 KB':>9}  {'8bpp KB':>8}")
    print('-' * 80)

    for radius in [6, 8, 10, 12, 15, 20, 32]:
        gx_lo, gx_hi = max(0, cam_gx - radius), min(CUBE_X, cam_gx + radius + 1)
        gz_lo, gz_hi = max(0, cam_gz - radius), min(CUBE_Z, cam_gz + radius + 1)

        in_box = [d for d in drawables
                  if gx_lo <= d[0] < gx_hi and gz_lo <= d[2] < gz_hi]
        if not in_box:
            continue

        minx = miny = 10**9
        maxx = maxy = -10**9
        for gx, gy, gz, dx, dy, hx, hy in in_box:
            sx = (gx - gz) * 24 + hx
            sy = (gx + gz) * 12 - gy * 15 + hy
            minx = min(minx, sx)
            miny = min(miny, sy)
            maxx = max(maxx, sx + dx)
            maxy = max(maxy, sy + dy)

        w = maxx - minx
        h = maxy - miny
        # Round up to phrase-friendly multiples (multiple of 4 px = 1 phrase)
        w_align = (w + 3) & ~3
        # Also enforce a valid Blitter WID for the BG (so the OP/blit can work nicely)
        wid_options = [320, 384, 448, 512, 640, 768, 896, 1024, 1280]
        w_wid = next((x for x in wid_options if x >= w_align), wid_options[-1])
        rgb16_kb = w_wid * h * 2 / 1024
        bpp8_kb = w_wid * h / 1024

        print(f"{radius:>7}  {len(in_box):>6}  ({minx:>5},{miny:>5})..({maxx:>5},{maxy:>5})"
              f"  {w_wid:>5}x{h:>4}  {rgb16_kb:>8.1f}   {bpp8_kb:>7.1f}")


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else 'cart/scene_000.bin')
