"""Find nuclei in a video and follow each one from frame to frame.

Saves output/tracks.csv with columns cell_id, frame, x, y.
"""

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from cellpose import models
from trackastra.model import Trackastra
from trackastra.tracking.utils import graph_to_napari_tracks

# Running this file as `python backend/track.py` puts backend/ on the path.
from video import load_video

# Project root is the folder that contains backend/ and output/.
ROOT = Path(__file__).resolve().parent.parent
TRACKS_PATH = ROOT / "output" / "tracks.csv"


def center_crop(video, size):
    """Keep a square from the middle of every frame. size is in pixels."""
    if size is None:
        return video

    _, height, width = video.shape
    size = min(int(size), height, width)
    top = (height - size) // 2
    left = (width - size) // 2
    return video[:, top : top + size, left : left + size]


def segment_nuclei(video):
    """Label nuclei in each frame with the pretrained Cellpose model.

    Returns an integer array of the same shape as video. 0 is background.
    Each frame is numbered on its own; Trackastra links those numbers later.
    """
    model = models.CellposeModel(gpu=True)
    # A list of 2D images makes Cellpose segment one frame at a time.
    frames = [frame for frame in video]
    masks, _flows, _styles = model.eval(frames)
    return np.stack(masks).astype(np.int32)


def link_tracks(video, masks):
    """Link nuclei across frames with Trackastra's general_2d model.

    Returns a table with columns cell_id, frame, x, y.
    Coordinates are the nucleus center, in pixels. x is horizontal.
    """
    model = Trackastra.from_pretrained("general_2d")
    track_graph, _tracked_masks = model.track(video, masks, mode="greedy")

    # Each row is cell_id, frame, y, x. Trackastra stores y before x.
    rows, _graph, _props = graph_to_napari_tracks(track_graph)
    if len(rows) == 0:
        return pd.DataFrame(columns=["cell_id", "frame", "x", "y"])

    table = pd.DataFrame(rows, columns=["cell_id", "frame", "y", "x"])
    table = table[["cell_id", "frame", "x", "y"]]
    table["cell_id"] = table["cell_id"].astype(int)
    table["frame"] = table["frame"].astype(int)
    table["x"] = table["x"].astype(float)
    table["y"] = table["y"].astype(float)
    return table.sort_values(["cell_id", "frame"]).reset_index(drop=True)


def save_tracks(table):
    """Write tracks.csv using the column order in README.md."""
    TRACKS_PATH.parent.mkdir(parents=True, exist_ok=True)
    table.to_csv(TRACKS_PATH, index=False)


def report(table):
    """Print how many cells were followed and how long a typical track is."""
    if table.empty:
        print("0 cells tracked, median track length 0 frames.")
        print(f"Saved {TRACKS_PATH}")
        return

    lengths = table.groupby("cell_id").size()
    cell_count = int(len(lengths))
    median_length = float(lengths.median())
    per_frame = table.groupby("frame").size()
    print(
        f"{cell_count} cells tracked, median track length {median_length:g} frames."
    )
    print(
        f"Cells per frame: min {int(per_frame.min())}, max {int(per_frame.max())}."
    )
    print(f"Saved {TRACKS_PATH}")


def main():
    parser = argparse.ArgumentParser(
        description="Segment nuclei and save output/tracks.csv."
    )
    parser.add_argument(
        "path",
        help="MP4 file or a folder of TIFF frames, for example data/hela_short.mp4",
    )
    parser.add_argument(
        "--frames",
        type=int,
        default=None,
        help="Use only the first N frames (handy for a quick test)",
    )
    parser.add_argument(
        "--crop",
        type=int,
        default=None,
        help="Center-crop every frame to this many pixels (handy for a quick test)",
    )
    args = parser.parse_args()

    if args.frames is not None and args.frames < 1:
        print("--frames must be at least 1")
        sys.exit(1)
    if args.crop is not None and args.crop < 1:
        print("--crop must be at least 1")
        sys.exit(1)

    video = load_video(args.path)
    if args.frames is not None:
        video = video[: args.frames]
    video = center_crop(video, args.crop)

    frame_count, height, width = video.shape
    print(f"Tracking {frame_count} frames, {height}×{width}.")

    masks = segment_nuclei(video)
    table = link_tracks(video, masks)
    save_tracks(table)
    report(table)


if __name__ == "__main__":
    main()
