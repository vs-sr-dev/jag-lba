#!/usr/bin/env python3
"""HQR LZSS decompressor for LBA1/LBA2 HQR/VOX/ILE/OBL files.

Compression types:
  0: no compression (raw copy)
  1: LZSS, length = (low_nibble & 0xF) + 2
  2: LZSS, length = (low_nibble & 0xF) + 3

LZSS bitstream:
  Read 1 flag byte. Process its 8 bits LSB-first. For each bit:
    1 -> copy next 1 byte literal
    0 -> read 2-byte back-reference:
            b0 = src[pos]; b1 = src[pos+1]
            length = (b0 & 0x0F) + ctype + 1
            offset = ((b0 >> 4) | (b1 << 4)) + 1   # back from current dst pos
            copy `length` bytes from dst[dst_pos - offset]

Usage:
  python hqr_decompress.py <file.hqr> <entry_index> [-o output.bin]
  python hqr_decompress.py <file.hqr> --extract-all <out_dir>
"""
import sys, struct, argparse, os

def parse_offsets(data):
    first_off = struct.unpack('<I', data[0:4])[0]
    n_entries = first_off // 4 - 1
    offsets = [struct.unpack('<I', data[i*4:(i+1)*4])[0] for i in range(n_entries + 1)]
    return offsets[:-1]  # drop file-size marker

def read_entry_header(data, off):
    orig = struct.unpack('<I', data[off:off+4])[0]
    comp = struct.unpack('<I', data[off+4:off+8])[0]
    ctype = struct.unpack('<H', data[off+8:off+10])[0]
    return orig, comp, ctype

def decompress_lzss(src, dst_size, ctype):
    if ctype not in (1, 2):
        raise ValueError(f"unsupported compression type {ctype}")
    dst = bytearray()
    sp = 0
    n = len(src)
    while len(dst) < dst_size:
        if sp >= n:
            raise ValueError(f"source exhausted at output {len(dst)}/{dst_size}")
        flag = src[sp]; sp += 1
        for bit in range(8):
            if len(dst) >= dst_size:
                break
            if (flag >> bit) & 1:
                if sp >= n:
                    raise ValueError("source exhausted reading literal")
                dst.append(src[sp]); sp += 1
            else:
                if sp + 2 > n:
                    raise ValueError("source exhausted reading backref")
                b0 = src[sp]; b1 = src[sp+1]; sp += 2
                length = (b0 & 0x0F) + ctype + 1
                offset = ((b0 >> 4) | (b1 << 4)) + 1
                start = len(dst) - offset
                if start < 0:
                    raise ValueError(f"backref offset {offset} > dst length {len(dst)}")
                for i in range(length):
                    if len(dst) >= dst_size:
                        break
                    dst.append(dst[start + i])
    return bytes(dst)

def compress_lzss(src, ctype=1):
    """L'inverso di decompress_lzss, per i payload che il packer GENERA (S43:
    i corpi pre-digeriti). Sta in questo modulo apposta: le due direzioni si
    verificano a vicenda, e il roundtrip e' il test - chi chiama DEVE
    verificare decompress_lzss(compress_lzss(x), len(x), ctype) == x, che e'
    lo stesso decompressore trascritto in engine_c/hal_hqr.c.

    Greedy con catene di hash sulla coppia di byte: non produce i byte del
    compressore Adeline, produce un FLUSSO VALIDO per il suo decompressore.
    Lunghezze 2..17 (ctype 1), scostamenti 1..4096, corse auto-referenzianti
    ammesse (il decoder copia in avanti byte per byte)."""
    if ctype not in (1, 2):
        raise ValueError("ctype %r non supportato" % (ctype,))
    n = len(src)
    minlen = ctype + 1
    maxlen = 15 + ctype + 1
    out = bytearray()
    heads = {}
    pos = 0
    flag_pos = -1
    flag = 0
    nbit = 8
    while pos < n:
        if nbit == 8:
            if flag_pos >= 0:
                out[flag_pos] = flag
            flag_pos = len(out)
            out.append(0)
            flag = 0
            nbit = 0
        best_len = 0
        best_off = 0
        if pos + minlen <= n:
            key = src[pos:pos + 2]
            cands = heads.get(bytes(key))
            if cands:
                lim = maxlen if n - pos > maxlen else n - pos
                for c in reversed(cands[-64:]):
                    if pos - c > 4096:
                        break
                    l = 0
                    while l < lim and src[c + l] == src[pos + l]:
                        l += 1
                    if l > best_len:
                        best_len = l
                        best_off = pos - c
                        if l == lim:
                            break
        if best_len >= minlen:
            stored = best_off - 1
            out.append((best_len - ctype - 1) | ((stored & 0x0F) << 4))
            out.append(stored >> 4)
            end = pos + best_len
            while pos < end:
                heads.setdefault(bytes(src[pos:pos + 2]), []).append(pos)
                pos += 1
        else:
            out.append(src[pos])
            flag |= 1 << nbit
            heads.setdefault(bytes(src[pos:pos + 2]), []).append(pos)
            pos += 1
        nbit += 1
    if flag_pos >= 0:
        out[flag_pos] = flag
    return bytes(out)

def extract_entry(data, idx, offsets):
    if idx >= len(offsets):
        raise IndexError(f"entry {idx} out of range ({len(offsets)} total)")
    off = offsets[idx]
    if off == 0:
        return None
    orig, comp, ctype = read_entry_header(data, off)
    payload = data[off+10 : off+10+comp]
    if ctype == 0:
        if len(payload) != orig:
            raise ValueError(f"type0 size mismatch: payload {len(payload)} vs orig {orig}")
        return payload
    return decompress_lzss(payload, orig, ctype)

def hexdump(b, width=16):
    lines = []
    for i in range(0, len(b), width):
        chunk = b[i:i+width]
        hx = ' '.join(f'{x:02X}' for x in chunk)
        asc = ''.join(chr(x) if 32 <= x < 127 else '.' for x in chunk)
        lines.append(f"  {i:04X}  {hx:<{width*3-1}}  {asc}")
    return '\n'.join(lines)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('path')
    ap.add_argument('index', type=int, nargs='?', default=None)
    ap.add_argument('-o', '--output')
    ap.add_argument('--extract-all', metavar='DIR')
    ap.add_argument('--head', type=int, default=64)
    args = ap.parse_args()

    with open(args.path, 'rb') as f:
        data = f.read()
    offsets = parse_offsets(data)

    if args.extract_all:
        os.makedirs(args.extract_all, exist_ok=True)
        ok = blanks = errors = 0
        type_counts = {0:0, 1:0, 2:0}
        for i in range(len(offsets)):
            off = offsets[i]
            if off == 0:
                blanks += 1
                continue
            try:
                _, _, ctype = read_entry_header(data, off)
                payload = extract_entry(data, i, offsets)
                with open(os.path.join(args.extract_all, f'entry_{i:04d}.bin'), 'wb') as fo:
                    fo.write(payload)
                ok += 1
                type_counts[ctype] = type_counts.get(ctype, 0) + 1
            except Exception as e:
                print(f"  entry {i}: ERROR {e}")
                errors += 1
        print(f"Extracted {ok} entries, {blanks} blank, {errors} errors -> {args.extract_all}")
        print(f"  type0={type_counts.get(0,0)} type1={type_counts.get(1,0)} type2={type_counts.get(2,0)}")
        return

    if args.index is None:
        print(f"{args.path}: {len(offsets)} entries — pass an index or --extract-all DIR")
        return

    payload = extract_entry(data, args.index, offsets)
    if payload is None:
        print(f"Entry {args.index}: BLANK")
        return
    off = offsets[args.index]
    orig, comp, ctype = read_entry_header(data, off)
    ok = len(payload) == orig
    print(f"Entry {args.index}: ctype={ctype} comp={comp} orig_expected={orig} got={len(payload)} match={ok}")
    print(f"\nFirst {min(args.head, len(payload))} bytes of decompressed output:")
    print(hexdump(payload[:args.head]))
    if args.output:
        with open(args.output, 'wb') as fo:
            fo.write(payload)
        print(f"\nWrote {len(payload)} bytes -> {args.output}")

if __name__ == '__main__':
    main()
