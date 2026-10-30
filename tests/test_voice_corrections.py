from types import SimpleNamespace
import copy
import numpy as np
import pytest
import soundfile as sf
from main import SoundboardAPI
from analysis_jobs import AnalysisQueue
from store import SoundboardStore


@pytest.fixture
def api(tmp_path):
    instance = SoundboardAPI.__new__(SoundboardAPI)
    instance._store = SoundboardStore(str(tmp_path / 'data'))
    instance._analysis_queue = AnalysisQueue()
    instance._ai = SimpleNamespace(compute_embedding=lambda audio: np.array([1., 0.]),
                                   extractor_version='synthetic-v1')
    instance._capability_error = lambda *a: None
    instance._hotkeys = SimpleNamespace(refresh=lambda *a: None)
    from clip_renderer import render_revision
    instance._audio = SimpleNamespace(render_clip=lambda context: render_revision(context['_source'],context['_revision']))
    path = tmp_path / 'speech.wav'
    sf.write(path, .2 * np.sin(np.arange(144000) * .05), 48000, subtype='FLOAT')
    clip = instance._store.add_imported_clip({'file_path': str(path)}, rendered=True)
    instance._store.begin_edit(clip['id']); instance._store.commit_edit(clip['id'])
    instance.clip_id = clip['id']
    yield instance
    instance._analysis_queue.close(); instance._store.close()


def finish(api, result):
    if result.get('learning', {}).get('analysis_id'):
        return api._analysis_queue.wait(result['learning']['analysis_id'])
    return result


def test_normal_save_manual_learning_is_opt_in_and_dedupe_undo_is_owned(api):
    assert hasattr(api, 'get_voice_profiles'), 'Fluxo de aprendizado supervisionado ausente'
    api.save_clip_edits({'id': api.clip_id, 'speaker_id': 'a'})
    assert not api.get_voice_profiles()['samples']
    api._store.update_settings({'learn_voice_corrections': True})
    result = api.save_clip_edits({'id': api.clip_id, 'speaker_id': 'b', 'voice_interval': [0, 48000]})
    finish(api, result)
    sample = api.get_voice_profiles()['samples'][0]
    assert sample['spans'] == [[0, 48000]] and sample['speaker_id'] == 'b'
    assert sample['eligibility'] == 'eligible'
    repeated = api.enroll_speaker_from_clip(api.clip_id, 'b', '', [0, 48000])
    finish(api, repeated)
    assert len(api.get_voice_profiles()['samples']) == 1
    api.undo_voice_correction(repeated['action_id'])
    assert len(api.get_voice_profiles()['samples']) == 1
    api.undo_voice_correction(result['action_id'])
    assert not api.get_voice_profiles()['samples']
    assert api._store.get_clip(api.clip_id)['speaker_id'] == 'a'


def test_failed_extraction_keeps_assignment_and_does_not_transcribe(api):
    assert hasattr(api, 'get_voice_profiles'), 'Fluxo de aprendizado supervisionado ausente'
    api._ai.compute_embedding = lambda audio: None
    result = api.enroll_speaker_from_clip(api.clip_id, 'friend', '', [0, 48000])
    finish(api, result)
    assert api._store.get_clip(api.clip_id)['speaker_id'] == 'friend'
    action = api.get_voice_profiles()['actions'][result['action_id']]
    assert action['reference_learned'] is False
    assert 'embedding_unavailable' in action['error']


def test_multivoice_requires_selection_and_rejects_overlap(api):
    assert hasattr(api, 'get_voice_profiles'), 'Fluxo de aprendizado supervisionado ausente'
    api._store.add_or_update_clip({'id': api.clip_id, 'speaker_ids': ['a', 'b'], 'segments': [
        {'start_sample': 0, 'end_sample': 96000, 'speaker_id': 'a'},
        {'start_sample': 48000, 'end_sample': 144000, 'speaker_id': 'b'}]})
    missing = api.enroll_speaker_from_clip(api.clip_id, 'b')
    assert missing['assignment_saved'] and not missing['reference_learned']
    assert 'selecione' in missing['error'].lower()
    overlap = api.enroll_speaker_from_clip(api.clip_id, 'b', '', [48000, 96000])
    finish(api, overlap)
    assert api.get_voice_profiles()['samples'][0]['eligibility'] == 'pending'


def test_profile_update_invalidates_matching_but_not_asr_and_batch_is_proposal(api):
    assert hasattr(api, 'get_voice_profiles'), 'Fluxo de aprendizado supervisionado ausente'
    clip = api._store.get_clip(api.clip_id)
    clip['analysis_generations'] = {'transcription': 8, 'speakers': 2}
    result = api.enroll_speaker_from_clip(api.clip_id, 'a', '', [0, 48000]); finish(api, result)
    assert clip['analysis_generations']['transcription'] == 8
    assert clip['analysis_generations']['speakers'] > 2
    old = copy.deepcopy(clip)
    review = api.request_voice_review([api.clip_id])
    assert review['ok'] and review['proposals'] == []  # Atribuição manual não é candidata.
    assert clip == old


def test_duplicate_reuses_embedding_and_failed_job_is_terminal(api):
    calls=[]
    api._ai.compute_embedding=lambda audio: calls.append(len(audio)) or np.array([1.,0.])
    first=api.enroll_speaker_from_clip(api.clip_id,'a','',[0,48000]);finish(api,first)
    duplicate=api.enroll_speaker_from_clip(api.clip_id,'a','',[0,48000]);finish(api,duplicate)
    assert calls==[16000]
    context=api._store.render_context(api.clip_id)
    from pathlib import Path
    Path(context['_source']['file_path']).write_bytes(b'corrupt synthetic')
    failed=api.enroll_speaker_from_clip(api.clip_id,'a','',[48000,96000]);finish(api,failed)
    assert api.get_voice_profiles()['actions'][failed['action_id']]['state']=='failed'
    assert 'active' not in api._store.source_references(context['source_id'])


def test_new_correction_after_duplicate_retracts_old_derived_influence(api):
    first=api.enroll_speaker_from_clip(api.clip_id,'a','',[0,48000]);finish(api,first)
    duplicate=api.enroll_speaker_from_clip(api.clip_id,'a','',[0,48000]);finish(api,duplicate)
    corrected=api.enroll_speaker_from_clip(api.clip_id,'b','',[0,48000]);finish(api,corrected)
    samples=api.get_voice_profiles()['samples']
    assert len(samples)==1 and samples[0]['speaker_id']=='b'


def test_cancel_running_learning_keeps_person_and_cannot_late_apply(api):
    import threading
    entered,release=threading.Event(),threading.Event()
    def embed(audio): entered.set();release.wait(3);return np.array([1.,0.])
    api._ai.compute_embedding=embed
    result=api.enroll_speaker_from_clip(api.clip_id,'a','',[0,48000])
    assert entered.wait(2)
    api.cancel_voice_learning(result['action_id']);release.set()
    api._analysis_queue.close()
    assert not api.get_voice_profiles()['samples']
    assert api._store.get_clip(api.clip_id)['speaker_id']=='a'


def test_external_npz_is_not_read_implicitly(api,tmp_path,monkeypatch):
    external=tmp_path/'external.npz';external.write_bytes(b'not an NPZ')
    api._ai.ref_voices_path=external
    assert api.get_voice_profiles()['legacy']=={}
    assert not external.with_suffix('.npz.bak').exists()


def test_policy_change_invalidates_only_speaker_analysis(api):
    clip=api._store.get_clip(api.clip_id)
    clip['analysis_generations']={'transcription':4,'speakers':2}
    api.update_settings({'voice_match_margin':.2})
    assert clip['analysis_generations']=={'transcription':4,'speakers':3}


def test_cached_automatic_results_form_proposals_never_samples(api):
    learned=api.enroll_speaker_from_clip(api.clip_id,'a','',[0,48000]);finish(api,learned)
    clip=api._store.get_clip(api.clip_id)
    clip['field_provenance']={'speaker_id':'analysis:synthetic'}
    clip['speaker_id']='wrong'
    clip['segments']=[{'start_sample':0,'end_sample':48000,'speaker_id':'wrong',
        'voice_embedding':[1.,0.],'extractor_version':'synthetic-v1','local_cluster_id':'local:one:0'}]
    count=len(api.get_voice_profiles()['samples'])
    proposals=api.request_voice_review([api.clip_id])['proposals']
    assert len(proposals)==1 and proposals[0]['speaker_id']=='a'
    assert clip['speaker_id']=='wrong' and len(api.get_voice_profiles()['samples'])==count
    result=api.apply_voice_review([proposals[0]['proposal_id']])
    assert result['applied']==[api.clip_id] and clip['speaker_id']=='a'
    assert len(api.get_voice_profiles()['samples'])==count
    proposals=api.request_voice_review([api.clip_id])['proposals']
    api.update_settings({'voice_match_threshold':.9})
    assert api.apply_voice_review([proposals[0]['proposal_id']])['obsolete']


def test_undo_updates_open_draft_and_profile_deletion_is_atomic(api, monkeypatch):
    first=api.enroll_speaker_from_clip(api.clip_id,'a','',[0,48000]);finish(api,first)
    api._store.begin_edit(api.clip_id)
    api._store.update_edit(api.clip_id, {'speaker_id':'a', 'speaker_ids':['a']})
    assert api.undo_voice_correction(first['action_id'])['ok']
    assert api._store.render_context(api.clip_id,True)['speaker_id']==''
    second=api.enroll_speaker_from_clip(api.clip_id,'b','',[48000,96000]);finish(api,second)
    before=api._store.snapshot()
    monkeypatch.setattr(api._store,'save',lambda:(_ for _ in ()).throw(OSError('disk')))
    with pytest.raises(OSError): api.delete_speaker('b')
    assert api._store.snapshot()==before


def test_failed_pending_sample_can_be_reconfirmed_when_extractor_recovers(api):
    api._ai.compute_embedding=lambda audio:None
    failed=api.enroll_speaker_from_clip(api.clip_id,'a','',[0,48000]);finish(api,failed)
    api._ai.compute_embedding=lambda audio:np.array([1.,0.])
    recovered=api.enroll_speaker_from_clip(api.clip_id,'a','',[0,48000]);finish(api,recovered)
    samples=api.get_voice_profiles()['samples']
    assert len(samples)==1 and samples[0]['used_in_profile'] is True


def test_preview_uses_monitor_and_retains_source_until_owned_finish(api):
    result=api.enroll_speaker_from_clip(api.clip_id,'a','',[0,48000]);finish(api,result)
    sample=api.get_voice_profiles()['samples'][0];calls=[]
    api._audio=SimpleNamespace(play_clip=lambda **kwargs:calls.append(kwargs) or 'owned')
    assert api.preview_voice_sample(sample['sample_id'])['play_id']=='owned'
    assert calls[0]['monitor_only'] is True
    assert calls[0]['settings']['overlap_audio'] is True
    assert 'active' in api._store.source_references(sample['source_id'])
    calls[0]['on_finish']()
    assert 'active' not in api._store.source_references(sample['source_id'])


def test_queued_extraction_keeps_original_analyzer_version(api):
    api._analysis_queue.close()
    api._analysis_queue=AnalysisQueue(autostart=False)
    original=api._ai
    result=api.enroll_speaker_from_clip(api.clip_id,'a','',[0,48000])
    api._ai=SimpleNamespace(compute_embedding=lambda audio:np.array([0.,1.,0.]),extractor_version='new-model')
    api._analysis_queue.start();finish(api,result)
    sample=api._voices().snapshot()['samples'].popitem()[1]
    assert sample['extractor_version']=='synthetic-v1' and sample['embedding']==[1.,0.]


def test_voice_bank_rebinds_when_analyzer_is_replaced(api):
    bank=api._voices()
    api._ai=SimpleNamespace()
    api._voices()
    assert api._ai.voice_profiles is bank


def test_undo_reassignment_restores_previous_reference_and_clear_retracts_it(api):
    api._store.update_settings({'learn_voice_corrections':True})
    first=api.enroll_speaker_from_clip(api.clip_id,'a','',[0,48000]);finish(api,first)
    second=api.enroll_speaker_from_clip(api.clip_id,'b','',[0,48000]);finish(api,second)
    assert api.undo_voice_correction(second['action_id'])['ok']
    assert api.get_voice_profiles()['samples'][0]['speaker_id']=='a'
    cleared=api.save_clip_edits({'id':api.clip_id,'speaker_id':''})
    assert not api.get_voice_profiles()['samples']
    assert api.undo_voice_correction(cleared['action_id'])['ok']
    assert api.get_voice_profiles()['samples'][0]['speaker_id']=='a'


def test_deleting_person_while_learning_cannot_resurrect_person_or_sample(api):
    import threading
    entered,release=threading.Event(),threading.Event()
    def embed(audio): entered.set();release.wait(3);return np.array([1.,0.])
    api._ai.compute_embedding=embed
    result=api.enroll_speaker_from_clip(api.clip_id,'a','',[0,48000])
    assert entered.wait(2)
    assert api.delete_speaker('a')['ok']
    release.set();finish(api,result)
    state=api.get_voice_profiles()
    assert not state['samples'] and 'a' not in state['speakers']
    assert api.undo_voice_correction(result['action_id'])['ok'] is False


def test_repeated_legacy_multivoice_enrollment_cannot_erase_source_evidence(api):
    api._store.add_or_update_clip({'id':api.clip_id,'speaker_id':'a','speaker_ids':['a','b'],'segments':[]})
    for _ in range(2):
        result=api.enroll_speaker_from_clip(api.clip_id,'b')
        finish(api,result)
        assert result['assignment_saved'] and 'selecione' in result.get('error','').lower()
        assert not api.get_voice_profiles()['samples']


@pytest.mark.parametrize('initial_person', ['a','b'])
@pytest.mark.parametrize('interval', [None,[0,144000],[0,48000]])
def test_save_then_enroll_preserves_multivoice_without_anchors(api,initial_person,interval):
    api._store.add_or_update_clip({'id':api.clip_id,'speaker_id':initial_person,'speaker_ids':['a','b'],'segments':[]})
    api._store.update_settings({'learn_voice_corrections':True})
    # O autosave do editor persiste o campo antes de Salvar/Cadastrar; a pessoa
    # primária pode já ser B e a seleção inicial pode cobrir toda a captura.
    api._store.begin_edit(api.clip_id)
    api._store.update_edit(api.clip_id,{'speaker_id':'b','speaker_ids':['b']})
    saved=api.save_clip_edits({'id':api.clip_id,'voice_interval':interval})
    finish(api,saved)
    enrolled=api.enroll_speaker_from_clip(api.clip_id,'b','',interval)
    finish(api,enrolled)
    samples=api.get_voice_profiles()['samples']
    if interval is None:
        assert 'selecione' in enrolled.get('error','').lower() and not samples
    else:
        assert len(samples)==1 and samples[0]['eligibility']=='pending'
        assert samples[0]['quality']['mixed'] is None
        assert 'uncertain_mix' in samples[0]['quality']['reasons']
    assert not api._voices().prototypes('synthetic-v1')


def test_anchored_single_voice_selection_remains_eligible_after_manual_relabel(api):
    api._store.add_or_update_clip({'id':api.clip_id,'speaker_id':'a','speaker_ids':['a','b'],'segments':[
        {'speaker_id':'a','start_sample':0,'end_sample':48000},
        {'speaker_id':'b','start_sample':48000,'end_sample':144000}]})
    api.save_clip_edits({'id':api.clip_id,'speaker_id':'b','voice_interval':[0,144000]})
    result=api.enroll_speaker_from_clip(api.clip_id,'b','',[0,48000]);finish(api,result)
    sample=api.get_voice_profiles()['samples'][0]
    assert sample['eligibility']=='eligible' and sample['quality']['mixed'] is False
    assert sample['spans']==[[0,48000]]


def test_multivoice_evidence_survives_restart_and_shared_source_clip(api):
    api._store.add_or_update_clip({'id':api.clip_id,'speaker_id':'a','speaker_ids':['a','b'],'segments':[]})
    api.enroll_speaker_from_clip(api.clip_id,'b')
    other=copy.deepcopy(api._store.get_clip(api.clip_id))
    other.update(id='other',speaker_id='b',speaker_ids=['b'])
    other.pop('voice_source_evidence',None)
    api._store.add_or_update_clip(other)
    api._store.close();api._store=SoundboardStore(str(api._store.data_dir))
    if hasattr(api,'_voice_profiles'):del api._voice_profiles
    result=api.enroll_speaker_from_clip('other','b','',[0,144000]);finish(api,result)
    assert api.get_voice_profiles()['samples'][0]['quality']['mixed'] is None


@pytest.mark.parametrize('segments,expected_mixed', [([],None),([
    {'speaker_id':'a','start_sample':0,'end_sample':48000},
    {'speaker_id':'b','start_sample':48000,'end_sample':144000}],True)])
def test_cached_sample_is_reevaluated_when_source_reveals_multiple_voices(api,segments,expected_mixed):
    calls=[]
    api._ai.compute_embedding=lambda audio:calls.append(len(audio)) or np.array([1.,0.])
    first=api.enroll_speaker_from_clip(api.clip_id,'b','',[0,144000]);finish(api,first)
    assert api.get_voice_profiles()['samples'][0]['used_in_profile']
    # Metadados posteriores revelam duas vozes na mesma origem; o embedding
    # pode ser reutilizado, mas sua antiga avaliação de mistura não é prova.
    api._store.add_or_update_clip({'id':api.clip_id,'speaker_id':'b','speaker_ids':['a','b'],'segments':segments})
    again=api.enroll_speaker_from_clip(api.clip_id,'b','',[0,144000]);finish(api,again)
    samples=api.get_voice_profiles()['samples']
    assert len(samples)==1 and samples[0]['eligibility']=='pending'
    assert samples[0]['quality']['mixed'] is expected_mixed
    assert not samples[0]['used_in_profile'] and not api._voices().prototypes('synthetic-v1')
    assert calls==[48000]
    assert api.undo_voice_correction(again['action_id'])['ok']
    assert not api._voices().prototypes('synthetic-v1')
    assert api.undo_voice_correction(first['action_id'])['ok']
    assert not api.get_voice_profiles()['samples']


def test_editor_child_does_not_inherit_parent_voice_correction(api):
    learned=api.enroll_speaker_from_clip(api.clip_id,'a','',[0,48000]);finish(api,learned)
    api.begin_clip_edit(api.clip_id)
    child=api.edit_clip_command(api.clip_id,'create',{'interval':[0,48000]})['clip']
    assert not child.get('voice_action_id')
    before=api._store.snapshot()
    result=api.undo_voice_correction(learned['action_id'],child['id'])
    assert result['ok'] is False
    assert api._store.snapshot()==before
    assert api.undo_voice_correction(learned['action_id'],api.clip_id)['ok']
    assert api._store.get_clip(child['id'])['speaker_id']=='a'


@pytest.mark.parametrize('shared_clip',[False,True])
def test_removing_sample_invalidates_queued_cached_enrollment(api,shared_clip):
    learned=api.enroll_speaker_from_clip(api.clip_id,'a','',[0,48000]);finish(api,learned)
    sample=api.get_voice_profiles()['samples'][0]
    target=api.clip_id
    if shared_clip:
        api.begin_clip_edit(api.clip_id)
        target=api.edit_clip_command(api.clip_id,'create',{'interval':[0,48000]})['clip']['id']
    api._analysis_queue.close();api._analysis_queue=AnalysisQueue(autostart=False)
    queued=api.enroll_speaker_from_clip(target,'a','',[0,48000])
    assert api.remove_voice_sample(sample['sample_id'])['ok']
    api._analysis_queue.start();finish(api,queued)
    assert not api.get_voice_profiles()['samples']
    assert 'active' not in api._store.source_references(sample['source_id'])
    later=api.enroll_speaker_from_clip(target,'a','',[0,48000]);finish(api,later)
    assert len(api.get_voice_profiles()['samples'])==1


def test_voice_review_rejects_incomplete_segments_without_losing_text_or_timing(api):
    learned=api.enroll_speaker_from_clip(api.clip_id,'a','',[0,48000]);finish(api,learned)
    clip=api._store.get_clip(api.clip_id)
    clip['field_provenance']={'speaker_id':'analysis:synthetic'}
    clip['segments']=[
        {'id':'first','text':'primeira fala','start_sample':0,'end_sample':48000,'speaker_id':'wrong',
         'voice_embedding':[1.,0.],'extractor_version':'synthetic-v1'},
        {'id':'second','text':'fala sem embedding','start_sample':48000,'end_sample':96000,'speaker_id':'other'}]
    before=copy.deepcopy(clip)
    review=api.request_voice_review([api.clip_id])
    assert review['requires_speaker_analysis']==[api.clip_id]
    assert review['proposals']==[]
    assert clip==before


def test_sample_removal_does_not_invalidate_pending_different_interval(api):
    learned=api.enroll_speaker_from_clip(api.clip_id,'a','',[0,48000]);finish(api,learned)
    sample=api.get_voice_profiles()['samples'][0]
    api._analysis_queue.close();api._analysis_queue=AnalysisQueue(autostart=False)
    queued=api.enroll_speaker_from_clip(api.clip_id,'a','',[48000,96000])
    api.remove_voice_sample(sample['sample_id'])
    api._analysis_queue.start();finish(api,queued)
    samples=api.get_voice_profiles()['samples']
    assert len(samples)==1 and samples[0]['spans']==[[48000,96000]]
    assert samples[0]['used_in_profile']


def real_voice_api(directory, monkeypatch):
    import main
    monkeypatch.setenv('SOUNDBOARD_MODELS_DIR',str(directory/'models'))
    monkeypatch.setenv('SOUNDBOARD_SEPARADOS_ROOT',str(directory/'separados'))
    monkeypatch.setattr(main,'AudioEngine',lambda **kwargs:SimpleNamespace())
    monkeypatch.setattr(main,'HotkeyManager',lambda **kwargs:SimpleNamespace(refresh=lambda *args:None))
    monkeypatch.setattr(SoundboardAPI,'_auto_bind_devices',lambda self:None)
    instance=SoundboardAPI(str(directory),start_background_services=False)
    instance._ai.compute_embedding=lambda audio:np.array([1.,0.])
    return instance


def test_real_api_restart_marks_orphan_learning_interrupted_without_resuming(tmp_path,monkeypatch):
    directory=tmp_path/'restart'
    original=real_voice_api(directory,monkeypatch)
    path=original._store.clips_dir/'synthetic.wav'
    sf.write(path,.2*np.sin(np.arange(96000)*.05),48000,subtype='FLOAT')
    clip=original._store.add_imported_clip({'file_path':str(path)},rendered=True)
    original._store.begin_edit(clip['id']);original._store.commit_edit(clip['id'])
    original._analysis_queue.close();original._analysis_queue=AnalysisQueue(autostart=False)
    pending=original.enroll_speaker_from_clip(clip['id'],'friend','',[0,48000])
    assert original.get_voice_profiles()['actions'][pending['action_id']]['state']=='pending'
    # Snapshot durável anterior ao encerramento limpo simula a perda do processo.
    db=original._store.db_path;crash_image=db.read_bytes()
    original._analysis_queue.close();original._store.close();db.write_bytes(crash_image)
    reopened=real_voice_api(directory,monkeypatch)
    try:
        action=reopened.get_voice_profiles()['actions'][pending['action_id']]
        assert action['state']=='interrupted' and 'interrompido' in action['error'].lower()
        assert reopened.list_analysis_jobs()['jobs']==[]
        assert reopened._store.get_clip(clip['id'])['speaker_id']=='friend'
        fresh=reopened.enroll_speaker_from_clip(clip['id'],'friend','',[0,48000]);finish(reopened,fresh)
        assert reopened.get_voice_profiles()['actions'][fresh['action_id']]['reference_learned']
        assert reopened.undo_voice_correction(fresh['action_id'],clip['id'])['ok']
        assert reopened.undo_voice_correction(pending['action_id'],clip['id'])['ok']
        assert reopened._store.get_clip(clip['id'])['speaker_id']==''
    finally:
        reopened._analysis_queue.close();reopened._store.close()


def test_real_api_restart_preserves_ready_states_and_read_does_not_cancel_active_job(tmp_path,monkeypatch):
    directory=tmp_path/'restart-ready'
    original=real_voice_api(directory,monkeypatch)
    path=original._store.clips_dir/'synthetic.wav'
    sf.write(path,.2*np.sin(np.arange(96000)*.05),48000,subtype='FLOAT')
    clip=original._store.add_imported_clip({'file_path':str(path)},rendered=True)
    original._store.begin_edit(clip['id']);original._store.commit_edit(clip['id'])
    learned=original.enroll_speaker_from_clip(clip['id'],'friend','',[0,48000]);finish(original,learned)
    original._ai.compute_embedding=lambda audio:None
    review=original.enroll_speaker_from_clip(clip['id'],'friend','',[48000,96000]);finish(original,review)
    before=original.get_voice_profiles()['actions']
    assert before[learned['action_id']]['state']=='learned'
    assert before[review['action_id']]['state']=='pending_review'
    original._analysis_queue.close();original._store.close()
    reopened=real_voice_api(directory,monkeypatch)
    try:
        assert reopened.get_voice_profiles()['actions']==before
        assert reopened.list_analysis_jobs()['jobs']==[]
        reopened._analysis_queue.close();reopened._analysis_queue=AnalysisQueue(autostart=False)
        current=reopened.enroll_speaker_from_clip(clip['id'],'friend','',[48000,96000])
        for _ in range(2):
            assert reopened.get_voice_profiles()['actions'][current['action_id']]['state']=='pending'
    finally:
        reopened._analysis_queue.close();reopened._store.close()
