"""Referências locais supervisionadas, reconstruíveis e rastreáveis."""
import copy
import hashlib
import json
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

import numpy as np
from clip_edits import validate_spans
from speaker_matching import normalized, match_embedding

EXTRACTOR_VERSION = 'titanet-local-v1'
QUALITY_POLICY = {'version': 'conservative-v1', 'min_speech_seconds': .5,
                  'max_silence_fraction': .65, 'max_clipping_fraction': .02,
                  'max_samples_per_person': 64, 'max_active_samples': 32}


def empty_bank():
    return {'version': 1, 'samples': {}, 'profiles': {}, 'legacy': {}, 'actions': {},
            'migrations': {}, 'quality_policy': copy.deepcopy(QUALITY_POLICY), 'review_proposals': {}, 'matching_version': 0,
            'sample_epoch': 0}


def measure_quality(audio, mixed=None):
    """Heurística conservadora registrada; energia não prova fala nem separa vozes."""
    audio = np.asarray(audio)
    frame_count = len(audio) // 320
    frames = audio[:frame_count * 320].reshape((-1, 320)) if frame_count else np.empty((0, 320))
    active = np.sqrt(np.mean(frames ** 2, axis=1)) > .01 if frame_count else np.array([])
    return {'speech_seconds': float(np.sum(active) * .02),
            'silence_fraction': float(1 - np.mean(active)) if frame_count else 1.,
            'clipping_fraction': float(np.mean(np.abs(audio) >= .999)) if len(audio) else 1.,
            'mixed': mixed, 'estimator': 'energy-20ms-v1',
            'limitations': 'Energia estima atividade; mistura depende da seleção/diarização, não de certeza acústica.'}


class VoiceProfiles:
    def __init__(self, store):
        self.store = store
        self._transaction_depth = 0
        from analysis_cache import AnalysisCache
        self._matching_cache = AnalysisCache(max_bytes=2*1024*1024, max_items=256)

    @property
    def bank(self):
        return self.store.state['voice_bank']

    @contextmanager
    def transaction(self):
        # O lock cobre snapshot, mutação e gravação; rollback não perde alterações concorrentes.
        with self.store._lock:
            if self._transaction_depth:
                yield
                return
            previous = copy.deepcopy(self.store.state)
            self._transaction_depth += 1
            try:
                yield
                self.store.save()
            except Exception:
                self.store.state.clear(); self.store.state.update(previous)
                raise
            finally:
                self._transaction_depth -= 1

    def snapshot(self):
        with self.store._lock:
            return copy.deepcopy(self.bank)

    def recover_interrupted_learning(self):
        """Somente no startup, antes de aceitar trabalhos da nova sessão."""
        with self.store._lock:
            pending = [action for action in self.bank['actions'].values() if action['state'] == 'pending']
            if not pending:
                return 0
            with self.transaction():
                for action in pending:
                    action.update(state='interrupted', reference_learned=False, interrupted_at=time.time(),
                                  error='Aprendizado interrompido na sessão anterior. Cadastre o trecho novamente ou desfaça a correção.')
            return len(pending)

    def _rebuild(self, people):
        for person in set(people):
            old = self.bank['profiles'].get(person, {})
            groups = {}
            samples = [s for s in self.bank['samples'].values() if s['speaker_id'] == person]
            eligible = sorted((s for s in samples if s['eligibility'] == 'eligible'),
                              key=lambda s: (-s['weight'], s['sample_id']))[:QUALITY_POLICY['max_active_samples']]
            used = {s['sample_id'] for s in eligible}
            for sample in samples:
                sample['used_in_profile'] = sample['sample_id'] in used
            for sample in eligible:
                groups.setdefault(sample['extractor_version'], []).append((sample['embedding'], sample['weight']))
            legacy = self.bank['legacy'].get(person)
            if legacy and not legacy.get('disabled'):
                groups.setdefault(legacy['extractor_version'], []).append((legacy['embedding'], .25))
            prototypes = {}
            for version, items in groups.items():
                # Modelos/dimensões diferentes nunca são comparados ou somados.
                dimensions = {len(v) for v, _ in items}
                if len(dimensions) != 1:
                    continue
                vector = normalized(np.sum([np.asarray(v) * w for v, w in items], axis=0))
                if vector is not None:
                    prototypes[version] = vector.tolist()
            self.bank['profiles'][person] = {'profile_version': old.get('profile_version', 0) + 1,
                                            'prototypes': prototypes, 'sample_ids': sorted(used)}
        self._invalidate_matching()

    def _invalidate_matching(self):
        # Um novo perfil pode mudar o segundo candidato: invalida matching, jamais ASR.
        self.bank['matching_version'] = self.bank.get('matching_version', 0) + 1
        for clip in self.store.state['clips']:
            clip['speaker_matching_stale'] = True
            generations = clip.setdefault('analysis_generations', {})
            generations['speakers'] = generations.get('speakers', 0) + 1
        self.bank['review_proposals'].clear()

    def update_match_settings(self, updates):
        with self.transaction():
            previous = {k: self.store.state['settings'].get(k) for k in updates}
            self.store.state['settings'].update(updates)
            if any(previous[k] != updates[k] for k in ('voice_match_threshold', 'voice_match_margin') if k in updates):
                self._invalidate_matching()
            return copy.deepcopy(self.store.state['settings'])

    def prototypes(self, extractor_version):
        with self.store._lock:
            return {person: copy.deepcopy(profile['prototypes'][extractor_version])
                    for person, profile in self.bank['profiles'].items() if extractor_version in profile['prototypes']}

    def match(self, embedding, extractor_version=EXTRACTOR_VERSION):
        with self.store._lock:
            from analysis_cache import cache_key, pcm_hash
            settings = self.store.state['settings']
            versions = {k: v['profile_version'] for k, v in self.bank['profiles'].items()}
            key = cache_key(pcm_hash(embedding), None, 'matching', extractor_version,
                            {'profile_versions': versions, 'bank_version': self.bank.get('matching_version', 0),
                             'threshold': settings['voice_match_threshold'], 'margin': settings['voice_match_margin']})
            cached = self._matching_cache.get(key)
            if cached is not None:
                return cached
            result = {**match_embedding(embedding, self.prototypes(extractor_version),
                    settings['voice_match_threshold'], settings['voice_match_margin']),
                    'extractor_version': extractor_version,
                    'profile_versions': versions}
            self._matching_cache.put(key, result)
            return result

    def migrate_legacy(self, path, extractor_version='legacy:unknown'):
        path = Path(path)
        if not path.exists():
            return
        identity = str(path.resolve())
        with self.transaction():
            if identity in self.bank['migrations']:
                return
            raw = path.read_bytes()
            digest = hashlib.sha256(raw).hexdigest()
            backup = path.with_suffix('.npz.bak')
            if backup.exists() and backup.read_bytes() != raw:
                backup = path.with_name(path.name + '.' + digest[:12] + '.bak')
            if not backup.exists():
                backup.write_bytes(raw)
            if backup.read_bytes() != raw:
                raise OSError('Backup das referências não pôde ser verificado')
            people = []
            with np.load(path, allow_pickle=False) as data:
                for person in data.files:
                    vector = normalized(data[person])
                    if vector is None:
                        continue
                    self.bank['legacy'][person] = {'provenance': 'legacy', 'embedding': vector.tolist(),
                        'extractor_version': extractor_version, 'original_path': identity, 'sha256': digest,
                        'quality': 'not_recorded', 'source_intervals': None}
                    self.store.ensure_speaker(person)
                    people.append(person)
            self.bank['migrations'][identity] = {'backup': str(backup), 'sha256': digest}
            self._rebuild(people)

    @staticmethod
    def dedupe_key(source_id, spans, extractor_version):
        return hashlib.sha256(json.dumps([source_id, spans, extractor_version], sort_keys=True).encode()).hexdigest()

    def add_sample(self, speaker_id, context, spans, *, action_id, extractor_version, embedding, quality):
        if not speaker_id or speaker_id.startswith('local:') or not action_id:
            raise ValueError('Uma pessoa global e uma confirmação manual são obrigatórias')
        source, revision = context['_source'], context['_revision']
        spans = [list(pair) for pair in validate_spans(spans, source['total_samples'])]
        if any(not any(a <= lo and hi <= b for a, b in revision['spans']) for lo, hi in spans):
            raise ValueError('Referência inclui áudio removido da montagem')
        key = self.dedupe_key(source['id'], spans, extractor_version)
        with self.transaction():
            existing = self.bank['samples'].get(key)
            if existing:
                if existing['speaker_id'] != speaker_id:
                    raise ValueError('Este trecho já pertence a outra pessoa; reatribua no perfil')
                if (existing.get('embedding') is not None or embedding is None) and existing['quality'].get('mixed') == quality.get('mixed'):
                    return {'sample': copy.deepcopy(existing), 'created': False}
            if existing is None and sum(s['speaker_id'] == speaker_id for s in self.bank['samples'].values()) >= QUALITY_POLICY['max_samples_per_person']:
                raise ValueError('Limite de amostras por pessoa; remova exemplos antes de cadastrar')
            vector = normalized(embedding) if embedding is not None else None
            quality = copy.deepcopy(quality)
            reasons = []
            if quality.get('speech_seconds', 0) < QUALITY_POLICY['min_speech_seconds']: reasons.append('insufficient_speech')
            if quality.get('silence_fraction', 1) > QUALITY_POLICY['max_silence_fraction']: reasons.append('silence')
            if quality.get('clipping_fraction', 1) > QUALITY_POLICY['max_clipping_fraction']: reasons.append('clipping')
            if quality.get('mixed') is True: reasons.append('mixed')
            if quality.get('mixed') is None: reasons.append('uncertain_mix')
            if vector is None: reasons.append('embedding_unavailable')
            quality.update(reasons=reasons, policy=copy.deepcopy(QUALITY_POLICY))
            sample = {'sample_id': key, 'speaker_id': speaker_id, 'source_id': source['id'],
                'revision_id': revision['id'], 'revision': copy.deepcopy(revision), 'spans': spans,
                'manual_action_id': action_id, 'extractor_version': extractor_version,
                'embedding': vector.tolist() if vector is not None else None,
                'quality': quality, 'eligibility': 'pending' if reasons else 'eligible',
                'weight': max(.01, min(quality.get('speech_seconds', 0), 5.) * (1 - quality.get('silence_fraction', 1))),
                'created_at': time.time(), 'used_in_profile': False}
            downgraded_mix = bool(existing and existing['quality'].get('mixed') is False and quality.get('mixed') is not False)
            if downgraded_mix:
                # Nova evidência da fonte não é anulada por desfazer o cadastro.
                # Mantém a propriedade da ação original, agora sem influência.
                sample['manual_action_id'] = existing['manual_action_id']
                sample['created_at'] = existing['created_at']
                sample['quality_review_action_id'] = action_id
                original_action = self.bank['actions'].get(existing['manual_action_id'])
                if original_action:
                    original_action.update(state='pending_review', reference_learned=False, error=', '.join(reasons))
            self.bank['samples'][key] = sample
            self.store.state['source_references']['voice:' + key] = source['id']
            self.store.ensure_speaker(speaker_id)
            self._rebuild([speaker_id])
            return {'sample': copy.deepcopy(sample), 'created': existing is None,
                    'previous_sample': copy.deepcopy(existing) if not downgraded_mix else None}

    def remove_sample(self, sample_id):
        with self.transaction():
            sample = self.bank['samples'].pop(sample_id, None)
            if not sample: return False
            epoch = self.bank.get('sample_epoch', 0) + 1
            self.bank['sample_epoch'] = epoch
            source = self.store.state['sources'][sample['source_id']]
            source.setdefault('voice_sample_removals', {})[sample_id] = epoch
            self.store.state['source_references'].pop('voice:' + sample_id, None)
            self._rebuild([sample['speaker_id']])
            return True

    def reassign_sample(self, sample_id, speaker_id):
        if not speaker_id or speaker_id.startswith('local:'):
            raise ValueError('Selecione uma pessoa global')
        with self.transaction():
            sample = self.bank['samples'][sample_id]
            previous = sample['speaker_id']
            if previous != speaker_id:
                if sum(s['speaker_id'] == speaker_id for s in self.bank['samples'].values()) >= QUALITY_POLICY['max_samples_per_person']:
                    raise ValueError('Limite de amostras da pessoa de destino; remova exemplos antes de reatribuir')
                sample['speaker_id'] = speaker_id
                sample.setdefault('reassignments', []).append({'from': previous, 'to': speaker_id, 'at': time.time()})
                self.store.ensure_speaker(speaker_id)
                self._rebuild([previous, speaker_id])
            return copy.deepcopy(sample)

    def sample_context(self, sample_id):
        with self.store._lock:
            sample = self.bank['samples'][sample_id]
            revision = {**copy.deepcopy(sample['revision']), 'spans': copy.deepcopy(sample['spans']),
                        'gain_db': 0, 'normalization_gain': 1, 'fade_samples': 0}
            return {'id': 'voice:' + sample_id, 'source_id': sample['source_id'], 'volume': 1,
                    '_revision': revision, '_source': copy.deepcopy(self.store.state['sources'][sample['source_id']])}

    def delete_person(self, speaker_id):
        with self.transaction():
            existed = speaker_id in self.store.state['speakers']
            for key, sample in list(self.bank['samples'].items()):
                if sample['speaker_id'] == speaker_id:
                    del self.bank['samples'][key]
                    self.store.state['source_references'].pop('voice:' + key, None)
            if speaker_id in self.bank['legacy']:
                self.bank['legacy'][speaker_id]['disabled'] = True
            self._rebuild([speaker_id])
            self.store.state['speakers'].pop(speaker_id, None)
            for action in self.bank['actions'].values():
                if speaker_id in (action['speaker_id'], action.get('previous_speaker_id')):
                    action.update(state='deleted_person', reference_learned=False)
            for clip in self.store.state['clips']:
                if speaker_id in clip.get('speaker_ids', []):
                    clip['speaker_ids'] = [s for s in clip['speaker_ids'] if s != speaker_id]
                if clip.get('speaker_id') == speaker_id:
                    clip['speaker_id'] = ''
            for draft in self.store.state['edit_drafts'].values():
                if draft['fields'].get('speaker_id') == speaker_id:
                    draft['fields']['speaker_id'] = ''
                if speaker_id in draft['fields'].get('speaker_ids', []):
                    draft['fields']['speaker_ids'] = [s for s in draft['fields']['speaker_ids'] if s != speaker_id]
            return existed
