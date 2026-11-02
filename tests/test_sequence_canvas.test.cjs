const test=require('node:test'),assert=require('node:assert/strict');
const {layout}=require('../ui/sequence-canvas.js');
test('fluxo distribui ramos lado a lado e só conecta continuação depois da junção',()=>{
 const result=layout([{id:'a',type:'play'},{id:'p',type:'parallel',branches:[[{id:'b',type:'play'},{id:'w',type:'wait'}],[{id:'c',type:'play'}]]},{id:'end',type:'manual'}]);
 const at=id=>result.nodes.find(n=>n.id===id);
 assert.ok(at('b').x<at('c').x);assert.equal(at('b').y,at('c').y);
 assert.ok(at('end').y>at('w').y+at('w').height);assert.ok(result.edges.length>=8);
 assert.ok(result.slots.some(s=>s.parent==='p'&&s.branch===0&&s.before==='w'));
 assert.ok(result.slots.some(s=>!s.parent&&s.before==='p'));
 for(const n of result.nodes){assert.ok(n.x>=0);assert.ok(n.x+n.width<=result.width);assert.ok(n.y+n.height<=result.height);}
});
test('fluxo vazio oferece inserção entre início e fim',()=>{
 const result=layout([]);assert.equal(result.nodes.length,0);assert.equal(result.slots.length,1);assert.ok(result.edges.length);
});
