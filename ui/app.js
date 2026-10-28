const state = {
  settings: {},
  categories: [],
  speakers: {},
  clips: [],
  devices: { outputs: [], inputs: [] },
  discoveredSeparados: [],
  activeCategory: "Todos",
  activeSpeaker: "Todos",
  favoritesOnly: false,
  searchQuery: "",
  playingClips: {},
  recordingHotkey: false,
};

function showToast(message, color = "#3b82f6") {
  const container = document.getElementById("toastContainer");
  const el = document.createElement("div");
  el.className = "toast";
  el.style.borderLeftColor = color;
  el.textContent = message;
  container.appendChild(el);
  setTimeout(() => el.remove(), 3400);
}

function api() {
  return window.pywebview && window.pywebview.api ? window.pywebview.api : null;
}

async function initApp() {
  if (!api()) {
    setTimeout(initApp, 80);
    return;
  }
  const initial = await api().get_initial_state();
  state.settings = initial.settings || {};
  state.categories = initial.categories || [];
  state.speakers = initial.speakers || {};
  state.clips = initial.clips || [];
  state.devices = initial.devices || { outputs: [], inputs: [] };
  state.discoveredSeparados = initial.discovered_separados || [];

  renderDevices();
  renderQuickImportBar();
  renderSidebar();
  renderGrid();
  bindEvents();
}

function renderDevices() {
  const cableSel = document.getElementById("cableDeviceSelect");
  const monSel = document.getElementById("monitorDeviceSelect");
  const badge = document.getElementById("cableStatusBadge");

  cableSel.innerHTML = "";
  monSel.innerHTML = "";

  const outputs = state.devices.outputs || [];
  outputs.forEach((d) => {
    const opt1 = document.createElement("option");
    opt1.value = d.id;
    opt1.textContent = `${d.is_cable ? "🔌 " : ""}${d.name}`;
    if (state.settings.cable_device_id === d.id) opt1.selected = true;
    cableSel.appendChild(opt1);

    const opt2 = document.createElement("option");
    opt2.value = d.id;
    opt2.textContent = `${d.is_default ? "🎧 " : ""}${d.name}`;
    if (state.settings.monitor_device_id === d.id) opt2.selected = true;
    monSel.appendChild(opt2);
  });

  if (state.devices.detected_cable_id !== null && state.devices.detected_cable_id !== undefined) {
    badge.textContent = "VB-Cable Ativo";
    badge.className = "status-pill status-ok";
  } else {
    badge.textContent = "VB-Cable Não Detectado (Usando Saída Selecionada)";
    badge.className = "status-pill status-warn";
  }

  const cVol = Number(state.settings.cable_volume ?? 1.0);
  const mVol = Number(state.settings.monitor_volume ?? 0.75);
  document.getElementById("cableVolumeSlider").value = cVol;
  document.getElementById("cableVolumeVal").textContent = `${Math.round(cVol * 100)}%`;
  document.getElementById("monitorVolumeSlider").value = mVol;
  document.getElementById("monitorVolumeVal").textContent = `${Math.round(mVol * 100)}%`;

  document.getElementById("micPassthroughToggle").checked = Boolean(state.settings.mic_passthrough);
  document.getElementById("overlapToggle").checked = Boolean(state.settings.overlap_audio);
  const autoSplitEl = document.getElementById("autoSplitToggle");
  if (autoSplitEl) autoSplitEl.checked = Boolean(state.settings.auto_split_speakers);
  const repSecs = Number(state.settings.replay_seconds || 15);
  document.getElementById("replaySecsLabel").textContent = repSecs;
  const repSelect = document.getElementById("replaySecondsInput");
  if (![...repSelect.options].some((o) => Number(o.value) === repSecs)) {
    const customOpt = document.createElement("option");
    customOpt.value = repSecs;
    customOpt.textContent = `${repSecs}s`;
    repSelect.appendChild(customOpt);
  }
  repSelect.value = String(repSecs);
}

function renderQuickImportBar() {
  const bar = document.getElementById("quickImportBar");
  const wrap = document.getElementById("quickImportChips");
  wrap.innerHTML = "";
  if (!state.discoveredSeparados || state.discoveredSeparados.length === 0) {
    bar.classList.add("hidden");
    return;
  }
  bar.classList.remove("hidden");
  state.discoveredSeparados.forEach((item) => {
    const btn = document.createElement("button");
    btn.className = "quick-chip-btn";
    btn.textContent = `📥 Importar ${item.label}`;
    btn.onclick = async () => {
      showToast(`Importando e classificando ${item.label} com IA...`, "#8b5cf6");
      const res = await api().import_folder_or_separados(item.path);
      if (res && res.ok) {
        state.clips = res.clips || state.clips;
        state.speakers = res.speakers || state.speakers;
        renderSidebar();
        renderGrid();
        showToast(`✅ ${res.imported_count || 0} áudios importados com sucesso!`, "#10b981");
      }
    };
    wrap.appendChild(btn);
  });
}

function renderSidebar() {
  const catList = document.getElementById("categoryList");
  catList.innerHTML = "";

  const allItems = [
    { id: "Todos", name: "Todos os Áudios", emoji: "🎛️", count: state.clips.length },
    { id: "Favoritos", name: "Favoritos", emoji: "⭐", count: state.clips.filter((c) => c.favorite).length },
    ...state.categories.map((cat) => ({
      ...cat,
      count: state.clips.filter((c) => c.category === cat.id).length,
    })),
  ];

  allItems.forEach((item) => {
    const div = document.createElement("div");
    const isActive =
      (item.id === "Favoritos" && state.favoritesOnly) ||
      (!state.favoritesOnly && item.id === state.activeCategory);
    div.className = `nav-item ${isActive ? "active" : ""}`;
    div.innerHTML = `
      <div class="nav-item-left">
        <span>${item.emoji}</span>
        <span>${item.name}</span>
      </div>
      <span class="count-pill">${item.count}</span>
    `;
    div.onclick = () => {
      if (item.id === "Favoritos") {
        state.favoritesOnly = true;
        state.activeCategory = "Todos";
      } else {
        state.favoritesOnly = false;
        state.activeCategory = item.id;
      }
      renderSidebar();
      renderGrid();
    };
    catList.appendChild(div);
  });

  // Speakers
  const spkList = document.getElementById("speakerList");
  spkList.innerHTML = "";

  const allSpkDiv = document.createElement("div");
  allSpkDiv.className = `nav-item ${state.activeSpeaker === "Todos" ? "active" : ""}`;
  allSpkDiv.innerHTML = `
    <div class="nav-item-left"><span>👥</span><span>Todos os Amigos</span></div>
    <span class="count-pill">${Object.keys(state.speakers).length}</span>
  `;
  allSpkDiv.onclick = () => {
    state.activeSpeaker = "Todos";
    renderSidebar();
    renderGrid();
  };
  spkList.appendChild(allSpkDiv);

  Object.values(state.speakers).forEach((spk) => {
    const count = state.clips.filter((c) => {
      const spks = c.speaker_ids && c.speaker_ids.length ? c.speaker_ids : c.speaker_id ? [c.speaker_id] : [];
      return spks.includes(spk.id);
    }).length;
    const div = document.createElement("div");
    div.className = `nav-item ${state.activeSpeaker === spk.id ? "active" : ""}`;
    div.innerHTML = `
      <div class="nav-item-left">
        <span style="color:${spk.color || "#3b82f6"}">●</span>
        <span>${spk.name}</span>
      </div>
      <div class="nav-item-right">
        <span class="count-pill">${count}</span>
        <button class="btn-rename-spk" title="Renomear este amigo">✏️</button>
      </div>
    `;
    div.onclick = (e) => {
      if (e.target.classList.contains("btn-rename-spk")) {
        e.stopPropagation();
        openSpeakerModal(spk);
        return;
      }
      state.activeSpeaker = spk.id;
      renderSidebar();
      renderGrid();
    };
    spkList.appendChild(div);
  });
}

function getFilteredClips() {
  const q = (state.searchQuery || "").trim().toLowerCase();
  return state.clips.filter((c) => {
    if (state.favoritesOnly && !c.favorite) return false;
    if (state.activeCategory !== "Todos" && c.category !== state.activeCategory) return false;
    const clipSpks = c.speaker_ids && c.speaker_ids.length ? c.speaker_ids : c.speaker_id ? [c.speaker_id] : [];
    if (state.activeSpeaker !== "Todos" && !clipSpks.includes(state.activeSpeaker)) return false;
    if (q) {
      const spkNames = clipSpks.map((sid) => state.speakers[sid]?.name || "").join(" ");
      const hay = [
        c.title,
        c.transcript,
        c.category,
        c.acoustic_mood,
        spkNames,
        c.hotkey,
        ...(c.tags || []),
      ]
        .join(" ")
        .toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });
}

function renderGrid() {
  const grid = document.getElementById("soundGrid");
  const filtered = getFilteredClips();
  document.getElementById("clipCountBadge").textContent = `${filtered.length} áudios`;
  grid.innerHTML = "";

  if (filtered.length === 0) {
    grid.innerHTML = `
      <div class="empty-state">
        <h3>Nenhum áudio nesta visualização</h3>
        <p>Use os botões acima para importar uma pasta <b>separados</b>, adicionar áudios (.mp3/.wav) ou clicar em <b>⚡ Clipar Call (15s)</b>.</p>
      </div>
    `;
    return;
  }

  filtered.forEach((clip) => {
    const clipSpkIds =
      clip.speaker_ids && clip.speaker_ids.length
        ? clip.speaker_ids
        : clip.speaker_id
        ? [clip.speaker_id]
        : [];
    const spkBadgesHtml = clipSpkIds
      .map((sid) => state.speakers[sid])
      .filter(Boolean)
      .map(
        (spk) =>
          `<span class="spk-badge" style="background:${spk.color}33;color:${spk.color};border:1px solid ${spk.color}66">${spk.name}</span>`
      )
      .join("");

    const card = document.createElement("div");
    const isPlaying = Boolean(state.playingClips[clip.id]);
    card.className = `sound-card ${isPlaying ? "playing" : ""}`;
    card.style.setProperty("--card-color", clip.color || "#3b82f6");

    const effDur = Math.max(
      0.1,
      (clip.trim_end && clip.trim_end > clip.trim_start ? clip.trim_end : clip.duration || 0) -
        (clip.trim_start || 0)
    ).toFixed(1);

    card.innerHTML = `
      <div class="card-top">
        <span class="card-emoji">${clip.emoji || "🔊"}</span>
        <div class="card-actions">
          <button class="card-icon-btn btn-preview" title="Ouvir somente no meu fone (sem mandar pro VB-Cable)">🎧</button>
          <button class="card-icon-btn btn-fav ${clip.favorite ? "fav-active" : ""}" title="Favoritar">★</button>
          <button class="card-icon-btn btn-edit" title="Editar nome, corte, atalho ou IA">⚙️</button>
        </div>
      </div>
      <div class="card-title" title="${clip.title}">${clip.title}</div>
      <div class="card-transcript" title="${clip.transcript || ""}">${
        clip.transcript ? `"${clip.transcript}"` : clip.category
      }</div>
      <div class="card-footer">
        <div class="badge-row">
          ${spkBadgesHtml}
          <span class="mood-badge">${clip.acoustic_mood || "Normal"}</span>
        </div>
        <div class="badge-row">
          ${clip.hotkey ? `<span class="hotkey-badge">${clip.hotkey}</span>` : ""}
          <span>${effDur}s</span>
        </div>
      </div>
      <div class="play-progress" id="prog_${clip.id}"></div>
    `;

    card.onclick = (e) => {
      if (e.target.closest(".btn-preview")) {
        e.stopPropagation();
        api().play_clip(clip.id, true);
        return;
      }
      if (e.target.closest(".btn-fav")) {
        e.stopPropagation();
        api()
          .toggle_favorite(clip.id)
          .then((res) => {
            if (res && res.ok) {
              const idx = state.clips.findIndex((x) => x.id === clip.id);
              if (idx >= 0) state.clips[idx] = res.clip;
              renderSidebar();
              renderGrid();
            }
          });
        return;
      }
      if (e.target.closest(".btn-edit")) {
        e.stopPropagation();
        openEditModal(clip);
        return;
      }
      api().play_clip(clip.id, false);
    };

    card.oncontextmenu = (e) => {
      e.preventDefault();
      openEditModal(clip);
    };

    grid.appendChild(card);
  });
}

function openEditModal(clip) {
  document.getElementById("editClipId").value = clip.id;
  document.getElementById("editEmoji").value = clip.emoji || "🔊";
  document.getElementById("editTitle").value = clip.title || "";
  document.getElementById("editColor").value = clip.color || "#3b82f6";
  document.getElementById("editCategory").value = clip.category || "Call";
  document.getElementById("editHotkey").value = clip.hotkey || "";
  document.getElementById("editMood").value = clip.acoustic_mood || "Normal";
  document.getElementById("editTranscript").value = clip.transcript || "";

  const spkSel = document.getElementById("editSpeaker");
  spkSel.innerHTML = `<option value="">-- Sem Interlocutor --</option>`;
  Object.values(state.speakers).forEach((s) => {
    const opt = document.createElement("option");
    opt.value = s.id;
    opt.textContent = `${s.name} (${s.id})`;
    if (clip.speaker_id === s.id) opt.selected = true;
    spkSel.appendChild(opt);
  });
  // Allow creating a new speaker slot
  const nextNum = Object.keys(state.speakers).length + 1;
  const newOpt = document.createElement("option");
  newOpt.value = `interlocutor_${nextNum}`;
  newOpt.textContent = `➕ Novo Amigo (interlocutor_${nextNum})`;
  spkSel.appendChild(newOpt);

  const maxDur = Math.max(0.2, Number(clip.duration || 10));
  const trimStartEl = document.getElementById("editTrimStart");
  const trimEndEl = document.getElementById("editTrimEnd");
  const trimStartNum = document.getElementById("editTrimStartNum");
  const trimEndNum = document.getElementById("editTrimEndNum");

  trimStartEl.max = maxDur;
  trimEndEl.max = maxDur;
  trimStartNum.max = maxDur;
  trimEndNum.max = maxDur;

  const stVal = Number(clip.trim_start || 0);
  const enVal = Number(clip.trim_end && clip.trim_end > stVal ? clip.trim_end : maxDur);

  trimStartEl.value = stVal;
  trimEndEl.value = enVal;
  trimStartNum.value = stVal.toFixed(2);
  trimEndNum.value = enVal.toFixed(2);
  updateTrimTotalBadge(stVal, enVal);

  const volEl = document.getElementById("editVolume");
  volEl.value = Number(clip.volume ?? 1.0);
  document.getElementById("clipVolVal").textContent = `${Math.round(volEl.value * 100)}%`;

  document.getElementById("editModal").classList.remove("hidden");
}

function updateTrimTotalBadge(st, en) {
  const dur = Math.max(0.05, Number(en) - Number(st));
  const badge = document.getElementById("trimTotalDurationVal");
  if (badge) badge.textContent = `${dur.toFixed(2)}s`;
}

function openSpeakerModal(spk) {
  document.getElementById("renameSpeakerId").value = spk.id;
  document.getElementById("renameSpeakerName").value = spk.name || "";
  document.getElementById("renameSpeakerColor").value = spk.color || "#3b82f6";
  document.getElementById("speakerModal").classList.remove("hidden");
}

function bindEvents() {
  document.getElementById("cableDeviceSelect").onchange = (e) => {
    const id = Number(e.target.value);
    state.settings.cable_device_id = id;
    api().update_settings({ cable_device_id: id });
  };

  document.getElementById("monitorDeviceSelect").onchange = (e) => {
    const id = Number(e.target.value);
    state.settings.monitor_device_id = id;
    api().update_settings({ monitor_device_id: id });
  };

  document.getElementById("cableVolumeSlider").oninput = (e) => {
    const v = Number(e.target.value);
    document.getElementById("cableVolumeVal").textContent = `${Math.round(v * 100)}%`;
    state.settings.cable_volume = v;
    api().update_settings({ cable_volume: v });
  };

  document.getElementById("monitorVolumeSlider").oninput = (e) => {
    const v = Number(e.target.value);
    document.getElementById("monitorVolumeVal").textContent = `${Math.round(v * 100)}%`;
    state.settings.monitor_volume = v;
    api().update_settings({ monitor_volume: v });
  };

  document.getElementById("micPassthroughToggle").onchange = (e) => {
    const enabled = e.target.checked;
    state.settings.mic_passthrough = enabled;
    api().update_settings({ mic_passthrough: enabled });
    showToast(
      enabled
        ? "🎙️ Mic Passthrough ativado (seu microfone está indo para o CABLE Input)"
        : "🎙️ Mic Passthrough desativado"
    );
  };

  document.getElementById("overlapToggle").onchange = (e) => {
    state.settings.overlap_audio = e.target.checked;
    api().update_settings({ overlap_audio: e.target.checked });
  };

  const autoSplitToggle = document.getElementById("autoSplitToggle");
  if (autoSplitToggle) {
    autoSplitToggle.onchange = (e) => {
      state.settings.auto_split_speakers = e.target.checked;
      api().update_settings({ auto_split_speakers: e.target.checked });
      showToast(
        e.target.checked
          ? "✂️ Auto-Separar Vozes ativado: o Clipar Call criará botões individuais por fala/amigo"
          : "✂️ Auto-Separar Vozes desativado: o Clipar Call manterá 1 botão com todos identificados"
      );
    };
  }

  document.getElementById("btnStopAll").onclick = () => api().stop_all();

  const replaySecsInput = document.getElementById("replaySecondsInput");
  replaySecsInput.onchange = (e) => {
    const secs = Math.max(1, Math.min(60, Number(e.target.value) || 15));
    replaySecsInput.value = secs;
    document.getElementById("replaySecsLabel").textContent = secs;
    state.settings.replay_seconds = secs;
    api().update_settings({ replay_seconds: secs });
  };
  replaySecsInput.oninput = (e) => {
    const secs = Math.max(1, Math.min(60, Number(e.target.value) || 15));
    document.getElementById("replaySecsLabel").textContent = secs;
  };

  document.getElementById("btnReplayClip").onclick = async () => {
    const secs = Math.max(1, Math.min(60, Number(replaySecsInput.value) || 15));
    showToast(`⚡ Salvando últimos ${secs}s da call e identificando vozes...`, "#f59e0b");
    const res = await api().capture_replay_clip(secs);
    if (res && res.ok) {
      state.clips = res.clips;
      state.speakers = res.speakers;
      renderSidebar();
      renderGrid();
      if (res.split_count) {
        showToast(`✅ Replay (${secs}s) separado automaticamente em ${res.split_count} botões por voz!`, "#10b981");
      } else {
        showToast(`✅ Replay (${secs}s) salvo como "${res.clip.title}"!`, "#10b981");
      }
    } else {
      showToast(res?.error || "Sem áudio recente no buffer da call.", "#ef4444");
    }
  };

  document.getElementById("btnImportFiles").onclick = async () => {
    const res = await api().import_audio_files();
    if (res && res.ok) {
      state.clips = res.clips;
      state.speakers = res.speakers;
      renderSidebar();
      renderGrid();
      showToast(`✅ ${res.imported.length} áudio(s) importado(s) e nomeado(s) pela IA!`, "#10b981");
    }
  };

  document.getElementById("btnImportFolder").onclick = async () => {
    const res = await api().import_folder_or_separados();
    if (res && res.ok) {
      state.clips = res.clips;
      state.speakers = res.speakers;
      renderSidebar();
      renderGrid();
      showToast(`✅ ${res.imported_count || 0} áudio(s) importado(s) da pasta!`, "#10b981");
    }
  };

  document.getElementById("btnMiniOverlay").onclick = async () => {
    const res = await api().toggle_mini_overlay();
    if (res) {
      document.body.classList.toggle("mini-overlay", Boolean(res.is_mini_overlay));
    }
  };

  // Search
  const searchInput = document.getElementById("searchInput");
  const clearBtn = document.getElementById("btnClearSearch");
  searchInput.oninput = (e) => {
    state.searchQuery = e.target.value;
    clearBtn.classList.toggle("hidden", !state.searchQuery);
    renderGrid();
  };
  clearBtn.onclick = () => {
    searchInput.value = "";
    state.searchQuery = "";
    clearBtn.classList.add("hidden");
    renderGrid();
  };

  window.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "f") {
      e.preventDefault();
      searchInput.focus();
    }
  });

  // Modal bindings
  document.getElementById("btnCloseModal").onclick = () =>
    document.getElementById("editModal").classList.add("hidden");

  const trimStartSlider = document.getElementById("editTrimStart");
  const trimEndSlider = document.getElementById("editTrimEnd");
  const trimStartNum = document.getElementById("editTrimStartNum");
  const trimEndNum = document.getElementById("editTrimEndNum");

  function syncTrimValues(st, en) {
    const maxDur = Number(trimEndSlider.max) || 10;
    let s = Math.max(0, Math.min(maxDur - 0.05, Number(st) || 0));
    let e = Math.max(s + 0.05, Math.min(maxDur, Number(en) || maxDur));
    trimStartSlider.value = s;
    trimEndSlider.value = e;
    trimStartNum.value = s.toFixed(2);
    trimEndNum.value = e.toFixed(2);
    updateTrimTotalBadge(s, e);
  }

  trimStartSlider.oninput = (e) => syncTrimValues(e.target.value, trimEndSlider.value);
  trimEndSlider.oninput = (e) => syncTrimValues(trimStartSlider.value, e.target.value);
  trimStartNum.onchange = (e) => syncTrimValues(e.target.value, trimEndNum.value);
  trimEndNum.onchange = (e) => syncTrimValues(trimStartNum.value, e.target.value);

  document.querySelectorAll(".btn-preset").forEach((btn) => {
    btn.onclick = () => {
      const maxDur = Number(trimEndSlider.max) || 10;
      const preset = btn.getAttribute("data-preset");
      if (preset === "first3") syncTrimValues(0, Math.min(3, maxDur));
      else if (preset === "first5") syncTrimValues(0, Math.min(5, maxDur));
      else if (preset === "last3") syncTrimValues(Math.max(0, maxDur - 3), maxDur);
      else if (preset === "last5") syncTrimValues(Math.max(0, maxDur - 5), maxDur);
      else syncTrimValues(0, maxDur);
    };
  });

  document.getElementById("editVolume").oninput = (e) => {
    document.getElementById("clipVolVal").textContent = `${Math.round(Number(e.target.value) * 100)}%`;
  };

  // Press-to-bind hotkey input
  const hkInput = document.getElementById("editHotkey");
  hkInput.onkeydown = (e) => {
    e.preventDefault();
    e.stopPropagation();
    if (["Control", "Shift", "Alt", "Meta"].includes(e.key)) return;
    const parts = [];
    if (e.ctrlKey) parts.push("ctrl");
    if (e.altKey) parts.push("alt");
    if (e.shiftKey) parts.push("shift");
    let k = e.key.toLowerCase();
    if (k === " ") k = "space";
    parts.push(k);
    hkInput.value = parts.join("+");
  };
  document.getElementById("btnClearHotkey").onclick = () => {
    hkInput.value = "";
  };

  document.getElementById("btnSaveClip").onclick = async () => {
    const payload = {
      id: document.getElementById("editClipId").value,
      emoji: document.getElementById("editEmoji").value || "🔊",
      title: document.getElementById("editTitle").value || "Áudio",
      color: document.getElementById("editColor").value,
      category: document.getElementById("editCategory").value,
      speaker_id: document.getElementById("editSpeaker").value,
      hotkey: document.getElementById("editHotkey").value,
      transcript: document.getElementById("editTranscript").value,
      trim_start: Number(document.getElementById("editTrimStart").value),
      trim_end: Number(document.getElementById("editTrimEnd").value),
      volume: Number(document.getElementById("editVolume").value),
    };
    const res = await api().save_clip_edits(payload);
    if (res && res.ok) {
      const idx = state.clips.findIndex((c) => c.id === payload.id);
      if (idx >= 0) state.clips[idx] = res.clip;
      state.speakers = res.speakers || state.speakers;
      document.getElementById("editModal").classList.add("hidden");
      renderSidebar();
      renderGrid();
      showToast("💾 Alterações salvas!");
    }
  };

  document.getElementById("btnPreviewMonitor").onclick = async () => {
    const id = document.getElementById("editClipId").value;
    // Save temporary trim/vol adjustments before previewing
    await document.getElementById("btnSaveClip").onclick();
    api().play_clip(id, true);
  };

  document.getElementById("btnReanalyzeAI").onclick = async () => {
    const id = document.getElementById("editClipId").value;
    showToast("🧠 Analisando vozes (pyannote + TitaNet), transcrição e emoção...", "#8b5cf6");
    const res = await api().reanalyze_clip_ai(id);
    if (res && res.ok) {
      const idx = state.clips.findIndex((c) => c.id === id);
      if (idx >= 0) state.clips[idx] = res.clip;
      state.speakers = res.speakers || state.speakers;
      openEditModal(res.clip);
      renderSidebar();
      renderGrid();
      const spkCount = (res.clip.speaker_ids || []).length || 1;
      showToast(`✨ IA detectou ${spkCount} voz(es): "${res.clip.title}"`, "#10b981");
    }
  };

  document.getElementById("btnSplitSpeakers").onclick = async () => {
    const id = document.getElementById("editClipId").value;
    showToast("✂️ Separando falas por interlocutor em botões individuais...", "#8b5cf6");
    const res = await api().split_clip_by_speakers(id);
    if (res && res.ok) {
      state.clips = res.clips || state.clips;
      state.speakers = res.speakers || state.speakers;
      document.getElementById("editModal").classList.add("hidden");
      renderSidebar();
      renderGrid();
      showToast(`✅ Áudio dividido em ${res.created_count} botões separados por fala!`, "#10b981");
    } else {
      showToast(res?.error || "Não há múltiplas falas para separar neste áudio.", "#f59e0b");
    }
  };

  document.getElementById("btnEnrollVoice").onclick = async () => {
    const id = document.getElementById("editClipId").value;
    const spkId = document.getElementById("editSpeaker").value || "interlocutor_1";
    const res = await api().enroll_speaker_from_clip(id, spkId, "");
    if (res && res.ok) {
      state.speakers = res.speakers || state.speakers;
      showToast(`🎙️ Voz salva em referencias_vozes.npz para ${spkId}!`, "#10b981");
    } else {
      showToast("Não foi possível extrair embedding vocal (áudio muito curto ou sem fala).", "#f59e0b");
    }
  };

  document.getElementById("btnDeleteClip").onclick = async () => {
    const id = document.getElementById("editClipId").value;
    const res = await api().delete_clip(id, false);
    if (res && res.ok) {
      state.clips = state.clips.filter((c) => c.id !== id);
      document.getElementById("editModal").classList.add("hidden");
      renderSidebar();
      renderGrid();
      showToast("🗑️ Botão removido do Soundboard.");
    }
  };

  // Speaker rename modal
  document.getElementById("btnCloseSpeakerModal").onclick = () =>
    document.getElementById("speakerModal").classList.add("hidden");

  document.getElementById("btnSaveSpeaker").onclick = async () => {
    const spkId = document.getElementById("renameSpeakerId").value;
    const name = document.getElementById("renameSpeakerName").value;
    const color = document.getElementById("renameSpeakerColor").value;
    const res = await api().update_speaker(spkId, name, color);
    if (res && res.ok) {
      state.speakers = res.speakers;
      document.getElementById("speakerModal").classList.add("hidden");
      renderSidebar();
      renderGrid();
      showToast(`✅ Interlocutor atualizado para "${name}"!`);
    }
  };
}

// Callback receiver from Python backend
window.onBackendEvent = (event, payload) => {
  if (event === "clip_started") {
    state.playingClips[payload.clip_id] = true;
    renderGrid();
    const prog = document.getElementById(`prog_${payload.clip_id}`);
    if (prog) {
      prog.style.transition = `width ${payload.duration || 1.5}s linear`;
      requestAnimationFrame(() => {
        prog.style.width = "100%";
      });
    }
  } else if (event === "clip_finished") {
    delete state.playingClips[payload.clip_id];
    renderGrid();
  } else if (event === "stopped_all") {
    state.playingClips = {};
    renderGrid();
  } else if (event === "replay_captured" || event === "clips_updated") {
    if (payload.clips) state.clips = payload.clips;
    if (payload.speakers) state.speakers = payload.speakers;
    renderSidebar();
    renderGrid();
    if (event === "replay_captured" && payload.clip) {
      showToast(`⚡ Replay salvo: "${payload.clip.title}"`, "#10b981");
    }
  } else if (event === "overlay_toggled") {
    document.body.classList.toggle("mini-overlay", Boolean(payload.is_mini_overlay));
  } else if (event === "toast") {
    showToast(payload.message || "", "#f59e0b");
  }
};

window.addEventListener("DOMContentLoaded", initApp);
