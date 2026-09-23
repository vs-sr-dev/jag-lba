#!/usr/bin/env python3
"""Extract SCENE.HQR (and optionally other files) from FAT12 disk images.

Strategy:
  1) Parse FAT12 boot sector.
  2) Walk root dir to find SCENE.HQR. If missing, try subdirs (LBA/ etc).
  3) Follow FAT chain (with multi-disk continuation: when last cluster is
     end-of-chain, file is complete; if not found completely on disk1, we
     try concatenating subsequent disks since LBA1 spans multiple floppies).

LBA1 install set typically uses Stac/installer; on the *floppy* disks the
files are usually plain (DOS install kit). If SCENE.HQR straddles disks
we glue the cluster runs from each disk in order.

Usage:
  python extract_scene_hqr.py
"""
import os, sys, struct, glob

DISK_DIR = r'd:\Homebrew5\JagLBA\000644_little_big_adventure'
OUT_DIR  = r'd:\Homebrew5\JagLBA\extracted'

def parse_boot(boot):
    bps   = int.from_bytes(boot[11:13], 'little')
    spc   = boot[13]
    rsvd  = int.from_bytes(boot[14:16], 'little')
    nfats = boot[16]
    rootN = int.from_bytes(boot[17:19], 'little')
    totS  = int.from_bytes(boot[19:21], 'little')
    media = boot[21]
    spF   = int.from_bytes(boot[22:24], 'little')
    return dict(bps=bps, spc=spc, rsvd=rsvd, nfats=nfats, rootN=rootN,
                totS=totS, media=media, spF=spF)

def fat12_get(fat, n):
    off = (n * 3) // 2
    val = fat[off] | (fat[off+1] << 8)
    if n & 1:
        return val >> 4
    return val & 0xFFF

def read_dir_entries(data, off, count):
    entries = []
    for i in range(count):
        e = data[off + i*32 : off + (i+1)*32]
        if e[0] == 0x00:
            break
        if e[0] == 0xE5:
            continue
        attr = e[11]
        if attr == 0x0F:
            continue  # LFN
        name = e[0:8].decode('ascii', errors='replace').rstrip()
        ext  = e[8:11].decode('ascii', errors='replace').rstrip()
        cluster = int.from_bytes(e[26:28], 'little')
        size = int.from_bytes(e[28:32], 'little')
        full = name + ('.' + ext if ext else '')
        entries.append(dict(name=full, raw=e, attr=attr, cluster=cluster, size=size))
    return entries

def parse_fs(path):
    with open(path, 'rb') as f:
        data = f.read()
    bp = parse_boot(data[:512])
    bps = bp['bps']; spc = bp['spc']
    fat_off = bp['rsvd'] * bps
    fat_size = bp['spF'] * bps
    fat = data[fat_off : fat_off + fat_size]
    root_off = fat_off + bp['nfats'] * fat_size
    root_size = bp['rootN'] * 32
    root = read_dir_entries(data, root_off, bp['rootN'])
    data_off = root_off + root_size
    return data, bp, fat, root, data_off

def cluster_offset(bp, data_off, cluster):
    return data_off + (cluster - 2) * bp['spc'] * bp['bps']

def read_file(data, bp, fat, data_off, start_cluster, size):
    out = bytearray()
    c = start_cluster
    csize = bp['spc'] * bp['bps']
    while c < 0xFF8 and c >= 2:
        off = cluster_offset(bp, data_off, c)
        out.extend(data[off : off + csize])
        nxt = fat12_get(fat, c)
        if nxt >= 0xFF8 or nxt == 0:
            break
        c = nxt
    return bytes(out[:size]) if size else bytes(out)

def list_disk(disk_path):
    print(f'\n=== {os.path.basename(disk_path)} ===')
    try:
        data, bp, fat, root, data_off = parse_fs(disk_path)
    except Exception as e:
        print(f'  parse error: {e}')
        return None
    print(f'  bps={bp["bps"]} spc={bp["spc"]} rsvd={bp["rsvd"]} fats={bp["nfats"]} '
          f'root={bp["rootN"]} totS={bp["totS"]} spF={bp["spF"]}')
    for e in root:
        flag = 'D' if (e['attr'] & 0x10) else 'F'
        print(f'  [{flag}] {e["name"]:<14} cluster={e["cluster"]:5d} size={e["size"]}')
    return data, bp, fat, root, data_off

def find_in_root(root, target):
    target = target.upper()
    for e in root:
        if e['name'].upper() == target:
            return e
    return None

def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    disks = sorted(glob.glob(os.path.join(DISK_DIR, 'disk*.IMG')))
    print(f'Found {len(disks)} disk images')
    located = []  # (disk_idx, entry, bp, data, fat, data_off)
    for i, d in enumerate(disks, 1):
        res = list_disk(d)
        if not res:
            continue
        data, bp, fat, root, data_off = res
        for e in root:
            if 'SCENE' in e['name'].upper():
                located.append((i, d, e, bp, data, fat, data_off))

    print('\n=== SCENE-related entries found ===')
    for L in located:
        i, d, e, *_ = L
        print(f'  disk{i}  {e["name"]}  cluster={e["cluster"]} size={e["size"]}')

    if not located:
        print('No SCENE.HQR found in root directories. Will need raw scan.')
        return

    # Read each candidate's full chain from its disk; if the FAT chain ends
    # before size is satisfied, concatenate clusters from successive disks
    # (LBA1 floppy install splits big files across disks at cluster boundary).
    for (i, d, e, bp, data, fat, data_off) in located:
        size = e['size']
        out_name = e['name']
        out_path = os.path.join(OUT_DIR, out_name)
        # walk chain on this disk
        out = bytearray()
        c = e['cluster']
        csize = bp['spc'] * bp['bps']
        chain_ok = True
        while c >= 2 and c < 0xFF8 and len(out) < size:
            off = cluster_offset(bp, data_off, c)
            out.extend(data[off : off + csize])
            nxt = fat12_get(fat, c)
            if nxt >= 0xFF8 or nxt == 0:
                break
            c = nxt
        gathered = len(out)
        print(f'\nFile {out_name} disk{i}: gathered {gathered}/{size} from chain.')
        if gathered < size:
            # Continue from disk i+1, ..., reading FROM cluster 2 onward in
            # data area, until size satisfied
            for j in range(i + 1, len(disks) + 1):
                disk_j = os.path.join(DISK_DIR, f'disk{j}.IMG')
                if not os.path.exists(disk_j):
                    break
                data2, bp2, fat2, root2, data_off2 = parse_fs(disk_j)
                # If file present in root of disk_j with same name, use chain from there
                e2 = find_in_root(root2, out_name)
                if e2:
                    c2 = e2['cluster']
                    csize2 = bp2['spc'] * bp2['bps']
                    while c2 >= 2 and c2 < 0xFF8 and len(out) < size:
                        off2 = cluster_offset(bp2, data_off2, c2)
                        out.extend(data2[off2 : off2 + csize2])
                        nxt = fat12_get(fat2, c2)
                        if nxt >= 0xFF8 or nxt == 0:
                            break
                        c2 = nxt
                    print(f'  appended from disk{j} (root entry): now {len(out)}/{size}')
                else:
                    # fallback: dump raw data area linearly
                    csize2 = bp2['spc'] * bp2['bps']
                    nclust = (bp2['totS'] - (data_off2 // bp2['bps'])) // bp2['spc']
                    cc = 2
                    while cc < nclust + 2 and len(out) < size:
                        off2 = cluster_offset(bp2, data_off2, cc)
                        out.extend(data2[off2 : off2 + csize2])
                        cc += 1
                    print(f'  appended raw from disk{j} (linear): now {len(out)}/{size}')
                if len(out) >= size:
                    break
        out = bytes(out[:size])
        with open(out_path, 'wb') as fo:
            fo.write(out)
        print(f'  WROTE {out_path}  ({len(out)} bytes)')

if __name__ == '__main__':
    main()
