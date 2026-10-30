import importlib.util
import threading


def queue(capacity=3):
    assert importlib.util.find_spec('analysis_jobs'), 'Fila de análise ausente'
    from analysis_jobs import AnalysisQueue
    return AnalysisQueue(capacity=capacity, autostart=False)


def test_bounded_queue_prioritizes_open_clip_and_runs_serially():
    q = queue(2)
    order = []
    one = q.submit(lambda: order.append('background') or {}, priority=10)
    two = q.submit(lambda: order.append('open') or {}, priority=0)
    assert q.submit(lambda: {})['error_code'] == 'queue_full'
    q.start()
    assert q.wait(one['analysis_id'], 2)['status'] == 'ready'
    assert q.wait(two['analysis_id'], 2)['status'] == 'ready'
    assert order == ['open', 'background']
    q.close()


def test_cancel_running_never_applies_and_shutdown_cancels_queued():
    q = queue()
    entered, finish = threading.Event(), threading.Event()
    applied = []
    def run():
        entered.set()
        assert finish.wait(2)
        return {'text': 'tardio'}
    job = q.submit(run, apply=lambda result: applied.append(result))
    q.start()
    assert entered.wait(2)
    assert q.cancel(job['analysis_id'])
    pending = q.submit(lambda: {})
    q.close(wait=False)
    finish.set()
    q.close()
    assert applied == []
    assert q.get(job['analysis_id'])['status'] == 'cancelled'
    assert q.get(pending['analysis_id'])['status'] == 'cancelled'


def test_worker_error_is_visible_and_next_job_still_runs():
    q = queue()
    def bad():
        raise RuntimeError('falha sintética')
    job = q.submit(bad)
    next_job = q.submit(lambda: {'status': 'no_speech'})
    q.start()
    assert q.wait(job['analysis_id'], 2)['error']['code'] == 'analysis_failed'
    assert q.wait(next_job['analysis_id'], 2)['status'] == 'no_speech'
    q.close()


def test_cancelled_running_inference_still_reserves_capacity_until_cleanup():
    q = queue(1)
    entered, release = threading.Event(), threading.Event()
    def run():
        entered.set()
        release.wait(2)
        return {}
    job = q.submit(run)
    q.start()
    assert entered.wait(2)
    q.cancel(job['analysis_id'])
    try:
        assert q.submit(lambda: {})['error_code'] == 'queue_full'
    finally:
        release.set()
        q.close()
