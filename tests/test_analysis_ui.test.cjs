const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function deferred(){let resolve;const promise=new Promise(done=>resolve=done);return {promise,resolve};}
function inlineFixture(options={}){
  const stages=['transcription','metadata','speakers'],elements={},timers=new Map(),calls=[],applied=[];
  const ids=['analysisTranscribe','analysisMetadata','analysisSpeakers','editorRetranscribe','analysisTranscriptionFeedback','analysisMetadataFeedback','analysisSpeakersFeedback',
    ...stages.flatMap(stage=>['Status','Progress','Cancel','Proposal','Accept','Runtime'].map(suffix=>stage+'Analysis'+suffix))];
  for(const id of ids)elements[id]={textContent:'',hidden:false,disabled:false,value:35,attrs:{},
    setAttribute(name,value){this.attrs[name]=value;},removeAttribute(name){delete this.attrs[name];delete this[name];}};
  const document={getElementById(id){assert.ok(id in elements,'Dependência de controle antigo: '+id);return elements[id];}};
  const jobs=new Map();let history=[],serial=0,timerId=0;
  const api={list_analysis_jobs:async()=>({ok:true,jobs:structuredClone(history)}),
    request_clip_analysis:async(...args)=>{calls.push(['request',...args]);const job={ok:true,analysis_id:'j'+(++serial),stage:args[1],status:'queued'};jobs.set(job.analysis_id,job);return structuredClone(job);},
    get_analysis_job:async id=>{calls.push(['poll',id]);return structuredClone(jobs.get(id));},
    cancel_analysis_job:async id=>{calls.push(['cancel',id]);jobs.get(id).status='cancelled';return {ok:true};},
    accept_analysis_result:async(...args)=>{calls.push(['accept',...args]);return {ok:true};}};
  const callbacks={persist:async()=>({ok:true}),selection:()=>[96000,144000],applied:async info=>applied.push(info),
    schedule:fn=>{timers.set(++timerId,fn);return timerId;},unschedule:id=>timers.delete(id),...options};
  const panel=require('../ui/analysis.js').create(document,()=>api,callbacks);
  return {panel,api,elements,calls,applied,jobs,timers,setHistory(list){history=list;for(const job of list)jobs.set(job.analysis_id,job);}};
}
const proposal=(stage,id)=>({ok:true,analysis_id:id,stage,status:'ready',application:{state:'proposal',accept_token:id+'-token'},
  result:stage==='transcription'?{text:'Texto proposto'}:{fields:stage==='metadata'?{title:'Título proposto',category:'Meme'}:{speaker_id:'pessoa-1'}}});

test('cada local restaura seu último trabalho e atualiza resultado aplicado uma vez, sem controles globais',async()=>{
  let writes=0;const f=inlineFixture({persist:async()=>{writes++;return {ok:true};}});
  f.setHistory([{ok:true,analysis_id:'old',stage:'transcription',status:'failed'},
    {ok:true,analysis_id:'new',stage:'transcription',status:'ready',application:{state:'applied'},result:{text:'Nova fala',model:'small',device:'cpu'}},
    proposal('metadata','title'),{ok:true,analysis_id:'people',stage:'speakers',status:'running',progress:70}]);
  await f.panel.open('clip');await f.panel.poll();
  assert.deepEqual(f.applied,[{clipId:'clip',analysisId:'new',stage:'transcription'}]);assert.equal(writes,0);
  assert.ok(!f.calls.some(call=>call[0]==='poll'&&call[1]==='old'));
  assert.equal(f.elements.metadataAnalysisProposal.textContent,'Título: Título proposto\nCategoria: Meme');
  assert.equal(f.elements.speakersAnalysisProgress.hidden,false);assert.equal('value' in f.elements.speakersAnalysisProgress,false);
  assert.match(f.elements.transcriptionAnalysisRuntime.title,/small.*cpu/);
  assert.equal(f.elements.transcriptionAnalysisProgress.hidden,true);f.panel.close();assert.equal(f.timers.size,0);
});
test('ações rápidas transcrevem inteiro e seleção explícita captura intervalo canônico no clique',async()=>{
  const f=inlineFixture();await f.panel.open('clip');await f.elements.analysisTranscribe.onclick();
  assert.equal(f.calls.find(call=>call[0]==='request')[3],null);
  f.jobs.get('j1').status='cancelled';await f.panel.poll('transcription');
  await f.panel.start('transcription',{selection:true});await f.elements.analysisMetadata.onclick();await f.elements.analysisSpeakers.onclick();
  assert.deepEqual(f.calls.filter(call=>call[0]==='request').map(call=>[call[2],call[3]]),
    [['transcription',null],['transcription',[96000,144000]],['metadata',null],['speakers',null]]);
  f.panel.close();
});
test('estágios independentes não duplicam admissão e cancelamento só afeta seu próprio trabalho',async()=>{
  const hold=deferred();const f=inlineFixture({persist:()=>hold.promise});await f.panel.open('clip');
  const a=f.panel.start('transcription'),duplicate=f.panel.start('transcription'),b=f.panel.start('metadata');
  assert.equal(f.elements.analysisTranscribe.disabled,true);assert.equal(f.elements.transcriptionAnalysisProgress.hidden,false);
  hold.resolve({ok:true});await Promise.all([a,duplicate,b]);
  assert.equal(f.calls.filter(call=>call[0]==='request').length,2);
  await f.elements.transcriptionAnalysisCancel.onclick();
  assert.deepEqual(f.calls.filter(call=>call[0]==='cancel'),[['cancel','j1']]);
  assert.equal(f.elements.transcriptionAnalysisProgress.hidden,true);assert.equal(f.elements.metadataAnalysisProgress.hidden,false);
  f.panel.close();
});
test('aceite preserva alvo e token, recusa clique duplicado e ignora retorno após trocar clipe',async()=>{
  const f=inlineFixture();f.setHistory([proposal('transcription','text'),proposal('metadata','title')]);await f.panel.open('A');
  const hold=deferred();f.api.accept_analysis_result=(...args)=>{f.calls.push(['accept',...args]);return hold.promise;};
  const first=f.elements.metadataAnalysisAccept.onclick(),second=f.elements.metadataAnalysisAccept.onclick();
  await new Promise(resolve=>setImmediate(resolve));assert.deepEqual(f.calls.filter(call=>call[0]==='accept'),[['accept','A','title','title-token']]);
  f.setHistory([]);await f.panel.open('B');hold.resolve({ok:true});await Promise.all([first,second]);
  assert.deepEqual(f.applied,[]);assert.equal(f.elements.metadataAnalysisProposal.hidden,true);f.panel.close();
});
test('persistência inválida mantém proposta e impede tanto admissão como aceite',async()=>{
  for(const mode of ['false','null','throw']){
    const f=inlineFixture({persist:async()=>{if(mode==='throw')throw Error('Falha de gravação');return mode==='null'?null:{ok:false,error:'Falha de gravação'};}});
    f.setHistory([proposal('metadata','title')]);await f.panel.open('clip');
    await f.panel.start('transcription');await f.elements.metadataAnalysisAccept.onclick();
    assert.equal(f.calls.some(call=>['request','accept'].includes(call[0])),false,mode);
    assert.equal(f.elements.metadataAnalysisProposal.hidden,false);assert.match(f.elements.metadataAnalysisStatus.textContent,/falha|preservar/i);
    assert.equal(f.elements.analysisTranscribe.disabled,false);f.panel.close();
  }
});
test('poll antigo não substitui pedido novo nem dispara atualização para resultado superado',async()=>{
  const f=inlineFixture();f.setHistory([proposal('metadata','old')]);await f.panel.open('clip');
  const hold=deferred();f.api.get_analysis_job=id=>id==='old'?hold.promise:Promise.resolve(f.jobs.get(id));
  const polling=f.panel.poll('metadata');await f.panel.start('metadata');
  hold.resolve({ok:true,analysis_id:'old',stage:'metadata',status:'ready',application:{state:'applied'},result:{fields:{title:'Velho'}}});await polling;
  assert.deepEqual(f.applied,[]);assert.equal(f.elements.metadataAnalysisProgress.hidden,false);assert.equal(f.elements.metadataAnalysisProposal.hidden,true);f.panel.close();
});
test('restauração lenta não substitui pedido disparado enquanto abre o clipe',async()=>{
  const f=inlineFixture(),hold=deferred();f.api.list_analysis_jobs=()=>hold.promise;
  const opening=f.panel.open('clip');await f.panel.start('transcription');
  hold.resolve({ok:true,jobs:[proposal('transcription','old')]});await opening;
  assert.equal(f.calls.some(call=>call[0]==='poll'&&call[1]==='old'),false);
  assert.equal(f.elements.transcriptionAnalysisProgress.hidden,false);f.panel.close();
});
test('aceite aplicado atualiza só seu estágio e resultado obsoleto não oferece aplicação',async()=>{
  const f=inlineFixture();f.setHistory([proposal('transcription','text'),proposal('metadata','title')]);await f.panel.open('clip');
  await f.elements.transcriptionAnalysisAccept.onclick();
  assert.deepEqual(f.applied,[{clipId:'clip',analysisId:'text',stage:'transcription'}]);
  assert.equal(f.elements.transcriptionAnalysisAccept.hidden,true);assert.equal(f.elements.metadataAnalysisAccept.hidden,false);
  f.jobs.get('title').application={state:'obsolete'};await f.panel.poll('metadata');
  assert.equal(f.elements.metadataAnalysisAccept.hidden,true);assert.match(f.elements.metadataAnalysisStatus.textContent,/obsoleto|mudou/i);f.panel.close();
});

test('seleção é capturada antes de salvar e intervalo inválido não vira análise integral',async()=>{
  const hold=deferred(),selection=[100,200],f=inlineFixture({persist:()=>hold.promise,selection:()=>selection});
  await f.panel.open('clip');const work=f.panel.start('transcription',{selection:true});selection[1]=300;
  hold.resolve({ok:true});await work;assert.deepEqual(f.calls.find(call=>call[0]==='request')[3],[100,200]);
  f.jobs.get('j1').status='cancelled';await f.panel.poll('transcription');selection[0]=400;
  await f.panel.start('transcription',{selection:true});assert.equal(f.calls.filter(call=>call[0]==='request').length,1);
  assert.match(f.elements.transcriptionAnalysisStatus.textContent,/trecho.*válido/);f.panel.close();
});

test('erro de admissão fica no campo e resposta de outro clipe não cria polling',async()=>{
  const f=inlineFixture();await f.panel.open('A');
  f.api.request_clip_analysis=async()=>({ok:false,error:'Fila cheia'});await f.panel.start('metadata');
  assert.match(f.elements.metadataAnalysisStatus.textContent,/Fila cheia/);assert.equal(f.elements.analysisMetadata.disabled,false);
  assert.equal(f.elements.analysisTranscriptionFeedback.hidden,true);
  const hold=deferred();f.api.request_clip_analysis=()=>hold.promise;
  const work=f.panel.start('transcription');await new Promise(resolve=>setImmediate(resolve));await f.panel.open('B');
  hold.resolve({ok:true,analysis_id:'late',stage:'transcription',status:'queued'});await work;
  assert.equal(f.timers.size,0);assert.equal(f.elements.analysisTranscriptionFeedback.hidden,true);f.panel.close();
});

test('poll transitório e falha ao projetar aceite tentam novamente sem repetir gravação',async()=>{
  let notifications=0;const f=inlineFixture({applied:async()=>{notifications++;if(notifications===1)throw Error('Projeção indisponível');}});
  f.setHistory([proposal('metadata','title')]);await f.panel.open('clip');
  f.api.accept_analysis_result=async()=>{f.calls.push(['accept']);f.jobs.get('title').application={state:'applied'};return {ok:true};};
  await f.elements.metadataAnalysisAccept.onclick();assert.equal(f.timers.size,1);assert.match(f.elements.metadataAnalysisStatus.textContent,/Projeção/);
  await f.panel.poll('metadata');assert.equal(notifications,2);assert.equal(f.calls.filter(call=>call[0]==='accept').length,1);
  await f.panel.start('transcription');const original=f.api.get_analysis_job;
  f.api.get_analysis_job=async()=>{throw Error('Ponte indisponível');};await f.panel.poll('transcription');
  assert.equal(f.elements.transcriptionAnalysisProgress.hidden,false);assert.equal(f.timers.size,1);
  f.api.get_analysis_job=original;f.jobs.get('j1').status='failed';f.jobs.get('j1').error={message:'Modelo ausente'};
  await f.panel.poll('transcription');assert.match(f.elements.transcriptionAnalysisStatus.textContent,/Modelo ausente/);
  assert.equal(f.elements.transcriptionAnalysisProgress.hidden,true);assert.equal(f.timers.size,0);f.panel.close();
});

test('recusa de aceite obsoleto retira a proposta e mantém edição atual',async()=>{
  const f=inlineFixture();f.setHistory([proposal('metadata','title')]);await f.panel.open('clip');
  f.api.accept_analysis_result=async()=>({ok:false,state:'obsolete'});await f.elements.metadataAnalysisAccept.onclick();
  assert.equal(f.elements.metadataAnalysisAccept.hidden,true);assert.equal(f.elements.metadataAnalysisProposal.hidden,true);
  assert.equal(f.applied.length,0);assert.match(f.elements.metadataAnalysisStatus.textContent,/mudou/);f.panel.close();
});

test('projeção pendente mantém progresso e bloqueia ambas ações de transcrição até atualizar o campo',async()=>{
  const hold=deferred(),f=inlineFixture({applied:()=>hold.promise});
  f.setHistory([{ok:true,analysis_id:'text',stage:'transcription',status:'ready',application:{state:'applied'},result:{text:'Novo'}}]);
  const opening=f.panel.open('clip');await new Promise(resolve=>setImmediate(resolve));
  assert.equal(f.elements.transcriptionAnalysisProgress.hidden,false);assert.equal(f.elements.editorRetranscribe.disabled,true);
  assert.equal(f.elements.analysisTranscribe.disabled,true);assert.match(f.elements.transcriptionAnalysisStatus.textContent,/Atualizando/);
  await f.panel.start('transcription',{selection:true});assert.equal(f.calls.some(call=>call[0]==='request'),false);
  hold.resolve();await opening;assert.equal(f.elements.transcriptionAnalysisProgress.hidden,true);
  assert.equal(f.elements.editorRetranscribe.disabled,false);assert.match(f.elements.transcriptionAnalysisStatus.textContent,/aplicado/);f.panel.close();
});




test('preferências ASR inicializam e salvam globalmente sem abrir clipe',async()=>{
  assert.ok(fs.existsSync('ui/ai-settings.js'),'Preferências globais de IA ausentes');
  const {create}=require('../ui/ai-settings.js'),elements={},saved=[],changed=[];
  const document={getElementById:id=>elements[id]||={value:'',textContent:'',disabled:false}};
  const settings=create(document,()=>({save_app_config:async updates=>{saved.push(updates);return {ok:true,config:{...updates,analysis_profile:'reviewing'}};}}),{configChanged:config=>changed.push(config)});
  settings.initialize({asr_model:'small',asr_device:'cpu',asr_beam_size:4,asr_hotwords:'Lucas',asr_initial_prompt:'Português'});
  assert.equal(elements.asrModel.value,'small');elements.asrModel.value='medium';
  settings.initialize({asr_model:'base'});assert.equal(elements.asrModel.value,'medium','Abrir outra tela não apaga configuração em edição');
  await elements.btnSaveAsr.onclick();assert.equal(saved[0].asr_model,'medium');assert.equal(saved[0].asr_beam_size,4);
  assert.equal(changed[0].analysis_profile,'reviewing');assert.match(elements.aiSettingsStatus.textContent,/globais/i);
});
test('salvamento global pendente não duplica pedido nem apaga digitação posterior',async()=>{
  assert.ok(fs.existsSync('ui/ai-settings.js'));
  const {create}=require('../ui/ai-settings.js'),elements={};let complete,calls=0;
  const document={getElementById:id=>elements[id]||={value:'',textContent:'',disabled:false}};
  const settings=create(document,()=>({save_app_config:updates=>{calls++;return new Promise(resolve=>complete=()=>resolve({ok:true,config:updates}));}}));
  settings.initialize({asr_model:'base'});const first=settings.save();const second=settings.save();
  elements.asrModel.value='small';complete();await Promise.all([first,second]);
  assert.equal(calls,1);assert.equal(elements.asrModel.value,'small');assert.match(elements.aiSettingsStatus.textContent,/pendente/i);
});



test('autosave ignora campos intocados enquanto o resultado atualiza o rascunho', () => {
  const source = fs.readFileSync(path.join(__dirname, '../ui/app.js'), 'utf8');
  const start = source.indexOf('function currentClipEditPayload(');
  const code = source.slice(start, source.indexOf('async function persistCurrentClipDraft', start));
  const values = {editClipId: 'clip', editTitle: 'original', editTranscript: 'antigo', editVolume: '1'};
  const state = {durableEdit: true, editTrimDirty: false, editFieldBaseline: {title: 'original', transcript: 'antigo'}};
  const context = vm.createContext({state, document: {getElementById: id => ({value: values[id] || ''})}});
  vm.runInContext(code, context);
  const payload = context.currentClipEditPayload();
  assert.equal('transcript' in payload, false);
  assert.equal('title' in payload, false);
  values.editTitle = 'mudou';
  assert.equal(context.currentClipEditPayload().title, 'mudou');
});

test('reverter campo enquanto autosave aguarda conserva a última intenção humana', async () => {
  const source = fs.readFileSync(path.join(__dirname, '../ui/app.js'), 'utf8');
  const start = source.indexOf('function currentClipEditPayload()');
  const actualStart = start >= 0 ? start : source.indexOf('function currentClipEditPayload(');
  const code = source.slice(actualStart, source.indexOf('async function refreshEditDrafts', actualStart));
  const values = {editClipId:'clip', editTitle:'original', editVolume:'1'};
  const calls = [];
  const state = {durableEdit:true, editTrimDirty:false, editFieldBaseline:{title:'original'}, editDraftQueue:Promise.resolve()};
  const context = vm.createContext({state, document:{getElementById:id=>({value:values[id] || ''})},
    api:()=>({update_clip_edit:async payload=>{calls.push(payload);return {ok:true};}}),showToast:()=>{}});
  vm.runInContext(code, context);
  values.editTitle='temporário';
  const first=context.persistCurrentClipDraft();
  values.editTitle='original';
  const second=context.persistCurrentClipDraft();
  await Promise.all([first,second]);
  assert.deepEqual(calls.map(c=>c.title), ['temporário','original']);
});

test('autosave da janela anterior não reenvia campos intocados ao trocar editor', async () => {
  const source = fs.readFileSync(path.join(__dirname, '../ui/app.js'), 'utf8');
  const start = source.indexOf('function currentClipEditPayload(');
  const code = source.slice(start, source.indexOf('async function refreshEditDrafts', start));
  const values = {editClipId:'old',editTitle:'original',editTranscript:'antigo',editVolume:'1'};
  const calls = [];
  const state = {durableEdit:true,editTrimDirty:false,editOpenEpoch:1,editFieldBaseline:{title:'original',transcript:'antigo'},editDraftQueue:Promise.resolve()};
  const context = vm.createContext({state,document:{getElementById:id=>({value:values[id] || ''})},
    api:()=>({update_clip_edit:async payload=>{calls.push(payload);return {ok:true};}}),showToast:()=>{}});
  vm.runInContext(code,context);
  values.editTitle='editado';
  const pending=context.persistCurrentClipDraft();
  state.editOpenEpoch=2;
  state.editFieldBaseline={title:'outro',transcript:'outra'};
  await pending;
  assert.equal('transcript' in calls[0],false);
  assert.equal(state.editFieldBaseline.title,'outro');
});






test('IA atualiza título intocado sem sobrescrever edição humana durante a resposta',async()=>{
 const source=fs.readFileSync(path.join(__dirname,'../ui/app.js'),'utf8');
 const start=source.indexOf('async function refreshAppliedAnalysis('),end=source.indexOf('async function openEditModal(',start);
 const elements={editClipId:{value:'c'},editTitle:{value:'Original'},editCategory:{value:'Call'}};
 const state={editOpenEpoch:1,editFieldBaseline:{title:'Original',category:'Call'},integratedEditor:{refresh:async()=>({ok:true})}};
 let finish;
 const context=vm.createContext({state,document:{getElementById:id=>elements[id]},currentClipEditPayload:()=>({title:elements.editTitle.value,category:elements.editCategory.value}),api:()=>({list_clip_edits:()=>new Promise(resolve=>finish=resolve)})});
 vm.runInContext(source.slice(start,end),context);
 const first=context.refreshAppliedAnalysis({clipId:'c',stage:'metadata'});while(!finish)await new Promise(resolve=>setImmediate(resolve));
 elements.editTitle.value='Humano';finish({ok:true,clips:[{id:'c',title:'IA',category:'Meme'}]});await first;
 assert.equal(elements.editTitle.value,'Humano');assert.equal(elements.editCategory.value,'Meme');assert.equal(state.editFieldBaseline.category,'Meme');
});
