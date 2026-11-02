const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs');
function contract(){assert.ok(fs.existsSync('ui/imports.js'),'Fila visual ausente');return require('../ui/imports.js');}
function document(){
 let dom;
 class E{constructor(tag='div'){this.tagName=tag.toUpperCase();this.children=[];this.dataset={};this.value='';this.checked=false;this.textContent='';this.classList={add(){},remove(){},toggle(){}};this.listeners={};}
  contains(node){return this===node||this.children.some(child=>child.contains(node));}
  append(...x){for(const child of x){if(child.parentNode)child.remove();child.parentNode=this;this.children.push(child);}}
  appendChild(x){this.append(x);}replaceChildren(...x){for(const child of [...this.children])child.remove();this.append(...x);}
  remove(){if(this.contains(dom.activeElement))dom.activeElement=null;if(this.parentNode){this.parentNode.children=this.parentNode.children.filter(child=>child!==this);this.parentNode=null;}}
  replaceWith(node){const parent=this.parentNode,index=parent.children.indexOf(this);this.remove();parent.children.splice(index,0,node);node.parentNode=parent;}
  focus(){dom.activeElement=this;}addEventListener(n,f){this.listeners[n]=f;}setAttribute(){} }
 const es=new Map();dom={getElementById(id){if(!es.has(id))es.set(id,new E());return es.get(id);},createElement(tag){return new E(tag);},activeElement:null};return dom;
}
test('fila visível encaminha janela, cancelamento, retry e abertura do editor pelo ID',async()=>{
 const {create}=contract(),dom=document(),calls=[],opened=[];
 const bridge={list_import_jobs:async()=>({ok:true,jobs:[{id:'j',source:'C:/a.wav',status:'awaiting_window',duration:500,history:[],warnings:[],progress:10}]}),
  set_import_window:async(...args)=>{calls.push(['window',...args]);return {ok:true};},cancel_import_job:async(id)=>{calls.push(['cancel',id]);return {ok:true};},
  retry_import_job:async(id)=>{calls.push(['retry',id]);return {ok:true};}};
 const ui=create(dom,()=>bridge,{open:clip=>opened.push(clip)});await ui.refresh();
 assert.equal(dom.getElementById('importQueueItems').children.length,1);
 await ui.window('j',5,15);await ui.cancel('j');await ui.retry('j');ui.open('c');
 assert.deepEqual(calls,[['window','j',5,15],['cancel','j'],['retry','j']]);assert.deepEqual(opened,[{id:'c'}]);
});
test('metadados em lote usam somente seleção explícita e mostram conflitos',async()=>{
 const {create}=contract(),dom=document(),calls=[];
 const bridge={list_import_jobs:async()=>({ok:true,jobs:[]}),apply_import_metadata:async(...args)=>{calls.push(args);return {ok:true,conflicts:[{clip_id:'c',field:'category'}]};}};
 const ui=create(dom,()=>bridge);ui.select('j',true);dom.getElementById('importBatchCategory').value='Meme';
 await ui.batch();assert.deepEqual(calls,[[['j'],{category:'Meme'}]]);assert.match(dom.getElementById('importQueueMessage').textContent,/1.*preservad/);
});
test('todas entradas UI usam fila e packs têm modos explícitos e raízes selecionáveis',()=>{
 contract();const js=fs.readFileSync('ui/app.js','utf8'),html=fs.readFileSync('ui/index.html','utf8');
 assert.match(js,/enqueue_import/);for(const id of ['importQueue','importQueueItems','importAutoAnalyze','packMode','packCollection','packSequence']) assert.ok(html.includes(`id="${id}"`),id);
 assert.match(js,/export_pack\(null, null,.*packMode/);assert.ok(!fs.readFileSync('ui/imports.js','utf8').includes('file.name'));
});
test('fila oferece admissão por arquivos ou pasta mesmo sem botões do cabeçalho',async()=>{
 const {create}=contract(),dom=document(),calls=[];
 const ui=create(dom,()=>({list_import_jobs:async()=>({ok:true,jobs:[]}),enqueue_import:async(...args)=>{calls.push(args);return {ok:true,jobs:[]};}}));
 dom.getElementById('importAutoAnalyze').checked=true;dom.getElementById('importQueueMode').value='vinheta';
 try{await ui.initialize();assert.equal(typeof dom.getElementById('btnQueueAddFiles').onclick,'function');
  await dom.getElementById('btnQueueAddFiles').onclick();await dom.getElementById('btnQueueAddFolder').onclick();
  assert.deepEqual(calls,[[null,true,'Vinhetas',false,true],[null,true,'Vinhetas',true,true]]);
 }finally{ui.close();}
});
test('progresso de outro item preserva foco, digitação parcial, seleção e histórico aberto',async()=>{
 const {create}=contract(),dom=document();
 let jobs=[{id:'window',source:'long.wav',status:'awaiting_window',duration:500,progress:10,history:[]},
  {id:'worker',source:'other.wav',status:'running',progress:20,history:[]}];
 const ui=create(dom,()=>({list_import_jobs:async()=>({ok:true,jobs:structuredClone(jobs)})}));
 await ui.refresh();
 const list=dom.getElementById('importQueueItems'),row=list.children[0],other=list.children[1];
 const start=row.children[2].children.find(child=>child.tagName==='INPUT');
 const check=row.children[0].children[0];check.checked=true;check.onchange();
 const history=row.children.find(child=>child.tagName==='DETAILS');history.open=true;
 const otherHistory=other.children.find(child=>child.tagName==='DETAILS');otherHistory.open=true;
 start.value='1.';start.oninput();start.selectionStart=1;start.selectionEnd=2;start.focus();
 jobs[1].progress=80;await ui.refresh();
 assert.equal(list.children[0],row,'Linha inalterada deve continuar no DOM');
 assert.equal(dom.activeElement,start);assert.equal(start.value,'1.');assert.deepEqual([start.selectionStart,start.selectionEnd],[1,2]);
 assert.equal(check.checked,true);assert.equal(history.open,true);
 assert.equal(list.children[1].children.find(child=>child.tagName==='DETAILS').open,true,'Histórico do item atualizado permanece aberto');
 // Mesmo uma atualização externa deste item não desfaz a digitação em curso.
 jobs[0].metadata={category:'Meme'};await ui.refresh();assert.equal(dom.activeElement,start);assert.equal(start.value,'1.');
 dom.activeElement=null;await ui.refresh();assert.equal(list.children[0].children.find(child=>child.tagName==='DETAILS').open,true);
});
test('popover Pack permanece dentro da viewport com botão à esquerda e janela estreita',()=>{
 const source=fs.readFileSync('ui/app.js','utf8'),definition=source.match(/function positionPackPopover\(\) \{[\s\S]*?\n}\n/);
 assert.ok(definition,'Posicionamento por viewport ausente');
 for(const width of [768,360]){
  const menu={style:{},getBoundingClientRect:()=>({height:480})},button={getBoundingClientRect:()=>({left:20,right:90,bottom:140})};
  const context={window:{innerWidth:width,innerHeight:700},document:{getElementById:id=>id==='packPopover'?menu:button}};
  require('node:vm').runInNewContext(`${definition[0]}positionPackPopover();`,context);
  assert.equal(menu.style.position,'fixed');const left=parseFloat(menu.style.left),top=parseFloat(menu.style.top),w=parseFloat(menu.style.width);
  assert.ok(left>=8&&left+w<=width-8);assert.ok(top>=8&&top+480<=692);
 }
});
