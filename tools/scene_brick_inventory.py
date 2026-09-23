#!/usr/bin/env python3
"""Inventory all 134 LBA1 scenes for cart sizing (Phase A of V4 multi-scene).

For each scene N in LBA_GRI.HQR:
  - decode the grid RLE payload, walk cells, intersect with BLL[N]
  - collect set of unique HQR brick indices referenced
  - measure grid payload size, BLL payload size, brick pixel-bytes (8bpp)

Aggregates:
  - global brick set = union of per-scene brick sets
  - HQR repeat-address dedup for BLL (count of unique storage entries)
  - HQR repeat-address dedup for GRI (sanity — should be 134 distinct)
  - total cart size projection for V4 single-bank layout

Exit: prints summary table + sizing block.

Usage:
  python tools/scene_brick_inventory.py
  python tools/scene_brick_inventory.py --csv out.csv
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import lba_paths
import sys, os, struct, argparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hqr_decompress import parse_offsets, extract_entry
from grid_decode import decode_grid, CUBE_X, CUBE_Y, CUBE_Z
from render_brick import decode_brick


GOG_COMMON = lba_paths.COMMON


def collect_per_scene(scene_idx, gri_data, bll_data, gri_off, bll_off):
    """Returns dict with keys: brick_set, n_bll_blocks, grid_payload_size,
    bll_payload_size, n_filled_cells, ok=True/err=str."""
    try:
        grid_payload = extract_entry(gri_data, scene_idx, gri_off)
    except Exception as e:
        return dict(ok=False, err=f'grid extract: {e}')
    if grid_payload is None:
        return dict(ok=False, err='grid BLANK')

    try:
        cube, _ = decode_grid(grid_payload)
    except Exception as e:
        return dict(ok=False, err=f'grid decode: {e}')

    try:
        bll_payload = extract_entry(bll_data, scene_idx, bll_off)
    except Exception as e:
        return dict(ok=False, err=f'bll extract: {e}')
    if bll_payload is None:
        return dict(ok=False, err='bll BLANK')

    if len(bll_payload) < 4:
        return dict(ok=False, err='bll too short')

    first_off = struct.unpack('<I', bll_payload[0:4])[0]
    if first_off == 0 or first_off % 4 != 0:
        return dict(ok=False, err=f'bad first_off {first_off}')
    n_blocks = first_off // 4

    blocks = []
    for i in range(n_blocks):
        off = struct.unpack('<I', bll_payload[i*4:(i+1)*4])[0]
        if off + 3 > len(bll_payload):
            return dict(ok=False, err=f'block {i} off {off} OOB')
        dx, dy, dz = bll_payload[off], bll_payload[off+1], bll_payload[off+2]
        n_cells = dx * dy * dz
        cells = []
        cp = off + 3
        if cp + n_cells * 4 > len(bll_payload):
            return dict(ok=False, err=f'block {i} cells OOB')
        for _ in range(n_cells):
            info = struct.unpack('<H', bll_payload[cp:cp+2])[0]
            br   = struct.unpack('<H', bll_payload[cp+2:cp+4])[0]
            cells.append((info, br))
            cp += 4
        blocks.append((dx, dy, dz, cells))

    used = set()
    n_filled = 0
    for gz in range(CUBE_Z):
        for gx in range(CUBE_X):
            for gy in range(CUBE_Y):
                v = cube[gz][gx][gy]
                if v == 0:
                    continue
                n_filled += 1
                num_block = v & 0xFF
                pos_block = v >> 8
                if num_block == 0:
                    continue
                if num_block - 1 >= len(blocks):
                    continue
                _, _, _, cells = blocks[num_block - 1]
                if pos_block >= len(cells):
                    continue
                _info, br = cells[pos_block]
                if br != 0:
                    used.add(br - 1)

    return dict(
        ok=True,
        brick_set=used,
        n_bll_blocks=n_blocks,
        grid_payload_size=len(grid_payload),
        bll_payload_size=len(bll_payload),
        n_filled_cells=n_filled,
    )


def measure_brick_pixels(brk_data, brk_off, brick_idx_iter):
    """Sum dx*dy bytes (8bpp) for each brick, padded to 8-byte phrase.
    Returns (total_padded_bytes, count_decoded, count_blank)."""
    total = 0
    n_ok = 0
    n_blank = 0
    for hqr_idx in brick_idx_iter:
        payload = extract_entry(brk_data, hqr_idx, brk_off)
        if payload is None:
            n_blank += 1
            total += 8  # placeholder 1x1 -> still phrase-padded
            continue
        try:
            dx, dy, _hx, _hy, _pix, _alpha, _ = decode_brick(payload)
        except Exception:
            n_blank += 1
            total += 8
            continue
        size = dx * dy
        size = (size + 7) & ~7
        total += size
        n_ok += 1
    return total, n_ok, n_blank


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--brk',  default=os.path.join(GOG_COMMON, 'LBA_BRK.HQR'))
    ap.add_argument('--bll',  default=os.path.join(GOG_COMMON, 'LBA_BLL.HQR'))
    ap.add_argument('--gri',  default=os.path.join(GOG_COMMON, 'LBA_GRI.HQR'))
    ap.add_argument('--csv',  default=None)
    args = ap.parse_args()

    print(f'BRK: {args.brk}')
    print(f'BLL: {args.bll}')
    print(f'GRI: {args.gri}\n')

    with open(args.brk, 'rb') as f: brk_data = f.read()
    with open(args.bll, 'rb') as f: bll_data = f.read()
    with open(args.gri, 'rb') as f: gri_data = f.read()
    brk_off = parse_offsets(brk_data)
    bll_off = parse_offsets(bll_data)
    gri_off = parse_offsets(gri_data)

    print(f'BRK entries: {len(brk_off)}')
    print(f'BLL entries: {len(bll_off)}')
    print(f'GRI entries: {len(gri_off)}\n')

    # HQR repeat-address dedup
    bll_unique_offsets = set(o for o in bll_off if o != 0)
    gri_unique_offsets = set(o for o in gri_off if o != 0)
    bll_blank = sum(1 for o in bll_off if o == 0)
    gri_blank = sum(1 for o in gri_off if o == 0)
    print(f'BLL unique storage entries: {len(bll_unique_offsets)} (blanks: {bll_blank})')
    print(f'GRI unique storage entries: {len(gri_unique_offsets)} (blanks: {gri_blank})\n')

    # Per-scene scan
    n_scenes = len(gri_off)
    rows = []
    global_bricks = set()
    n_ok = 0
    n_err = 0
    err_msgs = []
    for s in range(n_scenes):
        info = collect_per_scene(s, gri_data, bll_data, gri_off, bll_off)
        if not info['ok']:
            n_err += 1
            err_msgs.append((s, info['err']))
            rows.append((s, 0, 0, 0, 0, 0, info['err']))
            continue
        n_ok += 1
        global_bricks |= info['brick_set']
        rows.append((
            s,
            len(info['brick_set']),
            info['n_filled_cells'],
            info['grid_payload_size'],
            info['bll_payload_size'],
            info['n_bll_blocks'],
            'OK',
        ))

    print(f'Per-scene scan: {n_ok} ok, {n_err} err')
    if err_msgs:
        print('Errors:')
        for s, m in err_msgs[:10]:
            print(f'  scene {s:3d}: {m}')
        if len(err_msgs) > 10:
            print(f'  ... +{len(err_msgs)-10} more')
    print()

    # Stats over OK scenes
    ok_rows = [r for r in rows if r[6] == 'OK']
    if ok_rows:
        n_uniq = [r[1] for r in ok_rows]
        gp_sz  = [r[3] for r in ok_rows]
        bp_sz  = [r[4] for r in ok_rows]
        print(f'Per-scene unique bricks: min={min(n_uniq)} avg={sum(n_uniq)//len(n_uniq)} max={max(n_uniq)}')
        print(f'Per-scene grid payload:  min={min(gp_sz):,} avg={sum(gp_sz)//len(gp_sz):,} max={max(gp_sz):,}')
        print(f'Per-scene BLL payload:   min={min(bp_sz):,} avg={sum(bp_sz)//len(bp_sz):,} max={max(bp_sz):,}')
        print(f'Sum of grid payloads:    {sum(gp_sz):,}')
        print(f'Sum of BLL payloads (*including dup HQR storage*): {sum(bp_sz):,}')
        print()

    # Global brick blob sizing
    print(f'Global unique bricks (union across {n_ok} scenes): {len(global_bricks)} / {len(brk_off)} total')
    total_pix, n_dec, n_blk = measure_brick_pixels(brk_data, brk_off, sorted(global_bricks))
    print(f'  decoded: {n_dec}, blanks: {n_blk}')
    print(f'  brick pixels (8bpp, phrase-padded): {total_pix:,} bytes ({total_pix/1024:.1f} KB)')
    print(f'  brick directory (8 B/entry):         {len(global_bricks)*8:,} bytes\n')

    # BLL unique-storage size sum (dedup at HQR level)
    bll_unique_size = 0
    for off in bll_unique_offsets:
        # Need decompressed size — extract by finding the index
        pass  # we'll do it via per-unique-idx below
    # Simpler: pick first occurrence per unique offset
    seen = set()
    unique_bll_payloads_size = 0
    for i, off in enumerate(bll_off):
        if off == 0 or off in seen:
            continue
        seen.add(off)
        try:
            p = extract_entry(bll_data, i, bll_off)
            if p:
                unique_bll_payloads_size += len(p)
        except Exception:
            pass
    print(f'BLL unique-storage decompressed sum: {unique_bll_payloads_size:,} bytes ({unique_bll_payloads_size/1024:.1f} KB)')

    # Sum of unique grid payloads (LBA_GRI dedup is rare but check)
    seen_g = set()
    unique_grid_size = 0
    for i, off in enumerate(gri_off):
        if off == 0 or off in seen_g:
            continue
        seen_g.add(off)
        try:
            p = extract_entry(gri_data, i, gri_off)
            if p:
                unique_grid_size += len(p)
        except Exception:
            pass
    print(f'GRI unique-storage decompressed sum: {unique_grid_size:,} bytes ({unique_grid_size/1024:.1f} KB)')
    print()

    # === V4 cart size projection ===
    print('=== V4 single-bank cart size projection ===')
    HEADER = 64
    SCENE_TABLE_ENTRY = 16  # grid_off(4) grid_size(4) bll_idx(2) palette_idx(1) gx(1) gz(1) pad(3)
    PALETTE_BYTES = 512 * 3   # global + alarm + musee = 1536 B (overestimate)
    BDIR_BYTES = len(global_bricks) * 8

    scene_table_sz = n_scenes * SCENE_TABLE_ENTRY
    grid_total = unique_grid_size  # only unique storage
    bll_total  = unique_bll_payloads_size
    total = HEADER + scene_table_sz + PALETTE_BYTES + BDIR_BYTES + total_pix + grid_total + bll_total

    def pct(b): return f'{b/(6*1024*1024)*100:.1f}%'

    print(f'  Header:                          64 B')
    print(f'  Scene table (134 x 16 B):        {scene_table_sz:>10,} B')
    print(f'  Palette (3 alt):                 {PALETTE_BYTES:>10,} B')
    print(f'  Brick directory:                 {BDIR_BYTES:>10,} B')
    print(f'  Brick pixels (global, 8bpp):     {total_pix:>10,} B   ({pct(total_pix)} of 6 MB)')
    print(f'  Grid payloads (BE, raw):         {grid_total:>10,} B   ({pct(grid_total)})')
    print(f'  BLL payloads (BE, raw):          {bll_total:>10,} B   ({pct(bll_total)})')
    print(f'  -----------------------------------')
    print(f'  TOTAL:                           {total:>10,} B   ({total/1024:.1f} KB, {total/(1024*1024):.2f} MB, {pct(total)} of 6 MB)')
    print()
    if total < 6 * 1024 * 1024:
        print(f'  ==> FITS in 6 MB cart, NO bank switching needed for V4.')
        print(f'      Headroom: {(6*1024*1024 - total):,} B ({(6*1024*1024-total)/1024/1024:.2f} MB)')
    else:
        print(f'  ==> EXCEEDS 6 MB by {total - 6*1024*1024:,} B — bank switching required.')

    # CSV out
    if args.csv:
        with open(args.csv, 'w') as f:
            f.write('scene,unique_bricks,filled_cells,grid_size,bll_size,n_bll_blocks,status\n')
            for r in rows:
                f.write(','.join(str(x) for x in r) + '\n')
        print(f'\nWrote {args.csv}')


if __name__ == '__main__':
    main()
