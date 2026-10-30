from pathlib import Path
from types import SimpleNamespace
import pytest
from main import SoundboardAPI
from store import SoundboardStore

@pytest.fixture
def api(tmp_path):
    api = SoundboardAPI.__new__(SoundboardAPI)
    api._store = SoundboardStore(data_dir=str(tmp_path/'library'))
    api._window = None
    api._capabilities = lambda: {'audio_convert':False}
    api._emit_progress = lambda *a: None
    api._notify_ui = lambda *a: None
    api._calculate_clip_loudness = lambda *a: {}
    api._ai = SimpleNamespace(analyze_file=lambda *a, **k: {'title':'test', 'duration':1})
    return api

def test_batch_isolates_failures_and_removes_only_orphan(api, tmp_path):
    sources = [tmp_path/name for name in ['one.wav','broken.wav','two.wav']]
    for source in sources: source.write_bytes(b'audio')
    prior = api._store.clips_dir / 'prior.wav'
    prior.write_bytes(b'prior')
    def analyze(path, **kwargs):
        if path.endswith('broken.wav'): raise ValueError('invalid audio')
        return {'title':Path(path).stem, 'duration':1, 'warnings':['Transcrição indisponível']}
    api._ai.analyze_file = analyze
    result = api.import_audio_files([str(p) for p in sources]+[str(tmp_path/'missing.wav')])
    assert len(result['imported']) == 2
    assert [r['status'] for r in result['results']] == ['success','failed','success','failed']
    assert result['results'][0]['warnings']
    assert len(api._store.state['clips']) == 2
    assert len(list(api._store.clips_dir.iterdir())) == 3
    assert prior.read_bytes() == b'prior'
    assert all(p.exists() for p in sources)

def test_structured_decode_error_is_failed_import(api,tmp_path):
    source=tmp_path/'bad.wav'; source.write_bytes(b'bad')
    api._ai.analyze_file=lambda *a, **k: {'analysis_status':'decode_error','error':'decode failed'}
    result=api.import_audio_files([str(source)])
    assert result['ok'] is False
    assert result['results'][0]['status']=='failed'
    assert not api._store.state['clips']
    assert not list(api._store.clips_dir.iterdir())

def test_unsupported_conversion_reports_each_item(api,tmp_path):
    source=tmp_path/'convert.m4a'; source.write_bytes(b'fake')
    result=api.import_audio_files([str(source)])
    assert result['results'][0]['status']=='failed'
    assert 'FFmpeg' in result['results'][0]['error']

def test_folder_mode_is_explicit_and_import_rejections_are_handled():
    import os
    import subprocess
    result = subprocess.run([os.environ.get('NODE_BINARY', 'node'), '-e', r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const src=fs.readFileSync('ui/app.js','utf8'),html=fs.readFileSync('ui/index.html','utf8');
assert.match(html,/id="folderImportMode"/);
const elements={btnImportFolder:{},folderImportMode:{value:'audio'},importResults:{classList:{remove:()=>{}},open:false},importResultsText:{textContent:''}};
const calls=[],toasts=[];
let reject=false;
const context={document:{getElementById:id=>elements[id]},api:()=>({import_folder_or_separados:async(...args)=>{calls.push(args);if(reject)throw Error('bridge failure');return {ok:true,imported:[],results:[{source:'bad.wav',status:'failed',error:'decode failed',warnings:[]}]};}}),state:{activeCategory:'Vinhetas',activeSpeaker:'__none__',clips:[],speakers:{}},showToast:msg=>toasts.push(msg),renderGrid:()=>{},renderSidebar:()=>{}};
vm.createContext(context);
const helper=src.match(/async function runImportOperation\([\s\S]*?\n}\n/);if(helper)vm.runInContext(helper[0],context);
const start=src.indexOf('document.getElementById("btnImportFolder").onclick =');
vm.runInContext(src.slice(start,src.indexOf('\n  };',start)+5),context);
(async()=>{await elements.btnImportFolder.onclick();assert.deepEqual(calls[0],[null,false,null]);assert.match(elements.importResultsText.textContent,/bad.wav.*decode failed/);elements.folderImportMode.value='vinheta';await elements.btnImportFolder.onclick();assert.deepEqual(calls[1],[null,true,'Vinhetas']);reject=true;await elements.btnImportFolder.onclick();assert.match(toasts.at(-1),/bridge failure/);})().catch(e=>{console.error(e);process.exitCode=1});
'''], capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stderr

def test_import_save_failure_rolls_back_only_failed_insertion(api, tmp_path, monkeypatch):
    import json
    import threading
    sources = [tmp_path/'failed.wav', tmp_path/'valid.wav']
    for source in sources:
        source.write_bytes(b'audio')
    prior_file = api._store.clips_dir/'prior.wav'
    prior_file.write_bytes(b'prior')
    prior = api._store.add_or_update_clip({'title':'prior', 'file_path':str(prior_file)})
    concurrent_file = api._store.clips_dir/'concurrent.wav'
    concurrent_file.write_bytes(b'concurrent')
    save_started = threading.Event()
    writer_started = threading.Event()
    original_save = api._store.save
    failed_once = False
    def fail_first_save():
        nonlocal failed_once
        if not failed_once:
            failed_once = True
            save_started.set()
            assert writer_started.wait(2)
            raise OSError('transient persistence error')
        return original_save()
    monkeypatch.setattr(api._store, 'save', fail_first_save)
    def concurrent_writer():
        assert save_started.wait(2)
        writer_started.set()
        api._store.add_or_update_clip({'title':'concurrent', 'file_path':str(concurrent_file)})
    writer = threading.Thread(target=concurrent_writer)
    writer.start()
    result = api.import_audio_files([str(p) for p in sources])
    writer.join(timeout=2)
    assert not writer.is_alive()
    assert [item['status'] for item in result['results']] == ['failed','success']
    assert len(result['imported']) == 1
    state = api._store.snapshot()
    assert len(state['clips']) == 3
    assert api._store.get_clip(prior['id'])['title'] == 'prior'
    assert {clip['title'] for clip in state['clips']} == {'prior', 'concurrent', 'test'}
    paths = {Path(clip['file_path']) for clip in state['clips']}
    assert all(path.exists() for path in paths)
    assert set(api._store.clips_dir.iterdir()) == paths
    persisted = json.loads(api._store.db_path.read_text(encoding='utf-8'))
    assert {clip['id'] for clip in persisted['clips']} == {clip['id'] for clip in state['clips']}
    assert all(source.exists() for source in sources)
