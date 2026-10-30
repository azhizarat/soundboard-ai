"""E8: definições congeladas e referências não destrutivas."""
import copy
import importlib.util
import json

import numpy as np
import pytest
import soundfile as sf

from store import SoundboardStore


@pytest.fixture
def library(tmp_path):
    store = SoundboardStore(str(tmp_path / 'data'))
    path = tmp_path / 'source.wav'
    sf.write(path, np.linspace(-.2, .2, 96000, dtype=np.float32), 48000, subtype='FLOAT')
    clip = store.add_imported_clip({'file_path': str(path), 'title': 'Pergunta'}, rendered=True)
    store.begin_edit(clip['id'])
    clip = store.commit_edit(clip['id'])
    yield store, clip
    store.close()


def document(clip, blocks=None):
    return {'schema_version': 1, 'id': 'dialogue', 'name': 'Diálogo', 'revision': 0, 'hotkey': '',
            'blocks': blocks if blocks is not None else [play(clip)]}


def play(clip, ident='one', **kwargs):
    return {'id': ident, 'type': 'play', 'clip_id': clip['id'], 'revision_id': clip['revision_id'],
            'volume': 1, 'interval': [0, 48000], **kwargs}


def module():
    assert importlib.util.find_spec('sound_sequences'), 'Falta o contrato de sequências versionadas'
    import sound_sequences
    return sound_sequences


def test_occurrence_is_pinned_and_trim_uses_rendered_revision(library):
    store, clip = library
    assert hasattr(store, 'save_sequence'), 'Persistência de sequências ausente'
    original = copy.deepcopy(clip)
    seq = store.save_sequence(document(clip, [play(clip), play(clip, 'two', interval=[48000, 96000], volume=.3)]))
    first = store.resolve_sequence_occurrence(seq['blocks'][0])
    assert first['_revision']['id'] == clip['revision_id']
    assert first['_preview_ranges'] == [[0, 48000]]
    store.begin_edit(clip['id'])
    store.update_edit(clip['id'], {'spans': [[48000, 96000]]})
    new = store.commit_edit(clip['id'])
    assert new['revision_id'] != original['revision_id']
    assert store.resolve_sequence_occurrence(seq['blocks'][0])['_revision']['id'] == original['revision_id']
    assert store.get_sequence('dialogue')['blocks'][1]['volume'] == .3
    assert store.sequence_details('dialogue')['new_revisions'] == ['one', 'two']
    assert any(ref.startswith('sequence:dialogue:') for ref in store.source_references(clip['source_id']))
    with pytest.raises(ValueError, match='Diálogo'):
        store.delete_clip(clip['id'], True)


@pytest.mark.parametrize('blocks', [
    [{'id': 'w', 'type': 'wait', 'ms': -1}],
    [{'id': 'w', 'type': 'wait', 'ms': float('inf')}],
    [{'id': 'w', 'type': 'wait', 'ms': True}],
    [{'id': 'p', 'type': 'parallel', 'branches': [[]]}],
    [{'id': 'p', 'type': 'parallel', 'branches': [[{'id': 'm', 'type': 'manual'}], [{'id': 'w', 'type': 'wait', 'ms': 1}]]}],
    [{'id': 'p', 'type': 'parallel', 'branches': [[{'id': 'nested', 'type': 'parallel', 'branches': []}], [{'id': 'w', 'type': 'wait', 'ms': 1}]]}],
    [{'id': 'same', 'type': 'manual'}, {'id': 'same', 'type': 'manual'}],
])
def test_invalid_structure_rejected(library, blocks):
    store, clip = library
    with pytest.raises(ValueError):
        module().validate_document(document(clip, blocks), store.resolve_sequence_occurrence)


def test_cycles_missing_reference_invalid_interval_and_duration(library):
    store, clip = library
    contract = module()
    circular = {'id': 'p', 'type': 'parallel'}
    circular['branches'] = [[circular], [{'id': 'w', 'type': 'wait', 'ms': 0}]]
    for blocks in [[circular], [play(clip, interval=[0, 100000])], [play(clip, revision_id='missing')]]:
        with pytest.raises(ValueError):
            contract.validate_document(document(clip, blocks), store.resolve_sequence_occurrence)
    blocks = [play(clip), {'id': 'p', 'type': 'parallel', 'branches': [
        [play(clip, 'two')], [{'id': 'w', 'type': 'wait', 'ms': 2000}]]}, {'id': 'm', 'type': 'manual'}]
    result = contract.validate_document(document(clip, blocks), store.resolve_sequence_occurrence)
    assert result['duration_ms'] is None
    assert result['known_duration_ms'] == 3000
    assert result['manual_count'] == 1


def test_schema4_backup_roundtrip_preserves_voices_and_never_execution(tmp_path):
    root = tmp_path / 'data'
    root.mkdir()
    raw = {'schema_version': 3, 'clips': [], 'voice_bank': {'custom': 'preservar'}, 'future': {'x': 1}}
    (root / 'soundboard.json').write_text(json.dumps(raw), encoding='utf-8')
    (root / 'soundboard.json.bak').write_text('anterior', encoding='utf-8')
    store = SoundboardStore(str(root))
    try:
        assert store.state['schema_version'] == 6
        assert store.state['sequences'] == {}
        assert json.loads((root / 'soundboard.v3.sequences.bak.json').read_text(encoding='utf-8')) == raw
        assert (root / 'soundboard.json.bak').read_text(encoding='utf-8') == 'anterior'
        assert store.snapshot()['voice_bank'] == raw['voice_bank']
        assert store.snapshot()['future'] == raw['future']
    finally:
        store.close()


def test_failed_sequence_save_rolls_back_named_refs(library, monkeypatch):
    store, clip = library
    assert hasattr(store, 'save_sequence')
    before = store.snapshot()
    monkeypatch.setattr(store, 'save', lambda: (_ for _ in ()).throw(OSError('disk full')))
    with pytest.raises(OSError):
        store.save_sequence(document(clip))
    assert store.snapshot() == before


def test_local_trim_after_gap_matches_rendered_audio_and_updates_are_explicit(library):
    from audio_engine import AudioEngine
    from clip_renderer import render_revision
    store, clip = library
    store.begin_edit(clip['id'])
    store.update_edit(clip['id'], {'spans': [[0, 12000], [48000, 72000]]})
    clip = store.commit_edit(clip['id'])
    block = play(clip, interval=[6000, 18000], volume=.4)
    context = store.resolve_sequence_occurrence(block)
    engine = AudioEngine(start_loopback=False)
    expected = render_revision(context['_source'], context['_revision'])[6000:18000]
    assert np.array_equal(engine.render_clip(context), expected)
    assert context['volume'] == .4
    seq = store.save_sequence(document(clip, [block]))
    stale = copy.deepcopy(seq)
    seq['name'] = 'Novo nome'
    store.save_sequence(seq)
    with pytest.raises(ValueError, match='mudou'):
        store.save_sequence(stale)
    assert store.delete_sequence(seq['id'])
    assert not any(ref.startswith('sequence:') for ref in store.source_references(clip['source_id']))
    engine.shutdown()
