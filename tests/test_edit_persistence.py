import copy
import json
from pathlib import Path
import numpy as np
import pytest
import soundfile as sf
from store import SoundboardStore


@pytest.fixture
def store(tmp_path):
    obj = SoundboardStore(str(tmp_path / 'data'))
    yield obj
    obj.close()


def clip(store, name='one', pending=False):
    path = (store.tmp_dir if pending else store.clips_dir) / 'original.wav'
    sf.write(path, np.full((48000, 2), .1), 48000, subtype='FLOAT')
    return store.add_or_update_clip({'id': name, 'file_path': str(path), 'duration': 1, 'pending': pending})


def test_explicit_ids_never_merge_same_path(store):
    first = clip(store)
    second = store.add_or_update_clip({**first, 'id': 'two', 'trim_start': .2})
    assert first['id'] == 'one' and second['id'] == 'two'
    assert len(store.snapshot()['clips']) == 2


@pytest.mark.parametrize('version', [None, 0, 1])
def test_migration_backup_and_missing_media(tmp_path, version):
    root = tmp_path / 'old'; root.mkdir()
    raw = {'clips': [{'id': 'missing', 'file_path': str(root / 'gone.wav'), 'transcript': 'old', 'speaker_id': 'a'}]}
    if version is not None:
        raw['schema_version'] = version
    db = root / 'soundboard.json'; original = json.dumps(raw); db.write_text(original)
    obj = SoundboardStore(str(root))
    try:
        state = obj.snapshot()
        assert state['schema_version'] == 6
        assert db.with_suffix('.json.bak').read_text() == original
        item = state['clips'][0]
        assert state['sources'][item['source_id']]['available'] is False
        assert item['field_provenance']['transcript'] == 'unknown'
        assert item['field_provenance']['speaker_id'] == 'unknown'
    finally:
        obj.close()


def test_future_schema_rejected_without_overwrite(tmp_path):
    db = tmp_path / 'soundboard.json'; raw = '{"schema_version": 999, "clips": []}'; db.write_text(raw)
    with pytest.raises(ValueError, match='versão'):
        SoundboardStore(str(tmp_path))
    assert db.read_text() == raw


def test_durable_draft_restart_commit_discard_and_source_sharing(store):
    item = clip(store, pending=True)
    assert hasattr(store, 'begin_edit'), 'Rascunhos duráveis ausentes'
    draft = store.begin_edit(item['id'])
    source = store.snapshot()['sources'][draft['source_id']]
    assert Path(source['file_path']).is_relative_to(store.clips_dir)
    store.update_edit(item['id'], {'trim_start': .25, 'trim_end': .75, 'title': 'alterado'})
    store.purge_pending_clips()
    assert store.get_clip(item['id']) is not None
    reopened = SoundboardStore(str(store.data_dir))
    try:
        assert reopened.begin_edit(item['id'])['fields']['title'] == 'alterado'
        saved = reopened.commit_edit(item['id'])
        assert saved['revision_id'] in reopened.snapshot()['revisions']
        assert saved['pending'] is False
        assert reopened.snapshot()['revisions'][saved['revision_id']]['spans'] == [[12000, 36000]]
        reopened.begin_edit(item['id']); reopened.update_edit(item['id'], {'title': 'descartar'})
        reopened.discard_edit(item['id'])
        assert reopened.get_clip(item['id'])['title'] == 'alterado'
        second = reopened.add_or_update_clip({**saved, 'id': 'other'})
        reopened.delete_clip(saved['id'], delete_file=True)
        assert Path(source['file_path']).exists() and reopened.get_clip(second['id'])
    finally:
        reopened.close()


def test_failed_publish_retains_previous_clip_and_edit(store, monkeypatch):
    item = clip(store)
    assert hasattr(store, 'begin_edit'), 'Transação de edição ausente'
    store.begin_edit(item['id']); store.update_edit(item['id'], {'title': 'novo'})
    before = copy.deepcopy(store.get_clip(item['id']))
    disk = store.db_path.read_bytes()
    with monkeypatch.context() as patch:
        patch.setattr(store, '_perform_save', lambda: (_ for _ in ()).throw(OSError('disk full')))
        with pytest.raises(OSError):
            store.commit_edit(item['id'])
    assert store.get_clip(item['id']) == before
    assert store.db_path.read_bytes() == disk
    assert store.begin_edit(item['id'])['fields']['title'] == 'novo'


def test_source_reference_prevents_collection(store):
    item = clip(store)
    assert hasattr(store, 'begin_edit'), 'Contabilidade de fontes ausente'
    draft = store.begin_edit(item['id']); sid = draft['source_id']
    handle = store.acquire_source(sid)
    assert 'active' in store.source_references(sid)
    assert store.collect_source(sid) is False
    store.release_source(handle)
    store.set_source_reference('voice-sample', 'sample-1', sid)
    assert 'voice-sample:sample-1' in store.source_references(sid)
    assert store.collect_source(sid) is False


def test_api_edit_reopen_waveform_preview_save_and_restore(tmp_path, monkeypatch):
    from main import SoundboardAPI
    from clip_renderer import render_revision
    api = SoundboardAPI(str(tmp_path / 'api'), start_background_services=False)
    api._hotkeys.enabled = False
    try:
        item = clip(api._store)
        assert hasattr(api, 'begin_clip_edit'), 'Editor não integrado à API'
        assert api.begin_clip_edit(item['id'])['ok']
        assert api.update_clip_edit({'id': item['id'], 'trim_start': .25, 'trim_end': .75})['ok']
        assert api.begin_clip_edit(item['id'])['clip']['trim_start'] == .25
        result = api.save_clip_edits({'id': item['id'], 'title': 'montagem'})
        assert result['ok'] and result['clip']['revision_id']
        context = api._store.render_context(item['id'])
        expected = render_revision(context['_source'], context['_revision'])
        assert api.get_clip_waveform(item['id'])['duration'] == .5
        played = []
        monkeypatch.setattr(api._audio, 'play_clip', lambda **kwargs: played.append(kwargs['clip']) or 'playing')
        assert api.play_clip(item['id'])['ok']
        assert np.array_equal(api._audio.render_clip(played[0], {'normalize_on_import': False}), expected)
        assert np.array_equal(api._audio.render_clip(played[0], {'normalize_on_import': True}), expected)
        other = api._store.add_or_update_clip({**result['clip'], 'id': 'other'})
        removed = api.delete_clip(item['id'])
        assert api.restore_clip(removed['clip'])['ok']
        assert api._store.get_clip(other['id'])
    finally:
        api._store.close()


def test_promotion_save_failure_keeps_replay_file_and_record(store, monkeypatch):
    item = clip(store, pending=True)
    before = copy.deepcopy(item); raw = Path(item['file_path']).read_bytes()
    with monkeypatch.context() as patch:
        patch.setattr(store, '_perform_save', lambda: (_ for _ in ()).throw(OSError('disk')))
        with pytest.raises(OSError):
            store.begin_edit(item['id'])
    assert store.get_clip(item['id']) == before
    assert Path(item['file_path']).read_bytes() == raw
    assert not store.snapshot()['edit_drafts']


def test_v2_roundtrip_keeps_all_records_and_provenance(store):
    item = clip(store)
    store.begin_edit(item['id']); store.update_edit(item['id'], {'title': 'persistido'})
    store.state['sources']['unavailable'] = {'id': 'unavailable', 'available': False}
    store.state['revisions']['unreferenced'] = {'id': 'unreferenced', 'source_id': 'unavailable'}
    store.state['clips'][0]['field_provenance'] = {'transcript': 'unknown'}
    store.save()
    before = store.snapshot()
    other = SoundboardStore(str(store.data_dir))
    try:
        assert other.snapshot() == before
    finally:
        other.close()


def test_pack_preserves_two_montages_and_reimport_identity(tmp_path):
    from main import SoundboardAPI
    import packs
    import zipfile
    api = SoundboardAPI(str(tmp_path / 'api'), start_background_services=False)
    api._hotkeys.enabled = False
    try:
        item = clip(api._store)
        api._store.add_or_update_clip({**item, 'id': 'two', 'trim_start': .5, 'trim_end': 1, 'title': 'segunda'})
        package = tmp_path / 'two.zip'
        packs.write_pack(package, api._store.snapshot()['clips'], {})
        fresh = SoundboardAPI(str(tmp_path / 'fresh'), start_background_services=False)
        fresh._hotkeys.enabled = False
        try:
            result = fresh.import_pack(str(package))
            assert result['ok'] and len(result['imported']) == 2
            assert fresh.import_pack(str(package))['skipped'] == 2
        finally:
            fresh._store.close()
        api._store.begin_edit(item['id'])
        api._store.update_edit(item['id'], {'trim_start': .2, 'trim_end': .7, 'volume': .5})
        api._store.commit_edit(item['id'])
        edited = tmp_path / 'edited.zip'
        assert api.export_pack([item['id']], str(edited))['ok']
        with zipfile.ZipFile(edited) as archive:
            manifest = json.loads(archive.read('manifest.json'))
            entry = manifest['clips'][0]
            import io
            data, sr = sf.read(io.BytesIO(archive.read(entry['file'])), always_2d=True)
            assert len(data) == 24000 and sr == 48000
            assert entry['trim_start'] == 0 and entry['trim_end'] == .5
            assert entry['volume'] == 1
            assert data[1000, 0] == pytest.approx(.46, abs=1e-5)
        imported_api = SoundboardAPI(str(tmp_path / 'rendered-import'), start_background_services=False)
        imported_api._hotkeys.enabled = False
        try:
            imported = imported_api.import_pack(str(edited))['imported'][0]
            imported_context = imported_api._store.render_context(imported['id'])
            actual = imported_api._audio.render_clip(imported_context, {'normalize_on_import': True})
            assert np.allclose(actual, data, atol=1e-6)
        finally:
            imported_api._store.close()
    finally:
        api._store.close()


def test_loudness_uses_frozen_render_not_legacy_trim(tmp_path, monkeypatch):
    from main import SoundboardAPI
    import loudness
    api = SoundboardAPI(str(tmp_path / 'api'), start_background_services=False)
    api._hotkeys.enabled = False
    try:
        item = clip(api._store)
        api._store.begin_edit(item['id']); api._store.update_edit(item['id'], {'spans': [[0, 6000], [36000, 48000]]})
        api._store.commit_edit(item['id'])
        inputs = []
        monkeypatch.setattr(loudness, 'analyze_audio_loudness', lambda samples, sr: inputs.append(samples) or {'lufs': -18, 'loudness_gain_db': 4})
        assert api.reanalyze_clip_loudness(item['id'])['ok']
        assert len(inputs[0]) == 18000
    finally:
        api._store.close()


def test_playback_retains_source_and_releases_on_failure_and_finish(tmp_path, monkeypatch):
    from main import SoundboardAPI
    api = SoundboardAPI(str(tmp_path / 'api'), start_background_services=False)
    api._hotkeys.enabled = False
    try:
        item = clip(api._store); api._store.begin_edit(item['id']); saved = api._store.commit_edit(item['id'])
        sid = saved['source_id']; callbacks = []
        def play(**kwargs):
            assert 'active' in api._store.source_references(sid)
            callbacks.append(kwargs['on_finish'])
            return 'play'
        monkeypatch.setattr(api._audio, 'play_clip', play)
        assert api.play_clip(item['id'])['ok']
        callbacks[0](None, None); callbacks[0](None, None)
        assert 'active' not in api._store.source_references(sid)
        monkeypatch.setattr(api._audio, 'play_clip', lambda **kw: '')
        assert not api.play_clip(item['id'])['ok']
        assert 'active' not in api._store.source_references(sid)
        def failure(**kw):
            raise RuntimeError('device failure')
        monkeypatch.setattr(api._audio, 'play_clip', failure)
        assert not api.play_clip(item['id'])['ok']
        assert 'active' not in api._store.source_references(sid)
    finally:
        api._store.close()


def test_backup_failure_never_writes_migration(tmp_path, monkeypatch):
    db = tmp_path / 'soundboard.json'; raw = '{"schema_version":1,"clips":[]}'; db.write_text(raw)
    original = Path.write_text
    def failure(path, *args, **kwargs):
        if path.suffix == '.bak':
            raise OSError('backup failed')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'write_text', failure)
    with pytest.raises(OSError, match='backup failed'):
        SoundboardStore(str(tmp_path))
    assert db.read_text() == raw


def test_direct_api_save_commits_revision_and_preserves_unchanged_provenance(tmp_path):
    from main import SoundboardAPI
    api = SoundboardAPI(str(tmp_path / 'api'), start_background_services=False)
    api._hotkeys.enabled = False
    try:
        item = clip(api._store)
        api._store.begin_edit(item['id']); first = api._store.commit_edit(item['id'])
        result = api.save_clip_edits({'id': item['id'], 'trim_start': .2, 'trim_end': .6, 'transcript': '', 'speaker_id': ''})
        assert result['ok']
        assert result['clip']['revision_id'] != first['revision_id']
        assert api._store.render_context(item['id'])['_revision']['spans'] == [[9600, 28800]]
        assert result['clip']['field_provenance']['transcript'] == 'unknown'
        assert result['clip']['field_provenance']['speaker_id'] == 'unknown'
    finally:
        api._store.close()


def test_discard_promoted_replay_removes_pending_record(store):
    item = clip(store, pending=True)
    store.begin_edit(item['id']); store.update_edit(item['id'], {'title': 'rascunho'})
    assert store.discard_edit(item['id'])
    assert store.get_clip(item['id']) is None
    assert item['id'] not in store.snapshot()['edit_drafts']


def test_metadata_autosave_and_preview_preserve_multiple_spans(tmp_path):
    from main import SoundboardAPI
    api = SoundboardAPI(str(tmp_path / 'api'), start_background_services=False)
    api._hotkeys.enabled = False
    try:
        item = clip(api._store)
        api._store.begin_edit(item['id'])
        expected = [[0, 6000], [36000, 48000]]
        api._store.update_edit(item['id'], {'spans': expected})
        api._store.commit_edit(item['id'])
        opened = api.begin_clip_edit(item['id'])['clip']
        payload = {'id': item['id'], 'title': 'apenas título', 'trim_start': opened.get('trim_start', 0),
                   'trim_end': opened.get('trim_end') or opened['duration']}
        assert api.update_clip_edit(payload)['ok']
        assert api._store.render_context(item['id'], draft=True)['_revision']['spans'] == expected
        assert api.save_clip_edits(payload)['ok']
        assert api._store.render_context(item['id'])['_revision']['spans'] == expected
        assert api.begin_clip_edit(item['id'])['draft']['revision']['spans'] == expected
        assert api.update_clip_edit({**payload, 'trim_start': .1})['ok']
        assert api._store.render_context(item['id'], draft=True)['_revision']['spans'] == [[4800, 48000]]
    finally:
        api._store.close()


def test_fractional_source_metadata_omits_trim_and_explicit_sample_cut_is_exact(tmp_path):
    from main import SoundboardAPI
    api = SoundboardAPI(str(tmp_path / 'api'), start_background_services=False)
    api._hotkeys.enabled = False
    try:
        path = api._store.clips_dir / 'third.wav'
        sf.write(path, np.full((16000, 2), .1), 48000, subtype='FLOAT')
        item = api._store.add_or_update_clip({'id': 'one', 'file_path': str(path), 'duration': 16000 / 48000})
        expected = [[0, 2000], [12000, 16000]]
        api._store.begin_edit(item['id']); api._store.update_edit(item['id'], {'spans': expected})
        api._store.commit_edit(item['id'])
        for title in ('autosave', 'prévia', 'save'):
            assert api.begin_clip_edit(item['id'])['ok']
            assert api.update_clip_edit({'id': item['id'], 'title': title})['ok']
            assert api._store.render_context(item['id'], draft=True)['_revision']['spans'] == expected
        assert api.save_clip_edits({'id': item['id'], 'title': 'final'})['ok']
        assert api._store.render_context(item['id'])['_revision']['spans'] == expected
        assert api.update_clip_edit({'id': item['id'], 'trim_start': 1 / 48000, 'trim_end': .33})['ok']
        assert api._store.render_context(item['id'], draft=True)['_revision']['spans'] == [[1, 15840]]
    finally:
        api._store.close()


def test_pack_distinguishes_identical_rendered_montages_and_transports_identity(tmp_path):
    from main import SoundboardAPI
    import zipfile
    apis = [SoundboardAPI(str(tmp_path / name), start_background_services=False) for name in ('source', 'import1', 'import2')]
    for api in apis:
        api._hotkeys.enabled = False
    origin, first, second = apis
    try:
        item = clip(origin._store)
        origin._store.add_or_update_clip({**item, 'id': 'two'})
        for clip_id, spans in [('one', [[0, 24000]]), ('two', [[24000, 48000]])]:
            origin._store.begin_edit(clip_id)
            origin._store.update_edit(clip_id, {'spans': spans})
            origin._store.commit_edit(clip_id)
        package = tmp_path / 'identical.zip'
        assert origin.export_pack(path=str(package))['count'] == 2
        with zipfile.ZipFile(package) as archive:
            entries = json.loads(archive.read('manifest.json'))['clips']
            assert archive.read(entries[0]['file']) == archive.read(entries[1]['file'])
        imported = first.import_pack(str(package))
        assert imported['ok'] and len(imported['imported']) == 2 and imported['skipped'] == 0
        assert first.import_pack(str(package))['skipped'] == 2
        reexported = tmp_path / 'reexported.zip'
        assert first.export_pack(path=str(reexported))['count'] == 2
        assert first.import_pack(str(reexported))['skipped'] == 2
        again = second.import_pack(str(reexported))
        assert len(again['imported']) == 2
        assert second.import_pack(str(package))['skipped'] == 2
        # Depois do roundtrip neutro, novas receitas não herdam identidade antiga.
        import packs
        edited_id = again['imported'][0]['id']
        old_identity = again['imported'][0]['montage_identity']
        new_identities, rendered = [], []
        for spans in ([[1000, 11000]], [[12000, 22000]]):
            second._store.begin_edit(edited_id)
            second._store.update_edit(edited_id, {'spans': spans})
            second._store.commit_edit(edited_id)
            context = second._store.render_context(edited_id)
            new_identities.append(packs.montage_identity(context))
            rendered.append(second._audio.render_clip(context))
        assert old_identity not in new_identities
        assert new_identities[0] != new_identities[1]
        assert np.array_equal(rendered[0], rendered[1])
    finally:
        for api in apis:
            api._store.close()


@pytest.mark.parametrize('identity', ['not-a-digest', '../path', 123, None, 'a' * 200])
def test_pack_rejects_invalid_portable_montage_identity(tmp_path, identity):
    import packs
    import zipfile
    package = tmp_path / 'invalid.zip'
    with zipfile.ZipFile(package, 'w') as archive:
        archive.writestr('audio/a.wav', b'placeholder')
        archive.writestr('manifest.json', json.dumps({'format': packs.PACK_FORMAT, 'version': 1,
            'clips': [{'file': 'audio/a.wav', 'montage_identity': identity}]}))
    with pytest.raises(packs.PackError, match='identidade'):
        packs.validate_pack(package)
    from main import SoundboardAPI
    api = SoundboardAPI(str(tmp_path / 'rejected'), start_background_services=False)
    api._hotkeys.enabled = False
    try:
        before = api._store.snapshot()
        assert not api.import_pack(str(package))['ok']
        assert api._store.snapshot() == before
        assert not list(api._store.clips_dir.iterdir())
    finally:
        api._store.close()


def test_commit_creates_and_synchronizes_selected_speaker_atomically(store, monkeypatch):
    item = clip(store)
    store.ensure_speaker('existing', default_name='Preservada')
    store.save()
    store.begin_edit(item['id'])
    store.update_edit(item['id'], {'speaker_id': 'interlocutor_1'})
    before = store.snapshot(); disk = store.db_path.read_bytes()
    with monkeypatch.context() as patch:
        patch.setattr(store, '_perform_save', lambda: (_ for _ in ()).throw(OSError('disk full')))
        with pytest.raises(OSError):
            store.commit_edit(item['id'])
    assert store.snapshot() == before
    assert store.db_path.read_bytes() == disk
    saved = store.commit_edit(item['id'])
    assert saved['speaker_id'] == 'interlocutor_1'
    assert saved['speaker_ids'] == ['interlocutor_1']
    assert store.snapshot()['speakers']['interlocutor_1']['name'] == 'Amigo 1'
    reopened = SoundboardStore(str(store.data_dir))
    try:
        assert reopened.snapshot()['speakers'] == store.snapshot()['speakers']
    finally:
        reopened.close()


def test_legacy_rounded_trim_is_adapted_once_to_canonical_samples(store):
    path = store.clips_dir / 'rounded.wav'
    sf.write(path, np.full((64128, 2), .1), 48000, subtype='FLOAT')
    item = store.add_or_update_clip({'id': 'rounded', 'file_path': str(path), 'duration': 1.34, 'trim_end': 1.34})
    draft = store.begin_edit(item['id'])
    assert draft['revision']['spans'] == [[0, 64128]]
    assert store.get_clip(item['id'])['duration'] == 1.336
    assert store.get_clip(item['id'])['trim_end'] == 1.336
    store.commit_edit(item['id'])
    from clip_renderer import render_revision
    context = store.render_context(item['id'])
    rendered = render_revision(context['_source'], context['_revision'])
    assert len(rendered) == 64128
    assert rendered[1000, 0] == pytest.approx(.92)
    with pytest.raises(ValueError):
        store.update_edit(item['id'], {'trim_end': 1.34})
    with pytest.raises(ValueError):
        store.update_edit(item['id'], {'spans': [[0, 64129]]})
    store.update_edit(item['id'], {'trim_end': 64127 / 48000})
    assert store.render_context(item['id'], draft=True)['_revision']['spans'] == [[0, 64127]]


@pytest.mark.parametrize('failure', [False, True])
def test_enrollment_uses_only_unprocessed_kept_revision_samples(tmp_path, monkeypatch, failure):
    from main import SoundboardAPI
    api = SoundboardAPI(str(tmp_path / 'api'), start_background_services=False)
    api._hotkeys.enabled = False
    api._capability_error = lambda *args: None
    try:
        path = api._store.clips_dir / 'three.wav'
        original = np.concatenate([np.full((48000, 2), value, dtype=np.float32) for value in (.1, .8, -.2)])
        sf.write(path, original, 48000, subtype='FLOAT')
        item = api._store.add_or_update_clip({'id': 'three', 'file_path': str(path), 'duration': 3})
        api._store.begin_edit(item['id'])
        api._store.update_edit(item['id'], {'spans': [[0, 48000], [96000, 144000]]})
        api._store.commit_edit(item['id'])
        observed = []
        import ai_analyzer
        original_loader = ai_analyzer.load_audio_mono_16k
        def load_selected(file_path):
            data, rate = sf.read(file_path, dtype='float32', always_2d=True)
            observed.append((file_path, data, rate))
            return original_loader(file_path)
        def embed(audio):
            if failure:
                raise RuntimeError('synthetic enrollment failure')
            return np.array([1., 0.])
        monkeypatch.setattr(ai_analyzer, 'load_audio_mono_16k', load_selected)
        monkeypatch.setattr(api._ai, 'compute_embedding', embed)
        result = api.enroll_speaker_from_clip(item['id'], 'friend')
        assert result['ok'] is True
        assert result['assignment_saved'] is True
        api._analysis_queue.wait(result['learning']['analysis_id'])
        action = api.get_voice_profiles()['actions'][result['action_id']]
        assert action['reference_learned'] is not failure
        temporary, data, rate = observed[0]
        assert rate == 48000 and len(data) == 96000
        assert np.array_equal(data, np.concatenate([original[:48000], original[96000:]]))
        assert not Path(temporary).exists()
        assert Path(path).exists()
        assert 'active' not in api._store.source_references(item['source_id'])
    finally:
        api._analysis_queue.close()
        api._store.close()
