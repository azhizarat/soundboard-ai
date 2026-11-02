"""A biblioteca de sequências expõe metadados sem promover áudio legado."""
import copy

import pytest

from store import SoundboardStore


@pytest.fixture
def store(tmp_path):
    instance = SoundboardStore(str(tmp_path / 'data'))
    yield instance
    instance.close()


def test_choices_include_searchable_metadata_and_published_rendered_duration(store):
    store.state['speakers'] = {'a': {'name': 'Ana'}, 'b': {'name': 'Beto'}}
    store.state['clips'] = [{'id': 'clip', 'title': 'Pergunta', 'transcript': 'Vamos começar?',
        'speaker_id': 'a', 'speaker_ids': ['a', 'b'], 'category': 'Call', 'emoji': '🎙️',
        'color': '#123456', 'duration': 10, 'volume': .7, 'revision_id': 'rev'}]
    store.state['revisions']['rev'] = {'spans': [[0, 48000], [96000, 144000]], 'variant': {'speed': 2}}
    choice = store.sequence_clip_choices()[0]
    assert choice == {'id': 'clip', 'title': 'Pergunta', 'transcript': 'Vamos começar?',
        'text': 'Vamos começar?', 'speaker_name': 'Ana, Beto', 'speaker_names': ['Ana', 'Beto'],
        'category': 'Call', 'emoji': '🎙️', 'color': '#123456', 'duration': 1,
        'volume': .7, 'revision_id': 'rev', 'samples': 48000}
    choice['speaker_names'].append('Outro')
    assert store.sequence_clip_choices()[0]['speaker_names'] == ['Ana', 'Beto']


@pytest.mark.parametrize('duration,start,end,expected', [(4, 1, 3, 96000), (4, 1, 0, 144000),
    (4, 0, 8, 192000), (0, 0, 0, 0)])
def test_legacy_choices_use_metadata_without_import_decode_or_mutation(store, monkeypatch, duration, start, end, expected):
    store.state['clips'] = [{'id': 'legacy', 'file_path': 'missing.wav', 'duration': duration,
        'trim_start': start, 'trim_end': end, 'speaker_id': 'unknown'}, {'id': 'pending', 'pending': True}]
    before = copy.deepcopy(store.state)
    def forbidden(*args, **kwargs):
        pytest.fail('A listagem não pode promover, decodificar ou salvar áudio')
    monkeypatch.setattr(store, 'begin_edit', forbidden)
    monkeypatch.setattr(store, 'save', forbidden)
    monkeypatch.setattr('clip_renderer.preserve_source', forbidden)
    monkeypatch.setattr('soundfile.read', forbidden)
    choices = store.sequence_clip_choices()
    assert len(choices) == 1
    assert choices[0]['samples'] == expected
    assert choices[0]['duration'] == expected / 48000
    assert choices[0]['revision_id'] is None
    assert choices[0]['speaker_names'] == []
    assert choices[0]['transcript'] == ''
    assert store.state == before
