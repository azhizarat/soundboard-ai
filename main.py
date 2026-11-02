import os
import sys
import copy
import math
import time
import atexit
import argparse
import logging
import shutil
import threading
import uuid
import webbrowser
import zipfile
from pathlib import Path
from functools import wraps

import config
import doctor
import loudness
import packs
from store import SoundboardStore
from ai_analyzer import AIAnalyzer, DiarizationError
from audio_engine import AudioEngine, compute_waveform_peaks, device_names_match
from hotkeys import HotkeyManager
from analysis_jobs import AnalysisQueue, AnalysisPausedError
from voice_service import VoiceLearningAPI
from sequence_service import SequenceAPI
from sequence_runner import SequenceRunner
from operation_service import OperationAPI
from audio_variants import VariantAPI
from import_queue import ImportAPI

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

SUPPORTED_AUDIO_EXTS = packs.AUDIO_EXTS
# Formats that only work when FFmpeg can convert them to PCM.
CONVERT_ONLY_EXTS = {".m4a", ".webm", ".aac", ".wma", ".opus"}
# Fields a normal editor may change. Identity, audio location and analysis
# artifacts are server-owned: a client must not be able to publish a draft or
# repoint a clip at an arbitrary file.
EDITABLE_CLIP_FIELDS = (
    "title",
    "emoji",
    "color",
    "category",
    "speaker_id",
    "speaker_ids",
    "hotkey",
    "transcript",
    "trim_start",
    "trim_end",
    "volume",
    "acoustic_mood",
    "lufs",
    "loudness_gain_db",
    "lufs_estimated",
)


def _unlink_quiet(path) -> None:
    """Best-effort delete: temp cleanup must never raise."""
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        pass


def _as_float(value, default: float, lo: float, hi: float) -> float:
    """Coerces a persisted setting to a clamped float, falling back on corrupt data."""
    try:
        num = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(num):
        return default
    return max(lo, min(hi, num))



def _device_name(entries: list[dict], dev_id) -> str:
    for entry in entries or []:
        if entry.get("id") == dev_id:
            return entry.get("name") or ""
    return ""


def _dev_names_match(a: str, b: str) -> bool:
    return device_names_match(a, b)


def _resolve_device(
    entries: list[dict],
    stored_id,
    stored_name,
    role_fallback_id=None,
) -> dict | None:
    """Resolves a saved device against live entries using the durable name.

    Device indices are unstable across reboots/USB changes, so a saved *name* is
    the only trusted identity. A legacy/empty name means the stored index is
    untrusted: ignore it and fall back to the device detected for that role.
    """
    wanted_name = (stored_name or "").strip()
    if not wanted_name:
        if role_fallback_id is None:
            return None
        for entry in entries or []:
            if entry.get("id") == role_fallback_id:
                return entry
        return None

    name_match = None
    for entry in entries or []:
        entry_name = str(entry.get("name", ""))
        if not _dev_names_match(entry_name, wanted_name):
            continue
        if entry.get("id") == stored_id:
            return entry
        if name_match is None:
            name_match = entry
    return name_match


def _serialized_configuration(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        # Persistência e publicação compartilham ordem; nunca adquirir lock do store aqui.
        with self._config_lock:
            return method(self, *args, **kwargs)
    return wrapped


class SoundboardAPI(VoiceLearningAPI, SequenceAPI, OperationAPI, VariantAPI, ImportAPI):
    def __init__(self, data_dir: str = None, start_background_services: bool = True,
                 defer_ai_prewarm: bool = False):
        self._startup_at = time.perf_counter()
        self._ui_ready_reported = False
        # All internal state attributes MUST start with '_' so pywebview does not
        # recursively inspect/serialize them (especially self._window.native!).
        self._config = config.load_config(data_dir)
        self._config_lock = threading.RLock()
        self._prewarm_enabled = start_background_services
        self._prewarm_started = False
        self._store = SoundboardStore(data_dir=str(self._config.data_dir))
        self._store.ffmpeg_path = self._config.ffmpeg_path
        # Replay drafts live in data/tmp and are throwaway: never survive a restart.
        self._store.purge_pending_clips()
        if start_background_services:
            atexit.register(self._store.flush)
        self._ai = AIAnalyzer(
            str(self._config.models_dir),
            ffmpeg_path=self._config.ffmpeg_path,
            ollama_url=self._config.ollama_url,
            ollama_model=self._config.ollama_model,
        )
        self._configure_transcription()
        self._inference_lock = threading.RLock()
        self._analysis_queue = AnalysisQueue(capacity=8)
        self._analysis_queue.set_policy(self._config.analysis_profile, self._config.analysis_paused)
        self._voices().recover_interrupted_learning()
        if start_background_services:
            atexit.register(self._analysis_queue.close)
        self._audio = AudioEngine(
            replay_seconds=max(60.0, float(self._store.state["settings"].get("replay_seconds", 15))),
            start_loopback=start_background_services,
            ffmpeg_path=self._config.ffmpeg_path,
            mixer_mode=self._config.mixer_mode,
            loopback_source=str(self._store.state["settings"].get("replay_source_name") or ""),
        )
        if start_background_services:
            # Close PortAudio handles explicitly: leaving them to the interpreter
            # makes the virtual-cable endpoints end abruptly on exit.
            atexit.register(self._audio.shutdown)
        self._window = None
        self._is_mini_overlay = False
        self._cable_warned = False
        self._init_operation()

        self._auto_bind_devices()

        self._hotkeys = HotkeyManager(
            on_play_clip=self._trigger_clip_hotkey,
            on_stop_all=lambda: self.stop_all(from_hotkey=True),
            on_save_replay=self.capture_replay_clip,
            on_toggle_overlay=self.toggle_mini_overlay,
            on_sequence=self._trigger_sequence_hotkey,
            sequence_provider=lambda: list(self._store.snapshot()['sequences'].values()),
            on_collection=self._dispatch_collection_hotkey,
            collection_provider=self._active_collection,
        )
        self._sequence_runner = SequenceRunner(self._store, self._audio)
        if start_background_services:
            atexit.register(self._sequence_runner.close)
        if start_background_services:
            self._hotkeys.refresh(self._public_clips(self._store.state["clips"]), self._store.state["settings"])
            if self._store.state["settings"].get("mic_passthrough"):
                self._apply_mic_passthrough()
            if not defer_ai_prewarm:
                self._start_ai_prewarm()

    def _set_window(self, window):
        self._window = window

    def notify_ui_ready(self) -> dict:
        """A UI solicita aquecimento após pintar; replay e atalhos já estão ativos."""
        with self._config_lock:
            if not self._ui_ready_reported:
                logging.info('Startup: UI interativa em %.3f s desde o início da API',
                             time.perf_counter() - self._startup_at)
                self._ui_ready_reported = True
        self._start_ai_prewarm()
        return {'ok': True}

    def _start_ai_prewarm(self):
        with self._config_lock:
            if self._prewarm_enabled and not self._prewarm_started:
                threading.Thread(target=self._prewarm_ai, daemon=True, name='AIPrewarm').start()
                self._prewarm_started = True

    def _prewarm_ai(self):
        try:
            if not self._analysis_queue.allows():
                return
            self._ai._get_extractor()
            self._ai._get_diarizer()
            self._ai._get_whisper()
        except Exception:
            pass

    def _analyze_audio(self, *args, **kwargs) -> dict:
        with getattr(self, '_inference_lock', threading.RLock()):
            self._require_explicit_analysis()
            result = self._ai.analyze_file(*args, **kwargs)
        if result.get("analysis_status") == "decode_error":
            error = result.get("error") or "Falha ao decodificar áudio"
            self._notify_ui("toast", {"type": "warning", "message": error})
            raise ValueError(error)
        for warning in result.get("warnings") or []:
            self._notify_ui("toast", {"type": "warning", "message": warning})
        return result

    def _get_speaker_names_map(self) -> dict:
        return {k: v.get("name", k) for k, v in self._store.state["speakers"].items()}

    @staticmethod
    def _public_clips(clips: list[dict]) -> list[dict]:
        """Drafts are internal: the UI and hotkeys never see them."""
        return [c for c in clips if isinstance(c, dict) and not c.get("pending")]

    def _reject_pending(self, clip_id: str, action: str = "") -> dict | None:
        """Blocks destructive analysis on a replay draft (it is not saved yet)."""
        clip = self._store.get_clip(clip_id)
        if clip and clip.get("pending"):
            return {"ok": False, "error": "Rascunhos de replay não podem ser alterados assim."}
        return None

    def _auto_bind_devices(self):
        dev_info = self._audio.list_devices()
        # Snapshot da enumeração que resolveu as rotas iniciais, exclusivo do bootstrap.
        self._startup_devices = copy.deepcopy(dev_info)
        settings = self._store.state["settings"]
        outputs = dev_info.get("outputs") or []
        inputs = dev_info.get("inputs") or []
        updates = {}

        def _bind(id_key, name_key, entries, fallback_id):
            stored_id = settings.get(id_key)
            stored_name = settings.get(name_key) or ""
            resolved = _resolve_device(entries, stored_id, stored_name, fallback_id)
            if resolved is not None:
                new_id = resolved.get("id")
                new_name = resolved.get("name") or ""
                if stored_id != new_id:
                    updates[id_key] = new_id
                if stored_name != new_name:
                    updates[name_key] = new_name
                return
            if stored_id is not None or stored_name.strip():
                # Configured device is gone: keep the saved name for a later re-resolve,
                # but never silently route to whatever index happens to exist now.
                if stored_id is not None:
                    updates[id_key] = None
                return
            # Unconfigured (legacy install with empty name): adopt the role default.
            if fallback_id is not None:
                updates[id_key] = fallback_id
                updates[name_key] = _device_name(entries, fallback_id)

        _bind(
            "cable_device_id",
            "cable_device_name",
            outputs,
            dev_info.get("detected_cable_id"),
        )
        # The Windows default output is frequently the cable itself. Adopting it
        # as the "fone" route would send the monitor audio back into the cable,
        # which VB-Cable accepts and silently discards.
        monitor_default = dev_info.get("default_output_id")
        monitor_entry = next(
            (d for d in outputs if d.get("id") == monitor_default), None
        )
        if monitor_entry and monitor_entry.get("is_cable"):
            monitor_default = None
        _bind(
            "monitor_device_id",
            "monitor_device_name",
            outputs,
            monitor_default,
        )
        _bind(
            "mic_device_id",
            "mic_device_name",
            inputs,
            dev_info.get("default_input_id"),
        )

        if updates:
            self._store.update_settings(updates)

    def _apply_ducking(self):
        """Pushes ducking settings only — never touches the mic stream."""
        s = self._store.state["settings"]
        self._audio.set_ducking(
            bool(s.get("duck_enabled", False)),
            _as_float(s.get("duck_amount_db"), -12.0, -24.0, 0.0),
            _as_float(s.get("duck_threshold_dbfs"), -40.0, -60.0, -10.0),
        )

    def _apply_mic_passthrough(self):
        self._apply_ducking()
        s = self._store.state["settings"]
        enabled = bool(s.get("mic_passthrough", False))
        if enabled:
            # A missing cable is not fatal: silence the route and warn once.
            try:
                cable_ok = self._capabilities().get("cable_routing", True)
            except Exception:
                cable_ok = True
            if not cable_ok:
                enabled = False
                if not self._cable_warned:
                    self._cable_warned = True
                    self._notify_ui("toast", {
                        "type": "warning",
                        "message": "VB-Cable não encontrado: encaminhamento do microfone desativado.",
                    })
        self._audio.set_mic_passthrough(
            enabled=enabled,
            mic_id=s.get("mic_device_id"),
            cable_id=s.get("cable_device_id"),
            gain=_as_float(s.get("mic_gain", 1.0), 1.0, 0.0, 3.0),
        )

    def _notify_ui(self, event_name: str, payload: dict = None):
        if self._window is None:
            return
        try:
            import json
            js_payload = json.dumps(payload or {}, ensure_ascii=False)
            self._window.evaluate_js(
                f"window.onBackendEvent && window.onBackendEvent({json.dumps(event_name)}, {js_payload});"
            )
        except Exception:
            pass

    def _emit_progress(self, active: bool, current: int = 0, total: int = 1, label: str = "", detail: str = ""):
        pct = min(100, int(round((current / max(1, total)) * 100))) if active else 100
        self._notify_ui("progress", {
            "active": active,
            "current": current,
            "total": total,
            "percent": pct,
            "label": label,
            "detail": detail,
        })

    def _discover_separados(self) -> list[dict]:
        discovered_separados = []
        videos_root = self._config.separados_root
        try:
            if videos_root.exists():
                for sub in sorted(videos_root.iterdir()):
                    if sub.is_dir() and (sub / "separados" / "mapa_de_falas.json").exists():
                        discovered_separados.append({
                            "label": f"Videos/{sub.name}/separados",
                            "path": str((sub / "separados").resolve()),
                        })
        except Exception:
            pass
        return discovered_separados

    def _library_state(self, devices) -> dict:
        snap = self._store.snapshot()
        return {
            "settings": snap["settings"],
            "categories": snap["categories"],
            "speakers": snap["speakers"],
            "clips": self._public_clips(snap["clips"]),
            "devices": copy.deepcopy(devices),
            "discovered_separados": [],
            "models_dir": str(self._config.models_dir),
            "has_titanet": None,
            "has_ref_voices": None,
            "is_mini_overlay": self._is_mini_overlay,
            "environment": None,
            "config": config.config_dict(self._config),
            "analysis_jobs": self._analysis_queue.list(),
            "transcription_runtime": copy.deepcopy(self._ai.transcription_runtime),
            "playback_capabilities": self.get_playback_capabilities(),
        }

    def get_bootstrap_state(self) -> dict:
        """Biblioteca pronta para interação sem probes, scans ou nova enumeração."""
        return {**self._library_state(self._startup_devices), 'startup_details_pending': True}

    def get_startup_details(self) -> dict:
        """Enriquecimento independente; uma falha de diagnóstico não bloqueia a biblioteca."""
        started_at = time.perf_counter()
        details = {'startup_details_pending': False, 'environment': None,
                   'discovered_separados': self._discover_separados(),
                   'has_titanet': None, 'has_ref_voices': None}
        try:
            details['has_titanet'] = self._ai.emb_model_path.exists()
            details['has_ref_voices'] = self._ai.ref_voices_path.exists()
            details['environment'] = doctor.snapshot(self._config)
        except Exception:
            details['startup_details_error'] = 'Não foi possível concluir a verificação do ambiente.'
        logging.info('Startup: detalhes concluídos em %.3f s; erro=%s',
                     time.perf_counter() - started_at, bool(details.get('startup_details_error')))
        return details

    def get_initial_state(self) -> dict:
        # Mantém o endpoint completo e a enumeração atual para refreshs existentes.
        return {**self._library_state(self._audio.list_devices()),
                'discovered_separados': self._discover_separados(),
                'has_titanet': self._ai.emb_model_path.exists(),
                'has_ref_voices': self._ai.ref_voices_path.exists(),
                'environment': doctor.snapshot(self._config)}

    def get_environment_status(self, refresh=False) -> dict:
        return doctor.snapshot(self._config, refresh=bool(refresh))

    def refresh_environment(self) -> dict:
        self._cable_warned = False
        doctor.invalidate_cache()
        return doctor.snapshot(self._config, refresh=True)

    @_serialized_configuration
    def save_app_config(self, updates: dict) -> dict:
        try:
            self._config = config.save_config(self._config, updates or {})
        except Exception as exc:
            return {"ok": False, "error": f"Não foi possível salvar a configuração: {exc}"}
        self._apply_config()
        doctor.invalidate_cache()
        environment = doctor.snapshot(self._config, refresh=True)
        self._notify_ui("environment_updated", environment)
        return {
            "ok": True,
            "config": config.config_dict(self._config),
            "environment": environment,
        }

    def _apply_config(self) -> None:
        """Rebuilds everything that depends on config paths without restarting."""
        self._cable_warned = False
        self._ai = AIAnalyzer(
            str(self._config.models_dir),
            ffmpeg_path=self._config.ffmpeg_path,
            ollama_url=self._config.ollama_url,
            ollama_model=self._config.ollama_model,
        )
        profiles = self._voices()
        with profiles.transaction():
            profiles._invalidate_matching()
        self._audio.ffmpeg_path = self._config.ffmpeg_path
        self._store.ffmpeg_path = self._config.ffmpeg_path
        self._configure_transcription()
        # Re-applying keeps the route in sync with the new devices/config; only
        # touch the mic stream when the user actually enabled it.
        if self._store.state["settings"].get("mic_passthrough"):
            self._apply_mic_passthrough()

    def _configure_transcription(self):
        options = {key: getattr(self._config, 'asr_' + key)
                   for key in ('model', 'device', 'beam_size', 'language', 'hotwords', 'initial_prompt')}
        terms = self._config.personal_dictionary
        options['hotwords'] = ', '.join(filter(None, [self._config.asr_hotwords, ', '.join(terms)]))
        self._ai.configure_transcription(options)
        if hasattr(self, '_analysis_queue'):
            self._analysis_queue.set_policy(self._config.analysis_profile, self._config.analysis_paused)

    def _require_explicit_analysis(self):
        queue = getattr(self, '_analysis_queue', None)
        if queue and not queue.allows(explicit=True):
            if queue.policy()['paused']:
                raise AnalysisPausedError('Análises pausadas; retome em Qualidade local para executar esta tarefa')
            raise ValueError('Fila de análise encerrada')

    def get_local_quality(self):
        from analysis_cache import DECODE_CACHE
        return {'ok': True, 'policy': self._analysis_queue.policy(),
                'dictionary': list(self._config.personal_dictionary),
                'dictionary_provenance': 'user_confirmed_hotwords',
                'cache': {'inference': self._ai.cache.stats(), 'decode': DECODE_CACHE.stats()},
                'jobs': self._analysis_queue.list(),
                'quality_calibration': 'pending_labeled_corpus'}

    @_serialized_configuration
    def set_resource_policy(self, profile='reviewing', paused=False):
        try:
            self._config = config.save_config(self._config, {'analysis_profile': profile, 'analysis_paused': paused})
            self._analysis_queue.set_policy(profile, paused)
            return self.get_local_quality()
        except (ValueError, OSError) as exc:
            return {'ok': False, 'error': str(exc)}

    def set_analysis_priority(self, analysis_id, prioritized=True):
        return {'ok': self._analysis_queue.reprioritize(analysis_id, prioritized)}

    @_serialized_configuration
    def save_personal_dictionary(self, terms, confirmed=False):
        if confirmed is not True:
            return {'ok': False, 'error': 'Confirme explicitamente os termos pessoais'}
        try:
            self._config = config.save_config(self._config, {'personal_dictionary': terms})
            self._configure_transcription()
            return self.get_local_quality()
        except (ValueError, OSError) as exc:
            return {'ok': False, 'error': str(exc)}

    @staticmethod
    def _metadata_speaker_name(context, token, names):
        provenance = token.get('field_provenance', {})
        # Uma atribuição humana é intenção explícita, não uma previsão a recalcular.
        if provenance.get('speaker_id') == 'human':
            return names.get(context.get('speaker_id'), '')
        if provenance.get('speaker_ids') == 'human':
            return ', '.join(names[sid] for sid in context.get('speaker_ids', []) if sid in names)
        spans = context['_revision']['spans']
        retained = set()
        for segment in context.get('segments', []):
            start, end = segment.get('start_sample'), segment.get('end_sample')
            if not isinstance(start, int) or not isinstance(end, int) or start >= end:
                continue
            if segment.get('source_id', token['source_id']) != token['source_id']:
                continue
            if segment.get('speaker_id') and any(start < hi and end > lo for lo, hi in spans):
                retained.add(segment['speaker_id'])
        # Sem evidência temporal ou com vários interlocutores, omite o nome.
        # O campo antigo do clipe não demonstra quem ficou na montagem atual.
        return names.get(next(iter(retained)), '') if len(retained) == 1 else ''

    def request_clip_analysis(self, clip_id: str, stage='transcription', interval=None, context_seconds=0, priority=True):
        handle = None
        reserved = self._analysis_queue.reserve()
        if not reserved['ok']:
            return reserved
        try:
            if interval is not None and stage != 'transcription':
                raise ValueError('Seleção temporal é exclusiva da transcrição')
            with self._store._lock:
                token = self._store.prepare_analysis(clip_id, stage, interval)
                context = self._store.render_context(clip_id, draft=True)
                transcript = self._store.get_transcript(clip_id)
                handle = self._store.acquire_source(token['source_id'])
                names = self._get_speaker_names_map()
                use_llm = bool(self._store.state['settings'].get('use_local_llm', True))
            analyzer = self._ai
            from importlib.metadata import version
            parameters = copy.deepcopy(analyzer.transcription_config) if stage == 'transcription' else {}
            requested = {**token, 'model': parameters.pop('model', stage),
                         'model_version': 'faster-whisper/' + version('faster-whisper') if stage == 'transcription' else 'local-v1',
                         'parameters': {**parameters, 'word_timestamps': True} if stage == 'transcription' else {},
                         'device': None, 'fallback_reason': None}
            def run():
                from ai_analyzer import generate_smart_metadata, analyze_acoustics
                from audio_processing import resample_audio
                from clip_renderer import render_revision, file_hash
                import soundfile as sf
                import numpy as np
                with self._inference_lock:
                    identity = {k: token[k] for k in ('analysis_id', 'source_id', 'revision_id', 'interval', 'stage')}
                    if stage == 'metadata':
                        meta = generate_smart_metadata(transcript['text'], context.get('acoustic_mood', 'Normal'),
                                                       self._metadata_speaker_name(context, token, names),
                                                       sum(b-a for a,b in context['_revision']['spans']) / 48000,
                                                       use_llm=use_llm, transcription_status='provided')
                        return {**identity, 'status': 'ready', 'fields': meta,
                                'metadata_source': meta['metadata_source'], 'fallback_reason': meta['fallback_reason'],
                                'model': self._config.ollama_model if meta['metadata_source'] == 'local_llm' else 'heuristic',
                                'model_version': 'metadata-v1', 'parameters': {'use_llm': use_llm}, 'error': None}
                    source = context['_source']
                    if stage == 'speakers':
                        from analysis_cache import project_voice_embeddings
                        reusable = project_voice_embeddings(context.get('segments', []), source['id'],
                            context['_revision']['spans'], analyzer.extractor_version)
                        if reusable is not None:
                            if file_hash(source['file_path']) != source['sha256']:
                                raise ValueError('A fonte foi alterada')
                            mapped = []
                            for segment in reusable:
                                decision = self._voices().match(segment['voice_embedding'], analyzer.extractor_version)
                                mapped.append({**segment, 'speaker_id': decision['speaker_id'], 'identity': decision,
                                               'id': token['analysis_id'] + ':s' + str(len(mapped)),
                                               'embedding_provenance': 'retained_complete_source_segment'})
                            speakers = list(dict.fromkeys(s['speaker_id'] for s in mapped if s.get('speaker_id')))
                            return {**identity, 'status': 'ready',
                                'fields': {'segments': mapped, 'speaker_ids': speakers, 'speaker_id': speakers[0] if speakers else ''},
                                'model': 'retained_source_embeddings', 'model_version': analyzer.extractor_version,
                                'parameters': {'reused_complete_segments': True}, 'error': None}
                    if stage == 'transcription':
                        margin = round(max(0, min(5, float(context_seconds))) * 48000)
                        lo, hi = max(0, token['interval'][0] - margin), min(source['total_samples'], token['interval'][1] + margin)
                        if file_hash(source['file_path']) != source['sha256']:
                            raise ValueError('A fonte foi alterada')
                        data, rate = sf.read(source['file_path'], start=lo, stop=hi, dtype='float32', always_2d=True)
                        if rate != 48000:
                            raise ValueError('Referencial PCM inválido')
                        result = analyzer.transcribe_temporal(resample_audio(data.mean(axis=1), rate, 16000),
                                                              token['analysis_id'], token['source_id'], [lo, hi])
                        return {**result, **identity, 'context_interval': [lo, hi]}
                    revision = {**context['_revision'], 'gain_db': 0, 'normalization_gain': 1, 'fade_samples': 0}
                    audio = resample_audio(render_revision(source, revision).mean(axis=1), 48000, 16000)
                    if stage == 'acoustics':
                        return {**identity, 'status': 'ready', 'fields': {'acoustic_mood': analyze_acoustics(audio)['mood']},
                                'model': 'acoustic_heuristic', 'model_version': '1', 'parameters': {}, 'error': None}
                    if analyzer._get_diarizer() is None:
                        return {**identity, 'status': 'failed', 'error': {'code': 'model_unavailable', 'message': 'Modelo local de pessoas indisponível'}}
                    try:
                        segments = analyzer.diarize_and_identify(audio, names, transcribe_segments=False)
                    except DiarizationError as exc:
                        return {**identity, 'status': 'failed', 'error': exc.as_dict()}
                    mapped = []
                    def source_ranges(start, end):
                        offset, ranges = 0, []
                        for lo, hi in revision['spans']:
                            left, right = max(start, offset), min(end, offset + hi-lo)
                            if left < right:
                                ranges.append([lo + left-offset, lo + right-offset])
                            offset += hi-lo
                        return ranges
                    for segment in segments:
                        a, b = round(segment['start'] * 48000), round(segment['end'] * 48000)
                        evidence = [span for start, end in segment.get('embedding_intervals', [[segment['start'],segment['end']]])
                                    for span in source_ranges(round(start*48000),round(end*48000))]
                        for lo, hi in source_ranges(a,b):
                            mapped.append({**segment, 'start_sample': lo, 'end_sample': hi,
                                           'embedding_source_id': token['source_id'], 'embedding_source_spans': evidence,
                                           'id': token['analysis_id'] + ':s' + str(len(mapped))})
                    speakers = list(dict.fromkeys(s['speaker_id'] for s in mapped if s.get('speaker_id')))
                    return {**identity, 'status': 'ready' if mapped else 'no_speech',
                            'fields': {'segments': mapped, 'speaker_ids': speakers, 'speaker_id': speakers[0] if speakers else ''},
                            'model': 'pyannote-segmentation-3-0/titanet', 'model_version': 'local', 'parameters': {}, 'error': None}
            queued = self._analysis_queue.submit(run, apply=lambda result: self._store.apply_analysis(token, result),
                                                 priority=0 if priority else 10, metadata=requested,
                                                 cleanup=lambda: self._store.release_source(handle), reservation=reserved['reservation'])
            if not queued['ok']:
                self._store.release_source(handle)
                self._store.invalidate_analysis(token)
            return queued
        except Exception as exc:
            if handle:
                self._store.release_source(handle)
            return {'ok': False, 'error': str(exc), 'error_code': 'invalid_request'}
        finally:
            self._analysis_queue.release_reservation(reserved['reservation'])

    def get_analysis_job(self, analysis_id: str):
        job = self._analysis_queue.get(analysis_id)
        if not job:
            return {'ok': False, 'error': 'Análise não encontrada'}
        if job.get('application') and job.get('clip_id'):
            # A fila guarda o resultado inicial; o aceite posterior é persistido no store.
            with self._store._lock:
                clip = self._store.get_clip(job['clip_id']) or {}
                application = clip.get('analysis_proposals', {}).get(analysis_id)
                if application:
                    job['application'] = {'state': application['state']}
                    if application['state'] == 'proposal':
                        job['application']['accept_token'] = application['accept_token']
                elif job['application'].get('state') == 'proposal':
                    job['application'] = {'state': 'obsolete'}
        error = job.get('error')
        return {'ok': True, **job, 'analysis_status': 'partial' if job['status'] == 'failed' else job['status'],
                'warnings': [error['message']] if isinstance(error, dict) else []}

    def list_analysis_jobs(self, clip_id=None):
        return {'ok': True, 'jobs': [j for j in self._analysis_queue.list() if clip_id is None or j.get('clip_id') == clip_id]}

    def cancel_analysis_job(self, analysis_id: str):
        job = self._analysis_queue.get(analysis_id)
        if job and job.get('stage') == 'voice_learning':
            return self.cancel_voice_learning(analysis_id)
        cancelled = self._analysis_queue.cancel(analysis_id)
        if cancelled and all(key in job for key in ('clip_id', 'stage', 'generation')):
            self._store.invalidate_analysis(job)
        return {'ok': cancelled}

    def accept_analysis_result(self, clip_id: str, analysis_id: str, accept_token: str):
        try:
            result = self._store.accept_analysis(clip_id, analysis_id, accept_token)
            return {'ok': result['state'] == 'applied', **result,
                    'clip': self._store.render_context(clip_id, draft=True)}
        except (OSError, ValueError) as exc:
            return {'ok': False, 'error': str(exc)}

    def get_clip_transcript(self, clip_id: str):
        try:
            return {'ok': True, 'transcript': self._store.get_transcript(clip_id)}
        except ValueError as exc:
            return {'ok': False, 'error': str(exc)}

    def correct_clip_transcript(self, clip_id: str, word_ids: list, text: str):
        try:
            return {'ok': True, 'transcript': self._store.correct_transcript(clip_id, word_ids, text)}
        except (OSError, ValueError) as exc:
            return {'ok': False, 'error': str(exc)}

    def pick_directory(self, kind: str) -> dict:
        if kind not in ("models_dir", "separados_root"):
            return {"ok": False, "error": "Pasta desconhecida."}
        if self._window is None:
            return {"ok": False, "error": "Janela indisponível."}
        try:
            import webview
            chosen = self._window.create_file_dialog(webview.FOLDER_DIALOG)
            path = chosen if isinstance(chosen, str) else (chosen[0] if chosen else None)
        except Exception as exc:
            return {"ok": False, "error": f"Não foi possível abrir o seletor: {exc}"}
        if not path:
            return {"ok": False, "error": "Seleção cancelada."}
        return self.save_app_config({kind: path})

    def open_help_url(self, url: str) -> dict:
        safe = doctor.safe_help_url(url)
        if not safe:
            return {"ok": False, "error": "Link não permitido."}
        try:
            webbrowser.open(safe)
        except Exception as exc:
            return {"ok": False, "error": f"Não foi possível abrir o link: {exc}"}
        return {"ok": True}

    def _capabilities(self) -> dict:
        return doctor.capabilities(doctor.run_checks(self._config))

    def _capability_error(self, name: str) -> dict | None:
        if self._capabilities().get(name):
            return None
        messages = {
            "ai_analysis": "Análise de IA indisponível: coloque os modelos em data/models ou abra ⚙️ Ambiente.",
            "audio_convert": "Conversão de áudio indisponível: instale o FFmpeg para importar este formato.",
            "cable_routing": "VB-Cable não encontrado: sem rota para o cabo virtual.",
        }
        return {"ok": False, "error": messages.get(name, "Recurso indisponível.")}

    def refresh_devices(self) -> dict:
        # Enumerar reconexões não altera a escolha nem retoma sons cancelados.
        return self._audio.list_devices()

    def list_replay_sources(self) -> dict:
        """Outputs the replay can record (WASAPI loopback endpoints)."""
        return self._audio.list_loopback_sources()

    def get_replay_status(self) -> dict:
        """What the replay is recording right now, with a live peak level."""
        return self._audio.loopback_status()

    def _apply_device_names(self, updates: dict):
        """Keeps the saved device names in sync when a device is selected by index."""
        keys = {
            "cable_device_id": ("cable_device_name", "outputs"),
            "monitor_device_id": ("monitor_device_name", "outputs"),
            "mic_device_id": ("mic_device_name", "inputs"),
        }
        pending = [k for k in keys if k in updates]
        if not pending:
            return
        dev_info = self._audio.list_devices()
        for id_key in pending:
            name_key, list_key = keys[id_key]
            dev_id = updates.get(id_key)
            if dev_id is None:
                # Explicit "no device": keep the saved name for a later re-resolve.
                continue
            matched = False
            for entry in dev_info.get(list_key) or []:
                if entry.get("id") == dev_id:
                    updates[name_key] = entry.get("name") or ""
                    matched = True
                    break
            if not matched:
                # Stale index: clear the old name so the next refresh cannot
                # silently rebind the saved name to a different device.
                updates[name_key] = ""

    def update_settings(self, updates: dict) -> dict:
        with getattr(self, '_operation_lock', threading.RLock()):
            if getattr(self, '_operation_transition', False):
                return {'ok': False, 'error': 'Mudança de modo em andamento; aguarde a confirmação'}
            return self._update_settings_locked(updates)

    def _update_settings_locked(self, updates):
        updates = dict(updates or {})
        try:
            self._store.validate_settings_bindings(updates)
            if getattr(self, '_operation_rehearsal', False):
                self._validate_rehearsal({**self._store.snapshot()['settings'], **updates})
        except ValueError as exc:
            return {'ok': False, 'error': str(exc)}
        if any(k in updates for k in ('voice_match_threshold', 'voice_match_margin')):
            from speaker_matching import match_embedding
            settings = {**self._store.state['settings'], **updates}
            match_embedding([], {}, settings['voice_match_threshold'], settings['voice_match_margin'])
        if "replay_seconds" in updates:
            updates["replay_seconds"] = round(max(1.0, min(120.0, float(updates["replay_seconds"]))), 1)
        if any(k in updates for k in ("cable_device_id", "monitor_device_id", "mic_device_id")):
            self._apply_device_names(updates)
        if "replay_source_name" in updates:
            updates["replay_source_name"] = str(updates["replay_source_name"] or "").strip()
        try:
            s = self._voices().update_match_settings(updates) if any(k in updates for k in ('voice_match_threshold', 'voice_match_margin')) else self._store.update_settings(updates)
        except ValueError as exc:
            return {'ok': False, 'error': str(exc)}
        if any(k in updates for k in ('cable_device_id', 'monitor_device_id')):
            with self._operation_lock:
                self._audio.stop_all()
                self._operation_route_fault = ''
                self._operation_failure_seen = self._audio.output_failure()[0]
                self._check_operation_routes()
        if "replay_source_name" in updates:
            self._audio.set_loopback_source(s.get("replay_source_name") or "")
        if "replay_seconds" in updates:
            self._audio.replay_buffer.set_max_seconds(max(60.0, float(s.get("replay_seconds", 15))))
        if any(k in updates for k in ("mic_passthrough", "mic_device_id", "cable_device_id", "mic_gain")):
            self._apply_mic_passthrough()
        if any(k in updates for k in ("stop_all_hotkey", "replay_hotkey", "overlay_hotkey")):
            self._hotkeys.refresh(self._public_clips(self._store.snapshot()["clips"]), s)
        return s

    def set_ducking(self, payload=None, amount_db=None, threshold_dbfs=None) -> dict:
        """Updates ducking settings.

        Accepts the JS call style ``set_ducking({...})``, the positional style
        ``set_ducking(True, -6.0, -35.0)`` and keyword overrides.
        """
        if isinstance(payload, dict):
            data = dict(payload)
        else:
            data = {}
            if payload is not None:
                data["enabled"] = payload
        if amount_db is not None:
            data["amount_db"] = amount_db
        if threshold_dbfs is not None:
            data["threshold_dbfs"] = threshold_dbfs

        updates = {}
        if "enabled" in data:
            enabled = data.get("enabled")
            if isinstance(enabled, str):
                # JS/property payloads arrive as strings: "false" must disable.
                updates["duck_enabled"] = enabled.strip().lower() in ("1", "true", "yes", "on")
            else:
                updates["duck_enabled"] = bool(enabled)
        if data.get("amount_db") is not None:
            try:
                updates["duck_amount_db"] = max(-24.0, min(0.0, float(data["amount_db"])))
            except (TypeError, ValueError):
                return {"ok": False, "error": "Intensidade de ducking inválida."}
        if data.get("threshold_dbfs") is not None:
            try:
                updates["duck_threshold_dbfs"] = max(-60.0, min(-10.0, float(data["threshold_dbfs"])))
            except (TypeError, ValueError):
                return {"ok": False, "error": "Limiar de ducking inválido."}

        settings = self._store.snapshot()["settings"]
        if not updates:
            return {"ok": True, "settings": settings}
        self._store.update_settings(updates)
        # Ducking-only change: the mic stream must keep running.
        self._apply_ducking()
        return {"ok": True, "settings": self._store.snapshot()["settings"]}

    def play_clip(self, clip_id: str, monitor_only: bool = False, from_hotkey: bool = False) -> dict:
        try:
            request = self._operation_request(monitor_only)
        except ValueError as exc:
            return {'ok': False, 'error': str(exc)}
        return self._play_clip_request(clip_id, monitor_only, from_hotkey, request)

    def _trigger_clip_hotkey(self, clip_id):
        try:
            request = self._operation_request()
        except ValueError as exc:
            self._notify_ui('toast', {'type': 'warning', 'message': str(exc)})
            return
        threading.Thread(target=lambda: self._play_clip_request(clip_id, False, True, request),
                         daemon=True, name='ClipHotkeyStart').start()

    def _play_clip_request(self, clip_id, monitor_only, from_hotkey, request):
        settings, routed_monitor, request_epoch = request
        clip = self._store.render_context(clip_id, draft=bool(monitor_only))
        if not clip:
            return {"ok": False, "error": "Clip não encontrado"}

        file_path = clip.get("file_path") or ""
        if not file_path or not os.path.exists(file_path):
            error = "Arquivo de áudio não encontrado."
            if from_hotkey:
                self._notify_ui("toast", {"type": "warning", "message": error})
            return {"ok": False, "error": error}

        eff_dur = max(
            0.2,
            float(clip.get("trim_end") or clip.get("duration") or 1.5) - float(clip.get("trim_start") or 0.0),
        )
        handle = self._store.acquire_source(clip['source_id']) if clip.get('source_id') else None
        def release_source(*_args):
            if handle:
                self._store.release_source(handle)
        clip['_release_source'] = release_source
        try:
            play_id = self._audio.play_clip(
                clip=clip, settings=settings, on_finish=release_source,
                on_tick=None, monitor_only=routed_monitor, expected_epoch=request_epoch)
        except Exception as exc:
            release_source()
            return {'ok': False, 'error': f'Não foi possível reproduzir o áudio: {exc}'}
        if not play_id:
            release_source()
            # No active/valid output route (muted cable+monitor, stale device, ...).
            error = "Nenhuma saída de áudio ativa (cabo/fone mudos ou dispositivo indisponível)."
            try:
                from mixer import is_cable_output_device

                if is_cable_output_device(settings.get("monitor_device_id")):
                    error = (
                        "A saída 'Fone' está apontando para o CABLE Input: monitorar no cabo "
                        "não gera áudio. Escolha seus fones de verdade."
                    )
            except Exception:
                pass
            if from_hotkey:
                self._notify_ui("toast", {"type": "warning", "message": error})
            return {"ok": False, "error": error}
        if not monitor_only:
            self._store.increment_play_count(clip_id)
        sess = self._audio.get_session(play_id)
        initial_status = sess.status() if sess else {
            "playing": True,
            "clip_id": clip_id,
            "play_id": play_id,
            "title": clip.get("title") or "Áudio",
            "emoji": clip.get("emoji") or "🔊",
            "position": 0.0,
            "duration": eff_dur,
            "paused": False,
            "loop": False,
            "volume": float(clip.get("volume", 1.0)),
            "monitor_only": routed_monitor,
        }
        if from_hotkey:
            self._notify_ui("clip_started", initial_status)
        return {"ok": True, "play_id": play_id, "status": initial_status}

    def pause_resume_playback(self, play_id: str = None) -> dict:
        sess = self._audio.get_session(play_id)
        if not sess:
            return {"ok": False}
        paused = sess.toggle_pause()
        st = sess.status()
        return {"ok": True, "paused": paused, "status": st}

    def seek_playback(self, position_s: float, play_id: str = None) -> dict:
        sess = self._audio.get_session(play_id)
        if not sess:
            return {"ok": False}
        sess.seek(position_s)
        st = sess.status()
        return {"ok": True, "status": st}

    def set_playback_loop(self, loop: bool, play_id: str = None) -> dict:
        sess = self._audio.get_session(play_id)
        if not sess:
            return {"ok": False}
        loop_val = sess.set_loop(loop)
        st = sess.status()
        return {"ok": True, "loop": loop_val, "status": st}

    def set_live_clip_volume(self, volume: float, play_id: str = None) -> dict:
        sess = self._audio.get_session(play_id)
        if not sess:
            return {"ok": False}
        vol = sess.set_live_volume(volume)
        self._store.set_clip_volume(sess.clip_id, vol)
        st = sess.status()
        return {"ok": True, "volume": vol, "status": st}

    def restart_playback(self, play_id: str = None) -> dict:
        sess = self._audio.get_session(play_id)
        if not sess:
            return {"ok": False}
        sess.restart()
        st = sess.status()
        return {"ok": True, "status": st}

    def get_clip_waveform(self, clip_id: str, num_bins: int = 160) -> dict:
        clip = self._store.render_context(clip_id)
        if not clip or not clip.get("file_path") or not os.path.exists(clip["file_path"]):
            return {"ok": False, "peaks": []}
        try:
            if clip.get('_revision'):
                raw_audio = self._audio.render_clip(clip)
                dur = len(raw_audio) / 48000
            else:
                raw_audio, dur = self._audio.load_clip_stereo_48k(clip["file_path"])
            peaks = compute_waveform_peaks(raw_audio, num_bins=num_bins)
            return {"ok": True, "peaks": peaks, "duration": round(dur, 2)}
        except Exception:
            return {"ok": False, "peaks": []}

    def stop_all(self, from_hotkey: bool = False) -> dict:
        self._audio.stop_all(fade_ms=0)
        self._notify_ui("stopped_all", {})
        return {"ok": True}

    def get_playback_capabilities(self) -> dict:
        return self._audio.playback_capabilities()

    def stop_playback_group(self, group_id: str) -> dict:
        capabilities = self.get_playback_capabilities()
        if not capabilities['groups']:
            return {'ok': False, 'error': capabilities['unavailable_reason']}
        if not isinstance(group_id, str) or not group_id:
            return {'ok': False, 'error': 'group_id obrigatório'}
        self._audio.stop_group(group_id)
        return {'ok': True}

    def _calculate_clip_loudness(
        self, file_path: str, trim_start: float = 0.0, trim_end: float = 0.0
    ) -> dict:
        try:
            samples, dur = self._audio.load_clip_stereo_48k(file_path)
            if samples is None or len(samples) == 0:
                return {"lufs": None, "loudness_gain_db": 0.0, "lufs_estimated": False}
            sr = int(self._audio.TARGET_SR)
            t_start = max(0.0, float(trim_start or 0.0))
            t_end = float(trim_end or 0.0)
            if t_end <= 0.0 or t_end > dur:
                t_end = dur
            if t_end > t_start:
                i_start = int(t_start * sr)
                i_end = int(t_end * sr)
                samples = samples[i_start:i_end]
            return loudness.analyze_audio_loudness(samples, sr=sr)
        except Exception:
            return {"lufs": None, "loudness_gain_db": 0.0, "lufs_estimated": False}

    def reanalyze_clip_loudness(self, clip_id: str) -> dict:
        clip = self._store.get_clip(clip_id)
        if not clip:
            return {"ok": False, "error": "Clip não encontrado."}
        file_path = clip.get("file_path", "")
        if not file_path or not os.path.exists(file_path):
            return {"ok": False, "error": "Arquivo de áudio não encontrado."}
        context = self._store.render_context(clip_id)
        if context.get('_revision'):
            try:
                samples = self._audio.render_clip(context)
                loud_info = loudness.analyze_audio_loudness(samples, sr=48000)
            except (OSError, ValueError, RuntimeError) as exc:
                return {'ok': False, 'error': f'Não foi possível medir a revisão: {exc}'}
        else:
            loud_info = self._calculate_clip_loudness(
                file_path, trim_start=float(clip.get('trim_start', 0.0)),
                trim_end=float(clip.get('trim_end', 0.0)))
        updated = self._store.add_or_update_clip({"id": clip_id, **loud_info})
        snap = self._store.snapshot()
        self._notify_ui("clips_updated", {
            "clips": self._public_clips(snap["clips"]),
        })
        return {"ok": True, "clip": copy.deepcopy(updated)}

    def save_clip_edits(self, clip_data: dict) -> dict:
        data = dict(clip_data or {})
        if 'hotkey' in data:
            try:
                self._store.validate_binding_change('clip', data.get('id'), data['hotkey'])
            except ValueError as exc:
                return {'ok': False, 'error': str(exc)}
        # Whitelist: a draft must not be publishable (pending/file_path/duration/...).
        payload = {"id": data.get("id")}
        for key in EDITABLE_CLIP_FIELDS:
            if key in data:
                payload[key] = data[key]
        clip_id = payload.get("id")
        existing = self._store.get_clip(clip_id) if clip_id else None
        previous = copy.deepcopy(existing or {})
        self._preserve_voice_evidence(previous)
        if existing:
            self._preserve_voice_evidence(self._store.render_context(clip_id, draft=True))
        if existing and existing.get('revision_id') and clip_id not in self._store.snapshot()['edit_drafts']:
            opened = self.begin_clip_edit(clip_id)
            if not opened.get('ok'):
                return opened
        if existing and clip_id in self._store.snapshot()['edit_drafts']:
            try:
                self._store.update_edit(clip_id, payload)
                updated = self._store.commit_edit(clip_id)
            except (OSError, ValueError, RuntimeError) as exc:
                return {'ok': False, 'error': f'Não foi possível salvar a edição: {exc}'}
            snap = self._store.snapshot()
            self._hotkeys.refresh(self._public_clips(snap['clips']), snap['settings'])
            learning = self._learn_correction(previous, self._store.get_clip(clip_id), data.get('voice_interval')) if previous.get('speaker_id') != updated.get('speaker_id') else {}
            return {'ok': True, 'clip': copy.deepcopy(self._store.get_clip(clip_id)), 'speakers': snap['speakers'], **learning}
        if existing and existing.get("file_path"):
            trim_start = float(payload.get("trim_start", existing.get("trim_start", 0.0)))
            trim_end = float(payload.get("trim_end", existing.get("trim_end", 0.0)))
            loud_info = self._calculate_clip_loudness(
                existing["file_path"], trim_start=trim_start, trim_end=trim_end
            )
            payload.update(loud_info)
        updated = self._store.add_or_update_clip(payload)
        snap = self._store.snapshot()
        self._hotkeys.refresh(self._public_clips(snap["clips"]), snap["settings"])
        learning = self._learn_correction(previous, updated, data.get('voice_interval')) if previous.get('speaker_id') != updated.get('speaker_id') else {}
        return {"ok": True, "clip": copy.deepcopy(updated), "speakers": snap["speakers"], **learning}

    def begin_clip_edit(self, clip_id: str) -> dict:
        try:
            self._store.begin_edit(clip_id)
            draft = self._store.reopen_edit(clip_id)
            clip = copy.deepcopy(self._store.get_clip(clip_id))
            clip.update(draft['fields'])
            return {'ok': True, 'clip': clip, 'draft': draft}
        except (OSError, ValueError, RuntimeError) as exc:
            return {'ok': False, 'error': f'Não foi possível abrir a edição: {exc}'}

    def list_clip_edits(self) -> dict:
        snapshot = self._store.snapshot()
        clips = {clip['id']: clip for clip in snapshot['clips']}
        return {'ok': True, 'clips': [{**clips[key], **draft['fields']} for key, draft in
                                    snapshot['edit_drafts'].items() if key in clips]}

    def update_clip_edit(self, clip_data: dict) -> dict:
        try:
            draft = self._store.update_edit(str(clip_data.get('id') or ''), clip_data)
            return {'ok': True, 'draft': draft}
        except (OSError, ValueError, RuntimeError) as exc:
            return {'ok': False, 'error': f'Não foi possível preservar a edição: {exc}'}

    def discard_clip_edit(self, clip_id: str) -> dict:
        try:
            return {'ok': self._store.discard_edit(clip_id)}
        except OSError as exc:
            return {'ok': False, 'error': f'Não foi possível descartar a edição: {exc}'}

    def get_edit_source_waveform(self, clip_id: str, num_bins: int = 160) -> dict:
        # Os controles legados de corte ainda usam a régua da fonte, não da montagem.
        context = self._store.render_context(clip_id, draft=True)
        if not context or not context.get('_source'):
            return self.get_clip_waveform(clip_id, num_bins)
        try:
            from clip_renderer import render_revision
            source = context['_source']
            revision = {**context['_revision'], 'spans': [[0, source['total_samples']]],
                        'variant': {}, 'gain_db': 0, 'normalization_gain': 1, 'fade_samples': 0}
            data = render_revision(source, revision)
            return {'ok': True, 'peaks': compute_waveform_peaks(data, num_bins), 'duration': source['duration']}
        except (OSError, ValueError, RuntimeError):
            return {'ok': False, 'peaks': []}

    def get_clip_editor(self, clip_id: str) -> dict:
        try:
            import editor_service
            editor = editor_service.state(self._store, clip_id)
            context = self._store.render_context(clip_id, True)
            samples = self._audio.render_clip(context) * float(context.get('volume', 1))
            measurement = loudness.analyze_audio_loudness(samples, sr=48000)
            editor['lufs'] = measurement['lufs']
            editor['lufs_estimated'] = measurement['lufs_estimated']
            return {'ok': True, 'editor': editor}
        except (OSError, ValueError, RuntimeError, TypeError, KeyError) as exc:
            return {'ok': False, 'error': str(exc)}

    def edit_clip_command(self, clip_id: str, action: str, payload: dict = None) -> dict:
        try:
            import editor_service
            created = editor_service.command(self._store, clip_id, action, payload or {})
            result = self.get_clip_editor(clip_id)
            if created:
                result['clip'] = created
            return result
        except (OSError, ValueError, RuntimeError, TypeError, KeyError) as exc:
            return {'ok': False, 'error': str(exc)}

    def suggest_clip_edges(self, clip_id: str, margin: float = .15) -> dict:
        try:
            from editor_service import suggest
            return {'ok': True, **suggest(self._store.render_context(clip_id, True), margin)}
        except (OSError, ValueError, RuntimeError, TypeError, KeyError) as exc:
            return {'ok': False, 'error': str(exc)}

    def preview_clip_edit(self, clip_id: str, mode: str = 'selection', interval: list = None,
                          junction: int = 0, loop: bool = False) -> dict:
        handle = None
        try:
            request_settings, _, request_epoch = self._operation_request(True)
            from editor_service import preview_ranges
            from clip_edits import validate_spans
            with self._store._lock:
                context = self._store.render_context(clip_id, True)
                if not context or not context.get('_revision'):
                    raise ValueError('Abra uma montagem antes de ouvir')
                if interval is not None:
                    validate_spans([interval], context['_source']['total_samples'])
                ranges = preview_ranges(context['_revision']['spans'], mode, interval, junction)
                if context['_revision'].get('variant'):
                    from audio_variants import map_ranges
                    ranges = map_ranges(ranges, sum(b-a for a,b in context['_revision']['spans']), context['_revision']['variant'])
                if not ranges:
                    raise ValueError('A seleção não contém amostras após o efeito; amplie o intervalo')
                context['_preview_ranges'] = ranges
                handle = self._store.acquire_source(context['source_id'])
            def release(*_args):
                self._store.release_source(handle)
            context['_release_source'] = release
            # Audição do editor é independente da política pública de sobreposição.
            # Uma prévia (mesmo falha) não deve interromper transmissão ou monitor externo.
            preview_settings = {**request_settings, 'overlap_audio': True}
            play_id = self._audio.play_clip(clip=context, settings=preview_settings,
                                            monitor_only=True, on_finish=release, on_tick=None, expected_epoch=request_epoch)
            if not play_id:
                release()
                raise ValueError('Nenhum monitor disponível para ouvir a seleção')
            session = self._audio.get_session(play_id)
            if session:
                session.set_loop(bool(loop))
            return {'ok': True, 'play_id': play_id, 'duration': sum(b-a for a,b in ranges)/48000,
                    'ranges': ranges, 'status': session.status() if session else None}
        except (OSError, ValueError, RuntimeError, TypeError, KeyError) as exc:
            if handle:
                self._store.release_source(handle)
            return {'ok': False, 'error': str(exc)}

    def get_editor_playback(self, play_id: str) -> dict:
        session = self._audio.get_session(play_id)
        return {'ok': True, 'status': session.status() if session else {'playing': False}}

    def stop_editor_preview(self, play_id: str) -> dict:
        session = self._audio.get_session(play_id)
        if session and session.monitor_only:
            session.stop()
        return {'ok': True}

    def toggle_favorite(self, clip_id: str) -> dict:
        clip = self._store.get_clip(clip_id)
        if not clip:
            return {"ok": False}
        updated = self._store.add_or_update_clip({"id": clip_id, "favorite": not clip.get("favorite", False)})
        return {"ok": True, "clip": copy.deepcopy(updated)}

    def delete_clip(self, clip_id: str, delete_file: bool = False) -> dict:
        clip = self._store.get_clip(clip_id)
        file_path = (clip or {}).get("file_path") or ""
        try:
            ok = self._store.delete_clip(clip_id, delete_file=delete_file)
        except ValueError as exc:
            return {'ok': False, 'error': str(exc)}
        if file_path:
            self._audio.invalidate_cache(file_path)
        snap = self._store.snapshot()
        self._hotkeys.refresh(self._public_clips(snap["clips"]), snap["settings"])
        return {
            "ok": bool(ok),
            "clip": copy.deepcopy(clip) if ok and clip else None,
            "can_undo": bool(ok and clip and not delete_file),
        }

    def restore_clip(self, clip: dict) -> dict:
        """Restores a soft-deleted clip (undo). The audio file must still exist."""
        payload = copy.deepcopy(clip or {})
        clip_id = str(payload.get("id") or "").strip()
        empty = self._store.snapshot()
        if not clip_id:
            return {"ok": False, "error": "Clipe inválido.", "clips": self._public_clips(empty["clips"]), "speakers": empty["speakers"]}
        if self._store.get_clip(clip_id):
            return {
                "ok": False,
                "error": "Este botão já existe.",
                "clips": self._public_clips(empty["clips"]),
                "speakers": empty["speakers"],
            }

        raw_path = str(payload.get("file_path") or "")
        clips_root = self._store.clips_dir.resolve()
        try:
            resolved = Path(raw_path).resolve() if raw_path else None
        except Exception:
            resolved = None
        # Undo must never resurrect a clip pointing at an arbitrary file, nor at
        # a directory (including clips_dir itself).
        if resolved is None or not resolved.is_relative_to(clips_root) or not resolved.is_file():
            return {
                "ok": False,
                "error": "Arquivo do áudio não encontrado.",
                "clips": self._public_clips(empty["clips"]),
                "speakers": empty["speakers"],
            }

        payload["file_path"] = str(resolved)
        saved = self._store.add_or_update_clip(payload)
        snap = self._store.snapshot()
        self._hotkeys.refresh(self._public_clips(snap["clips"]), snap["settings"])
        return {
            "ok": True,
            "clip": copy.deepcopy(saved),
            "clips": self._public_clips(snap["clips"]),
            "speakers": snap["speakers"],
        }

    def update_speaker(self, spk_id: str, name: str, color: str = None, avatar: str = None) -> dict:
        spk = self._store.update_speaker(spk_id, name=name, color=color, avatar=avatar)
        snap = self._store.snapshot()
        return {"ok": True, "speaker": copy.deepcopy(spk), "speakers": snap["speakers"]}

    def delete_speaker(self, spk_id: str) -> dict:
        ok = self._voices().delete_person(spk_id)
        snap = self._store.snapshot()
        return {
            "ok": ok,
            "speakers": snap["speakers"],
            "clips": self._public_clips(snap["clips"]),
        }

    def enroll_speaker_from_clip(self, clip_id: str, spk_id: str, speaker_name: str = "", interval=None) -> dict:
        return self._enroll_voice(clip_id, spk_id, speaker_name, interval)

    def reanalyze_clip_ai(self, clip_id: str) -> dict:
        blocked = self._capability_error("ai_analysis")
        if blocked:
            return blocked
        rejected = self._reject_pending(clip_id, "reanalisar")
        if rejected:
            return rejected
        # Compatibilidade do nome público; o trabalho agora pode ser consultado/cancelado.
        return self.request_clip_analysis(clip_id, 'transcription')

    def split_clip_by_speakers(self, clip_id: str) -> dict:
        blocked = self._capability_error("ai_analysis")
        if blocked:
            return blocked
        import soundfile as sf
        from ai_analyzer import load_audio_mono_16k

        rejected = self._reject_pending(clip_id, "separar falas")
        if rejected:
            return rejected
        clip = self._store.get_clip(clip_id)
        if not clip or not clip.get("file_path") or not os.path.exists(clip["file_path"]):
            return {"ok": False, "error": "Arquivo de áudio não encontrado"}

        self._emit_progress(True, 1, 2, "Separando falas por voz...", clip.get("title", ""))
        try:
            spk_names = self._get_speaker_names_map()
            segments = clip.get("segments") or []
            if len(segments) <= 1:
                self._require_explicit_analysis()
                audio_16k, _ = load_audio_mono_16k(clip["file_path"])
                segments = self._ai.diarize_and_identify(audio_16k, speaker_names=spk_names)

            warnings = list(dict.fromkeys(seg["transcription_error"] for seg in segments if seg.get("transcription_error")))
            for warning in warnings:
                self._notify_ui("toast", {"type": "warning", "message": warning})
            if len(segments) <= 1:
                return {"ok": False, "error": "Apenas 1 fala/interlocutor contínuo foi detectado neste áudio.",
                        "analysis_status": "partial" if warnings else "ok", "warnings": warnings}

            stereo_data, _ = self._audio.load_clip_stereo_48k(clip["file_path"])
            sr = self._audio.TARGET_SR
            created_clips = []
            base_stem = Path(clip["file_path"]).stem
            total_segs = len(segments)

            for idx, seg in enumerate(segments, 1):
                self._emit_progress(True, idx, total_segs, f"Cortando fala {idx}/{total_segs}...", seg.get("title", ""))
                st_s = float(seg.get("start", 0.0))
                en_s = float(seg.get("end", 0.0))
                if en_s - st_s < 0.25:
                    continue
                st_idx = max(0, int(round(st_s * sr)))
                en_idx = min(len(stereo_data), int(round(en_s * sr)))
                chunk = stereo_data[st_idx:en_idx]
                if len(chunk) == 0:
                    continue

                spk_id = seg.get("speaker_id") or "interlocutor_1"
                spk_obj = self._store.ensure_speaker(spk_id)
                out_name = f"{base_stem}_part{idx:02d}_{spk_id}.wav"
                out_path = str(self._store.clips_dir / out_name)
                sf.write(out_path, chunk, sr, subtype="PCM_16")

                new_c = self._store.add_or_update_clip({
                    "title": seg.get("title") or f"{spk_obj.get('name', spk_id)} #{idx}",
                    "file_path": out_path,
                    "category": seg.get("category") or clip.get("category") or "Call",
                    "speaker_id": spk_id,
                    "speaker_ids": [spk_id],
                    "segments": [seg],
                    "transcript": seg.get("transcript") or "",
                    "acoustic_mood": seg.get("mood") or "Normal",
                    "emoji": seg.get("emoji") or "🎙️",
                    "color": spk_obj.get("color") or seg.get("color") or "#3b82f6",
                    "tags": [spk_obj.get("name", spk_id).lower(), "separado"],
                    "duration": round(en_s - st_s, 2),
                    "created_at": time.time(),
                })
                created_clips.append(new_c)

            snap = self._store.snapshot()
            self._notify_ui("clips_updated", {
                "clips": self._public_clips(snap["clips"]),
                "speakers": snap["speakers"],
            })
            return {
                "ok": True,
                "analysis_status": "partial" if warnings else "ok",
                "warnings": warnings,
                "created_count": len(created_clips),
                "created_clips": copy.deepcopy(created_clips),
                "clips": self._public_clips(snap["clips"]),
                "speakers": snap["speakers"],
            }
        except DiarizationError as exc:
            return {'ok': False, 'error': str(exc), 'error_code': exc.code,
                    'analysis_status': 'failed', 'warnings': [str(exc)]}
        finally:
            self._emit_progress(False)

    def import_audio_files(
        self,
        file_paths: list[str] = None,
        skip_speaker_id: bool = False,
        forced_category: str = None,
    ) -> dict:
        if not file_paths and self._window is not None:
            try:
                import webview
                selected = self._window.create_file_dialog(
                    webview.OPEN_DIALOG,
                    allow_multiple=True,
                    file_types=("Arquivos de Áudio (*.mp3;*.wav;*.ogg;*.flac;*.m4a)", "Todos os arquivos (*.*)"),
                )
                file_paths = list(selected) if selected else []
            except Exception:
                file_paths = []

        if not file_paths:
            return {"ok": False, "imported": []}

        imported = []
        results = []
        caps = self._capabilities()
        use_llm = bool(self._store.state["settings"].get("use_local_llm", True))
        total_files = len(file_paths)
        mode_lbl = "Importando Vinhetas / Sons" if skip_speaker_id else "Importando e analisando áudios com IA"
        try:
            for idx, src in enumerate(file_paths, 1):
                dst_path = None
                persisted = False
                item = {"source": str(src), "status": "failed", "error": None, "warnings": []}
                try:
                    src_path = Path(src)
                    if not src_path.is_file():
                        raise ValueError("Arquivo não encontrado")
                    if src_path.suffix.lower() in CONVERT_ONLY_EXTS and not caps.get("audio_convert", True):
                        raise ValueError("Instale o FFmpeg para importar este formato")
                    self._emit_progress(True, idx, total_files, f"{mode_lbl} ({idx}/{total_files})", src_path.name)
                    # Nome exclusivo: uma falha nunca remove cópia de uma importação anterior.
                    dst_path = self._store.clips_dir / f"{uuid.uuid4().hex}_{src_path.name}"
                    shutil.copy2(src_path, dst_path)
                    res = self._analyze_audio(
                        str(dst_path), speaker_names=self._get_speaker_names_map(), use_llm=use_llm,
                        skip_speaker_id=bool(skip_speaker_id), forced_category=forced_category,
                    )
                    item["warnings"] = list(res.get("warnings") or [])
                    spk_id = "" if skip_speaker_id else res.get("speaker_id", "")
                    spk_ids = [] if skip_speaker_id else (res.get("speaker_ids") or ([spk_id] if spk_id else []))
                    for sid in spk_ids:
                        self._store.ensure_speaker(sid)

                    loud_info = self._calculate_clip_loudness(str(dst_path))
                    clip = self._store.add_or_update_clip({
                        "title": res.get("title") or src_path.stem[:28],
                        "file_path": str(dst_path),
                        "category": forced_category or res.get("category") or "Call",
                        "speaker_id": spk_id,
                        "speaker_ids": spk_ids,
                        "segments": [] if skip_speaker_id else (res.get("segments") or []),
                        "transcript": res.get("transcript") or "",
                        "acoustic_mood": res.get("acoustic_mood") or "Normal",
                        "emoji": res.get("emoji") or ("🎵" if forced_category == "Vinhetas" else "🔊"),
                        "color": res.get("color") or ("#06b6d4" if forced_category == "Vinhetas" else "#3b82f6"),
                        "tags": res.get("tags") or [],
                        "duration": res.get("duration") or 1.0,
                        "created_at": time.time(),
                        **loud_info,
                    })
                    persisted = True
                    imported.append(clip)
                    item.update(status="success", clip_id=clip["id"])
                except Exception as exc:
                    item["error"] = str(exc) or "Falha ao importar arquivo"
                    if dst_path is not None and not persisted:
                        try:
                            dst_path.unlink(missing_ok=True)
                        except OSError as cleanup_error:
                            item["warnings"].append(f"Não foi possível remover a cópia incompleta: {cleanup_error}")
                results.append(item)
        finally:
            self._emit_progress(False)
        snap = self._store.snapshot()
        self._notify_ui("clips_updated", {
            "clips": self._public_clips(snap["clips"]), "speakers": snap["speakers"],
        })
        warnings = [f"{Path(item['source']).name}: {message}"
                    for item in results for message in item["warnings"] + ([item["error"]] if item["error"] else [])]
        return {"ok": bool(imported), "imported": copy.deepcopy(imported), "results": results,
                **({"warnings": warnings} if warnings else {}), "error": None if imported else ("; ".join(warnings) or "Nenhum áudio importado"),
                "clips": self._public_clips(snap["clips"]), "speakers": snap["speakers"]}

    def import_folder_or_separados(
        self,
        folder_path: str = None,
        skip_speaker_id: bool = False,
        forced_category: str = None,
    ) -> dict:
        if not folder_path and self._window is not None:
            try:
                import webview
                selected = self._window.create_file_dialog(webview.FOLDER_DIALOG)
                if selected:
                    folder_path = selected[0] if isinstance(selected, (list, tuple)) else str(selected)
            except Exception:
                folder_path = None

        if not folder_path:
            return {"ok": False, "imported": []}

        target = Path(folder_path).resolve()
        if not target.exists():
            return {"ok": False, "error": "Pasta não encontrada"}

        if not skip_speaker_id and (target / "separados" / "mapa_de_falas.json").exists():
            target = target / "separados"

        use_llm = bool(self._store.state["settings"].get("use_local_llm", True))
        if not skip_speaker_id and (target / "mapa_de_falas.json").exists():
            try:
                def _fast_analyzer(fp, existing_transcript="", existing_speaker_id="", speaker_name=""):
                    return self._analyze_audio(
                        fp,
                        existing_transcript=existing_transcript,
                        existing_speaker_id=existing_speaker_id,
                        speaker_name=speaker_name,
                        use_llm=use_llm,
                    )

                def _on_prog(cur, tot, fname):
                    self._emit_progress(True, cur, tot, f"Importando pasta separados ({cur}/{tot})", fname)

                imported = self._store.import_separados_manifest(
                    str(target),
                    analyzer_fn=_fast_analyzer,
                    progress_fn=_on_prog,
                )
            finally:
                self._emit_progress(False)

            snap = self._store.snapshot()
            self._notify_ui("clips_updated", {
                "clips": self._public_clips(snap["clips"]),
                "speakers": snap["speakers"],
            })
            return {
                "ok": True,
                "imported_count": len(imported),
                "clips": self._public_clips(snap["clips"]),
                "speakers": snap["speakers"],
            }

        audio_files = [
            str(p) for p in sorted(target.rglob("*"))
            if p.is_file() and p.suffix.lower() in SUPPORTED_AUDIO_EXTS and not p.name.endswith("_completo.mp3")
        ]
        res = self.import_audio_files(audio_files, skip_speaker_id=skip_speaker_id, forced_category=forced_category)
        res["imported_count"] = len(res.get("imported", []))
        return res

    def _capture_replay_analysis(self, out_dir: Path, last_seconds: float = None) -> dict:
        """Captures the replay buffer, runs AI and returns a clip payload (not persisted)."""
        secs = float(last_seconds or self._store.state["settings"].get("replay_seconds", 15))
        out_name = f"replay_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex}.wav"
        out_path = str(Path(out_dir) / out_name)

        self._emit_progress(True, 1, 3, f"Capturando últimos {int(secs)}s da call...", out_name)
        saved = self._audio.save_replay_wav(out_path, last_seconds=secs)
        if not saved:
            return {"ok": False, "error": "Nenhum áudio recente tocado no fone/sistema nos últimos 15s"}

        self._emit_progress(True, 2, 3, "Identificando vozes e transcrevendo com IA...", out_name)
        deferred = None
        try:
            res = self._analyze_audio(
                saved,
                speaker_names=self._get_speaker_names_map(),
                use_llm=bool(self._store.state["settings"].get("use_local_llm", True)),
            )
        except AnalysisPausedError:
            # Captura é independente da IA. Validar PCM sem chamar modelo ou LLM.
            import soundfile as sf
            import numpy as np
            try:
                audio, rate = sf.read(saved, dtype='float32', always_2d=True)
                if rate <= 0 or not len(audio) or not np.isfinite(audio).all():
                    raise ValueError('PCM vazio ou inválido')
            except (OSError, ValueError, RuntimeError) as exc:
                return {'ok': False, 'error_code': 'decode_error', 'error': f'Falha ao validar a captura: {exc}'}
            deferred = {'status': 'deferred', 'reason': 'analysis_paused',
                        'message': 'Áudio preservado. A análise foi adiada porque a IA estava pausada; edite manualmente ou retome a IA e solicite uma etapa.'}
            res = {'duration': len(audio)/rate, 'acoustic_mood': 'Não analisado',
                   'analysis_status': 'deferred', 'warnings': [deferred['message']]}
        spk_id = res.get("speaker_id", "")
        spk_ids = res.get("speaker_ids") or ([spk_id] if spk_id else [])
        for sid in spk_ids:
            self._store.ensure_speaker(sid)

        return {
            "ok": True,
            "analysis_status": res.get('analysis_status', 'ok'),
            "warnings": list(res.get('warnings') or []),
            "clip": {
                "title": res.get("title") or f"Replay {time.strftime('%H:%M:%S')}",
                "file_path": saved,
                "category": res.get("category") or "Call",
                "speaker_id": spk_id,
                "speaker_ids": spk_ids,
                "segments": res.get("segments") or [],
                "transcript": res.get("transcript") or "",
                "acoustic_mood": res.get("acoustic_mood") or "Normal",
                "emoji": res.get("emoji") or "⚡",
                "color": res.get("color") or "#f59e0b",
                "tags": res.get("tags") or ["replay"],
                "duration": res.get("duration") or secs,
                "created_at": time.time(),
                **({'capture_analysis': deferred, 'field_provenance': {
                    'title': 'capture_default', 'category': 'capture_default',
                    'transcript': 'not_analyzed', 'speaker_id': 'not_analyzed',
                    'speaker_ids': 'not_analyzed', 'acoustic_mood': 'not_analyzed'}} if deferred else {}),
            },
        }

    def capture_replay_clip(self, last_seconds: float = None) -> dict:
        """Alt+F10 path: saves the replay straight into the library."""
        try:
            captured = self._capture_replay_analysis(self._store.clips_dir, last_seconds)
            if not captured.get("ok"):
                self._notify_ui("toast", {"type": "warning", "message": captured["error"]})
                return captured
            clip = self._store.add_or_update_clip(captured["clip"])
            split_count = 0
            settings = self._store.state["settings"]
            if settings.get("auto_split_speakers") and len(clip.get("segments") or []) > 1:
                split_res = self.split_clip_by_speakers(clip["id"])
                if split_res.get("ok"):
                    split_count = int(split_res.get("created_count") or 0)
            snap = self._store.snapshot()
            self._hotkeys.refresh(self._public_clips(snap["clips"]), snap["settings"])
            self._notify_ui("replay_captured", {
                "clip": copy.deepcopy(clip),
                "clips": self._public_clips(snap["clips"]),
                "speakers": snap["speakers"],
                "warnings": captured.get('warnings', []),
            })
            result = {
                "ok": True,
                "analysis_status": captured.get('analysis_status', 'ok'),
                "warnings": captured.get('warnings', []),
                "clip": copy.deepcopy(clip),
                "clips": self._public_clips(snap["clips"]),
                "speakers": snap["speakers"],
            }
            if split_count:
                result["split_count"] = split_count
            return result
        finally:
            self._emit_progress(False)

    def capture_replay_draft(self, last_seconds: float = None) -> dict:
        """Captures a replay into data/tmp and keeps it out of the library until saved."""
        try:
            captured = self._capture_replay_analysis(self._store.tmp_dir, last_seconds)
            if not captured.get("ok"):
                return captured
            payload = dict(captured["clip"])
            payload["pending"] = True
            clip = self._store.add_or_update_clip(payload)
            snap = self._store.snapshot()
            return {
                "ok": True,
                "analysis_status": captured.get('analysis_status', 'ok'),
                "warnings": captured.get('warnings', []),
                "clip": copy.deepcopy(clip),
                "clips": self._public_clips(snap["clips"]),
                "speakers": snap["speakers"],
            }
        finally:
            self._emit_progress(False)

    def commit_replay_draft(self, clip_id: str) -> dict:
        clip = self._store.get_clip(clip_id)
        snap = self._store.snapshot()
        base = {
            "ok": False,
            "clips": self._public_clips(snap["clips"]),
            "speakers": snap["speakers"],
        }
        if not clip or not clip.get("pending"):
            return {**base, "error": "Este rascunho não existe mais."}

        src = Path(str(clip.get("file_path") or ""))
        try:
            src_ok = src.is_file() and src.resolve().is_relative_to(self._store.tmp_dir.resolve())
        except Exception:
            src_ok = False
        if not src_ok:
            return {**base, "error": "Arquivo do rascunho não encontrado."}

        # The id comes from a client payload: never let it shape the path.
        safe_id = "".join(ch for ch in str(clip_id) if ch.isalnum() or ch in "-_") or uuid.uuid4().hex[:10]
        dst = self._store.clips_dir / f"replay_{safe_id}{src.suffix or '.wav'}"
        try:
            shutil.move(str(src), str(dst))
        except OSError as exc:
            return {**base, "error": f"Falha ao salvar o replay: {exc}"}

        try:
            saved = self._store.add_or_update_clip({
                "id": clip_id,
                "file_path": str(dst),
                "pending": False,
                "created_at": time.time(),
            })
            self._audio.invalidate_cache(str(src))

            split_count = 0
            settings = self._store.state["settings"]
            if settings.get("auto_split_speakers") and len(saved.get("segments") or []) > 1:
                split_res = self.split_clip_by_speakers(saved["id"])
                if split_res.get("ok"):
                    split_count = int(split_res.get("created_count") or 0)
        except Exception:
            # Atomic commit: put the audio back in tmp and keep the clip a draft.
            self._rollback_failed_commit(clip_id, src, dst)
            snap = self._store.snapshot()
            return {
                "ok": False,
                "error": "Falha ao salvar o replay. Tente de novo.",
                "clips": self._public_clips(snap["clips"]),
                "speakers": snap["speakers"],
            }
        snap = self._store.snapshot()
        self._hotkeys.refresh(self._public_clips(snap["clips"]), snap["settings"])
        return {
            "ok": True,
            "clip": copy.deepcopy(self._store.get_clip(clip_id) or saved),
            "clips": self._public_clips(snap["clips"]),
            "speakers": snap["speakers"],
            "split_count": split_count,
        }

    def _rollback_failed_commit(self, clip_id: str, src: Path, dst: Path) -> None:
        """Best effort: audio back to tmp, clip pending again. Never raises."""
        try:
            if dst.exists() and not src.exists():
                shutil.move(str(dst), str(src))
        except OSError:
            pass
        try:
            clip = self._store.get_clip(clip_id)
            if clip is not None and not clip.get("pending"):
                self._store.add_or_update_clip({
                    "id": clip_id,
                    "file_path": str(src),
                    "pending": True,
                })
        except Exception:
            pass

    def discard_replay_draft(self, clip_id: str) -> dict:
        clip = self._store.get_clip(clip_id)
        ok = bool(clip and clip.get("pending"))
        if ok:
            self._store.delete_clip(clip_id, delete_file=False)
            self._store.delete_file_inside(self._store.tmp_dir, clip.get("file_path"))
            self._audio.invalidate_cache(clip.get("file_path") or "")
        snap = self._store.snapshot()
        return {
            "ok": ok,
            "clips": self._public_clips(snap["clips"]),
            "speakers": snap["speakers"],
        }

    def _ensure_imported_speaker(self, name: str, color: str = "") -> str:
        """Maps a pack speaker to a local one by name, creating it when missing."""
        clean = (name or "").strip()
        if not clean:
            return ""
        speakers = self._store.snapshot()["speakers"]
        for spk in speakers.values():
            if str(spk.get("name") or "").strip().casefold() == clean.casefold():
                return spk["id"]
        idx = len(speakers)
        spk_id = f"interlocutor_{idx + 1}"
        while spk_id in speakers:
            idx += 1
            spk_id = f"interlocutor_{idx + 1}"
        self._store.ensure_speaker(spk_id, default_name=clean)
        if color:
            self._store.update_speaker(spk_id, name=clean, color=color)
        return spk_id

    def export_pack(self, clip_ids: list[str] = None, path: str = None, mode=None,
                    collection_ids=None, sequence_ids=None) -> dict:
        snap = self._store.snapshot()
        clips = self._public_clips(snap["clips"])
        if clip_ids is not None:
            wanted = {str(i) for i in clip_ids}
            clips = [c for c in clips if c["id"] in wanted]
        if not clips and not (mode is not None and (collection_ids or sequence_ids)):
            return {"ok": False, "error": "Nenhum áudio para exportar.", "count": 0, "skipped": 0}

        if not path and self._window is not None:
            try:
                import webview
                chosen = self._window.create_file_dialog(
                    webview.SAVE_DIALOG,
                    save_filename=f"soundboard-pack-{time.strftime('%Y%m%d-%H%M')}.zip",
                    file_types=("Pacote do Soundboard (*.zip)",),
                )
                path = chosen if isinstance(chosen, str) else (chosen[0] if chosen else None)
            except Exception:
                path = None
        if not path:
            return {"ok": False, "error": "Exportação cancelada.", "count": 0, "skipped": 0}

        try:
            out = Path(path)
            if out.suffix.lower() != ".zip":
                out = out.with_name(out.name + ".zip")
            tmp = out.with_suffix(out.suffix + ".tmp")
        except (TypeError, ValueError):
            return {
                "ok": False,
                "error": "Falha ao exportar: caminho inválido.",
                "count": 0,
                "skipped": 0,
            }
        try:
            if mode is None:
                # Compatibilidade da API v1; a UI sempre escolhe um modo v2 explícito.
                res = packs.write_pack(tmp, [self._store.render_context(c["id"]) for c in clips], snap["speakers"])
            else:
                from pack_graph import write_graph_pack
                res = write_graph_pack(tmp, self._store, clip_ids, collection_ids, sequence_ids, mode)
            os.replace(tmp, out)
        except packs.PackError as exc:
            _unlink_quiet(tmp)
            return {"ok": False, "error": str(exc), "count": 0, "skipped": 0}
        except Exception as exc:
            _unlink_quiet(tmp)
            return {"ok": False, "error": f"Falha ao exportar: {exc}", "count": 0, "skipped": 0}
        return {"ok": True, "path": str(out), **res}

    def import_pack(self, path: str = None) -> dict:
        if not path and self._window is not None:
            try:
                import webview
                chosen = self._window.create_file_dialog(
                    webview.OPEN_DIALOG,
                    allow_multiple=False,
                    file_types=("Pacote do Soundboard (*.zip)", "Todos os arquivos (*.*)"),
                )
                path = chosen if isinstance(chosen, str) else (chosen[0] if chosen else None)
            except Exception:
                path = None
        if not path:
            snap = self._store.snapshot()
            return {
                "ok": False,
                "error": "Importação cancelada.",
                "imported": [],
                "skipped": 0,
                "warnings": [],
                "clips": self._public_clips(snap["clips"]),
                "speakers": snap["speakers"],
            }

        try:
            pack = packs.validate_pack(path)
        except packs.PackError as exc:
            snap = self._store.snapshot()
            return {
                "ok": False,
                "error": str(exc),
                "imported": [],
                "skipped": 0,
                "warnings": [],
                "clips": self._public_clips(snap["clips"]),
                "speakers": snap["speakers"],
            }

        import soundfile as sf

        manifest = pack["manifest"]
        if manifest.get('version') == 2:
            try:
                from pack_graph import import_graph_pack
                result = import_graph_pack(self._store, path)
                snap = self._store.snapshot()
                self._hotkeys.refresh(self._public_clips(snap['clips']), snap['settings'])
                self._notify_ui('clips_updated', {'clips': self._public_clips(snap['clips']), 'speakers': snap['speakers']})
                return {**result, 'clips': self._public_clips(snap['clips']), 'speakers': snap['speakers']}
            except Exception as exc:
                return {'ok': False, 'error': str(exc), 'imported': [], 'skipped': 0, 'warnings': []}
        entries = manifest["clips"]
        speakers_meta = {
            str(s.get("name") or "").casefold(): s
            for s in (manifest.get("speakers") or [])
            if isinstance(s, dict)
        }
        existing = self._store.snapshot()
        used_hotkeys = {
            str(c.get("hotkey") or "").lower()
            for c in self._public_clips(existing["clips"])
            if c.get("hotkey")
        }
        known_identities = {c.get('pack_identity') for c in existing['clips'] if c.get('pack_identity')}

        imported, warnings = [], []
        skipped = 0
        total = len(entries)
        remaining = packs.MAX_UNCOMPRESSED_BYTES
        copied_names: set[str] = set()
        try:
            with zipfile.ZipFile(path) as archive:
                for idx, entry in enumerate(entries, 1):
                    self._emit_progress(True, idx, total, f"Importando pacote ({idx}/{total})", entry.get("title") or "")
                    name = str(entry.get("file") or "")
                    label = entry.get("title") or name
                    if name in copied_names:
                        skipped += 1
                        warnings.append(f"{label}: áudio repetido no pacote.")
                        continue
                    ext = Path(name).suffix.lower()
                    if ext not in packs.AUDIO_EXTS:
                        skipped += 1
                        warnings.append(f"{label}: formato não suportado.")
                        continue

                    new_id = uuid.uuid4().hex[:10]
                    final = self._store.clips_dir / f"{new_id}{ext}"
                    while final.exists():
                        new_id = uuid.uuid4().hex[:10]
                        final = self._store.clips_dir / f"{new_id}{ext}"
                    part = self._store.clips_dir / f"{new_id}{ext}.part"
                    try:
                        try:
                            digest, size = packs.copy_entry(archive, name, part, remaining=remaining)
                        except packs.PackError as exc:
                            skipped += 1
                            warnings.append(f"{label}: {exc}")
                            continue
                        copied_names.add(name)
                        remaining -= size

                        identity = packs.entry_identity(entry, digest)
                        if identity in known_identities:
                            skipped += 1
                            warnings.append(f"{label}: já existe na biblioteca.")
                            continue

                        os.replace(part, final)


                        try:
                            duration = float(sf.info(str(final)).duration or 0.0)
                        except Exception:
                            duration = _as_float(entry.get("duration"), 0.0, 0.0, 24 * 3600.0)
                        duration = max(0.2, duration or _as_float(entry.get("duration"), 0.2, 0.2, 24 * 3600.0))

                        spk_ids = []
                        raw_spk_names = entry.get("speaker_names")
                        for spk_name in raw_spk_names if isinstance(raw_spk_names, list) else []:
                            meta = speakers_meta.get(str(spk_name).strip().casefold(), {})
                            sid = self._ensure_imported_speaker(str(spk_name), str(meta.get("color") or ""))
                            if sid and sid not in spk_ids:
                                spk_ids.append(sid)

                        hotkey = str(entry.get("hotkey") or "").strip()
                        if hotkey and hotkey.lower() in used_hotkeys:
                            hotkey = ""
                            warnings.append(f"{label}: atalho já em uso, importado sem atalho.")
                        if hotkey:
                            used_hotkeys.add(hotkey.lower())

                        # Keep a positive, audible window inside the real duration.
                        trim_start = _as_float(entry.get("trim_start"), 0.0, 0.0, duration)
                        trim_start = min(trim_start, max(0.0, duration - 0.2))
                        trim_end = _as_float(entry.get("trim_end"), 0.0, 0.0, duration)
                        if trim_end <= trim_start:
                            trim_end = duration
                        trim_end = min(duration, max(trim_end, trim_start + 0.05))
                        volume = _as_float(entry.get("volume"), 1.0, 0.0, 3.0)
                        raw_tags = entry.get("tags")

                        try:
                            clip = self._store.add_imported_clip({
                                "id": new_id,
                                "pack_identity": identity,
                                "montage_identity": entry.get('montage_identity'),
                                "title": str(entry.get("title") or "Áudio importado")[:60],
                                "file_path": str(final),
                                "category": str(entry.get("category") or "Call"),
                                "speaker_id": spk_ids[0] if spk_ids else "",
                                "speaker_ids": spk_ids,
                                "segments": [],
                                "transcript": str(entry.get("transcript") or ""),
                                "acoustic_mood": str(entry.get("acoustic_mood") or "Normal"),
                                "emoji": str(entry.get("emoji") or "🔊")[:4],
                                "color": str(entry.get("color") or "#3b82f6"),
                                "tags": [str(t) for t in (raw_tags if isinstance(raw_tags, list) else [])][:32],
                                "hotkey": hotkey,
                                "trim_start": trim_start,
                                "trim_end": trim_end,
                                "volume": volume,
                                "favorite": bool(entry.get("favorite")),
                                "duration": duration,
                                "created_at": time.time(),
                                "pending": False,
                            }, rendered=entry.get("rendered") is True)
                        except Exception:
                            # Never leave a file the library does not reference.
                            _unlink_quiet(final)
                            raise
                        imported.append(clip)
                        known_identities.add(identity)
                    finally:
                        # The part file is gone after os.replace; on any failure it is
                        # removed here so a bad pack never leaves orphans behind.
                        _unlink_quiet(part)
        except Exception as exc:
            snap = self._store.snapshot()
            return {
                "ok": False,
                "error": f"Falha ao importar: {exc}",
                "imported": copy.deepcopy(imported),
                "skipped": skipped,
                "warnings": warnings,
                "clips": self._public_clips(snap["clips"]),
                "speakers": snap["speakers"],
            }
        finally:
            self._emit_progress(False)

        snap = self._store.snapshot()
        self._hotkeys.refresh(self._public_clips(snap["clips"]), snap["settings"])
        self._notify_ui("clips_updated", {
            "clips": self._public_clips(snap["clips"]),
            "speakers": snap["speakers"],
        })
        return {
            "ok": True,
            "imported": copy.deepcopy(imported),
            "skipped": skipped,
            "warnings": warnings,
            "clips": self._public_clips(snap["clips"]),
            "speakers": snap["speakers"],
        }

    def toggle_mini_overlay(self) -> dict:
        self._is_mini_overlay = not self._is_mini_overlay
        if self._window is not None and sys.platform == "win32":
            try:
                import ctypes
                from ctypes import wintypes

                user32 = ctypes.windll.user32
                user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
                user32.FindWindowW.restype = wintypes.HWND
                user32.ShowWindowAsync.argtypes = [wintypes.HWND, ctypes.c_int]
                user32.ShowWindowAsync.restype = wintypes.BOOL
                user32.SetWindowPos.argtypes = [
                    wintypes.HWND,
                    wintypes.HWND,
                    ctypes.c_int,
                    ctypes.c_int,
                    ctypes.c_int,
                    ctypes.c_int,
                    ctypes.c_uint,
                ]
                user32.SetWindowPos.restype = wintypes.BOOL

                hwnd = user32.FindWindowW(None, "Soundboard AI — VB-Audio Cable Studio")
                if hwnd:
                    # Restore if maximized (SW_RESTORE = 9) asynchronously on the UI thread
                    user32.ShowWindowAsync(hwnd, 9)
                    dpi = user32.GetDpiForWindow(hwnd) if hasattr(user32, "GetDpiForWindow") else 96
                    scale = (dpi / 96.0) if dpi and dpi > 0 else 1.0
                    target_w = int((420 if self._is_mini_overlay else 1280) * scale)
                    target_h = int((600 if self._is_mini_overlay else 820) * scale)
                    hwnd_insert_after = wintypes.HWND(-1 if self._is_mini_overlay else -2)
                    # SWP_NOMOVE (0x0002) | SWP_NOACTIVATE (0x0010) | SWP_ASYNCWINDOWPOS (0x4000)
                    user32.SetWindowPos(
                        hwnd,
                        hwnd_insert_after,
                        0,
                        0,
                        target_w,
                        target_h,
                        0x0002 | 0x0010 | 0x4000,
                    )
            except Exception:
                pass
        self._notify_ui("overlay_toggled", {"is_mini_overlay": self._is_mini_overlay})
        return {"ok": True, "is_mini_overlay": self._is_mini_overlay}


def parse_args(args=None):
    parser = argparse.ArgumentParser(description="Soundboard AI — VB-Audio Cable Studio")
    parser.add_argument("--minimized", action="store_true", help="Inicia minimizado na bandeja do sistema")
    parser.add_argument("--debug", action="store_true", help="Ativa modo debug e logs no console")
    parser.add_argument("--devtools", action="store_true", help="Abre o painel de desenvolvedor (DevTools) do WebView")
    parser.add_argument("--no-tray", action="store_true", help="Desativa o ícone na bandeja do sistema")
    parser.add_argument("--data-dir", type=str, default=None, help="Caminho personalizado da pasta de dados")
    return parser.parse_args(args)


def main(args=None):
    started_at = time.perf_counter()
    import webview
    from single_instance import SingleInstance
    from app_logging import setup_logging
    from tray import SoundboardTray, ensure_icon_image, ensure_ico_file

    opts = parse_args(args)

    app_cfg = config.load_config(opts.data_dir)
    log_file = setup_logging(app_cfg.data_dir, debug=opts.debug)
    logging.info(f"Soundboard AI iniciando. Log em: {log_file}")
    logging.info('Startup: configuração e imports de desktop em %.3f s desde main',
                 time.perf_counter() - started_at)

    single_inst = SingleInstance(data_dir=app_cfg.data_dir)
    if not single_inst.acquire():
        logging.warning("Outra instância do Soundboard AI já está em execução. Encerrando.")
        single_inst.notify_already_running()
        sys.exit(0)

    atexit.register(single_inst.release)

    api = SoundboardAPI(data_dir=opts.data_dir, start_background_services=True, defer_ai_prewarm=True)
    logging.info('Startup: API construída em %.3f s desde main', time.perf_counter() - started_at)
    ui_path = Path(__file__).parent.resolve() / "ui" / "index.html"
    icon_path = Path(__file__).parent.resolve() / "ui" / "icon.png"
    ensure_icon_image(icon_path)
    ico_path = ensure_ico_file(icon_path) if sys.platform == "win32" else icon_path

    window = webview.create_window(
        title="Soundboard AI — VB-Audio Cable Studio",
        url=str(ui_path),
        js_api=api,
        width=1280,
        height=820,
        min_size=(360, 460),
        background_color="#0b0f19",
        hidden=opts.minimized,
    )
    api._set_window(window)
    logging.info('Startup: janela criada em %.3f s desde main', time.perf_counter() - started_at)
    window.events.loaded += api._bind_native_drop

    tray = None
    is_exiting = False

    if not opts.no_tray:
        def on_open():
            window.show()
            window.restore()

        def on_exit():
            nonlocal is_exiting
            is_exiting = True
            if tray:
                tray.stop()
            window.destroy()

        def on_open_data_dir():
            try:
                os.startfile(str(api._config.data_dir))
            except Exception:
                import webbrowser
                webbrowser.open(api._config.data_dir.as_uri())

        tray = SoundboardTray(
            on_open=on_open,
            on_exit=on_exit,
            on_open_data_dir=on_open_data_dir,
            app_root=Path(__file__).parent.resolve(),
            icon_path=icon_path,
        )
        tray.run_detached()

        def on_closing():
            if not is_exiting:
                window.hide()
                return False
            if tray:
                tray.stop()
            return True

        window.events.closing += on_closing

    try:
        webview.start(debug=opts.devtools, icon=str(ico_path) if ico_path and ico_path.exists() else None)
    finally:
        if tray:
            tray.stop()


if __name__ == "__main__":
    main()
