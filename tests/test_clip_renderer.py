import importlib
import importlib.util
import hashlib
import numpy as np
import soundfile as sf
import pytest


def renderer():
    assert importlib.util.find_spec('clip_renderer'), 'Renderizador compartilhado ausente'
    return importlib.import_module('clip_renderer')


def fixture(tmp_path):
    path = tmp_path / 'source.wav'
    sf.write(path, np.full((48000, 2), .1, dtype=np.float32), 48000, subtype='FLOAT')
    source = {'id': 'source', 'file_path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'total_samples': 48000}
    revision = {'id': 'revision', 'source_id': 'source', 'source_hash': source['sha256'], 'spans': [[0, 12000], [24000, 48000]], 'gain_db': 6.020599913, 'normalization_gain': 2.0, 'fade_samples': 48}
    return path, source, revision


def test_frozen_gain_fades_length_hash_and_original(tmp_path):
    module = renderer()
    path, source, revision = fixture(tmp_path)
    before = path.read_bytes()
    data = module.render_revision(source, revision)
    assert data.shape == (36000, 2)
    assert data[1000, 0] == pytest.approx(.4)
    assert data[0, 0] == 0 and data[11999, 0] == 0 and data[12000, 0] == 0
    assert before == path.read_bytes()
    first = module.render_key(source, revision)
    assert first != module.render_key(source, {**revision, 'gain_db': 0})
    sf.write(path, np.zeros((48000, 2)), 48000)
    with pytest.raises(ValueError, match='alterad'):
        module.render_revision(source, revision)


def test_content_address_cannot_accept_modified_existing_source(tmp_path):
    module = renderer()
    original, _, _ = fixture(tmp_path)
    source = module.preserve_source(original, tmp_path / 'sources')
    sf.write(source['file_path'], np.zeros((48000, 2)), 48000)
    with pytest.raises(ValueError, match='alterad'):
        module.preserve_source(original, tmp_path / 'sources')
