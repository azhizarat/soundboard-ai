import copy
import threading

import pytest

from test_editor_api import editor
from test_collections import collection
from test_mixer_schedule import SETTINGS


@pytest.fixture
def operation(editor, monkeypatch):
    api, ident = editor
    assert hasattr(api, 'save_collection'), 'Operação de coleções ausente'
    from mixer import DeviceMixer
    monkeypatch.setattr(DeviceMixer, 'ensure_stream', lambda self: True)
    monkeypatch.setattr('mixer.is_cable_output_device', lambda _: False)
    monkeypatch.setattr(api._audio, '_output_route_usable', lambda _: True)
    api._store.update_settings(SETTINGS)
    api.save_clip_edits({'id': ident})
    doc = collection(ident)
    for position, group in enumerate(['track', 'effect', 'speech']):
        pad = copy.deepcopy(doc['pads'][0]); pad.update(id=group, position=position, group=group, hotkey='', loop=group=='track')
        doc['pads'].append(pad)
    assert api.save_collection(doc)['ok']
    assert api.activate_collection('show')['ok']
    yield api, ident
    api.stop_all()
    api._sequence_runner.close()


def test_speech_replaces_only_speech_and_restart_has_new_ownership(operation):
    api, _ = operation
    track = api.trigger_collection_pad('show', 'track')
    effect = api.trigger_collection_pad('show', 'effect')
    first = api.trigger_collection_pad('show', 'pad')
    second = api.trigger_collection_pad('show', 'speech')
    assert all(r['ok'] for r in [track, effect, first, second])
    assert api._audio.get_session(first['play_id']) is None
    assert api._audio.get_session(track['play_id']) is not None
    assert api._audio.get_session(effect['play_id']) is not None
    restarted = api.trigger_collection_pad('show', 'speech')
    assert restarted['execution_id'] != second['execution_id']
    assert api._audio.get_session(second['play_id']) is None
    assert api.stop_collection_pad(track['execution_id'], fade=True)['ok']
    assert api._audio.get_session(track['play_id']).stopping
    assert api._audio.get_session(effect['play_id']) is not None


def test_hold_release_keeps_execution_identity_across_collection_change(operation):
    api, ident = operation
    doc = api._store.get_collection('show'); doc['pads'][0]['hold'] = True
    assert api.save_collection(doc)['ok']
    first = api.trigger_collection_pad('show', 'pad', token='physical-f1')
    assert first['ok']
    assert api.trigger_collection_pad('show', 'pad', repeat=True, token='physical-f1')['ignored']
    assert api.save_collection(collection(ident, ident='other', hold=True))['ok']
    api.activate_collection('other')
    assert api.trigger_collection_pad('other', 'pad', token='physical-f1')['ignored']
    api.trigger_collection_pad('show', 'pad', phase='release', token='physical-f1')
    assert api._audio.get_session(first['play_id']) is None
    assert api.trigger_collection_pad('other', 'pad', token='physical-f1')['ok']


def test_restart_stop_ignore_and_ui_keyboard_share_dispatch(operation):
    api, _ = operation
    doc = api._store.get_collection('show'); doc['pads'][0]['retrigger'] = 'ignore'
    api.save_collection(doc)
    first = api.trigger_collection_pad('show', 'pad')
    assert api.trigger_collection_pad('show', 'pad')['ignored']
    doc = api._store.get_collection('show'); doc['pads'][0]['retrigger'] = 'stop'; api.save_collection(doc)
    api._hotkeys.trigger_collection('f1')
    assert api._audio.get_session(first['play_id']) is None
    api._hotkeys.release_collection('f1')
    api._hotkeys.trigger_collection('f1')
    assert any(x['pad_id']=='pad' for x in api.get_operation_status()['instances'])


def test_stop_all_during_pad_preparation_never_starts_late(operation, monkeypatch):
    api, _ = operation
    entered, release = threading.Event(), threading.Event()
    original = api._audio.render_clip
    def blocked(*args, **kwargs):
        entered.set(); assert release.wait(3); return original(*args, **kwargs)
    monkeypatch.setattr(api._audio, 'render_clip', blocked)
    result = []
    worker = threading.Thread(target=lambda: result.append(api.trigger_collection_pad('show', 'pad')))
    worker.start(); assert entered.wait(3)
    api.stop_all(); release.set(); worker.join(3)
    assert not worker.is_alive() and not result[0]['ok']
    assert not api._audio._sessions


def test_manual_collection_uses_e8_wait_gate(operation):
    api, ident = operation
    from test_sound_sequences import document, play
    from sequence_runner import SequenceRunner
    from test_sequence_runner import Clock
    clip = api._store.get_clip(ident)
    api.save_sequence(document(clip, [{'id':'gate','type':'manual'}, play(clip)]))
    api._sequence_runner = SequenceRunner(api._store, api._audio, clock=Clock(), background=False)
    doc = api._store.get_collection('show'); doc['pads'][0]['target'] = {'type':'sequence','id':'dialogue'}
    api.save_collection(doc)
    started = api.trigger_collection_pad('show', 'pad')
    assert started['ok'] and api._sequence_runner.snapshot()['state'] == 'manual'
    assert api.trigger_collection_pad('show', 'pad', repeat=True)['ignored']
    advanced = api.trigger_collection_pad('show', 'pad')
    assert advanced['ok'] and api._sequence_runner.snapshot()['state'] == 'playing'
    assert advanced['execution_id'] == started['execution_id']


def test_adding_global_sequence_or_clip_hotkey_cannot_shadow_existing_collection(operation):
    api, ident = operation
    from test_sound_sequences import document
    seq = document(api._store.get_clip(ident)); seq['hotkey'] = 'F1'
    assert not api.save_sequence(seq)['ok']
    assert not api.save_clip_edits({'id': ident, 'hotkey': 'f1'})['ok']
    assert not api.update_settings({'replay_hotkey': 'F1'})['ok']


def test_settings_batch_validates_conflicts_in_final_candidate_atomically(operation):
    api, _=operation
    before=api._store.snapshot()['settings']
    result=api.update_settings({'replay_hotkey':'f19','overlay_hotkey':'F19'})
    assert result.get('ok') is False
    assert api._store.snapshot()['settings']==before
    result=api.update_settings({'replay_hotkey':before['overlay_hotkey'],'overlay_hotkey':before['replay_hotkey']})
    assert result.get('ok') is not False, 'Troca válida entre dois atalhos deve validar estado final'


def test_collection_sequence_loop_reprepares_and_stop_all_cancels_wait(operation):
    api, ident = operation
    from test_sound_sequences import document, play
    from sequence_runner import SequenceRunner
    from test_sequence_runner import Clock
    api.save_sequence(document(api._store.get_clip(ident)))
    api._sequence_runner = SequenceRunner(api._store, api._audio, clock=Clock(), background=False)
    doc=api._store.get_collection('show'); doc['pads'][0].update(target={'type':'sequence','id':'dialogue'},loop=True)
    api.save_collection(doc)
    first=api.trigger_collection_pad('show','pad'); assert first['ok']
    old=set(api._audio._sessions)
    api._audio._mixer.cable_mixer.render_block(96001); api._audio._mixer.monitor_mixer.render_block(96001)
    api._sequence_runner.pump(); api._sequence_runner.pump()
    assert api._sequence_runner.snapshot()['state']=='playing'
    assert set(api._audio._sessions) and not (set(api._audio._sessions)&old)
    api.stop_all(); api._sequence_runner.pump()
    assert api._sequence_runner.snapshot()['state']=='cancelled'


def test_stop_all_between_loop_prepare_and_publish_reaches_terminal(operation,monkeypatch):
    api,ident=operation
    from test_sound_sequences import document
    from sequence_runner import SequenceRunner
    from test_sequence_runner import Clock
    api.save_sequence(document(api._store.get_clip(ident),[{'id':'wait','type':'wait','ms':1}]))
    clock=Clock();api._sequence_runner=SequenceRunner(api._store,api._audio,clock=clock,background=False)
    doc=api._store.get_collection('show');doc['pads'][0].update(target={'type':'sequence','id':'dialogue'},loop=True);api.save_collection(doc)
    assert api.trigger_collection_pad('show','pad')['ok']
    prepare=api._sequence_runner._prepare
    def cancel_after_prepare(run):prepare(run);api.stop_all()
    monkeypatch.setattr(api._sequence_runner,'_prepare',cancel_after_prepare)
    clock.advance(.01);api._sequence_runner.pump();api._sequence_runner.pump()
    assert api._sequence_runner.snapshot()['state']=='cancelled'
    monkeypatch.setattr(api._sequence_runner,'_prepare',prepare)
    assert api.start_sequence('dialogue')['ok']


def test_loop_poll_never_unregisters_owner_between_iterations(operation,monkeypatch):
    import numpy as np
    api,ident=operation
    from test_sound_sequences import document
    from sequence_runner import SequenceRunner
    from test_sequence_runner import Clock
    api.save_sequence(document(api._store.get_clip(ident)))
    api._sequence_runner=SequenceRunner(api._store,api._audio,clock=Clock(),background=False)
    doc=api._store.get_collection('show');doc['pads'][0].update(target={'type':'sequence','id':'dialogue'},loop=True);api.save_collection(doc)
    assert api.trigger_collection_pad('show','pad')['ok']
    api._audio._mixer.cable_mixer.render_block(96001);api._audio._mixer.monitor_mixer.render_block(96001)
    original_lane=api._sequence_runner._lane
    polled=[]
    def poll_between_iterations(*args,**kwargs):
        polled.append(api.get_operation_status())
        return original_lane(*args,**kwargs)
    monkeypatch.setattr(api._sequence_runner,'_lane',poll_between_iterations)
    original_resolve=api._store.resolve_sequence_occurrence
    def stop_before_resolve(*args,**kwargs):
        api.stop_all();return original_resolve(*args,**kwargs)
    monkeypatch.setattr(api._store,'resolve_sequence_occurrence',stop_before_resolve)
    api._sequence_runner.pump();api._sequence_runner.pump()
    assert polled and polled[0]['instances'], 'Iteração intermediária não é execução concluída'
    assert api._sequence_runner.snapshot()['state']=='cancelled'
    assert not np.any(api._audio._mixer.cable_mixer.render_block(480))


def test_sequence_smooth_stop_survives_runner_pump(operation):
    import numpy as np
    api, ident = operation
    from test_sound_sequences import document
    from sequence_runner import SequenceRunner
    from test_sequence_runner import Clock
    api.save_sequence(document(api._store.get_clip(ident)))
    api._sequence_runner=SequenceRunner(api._store,api._audio,clock=Clock(),background=False)
    doc=api._store.get_collection('show');doc['pads'][0]['target']={'type':'sequence','id':'dialogue'};api.save_collection(doc)
    result=api.trigger_collection_pad('show','pad');assert result['ok']
    api._audio._mixer.cable_mixer.render_block(480)
    assert api.stop_collection_pad(result['execution_id'],fade=True)['ok']
    api._sequence_runner.pump()
    assert np.any(api._audio._mixer.cable_mixer.render_block(480)), 'Saída suave não deve virar corte imediato no pump'


def test_cancelled_preparation_does_not_accumulate_instances(operation, monkeypatch):
    api, _ = operation
    monkeypatch.setattr(api._audio,'prepare_group',lambda *a,**k: (_ for _ in ()).throw(ValueError('Falha de saída')))
    assert not api.trigger_collection_pad('show','pad')['ok']
    api.get_operation_status()
    assert not api._operation_instances


def test_release_before_press_admission_cancels_only_old_gesture(operation):
    api, _ = operation
    doc=api._store.get_collection('show');doc['pads'][0]['hold']=True;api.save_collection(doc)
    api.trigger_collection_pad('show','pad',phase='release',token='old-gesture')
    old=api.trigger_collection_pad('show','pad',token='old-gesture')
    assert old['ok'] and old.get('ignored') and not api._audio._sessions
    new=api.trigger_collection_pad('show','pad',token='new-gesture');assert new['ok']
    api.trigger_collection_pad('show','pad',phase='release',token='old-gesture')
    assert api._audio.get_session(new['play_id']) is not None


def test_keyboard_processing_loop_can_release_and_stop_while_preparing(operation, monkeypatch):
    api, _ = operation
    doc=api._store.get_collection('show');doc['pads'][0]['hold']=True;api.save_collection(doc)
    entered, resume=threading.Event(),threading.Event();original=api._audio.render_clip
    def blocked(*args,**kwargs):
        entered.set();assert resume.wait(3);return original(*args,**kwargs)
    monkeypatch.setattr(api._audio,'render_clip',blocked)
    finished=threading.Event();original_prepare=api._prepare_operation_pad
    def prepare(*args,**kwargs):
        try:return original_prepare(*args,**kwargs)
        finally:finished.set()
    monkeypatch.setattr(api,'_prepare_operation_pad',prepare)
    returned=threading.Event();results=[]
    def keyboard_processing_loop():
        results.append(api._hotkeys.trigger_collection('f1'))
        returned.set()
        api._hotkeys.release_collection('f1')
        api.stop_all()
    worker=threading.Thread(target=keyboard_processing_loop);worker.start()
    try:
        assert entered.wait(2)
        assert returned.wait(.3), 'keydown bloqueou fila serial de key-up/Stop All'
    finally:
        resume.set();worker.join(3)
    assert not worker.is_alive()
    assert results[0]['ok']
    assert finished.wait(3)
    assert not api._audio._sessions
