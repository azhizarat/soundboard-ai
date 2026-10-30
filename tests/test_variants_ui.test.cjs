const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const modulePath=path.join(__dirname,'../ui/variants.js');
function setup(){
 assert.ok(fs.existsSync(modulePath),'Controles de variantes ausentes');
 const elements={};const dom={getElementById:id=>elements[id]||=( {value:'',checked:false,textContent:'',disabled:false} )};
 const calls=[],created=[];let resolve;
 const api={get_variant_capabilities:async()=>({ok:true,speed:true,pitch:true,limitations:'limites'}),
  preview_audio_variant:(...args)=>{calls.push(['preview',...args]);return new Promise(r=>resolve=r);},
  stop_editor_preview:async id=>{calls.push(['stop',id]);return {ok:true};},
  stop_variant_preview:async token=>{calls.push(['cancel',token]);return {ok:true};},
  create_audio_variant:async(...args)=>{calls.push(['create',...args]);return {ok:true,clip:{id:'child'}};}};
 const ui=require(modulePath).create(dom,()=>api,c=>created.push(c));
 return {ui,elements,calls,created,api,finish:r=>resolve(r)};
}
test('prévia tardia é parada ao fechar e nunca se confunde com outra revisão',async()=>{
 const f=setup();await f.ui.open('parent','rev-old');f.elements.variantSpeed.value='2';
 const pending=f.ui.preview();f.ui.close();f.finish({ok:true,play_id:'late'});await pending;
 const preview=f.calls.find(c=>c[0]==='preview');assert.equal(typeof preview[4],'string');
 assert.deepEqual(preview.slice(0,4),['preview','parent',{speed:2,pitch_semitones:0,reverse:false},'rev-old']);
 assert.ok(f.calls.some(c=>c[0]==='cancel'&&c[1]===preview[4]));assert.ok(f.calls.some(c=>c[0]==='stop'&&c[1]==='late'));
});
test('duas chamadas de criar concorrentes publicam somente um pedido',async()=>{
 const f=setup();await f.ui.open('parent','r');let complete;
 f.api.create_audio_variant=async()=>{f.calls.push('create');return new Promise(resolve=>complete=resolve);};
 const first=f.ui.save();const second=f.ui.save();assert.deepEqual(f.calls,['create']);complete({ok:true,clip:{id:'child'}});await first;await second;
});
test('criação usa revisão publicada escolhida e anuncia filho sem transferir edição',async()=>{
 const f=setup();await f.ui.open('parent','rev-old');f.elements.variantReverse.checked=true;
 await f.ui.save();assert.deepEqual(f.calls,[['create','parent',{speed:1,pitch_semitones:0,reverse:true},'rev-old']]);assert.deepEqual(f.created,[{id:'child'}]);
});
test('capacidade ausente desabilita velocidade e tom mantendo reverso',async()=>{
 const f=setup();f.api.get_variant_capabilities=async()=>({speed:false,pitch:false,reason:'FFmpeg ausente',limitations:'limites'});
 await f.ui.open('parent','r');assert.equal(f.elements.variantSpeed.disabled,true);assert.equal(f.elements.variantPitch.disabled,true);assert.equal(f.elements.variantReverse.disabled,false);
});
test('WebView sem randomUUID consegue enviar e cancelar token único',async()=>{
 const original=Object.getOwnPropertyDescriptor(globalThis,'crypto');Object.defineProperty(globalThis,'crypto',{value:{},configurable:true});
 try{const f=setup();await f.ui.open('c','r');const pending=f.ui.preview();
  assert.equal(f.calls[0]?.[0],'preview');assert.match(f.calls[0][4],/^variant:[0-9a-f-]+$/);
  f.finish({ok:true,play_id:'p'});await pending;await f.ui.stop();
 }finally{Object.defineProperty(globalThis,'crypto',original);}
});
