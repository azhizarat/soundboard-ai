"""Comandos transacionais do editor, sempre na referência imutável de 48 kHz."""
import copy
import math
import uuid

from clip_edits import SAMPLE_RATE, validate_spans
from transcript_model import legacy, project, correct


def intersect(spans, interval):
    lo, hi = interval
    return [[max(a, lo), min(b, hi)] for a, b in spans if a < hi and b > lo]


def edit_spans(spans, action, interval, total):
    lo, hi = validate_spans([interval], total)[0]
    if action == 'keep':
        result = intersect(spans, (lo, hi))
    elif action == 'remove':
        result = []
        for a, b in spans:
            if a < lo:
                result.append([a, min(b, lo)])
            if b > hi:
                result.append([max(a, hi), b])
    elif action == 'restore':
        result = []
        for a, b in sorted([*spans, [lo, hi]]):
            if result and a <= result[-1][1]:
                result[-1][1] = max(b, result[-1][1])
            else:
                result.append([a, b])
    else:
        raise ValueError('Comando de montagem inválido')
    return [list(pair) for pair in validate_spans(result, total)]


def snapshot(context):
    return {'spans': copy.deepcopy(context['_revision']['spans']),
            'transcript': copy.deepcopy(context.get('transcript_analysis') or legacy(context.get('transcript', '')))}


def _history(store, ident, draft, current):
    if not hasattr(store, '_editor_history'):
        store._editor_history = {}
    history = store._editor_history.get(ident)
    if not history or history['draft_id'] != draft['id'] or history['current'] != current:
        history = {'draft_id': draft['id'], 'current': current, 'undo': [], 'redo': []}
    return copy.deepcopy(history)


def state(store, ident):
    with store._lock:
        context = store.render_context(ident, True)
        draft = store.state['edit_drafts'].get(ident)
        if not context or not draft:
            raise ValueError('Abra o editor antes de editar')
        current = snapshot(context)
        history = _history(store, ident, draft, current)
        from audio_variants import project as project_variant, output_samples
        variant = context['_revision'].get('variant', {})
        return {'spans': current['spans'], 'source_samples': context['_source']['total_samples'],
                'source_id': draft['source_id'], 'draft_id': draft['id'], 'generation': draft['generation'],
                'source_transcript': current['transcript'], 'result': project_variant(current['transcript'], current['spans'], variant),
                'variant': copy.deepcopy(variant), 'revision_id': context.get('revision_id'),
                'duration': output_samples(sum(b-a for a, b in current['spans']), variant) / SAMPLE_RATE,
                'volume': context.get('volume', 1), 'can_undo': bool(history['undo']), 'can_redo': bool(history['redo'])}


def command(store, ident, action, payload):
    with store._lock:
        context = store.render_context(ident, True)
        previous = copy.deepcopy(store.state['edit_drafts'].get(ident))
        if not previous or not context:
            raise ValueError('Abra o editor antes de editar')
        if payload.get('draft_id', previous['id']) != previous['id'] or payload.get('generation', previous['generation']) != previous['generation']:
            raise ValueError('A edição mudou. Atualize a seleção antes de aplicar o comando')
        current = snapshot(context)
        history = _history(store, ident, previous, current)
        updated = copy.deepcopy(current)
        total = context['_source']['total_samples']
        if action == 'create':
            return _create(store, context, payload['interval'], previous)
        if action in ('undo', 'redo'):
            if not history[action]:
                raise ValueError('Nenhum comando para desfazer/refazer')
            updated = history[action].pop()
            history['redo' if action == 'undo' else 'undo'].append(current)
        else:
            if action in ('keep', 'remove', 'restore'):
                updated['spans'] = edit_spans(current['spans'], action, payload['interval'], total)
            elif action == 'correct':
                updated['transcript'] = correct(current['transcript'], payload.get('word_ids', []), payload.get('text', ''), current['spans'])
            elif action == 'legacy_text':
                if current['transcript'].get('alignment') != 'unaligned':
                    raise ValueError('Selecione um bloco para corrigir a transcrição alinhada')
                updated['transcript'] = legacy(str(payload.get('text', '')))
            else:
                raise ValueError('Comando de edição inválido')
            if updated == current:
                return None
            history['undo'].append(current)
            history['undo'] = history['undo'][-100:]
            history['redo'] = []
        validate_spans(updated['spans'], total)
        draft = copy.deepcopy(previous)
        draft['revision']['spans'] = updated['spans']
        draft['fields']['transcript_analysis'] = updated['transcript']
        draft['fields']['transcript'] = project(updated['transcript'], updated['spans'])['text']
        if updated['transcript'] != current['transcript']:
            for key in ('transcript', 'transcript_analysis'):
                draft.setdefault('field_versions', {})[key] = draft.get('field_versions', {}).get(key, 0) + 1
                draft.setdefault('field_provenance', {})[key] = 'human'
        draft['generation'] += 1
        store.state['edit_drafts'][ident] = draft
        try:
            store.save()
        except Exception:
            store.state['edit_drafts'][ident] = previous
            raise
        history['current'] = updated
        store._editor_history[ident] = history
        return None


def _create(store, context, interval, draft):
    """Nova referência publicada numa única gravação; não altera o draft pai."""
    spans = edit_spans(context['_revision']['spans'], 'keep', interval, context['_source']['total_samples'])
    revision = copy.deepcopy(context['_revision'])
    from audio_variants import output_samples
    revision.update(id=uuid.uuid4().hex, spans=spans, duration=output_samples(sum(b-a for a,b in spans), revision.get('variant', {}))/SAMPLE_RATE)
    clip = {k: copy.deepcopy(v) for k,v in context.items() if not k.startswith('_')}
    clip.update(id=uuid.uuid4().hex, revision_id=revision['id'], pending=False,
                title=(clip.get('title') or 'Áudio') + ' — trecho', hotkey='', play_count=0)
    clip.pop('montage_identity', None)
    clip.pop('analysis_proposals', None)
    clip.pop('analysis_generations', None)
    clip.pop('voice_action_id', None)
    clip['field_versions'] = copy.deepcopy(draft.get('field_versions', {}))
    clip['field_provenance'] = copy.deepcopy(draft.get('field_provenance', {}))
    clip['transcript'] = project(clip.get('transcript_analysis') or legacy(clip.get('transcript')), spans)['text']
    from clip_renderer import render_revision
    from loudness import analyze_audio_loudness
    measurement = analyze_audio_loudness(render_revision(context['_source'], revision, context.get('_ffmpeg_path')) * float(clip.get('volume', 1)), sr=SAMPLE_RATE)
    clip['lufs'], clip['lufs_estimated'] = measurement['lufs'], measurement['lufs_estimated']
    clip['effective_duration'] = revision['duration']
    store.state['revisions'][revision['id']] = revision
    store.state['clips'].append(clip)
    try:
        store.save()
    except Exception:
        store.state['clips'].remove(clip)
        store.state['revisions'].pop(revision['id'], None)
        raise
    return copy.deepcopy(clip)


def preview_ranges(spans, mode, interval=None, junction=0):
    total = sum(b-a for a,b in spans)
    if mode == 'result':
        return [[0, total]]
    if mode == 'junction':
        if isinstance(junction, bool) or not isinstance(junction, int) or not 0 <= junction < len(spans)-1:
            raise ValueError('Junção inválida')
        boundary = sum(b-a for a,b in spans[:junction+1])
        return [[boundary-min(24000, spans[junction][1]-spans[junction][0]),
                 boundary+min(24000, spans[junction+1][1]-spans[junction+1][0])]]
    if mode != 'selection':
        raise ValueError('Modo de prévia inválido')
    validate_spans([interval], spans[-1][1] if interval is None else max(spans[-1][1], interval[1]))
    ranges, offset = [], 0
    for a,b in spans:
        for lo,hi in intersect([[a,b]], interval):
            ranges.append([offset+lo-a, offset+hi-a])
        offset += b-a
    return [list(pair) for pair in validate_spans(ranges, total)]


def suggest(context, margin):
    if not math.isfinite(float(margin)) or not 0 <= float(margin) <= 2:
        raise ValueError('Margem deve estar entre 0 e 2 segundos')
    temporal = context.get('transcript_analysis') or {}
    items = temporal.get('segments', []) or temporal.get('words', [])
    total = context['_source']['total_samples']
    pad = round(float(margin)*SAMPLE_RATE)
    if not items:
        return {'interval': [0,total], 'reason': 'Sem fala alinhada: origem inteira preservada; ajuste pela waveform.'}
    return {'interval': [max(0,min(i['start_sample'] for i in items)-pad), min(total,max(i['end_sample'] for i in items)+pad)],
            'reason': 'Bordas da fala com margem. Risadas e efeitos fora da fala podem existir; ouça antes de manter.'}
