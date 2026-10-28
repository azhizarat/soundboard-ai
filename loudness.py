"""Loudness measurement and gain normalization based on ITU-R BS.1770-4 / EBU R128."""

from __future__ import annotations

import math
import numpy as np

TARGET_LUFS = -16.0
MAX_GAIN_DB = 12.0
MIN_GAIN_DB = -12.0
TRUE_PEAK_CEILING_DB = -1.0
BLOCK_DURATION_S = 0.400  # 400 ms
OVERLAP_PERCENT = 0.75  # 75% overlap -> 100 ms hop

# ITU-R BS.1770-4 Stage 1: Pre-filter (high-shelf filter simulating head acoustics)
PRE_B = [1.53512485958697, -2.69169618940638, 1.19839281085285]
PRE_A = [1.0, -1.69065929318241, 0.69065929318241]

# ITU-R BS.1770-4 Stage 2: RLB weighting (high-pass filter)
RLB_B = [1.0, -2.0, 1.0]
RLB_A = [1.0, -1.99004745483398, 0.99007225035621]


def _apply_biquad(b: list[float], a: list[float], x: np.ndarray) -> np.ndarray:
    """Direct Form II Transposed biquad filter implementation in NumPy."""
    y = np.zeros_like(x, dtype=np.float32)
    b0, b1, b2 = b
    a1, a2 = a[1], a[2]
    d1 = 0.0
    d2 = 0.0
    for i in range(len(x)):
        xi = float(x[i])
        yi = b0 * xi + d1
        d1 = b1 * xi - a1 * yi + d2
        d2 = b2 * xi - a2 * yi
        y[i] = yi
    return y


def apply_k_weighting(samples_mono_48k: np.ndarray) -> np.ndarray:
    """Applies the two-stage K-weighting filter to 48 kHz mono samples."""
    stage1 = _apply_biquad(PRE_B, PRE_A, samples_mono_48k)
    stage2 = _apply_biquad(RLB_B, RLB_A, stage1)
    return stage2


def _to_mono(samples: np.ndarray) -> np.ndarray:
    """Ensures 1D float32 mono audio."""
    if samples.ndim == 2:
        return np.mean(samples, axis=-1, dtype=np.float32)
    return samples.astype(np.float32)


def integrated_lufs(samples: np.ndarray, sr: int = 48000) -> tuple[float | None, bool]:
    """Calculates integrated loudness in LUFS according to ITU-R BS.1770-4.

    Returns (lufs_value, is_estimated).
    If audio is silence or below -70 LUFS, returns (None, False).
    For clips under 400 ms, returns (rms_lufs, True).
    """
    if samples is None or len(samples) == 0 or sr <= 1000:
        return None, False

    mono = _to_mono(samples)
    if sr != 48000 and len(mono) > 0:
        new_len = max(1, int(round(len(mono) * 48000 / sr)))
        mono = np.interp(
            np.linspace(0.0, 1.0, new_len),
            np.linspace(0.0, 1.0, len(mono)),
            mono,
        ).astype(np.float32)

    block_size = int(BLOCK_DURATION_S * 48000)  # 19200
    hop = int(block_size * (1.0 - OVERLAP_PERCENT))  # 4800

    filtered = apply_k_weighting(mono)

    if len(filtered) < block_size:
        msq = float(np.mean(filtered**2))
        if msq < 1e-7:
            return None, True
        lufs = -0.691 + 10.0 * math.log10(msq)
        return round(lufs, 1), True

    z_blocks = []
    for start in range(0, len(filtered) - block_size + 1, hop):
        block = filtered[start : start + block_size]
        z_blocks.append(float(np.mean(block**2)))

    if not z_blocks:
        return None, False

    z_arr = np.array(z_blocks, dtype=np.float64)
    safe_z = np.maximum(z_arr, 1e-12)
    l_blocks = -0.691 + 10.0 * np.log10(safe_z)

    pass_abs = l_blocks > -70.0
    if not np.any(pass_abs):
        return None, False

    z_abs = z_arr[pass_abs]
    z_avg = float(np.mean(z_abs))
    gamma_r = -0.691 + 10.0 * math.log10(max(1e-12, z_avg)) - 10.0

    pass_rel = (-0.691 + 10.0 * np.log10(np.maximum(z_abs, 1e-12))) > gamma_r
    if not np.any(pass_rel):
        return None, False

    z_final = z_abs[pass_rel]
    integrated = -0.691 + 10.0 * math.log10(float(np.mean(z_final)))
    return round(integrated, 1), False


def calculate_loudness_gain(
    lufs: float | None,
    peak_abs: float = 1.0,
    target_lufs: float = TARGET_LUFS,
) -> float:
    """Calculates compensation gain in dB clamped to [-12, +12] dB and -1.0 dBFS ceiling."""
    if lufs is None:
        return 0.0

    raw_gain_db = target_lufs - lufs
    gain_db = max(MIN_GAIN_DB, min(MAX_GAIN_DB, raw_gain_db))

    if peak_abs > 0.0:
        peak_db = 20.0 * math.log10(peak_abs)
        headroom = TRUE_PEAK_CEILING_DB - peak_db
        if gain_db > headroom:
            # Floor to two decimal places so rounding never exceeds the ceiling
            safe_headroom = math.floor(headroom * 100.0) / 100.0
            return max(MIN_GAIN_DB, safe_headroom)

    return round(gain_db, 2)


def analyze_audio_loudness(samples: np.ndarray, sr: int = 48000) -> dict:
    """High-level analyzer returning lufs, gain_db and estimation flag."""
    if samples is None or len(samples) == 0:
        return {"lufs": None, "loudness_gain_db": 0.0, "lufs_estimated": False}

    mono = _to_mono(samples)
    peak_abs = float(np.max(np.abs(mono))) if len(mono) > 0 else 0.0
    lufs, is_est = integrated_lufs(mono, sr=sr)
    gain_db = calculate_loudness_gain(lufs, peak_abs=peak_abs)
    return {
        "lufs": lufs,
        "loudness_gain_db": gain_db,
        "lufs_estimated": is_est,
    }
