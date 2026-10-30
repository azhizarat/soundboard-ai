"""Fixture sintética de contratos; ruído e predições não medem qualidade de fala."""
import argparse
import json
from pathlib import Path
import numpy as np
import soundfile as sf


def generate(folder):
    folder = Path(folder)
    folder.mkdir(parents=True,exist_ok=True)
    data = {'synthetic':True,'configurations':[
        {'id':'baseline','parameters':{}},
        {'id':'speech_context','parameters':{'speech_windows':True,'context_seconds':.2}},
        {'id':'conservative_vad','parameters':{'vad_filter':True}},
        {'id':'confirmed_vocabulary','parameters':{'hotwords':'TitaNet'}},
        {'id':'quality_model','parameters':{'model':'small','beam_size':5}}],
        'enrollment':[],'calibration':[],'test':[]}
    for index, partition in enumerate(('enrollment','calibration','test')):
        for person in range(2):
            seed=index*2+person
            path=folder/f'{partition}-{person}.wav'
            sf.write(path,np.random.default_rng(seed).normal(0,.05,48000),48000,subtype='FLOAT')
            data[partition].append({'id':f'{partition}-{person}','capture_id':f'capture-{seed}',
                'session_id':f'session-{seed}','source_path':path.name,'interval':[0,48000],
                'speech_windows':[[4800,38400]], 'extractor_version':'synthetic-vector/1',
                'embedding':[1.,.1] if person==0 else [.1,1.],
                'labels':{'text':'ola mundo','speaker_id':f'p{person}',
                          'words':[{'text':'ola','start_sample':4800,'end_sample':14400},
                                   {'text':'mundo','start_sample':14400,'end_sample':38400}]},
                'predictions':{c['id']:{'text':'ola mundo' if person==0 else 'ola',
                                       'speaker_id':f'p{person}' if person==0 else '',
                                       'words':[],'latency_ms':1.} for c in data['configurations']}})
    path=folder/'manifest.json'
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    return path


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',required=True,type=Path)
    print(generate(parser.parse_args().output_dir))
