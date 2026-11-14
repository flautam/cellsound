"""Cluster analysis: trend-matching ranks + calibrated speeds.

Reads:
  output/cluster_summary.csv
  output/clusters.csv
  output/motion.csv

Writes:
  output/summary.json              — population + ranked clusters (sonify-ready)
  output/cluster_analysis.csv      — one row per cluster
  output/cluster_timeseries.csv    — per-frame cluster metrics

Calibration comes from the website upload fields (same names as the API):
  pixel_size_um      — µm per pixel (from the scale bar)
  minutes_per_frame  — minutes between frames (from the time lapse)

If both are set, velocities are µm/min. If either is missing, velocities stay
in px/frame (no silent bar15 defaults).

Ranking: detect the population velocity trend, then rank clusters by how
strongly they move along that same direction (highest = best match).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
SUMMARY_IN = ROOT / "output" / "cluster_summary.csv"
CLUSTERS_IN = ROOT / "output" / "clusters.csv"
MOTION_IN = ROOT / "output" / "motion.csv"
SUMMARY_JSON = ROOT / "output" / "summary.json"
ANALYSIS_CSV = ROOT / "output" / "cluster_analysis.csv"
TIMESERIES_CSV = ROOT / "output" / "cluster_timeseries.csv"

# Optional local demo only (bar15 mid23 first30). Website must pass real values.
DEMO_BAR15_PIXEL_SIZE_UM = 100.0 / 77.0
DEMO_BAR15_MINUTES_PER_FRAME = (2.42 * 60.0) / 29.0

COLORS = ["#6E9BFF", "#3FD1C9", "#B79BFF", "#F2A93B", "#FF7A6B", "#7BE0A6", "#FFD25E", "#C0C0C0"]
INSTRUMENTS = ["piano", "marimba", "cello", "flute", "bell", "soft synth", "piano", "marimba"]


def _num(value, places: int = 3):
    if value is None or (isinstance(value, float) and (np.isnan(value) or np.isinf(value))):
        return 0.0
    return round(float(value), places)


def resolve_calibration(
    pixel_size_um: float | None,
    minutes_per_frame: float | None,
) -> tuple[bool, float, float, str, str]:
    """Return (calibrated, scale, dt, speed_unit, note) from website inputs."""
    has_scale = pixel_size_um is not None and float(pixel_size_um) > 0
    has_time = minutes_per_frame is not None and float(minutes_per_frame) > 0
    if has_scale and has_time:
        return (
            True,
            float(pixel_size_um),
            float(minutes_per_frame),
            "µm/min",
            "from website calibration (pixel_size_um, minutes_per_frame)",
        )
    if has_scale or has_time:
        missing = "minutes_per_frame" if has_scale else "pixel_size_um"
        return (
            False,
            float(pixel_size_um) if has_scale else 1.0,
            float(minutes_per_frame) if has_time else 1.0,
            "px/frame",
            f"incomplete calibration (missing {missing}); reporting px/frame",
        )
    return (
        False,
        1.0,
        1.0,
        "px/frame",
        "no website calibration; reporting px/frame",
    )


def px_per_frame_to_display(
    v_px_frame: float,
    pixel_size_um: float,
    minutes_per_frame: float,
    calibrated: bool,
) -> float:
    """Pixels/frame → µm/min when calibrated, else leave as px/frame."""
    if not calibrated:
        return float(v_px_frame)
    if minutes_per_frame <= 0 or pixel_size_um <= 0:
        raise ValueError("pixel_size_um and minutes_per_frame must be > 0")
    return float(v_px_frame) * float(pixel_size_um) / float(minutes_per_frame)


def load_tables(
    summary_path: Path,
    clusters_path: Path,
    motion_path: Path,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    summary = pd.read_csv(summary_path)
    clusters = pd.read_csv(clusters_path)
    motion = pd.read_csv(motion_path)
    needed_sum = {
        "frame", "cluster_id", "n_cells", "centroid_x", "centroid_y",
        "mean_vx", "mean_vy", "mean_speed", "mean_radial_v",
    }
    missing = needed_sum - set(summary.columns)
    if missing:
        raise ValueError(f"cluster_summary.csv missing columns: {sorted(missing)}")
    if not {"frame", "cell_id", "cluster_id"} <= set(clusters.columns):
        raise ValueError("clusters.csv needs frame, cell_id, cluster_id")
    if not {"frame", "cell_id", "vx", "vy", "speed", "radial_v", "x", "y"} <= set(motion.columns):
        raise ValueError("motion.csv missing velocity/position columns")
    return summary, clusters, motion


def build_timeseries(
    summary: pd.DataFrame,
    pixel_size_um: float,
    minutes_per_frame: float,
    calibrated: bool,
) -> pd.DataFrame:
    rows = []
    for _, row in summary.iterrows():
        scale = pixel_size_um if calibrated else 1.0
        rows.append(
            {
                "frame": int(row["frame"]),
                "cluster_id": int(row["cluster_id"]),
                "n_cells": int(row["n_cells"]),
                "centroid_x_px": float(row["centroid_x"]),
                "centroid_y_px": float(row["centroid_y"]),
                "centroid_x_um": float(row["centroid_x"]) * scale,
                "centroid_y_um": float(row["centroid_y"]) * scale,
                "vx_um_per_min": px_per_frame_to_display(
                    row["mean_vx"], pixel_size_um, minutes_per_frame, calibrated
                ),
                "vy_um_per_min": px_per_frame_to_display(
                    row["mean_vy"], pixel_size_um, minutes_per_frame, calibrated
                ),
                "speed_um_per_min": px_per_frame_to_display(
                    row["mean_speed"], pixel_size_um, minutes_per_frame, calibrated
                ),
                "radial_v_um_per_min": px_per_frame_to_display(
                    row["mean_radial_v"], pixel_size_um, minutes_per_frame, calibrated
                ),
            }
        )
    return pd.DataFrame(rows)


def _cohesion_px(
    clusters: pd.DataFrame,
    motion: pd.DataFrame,
    summary: pd.DataFrame,
) -> dict[int, float]:
    """Mean RMS distance of members to algorithm centroid (pixels)."""
    merged = motion.merge(clusters, on=["frame", "cell_id"], how="inner")
    out: dict[int, float] = {}
    for cid, gsum in summary.groupby("cluster_id"):
        rms_vals = []
        for _, row in gsum.iterrows():
            fr = int(row["frame"])
            cx, cy = float(row["centroid_x"]), float(row["centroid_y"])
            pts = merged[(merged.frame == fr) & (merged.cluster_id == cid)]
            if pts.empty:
                continue
            d2 = (pts["x"] - cx) ** 2 + (pts["y"] - cy) ** 2
            rms_vals.append(float(np.sqrt(d2.mean())))
        out[int(cid)] = float(np.mean(rms_vals)) if rms_vals else 0.0
    return out


def _region_label(x_px: float, y_px: float, width: float, height: float) -> str:
    """Plain-language place in the field of view (image y grows downward)."""
    xr = x_px / max(width, 1.0)
    yr = y_px / max(height, 1.0)
    if yr < 1 / 3:
        vert = "top"
    elif yr < 2 / 3:
        vert = "middle"
    else:
        vert = "bottom"
    if xr < 1 / 3:
        horiz = "left"
    elif xr < 2 / 3:
        horiz = "center"
    else:
        horiz = "right"
    if horiz == "center" and vert == "middle":
        return "center"
    if horiz == "center":
        return f"{vert} center"
    if vert == "middle":
        return f"{horiz} side"
    return f"{vert}-{horiz}"


def _speed_phrase(speed: float, pop_speed: float, speed_unit: str) -> str:
    unit_words = (
        "micrometers per minute" if speed_unit == "µm/min" else "pixels per frame"
    )
    if speed < max(0.25, 0.4 * pop_speed):
        return f"slowly, about {_num(speed, 2)} {unit_words}"
    if speed < max(0.8, 1.2 * pop_speed):
        return f"at a moderate pace, about {_num(speed, 2)} {unit_words}"
    return f"quickly, about {_num(speed, 2)} {unit_words}"


def _move_phrase(
    dx: float,
    dy: float,
    start_region: str,
    end_region: str,
    length_unit: str,
) -> str:
    dist = float(np.hypot(dx, dy))
    unit_words = "micrometers" if length_unit == "µm" else "pixels"
    near = 5.0 if length_unit == "µm" else 5.0
    if dist < near and start_region == end_region:
        return f"stayed near the {start_region}"
    direction = _compass_label(float(np.degrees(np.arctan2(dy, dx))))
    if start_region == end_region:
        return (
            f"drifted {direction} within the {start_region}, "
            f"covering about {_num(dist, 1)} {unit_words}"
        )
    return (
        f"moved from the {start_region} toward the {end_region} "
        f"(generally {direction}), covering about {_num(dist, 1)} {unit_words}"
    )


def _chunk_narration(
    cluster_id: int,
    start_region: str,
    end_region: str,
    dx: float,
    dy: float,
    speed: float,
    pop_speed: float,
    n_cells_mean: float,
    duration_min: float | None,
    n_frames: int,
    speed_unit: str,
    length_unit: str,
    calibrated: bool,
) -> str:
    """Speakable description: where it started, where it went, how fast."""
    move = _move_phrase(dx, dy, start_region, end_region, length_unit)
    speed_text = _speed_phrase(speed, pop_speed, speed_unit)
    n = max(1, int(round(n_cells_mean)))
    if calibrated and duration_min is not None:
        time_bit = f"over {_num(duration_min, 1)} minutes"
    else:
        time_bit = f"across {n_frames} frames"
    return (
        f"Chunk {cluster_id} started in the {start_region} of the field. "
        f"It {move}. "
        f"On average it moved {speed_text}, "
        f"with about {n} cells {time_bit}."
    )


def _mode_label(values: list[str]) -> str | None:
    if not values:
        return None
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return max(counts.items(), key=lambda item: (item[1], item[0]))[0]


def _video_overview_from_chunks(
    chunks: list[dict],
    *,
    n_cells: int,
    time_phrase: str,
    compass: str,
    pop_speed: float,
    trend_speed: float,
    pct_main: int,
    speed_unit: str,
) -> dict:
    """Speakable video summary built by reading the chunk summaries."""
    if not chunks:
        text = f"No movement chunks were found over {time_phrase}."
        return {"description": text, "lead_chunk_ids": [], "unusual_chunk_ids": []}

    followers = [c for c in chunks if c["along_trend"] >= 0 and not c["unusual"]]
    unusual = [c for c in chunks if c["unusual"]]
    start_mode = _mode_label([c["start_region"] for c in chunks])
    end_mode = _mode_label([c["end_region"] for c in chunks])
    lead = chunks[: min(3, len(chunks))]
    lead_ids = [int(c["cluster_id"]) for c in lead]
    unusual_ids = [int(c["cluster_id"]) for c in unusual]

    lead_bits = []
    for c in lead:
        lead_bits.append(
            f"chunk {c['cluster_id']} ({c['start_region']} → {c['end_region']})"
        )
    lead_phrase = ", ".join(lead_bits[:-1]) + (
        f", and {lead_bits[-1]}" if len(lead_bits) > 1 else lead_bits[0]
    )

    place_phrase = ""
    if start_mode and end_mode:
        if start_mode == end_mode:
            place_phrase = f"Most chunks stayed around the {start_mode}. "
        else:
            place_phrase = (
                f"Chunks often began in the {start_mode} and ended toward the {end_mode}. "
            )

    if pct_main >= 60:
        trend_phrase = (
            f"Overall, cells drift {compass} "
            f"(about {_num(trend_speed, 2)} {speed_unit} net), "
            f"and {pct_main}% of the activity follows that pattern. "
        )
    elif pct_main >= 40:
        trend_phrase = (
            f"Motion is mixed, with a mild overall drift {compass} "
            f"({_num(trend_speed, 2)} {speed_unit} net; {pct_main}% with the trend). "
        )
    else:
        trend_phrase = (
            f"There is little shared direction; net drift is only "
            f"{_num(trend_speed, 2)} {speed_unit} toward {compass}. "
        )

    speed_phrase = (
        f"Typical chunk speed is about {_num(pop_speed, 2)} {speed_unit}. "
    )

    follow_phrase = (
        f"{len(followers)} of {len(chunks)} chunks support the main trend; "
        f"the strongest matches are {lead_phrase}. "
    )
    if unusual:
        if len(unusual_ids) == 1:
            unusual_phrase = f"Chunk {unusual_ids[0]} moves against that trend. "
        else:
            head = ", ".join(str(i) for i in unusual_ids[:-1])
            unusual_phrase = (
                f"Chunks {head} and {unusual_ids[-1]} move against that trend. "
            )
    else:
        unusual_phrase = "No chunk clearly opposes the main trend. "

    closing = (
        f"This overview covers {n_cells} tracked cells in {len(chunks)} chunks "
        f"over {time_phrase}. You can step through each chunk next for where it "
        f"started, where it went, and how fast it moved."
    )

    description = (
        trend_phrase
        + place_phrase
        + speed_phrase
        + follow_phrase
        + unusual_phrase
        + closing
    )
    return {
        "description": description,
        "lead_chunk_ids": lead_ids,
        "unusual_chunk_ids": unusual_ids,
        "common_start_region": start_mode,
        "common_end_region": end_mode,
    }


def analyze(
    summary: pd.DataFrame,
    clusters: pd.DataFrame,
    motion: pd.DataFrame,
    pixel_size_um: float | None,
    minutes_per_frame: float | None,
    width_px: float | None = None,
    height_px: float | None = None,
) -> tuple[dict, pd.DataFrame, pd.DataFrame]:
    calibrated, scale, dt, speed_unit, calibration_note = resolve_calibration(
        pixel_size_um, minutes_per_frame
    )
    length_unit = "µm" if calibrated else "px"
    ts = build_timeseries(summary, scale, dt, calibrated)
    cohesion = _cohesion_px(clusters, motion, summary)
    width = float(width_px) if width_px else float(max(motion["x"].max(), summary["centroid_x"].max()) + 1)
    height = float(height_px) if height_px else float(max(motion["y"].max(), summary["centroid_y"].max()) + 1)

    # Population trend = n_cells-weighted mean velocity over all cluster-frames
    w = ts["n_cells"].to_numpy(dtype=float)
    wsum = float(w.sum()) or 1.0
    pop_vx = float((ts["vx_um_per_min"] * w).sum() / wsum)
    pop_vy = float((ts["vy_um_per_min"] * w).sum() / wsum)
    pop_speed = float((ts["speed_um_per_min"] * w).sum() / wsum)
    trend_speed = float(np.hypot(pop_vx, pop_vy))
    pop_radial = float((ts["radial_v_um_per_min"] * w).sum() / wsum)
    if trend_speed < 1e-9:
        ux, uy = 1.0, 0.0
        trend_deg = 0.0
    else:
        ux, uy = pop_vx / trend_speed, pop_vy / trend_speed
        trend_deg = float(np.degrees(np.arctan2(uy, ux)))

    # Compass-ish label for descriptions (image y grows down; still report math angle)
    compass = _compass_label(trend_deg)

    cell_ids_by_cluster: dict[int, list[int]] = {}
    for cid, g in clusters.groupby("cluster_id"):
        cell_ids_by_cluster[int(cid)] = sorted(int(x) for x in g["cell_id"].unique())

    scored = []
    for cid, g in ts.groupby("cluster_id"):
        g = g.sort_values("frame")
        nw = g["n_cells"].to_numpy(dtype=float)
        nwsum = float(nw.sum()) or 1.0
        vx = float((g["vx_um_per_min"] * nw).sum() / nwsum)
        vy = float((g["vy_um_per_min"] * nw).sum() / nwsum)
        speed = float((g["speed_um_per_min"] * nw).sum() / nwsum)
        radial = float((g["radial_v_um_per_min"] * nw).sum() / nwsum)
        mean_n = float(nw.mean())
        along = vx * ux + vy * uy  # µm/min along population trend
        contrib = along * mean_n

        start = g.iloc[0]
        end = g.iloc[-1]
        x0_px, y0_px = float(start["centroid_x_px"]), float(start["centroid_y_px"])
        x1_px, y1_px = float(end["centroid_x_px"]), float(end["centroid_y_px"])
        start_region = _region_label(x0_px, y0_px, width, height)
        end_region = _region_label(x1_px, y1_px, width, height)
        length_scale = scale if calibrated else 1.0
        dx = (x1_px - x0_px) * length_scale
        dy = (y1_px - y0_px) * length_scale
        n_fr = int(g["frame"].nunique())
        chunk_minutes = (max(1, n_fr - 1) * dt) if calibrated else None

        scored.append(
            {
                "cluster_id": int(cid),
                "n_cells_mean": mean_n,
                "n_cells_max": int(g["n_cells"].max()),
                "n_frames": n_fr,
                "duration_min": chunk_minutes,
                "mean_vx_um_per_min": vx,
                "mean_vy_um_per_min": vy,
                "mean_speed_um_per_min": speed,
                "mean_radial_v_um_per_min": radial,
                "along_trend_um_per_min": along,
                "trend_contribution": contrib,
                "cohesion_px": cohesion.get(int(cid), 0.0),
                "cohesion_um": cohesion.get(int(cid), 0.0) * length_scale,
                "start_frame": int(start["frame"]),
                "end_frame": int(end["frame"]),
                "start_x_px": x0_px,
                "start_y_px": y0_px,
                "end_x_px": x1_px,
                "end_y_px": y1_px,
                "start_x_um": x0_px * length_scale,
                "start_y_um": y0_px * length_scale,
                "end_x_um": x1_px * length_scale,
                "end_y_um": y1_px * length_scale,
                "displacement_um": float(np.hypot(dx, dy)),
                "dx_um": dx,
                "dy_um": dy,
                "start_region": start_region,
                "end_region": end_region,
                "cell_ids": cell_ids_by_cluster.get(int(cid), []),
            }
        )

    # Highest along-trend velocity = best match to general trend
    scored.sort(key=lambda r: (-r["along_trend_um_per_min"], -r["trend_contribution"], r["cluster_id"]))
    for rank, row in enumerate(scored, start=1):
        row["rank"] = rank

    # % of cell-frames whose cluster velocity projects positively on trend
    match_mask = (ts["vx_um_per_min"] * ux + ts["vy_um_per_min"] * uy) > 0
    pct_main = int(round(100.0 * float((match_mask.to_numpy() * w).sum() / wsum))) if wsum else 0

    frames = sorted(summary["frame"].unique())
    n_frames = len(frames)
    duration_min = (n_frames * dt) if calibrated else None
    n_cells_tracked = int(clusters["cell_id"].nunique())
    n_clusters = len(scored)
    time_phrase = (
        f"{n_frames} frames ({_num(duration_min, 1)} min)"
        if calibrated
        else f"{n_frames} frames"
    )

    analysis_rows = []
    cluster_json = []
    for row in scored:
        cid = row["cluster_id"]
        rank = row["rank"]
        name = _cluster_name(
            row["along_trend_um_per_min"],
            row["mean_speed_um_per_min"],
            trend_speed,
            pop_speed,
        )
        unusual = row["along_trend_um_per_min"] < 0 and trend_speed > 0.05
        narration = _chunk_narration(
            cid,
            row["start_region"],
            row["end_region"],
            row["dx_um"],
            row["dy_um"],
            row["mean_speed_um_per_min"],
            pop_speed,
            row["n_cells_mean"],
            row["duration_min"],
            row["n_frames"],
            speed_unit,
            length_unit,
            calibrated,
        )
        # Keep a short technical blurb too (rank / trend context for UI).
        tech = _cluster_description(name, row, compass, unusual, speed_unit)
        analysis_rows.append(
            {
                "rank": rank,
                "cluster_id": cid,
                "name": name,
                "unusual": unusual,
                "n_cells_mean": _num(row["n_cells_mean"], 2),
                "n_cells_max": row["n_cells_max"],
                "n_frames": row["n_frames"],
                "duration_min": _num(row["duration_min"], 1) if row["duration_min"] is not None else "",
                "start_region": row["start_region"],
                "end_region": row["end_region"],
                "start_x_um": _num(row["start_x_um"], 1),
                "start_y_um": _num(row["start_y_um"], 1),
                "end_x_um": _num(row["end_x_um"], 1),
                "end_y_um": _num(row["end_y_um"], 1),
                "displacement_um": _num(row["displacement_um"], 1),
                "mean_vx_um_per_min": _num(row["mean_vx_um_per_min"]),
                "mean_vy_um_per_min": _num(row["mean_vy_um_per_min"]),
                "mean_speed_um_per_min": _num(row["mean_speed_um_per_min"]),
                "mean_radial_v_um_per_min": _num(row["mean_radial_v_um_per_min"]),
                "along_trend_um_per_min": _num(row["along_trend_um_per_min"]),
                "trend_contribution": _num(row["trend_contribution"]),
                "cohesion_um": _num(row["cohesion_um"]),
                "instrument": INSTRUMENTS[(cid - 1) % len(INSTRUMENTS)],
                "color": COLORS[(cid - 1) % len(COLORS)],
                "audio": f"cluster_{cid}.wav",
                "description": narration,
                "description_tech": tech,
                "n_unique_cells": len(row["cell_ids"]),
            }
        )
        cluster_json.append(
            {
                "cluster_id": cid,
                "rank": rank,
                "name": name,
                "n_cells": int(round(row["n_cells_mean"])),
                "n_unique_cells": len(row["cell_ids"]),
                "unusual": unusual,
                "instrument": INSTRUMENTS[(cid - 1) % len(INSTRUMENTS)],
                "color": COLORS[(cid - 1) % len(COLORS)],
                "mean_speed": _num(row["mean_speed_um_per_min"]),
                "mean_vx": _num(row["mean_vx_um_per_min"]),
                "mean_vy": _num(row["mean_vy_um_per_min"]),
                "mean_radial_v": _num(row["mean_radial_v_um_per_min"]),
                "mean_angular_v": 0.0,
                "along_trend": _num(row["along_trend_um_per_min"]),
                "trend_contribution": _num(row["trend_contribution"]),
                "cohesion_um": _num(row["cohesion_um"]),
                "start_region": row["start_region"],
                "end_region": row["end_region"],
                "start": {
                    "frame": row["start_frame"],
                    "x_um": _num(row["start_x_um"], 1),
                    "y_um": _num(row["start_y_um"], 1),
                },
                "end": {
                    "frame": row["end_frame"],
                    "x_um": _num(row["end_x_um"], 1),
                    "y_um": _num(row["end_y_um"], 1),
                },
                "displacement_um": _num(row["displacement_um"], 1),
                "duration_min": _num(row["duration_min"], 1) if row["duration_min"] is not None else None,
                "description": narration,
                "description_tech": tech,
                "audio": f"cluster_{cid}.wav",
                "cell_ids": row["cell_ids"],
            }
        )

    overview = _video_overview_from_chunks(
        cluster_json,
        n_cells=n_cells_tracked,
        time_phrase=time_phrase,
        compass=compass,
        pop_speed=pop_speed,
        trend_speed=trend_speed,
        pct_main=pct_main,
        speed_unit=speed_unit,
    )
    # Short stats line kept for UI cards; overview.description is the spoken video summary.
    pop_stats = (
        f"{n_cells_tracked} cells · {n_clusters} chunks · {time_phrase}. "
        f"Trend {compass} at {_num(trend_speed)} {speed_unit}; "
        f"mean speed {_num(pop_speed)} {speed_unit}; "
        f"{pct_main}% with trend."
    )

    summary_json = {
        "units": {
            "speed": speed_unit,
            "length": length_unit,
            "time": "min" if calibrated else "frame",
        },
        "calibration": {
            "pixel_size_um": _num(scale, 6) if calibrated else None,
            "minutes_per_frame": _num(dt, 4) if calibrated else None,
            "calibrated": calibrated,
            "note": calibration_note,
        },
        "overview": {
            "description": overview["description"],
            "lead_chunk_ids": overview["lead_chunk_ids"],
            "unusual_chunk_ids": overview["unusual_chunk_ids"],
            "common_start_region": overview["common_start_region"],
            "common_end_region": overview["common_end_region"],
            "audio": "overview.wav",
        },
        "population": {
            "n_cells": n_cells_tracked,
            "n_clusters": n_clusters,
            "n_frames": n_frames,
            "duration_min": _num(duration_min, 2) if duration_min is not None else None,
            "mean_speed": _num(pop_speed),
            "mean_vx": _num(pop_vx),
            "mean_vy": _num(pop_vy),
            "trend_speed": _num(trend_speed),
            "mean_radial_v": _num(pop_radial),
            "mean_angular_v": 0.0,
            "trend_direction_deg": _num(trend_deg, 1),
            "trend_compass": compass,
            "pct_main_pattern": pct_main,
            "description": overview["description"],
            "stats_line": pop_stats,
        },
        "reliability": _reliability(clusters, motion),
        # Rank order: play overview first, then walk these chunks in sequence.
        "clusters": cluster_json,
    }

    # Attach along-trend onto timeseries for plotting
    ts = ts.copy()
    ts["along_trend_um_per_min"] = ts["vx_um_per_min"] * ux + ts["vy_um_per_min"] * uy
    ts["trend_ux"] = ux
    ts["trend_uy"] = uy

    analysis_df = pd.DataFrame(analysis_rows)
    return summary_json, analysis_df, ts


def _compass_label(deg: float) -> str:
    """Map math angle (0=+x/right, 90=+y/down in image coords) to a plain phrase."""
    # Normalize to [0, 360)
    a = deg % 360.0
    dirs = [
        (0, "right"),
        (45, "down-right"),
        (90, "down"),
        (135, "down-left"),
        (180, "left"),
        (225, "up-left"),
        (270, "up"),
        (315, "up-right"),
    ]
    best = min(dirs, key=lambda d: min(abs(a - d[0]), 360 - abs(a - d[0])))
    return best[1]


def _cluster_name(
    along: float,
    speed: float,
    trend_speed: float,
    mean_speed: float,
) -> str:
    if speed < max(0.2, 0.35 * mean_speed):
        return "Slow / nearly still"
    # Compare along-trend speed to the net population drift magnitude.
    scale = max(trend_speed, 0.05)
    if along >= 1.25 * scale:
        return "Strong trend followers"
    if along >= 0.25 * scale:
        return "Trend supporters"
    if along >= -0.25 * scale:
        return "Sideways / weak match"
    if along > -1.25 * scale:
        return "Weak counter-movers"
    return "Against the trend"


def _cluster_description(
    name: str, row: dict, compass: str, unusual: bool, speed_unit: str
) -> str:
    along = _num(row["along_trend_um_per_min"])
    speed = _num(row["mean_speed_um_per_min"])
    n = int(round(row["n_cells_mean"]))
    radial = _num(row["mean_radial_v_um_per_min"])
    toward = "toward" if radial < 0 else "away from" if radial > 0 else "around"
    base = (
        f"Cluster {row['cluster_id']}: ~{n} cells, {name.lower()}. "
        f"Mean speed {speed} {speed_unit}; {along} {speed_unit} along the population trend ({compass}). "
        f"Radial velocity {radial} {speed_unit} ({toward} POA)."
    )
    if unusual:
        return base + " This group moves against the main trend."
    return base


def _reliability(clusters: pd.DataFrame, motion: pd.DataFrame) -> dict:
    # Frame gaps per cell relative to its observed span
    missing = 0
    for _, g in clusters.sort_values("frame").groupby("cell_id"):
        frames = g["frame"].to_numpy()
        if len(frames) < 2:
            continue
        missing += int((frames.max() - frames.min() + 1) - len(frames))
    n_cells = int(clusters["cell_id"].nunique())
    if missing == 0:
        return {
            "level": "good",
            "note": "Every clustered cell was present on consecutive frames where it appeared.",
        }
    level = "fair" if missing > n_cells else "good"
    return {
        "level": level,
        "note": f"{missing} frame gaps across {n_cells} cells where a track skipped frames.",
    }


def main() -> None:
    p = argparse.ArgumentParser(
        description="Analyze clusters vs population trend. "
        "Pass website calibration: --pixel-size-um and --minutes-per-frame."
    )
    p.add_argument("--summary", type=Path, default=SUMMARY_IN)
    p.add_argument("--clusters", type=Path, default=CLUSTERS_IN)
    p.add_argument("--motion", type=Path, default=MOTION_IN)
    p.add_argument("--json-out", type=Path, default=SUMMARY_JSON)
    p.add_argument("--analysis-out", type=Path, default=ANALYSIS_CSV)
    p.add_argument("--timeseries-out", type=Path, default=TIMESERIES_CSV)
    p.add_argument(
        "--pixel-size-um",
        type=float,
        default=None,
        help="Website field pixel_size_um (µm per pixel from the scale bar).",
    )
    p.add_argument(
        "--minutes-per-frame",
        type=float,
        default=None,
        help="Website field minutes_per_frame (minutes between frames).",
    )
    p.add_argument(
        "--demo-bar15",
        action="store_true",
        help="Local demo only: use bar15 mid23 calibration (~1.30 µm/px, ~5 min/frame).",
    )
    p.add_argument("--width", type=float, default=None, help="FOV width in pixels.")
    p.add_argument("--height", type=float, default=None, help="FOV height in pixels.")
    args = p.parse_args()

    for path, label in [
        (args.summary, "cluster_summary"),
        (args.clusters, "clusters"),
        (args.motion, "motion"),
    ]:
        if not path.exists():
            print(f"Missing {label}: {path}")
            sys.exit(1)

    pixel = args.pixel_size_um
    minutes = args.minutes_per_frame
    if args.demo_bar15:
        pixel = DEMO_BAR15_PIXEL_SIZE_UM if pixel is None else pixel
        minutes = DEMO_BAR15_MINUTES_PER_FRAME if minutes is None else minutes

    summary, clusters, motion = load_tables(args.summary, args.clusters, args.motion)
    calibrated, scale, dt, speed_unit, note = resolve_calibration(pixel, minutes)
    print(
        f"Analyzing {summary['cluster_id'].nunique()} clusters, "
        f"{summary['frame'].nunique()} frames; "
        f"{'calibrated' if calibrated else 'uncalibrated'} "
        f"({note})."
    )
    summary_json, analysis_df, ts = analyze(
        summary,
        clusters,
        motion,
        pixel,
        minutes,
        width_px=args.width,
        height_px=args.height,
    )

    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(json.dumps(summary_json, indent=2))
    analysis_df.to_csv(args.analysis_out, index=False)
    ts.to_csv(args.timeseries_out, index=False)

    pop = summary_json["population"]
    print(
        f"Trend: {pop['trend_compass']} "
        f"({pop['mean_vx']} , {pop['mean_vy']}); "
        f"{pop['pct_main_pattern']}% with trend."
    )
    print("Overview:")
    print(" ", summary_json["overview"]["description"])
    print("Rank | cluster | along_trend | name")
    for c in summary_json["clusters"]:
        print(
            f"  {c['rank']:2d} | C{c['cluster_id']} | "
            f"{c['along_trend']:8.3f} | {c['name']}"
        )
    print(f"Saved {args.json_out}")
    print(f"Saved {args.analysis_out}")
    print(f"Saved {args.timeseries_out}")


if __name__ == "__main__":
    main()
