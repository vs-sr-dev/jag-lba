#!/usr/bin/env python3
"""Pack all 134 LBA1 scenes + selected character bodies into a single Jaguar cart
binary (V5 multi-scene + 3D bodies).

Strategy: bricks stay as RLE (HQR-decompressed format, 4 byte header + per-row RLE),
68k or GPU decodes to 8bpp working buffer per scene at LoadScene() time.
Bodies are pre-packed via tools/body_pack.py into cart-friendly fixed-record
format (header + vertex array + 16 B/poly), included alongside scenes.
Total ~5.83 MB + body assets, fits in 6 MB cart unbanked.

Layout (BE everywhere):

  HEADER (72 B):
    0x00  8B    magic   = b"JAGLBA03"
    0x08  u32   version = 3
    0x0C  u16   scene_count    = 134
    0x0E  u16   brick_count    = N (only bricks actually referenced by any scene)
    0x10  u32   palette_off
    0x14  u32   scene_table_off
    0x18  u32   brick_dir_off       -- per-brick header table (8 B/entry)
    0x1C  u32   brick_blob_off      -- RLE brick payloads (concatenated)
    0x20  u32   brick_blob_size
    0x24  u32   bll_table_off       -- u32[bll_unique_count] offsets to BLL payloads
    0x28  u16   bll_unique_count    -- HQR-deduped (= 14)
    0x2A  u16   body_count          -- number of bodies packed (V5+)
    0x2C  u32   bll_blob_off        -- BLL payloads concat
    0x30  u32   bll_blob_size
    0x34  u32   grid_table_off      -- u32[scene_count] offsets to grid payloads
    0x38  u32   grid_blob_off       -- grid payloads concat
    0x3C  u32   file_size
    0x40  u32   body_table_off      -- u32[body_count] offsets into body_blob
    0x44  u32   body_blob_off       -- body_pack payloads concatenated

  PALETTE (512 B): 256 RGB16 BE entries (global RESS_PAL)

  SCENE TABLE (16 B per entry × scene_count):
    u8   bank_idx          -- always 0 in V4 (V5 uses for extension banks)
    u8   bll_unique_idx    -- index into bll_table (0..bll_unique_count-1)
    u8   palette_idx       -- always 0 in V4 (single global palette)
    u8   _pad
    u8   cam_gx_init       -- starting camera grid X
    u8   cam_gz_init       -- starting camera grid Z
    u8   _pad2[2]
    u8   tx_right          -- transition: scene_idx when cam exits east, 0xFF=none
    u8   tx_left
    u8   tx_down
    u8   tx_up
    u32  _reserved

  BRICK DIRECTORY (8 B per entry × brick_count):
    u32  brick_blob_offset    -- offset into brick_blob to RLE payload start
    u8   dx, u8 dy, u8 hot_x, u8 hot_y    -- decoded for runtime convenience

  BRICK BLOB: concatenated RLE payloads (HQR-decompressed format), each starts
    with the 4 B header (dx, dy, hot_x, hot_y) followed by per-row RLE data.
    Each entry padded to next 8-byte (phrase) boundary.

  BLL TABLE: u32 offsets into bll_blob for each unique BLL.

  BLL BLOB: concatenated BLL payloads (BE-converted, brick refs REMAPPED to local
    brick_idx 1-based; 0 = empty cell).

  GRID TABLE: u32 offsets into grid_blob for each of the 134 scenes.

  GRID BLOB: concatenated grid payloads (BE-converted offset table + RLE columns,
    same column-RLE format as original LBA but UWORDs in BE order).

Usage:
  python tools/cart_pack_all.py
  python tools/cart_pack_all.py --out cart/scene_all.bin
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

GOG_COMMON = lba_paths.COMMON

MAGIC = b'JAGLBA03'
VERSION = 3
HEADER_SIZE = 72
SCENE_TABLE_ENTRY_SIZE = 16
BRICK_DIR_ENTRY_SIZE = 8


def rgb_to_jag16(r, g, b):
    r5 = (r >> 3) & 0x1F
    b5 = (b >> 3) & 0x1F
    g6 = (g >> 2) & 0x3F
    return (r5 << 11) | (b5 << 6) | g6


def build_palette_section(pal_raw):
    pal = normalize_palette(pal_raw)
    assert len(pal) == 256
    buf = bytearray()
    n_remap = 0
    n_white_remap = 0
    for i, (r, g, b) in enumerate(pal):
        v = rgb_to_jag16(r, g, b)
        if i != 0 and v == 0:
            v = 1
            n_remap += 1
        # S22 visual cleanup: idx 2..15, 26..30, 255 sono "reserved/UI" slots
        # filled with FFFF white in source pal -> Jaguar TOM rende come bianco
        # abbagliante. Forziamo a 0000 (nero) per evitare flash bianchi se brick
        # pixels rientrano in questi idx. idx 0 e 1 lasciati invariati (sentinella).
        if i >= 2 and v == 0xFFFF:
            v = 0x0000
            n_white_remap += 1
        buf += struct.pack('>H', v)
    if n_remap:
        print(f'  Palette: forced {n_remap} dark non-zero entries to 0x0001 (sentinel-collision avoidance)')
    if n_white_remap:
        print(f'  Palette S22: remapped {n_white_remap} reserved-white entries (FFFF) to 0000 (black)')
    return bytes(buf)


def parse_bll_blocks(bll_payload):
    """Returns list of (dx, dy, dz, [(info_uword_LE, brick_ref_LE) per cell])."""
    first_off = struct.unpack('<I', bll_payload[0:4])[0]
    n_blocks = first_off // 4
    blocks = []
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
    return blocks


def collect_used_bricks_one_scene(scene_idx, gri_data, bll_data, gri_off, bll_off):
    grid_payload = extract_entry(gri_data, scene_idx, gri_off)
    cube, _ = decode_grid(grid_payload)
    bll_payload = extract_entry(bll_data, scene_idx, bll_off)
    blocks = parse_bll_blocks(bll_payload)
    used = set()
    for gz in range(CUBE_Z):
        for gx in range(CUBE_X):
            for gy in range(CUBE_Y):
                v = cube[gz][gx][gy]
                if v == 0: continue
                nb = v & 0xFF; pb = v >> 8
                if nb == 0 or nb-1 >= len(blocks): continue
                _, _, _, cells = blocks[nb-1]
                if pb >= len(cells): continue
                _, br = cells[pb]
                if br: used.add(br - 1)
    return used


def build_grid_section_be(payload):
    """BE-convert grid payload (offset table + RLE columns).
    Same logic as cart_pack_scene.py.build_grid_section."""
    out = bytearray()
    if len(payload) < 8192:
        raise ValueError(f'grid too short: {len(payload)}')
    # BE offset table
    for i in range(4096):
        v = struct.unpack('<H', payload[i*2:i*2+2])[0]
        out += struct.pack('>H', v)
    # Walk RLE, byte-swap each cell UWORD once (dedupe via offset set)
    rle = bytearray(payload[8192:])
    swapped = set()
    for i in range(4096):
        col_off = struct.unpack('<H', payload[i*2:i*2+2])[0]
        if col_off in swapped:
            continue
        swapped.add(col_off)
        local = col_off - 8192
        if local < 0:
            continue
        nb_entity = rle[local]
        cp = local + 1
        for _ in range(nb_entity):
            op = rle[cp]; cp += 1
            count = (op & 0x3F) + 1
            mode = op & 0xC0
            if mode == 0x00:
                pass  # zero fill, no payload
            elif mode == 0x80:
                lo = rle[cp]; hi = rle[cp+1]
                rle[cp], rle[cp+1] = hi, lo
                cp += 2
            else:  # 0x40 or 0xC0: diff
                for _u in range(count):
                    lo = rle[cp]; hi = rle[cp+1]
                    rle[cp], rle[cp+1] = hi, lo
                    cp += 2
    out += bytes(rle)
    return bytes(out)


def build_bll_section_be(blocks, brick_remap_global):
    """Re-emit BLL as BE with brick_refs remapped to GLOBAL brick_idx (1-based, 0=empty).
    Layout: u32 offset table BE + per-block (dx,dy,dz bytes + n_cells × (info_BE, brick_ref_BE))."""
    n_blocks = len(blocks)
    block_bytes = []
    for dx, dy, dz, cells in blocks:
        b = bytearray()
        b += bytes([dx, dy, dz])
        for info, br in cells:
            local = 0 if br == 0 else brick_remap_global.get(br - 1, 0)
            b += struct.pack('>H', info)
            b += struct.pack('>H', local)
        block_bytes.append(bytes(b))
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--brk',  default=os.path.join(GOG_COMMON, 'LBA_BRK.HQR'))
    ap.add_argument('--bll',  default=os.path.join(GOG_COMMON, 'LBA_BLL.HQR'))
    ap.add_argument('--gri',  default=os.path.join(GOG_COMMON, 'LBA_GRI.HQR'))
    ap.add_argument('--ress', default=os.path.join(GOG_COMMON, 'RESS.HQR'))
    ap.add_argument('--out',  default=lba_paths.repo(r'cart\scene_all.bin'))
    ap.add_argument('--max-scenes', type=int, default=None,
                    help='Pack only first N scenes (for size-limited test builds)')
    ap.add_argument('--body-pack-dir',
                    default=lba_paths.repo(r'extracted\BODY_PACK'),
                    help='Directory containing body_NNN.bin pre-packed by body_pack.py')
    ap.add_argument('--body-include', type=int, nargs='*', default=[0],
                    help='List of body indices to include (default: just 0 = Twinsen)')
    args = ap.parse_args()

    print(f'BRK : {args.brk}')
    print(f'BLL : {args.bll}')
    print(f'GRI : {args.gri}')
    print(f'RESS: {args.ress}\n')

    with open(args.brk, 'rb')  as f: brk_data  = f.read()
    with open(args.bll, 'rb')  as f: bll_data  = f.read()
    with open(args.gri, 'rb')  as f: gri_data  = f.read()
    with open(args.ress, 'rb') as f: ress_data = f.read()
    brk_off  = parse_offsets(brk_data)
    bll_off  = parse_offsets(bll_data)
    gri_off  = parse_offsets(gri_data)
    ress_off = parse_offsets(ress_data)

    n_scenes = len(gri_off)
    if args.max_scenes is not None and args.max_scenes < n_scenes:
        n_scenes = args.max_scenes
        print(f'Scenes: {n_scenes} (capped from {len(gri_off)} via --max-scenes)')
    else:
        print(f'Scenes: {n_scenes}')

    # 1. Palette (single global, RESS slot 0)
    pal_raw = extract_entry(ress_data, 0, ress_off)
    palette_bytes = build_palette_section(pal_raw)
    print(f'\nPalette: 1 global ({len(palette_bytes)} B)')

    # 2. Walk all scenes, collect global brick set + parse BLLs
    print(f'\nCollecting per-scene brick refs...')
    per_scene_used = []
    global_used = set()
    for s in range(n_scenes):
        used = collect_used_bricks_one_scene(s, gri_data, bll_data, gri_off, bll_off)
        per_scene_used.append(used)
        global_used |= used
    sorted_global = sorted(global_used)
    brick_remap_global = {hqr_idx: i + 1 for i, hqr_idx in enumerate(sorted_global)}  # 1-based
    print(f'  Global unique bricks: {len(sorted_global)} / {len(brk_off)}')

    # 3. BLL HQR dedup: collect unique-storage BLL payloads + map scene -> bll_unique_idx
    print(f'\nDeduping BLLs at HQR level...')
    bll_off_to_unique_idx = {}
    bll_unique_payloads = []  # list of raw payloads (LE, original)
    bll_unique_blocks   = []  # parsed blocks per unique
    scene_to_bll_unique = [0] * n_scenes
    for s in range(n_scenes):
        off = bll_off[s]
        if off not in bll_off_to_unique_idx:
            bll_off_to_unique_idx[off] = len(bll_unique_payloads)
            payload = extract_entry(bll_data, s, bll_off)
            bll_unique_payloads.append(payload)
            bll_unique_blocks.append(parse_bll_blocks(payload))
        scene_to_bll_unique[s] = bll_off_to_unique_idx[off]
    print(f'  BLL unique storage: {len(bll_unique_payloads)} (from {n_scenes} scenes)')

    # 4. Build BLL blob (BE, remapped to GLOBAL brick idx)
    bll_blob = bytearray()
    bll_table = bytearray()  # u32 offsets relative to bll_blob start
    for blocks in bll_unique_blocks:
        bll_table += struct.pack('>I', len(bll_blob))
        section = build_bll_section_be(blocks, brick_remap_global)
        bll_blob += section
        # phrase-pad
        pad = (-len(bll_blob)) % 8
        bll_blob += b'\x00' * pad
    print(f'  BLL table : {len(bll_table)} B')
    print(f'  BLL blob  : {len(bll_blob):,} B')

    # 5. Build grid blob (BE per scene, no dedup — almost all scenes unique anyway)
    print(f'\nBuilding grid blob...')
    grid_blob = bytearray()
    grid_table = bytearray()  # u32 offsets relative to grid_blob start
    for s in range(n_scenes):
        grid_table += struct.pack('>I', len(grid_blob))
        payload = extract_entry(gri_data, s, gri_off)
        section = build_grid_section_be(payload)
        grid_blob += section
        pad = (-len(grid_blob)) % 8
        grid_blob += b'\x00' * pad
    print(f'  Grid table: {len(grid_table)} B')
    print(f'  Grid blob : {len(grid_blob):,} B')

    # 6. Build brick directory + RLE blob (RLE = HQR-decompressed payload, NOT decoded)
    print(f'\nBuilding brick blob (RLE)...')
    brick_dir = bytearray()
    brick_blob = bytearray()
    for hqr_idx in sorted_global:
        payload = extract_entry(brk_data, hqr_idx, brk_off)
        if payload is None or len(payload) < 4:
            # Substitute 1x1 empty
            brick_dir += struct.pack('>I', len(brick_blob))
            brick_dir += bytes([1, 1, 0, 0])
            stub = bytes([1, 1, 0, 0, 1, 0])  # dx=1,dy=1,hot=0,0; row: NbBlock=1, op=skip-1
            brick_blob += stub
        else:
            dx, dy, hx, hy = payload[0], payload[1], payload[2], payload[3]
            brick_dir += struct.pack('>I', len(brick_blob))
            brick_dir += bytes([dx, dy, hx, hy])
            brick_blob += payload
        pad = (-len(brick_blob)) % 8
        brick_blob += b'\x00' * pad
    print(f'  Brick dir : {len(brick_dir):,} B  ({len(sorted_global)} entries)')
    print(f'  Brick blob: {len(brick_blob):,} B  ({len(brick_blob)/(1024*1024):.2f} MB)')

    # 7. Build scene table (transition map: cyclic placeholder N -> N+1, last -> 0)
    scene_table = bytearray()
    for s in range(n_scenes):
        bank_idx        = 0
        bll_uniq_idx    = scene_to_bll_unique[s]
        palette_idx     = 0
        cam_gx_init     = 8   # placeholder, may be tuned per scene later
        cam_gz_init     = 50
        # Cyclic transition placeholder for testing
        tx_right = (s + 1) % n_scenes
        tx_left  = (s - 1) % n_scenes
        tx_down  = 0xFF  # none
        tx_up    = 0xFF  # none
        scene_table += struct.pack('>BBBB', bank_idx, bll_uniq_idx, palette_idx, 0)
        scene_table += struct.pack('>BBBB', cam_gx_init, cam_gz_init, 0, 0)
        scene_table += struct.pack('>BBBB', tx_right, tx_left, tx_down, tx_up)
        scene_table += struct.pack('>I', 0)
    assert len(scene_table) == n_scenes * SCENE_TABLE_ENTRY_SIZE

    # 7b. Build body section (V5+ — pre-packed by body_pack.py)
    print(f'\nBuilding body blob ({len(args.body_include)} bodies)...')
    body_blob = bytearray()
    body_table = bytearray()  # u32 offsets into body_blob (one per body)
    bodies_loaded = 0
    for body_idx in args.body_include:
        path = os.path.join(args.body_pack_dir, f'body_{body_idx:03d}.bin')
        if not os.path.isfile(path):
            print(f'  WARN: {path} not found — skipping body {body_idx}')
            continue
        with open(path, 'rb') as f:
            payload = f.read()
        body_table += struct.pack('>I', len(body_blob))
        body_blob += payload
        # phrase-pad
        pad = (-len(body_blob)) % 8
        body_blob += b'\x00' * pad
        bodies_loaded += 1
        print(f'  body {body_idx:3d}: {len(payload)} B')
    print(f'  Body table: {len(body_table)} B ({bodies_loaded} entries)')
    print(f'  Body blob:  {len(body_blob):,} B')

    # 8. Layout sections
    cur = HEADER_SIZE
    def place(size, align=4):
        nonlocal cur
        cur = (cur + align - 1) & ~(align - 1)
        off = cur
        cur += size
        return off

    palette_off     = place(len(palette_bytes), 4)
    scene_table_off = place(len(scene_table), 4)
    brick_dir_off   = place(len(brick_dir), 4)
    brick_blob_off  = place(len(brick_blob), 8)  # phrase
    bll_table_off   = place(len(bll_table), 4)
    bll_blob_off    = place(len(bll_blob), 8)
    grid_table_off  = place(len(grid_table), 4)
    grid_blob_off   = place(len(grid_blob), 8)
    body_table_off  = place(len(body_table), 4) if bodies_loaded else 0
    body_blob_off   = place(len(body_blob), 8) if bodies_loaded else 0
    file_size       = (cur + 7) & ~7

    # 9. Header
    hdr = bytearray()
    hdr += MAGIC
    hdr += struct.pack('>I', VERSION)
    hdr += struct.pack('>H', n_scenes)
    hdr += struct.pack('>H', len(sorted_global))
    hdr += struct.pack('>I', palette_off)
    hdr += struct.pack('>I', scene_table_off)
    hdr += struct.pack('>I', brick_dir_off)
    hdr += struct.pack('>I', brick_blob_off)
    hdr += struct.pack('>I', len(brick_blob))
    hdr += struct.pack('>I', bll_table_off)
    hdr += struct.pack('>H', len(bll_unique_payloads))
    hdr += struct.pack('>H', bodies_loaded)
    hdr += struct.pack('>I', bll_blob_off)
    hdr += struct.pack('>I', len(bll_blob))
    hdr += struct.pack('>I', grid_table_off)
    hdr += struct.pack('>I', grid_blob_off)
    hdr += struct.pack('>I', file_size)
    hdr += struct.pack('>I', body_table_off)
    hdr += struct.pack('>I', body_blob_off)
    assert len(hdr) == HEADER_SIZE, f'header {len(hdr)} != {HEADER_SIZE}'

    # 10. Concatenate
    out = bytearray(file_size)
    out[0:HEADER_SIZE] = hdr
    out[palette_off:palette_off+len(palette_bytes)]       = palette_bytes
    out[scene_table_off:scene_table_off+len(scene_table)] = scene_table
    out[brick_dir_off:brick_dir_off+len(brick_dir)]       = brick_dir
    out[brick_blob_off:brick_blob_off+len(brick_blob)]    = brick_blob
    out[bll_table_off:bll_table_off+len(bll_table)]       = bll_table
    out[bll_blob_off:bll_blob_off+len(bll_blob)]          = bll_blob
    out[grid_table_off:grid_table_off+len(grid_table)]    = grid_table
    out[grid_blob_off:grid_blob_off+len(grid_blob)]       = grid_blob
    if bodies_loaded:
        out[body_table_off:body_table_off+len(body_table)] = body_table
        out[body_blob_off:body_blob_off+len(body_blob)]    = body_blob

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, 'wb') as f:
        f.write(out)

    print(f'\n=== CART LAYOUT (V5) ===')
    print(f'  total file size:  {file_size:>10,} B  ({file_size/(1024*1024):.2f} MB)')
    print(f'  6 MB cap usage:   {file_size/(6*1024*1024)*100:.1f}%')
    print(f'  headroom:         {6*1024*1024-file_size:,} B  ({(6*1024*1024-file_size)/1024:.0f} KB)')
    print(f'  ---')
    print(f'  header:           0x{0:08X}  ({HEADER_SIZE} B)')
    print(f'  palette:          0x{palette_off:08X}  ({len(palette_bytes)} B)')
    print(f'  scene table:      0x{scene_table_off:08X}  ({len(scene_table)} B, {n_scenes} entries)')
    print(f'  brick dir:        0x{brick_dir_off:08X}  ({len(brick_dir)} B, {len(sorted_global)} entries)')
    print(f'  brick blob:       0x{brick_blob_off:08X}  ({len(brick_blob):,} B)')
    print(f'  bll table:        0x{bll_table_off:08X}  ({len(bll_table)} B, {len(bll_unique_payloads)} unique)')
    print(f'  bll blob:         0x{bll_blob_off:08X}  ({len(bll_blob):,} B)')
    print(f'  grid table:       0x{grid_table_off:08X}  ({len(grid_table)} B)')
    print(f'  grid blob:        0x{grid_blob_off:08X}  ({len(grid_blob):,} B)')
    if bodies_loaded:
        print(f'  body table:       0x{body_table_off:08X}  ({len(body_table)} B, {bodies_loaded} entries)')
        print(f'  body blob:        0x{body_blob_off:08X}  ({len(body_blob):,} B)')
    print(f'\nWrote {args.out}')


if __name__ == '__main__':
    main()
