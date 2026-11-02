/* Cada análise pertence ao seu campo, clipe e pedido. */
(function (root) {
  'use strict';
  function create(document, bridge, callbacks = {}) {
    const stages = {
      transcription: ['analysisTranscribe', 'analysisTranscriptionFeedback'],
      metadata: ['analysisMetadata', 'analysisMetadataFeedback'],
      speakers: ['analysisSpeakers', 'analysisSpeakersFeedback'],
    };
    const el = id => document.getElementById(id);
    const control = (stage, suffix) => el(stage + 'Analysis' + suffix);
    const schedule = callbacks.schedule || setTimeout, unschedule = callbacks.unschedule || clearTimeout;
    const labels = {queued: 'Na fila', running: 'Analisando…', ready: 'Concluído', no_speech: 'Nenhuma fala detectada', failed: 'Falhou', cancelled: 'Cancelado'};
    const message = error => error?.message || String(error || 'Não foi possível concluir a análise.');
    let clipId = null, epoch = 0, states = {};
    const fresh = () => ({version: 0, job: null, notice: '', phase: '', timer: null, pending: null, polling: null, notified: null});
    const active = state => ['queued', 'running'].includes(state.job?.status);
    function clearTimer(state) {if (state.timer !== null) unschedule(state.timer); state.timer = null;}
    function own(stage, state, version, opened) {return epoch === opened && states[stage] === state && state.version === version;}
    function invalidate(state) {clearTimer(state); state.version++; state.polling = null;}
    function proposalText(stage, result) {
      if (stage === 'transcription') return result.text || 'Nenhum texto reconhecido.';
      const fields = result.fields || {};
      if (stage === 'metadata') return [['Título', fields.title], ['Categoria', fields.category], ['Tags', fields.tags]]
        .filter(([, value]) => value != null && value !== '').map(([label, value]) => `${label}: ${Array.isArray(value) ? value.join(', ') : value}`).join('\n') || 'Nenhuma sugestão disponível.';
      const voices = fields.speaker_names || result.speaker_names || fields.speaker_ids || (fields.speaker_id ? [fields.speaker_id] : []);
      const names = Array.isArray(voices) ? voices.map(voice => typeof voice === 'string' ? voice : voice.name || voice.speaker_name || voice.speaker_id).filter(Boolean) : Object.values(voices);
      const segments = Array.isArray(fields.segments) ? fields.segments.length : 0;
      return [names.length ? `Vozes: ${names.join(', ')}` : 'Nenhuma voz identificada.', segments ? `${segments} trechos de fala identificados.` : ''].filter(Boolean).join('\n');
    }
    function render(stage) {
      const state = states[stage], job = state.job, result = job?.result || {};
      const busy = !!state.phase || active(state), proposal = job?.application?.state === 'proposal';
      const host = el(stages[stage][1]);
      host.hidden = !job && !state.notice && !busy;
      host.setAttribute('aria-busy', String(busy));
      el(stages[stage][0]).disabled = !clipId || busy;
      if (stage === 'transcription') {
        const selectedAction = el('editorRetranscribe');
        if (selectedAction) selectedAction.disabled = !clipId || busy;
      }
      let text = labels[job?.status] || '';
      if (job?.error) text += ': ' + message(job.error);
      if (job?.application?.state === 'obsolete') text = 'Resultado obsoleto: a edição ou o pedido mudou. Solicite outra análise.';
      if (job?.application?.state === 'applied') text = 'Resultado aplicado.';
      control(stage, 'Status').textContent = state.notice || text;
      const progress = control(stage, 'Progress');
      // O serviço não informa avanço mensurável; o progress permanece indeterminado.
      progress.removeAttribute('value'); progress.hidden = !busy;
      control(stage, 'Cancel').hidden = !active(state);
      control(stage, 'Cancel').disabled = !!state.phase;
      control(stage, 'Accept').hidden = !proposal;
      control(stage, 'Accept').disabled = busy;
      control(stage, 'Proposal').hidden = !proposal;
      control(stage, 'Proposal').textContent = proposal ? proposalText(stage, result) : '';
      const runtime = [result.model, result.device, result.compute_type, result.metadata_source, result.fallback_reason].filter(Boolean).join(' · ');
      control(stage, 'Runtime').hidden = !runtime;
      control(stage, 'Runtime').textContent = runtime ? 'Detalhes da análise' : '';
      control(stage, 'Runtime').title = runtime;
    }
    async function notifyApplied(stage, state, target, valid) {
      const id = state.job?.analysis_id;
      if (!valid() || !callbacks.applied || state.notified === id) return;
      state.notified = id;
      const previousPhase = state.phase;
      state.phase = 'applying'; state.notice = 'Atualizando resultado…'; render(stage);
      try {await callbacks.applied({clipId: target, analysisId: id, stage});}
      catch (error) {if (valid()) state.notified = null; throw error;}
      finally {if (valid()) {state.phase = previousPhase; state.notice = '';}}
    }
    function later(stage, state, delay = 500) {
      clearTimer(state);
      state.timer = schedule(() => {state.timer = null; return poll(stage);}, delay);
    }
    function poll(stage) {
      if (stage === undefined) return Promise.all(Object.keys(stages).map(poll));
      const state = states[stage];
      if (!state || !clipId || !state.job?.analysis_id || state.phase) return Promise.resolve();
      if (state.polling) return state.polling;
      clearTimer(state);
      const opened = epoch, version = state.version, target = clipId, id = state.job.analysis_id;
      const valid = () => own(stage, state, version, opened) && state.job?.analysis_id === id;
      const work = (async () => {
        try {
          const job = await bridge().get_analysis_job(id);
          if (!valid()) return;
          if (!job?.ok) throw Error(message(job?.error));
          state.job = job; state.notice = '';
          if (job.application?.state === 'applied') await notifyApplied(stage, state, target, valid);
          if (valid()) render(stage);
        } catch (error) {if (valid()) {state.notice = message(error); render(stage);}}
        finally {
          if (valid() && (active(state) || (state.job.application?.state === 'applied' && callbacks.applied && state.notified !== id))) later(stage, state);
        }
      })();
      state.polling = work;
      work.finally(() => {if (state.polling === work) state.polling = null;});
      return work;
    }
    async function preserve() {
      if (!callbacks.persist) return;
      const saved = await callbacks.persist();
      if (!saved?.ok) throw Error(message(saved?.error || 'Falha ao preservar a edição. O formulário foi mantido.'));
    }
    function operation(stage, phase, action) {
      const state = states[stage];
      if (!state || !clipId) return Promise.resolve();
      if (state.pending) return state.pending;
      if (state.phase) return Promise.resolve();
      invalidate(state);
      const opened = epoch, version = state.version, target = clipId;
      const valid = () => own(stage, state, version, opened);
      state.phase = phase; state.notice = phase === 'cancel' ? 'Cancelando…' : phase === 'accept' ? 'Aplicando resultado…' : 'Preparando análise…'; render(stage);
      const work = (async () => {
        try {await action(state, target, valid);}
        catch (error) {if (valid()) state.notice = message(error);}
        finally {
          if (valid()) {
            state.phase = ''; render(stage);
            if (active(state) || state.needsPoll || (state.job?.application?.state === 'applied' && callbacks.applied && state.notified !== state.job.analysis_id)) later(stage, state, 0);
            state.needsPoll = false;
          }
        }
      })();
      state.pending = work;
      work.finally(() => {if (state.pending === work) state.pending = null;});
      return work;
    }
    function start(stage = 'transcription', {selection = false} = {}) {
      const state = states[stage];
      if (!state || !clipId) return Promise.resolve();
      if (state.pending) return state.pending;
      if (state.phase || active(state)) return Promise.resolve();
      // Captura a seleção canônica antes da persistência assíncrona.
      const selected = selection && stage === 'transcription' ? callbacks.selection?.() : null;
      const interval = selected ? [...selected] : null;
      return operation(stage, 'start', async (current, target, valid) => {
        if (selection && stage === 'transcription' && (!interval || interval.length !== 2 || !interval.every(Number.isInteger) || interval[0] < 0 || interval[1] <= interval[0])) throw Error('Selecione um trecho de áudio válido.');
        await preserve(); if (!valid()) return;
        const result = await bridge().request_clip_analysis(target, stage, interval, 0, true);
        if (!valid()) return;
        if (!result?.ok) throw Error(message(result?.error));
        current.job = result; current.notice = ''; current.notified = null; current.needsPoll = true;
      });
    }
    function accept(stage) {
      const state = states[stage], consent = state?.job?.application;
      if (!state || consent?.state !== 'proposal') return Promise.resolve();
      const id = state.job.analysis_id, token = consent.accept_token;
      return operation(stage, 'accept', async (current, target, valid) => {
        await preserve(); if (!valid()) return;
        const result = await bridge().accept_analysis_result(target, id, token);
        if (!valid()) return;
        if (!result?.ok) {
          if (result?.state === 'obsolete') current.job = {...current.job, application: {state: 'obsolete'}};
          throw Error(message(result?.error || 'A edição mudou; solicite outra análise antes de substituir.'));
        }
        current.job = {...current.job, application: {state: 'applied'}}; current.notice = '';
        await notifyApplied(stage, current, target, valid);
      });
    }
    async function cancel(stage) {
      const state = states[stage];
      if (!state || !active(state)) return;
      const id = state.job.analysis_id;
      await operation(stage, 'cancel', async (current, target, valid) => {
        const result = await bridge().cancel_analysis_job(id);
        if (!valid()) return;
        if (!result?.ok) throw Error(message(result?.error));
        current.notice = '';
      });
      if (states[stage] === state && state.job?.analysis_id === id) await poll(stage);
    }
    async function open(id) {
      close(); clipId = id;
      const opened = epoch;
      for (const stage of Object.keys(stages)) {
        el(stages[stage][0]).onclick = () => start(stage);
        control(stage, 'Cancel').onclick = () => cancel(stage);
        control(stage, 'Accept').onclick = () => accept(stage);
        render(stage);
      }
      try {
        const response = await bridge().list_analysis_jobs(id);
        if (opened !== epoch) return;
        if (!response?.ok) throw Error(message(response?.error));
        const latest = {};
        for (const job of response.jobs || []) if (Object.hasOwn(stages, job.stage)) latest[job.stage] = job;
        await Promise.all(Object.keys(latest).map(stage => {
          if (states[stage].version !== 0) return;
          states[stage].job = latest[stage]; render(stage); return poll(stage);
        }));
      } catch (error) {
        if (opened === epoch) for (const stage of Object.keys(stages)) if (states[stage].version === 0) {states[stage].notice = message(error); render(stage);}
      }
    }
    function close() {
      epoch++; Object.values(states).forEach(clearTimer); clipId = null;
      states = Object.fromEntries(Object.keys(stages).map(stage => [stage, fresh()]));
    }
    close();
    return {open, close, start, poll};
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = {create};
  else root.AnalysisPanel = {create};
})(typeof window !== 'undefined' ? window : globalThis);
