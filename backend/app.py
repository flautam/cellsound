"""Website server. Upload a video, track nuclei, then serve the pages.

Run from the repo root:

    source .venv/bin/activate
    uvicorn backend.app:app --port 8000

Open http://127.0.0.1:8000/
"""

from __future__ import annotations

import json
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
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from backend.motion_types import write_motion_types
from backend.pipeline import run_job_analysis
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

app = FastAPI(title="cellsound")


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
            message="Detecting and tracking nuclei",
        )

        def on_frame(done, count):
            _write_meta(job_id, frame=done, total_frames=count, stage="tracking")

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
    threading.Thread(target=_track, args=(job_id, video_path), daemon=True).start()
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
        fps = float(meta.get("fps") or 15.0)
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


@app.get("/api/jobs/{job_id}/export.zip")
def export_zip(job_id: str, include: str = "audio,text,data,settings"):
    folder = _job_dir(job_id)
    if not (folder / "summary.json").exists():
        return JSONResponse(status_code=404, content={"error": "not_ready", "message": "Run the analysis before downloading."})
    wanted = {part.strip() for part in include.split(",") if part.strip()}
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        if "text" in wanted:
            summary_json = json.loads((folder / "summary.json").read_text())
            overview = (summary_json.get("overview") or {}).get("description") or summary_json["population"]["description"]
            lines = ["Video overview", overview, "", "Chunks"]
            for cluster in summary_json.get("clusters", []):
                lines.append(
                    f"Chunk {cluster['cluster_id']} (rank {cluster.get('rank', '?')}): "
                    f"{cluster.get('name', '')}. {cluster.get('description', '')}"
                )
            archive.writestr("descriptions/summary.txt", "\n".join(lines) + "\n")
        if "data" in wanted and (folder / "tracks.csv").exists():
            archive.write(folder / "tracks.csv", "data/tracks.csv")
            if (folder / "motion.csv").exists():
                archive.write(folder / "motion.csv", "data/motion.csv")
            if (folder / "clusters.csv").exists():
                archive.write(folder / "clusters.csv", "data/clusters.csv")
            if (folder / "cluster_summary.csv").exists():
                archive.write(folder / "cluster_summary.csv", "data/cluster_summary.csv")
            if (folder / "cluster_analysis.csv").exists():
                archive.write(folder / "cluster_analysis.csv", "data/cluster_analysis.csv")
            if (folder / "clusters_overlay.mp4").exists():
                archive.write(folder / "clusters_overlay.mp4", "data/clusters_overlay.mp4")
        if "settings" in wanted and (folder / "settings.json").exists():
            archive.write(folder / "settings.json", "settings.json")
        if "audio" in wanted and (folder / "sounds").exists():
            for wav in (folder / "sounds").glob("*.wav"):
                archive.write(wav, f"sounds/{wav.name}")
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
                "message": item.get("message") or meta.get("message") or "",
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


app.mount("/", StaticFiles(directory=FRONTEND, html=True), name="frontend")
