import threading

from test_trigger_policy import operation
from test_editor_api import editor


def test_rehearsal_all_entrypoints_monitor_only_and_enable_stops_previous(operation, monkeypatch):
    api, ident = operation
    from test_sound_sequences import document
    api.save_sequence(document(api._store.get_clip(ident)))
    live = api.trigger_collection_pad('show', 'track')
    assert api.set_rehearsal(True)['ok']
    assert api._audio.get_session(live['play_id']) is None
    for call in [lambda: api.play_clip(ident), lambda: api.play_clip(ident, from_hotkey=True),
                 lambda: api.trigger_collection_pad('show', 'pad')]:
        result = call(); assert result['ok'], result
        assert api._audio.get_session(result['play_id']).monitor_only
    calls = []
    monkeypatch.setattr(api._sequence_runner, 'start', lambda *a, **kw: calls.append(kw) or {'state':'playing'})
    assert api.start_sequence('dialogue')['ok']
    assert calls[-1]['monitor_only']
    assert api.preview_sequence(api._store.get_sequence('dialogue'))['ok']
    assert calls[-1]['monitor_only']


def test_rehearsal_refuses_equal_device_ids_without_guessing_names(operation):
    api, _ = operation
    settings = api._store.snapshot()['settings']
    api._store.update_settings({'monitor_device_id': settings['cable_device_id'], 'monitor_device_name':'Fone privado'})
    result = api.set_rehearsal(True)
    assert not result['ok'] and 'isolamento' in result['error']
    assert not api.get_operation_status()['rehearsal']


def test_mode_change_cancels_clip_request_started_before_render_context(operation, monkeypatch):
    api, ident = operation
    entered, release = threading.Event(), threading.Event()
    original = api._store.render_context
    def blocked(*a, **kw):
        entered.set(); assert release.wait(3); return original(*a, **kw)
    monkeypatch.setattr(api._store, 'render_context', blocked)
    result=[]
    worker=threading.Thread(target=lambda: result.append(api.play_clip(ident)))
    worker.start(); assert entered.wait(3)
    assert api.set_rehearsal(True)['ok']
    release.set(); worker.join(3)
    assert not worker.is_alive() and not result[0]['ok']
    assert not api._audio._sessions


def test_device_loss_requires_explicit_reselection_and_no_replay(operation, monkeypatch):
    api, ident = operation
    started=api.trigger_collection_pad('show','pad'); assert started['ok']
    monkeypatch.setattr(api._audio, '_output_route_usable', lambda _: False)
    status=api.get_operation_status()
    assert status['route_error']
    assert not api.play_clip(ident)['ok']
    monkeypatch.setattr(api._audio, '_output_route_usable', lambda _: True)
    assert not api.play_clip(ident)['ok'], 'Reconexão exige confirmação explícita da saída'
    assert not api._audio._sessions


def test_next_trigger_checks_loss_even_without_status_poll_and_selection_rearms(operation, monkeypatch):
    api, ident = operation
    settings = api._store.snapshot()['settings']
    monkeypatch.setattr(api._audio, '_output_route_usable', lambda device: device != settings['cable_device_id'])
    assert not api.play_clip(ident)['ok'], 'Não deve cair silenciosamente só no monitor'
    monkeypatch.setattr(api._audio, '_output_route_usable', lambda _: True)
    assert not api.play_clip(ident)['ok']
    monkeypatch.setattr(api._audio, 'list_devices', lambda: {'outputs': [{'id':1,'name':'Transmissão'}, {'id':2,'name':'Monitor'}]})
    api.update_settings({'cable_device_id': 1, 'monitor_device_id': 2})
    assert api.play_clip(ident)['ok']


def test_live_settings_cannot_remove_rehearsal_isolation(operation):
    api, _ = operation
    assert api.set_rehearsal(True)['ok']
    before = api._store.snapshot()['settings']
    result = api.update_settings({'monitor_device_id': before['cable_device_id']})
    assert result.get('ok') is False
    assert api._store.snapshot()['settings'] == before


def test_enabling_rehearsal_removes_broadcast_tail_before_confirming(operation):
    import numpy as np
    api, _ = operation
    assert api.trigger_collection_pad('show', 'track')['ok']
    assert np.any(api._audio._mixer.cable_mixer.render_block(480))
    assert api.set_rehearsal(True)['ok']
    assert not np.any(api._audio._mixer.cable_mixer.render_block(480)), 'Ensaio confirmado não pode deixar fade residual no cabo'


def test_native_stream_loss_is_reported_even_when_device_enumeration_still_lists_it(operation):
    api, ident = operation
    assert api.trigger_collection_pad('show', 'pad')['ok']
    api._audio._mixer.cable_mixer.invalidate_route('USB removido')
    assert api.get_operation_status()['route_error']
    assert not api.play_clip(ident)['ok']
    assert not api._audio._sessions


def test_refresh_devices_does_not_silently_rebind_output(operation, monkeypatch):
    api, _ = operation
    before=api._store.snapshot()['settings']
    monkeypatch.setattr(api._audio,'list_devices',lambda:{'outputs':[{'id':19,'name':before['cable_device_name']}],'inputs':[],'detected_cable_id':19,'default_output_id':19})
    api.refresh_devices()
    assert api._store.snapshot()['settings']==before


def test_global_sequence_hotkey_freezes_request_before_background_dispatch(operation, monkeypatch):
    api, ident=operation
    from test_sound_sequences import document
    api.save_sequence(document(api._store.get_clip(ident)))
    dispatched=[]
    api._sequence_runner._background=False
    class DeferredThread:
        def __init__(self,target,**kwargs): self.target=target
        def start(self): dispatched.append(self.target)
    monkeypatch.setattr('sequence_service.threading.Thread',DeferredThread)
    api._trigger_sequence_hotkey('dialogue')
    assert api.set_rehearsal(True)['ok']
    assert api.set_rehearsal(False)['ok']
    dispatched.pop()()
    assert not api._audio._sessions, 'Pedido de tecla anterior à mudança não pode iniciar depois dela'


def test_global_clip_hotkey_freezes_request_before_background_dispatch(operation, monkeypatch):
    import sys
    import types
    api, ident=operation
    registrations={};deferred=[]
    fake=types.SimpleNamespace(add_hotkey=lambda key,fn,**kw:registrations.setdefault(key,fn),remove_hotkey=lambda _:None,
        on_release_key=lambda *a:'release',unhook=lambda _:None)
    monkeypatch.setitem(sys.modules,'keyboard',fake)
    api._hotkeys.enabled=True
    api._hotkeys.refresh([{'id':ident,'hotkey':'f9'}],{})
    class DeferredThread:
        def __init__(self,target,args=(),**kwargs): self.target=lambda:target(*args)
        def start(self): deferred.append(self.target)
    monkeypatch.setattr('hotkeys.threading.Thread',DeferredThread)
    registrations['f9']()
    assert api.set_rehearsal(True)['ok']
    assert api.set_rehearsal(False)['ok']
    deferred.pop()()
    assert not api._audio._sessions


def test_rehearsal_ack_waits_for_concurrent_stop_and_removes_all_cable_pcm(operation, monkeypatch):
    import numpy as np
    api, _=operation
    api.trigger_collection_pad('show','track')
    api._audio._mixer.cable_mixer.render_block(480)
    entered,resume,ack=threading.Event(),threading.Event(),threading.Event()
    original=api._audio._mixer.stop_all
    def blocked(*args,**kwargs):
        entered.set();assert resume.wait(3);return original(*args,**kwargs)
    monkeypatch.setattr(api._audio._mixer,'stop_all',blocked)
    stopper=threading.Thread(target=api.stop_all);stopper.start();assert entered.wait(2)
    results=[]
    switch=threading.Thread(target=lambda:(results.append(api.set_rehearsal(True)),ack.set()));switch.start()
    try:
        assert not ack.wait(.2), 'Ensaio confirmou antes de concluir a parada concorrente'
    finally:
        resume.set();stopper.join(3);switch.join(3)
    assert not stopper.is_alive() and not switch.is_alive()
    assert results[0]['ok']
    assert not np.any(api._audio._mixer.cable_mixer.render_block(480))


def test_rehearsal_validation_serializes_with_settings_publication(operation, monkeypatch):
    api, _=operation
    entered,resume,ack=threading.Event(),threading.Event(),threading.Event()
    original=api._store.update_settings
    def blocked(updates):
        entered.set();assert resume.wait(3);return original(updates)
    monkeypatch.setattr(api._store,'update_settings',blocked)
    updates=[];results=[]
    writer=threading.Thread(target=lambda:updates.append(api.update_settings({'monitor_volume':0})));writer.start();assert entered.wait(2)
    switch=threading.Thread(target=lambda:(results.append(api.set_rehearsal(True)),ack.set()));switch.start()
    try:
        assert not ack.wait(.2), 'Validação de ensaio ultrapassou atualização validada ainda não publicada'
    finally:
        resume.set();writer.join(3);switch.join(3)
    assert not writer.is_alive() and not switch.is_alive()
    assert not results[0]['ok'] and not api.get_operation_status()['rehearsal']


def test_rehearsal_wait_does_not_hold_api_lock_across_owner_callbacks(operation,monkeypatch):
    api,ident=operation
    owner_entered,waiting=threading.Event(),threading.Event()
    original=api._audio.stop_all
    def stop(*args,**kwargs):
        if kwargs.get('wait'):waiting.set()
        return original(*args,**kwargs)
    monkeypatch.setattr(api._audio,'stop_all',stop)
    callbacks=[];results=[]
    def cancel_owner():
        owner_entered.set()
        assert waiting.wait(2)
        callbacks.append(api.play_clip(ident))
    api._audio.register_group_owner('callback-owner',cancel_owner)
    stopper=threading.Thread(target=api.stop_all);stopper.start();assert owner_entered.wait(2)
    switch=threading.Thread(target=lambda:results.append(api.set_rehearsal(True)));switch.start()
    stopper.join(3);switch.join(3)
    assert not stopper.is_alive() and not switch.is_alive()
    assert results[0]['ok'] and not callbacks[0]['ok']
    assert 'modo' in callbacks[0]['error']
