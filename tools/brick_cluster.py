#!/usr/bin/env python3
"""K-means cluster bricks by normalized color histogram to discover visual zones.

Pipeline:
  1. Decode all bricks; per-brick build L1-normalized 256-dim histogram of palette indices
     used by opaque pixels (transparent ignored — they don't define visual identity).
  2. K-means++ init then standard k-means with K clusters.
  3. Per cluster: stats (size, top-16/32 color coverage, top dims) + 3x3 composite PNG
     of the 9 representative bricks closest to the cluster center.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import lba_paths
import sys, os, struct, zlib, argparse
from collections import Counter
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hqr_decompress import parse_offsets, extract_entry
from render_brick import decode_brick, write_png_rgba
from render_slide import normalize_palette

def build_features(brk_data, brk_offsets):
    """Returns list of (orig_idx, dx, dy, opaque) and matching (N, 256) float32 array."""
    rows = []
    meta = []
    for i in range(len(brk_offsets)):
        payload = extract_entry(brk_data, i, brk_offsets)
        if payload is None:
            continue
        dx, dy, _, _, pixels, alpha, _ = decode_brick(payload)
        hist = np.zeros(256, dtype=np.float32)
        opaque = 0
        for y in range(dy):
            for x in range(dx):
                if alpha[y][x]:
                    hist[pixels[y][x]] += 1
                    opaque += 1
        if opaque == 0:
            continue
        hist /= opaque  # L1 normalize
        rows.append(hist)
        meta.append((i, dx, dy, opaque))
    return meta, np.stack(rows)

def kmeans_pp_init(X, k, rng):
    n = X.shape[0]
    centers = np.empty((k, X.shape[1]), dtype=np.float32)
    centers[0] = X[rng.integers(n)]
    closest_d2 = np.sum((X - centers[0])**2, axis=1)
    for c in range(1, k):
        total = closest_d2.sum()
        if total <= 0:
            centers[c] = X[rng.integers(n)]
        else:
            probs = closest_d2 / total
            idx = rng.choice(n, p=probs)
            centers[c] = X[idx]
        d2 = np.sum((X - centers[c])**2, axis=1)
        np.minimum(closest_d2, d2, out=closest_d2)
    return centers

def kmeans(X, k, max_iter=100, seed=42, verbose=True):
    rng = np.random.default_rng(seed)
    centers = kmeans_pp_init(X, k, rng)
    assigns = np.zeros(X.shape[0], dtype=np.int32)
    for it in range(max_iter):
        # Assign: ||x||² + ||c||² - 2 x.c, but ||x||² constant for argmin -> drop it
        d = -2 * X @ centers.T + np.sum(centers**2, axis=1)
        new_assigns = np.argmin(d, axis=1)
        changed = int(np.sum(new_assigns != assigns))
        assigns = new_assigns
        # Update
        for c in range(k):
            mask = assigns == c
            if mask.any():
                centers[c] = X[mask].mean(axis=0)
        if verbose:
            print(f"  iter {it+1}: {changed} reassignments", file=sys.stderr)
        if changed == 0:
            break
    return centers, assigns

def render_composite(reps, palette, brk_data, brk_offsets, out_path, scale=4):
    GAP = 8
    COLS = 3
    rows = (len(reps) + COLS - 1) // COLS
    decoded = []
    for orig_idx, dx, dy, _ in reps:
        payload = extract_entry(brk_data, orig_idx, brk_offsets)
        ddx, ddy, _, _, pixels, alpha, _ = decode_brick(payload)
        decoded.append((ddx, ddy, pixels, alpha))
    max_dx = max(d[0] for d in decoded) * scale
    max_dy = max(d[1] for d in decoded) * scale
    cw = COLS * max_dx + (COLS+1) * GAP
    ch = rows * max_dy + (rows+1) * GAP
    rgba = bytearray(cw * ch * 4)
    for n, (dx, dy, pixels, alpha) in enumerate(decoded):
        col = n % COLS
        row = n // COLS
        ox = GAP + col * (max_dx + GAP) + (max_dx - dx*scale) // 2
        oy = GAP + row * (max_dy + GAP) + (max_dy - dy*scale) // 2
        for y in range(dy):
            for x in range(dx):
                r, g, b = palette[pixels[y][x]]
                a = alpha[y][x]
                for sy in range(scale):
                    py = oy + y*scale + sy
                    for sx in range(scale):
                        px = ox + x*scale + sx
                        p = (py * cw + px) * 4
                        rgba[p]   = r
                        rgba[p+1] = g
                        rgba[p+2] = b
                        rgba[p+3] = a
    write_png_rgba(out_path, cw, ch, bytes(rgba))

def main(brk_path, ress_path, out_dir, k, seed):
    print(f"Loading bricks from {brk_path}...")
    with open(brk_path, 'rb') as f: brk_data = f.read()
    brk_offsets = parse_offsets(brk_data)
    meta, X = build_features(brk_data, brk_offsets)
    print(f"  {len(meta)} non-empty bricks, feature matrix {X.shape}")

    print(f"\nK-means K={k}, seed={seed}...")
    centers, assigns = kmeans(X, k, seed=seed)

    with open(ress_path, 'rb') as f: ress_data = f.read()
    ress_offsets = parse_offsets(ress_data)
    pal_raw = extract_entry(ress_data, 0, ress_offsets)
    palette = normalize_palette(pal_raw)

    os.makedirs(out_dir, exist_ok=True)

    print(f"\n=== Cluster summary (K={k}) ===")
    for c in range(k):
        members_idx = np.where(assigns == c)[0]
        n_mem = len(members_idx)
        if n_mem == 0:
            print(f"\nCluster {c:02d}: EMPTY")
            continue
        # Aggregate histogram (mean of members)
        agg = X[members_idx].mean(axis=0)
        ranked = np.argsort(-agg)
        top16_cov = float(agg[ranked[:16]].sum())
        top32_cov = float(agg[ranked[:32]].sum())
        # Distance of each member to center
        d = np.sum((X[members_idx] - centers[c])**2, axis=1)
        order = np.argsort(d)
        # Top dims
        dim_cnt = Counter((meta[members_idx[i]][1], meta[members_idx[i]][2]) for i in range(n_mem))
        top_dims = ', '.join(f"{dx}x{dy}({n})" for (dx,dy), n in dim_cnt.most_common(4))
        # Top palette indices
        top8_pal = ' '.join(f"{ranked[j]}({100*agg[ranked[j]]:.1f}%)" for j in range(8))
        print(f"\nCluster {c:02d}: {n_mem:>5} bricks ({100*n_mem/len(meta):>5.1f}%)")
        print(f"  Top-16 coverage: {100*top16_cov:>5.1f}%   Top-32: {100*top32_cov:>5.1f}%")
        print(f"  Top dims: {top_dims}")
        print(f"  Top-8 palette idx (% pixels): {top8_pal}")
        # Render representative composite
        n_rep = min(9, n_mem)
        reps = [meta[members_idx[order[i]]] for i in range(n_rep)]
        out_png = os.path.join(out_dir, f'cluster_{c:02d}_n{n_mem:04d}.png')
        render_composite(reps, palette, brk_data, brk_offsets, out_png)
        print(f"  -> {out_png}")

if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--brk', default=lba_paths.common('LBA_BRK.HQR'))
    ap.add_argument('--ress', default=lba_paths.common('RESS.HQR'))
    ap.add_argument('--out', default=lba_paths.repo(r'extracted\BRK_clusters'))
    ap.add_argument('-k', type=int, default=12)
    ap.add_argument('--seed', type=int, default=42)
    args = ap.parse_args()
    main(args.brk, args.ress, args.out, args.k, args.seed)
