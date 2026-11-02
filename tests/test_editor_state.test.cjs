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

function fixture(options={}) {
  const elements={};
  function node() {
    return {value:'',textContent:'',children:[],dataset:{},clientWidth:900,checked:false,
      classList:{toggle(){}},replaceChildren(){this.children=[];},appendChild(child){this.children.push(child);},
      get options(){return this.children;},getContext(){return null;},focus(){},setPointerCapture(){},
      getBoundingClientRect(){return {left:0,top:0,width:900,height:154};},style:{}};
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
  const controller=editor.create(document,()=>api,{persist:async()=>({ok:true}),transcriptChanged:text=>synced.push(text),...options});
  return {elements,controller,calls,synced,api,setFail:value=>{fail=value;}};
}

function pointer(x,y=75,extra={}) {return {clientX:x,clientY:y,pointerId:1,button:0,preventDefault(){},...extra};}
test('wave click and scrub position cursor without destroying selection; handles adjust only one edge',async()=>{
 const f=fixture();await f.controller.open('one');f.controller.setSelection([24000,72000]);
 const c=f.elements.editorWaveform;
 c.onpointerdown(pointer(450));c.onpointerup(pointer(450));
 assert.deepEqual(f.controller.selection,[24000,72000]);
 assert.equal(f.controller.cursor,48000);
 c.onpointerdown(pointer(225));c.onpointermove(pointer(270));c.onpointerup(pointer(270));
 assert.deepEqual(f.controller.selection,[28800,72000]);assert.equal(f.controller.cursor,48000);
 c.onpointerdown(pointer(450));c.onpointermove(pointer(540));c.onpointerup(pointer(540));
 assert.equal(f.controller.cursor,57600);assert.deepEqual(f.controller.selection,[28800,72000]);f.controller.close();
});
test('shift drag selects and alt drag moves selection relatively without changing its length',async()=>{
 const f=fixture();await f.controller.open('one');const c=f.elements.editorWaveform;
 c.onpointerdown(pointer(225,75,{shiftKey:true}));c.onpointermove(pointer(450));c.onpointerup(pointer(450));
 assert.deepEqual(f.controller.selection,[24000,48000]);
 c.onpointerdown(pointer(330,75,{altKey:true}));c.onpointermove(pointer(420));c.onpointerup(pointer(420));
 assert.deepEqual(f.controller.selection,[33600,57600]);f.controller.close();
});
test('cursor keyboard and I/O marks work without resetting cursor; input arrows stay native',async()=>{
 const f=fixture();await f.controller.open('one');
 const key=(key,extra={})=>f.elements.integratedEditor.onkeydown({key,target:{tagName:'CANVAS'},preventDefault(){},stopPropagation(){},...extra});
 key('ArrowRight');assert.equal(f.controller.cursor,480);
 key('i');assert.equal(f.controller.selection[0],480);
 key('ArrowRight',{shiftKey:true});key('o');assert.equal(f.controller.selection[1],5280);
 assert.equal(f.controller.cursor,5280);f.controller.close();
});

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
  f.elements.integratedEditor.onkeydown({key:'i',target:{tagName:'CANVAS'},preventDefault(){}});assert.equal(f.controller.selection[0],0);
});

test('failed montage command keeps preview on original mapping; explicit stop ignores late poll',async t=>{
  const f=await playbackFixture(t);f.setFail(true);
  assert.equal((await f.controller.run('remove',{interval:[0,48000]})).ok,false);
  assert.deepEqual(f.stopped,[]);f.finishPoll();await new Promise(resolve=>setImmediate(resolve));
  f.elements.integratedEditor.onkeydown({key:'i',target:{tagName:'CANVAS'},preventDefault(){}});assert.equal(f.controller.selection[0],12000);
  await f.elements.editorStop.onclick();assert.deepEqual(f.stopped,['own-monitor']);
});

test('explicit stop invalidates an already outstanding poll',async t=>{
  const f=await playbackFixture(t);await f.elements.editorStop.onclick();
  f.finishPoll();await new Promise(resolve=>setImmediate(resolve));
  f.elements.integratedEditor.onkeydown({key:'i',target:{tagName:'CANVAS'},preventDefault(){}});assert.equal(f.controller.selection[0],0);
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


test('agulha faz seek só na própria sessão, com offset relativo após corte e preserva seleção',async()=>{
 const f=fixture();await f.controller.open('one');const seeks=[],stops=[];
 await f.controller.run('remove',{interval:[0,48000]});
 f.api.preview_clip_edit=async()=>({ok:true,play_id:'editor-only',ranges:[[0,48000]]});
 f.api.stop_editor_preview=async id=>{stops.push(id);};
 f.api.seek_playback=async (position,id)=>{seeks.push([position,id]);return {ok:true};};
 f.api.get_editor_playback=async()=>({playing:false});
 f.controller.setSelection([48000,96000]);
 const c=f.elements.editorWaveform;c.onpointerdown(pointer(675));c.onpointerup(pointer(675));
 await f.controller.preview('result',true);await f.controller.settleSeek();
 assert.deepEqual(seeks,[[.5,'editor-only']]);assert.deepEqual(f.controller.selection,[48000,96000]);
 c.onpointerdown(pointer(810));c.onpointerup(pointer(810));await f.controller.settleSeek();
 assert.deepEqual(seeks.at(-1),[.8,'editor-only']);f.controller.close();
 assert.deepEqual(stops,['editor-only']);
});

test('seek pendente não vaza para outro clipe nem usa sessão global após fechar',async()=>{
 const f=fixture();await f.controller.open('one');const seeks=[];let finish;
 f.api.preview_clip_edit=async()=>({ok:true,play_id:'old',ranges:[[0,96000]]});
 f.api.stop_editor_preview=async()=>({ok:true});
 f.api.get_editor_playback=async()=>({playing:false});
 f.api.seek_playback=(position,id)=>{seeks.push([position,id]);return new Promise(resolve=>finish=resolve);};
 await f.controller.preview();const c=f.elements.editorWaveform;
 c.onpointerdown(pointer(400));c.onpointerup(pointer(400));
 await new Promise(resolve=>setImmediate(resolve));
 c.onpointerdown(pointer(600));c.onpointerup(pointer(600));
 f.controller.close();await f.controller.open('two');finish({ok:true});await f.controller.settleSeek();
 assert.equal(seeks.length,1);assert.equal(seeks[0][1],'old');f.controller.close();
});

test('atualizar transcrição alinhada revela palavras sem apagar correção em digitação',async()=>{
 const f=fixture();await f.controller.open('one');
 assert.equal(f.elements.editorWords.hidden,false);assert.equal(f.elements.editorCorrectionRow.hidden,true);
 f.elements.editorEditText.onclick();f.elements.editorCorrection.value='correção pendente';f.elements.editorCorrection.oninput();
 await f.controller.refresh();assert.equal(f.elements.editorCorrection.value,'correção pendente');
 assert.equal(f.elements.editorCorrectionRow.hidden,false);assert.equal(f.elements.editorWords.hidden,true);
 f.controller.close();
});


test('resposta tardia ao parar prévia antiga não perde ownership da prévia mais nova',async()=>{
 const f=fixture();await f.controller.open('one');let pendingStop;const stops=[];let serial=0;
 f.api.preview_clip_edit=async()=>({ok:true,play_id:'p'+(++serial),ranges:[[0,96000]]});
 f.api.stop_editor_preview=id=>{stops.push(id);return id==='p1'?new Promise(resolve=>pendingStop=resolve):Promise.resolve({ok:true});};
 f.api.get_editor_playback=async()=>({playing:false});
 await f.controller.preview();const older=f.controller.preview();await new Promise(resolve=>setImmediate(resolve));
 await f.controller.preview();pendingStop({ok:true});await older;await f.elements.editorStop.onclick();
 assert.equal(serial,2);assert.deepEqual(stops,['p1','p2']);f.controller.close();
});

test('poll iniciado durante seek não move agulha para status anterior recebido depois',async()=>{
 const f=fixture();await f.controller.open('one');let finishSeek,finishPoll;
 f.api.preview_clip_edit=async()=>({ok:true,play_id:'own',ranges:[[0,96000]]});
 f.api.stop_editor_preview=async()=>({ok:true});
 f.api.seek_playback=()=>new Promise(resolve=>finishSeek=resolve);
 f.api.get_editor_playback=()=>new Promise(resolve=>finishPoll=resolve);
 await f.controller.preview();f.elements.editorWaveform.onpointerdown(pointer(675));f.elements.editorWaveform.onpointerup(pointer(675));
 while(!finishPoll)await new Promise(resolve=>setTimeout(resolve,5));
 finishSeek({ok:true});await f.controller.settleSeek();finishPoll({playing:true,position:.1});
 await new Promise(resolve=>setImmediate(resolve));assert.equal(f.controller.cursor,72000);f.controller.close();
});


test('selecionar palavra preserva seu elemento para duplo clique e não reconstrói texto a cada gesto',async()=>{
 const f=fixture();await f.controller.open('one');const word=f.elements.editorWords.children[0];
 word.onclick({shiftKey:false});assert.equal(f.elements.editorWords.children[0],word);
 word.ondblclick();assert.equal(f.elements.editorCorrectionRow.hidden,false);assert.equal(f.elements.editorCorrection.value,'um');
 f.controller.close();
});


function animatedFixture(){
 let time=0,nextId=0;const frames=new Map(),timers=new Map();
 const f=fixture({now:()=>time,requestFrame:fn=>{frames.set(++nextId,fn);return nextId;},cancelFrame:id=>frames.delete(id),
   schedule:fn=>{timers.set(++nextId,fn);return nextId;},unschedule:id=>timers.delete(id)});
 return {...f,frame(ms){time=ms;const pending=[...frames.values()];frames.clear();pending.forEach(fn=>fn(time));},
   async poll(){const pending=[...timers.values()];timers.clear();await Promise.all(pending.map(fn=>fn()));},frames,timers};
}
test('agulha avança entre consultas e termina na borda mesmo quando trecho acaba antes do primeiro poll',async()=>{
 const f=animatedFixture();await f.controller.open('one');f.controller.setSelection([12000,16800]);
 f.api.preview_clip_edit=async()=>({ok:true,play_id:'short',ranges:[[12000,16800]],status:{playing:true,position:0,paused:false,loop:false}});
 f.api.get_editor_playback=async()=>({status:{playing:false,reason:'completed',position:.1}});
 f.api.stop_editor_preview=async()=>({ok:true});
 await f.controller.preview();f.frame(16);assert.equal(f.controller.cursor,12768);
 f.frame(32);assert.equal(f.controller.cursor,13536);
 await f.poll();assert.equal(f.controller.cursor,16800);assert.equal(f.frames.size,0);f.controller.close();
});
test('parada externa não simula conclusão e frame pendente não altera agulha após fechar',async()=>{
 const f=animatedFixture();await f.controller.open('one');
 f.api.preview_clip_edit=async()=>({ok:true,play_id:'own',ranges:[[0,96000]],status:{playing:true,position:0}});
 f.api.get_editor_playback=async()=>({status:{playing:false,reason:'cancelled',position:.06}});
 f.api.stop_editor_preview=async()=>({ok:true});
 await f.controller.preview();f.frame(16);await f.poll();assert.equal(f.controller.cursor,2880);assert.equal(f.frames.size,0);
 await f.controller.preview();const last=f.controller.cursor;f.controller.close();f.frame(99);assert.equal(f.controller.cursor,last);
});
test('clock respeita pausa, loop e limite de extrapolação quando bridge demora',async()=>{
 const f=animatedFixture();await f.controller.open('one');
 f.api.preview_clip_edit=async()=>({ok:true,play_id:'loop',ranges:[[24000,28800]],status:{playing:true,position:.09,loop:true}});
 let playback={playing:true,position:.02,paused:true,loop:true};
 f.api.get_editor_playback=async()=>({status:playback});f.api.stop_editor_preview=async()=>({ok:true});
 await f.controller.preview();f.frame(20);assert.equal(f.controller.cursor,24480);
 await f.poll();f.frame(200);assert.equal(f.controller.cursor,24960);
 playback={playing:true,position:.03,paused:false,loop:false};await f.poll();f.frame(2000);
 assert.ok(f.controller.cursor<=28800);f.controller.close();
});


test('falha transitória no status preserva ownership e recupera atualizações na consulta seguinte',async()=>{
 const f=animatedFixture();await f.controller.open('one');let calls=0;const stopped=[];
 f.api.preview_clip_edit=async()=>({ok:true,play_id:'own',ranges:[[0,96000]],status:{playing:true,position:0}});
 f.api.get_editor_playback=async()=>{if(++calls===1)throw Error('bridge ocupado');return {status:{playing:true,position:.3}};};
 f.api.stop_editor_preview=async id=>{stopped.push(id);};
 await f.controller.preview();await f.poll();await f.poll();f.frame(16);
 assert.equal(f.controller.cursor,15168);await f.elements.editorStop.onclick();assert.deepEqual(stopped,['own']);f.controller.close();
});


test('conclusão no próprio retorno da prévia posiciona fim sem agendar frames ou polls',async()=>{
 const f=animatedFixture();await f.controller.open('one');
 f.api.preview_clip_edit=async()=>({ok:true,play_id:'short',ranges:[[12000,12137]],status:{playing:false,position:137/48000,reason:'completed'}});
 await f.controller.preview();assert.equal(f.controller.cursor,12137);assert.equal(f.frames.size,0);assert.equal(f.timers.size,0);f.controller.close();
});


test('arraste para trás com seeks sobrepostos retoma animação na última intenção',async()=>{
 const f=animatedFixture();await f.controller.open('one');const pending=[],positions=[];
 f.api.preview_clip_edit=async()=>({ok:true,play_id:'own',ranges:[[0,96000]],status:{playing:true,position:1.5}});
 f.api.get_editor_playback=async()=>({status:{playing:true,position:.5}});
 f.api.seek_playback=position=>{positions.push(position);return new Promise(resolve=>pending.push(resolve));};
 f.api.stop_editor_preview=async()=>({ok:true});
 await f.controller.preview();const wave=f.elements.editorWaveform;
 wave.onpointerdown(pointer(112.5));wave.onpointerup(pointer(112.5));await new Promise(resolve=>setImmediate(resolve));
 wave.onpointerdown(pointer(225));wave.onpointerup(pointer(225));pending.shift()({ok:true});
 await new Promise(resolve=>setImmediate(resolve));pending.shift()({ok:true});await f.controller.settleSeek();
 await f.poll();f.frame(16);assert.equal(f.controller.cursor,24768);assert.deepEqual(positions,[.25,.5]);f.controller.close();
});
