#!/usr/bin/env python3
"""Overlay test: draw the iso diamond grid (2x2 cells in X/Z, 3 levels in Y) with
specified bricks blitted at their HotX/HotY-anchored positions, to verify whether
our HotX/HotY application convention matches what the engine intends.

Output: a PNG with:
  - cyan grid lines (cell diamond outlines at gy=0)
  - magenta grid lines (cell diamond outlines at gy=1)
  - yellow grid lines (cell diamond outlines at gy=2)
  - red dot at each cell anchor (sx, sy)
  - bricks blitted at (sx - HotX, sy - HotY)

If footprint ALIGNS with diamond → convention OK.
If footprint is OFFSET → diagnose the sign / axis flip.
"""
import sys, os, struct, argparse
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hqr_decompress import parse_offsets, extract_entry
from render_brick import decode_brick, write_png_rgba
from render_slide import normalize_palette


def make_brick_array(payload, palette_arr):
    dec = decode_brick(payload)
    bx, by, hx, hy, pixels, alpha, _ = dec
    px = np.array(pixels, dtype=np.uint8)
    al = np.array(alpha, dtype=np.uint8)
    rgba = np.zeros((by, bx, 4), dtype=np.uint8)
    rgba[:, :, 0:3] = palette_arr[px]
    rgba[:, :, 3] = al
    mask = al > 0
    return rgba, mask, hx, hy, bx, by


def iso_xy(gx, gy, gz):
    sx = (gx - gz) * 24
    sy = (gx + gz) * 12 - gy * 15
    return sx, sy


def draw_line(canvas, x0, y0, x1, y1, color):
    """Bresenham, color = (r,g,b,a)."""
    h, w = canvas.shape[:2]
    dx = abs(x1 - x0); sx = 1 if x0 < x1 else -1
    dy = -abs(y1 - y0); sy = 1 if y0 < y1 else -1
    err = dx + dy
    while True:
        if 0 <= x0 < w and 0 <= y0 < h:
            canvas[y0, x0] = color
        if x0 == x1 and y0 == y1: break
        e2 = 2 * err
        if e2 >= dy: err += dy; x0 += sx
        if e2 <= dx: err += dx; y0 += sy


def draw_diamond(canvas, cx, cy, color):
    """Iso diamond outline: 48 wide, 24 tall, centered at (cx,cy)."""
    pts = [(cx-24, cy), (cx, cy-12), (cx+24, cy), (cx, cy+12)]
    for i in range(4):
        x0, y0 = pts[i]; x1, y1 = pts[(i+1) % 4]
        draw_line(canvas, x0, y0, x1, y1, color)


def draw_cross(canvas, cx, cy, color, size=2):
    h, w = canvas.shape[:2]
    for dx in range(-size, size+1):
        for dy in range(-size, size+1):
            if abs(dx) <= 1 or abs(dy) <= 1:
                x = cx + dx; y = cy + dy
                if 0 <= x < w and 0 <= y < h:
                    canvas[y, x] = color


def main(brk_path, ress_path, brick_indices, out_path):
    print('Loading...')
    with open(brk_path, 'rb') as f: brk_data = f.read()
    with open(ress_path, 'rb') as f: ress_data = f.read()
    brk_off = parse_offsets(brk_data)
    ress_off = parse_offsets(ress_data)
    palette = normalize_palette(extract_entry(ress_data, 0, ress_off))
    palette_arr = np.array(palette, dtype=np.uint8)

    bricks = {}
    for bidx in brick_indices:
        payload = extract_entry(brk_data, bidx, brk_off)
        if payload is None:
            print(f'brick {bidx}: blank'); continue
        bricks[bidx] = make_brick_array(payload, palette_arr)
        rgba, mask, hx, hy, bx, by = bricks[bidx]
        print(f'brick {bidx}: DX={bx} DY={by} HotX={hx} HotY={hy}')

    # Layout: 4 columns horizontally, each shows brick at gx=0..3 of one Y layer
    # We'll place bricks at gy=0 across gx=1..3, gz=1, gy=1 same, gy=2 same
    # Plus a single floor diamond grid 4x4 at gy=0 to anchor visual reference
    placements = []
    # Floor 4x4 grid of empty diamonds at gy=0
    for gx in range(4):
        for gz in range(4):
            placements.append(('grid', gx, 0, gz, None))
    # Stacked diamond markers at gy=1, gy=2 at one position
    placements.append(('grid', 1, 1, 1, None))
    placements.append(('grid', 1, 2, 1, None))

    # Floor brick at (gx=2,gy=0,gz=2) — usually a 48x23 floor tile
    # Wall base at (gx=1,gy=1,gz=2) — brick 0
    # Wall top  at (gx=1,gy=2,gz=2) — brick 1
    # Floor brick at (gx=0,gy=0,gz=0)
    test_placements = [
        # (label, gx, gy, gz, brick_idx)
        ('floor1', 0, 0, 0, 38),       # floor variant
        ('floor2', 3, 0, 3, 38),       # floor variant
        ('wall_base', 1, 1, 2, 0),     # 48x26 HotY=12
        ('wall_top',  1, 2, 2, 1),     # 48x38 HotY=0
    ]
    for lbl, gx, gy, gz, bidx in test_placements:
        if bidx in bricks:
            placements.append(('brick', gx, gy, gz, bidx))

    # Compute canvas extents
    minx = miny = 10**9
    maxx = maxy = -10**9
    for kind, gx, gy, gz, bidx in placements:
        sx, sy = iso_xy(gx, gy, gz)
        if kind == 'grid':
            xs = [sx-24, sx+24]; ys = [sy-12, sy+12]
        else:
            rgba, mask, hx, hy, bx, by = bricks[bidx]
            xs = [sx-hx, sx-hx+bx]; ys = [sy-hy, sy-hy+by]
        minx = min(minx, *xs); maxx = max(maxx, *xs)
        miny = min(miny, *ys); maxy = max(maxy, *ys)
    margin = 12
    cw = (maxx - minx + 2*margin)
    ch = (maxy - miny + 2*margin)
    print(f'canvas: {cw}x{ch}')
    canvas = np.zeros((ch, cw, 4), dtype=np.uint8)
    canvas[:, :, 0:3] = 24; canvas[:, :, 3] = 255  # dark gray background

    def to_canvas(sx, sy):
        return sx - minx + margin, sy - miny + margin

    # First pass: blit bricks (back-to-front)
    brick_placements = [p for p in placements if p[0] == 'brick']
    brick_placements.sort(key=lambda p: (-(p[1] + p[3]), p[2]))
    for kind, gx, gy, gz, bidx in brick_placements:
        sx, sy = iso_xy(gx, gy, gz)
        rgba, mask, hx, hy, bx, by = bricks[bidx]
        cx, cy = to_canvas(sx - hx, sy - hy)
        target = canvas[cy:cy+by, cx:cx+bx]
        if target.shape[:2] != (by, bx):
            print(f'  skip out-of-bounds blit for brick {bidx}'); continue
        target[mask] = rgba[mask]

    # Second pass: draw diamond outlines on top
    GRID_COLORS = {
        0: (0, 220, 220, 255),    # cyan = ground
        1: (220, 0, 220, 255),    # magenta = gy=1
        2: (240, 230, 0, 255),    # yellow = gy=2
    }
    for kind, gx, gy, gz, bidx in placements:
        if kind != 'grid': continue
        sx, sy = iso_xy(gx, gy, gz)
        cx, cy = to_canvas(sx, sy)
        col = GRID_COLORS.get(gy, (255, 255, 255, 255))
        draw_diamond(canvas, cx, cy, col)
        draw_cross(canvas, cx, cy, (255, 50, 50, 255))  # red anchor

    # Third pass: anchor cross for brick placements
    for kind, gx, gy, gz, bidx in brick_placements:
        sx, sy = iso_xy(gx, gy, gz)
        cx, cy = to_canvas(sx, sy)
        draw_cross(canvas, cx, cy, (50, 255, 50, 255))  # green anchor

    write_png_rgba(out_path, cw, ch, canvas.tobytes())
    print(f'Wrote {out_path}')


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--brk', default=r'I:\GOG\Little Big Adventure\Common\LBA_BRK.HQR')
    ap.add_argument('--ress', default=r'I:\GOG\Little Big Adventure\Common\RESS.HQR')
    ap.add_argument('--bricks', default='0,1,38', help='comma-sep brick idxs to load')
    ap.add_argument('--out', default=r'd:\Homebrew5\JagLBA\extracted\SCENE_render\overlay_a2.png')
    args = ap.parse_args()
    bricks = [int(x) for x in args.bricks.split(',')]
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    main(args.brk, args.ress, bricks, args.out)
