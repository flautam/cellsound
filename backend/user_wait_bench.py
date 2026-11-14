#!/usr/bin/env python3
"""User-wait timing + quality benchmarks (overnight sweep must be paused).

Measures wall time from opening an MP4 until tracks.csv is written, broken into:
  load | preprocess | model_startup | detect | link | output

Writes:
  output/overnight/user_wait_runs.csv
  output/overnight/user_wait_summary.md
  and refreshes the user-wait section of MORNING_REPORT.md

Quality is primary. PROXY metrics (no GT): coverage (median track fraction,
obs cell-frames ≥50% lifetime) and a hop-distance ID-switch heuristic.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import traceback
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import torch
from cellpose import models
from trackastra.model import Trackastra
from trackastra.tracking.utils import graph_to_napari_tracks

sys.path.insert(0, str(Path(__file__).resolve().parent))
from overnight_optimize import (  # noqa: E402
    masks_to_detections,
    nn_link,
    track_metrics,
)
from track import CELLPOSE_MODEL, select_frames  # noqa: E402
from video import load_video  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = ROOT / "output" / "overnight"
RUNS = OUT / "user_wait_runs.csv"
SUMMARY = OUT / "user_wait_summary.md"
COMPARE = OUT / "user_wait_compare.json"
REPORT = OUT / "MORNING_REPORT.md"
LOG = OUT / "user_wait.log"

LONG_FRAC = 0.50
# User-facing CPU defaults from track.py (full-movie OOM otherwise).
DEFAULT_FRAMES = 10
DEFAULT_CROP = 256
DEFAULT_VIDEOS = ["bar15_01_20260206_bar025.20k.00.mp4"]


def log(msg: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    line = f"{datetime.now(timezone.utc).isoformat()} {msg}"
    print(line, flush=True)
    with LOG.open("a") as f:
        f.write(line + "\n")


@dataclass(frozen=True)
class Config:
    name: str
    frames: int = DEFAULT_FRAMES
    crop: int = DEFAULT_CROP
    linker: str = "trackastra"  # trackastra | nn
    diameter: float | None = None
    cellprob_threshold: float = 0.0
    flow_threshold: float = 0.4
    nn_max_dist: float = 40.0
    nn_max_gap: int = 2
    include_model_startup: bool = True  # first-run style: charge startup to wait


def video_paths(only: list[str] | None = None) -> list[Path]:
    paths = sorted(DATA.glob("*.mp4"))
    if only:
        wanted = {Path(n).name for n in only}
        paths = [p for p in paths if p.name in wanted]
        missing = wanted - {p.name for p in paths}
        if missing:
            raise FileNotFoundError(f"Missing videos: {sorted(missing)}")
    if not paths:
        raise FileNotFoundError(f"No MP4s in {DATA}")
    return paths


def id_switch_proxy(table: pd.DataFrame, max_jump_px: float = 60.0) -> int:
    """Count large frame-to-frame jumps within a track (PROXY, not GT)."""
    if table.empty:
        return 0
    obs = table[table.get("observed", True) == True]  # noqa: E712
    switches = 0
    for _, g in obs.groupby("cell_id"):
        g = g.sort_values("frame")
        if len(g) < 2:
            continue
        dx = g["x"].to_numpy()[1:] - g["x"].to_numpy()[:-1]
        dy = g["y"].to_numpy()[1:] - g["y"].to_numpy()[:-1]
        df = g["frame"].to_numpy()[1:] - g["frame"].to_numpy()[:-1]
        dist = np.sqrt(dx * dx + dy * dy)
        # only adjacent frames (gap=1); larger gaps are dropouts not switches
        switches += int(np.sum((df == 1) & (dist > max_jump_px)))
    return switches


def append_run(row: dict) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame([row])
    if RUNS.exists():
        df.to_csv(RUNS, mode="a", header=False, index=False)
    else:
        df.to_csv(RUNS, index=False)


def timed_pipeline(path: Path, cfg: Config, run_id: int) -> dict:
    """Full user-wait path from original file — never reuses detection caches."""
    torch.set_num_threads(max(1, min(4, torch.get_num_threads())))
    n_frames = cfg.frames
    stages: dict[str, float] = {}
    t_user0 = time.perf_counter()

    t0 = time.perf_counter()
    raw = load_video(path, max_frames=cfg.frames)
    stages["load_s"] = time.perf_counter() - t0

    t0 = time.perf_counter()
    # select_frames already applied max_frames via load; crop only
    video = select_frames(raw, frames=None, crop=cfg.crop)
    stages["preprocess_s"] = time.perf_counter() - t0

    cellpose_model = None
    trackastra_model = None
    startup_s = 0.0
    if cfg.include_model_startup:
        t0 = time.perf_counter()
        cellpose_model = models.CellposeModel(
            gpu=False, pretrained_model=CELLPOSE_MODEL
        )
        if cfg.linker == "trackastra":
            trackastra_model = Trackastra.from_pretrained(
                "general_2d", device="cpu"
            )
        startup_s = time.perf_counter() - t0
    stages["model_startup_s"] = startup_s

    t0 = time.perf_counter()
    if cellpose_model is None:
        cellpose_model = models.CellposeModel(
            gpu=False, pretrained_model=CELLPOSE_MODEL
        )
        # startup charged outside wait when include_model_startup=False
        # but still required — attribute residual to detect only if preloaded
    masks = np.empty(video.shape, dtype=np.int32)
    for index in range(video.shape[0]):
        kwargs = {
            "diameter": cfg.diameter,
            "cellprob_threshold": cfg.cellprob_threshold,
            "flow_threshold": cfg.flow_threshold,
            "batch_size": 1,
        }
        mask, _flows, _styles = cellpose_model.eval(video[index], **kwargs)
        masks[index] = mask
    stages["detect_s"] = time.perf_counter() - t0

    t0 = time.perf_counter()
    if cfg.linker == "trackastra":
        if trackastra_model is None:
            trackastra_model = Trackastra.from_pretrained(
                "general_2d", device="cpu"
            )
        track_graph, _ = trackastra_model.track(video, masks, mode="greedy")
        rows, _g, _p = graph_to_napari_tracks(track_graph)
        if len(rows) == 0:
            table = pd.DataFrame(columns=["cell_id", "frame", "x", "y", "observed"])
        else:
            table = pd.DataFrame(rows, columns=["cell_id", "frame", "y", "x"])
            table = table[["cell_id", "frame", "x", "y"]]
            table["observed"] = True
    else:
        dets = masks_to_detections(masks)
        table = nn_link(dets, max_dist=cfg.nn_max_dist, max_gap=cfg.nn_max_gap)
    stages["link_s"] = time.perf_counter() - t0

    t0 = time.perf_counter()
    out_csv = OUT / "user_wait_tracks" / cfg.name / f"{path.stem}.csv"
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    table.to_csv(out_csv, index=False)
    stages["output_s"] = time.perf_counter() - t0

    total_s = time.perf_counter() - t_user0
    n_frames = int(video.shape[0])
    m = track_metrics(table, n_frames)
    switches = id_switch_proxy(table)
    row = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "video": path.name,
        "config": cfg.name,
        "run_id": run_id,
        "frames": n_frames,
        "crop": cfg.crop,
        "linker": cfg.linker,
        "diameter": cfg.diameter if cfg.diameter is not None else "",
        "include_model_startup": cfg.include_model_startup,
        "total_s": total_s,
        "ms_per_frame": (total_s * 1000.0 / n_frames) if n_frames else 0.0,
        "load_s": stages["load_s"],
        "preprocess_s": stages["preprocess_s"],
        "model_startup_s": stages["model_startup_s"],
        "detect_s": stages["detect_s"],
        "link_s": stages["link_s"],
        "output_s": stages["output_s"],
        "id_switches_proxy": switches,
        "tracks_path": str(out_csv),
        **{f"m_{k}": v for k, v in m.items()},
    }
    append_run(row)
    log(
        f"{cfg.name} {path.name} run={run_id} total={total_s:.1f}s "
        f"detect={stages['detect_s']:.1f}s link={stages['link_s']:.1f}s "
        f"startup={stages['model_startup_s']:.1f}s "
        f"median_frac={m['median_frac']:.3f} switches={switches}"
    )
    return row


BASELINE = Config(name="baseline", include_model_startup=True)

# Candidate speed configs — quality checked vs baseline after timing.
CANDIDATES = [
    Config(name="diam30", diameter=30.0, include_model_startup=True),
    Config(name="nn_link", linker="nn", include_model_startup=True),
    Config(
        name="diam30_nn",
        diameter=30.0,
        linker="nn",
        include_model_startup=True,
    ),
]


def median_row(rows: list[dict]) -> dict:
    keys = [
        "total_s",
        "ms_per_frame",
        "load_s",
        "preprocess_s",
        "model_startup_s",
        "detect_s",
        "link_s",
        "output_s",
        "id_switches_proxy",
        "m_median_frac",
        "m_obs_cell_frames_ge_50",
        "m_n_tracks",
        "m_frac_tracks_ge_50",
        "m_frac_tracks_ge_75",
    ]
    out = {k: rows[0].get(k) for k in rows[0] if k not in keys}
    for k in keys:
        vals = [float(r[k]) for r in rows if k in r and r[k] != ""]
        out[k] = float(statistics.median(vals)) if vals else float("nan")
    out["n_reps"] = len(rows)
    return out


def stage_breakdown(med: dict) -> list[tuple[str, float]]:
    stages = [
        ("load", med["load_s"]),
        ("preprocess", med["preprocess_s"]),
        ("model_startup", med["model_startup_s"]),
        ("detect", med["detect_s"]),
        ("link", med["link_s"]),
        ("output", med["output_s"]),
    ]
    return sorted(stages, key=lambda kv: -kv[1])


def run_reps(path: Path, cfg: Config, reps: int) -> dict:
    rows = []
    for i in range(1, reps + 1):
        rows.append(timed_pipeline(path, cfg, run_id=i))
    return median_row(rows)


def write_summary(compare: dict) -> None:
    lines = [
        "# User-wait timing (upload → tracks ready)",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        "",
        "Overnight experiment throughput is **separate** — see MORNING_REPORT overnight sections.",
        "These numbers are single-video user wait with competing overnight jobs paused.",
        "",
        "PROXY quality: `median_frac`, `obs_cell_frames_ge_50`, `id_switches_proxy` (large adjacent jumps).",
        "",
    ]
    videos = compare.get("videos", {})
    lines += [
        "## Per-video baseline vs optimized",
        "",
        "| video | baseline_s | optimized_s | speedup | baseline ms/frame | opt ms/frame | baseline median_frac | opt median_frac | baseline switches | opt switches | config | note |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|",
    ]
    for video, info in sorted(videos.items()):
        b = info["baseline_median"]
        q = info.get("best_quality")
        f = info.get("fastest_match")
        # Prefer fastest_match for "optimized" display; fall back to best_quality
        opt = f or q or b
        label = info.get("optimized_label", opt.get("config", "?"))
        note = info.get("note", "")
        speedup = (b["total_s"] / opt["total_s"]) if opt["total_s"] > 0 else float("nan")
        lines.append(
            "| {v} | {bs:.1f} | {os:.1f} | {sp:.2f}x | {bm:.0f} | {om:.0f} | {bf:.3f} | {of:.3f} | {bsw:.0f} | {osw:.0f} | {c} | {n} |".format(
                v=video,
                bs=b["total_s"],
                os=opt["total_s"],
                sp=speedup,
                bm=b["ms_per_frame"],
                om=opt["ms_per_frame"],
                bf=b["m_median_frac"],
                of=opt["m_median_frac"],
                bsw=b["id_switches_proxy"],
                osw=opt["id_switches_proxy"],
                c=label,
                n=note,
            )
        )

    lines += ["", "## Slowest stage (baseline median, first video listed)", ""]
    for video, info in sorted(videos.items()):
        stages = stage_breakdown(info["baseline_median"])
        lines.append(f"### {video}")
        lines.append("")
        for name, sec in stages:
            lines.append(f"- {name}: {sec:.2f}s")
        lines.append("")

    lines += [
        "## Dual configs (quality-first)",
        "",
        "Per video we keep:",
        "1. **best_quality** — highest identity-preserving coverage (`m_obs_cell_frames_ge_50`, then `median_frac`, penalize switches)",
        "2. **fastest_match** — lowest `total_s` among configs with coverage ≥ best_quality − tolerance and switches ≤ best_quality",
        "",
    ]
    for video, info in sorted(videos.items()):
        lines.append(f"### {video}")
        bq = info.get("best_quality")
        fm = info.get("fastest_match")
        if bq:
            lines.append(
                f"- best_quality `{bq.get('config')}`: {bq['total_s']:.1f}s, "
                f"median_frac={bq['m_median_frac']:.3f}, "
                f"ge50_frames={bq['m_obs_cell_frames_ge_50']:.0f}, "
                f"switches={bq['id_switches_proxy']:.0f}"
            )
        if fm:
            lines.append(
                f"- fastest_match `{fm.get('config')}`: {fm['total_s']:.1f}s "
                f"(saved {info['baseline_median']['total_s'] - fm['total_s']:.1f}s vs baseline)"
            )
        for trade in info.get("tradeoffs", []):
            lines.append(f"- tradeoff: {trade}")
        lines.append("")

    SUMMARY.write_text("\n".join(lines))
    log(f"wrote {SUMMARY}")

    # Merge into morning report (user-wait section + keep overnight separate)
    overnight_body = ""
    if REPORT.exists():
        text = REPORT.read_text()
        marker = "## User-wait timing"
        if marker in text:
            overnight_body = text.split(marker)[0].rstrip() + "\n\n"
        else:
            overnight_body = text.rstrip() + "\n\n"
    else:
        overnight_body = (
            "# Morning tracking comparison\n\n"
            f"Generated: {datetime.now(timezone.utc).isoformat()}\n\n"
            "## Overnight experiment throughput (PROXY)\n\n"
            "See `results.csv` / detect-link-final phases. This section is separate from user-wait.\n\n"
        )
    REPORT.write_text(
        overnight_body
        + "## User-wait timing (upload → tracks)\n\n"
        + "Competing overnight jobs were paused during these timings.\n\n"
        + SUMMARY.read_text()
        + "\n"
    )
    log(f"updated {REPORT}")


def quality_key(med: dict) -> tuple:
    # Higher coverage better; fewer switches better
    return (
        med["m_obs_cell_frames_ge_50"],
        med["m_median_frac"],
        -med["id_switches_proxy"],
    )


def pick_dual(baseline: dict, candidates: list[dict], cov_tol: float = 0.02) -> tuple[dict, dict | None, list[str]]:
    pool = [baseline] + candidates
    best_q = max(pool, key=quality_key)
    tradeoffs = []
    matches = []
    for c in pool:
        cov_ok = c["m_obs_cell_frames_ge_50"] >= best_q["m_obs_cell_frames_ge_50"] * (
            1.0 - cov_tol
        ) or abs(c["m_median_frac"] - best_q["m_median_frac"]) <= cov_tol
        # Prefer ge50 coverage match
        cov_ok = c["m_obs_cell_frames_ge_50"] >= best_q["m_obs_cell_frames_ge_50"] * (
            1.0 - cov_tol
        )
        sw_ok = c["id_switches_proxy"] <= best_q["id_switches_proxy"] + 0.5
        if cov_ok and sw_ok:
            matches.append(c)
        elif c["total_s"] + 0.05 < best_q["total_s"]:
            saved = best_q["total_s"] - c["total_s"]
            tradeoffs.append(
                f"`{c.get('config')}` saves {saved:.1f}s but "
                f"ge50_frames {c['m_obs_cell_frames_ge_50']:.0f} vs {best_q['m_obs_cell_frames_ge_50']:.0f}, "
                f"switches {c['id_switches_proxy']:.0f} vs {best_q['id_switches_proxy']:.0f}"
            )
    fastest = min(matches, key=lambda r: r["total_s"]) if matches else None
    return best_q, fastest, tradeoffs


def cmd_baseline(paths: list[Path], reps: int) -> dict:
    compare = {"videos": {}, "updated": datetime.now(timezone.utc).isoformat()}
    if COMPARE.exists():
        compare = json.loads(COMPARE.read_text())
        compare.setdefault("videos", {})

    for path in paths:
        log(f"=== baseline {path.name} reps={reps} ===")
        med = run_reps(path, BASELINE, reps)
        med["config"] = BASELINE.name
        stages = stage_breakdown(med)
        log(f"slowest stage for {path.name}: {stages[0][0]} ({stages[0][1]:.1f}s)")
        compare["videos"][path.name] = {
            "baseline_median": med,
            "best_quality": med,
            "fastest_match": med,
            "optimized_label": "baseline",
            "note": f"slowest={stages[0][0]}",
            "tradeoffs": [],
            "candidates": {},
        }
        COMPARE.write_text(json.dumps(compare, indent=2))
        write_summary(compare)
    return compare


def cmd_candidates(paths: list[Path], reps: int, which: list[str] | None) -> dict:
    compare = json.loads(COMPARE.read_text()) if COMPARE.exists() else {"videos": {}}
    cands = [c for c in CANDIDATES if which is None or c.name in which]
    for path in paths:
        v = path.name
        if v not in compare.get("videos", {}):
            log(f"skip {v}: run baseline first")
            continue
        info = compare["videos"][v]
        baseline = info["baseline_median"]
        cand_meds = []
        for cfg in cands:
            log(f"=== candidate {cfg.name} on {v} reps={reps} ===")
            med = run_reps(path, cfg, reps)
            med["config"] = cfg.name
            info.setdefault("candidates", {})[cfg.name] = med
            cand_meds.append(med)
            # If faster but worse quality, still record tradeoff via pick_dual
        best_q, fastest, tradeoffs = pick_dual(baseline, cand_meds)
        info["best_quality"] = best_q
        info["fastest_match"] = fastest
        info["tradeoffs"] = tradeoffs
        opt = fastest or best_q
        info["optimized_label"] = opt.get("config", "?")
        info["note"] = (
            f"best_q={best_q.get('config')}; "
            f"fast={fastest.get('config') if fastest else 'none'}"
        )
        compare["videos"][v] = info
        COMPARE.write_text(json.dumps(compare, indent=2))
        write_summary(compare)
    return compare


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        choices=["baseline", "candidates", "report"],
        default="baseline",
    )
    parser.add_argument("--reps", type=int, default=3)
    parser.add_argument(
        "--video",
        action="append",
        default=None,
        help=f"Default: {DEFAULT_VIDEOS} (bar15-only).",
    )
    parser.add_argument(
        "--all-videos",
        action="store_true",
        help="Benchmark every MP4 in data/.",
    )
    parser.add_argument(
        "--candidate",
        action="append",
        default=None,
        help="Limit candidate names (default: all)",
    )
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    if args.all_videos:
        only = None
    elif args.video:
        only = args.video
    else:
        only = list(DEFAULT_VIDEOS)
    paths = video_paths(only=only)
    assert all("bar15" in p.name for p in paths) or args.all_videos, (
        "bar15-only mode: refusing non-bar15 paths"
    )
    log(f"user_wait videos: {[p.name for p in paths]}")

    # Safety: refuse if overnight still running
    import subprocess

    try:
        out = subprocess.check_output(
            ["pgrep", "-f", "backend/overnight_optimize.py"], text=True
        ).strip()
        if out:
            log(f"REFUSING: overnight_optimize still running: {out}")
            sys.exit(2)
    except subprocess.CalledProcessError:
        pass

    try:
        if args.mode == "baseline":
            cmd_baseline(paths, args.reps)
        elif args.mode == "candidates":
            cmd_candidates(paths, args.reps, args.candidate)
        else:
            compare = json.loads(COMPARE.read_text()) if COMPARE.exists() else {"videos": {}}
            write_summary(compare)
        log("user_wait_bench done")
    except Exception as e:
        log(f"FATAL: {e}")
        traceback.print_exc()
        raise


if __name__ == "__main__":
    main()
