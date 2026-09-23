#!/usr/bin/env python3
"""Parse SCENE 0 (Citadel) hero init values from SCENE.HQR.

Reads the LBA1 scene struct stream-style and prints every field. Layout
follows TwinE / OpenLBA reverse-engineered scene loader for LBA1.
"""
import sys, os, struct
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hqr_decompress import parse_offsets, extract_entry, hexdump

SCENE_HQR = r'd:\Homebrew5\JagLBA\extracted\SCENE.HQR'

with open(SCENE_HQR, 'rb') as f:
    data = f.read()
offs = parse_offsets(data)
print(f'SCENE.HQR: {len(offs)} entries')

p = extract_entry(data, 0, offs)
print(f'Scene 0 size: {len(p)} bytes')

class R:
    def __init__(self, b):
        self.b = b
        self.p = 0
    def u8(self):
        v = self.b[self.p]; self.p += 1; return v
    def s8(self):
        v = self.b[self.p]; self.p += 1
        return v - 256 if v >= 128 else v
    def u16(self):
        v = self.b[self.p] | (self.b[self.p+1] << 8); self.p += 2; return v
    def s16(self):
        v = self.b[self.p] | (self.b[self.p+1] << 8); self.p += 2
        return v - 65536 if v >= 0x8000 else v

r = R(p)
print('\n--- HEADER ---')
textBank = r.u8(); print(f' [0x{r.p-1:02X}] textBank        = {textBank}')
gameOverScene = r.u8(); print(f' [0x{r.p-1:02X}] gameOverScene   = {gameOverScene}')
# 4 unknown bytes (or maybe 2 u16). Print raw.
unk = [r.u8() for _ in range(4)]
print(f' [0x{r.p-4:02X}..] unknown[4]      = {unk}')

alphaLight = r.s16(); print(f' [0x{r.p-2:02X}] alphaLight      = {alphaLight}')
betaLight  = r.s16(); print(f' [0x{r.p-2:02X}] betaLight       = {betaLight}')

print('\n--- AMBIENCE TABLE (4 entries) ---')
for i in range(4):
    a = r.s16(); rep = r.s16(); rnd = r.s16()
    print(f' [0x{r.p-6:02X}] sample[{i}]: id={a:5d} repeat={rep:3d} round={rnd:5d}')

minDelay = r.u16(); print(f'\n [0x{r.p-2:02X}] sampleMinDelay     = {minDelay}')
minDelayRnd = r.u16(); print(f' [0x{r.p-2:02X}] sampleMinDelayRnd  = {minDelayRnd}')

sceneMusic = r.u8(); print(f' [0x{r.p-1:02X}] sceneMusic         = {sceneMusic}')

print(f'\n--- HERO INITIAL STATE (offset 0x{r.p:02X}) ---')
hx = r.s16(); print(f' [0x{r.p-2:02X}] heroX (world)   = {hx:6d}  (=0x{hx & 0xFFFF:04X})')
hy = r.s16(); print(f' [0x{r.p-2:02X}] heroY (world)   = {hy:6d}  (=0x{hy & 0xFFFF:04X})')
hz = r.s16(); print(f' [0x{r.p-2:02X}] heroZ (world)   = {hz:6d}  (=0x{hz & 0xFFFF:04X})')

print(f'\n  Cell coords (world / 512):')
print(f'    gx = {hx/512:6.3f}  -> cell {hx >> 9}')
print(f'    gy = {hy/512:6.3f}  -> cell {hy >> 9}')
print(f'    gz = {hz/512:6.3f}  -> cell {hz >> 9}')

# Heroic actor record continues. The structure for actor 0:
# u8 sceneHeroBody  (model index)
# u8 sceneHeroAnim
# u16 brickShape  (?)
# u16 sceneHeroOrient (angle/orientation)
# Print next bytes raw for inspection
print(f'\nRaw next 64 bytes from cursor 0x{r.p:02X}:')
print(hexdump(p[r.p:r.p+64], width=16))

# Compare with scene 1, 2, 6 hero positions to validate offsets
print('\n--- SANITY: hero pos in scenes 0..7 (using same offsets) ---')
def parse_hero(scene_idx):
    pp = extract_entry(data, scene_idx, offs)
    rr = R(pp)
    rr.u8(); rr.u8()          # textBank, gameOverScene
    for _ in range(4): rr.u8() # unknown 4 bytes
    rr.s16(); rr.s16()        # alpha/beta light
    for _ in range(4):
        rr.s16(); rr.s16(); rr.s16()  # 4 ambience entries
    rr.u16(); rr.u16()        # min delays
    rr.u8()                    # music
    return rr.s16(), rr.s16(), rr.s16(), rr.p

for i in range(8):
    hx,hy,hz,off_h = parse_hero(i)
    print(f' scene {i}: HERO ({hx:5d},{hy:5d},{hz:5d})  cell=({hx>>9},{hy>>9},{hz>>9})  hero_off=0x{off_h-6:02X}')
