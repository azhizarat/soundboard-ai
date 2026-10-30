"""Executor serial no backend. Callbacks de áudio apenas publicam eventos."""
import copy
import queue
import threading
import time
import uuid

from sound_sequences import validate_document, walk_blocks

TERMINAL = frozenset(('idle', 'completed', 'cancelled', 'failed'))


class SequenceRunner:
    def __init__(self, store, engine, clock=time.monotonic, background=True):
        self.store, self.engine, self.clock = store, engine, clock
        self._lock = threading.RLock()
        self._commands = threading.RLock()
        self._wake = threading.Event()
        self._closed = threading.Event()
        self._events = queue.SimpleQueue()
        self._run = None
        self._thread = None
        self._background = background

    @staticmethod
    def _lane(blocks, index=0):
        return {'blocks': blocks, 'index': index, 'waiting': None, 'deadline': None, 'children': None}

    def _cancel_signal(self, run):
        # Sem store, decode, chamadas ao engine ou locks cruzados na thread de áudio.
        run['cancel'].set()
        self._wake.set()

    def enqueue_completion(self, execution_id, event):
        self._events.put((execution_id, event))
        self._wake.set()

    def start(self, document, settings, monitor_only=False, replace=False, expected_epoch=None,
              execution_id=None, cancel_event=None, loop=False):
        request_epoch = self.engine.capture_playback_epoch() if expected_epoch is None else expected_epoch
        with self._commands:
            with self._lock:
                active = self._run and self._run['state'] not in TERMINAL
            if active and not replace:
                raise ValueError('Há uma sequência ativa; pare ou reinicie explicitamente')
            if active:
                self.stop()
            if self._closed.is_set():
                raise ValueError('Executor encerrado')
            if not self.engine.playback_capabilities()['groups']:
                raise ValueError(self.engine.playback_capabilities()['unavailable_reason'])
            # Validação antes do deepcopy também reconhece ciclos do documento Python.
            details = validate_document(document, self.store.resolve_sequence_occurrence)
            frozen, config = copy.deepcopy(document), copy.deepcopy(settings)
            run = {'execution_id': execution_id or str(uuid.uuid4()), 'document': frozen, 'settings': config,
                   'monitor_only': bool(monitor_only), 'state': 'preparing', 'error': None,
                   'cancel': cancel_event if cancel_event is not None else threading.Event(), 'root': self._lane(frozen['blocks']),
                   'loop': bool(loop), 'request_epoch': request_epoch,
                   'prepared': {}, 'plays': {}, 'parallel': {}, 'completed': set(), **details}
            with self._lock:
                self._run = run
            eid = run['execution_id']
            try:
                if execution_id is None:
                    self.engine.register_group_owner(eid, lambda: self._cancel_signal(run), expected_epoch=request_epoch)
                elif run['cancel'].is_set() or self.engine.capture_playback_epoch() != request_epoch:
                    raise ValueError('Solicitação de coleção cancelada')
                self._prepare(run)
                if run['cancel'].is_set():
                    raise ValueError('Preparação cancelada')
                with self._lock:
                    run['state'] = 'playing'
                self.pump()
                if self._background and (self._thread is None or not self._thread.is_alive()):
                    self._thread = threading.Thread(target=self._worker, daemon=True, name='SoundSequenceExecutor')
                    self._thread.start()
                self._wake.set()
                return self.snapshot()
            except Exception as exc:
                with self._lock:
                    run['error'] = str(exc)
                    run['state'] = 'cancelled' if run['cancel'].is_set() else 'failed'
                    run['cancel'].set()
                self.engine.stop_group(eid, fade_ms=0)
                self.engine.unregister_group_owner(eid)
                raise

    def _prepare(self, run):
        """Prepara TODOS os sons; primeiros sons de ramos compartilham um lote."""
        if run['cancel'].is_set() or self.engine.capture_playback_epoch() != run['request_epoch']:
            run['cancel'].set()
            raise ValueError('Preparação cancelada')
        batches, assigned = [], set()
        for block in walk_blocks(run['document']['blocks']):
            if block['type'] != 'parallel':
                continue
            firsts, indices = [], []
            for branch in block['branches']:
                delay = 0
                first = None
                for index, item in enumerate(branch):
                    if item['type'] == 'wait':
                        delay += item['ms']
                    else:
                        first = index
                        firsts.append((item, round(delay * 48)))
                        assigned.add(item['id'])
                        break
                indices.append(first)
            run['parallel'][block['id']] = indices
            if firsts:
                batches.append((block['id'], firsts))
        for block in walk_blocks(run['document']['blocks']):
            if block['type'] == 'play' and block['id'] not in assigned:
                batches.append((block['id'], [(block, 0)]))
        for key, items in batches:
            if run['cancel'].is_set():
                raise ValueError('Preparação cancelada')
            occurrences, handles = [], []
            try:
                # Resolve+lease sob o mesmo lock: remoção nunca passa entre ambos.
                with self.store._lock:
                    for block, offset in items:
                        context = self.store.resolve_sequence_occurrence(block)
                        handle = self.store.acquire_source(context['_source']['id'])
                        handles.append(handle)
                        context['_release_source'] = lambda h=handle: self.store.release_source(h)
                        occurrences.append({'clip': context, 'offset_samples': offset})
                prepared = self.engine.prepare_group(run['execution_id'], occurrences, run['settings'],
                    monitor_only=run['monitor_only'],
                    on_complete=lambda event, eid=run['execution_id']: self.enqueue_completion(eid, event))
                run['prepared'][key] = prepared
                for (block, _), voice in zip(items, prepared.voices):
                    run['plays'][block['id']] = voice.play_id
            except Exception:
                for handle in handles:
                    self.store.release_source(handle)
                raise

    def _step_lane(self, run, lane, now, starts):
        reference_time = now
        while lane['index'] < len(lane['blocks']):
            if lane['waiting'] == 'manual':
                return
            if lane['waiting'] == 'audio':
                current = lane['blocks'][lane['index']]
                if run['plays'][current['id']] not in run['completed']:
                    return
                lane['waiting'] = None
                lane['index'] += 1
                continue
            if lane['waiting'] == 'wait':
                if now + 1e-9 < lane['deadline']:
                    return
                reference_time = lane['deadline']
                lane['waiting'] = None
                lane['index'] += 1
                continue
            if lane['children'] is not None:
                for child in lane['children']:
                    self._step_lane(run, child, now, starts)
                if any(child['index'] < len(child['blocks']) for child in lane['children']):
                    return
                lane['children'] = None
                lane['index'] += 1
                continue
            block = lane['blocks'][lane['index']]
            kind = block['type']
            if kind == 'play':
                lane['waiting'] = 'audio'
                starts.append(run['prepared'][block['id']])
                return
            if kind == 'manual':
                lane['waiting'] = 'manual'
                return
            if kind == 'wait':
                lane['waiting'] = 'wait'
                lane['deadline'] = reference_time + block['ms'] / 1000
                continue
            lane['children'] = []
            for branch, first in zip(block['branches'], run['parallel'][block['id']]):
                child = self._lane(branch, 0 if first is None else first)
                if first is not None:
                    child['waiting'] = 'audio'
                lane['children'].append(child)
            if block['id'] in run['prepared']:
                starts.append(run['prepared'][block['id']])
            # Esperas de ramos sem áudio começam na mesma referência monotônica.

    def pump(self):
        """Uma iteração determinística; testes avançam apenas o relógio injetado."""
        with self._commands:
            starts, finish = [], None
            with self._lock:
                run = self._run
                if not run:
                    return
                while not self._events.empty():
                    eid, event = self._events.get()
                    if eid != run['execution_id'] or event.group_id != eid or event.play_id not in run['plays'].values():
                        continue
                    if event.reason == 'completed':
                        run['completed'].add(event.play_id)
                    elif event.reason in ('cancelled', 'failed'):
                        if event.reason == 'failed' or run['state'] != 'failed':
                            run['state'], run['error'] = event.reason, event.error
                        run['cancel'].set()
                if run['state'] == 'preparing' and not run['cancel'].is_set():
                    return
                if run['cancel'].is_set():
                    if run['state'] not in ('cancelled', 'failed'):
                        run['state'] = 'cancelled'
                    if not run.get('cleaned'):
                        run['cleaned'] = True
                        finish = 'stop'
                elif run['state'] not in TERMINAL:
                    self._step_lane(run, run['root'], self.clock(), starts)
                    root = run['root']
                    if root['index'] == len(root['blocks']):
                        # Uma iteração intermediária conserva dono e estado ativo.
                        run['state'] = 'preparing' if run['loop'] else 'completed'
                        finish = 'loop' if run['loop'] else 'complete'
                    else:
                        lanes = root['children'] or [root]
                        waiting = [lane['waiting'] for lane in lanes if lane['index'] < len(lane['blocks'])]
                        run['state'] = 'manual' if 'manual' in waiting else 'playing' if 'audio' in waiting else 'waiting'
            # Nenhum lock de estado atravessa engine/start/stop, inclusive callbacks imediatos.
            if finish:
                if finish == 'loop':
                    try:
                        with self._lock:
                            run.update(root=self._lane(run['document']['blocks']),
                                       prepared={}, plays={}, parallel={}, completed=set())
                        self._prepare(run)
                        with self._lock:
                            if run['cancel'].is_set():
                                raise ValueError('Repetição cancelada')
                            run['state'] = 'playing'
                        self._wake.set()
                    except Exception as exc:
                        with self._lock:
                            run['state'], run['error'] = 'cancelled' if run['cancel'].is_set() else 'failed', str(exc)
                            run['cancel'].set()
                            run['cleaned'] = True
                        self.engine.stop_group(run['execution_id'], fade_ms=0)
                        self.engine.unregister_group_owner(run['execution_id'])
                    return
                if finish == 'stop':
                    self.engine.stop_group(run['execution_id'], fade_ms=0)
                self.engine.unregister_group_owner(run['execution_id'])
            for prepared in starts:
                if run['cancel'].is_set():
                    break
                try:
                    self.engine.start_group(prepared)
                except Exception as exc:
                    with self._lock:
                        run['state'], run['error'] = 'failed', str(exc)
                        run['cancel'].set()
                    self.engine.stop_group(run['execution_id'], fade_ms=0)
                    self.engine.unregister_group_owner(run['execution_id'])
                    break
            if starts:
                self._wake.set()

    def advance(self, execution_id, repeat=False):
        # Decide na chegada da ação, antes de esperar pelo executor/preparação.
        with self._lock:
            run = self._run
            if repeat or not run or run['execution_id'] != execution_id or run['state'] != 'manual' or run['cancel'].is_set():
                return False
            index = run['root']['index']
        with self._commands:
            with self._lock:
                if self._run is not run or run['root']['index'] != index or run['state'] != 'manual' or run['cancel'].is_set():
                    return False
                run['root']['waiting'] = None
                run['root']['index'] += 1
                run['state'] = 'playing'
            self.pump()
            self._wake.set()
            return True

    def stop(self, execution_id=None, fade_ms=0):
        # Pode interromper preparação sem aguardar o lock serial do executor.
        with self._lock:
            run = self._run
            if not run or (execution_id and run['execution_id'] != execution_id) or run['state'] in TERMINAL:
                return False
            run['cancel'].set()
            run['state'] = 'cancelled'
            run['cleaned'] = True
        self.engine.stop_group(run['execution_id'], fade_ms=fade_ms)
        self.engine.unregister_group_owner(run['execution_id'])
        self._wake.set()
        return True

    def snapshot(self):
        with self._lock:
            run = self._run
            if not run:
                return {'state': 'idle', 'execution_id': None, 'active_blocks': [], 'branches': [], 'next_block': None}
            root = run['root']
            terminal = run['state'] in TERMINAL
            lanes = root['children'] or [root]
            active = [] if terminal else [lane['blocks'][lane['index']]['id'] for lane in lanes if lane['index'] < len(lane['blocks'])]
            branches = [] if not root['children'] else [{'index': index, 'block_id': lane['blocks'][lane['index']]['id'] if lane['index'] < len(lane['blocks']) else None,
                'state': 'completed' if lane['index'] == len(lane['blocks']) else lane['waiting'] or 'waiting'} for index, lane in enumerate(lanes)]
            index = root['index'] + 1
            return {'execution_id': run['execution_id'], 'sequence_id': run['document']['id'],
                    'state': run['state'], 'error': run['error'], 'monitor_only': run['monitor_only'],
                    'document': copy.deepcopy(run['document']), 'active_blocks': active, 'branches': branches,
                    'current_block': root['blocks'][root['index']]['id'] if not terminal and root['index'] < len(root['blocks']) else None,
                    'next_block': copy.deepcopy(root['blocks'][index]) if not terminal and index < len(root['blocks']) else None,
                    'duration_ms': run['duration_ms'], 'known_duration_ms': run['known_duration_ms'], 'manual_count': run['manual_count']}

    def _worker(self):
        while not self._closed.is_set():
            self._wake.clear()
            self.pump()
            with self._lock:
                run = self._run
                deadlines = []
                if run and run['state'] not in TERMINAL:
                    for lane in run['root']['children'] or [run['root']]:
                        if lane['waiting'] == 'wait':
                            deadlines.append(max(0, lane['deadline'] - self.clock()))
            self._wake.wait(min(deadlines) if deadlines else None)

    def close(self):
        self.stop()
        self._closed.set()
        self._wake.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=2)
