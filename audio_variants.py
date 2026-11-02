"""Efeitos de revisão: PCM permanece na origem; tempo de saída tem mapa explícito."""
import copy
import math
import os
import shutil
import subprocess
import threading
import uuid
from functools import lru_cache

import numpy as np

SAMPLE_RATE = 48000
MAX_INPUT_SECONDS = 120
PROCESSOR_VERSION = 'ffmpeg-atempo-asetrate-v1'


def validate(options):
    if not isinstance(options, dict) or set(options) - {'speed', 'pitch_semitones', 'reverse'}:
        raise ValueError('Parâmetros de variante inválidos')
    result = {'speed': options.get('speed', 1.0), 'pitch_semitones': options.get('pitch_semitones', 0.0),
              'reverse': options.get('reverse', False)}
    for key, lo, hi in [('speed', .5, 2), ('pitch_semitones', -12, 12)]:
        value = result[key]
        if type(value) not in (int, float) or not math.isfinite(value) or not lo <= value <= hi:
            raise ValueError('Velocidade: 0,5–2x; tom: -12 a +12 semitons')
    if type(result['reverse']) is not bool:
        raise ValueError('Reverso deve ser booleano')
    return result


def _executable(ffmpeg_path=None):
    return shutil.which(ffmpeg_path if ffmpeg_path is not None else os.environ.get('SOUNDBOARD_FFMPEG', 'ffmpeg'))


@lru_cache(maxsize=4)
def _filters(executable):
    if not executable:
        return False
    try:
        result = subprocess.run([executable, '-hide_banner', '-filters'], capture_output=True,
            timeout=10, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        return result.returncode == 0 and all(name in result.stdout for name in (b' atempo ', b' asetrate ', b' aresample '))
    except (OSError, subprocess.SubprocessError):
        return False


def capabilities(ffmpeg_path=None):
    enabled = _filters(_executable(ffmpeg_path))
    return {'ok': True, 'reverse': True, 'speed': enabled, 'pitch': enabled,
            'speed_range': [.5, 2], 'pitch_range': [-12, 12], 'max_input_seconds': MAX_INPUT_SECONDS,
            'processor': PROCESSOR_VERSION, 'reason': '' if enabled else 'Velocidade/tom exigem FFmpeg com atempo, asetrate e aresample. Configure SOUNDBOARD_FFMPEG.',
            'limitations': 'Até 120 s de montagem; 0,5–2x; ±12 semitons. Pode produzir artefatos. Sem encadear variantes.'}


def output_samples(total, options):
    return max(1, round(total / validate(options)['speed']))


def map_ranges(ranges, total, options):
    effect = validate(options)
    length = output_samples(total, effect)
    result = []
    for start, end in ranges:
        a, b = round(start / effect['speed']), round(end / effect['speed'])
        a, b = max(0, min(length, a)), max(0, min(length, b))
        if effect['reverse']:
            a, b = length - b, length - a
        if a < b:
            result.append([a, b])
    return sorted(result)


def project(transcript, spans, options):
    from transcript_model import project as project_source
    result = project_source(transcript, spans)
    total = sum(b-a for a,b in spans)
    for key in ('words', 'segments', 'corrections'):
        projected = []
        for item in result[key]:
            mapped = map_ranges([[item['output_start_sample'], item['output_end_sample']]], total, options)
            if mapped:
                projected.append({**item, 'output_start_sample': mapped[0][0], 'output_end_sample': mapped[0][1]})
        result[key] = sorted(projected, key=lambda item: item['output_start_sample'])
    result['reversed_audio'] = validate(options)['reverse']
    # Texto descreve a origem, jamais uma falsa transcrição da fala invertida.
    result['text_direction'] = 'source' if result['reversed_audio'] else 'output'
    return result


def revision_text(context, revision):
    """Texto deriva da montagem escolhida; correções humanas continuam na origem."""
    temporal = context.get('transcript_analysis')
    if temporal:
        return project(temporal, revision['spans'], revision.get('variant', {}))['text']
    if context.get('segments'):
        from clip_edits import project_segments
        segments = project_segments(context['segments'], revision['spans'])
        return ' '.join(str(segment.get('text') or segment.get('transcript') or '').strip()
                        for segment in segments).strip()
    # Sem âncoras não há como atribuir palavras a intervalos menores.
    return context.get('transcript') or ''


def apply(samples, options, ffmpeg_path=None):
    effect = validate(options)
    if samples.ndim != 2 or samples.shape[1] not in (1, 2) or not 0 < len(samples) <= MAX_INPUT_SECONDS * SAMPLE_RATE:
        raise ValueError('Variantes aceitam montagem mono/estéreo de até 120 segundos')
    if not np.isfinite(samples).all():
        raise ValueError('PCM inválido para variante')
    audio = np.asarray(samples, dtype=np.float32)
    if effect['speed'] != 1 or effect['pitch_semitones'] != 0:
        executable = _executable(ffmpeg_path)
        if not _filters(executable):
            raise ValueError(capabilities(ffmpeg_path)['reason'])
        pitch = 2 ** (effect['pitch_semitones']/12)
        filters = [f'asetrate={SAMPLE_RATE*pitch:.9f}', f'aresample={SAMPLE_RATE}']
        tempo = effect['speed']/pitch
        while tempo < .5:
            filters.append('atempo=0.5'); tempo /= .5
        while tempo > 2:
            filters.append('atempo=2'); tempo /= 2
        filters.append(f'atempo={tempo:.12f}')
        try:
            processed = subprocess.run([executable, '-hide_banner', '-loglevel', 'error', '-nostdin',
                '-f', 'f32le', '-ar', str(SAMPLE_RATE), '-ac', str(audio.shape[1]), '-i', 'pipe:0',
                '-af', ','.join(filters), '-f', 'f32le', '-ar', str(SAMPLE_RATE), '-ac', str(audio.shape[1]), 'pipe:1'],
                input=audio.astype('<f4').tobytes(), capture_output=True, timeout=60,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except (OSError, subprocess.SubprocessError) as exc:
            raise ValueError('FFmpeg indisponível ou excedeu 60 segundos') from exc
        if processed.returncode or not processed.stdout:
            raise ValueError('FFmpeg não conseguiu processar a variante')
        audio = np.frombuffer(processed.stdout, dtype='<f4').reshape(-1, audio.shape[1]).copy()
        length = output_samples(len(samples), effect)
        # atempo pode arredondar blocos; o contrato temporal é exato em amostras.
        audio = np.pad(audio[:length], ((0, max(0, length-len(audio))), (0, 0)))
    return np.ascontiguousarray(audio[::-1] if effect['reverse'] else audio)


class VariantAPI:
    def get_variant_capabilities(self):
        return capabilities(self._audio.ffmpeg_path)

    def _variant_context(self, clip_id, options, revision_id=None):
        effect = validate(options)
        with self._store._lock:
            context = self._store.render_context(clip_id)
            if not context or context.get('pending') or not context.get('_revision'):
                raise ValueError('Salve o clipe antes de criar uma variante')
            if revision_id:
                revision = self._store.state['revisions'].get(revision_id)
                if not revision or revision['source_id'] != context['source_id']:
                    raise ValueError('Revisão escolhida não pertence à origem deste clipe')
                context['_revision'] = copy.deepcopy(revision)
            revision = context['_revision']
            if revision.get('variant'):
                raise ValueError('Sem encadear variantes: selecione o clipe original')
            revision['variant_origin'] = {'clip_id': clip_id, 'revision_id': revision['id'], 'source_id': context['source_id']}
            revision['variant'] = effect
            revision['variant_processor'] = PROCESSOR_VERSION
            revision['id'] = uuid.uuid4().hex
            return context

    def create_audio_variant(self, clip_id, options, revision_id=None):
        handle = None
        try:
            with self._store._lock:
                context = self._variant_context(clip_id, options, revision_id)
                handle = self._store.acquire_source(context['source_id'])
            from clip_renderer import render_revision
            from loudness import analyze_audio_loudness
            audio = render_revision(context['_source'], context['_revision'], self._audio.ffmpeg_path)
            measured = analyze_audio_loudness(audio * float(context.get('volume', 1)), sr=SAMPLE_RATE)
            revision = context['_revision']; revision['duration'] = len(audio)/SAMPLE_RATE
            child = {k: copy.deepcopy(v) for k,v in context.items() if not k.startswith('_')}
            for key in ('voice_action_id', 'montage_identity', 'analysis_proposals', 'analysis_generations', 'pack_identity'):
                child.pop(key, None)
            child.update(id=uuid.uuid4().hex, revision_id=revision['id'], hotkey='', play_count=0,
                title=(child.get('title') or 'Áudio') + ' — variante', effective_duration=revision['duration'],
                lufs=measured['lufs'], lufs_estimated=measured['lufs_estimated'])
            child['transcript'] = revision_text(context, revision)
            with self._store._lock:
                self._store.state['revisions'][revision['id']] = revision
                self._store.state['clips'].append(child)
                try:
                    self._store.save()
                except Exception:
                    self._store.state['clips'].remove(child)
                    self._store.state['revisions'].pop(revision['id'], None)
                    raise
            return {'ok': True, 'clip': copy.deepcopy(child)}
        except (ValueError, TypeError, KeyError, OSError, RuntimeError) as exc:
            return {'ok': False, 'error': str(exc)}
        finally:
            if handle:
                self._store.release_source(handle)

    def stop_variant_preview(self, token):
        if not isinstance(token, str) or not token or len(token) > 200:
            return {'ok': False, 'error': 'Token de prévia inválido'}
        with self._operation_lock:
            owner = self._variant_previews.pop(token, None)
            if owner:
                owner['cancel'].set()
            else:
                self._operation_released[token] = True
                if len(self._operation_released) > 4096:
                    self._operation_released.popitem(last=False)
        if owner:
            self._audio.stop_group(owner['id'], fade_ms=0)
        return {'ok': True}

    def preview_audio_variant(self, clip_id, options, revision_id=None, token=None):
        handle = None
        owner = None
        try:
            token = token or 'variant:' + uuid.uuid4().hex
            if not isinstance(token, str) or len(token) > 200:
                raise ValueError('Token de prévia inválido')
            with self._operation_lock:
                if token in self._operation_released:
                    self._operation_released.pop(token)
                    return {'ok': False, 'cancelled': True, 'error': 'Prévia cancelada antes do preparo'}
                if token in self._variant_previews:
                    raise ValueError('Prévia já solicitada com este token')
                settings, _, epoch = self._operation_request(True)
                self._validate_rehearsal(settings)
                owner = {'id': uuid.uuid4().hex, 'cancel': threading.Event()}
                self._variant_previews[token] = owner
                self._audio.register_group_owner(owner['id'], owner['cancel'].set, expected_epoch=epoch)
            with self._store._lock:
                context = self._variant_context(clip_id, options, revision_id)
                handle = self._store.acquire_source(context['source_id'])
            context['_release_source'] = lambda *_: self._store.release_source(handle)
            def finish(_event):
                self._audio.unregister_group_owner(owner['id'])
                with self._operation_lock:
                    if self._variant_previews.get(token) is owner:
                        self._variant_previews.pop(token, None)
            if owner['cancel'].is_set():
                raise ValueError('Prévia cancelada')
            prepared = self._audio.prepare_group(owner['id'], [{'clip': context}], settings,
                monitor_only=True, on_complete=finish)
            if owner['cancel'].is_set():
                raise ValueError('Prévia cancelada')
            self._audio.start_group(prepared)
            return {'ok': True, 'play_id': prepared.voices[0].play_id, 'token': token}
        except (ValueError, TypeError, KeyError, OSError, RuntimeError) as exc:
            cancelled = bool(owner and owner['cancel'].is_set())
            if owner:
                self._audio.stop_group(owner['id'], fade_ms=0)
                with self._operation_lock:
                    if self._variant_previews.get(token) is owner:
                        self._variant_previews.pop(token, None)
            if handle:
                self._store.release_source(handle)
            return {'ok': False, 'cancelled': cancelled, 'error': str(exc)}
