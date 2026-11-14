"""Write the wav files the Play buttons and the download zip share.

Pitch follows direction (higher when cells move toward the Point of Audio).
Volume follows closeness. A beat follows speed. Left and right follow
which side of the Point of Audio the cells are on.

Each instrument is a real FluidR3_GM note (Frank Wen, CC BY 3.0),
pitch-shifted to the motion. See backend/samples/LICENSE.txt.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import soundfile as sf

RATE = 22050
SAMPLES = Path(__file__).resolve().parent / "samples"

# Octave shifts keep the same motion in a register that suits the instrument.
# strike=True keeps the hammer/pluck; False lets the note ring.
VOICES = {
    "piano": {"sample": "piano", "octave": 1.0, "strike": True},
    "marimba": {"sample": "marimba", "octave": 1.0, "strike": True},
    "cello": {"sample": "cello", "octave": 0.5, "strike": False},
    "flute": {"sample": "flute", "octave": 1.7, "strike": False},
    "bell": {"sample": "bell", "octave": 1.45, "strike": True},
    "soft synth": {"sample": "soft_synth", "octave": 1.0, "strike": False},
    "guitar": {"sample": "guitar", "octave": 0.85, "strike": True},
    "trumpet": {"sample": "trumpet", "octave": 1.15, "strike": False},
    "violin": {"sample": "violin", "octave": 1.55, "strike": False},
    "choir": {"sample": "choir", "octave": 0.9, "strike": False},
}

# Recorded pitch of each one-shot, so we pick the closest note before shifting.
NOTES = (
    ("C3", 130.8128),
    ("C4", 261.6256),
    ("C5", 523.2511),
    ("C6", 1046.5023),
)

_LOADED = {}


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


def _load_note(folder, note):
    key = (folder, note)
    if key not in _LOADED:
        path = SAMPLES / folder / f"{note}.wav"
        data, rate = sf.read(path, always_2d=False)
        if np.asarray(data).ndim > 1:
            data = np.asarray(data).mean(axis=1)
        data = np.asarray(data, dtype=np.float32)
        if int(rate) != RATE:
            length = max(1, int(len(data) * RATE / float(rate)))
            data = np.interp(np.linspace(0, len(data) - 1, length), np.arange(len(data)), data).astype(np.float32)
        _LOADED[key] = data
    return _LOADED[key]


def _closest_note(target_hz):
    target = max(float(target_hz), 1.0)
    best = NOTES[1]
    best_gap = 1e9
    for note in NOTES:
        gap = abs(np.log(target / note[1]))
        if gap < best_gap:
            best = note
            best_gap = gap
    return best


def _shift(sample, source_hz, target_hz):
    ratio = float(target_hz) / float(source_hz)
    if ratio <= 0:
        return sample
    length = max(1, int(len(sample) / ratio))
    positions = np.linspace(0, len(sample) - 1, length)
    return np.interp(positions, np.arange(len(sample)), sample).astype(np.float32)


def _fade_edges(sample):
    faded = np.array(sample, dtype=np.float32, copy=True)
    edge = min(int(RATE * 0.008), max(0, len(faded) // 4))
    if edge > 1:
        ramp = np.linspace(0, 1, edge, dtype=np.float32)
        faded[:edge] *= ramp
        faded[-edge:] *= ramp[::-1]
    return faded


def _place_note(sample, target_hz, strike):
    note, source_hz = _closest_note(target_hz)
    shifted = _shift(sample, source_hz, target_hz)
    cap = int(RATE * (0.7 if strike else 1.6))
    return _fade_edges(shifted[:cap])


def _render(freq, amp, pulse_hz, pan, voice):
    count = len(freq)
    stereo = np.zeros((count, 2), dtype=np.float32)
    played = np.clip(np.asarray(freq, dtype=float) * float(voice["octave"]), 70.0, 1600.0)
    phase = np.cumsum(np.maximum(pulse_hz, 0.05)) / RATE
    hits = np.flatnonzero(np.diff(np.floor(phase), prepend=-1) > 0)
    folder = voice["sample"]
    cache = {}
    for hit in hits:
        target = float(played[hit])
        note, _source = _closest_note(target)
        if note not in cache:
            cache[note] = _load_note(folder, note)
        note_wave = _place_note(cache[note], target, voice["strike"])
        end = min(count, hit + len(note_wave))
        length = end - hit
        if length <= 0:
            continue
        level = float(amp[hit])
        side = float(pan[hit])
        left = np.sqrt(np.clip((1 - side) / 2, 0, 1))
        right = np.sqrt(np.clip((1 + side) / 2, 0, 1))
        stereo[hit:end, 0] += note_wave[:length] * level * left
        stereo[hit:end, 1] += note_wave[:length] * level * right
    return _level(stereo)


def _from_motion(radial, speed, dist, xs, poa_x, span, voice, seconds, pan_bias=0.0):
    count = max(1, int(RATE * seconds))
    base = np.clip(392.0 * np.exp(np.clip(-_stretch(radial, count), -3, 3) * 0.28), 160, 900)
    closeness = 1.0 - np.clip(_stretch(dist, count) / max(span, 1.0), 0, 1)
    amp = 0.28 + 0.72 * closeness
    pulse = np.clip(0.8 + np.clip(_stretch(speed, count), 0, 8) * 0.9, 0.6, 7)
    pan = np.clip((_stretch(xs, count) - poa_x) / max(span, 1.0) + pan_bias, -1, 1)
    return _render(base, amp, pulse, pan, voice)


def _curve(freq, amp, pulse, pan, voice):
    return _render(
        np.asarray(freq, dtype=float),
        np.asarray(amp, dtype=float),
        np.asarray(pulse, dtype=float),
        np.asarray(pan, dtype=float),
        voice,
    )


def _examples():
    count = int(RATE * 3)
    line = np.linspace(0, 1, count)
    up_down = np.sin(np.pi * line)
    pitch = _curve(280 + 380 * up_down, np.full(count, 0.55), np.full(count, 1.5), np.zeros(count), _voice("flute"))
    volume = _curve(np.full(count, 440.0), 0.2 + 0.8 * line, np.full(count, 1.5), np.zeros(count), _voice("piano"))
    beat = _curve(np.full(count, 240.0), np.full(count, 0.6), 1 + 6 * line, np.zeros(count), _voice("marimba"))
    together = _curve(
        300 + 280 * line,
        0.25 + 0.7 * line,
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
    places = (-0.5, 0.5, -0.32, 0.32, -0.16, 0.16, -0.42, 0.42, 0.0, 0.22)
    for index, cluster in enumerate(summary.get("clusters", [])):
        voice = _voice(cluster.get("instrument"))
        cr, cs, cd, cx = _columns(motion, cluster.get("cell_ids"))
        wave = _from_motion(cr, cs, cd, cx, poa_x, span, voice, seconds)
        name = cluster.get("audio") or f"cluster_{cluster.get('cluster_id', index + 1)}.wav"
        sf.write(folder / name, wave, RATE)
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
