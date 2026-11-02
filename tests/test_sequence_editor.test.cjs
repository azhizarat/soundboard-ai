const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const filename=path.join(__dirname,'../ui/sequences.js');
function contract(){assert.ok(fs.existsSync(filename),'Editor de sequências ausente');return require(filename);}
const clip={id:'clip',revision_id:'rev',samples:96000,title:'Pergunta',volume:1};
function doc(){return {schema_version:1,id:'s',revision:1,name:'Diálogo',hotkey:'',blocks:[{id:'a',type:'play',clip_id:'clip',revision_id:'rev',interval:[0,48000],volume:1}]};}

test('duplicar cria identidade própria e ajustes não alteram original, com undo redo',()=>{
  const {createModel}=contract(), original=doc(), model=createModel(original);
  model.command('duplicate',{id:'a'});
  const duplicate=model.value().blocks[1];
  assert.notEqual(duplicate.id,'a');
  model.command('patch',{id:duplicate.id,changes:{volume:.3,interval:[12000,24000]}});
  assert.deepEqual(model.value().blocks[0],original.blocks[0]);
  assert.deepEqual(original,doc());
  model.undo();assert.equal(model.value().blocks[1].volume,1);
  model.redo();assert.equal(model.value().blocks[1].volume,.3);
  model.command('move',{id:duplicate.id,direction:-1});
  assert.equal(model.value().blocks[0].id,duplicate.id);
  model.command('remove',{id:duplicate.id});assert.equal(model.value().blocks.length,1);
});

test('ramos editáveis, cópia profunda e IDs únicos inclusive dentro do grupo',()=>{
  const {createModel}=contract(), model=createModel(doc());
  model.command('add',{block:{id:'p',type:'parallel',branches:[[],[]]}});
  model.command('add',{parent:'p',branch:0,block:{id:'w',type:'wait',ms:300}});
  model.command('add',{parent:'p',branch:1,block:{id:'b',type:'play',clip_id:'clip',revision_id:'rev',interval:[0,48000],volume:1}});
  model.command('duplicate',{id:'p'});
  const p=model.value().blocks[1], q=model.value().blocks[2];
  assert.notEqual(p.id,q.id);assert.notEqual(p.branches[0][0].id,q.branches[0][0].id);
  model.command('patch',{id:q.branches[0][0].id,changes:{ms:100}});
  assert.equal(model.value().blocks[1].branches[0][0].ms,300);
});

test('atualização explícita de revisão mantém volume e marca duração manual sem total fictício',()=>{
  const {createModel,durationLabel}=contract(), model=createModel(doc());
  model.command('reference',{id:'a',occurrence:{...doc().blocks[0],revision_id:'new',interval:[0,24000]}});
  assert.equal(model.value().blocks[0].revision_id,'new');
  assert.equal(model.value().blocks[0].id,'a');
  assert.match(durationLabel({known_duration_ms:2300,duration_ms:null,manual_count:1}),/2.30 s.*acionamento/);
  assert.doesNotMatch(durationLabel({known_duration_ms:2300,duration_ms:null,manual_count:1}),/total/i);
  model.undo();assert.equal(model.value().blocks[0].revision_id,'rev');
});

test('salvar mantém histórico mas atualiza revisão otimista; abrir descarta histórico anterior',()=>{
  const {createModel}=contract(), model=createModel(doc());
  model.command('metadata',{name:'Alterada'});
  model.saved({...model.value(),revision:2});
  model.undo();assert.equal(model.value().name,'Diálogo');assert.equal(model.value().revision,2);
  model.redo();assert.equal(model.value().name,'Alterada');
  model.load(doc());assert.equal(model.canUndo(),false);
});

test('montagem inválida não corrompe undo nem permite editar identidade por patch',()=>{
  const {createModel}=contract(), model=createModel(doc());
  assert.throws(()=>model.command('patch',{id:'a',changes:{id:'changed'}}));
  assert.equal(model.canUndo(),false);
  assert.throws(()=>model.command('add',{parent:'missing',branch:1,block:{id:'x',type:'wait',ms:0}}));
  assert.deepEqual(model.value(),doc());
});

test('UI integrada expõe controles e não agenda áudio por timer frontend',()=>{
  const {create}=contract();assert.equal(typeof create,'function');
  const html=fs.readFileSync(path.join(__dirname,'../ui/index.html'),'utf8');
  for(const id of ['btnSequences','sequencePanel','sequenceBlocks','sequenceLibrary','sequenceStatus','btnSequenceAdvance','btnSequencePreview','btnSequenceRestart'])assert.match(html,new RegExp('id="'+id+'"'));
  const js=fs.readFileSync(filename,'utf8');
  assert.match(js,/get_sequence_status/);assert.match(js,/preview_sequence/);
  assert.doesNotMatch(js,/setTimeout\([^\n]*(start_sequence|play_clip|advance_sequence)/);
});

function deferred(){let resolve;const promise=new Promise(done=>{resolve=done;});return {promise,resolve};}
function fakeDocument(){
  let activeElement=null;
  class Element{
    constructor(tag='div'){this.tagName=tag.toUpperCase();this.children=[];this.dataset={};this.value='';this.style={};this.clientWidth=800;this.clientHeight=600;this.isConnected=true;this.classList={add(){},remove(){},toggle(){}};}
    append(...items){for(const item of items){item.parentElement=this;this.children.push(item);}}
    replaceChildren(...items){this.children=items;}
    get firstChild(){return this.children[0];}
    setAttribute(name,value){this[name]=value;} getAttribute(name){return this[name];} remove(){} addEventListener(){} focus(){activeElement=this;}
  }
  const elements=new Map();
  return {getElementById(id){for(const root of elements.values()){const found=descendants(root).find(e=>e.id===id);if(found)return found;}if(!elements.has(id))elements.set(id,new Element());return elements.get(id);},
    addEventListener(){},removeEventListener(){},createElement:tag=>new Element(tag),createElementNS:(_,tag)=>new Element(tag),body:new Element('body'),querySelectorAll:()=>[],get activeElement(){return activeElement;}};
}
function setup(){
  const documents={A:{...doc(),id:'A',name:'A',revision:4},B:{...doc(),id:'B',name:'B',revision:1}};
  const snapshot=()=>({ok:true,sequences:Object.values(documents).map(document=>({document:structuredClone(document)})),clips:[clip],capabilities:{groups:true},status:{state:'idle'}});
  const bridge={get_sequences:async()=>snapshot(),validate_sequence:async()=>({ok:true,known_duration_ms:1000,duration_ms:1000,manual_count:0})};
  const dom=fakeDocument(),controller=contract().create(dom,()=>bridge);
  return {documents,snapshot,bridge,dom,controller};
}

test('Iniciar A mantém identidade ao abrir B enquanto salvar A está pendente',async()=>{
  const {documents,bridge,controller}=setup(),pending=deferred(),started=[];
  bridge.save_sequence=()=>pending.promise;
  bridge.start_sequence=async id=>{started.push(id);return {ok:true,status:{state:'completed'}};};
  await controller.open('A');const a=controller.model();
  const starting=controller.start();
  await controller.open('B');const b=controller.model();
  documents.A={...documents.A,revision:5};pending.resolve({ok:true,document:structuredClone(documents.A)});
  await starting;
  assert.deepEqual(started,['A']);
  assert.equal(controller.model(),b);assert.equal(b.value().revision,1);assert.equal(a.value().revision,5);
});

test('edições durante salvar ficam locais e segunda gravação concorrente é recusada',async()=>{
  const {documents,bridge,controller}=setup(),pending=deferred(),writes=[];
  bridge.save_sequence=document=>{writes.push(document);return pending.promise;};
  await controller.open('A');controller.model().command('metadata',{name:'Enviada'});
  const saving=controller.save();
  controller.model().command('metadata',{name:'Ainda local'});
  const concurrent=controller.save();
  // Resolve antes de aguardar para o comportamento antigo não bloquear o teste.
  documents.A={...writes[0],revision:5};pending.resolve({ok:true,document:structuredClone(documents.A)});
  const results=await Promise.allSettled([saving,concurrent]);
  assert.equal(results[0].status,'fulfilled');assert.equal(results[1].status,'rejected');assert.equal(writes.length,1);
  assert.equal(controller.model().value().name,'Ainda local');assert.equal(controller.model().value().revision,5);
  assert.equal(controller.model().isDirty(),true);assert.equal(documents.A.name,'Enviada');
});

test('reload explícito recupera revisão atual e exige descarte explícito do rascunho dirty',async()=>{
  const {documents,controller}=setup();await controller.open('A');
  documents.A={...documents.A,revision:5,name:'Remota'};
  await controller.reload();assert.equal(controller.model().value().revision,5);assert.equal(controller.model().value().name,'Remota');
  controller.model().command('metadata',{name:'Local'});
  documents.A={...documents.A,revision:6,name:'Outra remota'};
  await assert.rejects(()=>controller.reload(),/altera|local/i);
  assert.equal(controller.model().value().name,'Local');assert.equal(controller.model().value().revision,5);
  await controller.open('B');await controller.open('A');assert.equal(controller.model().value().name,'Local');
  await controller.reload({discardLocal:true});
  assert.equal(controller.model().value().name,'Outra remota');assert.equal(controller.model().value().revision,6);
  assert.equal(controller.model().isDirty(),false);assert.equal(controller.model().canUndo(),false);
});

test('abrir cache limpo atualiza revisão mas não adota resposta de abertura obsoleta',async()=>{
  const {documents,bridge,snapshot,controller}=setup();await controller.open('A');await controller.open('B');
  documents.A={...documents.A,revision:7};await controller.open('A');assert.equal(controller.model().value().revision,7);
  const pending=deferred();let calls=0;
  bridge.get_sequences=()=>++calls===1?pending.promise:Promise.resolve(snapshot());
  const openingA=controller.open('A');await controller.open('B');pending.resolve(snapshot());await openingA;
  assert.equal(controller.model().value().id,'B');
});

test('reload pendente não descarta edição feita depois do clique nem muda outro modelo',async()=>{
  const {documents,bridge,snapshot,controller}=setup();await controller.open('A');
  const a=controller.model(),pending=deferred();let calls=0;
  bridge.get_sequences=()=>++calls===1?pending.promise:Promise.resolve(snapshot());
  const reloading=controller.reload({discardLocal:true});
  a.command('metadata',{name:'Digitada durante reload'});
  await controller.open('B');documents.A={...documents.A,revision:8};pending.resolve(snapshot());
  await assert.rejects(()=>reloading,/mud|edi|local/i);
  assert.equal(a.value().name,'Digitada durante reload');assert.equal(controller.model().value().id,'B');assert.equal(controller.model().value().revision,1);
});

function findAction(element,action){
  if(element.dataset?.action===action)return element;
  for(const child of element.children||[]){const found=findAction(child,action);if(found)return found;}
  return null;
}

test('adicionar clipe pendente pertence ao modelo clicado mesmo depois de abrir outro',async()=>{
  const {bridge,dom,controller}=setup(),pending=deferred();bridge.pin_sequence_clip=()=>pending.promise;
  await controller.open('A');const a=controller.model();
  const adding=findAction(dom.getElementById('sequenceLibrary'),'library-clip').onclick();
  await controller.open('B');const b=controller.model();
  pending.resolve({ok:true,occurrence:{...doc().blocks[0],id:'added'}});await adding;
  assert.equal(a.value().blocks.length,2);assert.equal(b.value().blocks.length,1);assert.equal(controller.model(),b);
});

test('atualização assíncrona de referência nunca modifica ocorrência homônima de outro modelo',async()=>{
  const {documents,bridge,dom,controller}=setup(),pending=deferred();
  documents.A.blocks=[{...doc().blocks[0],revision_id:'old'}];bridge.pin_sequence_clip=()=>pending.promise;
  await controller.open('A');const a=controller.model();
  const updating=findAction(dom.getElementById('sequenceBlocks'),'reference').onclick();
  await controller.open('B');const b=controller.model();
  pending.resolve({ok:true,occurrence:{...doc().blocks[0],revision_id:'new',interval:[0,24000]}});await updating;
  assert.equal(a.value().blocks[0].revision_id,'new');assert.equal(b.value().blocks[0].revision_id,'rev');
  assert.deepEqual(b.value().blocks[0].interval,[0,48000]);
});

function key(key,options={}){return {key,ctrlKey:false,metaKey:false,altKey:false,shiftKey:false,target:{tagName:'DIV'},preventDefault(){this.prevented=true;},stopPropagation(){this.stopped=true;},...options};}
function descendants(element){return [element,...(element.children||[]).flatMap(descendants)];}
test('roteiro longo mantém inserção antes dos blocos e edita apenas a seleção',async()=>{
  const {documents,dom,controller}=setup();
  documents.A.blocks.push({id:'wait',type:'wait',ms:120},{id:'manual',type:'manual'});
  await controller.open('A');
  const host=dom.getElementById('sequenceBlocks'),nodes=descendants(host);
  assert.ok(nodes.indexOf(findAction(host,'add-play'))<nodes.findIndex(node=>node.dataset.blockId==='a'));
  assert.equal(nodes.filter(node=>node.dataset.field==='start').length,1);
  assert.equal(nodes.filter(node=>node.dataset.field==='ms').length,0);
  await findAction(host,'select-block').onclick();
  assert.equal(controller.selection(),'a');
  controller.select('wait');
  assert.equal(descendants(host).filter(node=>node.dataset.field==='start').length,0);
  assert.equal(descendants(host).find(node=>node.dataset.field==='ms').value,120);
  await findAction(host,'duplicate').onclick();
  assert.equal(controller.model().value().blocks.length,4);
  assert.notEqual(controller.selection(),'wait');
  assert.equal(controller.model().value().blocks.find(block=>block.id===controller.selection()).ms,120);
});
test('atalhos do roteiro navegam, movem, duplicam e desfazem sem roubar edição nativa ou combos globais',async()=>{
  const {documents,controller}=setup();documents.A.blocks.push({id:'wait',type:'wait',ms:120});
  await controller.open('A');
  let event=key('ArrowDown');await controller.handleKeydown(event);assert.equal(controller.selection(),'wait');assert.equal(event.prevented,true);
  event=key('ArrowUp',{altKey:true});await controller.handleKeydown(event);assert.equal(controller.model().value().blocks[0].id,'wait');
  event=key('d',{ctrlKey:true});await controller.handleKeydown(event);assert.equal(controller.model().value().blocks.length,3);
  await controller.handleKeydown(key('z',{ctrlKey:true}));assert.equal(controller.model().value().blocks.length,2);
  await controller.handleKeydown(key('y',{ctrlKey:true}));assert.equal(controller.model().value().blocks.length,3);
  for(const tagName of ['INPUT','TEXTAREA','SELECT']){
    event=key('z',{ctrlKey:true,target:{tagName}});await controller.handleKeydown(event);assert.equal(event.prevented,undefined);
    await controller.handleKeydown(key('Delete',{target:{tagName}}));assert.equal(controller.model().value().blocks.length,3);
  }
  event=key('s',{ctrlKey:true,shiftKey:true});await controller.handleKeydown(event);assert.equal(event.prevented,undefined);
  controller.close();event=key('Delete');await controller.handleKeydown(event);assert.equal(event.prevented,undefined);
});
test('Ctrl+S inclui campo ainda em edição e não depende de blur para salvar',async()=>{
  const {documents,bridge,controller}=setup();const writes=[];
  bridge.save_sequence=async document=>{writes.push(document);documents.A={...document,revision:5};return {ok:true,document:documents.A};};
  await controller.open('A');
  const input={tagName:'INPUT',value:'Nome digitado',onchange(event){controller.model().command('metadata',{name:event.target.value});}};
  const event=key('s',{ctrlKey:true,target:input});await controller.handleKeydown(event);
  assert.equal(writes[0].name,'Nome digitado');assert.equal(event.prevented,true);assert.equal(event.stopped,true);
});

test('inserção e movimentação respeitam conector do meio e undo',()=>{
 const {createModel}=contract(),model=createModel(doc());
 model.command('add',{block:{id:'w',type:'wait',ms:100},before:'a'});
 assert.deepEqual(model.value().blocks.map(b=>b.id),['w','a']);
 model.command('relocate',{id:'a',before:'w'});
 assert.deepEqual(model.value().blocks.map(b=>b.id),['a','w']);
 model.undo();assert.deepEqual(model.value().blocks.map(b=>b.id),['w','a']);
 assert.throws(()=>model.command('add',{block:{id:'lost',type:'wait',ms:1},before:'missing'}),/destino|conector/i);
 assert.deepEqual(model.value().blocks.map(b=>b.id),['w','a']);
});

test('busca de biblioteca inclui frase e pessoa sem depender de acentos',()=>{
 const {matches}=contract();assert.equal(matches({title:'Outro',transcript:'Não acredito',speaker_name:'João'},'nao acredito'),true);
 assert.equal(matches({speaker_name:'João'},'joao'),true);assert.equal(matches({title:'Risada'},'pausa'),false);
});
test('prévia da biblioteca preserva clique mais recente durante parada pendente',async()=>{
 const {bridge,dom,controller,snapshot}=setup(),pending=deferred(),played=[],stopped=[];
 const clips=[{...clip,id:'a'},{...clip,id:'b'},{...clip,id:'c'}];
 bridge.get_sequences=async()=>({...snapshot(),clips});
 bridge.play_clip=async(id,monitor)=>{assert.equal(monitor,true);played.push(id);return {ok:true,play_id:id};};
 bridge.stop_editor_preview=async id=>{stopped.push(id);if(id==='a')return pending.promise;return {ok:true};};
 await controller.open('A');
 const hear=id=>findAction(dom.getElementById('sequenceLibrary').children.find(e=>e.dataset.clipId===id),'preview-library').onclick();
 await hear('a');const b=hear('b');await hear('c');pending.resolve({ok:true});await b;
 assert.deepEqual(played,['a','c']);controller.close();
});
test('fechar durante preparação de prévia para só a reprodução tardia própria',async()=>{
 const {bridge,dom,controller}=setup(),pending=deferred(),stopped=[];
 bridge.play_clip=()=>pending.promise;bridge.stop_editor_preview=async id=>{stopped.push(id);return {ok:true};};
 await controller.open('A');const listening=findAction(dom.getElementById('sequenceLibrary'),'preview-library').onclick();
 await new Promise(resolve=>setImmediate(resolve));controller.close();pending.resolve({ok:true,play_id:'owned'});await listening;
 assert.deepEqual(stopped,['owned']);assert.equal(controller.model().value().blocks.length,1);
});
