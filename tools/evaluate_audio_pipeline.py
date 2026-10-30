"""Avaliação offline reproduzível. Não treina modelos nem altera perfis do app."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
from pathlib import Path
import re
import sys
import time
import tracemalloc
from collections import Counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
import soundfile as sf
from audio_processing import resample_audio
from speaker_matching import normalized

PARTITIONS = ('enrollment', 'calibration', 'test')


def tokens(text):
    return re.findall(r"\w+", str(text).casefold(), re.UNICODE)


def alignment(reference, hypothesis):
    """Levenshtein com pares iguais, sem tempos inventados para substituições."""
    rows = [[(0, []) for _ in range(len(hypothesis)+1)] for _ in range(len(reference)+1)]
    for i in range(1, len(reference)+1): rows[i][0] = (i, [])
    for j in range(1, len(hypothesis)+1): rows[0][j] = (j, [])
    for i, left in enumerate(reference, 1):
        for j, right in enumerate(hypothesis, 1):
            cost, pairs = rows[i-1][j-1]
            diagonal = (cost + (left != right), pairs + ([(i-1,j-1)] if left == right else []))
            rows[i][j] = min((diagonal, (rows[i-1][j][0]+1, rows[i-1][j][1]),
                              (rows[i][j-1][0]+1, rows[i][j-1][1])), key=lambda v:(v[0],-len(v[1])))
    return rows[-1][-1]


def _overlap_bytes(a, b):
    # Repartições exatas detectáveis mesmo sob IDs novos ou novo container.
    small, large = sorted((a, b), key=len)
    if small and small in large:
        return True
    size = 4096  # 1024 amostras float32 (~21 ms); silêncio puro não prova captura.
    for value, other in ((a,b),(b,a)):
        for part in (value[:size],value[-size:]):
            if len(part) == size and len(set(part)) > 4 and part in other:
                return True
    return False


def validate_manifest(manifest, base):
    if not isinstance(manifest, dict) or not all(isinstance(manifest.get(p), list) for p in PARTITIONS):
        raise ValueError('manifest requires enrollment/calibration/test lists')
    loaded, seen_ids = [], set()
    for partition in PARTITIONS:
        for original in manifest[partition]:
            item = copy.deepcopy(original)
            for name in ('id','capture_id','session_id','source_path'):
                if not isinstance(item.get(name), str) or not item[name].strip():
                    raise ValueError(f'missing {name}')
            if item['id'] in seen_ids: raise ValueError('duplicate entry id')
            seen_ids.add(item['id'])
            path = (Path(base)/item['source_path']).resolve()
            audio, rate = sf.read(path, dtype='float32', always_2d=True)
            if not np.isfinite(audio).all(): raise ValueError('nonfinite source PCM')
            audio = resample_audio(audio.mean(axis=1), rate, 48000)
            interval = item.get('interval')
            if (not isinstance(interval,list) or len(interval)!=2 or
                any(type(v) is not int for v in interval) or not 0 <= interval[0] < interval[1] <= len(audio)):
                raise ValueError('invalid interval: expected PCM48k samples within source')
            raw = np.ascontiguousarray(audio,dtype='<f4').tobytes()
            selected = raw[interval[0]*4:interval[1]*4]
            labels = item.get('labels',{})
            if not isinstance(labels,dict): raise ValueError('invalid labels')
            _validate_words(labels.get('words',[]), interval)
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            pcm_digest = hashlib.sha256(raw).hexdigest()
            item.update(_partition=partition,_path=path,_audio=audio,_raw=raw,_selected=selected,
                        _file_hash=digest,_pcm_hash=pcm_digest)
            for previous in loaded:
                if previous['_partition'] == partition: continue
                shared = [k for k in ('capture_id','session_id','_path','_file_hash','_pcm_hash') if previous[k] == item[k]]
                if shared or _overlap_bytes(selected, previous['_raw']) or _overlap_bytes(previous['_selected'], raw):
                    raise ValueError('partition leakage: ' + ','.join(shared or ['overlapping_pcm']))
            loaded.append(item)
    configurations = manifest.get('configurations', [{'id':'baseline','parameters':{}}])
    if not configurations or len({c.get('id') for c in configurations}) != len(configurations):
        raise ValueError('invalid/duplicate configurations')
    for configuration in configurations:
        if not isinstance(configuration.get('id'),str) or not configuration['id']:
            raise ValueError('configuration id required')
        if not isinstance(configuration.get('parameters',{}),dict): raise ValueError('invalid parameters')
    return loaded, configurations


def _validate_words(words, interval):
    if not isinstance(words,list): raise ValueError('invalid word anchors')
    previous = interval[0]
    for word in words:
        a,b = word.get('start_sample'),word.get('end_sample')
        if not isinstance(word.get('text'),str) or type(a) is not int or type(b) is not int or not previous <= a < b <= interval[1]:
            raise ValueError('invalid word interval/anchor')
        previous = a


def summarize(items, predictions, provenance):
    errors = reference_count = text_items = speaker_total = assigned = correct = abstained = 0
    temporal_errors, latencies, ram = [], [], []
    temporal_total = matched = timing_items = 0
    confusion, pairs = Counter(), Counter()
    failures, missing = Counter(), 0
    for item in items:
        prediction = predictions.get(item['id']) or {}
        if not prediction: missing += 1
        labels = item.get('labels',{})
        if prediction.get('status') in ('failed','model_unavailable','inference_error'):
            failures[prediction.get('error_code',prediction['status'])] += 1
        if 'text' in labels:
            reference = tokens(labels['text'])
            errors += alignment(reference,tokens(prediction.get('text','')))[0]
            reference_count += len(reference)
            text_items += 1
        if 'speaker_id' in labels:
            speaker_total += 1
            truth, guess = labels['speaker_id'] or '<unknown>', prediction.get('speaker_id') or '<abstain>'
            confusion[(truth,guess)] += 1
            if guess == '<abstain>': abstained += 1
            else:
                assigned += 1
                correct += guess == truth
                if guess != truth: pairs[(truth,guess)] += 1
        if labels.get('words'):
            reference = labels['words']; hypothesis = prediction.get('words',[])
            _validate_words(hypothesis,item['interval'])
            timing_items += 1; temporal_total += len(reference)
            _, anchors = alignment([tokens(w['text']) for w in reference], [tokens(w['text']) for w in hypothesis])
            matched += len(anchors)
            for i,j in anchors:
                temporal_errors.extend(abs(reference[i][k]-hypothesis[j][k])/48 for k in ('start_sample','end_sample'))
        for name, target in (('latency_ms',latencies),('peak_python_bytes',ram)):
            value = prediction.get(name)
            if value is not None:
                if not isinstance(value,(int,float)) or not math.isfinite(value) or value < 0:
                    raise ValueError('invalid resource measurement')
                target.append(value)
    matrix = {}
    for (truth,guess), count in sorted(confusion.items()): matrix.setdefault(truth,{})[guess] = count
    return {'items':len(items),'missing_predictions':missing,'failures':dict(failures),
            'wer':{'value':errors/reference_count if reference_count else None,'errors':errors,
                   'reference_words':reference_count,'labeled_items':text_items,'text_source':'raw_asr_before_corrections',
                   'unavailable_reason':None if reference_count else 'no_reference_words'},
            'speaker':{'precision':correct/assigned if assigned else None,'coverage':assigned/speaker_total if speaker_total else None,
                       'accuracy_all':correct/speaker_total if speaker_total else None,'labeled_items':speaker_total,
                       'assigned':assigned,'correct':correct,'abstentions':abstained,'confusion':matrix,
                       'confused_pairs':[{'reference':a,'predicted':b,'count':v} for (a,b),v in pairs.most_common()],
                       'unavailable_reason':None if speaker_total else 'no_speaker_labels'},
            'timing':{'mean_absolute_error_ms':float(np.mean(temporal_errors)) if temporal_errors else None,
                      'p95_absolute_error_ms':float(np.percentile(temporal_errors,95)) if temporal_errors else None,
                      'matched_words':matched,'reference_words':temporal_total,'labeled_items':timing_items,
                      'coverage':matched/temporal_total if temporal_total else None,
                      'unavailable_reason':None if temporal_errors else 'no_matching_word_anchors'},
            'latency':{'mean_ms':float(np.mean(latencies)) if latencies else None,
                       'p95_ms':float(np.percentile(latencies,95)) if latencies else None,
                       'measured_items':len(latencies),'provenance':provenance,
                       'unavailable_reason':None if latencies else 'not_measured'},
            'resources':{'peak_python_bytes':max(ram) if ram else None,'measured_items':len(ram),'provenance':provenance,
                         'ram_rss_bytes':None,'vram_bytes':None,'game_frame_time_ms':None,
                         'unavailable_reason':'RSS/VRAM/gameplay not measured; tracemalloc excludes native allocations'}}


def _local_prediction(item, configuration, analyzer):
    parameters = configuration.get('parameters',{})
    allowed = {'model','device','beam_size','language','hotwords','initial_prompt','vad_filter','speech_windows','context_seconds'}
    if set(parameters)-allowed: raise ValueError('unsupported local parameters')
    analyzer.configure_transcription({**{'model':'base','device':'cpu','beam_size':3,'language':'pt',
                                         'hotwords':'','initial_prompt':'','vad_filter':False},
                                      **{k:v for k,v in parameters.items() if k not in ('speech_windows','context_seconds')}})
    a,b = item['interval']
    windows = item.get('speech_windows',[[a,b]]) if parameters.get('speech_windows') else [[a,b]]
    context = float(parameters.get('context_seconds',0))
    if not math.isfinite(context) or not 0 <= context <= 5: raise ValueError('invalid context_seconds')
    padded = []
    for lo,hi in windows:
        if type(lo) is not int or type(hi) is not int or not a <= lo < hi <= b: raise ValueError('invalid speech_windows')
        lo,hi = max(a,lo-round(context*48000)),min(b,hi+round(context*48000))
        padded.append([lo,hi])
    ranges = []
    for lo,hi in sorted(padded):
        if ranges and lo <= ranges[-1][1]: ranges[-1][1] = max(hi,ranges[-1][1])
        else: ranges.append([lo,hi])
    words,texts,errors = [],[],[]
    analyzer.cache.clear()  # Cada alternativa mede inferência fria no cache de resultados.
    tracemalloc.start(); started = time.perf_counter()
    try:
        for lo,hi in ranges:
            audio = resample_audio(item['_audio'][lo:hi],48000,16000)
            result = analyzer.transcribe_temporal(audio,item['id'],item['capture_id'],[lo,hi])
            words.extend(result['words']); texts.append(result['text'])
            if result['status'] == 'failed': errors.append(result['error']['code'])
        elapsed = (time.perf_counter()-started)*1000
        _,peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return {'text':' '.join(texts),'words':words,'status':'failed' if errors else 'ready',
            'error_code':','.join(errors) if errors else None,'latency_ms':elapsed,'peak_python_bytes':peak,
            'runtime':copy.deepcopy(analyzer.transcription_runtime), 'speaker_id':''}


def _voice_comparison(items, manifest):
    labeled = [i for i in items if 'speaker_id' in i.get('labels',{})]
    available = [i for i in labeled if i.get('embedding') is not None]
    by_part = {p:[i for i in labeled if i['_partition']==p] for p in PARTITIONS}
    availability = {}
    for partition,entries in by_part.items():
        missing = [i['id'] for i in entries if i.get('embedding') is None]
        availability[partition] = {'labeled_items':len(entries),'available':len(entries)-len(missing),
                                   'missing':len(missing),'missing_ids':missing}
    if not all(by_part.values()) or not available:
        return {'status':'unavailable','reason':'independent enrollment/calibration/test embeddings and labels required',
                'embedding_availability':availability}
    versions = {i.get('extractor_version') for i in available}
    dimensions = {len(i['embedding']) for i in available}
    if None in versions or len(versions)!=1 or len(dimensions)!=1 or any(normalized(i['embedding']) is None for i in available):
        raise ValueError('incompatible/invalid embedding provenance')
    bank = {}
    for item in by_part['enrollment']:
        if item['labels']['speaker_id'] and item.get('embedding') is not None:
            bank.setdefault(item['labels']['speaker_id'],[]).append(normalized(item['embedding']))
    if not bank: return {'status':'unavailable','reason':'no known enrollment speaker embeddings',
                         'embedding_availability':availability}
    options = manifest.get('voice_parameters', {'thresholds':[.55,.65,.75], 'margins':[.05,.1]})
    thresholds, margins = options.get('thresholds',[]),options.get('margins',[])
    if not thresholds or not margins or any(not math.isfinite(v) or not -1<=v<=1 for v in thresholds) or any(not math.isfinite(v) or not 0<=v<=2 for v in margins):
        raise ValueError('invalid voice parameter grid')
    def predict(entries,strategy,threshold,margin):
        result = {}
        for item in entries:
            if item.get('embedding') is None:
                result[item['id']] = {'speaker_id':'','status':'failed','error_code':'embedding_unavailable',
                                     'embedding_available':False}
                continue
            vector = normalized(item['embedding']); scores=[]
            for person,vectors in bank.items():
                # Limite igual por pessoa: dois protótipos, sem vantagem por N comparações.
                if strategy == 'centroid': prototypes = [normalized(np.mean(vectors,axis=0))]
                else:
                    first = vectors[0]
                    second = min(vectors,key=lambda v:float(np.dot(first,v)))
                    groups = [[v for v in vectors if np.dot(v,first)>=np.dot(v,second)],
                              [v for v in vectors if np.dot(v,first)<np.dot(v,second)]]
                    prototypes = [normalized(np.mean(group,axis=0)) for group in groups if group]
                scores.append((max((float(np.dot(vector,p)) for p in prototypes if p is not None),default=-1),person))
            scores.sort(reverse=True)
            accepted = scores and scores[0][0]>=threshold and (len(scores)<2 or scores[0][0]-scores[1][0]>=margin)
            result[item['id']] = {'speaker_id':scores[0][1] if accepted else '', 'embedding_available':True}
        return result
    output = {}
    for strategy in ('centroid','two_prototypes'):
        trials=[]
        for threshold in thresholds:
            for margin in margins:
                metrics = summarize(by_part['calibration'],predict(by_part['calibration'],strategy,threshold,margin),'embedding_fixture')['speaker']
                trials.append({'threshold':threshold,'margin':margin,'metrics':metrics})
        # Seleção só na calibração; erro de atribuição pesa 2, abstenção pesa 1.
        best = min(trials,key=lambda t:(2*(t['metrics']['assigned']-t['metrics']['correct'])+t['metrics']['abstentions'], -t['metrics']['correct'],t['threshold'],t['margin']))
        metrics = summarize(by_part['test'],predict(by_part['test'],strategy,best['threshold'],best['margin']),'embedding_fixture')['speaker']
        output[strategy] = {'calibration_trials':trials,'selected':{'threshold':best['threshold'],'margin':best['margin']},'test':metrics}
    return {'status':'synthetic_contract_only' if manifest.get('synthetic',False) else 'evaluated',
            'extractor_version':next(iter(versions)),'selection_objective':'2*wrong_assignments + abstentions',
            'production_changed':False,'strategies':output,'embedding_availability':availability}


def evaluate(manifest, base='.', mode='predictions', models_dir=None):
    if mode not in ('predictions','local'): raise ValueError('invalid evaluation mode')
    items, configurations = validate_manifest(manifest,base)
    analyzer = None
    voice_extraction = {'provenance':'manifest_embeddings','measured_items':0,'latency_ms':None}
    if mode == 'local':
        if not models_dir: raise ValueError('--models-dir required for local evaluation')
        from ai_analyzer import AIAnalyzer
        analyzer = AIAnalyzer(models_dir=str(Path(models_dir).resolve()))
        began = time.perf_counter()
        count = 0
        for item in items:
            if 'speaker_id' not in item.get('labels',{}): continue
            a,b = item['interval']
            embedding = analyzer.compute_embedding(resample_audio(item['_audio'][a:b],48000,16000))
            # Modo local nunca mistura vetor fornecido com extrator instalado.
            item['embedding'] = embedding.tolist() if embedding is not None else None
            item['extractor_version'] = analyzer.extractor_version
            count += 1
        voice_extraction = {'provenance':'local_extractor','measured_items':count,
                            'latency_ms':(time.perf_counter()-began)*1000,'extractor_version':analyzer.extractor_version}
    output = {}
    for configuration in configurations:
        predictions={}
        for item in items:
            predictions[item['id']] = (copy.deepcopy(item.get('predictions',{}).get(configuration['id'],{})) if mode=='predictions'
                                        else _local_prediction(item,configuration,analyzer))
        output[configuration['id']] = {'parameters':configuration.get('parameters',{}),
            **{p:summarize([i for i in items if i['_partition']==p],predictions,
                          'manifest_predictions' if mode=='predictions' else 'local_inference_wall_time') for p in PARTITIONS},
            'runtime':copy.deepcopy(analyzer.transcription_runtime) if analyzer else None}
    return {'schema_version':1,'mode':mode,'synthetic':bool(manifest.get('synthetic',False)),
            'decision_status':'pending_real_labeled_corpus' if manifest.get('synthetic',False) else 'report_only_no_production_change',
            'configurations':output,'voice_comparison':_voice_comparison(items,manifest),'voice_extraction':voice_extraction,
            'sources':[{'id':i['id'],'partition':i['_partition'],'capture_id':i['capture_id'],'session_id':i['session_id'],
                        'source_path':i['source_path'],'interval':i['interval'],'sha256':i['_file_hash'],'pcm_sha256':i['_pcm_hash'],
                        'extractor_version':i.get('extractor_version')} for i in items],
            'limitations':['Exact PCM overlap detection cannot identify re-encoded/filtered/shifted recordings reliably.',
                           'Offline prediction timing/resources are supplied, not measured inference.',
                           'No production model, threshold, voice profile or neural training is changed.']}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--mode',choices=('predictions','local'),default='predictions')
    parser.add_argument('--models-dir',type=Path)
    args=parser.parse_args()
    try:
        manifest=json.loads(args.manifest.read_text(encoding='utf-8'))
        result=evaluate(manifest,args.manifest.parent,args.mode,args.models_dir)
        result['manifest_sha256']=hashlib.sha256(args.manifest.read_bytes()).hexdigest()
        result['evaluator_sha256']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
        print(json.dumps({'ok':True,'output':str(args.output),'mode':args.mode}))
    except (ValueError,OSError,KeyError,TypeError) as exc:
        print(json.dumps({'ok':False,'error_code':'evaluation_invalid','message':str(exc)}),file=sys.stderr)
        return 2
    return 0


if __name__=='__main__': raise SystemExit(main())
