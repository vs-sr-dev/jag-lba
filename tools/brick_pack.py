#!/usr/bin/env python3
"""Encode/decode LBA1 bricks in JagLBA cart format. Round-trip test on one brick.

Cart format (per-brick blob):
  BYTE  DX, DY, HotX, HotY, cluster_id    (5 bytes header)
  For each of DY rows:
    BYTE  NbBlock
    For each block, 1 opcode byte:
      00xxxxxx           : skip (xxxxxx+1) transparent pixels
      01xxxxxx + N bytes : copy (xxxxxx+1) 4-bit pixels (packed 2/byte, high nibble first)
                           N = ceil((count+1)/2)
      10xxxxxx + 1 byte  : repeat (xxxxxx+1) times; 4-bit pixel value in low nibble

This mode runs the K=12 clustering, computes sub-palettes, encodes ONE brick,
decodes it back, and verifies pixel-perfect round-trip. Renders 3-panel PNG:
original (256-color) | quantized (16-color sub-palette) | round-tripped.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import lba_paths
import sys, os, struct, argparse
import numpy as np
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hqr_decompress import parse_offsets, extract_entry
from render_brick import decode_brick, write_png_rgba
from render_slide import normalize_palette
from brick_cluster import build_features, kmeans
from brick_quant_eval import reduce_palette

MAX_RUN = 64

def quantize_pixels(pixels, alpha, dx, dy, sub_arr, pal_arr):
    """Returns 2D list of 4-bit indices into sub-palette (0 where transparent)."""
    quant = [[0]*dx for _ in range(dy)]
    for y in range(dy):
        for x in range(dx):
            if alpha[y][x]:
                d = np.sum((sub_arr - pal_arr[pixels[y][x]])**2, axis=1)
                quant[y][x] = int(np.argmin(d))
    return quant

def encode_row(quant_row, alpha_row, dx):
    blocks = []
    x = 0
    while x < dx:
        if not alpha_row[x]:
            n = 1
            while x+n < dx and not alpha_row[x+n] and n < MAX_RUN:
                n += 1
            blocks.append(('skip', n))
            x += n
        else:
            # Try repeat run
            v = quant_row[x]
            n = 1
            while x+n < dx and alpha_row[x+n] and quant_row[x+n] == v and n < MAX_RUN:
                n += 1
            if n >= 3:
                blocks.append(('repeat', v, n))
                x += n
            else:
                # Copy run, breaking when 3+ same pixels coming up
                copy_pix = []
                while x < dx and alpha_row[x] and len(copy_pix) < MAX_RUN:
                    if (len(copy_pix) > 0 and x+2 < dx
                        and alpha_row[x+1] and alpha_row[x+2]
                        and quant_row[x+1] == quant_row[x] and quant_row[x+2] == quant_row[x]):
                        break
                    copy_pix.append(quant_row[x])
                    x += 1
                blocks.append(('copy', copy_pix))
    return blocks

def emit_blocks(blocks):
    out = bytearray()
    for block in blocks:
        if block[0] == 'skip':
            n = block[1]
            out.append(0x00 | ((n-1) & 0x3F))
        elif block[0] == 'copy':
            pix = block[1]
            n = len(pix)
            out.append(0x40 | ((n-1) & 0x3F))
            for i in range(0, n, 2):
                hi = pix[i] & 0xF
                lo = (pix[i+1] & 0xF) if i+1 < n else 0
                out.append((hi << 4) | lo)
        elif block[0] == 'repeat':
            v, n = block[1], block[2]
            out.append(0x80 | ((n-1) & 0x3F))
            out.append(v & 0xF)
    return bytes(out)

def encode_brick_blob(quant, alpha, dx, dy, hx, hy, cid):
    out = bytearray([dx, dy, hx, hy, cid])
    for y in range(dy):
        blocks = encode_row(quant[y], alpha[y], dx)
        out.append(len(blocks))
        out.extend(emit_blocks(blocks))
    return bytes(out)

def decode_brick_blob(blob):
    if len(blob) < 5:
        raise ValueError("blob too small")
    dx, dy, hx, hy, cid = blob[0], blob[1], blob[2], blob[3], blob[4]
    pixels = [[0]*dx for _ in range(dy)]
    alpha  = [[0]*dx for _ in range(dy)]
    pos = 5
    for row in range(dy):
        nb_block = blob[pos]; pos += 1
        col = 0
        for _ in range(nb_block):
            op = blob[pos]; pos += 1
            count = (op & 0x3F) + 1
            mode = op & 0xC0
            if mode == 0x00:
                col += count
            elif mode == 0x40:
                b = 0
                for i in range(count):
                    if i % 2 == 0:
                        b = blob[pos]; pos += 1
                        v = (b >> 4) & 0xF
                    else:
                        v = b & 0xF
                    if col+i < dx:
                        pixels[row][col+i] = v
                        alpha[row][col+i] = 255
                col += count
            elif mode == 0x80:
                v = blob[pos] & 0xF; pos += 1
                for i in range(count):
                    if col+i < dx:
                        pixels[row][col+i] = v
                        alpha[row][col+i] = 255
                col += count
            else:
                raise ValueError(f"bad opcode {op:02X}")
    return dx, dy, hx, hy, cid, pixels, alpha, pos

def main(brk_path, ress_path, brick_idx, out_dir, scale):
    print(f"Loading {brk_path}...")
    with open(brk_path, 'rb') as f: brk_data = f.read()
    brk_offsets = parse_offsets(brk_data)
    meta, X = build_features(brk_data, brk_offsets)
    print(f"  {len(meta)} non-empty bricks")

    print(f"K-means K=12 (seed=42)...")
    centers, assigns = kmeans(X, 12, seed=42, verbose=False)

    with open(ress_path, 'rb') as f: ress_data = f.read()
    ress_offsets = parse_offsets(ress_data)
    pal_raw = extract_entry(ress_data, 0, ress_offsets)
    pal_rgb = normalize_palette(pal_raw)
    pal_arr = np.array(pal_rgb, dtype=np.float32)

    # Find meta position for requested brick index
    meta_pos = next((i for i, m in enumerate(meta) if m[0] == brick_idx), None)
    if meta_pos is None:
        raise SystemExit(f"brick {brick_idx} not in non-empty list")
    cluster_id = int(assigns[meta_pos])

    # Compute sub-palette ONLY for this cluster (cheap)
    members = np.where(assigns == cluster_id)[0]
    used = Counter()
    for mi in members:
        orig_idx = meta[mi][0]
        payload = extract_entry(brk_data, orig_idx, brk_offsets)
        bdx, bdy, _, _, bpix, balpha, _ = decode_brick(payload)
        for y in range(bdy):
            for x in range(bdx):
                if balpha[y][x]:
                    used[bpix[y][x]] += 1
    sub_pal = reduce_palette(used, pal_rgb, k=16, seed=42)
    sub_arr = np.array(sub_pal, dtype=np.float32)

    # Decode original
    payload = extract_entry(brk_data, brick_idx, brk_offsets)
    dx, dy, hx, hy, pixels, alpha, _ = decode_brick(payload)
    print(f"\nBrick {brick_idx}: DX={dx} DY={dy} HotX={hx} HotY={hy} cluster={cluster_id}")
    print(f"  Original payload: {len(payload)} bytes")

    # Quantize
    quant = quantize_pixels(pixels, alpha, dx, dy, sub_arr, pal_arr)

    # Encode
    blob = encode_brick_blob(quant, alpha, dx, dy, hx, hy, cluster_id)
    print(f"  Encoded blob:     {len(blob)} bytes ({100*len(blob)/len(payload):.1f}% of original)")

    # Decode
    ddx, ddy, dhx, dhy, dcid, dpix, dalpha, consumed = decode_brick_blob(blob)
    print(f"  Decoded: DX={ddx} DY={ddy} HotX={dhx} HotY={dhy} cluster={dcid}")
    print(f"  Consumed: {consumed}/{len(blob)} bytes (should be equal: {consumed == len(blob)})")

    # Verify pixel-perfect round-trip (quantized vs decoded)
    diffs = 0
    alpha_diffs = 0
    for y in range(dy):
        for x in range(dx):
            if alpha[y][x] != dalpha[y][x]:
                alpha_diffs += 1
            elif alpha[y][x] and quant[y][x] != dpix[y][x]:
                diffs += 1
    print(f"  Round-trip alpha diffs: {alpha_diffs} (should be 0)")
    print(f"  Round-trip pixel diffs: {diffs} (should be 0)")

    # Render 3-panel: original | quantized | round-tripped
    os.makedirs(out_dir, exist_ok=True)
    GAP = 8
    panel_w = dx * scale
    cw = 3 * panel_w + 4 * GAP
    ch = dy * scale + 2 * GAP
    rgba = bytearray(cw * ch * 4)

    def paint(ox, get_rgb):
        for y in range(dy):
            for x in range(dx):
                a = alpha[y][x]
                r, g, b = get_rgb(y, x) if a else (0, 0, 0)
                for sy in range(scale):
                    py = GAP + y*scale + sy
                    for sx in range(scale):
                        px = ox + x*scale + sx
                        p = (py * cw + px) * 4
                        rgba[p]   = r
                        rgba[p+1] = g
                        rgba[p+2] = b
                        rgba[p+3] = a

    paint(GAP,                       lambda y, x: pal_rgb[pixels[y][x]])
    paint(GAP + panel_w + GAP,       lambda y, x: sub_pal[quant[y][x]])
    paint(GAP + 2*(panel_w + GAP),   lambda y, x: sub_pal[dpix[y][x]])

    out_png = os.path.join(out_dir, f'roundtrip_b{brick_idx:05d}_c{cluster_id:02d}.png')
    write_png_rgba(out_png, cw, ch, bytes(rgba))
    print(f"\nWrote {out_png} (orig | quantized | round-tripped)")

if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--brk', default=lba_paths.common('LBA_BRK.HQR'))
    ap.add_argument('--ress', default=lba_paths.common('RESS.HQR'))
    ap.add_argument('--out', default=lba_paths.repo(r'extracted\BRK_roundtrip'))
    ap.add_argument('--brick', type=int, default=0)
    ap.add_argument('--scale', type=int, default=8)
    args = ap.parse_args()
    main(args.brk, args.ress, args.brick, args.out, args.scale)
