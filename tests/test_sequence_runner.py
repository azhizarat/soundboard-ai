"""E8: execução backend determinística sobre engine/mixer reais e PCM sintético."""
import copy
import importlib.util
import uuid
import threading
import time

import pytest

from mixer import CompletionEvent
from test_sound_sequences import library, document, play
from test_playback_groups import engine
from test_mixer_schedule import SETTINGS


class Clock:
    def __init__(self):
        self.now = 0.0
    def __call__(self):
        return self.now
    def advance(self, seconds):
        self.now += seconds


def runner(store, audio, clock=None, background=False):
    assert importlib.util.find_spec('sequence_runner'), 'Executor backend ausente'
    from sequence_runner import SequenceRunner
    return SequenceRunner(store, audio, clock=clock or Clock(), background=background)


def render(audio, count):
    audio._mixer.cable_mixer.render_block(count)
    audio._mixer.monitor_mixer.render_block(count)


def test_m2_parallel_barrier_manual_repeated_clip_and_no_early_advance(library, engine):
    store, clip = library
    clock = Clock()
    run = runner(store, engine, clock)
    blocks = [play(clip, 'question'), {'id': 'pause', 'type': 'wait', 'ms': 300},
              {'id': 'together', 'type': 'parallel', 'branches': [
                  [play(clip, 'answer', interval=[0, 24000]), {'id': 'tail', 'type': 'wait', 'ms': 500}],
                  [play(clip, 'laugh', interval=[0, 48000])]]},
              {'id': 'operator', 'type': 'manual'}, play(clip, 'final')]
    started = run.start(document(clip, blocks), SETTINGS)
    eid = started['execution_id']
    assert str(uuid.UUID(eid)) == eid
    assert not run.advance(eid)
    assert run.snapshot()['active_blocks'] == ['question']
    render(engine, 48000)
    run.pump()
    assert run.snapshot()['state'] == 'waiting'
    clock.advance(.299); run.pump()
    assert run.snapshot()['active_blocks'] == ['pause']
    clock.advance(.001); run.pump()
    voices = list(engine._mixer.cable_mixer._voices.values())
    assert len(voices) == 2
    assert len({item['start_sample'] for item in engine._mixer.cable_mixer._voice_states.values()}) == 1
    render(engine, 24000); run.pump()
    assert 'tail' in run.snapshot()['active_blocks']
    render(engine, 24000); run.pump()
    assert run.snapshot()['state'] != 'manual'
    clock.advance(.5); run.pump()
    assert run.snapshot()['state'] == 'manual'
    assert run.advance(eid)
    assert not run.advance(eid)
    render(engine, 48000); run.pump()
    assert run.snapshot()['state'] == 'completed'
    assert not store._active_sources


@pytest.mark.parametrize('kind', ['play', 'wait', 'manual'])
def test_stop_all_in_every_state_prevents_future_audio(library, engine, kind):
    store, clip = library
    clock = Clock(); run = runner(store, engine, clock)
    first = play(clip) if kind == 'play' else {'id': 'first', 'type': kind, 'ms': 50}
    run.start(document(clip, [first, play(clip, 'later')]), SETTINGS)
    engine.stop_all(); run.pump()
    clock.advance(1000); run.pump(); render(engine, 100000); run.pump()
    assert run.snapshot()['state'] == 'cancelled'
    assert not run.advance(run.snapshot()['execution_id'])
    assert not engine._sessions and not store._active_sources


def test_wait_only_branch_tail_and_leading_delay_share_start(library, engine):
    store, clip = library
    clock = Clock(); run = runner(store, engine, clock)
    blocks = [{'id': 'parallel', 'type': 'parallel', 'branches': [
        [{'id': 'delay', 'type': 'wait', 'ms': 100}, play(clip, interval=[0, 48])],
        [{'id': 'silent', 'type': 'wait', 'ms': 300}]]}, {'id': 'manual', 'type': 'manual'}]
    run.start(document(clip, blocks), SETTINGS)
    assert engine._mixer.cable_mixer.render_block(4800).sum() == 0
    engine._mixer.monitor_mixer.render_block(4800)
    render(engine, 48); run.pump()
    assert run.snapshot()['state'] == 'waiting'
    clock.advance(.299); run.pump()
    assert run.snapshot()['state'] == 'waiting'
    clock.advance(.001); run.pump()
    assert run.snapshot()['state'] == 'manual'
    run.stop()


def test_preflight_every_dependency_before_any_start_and_release(library, engine, monkeypatch):
    store, clip = library
    run = runner(store, engine)
    starts = []
    monkeypatch.setattr(engine, 'start_group', lambda *args, **kwargs: starts.append(args))
    bad = document(clip, [play(clip), play(clip, 'absent', revision_id='missing')])
    with pytest.raises(ValueError):
        run.start(bad, SETTINGS)
    assert not starts and not store._active_sources
    count = 0
    original = engine.render_clip
    def fail_second(*args):
        nonlocal count
        count += 1
        if count == 2:
            raise ValueError('decode failed')
        return original(*args)
    monkeypatch.setattr(engine, 'render_clip', fail_second)
    with pytest.raises(ValueError):
        run.start(document(clip, [play(clip), play(clip, 'two')]), SETTINGS)
    assert not starts and not store._active_sources and not engine._sessions


def test_restart_frozen_documents_settings_and_stale_completion(library, engine):
    store, clip = library
    run = runner(store, engine)
    doc = document(clip, [play(clip), {'id': 'm', 'type': 'manual'}, play(clip, 'two')])
    settings = copy.deepcopy(SETTINGS)
    old = run.start(doc, settings)['execution_id']
    old_voice = next(iter(engine._mixer.cable_mixer._voices.values()))
    doc['blocks'][0]['interval'] = [0, 1]; settings['cable_volume'] = 0
    assert run.snapshot()['document']['blocks'][0]['interval'] == [0, 48000]
    with pytest.raises(ValueError, match='ativa'):
        run.start(doc, SETTINGS)
    new = run.start(document(clip, [{'id': 'gate', 'type': 'manual'}, play(clip)]), SETTINGS, replace=True)['execution_id']
    assert new != old
    run.enqueue_completion(old, CompletionEvent(old_voice.play_id, clip['id'], old, 'completed'))
    run.pump()
    assert run.snapshot()['state'] == 'manual'
    assert not run.advance(old)
    assert not run.advance(new, repeat=True)
    assert run.advance(new)
    run.stop()


@pytest.mark.parametrize('reason', ['cancelled', 'failed'])
def test_terminal_audio_reason_cancels_siblings_not_external(library, engine, reason):
    store, clip = library
    run = runner(store, engine)
    external = engine.play_clip(store.render_context(clip['id']), SETTINGS)
    seq = document(clip, [{'id': 'p', 'type': 'parallel', 'branches': [[play(clip)], [play(clip, 'two')]]}, play(clip, 'future')])
    eid = run.start(seq, SETTINGS)['execution_id']
    voice = next(v for v in engine._sessions.values() if v.group_id == eid)
    run.enqueue_completion(eid, CompletionEvent(voice.play_id, clip['id'], eid, reason, 'test' if reason == 'failed' else None))
    run.pump()
    assert run.snapshot()['state'] == reason
    assert engine.get_session(external) is not None
    assert not any(v.group_id == eid for v in engine._sessions.values())
    assert not store._active_sources
    engine.stop_all()


def test_early_advance_never_waits_for_executor_to_reach_manual(library, engine):
    store, clip = library
    run = runner(store, engine)
    eid = run.start(document(clip, [play(clip), {'id': 'gate', 'type': 'manual'}, play(clip, 'last')]), SETTINGS)['execution_id']
    returned, results = threading.Event(), []
    def advance():
        results.append(run.advance(eid))
        returned.set()
    with run._commands:
        thread = threading.Thread(target=advance)
        thread.start()
        early_return = returned.wait(.3)
        render(engine, 48000); run.pump()
    thread.join(2)
    try:
        assert early_return, 'Avanço antecipado ficou enfileirado no executor'
        assert results == [False]
        assert run.snapshot()['state'] == 'manual'
    finally:
        run.stop()


def test_consecutive_waits_use_deadlines_without_accumulating_poll_delay(library, engine):
    store, clip = library
    clock = Clock(); run = runner(store, engine, clock)
    run.start(document(clip, [{'id': 'w1', 'type': 'wait', 'ms': 100}, {'id': 'w2', 'type': 'wait', 'ms': 200}, {'id': 'm', 'type': 'manual'}]), SETTINGS)
    clock.advance(.3); run.pump()
    assert run.snapshot()['state'] == 'manual'
    run.stop()


def test_background_executor_runs_without_ui_and_does_not_leak_idle_thread(library, engine):
    store, clip = library
    run = runner(store, engine, clock=time.monotonic, background=True)
    assert run._thread is None, 'Executor ocioso não deve reter um store/thread indefinidamente'
    run.start(document(clip, [{'id': 'w', 'type': 'wait', 'ms': 10}, play(clip, interval=[0, 48])]), SETTINGS)
    deadline = time.monotonic() + 2
    while not engine._mixer.cable_mixer._voices and time.monotonic() < deadline:
        time.sleep(.005)
    assert engine._mixer.cable_mixer._voices
    render(engine, 48)
    while run.snapshot()['state'] != 'completed' and time.monotonic() < deadline:
        time.sleep(.005)
    assert run.snapshot()['state'] == 'completed'
    run.close()
    assert not run._thread.is_alive()


def test_cancel_during_preflight_never_publishes_audio(library, engine, monkeypatch):
    store, clip = library
    run = runner(store, engine)
    entered, release = threading.Event(), threading.Event()
    original = engine.render_clip
    def blocked(*args):
        entered.set()
        assert release.wait(2)
        return original(*args)
    monkeypatch.setattr(engine, 'render_clip', blocked)
    errors = []
    def start():
        try: run.start(document(clip, [play(clip), play(clip, 'two')]), SETTINGS)
        except ValueError as error: errors.append(str(error))
    thread = threading.Thread(target=start);thread.start()
    assert entered.wait(2)
    engine.stop_all();release.set();thread.join(2)
    run.pump()
    assert errors and not thread.is_alive()
    assert run.snapshot()['state'] == 'cancelled'
    assert not engine._sessions and not store._active_sources
    assert not engine._mixer.cable_mixer._voices


@pytest.mark.parametrize('boundary', ['validation', 'registration'])
def test_stop_all_invalidates_start_requested_before_owner_registration(library, engine, monkeypatch, boundary):
    store, clip = library
    run = runner(store, engine)
    entered, release = threading.Event(), threading.Event()
    owner = store if boundary == 'validation' else engine
    method = 'resolve_sequence_occurrence' if boundary == 'validation' else 'register_group_owner'
    original = getattr(owner, method)
    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return original(*args, **kwargs)
    monkeypatch.setattr(owner, method, blocked)
    errors = []
    def start():
        try:
            run.start(document(clip), SETTINGS)
        except ValueError as error:
            errors.append(str(error))
    thread = threading.Thread(target=start)
    thread.start()
    try:
        assert entered.wait(3)
        assert run.snapshot()['state'] == ('preparing' if boundary == 'registration' else 'idle')
        # Stop All termina antes de liberar o trabalho suspenso; não espera I/O.
        engine.stop_all()
        assert not engine._group_owners
        release.set()
        thread.join(3)
        run.pump()
        assert not thread.is_alive()
        assert run.snapshot()['state'] == 'cancelled'
        assert errors and not engine._sessions and not store._active_sources
        assert not engine._mixer.cable_mixer._voices
        assert not engine._mixer.monitor_mixer._voices
        # Uma solicitação realmente posterior à parada usa a nova geração.
        monkeypatch.setattr(owner, method, original)
        assert run.start(document(clip), SETTINGS)['state'] == 'playing'
    finally:
        release.set()
        thread.join(3)
        run.stop()
