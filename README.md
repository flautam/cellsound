# cellsound

## Test with your own MP4

The video is a command-line path, not an upload page. From the repo root, replace the path below with the full path to your MP4:

```bash
source .venv/bin/activate
python backend/video.py /path/to/their_video.mp4
python backend/track.py /path/to/their_video.mp4 --frames 25 --crop 256
```

Tracking writes `output/tracks.csv` (`cell_id, frame, x, y`). `data/` and `output/` are gitignored. A folder of TIFF frames is an alternative input for `load_video`.

## Data formats
tracks.csv:          cell_id, frame, x, y
motion.csv:          cell_id, frame, x, y, vx, vy, speed, direction_deg, dist_poa, radial_v
clusters.csv:        frame, cell_id, cluster_id
cluster_summary.csv: frame, cluster_id, n_cells, mean_x, mean_y, mean_vx, mean_vy, mean_speed, mean_radial_v
summary.json:        global stats, cluster ranking, text descriptions
Sounds:              output/overview.wav, output/cluster_1.wav, ...

Conventions: POA defaults to image center (`--poa X Y` to override).
radial_v negative = toward POA. No angular velocity.
Units: pixels/frame unless pixel size + frame time are given.
bar15 scale: ~1.30 µm/px (100 µm ≈ 77 px).
