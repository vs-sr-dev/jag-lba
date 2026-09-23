#!/usr/bin/env python3
"""Pack a single LBA1 scene into Jaguar cart format.

Output: cart/scene_NNN.bin = a self-contained binary the Jaguar 68k can incbin
and walk to render the scene without any HQR knowledge.

Format (all multi-byte values BIG-ENDIAN unless noted):

  HEADER (64 bytes, 4-aligned):
    0x00  8B   magic   = b"JAGLBA01"
    0x08  u32  version = 1
    0x0C  u32  scene_id (grid index in source HQR)
    0x10  u32  palette_off
    0x14  u32  palette_size  (= 512 always)
    0x18  u32  grid_off
    0x1C  u32  grid_size
    0x20  u32  bll_off
    0x24  u32  bll_size
    0x28  u32  bll_count       (n_blocks in BLL)
    0x2C  u32  bdir_off
    0x30  u32  bdir_count      (= num_unique_bricks)
    0x34  u32  bpix_off
    0x38  u32  bpix_size
    0x3C  u32  file_size

  PALETTE (512 B): 256 entries x 2 bytes Jaguar RGB16 BE (R5-B5-G6).

  GRID (variable): same column-RLE format as original LBA, but UWORDs converted to BE.
    - 4096 BE UWORDs offset table (8192 B)
    - RLE columns: opcodes + BE UWORDs

  BLL (variable): same TabBlock format as original LBA, but:
    - ULONGs in offset table converted to BE
    - per-cell UWORDs (info + brick_ref) converted to BE
    - brick_ref values REMAPPED from HQR-idx to local-idx (1-based local; 0 = empty)

  BRICK DIRECTORY (8 B per entry):
    u32 pixel_offset_within_bpix
    u8  dx, u8 dy, u8 hot_x, u8 hot_y

  BRICK PIXELS (variable):
    contiguous dx*dy bytes per brick, palette idx 0 = transparent (verified
    via tools/check_palette0.py — 0/4M opaque pixels use idx 0).
    Each brick padded up to next 8-byte boundary for Blitter phrase alignment.

Endianness: all multi-byte BE. 68k native, no byte-swap needed at runtime.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import lba_paths
import sys, os, struct, argparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hqr_decompress import parse_offsets, extract_entry
from grid_decode import decode_grid, CUBE_X, CUBE_Y, CUBE_Z
from render_brick import decode_brick
from render_slide import normalize_palette


MAGIC = b"JAGLBA01"
VERSION = 1
HEADER_SIZE = 64


def rgb_to_jag16(r, g, b):
    """Pack RGB(8,8,8) into Jaguar RGB16 (R5-B5-G6) BE word."""
    r5 = (r >> 3) & 0x1F
    b5 = (b >> 3) & 0x1F
    g6 = (g >> 2) & 0x3F
    return (r5 << 11) | (b5 << 6) | g6


def collect_used_bricks(grid_idx, gri_data, bll_data, gri_off, bll_off):
    """Walk grid + BLL to find unique HQR brick indices used by scene.
    Returns (sorted_unique_brick_indices, set, bll_payload_raw)."""
    payload = extract_entry(gri_data, grid_idx, gri_off)
    cube, _ = decode_grid(payload)

    bll_payload = extract_entry(bll_data, grid_idx, bll_off)

    # Parse BLL block table to find what brick_refs the cells point to.
    # Layout: ULONG offset table + blocks. Each block: 3-byte (dx,dy,dz) + n_cells * 4 bytes (info+brick_ref).
    if len(bll_payload) < 4:
        raise ValueError(f"BLL[{grid_idx}] too short")
    first_off = struct.unpack('<I', bll_payload[0:4])[0]
    n_blocks = first_off // 4
    blocks = []  # list of (dx, dy, dz, [(info, brick_ref) per cell])
    for i in range(n_blocks):
        off = struct.unpack('<I', bll_payload[i*4:(i+1)*4])[0]
        dx, dy, dz = bll_payload[off], bll_payload[off+1], bll_payload[off+2]
        n_cells = dx * dy * dz
        cells = []
        cp = off + 3
        for _ in range(n_cells):
            info = struct.unpack('<H', bll_payload[cp:cp+2])[0]
            br   = struct.unpack('<H', bll_payload[cp+2:cp+4])[0]
            cells.append((info, br))
            cp += 4
        blocks.append((dx, dy, dz, cells))

    # Collect brick_refs ACTUALLY referenced by cells in cube (not all BLL cells).
    used_bricks = set()
    for gz in range(CUBE_Z):
        for gx in range(CUBE_X):
            for gy in range(CUBE_Y):
                v = cube[gz][gx][gy]
                if v == 0: continue
                num_block = v & 0xFF
                pos_block = v >> 8
                if num_block == 0: continue
                if num_block - 1 >= len(blocks): continue
                _, _, _, cells = blocks[num_block - 1]
                if pos_block >= len(cells): continue
                info, br = cells[pos_block]
                if br != 0:
                    used_bricks.add(br - 1)  # 0-based HQR index

    return sorted(used_bricks), blocks, bll_payload


def build_palette_section(palette):
    """palette = list of (r,g,b) tuples, len 256. Returns 512 bytes BE RGB16.

    Reserve RGB16 0x0000 exclusively for palette idx 0 (transparent sentinel).
    For Jaguar Blitter DCOMPEN-style masking and CPU skip-on-zero blits, we need
    a single sentinel value. Force any non-zero palette entry that would quantize
    to 0x0000 (i.e., very dark colors) to 0x0001 to avoid false transparency.
    """
    assert len(palette) == 256
    buf = bytearray()
    n_remapped = 0
    for i, (r, g, b) in enumerate(palette):
        rgb16 = rgb_to_jag16(r, g, b)
        if i != 0 and rgb16 == 0:
            rgb16 = 1
            n_remapped += 1
        buf += struct.pack('>H', rgb16)
    if n_remapped:
        print(f'  Palette: forced {n_remapped} dark non-zero entries to 0x0001 (sentinel-collision avoidance)')
    return bytes(buf)


def build_grid_section(payload):
    """Convert original LE grid (offset table + RLE columns) to BE UWORDs.
    Layout preserved; just byte-swap UWORDs."""
    out = bytearray()
    # Offset table: 4096 UWORDs = 8192 bytes
    if len(payload) < 8192:
        raise ValueError(f"grid payload too short: {len(payload)}")
    for i in range(4096):
        v = struct.unpack('<H', payload[i*2:i*2+2])[0]
        out += struct.pack('>H', v)

    # Column RLE: walk each column header and swap UWORDs in 'diff'/'same' modes
    pos = 8192
    out += b'\x00' * (len(payload) - 8192)  # init with original size
    rle = bytearray(out[8192:])

    src = payload[8192:]
    sp = 0
    dp = 0

    # We need to walk the columns the same way decomp_column does, byte-swapping cell UWORDs.
    # Each column: BYTE NbEntity, then per entity: opcode + payload depending on mode.
    # 64*64 = 4096 columns — but only the ones referenced by offset table are used.
    # Cleaner approach: copy block of payload[8192:], then walk once and byte-swap UWORDs in place.
    rle = bytearray(payload[8192:])

    # Walk each column from the offset table. Some columns may share offset (deduped). We need
    # to byte-swap each UWORD only once, so dedupe offsets first.
    swapped_offsets = set()
    for i in range(4096):
        col_off = struct.unpack('<H', payload[i*2:i*2+2])[0]
        if col_off in swapped_offsets:
            continue
        swapped_offsets.add(col_off)
        # col_off is byte offset into the FULL payload (including the 8192 header)
        local = col_off - 8192
        if local < 0: continue
        nb_entity = rle[local]
        cp = local + 1
        for _ in range(nb_entity):
            op = rle[cp]; cp += 1
            count = (op & 0x3F) + 1
            mode = op & 0xC0
            if mode == 0x00:
                pass  # zero fill, no payload bytes
            elif mode == 0x80:
                # repeat: 1 UWORD payload, swap it
                lo = rle[cp]; hi = rle[cp+1]
                rle[cp] = hi; rle[cp+1] = lo
                cp += 2
            else:  # diff (01 or 11): N UWORDs
                for _u in range(count):
                    lo = rle[cp]; hi = rle[cp+1]
                    rle[cp] = hi; rle[cp+1] = lo
                    cp += 2

    out = bytearray()
    # rebuild: BE offset table + swapped RLE
    for i in range(4096):
        v = struct.unpack('<H', payload[i*2:i*2+2])[0]
        out += struct.pack('>H', v)
    out += bytes(rle)
    return bytes(out)


def build_bll_section(blocks, bll_payload, brick_remap):
    """Re-emit the BLL as BE with brick_refs remapped to local indices.

    blocks: list of (dx, dy, dz, cells_list_le) parsed from original
    brick_remap: dict {hqr_idx: local_idx_1based} — 0 stays 0 (empty)

    Layout: same as original (offset table + blocks), but:
      - offset table ULONGs in BE
      - per-cell info UWORD in BE
      - per-cell brick_ref UWORD remapped + BE
      - dx/dy/dz bytes unchanged
    """
    n_blocks = len(blocks)
    # Compute new layout: offset table at start (n_blocks * 4 bytes), then blocks.
    block_bytes = []
    for dx, dy, dz, cells in blocks:
        b = bytearray()
        b += bytes([dx, dy, dz])
        for info, br in cells:
            local = 0 if br == 0 else brick_remap.get(br - 1, 0)
            b += struct.pack('>H', info)
            b += struct.pack('>H', local)
        block_bytes.append(bytes(b))

    # Compute offsets (relative to start of BLL section)
    offsets = []
    cur = n_blocks * 4
    for bb in block_bytes:
        offsets.append(cur)
        cur += len(bb)

    out = bytearray()
    for o in offsets:
        out += struct.pack('>I', o)
    for bb in block_bytes:
        out += bb
    return bytes(out)


def pad_to(buf, alignment):
    pad = (-len(buf)) % alignment
    return buf + b'\x00' * pad


def build_brick_pixels(brick_data_list):
    """brick_data_list: list of (dx, dy, hot_x, hot_y, pixels_2d_or_flat).
    Returns (bdir_bytes, bpix_bytes).

    bdir entry: u32 offset_in_bpix, u8 dx, u8 dy, u8 hot_x, u8 hot_y. = 8 bytes.
    bpix: contiguous dx*dy bytes per brick (palette indices, 0 = transparent),
          padded to 8-byte phrase boundary.
    """
    bdir = bytearray()
    bpix = bytearray()

    for dx, dy, hx, hy, pixels in brick_data_list:
        offset = len(bpix)
        # Flatten pixel data
        flat = bytearray()
        for y in range(dy):
            row = pixels[y]
            for x in range(dx):
                flat.append(row[x] & 0xFF)
        bpix += flat
        # Pad to phrase (8-byte) boundary
        pad = (-len(bpix)) % 8
        bpix += b'\x00' * pad

        bdir += struct.pack('>I', offset)
        bdir += bytes([dx, dy, hx, hy])

    return bytes(bdir), bytes(bpix)


def main(scene_id, brk_path, bll_path, gri_path, ress_path, out_path):
    print(f'Packing scene {scene_id}')
    print(f'  BRK:  {brk_path}')
    print(f'  BLL:  {bll_path}')
    print(f'  GRI:  {gri_path}')
    print(f'  RESS: {ress_path}')

    with open(brk_path, 'rb') as f: brk_data = f.read()
    with open(bll_path, 'rb') as f: bll_data = f.read()
    with open(gri_path, 'rb') as f: gri_data = f.read()
    with open(ress_path, 'rb') as f: ress_data = f.read()
    brk_off = parse_offsets(brk_data)
    bll_off = parse_offsets(bll_data)
    gri_off = parse_offsets(gri_data)
    ress_off = parse_offsets(ress_data)

    # 1. Palette
    pal_raw = extract_entry(ress_data, 0, ress_off)
    palette = normalize_palette(pal_raw)
    pal_bytes = build_palette_section(palette)
    print(f'\nPalette: {len(pal_bytes)} bytes (256 RGB16 entries)')

    # 2. Identify used bricks
    used_bricks_hqr, blocks, bll_payload = collect_used_bricks(
        scene_id, gri_data, bll_data, gri_off, bll_off)
    print(f'Unique bricks used by scene: {len(used_bricks_hqr)}')

    # Build remap: hqr_idx -> local_idx_1based (so 0 means empty)
    brick_remap = {h: i + 1 for i, h in enumerate(used_bricks_hqr)}

    # 3. Decode bricks
    print('Decoding bricks...')
    brick_data_list = []
    n_used = 0
    for hqr_idx in used_bricks_hqr:
        payload = extract_entry(brk_data, hqr_idx, brk_off)
        if payload is None:
            print(f'  WARN: brick {hqr_idx} BLANK, substituting empty 1x1')
            brick_data_list.append((1, 1, 0, 0, [[0]]))
            continue
        dx, dy, hx, hy, pixels, alpha, _ = decode_brick(payload)
        # Where alpha=0, force pixel=0 (transparent in cart format)
        for y in range(dy):
            for x in range(dx):
                if alpha[y][x] == 0:
                    pixels[y][x] = 0
        brick_data_list.append((dx, dy, hx, hy, pixels))
        n_used += 1
    print(f'  Decoded {n_used} bricks')

    bdir_bytes, bpix_bytes = build_brick_pixels(brick_data_list)
    print(f'Brick directory: {len(bdir_bytes)} bytes ({len(used_bricks_hqr)} entries × 8B)')
    print(f'Brick pixels:    {len(bpix_bytes):,} bytes (phrase-padded)')

    # 4. Grid (BE)
    grid_raw = extract_entry(gri_data, scene_id, gri_off)
    grid_bytes = build_grid_section(grid_raw)
    print(f'Grid: {len(grid_bytes)} bytes (BE)')

    # 5. BLL (remapped, BE)
    bll_bytes = build_bll_section(blocks, bll_payload, brick_remap)
    print(f'BLL: {len(bll_bytes)} bytes (remapped, BE)')

    # 6. Layout sections
    cur = HEADER_SIZE
    pal_off = cur; cur += len(pal_bytes); cur = (cur + 3) & ~3
    grid_off = cur; cur += len(grid_bytes); cur = (cur + 3) & ~3
    bll_off_local = cur; cur += len(bll_bytes); cur = (cur + 3) & ~3
    bdir_off = cur; cur += len(bdir_bytes); cur = (cur + 3) & ~3
    bpix_off = cur; cur += len(bpix_bytes); cur = (cur + 3) & ~3
    file_size = cur

    # 7. Header
    hdr = bytearray()
    hdr += MAGIC
    hdr += struct.pack('>I', VERSION)
    hdr += struct.pack('>I', scene_id)
    hdr += struct.pack('>I', pal_off)
    hdr += struct.pack('>I', len(pal_bytes))
    hdr += struct.pack('>I', grid_off)
    hdr += struct.pack('>I', len(grid_bytes))
    hdr += struct.pack('>I', bll_off_local)
    hdr += struct.pack('>I', len(bll_bytes))
    hdr += struct.pack('>I', len(blocks))
    hdr += struct.pack('>I', bdir_off)
    hdr += struct.pack('>I', len(used_bricks_hqr))
    hdr += struct.pack('>I', bpix_off)
    hdr += struct.pack('>I', len(bpix_bytes))
    hdr += struct.pack('>I', file_size)
    assert len(hdr) == HEADER_SIZE, f'header size {len(hdr)} != {HEADER_SIZE}'

    # 8. Concat
    out = bytearray(file_size)
    out[0:HEADER_SIZE] = hdr
    out[pal_off:pal_off+len(pal_bytes)] = pal_bytes
    out[grid_off:grid_off+len(grid_bytes)] = grid_bytes
    out[bll_off_local:bll_off_local+len(bll_bytes)] = bll_bytes
    out[bdir_off:bdir_off+len(bdir_bytes)] = bdir_bytes
    out[bpix_off:bpix_off+len(bpix_bytes)] = bpix_bytes

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'wb') as f:
        f.write(out)

    print(f'\n=== CART LAYOUT (scene {scene_id}) ===')
    print(f'  total file size:     {file_size:>10,} bytes')
    print(f'  header:              0x{0:08X} - 0x{HEADER_SIZE-1:08X}')
    print(f'  palette:             0x{pal_off:08X} ({len(pal_bytes):>9,} B)')
    print(f'  grid:                0x{grid_off:08X} ({len(grid_bytes):>9,} B)')
    print(f'  BLL ({len(blocks):>3} blocks):   0x{bll_off_local:08X} ({len(bll_bytes):>9,} B)')
    print(f'  brick dir ({len(used_bricks_hqr):>3}):    0x{bdir_off:08X} ({len(bdir_bytes):>9,} B)')
    print(f'  brick pixels:        0x{bpix_off:08X} ({len(bpix_bytes):>9,} B)')
    print(f'\nWrote {out_path}')


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--scene', type=int, default=0)
    ap.add_argument('--brk', default=lba_paths.common('LBA_BRK.HQR'))
    ap.add_argument('--bll', default=lba_paths.common('LBA_BLL.HQR'))
    ap.add_argument('--gri', default=lba_paths.common('LBA_GRI.HQR'))
    ap.add_argument('--ress', default=lba_paths.common('RESS.HQR'))
    ap.add_argument('--out', default=None)
    args = ap.parse_args()
    out = args.out or lba_paths.repo(rf'cart\scene_{args.scene:03d}.bin')
    main(args.scene, args.brk, args.bll, args.gri, args.ress, out)
