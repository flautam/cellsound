#!/usr/bin/env python3
"""Staged bar15 tracking optimization (screen → tune → promote → holdout).

Schedule:
  - 10-frame screen (discard obvious failures)
  - 30-frame tuning clips
  - 60-frame major gate (80% same-ID retention PROXY)
  - extend same sequence: 90 → 120 → 180 → 271
  - full-video baseline kept as a separate comparison
  - holdout 60-frame clip (not used for tuning) before declaring a winner

Detections are cached per (detector_cfg, frame). Extending a window reuses
cached frames; linker-only changes make zero detector calls.

Retention is labeled PROXY unless verified reference identities exist.
"""

from __future__ import annotations

import json
import time
import traceback
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pandas as pd

from overnight_optimize import (
    CLIP_CROP,
    DEFAULT_VIDEOS,
    DetectCfg,
    LinkCfg,
    OUT,
    REPORT,
    append_result,
    count_timepoints,
    exp_key,
    frame_range_tag,
    free_mem,
    is_done,
    log,
    mark_done,
    masks_to_detections,
    nn_link,
    prep_video,
    preprocess,
    save_overlay,
    segment,
    trackastra_link,
    track_metrics,
    video_paths,
    write_morning_report,
)

SCREEN_FRAMES = 10
TUNE_FRAMES = 30
MAJOR_FRAMES = 60
EXTEND_LENGTHS = [60, 90, 120, 180, 271]
RETENTION_TARGET = 0.80
# Holdout starts later in the movie so it is not the tuning prefix.
HOLDOUT_FRAME0 = 90
HOLDOUT_FRAMES = 60
# Alternate short window if few nuclei span a long window.
ALT_FRAME0 = 140
ALT_FRAMES = 60

DET_CACHE = OUT / "det_cache"


def detect_key(cfg: DetectCfg) -> str:
    d = "auto" if cfg.diameter is None else f"{cfg.diameter:g}"
    return f"{cfg.preprocess}_d{d}_cp{cfg.cellprob_threshold:g}_fl{cfg.flow_threshold:g}"


def link_key(cfg: LinkCfg) -> str:
    return f"{cfg.method}_md{cfg.max_dist:g}_g{cfg.max_gap}_{cfg.trackastra_mode}"


def frame_cache_path(video_stem: str, dkey: str, frame_i: int) -> Path:
    return DET_CACHE / video_stem / dkey / f"frame_{frame_i:05d}.npz"


def load_or_compute_masks(
    path: Path,
    video: np.ndarray,
    meta: dict,
    detect: DetectCfg,
    frame0: int,
    n_frames: int,
) -> tuple[np.ndarray, float, int]:
    """Return masks for [frame0, frame0+n). Only uncached frames call Cellpose."""
    stem = path.stem
    dkey = detect_key(detect)
    t0 = time.time()
    detect_calls = 0
    masks = np.zeros((n_frames, video.shape[1], video.shape[2]), dtype=np.int32)
    missing_idx: list[int] = []
    for i in range(n_frames):
        abs_f = frame0 + i
        cache_p = frame_cache_path(stem, dkey, abs_f)
        if cache_p.exists():
            masks[i] = np.load(cache_p)["mask"]
        else:
            missing_idx.append(i)

    if missing_idx:
        log(
            f"detect cache miss {len(missing_idx)}/{n_frames} frames "
            f"detector={dkey} window=[{frame0},{frame0 + n_frames})"
        )
        proc = preprocess(video, detect.preprocess)
        # Segment only uncached frames, then scatter back into the window.
        miss_stack = proc[missing_idx]
        miss_masks = segment(
            miss_stack,
            detect.diameter,
            detect.cellprob_threshold,
            detect.flow_threshold,
            source_timepoints=meta.get("source_timepoints"),
            frame0=frame0 + missing_idx[0],
        )
        detect_calls = len(missing_idx)
        (DET_CACHE / stem / dkey).mkdir(parents=True, exist_ok=True)
        for j, i in enumerate(missing_idx):
            masks[i] = miss_masks[j]
            abs_f = frame0 + i
            np.savez_compressed(frame_cache_path(stem, dkey, abs_f), mask=masks[i])
    else:
        log(
            f"detect cache HIT 0 calls detector={dkey} "
            f"window=[{frame0},{frame0 + n_frames})"
        )

    return masks, time.time() - t0, detect_calls


def run_tracked_window(
    path: Path,
    detect: DetectCfg,
    link: LinkCfg,
    frame0: int,
    n_frames: int,
    crop: int = CLIP_CROP,
) -> dict:
    """Track a window with per-frame detection cache. Returns metrics + artifacts."""
    # Load only needed frames (prep_video always from 0 — load max frame0+n)
    video_full, meta_full = prep_video(path, frame0 + n_frames, crop)
    video = video_full[frame0 : frame0 + n_frames]
    meta = dict(meta_full)
    meta["frame0"] = frame0
    meta["selected_frames"] = n_frames
    meta["frame_last"] = frame0 + n_frames - 1
    meta["frame_tag"] = frame_range_tag(frame0, n_frames, meta_full["source_timepoints"])

    t_user0 = time.time()
    masks, detect_s, detect_calls = load_or_compute_masks(
        path, video, meta, detect, frame0, n_frames
    )
    t_link0 = time.time()
    if link.method.startswith("trackastra"):
        proc = preprocess(video, detect.preprocess)
        table = trackastra_link(proc, masks, mode=link.trackastra_mode)
    else:
        dets = masks_to_detections(masks)
        table = nn_link(dets, max_dist=link.max_dist, max_gap=link.max_gap)
    link_s = time.time() - t_link0
    total_s = time.time() - t_user0

    ret = proxy_same_id_retention(table, masks, n_frames)
    gaps = detection_gap_report(table, masks, n_frames)
    m = track_metrics(table, n_frames)
    return {
        "table": table,
        "masks": masks,
        "video": video,
        "meta": meta,
        "retention": ret,
        "gaps": gaps,
        "metrics": m,
        "runtime_s": total_s,
        "detect_s": detect_s,
        "link_s": link_s,
        "detect_calls": detect_calls,
        "detect": detect,
        "link": link,
    }


def proxy_reference_paths(
    masks: np.ndarray, max_dist: float = 40.0
) -> list[np.ndarray]:
    """PROXY refs: NN-linked detection chains present on first and last frame.

    Without verified GT we cannot prove 'visibly present but missed'. We form
    references from a greedy NN chain that starts on frame 0 and can be matched
    through to the last frame (nuclei that appear throughout under proximity).
    Detector-miss failures still show up as broken chains / short tracks.
    """
    dets = masks_to_detections(masks)
    n = len(dets)
    if n == 0 or dets[0].empty:
        return []
    # Each ref is an (n_frames, 2) array of xy; nan where missing
    refs: list[dict] = []
    for _, row in dets[0].iterrows():
        refs.append(
            {
                "xy": np.full((n, 2), np.nan, dtype=float),
                "alive": True,
            }
        )
        refs[-1]["xy"][0] = [row["x"], row["y"]]

    for t in range(1, n):
        det = dets[t]
        if det.empty:
            for r in refs:
                r["alive"] = False
            continue
        pts = det[["x", "y"]].to_numpy(dtype=float)
        used = set()
        for r in refs:
            if not r["alive"]:
                continue
            # last known position
            prev = None
            for k in range(t - 1, -1, -1):
                if np.isfinite(r["xy"][k, 0]):
                    prev = r["xy"][k]
                    break
            if prev is None:
                r["alive"] = False
                continue
            d = np.linalg.norm(pts - prev[None, :], axis=1)
            order = np.argsort(d)
            chosen = None
            for j in order:
                if j in used:
                    continue
                if d[j] <= max_dist:
                    chosen = int(j)
                    break
                break
            if chosen is None:
                r["alive"] = False
            else:
                used.add(chosen)
                r["xy"][t] = pts[chosen]

    # Keep only refs that have a finite last-frame position (visible start→finish PROXY)
    out = []
    for r in refs:
        if np.isfinite(r["xy"][0, 0]) and np.isfinite(r["xy"][-1, 0]):
            out.append(r["xy"])
    return out


def proxy_same_id_retention(
    table: pd.DataFrame,
    masks: np.ndarray,
    n_frames: int,
    match_dist: float = 40.0,
    switch_jump: float = 60.0,
) -> dict:
    """PROXY same-ID retention: count/percentage + per-ref break info.

    Numerator: refs followed by one cell_id for all frames without a large jump
    (neighbor switch heuristic). Denominator: refs visible start→finish (PROXY).
    """
    refs = proxy_reference_paths(masks, max_dist=match_dist)
    denom = len(refs)
    if denom == 0:
        return {
            "label": "PROXY",
            "retained": 0,
            "denom": 0,
            "pct": 0.0,
            "summary": "0/0",
            "breaks": [],
            "note": "no PROXY references spanning window",
        }

    obs = table
    if "observed" in table.columns:
        obs = table[table["observed"] == True]  # noqa: E712

    retained = 0
    breaks = []
    for ri, path in enumerate(refs):
        # Assign cell_id on frame 0 by nearest track point
        if obs.empty:
            breaks.append({"ref": ri, "break_frame": 0, "cause": "no_tracks"})
            continue
        f0 = obs[obs["frame"] == 0]
        if f0.empty:
            breaks.append({"ref": ri, "break_frame": 0, "cause": "missed_detection"})
            continue
        d0 = np.linalg.norm(f0[["x", "y"]].to_numpy() - path[0][None, :], axis=1)
        j = int(np.argmin(d0))
        if d0[j] > match_dist:
            breaks.append({"ref": ri, "break_frame": 0, "cause": "missed_detection"})
            continue
        cid = int(f0.iloc[j]["cell_id"])
        tr = obs[obs["cell_id"] == cid].sort_values("frame")
        ok = True
        break_frame = None
        cause = None
        for t in range(n_frames):
            row = tr[tr["frame"] == t]
            if row.empty:
                # gap in observed detections for this ID
                # check if any detection exists near ref (merged/miss vs bad link)
                # Look at masks detections
                dets_t = masks_to_detections(masks[t : t + 1])[0]
                if dets_t.empty:
                    cause = "missed_detection"
                else:
                    dd = np.linalg.norm(
                        dets_t[["x", "y"]].to_numpy() - path[t][None, :], axis=1
                    )
                    if dd.min() <= match_dist:
                        # detection present but wrong/missing ID → bad link or merge
                        # if multiple close: merged_nuclei heuristic
                        close = int(np.sum(dd <= match_dist))
                        cause = "merged_nuclei" if close >= 2 else "bad_link"
                    else:
                        cause = "missed_detection"
                break_frame = t
                ok = False
                break
            xy = row.iloc[0][["x", "y"]].to_numpy(dtype=float)
            if np.linalg.norm(xy - path[t]) > match_dist:
                # ID jumped toward a neighbor
                cause = "bad_link"
                break_frame = t
                ok = False
                break
            if t > 0:
                prev = tr[tr["frame"] == t - 1]
                if not prev.empty:
                    pxy = prev.iloc[0][["x", "y"]].to_numpy(dtype=float)
                    if np.linalg.norm(xy - pxy) > switch_jump:
                        cause = "bad_link"
                        break_frame = t
                        ok = False
                        break
        if ok and len(tr) >= n_frames:
            retained += 1
        else:
            if break_frame is None:
                break_frame = int(tr["frame"].max()) + 1 if len(tr) else 0
                cause = cause or "short_track"
            breaks.append(
                {"ref": ri, "break_frame": int(break_frame), "cause": cause or "unknown"}
            )

    pct = retained / denom if denom else 0.0
    return {
        "label": "PROXY",
        "retained": retained,
        "denom": denom,
        "pct": pct,
        "summary": f"{retained}/{denom}",
        "breaks": breaks,
        "note": "No verified GT identities; same-ID retention is PROXY",
    }


def detection_gap_report(table: pd.DataFrame, masks: np.ndarray, n_frames: int) -> dict:
    """Gaps in observed detections (separate from retention; matters for velocity)."""
    per_frame = []
    for t in range(n_frames):
        n_det = int(masks[t].max()) if masks[t].size else 0
        if table.empty:
            n_obs = 0
        else:
            sub = table[table["frame"] == t]
            if "observed" in table.columns:
                sub = sub[sub["observed"] == True]  # noqa: E712
            n_obs = int(len(sub))
        per_frame.append({"frame": t, "n_det": n_det, "n_obs_tracks": n_obs})
    gap_frames = [p for p in per_frame if p["n_det"] == 0]
    return {
        "n_frames": n_frames,
        "n_zero_detection_frames": len(gap_frames),
        "zero_detection_frames": [p["frame"] for p in gap_frames],
        "mean_det": float(np.mean([p["n_det"] for p in per_frame])) if per_frame else 0.0,
        "mean_obs": float(np.mean([p["n_obs_tracks"] for p in per_frame])) if per_frame else 0.0,
    }


def screen_ok(result: dict) -> bool:
    """10-frame screen: discard empty / tiny / collapsed tracking."""
    m = result["metrics"]
    ret = result["retention"]
    if m["n_tracks"] < 1:
        return False
    if m["mean_cells_per_frame"] < 1:
        return False
    if ret["denom"] >= 3 and ret["pct"] < 0.25:
        return False
    return True


def save_run_artifacts(path: Path, tag: str, result: dict) -> Path:
    exp_dir = OUT / "staged" / path.stem / tag
    exp_dir.mkdir(parents=True, exist_ok=True)
    result["table"].to_csv(exp_dir / "tracks.csv", index=False)
    (exp_dir / "retention.json").write_text(json.dumps(result["retention"], indent=2))
    (exp_dir / "gaps.json").write_text(json.dumps(result["gaps"], indent=2))
    (exp_dir / "meta.json").write_text(
        json.dumps(
            {
                "meta": result["meta"],
                "detect": asdict(result["detect"]),
                "link": asdict(result["link"]),
                "runtime_s": result["runtime_s"],
                "detect_s": result["detect_s"],
                "link_s": result["link_s"],
                "detect_calls": result["detect_calls"],
                "metrics": result["metrics"],
                "retention": result["retention"],
                "gaps": result["gaps"],
            },
            indent=2,
            default=str,
        )
    )
    save_overlay(result["video"], result["table"], exp_dir / "overlay_f0.png", 0)
    mid = min(5, result["video"].shape[0] - 1)
    save_overlay(result["video"], result["table"], exp_dir / "overlay_mid.png", mid)
    return exp_dir


def candidate_grid() -> list[tuple[DetectCfg, LinkCfg]]:
    detects = [
        DetectCfg(preprocess="raw", diameter=None),
        DetectCfg(preprocess="invert", diameter=None),
        DetectCfg(preprocess="invert", diameter=30.0),
        DetectCfg(preprocess="invert", diameter=40.0),
        DetectCfg(preprocess="raw", diameter=30.0),
        DetectCfg(preprocess="invert_clahe", diameter=40.0),
    ]
    links = [
        LinkCfg(method="trackastra_greedy", trackastra_mode="greedy"),
        LinkCfg(method="nn", max_dist=25.0, max_gap=1),
        LinkCfg(method="nn", max_dist=40.0, max_gap=2),
    ]
    out = []
    for d in detects:
        for lk in links:
            out.append((d, lk))
    return out


def local_tune_around_break(
    path: Path,
    detect: DetectCfg,
    link: LinkCfg,
    break_frame: int,
    cause: str,
    state: dict,
) -> tuple[DetectCfg, LinkCfg]:
    """Short-clip tune around the first break, then return best local pair."""
    # Window around break: 10 frames centered
    f0 = max(0, break_frame - 4)
    n = 10
    key = exp_key("local_tune", path, f"f{f0}_n{n}", detect_key(detect), link_key(link), cause)
    if is_done(state, key):
        log(f"skip {key}")
        return detect, link

    log(f"local tune around break_frame={break_frame} cause={cause}")
    variants: list[tuple[DetectCfg, LinkCfg]] = [(detect, link)]
    if cause == "missed_detection":
        variants += [
            (DetectCfg(preprocess="invert", diameter=detect.diameter or 30.0), link),
            (DetectCfg(preprocess="invert_clahe", diameter=30.0), link),
            (DetectCfg(preprocess="raw", diameter=20.0), link),
        ]
    elif cause in ("bad_link", "merged_nuclei"):
        variants += [
            (detect, LinkCfg(method="nn", max_dist=20.0, max_gap=0)),
            (detect, LinkCfg(method="nn", max_dist=35.0, max_gap=2)),
            (detect, LinkCfg(method="trackastra_greedy", trackastra_mode="greedy_nodiv")),
        ]

    best = (detect, link)
    best_pct = -1.0
    for d, lk in variants:
        try:
            r = run_tracked_window(path, d, lk, f0, n)
            pct = r["retention"]["pct"]
            log(
                f"  local {detect_key(d)}|{link_key(lk)} "
                f"retention={r['retention']['summary']} ({pct:.0%}) "
                f"detect_calls={r['detect_calls']} t={r['runtime_s']:.1f}s"
            )
            append_result(
                {
                    "phase": "local_tune",
                    "video": path.name,
                    "key": exp_key("local_tune", path, f"f{f0}_n{n}", detect_key(d), link_key(lk)),
                    "frame0": f0,
                    "n_frames": n,
                    "retention_summary": r["retention"]["summary"],
                    "retention_pct": pct,
                    "retention_label": "PROXY",
                    "runtime_s": r["runtime_s"],
                    "detect_calls": r["detect_calls"],
                    "cause_targeted": cause,
                    "preprocess": d.preprocess,
                    "diameter": d.diameter,
                    "linker": lk.method,
                    "max_dist": lk.max_dist,
                    "max_gap": lk.max_gap,
                }
            )
            if pct > best_pct:
                best_pct = pct
                best = (d, lk)
            del r
            free_mem()
        except Exception as e:
            log(f"  local FAIL {e}")
            traceback.print_exc()
    mark_done(state, key)
    return best


def phase_staged(state: dict, paths: list[Path]) -> None:
    for path in paths:
        log(f"=== STAGED schedule for {path.name} ===")
        source_n = count_timepoints(path)
        log(f"source_timepoints={source_n}")

        # --- Separate full baseline comparison (default detector/linker) ---
        # Use first 60 as a cheap baseline comparison if full 271 already too costly;
        # also schedule true full baseline key separately with resume.
        base_key = exp_key("baseline_cmp", path, frame_range_tag(0, min(60, source_n), source_n))
        if not is_done(state, base_key):
            log("baseline comparison window (60f or shorter) — separate from promotion")
            n_b = min(60, source_n)
            r = run_tracked_window(path, DetectCfg(), LinkCfg(method="trackastra_greedy"), 0, n_b)
            save_run_artifacts(path, f"baseline_cmp_{r['meta']['frame_tag']}", r)
            append_result(
                {
                    "phase": "baseline_cmp",
                    "video": path.name,
                    "key": base_key,
                    "frame0": 0,
                    "n_frames": n_b,
                    "retention_summary": r["retention"]["summary"],
                    "retention_pct": r["retention"]["pct"],
                    "retention_label": "PROXY",
                    "runtime_s": r["runtime_s"],
                    "detect_calls": r["detect_calls"],
                    "gap_zero_frames": r["gaps"]["n_zero_detection_frames"],
                    "preprocess": "raw",
                    "diameter": None,
                    "linker": "trackastra_greedy",
                }
            )
            mark_done(state, base_key)
            log(
                f"baseline_cmp retention={r['retention']['summary']} "
                f"({r['retention']['pct']:.0%}) PROXY t={r['runtime_s']:.1f}s"
            )
            del r
            free_mem("after baseline_cmp")

        # Optional deferred full-271 baseline comparison (resume-safe, separate key)
        full_tag = frame_range_tag(0, source_n, source_n)
        full_key = exp_key("baseline_full", path, full_tag)
        # Do not start full 271 until promotion finished — mark intent in log only here.

        survivors: list[tuple[DetectCfg, LinkCfg]] = []

        def _passed_from_results(phase: str, key: str) -> bool | None:
            if not (OUT / "results.csv").exists():
                return None
            try:
                df = pd.read_csv(OUT / "results.csv")
                sub = df[df["key"] == key]
                if sub.empty:
                    return None
                row = sub.iloc[-1]
                if "passed" in sub.columns and pd.notna(row.get("passed")):
                    return bool(row["passed"])
                if "retention_pct" in sub.columns and pd.notna(row.get("retention_pct")):
                    return float(row["retention_pct"]) >= 0.25
            except Exception:
                return None
            return None

        # --- 10-frame screen ---
        for d, lk in candidate_grid():
            tag = frame_range_tag(0, SCREEN_FRAMES, source_n)
            key = exp_key("screen10", path, tag, detect_key(d), link_key(lk))
            if is_done(state, key):
                ok_prev = _passed_from_results("screen10", key)
                if ok_prev is True:
                    survivors.append((d, lk))
                continue
            log(f"screen10 {detect_key(d)}|{link_key(lk)}")
            r = run_tracked_window(path, d, lk, 0, SCREEN_FRAMES)
            save_run_artifacts(path, f"screen10_{detect_key(d)}_{link_key(lk)}", r)
            ok = screen_ok(r)
            append_result(
                {
                    "phase": "screen10",
                    "video": path.name,
                    "key": key,
                    "frame0": 0,
                    "n_frames": SCREEN_FRAMES,
                    "passed": ok,
                    "retention_summary": r["retention"]["summary"],
                    "retention_pct": r["retention"]["pct"],
                    "retention_label": "PROXY",
                    "runtime_s": r["runtime_s"],
                    "detect_calls": r["detect_calls"],
                    "preprocess": d.preprocess,
                    "diameter": d.diameter,
                    "linker": lk.method,
                    "max_dist": lk.max_dist,
                    "max_gap": lk.max_gap,
                }
            )
            mark_done(state, key)
            if ok:
                survivors.append((d, lk))
                log(f"  PASS screen retention={r['retention']['summary']}")
            else:
                log(f"  FAIL screen retention={r['retention']['summary']}")
            del r
            free_mem()

        if not survivors:
            log("all screen candidates failed — keeping raw+trackastra for tune")
            survivors = [(DetectCfg(), LinkCfg(method="trackastra_greedy"))]

        # dedupe
        uniq = {}
        for d, lk in survivors:
            uniq[(detect_key(d), link_key(lk))] = (d, lk)
        survivors = list(uniq.values())

        # --- 30-frame tuning ---
        tuned: list[tuple[DetectCfg, LinkCfg, float]] = []
        for d, lk in survivors:
            tag = frame_range_tag(0, TUNE_FRAMES, source_n)
            key = exp_key("tune30", path, tag, detect_key(d), link_key(lk))
            if is_done(state, key):
                pct_prev = 0.0
                if (OUT / "results.csv").exists():
                    try:
                        df = pd.read_csv(OUT / "results.csv")
                        sub = df[df["key"] == key]
                        if not sub.empty and "retention_pct" in sub.columns:
                            pct_prev = float(sub.iloc[-1]["retention_pct"])
                    except Exception:
                        pass
                tuned.append((d, lk, pct_prev))
                continue
            log(f"tune30 {detect_key(d)}|{link_key(lk)}")
            r = run_tracked_window(path, d, lk, 0, TUNE_FRAMES)
            save_run_artifacts(path, f"tune30_{detect_key(d)}_{link_key(lk)}", r)
            append_result(
                {
                    "phase": "tune30",
                    "video": path.name,
                    "key": key,
                    "frame0": 0,
                    "n_frames": TUNE_FRAMES,
                    "retention_summary": r["retention"]["summary"],
                    "retention_pct": r["retention"]["pct"],
                    "retention_label": "PROXY",
                    "runtime_s": r["runtime_s"],
                    "detect_calls": r["detect_calls"],
                    "gap_zero_frames": r["gaps"]["n_zero_detection_frames"],
                    "preprocess": d.preprocess,
                    "diameter": d.diameter,
                    "linker": lk.method,
                    "max_dist": lk.max_dist,
                    "max_gap": lk.max_gap,
                }
            )
            mark_done(state, key)
            tuned.append((d, lk, r["retention"]["pct"]))
            log(
                f"  retention={r['retention']['summary']} ({r['retention']['pct']:.0%}) "
                f"detect_calls={r['detect_calls']} t={r['runtime_s']:.1f}s"
            )
            del r
            free_mem()

        tuned.sort(key=lambda x: -x[2])
        # keep top few
        tuned = tuned[:6]
        promoted: list[tuple[DetectCfg, LinkCfg]] = [(d, lk) for d, lk, _ in tuned]

        # --- Major 60 + extensions ---
        winners: list[dict] = []
        for d, lk in promoted:
            cur_d, cur_lk = d, lk
            passed_all = True
            for n_len in EXTEND_LENGTHS:
                if n_len > source_n:
                    n_len = source_n
                tag = frame_range_tag(0, n_len, source_n)
                key = exp_key("promote", path, tag, detect_key(cur_d), link_key(cur_lk))
                if is_done(state, key):
                    log(f"skip {key}")
                    continue
                log(f"promote n={n_len} {detect_key(cur_d)}|{link_key(cur_lk)}")
                r = run_tracked_window(path, cur_d, cur_lk, 0, n_len)
                save_run_artifacts(
                    path, f"promote_n{n_len}_{detect_key(cur_d)}_{link_key(cur_lk)}", r
                )
                ret = r["retention"]
                append_result(
                    {
                        "phase": f"promote_{n_len}",
                        "video": path.name,
                        "key": key,
                        "frame0": 0,
                        "n_frames": n_len,
                        "retention_summary": ret["summary"],
                        "retention_pct": ret["pct"],
                        "retention_label": "PROXY",
                        "runtime_s": r["runtime_s"],
                        "detect_s": r["detect_s"],
                        "link_s": r["link_s"],
                        "detect_calls": r["detect_calls"],
                        "gap_zero_frames": r["gaps"]["n_zero_detection_frames"],
                        "mean_det": r["gaps"]["mean_det"],
                        "preprocess": cur_d.preprocess,
                        "diameter": cur_d.diameter,
                        "linker": cur_lk.method,
                        "max_dist": cur_lk.max_dist,
                        "max_gap": cur_lk.max_gap,
                    }
                )
                log(
                    f"  retention={ret['summary']} ({ret['pct']:.0%}) PROXY "
                    f"detect_calls={r['detect_calls']} (0 means linker-only/cache) "
                    f"t={r['runtime_s']:.1f}s gaps0={r['gaps']['n_zero_detection_frames']}"
                )

                if ret["denom"] < 3 and n_len >= 120:
                    log(
                        f"  few PROXY refs spanning window (denom={ret['denom']}); "
                        f"also testing alternate {ALT_FRAMES}f @ frame0={ALT_FRAME0}"
                    )
                    alt = run_tracked_window(
                        path, cur_d, cur_lk, ALT_FRAME0, min(ALT_FRAMES, max(0, source_n - ALT_FRAME0))
                    )
                    append_result(
                        {
                            "phase": "alt_window",
                            "video": path.name,
                            "key": exp_key("alt", path, frame_range_tag(ALT_FRAME0, alt["meta"]["selected_frames"], source_n), detect_key(cur_d), link_key(cur_lk)),
                            "frame0": ALT_FRAME0,
                            "n_frames": alt["meta"]["selected_frames"],
                            "retention_summary": alt["retention"]["summary"],
                            "retention_pct": alt["retention"]["pct"],
                            "retention_label": "PROXY",
                            "runtime_s": alt["runtime_s"],
                            "detect_calls": alt["detect_calls"],
                        }
                    )
                    del alt
                    free_mem()

                if ret["pct"] < RETENTION_TARGET and n_len >= MAJOR_FRAMES:
                    passed_all = False
                    # diagnose first breaks and local-tune
                    if ret["breaks"]:
                        br = sorted(ret["breaks"], key=lambda b: b["break_frame"])[0]
                        log(
                            f"  below {RETENTION_TARGET:.0%}: first break "
                            f"frame={br['break_frame']} cause={br['cause']}"
                        )
                        cur_d, cur_lk = local_tune_around_break(
                            path, cur_d, cur_lk, br["break_frame"], br["cause"], state
                        )
                        # rerun this length once after local tune
                        r2 = run_tracked_window(path, cur_d, cur_lk, 0, n_len)
                        save_run_artifacts(
                            path,
                            f"promote_retry_n{n_len}_{detect_key(cur_d)}_{link_key(cur_lk)}",
                            r2,
                        )
                        append_result(
                            {
                                "phase": f"promote_retry_{n_len}",
                                "video": path.name,
                                "key": exp_key("promote_retry", path, tag, detect_key(cur_d), link_key(cur_lk)),
                                "frame0": 0,
                                "n_frames": n_len,
                                "retention_summary": r2["retention"]["summary"],
                                "retention_pct": r2["retention"]["pct"],
                                "retention_label": "PROXY",
                                "runtime_s": r2["runtime_s"],
                                "detect_calls": r2["detect_calls"],
                                "preprocess": cur_d.preprocess,
                                "diameter": cur_d.diameter,
                                "linker": cur_lk.method,
                            }
                        )
                        log(
                            f"  retry retention={r2['retention']['summary']} "
                            f"({r2['retention']['pct']:.0%})"
                        )
                        if r2["retention"]["pct"] < RETENTION_TARGET:
                            mark_done(state, key)
                            del r, r2
                            free_mem()
                            break
                        del r2
                    else:
                        mark_done(state, key)
                        del r
                        free_mem()
                        break

                mark_done(state, key)
                del r
                free_mem(f"after promote {n_len}")
            else:
                # completed all lengths
                winners.append({"detect": cur_d, "link": cur_lk})

            if not passed_all:
                continue

        # --- Holdout 60f not used for tuning ---
        if not winners and promoted:
            winners = [{"detect": promoted[0][0], "link": promoted[0][1]}]

        for w in winners[:3]:
            d, lk = w["detect"], w["link"]
            n_h = min(HOLDOUT_FRAMES, max(0, source_n - HOLDOUT_FRAME0))
            if n_h < 10:
                log("holdout skipped — not enough frames after frame0=90")
                continue
            tag = frame_range_tag(HOLDOUT_FRAME0, n_h, source_n)
            key = exp_key("holdout60", path, tag, detect_key(d), link_key(lk))
            if is_done(state, key):
                continue
            log(f"holdout60 @ {HOLDOUT_FRAME0} {detect_key(d)}|{link_key(lk)}")
            r = run_tracked_window(path, d, lk, HOLDOUT_FRAME0, n_h)
            save_run_artifacts(path, f"holdout_{tag}_{detect_key(d)}_{link_key(lk)}", r)
            append_result(
                {
                    "phase": "holdout60",
                    "video": path.name,
                    "key": key,
                    "frame0": HOLDOUT_FRAME0,
                    "n_frames": n_h,
                    "retention_summary": r["retention"]["summary"],
                    "retention_pct": r["retention"]["pct"],
                    "retention_label": "PROXY",
                    "runtime_s": r["runtime_s"],
                    "detect_calls": r["detect_calls"],
                    "gap_zero_frames": r["gaps"]["n_zero_detection_frames"],
                    "preprocess": d.preprocess,
                    "diameter": d.diameter,
                    "linker": lk.method,
                }
            )
            mark_done(state, key)
            log(
                f"  holdout retention={r['retention']['summary']} "
                f"({r['retention']['pct']:.0%}) PROXY t={r['runtime_s']:.1f}s"
            )
            del r
            free_mem()

        # --- Full baseline comparison last (expensive) ---
        if not is_done(state, full_key):
            log(f"FULL baseline comparison n={source_n} (separate from promotion)")
            r = run_tracked_window(path, DetectCfg(), LinkCfg(method="trackastra_greedy"), 0, source_n)
            save_run_artifacts(path, f"baseline_full_{full_tag}", r)
            append_result(
                {
                    "phase": "baseline_full",
                    "video": path.name,
                    "key": full_key,
                    "frame0": 0,
                    "n_frames": source_n,
                    "retention_summary": r["retention"]["summary"],
                    "retention_pct": r["retention"]["pct"],
                    "retention_label": "PROXY",
                    "runtime_s": r["runtime_s"],
                    "detect_calls": r["detect_calls"],
                    "gap_zero_frames": r["gaps"]["n_zero_detection_frames"],
                }
            )
            mark_done(state, full_key)
            log(
                f"baseline_full retention={r['retention']['summary']} "
                f"t={r['runtime_s']:.1f}s detect_calls={r['detect_calls']}"
            )
            del r
            free_mem("after baseline_full")

    write_staged_report()


def write_staged_report() -> None:
    lines = [
        "# Morning staged tracking report",
        "",
        "Retention labeled **PROXY** (no verified nucleus identities).",
        "Format retained/denom e.g. 17/20. Detection gaps reported separately for velocity.",
        "",
        "Schedule: screen10 → tune30 → promote 60/90/120/180/271 (80% gate) → holdout60 → baseline_full.",
        "Detections cached per frame+detector; linker-only changes use 0 detect calls.",
        "",
    ]
    results = OUT / "results.csv"
    if results.exists():
        df = pd.read_csv(results)
        lines += ["## Latest runs", "", "```"]
        cols = [c for c in ["phase", "n_frames", "retention_summary", "retention_pct", "runtime_s", "detect_calls", "gap_zero_frames"] if c in df.columns]
        lines.append(df[cols].tail(40).to_string(index=False))
        lines += ["```", ""]
    else:
        lines.append("No results yet.")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "STAGED_REPORT.md").write_text("\n".join(lines))
    # Also refresh morning report shell
    try:
        write_morning_report()
    except Exception:
        pass
    report = REPORT
    extra = "\n\n## Staged optimization\n\nSee `STAGED_REPORT.md`.\n"
    if report.exists():
        txt = report.read_text()
        if "## Staged optimization" not in txt:
            report.write_text(txt.rstrip() + extra)
    log(f"wrote {OUT / 'STAGED_REPORT.md'}")


def run_staged(state: dict, only: list[str] | None = None) -> None:
    paths = video_paths(only=only or list(DEFAULT_VIDEOS))
    log(f"staged videos: {[p.name for p in paths]}")
    phase_staged(state, paths)
    log("ALL REQUESTED PHASES COMPLETE")
