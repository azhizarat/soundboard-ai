import copy
import sys
import types

import pytest

from hotkeys import HotkeyManager
from test_editor_api import editor
from test_sequence_runner import Clock
from test_sound_sequences import document, play
from test_mixer_schedule import SETTINGS


def saved(editor):
    api, ident = editor
    api.save_clip_edits({'id': ident})
    clip = copy.deepcopy(api._store.get_clip(ident))
    assert hasattr(api, 'save_sequence'), 'Fachada de sequências ausente'
    return api, clip


def test_api_save_listing_preview_and_explicit_reference_update(editor, monkeypatch):
    api, clip = saved(editor)
    result = api.save_sequence(document(clip))
    assert result['ok'] and result['document']['revision'] == 1
    listed = api.get_sequences()
    assert listed['sequences'][0]['document'] == result['document']
    assert any(choice['id'] == clip['id'] for choice in listed['clips'])
    calls = []
    monkeypatch.setattr(api._sequence_runner, 'start', lambda *args, **kwargs: calls.append((args, kwargs)) or {'state': 'playing'})
    assert api.preview_sequence(result['document'], 'one')['ok']
    assert calls[0][1]['monitor_only'] is True
    assert calls[0][0][0]['blocks'] == result['document']['blocks']
    api._store.begin_edit(clip['id'])
    api._store.update_edit(clip['id'], {'spans': [[48000, 96000]]})
    changed = api._store.commit_edit(clip['id'])
    assert api.get_sequences()['sequences'][0]['new_revisions'] == ['one']
    assert api.pin_sequence_clip(clip['id'])['occurrence']['revision_id'] == changed['revision_id']
    assert api._store.get_sequence('dialogue')['blocks'][0]['revision_id'] == clip['revision_id']
    deleted = api.delete_clip(clip['id'], True)
    assert not deleted['ok'] and 'Diálogo' in deleted['error']
    assert api.delete_sequence('dialogue')['ok']
    api._sequence_runner.close()


def test_api_restart_conflict_no_resume_and_legacy_explained(editor, monkeypatch):
    api, clip = saved(editor)
    api.save_sequence(document(clip, [{'id': 'manual', 'type': 'manual'}, play(clip)]))
    from sequence_runner import SequenceRunner
    api._sequence_runner.close()
    api._sequence_runner = SequenceRunner(api._store, api._audio, clock=Clock(), background=False)
    monkeypatch.setattr(api._audio, 'playback_capabilities', lambda: {'groups': False, 'scheduled_playback': False, 'unavailable_reason': 'Use mixer'})
    assert api.start_sequence('dialogue') == {'ok': False, 'error': 'Use mixer'}
    assert api.get_sequence_status()['status']['state'] == 'idle'
    assert api.get_sequences()['capabilities']['groups'] is False
    api._sequence_runner.close()


def test_hotkey_press_latch_ignores_repeat_and_release_rearms():
    actions = []
    assert 'on_sequence' in __import__('inspect').signature(HotkeyManager).parameters
    manager = HotkeyManager(None, None, None, None, on_sequence=actions.append)
    manager._safe_call = lambda fn, *args: fn(*args)
    assert manager.trigger_sequence('s')
    assert not manager.trigger_sequence('s')
    assert not manager.trigger_sequence('s', repeat=True)
    manager.release_sequence('s')
    assert manager.trigger_sequence('s')
    assert actions == ['s', 's']


def test_refresh_retains_sequences_when_clips_change_and_uses_release_hook(monkeypatch):
    assert 'sequence_provider' in __import__('inspect').signature(HotkeyManager).parameters
    registrations, releases = [], []
    fake = types.SimpleNamespace(add_hotkey=lambda key, fn, **kwargs: registrations.append((key, fn)) or fn,
        remove_hotkey=lambda _: None, on_release_key=lambda key, fn: releases.append((key, fn)) or fn,
        unhook=lambda _: None)
    monkeypatch.setitem(sys.modules, 'keyboard', fake)
    manager = HotkeyManager(None, None, None, None, on_sequence=lambda _: None,
        sequence_provider=lambda: [{'id': 's', 'hotkey': 'ctrl+f8'}])
    manager.refresh([], {})
    manager.refresh([{'id': 'clip', 'hotkey': 'f9'}], {})
    assert [key for key, _ in registrations].count('ctrl+f8') == 2
    assert [key for key, _ in releases] == ['f8', 'f8']
    manager.clear()


def test_refresh_does_not_rearm_held_sequence_key(monkeypatch):
    calls = []
    fake = types.SimpleNamespace(add_hotkey=lambda *args, **kwargs: 'h', remove_hotkey=lambda _: None,
        on_release_key=lambda *args: 'r', unhook=lambda _: None)
    monkeypatch.setitem(sys.modules, 'keyboard', fake)
    manager = HotkeyManager(None, None, None, None, on_sequence=calls.append,
        sequence_provider=lambda: [{'id': 's', 'hotkey': 'f8'}])
    manager._safe_call = lambda fn, *args: fn(*args)
    manager.trigger_sequence('s')
    manager.refresh([], {})
    assert not manager.trigger_sequence('s'), 'Refresh não equivale a soltar a tecla'
    manager.release_sequence('s')
    assert manager.trigger_sequence('s')
    assert calls == ['s', 's']
    manager.clear()


def test_preview_one_valid_block_while_other_branches_are_still_being_built(editor, monkeypatch):
    api, clip = saved(editor)
    calls = []
    monkeypatch.setattr(api._sequence_runner, 'start', lambda *args, **kwargs: calls.append((args, kwargs)) or {'state': 'playing'})
    draft = document(clip, [play(clip), {'id': 'incomplete', 'type': 'parallel', 'branches': [[], []]}])
    assert not api.validate_sequence(draft)['ok']
    assert api.preview_sequence(draft, 'one')['ok']
    assert calls[0][0][0]['blocks'] == [draft['blocks'][0]]
    assert calls[0][1]['monitor_only'] is True
    assert not api.preview_sequence(draft, 'incomplete')['ok']
    api._sequence_runner.close()
