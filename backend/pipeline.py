"""Job pipeline: tracks → motion → clusters → analysis → overlay → sounds."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from backend.analysis import analyze as analyze_clusters
from backend.cluster import DEFAULT_JOINER_WEIGHT, DEFAULT_K, DEFAULT_MAX_RADIUS, DEFAULT_MIN_SIZE, DEFAULT_STICKINESS, run_clustering
from backend.motion import compute_motion
from backend.render_cluster_video import render as render_cluster_video
from backend.sonify import write_sounds


def _num(value, places: int = 1):
    try:
        return round(float(value), places)
    except (TypeError, ValueError):
        return 0.0


def build_cluster_traces(
    cluster_summary: pd.DataFrame,
    summary_json: dict,
    width: int,
    height: int,
    poa_x: float,
    poa_y: float,
    max_radius: float = DEFAULT_MAX_RADIUS,
) -> dict:
    """Centroid trails + fixed-radius circles (same balls as the overlay video)."""
    color_of = {
        int(c["cluster_id"]): c.get("color", "#6E9BFF")
        for c in summary_json.get("clusters", [])
    }
    import numpy as np

    clusters = []
    max_frame = 0
    for cid, g in cluster_summary.sort_values("frame").groupby("cluster_id"):
        rows = g.sort_values("frame")
        frames = rows["frame"].to_numpy()
        pts = rows[["centroid_x", "centroid_y"]].to_numpy(dtype=float)
        if len(pts) > 50:
            picked = np.linspace(0, len(pts) - 1, 50).astype(int)
            pts = pts[picked]
            frames = frames[picked]
        path = [[_num(x), _num(y), int(frame)] for (x, y), frame in zip(pts, frames)]
        if len(frames):
            max_frame = max(max_frame, int(frames.max()))
        end = path[-1] if path else [0.0, 0.0]
        clusters.append(
            {
                "cluster_id": int(cid),
                "color": color_of.get(int(cid), "#6E9BFF"),
                "paths": [path],
                "centroid": {"x": end[0], "y": end[1]},
            }
        )
    n_cells = int(summary_json.get("population", {}).get("n_cells") or 0)
    n_frames = int(summary_json.get("population", {}).get("n_frames") or 0)
    return {
        "width": int(width),
        "height": int(height),
        "poa": {"x": float(poa_x), "y": float(poa_y)},
        "max_radius": float(max_radius),
        "clusters": clusters,
        "max_frame": int(max_frame),
        "description": (
            f"Fixed-radius chunk circles and centroid trails for {len(clusters)} chunks "
            f"from {n_cells} cells over {n_frames} frames. The Point of Audio is marked."
        ),
        "cluster_video_url": "clusters_overlay.mp4",
    }


def run_job_analysis(
    folder: Path,
    tracks: pd.DataFrame,
    settings: dict,
    width: int,
    height: int,
    pixel_size_um: float | None,
    minutes_per_frame: float | None,
    fps: float | None = None,
) -> dict:
    """Write motion/clusters/summary/traces/overlay/sounds into a job folder.

    Re-runs clustering from scratch each analyze call (POA / settings changes apply).
    """
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)

    poa = settings.get("poa") or {}
    poa_x = float(poa.get("x", width / 2))
    poa_y = float(poa.get("y", height / 2))
    k = int(settings.get("n_clusters") or DEFAULT_K)
    max_radius = float(settings.get("max_radius") or DEFAULT_MAX_RADIUS)
    min_size = int(settings.get("min_size") or DEFAULT_MIN_SIZE)
    stickiness = float(settings.get("stickiness") or DEFAULT_STICKINESS)
    joiner_weight = float(settings.get("joiner_weight", DEFAULT_JOINER_WEIGHT))
    overlay_fps = float(fps) if fps and float(fps) > 0 else None

    motion = compute_motion(tracks, poa_x, poa_y) if not tracks.empty else pd.DataFrame()
    motion_path = folder / "motion.csv"
    if not motion.empty:
        motion.to_csv(motion_path, index=False)

    if motion.empty:
        summary = {
            "units": {"speed": "px/frame", "length": "px", "time": "frame"},
            "calibration": {
                "pixel_size_um": pixel_size_um,
                "minutes_per_frame": minutes_per_frame,
                "calibrated": bool(pixel_size_um and minutes_per_frame),
                "note": "no motion rows",
            },
            "overview": {
                "description": "No cells were tracked, so there is nothing to summarize yet.",
                "lead_chunk_ids": [],
                "unusual_chunk_ids": [],
                "common_start_region": None,
                "common_end_region": None,
                "audio": "overview.wav",
            },
            "population": {
                "n_cells": 0,
                "n_clusters": 0,
                "n_frames": 0,
                "duration_min": None,
                "mean_speed": 0.0,
                "mean_vx": 0.0,
                "mean_vy": 0.0,
                "trend_speed": 0.0,
                "mean_radial_v": 0.0,
                "mean_angular_v": 0.0,
                "trend_direction_deg": 0.0,
                "trend_compass": "right",
                "pct_main_pattern": 0,
                "description": "No cells were tracked, so there is nothing to summarize yet.",
                "stats_line": "0 cells tracked.",
            },
            "reliability": {"level": "unknown", "note": "No tracks were available."},
            "clusters": [],
        }
        traces = {
            "width": int(width),
            "height": int(height),
            "poa": {"x": poa_x, "y": poa_y},
            "max_radius": float(max_radius),
            "clusters": [],
            "description": "No cluster trails yet.",
            "cluster_video_url": None,
        }
        (folder / "summary.json").write_text(json.dumps(summary, indent=2))
        (folder / "traces.json").write_text(json.dumps(traces, indent=2))
        write_sounds(folder / "sounds", motion, summary, poa_x, poa_y, width, height)
        return summary

    clusters_df, cluster_summary = run_clustering(
        motion,
        k=max(1, k),
        min_size=max(1, min_size),
        max_radius=max_radius,
        stickiness=max(1.0, stickiness),
        joiner_weight=max(0.0, joiner_weight),
    )
    clusters_path = folder / "clusters.csv"
    summary_csv_path = folder / "cluster_summary.csv"
    clusters_df.to_csv(clusters_path, index=False)
    cluster_summary.to_csv(summary_csv_path, index=False)

    summary, analysis_df, timeseries = analyze_clusters(
        cluster_summary,
        clusters_df,
        motion,
        pixel_size_um,
        minutes_per_frame,
        width_px=float(width),
        height_px=float(height),
    )
    # Optional instrument overrides from the setup form.
    instruments = settings.get("instruments") or {}
    for cluster in summary.get("clusters", []):
        cid = cluster["cluster_id"]
        override = instruments.get(str(cid), instruments.get(cid))
        if override:
            cluster["instrument"] = str(override)

    analysis_df.to_csv(folder / "cluster_analysis.csv", index=False)
    timeseries.to_csv(folder / "cluster_timeseries.csv", index=False)
    (folder / "summary.json").write_text(json.dumps(summary, indent=2))

    traces = build_cluster_traces(
        cluster_summary, summary, width, height, poa_x, poa_y, max_radius=max_radius
    )
    # Prefer the cropped/selected tracking clip; fall back to the upload.
    video_path = folder / "tracked.mp4"
    if not video_path.exists():
        video_path = folder / "video.mp4"
    overlay_path = folder / "clusters_overlay.mp4"
    if video_path.exists():
        try:
            render_cluster_video(
                video_path,
                motion_path,
                clusters_path,
                summary_csv_path,
                overlay_path,
                max_radius=max_radius,
                fps=overlay_fps,
            )
            traces["cluster_video_url"] = "clusters_overlay.mp4"
        except Exception as exc:
            traces["cluster_video_url"] = None
            traces["cluster_video_error"] = str(exc)
    else:
        traces["cluster_video_url"] = None

    (folder / "traces.json").write_text(json.dumps(traces, indent=2))
    write_sounds(folder / "sounds", motion, summary, poa_x, poa_y, width, height)
    return summary
