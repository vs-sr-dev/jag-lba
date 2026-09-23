#!/usr/bin/env python3
"""HQR header inspector — reads offset block + entry headers, prints summary.
Does NOT decompress entry bodies. Safe to run on game asset files.

Usage: python hqr_inspect.py <file.hqr> [--limit N]
"""
import sys, struct, argparse

def inspect(path, limit=40):
    with open(path, 'rb') as f:
        data = f.read()
    n = len(data)

    if n < 8:
        print(f"{path}: too small ({n} bytes)")
        return

    first_off = struct.unpack('<I', data[0:4])[0]
    if first_off > n or first_off % 4 != 0 or first_off < 4:
        print(f"{path}: malformed (first offset={first_off}, file size={n})")
        return

    n_entries = first_off // 4 - 1
    offsets = [struct.unpack('<I', data[i*4:(i+1)*4])[0] for i in range(n_entries + 1)]
    file_size_marker = offsets[-1]
    entry_offs = offsets[:-1]

    print(f"=== {path} ===")
    print(f"  File size:           {n:,} bytes")
    print(f"  Offset block:        {first_off} bytes -> {n_entries} entries + size marker")
    print(f"  Size marker:         {file_size_marker:,}  (matches file: {file_size_marker == n})")

    blank = 0
    repeated = 0
    normal = 0
    seen = {}
    type_count = {0:0, 1:0, 2:0}
    total_orig = 0
    total_comp = 0

    rows = []
    for idx, off in enumerate(entry_offs):
        if off == 0:
            blank += 1
            rows.append((idx, 'BLANK', '', '', '', ''))
            continue
        if off in seen:
            repeated += 1
            rows.append((idx, off, '(repeat)', '', '', f'-> entry {seen[off]}'))
            continue
        seen[off] = idx
        normal += 1
        if off + 10 > n:
            rows.append((idx, off, '<bad>', '', '', ''))
            continue
        orig = struct.unpack('<I', data[off:off+4])[0]
        comp = struct.unpack('<I', data[off+4:off+8])[0]
        ctype = struct.unpack('<H', data[off+8:off+10])[0]
        total_orig += orig
        total_comp += comp
        type_count[ctype] = type_count.get(ctype, 0) + 1
        # Peek at first 8 bytes of (possibly compressed) data
        peek = data[off+10:off+10+min(8, comp)]
        peek_hex = ' '.join(f'{b:02X}' for b in peek)
        ascii_repr = ''.join(chr(b) if 32 <= b < 127 else '.' for b in peek) if ctype == 0 else ''
        rows.append((idx, off, orig, comp, ctype, f'{peek_hex} {ascii_repr}'))

    print(f"\n  {'Idx':>5} {'Offset':>10} {'Orig':>10} {'Comp':>10} {'T':>3}  Peek")
    print(f"  {'-'*5} {'-'*10} {'-'*10} {'-'*10} {'-'*3}  {'-'*40}")
    shown = 0
    for r in rows:
        if shown >= limit and limit > 0:
            print(f"  ... {len(rows) - shown} more entries (use --limit 0 for all)")
            break
        print(f"  {r[0]:>5} {str(r[1]):>10} {str(r[2]):>10} {str(r[3]):>10} {str(r[4]):>3}  {r[5]}")
        shown += 1

    print(f"\n  Summary:")
    print(f"    {normal:>5} normal, {repeated:>5} repeated, {blank:>5} blank")
    print(f"    Compression: type0={type_count.get(0,0)}, type1={type_count.get(1,0)}, type2={type_count.get(2,0)}")
    print(f"    Total uncompressed: {total_orig:>12,} bytes ({total_orig/1024/1024:.2f} MB)")
    print(f"    Total compressed:   {total_comp:>12,} bytes ({total_comp/1024/1024:.2f} MB)")
    if total_comp:
        print(f"    Ratio:              {total_orig/total_comp:.2f}x")
    if normal:
        print(f"    Avg uncompressed:   {total_orig/normal:>12,.0f} bytes/entry")
        print(f"    Min/Max uncomp:     {min((r[2] for r in rows if isinstance(r[2], int)), default=0):,} / {max((r[2] for r in rows if isinstance(r[2], int)), default=0):,}")

if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('path')
    ap.add_argument('--limit', type=int, default=40, help='entries to show in table (0 = all)')
    args = ap.parse_args()
    inspect(args.path, args.limit)
