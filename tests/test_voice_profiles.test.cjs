const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
function moduleUnderTest(){
  const filename=path.join(__dirname,'../ui/voice-profiles.js');
  assert.ok(fs.existsSync(filename),'Interface de perfis vocais ausente');
  return require(filename);
}
test('estados de identidade não apresentam percentuais de certeza',()=>{
  const voice=moduleUnderTest();
  assert.equal(voice.identityLabel({status:'ambiguous'}),'Duvidosa');
  assert.equal(voice.identityLabel({status:'unknown'}),'Desconhecida');
  assert.equal(voice.identityLabel({status:'identified'}),'Identificada');
  assert.ok(!voice.identityLabel({status:'identified',candidates:[{score:.95}]}).includes('%'));
});
test('resultado humano e aprendizado pendente/falha são separados',()=>{
  const voice=moduleUnderTest();
  assert.match(voice.actionLabel({state:'pending'}),/Pessoa corrigida/);
  assert.match(voice.actionLabel({state:'learned',reference_learned:true}),/exemplo adicionado/);
  assert.match(voice.actionLabel({state:'pending_review',error:'mixed'}),/não foi usado/);
});
test('integração salvar pessoa envia seleção da fonte e carrega gestão real',()=>{
  moduleUnderTest();
  const app=fs.readFileSync(path.join(__dirname,'../ui/app.js'),'utf8');
  const html=fs.readFileSync(path.join(__dirname,'../ui/index.html'),'utf8');
  assert.match(app,/voice_interval/);
  assert.match(app,/VoiceProfiles\.create/);
  for(const id of ['learnVoiceCorrections','voiceLearningStatus','voiceSamples','voiceIdentityChoices','btnUndoVoiceCorrection','btnCancelVoiceLearning'])assert.ok(html.includes(`id="${id}"`));
  assert.ok(html.indexOf('voice-profiles.js')<html.indexOf('app.js'));
});

function fakeDocument(){
  class Element{
    constructor(tag='div'){this.tag=tag;this.children=[];this.dataset={};this.value='';}
    append(...items){this.children.push(...items);}
    replaceChildren(...items){this.children=items;}
    setAttribute(){}
  }
  const elements=new Map();
  return {getElementById(id){if(!elements.has(id))elements.set(id,new Element());return elements.get(id);},createElement:tag=>new Element(tag)};
}
test('fechar perfil cancela prévia própria mesmo se resposta chega depois',async()=>{
  const voice=moduleUnderTest(),doc=fakeDocument(),calls=[];
  let resolvePreview;
  const pending=new Promise(resolve=>{resolvePreview=resolve;});
  const profile={settings:{},profiles:{a:{profile_version:1}},legacy:{},speakers:{a:{name:'A'}},samples:[{sample_id:'s',speaker_id:'a',spans:[[0,48000]],quality:{reasons:[],policy:{version:'v1'}},used_in_profile:true}]};
  const controller=voice.create(doc,()=>({get_voice_profiles:async()=>profile,preview_voice_sample:()=>pending,stop_editor_preview:async id=>calls.push(id)}));
  await controller.openProfile('a');
  const row=doc.getElementById('voiceSamples').children[0];
  const click=row.children.find(item=>item.textContent==='Ouvir no monitor').onclick();
  await Promise.resolve();controller.close();resolvePreview({ok:true,play_id:'owned'});await click;
  assert.deepEqual(calls,['owned']);
});

test('editor filho não habilita undo de ação pertencente ao pai',async()=>{
  const voice=moduleUnderTest(),doc=fakeDocument(),calls=[];
  const profile={settings:{},speakers:{},actions:{action:{action_id:'action',clip_id:'parent',state:'learned',reference_learned:true}}};
  const controller=voice.create(doc,()=>({get_voice_profiles:async()=>profile,undo_voice_correction:async(...args)=>{calls.push(args);return {ok:true};}}));
  await controller.openEditor({id:'child',voice_action_id:'action'});
  assert.equal(doc.getElementById('btnUndoVoiceCorrection').disabled,true);
  assert.equal(doc.getElementById('btnCancelVoiceLearning').disabled,true);
  assert.deepEqual(calls,[]);
  await controller.openEditor({id:'parent',voice_action_id:'action'});
  assert.equal(doc.getElementById('btnUndoVoiceCorrection').disabled,false);
  await doc.getElementById('btnUndoVoiceCorrection').onclick();
  assert.deepEqual(calls,[['action','parent']]);
  controller.close();
});
