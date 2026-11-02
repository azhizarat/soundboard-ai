import threading
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import soundfile as sf
import pytest

from main import SoundboardAPI
from store import SoundboardStore


@pytest.fixture
def api(tmp_path):
    result = SoundboardAPI.__new__(SoundboardAPI)
    result._store = SoundboardStore(str(tmp_path/'library'))
    result._window = None
    result._config = SimpleNamespace(ffmpeg_path='ffmpeg')
    result._notify_ui = lambda *args: None
    yield result
    if hasattr(result, '_imports'):
        result._imports.close()
    result._store.close()


def wav(path, seconds=1):
    sf.write(path, np.full((int(seconds*48000), 2), .1, dtype=np.float32), 48000)
    return str(path)


def settled(api):
    deadline = time.monotonic()+5
    while time.monotonic() < deadline:
        jobs = api.list_import_jobs()['jobs']
        if all(j['status'] not in ('queued','probing','running') for j in jobs):
            return jobs
        time.sleep(.01)
    pytest.fail('Fila não terminou')


def test_queue_isolates_invalid_deduplicates_content_and_retries(api, tmp_path):
    assert hasattr(api, 'enqueue_import'), 'Fila unificada ausente'
    path = wav(tmp_path/'a.wav')
    other = tmp_path/'b.wav'; other.write_bytes((tmp_path/'a.wav').read_bytes())
    missing = tmp_path/'missing.wav'
    result = api.enqueue_import([path, str(missing), str(other)])
    assert result['ok']
    jobs = settled(api)
    assert [j['status'] for j in jobs] == ['success','failed','duplicate']
    assert len(api._store.snapshot()['clips']) == 1
    wav(missing, .5)
    assert api.retry_import_job(jobs[1]['id'])['ok']
    assert settled(api)[1]['status'] == 'success'
    assert len(settled(api)[1]['history']) >= 4


def test_long_file_waits_for_window_and_windows_have_distinct_identity(api, tmp_path, monkeypatch):
    assert hasattr(api, 'enqueue_import'), 'Fila unificada ausente'
    import import_queue
    monkeypatch.setattr(import_queue, 'LONG_SECONDS', .5)
    path = wav(tmp_path/'long.wav', 1)
    api.enqueue_import([path, path])
    jobs = settled(api)
    assert all(j['status'] == 'awaiting_window' for j in jobs)
    assert not api._store.snapshot()['clips']
    assert api.set_import_window(jobs[0]['id'], 0, .2)['ok']
    assert api.set_import_window(jobs[1]['id'], .3, .5)['ok']
    jobs = settled(api)
    assert all(j['status'] == 'success' for j in jobs)
    assert len(api._store.snapshot()['clips']) == 2
    assert all(sf.info(c['file_path']).duration == pytest.approx(.2) for c in api._store.snapshot()['clips'])


def test_cancel_during_decode_prevents_late_publish(api, tmp_path, monkeypatch):
    assert hasattr(api, 'enqueue_import'), 'Fila unificada ausente'
    import import_queue
    started, release = threading.Event(), threading.Event()
    original = import_queue.decode_window
    def slow(*args):
        started.set(); assert release.wait(3)
        return original(*args)
    monkeypatch.setattr(import_queue, 'decode_window', slow)
    ident = api.enqueue_import([wav(tmp_path/'a.wav')])['jobs'][0]['id']
    assert started.wait(3)
    assert api.cancel_import_job(ident)['ok']
    release.set()
    api._imports.close()
    assert api.list_import_jobs()['jobs'][0]['status'] == 'cancelled'
    assert not api._store.snapshot()['clips']


def test_batch_metadata_does_not_overwrite_later_human_edit(api, tmp_path):
    assert hasattr(api, 'enqueue_import'), 'Fila unificada ausente'
    api.enqueue_import([wav(tmp_path/'a.wav')])
    job = settled(api)[0]
    clip = api._store.get_clip(job['clip_id'])
    api._store.add_or_update_clip({'id': clip['id'], 'category': 'Meme'})
    result = api.apply_import_metadata([job['id']], {'category': 'Vinhetas'})
    assert result['ok']
    assert api._store.get_clip(clip['id'])['category'] == 'Meme'
    assert result['conflicts']


def test_automatic_analysis_uses_background_policy_and_sequential_stages(api,tmp_path):
    policy = {'paused':True}
    calls=[]
    api._analysis_queue=SimpleNamespace(allows=lambda explicit=False:not policy['paused'], policy=lambda:policy)
    def request(clip_id,stage,priority=True):
        calls.append((clip_id,stage,priority))
        return {'ok':True,'analysis_id':stage}
    api.request_clip_analysis=request
    api.get_analysis_job=lambda ident:{'ok':True,'status':'ready','application':{'state':'applied'}}
    api.enqueue_import([wav(tmp_path/'a.wav')],skip_speaker_id=True)
    job=settled(api)[0]
    assert job['status']=='success'
    assert 'analysis' in job, 'Status de análise separado ausente'
    assert not calls
    policy['paused']=False
    deadline=time.monotonic()+5
    while time.monotonic()<deadline:
        if api.list_import_jobs()['jobs'][0]['analysis']['status']=='ready': break
        time.sleep(.02)
    assert [stage for _,stage,_ in calls]==['transcription','acoustics','metadata']
    assert all(not priority for _,_,priority in calls)


def test_batch_preserves_human_changes_in_open_editor(api,tmp_path):
    api.enqueue_import([wav(tmp_path/'a.wav')],auto_analyze=False)
    job=settled(api)[0]
    api._store.begin_edit(job['clip_id'])
    api._store.update_edit(job['clip_id'],{'category':'Meme'})
    result=api.apply_import_metadata([job['id']],{'category':'Vinhetas'})
    assert result['conflicts'], 'Edição humana aberta deve impedir alteração em lote'
    assert api._store.render_context(job['clip_id'],draft=True)['category']=='Meme'


def test_folder_discovery_stops_at_admission_limit(api,tmp_path,monkeypatch):
    import import_queue
    folder=tmp_path/'folder';folder.mkdir()
    for i in range(8): wav(folder/f'{i}.wav',.01)
    monkeypatch.setattr(import_queue,'MAX_JOBS',3)
    original=import_queue.Path.rglob
    visited=[]
    def bounded(path,pattern):
        for entry in original(path,pattern):
            visited.append(entry)
            if len(visited)>4: raise AssertionError('Descoberta ultrapassou o limite')
            yield entry
    monkeypatch.setattr(import_queue.Path,'rglob',bounded)
    result=api.enqueue_import([str(folder)])
    assert not result['ok']
    assert 'limite' in result['error'].lower()
    assert len(visited)==4


def test_native_drop_uses_only_full_native_paths_and_respects_manual_choice(api,tmp_path):
    assert hasattr(api,'set_import_options'), 'Preferência do drop ausente'
    path=wav(tmp_path/'a.wav')
    api.set_import_options(False)
    api._on_native_drop({'dataTransfer':{'files':[{'name':'invented.wav'}, {'pywebviewFullPath':path}]}})
    jobs=settled(api)
    assert len(jobs)==1 and jobs[0]['source']==str(Path(path).resolve())
    assert jobs[0]['analysis']['status']=='manual'


def test_float_and_integer_window_dedupe_and_atomic_publish_failure(api,tmp_path,monkeypatch):
    import import_queue
    monkeypatch.setattr(import_queue,'LONG_SECONDS',.5)
    path=wav(tmp_path/'long.wav')
    api.enqueue_import([path,path],auto_analyze=False)
    jobs=settled(api)
    api.set_import_window(jobs[0]['id'],0,1)
    settled(api)
    api.set_import_window(jobs[1]['id'],0.0,1.0)
    assert settled(api)[1]['status']=='duplicate'


def test_publish_save_failure_rolls_back_clip_and_continues(api,tmp_path,monkeypatch):
    original=api._store.save
    failed=False
    def fail_once():
        nonlocal failed
        if api._store.state['clips'] and any(j['status']=='success' for j in api._store.state.get('import_jobs',[])) and not failed:
            failed=True
            raise OSError('disk unavailable')
        return original()
    monkeypatch.setattr(api._store,'save',fail_once)
    api.enqueue_import([wav(tmp_path/'a.wav'),wav(tmp_path/'b.wav',.5)],auto_analyze=False)
    jobs=settled(api)
    assert [j['status'] for j in jobs]==['failed','success']
    assert len(api._store.state['clips'])==1
    assert len(list(api._store.clips_dir.glob('*.wav')))==1


@pytest.mark.parametrize('action',['cancel','close'])
def test_cancel_or_close_analysis_does_not_publish_late_result_or_block_media(api,tmp_path,action):
    from analysis_jobs import AnalysisQueue
    queue=AnalysisQueue(capacity=8)
    api._analysis_queue=queue
    started,release=threading.Event(),threading.Event()
    applied=[]
    def request(clip_id,stage,priority=True):
        def run():
            started.set(); assert release.wait(4)
            return {'status':'ready'}
        return queue.submit(run,apply=lambda result:applied.append(clip_id),priority=10,metadata={'clip_id':clip_id,'stage':stage})
    api.request_clip_analysis=request
    try:
        result=api.enqueue_import([wav(tmp_path/'a.wav'),wav(tmp_path/'b.wav',.5)])
        assert started.wait(3)
        assert all(j['status']=='success' for j in settled(api)), 'Fila de mídia não espera inferência'
        if action=='cancel':
            for job in result['jobs']: assert api.cancel_import_job(job['id'])['ok']
        else: api._imports.close()
        release.set(); queue.close()
        assert not applied
        assert all(j['analysis']['status']==('cancelled' if action=='cancel' else 'interrupted') for j in api.list_import_jobs()['jobs'])
    finally:
        release.set();queue.close()


def test_explicit_category_and_separados_mapping_are_preserved_by_auto_pipeline(api,tmp_path):
    import json
    folder=tmp_path/'separados';folder.mkdir()
    wav(folder/'fala.wav')
    (folder/'mapa_de_falas.json').write_text(json.dumps([{'arquivo_trecho':'fala.wav','transcricao':'Texto fornecido','interlocutor':7}]),encoding='utf-8')
    api.enqueue_import([str(folder)],forced_category='Vinhetas')
    job=settled(api)[0];clip=api._store.get_clip(job['clip_id'])
    assert clip['field_provenance']['category']=='human'
    assert clip['speaker_id']=='interlocutor_7' and clip['transcript']=='Texto fornecido'
    assert 'speakers' not in job['analysis']['stages']


def test_history_limit_never_discards_media_with_active_analysis(api,tmp_path,monkeypatch):
    import import_queue
    monkeypatch.setattr(import_queue,'MAX_JOBS',1)
    policy={'paused':True};calls=[]
    api._analysis_queue=SimpleNamespace(allows=lambda explicit=False:not policy['paused'],policy=lambda:policy)
    api.request_clip_analysis=lambda clip_id,stage,priority=True:(calls.append((clip_id,stage)) or {'ok':True,'analysis_id':stage})
    api.get_analysis_job=lambda ident:{'ok':True,'status':'ready','application':{'state':'applied'}}
    api.enqueue_import([wav(tmp_path/'a.wav')],skip_speaker_id=True)
    first=settled(api)[0]
    result=api.enqueue_import([wav(tmp_path/'b.wav',.5)],auto_analyze=False)
    assert not result['ok'], 'Análise pendente não pode ser descartada para abrir vaga'
    assert api.list_import_jobs()['jobs'][0]['id']==first['id']
    policy['paused']=False
    deadline=time.monotonic()+4
    while time.monotonic()<deadline and api.list_import_jobs()['jobs'][0]['analysis']['status']!='ready': time.sleep(.02)
    assert [stage for _,stage in calls]==['transcription','acoustics','metadata']
    assert api.enqueue_import([str(tmp_path/'b.wav')],auto_analyze=False)['ok']
    assert settled(api)[0]['status']=='success'


@pytest.mark.parametrize('late_failure',[False,True])
def test_cancel_then_immediate_retry_requires_new_decode_attempt(api,tmp_path,monkeypatch,late_failure):
    import import_queue
    started,release=threading.Event(),threading.Event();original=import_queue.decode_window;calls=[]
    def slow(*args):
        calls.append(str(args[1]));result=original(*args)
        if len(calls)==1:
            started.set();assert release.wait(3)
            if late_failure: raise ValueError('Falha tardia da tentativa cancelada')
        return result
    monkeypatch.setattr(import_queue,'decode_window',slow)
    path=tmp_path/'a.wav';wav(path,.01)
    ident=api.enqueue_import([str(path)],auto_analyze=False)['jobs'][0]['id']
    assert started.wait(3);assert api.cancel_import_job(ident)['ok']
    sf.write(path,np.full((480,2),.7,dtype=np.float32),48000,subtype='FLOAT')
    assert api.retry_import_job(ident)['ok'];release.set()
    jobs=settled(api)
    assert jobs[0]['status']=='success',jobs
    assert len(calls)==2,'Tentativa cancelada não pode satisfazer o retry'
    assert calls[0]!=calls[1],'Cada tentativa possui arquivo de preparo próprio'
    audio,_=sf.read(api._store.get_clip(jobs[0]['clip_id'])['file_path'])
    assert audio[0,0]==pytest.approx(.7)


def test_batch_rollback_during_decode_does_not_detach_job_or_history(api,tmp_path,monkeypatch):
    import import_queue,json
    started,release=threading.Event(),threading.Event();original=import_queue.decode_window
    def slow(*args):
        started.set();assert release.wait(3);return original(*args)
    monkeypatch.setattr(import_queue,'decode_window',slow)
    ident=api.enqueue_import([wav(tmp_path/'a.wav')],auto_analyze=False)['jobs'][0]['id']
    assert started.wait(3)
    live_job=api._imports.jobs[0];live_analysis=live_job['analysis']
    with monkeypatch.context() as patch:
        patch.setattr(api._store,'save',lambda:(_ for _ in ()).throw(OSError('disk full')))
        assert not api.apply_import_metadata([ident],{'category':'Meme'})['ok']
    assert api._imports.jobs[0] is live_job and live_job['analysis'] is live_analysis
    release.set();api._imports.thread.join(3)
    job=api.list_import_jobs()['jobs'][0]
    assert job['status']=='success'
    assert api._store.get_clip(job['clip_id'])['category']=='Call'
    persisted=json.loads(api._store.db_path.read_text(encoding='utf-8'))
    assert persisted['import_jobs']==api.list_import_jobs()['jobs']
    assert api._imports.jobs is api._store.state['import_jobs']


def test_cancel_retry_during_analysis_submission_discards_old_generation(api,tmp_path):
    started,release=threading.Event(),threading.Event();calls=[];cancelled=[]
    api._analysis_queue=SimpleNamespace(allows=lambda:True,policy=lambda:{})
    def request(clip_id,stage,priority=True):
        ident=str(len(calls));calls.append(stage)
        if ident=='0': started.set();assert release.wait(3)
        return {'ok':True,'analysis_id':ident}
    api.request_clip_analysis=request
    api.get_analysis_job=lambda ident:{'ok':True,'status':'ready','application':{'state':'applied'}}
    api.cancel_analysis_job=lambda ident:cancelled.append(ident) or {'ok':True}
    ident=api.enqueue_import([wav(tmp_path/'a.wav')],skip_speaker_id=True)['jobs'][0]['id']
    assert started.wait(3);assert api.cancel_import_job(ident)['ok'];assert api.retry_import_analysis(ident)['ok']
    release.set();api._imports.analysis_thread.join(3)
    assert '0' in cancelled
    assert calls.count('transcription')==2
    assert api.list_import_jobs()['jobs'][0]['analysis']['status']=='ready'


def test_batch_rollback_during_analysis_submission_keeps_current_pipeline(api,tmp_path,monkeypatch):
    import json
    started,release=threading.Event(),threading.Event();calls=[]
    api._analysis_queue=SimpleNamespace(allows=lambda:True,policy=lambda:{})
    def request(clip_id,stage,priority=True):
        calls.append(stage)
        if len(calls)==1: started.set();assert release.wait(3)
        return {'ok':True,'analysis_id':stage}
    api.request_clip_analysis=request
    api.get_analysis_job=lambda ident:{'ok':True,'status':'ready','application':{'state':'applied'}}
    ident=api.enqueue_import([wav(tmp_path/'a.wav')],skip_speaker_id=True)['jobs'][0]['id']
    assert started.wait(3)
    with monkeypatch.context() as patch:
        patch.setattr(api._store,'save',lambda:(_ for _ in ()).throw(OSError('disk full')))
        assert not api.apply_import_metadata([ident],{'title':'Não persistido'})['ok']
    release.set();api._imports.analysis_thread.join(3)
    job=api.list_import_jobs()['jobs'][0]
    assert job['analysis']['status']=='ready'
    assert calls==['transcription','acoustics','metadata']
    assert api._store.get_clip(job['clip_id'])['title']=='a'
    assert json.loads(api._store.db_path.read_text(encoding='utf-8'))['import_jobs']==api.list_import_jobs()['jobs']


def test_publish_save_failure_then_retry_survives_restart(api,tmp_path,monkeypatch):
    original=api._store.save;failed=False
    def fail_once():
        nonlocal failed
        if api._store.state['clips'] and any(j['status']=='success' for j in api._store.state['import_jobs']) and not failed:
            failed=True;raise OSError('publication failure')
        return original()
    monkeypatch.setattr(api._store,'save',fail_once)
    ident=api.enqueue_import([wav(tmp_path/'a.wav')],auto_analyze=False)['jobs'][0]['id']
    assert settled(api)[0]['status']=='failed'
    assert not api._store.state['clips']
    assert api.retry_import_job(ident)['ok'];job=settled(api)[0]
    assert job['status']=='success'
    assert api._imports.jobs is api._store.state['import_jobs']
    api._imports.close();api._store.flush()
    reopened=SoundboardAPI.__new__(SoundboardAPI)
    reopened._store=SoundboardStore(str(api._store.data_dir))
    try:
        assert reopened.list_import_jobs()['jobs'][0]==job
        assert reopened._imports.thread is None and reopened._imports.analysis_thread is None
        assert len(reopened._store.state['clips'])==1
        assert len(list(reopened._store.clips_dir.glob('import-*.wav')))==1
    finally:
        reopened._imports.close();reopened._store.close()


@pytest.mark.parametrize('barrier',['before_submit','after_submit'])
def test_real_e6_cannot_apply_cancelled_import_before_submission_returns(api,tmp_path,monkeypatch,barrier):
    from analysis_jobs import AnalysisQueue
    queue=AnalysisQueue(capacity=8);api._analysis_queue=queue
    api._inference_lock=threading.RLock();api._ai=SimpleNamespace(transcription_config={})
    entered,release=threading.Event(),threading.Event();observed=[]
    original=queue.submit
    def blocked_submit(run,*args,**kwargs):
        if barrier=='before_submit':
            entered.set();assert release.wait(3)
        else:
            original_run=run
            def run():
                entered.set();assert release.wait(3);return original_run()
        result=original(run,*args,**kwargs)
        if result.get('ok'): observed.append(queue.wait(result['analysis_id'],3))
        else: observed.append(result)
        return result
    monkeypatch.setattr(queue,'submit',blocked_submit)
    try:
        ident=api.enqueue_import([wav(tmp_path/'a.wav',.1)],auto_analyze=False)['jobs'][0]['id']
        job=settled(api)[0];clip_id=job['clip_id']
        with api._imports.lock,api._store._lock:
            api._imports.jobs[0]['analysis']['stages']=['acoustics']
        assert api.retry_import_analysis(ident)['ok'];assert entered.wait(3)
        assert api.cancel_import_job(ident)['ok'];release.set()
        api._imports.analysis_thread.join(3)
        assert observed
        assert api.list_import_jobs()['jobs'][0]['analysis']['status']=='cancelled'
        assert observed[0].get('application',{}).get('state')!='applied'
        assert api._store.render_context(clip_id,draft=True)['acoustic_mood']=='Não analisado'
        if barrier=='before_submit': assert not observed[0]['ok']
        else: assert observed[0]['application']['state']=='obsolete'
    finally:
        release.set();api._imports.close();queue.close()


@pytest.mark.parametrize('failure',[False,True])
def test_import_application_scope_resets_on_return_and_exception(failure):
    import analysis_jobs
    assert hasattr(analysis_jobs,'guard_analysis_scope'),'Escopo interno de aplicação ausente'
    queue=analysis_jobs.AnalysisQueue();applied=[]
    try:
        try:
            with analysis_jobs.guard_analysis_scope(lambda:False,lambda callback,result:{'state':'obsolete'}):
                assert not queue.submit(lambda:{'status':'ready'})['ok']
                # Uma chamada em outra thread não herda a intenção do importador.
                outside=[]
                worker=threading.Thread(target=lambda:outside.append(queue.submit(lambda:{'status':'ready'},apply=lambda result:applied.append('other-thread'))))
                worker.start();worker.join(3);assert outside[0]['ok'];queue.wait(outside[0]['analysis_id'])
                if failure: raise ValueError('Falha simulada da submissão')
        except ValueError:
            assert failure
        manual=queue.submit(lambda:{'status':'ready'},apply=lambda result:applied.append('manual'))
        assert manual['ok'];queue.wait(manual['analysis_id'])
        assert applied==['other-thread','manual']
    finally: queue.close()
