#!/usr/bin/env python3
"""Composite render of an LBA1 scene: walk grid, look up bricks via BLL, blit at iso positions.

Pipeline:
  1. Decode grid N from LBA_GRI.HQR -> 64x25x64 cube of (NumBlock, PosBlock) UWORDs
  2. Parse BLL entries: each is (dx, dy, dz, jump, collis, dx*dy*dz UWORDs of brick refs)
  3. For each filled cell at (gx, gy, gz) with value (NumBlock, PosBlock):
       brick_idx = BLL[NumBlock].cells[PosBlock] - 1
       world_pos = (gx*512, gy*256, gz*512)
       screen_pos = iso_project(world_pos) - (HotX, HotY)
       blit brick rgba+alpha at screen_pos
  4. Painter order: back-to-front by (-(gx+gz), gy)
  5. Output composite PNG

Uses numpy for blitting (otherwise pure-Python takes minutes).
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import lba_paths
import sys, os, struct, argparse
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hqr_decompress import parse_offsets, extract_entry
from render_brick import decode_brick, write_png_rgba
from render_slide import normalize_palette
from grid_decode import decode_grid, CUBE_X, CUBE_Y, CUBE_Z

# Iso projection at brick-cell granularity:
# world_x = gx * 512, world_y = gy * 256, world_z = gz * 512
# screen_x = (world_x - world_z) * 24 / 512 = (gx - gz) * 24
# screen_y = (world_x + world_z) * 12 / 512 - world_y * 30 / 512 = (gx + gz) * 12 - gy * 15

def parse_bll_entry(payload):
    """A BLL entry is a 'TabBlock' = ULONG offset table + N blocks.
    Block layout: 3-byte header (dx, dy, dz) + dx*dy*dz cells, each cell = 2 UWORDs (brick_ref, info).
    Returns dict {block_idx_0based: (dx, dy, dz, [brick_refs])}.
    """
    if len(payload) < 4:
        return None
    first_off = struct.unpack('<I', payload[0:4])[0]
    if first_off <= 0 or first_off > len(payload) or first_off % 4 != 0:
        return None
    n_blocks = first_off // 4
    blocks = {}
    for i in range(n_blocks):
        off = struct.unpack('<I', payload[i*4:(i+1)*4])[0]
        if off + 3 > len(payload):
            continue
        dx, dy, dz = payload[off], payload[off+1], payload[off+2]
        n_cells = dx * dy * dz
        if off + 3 + n_cells * 4 > len(payload):
            continue
        brick_refs = []
        cp = off + 3
        for _ in range(n_cells):
            # Per asm `ptw = ptb + 5`, brick_ref is the SECOND UWORD per cell,
            # first UWORD is some position/info field (skipped).
            br = struct.unpack('<H', payload[cp+2:cp+4])[0]
            brick_refs.append(br)
            cp += 4
        blocks[i] = (dx, dy, dz, brick_refs)
    return blocks

def make_brick_array(brick_dec, palette_arr):
    """Decoded brick -> (rgba: H,W,4 uint8, mask: H,W bool, hx, hy)."""
    bx, by, hx, hy, pixels, alpha, _ = brick_dec
    px = np.array(pixels, dtype=np.uint8)
    al = np.array(alpha, dtype=np.uint8)
    rgba = np.zeros((by, bx, 4), dtype=np.uint8)
    rgba[:, :, 0:3] = palette_arr[px]
    rgba[:, :, 3] = al
    mask = al > 0
    return rgba, mask, hx, hy

def main(grid_idx, brk_path, bll_path, gri_path, ress_path, out_path, scale):
    print(f"Loading HQRs...")
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
    palette_arr = np.array(palette, dtype=np.uint8)  # (256, 3)

    print(f"Decoding grid {grid_idx}...")
    gri_payload = extract_entry(gri_data, grid_idx, gri_offsets)
    cube, _ = decode_grid(gri_payload)

    # BLL cache
    bll_cache = {}
    def get_bll(idx):
        if idx not in bll_cache:
            try:
                payload = extract_entry(bll_data, idx, bll_offsets)
                bll_cache[idx] = parse_bll_entry(payload) if payload else None
            except Exception:
                bll_cache[idx] = None
        return bll_cache[idx]

    # Brick cache: brick_idx -> (rgba, mask, hx, hy)
    brick_cache = {}
    def get_brick(idx):
        if idx not in brick_cache:
            try:
                payload = extract_entry(brk_data, idx, brk_offsets)
                if payload is None:
                    brick_cache[idx] = None
                else:
                    dec = decode_brick(payload)
                    brick_cache[idx] = make_brick_array(dec, palette_arr)
            except Exception as e:
                print(f"  brick {idx}: error {e}")
                brick_cache[idx] = None
        return brick_cache[idx]

    # Walk grid: collect drawables
    drawables = []  # list of (gx, gy, gz, brick_idx)
    missing_blocks = set()
    bad_pos = 0
    for gz in range(CUBE_Z):
        for gx in range(CUBE_X):
            col = cube[gz][gx]
            for gy in range(CUBE_Y):
                v = col[gy]
                if v == 0:
                    continue
                # NumBlock=low (1-based, 0 = empty), PosBlock=high
                num_block = v & 0xFF
                pos_block = v >> 8
                if num_block == 0:
                    continue  # empty marker
                blocks = get_bll(grid_idx)
                if blocks is None:
                    missing_blocks.add(num_block); continue
                bll_block = blocks.get(num_block - 1)
                if bll_block is None:
                    missing_blocks.add(num_block); continue
                _, _, _, brick_refs = bll_block
                if pos_block >= len(brick_refs):
                    bad_pos += 1; continue
                brick_ref = brick_refs[pos_block]
                if brick_ref == 0:
                    continue
                brick_idx = brick_ref - 1
                if brick_idx < 0 or brick_idx >= 8715:
                    continue  # filter garbage indices
                drawables.append((gx, gy, gz, brick_idx))

    print(f"  drawables: {len(drawables)}")
    if missing_blocks: print(f"  missing BLL blocks: {sorted(missing_blocks)[:10]}{'...' if len(missing_blocks)>10 else ''}")
    if bad_pos: print(f"  bad pos_block: {bad_pos}")

    if not drawables:
        print("Empty scene"); return

    # Painter order: back-to-front. Engine iterates `for z, for x, for y` (GRILLE.C:670):
    # cells with low (gx+gz) project to upper-screen (back), high (gx+gz) to lower-screen (front).
    # Sort ascending by (gx+gz, gy) so back is drawn first, front last (overdraws).
    drawables.sort(key=lambda d: (d[0] + d[2], d[1]))

    # Compute screen positions and canvas bbox
    proj = []
    for gx, gy, gz, bidx in drawables:
        sx = (gx - gz) * 24
        sy = (gx + gz) * 12 - gy * 15
        proj.append((sx, sy))

    # Need brick dims to compute true bbox
    print(f"Pre-decoding {len(set(d[3] for d in drawables))} unique bricks...")
    for d in drawables:
        get_brick(d[3])

    # Compute canvas extents using each brick's footprint.
    # Engine convention (LIB_SVGA AffGraph: ebx += HotX, ecx += HotY before blit):
    # brick top-left = cell_anchor + (HotX, HotY).  Note: ADD, not subtract.
    minx = miny = 10**9
    maxx = maxy = -10**9
    for (gx, gy, gz, bidx), (sx, sy) in zip(drawables, proj):
        b = get_brick(bidx)
        if b is None: continue
        rgba, mask, hx, hy = b
        bw = rgba.shape[1]; bh = rgba.shape[0]
        x0 = sx + hx;       y0 = sy + hy
        x1 = x0 + bw;       y1 = y0 + bh
        if x0 < minx: minx = x0
        if y0 < miny: miny = y0
        if x1 > maxx: maxx = x1
        if y1 > maxy: maxy = y1

    margin = 8
    canvas_w = (maxx - minx + 2*margin) * scale
    canvas_h = (maxy - miny + 2*margin) * scale
    print(f"  canvas: {canvas_w}x{canvas_h}")

    # Background dark blue
    canvas = np.zeros((canvas_h, canvas_w, 4), dtype=np.uint8)
    canvas[:, :, 0] = 16; canvas[:, :, 1] = 18; canvas[:, :, 2] = 28; canvas[:, :, 3] = 255

    # Composite each brick
    for (gx, gy, gz, bidx), (sx, sy) in zip(drawables, proj):
        b = get_brick(bidx)
        if b is None: continue
        rgba, mask, hx, hy = b
        bw = rgba.shape[1]; bh = rgba.shape[0]
        ox = (sx + hx - minx + margin) * scale
        oy = (sy + hy - miny + margin) * scale

        if scale == 1:
            target = canvas[oy:oy+bh, ox:ox+bw]
            if target.shape[:2] != (bh, bw):
                continue
            target[mask] = rgba[mask]
        else:
            # Scale up by repeating
            big_rgba = np.kron(rgba, np.ones((scale, scale, 1), dtype=np.uint8))
            big_mask = np.kron(mask.astype(np.uint8), np.ones((scale, scale), dtype=np.uint8)).astype(bool)
            sh = big_rgba.shape[0]; sw = big_rgba.shape[1]
            target = canvas[oy:oy+sh, ox:ox+sw]
            if target.shape[:2] != (sh, sw):
                continue
            target[big_mask] = big_rgba[big_mask]

    # Flatten to bytes
    write_png_rgba(out_path, canvas_w, canvas_h, canvas.tobytes())
    print(f"Wrote {out_path}")

if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--grid', type=int, default=0)
    ap.add_argument('--brk', default=lba_paths.common('LBA_BRK.HQR'))
    ap.add_argument('--bll', default=lba_paths.common('LBA_BLL.HQR'))
    ap.add_argument('--gri', default=lba_paths.common('LBA_GRI.HQR'))
    ap.add_argument('--ress', default=lba_paths.common('RESS.HQR'))
    ap.add_argument('--out', default=None)
    ap.add_argument('--scale', type=int, default=1)
    args = ap.parse_args()
    out = args.out or lba_paths.repo(rf'extracted\SCENE_render\scene_{args.grid:03d}.png')
    os.makedirs(os.path.dirname(out), exist_ok=True)
    main(args.grid, args.brk, args.bll, args.gri, args.ress, out, args.scale)
