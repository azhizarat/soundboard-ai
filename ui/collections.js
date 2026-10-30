(function(root){
  'use strict';
  const clone=value=>JSON.parse(JSON.stringify(value));
  const uuid=()=>globalThis.crypto?.randomUUID?.() || `pad-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  const fresh=()=>({schema_version:1,id:uuid(),name:'Nova coleção',revision:0,columns:4,pads:[]});
  function layout(document){
    const slots=Array(Math.max(document.columns*2,...document.pads.map(p=>p.position+1))).fill(null);
    document.pads.forEach(p=>slots[p.position]=clone(p));return slots;
  }
  function createModel(document){
    let current=clone(document);
    return {value:()=>clone(current), metadata(changes){Object.assign(current,clone(changes));},
      add(target){const used=new Set(current.pads.map(p=>p.position));let position=0;while(used.has(position))position++;
        current.pads.push({id:uuid(),position,label:'Novo botão',target:clone(target),hotkey:'',group:'effect',retrigger:'restart',hold:false,loop:false,fade_ms:250});},
      patch(id,changes){const pad=current.pads.find(p=>p.id===id);if(!pad)throw Error('Botão não encontrado');Object.assign(pad,clone(changes));},
      remove(id){current.pads=current.pads.filter(p=>p.id!==id);},saved(document){current=clone(document);}};
  }
  function create(dom,bridge){
    const $=id=>dom.getElementById(id), held=new Map(), choiceLabels=new Map();
    let snapshot={collections:[],clips:[],sequences:[],conflicts:[]}, model=createModel(fresh()), generation=0, timer=null, closed=false, pollBusy=false;
    const feedback=text=>{$('collectionMessage').textContent=text||'';};
    const check=result=>{if(result?.ok===false)throw Error(result.error||'Não foi possível concluir');return result;};
    const action=fn=>async event=>{try{await fn(event);}catch(error){feedback(error.message);}};
    function element(tag,text,cls){const el=dom.createElement(tag);if(text!==undefined)el.textContent=text;if(cls)el.className=cls;return el;}
    function button(text,handler,cls='btn'){const el=element('button',text,cls);el.type='button';el.addEventListener('click',action(handler));return el;}
    function options(select,items,value){select.replaceChildren();items.forEach(([v,label])=>{const option=element('option',label);option.value=v;select.append(option);});select.value=String(value??'');}
    function select(id){const doc=snapshot.collections.find(c=>c.id===id);if(!doc)return;generation++;model=createModel(doc);renderEditor();}
    function renderSelectors(){
      options($('collectionSelect'),[['','Selecione uma coleção'],...snapshot.collections.map(c=>[c.id,c.name])],snapshot.active_collection_id);
      options($('collectionEditSelect'),[['','Nova coleção'],...snapshot.collections.map(c=>[c.id,c.name])],model.value().id);
    }
    function renderPads(){
      choiceLabels.clear();
      const doc=snapshot.collections.find(c=>c.id===snapshot.active_collection_id), area=$('collectionPads');area.replaceChildren();
      if(!doc){area.append(element('p','Crie uma coleção em Preparar e selecione-a para tocar.','collection-empty'));return;}
      area.style.gridTemplateColumns=`repeat(${doc.columns}, minmax(0, 1fr))`;
      layout(doc).forEach((pad,index)=>{
        if(!pad){const empty=element('div',String(index+1),'collection-slot-empty');empty.setAttribute('aria-label',`Posição ${index+1} vazia`);area.append(empty);return;}
        const control=element('button',undefined,'collection-pad');control.type='button';control.dataset.padId=pad.id;
        control.append(element('span',String(pad.position+1),'pad-position'),element('strong',pad.label||'Sem rótulo'),
          element('span',`${({speech:'Fala',track:'Trilha',effect:'Efeito'})[pad.group]}${pad.loop?' · Loop':''}${pad.hold?' · Segurar':''}`,'pad-kind'),element('kbd',pad.hotkey||'Sem atalho'));
        control.setAttribute('aria-label',`${pad.label}. ${pad.hold?'Segure para tocar. ':''}${pad.hotkey||''}`);
        function bindTrigger(button,repeatLast=false){
          const token=`pointer:${doc.id}:${pad.id}:${repeatLast}`;
          button.addEventListener('pointerdown',action(async event=>{if(event.button!==0)return;event.preventDefault();button.setPointerCapture?.(event.pointerId);await press(doc.id,pad.id,token,repeatLast);}));
          for(const name of ['pointerup','pointercancel','lostpointercapture'])button.addEventListener(name,action(()=>release(token)));
          button.addEventListener('keydown',action(async event=>{if(!['Enter',' '].includes(event.key))return;event.preventDefault();if(!event.repeat)await press(doc.id,pad.id,'ui:'+event.code,repeatLast);}));
          button.addEventListener('keyup',action(event=>{if(['Enter',' '].includes(event.key)){event.preventDefault();return release('ui:'+event.code);}}));
          button.addEventListener('click',action(async event=>{if(event.detail===0){const accessToken='accessible:'+uuid();await press(doc.id,pad.id,accessToken,repeatLast);await release(accessToken);}}));
        }
        bindTrigger(control);
        if(pad.target.type==='shuffle'){
          const cell=element('div',undefined,'collection-pad-cell'),chosen=element('span','Nenhuma escolha nesta sessão','shuffle-choice');
          const repeat=element('button','Repetir última escolha','btn');repeat.type='button';repeat.dataset.repeatPadId=pad.id;bindTrigger(repeat,true);
          choiceLabels.set(`${doc.id}:${pad.id}`,chosen);cell.append(control,chosen,repeat);area.append(cell);
        }else area.append(control);
      });
    }
    function field(label,input){const wrapper=element('label',label);wrapper.append(input);return wrapper;}
    function renderEditor(){
      const doc=model.value();$('collectionName').value=doc.name;$('collectionColumns').value=doc.columns;
      renderSelectors();const rows=$('collectionPadEditor');rows.replaceChildren();
      const targets=[...snapshot.clips.filter(c=>!c.pending).map(c=>[`clip:${c.id}`,`Clipe · ${c.title}`]),...snapshot.sequences.map(s=>[`sequence:${s.id}`,`Sequência · ${s.name}`])];
      options($('collectionTarget'),targets,targets[0]?.[0]);
      for(const pad of doc.pads){
        const row=element('div',undefined,'collection-edit-row');row.dataset.padId=pad.id;
        const input=(label,key,type='text')=>{const el=element('input');el.type=type;el.value=pad[key]??0;el.dataset.field=key;if(type==='number'){el.min=key==='position'?1:0;el.max=key==='position'?128:key==='cooldown_ms'?60000:10000;if(key==='position')el.value=pad[key]+1;}
          el.addEventListener('change',()=>model.patch(pad.id,{[key]:type==='number'?Number(el.value)-(key==='position'?1:0):el.value}));return field(label,el);};
        const choice=(label,key,items,value=pad[key])=>{const el=element('select');options(el,items,value);el.addEventListener('change',()=>{if(key==='target'){const split=el.value.indexOf(':');model.patch(pad.id,{target:{type:el.value.slice(0,split),id:el.value.slice(split+1)}});}else model.patch(pad.id,{[key]:el.value});});return field(label,el);};
        row.append(input('Posição','position','number'),input('Rótulo','label'),input('Atalho','hotkey'),
          choice('Grupo','group',[['speech','Falas (substituem falas)'],['track','Trilhas contínuas'],['effect','Efeitos sobrepostos']]),
          choice('Redisparo','retrigger',[['restart','Reiniciar'],['stop','Parar'],['ignore','Ignorar']]),input('Saída suave (ms)','fade_ms','number'));
        if(pad.target.type==='shuffle'){
          row.append(input('Cooldown (ms)','cooldown_ms','number'));
          const members=element('fieldset',undefined,'shuffle-members');members.append(element('legend','Grupo aleatório · marque os membros'));
          for(const [value,label] of targets){const split=value.indexOf(':'),target={type:value.slice(0,split),id:value.slice(split+1)};
            const checkbox=element('input');checkbox.type='checkbox';checkbox.dataset.shuffleTarget=value;
            checkbox.checked=pad.target.items.some(item=>item.type===target.type&&item.id===target.id);
            checkbox.addEventListener('change',()=>{const current=model.value().pads.find(p=>p.id===pad.id),items=current.target.items.filter(item=>item.type!==target.type||item.id!==target.id);if(checkbox.checked)items.push(target);model.patch(pad.id,{target:{type:'shuffle',items}});});
            members.append(field(label,checkbox));}
          row.append(members);
        }else row.append(choice('Referência','target',targets,`${pad.target.type}:${pad.target.id}`));
        for(const [key,label] of [['hold','Segurar para tocar'],['loop','Loop']]){const el=element('input');el.type='checkbox';el.checked=pad[key];el.addEventListener('change',()=>model.patch(pad.id,{[key]:el.checked}));row.append(field(label,el));}
        row.append(button('Remover posição',()=>{model.remove(pad.id);renderEditor();}));rows.append(row);
      }
    }
    async function refresh(){snapshot=check(await bridge().get_collections());renderSelectors();renderPads();
      $('collectionConflicts').textContent=snapshot.conflicts.map(c=>`Conflito ${c.hotkey}: ${c.owners.join(', ')}`).join(' · ');}
    async function save(){
      const target=model, request=target.value(), stamp=generation;
      const result=check(await bridge().save_collection(request));
      const index=snapshot.collections.findIndex(c=>c.id===request.id);if(index<0)snapshot.collections.push(result.document);else snapshot.collections[index]=result.document;
      if(model===target && generation===stamp){
        if(JSON.stringify(target.value())===JSON.stringify(request))target.saved(result.document);
        else target.metadata({revision:result.document.revision});
        renderEditor();feedback('Coleção salva. As posições permanecem fixas.');}
      renderSelectors();renderPads();return result;
    }
    async function press(collectionId,padId,token,repeatLast=false){
      if(held.has(token))return {ok:true,ignored:true};
      const item={collectionId,padId,token:'gesture:'+uuid(),pending:null};held.set(token,item);
      item.pending=Promise.resolve(bridge().trigger_collection_pad(collectionId,padId,'press',false,item.token,repeatLast));
      try{return check(await item.pending);}catch(error){if(held.get(token)===item)held.delete(token);throw error;}
    }
    async function release(token){const item=held.get(token);if(!item)return;held.delete(token);
      return check(await bridge().trigger_collection_pad(item.collectionId,item.padId,'release',false,item.token));}
    async function releaseAll(){await Promise.all([...held.keys()].map(release));}
    async function poll(){
      if(closed||pollBusy)return;pollBusy=true;
      try{
        const status=check(await bridge().get_operation_status());$('rehearsalToggle').checked=!!status.rehearsal;
        const targetName=target=>target?.type==='clip'?snapshot.clips.find(c=>c.id===target.id)?.title:snapshot.sequences.find(s=>s.id===target?.id)?.name;
        for(const [key,label] of choiceLabels){const target=status.shuffle_choices?.[key];label.textContent=target?`Última escolha: ${targetName(target)||target.id}`:'Nenhuma escolha nesta sessão';}
        const route=status.rehearsal?`ENSAIO · Monitor: ${status.monitor_device_name||status.monitor_device_id||'não selecionado'}`:
          `TRANSMISSÃO · Saída: ${status.cable_device_name||status.cable_device_id||'não selecionada'} · Monitor: ${status.monitor_device_name||status.monitor_device_id||'não selecionado'}`;
        const states={idle:'Pronto',playing:'Tocando',waiting:'Em espera',manual:'Aguardando avanço',preparing:'Preparando',completed:'Concluído',cancelled:'Parado',failed:'Falhou'};
        $('operationStatus').textContent=status.route_error||`${route} · ${status.instances.length} disparo(s) de coleção · ${states[status.sequence?.state]||'Pronto'}${status.isolated?'':' · Sem isolamento real entre monitor e transmissão'}`;
        $('operationStatus').classList.toggle('operation-error',!!status.route_error);
        const active=$('operationInstances');active.replaceChildren();
        for(const instance of status.instances){const doc=snapshot.collections.find(c=>c.id===instance.collection_id), pad=doc?.pads.find(p=>p.id===instance.pad_id);
          const row=element('div',undefined,'operation-instance');row.append(element('span',`${pad?.label||instance.pad_id} · ${targetName(instance.chosen_target)||''} · ${states[instance.state]||instance.state}`),
            button('Parar agora',()=>bridge().stop_collection_pad(instance.execution_id,false)),button('Saída suave',()=>bridge().stop_collection_pad(instance.execution_id,true)));active.append(row);}
      }catch(error){$('operationStatus').textContent=error.message;}finally{pollBusy=false;}
    }
    function area(perform){dom.body.classList.toggle('perform-mode',perform);$('btnPerform').setAttribute('aria-pressed',String(perform));$('btnPrepare').setAttribute('aria-pressed',String(!perform));}
    async function initialize(){
      await refresh();if(snapshot.collections.length)select(snapshot.active_collection_id||snapshot.collections[0].id);else renderEditor();
      $('btnPrepare').addEventListener('click',()=>area(false));$('btnPerform').addEventListener('click',()=>{area(true);renderPads();});
      $('btnCollectionEdit').addEventListener('click',()=>{area(false);$('collectionEditor').open=true;});
      $('collectionSelect').addEventListener('change',action(async event=>{const id=event.target.value;check(await bridge().activate_collection(id));snapshot.active_collection_id=id;renderPads();}));
      $('collectionEditSelect').addEventListener('change',event=>select(event.target.value));
      $('btnCollectionNew').addEventListener('click',()=>{generation++;model=createModel(fresh());$('collectionEditor').open=true;renderEditor();});
      $('collectionName').addEventListener('input',event=>model.metadata({name:event.target.value}));
      $('collectionColumns').addEventListener('change',event=>model.metadata({columns:Number(event.target.value)}));
      $('btnCollectionAdd').addEventListener('click',action(()=>{const value=$('collectionTarget').value, split=value.indexOf(':');if(split<0)throw Error('Selecione um clipe ou sequência');model.add({type:value.slice(0,split),id:value.slice(split+1)});renderEditor();}));
      $('btnCollectionShuffle').addEventListener('click',action(()=>{const value=$('collectionTarget').value,split=value.indexOf(':');if(split<0)throw Error('Selecione o primeiro membro');model.add({type:'shuffle',items:[{type:value.slice(0,split),id:value.slice(split+1)}]});const pad=model.value().pads.at(-1);model.patch(pad.id,{label:'Grupo aleatório',cooldown_ms:0});renderEditor();}));
      $('btnCollectionSave').addEventListener('click',action(save));
      $('btnCollectionDelete').addEventListener('click',action(async()=>{const target=model, id=target.value().id;check(await bridge().delete_collection(id));if(model===target){generation++;model=createModel(fresh());}await refresh();renderEditor();feedback('Coleção excluída; a mídia continua na biblioteca.');}));
      $('btnCollectionRefresh').addEventListener('click',action(async()=>{await refresh();renderEditor();}));
      $('rehearsalToggle').addEventListener('change',action(async event=>{try{check(await bridge().set_rehearsal(event.target.checked));}finally{await poll();}}));
      $('btnOperationStop').addEventListener('click',action(async()=>{await bridge().stop_all();await poll();}));
      dom.addEventListener('keyup',action(event=>release('ui:'+event.code)));
      root.addEventListener?.('pointerup',action(()=>releaseAll()));root.addEventListener?.('blur',action(releaseAll));
      await poll();if(root.document===dom)timer=root.setInterval(poll,500);
    }
    return {initialize,refresh,select,save,press,release,model:()=>model,poll,area,close(){closed=true;if(timer)root.clearInterval(timer);return releaseAll();}};
  }
  const exported={createModel,layout,create};if(typeof module!=='undefined')module.exports=exported;root.SoundCollections=exported;
})(typeof window!=='undefined'?window:globalThis);
