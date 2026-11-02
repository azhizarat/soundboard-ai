"""A abertura da interface não carrega filtros que só serão usados no áudio."""
import os
from pathlib import Path
import subprocess
import sys
import threading

import pytest


def test_importing_application_and_identity_resampling_do_not_load_scipy(tmp_path):
    root = Path(__file__).resolve().parents[1]
    script = """
import sys
import main
import numpy as np
from audio_processing import resample_audio
data = np.ones((8, 2), dtype=np.float32)
assert resample_audio(data, 48000, 48000) is data
assert resample_audio(data[:0], 44100, 48000).shape == (0, 2)
assert not any(name == 'scipy' or name.startswith('scipy.') for name in sys.modules), 'DSP carregado antes de ser necessário'
"""
    result = subprocess.run([sys.executable, '-c', script], cwd=root,
                            env={**os.environ, 'PYTHONPATH': str(root),
                                 'PYTHONDONTWRITEBYTECODE': '1',
                                 'SOUNDBOARD_MODELS_DIR': str(tmp_path / 'models'),
                                 'SOUNDBOARD_SEPARADOS_ROOT': str(tmp_path / 'separados')},
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


@pytest.fixture
def startup_api(tmp_path, monkeypatch):
    import main
    monkeypatch.setenv('SOUNDBOARD_MODELS_DIR', str(tmp_path / 'models'))
    monkeypatch.setenv('SOUNDBOARD_SEPARADOS_ROOT', str(tmp_path / 'videos'))
    devices = {'outputs': [{'id': 3, 'name': 'Monitor', 'is_cable': False}],
               'inputs': [], 'default_output_id': 3, 'default_input_id': None,
               'detected_cable_id': None}
    monkeypatch.setattr(main.AudioEngine, 'list_devices', lambda self: devices)
    api = main.SoundboardAPI(str(tmp_path / 'data'), start_background_services=False)
    yield api
    api._analysis_queue.close()
    api._sequence_runner.close()
    api._audio.shutdown()
    api._store.close()


def test_bootstrap_delivers_library_without_repeating_devices_or_running_diagnostics(startup_api, monkeypatch):
    import main
    api = startup_api
    def forbidden(*args, **kwargs):
        pytest.fail('Bootstrap não pode aguardar sondagens ou filesystem de modelos')
    monkeypatch.setattr(api._audio, 'list_devices', forbidden)
    monkeypatch.setattr(main.doctor, 'snapshot', forbidden)
    class UnavailablePath:
        exists = forbidden
    monkeypatch.setattr(api._ai, 'emb_model_path', UnavailablePath())
    monkeypatch.setattr(api._ai, 'ref_voices_path', UnavailablePath())
    result = api.get_bootstrap_state()
    assert result['startup_details_pending'] is True
    assert result['environment'] is None
    assert result['has_titanet'] is None
    assert result['discovered_separados'] == []
    assert result['settings']['monitor_device_id'] == result['devices']['outputs'][0]['id'] == 3
    result['devices']['outputs'][0]['name'] = 'Mutated'
    result['settings']['monitor_device_id'] = 99
    assert api.get_bootstrap_state()['devices']['outputs'][0]['name'] == 'Monitor'
    assert api._store.snapshot()['settings']['monitor_device_id'] == 3


def test_startup_details_enrich_without_device_refresh_and_keep_doctor_cache(startup_api, monkeypatch):
    import main
    api = startup_api
    main.doctor.invalidate_cache()
    calls = []
    for check_id, label in main.doctor.CHECK_ORDER:
        def probe(cfg, check_id=check_id, label=label):
            calls.append(check_id)
            return main.doctor.CheckResult(check_id, label, 'ok')
        monkeypatch.setattr(main.doctor, '_check_' + check_id, probe)
    manifest = api._config.separados_root / 'synthetic' / 'separados' / 'mapa_de_falas.json'
    manifest.parent.mkdir(parents=True)
    manifest.write_text('[]', encoding='utf-8')
    monkeypatch.setattr(api._audio, 'list_devices', lambda: pytest.fail('Detalhes não enumeram áudio'))
    try:
        first = api.get_startup_details()
        second = api.get_startup_details()
        assert first['environment']['incomplete'] == second['environment']['incomplete'] == 0
        assert len(calls) == len(main.doctor.CHECK_ORDER)
        assert first['startup_details_pending'] is False
        assert first['has_titanet'] is False
        assert first['discovered_separados'][0]['path'] == str(manifest.parent.resolve())
    finally:
        main.doctor.invalidate_cache()


def test_missing_discovery_and_failed_doctor_do_not_block_bootstrap(startup_api, monkeypatch):
    import main
    def fail(cfg):
        raise RuntimeError('synthetic probe failure')
    monkeypatch.setattr(main.doctor, 'snapshot', fail)
    assert startup_api.get_bootstrap_state()['clips'] == []
    details = startup_api.get_startup_details()
    assert details['environment'] is None
    assert details['startup_details_pending'] is False
    assert details['startup_details_error']
    assert details['discovered_separados'] == []


def test_full_initial_state_still_refreshes_devices_and_diagnostics(startup_api, monkeypatch):
    import main
    monkeypatch.setattr(startup_api._audio, 'list_devices', lambda: {'outputs': [{'id': 8}], 'inputs': []})
    monkeypatch.setattr(main.doctor, 'snapshot', lambda cfg: {'checks': [], 'capabilities': {}, 'incomplete': 0})
    result = startup_api.get_initial_state()
    assert result['devices']['outputs'][0]['id'] == 8
    assert result['environment']['incomplete'] == 0
    assert result['has_titanet'] is False


@pytest.mark.parametrize('defer_prewarm', [True, False])
def test_prewarm_waits_for_ui_ready_without_delaying_replay_or_hotkeys(tmp_path, monkeypatch, caplog, defer_prewarm):
    import main
    monkeypatch.setenv('SOUNDBOARD_MODELS_DIR', str(tmp_path / 'models'))
    monkeypatch.setenv('SOUNDBOARD_SEPARADOS_ROOT', str(tmp_path / 'videos'))
    calls = []
    warmed = threading.Event()
    monkeypatch.setattr(main.AudioEngine, 'list_devices', lambda self: {'outputs': [], 'inputs': []})
    monkeypatch.setattr(main.AudioEngine, 'start_loopback_capture', lambda self: calls.append('replay'))
    monkeypatch.setattr(main.HotkeyManager, 'refresh', lambda *args: calls.append('hotkeys'))
    monkeypatch.setattr(main.atexit, 'register', lambda *args: None)
    def prewarm(self):
        calls.append('prewarm')
        warmed.set()
    monkeypatch.setattr(main.SoundboardAPI, '_prewarm_ai', prewarm)
    caplog.set_level('INFO')
    api = main.SoundboardAPI(str(tmp_path / 'data'), defer_ai_prewarm=defer_prewarm)
    try:
        if not defer_prewarm:
            assert warmed.wait(2)
        assert calls == ['replay', 'hotkeys'] + ([] if defer_prewarm else ['prewarm'])
        assert not any('UI interativa' in record.message for record in caplog.records)
        assert api.notify_ui_ready()['ok']
        assert warmed.wait(2)
        assert api.notify_ui_ready()['ok']
        assert calls == ['replay', 'hotkeys', 'prewarm']
        assert sum('UI interativa' in record.message for record in caplog.records) == 1
    finally:
        api._analysis_queue.close()
        api._sequence_runner.close()
        api._audio.shutdown()
        api._store.close()


def test_ui_ready_does_not_start_services_when_disabled(startup_api, monkeypatch):
    monkeypatch.setattr(startup_api, '_prewarm_ai', lambda: pytest.fail('Serviços desabilitados'))
    assert startup_api.notify_ui_ready()['ok']
