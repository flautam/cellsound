"""Load an MP4 or a folder of TIFF frames as a grayscale video."""

import sys
from pathlib import Path

import imageio.v3 as iio
import numpy as np

# File types we treat as a movie. Anything else that is a folder is TIFF frames.
MOVIE_SUFFIXES = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
TIFF_SUFFIXES = {".tif", ".tiff"}


def load_video(path, max_frames=None):
    """Read a video into a numpy array shaped (frames, height, width).

    path can be an MP4 (or similar movie) or a folder of TIFF frames.
    Color frames are converted to grayscale.
    If max_frames is set, only the first N frames are decoded (saves RAM).
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Could not find {path}")

    if path.is_dir():
        frames = _read_tiff_folder(path, max_frames=max_frames)
    else:
        frames = _read_movie(path, max_frames=max_frames)

    return _to_grayscale(frames)


def _read_movie(path, max_frames=None):
    """Read a movie file; optionally only the first max_frames frames."""
    # imageio returns (frames, height, width) or (frames, height, width, channels).
    # Prefer streaming when capped — ffmpeg plugin rejects index=range(...).
    if max_frames is None:
        return np.asarray(iio.imread(path))
    frames = []
    for i, frame in enumerate(iio.imiter(path)):
        if i >= int(max_frames):
            break
        frames.append(np.asarray(frame))
    if not frames:
        raise ValueError(f"No frames read from {path}")
    return np.stack(frames)


def _read_tiff_folder(path, max_frames=None):
    """Read TIFF files in a folder, in filename order, and stack them."""
    files = sorted(
        item
        for item in path.iterdir()
        if item.is_file() and item.suffix.lower() in TIFF_SUFFIXES
    )
    if not files:
        raise FileNotFoundError(f"No TIFF frames in {path}")
    if max_frames is not None:
        files = files[: int(max_frames)]
    return np.stack([np.asarray(iio.imread(item)) for item in files])


def _to_grayscale(frames):
    """Turn one frame or a stack into a float grayscale stack."""
    frames = np.asarray(frames)

    # A single gray frame is (height, width). Give it a frame axis.
    if frames.ndim == 2:
        frames = frames[np.newaxis, ...]

    # A color stack is (frames, height, width, channels).
    if frames.ndim == 4:
        channels = frames[..., :3].astype(np.float32)
        # Standard brightness weights for red, green, and blue.
        gray = (
            0.2989 * channels[..., 0]
            + 0.5870 * channels[..., 1]
            + 0.1140 * channels[..., 2]
        )
        return gray

    if frames.ndim != 3:
        raise ValueError(
            f"Expected a video of shape (frames, height, width), got {frames.shape}"
        )

    return frames.astype(np.float32)


def main():
    if len(sys.argv) != 2:
        print("Usage: python backend/video.py path/to/video.mp4")
        print("       python backend/video.py path/to/tiff_folder")
        sys.exit(1)

    video = load_video(sys.argv[1])
    frame_count, height, width = video.shape
    # The multiplication sign is the one the project guide uses in its example.
    print(f"{frame_count} frames, {height}×{width}.")


if __name__ == "__main__":
    main()
