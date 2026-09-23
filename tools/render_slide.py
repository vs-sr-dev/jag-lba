#!/usr/bin/env python3
"""Render an LBA1 paletted slide image (typically 640x480x8bpp) to PNG.

Usage:
  python render_slide.py <file.hqr> <slide_index> [-p palette_index] [-o out.png]

Auto-detects:
  - Image dimensions from payload size (640x480 = 307200, 320x200 = 64000, 320x240 = 76800)
  - Palette depth (6-bit VGA scaled to 8-bit, or already 8-bit)

Uses only Python stdlib (zlib + struct) for PNG write. No PIL dependency.
"""
import sys, os, struct, zlib, argparse

# Reuse the decompressor from sibling module
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hqr_decompress import parse_offsets, extract_entry

DIM_BY_SIZE = {
    307200: (640, 480),
    76800:  (320, 240),
    64000:  (320, 200),
    16384:  (128, 128),
}

def write_png(path, w, h, rgb_pixels):
    def chunk(tag, data):
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack('>I', len(data)) + tag + data + struct.pack('>I', crc)
    sig = b'\x89PNG\r\n\x1a\n'
    ihdr = struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0)  # 8-bit RGB
    raw = bytearray()
    for y in range(h):
        raw.append(0)  # filter: None
        raw.extend(rgb_pixels[y*w*3 : (y+1)*w*3])
    idat = zlib.compress(bytes(raw), 6)
    with open(path, 'wb') as f:
        f.write(sig)
        f.write(chunk(b'IHDR', ihdr))
        f.write(chunk(b'IDAT', idat))
        f.write(chunk(b'IEND', b''))

def normalize_palette(pal_bytes):
    """Return list of 256 (R,G,B) tuples in 8-bit range."""
    if len(pal_bytes) != 768:
        raise ValueError(f"palette size {len(pal_bytes)} != 768")
    max_v = max(pal_bytes)
    if max_v <= 63:
        # 6-bit VGA -> scale to 8-bit by replicating high 2 bits in low 2
        scale = lambda v: (v << 2) | (v >> 4)
    else:
        scale = lambda v: v
    return [(scale(pal_bytes[i*3]), scale(pal_bytes[i*3+1]), scale(pal_bytes[i*3+2])) for i in range(256)]

def render(hqr_path, slide_idx, palette_idx, out_path):
    with open(hqr_path, 'rb') as f:
        data = f.read()
    offsets = parse_offsets(data)

    slide = extract_entry(data, slide_idx, offsets)
    if slide is None:
        raise ValueError(f"slide entry {slide_idx} is BLANK")
    pal_raw = extract_entry(data, palette_idx, offsets)
    if pal_raw is None:
        raise ValueError(f"palette entry {palette_idx} is BLANK")

    if len(slide) not in DIM_BY_SIZE:
        # Try square or aspect-ratio guess
        raise ValueError(f"unknown slide size {len(slide)}, supported: {list(DIM_BY_SIZE)}")
    w, h = DIM_BY_SIZE[len(slide)]

    palette = normalize_palette(pal_raw)
    max_v = max(pal_raw)
    print(f"Slide {slide_idx}: {len(slide)} bytes -> {w}x{h}")
    print(f"Palette {palette_idx}: max byte value {max_v} -> {'6-bit VGA scaled' if max_v <= 63 else '8-bit native'}")

    rgb = bytearray(w * h * 3)
    for i, idx in enumerate(slide):
        r, g, b = palette[idx]
        rgb[i*3] = r
        rgb[i*3+1] = g
        rgb[i*3+2] = b

    write_png(out_path, w, h, bytes(rgb))
    print(f"Wrote {out_path}")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('path')
    ap.add_argument('slide_index', type=int)
    ap.add_argument('-p', '--palette-index', type=int, default=0)
    ap.add_argument('-o', '--output', default=None)
    args = ap.parse_args()

    out = args.output or f'slide_{args.slide_index:03d}_pal{args.palette_index:03d}.png'
    render(args.path, args.slide_index, args.palette_index, out)

if __name__ == '__main__':
    main()
