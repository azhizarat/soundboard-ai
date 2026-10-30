/* Editor da origem: comandos no servidor, prévia no monitor, texto independente. */
(function(root) {
  'use strict';
  const RATE=48000;
  function selection(pair,total) {
    const a=Math.max(0,Math.min(total-1,Math.round(Number(pair[0])||0)));
    return [a,Math.max(a+1,Math.min(total,Math.round(Number(pair[1])||0)))];
  }
  function nudge(pair,edge,direction,large,total) {
    const next=[...pair],step=large?4800:480;
    next[edge]=edge===0?Math.max(0,Math.min(pair[1]-1,pair[0]+direction*step)):Math.min(total,Math.max(pair[0]+1,pair[1]+direction*step));
    return next;
  }
  function editingTarget(target) {return Boolean(target?.isContentEditable||['INPUT','TEXTAREA','SELECT'].includes(target?.tagName));}
  function outputToSource(spans,sample) {
    for(const [a,b] of spans){if(sample<b-a)return a+sample;sample-=b-a;}
    return spans.at(-1)?.[1]||0;
  }
  function variantToSource(spans,sample,effect={}) {
    const total=spans.reduce((n,[a,b])=>n+b-a,0),speed=effect.speed||1;
    const mapped=effect.reverse?total-1-Math.floor(sample*speed):Math.floor(sample*speed);
    return outputToSource(spans,Math.max(0,Math.min(total-1,mapped)));
  }
  function create(document,bridge,callbacks={}) {
    const el=id=>document.getElementById(id), host=el('integratedEditor');
    const transcript=root.TranscriptSelection || (typeof require==='function'?require('./transcript.js'):null);
    const variants=root.AudioVariants?.create(document,bridge,clip=>callbacks.created?.(clip));
    let clipId=null,model=null,units=[],chosen=[0,1],cursor=0,viewport=[0,1],epoch=0,queue=Promise.resolve({ok:true});
    let peaks=[],anchor=null,textDirty=false,correctionIds=[],playId=null,playRanges=[],timer=null,busy=0,previewEpoch=0;
    const status=text=>{el('editorStatus').textContent=text;};
    function receive(next) {
      if(model&&model.draft_id===next.draft_id&&model.generation>next.generation)return;
      if(model&&JSON.stringify(model.spans)!==JSON.stringify(next.spans))invalidatePreview();
      model=next; units=transcript.units(model.source_transcript,model.spans);
      chosen=selection(chosen,model.source_samples);
      callbacks.transcriptChanged?.(model.result.text);
      syncCorrection();
      render();
    }
    function syncCorrection() {
      if(!model||textDirty)return;
      const selected=units.filter(u=>transcript.intersects(u,chosen));
      correctionIds=[...new Set(selected.flatMap(u=>u.word_ids))];
      el('editorCorrection').value=model.result.alignment==='unaligned'?model.result.text:selected.map(u=>u.text).join(' ');
    }
    function setSelection(pair,updateText=true) {
      if(!model)return;
      chosen=selection(pair,model.source_samples); cursor=chosen[0];
      if(updateText)syncCorrection();
      render();
    }
    function render() {
      if(!model)return;
      el('editorStart').value=(chosen[0]/RATE).toFixed(5);
      el('editorEnd').value=(chosen[1]/RATE).toFixed(5);
      el('editorCursor').textContent=`Cursor ${(cursor/RATE).toFixed(3)} s`;
      el('editorMetrics').textContent=`Resultado ${model.duration.toFixed(3)} s · ${model.lufs==null?'nível indisponível':model.lufs.toFixed(1)+' LUFS'+(model.lufs_estimated?' (estimado)':'')} · volume ${Math.round(model.volume*100)}%`;
      const labels={aligned:'Alinhamento completo por palavras',partial:'Alinhamento parcial: palavras e blocos de segmento',segments:'Alinhamento por segmentos; sem tempos individuais',unaligned:'Texto legado sem alinhamento; selecione áudio pela waveform'};
      el('editorAlignment').textContent=labels[model.result.alignment]||'Sem alinhamento';
      el('editorResult').textContent=(model.result.reversed_audio?'Áudio invertido; texto descreve somente a origem: ':'')+(model.result.text||'Sem texto reconhecido. O áudio continua editável.');
      const area=el('editorWords');area.replaceChildren();
      for(const unit of units) {
        const button=document.createElement('button');button.type='button';button.className='editor-word';
        button.textContent=unit.text||'(texto apagado)';button.dataset.unitId=unit.id;
        button.classList.toggle('removed',unit.removed);button.classList.toggle('partial',unit.partial);
        button.classList.toggle('selected',transcript.intersects(unit,chosen));
        button.title=`${unit.kind==='word'?'Palavra':'Bloco indivisível'} · ${unit.anchors.map(([a,b])=>(a/RATE).toFixed(3)+'–'+(b/RATE).toFixed(3)+' s').join(', ')}`;
        button.onclick=event=>{
          if(textDirty){status('Aplique ou descarte a correção antes de selecionar outro bloco.');return;}
          const bounds=[Math.min(...unit.anchors.map(a=>a[0])),Math.max(...unit.anchors.map(a=>a[1]))];
          if(event.shiftKey&&anchor) setSelection([Math.min(anchor[0],bounds[0]),Math.max(anchor[1],bounds[1])]);
          else {anchor=bounds;setSelection(bounds);}
        };
        area.appendChild(button);
      }
      el('editorUndo').disabled=busy>0||!model.can_undo;
      el('editorRedo').disabled=busy>0||!model.can_redo;
      el('editorApplyText').disabled=busy>0||(!correctionIds.length&&model.result.alignment!=='unaligned');
      const junctions=el('editorJunction');const previous=junctions.value;junctions.replaceChildren();
      model.spans.slice(1).forEach((span,i)=>{const option=document.createElement('option');option.value=String(i);option.textContent=`Junção ${i+1}: ${(model.spans[i][1]/RATE).toFixed(2)} → ${(span[0]/RATE).toFixed(2)} s`;junctions.appendChild(option);});
      if([...junctions.options].some(o=>o.value===previous))junctions.value=previous;
      el('editorHearJunction').disabled=model.spans.length<2;
      draw();
    }
    function draw() {
      if(!model)return;
      el('editorCursor').textContent=`Cursor ${(cursor/RATE).toFixed(3)} s`;
      const canvas=el('editorWaveform'),width=Math.max(300,canvas.clientWidth||900),height=130;
      canvas.width=width;canvas.height=height;
      const ctx=canvas.getContext('2d');if(!ctx)return;
      const x=sample=>(sample-viewport[0])/(viewport[1]-viewport[0])*width;
      ctx.fillStyle='#121620';ctx.fillRect(0,0,width,height);
      for(let i=0;i<peaks.length;i++) {
        const sample=i/peaks.length*model.source_samples;
        const right=(i+1)/peaks.length*model.source_samples;
        if(right<viewport[0]||sample>viewport[1])continue;
        const kept=model.spans.some(([a,b])=>sample<b&&right>a);
        ctx.fillStyle=kept?'#62b4dd':'#414751';
        const value=Number(peaks[i])||0,bar=Math.max(2,Math.min(1,Math.abs(value))*100);
        ctx.fillRect(x(sample),65-bar/2,Math.max(1,x(right)-x(sample)),bar);
      }
      ctx.fillStyle='rgba(99,179,237,.22)';ctx.fillRect(x(chosen[0]),0,x(chosen[1])-x(chosen[0]),height);
      ctx.strokeStyle='#b4e1ff';ctx.lineWidth=2;
      for(const edge of chosen){ctx.beginPath();ctx.moveTo(x(edge),0);ctx.lineTo(x(edge),height);ctx.stroke();}
      ctx.strokeStyle='#f5b05e';
      for(const [a,b] of model.spans){for(const edge of [a,b]){ctx.beginPath();ctx.moveTo(x(edge),100);ctx.lineTo(x(edge),130);ctx.stroke();}}
      ctx.strokeStyle='#ffffff';ctx.beginPath();ctx.moveTo(x(cursor),0);ctx.lineTo(x(cursor),height);ctx.stroke();
      el('editorViewport').textContent=`Origem ${(viewport[0]/RATE).toFixed(2)}–${(viewport[1]/RATE).toFixed(2)} s · arraste para selecionar; Shift+clique no texto amplia`;
    }
    async function run(action,payload={}) {
      const opened=epoch,target=clipId;
      busy++;render();
      const operation=async()=>{
        if(opened!==epoch)return {ok:false};
        const saved=await callbacks.persist?.();
        if(opened!==epoch)return {ok:false};
        if(callbacks.persist&&!saved?.ok){status('Falha ao preservar campos. A seleção e correção foram mantidas.');return {ok:false};}
        // Autosave pode avançar a geração. O comando captura o estado atual após a fila.
        const fresh=await bridge().get_clip_editor(target);
        if(opened!==epoch)return {ok:false};
        if(!fresh.ok){status(fresh.error);return fresh;}
        const response=await bridge().edit_clip_command(target,action,{...payload,draft_id:fresh.editor.draft_id,generation:fresh.editor.generation});
        if(opened!==epoch)return response;
        if(response.ok){receive(response.editor);if(response.clip)callbacks.created?.(response.clip);status(action==='create'?'Outro clipe salvo; origem compartilhada.':'Edição preservada.');}
        else status(response.error||'Falha ao preservar edição.');
        return response;
      };
      queue=queue.catch(()=>({ok:false})).then(operation).catch(error=>{if(opened===epoch)status(error.message||String(error));return {ok:false};}).finally(()=>{busy--;if(opened===epoch)render();});
      return queue;
    }
    async function applyText() {
      const text=el('editorCorrection').value,ids=[...correctionIds],opened=epoch;
      const result=await run(model.result.alignment==='unaligned'?'legacy_text':'correct',{word_ids:ids,text});
      if(opened===epoch&&result.ok&&el('editorCorrection').value===text){textDirty=false;syncCorrection();render();}
      return result;
    }
    async function flush() {
      await queue;
      if(textDirty)return applyText();
      return {ok:true};
    }
    async function refresh() {
      const opened=epoch;
      const result=await bridge().get_clip_editor(clipId);
      if(opened===epoch&&result.ok)receive(result.editor);
      return result;
    }
    function stopPolling(){if(timer!==null)clearTimeout(timer);timer=null;}
    function invalidatePreview() {
      previewEpoch++;stopPolling();const id=playId;playId=null;
      // Invalida imediatamente: uma resposta de poll em trânsito não usa a nova montagem.
      return id?Promise.resolve(bridge().stop_editor_preview(id)).catch(error=>{status(error.message||String(error));}):Promise.resolve();
    }
    async function poll() {
      const opened=epoch,id=playId,request=previewEpoch;if(!id)return;
      try {
        const response=await bridge().get_editor_playback(id);
        if(opened!==epoch||id!==playId||request!==previewEpoch)return;
        const playback=response.status||response;
        if(!playback.playing){playId=null;return;}
        const output=outputToSource(playRanges,Math.round(Number(playback.position||0)*RATE));
        cursor=variantToSource(model.spans,output,model.variant||{});draw();
        for(const button of el('editorWords').children) {
          const unit=units.find(u=>u.id===button.dataset.unitId);
          button.classList.toggle('playing',Boolean(unit&&transcript.intersects(unit,[cursor,cursor+1])));
        }
        timer=setTimeout(poll,80);
      } catch(_error){if(opened===epoch&&id===playId&&request===previewEpoch)playId=null;}
    }
    async function preview(mode='selection') {
      const opened=epoch,request=++previewEpoch;
      if(playId)await bridge().stop_editor_preview(playId);
      playId=null;stopPolling();
      if(!(await flush())?.ok)return;
      const saved=await callbacks.persist?.();
      if(opened!==epoch||request!==previewEpoch||(callbacks.persist&&!saved?.ok))return;
      const result=await bridge().preview_clip_edit(clipId,mode,[...chosen],Number(el('editorJunction').value||0),el('editorLoop').checked);
      if(opened!==epoch||request!==previewEpoch){if(result?.play_id)await bridge().stop_editor_preview(result.play_id);return;}
      if(!result.ok){status(result.error);return;}
      stopPolling();playId=result.play_id;playRanges=result.ranges;status('Prévia no monitor.');timer=setTimeout(poll,80);
    }
    function bind() {
      for(const [id,action] of [['editorKeep','keep'],['editorRemove','remove'],['editorRestore','restore'],['editorCreate','create'],['editorUndo','undo'],['editorRedo','redo']])
        el(id).onclick=async()=>{if(textDirty&&!(await applyText()).ok)return;await run(action,{interval:[...chosen]});};
      el('editorApplyText').onclick=applyText;
      el('editorDiscardText').onclick=()=>{textDirty=false;setSelection(chosen);};
      el('editorCorrection').oninput=()=>{textDirty=true;};
      for(const id of ['editorStart','editorEnd'])el(id).onchange=()=>setSelection([Number(el('editorStart').value)*RATE,Number(el('editorEnd').value)*RATE]);
      el('editorZoom').onclick=()=>{viewport=[...chosen];draw();};
      el('editorFit').onclick=()=>{viewport=[0,model.source_samples];draw();};
      for(const [id,direction] of [['editorPanLeft',-1],['editorPanRight',1]])el(id).onclick=()=>{
        const width=viewport[1]-viewport[0],start=Math.max(0,Math.min(model.source_samples-width,viewport[0]+direction*width*.5));viewport=[start,start+width];draw();};
      el('editorHear').onclick=()=>preview();el('editorHearResult').onclick=()=>preview('result');el('editorHearJunction').onclick=()=>preview('junction');
      el('editorStop').onclick=invalidatePreview;
      el('editorMarkStart').onclick=()=>setSelection([Math.min(cursor,chosen[1]-1),chosen[1]]);
      el('editorMarkEnd').onclick=()=>setSelection([chosen[0],Math.max(cursor,chosen[0]+1)]);
      el('editorSuggest').onclick=async()=>{
        const opened=epoch;const result=await bridge().suggest_clip_edges(clipId,Number(el('editorMargin').value));
        if(opened!==epoch)return;
        if(result.ok){setSelection(result.interval);status(result.reason+' Apenas seleção: use Ouvir e Manter para aplicar.');}else status(result.error);
      };
      el('editorRetranscribe').onclick=()=>{
        el('analysisPanel').open=true;el('analysisStage').value='transcription';el('analysisSelection').checked=true;
        el('analysisStart').value=chosen[0]/RATE;el('analysisEnd').value=chosen[1]/RATE;
        el('analysisProposal').textContent='Texto atual: '+units.filter(u=>transcript.intersects(u,chosen)).map(u=>u.text).join(' ');
        callbacks.retranscribe?.();
      };
      const canvas=el('editorWaveform');let drag=null;
      const sample=event=>Math.max(0,Math.min(model.source_samples,Math.round(viewport[0]+(event.clientX-canvas.getBoundingClientRect().left)/canvas.getBoundingClientRect().width*(viewport[1]-viewport[0]))));
      canvas.onpointerdown=event=>{
        if(!model||textDirty)return;canvas.focus();const at=sample(event),tolerance=10/canvas.clientWidth*(viewport[1]-viewport[0]);
        if(event.ctrlKey||event.metaKey){cursor=at;render();return;}
        drag={origin:at,edge:Math.abs(at-chosen[0])<tolerance?0:Math.abs(at-chosen[1])<tolerance?1:null};canvas.setPointerCapture(event.pointerId);
        if(drag.edge===null)setSelection([at,at+1]);
      };
      canvas.onpointermove=event=>{if(!drag)return;const at=sample(event);
        if(drag.edge===0)setSelection([Math.min(at,chosen[1]-1),chosen[1]]);
        else if(drag.edge===1)setSelection([chosen[0],Math.max(at,chosen[0]+1)]);
        else setSelection([Math.min(at,drag.origin),Math.max(at,drag.origin)+1]);};
      canvas.onpointerup=()=>{drag=null;};canvas.onpointercancel=()=>{drag=null;};
      host.onkeydown=event=>{
        if(editingTarget(event.target)||!model)return;
        if(event.key==='ArrowLeft'||event.key==='ArrowRight'){
          event.preventDefault();setSelection(nudge(chosen,el('editorBoundary').value==='end'?1:0,event.key==='ArrowRight'?1:-1,event.shiftKey,model.source_samples));
        }else if((event.ctrlKey||event.metaKey)&&['z','y'].includes(event.key.toLowerCase())){
          event.preventDefault();run(event.key.toLowerCase()==='y'||event.shiftKey?'redo':'undo');
        }
      };
    }
    async function open(id) {
      close();clipId=id;const opened=epoch;queue=Promise.resolve({ok:true});bind();
      const result=await bridge().get_clip_editor(id);
      if(opened!==epoch)return;
      if(!result.ok){status(result.error);return;}
      chosen=[...result.editor.spans[0]];viewport=[0,result.editor.source_samples];receive(result.editor);setSelection(chosen);
      variants?.open(id,result.editor.revision_id);
      if(model.result.alignment==='unaligned')el('editorCorrection').value=model.result.text;
      const waveform=await bridge().get_edit_source_waveform(id,2048);
      if(opened===epoch&&waveform.ok){peaks=waveform.peaks||[];draw();}
      status('Selecione pelo texto ou waveform. Clique não inicia reprodução.');
    }
    function close(){epoch++;invalidatePreview();variants?.close();clipId=null;model=null;peaks=[];textDirty=false;correctionIds=[];anchor=null;}
    return {open,close,flush,refresh,preview,run,setSelection,settle:()=>queue,get selection(){return [...chosen];}};
  }
  const exported={create,selection,nudge,editingTarget,outputToSource,variantToSource};
  if(typeof module!=='undefined'&&module.exports)module.exports=exported;else root.IntegratedEditor=exported;
})(typeof window!=='undefined'?window:globalThis);
