import importlib.util
import numpy as np


def match(*args, **kwargs):
    assert importlib.util.find_spec('speaker_matching'), 'Decisão vocal com margem ausente'
    from speaker_matching import match_embedding
    return match_embedding(*args, **kwargs)


def test_new_person_unknown_and_confused_pair_ambiguous():
    assert match([0, 1], {'a': [1, 0]})['status'] == 'unknown'
    result = match([1, 0], {'a': [1, 0], 'b': [.999, .01]})
    assert result['status'] == 'ambiguous' and result['speaker_id'] == ''
    assert len(result['candidates']) == 2


def test_threshold_margin_are_recorded_and_similarity_is_not_probability():
    result = match([1, 0], {'a': [1, 0], 'b': [0, 1]}, threshold=.7, margin=.2)
    assert result['status'] == 'identified' and result['speaker_id'] == 'a'
    assert result['parameters'] == {'threshold': .7, 'margin': .2}
    assert 'confidence' not in result


def test_incompatible_nonfinite_and_zero_embeddings_abstain():
    for vector in ([0, 0], [np.nan, 1], [1, 2, 3]):
        assert match(vector, {'a': [1, 0]})['status'] == 'unknown'


def test_analyzer_unknown_clusters_are_local_to_each_capture(tmp_path, monkeypatch):
    from types import SimpleNamespace as NS
    from ai_analyzer import AIAnalyzer
    analyzer = AIAnalyzer(str(tmp_path))
    analyzer._diarizer = NS(process=lambda audio: NS(sort_by_start_time=lambda: [NS(start=0, end=1, speaker=0)]))
    monkeypatch.setattr(analyzer, 'compute_embedding', lambda audio: np.array([1., 0.]))
    first = analyzer.diarize_and_identify(np.ones(16000), transcribe_segments=False)[0]
    second = analyzer.diarize_and_identify(np.ones(16000), transcribe_segments=False)[0]
    assert first['speaker_id'] == ''
    assert first['identity']['status'] == 'unknown'
    assert first['local_cluster_id'] != second['local_cluster_id']


def test_analyzer_known_confused_pair_never_forces_global_assignment(tmp_path, monkeypatch):
    from types import SimpleNamespace as NS
    from ai_analyzer import AIAnalyzer
    analyzer = AIAnalyzer(str(tmp_path))
    analyzer.voice_profiles = NS(match=lambda emb, version: match(emb, {'a':[1,0], 'b':[1,.001]}))
    analyzer._diarizer = NS(process=lambda audio: NS(sort_by_start_time=lambda: [NS(start=0, end=1, speaker=0)]))
    monkeypatch.setattr(analyzer, 'compute_embedding', lambda audio: np.array([1., 0.]))
    result = analyzer.diarize_and_identify(np.ones(16000), transcribe_segments=False)[0]
    assert result['speaker_id'] == '' and result['identity']['status'] == 'ambiguous'
