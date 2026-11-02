(function(root){
  'use strict';
  const labels={queued:'Na fila',probing:'Lendo cabeçalho',awaiting_window:'Escolha uma janela',running:'Processando',success:'Importado',duplicate:'Já importado',failed:'Falhou',cancelled:'Cancelado',interrupted:'Interrompido',ready:'Pronta',manual:'Manual',paused:'Pausada',waiting_policy:'Aguardando política',unavailable:'Indisponível',obsolete:'Clipe alterado'};
  function create(dom,bridge,options={}){
    const $=id=>dom.getElementById(id), selected=new Set(), windows=new Map(), rows=new Map();
    let jobs=[],busy=false,timer=null,closed=false;
    const element=(tag,text,className)=>{const node=dom.createElement(tag);if(text!==undefined)node.textContent=text;if(className)node.className=className;return node;};
    const message=text=>{$('importQueueMessage').textContent=text;};
    function button(text,action,handler){const node=element('button',text,'btn btn-xs btn-secondary');node.type='button';node.dataset.action=action;node.onclick=()=>Promise.resolve(handler()).catch(error=>message(error.message||String(error)));return node;}
    async function operation(name,...args){try{const result=await bridge()[name](...args);if(!result?.ok)message(result?.error||'Não foi possível concluir');await refresh();return result;}catch(error){message(error.message||String(error));return null;}}
    function render(){
      const list=$('importQueueItems'),present=new Set(jobs.map(job=>job.id));
      for(const [id,previous] of rows){if(!present.has(id)){previous.row.remove();rows.delete(id);selected.delete(id);windows.delete(id);}}
      const pending=jobs.filter(job=>['queued','probing','running','awaiting_window'].includes(job.status)).length;
      $('importQueueCount').textContent=jobs.length?`${jobs.length} itens${pending?' · '+pending+' em andamento':''}`:'Nenhuma importação';
      for(const job of jobs){
        const signature=JSON.stringify(job),previous=rows.get(job.id);
        if(previous?.signature===signature)continue;
        // Não substitui um controle durante digitação; reconcilia no próximo poll após sair.
        if(previous&&previous.status===job.status&&job.status==='awaiting_window'&&previous.row.contains(dom.activeElement))continue;
        const row=element('article',undefined,'import-job');row.dataset.importId=job.id;
        const head=element('div',undefined,'import-job-heading'),check=element('input');check.type='checkbox';check.checked=selected.has(job.id);check.setAttribute('aria-label',`Selecionar ${job.source}`);check.onchange=()=>select(job.id,check.checked);
        const source=String(job.source||''),name=element('strong',source.split(/[\\/]/).filter(Boolean).pop()||source);name.title=source;
        head.append(check,name,element('span',labels[job.status]||job.status));row.append(head);
        const progress=element('progress');progress.max=100;progress.value=job.progress||0;progress.setAttribute('aria-label','Progresso de importação');row.append(progress);
        const actions=element('div',undefined,'import-job-actions');
        if(['queued','probing','running','awaiting_window'].includes(job.status))actions.append(button('Cancelar','cancel',()=>cancel(job.id)));
        if(['failed','cancelled','interrupted'].includes(job.status))actions.append(button('Tentar novamente','retry',()=>retry(job.id)));
        if(job.clip_id)actions.append(button('Abrir no editor','open',()=>open(job.clip_id)));
        if(job.status==='awaiting_window'){
          const values=windows.get(job.id)||[0,Math.min(120,job.duration)];
          const start=element('input'),end=element('input');start.type=end.type='number';start.min=end.min=0;start.max=end.max=job.duration;start.step=end.step=.1;
          start.value=values[0];end.value=values[1];start.setAttribute('aria-label','Início em segundos');end.setAttribute('aria-label','Fim em segundos');
          const remember=()=>windows.set(job.id,[start.value,end.value]);start.oninput=end.oninput=remember;
          actions.append(element('span',`${Number(job.duration).toFixed(1)} s. Janela até 120 s:`),start,end,button('Importar janela','window',()=>windowSelection(job.id,Number(start.value),Number(end.value))));
        }
        row.append(actions);
        if(job.analysis){
          const analysis=job.analysis;row.append(element('p',`IA: ${labels[analysis.status]||analysis.status}${analysis.analysis_id?' — '+(analysis.stages?.[analysis.index]||''):''}${analysis.error?' — '+(typeof analysis.error==='string'?analysis.error:analysis.error.message||'Erro'):''}`));
          if(['queued','running','paused','waiting_policy'].includes(analysis.status)&&job.status==='success')actions.append(button('Cancelar análise','cancel-analysis',()=>cancel(job.id)));
          if(['failed','cancelled','interrupted','unavailable','obsolete','manual'].includes(analysis.status)&&job.status==='success')actions.append(button('Analisar novamente','retry-analysis',()=>operation('retry_import_analysis',job.id)));
        }
        if(job.error)row.append(element('p',job.error,'import-job-error'));
        for(const warning of job.warnings||[])row.append(element('small',warning));
        const history=element('details'),summary=element('summary','Histórico e origem');history.append(summary,element('p',source),element('p',(job.history||[]).map(item=>`${new Date(item.at*1000).toLocaleTimeString()} ${labels[item.status]||item.status}`).join(' → ')));row.append(history);
        history.open=previous?.history.open||false;
        if(previous)previous.row.replaceWith(row);else list.append(row);
        rows.set(job.id,{row,history,signature,status:job.status});
      }
    }
    async function refresh(){
      if(busy||closed)return;busy=true;
      try{const result=await bridge().list_import_jobs();if(result?.ok){
        const previous=new Map(jobs.map(job=>[job.id,job.status])),next=result.jobs||[];
        const active=status=>['queued','probing','running','awaiting_window'].includes(status);
        // Histórico não ocupa a biblioteca. Progresso contínuo respeita o recolhimento manual.
        const attention=next.some(job=>(active(job.status)&&!active(previous.get(job.id)))||
          (job.status==='awaiting_window'&&previous.get(job.id)!=='awaiting_window'));
        if(attention)$('importQueue').open=true;
        jobs=next;render();
      }}
      catch(error){message(error.message||String(error));}finally{busy=false;}
    }
    function select(id,value){if(value)selected.add(id);else selected.delete(id);}
    function open(id){return options.open?.({id});}
    const cancel=id=>operation('cancel_import_job',id),retry=id=>operation('retry_import_job',id);
    const windowSelection=(id,start,end)=>operation('set_import_window',id,start,end);
    async function batch(){
      if(!selected.size){message('Selecione os itens que devem receber os metadados.');return;}
      const updates={};for(const [id,key] of [['importBatchCategory','category'],['importBatchTitle','title']]){const value=$ (id)?.value?.trim();if(value)updates[key]=value;}
      const tags=$('importBatchTags')?.value?.trim();if(tags)updates.tags=tags.split(',').map(value=>value.trim()).filter(Boolean);
      const result=await operation('apply_import_metadata',[...selected],updates);
      if(result?.ok)message(`${result.changed?.length||0} item(ns) atualizado(s). ${result.conflicts?.length||0} alteração(ões) posterior(es) preservada(s).`);
      options.updated?.();
    }
    async function initialize(){
      const dismiss=()=>{$('importQueue').open=false;$('importQueueToggle')?.focus();};
      $('btnCloseImportQueue').onclick=dismiss;
      $('importQueue').addEventListener('keydown',event=>{if(event.key==='Escape'){
        event.preventDefault();event.stopPropagation();dismiss();
      }});
      $('btnImportBatchApply').onclick=batch;
      $('importAutoAnalyze').onchange=()=>operation('set_import_options',$('importAutoAnalyze').checked);
      const admit=folder=>{const vinheta=$('importQueueMode').value==='vinheta';return operation('enqueue_import',null,vinheta,vinheta?'Vinhetas':null,folder,$('importAutoAnalyze').checked);};
      $('btnQueueAddFiles').onclick=()=>admit(false);
      $('btnQueueAddFolder').onclick=()=>admit(true);
      await refresh();timer=setInterval(refresh,650);
      if(bridge().get_import_capabilities){const caps=await bridge().get_import_capabilities();$('importDropHint').textContent=caps.video?'Arraste arquivos ou pastas para esta janela. Áudio de vídeo disponível (FFmpeg).':'Arraste arquivos ou pastas para esta janela. Vídeo exige FFmpeg e ffprobe.';}
    }
    function close(){closed=true;if(timer)clearInterval(timer);}
    return {initialize,refresh,select,batch,open,cancel,retry,window:windowSelection,close};
  }
  const exported={create};if(typeof module!=='undefined'&&module.exports)module.exports=exported;root.SoundImports=exported;
})(typeof window==='undefined'?globalThis:window);
