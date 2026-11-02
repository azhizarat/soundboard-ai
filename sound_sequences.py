"""Documentos de composição; intervalos locais medidos na revisão renderizada."""
import copy
import math
import uuid
from pathlib import Path

from clip_edits import validate_spans
from audio_variants import output_samples

DOCUMENT_VERSION = 1
MAX_BLOCKS = 512


def walk_blocks(blocks):
    for block in blocks:
        yield block
        if block['type'] == 'parallel':
            for branch in block['branches']:
                yield from walk_blocks(branch)


def validate_document(document, resolve):
    """Valida a árvore inteira antes de resolver ou iniciar qualquer áudio."""
    if not isinstance(document, dict) or document.get('schema_version') != DOCUMENT_VERSION:
        raise ValueError('Versão de sequência não suportada')
    for field in ('id', 'name'):
        if not isinstance(document.get(field), str) or not document[field].strip():
            raise ValueError(f'{field} da sequência obrigatório')
    if len(document['name']) > 200 or not isinstance(document.get('hotkey', ''), str):
        raise ValueError('Nome ou atalho inválido')
    seen, ancestors = set(), set()
    def validate(blocks, in_branch=False):
        if not isinstance(blocks, list) or not blocks:
            raise ValueError('Cada sequência e ramo precisa de ao menos um bloco')
        if id(blocks) in ancestors:
            raise ValueError('Ciclo na sequência')
        ancestors.add(id(blocks))
        total, manual = 0.0, 0
        for block in blocks:
            if not isinstance(block, dict):
                raise ValueError('Bloco inválido')
            ident = block.get('id')
            if not isinstance(ident, str) or not ident or ident in seen:
                raise ValueError('Cada ocorrência precisa de ID único; ciclos não são permitidos')
            seen.add(ident)
            if len(seen) > MAX_BLOCKS:
                raise ValueError(f'Limite de {MAX_BLOCKS} blocos excedido')
            kind = block.get('type')
            if kind == 'play':
                context = resolve(block)
                interval = context['_preview_ranges'][0]
                total += (interval[1] - interval[0]) / 48
            elif kind == 'wait':
                ms = block.get('ms')
                if isinstance(ms, bool) or not isinstance(ms, (float, int)) or not math.isfinite(ms) or ms < 0:
                    raise ValueError('Espera deve ser finita e não negativa')
                total += ms
            elif kind == 'manual':
                if in_branch:
                    raise ValueError('Acionamento manual não é permitido dentro de ramo')
                manual += 1
            elif kind == 'parallel':
                if in_branch:
                    raise ValueError('Paralelo aninhado não é permitido')
                branches = block.get('branches')
                if not isinstance(branches, list) or len(branches) < 2:
                    raise ValueError('Paralelo precisa de dois ou mais ramos')
                durations = [validate(branch, True)[0] for branch in branches]
                total += max(durations)
            else:
                raise ValueError('Tipo de bloco desconhecido')
            if not math.isfinite(total):
                raise ValueError('Duração excede o limite numérico')
        ancestors.remove(id(blocks))
        return total, manual
    duration, manual = validate(document.get('blocks'))
    return {'duration_ms': None if manual else duration, 'known_duration_ms': duration, 'manual_count': manual}


class SequenceStore:
    """Operações atômicas sob o lock do store; não persiste execução ativa."""

    def resolve_sequence_occurrence(self, occurrence):
        with self._lock:
            clip = self.get_clip(occurrence.get('clip_id'))
            revision = self.state['revisions'].get(occurrence.get('revision_id'))
            if not clip or not revision or revision.get('source_id') != clip.get('source_id'):
                raise ValueError('Clipe ou revisão fixada não encontrado')
            source = self.state['sources'].get(revision['source_id'])
            if not source or not source.get('available') or not Path(source['file_path']).is_file():
                raise ValueError('Origem da ocorrência indisponível')
            spans = validate_spans(revision['spans'], source['total_samples'])
            total = output_samples(sum(end - start for start, end in spans), revision.get('variant', {}))
            interval = occurrence.get('interval', [0, total])
            validate_spans([interval], total)
            volume = occurrence.get('volume', 1)
            if isinstance(volume, bool) or not isinstance(volume, (float, int)) or not math.isfinite(volume) or not 0 <= volume <= 3:
                raise ValueError('Volume local deve estar entre 0 e 3')
            # Não incorpora campos de aprendizado, texto atual ou ganho mutável da biblioteca.
            return {'id': clip['id'], 'file_path': source['file_path'], 'volume': volume,
                    '_source': copy.deepcopy(source), '_revision': copy.deepcopy(revision),
                    '_preview_ranges': [copy.deepcopy(interval)]}

    def get_sequence(self, sequence_id):
        with self._lock:
            return copy.deepcopy(self.state['sequences'].get(sequence_id))

    def sequence_details(self, sequence_id):
        with self._lock:
            document = self.get_sequence(sequence_id)
            if not document:
                raise ValueError('Sequência não encontrada')
            details = validate_document(document, self.resolve_sequence_occurrence)
            details['new_revisions'] = [block['id'] for block in walk_blocks(document['blocks'])
                if block['type'] == 'play' and self.get_clip(block['clip_id']).get('revision_id') != block['revision_id']]
            return {'document': document, **details}

    def save_sequence(self, document):
        with self._lock:
            validate_document(document, self.resolve_sequence_occurrence)
            self.validate_binding_change('sequence', document['id'], document.get('hotkey', ''))
            document = copy.deepcopy(document)
            previous = self.state['sequences'].get(document['id'])
            if previous and document.get('revision') != previous['revision']:
                raise ValueError('A sequência mudou; recarregue antes de salvar')
            document['revision'] = (previous or {}).get('revision', 0) + 1
            prefix = f"sequence:{document['id']}:"
            old_refs = copy.deepcopy(self.state['source_references'])
            self.state['source_references'] = {key: value for key, value in old_refs.items() if not key.startswith(prefix)}
            for block in walk_blocks(document['blocks']):
                if block['type'] == 'play':
                    self.state['source_references'][prefix + block['id']] = self.state['revisions'][block['revision_id']]['source_id']
            self.state['sequences'][document['id']] = document
            try:
                self.save()
            except Exception:
                self.state['source_references'] = old_refs
                if previous is None:
                    self.state['sequences'].pop(document['id'])
                else:
                    self.state['sequences'][document['id']] = previous
                raise
            return copy.deepcopy(document)

    def delete_sequence(self, sequence_id):
        with self._lock:
            affected = self.collections_using('sequence', sequence_id)
            if affected:
                raise ValueError('Sequência usada nas coleções: ' + ', '.join(affected))
            previous = self.state['sequences'].pop(sequence_id, None)
            refs = self.state['source_references']
            prefix = f'sequence:{sequence_id}:'
            self.state['source_references'] = {key: value for key, value in refs.items() if not key.startswith(prefix)}
            try:
                self.save()
            except Exception:
                self.state['source_references'] = refs
                if previous:
                    self.state['sequences'][sequence_id] = previous
                raise
            return previous is not None

    def sequences_using_clip(self, clip_id):
        with self._lock:
            return [doc['name'] for doc in self.state['sequences'].values()
                    if any(block['type'] == 'play' and block['clip_id'] == clip_id for block in walk_blocks(doc['blocks']))]

    def sequence_clip_choices(self):
        with self._lock:
            choices = []
            for clip in self.state['clips']:
                if clip.get('pending'):
                    continue
                revision = self.state['revisions'].get(clip.get('revision_id'))
                if revision:
                    samples = output_samples(sum(b - a for a, b in revision['spans']), revision.get('variant', {}))
                else:
                    # Estimativa legada pelos metadados; listar nunca promove nem decodifica áudio.
                    duration = max(0, float(clip.get('duration') or 0))
                    start = max(0, min(duration, float(clip.get('trim_start') or 0)))
                    end = max(start, min(duration, float(clip.get('trim_end') or duration)))
                    samples = round((end - start) * 48000)
                speaker_ids = list(clip.get('speaker_ids') or [])
                if clip.get('speaker_id') and clip['speaker_id'] not in speaker_ids:
                    speaker_ids.insert(0, clip['speaker_id'])
                names = [self.state['speakers'].get(sid, {}).get('name', '') for sid in dict.fromkeys(speaker_ids)]
                names = [name for name in names if name]
                transcript = clip.get('transcript') or ''
                choices.append({'id': clip['id'], 'title': clip.get('title', ''),
                    'revision_id': clip.get('revision_id'), 'samples': samples, 'volume': clip.get('volume', 1),
                    'transcript': transcript, 'text': transcript, 'speaker_name': ', '.join(names),
                    'speaker_names': names, 'category': clip.get('category') or '',
                    'emoji': clip.get('emoji') or '🔊', 'color': clip.get('color') or '#3b82f6',
                    'duration': samples / 48000})
            return choices


def new_document(name='Nova sequência'):
    return {'schema_version': DOCUMENT_VERSION, 'id': str(uuid.uuid4()), 'revision': 0,
            'name': name, 'hotkey': '', 'blocks': []}
