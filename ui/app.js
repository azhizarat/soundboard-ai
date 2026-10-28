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
  sortBy: "newest",
  gridDensity: "normal",
  playingClips: {},
  activePlayback: null,
  waveformPeaks: [],
  waveformDuration: 10,
  recordingHotkey: false,
  sessionStartSec: Date.now() / 1000,
};

function formatPlayTime(sec) {
  const s = Math.max(0, Number(sec) || 0);
  const mins = Math.floor(s / 60);
  const rem = (s % 60).toFixed(1).padStart(4, "0");
  return `${mins}:${rem}`;
}

function formatRelativeTime(createdAtSec) {
  if (!createdAtSec) return "";
  const nowSec = Date.now() / 1000;
  const diff = Math.max(0, nowSec - Number(createdAtSec));
  if (diff < 60) return "agora";
  if (diff < 3600) return `há ${Math.floor(diff / 60)} min`;
  const d = new Date(Number(createdAtSec) * 1000);
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  if (diff < 86400) return `Hoje ${hh}:${mm}`;
  const dd = String(d.getDate()).padStart(2, "0");
  const mo = String(d.getMonth() + 1).padStart(2, "0");
  return `${dd}/${mo} ${hh}:${mm}`;
}

function isClipNew(clip) {
  const created = Number(clip.created_at || 0);
  const nowSec = Date.now() / 1000;
  // Consider 'NOVO!' if created within the last 10 minutes or created in this session and not played yet
  return (nowSec - created <= 600) || (created >= state.sessionStartSec && Number(clip.play_count || 0) === 0);
}

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
    <div class="nav-item-left"><span>👥</span><span>Todos</span></div>
    <span class="count-pill">${state.clips.length}</span>
  `;
  allSpkDiv.onclick = () => {
    state.activeSpeaker = "Todos";
    renderSidebar();
    renderGrid();
  };
  spkList.appendChild(allSpkDiv);

  const noSpkCount = state.clips.filter((c) => {
    const spks = c.speaker_ids && c.speaker_ids.length ? c.speaker_ids : c.speaker_id ? [c.speaker_id] : [];
    return spks.length === 0;
  }).length;
  const noSpkDiv = document.createElement("div");
  noSpkDiv.className = `nav-item ${state.activeSpeaker === "__none__" ? "active" : ""}`;
  noSpkDiv.innerHTML = `
    <div class="nav-item-left"><span>🎵</span><span>Sem Pessoa / Vinhetas</span></div>
    <span class="count-pill">${noSpkCount}</span>
  `;
  noSpkDiv.onclick = () => {
    state.activeSpeaker = "__none__";
    renderSidebar();
    renderGrid();
  };
  spkList.appendChild(noSpkDiv);

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
        <button class="btn-delete-spk" title="Excluir esta pessoa (mantém os áudios sem pessoa)">🗑️</button>
      </div>
    `;
    div.onclick = async (e) => {
      if (e.target.classList.contains("btn-rename-spk")) {
        e.stopPropagation();
        openSpeakerModal(spk);
        return;
      }
      if (e.target.classList.contains("btn-delete-spk")) {
        e.stopPropagation();
        const res = await api().delete_speaker(spk.id);
        if (res && res.ok) {
          state.speakers = res.speakers || {};
          state.clips = res.clips || state.clips;
          if (state.activeSpeaker === spk.id) state.activeSpeaker = "Todos";
          renderSidebar();
          renderGrid();
          showToast(`🗑️ "${spk.name}" removido da lista de pessoas.`);
        }
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
  const filtered = state.clips.filter((c) => {
    if (state.favoritesOnly && !c.favorite) return false;
    if (state.activeCategory !== "Todos" && c.category !== state.activeCategory) return false;
    const clipSpks = c.speaker_ids && c.speaker_ids.length ? c.speaker_ids : c.speaker_id ? [c.speaker_id] : [];
    if (state.activeSpeaker === "__none__") {
      if (clipSpks.length > 0) return false;
    } else if (state.activeSpeaker !== "Todos" && !clipSpks.includes(state.activeSpeaker)) {
      return false;
    }
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

  filtered.sort((a, b) => {
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

function renderGrid() {
  const grid = document.getElementById("soundGrid");
  const filtered = getFilteredClips();
  document.getElementById("clipCountBadge").textContent = `${filtered.length} áudios`;
  grid.classList.toggle("density-compact", state.gridDensity === "compact");
  grid.classList.toggle("density-large", state.gridDensity === "large");
  updateNowPlayingBar(state.activePlayback);
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
    const isNew = isClipNew(clip);
    card.className = `sound-card ${isPlaying ? "playing" : ""} ${isNew ? "is-new-card" : ""}`;
    card.style.setProperty("--card-color", clip.color || "#3b82f6");

    const effDur = Math.max(
      0.1,
      (clip.trim_end && clip.trim_end > clip.trim_start ? clip.trim_end : clip.duration || 0) -
        (clip.trim_start || 0)
    ).toFixed(1);
    const timeStr = formatRelativeTime(clip.created_at);

    card.innerHTML = `
      <div class="card-top">
        <div class="badge-row">
          <span class="card-emoji">${clip.emoji || "🔊"}</span>
          ${isPlaying ? `<span class="eq-bars ${state.activePlayback?.clip_id === clip.id && state.activePlayback?.paused ? "paused" : ""}"><span></span><span></span><span></span><span></span></span>` : isNew ? `<span class="new-badge">✨ NOVO!</span>` : ""}
        </div>
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
          ${timeStr ? `<span class="time-badge" title="Horário em que o áudio subiu">${timeStr}</span>` : ""}
          ${clip.hotkey ? `<span class="hotkey-badge">${clip.hotkey}</span>` : ""}
          <span>${effDur}s</span>
        </div>
      </div>
      <div class="play-progress" id="prog_${clip.id}"></div>
    `;

    card.onclick = async (e) => {
      if (e.target.closest(".btn-preview")) {
        e.stopPropagation();
        const res = await api().play_clip(clip.id, true);
        if (res && res.status) syncActivePlayback(res.status);
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
      const res = await api().play_clip(clip.id, false);
      if (res && res.status) syncActivePlayback(res.status);
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

  state.waveformDuration = maxDur;
  state.waveformPeaks = [];
  drawWaveformCanvas();
  if (api()?.get_clip_waveform) {
    api()
      .get_clip_waveform(clip.id, 160)
      .then((res) => {
        if (res && res.ok && document.getElementById("editClipId").value === clip.id) {
          state.waveformPeaks = res.peaks || [];
          if (res.duration > 0) state.waveformDuration = res.duration;
          drawWaveformCanvas();
        }
      })
      .catch(() => {});
  }

  document.getElementById("editModal").classList.remove("hidden");
}

function drawWaveformCanvas() {
  const canvas = document.getElementById("waveformCanvas");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  const w = canvas.width;
  const h = canvas.height;
  ctx.clearRect(0, 0, w, h);

  const maxDur = Math.max(0.2, Number(document.getElementById("editTrimEnd")?.max || state.waveformDuration || 10));
  const st = Number(document.getElementById("editTrimStart")?.value || 0);
  const en = Number(document.getElementById("editTrimEnd")?.value || maxDur);
  const startX = Math.max(0, Math.min(w, (st / maxDur) * w));
  const endX = Math.max(startX + 2, Math.min(w, (en / maxDur) * w));

  ctx.fillStyle = "rgba(59, 130, 246, 0.12)";
  ctx.fillRect(startX, 0, endX - startX, h);

  const peaks =
    state.waveformPeaks && state.waveformPeaks.length
      ? state.waveformPeaks
      : Array.from({ length: 96 }, (_, i) => 0.18 + 0.55 * Math.abs(Math.sin(i * 0.23)));

  const n = peaks.length;
  const barGap = 1.5;
  const barW = Math.max(1.5, (w - (n - 1) * barGap) / n);
  const midY = h / 2;

  for (let i = 0; i < n; i++) {
    const x = i * (barW + barGap);
    const inTrim = x + barW >= startX && x <= endX;
    const amp = Math.max(0.06, Math.min(1, Number(peaks[i]) || 0.08));
    const bh = Math.max(3, amp * (h - 10));
    ctx.fillStyle = inTrim ? "#3b82f6" : "#293552";
    ctx.beginPath();
    ctx.roundRect(x, midY - bh / 2, barW, bh, 1.5);
    ctx.fill();
  }

  ctx.fillStyle = "rgba(11, 15, 25, 0.58)";
  if (startX > 0) ctx.fillRect(0, 0, startX, h);
  if (endX < w) ctx.fillRect(endX, 0, w - endX, h);

  ctx.fillStyle = "#10b981";
  ctx.fillRect(startX, 0, 2.5, h);
  ctx.fillStyle = "#f59e0b";
  ctx.fillRect(Math.max(0, endX - 2.5), 0, 2.5, h);
}

let playbackRafId = null;

function syncActivePlayback(status) {
  if (!status || !status.clip_id) return;
  const pos = Number(status.position ?? status.current_s ?? 0);
  const dur = Math.max(0.1, Number(status.duration ?? status.duration_s ?? 1));
  if (!state.settings.overlap_audio) {
    state.playingClips = {};
  }
  state.playingClips[status.clip_id] = true;
  state.activePlayback = {
    ...status,
    playing: status.playing !== false,
    position: pos,
    duration: dur,
    volume: Number(status.volume ?? status.live_volume ?? 1),
    anchorPosSec: pos,
    anchorTimeMs: performance.now(),
  };
  renderGrid();
  startPlaybackRaf();
}

function startPlaybackRaf() {
  if (playbackRafId) cancelAnimationFrame(playbackRafId);
  const step = () => {
    const ap = state.activePlayback;
    if (!ap || !ap.playing) {
      playbackRafId = null;
      return;
    }
    if (!ap.paused) {
      const elapsedSec = Math.max(0, (performance.now() - ap.anchorTimeMs) / 1000);
      let curPos = ap.anchorPosSec + elapsedSec;
      if (curPos >= ap.duration) {
        if (ap.loop) {
          curPos = curPos % ap.duration;
          ap.anchorPosSec = curPos;
          ap.anchorTimeMs = performance.now();
        } else {
          delete state.playingClips[ap.clip_id];
          state.activePlayback = null;
          playbackRafId = null;
          renderGrid();
          return;
        }
      }
      ap.position = curPos;
      const seekSlider = document.getElementById("npSeekSlider");
      if (seekSlider && document.activeElement !== seekSlider) {
        seekSlider.max = ap.duration.toFixed(2);
        seekSlider.value = curPos.toFixed(2);
      }
      const curEl = document.getElementById("npCurTime");
      if (curEl) curEl.textContent = formatPlayTime(curPos);
      const prog = document.getElementById(`prog_${ap.clip_id}`);
      if (prog && ap.duration > 0) {
        prog.style.transition = "none";
        prog.style.width = `${Math.min(100, (curPos / ap.duration) * 100)}%`;
      }
    }
    playbackRafId = requestAnimationFrame(step);
  };
  playbackRafId = requestAnimationFrame(step);
}

function updateNowPlayingBar(playback) {
  const playingIds = Object.keys(state.playingClips);
  const dot = document.getElementById("playStatusDot");
  const eq = document.getElementById("npEqBars");
  const statusEl = document.getElementById("playStatus");
  const routeBadge = document.getElementById("npRouteBadge");
  const controls = document.getElementById("nowPlayingControls");
  if (!statusEl || !controls) return;

  const active = playback && playback.playing ? playback : null;
  if (!active && playingIds.length === 0) {
    statusEl.textContent = "Pronto para tocar";
    controls.classList.add("hidden");
    routeBadge?.classList.add("hidden");
    eq?.classList.add("hidden");
    dot?.classList.remove("active");
    return;
  }

  const clipId = active?.clip_id || playingIds[0];
  const clip = state.clips.find((c) => c.id === clipId);
  const title = active?.title || clip?.title || "Áudio";
  const emoji = active?.emoji || clip?.emoji || "🔊";
  const paused = Boolean(active?.paused);
  const loop = Boolean(active?.loop);
  const monitorOnly = Boolean(active?.monitor_only);

  dot?.classList.toggle("active", !paused);
  if (eq) {
    eq.classList.remove("hidden");
    eq.classList.toggle("paused", paused);
  }
  statusEl.textContent = `${emoji} ${title}`;
  if (routeBadge) {
    routeBadge.classList.remove("hidden");
    routeBadge.textContent = monitorOnly ? "🎧 SÓ FONE" : "🔌 CALL + FONE";
  }
  controls.classList.remove("hidden");

  const pauseBtn = document.getElementById("btnNpPause");
  if (pauseBtn) pauseBtn.textContent = paused ? "▶" : "⏸";
  const loopBtn = document.getElementById("btnNpLoop");
  if (loopBtn) loopBtn.classList.toggle("active", loop);

  const dur = Math.max(0.1, Number(active?.duration || clip?.duration || 1));
  const pos = Math.max(0, Math.min(dur, Number(active?.position ?? 0)));
  const seekSlider = document.getElementById("npSeekSlider");
  if (seekSlider && document.activeElement !== seekSlider) {
    seekSlider.max = dur.toFixed(2);
    seekSlider.value = pos.toFixed(2);
  }
  const curEl = document.getElementById("npCurTime");
  const totEl = document.getElementById("npTotalTime");
  if (curEl) curEl.textContent = formatPlayTime(pos);
  if (totEl) totEl.textContent = formatPlayTime(dur);

  const volSlider = document.getElementById("npLiveVolume");
  const volLabel = document.getElementById("npLiveVolVal");
  if (volSlider && active?.volume !== undefined && document.activeElement !== volSlider) {
    volSlider.value = active.volume;
    if (volLabel) volLabel.textContent = `${Math.round(Number(active.volume) * 100)}%`;
  }
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

  document.getElementById("btnStopAll").onclick = () => {
    state.playingClips = {};
    state.activePlayback = null;
    renderGrid();
    api().stop_all();
  };

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
    const res = await api().import_audio_files(null, false, null);
    if (res && res.ok) {
      state.clips = res.clips;
      state.speakers = res.speakers;
      renderSidebar();
      renderGrid();
      showToast(`✅ ${res.imported.length} áudio(s) importado(s) e nomeado(s) pela IA!`, "#10b981");
    }
  };

  document.getElementById("btnImportVinheta").onclick = async () => {
    const res = await api().import_audio_files(null, true, "Vinhetas");
    if (res && res.ok) {
      state.clips = res.clips;
      state.speakers = res.speakers;
      renderSidebar();
      renderGrid();
      showToast(`🎵 ${res.imported.length} vinheta(s) importada(s) sem associar pessoa!`, "#06b6d4");
    }
  };

  document.getElementById("btnImportFolder").onclick = async () => {
    const isVinhetaFilter = state.activeCategory === "Vinhetas" || state.activeSpeaker === "__none__";
    const res = await api().import_folder_or_separados(
      null,
      isVinhetaFilter,
      isVinhetaFilter ? "Vinhetas" : null
    );
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

  // Search, Density & Sort
  document.querySelectorAll("#densityGroup .density-btn").forEach((btn) => {
    btn.onclick = () => {
      state.gridDensity = btn.dataset.density || "normal";
      document.querySelectorAll("#densityGroup .density-btn").forEach((b) =>
        b.classList.toggle("active", b === btn)
      );
      renderGrid();
    };
  });

  // Now Playing Deck controls
  const btnNpRestart = document.getElementById("btnNpRestart");
  if (btnNpRestart) {
    btnNpRestart.onclick = async () => {
      const res = await api().restart_playback();
      if (res && res.status) syncActivePlayback(res.status);
    };
  }
  const btnNpPause = document.getElementById("btnNpPause");
  if (btnNpPause) {
    btnNpPause.onclick = async () => {
      const res = await api().pause_resume_playback();
      if (res && res.status) {
        syncActivePlayback(res.status);
        renderGrid();
      }
    };
  }
  const btnNpLoop = document.getElementById("btnNpLoop");
  if (btnNpLoop) {
    btnNpLoop.onclick = async () => {
      const nextLoop = !Boolean(state.activePlayback?.loop);
      const res = await api().set_playback_loop(nextLoop);
      if (res && res.status) syncActivePlayback(res.status);
    };
  }
  const btnNpStop = document.getElementById("btnNpStop");
  if (btnNpStop) {
    btnNpStop.onclick = () => {
      state.playingClips = {};
      state.activePlayback = null;
      renderGrid();
      api().stop_all();
    };
  }
  const npSeekSlider = document.getElementById("npSeekSlider");
  if (npSeekSlider) {
    npSeekSlider.oninput = (e) => {
      const pos = Number(e.target.value) || 0;
      const curEl = document.getElementById("npCurTime");
      if (curEl) curEl.textContent = formatPlayTime(pos);
    };
    npSeekSlider.onchange = async (e) => {
      const pos = Number(e.target.value) || 0;
      const res = await api().seek_playback(pos);
      if (res && res.status) syncActivePlayback(res.status);
    };
  }
  const npLiveVolume = document.getElementById("npLiveVolume");
  if (npLiveVolume) {
    npLiveVolume.oninput = (e) => {
      const v = Number(e.target.value) || 1;
      const lbl = document.getElementById("npLiveVolVal");
      if (lbl) lbl.textContent = `${Math.round(v * 100)}%`;
      if (state.activePlayback) state.activePlayback.volume = v;
      api().set_live_clip_volume(v);
    };
  }

  const sortSelect = document.getElementById("sortSelect");
  if (sortSelect) {
    sortSelect.onchange = (e) => {
      state.sortBy = e.target.value;
      renderGrid();
    };
  }
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
    drawWaveformCanvas();
  }

  const wfCanvas = document.getElementById("waveformCanvas");
  if (wfCanvas) {
    let wfDragTarget = null;
    const pointerToSec = (ev) => {
      const rect = wfCanvas.getBoundingClientRect();
      const ratio = Math.max(0, Math.min(1, (ev.clientX - rect.left) / Math.max(1, rect.width)));
      const maxDur = Number(trimEndSlider.max) || 10;
      return ratio * maxDur;
    };
    wfCanvas.addEventListener("pointerdown", (ev) => {
      const t = pointerToSec(ev);
      const curSt = Number(trimStartSlider.value) || 0;
      const curEn = Number(trimEndSlider.value) || 10;
      wfDragTarget = Math.abs(t - curSt) <= Math.abs(t - curEn) ? "start" : "end";
      wfCanvas.setPointerCapture(ev.pointerId);
      if (wfDragTarget === "start") syncTrimValues(Math.min(t, curEn - 0.1), curEn);
      else syncTrimValues(curSt, Math.max(t, curSt + 0.1));
    });
    wfCanvas.addEventListener("pointermove", (ev) => {
      if (!wfDragTarget) return;
      const t = pointerToSec(ev);
      const curSt = Number(trimStartSlider.value) || 0;
      const curEn = Number(trimEndSlider.value) || 10;
      if (wfDragTarget === "start") syncTrimValues(Math.min(t, curEn - 0.1), curEn);
      else syncTrimValues(curSt, Math.max(t, curSt + 0.1));
    });
    const endWfDrag = () => {
      wfDragTarget = null;
    };
    wfCanvas.addEventListener("pointerup", endWfDrag);
    wfCanvas.addEventListener("pointercancel", endWfDrag);
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
    const selSpk = document.getElementById("editSpeaker").value;
    const payload = {
      id: document.getElementById("editClipId").value,
      emoji: document.getElementById("editEmoji").value || "🔊",
      title: document.getElementById("editTitle").value || "Áudio",
      color: document.getElementById("editColor").value,
      category: document.getElementById("editCategory").value,
      speaker_id: selSpk,
      speaker_ids: selSpk ? [selSpk] : [],
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
    const res = await api().play_clip(id, true);
    if (res && res.status) syncActivePlayback(res.status);
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

  // Speaker rename & delete modal
  document.getElementById("btnCloseSpeakerModal").onclick = () =>
    document.getElementById("speakerModal").classList.add("hidden");

  document.getElementById("btnDeleteSpeakerModal").onclick = async () => {
    const spkId = document.getElementById("renameSpeakerId").value;
    const res = await api().delete_speaker(spkId);
    if (res && res.ok) {
      state.speakers = res.speakers || {};
      state.clips = res.clips || state.clips;
      if (state.activeSpeaker === spkId) state.activeSpeaker = "Todos";
      document.getElementById("speakerModal").classList.add("hidden");
      renderSidebar();
      renderGrid();
      showToast("🗑️ Pessoa excluída com sucesso.");
    }
  };

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
    syncActivePlayback(payload);
    renderGrid();
  } else if (event === "playback_tick") {
    if (payload && payload.clip_id) {
      syncActivePlayback(payload);
    }
  } else if (event === "clip_finished") {
    delete state.playingClips[payload.clip_id];
    if (state.activePlayback?.clip_id === payload.clip_id) {
      state.activePlayback = null;
    }
    renderGrid();
  } else if (event === "stopped_all") {
    state.playingClips = {};
    state.activePlayback = null;
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
  } else if (event === "progress") {
    const banner = document.getElementById("progressBanner");
    if (!banner) return;
    if (payload.active) {
      banner.classList.remove("hidden");
      document.getElementById("progressLabel").textContent = payload.label || "Processando...";
      document.getElementById("progressDetail").textContent = payload.detail ? `— ${payload.detail}` : "";
      document.getElementById("progressPercent").textContent = `${payload.percent || 0}%`;
      document.getElementById("progressFill").style.width = `${payload.percent || 0}%`;
    } else {
      document.getElementById("progressPercent").textContent = "100%";
      document.getElementById("progressFill").style.width = "100%";
      setTimeout(() => banner.classList.add("hidden"), 450);
    }
  } else if (event === "toast") {
    showToast(payload.message || "", "#f59e0b");
  }
};

window.addEventListener("DOMContentLoaded", initApp);
