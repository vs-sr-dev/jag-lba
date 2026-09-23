#!/usr/bin/env python3
"""Per-cluster quantization quality evaluation.

For each cluster (K=12 default):
  1. Compute optimal 16-color sub-palette by k-means in RGB space, weighted by
     each palette index's total pixel-usage in that cluster.
  2. Quantize all cluster pixels to nearest of 16, measure RGB-distance error
     stats (mean, p90, p99, max).
  3. Render side-by-side composite (original | quantized) for 3 representative
     bricks per cluster.

Output: stats table + 12 comparison PNGs in BRK_quant/.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import lba_paths
import sys, os, struct, zlib, argparse
import numpy as np
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hqr_decompress import parse_offsets, extract_entry
from render_brick import decode_brick, write_png_rgba
from render_slide import normalize_palette
from brick_cluster import build_features, kmeans

def reduce_palette(used_colors, palette_rgb, k=16, seed=0):
    """Pick k RGB colors minimizing weighted L2 error.
    used_colors: dict {pal_idx: pixel_count}
    palette_rgb: list of 256 (R,G,B) tuples
    Returns: list of k (R,G,B) tuples (sub-palette)
    """
    pts = []
    weights = []
    for idx, cnt in used_colors.items():
        pts.append(palette_rgb[idx])
        weights.append(cnt)
    pts = np.array(pts, dtype=np.float32)
    weights = np.array(weights, dtype=np.float32)
    n = len(pts)

    if n <= k:
        return [tuple(map(int, p)) for p in pts]

    rng = np.random.default_rng(seed)
    # k-means++ init weighted
    centers = np.empty((k, 3), dtype=np.float32)
    first = rng.choice(n, p=weights/weights.sum())
    centers[0] = pts[first]
    closest_d2 = np.sum((pts - centers[0])**2, axis=1)
    for c in range(1, k):
        probs = closest_d2 * weights
        s = probs.sum()
        if s <= 0:
            centers[c] = pts[rng.integers(n)]
        else:
            centers[c] = pts[rng.choice(n, p=probs/s)]
        d2 = np.sum((pts - centers[c])**2, axis=1)
        np.minimum(closest_d2, d2, out=closest_d2)

    # Iterate weighted k-means
    for _ in range(40):
        d = -2 * pts @ centers.T + np.sum(centers**2, axis=1)
        assigns = np.argmin(d, axis=1)
        new_centers = centers.copy()
        for c in range(k):
            mask = assigns == c
            if mask.any():
                w = weights[mask]
                new_centers[c] = (pts[mask].T @ w) / w.sum()
        if np.allclose(new_centers, centers):
            break
        centers = new_centers
    return [tuple(int(round(v)) for v in c) for c in centers]

def quantize_pixel(rgb, sub_palette_arr):
    """Find nearest sub-palette entry (returns index)."""
    d = np.sum((sub_palette_arr - np.array(rgb))**2, axis=1)
    return int(np.argmin(d))

def render_quant_composite(reps, palette_rgb, sub_palette, brk_data, brk_offsets, out_path, scale=4):
    """Side-by-side: original | quantized for 3 reps stacked vertically."""
    GAP = 8
    sub_arr = np.array(sub_palette, dtype=np.float32)
    decoded = []
    for orig_idx, dx, dy, _ in reps[:3]:
        payload = extract_entry(brk_data, orig_idx, brk_offsets)
        ddx, ddy, _, _, pixels, alpha, _ = decode_brick(payload)
        decoded.append((ddx, ddy, pixels, alpha))
    if not decoded:
        return
    max_dx = max(d[0] for d in decoded) * scale
    max_dy = max(d[1] for d in decoded) * scale

    # Layout: 3 rows, 2 cols (orig | quant)
    cw = 2 * max_dx + 3 * GAP
    ch = 3 * max_dy + 4 * GAP
    rgba = bytearray(cw * ch * 4)

    for n, (dx, dy, pixels, alpha) in enumerate(decoded):
        oy = GAP + n * (max_dy + GAP)
        for variant in range(2):  # 0 = original, 1 = quantized
            ox = GAP + variant * (max_dx + GAP) + (max_dx - dx*scale) // 2
            oy_centered = oy + (max_dy - dy*scale) // 2
            for y in range(dy):
                for x in range(dx):
                    a = alpha[y][x]
                    if variant == 0:
                        r, g, b = palette_rgb[pixels[y][x]]
                    else:
                        orig_rgb = palette_rgb[pixels[y][x]]
                        q_idx = quantize_pixel(orig_rgb, sub_arr) if a else 0
                        r, g, b = sub_palette[q_idx] if a else (0, 0, 0)
                    for sy in range(scale):
                        py = oy_centered + y*scale + sy
                        for sx in range(scale):
                            px = ox + x*scale + sx
                            p = (py * cw + px) * 4
                            rgba[p]   = r
                            rgba[p+1] = g
                            rgba[p+2] = b
                            rgba[p+3] = a
    write_png_rgba(out_path, cw, ch, bytes(rgba))

def main(brk_path, ress_path, out_dir, k_clusters=12, sub_pal_size=16, seed=42):
    print(f"Loading bricks from {brk_path}...")
    with open(brk_path, 'rb') as f: brk_data = f.read()
    brk_offsets = parse_offsets(brk_data)
    meta, X = build_features(brk_data, brk_offsets)
    print(f"  {len(meta)} non-empty bricks")

    with open(ress_path, 'rb') as f: ress_data = f.read()
    ress_offsets = parse_offsets(ress_data)
    pal_raw = extract_entry(ress_data, 0, ress_offsets)
    palette_rgb = normalize_palette(pal_raw)
    palette_rgb_arr = np.array(palette_rgb, dtype=np.float32)

    print(f"K-means K={k_clusters} (seed={seed})...")
    centers, assigns = kmeans(X, k_clusters, seed=seed, verbose=False)

    os.makedirs(out_dir, exist_ok=True)

    print(f"\n=== Per-cluster quantization eval (sub-palette={sub_pal_size}) ===")
    print(f"{'C':>3} {'bricks':>6} {'pixels':>10} {'mean_e':>7} {'p50':>5} {'p90':>5} {'p99':>5} {'max':>5} {'>20':>6}")
    print("-" * 70)

    overall = []

    for c in range(k_clusters):
        members_idx = np.where(assigns == c)[0]
        if len(members_idx) == 0:
            print(f"{c:>3}  EMPTY")
            continue

        # Aggregate cluster's color usage (idx -> pixel count)
        used = Counter()
        for mi in members_idx:
            orig_idx = meta[mi][0]
            payload = extract_entry(brk_data, orig_idx, brk_offsets)
            dx, dy, _, _, pixels, alpha, _ = decode_brick(payload)
            for y in range(dy):
                for x in range(dx):
                    if alpha[y][x]:
                        used[pixels[y][x]] += 1

        # Reduce palette
        sub_palette = reduce_palette(used, palette_rgb, k=sub_pal_size, seed=seed)
        sub_arr = np.array(sub_palette, dtype=np.float32)

        # Measure error per pixel (weighted by usage)
        errors = []
        weights = []
        for idx, cnt in used.items():
            d = np.sum((sub_arr - np.array(palette_rgb[idx]))**2, axis=1)
            err = float(np.sqrt(d.min()))
            errors.append(err)
            weights.append(cnt)
        errs = np.array(errors)
        wts = np.array(weights)
        # Weighted percentiles via expansion (small enough N)
        # Sort by error, then walk weighted cumulative
        order = np.argsort(errs)
        sorted_errs = errs[order]
        sorted_wts = wts[order]
        cum = np.cumsum(sorted_wts)
        total_w = cum[-1]
        def wpct(p):
            target = p * total_w
            return float(sorted_errs[np.searchsorted(cum, target)])
        mean_e = float((errs * wts).sum() / total_w)
        p50 = wpct(0.50)
        p90 = wpct(0.90)
        p99 = wpct(0.99)
        max_e = float(sorted_errs[-1])
        # % pixels with error > 20 (clearly visible threshold in 8-bit RGB)
        gt20 = float(wts[errs > 20].sum() / total_w * 100) if (errs > 20).any() else 0.0

        print(f"{c:>3} {len(members_idx):>6} {int(total_w):>10,} {mean_e:>7.2f} {p50:>5.1f} {p90:>5.1f} {p99:>5.1f} {max_e:>5.1f} {gt20:>5.1f}%")
        overall.append((c, len(members_idx), int(total_w), mean_e, p50, p90, p99, max_e, gt20))

        # Render comparison composite
        d_to_center = np.sum((X[members_idx] - centers[c])**2, axis=1)
        order_d = np.argsort(d_to_center)
        reps = [meta[members_idx[order_d[i]]] for i in range(min(3, len(members_idx)))]
        out_png = os.path.join(out_dir, f'quant_c{c:02d}_n{len(members_idx):04d}.png')
        render_quant_composite(reps, palette_rgb, sub_palette, brk_data, brk_offsets, out_png)

    # Overall weighted mean
    print("-" * 70)
    total_pixels = sum(o[2] for o in overall)
    weighted_mean = sum(o[2] * o[3] for o in overall) / total_pixels if total_pixels else 0
    print(f"Total opaque pixels: {total_pixels:,}")
    print(f"Pixel-weighted mean RGB error across all clusters: {weighted_mean:.2f}")
    print(f"\nOutputs in {out_dir}/quant_cNN_*.png (left=original, right=quantized)")

if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--brk', default=lba_paths.common('LBA_BRK.HQR'))
    ap.add_argument('--ress', default=lba_paths.common('RESS.HQR'))
    ap.add_argument('--out', default=lba_paths.repo(r'extracted\BRK_quant'))
    ap.add_argument('-k', type=int, default=12, help='number of clusters')
    ap.add_argument('--pal', type=int, default=16, help='colors per cluster sub-palette')
    ap.add_argument('--seed', type=int, default=42)
    args = ap.parse_args()
    main(args.brk, args.ress, args.out, args.k, args.pal, args.seed)
