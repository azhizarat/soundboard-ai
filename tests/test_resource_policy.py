import threading
from pathlib import Path
import numpy as np
import pytest
import soundfile as sf

from analysis_jobs import AnalysisQueue


def test_playing_defers_background_but_runs_explicit_and_pause_cancels_safely():
    q = AnalysisQueue(autostart=False)
    q.set_policy('playing', False)
    order = []
    background = q.submit(lambda: order.append('background') or {})
    explicit = q.submit(lambda: order.append('explicit') or {}, priority=0)
    q.start()
    assert q.wait(explicit['analysis_id'], 2)['status'] == 'ready'
    assert q.get(background['analysis_id'])['status'] == 'queued'
    q.set_policy('reviewing', True)
    assert q.cancel(background['analysis_id'])
    q.close()
    assert order == ['explicit']


def test_pause_does_not_claim_to_preempt_running_and_close_releases_pending():
    q = AnalysisQueue()
    entered, finish = threading.Event(), threading.Event()
    cleanup = []
    active = q.submit(lambda: (entered.set(), finish.wait(2), {})[-1])
    assert entered.wait(2)
    q.set_policy('playing', True)
    pending = q.submit(lambda: {}, cleanup=lambda: cleanup.append(1))
    assert q.get(active['analysis_id'])['status'] == 'running'
    q.close(wait=False)
    finish.set()
    q.close()
    assert cleanup == [1]
    assert q.get(pending['analysis_id'])['status'] == 'cancelled'


def test_policy_and_confirmed_dictionary_persist_without_replacement_rules(tmp_path):
    import config
    loaded = config.load_config(tmp_path)
    updated = config.save_config(loaded, {'analysis_profile': 'playing', 'analysis_paused': True,
                                        'personal_dictionary': ['TitaNet', 'Lucas']})
    restored = config.load_config(tmp_path)
    assert restored.analysis_profile == updated.analysis_profile == 'playing'
    assert restored.analysis_paused is True
    assert restored.personal_dictionary == ['TitaNet', 'Lucas']


def test_analysis_metrics_log_drops_personal_text(caplog):
    from app_logging import log_analysis_metric
    import logging
    with caplog.at_level(logging.INFO):
        log_analysis_metric('ready', duration_ms=3, transcript='PRIVATE WORDS', source_path='PRIVATE PATH')
    assert 'duration_ms' in caplog.text
    assert 'PRIVATE' not in caplog.text


def test_explicit_priority_can_release_one_queued_job_while_playing():
    q=AnalysisQueue(autostart=False)
    q.set_policy('playing',False)
    first=q.submit(lambda:{})
    second=q.submit(lambda:{})
    assert q.reprioritize(second['analysis_id'],True)
    q.start()
    assert q.wait(second['analysis_id'],2)['status']=='ready'
    assert q.get(first['analysis_id'])['status']=='queued'
    assert not q.reprioritize(second['analysis_id'],True)
    q.close()


@pytest.fixture
def replay_api(tmp_path, monkeypatch):
    from main import SoundboardAPI
    api=SoundboardAPI(str(tmp_path/'data'),start_background_services=False)
    api._hotkeys.enabled=False
    monkeypatch.setattr(api._hotkeys,'refresh',lambda *a,**k:None)
    monkeypatch.setattr(api._ai,'analyze_file',lambda *a,**k:pytest.fail('Replay pausado executou IA'))
    yield api
    api._analysis_queue.close();api._sequence_runner.close();api._audio.shutdown();api._store.close()


@pytest.mark.parametrize('method',['capture_replay_clip','capture_replay_draft'])
@pytest.mark.parametrize('pause_during_capture',[False,True])
def test_paused_replay_keeps_editable_audio_and_explicit_deferred_analysis(replay_api,monkeypatch,method,pause_during_capture):
    api=replay_api
    pcm=np.random.default_rng(5).normal(0,.05,(48000,2)).astype('float32')
    def capture(path,**kwargs):
        sf.write(path,pcm,48000,subtype='FLOAT')
        if pause_during_capture: api.set_resource_policy('playing',True)
        return path
    monkeypatch.setattr(api._audio,'save_replay_wav',capture)
    api.set_resource_policy('playing',not pause_during_capture)
    result=getattr(api,method)(1)
    assert result['ok'] and result['analysis_status']=='deferred'
    assert result['warnings'] and result['clip']['capture_analysis']['reason']=='analysis_paused'
    clip=result['clip']
    assert not clip['transcript'] and not clip['segments'] and not clip['speaker_ids']
    assert clip['acoustic_mood']=='Não analisado'
    assert clip['field_provenance']['category']=='capture_default'
    np.testing.assert_array_equal(sf.read(clip['file_path'],dtype='float32')[0],pcm)
    assert api.begin_clip_edit(clip['id'])['ok']
    assert api.update_clip_edit({'id':clip['id'],'title':'Edição manual preservada'})['ok']
    source=api._store.render_context(clip['id'],draft=True)['_source']
    np.testing.assert_array_equal(sf.read(source['file_path'],dtype='float32')[0],pcm)
    assert not api.get_voice_profiles()['samples']


def test_fast_paused_replays_in_same_second_never_overwrite_previous_pcm(replay_api,monkeypatch):
    import main
    api=replay_api
    captures=[]
    def capture(path,**kwargs):
        captures.append(path)
        sf.write(path,np.full((4800,2),len(captures)*.1,dtype='float32'),48000,subtype='FLOAT')
        return path
    monkeypatch.setattr(main.time,'strftime',lambda *a:'same-second')
    monkeypatch.setattr(api._audio,'save_replay_wav',capture)
    api.set_resource_policy('playing',True)
    first=api.capture_replay_clip(1)['clip']
    second=api.capture_replay_clip(1)['clip']
    assert first['id']!=second['id'] and captures[0]!=captures[1]
    assert len(api._store.snapshot()['clips'])==2
    np.testing.assert_array_equal(sf.read(first['file_path'],dtype='float32')[0],np.full((4800,2),.1,dtype='float32'))
    np.testing.assert_array_equal(sf.read(second['file_path'],dtype='float32')[0],np.full((4800,2),.2,dtype='float32'))


def test_paused_replay_rejects_undecodable_capture_instead_of_fake_success(replay_api,monkeypatch):
    api=replay_api
    def broken(path,**kwargs):
        Path(path).write_bytes(b'not audio')
        return path
    monkeypatch.setattr(api._audio,'save_replay_wav',broken)
    api.set_resource_policy('playing',True)
    result=api.capture_replay_draft(1)
    assert not result['ok'] and result['error_code']=='decode_error'
    assert not api._store.snapshot()['clips']


def test_concurrent_dictionary_saves_keep_disk_and_runtime_in_same_order(replay_api,monkeypatch):
    import config
    api=replay_api
    original=config.save_config
    first_saved,release,second_attempted,second_finished=[threading.Event() for _ in range(4)]
    def save(current,updates):
        result=original(current,updates)
        if updates.get('personal_dictionary')==['first']:
            first_saved.set();assert release.wait(3)
        return result
    monkeypatch.setattr(config,'save_config',save)
    outcomes=[]
    def first(): outcomes.append(api.save_personal_dictionary(['first'],True))
    def second():
        second_attempted.set();outcomes.append(api.save_personal_dictionary(['last'],True));second_finished.set()
    one=threading.Thread(target=first);two=threading.Thread(target=second)
    one.start();assert first_saved.wait(3)
    two.start();assert second_attempted.wait(3)
    try:
        assert not second_finished.wait(.1),'Outro save ultrapassou persistência/aplicação ainda em curso'
    finally:
        release.set();one.join(3);two.join(3)
    assert len(outcomes)==2 and all(outcome['ok'] for outcome in outcomes)
    assert config.load_config(api._store.data_dir).personal_dictionary==['last']
    assert api._config.personal_dictionary==['last']
    assert api._ai.transcription_config['hotwords']=='last'


def test_dictionary_publishes_one_complete_transcription_configuration(replay_api,monkeypatch):
    api=replay_api
    calls=[]
    original=api._ai.configure_transcription
    def configure(options): calls.append(dict(options));return original(options)
    monkeypatch.setattr(api._ai,'configure_transcription',configure)
    assert api.save_personal_dictionary(['confirmed'],True)['ok']
    assert len(calls)==1
    assert calls[0]['hotwords']=='confirmed'
    assert all(key in calls[0] for key in ('model','device','beam_size','language','initial_prompt'))

