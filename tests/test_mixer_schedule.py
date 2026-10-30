"""E7: relógio real de amostras; streams falsos, sem hardware."""
import numpy as np
import pytest

from mixer import AudioMixer, DeviceMixer, CEILING_LINEAR


@pytest.fixture
def mixer(monkeypatch):
    monkeypatch.setattr(DeviceMixer, 'ensure_stream', lambda self: True)
    monkeypatch.setattr('mixer.is_cable_output_device', lambda _: False)
    return AudioMixer()


SETTINGS = {'cable_device_id': 1, 'monitor_device_id': 2,
            'cable_volume': 1., 'monitor_volume': 1.}


def items():
    return [{'clip': {'id': 'same'}, 'data': np.full((4, 2), .2), 'offset_samples': 0},
            {'clip': {'id': 'same'}, 'data': np.full((4, 2), .3), 'offset_samples': 2}]


def test_group_common_start_and_offsets_use_each_output_clock(mixer):
    mixer.cable_mixer.render_block(7)
    mixer.monitor_mixer.render_block(11)
    group = mixer.prepare_group('g', items(), SETTINGS)
    assert not mixer.cable_mixer._voices
    mixer.start_group(group, delay_samples=3)
    assert group.start_samples == {'cable': 10, 'monitor': 14}
    for device in (mixer.cable_mixer, mixer.monitor_mixer):
        rendered = device.render_block(10)[:, 0]
        np.testing.assert_allclose(rendered, [0, 0, 0, .2, .2, .5, .5, .3, .3, 0], atol=1e-6)
    assert len({v.play_id for v in group.voices}) == 2
    assert not mixer._voices


def test_group_preparation_rejects_bad_last_voice_without_publishing(mixer):
    data = items()
    data[-1]['data'][0, 0] = np.nan
    with pytest.raises(ValueError):
        mixer.prepare_group('g', data, SETTINGS)
    assert not mixer.cable_mixer._voices
    assert not mixer.monitor_mixer._voices


def test_group_requires_all_requested_routes(mixer, monkeypatch):
    monkeypatch.setattr(mixer.monitor_mixer, 'ensure_stream', lambda: False)
    with pytest.raises(ValueError):
        mixer.prepare_group('g', items(), SETTINGS)
    assert not mixer.cable_mixer._voices


def test_route_loss_fails_whole_group_and_reconnect_does_not_resurrect(mixer):
    events = []
    group = mixer.prepare_group('g', items(), SETTINGS, on_complete=events.append)
    mixer.start_group(group, delay_samples=100)
    mixer.monitor_mixer.invalidate_route('disconnected')
    assert len(events) == 2
    assert {event.reason for event in events} == {'failed'}
    assert not mixer._voices
    mixer.monitor_mixer.ensure_stream()
    assert not np.any(mixer.monitor_mixer.render_block(200))
    assert not np.any(mixer.cable_mixer.render_block(200))


def test_prepared_route_generation_cannot_be_replaced(mixer):
    group = mixer.prepare_group('g', items(), SETTINGS)
    mixer.monitor_mixer.invalidate_route('changed')
    with pytest.raises(ValueError):
        mixer.start_group(group)
    assert not mixer.cable_mixer._voices


def test_simultaneous_voices_duck_limit_and_short_fade(mixer):
    group = mixer.prepare_group('g', [dict(item, data=np.full((50, 2), 2.)) for item in items()], SETTINGS)
    mixer.start_group(group)
    device = mixer.cable_mixer
    device._prev_duck = .1
    np.testing.assert_allclose(device.render_block(4, duck_gain=.1)[2:], .4, atol=1e-6)
    assert np.max(np.abs(device.render_block(4))) <= CEILING_LINEAR
    mixer.stop_group('g', fade_ms=1/48)
    faded = device.render_block(8)
    assert np.all(faded[1:] == 0)


def test_atomic_publication_blocks_render_until_all_voices_visible(mixer, monkeypatch):
    import threading
    group = mixer.prepare_group('g', items(), SETTINGS)
    entered, finished, rendered = threading.Event(), threading.Event(), []
    def renderer():
        entered.set()
        rendered.append(mixer.cable_mixer.render_block(8))
        finished.set()
    original = mixer.cable_mixer.add_voice
    workers = []
    def add(voice, **kwargs):
        original(voice, **kwargs)
        if not workers:
            workers.append(threading.Thread(target=renderer))
            workers[0].start()
            assert entered.wait(1)
            assert not finished.is_set()
    monkeypatch.setattr(mixer.cable_mixer, 'add_voice', add)
    mixer.start_group(group)
    workers[0].join(2)
    assert finished.is_set()
    np.testing.assert_allclose(rendered[0][:, 0], [.2, .2, .5, .5, .3, .3, 0, 0], atol=1e-6)


def test_device_stream_finished_callback_invalidates_group_without_rerouting(mixer, monkeypatch):
    import sys
    from types import SimpleNamespace
    streams, events = [], []
    class Stream:
        def __init__(self, **kwargs):
            self.finished = kwargs['finished_callback']
            self.active = True
            self.samplerate = 48000
            streams.append(self)
        def start(self):
            pass
        def stop(self):
            self.active = False
        def close(self):
            pass
    monkeypatch.setitem(sys.modules, 'sounddevice', SimpleNamespace(OutputStream=Stream))
    monkeypatch.setattr(DeviceMixer, 'ensure_stream', DeviceMixer.start_stream)
    monkeypatch.setattr(DeviceMixer, '_preferred_samplerate', lambda self: 48000)
    group = mixer.prepare_group('g', items(), SETTINGS, on_complete=events.append)
    mixer.start_group(group)
    streams[1].active = False
    streams[1].finished()
    assert {event.reason for event in events} == {'failed'}
    assert len(events) == 2
    assert not mixer.cable_mixer._voices and not mixer.monitor_mixer._voices
    assert mixer.monitor_mixer.ensure_stream()
    assert not np.any(mixer.monitor_mixer.render_block(16))
    mixer.shutdown()


def test_output_duck_ramp_is_shared_across_offset_voices(mixer):
    group = mixer.prepare_group('g', items(), SETTINGS)
    mixer.start_group(group)
    rendered = mixer.cable_mixer.render_block(8, duck_gain=0)[:, 0]
    expected = np.array([.2, .2, .5, .5, .3, .3, 0, 0]) * np.linspace(1, 0, 8)
    np.testing.assert_allclose(rendered, expected, atol=1e-6)


def test_concurrent_output_reconfiguration_never_accepts_another_device(mixer, monkeypatch):
    def changed():
        mixer.monitor_mixer.device_id = 999
        mixer.monitor_mixer.generation += 1
        return True
    monkeypatch.setattr(mixer.monitor_mixer, 'ensure_stream', changed)
    with pytest.raises(ValueError, match='alterad'):
        mixer.prepare_group('g', items(), SETTINGS)
    assert not mixer._voices


def test_route_loss_of_standalone_does_not_treat_missing_owner_as_shared_group(mixer):
    events = []
    cable = mixer.prepare_group(None, items()[:1], dict(SETTINGS, monitor_volume=0), on_complete=events.append)
    monitor = mixer.prepare_group(None, items()[:1], SETTINGS, monitor_only=True, on_complete=events.append)
    mixer.start_group(cable)
    mixer.start_group(monitor)
    mixer.cable_mixer.invalidate_route('lost')
    assert len(events) == 1 and events[0].play_id == cable.voices[0].play_id
    assert not monitor.voices[0].stop_event.is_set()
    np.testing.assert_allclose(mixer.monitor_mixer.render_block(4), .2, atol=1e-6)
    assert [event.reason for event in events] == ['failed', 'completed']


@pytest.fixture
def racing_streams(monkeypatch):
    import sys
    import threading
    from types import SimpleNamespace
    entered, release = threading.Event(), threading.Event()
    streams = []
    class Stream:
        def __init__(self, **kwargs):
            self.device = kwargs['device']
            self.callback = kwargs['callback']
            self.finished = kwargs['finished_callback']
            self.samplerate = 48000
            self.active = False
            self.closed = False
            streams.append(self)
            if self.device == 1:
                entered.set()
                assert release.wait(3)
        def start(self):
            self.active = True
            self.start_output = self.render(4)
        def stop(self):
            self.active = False
        def close(self):
            self.closed = True
            # PortAudio pode notificar encerramento em outra thread durante close.
            worker = threading.Thread(target=self.finished)
            worker.start()
            worker.join(1)
            assert not worker.is_alive()
        def render(self, frames):
            output = np.empty((frames, 2), dtype=np.float32)
            self.callback(output, frames, None, None)
            return output
    monkeypatch.setitem(sys.modules, 'sounddevice', SimpleNamespace(OutputStream=Stream))
    monkeypatch.setattr(DeviceMixer, '_preferred_samplerate', lambda self: 48000)
    monkeypatch.setattr('mixer.is_cable_output_device', lambda _: False)
    return SimpleNamespace(entered=entered, release=release, streams=streams, stream_class=Stream)


def test_concurrent_open_cannot_install_wrong_handle_or_leak_loser(racing_streams):
    import threading
    mixer = AudioMixer()
    prepared, errors = {}, []
    def prepare(name, device):
        try:
            prepared[name] = mixer.prepare_group(name, [{'clip': {'id': name}, 'data': np.full((8, 2), .3)}],
                                                dict(SETTINGS, cable_device_id=device, monitor_volume=0))
        except ValueError as exc:
            errors.append(str(exc))
    first = threading.Thread(target=prepare, args=('a', 1))
    second = threading.Thread(target=prepare, args=('b', 2))
    first.start()
    assert racing_streams.entered.wait(2)
    second.start()
    second.join(2)
    assert not second.is_alive() and 'b' in prepared
    mixer.start_group(prepared['b'])
    racing_streams.release.set()
    first.join(2)
    assert not first.is_alive() and len(errors) == 1
    by_device = {stream.device: stream for stream in racing_streams.streams}
    assert mixer.cable_mixer._stream is by_device[2]
    assert by_device[1].closed
    assert not np.any(by_device[1].start_output)
    assert not np.any(by_device[2].start_output)
    assert prepared['b'].voices[0].pos_idx == 0
    generation = mixer.cable_mixer.generation
    by_device[1].finished()
    assert mixer.cable_mixer.generation == generation
    assert not prepared['b'].voices[0].stop_event.is_set()
    assert not np.any(by_device[1].render(4))
    np.testing.assert_allclose(by_device[2].render(4), .3, atol=1e-6)
    mixer.shutdown()
    assert all(stream.closed for stream in racing_streams.streams)


def test_shutdown_during_open_invalidates_reservation_and_closes_late_handle(racing_streams):
    import threading
    mixer, errors = AudioMixer(), []
    def prepare():
        try:
            mixer.prepare_group('a', items(), dict(SETTINGS, monitor_volume=0))
        except ValueError as exc:
            errors.append(str(exc))
    worker = threading.Thread(target=prepare)
    worker.start()
    assert racing_streams.entered.wait(2)
    mixer.shutdown()
    racing_streams.release.set()
    worker.join(2)
    assert not worker.is_alive() and len(errors) == 1
    assert mixer.cable_mixer._stream is None
    assert all(stream.closed for stream in racing_streams.streams)
    assert not mixer._groups and not mixer._voices
    with pytest.raises(ValueError):
        mixer.prepare_group('after-shutdown', items(), dict(SETTINGS, monitor_volume=0))


def test_failed_stream_start_closes_constructed_handle(racing_streams, monkeypatch):
    mixer = AudioMixer()
    def failed_start(self):
        raise RuntimeError('start failed')
    monkeypatch.setattr(racing_streams.stream_class, 'start', failed_start)
    with pytest.raises(ValueError):
        mixer.prepare_group('a', items(), dict(SETTINGS, cable_device_id=2, monitor_volume=0))
    assert len(racing_streams.streams) == 1
    assert racing_streams.streams[0].closed


def test_stream_finished_during_start_cannot_be_installed(racing_streams, monkeypatch):
    mixer = AudioMixer()
    def finished_start(self):
        self.active = False
        self.finished()
    monkeypatch.setattr(racing_streams.stream_class, 'start', finished_start)
    with pytest.raises(ValueError):
        mixer.prepare_group('a', items(), dict(SETTINGS, cable_device_id=2, monitor_volume=0))
    assert mixer.cable_mixer._stream is None
    assert racing_streams.streams[0].closed


def test_stream_closed_between_health_check_and_freezing_route_is_rejected(racing_streams, monkeypatch):
    mixer = AudioMixer()
    original = mixer.cable_mixer.ensure_stream
    def closed_after_check():
        result = original()
        mixer.cable_mixer.stop_stream()
        return result
    monkeypatch.setattr(mixer.cable_mixer, 'ensure_stream', closed_after_check)
    with pytest.raises(ValueError, match='Handle'):
        mixer.prepare_group('a', items(), dict(SETTINGS, cable_device_id=2, monitor_volume=0))
    assert not mixer._groups
    assert all(stream.closed for stream in racing_streams.streams)
