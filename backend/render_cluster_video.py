"""Overlay fixed-radius cluster circles + centroid trails on a video."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2
import imageio.v3 as iio
import numpy as np
import pandas as pd

from backend.cluster import DEFAULT_MAX_RADIUS

ROOT = Path(__file__).resolve().parent.parent

# Distinct BGR colors for OpenCV drawing (stable by cluster_id).
COLORS_BGR = [
    (255, 128, 0),    # blue-ish
    (0, 140, 255),    # orange
    (0, 200, 0),      # green
    (0, 0, 255),      # red
    (200, 0, 200),    # purple
    (0, 100, 180),    # brown
    (180, 100, 255),  # pink
    (160, 160, 160),  # gray
    (0, 180, 180),    # olive/cyan
    (255, 255, 0),    # cyan-yellow
]


def color_for(cluster_id: int) -> tuple[int, int, int]:
    return COLORS_BGR[(int(cluster_id) - 1) % len(COLORS_BGR)]


def render(
    video_path: Path,
    motion_path: Path,
    clusters_path: Path,
    summary_path: Path,
    out_path: Path,
    max_radius: float = DEFAULT_MAX_RADIUS,
    fps: float | None = None,
) -> Path:
    motion = pd.read_csv(motion_path)
    clusters = pd.read_csv(clusters_path)
    summary = pd.read_csv(summary_path)
    merged = motion.merge(clusters, on=["frame", "cell_id"], how="inner")

    frames_rgb = []
    for fr in iio.imiter(video_path):
        frames_rgb.append(np.asarray(fr))
    if not frames_rgb:
        raise ValueError(f"No frames in {video_path}")

    h, w = frames_rgb[0].shape[:2]
    out_fps = float(fps) if fps and float(fps) > 0 else 0.0
    if out_fps <= 0:
        try:
            meta_fps = iio.improps(video_path).fps
            if meta_fps:
                out_fps = float(meta_fps)
        except Exception:
            out_fps = 0.0
    if out_fps <= 0:
        out_fps = 15.0
    fps = out_fps
    radius = int(round(float(max_radius)))

    cx_col = "centroid_x" if "centroid_x" in summary.columns else "mean_x"
    cy_col = "centroid_y" if "centroid_y" in summary.columns else "mean_y"

    # Centroid history: cluster_id -> list of (frame, x, y)
    trails: dict[int, list[tuple[int, float, float]]] = {}
    for _, row in summary.sort_values(["cluster_id", "frame"]).iterrows():
        cid = int(row["cluster_id"])
        trails.setdefault(cid, []).append(
            (int(row["frame"]), float(row[cx_col]), float(row[cy_col]))
        )

    out_frames = []
    for f_idx, rgb in enumerate(frames_rgb):
        bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        fdf = merged[merged["frame"] == f_idx]
        sdf = summary[summary["frame"] == f_idx]

        # Same max-radius balls as the clustering algorithm / XY plot.
        for _, row in sdf.iterrows():
            cid = int(row["cluster_id"])
            color = color_for(cid)
            cx, cy = float(row[cx_col]), float(row[cy_col])
            center = (int(round(cx)), int(round(cy)))
            overlay = bgr.copy()
            cv2.circle(overlay, center, radius, color, -1, lineType=cv2.LINE_AA)
            cv2.addWeighted(overlay, 0.10, bgr, 0.90, 0, bgr)
            cv2.circle(bgr, center, radius, color, 2, lineType=cv2.LINE_AA)

        for cid, g in fdf.groupby("cluster_id"):
            color = color_for(int(cid))
            for x, y in g[["x", "y"]].to_numpy(dtype=float):
                cv2.circle(bgr, (int(round(x)), int(round(y))), 2, color, -1)

        # Centroid trails up to this frame
        for cid, hist in trails.items():
            pts = [(x, y) for fr, x, y in hist if fr <= f_idx]
            if len(pts) < 1:
                continue
            color = color_for(cid)
            for i in range(1, len(pts)):
                p0 = (int(round(pts[i - 1][0])), int(round(pts[i - 1][1])))
                p1 = (int(round(pts[i][0])), int(round(pts[i][1])))
                cv2.line(bgr, p0, p1, color, 2, lineType=cv2.LINE_AA)
            cx, cy = pts[-1]
            cv2.circle(bgr, (int(round(cx)), int(round(cy))), 5, color, -1)
            cv2.circle(bgr, (int(round(cx)), int(round(cy))), 5, (255, 255, 255), 1)
            cv2.putText(
                bgr,
                f"C{cid}",
                (int(round(cx)) + 6, int(round(cy)) - 6),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.35,
                color,
                1,
                cv2.LINE_AA,
            )

        cv2.putText(
            bgr,
            f"frame {f_idx}",
            (4, h - 6),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.4,
            (255, 255, 255),
            1,
            cv2.LINE_AA,
        )
        out_frames.append(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Prefer H.264 (avc1) so the website <video> player can decode it.
    # Fall back to mp4v for local QuickTime if avc1 is unavailable.
    tmp = out_path.with_suffix(".tmp.mp4")
    if tmp.exists():
        tmp.unlink()
    writer = None
    for tag in ("avc1", "H264", "mp4v"):
        candidate = cv2.VideoWriter(
            str(tmp),
            cv2.VideoWriter_fourcc(*tag),
            float(fps),
            (w, h),
        )
        if candidate.isOpened():
            writer = candidate
            break
        candidate.release()
    if writer is None:
        raise RuntimeError(f"Could not open VideoWriter for {tmp}")
    for rgb in out_frames:
        writer.write(cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
    writer.release()
    tmp.replace(out_path)
    # Still of the last overlay frame — same circles as the video, for the chunk map.
    map_path = out_path.with_name("clusters_map.png")
    iio.imwrite(map_path, out_frames[-1])
    return out_path


def main() -> None:
    p = argparse.ArgumentParser(description="Render fixed-radius cluster circle video.")
    p.add_argument(
        "--video",
        type=Path,
        default=ROOT / "output" / "bar15_mid23_short_noline_first30.mp4",
    )
    p.add_argument("--motion", type=Path, default=ROOT / "output" / "motion.csv")
    p.add_argument("--clusters", type=Path, default=ROOT / "output" / "clusters.csv")
    p.add_argument("--summary", type=Path, default=ROOT / "output" / "cluster_summary.csv")
    p.add_argument(
        "--out",
        type=Path,
        default=ROOT / "output" / "clusters_overlay_first30.mp4",
    )
    p.add_argument(
        "--max-radius",
        type=float,
        default=DEFAULT_MAX_RADIUS,
        help="Cluster circle radius in pixels (should match clustering).",
    )
    args = p.parse_args()
    for path, label in [
        (args.video, "video"),
        (args.motion, "motion"),
        (args.clusters, "clusters"),
        (args.summary, "summary"),
    ]:
        if not path.exists():
            print(f"Missing {label}: {path}")
            sys.exit(1)
    out = render(
        args.video,
        args.motion,
        args.clusters,
        args.summary,
        args.out,
        max_radius=args.max_radius,
    )
    print(f"Saved {out}")


if __name__ == "__main__":
    main()
