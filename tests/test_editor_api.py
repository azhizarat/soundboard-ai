import copy
import numpy as np
import pytest
import soundfile as sf
from main import SoundboardAPI


@pytest.fixture
def editor(tmp_path):
    api = SoundboardAPI(str(tmp_path / 'data'), start_background_services=False)
    api._hotkeys.enabled = False
    path = tmp_path / 'source.wav'
    sf.write(path, np.full((96000, 2), .1, dtype=np.float32), 48000, subtype='FLOAT')
    clip = api._store.add_imported_clip({'file_path': str(path), 'volume': .6}, rendered=True)
    api.begin_clip_edit(clip['id'])
    yield api, clip['id']
    api._store.close()


def command(api, ident, action, interval=None, **extra):
    return api.edit_clip_command(ident, action, {'interval': interval, **extra})


def test_keep_never_restores_gap_and_history_survives_failed_save(editor, monkeypatch):
    api, ident = editor
    assert command(api, ident, 'remove', [24000, 48000])['editor']['spans'] == [[0, 24000], [48000, 96000]]
    assert command(api, ident, 'keep', [12000, 72000])['editor']['spans'] == [[12000, 24000], [48000, 72000]]
    before = api.get_clip_editor(ident)['editor']
    with monkeypatch.context() as patch:
        patch.setattr(api._store, 'save', lambda: (_ for _ in ()).throw(OSError('disk full')))
        assert not command(api, ident, 'undo')['ok']
    assert api.get_clip_editor(ident)['editor']['spans'] == before['spans']
    assert command(api, ident, 'undo')['editor']['spans'] == [[0, 24000], [48000, 96000]]
    assert command(api, ident, 'redo')['editor']['spans'] == before['spans']
    assert command(api, ident, 'restore', [24000, 48000])['editor']['spans'] == [[12000, 72000]]
    assert api._store.get_clip(ident)['volume'] == .6


def test_text_correction_undo_and_clone_share_source_without_changing_parent(editor):
    api, ident = editor
    temporal = {'alignment': 'aligned', 'words': [{'id': 'a', 'text': 'um', 'start_sample': 0, 'end_sample': 20000},
        {'id': 'b', 'text': 'dois', 'start_sample': 20000, 'end_sample': 48000}], 'segments': [], 'corrections': []}
    api._store.state['edit_drafts'][ident]['fields']['transcript_analysis'] = temporal
    original = copy.deepcopy(api._store.render_context(ident, True)['_revision']['spans'])
    corrected = command(api, ident, 'correct', word_ids=['a', 'b'], text='uma frase unida')
    assert corrected['ok'] and corrected['editor']['result']['text'] == 'uma frase unida'
    assert corrected['editor']['spans'] == original
    assert command(api, ident, 'undo')['editor']['result']['text'] == 'um dois'
    assert command(api, ident, 'redo')['editor']['result']['text'] == 'uma frase unida'
    child = command(api, ident, 'create', [0, 48000])['clip']
    assert child['id'] != ident and child['source_id'] == api._store.get_clip(ident)['source_id']
    assert child['effective_duration'] == 1
    assert 'lufs' in child
    assert api._store.render_context(ident, True)['_revision']['spans'] == original
    assert api.update_clip_edit({'id': ident, 'title': 'Título humano'})['ok']
    assert api.get_clip_editor(ident)['editor']['source_transcript']['corrections'][0]['word_ids'] == ['a', 'b']
    assert api.save_clip_edits({'id': ident})['ok']
    api.begin_clip_edit(ident)
    assert api.get_clip_editor(ident)['editor']['result']['text'] == 'uma frase unida'
    assert api._store.render_context(child['id'])['_revision']['spans'] == [[0, 48000]]


def test_preview_crops_same_render_and_uses_only_monitor(editor, monkeypatch):
    api, ident = editor
    command(api, ident, 'remove', [24000, 48000])
    calls = []
    monkeypatch.setattr(api._audio, 'play_clip', lambda **kwargs: calls.append(kwargs) or 'preview')
    monkeypatch.setattr(api._audio, 'get_session', lambda _: None)
    preview = api.preview_clip_edit(ident, 'junction', None, 0, True)
    assert preview['ok'] and calls[0]['monitor_only'] is True
    context = calls[0]['clip']
    final = api._audio.render_clip(api._store.render_context(ident, True))
    audition = api._audio.render_clip(context)
    assert np.array_equal(audition, final[:48000])
    assert context['volume'] == .6
    assert api._store.snapshot()['edit_drafts'][ident]['revision']['spans'] == [[0, 24000], [48000, 96000]]


def test_short_preview_completion_before_return_keeps_exact_terminal_position(editor, monkeypatch):
    from mixer import CompletionEvent
    api, ident = editor
    def play(**kwargs):
        kwargs['clip']['_release_source']()
        kwargs['on_complete'](CompletionEvent('short-preview', ident, None, 'completed'))
        return 'short-preview'
    monkeypatch.setattr(api._audio, 'play_clip', play)
    monkeypatch.setattr(api._audio, 'get_session', lambda _: None)
    result = api.preview_clip_edit(ident, 'selection', [0, 137])
    assert result['ok']
    status = api.get_editor_playback(result['play_id'])['status']
    assert status['playing'] is False and status['reason'] == 'completed'
    assert status['position_samples'] == status['duration_samples'] == 137
    assert status['position'] == status['duration'] == 137 / 48000
    assert result['status'] == status
    assert not api._store._active_sources
    assert api.get_editor_playback('unknown')['status']['reason'] == 'unknown'


@pytest.mark.parametrize('reason', ['completed', 'cancelled', 'failed'])
def test_preview_terminal_status_retains_reason_and_sample_precision(editor, monkeypatch, reason):
    from audio_engine import PlaybackSession
    from mixer import CompletionEvent
    api, ident = editor
    sessions, callbacks = {}, {}
    def play(**kwargs):
        callbacks.update(kwargs)
        sessions['own-preview'] = PlaybackSession('own-preview', ident, np.zeros((137, 2)), monitor_only=True)
        return 'own-preview'
    monkeypatch.setattr(api._audio, 'play_clip', play)
    monkeypatch.setattr(api._audio, 'get_session', lambda key: sessions.get(key))
    result = api.preview_clip_edit(ident, 'selection', [0, 137])
    assert result['ok']
    session = sessions['own-preview']
    with session._lock:
        session.pos_idx = 73
    active = api.get_editor_playback('own-preview')['status']
    assert active['playing'] and active['reason'] is None
    assert active['position'] == 73 / 48000 and active['duration'] == 137 / 48000
    assert active['position_samples'] == 73 and active['sample_rate'] == 48000
    sessions.clear()
    event = CompletionEvent('own-preview', ident, None, reason, 'Falha sintética' if reason == 'failed' else None)
    with session._lock:
        session.completion = event
        session.stop_event.set()
    callbacks['clip']['_release_source']()
    callbacks['on_complete'](event)
    terminal = api.get_editor_playback('own-preview')['status']
    assert terminal['playing'] is False and terminal['reason'] == reason
    assert terminal['position_known'] is True
    assert terminal['position_samples'] == (137 if reason == 'completed' else 73)
    assert terminal['error'] == event.error
    # Resposta é isolada: consumidores não alteram o cache de outro poll.
    terminal['position'] = 999
    assert api.get_editor_playback('own-preview')['status']['position'] != 999
    assert not api._store._active_sources


def test_preview_terminal_cache_is_bounded_and_contains_metadata_only(editor, monkeypatch):
    import main
    from mixer import CompletionEvent
    api, ident = editor
    counter = 0
    def play(**kwargs):
        nonlocal counter
        counter += 1
        key = f'preview-{counter}'
        kwargs['clip']['_release_source']()
        kwargs['on_complete'](CompletionEvent(key, ident, None, 'cancelled'))
        return key
    monkeypatch.setattr(api._audio, 'play_clip', play)
    monkeypatch.setattr(api._audio, 'get_session', lambda _: None)
    for _ in range(main.EDITOR_PREVIEW_STATUS_LIMIT + 3):
        assert api.preview_clip_edit(ident, 'selection', [0, 137])['ok']
    assert len(api._editor_preview_status) <= main.EDITOR_PREVIEW_STATUS_LIMIT
    assert api.get_editor_playback('preview-1')['status']['reason'] == 'unknown'
    newest = api.get_editor_playback(f'preview-{counter}')['status']
    assert newest['reason'] == 'cancelled' and newest['position_known'] is False
    assert newest['position_samples'] == 0
    for status in api._editor_preview_status.values():
        assert all(value is None or isinstance(value, (bool, int, float, str)) for value in status.values())


def test_unowned_session_query_does_not_leave_stale_playing_cache(editor, monkeypatch):
    from audio_engine import PlaybackSession
    api, ident = editor
    sessions = {'external': PlaybackSession('external', ident, np.zeros((23, 2)), monitor_only=True)}
    monkeypatch.setattr(api._audio, 'get_session', lambda key: sessions.get(key))
    assert api.get_editor_playback('external')['status']['playing']
    sessions.clear()
    status = api.get_editor_playback('external')['status']
    assert status['playing'] is False and status['reason'] == 'unknown'
    assert 'external' not in api._editor_preview_status


def test_terminal_completion_wins_over_an_inflight_active_snapshot(editor, monkeypatch):
    import threading
    from audio_engine import PlaybackSession
    from mixer import CompletionEvent
    api, ident = editor
    session = PlaybackSession('race-preview', ident, np.zeros((137, 2)), monitor_only=True)
    callbacks, sessions = {}, {'race-preview': session}
    def play(**kwargs):
        callbacks.update(kwargs)
        return 'race-preview'
    monkeypatch.setattr(api._audio, 'play_clip', play)
    monkeypatch.setattr(api._audio, 'get_session', lambda key: sessions.get(key))
    assert api.preview_clip_edit(ident, 'selection', [0, 137])['ok']
    captured, release_poll = threading.Event(), threading.Event()
    original = api._editor_session_status
    def delayed(current):
        value = original(current)
        if threading.current_thread().name == 'editor-poll':
            captured.set()
            assert release_poll.wait(2)
        return value
    monkeypatch.setattr(api, '_editor_session_status', delayed)
    responses = []
    poll = threading.Thread(target=lambda: responses.append(api.get_editor_playback('race-preview')), name='editor-poll')
    poll.start()
    try:
        assert captured.wait(2)
        sessions.clear()
        event = CompletionEvent('race-preview', ident, None, 'completed')
        with session._lock:
            session.completion = event
            session.stop_event.set()
        callbacks['clip']['_release_source']()
        callbacks['on_complete'](event)
    finally:
        release_poll.set()
        poll.join(2)
    assert not poll.is_alive()
    assert responses[0]['status']['reason'] == 'completed'
    assert responses[0]['status']['position_samples'] == 137


def test_empty_edit_rejected_and_suggestions_do_not_mutate(editor):
    api, ident = editor
    assert not command(api, ident, 'remove', [0, 96000])['ok']
    before = copy.deepcopy(api._store.snapshot()['edit_drafts'][ident])
    result = api.suggest_clip_edges(ident, .15)
    assert result['ok'] and result['interval'] == [0, 96000]
    assert api._store.snapshot()['edit_drafts'][ident] == before


def test_segment_only_block_can_be_corrected_and_corrected_again_as_unit(editor):
    api, ident = editor
    api._store.state['edit_drafts'][ident]['fields']['transcript_analysis'] = {
        'alignment': 'segments', 'words': [], 'corrections': [],
        'segments': [{'id': 's', 'text': 'texto sem palavras', 'word_ids': [], 'start_sample': 1000, 'end_sample': 90000}]}
    command(api, ident, 'remove', [24000, 48000])
    result = command(api, ident, 'correct', word_ids=['s'], text='bloco corrigido')
    assert result['ok']
    assert result['editor']['result']['text'] == 'bloco corrigido'
    block = result['editor']['source_transcript']['corrections'][0]
    assert block['anchors'] == [[1000, 24000], [48000, 90000]]
    assert command(api, ident, 'correct', word_ids=['s'], text='outra correção')['editor']['result']['text'] == 'outra correção'


def test_clone_failure_rolls_back_without_changing_parent(editor, monkeypatch):
    api, ident = editor
    before = api._store.snapshot()
    monkeypatch.setattr(api._store, 'save', lambda: (_ for _ in ()).throw(OSError('disk full')))
    assert not command(api, ident, 'create', [0, 24000])['ok']
    assert api._store.snapshot() == before


def test_recovered_draft_keeps_montage_and_text_but_late_job_cannot_apply(editor):
    from store import SoundboardStore
    api, ident = editor
    token = api._store.prepare_analysis(ident, 'metadata')
    command(api, ident, 'remove', [24000, 48000])
    command(api, ident, 'undo')
    assert api._store.apply_analysis(token, {'fields': {'title': 'tardio'}})['state'] == 'obsolete'
    command(api, ident, 'remove', [24000, 48000])
    command(api, ident, 'legacy_text', text='texto sem tempos corrigido')
    recovered = SoundboardStore(str(api._store.data_dir))
    try:
        context = recovered.render_context(ident, True)
        assert context['_revision']['spans'] == [[0, 24000], [48000, 96000]]
        assert context['transcript'] == 'texto sem tempos corrigido'
    finally:
        recovered.close()


def test_monitor_stop_does_not_stop_transmitting_session(editor, monkeypatch):
    from types import SimpleNamespace
    api, _ = editor
    stopped = []
    sessions = {'monitor': SimpleNamespace(monitor_only=True, stop=lambda: stopped.append('monitor')),
                'cable': SimpleNamespace(monitor_only=False, stop=lambda: stopped.append('cable'))}
    monkeypatch.setattr(api._audio, 'get_session', lambda ident: sessions.get(ident))
    for ident in ['monitor', 'cable', 'gone']:
        assert api.stop_editor_preview(ident)['ok']
    assert stopped == ['monitor']


def test_meter_and_commit_preserve_frozen_audio_gains(editor):
    api, ident = editor
    revision=api._store.state['edit_drafts'][ident]['revision']
    revision['gain_db']=3.5
    revision['normalization_gain']=.75
    before=api._audio.render_clip(api._store.render_context(ident,True))
    assert api.get_clip_editor(ident)['ok']
    assert api.save_clip_edits({'id':ident})['ok']
    context=api._store.render_context(ident)
    assert context['_revision']['gain_db']==3.5
    assert context['_revision']['normalization_gain']==.75
    assert np.array_equal(before,api._audio.render_clip(context))


@pytest.mark.parametrize('failure', [None, 'render', 'mixer'])
def test_real_engine_preview_preserves_broadcast_and_external_monitor(editor, monkeypatch, failure):
    from mixer import AudioMixer, DeviceMixer
    api, ident = editor
    class FakeMixer(AudioMixer):
        def __init__(self):
            super().__init__()
            self.voices=[]
            self.stops=[]
            self.routes=[]
        def prepare_group(self, group_id, items, settings, monitor_only=False, **kwargs):
            self.routes.append((copy.deepcopy(settings),monitor_only))
            if failure=='mixer' and items[0]['clip'].get('_preview_ranges'):
                raise ValueError('Rota indisponível')
            group=super().prepare_group(group_id,items,settings,monitor_only=monitor_only,**kwargs)
            self.voices.extend(group.voices)
            return group
        def stop_all(self, fade_ms):
            self.stops.append(fade_ms)
            super().stop_all(fade_ms)
    monkeypatch.setattr(DeviceMixer,'ensure_stream',lambda self:True)
    mixer=FakeMixer()
    monkeypatch.setattr(api._audio,'_mixer',mixer)
    monkeypatch.setattr(api._audio,'_output_route_usable',lambda _:True)
    monkeypatch.setattr('mixer.is_cable_output_device',lambda _:False)
    api._store.update_settings({'overlap_audio':False,'cable_device_id':1,'monitor_device_id':2})
    saved_settings=api._store.snapshot()['settings']
    context=api._store.render_context(ident,True)
    broadcast=api._audio.play_clip(context,{**saved_settings,'overlap_audio':True},monitor_only=False)
    external=api._audio.play_clip(context,{**saved_settings,'overlap_audio':True},monitor_only=True)
    previous=[api._audio.get_session(broadcast),api._audio.get_session(external)]
    if failure=='render':
        monkeypatch.setattr(api._audio,'render_clip',lambda *_:(_ for _ in ()).throw(ValueError('render failed')))
    result=api.preview_clip_edit(ident,'result')
    try:
        assert result['ok'] is (failure is None)
        assert mixer.stops==[]
        assert api._audio.get_session(broadcast) is previous[0]
        assert api._audio.get_session(external) is previous[1]
        assert not any(voice.stopping or voice.stop_event.is_set() for voice in previous)
        assert api._store.snapshot()['settings']==saved_settings
        if failure!='render':
            assert mixer.routes[-1][1] is True
            assert mixer.routes[-1][0]['cable_device_id'] is None
    finally:
        api._audio.stop_all()
