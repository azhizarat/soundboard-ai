import copy
import importlib.util

import numpy as np
import pytest
import soundfile as sf

from test_editor_api import editor
from test_trigger_policy import operation


@pytest.fixture(autouse=True)
def no_native_audio(monkeypatch):
    from mixer import DeviceMixer
    # Mesmo uma regressão de mock nunca abre dispositivo nesta suíte sintética.
    monkeypatch.setattr(DeviceMixer, 'ensure_stream', lambda self: True)


def variant_module():
    assert importlib.util.find_spec('audio_variants'), 'Processamento de variantes ausente'
    import audio_variants
    return audio_variants


def frequency(samples):
    signal = samples[4800:-4800, 0]
    return np.argmax(abs(np.fft.rfft(signal))) * 48000 / len(signal)


def test_speed_pitch_reverse_have_exact_duration_and_independent_frequency():
    variants = variant_module()
    signal = np.sin(np.arange(96000)*2*np.pi*440/48000).astype('float32')[:,None]*.2
    signal = np.repeat(signal, 2, axis=1)
    fast = variants.apply(signal, {'speed': 2})
    high = variants.apply(signal, {'pitch_semitones': 12})
    assert len(fast) == 48000 and abs(frequency(fast)-440) < 3
    assert len(high) == 96000 and abs(frequency(high)-880) < 3
    np.testing.assert_array_equal(variants.apply(signal, {'reverse':True}), signal[::-1])
    for bad in [{'speed':0}, {'speed':True}, {'pitch_semitones':float('nan')}, {'reverse':1}, {'unknown':1}]:
        with pytest.raises(ValueError): variants.apply(signal, bad)


def test_variant_revision_preserves_parent_and_voice_uses_original(editor, tmp_path):
    api, ident = editor
    assert hasattr(api, 'create_audio_variant'), 'API de variantes ausente'
    assert api.save_clip_edits({'id':ident})['ok']
    parent = copy.deepcopy(api._store.get_clip(ident))
    api._store.get_clip(ident)['voice_action_id'] = 'parent-action'
    result = api.create_audio_variant(ident, {'speed':2, 'reverse':True}, parent['revision_id'])
    assert result['ok'], result
    child = result['clip']; context = api._store.render_context(child['id'])
    assert child['source_id'] == parent['source_id'] and 'voice_action_id' not in child
    assert context['_revision']['variant_origin']['revision_id'] == parent['revision_id']
    assert child['effective_duration'] == 1 and child['lufs'] is not None
    assert len(api._audio.render_clip(context)) == 48000
    from clip_renderer import selected_source_file, render_revision
    with selected_source_file(context['_source'], context['_revision'], tmp_path) as path:
        original, _ = sf.read(path, always_2d=True, dtype='float32')
    expected = render_revision(context['_source'], {**context['_revision'], 'variant':{}, 'gain_db':0, 'normalization_gain':1,'fade_samples':0})
    np.testing.assert_array_equal(original, expected)
    api.begin_clip_edit(child['id'])
    state = api.get_clip_editor(child['id'])['editor']
    assert state['duration'] == 1 and state['variant']['speed'] == 2
    assert api.get_clip_waveform(child['id'])['duration'] == 1
    assert api.get_edit_source_waveform(child['id'])['duration'] == 2


def test_variant_projection_and_preview_ranges():
    variants = variant_module()
    assert variants.map_ranges([[0,24000]], 96000, {'speed':2,'reverse':True}) == [[36000,48000]]
    from transcript_model import project
    model = {'alignment':'aligned','words':[{'id':'w','text':'olá','start_sample':0,'end_sample':24000}]}
    result = variants.project(model, [[0,96000]], {'speed':2,'reverse':True})
    assert result['words'][0]['output_start_sample'] == 36000
    assert result['words'][0]['output_end_sample'] == 48000
    assert result['words'][0]['start_sample'] == 0


def test_variant_invalid_or_failed_save_has_no_partial_publication(editor, monkeypatch):
    api, ident = editor
    assert hasattr(api, 'create_audio_variant'), 'API de variantes ausente'
    api.save_clip_edits({'id':ident})
    before = api._store.snapshot()
    assert not api.create_audio_variant(ident, {'speed':3})['ok']
    monkeypatch.setattr(api._store,'save',lambda: (_ for _ in ()).throw(OSError('disk full')))
    assert not api.create_audio_variant(ident, {'reverse':True})['ok']
    assert api._store.snapshot() == before


def test_sequence_pins_processed_duration_and_retains_render_after_edit(editor):
    api, ident = editor
    api.save_clip_edits({'id':ident})
    child = api.create_audio_variant(ident, {'speed':2,'reverse':True})['clip']
    pinned = api.pin_sequence_clip(child['id'])
    assert pinned['ok'] and pinned['occurrence']['interval'] == [0,48000]
    original = api._audio.render_clip(api._store.resolve_sequence_occurrence(pinned['occurrence']))
    choices = api._store.sequence_clip_choices()
    assert next(c for c in choices if c['id']==child['id'])['samples'] == 48000
    api.begin_clip_edit(child['id'])
    api._store.update_edit(child['id'], {'spans':[[0,48000]]})
    assert api.save_clip_edits({'id':child['id']})['ok']
    np.testing.assert_array_equal(api._audio.render_clip(api._store.resolve_sequence_occurrence(pinned['occurrence'])),original)
    with pytest.raises(ValueError):
        api._store.resolve_sequence_occurrence({**pinned['occurrence'],'interval':[0,96000]})


def test_configured_processor_controls_capabilities_creation_and_playback(editor, monkeypatch):
    api, ident = editor
    api.save_clip_edits({'id':ident})
    child = api.create_audio_variant(ident, {'speed':2})['clip']
    monkeypatch.setattr(api._audio, 'ffmpeg_path', 'missing-e10a-ffmpeg')
    assert api.get_variant_capabilities()['speed'] is False
    assert api.create_audio_variant(ident, {'speed':2})['ok'] is False
    with pytest.raises(ValueError, match='FFmpeg'):
        api._audio.render_clip(api._store.render_context(child['id']))
    assert api.create_audio_variant(ident, {'reverse':True})['ok']


def test_variant_preview_requires_monitor_before_any_preparation(editor, monkeypatch):
    api, ident = editor
    api.save_clip_edits({'id':ident})
    api._store.update_settings({'monitor_device_id':None})
    monkeypatch.setattr(api._audio, 'prepare_group', lambda *a, **kw: pytest.fail('Sem monitor não deve preparar'))
    assert not api.preview_audio_variant(ident, {'reverse':True})['ok']
    assert not api._store._active_sources


def test_compact_pack_identity_distinguishes_effect_and_audio_round_trips(editor, tmp_path):
    import packs
    import zipfile
    import io
    api, ident = editor
    api.save_clip_edits({'id':ident})
    child = api.create_audio_variant(ident, {'speed':2})['clip']
    context = api._store.render_context(child['id'])
    changed = copy.deepcopy(context);changed['_revision']['variant']['speed']=.5
    assert packs.montage_identity(context) != packs.montage_identity(changed)
    dest=tmp_path/'effects.zip';packs.write_pack(dest,[context],{})
    with zipfile.ZipFile(dest) as archive:
        audio_name=next(name for name in archive.namelist() if name.endswith('.wav'))
        audio,rate=sf.read(io.BytesIO(archive.read(audio_name)),always_2d=True,dtype='float32')
    assert rate==48000
    np.testing.assert_array_equal(audio,api._audio.render_clip(context)*context['volume'])


def test_v5_upgrade_preserves_data_and_verifies_backup(tmp_path, monkeypatch):
    import json
    from store import SoundboardStore
    path=tmp_path/'soundboard.json'
    original=json.dumps({'schema_version':5,'clips':[],'settings':{},'collections':{'c':{'id':'c'}}})
    path.write_text(original,encoding='utf-8')
    store=SoundboardStore(str(tmp_path))
    try:
        assert store.snapshot()['schema_version']==6
        assert store.snapshot()['collections']=={'c':{'id':'c'}}
        assert (tmp_path/'soundboard.v5.variants.bak.json').read_text(encoding='utf-8')==original
    finally: store.close()
    path.write_text(original,encoding='utf-8')
    (tmp_path/'soundboard.v5.variants.bak.json').write_text('{}',encoding='utf-8')
    with pytest.raises(ValueError,match='Backup'): SoundboardStore(str(tmp_path))
    assert path.read_text(encoding='utf-8')==original


def test_variant_preview_token_release_during_preparation_cannot_start(operation, monkeypatch):
    api,ident=operation
    prepare=api._audio.prepare_group
    def cancel(*args, **kwargs):
        assert api.stop_variant_preview('variant-gesture')['ok']
        return prepare(*args, **kwargs)
    monkeypatch.setattr(api._audio,'prepare_group',cancel)
    result=api.preview_audio_variant(ident,{'speed':2},token='variant-gesture')
    assert not result['ok'] and result['cancelled']
    assert not api._audio._sessions and not api._store._active_sources


def test_variant_preview_early_release_and_stop_all(operation, monkeypatch):
    api,ident=operation
    assert api.stop_variant_preview('early')['ok']
    assert not api.preview_audio_variant(ident,{'reverse':True},token='early')['ok']
    prepare=api._audio.prepare_group
    def cancel(*args,**kwargs):
        api.stop_all();return prepare(*args,**kwargs)
    monkeypatch.setattr(api._audio,'prepare_group',cancel)
    result=api.preview_audio_variant(ident,{'reverse':True},token='epoch')
    assert not result['ok']
    assert not api._audio._sessions and not api._store._active_sources


def test_accelerated_selection_without_output_samples_never_calls_playback(editor, monkeypatch):
    api, ident = editor
    assert api.save_clip_edits({'id': ident})['ok']
    child = api.create_audio_variant(ident, {'speed': 2})['clip']
    assert api.begin_clip_edit(child['id'])['ok']
    calls = []
    monkeypatch.setattr(api._audio, 'play_clip', lambda **kwargs: calls.append(kwargs) or 'unexpected')
    monkeypatch.setattr(api._audio, 'get_session', lambda _: None)
    result = api.preview_clip_edit(child['id'], 'selection', [0, 1])
    assert not result['ok'], result
    assert not calls and not api._store._active_sources


def test_explicit_empty_preview_range_is_not_an_instruction_to_play_everything(editor):
    api, ident = editor
    context = api._store.render_context(ident, True)
    context['_preview_ranges'] = []
    with pytest.raises(ValueError):
        api._audio.render_clip(context)


def test_previous_revision_variant_projects_human_text_and_compact_pack_consistently(editor, tmp_path):
    import json
    import packs
    import zipfile
    api, ident = editor
    temporal = {'alignment': 'aligned', 'words': [
        {'id': 'first', 'text': 'primeira', 'start_sample': 0, 'end_sample': 48000},
        {'id': 'second', 'text': 'segunda', 'start_sample': 48000, 'end_sample': 96000}],
        'segments': [], 'corrections': []}
    api._store.state['edit_drafts'][ident]['fields']['transcript_analysis'] = temporal
    # Segmentos antigos não podem sobrescrever a correção textual ancorada.
    api._store.state['edit_drafts'][ident]['fields']['segments'] = [
        {'start': 0, 'end': 1, 'text': 'primeira', 'speaker_id': 'a'},
        {'start': 1, 'end': 2, 'text': 'segunda', 'speaker_id': 'b'}]
    api._store.update_edit(ident, {'spans': [[0, 48000]]})
    first = api.save_clip_edits({'id': ident})['clip']
    api.begin_clip_edit(ident)
    api._store.update_edit(ident, {'spans': [[48000, 96000]]})
    assert api.save_clip_edits({'id': ident})['clip']['transcript'] == 'segunda'
    # Correção humana pertence à origem e é válida também na revisão anterior.
    current = api._store.get_clip(ident)
    current['transcript_analysis']['corrections'] = [
        {'id': 'correction:first', 'word_ids': ['first'], 'text': 'primeira corrigida',
         'start_sample': 0, 'end_sample': 48000, 'anchors': [[0, 48000]], 'provenance': 'human'}]
    current['voice_source_evidence'] = {'multiple_voices': True, 'provenance': 'observed'}
    before = copy.deepcopy(current)
    child = api.create_audio_variant(ident, {'reverse': True}, first['revision_id'])['clip']
    assert child['transcript'] == api._store.get_transcript(child['id'])['text'] == 'primeira corrigida'
    assert child['voice_source_evidence'] == before['voice_source_evidence']
    assert child['segments'] == before['segments']
    assert api._store.get_clip(ident) == before
    destination = tmp_path / 'variant.zip'
    packs.write_pack(destination, [api._store.render_context(child['id'])], {})
    with zipfile.ZipFile(destination) as archive:
        manifest = json.loads(archive.read('manifest.json'))
    assert manifest['clips'][0]['transcript'] == child['transcript']
