#!/usr/bin/env python3
"""Verify scene_all.bin (JAGLBA02) integrity: structure, ranges, dedup, sample decode.

Checks:
  1. Header magic + version
  2. Section offsets monotonic + within file
  3. Scene table: bll_idx in [0, bll_unique_count); transitions in [0, scene_count) or 0xFF
  4. Brick directory: offsets monotonic + within brick_blob; dx/dy non-zero
  5. BLL table: offsets monotonic + within bll_blob
  6. Grid table: offsets monotonic + within grid_blob
  7. Spot decode: scene 0, walk grid via BLL, count brick refs, verify in [1, brick_count]
  8. Spot decode: brick 0 RLE, verify dx*dy positive, walk RLE rows, no overruns

Exit nonzero if any check fails.

Usage:
  python tools/cart_verify_all.py [path_to_scene_all.bin]
"""
import sys, struct, argparse


MAGIC = b'JAGLBA02'
VERSION = 2
HEADER_SIZE = 64


def fail(msg):
    print(f'  FAIL: {msg}')
    return False


def parse_header(data):
    if len(data) < HEADER_SIZE:
        raise ValueError('file too small for header')
    magic = data[0:8]
    if magic != MAGIC:
        raise ValueError(f'magic {magic!r} != {MAGIC!r}')
    version, = struct.unpack('>I', data[8:12])
    if version != VERSION:
        raise ValueError(f'version {version} != {VERSION}')
    fields = {
        'scene_count':       struct.unpack('>H', data[0x0C:0x0E])[0],
        'brick_count':       struct.unpack('>H', data[0x0E:0x10])[0],
        'palette_off':       struct.unpack('>I', data[0x10:0x14])[0],
        'scene_table_off':   struct.unpack('>I', data[0x14:0x18])[0],
        'brick_dir_off':     struct.unpack('>I', data[0x18:0x1C])[0],
        'brick_blob_off':    struct.unpack('>I', data[0x1C:0x20])[0],
        'brick_blob_size':   struct.unpack('>I', data[0x20:0x24])[0],
        'bll_table_off':     struct.unpack('>I', data[0x24:0x28])[0],
        'bll_unique_count':  struct.unpack('>H', data[0x28:0x2A])[0],
        'bll_blob_off':      struct.unpack('>I', data[0x2C:0x30])[0],
        'bll_blob_size':     struct.unpack('>I', data[0x30:0x34])[0],
        'grid_table_off':    struct.unpack('>I', data[0x34:0x38])[0],
        'grid_blob_off':     struct.unpack('>I', data[0x38:0x3C])[0],
        'file_size':         struct.unpack('>I', data[0x3C:0x40])[0],
    }
    return fields


def check_section_layout(data, h):
    print('\n[1] Section layout')
    ok = True
    sections = [
        ('palette',     h['palette_off'],     512),
        ('scene_table', h['scene_table_off'], h['scene_count'] * 16),
        ('brick_dir',   h['brick_dir_off'],   h['brick_count'] * 8),
        ('brick_blob',  h['brick_blob_off'],  h['brick_blob_size']),
        ('bll_table',   h['bll_table_off'],   h['bll_unique_count'] * 4),
        ('bll_blob',    h['bll_blob_off'],    h['bll_blob_size']),
        ('grid_table',  h['grid_table_off'],  h['scene_count'] * 4),
        ('grid_blob',   h['grid_blob_off'],   h['file_size'] - h['grid_blob_off']),
    ]
    for name, off, size in sections:
        if off < HEADER_SIZE:
            ok = fail(f'{name} offset {off} < HEADER_SIZE'); continue
        if off + size > len(data) + 8:  # allow tiny rounding pad
            ok = fail(f'{name} extends beyond file ({off}+{size} > {len(data)})'); continue
        print(f'  {name:14s}: 0x{off:08X}+{size:,} OK')
    if h['file_size'] != len(data):
        ok = fail(f'header file_size={h["file_size"]} != actual={len(data)}')
    return ok


def check_scene_table(data, h):
    print('\n[2] Scene table')
    ok = True
    base = h['scene_table_off']
    bad = 0
    for s in range(h['scene_count']):
        e = base + s * 16
        bank_idx, bll_uniq_idx, palette_idx, _ = struct.unpack('>BBBB', data[e:e+4])
        cam_gx, cam_gz, _, _ = struct.unpack('>BBBB', data[e+4:e+8])
        tx_r, tx_l, tx_d, tx_u = struct.unpack('>BBBB', data[e+8:e+12])
        if bll_uniq_idx >= h['bll_unique_count']:
            bad += 1
            if bad <= 3: fail(f'scene {s}: bll_uniq_idx {bll_uniq_idx} >= {h["bll_unique_count"]}')
            ok = False
        if palette_idx != 0:
            bad += 1
            if bad <= 3: fail(f'scene {s}: palette_idx {palette_idx} != 0 (V4 only one palette)')
            ok = False
        for tname, tv in (('R', tx_r), ('L', tx_l), ('D', tx_d), ('U', tx_u)):
            if tv != 0xFF and tv >= h['scene_count']:
                bad += 1
                if bad <= 3: fail(f'scene {s}: tx_{tname}={tv} out of range')
                ok = False
    print(f'  {h["scene_count"]} entries scanned, {bad} errors')
    return ok


def check_brick_dir(data, h):
    print('\n[3] Brick directory')
    ok = True
    base = h['brick_dir_off']
    prev_off = -1
    bad_dim = 0
    bad_off = 0
    for b in range(h['brick_count']):
        e = base + b * 8
        boff = struct.unpack('>I', data[e:e+4])[0]
        dx, dy, hx, hy = data[e+4], data[e+5], data[e+6], data[e+7]
        if boff < prev_off:
            bad_off += 1
            if bad_off <= 3: fail(f'brick {b}: offset {boff} < prev {prev_off} (not monotonic)')
            ok = False
        if boff >= h['brick_blob_size']:
            bad_off += 1
            if bad_off <= 3: fail(f'brick {b}: offset {boff} >= blob_size {h["brick_blob_size"]}')
            ok = False
        if dx == 0 or dy == 0:
            bad_dim += 1
            if bad_dim <= 3: fail(f'brick {b}: zero dim ({dx}x{dy})')
            ok = False
        prev_off = boff
    print(f'  {h["brick_count"]} entries scanned, {bad_off} offset issues, {bad_dim} zero-dim')
    return ok


def check_bll_table(data, h):
    print('\n[4] BLL table')
    ok = True
    base = h['bll_table_off']
    prev = -1
    for b in range(h['bll_unique_count']):
        off = struct.unpack('>I', data[base + b*4 : base + b*4 + 4])[0]
        if off < prev:
            ok = fail(f'BLL {b}: offset {off} < prev {prev}')
        if off >= h['bll_blob_size']:
            ok = fail(f'BLL {b}: offset {off} >= blob_size {h["bll_blob_size"]}')
        prev = off
    print(f'  {h["bll_unique_count"]} entries scanned')
    return ok


def check_grid_table(data, h):
    print('\n[5] Grid table')
    ok = True
    base = h['grid_table_off']
    prev = -1
    grid_blob_size = h['file_size'] - h['grid_blob_off']
    for g in range(h['scene_count']):
        off = struct.unpack('>I', data[base + g*4 : base + g*4 + 4])[0]
        if off < prev:
            ok = fail(f'grid {g}: offset {off} < prev {prev}')
        if off >= grid_blob_size:
            ok = fail(f'grid {g}: offset {off} >= grid_blob_size {grid_blob_size}')
        prev = off
    print(f'  {h["scene_count"]} entries scanned')
    return ok


def spot_check_scene_0(data, h):
    print('\n[6] Spot decode: scene 0')
    ok = True
    # Get scene 0's BLL idx
    bll_uniq_idx = data[h['scene_table_off'] + 1]
    # Get BLL payload offset
    bll_off = struct.unpack('>I', data[h['bll_table_off'] + bll_uniq_idx*4 : h['bll_table_off'] + bll_uniq_idx*4 + 4])[0]
    bll_base = h['bll_blob_off'] + bll_off
    # Parse BLL: BE u32 offset table + blocks
    first_block_off = struct.unpack('>I', data[bll_base : bll_base+4])[0]
    n_blocks = first_block_off // 4
    print(f'  scene 0 -> bll_uniq_idx {bll_uniq_idx}, n_blocks {n_blocks}')

    # Walk grid 0, count drawables, verify brick refs
    grid_off = struct.unpack('>I', data[h['grid_table_off'] : h['grid_table_off']+4])[0]
    grid_base = h['grid_blob_off'] + grid_off
    # Grid layout: 8192 B BE u16 offset table + RLE columns (BE cells)
    n_filled = 0
    n_drawable = 0
    n_invalid = 0
    seen_brick = set()
    for col_idx in range(4096):
        col_off = struct.unpack('>H', data[grid_base + col_idx*2 : grid_base + col_idx*2 + 2])[0]
        cp = grid_base + col_off
        nb_entity = data[cp]; cp += 1
        cell_y = 0
        for _ in range(nb_entity):
            op = data[cp]; cp += 1
            count = (op & 0x3F) + 1
            mode = op & 0xC0
            if mode == 0x00:
                cell_y += count
                continue
            if mode == 0x80:
                v = struct.unpack('>H', data[cp:cp+2])[0]; cp += 2
                vals = [v] * count
            else:
                vals = [struct.unpack('>H', data[cp + i*2 : cp + i*2 + 2])[0] for i in range(count)]
                cp += count * 2
            for v in vals:
                if v != 0:
                    n_filled += 1
                    nb = v & 0xFF; pb = v >> 8
                    if nb == 0:
                        cell_y += 1; continue
                    if nb - 1 >= n_blocks:
                        n_invalid += 1; cell_y += 1; continue
                    block_off = struct.unpack('>I', data[bll_base + (nb-1)*4 : bll_base + (nb-1)*4 + 4])[0]
                    block_addr = bll_base + block_off
                    dx, dy, dz = data[block_addr], data[block_addr+1], data[block_addr+2]
                    n_cells = dx * dy * dz
                    if pb >= n_cells:
                        n_invalid += 1; cell_y += 1; continue
                    cell_addr = block_addr + 3 + pb * 4
                    info = struct.unpack('>H', data[cell_addr:cell_addr+2])[0]
                    br = struct.unpack('>H', data[cell_addr+2:cell_addr+4])[0]
                    if br != 0:
                        if br > h['brick_count']:
                            n_invalid += 1
                        else:
                            n_drawable += 1
                            seen_brick.add(br)
                cell_y += 1
    print(f'  filled cells: {n_filled}')
    print(f'  drawable    : {n_drawable}')
    print(f'  invalid refs: {n_invalid}')
    print(f'  unique bricks: {len(seen_brick)}')
    if n_invalid > 0:
        ok = fail(f'{n_invalid} invalid brick refs')
    if len(seen_brick) == 0:
        ok = fail('zero bricks referenced')
    return ok


def spot_check_brick_0(data, h):
    print('\n[7] Spot decode: brick 0 RLE')
    e = h['brick_dir_off']
    boff = struct.unpack('>I', data[e:e+4])[0]
    dx, dy, hx, hy = data[e+4], data[e+5], data[e+6], data[e+7]
    print(f'  brick 0: dx={dx} dy={dy} hot=({hx},{hy}) blob_off={boff}')
    payload_base = h['brick_blob_off'] + boff
    # payload is HQR-decompressed RLE: 4-byte header + per-row RLE
    # verify header dims match brick_dir
    p_dx, p_dy, p_hx, p_hy = data[payload_base], data[payload_base+1], data[payload_base+2], data[payload_base+3]
    if (p_dx, p_dy, p_hx, p_hy) != (dx, dy, hx, hy):
        return fail(f'brick 0: blob header ({p_dx}x{p_dy},{p_hx},{p_hy}) != dir ({dx}x{dy},{hx},{hy})')
    # Walk RLE rows
    pos = payload_base + 4
    end_max = payload_base + 4096  # generous upper bound
    for row in range(dy):
        if pos >= end_max:
            return fail(f'brick 0: RLE walk exceeds {end_max} at row {row}')
        nb_block = data[pos]; pos += 1
        col = 0
        for blk in range(nb_block):
            op = data[pos]; pos += 1
            count = (op & 0x3F) + 1
            mode = op & 0xC0
            if mode == 0x00:
                col += count
            elif mode == 0x40:
                pos += count
                col += count
            elif mode == 0x80:
                pos += 1
                col += count
        if col > dx:
            return fail(f'brick 0: row {row} overran dx ({col} > {dx})')
    print(f'  OK: {dy} rows decoded cleanly')
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('cart', nargs='?', default=r'd:\Homebrew5\JagLBA\cart\scene_all.bin')
    args = ap.parse_args()

    with open(args.cart, 'rb') as f:
        data = f.read()
    print(f'Cart: {args.cart}  ({len(data):,} bytes)')

    h = parse_header(data)
    print(f'Magic OK, version {VERSION}')
    print(f'  scenes: {h["scene_count"]}, bricks: {h["brick_count"]}, BLL unique: {h["bll_unique_count"]}')

    results = [
        check_section_layout(data, h),
        check_scene_table(data, h),
        check_brick_dir(data, h),
        check_bll_table(data, h),
        check_grid_table(data, h),
        spot_check_scene_0(data, h),
        spot_check_brick_0(data, h),
    ]
    print()
    if all(results):
        print('=== ALL CHECKS PASSED ===')
        return 0
    else:
        print(f'=== {sum(1 for r in results if not r)} of {len(results)} CHECKS FAILED ===')
        return 1


if __name__ == '__main__':
    sys.exit(main())
