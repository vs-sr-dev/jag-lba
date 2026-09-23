#!/usr/bin/env python3
"""JagLBA S51 - the OTHER target, for build_v6.bat's sixth step.

    alt_target.py OUT.s    writes src/main_v6.s with TARGET_HW flipped
    alt_target.py --hw     prints the flipped value

src/main_v6.s stays the only owner of TARGET_HW (cart_build.py reads it to
name the main ROM). This writes a copy for the other target, so every build
leaves both ROMs: jaglba_v6_ferro.j64 for the hardware, jaglba_v6_emu.j64 for
BigPEmu. The copy's includes are relative to the project root ("src/..."),
which is where build_v6.bat runs rmac from.
"""
import os, re, sys

REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
SRC = os.path.join(REPO, 'src', 'main_v6.s')
PAT = re.compile(r'^(TARGET_HW\s+EQU\s+)(\d+)', re.M)

def main():
    with open(SRC, encoding='utf-8', newline='') as f:
        text = f.read()
    m = PAT.search(text)
    if not m:
        raise SystemExit('alt_target: TARGET_HW not found in src/main_v6.s')
    alt = 0 if int(m.group(2)) else 1
    if sys.argv[1:] == ['--hw']:
        print(alt)
        return
    out = sys.argv[1]
    with open(out, 'w', encoding='utf-8', newline='') as f:
        f.write(PAT.sub(lambda mm: mm.group(1) + str(alt), text, count=1))

if __name__ == '__main__':
    main()
