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


const ICONS = {"play":"<path d=\"m8 5 11 7-11 7Z\"/>","stop":"<rect x=\"5\" y=\"5\" width=\"14\" height=\"14\" rx=\"2\"/>","headphones":"<path d=\"M3 14v-3a9 9 0 0 1 18 0v3\"/><rect x=\"3\" y=\"12\" width=\"4\" height=\"8\" rx=\"2\"/><rect x=\"17\" y=\"12\" width=\"4\" height=\"8\" rx=\"2\"/>","sliders":"<path d=\"M4 7h9m4 0h3M4 17h3m4 0h9\"/><circle cx=\"15\" cy=\"7\" r=\"2\"/><circle cx=\"9\" cy=\"17\" r=\"2\"/>","search":"<circle cx=\"10.5\" cy=\"10.5\" r=\"6.5\"/><path d=\"m16 16 5 5\"/>","plus":"<path d=\"M12 5v14M5 12h14\"/>","close":"<path d=\"m6 6 12 12M6 18 18 6\"/>","grid":"<rect x=\"3\" y=\"3\" width=\"7\" height=\"7\" rx=\"1\"/><rect x=\"14\" y=\"3\" width=\"7\" height=\"7\" rx=\"1\"/><rect x=\"3\" y=\"14\" width=\"7\" height=\"7\" rx=\"1\"/><rect x=\"14\" y=\"14\" width=\"7\" height=\"7\" rx=\"1\"/>","star":"<path d=\"m12 3 2.8 5.7 6.2.9-4.5 4.4 1.1 6.2-5.6-3-5.6 3 1.1-6.2L3 9.6l6.2-.9Z\"/>","edit":"<path d=\"m14 5 5 5M4 20l4-1L20 7a2.8 2.8 0 0 0-4-4L4 15Z\"/>","trash":"<path d=\"M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7m4-7v7\"/>","music":"<path d=\"M9 18V5l12-2v13M9 9l12-2\"/><ellipse cx=\"6\" cy=\"18\" rx=\"3\" ry=\"3\"/><ellipse cx=\"18\" cy=\"16\" rx=\"3\" ry=\"3\"/>","mic":"<rect x=\"9\" y=\"2\" width=\"6\" height=\"13\" rx=\"3\"/><path d=\"M5 10v2a7 7 0 0 0 14 0v-2M12 19v3M8 22h8\"/>","users":"<circle cx=\"9\" cy=\"8\" r=\"3\"/><path d=\"M3 21v-3a6 6 0 0 1 12 0v3M17 5a3 3 0 0 1 0 6m1 4a5 5 0 0 1 3 5\"/>","folder":"<path d=\"M3 7V4h6l2 3h10v13H3Z\"/>","capture":"<path d=\"M4 8V4h4m8 0h4v4M4 16v4h4m8 0h4v-4\"/><circle cx=\"12\" cy=\"12\" r=\"4\"/>","window":"<rect x=\"3\" y=\"4\" width=\"18\" height=\"16\" rx=\"2\"/><path d=\"M13 12h8v8h-8Z\"/>","spark":"<path d=\"m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5Z\"/>","chevron":"<path d=\"m6 9 6 6 6-6\"/>","volume":"<path d=\"M4 9h4l5-5v16l-5-5H4ZM17 8a6 6 0 0 1 0 8m3-11a10 10 0 0 1 0 14\"/>","tag":"<path d=\"M3 3h8l10 10-8 8L3 11Z\"/><circle cx=\"7.5\" cy=\"7.5\" r=\"1\"/>"};
function icon(name) {
  return '<svg class="od-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + (ICONS[name] || ICONS.grid) + '</svg>';
}
function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, c => ({ "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;" }[c]));
}
function safeColor(value) { return /^#[0-9a-f]{6}$/i.test(value || "") ? value : "#8fb5ff"; }
function restoreFocus(key) {
  if (!key) return;
  const target = [...document.querySelectorAll("[data-focus-key]")].find(el => el.dataset.focusKey === key);
  (target || document.getElementById("searchInput"))?.focus({preventScroll:true});
}
function rememberFocus() { return document.activeElement?.dataset.focusKey; }
let lastDialogFocus = null;
function openModal(id, field) {
  const dialog = document.getElementById(id);
  if (dialog.classList.contains("hidden")) {
    lastDialogFocus = { element:document.activeElement, key:rememberFocus() };
  }
  dialog.classList.remove("hidden");
  document.getElementById("app").inert = true;
  document.getElementById(field)?.focus();
}
function closeModal(id) {
  document.getElementById(id).classList.add("hidden");
  document.getElementById("app").inert = false;
  if (lastDialogFocus?.key) restoreFocus(lastDialogFocus.key);
  else if (lastDialogFocus?.element?.isConnected) lastDialogFocus.element.focus();
  else document.getElementById("searchInput").focus();
}
const previewApi = new Proxy({}, {
  get: () => async () => {
    showToast("Esta prévia não altera o catálogo. Abra o aplicativo local para usar áudio, IA e salvar alterações.");
    return {ok:false};
  }
});
let initialized = false;
let filterSignature = "";
let gridLimit = 48;

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
  return window.pywebview?.api || (window.soundboardPreview ? previewApi : null);
}

async function initApp() {
  if (!api()) {
    setTimeout(initApp, 80);
    return;
  }
  if (initialized) return;
  initialized = true;
  let initial;
  try {
    initial = window.pywebview?.api ? await window.pywebview.api.get_initial_state() : window.soundboardPreview;
  } catch (error) {
    initialized = false;
    const notice = document.getElementById("connectionNotice");
    notice.textContent = "Não foi possível carregar o aplicativo. Feche e abra o Soundboard novamente.";
    notice.classList.remove("hidden");
    return;
  }
  const notice = document.getElementById("connectionNotice");
  notice.classList.toggle("hidden", !window.soundboardPreview);
  if (window.soundboardPreview) notice.textContent = "Prévia do catálogo local · busca, filtros e editor disponíveis. Reprodução, IA e gravação de alterações exigem o aplicativo Python.";
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
  if (window.soundboardPreview) {
    ["btnReplayClip","btnStopAll","btnImportFiles","btnImportVinheta","btnImportFolder",
     "btnSaveClip","btnPreviewMonitor","btnReanalyzeAI","btnSplitSpeakers","btnEnrollVoice",
     "btnDeleteClip","btnDeleteSpeakerModal","btnSaveSpeaker"].forEach(id => {
      const el = document.getElementById(id);
      el.disabled = true;
      el.title = "Disponível no aplicativo local";
    });
    document.querySelectorAll("#routingPanel input,#routingPanel select,#replaySecondsInput").forEach(el => el.disabled = true);
    document.getElementById("btnMiniOverlay").onclick = () => document.body.classList.toggle("mini-overlay");
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
    opt1.textContent = d.name;
    if (state.settings.cable_device_id === d.id) opt1.selected = true;
    cableSel.appendChild(opt1);

    const opt2 = document.createElement("option");
    opt2.value = d.id;
    opt2.textContent = d.name;
    if (state.settings.monitor_device_id === d.id) opt2.selected = true;
    monSel.appendChild(opt2);
  });

  if (!outputs.length) {
    [cableSel, monSel].forEach(select => {
      const option = document.createElement("option");
      option.textContent = window.soundboardPreview ? "Dispositivo do aplicativo local" : "Nenhuma saída disponível";
      select.appendChild(option);
    });
  }
  if (state.devices.detected_cable_id !== null && state.devices.detected_cable_id !== undefined) {
    badge.textContent = "VB-Cable detectado";
    badge.className = "status-pill status-ok";
  } else {
    badge.textContent = window.soundboardPreview ? "Disponível no aplicativo" : "VB-Cable não detectado";
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
    btn.textContent = `Importar ${item.label}`;
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
  const focus = rememberFocus();
  const catList = document.getElementById("categoryList");
  catList.replaceChildren();
  const allItems = [
    {id:"Todos",name:"Todos os áudios",count:state.clips.length},
    {id:"Favoritos",name:"Favoritos",count:state.clips.filter(c=>c.favorite).length},
    ...state.categories.map(cat=>({...cat,count:state.clips.filter(c=>c.category===cat.id).length}))
  ];
  const names = {Rage:"Rage / surto",Risada:"Risadas",Meme:"Memes / pérolas",Call:"Falas / call",Vinhetas:"Vinhetas",Efeito:"Efeitos sonoros",Clássico:"Clássicos"};
  allItems.forEach(item => {
    const active = item.id==="Favoritos" ? state.favoritesOnly : !state.favoritesOnly && item.id===state.activeCategory;
    const row = document.createElement("div");
    row.className = "nav-item" + (active?" active":"");
    const button = document.createElement("button");
    button.className = "nav-choice";
    button.dataset.focusKey = "category:"+item.id;
    button.setAttribute("aria-pressed",String(active));
    const name = names[item.id] || item.name || item.id;
    const glyph = item.id==="Favoritos"?"star":item.id==="Todos"?"grid":item.id==="Vinhetas"?"music":item.id==="Call"?"mic":"tag";
    button.innerHTML = '<span class="nav-item-left">'+icon(glyph)+'<span>'+escapeHtml(name)+'</span></span><span class="count-pill">'+item.count+'</span>';
    button.onclick = () => {
      state.favoritesOnly = item.id==="Favoritos";
      state.activeCategory = state.favoritesOnly ? "Todos" : item.id;
      renderSidebar();renderGrid();
    };
    row.append(button);catList.append(row);
  });
  const speakerList = document.getElementById("speakerList");
  speakerList.replaceChildren();
  const idsOf = c=>c.speaker_ids?.length ? c.speaker_ids : c.speaker_id ? [c.speaker_id] : [];
  const speakers = [
    {id:"Todos",name:"Todas as vozes",count:state.clips.length,system:true},
    {id:"__none__",name:"Sem pessoa",count:state.clips.filter(c=>!idsOf(c).length).length,system:true},
    ...Object.values(state.speakers).map(s=>({...s,count:state.clips.filter(c=>idsOf(c).includes(s.id)).length}))
  ];
  speakers.forEach(spk=>{
    const row = document.createElement("div");
    row.className = "nav-item"+(state.activeSpeaker===spk.id?" active":"");
    const button = document.createElement("button");
    button.className = "nav-choice";button.dataset.focusKey = "speaker:"+spk.id;
    button.setAttribute("aria-pressed",String(state.activeSpeaker===spk.id));
    button.innerHTML = '<span class="nav-item-left">'+(spk.system?icon(spk.id==="Todos"?"users":"music"):'<span class="speaker-dot" style="--speaker-color:'+safeColor(spk.color)+'"></span>')+'<span>'+escapeHtml(spk.name)+'</span></span><span class="count-pill">'+spk.count+'</span>';
    button.onclick = ()=>{state.activeSpeaker=spk.id;renderSidebar();renderGrid();};
    row.append(button);
    if (!spk.system) {
      const actions = document.createElement("div");actions.className="nav-item-right";
      const edit=document.createElement("button");edit.className="btn-rename-spk";
      edit.innerHTML=icon("edit");edit.title="Editar "+spk.name;edit.setAttribute("aria-label",edit.title);
      edit.dataset.focusKey="edit-speaker:"+spk.id;edit.onclick=()=>openSpeakerModal(spk);
      const remove=document.createElement("button");remove.className="btn-delete-spk";
      remove.innerHTML=icon("trash");remove.title="Excluir "+spk.name;remove.setAttribute("aria-label",remove.title);
      remove.disabled=Boolean(window.soundboardPreview);
      remove.onclick=async()=>{
        if (!window.confirm('Excluir "'+spk.name+'"? Os áudios serão mantidos.')) return;
        const res=await api().delete_speaker(spk.id);
        if(res?.ok){
          state.speakers=res.speakers||{};state.clips=res.clips||state.clips;
          if(state.activeSpeaker===spk.id)state.activeSpeaker="Todos";
          renderSidebar();renderGrid();document.getElementById("searchInput").focus();
          showToast("Pessoa removida. Os áudios foram mantidos.");
        }
      };
      actions.append(edit,remove);row.append(actions);
    }
    speakerList.append(row);
  });
  restoreFocus(focus);
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


function resetFilters() {
  state.activeCategory="Todos";state.activeSpeaker="Todos";state.favoritesOnly=false;state.searchQuery="";
  document.getElementById("searchInput").value="";
  document.getElementById("btnClearSearch").classList.add("hidden");
  renderSidebar();renderGrid();document.getElementById("searchInput").focus();
}
function renderGrid() {
  const focus=rememberFocus();
  const grid=document.getElementById("soundGrid");
  const filtered=getFilteredClips();
  const signature=JSON.stringify([state.activeCategory,state.activeSpeaker,state.favoritesOnly,state.searchQuery,state.sortBy]);
  if(signature!==filterSignature){filterSignature=signature;gridLimit=48;}
  const hasFilter=state.activeCategory!=="Todos"||state.activeSpeaker!=="Todos"||state.favoritesOnly||Boolean(state.searchQuery);
  const title=state.favoritesOnly?"Favoritos":state.activeCategory==="Todos"?"Todos os áudios":(state.categories.find(c=>c.id===state.activeCategory)?.name||state.activeCategory);
  document.getElementById("libraryTitle").textContent=title;
  document.getElementById("libraryDescription").textContent=state.activeSpeaker==="Todos"?
    "Dispare na call. Ouça no fone. Guarde o que vale repetir.":
    "Filtrando por "+(state.activeSpeaker==="__none__"?"áudios sem pessoa":state.speakers[state.activeSpeaker]?.name||"voz selecionada");
  document.getElementById("btnResetFilters").classList.toggle("hidden",!hasFilter);
  document.getElementById("clipCountBadge").textContent=filtered.length+" "+(filtered.length===1?"áudio":"áudios")+(hasFilter?" de "+state.clips.length:" na biblioteca");
  document.getElementById("loadMoreRow").classList.toggle("hidden",filtered.length<=gridLimit);
  const playing=Object.keys(state.playingClips);
  document.getElementById("playStatusDot").classList.toggle("active",playing.length>0);
  updateNowPlayingBar(state.activePlayback);
  grid.classList.toggle("density-compact", state.gridDensity === "compact");
  grid.classList.toggle("density-large", state.gridDensity === "large");
  grid.replaceChildren();
  if(!filtered.length){
    const empty=document.createElement("div");empty.className="empty-state";
    empty.innerHTML=icon(hasFilter?"search":"music")+'<h3>'+(hasFilter?"Nenhum áudio encontrado":"Seu próximo momento começa aqui")+'</h3><p>'+(hasFilter?"Tente outra palavra, categoria ou pessoa. A busca também encontra trechos da transcrição.":"Importe áudios, adicione uma vinheta ou use Clipar call para guardar uma fala recente.")+'</p>';
    const action=document.createElement("button");action.className="btn btn-secondary";
    action.textContent=hasFilter?"Limpar filtros":"Importar áudios";
    action.onclick=hasFilter?resetFilters:()=>document.getElementById("btnImportFiles").click();
    action.disabled=!hasFilter&&Boolean(window.soundboardPreview);
    empty.append(action);grid.append(empty);restoreFocus(focus);return;
  }
  filtered.slice(0,gridLimit).forEach(clip=>{
    const ids=clip.speaker_ids?.length?clip.speaker_ids:clip.speaker_id?[clip.speaker_id]:[];
    const voices=ids.map(id=>state.speakers[id]?.name).filter(Boolean).join(", ")||"Sem pessoa";
    const effDur=Math.max(.1,(clip.trim_end&&clip.trim_end>clip.trim_start?clip.trim_end:clip.duration||0)-(clip.trim_start||0)).toFixed(1);
    const isPlaying=Boolean(state.playingClips[clip.id]);
    const card=document.createElement("article");
    card.className="sound-card"+(isPlaying?" playing":"");
    card.style.setProperty("--card-color",safeColor(clip.color));
    card.innerHTML=
      '<div class="card-top"><span class="card-category"><span class="card-color-dot"></span><span class="od-truncate">'+escapeHtml(clip.category||"Áudio")+'</span></span>'+
      '<button class="card-icon-btn btn-fav '+(clip.favorite?"fav-active":"")+'" aria-label="'+(clip.favorite?"Remover dos favoritos":"Favoritar")+'" aria-pressed="'+Boolean(clip.favorite)+'">'+icon("star")+'</button></div>'+
      '<button class="card-play" aria-label="Reproduzir '+escapeHtml(clip.title)+' na call">'+
      '<span class="card-title-row"><span class="card-emoji" aria-hidden="true">'+escapeHtml(clip.emoji||"🔊")+'</span><span class="card-title od-clamp-2 od-lines-2">'+escapeHtml(clip.title)+'</span><span class="play-glyph">'+icon("play")+'</span></span>'+
      '<span class="card-transcript od-truncate">'+escapeHtml(clip.transcript||clip.acoustic_mood||"Áudio local")+'</span>'+
      '<span class="card-footer"><span class="spk-badge od-clamp-2">'+escapeHtml(voices)+'</span><span class="card-duration">'+effDur+'s</span></span></button>'+
      '<div class="card-bottom"><span class="playing-label"><span class="eq-bars'+(state.activePlayback?.clip_id===clip.id&&state.activePlayback?.paused?" paused":"")+'" aria-hidden="true"><span></span><span></span><span></span><span></span></span>'+(state.activePlayback?.clip_id===clip.id&&state.activePlayback?.paused?"Pausado":"Tocando")+'</span><span class="card-time">'+(clip.hotkey?'<kbd class="hotkey-badge">'+escapeHtml(clip.hotkey)+'</kbd>':isClipNew(clip)?'<span class="new-badge">Novo</span>':'<span class="time-badge">'+escapeHtml(formatRelativeTime(clip.created_at))+'</span>')+'</span>'+
      '<div class="card-actions"><button class="card-icon-btn btn-preview" aria-label="Ouvir '+escapeHtml(clip.title)+' somente no fone" title="Ouvir só no fone">'+icon("headphones")+'<span class="preview-label">Fone</span></button><button class="card-icon-btn btn-edit" aria-label="Editar '+escapeHtml(clip.title)+'" title="Editar áudio e ver transcrição completa">'+icon("edit")+'</button></div></div><div class="play-progress" aria-hidden="true"></div>';
    const play=card.querySelector(".card-play");
    play.dataset.focusKey="play:"+clip.id;
    play.onclick=()=>api().play_clip(clip.id,false);
    card.querySelector(".btn-preview").onclick=()=>api().play_clip(clip.id,true);
    const edit=card.querySelector(".btn-edit");
    edit.dataset.focusKey="edit:"+clip.id;edit.onclick=()=>openEditModal(clip);
    const fav=card.querySelector(".btn-fav");
    fav.dataset.focusKey="favorite:"+clip.id;
    fav.onclick=async()=>{
      const res=await api().toggle_favorite(clip.id);
      if(res?.ok){const idx=state.clips.findIndex(x=>x.id===clip.id);if(idx>=0)state.clips[idx]=res.clip;renderSidebar();renderGrid();restoreFocus(fav.dataset.focusKey);}
    };
    card.oncontextmenu=e=>{e.preventDefault();openEditModal(clip);};
    const prog=card.querySelector(".play-progress");prog.id="prog_"+clip.id;
    if(isPlaying){
      const playback=state.playingClips[clip.id];
      const elapsed=Math.max(0,(Date.now()-playback.startedAt)/1000);
      const duration=Number(playback.duration)||Number(effDur);
      prog.style.width=Math.min(100,elapsed/duration*100)+"%";
      requestAnimationFrame(()=>{
        prog.style.transition="width "+Math.max(0,duration-elapsed)+"s linear";
        requestAnimationFrame(()=>prog.style.width="100%");
      });
    }
    grid.append(card);
  });
  restoreFocus(focus);
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
    api().get_clip_waveform(clip.id, 160).then((res) => {
      if (res && res.ok && document.getElementById("editClipId").value === clip.id) {
        state.waveformPeaks = res.peaks || [];
        if (res.duration > 0) state.waveformDuration = res.duration;
        drawWaveformCanvas();
      }
    }).catch(() => {});
  }

  openModal("editModal","editTitle");
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

  // Background active zone highlight
  ctx.fillStyle = "rgba(59, 130, 246, 0.10)";
  ctx.fillRect(startX, 0, endX - startX, h);

  const peaks = state.waveformPeaks && state.waveformPeaks.length
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
    const bh = Math.max(3, amp * (h - 14));
    ctx.fillStyle = inTrim ? "#8fb5ff" : "#343c4a";
    ctx.beginPath();
    ctx.roundRect(x, midY - bh / 2, barW, bh, 1.5);
    ctx.fill();
  }

  // Dimmed overlays outside [startX, endX]
  ctx.fillStyle = "rgba(10, 12, 16, 0.58)";
  if (startX > 0) ctx.fillRect(0, 0, startX, h);
  if (endX < w) ctx.fillRect(endX, 0, w - endX, h);

  // Trim handles
  ctx.fillStyle = "#8bdfb7";
  ctx.fillRect(startX, 0, 2.5, h);
  ctx.fillStyle = "#f0cf8b";
  ctx.fillRect(Math.max(0, endX - 2.5), 0, 2.5, h);
}

function updateNowPlayingBar(playback) {
  const playingIds = Object.keys(state.playingClips);
  const dot = document.getElementById("playStatusDot");
  const eq = document.getElementById("npEqBars");
  const statusEl = document.getElementById("playStatus");
  const routeBadge = document.getElementById("npRouteBadge");
  const controls = document.getElementById("nowPlayingControls");
  if (!statusEl || !controls) return;

  if (window.soundboardPreview) {
    statusEl.textContent = "Prévia · áudio disponível no aplicativo local";
    controls.classList.add("hidden");
    routeBadge?.classList.add("hidden");
    eq?.classList.add("hidden");
    dot?.classList.remove("active");
    return;
  }

  const active = playback && playback.playing ? playback : null;
  if (!active && playingIds.length === 0) {
    statusEl.textContent = "Pronto para reproduzir";
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
    routeBadge.textContent = monitorOnly ? "SÓ FONE" : "CALL + FONE";
  }
  controls.classList.remove("hidden");

  const pauseIcon = document.getElementById("npPauseIcon");
  if (pauseIcon) {
    pauseIcon.innerHTML = paused
      ? '<path d="m8 5 11 7-11 7Z"/>'
      : '<rect x="6" y="5" width="4" height="14" rx="1"/><rect x="14" y="5" width="4" height="14" rx="1"/>';
  }
  const loopBtn = document.getElementById("btnNpLoop");
  if (loopBtn) {
    loopBtn.classList.toggle("active", loop);
    loopBtn.setAttribute("aria-pressed", String(loop));
  }

  const dur = Math.max(0.1, Number(active?.duration || state.playingClips[clipId]?.duration || clip?.duration || 1));
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
  openModal("speakerModal","renameSpeakerName");
}

function bindEvents() {
  document.getElementById("btnRouting").onclick = () => {
    const panel=document.getElementById("routingPanel");
    panel.hidden=!panel.hidden;
    document.getElementById("btnRouting").setAttribute("aria-expanded",String(!panel.hidden));
  };
  document.getElementById("btnResetFilters").onclick=resetFilters;
  document.getElementById("btnLoadMore").onclick=()=>{gridLimit+=48;renderGrid();};
  document.querySelectorAll(".import-option").forEach(button=>button.addEventListener("click",()=>document.getElementById("importMenu").open=false));
  document.addEventListener("click",e=>{if(!e.target.closest("#importMenu"))document.getElementById("importMenu").open=false;});
  document.addEventListener("keydown",e=>{
    const modal=[...document.querySelectorAll(".modal-backdrop")].find(el=>!el.classList.contains("hidden"));
    if(e.key==="Escape"){
      document.getElementById("importMenu").open=false;
      if(modal){e.preventDefault();closeModal(modal.id);}
    }
    if(modal && e.key==="Tab"){
      const focusable=[...modal.querySelectorAll('button:not(:disabled),input:not(:disabled):not([type=hidden]),select:not(:disabled),textarea:not(:disabled),summary')].filter(el=>el.getClientRects().length);
      const first=focusable[0], last=focusable.at(-1);
      if(e.shiftKey&&document.activeElement===first){e.preventDefault();last?.focus();}
      else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first?.focus();}
    }
  });
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

  // Interactive Now Playing Deck controls
  const btnNpRestart = document.getElementById("btnNpRestart");
  if (btnNpRestart) {
    btnNpRestart.onclick = async () => {
      const res = await api().restart_playback();
      if (res && res.status) {
        state.activePlayback = res.status;
        updateNowPlayingBar(state.activePlayback);
      }
    };
  }
  const btnNpPause = document.getElementById("btnNpPause");
  if (btnNpPause) {
    btnNpPause.onclick = async () => {
      const res = await api().pause_resume_playback();
      if (res && res.status) {
        state.activePlayback = res.status;
        updateNowPlayingBar(state.activePlayback);
        renderGrid();
      }
    };
  }
  const btnNpLoop = document.getElementById("btnNpLoop");
  if (btnNpLoop) {
    btnNpLoop.onclick = async () => {
      const nextLoop = !Boolean(state.activePlayback?.loop);
      const res = await api().set_playback_loop(nextLoop);
      if (res && res.status) {
        state.activePlayback = res.status;
        updateNowPlayingBar(state.activePlayback);
      }
    };
  }
  const btnNpStop = document.getElementById("btnNpStop");
  if (btnNpStop) {
    btnNpStop.onclick = () => api().stop_all();
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
      if (res && res.status) {
        state.activePlayback = res.status;
        updateNowPlayingBar(state.activePlayback);
      }
    };
  }
  const npLiveVolume = document.getElementById("npLiveVolume");
  if (npLiveVolume) {
    npLiveVolume.oninput = (e) => {
      const v = Number(e.target.value) || 1;
      const lbl = document.getElementById("npLiveVolVal");
      if (lbl) lbl.textContent = `${Math.round(v * 100)}%`;
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
    closeModal("editModal");

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
    const endWfDrag = () => { wfDragTarget = null; };
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
      closeModal("editModal");
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
      closeModal("editModal");
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
    if (!window.confirm("Remover este botão? O arquivo de áudio será mantido.")) return;
    const res = await api().delete_clip(id, false);
    if (res && res.ok) {
      state.clips = state.clips.filter((c) => c.id !== id);
      closeModal("editModal");
      renderSidebar();
      renderGrid();
      showToast("🗑️ Botão removido do Soundboard.");
    }
  };

  // Speaker rename & delete modal
  document.getElementById("btnCloseSpeakerModal").onclick = () =>
    closeModal("speakerModal");

  document.getElementById("btnDeleteSpeakerModal").onclick = async () => {
    const spkId = document.getElementById("renameSpeakerId").value;
    if (!window.confirm("Excluir esta pessoa? Os áudios serão mantidos.")) return;
    const res = await api().delete_speaker(spkId);
    if (res && res.ok) {
      state.speakers = res.speakers || {};
      state.clips = res.clips || state.clips;
      if (state.activeSpeaker === spkId) state.activeSpeaker = "Todos";
      closeModal("speakerModal");
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
      closeModal("speakerModal");
      renderSidebar();
      renderGrid();
      showToast(`✅ Interlocutor atualizado para "${name}"!`);
    }
  };
}

// Callback receiver from Python backend
window.onBackendEvent = (event, payload) => {
  if (event === "clip_started") {
    state.playingClips[payload.clip_id] = {startedAt:Date.now(),duration:payload.duration||1.5};
    state.activePlayback = { ...payload, playing: true };
    renderGrid();
  } else if (event === "playback_tick") {
    if (payload && payload.clip_id) {
      state.activePlayback = payload;
      updateNowPlayingBar(payload);
      const prog = document.getElementById("prog_" + payload.clip_id);
      if (prog && payload.duration > 0) {
        prog.style.transition = "width 90ms linear";
        prog.style.width = `${Math.min(100, (payload.position / payload.duration) * 100)}%`;
      }
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
window.addEventListener("pywebviewready", initApp);
window.addEventListener("unhandledrejection", () => showToast("A operação não foi concluída. Verifique o aplicativo local e tente novamente."));
setTimeout(()=>{
  if(!initialized){
    const notice=document.getElementById("connectionNotice");
    notice.textContent="Aguardando o aplicativo local. Para reproduzir e gerenciar áudios, abra iniciar_soundboard.bat.";
    notice.classList.remove("hidden");
  }
},2500);
