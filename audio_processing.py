"""Reamostragem filtrada compartilhada para áudio decodificado."""
from math import gcd
from numbers import Integral

import numpy as np
from scipy.signal import resample_poly


def resample_audio(audio: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
    """Preserva canais no eixo 1; saída float32 com ceil(n * destino / origem)."""
    for rate in (source_rate, target_rate):
        if isinstance(rate, bool) or not isinstance(rate, Integral) or rate <= 0:
            raise ValueError("A taxa de amostragem deve ser um inteiro positivo")
    data = np.asarray(audio, dtype=np.float32)
    if data.ndim not in (1, 2) or (data.ndim == 2 and data.shape[1] == 0):
        raise ValueError("Áudio deve conter amostras mono ou canais no eixo 1")
    if not np.isfinite(data).all():
        raise ValueError("Áudio contém amostras inválidas")
    if not len(data) or source_rate == target_rate:
        return data
    divisor = gcd(source_rate, target_rate)
    return resample_poly(data, target_rate // divisor, source_rate // divisor, axis=0).astype(np.float32)
