import copy
import json
import numpy as np
import pytest
import soundfile as sf


def manifest(tmp_path):
    entries = []
    for n in range(3):
        path = tmp_path / f'{n}.wav'
        sf.write(path, np.random.default_rng(n).normal(0,.1,48000), 48000, subtype='FLOAT')
        entries.append({'id': str(n), 'capture_id': str(n), 'session_id': str(n), 'source_path': path.name,
                        'interval': [0,48000], 'labels': {'text': 'ola mundo', 'speaker_id': 'ana'},
                        'predictions': {'baseline': {'text': 'ola', 'speaker_id': ''}}})
    return {'synthetic': True, 'configurations': [{'id':'baseline', 'parameters':{}}],
            'enrollment': [entries[0]], 'calibration': [entries[1]], 'test': [entries[2]]}


def test_pipeline_reports_raw_wer_coverage_and_unavailable_timing(tmp_path):
    from tools.evaluate_audio_pipeline import evaluate
    data = manifest(tmp_path)
    data['test'][0]['predictions']['baseline']['corrected_text'] = 'ola mundo'
    result = evaluate(data, tmp_path)
    metrics = result['configurations']['baseline']['test']
    assert metrics['wer']['value'] == .5
    assert metrics['speaker']['coverage'] == 0
    assert metrics['speaker']['precision'] is None
    assert metrics['speaker']['abstentions'] == 1
    assert metrics['timing']['mean_absolute_error_ms'] is None
    assert result['decision_status'] == 'pending_real_labeled_corpus'


@pytest.mark.parametrize('leak', ['capture', 'session', 'file', 'content', 'cropped_content'])
def test_rejects_repartitioned_captures_sessions_and_content(tmp_path, leak):
    from tools.evaluate_audio_pipeline import evaluate
    data = manifest(tmp_path)
    a, b = data['enrollment'][0], data['test'][0]
    if leak in ('capture', 'session'): b[leak+'_id'] = a[leak+'_id']
    if leak == 'file': b['source_path'] = a['source_path']
    if leak in ('content', 'cropped_content'):
        pcm, sr = sf.read(tmp_path/a['source_path'])
        if leak == 'cropped_content': pcm = pcm[5000:35000]; b['interval'] = [0,30000]
        sf.write(tmp_path/b['source_path'], pcm, sr, subtype='FLOAT')
    with pytest.raises(ValueError, match='leakage'):
        evaluate(data, tmp_path)


def test_rejects_invalid_interval_and_marks_prediction_measurements_as_supplied(tmp_path):
    from tools.evaluate_audio_pipeline import evaluate
    data = manifest(tmp_path)
    data['test'][0]['interval'][1] = 50000
    with pytest.raises(ValueError, match='interval'):
        evaluate(data, tmp_path)
    data['test'][0]['interval'][1] = 48000
    data['test'][0]['predictions']['baseline']['latency_ms'] = 12
    metrics = evaluate(data, tmp_path)['configurations']['baseline']['test']
    assert metrics['latency']['provenance'] == 'manifest_predictions'
    assert metrics['latency']['mean_ms'] == 12


def test_temporal_alignment_counts_only_matched_tokens_and_reports_coverage(tmp_path):
    from tools.evaluate_audio_pipeline import evaluate
    data = manifest(tmp_path)
    entry = data['test'][0]
    entry['labels']['words'] = [{'text':'ola','start_sample':0,'end_sample':4800},
                               {'text':'mundo','start_sample':4800,'end_sample':9600}]
    entry['predictions']['baseline']['words'] = [{'text':'ola','start_sample':480,'end_sample':5280}]
    timing = evaluate(data,tmp_path)['configurations']['baseline']['test']['timing']
    assert timing['mean_absolute_error_ms'] == 10
    assert timing['matched_words'] == 1
    assert timing['coverage'] == .5


def test_local_runner_executes_comparable_windows_vad_hotwords_without_models(tmp_path, monkeypatch):
    import ai_analyzer
    from types import SimpleNamespace as NS
    from tools.evaluate_audio_pipeline import evaluate
    data=manifest(tmp_path)
    data['configurations']=[{'id':'base','parameters':{}},
        {'id':'windows','parameters':{'speech_windows':True,'context_seconds':.1,'hotwords':'Lucas','vad_filter':True}},
        {'id':'quality','parameters':{'model':'other-local-model','beam_size':5}}]
    for partition in ('enrollment','calibration','test'):
        data[partition][0]['speech_windows']=[[10000,20000]]
    calls=[]
    class Model:
        def transcribe(self,audio,**params):
            calls.append((len(audio),params))
            return iter([NS(start=0,end=.1,text='ola',words=[])]),None
    monkeypatch.setattr(ai_analyzer.AIAnalyzer,'_get_whisper',lambda self:Model())
    monkeypatch.setattr(ai_analyzer.AIAnalyzer,'compute_embedding',lambda *args:None)
    result=evaluate(data,tmp_path,'local',tmp_path/'models')
    assert len(calls)==9
    assert calls[0][0]==16000
    assert calls[3][0] < 16000
    assert calls[3][1]['vad_filter'] is True
    assert calls[3][1]['vad_parameters']['speech_pad_ms']==400
    assert calls[3][1]['hotwords']=='Lucas'
    assert calls[6][1]['hotwords']==''
    assert result['configurations']['windows']['test']['latency']['provenance']=='local_inference_wall_time'
    assert result['voice_comparison']['status']=='unavailable'


def test_voice_comparison_uses_enrollment_calibration_then_test_and_keeps_unknowns(tmp_path):
    from tools.evaluate_audio_pipeline import evaluate
    data=manifest(tmp_path)
    for partition in ('enrollment','calibration','test'):
        data[partition][0].update(embedding=[1.,0.],extractor_version='fixture/1')
    data['test'][0]['labels']['speaker_id']=''
    result=evaluate(data,tmp_path)['voice_comparison']
    assert result['production_changed'] is False
    for strategy in result['strategies'].values():
        assert strategy['test']['precision']==0
        assert strategy['test']['coverage']==1
        assert strategy['test']['confusion']['<unknown>']['ana']==1


@pytest.mark.parametrize('missing_field', [False, True])
def test_voice_comparison_counts_missing_embeddings_as_abstentions_in_both_partitions(tmp_path, missing_field):
    from tools.evaluate_audio_pipeline import evaluate
    data=manifest(tmp_path)
    for partition in ('enrollment','calibration','test'):
        data[partition][0].update(embedding=[1.,0.],extractor_version='fixture/1')
    for seed,partition in enumerate(('calibration','test'),77):
        absent=copy.deepcopy(data[partition][0])
        absent.update(id=f'missing-{partition}',capture_id=f'missing-{partition}',
                      session_id=f'missing-{partition}',source_path=f'missing-{partition}.wav',embedding=None)
        if missing_field:
            absent.pop('embedding')
        sf.write(tmp_path/absent['source_path'],np.random.default_rng(seed).normal(0,.1,48000),48000,subtype='FLOAT')
        data[partition].append(absent)
    result=evaluate(data,tmp_path)['voice_comparison']
    for strategy in result['strategies'].values():
        for metrics in [strategy['test'],*[trial['metrics'] for trial in strategy['calibration_trials']]]:
            assert metrics['labeled_items']==2
            assert metrics['coverage']==.5
            assert metrics['accuracy_all']==.5
            assert metrics['precision']==1
            assert metrics['abstentions']==1
    for partition in ('calibration','test'):
        availability=result['embedding_availability'][partition]
        assert availability['available']==1 and availability['missing']==1
        assert availability['missing_ids']==[f'missing-{partition}']


@pytest.mark.parametrize('windows,context,expected', [
    ([[30000,40000],[0,10000]],0,[[0,10000],[30000,40000]]),
    ([[15000,25000],[0,10000]],.1,[[0,29800]]),
    ([[47000,48000],[0,1000]],.1,[[0,5800],[42200,48000]]),
])
def test_local_speech_windows_preserve_permutations_merge_context_and_clip_edges(tmp_path, windows, context, expected):
    from tools.evaluate_audio_pipeline import _local_prediction
    from ai_analyzer import AIAnalyzer
    from types import SimpleNamespace as NS
    analyzer=AIAnalyzer(str(tmp_path/'models'))
    calls=[]
    def transcribe(audio,**parameters):
        calls.append(len(audio))
        return iter([NS(start=0,end=.01,text='ola',words=[NS(start=0,end=.01,word='ola')])]),None
    analyzer._get_whisper=lambda:NS(transcribe=transcribe)
    item={'id':'sample','capture_id':'capture','interval':[0,48000],'speech_windows':windows,
          '_audio':np.random.default_rng(2).normal(0,.1,48000).astype('float32')}
    result=_local_prediction(item,{'parameters':{'speech_windows':True,'context_seconds':context}},analyzer)
    assert result['status']=='ready'
    assert [word['start_sample'] for word in result['words']]==[lo for lo,hi in expected]
    assert calls==[(hi-lo+2)//3 for lo,hi in expected]
