"""Mede PCM sintético sem modelos, dispositivos, captura ou dados pessoais."""
import argparse
import hashlib
import json
from pathlib import Path
import statistics
import sys
import tempfile
import time
import tracemalloc
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
import soundfile as sf
from audio_engine import ReplayRingBuffer, compute_waveform_peaks
from ai_analyzer import load_audio_mono_16k
from analysis_cache import DECODE_CACHE


def measure(run, repeats=11):
    run()
    times=[]
    for _ in range(repeats):
        start=time.perf_counter(); result=run(); times.append((time.perf_counter()-start)*1000)
    tracemalloc.start(); result=run(); _,peak=tracemalloc.get_traced_memory(); tracemalloc.stop()
    return {'median_ms':round(statistics.median(times),4),'peak_traced_bytes':peak,
            'result_samples_or_bins':len(result)}


def benchmark(work_dir):
    rng=np.random.default_rng(42)
    chunk=rng.uniform(-.1,.1,(4800,2)).astype('float32'); rows=[]
    for seconds in (15,120):
        buffer=ReplayRingBuffer(seconds)
        for _ in range(seconds*10): buffer.append(chunk.copy())
        def baseline():
            with buffer._lock: return np.vstack(list(buffer._chunks))[-144000:]
        assert np.array_equal(baseline(),buffer.snapshot(3))
        for operation,run in (('legacy_full_copy_3s',baseline),('snapshot_3s',lambda:buffer.snapshot(3)),('snapshot_full',buffer.snapshot)):
            rows.append({'operation':operation,'buffer_seconds':seconds,**measure(run)})
    audio=rng.uniform(-.1,.1,(48000*15,2)).astype('float32')
    rows.append({'operation':'waveform_15s_160bins',**measure(lambda:compute_waveform_peaks(audio))})
    with tempfile.TemporaryDirectory(prefix='e6-audio-', dir=Path(work_dir).resolve()) as folder:
        path=Path(folder)/'synthetic.wav'; sf.write(path,audio,48000,subtype='FLOAT')
        def cold():
            DECODE_CACHE.clear()
            return load_audio_mono_16k(str(path))[0]
        rows.append({'operation':'decode_15s_uncached','includes_content_hash':True,**measure(cold)})
        expected=cold()
        rows.append({'operation':'decode_15s_cached','includes_content_hash':True,**measure(lambda:load_audio_mono_16k(str(path))[0])})
        assert np.array_equal(expected,load_audio_mono_16k(str(path))[0])
    return {'synthetic':True,'repeats':11,'physical_audio':False,'rows':rows,
            'limitations':'Warm OS filesystem. tracemalloc excludes native/GPU allocations. No gameplay benchmark.',
            'script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args(); args.output.parent.mkdir(parents=True,exist_ok=True); result=benchmark(args.output.parent)
    args.output.write_text(json.dumps(result,indent=2),encoding='utf-8'); print(json.dumps(result))
