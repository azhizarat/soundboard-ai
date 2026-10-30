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
