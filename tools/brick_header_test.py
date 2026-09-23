#!/usr/bin/env python3
"""Decode brick 0 with both header conventions and dump raw bytes."""
import sys, os, struct
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hqr_decompress import parse_offsets, extract_entry

BRK = r'I:\GOG\Little Big Adventure\Common\LBA_BRK.HQR'

with open(BRK, 'rb') as f: data = f.read()
offsets = parse_offsets(data)

for bidx in [0, 1, 2, 3, 38, 145]:
    payload = extract_entry(data, bidx, offsets)
    if payload is None:
        print(f'brick {bidx}: BLANK'); continue
    print(f'\n=== brick {bidx} (payload {len(payload)} bytes) ===')
    print(f'  bytes[0..15] = {" ".join(f"{b:02X}" for b in payload[:16])}')
    print(f'  byte 0 = {payload[0]} (DX)')
    print(f'  byte 1 = {payload[1]} (DY)')
    print(f'  byte 2 = {payload[2]}  -- with 4-byte hdr: HotX | with 2-byte hdr: NbBlock for line 0')
    print(f'  byte 3 = {payload[3]}  -- with 4-byte hdr: HotY | with 2-byte hdr: opcode 1 of line 0 (mode={(payload[3]>>6):02b})')
    print(f'  byte 4 = {payload[4]}  -- with 4-byte hdr: NbBlock for line 0 | with 2-byte hdr: pixel data or next opcode')
    print(f'  byte 5 = {payload[5]}  -- with 4-byte hdr: opcode 1 of line 0 (mode={(payload[5]>>6):02b})')

    # Test both decoders by walking until we cover DY rows
    for hdr_size in [2, 4]:
        dx = payload[0]; dy = payload[1]
        pos = hdr_size
        ok = True; warnings = []
        for row in range(dy):
            if pos >= len(payload):
                warnings.append(f'truncated at row {row}'); ok = False; break
            nb_block = payload[pos]; pos += 1
            col = 0
            for blk in range(nb_block):
                if pos >= len(payload):
                    warnings.append(f'row {row} block {blk}: truncated'); ok = False; break
                op = payload[pos]; pos += 1
                count = (op & 0x3F) + 1
                mode = op & 0xC0
                if mode == 0xC0:
                    warnings.append(f'row {row} block {blk}: invalid opcode 11xxxxxx (op=0x{op:02X})'); ok = False; break
                if mode == 0x00:
                    col += count
                elif mode == 0x40:
                    pos += count
                    col += count
                elif mode == 0x80:
                    pos += 1
                    col += count
            if not ok: break
            if col != dx:
                warnings.append(f'row {row}: col {col} != DX {dx}')
                ok = False
        consumed = pos
        status = 'OK' if (ok and consumed == len(payload)) else 'FAIL'
        print(f'  hdr={hdr_size}: {status}  consumed={consumed}/{len(payload)}  warnings={warnings[:3]}')
