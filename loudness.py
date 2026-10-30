"""LUFS mono/estéreo conforme BS.1770; limite de pico por amostra, sem true-peak."""

from __future__ import annotations

import math
import numpy as np
from scipy.signal import lfilter
from audio_processing import resample_audio

TARGET_LUFS = -16.0
MAX_GAIN_DB = 12.0
MIN_GAIN_DB = -12.0
SAMPLE_PEAK_CEILING_DB = -1.0
BLOCK_DURATION_S = 0.400  # 400 ms
OVERLAP_PERCENT = 0.75  # 75% overlap -> 100 ms hop

# ITU-R BS.1770-5, tabela 1: coeficientes a 48 kHz.
PRE_B = [1.53512485958697, -2.69169618940638, 1.19839281085285]
PRE_A = [1.0, -1.69065929318241, 0.73248077421585]

# ITU-R BS.1770-5, tabela 2: passa-altas RLB a 48 kHz.
RLB_B = [1.0, -2.0, 1.0]
RLB_A = [1.0, -1.99004745483398, 0.99007225036621]


def _apply_biquad(b: list[float], a: list[float], x: np.ndarray) -> np.ndarray:
    """Filtro compilado no eixo temporal; estados independentes por canal."""
    return lfilter(b, a, x, axis=0)


def apply_k_weighting(samples_48k: np.ndarray) -> np.ndarray:
    """Aplica os dois filtros K a 48 kHz sem misturar os canais."""
    stage1 = _apply_biquad(PRE_B, PRE_A, samples_48k)
    stage2 = _apply_biquad(RLB_B, RLB_A, stage1)
    return stage2


def _channels(samples: np.ndarray) -> np.ndarray:
    """Aceita mono e estéreo L/R. Outros layouts exigem pesos explícitos."""
    data = np.asarray(samples, dtype=np.float32)
    if data.ndim == 1:
        data = data[:, None]
    if data.ndim != 2 or data.shape[1] not in (1, 2):
        raise ValueError('A medição suporta somente áudio mono ou estéreo L/R')
    if not np.isfinite(data).all():
        raise ValueError('Áudio contém amostras inválidas')
    return data


def integrated_lufs(samples: np.ndarray, sr: int = 48000) -> tuple[float | None, bool]:
    """Calcula LUFS integrado mono/estéreo conforme ITU-R BS.1770-5.

    Returns (lufs_value, is_estimated).
    If audio is silence or below -70 LUFS, returns (None, False).
    For clips under 400 ms, returns (rms_lufs, True).
    """
    if samples is None or len(samples) == 0:
        return None, False

    channels = resample_audio(_channels(samples), sr, 48000)

    block_size = int(BLOCK_DURATION_S * 48000)  # 19200
    hop = int(block_size * (1.0 - OVERLAP_PERCENT))  # 4800

    filtered = apply_k_weighting(channels)
    # L/R têm peso 1. Soma de energias, nunca média dos sinais antes do quadrado.
    power = np.sum(filtered**2, axis=1)

    if len(filtered) < block_size:
        msq = float(np.mean(power))
        if msq <= 0:
            return None, True
        lufs = -0.691 + 10.0 * math.log10(msq)
        return (round(lufs, 1) if lufs > -70 else None), True

    z_blocks = []
    for start in range(0, len(filtered) - block_size + 1, hop):
        block = power[start : start + block_size]
        z_blocks.append(float(np.mean(block)))

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
    """Ganho de compensação limitado, com headroom de pico por amostra (não dBTP)."""
    if lufs is None:
        return 0.0

    raw_gain_db = target_lufs - lufs
    gain_db = max(MIN_GAIN_DB, min(MAX_GAIN_DB, raw_gain_db))

    if peak_abs > 0.0:
        peak_db = 20.0 * math.log10(peak_abs)
        headroom = SAMPLE_PEAK_CEILING_DB - peak_db
        if gain_db > headroom:
            # Floor to two decimal places so rounding never exceeds the ceiling
            safe_headroom = math.floor(headroom * 100.0) / 100.0
            return max(MIN_GAIN_DB, safe_headroom)

    return round(gain_db, 2)


def analyze_audio_loudness(samples: np.ndarray, sr: int = 48000) -> dict:
    """High-level analyzer returning lufs, gain_db and estimation flag."""
    if samples is None or len(samples) == 0:
        return {"lufs": None, "loudness_gain_db": 0.0, "lufs_estimated": False}

    channels = _channels(samples)
    peak_abs = float(np.max(np.abs(channels)))
    lufs, is_est = integrated_lufs(channels, sr=sr)
    gain_db = calculate_loudness_gain(lufs, peak_abs=peak_abs)
    return {
        "lufs": lufs,
        "loudness_gain_db": gain_db,
        "lufs_estimated": is_est,
    }
