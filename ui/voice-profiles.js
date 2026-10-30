(function(root){
  'use strict';
  const identityLabel=identity=>({identified:'Identificada',ambiguous:'Duvidosa',unknown:'Desconhecida'}[identity?.status]||'Sem decisão');
  const reasonLabel=value=>String(value||'').split(', ').map(reason=>({embedding_unavailable:'Não foi possível extrair a voz. Verifique o modelo local e cadastre novamente.',insufficient_speech:'Selecione pelo menos meio segundo de fala útil.',silence:'Há muito silêncio. Selecione a fala.',clipping:'O trecho está saturado. Use outra gravação.',mixed:'A seleção contém vozes diferentes. Selecione apenas uma pessoa.',uncertain_mix:'Não foi possível verificar a mistura de vozes. Selecione uma fala isolada.'}[reason]||reason)).join(' ');
  function actionLabel(action){
    if(!action)return '';
    if(action.reference_learned)return 'Pessoa corrigida; exemplo adicionado.';
    if(action.state==='pending')return 'Pessoa corrigida; avaliando exemplo em segundo plano…';
    if(action.state==='disabled')return 'Pessoa corrigida; aprendizado pelas correções desativado.';
    if(action.state==='undone')return 'Correção e influência do exemplo desfeitas.';
    if(action.state==='cancelled')return 'Pessoa corrigida; aprendizado cancelado.';
    return 'Pessoa corrigida; este trecho não foi usado como referência'+(action.error?': '+reasonLabel(action.error):'.');
  }
  function create(doc,bridge,callbacks={}){
    const el=id=>doc.getElementById(id);
    let actionId=null,clipId=null,person=null,epoch=0,timer=null,playId=null;
    const text=(id,value)=>{if(el(id))el(id).textContent=value;};
    const node=(tag,value)=>{const result=doc.createElement(tag);result.textContent=value;return result;};
    function button(label,run){const b=node('button',label);b.type='button';b.className='btn btn-xs btn-secondary';b.onclick=async()=>{b.disabled=true;try{await run();}catch(error){text('voiceProfileStatus',String(error));}finally{b.disabled=false;}};return b;}
    let previewEpoch=0;
    async function stop(){previewEpoch++;if(playId){const id=playId;playId=null;await bridge().stop_editor_preview(id);}}
    async function refreshAction(){
      if(!actionId)return;
      const opened=epoch,id=actionId;
      const result=await bridge().get_voice_profiles();
      if(opened!==epoch||id!==actionId)return;
      const action=result.actions[id];
      if(!action||action.clip_id!==clipId){
        actionId=null;
        el('btnUndoVoiceCorrection').disabled=true;
        el('btnCancelVoiceLearning').disabled=true;
        return;
      }
      text('voiceLearningStatus',actionLabel(action));
      el('btnCancelVoiceLearning').disabled=action?.state!=='pending';
      el('btnUndoVoiceCorrection').disabled=!action||action.state==='undone';
      if(action?.state==='pending')timer=setTimeout(()=>refreshAction().catch(()=>{}),350);
    }
    function observe(result){
      clearTimeout(timer);
      if(result?.action_id){actionId=result.action_id;refreshAction().catch(error=>text('voiceLearningStatus',String(error)));}
      else if(result?.error)text('voiceLearningStatus',result.error);
    }
    async function openEditor(clip){
      epoch++;clearTimeout(timer);clipId=clip.id;actionId=clip.voice_action_id||null;
      el('btnUndoVoiceCorrection').disabled=true;
      el('btnCancelVoiceLearning').disabled=true;
      const opened=epoch,result=await bridge().get_voice_profiles();
      if(opened!==epoch)return;
      el('learnVoiceCorrections').checked=!!result.settings.learn_voice_corrections;
      el('learnVoiceCorrections').onchange=async event=>{
        try{await bridge().update_settings({learn_voice_corrections:event.target.checked});}
        catch(error){event.target.checked=!event.target.checked;text('voiceLearningStatus',String(error));}
      };
      el('btnUndoVoiceCorrection').disabled=true;
      el('btnCancelVoiceLearning').disabled=true;
      el('btnUndoVoiceCorrection').onclick=async()=>{if(!actionId)return;const res=await bridge().undo_voice_correction(actionId,clipId);if(res.ok){callbacks.changed?.(res.clip);await refreshAction();}else text('voiceLearningStatus',res.error);};
      el('btnCancelVoiceLearning').onclick=async()=>{if(!actionId)return;await bridge().cancel_voice_learning(actionId,clipId);await refreshAction();};
      text('voiceLearningStatus','Referências ficam preservadas no perfil mesmo se o clipe for removido.');
      const choices=el('voiceIdentityChoices');choices.replaceChildren();
      for(const segment of clip.segments||[]){
        if(!segment.identity)continue;
        const row=node('div',identityLabel(segment.identity)+' · '+((segment.start_sample||0)/48000).toFixed(2)+' s');
        for(const candidate of segment.identity.candidates||[]){
          row.append(button('Selecionar '+(result.speakers[candidate.speaker_id]?.name||candidate.speaker_id),()=>{
            el('editSpeaker').value=candidate.speaker_id;
            callbacks.selectInterval?.([segment.start_sample,segment.end_sample]);
            text('voiceLearningStatus','Ouça a seleção e salve para confirmar esta pessoa.');
          }));
        }
        choices.append(row);
      }
      if(actionId)await refreshAction();
    }
    async function openProfile(speakerId){
      await stop();person=speakerId;
      const result=await bridge().get_voice_profiles(speakerId);
      if(person!==speakerId)return;
      const host=el('voiceSamples');host.replaceChildren();
      const legacy=result.legacy[speakerId];
      el('btnImportLegacyVoices').onclick=async()=>{const migrated=await bridge().import_legacy_voice_profiles();if(migrated.ok)await openProfile(person);else text('voiceLegacyStatus',migrated.error);};
      text('voiceLegacyStatus',legacy?(legacy.disabled?'Referência legada desativada.':'Referência legada preservada com backup. Versão do extrator desconhecida: não participa do reconhecimento; cadastre exemplos confirmados.'):'');
      text('voiceProfileStatus','Perfil v'+(result.profiles[speakerId]?.profile_version||0)+' · '+result.samples.length+' exemplo(s). Critérios iniciais; ajuste conforme suas gravações.');
      for(const sample of result.samples){
        const row=node('div','');row.className='voice-sample';row.dataset.sampleId=sample.sample_id;
        const label=(sample.used_in_profile?'Usada no perfil':'Pendente / fora do perfil')+' · '+sample.spans.map(([a,b])=>(a/48000).toFixed(2)+'–'+(b/48000).toFixed(2)+' s').join(', ');
        row.append(node('div',label),node('small',reasonLabel(sample.quality.reasons.join(', '))||'Elegível para compor este perfil.'));
        row.append(button('Ouvir no monitor',async()=>{await stop();const requested=previewEpoch,res=await bridge().preview_voice_sample(sample.sample_id);if(requested!==previewEpoch){if(res.ok)await bridge().stop_editor_preview(res.play_id);return;}if(res.ok)playId=res.play_id;else text('voiceProfileStatus',res.error);}),button('Parar prévia',stop));
        const select=doc.createElement('select');select.setAttribute('aria-label','Pessoa do exemplo');
        for(const [id,speaker] of Object.entries(result.speakers)){const opt=node('option',speaker.name||id);opt.value=id;select.append(opt);}select.value=sample.speaker_id;
        row.append(select,button('Reatribuir',async()=>{const res=await bridge().reassign_voice_sample(sample.sample_id,select.value);if(!res.ok)throw Error(res.error);await openProfile(person);}),button('Remover exemplo',async()=>{await stop();const res=await bridge().remove_voice_sample(sample.sample_id);if(!res.ok)throw Error(res.error||'Não foi possível remover');await openProfile(person);}));
        host.append(row);
      }
      el('voiceMatchThreshold').value=result.settings.voice_match_threshold;
      el('voiceMatchMargin').value=result.settings.voice_match_margin;
      el('btnSaveVoiceMatching').onclick=async()=>{try{await bridge().update_settings({voice_match_threshold:Number(el('voiceMatchThreshold').value),voice_match_margin:Number(el('voiceMatchMargin').value)});text('voiceProfileStatus','Critérios salvos; correspondências anteriores precisam de revisão.');}catch(error){text('voiceProfileStatus',String(error));}};
      el('btnReviewVoiceLibrary').onclick=async()=>{
        const review=await bridge().request_voice_review(),proposals=review.proposals||[];
        const host=el('voiceReviewProposals');host.replaceChildren();
        for(const proposal of proposals){const label=node('label','');const box=doc.createElement('input');box.type='checkbox';box.value=proposal.proposal_id;box.className='voice-review-choice';label.append(box,node('span',proposal.clip_id+': '+(result.speakers[proposal.previous_speaker_id]?.name||'Desconhecida')+' → '+(result.speakers[proposal.speaker_id]?.name||'Desconhecida')+' · '+proposal.segments.map(s=>identityLabel(s.identity)).join(', ')));host.append(label);}
        text('voiceReviewStatus',proposals.length+' proposta(s). '+review.requires_speaker_analysis.length+' clipe(s) precisam de análise de pessoas antes da revisão. Atribuições manuais são preservadas.');
      };
      el('btnApplyVoiceReview').onclick=async()=>{const ids=Array.from(el('voiceReviewProposals').querySelectorAll('input:checked')).map(box=>box.value);const res=await bridge().apply_voice_review(ids);text('voiceReviewStatus',res.applied.length+' aplicada(s); '+res.obsolete.length+' obsoleta(s).');callbacks.changed?.();};
    }
    function close(){epoch++;person=null;clipId=null;actionId=null;clearTimeout(timer);stop().catch(()=>{});}
    return {openEditor,openProfile,observe,close,stop};
  }
  const exported={create,identityLabel,actionLabel};
  if(typeof module!=='undefined'&&module.exports)module.exports=exported;else root.VoiceProfiles=exported;
})(typeof window!=='undefined'?window:globalThis);
