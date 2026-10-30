/* Variantes da revisão publicada: prévia isolada e respostas tardias descartadas. */
(function(root){
 'use strict';
 const uuid=()=>globalThis.crypto?.randomUUID?.()||'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g,char=>{
  const bytes=new Uint8Array(1);const random=globalThis.crypto?.getRandomValues?globalThis.crypto.getRandomValues(bytes)[0]:Math.floor(Math.random()*256);
  return (char==='x'?random&15:(random&3)|8).toString(16);
 });
 function create(dom,bridge,created=()=>{}){
  const $=id=>dom.getElementById(id);let clipId=null,revisionId=null,epoch=0,previewEpoch=0,playId=null,previewToken=null,saving=null;
  const message=text=>{$('variantMessage').textContent=text;};
  const check=result=>{if(!result?.ok)throw Error(result?.error||'Não foi possível criar a variante');return result;};
  const options=()=>({speed:Number($('variantSpeed').value),pitch_semitones:Number($('variantPitch').value),reverse:$('variantReverse').checked});
  function stop(){previewEpoch++;const previous=playId,token=previewToken;playId=null;previewToken=null;
   return Promise.all([token?bridge().stop_variant_preview(token):null,previous?bridge().stop_editor_preview(previous):null]);}
  async function preview(){
   const opened=epoch,request=++previewEpoch,id=clipId,revision=revisionId,effect=options();
   try{
    if(previewToken){const previous=previewToken;previewToken=null;await bridge().stop_variant_preview(previous);}
    if(playId){const previous=playId;playId=null;await bridge().stop_editor_preview(previous);}
    if(opened!==epoch||request!==previewEpoch)return;
    const token='variant:'+uuid();previewToken=token;
    const result=check(await bridge().preview_audio_variant(id,effect,revision,token));
    if(opened!==epoch||request!==previewEpoch){if(result.play_id)await bridge().stop_editor_preview(result.play_id);return;}
    playId=result.play_id;message('Prévia da variante no monitor.');
   }catch(error){if(opened===epoch&&request===previewEpoch)message(error.message);}
  }
  async function save(){
   if(saving)return {ok:false,ignored:true};
   const job={};saving=job;
   const opened=epoch,id=clipId,revision=revisionId,effect=options();$('variantCreate').disabled=true;
   try{const result=check(await bridge().create_audio_variant(id,effect,revision));created(result.clip);
    if(opened===epoch)message('Variante salva como outro clipe na biblioteca.');return result;
   }catch(error){if(opened===epoch)message(error.message);return {ok:false};}
   finally{if(saving===job)saving=null;if(opened===epoch)$('variantCreate').disabled=false;}
  }
  async function open(id,revision){
   close();clipId=id;revisionId=revision;const opened=epoch;
   $('variantSpeed').value='1';$('variantPitch').value='0';$('variantReverse').checked=false;
   $('variantCreate').disabled=!revision;$('variantPreview').disabled=!revision;
   $('variantPreview').onclick=preview;$('variantCreate').onclick=save;$('variantStop').onclick=()=>stop().catch(error=>message(error.message));
   message('Usa a revisão publicada ao abrir. Salve e reabra para usar alterações do editor.');
   try{const cap=await bridge().get_variant_capabilities();if(opened!==epoch)return;
    $('variantSpeed').disabled=!cap.speed;$('variantPitch').disabled=!cap.pitch;$('variantReverse').disabled=false;
    $('variantCapabilities').textContent=[cap.limitations,cap.reason].filter(Boolean).join(' ');
   }catch(error){if(opened===epoch)message(error.message);}
  }
  function close(){epoch++;clipId=null;revisionId=null;saving=null;stop().catch(()=>{});}
  return {open,close,preview,save,stop};
 }
 const exported={create};if(typeof module!=='undefined')module.exports=exported;root.AudioVariants=exported;
})(typeof window!=='undefined'?window:globalThis);
