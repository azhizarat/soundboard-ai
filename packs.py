"""Soundboard pack (.zip with manifest.json + audio/). Pure I/O: no store, no UI."""

from __future__ import annotations

import hashlib
import json
import re
import time
import zipfile
import io
from pathlib import Path, PurePosixPath

PACK_FORMAT = "soundboard-pack"
PACK_VERSION = 1
MANIFEST_NAME = "manifest.json"
MAX_UNCOMPRESSED_BYTES = 2 * 1024 ** 3
MAX_MANIFEST_BYTES = 8 * 1024 * 1024
AUDIO_EXTS = (".mp3", ".wav", ".ogg", ".flac", ".m4a", ".webm")


class PackError(Exception):
    """User-facing pack validation/IO error."""


def sha256_file(path: str | Path, chunk_size: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def entry_identity(entry: dict, audio_hash: str) -> str:
    """A identidade portátil inclui edição/metadados, nunca só os bytes da fonte."""
    payload = {key: value for key, value in entry.items() if key != 'file'}
    # WAVs regravados podem mudar cabeçalhos sem alterar PCM. A identidade portada
    # já inclui a fonte/receita original e sobrevive ao bake no pacote legado.
    media_identity = entry.get('montage_identity') or audio_hash
    return hashlib.sha256(json.dumps([media_identity, payload], sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def montage_identity(clip: dict) -> str:
    if clip.get('montage_identity') and float(clip.get('volume', 1)) == 1.0:
        return clip['montage_identity']
    revision = clip['_revision']
    recipe = {key: revision.get(key) for key in ('source_hash', 'conversion_version', 'render_version',
                                               'spans', 'gain_db', 'normalization_gain', 'fade_samples')}
    if revision.get('variant'):
        recipe.update(variant=revision['variant'], variant_processor=revision.get('variant_processor'))
    # Identidade do botão distingue também ocorrências independentes idênticas.
    payload = ['montage-v1', clip['id'], recipe, float(clip.get('volume', 1))]
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _safe_entry_name(name: str) -> str:
    """Rejects absolute paths, drive letters, backslashes and any '..' segment."""
    original = str(name or "")
    raw = original.replace("\\", "/")
    # A backslash would make the raw and normalized names diverge on some readers.
    if raw != original:
        raise PackError("Pacote inválido: caminho de arquivo suspeito.")
    if not raw or "\x00" in raw or raw.startswith("/"):
        raise PackError("Pacote inválido: caminho de arquivo suspeito.")
    pure = PurePosixPath(raw)
    if pure.is_absolute() or any(part == ".." for part in pure.parts):
        raise PackError("Pacote inválido: caminho de arquivo suspeito.")
    if raw[1:2] == ":":
        raise PackError("Pacote inválido: caminho de arquivo suspeito.")
    return raw


def _arcname(clip: dict, index: int, ext: str) -> str:
    """Portable entry name: ids are not exported, only a sanitized file stem."""
    safe = re.sub(r"[^A-Za-z0-9_-]", "", str(clip.get("id") or ""))
    return f"audio/{safe or f'clip{index:04d}'}{ext}"


def build_manifest(clips: list[dict], speakers: dict, created_at: float = None) -> dict:
    entries = []
    used_speakers: dict[str, dict] = {}
    used_names: set[str] = set()
    for index, clip in enumerate(clips, 1):
        file_path = str(clip.get("file_path") or "")
        ext = Path(file_path).suffix.lower()
        # AUDIO_EXTS is the single source of truth: never declare unreadable audio.
        if ext not in AUDIO_EXTS:
            continue
        arcname = _arcname(clip, index, ext)
        suffix = 2
        while arcname in used_names:
            arcname = _arcname(clip, index, f"_{suffix}{ext}")
            suffix += 1
        used_names.add(arcname)
        spk_ids = clip.get("speaker_ids") or ([clip.get("speaker_id")] if clip.get("speaker_id") else [])
        names = []
        for sid in spk_ids:
            spk = speakers.get(sid) or {}
            name = str(spk.get("name") or "").strip()
            if not name:
                continue
            names.append(name)
            used_speakers.setdefault(name.casefold(), {"name": name, "color": spk.get("color") or ""})
        entries.append({
            "file": arcname,
            "title": clip.get("title") or "",
            "emoji": clip.get("emoji") or "🔊",
            "category": clip.get("category") or "Call",
            "color": clip.get("color") or "#3b82f6",
            "hotkey": clip.get("hotkey") or "",
            "trim_start": float(clip.get("trim_start") or 0.0),
            "trim_end": float(clip.get("trim_end") or 0.0),
            "volume": float(clip.get("volume") if clip.get("volume") is not None else 1.0),
            "transcript": clip.get("transcript") or "",
            "acoustic_mood": clip.get("acoustic_mood") or "Normal",
            "tags": [str(t) for t in (clip.get("tags") or [])][:32],
            "duration": float(clip.get("duration") or 0.0),
            "favorite": bool(clip.get("favorite")),
            "speaker_names": names,
        })
    return {
        "format": PACK_FORMAT,
        "version": PACK_VERSION,
        "created_at": float(created_at or time.time()),
        "clips": entries,
        "speakers": list(used_speakers.values()),
    }


def write_pack(path: str | Path, clips: list[dict], speakers: dict) -> dict:
    """Writes the zip at ``path`` (caller passes a .tmp path). Unusable audio is skipped."""
    existentes = []
    for clip in clips:
        file_path = Path(str(clip.get("file_path") or ""))
        if file_path.suffix.lower() in AUDIO_EXTS and file_path.is_file():
            existentes.append(clip)
    skipped = len(clips) - len(existentes)
    if not existentes:
        raise PackError("Nenhum áudio encontrado para exportar.")
    manifest = build_manifest(existentes, speakers)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        # ``existentes`` is pre-filtered, so build_manifest keeps one entry per clip
        # in the same order: both lists line up.
        for entry, clip in zip(manifest["clips"], existentes):
            if clip.get('_revision'):
                import soundfile as sf
                from clip_renderer import render_revision
                from audio_variants import revision_text
                rendered = render_revision(clip['_source'], clip['_revision'], clip.get('_ffmpeg_path'))
                # O volume atual também é baked; o manifesto não reaplica ganhos.
                rendered *= float(clip.get('volume', 1))
                buffer = io.BytesIO()
                sf.write(buffer, rendered, 48000, format='WAV', subtype='FLOAT')
                entry.update({'trim_start': 0.0, 'trim_end': len(rendered) / 48000,
                              'duration': len(rendered) / 48000, 'volume': 1.0, 'rendered': True,
                              'montage_identity': montage_identity(clip)})
                entry['transcript'] = revision_text(clip, clip['_revision'])
                archive.writestr(entry['file'], buffer.getvalue())
            else:
                archive.write(str(clip.get("file_path")), entry["file"])
        archive.writestr(MANIFEST_NAME, json.dumps(manifest, ensure_ascii=False, indent=2))
    return {"count": len(manifest["clips"]), "skipped": skipped}


def validate_pack(path: str | Path) -> dict:
    """Validates the zip end-to-end and returns {manifest, entries: {name: size}}."""
    try:
        archive_path = Path(path)
        if not archive_path.is_file():
            raise PackError("Pacote não encontrado.")
        with zipfile.ZipFile(archive_path) as archive:
            infos = [i for i in archive.infolist() if not i.is_dir()]
            total = 0
            entries: dict[str, int] = {}
            for info in infos:
                name = _safe_entry_name(info.filename)
                if name != MANIFEST_NAME and not name.startswith("audio/"):
                    raise PackError("Pacote inválido: arquivo fora de audio/.")
                if name.startswith("audio/") and PurePosixPath(name).suffix.lower() not in AUDIO_EXTS:
                    raise PackError("Pacote inválido: formato de áudio não suportado.")
                total += int(info.file_size or 0)
                entries[name] = int(info.file_size or 0)
            if total > MAX_UNCOMPRESSED_BYTES:
                raise PackError("Pacote grande demais (limite de 2 GB).")
            if MANIFEST_NAME not in entries:
                raise PackError("Pacote inválido: manifest.json ausente.")
            if entries[MANIFEST_NAME] > MAX_MANIFEST_BYTES:
                raise PackError("Pacote inválido: manifest.json grande demais.")
            try:
                raw_manifest = archive.read(MANIFEST_NAME)
            except Exception as exc:
                raise PackError("Pacote inválido: manifest.json ilegível.") from exc
            if len(raw_manifest) > MAX_MANIFEST_BYTES:
                raise PackError("Pacote inválido: manifest.json grande demais.")
            try:
                manifest = json.loads(raw_manifest.decode("utf-8"))
            except Exception as exc:
                raise PackError("Pacote inválido: manifest.json ilegível.") from exc
    except PackError:
        raise
    except zipfile.BadZipFile as exc:
        raise PackError("Arquivo não é um pacote .zip válido.") from exc
    except (OSError, PermissionError) as exc:
        raise PackError("Pacote não pôde ser aberto.") from exc
    except Exception as exc:
        # Encrypted entries, unsupported compression methods, corrupt headers...
        raise PackError("Pacote não pôde ser aberto.") from exc

    if not isinstance(manifest, dict) or manifest.get("format") != PACK_FORMAT:
        raise PackError("Pacote inválido: formato desconhecido.")
    version = manifest.get("version")
    if version == 2 and type(version) is int:
        from pack_graph import validate_graph_pack
        return validate_graph_pack(path)
    if not isinstance(version, int) or isinstance(version, bool) or version > PACK_VERSION:
        raise PackError("Pacote criado por uma versão mais nova do Soundboard.")
    clips = manifest.get("clips")
    if not isinstance(clips, list) or not clips:
        raise PackError("Pacote vazio.")
    audio_entries = [name for name in entries if name.startswith("audio/")]
    if len(clips) > len(audio_entries):
        raise PackError("Pacote inválido: manifesto corrompido.")
    for entry in clips:
        if not isinstance(entry, dict):
            raise PackError("Pacote inválido: manifesto corrompido.")
        if 'montage_identity' in entry:
            identity = entry['montage_identity']
            if not isinstance(identity, str) or re.fullmatch(r'[0-9a-f]{64}', identity) is None:
                raise PackError('Pacote inválido: identidade de montagem inválida.')
        name = _safe_entry_name(entry.get("file") or "")
        if name not in entries:
            raise PackError("Pacote inválido: áudio declarado não existe no zip.")
    return {"manifest": manifest, "entries": entries}


def copy_entry(
    archive: zipfile.ZipFile,
    name: str,
    dst_path: Path,
    remaining: int = None,
) -> tuple[str, int]:
    """Streams one entry to ``dst_path``, hashing as it goes. Returns (sha256, bytes).

    ``remaining`` is the cumulative byte budget left for the whole import; the copy
    aborts as soon as it is exceeded.
    """
    digest = hashlib.sha256()
    total = 0
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with archive.open(name) as src, open(dst_path, "wb") as out:
            for chunk in iter(lambda: src.read(1 << 20), b""):
                total += len(chunk)
                if total > MAX_UNCOMPRESSED_BYTES:
                    raise PackError("Pacote grande demais (limite de 2 GB).")
                if remaining is not None and total > remaining:
                    raise PackError("Pacote grande demais (limite de 2 GB).")
                digest.update(chunk)
                out.write(chunk)
    except KeyError as exc:
        raise PackError(f"Pacote inválido: {name} ausente.") from exc
    except zipfile.BadZipFile as exc:
        raise PackError("Pacote corrompido.") from exc
    except PackError:
        raise
    except Exception as exc:
        # RuntimeError (encrypted), NotImplementedError (Deflate64/Zstd), CRC errors...
        raise PackError("Pacote corrompido ou não suportado.") from exc
    return digest.hexdigest(), total

