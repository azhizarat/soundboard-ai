import numpy as np
import pytest


def test_cache_identity_eviction_and_isolation():
    from analysis_cache import AnalysisCache, cache_key
    cache = AnalysisCache(max_bytes=1000, max_items=2)
    key = cache_key('abc', [0, 20], 'asr', 'model/1', {'beam': 3})
    value = {'words': ['original']}
    cache.put(key, value)
    value['words'].append('edit')
    result = cache.get(key)
    result['words'].append('edit')
    assert cache.get(key) == {'words': ['original']}
    assert key != cache_key('abc', [0, 21], 'asr', 'model/1', {'beam': 3})
    assert key != cache_key('abc', [0, 20], 'asr', 'model/2', {'beam': 3})
    cache.put('b', 2); cache.put('c', 3)
    assert cache.get(key) is None
    cache.put('huge', np.zeros(1000))
    assert cache.get('huge') is None
    assert cache.stats()['bytes'] <= 1000


def test_asr_cache_rebinds_analysis_identity_and_profile_changes_do_not_recompute(tmp_path):
    from ai_analyzer import AIAnalyzer
    from types import SimpleNamespace as NS
    ai = AIAnalyzer(models_dir=str(tmp_path))
    calls = []
    class Model:
        def transcribe(self, audio, **kwargs):
            calls.append(kwargs)
            return iter([NS(start=0., end=.5, text='ola', words=[NS(start=0., end=.5, word='ola', probability=.9)])]), None
    ai._get_whisper = lambda: Model()
    pcm = np.ones(16000, dtype=np.float32)
    first = ai.transcribe_temporal(pcm, 'first', 'source1', [0,48000])
    first['text'] = 'human edit'
    ai.voice_profiles = object()
    second = ai.transcribe_temporal(pcm, 'second', 'source2', [48000,96000])
    assert second['text'] == 'ola'
    assert second['analysis_id'] == 'second'
    assert second['source_id'] == 'source2'
    assert second['words'][0]['start_sample'] == 48000
    assert len(calls) == 1
    ai.configure_transcription({'hotwords': 'nome confirmado'})
    ai.transcribe_temporal(pcm, 'third', 'source1', [0,48000])
    assert len(calls) == 2


def test_embedding_cache_is_content_and_extractor_specific(tmp_path):
    from ai_analyzer import AIAnalyzer
    ai = AIAnalyzer(models_dir=str(tmp_path))
    calls = []
    class Extractor:
        def create_stream(self): return self
        def accept_waveform(self, **kwargs): pass
        def input_finished(self): pass
        def is_ready(self, stream): return True
        def compute(self, stream): calls.append(1); return [1., 0.]
    ai._get_extractor = lambda: Extractor()
    audio = np.ones(16000, dtype=np.float32)
    ai.compute_embedding(audio)
    ai.compute_embedding(audio.copy())
    assert len(calls) == 1
    ai._extractor_fingerprint = 'new'
    ai.compute_embedding(audio)
    assert len(calls) == 2
    ai.compute_embedding(audio[:8000])
    assert len(calls) == 3  # jamais reutilizar mistura/integral para recorte diferente


def test_replay_snapshot_copies_only_window_and_returns_owned_pcm(monkeypatch):
    from audio_engine import ReplayRingBuffer
    buffer = ReplayRingBuffer(max_seconds=10, sr=100, channels=2)
    chunks = [np.full((100,2), n, np.float32) for n in range(12)]
    for chunk in chunks: buffer.append(chunk)
    original = np.vstack
    copied = []
    def counted(parts):
        copied.append(sum(len(p) for p in parts))
        return original(parts)
    monkeypatch.setattr(np, 'vstack', counted)
    result = buffer.snapshot(1.5)
    assert np.array_equal(result, original(chunks)[-150:])
    assert not copied or max(copied) <= 150
    result[:] = -1
    assert (buffer.snapshot(.5) == 11).all()
    assert buffer.snapshot().shape == (1000,2)


def test_matching_cache_recomputes_after_profile_version_change():
    import threading
    from types import SimpleNamespace
    from voice_profiles import VoiceProfiles, empty_bank
    store = SimpleNamespace(_lock=threading.RLock(), state={'settings':{'voice_match_threshold':.55,'voice_match_margin':.08},
        'voice_bank':empty_bank()})
    profiles=VoiceProfiles(store)
    profiles.bank['profiles']['a']={'profile_version':1}
    calls=[]
    def prototypes(version): calls.append(1); return {'a':[1,0] if len(calls)==1 else [0,1]}
    profiles.prototypes=prototypes
    assert profiles.match([1,0],'test')['speaker_id']=='a'
    assert profiles.match([1,0],'test')['speaker_id']=='a'
    assert len(calls)==1
    profiles.bank['profiles']['a']['profile_version']=2
    assert profiles.match([1,0],'test')['speaker_id']==''
    assert len(calls)==2


def test_cut_reuses_anchored_source_embeddings_without_combining_voices():
    from analysis_cache import project_voice_embeddings
    segments=[{'start_sample':0,'end_sample':48000,'source_id':'s','local_cluster_id':'one',
               'voice_embedding':[1,0],'extractor_version':'v1','embedding_source_id':'s','embedding_source_spans':[[0,48000]]},
              {'start_sample':48000,'end_sample':96000,'source_id':'s','local_cluster_id':'two',
               'voice_embedding':[0,1],'extractor_version':'v1','embedding_source_id':'s','embedding_source_spans':[[48000,96000]]}]
    selected=project_voice_embeddings(segments,'s',[[48000,96000]],'v1')
    assert [s['voice_embedding'] for s in selected]==[[0,1]]
    assert [(s['start_sample'],s['end_sample']) for s in selected]==[(48000,96000)]
    legacy=[{k:v for k,v in segments[1].items() if k != 'embedding_source_spans'}]
    assert project_voice_embeddings(legacy,'s',[[48000,96000]],'v1') is None
    assert project_voice_embeddings(segments,'s',[[5000,20000]],'v1') is None
    assert project_voice_embeddings(segments,'s',[[0,48000]],'v2') is None
    assert project_voice_embeddings(segments,'other',[[0,48000]],'v1') is None
    segments[1]['start_sample']=40000
    assert project_voice_embeddings(segments,'s',[[40000,50000]],'v1') is None


def test_editor_person_analysis_reuses_complete_retained_segment_and_keeps_mix_evidence(tmp_path):
    import soundfile as sf
    from main import SoundboardAPI
    api=SoundboardAPI(str(tmp_path/'data'),start_background_services=False)
    api._hotkeys.enabled=False
    path=tmp_path/'source.wav'
    sf.write(path,np.random.default_rng(2).normal(0,.1,(96000,2)),48000,subtype='FLOAT')
    clip=api._store.add_imported_clip({'file_path':str(path)},rendered=True)
    api._store.begin_edit(clip['id']); api._store.commit_edit(clip['id'])
    source_id=api._store.get_clip(clip['id'])['source_id']
    segments=[{'start_sample':0,'end_sample':48000,'source_id':source_id,'local_cluster_id':'one','voice_embedding':[1,0],'extractor_version':'fixture'},
              {'start_sample':48000,'end_sample':96000,'source_id':source_id,'local_cluster_id':'two','voice_embedding':[0,1],'extractor_version':'fixture'}]
    for segment in segments:
        segment.update(embedding_source_id=source_id,embedding_source_spans=[[segment['start_sample'],segment['end_sample']]])
    api._store.add_or_update_clip({'id':clip['id'],'segments':segments})
    api._store.state['sources'][source_id]['voice_evidence']={'multiple_voices':True}
    api._ai._extractor_fingerprint='fixture'
    api._ai._get_diarizer=lambda:pytest.fail('Reutilização chamou modelo')
    try:
        api.begin_clip_edit(clip['id'])
        assert api.edit_clip_command(clip['id'],'keep',{'interval':[48000,96000]})['ok']
        job=api.request_clip_analysis(clip['id'],'speakers')
        result=api._analysis_queue.wait(job['analysis_id'],3)
        assert result['status']=='ready',result
        assert result['result']['parameters']['reused_complete_segments'] is True
        assert result['result']['fields']['segments'][0]['voice_embedding']==[0,1]
        assert api._store.state['sources'][source_id]['voice_evidence']['multiple_voices'] is True
    finally:
        api._analysis_queue.close(); api._audio.shutdown(); api._store.close()


def test_editor_reinfers_embedding_when_a_junction_fragment_is_removed(tmp_path):
    import soundfile as sf
    from main import SoundboardAPI
    api=SoundboardAPI(str(tmp_path/'data'),start_background_services=False)
    api._hotkeys.enabled=False
    path=tmp_path/'source.wav'
    sf.write(path,np.random.default_rng(4).normal(0,.1,(144000,2)),48000,subtype='FLOAT')
    clip=api._store.add_imported_clip({'file_path':str(path)},rendered=True)
    source_id=clip['source_id']
    api._store.state['sources'][source_id]['voice_evidence']={'multiple_voices':True}
    api._ai._extractor_fingerprint='fixture'
    api._ai._get_diarizer=lambda:object()
    calls=[]
    def diarize(audio,*args,**kwargs):
        calls.append(len(audio))
        return [{'start':0,'end':len(audio)/16000,'speaker_id':'', 'local_cluster_id':'one',
                 'voice_embedding':[1,1] if len(calls)==1 else [1,0], 'extractor_version':'fixture'}]
    api._ai.diarize_and_identify=diarize
    def analyze():
        requested=api.request_clip_analysis(clip['id'],'speakers')
        result=api._analysis_queue.wait(requested['analysis_id'],3)
        assert result['status']=='ready',result
        return result['result']
    try:
        api.begin_clip_edit(clip['id'])
        assert api.edit_clip_command(clip['id'],'remove',{'interval':[48000,96000]})['ok']
        first=analyze()
        assert len(first['fields']['segments'])==2
        assert api.edit_clip_command(clip['id'],'keep',{'interval':[0,48000]})['ok']
        second=analyze()
        assert calls==[32000,16000]
        assert second['fields']['segments'][0]['voice_embedding']==[1,0]
        third=analyze()
        assert calls==[32000,16000]
        assert third['parameters']['reused_complete_segments'] is True
        assert api._store.state['sources'][source_id]['voice_evidence']['multiple_voices'] is True
    finally:
        api._analysis_queue.close();api._sequence_runner.close();api._audio.shutdown();api._store.close()


def test_transcription_configuration_cannot_change_between_cache_key_and_inference(tmp_path):
    import threading
    from types import SimpleNamespace as NS
    from ai_analyzer import AIAnalyzer
    ai=AIAnalyzer(str(tmp_path/'models'))
    ai.configure_transcription({'hotwords':'before'})
    calls=[]
    def transcribe(audio,**parameters):
        calls.append(parameters['hotwords'])
        return iter([NS(start=0,end=.1,text=parameters['hotwords'],words=[])]),None
    ai._get_whisper=lambda:NS(transcribe=transcribe)
    entered,release,attempted,configured=[threading.Event() for _ in range(4)]
    original_get=ai.cache.get
    first_lookup=True
    def blocked_get(key):
        nonlocal first_lookup
        if first_lookup:
            first_lookup=False;entered.set()
            assert release.wait(3)
        return original_get(key)
    ai.cache.get=blocked_get
    outcomes=[]
    def analyze(): outcomes.append(ai.transcribe_temporal(np.ones(16000,dtype='float32'),'a','s',[0,48000]))
    def configure():
        attempted.set();ai.configure_transcription({'hotwords':'after'});configured.set()
    inference=threading.Thread(target=analyze)
    update=threading.Thread(target=configure)
    inference.start()
    assert entered.wait(3)
    update.start()
    assert attempted.wait(3)
    try:
        assert not configured.wait(.1), 'Configuração mudou enquanto a inferência detinha seu epoch'
    finally:
        release.set();inference.join(3);update.join(3)
    assert configured.is_set() and len(outcomes)==1
    assert outcomes[0]['text']==outcomes[0]['parameters']['hotwords']=='before'
    ai.configure_transcription({'hotwords':'before'})
    repeated=ai.transcribe_temporal(np.ones(16000,dtype='float32'),'b','s',[0,48000])
    assert repeated['text']==repeated['parameters']['hotwords']=='before'
    assert calls==['before']
