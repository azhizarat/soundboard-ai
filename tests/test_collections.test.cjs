const test=require('node:test'), assert=require('node:assert/strict'), fs=require('node:fs'), path=require('node:path');
const filename=path.join(__dirname,'../ui/collections.js');
function contract(){assert.ok(fs.existsSync(filename),'Superfície de coleções ausente');return require(filename);}
function doc(id='show'){return {schema_version:1,id,name:id,revision:0,columns:4,pads:[{id:'p',position:5,label:'Fala',target:{type:'clip',id:'c'},hotkey:'f1',group:'speech',retrigger:'restart',hold:false,loop:false,fade_ms:200}]};}
test('layout mantém lacunas e identidade sem reorganizar por metadados da biblioteca',()=>{
  const {layout}=contract(), original=doc();
  const slots=layout(original);assert.equal(slots[0],null);assert.equal(slots[5].id,'p');
  assert.deepEqual(original,doc());
});
test('edição remove só a posição escolhida e mantém referências compartilhadas',()=>{
  const {createModel}=contract(), model=createModel(doc());
  model.add({type:'clip',id:'c'});assert.equal(model.value().pads[1].position,0);
  model.patch('p',{position:7,loop:true});model.remove(model.value().pads[1].id);
  assert.equal(model.value().pads[0].position,7);assert.equal(model.value().pads[0].target.id,'c');
});
function deferred(){let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve};}
function fakeDocument(){
 class E{constructor(){this.children=[];this.dataset={};this.value='';this.checked=false;this.style={};this.classList={add(){},remove(){},toggle(){}};}append(...x){this.children.push(...x);}replaceChildren(...x){this.children=x;}addEventListener(){}setAttribute(){} }
 const es=new Map();return {body:new E(),getElementById(id){if(!es.has(id))es.set(id,new E());return es.get(id);},createElement(){return new E();},addEventListener(){},querySelectorAll(){return [];}};
}
test('save atrasado não troca coleção selecionada nem contamina novo documento',async()=>{
 const {create}=contract(), saving=deferred(), documents=[doc('A'),doc('B')];
 const bridge={get_collections:async()=>({ok:true,collections:documents,clips:[],sequences:[],active_collection_id:'A',conflicts:[]}),
  get_operation_status:async()=>({ok:true,instances:[],sequence:{state:'idle'}}),save_collection:()=>saving.promise};
 const controller=create(fakeDocument(),()=>bridge);await controller.initialize();controller.select('A');
 const pending=controller.save();controller.select('B');saving.resolve({ok:true,document:{...doc('A'),revision:1}});await pending;
 assert.equal(controller.model().value().id,'B');
});
test('soltura durante chamada assíncrona usa token e alvo capturados mesmo trocando coleção',async()=>{
 const {create}=contract(), starting=deferred(), calls=[];
 const bridge={get_collections:async()=>({ok:true,collections:[doc('A'),doc('B')],clips:[],sequences:[],active_collection_id:'A',conflicts:[]}),
 get_operation_status:async()=>({ok:true,instances:[],sequence:{state:'idle'}}),trigger_collection_pad:(...args)=>{calls.push(args);return args[2]==='press'?starting.promise:Promise.resolve({ok:true});}};
 const controller=create(fakeDocument(),()=>bridge);await controller.initialize();
 const down=controller.press('A','p','physical'), up=controller.release('physical');controller.select('B');
 assert.equal(calls.filter(x=>x[2]==='release').length,1,'Soltar precisa chegar enquanto press ainda prepara');
 starting.resolve({ok:true,execution_id:'old'});await down;await up;
 assert.deepEqual(calls.map(x=>x.slice(0,3)),[['A','p','press'],['A','p','release']]);assert.equal(calls[0][4],calls[1][4]);
});
test('novo gesto recebe token diferente e resposta antiga não solta o novo som',async()=>{
 const {create}=contract(), old=deferred(), calls=[];let first=true;
 const bridge={get_collections:async()=>({ok:true,collections:[doc('A')],clips:[],sequences:[],active_collection_id:'A',conflicts:[]}),get_operation_status:async()=>({ok:true,instances:[],sequence:{state:'idle'}}),
 trigger_collection_pad:(...args)=>{calls.push(args);if(args[2]==='press'&&first){first=false;return old.promise;}return Promise.resolve({ok:true});}};
 const controller=create(fakeDocument(),()=>bridge);await controller.initialize();
 const down=controller.press('A','p','physical');await controller.release('physical');
 await controller.press('A','p','physical');old.resolve({ok:false,error:'Antigo cancelado'});await assert.rejects(down);
 await controller.release('physical');const presses=calls.filter(x=>x[2]==='press'), releases=calls.filter(x=>x[2]==='release');
 assert.notEqual(presses[0][4],presses[1][4]);assert.equal(releases[1][4],presses[1][4]);
});
test('UI expõe áreas distintas, edição completa e saída persistente',()=>{
 contract();const html=fs.readFileSync(path.join(__dirname,'../ui/index.html'),'utf8');
 for(const id of ['btnPrepare','btnPerform','operationPanel','collectionPads','collectionSelect','collectionEditor','btnCollectionNew','btnCollectionSave','rehearsalToggle','operationStatus'])assert.match(html,new RegExp('id="'+id+'"'));
});

test('grupo curado preserva membros e repetição explícita usa dispatcher com gesto próprio',async()=>{
 const {create}=contract(), calls=[], group=doc('A');group.pads[0].target={type:'shuffle',items:[{type:'clip',id:'c'},{type:'sequence',id:'s'}]};group.pads[0].cooldown_ms=800;
 const bridge={get_collections:async()=>({ok:true,collections:[group],clips:[{id:'c',title:'Voz'}],sequences:[{id:'s',name:'Fluxo'}],active_collection_id:'A',conflicts:[]}),get_operation_status:async()=>({ok:true,instances:[],sequence:{state:'idle'},shuffle_choices:{'A:p':{type:'clip',id:'c'}}}),trigger_collection_pad:async(...args)=>{calls.push(args);return {ok:true};}};
 const dom=fakeDocument(), controller=create(dom,()=>bridge);await controller.initialize();
 await controller.press('A','p','repeat',true);await controller.release('repeat');
 assert.equal(calls[0][5],true);assert.equal(calls[0][4],calls[1][4]);
 assert.deepEqual(controller.model().value().pads[0].target,group.pads[0].target);
 assert.match(fs.readFileSync(path.join(__dirname,'../ui/index.html'),'utf8'),/id="btnCollectionShuffle"/);
});
test('biblioteca atualizada oferece variante nova sem substituir o draft da coleção',async()=>{
 const {create}=contract(),dom=fakeDocument();let clips=[{id:'c',title:'Original'}];
 const api={get_collections:async()=>({ok:true,collections:[doc()],clips,sequences:[],active_collection_id:'show',conflicts:[]}),get_operation_status:async()=>({ok:true,instances:[]})};
 const controller=create(dom,()=>api);await controller.initialize();controller.model().metadata({name:'Alteração ainda não salva'});
 clips=[...clips,{id:'variant',title:'Variante'}];await controller.refresh();
 assert.equal(controller.model().value().name,'Alteração ainda não salva');
 assert.ok(dom.getElementById('collectionTarget').children.some(option=>option.value==='clip:variant'));
});
