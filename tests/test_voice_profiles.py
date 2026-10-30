import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from store import SoundboardStore


@pytest.fixture
def bank(tmp_path):
    assert importlib.util.find_spec('voice_profiles'), 'Banco reversível de vozes ausente'
    from voice_profiles import VoiceProfiles
    store = SoundboardStore(str(tmp_path / 'data'))
    path = tmp_path / 'voice.wav'
    sf.write(path, np.sin(np.arange(96000) * .04) * .2, 48000, subtype='FLOAT')
    clip = store.add_imported_clip({'file_path': str(path)}, rendered=True)
    store.begin_edit(clip['id']); store.commit_edit(clip['id'])
    yield VoiceProfiles(store), store, clip['id']
    store.close()


def add(bank, speaker='a', spans=None, embedding=None, action='manual-1', **quality):
    profiles, store, clip_id = bank
    context = store.render_context(clip_id)
    return profiles.add_sample(speaker, context, spans or [[0, 48000]],
        action_id=action, extractor_version='synthetic-v1',
        embedding=[1., 0.] if embedding is None else embedding,
        quality={'speech_seconds': 1, 'silence_fraction': 0, 'clipping_fraction': 0,
                 'mixed': False, **quality})


def test_manual_trace_dedupe_and_rebuild_after_removal(bank):
    profiles, store, _ = bank
    first = add(bank)
    repeated = add(bank, action='manual-2')
    assert repeated['sample']['sample_id'] == first['sample']['sample_id']
    assert repeated['created'] is False
    assert first['sample']['manual_action_id'] == 'manual-1'
    second = add(bank, spans=[[48000, 96000]], embedding=[0., 1.], action='manual-3')
    np.testing.assert_allclose(profiles.prototypes('synthetic-v1')['a'], [2**-.5, 2**-.5])
    version = profiles.snapshot()['profiles']['a']['profile_version']
    profiles.remove_sample(second['sample']['sample_id'])
    np.testing.assert_allclose(profiles.prototypes('synthetic-v1')['a'], [1., 0.])
    assert profiles.snapshot()['profiles']['a']['profile_version'] > version
    assert len(profiles.snapshot()['samples']) == 1


@pytest.mark.parametrize('quality,reason', [({'speech_seconds': .1}, 'insufficient_speech'),
    ({'silence_fraction': .9}, 'silence'), ({'clipping_fraction': .2}, 'clipping'),
    ({'mixed': True}, 'mixed'), ({'mixed': None}, 'uncertain_mix')])
def test_uncertain_or_bad_examples_never_influence_profiles(bank, quality, reason):
    result = add(bank, **quality)
    assert result['sample']['eligibility'] == 'pending'
    assert reason in result['sample']['quality']['reasons']
    assert bank[0].prototypes('synthetic-v1') == {}


def test_reassign_moves_influence_and_retains_source_after_clip_deleted(bank):
    profiles, store, clip_id = bank
    sample = add(bank)['sample']
    profiles.reassign_sample(sample['sample_id'], 'b')
    assert set(profiles.prototypes('synthetic-v1')) == {'b'}
    store.delete_clip(clip_id, True)
    assert 'voice:' + sample['sample_id'] in store.source_references(sample['source_id'])
    assert profiles.sample_context(sample['sample_id'])['_revision']['spans'] == [[0, 48000]]
    reopened = SoundboardStore(str(store.data_dir))
    try:
        from voice_profiles import VoiceProfiles
        assert set(VoiceProfiles(reopened).prototypes('synthetic-v1')) == {'b'}
    finally:
        reopened.close()


def test_write_failure_rolls_back_bank_refs_and_profile(bank, monkeypatch):
    profiles, store, _ = bank
    before = store.snapshot()
    monkeypatch.setattr(store, 'save', lambda *a, **k: (_ for _ in ()).throw(OSError('disk')))
    with pytest.raises(OSError):
        add(bank)
    assert store.snapshot() == before


def test_legacy_npz_migration_is_backed_up_and_not_a_confirmed_sample(bank):
    profiles, store, _ = bank
    path = store.models_dir / 'referencias_vozes.npz'
    np.savez(path, old=np.array([1., 0.]))
    original = path.read_bytes()
    profiles.migrate_legacy(path, 'synthetic-v1')
    profiles.migrate_legacy(path, 'synthetic-v1')
    state = profiles.snapshot()
    assert not state['samples']
    assert state['legacy']['old']['provenance'] == 'legacy'
    assert path.read_bytes() == path.with_suffix('.npz.bak').read_bytes() == original
    assert set(profiles.prototypes('synthetic-v1')) == {'old'}


def test_schema_two_migration_preserves_source_ids_and_has_own_backup(tmp_path):
    db = tmp_path / 'soundboard.json'
    original = json.dumps({'schema_version': 2, 'sources': {'keep': {'id': 'keep'}}, 'clips': []})
    db.write_text(original)
    db.with_suffix('.json.bak').write_text('older backup')
    store = SoundboardStore(str(tmp_path))
    try:
        assert store.snapshot()['schema_version'] == 6
        assert store.snapshot()['sources'] == {'keep': {'id': 'keep'}}
        assert (tmp_path / 'soundboard.v2.bak.json').read_text() == original
        assert db.with_suffix('.json.bak').read_text() == 'older backup'
    finally:
        store.close()


def test_unequal_banks_do_not_award_more_comparisons(bank):
    profiles, store, clip_id = bank
    context = store.render_context(clip_id)
    for i in range(8):
        profiles.add_sample('many', context, [[i*6000,(i+1)*6000]], action_id=str(i),
            extractor_version='synthetic-v1', embedding=[.8,.6],
            quality={'speech_seconds':1,'silence_fraction':0,'clipping_fraction':0,'mixed':False})
    add(bank, speaker='one', embedding=[1,0], spans=[[48000,96000]])
    result = profiles.match([1,0], 'synthetic-v1')
    assert result['speaker_id'] == 'one'


def test_legacy_unknown_extractor_does_not_match_modern(bank):
    profiles, store, _ = bank
    path=store.models_dir/'old.npz';np.savez(path, a=np.array([1.,0.]))
    profiles.migrate_legacy(path)
    assert profiles.match([1,0], 'synthetic-v1')['status'] == 'unknown'


def test_nested_transaction_failure_never_commits_partial_bank(bank, monkeypatch):
    profiles, store, _ = bank
    before=store.snapshot(); disk=store.db_path.read_text(encoding='utf-8')
    original=store.save
    calls=[]
    def fail(): calls.append(1);raise OSError('disk')
    monkeypatch.setattr(store,'save',fail)
    with pytest.raises(OSError):
        with profiles.transaction(): add(bank)
    assert len(calls)==1 and store.snapshot()==before and store.db_path.read_text(encoding='utf-8')==disk


def test_full_bank_rejects_new_sample_but_allows_existing_mixture_downgrade(bank):
    profiles,store,clip_id=bank
    context=store.render_context(clip_id)
    quality={'speech_seconds':1,'silence_fraction':0,'clipping_fraction':0,'mixed':False}
    first=profiles.add_sample('a',context,[[0,96000]],action_id='first',
        extractor_version='synthetic-v1',embedding=[1.,0.],quality=quality)['sample']
    for index in range(63):
        profiles.add_sample('a',context,[[index*1000,(index+1)*1000]],action_id=f'other-{index}',
            extractor_version='synthetic-v1',embedding=[1.,0.],quality=quality)
    assert len(profiles.snapshot()['samples'])==64
    with pytest.raises(ValueError,match='Limite'):
        profiles.add_sample('a',context,[[90000,95000]],action_id='new',
            extractor_version='synthetic-v1',embedding=[1.,0.],quality=quality)
    updated=profiles.add_sample('a',context,[[0,96000]],action_id='reevaluate',
        extractor_version='synthetic-v1',embedding=[1.,0.],quality={**quality,'mixed':None})
    assert updated['created'] is False
    assert updated['sample']['sample_id']==first['sample_id']
    assert updated['sample']['eligibility']=='pending'
    assert updated['sample']['used_in_profile'] is False
    assert 'uncertain_mix' in updated['sample']['quality']['reasons']
    assert len(profiles.snapshot()['samples'])==64


def test_reassignment_respects_destination_limit_and_rejects_atomically(bank):
    profiles,store,clip_id=bank
    context=store.render_context(clip_id)
    quality={'speech_seconds':1,'silence_fraction':0,'clipping_fraction':0,'mixed':False}
    for index in range(64):
        profiles.add_sample('a',context,[[index*1000,(index+1)*1000]],action_id=f'a-{index}',
            extractor_version='synthetic-v1',embedding=[1.,0.],quality=quality)
    sample=profiles.add_sample('b',context,[[90000,95000]],action_id='b',
        extractor_version='synthetic-v1',embedding=[0.,1.],quality=quality)['sample']
    before=store.snapshot()
    with pytest.raises(ValueError,match='Limite'):
        profiles.reassign_sample(sample['sample_id'],'a')
    assert store.snapshot()==before
    own=next(s for s in profiles.snapshot()['samples'].values() if s['speaker_id']=='a')
    assert profiles.reassign_sample(own['sample_id'],'a')['speaker_id']=='a'
    assert store.snapshot()==before
