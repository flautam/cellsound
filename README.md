# cellsound

## Test with your own MP4

The video is a command-line path, not an upload page. From the repo root, replace the path below with the full path to your MP4:

```bash
source .venv/bin/activate
python backend/video.py /path/to/their_video.mp4
python backend/track.py /path/to/their_video.mp4 --frames 25 --crop 256
```

Tracking writes `output/tracks.csv` (`cell_id, frame, x, y`). `data/` and `output/` are gitignored. A folder of TIFF frames is an alternative input for `load_video`.

## Website flow
Upload MP4 (+ optional `pixel_size_um`, `minutes_per_frame`) → track → set Point of Audio
→ analyze → overview page (text summary + cluster video + trails) → chunks page
(per-chunk text) → export.

Analyze re-clusters each time. Overlay video uses the source video FPS.
Calibration is optional; missing scale/time keeps speeds in px/frame.

## Data formats
tracks.csv:          cell_id, frame, x, y
motion.csv:          cell_id, frame, x, y, vx, vy, speed, direction_deg, dist_poa, radial_v
clusters.csv:        frame, cell_id, cluster_id
cluster_summary.csv: frame, cluster_id, n_cells, mean_x, mean_y, mean_vx, mean_vy, mean_speed, mean_radial_v
cluster_analysis.csv: ranked cluster metrics, trend match scores
cluster_timeseries.csv: per-frame cluster velocities
summary.json:        overview (video summary from chunks) + ranked chunk descriptions
clusters_overlay.mp4: chunk circles on tracked frames
Sounds:              overview.wav, cluster_1.wav, ...
Instrument notes:    backend/samples/ (FluidR3_GM by Frank Wen, CC BY 3.0; see backend/samples/LICENSE.txt)

Conventions: POA defaults to image center (`--poa X Y` to override).
radial_v negative = toward POA. No angular velocity.
Cluster rank = strongest motion along the population velocity trend.
