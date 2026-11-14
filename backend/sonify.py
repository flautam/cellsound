"""Write the wav files the Play buttons and the download zip share.

Pitch follows direction (higher when cells move toward the Point of Audio).
Volume follows closeness. A beat follows speed. Left and right follow
which side of the Point of Audio the cells are on.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import soundfile as sf

RATE = 22050

HARMONICS = {
    "overview": ((1.0, 1.0), (2.0, 0.22)),
    "piano": ((1.0, 1.0), (2.0, 0.45), (3.0, 0.16)),
    "marimba": ((1.0, 1.0), (4.0, 0.32)),
    "cello": ((1.0, 1.0), (2.0, 0.5), (3.0, 0.2)),
    "flute": ((1.0, 1.0), (2.0, 0.08)),
    "bell": ((1.0, 0.55), (2.4, 0.4), (3.8, 0.22)),
    "soft synth": ((1.0, 1.0), (1.5, 0.28)),
}


def _columns(motion: pd.DataFrame, cell_ids):
    if motion is None or motion.empty:
        return np.zeros(1), np.zeros(1), np.ones(1), np.zeros(1)
    table = motion
    if cell_ids is not None:
        table = motion[motion["cell_id"].isin(list(cell_ids))]
    if table.empty:
        return np.zeros(1), np.zeros(1), np.ones(1), np.zeros(1)
    radial, speed, dist, xs = [], [], [], []
    for _frame, group in table.groupby("frame"):
        steps = group.dropna(subset=["radial_v"])
        use = steps if not steps.empty else group
        radial.append(float(use["radial_v"].mean()))
        speed.append(float(use["speed"].mean()) if "speed" in use else 0.0)
        dist.append(float(use["dist_poa"].mean()) if "dist_poa" in use else 0.0)
        xs.append(float(group["x"].mean()))
    return np.array(radial), np.array(speed), np.array(dist), np.array(xs)


def _stretch(values, count):
    if len(values) == 1:
        return np.full(count, float(values[0]))
    source = np.linspace(0, 1, len(values))
    return np.interp(np.linspace(0, 1, count), source, values)


def _render(freq, amp, pulse_hz, pan, harmonics):
    count = len(freq)
    carrier = np.cumsum(freq) / RATE
    tone = np.zeros(count)
    for multiple, weight in harmonics:
        tone += weight * np.sin(2 * np.pi * carrier * multiple)
    peak = float(np.max(np.abs(tone)) or 1)
    pulse = 0.3 + 0.7 * np.power(np.maximum(0, np.sin(2 * np.pi * np.cumsum(pulse_hz) / RATE)), 6)
    mono = (amp * pulse * tone / peak).astype(np.float32)
    left = np.sqrt(np.clip((1 - pan) / 2, 0, 1))
    right = np.sqrt(np.clip((1 + pan) / 2, 0, 1))
    return np.column_stack([mono * left, mono * right]).astype(np.float32)


def _from_motion(radial, speed, dist, xs, poa_x, span, harmonics, seconds):
    count = max(1, int(RATE * seconds))
    freq = np.clip(392.0 * np.exp(np.clip(-_stretch(radial, count), -3, 3) * 0.28), 160, 900)
    closeness = 1.0 - np.clip(_stretch(dist, count) / max(span, 1.0), 0, 1)
    amp = 0.05 + 0.22 * closeness
    pulse = np.clip(0.8 + np.clip(_stretch(speed, count), 0, 8) * 0.9, 0.6, 7)
    pan = np.clip((_stretch(xs, count) - poa_x) / max(span, 1.0), -1, 1)
    return _render(freq, amp, pulse, pan, harmonics)


def _curve(freq, amp, pulse, pan, harmonics):
    return _render(
        np.asarray(freq, dtype=float),
        np.asarray(amp, dtype=float),
        np.asarray(pulse, dtype=float),
        np.asarray(pan, dtype=float),
        harmonics,
    )


def _examples():
    count = int(RATE * 3)
    line = np.linspace(0, 1, count)
    up_down = np.sin(np.pi * line)
    pitch = _curve(280 + 380 * up_down, np.full(count, 0.2), np.full(count, 1.5), np.zeros(count), HARMONICS["flute"])
    volume = _curve(np.full(count, 440.0), 0.04 + 0.24 * line, np.full(count, 1.5), np.zeros(count), HARMONICS["piano"])
    beat = _curve(np.full(count, 240.0), np.full(count, 0.2), 1 + 6 * line, np.zeros(count), HARMONICS["marimba"])
    together = _curve(
        300 + 280 * line,
        0.05 + 0.2 * line,
        1.2 + 4 * line,
        np.linspace(-0.6, 0.6, count),
        HARMONICS["piano"],
    )
    return {
        "example_pitch.wav": pitch,
        "example_volume.wav": volume,
        "example_beat.wav": beat,
        "example_all.wav": together,
    }


def write_sounds(folder: Path, motion: pd.DataFrame, summary: dict, poa_x: float, poa_y: float, width: int, height: int) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    span = max(float(width), float(height), 1.0) / 2
    radial, speed, dist, xs = _columns(motion, None)
    seconds = max(3.0, len(radial) * 0.5)
    overview = _from_motion(radial, speed, dist, xs, poa_x, span, HARMONICS["overview"], seconds)
    sf.write(folder / "overview.wav", overview, RATE)
    for cluster in summary.get("clusters", []):
        voice = HARMONICS.get(str(cluster.get("instrument", "piano")).lower(), HARMONICS["piano"])
        cr, cs, cd, cx = _columns(motion, cluster.get("cell_ids"))
        wave = _from_motion(cr, cs, cd, cx, poa_x, span, voice, max(3.0, len(cr) * 0.5))
        sf.write(folder / cluster["audio"], wave, RATE)
    for name, wave in _examples().items():
        sf.write(folder / name, wave, RATE)
