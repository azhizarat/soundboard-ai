import json
from pathlib import Path
from store import SoundboardStore


def test_store_crud_and_deduplication(tmp_path):
    store = SoundboardStore(data_dir=str(tmp_path))
    c1 = store.add_or_update_clip({
        "title": "Relaxa",
        "file_path": "clips/test1.mp3",
        "category": "Call",
        "speaker_id": "interlocutor_1",
        "transcript": "Não vai esfolar, relaxe.",
    })
    assert c1["id"]
    # Re-importing same file_path updates existing clip instead of duplicating
    c2 = store.add_or_update_clip({
        "title": "Relaxa Editado",
        "file_path": "clips/test1.mp3",
        "category": "Meme",
    })
    assert c1["id"] == c2["id"]
    assert len(store.state["clips"]) == 1
    assert store.search_clips(query="esfolar")[0]["title"] == "Relaxa Editado"


def test_import_separados_skips_missing_files(tmp_path):
    sep_dir = tmp_path / "separados"
    (sep_dir / "interlocutor_1").mkdir(parents=True)
    valid_mp3 = sep_dir / "interlocutor_1" / "01.mp3"
    valid_mp3.write_bytes(b"RIFF_FAKE_AUDIO")
    manifest = [
        {"interlocutor": 1, "arquivo_trecho": "interlocutor_1/01.mp3", "duracao_s": 2.0, "transcricao": "Oi tudo bem"},
        {"interlocutor": 2, "arquivo_trecho": "interlocutor_2/missing.mp3", "duracao_s": 1.5, "transcricao": "Sumiu"},
    ]
    (sep_dir / "mapa_de_falas.json").write_text(json.dumps(manifest), encoding="utf-8")
    store = SoundboardStore(data_dir=str(tmp_path / "data"))
    imported = store.import_separados_manifest(str(sep_dir))
    assert len(imported) == 1
    assert imported[0]["speaker_id"] == "interlocutor_1"
