from types import SimpleNamespace
import numpy as np
import pytest
import ai_analyzer
from ai_analyzer import AIAnalyzer

@pytest.fixture
def analyzer(tmp_path, monkeypatch):
    obj = AIAnalyzer(models_dir=str(tmp_path))
    monkeypatch.setattr(obj, '_get_whisper', lambda: None)
    return obj

@pytest.mark.parametrize('mode,status', [('missing','model_unavailable'), ('error','inference_error'), ('empty','no_speech'), ('speech','ok')])
def test_transcription_status_preserves_string_api(analyzer, monkeypatch, mode, status):
    def infer(*args, **kwargs):
        if mode == 'error':
            raise RuntimeError('inference failed')
        return ([SimpleNamespace(text='Olá')] if mode == 'speech' else [], None)
    if mode != 'missing':
        monkeypatch.setattr(analyzer, '_get_whisper', lambda: SimpleNamespace(transcribe=infer))
    assert hasattr(analyzer, 'transcribe_result'), 'Structured status is missing'
    result = analyzer.transcribe_result(np.ones(16000, dtype=np.float32))
    assert result['status'] == status
    assert bool(result['error']) == (mode in ('missing', 'error'))
    assert analyzer.transcribe(np.ones(16000)) == ('Olá' if mode == 'speech' else '')

@pytest.mark.parametrize('duration', [.5, 3])
def test_model_failure_does_not_classify_vinheta(analyzer, monkeypatch, duration):
    monkeypatch.setattr(ai_analyzer, 'load_audio_mono_16k', lambda p: (np.zeros(int(16000*duration)), duration))
    result = analyzer.analyze_file('fake.wav', skip_speaker_id=True)
    assert result['category'] == 'Call'
    assert result['transcription_status'] == 'model_unavailable'
    assert result['warnings']

def test_decode_failure_is_distinct(analyzer, monkeypatch):
    def fail(p):
        raise ValueError('bad audio')
    monkeypatch.setattr(ai_analyzer, 'load_audio_mono_16k', fail)
    result = analyzer.analyze_file('broken.wav')
    assert result['analysis_status'] == 'decode_error'
    assert result['transcript'] == ''
    assert result['error']

def test_whisper_loading_never_downloads_during_analysis(analyzer, monkeypatch):
    import sys
    calls = []
    def create(*args, **kwargs):
        calls.append(kwargs)
        raise RuntimeError('not cached')
    monkeypatch.undo()
    monkeypatch.setitem(sys.modules, 'faster_whisper', SimpleNamespace(WhisperModel=create))
    assert analyzer._get_whisper() is None
    assert calls and all(call.get('local_files_only') is True for call in calls)

def test_partial_segment_failure_remains_visible(analyzer, monkeypatch):
    monkeypatch.setattr(ai_analyzer, 'load_audio_mono_16k', lambda p: (np.zeros(48000), 3.0))
    monkeypatch.setattr(analyzer, 'diarize_and_identify', lambda *a, **k: [
        {'speaker_id':'one', 'transcript':'Olá', 'transcription_status':'ok', 'transcription_error':None},
        {'speaker_id':'two', 'transcript':'', 'transcription_status':'inference_error', 'transcription_error':'Falha de inferência'},
    ])
    result = analyzer.analyze_file('fake.wav')
    assert result['analysis_status'] == 'partial'
    assert result['warnings'] == ['Falha de inferência']
    assert 'Olá' in result['transcript']

def test_api_analysis_notifies_partial_and_decode_failures():
    from main import SoundboardAPI
    api = SoundboardAPI.__new__(SoundboardAPI)
    events = []
    api._notify_ui = lambda *args: events.append(args)
    api._ai = SimpleNamespace(analyze_file=lambda *a, **k: {'analysis_status':'partial','warnings':['Modelo indisponível']})
    assert api._analyze_audio('fake.wav')['analysis_status'] == 'partial'
    assert events[-1] == ('toast', {'type':'warning','message':'Modelo indisponível'})
    api._ai.analyze_file = lambda *a, **k: {'analysis_status':'decode_error','error':'decode failed'}
    with pytest.raises(ValueError, match='decode failed'):
        api._analyze_audio('fake.wav')
    assert events[-1][1]['message'] == 'decode failed'

def test_reanalysis_exposes_partial_status_and_preserves_transcript(tmp_path):
    import soundfile as sf
    from main import SoundboardAPI
    api=SoundboardAPI(data_dir=str(tmp_path/'library'), start_background_services=False)
    source=tmp_path/'fake.wav'; sf.write(source, np.zeros((48000, 2), np.float32), 48000)
    clip=api._store.add_imported_clip({'file_path':str(source),'transcript':'Texto revisado'}, rendered=True)
    api._capability_error=lambda *a: None
    api._ai.transcribe_temporal=lambda *a, **k: {'status':'failed', 'error': {'code':'model_unavailable','message':'Modelo indisponível'}}
    try:
        queued=api.reanalyze_clip_ai(clip['id'])
        api._analysis_queue.wait(queued['analysis_id'], 3)
        result=api.get_analysis_job(queued['analysis_id'])
        assert result['analysis_status']=='partial'
        assert result['warnings']==['Modelo indisponível']
        assert api._store.render_context(clip['id'], draft=True)['transcript']=='Texto revisado'
    finally:
        api._analysis_queue.close()
        api._store.close()
        api._audio.shutdown()

def test_split_reports_partial_transcription_from_direct_diarization(tmp_path, monkeypatch):
    import soundfile as sf
    from main import SoundboardAPI
    from store import SoundboardStore
    from audio_engine import AudioEngine
    api=SoundboardAPI.__new__(SoundboardAPI)
    api._store=SoundboardStore(data_dir=str(tmp_path/'library'))
    api._audio=AudioEngine(start_loopback=False)
    source=tmp_path/'source.wav'
    sf.write(source, np.zeros((48000,2),dtype=np.float32),48000)
    clip=api._store.add_or_update_clip({'file_path':str(source)})
    events=[]
    api._capability_error=lambda *a: None
    api._emit_progress=lambda *a: None
    api._notify_ui=lambda *args: events.append(args)
    segments=[{'start':0,'end':.5,'speaker_id':'one','transcript':'Olá','transcription_status':'ok','transcription_error':None},
              {'start':.5,'end':1,'speaker_id':'two','transcript':'','transcription_status':'inference_error','transcription_error':'Inferência indisponível'}]
    api._ai=SimpleNamespace(diarize_and_identify=lambda *a,**k:segments)
    result=api.split_clip_by_speakers(clip['id'])
    assert result['ok'] and result['created_count']==2
    assert result['analysis_status']=='partial'
    assert result['warnings']==['Inferência indisponível']
    assert ('toast',{'type':'warning','message':'Inferência indisponível'}) in events
    assert all(__import__('pathlib').Path(c['file_path']).exists() for c in result['created_clips'])

def test_ui_split_reports_partial_analysis():
    import os
    import subprocess
    result=subprocess.run([os.environ.get('NODE_BINARY','node'),'-e',r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const src=fs.readFileSync('ui/app.js','utf8');
const start=src.indexOf('document.getElementById("btnSplitSpeakers").onclick =');
const elements={btnSplitSpeakers:{},editClipId:{value:'clip'}};
const toasts=[];
const context={document:{getElementById:id=>elements[id]},api:()=>({split_clip_by_speakers:async()=>({ok:true,created_count:2,analysis_status:'partial',warnings:['Falha vocal']})}),state:{clips:[],speakers:{}},showToast:msg=>toasts.push(msg),renderGrid:()=>{},renderSidebar:()=>{},closeEditModal:()=>{}};
vm.createContext(context);vm.runInContext(src.slice(start,src.indexOf('\n  };',start)+5),context);
(async()=>{await elements.btnSplitSpeakers.onclick();assert.match(toasts.at(-1),/parcial.*Falha vocal/);})().catch(e=>{console.error(e);process.exitCode=1});
'''],capture_output=True,text=True,encoding='utf-8')
    assert result.returncode==0,result.stderr


def test_legacy_file_analysis_reports_diarization_failure_without_losing_transcription(tmp_path, monkeypatch):
    ai = AIAnalyzer(str(tmp_path))
    def fail(_audio):
        raise RuntimeError('diarização falhou')
    monkeypatch.setattr(ai, '_get_diarizer', lambda: SimpleNamespace(process=fail))
    monkeypatch.setattr(ai_analyzer, 'load_audio_mono_16k', lambda _: (np.zeros(32000), 2.0))
    monkeypatch.setattr(ai, 'transcribe_result', lambda _: {'transcript':'fala mantida','status':'ok','error':None})
    result = ai.analyze_file('synthetic.wav')
    assert result['analysis_status'] == 'partial'
    assert result['transcript'] == 'fala mantida'
    assert result['diarization_error']['code'] == 'inference_error'
    assert any('diarização falhou' in warning for warning in result['warnings'])


def test_legacy_import_reports_insufficient_detected_speech_without_losing_text(tmp_path,monkeypatch):
    ai=AIAnalyzer(str(tmp_path))
    monkeypatch.setattr(ai,'_get_diarizer',lambda:SimpleNamespace(process=lambda _:SimpleNamespace(sort_by_start_time=lambda:[SimpleNamespace(start=.1,end=.3,speaker=0)])))
    monkeypatch.setattr(ai_analyzer,'load_audio_mono_16k',lambda _:(np.zeros(32000),2.0))
    monkeypatch.setattr(ai,'transcribe_result',lambda _:{'transcript':'texto preservado','status':'ok','error':None})
    result=ai.analyze_file('synthetic.wav')
    assert result['analysis_status']=='partial'
    assert result['diarization_error']['code']=='insufficient_speech'
    assert result['transcript']=='texto preservado' and result['warnings']
