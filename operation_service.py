"""Operação intencional: identidades por disparo e contexto de saída congelado."""
import threading
import uuid
import copy
from collections import OrderedDict

from sound_collections import binding_conflicts, normalize_binding, ShuffleGroups
from sequence_runner import TERMINAL


class OperationAPI:
    def _init_operation(self):
        self._operation_lock = threading.RLock()
        self._operation_rehearsal = False
        self._operation_transition = False
        self._operation_instances = {}
        self._operation_keys = {}
        self._operation_released = OrderedDict()
        self._operation_route_fault = ''
        self._operation_failure_seen = 0
        self._shuffle_groups = ShuffleGroups()
        self._variant_previews = {}

    def _operation_request(self, monitor_only=False):
        with self._operation_lock:
            if self._operation_transition:
                raise ValueError('Mudança de modo em andamento; aguarde a confirmação')
            settings = self._check_operation_routes(monitor_only=bool(monitor_only))
            monitor_only = bool(monitor_only or self._operation_rehearsal)
            if self._operation_route_fault:
                raise ValueError(self._operation_route_fault)
            if self._operation_rehearsal:
                self._validate_rehearsal(settings)
            return settings, monitor_only, self._audio.capture_playback_epoch()

    @staticmethod
    def _validate_rehearsal(settings):
        monitor = settings.get('monitor_device_id')
        if monitor is None:
            raise ValueError('Selecione um monitor para ensaiar')
        if monitor == settings.get('cable_device_id'):
            raise ValueError('Não existe isolamento: monitor e transmissão usam a mesma saída')
        if float(settings.get('monitor_volume', 0)) <= .001:
            raise ValueError('Monitor está mudo; ajuste o volume para ensaiar')

    def set_rehearsal(self, enabled):
        transition = False
        try:
            if type(enabled) is not bool:
                raise ValueError('Modo de ensaio inválido')
            with self._operation_lock:
                if self._operation_transition:
                    raise ValueError('Mudança de modo em andamento; aguarde a confirmação')
                settings = self._store.snapshot()['settings']
                if enabled:
                    self._validate_rehearsal(settings)
                    if not self._audio._output_route_usable(settings['monitor_device_id']):
                        raise ValueError('Monitor indisponível; selecione a saída novamente')
                if enabled == self._operation_rehearsal:
                    return {'ok': True, 'rehearsal': enabled}
                self._operation_transition = transition = True
            # Esperar a parada sem lock da API: callbacks podem consultar/recusar
            # novas ações enquanto a transição bloqueia sua admissão.
            self._audio.stop_all(fade_ms=0, wait=True)
            with self._operation_lock:
                self._operation_rehearsal = enabled
            return {'ok': True, 'rehearsal': enabled}
        except (ValueError, TypeError) as exc:
            return {'ok': False, 'error': str(exc)}
        finally:
            if transition:
                with self._operation_lock:
                    self._operation_transition = False

    def _operation_refresh_hotkeys(self):
        snap = self._store.snapshot()
        self._hotkeys.refresh(self._public_clips(snap['clips']), snap['settings'])

    def get_collections(self):
        snap = self._store.snapshot()
        active = snap['collections'].get(snap['settings'].get('active_collection_id'))
        return {'ok': True, 'collections': list(snap['collections'].values()),
                'active_collection_id': snap['settings'].get('active_collection_id'),
                'clips': self._public_clips(snap['clips']), 'sequences': list(snap['sequences'].values()),
                'conflicts': binding_conflicts(snap, active)}

    def save_collection(self, document):
        try:
            saved = self._store.save_collection(document)
            self._operation_refresh_hotkeys()
            return {'ok': True, 'document': saved}
        except (ValueError, TypeError, KeyError, OSError) as exc:
            return {'ok': False, 'error': str(exc)}

    def delete_collection(self, collection_id):
        try:
            result = self._store.delete_collection(collection_id)
            # Execuções e key-up retêm identidade; excluir layout não altera áudio em curso.
            self._operation_refresh_hotkeys()
            return {'ok': result}
        except (ValueError, OSError) as exc:
            return {'ok': False, 'error': str(exc)}

    def activate_collection(self, collection_id):
        try:
            snap = self._store.snapshot()
            collection = snap['collections'].get(collection_id)
            if not collection:
                raise ValueError('Coleção não encontrada')
            conflicts = binding_conflicts(snap, collection)
            if conflicts:
                raise ValueError('Atalho em conflito: ' + '; '.join(c['hotkey'] for c in conflicts))
            self._store.update_settings({'active_collection_id': collection_id})
            self._operation_refresh_hotkeys()
            return {'ok': True, 'active_collection_id': collection_id}
        except (ValueError, OSError) as exc:
            return {'ok': False, 'error': str(exc)}

    def _active_collection(self):
        snap = self._store.snapshot()
        return snap['collections'].get(snap['settings'].get('active_collection_id'))

    def _dispatch_collection_hotkey(self, binding, phase='press', repeat=False, token=None):
        key = normalize_binding(binding)
        if phase == 'release':
            return self.trigger_collection_pad('', '', phase='release', token=token)
        collection = self._active_collection()
        if not collection:
            return {'ok': False, 'error': 'Nenhuma coleção ativa'}
        snap = self._store.snapshot()
        if any(c['hotkey'] == key for c in binding_conflicts(snap, collection)):
            return {'ok': False, 'error': 'Atalho em conflito: ' + key}
        pad = next((p for p in collection['pads'] if normalize_binding(p.get('hotkey', '')) == key), None)
        if not pad:
            return {'ok': False, 'error': 'Atalho não encontrado'}
        result = self._trigger_collection_pad(collection['id'], pad['id'], repeat=repeat,
                                              token=token, background=True)
        if not result['ok']:
            self._notify_ui('toast', {'type': 'warning', 'message': result['error']})
        return result

    def _operation_active(self, instance):
        if instance['cancel'].is_set():
            return False
        if instance['state'] == 'preparing':
            return True
        if instance['kind'] == 'sequence':
            status = self._sequence_runner.snapshot()
            return status.get('execution_id') == instance['execution_id'] and status['state'] not in TERMINAL
        return any(self._audio.get_session(pid) is not None for pid in instance.get('play_ids', []))

    def trigger_collection_pad(self, collection_id, pad_id, phase='press', repeat=False, token=None, repeat_last=False):
        return self._trigger_collection_pad(collection_id, pad_id, phase, repeat, token, repeat_last=repeat_last)

    def _trigger_collection_pad(self, collection_id, pad_id, phase='press', repeat=False, token=None, background=False, repeat_last=False):
        instance = None
        try:
            if phase not in ('press', 'release'):
                raise ValueError('Fase de disparo inválida')
            if phase == 'release':
                with self._operation_lock:
                    held = self._operation_keys.pop(token, None)
                    if token and held is None:
                        # Bridge pode entregar key-up antes da chamada de key-down.
                        # Cada gesto da UI/teclado tem UUID; nunca cancela o gesto seguinte.
                        self._operation_released[token] = True
                        if len(self._operation_released) > 4096:
                            self._operation_released.popitem(last=False)
                if held and held['hold']:
                    self.stop_collection_pad(held['execution_id'])
                return {'ok': True}
            with self._operation_lock:
                if token in self._operation_released:
                    self._operation_released.pop(token)
                    return {'ok': True, 'ignored': True, 'cancelled': True}
                if repeat or (token and token in self._operation_keys):
                    return {'ok': True, 'ignored': True}
                collection = self._store.get_collection(collection_id)
                if not collection or self._store.snapshot()['settings'].get('active_collection_id') != collection_id:
                    raise ValueError('Coleção não está ativa; selecione-a antes de disparar')
                pad = next((p for p in collection['pads'] if p['id'] == pad_id), None)
                if not pad:
                    raise ValueError('Botão não encontrado')
                if pad['hold'] and not token:
                    token = 'hold:' + str(uuid.uuid4())
                settings, monitor_only, epoch = self._operation_request()
                active = [i for i in self._operation_instances.values() if self._operation_active(i)]
                same = next((i for i in active if i['collection_id']==collection_id and i['pad_id']==pad_id), None)
                if same:
                    if token:
                        self._operation_keys[token] = same
                    if same['kind'] == 'sequence':
                        status = self._sequence_runner.snapshot()
                        if status.get('execution_id') == same['execution_id'] and status['state'] == 'manual':
                            return {**self.advance_sequence(same['execution_id']), 'execution_id': same['execution_id']}
                    if pad['retrigger'] == 'ignore':
                        return {'ok': True, 'ignored': True, 'execution_id': same['execution_id']}
                    if pad['retrigger'] == 'stop':
                        self.stop_collection_pad(same['execution_id'])
                        return {'ok': True, 'stopped': True, 'execution_id': same['execution_id']}
                if pad['target']['type'] == 'shuffle':
                    chosen = self._shuffle_groups.choose(collection_id + ':' + pad_id,
                        pad['target']['items'], pad.get('cooldown_ms', 0), repeat_last=repeat_last)
                    pad = {**copy.deepcopy(pad), 'target': chosen}
                elif repeat_last:
                    raise ValueError('Repetir escolha exige um grupo aleatório')
                if same:
                    self.stop_collection_pad(same['execution_id'])
                if pad['group'] == 'speech':
                    for other in active:
                        if other['group'] == 'speech' and other['monitor_only'] == monitor_only:
                            self.stop_collection_pad(other['execution_id'])
                ident = str(uuid.uuid4())
                instance = {'execution_id': ident, 'collection_id': collection_id, 'pad_id': pad_id,
                            'kind': pad['target']['type'], 'group': pad['group'], 'hold': pad['hold'],
                            'loop': pad['loop'], 'fade_ms': pad.get('fade_ms', 0), 'cancel': threading.Event(),
                            'state': 'preparing', 'monitor_only': monitor_only, 'play_ids': []}
                instance['chosen_target'] = copy.deepcopy(pad['target'])
                self._operation_instances[ident] = instance
                if token:
                    self._operation_keys[token] = instance
                # Registrar antes de qualquer I/O: soltar/Stop All cancela inclusive preparação.
                def cancel():
                    instance['cancel'].set()
                    self._sequence_runner._wake.set()
                self._audio.register_group_owner(ident, cancel, expected_epoch=epoch)
            if background:
                def prepare():
                    result = self._prepare_operation_pad(instance, pad, settings, monitor_only, epoch, token)
                    if not result['ok'] and not result.get('cancelled'):
                        self._notify_ui('toast', {'type': 'warning', 'message': result['error']})
                threading.Thread(target=prepare, daemon=True, name='CollectionPadPrepare').start()
                return {'ok': True, 'execution_id': ident, 'token': token, 'state': 'preparing', 'chosen_target': instance['chosen_target']}
            return self._prepare_operation_pad(instance, pad, settings, monitor_only, epoch, token)
        except (ValueError, TypeError, KeyError, OSError, RuntimeError) as exc:
            if instance:
                cancelled = instance['cancel'].is_set()
                instance['cancel'].set()
                instance['state'] = 'cancelled'
                self._audio.stop_group(instance['execution_id'], fade_ms=0)
            return {'ok': False, 'error': str(exc), 'cancelled': cancelled if instance else False}

    def _prepare_operation_pad(self, instance, pad, settings, monitor_only, epoch, token):
        ident = instance['execution_id']
        try:
            if instance['cancel'].is_set():
                raise ValueError('Disparo cancelado antes da preparação')
            if instance['kind'] == 'sequence':
                document = self._store.get_sequence(pad['target']['id'])
                status = self._sequence_runner.start(document, settings, monitor_only=monitor_only,
                    expected_epoch=epoch, execution_id=ident, cancel_event=instance['cancel'], loop=pad['loop'])
                instance['state'] = status['state']
                return {'ok': True, 'execution_id': ident, 'token': token, 'status': status, 'chosen_target': instance['chosen_target']}
            clip = self._store.render_context(pad['target']['id'])
            if not clip:
                raise ValueError('Clipe não encontrado')
            if clip.get('source_id'):
                handle = self._store.acquire_source(clip['source_id'])
                clip['_release_source'] = lambda: self._store.release_source(handle)
            prepared = self._audio.prepare_group(ident, [{'clip': clip}], settings, monitor_only=monitor_only)
            for voice in prepared.voices:
                voice.set_loop(pad['loop'])
            instance['play_ids'] = [v.play_id for v in prepared.voices]
            if instance['cancel'].is_set():
                raise ValueError('Disparo cancelado')
            self._audio.start_group(prepared)
            instance['state'] = 'playing'
            self._store.increment_play_count(pad['target']['id'])
            return {'ok': True, 'execution_id': ident, 'play_id': instance['play_ids'][0], 'token': token, 'chosen_target': instance['chosen_target']}
        except (ValueError, TypeError, KeyError, OSError, RuntimeError) as exc:
            if instance:
                cancelled = instance['cancel'].is_set()
                instance['cancel'].set()
                instance['state'] = 'cancelled'
                self._audio.stop_group(instance['execution_id'], fade_ms=0)
            return {'ok': False, 'error': str(exc), 'cancelled': cancelled if instance else False}

    def stop_collection_pad(self, execution_id, fade=False):
        with self._operation_lock:
            instance = self._operation_instances.get(execution_id)
            if not instance:
                return {'ok': False, 'error': 'Execução não encontrada'}
            instance['cancel'].set()
        fade_ms = instance['fade_ms'] if fade else 0
        if instance['kind'] == 'sequence':
            self._sequence_runner.stop(execution_id, fade_ms=fade_ms)
        self._audio.stop_group(execution_id, fade_ms=fade_ms)
        return {'ok': True}

    def _check_operation_routes(self, monitor_only=False):
        settings = self._store.snapshot()['settings']
        failure_serial, failure_message = self._audio.output_failure()
        roles = ['monitor'] if self._operation_rehearsal or monitor_only else ['cable', 'monitor']
        unavailable = [role for role in roles if settings.get(role+'_device_id') is not None
                       and not self._audio._output_route_usable(settings[role+'_device_id'])]
        if (unavailable or failure_serial != self._operation_failure_seen) and not self._operation_route_fault:
            detail = ', '.join(unavailable) if unavailable else failure_message
            self._operation_route_fault = 'Saída indisponível (' + detail + '); selecione a saída novamente. Nenhum som será retomado.'
            self._audio.stop_all(fade_ms=0)
        return settings

    def get_operation_status(self):
        with self._operation_lock:
            settings = self._check_operation_routes()
            instances = []
            for ident, instance in list(self._operation_instances.items()):
                if self._operation_active(instance):
                    instances.append({k: v for k,v in instance.items() if k != 'cancel'})
                elif instance['state'] != 'preparing':
                    self._audio.unregister_group_owner(ident)
                    self._operation_instances.pop(ident, None)
            return {'ok': True, 'rehearsal': self._operation_rehearsal, 'instances': instances,
                    'shuffle_choices': self._shuffle_groups.snapshot(),
                    'route_error': self._operation_route_fault,
                    'cable_device_id': settings.get('cable_device_id'), 'monitor_device_id': settings.get('monitor_device_id'),
                    'cable_device_name': settings.get('cable_device_name'), 'monitor_device_name': settings.get('monitor_device_name'),
                    'isolated': settings.get('monitor_device_id') is not None and settings.get('monitor_device_id') != settings.get('cable_device_id'),
                    'sequence': self._sequence_runner.snapshot()}
