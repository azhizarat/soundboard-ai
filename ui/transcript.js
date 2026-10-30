/* Unidades de texto com âncoras reais; nunca divide tempo por caracteres. */
(function(root) {
  'use strict';
  function intersects(unit, interval) {
    return unit.anchors.some(([a,b]) => a < interval[1] && b > interval[0]);
  }
  function units(model, spans) {
    const words = model.words || [], corrections = model.corrections || [];
    const replaced = new Set(corrections.flatMap(c => c.word_ids));
    const segments = (model.segments || []).filter(s => !words.some(w => s.word_ids ? s.word_ids.includes(w.id) : w.start_sample < s.end_sample && w.end_sample > s.start_sample));
    const items = [...words.filter(w=>!replaced.has(w.id)).map(w=>({...w,kind:'word',word_ids:[w.id]})),
      ...corrections.map(c=>({...c,kind:'correction'})), ...segments.filter(s=>!replaced.has(s.id)).map(s=>({...s,kind:'segment',word_ids:[s.id]}))];
    return items.sort((a,b)=>a.start_sample-b.start_sample).map(item=>{
      const anchors=item.anchors || [[item.start_sample,item.end_sample]];
      const unit={...item,anchors};
      const kept=anchors.flatMap(([a,b])=>spans.filter(([lo,hi])=>lo<b&&hi>a).map(([lo,hi])=>[Math.max(a,lo),Math.min(b,hi)]));
      return {...unit,kept,removed:kept.length===0,partial:kept.reduce((s,[a,b])=>s+b-a,0)<anchors.reduce((s,[a,b])=>s+b-a,0)};
    });
  }
  const exported={units,intersects};
  if(typeof module!=='undefined'&&module.exports) module.exports=exported;
  else root.TranscriptSelection=exported;
})(typeof window!=='undefined'?window:globalThis);
