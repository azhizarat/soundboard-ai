/* Preferências explícitas; texto humano nunca vira regra de substituição. */
(function () {
  'use strict';
  function dictionary(text) { return [...new Set(text.split(/\r?\n/).map(x => x.trim()).filter(Boolean))]; }
  if (typeof module !== 'undefined') module.exports = { dictionary };
  if (typeof document === 'undefined') return;
  const el = id => document.getElementById(id);
  const api = () => window.pywebview?.api;
  function render(data, hydrate = true) {
    if (!data.ok) throw new Error(data.error || 'Falha na qualidade local');
    if (hydrate) {
      el('qualityProfile').value = data.policy.profile;
      el('qualityPaused').checked = data.policy.paused;
      el('qualityDictionary').value = data.dictionary.join('\n');
      el('qualityConfirm').checked = false;
    }
    el('qualityStatus').textContent = `${data.policy.paused ? 'Pausado' : data.policy.profile === 'playing' ? 'Jogando' : 'Revisando'} · 1 inferência por vez · Cache: ${data.cache.inference.hits} acertos, ${data.cache.inference.items} itens. Origem: termos confirmados pelo usuário / ASR local; sem correção automática.`;
    const jobs = el('qualityJobs'); jobs.replaceChildren();
    for (const job of data.jobs.filter(j => ['queued', 'running'].includes(j.status))) {
      const line = document.createElement('div');
      const stages = {transcription:'Transcrição', speakers:'Pessoas', acoustics:'Descritores acústicos', metadata:'Sugestões editoriais', voice_learning:'Referência vocal'};
      line.textContent = `${stages[job.stage] || 'Análise'} · ${job.status === 'queued' ? 'Aguardando' : 'Em andamento'} `;
      const cancel = document.createElement('button'); cancel.textContent = 'Cancelar'; cancel.className = 'btn btn-secondary btn-xs';
      cancel.dataset.action = 'cancel'; cancel.dataset.jobId = job.analysis_id;
      cancel.onclick = async () => { await api().cancel_analysis_job(job.analysis_id); await refresh(false); };
      if (job.status === 'queued' && job.priority > 0) {
        const promote = document.createElement('button'); promote.textContent = 'Priorizar'; promote.className = 'btn btn-secondary btn-xs';
        promote.dataset.action = 'prioritize'; promote.dataset.jobId = job.analysis_id;
        promote.onclick = async () => { await api().set_analysis_priority(job.analysis_id, true); await refresh(false); };
        line.append(promote);
      }
      line.append(cancel); jobs.append(line);
    }
  }
  async function refresh(hydrate = true) {
    if (!api()) return;
    try { render(await api().get_local_quality(), hydrate); }
    catch (err) { el('qualityStatus').textContent = err.message; }
  }
  el('btnRefreshQuality').onclick = () => refresh();
  el('localQualityPanel').addEventListener('toggle', () => { if (el('localQualityPanel').open) refresh(); });
  el('btnSaveQuality').onclick = async () => {
    if (!api()) return;
    try {
      const terms = dictionary(el('qualityDictionary').value);
      const current = await api().get_local_quality();
      if (JSON.stringify(terms) !== JSON.stringify(current.dictionary)) {
        if (!el('qualityConfirm').checked) throw new Error('Confirme os termos pessoais antes de salvar.');
        const saved = await api().save_personal_dictionary(terms, true);
        if (!saved.ok) throw new Error(saved.error);
      }
      render(await api().set_resource_policy(el('qualityProfile').value, el('qualityPaused').checked));
    } catch (err) { el('qualityStatus').textContent = err.message; }
  };
})();
