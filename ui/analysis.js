/* Trabalhos de análise independentes; a edição permanece utilizável. */
(function (root) {
  'use strict';
  function create(document, bridge, callbacks = {}) {
    const el = id => document.getElementById(id);
    const schedule = callbacks.schedule || setTimeout;
    const unschedule = callbacks.unschedule || clearTimeout;
    let clipId = null, jobId = null, timer = null, epoch = 0, proposal = null, lastJob = null;
    const notified = new Set();
    const labels = {queued: 'Na fila', running: 'Analisando', ready: 'Concluído', no_speech: 'Nenhuma fala detectada', failed: 'Falhou', cancelled: 'Cancelado'};
    const status = text => {el('analysisStatus').textContent = text;};
    async function notifyApplied(job, force = false) {
      if (!callbacks.applied) return false;
      const id = job?.analysis_id || jobId;
      if (!force && notified.has(id)) return true;
      notified.add(id);
      try {
        await callbacks.applied({clipId, analysisId: id, stage: job?.stage || job?.result?.stage || el('analysisStage').value});
      } catch (error) {notified.delete(id); throw error;}
      return true;
    }
    async function poll() {
      const opened = epoch;
      if (!jobId || !clipId) return;
      try {
        const job = await bridge().get_analysis_job(jobId);
        if (opened !== epoch) return;
        if (!job.ok) {status(job.error); return;}
        lastJob = job;
        const result = job.result || {};
        status(`${labels[job.status] || job.status}${job.error ? ': ' + (job.error.message || job.error) : ''}`);
        el('analysisRuntime').textContent = [result.model, result.model_version, result.device, result.compute_type,
          result.metadata_source, result.fallback_reason].filter(Boolean).join(' · ');
        el('btnCancelAnalysis').hidden = !['queued', 'running'].includes(job.status);
        if (job.application?.state === 'proposal') {
          proposal = job.application;
          el('analysisProposal').textContent = result.text ?? JSON.stringify(result.fields || {}, null, 2);
          el('btnAcceptAnalysis').hidden = false;
        } else {
          el('btnAcceptAnalysis').hidden = true;
          if (job.application?.state === 'obsolete') status('Resultado obsoleto: a edição ou o pedido mudou. Solicite outra análise.');
          el('btnLoadAnalysis').hidden = job.application?.state !== 'applied';
          if (job.application?.state === 'applied') {
            // Atualiza somente a projeção do editor; polling nunca grava campos humanos.
            await notifyApplied(job);
            if (opened !== epoch) return;
          }
        }
        if (['queued', 'running'].includes(job.status)) timer = schedule(poll, 500);
      } catch (error) { if (opened === epoch) status(error.message || String(error)); }
    }
    async function start() {
      if (!clipId) return;
      const opened = epoch, target = clipId;
      const saved = await callbacks.persist?.();
      if (opened !== epoch || saved?.ok === false) return;
      const stage = el('analysisStage').value || 'transcription';
      const selected = stage === 'transcription' && el('analysisSelection').checked;
      if (el('analysisCurrent')) el('analysisCurrent').textContent = callbacks.currentText?.() ?? el('editTranscript')?.value ?? '';
      const interval = selected ? [...(callbacks.selection?.() || [Math.round(Number(el('analysisStart').value) * 48000), Math.round(Number(el('analysisEnd').value) * 48000)])] : null;
      try {
        const result = await bridge().request_clip_analysis(target, stage, interval, Number(el('analysisContext').value || 0), true);
        if (opened !== epoch) return;
        if (!result.ok) {status(result.error); return;}
        jobId = result.analysis_id;
        proposal = null;
        el('analysisProposal').textContent = '';
        el('btnAcceptAnalysis').hidden = true;
        el('btnLoadAnalysis').hidden = true;
        status(labels[result.status] || result.status);
        unschedule(timer);
        timer = schedule(poll, 0);
      } catch (error) {if (opened === epoch) status(error.message || String(error));}
    }
    async function open(id) {
      close();
      clipId = id;
      const opened = epoch;
      el('btnStartAnalysis').onclick = start;
      for (const [id, stage] of [['analysisTranscribe', 'transcription'], ['analysisSpeakers', 'speakers'], ['analysisMetadata', 'metadata']]) {
        const button = el(id);
        if (button) button.onclick = () => {el('analysisStage').value = stage; return start();};
      }
      el('btnCancelAnalysis').onclick = async () => {await bridge().cancel_analysis_job(jobId); await poll();};
      el('btnLoadAnalysis').onclick = async () => {
        try {
          const saved = await callbacks.persist?.();
          if (opened !== epoch) return;
          if (callbacks.persist && !saved?.ok) {
            status(saved?.error || 'Falha ao preservar a edição. O formulário foi mantido.');
            return;
          }
          if (!await notifyApplied(lastJob, true)) await callbacks.refresh?.();
        } catch (error) {if (opened === epoch) status(error.message || String(error));}
      };
      el('btnAcceptAnalysis').onclick = async () => {
        const selectedJob = jobId, consent = proposal;
        const saved = await callbacks.persist?.();
        if (opened !== epoch || saved?.ok === false || !consent) return;
        const accepted = await bridge().accept_analysis_result(clipId, selectedJob, consent.accept_token);
        if (opened !== epoch) return;
        if (accepted.ok) {
          if (!await notifyApplied(lastJob)) await callbacks.refresh?.();
          if (opened === epoch) {proposal = null; el('btnAcceptAnalysis').hidden = true; status('Resultado aplicado ao editor.');}
        }
        else status(accepted.error || 'A edição mudou; solicite outra análise antes de substituir.');
      };
      el('btnAcceptAnalysis').hidden = true;
      el('btnLoadAnalysis').hidden = true;
      el('btnCancelAnalysis').hidden = true;
      el('analysisProposal').textContent = '';
      status('Selecione uma etapa. Edição manual disponível mesmo sem modelos.');
      try {
        const text = await bridge().get_clip_transcript(id);
        if (opened !== epoch) return;
        if (!text.ok) el('analysisAlignment').textContent = text.error;
        else if (text.transcript.alignment === 'unaligned') el('analysisAlignment').textContent = 'Texto não alinhado: sem seleção temporal por palavras.';
        else if (['partial', 'segments'].includes(text.transcript.alignment)) el('analysisAlignment').textContent = `Alinhamento parcial: ${text.transcript.words.length} palavras com tempos; há segmentos sem alinhamento por palavra. Preserve esses blocos inteiros ou edite o texto manualmente.`;
        else el('analysisAlignment').textContent = `${text.transcript.words.length} palavras com âncoras na fonte.`;
        const jobs = await bridge().list_analysis_jobs(id);
        if (opened !== epoch) return;
        const latest = jobs.jobs?.at(-1);
        if (latest) {jobId = latest.analysis_id; await poll();}
      } catch (error) {if (opened === epoch) status(error.message || String(error));}
    }
    function close() {epoch++; unschedule(timer); clipId = jobId = proposal = lastJob = null; notified.clear();}
    return {open, close, start, poll};
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = {create};
  else root.AnalysisPanel = {create};
})(typeof window !== 'undefined' ? window : globalThis);
