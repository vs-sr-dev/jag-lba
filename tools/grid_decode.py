#!/usr/bin/env python3
"""Decompress an LBA1 grid (LBA_GRI.HQR entry) to 64x25x64 cube of cell values.

Grid file (post-HQR-decompression):
  Bytes 0..8191: offset table = 4096 UWORDs (indexed as x + z*64), each UWORD is
                 a byte offset into the file pointing to that column's RLE data.
  Bytes 8192+:   column RLE data.

Column-RLE (per DecompColonne in GRILLE_A.ASM):
  BYTE NbEntity
  Per entity:
    BYTE opcode = (2-bit mode | 6-bit (count-1))
      00xxxxxx -> zero fill `count` cells
      01xxxxxx OR 11xxxxxx -> "diff": read `count` UWORDs, copy as-is
      10xxxxxx -> "same":  read 1 UWORD, repeat `count` times
  Each cell is a UWORD = NumBlock(high byte) + PosBlock(low byte).
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import lba_paths
import sys, os, struct, argparse
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hqr_decompress import parse_offsets, extract_entry

CUBE_X, CUBE_Y, CUBE_Z = 64, 25, 64

def decomp_column(data, offset):
    pos = offset
    nb_entity = data[pos]; pos += 1
    cells = []
    for _ in range(nb_entity):
        op = data[pos]; pos += 1
        count = (op & 0x3F) + 1
        mode = op & 0xC0
        if mode == 0x00:
            cells.extend([0] * count)
        elif mode == 0x80:
            v = struct.unpack('<H', data[pos:pos+2])[0]
            pos += 2
            cells.extend([v] * count)
        else:  # diff (01 or 11)
            for _ in range(count):
                v = struct.unpack('<H', data[pos:pos+2])[0]
                cells.append(v)
                pos += 2
    return cells, pos

def decode_grid(payload):
    cube = [[None]*CUBE_X for _ in range(CUBE_Z)]
    overflow_warnings = 0
    for z in range(CUBE_Z):
        for x in range(CUBE_X):
            offset_idx = (x + z*CUBE_Z) * 2
            col_offset = struct.unpack('<H', payload[offset_idx:offset_idx+2])[0]
            cells, _ = decomp_column(payload, col_offset)
            if len(cells) != CUBE_Y:
                overflow_warnings += 1
                if len(cells) < CUBE_Y:
                    cells = cells + [0]*(CUBE_Y - len(cells))
                else:
                    cells = cells[:CUBE_Y]
            cube[z][x] = cells
    return cube, overflow_warnings

def main(grid_path, grid_idx):
    with open(grid_path, 'rb') as f:
        data = f.read()
    offsets = parse_offsets(data)
    payload = extract_entry(data, grid_idx, offsets)
    print(f"Grid {grid_idx}: payload {len(payload)} bytes")

    cube, ofw = decode_grid(payload)
    if ofw:
        print(f"  WARN: {ofw} columns had cell count != {CUBE_Y}")

    total_cells = CUBE_X * CUBE_Y * CUBE_Z
    filled = 0
    cell_hist = Counter()
    block_hist = Counter()
    for z in range(CUBE_Z):
        for x in range(CUBE_X):
            for y in range(CUBE_Y):
                v = cube[z][x][y]
                if v != 0:
                    filled += 1
                    cell_hist[v] += 1
                    block_hist[v >> 8] += 1

    print(f"\nTotal cells:    {total_cells:>7,}")
    print(f"Filled cells:   {filled:>7,}  ({100*filled/total_cells:.1f}%)")
    print(f"Unique (NumBlock,PosBlock) pairs: {len(cell_hist)}")
    print(f"Unique NumBlock values:           {len(block_hist)}")

    print("\n--- Top-10 most common cell values ---")
    print("  cell    NumBlock  PosBlock  count")
    for (v, count) in cell_hist.most_common(10):
        nb = v >> 8
        pb = v & 0xFF
        print(f"  0x{v:04X}  {nb:>8}  {pb:>8}  {count:>6}")

    print("\n--- NumBlock usage histogram (top 10) ---")
    for (nb, count) in block_hist.most_common(10):
        print(f"  NumBlock {nb:>3}: {count:>6} cells")

    # Floor profile (Y level distribution)
    y_hist = Counter()
    for z in range(CUBE_Z):
        for x in range(CUBE_X):
            for y in range(CUBE_Y):
                if cube[z][x][y] != 0:
                    y_hist[y] += 1
    print("\n--- Filled cells by Y level ---")
    for y in range(CUBE_Y):
        c = y_hist.get(y, 0)
        bar = '#' * (c * 60 // max(y_hist.values())) if y_hist else ''
        print(f"  Y={y:>2}: {c:>5}  {bar}")

    # XZ footprint at Y=0 (ground level) — ASCII map
    print("\n--- Ground floor (Y=0) — XZ footprint (each char = 1 cell, '.'=empty, '#'=filled) ---")
    for z in range(CUBE_Z):
        row = ''
        for x in range(CUBE_X):
            row += '#' if cube[z][x][0] != 0 else '.'
        print(f"  z={z:>2}: {row}")

if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--gri', default=lba_paths.common('LBA_GRI.HQR'))
    ap.add_argument('--idx', type=int, default=0)
    args = ap.parse_args()
    main(args.gri, args.idx)
