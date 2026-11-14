"""Overlay cluster hulls + centroid trails on a video (one enclosure per cluster)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2
import imageio.v3 as iio
import numpy as np
import pandas as pd
from scipy.spatial import ConvexHull

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


def draw_hull(frame_bgr: np.ndarray, pts: np.ndarray, color, thickness: int = 2) -> None:
    """Draw a convex enclosure around points (N,2) in x,y image coords."""
    if len(pts) == 0:
        return
    pts_i = np.round(pts).astype(np.int32)
    if len(pts_i) == 1:
        cv2.circle(frame_bgr, (int(pts_i[0, 0]), int(pts_i[0, 1])), 6, color, thickness)
        return
    if len(pts_i) == 2:
        cv2.line(
            frame_bgr,
            (int(pts_i[0, 0]), int(pts_i[0, 1])),
            (int(pts_i[1, 0]), int(pts_i[1, 1])),
            color,
            thickness,
        )
        for p in pts_i:
            cv2.circle(frame_bgr, (int(p[0]), int(p[1])), 4, color, -1)
        return
    try:
        hull = ConvexHull(pts)
        poly = pts_i[hull.vertices].reshape(-1, 1, 2)
    except Exception:
        # Fallback: axis-aligned box
        x0, y0 = pts_i.min(axis=0)
        x1, y1 = pts_i.max(axis=0)
        cv2.rectangle(frame_bgr, (int(x0), int(y0)), (int(x1), int(y1)), color, thickness)
        return
    cv2.polylines(frame_bgr, [poly], isClosed=True, color=color, thickness=thickness)
    overlay = frame_bgr.copy()
    cv2.fillPoly(overlay, [poly], color)
    cv2.addWeighted(overlay, 0.12, frame_bgr, 0.88, 0, frame_bgr)


def render(
    video_path: Path,
    motion_path: Path,
    clusters_path: Path,
    summary_path: Path,
    out_path: Path,
    max_radius: float = 35.0,
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

        # Non-overlapping circle enclosures at algorithm centroids
        for _, row in sdf.iterrows():
            cid = int(row["cluster_id"])
            color = color_for(cid)
            cx, cy = float(row[cx_col]), float(row[cy_col])
            cv2.circle(
                bgr,
                (int(round(cx)), int(round(cy))),
                int(round(max_radius)),
                color,
                2,
                lineType=cv2.LINE_AA,
            )
            overlay = bgr.copy()
            cv2.circle(
                overlay,
                (int(round(cx)), int(round(cy))),
                int(round(max_radius)),
                color,
                -1,
                lineType=cv2.LINE_AA,
            )
            cv2.addWeighted(overlay, 0.10, bgr, 0.90, 0, bgr)

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
            # current average position
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
    # Write with OpenCV mpeg4 so QuickTime/Finder play it as a real movie
    # (imageio h264 often opens as a single still on macOS).
    tmp = out_path.with_suffix(".tmp.mp4")
    if tmp.exists():
        tmp.unlink()
    writer = cv2.VideoWriter(
        str(tmp),
        cv2.VideoWriter_fourcc(*"mp4v"),
        float(fps),
        (w, h),
    )
    if not writer.isOpened():
        raise RuntimeError(f"Could not open VideoWriter for {tmp}")
    for rgb in out_frames:
        writer.write(cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
    writer.release()
    tmp.replace(out_path)
    return out_path


def main() -> None:
    p = argparse.ArgumentParser(description="Render cluster hull video.")
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
        default=28.0,
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
