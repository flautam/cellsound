"""Label each cluster with a movement type.

Reads an existing job folder. Does not change the tracking or analysis endpoints.
Writes output/jobs/<id>/motion_types.json.

Image coordinates: x grows to the right, y grows downward.
Up is toward the top of the image. Clockwise is clockwise on the image.
Inward, outward, and circling use the Point of Audio when one was saved,
otherwise the center of the image.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
JOBS = ROOT / "output" / "jobs"

# Speeds are pixels per frame. Angles are radians per frame.
# A score reaches 1 at the matching threshold. Raise a number to make that
# label harder to earn.
THRESHOLDS = {
    "still_speed": 0.75,
    "direction_speed": 2.0,
    "radial_speed": 1.0,
    "angular_speed": 0.05,
    # A second label is included in search when it is at least this close
    # to the winning score, and not below close_floor.
    "close_gap": 0.15,
    "close_floor": 0.4,
}

TYPES = (
    "still",
    "inward",
    "outward",
    "left",
    "right",
    "up",
    "down",
    "circling clockwise",
    "circling counterclockwise",
)

SENTENCES = {
    "still": "These cells mostly stay still.",
    "inward": "These cells mostly move in toward the {place}.",
    "outward": "These cells mostly move out away from the {place}.",
    "left": "These cells mostly move left.",
    "right": "These cells mostly move right.",
    "up": "These cells mostly move up, toward the top of the image.",
    "down": "These cells mostly move down, toward the bottom of the image.",
    "circling clockwise": "These cells mostly circle the {place} clockwise.",
    "circling counterclockwise": "These cells mostly circle the {place} counterclockwise.",
}


def _clip(value: float) -> float:
    return float(min(1.0, max(0.0, value)))


def _scores(vx: float, vy: float, speed: float, radial: float, angular: float) -> dict:
    limits = THRESHOLDS
    still = _clip(1.0 - speed / limits["still_speed"]) if limits["still_speed"] else 0.0
    moving = 1.0 - still
    direction = limits["direction_speed"] or 1.0
    radial_limit = limits["radial_speed"] or 1.0
    angular_limit = limits["angular_speed"] or 1.0
    return {
        "still": round(still, 3),
        "left": round(moving * _clip(-vx / direction), 3),
        "right": round(moving * _clip(vx / direction), 3),
        "up": round(moving * _clip(-vy / direction), 3),
        "down": round(moving * _clip(vy / direction), 3),
        "inward": round(moving * _clip(-radial / radial_limit), 3),
        "outward": round(moving * _clip(radial / radial_limit), 3),
        "circling clockwise": round(moving * _clip(angular / angular_limit), 3),
        "circling counterclockwise": round(moving * _clip(-angular / angular_limit), 3),
    }


def _main_type(scores: dict) -> str:
    best = max(TYPES, key=lambda name: (scores.get(name, 0.0), -TYPES.index(name)))
    return best


def _search_types(scores: dict, main: str) -> list[str]:
    """Labels close to the winner, so a search for either one can find this cluster."""
    top = float(scores.get(main, 0.0))
    gap = float(THRESHOLDS["close_gap"])
    floor = float(THRESHOLDS["close_floor"])
    names = []
    for name in TYPES:
        score = float(scores.get(name, 0.0))
        if name == main or (score >= floor and top - score <= gap):
            names.append(name)
    return names


def _angular_by_cell(tracks: pd.DataFrame, poa_x: float, poa_y: float) -> dict:
    """Positive means clockwise on the image, in radians per frame."""
    turns = {}
    for cell_id, path in tracks.groupby("cell_id"):
        path = path.sort_values("frame")
        frames = path["frame"].to_numpy()
        theta = np.atan2(path["y"].to_numpy() - poa_y, path["x"].to_numpy() - poa_x)
        deltas = []
        for index in range(1, len(path)):
            if frames[index] - frames[index - 1] != 1:
                continue
            delta = theta[index] - theta[index - 1]
            deltas.append(float(np.atan2(np.sin(delta), np.cos(delta))))
        turns[int(cell_id)] = float(np.mean(deltas)) if deltas else 0.0
    return turns


def _reference(folder: Path) -> tuple[float, float, str]:
    meta = json.loads((folder / "meta.json").read_text()) if (folder / "meta.json").exists() else {}
    width = float(meta.get("width") or 0)
    height = float(meta.get("height") or 0)
    settings = {}
    if (folder / "settings.json").exists():
        settings = json.loads((folder / "settings.json").read_text())
    poa = settings.get("poa") or {}
    if poa.get("x") is not None and poa.get("y") is not None:
        return float(poa["x"]), float(poa["y"]), "Point of Audio"
    return width / 2.0, height / 2.0, "center"


def build_motion_types(folder: Path) -> dict:
    folder = Path(folder)
    summary = json.loads((folder / "summary.json").read_text())
    motion = pd.read_csv(folder / "motion.csv")
    tracks = pd.read_csv(folder / "tracks.csv")
    poa_x, poa_y, place = _reference(folder)
    angles = _angular_by_cell(tracks, poa_x, poa_y)
    clusters = []
    for cluster in sorted(summary.get("clusters", []), key=lambda item: int(item["cluster_id"])):
        cell_ids = [int(value) for value in cluster.get("cell_ids") or []]
        rows = motion[motion["cell_id"].isin(cell_ids)] if cell_ids else motion.iloc[0:0]
        steps = rows.dropna(subset=["speed"]) if not rows.empty else rows
        per_cell = []
        for cell_id, group in steps.groupby("cell_id") if not steps.empty else []:
            per_cell.append(
                {
                    "vx": float(group["vx"].mean()),
                    "vy": float(group["vy"].mean()),
                    "speed": float(group["speed"].mean()),
                    "radial": float(group["radial_v"].mean()),
                    "angular": angles.get(int(cell_id), 0.0),
                }
            )
        if per_cell:
            vx = float(np.mean([item["vx"] for item in per_cell]))
            vy = float(np.mean([item["vy"] for item in per_cell]))
            speed = float(np.mean([item["speed"] for item in per_cell]))
            radial = float(np.mean([item["radial"] for item in per_cell]))
            angular = float(np.mean([item["angular"] for item in per_cell]))
        else:
            vx = vy = speed = radial = angular = 0.0
        scores = _scores(vx, vy, speed, radial, angular)
        main = _main_type(scores)
        clusters.append(
            {
                "cluster_id": int(cluster["cluster_id"]),
                "n_cells": int(cluster.get("n_cells") or len(cell_ids)),
                "main_type": main,
                "search_types": _search_types(scores, main),
                "description": SENTENCES[main].format(place=place),
                "scores": scores,
            }
        )
    kind = "poa" if place == "Point of Audio" else "center"
    return {
        "job_id": folder.name,
        "reference": {"kind": kind, "x": round(poa_x, 2), "y": round(poa_y, 2)},
        "thresholds": dict(THRESHOLDS),
        "clusters": clusters,
    }


def write_motion_types(folder: Path) -> Path:
    payload = build_motion_types(folder)
    path = Path(folder) / "motion_types.json"
    path.write_text(json.dumps(payload, indent=2))
    return path


def _folder_from_arg(text: str) -> Path:
    path = Path(text)
    if path.is_dir():
        return path
    candidate = JOBS / text
    if candidate.is_dir():
        return candidate
    raise FileNotFoundError(f"No job folder at {text}")


def main():
    if len(sys.argv) != 2:
        print("Usage: python backend/motion_types.py output/jobs/<job_id>")
        sys.exit(1)
    path = write_motion_types(_folder_from_arg(sys.argv[1]))
    payload = json.loads(path.read_text())
    print(f"Saved {path}")
    for cluster in payload["clusters"]:
        print(
            f"Cluster {cluster['cluster_id']}: {cluster['main_type']} "
            f"({cluster['n_cells']} cells). {cluster['description']}"
        )


if __name__ == "__main__":
    main()
