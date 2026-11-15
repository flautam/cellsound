"""Website server. Upload a video, track nuclei, then serve the pages.

Run from the repo root:

    source .venv/bin/activate
    uvicorn backend.app:app --port 8000

Open http://127.0.0.1:8000/
"""

from __future__ import annotations

import json
import re
import threading
import traceback
import zipfile
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import imageio.v3 as iio
import numpy as np
import pandas as pd
from PIL import Image
from fastapi import FastAPI, File, Form, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from backend.motion_types import apply_direction_groups, write_motion_types
from backend.pipeline import overlay_playback_fps, run_job_analysis
from backend.track import choose_device, limit_for_cpu, link_tracks, segment_nuclei, select_frames
from backend.video import load_video

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
JOBS = ROOT / "output" / "jobs"
BATCHES = ROOT / "output" / "batches"
MAX_BYTES = 100 * 1024 * 1024
MAX_BATCH = 8
_lock = threading.Lock()
_gpu = threading.Lock()
_DIRECTIONS = ("up", "down", "left", "right")
_DIRECTION_PHRASES = (
    ("toward the top", "up"),
    ("towards the top", "up"),
    ("to the top", "up"),
    ("upward", "up"),
    ("upwards", "up"),
    ("toward the bottom", "down"),
    ("towards the bottom", "down"),
    ("to the bottom", "down"),
    ("downward", "down"),
    ("downwards", "down"),
    ("toward the left", "left"),
    ("towards the left", "left"),
    ("to the left", "left"),
    ("leftward", "left"),
    ("leftwards", "left"),
    ("toward the right", "right"),
    ("towards the right", "right"),
    ("to the right", "right"),
    ("rightward", "right"),
    ("rightwards", "right"),
)

app = FastAPI(title="cellsound")
# Pages on cellsound.select call this server on another host. Same-origin
# use (this process, or a laptop on 127.0.0.1) does not need these origins.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://cellsound.select",
        "https://www.cellsound.select",
        "https://8000--main--cellsound--flautam.coder.c1.workshop.cdr.dev",
    ],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Accept", "Content-Type"],
)


def _job_dir(job_id: str) -> Path:
    return JOBS / job_id


def _read_meta(job_id: str) -> dict:
    path = _job_dir(job_id) / "meta.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def _write_meta(job_id: str, **changes) -> dict:
    folder = _job_dir(job_id)
    folder.mkdir(parents=True, exist_ok=True)
    with _lock:
        meta = _read_meta(job_id)
        meta.update(changes)
        (folder / "meta.json").write_text(json.dumps(meta))
        return meta


def _missing(job_id: str):
    if not (_job_dir(job_id) / "meta.json").exists():
        return JSONResponse(status_code=404, content={"error": "missing_job", "message": "That analysis was not found."})
    return None


def _preview_frame(frame):
    """Save the real first frame as an 8-bit picture the page can draw."""
    image = np.asarray(frame)
    if image.ndim == 3 and image.shape[-1] in (3, 4):
        image = image[..., :3]
    if np.issubdtype(image.dtype, np.floating):
        image = image.astype(np.float32)
        peak = float(np.nanmax(image)) if image.size else 0.0
        # Float stacks are often ~0–1 with a little headroom above 1.0.
        if peak <= 1.5:
            image = image * 255.0
        image = np.clip(np.rint(image), 0, 255).astype(np.uint8)
    else:
        image = np.clip(image, 0, 255).astype(np.uint8)
    return image


def _source_fps(video_path: Path) -> float:
    """Read the upload's frame rate; fall back to 15 if metadata is missing."""
    try:
        fps = iio.improps(video_path).fps
        if fps and float(fps) > 0:
            return float(fps)
    except Exception:
        pass
    try:
        import cv2

        cap = cv2.VideoCapture(str(video_path))
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0)
        cap.release()
        if fps > 0:
            return fps
    except Exception:
        pass
    return 15.0


def _source_preview(video_path: Path, fallback):
    """Prefer the untouched first frame of the uploaded file."""
    try:
        if video_path.is_file():
            return _preview_frame(iio.imread(video_path, index=0))
    except Exception:
        pass
    return _preview_frame(fallback)


def _track(job_id: str, video_path: Path) -> None:
    try:
        device = choose_device()
        # Always track 30 frames so video, tracks, and sound stay aligned.
        frame_limit, crop, note = limit_for_cpu(device.type, 30, None)
        if note is None:
            note = "Tracking the first 30 frames."
        source_fps = _source_fps(video_path)
        _write_meta(
            job_id,
            status="running",
            stage="received",
            message=note or "Video received",
            fps=source_fps,
        )
        video = select_frames(load_video(video_path, max_frames=frame_limit), None, crop)
        total, height, width = (int(video.shape[0]), int(video.shape[1]), int(video.shape[2]))
        Image.fromarray(_source_preview(video_path, video[0])).save(_job_dir(job_id) / "frame0.png")
        _write_meta(
            job_id,
            stage="tracking",
            frame=0,
            total_frames=total,
            width=width,
            height=height,
            fps=source_fps,
            message="Tracking cells",
        )

        def on_frame(done, count):
            _write_meta(
                job_id,
                frame=done,
                total_frames=count,
                stage="tracking",
                message="Tracking cells frame " + str(done) + " of " + str(count),
            )

        masks = segment_nuclei(video, device, on_frame=on_frame)
        table = link_tracks(video, masks)
        folder = _job_dir(job_id)
        table.to_csv(folder / "tracks.csv", index=False)
        # Save the exact frames used for tracking so cluster overlays match.
        # frame0.png stays the untouched upload frame written above.
        try:
            import cv2

            gray_u8 = _preview_frame(video)
            if gray_u8.ndim == 2:
                gray_u8 = gray_u8[np.newaxis, ...]
            rgb = np.stack([gray_u8, gray_u8, gray_u8], axis=-1)
            tmp = folder / "tracked.tmp.mp4"
            writer = None
            for tag in ("avc1", "H264", "mp4v"):
                candidate = cv2.VideoWriter(
                    str(tmp),
                    cv2.VideoWriter_fourcc(*tag),
                    float(source_fps),
                    (width, height),
                )
                if candidate.isOpened():
                    writer = candidate
                    break
                candidate.release()
            if writer is None:
                raise RuntimeError("Could not open VideoWriter for tracked.mp4")
            for frame in rgb:
                writer.write(cv2.cvtColor(frame, cv2.COLOR_RGB2BGR))
            writer.release()
            tmp.replace(folder / "tracked.mp4")
        except Exception:
            # Overlay render will skip if this file is missing.
            pass
        _write_meta(
            job_id,
            status="done",
            stage="done",
            frame=total,
            total_frames=total,
            message="Tracking finished",
            width=width,
            height=height,
            fps=source_fps,
        )
    except Exception as exc:
        traceback.print_exc()
        _write_meta(job_id, status="error", stage="error", message=str(exc) or "Tracking stopped because of an error.")


def _progress(meta: dict) -> dict:
    return {
        "status": meta.get("status", "queued"),
        "stage": meta.get("stage", "received"),
        "frame": int(meta.get("frame") or 0),
        "total_frames": int(meta.get("total_frames") or 0),
        "message": meta.get("message") or "",
    }


@app.post("/api/jobs")
async def create_job(
    video: UploadFile = File(...),
    pixel_size_um: float | None = Form(None),
    minutes_per_frame: float | None = Form(None),
):
    name = (video.filename or "").lower()
    if name.endswith(".mov") or video.content_type == "video/quicktime":
        suffix = ".mov"
    elif name.endswith(".mp4") or video.content_type in {"video/mp4", "application/mp4"}:
        suffix = ".mp4"
    else:
        return JSONResponse(status_code=400, content={"error": "not_mp4", "message": "That file is not an MP4 or MOV video."})
    payload = await video.read()
    if len(payload) > MAX_BYTES:
        return JSONResponse(status_code=413, content={"error": "too_large", "message": "The video is larger than 100 MB."})
    job_id = uuid4().hex[:6]
    folder = _job_dir(job_id)
    folder.mkdir(parents=True, exist_ok=True)
    video_path = folder / f"video{suffix}"
    video_path.write_bytes(payload)
    _write_meta(
        job_id,
        status="queued",
        stage="received",
        frame=0,
        total_frames=0,
        message="Video received",
        pixel_size_um=pixel_size_um,
        minutes_per_frame=minutes_per_frame,
        width=0,
        height=0,
    )
    def _run():
        with _gpu:
            _track(job_id, video_path)

    threading.Thread(target=_run, daemon=True).start()
    return {"job_id": job_id}


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str):
    missing = _missing(job_id)
    if missing:
        return missing
    return _progress(_read_meta(job_id))


@app.get("/api/jobs/{job_id}/frame0.png")
def frame0_png(job_id: str):
    path = _job_dir(job_id) / "frame0.png"
    if not path.exists():
        return JSONResponse(status_code=404, content={"error": "not_ready", "message": "The first frame is not ready yet."})
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "no-store"})


@app.get("/api/jobs/{job_id}/frame0")
def frame0(job_id: str):
    missing = _missing(job_id)
    if missing:
        return missing
    tracks_path = _job_dir(job_id) / "tracks.csv"
    if not tracks_path.exists():
        return JSONResponse(status_code=404, content={"error": "not_ready", "message": "Tracking is not finished yet."})
    meta = _read_meta(job_id)
    tracks = pd.read_csv(tracks_path)
    if tracks.empty:
        cells = []
    else:
        first = int(tracks["frame"].min())
        cells = [
            {"cell_id": int(row.cell_id), "x": float(row.x), "y": float(row.y)}
            for row in tracks[tracks["frame"] == first].itertuples(index=False)
        ]
    return {
        "image_url": f"/api/jobs/{job_id}/frame0.png",
        "width": int(meta.get("width") or 0),
        "height": int(meta.get("height") or 0),
        "cells": cells,
    }


def _video_file(folder: Path) -> Path | None:
    for path in sorted(folder.glob("video.*")):
        if path.is_file() and path.suffix.lower() in {".mov", ".mp4", ".avi", ".mkv", ".webm"}:
            return path
    return None


def save_step_frames(folder: Path, max_side: int = 480, limit: int = 360) -> int:
    """Save a small JPEG of each video frame so the overview can step the picture."""
    video_path = _video_file(folder)
    dest = folder / "frames"
    dest.mkdir(parents=True, exist_ok=True)
    for old in dest.glob("*.jpg"):
        old.unlink()
    if video_path is None:
        return 0
    count = 0
    try:
        for index, frame in enumerate(iio.imiter(video_path)):
            if index >= limit:
                break
            image = _preview_frame(frame)
            picture = Image.fromarray(image)
            width, height = picture.size
            longest = max(width, height, 1)
            if longest > max_side:
                scale = max_side / longest
                picture = picture.resize(
                    (max(1, int(width * scale)), max(1, int(height * scale))),
                    Image.Resampling.BILINEAR,
                )
            picture.save(dest / f"{index}.jpg", quality=72, optimize=True)
            count += 1
    except Exception:
        traceback.print_exc()
    return count


def _attach_frames(job_id: str, data: dict) -> dict:
    folder = _job_dir(job_id)
    dest = folder / "frames"
    count = len(list(dest.glob("*.jpg"))) if dest.is_dir() else 0
    if count == 0:
        count = save_step_frames(folder)
    data["frames_url"] = f"/api/jobs/{job_id}/frames/{{index}}.jpg"
    data["frame_count"] = count
    return data


@app.post("/api/jobs/{job_id}/analyze")
def analyze(job_id: str, body: dict):
    missing = _missing(job_id)
    if missing:
        return missing
    tracks_path = _job_dir(job_id) / "tracks.csv"
    if not tracks_path.exists():
        return JSONResponse(status_code=404, content={"error": "not_ready", "message": "Tracking is not finished yet."})
    meta = _read_meta(job_id)
    tracks = pd.read_csv(tracks_path)
    folder = _job_dir(job_id)
    try:
        _write_meta(job_id, status="running", stage="measuring", message="Clustering and writing summaries")
        run_job_analysis(
            folder,
            tracks,
            body,
            int(meta.get("width") or 1),
            int(meta.get("height") or 1),
            meta.get("pixel_size_um"),
            meta.get("minutes_per_frame"),
            fps=meta.get("fps"),
        )
        for stale in folder.glob("chunk_*_overlay.mp4"):
            stale.unlink(missing_ok=True)
        for stale in folder.glob("chunk_*_map.png"):
            stale.unlink(missing_ok=True)
    except Exception as exc:
        traceback.print_exc()
        _write_meta(job_id, status="error", message=str(exc) or "The analysis could not finish.")
        return JSONResponse(status_code=500, content={"error": "analysis_failed", "message": str(exc)})
    # Pipeline already wrote summary/traces/overlay; attach frame0 for the trail backdrop.
    traces_path = folder / "traces.json"
    if traces_path.exists():
        traces = json.loads(traces_path.read_text())
        traces["image_url"] = f"/api/jobs/{job_id}/frame0.png"
        save_step_frames(folder)
        _attach_frames(job_id, traces)
        traces_path.write_text(json.dumps(traces, indent=2))
    (folder / "settings.json").write_text(json.dumps(body))
    try:
        write_motion_types(folder)
    except Exception:
        traceback.print_exc()
    _write_meta(job_id, status="done", stage="done", message="Analysis finished")
    return {"status": "done"}


@app.get("/api/jobs/{job_id}/summary")
def summary(job_id: str):
    path = _job_dir(job_id) / "summary.json"
    if not path.exists():
        return JSONResponse(status_code=404, content={"error": "not_ready", "message": "Run the analysis before opening the summary."})
    return json.loads(path.read_text())


@app.get("/api/jobs/{job_id}/traces")
def traces(job_id: str):
    path = _job_dir(job_id) / "traces.json"
    if not path.exists():
        return JSONResponse(status_code=404, content={"error": "not_ready", "message": "Run the analysis before opening the traces."})
    data = json.loads(path.read_text())
    data["image_url"] = f"/api/jobs/{job_id}/frame0.png"
    return _attach_frames(job_id, data)


@app.get("/api/jobs/{job_id}/frames/{index}.jpg")
def playback_frame(job_id: str, index: int):
    path = _job_dir(job_id) / "frames" / f"{index}.jpg"
    if not path.exists():
        return JSONResponse(status_code=404, content={"error": "not_ready", "message": "That video frame is not ready yet."})
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@app.get("/api/jobs/{job_id}/audio/{name}.wav")
def audio(job_id: str, name: str):
    path = _job_dir(job_id) / "sounds" / f"{name}.wav"
    if not path.exists():
        return JSONResponse(status_code=404, content={"error": "missing_audio", "message": "That sound is not ready yet."})
    return FileResponse(path, media_type="audio/wav", headers={"Cache-Control": "no-store"})


@app.get("/api/jobs/{job_id}/clusters_overlay.mp4")
def clusters_overlay(job_id: str):
    path = _job_dir(job_id) / "clusters_overlay.mp4"
    if not path.exists():
        return JSONResponse(
            status_code=404,
            content={"error": "missing_video", "message": "The cluster overlay video is not ready yet."},
        )
    return FileResponse(path, media_type="video/mp4", headers={"Cache-Control": "no-store"})


@app.get("/api/jobs/{job_id}/chunks/{cluster_id}/video.mp4")
def chunk_overlay(job_id: str, cluster_id: int):
    """Overlay with only one chunk drawn, cached per cluster id."""
    folder = _job_dir(job_id)
    out = folder / f"chunk_{int(cluster_id)}_overlay.mp4"
    if out.exists():
        return FileResponse(out, media_type="video/mp4", headers={"Cache-Control": "no-store"})
    missing = _missing(job_id)
    if missing:
        return missing
    clusters_path = folder / "clusters.csv"
    summary_path = folder / "cluster_summary.csv"
    motion_path = folder / "motion.csv"
    video_path = folder / "tracked.mp4"
    if not video_path.exists():
        video_path = folder / "video.mp4"
    for path, label in [
        (clusters_path, "clusters"),
        (summary_path, "cluster summary"),
        (motion_path, "motion"),
        (video_path, "tracked video"),
    ]:
        if not path.exists():
            return JSONResponse(
                status_code=404,
                content={"error": "not_ready", "message": f"Missing {label} for this chunk video."},
            )
    try:
        import pandas as pd
        from backend.render_cluster_video import render

        clusters = pd.read_csv(clusters_path)
        summary = pd.read_csv(summary_path)
        one = clusters[clusters["cluster_id"] == int(cluster_id)]
        sum_one = summary[summary["cluster_id"] == int(cluster_id)]
        if one.empty:
            return JSONResponse(
                status_code=404,
                content={"error": "missing_chunk", "message": f"Chunk {cluster_id} was not found."},
            )
        tmp_dir = folder / "_chunk_tmp"
        tmp_dir.mkdir(exist_ok=True)
        cl = tmp_dir / f"clusters_{int(cluster_id)}.csv"
        sm = tmp_dir / f"summary_{int(cluster_id)}.csv"
        one.to_csv(cl, index=False)
        sum_one.to_csv(sm, index=False)
        meta = _read_meta(job_id)
        fps = overlay_playback_fps(meta.get("fps"))
        # Render in a temp folder so we do not overwrite the full-field clusters_map.png.
        tmp_out = tmp_dir / f"chunk_{int(cluster_id)}_overlay.mp4"
        render(video_path, motion_path, cl, sm, tmp_out, fps=fps)
        tmp_out.replace(out)
        (tmp_dir / "clusters_map.png").unlink(missing_ok=True)
    except Exception as exc:
        traceback.print_exc()
        return JSONResponse(
            status_code=500,
            content={"error": "render_failed", "message": str(exc) or "Could not render that chunk video."},
        )
    if not out.exists():
        return JSONResponse(
            status_code=500,
            content={"error": "render_failed", "message": "Chunk video was not written."},
        )
    return FileResponse(out, media_type="video/mp4", headers={"Cache-Control": "no-store"})


@app.get("/api/jobs/{job_id}/clusters_map.png")
def clusters_map(job_id: str):
    path = _job_dir(job_id) / "clusters_map.png"
    if not path.exists():
        return JSONResponse(
            status_code=404,
            content={"error": "missing_map", "message": "The cluster map image is not ready yet."},
        )
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "no-store"})


def _export_slug(label: str, job_id: str) -> str:
    """Folder-safe video name for export zip paths."""
    stem = Path(str(label or "").strip()).stem or str(job_id)
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._")
    return cleaned[:80] or str(job_id)


def _job_display_name(job_id: str) -> str:
    """Prefer the original upload filename from a batch record."""
    if BATCHES.exists():
        for path in BATCHES.glob("*/batch.json"):
            try:
                payload = json.loads(path.read_text())
            except Exception:
                continue
            for item in payload.get("videos") or []:
                if item.get("job_id") == job_id and item.get("name"):
                    return str(item["name"])
    return job_id


def _add_job_to_export(archive: zipfile.ZipFile, job_id: str, wanted: set[str], root: str) -> bool:
    """Write one job's selected files under root/. Returns False if not ready."""
    folder = _job_dir(job_id)
    if not (folder / "summary.json").exists():
        return False
    prefix = root.strip("/") + "/" if root else ""
    summary_json = json.loads((folder / "summary.json").read_text())

    if "text" in wanted:
        overview = (summary_json.get("overview") or {}).get("description") or (
            (summary_json.get("population") or {}).get("description") or ""
        )
        video_label = _job_display_name(job_id)
        lines = [f"Video: {video_label}", "", "Video overview", overview, "", "Chunks"]
        clusters = sorted(
            summary_json.get("clusters") or [],
            key=lambda row: int(row.get("cluster_id") or 0),
        )
        for cluster in clusters:
            cid = cluster.get("cluster_id")
            name = cluster.get("name") or ""
            desc = cluster.get("description") or ""
            tech = cluster.get("description_tech") or ""
            header = f"Chunk {cid} (rank {cluster.get('rank', '?')}): {name}".strip()
            lines.append(header)
            if desc:
                lines.append(desc)
            if tech:
                lines.append(tech)
            lines.append("")
            chunk_lines = [f"Video: {video_label}", header, "", desc]
            if tech:
                chunk_lines.extend(["", tech])
            archive.writestr(
                f"{prefix}descriptions/chunk_{cid}.txt",
                "\n".join(chunk_lines).strip() + "\n",
            )
        archive.writestr(f"{prefix}descriptions/overview.txt", f"Video: {video_label}\n\n{overview}\n")
        archive.writestr(f"{prefix}descriptions/summary.txt", "\n".join(lines).rstrip() + "\n")
        archive.write(folder / "summary.json", f"{prefix}descriptions/summary.json")

    # "data" keeps older clients working; newer UI sends the split keys below.
    want_tracks = "tracks" in wanted or "data" in wanted
    want_chunks_csv = "chunks_csv" in wanted or "data" in wanted
    want_tracking_video = "tracking_video" in wanted or "data" in wanted
    want_overview_video = "overview_video" in wanted or "data" in wanted
    want_map = "map" in wanted or "data" in wanted

    if want_tracks:
        for name in ("tracks.csv", "motion.csv"):
            path = folder / name
            if path.exists():
                archive.write(path, f"{prefix}data/{name}")

    if want_chunks_csv:
        for name in (
            "clusters.csv",
            "cluster_summary.csv",
            "cluster_analysis.csv",
            "cluster_timeseries.csv",
        ):
            path = folder / name
            if path.exists():
                archive.write(path, f"{prefix}data/{name}")
        tmp = folder / "_chunk_tmp"
        if tmp.exists():
            for csv_path in sorted(tmp.glob("*.csv")):
                archive.write(csv_path, f"{prefix}data/chunks/{csv_path.name}")

    if want_tracking_video and (folder / "tracked.mp4").exists():
        archive.write(folder / "tracked.mp4", f"{prefix}videos/tracked.mp4")

    if want_overview_video:
        if (folder / "clusters_overlay.mp4").exists():
            archive.write(folder / "clusters_overlay.mp4", f"{prefix}videos/overview_clusters.mp4")
            archive.write(folder / "clusters_overlay.mp4", f"{prefix}data/clusters_overlay.mp4")
        for chunk_video in sorted(folder.glob("chunk_*_overlay.mp4")):
            archive.write(chunk_video, f"{prefix}videos/{chunk_video.name}")

    if want_map and (folder / "clusters_map.png").exists():
        archive.write(folder / "clusters_map.png", f"{prefix}maps/clusters_map.png")

    if "settings" in wanted and (folder / "settings.json").exists():
        archive.write(folder / "settings.json", f"{prefix}settings.json")
    if "audio" in wanted and (folder / "sounds").exists():
        for wav in (folder / "sounds").glob("*.wav"):
            archive.write(wav, f"{prefix}sounds/{wav.name}")
    return True


@app.get("/api/jobs/{job_id}/export.zip")
def export_zip(
    job_id: str,
    include: str = "audio,text,tracks,chunks_csv,tracking_video,overview_video,map,settings",
    jobs: str = "",
):
    wanted = {part.strip() for part in include.split(",") if part.strip()}
    job_ids = [part.strip() for part in jobs.split(",") if part.strip()] if jobs else [job_id]
    job_ids = [jid for jid in job_ids if _safe_job_id(jid)]
    if not job_ids:
        job_ids = [job_id] if _safe_job_id(job_id) else []
    if not job_ids:
        return JSONResponse(status_code=404, content={"error": "missing_job", "message": "That analysis was not found."})

    # Keep folder names unique when two uploads share a filename.
    used_slugs: set[str] = set()
    planned: list[tuple[str, str]] = []
    for jid in job_ids:
        slug = _export_slug(_job_display_name(jid), jid)
        base = slug
        index = 2
        while slug in used_slugs:
            slug = f"{base}_{index}"
            index += 1
        used_slugs.add(slug)
        planned.append((jid, slug))

    buffer = BytesIO()
    packed = 0
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for jid, slug in planned:
            # One video → files under that video's folder so descriptions stay grouped.
            if _add_job_to_export(archive, jid, wanted, slug):
                packed += 1
        if packed == 0:
            return JSONResponse(status_code=404, content={"error": "not_ready", "message": "Run the analysis before downloading."})
        if "text" in wanted and packed > 1:
            index_lines = ["Descriptions by video", ""]
            for jid, slug in planned:
                if (_job_dir(jid) / "summary.json").exists():
                    index_lines.append(f"- {slug}/descriptions/  ({_job_display_name(jid)})")
            archive.writestr("descriptions_index.txt", "\n".join(index_lines) + "\n")
    return Response(
        content=buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=cellsound-results.zip"},
    )


def _batch_path(batch_id: str) -> Path:
    return BATCHES / batch_id / "batch.json"


def _read_batch(batch_id: str) -> dict | None:
    path = _batch_path(batch_id)
    if not path.exists():
        return None
    return json.loads(path.read_text())


def _write_batch(batch_id: str, payload: dict) -> None:
    path = _batch_path(batch_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2))


def _video_suffix(name: str, content_type: str | None) -> str | None:
    lowered = (name or "").lower()
    if lowered.endswith(".mov") or content_type == "video/quicktime":
        return ".mov"
    if lowered.endswith(".mp4") or content_type in {"video/mp4", "application/mp4"}:
        return ".mp4"
    return None


def _batch_view(payload: dict) -> dict:
    videos = []
    for item in payload.get("videos", []):
        meta = _read_meta(item["job_id"])
        videos.append(
            {
                "job_id": item["job_id"],
                "name": item.get("name") or "video",
                "status": item.get("status") or "queued",
                "frame": int(meta.get("frame") or 0),
                "total_frames": int(meta.get("total_frames") or 0),
                # Prefer live job meta while this video is running so the UI
                # shows frame progress instead of the stale batch queue note.
                "message": (
                    meta.get("message")
                    if (item.get("status") or "") == "running"
                    else item.get("message")
                )
                or meta.get("message")
                or "",
            }
        )
    states = [video["status"] for video in videos]
    if any(state in {"queued", "running"} for state in states):
        status = "running" if any(state == "running" for state in states) or any(state == "done" for state in states) else "queued"
    elif states and all(state == "error" for state in states):
        status = "error"
    else:
        status = "done"
    return {"batch_id": payload.get("batch_id"), "status": status, "videos": videos}


def _finish_one_video(job_id: str) -> None:
    """Track one video, then cluster it around the image center. One at a time."""
    folder = _job_dir(job_id)
    video_path = _video_file(folder)
    if video_path is None:
        raise FileNotFoundError("The uploaded video could not be found.")
    with _gpu:
        _track(job_id, video_path)
        meta = _read_meta(job_id)
        if meta.get("status") == "error":
            raise RuntimeError(meta.get("message") or "Tracking stopped because of an error.")
        width = int(meta.get("width") or 1)
        height = int(meta.get("height") or 1)
        body = {"poa": {"x": width / 2, "y": height / 2}}
        tracks = pd.read_csv(folder / "tracks.csv")
        _write_meta(job_id, status="running", stage="measuring", message="Clustering this video")
        run_job_analysis(
            folder,
            tracks,
            body,
            width,
            height,
            meta.get("pixel_size_um"),
            meta.get("minutes_per_frame"),
            fps=meta.get("fps"),
        )
        (folder / "settings.json").write_text(json.dumps(body))
        write_motion_types(folder)
        _write_meta(job_id, status="done", stage="done", message="This video is ready")


def _run_batch(batch_id: str) -> None:
    payload = _read_batch(batch_id)
    if payload is None:
        return
    for item in payload.get("videos", []):
        item["status"] = "running"
        item["message"] = "Waiting for this video's turn"
        _write_batch(batch_id, payload)
        try:
            _finish_one_video(item["job_id"])
            item["status"] = "done"
            item["message"] = "This video is ready"
        except Exception as exc:
            traceback.print_exc()
            item["status"] = "error"
            item["message"] = str(exc) or "This video could not be processed."
        _write_batch(batch_id, payload)


@app.post("/api/batches")
async def create_batch(
    videos: list[UploadFile] = File(...),
    pixel_size_um: float | None = Form(None),
    minutes_per_frame: float | None = Form(None),
):
    if not videos:
        return JSONResponse(status_code=400, content={"error": "no_video", "message": "Choose at least one MP4 or MOV video."})
    if len(videos) > MAX_BATCH:
        return JSONResponse(status_code=400, content={"error": "too_many", "message": f"Choose at most {MAX_BATCH} videos at a time."})
    batch_id = uuid4().hex[:6]
    saved = []
    for video in videos:
        suffix = _video_suffix(video.filename or "", video.content_type)
        if suffix is None:
            return JSONResponse(status_code=400, content={"error": "not_mp4", "message": "Each file must be an MP4 or MOV video."})
        payload = await video.read()
        if len(payload) > MAX_BYTES:
            return JSONResponse(status_code=413, content={"error": "too_large", "message": "Each video must be 100 MB or smaller."})
        job_id = uuid4().hex[:6]
        folder = _job_dir(job_id)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"video{suffix}").write_bytes(payload)
        _write_meta(
            job_id,
            status="queued",
            stage="received",
            frame=0,
            total_frames=0,
            message="Waiting for this video's turn",
            pixel_size_um=pixel_size_um,
            minutes_per_frame=minutes_per_frame,
            width=0,
            height=0,
            batch_id=batch_id,
        )
        saved.append({"job_id": job_id, "name": video.filename or f"video{suffix}", "status": "queued", "message": "Waiting for this video's turn"})
    record = {"batch_id": batch_id, "videos": saved}
    _write_batch(batch_id, record)
    threading.Thread(target=_run_batch, args=(batch_id,), daemon=True).start()
    return _batch_view(record)


@app.get("/api/batches/{batch_id}")
def batch_status(batch_id: str):
    payload = _read_batch(batch_id)
    if payload is None:
        return JSONResponse(status_code=404, content={"error": "missing_batch", "message": "That group of videos was not found."})
    return _batch_view(payload)


def _safe_job_id(job_id: str) -> bool:
    if not job_id or len(job_id) > 64:
        return False
    return all(char.isalnum() or char in "-_" for char in job_id)


_SORT_READY = "Ask a question to sort these videos by behavior. For example: show videos where cells move up."
_SORT_NEEDS_DIRECTION = "The question needs a direction: up, down, left, or right."


@app.get("/api/jobs/{job_id}/motion-types")
def motion_types(job_id: str):
    if not _safe_job_id(job_id):
        return JSONResponse(status_code=404, content={"error": "missing_job", "message": "That analysis was not found."})
    missing = _missing(job_id)
    if missing:
        return missing
    folder = _job_dir(job_id)
    path = folder / "motion_types.json"
    if not path.exists() and all((folder / name).exists() for name in ("summary.json", "motion.csv", "tracks.csv")):
        try:
            write_motion_types(folder)
        except Exception:
            traceback.print_exc()
    if not path.exists():
        return JSONResponse(
            status_code=404,
            content={"error": "not_ready", "message": "Finish cell tracing before behavior labels are available."},
        )
    payload = json.loads(path.read_text())
    return apply_direction_groups(payload)


@app.get("/api/sort")
def sort_status():
    return {"configured": True, "message": _SORT_READY}


def _labels_for_sort(job_id: str, name: str) -> dict:
    path = _job_dir(job_id) / "motion_types.json"
    behaviors = []
    if path.exists():
        try:
            data = apply_direction_groups(json.loads(path.read_text()))
        except Exception:
            data = {}
        for cluster in data.get("clusters") or []:
            behaviors.append(
                {
                    "main_type": cluster.get("main_type"),
                    "search_types": cluster.get("search_types") or [],
                    "n_cells": cluster.get("n_cells"),
                    "description": cluster.get("description") or "",
                }
            )
    return {"job_id": job_id, "name": name, "behaviors": behaviors}


def _directions_in_text(text: str) -> list[str]:
    """Directions named in text, in the order they appear."""
    lowered = (text or "").lower()
    hits: list[tuple[int, str]] = []
    occupied: list[tuple[int, int]] = []

    def overlaps(start: int, end: int) -> bool:
        return any(start < stop and end > begin for begin, stop in occupied)

    for phrase, direction in _DIRECTION_PHRASES:
        start = 0
        while True:
            index = lowered.find(phrase, start)
            if index < 0:
                break
            end = index + len(phrase)
            if not overlaps(index, end):
                hits.append((index, direction))
                occupied.append((index, end))
            start = index + 1
    for direction in _DIRECTIONS:
        for match in re.finditer(rf"\b{direction}\b", lowered):
            if not overlaps(match.start(), match.end()):
                hits.append((match.start(), direction))
                occupied.append((match.start(), match.end()))
    hits.sort(key=lambda item: item[0])
    found: list[str] = []
    seen: set[str] = set()
    for _, direction in hits:
        if direction not in seen:
            seen.add(direction)
            found.append(direction)
    return found


def _direction_name(value) -> str:
    name = str(value or "").strip().lower()
    return name if name in _DIRECTIONS else ""


def _labels_from_request(item: dict) -> list | None:
    """Labels sent with one video. None means read the labels stored for that job."""
    behaviors = item.get("behaviors")
    if isinstance(behaviors, list):
        return [entry for entry in behaviors if isinstance(entry, dict)]
    label_keys = ("behavior", "main_type", "search_types", "description", "labels")
    if not any(key in item for key in label_keys):
        return None
    search = item.get("search_types")
    labels = item.get("labels")
    if not isinstance(search, list):
        search = labels if isinstance(labels, list) else []
    description = item.get("description") or item.get("behavior") or ""
    if isinstance(labels, str) and not description:
        description = labels
    return [
        {
            "main_type": item.get("main_type") or "",
            "search_types": search,
            "n_cells": item.get("n_cells") or 0,
            "description": description,
        }
    ]


def _cluster_directions(cluster: dict) -> tuple[str, set[str]]:
    found: list[str] = []
    seen: set[str] = set()

    def add(name: str) -> None:
        if name and name not in seen:
            seen.add(name)
            found.append(name)

    main = _direction_name(cluster.get("main_type"))
    add(main)
    search = cluster.get("search_types") or []
    if isinstance(search, list):
        for name in search:
            add(_direction_name(name))
    for name in _directions_in_text(str(cluster.get("description") or "")):
        add(name)
    for name in _directions_in_text(str(cluster.get("behavior") or "")):
        add(name)
    if not main and found:
        main = found[0]
    return main, set(found)


def _video_profile(behaviors: list) -> tuple[str, set[str]]:
    ranked = []
    for cluster in behaviors:
        if not isinstance(cluster, dict):
            continue
        try:
            weight = int(cluster.get("n_cells") or 0)
        except (TypeError, ValueError):
            weight = 0
        main, found = _cluster_directions(cluster)
        ranked.append((weight, main, found))
    if not ranked:
        return "", set()
    ranked.sort(key=lambda row: row[0], reverse=True)
    main = next((row[1] for row in ranked if row[1]), "")
    found: set[str] = set()
    for _, _, names in ranked:
        found.update(names)
    return main, found


def _match_rank(main: str, found: set[str], asked: list[str]) -> tuple[int, int, int] | None:
    """Match only the video's main behavior (what Overview shows), not side groups."""
    del found  # kept for call-site compatibility; secondary labels do not match
    if not main or main not in asked:
        return None
    earliest = asked.index(main)
    return (1, 1, -earliest)


def _direction_phrase(names: list[str]) -> str:
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} or {names[1]}"
    return ", ".join(names[:-1]) + ", or " + names[-1]


def _sort_reason(asked: list[str], matched: list[set[str]]) -> str:
    if not asked:
        return _SORT_NEEDS_DIRECTION
    present = [name for name in asked if any(name in found for found in matched)]
    if not present:
        return f"None of these videos move {_direction_phrase(asked)}. They stay in their current order."
    return f"These videos mostly move {_direction_phrase(present)}."


@app.post("/api/sort")
def sort_videos(body: dict):
    question = str((body or {}).get("question") or "").strip()
    if not question:
        return JSONResponse(status_code=400, content={"error": "empty_question", "message": "Type a question before sorting."})
    question = question[:500]
    raw = (body or {}).get("videos") or []
    if not isinstance(raw, list) or not raw:
        return JSONResponse(status_code=400, content={"error": "no_videos", "message": "There are no videos to sort."})
    videos = []
    for item in raw[:MAX_BATCH]:
        if not isinstance(item, dict):
            continue
        job_id = str(item.get("job_id") or "")
        if not _safe_job_id(job_id):
            continue
        supplied = _labels_from_request(item)
        if supplied is None:
            supplied = _labels_for_sort(job_id, str(item.get("name") or job_id)[:200])["behaviors"]
        videos.append({"job_id": job_id, "behaviors": supplied})
    if not videos:
        return JSONResponse(status_code=400, content={"error": "no_videos", "message": "There are no videos to sort."})
    asked = _directions_in_text(question)
    order = [video["job_id"] for video in videos]
    if not asked:
        return {"configured": True, "job_ids": order, "reason": _SORT_NEEDS_DIRECTION}
    ranked = []
    for index, video in enumerate(videos):
        main, found = _video_profile(video["behaviors"])
        ranked.append((_match_rank(main, found, asked), index, video["job_id"], found))
    matched = [row for row in ranked if row[0] is not None]
    missed = [row for row in ranked if row[0] is None]
    matched.sort(key=lambda row: (-row[0][0], -row[0][1], -row[0][2], row[1]))
    missed.sort(key=lambda row: row[1])
    ordered = [row[2] for row in matched + missed]
    matched_ids = [row[2] for row in matched]
    matched_mains: list[set[str]] = []
    for _rank, _index, job_id, _found in matched:
        for video in videos:
            if video["job_id"] != job_id:
                continue
            main, _ = _video_profile(video["behaviors"])
            if main:
                matched_mains.append({main})
            break
    reason = _sort_reason(asked, matched_mains)
    return {
        "configured": True,
        "job_ids": ordered,
        "matched_job_ids": matched_ids,
        "reason": reason,
    }


app.mount("/", StaticFiles(directory=FRONTEND, html=True), name="frontend")
