const test=require('node:test');
const assert=require('node:assert/strict');
const editor=require('../ui/editor.js');

test('selection clamps source bounds and keyboard moves exactly 10 or 100 ms',()=>{
  assert.deepEqual(editor.selection([-2,999999],96000),[0,96000]);
  assert.deepEqual(editor.nudge([10000,20000],0,1,false,96000),[10480,20000]);
  assert.deepEqual(editor.nudge([10000,20000],1,-1,true,96000),[10000,15200]);
  assert.deepEqual(editor.nudge([10000,10100],0,1,true,96000),[10099,10100]);
});

test('editing controls retain native delete, backspace, arrows and undo',()=>{
  for(const tag of ['INPUT','TEXTAREA','SELECT']) assert.equal(editor.editingTarget({tagName:tag}),true);
  assert.equal(editor.editingTarget({isContentEditable:true}),true);
  assert.equal(editor.editingTarget({tagName:'CANVAS'}),false);
});

test('playback output maps through removed gap into source coordinates',()=>{
  assert.equal(editor.outputToSource([[0,100],[200,400]],125),225);
  assert.equal(editor.outputToSource([[0,100],[200,400]],300),400);
});

test('cursor de variante inverte e escala saída antes de mapear lacunas da origem',()=>{
 assert.equal(typeof editor.variantToSource,'function');
 assert.equal(editor.variantToSource([[0,100],[200,400]],0,{speed:2,reverse:true}),399);
 assert.equal(editor.variantToSource([[0,100],[200,400]],100,{speed:2}),300);
});

function fixture() {
  const elements={};
  function node() {
    return {value:'',textContent:'',children:[],dataset:{},clientWidth:900,checked:false,
      classList:{toggle(){}},replaceChildren(){this.children=[];},appendChild(child){this.children.push(child);},
      get options(){return this.children;},getContext(){return null;}};
  }
  const document={getElementById:id=>elements[id]||=(node()),createElement:node};
  let model={source_samples:96000,spans:[[0,96000]],source_transcript:{alignment:'aligned',words:[
    {id:'a',text:'um',start_sample:0,end_sample:24000},{id:'b',text:'dois',start_sample:24000,end_sample:48000}],segments:[],corrections:[]},
    result:{alignment:'aligned',text:'um dois'},duration:2,volume:1,draft_id:'d',generation:1};
  let fail=false;const calls=[],synced=[];
  const undo=[],redo=[];
  const api={get_clip_editor:async()=>({ok:true,editor:model}),get_edit_source_waveform:async()=>({ok:true,peaks:[]}),
    stop_all:async()=>({ok:true}),edit_clip_command:async(id,action,payload)=>{
      calls.push({id,action,payload});if(fail)return {ok:false,error:'disk full'};
      if(action==='undo'||action==='redo') {
        const from=action==='undo'?undo:redo,to=action==='undo'?redo:undo;
        if(!from.length)return {ok:false,error:'empty history'};
        to.push(model);model={...from.pop(),generation:model.generation+1};
        return {ok:true,editor:model};
      }
      undo.push(model);redo.length=0;
      if(action==='correct') model={...model,generation:model.generation+1,result:{...model.result,text:payload.text},
        source_transcript:{...model.source_transcript,corrections:[{id:'c',word_ids:payload.word_ids,text:payload.text,start_sample:0,end_sample:48000,anchors:[[0,48000]]}]}};
      if(action==='remove')model={...model,generation:model.generation+1,spans:[[48000,96000]],duration:1};
      return {ok:true,editor:model};
    },preview_clip_edit:async()=>{throw Error('Não deve tocar em clique de texto');}};
  const controller=editor.create(document,()=>api,{persist:async()=>({ok:true}),transcriptChanged:text=>synced.push(text)});
  return {elements,controller,calls,synced,api,setFail:value=>{fail=value;}};
}

test('text click only selects; corrected block is selected as whole on subsequent edits',async()=>{
  const f=fixture();await f.controller.open('one');
  f.elements.editorWords.children[0].onclick({shiftKey:false});
  assert.deepEqual(f.controller.selection,[0,24000]);assert.equal(f.calls.length,0);
  f.elements.editorWords.children[1].onclick({shiftKey:true});
  f.elements.editorCorrection.value='unido';f.elements.editorCorrection.oninput();
  assert.equal((await f.controller.flush()).ok,true);
  assert.deepEqual(f.calls[0].payload.word_ids,['a','b']);
  assert.equal(f.synced.at(-1),'unido');
  f.controller.setSelection([1000,2000]);
  assert.equal(f.elements.editorCorrection.value,'unido');
  f.elements.editorCorrection.value='corrigido de novo';f.elements.editorCorrection.oninput();
  await f.controller.flush();assert.deepEqual(f.calls[1].payload.word_ids,['a','b']);
  f.controller.close();
});

test('undo and redo synchronize correction field and ids without overwriting pending typing',async()=>{
  const f=fixture();await f.controller.open('one');
  f.elements.editorCorrection.value='unido';f.elements.editorCorrection.oninput();await f.controller.flush();
  await f.controller.run('undo');assert.equal(f.elements.editorCorrection.value,'um dois');
  await f.controller.run('redo');assert.equal(f.elements.editorCorrection.value,'unido');
  f.elements.editorCorrection.value='rascunho pendente';f.elements.editorCorrection.oninput();
  await f.controller.run('undo');assert.equal(f.elements.editorCorrection.value,'rascunho pendente');
  f.setFail(true);assert.equal((await f.controller.flush()).ok,false);
  assert.equal(f.elements.editorCorrection.value,'rascunho pendente');f.setFail(false);
  await f.controller.flush();assert.deepEqual(f.calls.at(-1).payload.word_ids,['a','b']);
  f.controller.close();
});

test('undo recomputes correction ids for a selection inside a previously joined block',async()=>{
  const f=fixture();await f.controller.open('one');
  f.elements.editorCorrection.value='unido';f.elements.editorCorrection.oninput();await f.controller.flush();
  f.controller.setSelection([1000,2000]);await f.controller.run('undo');
  assert.equal(f.elements.editorCorrection.value,'um');
  f.elements.editorCorrection.value='primeira palavra';f.elements.editorCorrection.oninput();await f.controller.flush();
  assert.deepEqual(f.calls.at(-1).payload.word_ids,['a']);f.controller.close();
});

async function playbackFixture(t) {
  const f=fixture();await f.controller.open('one');const stopped=[];let resolvePoll;
  f.api.preview_clip_edit=async()=>({ok:true,play_id:'own-monitor',ranges:[[0,96000]]});
  f.api.stop_editor_preview=async id=>{stopped.push(id);return {ok:true};};
  f.api.get_editor_playback=()=>new Promise(resolve=>{resolvePoll=resolve;});
  await f.controller.preview('result');
  while(!resolvePoll)await new Promise(resolve=>setTimeout(resolve,5));
  t.after(()=>f.controller.close());
  return {...f,stopped,finishPoll:()=>resolvePoll({ok:true,status:{playing:true,position:.25}})};
}

test('montage mutation stops only owned preview and ignores its late poll',async t=>{
  const f=await playbackFixture(t);
  await f.controller.run('remove',{interval:[0,48000]});
  f.finishPoll();await new Promise(resolve=>setImmediate(resolve));
  assert.deepEqual(f.stopped,['own-monitor']);
  f.elements.editorMarkStart.onclick();assert.equal(f.controller.selection[0],0);
});

test('failed montage command keeps preview on original mapping; explicit stop ignores late poll',async t=>{
  const f=await playbackFixture(t);f.setFail(true);
  assert.equal((await f.controller.run('remove',{interval:[0,48000]})).ok,false);
  assert.deepEqual(f.stopped,[]);f.finishPoll();await new Promise(resolve=>setImmediate(resolve));
  f.elements.editorMarkStart.onclick();assert.equal(f.controller.selection[0],12000);
  await f.elements.editorStop.onclick();assert.deepEqual(f.stopped,['own-monitor']);
});

test('explicit stop invalidates an already outstanding poll',async t=>{
  const f=await playbackFixture(t);await f.elements.editorStop.onclick();
  f.finishPoll();await new Promise(resolve=>setImmediate(resolve));
  f.elements.editorMarkStart.onclick();assert.equal(f.controller.selection[0],0);
  assert.deepEqual(f.stopped,['own-monitor']);
});

test('failed correction remains recoverable and text editing never issues audio commands',async()=>{
  const f=fixture();await f.controller.open('one');f.setFail(true);
  f.elements.editorCorrection.value='não perder';f.elements.editorCorrection.oninput();
  assert.equal((await f.controller.flush()).ok,false);
  assert.equal(f.elements.editorCorrection.value,'não perder');
  for(const key of ['Delete','Backspace','ArrowLeft','z'])f.elements.integratedEditor.onkeydown({target:{tagName:'TEXTAREA'},key,ctrlKey:true,preventDefault(){throw Error('native input intercepted');}});
  assert.equal(f.calls.length,1);f.setFail(false);
  assert.equal((await f.controller.flush()).ok,true);assert.equal(f.synced.at(-1),'não perder');
  f.controller.close();
});

test('preview that starts after closing is stopped by its own id',async()=>{
  const f=fixture();await f.controller.open('one');let resolvePreview;const stopped=[];
  f.api.preview_clip_edit=()=>new Promise(resolve=>{resolvePreview=resolve;});
  f.api.stop_editor_preview=async id=>{stopped.push(id);return {ok:true};};
  const pending=f.controller.preview();
  while(!resolvePreview)await new Promise(resolve=>setImmediate(resolve));
  f.controller.close();resolvePreview({ok:true,play_id:'old-preview',ranges:[[0,24000]]});
  await pending;assert.deepEqual(stopped,['old-preview']);
});
