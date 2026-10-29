import json
from pathlib import Path
from store import SoundboardStore, DEFAULT_CATEGORIES
from ai_analyzer import generate_smart_metadata, _extract_punchy_title


def test_default_categories_structure():
    cat_ids = [c["id"] for c in DEFAULT_CATEGORIES]
    assert cat_ids == ["Rage", "Risada", "Meme", "Vinhetas", "Call"]

    rage_cat = next(c for c in DEFAULT_CATEGORIES if c["id"] == "Rage")
    assert rage_cat["name"] == "Rage"
    assert rage_cat["emoji"] == "🔥"
    assert rage_cat["color"] == "#ef4444"

    risada_cat = next(c for c in DEFAULT_CATEGORIES if c["id"] == "Risada")
    assert risada_cat["name"] == "Risadas & Pérolas"
    assert risada_cat["emoji"] == "😂"

    meme_cat = next(c for c in DEFAULT_CATEGORIES if c["id"] == "Meme")
    assert meme_cat["name"] == "Bordões & Memes"
    assert meme_cat["emoji"] == "⚡"

    vinhetas_cat = next(c for c in DEFAULT_CATEGORIES if c["id"] == "Vinhetas")
    assert vinhetas_cat["name"] == "Vinhetas & Trilhas"
    assert vinhetas_cat["emoji"] == "🎵"

    call_cat = next(c for c in DEFAULT_CATEGORIES if c["id"] == "Call")
    assert call_cat["name"] == "Papo & Resenha"
    assert call_cat["emoji"] == "💬"


def test_legacy_category_migration(tmp_path):
    # Prepare a clips.json with legacy categories Efeito and Clássico
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    legacy_state = {
        "schema_version": 2,
        "categories": [
            {"id": "Efeito", "name": "Efeito Sonoro", "emoji": "🔊"},
            {"id": "Clássico", "name": "Clássico", "emoji": "👑"},
            {"id": "Call", "name": "Fala Antiga", "emoji": "🎙️"},
        ],
        "clips": [
            {"id": "c1", "title": "Som de buzina", "category": "Efeito", "file_path": "c1.mp3"},
            {"id": "c2", "title": "Fala épica antiga", "category": "Clássico", "file_path": "c2.mp3"},
            {"id": "c3", "title": "Conversa normal", "category": "Call", "file_path": "c3.mp3"},
            {"id": "c4", "title": "Desconhecido", "category": "OutroInvalido", "file_path": "c4.mp3"},
        ],
    }
    (data_dir / "soundboard.json").write_text(json.dumps(legacy_state), encoding="utf-8")

    store = SoundboardStore(data_dir=str(data_dir))
    c1 = store.get_clip("c1")
    assert c1["category"] == "Vinhetas"

    c2 = store.get_clip("c2")
    assert c2["category"] == "Meme"

    c3 = store.get_clip("c3")
    assert c3["category"] == "Call"

    c4 = store.get_clip("c4")
    assert c4["category"] == "Call"

    # Verify categories in store
    cat_ids = [c["id"] for c in store.state["categories"]]
    assert "Efeito" not in cat_ids
    assert "Clássico" not in cat_ids
    assert set(cat_ids) == {"Rage", "Risada", "Meme", "Vinhetas", "Call"}


def test_decision_tree_rage_classification():
    meta = generate_smart_metadata(
        transcript="Não acredito mano, que cara lixo desgraça!",
        mood="Rage / Grito",
        speaker_name="Felipe",
        use_llm=False,
    )
    assert meta["category"] == "Rage"
    assert meta["emoji"] == "🔥"
    assert meta["title"].endswith("!")


def test_decision_tree_laugh_classification():
    meta1 = generate_smart_metadata(
        transcript="kkkkkkkk não tankei essa",
        mood="Risada",
        speaker_name="Cesar",
        use_llm=False,
    )
    assert meta1["category"] == "Risada"
    assert meta1["emoji"] == "😂"

    meta2 = generate_smart_metadata(
        transcript="hahahaha muito bom cara",
        mood="Calmo / Sussurro",
        speaker_name="Lucas",
        use_llm=False,
    )
    assert meta2["category"] == "Risada"
    assert meta2["emoji"] == "😂"


def test_decision_tree_vinhetas_classification():
    # Transcript with music/beat/vinheta keyword
    meta_music = generate_smart_metadata(
        transcript="solta o beat da vitória",
        mood="Normal",
        use_llm=False,
    )
    assert meta_music["category"] == "Vinhetas"
    assert meta_music["emoji"] == "🎵"

    # Non-verbal audio with sound effect mood or long duration
    meta_effect = generate_smart_metadata(
        transcript="",
        mood="Efeito / Meme",
        duration_s=3.0,
        use_llm=False,
    )
    assert meta_effect["category"] == "Vinhetas"
    assert meta_effect["emoji"] == "🎵"
    assert meta_effect["title"] == "Vinheta sonora"


def test_decision_tree_meme_classification():
    meta = generate_smart_metadata(
        transcript="Confia na call!",
        mood="Fala Animada",
        speaker_name="Matheus",
        use_llm=False,
    )
    assert meta["category"] == "Meme"
    assert meta["emoji"] == "⚡"


def test_decision_tree_call_classification():
    meta = generate_smart_metadata(
        transcript="A gente pode jogar mais uma partida depois do almoço.",
        mood="Normal",
        speaker_name="Rodrigo",
        use_llm=False,
    )
    assert meta["category"] == "Call"
    assert meta["emoji"] == "💬"


def test_extract_punchy_title_fallbacks():
    assert _extract_punchy_title("", mood="Risada", speaker_name="Lucas", category="Risada") == "Risada (Lucas)"
    assert _extract_punchy_title("", mood="Normal", speaker_name="", category="Vinhetas") == "Vinheta sonora"
    assert _extract_punchy_title("", mood="Rage / Grito", speaker_name="", category="Rage") == "Surto na call"
    assert _extract_punchy_title("", mood="Normal", speaker_name="Bia", category="Meme") == "Bordão (Bia)"
