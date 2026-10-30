"""Fila local limitada; uma inferência ativa e cancelamento lógico."""
import copy
import heapq
import threading
import time
import uuid

TERMINAL = frozenset(('ready', 'no_speech', 'failed', 'cancelled'))


class AnalysisPausedError(ValueError):
    """Pausa explícita: permite preservar captura sem confundir erro de decoder."""


class AnalysisQueue:
    def __init__(self, capacity=8, autostart=True):
        self.capacity = max(1, int(capacity))
        self._condition = threading.Condition(threading.RLock())
        self._pending, self._jobs = [], {}
        self._sequence = 0
        self._closed = False
        self._thread = None
        self._autostart = autostart
        self._reservations = set()
        self._profile, self._paused = 'reviewing', False

    def set_policy(self, profile='reviewing', paused=False):
        if profile not in ('reviewing', 'playing') or not isinstance(paused, bool):
            raise ValueError('Política de recursos inválida')
        with self._condition:
            self._profile, self._paused = profile, paused
            self._condition.notify_all()
        return self.policy()

    def policy(self):
        with self._condition:
            return {'profile': self._profile, 'paused': self._paused, 'concurrency': 1,
                    'running_is_preempted': False, 'explicit_allowed_while_playing': True}

    def allows(self, explicit=False):
        with self._condition:
            return not self._closed and not self._paused and (self._profile == 'reviewing' or explicit)

    def reprioritize(self, ident, prioritized=True):
        if not isinstance(prioritized, bool):
            return False
        with self._condition:
            job = self._jobs.get(ident)
            if not job or job['status'] != 'queued':
                return False
            priority = 0 if prioritized else 10
            self._pending = [(priority if key == ident else p, seq, key) for p, seq, key in self._pending]
            heapq.heapify(self._pending)
            job['priority'] = priority
            self._condition.notify_all()
            return True

    def reserve(self):
        """Reserva capacidade antes de criar uma nova geração no store."""
        with self._condition:
            if self._closed:
                return {'ok': False, 'error_code': 'queue_closed', 'error': 'Fila encerrada'}
            if len(self._reservations) + sum('_run' in j for j in self._jobs.values()) >= self.capacity:
                return {'ok': False, 'error_code': 'queue_full', 'error': 'Fila de análise cheia'}
            reservation = uuid.uuid4().hex
            self._reservations.add(reservation)
            return {'ok': True, 'reservation': reservation}

    def release_reservation(self, reservation):
        with self._condition:
            self._reservations.discard(reservation)

    def start(self):
        with self._condition:
            if self._thread is None and not self._closed:
                self._thread = threading.Thread(target=self._work, daemon=True, name='AnalysisWorker')
                self._thread.start()

    def submit(self, run, apply=None, priority=10, metadata=None, cleanup=None, reservation=None):
        with self._condition:
            if self._closed:
                return {'ok': False, 'error_code': 'queue_closed', 'error': 'Fila encerrada'}
            if reservation is not None and reservation not in self._reservations:
                return {'ok': False, 'error_code': 'invalid_reservation', 'error': 'Reserva de análise inválida'}
            if reservation is None and len(self._reservations) + sum('_run' in j for j in self._jobs.values()) >= self.capacity:
                return {'ok': False, 'error_code': 'queue_full', 'error': 'Fila de análise cheia'}
            self._reservations.discard(reservation)
            # Histórico em memória também é limitado; propostas aceitas vivem no store.
            for ident in list(self._jobs):
                if len(self._jobs) < 128:
                    break
                if self._jobs[ident]['status'] in TERMINAL and '_run' not in self._jobs[ident]:
                    del self._jobs[ident]
            ident = (metadata or {}).get('analysis_id') or uuid.uuid4().hex
            job = {**copy.deepcopy(metadata or {}), 'analysis_id': ident, 'status': 'queued', 'error': None, 'priority': priority,
                   '_run': run, '_apply': apply, '_cleanup': cleanup}
            self._jobs[ident] = job
            heapq.heappush(self._pending, (priority, self._sequence, ident))
            self._sequence += 1
            self._condition.notify_all()
            if self._autostart:
                self.start()
            return {'ok': True, **self._public(job)}

    @staticmethod
    def _public(job):
        return copy.deepcopy({k: v for k, v in job.items() if not k.startswith('_')})

    def get(self, ident):
        with self._condition:
            return self._public(self._jobs[ident]) if ident in self._jobs else None

    def list(self):
        with self._condition:
            return [self._public(j) for j in self._jobs.values()]

    def cancel(self, ident):
        with self._condition:
            job = self._jobs.get(ident)
            if not job or job['status'] in TERMINAL:
                return False
            was_queued = job['status'] == 'queued'
            job['status'] = 'cancelled'
            if was_queued:
                self._pending = [item for item in self._pending if item[2] != ident]
                heapq.heapify(self._pending)
                self._finish(job)
            self._condition.notify_all()
            return True

    def wait(self, ident, timeout=10):
        deadline = time.monotonic() + timeout
        with self._condition:
            while self._jobs[ident]['status'] not in TERMINAL:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('Análise não terminou')
                self._condition.wait(remaining)
            return self.get(ident)

    def _finish(self, job):
        cleanup = job.pop('_cleanup', None)
        job.pop('_run', None)
        job.pop('_apply', None)
        if cleanup:
            cleanup()

    def _work(self):
        while True:
            with self._condition:
                while not self._closed and (not self._pending or self._paused or
                        (self._profile == 'playing' and self._pending[0][0] > 0)):
                    self._condition.wait()
                if not self._pending:
                    return
                _, _, ident = heapq.heappop(self._pending)
                job = self._jobs[ident]
                if job['status'] == 'cancelled':
                    self._finish(job)
                    continue
                job['status'] = 'running'
            try:
                started = time.monotonic()
                result = job['_run']()
                with self._condition:
                    # Cancelamento e aplicação são serializados; não há janela entre ambos.
                    if job['status'] != 'cancelled' and not self._closed:
                        job['result'] = result
                        job['status'] = result.get('status', 'ready')
                        if job['status'] not in TERMINAL:
                            job['status'] = 'ready'
                        job['error'] = result.get('error')
                        if job['_apply'] and job['status'] in ('ready', 'no_speech'):
                            job['application'] = job['_apply'](result)
            except Exception as exc:
                with self._condition:
                    if job['status'] != 'cancelled':
                        job['status'] = 'failed'
                        job['error'] = {'code': 'analysis_failed', 'message': str(exc)}
            finally:
                with self._condition:
                    from app_logging import log_analysis_metric
                    log_analysis_metric(job['status'], duration_ms=(time.monotonic()-started)*1000,
                                        queue_depth=len(self._pending))
                    self._finish(job)
                    self._condition.notify_all()

    def close(self, wait=True):
        with self._condition:
            self._closed = True
            self._reservations.clear()
            for job in self._jobs.values():
                if job['status'] not in TERMINAL:
                    job['status'] = 'cancelled'
            # Liberar leases da fila imediatamente, mesmo com inferência ativa.
            for _, _, ident in self._pending:
                self._finish(self._jobs[ident])
            self._pending.clear()
            self._condition.notify_all()
        if wait and self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=2)
