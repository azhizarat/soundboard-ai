import importlib.util
from types import SimpleNamespace as NS


def model():
    assert importlib.util.find_spec('transcript_model'), 'Contrato temporal ausente'
    import transcript_model
    return transcript_model


def test_real_word_times_are_source_samples_and_stable():
    m = model()
    seg = NS(start=0, end=1, text='olá mundo', words=[NS(start=.1, end=.4, word='olá'), NS(start=.6, end=.9, word='mundo')])
    result = m.from_segments([seg], 'a', 's', [48000, 96000], {})
    assert [(w['start_sample'], w['end_sample']) for w in result['words']] == [(52800, 67200), (76800, 91200)]
    assert result == m.from_segments([seg], 'a', 's', [48000, 96000], {})
    assert len({w['id'] for w in result['words']}) == 2


def test_projection_excludes_removed_words_and_maps_boundaries():
    m = model()
    result = {'words': [{'id': 'a', 'text': 'um', 'start_sample': 0, 'end_sample': 100}, {'id': 'b', 'text': 'dois', 'start_sample': 200, 'end_sample': 300}], 'segments': [], 'corrections': []}
    projection = m.project(result, [[50, 150], [250, 350]])
    assert projection['text'] == 'um dois'
    assert [(w['output_start_sample'], w['output_end_sample']) for w in projection['words']] == [(0, 50), (100, 150)]
    assert m.project(result, [[100, 200]])['text'] == ''


def test_legacy_text_is_unaligned_and_correction_anchors_whole_block():
    m = model()
    old = m.legacy('texto antigo')
    assert old['alignment'] == 'unaligned' and old['words'] == []
    assert m.project(old, [[0, 100]])['text'] == 'texto antigo'
    original = {'words': [{'id': 'w1', 'text': 'a', 'start_sample': 10, 'end_sample': 20}, {'id': 'w2', 'text': 'b', 'start_sample': 30, 'end_sample': 60}], 'segments': [], 'corrections': []}
    corrected = m.correct(original, ['w1', 'w2'], 'uma palavra nova')
    block = corrected['corrections'][0]
    assert (block['start_sample'], block['end_sample']) == (10, 60)
    assert block['alignment'] == 'block' and 'words' not in block
    assert m.project(corrected, [[0, 100]])['text'] == 'uma palavra nova'


def test_selected_replacement_preserves_outside_and_requires_explicit_correction_override():
    m = model()
    old = {'words': [{'id': 'a', 'text': 'antes', 'start_sample': 0, 'end_sample': 10}, {'id': 'b', 'text': 'alvo', 'start_sample': 20, 'end_sample': 30}], 'segments': [], 'corrections': []}
    corrected = m.correct(old, ['b'], 'corrigido')
    new = {'words': [{'id': 'c', 'text': 'novo', 'start_sample': 20, 'end_sample': 30}], 'segments': [], 'corrections': []}
    import pytest
    with pytest.raises(ValueError, match='aceite'):
        m.replace_interval(corrected, new, [20, 30])
    replaced = m.replace_interval(corrected, new, [20, 30], accept_corrections=True)
    assert m.project(replaced, [[0, 40]])['text'] == 'antes novo'


def test_correction_block_preserves_gaps_in_current_montage():
    m = model()
    old = {'words': [{'id':'a','text':'um','start_sample':0,'end_sample':10}, {'id':'b','text':'dois','start_sample':30,'end_sample':40}], 'segments':[], 'corrections':[]}
    changed = m.correct(old, ['a','b'], 'juntos', spans=[[0,10],[30,40]])
    assert changed['corrections'][0]['anchors'] == [[0,10],[30,40]]
    assert m.project(changed, [[10,30]])['text'] == ''


def test_replacement_rejects_partial_word_or_corrected_block_without_mutation():
    import copy
    import pytest
    m = model()
    word = {'words':[{'id':'w','text':'inteira','start_sample':0,'end_sample':100}], 'segments':[], 'corrections':[]}
    corrected = m.correct({'words':[{'id':'a','text':'um','start_sample':0,'end_sample':40},
                                    {'id':'b','text':'dois','start_sample':60,'end_sample':100}],
                           'segments':[], 'corrections':[]}, ['a','b'], 'correção inteira')
    for previous in [word, corrected]:
        before = copy.deepcopy(previous)
        with pytest.raises(ValueError, match='Amplie a seleção'):
            m.replace_interval(previous, {'words':[],'segments':[],'corrections':[]}, [40,60], accept_corrections=True)
        assert previous == before


def test_replacement_preserves_aligned_segment_text_outside_target():
    m = model()
    old = {'words':[{'id':str(i),'text':text,'start_sample':i*10,'end_sample':(i+1)*10}
                    for i,text in enumerate(['antes','alvo','depois'])],
           'segments':[{'id':'s','text':'antes alvo depois','start_sample':0,'end_sample':30}], 'corrections':[]}
    updated = m.replace_interval(old, {'words':[],'segments':[],'corrections':[]}, [10,20])
    assert [(s['text'],s['start_sample'],s['end_sample']) for s in updated['segments']] == [('antes',0,10),('depois',20,30)]


def test_replacement_keeps_external_correction_anchors_and_trims_context_segment_text():
    m = model()
    old = {'words':[{'id':'a','text':'um','start_sample':0,'end_sample':10},
                    {'id':'b','text':'dois','start_sample':30,'end_sample':40}], 'segments':[], 'corrections':[]}
    old = m.correct(old,['a','b'],'bloco externo',spans=[[0,10],[30,40]])
    proposed = {'words':[{'id':'context','text':'contexto','start_sample':40,'end_sample':50},
                         {'id':'target','text':'alvo','start_sample':50,'end_sample':60}],
                'segments':[{'id':'new','text':'contexto alvo','start_sample':40,'end_sample':60}], 'corrections':[]}
    updated = m.replace_interval(old,proposed,[50,60],accept_corrections=True)
    assert updated['corrections'] == old['corrections']
    assert updated['segments'][0]['text'] == 'alvo'
    assert m.project(updated,[[0,60]])['text'] == 'bloco externo alvo'


def mixed_result():
    return model().from_segments([
        NS(start=0,end=1,text='antes',words=[]),
        NS(start=1,end=2,text='alvo',words=[NS(start=1,end=2,word='alvo')]),
        NS(start=2,end=3,text='depois',words=None)], 'mixed','source',[0,144000],{})


def test_mixed_alignment_preserves_unaligned_segments_in_full_and_external_projections():
    m = model()
    result = mixed_result()
    assert result['alignment'] == 'partial'
    assert len(result['words']) == 1
    assert m.project(result,[[0,144000]])['text'] == 'antes alvo depois'
    assert m.project(result,[[0,48000],[96000,144000]])['text'] == 'antes depois'


def test_mixed_alignment_rejects_partial_unaligned_segment_even_with_other_words():
    import copy
    import pytest
    m = model()
    previous = mixed_result()
    before = copy.deepcopy(previous)
    with pytest.raises(ValueError,match='Amplie a seleção'):
        m.replace_interval(previous,{'words':[],'segments':[],'corrections':[]},[9600,38400])
    assert previous == before


def test_segment_only_analysis_keeps_external_text_after_one_segment_gets_words():
    m = model()
    previous = m.from_segments([NS(start=i,end=i+1,text=text,words=None) for i,text in enumerate(['antes','alvo','depois'])], 'old','source',[0,144000],{})
    proposed = m.from_segments([NS(start=0,end=1,text='novo',words=[NS(start=0,end=1,word='novo')])], 'new','source',[48000,96000],{})
    updated = m.replace_interval(previous,proposed,[48000,96000])
    assert m.project(updated,[[0,144000]])['text'] == 'antes novo depois'
    assert updated['alignment'] == 'partial'
    assert [w['text'] for w in updated['words']] == ['novo']


def test_proposed_mixed_context_cannot_split_its_unaligned_segment():
    import pytest
    m = model()
    with pytest.raises(ValueError,match='Amplie a seleção'):
        m.replace_interval({'words':[],'segments':[],'corrections':[]},mixed_result(),[9600,38400])
