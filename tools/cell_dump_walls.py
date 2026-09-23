#!/usr/bin/env python3
"""Dump raw BLL cell data for wall vs floor candidates in a given grid.

Goal: test hypothesis 4 from session 1 checkpoint — does the 1st UWORD per BLL cell
encode a Y sub-offset (or zone_id, orientation, etc.)?

Strategy:
  - Decode grid N (default 0 = Citadel Prison)
  - Identify "wall column" cells: continuous fill across gy >= some threshold at fixed (gx, gz)
  - Identify "floor" cells: filled at gy=0 or gy=1, with no fill above gy=2 in same (gx,gz)
  - For each candidate, dump:
      gx, gy, gz, raw_grid_uword, NumBlock, PosBlock,
      bll_block_dim (dx,dy,dz), pos_within_block,
      1st_UWORD (the 'info' field), 2nd_UWORD (brick_ref-1 = brick_idx),
      brick metadata (DX, DY, HotX, HotY)
  - Aggregate: for each wall column, list (gy, 1st_UWORD) — does 1st_UWORD correlate with gy?
"""
import sys, os, struct, argparse
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hqr_decompress import parse_offsets, extract_entry
from grid_decode import decode_grid, CUBE_X, CUBE_Y, CUBE_Z
from render_brick import decode_brick


def parse_bll_full(payload):
    """Parse a BLL entry returning per-block FULL cell tuples (info_uword, brick_ref).
    Returns dict {block_idx_0based: (dx, dy, dz, [(info, brick_ref), ...])}.
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
        cells = []
        cp = off + 3
        for _ in range(n_cells):
            info = struct.unpack('<H', payload[cp:cp+2])[0]
            br   = struct.unpack('<H', payload[cp+2:cp+4])[0]
            cells.append((info, br))
            cp += 4
        blocks[i] = (dx, dy, dz, cells)
    return blocks


def find_wall_columns(cube, min_height=3, min_top_gy=4):
    """Return list of (gx, gz, [gy_filled_list]) where gy_filled_list is the
    sorted list of filled Y levels at that (gx,gz), with at least min_height
    filled and reaching at least min_top_gy."""
    cols = []
    for gz in range(CUBE_Z):
        for gx in range(CUBE_X):
            ys = [gy for gy in range(CUBE_Y) if cube[gz][gx][gy] != 0]
            if len(ys) >= min_height and max(ys) >= min_top_gy:
                cols.append((gx, gz, ys))
    return cols


def find_floor_cells(cube, ground_y=0):
    """Return list of (gx, gz) where gy=ground_y is filled and gy>=2 is empty."""
    cells = []
    for gz in range(CUBE_Z):
        for gx in range(CUBE_X):
            col = cube[gz][gx]
            if col[ground_y] == 0:
                continue
            if any(col[gy] != 0 for gy in range(2, CUBE_Y)):
                continue
            cells.append((gx, gz))
    return cells


def lookup_cell(grid_idx, gx, gy, gz, cube, bll_blocks, brk_data, brk_offsets):
    raw = cube[gz][gx][gy]
    if raw == 0:
        return None
    num_block = raw & 0xFF
    pos_block = raw >> 8
    if num_block == 0:
        return None
    bll_block = bll_blocks.get(num_block - 1)
    if bll_block is None:
        return {'raw': raw, 'NumBlock': num_block, 'PosBlock': pos_block,
                'error': 'BLL block missing'}
    dx, dy, dz, cells = bll_block
    if pos_block >= len(cells):
        return {'raw': raw, 'NumBlock': num_block, 'PosBlock': pos_block,
                'block_dim': (dx, dy, dz),
                'error': f'PosBlock {pos_block} out of range {len(cells)}'}
    info, brick_ref = cells[pos_block]
    out = {
        'raw': raw,
        'NumBlock': num_block,
        'PosBlock': pos_block,
        'block_dim': (dx, dy, dz),
        'info_uword': info,
        'brick_ref': brick_ref,
    }
    if brick_ref == 0:
        out['brick'] = None
        return out
    bidx = brick_ref - 1
    try:
        bp = extract_entry(brk_data, bidx, brk_offsets)
        if bp is None or len(bp) < 4:
            out['brick'] = ('blank/short', bidx)
        else:
            bdx, bdy, hx, hy = bp[0], bp[1], bp[2], bp[3]
            out['brick'] = {'idx': bidx, 'DX': bdx, 'DY': bdy,
                            'HotX': hx, 'HotY': hy}
    except Exception as e:
        out['brick'] = ('error', str(e))
    return out


def fmt_cell(c):
    if c is None:
        return '(empty)'
    if 'error' in c:
        return (f'raw=0x{c["raw"]:04X} NB={c["NumBlock"]:>3} PB={c["PosBlock"]:>3} '
                f'ERR={c["error"]}')
    info = c['info_uword']
    br   = c['brick_ref']
    bd   = c['block_dim']
    bk   = c.get('brick')
    bk_s = ''
    if isinstance(bk, dict):
        bk_s = f"brick={bk['idx']:>4} {bk['DX']}x{bk['DY']} Hot=({bk['HotX']},{bk['HotY']})"
    elif bk is None:
        bk_s = 'brick=NONE(ref=0)'
    else:
        bk_s = f'brick={bk}'
    return (f'NB={c["NumBlock"]:>3} PB={c["PosBlock"]:>3} '
            f'block={bd[0]}x{bd[1]}x{bd[2]} '
            f'info=0x{info:04X}({info:>5}) bref={br:>4} {bk_s}')


def main(grid_idx, gri_path, bll_path, brk_path, n_walls, n_floors):
    print(f'=== cell_dump_walls grid {grid_idx} ===')
    with open(gri_path, 'rb') as f: gri = f.read()
    with open(bll_path, 'rb') as f: bll = f.read()
    with open(brk_path, 'rb') as f: brk = f.read()
    gri_off = parse_offsets(gri)
    bll_off = parse_offsets(bll)
    brk_off = parse_offsets(brk)

    payload = extract_entry(gri, grid_idx, gri_off)
    cube, _ = decode_grid(payload)

    bll_payload = extract_entry(bll, grid_idx, bll_off)
    bll_blocks = parse_bll_full(bll_payload)
    print(f'BLL[{grid_idx}] -> {len(bll_blocks)} blocks')

    walls = find_wall_columns(cube)
    floors = find_floor_cells(cube)
    print(f'Found {len(walls)} wall-candidate columns, {len(floors)} pure-floor cells')

    # Sort wall columns by height (most fill first), pick top N
    walls.sort(key=lambda w: -len(w[2]))
    walls = walls[:n_walls]
    # Pick floor cells distributed across the grid (not all clustered)
    if n_floors > 0:
        step = max(1, len(floors) // n_floors)
        floors = floors[::step][:n_floors]

    print('\n=== WALL COLUMNS ===')
    print('(looking for: does info_uword change with gy? does it correlate with brick HotY/DY?)')
    info_per_gy = defaultdict(list)  # gy -> list of info_uwords across columns
    for gx, gz, ys in walls:
        print(f'\nColumn (gx={gx}, gz={gz})  filled_ys={ys}')
        for gy in ys:
            info = lookup_cell(grid_idx, gx, gy, gz, cube, bll_blocks, brk, brk_off)
            print(f'  gy={gy}: {fmt_cell(info)}')
            if info and 'info_uword' in info:
                info_per_gy[gy].append(info['info_uword'])

    print('\n=== FLOOR CELLS ===')
    print('(reference: pure floor cells should have a "baseline" info_uword)')
    floor_infos = []
    for gx, gz in floors:
        for gy in range(2):
            if cube[gz][gx][gy] != 0:
                info = lookup_cell(grid_idx, gx, gy, gz, cube, bll_blocks, brk, brk_off)
                print(f'  (gx={gx:>2}, gy={gy}, gz={gz:>2}): {fmt_cell(info)}')
                if info and 'info_uword' in info:
                    floor_infos.append(info['info_uword'])

    print('\n=== SUMMARY: info_uword distribution per gy (across wall columns) ===')
    for gy in sorted(info_per_gy.keys()):
        vals = info_per_gy[gy]
        unique = sorted(set(vals))
        print(f'  gy={gy:>2}: n={len(vals):>3}  unique_count={len(unique):>3}  '
              f'sample={[f"0x{v:04X}" for v in unique[:8]]}')

    if floor_infos:
        unique_floor = sorted(set(floor_infos))
        print(f'\nFloor info_uwords: n={len(floor_infos)} unique_count={len(unique_floor)}')
        print(f'  sample={[f"0x{v:04X}" for v in unique_floor[:16]]}')


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--grid', type=int, default=0)
    ap.add_argument('--gri', default=r'I:\GOG\Little Big Adventure\Common\LBA_GRI.HQR')
    ap.add_argument('--bll', default=r'I:\GOG\Little Big Adventure\Common\LBA_BLL.HQR')
    ap.add_argument('--brk', default=r'I:\GOG\Little Big Adventure\Common\LBA_BRK.HQR')
    ap.add_argument('--walls', type=int, default=8, help='number of wall columns to dump')
    ap.add_argument('--floors', type=int, default=10, help='number of floor cells to dump')
    args = ap.parse_args()
    main(args.grid, args.gri, args.bll, args.brk, args.walls, args.floors)
