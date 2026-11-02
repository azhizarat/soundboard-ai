import copy
import numpy as np
import pytest
import soundfile as sf

from store import SoundboardStore
from ai_analyzer import AIAnalyzer


@pytest.fixture
def edited(tmp_path):
    sf.write(tmp_path / 'source.wav', np.zeros((48000, 2), np.float32), 48000)
    store = SoundboardStore(str(tmp_path / 'data'))
    clip = store.add_imported_clip({'file_path': str(tmp_path / 'source.wav'), 'title': 'Inicial'}, rendered=True)
    store.begin_edit(clip['id'])
    yield store, clip['id']
    store.close()


def request(store, clip, stage='metadata'):
    assert hasattr(store, 'prepare_analysis'), 'Guardas de análise ausentes'
    return store.prepare_analysis(clip, stage)


def test_new_request_obsoletes_older_result_in_same_revision(edited):
    store, clip = edited
    old = request(store, clip)
    new = request(store, clip)
    assert store.apply_analysis(old, {'fields': {'title': 'Velho'}})['state'] == 'obsolete'
    assert store.apply_analysis(new, {'fields': {'title': 'Novo'}})['state'] == 'applied'
    assert store.render_context(clip, draft=True)['title'] == 'Novo'


@pytest.mark.parametrize('mutation', ['edit', 'discard_reopen', 'commit'])
def test_inflight_result_cannot_overwrite_changed_draft(edited, mutation):
    store, clip = edited
    token = request(store, clip)
    if mutation == 'edit':
        store.update_edit(clip, {'title': 'Humano'})
    elif mutation == 'discard_reopen':
        store.discard_edit(clip)
        store.begin_edit(clip)
    else:
        store.commit_edit(clip)
    assert store.apply_analysis(token, {'fields': {'title': 'Tardio'}})['state'] == 'obsolete'
    assert store.render_context(clip, draft=True)['title'] != 'Tardio'


def test_human_fields_require_proposal_acceptance_and_token_is_revalidated(edited):
    store, clip = edited
    store.update_edit(clip, {'title': 'Humano'})
    token = request(store, clip)
    result = store.apply_analysis(token, {'fields': {'title': 'Proposta', 'emoji': '✨'}})
    assert result['state'] == 'proposal'
    assert store.render_context(clip, draft=True)['title'] == 'Humano'
    assert store.accept_analysis(clip, token['analysis_id'], result['accept_token'])['state'] == 'applied'
    assert store.render_context(clip, draft=True)['title'] == 'Proposta'
    again = request(store, clip)
    proposal = store.apply_analysis(again, {'fields': {'title': 'Outra'}})
    store.update_edit(clip, {'title': 'Mais recente'})
    assert store.accept_analysis(clip, again['analysis_id'], proposal['accept_token'])['state'] == 'obsolete'


def test_api_accepted_proposal_stays_applied_after_editor_reopens(edited):
    from main import SoundboardAPI
    from analysis_jobs import AnalysisQueue
    store, clip = edited
    store.update_edit(clip, {'title': 'Humano'})
    token = request(store, clip)
    api = SoundboardAPI.__new__(SoundboardAPI)
    api._store = store
    api._analysis_queue = AnalysisQueue()
    try:
        api._analysis_queue.submit(lambda: {'status': 'ready', 'fields': {'title': 'Aceita'}},
                                   apply=lambda result: store.apply_analysis(token, result), metadata=token)
        api._analysis_queue.wait(token['analysis_id'], 3)
        proposal = api.get_analysis_job(token['analysis_id'])['application']
        assert proposal['state'] == 'proposal'
        assert api.accept_analysis_result(clip, token['analysis_id'], proposal['accept_token'])['ok']
        assert api.begin_clip_edit(clip)['ok']
        refreshed = api.get_analysis_job(token['analysis_id'])
        assert refreshed['application'] == {'state': 'applied'}
        assert refreshed['status'] == 'ready'
        assert store.render_context(clip, draft=True)['title'] == 'Aceita'
        # O histórico de propostas é limitado: uma entrada removida não volta
        # a oferecer o token inicial que ainda existe no histórico da fila.
        store.get_clip(clip)['analysis_proposals'].pop(token['analysis_id'])
        assert api.get_analysis_job(token['analysis_id'])['application'] == {'state': 'obsolete'}
    finally:
        api._analysis_queue.close()


def test_manual_legacy_text_survives_projection_and_restart(edited):
    store, clip = edited
    store.update_edit(clip, {'transcript': 'corrigido sem alinhamento'})
    assert hasattr(store, 'get_transcript'), 'Leitura temporal ausente'
    view = store.get_transcript(clip)
    assert view['alignment'] == 'unaligned' and view['text'] == 'corrigido sem alinhamento'
    store.commit_edit(clip)
    other = SoundboardStore(str(store.data_dir))
    try:
        assert other.get_transcript(clip)['text'] == 'corrigido sem alinhamento'
    finally:
        other.close()


def test_temporal_asr_requests_real_word_times_and_configured_parameters(tmp_path):
    from types import SimpleNamespace as NS
    ai = AIAnalyzer(str(tmp_path))
    assert hasattr(ai, 'transcribe_temporal'), 'ASR temporal ausente'
    kwargs = {}
    class Model:
        def transcribe(self, audio, **params):
            kwargs.update(params)
            return iter([NS(text='oi', start=0, end=.5, words=[NS(word='oi', start=.1, end=.4)])]), None
    ai._whisper = Model()
    ai.configure_transcription({'beam_size': 5, 'hotwords': 'Lucas', 'initial_prompt': 'Português'})
    result = ai.transcribe_temporal(np.zeros(16000), 'a', 's', [48000, 96000])
    assert kwargs['word_timestamps'] is True and kwargs['beam_size'] == 5
    assert kwargs['hotwords'] == 'Lucas' and kwargs['initial_prompt'] == 'Português'
    assert result['words'][0]['start_sample'] == 52800
    assert result['status'] == 'ready' and result['model_version']


def test_temporal_asr_distinguishes_unavailable_from_no_speech(tmp_path, monkeypatch):
    ai = AIAnalyzer(str(tmp_path))
    assert hasattr(ai, 'transcribe_temporal'), 'ASR temporal ausente'
    monkeypatch.setattr(ai, '_get_whisper', lambda: None)
    result = ai.transcribe_temporal(np.zeros(16000), 'a', 's', [0, 48000])
    assert result['status'] == 'failed' and result['error']['code'] == 'model_unavailable'


def test_manual_text_becomes_proposal_instead_of_apply_exception(edited):
    store, clip = edited
    store.update_edit(clip, {'transcript': 'Humano'})
    token = request(store, clip, 'transcription')
    result = {'status': 'ready', 'text': 'novo', 'alignment': 'aligned', 'words': [], 'segments': [], 'corrections': []}
    assert store.apply_analysis(token, result)['state'] == 'proposal'


def test_projected_transcript_changes_with_spans(edited):
    store, clip = edited
    token = request(store, clip, 'transcription')
    result = {'status': 'ready', 'text': 'primeiro segundo', 'alignment': 'aligned',
              'words': [{'id': 'a', 'text': 'primeiro', 'start_sample': 0, 'end_sample': 10000},
                        {'id': 'b', 'text': 'segundo', 'start_sample': 30000, 'end_sample': 40000}], 'segments': [], 'corrections': []}
    store.apply_analysis(token, result)
    store.update_edit(clip, {'spans': [[20000, 48000]]})
    assert store.render_context(clip, draft=True)['transcript'] == 'segundo'


def test_api_metadata_never_invokes_asr_and_queue_result_is_queryable(edited):
    from main import SoundboardAPI
    store, clip = edited
    api = SoundboardAPI(data_dir=str(store.data_dir / 'api'), start_background_services=False)
    api._store.close()
    api._store = store
    try:
        assert hasattr(api, 'request_clip_analysis'), 'API de trabalhos ausente'
        api._ai.transcribe_temporal = lambda *a, **k: pytest.fail('Título chamou ASR')
        store.update_settings({'use_local_llm': False})
        store.update_edit(clip, {'transcript': 'uma frase mantida'})
        response = api.request_clip_analysis(clip, 'metadata')
        assert response['ok'] and response['status'] in ('queued', 'running')
        api._analysis_queue.wait(response['analysis_id'], 3)
        done = api.get_analysis_job(response['analysis_id'])
        assert done['status'] == 'ready'
        assert done['result']['metadata_source'] == 'heuristic'
        assert store.render_context(clip, draft=True)['transcript'] == 'uma frase mantida'
    finally:
        if hasattr(api, '_analysis_queue'):
            api._analysis_queue.close()
        api._audio.shutdown()


def test_transcription_configuration_roundtrips(tmp_path):
    import config
    original = config.load_config(tmp_path)
    changed = config.save_config(original, {'asr_model': 'small', 'asr_device': 'cpu', 'asr_beam_size': 5, 'asr_hotwords': 'Lucas'})
    assert getattr(changed, 'asr_model', None) == 'small'
    assert config.load_config(tmp_path).asr_beam_size == 5


def test_metadata_input_change_obsoletes_result(edited):
    store, clip = edited
    token = request(store, clip)
    store.update_edit(clip, {'transcript': 'Outra frase'})
    assert store.apply_analysis(token, {'fields': {'title': 'Título antigo'}})['state'] == 'obsolete'


def test_failed_apply_persistence_rolls_back_fields_and_provenance(edited, monkeypatch):
    store, clip = edited
    token = request(store, clip)
    before = store.snapshot()
    def fail():
        raise OSError('disco cheio')
    monkeypatch.setattr(store, 'save', fail)
    with pytest.raises(OSError):
        store.apply_analysis(token, {'fields': {'title': 'Não persistido'}})
    assert store.snapshot() == before


def test_reopening_ui_invalidates_job_even_without_editing(edited):
    from main import SoundboardAPI
    store, clip = edited
    api = SoundboardAPI.__new__(SoundboardAPI)
    api._store = store
    token = request(store, clip)
    assert api.begin_clip_edit(clip)['ok']
    assert store.apply_analysis(token, {'fields': {'title': 'Tardio'}})['state'] == 'obsolete'


def test_analyzer_serializes_direct_legacy_calls_with_temporal_asr(tmp_path):
    import threading
    ai = AIAnalyzer(str(tmp_path))
    entered, release, attempted, second_entered = [threading.Event() for _ in range(4)]
    class Model:
        count = 0
        def transcribe(self, *args, **kwargs):
            self.count += 1
            if self.count == 1:
                entered.set()
                release.wait(2)
            else:
                second_entered.set()
            return iter([]), None
    ai._whisper = Model()
    first = threading.Thread(target=lambda: ai.transcribe_result(np.zeros(16000)))
    def next_call():
        attempted.set()
        # PCM distinto força duas inferências; conteúdo idêntico agora usa cache E6.
        ai.transcribe_temporal(np.ones(16000), 'a', 's', [0, 48000])
    second = threading.Thread(target=next_call)
    first.start()
    assert entered.wait(2)
    second.start()
    assert attempted.wait(2)
    try:
        assert not second_entered.wait(.1), 'Inferências executaram simultaneamente'
    finally:
        release.set()
        first.join(2)
        second.join(2)
    assert second_entered.is_set()


def test_queue_full_does_not_supersede_an_accepted_request(edited):
    import threading
    from main import SoundboardAPI
    from analysis_jobs import AnalysisQueue
    store, clip = edited
    api = SoundboardAPI.__new__(SoundboardAPI)
    api._store = store
    api._analysis_queue = AnalysisQueue(capacity=1, autostart=False)
    api._analysis_queue.submit(lambda: {})
    previous = request(store, clip)
    response = api.request_clip_analysis(clip, 'metadata')
    try:
        assert response['error_code'] == 'queue_full'
        assert store.apply_analysis(previous, {'fields': {'title': 'Aceito antes'}})['state'] == 'applied'
    finally:
        api._analysis_queue.close()


def test_selection_context_only_applies_inside_target_after_acceptance(edited):
    store, clip = edited
    token = store.prepare_analysis(clip, 'transcription', [10000, 20000])
    result = {'status':'ready', 'alignment':'aligned', 'words':[
        {'id':'before','text':'antes','start_sample':0,'end_sample':5000},
        {'id':'inside','text':'alvo','start_sample':12000,'end_sample':15000},
        {'id':'after','text':'depois','start_sample':30000,'end_sample':35000}], 'segments':[], 'corrections':[]}
    proposal = store.apply_analysis(token, result)
    assert proposal['state'] == 'proposal'
    assert store.accept_analysis(clip, token['analysis_id'], proposal['accept_token'])['state'] == 'applied'
    assert store.get_transcript(clip)['text'] == 'alvo'


def test_selection_cannot_discard_unaligned_text_outside_target(edited):
    store, clip = edited
    store.update_edit(clip, {'transcript': 'texto sem tempos de fora e dentro'})
    with pytest.raises(ValueError, match='alinhad'):
        store.prepare_analysis(clip, 'transcription', [10000, 20000])


def test_bad_asr_configuration_does_not_replace_valid_file(tmp_path):
    import config
    original = config.save_config(config.load_config(tmp_path), {'asr_beam_size': 5})
    before = (tmp_path / 'config.json').read_bytes()
    with pytest.raises(ValueError):
        config.save_config(original, {'asr_beam_size': 'inválido'})
    assert (tmp_path / 'config.json').read_bytes() == before


def test_queued_job_has_c2_identity_and_requested_model_parameters(edited):
    from main import SoundboardAPI
    from analysis_jobs import AnalysisQueue
    store, clip = edited
    api = SoundboardAPI(data_dir=str(store.data_dir / 'api'), start_background_services=False)
    api._store.close()
    api._store = store
    api._analysis_queue = AnalysisQueue(autostart=False)
    try:
        queued = api.request_clip_analysis(clip, 'transcription')
        assert queued['status'] == 'queued'
        assert queued['model'] == 'base'
        assert queued['model_version'].startswith('faster-whisper/1.2.1')
        assert queued['parameters']['word_timestamps'] is True
        assert queued['interval'] == [0, 48000] and queued['revision_id']
    finally:
        api._analysis_queue.close()
        api._audio.shutdown()


@pytest.mark.parametrize('cancel', [False, True])
def test_api_inflight_manual_edit_and_cancel_preserve_text_and_release_source(edited, cancel):
    import threading
    from main import SoundboardAPI
    store, clip = edited
    api = SoundboardAPI(data_dir=str(store.data_dir / 'api'), start_background_services=False)
    api._store.close()
    api._store = store
    entered, finish = threading.Event(), threading.Event()
    def fake(*args, **kwargs):
        entered.set()
        assert finish.wait(3)
        return {'status':'ready', 'text':'resultado tardio', 'alignment':'aligned',
                'words':[{'id':'w','text':'resultado tardio','start_sample':0,'end_sample':48000}],
                'segments':[], 'corrections':[]}
    api._ai.transcribe_temporal = fake
    try:
        response = api.request_clip_analysis(clip)
        assert entered.wait(3)
        source_id = response['source_id']
        assert 'active' in store.source_references(source_id)
        assert api.update_clip_edit({'id':clip, 'transcript':'corrigido durante inferência'})['ok']
        assert api.get_edit_source_waveform(clip, 16)['ok']
        if cancel:
            assert api.cancel_analysis_job(response['analysis_id'])['ok']
        finish.set()
        job = api._analysis_queue.wait(response['analysis_id'], 3)
        assert job['status'] == ('cancelled' if cancel else 'ready')
        if not cancel:
            assert job['application']['state'] == 'obsolete'
        assert store.render_context(clip, draft=True)['transcript'] == 'corrigido durante inferência'
    finally:
        finish.set()
        api._analysis_queue.close()
        api._audio.shutdown()
    assert 'active' not in store.source_references(source_id)


def test_api_accept_partial_correction_block_rejects_without_changing_draft_or_proposal(edited):
    from main import SoundboardAPI
    store, clip = edited
    original = {'status':'ready', 'alignment':'aligned',
                'words':[{'id':'a','text':'um','start_sample':0,'end_sample':10000},
                         {'id':'b','text':'dois','start_sample':30000,'end_sample':48000}],
                'segments':[], 'corrections':[]}
    store.apply_analysis(request(store,clip,'transcription'), original)
    store.correct_transcript(clip, ['a','b'], 'bloco corrigido')
    token = store.prepare_analysis(clip,'transcription',[10000,30000])
    proposal = store.apply_analysis(token, {'status':'ready','words':[],'segments':[],'corrections':[]})
    before = store.snapshot()
    api = SoundboardAPI.__new__(SoundboardAPI)
    api._store = store
    response = api.accept_analysis_result(clip, token['analysis_id'], proposal['accept_token'])
    assert response['ok'] is False
    assert 'Amplie a seleção' in response['error']
    assert store.snapshot() == before


@pytest.mark.parametrize('mode', ['failure','missing','empty'])
def test_speaker_job_distinguishes_failure_from_no_speech_and_preserves_people(edited, monkeypatch, mode):
    from main import SoundboardAPI
    from types import SimpleNamespace as NS
    store, clip = edited
    # Simula identificação automática anterior, sem proteção humana mascarando a falha.
    store.get_clip(clip).update(speaker_id='known',speaker_ids=['known'],segments=[{'speaker_id':'known'}])
    api = SoundboardAPI(data_dir=str(store.data_dir/'api'), start_background_services=False)
    api._store.close(); api._store = store
    def process(_audio):
        if mode == 'failure':
            raise RuntimeError('erro de inferência sintético')
        return NS(sort_by_start_time=lambda: [])
    monkeypatch.setattr(api._ai,'_get_diarizer',lambda: None if mode == 'missing' else NS(process=process))
    before = store.render_context(clip,draft=True)
    try:
        queued = api.request_clip_analysis(clip,'speakers')
        job = api._analysis_queue.wait(queued['analysis_id'],3)
        if mode == 'empty':
            assert job['status'] == 'no_speech' and job['error'] is None
        else:
            assert job['status'] == 'failed'
            assert job['error']['code'] == ('inference_error' if mode == 'failure' else 'model_unavailable')
            assert 'application' not in job
            current = store.render_context(clip,draft=True)
            assert {k:current[k] for k in ('speaker_id','speaker_ids','segments')} == {k:before[k] for k in ('speaker_id','speaker_ids','segments')}
    finally:
        api._analysis_queue.close(); api._audio.shutdown()


def test_legacy_split_returns_structured_inference_error_and_creates_no_clips(edited, monkeypatch):
    from main import SoundboardAPI
    from types import SimpleNamespace as NS
    store, clip = edited
    api = SoundboardAPI.__new__(SoundboardAPI)
    api._store = store
    api._ai = AIAnalyzer(str(store.models_dir))
    api._capability_error = lambda _: None
    api._emit_progress = lambda *a: None
    def fail(_audio):
        raise RuntimeError('diarização falhou')
    monkeypatch.setattr(api._ai,'_get_diarizer',lambda: NS(process=fail))
    before = store.snapshot()
    result = api.split_clip_by_speakers(clip)
    assert result['ok'] is False and result['error_code'] == 'inference_error'
    assert 'diarização falhou' in result['error']
    assert store.snapshot() == before


def test_api_mixed_alignment_partial_accept_is_rejected_atomically_and_full_block_is_safe(edited):
    from main import SoundboardAPI
    from transcript_model import from_segments
    from types import SimpleNamespace as NS
    store, clip = edited
    original = from_segments([NS(start=0,end=.25,text='antes',words=None),
                              NS(start=.25,end=.75,text='alvo',words=[NS(start=.25,end=.75,word='alvo')]),
                              NS(start=.75,end=1,text='depois',words=[])], 'old','source',[0,48000],{})
    store.apply_analysis(request(store,clip,'transcription'),original)
    api = SoundboardAPI.__new__(SoundboardAPI); api._store = store
    partial = store.prepare_analysis(clip,'transcription',[2400,9600])
    proposal = store.apply_analysis(partial,{'words':[],'segments':[],'corrections':[]})
    before = store.snapshot()
    response = api.accept_analysis_result(clip,partial['analysis_id'],proposal['accept_token'])
    assert response['ok'] is False and 'Amplie a seleção' in response['error']
    assert store.snapshot() == before
    complete = store.prepare_analysis(clip,'transcription',[0,12000])
    replacement = from_segments([NS(start=0,end=.25,text='novo',words=[NS(start=0,end=.25,word='novo')])], 'new','source',[0,12000],{})
    proposal = store.apply_analysis(complete,replacement)
    assert api.accept_analysis_result(clip,complete['analysis_id'],proposal['accept_token'])['ok']
    assert store.get_transcript(clip)['text'] == 'novo alvo depois'


def test_api_partial_manual_correction_of_existing_block_is_atomic(edited):
    from main import SoundboardAPI
    store,clip = edited
    source = {'words':[{'id':'a','text':'erro1','start_sample':0,'end_sample':12000},
                       {'id':'b','text':'erro2','start_sample':12000,'end_sample':24000}],
              'segments':[],'corrections':[]}
    store.apply_analysis(request(store,clip,'transcription'),source)
    store.correct_transcript(clip,['a','b'],'bom dia')
    before=store.snapshot()
    api=SoundboardAPI.__new__(SoundboardAPI); api._store=store
    response=api.correct_clip_transcript(clip,['a'],'olá')
    assert not response['ok'] and 'bloco' in response['error']
    assert store.snapshot()==before
    assert api.correct_clip_transcript(clip,['a','b'],'olá mundo')['ok']
    assert store.get_transcript(clip)['text']=='olá mundo'


def test_failed_request_persistence_restores_only_its_generation_and_releases_queue_reservation(edited,monkeypatch):
    from main import SoundboardAPI
    from analysis_jobs import AnalysisQueue
    store,clip=edited
    first=request(store,clip)
    other=store.add_or_update_clip({'id':'other','title':'Anterior'})
    api=SoundboardAPI.__new__(SoundboardAPI); api._store=store
    api._analysis_queue=AnalysisQueue(capacity=1,autostart=False)
    def fail():
        other['title']='Alteração independente'
        raise OSError('disco cheio')
    try:
        with monkeypatch.context() as patch:
            patch.setattr(store,'save',fail)
            rejected=api.request_clip_analysis(clip,'metadata')
        assert rejected['ok'] is False
        assert store.get_clip('other')['title']=='Alteração independente'
        assert store.apply_analysis(first,{'fields':{'title':'Primeiro aceito'}})['state']=='applied'
        assert store._active_sources=={}
        reserved=api._analysis_queue.reserve()
        assert reserved['ok']
        api._analysis_queue.release_reservation(reserved['reservation'])
    finally:
        api._analysis_queue.close()


@pytest.mark.parametrize('human', [False,True])
def test_metadata_speaker_context_follows_kept_temporal_evidence_or_explicit_human_assignment(edited,monkeypatch,human):
    from main import SoundboardAPI
    store,clip=edited
    store.ensure_speaker('A','Pessoa removida'); store.ensure_speaker('B','Pessoa mantida')
    original=store.get_clip(clip)
    original.update(speaker_id='A',speaker_ids=['A','B'],transcript='uma frase',segments=[
        {'speaker_id':'A','start_sample':0,'end_sample':24000},
        {'speaker_id':'B','start_sample':24000,'end_sample':48000}])
    store.update_edit(clip,{'spans':[[24000,48000]]})
    if human:
        store.update_edit(clip,{'speaker_id':'B'})
        store.update_edit(clip,{'speaker_id':'A'})
    store.update_settings({'use_local_llm':False})
    api=SoundboardAPI(data_dir=str(store.data_dir/'api'),start_background_services=False)
    api._store.close(); api._store=store
    monkeypatch.setattr(api._ai,'transcribe_temporal',lambda *a,**k:pytest.fail('Metadata chamou ASR'))
    monkeypatch.setattr(api._ai,'diarize_and_identify',lambda *a,**k:pytest.fail('Metadata chamou diarização'))
    before_person=store.render_context(clip,draft=True)['speaker_id']
    try:
        queued=api.request_clip_analysis(clip,'metadata')
        job=api._analysis_queue.wait(queued['analysis_id'],3)
        assert job['status']=='ready'
        tags=job['result']['fields']['tags']
        assert ('pessoa removida' in tags) == human
        assert ('pessoa mantida' in tags) == (not human)
        assert store.render_context(clip,draft=True)['speaker_id']==before_person
    finally:
        api._analysis_queue.close(); api._audio.shutdown()


@pytest.mark.parametrize('short_input,code',[(True,'insufficient_audio'),(False,'insufficient_speech')])
def test_insufficient_speaker_audio_preserves_previous_people_and_segments(edited,monkeypatch,short_input,code):
    from main import SoundboardAPI
    from types import SimpleNamespace as NS
    store,clip=edited
    store.get_clip(clip).update(speaker_id='existing',speaker_ids=['existing'],segments=[{'speaker_id':'existing'}])
    if short_input:
        store.update_edit(clip,{'spans':[[0,4800]]})
    api=SoundboardAPI(data_dir=str(store.data_dir/'api'),start_background_services=False)
    api._store.close(); api._store=store
    calls=[]
    def process(audio):
        calls.append(len(audio))
        return NS(sort_by_start_time=lambda:[NS(start=.1,end=.3,speaker=0)])
    monkeypatch.setattr(api._ai,'_get_diarizer',lambda:NS(process=process))
    before=store.render_context(clip,draft=True)
    try:
        queued=api.request_clip_analysis(clip,'speakers')
        job=api._analysis_queue.wait(queued['analysis_id'],3)
        assert job['status']=='failed' and job['error']['code']==code
        assert job['error']['message'] and 'application' not in job
        assert bool(calls) is (not short_input)
        current=store.render_context(clip,draft=True)
        assert {k:current[k] for k in ('speaker_id','speaker_ids','segments')}=={k:before[k] for k in ('speaker_id','speaker_ids','segments')}
    finally:
        api._analysis_queue.close(); api._audio.shutdown()


@pytest.mark.parametrize('short_input,code',[(True,'insufficient_audio'),(False,'insufficient_speech')])
def test_legacy_split_insufficient_audio_returns_reason_without_creating_clips(edited,monkeypatch,short_input,code):
    import ai_analyzer
    from main import SoundboardAPI
    from types import SimpleNamespace as NS
    store,clip=edited
    api=SoundboardAPI.__new__(SoundboardAPI); api._store=store
    api._ai=AIAnalyzer(str(store.models_dir)); api._capability_error=lambda _:None
    api._emit_progress=lambda *a:None
    monkeypatch.setattr(ai_analyzer,'load_audio_mono_16k',lambda _:(np.zeros(1600 if short_input else 16000),.1 if short_input else 1))
    monkeypatch.setattr(api._ai,'_get_diarizer',lambda:NS(process=lambda _:NS(sort_by_start_time=lambda:[NS(start=.1,end=.3,speaker=0)])))
    before=store.snapshot()
    response=api.split_clip_by_speakers(clip)
    assert not response['ok'] and response['error_code']==code
    assert store.snapshot()==before
