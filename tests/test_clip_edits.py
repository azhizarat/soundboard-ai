import importlib
import importlib.util
import pytest


def edits():
    assert importlib.util.find_spec('clip_edits'), 'Contrato de edição ausente'
    return importlib.import_module('clip_edits')


def test_half_open_mapping_and_rejection():
    module = edits()
    spans = [(0, 96000), (192000, 288000)]
    assert module.validate_spans(spans, 288000) == spans
    assert [module.output_to_source_sample(spans, i) for i in (95999, 96000, 191999)] == [95999, 192000, 287999]
    for i in (-1, 192000, 1.5, True):
        with pytest.raises(ValueError):
            module.output_to_source_sample(spans, i)
    for invalid in ([], [(0, 0)], [(1, 4), (3, 8)], [(0, 288001)], [(1.5, 4)], [(True, 5)]):
        with pytest.raises(ValueError):
            module.validate_spans(invalid, 288000)
