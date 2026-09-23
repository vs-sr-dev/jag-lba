#!/usr/bin/env python3
"""Unpack the LBA1 DOS floppy install set (disk1.IMG .. disk8.IMG).

The eight 1.44 MB floppies are plain FAT12. disk1 carries INSTALL.EXE,
DOS4GW.EXE, SETUP.LST, README.TXT and LBA.000; disk2..8 carry only
LBA.001..LBA.007. LBA.000..007 concatenated form ONE Adeline installer
archive (11,315,284 bytes stored / 11,962,108 unpacked, 61 files):

  header   u8  ?                     (0x08)
           u32 total unpacked size
           u16 number of files       (61)
  table    per file, 20 bytes:
           char name[13]  8.3, NUL padded
           u8   ?         (always 0)
           u32  size      unpacked
           u16  ?         (checksum, not needed to unpack)
  data     sequence of chunks, each:
           u8   method    0 = stored, 1 = LZSS
           u16  out_len   unpacked bytes (65535 except the last)
           u16  in_len    stored bytes following the header
           The unpacked chunks concatenated are the files back to back in
           table order (chunks do NOT align to file boundaries).

LZSS is the engine's ExpandLZ (LIB386/SYSTEM/LZ.CPP): flag byte, bit=1
literal, bit=0 -> 2 bytes [lo][hi]: offset = hi<<4 | lo>>4 (back-reference
distance - 1), length = (lo & 15) + 2 (min_bloc = 1 here; HQR uses 2).

Usage:
  python lba_floppy_unpack.py <dir with disk*.IMG> <output dir>
  python lba_floppy_unpack.py <LBA.000> ... <LBA.007> <output dir>
"""
import glob
import os
import struct
import sys


# ── FAT12 (1.44 MB MSDOS5 boot sector, root-dir files only) ──────────────────
def fat12_files(img):
    bps = struct.unpack('<H', img[0x0B:0x0D])[0]
    spc = img[0x0D]
    rsvd = struct.unpack('<H', img[0x0E:0x10])[0]
    nfats = img[0x10]
    nroot = struct.unpack('<H', img[0x11:0x13])[0]
    spfat = struct.unpack('<H', img[0x16:0x18])[0]
    fat_off = rsvd * bps
    fat = img[fat_off:fat_off + spfat * bps]
    root_off = fat_off + nfats * spfat * bps
    data_off = root_off + nroot * 32
    csize = spc * bps

    def chain(cl):
        while 2 <= cl < 0xFF8:
            yield cl
            i = cl + (cl >> 1)
            v = fat[i] | (fat[i + 1] << 8)
            cl = (v >> 4) if (cl & 1) else (v & 0xFFF)

    out = {}
    for i in range(nroot):
        e = img[root_off + i * 32:root_off + i * 32 + 32]
        if e[0] == 0:
            break
        if e[0] == 0xE5 or (e[0x0B] & 0x18):  # deleted, volume label, dir
            continue
        name = e[:8].decode('ascii', 'replace').rstrip()
        ext = e[8:11].decode('ascii', 'replace').rstrip()
        full = name + ('.' + ext if ext else '')
        first = struct.unpack('<H', e[0x1A:0x1C])[0]
        size = struct.unpack('<I', e[0x1C:0x20])[0]
        buf = b''.join(img[data_off + (c - 2) * csize:data_off + (c - 1) * csize]
                       for c in chain(first))
        out[full.upper()] = buf[:size]
    return out


# ── Adeline LZSS ─────────────────────────────────────────────────────────────
def expand_lz(src, out_size, min_bloc=1):
    dst = bytearray()
    si = 0
    n = len(src)
    while len(dst) < out_size and si < n:
        flags = src[si]
        si += 1
        for _ in range(8):
            if len(dst) >= out_size or si >= n:
                break
            if flags & 1:
                dst.append(src[si])
                si += 1
            else:
                lo, hi = src[si], src[si + 1]
                si += 2
                off = (hi << 4) | (lo >> 4)
                length = (lo & 0x0F) + min_bloc + 1
                start = len(dst) - off - 1
                for k in range(length):
                    if len(dst) >= out_size:
                        break
                    p = start + k
                    dst.append(dst[p] if p >= 0 else 0)
            flags >>= 1
    return bytes(dst), si


# ── archive ──────────────────────────────────────────────────────────────────
def unpack_archive(data, outdir):
    _, total, nfiles = struct.unpack('<BIH', data[:7])
    table = []
    off = 7
    for _ in range(nfiles):
        e = data[off:off + 20]
        name = e[:13].rstrip(b'\0').decode('ascii')
        _attr, size, _chk = struct.unpack('<BIH', e[13:20])
        table.append((name, size))
        off += 20
    if sum(s for _, s in table) != total:
        print(f"warning: table sum {sum(s for _, s in table)} != header total {total}")

    blob = bytearray()
    p = off
    nchunks = 0
    while p + 5 <= len(data) and len(blob) < total:
        method, out_len, in_len = struct.unpack('<BHH', data[p:p + 5])
        p += 5
        if method == 0:
            chunk = data[p:p + in_len]
        elif method == 1:
            chunk, used = expand_lz(data[p:p + in_len], out_len)
            if used != in_len:
                print(f"warning: chunk {nchunks} consumed {used}/{in_len}")
        else:
            raise SystemExit(f"unknown chunk method {method} at {p - 5}")
        if len(chunk) != out_len:
            raise SystemExit(f"chunk {nchunks}: got {len(chunk)} bytes, expected {out_len}")
        blob += chunk
        p += in_len
        nchunks += 1
    if len(blob) < total:
        raise SystemExit(f"archive truncated: {len(blob)}/{total} bytes")

    os.makedirs(outdir, exist_ok=True)
    pos = 0
    for name, size in table:
        with open(os.path.join(outdir, name), 'wb') as f:
            f.write(blob[pos:pos + size])
        print(f"  {size:9d}  {name}")
        pos += size
    print(f"{nfiles} files, {total} bytes from {nchunks} chunks -> {outdir}")


def main(argv):
    if len(argv) < 3:
        print(__doc__)
        return 1
    outdir = argv[-1]
    srcs = argv[1:-1]
    parts = []
    loose = {}  # non-archive files from disk1 (INSTALL.EXE, SETUP.LST, ...)
    if len(srcs) == 1 and os.path.isdir(srcs[0]):
        imgs = sorted({os.path.normcase(f) for f in glob.glob(os.path.join(srcs[0], 'disk*.IMG')) +
                       glob.glob(os.path.join(srcs[0], 'disk*.img'))},
                      key=lambda s: int(''.join(c for c in os.path.basename(s) if c.isdigit())))
        if not imgs:
            raise SystemExit(f"no disk*.IMG in {srcs[0]}")
        for img in imgs:
            files = fat12_files(open(img, 'rb').read())
            for k, v in files.items():
                if k.startswith('LBA.') and k[4:].isdigit():
                    parts.append((int(k[4:]), v))
                else:
                    loose[k] = v
            print(f"{os.path.basename(img)}: {', '.join(sorted(files))}")
    else:
        for i, pth in enumerate(sorted(srcs)):
            parts.append((i, open(pth, 'rb').read()))
    parts.sort()
    data = b''.join(v for _, v in parts)
    print(f"archive: {len(parts)} parts, {len(data)} bytes")
    unpack_archive(data, outdir)
    for k, v in loose.items():
        with open(os.path.join(outdir, k), 'wb') as f:
            f.write(v)
        print(f"  {len(v):9d}  {k}  (disk1, uncompressed)")
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
