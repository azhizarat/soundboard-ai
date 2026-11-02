/* Preferências de transcrição pertencem ao aplicativo, nunca a um clipe. */
(function(root){
  'use strict';
  const fields=[['asr_model','asrModel','base'],['asr_device','asrDevice','cuda'],
    ['asr_beam_size','asrBeam',3],['asr_hotwords','asrHotwords',''],['asr_initial_prompt','asrPrompt','']];
  function create(document,bridge,callbacks={}){
    const el=id=>document.getElementById(id);
    let initialized=false,pending=null;
    const values=()=>Object.fromEntries(fields.map(([key,id])=>[key,key==='asr_beam_size'?Number(el(id).value):el(id).value]));
    function initialize(config={}){
      if(initialized)return;
      initialized=true;
      for(const [key,id,fallback] of fields)el(id).value=config[key]??fallback;
      el('btnSaveAsr').onclick=save;
    }
    function save(){
      if(pending)return pending;
      const updates=values();el('btnSaveAsr').disabled=true;
      el('aiSettingsStatus').textContent='Salvando preferências globais…';
      pending=(async()=>{
        try{
          const result=await bridge().save_app_config(updates);
          if(!result?.ok)throw new Error(result?.error||'Não foi possível salvar as preferências.');
          callbacks.configChanged?.(result.config);
          const changed=JSON.stringify(values())!==JSON.stringify(updates);
          el('aiSettingsStatus').textContent=changed?'Preferências globais enviadas salvas; alterações posteriores continuam pendentes.':'Preferências globais salvas. Valem para as próximas análises; nenhum modelo foi baixado.';
          return result;
        }catch(error){el('aiSettingsStatus').textContent=error.message||String(error);return {ok:false,error:error.message||String(error)};}
      })().finally(()=>{pending=null;el('btnSaveAsr').disabled=false;});
      return pending;
    }
    return {initialize,save};
  }
  const exported={create};
  if(typeof module!=='undefined'&&module.exports)module.exports=exported;
  root.AISettings=exported;
})(typeof window==='undefined'?globalThis:window);
