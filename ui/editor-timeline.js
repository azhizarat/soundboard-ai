/* Coordenadas em amostras: origem → montagem → variante → sessão de prévia. */
(function(root){
  'use strict';
  function totalSamples(spans){
    if(!Array.isArray(spans))throw new RangeError('Intervalos inválidos');
    let previous=0,total=0;
    for(const pair of spans){
      if(!Array.isArray(pair)||pair.length!==2)throw new RangeError('Intervalo inválido');
      const [start,end]=pair;
      if(!Number.isSafeInteger(start)||!Number.isSafeInteger(end)||start<previous||end<=start)
        throw new RangeError('Intervalos fora de ordem, sobrepostos ou vazios');
      total+=end-start;previous=end;
      if(!Number.isSafeInteger(total))throw new RangeError('Duração fora dos limites');
    }
    return total;
  }
  function speedOf(variant){
    const speed=variant?.speed??1;
    if(typeof speed!=='number'||!Number.isFinite(speed)||speed<=0)throw new RangeError('Velocidade inválida');
    return speed;
  }
  // Python round(), usado por audio_variants.output_samples/map_ranges, resolve .5 para o par.
  function roundEven(value){
    const lower=Math.floor(value),fraction=value-lower;
    return fraction===.5?(lower%2===0?lower:lower+1):Math.round(value);
  }
  function outputSamples(spans,variant={}){
    const total=totalSamples(spans),speed=speedOf(variant);
    if(!total)return 0;
    const length=Math.max(1,roundEven(total/speed));
    if(!Number.isSafeInteger(length))throw new RangeError('Duração processada fora dos limites');
    return length;
  }
  function sourceToOutput(spans,sourceSample){
    const total=totalSamples(spans);
    if(!total)return 0;
    let source=Math.round(Number(sourceSample)),offset=0;
    if(Number.isNaN(source))source=0;
    for(const [start,end] of spans){
      // Intervalos são [início,fim): uma lacuna aponta ao próximo trecho mantido.
      if(source<start)return offset;
      if(source<end)return offset+source-start;
      offset+=end-start;
    }
    return total-1;
  }
  function sourceToVariant(spans,sourceSample,variant={}){
    const length=outputSamples(spans,variant);
    if(!length)return 0;
    const source=sourceToOutput(spans,sourceSample),total=totalSamples(spans);
    const mapped=variant?.reverse?total-1-source:source;
    // Inversa do floor(saída * speed) usado por variantToSource. Se várias
    // amostras apontam à mesma origem, escolhe a primeira; clamp nunca busca após o fim.
    return Math.max(0,Math.min(length-1,Math.ceil(mapped/speedOf(variant))));
  }
  function sourceToPreview(spans,sourceSample,variant={},playRanges=[]){
    // playRanges já inclui velocidade/reverso (contrato de preview_clip_edit).
    return sourceToOutput(playRanges,sourceToVariant(spans,sourceSample,variant));
  }
  const exported={sourceToOutput,sourceToVariant,sourceToPreview,outputSamples,previewSamples:totalSamples};
  if(typeof module!=='undefined'&&module.exports)module.exports=exported;
  root.EditorTimeline=exported;
})(typeof window==='undefined'?globalThis:window);
