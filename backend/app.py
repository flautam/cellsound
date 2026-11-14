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
from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from backend.results import build_analysis
from backend.sonify import write_sounds
from backend.track import choose_device, limit_for_cpu, link_tracks, segment_nuclei, select_frames
from backend.video import load_video

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
JOBS = ROOT / "output" / "jobs"
MAX_BYTES = 100 * 1024 * 1024
_lock = threading.Lock()

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


def _track(job_id: str, video_path: Path) -> None:
    try:
        device = choose_device()
        frame_limit, crop, note = limit_for_cpu(device.type, None, None)
        _write_meta(job_id, status="running", stage="received", message=note or "Video received")
        video = select_frames(load_video(video_path, max_frames=frame_limit), None, crop)
        total, height, width = (int(video.shape[0]), int(video.shape[1]), int(video.shape[2]))
        _write_meta(
            job_id,
            stage="tracking",
            frame=0,
            total_frames=total,
            width=width,
            height=height,
            message="Detecting and tracking nuclei",
        )

        def on_frame(done, count):
            _write_meta(job_id, frame=done, total_frames=count, stage="tracking")

        masks = segment_nuclei(video, device, on_frame=on_frame)
        table = link_tracks(video, masks)
        folder = _job_dir(job_id)
        table.to_csv(folder / "tracks.csv", index=False)
        iio.imwrite(folder / "frame0.png", np.asarray(video[0]))
        _write_meta(
            job_id,
            status="done",
            stage="done",
            frame=total,
            total_frames=total,
            message="Tracking finished",
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
    if not name.endswith(".mp4") and video.content_type not in {"video/mp4", "application/mp4"}:
        return JSONResponse(status_code=400, content={"error": "not_mp4", "message": "That file is not an MP4 video."})
    payload = await video.read()
    if len(payload) > MAX_BYTES:
        return JSONResponse(status_code=413, content={"error": "too_large", "message": "The video is larger than 100 MB."})
    job_id = uuid4().hex[:6]
    folder = _job_dir(job_id)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "video.mp4").write_bytes(payload)
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
    threading.Thread(target=_track, args=(job_id, folder / "video.mp4"), daemon=True).start()
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
    return FileResponse(path, media_type="image/png")


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
    try:
        summary, traces, motion = build_analysis(
            tracks,
            body,
            int(meta.get("width") or 1),
            int(meta.get("height") or 1),
            meta.get("pixel_size_um"),
            meta.get("minutes_per_frame"),
        )
    except Exception as exc:
        _write_meta(job_id, status="error", message=str(exc) or "The analysis could not finish.")
        return JSONResponse(status_code=500, content={"error": "analysis_failed", "message": str(exc)})
    folder = _job_dir(job_id)
    (folder / "summary.json").write_text(json.dumps(summary))
    (folder / "traces.json").write_text(json.dumps(traces))
    (folder / "settings.json").write_text(json.dumps(body))
    if not motion.empty:
        motion.to_csv(folder / "motion.csv", index=False)
    poa = body.get("poa") or {}
    write_sounds(
        folder / "sounds",
        motion,
        summary,
        float(poa.get("x", 0)),
        float(poa.get("y", 0)),
        int(meta.get("width") or 1),
        int(meta.get("height") or 1),
    )
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
    return json.loads(path.read_text())


@app.get("/api/jobs/{job_id}/audio/{name}.wav")
def audio(job_id: str, name: str):
    path = _job_dir(job_id) / "sounds" / f"{name}.wav"
    if not path.exists():
        return JSONResponse(status_code=404, content={"error": "missing_audio", "message": "That sound is not ready yet."})
    return FileResponse(path, media_type="audio/wav", headers={"Cache-Control": "no-store"})


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
            lines = [summary_json["population"]["description"], ""]
            for cluster in summary_json["clusters"]:
                lines.append(f"Cluster {cluster['cluster_id']}: {cluster['name']}. {cluster['description']}")
            archive.writestr("descriptions/summary.txt", "\n".join(lines) + "\n")
        if "data" in wanted and (folder / "tracks.csv").exists():
            archive.write(folder / "tracks.csv", "data/tracks.csv")
            if (folder / "motion.csv").exists():
                archive.write(folder / "motion.csv", "data/motion.csv")
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


app.mount("/", StaticFiles(directory=FRONTEND, html=True), name="frontend")
