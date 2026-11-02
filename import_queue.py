"""Entrada de mídia persistente; probe limitado, publicação cancelável e IA explícita."""
import atexit
import copy
import hashlib
import json
import math
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path

import numpy as np
import soundfile as sf

from packs import AUDIO_EXTS, sha256_file
from analysis_jobs import guard_analysis_scope

LONG_SECONDS = 120
MAX_WINDOW_SECONDS = 120
MAX_JOBS = 512
VIDEO_EXTS = ('.mp4', '.mkv', '.mov', '.avi', '.m4v')
EXTRA_AUDIO_EXTS = ('.aac', '.wma', '.opus')
TERMINAL = {'success', 'duplicate', 'failed', 'cancelled', 'interrupted'}
ANALYSIS_ACTIVE = {'queued','running','paused','waiting_policy'}


def decoder_paths(ffmpeg='ffmpeg'):
    executable = shutil.which(ffmpeg)
    probe = shutil.which(str(Path(executable).with_name('ffprobe.exe' if Path(executable).suffix == '.exe' else 'ffprobe'))) if executable else None
    return executable, probe or shutil.which('ffprobe')


def probe_media(path, ffmpeg='ffmpeg'):
    """Lê cabeçalhos, nunca decodifica o arquivo inteiro para descobrir duração."""
    path = Path(path)
    if not path.is_file():
        raise ValueError('Arquivo não encontrado')
    if path.suffix.lower() not in AUDIO_EXTS + EXTRA_AUDIO_EXTS + VIDEO_EXTS:
        raise ValueError('Formato de mídia não suportado')
    decoder, probe = decoder_paths(ffmpeg)
    if path.suffix.lower() not in VIDEO_EXTS:
        try:
            info = sf.info(path)
            if info.frames > 0 and info.samplerate > 0:
                return {'duration': info.duration, 'native': True, 'sample_rate': info.samplerate}
        except (OSError, RuntimeError):
            pass
    if not decoder or not probe:
        raise ValueError('Este formato exige FFmpeg e ffprobe disponíveis')
    result = subprocess.run([probe, '-v', 'error', '-select_streams', 'a:0', '-show_entries',
        'stream=duration,codec_name:format=duration', '-of', 'json', str(path)], capture_output=True,
        timeout=15, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise ValueError('Não foi possível ler a mídia')
    data = json.loads(result.stdout)
    streams = data.get('streams', [])
    if not streams:
        raise ValueError('A mídia não contém faixa de áudio')
    duration = float(streams[0].get('duration') or data.get('format', {}).get('duration') or 0)
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError('Duração desconhecida; mídia não admitida')
    return {'duration': duration, 'native': False, 'decoder': decoder, 'codec': streams[0].get('codec_name')}


def decode_window(path, destination, probe, window):
    start, end = window
    if probe['native']:
        from audio_processing import resample_audio
        rate = probe['sample_rate']
        data, rate = sf.read(path, start=round(start*rate), stop=round(end*rate), dtype='float32', always_2d=True)
        data = resample_audio(data, rate, 48000)
        data = np.repeat(data, 2, axis=1) if data.shape[1] == 1 else data[:, :2]
        sf.write(destination, data, 48000, format='WAV', subtype='FLOAT')
    else:
        result = subprocess.run([probe['decoder'], '-v', 'error', '-nostdin', '-y', '-ss', str(start),
            '-i', str(path), '-t', str(end-start), '-map', '0:a:0', '-vn', '-ac', '2', '-ar', '48000',
            '-c:a', 'pcm_f32le', '-f', 'wav', str(destination)], capture_output=True,
            timeout=180, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode:
            raise ValueError('Falha ao decodificar a janela selecionada')
    data, rate = sf.read(destination, dtype='float32', always_2d=True)
    if rate != 48000 or not len(data) or not np.isfinite(data).all():
        raise ValueError('Áudio vazio ou inválido')
    return len(data)/rate


class ImportQueue:
    @property
    def jobs(self):
        return self.store.state.setdefault('import_jobs', [])

    @jobs.setter
    def jobs(self,value):
        self.store.state['import_jobs']=value

    def __init__(self, api):
        self.api, self.store = api, api._store
        self.lock = threading.Condition(threading.RLock())
        self.closed, self.thread, self.analysis_thread = False, None, None
        with self.store._lock:
            self.jobs = self.store.state.setdefault('import_jobs', [])
            changed = False
            for job in self.jobs:
                if job['status'] not in TERMINAL | {'awaiting_window'}:
                    job.update(status='interrupted', error='Importação interrompida; tente novamente explicitamente')
                    changed = True
                if job.get('analysis',{}).get('status') in ('queued','running','paused','waiting_policy'):
                    job['analysis']['status']='interrupted'
                    changed=True
            if changed:
                self.store.save()

    def snapshot(self):
        with self.lock, self.store._lock:
            return copy.deepcopy(self.jobs)

    def current_job(self, ident, attempt=None):
        """Leitura sob store._lock; mutações da fila também mantêm self.lock."""
        job=next((j for j in self.jobs if j['id']==ident),None)
        if attempt is not None and (self.closed or not job or job.get('attempt_id')!=attempt
                                    or job['status'] not in ('probing','running')):
            return None
        return job

    def current_analysis(self, ident, generation):
        job=self.current_job(ident)
        if (self.closed or not job or job['status']!='success'
                or job.get('analysis',{}).get('generation')!=generation
                or job['analysis']['status'] not in ANALYSIS_ACTIVE):
            return None
        return job

    def restore_state(self, previous):
        """Rollback sob ambos os locks conserva a lista e registros vivos da fila."""
        live=self.jobs;existing={job['id']:job for job in live};restored=[]
        for saved in previous.get('import_jobs',[]):
            job=existing.get(saved['id'],{})
            analysis=job.get('analysis')
            if isinstance(analysis,dict) and isinstance(saved.get('analysis'),dict):
                analysis.clear();analysis.update(saved['analysis']);saved['analysis']=analysis
            job.clear();job.update(saved);restored.append(job)
        live[:]=restored;previous['import_jobs']=live
        self.store.state.clear();self.store.state.update(previous)

    def save(self):
        with self.store._lock:
            self.store.save()

    def change(self, job, status, progress=None, **fields):
        job.update(status=status, **fields)
        if progress is not None:
            job['progress'] = progress
        job['history'].append({'status': status, 'at': time.time()})
        job['history'] = job['history'][-64:]
        self.save()

    def start(self):
        if self.thread is None or not self.thread.is_alive():
            self.thread = threading.Thread(target=self.work, daemon=True, name='MediaImportWorker')
            self.thread.start()
        self.lock.notify_all()

    def enqueue(self, entries, skip=False, category=None, auto_analyze=True):
        with self.lock, self.store._lock:
            if self.closed:
                return {'ok': False, 'error': 'Fila encerrada'}
            if len(entries) > MAX_JOBS:
                return {'ok': False, 'error': 'Selecione até 512 arquivos por lote'}
            while len(self.jobs)+len(entries) > MAX_JOBS:
                old = next((j for j in self.jobs if j['status'] in TERMINAL and not (
                    j['status']=='success' and j.get('analysis',{}).get('status') in ANALYSIS_ACTIVE)), None)
                if not old:
                    return {'ok': False, 'error': 'Fila cheia; aguarde os itens atuais'}
                self.jobs.remove(old)
            added = []
            for entry in entries:
                job = {'id': uuid.uuid4().hex, 'source': entry['source'], 'status': 'queued', 'progress': 0,
                    'history': [{'status': 'queued', 'at': time.time()}], 'window': None,
                    'skip_speaker_id': bool(skip), 'metadata': {'category': category} if category else {},
                    'adapter_metadata': entry.get('metadata', {}), 'warnings': [], 'error': None,
                    'auto_analyze':bool(auto_analyze), 'analysis':{'status':'pending' if auto_analyze else 'manual','stages':[], 'index':0}}
                self.jobs.append(job); added.append(copy.deepcopy(job))
            self.save(); self.start()
            return {'ok': True, 'jobs': added}

    def work(self):
        while True:
            with self.lock, self.store._lock:
                job = next((j for j in self.jobs if j['status'] == 'queued'), None)
                if self.closed or job is None:
                    return
                ident,attempt,source_path=job['id'],uuid.uuid4().hex,job['source']
                job['attempt_id']=attempt
                self.change(job, 'probing', 5)
            output = self.store.clips_dir / ('import-'+ident+'-'+attempt+'.wav')
            published = False
            try:
                probe = probe_media(source_path, getattr(getattr(self.api, '_config', None), 'ffmpeg_path', 'ffmpeg'))
                with self.lock, self.store._lock:
                    job=self.current_job(ident,attempt)
                    if job is None:
                        continue
                    job['duration'] = probe['duration']
                    if job['window'] is None and probe['duration'] > LONG_SECONDS:
                        self.change(job, 'awaiting_window', 10)
                        continue
                    window = job['window'] or [0, probe['duration']]
                    self.change(job, 'running', 20, window=window)
                digest = sha256_file(source_path)
                identity = hashlib.sha256(json.dumps([digest, [round(v*48000) for v in window]]).encode()).hexdigest()
                with self.lock, self.store._lock:
                    job=self.current_job(ident,attempt)
                    duplicate = next((c for c in self.store.state['clips'] if c.get('import_identity') == identity), None)
                    if job is None:
                        continue
                    if duplicate:
                        self.change(job, 'duplicate', 100, clip_id=duplicate['id'], identity=identity)
                        continue
                    self.change(job, 'running', 45, identity=identity)
                duration = decode_window(source_path, output, probe, window)
                with self.lock, self.store._lock:
                    # Cancelamento e publicação usam o mesmo lock até a gravação final.
                    job=self.current_job(ident,attempt)
                    if job is None:
                        continue
                    original = copy.deepcopy(self.store.state)
                    metadata = {**job['adapter_metadata'], **job['metadata']}
                    if job['skip_speaker_id']:
                        metadata.pop('speaker_id', None)
                    # Clipe e histórico são publicados por um único save, sob o mesmo lock.
                    clip = {'id':uuid.uuid4().hex,'file_path':str(output),'duration':duration,
                        'title':Path(job['source']).stem,'acoustic_mood':'Não analisado','category':'Call',
                        'emoji':'🔊','color':'#3b82f6','hotkey':'','tags':[],'favorite':False,'pending':False,
                        'play_count':0,'volume':1.0,'trim_start':0.0,'trim_end':duration,'lufs':None,
                        'loudness_gain_db':0.0,'lufs_estimated':False,'speaker_id':'','speaker_ids':[],
                        'segments':[],'transcript':'','created_at':time.time(),**metadata}
                    if clip['speaker_id']:
                        self.store.ensure_speaker(clip['speaker_id'])
                        clip['speaker_ids']=[clip['speaker_id']]
                    self.store.state['clips'].insert(0,clip)
                    clip['import_identity'] = identity
                    clip['import_window'] = copy.deepcopy(window)
                    clip['field_provenance'] = {key: 'imported_label' for key in metadata}
                    clip['field_provenance'].update({key:'human' for key in job['metadata']})
                    job['clip_id'] = clip['id']
                    job['field_versions'] = copy.deepcopy(clip.get('field_versions', {}))
                    job['field_baseline'] = {k: copy.deepcopy(clip.get(k)) for k in ('title','category','emoji','color','tags')}
                    job['warnings'] = ['Áudio disponível para editar. A análise automática usa a fila e a política local; propostas ficam no editor.'] if job['auto_analyze'] else ['Somente importação; análise pode ser solicitada no editor.']
                    job['analysis']={'status':'queued' if job['auto_analyze'] else 'manual','index':0,'generation':uuid.uuid4().hex,
                        'stages':['transcription']+([] if job['skip_speaker_id'] or job['adapter_metadata'].get('speaker_id') else ['speakers'])+['acoustics','metadata']}
                    try:
                        self.change(job, 'success', 100)
                        published = True
                        if job['auto_analyze']:
                            self.start_analysis()
                    except Exception:
                        self.restore_state(original)
                        raise
                snap = self.store.snapshot()
                self.api._notify_ui('clips_updated', {'clips': self.api._public_clips(snap['clips']), 'speakers': snap['speakers']})
            except Exception as exc:
                with self.lock, self.store._lock:
                    job=self.current_job(ident,attempt)
                    if job is not None:
                        self.change(job, 'failed', error=str(exc))
            finally:
                if not published:
                    output.unlink(missing_ok=True)

    def start_analysis(self):
        if self.analysis_thread is None or not self.analysis_thread.is_alive():
            self.analysis_thread=threading.Thread(target=self.analyze,daemon=True,name='ImportAnalysisCoordinator')
            self.analysis_thread.start()

    def analyze(self):
        """Coordena etapas E6 sem executar modelos aqui nem bloquear a entrada de áudio."""
        while True:
            with self.lock, self.store._lock:
                if self.closed: return
                jobs=[(j['id'],j['analysis']['generation']) for j in self.jobs
                      if j['status']=='success' and j.get('analysis',{}).get('status') in ANALYSIS_ACTIVE]
                if not jobs: return
            for ident,generation in jobs:
                try:
                    with self.lock, self.store._lock:
                        job=self.current_analysis(ident,generation)
                        if job is None: continue
                        analysis=job['analysis']
                        current=analysis.get('analysis_id')
                    if current:
                        result=self.api.get_analysis_job(current)
                        if result.get('status') in ('queued','running'): continue
                        with self.lock, self.store._lock:
                            job=self.current_analysis(ident,generation)
                            if job is None or job['analysis'].get('analysis_id')!=current: continue
                            analysis=job['analysis']
                            analysis.setdefault('history',[]).append({'stage':analysis['stages'][analysis['index']], 'analysis_id':current,
                                'status':result.get('status','failed'),'application':result.get('application')})
                            analysis.pop('analysis_id',None)
                            if not result.get('ok') or result.get('status') in ('failed','cancelled'):
                                analysis.update(status='failed',error=result.get('error') or 'Análise indisponível; tente novamente')
                                self.save(); continue
                            if result.get('application',{}).get('state')=='obsolete':
                                analysis.update(status='obsolete',error='O clipe mudou; nova análise exige solicitação explícita')
                                self.save(); continue
                            analysis['index']+=1
                            if analysis['index']==len(analysis['stages']):
                                analysis['status']='ready'; self.save(); continue
                    queue=getattr(self.api,'_analysis_queue',None)
                    # Nunca adquirir o lock da fila E6 segurando o lock do store:
                    # a aplicação E6 usa a ordem condição da fila -> store.
                    allowed=queue is not None and queue.allows()
                    policy=queue.policy() if queue is not None and not allowed else {}
                    with self.lock, self.store._lock:
                        job=self.current_analysis(ident,generation)
                        if job is None: continue
                        analysis=job['analysis']
                        if queue is None:
                            analysis.update(status='unavailable',error='Serviço de análise indisponível'); self.save(); continue
                        if not allowed:
                            status='paused' if policy.get('paused') else 'waiting_policy'
                            if analysis['status']!=status: analysis['status']=status; self.save()
                            continue
                        clip_id,stage=job['clip_id'],analysis['stages'][analysis['index']]
                    def eligible(ident=ident,generation=generation):
                        with self.store._lock:
                            return self.current_analysis(ident,generation) is not None
                    def apply_if_current(callback,result,ident=ident,generation=generation):
                        # E6 mantém sua condição; apenas store aqui, sem lock da fila de mídia.
                        # Cancelamento usa o mesmo store lock: a checagem e o apply são atômicos.
                        with self.store._lock:
                            if self.current_analysis(ident,generation) is None:
                                return {'state':'obsolete'}
                            return callback(result)
                    with guard_analysis_scope(eligible,apply_if_current):
                        result=self.api.request_clip_analysis(clip_id,stage,priority=False)
                    with self.lock, self.store._lock:
                        job=self.current_analysis(ident,generation)
                        stale=job is None
                        if not stale:
                            analysis=job['analysis']
                            if result.get('ok'):
                                analysis.update(status='running',analysis_id=result['analysis_id'])
                            elif result.get('error_code')=='queue_full':
                                analysis['status']='queued'
                            else:
                                analysis.update(status='failed',error=result.get('error','Análise indisponível'))
                            self.save()
                    if stale and result.get('ok'):
                        self.api.cancel_analysis_job(result['analysis_id'])
                except Exception as exc:
                    with self.lock, self.store._lock:
                        job=self.current_analysis(ident,generation)
                        if job is not None:
                            job['analysis'].update(status='failed',error=str(exc)); self.save()
            with self.lock:
                if not self.closed: self.lock.wait(.15)

    def action(self, ident, action, window=None):
        cancel_analysis=None
        with self.lock, self.store._lock:
            job = self.current_job(ident)
            if not job:
                return {'ok': False, 'error': 'Item não encontrado'}
            if self.closed:
                return {'ok':False,'error':'Fila encerrada'}
            if action=='cancel' and job['status']=='success' and job.get('analysis',{}).get('status') in ANALYSIS_ACTIVE:
                analysis=job['analysis']; analysis['status']='cancelled'
                cancel_analysis=analysis.get('analysis_id')
                self.save()
            elif action=='retry_analysis' and job['status']=='success' and job.get('analysis',{}).get('status') not in ANALYSIS_ACTIVE:
                job['analysis'].update(status='queued',index=0,generation=uuid.uuid4().hex); job['analysis'].pop('analysis_id',None)
                self.save(); self.start_analysis()
            elif action == 'cancel' and job['status'] not in TERMINAL:
                job['attempt_id']=uuid.uuid4().hex
                self.change(job, 'cancelled')
            elif action == 'retry' and job['status'] in {'failed','cancelled','interrupted'}:
                job['attempt_id']=uuid.uuid4().hex
                self.change(job, 'queued', 0, error=None); self.start()
            elif action == 'window' and job['status'] == 'awaiting_window':
                a, b = window
                if any(type(v) not in (int,float) or not math.isfinite(v) for v in window) or not 0 <= a < b <= job['duration'] or b-a > MAX_WINDOW_SECONDS:
                    return {'ok': False, 'error': 'Escolha uma janela válida de até 120 segundos'}
                job['window'] = [a,b]
                self.change(job, 'queued', 0); self.start()
            else:
                return {'ok': False, 'error': 'Ação indisponível neste estado'}
            result={'ok': True, 'job': copy.deepcopy(job)}
        if cancel_analysis:
            self.api.cancel_analysis_job(cancel_analysis)
        return result

    def close(self):
        cancelled=[]
        with self.lock, self.store._lock:
            self.closed = True
            for job in self.jobs:
                if job['status'] in ('queued','probing','running'):
                    self.change(job, 'interrupted', error='Fila encerrada; tente novamente explicitamente')
                analysis=job.get('analysis',{})
                if analysis.get('status') in ANALYSIS_ACTIVE:
                    analysis['status']='interrupted'
                    if analysis.get('analysis_id'): cancelled.append(analysis['analysis_id'])
                    self.save()
            self.lock.notify_all()
        for analysis_id in cancelled:
            self.api.cancel_analysis_job(analysis_id)
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(timeout=5)
        if self.analysis_thread and self.analysis_thread is not threading.current_thread():
            self.analysis_thread.join(timeout=5)


class ImportAPI:
    _imports_init_lock = threading.Lock()

    def _import_queue(self):
        with self._imports_init_lock:
            if not hasattr(self, '_imports'):
                self._imports = ImportQueue(self)
                atexit.register(self._imports.close)
            return self._imports

    def get_import_capabilities(self):
        decoder, probe = decoder_paths(getattr(getattr(self, '_config', None), 'ffmpeg_path', 'ffmpeg'))
        return {'ok': True, 'video': bool(decoder and probe), 'audio_extensions': list(AUDIO_EXTS+EXTRA_AUDIO_EXTS),
                'video_extensions': list(VIDEO_EXTS) if decoder and probe else [], 'max_window_seconds': MAX_WINDOW_SECONDS}

    def set_import_options(self, auto_analyze=True):
        if type(auto_analyze) is not bool:
            return {'ok':False,'error':'Opção de análise inválida'}
        self._import_auto_analyze=auto_analyze
        return {'ok':True,'auto_analyze':auto_analyze}

    def enqueue_import(self, paths=None, skip_speaker_id=False, forced_category=None, folder=False, auto_analyze=None):
        try:
            if auto_analyze is None: auto_analyze=getattr(self,'_import_auto_analyze',True)
            if paths is None and self._window is not None:
                import webview
                paths = self._window.create_file_dialog(webview.FOLDER_DIALOG if folder else webview.OPEN_DIALOG,
                    allow_multiple=not folder)
            if not paths:
                return {'ok': False, 'error': 'Importação cancelada', 'jobs': []}
            if isinstance(paths, str):
                paths = [paths]
            entries = []
            def admit(entry):
                if len(entries)>=MAX_JOBS: raise ValueError(f'Limite de {MAX_JOBS} arquivos por lote excedido; escolha uma pasta menor')
                entries.append(entry)
            for raw in paths:
                target = Path(raw).resolve()
                if target.is_dir():
                    if not skip_speaker_id and (target/'separados'/'mapa_de_falas.json').is_file():
                        target = target/'separados'
                    manifest = target/'mapa_de_falas.json'
                    if not skip_speaker_id and manifest.is_file():
                        if manifest.stat().st_size>8*1024*1024: raise ValueError('Mapa de falas excede o limite de 8 MB')
                        items = json.loads(manifest.read_text(encoding='utf-8'))
                        if not isinstance(items, list):
                            raise ValueError('Mapa de falas inválido')
                        for item in items:
                            source = (target/str(item.get('arquivo_trecho', ''))).resolve()
                            if not source.is_relative_to(target):
                                raise ValueError('Caminho fora da pasta separados')
                            text = str(item.get('transcricao') or '')
                            admit({'source': str(source), 'metadata': {'transcript': text,
                                'title': text[:60] or source.stem, 'speaker_id': 'interlocutor_'+str(item.get('interlocutor',1)), 'emoji': '🎙️'}})
                    else:
                        for count,p in enumerate(target.rglob('*'),1):
                            if count>MAX_JOBS*20: raise ValueError('Limite de descoberta excedido; escolha uma pasta menor')
                            if p.is_file() and p.suffix.lower() in AUDIO_EXTS+EXTRA_AUDIO_EXTS+VIDEO_EXTS and not p.name.endswith('_completo.mp3'):
                                admit({'source':str(p)})
                else:
                    admit({'source': str(target)})
            return self._import_queue().enqueue(entries, skip_speaker_id, forced_category, auto_analyze)
        except (OSError, ValueError, TypeError) as exc:
            return {'ok': False, 'error': str(exc), 'jobs': []}

    def list_import_jobs(self):
        return {'ok': True, 'jobs': self._import_queue().snapshot()}

    def cancel_import_job(self, ident):
        return self._import_queue().action(ident, 'cancel')

    def retry_import_job(self, ident):
        return self._import_queue().action(ident, 'retry')

    def retry_import_analysis(self, ident):
        return self._import_queue().action(ident, 'retry_analysis')

    def set_import_window(self, ident, start, end):
        return self._import_queue().action(ident, 'window', [start,end])

    def apply_import_metadata(self, ids, updates):
        if not isinstance(updates, dict) or set(updates)-{'title','category','emoji','color','tags'}:
            return {'ok': False, 'error': 'Metadados em lote inválidos'}
        queue = self._import_queue()
        conflicts, changed = [], []
        with queue.lock, self._store._lock:
            previous = copy.deepcopy(self._store.state)
            try:
                for job in queue.jobs:
                    if job['id'] not in ids:
                        continue
                    if job['status'] in ('queued','probing','running','awaiting_window'):
                        job['metadata'].update(copy.deepcopy(updates)); continue
                    clip = self._store.get_clip(job.get('clip_id')) if job['status'] == 'success' else None
                    if clip is None:
                        continue
                    for key, value in updates.items():
                        draft=self._store.state['edit_drafts'].get(clip['id'],{})
                        if (clip.get('field_versions', {}).get(key,0) != job.get('field_versions',{}).get(key,0)
                                or clip.get(key) != job.get('field_baseline',{}).get(key)
                                or (key in draft.get('fields',{}) and draft.get('field_provenance',{}).get(key)=='human')):
                            conflicts.append({'clip_id': clip['id'], 'field': key}); continue
                        clip[key] = copy.deepcopy(value)
                        clip.setdefault('field_provenance', {})[key] = 'human'
                        clip.setdefault('field_versions', {})[key] = clip.get('field_versions',{}).get(key,0)+1
                        job.setdefault('field_versions', {})[key] = clip['field_versions'][key]
                        job.setdefault('field_baseline', {})[key] = copy.deepcopy(value)
                    changed.append(clip['id'])
                self._store.save()
            except Exception as exc:
                queue.restore_state(previous)
                return {'ok': False, 'error': str(exc)}
        return {'ok': True, 'changed': changed, 'conflicts': conflicts}

    def _on_native_drop(self, event):
        paths = [item.get('pywebviewFullPath') for item in event.get('dataTransfer', {}).get('files', [])]
        paths = [path for path in paths if isinstance(path, str) and Path(path).is_absolute()]
        if paths:
            # O evento apenas admite entradas; probe e decode rodam no worker.
            return self.enqueue_import(paths)

    def _bind_native_drop(self):
        from webview.dom import DOMEventHandler
        # dragover é frequente: serializá-lo no bridge percorre o documento inteiro.
        # O filtro roda antes do listener nativo, na fase de bolha, preservando
        # handlers dos alvos internos e os caminhos completos dos arquivos reais.
        self._window.run_js("""(() => {
            if (document._soundboardFileDropGuard) return;
            document._soundboardFileDropGuard = true;
            const hasFiles = event => Array.from(event.dataTransfer?.types || []).includes('Files');
            document.addEventListener('dragover', event => {
                if (hasFiles(event)) event.preventDefault();
            });
            document.addEventListener('drop', event => {
                if (!hasFiles(event)) event.stopImmediatePropagation();
            });
        })();""")
        self._window.dom.document.events.drop += DOMEventHandler(self._on_native_drop, prevent_default=True)
