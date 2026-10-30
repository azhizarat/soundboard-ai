"""Texto temporal no referencial imutável da fonte (48 kHz)."""
import copy

SAMPLE_RATE = 48000


def legacy(text):
    return {'version': 1, 'alignment': 'unaligned', 'text': text or '',
            'words': [], 'segments': [], 'corrections': []}


def from_segments(segments, analysis_id, source_id, interval, metadata):
    start, end = interval
    result = {**copy.deepcopy(metadata), 'version': 1, 'analysis_id': analysis_id,
              'source_id': source_id, 'interval': list(interval), 'sample_rate': SAMPLE_RATE,
              'alignment': 'aligned', 'words': [], 'segments': [], 'corrections': []}
    def entry(item, ident, text):
        lo = max(start, start + round(float(item.start) * SAMPLE_RATE))
        hi = min(end, start + round(float(item.end) * SAMPLE_RATE))
        return {'id': ident, 'text': text.strip(), 'start_sample': lo, 'end_sample': hi}
    for index, segment in enumerate(segments):
        block = entry(segment, f'{analysis_id}:s{index}', segment.text)
        block['word_ids'] = []
        if block['end_sample'] > block['start_sample']:
            result['segments'].append(block)
        for word in getattr(segment, 'words', None) or []:
            item = entry(word, f"{analysis_id}:w{len(result['words'])}", word.word)
            if item['end_sample'] > item['start_sample']:
                result['words'].append(item)
                block['word_ids'].append(item['id'])
        block['alignment'] = 'words' if block['word_ids'] else 'segment'
    result['text'] = ' '.join(s['text'] for s in result['segments']).strip()
    result['alignment'] = _alignment(result)
    return result


def _overlaps(item, interval):
    return item['start_sample'] < interval[1] and item['end_sample'] > interval[0]


def _segment_words(words, segment):
    # Resultados novos registram a associação real. Resultados anteriores podem
    # ser lidos pela interseção temporal, sem depender da lista global não vazia.
    if 'word_ids' in segment:
        ids = set(segment['word_ids'])
        return [word for word in words if word['id'] in ids]
    return [word for word in words if _overlaps(word, [segment['start_sample'], segment['end_sample']])]


def _alignment(result):
    words, segments = result.get('words', []), result.get('segments', [])
    if not words and not segments and result.get('alignment') == 'unaligned':
        return 'unaligned'
    missing = any(segment.get('text') and not _segment_words(words, segment) for segment in segments)
    if words:
        return 'partial' if missing else 'aligned'
    return 'segments' if segments else 'aligned'


def project(result, spans):
    """Recorta âncoras, sem atribuir precisão de palavra às correções manuais."""
    output = {'words': [], 'segments': [], 'corrections': [], 'alignment': _alignment(result)}
    for key in ('words', 'segments', 'corrections'):
        offset = 0
        for lo, hi in spans:
            for item in result.get(key, []):
                if _overlaps(item, [lo, hi]):
                    anchors = item.get('anchors') or [[item['start_sample'], item['end_sample']]]
                    for start, end in anchors:
                        a, b = max(lo, start), min(hi, end)
                        if a < b:
                            output[key].append({**copy.deepcopy(item), 'start_sample': a, 'end_sample': b,
                                                'output_start_sample': offset + a - lo,
                                                'output_end_sample': offset + b - lo,
                                                'partial': a != item['start_sample'] or b != item['end_sample']})
            offset += hi - lo
    replaced = {word for c in result.get('corrections', []) for word in c['word_ids']}
    items = [w for w in output['words'] if w['id'] not in replaced] + output['corrections']
    fallback_ids = {segment['id'] for segment in result.get('segments', [])
                    if not _segment_words(result.get('words', []), segment)}
    items += [segment for segment in output['segments'] if segment['id'] in fallback_ids and segment['id'] not in replaced]
    items.sort(key=lambda item: item['output_start_sample'])
    # Um bloco cortado em dois spans continua sendo uma única unidade textual.
    seen = set()
    text = []
    for item in items:
        if item['id'] not in seen:
            text.append(item['text'])
            seen.add(item['id'])
    output['text'] = result.get('text', '') if output['alignment'] == 'unaligned' else ' '.join(text).strip()
    return output


def correct(result, word_ids, text, spans=None):
    ids = set(word_ids)
    for block in result.get('corrections', []):
        block_ids = set(block['word_ids'])
        if ids.intersection(block_ids) and not block_ids.issubset(ids):
            raise ValueError('Amplie a seleção para corrigir o bloco humano completo')
    # Segmentos sem palavras também são unidades reais, sem tempos internos.
    ordered = sorted(result.get('words', []) + [segment for segment in result.get('segments', [])
                     if not _segment_words(result.get('words', []), segment)], key=lambda item: item['start_sample'])
    words = [w for w in ordered if w['id'] in ids]
    if not words or len(words) != len(ids):
        raise ValueError('Seleção de palavras inválida')
    positions = [i for i, word in enumerate(ordered) if word['id'] in ids]
    if max(positions) - min(positions) + 1 != len(positions):
        raise ValueError('Selecione um bloco contínuo')
    updated = copy.deepcopy(result)
    updated['corrections'] = [c for c in updated.get('corrections', []) if not ids.intersection(c['word_ids'])]
    updated['corrections'].append({'id': 'correction:' + ':'.join(sorted(ids)), 'word_ids': sorted(ids),
                                   'text': str(text), 'alignment': 'block', 'provenance': 'human',
                                   'start_sample': min(w['start_sample'] for w in words),
                                   'end_sample': max(w['end_sample'] for w in words)})
    block = updated['corrections'][-1]
    block['anchors'] = [[max(a, block['start_sample']), min(b, block['end_sample'])] for a, b in
                        (spans or [[block['start_sample'], block['end_sample']]]) if _overlaps(block, [a, b])]
    return updated


def replace_interval(previous, proposed, interval, accept_corrections=False):
    # Uma unidade sem alinhamento interno não pode perder texto externo ao alvo.
    # Mesmo o aceite humano de uma proposta não autoriza ampliar esse alvo.
    units = previous.get('words', []) + previous.get('corrections', [])
    units += [segment for segment in previous.get('segments', [])
              if not _segment_words(previous.get('words', []), segment)]
    for item in units:
        if _overlaps(item, interval) and (item['start_sample'] < interval[0] or item['end_sample'] > interval[1]):
            raise ValueError('Amplie a seleção para incluir a palavra ou o bloco corrigido completo')
    if (previous.get('alignment') == 'unaligned' and previous.get('text')) or any(
            _overlaps(c, interval) for c in previous.get('corrections', [])):
        if not accept_corrections:
            raise ValueError('A substituição exige aceite explícito das correções')
    updated = copy.deepcopy(proposed)
    for key in ('words', 'segments', 'corrections'):
        updated[key] = [copy.deepcopy(w) for w in previous.get(key, []) if not _overlaps(w, interval)]
        for item in proposed.get(key, []):
            if _overlaps(item, interval):
                updated[key].append({**copy.deepcopy(item), 'start_sample': max(item['start_sample'], interval[0]),
                                     'end_sample': min(item['end_sample'], interval[1])})
        updated[key].sort(key=lambda w: w['start_sample'])
    # Segmentos com palavras alinhadas podem manter suas partes externas usando
    # somente limites reconhecidos; não se reparte texto por caracteres.
    fragments = []
    def append_fragment(segment, words, bounds, suffix):
        if not _overlaps(segment, bounds):
            return
        if bounds[0] <= segment['start_sample'] and bounds[1] >= segment['end_sample']:
            fragments.append(copy.deepcopy(segment))
            return
        local_words = _segment_words(words, segment)
        if not local_words:
            raise ValueError('Amplie a seleção para incluir o segmento não alinhado completo')
        kept = [w for w in local_words if _overlaps(w, bounds)]
        if kept:
            fragments.append({**copy.deepcopy(segment), 'id': segment['id'] + ':' + suffix,
                              'word_ids': [w['id'] for w in kept], 'alignment': 'words',
                              'text': ' '.join(w['text'] for w in kept),
                              'start_sample': max(bounds[0], min(w['start_sample'] for w in kept)),
                              'end_sample': min(bounds[1], max(w['end_sample'] for w in kept))})
    for segment in previous.get('segments', []):
        if not _overlaps(segment, interval):
            fragments.append(copy.deepcopy(segment))
        else:
            append_fragment(segment, previous.get('words', []), [segment['start_sample'], interval[0]], 'before')
            append_fragment(segment, previous.get('words', []), [interval[1], segment['end_sample']], 'after')
    for segment in proposed.get('segments', []):
        append_fragment(segment, proposed.get('words', []), interval, 'target')
    updated['segments'] = sorted(fragments, key=lambda s: s['start_sample'])
    updated['alignment'] = _alignment(updated)
    return updated
