"""Packs v2: grafo portátil, mídia verificada e publicação em uma transação."""
import copy
import hashlib
import io
import json
import math
import re
import tempfile
import threading
import time
import uuid
import zipfile
from pathlib import Path

import numpy as np
import soundfile as sf

import packs
from audio_variants import PROCESSOR_VERSION, output_samples, project, validate as validate_variant
from clip_edits import CONVERSION_VERSION, validate_spans
from clip_renderer import RENDER_VERSION, render_revision
from sound_sequences import validate_document, walk_blocks
from sound_collections import CollectionStore, binding_entries, normalize_binding, target_references

MAX_NODES = 4096
MAX_FILES = 4096
CLIP_FIELDS = ('id','title','emoji','category','color','hotkey','volume','favorite','tags','acoustic_mood',
               'speaker_id','speaker_ids','transcript','source_id','revision_id','duration','trim_start','trim_end')
REVISION_FIELDS = ('id','source_id','source_hash','conversion_version','render_version','spans','gain_db',
                   'normalization_gain','fade_samples','duration','created_at','variant','variant_processor','variant_origin')
SOURCE_FIELDS = ('id','sha256','original_hash','conversion_version','sample_rate','total_samples','duration','available')


def _pick(value, fields):
    return {key:copy.deepcopy(value[key]) for key in fields if key in value}


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def _digest(value):
    return hashlib.sha256(_canonical(value)).hexdigest()


def _wav(data):
    buffer = io.BytesIO()
    sf.write(buffer, data, 48000, format='WAV', subtype='FLOAT')
    raw=bytearray(buffer.getvalue())
    # libsndfile escreve a hora no chunk PEAK; ela não faz parte do PCM/receita.
    offset=12
    while offset+8<=len(raw):
        size=int.from_bytes(raw[offset+4:offset+8],'little')
        if raw[offset:offset+4]==b'PEAK' and size>=8:
            raw[offset+12:offset+16]=b'\0'*4
        offset+=8+size+(size%2)
    return bytes(raw)


def _source_record(ident, data):
    raw = _wav(data)
    source = {'id': ident, 'file': 'audio/'+ident+'.wav', 'sha256': hashlib.sha256(raw).hexdigest(),
              'conversion_version': CONVERSION_VERSION, 'sample_rate': 48000, 'total_samples': len(data),
              'duration': len(data)/48000, 'available': True}
    return source, raw


def _compact_text(clip, revision, interval):
    """Sem marca temporal não exporta texto que possa conter uma parte removida."""
    temporal = clip.get('transcript_analysis')
    if temporal:
        projection = project(temporal, revision['spans'], revision.get('variant', {}))
        a,b = interval
        replaced={word for item in temporal.get('corrections',[]) for word in item.get('word_ids',[])}
        items=[w for w in projection.get('words',[]) if w['id'] not in replaced]+projection.get('corrections',[])
        # Segmentos sem palavras são indivisíveis: corte parcial omite seu texto.
        items += [s for s in projection.get('segments',[]) if s['id'] not in replaced and not any(
            w['start_sample']<s['end_sample'] and w['end_sample']>s['start_sample'] for w in temporal.get('words',[]))]
        original={item['id']:item for key in ('words','segments','corrections') for item in temporal.get(key,[])}
        def retained(item):
            unit=original[item['id']]
            anchors=unit.get('anchors') or [[unit['start_sample'],unit['end_sample']]]
            return all(sum(max(0,min(end,hi)-max(start,lo)) for lo,hi in revision['spans'])==end-start for start,end in anchors)
        safe=[item for item in items if retained(item) and a<=item['output_start_sample'] and item['output_end_sample']<=b]
        # Todas as partes de uma correção devem caber no intervalo exportado.
        safe=[item for item in safe if all(a<=part['output_start_sample'] and part['output_end_sample']<=b for part in items if part['id']==item['id'])]
        seen=set(); text=[]
        for item in sorted(safe,key=lambda value:value['output_start_sample']):
            if item['id'] not in seen: text.append(str(item.get('text') or '').strip()); seen.add(item['id'])
        return ' '.join(text).strip()
    return ''


def build_graph(store, clip_ids=None, collection_ids=None, sequence_ids=None, mode='compact'):
    if mode not in ('compact','editable'):
        raise packs.PackError('Escolha compacto ou backup editável')
    snap = store.snapshot()
    all_clips = {c['id']:c for c in snap['clips'] if not c.get('pending')}
    full = clip_ids is None and collection_ids is None and sequence_ids is None
    direct = set(all_clips if full else clip_ids or [])
    collections = set(snap['collections'] if full else collection_ids or [])
    sequences = set(snap['sequences'] if full else sequence_ids or [])
    if direct - all_clips.keys() or collections - snap['collections'].keys() or sequences - snap['sequences'].keys():
        raise packs.PackError('Seleção contém referência ausente')
    for ident in collections:
        for pad in snap['collections'][ident]['pads']:
            for ref in target_references(pad['target']):
                (direct if ref['type'] == 'clip' else sequences).add(ref['id'])
    needed = set(direct)
    for ident in sequences:
        needed.update(block['clip_id'] for block in walk_blocks(snap['sequences'][ident]['blocks']) if block['type']=='play')
    graph = {'clips': [], 'sources': {}, 'revisions': {}, 'speakers': {},
             'sequences': {k:copy.deepcopy(snap['sequences'][k]) for k in sorted(sequences)},
             'collections': {k:copy.deepcopy(snap['collections'][k]) for k in sorted(collections)}}
    media, legacy = {}, {}

    def context(ident, revision_id=None):
        clip = all_clips[ident]
        revision = snap['revisions'].get(revision_id or clip.get('revision_id'))
        if revision:
            return clip, snap['sources'][revision['source_id']], revision
        if ident not in legacy:
            from audio_processing import resample_audio
            data, rate = sf.read(clip['file_path'], dtype='float32', always_2d=True)
            data = resample_audio(data, rate, 48000)
            data = np.repeat(data, 2, axis=1) if data.shape[1] == 1 else data[:,:2]
            source, raw = _source_record('legacy-'+ident, data)
            start = max(0, round(float(clip.get('trim_start',0))*48000))
            end = min(len(data), round(float(clip.get('trim_end') or len(data)/48000)*48000))
            validate_spans([[start,end]],len(data))
            revision = {'id':'legacy-revision-'+ident, 'source_id':source['id'], 'source_hash':source['sha256'],
                'spans':[[start,end]], 'gain_db':float(clip.get('loudness_gain_db',0)),
                'normalization_gain':1.0, 'fade_samples':0, 'conversion_version':CONVERSION_VERSION,'render_version':RENDER_VERSION}
            legacy[ident] = (source,revision,raw,data)
        source,revision,_,_ = legacy[ident]
        return clip,source,revision

    def render(ident, source, revision):
        if ident in legacy:
            data = legacy[ident][3]
            return np.concatenate([data[a:b] for a,b in revision['spans']]) * np.float32(10**(revision['gain_db']/20))
        return render_revision(source, revision, getattr(store,'ffmpeg_path',None))

    compact_nodes = {}
    def compact(ident, revision_id=None, interval=None, root=False):
        clip,source,revision = context(ident, revision_id)
        total = output_samples(sum(b-a for a,b in revision['spans']),revision.get('variant',{}))
        interval = interval if interval is not None else [0,total]
        validate_spans([interval],total)
        key = (ident, revision['id'], tuple(interval), root)
        if key in compact_nodes:
            return compact_nodes[key]
        data = render(ident,source,revision)[interval[0]:interval[1]]
        if not len(data) or not np.isfinite(data).all():
            raise packs.PackError('Resultado de áudio inválido')
        node_id = ident if root else 'occ-'+_digest(list(key))[:24]
        source_id = 'render-'+hashlib.sha256(data.tobytes()).hexdigest()
        rendered_source=graph['sources'].get(source_id)
        if rendered_source is None:
            rendered_source,raw=_source_record(source_id,data)
            graph['sources'][source_id]=rendered_source;media[rendered_source['file']]=raw
        rev_id = 'render-revision-'+node_id
        graph['revisions'][rev_id] = {'id':rev_id,'source_id':source_id,'source_hash':rendered_source['sha256'],
            'conversion_version':CONVERSION_VERSION,'render_version':RENDER_VERSION,'spans':[[0,len(data)]],
            'gain_db':0.0,'normalization_gain':1.0,'fade_samples':0,'duration':len(data)/48000}
        node = _pick(clip,CLIP_FIELDS)
        node.update(id=node_id,source_id=source_id,revision_id=rev_id,volume=clip.get('volume',1) if root else 1,
                    duration=len(data)/48000,trim_start=0,trim_end=len(data)/48000,
                    transcript=_compact_text(clip,revision,interval),hotkey=clip.get('hotkey','') if root else '')
        graph['clips'].append(node)
        compact_nodes[key] = (node_id,rev_id,len(data))
        return compact_nodes[key]

    if mode == 'compact':
        for ident in sorted(direct):
            compact(ident,root=True)
        for document in graph['sequences'].values():
            for block in walk_blocks(document['blocks']):
                if block['type']=='play':
                    cid,rid,length = compact(block['clip_id'],block['revision_id'],block.get('interval'))
                    block.update(clip_id=cid,revision_id=rid,interval=[0,length])
    else:
        # Backup inclui o histórico das origens selecionadas e a proveniência das variantes.
        pending = list(needed)
        while pending:
            ident = pending.pop()
            clip,source,revision = context(ident)
            node = _pick(clip,CLIP_FIELDS)
            node.update(source_id=source['id'],revision_id=revision['id'])
            # Texto temporal editável, sem embeddings nem confirmação vocal local.
            if clip.get('transcript_analysis'):
                node['transcript_analysis'] = copy.deepcopy(clip['transcript_analysis'])
            graph['clips'].append(node)
            source_node = _pick(source,SOURCE_FIELDS)
            source_node['file'] = 'audio/'+hashlib.sha256(source['id'].encode()).hexdigest()+'.wav'
            raw = legacy[ident][2] if ident in legacy else Path(source['file_path']).read_bytes()
            if hashlib.sha256(raw).hexdigest() != source['sha256']:
                raise packs.PackError('A origem foi alterada')
            graph['sources'][source['id']] = source_node; media[source_node['file']] = raw
            revisions = [r for r in snap['revisions'].values() if r['source_id']==source['id']] or [revision]
            for rev in revisions:
                graph['revisions'][rev['id']] = _pick(rev,REVISION_FIELDS)
                origin = rev.get('variant_origin')
                if origin and origin['clip_id'] not in needed:
                    needed.add(origin['clip_id']); pending.append(origin['clip_id'])
    for clip in graph['clips']:
        for sid in clip.get('speaker_ids',[]) + ([clip['speaker_id']] if clip.get('speaker_id') else []):
            if sid in snap['speakers']:
                graph['speakers'][sid] = {'id':sid, **_pick(snap['speakers'][sid],('name','color'))}
    if not graph['clips']:
        raise packs.PackError('Nenhum áudio para exportar')
    files = {name:{'sha256':hashlib.sha256(raw).hexdigest(),'size':len(raw)} for name,raw in media.items()}
    manifest = {'format':packs.PACK_FORMAT,'version':2,'mode':mode,'voice_policy':'excluded',
        'graph':graph,'files':files,'roots':{'clips':sorted(direct),'sequences':sorted(sequences),'collections':sorted(collections)}}
    validate_graph(manifest)
    return manifest,media


def write_graph_pack(path,store,clip_ids=None,collection_ids=None,sequence_ids=None,mode='compact'):
    manifest,media = build_graph(store,clip_ids,collection_ids,sequence_ids,mode)
    if sum(len(raw) for raw in media.values()) > packs.MAX_UNCOMPRESSED_BYTES:
        raise packs.PackError('Pacote excede o limite de 2 GB')
    with zipfile.ZipFile(path,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
        for name,raw in media.items(): archive.writestr(name,raw)
        archive.writestr(packs.MANIFEST_NAME,_canonical(manifest))
    return {'count':len(manifest['graph']['clips']),'skipped':0,'summary':summary(manifest)}


def summary(manifest):
    graph = manifest['graph']
    return {'mode':manifest['mode'],'clips':len(graph['clips']),'sequences':len(graph['sequences']),
            'collections':len(graph['collections']),'sources':len(graph['sources']),'revisions':len(graph['revisions']),
            'voice_policy':'excluded','message':'Amostras vocais locais excluídas. Rótulos importados não confirmam pessoas.',
            'editable_sources_included':manifest['mode']=='editable'}


def validate_transcript(temporal, source, revisions):
    """Valida o modelo consumido pelo editor; metadados não tornam âncoras confiáveis."""
    if not isinstance(temporal,dict): raise packs.PackError('Transcrição temporal deve ser um objeto')
    if type(temporal.get('version',1)) is not int or temporal.get('version',1)!=1:
        raise packs.PackError('Versão de transcrição não suportada')
    if temporal.get('sample_rate',48000)!=48000 or temporal.get('source_id',source['id'])!=source['id']:
        raise packs.PackError('Referencial da transcrição divergente')
    if not isinstance(temporal.get('text',''),str): raise packs.PackError('Texto da transcrição inválido')
    if temporal.get('alignment','aligned') not in ('unaligned','aligned','partial','segments'):
        raise packs.PackError('Alinhamento da transcrição inválido')
    total=source['total_samples']
    def bounds(interval):
        if (not isinstance(interval,list) or len(interval)!=2 or any(type(v) is not int for v in interval)
                or not 0<=interval[0]<interval[1]<=total):
            raise packs.PackError('Intervalo da transcrição fora da origem')
        return interval
    if 'interval' in temporal: bounds(temporal['interval'])
    if 'context_interval' in temporal: bounds(temporal['context_interval'])
    revision_id=temporal.get('revision_id')
    if revision_id is not None:
        if not isinstance(revision_id,str) or revision_id not in revisions or revisions[revision_id]['source_id']!=source['id']:
            raise packs.PackError('Revisão da transcrição inválida')
    lists={key:temporal.get(key,[]) for key in ('words','segments','corrections')}
    if any(not isinstance(value,list) for value in lists.values()) or sum(len(v) for v in lists.values())>100000:
        raise packs.PackError('Unidades temporais inválidas ou acima do limite')
    units={}
    for kind,values in lists.items():
        for item in values:
            if (not isinstance(item,dict) or not isinstance(item.get('id'),str) or not item['id']
                    or item['id'] in units or not isinstance(item.get('text'),str)):
                raise packs.PackError('Identidade/texto de unidade temporal inválido')
            a,b=bounds([item.get('start_sample'),item.get('end_sample')]); units[item['id']]=item
            if 'anchors' in item:
                anchors=item['anchors']
                if not isinstance(anchors,list) or not anchors: raise packs.PackError('Âncoras temporais inválidas')
                previous=a
                for interval in anchors:
                    lo,hi=bounds(interval)
                    if lo<previous or hi>b: raise packs.PackError('Âncora fora da unidade temporal ou sobreposta')
                    previous=hi
            if 'word_ids' in item or kind=='corrections':
                refs=item.get('word_ids')
                if (not isinstance(refs,list) or any(not isinstance(ref,str) or not ref for ref in refs)
                        or len(refs)!=len(set(refs)) or (kind=='corrections' and not refs)):
                    raise packs.PackError('Referências textuais inválidas')
    words={item['id']:item for item in lists['words']}
    correction_targets=dict(words)
    for segment in lists['segments']:
        if 'word_ids' in segment:
            for ident in segment['word_ids']:
                word=words.get(ident)
                if not word or word['start_sample']>=segment['end_sample'] or word['end_sample']<=segment['start_sample']:
                    raise packs.PackError('Palavra referenciada não pertence ao segmento')
            has_words=bool(segment['word_ids'])
        else:
            has_words=any(word['start_sample']<segment['end_sample'] and word['end_sample']>segment['start_sample'] for word in words.values())
        if not has_words: correction_targets[segment['id']]=segment
    replaced=set()
    for correction in lists['corrections']:
        for ident in correction['word_ids']:
            unit=correction_targets.get(ident)
            if (not unit or ident in replaced or unit['start_sample']<correction['start_sample']
                    or unit['end_sample']>correction['end_sample']):
                raise packs.PackError('Referência de correção inválida ou duplicada')
            replaced.add(ident)


def validate_graph(manifest):
    if manifest.get('version') != 2 or manifest.get('mode') not in ('compact','editable') or manifest.get('voice_policy') != 'excluded':
        raise packs.PackError('Versão, modo ou política vocal inválidos')
    graph = manifest.get('graph')
    if not isinstance(graph,dict) or set(graph) != {'clips','sources','revisions','sequences','collections','speakers'}:
        raise packs.PackError('Grafo inválido')
    if not isinstance(graph['clips'],list) or not graph['clips']:
        raise packs.PackError('Pacote vazio')
    for key in ('sources','revisions','sequences','collections','speakers'):
        if not isinstance(graph[key],dict): raise packs.PackError('Tabela de grafo inválida')
    if sum(len(v) for v in graph.values()) > MAX_NODES:
        raise packs.PackError('Limite de nós excedido')
    ids = set()
    for key in graph:
        values = graph[key] if key=='clips' else graph[key].values()
        for node in values:
            if not isinstance(node,dict) or not isinstance(node.get('id'),str) or not node['id'] or len(node['id'])>200:
                raise packs.PackError('Identidade inválida')
            if key != 'clips' and graph[key].get(node['id']) is not node:
                raise packs.PackError('Chave de nó divergente')
            identity = (key,node['id'])
            if identity in ids: raise packs.PackError('Identidade duplicada')
            ids.add(identity)
    clips = {c['id']:c for c in graph['clips']}
    files = manifest.get('files')
    if not isinstance(files,dict) or not files or len(files)>MAX_FILES:
        raise packs.PackError('Tabela de mídia inválida')
    for name,meta in files.items():
        if packs._safe_entry_name(name) != name or not name.startswith('audio/') or not name.endswith('.wav'):
            raise packs.PackError('Caminho de mídia inválido')
        if not isinstance(meta,dict) or type(meta.get('size')) is not int or meta['size'] <= 0 or re.fullmatch('[0-9a-f]{64}',str(meta.get('sha256',''))) is None:
            raise packs.PackError('Hash/tamanho de mídia inválido')
    if set(files) != {s.get('file') for s in graph['sources'].values()}:
        raise packs.PackError('Mídia não referenciada ou ausente')
    for source in graph['sources'].values():
        if set(source)-set(SOURCE_FIELDS)-{'file'}:
            raise packs.PackError('Campo de origem não permitido')
        if (source.get('sample_rate') != 48000 or type(source.get('total_samples')) is not int or source['total_samples']<=0
                or source.get('sha256') != files[source['file']]['sha256'] or source.get('available') is not True):
            raise packs.PackError('Origem PCM inválida')
        if source.get('conversion_version') != CONVERSION_VERSION:
            raise packs.PackError('Conversão de origem não suportada')
    for revision in graph['revisions'].values():
        if set(revision)-set(REVISION_FIELDS): raise packs.PackError('Campo de revisão não permitido')
        source = graph['sources'].get(revision.get('source_id'))
        if not source or source['sha256'] != revision.get('source_hash'):
            raise packs.PackError('Referência de revisão inválida')
        if (revision.get('conversion_version',CONVERSION_VERSION)!=CONVERSION_VERSION
                or revision.get('render_version',RENDER_VERSION)!=RENDER_VERSION
                or revision.get('variant_processor',PROCESSOR_VERSION)!=PROCESSOR_VERSION):
            raise packs.PackError('Receita de áudio não suportada')
        validate_spans(revision.get('spans',[]),source['total_samples'])
        validate_variant(revision.get('variant',{}))
        for field,default,lo,hi in [('gain_db',0,-120,120),('normalization_gain',1,0,1e6),('fade_samples',48,0,240)]:
            value=revision.get(field,default)
            if type(value) not in (int,float) or not math.isfinite(value) or not lo <= value <= hi:
                raise packs.PackError('Ganho ou fade inválido')
        origin=revision.get('variant_origin')
        if origin:
            parent=graph['revisions'].get(origin.get('revision_id'))
            if (not parent or origin.get('clip_id') not in clips or origin.get('source_id') != parent['source_id']
                    or clips[origin['clip_id']]['source_id'] != parent['source_id']):
                raise packs.PackError('Origem da variante inválida')
    for ident in graph['revisions']:
        seen=set(); current=ident
        while current:
            if current in seen: raise packs.PackError('Ciclo entre revisões')
            seen.add(current)
            current=graph['revisions'][current].get('variant_origin',{}).get('revision_id')
    for clip in clips.values():
        if set(clip)-set(CLIP_FIELDS)-{'transcript_analysis'}: raise packs.PackError('Campo de clipe não permitido')
        for field in ('title','emoji','category','color','hotkey','transcript','acoustic_mood','speaker_id'):
            if field in clip and not isinstance(clip[field],str): raise packs.PackError('Texto de metadado inválido: '+field)
        for field in ('tags','speaker_ids'):
            if field in clip and (not isinstance(clip[field],list) or any(not isinstance(item,str) for item in clip[field])):
                raise packs.PackError('Lista de metadados inválida: '+field)
        if 'favorite' in clip and type(clip['favorite']) is not bool: raise packs.PackError('Favorito inválido')
        for field in ('duration','trim_start','trim_end'):
            if field in clip and (type(clip[field]) not in (int,float) or not math.isfinite(clip[field]) or clip[field]<0):
                raise packs.PackError('Tempo de clipe inválido: '+field)
        revision=graph['revisions'].get(clip.get('revision_id'))
        if not revision or revision['source_id'] != clip.get('source_id'):
            raise packs.PackError('Referência de clipe inválida')
        if 'transcript_analysis' in clip:
            validate_transcript(clip['transcript_analysis'],graph['sources'][clip['source_id']],graph['revisions'])
        volume=clip.get('volume',1)
        if type(volume) not in (int,float) or not math.isfinite(volume) or not 0<=volume<=3:
            raise packs.PackError('Volume inválido')
        for sid in clip.get('speaker_ids',[]) + ([clip['speaker_id']] if clip.get('speaker_id') else []):
            if sid not in graph['speakers']: raise packs.PackError('Pessoa referenciada não existe')
        normalize_binding(clip.get('hotkey',''))
    for speaker in graph['speakers'].values():
        for field in ('name','color'):
            if field in speaker and not isinstance(speaker[field],str): raise packs.PackError('Rótulo de pessoa inválido')
    def resolve(block):
        clip=clips.get(block.get('clip_id')); revision=graph['revisions'].get(block.get('revision_id'))
        if not clip or not revision or clip['source_id'] != revision['source_id']:
            raise packs.PackError('Ocorrência não resolve clipe/revisão')
        total=output_samples(sum(b-a for a,b in revision['spans']),revision.get('variant',{}))
        interval=block.get('interval',[0,total]); validate_spans([interval],total)
        volume=block.get('volume',1)
        if type(volume) not in (int,float) or not math.isfinite(volume) or not 0<=volume<=3:
            raise packs.PackError('Volume de ocorrência inválido')
        return {'_preview_ranges':[interval]}
    for seq in graph['sequences'].values(): validate_document(seq,resolve)
    # Reutiliza o contrato da coleção em um candidato isolado sem persistência.
    from store import DEFAULT_SETTINGS
    class Candidate(CollectionStore):
        def __init__(self):
            self._lock=threading.RLock()
            self.state={'settings':{**DEFAULT_SETTINGS,'stop_all_hotkey':'','replay_hotkey':'','overlay_hotkey':''},
                        'clips':copy.deepcopy(graph['clips']),'sequences':copy.deepcopy(graph['sequences']),'collections':{}}
            for clip in self.state['clips']: clip['hotkey']=''
            for seq in self.state['sequences'].values(): seq['hotkey']=''
        def get_clip(self,ident): return next((c for c in self.state['clips'] if c['id']==ident),None)
        def save(self): pass
    candidate=Candidate()
    for collection in graph['collections'].values():
        node=copy.deepcopy(collection)
        # Colisões de atalhos são resolvidas na importação, não invalidam referências.
        for pad in node['pads']: normalize_binding(pad.get('hotkey','')); pad['hotkey']=''
        candidate.save_collection(node)
    roots=manifest.get('roots')
    if not isinstance(roots,dict) or set(roots)!={'clips','sequences','collections'}:
        raise packs.PackError('Raízes inválidas')
    for key,values in roots.items():
        available=clips if key=='clips' else graph[key]
        if not isinstance(values,list) or any(v not in available for v in values):
            raise packs.PackError('Raiz não encontrada')


def validate_graph_pack(path):
    try:
        with zipfile.ZipFile(path) as archive:
            infos=archive.infolist()
            names=[packs._safe_entry_name(info.filename) for info in infos]
            if len(infos)>MAX_FILES+1 or len(names)!=len(set(names)):
                raise packs.PackError('Entradas duplicadas ou limite de arquivos excedido')
            if any(info.is_dir() or ((info.external_attr>>16)&0o170000)==0o120000 for info in infos):
                raise packs.PackError('Diretórios e links não são permitidos')
            if sum(info.file_size for info in infos)>packs.MAX_UNCOMPRESSED_BYTES:
                raise packs.PackError('Pacote excede o limite de 2 GB')
            info=archive.getinfo(packs.MANIFEST_NAME)
            if info.file_size>packs.MAX_MANIFEST_BYTES: raise packs.PackError('Manifesto grande demais')
            manifest=json.loads(archive.read(info),parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Número não finito')))
            validate_graph(manifest)
            if set(names)!=set(manifest['files'])|{packs.MANIFEST_NAME}:
                raise packs.PackError('Conteúdo não declarado no manifesto')
            for name,meta in manifest['files'].items():
                if archive.getinfo(name).file_size!=meta['size']: raise packs.PackError('Tamanho divergente')
                digest=hashlib.sha256(); size=0
                with archive.open(name) as stream:
                    for chunk in iter(lambda:stream.read(1<<20),b''):
                        size+=len(chunk)
                        if size>meta['size']: raise packs.PackError('Limite de extração excedido')
                        digest.update(chunk)
                if size!=meta['size'] or digest.hexdigest()!=meta['sha256']:
                    raise packs.PackError('Hash de mídia divergente')
            return {'manifest':manifest,'entries':{i.filename:i.file_size for i in infos}}
    except packs.PackError: raise
    except Exception as exc: raise packs.PackError('Pacote de grafo inválido: '+str(exc)) from exc


def import_graph_pack(store,path):
    checked=validate_graph_pack(path); manifest=checked['manifest']; graph=copy.deepcopy(manifest['graph'])
    identity=_digest(manifest)
    staged={}
    with tempfile.TemporaryDirectory(dir=store.tmp_dir,prefix='pack-stage-') as temporary:
        with zipfile.ZipFile(path) as archive:
            remaining=packs.MAX_UNCOMPRESSED_BYTES
            for source in graph['sources'].values():
                destination=Path(temporary)/(uuid.uuid4().hex+'.wav')
                digest,size=packs.copy_entry(archive,source['file'],destination,remaining)
                remaining-=size
                if digest!=source['sha256']: raise packs.PackError('A mídia mudou durante a leitura')
                info=sf.info(destination)
                if info.samplerate!=48000 or info.channels!=2 or info.frames!=source['total_samples']:
                    raise packs.PackError('Cabeçalho PCM diverge da origem declarada')
                for block in sf.blocks(destination,blocksize=65536,dtype='float32',always_2d=True):
                    if not np.isfinite(block).all(): raise packs.PackError('PCM contém amostras não finitas')
                staged[source['id']]=destination
        with store._lock:
            prior=store.state.get('pack_imports',{}).get(identity)
            if prior:
                # Repetição é sem efeito somente enquanto todos os nós continuam presentes.
                complete=all(store.get_clip(value) for value in prior['id_map']['clips'].values())
                complete=complete and all(value in store.state[key] for key in ('sequences','collections','revisions','sources') for value in prior['id_map'][key].values())
                if complete:
                    return {'ok':True,'imported':[],'skipped':len(graph['clips']),'warnings':[],**copy.deepcopy(prior)}
            before=copy.deepcopy(store.state); created=[]
            mapping={key:{} for key in graph}
            for key in graph:
                nodes=graph[key] if key=='clips' else graph[key].values()
                for node in nodes:
                    mapping[key][node['id']]=uuid.uuid4().hex
            warnings=[]
            used=set()
            for collection in [None,*store.state['collections'].values()]:
                for raw,_ in binding_entries(store.state,collection):
                    try:
                        if raw: used.add(normalize_binding(raw))
                    except ValueError: pass
            def binding(node):
                hotkey=normalize_binding(node.get('hotkey',''))
                if hotkey and hotkey in used:
                    warnings.append(str(node.get('title') or node.get('name') or node.get('label') or node['id'])+': atalho já em uso, importado sem atalho.')
                    node['hotkey']=''
                elif hotkey:
                    node['hotkey']=hotkey; used.add(hotkey)
            try:
                for ident,source in graph['sources'].items():
                    new=mapping['sources'][ident]
                    destination=store.sources_dir/(new+'.wav'); destination.parent.mkdir(parents=True,exist_ok=True)
                    staged[ident].replace(destination); created.append(destination)
                    source.pop('file'); source.update(id=new,file_path=str(destination))
                    store.state['sources'][new]=source
                for ident,revision in graph['revisions'].items():
                    revision.update(id=mapping['revisions'][ident],source_id=mapping['sources'][revision['source_id']])
                    if revision.get('variant_origin'):
                        origin=revision['variant_origin']
                        for key,table in [('clip_id','clips'),('revision_id','revisions'),('source_id','sources')]: origin[key]=mapping[table][origin[key]]
                    store.state['revisions'][revision['id']]=revision
                for ident,speaker in graph['speakers'].items():
                    new=mapping['speakers'][ident]
                    store.state['speakers'][new]={'name':str(speaker.get('name') or 'Pessoa importada'),'color':str(speaker.get('color') or '#3b82f6'),'imported':True}
                imported=[]
                for clip in graph['clips']:
                    old=clip['id']; clip.update(id=mapping['clips'][old],source_id=mapping['sources'][clip['source_id']],revision_id=mapping['revisions'][clip['revision_id']])
                    clip['file_path']=store.state['sources'][clip['source_id']]['file_path']
                    clip['speaker_ids']=[mapping['speakers'][sid] for sid in clip.get('speaker_ids',[])]
                    clip['speaker_id']=mapping['speakers'].get(clip.get('speaker_id'),'')
                    if clip.get('transcript_analysis'):
                        clip['transcript_analysis']['source_id']=clip['source_id']
                        if clip['transcript_analysis'].get('revision_id'):
                            clip['transcript_analysis']['revision_id']=mapping['revisions'][clip['transcript_analysis']['revision_id']]
                    clip.update(pending=False,play_count=0,created_at=time.time(),field_provenance={'speaker_id':'imported_label','speaker_ids':'imported_label','transcript':'imported'})
                    binding(clip); store.state['clips'].append(clip); imported.append(copy.deepcopy(clip))
                for ident,seq in graph['sequences'].items():
                    seq.update(id=mapping['sequences'][ident],revision=1); binding(seq)
                    for block in walk_blocks(seq['blocks']):
                        block['id']=uuid.uuid4().hex
                        if block['type']=='play':
                            block['clip_id']=mapping['clips'][block['clip_id']]; block['revision_id']=mapping['revisions'][block['revision_id']]
                            store.state['source_references']['sequence:'+seq['id']+':'+block['id']]=store.state['revisions'][block['revision_id']]['source_id']
                    store.state['sequences'][seq['id']]=seq
                for ident,collection in graph['collections'].items():
                    collection.update(id=mapping['collections'][ident],revision=1)
                    for pad in collection['pads']:
                        pad['id']=uuid.uuid4().hex; binding(pad)
                        for ref in target_references(pad['target']): ref['id']=mapping['clips' if ref['type']=='clip' else 'sequences'][ref['id']]
                    store.state['collections'][collection['id']]=collection
                result={'id_map':mapping,'summary':summary(manifest)}
                store.state.setdefault('pack_imports',{})[identity]=copy.deepcopy(result)
                store.save()
                return {'ok':True,'imported':imported,'skipped':0,'warnings':warnings,**result}
            except Exception:
                store.state.clear(); store.state.update(before)
                for file in created: file.unlink(missing_ok=True)
                raise
