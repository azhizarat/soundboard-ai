import copy
import random

import pytest

import sound_collections
from test_collections import collection, library
from test_trigger_policy import operation
from test_editor_api import editor


def test_shuffle_cycles_cooldown_and_explicit_repeat():
    assert hasattr(sound_collections, 'ShuffleGroups'), 'Saco embaralhado ausente'
    clock = [0.0]
    chooser = sound_collections.ShuffleGroups(random.Random(7), lambda: clock[0])
    items = [{'type': 'clip', 'id': str(i)} for i in range(3)]
    chosen = []
    for _ in range(15):
        chosen.append(chooser.choose('p', items, 100)['id'])
        with pytest.raises(ValueError, match='Cooldown'):
            chooser.choose('p', items, 100)
        clock[0] += .101
    assert all(a != b for a, b in zip(chosen, chosen[1:]))
    assert all(len(set(chosen[i:i+3])) == 3 for i in range(0,15,3))
    assert chooser.choose('p', items, 100, repeat_last=True)['id'] == chosen[-1]
    clock[0] += .101
    assert chooser.choose('p', items, 100)['id'] != chosen[-1]


def test_curated_references_validate_and_prevent_delete(library):
    target = {'type': 'shuffle', 'items': [{'type': 'clip', 'id': 'c'}]}
    doc = collection(target=target, cooldown_ms=250)
    saved = library.save_collection(doc)
    assert saved['pads'][0]['target'] == target
    with pytest.raises(ValueError, match='Apresentação'):
        library.delete_clip('c')
    for invalid in [[], target['items']*2, [{'type': 'shuffle', 'id': 'x'}], [{'type': 'clip', 'id': 'missing'}]]:
        with pytest.raises(ValueError):
            library.save_collection(dict(saved, pads=[dict(saved['pads'][0], target={'type':'shuffle','items':invalid})]))


def test_shuffle_dispatch_repeat_and_stop_all_share_owner(operation, monkeypatch):
    api, ident = operation
    doc = api._store.get_collection('show')
    doc['pads'][0].update(target={'type':'shuffle','items':[{'type':'clip','id':ident}]}, cooldown_ms=0, hold=True)
    assert api.save_collection(doc)['ok']
    assert api.set_rehearsal(True)['ok']
    first = api.trigger_collection_pad('show','pad', token='first')
    assert first['ok'] and first['chosen_target'] == {'type':'clip','id':ident}
    assert api._audio.get_session(first['play_id']).monitor_only
    api.trigger_collection_pad('show','pad',phase='release',token='first')
    repeated = api.trigger_collection_pad('show','pad',token='second',repeat_last=True)
    assert repeated['ok'] and repeated['chosen_target'] == first['chosen_target']
    api.stop_all()
    assert not api._audio.get_session(repeated['play_id'])
    prepare = api._audio.prepare_group
    def cancel(*a, **kw):
        api.trigger_collection_pad('show','pad',phase='release',token='third')
        return prepare(*a, **kw)
    monkeypatch.setattr(api._audio, 'prepare_group', cancel)
    assert api.trigger_collection_pad('show','pad',token='third')['ok'] is False
    assert not api._audio._sessions
