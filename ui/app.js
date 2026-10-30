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
  draftClipId: null,
  draftCommitting: false,
  environment: null,
  envAutoOpened: false,
  envBannerDismissed: (function () {
    try {
      return sessionStorage.getItem("env_banner_dismissed") === "1";
    } catch (_) {
      return false;
    }
  })(),
  config: {},
  replaySources: { sources: [], default_name: "" },
  replayMeterTimer: null,
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

let pendingUndoToast = null;

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
  return el;
}

function showPlayFailure(res) {
  if (res && res.ok === false) {
    showToast(res.error || "Não foi possível tocar este áudio.", "#ef4444");
    return true;
  }
  return false;
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[ch]));
}

function safeColor(value, fallback = "#3b82f6") {
  const v = String(value ?? "");
  return /^#[0-9a-fA-F]{3,8}$/.test(v) ? v : fallback;
}

function api() {
  return window.pywebview && window.pywebview.api ? window.pywebview.api : null;
}

/* Environment (doctor) */
const ENV_STATUS_ICONS = { ok: "✓", warn: "⚠", error: "✗" };
const ENV_HINTS = {
  ai: "Modelos de IA ausentes: ajuste a pasta de modelos em ⚙️ Ambiente.",
  convert: "Formatos .m4a/.webm/.aac/.wma/.opus precisam do FFmpeg instalado (veja ⚙️ Ambiente).",
  cable: "VB-Cable não detectado: instale o VB-Audio Cable para rotear o som (veja ⚙️ Ambiente).",
};
const envOriginalTitles = new Map();

function captureEnvOriginalTitles() {
  [
    "btnReanalyzeAI",
    "btnSplitSpeakers",
    "btnEnrollVoice",
    "btnImportFiles",
    "micPassthroughToggle",
  ].forEach((id) => {
    if (envOriginalTitles.has(id)) return;
    const el = document.getElementById(id);
    if (el) envOriginalTitles.set(id, el.title || "");
  });
}

// Only ever touches disabled/title so existing handlers keep working.
function setEnvGated(id, disabled, hint) {
  const el = document.getElementById(id);
  if (!el) return;
  captureEnvOriginalTitles();
  el.disabled = Boolean(disabled);
  el.title = hint || envOriginalTitles.get(id) || "";
}

function applyCapabilityGating(caps = {}) {
  const aiOff = caps.ai_analysis === false;
  ["btnReanalyzeAI", "btnSplitSpeakers", "btnEnrollVoice"].forEach((id) => {
    setEnvGated(id, aiOff, aiOff ? ENV_HINTS.ai : "");
  });
  // Import stays enabled: native formats keep working without FFmpeg.
  setEnvGated("btnImportFiles", false, caps.audio_convert === false ? ENV_HINTS.convert : "");
  const cableOff = caps.cable_routing === false;
  setEnvGated("micPassthroughToggle", cableOff, cableOff ? ENV_HINTS.cable : "");
}

function renderEnvironment(env) {
  if (!env) return;
  state.environment = env;
  const pending = Number(env.incomplete || 0);
  const checks = Array.isArray(env.checks) ? env.checks : [];
  const errors = checks.filter((c) => c.status === "error").length;
  const warns = checks.filter((c) => c.status === "warn").length;

  const badge = document.getElementById("envBadge");
  if (badge) {
    badge.textContent = `${pending} pendência(s)`;
    badge.classList.toggle("hidden", pending === 0);
    badge.classList.toggle("status-warn", pending > 0);
    badge.classList.toggle("status-ok", pending === 0);
  }
  const banner = document.getElementById("envBanner");
  if (banner) {
    const shouldShow = pending > 0 && !state.envBannerDismissed;
    banner.classList.toggle("hidden", !shouldShow);
  }
  const bannerText = document.getElementById("envBannerText");
  if (bannerText) {
    if (errors > 0) {
      bannerText.textContent = `Ambiente incompleto — ${errors} pendência(s) crítica(s)`;
    } else if (warns > 0) {
      bannerText.textContent = `Avisos de ambiente — ${warns} recurso(s) opcional(is) offline (Ollama)`;
    } else {
      bannerText.textContent = "Ambiente completo";
    }
  }
  renderEnvChecks(checks);
  applyCapabilityGating(env.capabilities || {});
}

function buildEnvCheckAction(check) {
  const action = String(check.action || "");
  const fixUrl = String(check.fix_url || "");
  if (!fixUrl && !action) return null;
  const isPick = action === "pick_models_dir" || action === "pick_separados_root";
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "btn btn-xs btn-secondary env-action";
  btn.textContent = check.fix_label || (isPick ? "Alterar" : "Abrir ajuda");
  btn.addEventListener("click", async () => {
    if (!api()) return;
    if (fixUrl) {
      const res = await api().open_help_url(fixUrl);
      if (res && res.ok === false) showToast(res.error || "Não foi possível abrir o link.", "#ef4444");
      return;
    }
    if (!isPick) return;
    await pickEnvDirectory(action === "pick_models_dir" ? "models_dir" : "separados_root");
  });
  return btn;
}

function renderEnvChecks(checks) {
  const list = document.getElementById("envChecks");
  if (!list) return;
  list.innerHTML = "";
  if (!checks.length) {
    const empty = document.createElement("li");
    empty.className = "env-check warn";
    empty.textContent = "Não foi possível verificar o ambiente agora.";
    list.appendChild(empty);
    return;
  }
  checks.forEach((check) => {
    const status = Object.prototype.hasOwnProperty.call(ENV_STATUS_ICONS, check.status)
      ? check.status
      : "warn";
    const li = document.createElement("li");
    li.className = `env-check ${status}`;

    const icon = document.createElement("span");
    icon.className = "env-icon";
    icon.textContent = ENV_STATUS_ICONS[status];
    icon.setAttribute("aria-hidden", "true");
    li.appendChild(icon);

    const body = document.createElement("div");
    body.className = "env-check-body";
    const label = document.createElement("strong");
    label.className = "env-label";
    label.textContent = check.label || check.id || "Verificação";
    body.appendChild(label);
    if (check.detail) {
      const detail = document.createElement("span");
      detail.className = "env-detail";
      detail.textContent = check.detail;
      body.appendChild(detail);
    }
    li.appendChild(body);

    const actionBtn = buildEnvCheckAction(check);
    if (actionBtn) li.appendChild(actionBtn);
    list.appendChild(li);
  });
}

function applyEnvPaths(config) {
  const cfg = config || {};
  const models = document.getElementById("envModelsDir");
  if (models) models.value = cfg.models_dir || "";
  const separados = document.getElementById("envSeparadosRoot");
  if (separados) separados.value = cfg.separados_root || "";
}

async function pickEnvDirectory(kind) {
  if (!api()) return;
  const res = await api().pick_directory(kind);
  if (!res || !res.ok) {
    if (res && res.error && res.error !== "Seleção cancelada.") {
      showToast(res.error, "#ef4444");
    }
    return;
  }
  if (res.config) state.config = res.config;
  applyEnvPaths(res.config || state.config);
  if (res.environment) renderEnvironment(res.environment);
  showToast("Pasta atualizada.", "#10b981");
}

function closeEnvModal() {
  const modal = document.getElementById("envModal");
  if (modal) modal.classList.add("hidden");
}

async function openEnvModal() {
  const modal = document.getElementById("envModal");
  if (!modal) return;
  modal.classList.remove("hidden");
  applyEnvPaths(state.config);
  if (!api()) return;
  try {
    const env = await api().get_environment_status(false);
    if (env && Array.isArray(env.checks)) renderEnvironment(env);
  } catch (err) {
    // Keep the modal usable with the last known snapshot.
  }
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

  state.config = initial.config || {};
  captureEnvOriginalTitles();
  renderEnvironment(initial.environment);
  const hasErrors =
    initial.environment &&
    Array.isArray(initial.environment.checks) &&
    initial.environment.checks.some((c) => c.status === "error");
  if (hasErrors && !state.envAutoOpened) {
    state.envAutoOpened = true;
    openEnvModal();
  }
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
    // A virtual cable is never a valid "fone" route: VB-Cable accepts the second
    // stream and silently discards it, so the clip would play into the void.
    opt2.disabled = Boolean(d.is_cable);
    opt2.textContent = d.is_cable
      ? `🚫 ${d.name} (é o cabo — não use como fone)`
      : `${d.is_default ? "🎧 " : ""}${d.name}`;
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
  updateRoutingSummary();

  document.getElementById("micPassthroughToggle").checked = Boolean(state.settings.mic_passthrough);
  document.getElementById("overlapToggle").checked = Boolean(state.settings.overlap_audio);
  const autoSplitEl = document.getElementById("autoSplitToggle");
  if (autoSplitEl) autoSplitEl.checked = Boolean(state.settings.auto_split_speakers);
  const duckToggle = document.getElementById("duckToggle");
  if (duckToggle) duckToggle.checked = Boolean(state.settings.duck_enabled);
  const duckSlider = document.getElementById("duckAmountSlider");
  if (duckSlider) {
    const db = Number(state.settings.duck_amount_db ?? -12);
    duckSlider.value = String(db);
    const lbl = document.getElementById("duckAmountVal");
    if (lbl) lbl.textContent = `${db} dB`;
  }
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

  const micSel = document.getElementById("micDeviceSelect");
  if (micSel) {
    micSel.innerHTML = "";
    const inputs = state.devices.inputs || [];
    inputs.forEach((d) => {
      const opt = document.createElement("option");
      opt.value = d.id;
      opt.textContent = `${d.is_default ? "🎙️ " : ""}${d.name}`;
      if (state.settings.mic_device_id === d.id) opt.selected = true;
      micSel.appendChild(opt);
    });
  }

  renderReplaySources();
}

function renderReplaySources() {
  const sel = document.getElementById("replaySourceSelect");
  if (!sel) return;
  sel.innerHTML = "";
  const saved = String(state.settings.replay_source_name || "").trim();
  const info = state.replaySources || { sources: [], default_name: "" };
  const sources = Array.isArray(info.sources) ? info.sources : [];
  const defLabel = info.default_name ? `: ${info.default_name}` : " do Windows";

  const autoOpt = document.createElement("option");
  autoOpt.value = "";
  autoOpt.textContent = `🔄 Automático (Saída padrão${defLabel})`;
  if (!saved) autoOpt.selected = true;
  sel.appendChild(autoOpt);

  let matchedSaved = !saved;
  sources.forEach((src) => {
    const opt = document.createElement("option");
    opt.value = src.name;
    const prefix = src.is_cable ? "🔌 " : src.is_default ? "🎧 " : "🔈 ";
    const suffix = src.is_cable ? " (cabo virtual)" : "";
    opt.textContent = `${prefix}${src.name}${suffix}`;
    if (saved && src.name === saved) {
      opt.selected = true;
      matchedSaved = true;
    }
    sel.appendChild(opt);
  });

  if (saved && !matchedSaved) {
    const missingOpt = document.createElement("option");
    missingOpt.value = saved;
    missingOpt.textContent = `⚠️ ${saved} (desconectado)`;
    missingOpt.selected = true;
    sel.appendChild(missingOpt);
  }
}

async function refreshReplaySources() {
  if (!api() || typeof api().list_replay_sources !== "function") return;
  try {
    const res = await api().list_replay_sources();
    if (res && Array.isArray(res.sources)) {
      state.replaySources = res;
      renderReplaySources();
    }
  } catch (_) {}
  await updateReplayMeter();
}

async function updateReplayMeter() {
  if (!api() || typeof api().get_replay_status !== "function") return;
  const fill = document.getElementById("replayMeterFill");
  const statusEl = document.getElementById("replayStatusText");
  if (!fill || !statusEl) return;
  try {
    const st = await api().get_replay_status();
    if (!st) return;
    const db = Number(st.level_db ?? -100);
    const pct = st.receiving ? Math.max(2, Math.min(100, ((db + 60) / 60) * 100)) : 0;
    fill.style.width = `${pct.toFixed(1)}%`;
    fill.classList.toggle("noise", st.receiving && db < -50);
    fill.classList.toggle("hot", st.receiving && db > -3);

    if (st.error) {
      statusEl.textContent = `⚠️ ${st.error}`;
      statusEl.classList.add("warn");
    } else if (!st.active) {
      statusEl.textContent = "⏸️ Aguardando saída de áudio ativa…";
      statusEl.classList.remove("warn");
    } else {
      const srKhz = st.samplerate ? ` · ${(st.samplerate / 1000).toFixed(1).replace(".0", "")} kHz` : "";
      const modeTag = st.mode === "fixed" ? "fixo" : "auto";
      const lvl = st.receiving && db > -90 ? ` · ${db.toFixed(0)} dBFS` : " · silêncio";
      statusEl.textContent = `🟢 Gravando de: ${st.device_name || "Saída padrão"} (${modeTag}${srKhz}${lvl})`;
      statusEl.classList.remove("warn");
    }
  } catch (_) {}
}

function startReplayMeterPolling() {
  stopReplayMeterPolling();
  refreshReplaySources();
  state.replayMeterTimer = setInterval(() => {
    const menu = document.getElementById("routingPopoverMenu");
    if (!menu || menu.classList.contains("hidden")) {
      stopReplayMeterPolling();
      return;
    }
    updateReplayMeter();
  }, 220);
}

function stopReplayMeterPolling() {
  if (state.replayMeterTimer) {
    clearInterval(state.replayMeterTimer);
    state.replayMeterTimer = null;
  }
}

function updateRoutingSummary() {
  const summaryEl = document.getElementById("routingStatusSummary");
  if (!summaryEl) return;
  const cVol = Math.round(Number(state.settings.cable_volume ?? 1.0) * 100);
  const mVol = Math.round(Number(state.settings.monitor_volume ?? 0.75) * 100);
  const outputs = (state.devices && state.devices.outputs) || [];
  const mon = outputs.find((d) => d.id === state.settings.monitor_device_id);
  const warning = mon && mon.is_cable ? " ⚠️ Fone no cabo (sem áudio)" : "";
  summaryEl.textContent = `Cable: ${cVol}% | Fone: ${mVol}%${warning}`;
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
        <span>${escapeHtml(item.emoji)}</span>
        <span>${escapeHtml(item.name)}</span>
      </div>
      <span class="count-pill">${escapeHtml(item.count)}</span>
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
    <span class="count-pill">${escapeHtml(state.clips.length)}</span>
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
    <span class="count-pill">${escapeHtml(noSpkCount)}</span>
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
    const spkColor = safeColor(spk.color);
    div.innerHTML = `
      <div class="nav-item-left">
        <span style="color:${escapeHtml(spkColor)}">●</span>
        <span>${escapeHtml(spk.name)}</span>
      </div>
      <div class="nav-item-right">
        <span class="count-pill">${escapeHtml(count)}</span>
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
  const q = (state.searchQuery || "").trim();
  const scores = new Map();
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
      const s = SBFuzzy.scoreClip(c, q, state.speakers);
      if (s === null) return false;
      scores.set(c, s);
    }
    return true;
  });

  filtered.sort((a, b) => {
    if (q) {
      const sa = scores.get(a) || 0;
      const sb = scores.get(b) || 0;
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

function renderGrid() {
  const grid = document.getElementById("soundGrid");
  const q = (state.searchQuery || "").trim();
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
      .map((spk) => {
        const spkColor = safeColor(spk.color);
        const badgeStyle = `background:${spkColor}33;color:${spkColor};border:1px solid ${spkColor}66`;
        return `<span class="spk-badge" style="${escapeHtml(badgeStyle)}">${escapeHtml(spk.name)}</span>`;
      })
      .join("");

    const card = document.createElement("div");
    const isPlaying = Boolean(state.playingClips[clip.id]);
    const isNew = isClipNew(clip);
    card.className = `sound-card ${isPlaying ? "playing" : ""} ${isNew ? "is-new-card" : ""}`;
    card.style.setProperty("--card-color", safeColor(clip.color));

    const effDur = Math.max(
      0.1,
      (clip.trim_end && clip.trim_end > clip.trim_start ? clip.trim_end : clip.duration || 0) -
        (clip.trim_start || 0)
    ).toFixed(1);

    const moodBadgeHtml =
      clip.acoustic_mood && clip.acoustic_mood.toLowerCase() !== "normal"
        ? `<span class="mood-badge">${escapeHtml(clip.acoustic_mood)}</span>`
        : "";

    const titleHtml = q ? SBFuzzy.highlight(clip.title, q) : escapeHtml(clip.title);
    const transcriptHtml = q ? SBFuzzy.highlight(clip.transcript, q) : escapeHtml(clip.transcript);

    card.innerHTML = `
      <div class="card-top">
        <div class="badge-row card-meta-left">
          <span class="card-emoji">${escapeHtml(clip.emoji || "🔊")}</span>
          ${isPlaying ? `<span class="eq-bars ${state.activePlayback?.clip_id === clip.id && state.activePlayback?.paused ? "paused" : ""}"><span></span><span></span><span></span><span></span></span>` : isNew ? `<span class="new-badge">✨ NOVO!</span>` : ""}
        </div>
        <div class="card-actions">
          <button class="card-icon-btn btn-preview" title="Ouvir somente no meu fone (sem mandar pro VB-Cable)">🎧</button>
          <button class="card-icon-btn btn-fav ${clip.favorite ? "fav-active" : ""}" title="Favoritar">★</button>
          <button class="card-icon-btn btn-edit" title="Editar nome, corte, atalho ou IA">⚙️</button>
        </div>
      </div>
      <div class="card-title" title="${escapeHtml(clip.title)}">${titleHtml}</div>
      ${clip.transcript ? `<div class="card-transcript" title="${escapeHtml(clip.transcript)}">"${transcriptHtml}"</div>` : ""}
      <div class="card-footer">
        <div class="badge-row card-meta-left">
          ${spkBadgesHtml}
          ${moodBadgeHtml}
        </div>
        <div class="badge-row card-meta-right">
          ${clip.hotkey ? `<span class="hotkey-badge">${escapeHtml(clip.hotkey)}</span>` : ""}
          <span class="card-duration">${effDur}s</span>
        </div>
      </div>
      <div class="play-progress" id="prog_${escapeHtml(clip.id)}"></div>
    `;

    card.onclick = async (e) => {
      if (e.target.closest(".btn-preview")) {
        e.stopPropagation();
        const res = await api().play_clip(clip.id, true);
        if (showPlayFailure(res)) return;
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
      if (showPlayFailure(res)) return;
      if (res && res.status) syncActivePlayback(res.status);
    };

    card.oncontextmenu = (e) => {
      e.preventDefault();
      openEditModal(clip);
    };

    grid.appendChild(card);
  });
}

function updateModalPreviewButtons(isPlaying) {
  const btnFooter = document.getElementById("btnPreviewMonitor");
  const btnTrim = document.getElementById("btnPreviewTrimHeader");
  if (btnFooter) {
    if (isPlaying) {
      btnFooter.innerHTML = "⏹ Parar Teste";
      btnFooter.classList.add("btn-danger");
      btnFooter.classList.remove("btn-secondary");
    } else {
      btnFooter.innerHTML = "🎧 Testar no Fone";
      btnFooter.classList.remove("btn-danger");
      btnFooter.classList.add("btn-secondary");
    }
  }
  if (btnTrim) {
    if (isPlaying) {
      btnTrim.innerHTML = "⏹ Parar";
      btnTrim.classList.add("btn-danger");
    } else {
      btnTrim.innerHTML = "▶️ Testar Corte";
      btnTrim.classList.remove("btn-danger");
    }
  }
}

async function saveCurrentClipEdits(closeModalAfter = true) {
  const id = document.getElementById("editClipId").value;
  if (!id) return null;
  const selSpk = document.getElementById("editSpeaker").value;
  const payload = {
    id,
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
    renderSidebar();
    renderGrid();
    if (closeModalAfter) {
      closeEditModal();
      showToast("💾 Alterações salvas!");
    }
    return res.clip;
  }
  return null;
}

function applyDraftChrome(isDraft) {
  document.getElementById("modalTitle").textContent = isDraft
    ? "⚡ Replay — revise antes de salvar"
    : "⚙️ Configurar Botão do Soundboard";
  const saveBtn = document.getElementById("btnSaveClip");
  saveBtn.textContent = isDraft ? "✅ Salvar no Soundboard" : "💾 Salvar";
  saveBtn.title = isDraft ? "Salvar no Soundboard" : "Salvar alterações e fechar";
  document.getElementById("btnDiscardDraft").classList.toggle("hidden", !isDraft);
  document.getElementById("btnDeleteClip").classList.toggle("hidden", isDraft);
  document.getElementById("btnSplitSpeakers").classList.toggle("hidden", isDraft);
  document.getElementById("btnReanalyzeAI").classList.toggle("hidden", isDraft);
  document.getElementById("btnEnrollVoice").classList.toggle("hidden", isDraft);
}

function setDraftBusy(busy) {
  ["btnSaveClip", "btnDiscardDraft", "btnCloseModal"].forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.disabled = busy;
  });
}

function closeEditModal() {
  document.getElementById("editModal").classList.add("hidden");
  const editId = document.getElementById("editClipId")?.value;
  if (state.activePlayback && state.activePlayback.clip_id === editId) {
    api().stop_all();
    state.activePlayback = null;
    delete state.playingClips[editId];
    if (playbackRafId) {
      cancelAnimationFrame(playbackRafId);
      playbackRafId = null;
    }
    renderGrid();
  }
  updateModalPreviewButtons(false);
  state.draftClipId = null;
  applyDraftChrome(false);
}

async function discardDraftAndClose({ silent = false } = {}) {
  if (state.draftCommitting) return;
  const id = state.draftClipId;
  state.draftClipId = null;
  if (id) {
    const res = await api().discard_replay_draft(id);
    if (res && res.ok) {
      state.clips = res.clips || state.clips;
      state.speakers = res.speakers || state.speakers;
    }
  }
  closeEditModal();
  renderSidebar();
  renderGrid();
  if (!silent && id) showToast("Replay descartado.");
}

async function commitReplayDraft() {
  if (state.draftCommitting) return null;
  const id = state.draftClipId;
  if (!id) return null;
  state.draftCommitting = true;
  setDraftBusy(true);
  try {
    const savedEdits = await saveCurrentClipEdits(false);
    if (!savedEdits) {
      showToast("Não foi possível salvar as alterações do replay.", "#ef4444");
      return null;
    }
    // A discard/close during the awaited save invalidates this commit.
    if (state.draftClipId !== id) return null;
    const res = await api().commit_replay_draft(id);
    if (!res || !res.ok) {
      showToast((res && res.error) || "Não foi possível salvar o replay.", "#ef4444");
      return null;
    }
    state.draftClipId = null;
    state.clips = res.clips || state.clips;
    state.speakers = res.speakers || state.speakers;
    closeEditModal();
    renderSidebar();
    renderGrid();
    if (res.split_count) {
      showToast(`⚡ Replay separado em ${res.split_count} botões!`, "#10b981");
    } else {
      showToast(`⚡ Replay salvo: "${res.clip.title}"`, "#10b981");
    }
    return res.clip;
  } finally {
    state.draftCommitting = false;
    setDraftBusy(false);
  }
}

function renderClipLufsBadge(clip) {
  const badge = document.getElementById("clipLufsBadge");
  if (!badge) return;
  if (clip && typeof clip.lufs === "number") {
    const est = clip.lufs_estimated ? " (est.)" : "";
    const gain =
      typeof clip.loudness_gain_db === "number"
        ? ` [${clip.loudness_gain_db >= 0 ? "+" : ""}${clip.loudness_gain_db.toFixed(1)} dB]`
        : "";
    badge.textContent = `Nível: ${clip.lufs.toFixed(1)} LUFS${est}${gain}`;
    badge.classList.remove("status-warn", "hidden");
    badge.classList.add("status-ok");
  } else {
    badge.textContent = "Nível: —";
    badge.classList.remove("status-ok");
    badge.classList.add("status-warn");
  }
}

function openEditModal(clip) {
  state.draftClipId = clip.pending ? clip.id : null;
  applyDraftChrome(Boolean(clip.pending));
  renderClipLufsBadge(clip);
  document.getElementById("editClipId").value = clip.id;
  document.getElementById("editEmoji").value = clip.emoji || "🔊";
  document.getElementById("editTitle").value = clip.title || "";
  document.getElementById("editColor").value = clip.color || "#3b82f6";

  const catSel = document.getElementById("editCategory");
  if (catSel) {
    catSel.innerHTML = "";
    const cats = (state.categories && state.categories.length)
      ? state.categories
      : [
          { id: "Rage", name: "Rage", emoji: "🔥" },
          { id: "Risada", name: "Risadas & Pérolas", emoji: "😂" },
          { id: "Meme", name: "Bordões & Memes", emoji: "⚡" },
          { id: "Vinhetas", name: "Vinhetas & Trilhas", emoji: "🎵" },
          { id: "Call", name: "Papo & Resenha", emoji: "💬" },
        ];
    cats.forEach((c) => {
      const opt = document.createElement("option");
      opt.value = c.id;
      opt.textContent = `${c.emoji || "📁"} ${c.name || c.id}`;
      if (clip.category === c.id) opt.selected = true;
      catSel.appendChild(opt);
    });
    catSel.value = clip.category || "Call";
  }

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

  const curClipPlaying = Boolean(
    state.activePlayback &&
    state.activePlayback.clip_id === clip.id &&
    state.activePlayback.playing
  );
  updateModalPreviewButtons(curClipPlaying);
  const curPos = curClipPlaying ? Number(state.activePlayback.position || 0) : 0;
  const cutDur = Math.max(0.05, enVal - stVal);
  const wfSeek = document.getElementById("wfSeekSlider");
  if (wfSeek) {
    wfSeek.max = cutDur.toFixed(2);
    wfSeek.value = curPos.toFixed(2);
  }
  const wfCur = document.getElementById("wfPlayCurrentTime");
  if (wfCur) wfCur.textContent = formatPlayTime(curPos);
  const wfDur = document.getElementById("wfPlayDuration");
  if (wfDur) wfDur.textContent = formatPlayTime(cutDur);

  document.getElementById("routingPopoverMenu")?.classList.add("hidden");
  document.getElementById("packPopover")?.classList.add("hidden");
  document.getElementById("editModal").classList.remove("hidden");
}

function drawWaveformCanvas(playheadAbsoluteSec = null) {
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

  const editId = document.getElementById("editClipId")?.value;
  const isCurPlaying = Boolean(
    state.activePlayback &&
    state.activePlayback.clip_id === editId &&
    state.activePlayback.playing
  );

  let currentPlayheadSec = playheadAbsoluteSec;
  if (currentPlayheadSec === null && isCurPlaying) {
    currentPlayheadSec = st + Number(state.activePlayback.position || 0);
  }

  const playheadX =
    currentPlayheadSec !== null && currentPlayheadSec >= 0
      ? Math.max(0, Math.min(w, (currentPlayheadSec / maxDur) * w))
      : null;

  for (let i = 0; i < n; i++) {
    const x = i * (barW + barGap);
    const inTrim = x + barW >= startX && x <= endX;
    const amp = Math.max(0.06, Math.min(1, Number(peaks[i]) || 0.08));
    const bh = Math.max(3, amp * (h - 10));

    let barColor = inTrim ? "#3b82f6" : "#293552";
    if (inTrim && playheadX !== null && x <= playheadX) {
      barColor = "#60a5fa";
    }

    ctx.fillStyle = barColor;
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

  // Playhead needle cursor with glowing effect
  if (playheadX !== null && (isCurPlaying || playheadAbsoluteSec !== null)) {
    ctx.save();
    ctx.shadowColor = "#38bdf8";
    ctx.shadowBlur = 8;
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(Math.max(0, playheadX - 1), 0, 2, h);
    ctx.restore();

    ctx.fillStyle = "#38bdf8";
    ctx.beginPath();
    ctx.moveTo(playheadX - 5, 0);
    ctx.lineTo(playheadX + 5, 0);
    ctx.lineTo(playheadX, 6);
    ctx.closePath();
    ctx.fill();

    ctx.beginPath();
    ctx.moveTo(playheadX - 5, h);
    ctx.lineTo(playheadX + 5, h);
    ctx.lineTo(playheadX, h - 6);
    ctx.closePath();
    ctx.fill();
  }
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

  const editModal = document.getElementById("editModal");
  const editClipId = document.getElementById("editClipId")?.value;
  if (editModal && !editModal.classList.contains("hidden") && status.clip_id === editClipId) {
    updateModalPreviewButtons(status.playing !== false);
    const wfSeek = document.getElementById("wfSeekSlider");
    if (wfSeek && document.activeElement !== wfSeek) {
      wfSeek.max = dur.toFixed(2);
      wfSeek.value = pos.toFixed(2);
    }
    const wfCur = document.getElementById("wfPlayCurrentTime");
    if (wfCur) wfCur.textContent = formatPlayTime(pos);
    const wfDur = document.getElementById("wfPlayDuration");
    if (wfDur) wfDur.textContent = formatPlayTime(dur);
    drawWaveformCanvas();
  }

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
          updateModalPreviewButtons(false);
          const wfSeek = document.getElementById("wfSeekSlider");
          if (wfSeek) wfSeek.value = "0";
          const wfCur = document.getElementById("wfPlayCurrentTime");
          if (wfCur) wfCur.textContent = "0:00.0";
          drawWaveformCanvas();
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

      // Live waveform playhead and seek slider in edit modal
      const editModal = document.getElementById("editModal");
      const editClipId = document.getElementById("editClipId")?.value;
      if (editModal && !editModal.classList.contains("hidden") && ap.clip_id === editClipId) {
        drawWaveformCanvas();
        const wfSeek = document.getElementById("wfSeekSlider");
        if (wfSeek && document.activeElement !== wfSeek) {
          wfSeek.value = curPos.toFixed(2);
        }
        const wfCur = document.getElementById("wfPlayCurrentTime");
        if (wfCur) wfCur.textContent = formatPlayTime(curPos);
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
  const wfSeek = document.getElementById("wfSeekSlider");
  if (wfSeek) {
    wfSeek.max = dur.toFixed(2);
    if (Number(wfSeek.value) > dur) wfSeek.value = "0";
  }
  const wfDur = document.getElementById("wfPlayDuration");
  if (wfDur) wfDur.textContent = formatPlayTime(dur);
}

let speakerConfirmTimer = null;

function resetSpeakerDeleteConfirm() {
  if (speakerConfirmTimer) {
    clearTimeout(speakerConfirmTimer);
    speakerConfirmTimer = null;
  }
  const btn = document.getElementById("btnDeleteSpeakerModal");
  if (!btn) return;
  delete btn.dataset.confirming;
  delete btn.dataset.confirmTarget;
  delete btn.dataset.lastClick;
  btn.textContent = "🗑️ Excluir Pessoa";
}

function openSpeakerModal(spk) {
  document.getElementById("renameSpeakerId").value = spk.id;
  document.getElementById("renameSpeakerName").value = spk.name || "";
  document.getElementById("renameSpeakerColor").value = spk.color || "#3b82f6";
  document.getElementById("routingPopoverMenu")?.classList.add("hidden");
  resetSpeakerDeleteConfirm();
  document.getElementById("speakerModal").classList.remove("hidden");
}

const commandState = { open: false, index: 0, results: [], returnFocus: null };

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
    api().play_clip(clip.id, true).then((res) => {
      if (showPlayFailure(res)) return;
      if (res && res.status) syncActivePlayback(res.status);
    });
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
  commandState.returnFocus = document.activeElement;
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
  const previous = commandState.returnFocus;
  commandState.returnFocus = null;
  if (previous && document.contains(previous) && typeof previous.focus === "function") {
    previous.focus();
  }
}

function moveCommandSelection(delta) {
  if (!commandState.results.length) return;
  commandState.index = (commandState.index + delta + commandState.results.length) % commandState.results.length;
  renderCommandPalette();
  const active = document.querySelector(".command-item.active");
  if (active) active.scrollIntoView({ block: "nearest" });
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
    updateRoutingSummary();
    api().update_settings({ monitor_device_id: id });
  };

  const micDeviceSel = document.getElementById("micDeviceSelect");
  if (micDeviceSel) {
    micDeviceSel.onchange = (e) => {
      const id = Number(e.target.value);
      state.settings.mic_device_id = id;
      api().update_settings({ mic_device_id: id });
      showToast("🎙️ Microfone atualizado");
    };
  }

  const replaySourceSel = document.getElementById("replaySourceSelect");
  if (replaySourceSel) {
    replaySourceSel.onchange = async (e) => {
      const name = String(e.target.value || "");
      state.settings.replay_source_name = name;
      await api().update_settings({ replay_source_name: name });
      await updateReplayMeter();
      showToast(
        name
          ? `⚡ Fonte do Replay fixada em: ${name}`
          : "⚡ Fonte do Replay em modo automático (segue a saída padrão do Windows)"
      );
    };
  }

  const btnRefreshReplay = document.getElementById("btnRefreshReplaySources");
  if (btnRefreshReplay) {
    btnRefreshReplay.onclick = async () => {
      if (api() && typeof api().refresh_devices === "function") {
        const devs = await api().refresh_devices();
        if (devs) state.devices = devs;
      }
      renderDevices();
      await refreshReplaySources();
      showToast("🔄 Dispositivos de áudio atualizados");
    };
  }

  document.getElementById("cableVolumeSlider").oninput = (e) => {
    const v = Number(e.target.value);
    document.getElementById("cableVolumeVal").textContent = `${Math.round(v * 100)}%`;
    state.settings.cable_volume = v;
    updateRoutingSummary();
    api().update_settings({ cable_volume: v });
  };

  document.getElementById("monitorVolumeSlider").oninput = (e) => {
    const v = Number(e.target.value);
    document.getElementById("monitorVolumeVal").textContent = `${Math.round(v * 100)}%`;
    state.settings.monitor_volume = v;
    updateRoutingSummary();
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

  // Routing Popover toggle and outside dismissal
  const btnRouting = document.getElementById("btnRoutingPopover");
  const routingMenu = document.getElementById("routingPopoverMenu");
  const btnCloseRouting = document.getElementById("btnCloseRoutingPopover");
  if (btnRouting && routingMenu) {
    btnRouting.onclick = (e) => {
      e.stopPropagation();
      const willOpen = routingMenu.classList.contains("hidden");
      routingMenu.classList.toggle("hidden");
      if (willOpen) {
        startReplayMeterPolling();
      } else {
        stopReplayMeterPolling();
      }
    };
    if (btnCloseRouting) {
      btnCloseRouting.onclick = (e) => {
        e.stopPropagation();
        routingMenu.classList.add("hidden");
        stopReplayMeterPolling();
      };
    }
    document.addEventListener("click", (e) => {
      if (!routingMenu.classList.contains("hidden") && !routingMenu.contains(e.target) && !btnRouting.contains(e.target)) {
        routingMenu.classList.add("hidden");
        stopReplayMeterPolling();
      }
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && !routingMenu.classList.contains("hidden")) {
        routingMenu.classList.add("hidden");
        stopReplayMeterPolling();
      }
    });
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
    showToast(`⚡ Capturando os últimos ${secs}s da call...`, "#f59e0b");
    const res = await api().capture_replay_draft(secs);
    if (!res || !res.ok) {
      showToast((res && res.error) || "Sem áudio recente no buffer da call.", "#ef4444");
      return;
    }
    state.clips = res.clips || state.clips;
    state.speakers = res.speakers || state.speakers;
    renderSidebar();
    renderGrid();
    openEditModal(res.clip);
    showToast("✂️ Revise o corte e clique em Salvar no Soundboard.", "#3b82f6");
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

  // Pack menu (export/import .zip)
  const packMenu = document.getElementById("packPopover");
  const packButton = document.getElementById("btnPackMenu");
  function hidePackMenu() {
    if (packMenu) packMenu.classList.add("hidden");
  }
  if (packButton && packMenu) {
    packButton.onclick = (e) => {
      e.stopPropagation();
      packMenu.classList.toggle("hidden");
    };
    const btnClosePackMenu = document.getElementById("btnClosePackMenu");
    if (btnClosePackMenu) {
      btnClosePackMenu.onclick = (e) => {
        e.stopPropagation();
        hidePackMenu();
      };
    }
    document.addEventListener("click", (e) => {
      if (packMenu.classList.contains("hidden")) return;
      if (!packMenu.contains(e.target) && !packButton.contains(e.target)) hidePackMenu();
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && !packMenu.classList.contains("hidden")) hidePackMenu();
    });
  }

  function showPackWarnings(warnings) {
    const list = document.getElementById("packWarningsList");
    const modal = document.getElementById("packWarningsModal");
    if (!list || !modal) return;
    list.innerHTML = "";
    warnings.forEach((w) => {
      const li = document.createElement("li");
      li.textContent = w;
      list.appendChild(li);
    });
    modal.classList.remove("hidden");
  }

  const closePackWarnings = document.getElementById("btnClosePackWarnings");
  if (closePackWarnings) {
    closePackWarnings.onclick = () =>
      document.getElementById("packWarningsModal").classList.add("hidden");
  }

  const btnExportPackAll = document.getElementById("btnExportPackAll");
  if (btnExportPackAll) {
    btnExportPackAll.onclick = async () => {
      hidePackMenu();
      const res = await api().export_pack(null, null);
      if (!res || !res.ok) {
        if ((res && res.error) !== "Exportação cancelada.") {
          showToast((res && res.error) || "Falha ao exportar.", "#ef4444");
        }
        return;
      }
      const extra = res.skipped ? ` (${res.skipped} sem arquivo)` : "";
      showToast(`📦 ${res.count} áudio(s) exportados${extra}.`, "#10b981");
    };
  }

  const btnExportPackFiltered = document.getElementById("btnExportPackFiltered");
  if (btnExportPackFiltered) {
    btnExportPackFiltered.onclick = async () => {
      hidePackMenu();
      const ids = getFilteredClips().map((c) => c.id);
      if (!ids.length) {
        showToast("Nenhum áudio no filtro atual.", "#f59e0b");
        return;
      }
      const res = await api().export_pack(ids, null);
      if (!res || !res.ok) {
        if ((res && res.error) !== "Exportação cancelada.") {
          showToast((res && res.error) || "Falha ao exportar.", "#ef4444");
        }
        return;
      }
      showToast(`📦 ${res.count} áudio(s) do filtro exportados.`, "#10b981");
    };
  }

  const btnImportPack = document.getElementById("btnImportPack");
  if (btnImportPack) {
    btnImportPack.onclick = async () => {
      hidePackMenu();
      const res = await api().import_pack(null);
      if (!res || !res.ok) {
        if ((res && res.error) !== "Importação cancelada.") {
          showToast((res && res.error) || "Falha ao importar.", "#ef4444");
        }
        return;
      }
      state.clips = res.clips || state.clips;
      state.speakers = res.speakers || state.speakers;
      renderSidebar();
      renderGrid();
      const importedCount = Array.isArray(res.imported) ? res.imported.length : Number(res.imported) || 0;
      showToast(`📦 ${importedCount} importado(s), ${res.skipped || 0} ignorado(s).`, "#10b981");
      if (res.warnings && res.warnings.length) showPackWarnings(res.warnings);
    };
  }

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
      const clipId = state.activePlayback ? state.activePlayback.clip_id : null;
      const findClipIndex = () => (clipId === null ? -1 : state.clips.findIndex((x) => x.id === clipId));
      const idx = findClipIndex();
      if (idx >= 0) state.clips[idx].volume = v;
      api()
        .set_live_clip_volume(v)
        .then((res) => {
          if (!res || !res.ok || typeof res.volume !== "number") return;
          const i = findClipIndex();
          if (i >= 0) state.clips[i].volume = res.volume;
        })
        .catch(() => {});
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

  // Ctrl+K command palette
  const commandInput = document.getElementById("commandInput");
  if (commandInput) {
    commandInput.oninput = (e) => {
      commandState.index = 0;
      commandState.results = commandPaletteResults(e.target.value);
      renderCommandPalette();
    };
  }
  const commandPaletteEl = document.getElementById("commandPalette");
  if (commandPaletteEl) {
    commandPaletteEl.onkeydown = (e) => {
      if (e.key === "ArrowDown") { e.preventDefault(); moveCommandSelection(1); }
      else if (e.key === "ArrowUp") { e.preventDefault(); moveCommandSelection(-1); }
      else if (e.key === "Enter") {
        e.preventDefault();
        const clip = commandState.results[commandState.index];
        if (clip) playFromPalette(clip, e.shiftKey);
      } else if (e.key === "Escape") { e.preventDefault(); closeCommandPalette(); }
    };
    commandPaletteEl.onclick = (e) => {
      if (e.target.id === "commandPalette") {
        closeCommandPalette();
        return;
      }
      // Keep keyboard shortcuts working after clicking panel padding/footer.
      if (commandInput && e.target !== commandInput && !e.target.closest(".command-item")) {
        commandInput.focus();
      }
    };
  }
  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
      const openOverlay = document.querySelector(".modal-backdrop:not(.hidden)");
      if (openOverlay) return;
      e.preventDefault();
      if (commandState.open) closeCommandPalette(); else openCommandPalette();
    }
  });

  window.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "f") {
      e.preventDefault();
      searchInput.focus();
    }
  });

  // Modal bindings
  document.getElementById("btnCloseModal").onclick = () => {
    if (state.draftClipId) {
      discardDraftAndClose();
      return;
    }
    closeEditModal();
  };

  // Esc / backdrop: a draft discards, the warnings and environment modals close.
  // A non-draft edit modal keeps the current behavior (no close path).
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    const warningsModal = document.getElementById("packWarningsModal");
    if (warningsModal && !warningsModal.classList.contains("hidden")) {
      e.preventDefault();
      warningsModal.classList.add("hidden");
      return;
    }
    const envModalEl = document.getElementById("envModal");
    if (envModalEl && !envModalEl.classList.contains("hidden")) {
      e.preventDefault();
      closeEnvModal();
      return;
    }
    if (!state.draftClipId || state.draftCommitting) return;
    const editModalEl = document.getElementById("editModal");
    if (!editModalEl || editModalEl.classList.contains("hidden")) return;
    e.preventDefault();
    discardDraftAndClose();
  });

  const editModalBackdrop = document.getElementById("editModal");
  if (editModalBackdrop) {
    editModalBackdrop.addEventListener("click", (e) => {
      if (e.target.id !== "editModal") return;
      if (!state.draftClipId || state.draftCommitting) return;
      discardDraftAndClose();
    });
  }

  const packWarningsBackdrop = document.getElementById("packWarningsModal");
  if (packWarningsBackdrop) {
    packWarningsBackdrop.addEventListener("click", (e) => {
      if (e.target.id === "packWarningsModal") packWarningsBackdrop.classList.add("hidden");
    });
  }

  // Environment (doctor) modal bindings
  const btnEnvironment = document.getElementById("btnEnvironment");
  if (btnEnvironment) {
    btnEnvironment.onclick = (e) => {
      e.stopPropagation();
      openEnvModal();
    };
  }
  const btnEnvBanner = document.getElementById("btnEnvBanner");
  if (btnEnvBanner) btnEnvBanner.onclick = () => openEnvModal();
  const btnEnvBannerClose = document.getElementById("btnEnvBannerClose");
  if (btnEnvBannerClose) {
    btnEnvBannerClose.onclick = () => {
      state.envBannerDismissed = true;
      try {
        sessionStorage.setItem("env_banner_dismissed", "1");
      } catch (_) {}
      const banner = document.getElementById("envBanner");
      if (banner) banner.classList.add("hidden");
    };
  }
  const btnCloseEnvModal = document.getElementById("btnCloseEnvModal");
  if (btnCloseEnvModal) btnCloseEnvModal.onclick = closeEnvModal;
  const btnCloseEnvFooter = document.getElementById("btnCloseEnvFooter");
  if (btnCloseEnvFooter) btnCloseEnvFooter.onclick = closeEnvModal;

  const btnPickModelsDir = document.getElementById("btnPickModelsDir");
  if (btnPickModelsDir) btnPickModelsDir.onclick = () => pickEnvDirectory("models_dir");
  const btnPickSeparadosRoot = document.getElementById("btnPickSeparadosRoot");
  if (btnPickSeparadosRoot) btnPickSeparadosRoot.onclick = () => pickEnvDirectory("separados_root");

  const btnRefreshEnv = document.getElementById("btnRefreshEnv");
  if (btnRefreshEnv) {
    btnRefreshEnv.onclick = async () => {
      if (!api()) return;
      btnRefreshEnv.disabled = true;
      showToast("Verificando ambiente...");
      try {
        const env = await api().refresh_environment();
        if (env && Array.isArray(env.checks)) {
          renderEnvironment(env);
          applyEnvPaths(state.config);
          const pending = Number(env.incomplete || 0);
          showToast(
            pending > 0 ? `Ambiente com ${pending} pendência(s).` : "✅ Ambiente pronto.",
            pending > 0 ? "#f59e0b" : "#10b981"
          );
        }
      } finally {
        btnRefreshEnv.disabled = false;
      }
    };
  }

  const envModalBackdrop = document.getElementById("envModal");
  if (envModalBackdrop) {
    envModalBackdrop.addEventListener("click", (e) => {
      if (e.target.id === "envModal") envModalBackdrop.classList.add("hidden");
    });
  }

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
      const rect = wfCanvas.getBoundingClientRect();
      const w = Math.max(1, rect.width);
      const maxDur = Number(trimEndSlider.max) || 10;
      const curSt = Number(trimStartSlider.value) || 0;
      const curEn = Number(trimEndSlider.value) || maxDur;
      const startX = (curSt / maxDur) * w;
      const endX = (curEn / maxDur) * w;
      const pointerX = ev.clientX - rect.left;
      const t = pointerToSec(ev);
      const HANDLE_HIT_PX = 10;

      if (Math.abs(pointerX - startX) <= HANDLE_HIT_PX) {
        wfDragTarget = "start";
        wfCanvas.setPointerCapture(ev.pointerId);
        syncTrimValues(Math.min(t, curEn - 0.1), curEn);
      } else if (Math.abs(pointerX - endX) <= HANDLE_HIT_PX) {
        wfDragTarget = "end";
        wfCanvas.setPointerCapture(ev.pointerId);
        syncTrimValues(curSt, Math.max(t, curSt + 0.1));
      } else if (pointerX >= startX && pointerX <= endX) {
        wfDragTarget = "seek";
        wfCanvas.setPointerCapture(ev.pointerId);
        const offset = Math.max(0, Math.min(curEn - curSt, t - curSt));
        const wfSeek = document.getElementById("wfSeekSlider");
        if (wfSeek) wfSeek.value = offset.toFixed(2);
        const wfCur = document.getElementById("wfPlayCurrentTime");
        if (wfCur) wfCur.textContent = formatPlayTime(offset);
        const isPlaying =
          state.activePlayback &&
          state.activePlayback.clip_id === document.getElementById("editClipId")?.value &&
          state.activePlayback.playing;
        if (isPlaying) {
          api().seek_playback(offset).then((res) => {
            if (res && res.status) syncActivePlayback(res.status);
          });
        } else {
          drawWaveformCanvas(curSt + offset);
        }
      } else {
        wfDragTarget = Math.abs(t - curSt) <= Math.abs(t - curEn) ? "start" : "end";
        wfCanvas.setPointerCapture(ev.pointerId);
        if (wfDragTarget === "start") syncTrimValues(Math.min(t, curEn - 0.1), curEn);
        else syncTrimValues(curSt, Math.max(t, curSt + 0.1));
      }
    });

    wfCanvas.addEventListener("pointermove", (ev) => {
      const rect = wfCanvas.getBoundingClientRect();
      const w = Math.max(1, rect.width);
      const maxDur = Number(trimEndSlider.max) || 10;
      const curSt = Number(trimStartSlider.value) || 0;
      const curEn = Number(trimEndSlider.value) || maxDur;
      const startX = (curSt / maxDur) * w;
      const endX = (curEn / maxDur) * w;
      const pointerX = ev.clientX - rect.left;

      if (!wfDragTarget) {
        const HANDLE_HIT_PX = 10;
        if (Math.abs(pointerX - startX) <= HANDLE_HIT_PX || Math.abs(pointerX - endX) <= HANDLE_HIT_PX) {
          wfCanvas.style.cursor = "ew-resize";
        } else if (pointerX >= startX && pointerX <= endX) {
          wfCanvas.style.cursor = "pointer";
        } else {
          wfCanvas.style.cursor = "default";
        }
        return;
      }

      const t = pointerToSec(ev);
      if (wfDragTarget === "start") syncTrimValues(Math.min(t, curEn - 0.1), curEn);
      else if (wfDragTarget === "end") syncTrimValues(curSt, Math.max(t, curSt + 0.1));
      else if (wfDragTarget === "seek") {
        const offset = Math.max(0, Math.min(curEn - curSt, t - curSt));
        const wfSeek = document.getElementById("wfSeekSlider");
        if (wfSeek) wfSeek.value = offset.toFixed(2);
        const wfCur = document.getElementById("wfPlayCurrentTime");
        if (wfCur) wfCur.textContent = formatPlayTime(offset);
        const isPlaying =
          state.activePlayback &&
          state.activePlayback.clip_id === document.getElementById("editClipId")?.value &&
          state.activePlayback.playing;
        if (isPlaying) {
          api().seek_playback(offset).then((res) => {
            if (res && res.status) syncActivePlayback(res.status);
          });
        } else {
          drawWaveformCanvas(curSt + offset);
        }
      }
    });

    const endWfDrag = () => {
      wfDragTarget = null;
    };
    wfCanvas.addEventListener("pointerup", endWfDrag);
    wfCanvas.addEventListener("pointercancel", endWfDrag);
  }

  const wfSeekSlider = document.getElementById("wfSeekSlider");
  if (wfSeekSlider) {
    wfSeekSlider.oninput = (e) => {
      const pos = Number(e.target.value) || 0;
      const curEl = document.getElementById("wfPlayCurrentTime");
      if (curEl) curEl.textContent = formatPlayTime(pos);
      const curSt = Number(trimStartSlider.value) || 0;
      drawWaveformCanvas(curSt + pos);
    };
    wfSeekSlider.onchange = async (e) => {
      const pos = Number(e.target.value) || 0;
      const isPlaying =
        state.activePlayback &&
        state.activePlayback.clip_id === document.getElementById("editClipId")?.value &&
        state.activePlayback.playing;
      if (isPlaying) {
        const res = await api().seek_playback(pos);
        if (res && res.status) syncActivePlayback(res.status);
      }
    };
  }

  trimStartSlider.oninput = (e) => syncTrimValues(e.target.value, trimEndSlider.value);
  trimEndSlider.oninput = (e) => syncTrimValues(trimStartSlider.value, e.target.value);
  trimStartNum.onchange = (e) => syncTrimValues(e.target.value, trimEndNum.value);
  trimEndNum.onchange = (e) => syncTrimValues(trimStartNum.value, e.target.value);

  document.querySelectorAll(".btn-preset[data-preset]").forEach((btn) => {
    btn.onclick = () => {
      const maxDur = Number(trimEndSlider.max) || 10;
      const preset = btn.getAttribute("data-preset");
      if (preset === "first3") syncTrimValues(0, Math.min(3, maxDur));
      else if (preset === "first5") syncTrimValues(0, Math.min(5, maxDur));
      else if (preset === "last3") syncTrimValues(Math.max(0, maxDur - 3), maxDur);
      else if (preset === "last5") syncTrimValues(Math.max(0, maxDur - 5), maxDur);
      else if (preset === "full") syncTrimValues(0, maxDur);
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

  async function toggleModalAudioPreview() {
    const id = document.getElementById("editClipId").value;
    if (!id) return;
    if (state.activePlayback && state.activePlayback.clip_id === id && state.activePlayback.playing) {
      api().stop_all();
      state.activePlayback = null;
      delete state.playingClips[id];
      if (playbackRafId) {
        cancelAnimationFrame(playbackRafId);
        playbackRafId = null;
      }
      updateModalPreviewButtons(false);
      const wfSeek = document.getElementById("wfSeekSlider");
      if (wfSeek) wfSeek.value = "0";
      const wfCur = document.getElementById("wfPlayCurrentTime");
      if (wfCur) wfCur.textContent = "0:00.0";
      renderGrid();
      drawWaveformCanvas();
      return;
    }

    // Save temporary adjustments without closing modal
    await saveCurrentClipEdits(false);

    // Play clip locally on headphone monitor only
    const res = await api().play_clip(id, true);
    if (showPlayFailure(res)) {
      updateModalPreviewButtons(false);
      return;
    }
    if (res && res.status) {
      syncActivePlayback(res.status);
      updateModalPreviewButtons(true);
      const wfSeek = document.getElementById("wfSeekSlider");
      const seekVal = Number(wfSeek?.value || 0);
      if (seekVal > 0.05) {
        api().seek_playback(seekVal).then((sRes) => {
          if (sRes && sRes.status) syncActivePlayback(sRes.status);
        });
      }
    }
  }

  document.getElementById("btnSaveClip").onclick = async () => {
    if (state.draftClipId) {
      await commitReplayDraft();
      return;
    }
    await saveCurrentClipEdits(true);
  };

  document.getElementById("btnDiscardDraft").onclick = () => discardDraftAndClose();

  document.getElementById("btnPreviewMonitor").onclick = toggleModalAudioPreview;
  const btnTrimHeader = document.getElementById("btnPreviewTrimHeader");
  if (btnTrimHeader) {
    btnTrimHeader.onclick = toggleModalAudioPreview;
  }

  const btnReanalyzeLufs = document.getElementById("btnReanalyzeLufs");
  if (btnReanalyzeLufs) {
    btnReanalyzeLufs.onclick = async () => {
      const id = document.getElementById("editClipId").value;
      if (!id || !api()) return;
      btnReanalyzeLufs.disabled = true;
      try {
        const res = await api().reanalyze_clip_loudness(id);
        if (res && res.ok && res.clip) {
          const idx = state.clips.findIndex((c) => c.id === id);
          if (idx >= 0) state.clips[idx] = res.clip;
          renderClipLufsBadge(res.clip);
          renderGrid();
          showToast("Nível de loudness recalculado.", "#10b981");
        } else {
          showToast(res?.error || "Falha ao recalcular nível.", "#ef4444");
        }
      } finally {
        btnReanalyzeLufs.disabled = false;
      }
    };
  }

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
      closeEditModal();
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
    if (!res || !res.ok) {
      showToast((res && res.error) || "Não foi possível remover.", "#ef4444");
      return;
    }
    state.clips = state.clips.filter((c) => c.id !== id);
    closeEditModal();
    renderSidebar();
    renderGrid();

    const deleted = res.clip;
    if (res.can_undo && deleted) {
      if (pendingUndoToast) {
        pendingUndoToast.remove();
        pendingUndoToast = null;
      }
      const toastEl = showToast("🗑️ Removido do Soundboard", "#f59e0b", {
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
      pendingUndoToast = toastEl;
      setTimeout(() => {
        if (pendingUndoToast === toastEl) pendingUndoToast = null;
      }, 8200);
    } else {
      showToast("🗑️ Botão removido.");
    }
  };

  // Speaker rename & delete modal
  document.getElementById("btnCloseSpeakerModal").onclick = () => {
    resetSpeakerDeleteConfirm();
    document.getElementById("speakerModal").classList.add("hidden");
  };

  const deleteSpeakerBtn = document.getElementById("btnDeleteSpeakerModal");
  deleteSpeakerBtn.onclick = async () => {
    const now = Date.now();
    const lastClick = Number(deleteSpeakerBtn.dataset.lastClick || 0);
    deleteSpeakerBtn.dataset.lastClick = String(now);
    if (now - lastClick < 250) return;

    const currentSpeakerId = document.getElementById("renameSpeakerId").value;

    if (!deleteSpeakerBtn.dataset.confirming) {
      deleteSpeakerBtn.dataset.confirming = "1";
      deleteSpeakerBtn.dataset.confirmTarget = currentSpeakerId;
      deleteSpeakerBtn.textContent = "⚠️ Clique de novo para excluir";
      speakerConfirmTimer = setTimeout(() => {
        resetSpeakerDeleteConfirm();
      }, 4000);
      return;
    }

    const armedTarget = deleteSpeakerBtn.dataset.confirmTarget;
    resetSpeakerDeleteConfirm();
    if (!armedTarget || armedTarget !== currentSpeakerId) return;

    const res = await api().delete_speaker(currentSpeakerId);
    if (res && res.ok) {
      state.speakers = res.speakers || {};
      state.clips = res.clips || state.clips;
      if (state.activeSpeaker === currentSpeakerId) state.activeSpeaker = "Todos";
      document.getElementById("speakerModal").classList.add("hidden");
      renderSidebar();
      renderGrid();
      showToast("🗑️ Interlocutor excluído (áudios mantidos).");
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
    updateModalPreviewButtons(false);
    const wfSeek = document.getElementById("wfSeekSlider");
    if (wfSeek) wfSeek.value = "0";
    const wfCur = document.getElementById("wfPlayCurrentTime");
    if (wfCur) wfCur.textContent = "0:00.0";
    drawWaveformCanvas();
    renderGrid();
  } else if (event === "stopped_all") {
    state.playingClips = {};
    state.activePlayback = null;
    updateModalPreviewButtons(false);
    const wfSeek = document.getElementById("wfSeekSlider");
    if (wfSeek) wfSeek.value = "0";
    const wfCur = document.getElementById("wfPlayCurrentTime");
    if (wfCur) wfCur.textContent = "0:00.0";
    drawWaveformCanvas();
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
  } else if (event === "environment_updated") {
    if (payload && Array.isArray(payload.checks)) renderEnvironment(payload);
  } else if (event === "toast") {
    showToast(payload.message || "", "#f59e0b");
  }
};

window.addEventListener("DOMContentLoaded", initApp);
