"""E7: finalização única e consumidores chamados fora dos locks."""
import threading

import numpy as np

from mixer import DeviceMixer, MixerVoice


def test_callback_typeerror_is_not_retried_without_arguments():
    calls = []
    def broken(*args):
        calls.append(args)
        raise TypeError('erro no consumidor')
    voice = MixerVoice('p', 'c', np.ones((1, 2)), on_finish=broken)
    voice.trigger_finish()
    assert calls == [('c', 'p')]


def test_zero_argument_legacy_callback_is_adapted_before_invocation():
    calls = []
    voice = MixerVoice('p', 'c', np.ones((1, 2)), on_finish=lambda: calls.append('done'))
    voice.trigger_finish()
    voice.trigger_finish()
    assert calls == ['done']


def test_completion_is_typed_once_and_outside_render_and_voice_locks():
    device = DeviceMixer(dest_key='monitor')
    events, acquired = [], []
    def finished(event):
        events.append(event)
        def worker():
            with device._lock, voice._lock:
                acquired.append(True)
        worker_thread = threading.Thread(target=worker)
        worker_thread.start()
        worker_thread.join(1)
        assert not worker_thread.is_alive()
    voice = MixerVoice('p', 'c', np.ones((1, 2)), group_id='g', on_complete=finished)
    voice.register_destinations({'monitor'})
    device.add_voice(voice)
    device.render_block(8)
    voice.stop(0)
    assert acquired == [True]
    assert len(events) == 1
    assert (events[0].play_id, events[0].group_id, events[0].reason) == ('p', 'g', 'completed')


def test_concurrent_cancel_and_natural_completion_deliver_exactly_one_event():
    for _ in range(30):
        events = []
        voice = MixerVoice('p', 'c', np.ones((1, 2)), on_complete=events.append)
        barrier = threading.Barrier(3)
        def run(action):
            barrier.wait()
            action()
        threads = [threading.Thread(target=run, args=(action,)) for action in
                   (lambda: voice.stop(0), voice.trigger_finish)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(1)
            assert not thread.is_alive()
        assert len(events) == 1
        assert events[0].reason in ('completed', 'cancelled')


def test_immediate_stop_upgrades_pending_fade_and_finishes_once():
    events = []
    voice = MixerVoice('p', 'c', np.ones((100, 2)), on_complete=events.append)
    voice.stop(10)
    voice.stop(0)
    assert len(events) == 1 and events[0].reason == 'cancelled'
