const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs');
function timeline(){assert.ok(fs.existsSync('ui/editor-timeline.js'),'Mapeamento temporal do editor ausente');return require('../ui/editor-timeline.js');}
const spans=[[100,110],[200,220]];

test('origem vira posição relativa do resultado, saltando lacunas e limitando extremos',()=>{
  const {sourceToOutput}=timeline();
  for(const [source,expected] of [[-10,0],[100,0],[105,5],[109,9],[110,10],[150,10],[200,10],[219,29],[220,29],[999,29]])
    assert.equal(sourceToOutput(spans,source),expected,'origem '+source);
  assert.equal(sourceToOutput([],30),0);
  assert.equal(sourceToOutput(spans,105.7),6);
  assert.deepEqual(spans,[[100,110],[200,220]]);
});
test('duração processada coincide com round ties-to-even do backend',()=>{
  const {outputSamples,previewSamples}=timeline();
  assert.equal(outputSamples([[0,5]],{speed:2}),2);
  assert.equal(outputSamples([[0,7]],{speed:2}),4);
  assert.equal(outputSamples([[0,1]],{speed:2}),1);
  assert.equal(outputSamples(spans,{speed:.5,reverse:true}),60);
  assert.equal(outputSamples([],{}),0);
  assert.equal(previewSamples([[3,8],[12,20]]),13);
});
test('inversa conserva amostra da origem representável, inclusive variantes reversas e cortes',()=>{
  const {sourceToVariant,outputSamples}=timeline(),{variantToSource}=require('../ui/editor.js');
  const cut=[[5,11],[20,27]];
  for(const speed of [.5,.75,1,1.25,1.5,2])for(const reverse of [false,true]){
    const effect={speed,reverse};
    for(let output=0;output<outputSamples(cut,effect);output++){
      const source=variantToSource(cut,output,effect),mapped=sourceToVariant(cut,source,effect);
      assert.ok(Number.isInteger(mapped)&&mapped>=0&&mapped<outputSamples(cut,effect));
      assert.equal(variantToSource(cut,mapped,effect),source,JSON.stringify({speed,reverse,output,source,mapped}));
    }
  }
});
test('seek de prévia usa o offset da sessão, atravessando somente seus ranges',()=>{
  const {sourceToPreview}=timeline();
  // Saída completa: origem 100..110 -> 0..10; origem 200..220 -> 10..30.
  const ranges=[[5,10],[20,25]];
  for(const [source,expected] of [[100,0],[105,0],[109,4],[200,5],[210,5],[214,9],[219,9]])
    assert.equal(sourceToPreview(spans,source,{},ranges),expected,'origem '+source);
  assert.equal(sourceToPreview(spans,210,{},[]),0);
});
test('seleção reversa acelerada mantém coordenadas processadas retornadas pelo backend',()=>{
  const {sourceToPreview,sourceToVariant}=timeline();
  // Backend: map_ranges([[0,24000]],96000,{speed:2,reverse:true}) = [[36000,48000]].
  const original=[[0,96000]],variant={speed:2,reverse:true},ranges=[[36000,48000]];
  assert.equal(sourceToPreview(original,23999,variant,ranges),0);
  assert.equal(sourceToPreview(original,11999,variant,ranges),6000);
  assert.equal(sourceToPreview(original,0,variant,ranges),11999);
  assert.equal(sourceToVariant(original,-1,variant),47999);
  assert.equal(sourceToVariant(original,999999,variant),0);
});
test('dados inválidos falham antes de produzir seek NaN ou duração negativa',()=>{
  const {sourceToOutput,sourceToVariant,outputSamples}=timeline();
  for(const invalid of [[[4,4]],[[5,3]],[[0,5],[4,7]],[[0,2.5]]])assert.throws(()=>sourceToOutput(invalid,1),RangeError);
  for(const speed of [0,-1,NaN,Infinity])assert.throws(()=>outputSamples(spans,{speed}),RangeError);
  assert.equal(sourceToVariant([],0,{}),0);
  assert.equal(sourceToOutput(spans,NaN),0);
});
