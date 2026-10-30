"""Render único: intervalos, microfades e ganhos congelados da revisão."""
import hashlib
import json
import math
import uuid
import tempfile
from contextlib import contextmanager
from pathlib import Path

import numpy as np
import soundfile as sf
from audio_processing import resample_audio
from clip_edits import SAMPLE_RATE, CONVERSION_VERSION, validate_spans

RENDER_VERSION = 'spans-fade-gain-v1'


@contextmanager
def selected_source_file(source, revision, directory):
    """Trechos de fonte para referência vocal, sem ganhos nem microfades."""
    selected = render_revision(source, {**revision, 'variant': {}, 'gain_db': 0.0,
                                       'normalization_gain': 1.0, 'fade_samples': 0})
    with tempfile.NamedTemporaryFile(dir=directory, prefix='voice-selection-', suffix='.wav', delete=False) as temporary:
        path = Path(temporary.name)
    try:
        sf.write(path, selected, SAMPLE_RATE, format='WAV', subtype='FLOAT')
        yield str(path)
    finally:
        path.unlink(missing_ok=True)


def file_hash(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def preserve_source(path, destination):
    """Copia canônica endereçada pelo conteúdo; nunca move nem altera a entrada."""
    original_hash = file_hash(path)
    data, rate = sf.read(path, dtype='float32', always_2d=True)
    data = resample_audio(data, rate, SAMPLE_RATE)
    data = np.repeat(data, 2, axis=1) if data.shape[1] == 1 else data[:, :2]
    if not len(data):
        raise ValueError('Fonte de áudio vazia')
    identity = hashlib.sha256(CONVERSION_VERSION.encode() + data.tobytes()).hexdigest()
    target = Path(destination) / f'{identity}.wav'
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f'{identity}.{uuid.uuid4().hex}.part')
    try:
        sf.write(temporary, data, SAMPLE_RATE, format='WAV', subtype='FLOAT')
        if target.exists():
            existing, existing_rate = sf.read(target, dtype='float32', always_2d=True)
            if existing_rate != SAMPLE_RATE or not np.array_equal(existing, data):
                raise ValueError('A fonte preservada foi alterada')
        else:
            temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return {'id': identity, 'file_path': str(target), 'sha256': file_hash(target),
            'original_hash': original_hash, 'conversion_version': CONVERSION_VERSION,
            'sample_rate': SAMPLE_RATE, 'total_samples': len(data), 'duration': len(data) / SAMPLE_RATE,
            'available': True}


def render_key(source, revision):
    return hashlib.sha256(json.dumps([RENDER_VERSION, source['sha256'], revision], sort_keys=True).encode()).hexdigest()


def render_revision(source, revision, ffmpeg_path=None):
    if file_hash(source['file_path']) != source['sha256'] or revision['source_hash'] != source['sha256']:
        raise ValueError('A fonte foi alterada; a revisão não pode ser reproduzida')
    data, rate = sf.read(source['file_path'], dtype='float32', always_2d=True)
    if rate != SAMPLE_RATE or len(data) != source['total_samples']:
        raise ValueError('Referência PCM inválida')
    spans = validate_spans(revision['spans'], len(data))
    parts = []
    fade = max(0, min(240, int(revision.get('fade_samples', 48))))
    for start, end in spans:
        part = data[start:end].copy()
        count = min(fade, len(part) // 2)
        if count:
            ramp = np.linspace(0, 1, count, dtype=np.float32)[:, None]
            part[:count] *= ramp
            part[-count:] *= ramp[::-1]
        parts.append(part)
    gain_db = float(revision.get('gain_db', 0))
    normalization = float(revision.get('normalization_gain', 1))
    if not math.isfinite(gain_db) or not math.isfinite(normalization) or normalization < 0 or abs(gain_db) > 120:
        raise ValueError('Ganho inválido')
    result = np.concatenate(parts) * np.float32(10 ** (gain_db / 20) * normalization)
    if revision.get('variant'):
        from audio_variants import apply
        result = apply(result, revision['variant'], ffmpeg_path)
    return result
