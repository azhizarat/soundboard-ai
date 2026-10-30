import copy
import json

import pytest

from store import SoundboardStore


def collection(clip_id='c', ident='show', **pad_changes):
    return {'schema_version': 1, 'id': ident, 'name': 'Apresentação', 'revision': 0,
            'columns': 4, 'pads': [{'id': 'pad', 'position': 5, 'label': 'Fala',
                'target': {'type': 'clip', 'id': clip_id}, 'hotkey': 'f1',
                'group': 'speech', 'retrigger': 'restart', 'hold': False,
                'loop': False, 'fade_ms': 120, **pad_changes}]}


@pytest.fixture
def library(tmp_path):
    store = SoundboardStore(str(tmp_path))
    store.add_or_update_clip({'id': 'c', 'title': 'Original', 'file_path': 'media.wav'})
    yield store
    store.close()


def test_fixed_references_share_media_and_delete_requires_explicit_unlink(library):
    assert hasattr(library, 'save_collection'), 'Coleções persistentes ausentes'
    first = library.save_collection(collection())
    library.save_collection(collection(ident='second'))
    assert len(library.snapshot()['clips']) == 1
    assert first['pads'][0]['position'] == 5
    with pytest.raises(ValueError, match='Apresentação'):
        library.delete_clip('c')
    library.delete_collection('show')
    with pytest.raises(ValueError):
        library.delete_clip('c')
    library.delete_collection('second')
    assert library.delete_clip('c')


def test_collection_rollback_and_optimistic_revision(library, monkeypatch):
    assert hasattr(library, 'save_collection'), 'Coleções persistentes ausentes'
    saved = library.save_collection(collection())
    with pytest.raises(ValueError, match='recarregue'):
        library.save_collection(collection())
    before = library.snapshot()
    with monkeypatch.context() as patch:
        patch.setattr(library, 'save', lambda: (_ for _ in ()).throw(OSError('disk full')))
        with pytest.raises(OSError):
            library.save_collection(dict(saved, name='Changed'))
        with pytest.raises(OSError):
            library.delete_collection('show')
    assert library.snapshot() == before


def test_conflicts_normalize_modifiers_aliases_and_allow_inactive_reuse(library):
    assert hasattr(library, 'save_collection'), 'Coleções persistentes ausentes'
    with pytest.raises(ValueError, match='conflito'):
        library.save_collection(collection(hotkey='SHIFT+Control+S'))
    saved = library.save_collection(collection(hotkey='alt+f1'))
    library.save_collection(collection(ident='other', hotkey='Alt+F1'))
    duplicate = copy.deepcopy(saved['pads'][0]); duplicate.update(id='other', position=0, hotkey='f1+alt')
    with pytest.raises(ValueError, match='conflito'):
        library.save_collection(dict(saved, pads=saved['pads']+[duplicate]))
    library.add_or_update_clip({'id': 'external', 'hotkey': 'ctrl+f8'})
    with pytest.raises(ValueError, match='conflito'):
        library.save_collection(collection(ident='third', hotkey='f8+control'))


@pytest.mark.parametrize('change', [{'position': -1}, {'retrigger': 'unknown'}, {'target': {'type': 'clip', 'id': 'missing'}}, {'fade_ms': float('nan')}, {'loop': 'yes'}])
def test_invalid_pad_rejected_without_mutation(library, change):
    assert hasattr(library, 'save_collection'), 'Coleções persistentes ausentes'
    before = library.snapshot()
    with pytest.raises(ValueError):
        library.save_collection(collection(**change))
    assert library.snapshot() == before


def test_v4_migration_verified_backup_and_reopen(tmp_path):
    path = tmp_path / 'soundboard.json'
    original = json.dumps({'schema_version': 4, 'clips': [], 'sequences': {'s': {'id': 's'}}, 'settings': {}})
    path.write_text(original, encoding='utf-8')
    store = SoundboardStore(str(tmp_path))
    try:
        assert store.snapshot()['schema_version'] == 6, 'Migração para o schema atual ausente'
        assert store.snapshot()['collections'] == {}
        assert (tmp_path/'soundboard.v4.collections.bak.json').read_text(encoding='utf-8') == original
        assert store.snapshot()['sequences'] == {'s': {'id': 's'}}
    finally:
        store.close()
    path.write_text(original, encoding='utf-8')
    (tmp_path/'soundboard.v4.collections.bak.json').write_text('{}', encoding='utf-8')
    with pytest.raises(ValueError, match='Backup'):
        SoundboardStore(str(tmp_path))
    assert path.read_text(encoding='utf-8') == original
