from types import SimpleNamespace
import numpy as np
import pytest
import ai_analyzer
from ai_analyzer import AIAnalyzer
from main import SoundboardAPI
from store import SoundboardStore

@pytest.fixture
def api(tmp_path):
    api = SoundboardAPI.__new__(SoundboardAPI)
    api._store = SoundboardStore(data_dir=str(tmp_path/'data'))
    api._ai = SimpleNamespace(enroll_speaker_reference=lambda *a, **k: False)
    api._capability_error = lambda *a: None
    return api

def test_enrollment_uses_trimmed_audio(tmp_path, monkeypatch):
    analyzer = AIAnalyzer(models_dir=str(tmp_path))
    samples = np.arange(48000, dtype=np.float32)
    monkeypatch.setattr(ai_analyzer, 'load_audio_mono_16k', lambda p: (samples, 3.0))
    observed = []
    monkeypatch.setattr(analyzer, 'compute_embedding', lambda data: observed.append(data.copy()) or np.array([1.,0.]))
    np.testing.assert_array_equal(analyzer.extract_speaker_reference('fake.wav', 'friend', trim_start=1, trim_end=2), [1, 0])
    np.testing.assert_array_equal(observed[0], samples[16000:32000])

def test_enrollment_requires_explicit_speaker(api):
    clip = api._store.add_or_update_clip({'file_path':'fake.wav', 'speaker_id':'old'})
    result = api.enroll_speaker_from_clip(clip['id'], '')
    assert result['ok'] is False
    assert result.get('error')
    assert api._store.get_clip(clip['id'])['speaker_id'] == 'old'

@pytest.mark.parametrize('raises', [False, True])
def test_assignment_survives_reference_failure(api, raises):
    observed = []
    def enroll(*args, **kwargs):
        observed.append(kwargs)
        if raises:
            raise RuntimeError('embedding failed')
        return False
    api._ai.enroll_speaker_reference = enroll
    clip = api._store.add_or_update_clip({'file_path':'fake.wav', 'trim_start':.3,'trim_end':.8})
    result = api.enroll_speaker_from_clip(clip['id'], 'friend')
    assert result['assignment_saved'] is True
    assert result['reference_learned'] is False
    assert result['error']
    # Fonte inexistente bloqueia extração, mas nunca desfaz a atribuição humana.
    assert observed == []
    assert api._store.get_clip(clip['id'])['speaker_id'] == 'friend'

def test_ui_enrollment_saves_selection_before_learning():
    import os
    import subprocess
    result = subprocess.run([os.environ.get('NODE_BINARY', 'node'), '-e', r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const src=fs.readFileSync('ui/app.js','utf8');
const start=src.indexOf('document.getElementById("btnEnrollVoice").onclick =');
const handler=src.slice(start,src.indexOf('\n  };',start)+5);
const elements={btnEnrollVoice:{},editClipId:{value:'clip'},editSpeaker:{value:'friend'}};
const calls=[];
const context={document:{getElementById:id=>elements[id]},api:()=>({enroll_speaker_from_clip:async(...args)=>{calls.push(['enroll',...args]);return {ok:false,assignment_saved:true,reference_learned:false,error:'partial'};}}),saveCurrentClipEdits:async()=>{calls.push(['save']);return {id:'clip'};},state:{clips:[],speakers:{}},showToast:()=>{},renderGrid:()=>{},renderSidebar:()=>{}};
vm.createContext(context);vm.runInContext(handler,context);
(async()=>{await elements.btnEnrollVoice.onclick();assert.deepEqual(calls,[['save'],['enroll','clip','friend','']]);calls.length=0;elements.editSpeaker.value='';await elements.btnEnrollVoice.onclick();assert.equal(calls.length,0);})().catch(e=>{console.error(e);process.exitCode=1});
'''], capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stderr
