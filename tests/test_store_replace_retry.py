"""Persistência atômica diante de bloqueios transitórios do Windows."""

import json
from pathlib import Path

import pytest

import store as store_module
from store import SoundboardStore


def windows_permission_error(code):
    error = PermissionError('Arquivo temporariamente bloqueado')
    error.winerror = code
    return error


@pytest.fixture
def database(tmp_path):
    database = SoundboardStore(str(tmp_path / 'data'))
    # O worker não participa deste contrato síncrono de persistência.
    database.close()
    database.db_path.write_text('{"version": "old"}', encoding='utf-8')
    database.state = {'version': 'new'}
    database._dirty = True
    database._save_deadline = 123.0
    return database


@pytest.mark.parametrize('code', [5, 32, 33])
def test_transient_replace_keeps_old_json_until_atomic_success(database, monkeypatch, code):
    original_replace = store_module.os.replace
    original_write = Path.write_text
    attempts, delays, writes = [], [], []
    temporary = database.db_path.with_suffix('.json.tmp')
    previous = database.db_path.read_bytes()

    def tracked_write(path, *args, **kwargs):
        if path == temporary:
            writes.append(path)
        return original_write(path, *args, **kwargs)

    def replace(source, destination):
        attempts.append((source, destination))
        assert database.db_path.read_bytes() == previous
        assert json.loads(temporary.read_text(encoding='utf-8')) == {'version': 'new'}
        assert database._dirty is True
        if len(attempts) < 3:
            raise windows_permission_error(code)
        original_replace(source, destination)

    monkeypatch.setattr(Path, 'write_text', tracked_write)
    monkeypatch.setattr(store_module.os, 'replace', replace)
    monkeypatch.setattr(store_module.time, 'sleep', delays.append)

    database.save()

    assert attempts == [(temporary, database.db_path)] * 3
    assert writes == [temporary]
    assert delays == [.02, .04]
    assert json.loads(database.db_path.read_text(encoding='utf-8')) == database.state
    assert not temporary.exists()
    assert database._dirty is False
    assert database._save_deadline == 0


@pytest.mark.parametrize('code', [5, 32, 33])
def test_permanent_replace_failure_is_bounded_and_preserves_dirty_json(database, monkeypatch, code):
    error = windows_permission_error(code)
    attempts, delays = [], []
    previous = database.db_path.read_bytes()

    def replace(source, destination):
        attempts.append((source, destination))
        raise error

    monkeypatch.setattr(store_module.os, 'replace', replace)
    monkeypatch.setattr(store_module.time, 'sleep', delays.append)

    with pytest.raises(PermissionError) as caught:
        database.save()

    assert caught.value is error
    assert len(attempts) == 3
    assert delays == [.02, .04]
    assert database.db_path.read_bytes() == previous
    assert not database.db_path.with_suffix('.json.tmp').exists()
    assert database._dirty is True
    assert database._save_deadline == 123.0


@pytest.mark.parametrize('error', [
    OSError('Disco cheio'),
    PermissionError('Sem código Windows'),
    windows_permission_error(13),
])
def test_other_replace_errors_propagate_without_retry(database, monkeypatch, error):
    attempts, delays = [], []
    previous = database.db_path.read_bytes()

    def replace(source, destination):
        attempts.append((source, destination))
        raise error

    monkeypatch.setattr(store_module.os, 'replace', replace)
    monkeypatch.setattr(store_module.time, 'sleep', delays.append)

    with pytest.raises(type(error)) as caught:
        database.save()

    assert caught.value is error
    assert len(attempts) == 1
    assert delays == []
    assert database.db_path.read_bytes() == previous
    assert not database.db_path.with_suffix('.json.tmp').exists()
    assert database._dirty is True


def test_temporary_write_permission_error_is_not_retried(database, monkeypatch):
    error = windows_permission_error(5)
    writes, delays = [], []
    previous = database.db_path.read_bytes()

    def write(path, *args, **kwargs):
        writes.append(path)
        raise error

    monkeypatch.setattr(Path, 'write_text', write)
    monkeypatch.setattr(store_module.time, 'sleep', delays.append)

    with pytest.raises(PermissionError) as caught:
        database.save()

    assert caught.value is error
    assert writes == [database.db_path.with_suffix('.json.tmp')]
    assert delays == []
    assert database.db_path.read_bytes() == previous
    assert database._dirty is True
