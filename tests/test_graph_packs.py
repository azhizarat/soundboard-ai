import copy
import json
import zipfile
from types import SimpleNamespace

import numpy as np
import pytest
import soundfile as sf

from main import SoundboardAPI
from store import SoundboardStore
from clip_renderer import render_revision


@pytest.fixture
def libraries(tmp_path):
    apis = []
    for name in ('src','dst'):
        api = SoundboardAPI.__new__(SoundboardAPI)
        api._store = SoundboardStore(str(tmp_path/name))
        api._window = None
        api._hotkeys = SimpleNamespace(refresh=lambda *a: None)
        api._notify_ui = lambda *a: None
        api._emit_progress = lambda *a: None
        apis.append(api)
    yield apis
    for api in apis:
        api._store.close()


def setup_graph(api):
    s = api._store
    s.state['settings']['normalize_on_import'] = False
    path = s.clips_dir/'a.wav'
    sf.write(path, np.repeat(np.array([.1,.6,.2], dtype=np.float32), 4800)[:,None], 48000, subtype='FLOAT')
    clip = s.add_or_update_clip({'title':'Original','file_path':str(path),'duration':.3,'volume':1.4,'transcript':'SEGREDO'})
    s.begin_edit(clip['id'])
    s.update_edit(clip['id'], {'spans':[[0,4800],[9600,14400]]})
    old = s.commit_edit(clip['id'])
    old_id = old['revision_id']
    s.begin_edit(clip['id']); s.update_edit(clip['id'], {'spans':[[9600,14400]]}); current = s.commit_edit(clip['id'])
    sequence = {'id':'seq','name':'Fluxo','schema_version':1,'revision':0,'hotkey':'', 'blocks':[
        {'id':'p1','type':'play','clip_id':clip['id'],'revision_id':old_id,'interval':[0,9600],'volume':.8},
        {'id':'manual','type':'manual'},
        {'id':'parallel','type':'parallel','branches':[[{'id':'p2','type':'play','clip_id':clip['id'],'revision_id':old_id,'interval':[4800,9600],'volume':1}], [{'id':'wait','type':'wait','ms':10}]]}]}
    s.save_sequence(sequence)
    collection = {'id':'show','name':'Show','schema_version':1,'revision':0,'columns':4,'pads':[
        {'id':'pad','position':5,'label':'Surpresa','target':{'type':'shuffle','items':[{'type':'clip','id':clip['id']},{'type':'sequence','id':'seq'}]},
         'hotkey':'f9','group':'speech','retrigger':'restart','hold':False,'loop':False,'fade_ms':50,'cooldown_ms':250}]}
    s.save_collection(collection)
    return current, old_id


@pytest.mark.parametrize('mode', ['compact','editable'])
def test_graph_roundtrip_pinned_revision_parallel_manual_collection(libraries, tmp_path, mode):
    src,dst = libraries
    clip, old = setup_graph(src)
    out = tmp_path/(mode+'.zip')
    assert 'mode' in __import__('inspect').signature(src.export_pack).parameters, 'Packs de grafo ausentes'
    result = src.export_pack(None,str(out),mode=mode)
    assert result['ok'], result
    with zipfile.ZipFile(out) as archive:
        manifest = json.loads(archive.read('manifest.json'))
        assert manifest['version'] == 2
        assert manifest['voice_policy'] == 'excluded'
        if mode == 'compact':
            assert b'SEGREDO' not in archive.read('manifest.json')
            for name in manifest['files']:
                import io
                pcm, _ = sf.read(io.BytesIO(archive.read(name)), dtype='float32')
                assert np.max(np.abs(pcm)) < .3
    settings = copy.deepcopy(dst._store.state['settings'])
    result = dst.import_pack(str(out)); assert result['ok'], result
    assert dst._store.state['settings'] == settings
    seq = next(iter(dst._store.state['sequences'].values()))
    expected = src._store.resolve_sequence_occurrence(src._store.get_sequence('seq')['blocks'][0])
    actual = dst._store.resolve_sequence_occurrence(seq['blocks'][0])
    a = render_revision(expected['_source'], expected['_revision'])[0:9600]
    b = render_revision(actual['_source'],actual['_revision'])
    lo,hi = actual['_preview_ranges'][0]
    np.testing.assert_allclose(a,b[lo:hi],atol=1e-7)
    assert seq['blocks'][0]['volume'] == .8
    pad = next(iter(dst._store.state['collections'].values()))['pads'][0]
    assert pad['position'] == 5 and pad['cooldown_ms'] == 250
    again = dst.import_pack(str(out)); assert again['ok'] and not again['imported']
    assert len(dst._store.state['sequences']) == 1


def test_invalid_hash_or_graph_and_save_failure_never_partially_publish(libraries,tmp_path,monkeypatch):
    src,dst = libraries
    setup_graph(src)
    assert 'mode' in __import__('inspect').signature(src.export_pack).parameters, 'Packs de grafo ausentes'
    path = tmp_path/'valid.zip'; assert src.export_pack(None,str(path),mode='editable')['ok']
    before = dst._store.snapshot()
    with zipfile.ZipFile(path) as archive:
        files = {name:archive.read(name) for name in archive.namelist()}
    manifest = json.loads(files['manifest.json'])
    manifest['graph']['sequences']['seq']['blocks'][0]['clip_id'] = 'missing'
    files['manifest.json'] = json.dumps(manifest).encode()
    broken = tmp_path/'broken.zip'
    with zipfile.ZipFile(broken,'w') as archive:
        for name,data in files.items(): archive.writestr(name,data)
    assert not dst.import_pack(str(broken))['ok']
    assert dst._store.snapshot() == before
    with monkeypatch.context() as patch:
        patch.setattr(dst._store,'save',lambda: (_ for _ in ()).throw(OSError('disk full')))
        assert not dst.import_pack(str(path))['ok']
    assert dst._store.snapshot() == before
    assert not list(dst._store.clips_dir.rglob('*.wav'))


def test_explicit_clip_filter_does_not_export_other_roots(libraries,tmp_path):
    src,_ = libraries
    clip,_ = setup_graph(src)
    assert 'mode' in __import__('inspect').signature(src.export_pack).parameters, 'Packs de grafo ausentes'
    path = tmp_path/'filtered.zip'
    assert src.export_pack([clip['id']],str(path),mode='compact')['ok']
    with zipfile.ZipFile(path) as archive:
        graph = json.loads(archive.read('manifest.json'))['graph']
        assert not graph['collections'] and not graph['sequences']
        assert len(graph['clips']) == 1


def test_sequence_only_compact_omits_current_clip_and_outside_interval(libraries,tmp_path):
    src,dst=libraries
    clip,old=setup_graph(src)
    seq=src._store.get_sequence('seq')
    seq['blocks']=[{'id':'only','type':'play','clip_id':clip['id'],'revision_id':old,'interval':[0,2400],'volume':1}]
    src._store.save_sequence(seq)
    path=tmp_path/'only.zip'
    result=src.export_pack([],str(path),mode='compact',sequence_ids=['seq'])
    assert result['ok'], result
    with zipfile.ZipFile(path) as archive:
        manifest=json.loads(archive.read('manifest.json'))
        assert len(manifest['graph']['clips'])==1
        assert all(s['total_samples']==2400 for s in manifest['graph']['sources'].values())
    assert dst.import_pack(str(path))['ok']


def test_compact_partial_segment_or_unaligned_text_never_leaks(libraries,tmp_path):
    src,_=libraries
    clip,_=setup_graph(src)
    current=src._store.get_clip(clip['id'])
    current['transcript_analysis']={'alignment':'segments','words':[],'corrections':[],
        'segments':[{'id':'s','start_sample':0,'end_sample':14400,'text':'PUBLICO SEGREDO PUBLICO'}]}
    path=tmp_path/'segment.zip'
    assert src.export_pack([clip['id']],str(path),mode='compact')['ok']
    with zipfile.ZipFile(path) as archive:
        assert b'SEGREDO' not in archive.read('manifest.json')


@pytest.mark.parametrize('corruption',['hash','cycle','unknown_recipe','path','nan'])
def test_adversarial_pack_rejected_before_any_persistence(libraries,tmp_path,corruption):
    src,dst=libraries; setup_graph(src)
    path=tmp_path/'original.zip'; assert src.export_pack(None,str(path),mode='editable')['ok']
    with zipfile.ZipFile(path) as archive: files={name:archive.read(name) for name in archive.namelist()}
    manifest=json.loads(files['manifest.json']);graph=manifest['graph']
    source=next(iter(graph['sources'].values()));revision=next(iter(graph['revisions'].values()))
    if corruption=='hash': files[source['file']]=b'corrupt'
    elif corruption=='cycle': revision['variant_origin']={'clip_id':graph['clips'][0]['id'],'source_id':source['id'],'revision_id':revision['id']}
    elif corruption=='unknown_recipe': revision['render_version']='future-recipe-v900'
    elif corruption=='path': files['audio/../outside.wav']=b'bad'
    else:
        import io,hashlib
        raw=io.BytesIO();sf.write(raw,np.full((source['total_samples'],2),np.nan,dtype=np.float32),48000,format='WAV',subtype='FLOAT')
        data=raw.getvalue();files[source['file']]=data;digest=hashlib.sha256(data).hexdigest()
        source['sha256']=digest;manifest['files'][source['file']]={'sha256':digest,'size':len(data)}
        for node in graph['revisions'].values():
            if node['source_id']==source['id']: node['source_hash']=digest
    files['manifest.json']=json.dumps(manifest).encode()
    malicious=tmp_path/'bad.zip'
    with zipfile.ZipFile(malicious,'w') as archive:
        for name,data in files.items(): archive.writestr(name,data)
    before=dst._store.snapshot()
    result=dst.import_pack(str(malicious))
    assert not result['ok'], corruption
    assert dst._store.snapshot()==before
    assert not list(dst._store.clips_dir.rglob('*.wav'))


def test_compact_correction_survives_only_when_all_its_anchors_are_exported(libraries,tmp_path):
    src,_=libraries; clip,_=setup_graph(src)
    current=src._store.get_clip(clip['id'])
    current['transcript_analysis']={'alignment':'aligned','words':[
        {'id':'a','start_sample':0,'end_sample':4800,'text':'antes'},
        {'id':'b','start_sample':9600,'end_sample':14400,'text':'depois'}], 'segments':[],
        'corrections':[{'id':'fix','word_ids':['a','b'],'start_sample':0,'end_sample':14400,
            'anchors':[[0,4800],[9600,14400]],'text':'CORRECAO_COMPLETA'}]}
    path=tmp_path/'partial-correction.zip'
    assert src.export_pack([clip['id']],str(path),mode='compact')['ok']
    with zipfile.ZipFile(path) as archive:
        assert b'CORRECAO_COMPLETA' not in archive.read('manifest.json')


def temporal_fixture(source_id):
    return {'version':1,'source_id':source_id,'sample_rate':48000,'interval':[0,14400],
        'alignment':'aligned','text':'antes depois','words':[
            {'id':'a','text':'antes','start_sample':0,'end_sample':4800},
            {'id':'b','text':'depois','start_sample':9600,'end_sample':14400}],
        'segments':[{'id':'s','text':'antes depois','start_sample':0,'end_sample':14400,'word_ids':['a','b']}],
        'corrections':[{'id':'fix','word_ids':['a'],'text':'corrigido','start_sample':0,'end_sample':4800,'anchors':[[0,4800]]}]}


@pytest.mark.parametrize('corruption',['missing_word_time','words_type','segment_text','correction_ids',
    'bool_time','outside_source','duplicate_id','anchor_outside','anchor_shape','unknown_word',
    'correction_cycle','source_mismatch','interval_outside','version','rate'])
def test_editable_temporal_graph_is_validated_before_publish(libraries,tmp_path,corruption):
    src,dst=libraries;clip,_=setup_graph(src)
    path=tmp_path/'temporal.zip';assert src.export_pack(None,str(path),mode='editable')['ok']
    with zipfile.ZipFile(path) as archive: files={name:archive.read(name) for name in archive.namelist()}
    manifest=json.loads(files['manifest.json']);node=manifest['graph']['clips'][0]
    temporal=temporal_fixture(node['source_id'])
    if corruption=='missing_word_time': temporal={'words':[{'id':'bad'}]}
    elif corruption=='words_type': temporal['words']={}
    elif corruption=='segment_text': temporal['segments'][0]['text']={'not':'text'}
    elif corruption=='correction_ids': temporal['corrections'][0].pop('word_ids')
    elif corruption=='bool_time': temporal['words'][0]['start_sample']=False
    elif corruption=='outside_source': temporal['words'][0]['end_sample']=14401
    elif corruption=='duplicate_id': temporal['words'][1]['id']='a'
    elif corruption=='anchor_outside': temporal['corrections'][0]['anchors']=[[4800,9600]]
    elif corruption=='anchor_shape': temporal['words'][0]['anchors']=['wrong']
    elif corruption=='unknown_word': temporal['segments'][0]['word_ids']=['unknown']
    elif corruption=='correction_cycle': temporal['corrections'][0]['word_ids']=['fix']
    elif corruption=='source_mismatch': temporal['source_id']='other'
    elif corruption=='interval_outside': temporal['interval']=[0,20000]
    elif corruption=='version': temporal['version']=999
    elif corruption=='rate': temporal['sample_rate']=44100
    node['transcript_analysis']=temporal;files['manifest.json']=json.dumps(manifest).encode()
    malformed=tmp_path/'malformed.zip'
    with zipfile.ZipFile(malformed,'w') as archive:
        for name,data in files.items(): archive.writestr(name,data)
    before=dst._store.snapshot();result=dst.import_pack(str(malformed))
    assert not result['ok'], corruption
    assert dst._store.snapshot()==before
    assert not list(dst._store.clips_dir.rglob('*.wav'))


@pytest.mark.parametrize('legacy',[False,True])
def test_editable_valid_temporal_and_legacy_text_remain_usable(libraries,tmp_path,legacy):
    src,dst=libraries;clip,_=setup_graph(src)
    from transcript_model import legacy as legacy_text
    src._store.get_clip(clip['id'])['transcript_analysis']=legacy_text('texto antigo') if legacy else temporal_fixture(clip['source_id'])
    path=tmp_path/'valid-temporal.zip';assert src.export_pack(None,str(path),mode='editable')['ok']
    result=dst.import_pack(str(path));assert result['ok'],result
    imported=next(c for c in result['imported'] if c['title']=='Original')
    transcript=dst.get_clip_transcript(imported['id'])
    assert transcript['ok']
    assert transcript['transcript']['text']==('texto antigo' if legacy else 'depois')


def test_compact_reuses_one_encoding_for_same_pcm_with_variable_wav_headers(libraries,tmp_path,monkeypatch):
    import pack_graph
    src,dst=libraries;setup_graph(src)
    original=pack_graph._wav;calls=[]
    def variable_header(data):
        calls.append(len(data))
        # Simula metadado WAV variável sem espera de relógio e sem alterar o PCM.
        return original(data)+len(calls).to_bytes(4,'little')
    monkeypatch.setattr(pack_graph,'_wav',variable_header)
    path=tmp_path/'variable-header.zip'
    result=src.export_pack(None,str(path),mode='compact')
    assert result['ok'],result
    with zipfile.ZipFile(path) as archive:
        manifest=json.loads(archive.read('manifest.json'))
        assert len(calls)==len(manifest['graph']['sources'])
    assert dst.import_pack(str(path))['ok']


def test_rendered_wav_header_is_deterministic_without_waiting_for_clock(monkeypatch):
    import pack_graph
    original=sf.write;count=0
    def write_with_timestamp(buffer,*args,**kwargs):
        nonlocal count
        original(buffer,*args,**kwargs);raw=bytearray(buffer.getvalue());offset=12
        while offset+8<=len(raw):
            size=int.from_bytes(raw[offset+4:offset+8],'little')
            if raw[offset:offset+4]==b'PEAK':
                count+=1;raw[offset+12:offset+16]=count.to_bytes(4,'little')
            offset+=8+size+(size%2)
        buffer.seek(0);buffer.write(raw)
    monkeypatch.setattr(sf,'write',write_with_timestamp)
    data=np.zeros((100,2),dtype=np.float32)
    assert pack_graph._wav(data)==pack_graph._wav(data)


@pytest.mark.parametrize('field,value',[('title',['bad']),('transcript',{}),('tags',[1]),('category',None),
    ('emoji',9),('color',[]),('acoustic_mood',False),('favorite','true'),('duration',-1),('trim_start','0')])
def test_clip_metadata_types_are_validated_before_publish(libraries,tmp_path,field,value):
    src,dst=libraries;setup_graph(src)
    path=tmp_path/'metadata.zip';assert src.export_pack(None,str(path),mode='editable')['ok']
    with zipfile.ZipFile(path) as archive: files={name:archive.read(name) for name in archive.namelist()}
    manifest=json.loads(files['manifest.json']);manifest['graph']['clips'][0][field]=value
    files['manifest.json']=json.dumps(manifest).encode()
    with zipfile.ZipFile(path,'w') as archive:
        for name,data in files.items(): archive.writestr(name,data)
    before=dst._store.snapshot();result=dst.import_pack(str(path))
    assert not result['ok'],field
    assert dst._store.snapshot()==before
    assert not list(dst._store.clips_dir.rglob('*.wav'))
