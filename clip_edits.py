"""Contratos puros de edição na referência PCM de 48 kHz."""
from numbers import Integral

SAMPLE_RATE = 48000
CONVERSION_VERSION = 'pcm48-stereo-f32-v1'


def validate_spans(spans, total_samples):
    if isinstance(total_samples, bool) or not isinstance(total_samples, Integral) or total_samples < 0:
        raise ValueError('Duração inválida')
    result, previous = [], 0
    for span in spans:
        if len(span) != 2:
            raise ValueError('Intervalo inválido')
        start, end = span
        if any(isinstance(v, bool) or not isinstance(v, Integral) for v in span):
            raise ValueError('Amostras devem ser inteiras')
        if start < previous or end <= start or end > total_samples:
            raise ValueError('Intervalos fora da fonte, sobrepostos ou vazios')
        result.append((int(start), int(end)))
        previous = end
    if not result:
        raise ValueError('Selecione ao menos um intervalo')
    return result


def output_to_source_sample(spans, output_sample):
    if isinstance(output_sample, bool) or not isinstance(output_sample, Integral) or output_sample < 0:
        raise ValueError('Amostra de saída inválida')
    validated = validate_spans(spans, max((end for _, end in spans), default=0))
    remaining = output_sample
    for start, end in validated:
        if remaining < end - start:
            return start + remaining
        remaining -= end - start
    raise ValueError('Amostra fora da saída')


def project_segments(segments, spans):
    result, offset = [], 0
    for start, end in spans:
        for segment in segments or []:
            left = max(start, round(float(segment.get('start', 0)) * SAMPLE_RATE))
            right = min(end, round(float(segment.get('end', 0)) * SAMPLE_RATE))
            if left < right:
                result.append({**segment, 'start': (offset + left - start) / SAMPLE_RATE,
                               'end': (offset + right - start) / SAMPLE_RATE})
        offset += end - start
    return result
