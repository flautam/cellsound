#!/usr/bin/env python3
"""Overnight nucleus-tracking optimization for the four Coder sample videos.

Phases:
  1) baseline  – current Cellpose+Trackastra pipeline (CPU), short clips
  2) diagnose  – why short tracks end
  3) detect    – detection sweep with cached masks
  4) link      – linking sweep on cached detections
  5) final     – best configs on longer clips + morning report

Resume-safe: every experiment appends to a CSV and skips completed keys.
Interpolated gap-bridge positions are flagged and excluded from observed counts.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from cellpose import models
from scipy.optimize import linear_sum_assignment
from skimage.measure import regionprops
from trackastra.model import Trackastra
from trackastra.tracking.utils import graph_to_napari_tracks

# backend/ on path when run as python backend/overnight_optimize.py
sys.path.insert(0, str(Path(__file__).resolve().parent))
from track import center_crop  # noqa: E402
from video import load_video  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = ROOT / "output" / "overnight"
LOG = OUT / "run.log"
STATE = OUT / "state.json"
RESULTS = OUT / "results.csv"
REPORT = OUT / "MORNING_REPORT.md"

CLIP_FRAMES = 30
CLIP_CROP = 256
FINAL_FRAMES = 60
LONG_FRAC = 0.50
TARGET_FRAC = 0.75


def log(msg: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    line = f"{datetime.now(timezone.utc).isoformat()} {msg}"
    print(line, flush=True)
    with LOG.open("a") as f:
        f.write(line + "\n")


def load_state() -> dict:
    if STATE.exists():
        return json.loads(STATE.read_text())
    return {"done": []}


def save_state(state: dict) -> None:
    STATE.write_text(json.dumps(state, indent=2))


def mark_done(state: dict, key: str) -> None:
    if key not in state["done"]:
        state["done"].append(key)
        save_state(state)


def is_done(state: dict, key: str) -> bool:
    return key in state.get("done", [])


def append_result(row: dict) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame([row])
    if RESULTS.exists():
        df.to_csv(RESULTS, mode="a", header=False, index=False)
    else:
        df.to_csv(RESULTS, index=False)


def video_paths() -> list[Path]:
    paths = sorted(DATA.glob("*.mp4"))
    if len(paths) < 1:
        raise FileNotFoundError(f"No MP4s in {DATA}")
    return paths


def prep_video(path: Path, frames: int, crop: int) -> tuple[np.ndarray, dict]:
    raw = load_video(path)
    meta = {
        "path": str(path),
        "name": path.name,
        "full_frames": int(raw.shape[0]),
        "full_height": int(raw.shape[1]),
        "full_width": int(raw.shape[2]),
        "dtype": str(raw.dtype),
        "min": float(np.min(raw)),
        "max": float(np.max(raw)),
        "frame_interval": None,  # unknown for these MP4s
        "clip_frames": frames,
        "clip_crop": crop,
    }
    video = raw[:frames]
    video = center_crop(video, crop)
    meta["used_shape"] = list(video.shape)
    return video.astype(np.float32), meta


def normalize_frame(frame: np.ndarray) -> np.ndarray:
    f = frame.astype(np.float32)
    lo, hi = np.percentile(f, (1, 99.8))
    if hi <= lo:
        hi = lo + 1.0
    f = np.clip((f - lo) / (hi - lo), 0, 1)
    return f


def preprocess(video: np.ndarray, mode: str) -> np.ndarray:
    out = []
    for frame in video:
        f = normalize_frame(frame)
        if mode == "invert":
            f = 1.0 - f
        elif mode == "invert_clahe":
            from skimage.exposure import equalize_adapthist

            f = 1.0 - f
            f = equalize_adapthist(f, clip_limit=0.02)
        elif mode == "clahe":
            from skimage.exposure import equalize_adapthist

            f = equalize_adapthist(f, clip_limit=0.02)
        elif mode != "raw":
            raise ValueError(mode)
        out.append((f * 255.0).astype(np.float32))
    return np.stack(out)


def segment(
    video: np.ndarray,
    diameter: float | None,
    cellprob_threshold: float,
    flow_threshold: float,
) -> np.ndarray:
    model = models.CellposeModel(gpu=False)
    frames = [frame for frame in video]
    kwargs = {
        "diameter": diameter,
        "cellprob_threshold": cellprob_threshold,
        "flow_threshold": flow_threshold,
    }
    masks, _flows, _styles = model.eval(frames, **kwargs)
    return np.stack(masks).astype(np.int32)


def masks_to_detections(masks: np.ndarray) -> list[pd.DataFrame]:
    """Per-frame detection tables: label, x, y, area. x is horizontal."""
    dets = []
    for t, mask in enumerate(masks):
        rows = []
        for prop in regionprops(mask):
            y, x = prop.centroid
            rows.append(
                {
                    "frame": t,
                    "label": int(prop.label),
                    "x": float(x),
                    "y": float(y),
                    "area": float(prop.area),
                }
            )
        dets.append(pd.DataFrame(rows))
    return dets


def trackastra_link(video: np.ndarray, masks: np.ndarray, mode: str = "greedy") -> pd.DataFrame:
    model = Trackastra.from_pretrained("general_2d", device="cpu")
    track_graph, _ = model.track(video, masks, mode=mode)
    rows, _graph, _props = graph_to_napari_tracks(track_graph)
    if len(rows) == 0:
        return pd.DataFrame(columns=["cell_id", "frame", "x", "y", "observed"])
    table = pd.DataFrame(rows, columns=["cell_id", "frame", "y", "x"])
    table = table[["cell_id", "frame", "x", "y"]]
    table["observed"] = True
    return table.sort_values(["cell_id", "frame"]).reset_index(drop=True)


def nn_link(
    dets: list[pd.DataFrame],
    max_dist: float,
    max_gap: int,
) -> pd.DataFrame:
    """Nearest-neighbor / Hungarian linker with optional gap closing.

    Gap-bridged rows are marked observed=False (interpolated) and must not
    count toward observed-frame coverage metrics.
    """
    if not dets or all(d.empty for d in dets):
        return pd.DataFrame(columns=["cell_id", "frame", "x", "y", "observed"])

    next_id = 1
    # active: list of dicts with id, x, y, last_frame
    active: list[dict] = []
    records: list[dict] = []

    for t, det in enumerate(dets):
        if det.empty:
            # age out
            active = [a for a in active if t - a["last_frame"] <= max_gap]
            continue

        xy = det[["x", "y"]].to_numpy(dtype=float)
        n_det = len(xy)
        # candidates: active tracks within gap
        cand = [a for a in active if t - a["last_frame"] <= max_gap]
        assigned_det = set()
        assigned_trk = set()

        if cand:
            cost = np.full((len(cand), n_det), 1e6, dtype=float)
            for i, a in enumerate(cand):
                dt = t - a["last_frame"]
                # constant-velocity prediction if we have prev
                px, py = a["x"], a["y"]
                if a.get("vx") is not None and dt > 0:
                    px = a["x"] + a["vx"] * dt
                    py = a["y"] + a["vy"] * dt
                d = np.hypot(xy[:, 0] - px, xy[:, 1] - py)
                # allow larger distance for gaps
                limit = max_dist * (1.0 + 0.5 * (dt - 1))
                for j, dist in enumerate(d):
                    if dist <= limit:
                        cost[i, j] = dist
            ri, cj = linear_sum_assignment(cost)
            for i, j in zip(ri, cj):
                if cost[i, j] >= 1e5:
                    continue
                a = cand[i]
                dt = t - a["last_frame"]
                x, y = float(xy[j, 0]), float(xy[j, 1])
                # interpolate missing frames if gap > 1
                if dt > 1:
                    for g in range(1, dt):
                        alpha = g / dt
                        records.append(
                            {
                                "cell_id": a["id"],
                                "frame": a["last_frame"] + g,
                                "x": a["x"] + alpha * (x - a["x"]),
                                "y": a["y"] + alpha * (y - a["y"]),
                                "observed": False,
                            }
                        )
                vx = (x - a["x"]) / max(dt, 1)
                vy = (y - a["y"]) / max(dt, 1)
                a.update({"x": x, "y": y, "last_frame": t, "vx": vx, "vy": vy})
                records.append(
                    {
                        "cell_id": a["id"],
                        "frame": t,
                        "x": x,
                        "y": y,
                        "observed": True,
                    }
                )
                assigned_det.add(j)
                assigned_trk.add(id(a))

        # new tracks for unmatched detections
        for j in range(n_det):
            if j in assigned_det:
                continue
            x, y = float(xy[j, 0]), float(xy[j, 1])
            active.append(
                {
                    "id": next_id,
                    "x": x,
                    "y": y,
                    "last_frame": t,
                    "vx": 0.0,
                    "vy": 0.0,
                }
            )
            records.append(
                {
                    "cell_id": next_id,
                    "frame": t,
                    "x": x,
                    "y": y,
                    "observed": True,
                }
            )
            next_id += 1

        # drop stale
        active = [a for a in active if t - a["last_frame"] <= max_gap]

    return pd.DataFrame(records).sort_values(["cell_id", "frame"]).reset_index(drop=True)


def track_metrics(table: pd.DataFrame, n_frames: int) -> dict:
    if table.empty:
        return {
            "n_tracks": 0,
            "median_len": 0.0,
            "median_frac": 0.0,
            "frac_tracks_ge_50": 0.0,
            "frac_tracks_ge_75": 0.0,
            "obs_cell_frames": 0,
            "obs_cell_frames_ge_50": 0,
            "mean_cells_per_frame": 0.0,
            "cv_cells_per_frame": 0.0,
            "min_cells": 0,
            "max_cells": 0,
        }
    obs = table[table.get("observed", True) == True]  # noqa: E712
    lengths = obs.groupby("cell_id").size()
    per_frame = obs.groupby("frame").size()
    ge50 = lengths[lengths >= LONG_FRAC * n_frames]
    ge75 = lengths[lengths >= TARGET_FRAC * n_frames]
    mean_c = float(per_frame.mean()) if len(per_frame) else 0.0
    std_c = float(per_frame.std(ddof=0)) if len(per_frame) else 0.0
    return {
        "n_tracks": int(len(lengths)),
        "median_len": float(lengths.median()),
        "median_frac": float(lengths.median()) / n_frames,
        "frac_tracks_ge_50": float((lengths >= LONG_FRAC * n_frames).mean()),
        "frac_tracks_ge_75": float((lengths >= TARGET_FRAC * n_frames).mean()),
        "obs_cell_frames": int(len(obs)),
        "obs_cell_frames_ge_50": int(ge50.sum()) if len(ge50) else 0,
        "mean_cells_per_frame": mean_c,
        "cv_cells_per_frame": (std_c / mean_c) if mean_c > 0 else 0.0,
        "min_cells": int(per_frame.min()) if len(per_frame) else 0,
        "max_cells": int(per_frame.max()) if len(per_frame) else 0,
    }


def save_overlay(
    video: np.ndarray,
    table: pd.DataFrame,
    path: Path,
    frame_index: int | None = None,
) -> None:
    """Save a simple RGB overlay PNG with IDs (no matplotlib required)."""
    from PIL import Image, ImageDraw

    t = frame_index if frame_index is not None else min(5, video.shape[0] - 1)
    frame = video[t]
    f = normalize_frame(frame)
    rgb = np.stack([f, f, f], axis=-1)
    rgb = (rgb * 255).astype(np.uint8)
    img = Image.fromarray(rgb)
    draw = ImageDraw.Draw(img)
    if not table.empty:
        sub = table[(table["frame"] == t) & (table.get("observed", True) == True)]  # noqa: E712
        for _, row in sub.iterrows():
            x, y = float(row["x"]), float(row["y"])
            cid = int(row["cell_id"])
            draw.ellipse([x - 3, y - 3, x + 3, y + 3], outline=(0, 255, 0))
            draw.text((x + 4, y - 4), str(cid), fill=(255, 64, 64))
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)


def diagnose_short_tracks(
    video: np.ndarray,
    masks: np.ndarray,
    table: pd.DataFrame,
    out_dir: Path,
    n_examples: int = 8,
) -> list[dict]:
    """Classify why short tracks end."""
    out_dir.mkdir(parents=True, exist_ok=True)
    if table.empty:
        return []
    obs = table[table.get("observed", True) == True]  # noqa: E712
    lengths = obs.groupby("cell_id").size()
    short_ids = lengths[lengths <= 5].index.tolist()[:n_examples]
    examples = []
    dets = masks_to_detections(masks)

    for cid in short_ids:
        tr = obs[obs["cell_id"] == cid].sort_values("frame")
        last = tr.iloc[-1]
        t = int(last["frame"])
        x, y = float(last["x"]), float(last["y"])
        reason = "unknown"
        detail = ""
        if t >= video.shape[0] - 1:
            reason = "left_end_of_clip"
        else:
            # detections next frame near last position
            nxt = dets[t + 1] if t + 1 < len(dets) else pd.DataFrame()
            if nxt.empty:
                reason = "no_detections_next_frame"
            else:
                d = np.hypot(nxt["x"] - x, nxt["y"] - y)
                j = int(d.idxmin())
                mind = float(d.min())
                # was this detection claimed by another track?
                near_tracks = obs[
                    (obs["frame"] == t + 1)
                    & (np.hypot(obs["x"] - x, obs["y"] - y) < 20)
                ]
                if mind < 12 and len(near_tracks) and int(near_tracks.iloc[0]["cell_id"]) != cid:
                    reason = "id_switch_or_reassigned"
                    detail = f"nearest_det_dist={mind:.1f}, other_id={int(near_tracks.iloc[0]['cell_id'])}"
                elif mind < 12:
                    reason = "detected_but_unlinked"
                    detail = f"nearest_det_dist={mind:.1f}"
                elif mind < 25:
                    reason = "possible_merge_or_jump"
                    detail = f"nearest_det_dist={mind:.1f}"
                else:
                    # look at local intensity - still a dark/bright blob?
                    reason = "missed_detection_or_left_fov"
                    detail = f"nearest_det_dist={mind:.1f}"

        examples.append(
            {
                "cell_id": int(cid),
                "last_frame": t,
                "x": x,
                "y": y,
                "track_len": int(len(tr)),
                "reason": reason,
                "detail": detail,
            }
        )
        # save before/after frames
        for tt, tag in [(t, "before"), (min(t + 1, video.shape[0] - 1), "after")]:
            save_overlay(
                video,
                table,
                out_dir / f"short_id{cid}_{tag}_f{tt}.png",
                frame_index=tt,
            )

    (out_dir / "short_track_examples.json").write_text(json.dumps(examples, indent=2))
    return examples


def score_row(m: dict) -> float:
    """Higher is better. Emphasizes long-track coverage and stable counts."""
    return (
        2.0 * m["frac_tracks_ge_75"]
        + 1.5 * m["frac_tracks_ge_50"]
        + 1.0 * m["median_frac"]
        + 0.5 * (m["obs_cell_frames_ge_50"] / max(m["obs_cell_frames"], 1))
        - 0.5 * min(m["cv_cells_per_frame"], 1.0)
    )


@dataclass
class DetectCfg:
    preprocess: str = "raw"
    diameter: float | None = None
    cellprob_threshold: float = 0.0
    flow_threshold: float = 0.4


@dataclass
class LinkCfg:
    method: str = "trackastra_greedy"  # or nn
    max_dist: float = 20.0
    max_gap: int = 0
    trackastra_mode: str = "greedy"


def run_pipeline(
    video: np.ndarray,
    detect: DetectCfg,
    link: LinkCfg,
    cache_dir: Path | None = None,
    cache_key: str | None = None,
) -> tuple[pd.DataFrame, np.ndarray, float]:
    t0 = time.time()
    masks = None
    if cache_dir and cache_key:
        mask_path = cache_dir / f"{cache_key}_masks.npz"
        if mask_path.exists():
            masks = np.load(mask_path)["masks"]
    if masks is None:
        proc = preprocess(video, detect.preprocess)
        masks = segment(
            proc,
            detect.diameter,
            detect.cellprob_threshold,
            detect.flow_threshold,
        )
        if cache_dir and cache_key:
            cache_dir.mkdir(parents=True, exist_ok=True)
            np.savez_compressed(cache_dir / f"{cache_key}_masks.npz", masks=masks)

    if link.method.startswith("trackastra"):
        # Trackastra wants intensities; use preprocessed if available
        proc = preprocess(video, detect.preprocess)
        table = trackastra_link(proc, masks, mode=link.trackastra_mode)
    else:
        dets = masks_to_detections(masks)
        table = nn_link(dets, max_dist=link.max_dist, max_gap=link.max_gap)
    runtime = time.time() - t0
    return table, masks, runtime


def phase_baseline(state: dict, paths: list[Path]) -> None:
    log("=== PHASE baseline ===")
    for path in paths:
        key = f"baseline::{path.stem}"
        if is_done(state, key):
            log(f"skip {key}")
            continue
        log(f"baseline {path.name}")
        video, meta = prep_video(path, CLIP_FRAMES, CLIP_CROP)
        detect = DetectCfg()  # raw defaults = "unchanged" intent on clip
        link = LinkCfg(method="trackastra_greedy", trackastra_mode="greedy")
        # Truly unchanged entrypoint uses gpu=True in track.py; we force CPU
        # for Coder survival but keep model/linker defaults.
        table, masks, runtime = run_pipeline(
            video,
            detect,
            link,
            cache_dir=OUT / "cache" / path.stem,
            cache_key="baseline_raw",
        )
        m = track_metrics(table, video.shape[0])
        exp_dir = OUT / "baseline" / path.stem
        exp_dir.mkdir(parents=True, exist_ok=True)
        table.to_csv(exp_dir / "tracks.csv", index=False)
        (exp_dir / "meta.json").write_text(json.dumps({**meta, **asdict(detect), **asdict(link), **m, "runtime_s": runtime}, indent=2))
        np.savez_compressed(exp_dir / "masks.npz", masks=masks)
        save_overlay(video, table, exp_dir / "overlay_f5.png", 5)
        examples = diagnose_short_tracks(video, masks, table, exp_dir / "diagnose")
        reasons = pd.Series([e["reason"] for e in examples]).value_counts().to_dict() if examples else {}
        (exp_dir / "diagnose_summary.json").write_text(json.dumps(reasons, indent=2))
        append_result(
            {
                "phase": "baseline",
                "video": path.name,
                "key": key,
                "score": score_row(m),
                "runtime_s": runtime,
                **{f"m_{k}": v for k, v in m.items()},
                "preprocess": detect.preprocess,
                "diameter": detect.diameter,
                "linker": link.method,
                "max_dist": link.max_dist,
                "max_gap": link.max_gap,
            }
        )
        mark_done(state, key)
        log(f"baseline done {path.name} score={score_row(m):.3f} median_frac={m['median_frac']:.3f}")


def phase_detect_sweep(state: dict, paths: list[Path]) -> None:
    log("=== PHASE detect sweep ===")
    preprocesses = ["raw", "invert", "invert_clahe"]
    diameters = [None, 30.0, 40.0, 50.0]
    for path in paths:
        video, _meta = prep_video(path, CLIP_FRAMES, CLIP_CROP)
        for prep in preprocesses:
            for diam in diameters:
                key = f"detect::{path.stem}::{prep}::d{diam}"
                if is_done(state, key):
                    continue
                log(f"detect {key}")
                detect = DetectCfg(preprocess=prep, diameter=diam)
                link = LinkCfg(method="trackastra_greedy")
                try:
                    table, masks, runtime = run_pipeline(
                        video,
                        detect,
                        link,
                        cache_dir=OUT / "cache" / path.stem,
                        cache_key=f"{prep}_d{diam}",
                    )
                    m = track_metrics(table, video.shape[0])
                    exp_dir = OUT / "detect" / path.stem / f"{prep}_d{diam}"
                    exp_dir.mkdir(parents=True, exist_ok=True)
                    table.to_csv(exp_dir / "tracks.csv", index=False)
                    save_overlay(video, table, exp_dir / "overlay_f5.png", 5)
                    append_result(
                        {
                            "phase": "detect",
                            "video": path.name,
                            "key": key,
                            "score": score_row(m),
                            "runtime_s": runtime,
                            **{f"m_{k}": v for k, v in m.items()},
                            "preprocess": prep,
                            "diameter": diam,
                            "linker": link.method,
                            "max_dist": link.max_dist,
                            "max_gap": link.max_gap,
                        }
                    )
                    mark_done(state, key)
                    log(f"detect done {key} score={score_row(m):.3f}")
                except Exception as e:
                    log(f"detect FAIL {key}: {e}")
                    traceback.print_exc()
                    mark_done(state, key)  # avoid infinite retry on hard fail


def best_detect_for_video(video_name: str) -> DetectCfg:
    if not RESULTS.exists():
        return DetectCfg(preprocess="invert", diameter=40.0)
    df = pd.read_csv(RESULTS)
    sub = df[(df["phase"].isin(["baseline", "detect"])) & (df["video"] == video_name)]
    if sub.empty:
        return DetectCfg(preprocess="invert", diameter=40.0)
    row = sub.sort_values("score", ascending=False).iloc[0]
    diam = row.get("diameter")
    if pd.isna(diam):
        diam = None
    else:
        diam = float(diam)
    return DetectCfg(preprocess=str(row["preprocess"]), diameter=diam)


def phase_link_sweep(state: dict, paths: list[Path]) -> None:
    log("=== PHASE link sweep ===")
    for path in paths:
        video, _meta = prep_video(path, CLIP_FRAMES, CLIP_CROP)
        detect = best_detect_for_video(path.name)
        cache_key = f"{detect.preprocess}_d{detect.diameter}"
        # ensure masks cached
        run_pipeline(
            video,
            detect,
            LinkCfg(method="trackastra_greedy"),
            cache_dir=OUT / "cache" / path.stem,
            cache_key=cache_key,
        )
        # estimate displacement from baseline tracks if present
        base_tracks = OUT / "baseline" / path.stem / "tracks.csv"
        max_dists = [12.0, 20.0, 30.0, 40.0]
        if base_tracks.exists():
            bt = pd.read_csv(base_tracks)
            if not bt.empty:
                disp = []
                for _, g in bt.groupby("cell_id"):
                    g = g.sort_values("frame")
                    if len(g) >= 2:
                        d = np.hypot(np.diff(g["x"]), np.diff(g["y"]))
                        disp.extend(d.tolist())
                if disp:
                    p90 = float(np.percentile(disp, 90))
                    max_dists = sorted(set([max(8.0, p90), p90 * 1.5, p90 * 2.0, 30.0]))

        link_cfgs = [
            LinkCfg(method="trackastra_greedy", trackastra_mode="greedy"),
            LinkCfg(method="trackastra_greedy_nodiv", trackastra_mode="greedy_nodiv"),
        ]
        for md in max_dists:
            for gap in [0, 1, 2]:
                link_cfgs.append(
                    LinkCfg(method="nn", max_dist=float(md), max_gap=gap)
                )

        for link in link_cfgs:
            key = f"link::{path.stem}::{link.method}::d{link.max_dist}::g{link.max_gap}::{link.trackastra_mode}"
            if is_done(state, key):
                continue
            log(f"link {key}")
            try:
                table, masks, runtime = run_pipeline(
                    video,
                    detect,
                    link,
                    cache_dir=OUT / "cache" / path.stem,
                    cache_key=cache_key,
                )
                m = track_metrics(table, video.shape[0])
                exp_dir = OUT / "link" / path.stem / key.replace("::", "__")
                exp_dir.mkdir(parents=True, exist_ok=True)
                table.to_csv(exp_dir / "tracks.csv", index=False)
                save_overlay(video, table, exp_dir / "overlay_f5.png", 5)
                append_result(
                    {
                        "phase": "link",
                        "video": path.name,
                        "key": key,
                        "score": score_row(m),
                        "runtime_s": runtime,
                        **{f"m_{k}": v for k, v in m.items()},
                        "preprocess": detect.preprocess,
                        "diameter": detect.diameter,
                        "linker": link.method,
                        "max_dist": link.max_dist,
                        "max_gap": link.max_gap,
                    }
                )
                mark_done(state, key)
                log(f"link done {key} score={score_row(m):.3f}")
            except Exception as e:
                log(f"link FAIL {key}: {e}")
                traceback.print_exc()
                mark_done(state, key)


def best_config_overall() -> tuple[DetectCfg, LinkCfg]:
    if not RESULTS.exists():
        return DetectCfg(preprocess="invert", diameter=40.0), LinkCfg(method="nn", max_dist=25.0, max_gap=1)
    df = pd.read_csv(RESULTS)
    # prefer configs that appear strong across videos: average score by config signature
    df = df[df["phase"].isin(["detect", "link", "baseline", "final"])]
    if df.empty:
        return DetectCfg(preprocess="invert", diameter=40.0), LinkCfg(method="nn", max_dist=25.0, max_gap=1)

    def sig(r):
        return (
            str(r.get("preprocess", "raw")),
            r.get("diameter"),
            str(r.get("linker", "trackastra_greedy")),
            float(r.get("max_dist", 20) or 20),
            int(r.get("max_gap", 0) or 0),
        )

    df = df.copy()
    df["sig"] = df.apply(sig, axis=1)
    # cross-video mean for signatures present in link/detect
    ranked = (
        df.groupby("sig")["score"]
        .agg(["mean", "count"])
        .sort_values(["mean", "count"], ascending=False)
    )
    best_sig = ranked.index[0]
    prep, diam, linker, max_dist, max_gap = best_sig
    if pd.isna(diam):
        diam = None
    else:
        diam = float(diam)
    detect = DetectCfg(preprocess=prep, diameter=diam)
    if str(linker).startswith("trackastra"):
        link = LinkCfg(method="trackastra_greedy", trackastra_mode="greedy")
    else:
        link = LinkCfg(method="nn", max_dist=float(max_dist), max_gap=int(max_gap))
    return detect, link


def phase_final(state: dict, paths: list[Path]) -> None:
    log("=== PHASE final ===")
    detect, link = best_config_overall()
    log(f"winner config detect={detect} link={link}")
    for path in paths:
        key = f"final::{path.stem}"
        if is_done(state, key):
            continue
        log(f"final {path.name}")
        video, meta = prep_video(path, FINAL_FRAMES, CLIP_CROP)
        table, masks, runtime = run_pipeline(
            video,
            detect,
            link,
            cache_dir=OUT / "cache" / path.stem,
            cache_key=f"final_{detect.preprocess}_d{detect.diameter}",
        )
        m = track_metrics(table, video.shape[0])
        exp_dir = OUT / "final" / path.stem
        exp_dir.mkdir(parents=True, exist_ok=True)
        table.to_csv(exp_dir / "tracks.csv", index=False)
        (exp_dir / "config.json").write_text(
            json.dumps({"detect": asdict(detect), "link": asdict(link), "meta": meta, "metrics": m, "runtime_s": runtime}, indent=2)
        )
        save_overlay(video, table, exp_dir / "overlay_f5.png", 5)
        save_overlay(video, table, exp_dir / "overlay_f20.png", min(20, video.shape[0] - 1))
        # baseline overlay copy pointer for side-by-side
        append_result(
            {
                "phase": "final",
                "video": path.name,
                "key": key,
                "score": score_row(m),
                "runtime_s": runtime,
                **{f"m_{k}": v for k, v in m.items()},
                "preprocess": detect.preprocess,
                "diameter": detect.diameter,
                "linker": link.method,
                "max_dist": link.max_dist,
                "max_gap": link.max_gap,
            }
        )
        mark_done(state, key)
        log(f"final done {path.name} score={score_row(m):.3f}")


def write_morning_report() -> None:
    log("=== writing morning report ===")
    lines = [
        "# Morning tracking comparison",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        "",
        "Metrics marked **PROXY** are automated (no manual GT). Identity switch counts are diagnostic heuristics, not verified GT.",
        "",
        "Interpolated gap-bridge points have `observed=False` and are excluded from observed-frame counts.",
        "",
    ]
    if not RESULTS.exists():
        lines.append("No results yet.")
        REPORT.write_text("\n".join(lines))
        return

    df = pd.read_csv(RESULTS)
    detect, link = best_config_overall()
    lines += [
        "## Winning configuration (cross-video)",
        "",
        "```",
        f"detect: {asdict(detect)}",
        f"link:   {asdict(link)}",
        "```",
        "",
        "Reproduce finalist on a video:",
        "",
        "```bash",
        "source .venv/bin/activate",
        "python backend/overnight_optimize.py --phase final",
        "```",
        "",
        "## Per-video baseline vs best (PROXY)",
        "",
        "| video | baseline median_frac | best median_frac | baseline ge50 cell-frames | best ge50 cell-frames | baseline n_tracks | best n_tracks |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for video in sorted(df["video"].unique()):
        sub = df[df["video"] == video]
        base = sub[sub["phase"] == "baseline"]
        best = sub.sort_values("score", ascending=False).iloc[0]
        b = base.iloc[0] if len(base) else None
        lines.append(
            "| {video} | {bm:.3f} | {bestm:.3f} | {bg} | {bestg} | {bn} | {bestn} |".format(
                video=video,
                bm=(b["m_median_frac"] if b is not None else float("nan")),
                bestm=best["m_median_frac"],
                bg=(int(b["m_obs_cell_frames_ge_50"]) if b is not None else 0),
                bestg=int(best["m_obs_cell_frames_ge_50"]),
                bn=(int(b["m_n_tracks"]) if b is not None else 0),
                bestn=int(best["m_n_tracks"]),
            )
        )

    lines += [
        "",
        "## Overlays",
        "",
        "Side-by-side style paths (open both):",
        "",
        "- Baseline: `output/overnight/baseline/<video>/overlay_f5.png`",
        "- Best final: `output/overnight/final/<video>/overlay_f5.png`",
        "- Short-track failure examples: `output/overnight/baseline/<video>/diagnose/`",
        "",
        "## Biggest remaining failure mode",
        "",
    ]
    # summarize diagnose reasons
    reason_counts: dict[str, int] = {}
    for p in (OUT / "baseline").glob("*/diagnose/short_track_examples.json"):
        for ex in json.loads(p.read_text()):
            reason_counts[ex["reason"]] = reason_counts.get(ex["reason"], 0) + 1
    if reason_counts:
        top = sorted(reason_counts.items(), key=lambda kv: -kv[1])
        lines.append("Most common short-track end reasons (heuristic):")
        lines.append("")
        for r, c in top:
            lines.append(f"- {r}: {c}")
        lines.append("")
        lines.append(f"**Primary remaining issue:** `{top[0][0]}`")
    else:
        lines.append("No diagnose summaries yet.")

    lines += [
        "",
        "## Note on nucleus lifetime ID metric",
        "",
        "Manual nucleus lifetime fractions were not fully labeled overnight. Use overlays + diagnose examples for review. Automated proxies above emphasize long single-ID tracks and stable detections.",
        "",
    ]
    REPORT.write_text("\n".join(lines))
    log(f"wrote {REPORT}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--phase",
        default="all",
        choices=["all", "baseline", "detect", "link", "final", "report"],
    )
    args = parser.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    state = load_state()
    paths = video_paths()
    log(f"videos: {[p.name for p in paths]}")
    log(f"phase={args.phase}")

    try:
        if args.phase in ("all", "baseline"):
            phase_baseline(state, paths)
        if args.phase in ("all", "detect"):
            phase_detect_sweep(state, paths)
        if args.phase in ("all", "link"):
            phase_link_sweep(state, paths)
        if args.phase in ("all", "final"):
            phase_final(state, paths)
        if args.phase in ("all", "report", "final"):
            write_morning_report()
        log("ALL REQUESTED PHASES COMPLETE")
    except Exception as e:
        log(f"FATAL: {e}")
        traceback.print_exc()
        write_morning_report()
        raise


if __name__ == "__main__":
    main()
