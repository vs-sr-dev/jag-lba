#!/usr/bin/env python3
"""Bulk-encode all 8715 bricks to JagLBA cart format. Report total size.

Pipeline:
  1. Load all bricks from LBA_BRK.HQR
  2. K-means K=12 clustering (seed=42)
  3. Compute 12 sub-palettes (16 colors each, k-means in RGB space weighted by usage)
  4. Encode each brick: quantize to its cluster sub-palette + RLE on 4-bit pixels
  5. Sum sizes, report cart-format total + comparison to original 3.79 MB HQR

Also tests: post-RLE LZSS (zlib L9) on top, to see if there's residual compressible
structure left.
"""
import sys, os, zlib, argparse
import numpy as np
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hqr_decompress import parse_offsets, extract_entry
from render_brick import decode_brick
from render_slide import normalize_palette
from brick_cluster import build_features, kmeans
from brick_quant_eval import reduce_palette
from brick_pack import quantize_pixels, encode_brick_blob, decode_brick_blob

ORIGINAL_HQR_BYTES = 3_977_143  # LBA_BRK.HQR compressed size from earlier inspection

def main(brk_path, ress_path):
    print(f"Loading {brk_path}...")
    with open(brk_path, 'rb') as f: brk_data = f.read()
    brk_offsets = parse_offsets(brk_data)
    n_total = len(brk_offsets)
    meta, X = build_features(brk_data, brk_offsets)
    print(f"  {len(meta)} non-empty bricks (of {n_total} entries)")

    print(f"K-means K=12 (seed=42)...")
    centers, assigns = kmeans(X, 12, seed=42, verbose=False)

    with open(ress_path, 'rb') as f: ress_data = f.read()
    ress_offsets = parse_offsets(ress_data)
    pal_raw = extract_entry(ress_data, 0, ress_offsets)
    pal_rgb = normalize_palette(pal_raw)
    pal_arr = np.array(pal_rgb, dtype=np.float32)

    print(f"Computing 12 sub-palettes (16 colors each)...")
    sub_palettes = []
    sub_arrs = []
    for c in range(12):
        members = np.where(assigns == c)[0]
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
        sub_palettes.append(sub_pal)
        sub_arrs.append(np.array(sub_pal, dtype=np.float32))

    print(f"Encoding {len(meta)} bricks...")
    blobs = []  # (original_brick_idx, blob_bytes)
    total_orig_payload = 0
    rt_diffs = 0
    for i, m in enumerate(meta):
        orig_idx = m[0]
        cid = int(assigns[i])
        payload = extract_entry(brk_data, orig_idx, brk_offsets)
        dx, dy, hx, hy, pixels, alpha, _ = decode_brick(payload)
        quant = quantize_pixels(pixels, alpha, dx, dy, sub_arrs[cid], pal_arr)
        blob = encode_brick_blob(quant, alpha, dx, dy, hx, hy, cid)
        blobs.append((orig_idx, blob))
        total_orig_payload += len(payload)
        # Spot-check round-trip on every 500th brick
        if i % 500 == 0:
            ddx, ddy, _, _, _, dpix, dalpha, _ = decode_brick_blob(blob)
            for y in range(dy):
                for x in range(dx):
                    if alpha[y][x] != dalpha[y][x] or (alpha[y][x] and quant[y][x] != dpix[y][x]):
                        rt_diffs += 1
        if (i+1) % 2000 == 0:
            cur = sum(len(b[1]) for b in blobs)
            print(f"  {i+1}/{len(meta)} encoded, running total {cur:,} bytes")

    total_blob = sum(len(b[1]) for b in blobs)

    # Cart layout estimate
    n_clusters = 12
    pal_size = 16
    palette_bytes = n_clusters * pal_size * 3
    n_brick_slots = max(orig for orig, _ in blobs) + 1
    offset_table_bytes = n_brick_slots * 4  # ULONG per slot, BLANKs stored as 0
    file_header_bytes = 2 + 2 + palette_bytes + 2 + offset_table_bytes
    cart_total = file_header_bytes + total_blob

    print(f"\n=== Bulk pack results ===")
    print(f"Bricks encoded:                 {len(blobs):>12,}")
    print(f"Round-trip diffs (sampled 1/500): {rt_diffs:>10}")
    print(f"Total original payload:         {total_orig_payload:>12,} bytes  (uncompressed brick RLE)")
    print(f"Total encoded blobs (4bpp+RLE): {total_blob:>12,} bytes")
    print(f"Per-blob avg savings:           {100*(1-total_blob/total_orig_payload):>6.1f}%")
    print()
    print(f"Cart file layout:")
    print(f"  Header (2+2+pals+2):     {2+2+palette_bytes+2:>8,} bytes")
    print(f"  Offset table:            {offset_table_bytes:>8,} bytes ({n_brick_slots} slots × 4)")
    print(f"  Brick blobs:             {total_blob:>8,} bytes")
    print(f"  CART TOTAL (uncompr):    {cart_total:>8,} bytes ({cart_total/1024/1024:.2f} MB)")
    print()
    print(f"vs DOS LBA_BRK.HQR (LZSS-compressed): {ORIGINAL_HQR_BYTES:>12,} bytes ({ORIGINAL_HQR_BYTES/1024/1024:.2f} MB)")
    print(f"  Reduction: {100*(1-cart_total/ORIGINAL_HQR_BYTES):.1f}%")

    # Try LZ on top of brick payload
    all_brick_data = b''.join(b[1] for b in blobs)
    z = zlib.compress(all_brick_data, 9)
    cart_with_lz = file_header_bytes + len(z)
    print(f"\nIf we LZ-compress brick payload (zlib L9, just to estimate; cart would use a smaller decoder):")
    print(f"  Brick data:              {len(all_brick_data):>8,} -> {len(z):>8,} bytes ({100*len(z)/len(all_brick_data):.1f}%)")
    print(f"  CART TOTAL (compr):      {cart_with_lz:>8,} bytes ({cart_with_lz/1024/1024:.2f} MB)")
    print(f"  Reduction vs DOS:        {100*(1-cart_with_lz/ORIGINAL_HQR_BYTES):.1f}%")

if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--brk', default=r'I:\GOG\Little Big Adventure\Common\LBA_BRK.HQR')
    ap.add_argument('--ress', default=r'I:\GOG\Little Big Adventure\Common\RESS.HQR')
    args = ap.parse_args()
    main(args.brk, args.ress)
