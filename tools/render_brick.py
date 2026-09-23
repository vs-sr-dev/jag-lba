#!/usr/bin/env python3
"""Render an LBA1 brick (LBA_BRK.HQR entry) to RGBA PNG.

Brick format (after HQR decompression — see GRAPHMSK.ASM in lba1-classic):
  Header (4 bytes): DX, DY, HotX, HotY (UBYTE each)
  Per row (DY rows): NbBlock byte, then NbBlock opcode-blocks:
    00xxxxxx        -> skip (xxxxxx+1) transparent pixels
    01xxxxxx + N b  -> copy (xxxxxx+1) raw palette indices (N = count)
    10xxxxxx + 1 b  -> repeat (xxxxxx+1) times next palette index byte

Palette: RESS_PAL = entry 0 of RESS.HQR (global game palette).

Usage:
  python render_brick.py <BRK.HQR> <brick_idx> <RESS.HQR> [--scale N] [-o out.png]
"""
import sys, os, struct, zlib, argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hqr_decompress import parse_offsets, extract_entry
from render_slide import normalize_palette

def decode_brick(payload):
    if len(payload) < 4:
        raise ValueError("brick too small for header")
    dx, dy, hot_x, hot_y = payload[0], payload[1], payload[2], payload[3]
    pixels = [[0]*dx for _ in range(dy)]
    alpha  = [[0]*dx for _ in range(dy)]
    pos = 4
    for row in range(dy):
        if pos >= len(payload):
            raise ValueError(f"truncated at row {row}")
        nb_block = payload[pos]; pos += 1
        col = 0
        for blk in range(nb_block):
            if pos >= len(payload):
                raise ValueError(f"truncated row {row} block {blk}")
            op = payload[pos]; pos += 1
            count = (op & 0x3F) + 1
            mode = op & 0xC0
            if mode == 0x00:
                col += count
            elif mode == 0x40:
                for i in range(count):
                    if col + i < dx:
                        pixels[row][col+i] = payload[pos+i]
                        alpha[row][col+i]  = 255
                pos += count
                col += count
            elif mode == 0x80:
                pix = payload[pos]; pos += 1
                for i in range(count):
                    if col + i < dx:
                        pixels[row][col+i] = pix
                        alpha[row][col+i]  = 255
                col += count
            else:
                raise ValueError(f"bad opcode {op:02X} at row {row} block {blk}")
        if col != dx:
            print(f"  warn row {row}: col total {col} != DX {dx}", file=sys.stderr)
    return dx, dy, hot_x, hot_y, pixels, alpha, pos

def write_png_rgba(path, w, h, rgba_bytes):
    def chunk(tag, data):
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack('>I', len(data)) + tag + data + struct.pack('>I', crc)
    sig = b'\x89PNG\r\n\x1a\n'
    ihdr = struct.pack('>IIBBBBB', w, h, 8, 6, 0, 0, 0)  # truecolor + alpha
    raw = bytearray()
    for y in range(h):
        raw.append(0)
        raw.extend(rgba_bytes[y*w*4 : (y+1)*w*4])
    idat = zlib.compress(bytes(raw), 6)
    with open(path, 'wb') as f:
        f.write(sig)
        f.write(chunk(b'IHDR', ihdr))
        f.write(chunk(b'IDAT', idat))
        f.write(chunk(b'IEND', b''))

def render(brk_path, brick_idx, ress_path, out_path, scale=1):
    with open(brk_path, 'rb') as f: brk_data = f.read()
    brk_offsets = parse_offsets(brk_data)
    payload = extract_entry(brk_data, brick_idx, brk_offsets)
    if payload is None:
        raise ValueError(f"brick {brick_idx} BLANK")

    with open(ress_path, 'rb') as f: ress_data = f.read()
    ress_offsets = parse_offsets(ress_data)
    pal_raw = extract_entry(ress_data, 0, ress_offsets)
    palette = normalize_palette(pal_raw)

    dx, dy, hot_x, hot_y, pixels, alpha, consumed = decode_brick(payload)
    print(f"Brick {brick_idx}: DX={dx} DY={dy} HotX={hot_x} HotY={hot_y} payload={len(payload)} bytes consumed={consumed}")

    sw, sh = dx*scale, dy*scale
    rgba = bytearray(sw*sh*4)
    for y in range(dy):
        for x in range(dx):
            r, g, b = palette[pixels[y][x]]
            a = alpha[y][x]
            for sy in range(scale):
                for sx in range(scale):
                    p = ((y*scale+sy)*sw + (x*scale+sx)) * 4
                    rgba[p]   = r
                    rgba[p+1] = g
                    rgba[p+2] = b
                    rgba[p+3] = a
    write_png_rgba(out_path, sw, sh, bytes(rgba))
    print(f"Wrote {out_path} ({sw}x{sh})")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('brk_path')
    ap.add_argument('brick_index', type=int)
    ap.add_argument('ress_path')
    ap.add_argument('--scale', type=int, default=4)
    ap.add_argument('-o', '--output', default=None)
    args = ap.parse_args()
    out = args.output or f'brick_{args.brick_index:05d}_x{args.scale}.png'
    render(args.brk_path, args.brick_index, args.ress_path, out, args.scale)

if __name__ == '__main__':
    main()
