#!/usr/bin/env python3
"""Verify whether palette index 0 is used as an OPAQUE pixel anywhere in BRK.HQR.

If palette[0] is reserved as transparent-only, we can use Blitter BCOMPEN
(skip-on-zero) for masked brick blits without a separate mask plane.
"""
import sys, os, argparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hqr_decompress import parse_offsets, extract_entry
from render_brick import decode_brick


def main(brk_path):
    with open(brk_path, 'rb') as f: data = f.read()
    offsets = parse_offsets(data)
    n_total = len(offsets)
    print(f'BRK has {n_total} entries')

    bricks_with_zero = []
    total_zero_pixels = 0
    total_opaque_pixels = 0
    n_blank = 0
    n_decoded = 0

    for bidx in range(n_total):
        try:
            payload = extract_entry(data, bidx, offsets)
            if payload is None:
                n_blank += 1; continue
            dx, dy, hx, hy, pixels, alpha, _ = decode_brick(payload)
            n_decoded += 1
            zero_count = 0
            opaque_count = 0
            for y in range(dy):
                for x in range(dx):
                    if alpha[y][x] != 0:
                        opaque_count += 1
                        if pixels[y][x] == 0:
                            zero_count += 1
            total_opaque_pixels += opaque_count
            total_zero_pixels += zero_count
            if zero_count > 0:
                bricks_with_zero.append((bidx, zero_count, opaque_count, dx, dy))
        except Exception as e:
            print(f'brick {bidx}: error {e}')

    print(f'\nDecoded: {n_decoded}/{n_total} (blank: {n_blank})')
    print(f'Total opaque pixels: {total_opaque_pixels:,}')
    print(f'Opaque pixels with palette idx 0: {total_zero_pixels:,}')
    if total_opaque_pixels:
        print(f'Fraction: {100*total_zero_pixels/total_opaque_pixels:.6f}%')
    print(f'\nBricks with at least one opaque palette-0 pixel: {len(bricks_with_zero)}')
    if bricks_with_zero:
        print('First 10 offenders:')
        for bidx, zc, oc, dx, dy in bricks_with_zero[:10]:
            print(f'  brick {bidx} ({dx}x{dy}): {zc} opaque-zero / {oc} opaque  ({100*zc/oc:.1f}%)')


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--brk', default=r'I:\GOG\Little Big Adventure\Common\LBA_BRK.HQR')
    args = ap.parse_args()
    main(args.brk)
