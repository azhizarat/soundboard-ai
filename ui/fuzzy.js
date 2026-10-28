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
    let lastWasSpace = false;
    for (let i = 0; i < original.length; i++) {
      // Collapse whitespace runs to a single space (same as normalizeText),
      // mapped to the first index of the run.
      if (/\s/.test(original[i])) {
        if (!lastWasSpace) {
          out += " ";
          map.push(i);
          lastWasSpace = true;
        }
        continue;
      }
      lastWasSpace = false;
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
      // Keep trailing combining marks inside the highlighted range.
      while (j < original.length && /[\u0300-\u036f]/.test(original[j])) j++;
      html += `<mark>${escapeHtml(original.slice(i, j))}</mark>`;
      i = j;
    }
    return html;
  }

  const api = { normalizeText, score, scoreClip, highlight };
  global.SBFuzzy = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
