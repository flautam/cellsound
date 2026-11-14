# cellsound API contract (draft v0.1)

The frontend and backend agree on this file. **The backend is still being built**, so the frontend uses mock data shaped exactly like the examples below. When an endpoint is ready, the backend owner ticks it off here and the frontend switches it from mock to real.

All URLs are relative (`/api/...`), so the same code works locally, on Coder, and behind a forwarded port.

| # | Endpoint | Ready? |
|---|---|---|
| 1 | `POST /api/jobs` | ☑ |
| 2 | `GET /api/jobs/{job_id}` | ☑ |
| 3 | `GET /api/jobs/{job_id}/frame0` | ☑ |
| 4 | `POST /api/jobs/{job_id}/analyze` | ☑ |
| 5 | `GET /api/jobs/{job_id}/summary` | ☑ |
| 6 | `GET /api/jobs/{job_id}/traces` | ☑ |
| 7 | `GET /api/jobs/{job_id}/audio/{name}.wav` | ☑ |
| 8 | `GET /api/jobs/{job_id}/export.zip?include=audio,text,data,settings` | ☑ |

Key idea: **tracking runs once per video** (slow, GPU). Everything after it (motion, clusters, descriptions, sound) reruns quickly through `/analyze` whenever the user moves the Point of Audio or changes a setting.

---

## 1. Upload a video: `POST /api/jobs`
`multipart/form-data`: `video` (MP4 or MOV, max 100 MB), `pixel_size_um` (optional number), `minutes_per_frame` (optional number).

```json
{ "job_id": "a1b2c3" }
```
Errors: `400 { "error": "not_mp4", "message": "That file is not an MP4 or MOV video." }`, `413 { "error": "too_large", "message": "The video is larger than 100 MB." }`

## 2. Progress: `GET /api/jobs/{job_id}`
```json
{
  "status": "running",            
  "stage": "tracking",            
  "frame": 80,
  "total_frames": 200,
  "message": "Detecting and tracking nuclei"
}
```
`status`: `queued | running | done | error`. `stage`: `received | tracking | measuring | grouping | sonifying | done`.

## 3. First frame for the Point of Audio screen: `GET /api/jobs/{job_id}/frame0`
```json
{
  "image_url": "/api/jobs/a1b2c3/frame0.png",
  "width": 512,
  "height": 512,
  "cells": [ { "cell_id": 12, "x": 92.4, "y": 113.0 }, { "cell_id": 27, "x": 410.1, "y": 60.8 } ]
}
```
The frontend counts "cells within 50 pixels" itself from `cells`.

## 4. Run or rerun the analysis: `POST /api/jobs/{job_id}/analyze`
```json
{
  "poa": { "x": 256, "y": 256 },
  "pitch_mode": "radial",
  "n_clusters": 5,
  "position_weight": 0.5,
  "volume_levels": { "closest": 100, "middle": 50, "farthest": 25 },
  "reverse_volume": false,
  "instruments": { "1": "piano", "2": "marimba", "3": "cello", "4": "flute", "5": "bell" }
}
```
`pitch_mode`: `radial | angular | speed`. `position_weight`: 0 = velocity only, 1 = position only.
Response: `{ "status": "running" }`, then poll endpoint 2 until `status` is `done`.

## 5. Results: `GET /api/jobs/{job_id}/summary`
```json
{
  "units": { "speed": "µm/min", "angular": "rad/min" },
  "population": {
    "n_cells": 142, "duration_min": 1000,
    "mean_speed": 1.2, "mean_radial_v": -0.6, "mean_angular_v": 0.08,
    "pct_main_pattern": 75, "n_clusters": 5,
    "description": "Most cells drift slowly toward the Point of Audio, averaging 1.2 µm per minute..."
  },
  "reliability": { "level": "good", "note": "9 cells were lost for a few frames where nuclei overlapped, mostly near the top edge." },
  "clusters": [
    {
      "cluster_id": 1, "rank": 1, "name": "Steady inward drifters", "n_cells": 48,
      "unusual": false, "instrument": "piano", "color": "#6E9BFF",
      "mean_speed": 1.3, "mean_radial_v": -0.8, "mean_angular_v": 0.15,
      "description": "The most typical group. These cells move steadily toward the Point of Audio...",
      "audio": "cluster_1.wav", "cell_ids": [12, 27, 31]
    }
  ],
  "cells": [
    {
      "cell_id": 12, "cluster_id": 1, "mean_speed": 1.4, "distance_travelled": 1380,
      "start_dist_poa": 210, "end_dist_poa": 60,
      "confidence": { "level": "high", "frames_tracked": 200, "frames_missing": 0 },
      "description": "Cell 12 starts in the top left and moves steadily toward the Point of Audio...",
      "events": [ { "minute": 0, "text": "Starts 210 µm from the Point of Audio, moving slowly." } ],
      "path": [[40, 30], [60, 48], [110, 120]],
      "audio": "cell_12.wav"
    }
  ]
}
```
If no calibration was given, `units` is `{ "speed": "px/frame", "angular": "rad/frame" }`.

## 6. Cluster traces: `GET /api/jobs/{job_id}/traces`
Paths are sent as **data, not an image**, so the frontend can draw an accessible SVG with a written description and a "Show cluster" filter.
```json
{
  "width": 512, "height": 512, "poa": { "x": 256, "y": 256 },
  "clusters": [ { "cluster_id": 1, "color": "#6E9BFF", "paths": [ [[40, 80], [120, 110], [262, 196]] ] } ],
  "description": "Traces of 142 cells over 16 hours, colored by cluster..."
}
```
Downsample to at most ~50 points per path.

## 7. Audio: `GET /api/jobs/{job_id}/audio/{name}.wav`
Names: `overview`, `cluster_1` … `cluster_N`, `cell_{id}`, and examples `example_pitch`, `example_volume`, `example_beat`, `example_all`.

## 8. Download: `GET /api/jobs/{job_id}/export.zip?include=audio,text,data,settings`
Returns `cellsound-results.zip`. `settings.json` inside holds the exact body sent to `/analyze`, so a collaborator can rerun it.
