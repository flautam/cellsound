"""Find nuclei in a video and follow each one from frame to frame.

Saves output/tracks.csv with columns cell_id, frame, x, y.
"""

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import torch
from cellpose import models
from trackastra.model import Trackastra
from trackastra.tracking.utils import graph_to_napari_tracks

# `python backend/track.py` puts backend/ on the path. The website imports this
# file as part of the backend package, so both import styles are accepted.
try:
    from video import load_video
except ModuleNotFoundError:
    from backend.video import load_video

# Project root is the folder that contains backend/ and output/.
ROOT = Path(__file__).resolve().parent.parent
TRACKS_PATH = ROOT / "output" / "tracks.csv"

# Cellpose 4.2 ships cpsam, cpsam_v2, cpdino, and cpdino-vitb.
# cpsam / cpsam_v2 / cpdino are ~1.15 GB. cpdino-vitb is ~328 MB and needs
# dinov3 (`pip install git+https://github.com/facebookresearch/dinov3`).
CELLPOSE_MODEL = "cpdino-vitb"

# A CPU box with 8 GB of RAM cannot segment a whole movie.
CPU_FRAMES = 10
CPU_CROP = 256
CPU_BATCH_SIZE = 1


def choose_device():
    """Pick CUDA when it exists, otherwise the CPU. Prints the choice."""
    if torch.cuda.is_available():
        device = torch.device("cuda")
    elif torch.backends.mps.is_available():
        device = torch.device("mps")
    else:
        device = torch.device("cpu")
    print(f"Using device: {device.type}")
    return device


def limit_for_cpu(device_type, frames, crop):
    """On CPU, use a short center crop when the user did not pass flags.

    Returns frames, crop, and a note to print. The note is None when the
    user already chose --frames and --crop, or when the device is not CPU.
    """
    if device_type == "cpu" and frames is None and crop is None:
        return (
            CPU_FRAMES,
            CPU_CROP,
            f"CPU default: first {CPU_FRAMES} frames and a {CPU_CROP} center crop.",
        )
    return frames, crop, None


def center_crop(video, size):
    """Keep a square from the middle of every frame. size is in pixels."""
    if size is None:
        return video

    _, height, width = video.shape
    size = min(int(size), height, width)
    top = (height - size) // 2
    left = (width - size) // 2
    return video[:, top : top + size, left : left + size]


def select_frames(video, frames, crop):
    """Keep the requested frames and crop, then drop the rest of the movie."""
    if frames is not None:
        video = video[:frames]
    video = center_crop(video, crop)
    # A slice still points at the full movie. Copy just this region so the
    # rest can be freed before segmentation.
    return np.ascontiguousarray(video)


def segment_nuclei(video, device, on_frame=None):
    """Label nuclei in each frame with the pretrained Cellpose model.

    Returns an integer array of the same shape as video. 0 is background.
    Each frame is numbered on its own; Trackastra links those numbers later.
    on_frame(done, total) is optional and is called after each frame.
    """
    model = models.CellposeModel(device=device, pretrained_model=CELLPOSE_MODEL)
    # One tile at a time on CPU. A GPU can take the library's usual batch.
    batch_size = CPU_BATCH_SIZE if device.type == "cpu" else 8
    masks = np.empty(video.shape, dtype=np.int32)
    total = int(video.shape[0])
    for index in range(total):
        # One frame, not a list of the whole video.
        mask, _flows, _styles = model.eval(video[index], batch_size=batch_size)
        masks[index] = mask
        if on_frame is not None:
            on_frame(index + 1, total)
    return masks


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
        help="Use only the first N frames. On CPU, the default is 10.",
    )
    parser.add_argument(
        "--crop",
        type=int,
        default=None,
        help="Center-crop every frame to this many pixels. On CPU, the default is 256.",
    )
    args = parser.parse_args()

    if args.frames is not None and args.frames < 1:
        print("--frames must be at least 1")
        sys.exit(1)
    if args.crop is not None and args.crop < 1:
        print("--crop must be at least 1")
        sys.exit(1)

    device = choose_device()
    frames, crop, note = limit_for_cpu(device.type, args.frames, args.crop)
    if note:
        print(note)

    video = select_frames(load_video(args.path), frames, crop)
    frame_count, height, width = video.shape
    print(f"Tracking {frame_count} frames, {height}×{width}.")

    masks = segment_nuclei(video, device)
    table = link_tracks(video, masks)
    save_tracks(table)
    report(table)


if __name__ == "__main__":
    main()
