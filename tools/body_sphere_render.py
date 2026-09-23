#!/usr/bin/env python3
"""S15 A2: render body 0 polygons + overlay E_SPHERE disks numbered.

Reuses body_gouraud_render.render() pipeline for the 224 polys, then on top
draws the 5 spheres as filled disks with palette coul1 + a red number label.

Output: build/body000_with_spheres.png — visual reference for S15 V0.
"""

import os, sys, math
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PIL import Image, ImageDraw
from body_gouraud_render import render, WIDTH, HEIGHT
from body_sphere_dump import parse_body_full, proj_iso, iso_screen_radius
from hqr_decompress import parse_offsets, extract_entry


def overlay_spheres(png_path, body_payload, xc, yc, out_path):
    info = parse_body_full(body_payload)
    spheres = info['spheres']

    img = Image.open(png_path).convert('RGB')
    draw = ImageDraw.Draw(img)

    # Use a fixed reference palette (RESS_PAL slot 0); we already rendered with it.
    # For sphere overlay, we look up coul1 in the saved-image palette by sampling
    # a representative pixel — too complex; instead use a heuristic: dark for
    # small coul1 (<32), skin tone for ~40-60, etc. A faithful palette lookup is
    # not necessary for identification — what matters is the position+size+label.
    # Use a simple deterministic color per coul1.
    def fake_color(c1):
        # Spread c1 across hue space deterministically
        import colorsys
        h = (c1 * 0.0314) % 1.0
        r, g, b = colorsys.hsv_to_rgb(h, 0.7, 0.85)
        return (int(r*255), int(g*255), int(b*255))

    for i, sp in enumerate(spheres):
        v = info['verts'][sp['v_idx']]
        sx, sy = proj_iso(v[0], v[1], v[2], xc, yc)
        rs = iso_screen_radius(sp['rayon'])
        rs = max(1, rs)
        col = fake_color(sp['coul1'])
        # filled disk
        draw.ellipse([sx-rs, sy-rs, sx+rs, sy+rs], fill=col, outline=(255, 0, 0))
        # number label (top-right of sphere)
        label = str(i)
        draw.text((sx+rs+1, sy-rs-1), label, fill=(255, 0, 0))

    img.save(out_path)
    print(f"Wrote: {out_path}")
    print(f"Sphere centers (yellow=skin, magenta=hair-area, etc):")
    for i, sp in enumerate(spheres):
        v = info['verts'][sp['v_idx']]
        sx, sy = proj_iso(v[0], v[1], v[2], xc, yc)
        rs = iso_screen_radius(sp['rayon'])
        print(f"  Sphere {i}: c1={sp['coul1']:>3}, rs={rs}, screen=({sx},{sy}), v=({v[0]},{v[1]},{v[2]})")


def main():
    BRK = r'I:\GOG\Little Big Adventure\Common\BODY.HQR'
    BODY_IDX = 0
    XC, YC = 160, 100

    out_dir = 'build'
    os.makedirs(out_dir, exist_ok=True)
    base_png = os.path.join(out_dir, f'body{BODY_IDX:03d}_gouraud_cosine.png')

    # Re-render base if missing
    if not os.path.exists(base_png):
        print(f"Rendering base body PNG: {base_png}")
        light = (0.5774, 0.5774, 0.5774)
        render(BRK, BODY_IDX, base_png, light, 'cosine', XC, YC, do_cull=True)

    # Load body
    with open(BRK, 'rb') as f:
        data = f.read()
    offsets = parse_offsets(data)
    payload = extract_entry(data, BODY_IDX, offsets)

    out = os.path.join(out_dir, f'body{BODY_IDX:03d}_with_spheres.png')
    overlay_spheres(base_png, payload, XC, YC, out)

    # 4x zoom of relevant area
    img = Image.open(out)
    crop = img.crop((130, 60, 200, 130))  # 70x70 around twinsen
    big = crop.resize((280, 280), Image.NEAREST)
    out_big = os.path.join(out_dir, f'body{BODY_IDX:03d}_with_spheres_zoom.png')
    big.save(out_big)
    print(f"Wrote zoom: {out_big}")


if __name__ == '__main__':
    main()
