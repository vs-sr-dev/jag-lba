#!/usr/bin/env python3
"""Build scene_init_inventory.csv with hero pos + orientation + body_idx for all scenes.

Layout (verified S20 + reference_lba_scene_init.md):
  0x00 u8  textBank
  0x01 u8  gameOverScene
  0x02 4 unknown bytes
  0x06 s16 alphaLight
  0x08 s16 betaLight
  0x0A 4 ambience entries (6 bytes each, s16 id + s16 rep + s16 rnd) = 24 bytes
  0x22 u16 sampleMinDelay
  0x24 u16 sampleMinDelayRnd
  0x26 u8  sceneMusic
  0x27 s16 heroX  (LE inside HQR)
  0x29 s16 heroY
  0x2B s16 heroZ
  0x2D u16 first_npc_body_LE  (validated S21 — body of first NPC actor in the scene,
                                NOT of Twinsen. Confirmed: scene 0 = body 29 (uomo
                                d'affari visitor in ospedale-prigione), scene 7 (casa
                                apartment) = body 1 (NPC ricorrente comune, 38 scene).
                                Twinsen body itself is set by game script via dialog/
                                cutscene triggers — NOT a per-scene field. For JagLBA,
                                wire Twinsen body switch via ANIM_STATE flag in game
                                logic, not via SCENE.HQR data.)
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import lba_paths
import os, sys, csv
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hqr_decompress import parse_offsets, extract_entry

SCENE_HQR = lba_paths.repo(r'extracted\SCENE.HQR')
OUT_CSV   = lba_paths.repo(r'extracted\scene_init_inventory.csv')

def s16(b, p):
    v = b[p] | (b[p+1] << 8)
    return v - 65536 if v >= 0x8000 else v

def parse_scene(payload):
    if len(payload) < 0x2F:
        return None
    music = payload[0x26]
    hx = s16(payload, 0x27)
    hy = s16(payload, 0x29)
    hz = s16(payload, 0x2B)
    body_idx = payload[0x2D] | (payload[0x2E] << 8)
    return music, hx, hy, hz, body_idx

def main():
    with open(SCENE_HQR, 'rb') as f:
        data = f.read()
    offs = parse_offsets(data)
    print(f'SCENE.HQR: {len(offs)} entries')

    rows = []
    for i in range(len(offs)):
        p = extract_entry(data, i, offs)
        if p is None:
            continue
        parsed = parse_scene(p)
        if parsed is None:
            print(f'  scene {i}: too small ({len(p)} bytes), skipped')
            continue
        music, hx, hy, hz, body_idx = parsed
        gx, gy, gz = hx >> 9, hy >> 9, hz >> 9
        rows.append((i, hx, hy, hz, gx, gy, gz, music, body_idx))

    with open(OUT_CSV, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['scene','hero_x','hero_y','hero_z','gx','gy','gz','music','first_npc_body'])
        w.writerows(rows)
    print(f'Wrote {len(rows)} rows to {OUT_CSV}')

if __name__ == '__main__':
    main()
