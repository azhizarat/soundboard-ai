/* Editor da origem: comandos no servidor, prévia no monitor, texto independente. */
(function(root) {
  'use strict';
  const RATE=48000;
  const timeline=root.EditorTimeline || (typeof require==='function'?require('./editor-timeline.js'):null);
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
    let peaks=[],anchor=null,textDirty=false,correctionIds=[],playId=null,playRanges=[],timer=null,busy=0,previewEpoch=0,positionEpoch=0,seekQueue=Promise.resolve(),resizeObserver=null,editingText=false,pendingSeeks=0,renderedUnits=null;
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
      chosen=selection(pair,model.source_samples);
      if(updateText)syncCorrection();
      render();
    }
    function render() {
      if(!model)return;
      el('editorCursor').textContent=`Cursor ${(cursor/RATE).toFixed(3)} s`;
      el('editorMetrics').textContent=`Resultado ${model.duration.toFixed(3)} s · ${model.lufs==null?'nível indisponível':model.lufs.toFixed(1)+' LUFS'+(model.lufs_estimated?' (estimado)':'')} · volume ${Math.round(model.volume*100)}%`;
      const labels={aligned:'Alinhamento completo por palavras',partial:'Alinhamento parcial: palavras e blocos de segmento',segments:'Alinhamento por segmentos; sem tempos individuais',unaligned:'Transcrição editável · sem tempos por palavra'};
      el('editorAlignment').textContent=labels[model.result.alignment]||'Sem alinhamento';
      const area=el('editorWords');
      if(renderedUnits!==units){
      renderedUnits=units;area.replaceChildren();
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
        button.ondblclick=()=>{editingText=true;render();el('editorCorrection').focus();};
        area.appendChild(button);
      }
      }else{
        [...area.children].forEach((button,i)=>button.classList.toggle('selected',transcript.intersects(units[i],chosen)));
      }
      el('editorUndo').disabled=busy>0||!model.can_undo;
      el('editorRedo').disabled=busy>0||!model.can_redo;
      el('editorApplyText').disabled=busy>0||(!correctionIds.length&&model.result.alignment!=='unaligned');
      el('editorWords').hidden=!units.length||editingText;
      el('editorCorrectionRow').hidden=units.length>0&&!editingText;
      el('editorEditText').hidden=!units.length;
      el('editorApplyText').hidden=!textDirty;el('editorDiscardText').hidden=!textDirty;
      el('editorTextHint').hidden=!units.length||editingText;
      draw();
    }
    function draw() {
      if(!model)return;
      el('editorCursor').textContent=`Agulha ${(cursor/RATE).toFixed(3)} s`;
      const resultPosition=timeline.sourceToVariant(model.spans,cursor,model.variant||{})/RATE;
      el('editorSeek').max=String(model.duration);el('editorSeek').value=String(resultPosition);
      el('editorSeekTime').textContent=`${resultPosition.toFixed(3)} / ${model.duration.toFixed(3)} s`;
      el('editorSelectionLength').textContent=`Seleção ${((chosen[1]-chosen[0])/RATE).toFixed(3)} s`;
      el('editorViewport').textContent=`Origem ${(viewport[0]/RATE).toFixed(3)}–${(viewport[1]/RATE).toFixed(3)} s`;
      const canvas=el('editorWaveform'),width=Math.max(300,canvas.clientWidth||900),height=154;
      const ratio=root.devicePixelRatio||1;
      canvas.width=Math.round(width*ratio);canvas.height=Math.round(height*ratio);
      const ctx=canvas.getContext('2d');if(!ctx)return;ctx.scale(ratio,ratio);
      const x=sample=>(sample-viewport[0])/(viewport[1]-viewport[0])*width;
      ctx.fillStyle='#10151c';ctx.fillRect(0,0,width,height);
      // Régua independente das alças de corte; a agulha continua visível sobre a seleção.
      const raw=(viewport[1]-viewport[0])/RATE/Math.max(2,Math.floor(width/95));
      const magnitude=10**Math.floor(Math.log10(raw));
      const tick=[1,2,5,10].find(v=>v*magnitude>=raw)*magnitude;
      ctx.font='11px system-ui';ctx.fillStyle='#99a9bb';ctx.strokeStyle='#26313d';ctx.lineWidth=1;
      for(let sec=Math.ceil(viewport[0]/RATE/tick)*tick;sec<=viewport[1]/RATE;sec+=tick){
        const px=x(sec*RATE);ctx.fillText(sec.toFixed(tick<.1?3:tick<1?2:1)+'s',Math.min(width-46,Math.max(3,px+4)),16);
        ctx.beginPath();ctx.moveTo(px,25);ctx.lineTo(px,height);ctx.stroke();
      }
      ctx.fillStyle='rgba(80,149,207,.18)';ctx.fillRect(x(chosen[0]),26,x(chosen[1])-x(chosen[0]),height-26);
      for(let i=0;i<peaks.length;i++) {
        const sample=i/peaks.length*model.source_samples,right=(i+1)/peaks.length*model.source_samples;
        if(right<viewport[0]||sample>viewport[1])continue;
        ctx.fillStyle=model.spans.some(([a,b])=>sample<b&&right>a)?'#67b9d5':'#39424e';
        const bar=Math.max(1,Math.min(1,Math.abs(Number(peaks[i])||0))*102);
        ctx.fillRect(x(sample),89-bar/2,Math.max(1,x(right)-x(sample)),bar);
      }
      for(const [i,edge] of chosen.entries()){
        const px=Math.max(1,Math.min(width-1,x(edge)));
        if(x(edge)<0||x(edge)>width)continue;
        ctx.fillStyle=ctx.strokeStyle=i===0?'#57d6b0':'#f0b66b';ctx.lineWidth=2;
        ctx.beginPath();ctx.moveTo(px,27);ctx.lineTo(px,height);ctx.stroke();
        ctx.fillRect(i===0?px:px-10,28,10,19);ctx.fillStyle='#10151c';ctx.fillText(i===0?'I':'O',i===0?px+3:px-8,42);
      }
      ctx.fillStyle='#d69e57';model.spans.slice(1).forEach((span,i)=>{for(const edge of [model.spans[i][1],span[0]]){const px=x(edge);ctx.beginPath();ctx.moveTo(px-7,height);ctx.lineTo(px,height-12);ctx.lineTo(px+7,height);ctx.closePath();ctx.fill();}});
      if(cursor>=viewport[0]&&cursor<=viewport[1]){
        const px=Math.max(1,Math.min(width-1,x(cursor)));ctx.strokeStyle='#fff';ctx.lineWidth=1.5;
        ctx.beginPath();ctx.moveTo(px,23);ctx.lineTo(px,height);ctx.stroke();
        ctx.fillStyle='#fff';ctx.beginPath();ctx.moveTo(px-6,18);ctx.lineTo(px+6,18);ctx.lineTo(px+6,25);ctx.lineTo(px,31);ctx.lineTo(px-6,25);ctx.closePath();ctx.fill();
      }
    }
    function zoom(factor,at=cursor) {
      const width=Math.max(Math.min(model.source_samples,480),Math.min(model.source_samples,Math.round((viewport[1]-viewport[0])*factor)));
      const fraction=(at-viewport[0])/(viewport[1]-viewport[0]);
      const start=Math.max(0,Math.min(model.source_samples-width,Math.round(at-width*fraction)));
      viewport=[start,start+width];draw();
    }
    function setCursor(sample,seek=true) {
      if(!model)return;
      cursor=Math.max(0,Math.min(model.source_samples,Math.round(sample)));positionEpoch++;draw();
      if(seek&&playId){
        const id=playId,request=previewEpoch,opened=epoch;
        const position=timeline.sourceToPreview(model.spans,cursor,model.variant||{},playRanges)/RATE;
        pendingSeeks++;
        seekQueue=seekQueue.catch(()=>{}).then(async()=>{
          if(id!==playId||request!==previewEpoch||opened!==epoch){pendingSeeks--;return;}
          try {const result=await bridge().seek_playback(position,id);if(result?.ok===false&&id===playId)status('Não foi possível mover a prévia.');}
          catch(error){if(id===playId)status(error.message||String(error));}
          finally {pendingSeeks--;if(id===playId&&request===previewEpoch&&opened===epoch)positionEpoch++;}
        });
      }
    }
    function showPanel(name) {
      const panels={text:'editorTranscript',metadata:'editorMetadata',voice:'editorVoice',variants:'variantPanel',analysis:'analysisPanel'};
      for(const [key,id] of Object.entries(panels)){
        const panel=el(id),active=key===name;panel.hidden=!active;if(panel.tagName==='DETAILS')panel.open=active;
        const button=el('editorTab'+key);button.setAttribute?.('aria-selected',String(active));button.tabIndex=active?0:-1;
      }
      draw();
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
      if(opened===epoch&&result.ok&&el('editorCorrection').value===text){textDirty=false;editingText=false;syncCorrection();render();}
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
      const opened=epoch,id=playId,request=previewEpoch,positionRequest=positionEpoch;if(!id)return;
      try {
        const response=await bridge().get_editor_playback(id);
        if(opened!==epoch||id!==playId||request!==previewEpoch)return;
        const playback=response.status||response;
        if(!playback.playing){playId=null;return;}
        const output=outputToSource(playRanges,Math.round(Number(playback.position||0)*RATE));
        if(positionRequest===positionEpoch&&!pendingSeeks){cursor=variantToSource(model.spans,output,model.variant||{});draw();}
        for(const button of el('editorWords').children) {
          const unit=units.find(u=>u.id===button.dataset.unitId);
          button.classList.toggle('playing',Boolean(unit&&transcript.intersects(unit,[cursor,cursor+1])));
        }
        timer=setTimeout(poll,80);
      } catch(_error){if(opened===epoch&&id===playId&&request===previewEpoch)playId=null;}
    }
    async function preview(mode='selection',fromCursor=false,junction=0) {
      const startCursor=cursor;
      const opened=epoch,request=++previewEpoch;
      const previousId=playId;playId=null;stopPolling();
      if(previousId)await bridge().stop_editor_preview(previousId);
      if(opened!==epoch||request!==previewEpoch)return;
      if(!(await flush())?.ok)return;
      const saved=await callbacks.persist?.();
      if(opened!==epoch||request!==previewEpoch||(callbacks.persist&&!saved?.ok))return;
      const result=await bridge().preview_clip_edit(clipId,mode,[...chosen],junction,el('editorLoop').checked);
      if(opened!==epoch||request!==previewEpoch){if(result?.play_id)await bridge().stop_editor_preview(result.play_id);return;}
      if(!result.ok){status(result.error);return;}
      stopPolling();playId=result.play_id;playRanges=result.ranges;
      if(fromCursor){setCursor(startCursor);await seekQueue;}else {cursor=variantToSource(model.spans,outputToSource(playRanges,0),model.variant||{});draw();}
      if(opened!==epoch||request!==previewEpoch)return;
      status('Prévia no monitor. Arraste a agulha para navegar.');timer=setTimeout(poll,80);
    }
    function bind() {
      for(const [id,action] of [['editorKeep','keep'],['editorRemove','remove'],['editorRestore','restore'],['editorCreate','create'],['editorUndo','undo'],['editorRedo','redo']])
        el(id).onclick=async()=>{if(textDirty&&!(await applyText()).ok)return;await run(action,{interval:[...chosen]});};
      el('editorApplyText').onclick=applyText;
      el('editorDiscardText').onclick=()=>{textDirty=false;editingText=false;setSelection(chosen);};
      el('editorEditText').onclick=()=>{editingText=true;render();el('editorCorrection').focus();};
      el('editorCorrection').oninput=()=>{textDirty=true;el('editorApplyText').hidden=false;el('editorDiscardText').hidden=false;status('Texto corrigido ainda não aplicado · Ctrl+Enter aplica; Salvar também preserva.');};
      el('editorCorrection').onkeydown=event=>{if((event.ctrlKey||event.metaKey)&&event.key==='Enter'){event.preventDefault();event.stopPropagation?.();applyText();}};
      el('editorZoom').onclick=()=>{viewport=[...chosen];draw();};
      el('editorZoomIn').onclick=()=>zoom(.5);el('editorZoomOut').onclick=()=>zoom(2);
      el('editorHearCursor').onclick=()=>preview('result',true);
      el('editorSeek').oninput=()=>setCursor(variantToSource(model.spans,Math.round(Number(el('editorSeek').value)*RATE),model.variant||{}));
      el('editorLoop').onchange=()=>{if(playId)Promise.resolve(bridge().set_playback_loop(el('editorLoop').checked,playId)).catch(error=>status(error.message));};
      const panelNames=['text','metadata','voice','variants','analysis'];
      for(const [i,name] of panelNames.entries()){
        const tab=el('editorTab'+name);tab.onclick=()=>showPanel(name);
        tab.onkeydown=event=>{if(['ArrowLeft','ArrowRight','Home','End'].includes(event.key)){event.preventDefault();event.stopPropagation();const next=event.key==='Home'?0:event.key==='End'?panelNames.length-1:(i+(event.key==='ArrowRight'?1:-1)+panelNames.length)%panelNames.length;showPanel(panelNames[next]);el('editorTab'+panelNames[next]).focus();}};
      }

      el('editorFit').onclick=()=>{viewport=[0,model.source_samples];draw();};
      for(const [id,direction] of [['editorPanLeft',-1],['editorPanRight',1]])el(id).onclick=()=>{
        const width=viewport[1]-viewport[0],start=Math.max(0,Math.min(model.source_samples-width,viewport[0]+direction*width*.5));viewport=[start,start+width];draw();};
      el('editorHear').onclick=()=>preview();el('editorHearResult').onclick=()=>preview('result');
      el('editorStop').onclick=invalidatePreview;
      el('editorRetranscribe').onclick=()=>{
        showPanel('analysis');el('analysisPanel').open=true;el('analysisStage').value='transcription';el('analysisSelection').checked=true;
        el('analysisStart').value=chosen[0]/RATE;el('analysisEnd').value=chosen[1]/RATE;
        el('analysisProposal').textContent='Texto atual: '+units.filter(u=>transcript.intersects(u,chosen)).map(u=>u.text).join(' ');
        callbacks.retranscribe?.();
      };
      const canvas=el('editorWaveform');let drag=null;
      const sample=event=>Math.max(0,Math.min(model.source_samples,Math.round(viewport[0]+(event.clientX-canvas.getBoundingClientRect().left)/canvas.getBoundingClientRect().width*(viewport[1]-viewport[0]))));
      canvas.onpointerdown=event=>{
        if(!model||event.button>0)return;event.preventDefault();canvas.focus();
        const at=sample(event),rect=canvas.getBoundingClientRect(),tolerance=10/rect.width*(viewport[1]-viewport[0]);
        const onNeedle=event.clientY-rect.top<28||event.ctrlKey||event.metaKey;
        if(event.clientY-rect.top>rect.height-14&&!event.shiftKey&&!event.altKey){
          const junction=model.spans.slice(1).findIndex((span,i)=>Math.abs(at-span[0])<tolerance||Math.abs(at-model.spans[i][1])<tolerance);
          if(junction>=0){preview('junction',false,junction);return;}
        }
        const edge=Math.abs(at-chosen[0])<=tolerance?0:Math.abs(at-chosen[1])<=tolerance?1:null;
        const mode=onNeedle?'cursor':event.shiftKey?'select':event.altKey?'move':edge!==null?'edge':'cursor';
        if(textDirty&&mode!=='cursor'){status('Aplique ou descarte a correção antes de mudar a seleção.');return;}
        drag={origin:at,edge,mode,selection:[...chosen]};canvas.setPointerCapture(event.pointerId);
        if(mode==='cursor')setCursor(at);
      };
      canvas.onpointermove=event=>{
        if(!model)return;const at=sample(event);
        if(!drag){const tolerance=10/canvas.clientWidth*(viewport[1]-viewport[0]);canvas.style.cursor=chosen.some(v=>Math.abs(at-v)<=tolerance)?'ew-resize':event.shiftKey?'crosshair':'col-resize';return;}
        if(drag.mode==='cursor')setCursor(at);
        else if(drag.mode==='edge')setSelection(drag.edge===0?[Math.min(at,chosen[1]-1),chosen[1]]:[chosen[0],Math.max(at,chosen[0]+1)]);
        else if(drag.mode==='move'){
          const delta=Math.max(-drag.selection[0],Math.min(model.source_samples-drag.selection[1],at-drag.origin));
          setSelection(drag.selection.map(v=>v+delta));
        }else setSelection([Math.min(at,drag.origin),Math.max(at,drag.origin)]);
      };
      canvas.onpointerup=()=>{drag=null;};canvas.onpointercancel=()=>{drag=null;};canvas.onlostpointercapture=()=>{drag=null;};
      canvas.onwheel=event=>{if(!model)return;event.preventDefault();zoom(event.deltaY>0?1.4:1/1.4,sample(event));};
      host.onkeydown=event=>{
        if(!model||event.defaultPrevented)return;
        const key=event.key.toLowerCase(),modified=event.ctrlKey||event.metaKey;
        if(modified&&key==='s'&&!event.shiftKey){event.preventDefault();event.stopPropagation?.();el('btnSaveClip').click?.();return;}
        if(editingTarget(event.target))return;
        if(modified&&['z','y'].includes(key)){
          event.preventDefault();event.stopPropagation?.();el(key==='y'||event.shiftKey?'editorRedo':'editorUndo').onclick();return;
        }
        if(modified||event.altKey)return;
        const actions={delete:'editorRemove',backspace:'editorRemove',k:'editorKeep','+':'editorZoomIn','=':'editorZoomIn','-':'editorZoomOut',f:'editorFit'};
        if(key==='arrowleft'||key==='arrowright'){
          event.preventDefault();event.stopPropagation?.();const direction=key==='arrowright'?1:-1;
          setCursor(cursor+direction*(event.shiftKey?4800:480));
        }else if(key==='home'||key==='end'){event.preventDefault();setCursor(key==='home'?chosen[0]:chosen[1]);}
        else if(key===' '&&event.target?.tagName!=='BUTTON'){event.preventDefault();event.stopPropagation?.();if(playId)invalidatePreview();else preview(event.shiftKey?'result':'selection',event.shiftKey);}
        else if((key==='i'||key==='o')&&!textDirty){event.preventDefault();setSelection(key==='i'?[Math.min(cursor,chosen[1]-1),chosen[1]]:[chosen[0],Math.max(cursor,chosen[0]+1)]);}
        else if(actions[key]){event.preventDefault();event.stopPropagation?.();if(!textDirty||['+','=','-','f'].includes(key))el(actions[key]).onclick();}
      };
    }

    async function open(id) {
      close();clipId=id;const opened=epoch;queue=Promise.resolve({ok:true});bind();
      const result=await bridge().get_clip_editor(id);
      if(opened!==epoch)return;
      if(!result.ok){status(result.error);return;}
      chosen=[...result.editor.spans[0]];cursor=chosen[0];viewport=[0,result.editor.source_samples];receive(result.editor);setSelection(chosen);
      variants?.open(id,result.editor.revision_id);showPanel('text');
      if(root.ResizeObserver){resizeObserver=new root.ResizeObserver(draw);resizeObserver.observe(el('editorWaveform'));}
      if(model.result.alignment==='unaligned')el('editorCorrection').value=model.result.text;
      const waveform=await bridge().get_edit_source_waveform(id,2048);
      if(opened===epoch&&waveform.ok){peaks=waveform.peaks||[];draw();}
      status('Clique ou arraste para navegar. Alças I/O ajustam o corte; Shift+arraste cria uma seleção.');
    }
    function close(){resizeObserver?.disconnect();resizeObserver=null;epoch++;invalidatePreview();variants?.close();clipId=null;model=null;peaks=[];textDirty=false;editingText=false;correctionIds=[];anchor=null;}
    return {open,close,flush,refresh,preview,run,setSelection,showPanel,settleSeek:()=>seekQueue,settle:()=>queue,get selection(){return [...chosen];},get cursor(){return cursor;}};
  }
  const exported={create,selection,nudge,editingTarget,outputToSource,variantToSource};
  if(typeof module!=='undefined'&&module.exports)module.exports=exported;else root.IntegratedEditor=exported;
})(typeof window!=='undefined'?window:globalThis);
