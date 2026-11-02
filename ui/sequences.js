(function(root){
  'use strict';
  const clone=value=>JSON.parse(JSON.stringify(value));
  const content=value=>JSON.stringify({...value,revision:0});
  const uid=()=>root.crypto?.randomUUID?.()||'seq-'+Date.now().toString(36)+'-'+Math.random().toString(36).slice(2);
  const fresh=()=>({schema_version:1,id:uid(),revision:0,name:'Nova sequência',hotkey:'',blocks:[]});
  function locate(blocks,id){
    for(let index=0;index<blocks.length;index++){
      const block=blocks[index];
      if(block.id===id)return {list:blocks,index,block};
      for(const branch of block.branches||[]){const found=locate(branch,id);if(found)return found;}
    }
    return null;
  }
  function renew(block){block.id=uid();for(const branch of block.branches||[])for(const child of branch)renew(child);return block;}
  function createModel(initial=fresh()){
    let current=clone(initial),persisted=clone(initial),past=[],future=[];
    function target(document,parent,branch){
      if(!parent)return document.blocks;
      const found=locate(document.blocks,parent);
      if(!found||found.block.type!=='parallel'||!found.block.branches[branch])throw Error('Ramo não encontrado');
      return found.block.branches[branch];
    }
    function command(action,payload={}){
      const next=clone(current),found=payload.id?locate(next.blocks,payload.id):null;
      if(['patch','duplicate','remove','move','reference','relocate','branch'].includes(action)&&!found)throw Error('Ocorrência não encontrada');
      if(action==='metadata'){
        if(payload.name!==undefined)next.name=String(payload.name);
        if(payload.hotkey!==undefined)next.hotkey=String(payload.hotkey);
      }else if(action==='add'){
        if(payload.parent&&['manual','parallel'].includes(payload.block.type))throw Error('Ramos aceitam apenas clipes e esperas');
        target(next,payload.parent,payload.branch).push(clone(payload.block));
      }else if(action==='patch'){
        const allowed=found.block.type==='play'?['volume','interval']:found.block.type==='wait'?['ms']:[];
        if(Object.keys(payload.changes).some(key=>!allowed.includes(key)))throw Error('Campo local inválido');
        Object.assign(found.block,clone(payload.changes));
      }else if(action==='duplicate')found.list.splice(found.index+1,0,renew(clone(found.block)));
      else if(action==='remove')found.list.splice(found.index,1);
      else if(action==='move'){
        const index=found.index+payload.direction;
        if(index<0||index>=found.list.length)return;
        found.list.splice(found.index,1);found.list.splice(index,0,found.block);
      }else if(action==='reference'){
        const replacement=payload.occurrence;
        found.block.revision_id=replacement.revision_id;
        found.block.clip_id=replacement.clip_id;
        found.block.interval=clone(replacement.interval);
      }else if(action==='relocate'){
        if(payload.parent&&['manual','parallel'].includes(found.block.type))throw Error('Ramos aceitam apenas clipes e esperas');
        const destination=target(next,payload.parent,payload.branch);
        found.list.splice(found.index,1);destination.push(found.block);
      }else if(action==='branch'){
        if(found.block.type!=='parallel')throw Error('Grupo não encontrado');
        if(payload.remove!==undefined){
          if(found.block.branches.length<=2)throw Error('Mantenha ao menos dois ramos');
          if(found.block.branches[payload.remove].length)throw Error('Mova ou remova os blocos antes de excluir o ramo');
          found.block.branches.splice(payload.remove,1);
        }else found.block.branches.push([]);
      }else throw Error('Operação desconhecida');
      past.push(current);future=[];current=next;
    }
    return {value:()=>clone(current),command,
      undo(){if(past.length){future.push(current);current={...past.pop(),revision:current.revision};}},
      redo(){if(future.length){past.push(current);current={...future.pop(),revision:current.revision};}},
      canUndo:()=>!!past.length,canRedo:()=>!!future.length,isDirty:()=>content(current)!==content(persisted),
      saved(document){
        if(document.id!==current.id||document.revision<current.revision)throw Error('Resposta de salvamento obsoleta');
        persisted=clone(document);current={...current,revision:document.revision};
      },
      load(document){current=clone(document);persisted=clone(document);past=[];future=[];}};
  }
  function durationLabel(details){
    if(!details||!Number.isFinite(details.known_duration_ms))return 'Adicione blocos válidos para calcular a duração';
    const seconds=(details.known_duration_ms/1000).toFixed(2)+' s';
    return details.manual_count?seconds+' calculáveis + '+details.manual_count+' acionamento(s) manual(is)':seconds;
  }
  const stateLabels={idle:'Pronta',preparing:'Preparando todos os sons…',playing:'Tocando',waiting:'Esperando',manual:'Aguardando você',completed:'Concluída',cancelled:'Parada',failed:'Falha'};
  const typeLabels={play:'Tocar',wait:'Esperar',parallel:'Paralelo',manual:'Aguardar acionamento'};
  function create(doc,bridge){
    const el=id=>doc.getElementById(id),node=(tag,text)=>{const n=doc.createElement(tag);if(text!==undefined)n.textContent=text;return n;};
    let model=createModel(),library=[],sequences=[],capabilities={},status={state:'idle'},opened=false,timer=null,validation=0,initialized=false,selectionTicket=0,refreshTicket=0;
    const drafts=new Map(),saving=new WeakSet(),blockButtons=new Map();
    let selectedId=null;
    const message=text=>{el('sequenceMessage').textContent=text||'';};
    async function checked(promise){const result=await promise;if(!result?.ok)throw Error(result?.error||'A ação não está disponível neste estado');return result;}
    function button(label,run,action){
      const b=node('button',label);b.type='button';b.className='btn btn-xs btn-secondary';if(action)b.dataset.action=action;
      b.onclick=async()=>{b.disabled=true;try{await run();}catch(error){message(error.message||String(error));}finally{if(b.isConnected)b.disabled=false;}};return b;
    }
    function field(label,value,run,options={}){
      const wrap=node('label',label),input=node('input');input.value=value;input.type=options.type||'number';
      if(options.type!=='text'){input.step=options.step||'0.01';input.min=options.min??0;if(options.max!==undefined)input.max=options.max;}
      input.setAttribute('aria-label',label);input.dataset.field=options.key||label;
      input.onchange=()=>{try{run(input.type==='number'?Number(input.value):input.value);}catch(error){message(error.message);}};
      wrap.append(input);return wrap;
    }
    function mutate(action,payload,target=model){
      target.command(action,payload);drafts.set(target.value().id,target);
      if(model===target){
        const restoreFocus=opened&&doc.activeElement?.tagName==='BUTTON';
        if(action==='add')selectedId=payload.block.id;
        if(action==='duplicate'){const original=locate(target.value().blocks,payload.id);selectedId=original.list[original.index+1].id;}
        render();if(restoreFocus)blockButtons.get(selectedId)?.focus();message('Alterações locais — Ctrl+S para salvar.');
      }
    }
    async function validate(){
      const ticket=++validation,result=await bridge().validate_sequence(model.value());
      if(ticket!==validation)return;
      el('sequenceDuration').textContent=result.ok?durationLabel(result):result.error;
    }
    async function add(kind,parent,branch){
      const target=model;
      let block={id:uid(),type:kind};
      if(kind==='play'){
        const clipId=el('sequenceClipSelect').value;
        if(!clipId)throw Error('Escolha um clipe na biblioteca');
        block=(await checked(bridge().pin_sequence_clip(clipId))).occurrence;
      }else if(kind==='wait')block.ms=300;
      else if(kind==='parallel')block.branches=[[],[]];
      mutate('add',{block,parent,branch},target);
    }
    async function addClip(clipId,parent,branch){
      const target=model;
      const result=await checked(bridge().pin_sequence_clip(clipId));
      mutate('add',{block:result.occurrence,parent,branch},target);
    }
    function blockLabel(block){
      const clip=library.find(item=>item.id===block.clip_id);
      return block.type==='play'?(clip?.title||'Clipe indisponível'):typeLabels[block.type];
    }
    function ordered(blocks=model.value().blocks){
      return blocks.flatMap(block=>[block,...(block.branches||[]).flatMap(items=>ordered(items))]);
    }
    function selectBlock(id,focus=false){
      if(!locate(model.value().blocks,id))return;
      selectedId=id;render();
      if(focus){const item=blockButtons.get(id);item?.focus();item?.scrollIntoView?.({block:'nearest'});}
    }
    function summary(block){
      if(block.type==='play')return ((block.interval[1]-block.interval[0])/48000).toFixed(2)+' s · '+Math.round(block.volume*100)+'%';
      if(block.type==='wait')return block.ms+' ms';
      if(block.type==='parallel')return block.branches.length+' ramos simultâneos';
      return 'Avançar para continuar';
    }
    function adders(parent,branch){
      const bar=node('div');bar.className='sequence-adders';
      bar.append(button('+ Clipe',()=>add('play',parent,branch),'add-play'),button('+ Espera',()=>add('wait',parent,branch),'add-wait'));
      if(!parent)bar.append(button('+ Paralelo',()=>add('parallel'),'add-parallel'),button('+ Acionamento',()=>add('manual'),'add-manual'));
      return bar;
    }
    function renderList(blocks,parent,branch){
      const list=node('div');list.className='sequence-block-list';list.dataset.parent=parent||'';if(branch!==undefined)list.dataset.branch=branch;
      list.ondragover=event=>{event.preventDefault();event.stopPropagation();};
      list.ondrop=async event=>{
        event.preventDefault();event.stopPropagation();
        try{const blockId=event.dataTransfer.getData('application/x-sequence-block'),clipId=event.dataTransfer.getData('application/x-soundboard-clip');
          if(blockId)mutate('relocate',{id:blockId,parent,branch});else if(clipId)await addClip(clipId,parent,branch);
        }catch(error){message(error.message);}
      };
      if(!blocks.length){const empty=node('p',parent?'Arraste um clipe ou adicione abaixo.':'Seu roteiro começa aqui. Adicione um clipe da biblioteca ou use os botões acima.');empty.className='sequence-empty';list.append(empty);}
      blocks.forEach((block,index)=>{
        const card=node('article');card.className='sequence-block sequence-row';card.dataset.blockId=block.id;card.draggable=true;
        card.classList.toggle('sequence-selected',selectedId===block.id);
        card.ondragstart=event=>{event.stopPropagation();event.dataTransfer.setData('application/x-sequence-block',block.id);};
        const choose=button('',()=>selectBlock(block.id,true),'select-block');choose.className='sequence-select';
        choose.setAttribute('aria-pressed',String(selectedId===block.id));choose.setAttribute('aria-label',(index+1)+'. '+blockLabel(block)+'. '+summary(block));
        choose.tabIndex=selectedId===block.id?0:-1;
        const number=node('span',String(index+1));number.className='sequence-order';
        const text=node('span');text.className='sequence-row-copy';text.append(node('strong',blockLabel(block)),node('small',summary(block)));
        choose.append(number,text);blockButtons.set(block.id,choose);card.append(choose);
        if(block.type==='play'){
          const clip=library.find(item=>item.id===block.clip_id);
          if(clip?.revision_id&&clip.revision_id!==block.revision_id){const badge=node('span','Revisão anterior');badge.className='sequence-revision-badge';badge.title='Selecione o bloco para atualizar a referência, se desejar.';card.append(badge);}
        }
        if(block.type==='parallel'){
          const branches=node('div');branches.className='sequence-branches';
          block.branches.forEach((items,i)=>{const panel=node('section');panel.className='sequence-branch';panel.dataset.branchIndex=i;
            const title=node('div','Ramo '+(i+1));title.className='sequence-branch-title';
            if(block.branches.length>2&&!items.length)title.append(button('Remover ramo vazio',()=>mutate('branch',{id:block.id,remove:i}),'remove-branch'));
            panel.append(title,renderList(items,block.id,i),adders(block.id,i));branches.append(panel);
          });card.append(branches);
        }
        list.append(card);
      });return list;
    }
    function renderInspector(){
      const panel=node('aside');panel.className='sequence-inspector';panel.setAttribute('aria-label','Propriedades do bloco selecionado');
      const found=selectedId&&locate(model.value().blocks,selectedId);
      if(!found){panel.append(node('h3','Bloco selecionado'),node('p','Selecione um bloco para ajustar corte, volume ou duração.'));return panel;}
      const block=found.block;
      panel.append(node('small',typeLabels[block.type]),node('h3',blockLabel(block)));
      const actions=node('div');actions.className='sequence-inspector-actions';
      const up=button('↑ Mover',()=>mutate('move',{id:block.id,direction:-1}),'up'),down=button('↓ Mover',()=>mutate('move',{id:block.id,direction:1}),'down');
      up.disabled=found.index===0;down.disabled=found.index===found.list.length-1;up.title='Alt+↑';down.title='Alt+↓';
      const duplicate=button('Duplicar',()=>mutate('duplicate',{id:block.id}),'duplicate');duplicate.title='Ctrl+D';
      const remove=button('Remover',()=>mutate('remove',{id:block.id}),'remove');remove.title='Delete';
      actions.append(up,down,duplicate,remove);panel.append(actions);
      if(block.type==='play'){
        const controls=node('div');controls.className='sequence-fields';
        controls.append(field('Início (s)',block.interval[0]/48000,value=>mutate('patch',{id:block.id,changes:{interval:[Math.round(value*48000),block.interval[1]]}}),{key:'start'}),
          field('Fim (s)',block.interval[1]/48000,value=>mutate('patch',{id:block.id,changes:{interval:[block.interval[0],Math.round(value*48000)]}}),{key:'end'}),
          field('Volume',block.volume,value=>mutate('patch',{id:block.id,changes:{volume:value}}),{max:3,key:'volume'}));
        panel.append(controls,node('p','Corte desta ocorrência. O clipe original permanece intacto.'),button('▶ Ouvir bloco no monitor',()=>preview(block.id),'preview'));
        const reference=node('div');reference.className='sequence-reference';reference.append(node('small','Revisão fixada · '+block.revision_id.slice(0,8)));
        const clip=library.find(item=>item.id===block.clip_id);
        if(clip?.revision_id&&clip.revision_id!==block.revision_id){
          reference.append(node('p','Há uma revisão mais recente. Atualizar reinicia o corte e mantém o volume.'),button('Atualizar referência',async()=>{
            const target=model,result=await checked(bridge().pin_sequence_clip(block.clip_id));
            mutate('reference',{id:block.id,occurrence:result.occurrence},target);
            if(model===target)message('Referência atualizada; corte reiniciado para a nova revisão. Volume mantido.');
          },'reference'));
        }panel.append(reference);
      }else if(block.type==='wait')panel.append(field('Espera (ms)',block.ms,value=>mutate('patch',{id:block.id,changes:{ms:value}}),{step:'1',key:'ms'}),node('p','Pausa antes do próximo bloco.'),button('▶ Ouvir bloco no monitor',()=>preview(block.id),'preview'));
      else if(block.type==='manual')panel.append(node('p','A sequência espera até você clicar Avançar ou pressionar novamente seu atalho global.'),button('▶ Testar no monitor',()=>preview(block.id),'preview'));
      else if(block.type==='parallel')panel.append(node('p','Os ramos tocam juntos. A sequência continua quando todos terminam.'),button('+ Adicionar ramo',()=>mutate('branch',{id:block.id}),'branch'),button('▶ Ouvir grupo no monitor',()=>preview(block.id),'preview'));
      return panel;
    }
    async function handleKeydown(event){
      if(!opened||event.isComposing||event.defaultPrevented)return;
      const tag=event.target?.tagName,typing=['INPUT','TEXTAREA','SELECT'].includes(tag)||event.target?.isContentEditable;
      const command=event.ctrlKey||event.metaKey,key=event.key.toLowerCase();
      if(event.repeat&&tag==='BUTTON'&&(key==='enter'||key===' ')){event.preventDefault();return;}
      let run=null;
      if(key==='escape'&&!event.ctrlKey&&!event.metaKey&&!event.altKey)run=close;
      else if(command&&!event.altKey&&!event.shiftKey&&key==='s')run=()=>{if(typing)event.target.onchange?.({target:event.target});return save();};
      else if(!typing){
        if(command&&!event.altKey&&key==='z')run=()=>{event.shiftKey?model.redo():model.undo();render();};
        else if(command&&!event.altKey&&!event.shiftKey&&key==='y')run=()=>{model.redo();render();};
        else if(command&&!event.altKey&&!event.shiftKey&&key==='d'&&selectedId)run=()=>mutate('duplicate',{id:selectedId});
        else if(!command&&!event.shiftKey&&event.altKey&&['arrowup','arrowdown'].includes(key)&&selectedId)run=()=>mutate('move',{id:selectedId,direction:key==='arrowup'?-1:1});
        else if(!command&&!event.altKey&&!event.shiftKey&&key==='delete'&&selectedId)run=()=>mutate('remove',{id:selectedId});
        else if(!command&&!event.altKey&&!event.shiftKey&&['arrowup','arrowdown','home','end'].includes(key))run=()=>{
          const items=ordered(),index=items.findIndex(block=>block.id===selectedId);
          const next=key==='home'?0:key==='end'?items.length-1:Math.max(0,Math.min(items.length-1,index+(key==='arrowup'?-1:1)));
          if(items[next])selectBlock(items[next].id,true);
        };
      }
      if(!run)return;event.preventDefault();event.stopPropagation();if(event.repeat&&command)return;
      try{await run();if(!typing&&opened)blockButtons.get(selectedId)?.focus();}catch(error){message(error.message||String(error));}
    }
    function render(){
      const document=model.value();el('sequenceName').value=document.name;el('sequenceHotkey').value=document.hotkey||'';
      if(!selectedId||!locate(document.blocks,selectedId))selectedId=document.blocks[0]?.id||null;
      blockButtons.clear();
      const toolbar=adders(),hint=node('p','↑↓ selecionar · Alt+↑↓ mover · Ctrl+D duplicar · Delete remover · Ctrl+Z/Y desfazer/refazer · Ctrl+S salvar');
      hint.className='sequence-keyboard-hint';
      const composition=node('div');composition.className='sequence-composition';composition.append(renderList(document.blocks),renderInspector());
      el('sequenceBlocks').replaceChildren(toolbar,hint,composition);
      el('btnSequenceUndo').disabled=!model.canUndo();el('btnSequenceRedo').disabled=!model.canRedo();
      el('btnSequenceDelete').disabled=!sequences.some(item=>item.document.id===document.id);
      el('btnSequenceDiscardReload').classList.toggle('hidden',!model.isDirty());
      el('sequenceCapability').textContent=capabilities.groups?'Prévia: somente monitor. Iniciar: saídas configuradas.':capabilities.unavailable_reason||'Sequências exigem o backend mixer.';
      for(const id of ['btnSequenceStart','btnSequenceRestart','btnSequencePreview'])el(id).disabled=!capabilities.groups||saving.has(model);
      el('btnSequenceSave').disabled=saving.has(model);
      validate().catch(error=>message(error.message));paintStatus();
    }
    function paintStatus(){
      const playingName=status.document?.name||'',next=status.next_block?blockLabel(status.next_block):'fim';
      el('sequenceStatus').textContent=(stateLabels[status.state]||status.state)+(playingName?' · '+playingName:'')+(status.monitor_only?' · MONITOR':'')+(status.error?' · '+status.error:'');
      el('sequenceNext').textContent=status.current_block?'Atual: '+(locate(status.document.blocks,status.current_block)?.block?blockLabel(locate(status.document.blocks,status.current_block).block):status.current_block)+' · Próximo: '+next:'';
      el('sequenceBranchesStatus').textContent=(status.branches||[]).map(branch=>'Ramo '+(branch.index+1)+': '+(branch.state==='completed'?'concluído':branch.block_id?blockLabel(locate(status.document.blocks,branch.block_id).block):branch.state)).join(' · ');
      el('sequenceLiveStatus').textContent=(stateLabels[status.state]||'Pronta')+(playingName?' · '+playingName:'');
      el('btnSequenceAdvance').disabled=status.state!=='manual';
      for(const card of doc.querySelectorAll('#sequenceBlocks [data-block-id]'))card.classList.toggle('sequence-active',status.sequence_id===model.value().id&&(status.active_blocks||[]).includes(card.dataset.blockId));
    }
    async function refreshStatus(){
      try{const result=await bridge().get_sequence_status();if(result.ok){status=result.status;paintStatus();}}
      catch(error){if(opened)message(error.message);}
      timer=setTimeout(refreshStatus,300);
    }
    function renderChoices(){
      const select=el('sequenceClipSelect'),selected=select.value;select.replaceChildren();
      select.append(node('option','Escolha um clipe…'));select.firstChild.value='';
      for(const clip of library){const option=node('option',clip.title||clip.id);option.value=clip.id;select.append(option);}select.value=selected;
      const list=el('sequenceLibrary');list.replaceChildren();
      for(const clip of library){const item=button(clip.title||clip.id,()=>addClip(clip.id),'library-clip');item.draggable=true;item.dataset.clipId=clip.id;
        item.title='Clique para adicionar; arraste para um ramo';item.ondragstart=event=>event.dataTransfer.setData('application/x-soundboard-clip',clip.id);list.append(item);}
      const saved=el('sequenceSaved'),pads=el('sequencePads');saved.replaceChildren();pads.replaceChildren();
      for(const item of sequences){const document=item.document;
        saved.append(button(document.name,()=>load(document),'open-sequence'));
        const pad=node('div');pad.className='sequence-pad';pad.dataset.sequenceId=document.id;
        pad.append(button('▶ '+document.name+(document.hotkey?' ['+document.hotkey+']':''),async()=>{const result=await checked(bridge().start_sequence(document.id));status=result.status;paintStatus();},'start-sequence'),button('Editar',()=>open(document.id),'edit-sequence'));pads.append(pad);
      }
    }
    async function refreshLibrary(){
      const ticket=++refreshTicket,result=await checked(bridge().get_sequences());
      if(ticket===refreshTicket){library=result.clips;sequences=result.sequences;capabilities=result.capabilities;status=result.status;renderChoices();}
      return result;
    }
    async function reload({discardLocal=false}={}){
      const target=model,before=target.value();
      if(saving.has(target))throw Error('Aguarde o salvamento desta sequência antes de recarregar');
      if(target.isDirty()&&!discardLocal){render();throw Error('Há alterações locais. Use “Descartar alterações e recarregar” para substituí-las pelo documento salvo.');}
      const result=await refreshLibrary(),saved=result.sequences.find(item=>item.document.id===before.id);
      if(saving.has(target)||JSON.stringify(target.value())!==JSON.stringify(before))throw Error('A edição mudou durante a recarga; alterações locais preservadas.');
      if(!saved)throw Error('Esta sequência não está salva; não há documento para recarregar.');
      target.load(saved.document);drafts.set(before.id,target);
      if(model===target){render();message('Documento salvo recarregado; histórico local reiniciado.');}
      return result;
    }
    function load(document){
      selectionTicket++;drafts.set(model.value().id,model);model=drafts.get(document.id)||createModel(document);selectedId=null;
      if(!model.isDirty()&&!saving.has(model)&&model.value().revision!==document.revision)model.load(document);
      render();message(model.isDirty()&&model.value().revision!==document.revision?'Há uma versão salva mais recente. Edições locais preservadas; recarregue explicitamente para resolver o conflito.':'');
    }
    async function save(target=model){
      if(saving.has(target))throw Error('Já há um salvamento desta sequência em andamento.');
      const document=target.value();saving.add(target);
      try{
        if(model===target)render();
        const result=await checked(bridge().save_sequence(document));
        target.saved(result.document);drafts.set(document.id,target);
        await refreshLibrary();
        if(model===target)message(target.isDirty()?'Versão enviada salva; alterações feitas durante o salvamento continuam locais.':'Sequência salva. Execuções já iniciadas mantêm sua versão.');
        return result;
      }finally{
        saving.delete(target);if(model===target)render();
      }
    }
    async function start(replace=false){
      const target=model,saved=await save(target);
      const result=await checked(bridge().start_sequence(saved.document.id,replace));status=result.status;paintStatus();
    }
    async function preview(blockId=null){
      const result=await checked(bridge().preview_sequence(model.value(),blockId));status=result.status;paintStatus();
    }
    async function open(sequenceId){
      const ticket=++selectionTicket,result=await refreshLibrary();
      if(ticket!==selectionTicket)return;
      opened=true;el('sequencePanel').classList.remove('hidden');
      if(sequenceId){const item=result.sequences.find(item=>item.document.id===sequenceId);if(item)load(item.document);}render();el('sequenceName').focus();
    }
    function close(){selectionTicket++;opened=false;el('sequencePanel').classList.add('hidden');el('btnSequences').focus();}
    async function initialize(){
      if(initialized)return;initialized=true;
      const bind=(id,action)=>{el(id).onclick=async()=>{try{await action();}catch(error){message(error.message);}};};
      bind('btnSequences',()=>open());bind('btnCloseSequences',close);
      bind('btnSequenceNew',()=>{load(fresh());message('Adicione clipes, esperas, ramos e acionamentos.');});
      bind('btnSequenceSave',save);bind('btnSequenceStart',()=>start());bind('btnSequenceRestart',()=>start(true));
      bind('btnSequencePreview',()=>preview());bind('btnSequenceAdvance',async()=>{const result=await checked(bridge().advance_sequence(status.execution_id,false));status=result.status;paintStatus();});
      bind('btnSequenceStop',async()=>{const result=await checked(bridge().stop_sequence(status.execution_id));status=result.status;paintStatus();});
      bind('btnSequenceUndo',()=>{model.undo();render();});bind('btnSequenceRedo',()=>{model.redo();render();});
      bind('btnSequenceDelete',async()=>{
        const target=model,id=target.value().id;
        if(saving.has(target))throw Error('Aguarde o salvamento antes de excluir esta sequência.');
        await checked(bridge().delete_sequence(id));drafts.delete(id);
        if(model===target){selectionTicket++;model=createModel();}
        await refreshLibrary();render();message('Sequência removida; clipes preservados.');
      });
      bind('btnSequenceRefreshLibrary',async()=>{await refreshLibrary();render();});
      bind('btnSequenceReload',()=>reload());
      bind('btnSequenceDiscardReload',()=>reload({discardLocal:true}));
      el('sequenceName').onchange=event=>mutate('metadata',{name:event.target.value});
      el('sequenceHotkey').onchange=event=>mutate('metadata',{hotkey:event.target.value.trim().toLowerCase()});
      el('sequencePanel').addEventListener('keydown',handleKeydown);
      await refreshLibrary();render();refreshStatus();
    }
    return {initialize,open,close,reload,refreshLibrary,render,save,start,preview,select:selectBlock,selection:()=>selectedId,handleKeydown,model:()=>model,status:()=>clone(status),destroy(){clearTimeout(timer);}};
  }
  const exported={createModel,create,durationLabel,fresh};
  if(typeof module!=='undefined'&&module.exports)module.exports=exported;
  root.SoundSequences=exported;
})(typeof globalThis!=='undefined'?globalThis:this);
