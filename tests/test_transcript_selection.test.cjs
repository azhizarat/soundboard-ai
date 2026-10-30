const test = require('node:test');
const assert = require('node:assert/strict');
const transcript = require('../ui/transcript.js');

test('manual blocks stay indivisible with multiple real anchors and no duplicate text', () => {
  const model = {words: [{id:'a',text:'one',start_sample:0,end_sample:20},{id:'b',text:'two',start_sample:20,end_sample:60}],
    segments: [], corrections:[{id:'c',word_ids:['a','b'],text:'joined',start_sample:0,end_sample:60,anchors:[[0,20],[40,60]]}]};
  const units = transcript.units(model, [[0,20],[40,60]]);
  assert.equal(units.length, 1);
  assert.deepEqual(units[0].word_ids,['a','b']);
  assert.deepEqual(units[0].anchors,[[0,20],[40,60]]);
  assert.equal(transcript.intersects(units[0],[25,35]),false);
  assert.equal(transcript.intersects(units[0],[45,50]),true);
});

test('segment-only transcript provides real block selection, legacy has no fake anchors', () => {
  const units=transcript.units({words:[],corrections:[],segments:[{id:'s',text:'hello world',start_sample:10,end_sample:90,word_ids:[]}]},[[0,100]]);
  assert.deepEqual(units[0].anchors,[[10,90]]);
  assert.equal(units[0].kind,'segment');
  assert.deepEqual(transcript.units({alignment:'unaligned',text:'old'},[[0,100]]),[]);
});
