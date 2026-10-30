"""E7: engine real, montagem congelada e isolamento de propriedade."""
import numpy as np
import pytest
import soundfile as sf

from audio_engine import AudioEngine
from mixer import DeviceMixer
from test_mixer_schedule import SETTINGS


@pytest.fixture
def engine(monkeypatch):
    monkeypatch.setattr(DeviceMixer, 'ensure_stream', lambda self: True)
    monkeypatch.setattr('mixer.is_cable_output_device', lambda _: False)
    engine = AudioEngine(start_loopback=False)
    monkeypatch.setattr(engine, '_output_route_usable', lambda _: True)
    return engine


@pytest.fixture
def clip(tmp_path):
    path = tmp_path / 'clip.wav'
    sf.write(path, np.full((16, 2), .2), 48000, subtype='FLOAT')
    return {'id': 'c', 'file_path': str(path), 'volume': 1.}


def test_immediate_completion_observes_registered_session_and_releases_once(engine, clip, monkeypatch):
    events, releases, seen = [], [], []
    clip['_release_source'] = lambda: releases.append(1)
    original = engine._mixer.start_group
    def immediate(group, **kwargs):
        seen.extend(engine.get_session(v.play_id) is v for v in group.voices)
        original(group, **kwargs)
        engine._mixer.cable_mixer.render_block(64)
        engine._mixer.monitor_mixer.render_block(64)
    monkeypatch.setattr(engine._mixer, 'start_group', immediate)
    pid = engine.play_clip(clip, SETTINGS, on_complete=events.append)
    assert pid and seen == [True]
    assert len(events) == 1 and events[0].reason == 'completed'
    assert not engine._sessions
    engine.stop_all()
    assert releases == [1]


def test_overlap_false_and_preview_do_not_stop_group_or_other_domain(engine, clip):
    group = engine.prepare_group('flow', [{'clip': clip}, {'clip': clip}], SETTINGS)
    engine.start_group(group)
    first = engine.play_clip(clip, SETTINGS)
    preview = engine.play_clip(clip, SETTINGS, monitor_only=True)
    second = engine.play_clip(clip, SETTINGS)
    assert engine.get_session(first).stopping
    assert not engine.get_session(preview).stopping
    assert all(not voice.stopping for voice in group.voices)
    engine.stop_group('flow', fade_ms=0)
    assert engine.get_session(second) is not None
    assert engine.get_session(preview) is not None
    assert all(engine.get_session(voice.play_id) is None for voice in group.voices)


def test_stop_all_cancels_owner_before_callbacks_and_invalidates_prepared(engine, clip):
    order = []
    engine.register_group_owner('g', lambda: order.append('owner'))
    group = engine.prepare_group('g', [{'clip': clip}], SETTINGS,
                                 on_complete=lambda event: order.append(event.reason))
    engine.stop_all()
    assert order == ['owner', 'cancelled']
    with pytest.raises(ValueError):
        engine.start_group(group)
    assert not engine._sessions


def test_preparation_failure_releases_all_sources_once(engine, clip):
    releases = []
    good = dict(clip, _release_source=lambda: releases.append('good'))
    bad = dict(clip, file_path='missing.wav', _release_source=lambda: releases.append('bad'))
    with pytest.raises(ValueError):
        engine.prepare_group('g', [{'clip': good}, {'clip': bad}], SETTINGS)
    assert releases == ['good', 'bad']
    assert not engine._sessions
    assert not engine._mixer.cable_mixer._voices


def test_legacy_exposes_unavailability_without_starting_threads(clip):
    engine = AudioEngine(start_loopback=False, mixer_mode='legacy')
    assert engine.playback_capabilities()['groups'] is False
    with pytest.raises(ValueError, match='mixer'):
        engine.prepare_group('g', [{'clip': clip}], SETTINGS)


def test_single_clip_failed_preflight_releases_source_once(engine, clip):
    releases = []
    missing = dict(clip, file_path='missing.wav', _release_source=lambda: releases.append(1))
    assert engine.play_clip(missing, SETTINGS) == ''
    assert releases == [1]


def test_legacy_typed_completion_releases_source_once(clip, monkeypatch):
    import threading
    engine = AudioEngine(start_loopback=False, mixer_mode='legacy')
    monkeypatch.setattr(engine, '_output_route_usable', lambda _: True)
    monkeypatch.setattr('mixer.is_cable_output_device', lambda _: False)
    monkeypatch.setattr(engine, '_stream_session_to_device', lambda *args: None)
    done, events, releases = threading.Event(), [], []
    def finished(event):
        events.append(event)
        done.set()
    clip['_release_source'] = lambda: releases.append(1)
    assert engine.play_clip(clip, SETTINGS, on_complete=finished)
    assert done.wait(2)
    engine.stop_all()
    assert [event.reason for event in events] == ['completed']
    assert releases == [1]


def test_api_exposes_group_capabilities_and_stop_owner(tmp_path, monkeypatch):
    from main import SoundboardAPI
    api = SoundboardAPI(data_dir=str(tmp_path / 'data'), start_background_services=False)
    assert api.get_playback_capabilities()['groups'] is True
    cancelled = []
    api._audio.register_group_owner('g', lambda: cancelled.append('g'))
    assert api.stop_playback_group('g') == {'ok': True}
    assert cancelled == ['g']
    api._audio = AudioEngine(start_loopback=False, mixer_mode='legacy')
    assert api.get_playback_capabilities()['groups'] is False
    assert 'mixer' in api.stop_playback_group('g')['error']


def test_hotkey_stop_dispatches_owner_cancellation_before_pending_audio(engine, clip):
    import threading
    from hotkeys import HotkeyManager
    order, done = [], threading.Event()
    engine.register_group_owner('g', lambda: order.append('owner'))
    engine.prepare_group('g', [{'clip': clip}], SETTINGS,
                         on_complete=lambda event: (order.append(event.reason), done.set()))
    hotkeys = HotkeyManager(None, engine.stop_all, None, None)
    hotkeys.trigger_stop_all()
    assert done.wait(2)
    assert order == ['owner', 'cancelled']


def test_reentrant_stop_all_does_not_reopen_playback_during_owner_cancellation(engine, clip):
    restarted = []
    def cancel():
        engine.stop_all()
        restarted.append(engine.play_clip(clip, SETTINGS))
    engine.register_group_owner('g', cancel)
    engine.prepare_group('g', [{'clip': clip}], SETTINGS)
    engine.stop_all()
    assert restarted == ['']
    assert not engine._sessions
    assert not engine._mixer._voices


def test_render_and_gain_are_frozen_once_and_preview_matches_broadcast(engine, clip, tmp_path):
    from clip_renderer import preserve_source
    source = preserve_source(clip['file_path'], tmp_path / 'sources')
    revision = {'source_hash': source['sha256'], 'spans': [[0, 8], [12, 16]],
                'gain_db': -6., 'normalization_gain': .8, 'fade_samples': 2}
    context = dict(clip, _source=source, _revision=revision, volume=.7)
    expected = engine.render_clip(context) * .7
    broadcast = engine.prepare_group('live', [{'clip': context}], SETTINGS)
    preview = engine.prepare_group('preview', [{'clip': context}], SETTINGS, monitor_only=True)
    revision['gain_db'] = 20
    revision['spans'] = [[0, 16]]
    context['volume'] = 3
    engine.start_group(broadcast)
    engine.start_group(preview)
    live = engine._mixer.cable_mixer.render_block(len(expected))
    # Monitor tem transmissão + prévia, usando o mesmo PCM e ganho congelados.
    local = engine._mixer.monitor_mixer.render_block(len(expected))
    np.testing.assert_allclose(live, expected, atol=1e-7)
    np.testing.assert_allclose(local, expected * 2, atol=1e-7)


def test_stop_all_during_render_preparation_cannot_publish_afterwards(engine, clip, monkeypatch):
    import threading
    entered, resume, errors, released = threading.Event(), threading.Event(), [], []
    original = engine.render_clip
    def slow(*args):
        entered.set()
        assert resume.wait(2)
        return original(*args)
    monkeypatch.setattr(engine, 'render_clip', slow)
    clip['_release_source'] = lambda: released.append(1)
    def prepare():
        try:
            engine.prepare_group('g', [{'clip': clip}], SETTINGS)
        except ValueError as exc:
            errors.append(str(exc))
    worker = threading.Thread(target=prepare)
    worker.start()
    assert entered.wait(2)
    engine.stop_all()
    resume.set()
    worker.join(2)
    assert not worker.is_alive() and len(errors) == 1
    assert released == [1]
    assert not engine._sessions and not engine._mixer._voices


def test_failing_consumer_does_not_prevent_other_completions_or_source_cleanup(engine, clip):
    events, releases = [], []
    def broken(event):
        events.append(event)
        raise TypeError('consumer')
    occurrences = [{'clip': dict(clip, _release_source=lambda: releases.append(1))} for _ in range(2)]
    group = engine.prepare_group('g', occurrences, SETTINGS, on_complete=broken)
    engine.start_group(group)
    engine._mixer.cable_mixer.render_block(32)
    engine._mixer.monitor_mixer.render_block(32)
    assert len(events) == 2 and releases == [1, 1]
    assert not engine._sessions and not engine._mixer._groups


def test_single_clip_preparing_when_stop_all_runs_cannot_start_afterwards(engine, clip, monkeypatch):
    import threading
    entered, resume, results = threading.Event(), threading.Event(), []
    original = engine.render_clip
    def slow(*args):
        entered.set()
        assert resume.wait(2)
        return original(*args)
    monkeypatch.setattr(engine, 'render_clip', slow)
    worker = threading.Thread(target=lambda: results.append(engine.play_clip(clip, SETTINGS)))
    worker.start()
    assert entered.wait(2)
    engine.stop_all()
    resume.set()
    worker.join(2)
    assert not worker.is_alive() and results == ['']
    assert not engine._sessions and not engine._mixer._voices


def test_uncontracted_mixer_cannot_publish_voice_before_engine_registration(engine, clip):
    from mixer import MixerVoice
    calls = []
    class UncontractedMixer:
        def play_voice(self, **kwargs):
            calls.append('published')
            return MixerVoice('unsafe', 'c', kwargs['data'])
    engine._mixer = UncontractedMixer()
    assert engine.play_clip(clip, SETTINGS) == ''
    assert calls == []
    assert not engine._sessions


@pytest.mark.parametrize('termination', ['route_failure', 'voice_cancel'])
def test_owner_termination_reaches_other_routes_and_prepared_batches_preserving_external(engine, clip, termination):
    import threading
    events, order, released, locks_free = [], [], [], []
    def complete(event):
        events.append(event)
        order.append(event.reason)
        def acquire():
            with engine._lock, engine._mixer._lock, engine._mixer.cable_mixer._lock, engine._mixer.monitor_mixer._lock:
                locks_free.append(True)
        worker = threading.Thread(target=acquire)
        worker.start()
        worker.join(1)
        assert not worker.is_alive()
    engine.register_group_owner('owner', lambda: order.append('owner'))
    def prepare(route, start=True):
        context = dict(clip, _release_source=lambda: released.append(route))
        settings = dict(SETTINGS, monitor_volume=0) if route == 'cable' else SETTINGS
        group = engine.prepare_group('owner', [{'clip': context}], settings,
                                     monitor_only=route != 'cable', on_complete=complete)
        if start:
            engine.start_group(group)
        return group
    cable = prepare('cable')
    monitor = prepare('monitor')
    pending = prepare('pending', start=False)
    external = engine.prepare_group('external', [{'clip': dict(clip, volume=.25)}], SETTINGS, monitor_only=True)
    engine.start_group(external)
    if termination == 'route_failure':
        engine._mixer.cable_mixer.invalidate_route('lost')
    else:
        cable.voices[0].stop(0)
    reason = 'failed' if termination == 'route_failure' else 'cancelled'
    assert len(events) == 3
    assert {event.play_id for event in events} == {group.voices[0].play_id for group in (cable, monitor, pending)}
    assert {event.reason for event in events} == {reason}
    assert order == ['owner', reason, reason, reason]
    assert len(locks_free) == 3
    assert sorted(released) == ['cable', 'monitor', 'pending']
    with pytest.raises(ValueError):
        engine.start_group(pending)
    expected = external.voices[0].data * .25
    np.testing.assert_allclose(engine._mixer.monitor_mixer.render_block(len(expected)), expected, atol=1e-6)
    assert external.voices[0].completion.reason == 'completed'
    assert len(events) == 3 and not engine._sessions


def test_completion_dispatch_is_iterative_when_consumer_stops_another_owner(engine, clip):
    order = []
    def complete(event):
        order.append(('enter', event.group_id))
        if event.group_id == 'first':
            engine.stop_group('second', fade_ms=0)
        order.append(('leave', event.group_id))
    first = engine.prepare_group('first', [{'clip': clip}], SETTINGS, on_complete=complete)
    engine.prepare_group('second', [{'clip': clip}], SETTINGS, on_complete=complete)
    engine.start_group(first)
    engine.stop_group('first', fade_ms=0)
    assert order == [('enter', 'first'), ('leave', 'first'), ('enter', 'second'), ('leave', 'second')]
    assert not engine._sessions
