"""Write the wav files the Play buttons and the download zip share.

Each cluster keeps one fixed tone, like a hearing test. Cluster 1 is
220 Hz and cluster 10 is 880 Hz. Pulse rate follows speed: fast clusters
tap quickly, slow clusters tap slowly. Pitch bend follows toward/away
motion so direction is easier to hear. Tones are synthesized here (soft
triangle), not loaded from a sample library.
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

# Slow clusters tap about once a second. Fast ones tap quickly.
# All pulse rates run at 80% so beats feel a bit slower overall.
PULSE_SLOW = 1.15 * 0.8
PULSE_FAST = 5.6 * 0.8
# Speed (px/frame) where pulse rate is most of the way to max.
SPEED_SCALE = 3.0
# Toward/away can shift pitch by this fraction at most (~22%).
BEND = 0.22
# Softens how quickly radial_v saturates into the full bend.
BEND_SCALE = 7.0
# Glide pitch slowly like a Doppler flyby (higher = slower pitch change).
PITCH_SMOOTH_SEC = 0.85
# Louder when near the Point of Audio, quieter when far.
# Far floor stays audible so left/right pan in distant regions is still clear.
VOL_NEAR = 1.0
VOL_FAR = 0.14
# Higher = volume falls off faster with distance from the POA.
VOL_FALLOFF = 2.2
# How hard far regions push into one ear (near POA stays more centered).
PAN_FAR = 1.0
PAN_NEAR = 0.12


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
        return np.zeros(1), np.zeros(1), np.ones(1), np.zeros(1), np.zeros(1)
    table = motion
    if cell_ids is not None:
        table = motion[motion["cell_id"].isin(list(cell_ids))]
    if table.empty:
        return np.zeros(1), np.zeros(1), np.ones(1), np.zeros(1), np.zeros(1)
    radial, speed, dist, xs, ys = [], [], [], [], []
    for _frame, group in table.groupby("frame"):
        steps = group.dropna(subset=["radial_v"])
        use = steps if not steps.empty else group
        radial.append(_mean(use["radial_v"]))
        speed.append(_mean(use["speed"]) if "speed" in use else 0.0)
        dist.append(_mean(use["dist_poa"]) if "dist_poa" in use else 0.0)
        xs.append(_mean(group["x"]))
        ys.append(_mean(group["y"]))
    return np.array(radial), np.array(speed), np.array(dist), np.array(xs), np.array(ys)


def _mean(series):
    numbers = pd.to_numeric(series, errors="coerce").dropna()
    return float(numbers.mean()) if not numbers.empty else 0.0


def _stretch(values, count):
    clean = np.nan_to_num(np.asarray(values, dtype=float), nan=0.0)
    if len(clean) == 1:
        return np.full(count, float(clean[0]))
    source = np.linspace(0, 1, len(clean))
    return np.interp(np.linspace(0, 1, count), source, clean)


def _smooth(values, seconds):
    """Bidirectional blur so pitch changes glide instead of kink."""
    series = np.asarray(values, dtype=float)
    if len(series) < 3:
        return series
    win = max(3, int(round(seconds * RATE)))
    if win % 2 == 0:
        win += 1
    kernel = np.hanning(win)
    kernel /= float(kernel.sum())
    pad = win // 2
    padded = np.pad(series, (pad, pad), mode="edge")
    return np.convolve(padded, kernel, mode="valid")


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


def _from_motion(base_hz, radial, speed, dist, xs, ys, poa_x, poa_y, span, seconds, pan_bias=0.0):
    count = max(1, int(RATE * seconds))
    # Negative radial velocity means toward the Point of Audio → pitch rises.
    # Smooth first so the toward→away flip is a continuous glide (real Doppler
    # is smooth; a raw sign change at the POA sounds like a kink).
    radial_s = _smooth(_stretch(radial, count), PITCH_SMOOTH_SEC)
    nudge = np.tanh(radial_s / BEND_SCALE) * BEND
    freq = _smooth(float(base_hz) * (1.0 - nudge), PITCH_SMOOTH_SEC * 0.5)
    activity = np.tanh(np.maximum(_stretch(speed, count), 0.0) / SPEED_SCALE)
    pulse = PULSE_SLOW + (PULSE_FAST - PULSE_SLOW) * activity
    dist_s = _stretch(dist, count)
    closeness = 1.0 - np.clip(dist_s / max(span, 1.0), 0, 1)
    amp = VOL_FAR + (VOL_NEAR - VOL_FAR) * np.power(closeness, VOL_FALLOFF)
    # Stereo follows horizontal place only: directly below/above the POA is
    # 50/50, bottom-left is left, bottom-right is right. Far left/right get
    # a harder push so distant side regions stay distinct.
    dx = _stretch(xs, count) - poa_x
    # Normalize by half-width; tanh keeps near-center almost centered.
    side = np.tanh(dx / max(0.28 * span, 1.0))
    farness = np.clip(dist_s / max(span, 1.0), 0.0, 1.0)
    strength = PAN_NEAR + (PAN_FAR - PAN_NEAR) * np.power(farness, 0.45)
    pan = np.clip(side * strength + pan_bias, -1.0, 1.0)
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
    # Same tone, pulses speeding up as the cluster moves faster.
    pulse = _curve(
        np.full(count, CLUSTER_HZ[4]),
        np.full(count, 0.8),
        PULSE_SLOW + (PULSE_FAST - PULSE_SLOW) * line,
        np.zeros(count),
    )
    # Two clusters at once: one slow, one fast.
    slow = _curve(np.full(count, CLUSTER_HZ[1]), np.full(count, 0.75), np.full(count, PULSE_SLOW), np.full(count, -0.35))
    fast = _curve(np.full(count, CLUSTER_HZ[6]), np.full(count, 0.75), np.full(count, PULSE_FAST), np.full(count, 0.35))
    together = _level(slow * 0.7 + fast * 0.7)
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
    radial, speed, dist, xs, ys = _columns(motion, None)
    seconds = max(3.0, len(radial) * 0.5)
    layers = []
    for index, cluster in enumerate(summary.get("clusters", [])):
        cluster_id = cluster.get("cluster_id", index + 1)
        base = tone_hz(cluster_id)
        cr, cs, cd, cx, cy = _columns(motion, cluster.get("cell_ids"))
        wave = _from_motion(base, cr, cs, cd, cx, cy, poa_x, poa_y, span, seconds)
        name = cluster.get("audio") or f"cluster_{cluster_id}.wav"
        sf.write(folder / name, wave, RATE)
        loudness = float(np.sqrt(np.mean(wave ** 2)) or 1.0)
        layers.append(wave * (0.16 / loudness))
    if layers:
        overview = _level(np.sum(layers, axis=0))
    else:
        overview = _from_motion(CLUSTER_HZ[0], radial, speed, dist, xs, ys, poa_x, poa_y, span, seconds)
    sf.write(folder / "overview.wav", overview, RATE)
    for name, wave in _examples().items():
        sf.write(folder / name, wave, RATE)
