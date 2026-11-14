"""Cluster cells per frame, then write cluster CSVs + an XY plot.

Clusters are **spatial neighborhoods**:
  - position (x, y) dominates
  - each cluster has a max radius (no huge sprawling blobs)
  - centroids stay >= 2R apart so radius-balls do not overlap
  - sticky membership (hard to switch)
  - centroid follows prior members' motion; new joiners pull at low weight (0.10)
  - at most K cluster ids (no easy births)

Writes:
  output/clusters.csv
  output/cluster_summary.csv
  output/clusters_xy.png
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.cluster import KMeans

ROOT = Path(__file__).resolve().parent.parent
MOTION_PATH = ROOT / "output" / "motion.csv"
CLUSTERS_PATH = ROOT / "output" / "clusters.csv"
SUMMARY_PATH = ROOT / "output" / "cluster_summary.csv"
XY_PATH = ROOT / "output" / "clusters_xy.png"

FEATURE_COLS = ["x", "y", "vx", "vy", "dist_poa", "radial_v"]

DEFAULT_K = 8
DEFAULT_MIN_SIZE = 3
DEFAULT_MAX_RADIUS = 28.0  # pixels — caps cluster area / non-overlap balls
DEFAULT_STICKINESS = 2.5
DEFAULT_CENTROID_EMA = 0.15
DEFAULT_JOINER_WEIGHT = 0.10  # vs 1.0 for continuing members
DEFAULT_MEMBER_PULL = 0.30  # blend toward weighted member mean each frame


def load_motion_rows(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    missing = set(FEATURE_COLS) - set(df.columns)
    if missing:
        raise ValueError(f"motion CSV missing columns: {sorted(missing)}")
    usable = df.dropna(subset=FEATURE_COLS).copy()
    usable["cell_id"] = usable["cell_id"].astype(int)
    usable["frame"] = usable["frame"].astype(int)
    return usable.reset_index(drop=True)


def _separate_centroids(
    xy: np.ndarray,
    min_dist: float,
    iters: int = 80,
    max_step: float | None = None,
) -> np.ndarray:
    """Push centroids apart so pairwise distance >= min_dist (non-overlapping balls).

    If max_step is set, each centroid may move at most that many pixels total
    (soft maintenance so centers don't teleport after init).
    """
    c = xy.copy().astype(float)
    start = c.copy()
    k = len(c)
    if k <= 1 or min_dist <= 0:
        return c
    for _ in range(iters):
        moved = False
        for i in range(k):
            for j in range(i + 1, k):
                d = c[j] - c[i]
                dist = float(np.hypot(d[0], d[1]))
                if dist < 1e-6:
                    angle = (i * 2.1 + j) % (2 * np.pi)
                    nudge = np.array([np.cos(angle), np.sin(angle)]) * 0.5
                    c[j] += nudge
                    moved = True
                    continue
                if dist < min_dist:
                    push = 0.5 * (min_dist - dist + 1e-3) * d / dist
                    if max_step is not None:
                        push_len = float(np.hypot(push[0], push[1]))
                        if push_len > max_step:
                            push = push * (max_step / push_len)
                    c[i] -= push
                    c[j] += push
                    moved = True
        if not moved:
            break
    if max_step is not None:
        for i in range(k):
            delta = c[i] - start[i]
            dist = float(np.hypot(delta[0], delta[1]))
            if dist > max_step:
                c[i] = start[i] + delta * (max_step / dist)
    return c


def _spatial_assign(
    xy: np.ndarray,
    centroids: np.ndarray,
    prev_labels: np.ndarray | None,
    max_radius: float,
    stickiness: float,
) -> np.ndarray:
    """Assign each point to a centroid; sticky + prefer staying inside max_radius."""
    n, k = len(xy), len(centroids)
    d = np.linalg.norm(xy[:, None, :] - centroids[None, :, :], axis=2)
    nearest = np.argmin(d, axis=1)
    labels = nearest.copy()

    for i in range(n):
        prev = int(prev_labels[i]) if prev_labels is not None else -1
        if 0 <= prev < k and d[i, prev] <= max_radius:
            in_radius = np.where(d[i] <= max_radius)[0]
            if len(in_radius) == 0:
                labels[i] = prev
                continue
            best = int(in_radius[np.argmin(d[i, in_radius])])
            if best == prev or d[i, best] * stickiness >= d[i, prev]:
                labels[i] = prev
            else:
                labels[i] = best
        else:
            in_radius = np.where(d[i] <= max_radius)[0]
            if len(in_radius):
                labels[i] = int(in_radius[np.argmin(d[i, in_radius])])
            else:
                labels[i] = int(nearest[i])
    return labels.astype(int)


def _peel_to_radius(
    xy: np.ndarray,
    labels: np.ndarray,
    centroids: np.ndarray,
    max_radius: float,
) -> np.ndarray:
    """Prefer in-ball membership; else nearest centroid."""
    labels = labels.copy()
    d = np.linalg.norm(xy[:, None, :] - centroids[None, :, :], axis=2)
    for i in range(len(xy)):
        in_radius = np.where(d[i] <= max_radius)[0]
        if len(in_radius):
            labels[i] = int(in_radius[np.argmin(d[i, in_radius])])
        else:
            labels[i] = int(np.argmin(d[i]))
    return labels


def _merge_small_labels(
    xy: np.ndarray,
    labels: np.ndarray,
    centroids: np.ndarray,
    min_size: int,
) -> np.ndarray:
    """Reassign tiny clusters to nearest other centroid; do NOT move centroids."""
    labels = labels.copy()
    while True:
        ids, counts = np.unique(labels, return_counts=True)
        if len(ids) <= 1:
            break
        small = [int(i) for i, c in zip(ids, counts) if c < min_size]
        if not small:
            break
        large = [int(i) for i, c in zip(ids, counts) if c >= min_size]
        if not large:
            keep = int(ids[np.argmax(counts)])
            labels[:] = keep
            break
        for s in small:
            target = min(
                large,
                key=lambda L: float(np.hypot(*(centroids[s] - centroids[L]))),
            )
            labels[labels == s] = target
    return labels


def _move_centroids_with_prior_members(
    centroids: np.ndarray,
    cell_ids: np.ndarray,
    xy: np.ndarray,
    cell_prev_label: dict[int, int],
    cell_prev_xy: dict[int, np.ndarray],
    cell_prev_frame: dict[int, int],
    prev_frame: int | None,
) -> np.ndarray:
    """Move each centroid by mean displacement of cells that were in it last frame.

    Only cells observed on the immediately previous frame count — no stale gaps.
    If no prior members appear, stay put (joiners handled separately).
    """
    new_c = centroids.copy()
    if prev_frame is None:
        return new_c
    for i in range(len(centroids)):
        deltas: list[np.ndarray] = []
        for cid, pos in zip(cell_ids, xy):
            cid_i = int(cid)
            if cell_prev_label.get(cid_i, -1) != i:
                continue
            if cell_prev_frame.get(cid_i) != prev_frame:
                continue
            prev_xy = cell_prev_xy.get(cid_i)
            if prev_xy is None:
                continue
            deltas.append(pos - prev_xy)
        if deltas:
            new_c[i] = centroids[i] + np.mean(deltas, axis=0)
    return new_c


def _pull_centroids_weighted(
    centroids: np.ndarray,
    labels: np.ndarray,
    cell_ids: np.ndarray,
    xy: np.ndarray,
    cell_prev_label: dict[int, int],
    joiner_weight: float = DEFAULT_JOINER_WEIGHT,
    pull: float = DEFAULT_MEMBER_PULL,
) -> np.ndarray:
    """Blend each centroid toward a weighted member mean.

    Continuing members weight 1.0; new joiners use joiner_weight (e.g. 0.05).
    """
    new_c = centroids.copy()
    if pull <= 0:
        return new_c
    for i in range(len(centroids)):
        mask = labels == i
        if not np.any(mask):
            continue
        pts = xy[mask]
        ids = cell_ids[mask]
        weights = np.array(
            [
                1.0 if cell_prev_label.get(int(cid), -1) == i else joiner_weight
                for cid in ids
            ],
            dtype=float,
        )
        wsum = float(weights.sum())
        if wsum <= 0:
            continue
        target = (pts * weights[:, None]).sum(axis=0) / wsum
        new_c[i] = (1.0 - pull) * centroids[i] + pull * target
    return new_c


def run_clustering(
    motion: pd.DataFrame,
    k: int,
    min_size: int,
    max_radius: float = DEFAULT_MAX_RADIUS,
    stickiness: float = DEFAULT_STICKINESS,
    centroid_ema: float = DEFAULT_CENTROID_EMA,
    joiner_weight: float = DEFAULT_JOINER_WEIGHT,
    member_pull: float = DEFAULT_MEMBER_PULL,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Fixed-K spatial clusters with max radius and non-overlapping balls."""
    del centroid_ema  # kept in signature for CLI compatibility; unused
    cluster_rows: list[dict] = []
    summary_rows: list[dict] = []

    centroids: np.ndarray | None = None  # (k, 2) in pixel x,y
    cell_prev_label: dict[int, int] = {}
    cell_prev_xy: dict[int, np.ndarray] = {}
    cell_prev_frame: dict[int, int] = {}
    prev_frame: int | None = None
    min_cent_dist = 2.0 * max_radius  # hard non-overlap of radius balls

    for frame in sorted(motion["frame"].unique()):
        frame_df = motion[motion["frame"] == frame].reset_index(drop=True)
        n = len(frame_df)
        if n == 0:
            continue

        xy = frame_df[["x", "y"]].to_numpy(dtype=float)
        cell_ids = frame_df["cell_id"].to_numpy(dtype=int)
        # Keep K fixed after init so centroid index slots never remapped.
        k_eff = k if centroids is not None else max(1, min(k, n // max(min_size, 1), n))

        if centroids is None:
            if k_eff == 1:
                labels = np.zeros(n, dtype=int)
                centroids = xy.mean(axis=0, keepdims=True)
            else:
                km = KMeans(n_clusters=k_eff, n_init=10, random_state=0)
                labels = km.fit_predict(xy)
                centroids = np.stack(
                    [
                        xy[labels == i].mean(axis=0)
                        if np.any(labels == i)
                        else xy.mean(axis=0)
                        for i in range(k_eff)
                    ]
                )
            centroids = _separate_centroids(centroids, min_cent_dist)
            labels = _spatial_assign(xy, centroids, None, max_radius, stickiness)
            labels = _peel_to_radius(xy, labels, centroids, max_radius)
            labels = _merge_small_labels(xy, labels, centroids, min_size)
            # Freeze first-frame centers after separation — no yank from membership.
            centroids = _separate_centroids(centroids, min_cent_dist)
            labels = _peel_to_radius(xy, labels, centroids, max_radius)
            labels = _merge_small_labels(xy, labels, centroids, min_size)
        else:
            # Move centers only with prior members' motion, then soft-separate.
            centroids = _move_centroids_with_prior_members(
                centroids,
                cell_ids,
                xy,
                cell_prev_label,
                cell_prev_xy,
                cell_prev_frame,
                prev_frame,
            )
            centroids = _separate_centroids(
                centroids, min_cent_dist, iters=20, max_step=1.5
            )

            prev = np.array(
                [cell_prev_label.get(int(c), -1) for c in cell_ids], dtype=int
            )
            prev = np.where(prev >= len(centroids), -1, prev)
            labels = _spatial_assign(xy, centroids, prev, max_radius, stickiness)
            labels = _peel_to_radius(xy, labels, centroids, max_radius)
            labels = _merge_small_labels(xy, labels, centroids, min_size)
            # Soft pull toward members; joiners worth joiner_weight of a full cell
            centroids = _pull_centroids_weighted(
                centroids,
                labels,
                cell_ids,
                xy,
                cell_prev_label,
                joiner_weight=joiner_weight,
                pull=member_pull,
            )
            centroids = _separate_centroids(
                centroids, min_cent_dist, iters=10, max_step=1.0
            )

        for i in range(n):
            lab = int(labels[i])
            cid = int(cell_ids[i])
            cell_prev_label[cid] = lab
            cell_prev_xy[cid] = xy[i].copy()
            cell_prev_frame[cid] = int(frame)
            cluster_rows.append(
                {
                    "frame": int(frame),
                    "cell_id": cid,
                    "cluster_id": lab + 1,
                }
            )
        prev_frame = int(frame)

        stable = labels + 1
        for lab in sorted(np.unique(stable)):
            part = frame_df.loc[stable == lab]
            cx, cy = float(centroids[int(lab) - 1][0]), float(centroids[int(lab) - 1][1])
            summary_rows.append(
                {
                    "frame": int(frame),
                    "cluster_id": int(lab),
                    "n_cells": int(len(part)),
                    "centroid_x": cx,
                    "centroid_y": cy,
                    "mean_x": float(part["x"].mean()),
                    "mean_y": float(part["y"].mean()),
                    "mean_vx": float(part["vx"].mean()),
                    "mean_vy": float(part["vy"].mean()),
                    "mean_speed": float(part["speed"].mean()),
                    "mean_radial_v": float(part["radial_v"].mean()),
                }
            )

    clusters = pd.DataFrame(cluster_rows, columns=["frame", "cell_id", "cluster_id"])
    summary = pd.DataFrame(
        summary_rows,
        columns=[
            "frame",
            "cluster_id",
            "n_cells",
            "centroid_x",
            "centroid_y",
            "mean_x",
            "mean_y",
            "mean_vx",
            "mean_vy",
            "mean_speed",
            "mean_radial_v",
        ],
    )
    return clusters, summary


def plot_clusters_xy(
    motion: pd.DataFrame,
    clusters: pd.DataFrame,
    summary: pd.DataFrame,
    out_path: Path,
    max_radius: float,
    title: str | None = None,
) -> Path:
    """Last-frame circles at algorithm centroids + centroid trails."""
    merged = motion.merge(clusters, on=["frame", "cell_id"], how="inner")
    ids = sorted(merged["cluster_id"].unique())
    cmap = plt.colormaps["tab10"].resampled(max(len(ids), 10))
    color_of = {cid: cmap(i % 10) for i, cid in enumerate(ids)}

    fig, ax = plt.subplots(figsize=(8, 8), dpi=160)
    last_frame = int(merged["frame"].max())
    fdf = merged[merged["frame"] == last_frame]
    sdf = summary[summary["frame"] == last_frame]

    for cid, g in fdf.groupby("cluster_id"):
        ax.scatter(
            g["x"], g["y"], s=14, color=color_of[cid], alpha=0.9,
            label=f"cluster {cid}", zorder=3,
        )

    for _, row in sdf.iterrows():
        cid = int(row["cluster_id"])
        cx, cy = float(row["centroid_x"]), float(row["centroid_y"])
        circ = plt.Circle(
            (cx, cy), max_radius, fill=False, color=color_of[cid], lw=2.0, alpha=0.95
        )
        ax.add_patch(circ)

    for cid, g in summary.sort_values("frame").groupby("cluster_id"):
        ax.plot(
            g["centroid_x"], g["centroid_y"], "-o", color=color_of[cid],
            lw=2.0, ms=3.0, alpha=0.95, zorder=4,
        )
        ax.plot(
            g["centroid_x"].iloc[-1], g["centroid_y"].iloc[-1],
            "s", color=color_of[cid], ms=7, zorder=5,
        )

    ax.set_aspect("equal", adjustable="box")
    ax.invert_yaxis()
    ax.set_xlabel("x (px)")
    ax.set_ylabel("y (px)")
    ax.set_title(title or f"Clusters XY — max_radius={max_radius:g}px")
    ax.grid(True, alpha=0.25, lw=0.5)
    ax.legend(loc="upper right", fontsize=8, framealpha=0.9)
    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path)
    plt.close(fig)
    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Spatial sticky clustering from motion.csv.")
    parser.add_argument("--motion", type=Path, default=MOTION_PATH)
    parser.add_argument("--clusters-out", type=Path, default=CLUSTERS_PATH)
    parser.add_argument("--summary-out", type=Path, default=SUMMARY_PATH)
    parser.add_argument("--xy-out", type=Path, default=XY_PATH)
    parser.add_argument("--k", type=int, default=DEFAULT_K)
    parser.add_argument("--min-size", type=int, default=DEFAULT_MIN_SIZE)
    parser.add_argument(
        "--max-radius",
        type=float,
        default=DEFAULT_MAX_RADIUS,
        help="Max cluster radius in pixels. Balls do not overlap.",
    )
    parser.add_argument("--stickiness", type=float, default=DEFAULT_STICKINESS)
    parser.add_argument("--title", type=str, default=None)
    args = parser.parse_args()

    if args.k < 1 or args.min_size < 1 or args.max_radius <= 0 or args.stickiness < 1:
        print("Invalid --k / --min-size / --max-radius / --stickiness")
        sys.exit(1)
    if not args.motion.exists():
        print(f"Could not find {args.motion}")
        sys.exit(1)

    motion = load_motion_rows(args.motion)
    if motion.empty:
        print("No motion rows with full features to cluster.")
        sys.exit(1)

    print(
        f"Clustering {len(motion)} cell-frames, {motion['frame'].nunique()} frames, "
        f"K={args.k}, min_size={args.min_size}, max_radius={args.max_radius}, "
        f"stickiness={args.stickiness}."
    )
    clusters, summary = run_clustering(
        motion,
        k=args.k,
        min_size=args.min_size,
        max_radius=args.max_radius,
        stickiness=args.stickiness,
    )

    args.clusters_out.parent.mkdir(parents=True, exist_ok=True)
    clusters.to_csv(args.clusters_out, index=False)
    summary.to_csv(args.summary_out, index=False)
    xy = plot_clusters_xy(
        motion, clusters, summary, args.xy_out,
        max_radius=args.max_radius, title=args.title,
    )

    # Diagnostics vs algorithm centroids
    merged = motion.merge(clusters, on=["frame", "cell_id"])
    radii = []
    for (fr, cid), g in merged.groupby(["frame", "cluster_id"]):
        row = summary[(summary.frame == fr) & (summary.cluster_id == cid)]
        if row.empty:
            continue
        cx, cy = float(row.centroid_x.iloc[0]), float(row.centroid_y.iloc[0])
        r = float(np.hypot(g["x"] - cx, g["y"] - cy).max())
        radii.append(r)

    min_pair = None
    for fr, g in summary.groupby("frame"):
        pts = g[["centroid_x", "centroid_y"]].to_numpy()
        ids = g["cluster_id"].to_numpy()
        for i in range(len(pts)):
            for j in range(i + 1, len(pts)):
                d = float(np.hypot(*(pts[i] - pts[j])))
                if min_pair is None or d < min_pair[0]:
                    min_pair = (d, fr, int(ids[i]), int(ids[j]))

    switches = opportunities = 0
    for _, g in clusters.sort_values("frame").groupby("cell_id"):
        labs = g["cluster_id"].to_numpy()
        if len(labs) < 2:
            continue
        opportunities += len(labs) - 1
        switches += int(np.sum(labs[1:] != labs[:-1]))
    rate = switches / opportunities if opportunities else 0.0

    print(
        f"{clusters['cluster_id'].nunique()} cluster ids; "
        f"max dist-to-centroid = {max(radii) if radii else 0:.1f}px "
        f"(cap {args.max_radius}); "
        f"min centroid gap = {min_pair[0]:.1f}px "
        f"(need >= {2 * args.max_radius:.1f}); "
        f"switch rate = {rate:.1%}."
    )
    print(f"Saved {args.clusters_out}")
    print(f"Saved {args.summary_out}")
    print(f"Saved {xy}")


if __name__ == "__main__":
    main()
