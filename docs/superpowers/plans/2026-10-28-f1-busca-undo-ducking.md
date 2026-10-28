# F1 — Busca fuzzy, undo de delete e ducking

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Entregar busca fuzzy com paleta Ctrl+K, undo de exclusão com confirmação de ações destrutivas e ducking do soundboard disparado pelo microfone.

**Architecture:** A busca é um módulo puro (`ui/fuzzy.js`, UMD) consumido por `app.js` e testável no Node. O undo é soft delete: o arquivo nunca é apagado no fluxo normal, então restaurar é reinserir o snapshot do clipe no store. O ducking vive no `AudioEngine`: o callback do mic calcula RMS e atualiza um ganho suavizado que o loop de cada clipe aplica com rampa por bloco.

**Tech Stack:** Python 3.11+ (pytest), JavaScript vanilla (Node para testes puros), pywebview.

**Spec:** `docs/superpowers/specs/2026-10-28-soundboard-roadmap-design.md` §6.

---

## Estrutura de arquivos

| Arquivo | Responsabilidade |
|---|---|
| `ui/fuzzy.js` (novo) | normalização, score, scoreClip, highlight — sem DOM |
| `tests/test_fuzzy.test.cjs` (novo) | testes Node do módulo puro |
| `ui/index.html` | carrega `fuzzy.js`; markup da paleta e dos controles de ducking |
| `ui/app.js` | filtro com score, highlight nos cards, paleta, undo, confirmação, controles de ducking |
| `ui/styles.css` | estilos da paleta, `<mark>` e do toast com ação |
| `audio_engine.py` | envelope de ducking, rampa no stream, `set_ducking` |
| `main.py` | `delete_clip` com snapshot, `restore_clip`, `set_ducking`, defaults |
| `store.py` | novos defaults de settings |
| `tests/test_f1_backend.py` (novo) | undo/restore, envelope, validação de `set_ducking` |

---

### Task 1: Módulo fuzzy puro

**Files:**
- Create: `ui/fuzzy.js`
- Test: `tests/test_fuzzy.test.cjs`

- [ ] **Step 1: Escrever o teste que falha**

```js
// tests/test_fuzzy.test.cjs
const test = require("node:test");
const assert = require("node:assert/strict");
const F = require("../ui/fuzzy.js");

test("normaliza acentos e caixa", () => {
  assert.equal(F.normalizeText("  Não   VS  Sim "), "nao vs sim");
});

test("igualdade vence prefixo", () => {
  assert.ok(F.score("nao", "não") > F.score("nao", "não vs sim"));
});

test("prefixo vence substring no meio", () => {
  assert.ok(F.score("nao", "não vs sim") > F.score("nao", "sim não"));
});

test("subsequência encontra iniciais", () => {
  assert.ok(F.score("nvs", "não vs sim") !== null);
});

test("sem correspondência retorna null", () => {
  assert.equal(F.score("xyz", "não vs sim"), null);
});

test("scoreClip pondera título acima de transcrição", () => {
  const speakers = { s1: { name: "Lucas" } };
  const titleHit = F.scoreClip({ title: "grito", transcript: "" }, "grito", speakers);
  const transcriptHit = F.scoreClip({ title: "outro", transcript: "grito" }, "grito", speakers);
  assert.ok(titleHit > transcriptHit);
});

test("scoreClip encontra nome do interlocutor", () => {
  const speakers = { s1: { name: "Lucas Barreiros" } };
  assert.ok(F.scoreClip({ title: "x", speaker_ids: ["s1"] }, "lucas", speakers) !== null);
});

test("highlight escapa HTML e marca o trecho", () => {
  const out = F.highlight("<b>Não</b> vs sim", "nao");
  assert.ok(!out.includes("<b>"));
  assert.ok(out.includes("<mark>Não</mark>"));
});

test("highlight sem query devolve texto escapado", () => {
  assert.equal(F.highlight("<img src=x>", ""), "&lt;img src=x&gt;");
});
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `node --test tests/test_fuzzy.test.cjs`
Expected: FAIL — `Cannot find module '../ui/fuzzy.js'`.

- [ ] **Step 3: Implementar `ui/fuzzy.js`**

```js
(function (global) {
  "use strict";

  function normalizeText(value) {
    return String(value ?? "")
      .normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "")
      .toLowerCase()
      .replace(/\s+/g, " ")
      .trim();
  }

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[ch]));
  }

  function isWordStart(text, index) {
    return index === 0 || /[\s\-_/]/.test(text[index - 1]);
  }

  function score(query, text) {
    const q = normalizeText(query);
    const t = normalizeText(text);
    if (!q || !t) return null;
    if (t === q) return 1000;
    if (t.startsWith(q)) return 900 - 0.5 * t.length;

    const pos = t.indexOf(q);
    if (pos >= 0) return (isWordStart(t, pos) ? 800 : 700) - 0.5 * pos;

    let qi = 0;
    let last = -1;
    let gaps = 0;
    for (let i = 0; i < t.length && qi < q.length; i++) {
      if (t[i] !== q[qi]) continue;
      if (last >= 0) gaps += i - last - 1;
      last = i;
      qi++;
    }
    if (qi < q.length) return null;
    const coverage = q.length / t.length;
    return 300 + 200 * coverage - Math.min(150, gaps);
  }

  const FIELD_WEIGHT = { title: 3, speaker: 2, transcript: 1, meta: 1 };

  function scoreClip(clip, query, speakers) {
    if (!clip) return null;
    const q = normalizeText(query);
    if (!q) return 0;
    const spkIds = clip.speaker_ids && clip.speaker_ids.length
      ? clip.speaker_ids
      : clip.speaker_id ? [clip.speaker_id] : [];
    const spkNames = spkIds.map((id) => (speakers && speakers[id] ? speakers[id].name : "")).join(" ");
    const fields = [
      ["title", clip.title],
      ["speaker", spkNames],
      ["transcript", clip.transcript],
      ["meta", [clip.category, clip.acoustic_mood, clip.hotkey, ...(clip.tags || [])].join(" ")],
    ];
    let best = null;
    for (const [name, value] of fields) {
      const s = score(q, value);
      if (s === null) continue;
      const weighted = s * FIELD_WEIGHT[name];
      if (best === null || weighted > best) best = weighted;
    }
    return best;
  }

  function buildNormalizedMap(text) {
    const original = String(text ?? "");
    const map = [];
    let out = "";
    for (let i = 0; i < original.length; i++) {
      const folded = original[i]
        .normalize("NFD")
        .replace(/[\u0300-\u036f]/g, "")
        .toLowerCase();
      for (const ch of folded) {
        out += ch;
        map.push(i);
      }
    }
    return { text: out, map: map };
  }

  function highlight(text, query) {
    const original = String(text ?? "");
    const q = normalizeText(query);
    if (!q) return escapeHtml(original);

    const norm = buildNormalizedMap(original);
    const marks = new Set();
    let pos = norm.text.indexOf(q);

    if (pos >= 0) {
      for (let i = 0; i < q.length; i++) marks.add(norm.map[pos + i]);
    } else {
      let qi = 0;
      for (let i = 0; i < norm.text.length && qi < q.length; i++) {
        if (norm.text[i] === q[qi]) {
          marks.add(norm.map[i]);
          qi++;
        }
      }
      if (qi < q.length) return escapeHtml(original);
    }

    let html = "";
    let i = 0;
    while (i < original.length) {
      if (!marks.has(i)) {
        html += escapeHtml(original[i]);
        i++;
        continue;
      }
      let j = i;
      while (j < original.length && marks.has(j)) j++;
      html += `<mark>${escapeHtml(original.slice(i, j))}</mark>`;
      i = j;
    }
    return html;
  }

  const api = { normalizeText, score, scoreClip, highlight };
  global.SBFuzzy = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
```

- [ ] **Step 4: Rodar e ver passar**

Run: `node --test tests/test_fuzzy.test.cjs`
Expected: `# pass 9`.

- [ ] **Step 5: Commit**

```bash
git add ui/fuzzy.js tests/test_fuzzy.test.cjs
git commit -m "feat(ui): add pure fuzzy search helpers"
```

---

### Task 2: Busca integrada e highlight nos cards

**Files:**
- Modify: `ui/index.html` (carregar `fuzzy.js` antes de `app.js`)
- Modify: `ui/app.js` (`getFilteredClips`, render do card)
- Modify: `ui/styles.css` (`mark`)

- [ ] **Step 1: Carregar o módulo**

Em `ui/index.html`, antes de `<script src="app.js"></script>`:

```html
<script src="fuzzy.js"></script>
<script src="app.js"></script>
```

- [ ] **Step 2: Substituir o filtro**

Em `getFilteredClips()`, trocar o bloco `if (q) { ... includes ... }` por:

```js
function getFilteredClips() {
  const q = (state.searchQuery || "").trim();
  const filtered = state.clips.filter((c) => {
    if (state.favoritesOnly && !c.favorite) return false;
    if (state.activeCategory !== "Todos" && c.category !== state.activeCategory) return false;
    const clipSpks = c.speaker_ids && c.speaker_ids.length ? c.speaker_ids : c.speaker_id ? [c.speaker_id] : [];
    if (state.activeSpeaker === "__none__") {
      if (clipSpks.length > 0) return false;
    } else if (state.activeSpeaker !== "Todos" && !clipSpks.includes(state.activeSpeaker)) {
      return false;
    }
    if (q && SBFuzzy.scoreClip(c, q, state.speakers) === null) return false;
    return true;
  });

  filtered.sort((a, b) => {
    if (q) {
      const sa = SBFuzzy.scoreClip(a, q, state.speakers) || 0;
      const sb = SBFuzzy.scoreClip(b, q, state.speakers) || 0;
      if (sb !== sa) return sb - sa;
      if (!!b.favorite !== !!a.favorite) return b.favorite ? 1 : -1;
      const plays = Number(b.play_count || 0) - Number(a.play_count || 0);
      if (plays !== 0) return plays;
    }
    if (state.sortBy === "most_played") {
      const diff = Number(b.play_count || 0) - Number(a.play_count || 0);
      if (diff !== 0) return diff;
    } else if (state.sortBy === "alpha") {
      return String(a.title || "").localeCompare(String(b.title || ""), "pt-BR");
    } else if (state.sortBy === "shortest") {
      return Number(a.duration || 0) - Number(b.duration || 0);
    }
    return Number(b.created_at || 0) - Number(a.created_at || 0);
  });
  return filtered;
}
```

- [ ] **Step 3: Highlight no card**

No template do card, o título e a transcrição passam a usar `SBFuzzy.highlight` **apenas no conteúdo**, mantendo `escapeHtml` no atributo `title`:

```js
const titleHtml = q ? SBFuzzy.highlight(clip.title, q) : escapeHtml(clip.title);
const transcriptHtml = q ? SBFuzzy.highlight(clip.transcript, q) : escapeHtml(clip.transcript);
```

```html
<div class="card-title" title="${escapeHtml(clip.title)}">${titleHtml}</div>
${clip.transcript ? `<div class="card-transcript" title="${escapeHtml(clip.transcript)}">"${transcriptHtml}"</div>` : ""}
```

`q` deve ser lido no topo de `renderGrid()` com `const q = (state.searchQuery || "").trim();`.

- [ ] **Step 4: Estilo**

Em `ui/styles.css`:

```css
.card-title mark,
.card-transcript mark {
  background: rgba(59, 130, 246, 0.35);
  color: inherit;
  border-radius: 3px;
  padding: 0 1px;
}
```

- [ ] **Step 5: Verificar sintaxe**

Run: `node --check ui/app.js`
Expected: sem saída, exit 0.

- [ ] **Step 6: Commit**

```bash
git add ui/app.js ui/index.html ui/styles.css
git commit -m "feat(ui): use fuzzy scoring and highlight matches"
```

---

### Task 3: Paleta Ctrl+K

**Files:**
- Modify: `ui/index.html`
- Modify: `ui/app.js`
- Modify: `ui/styles.css`

- [ ] **Step 1: Markup**

Antes de `</body>` em `ui/index.html`:

```html
<div id="commandPalette" class="command-palette hidden">
  <div class="command-panel">
    <input id="commandInput" type="text" placeholder="Tocar um áudio… (Enter toca, Shift+Enter ouve no fone)" autocomplete="off" />
    <div id="commandResults" class="command-results"></div>
    <div class="command-hint">↑↓ navegar · Enter tocar · Shift+Enter fone · Esc fechar</div>
  </div>
</div>
```

- [ ] **Step 2: Estilo**

```css
.command-palette {
  position: fixed;
  inset: 0;
  z-index: 200;
  background: rgba(3, 7, 18, 0.72);
  backdrop-filter: blur(6px);
  display: flex;
  align-items: flex-start;
  justify-content: center;
  padding-top: 12vh;
}
.command-palette.hidden { display: none; }
.command-panel {
  width: min(640px, 92vw);
  background: rgba(15, 22, 38, 0.98);
  border: 1px solid var(--border-subtle);
  border-radius: 14px;
  box-shadow: 0 24px 70px rgba(0, 0, 0, 0.75);
  overflow: hidden;
}
#commandInput {
  width: 100%;
  padding: 16px 18px;
  background: transparent;
  border: 0;
  border-bottom: 1px solid var(--border-subtle);
  color: inherit;
  font-size: 15px;
  outline: none;
}
.command-results { max-height: 46vh; overflow-y: auto; }
.command-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 16px;
  cursor: pointer;
  border-left: 3px solid transparent;
}
.command-item.active {
  background: rgba(59, 130, 246, 0.16);
  border-left-color: var(--accent);
}
.command-item .ci-title { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.command-item .ci-meta { opacity: 0.7; font-size: 11px; }
.command-hint {
  padding: 8px 16px;
  font-size: 11px;
  opacity: 0.55;
  border-top: 1px solid var(--border-subtle);
}
.mark-active { outline: 2px solid var(--accent); }
```

- [ ] **Step 3: Lógica**

Em `ui/app.js`, adicionar antes de `bindEvents()`:

```js
const commandState = { open: false, index: 0, results: [] };

function commandPaletteResults(query) {
  const q = (query || "").trim();
  const clips = state.clips;
  if (!q) return clips.slice(0, 12);
  return clips
    .map((clip) => ({ clip, score: SBFuzzy.scoreClip(clip, q, state.speakers) }))
    .filter((row) => row.score !== null)
    .sort((a, b) => b.score - a.score)
    .slice(0, 12)
    .map((row) => row.clip);
}

function renderCommandPalette() {
  const list = document.getElementById("commandResults");
  if (!list) return;
  list.innerHTML = "";
  commandState.results.forEach((clip, idx) => {
    const item = document.createElement("div");
    item.className = `command-item ${idx === commandState.index ? "active" : ""}`;
    const spk = (clip.speaker_ids || []).map((id) => state.speakers[id]?.name).filter(Boolean)[0] || "Sem pessoa";
    const title = document.getElementById("commandInput").value.trim();
    item.innerHTML = `
      <span>${escapeHtml(clip.emoji || "🔊")}</span>
      <span class="ci-title">${title ? SBFuzzy.highlight(clip.title, title) : escapeHtml(clip.title)}</span>
      <span class="ci-meta">${escapeHtml(spk)}</span>
      ${clip.hotkey ? `<span class="hotkey-badge">${escapeHtml(clip.hotkey)}</span>` : ""}
    `;
    item.onclick = () => playFromPalette(clip, false);
    list.appendChild(item);
  });
  if (commandState.results.length === 0) {
    list.innerHTML = `<div class="command-item"><span class="ci-meta">Nenhum áudio encontrado</span></div>`;
  }
}

function playFromPalette(clip, monitorOnly) {
  closeCommandPalette();
  if (monitorOnly) {
    api().play_clip(clip.id, true).then((res) => { if (res && res.status) syncActivePlayback(res.status); });
  } else {
    const res = api().play_clip(clip.id, false);
    res.then((r) => { if (showPlayFailure(r)) return; if (r && r.status) syncActivePlayback(r.status); });
  }
}

function openCommandPalette() {
  const el = document.getElementById("commandPalette");
  if (!el) return;
  commandState.open = true;
  commandState.index = 0;
  el.classList.remove("hidden");
  const input = document.getElementById("commandInput");
  input.value = "";
  commandState.results = commandPaletteResults("");
  renderCommandPalette();
  input.focus();
}

function closeCommandPalette() {
  const el = document.getElementById("commandPalette");
  if (!el) return;
  commandState.open = false;
  el.classList.add("hidden");
}

function moveCommandSelection(delta) {
  if (!commandState.results.length) return;
  commandState.index = (commandState.index + delta + commandState.results.length) % commandState.results.length;
  renderCommandPalette();
  const active = document.querySelector(".command-item.active");
  if (active) active.scrollIntoView({ block: "nearest" });
}
```

- [ ] **Step 4: Eventos**

Dentro de `bindEvents()`, adicionar:

```js
const commandInput = document.getElementById("commandInput");
if (commandInput) {
  commandInput.oninput = (e) => {
    commandState.index = 0;
    commandState.results = commandPaletteResults(e.target.value);
    renderCommandPalette();
  };
  commandInput.onkeydown = (e) => {
    if (e.key === "ArrowDown") { e.preventDefault(); moveCommandSelection(1); }
    else if (e.key === "ArrowUp") { e.preventDefault(); moveCommandSelection(-1); }
    else if (e.key === "Enter") {
      e.preventDefault();
      const clip = commandState.results[commandState.index];
      if (clip) playFromPalette(clip, e.shiftKey);
    } else if (e.key === "Escape") { e.preventDefault(); closeCommandPalette(); }
  };
}
document.getElementById("commandPalette").onclick = (e) => {
  if (e.target.id === "commandPalette") closeCommandPalette();
};
document.addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
    if (!document.getElementById("editModal").classList.contains("hidden")) return;
    e.preventDefault();
    if (commandState.open) closeCommandPalette(); else openCommandPalette();
  }
});
```

Atualizar o placeholder do `searchInput` para `"Buscar… (Ctrl+K)"`.

- [ ] **Step 5: Verificar**

Run: `node --check ui/app.js`
Expected: exit 0.

- [ ] **Step 6: Commit**

```bash
git add ui/app.js ui/index.html ui/styles.css
git commit -m "feat(ui): add Ctrl+K command palette"
```

---

### Task 4: Backend — delete com snapshot e restore

**Files:**
- Modify: `main.py` (`delete_clip`, novo `restore_clip`)
- Test: `tests/test_f1_backend.py`

- [ ] **Step 1: Teste que falha**

```python
import copy
from pathlib import Path

import soundfile as sf
import numpy as np
import pytest

from main import SoundboardAPI


@pytest.fixture
def api(tmp_path, monkeypatch):
    api = SoundboardAPI(data_dir=str(tmp_path / "data"), start_background_services=False)
    monkeypatch.setattr(api._hotkeys, "refresh", lambda *a, **k: None)
    return api


def _add_clip(api, tmp_path):
    wav = api._store.clips_dir / "clip.wav"
    sf.write(str(wav), np.zeros((4800, 2), dtype=np.float32), 48000)
    clip = api._store.add_or_update_clip({
        "title": "Teste",
        "file_path": str(wav),
        "duration": 0.1,
        "category": "Call",
    })
    return clip


def test_delete_returns_snapshot_and_allows_undo(api, tmp_path):
    clip = _add_clip(api, tmp_path)
    res = api.delete_clip(clip["id"], delete_file=False)
    assert res["ok"] is True
    assert res["can_undo"] is True
    assert res["clip"]["id"] == clip["id"]
    assert api._store.get_clip(clip["id"]) is None

    restored = api.restore_clip(res["clip"])
    assert restored["ok"] is True
    assert api._store.get_clip(clip["id"])["title"] == "Teste"


def test_restore_rejects_missing_file(api):
    res = api.restore_clip({"id": "abc", "file_path": "C:/nao/existe.wav"})
    assert res["ok"] is False
    assert "não encontrado" in res["error"].lower()


def test_restore_rejects_path_outside_clips_dir(api, tmp_path):
    outside = tmp_path / "evil.wav"
    sf.write(str(outside), np.zeros((100, 2), dtype=np.float32), 48000)
    res = api.restore_clip({"id": "abc", "file_path": str(outside)})
    assert res["ok"] is False


def test_restore_rejects_duplicate_id(api, tmp_path):
    clip = _add_clip(api, tmp_path)
    res = api.restore_clip(clip)
    assert res["ok"] is False
    assert "já existe" in res["error"].lower()
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest -q tests/test_f1_backend.py`
Expected: FAIL — `restore_clip` não existe.

- [ ] **Step 3: Implementar**

Em `delete_clip`:

```python
    def delete_clip(self, clip_id: str, delete_file: bool = False) -> dict:
        clip = self._store.get_clip(clip_id)
        file_path = (clip or {}).get("file_path") or ""
        ok = self._store.delete_clip(clip_id, delete_file=delete_file)
        if file_path:
            self._audio.invalidate_cache(file_path)
        snap = self._store.snapshot()
        self._hotkeys.refresh(snap["clips"], snap["settings"])
        return {
            "ok": bool(ok),
            "clip": copy.deepcopy(clip) if ok and clip else None,
            "can_undo": bool(ok and clip and not delete_file),
        }
```

Adicionar logo depois:

```python
    def restore_clip(self, clip: dict) -> dict:
        """Restores a soft-deleted clip (undo). The audio file must still exist."""
        payload = copy.deepcopy(clip or {})
        clip_id = str(payload.get("id") or "").strip()
        empty = self._store.snapshot()
        if not clip_id:
            return {"ok": False, "error": "Clipe inválido.", "clips": empty["clips"], "speakers": empty["speakers"]}
        if self._store.get_clip(clip_id):
            return {"ok": False, "error": "Este botão já existe.", "clips": empty["clips"], "speakers": empty["speakers"]}

        raw_path = str(payload.get("file_path") or "")
        clips_root = self._store.clips_dir.resolve()
        try:
            resolved = Path(raw_path).resolve() if raw_path else None
        except Exception:
            resolved = None
        if resolved is None or not resolved.is_relative_to(clips_root) or not resolved.exists():
            return {
                "ok": False,
                "error": "Arquivo do áudio não encontrado.",
                "clips": empty["clips"],
                "speakers": empty["speakers"],
            }

        payload["file_path"] = str(resolved)
        saved = self._store.add_or_update_clip(payload)
        snap = self._store.snapshot()
        self._hotkeys.refresh(snap["clips"], snap["settings"])
        return {
            "ok": True,
            "clip": copy.deepcopy(saved),
            "clips": snap["clips"],
            "speakers": snap["speakers"],
        }
```

- [ ] **Step 4: Rodar**

Run: `uv run pytest -q tests/test_f1_backend.py`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add main.py tests/test_f1_backend.py
git commit -m "feat(api): return deleted clip and support undo"
```

---

### Task 5: UI — undo e confirmação

**Files:**
- Modify: `ui/app.js`
- Modify: `ui/styles.css`

- [ ] **Step 1: `showToast` com ação**

```js
function showToast(message, color = "#3b82f6", options = {}) {
  const container = document.getElementById("toastContainer");
  const el = document.createElement("div");
  el.className = "toast";
  el.style.borderLeftColor = color;
  const label = document.createElement("span");
  label.textContent = message;
  el.appendChild(label);
  if (options.actionLabel && typeof options.onAction === "function") {
    const action = document.createElement("button");
    action.className = "toast-action";
    action.textContent = options.actionLabel;
    action.onclick = () => {
      el.remove();
      options.onAction();
    };
    el.appendChild(action);
  }
  container.appendChild(el);
  const timeout = Number(options.duration) > 0 ? Number(options.duration) : 3400;
  setTimeout(() => el.remove(), timeout);
}
```

Estilo:

```css
.toast { display: flex; align-items: center; gap: 10px; }
.toast-action {
  margin-left: auto;
  background: rgba(255, 255, 255, 0.12);
  border: 1px solid rgba(255, 255, 255, 0.2);
  color: inherit;
  border-radius: 6px;
  padding: 4px 8px;
  cursor: pointer;
  font-weight: 600;
}
.toast-action:hover { background: rgba(255, 255, 255, 0.2); }
```

- [ ] **Step 2: Fluxo de delete**

Substituir o handler de `btnDeleteClip`:

```js
  document.getElementById("btnDeleteClip").onclick = async () => {
    const id = document.getElementById("editClipId").value;
    const res = await api().delete_clip(id, false);
    if (!res || !res.ok) {
      showToast((res && res.error) || "Não foi possível remover.", "#ef4444");
      return;
    }
    state.clips = state.clips.filter((c) => c.id !== id);
    document.getElementById("editModal").classList.add("hidden");
    renderSidebar();
    renderGrid();

    const deleted = res.clip;
    if (res.can_undo && deleted) {
      showToast("🗑️ Removido do Soundboard", "#f59e0b", {
        actionLabel: "Desfazer",
        duration: 8000,
        onAction: async () => {
          const restored = await api().restore_clip(deleted);
          if (!restored || !restored.ok) {
            showToast((restored && restored.error) || "Não foi possível desfazer.", "#ef4444");
            return;
          }
          state.clips = restored.clips || state.clips;
          state.speakers = restored.speakers || state.speakers;
          renderSidebar();
          renderGrid();
          showToast("✅ Botão restaurado.", "#10b981");
        },
      });
    } else {
      showToast("🗑️ Botão removido.");
    }
  };
```

- [ ] **Step 3: Confirmação ao excluir interlocutor**

Substituir `btnDeleteSpeakerModal.onclick` por confirmação em dois cliques:

```js
  const deleteSpeakerBtn = document.getElementById("btnDeleteSpeakerModal");
  let confirmTimer = null;
  deleteSpeakerBtn.onclick = async () => {
    if (!deleteSpeakerBtn.dataset.confirming) {
      deleteSpeakerBtn.dataset.confirming = "1";
      deleteSpeakerBtn.textContent = "⚠️ Clique de novo para excluir";
      confirmTimer = setTimeout(() => {
        delete deleteSpeakerBtn.dataset.confirming;
        deleteSpeakerBtn.textContent = "🗑️ Excluir Interlocutor";
      }, 4000);
      return;
    }
    clearTimeout(confirmTimer);
    delete deleteSpeakerBtn.dataset.confirming;
    deleteSpeakerBtn.textContent = "🗑️ Excluir Interlocutor";

    const spkId = document.getElementById("renameSpeakerId").value;
    const res = await api().delete_speaker(spkId);
    if (res && res.ok) {
      state.speakers = res.speakers || {};
      state.clips = res.clips || state.clips;
      if (state.activeSpeaker === spkId) state.activeSpeaker = "Todos";
      document.getElementById("speakerModal").classList.add("hidden");
      renderSidebar();
      renderGrid();
      showToast("🗑️ Interlocutor excluído (áudios mantidos).");
    }
  };
```

- [ ] **Step 4: Verificar**

Run: `node --check ui/app.js`
Expected: exit 0.

- [ ] **Step 5: Commit**

```bash
git add ui/app.js ui/styles.css
git commit -m "feat(ui): undo clip deletion and confirm speaker removal"
```

---

### Task 6: Engine — envelope de ducking

**Files:**
- Modify: `audio_engine.py`
- Test: `tests/test_f1_backend.py`

- [ ] **Step 1: Teste que falha**

```python
from audio_engine import AudioEngine


def test_ducking_disabled_keeps_unity_gain():
    engine = AudioEngine(start_loopback=False)
    engine.set_ducking(False, -12.0, -40.0)
    assert engine._duck_step(0.9, 1024 / 48000) == 1.0


def test_ducking_attack_reaches_amount_and_release_recovers():
    engine = AudioEngine(start_loopback=False)
    engine.set_ducking(True, -12.0, -40.0)
    block = 1024 / 48000

    for _ in range(12):
        gain = engine._duck_step(0.5, block)
    assert 0.23 <= gain <= 0.30, gain

    for _ in range(60):
        gain = engine._duck_step(0.0001, block)
    assert gain > 0.95, gain


def test_ducking_clamps_out_of_range_amount():
    engine = AudioEngine(start_loopback=False)
    engine.set_ducking(True, -999.0, -999.0)
    assert engine._duck_amount_db == -24.0
    assert engine._duck_threshold_dbfs == -60.0
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest -q tests/test_f1_backend.py -k duck`
Expected: FAIL — `set_ducking` não existe.

- [ ] **Step 3: Implementar**

No topo de `audio_engine.py`, importar `math`. No `__init__` de `AudioEngine`, após `_cache_lock`:

```python
        self._duck_enabled = False
        self._duck_amount_db = -12.0
        self._duck_threshold_dbfs = -40.0
        self._duck_gain = 1.0
        self._duck_target_gain = 1.0
        self._duck_threshold_rms = 10 ** (-40.0 / 20.0)
```

Adicionar constantes de classe:

```python
    DUCK_ATTACK_TAU = 0.05
    DUCK_RELEASE_TAU = 0.40
    DUCK_AMOUNT_RANGE = (-24.0, 0.0)
    DUCK_THRESHOLD_RANGE = (-60.0, -10.0)
```

Métodos:

```python
    def set_ducking(self, enabled: bool, amount_db: float = -12.0, threshold_dbfs: float = -40.0):
        """Ducks soundboard output while the mic is above the threshold.

        Mic passthrough itself is never ducked. Values are clamped so a bad
        setting can never silence playback permanently.
        """
        try:
            amount = float(amount_db)
            threshold = float(threshold_dbfs)
        except (TypeError, ValueError):
            return
        lo, hi = self.DUCK_AMOUNT_RANGE
        amount = max(lo, min(hi, amount))
        lo, hi = self.DUCK_THRESHOLD_RANGE
        threshold = max(lo, min(hi, threshold))

        self._duck_enabled = bool(enabled)
        self._duck_amount_db = amount
        self._duck_threshold_dbfs = threshold
        self._duck_threshold_rms = 10 ** (threshold / 20.0)
        if enabled:
            self._duck_target_gain = 10 ** (amount / 20.0)
        else:
            self._duck_target_gain = 1.0
            self._duck_gain = 1.0

    def _duck_step(self, mic_rms: float, block_seconds: float) -> float:
        """One-pole envelope step; called from the mic callback (audio thread)."""
        if not self._duck_enabled:
            self._duck_gain = 1.0
            return 1.0
        target = self._duck_target_gain if mic_rms > self._duck_threshold_rms else 1.0
        tau = self.DUCK_ATTACK_TAU if target < self._duck_gain else self.DUCK_RELEASE_TAU
        alpha = 1.0 - math.exp(-max(1e-4, block_seconds) / tau)
        self._duck_gain += (target - self._duck_gain) * alpha
        return self._duck_gain
```

No callback do mic (`_mic_loop`), após `outdata[:] = ...`:

```python
                    mono = indata[:, 0]
                    rms = float(np.sqrt(np.mean(np.square(mono)))) if mono.size else 0.0
                    self._duck_step(rms, frames / float(self.TARGET_SR))
```

No loop de streaming, antes do `while`:

```python
                duck_prev = float(self._duck_gain)
```

E no trecho do chunk:

```python
                    cur_gain = float(base_gain * live_v)
                    end = min(local_idx + chunk_size, session.total_samples)
                    duck_now = float(self._duck_gain)
                    span = max(1, end - local_idx)
                    ramp = np.linspace(duck_prev, duck_now, span, dtype=np.float32)
                    chunk = np.clip(
                        session.data[local_idx:end] * (cur_gain * ramp)[:, None],
                        -1.0,
                        1.0,
                    ).astype(np.float32)
                    stream.write(chunk)
                    duck_prev = duck_now
```

Se `duck_now == duck_prev`, `np.linspace` devolve o valor constante — comportamento idêntico ao atual.

- [ ] **Step 4: Rodar**

Run: `uv run pytest -q tests/test_f1_backend.py -k duck`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add audio_engine.py tests/test_f1_backend.py
git commit -m "feat(audio): duck soundboard output while the mic is active"
```

---

### Task 7: Backend — settings e API de ducking

**Files:**
- Modify: `store.py` (defaults)
- Modify: `main.py` (`_apply_mic_passthrough`, `set_ducking`)
- Test: `tests/test_f1_backend.py`

- [ ] **Step 1: Teste que falha**

```python
def test_set_ducking_persists_and_validates(api):
    res = api.set_ducking({"enabled": True, "amount_db": -6.0, "threshold_dbfs": -35.0})
    assert res["ok"] is True
    settings = api._store.state["settings"]
    assert settings["duck_enabled"] is True
    assert settings["duck_amount_db"] == -6.0
    assert settings["duck_threshold_dbfs"] == -35.0

    bad = api.set_ducking({"amount_db": "abc"})
    assert bad["ok"] is False
    assert api._store.state["settings"]["duck_amount_db"] == -6.0


def test_set_ducking_clamps_ranges(api):
    api.set_ducking({"enabled": True, "amount_db": -99.0})
    assert api._store.state["settings"]["duck_amount_db"] == -24.0
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest -q tests/test_f1_backend.py -k ducking_persists`
Expected: FAIL — `set_ducking` não existe no API.

- [ ] **Step 3: Defaults**

Em `store.DEFAULT_SETTINGS`:

```python
    "duck_enabled": False,
    "duck_amount_db": -12.0,
    "duck_threshold_dbfs": -40.0,
```

- [ ] **Step 4: Aplicar no passthrough**

Em `_apply_mic_passthrough`, antes de `set_mic_passthrough`:

```python
        self._audio.set_ducking(
            bool(s.get("duck_enabled", False)),
            float(s.get("duck_amount_db", -12.0)),
            float(s.get("duck_threshold_dbfs", -40.0)),
        )
```

- [ ] **Step 5: API**

```python
    def set_ducking(self, payload: dict = None) -> dict:
        """Updates ducking settings. Called from JS with a single object."""
        payload = payload or {}
        updates = {}
        if "enabled" in payload:
            updates["duck_enabled"] = bool(payload.get("enabled"))
        if payload.get("amount_db") is not None:
            try:
                updates["duck_amount_db"] = max(-24.0, min(0.0, float(payload["amount_db"])))
            except (TypeError, ValueError):
                return {"ok": False, "error": "Intensidade de ducking inválida."}
        if payload.get("threshold_dbfs") is not None:
            try:
                updates["duck_threshold_dbfs"] = max(-60.0, min(-10.0, float(payload["threshold_dbfs"])))
            except (TypeError, ValueError):
                return {"ok": False, "error": "Limiar de ducking inválido."}

        settings = self._store.snapshot()["settings"]
        if not updates:
            return {"ok": True, "settings": settings}
        self._store.update_settings(updates)
        self._apply_mic_passthrough()
        return {"ok": True, "settings": self._store.snapshot()["settings"]}
```

- [ ] **Step 6: Rodar**

Run: `uv run pytest -q tests/test_f1_backend.py`
Expected: todos passando.

- [ ] **Step 7: Commit**

```bash
git add main.py store.py tests/test_f1_backend.py
git commit -m "feat(api): persist ducking settings and apply to the mic route"
```

---

### Task 8: UI — controles de ducking

**Files:**
- Modify: `ui/index.html`
- Modify: `ui/app.js`
- Modify: `ui/styles.css`

- [ ] **Step 1: Markup no popover**

Depois do `toggles-group` (antes de `</div>` do `popover-body`):

```html
            <div class="device-group duck-group">
              <label class="toggle-chip" title="Abaixa os clipes do soundboard enquanto você fala no microfone">
                <input type="checkbox" id="duckToggle" />
                <span>🔉 Ducking do mic</span>
              </label>
              <div class="device-row">
                <input type="range" id="duckAmountSlider" min="-24" max="0" step="1" value="-12" title="Quanto o som abaixa quando você fala" />
                <span id="duckAmountVal" class="vol-label">-12 dB</span>
              </div>
              <small class="duck-hint">Só funciona com o Mic no Cabo ativo.</small>
            </div>
```

- [ ] **Step 2: Estado inicial**

Em `applySettings` — o bloco que sincroniza `micPassthroughToggle`/`overlapToggle` (`ui/app.js` ~132-146) — adicionar:

```js
  const duckToggle = document.getElementById("duckToggle");
  if (duckToggle) duckToggle.checked = Boolean(state.settings.duck_enabled);
  const duckSlider = document.getElementById("duckAmountSlider");
  if (duckSlider) {
    const db = Number(state.settings.duck_amount_db ?? -12);
    duckSlider.value = String(db);
    const lbl = document.getElementById("duckAmountVal");
    if (lbl) lbl.textContent = `${db} dB`;
  }
```

- [ ] **Step 3: Eventos**

Em `bindEvents()`:

```js
  const duckToggle = document.getElementById("duckToggle");
  if (duckToggle) {
    duckToggle.onchange = async (e) => {
      const enabled = e.target.checked;
      state.settings.duck_enabled = enabled;
      const res = await api().set_ducking({ enabled });
      if (!res || !res.ok) {
        showToast((res && res.error) || "Não foi possível configurar o ducking.", "#ef4444");
        e.target.checked = !enabled;
        state.settings.duck_enabled = !enabled;
        return;
      }
      state.settings = res.settings || state.settings;
      showToast(enabled ? "🔉 Ducking ativado." : "Ducking desativado.");
    };
  }

  const duckSlider = document.getElementById("duckAmountSlider");
  if (duckSlider) {
    let duckTimer = null;
    duckSlider.oninput = (e) => {
      const db = Number(e.target.value);
      const lbl = document.getElementById("duckAmountVal");
      if (lbl) lbl.textContent = `${db} dB`;
      state.settings.duck_amount_db = db;
      clearTimeout(duckTimer);
      duckTimer = setTimeout(() => {
        api().set_ducking({ amount_db: db }).then((res) => {
          if (res && res.ok) state.settings = res.settings || state.settings;
        }).catch(() => {});
      }, 150);
    };
  }
```

- [ ] **Step 4: Estilo**

```css
.duck-group { border-top: 1px solid var(--border-subtle); padding-top: 10px; }
.duck-hint { display: block; margin-top: 6px; font-size: 11px; opacity: 0.55; }
```

- [ ] **Step 5: Verificar**

Run: `node --check ui/app.js`
Expected: exit 0.

- [ ] **Step 6: Commit**

```bash
git add ui/app.js ui/index.html ui/styles.css
git commit -m "feat(ui): add ducking controls to the routing popover"
```

---

### Task 9: Verificação e revisão

- [ ] **Step 1: Suíte completa**

Run: `uv run pytest -q`
Expected: todos passando (39 existentes + novos).

- [ ] **Step 2: Testes JS**

Run: `node --test tests/test_fuzzy.test.cjs`
Expected: 9 pass.

- [ ] **Step 3: Sintaxe e compilação**

Run: `node --check ui/app.js && python -m compileall -q main.py store.py audio_engine.py`
Expected: exit 0.

- [ ] **Step 4: Diff limpo**

Run: `git diff --check`
Expected: sem saída.

- [ ] **Step 5: Revisão independente**

Disparar revisor read-only com o diff contra a base, cobrindo: injeção no `<mark>`/paleta, corrida do envelope (GIL/atributos), path traversal no restore, perda de undo em delete+restore, e regressão de ordenação da busca.

- [ ] **Step 6: Smoke test manual**

Abrir o app e verificar: `nao` acha `não`; Ctrl+K + Enter toca; Shift+Enter ouve só no fone; excluir → Desfazer; excluir interlocutor pede confirmação; ligar Mic no Cabo + Ducking e falar abaixa os clipes (e não o mic).
