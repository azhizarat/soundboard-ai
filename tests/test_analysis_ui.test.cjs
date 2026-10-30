const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

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
