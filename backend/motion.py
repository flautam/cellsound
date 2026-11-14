"""Turn tracks.csv into motion.csv (velocities + distance to POA).

Reads output/tracks.csv (cell_id, frame, x, y) and writes output/motion.csv with:
  cell_id, frame, x, y, vx, vy, speed, direction_deg, dist_poa, radial_v

Units are pixels and pixels/frame. Optional --um-per-px / --fps only affect
printed summaries, not the CSV columns.

POA (point of attraction) defaults to the image center. Pass --poa X Y to
override. Image size for the default center comes from --video, or
--width/--height, or else the track bounding box.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
TRACKS_PATH = ROOT / "output" / "tracks.csv"
MOTION_PATH = ROOT / "output" / "motion.csv"

# From bar15 overlay: 100 µm bar ≈ 77 px on the original (and mid23 crop).
DEFAULT_UM_PER_PX = 100.0 / 77.0

MOTION_COLUMNS = [
    "cell_id",
    "frame",
    "x",
    "y",
    "vx",
    "vy",
    "speed",
    "direction_deg",
    "dist_poa",
    "radial_v",
]


def resolve_image_size(
    tracks: pd.DataFrame,
    video: Path | None,
    width: int | None,
    height: int | None,
) -> tuple[float, float]:
    """Return (width, height) used to place a default center POA."""
    if width is not None and height is not None:
        if width < 1 or height < 1:
            raise ValueError("--width and --height must be at least 1")
        return float(width), float(height)

    if video is not None:
        # Prefer OpenCV so we do not decode the whole movie.
        try:
            import cv2

            cap = cv2.VideoCapture(str(video))
            w = float(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            h = float(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            cap.release()
            if w >= 1 and h >= 1:
                return w, h
        except Exception:
            pass
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from video import load_video

        frame = load_video(video, max_frames=1)[0]
        return float(frame.shape[1]), float(frame.shape[0])

    # Fallback: slightly past the farthest tracked point.
    return float(np.ceil(tracks["x"].max() + 1)), float(np.ceil(tracks["y"].max() + 1))


def resolve_poa(
    tracks: pd.DataFrame,
    poa: tuple[float, float] | None,
    video: Path | None,
    width: int | None,
    height: int | None,
) -> tuple[float, float, str]:
    """Return (poa_x, poa_y, note). Default POA is image center."""
    if poa is not None:
        return float(poa[0]), float(poa[1]), "POA from --poa"

    w, h = resolve_image_size(tracks, video, width, height)
    return w / 2.0, h / 2.0, f"POA = image center ({w:g}×{h:g})"


def compute_motion(tracks: pd.DataFrame, poa_x: float, poa_y: float) -> pd.DataFrame:
    """Add step velocities and POA features. Gaps / first hits get NaN velocities."""
    required = {"cell_id", "frame", "x", "y"}
    missing = required - set(tracks.columns)
    if missing:
        raise ValueError(f"tracks table missing columns: {sorted(missing)}")

    rows: list[dict] = []
    for cell_id, group in tracks.groupby("cell_id", sort=True):
        g = group.sort_values("frame")
        xs = g["x"].to_numpy(dtype=float)
        ys = g["y"].to_numpy(dtype=float)
        frames = g["frame"].to_numpy(dtype=int)

        for i in range(len(g)):
            x, y, frame = xs[i], ys[i], int(frames[i])
            dx = x - poa_x
            dy = y - poa_y
            dist = float(np.hypot(dx, dy))

            vx = vy = speed = direction = radial = np.nan
            if i > 0 and frames[i] - frames[i - 1] == 1:
                vx = float(xs[i] - xs[i - 1])
                vy = float(ys[i] - ys[i - 1])
                speed = float(np.hypot(vx, vy))
                direction = float(np.degrees(np.atan2(vy, vx)))
                if dist > 1e-9:
                    # Outward radial speed; negative means toward the POA.
                    radial = float((vx * dx + vy * dy) / dist)

            rows.append(
                {
                    "cell_id": int(cell_id),
                    "frame": frame,
                    "x": x,
                    "y": y,
                    "vx": vx,
                    "vy": vy,
                    "speed": speed,
                    "direction_deg": direction,
                    "dist_poa": dist,
                    "radial_v": radial,
                }
            )

    return pd.DataFrame(rows, columns=MOTION_COLUMNS)


def save_motion(table: pd.DataFrame, path: Path = MOTION_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    table.to_csv(path, index=False)
    return path


def report(table: pd.DataFrame, um_per_px: float, fps: float | None) -> None:
    """Print a short summary of speeds and radial motion."""
    steps = table.dropna(subset=["speed"])
    if steps.empty:
        print("0 motion steps (need consecutive frames per cell).")
        return

    mean_speed = float(steps["speed"].mean())
    mean_radial = float(steps["radial_v"].mean())
    print(
        f"{int(table['cell_id'].nunique())} cells, {len(steps)} motion steps; "
        f"mean speed {mean_speed:.3f} px/frame, mean radial_v {mean_radial:.3f} px/frame "
        f"(negative = toward POA)."
    )
    if um_per_px > 0:
        um_s = mean_speed * um_per_px * (fps if fps and fps > 0 else 1.0)
        unit = "µm/s" if fps and fps > 0 else "µm/frame"
        print(f"Mean speed ≈ {um_s:.2f} {unit} using {um_per_px:.4f} µm/px.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compute velocities and POA features from tracks.csv."
    )
    parser.add_argument(
        "--tracks",
        type=Path,
        default=TRACKS_PATH,
        help=f"Input tracks CSV (default: {TRACKS_PATH})",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=MOTION_PATH,
        help=f"Output motion CSV (default: {MOTION_PATH})",
    )
    parser.add_argument(
        "--poa",
        nargs=2,
        type=float,
        metavar=("X", "Y"),
        default=None,
        help="Point of attraction in pixels. Default: image center.",
    )
    parser.add_argument(
        "--video",
        type=Path,
        default=None,
        help="Video used to get width/height for the default center POA.",
    )
    parser.add_argument("--width", type=int, default=None, help="Image width in pixels.")
    parser.add_argument("--height", type=int, default=None, help="Image height in pixels.")
    parser.add_argument(
        "--um-per-px",
        type=float,
        default=DEFAULT_UM_PER_PX,
        help=f"µm per pixel for printed summary (default: {DEFAULT_UM_PER_PX:.6g}).",
    )
    parser.add_argument(
        "--fps",
        type=float,
        default=15.0,
        help="Frames per second for printed µm/s summary (default: 15).",
    )
    args = parser.parse_args()

    if not args.tracks.exists():
        print(f"Could not find {args.tracks}")
        sys.exit(1)
    if (args.width is None) ^ (args.height is None):
        print("Pass both --width and --height, or neither.")
        sys.exit(1)
    if args.video is not None and not args.video.exists():
        print(f"Could not find {args.video}")
        sys.exit(1)

    tracks = pd.read_csv(args.tracks)
    if tracks.empty:
        print(f"No rows in {args.tracks}")
        sys.exit(1)

    poa = tuple(args.poa) if args.poa is not None else None
    try:
        poa_x, poa_y, note = resolve_poa(
            tracks, poa, args.video, args.width, args.height
        )
    except ValueError as exc:
        print(exc)
        sys.exit(1)

    print(note)
    print(f"POA = ({poa_x:.3f}, {poa_y:.3f})")

    table = compute_motion(tracks, poa_x, poa_y)
    out = save_motion(table, args.out)
    report(table, args.um_per_px, args.fps)
    print(f"Saved {out}")


if __name__ == "__main__":
    main()
