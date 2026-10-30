"""Fachada de sequências para o bridge; erros operacionais são dados legíveis."""
import copy
import uuid
import threading

from sound_sequences import MAX_BLOCKS, validate_document


class SequenceAPI:
    def get_sequences(self):
        sequences = []
        for ident, document in self._store.snapshot()['sequences'].items():
            try:
                sequences.append(self._store.sequence_details(ident))
            except ValueError as exc:
                sequences.append({'document': document, 'error': str(exc), 'new_revisions': []})
        return {'ok': True, 'sequences': sequences, 'clips': self._store.sequence_clip_choices(),
                'capabilities': self.get_playback_capabilities(), 'status': self._sequence_runner.snapshot()}

    def save_sequence(self, document):
        try:
            saved = self._store.save_sequence(document)
            self._hotkeys.refresh(self._public_clips(self._store.snapshot()['clips']), self._store.snapshot()['settings'])
            return {'ok': True, 'document': saved, **self._store.sequence_details(saved['id'])}
        except (ValueError, TypeError, KeyError, OSError) as exc:
            return {'ok': False, 'error': str(exc)}

    def delete_sequence(self, sequence_id):
        try:
            ok = self._store.delete_sequence(sequence_id)
            self._hotkeys.refresh(self._public_clips(self._store.snapshot()['clips']), self._store.snapshot()['settings'])
            return {'ok': ok}
        except (ValueError, OSError) as exc:
            return {'ok': False, 'error': str(exc)}

    def pin_sequence_clip(self, clip_id):
        try:
            clip = self._store.get_clip(clip_id)
            if not clip or clip.get('pending'):
                raise ValueError('Selecione um clipe salvo da biblioteca')
            if not clip.get('revision_id'):
                if clip_id in self._store.snapshot()['edit_drafts']:
                    raise ValueError('Salve a edição do clipe antes de adicioná-lo à sequência')
                self._store.begin_edit(clip_id)
                clip = self._store.commit_edit(clip_id)
            revision = self._store.snapshot()['revisions'][clip['revision_id']]
            from audio_variants import output_samples
            block = {'id': str(uuid.uuid4()), 'type': 'play', 'clip_id': clip_id,
                     'revision_id': clip['revision_id'], 'volume': clip.get('volume', 1),
                     'interval': [0, output_samples(sum(end-start for start,end in revision['spans']), revision.get('variant', {}))]}
            self._store.resolve_sequence_occurrence(block)
            return {'ok': True, 'occurrence': block}
        except (ValueError, TypeError, KeyError, OSError) as exc:
            return {'ok': False, 'error': str(exc)}

    def validate_sequence(self, document):
        try:
            return {'ok': True, **validate_document(document, self._store.resolve_sequence_occurrence)}
        except (ValueError, TypeError, KeyError) as exc:
            return {'ok': False, 'error': str(exc)}

    def start_sequence(self, sequence_id, replace=False):
        try:
            request = self._operation_request()
        except ValueError as exc:
            return {'ok': False, 'error': str(exc)}
        return self._start_sequence_request(sequence_id, replace, request)

    def _start_sequence_request(self, sequence_id, replace, request):
        try:
            settings, monitor_only, epoch = request
            document = self._store.get_sequence(sequence_id)
            if not document:
                raise ValueError('Sequência não encontrada; salve antes de iniciar')
            status = self._sequence_runner.start(document, settings, monitor_only=monitor_only,
                                                 replace=bool(replace), expected_epoch=epoch)
            return {'ok': True, 'status': status}
        except (ValueError, TypeError, KeyError, OSError) as exc:
            return {'ok': False, 'error': str(exc)}

    def preview_sequence(self, document, block_id=None, replace=False):
        try:
            settings, _, epoch = self._operation_request(True)
            if not isinstance(document, dict):
                raise ValueError('Documento inválido')
            preview = copy.deepcopy(document)
            if block_id:
                pending, seen, selected = list(preview.get('blocks') or []), set(), None
                while pending:
                    block = pending.pop()
                    if not isinstance(block, dict) or id(block) in seen or len(seen) >= MAX_BLOCKS:
                        raise ValueError('Estrutura de blocos inválida')
                    seen.add(id(block))
                    if block.get('id') == block_id:
                        if selected is not None:
                            raise ValueError('ID de ocorrência duplicado')
                        selected = block
                    for branch in block.get('branches', []):
                        pending.extend(branch)
                if not selected:
                    raise ValueError('Bloco de prévia não encontrado')
                preview['blocks'] = [selected]
            validate_document(preview, self._store.resolve_sequence_occurrence)
            status = self._sequence_runner.start(preview, settings,
                monitor_only=True, replace=bool(replace), expected_epoch=epoch)
            return {'ok': True, 'status': status}
        except (ValueError, TypeError, KeyError, OSError) as exc:
            return {'ok': False, 'error': str(exc)}

    def get_sequence_status(self):
        return {'ok': True, 'status': self._sequence_runner.snapshot()}

    def advance_sequence(self, execution_id, repeat=False):
        advanced = self._sequence_runner.advance(execution_id, repeat=bool(repeat))
        return {'ok': advanced, 'status': self._sequence_runner.snapshot()}

    def stop_sequence(self, execution_id=None):
        self._sequence_runner.stop(execution_id)
        return {'ok': True, 'status': self._sequence_runner.snapshot()}

    def _trigger_sequence_hotkey(self, sequence_id):
        status = self._sequence_runner.snapshot()
        if status.get('sequence_id') == sequence_id and status['state'] == 'manual':
            self.advance_sequence(status['execution_id'])
        elif status['state'] in ('idle', 'completed', 'cancelled', 'failed'):
            try:
                request = self._operation_request()
            except ValueError as exc:
                self._notify_ui('toast', {'type': 'warning', 'message': str(exc)})
                return
            def start():
                result = self._start_sequence_request(sequence_id, False, request)
                if not result['ok']:
                    self._notify_ui('toast', {'type': 'warning', 'message': result['error']})
            threading.Thread(target=start, daemon=True, name='SequenceHotkeyStart').start()
