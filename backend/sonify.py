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

# Octave, partials, and whether the note is a short strike or a held tone.
# These stay far enough apart that a cluster can be picked out of the mix.
VOICES = {
    "piano": {"octave": 1.0, "strike": True, "noise": 0.0, "partials": ((1.0, 1.0), (2.0, 0.62), (3.0, 0.34), (4.0, 0.18))},
    "marimba": {"octave": 0.85, "strike": True, "noise": 0.0, "partials": ((1.0, 1.0), (4.0, 0.22))},
    "cello": {"octave": 0.4, "strike": False, "noise": 0.0, "partials": ((1.0, 1.0), (2.0, 0.32), (3.0, 0.12))},
    "flute": {"octave": 2.0, "strike": False, "noise": 0.12, "partials": ((1.0, 1.0), (2.0, 0.04))},
    "bell": {"octave": 1.7, "strike": True, "noise": 0.0, "partials": ((1.0, 0.55), (2.4, 0.7), (5.4, 0.32), (8.1, 0.14))},
    "soft synth": {"octave": 1.0, "strike": False, "noise": 0.0, "partials": ((0.5, 0.55), (1.0, 1.0), (1.01, 0.75), (2.0, 0.12))},
}


def _voice(name):
    return VOICES.get(str(name or "piano").lower(), VOICES["piano"])


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
        radial.append(_mean(use["radial_v"]))
        speed.append(_mean(use["speed"]) if "speed" in use else 0.0)
        dist.append(_mean(use["dist_poa"]) if "dist_poa" in use else 0.0)
        xs.append(_mean(group["x"]))
    return np.array(radial), np.array(speed), np.array(dist), np.array(xs)


def _mean(series):
    numbers = pd.to_numeric(series, errors="coerce").dropna()
    return float(numbers.mean()) if not numbers.empty else 0.0


def _stretch(values, count):
    clean = np.nan_to_num(np.asarray(values, dtype=float), nan=0.0)
    if len(clean) == 1:
        return np.full(count, float(clean[0]))
    source = np.linspace(0, 1, len(clean))
    return np.interp(np.linspace(0, 1, count), source, clean)


def _level(wave, peak=0.85):
    loudest = float(np.max(np.abs(wave)) or 1.0)
    return (wave / loudest * peak).astype(np.float32)


def _render(freq, amp, pulse_hz, pan, voice):
    count = len(freq)
    carrier = np.cumsum(freq * voice["octave"]) / RATE
    tone = np.zeros(count)
    for multiple, weight in voice["partials"]:
        tone += weight * np.sin(2 * np.pi * carrier * multiple)
    if voice["noise"]:
        tone += voice["noise"] * np.random.default_rng(count).standard_normal(count)
    peak = float(np.max(np.abs(tone)) or 1)
    beat = np.maximum(0, np.sin(2 * np.pi * np.cumsum(pulse_hz) / RATE))
    if voice["strike"]:
        pulse = np.power(beat, 16)
    else:
        pulse = 0.72 + 0.28 * beat
    mono = amp * pulse * tone / peak
    left = np.sqrt(np.clip((1 - pan) / 2, 0, 1))
    right = np.sqrt(np.clip((1 + pan) / 2, 0, 1))
    return _level(np.column_stack([mono * left, mono * right]))


def _from_motion(radial, speed, dist, xs, poa_x, span, voice, seconds, pan_bias=0.0):
    count = max(1, int(RATE * seconds))
    base = np.clip(392.0 * np.exp(np.clip(-_stretch(radial, count), -3, 3) * 0.28), 160, 900)
    closeness = 1.0 - np.clip(_stretch(dist, count) / max(span, 1.0), 0, 1)
    amp = 0.05 + 0.22 * closeness
    pulse = np.clip(0.8 + np.clip(_stretch(speed, count), 0, 8) * 0.9, 0.6, 7)
    pan = np.clip((_stretch(xs, count) - poa_x) / max(span, 1.0) + pan_bias, -1, 1)
    return _render(base, amp, pulse, pan, voice)


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
    pitch = _curve(280 + 380 * up_down, np.full(count, 0.2), np.full(count, 1.5), np.zeros(count), _voice("flute"))
    volume = _curve(np.full(count, 440.0), 0.04 + 0.24 * line, np.full(count, 1.5), np.zeros(count), _voice("piano"))
    beat = _curve(np.full(count, 240.0), np.full(count, 0.2), 1 + 6 * line, np.zeros(count), _voice("marimba"))
    together = _curve(
        300 + 280 * line,
        0.05 + 0.2 * line,
        1.2 + 4 * line,
        np.linspace(-0.6, 0.6, count),
        _voice("piano"),
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
    radial, _speed, _dist, _xs = _columns(motion, None)
    seconds = max(3.0, len(radial) * 0.5)
    layers = []
    places = (-0.4, 0.4, 0.0, -0.2, 0.2)
    for index, cluster in enumerate(summary.get("clusters", [])):
        voice = _voice(cluster.get("instrument"))
        cr, cs, cd, cx = _columns(motion, cluster.get("cell_ids"))
        wave = _from_motion(cr, cs, cd, cx, poa_x, span, voice, seconds)
        sf.write(folder / cluster["audio"], wave, RATE)
        layer = _from_motion(cr, cs, cd, cx, poa_x, span, voice, seconds, places[index % len(places)])
        loudness = float(np.sqrt(np.mean(layer ** 2)) or 1.0)
        layers.append(layer * (0.12 / loudness))
    if layers:
        overview = _level(np.sum(layers, axis=0))
    else:
        overview = _from_motion(radial, _speed, _dist, _xs, poa_x, span, _voice("piano"), seconds)
    sf.write(folder / "overview.wav", overview, RATE)
    for name, wave in _examples().items():
        sf.write(folder / name, wave, RATE)
