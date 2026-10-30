"""Orquestração de correções humanas; a fila extrai, o banco decide elegibilidade."""
import copy
import threading
import time
import uuid
from pathlib import Path

from voice_profiles import VoiceProfiles, EXTRACTOR_VERSION, measure_quality


class VoiceLearningAPI:
    def _preserve_voice_evidence(self, clip):
        """Atribuição manual não apaga evidência de mistura da captura/fonte."""
        if not clip:
            return
        source = self._store.state['sources'].get(clip.get('source_id'), {})
        labels = {s.get('local_cluster_id') or s.get('speaker_id') for s in clip.get('segments', [])}
        multiple = (len(set(clip.get('speaker_ids', [])) - {None, ''}) > 1
                    or len(labels - {None, ''}) > 1
                    or clip.get('voice_source_evidence', {}).get('multiple_voices')
                    or source.get('voice_evidence', {}).get('multiple_voices'))
        if not multiple:
            return
        profiles = self._voices()
        with profiles.transaction():
            current = self._store.get_clip(clip['id'])
            evidence = {'multiple_voices': True, 'provenance': 'observed_clip_speakers_or_clusters'}
            if current:
                current['voice_source_evidence'] = evidence.copy()
                source = self._store.state['sources'].get(current.get('source_id'))
                if source is not None:
                    source['voice_evidence'] = evidence.copy()

    def _voices(self):
        if not hasattr(self, '_voice_profiles'):
            self._voice_profiles = VoiceProfiles(self._store)
            path = getattr(self._ai, 'ref_voices_path', None)
            if path and Path(path).resolve().is_relative_to(self._store.data_dir):
                self._voice_profiles.migrate_legacy(path)
        self._ai.voice_profiles = self._voice_profiles
        return self._voice_profiles

    def import_legacy_voice_profiles(self):
        """Ação explícita para instalações antigas fora da pasta desta biblioteca."""
        try:
            path = getattr(self._ai, 'ref_voices_path', self._store.models_dir / 'referencias_vozes.npz')
            if not Path(path).exists():
                return {'ok': False, 'error': 'Nenhuma referência legada configurada'}
            self._voices().migrate_legacy(path)
            return self.get_voice_profiles()
        except (OSError, ValueError) as exc:
            return {'ok': False, 'error': str(exc)}

    def get_voice_profiles(self, speaker_id=None):
        bank = self._voices().snapshot()
        samples = [{k: v for k, v in sample.items() if k not in ('embedding', 'revision')}
                   for sample in bank['samples'].values() if not speaker_id or sample['speaker_id'] == speaker_id]
        return {'ok': True, 'samples': samples, 'profiles': bank['profiles'],
                'legacy': {k: {field: value for field, value in v.items() if field != 'embedding'}
                           for k, v in bank['legacy'].items() if not speaker_id or k == speaker_id},
                'actions': bank['actions'], 'speakers': self._store.snapshot()['speakers'],
                'settings': self._store.snapshot()['settings'], 'quality_policy': bank['quality_policy']}

    def _learn_correction(self, previous, updated, interval=None, explicit=False):
        """Só chamado por salvar/cadastrar manualmente, nunca pelo aplicador de IA."""
        person = updated.get('speaker_id', '')
        enabled = bool(person) and (explicit or self._store.state['settings']['learn_voice_corrections'])
        if person.startswith('local:'):
            return {'assignment_saved': True, 'reference_learned': False,
                    'error': 'Selecione uma pessoa global para aprender a voz'}
        profiles = self._voices()
        action_id = uuid.uuid4().hex
        try:
            with profiles.transaction():
                # Uma nova correção substitui só a influência que a ação anterior criou.
                old_id = updated.get('voice_action_id')
                old = profiles.bank['actions'].get(old_id)
                if old and old['clip_id'] != updated['id']:
                    old_id, old = None, None
                withdrawn = None
                if old and old['speaker_id'] != person:
                    old['state'] = 'superseded'
                    sample = profiles.bank['samples'].get(old.get('sample_id'))
                    origin = profiles.bank['actions'].get(sample['manual_action_id']) if sample else None
                    if sample and origin and origin['clip_id'] == updated['id'] and sample['speaker_id'] == old['speaker_id']:
                        withdrawn = copy.deepcopy(sample)
                        profiles.remove_sample(sample['sample_id'])
                action = {'action_id': action_id, 'clip_id': updated['id'], 'speaker_id': person,
                    'previous_speaker_id': previous.get('speaker_id', ''),
                    'previous_speaker_ids': previous.get('speaker_ids', []), 'previous_action_id': old_id,
                    'created_at': time.time(), 'origin': 'manual_enrollment' if explicit else 'manual_correction',
                    'state': 'pending' if enabled else 'disabled', 'reference_learned': False,
                    'sample_epoch': profiles.bank.get('sample_epoch', 0),
                    'analysis_id': action_id if enabled else None,
                    'owned_sample_id': None, 'error': None,
                    'withdrawn_sample': withdrawn,
                    'confused_pair': [previous.get('speaker_id'), person] if previous.get('speaker_id') and previous.get('speaker_id') != person else None}
                profiles.bank['actions'][action_id] = action
                updated['voice_action_id'] = action_id
        except Exception as exc:
            return {'assignment_saved': True, 'reference_learned': False, 'error': f'Pessoa corrigida; cadastro não registrado: {exc}'}
        base = {'assignment_saved': True, 'reference_learned': False, 'action_id': action_id}
        if not enabled:
            return {**base, 'learning': {'state': 'disabled'}}
        handle = None
        try:
            if not updated.get('revision_id'):
                self._store.begin_edit(updated['id']); self._store.commit_edit(updated['id'])
            self._preserve_voice_evidence(updated)
            with self._store._lock:
                context = self._store.render_context(updated['id'])
                revision = context['_revision']
                multi = (len(set(previous.get('speaker_ids', []))) > 1
                         or context.get('voice_source_evidence', {}).get('multiple_voices', False)
                         or context['_source'].get('voice_evidence', {}).get('multiple_voices', False))
                labels = {s.get('local_cluster_id') or s.get('speaker_id') for s in context.get('segments', [])}
                multi = multi or len(labels - {None, ''}) > 1
                if multi and interval is None:
                    raise ValueError('Várias vozes: selecione a fala da pessoa antes de cadastrar')
                spans = copy.deepcopy(revision['spans'])
                if interval is not None:
                    from clip_edits import validate_spans
                    validate_spans([interval], context['_source']['total_samples'])
                    spans = [[max(a, interval[0]), min(b, interval[1])] for a, b in spans
                             if max(a, interval[0]) < min(b, interval[1])]
                    if not spans: raise ValueError('Seleção fora dos trechos mantidos')
                mixed = None if multi else False
                selected_labels = set()
                covered, incomplete = [], False
                for segment in context.get('segments', []):
                    lo, hi = segment.get('start_sample'), segment.get('end_sample')
                    if not isinstance(lo, int) or not isinstance(hi, int) or not 0 <= lo < hi <= context['_source']['total_samples']:
                        incomplete = True
                        continue
                    intersections = [[max(a, lo), min(b, hi)] for a, b in spans if max(a, lo) < min(b, hi)]
                    if intersections:
                        label = segment.get('local_cluster_id') or segment.get('speaker_id')
                        if not label: incomplete = True
                        selected_labels.add(label)
                        covered.extend(intersections)
                if len(selected_labels - {None, ''}) > 1: mixed = True
                elif multi and len(selected_labels - {None, ''}) == 1 and not incomplete:
                    # Uma seleção inicial/integral não confirma voz única. Só
                    # âncoras de uma voz cobrindo todo o recorte removem a dúvida.
                    fully_covered = True
                    for start, end in spans:
                        cursor = start
                        for lo, hi in sorted(covered):
                            if lo <= cursor < hi: cursor = hi
                        if cursor < end: fully_covered = False
                    if fully_covered: mixed = False
                context['_revision']['spans'] = spans
                handle = self._store.acquire_source(context['source_id'])
            analyzer = self._ai
            version = getattr(analyzer, 'extractor_version', EXTRACTOR_VERSION)
            sample_key = profiles.dedupe_key(context['source_id'], spans, version)
            cached = profiles.snapshot()['samples'].get(sample_key)
            def run():
                if cached and cached.get('embedding') is not None:
                    return {'status': 'ready', 'embedding': cached['embedding'],
                            'quality': {**cached['quality'], 'mixed': mixed}, 'extraction_error': None}
                from clip_renderer import selected_source_file
                from ai_analyzer import load_audio_mono_16k
                with getattr(self, '_inference_lock', threading.RLock()):
                    with selected_source_file(context['_source'], context['_revision'], self._store.tmp_dir) as path:
                        audio, _ = load_audio_mono_16k(path)
                        quality = measure_quality(audio, mixed=mixed)
                        try:
                            embedding = analyzer.compute_embedding(audio)
                            error = None
                        except Exception as exc:
                            embedding, error = None, str(exc)
                        return {'status': 'ready', 'embedding': embedding.tolist() if embedding is not None else None,
                                'quality': quality, 'extraction_error': error}
            def apply(result):
                with profiles.transaction():
                    current = profiles.bank['actions'].get(action_id)
                    clip = self._store.get_clip(updated['id'])
                    if not current or current['state'] != 'pending' or not clip or clip.get('voice_action_id') != action_id or clip.get('speaker_id') != person or person not in self._store.state['speakers']:
                        return {'state': 'obsolete'}
                    removed_at = self._store.state['sources'][context['source_id']].get('voice_sample_removals', {}).get(sample_key, 0)
                    if removed_at > current.get('sample_epoch', 0):
                        current.update(state='sample_removed', error='Exemplo removido; cadastre novamente para usar este trecho.')
                        return {'state': 'obsolete'}
                    inserted = profiles.add_sample(person, context, spans, action_id=action_id,
                        extractor_version=version, embedding=result['embedding'], quality=result['quality'])
                    sample = inserted['sample']
                    current['sample_id'] = sample['sample_id']
                    current['owned_sample_id'] = sample['sample_id'] if inserted['created'] else None
                    current['previous_sample'] = inserted.get('previous_sample')
                    current['reference_learned'] = sample['eligibility'] == 'eligible'
                    current['state'] = 'learned' if current['reference_learned'] else 'pending_review'
                    current['error'] = ', '.join(sample['quality']['reasons']) or result.get('extraction_error')
                    return copy.deepcopy(current)
            def cleanup():
                self._store.release_source(handle)
                job = self._analysis_queue.get(action_id)
                if job and job['status'] in ('failed', 'cancelled'):
                    try:
                        with profiles.transaction():
                            action = profiles.bank['actions'].get(action_id)
                            if action and action['state'] == 'pending':
                                action.update(state=job['status'], error=(job.get('error') or {}).get('message'))
                    except OSError:
                        pass  # O job continua expondo erro, mesmo com disco indisponível.
                elif job and job.get('application', {}).get('state') == 'obsolete':
                    with profiles.transaction():
                        action = profiles.bank['actions'].get(action_id)
                        if action and action['state'] == 'pending': action['state'] = 'superseded'
            queued = self._analysis_queue.submit(run, apply=apply,
                priority=0 if explicit else 10,
                metadata={'stage': 'voice_learning', 'analysis_id': action_id, 'action_id': action_id, 'clip_id': updated['id']},
                cleanup=cleanup)
            if not queued['ok']:
                raise ValueError(queued.get('error', 'Fila de análise cheia'))
            return {**base, 'learning': queued}
        except Exception as exc:
            if handle: self._store.release_source(handle)
            with profiles.transaction():
                profiles.bank['actions'][action_id].update(state='failed', error=str(exc))
            return {**base, 'error': f'Pessoa corrigida; este trecho não foi usado como referência: {exc}'}

    def _enroll_voice(self, clip_id, speaker_id, speaker_name='', interval=None):
        rejected = self._reject_pending(clip_id, 'cadastrar voz')
        if rejected: return rejected
        clip = self._store.get_clip(clip_id)
        if not clip or not speaker_id or not speaker_id.strip() or speaker_id.startswith('local:'):
            return {'ok': False, 'error': 'Selecione uma pessoa global e um clipe válido',
                    'assignment_saved': False, 'reference_learned': False}
        previous = copy.deepcopy(clip)
        self._preserve_voice_evidence(previous)
        if speaker_name: self._store.update_speaker(speaker_id, speaker_name)
        # Atribuição humana persiste antes de qualquer tentativa de inferência.
        updated = self._store.add_or_update_clip({'id': clip_id, 'speaker_id': speaker_id, 'speaker_ids': [speaker_id],
            'field_provenance': {**clip.get('field_provenance', {}), 'speaker_id': 'human', 'speaker_ids': 'human'}})
        result = self._learn_correction(previous, updated, interval, explicit=True)
        return {'ok': True, **result, 'clip': copy.deepcopy(updated), 'speakers': self._store.snapshot()['speakers']}

    def undo_voice_correction(self, action_id, clip_id=None):
        profiles = self._voices()
        action = profiles.snapshot()['actions'].get(action_id)
        if not action: return {'ok': False, 'error': 'Correção não encontrada'}
        if clip_id is not None and action['clip_id'] != clip_id:
            return {'ok': False, 'error': 'Esta correção pertence a outro clipe'}
        if action['state'] == 'deleted_person': return {'ok': False, 'error': 'A pessoa desta correção foi excluída'}
        if action.get('analysis_id'): self._analysis_queue.cancel(action['analysis_id'])
        with profiles.transaction():
            current = profiles.bank['actions'][action_id]
            clip = self._store.get_clip(current['clip_id'])
            if current['state'] == 'undone': return {'ok': True}
            if clip and clip.get('voice_action_id') != action_id:
                return {'ok': False, 'error': 'Há uma correção mais recente; desfaça-a primeiro'}
            sample = profiles.bank['samples'].get(current.get('owned_sample_id'))
            if sample and sample['manual_action_id'] == action_id and sample['speaker_id'] == current['speaker_id']:
                profiles.remove_sample(sample['sample_id'])
            replaced = current.get('previous_sample')
            if replaced:
                sample = profiles.bank['samples'].get(replaced['sample_id'])
                if sample and sample['manual_action_id'] == action_id:
                    profiles.bank['samples'][replaced['sample_id']] = copy.deepcopy(replaced)
                    profiles._rebuild([replaced['speaker_id']])
            withdrawn = current.get('withdrawn_sample')
            if withdrawn and withdrawn['sample_id'] not in profiles.bank['samples']:
                profiles.bank['samples'][withdrawn['sample_id']] = copy.deepcopy(withdrawn)
                self._store.state['source_references']['voice:' + withdrawn['sample_id']] = withdrawn['source_id']
                profiles._rebuild([withdrawn['speaker_id']])
            if clip:
                clip.update(speaker_id=current['previous_speaker_id'], speaker_ids=current['previous_speaker_ids'],
                            voice_action_id=current['previous_action_id'])
                for key in ('speaker_id', 'speaker_ids'):
                    clip.setdefault('field_versions', {})[key] = clip.get('field_versions', {}).get(key, 0) + 1
                    clip.setdefault('field_provenance', {})[key] = 'human'
                draft = self._store.state['edit_drafts'].get(clip['id'])
                if draft:
                    for key in ('speaker_id', 'speaker_ids'):
                        draft['fields'][key] = copy.deepcopy(clip[key])
                        draft.setdefault('field_versions', {})[key] = draft.get('field_versions', {}).get(key, 0) + 1
                        draft.setdefault('field_provenance', {})[key] = 'human'
                    draft['generation'] = draft.get('generation', 0) + 1
            current.update(state='undone', reference_learned=False)
        return {'ok': True, 'clip': copy.deepcopy(clip)}

    def cancel_voice_learning(self, action_id, clip_id=None):
        profiles = self._voices()
        action = profiles.snapshot()['actions'].get(action_id)
        if not action: return {'ok': False}
        if clip_id is not None and action['clip_id'] != clip_id:
            return {'ok': False, 'error': 'Este aprendizado pertence a outro clipe'}
        if action.get('analysis_id'): self._analysis_queue.cancel(action['analysis_id'])
        with profiles.transaction():
            current = profiles.bank['actions'][action_id]
            if current['state'] == 'pending': current['state'] = 'cancelled'
        return {'ok': True}

    def remove_voice_sample(self, sample_id):
        return {'ok': self._voices().remove_sample(sample_id)}

    def reassign_voice_sample(self, sample_id, speaker_id):
        try:
            return {'ok': True, 'sample': self._voices().reassign_sample(sample_id, speaker_id)}
        except (KeyError, ValueError) as exc:
            return {'ok': False, 'error': str(exc)}

    def preview_voice_sample(self, sample_id):
        handle = None
        try:
            with self._store._lock:
                context = self._voices().sample_context(sample_id)
                handle = self._store.acquire_source(context['source_id'])
            def release(*args): self._store.release_source(handle)
            context['_release_source'] = release
            play_id = self._audio.play_clip(clip=context,
                settings={**self._store.snapshot()['settings'], 'overlap_audio': True},
                monitor_only=True, on_finish=release, on_tick=None)
            if not play_id: raise ValueError('Nenhum monitor disponível')
            return {'ok': True, 'play_id': play_id}
        except (KeyError, OSError, RuntimeError, ValueError) as exc:
            if handle: self._store.release_source(handle)
            return {'ok': False, 'error': str(exc)}

    def request_voice_review(self, clip_ids=None):
        """Reutiliza apenas embeddings de análises; jamais os cadastra como confirmados."""
        profiles = self._voices()
        proposals, missing = [], []
        with profiles.transaction():
            for clip in self._store.state['clips']:
                if clip_ids is not None and clip['id'] not in clip_ids: continue
                if clip.get('field_provenance', {}).get('speaker_id') == 'human': continue
                draft = self._store.state['edit_drafts'].get(clip['id'])
                if draft and draft.get('field_provenance', {}).get('speaker_id') == 'human': continue
                segments = clip.get('segments', [])
                if not segments or any(segment.get('voice_embedding') is None for segment in segments):
                    missing.append(clip['id']); continue
                matches = []
                for segment in segments:
                    if segment.get('voice_embedding') is None: continue
                    decision = profiles.match(segment['voice_embedding'], segment.get('extractor_version', 'unknown'))
                    matches.append({**copy.deepcopy(segment), 'speaker_id': decision['speaker_id'], 'identity': decision})
                if not matches:
                    missing.append(clip['id']); continue
                ident = uuid.uuid4().hex
                proposal = {'proposal_id': ident, 'clip_id': clip['id'], 'revision_id': clip.get('revision_id'),
                    'field_versions': copy.deepcopy(clip.get('field_versions', {})),
                    'profile_versions': {k: p['profile_version'] for k, p in profiles.bank['profiles'].items()},
                    'matching_version': profiles.bank.get('matching_version', 0),
                    'previous_speaker_id': clip.get('speaker_id'), 'segments': matches,
                    'speaker_id': next((m['speaker_id'] for m in matches if m['speaker_id']), '')}
                profiles.bank['review_proposals'][ident] = proposal
                proposals.append(copy.deepcopy(proposal))
        return {'ok': True, 'proposals': proposals, 'requires_speaker_analysis': missing}

    def apply_voice_review(self, proposal_ids):
        profiles = self._voices()
        applied, stale = [], []
        with profiles.transaction():
            versions = {k: p['profile_version'] for k, p in profiles.bank['profiles'].items()}
            for ident in proposal_ids:
                proposal = profiles.bank['review_proposals'].get(ident)
                clip = self._store.get_clip(proposal['clip_id']) if proposal else None
                draft = self._store.state['edit_drafts'].get(clip['id']) if clip else None
                if not clip or proposal['matching_version'] != profiles.bank.get('matching_version', 0) or proposal['profile_versions'] != versions or proposal['revision_id'] != clip.get('revision_id') or proposal['field_versions'] != clip.get('field_versions', {}) or clip.get('field_provenance', {}).get('speaker_id') == 'human' or draft:
                    stale.append(ident); continue
                clip['speaker_id'] = proposal['speaker_id']
                clip['speaker_ids'] = list(dict.fromkeys(s['speaker_id'] for s in proposal['segments'] if s['speaker_id']))
                clip['segments'] = copy.deepcopy(proposal['segments'])
                for key in ('speaker_id', 'speaker_ids', 'segments'):
                    clip.setdefault('field_versions', {})[key] = clip.get('field_versions', {}).get(key, 0) + 1
                    clip.setdefault('field_provenance', {})[key] = 'voice_review:' + ident
                clip['speaker_matching_stale'] = False
                applied.append(clip['id'])
                del profiles.bank['review_proposals'][ident]
        return {'ok': True, 'applied': applied, 'obsolete': stale}
