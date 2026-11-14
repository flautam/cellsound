#!/usr/bin/env python3
"""Plot tracks.csv as a 2D XY trajectory grid and save output/tracks_xy.png."""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CSV = ROOT / "output" / "tracks.csv"
DEFAULT_PNG = ROOT / "output" / "tracks_xy.png"


def plot_tracks_xy(csv_path: Path, out_path: Path, title: str | None = None) -> Path:
    df = pd.read_csv(csv_path)
    if df.empty:
        raise SystemExit(f"No tracks in {csv_path}")

    required = {"cell_id", "frame", "x", "y"}
    missing = required - set(df.columns)
    if missing:
        raise SystemExit(f"Missing columns {sorted(missing)} in {csv_path}")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(8, 8), dpi=160)

    # Stable colors per cell_id
    ids = sorted(df["cell_id"].unique())
    n = max(len(ids), 1)
    cmap = plt.colormaps["turbo"].resampled(n)
    for i, cid in enumerate(ids):
        tr = df[df["cell_id"] == cid].sort_values("frame")
        color = cmap(i % n)
        ax.plot(tr["x"], tr["y"], "-", lw=0.9, alpha=0.85, color=color)
        ax.plot(tr["x"].iloc[0], tr["y"].iloc[0], "o", ms=2.2, color=color, alpha=0.9)
        ax.plot(tr["x"].iloc[-1], tr["y"].iloc[-1], "s", ms=2.0, color=color, alpha=0.9)

    ax.set_aspect("equal", adjustable="box")
    ax.invert_yaxis()  # image coords: y down
    ax.set_xlabel("x (px)")
    ax.set_ylabel("y (px)")
    n_cells = df["cell_id"].nunique()
    n_frames = int(df["frame"].max()) + 1 if len(df) else 0
    ax.set_title(title or f"Tracks XY — {n_cells} cells, {n_frames} frames")
    ax.grid(True, alpha=0.25, lw=0.5)
    fig.tight_layout()
    fig.savefig(out_path)
    plt.close(fig)
    return out_path


def main() -> None:
    p = argparse.ArgumentParser(description="Plot tracks.csv as 2D XY trajectories.")
    p.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    p.add_argument("--out", type=Path, default=DEFAULT_PNG)
    p.add_argument("--title", type=str, default=None)
    args = p.parse_args()
    out = plot_tracks_xy(args.csv, args.out, title=args.title)
    print(f"Saved {out}")


if __name__ == "__main__":
    main()
