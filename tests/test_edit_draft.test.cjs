const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

test('rascunho captura campos e serializa gravações sem publicar revisão', async () => {
  const source = fs.readFileSync(require('node:path').join(__dirname, '../ui/app.js'), 'utf8');
  const start = source.indexOf('function currentClipEditPayload(');
  assert.ok(start >= 0, 'Persistência de rascunho do editor ausente');
  const code = source.slice(start, source.indexOf('async function saveCurrentClipEdits', start));
  const values = {editClipId: 'clip', editTitle: 'primeiro', editTrimStart: '.25', editTrimEnd: '.75', editVolume: '.5'};
  const calls = [];
  const context = vm.createContext({
    document: {getElementById: id => ({value: values[id] || ''})},
    state: {durableEdit: true, editTrimDirty: true, editDraftQueue: Promise.resolve()},
    api: () => ({update_clip_edit: async payload => {calls.push(payload); return {ok: true};}}),
    showToast: () => {},
  });
  vm.runInContext(code, context);
  const first = context.persistCurrentClipDraft();
  values.editTitle = 'segundo';
  const second = context.persistCurrentClipDraft();
  await Promise.all([first, second]);
  assert.deepEqual(calls.map(call => call.title), ['primeiro', 'segundo']);
  assert.equal(calls[1].trim_start, .25);
  assert.equal(calls[1].volume, .5);
  assert.equal('file_path' in calls[1], false);
});

test('arredondamento do range não envia trim em título, autosave, prévia e save', async () => {
  const source = fs.readFileSync(require('node:path').join(__dirname, '../ui/app.js'), 'utf8');
  const start = source.indexOf('function currentClipEditPayload(');
  const code = source.slice(start, source.indexOf('function applyDraftChrome(', start));
  const controls = {};
  for (const id of ['editClipId', 'editTitle', 'editTrimStart', 'editTrimEnd', 'editVolume']) {
    let stored = '';
    controls[id] = {get value() {return stored;}, set value(raw) {
      stored = id.startsWith('editTrim') ? String(Math.round(Number(raw) / .01) * .01) : String(raw);
    }};
  }
  controls.editClipId.value = 'one'; controls.editTitle.value = 'original';
  controls.editTrimStart.value = 0; controls.editTrimEnd.value = 16000 / 48000;
  controls.editVolume.value = 1;
  assert.equal(controls.editTrimEnd.value, '.33'.replace(/^\./, '0.'));
  const calls = [];
  const clip = {id: 'one', duration: 16000 / 48000};
  const context = vm.createContext({
    document: {getElementById: id => controls[id] || {value: ''}},
    state: {durableEdit: true, editTrimDirty: false, editDraftQueue: Promise.resolve(), clips: [clip]},
    api: () => ({
      update_clip_edit: async payload => {calls.push(['draft', payload]); return {ok: true};},
      save_clip_edits: async payload => {calls.push(['save', payload]); return {ok: true, clip};},
    }),
    renderSidebar: () => {}, renderGrid: () => {}, showToast: () => {},
  });
  vm.runInContext(code, context);
  vm.runInContext('refreshEditDrafts = () => {};', context);
  controls.editTitle.value = 'apenas título';
  await context.persistCurrentClipDraft(); // autosave
  await context.persistCurrentClipDraft(); // caminho da prévia
  await context.saveCurrentClipEdits(false);
  for (const [, payload] of calls) {
    assert.equal('trim_start' in payload, false);
    assert.equal('trim_end' in payload, false);
  }
  context.state.editTrimDirty = true;
  controls.editTrimStart.value = .01;
  const intentional = context.currentClipEditPayload();
  assert.equal(intentional.trim_start, .01);
  assert.equal(intentional.trim_end, .33);
});

test('abertura antiga não substitui a edição mais recente', async () => {
  const source = fs.readFileSync(require('node:path').join(__dirname, '../ui/app.js'), 'utf8');
  const start = source.indexOf('async function prepareClipEdit(');
  assert.ok(start >= 0, 'Proteção contra abertura obsoleta ausente');
  const code = source.slice(start, source.indexOf('async function openEditModal(', start));
  const pending = {};
  const context = vm.createContext({state: {}, api: () => ({begin_clip_edit: id => new Promise(resolve => {pending[id] = resolve;})}), showToast: () => {}});
  vm.runInContext(code, context);
  const a = context.prepareClipEdit({id: 'a'});
  const b = context.prepareClipEdit({id: 'b'});
  pending.b({ok: true, clip: {id: 'b'}});
  assert.equal((await b).clip.id, 'b');
  pending.a({ok: true, clip: {id: 'a'}});
  assert.equal(await a, null);
});

test('footer do editor usa a preview isolada em vez da política pública',async()=>{
  const source=fs.readFileSync(require('node:path').join(__dirname,'../ui/app.js'),'utf8');
  const start=source.indexOf('  async function toggleModalAudioPreview()');
  const code=source.slice(start,source.indexOf('  document.getElementById("btnSaveClip").onclick',start));
  const calls=[];
  const context=vm.createContext({state:{integratedEditor:{preview:async mode=>calls.push(mode)}},
    document:{getElementById:()=>({value:'clip'})},persistCurrentClipDraft:async()=>({ok:true}),
    api:()=>({play_clip:()=>{throw Error('política pública não deve ser usada');}})});
  vm.runInContext(code,context);await context.toggleModalAudioPreview();
  assert.deepEqual(calls,['result']);
});

test('fechar editor não encerra transmissão externa do mesmo clipe',()=>{
  const source=fs.readFileSync(require('node:path').join(__dirname,'../ui/app.js'),'utf8');
  const start=source.indexOf('function closeEditModal()');
  const code=source.slice(start,source.indexOf('async function discardDraftAndClose',start));
  const calls=[];
  const context=vm.createContext({state:{integratedEditor:{close:()=>calls.push('owned-preview')},
    activePlayback:{clip_id:'clip',monitor_only:false},playingClips:{clip:true}},
    document:{getElementById:()=>({value:'clip',classList:{add(){}}})},
    api:()=>({stop_all:()=>calls.push('global-stop')}),refreshEditDrafts(){},
    updateModalPreviewButtons(){},applyDraftChrome(){},renderGrid(){},playbackRafId:null});
  vm.runInContext(code,context);context.closeEditModal();
  assert.deepEqual(calls,['owned-preview']);
  assert.equal(context.state.activePlayback.clip_id,'clip');
});

test('rótulo do footer permanece coerente ao transmitir ou ouvir prévia do mesmo clipe',async()=>{
  const source=fs.readFileSync(require('node:path').join(__dirname,'../ui/app.js'),'utf8');
  const labelStart=source.indexOf('function updateModalPreviewButtons(');
  const labelCode=source.slice(labelStart,source.indexOf('function currentClipEditPayload(',labelStart));
  const handlerStart=source.indexOf('  async function toggleModalAudioPreview()');
  const handlerCode=source.slice(handlerStart,source.indexOf('  document.getElementById("btnSaveClip").onclick',handlerStart));
  const elements={},calls=[];
  const context=vm.createContext({state:{activePlayback:{clip_id:'clip',playing:true,monitor_only:false}},
    window:{IntegratedEditor:{}},document:{getElementById:id=>(elements[id]||={value:'clip',classList:{add(){},remove(){}}})},
    api:()=>{throw Error('não usar reprodução global');}});
  vm.runInContext(labelCode+handlerCode,context);
  // Primeiro open: módulo carregado, controller ainda não foi criado.
  context.updateModalPreviewButtons(true);
  assert.match(elements.btnPreviewMonitor.innerHTML,/Testar no Fone/);
  context.state.integratedEditor={preview:async mode=>calls.push(mode)};
  await context.toggleModalAudioPreview();
  // Updates globais durante preview própria também não transformam o botão em Parar.
  context.state.activePlayback.monitor_only=true;
  context.updateModalPreviewButtons(true);
  assert.match(elements.btnPreviewMonitor.innerHTML,/Testar no Fone/);
  await context.toggleModalAudioPreview();assert.deepEqual(calls,['result','result']);
});
