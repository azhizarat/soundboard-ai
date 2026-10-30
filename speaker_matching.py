"""Decisão de identidade: cosseno não é probabilidade calibrada."""
import math
import numpy as np


def normalized(vector):
    value = np.asarray(vector, dtype=np.float64)
    if value.ndim != 1 or not value.size or not np.isfinite(value).all():
        return None
    norm = np.linalg.norm(value)
    return value / norm if norm > 1e-12 else None


def match_embedding(embedding, prototypes, threshold=.55, margin=.08):
    threshold, margin = float(threshold), float(margin)
    if not math.isfinite(threshold) or not math.isfinite(margin) or not -1 <= threshold <= 1 or not 0 <= margin <= 2:
        raise ValueError('Limiar/margem vocal inválidos')
    vector = normalized(embedding)
    scores = []
    if vector is not None:
        for person, prototype in prototypes.items():
            candidate = normalized(prototype)
            if candidate is not None and vector.shape == candidate.shape:
                scores.append({'speaker_id': person, 'score': float(np.clip(np.dot(vector, candidate), -1, 1))})
    scores.sort(key=lambda item: (-item['score'], item['speaker_id']))
    status, person = 'unknown', ''
    if scores and scores[0]['score'] >= threshold:
        status = 'ambiguous' if len(scores) > 1 and scores[0]['score'] - scores[1]['score'] < margin else 'identified'
        if status == 'identified':
            person = scores[0]['speaker_id']
    return {'status': status, 'speaker_id': person, 'candidates': scores[:2],
            'parameters': {'threshold': threshold, 'margin': margin}}
