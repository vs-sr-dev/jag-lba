#!/usr/bin/env python3
"""Verify a packed scene cart binary by re-rendering it to PNG using ONLY the
binary contents (no HQR access). If the output matches the reference render,
the cart format is correct.

Usage: python cart_verify.py cart/scene_000.bin -o verify_000.png
"""
import sys, os, struct, argparse
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from render_brick import write_png_rgba

CUBE_X, CUBE_Y, CUBE_Z = 64, 25, 64
MAGIC = b"JAGLBA01"


def jag16_to_rgb(w):
    """Unpack Jaguar RGB16 (R5-B5-G6) to 8-bit RGB tuple."""
    r5 = (w >> 11) & 0x1F
    b5 = (w >> 6) & 0x1F
    g6 = w & 0x3F
    r = (r5 << 3) | (r5 >> 2)
    b = (b5 << 3) | (b5 >> 2)
    g = (g6 << 2) | (g6 >> 4)
    return r, g, b


def parse_header(data):
    if data[0:8] != MAGIC:
        raise ValueError(f'bad magic: {data[0:8]!r}')
    h = struct.unpack('>14I', data[8:8+14*4])
    return {
        'version':       h[0],
        'scene_id':      h[1],
        'palette_off':   h[2], 'palette_size': h[3],
        'grid_off':      h[4], 'grid_size':    h[5],
        'bll_off':       h[6], 'bll_size':     h[7],
        'bll_count':     h[8],
        'bdir_off':      h[9], 'bdir_count':   h[10],
        'bpix_off':      h[11], 'bpix_size':    h[12],
        'file_size':     h[13],
    }


def parse_palette(data, off):
    pal = []
    for i in range(256):
        w = struct.unpack('>H', data[off+i*2:off+i*2+2])[0]
        pal.append(jag16_to_rgb(w))
    return pal


def decomp_column_be(data, offset):
    """Same as grid_decode.decomp_column but with BE UWORDs."""
    pos = offset
    nb_entity = data[pos]; pos += 1
    cells = []
    for _ in range(nb_entity):
        op = data[pos]; pos += 1
        count = (op & 0x3F) + 1
        mode = op & 0xC0
        if mode == 0x00:
            cells.extend([0] * count)
        elif mode == 0x80:
            v = struct.unpack('>H', data[pos:pos+2])[0]
            pos += 2
            cells.extend([v] * count)
        else:
            for _ in range(count):
                v = struct.unpack('>H', data[pos:pos+2])[0]
                cells.append(v); pos += 2
    return cells, pos


def parse_grid_be(data, off, size):
    """Parse BE-converted grid. Returns cube[z][x][y]."""
    grid_data = data[off:off+size]
    cube = [[None]*CUBE_X for _ in range(CUBE_Z)]
    for z in range(CUBE_Z):
        for x in range(CUBE_X):
            offset_idx = (x + z*CUBE_Z) * 2
            col_offset = struct.unpack('>H', grid_data[offset_idx:offset_idx+2])[0]
            cells, _ = decomp_column_be(grid_data, col_offset)
            if len(cells) < CUBE_Y:
                cells += [0] * (CUBE_Y - len(cells))
            elif len(cells) > CUBE_Y:
                cells = cells[:CUBE_Y]
            cube[z][x] = cells
    return cube


def parse_bll_be(data, off, size, n_blocks):
    """Parse BE-converted BLL. Returns dict {block_idx: (dx, dy, dz, [brick_refs_local_1based])}."""
    bll_data = data[off:off+size]
    blocks = {}
    for i in range(n_blocks):
        block_off = struct.unpack('>I', bll_data[i*4:(i+1)*4])[0]
        dx, dy, dz = bll_data[block_off], bll_data[block_off+1], bll_data[block_off+2]
        n_cells = dx * dy * dz
        cp = block_off + 3
        refs = []
        for _ in range(n_cells):
            # info at cp..cp+2 (skip), brick_ref at cp+2..cp+4
            br = struct.unpack('>H', bll_data[cp+2:cp+4])[0]
            refs.append(br)
            cp += 4
        blocks[i] = (dx, dy, dz, refs)
    return blocks


def parse_brick_dir(data, off, count):
    """Returns list of (pixel_offset, dx, dy, hot_x, hot_y)."""
    out = []
    for i in range(count):
        e_off = off + i * 8
        po = struct.unpack('>I', data[e_off:e_off+4])[0]
        dx, dy, hx, hy = data[e_off+4], data[e_off+5], data[e_off+6], data[e_off+7]
        out.append((po, dx, dy, hx, hy))
    return out


def render_brick_from_cart(data, bpix_off, brick_dir_entry, palette_arr):
    """Returns (rgba H,W,4 uint8, mask H,W bool, hot_x, hot_y)."""
    po, dx, dy, hx, hy = brick_dir_entry
    base = bpix_off + po
    pix_flat = data[base:base+dx*dy]
    px = np.frombuffer(pix_flat, dtype=np.uint8).reshape(dy, dx)
    rgba = np.zeros((dy, dx, 4), dtype=np.uint8)
    rgba[:, :, 0:3] = palette_arr[px]
    mask = px != 0  # palette idx 0 = transparent
    rgba[:, :, 3] = np.where(mask, 255, 0)
    return rgba, mask, hx, hy


def main(cart_path, out_path):
    with open(cart_path, 'rb') as f: data = f.read()
    print(f'Cart: {cart_path}  ({len(data):,} bytes)')

    hdr = parse_header(data)
    print(f'  scene {hdr["scene_id"]}, version {hdr["version"]}')
    print(f'  palette @ 0x{hdr["palette_off"]:08X} ({hdr["palette_size"]} B)')
    print(f'  grid    @ 0x{hdr["grid_off"]:08X} ({hdr["grid_size"]} B)')
    print(f'  BLL     @ 0x{hdr["bll_off"]:08X} ({hdr["bll_size"]} B, {hdr["bll_count"]} blocks)')
    print(f'  bdir    @ 0x{hdr["bdir_off"]:08X} ({hdr["bdir_count"]} entries)')
    print(f'  bpix    @ 0x{hdr["bpix_off"]:08X} ({hdr["bpix_size"]:,} B)')
    if len(data) != hdr['file_size']:
        print(f'  WARN: header file_size {hdr["file_size"]} != actual {len(data)}')

    palette = parse_palette(data, hdr['palette_off'])
    palette_arr = np.array(palette, dtype=np.uint8)

    cube = parse_grid_be(data, hdr['grid_off'], hdr['grid_size'])
    bll_blocks = parse_bll_be(data, hdr['bll_off'], hdr['bll_size'], hdr['bll_count'])
    brick_dir = parse_brick_dir(data, hdr['bdir_off'], hdr['bdir_count'])

    # Pre-render bricks
    print(f'Pre-rendering {len(brick_dir)} bricks...')
    bricks = [render_brick_from_cart(data, hdr['bpix_off'], be, palette_arr)
              for be in brick_dir]

    # Walk grid
    drawables = []
    bad_pos = 0
    bad_block = 0
    for gz in range(CUBE_Z):
        for gx in range(CUBE_X):
            col = cube[gz][gx]
            for gy in range(CUBE_Y):
                v = col[gy]
                if v == 0: continue
                num_block = v & 0xFF
                pos_block = v >> 8
                if num_block == 0: continue
                if num_block - 1 not in bll_blocks: bad_block += 1; continue
                _, _, _, refs = bll_blocks[num_block - 1]
                if pos_block >= len(refs): bad_pos += 1; continue
                local_ref = refs[pos_block]
                if local_ref == 0: continue
                local_idx = local_ref - 1
                if local_idx >= len(brick_dir): continue
                drawables.append((gx, gy, gz, local_idx))
    print(f'Drawables: {len(drawables)}  bad_pos: {bad_pos}  bad_block: {bad_block}')

    # Painter sort (back→front)
    drawables.sort(key=lambda d: (d[0] + d[2], d[1]))

    # Compute extents
    minx = miny = 10**9
    maxx = maxy = -10**9
    for gx, gy, gz, bidx in drawables:
        sx = (gx - gz) * 24
        sy = (gx + gz) * 12 - gy * 15
        rgba, mask, hx, hy = bricks[bidx]
        bw, bh = rgba.shape[1], rgba.shape[0]
        x0 = sx + hx; y0 = sy + hy
        x1 = x0 + bw; y1 = y0 + bh
        minx = min(minx, x0); miny = min(miny, y0)
        maxx = max(maxx, x1); maxy = max(maxy, y1)
    margin = 8
    cw = maxx - minx + 2*margin
    ch = maxy - miny + 2*margin
    print(f'Canvas: {cw}x{ch}')

    canvas = np.zeros((ch, cw, 4), dtype=np.uint8)
    canvas[:, :, 0] = 16; canvas[:, :, 1] = 18; canvas[:, :, 2] = 28; canvas[:, :, 3] = 255

    for gx, gy, gz, bidx in drawables:
        sx = (gx - gz) * 24
        sy = (gx + gz) * 12 - gy * 15
        rgba, mask, hx, hy = bricks[bidx]
        bw, bh = rgba.shape[1], rgba.shape[0]
        ox = sx + hx - minx + margin
        oy = sy + hy - miny + margin
        target = canvas[oy:oy+bh, ox:ox+bw]
        if target.shape[:2] != (bh, bw): continue
        target[mask] = rgba[mask]

    write_png_rgba(out_path, cw, ch, canvas.tobytes())
    print(f'Wrote {out_path}')


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('cart')
    ap.add_argument('-o', '--out', default=None)
    args = ap.parse_args()
    out = args.out or args.cart.replace('.bin', '_verify.png')
    main(args.cart, out)
