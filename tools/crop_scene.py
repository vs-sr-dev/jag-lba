#!/usr/bin/env python3
"""Crop a small region of a scene PNG (or paste two crops side-by-side) for visual inspection.
Pure stdlib (no PIL).
"""
import sys, os, struct, zlib, argparse

def png_read(path):
    with open(path, 'rb') as f: data = f.read()
    assert data[:8] == b'\x89PNG\r\n\x1a\n'
    pos = 8
    chunks = {}
    width = height = bd = ct = None
    idat = b''
    while pos < len(data):
        ln = struct.unpack('>I', data[pos:pos+4])[0]; pos += 4
        tag = data[pos:pos+4]; pos += 4
        body = data[pos:pos+ln]; pos += ln
        pos += 4  # crc
        if tag == b'IHDR':
            width, height, bd, ct = struct.unpack('>IIBB', body[0:10])
        elif tag == b'IDAT':
            idat += body
    raw = zlib.decompress(idat)
    bpp = {0:1, 2:3, 3:1, 4:2, 6:4}[ct]
    stride = width * bpp
    out = bytearray(stride * height)
    pos = 0
    prev = bytearray(stride)
    for y in range(height):
        filt = raw[pos]; pos += 1
        line = bytearray(raw[pos:pos+stride]); pos += stride
        if filt == 0: pass
        elif filt == 1:  # Sub
            for i in range(bpp, stride): line[i] = (line[i] + line[i-bpp]) & 0xFF
        elif filt == 2:  # Up
            for i in range(stride): line[i] = (line[i] + prev[i]) & 0xFF
        elif filt == 3:  # Average
            for i in range(stride):
                a = line[i-bpp] if i >= bpp else 0; b = prev[i]
                line[i] = (line[i] + (a + b) // 2) & 0xFF
        elif filt == 4:  # Paeth
            for i in range(stride):
                a = line[i-bpp] if i >= bpp else 0; b = prev[i]; c = prev[i-bpp] if i >= bpp else 0
                p = a + b - c; pa = abs(p-a); pb = abs(p-b); pc = abs(p-c)
                pred = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
                line[i] = (line[i] + pred) & 0xFF
        out[y*stride:(y+1)*stride] = line
        prev = line
    return width, height, bpp, ct, bytes(out)

def png_write(path, w, h, bpp, ct, data):
    def chunk(tag, body):
        crc = zlib.crc32(tag + body) & 0xFFFFFFFF
        return struct.pack('>I', len(body)) + tag + body + struct.pack('>I', crc)
    sig = b'\x89PNG\r\n\x1a\n'
    ihdr = struct.pack('>IIBBBBB', w, h, 8, ct, 0, 0, 0)
    raw = bytearray()
    stride = w * bpp
    for y in range(h):
        raw.append(0); raw.extend(data[y*stride:(y+1)*stride])
    idat = zlib.compress(bytes(raw), 6)
    with open(path, 'wb') as f:
        f.write(sig); f.write(chunk(b'IHDR', ihdr)); f.write(chunk(b'IDAT', idat)); f.write(chunk(b'IEND', b''))

def crop(src, x, y, cw, ch):
    w, h, bpp, ct, data = png_read(src)
    cw = min(cw, w - x); ch = min(ch, h - y)
    out = bytearray(cw * ch * bpp)
    for row in range(ch):
        src_off = ((y + row) * w + x) * bpp
        dst_off = row * cw * bpp
        out[dst_off:dst_off+cw*bpp] = data[src_off:src_off+cw*bpp]
    return cw, ch, bpp, ct, bytes(out)

def downscale(src, factor):
    w, h, bpp, ct, data = png_read(src)
    nw = w // factor; nh = h // factor
    out = bytearray(nw * nh * bpp)
    for y in range(nh):
        sy = y * factor
        for x in range(nw):
            sx = x * factor
            src_off = (sy * w + sx) * bpp
            dst_off = (y * nw + x) * bpp
            out[dst_off:dst_off+bpp] = data[src_off:src_off+bpp]
    return nw, nh, bpp, ct, bytes(out)

if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('src')
    ap.add_argument('out')
    ap.add_argument('--mode', choices=['crop', 'downscale'], default='crop')
    ap.add_argument('--x', type=int, default=0)
    ap.add_argument('--y', type=int, default=0)
    ap.add_argument('--w', type=int, default=400)
    ap.add_argument('--h', type=int, default=300)
    ap.add_argument('--factor', type=int, default=4)
    args = ap.parse_args()
    if args.mode == 'crop':
        cw, ch, bpp, ct, body = crop(args.src, args.x, args.y, args.w, args.h)
    else:
        cw, ch, bpp, ct, body = downscale(args.src, args.factor)
    png_write(args.out, cw, ch, bpp, ct, body)
    print(f'wrote {args.out} {cw}x{ch}')
