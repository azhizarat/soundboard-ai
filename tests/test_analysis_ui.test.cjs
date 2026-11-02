const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

test('resultado aplicado atualiza o editor uma vez sem persistir ou reabrir formulário durante polling',async()=>{
  const {create}=require('../ui/analysis.js'),elements={},applied=[];let writes=0,reopens=0;
  const document={getElementById:id=>elements[id]||={value:'texto digitado',textContent:'',hidden:false,checked:false}};
  const job={ok:true,analysis_id:'job',stage:'transcription',status:'ready',application:{state:'applied'},result:{text:'Texto novo'}};
  const bridge={get_clip_transcript:async()=>({ok:true,transcript:{alignment:'unaligned',words:[]}}),list_analysis_jobs:async()=>({jobs:[job]}),get_analysis_job:async()=>job};
  const panel=create(document,()=>bridge,{persist:async()=>{writes++;return {ok:true};},refresh:async()=>reopens++,applied:async info=>applied.push(info),schedule:()=>0,unschedule(){}});
  await panel.open('clip');await panel.poll();await panel.poll();
  assert.equal(applied.length,1);assert.equal(applied[0].clipId,'clip');assert.equal(applied[0].stage,'transcription');
  assert.equal(writes,0);assert.equal(reopens,0);assert.equal(elements.editTranscript?.value??'texto digitado','texto digitado');
  panel.close();
});
test('ações rápidas usam seleção atual só para transcrição, sem ler hidden fields antigos',async()=>{
  const {create}=require('../ui/analysis.js'),elements={},calls=[];
  const document={getElementById:id=>elements[id]||={value:'',textContent:'',hidden:false,checked:false}};
  const bridge={get_clip_transcript:async()=>({ok:true,transcript:{alignment:'aligned',words:[]}}),list_analysis_jobs:async()=>({jobs:[]}),request_clip_analysis:async(...args)=>{calls.push(args);return {ok:true,analysis_id:'job',status:'queued'};}};
  const panel=create(document,()=>bridge,{selection:()=>[96000,144000],persist:async()=>({ok:true}),schedule:()=>0,unschedule(){}});
  await panel.open('clip');document.getElementById('analysisSelection').checked=true;document.getElementById('analysisStart').value='0';document.getElementById('analysisEnd').value='1';
  await document.getElementById('analysisTranscribe').onclick();await document.getElementById('analysisSpeakers').onclick();await document.getElementById('analysisMetadata').onclick();
  assert.deepEqual(calls.map(call=>[call[1],call[2]]),[['transcription',[96000,144000]],['speakers',null],['metadata',null]]);
  panel.close();
});
test('aceitar proposta atualiza editor sem reabertura e abrir clipe não muda preferências globais',async()=>{
  const {create}=require('../ui/analysis.js'),elements={},applied=[];let reopened=0;
  const document={getElementById:id=>elements[id]||={value:'',textContent:'',hidden:false,checked:false}};
  document.getElementById('asrModel').value='configuração ainda em edição';
  const job={ok:true,analysis_id:'job',stage:'transcription',status:'ready',application:{state:'proposal',accept_token:'t'},result:{text:'Novo'}};
  const bridge={get_clip_transcript:async()=>({ok:true,transcript:{alignment:'aligned',words:[]}}),list_analysis_jobs:async()=>({jobs:[job]}),get_analysis_job:async()=>job,accept_analysis_result:async()=>({ok:true})};
  const panel=create(document,()=>bridge,{persist:async()=>({ok:true}),refresh:async()=>reopened++,applied:async info=>applied.push(info),schedule:()=>0,unschedule(){}});
  await panel.open('clip',{asr_model:'base'});assert.equal(elements.asrModel.value,'configuração ainda em edição');
  await elements.btnAcceptAnalysis.onclick();assert.equal(applied.length,1);assert.equal(reopened,0);panel.close();
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

test('painel mostra fila cheia, estado e proposta sem reabrir nem sobrescrever campos', async () => {
  const filename = path.join(__dirname, '../ui/analysis.js');
  assert.ok(fs.existsSync(filename), 'Painel de análise ausente');
  const {create} = require(filename);
  const elements = {};
  const document = {getElementById: id => elements[id] ||= {value: '', textContent: '', hidden: false, checked: false}};
  let response = {ok: false, error: 'Fila cheia'};
  let refreshed = 0;
  const api = {request_clip_analysis: async () => response,
    list_analysis_jobs: async () => ({jobs: []}),
    get_clip_transcript: async () => ({ok: true, transcript: {alignment: 'unaligned', words: []}}),
    get_analysis_job: async () => ({ok: true, status: 'ready', application: {state: 'proposal', accept_token: 'token'}, result: {text: 'sugestão'}}),
    accept_analysis_result: async (...args) => {assert.equal(args[2], 'token'); return {ok: true};}};
  const panel = create(document, () => api, {persist: async () => ({ok:true}), refresh: async () => refreshed++, schedule: () => 0, unschedule: () => {}});
  await panel.open('clip', {});
  await panel.start();
  assert.match(elements.analysisStatus.textContent, /Fila cheia/);
  response = {ok: true, analysis_id: 'a', status: 'queued'};
  await panel.start();
  await panel.poll();
  assert.equal(refreshed, 0);
  assert.equal(elements.analysisProposal.textContent, 'sugestão');
  assert.equal(elements.btnAcceptAnalysis.hidden, false);
  await elements.btnAcceptAnalysis.onclick();
  assert.equal(refreshed, 1);
  panel.close();
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

test('painel identifica alinhamento parcial e blocos sem tempos por palavra', async () => {
  const {create} = require('../ui/analysis.js');
  const elements = {};
  const document = {getElementById:id=>elements[id] ||= {value:'',textContent:'',hidden:false}};
  const bridge = {get_clip_transcript:async()=>({ok:true,transcript:{alignment:'partial',words:[{text:'alvo'}]}}),
    list_analysis_jobs:async()=>({jobs:[]})};
  const panel=create(document,()=>bridge,{schedule:()=>0,unschedule:()=>{}});
  await panel.open('mixed');
  assert.match(elements.analysisAlignment.textContent,/parcial/i);
  assert.match(elements.analysisAlignment.textContent,/segmentos sem alinhamento por palavra/i);
  panel.close();
});

test('carregar resultado conserva formulário quando persist falha, retorna null ou rejeita',async()=>{
  const {create}=require('../ui/analysis.js');
  for(const mode of ['failed','null','reject']){
    const elements={}; let refreshed=0;
    const document={getElementById:id=>elements[id] ||= {value:'edição local',textContent:'',hidden:false}};
    const bridge={get_clip_transcript:async()=>({ok:true,transcript:{alignment:'aligned',words:[]}}),list_analysis_jobs:async()=>({jobs:[]})};
    const panel=create(document,()=>bridge,{schedule:()=>0,unschedule:()=>{},
      persist:async()=>{if(mode==='reject')throw new Error('Falha de gravação');return mode==='null'?null:{ok:false,error:'Falha de gravação'};},
      refresh:async()=>{refreshed++;elements.editTranscript.value='resultado';}});
    await panel.open('clip');
    document.getElementById('editTranscript');
    await elements.btnLoadAnalysis.onclick();
    assert.equal(refreshed,0,mode);
    assert.equal(elements.editTranscript.value,'edição local');
    assert.match(elements.analysisStatus.textContent,/falha|preservar/i);
    panel.close();
  }
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
