"""Write the wav files the Play buttons and the download zip share.

Each cluster keeps one fixed tone, like a hearing test. Cluster 1 is
220 Hz and cluster 10 is 880 Hz. Pulse rate follows closeness to the
Point of Audio: closer cells tap faster. A slight pitch bend can hint
at direction, but it stays small enough that the cluster tone is still
obvious. Tones are synthesized here (soft triangle), not loaded from
a sample library.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import soundfile as sf

RATE = 22050

# One comfortable pitch per cluster number. Steps are about a minor third,
# so neighbors stay easy to tell apart and nothing sits in a squeal or a rumble.
CLUSTER_HZ = (
    220.0,
    256.0,
    299.0,
    349.0,
    407.0,
    475.0,
    554.0,
    647.0,
    754.0,
    880.0,
)

# Far cells tap about once a second. Cells on the Point of Audio tap faster.
PULSE_FAR = 1.15
PULSE_NEAR = 5.6
# Direction may nudge the pitch by this fraction at most.
BEND = 0.025


def tone_hz(cluster_id) -> float:
    try:
        index = int(cluster_id) - 1
    except (TypeError, ValueError):
        index = 0
    if index < 0:
        index = 0
    return float(CLUSTER_HZ[index % len(CLUSTER_HZ)])


def tone_name(cluster_id) -> str:
    try:
        number = int(cluster_id)
    except (TypeError, ValueError):
        number = 1
    if number < 1:
        number = 1
    return f"Tone {((number - 1) % len(CLUSTER_HZ)) + 1}"


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


def _level(wave, peak=0.8):
    loudest = float(np.max(np.abs(wave)) or 1.0)
    return (wave / loudest * peak).astype(np.float32)


def _soft_triangle(freq):
    phase = np.cumsum(np.asarray(freq, dtype=float)) / RATE
    # A triangle's extra harmonic is quiet, so the tone stays soft.
    wave = np.sin(2 * np.pi * phase)
    wave += 0.16 * np.sin(6 * np.pi * phase)
    return wave.astype(np.float32)


def _pulse_envelope(pulse_hz):
    pulse = np.clip(np.asarray(pulse_hz, dtype=float), 0.7, 6.5)
    phase = np.cumsum(pulse) / RATE
    local = phase - np.floor(phase)
    into = local / np.maximum(pulse, 0.7)
    attack = np.clip(into / 0.014, 0.0, 1.0)
    body = np.exp(-into / 0.085)
    return (attack * body).astype(np.float32)


def _render(freq, amp, pulse_hz, pan):
    count = len(freq)
    wave = _soft_triangle(freq) * _pulse_envelope(pulse_hz) * np.asarray(amp, dtype=np.float32)
    side = np.clip(np.asarray(pan, dtype=float), -1.0, 1.0)
    left = np.sqrt(np.clip((1 - side) / 2, 0, 1)).astype(np.float32)
    right = np.sqrt(np.clip((1 + side) / 2, 0, 1)).astype(np.float32)
    stereo = np.column_stack((wave * left, wave * right))
    edge = min(int(RATE * 0.012), max(0, count // 4))
    if edge > 1:
        ramp = np.linspace(0, 1, edge, dtype=np.float32)
        stereo[:edge] *= ramp[:, None]
        stereo[-edge:] *= ramp[::-1, None]
    return _level(stereo)


def _from_motion(base_hz, radial, dist, xs, poa_x, span, seconds, pan_bias=0.0):
    count = max(1, int(RATE * seconds))
    # Negative radial velocity means toward the Point of Audio.
    nudge = np.tanh(_stretch(radial, count) / 12.0) * BEND
    freq = float(base_hz) * (1.0 - nudge)
    closeness = 1.0 - np.clip(_stretch(dist, count) / max(span, 1.0), 0, 1)
    pulse = PULSE_FAR + (PULSE_NEAR - PULSE_FAR) * closeness
    amp = 0.72 + 0.28 * closeness
    pan = np.clip(((_stretch(xs, count) - poa_x) / max(span, 1.0)) * 0.7 + pan_bias, -1, 1)
    return _render(freq, amp, pulse, pan)


def _curve(freq, amp, pulse, pan):
    return _render(
        np.asarray(freq, dtype=float),
        np.asarray(amp, dtype=float),
        np.asarray(pulse, dtype=float),
        np.asarray(pan, dtype=float),
    )


def _examples():
    count = int(RATE * 3)
    line = np.linspace(0, 1, count)
    # Three cluster tones in a row, each tapping slowly so the pitch is clear.
    tones = np.piecewise(
        line,
        [line < 1 / 3, (line >= 1 / 3) & (line < 2 / 3), line >= 2 / 3],
        [CLUSTER_HZ[0], CLUSTER_HZ[4], CLUSTER_HZ[7]],
    )
    tone = _curve(tones, np.full(count, 0.8), np.full(count, 1.6), np.zeros(count))
    # Same tone, pulses speeding up as a cell approaches the Point of Audio.
    pulse = _curve(
        np.full(count, CLUSTER_HZ[4]),
        np.full(count, 0.8),
        PULSE_FAR + (PULSE_NEAR - PULSE_FAR) * line,
        np.zeros(count),
    )
    # Two clusters at once: one far and slow, one close and fast.
    far = _curve(np.full(count, CLUSTER_HZ[1]), np.full(count, 0.75), np.full(count, PULSE_FAR), np.full(count, -0.35))
    near = _curve(np.full(count, CLUSTER_HZ[6]), np.full(count, 0.75), np.full(count, PULSE_NEAR), np.full(count, 0.35))
    together = _level(far * 0.7 + near * 0.7)
    return {
        "example_tone.wav": tone,
        "example_pulse.wav": pulse,
        "example_all.wav": together,
        # Older button names, same demonstrations, so a stale page still plays a tone.
        "example_pitch.wav": tone,
        "example_volume.wav": pulse,
        "example_beat.wav": pulse,
    }


def write_sounds(folder: Path, motion: pd.DataFrame, summary: dict, poa_x: float, poa_y: float, width: int, height: int) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    span = max(float(width), float(height), 1.0) / 2
    radial, _speed, dist, xs = _columns(motion, None)
    seconds = max(3.0, len(radial) * 0.5)
    layers = []
    places = (-0.45, 0.45, -0.28, 0.28, -0.12, 0.12, -0.36, 0.36, 0.0, 0.18)
    for index, cluster in enumerate(summary.get("clusters", [])):
        cluster_id = cluster.get("cluster_id", index + 1)
        base = tone_hz(cluster_id)
        cr, _cs, cd, cx = _columns(motion, cluster.get("cell_ids"))
        wave = _from_motion(base, cr, cd, cx, poa_x, span, seconds)
        name = cluster.get("audio") or f"cluster_{cluster_id}.wav"
        sf.write(folder / name, wave, RATE)
        layer = _from_motion(base, cr, cd, cx, poa_x, span, seconds, places[index % len(places)])
        loudness = float(np.sqrt(np.mean(layer ** 2)) or 1.0)
        layers.append(layer * (0.16 / loudness))
    if layers:
        overview = _level(np.sum(layers, axis=0))
    else:
        overview = _from_motion(CLUSTER_HZ[0], radial, dist, xs, poa_x, span, seconds)
    sf.write(folder / "overview.wav", overview, RATE)
    for name, wave in _examples().items():
        sf.write(folder / name, wave, RATE)
