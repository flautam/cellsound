"""Turn tracks into the JSON shapes the website already expects.

No Cellpose here. The server tracks first, then calls these functions.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans

from backend.motion import compute_motion

COLORS = ["#6E9BFF", "#3FD1C9", "#B79BFF", "#F2A93B", "#FF7A6B", "#7BE0A6", "#FFD25E"]


def _num(value, places=2):
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return 0.0
    return round(float(value), places)


def _scale(value, pixel_size_um, minutes_per_frame):
    """Pixels per frame become µm per minute when both calibration numbers exist."""
    if pixel_size_um and minutes_per_frame:
        return value * float(pixel_size_um) / float(minutes_per_frame)
    return value


def _per_cell(tracks: pd.DataFrame, motion: pd.DataFrame, poa_x: float, poa_y: float) -> pd.DataFrame:
    rows = []
    for cell_id, group in motion.groupby("cell_id"):
        steps = group.dropna(subset=["speed"])
        path = tracks[tracks["cell_id"] == cell_id].sort_values("frame")
        frames = path["frame"].to_numpy()
        theta = np.atan2(path["y"].to_numpy() - poa_y, path["x"].to_numpy() - poa_x)
        turns = []
        for index in range(1, len(path)):
            if frames[index] - frames[index - 1] != 1:
                continue
            delta = theta[index] - theta[index - 1]
            turns.append(float(np.atan2(np.sin(delta), np.cos(delta))))
        rows.append(
            {
                "cell_id": int(cell_id),
                "x": float(group["x"].mean()),
                "y": float(group["y"].mean()),
                "vx": float(steps["vx"].mean()) if not steps.empty else 0.0,
                "vy": float(steps["vy"].mean()) if not steps.empty else 0.0,
                "speed": float(steps["speed"].mean()) if not steps.empty else 0.0,
                "radial_v": float(steps["radial_v"].mean()) if not steps.empty else 0.0,
                "angular_v": float(np.mean(turns)) if turns else 0.0,
                "n_frames": int(len(path)),
                "frames_missing": int((frames.max() - frames.min() + 1) - len(path)) if len(path) else 0,
            }
        )
    return pd.DataFrame(rows)


def _assign_clusters(cells: pd.DataFrame, n_clusters: int, position_weight: float) -> pd.DataFrame:
    labeled = cells.copy()
    if labeled.empty:
        labeled["cluster_id"] = pd.Series(dtype=int)
        return labeled
    count = max(1, min(int(n_clusters), len(labeled)))
    if count == 1 or len(labeled) == 1:
        labeled["cluster_id"] = 1
        return labeled
    position = labeled[["x", "y"]].to_numpy(dtype=float)
    velocity = labeled[["vx", "vy"]].to_numpy(dtype=float)

    def standardize(values):
        spread = values.std(axis=0)
        spread[spread < 1e-6] = 1.0
        return (values - values.mean(axis=0)) / spread

    weight = min(1.0, max(0.0, float(position_weight)))
    features = weight * standardize(position) + (1.0 - weight) * standardize(velocity)
    labels = KMeans(n_clusters=count, n_init=10, random_state=0).fit_predict(features)
    labeled["cluster_id"] = labels + 1
    return labeled


def _cluster_name(speed, radial, angular, population_radial):
    if speed < 0.15:
        return "Slow cells", False
    if radial > 0.05 and population_radial <= 0:
        return "Outward movers", True
    if radial < -0.05:
        return "Inward drifters", False
    if abs(angular) > 0.08:
        return "Circling cells", False
    return "Steady movers", False


def _sentence(name, n_cells, speed_text, radial, toward_word):
    if name == "Outward movers":
        return f"{n_cells} cells move away from the Point of Audio. That is unusual."
    if name == "Slow cells":
        return f"{n_cells} cells barely move, at about {speed_text}."
    if name == "Circling cells":
        return f"{n_cells} cells circle the Point of Audio."
    if name == "Inward drifters":
        return f"{n_cells} cells move {toward_word} the Point of Audio."
    return f"{n_cells} cells move steadily, at about {speed_text}."


def build_analysis(tracks: pd.DataFrame, settings: dict, width: int, height: int, pixel_size_um, minutes_per_frame):
    """Return summary JSON, traces JSON, and a motion table."""
    poa = settings.get("poa") or {}
    poa_x = float(poa.get("x", width / 2))
    poa_y = float(poa.get("y", height / 2))
    motion = compute_motion(tracks, poa_x, poa_y) if not tracks.empty else pd.DataFrame()
    cells = _per_cell(tracks, motion, poa_x, poa_y) if not motion.empty else pd.DataFrame()
    n_clusters = int(settings.get("n_clusters") or 5)
    weight = float(settings.get("position_weight", 0.5))
    cells = _assign_clusters(cells, n_clusters, weight)

    calibrated = bool(pixel_size_um and minutes_per_frame)
    speed_unit = "µm/min" if calibrated else "px/frame"
    angular_unit = "rad/min" if calibrated else "rad/frame"
    frame_count = int(tracks["frame"].nunique()) if not tracks.empty else 0
    duration = frame_count * float(minutes_per_frame) if calibrated else frame_count

    def speed_of(value):
        return _num(_scale(value, pixel_size_um, minutes_per_frame) if calibrated else value)

    def angle_of(value):
        scaled = value / float(minutes_per_frame) if calibrated else value
        return _num(scaled, 3)

    if cells.empty:
        population_radial = 0.0
        population_speed = 0.0
        population_angular = 0.0
    else:
        population_radial = float(cells["radial_v"].mean())
        population_speed = float(cells["speed"].mean())
        population_angular = float(cells["angular_v"].mean())

    groups = []
    if not cells.empty:
        scored = []
        for cluster_id, group in cells.groupby("cluster_id"):
            mean_radial = float(group["radial_v"].mean())
            scored.append((abs(mean_radial - population_radial), int(cluster_id), group, mean_radial))
        # 1 is closest to the population mean radial velocity (most typical).
        # The highest number is farthest from it. Ties keep the lower original id.
        scored.sort(key=lambda item: (item[0], item[1]))
        for rank, (_gap, _old_id, group, mean_radial) in enumerate(scored, start=1):
            cluster_id = rank
            mean_speed = float(group["speed"].mean())
            mean_angular = float(group["angular_v"].mean())
            shown_speed = speed_of(mean_speed)
            name, unusual = _cluster_name(shown_speed, speed_of(mean_radial), angle_of(mean_angular), speed_of(population_radial))
            groups.append(
                {
                    "cluster_id": cluster_id,
                    "rank": rank,
                    "name": name,
                    "n_cells": int(len(group)),
                    "unusual": unusual,
                    "instrument": f"Tone {cluster_id}",
                    "color": COLORS[(cluster_id - 1) % len(COLORS)],
                    "mean_speed": shown_speed,
                    "mean_radial_v": speed_of(mean_radial),
                    "mean_angular_v": angle_of(mean_angular),
                    "description": _sentence(name, int(len(group)), f"{shown_speed:g} {speed_unit}", speed_of(mean_radial), "toward"),
                    "audio": f"cluster_{cluster_id}.wav",
                    "cell_ids": [int(value) for value in group["cell_id"].tolist()],
                    "_ids": set(int(value) for value in group["cell_id"].tolist()),
                }
            )

    n_cells = int(len(cells))
    same_direction = 0
    if n_cells and population_radial != 0:
        same_direction = int((np.sign(cells["radial_v"]) == np.sign(population_radial)).sum())
    pct = int(round(100 * same_direction / n_cells)) if n_cells else 0
    missing = int(cells["frames_missing"].sum()) if n_cells else 0
    if missing == 0:
        reliability = {"level": "good", "note": "Every tracked cell was followed on the frames where it appeared."}
    else:
        reliability = {
            "level": "fair" if missing > n_cells else "good",
            "note": f"{missing} frame gaps were found where a nucleus was not linked to the next frame.",
        }

    direction = "toward" if population_radial < 0 else "away from" if population_radial > 0 else "around"
    shown_pop_speed = speed_of(population_speed)
    description = (
        f"{n_cells} cells were tracked over {frame_count} frames. "
        f"Average speed is {shown_pop_speed:g} {speed_unit}. "
        f"On average they move {direction} the Point of Audio. "
        f"{pct}% of cells follow that direction. "
        f"We found {len(groups)} groups."
    )
    summary = {
        "units": {"speed": speed_unit, "angular": angular_unit},
        "population": {
            "n_cells": n_cells,
            "duration_min": _num(duration, 2),
            "mean_speed": shown_pop_speed,
            "mean_radial_v": speed_of(population_radial),
            "mean_angular_v": angle_of(population_angular),
            "pct_main_pattern": pct,
            "n_clusters": len(groups),
            "description": description,
        },
        "reliability": reliability,
        "clusters": [{key: value for key, value in group.items() if key != "_ids"} for group in groups],
        "cells": [],
    }
    traces = {
        "width": int(width),
        "height": int(height),
        "poa": {"x": poa_x, "y": poa_y},
        "clusters": [],
        "description": f"Traces of {n_cells} cells over {frame_count} frames, colored by group, with the Point of Audio marked.",
    }
    for group in groups:
        paths = []
        for cell_id in group["cell_ids"]:
            path = tracks[tracks["cell_id"] == cell_id].sort_values("frame")
            framed = path[["frame", "x", "y"]].to_numpy(dtype=float)
            if len(framed) > 50:
                picked = np.linspace(0, len(framed) - 1, 50).astype(int)
                framed = framed[picked]
            paths.append([[_num(x, 1), _num(y, 1), int(frame)] for frame, x, y in framed])
        traces["clusters"].append(
            {"cluster_id": group["cluster_id"], "color": group["color"], "paths": paths}
        )
    if not tracks.empty:
        traces["max_frame"] = int(tracks["frame"].max())
    return summary, traces, motion
