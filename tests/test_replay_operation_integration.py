"""Contratos da fonte de replay integrada à operação e ao ensaio."""
from test_trigger_policy import operation
from test_editor_api import editor


def test_replay_source_changes_preserve_rehearsal_and_output_choices(operation, monkeypatch):
    api, _ = operation
    calls = []
    monkeypatch.setattr(api._audio, 'set_loopback_source', calls.append)
    assert api.set_rehearsal(True)['ok']
    before = api._store.snapshot()['settings']

    result = api.update_settings({'replay_source_name': '  Saída de jogo  '})

    assert result['replay_source_name'] == 'Saída de jogo'
    assert calls == ['Saída de jogo']
    assert api.get_operation_status()['rehearsal']
    for key in ('cable_device_id', 'monitor_device_id', 'monitor_volume'):
        assert result[key] == before[key]


def test_rejected_settings_do_not_switch_replay_capture(operation, monkeypatch):
    api, _ = operation
    calls = []
    monkeypatch.setattr(api._audio, 'set_loopback_source', calls.append)
    before = api._store.snapshot()['settings']

    result = api.update_settings({'replay_source_name': 'Outra saída',
                                  'replay_hotkey': 'f19', 'overlay_hotkey': 'f19'})

    assert result['ok'] is False
    assert calls == []
    assert api._store.snapshot()['settings'] == before


def test_replay_selection_waits_for_mode_transition(operation, monkeypatch):
    api, _ = operation
    calls = []
    monkeypatch.setattr(api._audio, 'set_loopback_source', calls.append)
    api._operation_transition = True
    try:
        result = api.update_settings({'replay_source_name': 'Outra saída'})
        assert result['ok'] is False
        assert not calls
        assert api._store.snapshot()['settings']['replay_source_name'] == ''
    finally:
        api._operation_transition = False
